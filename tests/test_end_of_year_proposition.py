"""Tests complets de la fonctionnalité « Proposition de fin d'année » (Enseignement primaire)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import io
import json
from openpyxl import load_workbook

from tests.conftest import extract_csrf_token
from yelen_notes.extensions import db
from yelen_notes.models import (
    ANNUAL_WEIGHTED,
    STATUS_OUVERTE,
    AcademicClass,
    AuditLog,
    CalculationPolicy,
    EvaluationPeriod,
    SchoolYear,
    Subject,
    User,
)
from yelen_notes.services.calculation_service import (
    PROPOSITION_NON_DETERMINE,
    PROPOSITION_PASSE,
    PROPOSITION_REDOUBLE,
    compute_end_of_year_proposition_report,
)
from yelen_notes.services.grade_service import upsert_single_grade
from yelen_notes.services.student_service import create_and_enroll_student, promote_or_repeat_students


def test_end_of_year_proposition_rules_history_incomplete_and_exports(app, client, configured_school):
    """Vérifie toutes les règles métier, d'historique, de tri, de seuil dynamique, de trace et d'export PDF/Excel."""
    with app.app_context():
        admin = db.session.get(User, configured_school["admin_id"])
        year = db.session.get(SchoolYear, configured_school["year_id"])
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        t1 = db.session.get(EvaluationPeriod, configured_school["t1_id"])
        t2 = db.session.get(EvaluationPeriod, configured_school["t2_id"])
        t2.status = STATUS_OUVERTE

        # Ajouter le 3e trimestre afin d'avoir les 3 trimestres complets (T1, T2, T3)
        t3 = EvaluationPeriod(
            school_year_id=year.id,
            name="Troisième Trimestre",
            short_code="T3",
            display_order=3,
            annual_weight=Decimal("2"),
            status=STATUS_OUVERTE,
        )
        db.session.add(t3)

        # Créer une année antérieure 2025-2026 pour vérifier la détection historique d'un redoublement de CM2
        prev_year = SchoolYear(
            label="2025-2026",
            age_reference_date=date(2025, 10, 1),
            start_date=date(2025, 10, 1),
            end_date=date(2026, 6, 30),
        )
        db.session.add(prev_year)
        db.session.flush()
        prev_cm2 = AcademicClass(
            school_year_id=prev_year.id,
            name="CM2",
            level_label="CM2",
            titulaire_name="M. TRAORÉ",
            director_title="Le Directeur",
            director_name="M. KABORÉ",
        )
        db.session.add(prev_cm2)
        db.session.flush()

        # Élève B : inscrit en CM2 l'année précédente puis redoublant en CM2 cette année
        prev_enr_b = create_and_enroll_student(
            academic_class=prev_cm2,
            last_name="DIALLO",
            first_names="Moussa",
            sex="M",
            birth_date=date(2014, 3, 10),
            schooling_status="6e année",
        )
        promote_or_repeat_students(
            source_class=prev_cm2,
            target_class=cm2,
            decisions_by_enrollment_id={prev_enr_b.id: "REDOUBLE"},
        )
        # Mettre à jour la scolarité actuelle de Moussa sans écraser son historique de redoublement
        enr_b = next(e for e in cm2.enrollments if e.student.last_name == "DIALLO")
        enr_b.schooling_status = "6e année"

        # Élève A : non redoublante (a redoublé CE2 autrefois -> ne doit PAS afficher Oui en CM2 !)
        enr_a = create_and_enroll_student(
            academic_class=cm2,
            last_name="SAWADOGO",
            first_names="Aïcha",
            sex="F",
            birth_date=date(2016, 4, 15),
            schooling_status="6e année",
            repeated_class_detail="CE2 (2023-2024)",
        )

        # Élève C : moyenne inférieure au seuil (< 5/10) -> Redouble
        enr_c = create_and_enroll_student(
            academic_class=cm2,
            last_name="ZONGO",
            first_names="Fatou",
            sex="F",
            birth_date=date(2015, 7, 20),
            schooling_status="6e année",
        )

        # Élève D : dossier incomplet au 3e trimestre -> Non déterminé et sans rang !
        enr_d = create_and_enroll_student(
            academic_class=cm2,
            last_name="OUÉDRAOGO",
            first_names="Issouf",
            sex="M",
            birth_date=date(2015, 9, 5),
            schooling_status="6e année",
        )
        db.session.commit()

        subjects = (
            Subject.query.filter_by(class_id=cm2.id, is_active=True)
            .order_by(Subject.display_order)
            .all()
        )

        def _fill_period_avg(enr, period, target_on_10: str):
            val10 = Decimal(target_on_10)
            for s in subjects:
                scaled = (val10 * Decimal(str(s.max_score))) / Decimal("10")
                upsert_single_grade(
                    enrollment=enr,
                    period=period,
                    subject=s,
                    raw_input=str(scaled),
                    actor=admin,
                    commit=False,
                )

        # Élève A : 7,00 / 8,00 / 9,00 -> Moyenne annuelle simple = 8,00 -> 1re -> Passe
        _fill_period_avg(enr_a, t1, "7")
        _fill_period_avg(enr_a, t2, "8")
        _fill_period_avg(enr_a, t3, "9")

        # Élève B (redoublant CM2) : 5,00 / 5,50 / 6,00 -> Moyenne annuelle = 5,50 -> 2e -> Passe
        _fill_period_avg(enr_b, t1, "5")
        _fill_period_avg(enr_b, t2, "5.5")
        _fill_period_avg(enr_b, t3, "6")

        # Élève C : 4,00 / 4,50 / 4,70 -> Moyenne annuelle = 4,40 (< 5,00) -> 3e -> Redouble
        _fill_period_avg(enr_c, t1, "4")
        _fill_period_avg(enr_c, t2, "4.5")
        _fill_period_avg(enr_c, t3, "4.7")

        # Élève D : T1=8,00, T2=8,00, T3 non saisi -> Non déterminé, aucun rang !
        _fill_period_avg(enr_d, t1, "8")
        _fill_period_avg(enr_d, t2, "8")
        db.session.commit()

        prop = compute_end_of_year_proposition_report(cm2)
        assert prop.total_students == 4
        assert prop.passe_count == 2
        assert prop.redouble_count == 1
        assert prop.undetermined_count == 1
        assert [s.header_label for s in prop.period_slots] == ["Moy. 1er T", "Moy. 2e T", "Moy. 3e T"]

        # Vérifier le tri décroissant, N° d'ordre 1..4, scolarité vs classe redoublée, rangs et propositions
        r1, r2, r3, r4 = prop.rows
        assert (r1.order_number, r1.enrollment.id, r1.repeated_current_class, r1.display_annual_average_2dec, r1.display_rank, r1.auto_proposition) == (
            1, enr_a.id, "Non", "8,00", "1re", PROPOSITION_PASSE
        )
        assert r1.slot_displays == ["7,00", "8,00", "9,00"]
        assert r1.schooling_display == "6e année"

        assert (r2.order_number, r2.enrollment.id, r2.repeated_current_class, r2.display_annual_average_2dec, r2.display_rank, r2.auto_proposition) == (
            2, enr_b.id, "Oui", "5,50", "2e", PROPOSITION_PASSE
        )

        assert (r3.order_number, r3.enrollment.id, r3.repeated_current_class, r3.display_annual_average_2dec, r3.display_rank, r3.auto_proposition) == (
            3, enr_c.id, "Non", "4,40", "3e", PROPOSITION_REDOUBLE
        )

        # Élève incomplet : ni moyenne annuelle nulle, ni rang, proposition = « Non déterminé »
        assert (r4.order_number, r4.enrollment.id, r4.display_annual_average_2dec, r4.display_rank, r4.auto_proposition) == (
            4, enr_d.id, "—", "—", PROPOSITION_NON_DETERMINE
        )
        assert r4.slot_displays == ["8,00", "8,00", "—"]

        # Vérifier la priorité de la règle pondérée officielle si activée (T1*1 + T2*2 + T3*2) / 5
        pol = CalculationPolicy.query.filter_by(school_year_id=year.id, class_id=None).first()
        pol.annual_average_mode = ANNUAL_WEIGHTED
        db.session.commit()
        prop_weighted = compute_end_of_year_proposition_report(cm2)
        # Élève A : (7*1 + 8*2 + 9*2) / 5 = 41/5 = 8,20
        assert prop_weighted.rows[0].display_annual_average_2dec == "8,20"
        pol.annual_average_mode = "SIMPLE"
        db.session.commit()

        cm2_id = cm2.id
        enr_c_id = enr_c.id

    # Vérification HTTP (écran, PDF paysage, export Excel exact, trace et validation modifiable)
    login_page = client.get("/connexion")
    tok = extract_csrf_token(login_page.data)
    client.post(
        "/connexion",
        data={"csrf_token": tok, "username": "directeur", "password": "Directeur2026!"},
        follow_redirects=True,
    )

    page = client.get(f"/resultats/proposition-fin-annee?class_id={cm2_id}")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "PROPOSITION DE FIN D'ANNÉE" in html
    assert "Non déterminé" in html

    pdf_resp = client.get(f"/resultats/proposition-fin-annee/pdf?class_id={cm2_id}")
    assert pdf_resp.status_code == 200
    assert pdf_resp.data.startswith(b"%PDF-")

    xlsx_resp = client.get(f"/resultats/proposition-fin-annee/excel?class_id={cm2_id}")
    assert xlsx_resp.status_code == 200
    wb = load_workbook(io.BytesIO(xlsx_resp.data))
    ws = wb.active
    expected_headers = [
        "N° d'ordre",
        "Nom et prénom(s)",
        "Âge",
        "Sexe",
        "Scolarité",
        "Classe redoublée",
        "Moy. 1er T",
        "Moy. 2e T",
        "Moy. 3e T",
        "Moyenne générale annuelle /10",
        "Rang annuel",
        "Proposition",
    ]
    actual_headers = [ws.cell(row=4, column=i).value for i in range(1, 13)]
    assert actual_headers == expected_headers
    assert ws.cell(row=5, column=1).value == 1
    assert ws.cell(row=5, column=12).value == "Passe"
    assert ws.cell(row=8, column=12).value == "Non déterminé"

    # Validation modifiable par la direction (ex. repêchage par le conseil des maîtres) + trace conservée
    tok = extract_csrf_token(page.data)
    val_resp = client.post(
        "/resultats/proposition-fin-annee/valider-decisions",
        data={
            "csrf_token": tok,
            "class_id": str(cm2_id),
            "mode_action": "save_custom",
            f"decision_{enr_c_id}": "Passe (repêchée conseil)",
        },
        follow_redirects=True,
    )
    assert val_resp.status_code == 200
    assert "Passe (repêchée conseil)" in val_resp.data.decode("utf-8")

    with app.app_context():
        logs = AuditLog.query.filter(
            AuditLog.action.in_(["PROPOSITION_FIN_ANNEE_PDF", "PROPOSITION_FIN_ANNEE_VALIDATION"])
        ).all()
        assert len(logs) >= 2
        payload = json.loads(logs[-1].details_json)
        assert payload["total_students"] == 4
        assert len(payload["rows"]) == 4
