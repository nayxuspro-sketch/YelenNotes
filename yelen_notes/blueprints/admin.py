"""Administration : comptes utilisateurs multi-rôles, sauvegardes/restaurations et journaux d'audit (§6, §9, §10 RBAC)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import socket
import sys
import tempfile

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import or_

from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_ENSEIGNANT,
    ROLE_SUPER_ADMIN,
    AcademicClass,
    AuditLog,
    BackupRecord,
    Establishment,
    EstablishmentSettings,
    ParentStudentLink,
    Role,
    SchoolYear,
    Student,
    TeacherClassAssignment,
    User,
    UserRole,
)
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import (
    RBACSecurityError,
    any_permission_required,
    assign_roles_to_user,
    ensure_rbac_initialized,
    ensure_user_role_synced,
    get_current_establishment_id,
    get_user_accessible_establishment_ids,
    has_permission,
    invalidate_rbac_cache,
    is_super_admin,
    permission_required,
    record_audit_log,
    user_can_access_establishment,
)
from yelen_notes.security.rbac_service import count_other_active_super_admins
from yelen_notes.services.backup_service import (
    BackupError,
    create_safe_backup,
    restore_from_backup_archive,
    run_sqlite_integrity_check,
    verify_backup_archive,
)
from yelen_notes.blueprints.licensing import super_admin_only
from yelen_notes.services.update_service import (
    MAX_UPDATE_ARCHIVE_BYTES,
    UpdateError,
    apply_program_update,
    check_online_update,
    download_online_release_zip,
    load_update_history,
    schedule_graceful_server_restart,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/administration")


def detect_lan_ip_addresses() -> list[str]:
    """Détecte les adresses IPv4 locales utiles pour le mode multiposte sans Internet."""
    ips: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addr = info[4][0]
            if addr and not addr.startswith("127."):
                ips.add(addr)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        addr = s.getsockname()[0]
        if addr and not addr.startswith("127."):
            ips.add(addr)
        s.close()
    except OSError:
        pass
    return sorted(ips)


@admin_bp.route("/utilisateurs", methods=["GET", "POST"])
@permission_required("users.view", module="users")
def users_manage():
    ensure_rbac_initialized()
    can_create_user = has_permission(current_user, "users.create") or has_permission(
        current_user, "users.manage"
    )
    can_update_user = has_permission(current_user, "users.update") or has_permission(
        current_user, "users.manage"
    )

    if request.method == "POST":
        if not can_create_user:
            abort(403)

        username = (request.form.get("username") or "").strip().lower()
        full_name = (request.form.get("full_name") or "").strip()
        password = request.form.get("password") or ""
        must_change = request.form.get("must_change_password") == "1"

        role_ids = [int(rid) for rid in request.form.getlist("role_ids[]") if rid.isdigit()]
        if not role_ids:
            raw_role_code = (request.form.get("role") or ROLE_ENSEIGNANT).strip().upper()
            r_obj = Role.query.filter_by(code=raw_role_code).first()
            if r_obj:
                role_ids = [r_obj.id]

        selected_roles = Role.query.filter(Role.id.in_(role_ids), Role.is_active.is_(True)).all() if role_ids else []
        est_id = request.form.get("establishment_id", type=int) or get_current_establishment_id(current_user)
        if not is_super_admin(current_user):
            est_id = current_user.establishment_id or 1

        errors: list[str] = []
        if len(username) < 3 or " " in username:
            errors.append("L'identifiant doit comporter au moins 3 caractères sans espace.")
        if User.query.filter_by(username=username).first():
            errors.append(f"L'identifiant « {username} » est déjà utilisé.")
        if len(full_name) < 2:
            errors.append("Le nom complet est obligatoire.")
        if not selected_roles:
            errors.append("Veuillez sélectionner au moins un rôle actif.")
        if len(password) < 8:
            errors.append("Le mot de passe initial doit contenir au moins 8 caractères.")
        if not is_super_admin(current_user) and any(r.code == ROLE_SUPER_ADMIN for r in selected_roles):
            errors.append("Seul un Super administrateur peut créer un compte Super administrateur.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return redirect(url_for("admin.users_manage"))

        user = User(
            username=username,
            full_name=full_name,
            role=selected_roles[0].code,
            establishment_id=est_id,
            is_active=True,
            must_change_password=must_change,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        try:
            assign_roles_to_user(
                target_user=user,
                role_ids=[r.id for r in selected_roles],
                actor=current_user,
            )
        except RBACSecurityError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("admin.users_manage"))

        # Affectations éventuelles aux classes (enseignants) et enfants (parents)
        assigned_ids = [int(cid) for cid in request.form.getlist("class_ids[]") if cid.isdigit()]
        for cid in assigned_ids:
            cls_obj = db.session.get(AcademicClass, cid)
            if cls_obj and user_can_access_establishment(current_user, cls_obj.establishment_id):
                db.session.add(TeacherClassAssignment(user_id=user.id, class_id=cid, can_edit_grades=True))

        student_ids = [int(sid) for sid in request.form.getlist("student_ids[]") if sid.isdigit()]
        for sid in student_ids:
            st_obj = db.session.get(Student, sid)
            if st_obj and user_can_access_establishment(current_user, st_obj.establishment_id):
                db.session.add(ParentStudentLink(user_id=user.id, student_id=sid))

        invalidate_rbac_cache()
        record_audit_log(
            "USER_CREATE",
            f"Création du compte '{user.username}' avec rôle(s) {[r.code for r in selected_roles]}",
            module="users",
            establishment_id=est_id,
            target_type="User",
            target_id=user.id,
            details={"roles": [r.code for r in selected_roles], "establishment_id": est_id},
            commit=True,
        )
        flash(f"Compte « {user.username} » créé avec succès.", "success")
        return redirect(url_for("admin.users_manage"))

    allowed_ests = get_user_accessible_establishment_ids(current_user)
    u_query = User.query
    if allowed_ests is not None:
        u_query = u_query.filter(
            or_(User.establishment_id.in_(allowed_ests), User.establishment_id.is_(None))
        )
    users = u_query.order_by(User.username).all()

    roles_q = Role.query.filter_by(is_active=True).order_by(Role.is_system.desc(), Role.id)
    if not is_super_admin(current_user):
        roles_q = roles_q.filter(Role.code != ROLE_SUPER_ADMIN)
    available_roles = roles_q.all()

    all_classes = [
        c
        for c in AcademicClass.query.join(SchoolYear)
        .order_by(SchoolYear.label.desc(), AcademicClass.display_order, AcademicClass.name)
        .all()
        if user_can_access_establishment(current_user, c.establishment_id)
    ]
    all_students = [
        s
        for s in Student.query.order_by(Student.last_name, Student.first_names).limit(300).all()
        if user_can_access_establishment(current_user, s.establishment_id)
    ]
    establishments = (
        Establishment.query.filter_by(is_active=True).order_by(Establishment.id).all()
        if allowed_ests is None
        else Establishment.query.filter(Establishment.id.in_(allowed_ests)).order_by(Establishment.id).all()
    )

    return render_template(
        "admin/users.html",
        users=users,
        available_roles=available_roles,
        all_classes=all_classes,
        all_students=all_students,
        establishments=establishments,
        can_create_user=can_create_user,
        can_update_user=can_update_user,
        user_is_super_admin=is_super_admin(current_user),
    )


@admin_bp.route("/utilisateurs/<int:user_id>/modifier", methods=["POST"])
@any_permission_required("users.update", "users.manage", module="users")
def update_user(user_id: int):
    user = db.get_or_404(User, user_id)
    if not user_can_access_establishment(current_user, user.establishment_id):
        abort(403)
    if is_super_admin(user) and not is_super_admin(current_user):
        abort(403)

    full_name = (request.form.get("full_name") or "").strip()
    is_active = request.form.get("is_active") == "1"
    unlock = request.form.get("unlock") == "1"
    clear_suspension = request.form.get("clear_suspension") == "1"
    suspended_until_str = (request.form.get("suspended_until") or "").strip()
    suspension_reason = (request.form.get("suspension_reason") or "").strip()[:255] or None

    if user.id == current_user.id and (not is_active or suspended_until_str):
        flash("Vous ne pouvez ni désactiver ni suspendre votre propre compte.", "danger")
        return redirect(url_for("admin.users_manage"))

    old_codes = {r.code for r in ensure_user_role_synced(user)}
    if ROLE_SUPER_ADMIN in old_codes and not is_active:
        if count_other_active_super_admins(user.id) == 0:
            flash("Opération refusée : le système doit conserver au moins un Super administrateur actif.", "danger")
            return redirect(url_for("admin.users_manage"))

    # Mise à jour éventuelle des rôles attribués
    role_ids = [int(rid) for rid in request.form.getlist("role_ids[]") if rid.isdigit()]
    if not role_ids and request.form.get("role"):
        r_obj = Role.query.filter_by(code=request.form.get("role", "").strip().upper()).first()
        if r_obj:
            role_ids = [r_obj.id]

    if role_ids and user.id != current_user.id:
        try:
            assign_roles_to_user(target_user=user, role_ids=role_ids, actor=current_user)
        except RBACSecurityError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("admin.users_manage"))
    elif role_ids and user.id == current_user.id:
        submitted_codes = {r.code for r in Role.query.filter(Role.id.in_(role_ids)).all()}
        if submitted_codes != old_codes:
            record_audit_log(
                "SECURITY_VIOLATION",
                f"Tentative bloquée d'auto-modification des rôles par '{current_user.username}'",
                module="rbac",
                status="DENIED",
                target_type="User",
                target_id=user.id,
                commit=True,
            )
            flash("Sécurité RBAC : vous ne pouvez pas modifier les rôles de votre propre compte.", "danger")
            return redirect(url_for("admin.users_manage"))

    if full_name:
        user.full_name = full_name
    if is_super_admin(current_user) and user.id != current_user.id:
        est_id = request.form.get("establishment_id", type=int)
        if est_id and db.session.get(Establishment, est_id):
            user.establishment_id = est_id

    user.is_active = is_active
    if unlock:
        user.failed_login_count = 0
        user.locked_until = None

    if clear_suspension:
        user.suspended_until = None
        user.suspension_reason = None
    elif suspended_until_str:
        try:
            user.suspended_until = datetime.strptime(suspended_until_str, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
            user.suspension_reason = suspension_reason or "Suspension temporaire administrative"
        except ValueError:
            flash("Date de fin de suspension invalide.", "danger")
            return redirect(url_for("admin.users_manage"))

    # Mise à jour des classes affectées et enfants rattachés
    TeacherClassAssignment.query.filter_by(user_id=user.id).delete()
    assigned_ids = [int(cid) for cid in request.form.getlist("class_ids[]") if cid.isdigit()]
    for cid in assigned_ids:
        cls_obj = db.session.get(AcademicClass, cid)
        if cls_obj and user_can_access_establishment(current_user, cls_obj.establishment_id):
            db.session.add(TeacherClassAssignment(user_id=user.id, class_id=cid, can_edit_grades=True))

    if "student_ids_present" in request.form:
        ParentStudentLink.query.filter_by(user_id=user.id).delete()
        student_ids = [int(sid) for sid in request.form.getlist("student_ids[]") if sid.isdigit()]
        for sid in student_ids:
            st_obj = db.session.get(Student, sid)
            if st_obj and user_can_access_establishment(current_user, st_obj.establishment_id):
                db.session.add(ParentStudentLink(user_id=user.id, student_id=sid))

    invalidate_rbac_cache()
    record_audit_log(
        "USER_UPDATE",
        f"Mise à jour du compte '{user.username}' (rôles={user.role_label}, actif={user.is_active})",
        module="users",
        establishment_id=user.establishment_id,
        target_type="User",
        target_id=user.id,
        commit=True,
    )
    flash(f"Compte « {user.username} » mis à jour.", "success")
    return redirect(url_for("admin.users_manage"))


@admin_bp.route("/utilisateurs/<int:user_id>/reinitialiser-mdp", methods=["POST"])
@any_permission_required("users.update", "users.manage", module="users")
def reset_user_password(user_id: int):
    user = db.get_or_404(User, user_id)
    if not user_can_access_establishment(current_user, user.establishment_id):
        abort(403)
    if is_super_admin(user) and not is_super_admin(current_user):
        abort(403)

    new_pw = request.form.get("new_password") or ""
    if len(new_pw) < 8:
        flash("Le mot de passe temporaire doit contenir au moins 8 caractères.", "danger")
        return redirect(url_for("admin.users_manage"))

    user.set_password(new_pw)
    user.must_change_password = user.id != current_user.id
    user.failed_login_count = 0
    user.locked_until = None

    record_audit_log(
        "USER_PASSWORD_RESET",
        f"Réinitialisation administrative du mot de passe de '{user.username}'",
        module="users",
        establishment_id=user.establishment_id,
        target_type="User",
        target_id=user.id,
        commit=True,
    )
    flash(f"Mot de passe réinitialisé pour « {user.username} ».", "success")
    return redirect(url_for("admin.users_manage"))


@admin_bp.route("/mises-a-jour")
@super_admin_only
def updates_manage():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    current_version = str(current_app.config["APP_VERSION"])
    result = check_online_update(paths, current_version) if request.args.get("verifier") == "1" else None
    return render_template(
        "admin/updates.html",
        paths=paths,
        current_version=current_version,
        update_result=result,
        update_history=load_update_history(paths),
        is_frozen=bool(getattr(sys, "frozen", False)),
    )


@admin_bp.route("/mises-a-jour/appliquer-en-ligne", methods=["POST"])
@super_admin_only
def apply_online_update_route():
    if getattr(sys, "frozen", False):
        flash("L'édition compilée doit être mise à jour avec le paquet de déploiement officiel.", "warning")
        return redirect(url_for("admin.updates_manage"))
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    current_version = str(current_app.config["APP_VERSION"])
    try:
        check = check_online_update(paths, current_version)
        if not check.reachable:
            raise UpdateError(check.error_message or "Serveur de mise à jour injoignable.")
        if not check.update_available or not check.download_tag:
            raise UpdateError("Aucune version plus récente n'est actuellement disponible.")
        archive = download_online_release_zip(paths, check.download_tag)
        info = apply_program_update(
            paths,
            archive,
            current_version=current_version,
            actor_username=current_user.username,
            source_label=f"GitHub Release {check.download_tag}",
        )
        record_audit_log(
            "PROGRAM_UPDATE",
            f"Mise à jour en ligne appliquée : v{info.previous_version} vers v{info.new_version}",
            module="updates",
            details={"source": info.source_label, "files_updated": info.files_updated},
            commit=True,
        )
        restart_scheduled = schedule_graceful_server_restart(
            paths,
            str(current_app.config.get("SERVER_MODE", "monoposte")),
            bool(current_app.config.get("IS_DEMO_MODE", False)),
            request.environ.get("SERVER_PORT", 5000),
        )
        if restart_scheduled:
            flash(
                f"Mise à jour v{info.new_version} appliquée. Le serveur va redémarrer dans quelques secondes; "
                "vos données scolaires, paramètres et licence ont été conservés.",
                "success",
            )
        else:
            flash(
                f"Mise à jour v{info.new_version} appliquée. Une sauvegarde vérifiée a été créée : "
                f"{info.backup_filename}. Redémarrez le serveur pour charger la nouvelle version.",
                "success",
            )
    except (UpdateError, BackupError, OSError) as exc:
        flash(f"Mise à jour en ligne annulée : {exc}", "danger")
    return redirect(url_for("admin.updates_manage"))


@admin_bp.route("/mises-a-jour/importer", methods=["POST"])
@super_admin_only
def apply_offline_update_route():
    if getattr(sys, "frozen", False):
        flash("L'édition compilée doit être mise à jour avec le paquet de déploiement officiel.", "warning")
        return redirect(url_for("admin.updates_manage"))
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    current_version = str(current_app.config["APP_VERSION"])
    uploaded = request.files.get("update_zip")
    if not uploaded or not uploaded.filename or not uploaded.filename.lower().endswith(".zip"):
        flash("Veuillez sélectionner une archive de mise à jour .zip.", "danger")
        return redirect(url_for("admin.updates_manage"))
    try:
        archive = uploaded.stream.read(MAX_UPDATE_ARCHIVE_BYTES + 1)
        if len(archive) > MAX_UPDATE_ARCHIVE_BYTES:
            raise UpdateError("Archive trop volumineuse (limite : 64 Mo).")
        info = apply_program_update(
            paths,
            archive,
            current_version=current_version,
            actor_username=current_user.username,
            source_label=f"Import hors ligne : {Path(uploaded.filename).name}",
            allow_same_or_older=request.form.get("force_reinstall") == "1",
        )
        record_audit_log(
            "PROGRAM_UPDATE",
            f"Mise à jour hors ligne appliquée : v{info.previous_version} vers v{info.new_version}",
            module="updates",
            details={"source": info.source_label, "files_updated": info.files_updated},
            commit=True,
        )
        restart_scheduled = schedule_graceful_server_restart(
            paths,
            str(current_app.config.get("SERVER_MODE", "monoposte")),
            bool(current_app.config.get("IS_DEMO_MODE", False)),
            request.environ.get("SERVER_PORT", 5000),
        )
        if restart_scheduled:
            flash(
                f"Mise à jour v{info.new_version} appliquée et sauvegarde vérifiée créée. "
                "Le serveur va redémarrer.",
                "success",
            )
        else:
            flash(
                f"Mise à jour v{info.new_version} appliquée. Sauvegarde vérifiée : {info.backup_filename}. "
                "Redémarrez le serveur pour charger la nouvelle version.",
                "success",
            )
    except (UpdateError, BackupError, OSError) as exc:
        flash(f"Import de mise à jour annulé : {exc}", "danger")
    return redirect(url_for("admin.updates_manage"))


@admin_bp.route("/sauvegardes")
@permission_required("settings.view", module="settings")
def backups_manage():
    if not (has_permission(current_user, "settings.update") or has_permission(current_user, "settings.manage")):
        abort(403)
    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    records = BackupRecord.query.order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc()).limit(50).all()
    db_ok, db_msg = run_sqlite_integrity_check(paths.db_path)

    return render_template(
        "admin/backups.html",
        settings=settings,
        paths=paths,
        records=records,
        db_ok=db_ok,
        db_msg=db_msg,
        is_admin=has_permission(current_user, "settings.manage"),
    )


@admin_bp.route("/sauvegardes/creer", methods=["POST"])
@permission_required("settings.update", module="settings")
def trigger_manual_backup():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    notes = (request.form.get("notes") or "").strip() or "Sauvegarde manuelle"
    custom_dest = (request.form.get("custom_destination") or "").strip() or None

    try:
        archive = create_safe_backup(
            paths,
            app_version=current_app.config["APP_VERSION"],
            trigger_type="MANUAL",
            created_by=current_user.username,
            notes=notes,
            destination_dir=custom_dest,
            record_in_db=True,
        )
        record_audit_log(
            "BACKUP_CREATE",
            f"Sauvegarde manuelle créée et vérifiée : {archive.name}",
            module="settings",
            details={"path": str(archive)},
            commit=True,
        )
        flash(f"Sauvegarde créée et vérifiée avec succès : {archive.name}", "success")
    except (BackupError, OSError) as exc:
        flash(f"Échec de la sauvegarde : {exc}", "danger")

    return redirect(url_for("admin.backups_manage"))


@admin_bp.route("/sauvegardes/<int:record_id>/telecharger")
@permission_required("settings.update", module="settings")
def download_backup(record_id: int):
    rec = db.get_or_404(BackupRecord, record_id)
    file_path = Path(rec.file_path)
    if not file_path.is_file():
        flash("Le fichier d'archive correspondant n'est plus présent à l'emplacement enregistré.", "danger")
        return redirect(url_for("admin.backups_manage"))
    return send_file(file_path, as_attachment=True, download_name=rec.filename)


@admin_bp.route("/sauvegardes/<int:record_id>/verifier", methods=["POST"])
@permission_required("settings.update", module="settings")
def verify_existing_backup(record_id: int):
    rec = db.get_or_404(BackupRecord, record_id)
    ver = verify_backup_archive(Path(rec.file_path))
    rec.integrity_verified = ver.is_valid
    db.session.commit()
    if ver.is_valid:
        flash(f"Contrôle d'intégrité réussi pour « {rec.filename} » ({ver.message}).", "success")
    else:
        flash(f"Alerte d'intégrité sur « {rec.filename} » : {ver.message}", "danger")
    return redirect(url_for("admin.backups_manage"))


@admin_bp.route("/sauvegardes/parametres", methods=["POST"])
@permission_required("settings.manage", module="settings")
def update_backup_settings():
    settings = EstablishmentSettings.get_singleton()
    settings.auto_backup_enabled = request.form.get("auto_backup_enabled") == "1"
    interval = request.form.get("auto_backup_interval_hours", type=int) or 24
    retention = request.form.get("backup_retention_count", type=int) or 15
    custom_dir = (request.form.get("backup_directory") or "").strip() or None

    if custom_dir:
        try:
            Path(custom_dir).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            flash(f"Impossible d'accéder au dossier de sauvegarde indiqué : {exc}", "danger")
            return redirect(url_for("admin.backups_manage"))

    settings.auto_backup_interval_hours = max(1, min(interval, 720))
    settings.backup_retention_count = max(1, min(retention, 200))
    settings.backup_directory = custom_dir
    record_audit_log(
        "BACKUP_SETTINGS_UPDATE",
        "Mise à jour de la politique de sauvegarde automatique",
        module="settings",
        commit=True,
    )
    flash("Paramètres de sauvegarde automatique enregistrés.", "success")
    return redirect(url_for("admin.backups_manage"))


@admin_bp.route("/sauvegardes/restaurer", methods=["POST"])
@permission_required("settings.manage", module="settings")
def restore_backup():
    if not is_super_admin(current_user):
        abort(403)
    confirmation = (request.form.get("confirm_word") or "").strip().upper()
    if confirmation != "RESTAURER":
        flash("Restauration annulée : veuillez saisir exactement le mot RESTAURER pour confirmer.", "warning")
        return redirect(url_for("admin.backups_manage"))

    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    record_id = request.form.get("record_id", type=int)
    uploaded_zip = request.files.get("backup_zip")

    try:
        if uploaded_zip and uploaded_zip.filename:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                uploaded_zip.save(tmp.name)
                tmp_path = Path(tmp.name)
            try:
                restore_from_backup_archive(
                    tmp_path,
                    paths,
                    app_version=current_app.config["APP_VERSION"],
                    restored_by=current_user.username,
                )
            finally:
                tmp_path.unlink(missing_ok=True)
        elif record_id:
            rec = db.get_or_404(BackupRecord, record_id)
            restore_from_backup_archive(
                Path(rec.file_path),
                paths,
                app_version=current_app.config["APP_VERSION"],
                restored_by=current_user.username,
            )
        else:
            flash("Aucune sauvegarde sélectionnée pour la restauration.", "danger")
            return redirect(url_for("admin.backups_manage"))

        invalidate_rbac_cache()
        flash(
            "Restauration effectuée et intégrité SQLite vérifiée avec succès. "
            "Une sauvegarde de sécurité préalable (PRE_RESTORE) a été conservée.",
            "success",
        )
        return redirect(url_for("dashboard.index"))
    except BackupError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.backups_manage"))


@admin_bp.route("/journaux")
@permission_required("audit.view", module="audit")
def audit_logs_view():
    action_filter = (request.args.get("action") or "").strip()
    module_filter = (request.args.get("module") or "").strip()
    status_filter = (request.args.get("status") or "").strip()

    query = AuditLog.query
    allowed_ests = get_user_accessible_establishment_ids(current_user)
    if allowed_ests is not None:
        query = query.filter(
            or_(AuditLog.establishment_id.in_(allowed_ests), AuditLog.establishment_id.is_(None))
        )

    if action_filter:
        query = query.filter(AuditLog.action == action_filter)
    if module_filter:
        query = query.filter(AuditLog.module == module_filter)
    if status_filter:
        query = query.filter(AuditLog.status == status_filter)

    logs = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(250).all()

    distinct_actions = [
        r[0] for r in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all() if r[0]
    ]
    distinct_modules = [
        r[0] for r in db.session.query(AuditLog.module).distinct().order_by(AuditLog.module).all() if r[0]
    ]
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    db_ok, db_msg = run_sqlite_integrity_check(paths.db_path)

    journal_mode = "inconnu"
    try:
        row = db.session.execute(db.text("PRAGMA journal_mode;")).fetchone()
        if row:
            journal_mode = str(row[0]).upper()
    except Exception:
        pass

    return render_template(
        "admin/audit_logs.html",
        logs=logs,
        distinct_actions=distinct_actions,
        distinct_modules=distinct_modules,
        action_filter=action_filter,
        module_filter=module_filter,
        status_filter=status_filter,
        can_export_audit=has_permission(current_user, "audit.export"),
        paths=paths,
        db_ok=db_ok,
        db_msg=db_msg,
        journal_mode=journal_mode,
        lan_ips=detect_lan_ip_addresses(),
    )
