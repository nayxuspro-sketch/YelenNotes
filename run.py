#!/usr/bin/env python3
"""Point d'entrée principal de Yelen Notes (Waitress HTTP silencieux en arrière-plan + utilitaires).

Modes d'utilisation (Architecture technique §3 et §6) :
- Monoposte silencieux : pythonw run.py                 -> http://127.0.0.1:5000
- Multiposte réseau    : pythonw run.py --mode multiposte -> http://0.0.0.0:5000
- Base de démo séparée : pythonw run.py --demo
- Arrêt propre serveur : pythonw run.py --stop
- Récupération admin   : python run.py --reset-admin admin
- Contrôle intégrité   : python run.py --check-db
"""

from __future__ import annotations

import os
import sys

# En exécution sans console Windows (pythonw.exe ou exécutable PyInstaller console=False),
# sys.stdout et sys.stderr valent None : on les redirige immédiatement vers un flux sûr
# afin d'éviter toute erreur d'écriture dans les bibliothèques tierces (Alembic, Waitress, etc.).
_STARTED_WINDOWLESS = sys.stdout is None or sys.stderr is None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import argparse
import getpass
import json
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.request
import webbrowser

from waitress import create_server

from yelen_notes import create_app
from yelen_notes.blueprints.admin import detect_lan_ip_addresses
from yelen_notes.extensions import db
from yelen_notes.paths import RuntimePaths, build_runtime_paths, load_ini_config
from yelen_notes.security import reset_or_create_admin_account
from yelen_notes.services.backup_service import run_sqlite_integrity_check
from yelen_notes.services.demo_service import populate_demo_database
from yelen_notes.services.license_service import (
    build_client_school_kit_zip,
    evaluate_runtime_license,
    get_machine_identity,
    is_deployment_edition,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="YelenNotes",
        description="Yelen Notes — Application locale de gestion et de calcul des notes scolaires",
    )
    parser.add_argument(
        "--mode",
        choices=("monoposte", "multiposte"),
        default=None,
        help="Mode d'écoute réseau : monoposte (127.0.0.1) ou multiposte (réseau local).",
    )
    parser.add_argument("--host", default=None, help="Adresse IP d'écoute explicite.")
    parser.add_argument("--port", type=int, default=None, help="Port TCP HTTP (défaut : 5000).")
    parser.add_argument("--threads", type=int, default=None, help="Nombre de threads Waitress.")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Répertoire indépendant des données scolaires (remplace la configuration par défaut).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Démarrer sur la base de démonstration séparée (ecole_demo.db) clairement identifiée.",
    )
    parser.add_argument(
        "--init-demo",
        action="store_true",
        help="Générer/vérifier uniquement la base de démonstration séparée (ecole_demo.db) puis quitter.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Ne pas ouvrir automatiquement le navigateur au démarrage.",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Conserver l'affichage de la console (diagnostic développeur uniquement).",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Arrête proprement l'instance Yelen Notes en cours d'exécution en arrière-plan.",
    )
    parser.add_argument(
        "--reset-admin",
        metavar="IDENTIFIANT",
        default=None,
        help="Procédure locale de création ou récupération du mot de passe d'un administrateur.",
    )
    parser.add_argument(
        "--admin-password",
        default=None,
        help="Nouveau mot de passe administrateur (utilisé avec --reset-admin en mode non interactif).",
    )
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Exécute un contrôle complet d'intégrité SQLite (PRAGMA integrity_check) puis quitte.",
    )
    parser.add_argument(
        "--show-fingerprint",
        action="store_true",
        help="Affiche l'empreinte matérielle unique de cet ordinateur puis quitte.",
    )
    parser.add_argument(
        "--export-client-kit",
        metavar="FICHIER_ZIP",
        default=None,
        help="Exporte une archive ZIP Version Client École (sans clé privée de déploiement Super Admin).",
    )
    return parser.parse_args(argv)


def _redirect_windowless_streams_to_logfile(paths: RuntimePaths) -> None:
    """Redirige stdout/stderr vers `logs/server_background.log` lorsqu'aucune console n'est affichée."""
    try:
        log_path = paths.logs_dir / "server_background.log"
        log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = log_handle
        sys.stderr = log_handle
    except OSError:
        pass


def _detach_windows_console_if_present(paths: RuntimePaths) -> None:
    """Masque et détache toute fenêtre console résiduelle sous Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass
    _redirect_windowless_streams_to_logfile(paths)


def _probe_existing_yelen_server(port: int) -> dict | None:
    """Vérifie si une instance Yelen Notes répond déjà sur `127.0.0.1:port`."""
    url = f"http://127.0.0.1:{port}/etat-sante"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("ok") is True:
                    return data
    except Exception:
        return None
    return None


def _pid_file_for_port(paths: RuntimePaths, port: int) -> Path:
    return paths.data_dir / f"yelen_server_{port}.pid"


def _terminate_windows_server(pid: int) -> None:
    """Arrête uniquement le processus serveur, sans tuer l'assistant de redémarrage qui l'a lancé."""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        check=False,
    )


def _stop_running_instance(paths: RuntimePaths, port: int) -> bool:
    """Arrête proprement le processus serveur Yelen Notes enregistré pour `port`."""
    pid_file = _pid_file_for_port(paths, port)
    stopped = False
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if pid > 0 and pid != os.getpid():
                if os.name == "nt":
                    # Ne pas utiliser taskkill /T : l'assistant qui orchestre le redémarrage
                    # est un enfant temporaire de ce serveur et serait tué avec lui.
                    _terminate_windows_server(pid)
                else:
                    os.kill(pid, signal.SIGTERM)
                stopped = True
        except Exception:
            pass
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    if stopped:
        for _ in range(15):
            if _probe_existing_yelen_server(port) is None:
                break
            time.sleep(0.2)
    return stopped


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ini_cfg = load_ini_config()

    mode = (
        args.mode
        or os.environ.get("YELEN_MODE", "").strip().lower()
        or ini_cfg.get("server", "mode", fallback="monoposte").strip().lower()
    )
    if mode not in ("monoposte", "multiposte"):
        mode = "monoposte"

    is_demo = bool(args.demo or args.init_demo or os.environ.get("YELEN_DEMO") == "1")
    paths = build_runtime_paths(
        data_dir_override=args.data_dir,
        is_demo_mode=is_demo,
    )

    if _STARTED_WINDOWLESS:
        _redirect_windowless_streams_to_logfile(paths)

    port = args.port or int(ini_cfg.get("server", "port", fallback="5000"))

    # Arrêt propre demandé (--stop)
    if args.stop:
        _stop_running_instance(paths, port)
        return 0

    is_cli_utility = bool(
        args.check_db
        or args.show_fingerprint
        or args.export_client_kit
        or args.init_demo
        or args.reset_admin
    )

    # Sous Windows, masquer immédiatement toute console lors d'un démarrage applicatif normal
    if os.name == "nt" and not is_cli_utility and not args.console:
        _detach_windows_console_if_present(paths)

    # Si Yelen Notes tourne déjà en arrière-plan sur ce port :
    # - même mode & même base -> ouvrir simplement le navigateur sans relancer un second serveur
    # - mode différent (ex. passage Monoposte -> Multiposte ou Démo) -> arrêter l'ancien puis relancer
    if not is_cli_utility:
        existing = _probe_existing_yelen_server(port)
        local_url = f"http://127.0.0.1:{port}"
        open_browser_cfg = ini_cfg.getboolean("server", "open_browser", fallback=True)
        if existing is not None:
            same_mode = str(existing.get("mode", "")).lower() == mode
            same_demo = bool(existing.get("demo", False)) == is_demo
            if same_mode and same_demo:
                if os.name == "nt" and open_browser_cfg and not args.no_browser:
                    webbrowser.open(local_url)
                return 0
            _stop_running_instance(paths, port)

    app = create_app(runtime_paths=paths, server_mode=mode)

    if args.show_fingerprint:
        ident = get_machine_identity()
        print(f"Poste               : {ident.hostname} ({ident.os_label})")
        print(f"Empreinte matérielle: {ident.fingerprint}")
        return 0

    if args.export_client_kit:
        if not is_deployment_edition(paths):
            print(
                "Erreur : seule la Version de Déploiement du Super Admin peut générer un Kit Client École.",
                file=sys.stderr,
            )
            return 1
        out_path = Path(args.export_client_kit).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(build_client_school_kit_zip(paths, str(app.config["APP_VERSION"])))
        print(f"Kit Client École généré avec succès : {out_path}")
        return 0

    # Commande de contrôle d'intégrité après arrêt imprévu (§19 test n°12 CdC)
    if args.check_db:
        ok, msg = run_sqlite_integrity_check(paths.db_path)
        print(f"Base SQLite : {paths.db_path}")
        print(f"Résultat PRAGMA integrity_check : {msg}")
        return 0 if ok else 1

    # Commande d'initialisation de la base de démonstration séparée
    if is_demo:
        with app.app_context():
            info = populate_demo_database(paths)
            if args.init_demo:
                print(f"Base de démonstration prête ({info['status']}) : {info['db_path']}")
                return 0

    # Procédure locale de récupération du compte administrateur (Architecture §7)
    if args.reset_admin:
        new_pw = args.admin_password
        if not new_pw:
            pw1 = getpass.getpass(f"Nouveau mot de passe pour '{args.reset_admin}' (min. 8 caractères) : ")
            pw2 = getpass.getpass("Confirmez le nouveau mot de passe : ")
            if pw1 != pw2:
                print("Erreur : les mots de passe saisis ne correspondent pas.", file=sys.stderr)
                return 1
            new_pw = pw1
        with app.app_context():
            try:
                user = reset_or_create_admin_account(args.reset_admin, new_pw)
                print(f"Succès : le compte administrateur '{user.username}' est actif et prêt à se connecter.")
                return 0
            except ValueError as exc:
                print(f"Erreur : {exc}", file=sys.stderr)
                return 1

    threads = args.threads or int(ini_cfg.get("server", "threads", fallback="6"))
    if args.host:
        host = args.host
    else:
        host = "0.0.0.0" if mode == "multiposte" else "127.0.0.1"

    lic_startup = evaluate_runtime_license(paths, server_mode=mode)
    local_url = f"http://127.0.0.1:{port}"
    print("=" * 72)
    print(f"  YELEN NOTES v{app.config['APP_VERSION']} — Démarrage du serveur local")
    print("=" * 72)
    print(f"  Mode actif          : {mode.upper()}" + (" [BASE DE DÉMONSTRATION FICTIVE]" if is_demo else ""))
    print(f"  Empreinte ce poste  : {lic_startup.current_machine_fingerprint}")
    print(f"  Statut licence      : {lic_startup.status_label}")
    print(f"  Répertoire données  : {paths.data_dir}")
    print(f"  Base SQLite locale  : {paths.db_path}")
    print(f"  Accès sur ce poste  : {local_url}")
    if mode == "multiposte" or host == "0.0.0.0":
        lan_ips = detect_lan_ip_addresses()
        if lan_ips:
            for ip in lan_ips:
                print(f"  Accès réseau local  : http://{ip}:{port}")
        else:
            print(f"  Accès réseau local  : http://<IP-DU-SERVEUR>:{port}")
    if paths.is_unc_path:
        print("  [ATTENTION] Chemin réseau UNC détecté ! Placez les données sur un disque local.")
    print("=" * 72, flush=True)

    pid_file = _pid_file_for_port(paths, port)
    try:
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    open_browser_cfg = ini_cfg.getboolean("server", "open_browser", fallback=True)
    if os.name == "nt" and open_browser_cfg and not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(local_url)).start()

    server = create_server(app, host=host, port=port, threads=max(2, threads))

    shutting_down = False

    def _graceful_shutdown(_signum, _frame):
        nonlocal shutting_down
        shutting_down = True
        print("\nArrêt propre du serveur Yelen Notes en cours...", flush=True)
        server.close()

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except OSError:
        if not shutting_down:
            raise
    finally:
        try:
            if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        with app.app_context():
            db.session.remove()
        print("Serveur arrêté proprement. Ressources libérées.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
