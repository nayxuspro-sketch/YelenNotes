"""Module de mise à jour en ligne et par archive locale sécurisée de OviCloud Notes.

Garanties fondamentales :
1. Sauvegarde SQLite à chaud vérifiée obligatoire (`PRE_UPDATE`) avant toute modification de fichiers.
2. Préservation absolue des bases scolaires (`data_dir`), des paramètres `.ini` et de la licence `.ylic`.
3. Étanchéité stricte des éditions : un Poste Client École mis à jour en ligne depuis GitHub
   ne reçoit JAMAIS la clé privée `yelen_deployment_edition.json` du Super Admin, tandis qu'une
   Version de déploiement Super Admin conserve automatiquement son autorité de signature.
4. Support du mode connecté (GitHub Releases / Tags) ET du mode hors ligne (import d'un `.zip`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any, Callable
import urllib.error
import urllib.request
import zipfile

from yelen_notes.paths import RuntimePaths, load_ini_config
from yelen_notes.services.backup_service import create_safe_backup
from yelen_notes.services.license_service import (
    CLIENT_LICENSE_FILENAME,
    DEPLOYMENT_KEY_FILENAME,
    PUBLISHER_PUB_FILENAME,
    is_deployment_edition,
)

DEFAULT_GITHUB_REPO = "nayxuspro-sketch/YelenNotes"
MAX_UPDATE_ARCHIVE_BYTES = 64 * 1024 * 1024  # 64 Mo max
UPDATE_HISTORY_FILENAME = "update_history.json"


class UpdateError(ValueError):
    """Erreur métier ou de sécurité lors de la vérification ou de l'installation d'une mise à jour."""


@dataclass(frozen=True)
class UpdateCheckResult:
    reachable: bool
    update_available: bool
    current_version: str
    latest_version: str | None
    download_tag: str | None
    release_title: str
    release_notes: str
    published_at: str | None
    repository: str
    checked_at: str
    error_message: str | None = None


@dataclass(frozen=True)
class AppliedUpdateInfo:
    previous_version: str
    new_version: str
    backup_filename: str
    files_updated: int
    edition_preserved: str
    source_label: str
    applied_at: str


def parse_semver(version_str: str) -> tuple[int, ...]:
    """Convertit `'v1.3.1'` ou `'1.3.1'` en tuple numérique comparable `(1, 3, 1)`."""
    cleaned = re.sub(r"^[vV]\s*", "", (version_str or "").strip())
    core = cleaned.split("-", 1)[0].split("+", 1)[0]
    nums: list[int] = []
    for part in core.split("."):
        m = re.match(r"^(\d+)", part.strip())
        if m:
            nums.append(int(m.group(1)))
    return tuple(nums) if nums else (0, 0, 0)


def normalize_version_string(version_str: str) -> str:
    cleaned = re.sub(r"^[vV]\s*", "", (version_str or "").strip())
    return cleaned or "0.0.0"


def is_newer_version(candidate_version: str, current_version: str) -> bool:
    cand = parse_semver(candidate_version)
    curr = parse_semver(current_version)
    max_len = max(len(cand), len(curr), 3)
    cand_pad = cand + (0,) * (max_len - len(cand))
    curr_pad = curr + (0,) * (max_len - len(curr))
    return cand_pad > curr_pad


def get_configured_update_repo(paths: RuntimePaths, override_repo: str | None = None) -> str:
    if override_repo and override_repo.strip():
        repo = override_repo.strip()
    else:
        cfg = load_ini_config(paths.program_root)
        repo = cfg.get("updates", "github_repo", fallback=DEFAULT_GITHUB_REPO).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return DEFAULT_GITHUB_REPO
    return repo


def _default_http_get(url: str, *, accept: str = "*/*", timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "YelenNotes-OnlineUpdater/1.3",
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_UPDATE_ARCHIVE_BYTES + 1)
        if len(data) > MAX_UPDATE_ARCHIVE_BYTES:
            raise UpdateError("Archive de mise à jour trop volumineuse (limite de sécurité dépassée).")
        return data


def check_online_update(
    paths: RuntimePaths,
    current_version: str,
    *,
    repo_override: str | None = None,
    fetcher: Callable[..., bytes] | None = None,
) -> UpdateCheckResult:
    """Interroge le serveur officiel de versions GitHub sans jamais bloquer si le PC est hors ligne."""
    http_get = fetcher or _default_http_get
    repo = get_configured_update_repo(paths, repo_override)
    now_str = datetime.utcnow().strftime("%d/%m/%Y à %H:%M UTC")
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"

    try:
        raw = http_get(api_url, accept="application/vnd.github+json", timeout=8.0)
        data = json.loads(raw.decode("utf-8"))
        tag_name = str(data.get("tag_name") or "").strip()
        if not tag_name:
            raise UpdateError("Réponse du serveur de mise à jour incomplète (tag manquant).")

        latest_ver = normalize_version_string(tag_name)
        pub_raw = str(data.get("published_at") or "").strip()
        pub_fmt = pub_raw[:10] if pub_raw else None

        return UpdateCheckResult(
            reachable=True,
            update_available=is_newer_version(latest_ver, current_version),
            current_version=normalize_version_string(current_version),
            latest_version=latest_ver,
            download_tag=tag_name,
            release_title=str(data.get("name") or f"OviCloud Notes {tag_name}").strip(),
            release_notes=str(data.get("body") or "Aucune note de version détaillée.").strip(),
            published_at=pub_fmt,
            repository=repo,
            checked_at=now_str,
            error_message=None,
        )
    except urllib.error.HTTPError as exc:
        return UpdateCheckResult(
            reachable=False,
            update_available=False,
            current_version=normalize_version_string(current_version),
            latest_version=None,
            download_tag=None,
            release_title="",
            release_notes="",
            published_at=None,
            repository=repo,
            checked_at=now_str,
            error_message=f"Serveur de mise à jour momentanément indisponible (HTTP {exc.code}).",
        )
    except Exception:
        return UpdateCheckResult(
            reachable=False,
            update_available=False,
            current_version=normalize_version_string(current_version),
            latest_version=None,
            download_tag=None,
            release_title="",
            release_notes="",
            published_at=None,
            repository=repo,
            checked_at=now_str,
            error_message=(
                "Impossible de joindre le serveur de mise à jour en ligne. "
                "Vérifiez votre connexion Internet ou importez une archive .zip manuellement ci-dessous."
            ),
        )


def download_online_release_zip(
    paths: RuntimePaths,
    tag_name: str,
    *,
    repo_override: str | None = None,
    fetcher: Callable[..., bytes] | None = None,
) -> bytes:
    """Télécharge l'archive officielle correspondant au tag demandé."""
    clean_tag = (tag_name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", clean_tag):
        raise UpdateError("Identifiant de version distant invalide.")

    http_get = fetcher or _default_http_get
    repo = get_configured_update_repo(paths, repo_override)
    candidate_urls = [
        f"https://codeload.github.com/{repo}/zip/refs/tags/{clean_tag}",
        f"https://github.com/{repo}/archive/refs/tags/{clean_tag}.zip",
    ]
    last_exc: Exception | None = None
    for url in candidate_urls:
        try:
            data = http_get(url, accept="application/zip", timeout=45.0)
            if len(data) < 512:
                raise UpdateError("Archive téléchargée vide ou incomplète.")
            return data
        except Exception as exc:
            last_exc = exc
            continue
    raise UpdateError(f"Échec du téléchargement de la version {clean_tag} : {last_exc}")


def inspect_and_validate_update_zip(zip_bytes: bytes) -> tuple[str, str]:
    """Vérifie la structure, les contrôles CRC et le contenu requis d'une archive ZIP de mise à jour.

    Retourne `(new_version, archive_root_prefix)`.
    """
    if not zip_bytes:
        raise UpdateError("Aucun fichier de mise à jour reçu.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as exc:
        raise UpdateError("Le fichier fourni n'est pas une archive ZIP valide.") from exc

    with zf:
        bad_member = zf.testzip()
        if bad_member is not None:
            raise UpdateError(f"Archive ZIP corrompue (membre altéré : {bad_member}).")

        names = zf.namelist()
        if len(names) > 12000:
            raise UpdateError("Archive rejetée : elle contient un nombre excessif de fichiers.")
        if sum(item.file_size for item in zf.infolist()) > 256 * 1024 * 1024:
            raise UpdateError("Archive rejetée : sa taille décompressée dépasse la limite de sécurité de 256 Mo.")
        normalized_names: set[str] = set()
        for member in names:
            norm = member.replace("\\", "/")
            parts = norm.rstrip("/").split("/")
            if (
                norm.startswith("/")
                or "\x00" in norm
                or ":" in norm
                or any(part in (".", "..") or part.rstrip(" .") in (".", "..") for part in parts)
            ):
                raise UpdateError("Archive rejetée par sécurité : chemin relatif illégal détecté.")
            normalized = norm.rstrip("/")
            if normalized in normalized_names:
                raise UpdateError("Archive rejetée : membre ZIP dupliqué détecté.")
            normalized_names.add(normalized)

        # Détecte la racine interne (ex. 'YelenNotes-1.4.0/' ou archive plate '')
        version_entry = None
        prefix = ""
        for member in names:
            norm = member.replace("\\", "/")
            if norm == "VERSION.txt":
                version_entry = member
                prefix = ""
                break
            parts = norm.split("/")
            if len(parts) == 2 and parts[1] == "VERSION.txt":
                version_entry = member
                prefix = parts[0] + "/"
                break

        if version_entry is None:
            raise UpdateError("Archive invalide : fichier VERSION.txt introuvable dans le paquet.")

        required_rel = ("run.py", "yelen_notes/__init__.py", "resources/templates/base.html")
        normalized_set = {m.replace("\\", "/") for m in names}
        for req in required_rel:
            if f"{prefix}{req}" not in normalized_set:
                raise UpdateError(
                    f"Cette archive n'est pas un paquet officiel complet OviCloud Notes ({req} manquant)."
                )

        new_ver = normalize_version_string(zf.read(version_entry).decode("utf-8", errors="replace"))
        if parse_semver(new_ver) == (0, 0, 0):
            raise UpdateError("Numéro de version illisible dans VERSION.txt.")
        return new_ver, prefix


def load_update_history(paths: RuntimePaths) -> list[dict[str, Any]]:
    hist_file = paths.data_dir / UPDATE_HISTORY_FILENAME
    if not hist_file.is_file():
        return []
    try:
        items = json.loads(hist_file.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _append_update_history(paths: RuntimePaths, entry: dict[str, Any]) -> None:
    items = load_update_history(paths)
    items.insert(0, entry)
    hist_file = paths.data_dir / UPDATE_HISTORY_FILENAME
    try:
        hist_file.write_text(json.dumps(items[:50], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def apply_program_update(
    paths: RuntimePaths,
    zip_bytes: bytes,
    *,
    current_version: str,
    actor_username: str,
    source_label: str,
    allow_same_or_older: bool = False,
    force_client_edition: bool = False,
) -> AppliedUpdateInfo:
    """Valide l'archive, sauvegarde la base SQLite à chaud, et applique la mise à jour atomiquement
    en préservant strictement le type d'édition (Déploiement Super Admin vs Client École) et la licence."""
    new_ver, prefix = inspect_and_validate_update_zip(zip_bytes)
    curr_norm = normalize_version_string(current_version)

    newer = is_newer_version(new_ver, curr_norm)
    same_version = not newer and not is_newer_version(curr_norm, new_ver)
    if not newer and not (allow_same_or_older and same_version):
        if same_version:
            raise UpdateError(
                f"La version v{new_ver} est déjà installée. Cochez « Réinstaller la même version » si nécessaire."
            )
        raise UpdateError(
            f"Retour arrière refusé : la version v{new_ver} est antérieure à la version installée v{curr_norm}. "
            "Les retours arrière nécessitent une procédure de restauration dédiée."
        )

    # 1. État d'édition AVANT mise à jour (barrière étanche Client École vs Super Admin)
    was_deployment = is_deployment_edition(paths, force_client=force_client_edition)

    # 2. Sauvegarde SQLite à chaud vérifiée obligatoire AVANT modification de fichiers
    if not paths.db_path.is_file():
        raise UpdateError("Mise à jour annulée : aucune base scolaire active n'a pu être sauvegardée.")
    bkp_path = create_safe_backup(
        paths,
        app_version=curr_norm,
        trigger_type="PRE_UPDATE",
        created_by=actor_username,
        notes=f"Sauvegarde automatique avant mise à jour v{curr_norm} -> v{new_ver}",
    )
    backup_filename = bkp_path.name

    allowed_dirs = {"yelen_notes", "resources", "migrations", "packaging"}
    allowed_root_files = {
        "run.py",
        "VERSION.txt",
        "README.txt",
        "README.md",
        "requirements.txt",
        "requirements-build.txt",
        "1_Lancer_Yelen_Notes.vbs",
        "Lancer_Multiposte_Reseau.vbs",
        "Lancer_Demonstration.vbs",
        "Arreter_Yelen_Notes.vbs",
        "1_Installer_et_Lancer_Directement.bat",
        "2_Compiler_Dossier_Portable_EXE.bat",
    }
    if was_deployment:
        allowed_root_files.add("3_Generer_Kit_Client_Ecole.bat")

    # Fichiers locaux ne devant JAMAIS être écrasés par une mise à jour
    never_overwrite_config_files = {
        CLIENT_LICENSE_FILENAME,
        DEPLOYMENT_KEY_FILENAME,
        PUBLISHER_PUB_FILENAME,
        "yelen_notes.ini",
        "secret_key.txt",
    }

    updated_count = 0
    target_root = paths.program_root

    with tempfile.TemporaryDirectory(prefix="yelen_upd_stage_") as stage_str:
        stage_dir = Path(stage_str)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                norm = member.filename.replace("\\", "/")
                if prefix and not norm.startswith(prefix):
                    continue
                rel = norm[len(prefix) :] if prefix else norm
                if not rel:
                    continue

                parts = rel.split("/")
                top = parts[0]

                should_extract = False
                if len(parts) == 1 and top in allowed_root_files:
                    should_extract = True
                elif len(parts) >= 2 and top in allowed_dirs:
                    if "__pycache__" not in parts and not rel.endswith((".pyc", ".pyo", ".db", ".ylic")):
                        should_extract = True
                elif len(parts) == 2 and top == "config":
                    cfg_name = parts[1]
                    # Le modèle est générique; la configuration et la clé publique locales ne sont jamais écrasées.
                    if cfg_name == "yelen_notes.example.ini":
                        should_extract = True

                if not should_extract:
                    continue

                dest_stage = stage_dir / rel
                dest_stage.parent.mkdir(parents=True, exist_ok=True)
                dest_stage.write_bytes(zf.read(member))

        # Sauvegarde mémoire de sécurité des fichiers locaux critiques avant copie
        preserved_deployment_key: bytes | None = None
        dep_path = paths.config_dir / DEPLOYMENT_KEY_FILENAME
        if was_deployment and dep_path.is_file():
            preserved_deployment_key = dep_path.read_bytes()

        preserved_client_lic: bytes | None = None
        lic_path = paths.config_dir / CLIENT_LICENSE_FILENAME
        if lic_path.is_file():
            preserved_client_lic = lic_path.read_bytes()

        # Copie avec rollback en cas d'erreur d'E/S : une archive incomplètement appliquée
        # ne laisse pas le programme dans un état partiellement mis à jour.
        rollback_dir = stage_dir / "__rollback__"
        previous_files: dict[Path, Path | None] = {}
        try:
            for staged_file in sorted(stage_dir.rglob("*")):
                if not staged_file.is_file() or rollback_dir in staged_file.parents:
                    continue
                rel_path = staged_file.relative_to(stage_dir)
                final_dest = target_root / rel_path
                # Double verrou de sécurité : ne jamais écrire une clé privée sur un poste Client
                if final_dest.name in never_overwrite_config_files:
                    continue
                final_dest.parent.mkdir(parents=True, exist_ok=True)
                if final_dest not in previous_files:
                    if final_dest.is_file():
                        backup_copy = rollback_dir / rel_path
                        backup_copy.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(final_dest, backup_copy)
                        previous_files[final_dest] = backup_copy
                    else:
                        previous_files[final_dest] = None
                shutil.copy2(staged_file, final_dest)
                updated_count += 1
        except Exception:
            for final_dest, backup_copy in reversed(list(previous_files.items())):
                try:
                    if backup_copy is None:
                        final_dest.unlink(missing_ok=True)
                    else:
                        final_dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup_copy, final_dest)
                except OSError:
                    pass
            raise

        # Garantie post-copie sur l'étanchéité de l'édition et de la licence
        if was_deployment and preserved_deployment_key is not None:
            dep_path.parent.mkdir(parents=True, exist_ok=True)
            dep_path.write_bytes(preserved_deployment_key)
        elif not was_deployment and dep_path.exists():
            dep_path.unlink(missing_ok=True)

        if preserved_client_lic is not None and not lic_path.is_file():
            lic_path.parent.mkdir(parents=True, exist_ok=True)
            lic_path.write_bytes(preserved_client_lic)

    edition_label = (
        "Version de déploiement Super Admin" if was_deployment else "Poste Client École (sous licence)"
    )
    applied_at = datetime.utcnow().strftime("%d/%m/%Y à %H:%M UTC")
    info = AppliedUpdateInfo(
        previous_version=curr_norm,
        new_version=new_ver,
        backup_filename=backup_filename,
        files_updated=updated_count,
        edition_preserved=edition_label,
        source_label=source_label,
        applied_at=applied_at,
    )
    _append_update_history(
        paths,
        {
            "previous_version": info.previous_version,
            "new_version": info.new_version,
            "backup_filename": info.backup_filename,
            "files_updated": info.files_updated,
            "edition_preserved": info.edition_preserved,
            "source_label": info.source_label,
            "applied_by": actor_username,
            "applied_at": info.applied_at,
        },
    )
    return info


def schedule_graceful_server_restart(paths: RuntimePaths, server_mode: str, is_demo: bool, port: int) -> bool:
    """Programme un redémarrage silencieux en arrière-plan sous Windows/Linux hors tests unitaires."""
    if "pytest" in sys.modules:
        return False

    def _restart_worker() -> None:
        import time

        time.sleep(1.2)
        run_script = paths.program_root / "run.py"
        if not run_script.is_file():
            return
        py_exe = Path(sys.executable)
        if os.name == "nt" and py_exe.name.lower() == "python.exe":
            pyw = py_exe.with_name("pythonw.exe")
            if pyw.is_file():
                py_exe = pyw
        cmd = [str(py_exe), str(run_script)]

        common = ["--mode", server_mode, "--port", str(port), "--data-dir", str(paths.data_dir), "--no-browser"]
        cmd.extend(common)
        stop_cmd = cmd[:]
        stop_cmd.remove("--no-browser")
        stop_cmd.append("--stop")
        if is_demo:
            cmd.append("--demo")
            stop_cmd.append("--demo")

        helper_code = (
            "import subprocess, time; "
            f"subprocess.run({stop_cmd!r}, cwd={str(paths.program_root)!r}, check=False); "
            "time.sleep(0.7); "
            f"subprocess.Popen({cmd!r}, cwd={str(paths.program_root)!r}, "
            "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)"
            " if __import__('os').name == 'nt' else 0, "
            "start_new_session=__import__('os').name != 'nt')"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        subprocess.Popen(
            [str(py_exe), "-c", helper_code],
            cwd=str(paths.program_root),
            creationflags=flags,
            start_new_session=os.name != "nt",
        )

    threading.Thread(target=_restart_worker, daemon=True).start()
    return True
