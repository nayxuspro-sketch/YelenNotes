"""Sécurité côté serveur : RBAC, contrôle d'accès par classe, audit et récupération admin."""

from __future__ import annotations

import json
from datetime import timedelta
from functools import wraps
from typing import Any, Callable

from flask import abort, current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, logout_user

from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_ADMIN,
    ROLE_CONSULTATION,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    STATUS_OUVERTE,
    AcademicClass,
    AuditLog,
    EstablishmentSettings,
    EvaluationPeriod,
    TeacherClassAssignment,
    User,
    utcnow_naive,
)


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.remote_addr or "local")[:64]


def record_audit_log(
    action: str,
    summary: str,
    *,
    user: User | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = False,
) -> AuditLog:
    """Enregistre une entrée immuable dans le journal de sécurité (`audit_logs`)."""
    actor = user
    if actor is None and getattr(current_user, "is_authenticated", False):
        actor = current_user  # type: ignore[assignment]

    ip_addr = "cli"
    try:
        ip_addr = get_client_ip()
    except RuntimeError:
        pass

    entry = AuditLog(
        user_id=actor.id if actor else None,
        username_snapshot=actor.username if actor else "système",
        role_snapshot=actor.role if actor else None,
        ip_address=ip_addr,
        action=action[:60],
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


def roles_required(*allowed_roles: str) -> Callable:
    """Décorateur RBAC contrôlant strictement le rôle côté serveur (§2, §17 CdC)."""

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                if is_ajax_or_json_request():
                    return jsonify({"ok": False, "error": "Authentification requise."}), 401
                return redirect(url_for("auth.login", next=request.full_path))
            if not current_user.is_active or current_user.role not in allowed_roles:
                record_audit_log(
                    "ACCESS_DENIED",
                    f"Accès refusé sur {request.path} pour le rôle {current_user.role}",
                    commit=True,
                )
                if is_ajax_or_json_request():
                    return jsonify({"ok": False, "error": "Droits insuffisants pour cette action."}), 403
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def get_accessible_classes_query(user: User, school_year_id: int | None = None):
    """Retourne la requête des classes autorisées pour l'utilisateur donné."""
    query = AcademicClass.query
    if school_year_id is not None:
        query = query.filter(AcademicClass.school_year_id == school_year_id)
    if user.role in (ROLE_ADMIN, ROLE_DIRECTEUR, ROLE_CONSULTATION):
        return query.order_by(AcademicClass.display_order, AcademicClass.name)
    if user.role == ROLE_ENSEIGNANT:
        assigned_ids = db.select(TeacherClassAssignment.class_id).filter_by(user_id=user.id)
        return query.filter(AcademicClass.id.in_(assigned_ids)).order_by(
            AcademicClass.display_order, AcademicClass.name
        )
    return query.filter(db.false())


def user_can_view_class(user: User, class_id: int) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    if user.role in (ROLE_ADMIN, ROLE_DIRECTEUR, ROLE_CONSULTATION):
        return True
    if user.role == ROLE_ENSEIGNANT:
        assignment = TeacherClassAssignment.query.filter_by(
            user_id=user.id, class_id=class_id
        ).first()
        return assignment is not None
    return False


def user_can_edit_grades(
    user: User, academic_class: AcademicClass, period: EvaluationPeriod
) -> tuple[bool, str]:
    """Vérifie si l'utilisateur peut saisir ou modifier des notes pour cette classe et période."""
    if not user.is_authenticated or not user.is_active:
        return False, "Session non authentifiée."
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
    if user.role in (ROLE_ADMIN, ROLE_DIRECTEUR):
        return True, ""
    if user.role == ROLE_ENSEIGNANT:
        assignment = TeacherClassAssignment.query.filter_by(
            user_id=user.id, class_id=academic_class.id
        ).first()
        if assignment and assignment.can_edit_grades:
            return True, ""
        return False, "Vous n'êtes pas autorisé à saisir les notes de cette classe."
    return False, "Votre profil est limité à la consultation."


def enforce_session_security() -> Any:
    """Vérifie l'expiration d'inactivité et l'état actif du compte à chaque requête."""
    if request.endpoint and (
        request.endpoint.startswith("static")
        or request.endpoint in ("auth.login", "auth.bootstrap_admin", "health_check")
    ):
        return None

    # Si aucun utilisateur n'existe encore (base 100 % vierge), rediriger vers l'amorçage initial
    if User.query.count() == 0:
        return redirect(url_for("auth.bootstrap_admin"))

    if not current_user.is_authenticated:
        return None

    if not current_user.is_active:
        logout_user()
        session.clear()
        flash("Votre compte utilisateur a été désactivé.", "danger")
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
    """Procédure locale de récupération du compte administrateur (Architecture §7)."""
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
            role=ROLE_ADMIN,
            is_active=True,
            must_change_password=False,
        )
        db.session.add(user)
        created = True
    else:
        user.role = ROLE_ADMIN
        user.is_active = True
        user.failed_login_count = 0
        user.locked_until = None
        user.must_change_password = False

    user.set_password(new_password)
    db.session.flush()
    record_audit_log(
        "ADMIN_RECOVERY_CLI",
        f"{'Création' if created else 'Réinitialisation'} locale du compte administrateur '{clean_username}'",
        user=user,
        target_type="User",
        target_id=user.id,
        commit=True,
    )
    return user
