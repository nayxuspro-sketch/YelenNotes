"""Extensions partagées (SQLAlchemy, Flask-Migrate, Flask-Login, CSRFProtect) et PRAGMA SQLite."""

from __future__ import annotations

import sqlite3
from sqlalchemy import Engine, event
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()

login_manager.login_view = "auth.login"
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "warning"


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Applique les règles SQLite obligatoires (Architecture §4 : WAL, FK, busy_timeout)."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA busy_timeout = 10000;")
        cursor.execute("PRAGMA temp_store = MEMORY;")
        # En base fichier locale, activer WAL + synchronous NORMAL pour lectures/écritures multipostes
        db_list = cursor.execute("PRAGMA database_list;").fetchall()
        main_file = ""
        for row in db_list:
            if len(row) >= 3 and row[1] == "main":
                main_file = row[2] or ""
                break
        if main_file:
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
    finally:
        cursor.close()
