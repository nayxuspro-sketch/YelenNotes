"""Configuration applicative Flask / SQLAlchemy / Sécurité."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from yelen_notes.paths import RuntimePaths, build_runtime_paths, get_or_create_secret_key, load_ini_config
from yelen_notes.version import APP_NAME, SCHEMA_VERSION, get_app_version


def build_flask_config(
    runtime_paths: RuntimePaths | None = None,
    server_mode: str = "monoposte",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Génère le dictionnaire de configuration Flask."""
    paths = runtime_paths or build_runtime_paths()
    ini_cfg = load_ini_config(paths.program_root)
    secret_key = get_or_create_secret_key(paths, ini_cfg)

    sqlite_uri = f"sqlite:///{Path(paths.db_path).as_posix()}"

    config: dict[str, Any] = {
        "APP_NAME": APP_NAME,
        "APP_VERSION": get_app_version(paths.program_root),
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "SERVER_MODE": server_mode,
        "IS_DEMO_MODE": paths.is_demo_mode,
        "RUNTIME_PATHS": paths,
        "SECRET_KEY": secret_key,
        "SQLALCHEMY_DATABASE_URI": sqlite_uri,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "connect_args": {
                "timeout": 15,
                "check_same_thread": False,
            },
            "pool_pre_ping": True,
        },
        "WTF_CSRF_ENABLED": True,
        "WTF_CSRF_TIME_LIMIT": 14400,  # 4h pour éviter l'expiration brutale en cours de saisie
        "MAX_CONTENT_LENGTH": 16 * 1024 * 1024,  # 16 Mo max (import Excel / logo / archive sauvegarde)
        "SESSION_COOKIE_NAME": "yelen_notes_session",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": False,
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=12),
        "JSON_AS_ASCII": False,
    }
    if overrides:
        config.update(overrides)
    return config
