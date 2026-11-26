"""Tests complets de la fonctionnalité : Tableau synoptique des résultats scolaires par période."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from yelen_notes.extensions import db
from yelen_notes.models import (
    ABSENCE_NEUTRALIZE,
    ABSENCE_ZERO,
    AcademicClass,
    CalculationPolicy,
    EstablishmentSettings,
    EvaluationPeriod,
    Subject,
    User,
)
from yelen_notes.services.calculation_service import compute_synoptic_period_report
from yelen_notes.services.excel_service import export_synoptic_period_to_excel
from yelen_notes.services.grade_service import upsert_single_grade
from yelen_notes.services.pdf_service import generate_synoptic_period_pdf
from yelen_notes.services.student_service import create_and_enroll_student


def test_synoptic_report_rules_na_absences_missing_and_exports(app, configured_school):
    """Vérifie toutes les règles métier (§3, §4, §5, §6, §7) du Tableau Synoptique."""
    with app.app_context():
        paths = app.config["RUNTIME_PATHS"]
        settings = EstablishmentSettings.get_singleton()
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        t1 = db.session.get(EvaluationPeriod, configured_school["t1_id"])
        admin = db.session.get(User, configured_school["admin_id"])
        s_lec = db.session.get(Subject, configured_school["s_lec_id"])   # /10 (seuil 5)
        s_calc = db.session.get(Subject, configured_school["s_calc_id"])  # /20 (seuil 10)
        s_eps = db.session.get(Subject, configured_school["s_sport_id"])  # /10 (seuil 5)

        # 1. Avant toute inscription / saisie : aucun élève n'a composé -> "N/A" (zéro division par zéro)
        empty_syn = compute_synoptic_period_report(cm2, t1)
        assert empty_syn.enrolled_total == 0
        for row in empty_syn.subject_rows:
            assert row.total.composed == 0
            assert row.total.display_pass_rate == "N/A"
            assert row.total.display_fail_rate == "N/A"
            assert row.boys.display_pass_rate == "N/A"
            assert row.girls.display_pass_rate == "N/A"

        # 2. Inscrire 3 Garçons et 2 Filles actifs (+ 1 élève désactivé qui ne doit pas fausser les effectifs)
        g1 = create_and_enroll_student(academic_class=cm2, last_name="TRAORÉ", first_names="Moussa", sex="M", birth_date=date(2015, 1, 10))
        g2 = create_and_enroll_student(academic_class=cm2, last_name="SAWADOGO", first_names="Issa", sex="M", birth_date=date(2015, 2, 15))
        g3 = create_and_enroll_student(academic_class=cm2, last_name="OUÉDRAOGO", first_names="Ali", sex="M", birth_date=date(2015, 3, 20))
        f1 = create_and_enroll_student(academic_class=cm2, last_name="KABORÉ", first_names="Aïcha", sex="F", birth_date=date(2015, 4, 5))
        f2 = create_and_enroll_student(academic_class=cm2, last_name="ZONGO", first_names="Fatou", sex="F", birth_date=date(2015, 5, 12))
        inact = create_and_enroll_student(academic_class=cm2, last_name="SANOU", first_names="Paul", sex="M", birth_date=date(2015, 6, 1))
        inact.is_active = False
        db.session.commit()

        # Saisie en Lecture (/10, seuil = 5) :
        # - g1 = 7 (réussi), g2 = 4 (échec), g3 = NON SAISI (ne doit PAS compter comme échec !)
        # - f1 = 5 (exactement la moyenne -> réussi), f2 = ABS
        upsert_single_grade(enrollment=g1, period=t1, subject=s_lec, raw_input="7", actor=admin)
        upsert_single_grade(enrollment=g2, period=t1, subject=s_lec, raw_input="4", actor=admin)
        upsert_single_grade(enrollment=f1, period=t1, subject=s_lec, raw_input="5", actor=admin)
        upsert_single_grade(enrollment=f2, period=t1, subject=s_lec, raw_input="ABS", actor=admin)

        # Saisie en Calcul (/20, seuil = 10) :
        # - g1 = 14 (réussi), g2 = 12 (réussi), g3 = 6 (échec) -> Garçons : 2/3 = 66,67 % réussite, 33,33 % échec
        # - f1 = 15 (réussi), f2 = 10 (réussi) -> Filles : 2/2 = 100,00 % réussite, 0,00 % échec
        upsert_single_grade(enrollment=g1, period=t1, subject=s_calc, raw_input="14", actor=admin)
        upsert_single_grade(enrollment=g2, period=t1, subject=s_calc, raw_input="12", actor=admin)
        upsert_single_grade(enrollment=g3, period=t1, subject=s_calc, raw_input="6", actor=admin)
        upsert_single_grade(enrollment=f1, period=t1, subject=s_calc, raw_input="15", actor=admin)
        upsert_single_grade(enrollment=f2, period=t1, subject=s_calc, raw_input="10", actor=admin)

        # Saisie en EPS : aucune note (tous sans note ou 1 dispensé) -> doit rester à "N/A"
        upsert_single_grade(enrollment=g1, period=t1, subject=s_eps, raw_input="DISP", actor=admin)

        # Avec politique d'absence par défaut (ABSENCE_ZERO), f2 (ABS en Lecture) compte comme 0 (composé + échec)
        syn = compute_synoptic_period_report(cm2, t1)
        assert syn.enrolled_boys == 3
        assert syn.enrolled_girls == 2
        assert syn.enrolled_total == 5

        by_subj = {r.subject.id: r for r in syn.subject_rows}

        lec_row = by_subj[s_lec.id]
        # Garçons en Lecture : g1 (7) et g2 (4) ont composé ; g3 (sans note) est exclu !
        assert lec_row.boys.composed == 2
        assert lec_row.boys.passed == 1
        assert lec_row.boys.failed == 1
        assert lec_row.boys.display_pass_rate == "50,00 %"
        assert lec_row.boys.display_fail_rate == "50,00 %"
        # Filles en Lecture (ABS = 0) : f1 (5 -> réussi), f2 (ABS=0 -> échec)
        assert lec_row.girls.composed == 2
        assert lec_row.girls.passed == 1
        assert lec_row.girls.failed == 1
        assert lec_row.total.composed == 4
        assert lec_row.total.passed == 2
        assert lec_row.total.failed == 2

        calc_row = by_subj[s_calc.id]
        assert calc_row.boys.composed == 3
        assert calc_row.boys.passed == 2
        assert calc_row.boys.failed == 1
        assert calc_row.boys.display_pass_rate == "66,67 %"
        assert calc_row.boys.display_fail_rate == "33,33 %"
        assert calc_row.boys.pass_rate + calc_row.boys.fail_rate == Decimal("100.00")
        assert calc_row.girls.display_pass_rate == "100,00 %"
        assert calc_row.girls.display_fail_rate == "0,00 %"
        assert calc_row.total.composed == 5
        assert calc_row.total.passed == 4
        assert calc_row.total.failed == 1
        assert calc_row.total.display_pass_rate == "80,00 %"
        assert calc_row.total.display_fail_rate == "20,00 %"

        eps_row = by_subj[s_eps.id]
        assert eps_row.total.composed == 0
        assert eps_row.total.display_pass_rate == "N/A"
        assert eps_row.total.display_fail_rate == "N/A"

        # 3. Changement de politique d'absence -> ABSENCE_NEUTRALIZE : f2 (ABS) n'est plus comptée ayant composé
        pol = CalculationPolicy.query.filter_by(school_year_id=cm2.school_year_id).first()
        pol.absence_policy = ABSENCE_NEUTRALIZE
        db.session.commit()

        syn_neut = compute_synoptic_period_report(cm2, t1)
        lec_neut = {r.subject.id: r for r in syn_neut.subject_rows}[s_lec.id]
        assert lec_neut.girls.composed == 1
        assert lec_neut.girls.passed == 1
        assert lec_neut.girls.failed == 0
        assert lec_neut.girls.display_pass_rate == "100,00 %"

        pol.absence_policy = ABSENCE_ZERO
        db.session.commit()

        # 4. Filtre sur une seule discipline (§2)
        syn_single = compute_synoptic_period_report(cm2, t1, subject_id=s_calc.id)
        assert len(syn_single.subject_rows) == 1
        assert syn_single.subject_rows[0].subject.id == s_calc.id

        # 5. Actualisation immédiate après modification d'une note (§6.6)
        upsert_single_grade(enrollment=g2, period=t1, subject=s_lec, raw_input="8", actor=admin, force_overwrite=True)
        syn_updated = compute_synoptic_period_report(cm2, t1)
        lec_updated = {r.subject.id: r for r in syn_updated.subject_rows}[s_lec.id]
        assert lec_updated.boys.passed == 2
        assert lec_updated.boys.display_pass_rate == "100,00 %"

        # 6. Export PDF (Paysage & Portrait) et Export Excel
        pdf_land = generate_synoptic_period_pdf(settings, paths, syn_updated, use_landscape=True)
        pdf_port = generate_synoptic_period_pdf(settings, paths, syn_updated, use_landscape=False)
        assert pdf_land.startswith(b"%PDF-")
        assert pdf_port.startswith(b"%PDF-")

        xlsx_bytes = export_synoptic_period_to_excel(settings, syn_updated)
        assert xlsx_bytes[:2] == b"PK"
