"""Sauvegarde à chaud SQLite, contrôle d'intégrité et restauration sécurisée (§14 CdC & §8 Arch)."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from yelen_notes.extensions import db
from yelen_notes.models import BackupRecord, EstablishmentSettings, utcnow_naive
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import record_audit_log
from yelen_notes.version import APP_NAME, SCHEMA_VERSION


class BackupError(RuntimeError):
    """Erreur lors d'une opération de sauvegarde ou de restauration."""


@dataclass
class BackupVerification:
    is_valid: bool
    message: str
    manifest: dict


def _compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_sqlite_integrity_check(sqlite_file: Path) -> tuple[bool, str]:
    """Exécute `PRAGMA integrity_check;` et `PRAGMA foreign_key_check;` sur un fichier SQLite."""
    if not sqlite_file.is_file():
        return False, "Fichier de base de données introuvable."
    conn = sqlite3.connect(f"file:{sqlite_file.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        cur = conn.cursor()
        rows = cur.execute("PRAGMA integrity_check;").fetchall()
        status_msg = "; ".join(str(r[0]) for r in rows)
        if len(rows) != 1 or str(rows[0][0]).lower() != "ok":
            return False, f"Échec integrity_check : {status_msg}"
        fk_issues = cur.execute("PRAGMA foreign_key_check;").fetchall()
        if fk_issues:
            return False, f"Violations de clés étrangères détectées ({len(fk_issues)})."
        return True, "ok"
    except sqlite3.Error as exc:
        return False, f"Fichier SQLite illisible ou corrompu : {exc}"
    finally:
        conn.close()


def resolve_backup_directory(paths: RuntimePaths, custom_dir: str | None = None) -> Path:
    """Détermine le répertoire effectif des sauvegardes (configurable vers clé USB / disque externe)."""
    if custom_dir and custom_dir.strip():
        target = Path(custom_dir.strip()).expanduser()
    else:
        settings = db.session.get(EstablishmentSettings, 1)
        if settings and settings.backup_directory and settings.backup_directory.strip():
            target = Path(settings.backup_directory.strip()).expanduser()
        else:
            target = paths.backups_dir
    target.mkdir(parents=True, exist_ok=True)
    return target


def create_safe_backup(
    paths: RuntimePaths,
    *,
    app_version: str = "1.0.0",
    trigger_type: str = "MANUAL",
    created_by: str = "système",
    notes: str | None = None,
    destination_dir: Path | str | None = None,
    record_in_db: bool = True,
) -> Path:
    """Crée une sauvegarde cohérente à chaud via `sqlite3.Connection.backup()` (§14 CdC)."""
    if not paths.db_path.is_file():
        raise BackupError("Aucune base de données SQLite active à sauvegarder.")

    target_dir = resolve_backup_directory(paths, str(destination_dir) if destination_dir else None)
    now = utcnow_naive()
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    archive_name = f"yelen_notes_backup_{trigger_type.lower()}_{stamp}.zip"
    archive_path = target_dir / archive_name

    with tempfile.TemporaryDirectory(prefix="yelen_bkp_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        snapshot_db = tmp_dir / "ecole.db"

        # Copie transactionnellement sûre avec l'API SQLite native
        src_conn = sqlite3.connect(str(paths.db_path), timeout=15)
        dst_conn = sqlite3.connect(str(snapshot_db), timeout=15)
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        ok, check_msg = run_sqlite_integrity_check(snapshot_db)
        if not ok:
            raise BackupError(f"La sauvegarde générée a échoué au contrôle d'intégrité : {check_msg}")

        db_sha256 = _compute_sha256(snapshot_db)
        manifest = {
            "application": APP_NAME,
            "app_version": app_version,
            "schema_version": SCHEMA_VERSION,
            "trigger_type": trigger_type,
            "created_at_utc": now.isoformat(),
            "created_by": created_by,
            "db_sha256": db_sha256,
            "notes": notes or "",
        }
        manifest_path = tmp_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot_db, arcname="database/ecole.db")
            zf.write(manifest_path, arcname="manifest.json")
            if paths.logos_dir.is_dir():
                for logo_file in paths.logos_dir.iterdir():
                    if logo_file.is_file():
                        zf.write(logo_file, arcname=f"logos/{logo_file.name}")

    archive_sha256 = _compute_sha256(archive_path)
    size_bytes = archive_path.stat().st_size

    if record_in_db:
        rec = BackupRecord(
            filename=archive_name,
            file_path=str(archive_path),
            size_bytes=size_bytes,
            sha256_hash=archive_sha256,
            trigger_type=trigger_type,
            integrity_verified=True,
            app_version=app_version,
            schema_version=SCHEMA_VERSION,
            created_by_username=created_by,
            notes=notes,
        )
        db.session.add(rec)
        db.session.commit()
        _prune_old_auto_backups(target_dir)

    return archive_path


def _prune_old_auto_backups(target_dir: Path) -> None:
    """Conserve les N générations récentes de sauvegardes automatiques (§14 CdC)."""
    settings = db.session.get(EstablishmentSettings, 1)
    keep = max(1, settings.backup_retention_count if settings else 15)
    auto_records = (
        BackupRecord.query.filter_by(trigger_type="AUTO")
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        .all()
    )
    for stale in auto_records[keep:]:
        stale_path = Path(stale.file_path)
        if stale_path.is_file() and stale_path.parent.resolve() == target_dir.resolve():
            stale_path.unlink(missing_ok=True)
        db.session.delete(stale)
    if len(auto_records) > keep:
        db.session.commit()


def verify_backup_archive(archive_path: Path) -> BackupVerification:
    """Vérifie l'archive ZIP, l'empreinte SHA-256 et l'intégrité SQLite avant restauration."""
    if not archive_path.is_file():
        return BackupVerification(False, "Fichier de sauvegarde introuvable.", {})

    try:
        with tempfile.TemporaryDirectory(prefix="yelen_verify_") as tmp_str:
            tmp_dir = Path(tmp_str)
            with zipfile.ZipFile(archive_path, "r") as zf:
                names = set(zf.namelist())
                if "database/ecole.db" not in names or "manifest.json" not in names:
                    return BackupVerification(
                        False,
                        "Archive non reconnue : 'database/ecole.db' ou 'manifest.json' manquant.",
                        {},
                    )
                zf.extract("database/ecole.db", path=tmp_dir)
                zf.extract("manifest.json", path=tmp_dir)

            manifest = json.loads((tmp_dir / "manifest.json").read_text(encoding="utf-8"))
            extracted_db = tmp_dir / "database" / "ecole.db"
            expected_hash = manifest.get("db_sha256")
            if expected_hash:
                actual_hash = _compute_sha256(extracted_db)
                if actual_hash != expected_hash:
                    return BackupVerification(
                        False,
                        "Empreinte SHA-256 invalide : le fichier de sauvegarde a été altéré.",
                        manifest,
                    )

            ok, msg = run_sqlite_integrity_check(extracted_db)
            if not ok:
                return BackupVerification(False, msg, manifest)
            return BackupVerification(True, "Intégrité vérifiée avec succès.", manifest)
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError) as exc:
        return BackupVerification(False, f"Archive de sauvegarde invalide : {exc}", {})


def restore_from_backup_archive(
    archive_path: Path,
    paths: RuntimePaths,
    *,
    app_version: str = "1.0.0",
    restored_by: str = "admin",
) -> BackupVerification:
    """Restaure une sauvegarde après vérification avant/après et sauvegarde pré-restauration (§14 CdC)."""
    pre_check = verify_backup_archive(archive_path)
    if not pre_check.is_valid:
        raise BackupError(f"Restauration annulée — {pre_check.message}")

    # 1. Sauvegarde automatique de sécurité de l'état actuel avant écrasement
    if paths.db_path.is_file():
        create_safe_backup(
            paths,
            app_version=app_version,
            trigger_type="PRE_RESTORE",
            created_by=restored_by,
            notes=f"Sauvegarde de sécurité avant restauration de {archive_path.name}",
            record_in_db=False,
        )

    # 2. Libérer les connexions SQLAlchemy actives
    db.session.remove()
    db.engine.dispose()

    with tempfile.TemporaryDirectory(prefix="yelen_restore_") as tmp_str:
        tmp_dir = Path(tmp_str)
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(tmp_dir)

        extracted_db = tmp_dir / "database" / "ecole.db"

        # 3. Restauration transactionnelle via l'API SQLite native vers la base active
        src_conn = sqlite3.connect(str(extracted_db), timeout=15)
        dst_conn = sqlite3.connect(str(paths.db_path), timeout=15)
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        # 4. Restaurer les logos éventuels
        extracted_logos = tmp_dir / "logos"
        if extracted_logos.is_dir():
            paths.logos_dir.mkdir(parents=True, exist_ok=True)
            for item in extracted_logos.iterdir():
                if item.is_file():
                    shutil.copy2(item, paths.logos_dir / item.name)

    # 5. Contrôle d'intégrité APRÈS restauration (§14 CdC)
    post_ok, post_msg = run_sqlite_integrity_check(paths.db_path)
    if not post_ok:
        raise BackupError(f"Alerte critique : échec du contrôle post-restauration ({post_msg}).")

    record_audit_log(
        "BACKUP_RESTORE",
        f"Restauration complète effectuée depuis l'archive {archive_path.name}",
        details={"archive": archive_path.name, "manifest": pre_check.manifest},
        commit=True,
    )
    return pre_check


def maybe_trigger_automatic_backup(paths: RuntimePaths, app_version: str = "1.0.0") -> Path | None:
    """Déclenche une sauvegarde automatique périodique si l'échéance configurée est atteinte."""
    if not paths.db_path.is_file():
        return None
    settings = db.session.get(EstablishmentSettings, 1)
    if settings is None or not settings.auto_backup_enabled:
        return None

    now = utcnow_naive()
    interval = timedelta(hours=max(1, settings.auto_backup_interval_hours or 24))
    if settings.last_auto_backup_at and (now - settings.last_auto_backup_at) < interval:
        return None

    settings.last_auto_backup_at = now
    db.session.commit()
    return create_safe_backup(
        paths,
        app_version=app_version,
        trigger_type="AUTO",
        created_by="planificateur",
        notes="Sauvegarde automatique périodique",
        record_in_db=True,
    )
