"""Fabrique d'application Flask (Application Factory) de Yelen Notes."""

from __future__ import annotations

from datetime import date, datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
from flask_migrate import upgrade as alembic_upgrade
from flask_wtf.csrf import CSRFError
from sqlalchemy import inspect
from werkzeug.middleware.proxy_fix import ProxyFix

from yelen_notes.blueprints import register_all_blueprints
from yelen_notes.blueprints.dashboard import get_selected_school_year
from yelen_notes.config import build_flask_config
from yelen_notes.extensions import csrf, db, login_manager, migrate
from yelen_notes.models import (
    ROLE_ADMIN,
    ROLE_CONSULTATION,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    Establishment,
    EstablishmentSettings,
    SchoolYear,
    User,
)
from yelen_notes.paths import RuntimePaths, build_runtime_paths
from yelen_notes.security import (
    enforce_session_security,
    ensure_rbac_initialized,
    get_accessible_years_query,
    get_current_establishment_id,
    get_user_accessible_establishment_ids,
    has_any_permission,
    has_permission,
    is_ajax_or_json_request,
    is_strictly_scoped_parent,
)
from yelen_notes.services.backup_service import create_safe_backup, maybe_trigger_automatic_backup
from yelen_notes.services.calculation_service import format_compact_decimal_fr, format_decimal_fr


def _configure_logging(app: Flask, paths: RuntimePaths) -> None:
    log_file = paths.logs_dir / "application.log"
    if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)


def initialize_database(app: Flask, run_migrations: bool = True) -> None:
    """Initialise ou met à jour le schéma SQLite avec sauvegarde préalable si migration."""
    paths: RuntimePaths = app.config["RUNTIME_PATHS"]
    migrations_dir = paths.program_root / "migrations"

    with app.app_context():
        db_existed = paths.db_path.is_file() and paths.db_path.stat().st_size > 0
        if run_migrations and migrations_dir.is_dir() and (migrations_dir / "env.py").is_file():
            if db_existed:
                insp = inspect(db.engine)
                has_alembic = insp.has_table("alembic_version")
                if has_alembic:
                    try:
                        create_safe_backup(
                            paths,
                            app_version=app.config["APP_VERSION"],
                            trigger_type="PRE_MIGRATION",
                            created_by="système",
                            notes="Sauvegarde automatique avant vérification/migration du schéma",
                            record_in_db=False,
                        )
                    except Exception as exc:
                        app.logger.warning("Sauvegarde pré-migration ignorée : %s", exc)
            alembic_upgrade(directory=str(migrations_dir))
        else:
            db.create_all()

        # Garantir l'existence de la ligne singleton des paramètres généraux (sans préconfigurer de règles pédagogiques)
        if inspect(db.engine).has_table("establishment_settings"):
            EstablishmentSettings.get_singleton()
        ensure_rbac_initialized()


def create_app(
    *,
    runtime_paths: RuntimePaths | None = None,
    server_mode: str = "monoposte",
    config_overrides: dict[str, Any] | None = None,
    auto_init_db: bool = True,
) -> Flask:
    """Crée et configure une instance complète de l'application Flask Yelen Notes."""
    paths = runtime_paths or build_runtime_paths()
    cfg = build_flask_config(paths, server_mode=server_mode, overrides=config_overrides)
    cfg.setdefault("WTF_CSRF_SSL_STRICT", False)

    app = Flask(
        __name__,
        template_folder=str(paths.templates_dir),
        static_folder=str(paths.static_dir),
        static_url_path="/static",
    )
    app.config.update(cfg)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # type: ignore[method-assign]

    _configure_logging(app, paths)
    if paths.is_unc_path:
        app.logger.critical(
            "ALERTE ARCHITECTURE : Le répertoire de données pointe vers un partage réseau UNC (%s). "
            "SQLite doit impérativement résider sur un disque local de l'ordinateur serveur.",
            paths.data_dir,
        )

    db.init_app(app)
    migrate.init_app(app, db, directory=str(paths.program_root / "migrations"))
    csrf.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def _load_user(user_id: str) -> User | None:
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    register_all_blueprints(app)

    @app.route("/etat-sante")
    def health_check():
        return jsonify(
            {
                "ok": True,
                "app": app.config["APP_NAME"],
                "version": app.config["APP_VERSION"],
                "mode": app.config["SERVER_MODE"],
                "demo": bool(app.config["IS_DEMO_MODE"]),
            }
        )

    @app.before_request
    def _before_every_request():
        sec_resp = enforce_session_security()
        if sec_resp is not None:
            return sec_resp
        # Sauvegarde automatique périodique non bloquante sur requêtes GET authentifiées
        if request.method == "GET" and not (request.endpoint or "").startswith("static"):
            try:
                maybe_trigger_automatic_backup(paths, app.config["APP_VERSION"])
            except Exception as exc:
                app.logger.warning("Échec sauvegarde automatique périodique : %s", exc)
        return None

    @app.after_request
    def _security_headers_and_cookies(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # Compatibilité transparente : SameSite=Lax sur HTTP local, SameSite=None; Secure derrière proxy HTTPS
        if request.is_secure:
            cookies = response.headers.getlist("Set-Cookie")
            if cookies:
                del response.headers["Set-Cookie"]
                for ck in cookies:
                    if "SameSite=Lax" in ck and "Secure" not in ck:
                        ck = ck.replace("SameSite=Lax", "SameSite=None; Secure")
                    response.headers.add("Set-Cookie", ck)
        return response

    # Filtres Jinja2 français
    @app.template_filter("dec_fr")
    def _filter_dec_fr(val, places: int = 2) -> str:
        return format_decimal_fr(val, decimal_places=places)

    @app.template_filter("compact_fr")
    def _filter_compact_fr(val) -> str:
        return format_compact_decimal_fr(val)

    @app.template_filter("date_fr")
    def _filter_date_fr(val: date | datetime | None) -> str:
        if val is None:
            return "—"
        return val.strftime("%d/%m/%Y")

    @app.template_filter("datetime_fr")
    def _filter_datetime_fr(val: datetime | None) -> str:
        if val is None:
            return "—"
        return val.strftime("%d/%m/%Y à %H:%M")

    @app.context_processor
    def _inject_global_template_vars() -> dict[str, Any]:
        from flask_login import current_user as _cu

        settings = None
        nav_years = []
        nav_selected_year = None
        nav_establishments = []
        nav_current_est = None
        try:
            insp = inspect(db.engine)
            if insp.has_table("establishment_settings"):
                settings = EstablishmentSettings.get_singleton()
                if getattr(_cu, "is_authenticated", False):
                    nav_years = get_accessible_years_query(_cu).all()
                    nav_selected_year = get_selected_school_year()
                    if insp.has_table("establishments"):
                        allowed_ests = get_user_accessible_establishment_ids(_cu)
                        if allowed_ests is None:
                            nav_establishments = Establishment.query.filter_by(is_active=True).order_by(Establishment.id).all()
                        elif allowed_ests:
                            nav_establishments = (
                                Establishment.query.filter(
                                    Establishment.id.in_(allowed_ests),
                                    Establishment.is_active.is_(True),
                                )
                                .order_by(Establishment.id)
                                .all()
                            )
                        cur_est_id = get_current_establishment_id(_cu)
                        nav_current_est = db.session.get(Establishment, cur_est_id)
        except Exception:
            pass

        def _can_perm(code: str) -> bool:
            return has_permission(_cu, code) if getattr(_cu, "is_authenticated", False) else False

        def _has_any_perm(*codes: str) -> bool:
            return has_any_permission(_cu, *codes) if getattr(_cu, "is_authenticated", False) else False

        return {
            "app_name": app.config["APP_NAME"],
            "app_version": app.config["APP_VERSION"],
            "schema_version": app.config["SCHEMA_VERSION"],
            "server_mode": app.config["SERVER_MODE"],
            "is_demo_mode": bool(app.config["IS_DEMO_MODE"]),
            "is_unc_warning": paths.is_unc_path,
            "current_establishment": settings,
            "nav_establishments": nav_establishments,
            "nav_current_est": nav_current_est,
            "nav_years": nav_years,
            "nav_selected_year": nav_selected_year,
            "can_perm": _can_perm,
            "has_any_perm": _has_any_perm,
            "is_parent_only": (is_strictly_scoped_parent(_cu) if getattr(_cu, "is_authenticated", False) else False),
            "ROLE_ADMIN": ROLE_ADMIN,
            "ROLE_DIRECTEUR": ROLE_DIRECTEUR,
            "ROLE_ENSEIGNANT": ROLE_ENSEIGNANT,
            "ROLE_CONSULTATION": ROLE_CONSULTATION,
        }

    # Gestionnaires d'erreurs propres sans fuite de détails techniques (§17 CdC)
    @app.errorhandler(CSRFError)
    def _handle_csrf_error(err: CSRFError):
        if is_ajax_or_json_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Jeton de sécurité CSRF expiré ou manquant. Rechargez la page.",
                    }
                ),
                400,
            )
        return (
            render_template(
                "errors/error.html",
                code=400,
                title="Jeton de sécurité expiré",
                message="Le formulaire est resté inactif trop longtemps ou le jeton CSRF est invalide. "
                "Veuillez revenir à la page précédente et réessayer.",
            ),
            400,
        )

    @app.errorhandler(403)
    def _handle_403(_err):
        if is_ajax_or_json_request():
            return jsonify({"ok": False, "error": "Accès refusé pour votre profil utilisateur."}), 403
        return (
            render_template(
                "errors/error.html",
                code=403,
                title="Accès non autorisé",
                message="Votre profil utilisateur ne dispose pas des droits requis pour effectuer cette opération.",
            ),
            403,
        )

    @app.errorhandler(404)
    def _handle_404(_err):
        if is_ajax_or_json_request():
            return jsonify({"ok": False, "error": "Ressource introuvable."}), 404
        return (
            render_template(
                "errors/error.html",
                code=404,
                title="Page ou élément introuvable",
                message="La page ou l'enregistrement demandé n'existe pas ou n'est plus disponible.",
            ),
            404,
        )

    @app.errorhandler(413)
    def _handle_413(_err):
        return (
            render_template(
                "errors/error.html",
                code=413,
                title="Fichier trop volumineux",
                message="Le fichier envoyé dépasse la taille maximale autorisée (16 Mo).",
            ),
            413,
        )

    @app.errorhandler(500)
    def _handle_500(err):
        db.session.rollback()
        app.logger.exception("Erreur interne inattendue : %s", err)
        if is_ajax_or_json_request():
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Une erreur interne est survenue. L'opération a été annulée en toute sécurité.",
                    }
                ),
                500,
            )
        return (
            render_template(
                "errors/error.html",
                code=500,
                title="Erreur inattendue de l'application",
                message="Une erreur interne a été enregistrée dans le journal technique. "
                "Aucune donnée n'a été altérée. Vous pouvez reprendre votre travail.",
            ),
            500,
        )

    if auto_init_db:
        initialize_database(app)

    return app
