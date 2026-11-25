"""Tests de sécurité RBAC côté serveur, CSRF, verrouillage compte, saisie concurrente (409) et clôture."""

from __future__ import annotations

from datetime import date
import pytest

from tests.conftest import extract_csrf_token
from yelen_notes.extensions import db
from yelen_notes.models import (
    STATUS_CLOTUREE,
    AcademicClass,
    EvaluationPeriod,
    GradeHistory,
    Subject,
    User,
)
from yelen_notes.security import reset_or_create_admin_account
from yelen_notes.services.grade_service import (
    GradeConflictError,
    GradeValidationError,
    upsert_single_grade,
)
from yelen_notes.services.student_service import create_and_enroll_student


def _login_client(client, username: str, password: str):
    page = client.get("/connexion", follow_redirects=True)
    token = extract_csrf_token(page.data)
    return client.post(
        "/connexion",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=True,
    )


def test_first_run_bootstrap_and_csrf_protection(app, client):
    """Vérifie l'amorçage initial sur base vierge et le rejet des POST sans jeton CSRF."""
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/initialisation" in resp.headers["Location"]

    # Tentative POST sans jeton CSRF -> 400
    bad_post = client.post(
        "/initialisation",
        data={
            "username": "admin1",
            "full_name": "Admin École",
            "password": "Password123!",
            "confirm_password": "Password123!",
        },
    )
    assert bad_post.status_code == 400

    # Amorçage légitime avec CSRF
    init_page = client.get("/initialisation")
    token = extract_csrf_token(init_page.data)
    ok_post = client.post(
        "/initialisation",
        data={
            "csrf_token": token,
            "username": "admin1",
            "full_name": "Admin École",
            "password": "Password123!",
            "confirm_password": "Password123!",
            "school_name": "EPP Koulouba",
        },
        follow_redirects=True,
    )
    assert ok_post.status_code == 200
    assert "EPP Koulouba" in ok_post.data.decode("utf-8")


def test_server_side_rbac_and_teacher_class_isolation(app, client, configured_school):
    """§2 & §17 CdC : Contrôle des autorisations côté serveur pour Enseignant et Consultation."""
    with app.app_context():
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        ce1 = db.session.get(AcademicClass, configured_school["ce1_id"])
        enr_cm2 = create_and_enroll_student(
            academic_class=cm2, last_name="OUÉDRAOGO", first_names="Ali", sex="M", birth_date=date(2015, 3, 3)
        )
        enr_cm2_id = enr_cm2.id
        ce1_id = ce1.id

    # 1. L'enseignant titulaire de CM2 n'a PAS accès à l'administration ni à la classe CE1
    _login_client(client, "titulaire_cm2", "Titulaire2026!")
    assert client.get("/administration/utilisateurs").status_code == 403
    assert client.get("/administration/sauvegardes").status_code == 403
    assert client.get(f"/classes/{ce1_id}/matieres").status_code == 403

    # Mais il peut saisir une note via l'API JSON pour sa classe CM2 pendant la période OUVERTE
    notes_page = client.get(f"/notes?class_id={configured_school['cm2_id']}&period_id={configured_school['t1_id']}")
    csrf_tok = extract_csrf_token(notes_page.data)
    api_ok = client.post(
        "/notes/api/cellule",
        json={
            "enrollment_id": enr_cm2_id,
            "period_id": configured_school["t1_id"],
            "subject_id": configured_school["s_lec_id"],
            "value": "8,5",
            "version": None,
        },
        headers={"X-CSRFToken": csrf_tok},
    )
    assert api_ok.status_code == 200
    assert api_ok.get_json()["ok"] is True

    # Déconnexion puis test du profil CONSULTATION
    client.post("/deconnexion", data={"csrf_token": csrf_tok})
    _login_client(client, "lecteur", "Lecteur2026!")
    releve_page = client.get(f"/resultats/releve?class_id={configured_school['cm2_id']}&period_id={configured_school['t1_id']}")
    assert releve_page.status_code == 200
    tok_lecteur = extract_csrf_token(releve_page.data)

    # Toute tentative d'écriture par le profil CONSULTATION est bloquée côté serveur (403)
    forbidden_write = client.post(
        "/notes/api/cellule",
        json={
            "enrollment_id": enr_cm2_id,
            "period_id": configured_school["t1_id"],
            "subject_id": configured_school["s_lec_id"],
            "value": "5",
        },
        headers={"X-CSRFToken": tok_lecteur},
    )
    assert forbidden_write.status_code == 403


def test_concurrent_grade_editing_conflict_detection_and_history(app, client, configured_school):
    """§19 test n°5 & §8.2 CdC : Saisie concurrente sans perte silencieuse (HTTP 409) + historique."""
    with app.app_context():
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        t1 = db.session.get(EvaluationPeriod, configured_school["t1_id"])
        s_lec = db.session.get(Subject, configured_school["s_lec_id"])
        admin = db.session.get(User, configured_school["admin_id"])
        titulaire = db.session.get(User, configured_school["titulaire_id"])

        enr = create_and_enroll_student(
            academic_class=cm2, last_name="DIALLO", first_names="Fatou", sex="F", birth_date=date(2015, 8, 8)
        )
        g1, _ = upsert_single_grade(
            enrollment=enr, period=t1, subject=s_lec, raw_input="7", actor=titulaire
        )
        initial_version = g1.version
        assert initial_version == 1

        # Le poste A (admin) modifie la note -> version passe à 2
        g2, _ = upsert_single_grade(
            enrollment=enr,
            period=t1,
            subject=s_lec,
            raw_input="8.5",
            actor=admin,
            expected_version=initial_version,
        )
        assert g2.version == 2

        # Le poste B envoie sa saisie avec l'ancienne version=1 -> conflit détecté sans écrasement !
        with pytest.raises(GradeConflictError):
            upsert_single_grade(
                enrollment=enr,
                period=t1,
                subject=s_lec,
                raw_input="6",
                actor=titulaire,
                expected_version=initial_version,
            )

        # Test via l'API HTTP JSON -> code 409 avec détails du conflit
        enr_id = enr.id

    _login_client(client, "titulaire_cm2", "Titulaire2026!")
    page = client.get(f"/notes?class_id={configured_school['cm2_id']}&period_id={configured_school['t1_id']}")
    csrf_tok = extract_csrf_token(page.data)

    conflict_resp = client.post(
        "/notes/api/cellule",
        json={
            "enrollment_id": enr_id,
            "period_id": configured_school["t1_id"],
            "subject_id": configured_school["s_lec_id"],
            "value": "6",
            "version": 1,  # obsolète (serveur à version 2)
        },
        headers={"X-CSRFToken": csrf_tok},
    )
    assert conflict_resp.status_code == 409
    body = conflict_resp.get_json()
    assert body["conflict"] is True
    assert body["current_version"] == 2
    assert body["current_display"] == "8,5"

    # Vérification de l'historique des modifications (§8.2 CdC) et du blocage après clôture (§4.3 CdC)
    with app.app_context():
        histories = GradeHistory.query.filter_by(enrollment_id=enr_id).all()
        assert len(histories) == 2

        # Valeur hors barème refusée (§19 test n°6 CdC)
        enr_obj = db.session.get(type(enr), enr_id)
        t1_obj = db.session.get(EvaluationPeriod, configured_school["t1_id"])
        s_lec_obj = db.session.get(Subject, configured_school["s_lec_id"])
        admin_obj = db.session.get(User, configured_school["admin_id"])
        with pytest.raises(GradeValidationError):
            upsert_single_grade(
                enrollment=enr_obj, period=t1_obj, subject=s_lec_obj, raw_input="12", actor=admin_obj
            )

        # Clôture de la période -> plus aucune modification ordinaire acceptée
        t1_obj.status = STATUS_CLOTUREE
        db.session.commit()
        with pytest.raises(GradeValidationError):
            upsert_single_grade(
                enrollment=enr_obj, period=t1_obj, subject=s_lec_obj, raw_input="9", actor=admin_obj
            )


def test_bruteforce_lockout_and_admin_cli_recovery(app, client, configured_school):
    """Vérifie le verrouillage après 5 échecs et la procédure de récupération administrateur."""
    page = client.get("/connexion")
    token = extract_csrf_token(page.data)

    for _ in range(5):
        r = client.post("/connexion", data={"username": "admin", "password": "wrong", "csrf_token": token})
        assert r.status_code == 401

    locked_resp = client.post(
        "/connexion", data={"username": "admin", "password": "AdminPass2026!", "csrf_token": token}
    )
    assert locked_resp.status_code == 429

    # Récupération locale via la fonction CLI documentée
    with app.app_context():
        reset_or_create_admin_account("admin", "NouveauAdmin2026!")

    ok_resp = client.post(
        "/connexion",
        data={"username": "admin", "password": "NouveauAdmin2026!", "csrf_token": token},
        follow_redirects=True,
    )
    assert ok_resp.status_code == 200
