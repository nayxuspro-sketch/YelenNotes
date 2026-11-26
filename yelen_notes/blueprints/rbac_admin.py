"""Module centralisé d'administration des Rôles, Permissions (RBAC), Périmètres et Établissements (§10 RBAC)."""

from __future__ import annotations

import csv
from datetime import datetime
import io

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_

from yelen_notes.extensions import db
from yelen_notes.models import (
    ALL_PERMISSION_ACTIONS,
    PERMISSION_ACTION_LABELS,
    AcademicClass,
    AuditLog,
    Establishment,
    ParentStudentLink,
    Permission,
    Role,
    SchoolYear,
    Student,
    TeacherClassAssignment,
    User,
    UserEstablishmentScope,
    UserRole,
)
from yelen_notes.security import (
    RBACSecurityError,
    any_permission_required,
    assign_roles_to_user,
    create_custom_permission,
    create_role,
    delete_role,
    ensure_rbac_initialized,
    ensure_user_role_synced,
    get_effective_permissions,
    get_user_accessible_establishment_ids,
    has_permission,
    invalidate_rbac_cache,
    is_super_admin,
    permission_required,
    record_audit_log,
    remove_user_permission_override,
    set_user_permission_override,
    update_role,
    update_role_permissions,
    user_can_access_establishment,
)
from yelen_notes.security.rbac_service import MODULES_ORDER

rbac_bp = Blueprint("rbac_admin", __name__, url_prefix="/administration")


def _build_permissions_matrix_rows() -> list[dict]:
    """Organise toutes les permissions par module et par action standard pour l'affichage matriciel (§5)."""
    all_perms = Permission.query.order_by(Permission.module, Permission.id).all()
    by_module: dict[str, dict] = {}
    ordered_module_keys = [m[0] for m in MODULES_ORDER]

    for p in all_perms:
        if p.module not in by_module:
            by_module[p.module] = {
                "module": p.module,
                "module_label": p.module_label,
                "by_action": {},
                "all_perms": [],
            }
        by_module[p.module]["all_perms"].append(p)
        by_module[p.module]["by_action"].setdefault(p.action, []).append(p)

    sorted_keys = sorted(
        by_module.keys(),
        key=lambda k: (ordered_module_keys.index(k) if k in ordered_module_keys else 999, k),
    )
    return [by_module[k] for k in sorted_keys]


@rbac_bp.route("/roles-et-permissions")
@permission_required("rbac.view", module="rbac")
def index():
    ensure_rbac_initialized()
    active_tab = (request.args.get("tab") or "roles").strip().lower()
    if active_tab not in ("roles", "matrix", "users", "audit", "establishments"):
        active_tab = "roles"

    roles = Role.query.order_by(Role.is_system.desc(), Role.id).all()
    selected_role_id = request.args.get("role_id", type=int)
    selected_role = (
        db.session.get(Role, selected_role_id)
        if selected_role_id
        else (roles[0] if roles else None)
    )

    selected_role_perm_ids: set[int] = set()
    role_users: list[User] = []
    if selected_role is not None:
        selected_role_perm_ids = {rp.permission_id for rp in selected_role.role_permissions}
        user_q = User.query.join(UserRole, User.id == UserRole.user_id).filter(
            UserRole.role_id == selected_role.id
        )
        allowed_ests = get_user_accessible_establishment_ids(current_user)
        if allowed_ests is not None:
            user_q = user_q.filter(
                or_(User.establishment_id.in_(allowed_ests), User.establishment_id.is_(None))
            )
        role_users = user_q.order_by(User.username).all()

    # Liste des utilisateurs gérables dans le périmètre
    users_query = User.query
    allowed_ests = get_user_accessible_establishment_ids(current_user)
    if allowed_ests is not None:
        users_query = users_query.filter(
            or_(User.establishment_id.in_(allowed_ests), User.establishment_id.is_(None))
        )
    scoped_users = users_query.order_by(User.username).all()

    # Journaux d'audit récents relatifs à la sécurité & RBAC
    audit_query = AuditLog.query
    if allowed_ests is not None:
        audit_query = audit_query.filter(
            or_(AuditLog.establishment_id.in_(allowed_ests), AuditLog.establishment_id.is_(None))
        )
    rbac_logs = (
        audit_query.filter(
            AuditLog.module.in_(("rbac", "users", "auth", "settings", "system"))
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(100)
        .all()
    )

    establishments = Establishment.query.order_by(Establishment.id).all()
    role_user_counts = {
        r.id: UserRole.query.filter_by(role_id=r.id).count() for r in roles
    }

    return render_template(
        "admin/rbac_center.html",
        active_tab=active_tab,
        roles=roles,
        role_user_counts=role_user_counts,
        selected_role=selected_role,
        selected_role_perm_ids=selected_role_perm_ids,
        role_users=role_users,
        matrix_rows=_build_permissions_matrix_rows(),
        action_codes=ALL_PERMISSION_ACTIONS,
        action_labels=PERMISSION_ACTION_LABELS,
        scoped_users=scoped_users,
        rbac_logs=rbac_logs,
        establishments=establishments,
        modules_order=MODULES_ORDER,
        can_create_rbac=has_permission(current_user, "rbac.create"),
        can_update_rbac=has_permission(current_user, "rbac.update"),
        can_delete_rbac=has_permission(current_user, "rbac.delete"),
        can_manage_rbac=has_permission(current_user, "rbac.manage"),
        user_is_super_admin=is_super_admin(current_user),
    )


@rbac_bp.route("/roles-et-permissions/roles/creer", methods=["POST"])
@permission_required("rbac.create", module="rbac")
def create_role_route():
    name = request.form.get("name") or ""
    code = request.form.get("code") or ""
    description = request.form.get("description") or ""
    is_active = request.form.get("is_active", "1") == "1"
    copy_from_id = request.form.get("copy_from_role_id", type=int)

    try:
        role = create_role(
            name=name,
            code=code,
            description=description,
            is_active=is_active,
            copy_from_role_id=copy_from_id,
        )
        record_audit_log(
            "ROLE_CREATE",
            f"Création du rôle '{role.name}' ({role.code})",
            module="rbac",
            target_type="Role",
            target_id=role.id,
            commit=True,
        )
        flash(f"Rôle « {role.name} » ({role.code}) créé avec succès.", "success")
        return redirect(url_for("rbac_admin.index", tab="matrix", role_id=role.id))
    except RBACSecurityError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("rbac_admin.index", tab="roles"))


@rbac_bp.route("/roles-et-permissions/roles/<int:role_id>/modifier", methods=["POST"])
@permission_required("rbac.update", module="rbac")
def update_role_route(role_id: int):
    role = db.get_or_404(Role, role_id)
    name = request.form.get("name") or role.name
    description = request.form.get("description")
    is_active = request.form.get("is_active") == "1"

    try:
        update_role(role=role, name=name, description=description, is_active=is_active)
        record_audit_log(
            "ROLE_UPDATE",
            f"Modification du rôle '{role.name}' ({role.code}, actif={role.is_active})",
            module="rbac",
            target_type="Role",
            target_id=role.id,
            commit=True,
        )
        flash(f"Rôle « {role.name} » mis à jour.", "success")
    except RBACSecurityError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("rbac_admin.index", tab="roles"))


@rbac_bp.route("/roles-et-permissions/roles/<int:role_id>/supprimer", methods=["POST"])
@permission_required("rbac.delete", module="rbac")
def delete_role_route(role_id: int):
    role = db.get_or_404(Role, role_id)
    reassign_id = request.form.get("reassign_to_role_id", type=int)
    role_name = role.name
    role_code = role.code

    try:
        reassigned = delete_role(role, reassign_to_role_id=reassign_id)
        record_audit_log(
            "ROLE_DELETE",
            f"Suppression du rôle '{role_name}' ({role_code}) — {reassigned} utilisateur(s) réaffecté(s)",
            module="rbac",
            target_type="Role",
            target_id=role_id,
            details={"reassigned_users": reassigned, "reassign_to_role_id": reassign_id},
            commit=True,
        )
        flash(
            f"Rôle « {role_name} » supprimé"
            + (f" ({reassigned} utilisateur(s) réaffecté(s))." if reassigned else "."),
            "info",
        )
    except RBACSecurityError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("rbac_admin.index", tab="roles"))


@rbac_bp.route("/roles-et-permissions/matrice/<int:role_id>", methods=["POST"])
@permission_required("rbac.update", module="rbac")
def save_role_matrix(role_id: int):
    role = db.get_or_404(Role, role_id)
    perm_ids = [int(pid) for pid in request.form.getlist("permission_ids[]") if pid.isdigit()]

    try:
        total = update_role_permissions(role, perm_ids, actor=current_user)
        record_audit_log(
            "RBAC_MATRIX_UPDATE",
            f"Mise à jour de la matrice des permissions du rôle '{role.name}' ({total} permission(s))",
            module="rbac",
            target_type="Role",
            target_id=role.id,
            details={"permission_count": total},
            commit=True,
        )
        flash(
            f"Matrice enregistrée pour « {role.name} » ({total} permission(s) active(s)). Application immédiate.",
            "success",
        )
    except RBACSecurityError as exc:
        db.session.rollback()
        record_audit_log(
            "SECURITY_VIOLATION",
            f"Refus de modification de matrice sur '{role.code}' : {exc}",
            module="rbac",
            status="DENIED",
            target_type="Role",
            target_id=role.id,
            commit=True,
        )
        flash(str(exc), "danger")
    return redirect(url_for("rbac_admin.index", tab="matrix", role_id=role.id))


@rbac_bp.route("/roles-et-permissions/permissions/creer", methods=["POST"])
@permission_required("rbac.create", module="rbac")
def create_permission_route():
    try:
        perm = create_custom_permission(
            code=request.form.get("code") or "",
            module=request.form.get("module") or "",
            module_label=request.form.get("module_label") or "",
            resource=request.form.get("resource") or "",
            action=request.form.get("action") or "",
            description=request.form.get("description") or "",
        )
        record_audit_log(
            "PERM_CREATE",
            f"Ajout de la permission granulaire '{perm.code}' ({perm.module_label})",
            module="rbac",
            target_type="Permission",
            target_id=perm.id,
            commit=True,
        )
        flash(f"Permission « {perm.code} » ajoutée au catalogue.", "success")
    except RBACSecurityError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("rbac_admin.index", tab="matrix"))


@rbac_bp.route("/roles-et-permissions/utilisateur/<int:user_id>")
@any_permission_required("rbac.view", "users.view", module="rbac")
def inspect_user_permissions(user_id: int):
    target_user = db.get_or_404(User, user_id)
    if not user_can_access_establishment(current_user, target_user.establishment_id):
        record_audit_log(
            "ACCESS_DENIED",
            f"Tentative de consultation hors établissement du compte '{target_user.username}'",
            module="rbac",
            status="DENIED",
            commit=True,
        )
        abort(403)

    active_roles = ensure_user_role_synced(target_user)
    effective_codes = get_effective_permissions(target_user)
    all_roles = Role.query.order_by(Role.is_system.desc(), Role.name).all()
    all_perms = Permission.query.order_by(Permission.module_label, Permission.code).all()
    establishments = Establishment.query.filter_by(is_active=True).order_by(Establishment.id).all()

    est_id = target_user.establishment_id or 1
    available_classes = (
        AcademicClass.query.join(SchoolYear)
        .filter(or_(AcademicClass.establishment_id == est_id, AcademicClass.establishment_id.is_(None)))
        .order_by(SchoolYear.label.desc(), AcademicClass.display_order, AcademicClass.name)
        .all()
    )
    available_students = (
        Student.query.filter(or_(Student.establishment_id == est_id, Student.establishment_id.is_(None)))
        .order_by(Student.last_name, Student.first_names)
        .limit(300)
        .all()
    )

    return render_template(
        "admin/user_permissions_detail.html",
        target_user=target_user,
        active_roles=active_roles,
        effective_codes=effective_codes,
        matrix_rows=_build_permissions_matrix_rows(),
        all_roles=all_roles,
        all_perms=all_perms,
        establishments=establishments,
        available_classes=available_classes,
        available_students=available_students,
        can_manage=(
            has_permission(current_user, "rbac.manage")
            or has_permission(current_user, "users.manage")
        ),
        user_is_super_admin=is_super_admin(current_user),
    )


@rbac_bp.route("/roles-et-permissions/utilisateur/<int:user_id>/roles", methods=["POST"])
@any_permission_required("rbac.manage", "users.manage", module="rbac")
def update_user_roles_and_scope(user_id: int):
    target_user = db.get_or_404(User, user_id)
    role_ids = [int(rid) for rid in request.form.getlist("role_ids[]") if rid.isdigit()]

    try:
        assigned = assign_roles_to_user(
            target_user=target_user,
            role_ids=role_ids,
            actor=current_user,
        )

        # Périmètre établissement (modifiable par Super Admin)
        if is_super_admin(current_user) and current_user.id != target_user.id:
            est_id = request.form.get("establishment_id", type=int)
            if est_id and db.session.get(Establishment, est_id):
                target_user.establishment_id = est_id
            extra_est_ids = [
                int(eid) for eid in request.form.getlist("extra_establishment_ids[]") if eid.isdigit()
            ]
            UserEstablishmentScope.query.filter_by(user_id=target_user.id).delete()
            for eid in extra_est_ids:
                if eid != target_user.establishment_id and db.session.get(Establishment, eid):
                    db.session.add(UserEstablishmentScope(user_id=target_user.id, establishment_id=eid))

        # Affectations classes si enseignant & rattachement enfants si parent
        if "update_scopes" in request.form:
            class_ids = [int(cid) for cid in request.form.getlist("class_ids[]") if cid.isdigit()]
            TeacherClassAssignment.query.filter_by(user_id=target_user.id).delete()
            for cid in class_ids:
                cls_obj = db.session.get(AcademicClass, cid)
                if cls_obj and user_can_access_establishment(current_user, cls_obj.establishment_id):
                    db.session.add(
                        TeacherClassAssignment(user_id=target_user.id, class_id=cid, can_edit_grades=True)
                    )

            student_ids = [int(sid) for sid in request.form.getlist("student_ids[]") if sid.isdigit()]
            ParentStudentLink.query.filter_by(user_id=target_user.id).delete()
            for sid in student_ids:
                st_obj = db.session.get(Student, sid)
                if st_obj and user_can_access_establishment(current_user, st_obj.establishment_id):
                    db.session.add(ParentStudentLink(user_id=target_user.id, student_id=sid))

        invalidate_rbac_cache()
        record_audit_log(
            "RBAC_USER_ROLES_UPDATE",
            f"Attribution des rôles {[r.code for r in assigned]} à '{target_user.username}'",
            module="rbac",
            target_type="User",
            target_id=target_user.id,
            details={"roles": [r.code for r in assigned]},
            commit=True,
        )
        flash(f"Rôles et périmètres mis à jour pour « {target_user.username} ».", "success")
    except RBACSecurityError as exc:
        db.session.rollback()
        record_audit_log(
            "SECURITY_VIOLATION",
            f"Refus de modification des rôles de '{target_user.username}' par '{current_user.username}' : {exc}",
            module="rbac",
            status="DENIED",
            target_type="User",
            target_id=target_user.id,
            commit=True,
        )
        flash(str(exc), "danger")

    return redirect(url_for("rbac_admin.inspect_user_permissions", user_id=target_user.id))


@rbac_bp.route("/roles-et-permissions/utilisateur/<int:user_id>/exception", methods=["POST"])
@permission_required("rbac.manage", module="rbac")
def add_user_override(user_id: int):
    target_user = db.get_or_404(User, user_id)
    perm_id = request.form.get("permission_id", type=int)
    effect = request.form.get("effect") or "ALLOW"
    reason = request.form.get("reason") or ""
    expires_str = (request.form.get("expires_at") or "").strip()

    perm = db.session.get(Permission, perm_id) if perm_id else None
    if perm is None:
        flash("Veuillez sélectionner une permission valide.", "danger")
        return redirect(url_for("rbac_admin.inspect_user_permissions", user_id=target_user.id))

    expires_dt = None
    if expires_str:
        try:
            expires_dt = datetime.strptime(expires_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            flash("Date d'expiration invalide.", "danger")
            return redirect(url_for("rbac_admin.inspect_user_permissions", user_id=target_user.id))

    try:
        ov = set_user_permission_override(
            target_user=target_user,
            permission=perm,
            effect=effect,
            reason=reason,
            actor=current_user,
            expires_at=expires_dt,
        )
        record_audit_log(
            "RBAC_USER_OVERRIDE_SET",
            f"Permission individuelle {ov.effect} '{perm.code}' sur '{target_user.username}' (motif : {ov.reason})",
            module="rbac",
            target_type="User",
            target_id=target_user.id,
            details={"permission": perm.code, "effect": ov.effect, "reason": ov.reason},
            commit=True,
        )
        flash(
            f"Permission individuelle exceptionnelle ({ov.effect}) enregistrée pour « {target_user.username} ».",
            "success",
        )
    except RBACSecurityError as exc:
        db.session.rollback()
        record_audit_log(
            "SECURITY_VIOLATION",
            f"Tentative interdite de permission individuelle sur '{target_user.username}' : {exc}",
            module="rbac",
            status="DENIED",
            target_type="User",
            target_id=target_user.id,
            commit=True,
        )
        flash(str(exc), "danger")

    return redirect(url_for("rbac_admin.inspect_user_permissions", user_id=target_user.id))


@rbac_bp.route("/roles-et-permissions/exception/<int:override_id>/supprimer", methods=["POST"])
@permission_required("rbac.manage", module="rbac")
def delete_user_override(override_id: int):
    ov_obj = db.session.get(User, 1)  # fallback uniquement pour redirection si erreur
    target_uid = None
    try:
        removed = remove_user_permission_override(override_id, actor=current_user)
        target_uid = removed.user_id
        record_audit_log(
            "RBAC_USER_OVERRIDE_REMOVE",
            f"Retrait de la permission individuelle '{removed.permission.code}' sur user_id={target_uid}",
            module="rbac",
            target_type="User",
            target_id=target_uid,
            commit=True,
        )
        flash("Permission individuelle exceptionnelle retirée.", "info")
    except RBACSecurityError as exc:
        db.session.rollback()
        record_audit_log(
            "SECURITY_VIOLATION",
            f"Refus de suppression d'exception RBAC #{override_id} : {exc}",
            module="rbac",
            status="DENIED",
            commit=True,
        )
        flash(str(exc), "danger")

    if target_uid:
        return redirect(url_for("rbac_admin.inspect_user_permissions", user_id=target_uid))
    return redirect(url_for("rbac_admin.index", tab="users"))


@rbac_bp.route("/etablissements/creer", methods=["POST"])
@permission_required("settings.manage", module="settings")
def create_establishment():
    if not is_super_admin(current_user):
        abort(403)
    code = (request.form.get("code") or "").strip().upper()[:50]
    name = (request.form.get("name") or "").strip()[:200]
    ceb = (request.form.get("ceb") or "").strip()[:150] or None
    locality = (request.form.get("locality") or "").strip()[:120] or None

    if not code or not name:
        flash("Le code et le nom de l'établissement sont obligatoires.", "danger")
        return redirect(url_for("rbac_admin.index", tab="establishments"))
    if Establishment.query.filter_by(code=code).first():
        flash(f"Le code établissement « {code} » existe déjà.", "danger")
        return redirect(url_for("rbac_admin.index", tab="establishments"))

    est = Establishment(code=code, name=name, ceb=ceb, locality=locality, is_active=True)
    db.session.add(est)
    db.session.flush()
    record_audit_log(
        "ESTABLISHMENT_CREATE",
        f"Création du nouvel établissement '{est.name}' ({est.code})",
        module="settings",
        establishment_id=est.id,
        target_type="Establishment",
        target_id=est.id,
        commit=True,
    )
    flash(f"Établissement « {est.name} » créé avec isolation des données.", "success")
    return redirect(url_for("rbac_admin.index", tab="establishments"))


@rbac_bp.route("/etablissements/basculer", methods=["POST"])
@login_required
def switch_establishment():
    est_id = request.form.get("establishment_id", type=int)
    if not est_id or not user_can_access_establishment(current_user, est_id):
        record_audit_log(
            "ACCESS_DENIED",
            f"Tentative de bascule vers l'établissement non autorisé #{est_id}",
            module="settings",
            status="DENIED",
            commit=True,
        )
        abort(403)
    est = db.get_or_404(Establishment, est_id)
    session["selected_establishment_id"] = est.id
    session.pop("selected_school_year_id", None)
    flash(f"Périmètre actif basculé sur : {est.name} ({est.code}).", "info")
    return redirect(request.referrer or url_for("dashboard.index"))


@rbac_bp.route("/journaux/exporter")
@permission_required("audit.export", module="audit")
def export_audit_logs_csv():
    query = AuditLog.query
    allowed_ests = get_user_accessible_establishment_ids(current_user)
    if allowed_ests is not None:
        query = query.filter(
            or_(AuditLog.establishment_id.in_(allowed_ests), AuditLog.establishment_id.is_(None))
        )
    logs = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(2000).all()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "ID",
            "Date_Heure_UTC",
            "Etablissement_ID",
            "Utilisateur",
            "Roles",
            "Module",
            "Action",
            "Resultat",
            "Cible",
            "Adresse_IP",
            "Resume",
        ]
    )
    for item in logs:
        writer.writerow(
            [
                item.id,
                item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else "",
                item.establishment_id or 1,
                item.username_snapshot,
                item.role_snapshot or "",
                item.module or "",
                item.action,
                item.status or "SUCCESS",
                f"{item.target_type or ''}:{item.target_id or ''}".strip(":"),
                item.ip_address or "",
                item.summary,
            ]
        )

    record_audit_log(
        "AUDIT_LOG_EXPORT",
        f"Export CSV du journal d'audit ({len(logs)} entrées)",
        module="audit",
        commit=True,
    )
    mem = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(
        mem,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="journal_audit_yelen_notes.csv",
    )
