"""Modules opérationnels complémentaires régis par RBAC : Portail Parent, Suivi Absences et Comptabilité."""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import io

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
    AcademicClass,
    AccountingEntry,
    ClassEnrollment,
    EstablishmentSettings,
    EvaluationPeriod,
    ParentStudentLink,
    StudentAbsenceRecord,
)
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import (
    any_permission_required,
    get_accessible_classes_query,
    get_current_establishment_id,
    get_parent_linked_student_ids,
    has_permission,
    permission_required,
    record_audit_log,
    user_can_access_establishment,
    user_can_view_class,
    user_can_view_enrollment,
)
from yelen_notes.services.calculation_service import (
    compute_annual_class_results,
    compute_period_class_results,
)
from yelen_notes.services.pdf_service import (
    generate_annual_bulletins_pdf,
    generate_periodic_bulletins_pdf,
)

operations_bp = Blueprint("operations", __name__)


@operations_bp.route("/espace-parent")
@any_permission_required("students.view", "bulletins.view", module="students")
def parent_portal():
    """Portail dédié Parent / Tuteur : affiche exclusivement les enfants rattachés au compte (§3.8, §7.C)."""
    linked_ids = get_parent_linked_student_ids(current_user)
    enrollments = (
        ClassEnrollment.query.filter(ClassEnrollment.student_id.in_(linked_ids))
        .order_by(ClassEnrollment.id.desc())
        .all()
        if linked_ids
        else []
    )
    cards = []
    for enr in enrollments:
        if not user_can_view_enrollment(current_user, enr):
            continue
        ac = enr.academic_class
        periods = (
            EvaluationPeriod.query.filter_by(school_year_id=ac.school_year_id)
            .order_by(EvaluationPeriod.display_order)
            .all()
        )
        ann = compute_annual_class_results(ac, periods)
        ann_row = ann.all_rows_by_enrollment.get(enr.id)
        absences = (
            StudentAbsenceRecord.query.filter_by(enrollment_id=enr.id)
            .order_by(StudentAbsenceRecord.event_date.desc())
            .limit(10)
            .all()
        )
        period_summaries = []
        for p in periods:
            p_res = compute_period_class_results(ac, p)
            s_res = p_res.all_rows_by_enrollment.get(enr.id)
            period_summaries.append(
                {
                    "period": p,
                    "student_result": s_res,
                    "general_scale": p_res.general_scale,
                }
            )
        cards.append(
            {
                "student": enr.student,
                "academic_class": ac,
                "enrollment": enr,
                "periods": periods,
                "period_summaries": period_summaries,
                "annual_row": ann_row,
                "absences": absences,
            }
        )

    return render_template(
        "operations/parent_portal.html",
        children_cards=cards,
        can_print_bulletin=has_permission(current_user, "bulletins.print"),
        can_view_absences=has_permission(current_user, "absences.view"),
    )


@operations_bp.route("/espace-parent/bulletin/<int:enrollment_id>")
@permission_required("bulletins.print", module="bulletins")
def parent_child_bulletin_pdf(enrollment_id: int):
    enrollment = db.get_or_404(ClassEnrollment, enrollment_id)
    if not user_can_view_enrollment(current_user, enrollment):
        record_audit_log(
            "ACCESS_DENIED",
            f"Accès refusé au bulletin de l'élève #{enrollment_id} pour '{current_user.username}'",
            module="bulletins",
            status="DENIED",
            commit=True,
        )
        abort(403)

    ac = enrollment.academic_class
    settings = EstablishmentSettings.get_singleton()
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    period_id = request.args.get("period_id", type=int)

    if period_id:
        period = db.get_or_404(EvaluationPeriod, period_id)
        if period.school_year_id != ac.school_year_id:
            abort(400)
        p_res = compute_period_class_results(ac, period)
        pdf_bytes = generate_periodic_bulletins_pdf(
            settings, paths, p_res, enrollment_ids=[enrollment.id]
        )
        fname = f"bulletin_{enrollment.student.last_name}_{period.short_code}.pdf"
    else:
        ann_res = compute_annual_class_results(ac)
        pdf_bytes = generate_annual_bulletins_pdf(
            settings, paths, ann_res, enrollment_ids=[enrollment.id]
        )
        fname = f"bulletin_annuel_{enrollment.student.last_name}_{ac.school_year.label}.pdf"

    record_audit_log(
        "BULLETIN_PARENT_DOWNLOAD",
        f"Téléchargement autorisé du bulletin de {enrollment.student.full_name}",
        module="bulletins",
        target_type="ClassEnrollment",
        target_id=enrollment.id,
        commit=True,
    )
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True, download_name=fname)


@operations_bp.route("/suivi-absences", methods=["GET", "POST"])
@permission_required("absences.view", module="absences")
def absences_manage():
    selected_year = get_selected_school_year()
    classes = (
        get_accessible_classes_query(current_user, selected_year.id).all()
        if selected_year
        else []
    )
    class_id = request.args.get("class_id", type=int) or request.form.get("class_id", type=int)
    selected_class = next((c for c in classes if c.id == class_id), None) if class_id else (classes[0] if classes else None)

    can_create = has_permission(current_user, "absences.create")
    can_update = has_permission(current_user, "absences.update")
    can_delete = has_permission(current_user, "absences.delete")

    if request.method == "POST":
        action = request.form.get("action") or "create"
        if action == "create":
            if not can_create or selected_class is None:
                abort(403)
            enr_id = request.form.get("enrollment_id", type=int)
            enr = db.session.get(ClassEnrollment, enr_id) if enr_id else None
            if enr is None or enr.class_id != selected_class.id or not user_can_view_class(current_user, selected_class.id):
                abort(403)
            try:
                ev_date = datetime.strptime(request.form.get("event_date") or "", "%Y-%m-%d").date()
            except ValueError:
                ev_date = date.today()
            ev_type = (request.form.get("event_type") or "ABSENCE").strip().upper()[:30]
            justified = request.form.get("justified") == "1"
            notes = (request.form.get("notes") or "").strip()[:255] or None

            rec = StudentAbsenceRecord(
                establishment_id=selected_class.establishment_id or 1,
                enrollment_id=enr.id,
                event_date=ev_date,
                event_type=ev_type,
                justified=justified,
                notes=notes,
                recorded_by_id=current_user.id,
            )
            db.session.add(rec)
            record_audit_log(
                "ABSENCE_CREATE",
                f"Enregistrement {ev_type} pour {enr.student.full_name} ({selected_class.name})",
                module="absences",
                establishment_id=rec.establishment_id,
                target_type="StudentAbsenceRecord",
                commit=True,
            )
            flash("Événement d'assiduité enregistré.", "success")
            return redirect(url_for("operations.absences_manage", class_id=selected_class.id))

        if action == "toggle_justified":
            if not can_update:
                abort(403)
            rec_id = request.form.get("record_id", type=int)
            rec = db.get_or_404(StudentAbsenceRecord, rec_id)
            if not user_can_access_establishment(current_user, rec.establishment_id):
                abort(403)
            rec.justified = not rec.justified
            record_audit_log(
                "ABSENCE_UPDATE",
                f"Justification de l'absence #{rec_id} passée à {rec.justified}",
                module="absences",
                establishment_id=rec.establishment_id,
                commit=True,
            )
            flash("Statut de justification mis à jour.", "success")
            return redirect(url_for("operations.absences_manage", class_id=selected_class.id if selected_class else None))

        if action == "delete":
            if not can_delete:
                abort(403)
            rec_id = request.form.get("record_id", type=int)
            rec = db.get_or_404(StudentAbsenceRecord, rec_id)
            if not user_can_access_establishment(current_user, rec.establishment_id):
                abort(403)
            db.session.delete(rec)
            record_audit_log(
                "ABSENCE_DELETE",
                f"Suppression de l'enregistrement d'absence #{rec_id}",
                module="absences",
                establishment_id=rec.establishment_id,
                commit=True,
            )
            flash("Enregistrement supprimé.", "info")
            return redirect(url_for("operations.absences_manage", class_id=selected_class.id if selected_class else None))

    records = []
    if selected_class:
        enr_ids = [e.id for e in selected_class.enrollments]
        if enr_ids:
            records = (
                StudentAbsenceRecord.query.filter(StudentAbsenceRecord.enrollment_id.in_(enr_ids))
                .order_by(StudentAbsenceRecord.event_date.desc(), StudentAbsenceRecord.id.desc())
                .limit(200)
                .all()
            )

    enrollments = (
        [e for e in selected_class.enrollments if e.is_active]
        if selected_class
        else []
    )

    return render_template(
        "operations/absences.html",
        selected_year=selected_year,
        classes=classes,
        selected_class=selected_class,
        enrollments=enrollments,
        records=records,
        today_iso=date.today().isoformat(),
        can_create=can_create,
        can_update=can_update,
        can_delete=can_delete,
    )


@operations_bp.route("/comptabilite", methods=["GET", "POST"])
@permission_required("accounting.view", module="accounting")
def accounting_manage():
    """Module Comptabilité isolé par établissement (sans exposition des notes/bulletins au comptable, §7.C)."""
    est_id = get_current_establishment_id(current_user)
    if not user_can_access_establishment(current_user, est_id):
        abort(403)

    can_create = has_permission(current_user, "accounting.create")
    can_validate = has_permission(current_user, "accounting.validate")
    can_delete = has_permission(current_user, "accounting.delete")
    can_export = has_permission(current_user, "accounting.export")

    if request.method == "POST":
        action = request.form.get("action") or "create"
        if action == "create":
            if not can_create:
                abort(403)
            entry_type = (request.form.get("entry_type") or "PAIEMENT").strip().upper()[:30]
            ref = (request.form.get("reference") or "").strip()[:60]
            label = (request.form.get("label") or "").strip()[:200]
            try:
                amount = Decimal((request.form.get("amount") or "0").replace(",", "."))
                entry_date = datetime.strptime(request.form.get("entry_date") or "", "%Y-%m-%d").date()
            except (InvalidOperation, ValueError):
                flash("Montant ou date comptable invalide.", "danger")
                return redirect(url_for("operations.accounting_manage"))
            if not ref or not label or amount <= 0:
                flash("Référence, libellé et montant positif sont obligatoires.", "danger")
                return redirect(url_for("operations.accounting_manage"))

            entry = AccountingEntry(
                establishment_id=est_id,
                entry_date=entry_date,
                entry_type=entry_type,
                reference=ref,
                label=label,
                amount=amount,
                is_validated=False,
                recorded_by_id=current_user.id,
            )
            db.session.add(entry)
            db.session.flush()
            record_audit_log(
                "ACCOUNTING_CREATE",
                f"Écriture comptable {entry_type} '{ref}' ({amount} FCFA)",
                module="accounting",
                establishment_id=est_id,
                target_type="AccountingEntry",
                target_id=entry.id,
                commit=True,
            )
            flash(f"Écriture « {ref} » enregistrée.", "success")
            return redirect(url_for("operations.accounting_manage"))

        if action == "validate":
            if not can_validate:
                abort(403)
            entry_id = request.form.get("entry_id", type=int)
            entry = db.get_or_404(AccountingEntry, entry_id)
            if entry.establishment_id != est_id:
                abort(403)
            entry.is_validated = True
            record_audit_log(
                "ACCOUNTING_VALIDATE",
                f"Validation de l'écriture comptable '{entry.reference}'",
                module="accounting",
                establishment_id=est_id,
                target_type="AccountingEntry",
                target_id=entry.id,
                commit=True,
            )
            flash(f"Écriture « {entry.reference} » validée.", "success")
            return redirect(url_for("operations.accounting_manage"))

        if action == "delete":
            if not can_delete:
                abort(403)
            entry_id = request.form.get("entry_id", type=int)
            entry = db.get_or_404(AccountingEntry, entry_id)
            if entry.establishment_id != est_id:
                abort(403)
            ref_name = entry.reference
            db.session.delete(entry)
            record_audit_log(
                "ACCOUNTING_DELETE",
                f"Suppression de l'écriture comptable '{ref_name}'",
                module="accounting",
                establishment_id=est_id,
                commit=True,
            )
            flash(f"Écriture « {ref_name} » supprimée.", "info")
            return redirect(url_for("operations.accounting_manage"))

    entries = (
        AccountingEntry.query.filter_by(establishment_id=est_id)
        .order_by(AccountingEntry.entry_date.desc(), AccountingEntry.id.desc())
        .limit(200)
        .all()
    )
    total_recettes = sum((e.amount for e in entries if e.entry_type != "DEPENSE"), Decimal("0.00"))
    total_depenses = sum((e.amount for e in entries if e.entry_type == "DEPENSE"), Decimal("0.00"))

    return render_template(
        "operations/accounting.html",
        entries=entries,
        total_recettes=total_recettes,
        total_depenses=total_depenses,
        solde=(total_recettes - total_depenses),
        today_iso=date.today().isoformat(),
        can_create=can_create,
        can_validate=can_validate,
        can_delete=can_delete,
        can_export=can_export,
    )


@operations_bp.route("/comptabilite/exporter")
@permission_required("accounting.export", module="accounting")
def accounting_export_csv():
    est_id = get_current_establishment_id(current_user)
    if not user_can_access_establishment(current_user, est_id):
        abort(403)
    entries = (
        AccountingEntry.query.filter_by(establishment_id=est_id)
        .order_by(AccountingEntry.entry_date.desc(), AccountingEntry.id.desc())
        .all()
    )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Date", "Type", "Reference", "Libelle", "Montant_FCFA", "Statut"])
    for e in entries:
        writer.writerow(
            [
                e.entry_date.isoformat(),
                e.entry_type,
                e.reference,
                e.label,
                str(e.amount),
                "VALIDE" if e.is_validated else "BROUILLON",
            ]
        )
    record_audit_log(
        "ACCOUNTING_EXPORT",
        f"Export CSV des écritures comptables ({len(entries)} lignes)",
        module="accounting",
        establishment_id=est_id,
        commit=True,
    )
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="etat_comptable_yelen_notes.csv",
    )
