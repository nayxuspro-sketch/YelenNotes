"""Saisie individuelle/collective des notes, autosave AJAX CSRF/409, observations et historique (§8 CdC)."""

from __future__ import annotations

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from yelen_notes.blueprints.dashboard import get_selected_school_year
from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_ADMIN,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    STATUS_OUVERTE,
    AcademicClass,
    ClassEnrollment,
    EvaluationPeriod,
    Grade,
    GradeHistory,
    StudentPeriodObservation,
    Subject,
)
from yelen_notes.security import (
    get_accessible_classes_query,
    record_audit_log,
    roles_required,
    user_can_edit_grades,
    user_can_view_class,
)
from yelen_notes.services.calculation_service import compute_period_class_results
from yelen_notes.services.grade_service import (
    GradeConflictError,
    GradeValidationError,
    format_grade_raw_input,
    save_student_observation,
    upsert_single_grade,
)

grades_bp = Blueprint("grades", __name__)


def _resolve_class_and_period():
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
        open_periods = [p for p in periods if p.status == STATUS_OUVERTE]
        selected_period = open_periods[0] if open_periods else periods[0]

    return selected_year, classes, periods, selected_class, selected_period


@grades_bp.route("/notes")
@login_required
def grade_entry():
    selected_year, classes, periods, selected_class, selected_period = _resolve_class_and_period()
    subject_filter_id = request.args.get("subject_id", type=int)
    only_missing = request.args.get("missing") == "1"
    search_q = (request.args.get("q") or "").strip().lower()

    can_edit = False
    edit_lock_reason = ""
    results = None
    all_subjects: list[Subject] = []
    displayed_subjects: list[Subject] = []
    grade_inputs: dict[tuple[int, int], tuple[str, int]] = {}
    displayed_rows = []

    if selected_class and selected_period:
        can_edit, edit_lock_reason = user_can_edit_grades(
            current_user, selected_class, selected_period
        )
        results = compute_period_class_results(selected_class, selected_period)
        all_subjects = results.subjects
        if subject_filter_id:
            displayed_subjects = [s for s in all_subjects if s.id == subject_filter_id]
        else:
            displayed_subjects = list(all_subjects)

        enr_ids = [r.enrollment.id for r in results.active_rows]
        subj_ids = [s.id for s in all_subjects]
        if enr_ids and subj_ids:
            grades_list = Grade.query.filter(
                Grade.period_id == selected_period.id,
                Grade.enrollment_id.in_(enr_ids),
                Grade.subject_id.in_(subj_ids),
            ).all()
            for g in grades_list:
                grade_inputs[(g.enrollment_id, g.subject_id)] = (
                    format_grade_raw_input(g),
                    g.version,
                )

        for row in results.active_rows:
            if search_q:
                st = row.enrollment.student
                hay = f"{row.enrollment.roll_number} {st.last_name} {st.first_names} {st.unique_code}".lower()
                if search_q not in hay:
                    continue
            if only_missing:
                if subject_filter_id:
                    cell = row.subject_cells.get(subject_filter_id)
                    if cell and cell.status != "NON_SAISIE":
                        continue
                elif row.missing_count == 0:
                    continue
            displayed_rows.append(row)

    return render_template(
        "grades/entry.html",
        selected_year=selected_year,
        classes=classes,
        periods=periods,
        selected_class=selected_class,
        selected_period=selected_period,
        all_subjects=all_subjects,
        displayed_subjects=displayed_subjects,
        subject_filter_id=subject_filter_id,
        only_missing=only_missing,
        search_q=search_q,
        results=results,
        displayed_rows=displayed_rows,
        grade_inputs=grade_inputs,
        can_edit=can_edit,
        edit_lock_reason=edit_lock_reason,
    )


@grades_bp.route("/notes/api/cellule", methods=["POST"])
@roles_required(ROLE_ADMIN, ROLE_DIRECTEUR, ROLE_ENSEIGNANT)
def api_save_cell():
    """API JSON d'enregistrement unitaire rapide avec contrôle CSRF, barème et conflit multiposte."""
    payload = request.get_json(silent=True) or {}
    enrollment_id = payload.get("enrollment_id")
    period_id = payload.get("period_id")
    subject_id = payload.get("subject_id")
    raw_value = payload.get("value", "")
    expected_version = payload.get("version")
    force_overwrite = bool(payload.get("force", False))

    try:
        enrollment = db.session.get(ClassEnrollment, int(enrollment_id))
        period = db.session.get(EvaluationPeriod, int(period_id))
        subject = db.session.get(Subject, int(subject_id))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Identifiants de cellule invalides."}), 400

    if not enrollment or not period or not subject:
        return jsonify({"ok": False, "error": "Élève, période ou matière introuvable."}), 404

    allowed, reason = user_can_edit_grades(current_user, enrollment.academic_class, period)
    if not allowed:
        return jsonify({"ok": False, "error": reason}), 403

    exp_ver_int = None
    if expected_version not in (None, ""):
        try:
            exp_ver_int = int(expected_version)
        except (TypeError, ValueError):
            exp_ver_int = None

    try:
        grade, _ = upsert_single_grade(
            enrollment=enrollment,
            period=period,
            subject=subject,
            raw_input=str(raw_value),
            actor=current_user,
            expected_version=exp_ver_int,
            force_overwrite=force_overwrite,
            commit=True,
        )
    except GradeConflictError as conflict:
        db.session.rollback()
        return (
            jsonify(
                {
                    "ok": False,
                    "conflict": True,
                    "error": str(conflict),
                    "current_version": conflict.current_version,
                    "current_display": conflict.current_display,
                    "updated_by": conflict.updated_by,
                }
            ),
            409,
        )
    except GradeValidationError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400

    # Recalcul de la ligne de l'élève pour rafraîchir instantanément son état / moyenne provisoire
    results = compute_period_class_results(enrollment.academic_class, period)
    st_row = results.all_rows_by_enrollment.get(enrollment.id)
    return jsonify(
        {
            "ok": True,
            "normalized_input": format_grade_raw_input(grade),
            "status": grade.status,
            "version": grade.version,
            "student_summary": {
                "is_complete": st_row.is_complete if st_row else False,
                "missing_count": st_row.missing_count if st_row else 0,
                "display_average": st_row.display_average if st_row else "—",
                "rank_label": st_row.rank_label if st_row else "—",
                "unranked_reason": st_row.unranked_reason if st_row else "",
            },
        }
    )


@grades_bp.route("/notes/enregistrer-grille", methods=["POST"])
@roles_required(ROLE_ADMIN, ROLE_DIRECTEUR, ROLE_ENSEIGNANT)
def save_grid_form():
    """Enregistrement groupé explicite du formulaire complet de notes (§8.2 CdC)."""
    class_id = request.form.get("class_id", type=int)
    period_id = request.form.get("period_id", type=int)
    subject_filter_id = request.form.get("subject_id", type=int)

    ac = db.get_or_404(AcademicClass, class_id)
    period = db.get_or_404(EvaluationPeriod, period_id)

    allowed, reason = user_can_edit_grades(current_user, ac, period)
    if not allowed:
        flash(reason, "danger")
        return redirect(url_for("grades.grade_entry", class_id=ac.id, period_id=period.id))

    subjects = Subject.query.filter_by(class_id=ac.id, is_active=True).all()
    subj_map = {s.id: s for s in subjects}
    enr_map = {e.id: e for e in ac.enrollments if e.is_active}

    changed_count = 0
    errors: list[str] = []
    conflicts: list[str] = []

    for key, raw_val in request.form.items():
        if not key.startswith("cell_"):
            continue
        parts = key.split("_")
        if len(parts) != 3 or not (parts[1].isdigit() and parts[2].isdigit()):
            continue
        enr_id = int(parts[1])
        subj_id = int(parts[2])
        enr = enr_map.get(enr_id)
        subj = subj_map.get(subj_id)
        if not enr or not subj:
            continue

        ver_raw = request.form.get(f"ver_{enr_id}_{subj_id}", "")
        exp_ver = int(ver_raw) if ver_raw.isdigit() else None
        try:
            _, changed = upsert_single_grade(
                enrollment=enr,
                period=period,
                subject=subj,
                raw_input=raw_val,
                actor=current_user,
                expected_version=exp_ver,
                commit=False,
            )
            if changed:
                changed_count += 1
        except GradeConflictError as cf:
            conflicts.append(f"{enr.student.full_name} ({subj.name}) : {cf}")
        except GradeValidationError as ve:
            errors.append(f"{enr.student.full_name} — {ve}")

    if errors or conflicts:
        db.session.rollback()
        for err in errors[:10]:
            flash(err, "danger")
        for cf_msg in conflicts[:10]:
            flash(cf_msg, "warning")
    else:
        if changed_count > 0:
            record_audit_log(
                "GRADE_BATCH_SAVE",
                f"Enregistrement groupé de {changed_count} note(s) dans {ac.name} ({period.name})",
                target_type="AcademicClass",
                target_id=ac.id,
                commit=False,
            )
        db.session.commit()
        flash(f"Enregistrement terminé ({changed_count} cellule(s) modifiée(s)).", "success")

    return redirect(
        url_for(
            "grades.grade_entry",
            class_id=ac.id,
            period_id=period.id,
            subject_id=subject_filter_id or None,
        )
    )


@grades_bp.route("/notes/observations", methods=["GET", "POST"])
@login_required
def period_observations():
    selected_year, classes, periods, selected_class, selected_period = _resolve_class_and_period()
    scope = request.args.get("scope") or request.form.get("scope") or "PERIODE"
    if scope not in ("PERIODE", "ANNUEL"):
        scope = "PERIODE"

    can_edit = False
    if selected_class:
        if scope == "ANNUEL":
            can_edit = (
                current_user.role in (ROLE_ADMIN, ROLE_DIRECTEUR)
                and not selected_class.school_year.is_closed
            ) or (
                current_user.role == ROLE_ENSEIGNANT
                and user_can_view_class(current_user, selected_class.id)
                and not selected_class.school_year.is_closed
            )
        elif selected_period:
            can_edit, _ = user_can_edit_grades(current_user, selected_class, selected_period)

    if request.method == "POST" and selected_class:
        if not can_edit:
            abort(403)
        for enr in selected_class.enrollments:
            if not enr.is_active:
                continue
            t_obs = request.form.get(f"t_obs_{enr.id}")
            d_obs = (
                request.form.get(f"d_obs_{enr.id}")
                if current_user.role in (ROLE_ADMIN, ROLE_DIRECTEUR)
                else None
            )
            mention = request.form.get(f"mention_{enr.id}")
            if current_user.role == ROLE_ENSEIGNANT:
                # Préserver l'observation éventuelle du directeur
                existing = StudentPeriodObservation.query.filter_by(
                    enrollment_id=enr.id,
                    period_id=(selected_period.id if scope == "PERIODE" and selected_period else None),
                    scope=scope,
                ).first()
                d_obs = existing.director_observation if existing else None

            save_student_observation(
                enrollment=enr,
                period=selected_period if scope == "PERIODE" else None,
                scope=scope,
                teacher_observation=t_obs,
                director_observation=d_obs,
                honors_mention=mention,
                actor=current_user,
                commit=False,
            )
            if scope == "ANNUEL" and current_user.role in (ROLE_ADMIN, ROLE_DIRECTEUR):
                dec = (request.form.get(f"decision_{enr.id}") or "").strip()
                enr.annual_decision = dec[:40] or None

        record_audit_log(
            "OBSERVATIONS_SAVE",
            f"Enregistrement des appréciations/observations ({scope}) pour {selected_class.name}",
            target_type="AcademicClass",
            target_id=selected_class.id,
            commit=True,
        )
        flash("Les observations de bulletins ont été enregistrées.", "success")
        return redirect(
            url_for(
                "grades.period_observations",
                class_id=selected_class.id,
                period_id=selected_period.id if selected_period else None,
                scope=scope,
            )
        )

    obs_by_enrollment: dict[int, StudentPeriodObservation] = {}
    active_enrollments = []
    if selected_class:
        active_enrollments = [e for e in selected_class.enrollments if e.is_active]
        enr_ids = [e.id for e in active_enrollments]
        if enr_ids:
            q = StudentPeriodObservation.query.filter(
                StudentPeriodObservation.enrollment_id.in_(enr_ids),
                StudentPeriodObservation.scope == scope,
            )
            if scope == "PERIODE" and selected_period:
                q = q.filter(StudentPeriodObservation.period_id == selected_period.id)
            for item in q.all():
                obs_by_enrollment[item.enrollment_id] = item

    return render_template(
        "grades/observations.html",
        selected_year=selected_year,
        classes=classes,
        periods=periods,
        selected_class=selected_class,
        selected_period=selected_period,
        scope=scope,
        active_enrollments=active_enrollments,
        obs_by_enrollment=obs_by_enrollment,
        can_edit=can_edit,
    )


@grades_bp.route("/notes/historique")
@login_required
def grade_history_view():
    """Consultation de l'historique horodaté des saisies et corrections de notes (§8.2 CdC)."""
    selected_year, classes, periods, selected_class, selected_period = _resolve_class_and_period()
    entries = []
    enr_lookup = {}
    subj_lookup = {}

    if selected_class and selected_period:
        if not user_can_view_class(current_user, selected_class.id):
            abort(403)
        enr_lookup = {e.id: e for e in selected_class.enrollments}
        subj_lookup = {s.id: s for s in selected_class.subjects}
        if enr_lookup:
            entries = (
                GradeHistory.query.filter(
                    GradeHistory.period_id == selected_period.id,
                    GradeHistory.enrollment_id.in_(list(enr_lookup.keys())),
                )
                .order_by(GradeHistory.changed_at.desc(), GradeHistory.id.desc())
                .limit(300)
                .all()
            )

    return render_template(
        "grades/history.html",
        selected_year=selected_year,
        classes=classes,
        periods=periods,
        selected_class=selected_class,
        selected_period=selected_period,
        entries=entries,
        enr_lookup=enr_lookup,
        subj_lookup=subj_lookup,
    )
