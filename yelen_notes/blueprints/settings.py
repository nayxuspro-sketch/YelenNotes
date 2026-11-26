"""Paramètres de l'établissement, téléversement du logo, années scolaires et périodes (§3, §4 CdC)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from PIL import Image

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_login import current_user, login_required

from yelen_notes.blueprints.dashboard import get_selected_school_year
from yelen_notes.extensions import db
from yelen_notes.models import (
    LIFECYCLE_STATUSES,
    STATUS_BROUILLON,
    STATUS_CLOTUREE,
    STATUS_OUVERTE,
    Establishment,
    EstablishmentSettings,
    EvaluationPeriod,
    SchoolYear,
    utcnow_naive,
)
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import (
    any_permission_required,
    get_accessible_years_query,
    get_current_establishment_id,
    has_permission,
    is_strictly_scoped_teacher,
    permission_required,
    record_audit_log,
    user_can_access_establishment,
    user_can_modify_year_periods,
)

settings_bp = Blueprint("settings", __name__)


def _parse_optional_date(raw: str | None):
    txt = (raw or "").strip()
    if not txt:
        return None
    return datetime.strptime(txt, "%Y-%m-%d").date()


@settings_bp.route("/logo-etablissement")
@login_required
def school_logo():
    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    if not settings.logo_filename:
        abort(404)
    logo_file = paths.logos_dir / settings.logo_filename
    if not logo_file.is_file():
        abort(404)
    return send_from_directory(paths.logos_dir, settings.logo_filename)


@settings_bp.route("/etablissement", methods=["GET", "POST"])
@permission_required("settings.view", module="settings")
def establishment():
    settings = EstablishmentSettings.get_singleton()
    years = get_accessible_years_query(current_user).all()
    can_edit = has_permission(current_user, "settings.update")
    cur_est_id = get_current_establishment_id(current_user)
    cur_est = db.session.get(Establishment, cur_est_id)

    if request.method == "POST":
        if not can_edit:
            record_audit_log(
                "ACCESS_DENIED",
                f"Modification des paramètres d'établissement refusée pour '{current_user.username}'",
                module="settings",
                status="DENIED",
                commit=True,
            )
            abort(403)

        settings.country = (request.form.get("country") or "Burkina Faso").strip()[:100]
        settings.motto = (request.form.get("motto") or "").strip()[:200]
        settings.ceb = (request.form.get("ceb") or "").strip()[:150]
        settings.school_name = (request.form.get("school_name") or "").strip()[:200]
        settings.school_code = (request.form.get("school_code") or "").strip()[:50] or None
        settings.locality = (request.form.get("locality") or "").strip()[:120] or None
        settings.address = (request.form.get("address") or "").strip()[:255] or None
        settings.phone = (request.form.get("phone") or "").strip()[:60] or None
        settings.email = (request.form.get("email") or "").strip()[:120] or None

        if cur_est is not None and cur_est.id == 1:
            if settings.school_name:
                cur_est.name = settings.school_name
            if settings.school_code:
                cur_est.code = settings.school_code
            cur_est.ceb = settings.ceb
            cur_est.locality = settings.locality
            cur_est.address = settings.address
            cur_est.phone = settings.phone
            cur_est.email = settings.email

        active_yr_id = request.form.get("active_school_year_id", type=int)
        if active_yr_id:
            yr_obj = db.session.get(SchoolYear, active_yr_id)
            if yr_obj and user_can_access_establishment(current_user, yr_obj.establishment_id):
                settings.active_school_year_id = active_yr_id
                session["selected_school_year_id"] = active_yr_id

        if has_permission(current_user, "settings.manage"):
            timeout_min = request.form.get("session_timeout_minutes", type=int)
            if timeout_min and timeout_min >= 5:
                settings.session_timeout_minutes = min(timeout_min, 480)

        # Suppression éventuelle du logo
        paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
        if request.form.get("remove_logo") == "1" and settings.logo_filename:
            (paths.logos_dir / settings.logo_filename).unlink(missing_ok=True)
            settings.logo_filename = None

        # Téléversement sécurisé d'un logo image local (vérifié et normalisé via Pillow)
        uploaded_logo = request.files.get("logo_file")
        if uploaded_logo and uploaded_logo.filename:
            try:
                img = Image.open(uploaded_logo.stream)
                img.verify()
                uploaded_logo.stream.seek(0)
                img_clean = Image.open(uploaded_logo.stream).convert("RGBA")
                img_clean.thumbnail((360, 360))
                dest_name = "logo_etablissement.png"
                img_clean.save(paths.logos_dir / dest_name, format="PNG")
                settings.logo_filename = dest_name
            except Exception:
                flash("Le fichier logo fourni n'est pas une image valide (PNG ou JPG attendu).", "danger")
                return render_template(
                    "settings/establishment.html",
                    settings=settings,
                    years=years,
                    can_edit=can_edit,
                )

        record_audit_log(
            "ESTABLISHMENT_UPDATE",
            f"Mise à jour des paramètres de l'établissement ({settings.school_name or 'sans nom'})",
            target_type="EstablishmentSettings",
            target_id=1,
            commit=True,
        )
        flash("Les paramètres de l'établissement ont été enregistrés.", "success")
        return redirect(url_for("settings.establishment"))

    return render_template(
        "settings/establishment.html",
        settings=settings,
        years=years,
        can_edit=can_edit,
    )


@settings_bp.route("/annees-scolaires", methods=["GET", "POST"])
@permission_required("evaluations.view", module="evaluations")
def school_years():
    years = get_accessible_years_query(current_user).all()
    settings = EstablishmentSettings.get_singleton()
    can_edit = has_permission(current_user, "evaluations.create") or has_permission(
        current_user, "evaluations.update"
    )

    if request.method == "POST":
        if not has_permission(current_user, "evaluations.create"):
            record_audit_log(
                "ACCESS_DENIED",
                f"Création d'année scolaire refusée pour '{current_user.username}'",
                module="evaluations",
                status="DENIED",
                commit=True,
            )
            abort(403)

        label = (request.form.get("label") or "").strip()
        def_dir_name = (request.form.get("default_director_name") or "").strip() or None
        def_dir_title = (request.form.get("default_director_title") or "Le Directeur").strip()
        make_active = request.form.get("make_active") == "1"

        if not label:
            flash("L'intitulé de l'année scolaire (ex. 2026-2027) est obligatoire.", "danger")
            return redirect(url_for("settings.school_years"))
        if SchoolYear.query.filter_by(label=label).first():
            flash(f"L'année scolaire « {label} » existe déjà.", "danger")
            return redirect(url_for("settings.school_years"))

        try:
            age_ref = _parse_optional_date(request.form.get("age_reference_date"))
            start_d = _parse_optional_date(request.form.get("start_date"))
            end_d = _parse_optional_date(request.form.get("end_date"))
        except ValueError:
            flash("Format de date invalide.", "danger")
            return redirect(url_for("settings.school_years"))

        if age_ref is None:
            flash("La date de référence pour le calcul de l'âge des élèves est obligatoire.", "danger")
            return redirect(url_for("settings.school_years"))
        if start_d and end_d and start_d > end_d:
            flash("La date de début doit précéder la date de fin.", "danger")
            return redirect(url_for("settings.school_years"))

        yr = SchoolYear(
            establishment_id=get_current_establishment_id(current_user),
            label=label,
            start_date=start_d,
            end_date=end_d,
            age_reference_date=age_ref,
            status=STATUS_OUVERTE,
            default_director_name=def_dir_name,
            default_director_title=def_dir_title,
        )
        db.session.add(yr)
        db.session.flush()

        if make_active or settings.active_school_year_id is None:
            settings.active_school_year_id = yr.id
            session["selected_school_year_id"] = yr.id

        record_audit_log(
            "SCHOOL_YEAR_CREATE",
            f"Création de l'année scolaire {yr.label}",
            target_type="SchoolYear",
            target_id=yr.id,
            commit=True,
        )
        flash(f"Année scolaire « {yr.label} » créée. Vous pouvez maintenant définir ses périodes.", "success")
        return redirect(url_for("settings.periods", year_id=yr.id))

    return render_template(
        "settings/school_years.html",
        years=years,
        settings=settings,
        can_edit=can_edit,
    )


@settings_bp.route("/annees-scolaires/<int:year_id>/modifier", methods=["POST"])
@any_permission_required("evaluations.update", "evaluations.validate", "evaluations.manage", module="evaluations")
def update_school_year(year_id: int):
    yr = db.get_or_404(SchoolYear, year_id)
    if not user_can_access_establishment(current_user, yr.establishment_id):
        abort(403)
    action = request.form.get("action", "update")

    if action == "activate":
        settings = EstablishmentSettings.get_singleton()
        settings.active_school_year_id = yr.id
        session["selected_school_year_id"] = yr.id
        record_audit_log(
            "SCHOOL_YEAR_ACTIVATE",
            f"Définition de {yr.label} comme année scolaire active",
            target_type="SchoolYear",
            target_id=yr.id,
            commit=True,
        )
        flash(f"L'année scolaire « {yr.label} » est désormais l'année active.", "success")
        return redirect(url_for("settings.school_years"))

    if action == "close":
        if not has_permission(current_user, "evaluations.validate"):
            abort(403)
        yr.status = STATUS_CLOTUREE
        yr.closed_at = utcnow_naive()
        yr.closed_by_id = current_user.id
        for p in yr.periods:
            if p.status != STATUS_CLOTUREE:
                p.status = STATUS_CLOTUREE
                p.closed_at = utcnow_naive()
                p.closed_by_id = current_user.id
        record_audit_log(
            "SCHOOL_YEAR_CLOSE",
            f"Clôture définitive de l'année scolaire {yr.label} (consultation conservée)",
            target_type="SchoolYear",
            target_id=yr.id,
            commit=True,
        )
        flash(
            f"L'année scolaire « {yr.label} » a été clôturée. Ses résultats restent consultables à tout moment.",
            "warning",
        )
        return redirect(url_for("settings.school_years"))

    if action == "reopen":
        if not has_permission(current_user, "evaluations.manage"):
            abort(403)
        yr.status = STATUS_OUVERTE
        yr.closed_at = None
        record_audit_log(
            "SCHOOL_YEAR_REOPEN",
            f"Réouverture administrative de l'année scolaire {yr.label}",
            target_type="SchoolYear",
            target_id=yr.id,
            commit=True,
        )
        flash(f"L'année scolaire « {yr.label} » a été rouverte par l'administrateur.", "info")
        return redirect(url_for("settings.school_years"))

    if yr.is_closed:
        flash("Impossible de modifier une année clôturée.", "danger")
        return redirect(url_for("settings.school_years"))

    try:
        age_ref = _parse_optional_date(request.form.get("age_reference_date"))
        start_d = _parse_optional_date(request.form.get("start_date"))
        end_d = _parse_optional_date(request.form.get("end_date"))
    except ValueError:
        flash("Format de date invalide.", "danger")
        return redirect(url_for("settings.school_years"))

    if age_ref:
        yr.age_reference_date = age_ref
    yr.start_date = start_d
    yr.end_date = end_d
    yr.default_director_name = (request.form.get("default_director_name") or "").strip() or None
    yr.default_director_title = (request.form.get("default_director_title") or "Le Directeur").strip()
    record_audit_log(
        "SCHOOL_YEAR_UPDATE",
        f"Modification des paramètres de l'année {yr.label}",
        target_type="SchoolYear",
        target_id=yr.id,
        commit=True,
    )
    flash(f"Année scolaire « {yr.label} » mise à jour.", "success")
    return redirect(url_for("settings.school_years"))


@settings_bp.route("/periodes", methods=["GET", "POST"])
@permission_required("evaluations.view", module="evaluations")
def periods():
    all_years = get_accessible_years_query(current_user).all()
    year_id = request.args.get("year_id", type=int) or request.form.get("year_id", type=int)
    selected_year = db.session.get(SchoolYear, year_id) if year_id else get_selected_school_year()
    if selected_year and not user_can_access_establishment(current_user, selected_year.establishment_id):
        abort(403)
    can_create_period = bool(
        selected_year and not selected_year.is_closed and has_permission(current_user, "evaluations.create")
    )
    can_modify_periods = user_can_modify_year_periods(current_user, selected_year)
    can_validate_periods = bool(
        selected_year and not selected_year.is_closed and has_permission(current_user, "evaluations.validate")
    )

    if request.method == "POST":
        if not has_permission(current_user, "evaluations.create") or selected_year is None:
            abort(403)
        if selected_year.is_closed:
            flash("Cette année scolaire est clôturée : impossible d'y ajouter une période.", "danger")
            return redirect(url_for("settings.periods", year_id=selected_year.id))

        name = (request.form.get("name") or "").strip()
        short_code = (request.form.get("short_code") or "").strip()
        display_order = request.form.get("display_order", type=int)
        status = (request.form.get("status") or STATUS_OUVERTE).strip().upper()
        if status not in LIFECYCLE_STATUSES:
            status = STATUS_BROUILLON

        try:
            weight = Decimal((request.form.get("annual_weight") or "1").replace(",", "."))
            start_d = _parse_optional_date(request.form.get("start_date"))
            end_d = _parse_optional_date(request.form.get("end_date"))
        except (InvalidOperation, ValueError):
            flash("Poids annuel ou dates invalides.", "danger")
            return redirect(url_for("settings.periods", year_id=selected_year.id))

        if not name or not short_code:
            flash("L'intitulé et le code court de la période sont obligatoires.", "danger")
            return redirect(url_for("settings.periods", year_id=selected_year.id))
        if weight <= 0:
            flash("Le poids annuel de la période doit être strictement positif.", "danger")
            return redirect(url_for("settings.periods", year_id=selected_year.id))
        if start_d and end_d and start_d > end_d:
            flash("La date de début ne peut pas être postérieure à la date de fin.", "danger")
            return redirect(url_for("settings.periods", year_id=selected_year.id))

        if not display_order or display_order <= 0:
            max_ord = (
                db.session.query(db.func.max(EvaluationPeriod.display_order))
                .filter_by(school_year_id=selected_year.id)
                .scalar()
            )
            display_order = (int(max_ord) + 1) if max_ord else 1

        if EvaluationPeriod.query.filter_by(school_year_id=selected_year.id, name=name).first():
            flash(f"Une période nommée « {name} » existe déjà pour cette année.", "danger")
            return redirect(url_for("settings.periods", year_id=selected_year.id))
        if EvaluationPeriod.query.filter_by(
            school_year_id=selected_year.id, display_order=display_order
        ).first():
            flash(f"L'ordre d'affichage {display_order} est déjà utilisé.", "danger")
            return redirect(url_for("settings.periods", year_id=selected_year.id))

        p = EvaluationPeriod(
            school_year_id=selected_year.id,
            name=name,
            short_code=short_code[:20],
            display_order=display_order,
            annual_weight=weight,
            start_date=start_d,
            end_date=end_d,
            status=status,
        )
        db.session.add(p)
        db.session.flush()
        record_audit_log(
            "PERIOD_CREATE",
            f"Création de la période '{p.name}' ({p.status}) pour {selected_year.label}",
            target_type="EvaluationPeriod",
            target_id=p.id,
            commit=True,
        )
        flash(f"Période « {p.name} » enregistrée.", "success")
        return redirect(url_for("settings.periods", year_id=selected_year.id))

    periods_list = (
        EvaluationPeriod.query.filter_by(school_year_id=selected_year.id)
        .order_by(EvaluationPeriod.display_order)
        .all()
        if selected_year
        else []
    )
    return render_template(
        "settings/periods.html",
        all_years=all_years,
        selected_year=selected_year,
        periods=periods_list,
        can_edit=can_create_period,
        can_create_period=can_create_period,
        can_modify_periods=can_modify_periods,
        can_validate_periods=can_validate_periods,
    )


@settings_bp.route("/periodes/<int:period_id>/statut", methods=["POST"])
@permission_required("evaluations.validate", module="evaluations")
def change_period_status(period_id: int):
    period = db.get_or_404(EvaluationPeriod, period_id)
    if not user_can_access_establishment(current_user, period.school_year.establishment_id):
        abort(403)
    if period.school_year.is_closed:
        flash("L'année scolaire est clôturée : le statut des périodes est figé.", "danger")
        return redirect(url_for("settings.periods", year_id=period.school_year_id))

    new_status = (request.form.get("status") or "").strip().upper()
    if new_status not in LIFECYCLE_STATUSES:
        flash("Statut de période invalide.", "danger")
        return redirect(url_for("settings.periods", year_id=period.school_year_id))

    old_status = period.status
    period.status = new_status
    if new_status == STATUS_CLOTUREE:
        period.closed_at = utcnow_naive()
        period.closed_by_id = current_user.id
    else:
        period.closed_at = None

    record_audit_log(
        "PERIOD_STATUS_CHANGE",
        f"Changement de statut de la période '{period.name}' ({period.school_year.label}) : {old_status} -> {new_status}",
        target_type="EvaluationPeriod",
        target_id=period.id,
        commit=True,
    )
    flash(f"Statut de « {period.name} » mis à jour : {new_status}.", "success")
    return redirect(url_for("settings.periods", year_id=period.school_year_id))


@settings_bp.route("/periodes/<int:period_id>/modifier", methods=["POST"])
@any_permission_required("evaluations.update", "periods.update", module="evaluations")
def update_period(period_id: int):
    period = db.get_or_404(EvaluationPeriod, period_id)
    school_year = period.school_year
    if not user_can_access_establishment(current_user, school_year.establishment_id):
        abort(403)
    if school_year.is_closed:
        if is_strictly_scoped_teacher(current_user):
            # Si l'enseignant n'est même pas titulaire d'une classe de cette année, refuser 403
            from yelen_notes.models import AcademicClass, TeacherClassAssignment

            has_year_class = (
                TeacherClassAssignment.query.join(
                    AcademicClass, TeacherClassAssignment.class_id == AcademicClass.id
                )
                .filter(
                    TeacherClassAssignment.user_id == current_user.id,
                    TeacherClassAssignment.can_edit_grades.is_(True),
                    AcademicClass.school_year_id == school_year.id,
                )
                .first()
                is not None
            )
            if not has_year_class:
                abort(403)
        flash("Impossible de modifier une période d'une année clôturée.", "danger")
        return redirect(url_for("settings.periods", year_id=school_year.id))

    if not user_can_modify_year_periods(current_user, school_year):
        record_audit_log(
            "ACCESS_DENIED",
            f"Tentative non autorisée de modification de la période '{period.name}' ({school_year.label}) par {current_user.username}",
            module="evaluations",
            target_type="EvaluationPeriod",
            target_id=period.id,
            status="REFUSED",
            commit=True,
        )
        abort(403)

    name = (request.form.get("name") or "").strip()
    short_code = (request.form.get("short_code") or "").strip()
    raw_order = (request.form.get("display_order") or "").strip()

    try:
        display_order = int(raw_order) if raw_order else period.display_order
        weight = Decimal(
            (request.form.get("annual_weight") or str(period.annual_weight)).replace(",", ".")
        )
        start_d = _parse_optional_date(request.form.get("start_date"))
        end_d = _parse_optional_date(request.form.get("end_date"))
    except (InvalidOperation, ValueError):
        flash("Valeurs d'ordre, de poids annuel ou de dates invalides.", "danger")
        return redirect(url_for("settings.periods", year_id=school_year.id))

    if not name or not short_code:
        flash("L'intitulé et le code court de la période sont obligatoires.", "danger")
        return redirect(url_for("settings.periods", year_id=school_year.id))
    if display_order <= 0:
        flash("L'ordre d'affichage doit être un entier strictement positif.", "danger")
        return redirect(url_for("settings.periods", year_id=school_year.id))
    if weight <= 0:
        flash("Le poids annuel de la période doit être strictement positif.", "danger")
        return redirect(url_for("settings.periods", year_id=school_year.id))
    if start_d and end_d and start_d > end_d:
        flash("La date de début ne peut pas être postérieure à la date de fin.", "danger")
        return redirect(url_for("settings.periods", year_id=school_year.id))

    duplicate_name = EvaluationPeriod.query.filter(
        EvaluationPeriod.school_year_id == school_year.id,
        EvaluationPeriod.name == name,
        EvaluationPeriod.id != period.id,
    ).first()
    if duplicate_name is not None:
        flash(f"Une autre période nommée « {name} » existe déjà pour l'année {school_year.label}.", "danger")
        return redirect(url_for("settings.periods", year_id=school_year.id))

    duplicate_order = EvaluationPeriod.query.filter(
        EvaluationPeriod.school_year_id == school_year.id,
        EvaluationPeriod.display_order == display_order,
        EvaluationPeriod.id != period.id,
    ).first()
    if duplicate_order is not None:
        flash(f"L'ordre d'affichage {display_order} est déjà utilisé par « {duplicate_order.name} ».", "danger")
        return redirect(url_for("settings.periods", year_id=school_year.id))

    old_summary = f"{period.name} [{period.short_code}] (ordre={period.display_order}, poids={period.annual_weight})"
    period.name = name
    period.short_code = short_code[:20]
    period.display_order = display_order
    period.annual_weight = weight
    period.start_date = start_d
    period.end_date = end_d

    record_audit_log(
        "PERIOD_UPDATE",
        f"Modification de la période ({school_year.label}) : {old_summary} -> {period.name} [{period.short_code}] (ordre={period.display_order}, poids={period.annual_weight})",
        module="evaluations",
        target_type="EvaluationPeriod",
        target_id=period.id,
        commit=True,
    )
    flash(f"Période « {period.name} » mise à jour.", "success")
    return redirect(url_for("settings.periods", year_id=school_year.id))
