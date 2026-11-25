"""Fixtures partagées de la suite de tests automatisés Yelen Notes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
import pytest

from yelen_notes import create_app
from yelen_notes.extensions import db
from yelen_notes.models import (
    ABSENCE_ZERO,
    ANNUAL_SIMPLE,
    ROLE_ADMIN,
    ROLE_CONSULTATION,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    ROUNDING_HALF_UP,
    STATUS_OUVERTE,
    TIE_COMPETITION,
    AcademicClass,
    CalculationPolicy,
    EstablishmentSettings,
    EvaluationPeriod,
    GradingScaleItem,
    SchoolYear,
    Subject,
    TeacherClassAssignment,
    User,
)
from yelen_notes.paths import build_runtime_paths


def extract_csrf_token(html_bytes: bytes) -> str:
    text = html_bytes.decode("utf-8")
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', text)
    if m:
        return m.group(1)
    m2 = re.search(r'name="csrf_token"\s+value="([^"]+)"', text)
    return m2.group(1) if m2 else ""


@pytest.fixture()
def app(tmp_path):
    data_dir = tmp_path / "yelen_data"
    paths = build_runtime_paths(data_dir_override=data_dir)
    flask_app = create_app(
        runtime_paths=paths,
        server_mode="monoposte",
        config_overrides={"WTF_CSRF_ENABLED": True},
        auto_init_db=True,
    )
    yield flask_app
    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def configured_school(app):
    """Crée un environnement scolaire configuré pour tester les calculs, droits, exports et PDF."""
    with app.app_context():
        admin = User(username="admin", full_name="Admin Test", role=ROLE_ADMIN, is_active=True)
        admin.set_password("AdminPass2026!")

        directeur = User(
            username="directeur", full_name="Directeur Test", role=ROLE_DIRECTEUR, is_active=True
        )
        directeur.set_password("Directeur2026!")

        titulaire_cm2 = User(
            username="titulaire_cm2", full_name="Titulaire CM2", role=ROLE_ENSEIGNANT, is_active=True
        )
        titulaire_cm2.set_password("Titulaire2026!")

        lecteur = User(
            username="lecteur", full_name="Lecteur Test", role=ROLE_CONSULTATION, is_active=True
        )
        lecteur.set_password("Lecteur2026!")

        db.session.add_all([admin, directeur, titulaire_cm2, lecteur])
        db.session.flush()

        yr = SchoolYear(
            label="2026-2027",
            start_date=date(2026, 10, 1),
            end_date=date(2027, 6, 30),
            age_reference_date=date(2026, 10, 1),
            status=STATUS_OUVERTE,
            default_director_name="M. OUÉDRAOGO Issiaka",
            default_director_title="Le Directeur",
        )
        db.session.add(yr)
        db.session.flush()

        settings = EstablishmentSettings.get_singleton()
        settings.school_name = "École Primaire Publique de Tanghin"
        settings.ceb = "CEB de Ouagadougou II"
        settings.locality = "Ouagadougou"
        settings.active_school_year_id = yr.id

        pol = CalculationPolicy(
            school_year_id=yr.id,
            class_id=None,
            general_average_scale=Decimal("10.00"),
            decimal_places=2,
            rounding_mode=ROUNDING_HALF_UP,
            absence_policy=ABSENCE_ZERO,
            tie_breaking_mode=TIE_COMPETITION,
            annual_average_mode=ANNUAL_SIMPLE,
            annual_require_all_periods=True,
        )
        db.session.add(pol)
        db.session.flush()

        for idx, (lo, hi, lbl, last) in enumerate(
            [
                ("0", "5", "Insuffisant", False),
                ("5", "6", "Passable", False),
                ("6", "7", "Assez bien", False),
                ("7", "8.5", "Bien", False),
                ("8.5", "10", "Très bien", True),
            ],
            start=1,
        ):
            db.session.add(
                GradingScaleItem(
                    policy_id=pol.id,
                    min_score=Decimal(lo),
                    max_score=Decimal(hi),
                    appreciation=lbl,
                    is_last_interval=last,
                    display_order=idx,
                )
            )

        t1 = EvaluationPeriod(
            school_year_id=yr.id,
            name="Premier Trimestre",
            short_code="T1",
            display_order=1,
            annual_weight=Decimal("1"),
            status=STATUS_OUVERTE,
        )
        t2 = EvaluationPeriod(
            school_year_id=yr.id,
            name="Deuxième Trimestre",
            short_code="T2",
            display_order=2,
            annual_weight=Decimal("2"),
            status=STATUS_OUVERTE,
        )
        db.session.add_all([t1, t2])
        db.session.flush()

        cm2 = AcademicClass(
            school_year_id=yr.id,
            name="CM2 A",
            level_label="CM2",
            director_name="M. OUÉDRAOGO Issiaka",
            director_title="Le Directeur",
            titulaire_name="Mme SAWADOGO Rasmata",
            display_order=1,
        )
        ce1 = AcademicClass(
            school_year_id=yr.id,
            name="CE1",
            level_label="CE1",
            director_name="M. OUÉDRAOGO Issiaka",
            director_title="Le Directeur",
            titulaire_name="M. KABORÉ Paul",
            display_order=2,
        )
        db.session.add_all([cm2, ce1])
        db.session.flush()

        db.session.add(
            TeacherClassAssignment(user_id=titulaire_cm2.id, class_id=cm2.id, can_edit_grades=True)
        )

        s_lec = Subject(
            class_id=cm2.id,
            name="Lecture",
            short_label="Lect.",
            coefficient=Decimal("2"),
            min_score=Decimal("0"),
            max_score=Decimal("10"),
            reference_scale=Decimal("10"),
            allow_exemption=False,
            display_order=1,
        )
        s_calc = Subject(
            class_id=cm2.id,
            name="Calcul",
            short_label="Calc.",
            coefficient=Decimal("2"),
            min_score=Decimal("0"),
            max_score=Decimal("20"),
            reference_scale=Decimal("10"),
            allow_exemption=False,
            display_order=2,
        )
        s_sport = Subject(
            class_id=cm2.id,
            name="Sport",
            short_label="EPS",
            coefficient=Decimal("1"),
            min_score=Decimal("0"),
            max_score=Decimal("10"),
            reference_scale=Decimal("10"),
            allow_exemption=True,
            display_order=3,
        )
        db.session.add_all([s_lec, s_calc, s_sport])
        db.session.commit()

        return {
            "year_id": yr.id,
            "t1_id": t1.id,
            "t2_id": t2.id,
            "cm2_id": cm2.id,
            "ce1_id": ce1.id,
            "s_lec_id": s_lec.id,
            "s_calc_id": s_calc.id,
            "s_sport_id": s_sport.id,
            "admin_id": admin.id,
            "titulaire_id": titulaire_cm2.id,
        }
