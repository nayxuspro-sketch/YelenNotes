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
    session,
    url_for,
)
from flask_login import current_user, login_required

from yelen_notes.blueprints.dashboard import get_selected_school_year
from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_ADMIN,
    ROLE_DIRECTEUR,
    STATUS_OUVERTE,
    AcademicClass,
    AuditLog,
    EstablishmentSettings,
    EvaluationPeriod,
    SchoolYear,
)
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import (
    get_accessible_classes_query,
    get_accessible_years_query,
    has_permission,
    is_strictly_scoped_parent,
    permission_required,
    record_audit_log,
    user_can_access_establishment,
    user_can_view_class,
)
from yelen_notes.services.calculation_service import (
    compute_annual_class_results,
    compute_end_of_year_proposition_report,
    compute_period_class_results,
    compute_synoptic_period_report,
    format_compact_decimal_fr,
    format_decimal_fr,
)
from yelen_notes.services.excel_service import (
    export_annual_ranking_to_excel,
    export_end_of_year_proposition_to_excel,
    export_period_ranking_to_excel,
    export_period_releve_to_excel,
    export_synoptic_period_to_excel,
)
from yelen_notes.services.pdf_service import (
    generate_annual_bulletins_pdf,
    generate_end_of_year_proposition_pdf,
    generate_merit_ranking_pdf,
    generate_period_releve_pdf,
    generate_periodic_bulletins_pdf,
    generate_synoptic_period_pdf,
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
@permission_required("bulletins.view", module="bulletins")
def period_releve():
    if is_strictly_scoped_parent(current_user):
        return redirect(url_for("operations.parent_portal"))
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
@permission_required("bulletins.print", module="bulletins")
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
@permission_required("bulletins.export", module="bulletins")
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
@permission_required("bulletins.view", module="bulletins")
def merit_ranking():
    if is_strictly_scoped_parent(current_user):
        return redirect(url_for("operations.parent_portal"))
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
@permission_required("bulletins.print", module="bulletins")
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
@permission_required("bulletins.export", module="bulletins")
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
@permission_required("bulletins.view", module="bulletins")
def bulletins_center():
    if is_strictly_scoped_parent(current_user):
        return redirect(url_for("operations.parent_portal"))
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
@permission_required("bulletins.print", module="bulletins")
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


@reports_bp.route("/resultats/tableau-synoptique")
@permission_required("bulletins.view", module="bulletins")
def synoptic_report():
    """Fiche statistique « TABLEAU SYNOPTIQUE DES RÉSULTATS SCOLAIRES » par période d'évaluation."""
    from datetime import datetime

    if is_strictly_scoped_parent(current_user):
        return redirect(url_for("operations.parent_portal"))

    # Permettre la sélection directe de l'année scolaire sur la fiche (§2 & §6.1)
    requested_year_id = request.args.get("year_id", type=int)
    if requested_year_id is not None:
        yr_obj = db.session.get(SchoolYear, requested_year_id)
        if yr_obj is not None and user_can_access_establishment(current_user, yr_obj.establishment_id):
            session["selected_school_year_id"] = yr_obj.id

    all_years = get_accessible_years_query(current_user).all()
    selected_year, classes, periods, selected_class, selected_period = _select_context()
    subject_id = request.args.get("subject_id", type=int)
    orientation = request.args.get("orientation", "landscape")
    if orientation not in ("landscape", "portrait"):
        orientation = "landscape"

    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    has_logo = bool(
        settings.logo_filename and (paths.logos_dir / settings.logo_filename).is_file()
    )

    synoptic = None
    if selected_class and selected_period:
        synoptic = compute_synoptic_period_report(
            selected_class, selected_period, subject_id=subject_id
        )

    return render_template(
        "reports/synoptic.html",
        settings=settings,
        has_logo=has_logo,
        all_years=all_years,
        selected_year=selected_year,
        classes=classes,
        periods=periods,
        selected_class=selected_class,
        selected_period=selected_period,
        selected_subject_id=synoptic.selected_subject_id if synoptic else None,
        orientation=orientation,
        synoptic=synoptic,
        generated_at=datetime.now(),
    )


@reports_bp.route("/resultats/tableau-synoptique/pdf")
@permission_required("bulletins.print", module="bulletins")
def synoptic_report_pdf():
    class_id = request.args.get("class_id", type=int)
    period_id = request.args.get("period_id", type=int)
    subject_id = request.args.get("subject_id", type=int)
    orientation = request.args.get("orientation", "landscape")
    inline = request.args.get("inline") == "1"

    ac = db.get_or_404(AcademicClass, class_id)
    period = db.get_or_404(EvaluationPeriod, period_id)
    if not user_can_view_class(current_user, ac.id) or ac.school_year_id != period.school_year_id:
        abort(403)

    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    synoptic = compute_synoptic_period_report(ac, period, subject_id=subject_id)
    pdf_bytes = generate_synoptic_period_pdf(
        settings,
        paths,
        synoptic,
        use_landscape=(orientation != "portrait"),
    )

    fname = f"tableau_synoptique_{ac.name.replace(' ', '_')}_{period.short_code}_{ac.school_year.label}.pdf"
    _archive_document(paths.releves_dir, fname, pdf_bytes)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=(not inline),
        download_name=fname,
    )


@reports_bp.route("/resultats/tableau-synoptique/excel")
@permission_required("bulletins.export", module="bulletins")
def synoptic_report_excel():
    class_id = request.args.get("class_id", type=int)
    period_id = request.args.get("period_id", type=int)
    subject_id = request.args.get("subject_id", type=int)

    ac = db.get_or_404(AcademicClass, class_id)
    period = db.get_or_404(EvaluationPeriod, period_id)
    if not user_can_view_class(current_user, ac.id) or ac.school_year_id != period.school_year_id:
        abort(403)

    settings = EstablishmentSettings.get_singleton()
    synoptic = compute_synoptic_period_report(ac, period, subject_id=subject_id)
    xlsx_bytes = export_synoptic_period_to_excel(settings, synoptic)
    fname = f"tableau_synoptique_{ac.name.replace(' ', '_')}_{period.short_code}_{ac.school_year.label}.xlsx"
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=fname,
    )


def _build_proposition_trace_payload(proposition) -> dict:
    """Construit l'empreinte détaillée des résultats ayant servi à établir la Proposition de fin d'année."""
    ac = proposition.academic_class
    ann = proposition.annual_results
    return {
        "school_year": ac.school_year.label,
        "class_id": ac.id,
        "class_name": ac.name,
        "general_scale": format_compact_decimal_fr(ann.general_scale),
        "pass_threshold": format_decimal_fr(proposition.pass_threshold, 2, ann.rounding_mode),
        "annual_mode": ann.annual_mode,
        "total_students": proposition.total_students,
        "passe_count": proposition.passe_count,
        "redouble_count": proposition.redouble_count,
        "undetermined_count": proposition.undetermined_count,
        "period_headers": [s.header_label for s in proposition.period_slots],
        "rows": [
            {
                "order": r.order_number,
                "enrollment_id": r.enrollment.id,
                "student": r.full_name,
                "periods": r.slot_displays,
                "annual_average": r.display_annual_average_2dec,
                "rank": r.display_rank,
                "auto_proposition": r.auto_proposition,
                "validated_decision": r.validated_decision,
            }
            for r in proposition.rows
        ],
    }


@reports_bp.route("/resultats/proposition-fin-annee")
@permission_required("proposition.view", module="proposition")
def end_of_year_proposition():
    """Fiche annuelle « PROPOSITION DE FIN D'ANNÉE » (consultation écran, impression et gestion des décisions)."""
    from datetime import datetime

    requested_year_id = request.args.get("year_id", type=int)
    if requested_year_id is not None:
        yr_obj = db.session.get(SchoolYear, requested_year_id)
        if yr_obj is not None and user_can_access_establishment(current_user, yr_obj.establishment_id):
            session["selected_school_year_id"] = yr_obj.id

    all_years = get_accessible_years_query(current_user).all()
    selected_year, classes, periods, selected_class, _ = _select_context()

    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    has_logo = bool(
        settings.logo_filename and (paths.logos_dir / settings.logo_filename).is_file()
    )

    proposition = None
    trace_logs = []
    can_validate_decisions = False

    if selected_class:
        proposition = compute_end_of_year_proposition_report(selected_class, periods)
        can_validate_decisions = (
            has_permission(current_user, "proposition.validate")
            and not selected_class.school_year.is_closed
        )
        trace_logs = (
            AuditLog.query.filter(
                AuditLog.target_type == "AcademicClass",
                AuditLog.target_id == str(selected_class.id),
                AuditLog.action.in_(
                    [
                        "PROPOSITION_FIN_ANNEE_PDF",
                        "PROPOSITION_FIN_ANNEE_EXCEL",
                        "PROPOSITION_FIN_ANNEE_TRACE",
                        "PROPOSITION_FIN_ANNEE_VALIDATION",
                    ]
                ),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(8)
            .all()
        )

    return render_template(
        "reports/proposition_fin_annee.html",
        settings=settings,
        has_logo=has_logo,
        all_years=all_years,
        selected_year=selected_year,
        classes=classes,
        selected_class=selected_class,
        proposition=proposition,
        can_validate_decisions=can_validate_decisions,
        trace_logs=trace_logs,
        generated_at=datetime.now(),
    )


@reports_bp.route("/resultats/proposition-fin-annee/archiver-trace", methods=["POST"])
@permission_required("proposition.create", module="proposition")
def archive_proposition_trace():
    """Enregistre explicitement une trace horodatée des résultats utilisés pour la fiche (§6.7)."""
    class_id = request.form.get("class_id", type=int)
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)

    proposition = compute_end_of_year_proposition_report(ac)
    record_audit_log(
        "PROPOSITION_FIN_ANNEE_TRACE",
        (
            f"Trace officielle Proposition de fin d'année — {ac.name} ({ac.school_year.label}) : "
            f"{proposition.passe_count} Passe, {proposition.redouble_count} Redouble, "
            f"{proposition.undetermined_count} Non déterminé (effectif {proposition.total_students})"
        ),
        target_type="AcademicClass",
        target_id=ac.id,
        details=_build_proposition_trace_payload(proposition),
        commit=True,
    )
    flash("La trace horodatée des résultats de la fiche de proposition a été enregistrée.", "success")
    return redirect(url_for("reports.end_of_year_proposition", class_id=ac.id))


@reports_bp.route("/resultats/proposition-fin-annee/valider-decisions", methods=["POST"])
@permission_required("proposition.validate", module="proposition")
def validate_end_of_year_decisions():
    """Permet à la direction de modifier et valider les décisions définitives de fin d'année (§8)."""
    class_id = request.form.get("class_id", type=int)
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)
    if ac.school_year.is_closed:
        flash("Impossible de modifier les décisions d'une année scolaire clôturée.", "warning")
        return redirect(url_for("reports.end_of_year_proposition", class_id=ac.id))

    mode_action = request.form.get("mode_action", "save_custom")
    proposition = compute_end_of_year_proposition_report(ac)

    updated_count = 0
    for row in proposition.rows:
        enr = row.enrollment
        if mode_action == "apply_auto":
            new_dec = row.auto_proposition if row.auto_proposition != "Non déterminé" else ""
        else:
            new_dec = (request.form.get(f"decision_{enr.id}") or "").strip()[:40]

        if (enr.annual_decision or "") != new_dec:
            enr.annual_decision = new_dec or None
            updated_count += 1

    # Recalculer la charge utile avec les décisions mises à jour pour archivage fidèle
    db.session.flush()
    refreshed = compute_end_of_year_proposition_report(ac)
    record_audit_log(
        "PROPOSITION_FIN_ANNEE_VALIDATION",
        (
            f"Validation des décisions de fin d'année ({updated_count} mise(s) à jour) — "
            f"{ac.name} ({ac.school_year.label})"
        ),
        target_type="AcademicClass",
        target_id=ac.id,
        details=_build_proposition_trace_payload(refreshed),
        commit=True,
    )
    flash(
        f"Décisions définitives enregistrées ({updated_count} élève(s) mis à jour) et trace conservée.",
        "success",
    )
    return redirect(url_for("reports.end_of_year_proposition", class_id=ac.id))


@reports_bp.route("/resultats/proposition-fin-annee/pdf")
@permission_required("proposition.print", module="proposition")
def end_of_year_proposition_pdf():
    from datetime import datetime

    class_id = request.args.get("class_id", type=int)
    inline = request.args.get("inline") == "1"

    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)

    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    proposition = compute_end_of_year_proposition_report(ac)
    pdf_bytes = generate_end_of_year_proposition_pdf(settings, paths, proposition)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"proposition_fin_annee_{ac.name.replace(' ', '_')}_{ac.school_year.label}.pdf"
    archived_name = f"proposition_fin_annee_{ac.name.replace(' ', '_')}_{ac.school_year.label}_{stamp}.pdf"
    _archive_document(paths.releves_dir, archived_name, pdf_bytes)

    record_audit_log(
        "PROPOSITION_FIN_ANNEE_PDF",
        (
            f"Édition PDF Proposition de fin d'année — {ac.name} ({ac.school_year.label}) : "
            f"{proposition.passe_count} Passe, {proposition.redouble_count} Redouble, "
            f"{proposition.undetermined_count} Non déterminé"
        ),
        target_type="AcademicClass",
        target_id=ac.id,
        details=_build_proposition_trace_payload(proposition),
        commit=True,
    )

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=(not inline),
        download_name=fname,
    )


@reports_bp.route("/resultats/proposition-fin-annee/excel")
@permission_required("proposition.export", module="proposition")
def end_of_year_proposition_excel():
    class_id = request.args.get("class_id", type=int)
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)

    settings = EstablishmentSettings.get_singleton()
    proposition = compute_end_of_year_proposition_report(ac)
    xlsx_bytes = export_end_of_year_proposition_to_excel(settings, proposition)
    fname = f"proposition_fin_annee_{ac.name.replace(' ', '_')}_{ac.school_year.label}.xlsx"

    record_audit_log(
        "PROPOSITION_FIN_ANNEE_EXCEL",
        f"Export Excel Proposition de fin d'année — {ac.name} ({ac.school_year.label})",
        target_type="AcademicClass",
        target_id=ac.id,
        details=_build_proposition_trace_payload(proposition),
        commit=True,
    )

    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=fname,
    )
