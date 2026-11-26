"""Tableau de bord dynamique selon le profil, les permissions RBAC et l'avancement (§13 CdC & Design SaaS)."""

from __future__ import annotations

from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_ENSEIGNANT,
    AccountingEntry,
    AuditLog,
    CalculationPolicy,
    ClassEnrollment,
    EstablishmentSettings,
    EvaluationPeriod,
    Role,
    SchoolYear,
    Subject,
    User,
    UserRole,
)
from yelen_notes.security import (
    get_accessible_classes_query,
    get_accessible_years_query,
    get_current_establishment_id,
    get_user_accessible_establishment_ids,
    has_any_permission,
    has_permission,
    is_strictly_scoped_parent,
    permission_required,
    user_can_access_establishment,
)
from yelen_notes.services.calculation_service import compute_period_class_results

dashboard_bp = Blueprint("dashboard", __name__)


def get_selected_school_year() -> SchoolYear | None:
    """Détermine l'année scolaire consultée en session dans le périmètre d'établissement autorisé."""
    is_auth = getattr(current_user, "is_authenticated", False)
    sess_year_id = session.get("selected_school_year_id")
    if sess_year_id:
        yr = db.session.get(SchoolYear, int(sess_year_id))
        if yr is not None and (not is_auth or user_can_access_establishment(current_user, yr.establishment_id)):
            return yr
    settings = EstablishmentSettings.get_singleton()
    if settings.active_school_year_id:
        yr = db.session.get(SchoolYear, settings.active_school_year_id)
        if yr is not None and (not is_auth or user_can_access_establishment(current_user, yr.establishment_id)):
            return yr
    if is_auth:
        return get_accessible_years_query(current_user).first()
    return SchoolYear.query.order_by(SchoolYear.id.desc()).first()


@dashboard_bp.route("/")
@permission_required("dashboard.view", module="dashboard")
def index():
    if is_strictly_scoped_parent(current_user):
        return redirect(url_for("operations.parent_portal"))

    settings = EstablishmentSettings.get_singleton()
    selected_year = get_selected_school_year()
    all_years = get_accessible_years_query(current_user).all()
    cur_est_id = get_current_establishment_id(current_user)
    allowed_ests = get_user_accessible_establishment_ids(current_user)

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
            "desc": "Nom de l'école, CEB, localité et logo officiel facultatif.",
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
    setup_complete = all(s["done"] for s in setup_steps) or not has_permission(
        current_user, "settings.update"
    )

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

    girls_pct = round((total_girls * 100.0 / total_active_pupils), 1) if total_active_pupils > 0 else 0.0
    boys_pct = round((total_boys * 100.0 / total_active_pupils), 1) if total_active_pupils > 0 else 0.0

    # Nombre d'enseignants actifs dans le périmètre de l'établissement
    teacher_role = Role.query.filter_by(code=ROLE_ENSEIGNANT).first()
    teachers_q = User.query.filter(User.is_active.is_(True))
    if allowed_ests is not None:
        teachers_q = teachers_q.filter(
            or_(User.establishment_id.in_(allowed_ests), User.establishment_id.is_(None))
        )
    else:
        teachers_q = teachers_q.filter(
            or_(User.establishment_id == cur_est_id, User.establishment_id.is_(None))
        )
    if teacher_role is not None:
        teachers_count = (
            teachers_q.outerjoin(UserRole, User.id == UserRole.user_id)
            .filter(or_(UserRole.role_id == teacher_role.id, User.role == ROLE_ENSEIGNANT))
            .distinct()
            .count()
        )
    else:
        teachers_count = teachers_q.filter_by(role=ROLE_ENSEIGNANT).count()

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
    latest_pass_rate: Decimal | None = None
    latest_period_label: str = ""

    can_see_pedagogy = has_any_permission(current_user, "grades.view", "bulletins.view")
    if selected_class is not None:
        for enr in selected_class.enrollments:
            if enr.is_active:
                selected_class_active += 1
                if enr.student.sex == "F":
                    selected_class_girls += 1
                else:
                    selected_class_boys += 1
        if can_see_pedagogy:
            for p in periods:
                res = compute_period_class_results(selected_class, p)
                avg_pct = 0.0
                if res.class_average is not None and res.general_scale and res.general_scale > 0:
                    avg_pct = float(min(Decimal("100"), (res.class_average * Decimal("100")) / res.general_scale))
                if res.pass_rate_percent is not None and res.ranked_count > 0:
                    latest_pass_rate = res.pass_rate_percent
                    latest_period_label = p.short_code
                period_progress_cards.append(
                    {
                        "period": p,
                        "completion_percent": res.completion_rate_percent,
                        "ranked_count": res.ranked_count,
                        "incomplete_count": res.incomplete_count,
                        "class_average": res.class_average,
                        "general_scale": res.general_scale,
                        "decimal_places": res.decimal_places,
                        "pass_rate_percent": res.pass_rate_percent,
                        "pass_count": res.pass_count,
                        "avg_height_pct": round(avg_pct, 1),
                    }
                )

    # Situation financière synthétique — strictement réservée aux détenteurs de `accounting.view`
    financial_summary = None
    if has_permission(current_user, "accounting.view"):
        entries = AccountingEntry.query.filter_by(establishment_id=cur_est_id).all()
        recettes = sum((e.amount for e in entries if e.entry_type != "DEPENSE"), Decimal("0.00"))
        depenses = sum((e.amount for e in entries if e.entry_type == "DEPENSE"), Decimal("0.00"))
        pending_entries = sum(1 for e in entries if not e.is_validated)
        financial_summary = {
            "recettes": recettes,
            "depenses": depenses,
            "solde": recettes - depenses,
            "pending_count": pending_entries,
            "entries_count": len(entries),
        }

    # Dernières activités pertinentes selon les permissions RBAC
    audit_q = AuditLog.query
    if has_permission(current_user, "audit.view"):
        if allowed_ests is not None:
            audit_q = audit_q.filter(
                or_(AuditLog.establishment_id.in_(allowed_ests), AuditLog.establishment_id.is_(None))
            )
    else:
        audit_q = audit_q.filter(AuditLog.user_id == current_user.id)
    recent_activities = audit_q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(6).all()

    # Notifications métier importantes
    dashboard_alerts: list[dict[str, str]] = []
    if not setup_complete:
        rem = sum(1 for s in setup_steps if not s["done"])
        dashboard_alerts.append(
            {
                "level": "info",
                "title": f"{rem} étape(s) de configuration initiale à compléter",
                "detail": "Finalisez le paramétrage de l'établissement pour activer tous les calculs.",
                "url": setup_steps[0]["url"],
            }
        )
    for item in period_progress_cards:
        if item["period"].status == "OUVERTE" and item["incomplete_count"] > 0:
            dashboard_alerts.append(
                {
                    "level": "warning",
                    "title": f"{item['incomplete_count']} dossier(s) incomplet(s) en {item['period'].name}",
                    "detail": f"Classe {selected_class.name} — saisie à {item['completion_percent']}%.",
                    "url": url_for("grades.grade_entry", class_id=selected_class.id, period_id=item["period"].id),
                }
            )
    if financial_summary and financial_summary["pending_count"] > 0 and has_permission(current_user, "accounting.validate"):
        dashboard_alerts.append(
            {
                "level": "warning",
                "title": f"{financial_summary['pending_count']} écriture(s) comptable(s) en attente de validation",
                "detail": "Vérifiez et validez les pièces en brouillon dans le journal comptable.",
                "url": url_for("operations.accounting_manage"),
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
        girls_pct=girls_pct,
        boys_pct=boys_pct,
        teachers_count=teachers_count,
        selected_class_active=selected_class_active,
        selected_class_girls=selected_class_girls,
        selected_class_boys=selected_class_boys,
        periods=periods,
        period_progress_cards=period_progress_cards,
        latest_pass_rate=latest_pass_rate,
        latest_period_label=latest_period_label,
        can_see_pedagogy=can_see_pedagogy,
        financial_summary=financial_summary,
        recent_activities=recent_activities,
        dashboard_alerts=dashboard_alerts,
    )


@dashboard_bp.route("/changer-annee-consultation", methods=["POST"])
@login_required
def switch_view_year():
    year_id = request.form.get("school_year_id", type=int)
    if year_id:
        yr = db.session.get(SchoolYear, year_id)
        if yr is not None:
            if not user_can_access_establishment(current_user, yr.establishment_id):
                return redirect(url_for("dashboard.index"))
            session["selected_school_year_id"] = yr.id
            flash(f"Année scolaire affichée : {yr.label}.", "info")
    ref = request.referrer or url_for("dashboard.index")
    return redirect(ref)
