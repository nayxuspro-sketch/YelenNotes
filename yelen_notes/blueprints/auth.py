"""Authentification, amorçage du 1er administrateur sur base vierge et gestion du mot de passe."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_SUPER_ADMIN,
    Establishment,
    EstablishmentSettings,
    Role,
    User,
    UserRole,
    utcnow_naive,
)
from yelen_notes.security import ensure_rbac_initialized, invalidate_rbac_cache, record_audit_log

auth_bp = Blueprint("auth", __name__)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 5


def _safe_next_url(target: str | None) -> str:
    if not target:
        return url_for("dashboard.index")
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/"):
        return url_for("dashboard.index")
    return target


@auth_bp.route("/initialisation", methods=["GET", "POST"])
def bootstrap_admin():
    """Assistant d'amorçage lors du tout premier lancement sur une base 100 % vierge."""
    if User.query.count() > 0:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        full_name = (request.form.get("full_name") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        school_name = (request.form.get("school_name") or "").strip()
        ceb = (request.form.get("ceb") or "").strip()

        errors: list[str] = []
        if len(username) < 3 or " " in username:
            errors.append("L'identifiant doit comporter au moins 3 caractères sans espace.")
        if len(full_name) < 2:
            errors.append("Veuillez indiquer le nom complet de l'administrateur.")
        if len(password) < 8:
            errors.append("Le mot de passe doit contenir au moins 8 caractères.")
        if password != confirm:
            errors.append("La confirmation du mot de passe ne correspond pas.")

        if errors:
            for err in errors:
                flash(err, "danger")
            return render_template(
                "auth/bootstrap_admin.html",
                form_data=request.form,
            )

        ensure_rbac_initialized()
        admin_user = User(
            username=username,
            full_name=full_name,
            role=ROLE_SUPER_ADMIN,
            establishment_id=1,
            is_active=True,
            must_change_password=False,
            last_login_at=utcnow_naive(),
        )
        admin_user.set_password(password)
        db.session.add(admin_user)
        db.session.flush()

        sa_role = Role.query.filter_by(code=ROLE_SUPER_ADMIN).first()
        if sa_role is not None:
            db.session.add(UserRole(user_id=admin_user.id, role_id=sa_role.id))

        settings = EstablishmentSettings.get_singleton()
        est1 = db.session.get(Establishment, 1)
        if school_name:
            settings.school_name = school_name
            if est1 is not None:
                est1.name = school_name
        if ceb:
            settings.ceb = ceb
            if est1 is not None:
                est1.ceb = ceb

        invalidate_rbac_cache()

        record_audit_log(
            "INITIAL_BOOTSTRAP",
            f"Initialisation du premier compte administrateur '{username}' sur base vierge",
            user=admin_user,
            target_type="User",
            target_id=admin_user.id,
            commit=True,
        )

        login_user(admin_user)
        session.permanent = True
        session["last_activity_ts"] = int(utcnow_naive().timestamp())
        flash(
            "Compte administrateur créé avec succès. Bienvenue dans Yelen Notes ! "
            "Suivez le guide de configuration ci-dessous pour paramétrer votre école.",
            "success",
        )
        return redirect(url_for("dashboard.index"))

    return render_template("auth/bootstrap_admin.html", form_data={})


@auth_bp.route("/connexion", methods=["GET", "POST"])
def login():
    if User.query.count() == 0:
        return redirect(url_for("auth.bootstrap_admin"))

    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        next_url = request.form.get("next") or request.args.get("next")

        user = User.query.filter_by(username=username).first()
        now = utcnow_naive()

        if user is not None and user.is_locked(now):
            remaining_min = max(1, int(((user.locked_until - now).total_seconds() + 59) // 60))
            record_audit_log(
                "AUTH_LOCKED",
                f"Tentative de connexion sur le compte temporairement verrouillé '{username}'",
                user=user,
                commit=True,
            )
            flash(
                f"Ce compte est temporairement verrouillé suite à plusieurs échecs. Réessayez dans {remaining_min} min.",
                "danger",
            )
            return render_template("auth/login.html", next_url=next_url), 429

        if user is not None and user.is_suspended(now):
            until_str = user.suspended_until.strftime("%d/%m/%Y à %H:%M") if user.suspended_until else ""
            record_audit_log(
                "AUTH_SUSPENDED",
                f"Tentative de connexion sur le compte suspendu '{username}'",
                user=user,
                module="auth",
                status="DENIED",
                commit=True,
            )
            flash(
                f"Votre accès est temporairement suspendu jusqu'au {until_str}.",
                "warning",
            )
            return render_template("auth/login.html", next_url=next_url), 403

        if user is None or not user.is_active or not user.check_password(password):
            if user is not None:
                user.failed_login_count = (user.failed_login_count or 0) + 1
                if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
                    user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            record_audit_log(
                "AUTH_FAIL",
                f"Échec d'authentification pour l'identifiant '{username or 'inconnu'}'",
                user=user,
                commit=True,
            )
            flash("Identifiant ou mot de passe incorrect, ou compte inactif.", "danger")
            return render_template("auth/login.html", next_url=next_url), 401

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        login_user(user)
        session.permanent = True
        session["last_activity_ts"] = int(now.timestamp())
        record_audit_log(
            "AUTH_LOGIN",
            f"Connexion réussie de '{user.username}' ({user.role})",
            user=user,
            commit=True,
        )
        if user.must_change_password:
            return redirect(url_for("auth.change_password"))
        return redirect(_safe_next_url(next_url))

    return render_template(
        "auth/login.html",
        next_url=request.args.get("next", ""),
        is_demo=bool(current_app.config.get("IS_DEMO_MODE")),
    )


@auth_bp.route("/deconnexion", methods=["POST"])
@login_required
def logout():
    record_audit_log(
        "AUTH_LOGOUT",
        f"Déconnexion de '{current_user.username}'",
        commit=True,
    )
    logout_user()
    session.clear()
    flash("Vous avez été déconnecté en toute sécurité.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/mon-mot-de-passe", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_pw = request.form.get("current_password") or ""
        new_pw = request.form.get("new_password") or ""
        confirm_pw = request.form.get("confirm_password") or ""

        if not current_user.check_password(current_pw):
            flash("Le mot de passe actuel est incorrect.", "danger")
            return render_template("auth/change_password.html")
        if len(new_pw) < 8:
            flash("Le nouveau mot de passe doit contenir au moins 8 caractères.", "danger")
            return render_template("auth/change_password.html")
        if new_pw != confirm_pw:
            flash("La confirmation du nouveau mot de passe ne correspond pas.", "danger")
            return render_template("auth/change_password.html")
        if new_pw == current_pw:
            flash("Veuillez choisir un mot de passe différent de l'ancien.", "warning")
            return render_template("auth/change_password.html")

        current_user.set_password(new_pw)
        current_user.must_change_password = False
        record_audit_log(
            "PASSWORD_CHANGE",
            f"Changement de mot de passe effectué par '{current_user.username}'",
            commit=True,
        )
        flash("Votre mot de passe a été mis à jour avec succès.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/change_password.html")
