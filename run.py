#!/usr/bin/env python3
"""Point d'entrée principal de Yelen Notes (Waitress HTTP + utilitaires de maintenance).

Modes d'utilisation (Architecture technique §3 et §6) :
- Monoposte (défaut) : python run.py                 -> http://127.0.0.1:5000
- Multiposte réseau  : python run.py --mode multiposte -> http://0.0.0.0:5000
- Base de démo séparée : python run.py --demo
- Récupération admin : python run.py --reset-admin admin
- Contrôle intégrité : python run.py --check-db
"""

from __future__ import annotations

import argparse
import getpass
import os
import signal
import sys
import threading
import webbrowser

from waitress import create_server

from yelen_notes import create_app
from yelen_notes.blueprints.admin import detect_lan_ip_addresses
from yelen_notes.extensions import db
from yelen_notes.paths import build_runtime_paths, load_ini_config
from yelen_notes.security import reset_or_create_admin_account
from yelen_notes.services.backup_service import run_sqlite_integrity_check
from yelen_notes.services.demo_service import populate_demo_database


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
    return parser.parse_args(argv)


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

    app = create_app(runtime_paths=paths, server_mode=mode)

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

    port = args.port or int(ini_cfg.get("server", "port", fallback="5000"))
    threads = args.threads or int(ini_cfg.get("server", "threads", fallback="6"))
    if args.host:
        host = args.host
    else:
        host = "0.0.0.0" if mode == "multiposte" else "127.0.0.1"

    local_url = f"http://127.0.0.1:{port}"
    print("=" * 72)
    print(f"  YELEN NOTES v{app.config['APP_VERSION']} — Démarrage du serveur local")
    print("=" * 72)
    print(f"  Mode actif          : {mode.upper()}" + (" [BASE DE DÉMONSTRATION FICTIVE]" if is_demo else ""))
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
    print("  Appuyez sur Ctrl+C pour arrêter proprement le serveur.")
    print("=" * 72, flush=True)

    open_browser_cfg = ini_cfg.getboolean("server", "open_browser", fallback=True)
    if os.name == "nt" and open_browser_cfg and not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(local_url)).start()

    server = create_server(app, host=host, port=port, threads=max(2, threads))

    def _graceful_shutdown(_signum, _frame):
        print("\nArrêt propre du serveur Yelen Notes en cours...", flush=True)
        server.close()

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    try:
        server.run()
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        print("Serveur arrêté proprement. Ressources libérées.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
