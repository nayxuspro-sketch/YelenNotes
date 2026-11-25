"""Tableau de bord dynamique selon le profil et l'avancement de la configuration (§13 CdC)."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from yelen_notes.extensions import db
from yelen_notes.models import (
    CalculationPolicy,
    ClassEnrollment,
    EstablishmentSettings,
    EvaluationPeriod,
    SchoolYear,
    Subject,
)
from yelen_notes.security import get_accessible_classes_query
from yelen_notes.services.calculation_service import compute_period_class_results

dashboard_bp = Blueprint("dashboard", __name__)


def get_selected_school_year() -> SchoolYear | None:
    """Détermine l'année scolaire consultée en session, ou à défaut l'année active de l'établissement."""
    sess_year_id = session.get("selected_school_year_id")
    if sess_year_id:
        yr = db.session.get(SchoolYear, int(sess_year_id))
        if yr is not None:
            return yr
    settings = EstablishmentSettings.get_singleton()
    if settings.active_school_year_id:
        yr = db.session.get(SchoolYear, settings.active_school_year_id)
        if yr is not None:
            return yr
    return SchoolYear.query.order_by(SchoolYear.id.desc()).first()


@dashboard_bp.route("/")
@login_required
def index():
    settings = EstablishmentSettings.get_singleton()
    selected_year = get_selected_school_year()
    all_years = SchoolYear.query.order_by(SchoolYear.label.desc()).all()

    # Calcul de l'état du guide de démarrage (utile sur une base vierge)
    has_school_info = bool((settings.school_name or "").strip())
    has_year_and_periods = bool(
        selected_year
        and EvaluationPeriod.query.filter_by(school_year_id=selected_year.id).count() > 0
    )
    has_policy = bool(
        selected_year
        and CalculationPolicy.query.filter_by(school_year_id=selected_year.id).count() > 0
    )

    accessible_classes = (
        get_accessible_classes_query(current_user, selected_year.id).all()
        if selected_year
        else []
    )
    class_ids = [c.id for c in accessible_classes]
    has_classes_and_subjects = bool(
        class_ids and Subject.query.filter(Subject.class_id.in_(class_ids), Subject.is_active.is_(True)).count() > 0
    )
    has_students = bool(
        class_ids
        and ClassEnrollment.query.filter(
            ClassEnrollment.class_id.in_(class_ids), ClassEnrollment.is_active.is_(True)
        ).count()
        > 0
    )

    setup_steps = [
        {
            "done": has_school_info,
            "title": "1. Renseigner l'identité de l'établissement",
            "desc": "Nom de l'école, CEB, localité et logo facultatif.",
            "url": url_for("settings.establishment"),
        },
        {
            "done": has_year_and_periods,
            "title": "2. Créer l'année scolaire et ses périodes d'évaluation",
            "desc": "Définir l'année active, la date de référence d'âge et les périodes (trimestres, semestres…).",
            "url": url_for("settings.school_years"),
        },
        {
            "done": has_policy,
            "title": "3. Configurer le barème de référence et la grille d'appréciations",
            "desc": "Choisir le barème (/10, /20…), les règles de calcul et les seuils d'appréciation.",
            "url": url_for("academics.policies_and_scales"),
        },
        {
            "done": has_classes_and_subjects,
            "title": "4. Créer les classes et configurer leurs matières",
            "desc": "Renseigner titulaire, direction historisée, disciplines, barèmes et coefficients.",
            "url": url_for("academics.classes_list"),
        },
        {
            "done": has_students,
            "title": "5. Inscrire ou importer les élèves",
            "desc": "Saisir les élèves ou importer la liste Excel pour démarrer la saisie des notes.",
            "url": url_for("students.students_list"),
        },
    ]
    setup_complete = all(s["done"] for s in setup_steps)

    # Classe sélectionnée sur le tableau de bord
    requested_class_id = request.args.get("class_id", type=int)
    selected_class = None
    if accessible_classes:
        if requested_class_id:
            selected_class = next((c for c in accessible_classes if c.id == requested_class_id), None)
        if selected_class is None:
            selected_class = accessible_classes[0]

    # Statistiques globales de l'année sur les classes accessibles
    total_active_pupils = 0
    total_girls = 0
    total_boys = 0
    if class_ids:
        active_enrs = ClassEnrollment.query.filter(
            ClassEnrollment.class_id.in_(class_ids),
            ClassEnrollment.is_active.is_(True),
        ).all()
        total_active_pupils = len(active_enrs)
        for enr in active_enrs:
            if enr.student.sex == "F":
                total_girls += 1
            else:
                total_boys += 1

    periods = (
        EvaluationPeriod.query.filter_by(school_year_id=selected_year.id)
        .order_by(EvaluationPeriod.display_order)
        .all()
        if selected_year
        else []
    )

    period_progress_cards = []
    selected_class_girls = 0
    selected_class_boys = 0
    selected_class_active = 0
    if selected_class is not None:
        for enr in selected_class.enrollments:
            if enr.is_active:
                selected_class_active += 1
                if enr.student.sex == "F":
                    selected_class_girls += 1
                else:
                    selected_class_boys += 1
        for p in periods:
            res = compute_period_class_results(selected_class, p)
            period_progress_cards.append(
                {
                    "period": p,
                    "completion_percent": res.completion_rate_percent,
                    "ranked_count": res.ranked_count,
                    "incomplete_count": res.incomplete_count,
                    "class_average": res.class_average,
                    "general_scale": res.general_scale,
                    "decimal_places": res.decimal_places,
                }
            )

    return render_template(
        "dashboard/index.html",
        settings=settings,
        selected_year=selected_year,
        all_years=all_years,
        setup_steps=setup_steps,
        setup_complete=setup_complete,
        accessible_classes=accessible_classes,
        selected_class=selected_class,
        total_active_pupils=total_active_pupils,
        total_girls=total_girls,
        total_boys=total_boys,
        selected_class_active=selected_class_active,
        selected_class_girls=selected_class_girls,
        selected_class_boys=selected_class_boys,
        periods=periods,
        period_progress_cards=period_progress_cards,
    )


@dashboard_bp.route("/changer-annee-consultation", methods=["POST"])
@login_required
def switch_view_year():
    year_id = request.form.get("school_year_id", type=int)
    if year_id:
        yr = db.session.get(SchoolYear, year_id)
        if yr is not None:
            session["selected_school_year_id"] = yr.id
            flash(f"Année scolaire affichée : {yr.label}.", "info")
    ref = request.referrer or url_for("dashboard.index")
    return redirect(ref)
