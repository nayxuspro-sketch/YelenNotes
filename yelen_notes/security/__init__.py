"""Sécurité côté serveur : RBAC centralisé, contrôle d'accès aux données/établissements, audit et récupération."""

from __future__ import annotations

import json
from functools import wraps
from typing import Any, Callable

from flask import abort, current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, logout_user
from sqlalchemy import or_

from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    STATUS_OUVERTE,
    AcademicClass,
    AuditLog,
    ClassEnrollment,
    EstablishmentSettings,
    EvaluationPeriod,
    Role,
    SchoolYear,
    TeacherClassAssignment,
    User,
    UserRole,
    utcnow_naive,
)
from yelen_notes.security.rbac_service import (
    RBACSecurityError,
    assign_roles_to_user,
    create_custom_permission,
    create_role,
    delete_role,
    ensure_rbac_initialized,
    ensure_user_role_synced,
    get_current_establishment_id,
    get_effective_permissions,
    get_parent_linked_student_ids,
    get_user_accessible_establishment_ids,
    has_any_permission,
    has_permission,
    invalidate_rbac_cache,
    is_strictly_scoped_parent,
    is_strictly_scoped_teacher,
    is_super_admin,
    remove_user_permission_override,
    set_user_permission_override,
    update_role,
    update_role_permissions,
    user_can_access_establishment,
)


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.remote_addr or "local")[:64]


def _infer_audit_module(action: str) -> str:
    act = (action or "").upper()
    if act.startswith(("AUTH_", "INITIAL_BOOTSTRAP", "ADMIN_RECOVERY")):
        return "auth"
    if act.startswith(("RBAC_", "ROLE_", "PERM_")):
        return "rbac"
    if act.startswith("USER_"):
        return "users"
    if act.startswith(("STUDENT_", "EXCEL_STUDENT", "YEAR_TRANSITION", "CLASS_RENUMBER")):
        return "students"
    if act.startswith("CLASS_"):
        return "classes"
    if act.startswith(("SUBJECT_", "CALCULATION_POLICY", "POLICY_")):
        return "subjects"
    if act.startswith(("GRADE_", "OBSERVATIONS_")):
        return "grades"
    if act.startswith(("SCHOOL_YEAR_", "PERIOD_")):
        return "evaluations"
    if act.startswith("PROPOSITION_"):
        return "proposition"
    if act.startswith(("BULLETIN_", "RELEVE_", "RANKING_", "SYNOPTIC_")):
        return "bulletins"
    if act.startswith("ABSENCE_"):
        return "absences"
    if act.startswith("ACCOUNTING_"):
        return "accounting"
    if act.startswith(("BACKUP_", "ESTABLISHMENT_")):
        return "settings"
    try:
        ep = request.endpoint or ""
        if "." in ep:
            return ep.split(".", 1)[0][:60]
    except RuntimeError:
        pass
    return "system"


def record_audit_log(
    action: str,
    summary: str,
    *,
    user: User | None = None,
    module: str | None = None,
    status: str | None = None,
    establishment_id: int | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = False,
) -> AuditLog:
    """Enregistre une entrée immuable et enrichie dans le journal d'audit (`audit_logs`, §9 RBAC)."""
    actor = user
    if actor is None and getattr(current_user, "is_authenticated", False):
        actor = current_user  # type: ignore[assignment]

    ip_addr = "cli"
    try:
        ip_addr = get_client_ip()
    except RuntimeError:
        pass

    eff_status = status
    if eff_status is None:
        if action in ("ACCESS_DENIED", "AUTH_FAIL", "AUTH_LOCKED", "SECURITY_VIOLATION"):
            eff_status = "DENIED"
        else:
            eff_status = "SUCCESS"

    eff_est_id = establishment_id
    if eff_est_id is None and actor is not None:
        eff_est_id = actor.establishment_id or 1
    if eff_est_id is None:
        eff_est_id = 1

    role_snap = None
    if actor is not None:
        codes = actor.effective_role_codes
        role_snap = ",".join(codes)[:120] if codes else actor.role

    entry = AuditLog(
        establishment_id=eff_est_id,
        user_id=actor.id if actor else None,
        username_snapshot=actor.username if actor else "système",
        role_snapshot=role_snap,
        ip_address=ip_addr,
        module=(module or _infer_audit_module(action))[:60],
        action=action[:60],
        status=eff_status[:20],
        target_type=target_type[:60] if target_type else None,
        target_id=str(target_id)[:60] if target_id is not None else None,
        summary=summary[:300],
        details_json=json.dumps(details, ensure_ascii=False, default=str) if details else None,
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry


def is_ajax_or_json_request() -> bool:
    accept = request.headers.get("Accept", "")
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in accept
    )


def _deny_current_request(reason_summary: str, module: str | None = None):
    record_audit_log(
        "ACCESS_DENIED",
        reason_summary,
        module=module,
        status="DENIED",
        commit=True,
    )
    if is_ajax_or_json_request():
        return jsonify({"ok": False, "error": "Droits insuffisants pour cette action."}), 403
    abort(403)


def permission_required(*required_perms: str, module: str | None = None) -> Callable:
    """Décorateur RBAC centralisé : exige toutes les permissions indiquées (sécurité par défaut)."""

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                if is_ajax_or_json_request():
                    return jsonify({"ok": False, "error": "Authentification requise."}), 401
                return redirect(url_for("auth.login", next=request.full_path))
            if not current_user.is_active or current_user.is_suspended() or current_user.is_locked():
                return _deny_current_request(
                    f"Accès refusé (compte inactif/suspendu) sur {request.path}", module=module
                )
            eff = get_effective_permissions(current_user)
            missing = [p for p in required_perms if p not in eff]
            if missing:
                return _deny_current_request(
                    f"Permission(s) manquante(s) {missing} sur {request.path} pour {current_user.username}",
                    module=module,
                )
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def any_permission_required(*any_perms: str, module: str | None = None) -> Callable:
    """Décorateur RBAC centralisé : exige au moins l'une des permissions listées."""

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                if is_ajax_or_json_request():
                    return jsonify({"ok": False, "error": "Authentification requise."}), 401
                return redirect(url_for("auth.login", next=request.full_path))
            if not current_user.is_active or current_user.is_suspended() or current_user.is_locked():
                return _deny_current_request(
                    f"Accès refusé (compte inactif/suspendu) sur {request.path}", module=module
                )
            if not has_any_permission(current_user, *any_perms):
                return _deny_current_request(
                    f"Aucune des permissions {list(any_perms)} détenue sur {request.path} par {current_user.username}",
                    module=module,
                )
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def roles_required(*allowed_roles: str) -> Callable:
    """Décorateur de compatibilité vérifiant les rôles actifs dans le moteur RBAC centralisé."""

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                if is_ajax_or_json_request():
                    return jsonify({"ok": False, "error": "Authentification requise."}), 401
                return redirect(url_for("auth.login", next=request.full_path))
            if not current_user.is_active or current_user.is_suspended() or not current_user.has_role(*allowed_roles):
                return _deny_current_request(
                    f"Accès refusé sur {request.path} pour {current_user.username} ({current_user.role_label})"
                )
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def _apply_establishment_filter(query, model_est_col, user: User):
    allowed_ests = get_user_accessible_establishment_ids(user)
    if allowed_ests is None:
        return query
    if not allowed_ests:
        return query.filter(db.false())
    if 1 in allowed_ests:
        return query.filter(or_(model_est_col.in_(allowed_ests), model_est_col.is_(None)))
    return query.filter(model_est_col.in_(allowed_ests))


def get_accessible_years_query(user: User):
    """Retourne les années scolaires appartenant au périmètre d'établissement de l'utilisateur (§8)."""
    if not user or not user.is_authenticated or not user.is_active or user.is_suspended():
        return SchoolYear.query.filter(db.false())
    q = _apply_establishment_filter(SchoolYear.query, SchoolYear.establishment_id, user)
    return q.order_by(SchoolYear.label.desc())


def get_accessible_classes_query(user: User, school_year_id: int | None = None):
    """Retourne la requête des classes autorisées selon les permissions RBAC, l'établissement et les affectations."""
    query = AcademicClass.query
    if not user or not user.is_authenticated or not user.is_active or user.is_suspended():
        return query.filter(db.false())

    # Un parent n'accède jamais aux listes complètes de classe (uniquement à ses propres enfants)
    if is_strictly_scoped_parent(user):
        return query.filter(db.false())

    # Règle absolue : refuser si aucune permission de consultation pédagogique/administrative sur les classes
    if not has_any_permission(
        user,
        "classes.view",
        "students.view",
        "grades.view",
        "bulletins.view",
        "proposition.view",
        "absences.view",
    ):
        return query.filter(db.false())

    query = _apply_establishment_filter(query, AcademicClass.establishment_id, user)
    if school_year_id is not None:
        query = query.filter(AcademicClass.school_year_id == school_year_id)

    if is_strictly_scoped_teacher(user):
        assigned_ids = db.select(TeacherClassAssignment.class_id).filter_by(user_id=user.id)
        return query.filter(AcademicClass.id.in_(assigned_ids)).order_by(
            AcademicClass.display_order, AcademicClass.name
        )

    return query.order_by(AcademicClass.display_order, AcademicClass.name)


def user_can_view_class(user: User, class_id: int) -> bool:
    if not user or not user.is_authenticated or not user.is_active or user.is_suspended():
        return False
    ac = db.session.get(AcademicClass, class_id)
    if ac is None:
        return False
    if not user_can_access_establishment(user, ac.establishment_id or ac.school_year.establishment_id):
        return False
    if is_strictly_scoped_parent(user):
        return False
    if not has_any_permission(
        user,
        "classes.view",
        "students.view",
        "grades.view",
        "bulletins.view",
        "proposition.view",
        "absences.view",
    ):
        return False
    if is_strictly_scoped_teacher(user):
        assignment = TeacherClassAssignment.query.filter_by(
            user_id=user.id, class_id=class_id
        ).first()
        return assignment is not None
    return True


def user_can_view_enrollment(user: User, enrollment: ClassEnrollment | None) -> bool:
    """Vérifie si l'utilisateur peut consulter le dossier / bulletin d'un élève donné (incluant Parent / Tuteur)."""
    if enrollment is None or not user or not user.is_authenticated or not user.is_active or user.is_suspended():
        return False
    ac = enrollment.academic_class
    if not user_can_access_establishment(user, ac.establishment_id or ac.school_year.establishment_id):
        return False
    if is_strictly_scoped_parent(user):
        if not has_any_permission(user, "students.view", "bulletins.view"):
            return False
        return enrollment.student_id in get_parent_linked_student_ids(user)
    if not has_any_permission(user, "students.view", "grades.view", "bulletins.view", "absences.view"):
        return False
    return user_can_view_class(user, ac.id)


def user_can_create_class_students(user: User, academic_class: AcademicClass | None) -> bool:
    """Vérifie si l'utilisateur peut inscrire ou importer de nouveaux élèves dans la classe."""
    if academic_class is None or not user.is_authenticated or not user.is_active or user.is_suspended():
        return False
    if academic_class.school_year.is_closed:
        return False
    if not user_can_access_establishment(
        user, academic_class.establishment_id or academic_class.school_year.establishment_id
    ):
        return False
    if not has_any_permission(user, "students.create", "enrollments.create"):
        return False
    if is_strictly_scoped_teacher(user):
        if not academic_class.is_active:
            return False
        assignment = TeacherClassAssignment.query.filter_by(
            user_id=user.id, class_id=academic_class.id
        ).first()
        return bool(assignment and assignment.can_edit_grades)
    return True


def user_can_edit_class_students(user: User, academic_class: AcademicClass | None) -> bool:
    """Vérifie si l'utilisateur peut modifier les identités et informations des élèves de la classe."""
    if academic_class is None or not user.is_authenticated or not user.is_active or user.is_suspended():
        return False
    if academic_class.school_year.is_closed:
        return False
    if not user_can_access_establishment(
        user, academic_class.establishment_id or academic_class.school_year.establishment_id
    ):
        return False
    if not has_any_permission(user, "students.update", "enrollments.update"):
        return False
    if is_strictly_scoped_teacher(user):
        if not academic_class.is_active:
            return False
        assignment = TeacherClassAssignment.query.filter_by(
            user_id=user.id, class_id=academic_class.id
        ).first()
        return bool(assignment and assignment.can_edit_grades)
    return True


def user_can_manage_class_subjects(user: User, academic_class: AcademicClass | None) -> bool:
    """Vérifie si l'utilisateur peut ajouter et modifier les matières et barèmes de la classe."""
    if academic_class is None or not user.is_authenticated or not user.is_active or user.is_suspended():
        return False
    if academic_class.school_year.is_closed:
        return False
    if not user_can_access_establishment(
        user, academic_class.establishment_id or academic_class.school_year.establishment_id
    ):
        return False
    if not has_any_permission(user, "subjects.create", "subjects.update", "subjects.manage"):
        return False
    if is_strictly_scoped_teacher(user):
        if not academic_class.is_active:
            return False
        assignment = TeacherClassAssignment.query.filter_by(
            user_id=user.id, class_id=academic_class.id
        ).first()
        return bool(assignment and assignment.can_edit_grades)
    return True


def user_can_modify_year_periods(user: User, school_year: SchoolYear | None) -> bool:
    """Vérifie si l'utilisateur (Super Admin, Directeur, Admin Étab ou Titulaire d'une classe active de l'année) peut modifier les périodes créées."""
    if school_year is None or not user or not user.is_authenticated or not user.is_active or user.is_suspended():
        return False
    if school_year.is_closed:
        return False
    if not user_can_access_establishment(user, school_year.establishment_id):
        return False
    if not has_any_permission(user, "evaluations.update", "periods.update"):
        return False
    if is_strictly_scoped_teacher(user):
        titulaire_assignment = (
            TeacherClassAssignment.query.join(
                AcademicClass, TeacherClassAssignment.class_id == AcademicClass.id
            )
            .filter(
                TeacherClassAssignment.user_id == user.id,
                TeacherClassAssignment.can_edit_grades.is_(True),
                AcademicClass.school_year_id == school_year.id,
                AcademicClass.is_active.is_(True),
            )
            .first()
        )
        return titulaire_assignment is not None
    return True


def user_can_edit_grades(
    user: User, academic_class: AcademicClass, period: EvaluationPeriod
) -> tuple[bool, str]:
    """Vérifie via le moteur RBAC si l'utilisateur peut saisir ou modifier des notes pour cette classe et période."""
    if not user.is_authenticated or not user.is_active or user.is_suspended():
        return False, "Session non authentifiée ou suspendue."
    if not user_can_access_establishment(
        user, academic_class.establishment_id or academic_class.school_year.establishment_id
    ):
        return False, "Cette classe n'appartient pas à votre établissement autorisé."
    if academic_class.school_year_id != period.school_year_id:
        return False, "La période sélectionnée n'appartient pas à l'année scolaire de la classe."
    if academic_class.school_year.is_closed:
        return False, "L'année scolaire est clôturée : aucune modification des notes n'est autorisée."
    if not academic_class.is_active:
        return False, "Cette classe est désactivée."
    if period.status != STATUS_OUVERTE:
        return (
            False,
            f"La période « {period.name} » a le statut {period.status} : seules les périodes OUVERTES acceptent la saisie.",
        )
    if not has_any_permission(user, "grades.create", "grades.update"):
        return False, "Votre rôle ne dispose pas de la permission de saisie ou modification des notes."
    if is_strictly_scoped_teacher(user):
        assignment = TeacherClassAssignment.query.filter_by(
            user_id=user.id, class_id=academic_class.id
        ).first()
        if assignment and assignment.can_edit_grades:
            return True, ""
        return False, "Vous n'êtes pas autorisé à saisir les notes de cette classe."
    return True, ""


def enforce_session_security() -> Any:
    """Vérifie l'expiration d'inactivité, la désactivation et la suspension temporaire à chaque requête."""
    if request.endpoint and (
        request.endpoint.startswith("static")
        or request.endpoint in ("auth.login", "auth.bootstrap_admin", "health_check")
    ):
        return None

    if User.query.count() == 0:
        return redirect(url_for("auth.bootstrap_admin"))

    if not current_user.is_authenticated:
        return None

    if not current_user.is_active:
        logout_user()
        session.clear()
        if is_ajax_or_json_request():
            return jsonify({"ok": False, "error": "Votre compte utilisateur a été désactivé."}), 403
        flash("Votre compte utilisateur a été désactivé.", "danger")
        return redirect(url_for("auth.login"))

    if current_user.is_suspended():
        until_str = current_user.suspended_until.strftime("%d/%m/%Y à %H:%M") if current_user.suspended_until else ""
        logout_user()
        session.clear()
        if is_ajax_or_json_request():
            return jsonify({"ok": False, "error": "Accès temporairement suspendu."}), 403
        flash(f"Votre accès est temporairement suspendu jusqu'au {until_str}.", "warning")
        return redirect(url_for("auth.login"))

    settings = EstablishmentSettings.get_singleton()
    timeout_minutes = max(5, settings.session_timeout_minutes or 30)
    now_ts = int(utcnow_naive().timestamp())
    last_seen = session.get("last_activity_ts")
    if last_seen is not None and (now_ts - int(last_seen)) > timeout_minutes * 60:
        username = current_user.username
        logout_user()
        session.clear()
        current_app.logger.info("Session expirée par inactivité pour %s", username)
        if is_ajax_or_json_request():
            return jsonify({"ok": False, "error": "Session expirée pour inactivité. Veuillez vous reconnecter."}), 401
        flash("Votre session a expiré après une période d'inactivité. Veuillez vous reconnecter.", "warning")
        return redirect(url_for("auth.login"))

    session["last_activity_ts"] = now_ts
    if current_user.must_change_password and request.endpoint not in (
        "auth.change_password",
        "auth.logout",
    ):
        flash("Vous devez définir un nouveau mot de passe personnel avant de continuer.", "warning")
        return redirect(url_for("auth.change_password"))
    return None


def reset_or_create_admin_account(username: str, new_password: str, full_name: str = "Administrateur") -> User:
    """Procédure locale de récupération du compte Super administrateur (Architecture §7)."""
    ensure_rbac_initialized()
    clean_username = username.strip().lower()
    if len(clean_username) < 3:
        raise ValueError("L'identifiant administrateur doit contenir au moins 3 caractères.")
    if len(new_password) < 8:
        raise ValueError("Le mot de passe administrateur doit contenir au moins 8 caractères.")

    user = User.query.filter_by(username=clean_username).first()
    created = False
    if user is None:
        user = User(
            username=clean_username,
            full_name=full_name.strip() or "Administrateur",
            role=ROLE_SUPER_ADMIN,
            is_active=True,
            must_change_password=False,
        )
        db.session.add(user)
        db.session.flush()
        created = True
    else:
        user.role = ROLE_SUPER_ADMIN
        user.is_active = True
        user.suspended_until = None
        user.suspension_reason = None
        user.failed_login_count = 0
        user.locked_until = None
        user.must_change_password = False

    sa_role = Role.query.filter_by(code=ROLE_SUPER_ADMIN).first()
    if sa_role is not None and not UserRole.query.filter_by(user_id=user.id, role_id=sa_role.id).first():
        db.session.add(UserRole(user_id=user.id, role_id=sa_role.id))

    user.set_password(new_password)
    db.session.flush()
    invalidate_rbac_cache()
    record_audit_log(
        "ADMIN_RECOVERY_CLI",
        f"{'Création' if created else 'Réinitialisation'} locale du compte administrateur '{clean_username}'",
        user=user,
        module="users",
        target_type="User",
        target_id=user.id,
        commit=True,
    )
    return user


__all__ = [
    "RBACSecurityError",
    "any_permission_required",
    "assign_roles_to_user",
    "create_custom_permission",
    "create_role",
    "delete_role",
    "enforce_session_security",
    "ensure_rbac_initialized",
    "ensure_user_role_synced",
    "get_accessible_classes_query",
    "get_accessible_years_query",
    "get_client_ip",
    "get_current_establishment_id",
    "get_effective_permissions",
    "get_parent_linked_student_ids",
    "get_user_accessible_establishment_ids",
    "has_any_permission",
    "has_permission",
    "invalidate_rbac_cache",
    "is_ajax_or_json_request",
    "is_strictly_scoped_parent",
    "is_strictly_scoped_teacher",
    "is_super_admin",
    "permission_required",
    "record_audit_log",
    "remove_user_permission_override",
    "reset_or_create_admin_account",
    "roles_required",
    "set_user_permission_override",
    "update_role",
    "update_role_permissions",
    "user_can_access_establishment",
    "user_can_create_class_students",
    "user_can_edit_class_students",
    "user_can_edit_grades",
    "user_can_manage_class_subjects",
    "user_can_modify_year_periods",
    "user_can_view_class",
    "user_can_view_enrollment",
]
