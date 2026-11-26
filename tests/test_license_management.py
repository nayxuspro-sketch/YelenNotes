"""Tests complets du module de gestion de licences d'utilisation :
- Accès strictement exclusif au Super Admin (403 pour tous les autres rôles),
- Licence Monoposte vs Licence Multiposte Réseau,
- Liaison cryptographique RSA-2048 à l'empreinte matérielle unique de l'ordinateur (anti-copie),
- Version de déploiement Super Admin et export du Kit Client École (.zip) sans clé privée.
"""

from __future__ import annotations

from datetime import date, timedelta
import io
import json
import shutil
import zipfile

from tests.conftest import extract_csrf_token
from yelen_notes import create_app
from yelen_notes.extensions import db
from yelen_notes.models import (
    LICENSE_MONOPOSTE,
    LICENSE_MULTIPOSTE,
    ROLE_ADMIN_ETAB,
    ROLE_COMPTABLE,
    ROLE_CONSULTATION,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    ROLE_PARENT,
    ROLE_SECRETAIRE,
    ROLE_SUPER_ADMIN,
    ROLE_SURVEILLANT,
    STATUS_OUVERTE,
    IssuedLicense,
    SchoolYear,
    User,
)
from yelen_notes.paths import build_runtime_paths
from yelen_notes.security import ensure_user_role_synced
from yelen_notes.services.license_service import (
    CLIENT_LICENSE_FILENAME,
    DEPLOYMENT_KEY_FILENAME,
    PUBLISHER_PUB_FILENAME,
    convert_current_installation_to_client,
    evaluate_runtime_license,
    generate_signed_license,
    get_machine_identity,
    install_license_on_machine,
    is_deployment_edition,
)


def _login(client, username: str, password: str):
    page = client.get("/connexion")
    csrf = extract_csrf_token(page.data)
    return client.post(
        "/connexion",
        data={"csrf_token": csrf, "username": username, "password": password},
        follow_redirects=True,
    )


def test_license_module_strictly_exclusive_to_super_admin(app, client):
    """Seul le Super Admin voit le menu et accède aux routes `/administration/licences*`."""
    non_super_roles = [
        ("admin_etab", ROLE_ADMIN_ETAB),
        ("directeur_lic", ROLE_DIRECTEUR),
        ("enseignant_lic", ROLE_ENSEIGNANT),
        ("secretaire_lic", ROLE_SECRETAIRE),
        ("comptable_lic", ROLE_COMPTABLE),
        ("surveillant_lic", ROLE_SURVEILLANT),
        ("lecteur_lic", ROLE_CONSULTATION),
        ("parent_lic", ROLE_PARENT),
    ]

    with app.app_context():
        yr = SchoolYear(
            label="2026-2027",
            start_date=date(2026, 10, 1),
            end_date=date(2027, 6, 30),
            age_reference_date=date(2026, 10, 1),
            status=STATUS_OUVERTE,
            default_director_name="M. OUEDRAOGO",
            default_director_title="Le Directeur",
        )
        db.session.add(yr)
        super_u = User(
            username="super_root",
            full_name="Super Admin Déploiement",
            role=ROLE_SUPER_ADMIN,
            is_active=True,
        )
        super_u.set_password("SuperPass2026!")
        db.session.add(super_u)
        db.session.flush()
        ensure_user_role_synced(super_u)

        for uname, rcode in non_super_roles:
            u = User(username=uname, full_name=f"Compte {rcode}", role=rcode, is_active=True)
            u.set_password("RolePass2026!")
            db.session.add(u)
            db.session.flush()
            ensure_user_role_synced(u)
        db.session.commit()

    # Invité non connecté -> redirection vers /connexion
    r_guest = client.get("/administration/licences")
    assert r_guest.status_code == 302
    assert "/connexion" in r_guest.headers["Location"]

    # Tous les autres rôles (y compris Administrateur d'établissement et Directeur) -> HTTP 403 strict
    for uname, _rcode in non_super_roles:
        c = app.test_client()
        home = _login(c, uname, "RolePass2026!")
        assert home.status_code == 200
        assert "/administration/licences" not in home.data.decode("utf-8")

        assert c.get("/administration/licences").status_code == 403
        assert c.get("/administration/licences/kit-client").status_code == 403

        csrf = extract_csrf_token(home.data)
        r_post = c.post(
            "/administration/licences/generer",
            data={
                "csrf_token": csrf,
                "license_type": LICENSE_MONOPOSTE,
                "beneficiary_name": "Tentative non autorisée",
                "machine_fingerprint": "YNM-1111-2222-3333-4444-5555",
            },
        )
        assert r_post.status_code == 403

    # Le Super Admin voit le lien de menu et émet les 2 types de licences
    sa_client = app.test_client()
    sa_home = _login(sa_client, "super_root", "SuperPass2026!")
    assert sa_home.status_code == 200
    assert "/administration/licences" in sa_home.data.decode("utf-8")

    page = sa_client.get("/administration/licences")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "Version de déploiement Super Admin" in html
    csrf = extract_csrf_token(page.data)

    # Émission d'une licence Monoposte
    r_mono = sa_client.post(
        "/administration/licences/generer",
        data={
            "csrf_token": csrf,
            "license_type": LICENSE_MONOPOSTE,
            "beneficiary_name": "École Primaire Koulouba",
            "machine_fingerprint": "YNM-AAAA-BBBB-CCCC-DDDD-1111",
            "notes": "Poste Secrétariat",
        },
        follow_redirects=True,
    )
    assert r_mono.status_code == 200
    assert "Licence Monoposte" in r_mono.data.decode("utf-8")
    assert "École Primaire Koulouba" in r_mono.data.decode("utf-8")

    # Émission d'une licence Multiposte Réseau
    csrf2 = extract_csrf_token(r_mono.data)
    r_multi = sa_client.post(
        "/administration/licences/generer",
        data={
            "csrf_token": csrf2,
            "license_type": LICENSE_MULTIPOSTE,
            "beneficiary_name": "Lycée Scientifique National",
            "machine_fingerprint": "YNM-AAAA-BBBB-CCCC-DDDD-2222",
            "expires_at": (date.today() + timedelta(days=365)).isoformat(),
        },
        follow_redirects=True,
    )
    assert r_multi.status_code == 200
    assert "Multiposte Réseau" in r_multi.data.decode("utf-8")

    with app.app_context():
        all_lics = IssuedLicense.query.order_by(IssuedLicense.id.asc()).all()
        assert len(all_lics) == 2
        assert all_lics[0].license_type == LICENSE_MONOPOSTE
        assert all_lics[1].license_type == LICENSE_MULTIPOSTE
        lic_id = all_lics[0].id

    dl = sa_client.get(f"/administration/licences/{lic_id}/telecharger")
    assert dl.status_code == 200
    ylic_doc = json.loads(dl.data.decode("utf-8"))
    assert ylic_doc["format"] == "Yelen Notes Official License File (.ylic)"
    assert ylic_doc["license_type"] == LICENSE_MONOPOSTE
    assert ylic_doc["machine_fingerprint"] == "YNM-AAAA-BBBB-CCCC-DDDD-1111"
    assert ylic_doc["token"].startswith("YNL1.")


def test_client_edition_hardware_binding_and_anti_copy_protection(tmp_path):
    """Vérifie le blocage sans licence, l'activation liée à l'empreinte matérielle,
    et le blocage immédiat si le dossier est copié vers un autre ordinateur."""
    pc_a_fp = "YNM-1010-2020-3030-4040-5050"
    pc_b_fp = "YNM-9999-8888-7777-6666-5555"

    # Vérifie que l'empreinte matérielle réelle de l'ordinateur hôte est bien générée au format attendu
    real_ident = get_machine_identity()
    assert real_ident.fingerprint.startswith("YNM-")
    assert len(real_ident.fingerprint) == 28

    # Dossier d'installation du Poste Client A (sans clé privée de déploiement)
    client_root = tmp_path / "pc_ecole_a"
    (client_root / "config").mkdir(parents=True)
    master_paths = build_runtime_paths(data_dir_override=tmp_path / "master_data")
    shutil.copy2(
        master_paths.config_dir / PUBLISHER_PUB_FILENAME,
        client_root / "config" / PUBLISHER_PUB_FILENAME,
    )

    paths_a = build_runtime_paths(data_dir_override=client_root / "data")
    paths_a = paths_a.__class__(
        **{
            **paths_a.__dict__,
            "config_dir": client_root / "config",
        }
    )
    assert is_deployment_edition(paths_a) is False

    app_a = create_app(
        runtime_paths=paths_a,
        server_mode="monoposte",
        config_overrides={
            "WTF_CSRF_ENABLED": True,
            "TEST_MACHINE_FINGERPRINT_OVERRIDE": pc_a_fp,
        },
        auto_init_db=True,
    )

    try:
        ca = app_a.test_client()

        # 1. Sans licence : toutes les routes applicatives redirigent vers /licence/activation
        r_locked = ca.get("/connexion")
        assert r_locked.status_code == 302
        assert "/licence/activation" in r_locked.headers["Location"]

        r_ajax = ca.get("/connexion", headers={"Accept": "application/json"})
        assert r_ajax.status_code == 403
        assert r_ajax.get_json()["license_status"] == "MISSING"

        # 2. Téléchargement de la fiche d'empreinte (.req) sur le Poste A
        r_req = ca.get("/licence/demande-activation.req")
        assert r_req.status_code == 200
        req_json = json.loads(r_req.data.decode("utf-8"))
        assert req_json["machine_fingerprint"] == pc_a_fp

        # 3. Le Super Admin génère une licence pour le Poste B -> refusée sur le Poste A !
        _ser_b, token_for_b, _ = generate_signed_license(
            master_paths,
            license_type=LICENSE_MONOPOSTE,
            beneficiary_name="Autre École",
            machine_fingerprint=pc_b_fp,
        )
        act_page = ca.get("/licence/activation")
        csrf = extract_csrf_token(act_page.data)
        r_wrong_pc = ca.post(
            "/licence/activer",
            data={"csrf_token": csrf, "license_key": token_for_b},
            follow_redirects=True,
        )
        assert r_wrong_pc.status_code == 200
        assert "Activation refusée" in r_wrong_pc.data.decode("utf-8")
        assert ca.get("/connexion").status_code == 302  # Toujours verrouillé

        # 4. Le Super Admin génère la licence officielle liée à l'empreinte du Poste A -> activation réussie !
        _ser_a, token_for_a, _ = generate_signed_license(
            master_paths,
            license_type=LICENSE_MONOPOSTE,
            beneficiary_name="Complexe Scolaire Yelen",
            machine_fingerprint=pc_a_fp,
        )
        csrf2 = extract_csrf_token(ca.get("/licence/activation").data)
        r_ok = ca.post(
            "/licence/activer",
            data={"csrf_token": csrf2, "license_key": token_for_a},
            follow_redirects=False,
        )
        assert r_ok.status_code == 302
        assert "/licence/activation" not in r_ok.headers["Location"]

        # Le Poste A accède désormais normalement à l'application (initialisation / connexion)
        r_unlocked = ca.get("/initialisation")
        assert r_unlocked.status_code == 200

        # 5. COPIE DU DOSSIER COMPLET (programme + config/yelen_license.ylic + data/) VERS UN AUTRE PC (Poste B) !
        copied_root = tmp_path / "dossier_copie_sur_pc_b"
        shutil.copytree(client_root, copied_root)
        assert (copied_root / "config" / CLIENT_LICENSE_FILENAME).is_file()

        paths_copied = build_runtime_paths(data_dir_override=copied_root / "data")
        paths_copied = paths_copied.__class__(
            **{
                **paths_copied.__dict__,
                "config_dir": copied_root / "config",
            }
        )
        eval_copied = evaluate_runtime_license(
            paths_copied,
            server_mode="monoposte",
            fingerprint_override=pc_b_fp,
        )
        assert eval_copied.allowed is False
        assert eval_copied.status_code == "MACHINE_MISMATCH"
        assert "Cette copie du dossier applicatif ne peut pas fonctionner" in eval_copied.reason

        app_copied = create_app(
            runtime_paths=paths_copied,
            server_mode="monoposte",
            config_overrides={
                "WTF_CSRF_ENABLED": True,
                "TEST_MACHINE_FINGERPRINT_OVERRIDE": pc_b_fp,
            },
            auto_init_db=True,
        )
        try:
            cb = app_copied.test_client()
            r_blocked_copy = cb.get("/connexion", follow_redirects=True)
            assert r_blocked_copy.status_code == 200
            body_b = r_blocked_copy.data.decode("utf-8")
            assert "Copie sur un autre ordinateur détectée" in body_b
            assert pc_b_fp in body_b
        finally:
            with app_copied.app_context():
                db.session.remove()
                db.engine.dispose()

        # 6. Falsification d'un seul caractère de la signature RSA-2048 -> blocage immédiat
        lic_file = client_root / "config" / CLIENT_LICENSE_FILENAME
        tampered = token_for_a[:-2] + ("AA" if not token_for_a.endswith("AA") else "BB")
        lic_file.write_text(tampered, encoding="utf-8")
        eval_tampered = evaluate_runtime_license(
            paths_a,
            server_mode="monoposte",
            fingerprint_override=pc_a_fp,
        )
        assert eval_tampered.allowed is False
        assert eval_tampered.status_code == "SIGNATURE_INVALID"
    finally:
        with app_a.app_context():
            db.session.remove()
            db.engine.dispose()


def test_monoposte_vs_multiposte_reseau_enforcement_and_expiration_revocation(tmp_path):
    """Vérifie la distinction stricte entre Licence Monoposte et Licence Multiposte Réseau,
    ainsi que le blocage à l'expiration ou à la révocation."""
    server_fp = "YNM-ABCD-EF23-4567-89AB-CDEF"
    master_paths = build_runtime_paths(data_dir_override=tmp_path / "master")

    client_cfg = tmp_path / "school_srv" / "config"
    client_cfg.mkdir(parents=True)
    shutil.copy2(master_paths.config_dir / PUBLISHER_PUB_FILENAME, client_cfg / PUBLISHER_PUB_FILENAME)
    paths_srv = build_runtime_paths(data_dir_override=tmp_path / "school_srv" / "data")
    paths_srv = paths_srv.__class__(**{**paths_srv.__dict__, "config_dir": client_cfg})

    # A. Installation d'une licence MONOPOSTE sur le serveur
    _s1, mono_token, _ = generate_signed_license(
        master_paths,
        license_type=LICENSE_MONOPOSTE,
        beneficiary_name="Collège Monoposte",
        machine_fingerprint=server_fp,
    )
    install_license_on_machine(paths_srv, mono_token, fingerprint_override=server_fp)

    # Monoposte en local (127.0.0.1) -> Autorisé
    assert (
        evaluate_runtime_license(
            paths_srv, server_mode="monoposte", remote_addr="127.0.0.1", fingerprint_override=server_fp
        ).allowed
        is True
    )
    # Monoposte lancé en mode serveur multiposte -> Bloqué (MODE_FORBIDDEN)
    ev_mode = evaluate_runtime_license(
        paths_srv, server_mode="multiposte", remote_addr="127.0.0.1", fingerprint_override=server_fp
    )
    assert ev_mode.allowed is False
    assert ev_mode.status_code == "MODE_FORBIDDEN"

    # Monoposte sollicité depuis un autre poste du réseau local (192.168.1.45) -> Bloqué (NETWORK_FORBIDDEN)
    ev_lan = evaluate_runtime_license(
        paths_srv, server_mode="monoposte", remote_addr="192.168.1.45", fingerprint_override=server_fp
    )
    assert ev_lan.allowed is False
    assert ev_lan.status_code == "NETWORK_FORBIDDEN"

    # B. Remplacement par une licence MULTIPOSTE RÉSEAU liée à ce même serveur
    serial_multi, multi_token, _ = generate_signed_license(
        master_paths,
        license_type=LICENSE_MULTIPOSTE,
        beneficiary_name="Lycée Multiposte Réseau",
        machine_fingerprint=server_fp,
    )
    install_license_on_machine(paths_srv, multi_token, fingerprint_override=server_fp)

    # Multiposte Réseau accepte à la fois le serveur local ET les postes clients LAN (192.168.1.45)
    assert (
        evaluate_runtime_license(
            paths_srv, server_mode="multiposte", remote_addr="127.0.0.1", fingerprint_override=server_fp
        ).allowed
        is True
    )
    assert (
        evaluate_runtime_license(
            paths_srv, server_mode="multiposte", remote_addr="192.168.1.45", fingerprint_override=server_fp
        ).allowed
        is True
    )
    # Mais bloque toujours si le serveur est copié sur une autre machine !
    ev_other_srv = evaluate_runtime_license(
        paths_srv,
        server_mode="multiposte",
        remote_addr="192.168.1.45",
        fingerprint_override="YNM-2222-3333-4444-5555-6666",
    )
    assert ev_other_srv.allowed is False
    assert ev_other_srv.status_code == "MACHINE_MISMATCH"

    # C. Licence expirée -> Bloquée (EXPIRED)
    _se, exp_token, exp_payload = generate_signed_license(
        master_paths,
        license_type=LICENSE_MULTIPOSTE,
        beneficiary_name="École Expirée",
        machine_fingerprint=server_fp,
        expires_at=date.today() - timedelta(days=1),
    )
    (client_cfg / CLIENT_LICENSE_FILENAME).write_text(exp_token, encoding="utf-8")
    ev_exp = evaluate_runtime_license(paths_srv, server_mode="multiposte", fingerprint_override=server_fp)
    assert ev_exp.allowed is False
    assert ev_exp.status_code == "EXPIRED"

    # D. Révocation dans la base locale -> Bloquée (REVOKED)
    install_license_on_machine(paths_srv, multi_token, fingerprint_override=server_fp)
    app_srv = create_app(
        runtime_paths=paths_srv,
        server_mode="multiposte",
        config_overrides={"TEST_MACHINE_FINGERPRINT_OVERRIDE": server_fp},
        auto_init_db=True,
    )
    try:
        with app_srv.app_context():
            rec = IssuedLicense(
                license_serial=serial_multi,
                license_type=LICENSE_MULTIPOSTE,
                beneficiary_name="Lycée Multiposte Réseau",
                machine_fingerprint=server_fp,
                license_token=multi_token,
                is_revoked=True,
            )
            db.session.add(rec)
            db.session.commit()

            ev_rev = evaluate_runtime_license(
                paths_srv, server_mode="multiposte", fingerprint_override=server_fp
            )
            assert ev_rev.allowed is False
            assert ev_rev.status_code == "REVOKED"
    finally:
        with app_srv.app_context():
            db.session.remove()
            db.engine.dispose()


def test_super_admin_deployment_edition_and_client_school_kit_export(app, tmp_path):
    """Vérifie l'export du Kit Client École (.zip) depuis la Version de déploiement Super Admin
    et la conversion sécurisée d'un poste en Édition Client."""
    with app.app_context():
        sa = User(
            username="deployer_sa",
            full_name="Super Admin Déploiement",
            role=ROLE_SUPER_ADMIN,
            is_active=True,
        )
        sa.set_password("DeployPass2026!")
        db.session.add(sa)
        db.session.flush()
        ensure_user_role_synced(sa)
        db.session.commit()

    c = app.test_client()
    _login(c, "deployer_sa", "DeployPass2026!")

    # Export HTTP du Kit Client École (.zip)
    r_zip = c.get("/administration/licences/kit-client")
    assert r_zip.status_code == 200
    assert r_zip.mimetype == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(r_zip.data))
    names = zf.namelist()
    # La clé publique de vérification et le guide d'activation sont inclus
    assert any(n.endswith(f"config/{PUBLISHER_PUB_FILENAME}") for n in names)
    assert any(n.endswith("LISEZ_MOI_ACTIVATION_LICENCE.txt") for n in names)
    assert any(n.endswith("run.py") for n in names)

    # La clé privée de déploiement Super Admin et les scripts éditeur sont strictement absents !
    assert not any(DEPLOYMENT_KEY_FILENAME in n for n in names)
    assert not any("3_Generer_Kit_Client_Ecole.bat" in n for n in names)
    assert not any(n.endswith(".ylic") for n in names)
    assert not any(n.endswith(".db") for n in names)

    # Extraction du Kit Client dans un dossier vierge : il démarre bien en Édition Client verrouillée
    extract_dir = tmp_path / "extracted_kit"
    zf.extractall(extract_dir)
    sub_dirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    assert len(sub_dirs) == 1
    kit_root = sub_dirs[0]

    kit_paths = build_runtime_paths(data_dir_override=tmp_path / "kit_data")
    kit_paths = kit_paths.__class__(
        **{
            **kit_paths.__dict__,
            "program_root": kit_root,
            "config_dir": kit_root / "config",
        }
    )
    assert is_deployment_edition(kit_paths) is False
    ev_kit = evaluate_runtime_license(kit_paths, server_mode="monoposte")
    assert ev_kit.allowed is False
    assert ev_kit.status_code == "MISSING"

    # Test de la conversion d'une installation de déploiement vers un Poste Client verrouillé
    sim_deploy_dir = tmp_path / "direct_deploy"
    (sim_deploy_dir / "config").mkdir(parents=True)
    orig_paths = app.config["RUNTIME_PATHS"]
    shutil.copy2(orig_paths.config_dir / PUBLISHER_PUB_FILENAME, sim_deploy_dir / "config" / PUBLISHER_PUB_FILENAME)
    shutil.copy2(orig_paths.config_dir / DEPLOYMENT_KEY_FILENAME, sim_deploy_dir / "config" / DEPLOYMENT_KEY_FILENAME)

    direct_paths = build_runtime_paths(data_dir_override=sim_deploy_dir / "data")
    direct_paths = direct_paths.__class__(**{**direct_paths.__dict__, "config_dir": sim_deploy_dir / "config"})
    assert is_deployment_edition(direct_paths) is True

    target_fp = "YNM-7777-6666-5555-4444-3333"
    _s, tok, _ = generate_signed_license(
        direct_paths,
        license_type=LICENSE_MONOPOSTE,
        beneficiary_name="École Déployée Sur Place",
        machine_fingerprint=target_fp,
    )
    install_license_on_machine(direct_paths, tok, fingerprint_override=target_fp)
    convert_current_installation_to_client(
        direct_paths, server_mode="monoposte", fingerprint_override=target_fp
    )
    assert is_deployment_edition(direct_paths) is False
    assert not (sim_deploy_dir / "config" / DEPLOYMENT_KEY_FILENAME).exists()
    assert (
        evaluate_runtime_license(
            direct_paths, server_mode="monoposte", fingerprint_override=target_fp
        ).allowed
        is True
    )
