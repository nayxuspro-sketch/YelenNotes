"""Vérifications de la nouvelle identité commerciale sans modifier les identifiants historiques."""


def test_ovicloud_branding_and_logo_are_served(client, app, configured_school):
    health = client.get("/etat-sante")
    assert health.status_code == 200
    assert health.get_json()["app"] == "OviCloud Notes"

    login = client.get("/connexion")
    assert login.status_code == 200
    assert b"OviCloud Notes" in login.data
    assert "Connexion — OviCloud Notes".encode("utf-8") in login.data
    assert b"Cultiver le vivant, connecter" in login.data
    assert b"images/ovicloud-logo.png" in login.data
    assert b"images/ovicloud-mark.png" in login.data

    logo = client.get("/static/images/ovicloud-logo.png")
    mark = client.get("/static/images/ovicloud-mark.png")
    icon = client.get("/static/images/ovicloud.ico")
    assert logo.status_code == 200 and logo.mimetype == "image/png"
    assert mark.status_code == 200 and mark.mimetype == "image/png"
    assert icon.status_code == 200 and icon.mimetype == "image/vnd.microsoft.icon"
    assert logo.data.startswith(b"\x89PNG\r\n\x1a\n")

    # L'identifiant de licence signé reste stable pour ne pas invalider les fichiers .ylic existants.
    from yelen_notes.services.license_service import build_ylic_file_content

    assert b"Yelen Notes Official License File (.ylic)" in build_ylic_file_content(
        "YNL1.test", {"serial": "TEST", "license_type": "MONOPOSTE"}
    )
