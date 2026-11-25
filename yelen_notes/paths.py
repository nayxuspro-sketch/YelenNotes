"""Résolution des chemins du programme portable et du répertoire indépendant des données.

Conforme à l'Architecture technique (§4 et §5) et au Cahier des charges (§16.3) :
- Séparation stricte entre le dossier programme (YELEN-NOTES/) et le dossier données.
- Support du mode PyInstaller ONEDIR (YelenNotes.exe + resources/ + config/).
- Détection des chemins réseau partagés UNC (interdits pour SQLite).
"""

from __future__ import annotations

import configparser
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    program_root: Path
    resources_dir: Path
    templates_dir: Path
    static_dir: Path
    fonts_dir: Path
    config_dir: Path
    data_dir: Path
    database_dir: Path
    db_path: Path
    backups_dir: Path
    documents_dir: Path
    bulletins_dir: Path
    releves_dir: Path
    logos_dir: Path
    logs_dir: Path
    is_unc_path: bool
    is_demo_mode: bool = False


def get_program_root() -> Path:
    """Détermine la racine du dossier programme (source ou PyInstaller ONEDIR)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_resources_dir(program_root: Path | None = None) -> Path:
    """Localise le dossier resources/ aussi bien en ONEDIR qu'en mode développement."""
    root = program_root or get_program_root()
    direct = root / "resources"
    if direct.is_dir():
        return direct
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "resources"
        if candidate.is_dir():
            return candidate
    internal = root / "_internal" / "resources"
    if internal.is_dir():
        return internal
    return direct


def load_ini_config(program_root: Path | None = None) -> configparser.ConfigParser:
    """Charge config/yelen_notes.ini s'il existe."""
    root = program_root or get_program_root()
    parser = configparser.ConfigParser()
    ini_path = root / "config" / "yelen_notes.ini"
    if ini_path.is_file():
        parser.read(ini_path, encoding="utf-8")
    return parser


def _is_writable_or_creatable(target: Path) -> bool:
    """Vérifie si le processus courant peut créer et écrire dans `target`."""
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def is_network_unc_path(path: Path | str) -> bool:
    """Détecte un chemin UNC Windows (\\\\serveur\\partage) déconseillé/interdit pour SQLite."""
    raw = str(path).strip()
    return raw.startswith("\\\\") or raw.startswith("//")


def resolve_default_data_dir(ini_cfg: configparser.ConfigParser | None = None) -> Path:
    """Détermine le répertoire de données indépendant du dossier programme."""
    env_dir = os.environ.get("YELEN_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    if ini_cfg is not None:
        cfg_dir = ini_cfg.get("storage", "data_dir", fallback="").strip()
        if cfg_dir:
            return Path(cfg_dir).expanduser().resolve()

    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        candidate = Path(program_data) / "YelenNotes"
        if _is_writable_or_creatable(candidate):
            return candidate.resolve()
        local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return (Path(local_app_data) / "YelenNotes").resolve()

    return (Path.home() / ".local" / "share" / "YelenNotes").resolve()


def build_runtime_paths(
    data_dir_override: Path | str | None = None,
    db_filename_override: str | None = None,
    is_demo_mode: bool = False,
) -> RuntimePaths:
    """Construit et prépare l'arborescence complète d'exécution."""
    program_root = get_program_root()
    resources_dir = get_resources_dir(program_root)
    ini_cfg = load_ini_config(program_root)

    if data_dir_override is not None:
        raw_data_dir_str = str(data_dir_override)
        unc_flag = is_network_unc_path(raw_data_dir_str)
        data_dir = Path(data_dir_override).expanduser()
        if not unc_flag:
            data_dir = data_dir.resolve()
    else:
        env_raw = os.environ.get("YELEN_DATA_DIR", "").strip()
        cfg_raw = ini_cfg.get("storage", "data_dir", fallback="").strip()
        unc_flag = is_network_unc_path(env_raw or cfg_raw)
        data_dir = resolve_default_data_dir(ini_cfg)

    if db_filename_override:
        db_filename = db_filename_override
    elif is_demo_mode:
        db_filename = "ecole_demo.db"
    else:
        db_filename = os.environ.get("YELEN_DB_FILENAME", "").strip() or ini_cfg.get(
            "storage", "db_filename", fallback="ecole.db"
        ).strip() or "ecole.db"

    database_dir = data_dir / "database"
    backups_dir = data_dir / "backups"
    documents_dir = data_dir / "documents"
    bulletins_dir = documents_dir / "bulletins"
    releves_dir = documents_dir / "releves"
    logos_dir = data_dir / "logos"
    logs_dir = data_dir / "logs"

    for folder in (
        database_dir,
        backups_dir,
        documents_dir,
        bulletins_dir,
        releves_dir,
        logos_dir,
        logs_dir,
    ):
        folder.mkdir(parents=True, exist_ok=True)

    return RuntimePaths(
        program_root=program_root,
        resources_dir=resources_dir,
        templates_dir=resources_dir / "templates",
        static_dir=resources_dir / "static",
        fonts_dir=resources_dir / "static" / "fonts",
        config_dir=program_root / "config",
        data_dir=data_dir,
        database_dir=database_dir,
        db_path=database_dir / db_filename,
        backups_dir=backups_dir,
        documents_dir=documents_dir,
        bulletins_dir=bulletins_dir,
        releves_dir=releves_dir,
        logos_dir=logos_dir,
        logs_dir=logs_dir,
        is_unc_path=unc_flag,
        is_demo_mode=is_demo_mode or (db_filename == "ecole_demo.db"),
    )


def get_or_create_secret_key(paths: RuntimePaths, ini_cfg: configparser.ConfigParser | None = None) -> str:
    """Récupère ou génère de manière persistante la clé secrète cryptographique du serveur."""
    env_key = os.environ.get("YELEN_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    if ini_cfg is not None:
        cfg_key = ini_cfg.get("security", "secret_key", fallback="").strip()
        if cfg_key:
            return cfg_key
    key_file = paths.data_dir / "secret_key.txt"
    if key_file.is_file():
        existing = key_file.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
    generated = secrets.token_hex(32)
    key_file.write_text(generated, encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return generated
