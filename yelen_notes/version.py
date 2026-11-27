"""Informations de version de OviCloud Notes."""

from pathlib import Path

APP_NAME = "OviCloud Notes"
SCHEMA_VERSION = "1.1.0"


def get_app_version(program_root: Path | None = None) -> str:
    """Lit VERSION.txt à la racine du programme ou retourne la valeur par défaut."""
    candidates = []
    if program_root is not None:
        candidates.append(program_root / "VERSION.txt")
    candidates.append(Path(__file__).resolve().parent.parent / "VERSION.txt")
    for path in candidates:
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return content
    return "1.0.0"
