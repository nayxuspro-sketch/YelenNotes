"""Enregistrement centralisé des Blueprints Flask."""

from flask import Flask

from yelen_notes.blueprints.academics import academics_bp
from yelen_notes.blueprints.admin import admin_bp
from yelen_notes.blueprints.auth import auth_bp
from yelen_notes.blueprints.dashboard import dashboard_bp
from yelen_notes.blueprints.grades import grades_bp
from yelen_notes.blueprints.operations import operations_bp
from yelen_notes.blueprints.rbac_admin import rbac_bp
from yelen_notes.blueprints.reports import reports_bp
from yelen_notes.blueprints.settings import settings_bp
from yelen_notes.blueprints.students import students_bp


def register_all_blueprints(app: Flask) -> None:
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(academics_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(grades_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(rbac_bp)
    app.register_blueprint(operations_bp)
