"""Tests automatisés des règles mathématiques, barèmes, états de notes, ex æquo et appréciations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from yelen_notes.extensions import db
from yelen_notes.models import (
    ABSENCE_NEUTRALIZE,
    ABSENCE_UNRANKED,
    ANNUAL_WEIGHTED,
    TIE_DENSE,
    AcademicClass,
    CalculationPolicy,
    EstablishmentSettings,
    EvaluationPeriod,
    GradingScaleItem,
    SchoolYear,
    Subject,
    User,
)
from yelen_notes.services.calculation_service import (
    compute_annual_class_results,
    compute_period_class_results,
    quantize_value,
    validate_grading_scale_intervals,
)
from yelen_notes.services.grade_service import upsert_single_grade
from yelen_notes.services.student_service import (
    create_and_enroll_student,
    toggle_enrollment_active_state,
)


def test_blank_database_has_no_preconfigured_pedagogical_rules(app):
    """Vérifie que la base initiale est 100 % vierge hors pays/devise modifiables."""
    with app.app_context():
        assert User.query.count() == 0
        assert SchoolYear.query.count() == 0
        assert EvaluationPeriod.query.count() == 0
        assert AcademicClass.query.count() == 0
        assert Subject.query.count() == 0
        assert CalculationPolicy.query.count() == 0
        settings = EstablishmentSettings.get_singleton()
        assert settings.country == "Burkina Faso"
        assert settings.motto == "La Patrie ou la Mort, nous Vaincrons"


def test_exact_age_calculation_at_reference_date(app, configured_school):
    """§7.1 CdC : L'âge est calculé exactement à la date de référence, pas par simple différence d'années."""
    with app.app_context():
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        # Date de référence : 01/10/2026
        e_before = create_and_enroll_student(
            academic_class=cm2,
            last_name="KABORÉ",
            first_names="Awa",
            sex="F",
            birth_date=date(2015, 9, 30),  # anniversaire déjà passé au 01/10/2026 -> 11 ans
        )
        e_after = create_and_enroll_student(
            academic_class=cm2,
            last_name="ZONGO",
            first_names="Issa",
            sex="M",
            birth_date=date(2015, 10, 15),  # anniversaire pas encore passé au 01/10/2026 -> 10 ans
        )
        ref = cm2.school_year.age_reference_date
        assert e_before.student.age_at(ref) == 11
        assert e_after.student.age_at(ref) == 10


def test_pedagogical_calculation_reference_cases_and_distinct_states(app, configured_school):
    """Vérifie l'exemple de recette (§3.7) : conversion /20->/10, dispense, absence=0, 0 réel et note manquante."""
    with app.app_context():
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        t1 = db.session.get(EvaluationPeriod, configured_school["t1_id"])
        admin = db.session.get(User, configured_school["admin_id"])
        s_lec = db.session.get(Subject, configured_school["s_lec_id"])
        s_calc = db.session.get(Subject, configured_school["s_calc_id"])
        s_sport = db.session.get(Subject, configured_school["s_sport_id"])

        # Élève A (Aminata, F) : Lecture 8/10 (c.2), Calcul 15/20 -> 7,50/10 (c.2), Sport DISP -> Moyenne = 31 / 4 = 7,75
        aminata = create_and_enroll_student(
            academic_class=cm2, last_name="KABORÉ", first_names="Aminata", sex="F", birth_date=date(2015, 4, 10)
        )
        upsert_single_grade(enrollment=aminata, period=t1, subject=s_lec, raw_input="8", actor=admin)
        upsert_single_grade(enrollment=aminata, period=t1, subject=s_calc, raw_input="15", actor=admin)
        upsert_single_grade(enrollment=aminata, period=t1, subject=s_sport, raw_input="DISP", actor=admin)

        # Élève B (Moussa, M) : Lecture 9/10 (c.2), Calcul ABS -> 0 (c.2), Sport 8/10 (c.1) -> Moyenne = 26 / 5 = 5,20
        moussa = create_and_enroll_student(
            academic_class=cm2, last_name="OUÉDRAOGO", first_names="Moussa", sex="M", birth_date=date(2015, 6, 12)
        )
        upsert_single_grade(enrollment=moussa, period=t1, subject=s_lec, raw_input="9", actor=admin)
        upsert_single_grade(enrollment=moussa, period=t1, subject=s_calc, raw_input="ABS", actor=admin)
        upsert_single_grade(enrollment=moussa, period=t1, subject=s_sport, raw_input="8", actor=admin)

        # Élève C (Issouf, M) : Lecture 7/10, Calcul NON SAISIE, Sport 8/10 -> Jamais 0, Incomplet / Non classé !
        issouf = create_and_enroll_student(
            academic_class=cm2, last_name="SAWADOGO", first_names="Issouf", sex="M", birth_date=date(2015, 2, 3)
        )
        upsert_single_grade(enrollment=issouf, period=t1, subject=s_lec, raw_input="7", actor=admin)
        upsert_single_grade(enrollment=issouf, period=t1, subject=s_sport, raw_input="8", actor=admin)

        # Élève D (Zéro réel) : Lecture 0/10, Calcul 0/20, Sport 0/10 -> Classable avec 0,00 !
        salif = create_and_enroll_student(
            academic_class=cm2, last_name="TRAORÉ", first_names="Salif", sex="M", birth_date=date(2015, 1, 20)
        )
        upsert_single_grade(enrollment=salif, period=t1, subject=s_lec, raw_input="0", actor=admin)
        upsert_single_grade(enrollment=salif, period=t1, subject=s_calc, raw_input="0", actor=admin)
        upsert_single_grade(enrollment=salif, period=t1, subject=s_sport, raw_input="0", actor=admin)

        res = compute_period_class_results(cm2, t1)

        row_a = res.all_rows_by_enrollment[aminata.id]
        assert row_a.is_rankable is True
        assert row_a.rounded_average == Decimal("7.75")
        assert row_a.appreciation == "Bien"
        assert row_a.rank_label == "1re"

        row_b = res.all_rows_by_enrollment[moussa.id]
        assert row_b.is_rankable is True
        assert row_b.rounded_average == Decimal("5.20")
        assert row_b.appreciation == "Passable"
        assert row_b.rank_label == "2e"

        row_c = res.all_rows_by_enrollment[issouf.id]
        assert row_c.is_complete is False
        assert row_c.is_rankable is False
        assert row_c.rounded_average is None
        assert row_c.rank is None

        row_d = res.all_rows_by_enrollment[salif.id]
        assert row_d.is_complete is True
        assert row_d.is_rankable is True
        assert row_d.rounded_average == Decimal("0.00")
        assert row_d.appreciation == "Insuffisant"
        assert row_d.rank_label == "3e"

        # Vérification des autres politiques configurables d'absence (NEUTRALIZE et UNRANKED)
        pol = CalculationPolicy.query.filter_by(school_year_id=cm2.school_year_id).first()
        pol.absence_policy = ABSENCE_NEUTRALIZE
        db.session.commit()
        res_neut = compute_period_class_results(cm2, t1)
        # Moussa sans Calcul : (9*2 + 8*1) / (2+1) = 26 / 3 = 8,666... -> 8,67 (ROUND_HALF_UP)
        assert res_neut.all_rows_by_enrollment[moussa.id].rounded_average == Decimal("8.67")
        assert res_neut.all_rows_by_enrollment[moussa.id].appreciation == "Très bien"

        pol.absence_policy = ABSENCE_UNRANKED
        db.session.commit()
        res_unrk = compute_period_class_results(cm2, t1)
        assert res_unrk.all_rows_by_enrollment[moussa.id].is_rankable is False


def test_ex_aequo_ranking_modes_and_inactive_exclusion(app, configured_school):
    """Vérifie le classement des ex æquo avec saut (1er, 1er ex, 3e) et sans saut (1er, 1er ex, 2e) + exclusion inactifs."""
    with app.app_context():
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        t1 = db.session.get(EvaluationPeriod, configured_school["t1_id"])
        admin = db.session.get(User, configured_school["admin_id"])
        subjects = [
            db.session.get(Subject, configured_school["s_lec_id"]),
            db.session.get(Subject, configured_school["s_calc_id"]),
            db.session.get(Subject, configured_school["s_sport_id"]),
        ]

        e1 = create_and_enroll_student(academic_class=cm2, last_name="A", first_names="Fille", sex="F", birth_date=date(2015, 1, 1))
        e2 = create_and_enroll_student(academic_class=cm2, last_name="B", first_names="Garcon", sex="M", birth_date=date(2015, 1, 2))
        e3 = create_and_enroll_student(academic_class=cm2, last_name="C", first_names="Suivant", sex="M", birth_date=date(2015, 1, 3))
        e_inact = create_and_enroll_student(academic_class=cm2, last_name="D", first_names="Parti", sex="M", birth_date=date(2015, 1, 4))

        for enr, vals in [
            (e1, ["8", "16", "8"]),       # 8.00
            (e2, ["8", "16", "8"]),       # 8.00 (ex æquo au 1er rang)
            (e3, ["7", "14", "7"]),       # 7.00 -> 3e (avec saut)
            (e_inact, ["10", "20", "10"]),  # 10.00 mais désactivé -> exclu du classement !
        ]:
            for s, v in zip(subjects, vals):
                upsert_single_grade(enrollment=enr, period=t1, subject=s, raw_input=v, actor=admin)

        toggle_enrollment_active_state(e_inact, is_active=False, reason="Transfert")

        res = compute_period_class_results(cm2, t1)
        assert len(res.ranked_rows) == 3
        assert res.all_rows_by_enrollment[e1.id].rank_label == "1re ex"
        assert res.all_rows_by_enrollment[e2.id].rank_label == "1er ex"
        assert res.all_rows_by_enrollment[e3.id].rank_label == "3e"
        # L'élève inactif conserve sa moyenne historique 10,00 mais n'est pas dans les classés actifs
        assert res.all_rows_by_enrollment[e_inact.id].rounded_average == Decimal("10.00")
        assert len(res.inactive_rows) == 1

        # Mode DENSE sans saut
        pol = CalculationPolicy.query.filter_by(school_year_id=cm2.school_year_id).first()
        pol.tie_breaking_mode = TIE_DENSE
        db.session.commit()
        res_dense = compute_period_class_results(cm2, t1)
        assert res_dense.all_rows_by_enrollment[e3.id].rank_label == "2e"


def test_annual_average_simple_and_weighted_and_round_half_up(app, configured_school):
    """Vérifie le calcul de la moyenne annuelle (simple puis pondérée) et l'arrondi ROUND_HALF_UP."""
    assert quantize_value(Decimal("7.445"), 2, "ROUND_HALF_UP") == Decimal("7.45")
    assert quantize_value(Decimal("7.444"), 2, "ROUND_HALF_UP") == Decimal("7.44")

    with app.app_context():
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        t1 = db.session.get(EvaluationPeriod, configured_school["t1_id"])  # poids 1
        t2 = db.session.get(EvaluationPeriod, configured_school["t2_id"])  # poids 2
        admin = db.session.get(User, configured_school["admin_id"])
        subjects = [
            db.session.get(Subject, configured_school["s_lec_id"]),
            db.session.get(Subject, configured_school["s_calc_id"]),
            db.session.get(Subject, configured_school["s_sport_id"]),
        ]

        eleve = create_and_enroll_student(
            academic_class=cm2, last_name="SANOU", first_names="Fatimata", sex="F", birth_date=date(2015, 5, 5)
        )
        # T1 = 6,00 ; T2 = 9,00
        for s, v in zip(subjects, ["6", "12", "6"]):
            upsert_single_grade(enrollment=eleve, period=t1, subject=s, raw_input=v, actor=admin)
        for s, v in zip(subjects, ["9", "18", "9"]):
            upsert_single_grade(enrollment=eleve, period=t2, subject=s, raw_input=v, actor=admin)

        ann_simple = compute_annual_class_results(cm2)
        assert ann_simple.all_rows_by_enrollment[eleve.id].rounded_annual_average == Decimal("7.50")

        pol = CalculationPolicy.query.filter_by(school_year_id=cm2.school_year_id).first()
        pol.annual_average_mode = ANNUAL_WEIGHTED
        db.session.commit()
        # Pondéré : (6*1 + 9*2) / (1+2) = 24 / 3 = 8,00
        ann_weighted = compute_annual_class_results(cm2)
        assert ann_weighted.all_rows_by_enrollment[eleve.id].rounded_annual_average == Decimal("8.00")


def test_grading_scale_overlap_and_gap_validation():
    """§6 CdC : Empêche les chevauchements et signale les intervalles manquants."""
    overlap = validate_grading_scale_intervals(
        [
            {"min_score": "0", "max_score": "6", "appreciation": "Insuffisant"},
            {"min_score": "5.5", "max_score": "10", "appreciation": "Bien"},
        ],
        Decimal("10"),
    )
    assert overlap.is_valid is False
    assert any("Chevauchement" in e for e in overlap.errors)

    gap = validate_grading_scale_intervals(
        [
            {"min_score": "0", "max_score": "5", "appreciation": "Insuffisant"},
            {"min_score": "6", "max_score": "10", "appreciation": "Bien"},
        ],
        Decimal("10"),
    )
    assert gap.is_valid is True
    assert any("Intervalle manquant" in w for w in gap.warnings)
