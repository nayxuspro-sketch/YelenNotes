"""Administration : comptes utilisateurs, affectations, sauvegardes/restaurations et journaux (§2, §14, §17 CdC)."""

from __future__ import annotations

from pathlib import Path
import socket
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

from yelen_notes.extensions import db
from yelen_notes.models import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    ROLE_LABELS,
    AcademicClass,
    AuditLog,
    BackupRecord,
    EstablishmentSettings,
    SchoolYear,
    TeacherClassAssignment,
    User,
)
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import record_audit_log, roles_required
from yelen_notes.services.backup_service import (
    BackupError,
    create_safe_backup,
    restore_from_backup_archive,
    run_sqlite_integrity_check,
    verify_backup_archive,
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
@roles_required(ROLE_ADMIN)
def users_manage():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        full_name = (request.form.get("full_name") or "").strip()
        role = (request.form.get("role") or ROLE_ENSEIGNANT).strip().upper()
        password = request.form.get("password") or ""
        must_change = request.form.get("must_change_password") == "1"

        errors: list[str] = []
        if len(username) < 3 or " " in username:
            errors.append("L'identifiant doit comporter au moins 3 caractères sans espace.")
        if User.query.filter_by(username=username).first():
            errors.append(f"L'identifiant « {username} » est déjà utilisé.")
        if len(full_name) < 2:
            errors.append("Le nom complet est obligatoire.")
        if role not in ALL_ROLES:
            errors.append("Rôle utilisateur invalide.")
        if len(password) < 8:
            errors.append("Le mot de passe initial doit contenir au moins 8 caractères.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return redirect(url_for("admin.users_manage"))

        user = User(
            username=username,
            full_name=full_name,
            role=role,
            is_active=True,
            must_change_password=must_change,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        # Affectations éventuelles aux classes si enseignant
        if role == ROLE_ENSEIGNANT:
            assigned_ids = [
                int(cid) for cid in request.form.getlist("class_ids[]") if cid.isdigit()
            ]
            for cid in assigned_ids:
                if db.session.get(AcademicClass, cid):
                    db.session.add(
                        TeacherClassAssignment(user_id=user.id, class_id=cid, can_edit_grades=True)
                    )

        record_audit_log(
            "USER_CREATE",
            f"Création du compte '{user.username}' avec le profil {user.role}",
            target_type="User",
            target_id=user.id,
            commit=True,
        )
        flash(f"Compte « {user.username} » créé avec succès.", "success")
        return redirect(url_for("admin.users_manage"))

    users = User.query.order_by(User.role, User.username).all()
    all_classes = (
        AcademicClass.query.join(SchoolYear)
        .order_by(SchoolYear.label.desc(), AcademicClass.display_order, AcademicClass.name)
        .all()
    )
    return render_template(
        "admin/users.html",
        users=users,
        role_labels=ROLE_LABELS,
        all_classes=all_classes,
    )


@admin_bp.route("/utilisateurs/<int:user_id>/modifier", methods=["POST"])
@roles_required(ROLE_ADMIN)
def update_user(user_id: int):
    user = db.get_or_404(User, user_id)
    full_name = (request.form.get("full_name") or "").strip()
    new_role = (request.form.get("role") or user.role).strip().upper()
    is_active = request.form.get("is_active") == "1"
    unlock = request.form.get("unlock") == "1"

    # Empêcher l'administrateur de se désactiver ou de retirer le tout dernier administrateur actif
    if user.id == current_user.id and (not is_active or new_role != ROLE_ADMIN):
        flash("Vous ne pouvez pas désactiver votre propre compte ni vous retirer le rôle Administrateur.", "danger")
        return redirect(url_for("admin.users_manage"))

    if user.role == ROLE_ADMIN and (not is_active or new_role != ROLE_ADMIN):
        active_admins = User.query.filter(
            User.role == ROLE_ADMIN,
            User.is_active.is_(True),
            User.id != user.id,
        ).count()
        if active_admins == 0:
            flash("Opération refusée : l'application doit conserver au moins un administrateur actif.", "danger")
            return redirect(url_for("admin.users_manage"))

    if full_name:
        user.full_name = full_name
    if new_role in ALL_ROLES:
        user.role = new_role
    user.is_active = is_active
    if unlock:
        user.failed_login_count = 0
        user.locked_until = None

    # Mise à jour des affectations de classes pour les enseignants
    if user.role == ROLE_ENSEIGNANT:
        TeacherClassAssignment.query.filter_by(user_id=user.id).delete()
        assigned_ids = [int(cid) for cid in request.form.getlist("class_ids[]") if cid.isdigit()]
        for cid in assigned_ids:
            if db.session.get(AcademicClass, cid):
                db.session.add(
                    TeacherClassAssignment(user_id=user.id, class_id=cid, can_edit_grades=True)
                )

    record_audit_log(
        "USER_UPDATE",
        f"Mise à jour du compte '{user.username}' (rôle={user.role}, actif={user.is_active})",
        target_type="User",
        target_id=user.id,
        commit=True,
    )
    flash(f"Compte « {user.username} » mis à jour.", "success")
    return redirect(url_for("admin.users_manage"))


@admin_bp.route("/utilisateurs/<int:user_id>/reinitialiser-mdp", methods=["POST"])
@roles_required(ROLE_ADMIN)
def reset_user_password(user_id: int):
    user = db.get_or_404(User, user_id)
    new_pw = request.form.get("new_password") or ""
    if len(new_pw) < 8:
        flash("Le mot de passe temporaire doit contenir au moins 8 caractères.", "danger")
        return redirect(url_for("admin.users_manage"))

    user.set_password(new_pw)
    user.must_change_password = (user.id != current_user.id)
    user.failed_login_count = 0
    user.locked_until = None

    record_audit_log(
        "USER_PASSWORD_RESET",
        f"Réinitialisation administrative du mot de passe de '{user.username}'",
        target_type="User",
        target_id=user.id,
        commit=True,
    )
    flash(f"Mot de passe réinitialisé pour « {user.username} ».", "success")
    return redirect(url_for("admin.users_manage"))


@admin_bp.route("/sauvegardes")
@roles_required(ROLE_ADMIN, ROLE_DIRECTEUR)
def backups_manage():
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
        is_admin=(current_user.role == ROLE_ADMIN),
    )


@admin_bp.route("/sauvegardes/creer", methods=["POST"])
@roles_required(ROLE_ADMIN, ROLE_DIRECTEUR)
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
            details={"path": str(archive)},
            commit=True,
        )
        flash(f"Sauvegarde créée et vérifiée avec succès : {archive.name}", "success")
    except (BackupError, OSError) as exc:
        flash(f"Échec de la sauvegarde : {exc}", "danger")

    return redirect(url_for("admin.backups_manage"))


@admin_bp.route("/sauvegardes/<int:record_id>/telecharger")
@roles_required(ROLE_ADMIN, ROLE_DIRECTEUR)
def download_backup(record_id: int):
    rec = db.get_or_404(BackupRecord, record_id)
    file_path = Path(rec.file_path)
    if not file_path.is_file():
        flash("Le fichier d'archive correspondant n'est plus présent à l'emplacement enregistré.", "danger")
        return redirect(url_for("admin.backups_manage"))
    return send_file(file_path, as_attachment=True, download_name=rec.filename)


@admin_bp.route("/sauvegardes/<int:record_id>/verifier", methods=["POST"])
@roles_required(ROLE_ADMIN, ROLE_DIRECTEUR)
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
@roles_required(ROLE_ADMIN)
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
        commit=True,
    )
    flash("Paramètres de sauvegarde automatique enregistrés.", "success")
    return redirect(url_for("admin.backups_manage"))


@admin_bp.route("/sauvegardes/restaurer", methods=["POST"])
@roles_required(ROLE_ADMIN)
def restore_backup():
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
@roles_required(ROLE_ADMIN)
def audit_logs_view():
    action_filter = (request.args.get("action") or "").strip()
    query = AuditLog.query
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)
    logs = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(250).all()

    distinct_actions = [
        r[0] for r in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    ]
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    db_ok, db_msg = run_sqlite_integrity_check(paths.db_path)

    # Lecture du mode journal SQLite actif
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
        action_filter=action_filter,
        paths=paths,
        db_ok=db_ok,
        db_msg=db_msg,
        journal_mode=journal_mode,
        lan_ips=detect_lan_ip_addresses(),
    )
