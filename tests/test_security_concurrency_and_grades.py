"""Tests de sécurité RBAC côté serveur, CSRF, verrouillage compte, saisie concurrente (409) et clôture."""

from __future__ import annotations

from datetime import date
import pytest

from tests.conftest import extract_csrf_token
from yelen_notes.extensions import db
from yelen_notes.models import (
    STATUS_CLOTUREE,
    AcademicClass,
    AuditLog,
    ClassEnrollment,
    EvaluationPeriod,
    GradeHistory,
    SchoolYear,
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


def test_teacher_can_edit_assigned_class_student_identities_and_info(app, client, configured_school):
    """Vérifie que le titulaire peut modifier l'identité et les infos des élèves de sa classe uniquement."""
    with app.app_context():
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        ce1 = db.session.get(AcademicClass, configured_school["ce1_id"])
        enr_cm2_1 = create_and_enroll_student(
            academic_class=cm2,
            last_name="ZONGO",
            first_names="ibrahim",
            sex="M",
            birth_date=date(2015, 5, 10),
            schooling_status="Ancienne mention",
        )
        enr_cm2_2 = create_and_enroll_student(
            academic_class=cm2,
            last_name="BATIONO",
            first_names="Awa",
            sex="F",
            birth_date=date(2015, 11, 20),
        )
        enr_ce1 = create_and_enroll_student(
            academic_class=ce1,
            last_name="KABORÉ",
            first_names="Salif",
            sex="M",
            birth_date=date(2018, 2, 14),
        )
        cm2_id = cm2.id
        ce1_id = ce1.id
        enr_cm2_1_id = enr_cm2_1.id
        enr_cm2_2_id = enr_cm2_2.id
        enr_ce1_id = enr_ce1.id

    # Connexion du titulaire affecté à CM2
    _login_client(client, "titulaire_cm2", "Titulaire2026!")

    # 1. Modification individuelle (identité complète + informations scolaires) sur un élève de CM2 -> 200 OK
    detail_page = client.get(f"/eleves/{enr_cm2_1_id}")
    assert detail_page.status_code == 200
    assert "Modification autorisée" in detail_page.data.decode("utf-8")
    tok = extract_csrf_token(detail_page.data)

    upd_resp = client.post(
        f"/eleves/{enr_cm2_1_id}",
        data={
            "csrf_token": tok,
            "roll_number": "1",
            "unique_code": "MAT-CM2-0001",
            "last_name": "sawadogo",
            "first_names": "ibrahim wendkouni",
            "sex": "M",
            "birth_date": "2014-08-15",
            "schooling_status": "6e année",
            "repeated_class_detail": "CM2 (2025-2026)",
            "observations": "État civil vérifié sur extrait de naissance",
            "action": "save_and_next",
        },
        follow_redirects=False,
    )
    # Redirection directe vers l'élève suivant grâce à save_and_next
    assert upd_resp.status_code == 302
    assert f"/eleves/{enr_cm2_2_id}" in upd_resp.headers["Location"]

    with app.app_context():
        refreshed = db.session.get(ClassEnrollment, enr_cm2_1_id)
        assert refreshed.student.unique_code == "MAT-CM2-0001"
        assert refreshed.student.last_name == "SAWADOGO"
        assert refreshed.student.first_names == "Ibrahim Wendkouni"
        assert refreshed.student.birth_date == date(2014, 8, 15)
        assert refreshed.schooling_status == "6e année"
        assert refreshed.repeated_class_detail == "CM2 (2025-2026)"
        assert refreshed.observations == "État civil vérifié sur extrait de naissance"
        assert AuditLog.query.filter_by(action="STUDENT_UPDATE", target_id=str(enr_cm2_1_id)).count() == 1

    # 2. Modification groupée via le tableau rapide de la classe CM2 + renumérotation alphabétique -> OK
    bulk_get = client.get(f"/eleves/modifier-liste/{cm2_id}")
    assert bulk_get.status_code == 200
    tok_bulk = extract_csrf_token(bulk_get.data)
    bulk_post = client.post(
        f"/eleves/modifier-liste/{cm2_id}",
        data={
            "csrf_token": tok_bulk,
            f"enr_{enr_cm2_1_id}_roll_number": "1",
            f"enr_{enr_cm2_1_id}_unique_code": "MAT-CM2-0001",
            f"enr_{enr_cm2_1_id}_last_name": "SAWADOGO",
            f"enr_{enr_cm2_1_id}_first_names": "Ibrahim Wendkouni",
            f"enr_{enr_cm2_1_id}_sex": "M",
            f"enr_{enr_cm2_1_id}_birth_date": "2014-08-15",
            f"enr_{enr_cm2_1_id}_schooling_status": "6e année",
            f"enr_{enr_cm2_1_id}_repeated_class_detail": "CM2 (2025-2026)",
            f"enr_{enr_cm2_1_id}_observations": "État civil vérifié sur extrait de naissance",
            f"enr_{enr_cm2_2_id}_roll_number": "2",
            f"enr_{enr_cm2_2_id}_unique_code": "MAT-CM2-0002",
            f"enr_{enr_cm2_2_id}_last_name": "BATIONO",
            f"enr_{enr_cm2_2_id}_first_names": "Awa",
            f"enr_{enr_cm2_2_id}_sex": "F",
            f"enr_{enr_cm2_2_id}_birth_date": "2015-11-20",
            f"enr_{enr_cm2_2_id}_schooling_status": "6e année",
            f"enr_{enr_cm2_2_id}_repeated_class_detail": "",
            f"enr_{enr_cm2_2_id}_observations": "RAS",
        },
        follow_redirects=True,
    )
    assert bulk_post.status_code == 200
    assert "1 élève(s) ont été mises à jour" in bulk_post.data.decode("utf-8")

    renum_resp = client.post(
        f"/eleves/renumeroter/{cm2_id}",
        data={"csrf_token": tok_bulk},
        follow_redirects=True,
    )
    assert renum_resp.status_code == 200
    with app.app_context():
        # BATIONO passe N° 1 et SAWADOGO N° 2 après renumérotation alphabétique
        assert db.session.get(ClassEnrollment, enr_cm2_2_id).roll_number == 1
        assert db.session.get(ClassEnrollment, enr_cm2_1_id).roll_number == 2

    # 3. Isolation stricte : le titulaire de CM2 NE PEUT PAS modifier un élève de CE1 ni désactiver un élève
    assert client.get(f"/eleves/{enr_ce1_id}").status_code == 403
    assert client.post(
        f"/eleves/{enr_ce1_id}",
        data={
            "csrf_token": tok_bulk,
            "last_name": "PIRATE",
            "first_names": "Test",
            "sex": "M",
            "birth_date": "2018-02-14",
        },
    ).status_code == 403
    assert client.get(f"/eleves/modifier-liste/{ce1_id}").status_code == 403
    assert client.post(f"/eleves/{enr_cm2_1_id}/statut", data={"csrf_token": tok_bulk, "is_active": "0"}).status_code == 403

    # 4. Profil CONSULTATION : lecture seule, aucune modification possible (403)
    client.post("/deconnexion", data={"csrf_token": tok_bulk})
    _login_client(client, "lecteur", "Lecteur2026!")
    ro_page = client.get(f"/eleves/{enr_cm2_1_id}")
    assert ro_page.status_code == 200
    assert "Lecture seule" in ro_page.data.decode("utf-8")
    tok_ro = extract_csrf_token(ro_page.data)
    assert client.post(
        f"/eleves/{enr_cm2_1_id}",
        data={
            "csrf_token": tok_ro,
            "last_name": "TEST",
            "first_names": "Test",
            "sex": "M",
            "birth_date": "2014-08-15",
        },
    ).status_code == 403
    assert client.get(f"/eleves/modifier-liste/{cm2_id}").status_code == 403

    # 5. Année scolaire clôturée : même le titulaire ne peut plus modifier la fiche (403)
    client.post("/deconnexion", data={"csrf_token": tok_ro})
    with app.app_context():
        yr = db.session.get(SchoolYear, configured_school["year_id"])
        yr.status = STATUS_CLOTUREE
        db.session.commit()

    _login_client(client, "titulaire_cm2", "Titulaire2026!")
    closed_page = client.get(f"/eleves/{enr_cm2_1_id}")
    assert closed_page.status_code == 200
    assert "Lecture seule" in closed_page.data.decode("utf-8")
    tok_closed = extract_csrf_token(closed_page.data)
    assert client.post(
        f"/eleves/{enr_cm2_1_id}",
        data={
            "csrf_token": tok_closed,
            "last_name": "MODIF",
            "first_names": "Impossible",
            "sex": "M",
            "birth_date": "2014-08-15",
        },
    ).status_code == 403


def test_teacher_can_add_and_update_subjects_and_scales_for_assigned_class_only(
    app, client, configured_school
):
    """Vérifie que le titulaire peut ajouter/modifier les matières et barèmes de sa classe uniquement."""
    with app.app_context():
        ce1 = db.session.get(AcademicClass, configured_school["ce1_id"])
        ce1_subj = Subject(
            class_id=ce1.id,
            name="Dessin CE1",
            short_label="Des",
            coefficient=1,
            min_score=0,
            max_score=10,
            reference_scale=10,
            display_order=1,
            is_active=True,
        )
        db.session.add(ce1_subj)
        db.session.commit()
        cm2_id = configured_school["cm2_id"]
        ce1_id = ce1.id
        ce1_subj_id = ce1_subj.id
        year_id = configured_school["year_id"]

    _login_client(client, "titulaire_cm2", "Titulaire2026!")

    # 1. Raccourci menu « Matières & Barèmes » redirige vers la classe affectée CM2
    shortcut_resp = client.get("/matieres-et-baremes", follow_redirects=False)
    assert shortcut_resp.status_code == 302
    assert f"/classes/{cm2_id}/matieres" in shortcut_resp.headers["Location"]

    subj_page = client.get(f"/classes/{cm2_id}/matieres")
    assert subj_page.status_code == 200
    assert "Ajouter une matière et son barème" in subj_page.data.decode("utf-8")
    tok = extract_csrf_token(subj_page.data)

    # 2. Ajout d'une nouvelle matière et de son barème (/20 ramené sur /10, coef 2) par le titulaire
    add_resp = client.post(
        f"/classes/{cm2_id}/matieres",
        data={
            "csrf_token": tok,
            "name": "Histoire-Géographie",
            "short_label": "Hist-Géo",
            "display_order": "10",
            "min_score": "0",
            "max_score": "20",
            "reference_scale": "10",
            "coefficient": "2",
            "include_in_average": "1",
            "allow_exemption": "1",
        },
        follow_redirects=True,
    )
    assert add_resp.status_code == 200
    assert "Histoire-Géographie" in add_resp.data.decode("utf-8")

    with app.app_context():
        created = Subject.query.filter_by(class_id=cm2_id, name="Histoire-Géographie").first()
        assert created is not None
        assert float(created.max_score) == 20.0
        assert float(created.reference_scale) == 10.0
        assert float(created.coefficient) == 2.0
        created_id = created.id

    # 3. Modification du barème et du coefficient de cette matière par le titulaire
    upd_resp = client.post(
        f"/matieres/{created_id}/modifier",
        data={
            "csrf_token": tok,
            "name": "Histoire-Géographie & ECM",
            "short_label": "HG-ECM",
            "min_score": "0",
            "max_score": "10",
            "reference_scale": "10",
            "coefficient": "1,5",
            "display_order": "10",
            "include_in_average": "1",
            "allow_exemption": "1",
            "is_active": "1",
        },
        follow_redirects=True,
    )
    assert upd_resp.status_code == 200
    with app.app_context():
        refreshed = db.session.get(Subject, created_id)
        assert refreshed.name == "Histoire-Géographie & ECM"
        assert float(refreshed.max_score) == 10.0
        assert float(refreshed.coefficient) == 1.5

    # 4. Copie de matières manquantes depuis une autre classe dans CM2
    copy_resp = client.post(
        f"/classes/{cm2_id}/matieres",
        data={
            "csrf_token": tok,
            "action": "copy_subjects",
            "source_class_id": str(ce1_id),
        },
        follow_redirects=True,
    )
    assert copy_resp.status_code == 200
    with app.app_context():
        assert Subject.query.filter_by(class_id=cm2_id, name="Dessin CE1").first() is not None

    # 5. Le titulaire peut définir le barème général & la grille d'appréciations spécifiques de SA classe CM2
    pol_page = client.get("/regles-et-appreciations")
    assert pol_page.status_code == 200
    assert "Règle spécifique : CM2" in pol_page.data.decode("utf-8")
    tok_pol = extract_csrf_token(pol_page.data)

    pol_save = client.post(
        "/regles-et-appreciations",
        data={
            "csrf_token": tok_pol,
            "year_id": str(year_id),
            "class_id": str(cm2_id),
            "general_average_scale": "10",
            "decimal_places": "2",
            "rounding_mode": "ROUND_HALF_UP",
            "absence_policy": "ZERO",
            "tie_breaking_mode": "COMPETITION",
            "annual_average_mode": "SIMPLE",
            "annual_require_all_periods": "1",
            "scale_min[]": ["0", "5", "8"],
            "scale_max[]": ["5", "8", "10"],
            "scale_label[]": ["Insuffisant", "Bien", "Très bien"],
        },
        follow_redirects=True,
    )
    assert pol_save.status_code == 200
    assert "enregistrées avec succès" in pol_save.data.decode("utf-8")

    # 6. Isolation stricte : le titulaire NE PEUT PAS modifier la règle générale de l'école entière,
    #    ni accéder/modifier les matières et barèmes d'une classe non affectée (CE1)
    assert (
        client.post(
            "/regles-et-appreciations",
            data={
                "csrf_token": tok_pol,
                "year_id": str(year_id),
                "class_id": "",
                "general_average_scale": "20",
            },
        ).status_code
        == 403
    )
    assert client.get(f"/regles-et-appreciations?year_id={year_id}&class_id={ce1_id}").status_code == 403
    assert client.get(f"/classes/{ce1_id}/matieres").status_code == 403
    assert (
        client.post(
            f"/classes/{ce1_id}/matieres",
            data={
                "csrf_token": tok_pol,
                "name": "Interdit",
                "min_score": "0",
                "max_score": "10",
                "reference_scale": "10",
                "coefficient": "1",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/matieres/{ce1_subj_id}/modifier",
            data={
                "csrf_token": tok_pol,
                "name": "Interdit",
                "min_score": "0",
                "max_score": "10",
                "reference_scale": "10",
                "coefficient": "1",
            },
        ).status_code
        == 403
    )


def test_period_modification_by_titulaire_directeur_and_super_admin(app, configured_school):
    """Dans le tableau « Périodes de l'année 2026-2027 », autoriser le titulaire de la classe,
    le directeur et le super admin à modifier les périodes créées, et bloquer les profils non autorisés."""
    from yelen_notes.models import (
        ROLE_ENSEIGNANT,
        ROLE_SECRETAIRE,
        STATUS_OUVERTE,
        TeacherClassAssignment,
    )

    with app.app_context():
        year_id = configured_school["year_id"]
        t1_id = configured_school["t1_id"]
        t2_id = configured_school["t2_id"]

        # Créer un enseignant sans classe en 2026-2027 (uniquement affecté à une autre année 2025-2026)
        other_year = SchoolYear(
            label="2025-2026",
            start_date=date(2025, 10, 1),
            end_date=date(2026, 6, 30),
            age_reference_date=date(2025, 10, 1),
            status=STATUS_OUVERTE,
        )
        db.session.add(other_year)
        db.session.flush()
        other_class = AcademicClass(
            school_year_id=other_year.id,
            name="CP1-2025",
            level_label="CP1",
            director_name="M. OUÉDRAOGO Issiaka",
            director_title="Le Directeur",
            titulaire_name="M. ZONGO",
            display_order=1,
            is_active=True,
        )
        db.session.add(other_class)
        db.session.flush()

        other_teacher = User(
            username="titulaire_2025",
            full_name="Enseignant Autre Année",
            role=ROLE_ENSEIGNANT,
            is_active=True,
        )
        other_teacher.set_password("AutreAnnee2026!")
        secretaire = User(
            username="sec_periodes",
            full_name="Secrétaire Test",
            role=ROLE_SECRETAIRE,
            is_active=True,
        )
        secretaire.set_password("SecPass2026!")
        db.session.add_all([other_teacher, secretaire])
        db.session.flush()
        db.session.add(
            TeacherClassAssignment(
                user_id=other_teacher.id, class_id=other_class.id, can_edit_grades=True
            )
        )
        db.session.commit()

    # 1. SUPER ADMIN : voit le modal de modification dans « Périodes de l'année 2026-2027 » et modifie T1
    admin_c = app.test_client()
    _login_client(admin_c, "admin", "AdminPass2026!")
    p_admin = admin_c.get(f"/periodes?year_id={year_id}")
    assert p_admin.status_code == 200
    html_admin = p_admin.data.decode("utf-8")
    assert "Périodes de l&#39;année 2026-2027" in html_admin or "Périodes de l'année 2026-2027" in html_admin
    assert f'id="editPeriodModal-{t1_id}"' in html_admin
    tok_admin = extract_csrf_token(p_admin.data)

    r_admin = admin_c.post(
        f"/periodes/{t1_id}/modifier",
        data={
            "csrf_token": tok_admin,
            "name": "Premier Trimestre (SA)",
            "short_code": "TRIM1",
            "display_order": "1",
            "annual_weight": "1,5",
            "start_date": "2026-10-01",
            "end_date": "2026-12-20",
        },
        follow_redirects=True,
    )
    assert r_admin.status_code == 200
    with app.app_context():
        p1 = db.session.get(EvaluationPeriod, t1_id)
        assert p1.name == "Premier Trimestre (SA)"
        assert p1.short_code == "TRIM1"
        assert float(p1.annual_weight) == 1.5
        assert p1.start_date == date(2026, 10, 1)
        assert p1.end_date == date(2026, 12, 20)

    # 2. DIRECTEUR : voit le bouton Modifier et met à jour une période créée
    dir_c = app.test_client()
    _login_client(dir_c, "directeur", "Directeur2026!")
    p_dir = dir_c.get(f"/periodes?year_id={year_id}")
    assert p_dir.status_code == 200
    assert f'id="editPeriodModal-{t2_id}"' in p_dir.data.decode("utf-8")
    tok_dir = extract_csrf_token(p_dir.data)

    r_dir = dir_c.post(
        f"/periodes/{t2_id}/modifier",
        data={
            "csrf_token": tok_dir,
            "name": "Deuxième Trimestre (Dir)",
            "short_code": "TRIM2",
            "display_order": "2",
            "annual_weight": "2",
            "start_date": "2027-01-05",
            "end_date": "2027-03-28",
        },
        follow_redirects=True,
    )
    assert r_dir.status_code == 200
    with app.app_context():
        p2 = db.session.get(EvaluationPeriod, t2_id)
        assert p2.name == "Deuxième Trimestre (Dir)"
        assert p2.short_code == "TRIM2"
        assert float(p2.annual_weight) == 2.0

    # 3. TITULAIRE DE LA CLASSE (2026-2027) : voit les boutons « Modifier » ET « Clôturer » dans la colonne Actions
    tit_c = app.test_client()
    _login_client(tit_c, "titulaire_cm2", "Titulaire2026!")
    p_tit = tit_c.get(f"/periodes?year_id={year_id}")
    assert p_tit.status_code == 200
    html_tit = p_tit.data.decode("utf-8")
    assert f'id="editPeriodModal-{t1_id}"' in html_tit
    assert "Clôturer" in html_tit
    assert "Ajouter une période" not in html_tit
    tok_tit = extract_csrf_token(p_tit.data)

    r_tit = tit_c.post(
        f"/periodes/{t1_id}/modifier",
        data={
            "csrf_token": tok_tit,
            "name": "1er Trimestre 2026-2027",
            "short_code": "T1",
            "display_order": "4",
            "annual_weight": "1",
            "start_date": "2026-10-05",
            "end_date": "2026-12-18",
        },
        follow_redirects=True,
    )
    assert r_tit.status_code == 200
    assert "1er Trimestre 2026-2027" in r_tit.data.decode("utf-8")
    with app.app_context():
        p1_after = db.session.get(EvaluationPeriod, t1_id)
        assert p1_after.name == "1er Trimestre 2026-2027"
        assert p1_after.short_code == "T1"
        assert p1_after.display_order == 4
        assert p1_after.start_date == date(2026, 10, 5)
        assert (
            AuditLog.query.filter_by(action="PERIOD_UPDATE", target_id=t1_id)
            .order_by(AuditLog.id.desc())
            .first()
            is not None
        )

    # Validation métier : doublon de nom ou dates inversées rejetés proprement sans erreur 500
    r_dup = tit_c.post(
        f"/periodes/{t1_id}/modifier",
        data={
            "csrf_token": tok_tit,
            "name": "Deuxième Trimestre (Dir)",
            "short_code": "T1",
            "display_order": "4",
            "annual_weight": "1",
        },
        follow_redirects=True,
    )
    assert r_dup.status_code == 200
    assert "existe déjà" in r_dup.data.decode("utf-8")

    r_bad_dates = tit_c.post(
        f"/periodes/{t1_id}/modifier",
        data={
            "csrf_token": tok_tit,
            "name": "1er Trimestre 2026-2027",
            "short_code": "T1",
            "display_order": "4",
            "annual_weight": "1",
            "start_date": "2026-12-25",
            "end_date": "2026-10-01",
        },
        follow_redirects=True,
    )
    assert r_bad_dates.status_code == 200
    assert "postérieure" in r_bad_dates.data.decode("utf-8")

    # Le titulaire ne peut pas créer une nouvelle période
    assert (
        tit_c.post(
            "/periodes",
            data={"csrf_token": tok_tit, "year_id": str(year_id), "name": "T4", "short_code": "T4"},
        ).status_code
        == 403
    )

    # Le titulaire de la classe PEUT clôturer la période depuis la colonne Actions
    r_close_tit = tit_c.post(
        f"/periodes/{t1_id}/statut",
        data={"csrf_token": tok_tit, "status": "CLOTUREE"},
        follow_redirects=True,
    )
    assert r_close_tit.status_code == 200
    with app.app_context():
        p1_closed = db.session.get(EvaluationPeriod, t1_id)
        assert p1_closed.status == STATUS_CLOTUREE
        assert p1_closed.closed_by_id == configured_school["titulaire_id"]
        assert (
            AuditLog.query.filter_by(action="PERIOD_STATUS_CHANGE", target_id=t1_id)
            .order_by(AuditLog.id.desc())
            .first()
            is not None
        )

    # En revanche, la réouverture d'une période clôturée reste réservée à la direction / super admin (403 pour le titulaire)
    assert (
        tit_c.post(
            f"/periodes/{t1_id}/statut",
            data={"csrf_token": tok_tit, "status": "OUVERTE"},
        ).status_code
        == 403
    )
    # Le directeur peut rouvrir la période si nécessaire
    assert (
        dir_c.post(
            f"/periodes/{t1_id}/statut",
            data={"csrf_token": tok_dir, "status": "OUVERTE"},
            follow_redirects=True,
        ).status_code
        == 200
    )

    # 4. ISOLATION : un enseignant non titulaire en 2026-2027 reçoit 403 sur modifier ET clôturer et ne voit ni Modifier ni Clôturer
    other_c = app.test_client()
    _login_client(other_c, "titulaire_2025", "AutreAnnee2026!")
    p_other = other_c.get(f"/periodes?year_id={year_id}")
    assert p_other.status_code == 200
    html_other = p_other.data.decode("utf-8")
    assert f'id="editPeriodModal-{t1_id}"' not in html_other
    assert f'/periodes/{t1_id}/statut' not in html_other
    tok_other = extract_csrf_token(p_other.data)
    assert (
        other_c.post(
            f"/periodes/{t1_id}/modifier",
            data={"csrf_token": tok_other, "name": "Piratage", "short_code": "PIR", "annual_weight": "1"},
        ).status_code
        == 403
    )
    assert (
        other_c.post(
            f"/periodes/{t1_id}/statut",
            data={"csrf_token": tok_other, "status": "CLOTUREE"},
        ).status_code
        == 403
    )

    # 5. SECRÉTAIRE & CONSULTATION : refusés en 403 sur modifier ET clôturer
    for uname, pwd in [("sec_periodes", "SecPass2026!"), ("lecteur", "Lecteur2026!")]:
        unauth_c = app.test_client()
        _login_client(unauth_c, uname, pwd)
        p_unauth = unauth_c.get(f"/periodes?year_id={year_id}")
        assert p_unauth.status_code == 200
        html_unauth = p_unauth.data.decode("utf-8")
        assert f'id="editPeriodModal-{t1_id}"' not in html_unauth
        assert f'/periodes/{t1_id}/statut' not in html_unauth
        tok_u = extract_csrf_token(p_unauth.data)
        assert (
            unauth_c.post(
                f"/periodes/{t1_id}/modifier",
                data={"csrf_token": tok_u, "name": "Interdit", "short_code": "INT", "annual_weight": "1"},
            ).status_code
            == 403
        )
        assert (
            unauth_c.post(
                f"/periodes/{t1_id}/statut",
                data={"csrf_token": tok_u, "status": "CLOTUREE"},
            ).status_code
            == 403
        )



