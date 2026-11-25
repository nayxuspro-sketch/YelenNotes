"""Relevés de notes, classements par ordre de mérite et bulletins PDF périodiques & annuels (§10, §11, §12 CdC)."""

from __future__ import annotations

import io
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from yelen_notes.blueprints.dashboard import get_selected_school_year
from yelen_notes.extensions import db
from yelen_notes.models import (
    STATUS_OUVERTE,
    AcademicClass,
    EstablishmentSettings,
    EvaluationPeriod,
)
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import get_accessible_classes_query, user_can_view_class
from yelen_notes.services.calculation_service import (
    compute_annual_class_results,
    compute_period_class_results,
)
from yelen_notes.services.excel_service import (
    export_annual_ranking_to_excel,
    export_period_ranking_to_excel,
    export_period_releve_to_excel,
)
from yelen_notes.services.pdf_service import (
    generate_annual_bulletins_pdf,
    generate_merit_ranking_pdf,
    generate_period_releve_pdf,
    generate_periodic_bulletins_pdf,
)

reports_bp = Blueprint("reports", __name__)


def _select_context():
    selected_year = get_selected_school_year()
    classes = (
        get_accessible_classes_query(current_user, selected_year.id).all()
        if selected_year
        else []
    )
    periods = (
        EvaluationPeriod.query.filter_by(school_year_id=selected_year.id)
        .order_by(EvaluationPeriod.display_order)
        .all()
        if selected_year
        else []
    )
    class_id = request.args.get("class_id", type=int) or request.form.get("class_id", type=int)
    period_id = request.args.get("period_id", type=int) or request.form.get("period_id", type=int)

    selected_class = next((c for c in classes if c.id == class_id), None) if class_id else None
    if selected_class is None and classes:
        selected_class = classes[0]

    selected_period = next((p for p in periods if p.id == period_id), None) if period_id else None
    if selected_period is None and periods:
        open_p = [p for p in periods if p.status == STATUS_OUVERTE]
        selected_period = open_p[0] if open_p else periods[0]

    return selected_year, classes, periods, selected_class, selected_period


def _archive_document(folder: Path, filename: str, content: bytes) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(content)
    except OSError:
        pass


@reports_bp.route("/resultats/releve")
@login_required
def period_releve():
    selected_year, classes, periods, selected_class, selected_period = _select_context()
    settings = EstablishmentSettings.get_singleton()
    results = None
    if selected_class and selected_period:
        results = compute_period_class_results(selected_class, selected_period)

    return render_template(
        "reports/releve.html",
        settings=settings,
        selected_year=selected_year,
        classes=classes,
        periods=periods,
        selected_class=selected_class,
        selected_period=selected_period,
        results=results,
    )


@reports_bp.route("/resultats/releve/pdf")
@login_required
def period_releve_pdf():
    class_id = request.args.get("class_id", type=int)
    period_id = request.args.get("period_id", type=int)
    inline = request.args.get("inline") == "1"

    ac = db.get_or_404(AcademicClass, class_id)
    period = db.get_or_404(EvaluationPeriod, period_id)
    if not user_can_view_class(current_user, ac.id) or ac.school_year_id != period.school_year_id:
        abort(403)

    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    results = compute_period_class_results(ac, period)
    pdf_bytes = generate_period_releve_pdf(settings, paths, results)

    fname = f"releve_{ac.name.replace(' ', '_')}_{period.short_code}_{ac.school_year.label}.pdf"
    _archive_document(paths.releves_dir, fname, pdf_bytes)

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=(not inline),
        download_name=fname,
    )


@reports_bp.route("/resultats/releve/excel")
@login_required
def period_releve_excel():
    class_id = request.args.get("class_id", type=int)
    period_id = request.args.get("period_id", type=int)

    ac = db.get_or_404(AcademicClass, class_id)
    period = db.get_or_404(EvaluationPeriod, period_id)
    if not user_can_view_class(current_user, ac.id) or ac.school_year_id != period.school_year_id:
        abort(403)

    settings = EstablishmentSettings.get_singleton()
    results = compute_period_class_results(ac, period)
    xlsx_bytes = export_period_releve_to_excel(settings, results)
    fname = f"releve_{ac.name.replace(' ', '_')}_{period.short_code}_{ac.school_year.label}.xlsx"

    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=fname,
    )


@reports_bp.route("/resultats/classement")
@login_required
def merit_ranking():
    selected_year, classes, periods, selected_class, selected_period = _select_context()
    scope = request.args.get("scope", "PERIODE")
    if scope not in ("PERIODE", "ANNUEL"):
        scope = "PERIODE"

    settings = EstablishmentSettings.get_singleton()
    period_results = None
    annual_results = None
    if selected_class:
        if scope == "ANNUEL":
            annual_results = compute_annual_class_results(selected_class, periods)
        elif selected_period:
            period_results = compute_period_class_results(selected_class, selected_period)

    return render_template(
        "reports/ranking.html",
        settings=settings,
        selected_year=selected_year,
        classes=classes,
        periods=periods,
        selected_class=selected_class,
        selected_period=selected_period,
        scope=scope,
        period_results=period_results,
        annual_results=annual_results,
    )


@reports_bp.route("/resultats/classement/pdf")
@login_required
def merit_ranking_pdf():
    class_id = request.args.get("class_id", type=int)
    period_id = request.args.get("period_id", type=int)
    scope = request.args.get("scope", "PERIODE")
    inline = request.args.get("inline") == "1"

    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)

    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]

    if scope == "ANNUEL":
        res = compute_annual_class_results(ac)
        pdf_bytes = generate_merit_ranking_pdf(settings, paths, res, is_annual=True)
        fname = f"classement_annuel_{ac.name.replace(' ', '_')}_{ac.school_year.label}.pdf"
    else:
        period = db.get_or_404(EvaluationPeriod, period_id)
        if period.school_year_id != ac.school_year_id:
            abort(403)
        res = compute_period_class_results(ac, period)
        pdf_bytes = generate_merit_ranking_pdf(settings, paths, res, is_annual=False)
        fname = f"classement_{ac.name.replace(' ', '_')}_{period.short_code}_{ac.school_year.label}.pdf"

    _archive_document(paths.releves_dir, fname, pdf_bytes)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=(not inline),
        download_name=fname,
    )


@reports_bp.route("/resultats/classement/excel")
@login_required
def merit_ranking_excel():
    class_id = request.args.get("class_id", type=int)
    period_id = request.args.get("period_id", type=int)
    scope = request.args.get("scope", "PERIODE")

    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)

    settings = EstablishmentSettings.get_singleton()
    if scope == "ANNUEL":
        ann_res = compute_annual_class_results(ac)
        xlsx_bytes = export_annual_ranking_to_excel(settings, ann_res)
        fname = f"classement_annuel_{ac.name.replace(' ', '_')}_{ac.school_year.label}.xlsx"
    else:
        period = db.get_or_404(EvaluationPeriod, period_id)
        p_res = compute_period_class_results(ac, period)
        xlsx_bytes = export_period_ranking_to_excel(settings, p_res)
        fname = f"classement_{ac.name.replace(' ', '_')}_{period.short_code}_{ac.school_year.label}.xlsx"

    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=fname,
    )


@reports_bp.route("/resultats/bulletins")
@login_required
def bulletins_center():
    selected_year, classes, periods, selected_class, selected_period = _select_context()
    scope = request.args.get("scope", "PERIODE")
    if scope not in ("PERIODE", "ANNUEL"):
        scope = "PERIODE"

    settings = EstablishmentSettings.get_singleton()
    period_results = None
    annual_results = None
    preview_enrollment_id = request.args.get("preview_id", type=int)

    if selected_class:
        if scope == "ANNUEL":
            annual_results = compute_annual_class_results(selected_class, periods)
        elif selected_period:
            period_results = compute_period_class_results(selected_class, selected_period)

    return render_template(
        "reports/bulletins.html",
        settings=settings,
        selected_year=selected_year,
        classes=classes,
        periods=periods,
        selected_class=selected_class,
        selected_period=selected_period,
        scope=scope,
        period_results=period_results,
        annual_results=annual_results,
        preview_enrollment_id=preview_enrollment_id,
    )


@reports_bp.route("/resultats/bulletins/pdf", methods=["GET", "POST"])
@login_required
def bulletins_pdf():
    class_id = request.values.get("class_id", type=int)
    period_id = request.values.get("period_id", type=int)
    scope = request.values.get("scope", "PERIODE")
    inline = request.values.get("inline") == "1"

    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)

    # Sélection : soit 1 élève (`enrollment_id`), soit une sélection cochée (`enrollment_ids[]`), soit toute la classe
    single_id = request.values.get("enrollment_id", type=int)
    multi_ids_raw = request.values.getlist("enrollment_ids[]") or request.values.getlist("enrollment_ids")
    selected_ids: list[int] | None = None

    if single_id:
        selected_ids = [single_id]
    elif multi_ids_raw:
        selected_ids = [int(x) for x in multi_ids_raw if str(x).isdigit()]
    elif request.method == "POST" and request.form.get("selection_mode") == "selected":
        flash("Veuillez cocher au moins un élève pour générer une sélection de bulletins.", "warning")
        return redirect(
            url_for("reports.bulletins_center", class_id=ac.id, period_id=period_id, scope=scope)
        )

    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]

    if scope == "ANNUEL":
        ann_res = compute_annual_class_results(ac)
        pdf_bytes = generate_annual_bulletins_pdf(settings, paths, ann_res, enrollment_ids=selected_ids)
        suffix = f"eleve_{single_id}" if single_id else "classe_entiere"
        fname = f"bulletins_annuels_{ac.name.replace(' ', '_')}_{ac.school_year.label}_{suffix}.pdf"
    else:
        period = db.get_or_404(EvaluationPeriod, period_id)
        if period.school_year_id != ac.school_year_id:
            abort(403)
        p_res = compute_period_class_results(ac, period)
        pdf_bytes = generate_periodic_bulletins_pdf(settings, paths, p_res, enrollment_ids=selected_ids)
        suffix = f"eleve_{single_id}" if single_id else "classe_entiere"
        fname = f"bulletins_{ac.name.replace(' ', '_')}_{period.short_code}_{ac.school_year.label}_{suffix}.pdf"

    _archive_document(paths.bulletins_dir, fname, pdf_bytes)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=(not inline),
        download_name=fname,
    )
