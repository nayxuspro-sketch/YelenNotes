"""Tests du module de mise à jour en ligne/hors ligne, sauvegardes et cloisonnement des éditions."""

from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from tests.conftest import extract_csrf_token
import run as yelen_entrypoint
from yelen_notes.extensions import db
from yelen_notes.models import ROLE_ADMIN_ETAB, ROLE_SUPER_ADMIN, User
from yelen_notes.security import ensure_user_role_synced
from yelen_notes.services.license_service import CLIENT_LICENSE_FILENAME
from yelen_notes.services.update_service import (
    UpdateError,
    apply_program_update,
    check_online_update,
    inspect_and_validate_update_zip,
    is_newer_version,
    parse_semver,
)


def _make_update_zip(version: str = "1.3.2", *, extra: dict[str, bytes] | None = None) -> bytes:
    prefix = f"YelenNotes-{version}/"
    files = {
        "VERSION.txt": version.encode(),
        "run.py": b"# new launcher\n",
        "yelen_notes/__init__.py": b"# updated package\n",
        "resources/templates/base.html": b"<!-- new template -->\n",
        "migrations/versions/update_test.py": b"# migration\n",
        "config/yelen_deployment_edition.json": b"ATTACKER PRIVATE KEY",
        "config/yelen_notes.ini": b"[security]\nsecret_key=attacker\n",
        "data/database/ecole.db": b"ATTACKER DATABASE",
    }
    files.update(extra or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(prefix + name, content)
    return stream.getvalue()


def _isolated_paths(app, tmp_path, *, deployment: bool):
    original = app.config["RUNTIME_PATHS"]
    root = tmp_path / ("deployment" if deployment else "client")
    config_dir = root / "config"
    data_dir = tmp_path / ("deployment-data" if deployment else "client-data")
    config_dir.mkdir(parents=True)
    (root / "resources" / "templates").mkdir(parents=True)
    (root / "yelen_notes").mkdir(parents=True)
    (root / "run.py").write_text("# old run\n", encoding="utf-8")
    (root / "VERSION.txt").write_text("1.3.1\n", encoding="utf-8")
    (config_dir / "yelen_notes.ini").write_text("[security]\nsecret_key=local-secret\n", encoding="utf-8")
    if deployment:
        shutil.copy2(original.config_dir / "yelen_deployment_edition.json", config_dir)
        shutil.copy2(original.config_dir / "yelen_publisher_pub.json", config_dir)
    else:
        (config_dir / "yelen_publisher_pub.json").write_bytes(
            (original.config_dir / "yelen_publisher_pub.json").read_bytes()
        )
    # L'archive contient aussi volontairement un faux fichier de licence à écraser.
    (config_dir / CLIENT_LICENSE_FILENAME).write_text("LOCAL-YLIC", encoding="utf-8")
    data_dir.mkdir(parents=True)
    db_dir = data_dir / "database"
    db_dir.mkdir()
    shutil.copy2(original.db_path, db_dir / original.db_path.name)
    paths = replace(
        original,
        program_root=root,
        resources_dir=root / "resources",
        templates_dir=root / "resources" / "templates",
        static_dir=root / "resources" / "static",
        fonts_dir=root / "resources" / "static" / "fonts",
        config_dir=config_dir,
        data_dir=data_dir,
        database_dir=db_dir,
        db_path=db_dir / original.db_path.name,
        backups_dir=data_dir / "backups",
        documents_dir=data_dir / "documents",
        bulletins_dir=data_dir / "documents" / "bulletins",
        releves_dir=data_dir / "documents" / "releves",
        logos_dir=data_dir / "logos",
        logs_dir=data_dir / "logs",
    )
    return paths


def test_windows_restart_stop_does_not_kill_its_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(yelen_entrypoint.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))

    yelen_entrypoint._terminate_windows_server(4321)

    assert calls[0][0] == ["taskkill", "/PID", "4321", "/F"]
    assert "/T" not in calls[0][0]
    assert calls[0][1]["check"] is False


def test_online_release_check_semver_and_network_failure(app, tmp_path):
    paths = app.config["RUNTIME_PATHS"]
    assert parse_semver("v1.10.0-rc.2") == (1, 10, 0)
    assert is_newer_version("1.10.0", "1.9.9")
    assert not is_newer_version("1.3.1", "1.3.1.0")

    payload = json.dumps({
        "tag_name": "v1.4.0",
        "name": "Yelen Notes 1.4.0",
        "body": "Notes officielles",
        "published_at": "2026-11-25T12:00:00Z",
    }).encode()
    result = check_online_update(paths, "1.3.1", fetcher=lambda *_a, **_k: payload)
    assert result.reachable is True
    assert result.update_available is True
    assert result.latest_version == "1.4.0"
    assert result.download_tag == "v1.4.0"

    custom_root = tmp_path / "configured-install"
    (custom_root / "config").mkdir(parents=True)
    (custom_root / "config" / "yelen_notes.ini").write_text(
        "[updates]\ngithub_repo = example-school/yelen-notes\n", encoding="utf-8"
    )
    custom_paths = replace(paths, program_root=custom_root)
    requested_urls = []
    custom_check = check_online_update(
        custom_paths,
        "1.3.1",
        fetcher=lambda url, **_kwargs: (requested_urls.append(url) or payload),
    )
    assert custom_check.repository == "example-school/yelen-notes"
    assert requested_urls == ["https://api.github.com/repos/example-school/yelen-notes/releases/latest"]

    offline = check_online_update(paths, "1.3.1", fetcher=lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline")))
    assert offline.reachable is False
    assert "archive .zip" in offline.error_message


def test_archive_validation_rejects_traversal_and_incomplete_package():
    good_version, prefix = inspect_and_validate_update_zip(_make_update_zip())
    assert (good_version, prefix) == ("1.3.2", "YelenNotes-1.3.2/")

    traversal = _make_update_zip(extra={"../../escape.txt": b"bad"})
    with pytest.raises(UpdateError, match="chemin relatif illégal"):
        inspect_and_validate_update_zip(traversal)

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("VERSION.txt", "1.3.2")
    with pytest.raises(UpdateError, match="run.py manquant"):
        inspect_and_validate_update_zip(stream.getvalue())


def test_apply_update_preserves_school_data_deployment_key_and_local_config(app, tmp_path):
    paths = _isolated_paths(app, tmp_path, deployment=True)
    deployment_key = (paths.config_dir / "yelen_deployment_edition.json").read_bytes()
    local_ini = (paths.config_dir / "yelen_notes.ini").read_bytes()
    local_pub_key = (paths.config_dir / "yelen_publisher_pub.json").read_bytes()
    license_file = paths.config_dir / CLIENT_LICENSE_FILENAME
    data_bytes = paths.db_path.read_bytes()
    archive = _make_update_zip("1.3.2")

    with app.app_context():
        info = apply_program_update(
            paths,
            archive,
            current_version="1.3.1",
            actor_username="super_root",
            source_label="Test ZIP",
        )
    assert info.new_version == "1.3.2"
    assert info.edition_preserved == "Version de déploiement Super Admin"
    assert info.backup_filename.startswith("yelen_notes_backup_pre_update_")
    assert (paths.program_root / "run.py").read_text(encoding="utf-8") == "# new launcher\n"
    assert (paths.config_dir / "yelen_deployment_edition.json").read_bytes() == deployment_key
    assert (paths.config_dir / "yelen_notes.ini").read_bytes() == local_ini
    assert (paths.config_dir / "yelen_publisher_pub.json").read_bytes() == local_pub_key
    assert license_file.read_text(encoding="utf-8") == "LOCAL-YLIC"
    assert paths.db_path.read_bytes() == data_bytes
    assert (paths.backups_dir / info.backup_filename).is_file()
    history = json.loads((paths.data_dir / "update_history.json").read_text(encoding="utf-8"))
    assert history[0]["new_version"] == "1.3.2"


def test_apply_client_update_never_imports_private_key_and_never_downgrades(app, tmp_path):
    paths = _isolated_paths(app, tmp_path, deployment=False)
    assert not (paths.config_dir / "yelen_deployment_edition.json").exists()
    license_file = paths.config_dir / CLIENT_LICENSE_FILENAME

    with app.app_context():
        info = apply_program_update(
            paths,
            _make_update_zip("1.3.2"),
            current_version="1.3.1",
            actor_username="super_root",
            source_label="Test ZIP client",
        )
    assert info.edition_preserved == "Poste Client École (sous licence)"
    assert not (paths.config_dir / "yelen_deployment_edition.json").exists()
    assert license_file.read_text(encoding="utf-8") == "LOCAL-YLIC"

    with pytest.raises(UpdateError, match="Retour arrière refusé"):
        apply_program_update(
            paths,
            _make_update_zip("1.2.0"),
            current_version="1.3.2",
            actor_username="super_root",
            source_label="Older ZIP",
        )


def test_update_routes_are_exclusive_to_super_admin(app, configured_school):
    with app.app_context():
        establishment_admin = User(
            username="update_establishment_admin",
            full_name="Administrateur Établissement",
            role=ROLE_ADMIN_ETAB,
            is_active=True,
        )
        establishment_admin.set_password("EtabPass2026!")
        db.session.add(establishment_admin)
        db.session.flush()
        ensure_user_role_synced(establishment_admin)
        db.session.commit()

    admin = app.test_client()
    login_page = admin.get("/connexion")
    csrf = extract_csrf_token(login_page.data)
    assert admin.post(
        "/connexion",
        data={
            "csrf_token": csrf,
            "username": "update_establishment_admin",
            "password": "EtabPass2026!",
        },
        follow_redirects=True,
    ).status_code == 200
    assert admin.get("/administration/mises-a-jour").status_code == 403

    with app.app_context():
        super_admin = User(
            username="update_super",
            full_name="Super Administrateur",
            role=ROLE_SUPER_ADMIN,
            is_active=True,
        )
        super_admin.set_password("SuperPass2026!")
        db.session.add(super_admin)
        db.session.flush()
        ensure_user_role_synced(super_admin)
        db.session.commit()

    client = app.test_client()
    login_page = client.get("/connexion")
    csrf = extract_csrf_token(login_page.data)
    assert client.post(
        "/connexion",
        data={"csrf_token": csrf, "username": "update_super", "password": "SuperPass2026!"},
        follow_redirects=True,
    ).status_code == 200
    response = client.get("/administration/mises-a-jour")
    assert response.status_code == 200
    assert "Mise à jour de Yelen Notes" in response.data.decode("utf-8")
    assert "Mises à jour en ligne" in response.data.decode("utf-8")
