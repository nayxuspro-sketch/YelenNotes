"""Test fonctionnel de bout en bout (HTTP + Jinja2) couvrant l'ensemble des modules de Yelen Notes."""

from __future__ import annotations

from tests.conftest import extract_csrf_token


def test_complete_school_workflow_over_http(app, client):
    """Parcourt de bout en bout une base vierge via les écrans HTTP jusqu'aux PDF et sauvegardes."""
    # 1. Amorçage initial sur base 100 % vierge
    r = client.get("/initialisation")
    assert r.status_code == 200
    tok = extract_csrf_token(r.data)

    r = client.post(
        "/initialisation",
        data={
            "csrf_token": tok,
            "username": "admin_ecole",
            "full_name": "M. KABORÉ Adama",
            "password": "AdminPassword2026!",
            "confirm_password": "AdminPassword2026!",
            "school_name": "École Primaire Publique de Pissy",
            "ceb": "CEB de Ouagadougou IV",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Guide de mise en route" in r.data.decode("utf-8")

    # 2. Création de l'année scolaire
    tok = extract_csrf_token(client.get("/annees-scolaires").data)
    r = client.post(
        "/annees-scolaires",
        data={
            "csrf_token": tok,
            "label": "2026-2027",
            "age_reference_date": "2026-10-01",
            "start_date": "2026-10-01",
            "end_date": "2027-06-30",
            "default_director_title": "Le Directeur",
            "default_director_name": "M. KABORÉ Adama",
            "make_active": "1",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    # 3. Création d'une période ouverte
    tok = extract_csrf_token(client.get("/periodes").data)
    r = client.post(
        "/periodes",
        data={
            "csrf_token": tok,
            "year_id": "1",
            "name": "Premier Trimestre",
            "short_code": "T1",
            "display_order": "1",
            "annual_weight": "1",
            "status": "OUVERTE",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    # 4. Configuration du barème général (/10) et de la grille d'appréciations
    tok = extract_csrf_token(client.get("/regles-et-appreciations").data)
    r = client.post(
        "/regles-et-appreciations",
        data={
            "csrf_token": tok,
            "year_id": "1",
            "class_id": "",
            "general_average_scale": "10",
            "decimal_places": "2",
            "rounding_mode": "ROUND_HALF_UP",
            "absence_policy": "ZERO",
            "tie_breaking_mode": "COMPETITION",
            "annual_average_mode": "SIMPLE",
            "annual_require_all_periods": "1",
            "scale_min[]": ["0", "5", "7", "8.5"],
            "scale_max[]": ["5", "7", "8.5", "10"],
            "scale_label[]": ["Insuffisant", "Passable", "Bien", "Très bien"],
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    # 5. Création d'une classe et de deux matières
    tok = extract_csrf_token(client.get("/classes").data)
    r = client.post(
        "/classes",
        data={
            "csrf_token": tok,
            "year_id": "1",
            "name": "CM2",
            "level_label": "CM2",
            "titulaire_name": "Mme OUÉDRAOGO Salimata",
            "director_title": "Le Directeur",
            "director_name": "M. KABORÉ Adama",
            "display_order": "1",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    for subj_name, short, max_s, coef in [("Lecture", "Lect", "10", "2"), ("Calcul", "Calc", "20", "2")]:
        tok = extract_csrf_token(client.get("/classes/1/matieres").data)
        r = client.post(
            "/classes/1/matieres",
            data={
                "csrf_token": tok,
                "name": subj_name,
                "short_label": short,
                "min_score": "0",
                "max_score": max_s,
                "reference_scale": "10",
                "coefficient": coef,
                "include_in_average": "1",
                "allow_exemption": "1",
            },
            follow_redirects=True,
        )
        assert r.status_code == 200

    # 6. Inscription de deux élèves
    for ln, fn, sx, bd in [("SAWADOGO", "Aïcha", "F", "2015-04-12"), ("ZONGO", "Issa", "M", "2015-06-20")]:
        tok = extract_csrf_token(client.get("/eleves?class_id=1").data)
        r = client.post(
            "/eleves",
            data={
                "csrf_token": tok,
                "class_id": "1",
                "last_name": ln,
                "first_names": fn,
                "sex": sx,
                "birth_date": bd,
                "schooling_status": "Public",
            },
            follow_redirects=True,
        )
        assert r.status_code == 200

    # 7. Enregistrement groupé des notes et observations
    notes_html = client.get("/notes?class_id=1&period_id=1")
    assert notes_html.status_code == 200
    tok = extract_csrf_token(notes_html.data)
    r = client.post(
        "/notes/enregistrer-grille",
        data={
            "csrf_token": tok,
            "class_id": "1",
            "period_id": "1",
            "cell_1_1": "6",
            "cell_1_2": "12",
            "cell_2_1": "8.5",
            "cell_2_2": "17",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    obs_html = client.get("/notes/observations?class_id=1&period_id=1&scope=PERIODE")
    assert obs_html.status_code == 200
    obs_text = obs_html.data.decode("utf-8")
    assert "Moyenne période /10" in obs_text
    assert "8,50" in obs_text
    assert "6,00" in obs_text
    tok = extract_csrf_token(obs_html.data)
    assert (
        client.post(
            "/notes/observations",
            data={
                "csrf_token": tok,
                "class_id": "1",
                "period_id": "1",
                "scope": "PERIODE",
                "t_obs_1": "Excellent travail",
                "d_obs_1": "Félicitations",
            },
            follow_redirects=True,
        ).status_code
        == 200
    )

    # 8. Consultation de tous les écrans de résultats, exports PDF/Excel, sauvegardes et journaux
    for url in [
        "/",
        "/notes/historique?class_id=1&period_id=1",
        "/resultats/releve?class_id=1&period_id=1",
        "/resultats/tableau-synoptique?class_id=1&period_id=1",
        "/resultats/tableau-synoptique?class_id=1&period_id=1&subject_id=1",
        "/resultats/classement?class_id=1&period_id=1&scope=PERIODE",
        "/resultats/classement?class_id=1&scope=ANNUEL",
        "/resultats/bulletins?class_id=1&period_id=1&scope=PERIODE",
        "/resultats/bulletins?class_id=1&scope=ANNUEL",
        "/administration/utilisateurs",
        "/administration/sauvegardes",
        "/administration/journaux",
        "/etat-sante",
    ]:
        resp = client.get(url)
        assert resp.status_code == 200, f"Échec sur {url}"

    for pdf_url in [
        "/resultats/releve/pdf?class_id=1&period_id=1",
        "/resultats/tableau-synoptique/pdf?class_id=1&period_id=1&orientation=landscape",
        "/resultats/tableau-synoptique/pdf?class_id=1&period_id=1&orientation=portrait",
        "/resultats/classement/pdf?class_id=1&period_id=1&scope=PERIODE",
        "/resultats/classement/pdf?class_id=1&scope=ANNUEL",
        "/resultats/bulletins/pdf?class_id=1&period_id=1&scope=PERIODE",
        "/resultats/bulletins/pdf?class_id=1&scope=ANNUEL",
    ]:
        pdf_resp = client.get(pdf_url)
        assert pdf_resp.status_code == 200
        assert pdf_resp.data.startswith(b"%PDF-")

    # Vérification explicite de l'ordre des colonnes (N° en 1re position) et de la renumérotation 1..N
    import io
    from openpyxl import load_workbook

    rk_xlsx = client.get("/resultats/classement/excel?class_id=1&period_id=1&scope=PERIODE")
    assert rk_xlsx.status_code == 200
    wb = load_workbook(io.BytesIO(rk_xlsx.data))
    ws = wb.active
    assert ws.cell(row=3, column=1).value == "N°"
    assert ws.cell(row=3, column=2).value == "Rang"
    # ZONGO (roll_number=2 dans la classe) est 1er au mérite -> son N° dans le classement doit être 1
    assert ws.cell(row=4, column=1).value == 1
    assert ws.cell(row=4, column=2).value == "1er"
    assert ws.cell(row=4, column=3).value == "ZONGO"
    assert ws.cell(row=5, column=1).value == 2
    assert ws.cell(row=5, column=3).value == "SAWADOGO"

    # 9. Création d'une sauvegarde manuelle depuis l'interface
    bkp_page = client.get("/administration/sauvegardes")
    tok = extract_csrf_token(bkp_page.data)
    bkp_post = client.post(
        "/administration/sauvegardes/creer",
        data={"csrf_token": tok, "notes": "Recette fin de T1"},
        follow_redirects=True,
    )
    assert bkp_post.status_code == 200
    assert "Sauvegarde créée et vérifiée avec succès" in bkp_post.data.decode("utf-8")
