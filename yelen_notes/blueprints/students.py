"""Gestion des élèves, historique pluriannuel, import/export Excel et passage d'année (§7 CdC)."""

from __future__ import annotations

from datetime import datetime
import io

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from yelen_notes.blueprints.dashboard import get_selected_school_year
from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_ADMIN,
    ROLE_DIRECTEUR,
    AcademicClass,
    ClassEnrollment,
    SchoolYear,
    Student,
)
from yelen_notes.security import (
    any_permission_required,
    get_accessible_classes_query,
    has_permission,
    is_strictly_scoped_parent,
    permission_required,
    record_audit_log,
    user_can_access_establishment,
    user_can_create_class_students,
    user_can_edit_class_students,
    user_can_view_class,
    user_can_view_enrollment,
)
from yelen_notes.services.excel_service import (
    export_students_to_excel,
    generate_student_import_template,
    import_students_from_excel,
)
from yelen_notes.services.student_service import (
    StudentValidationError,
    create_and_enroll_student,
    promote_or_repeat_students,
    renumber_class_alphabetically,
    toggle_enrollment_active_state,
    update_student_and_enrollment,
)

students_bp = Blueprint("students", __name__)


@students_bp.route("/eleves", methods=["GET", "POST"])
@any_permission_required("students.view", "enrollments.view", module="students")
def students_list():
    if is_strictly_scoped_parent(current_user):
        return redirect(url_for("operations.parent_portal"))

    selected_year = get_selected_school_year()
    accessible_classes = (
        get_accessible_classes_query(current_user, selected_year.id).all()
        if selected_year
        else []
    )
    class_id = request.args.get("class_id", type=int) or request.form.get("class_id", type=int)
    selected_class = None
    if accessible_classes:
        if class_id:
            selected_class = next((c for c in accessible_classes if c.id == class_id), None)
        if selected_class is None:
            selected_class = accessible_classes[0]

    can_edit = user_can_create_class_students(current_user, selected_class)
    can_edit_students = user_can_edit_class_students(current_user, selected_class)

    if request.method == "POST":
        if not can_edit or selected_class is None:
            record_audit_log(
                "ACCESS_DENIED",
                f"Création d'élève refusée sur {request.path} pour {current_user.username} ({current_user.role})",
                commit=True,
            )
            abort(403)

        last_name = request.form.get("last_name") or ""
        first_names = request.form.get("first_names") or ""
        sex = request.form.get("sex") or ""
        birth_str = (request.form.get("birth_date") or "").strip()
        roll_number = request.form.get("roll_number", type=int)
        unique_code = request.form.get("unique_code") or None
        schooling = request.form.get("schooling_status") or None
        repeated = request.form.get("repeated_class_detail") or None
        obs = request.form.get("observations") or None

        try:
            bdate = datetime.strptime(birth_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Veuillez saisir une date de naissance valide.", "danger")
            return redirect(url_for("students.students_list", class_id=selected_class.id))

        try:
            enr = create_and_enroll_student(
                academic_class=selected_class,
                last_name=last_name,
                first_names=first_names,
                sex=sex,
                birth_date=bdate,
                roll_number=roll_number,
                unique_code=unique_code,
                schooling_status=schooling,
                repeated_class_detail=repeated,
                observations=obs,
                is_active=True,
                commit=False,
            )
            db.session.flush()
            record_audit_log(
                "STUDENT_CREATE",
                f"Inscription de l'élève {enr.student.full_name} dans {selected_class.name}",
                target_type="ClassEnrollment",
                target_id=enr.id,
                commit=True,
            )
            flash(f"Élève « {enr.student.full_name} » inscrit(e) au N° {enr.roll_number}.", "success")
        except StudentValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        return redirect(url_for("students.students_list", class_id=selected_class.id))

    # Recherche, filtres actif/inactif et tri (numéro ou alphabétique)
    search_q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status", "active")
    sort_by = request.args.get("sort", "roll")

    enrollments: list[ClassEnrollment] = []
    if selected_class is not None:
        query = ClassEnrollment.query.join(Student).filter(
            ClassEnrollment.class_id == selected_class.id
        )
        if status_filter == "active":
            query = query.filter(ClassEnrollment.is_active.is_(True))
        elif status_filter == "inactive":
            query = query.filter(ClassEnrollment.is_active.is_(False))

        if search_q:
            like_pat = f"%{search_q}%"
            conds = [
                Student.last_name.ilike(like_pat),
                Student.first_names.ilike(like_pat),
                Student.unique_code.ilike(like_pat),
            ]
            if search_q.isdigit():
                conds.append(ClassEnrollment.roll_number == int(search_q))
            query = query.filter(or_(*conds))

        if sort_by == "alpha":
            query = query.order_by(Student.last_name, Student.first_names, ClassEnrollment.roll_number)
        else:
            query = query.order_by(ClassEnrollment.roll_number, Student.last_name)

        enrollments = query.all()

    return render_template(
        "students/list.html",
        selected_year=selected_year,
        accessible_classes=accessible_classes,
        selected_class=selected_class,
        enrollments=enrollments,
        search_q=search_q,
        status_filter=status_filter,
        sort_by=sort_by,
        can_edit=can_edit,
        can_edit_students=can_edit_students,
    )


@students_bp.route("/eleves/<int:enrollment_id>", methods=["GET", "POST"])
@permission_required("students.view", module="students")
def student_detail(enrollment_id: int):
    enrollment = db.get_or_404(ClassEnrollment, enrollment_id)
    ac = enrollment.academic_class
    if not user_can_view_enrollment(current_user, enrollment):
        record_audit_log(
            "ACCESS_DENIED",
            f"Accès refusé à la fiche élève {enrollment_id} ({ac.name}) pour {current_user.username}",
            module="students",
            status="DENIED",
            commit=True,
        )
        abort(403)

    can_edit = user_can_edit_class_students(current_user, ac)
    can_toggle_status = (
        has_permission(current_user, "students.delete")
        and user_can_access_establishment(current_user, ac.establishment_id)
        and not ac.school_year.is_closed
    )
    student = enrollment.student

    # Navigation rapide précédent / suivant dans la classe (uniquement si accès à la liste de classe)
    ordered_class_ids = (
        [
            row_id
            for (row_id,) in db.session.query(ClassEnrollment.id)
            .join(Student)
            .filter(ClassEnrollment.class_id == ac.id, ClassEnrollment.is_active.is_(True))
            .order_by(ClassEnrollment.roll_number, Student.last_name, ClassEnrollment.id)
            .all()
        ]
        if user_can_view_class(current_user, ac.id)
        else []
    )
    prev_enrollment_id = None
    next_enrollment_id = None
    if enrollment.id in ordered_class_ids:
        idx = ordered_class_ids.index(enrollment.id)
        if idx > 0:
            prev_enrollment_id = ordered_class_ids[idx - 1]
        if idx + 1 < len(ordered_class_ids):
            next_enrollment_id = ordered_class_ids[idx + 1]

    if request.method == "POST":
        if not can_edit:
            record_audit_log(
                "ACCESS_DENIED",
                f"Modification refusée sur la fiche élève {enrollment_id} ({ac.name}) pour {current_user.username}",
                commit=True,
            )
            abort(403)

        birth_str = (request.form.get("birth_date") or "").strip()
        try:
            bdate = datetime.strptime(birth_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Veuillez saisir une date de naissance valide.", "danger")
            return redirect(url_for("students.student_detail", enrollment_id=enrollment.id))

        try:
            changes = update_student_and_enrollment(
                enrollment=enrollment,
                last_name=request.form.get("last_name") or "",
                first_names=request.form.get("first_names") or "",
                sex=request.form.get("sex") or "",
                birth_date=bdate,
                roll_number=request.form.get("roll_number", type=int),
                unique_code=request.form.get("unique_code"),
                schooling_status=request.form.get("schooling_status"),
                repeated_class_detail=request.form.get("repeated_class_detail"),
                observations=request.form.get("observations"),
                commit=False,
            )
        except StudentValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("students.student_detail", enrollment_id=enrollment.id))

        record_audit_log(
            "STUDENT_UPDATE",
            f"Modification de l'identité/fiche de l'élève {student.full_name} ({ac.name})",
            target_type="ClassEnrollment",
            target_id=enrollment.id,
            details={"changes": changes} if changes else None,
            commit=True,
        )
        flash("Identité et informations de l'élève mises à jour.", "success")
        if request.form.get("action") == "save_and_next" and next_enrollment_id:
            return redirect(url_for("students.student_detail", enrollment_id=next_enrollment_id))
        return redirect(url_for("students.student_detail", enrollment_id=enrollment.id))

    history_enrollments = (
        ClassEnrollment.query.join(AcademicClass)
        .join(SchoolYear)
        .filter(ClassEnrollment.student_id == student.id)
        .order_by(SchoolYear.label.desc())
        .all()
    )
    return render_template(
        "students/detail.html",
        enrollment=enrollment,
        student=student,
        academic_class=ac,
        history_enrollments=history_enrollments,
        can_edit=can_edit,
        can_toggle_status=can_toggle_status,
        prev_enrollment_id=prev_enrollment_id,
        next_enrollment_id=next_enrollment_id,
    )


@students_bp.route("/eleves/modifier-liste/<int:class_id>", methods=["GET", "POST"])
@login_required
def students_bulk_edit(class_id: int):
    """Tableau d'édition groupée des identités et informations des élèves d'une classe."""
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_edit_class_students(current_user, ac):
        record_audit_log(
            "ACCESS_DENIED",
            f"Édition groupée des élèves refusée sur {ac.name} pour {current_user.username} ({current_user.role})",
            commit=True,
        )
        abort(403)

    include_inactive = request.args.get("inactive", "0") == "1" or request.form.get("inactive") == "1"
    query = ClassEnrollment.query.join(Student).filter(ClassEnrollment.class_id == ac.id)
    if not include_inactive:
        query = query.filter(ClassEnrollment.is_active.is_(True))
    enrollments = query.order_by(ClassEnrollment.roll_number, Student.last_name, Student.first_names).all()

    if request.method == "POST":
        updated_count = 0
        detailed_changes: dict[str, dict] = {}
        try:
            for enr in enrollments:
                prefix = f"enr_{enr.id}_"
                if f"{prefix}last_name" not in request.form:
                    continue
                birth_str = (request.form.get(f"{prefix}birth_date") or "").strip()
                try:
                    bdate = datetime.strptime(birth_str, "%Y-%m-%d").date()
                except ValueError as exc:
                    raise StudentValidationError(
                        f"N° {enr.roll_number} ({enr.student.full_name}) : date de naissance invalide."
                    ) from exc

                changes = update_student_and_enrollment(
                    enrollment=enr,
                    last_name=request.form.get(f"{prefix}last_name") or "",
                    first_names=request.form.get(f"{prefix}first_names") or "",
                    sex=request.form.get(f"{prefix}sex") or "",
                    birth_date=bdate,
                    roll_number=request.form.get(f"{prefix}roll_number", type=int),
                    unique_code=request.form.get(f"{prefix}unique_code"),
                    schooling_status=request.form.get(f"{prefix}schooling_status"),
                    repeated_class_detail=request.form.get(f"{prefix}repeated_class_detail"),
                    observations=request.form.get(f"{prefix}observations"),
                    commit=False,
                )
                if changes:
                    updated_count += 1
                    detailed_changes[str(enr.id)] = {
                        "student": enr.student.full_name,
                        "changes": changes,
                    }

            if updated_count > 0:
                record_audit_log(
                    "STUDENT_BULK_UPDATE",
                    f"Mise à jour groupée de {updated_count} élève(s) dans {ac.name} ({ac.school_year.label})",
                    target_type="AcademicClass",
                    target_id=ac.id,
                    details=detailed_changes,
                    commit=True,
                )
                flash(
                    f"Les identités et informations de {updated_count} élève(s) ont été mises à jour avec succès.",
                    "success",
                )
            else:
                db.session.commit()
                flash("Aucune modification détectée dans la liste.", "info")
            return redirect(url_for("students.students_list", class_id=ac.id))
        except StudentValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    return render_template(
        "students/bulk_edit.html",
        academic_class=ac,
        enrollments=enrollments,
        include_inactive=include_inactive,
    )


@students_bp.route("/eleves/<int:enrollment_id>/statut", methods=["POST"])
@permission_required("students.delete", module="students")
def toggle_student_status(enrollment_id: int):
    enrollment = db.get_or_404(ClassEnrollment, enrollment_id)
    if not user_can_access_establishment(current_user, enrollment.academic_class.establishment_id):
        abort(403)
    if enrollment.academic_class.school_year.is_closed:
        flash("Impossible de modifier le statut d'un élève sur une année clôturée.", "danger")
        return redirect(url_for("students.student_detail", enrollment_id=enrollment.id))

    new_active = request.form.get("is_active") == "1"
    reason = request.form.get("reason")
    toggle_enrollment_active_state(enrollment, is_active=new_active, reason=reason, commit=False)
    record_audit_log(
        "STUDENT_STATUS_TOGGLE",
        f"{'Réactivation' if new_active else 'Désactivation'} de l'élève {enrollment.student.full_name} (historique conservé)",
        target_type="ClassEnrollment",
        target_id=enrollment.id,
        commit=True,
    )
    flash(
        f"L'élève {enrollment.student.full_name} est désormais {'actif' if new_active else 'inactif (notes et historique conservés)'}.",
        "info",
    )
    return redirect(url_for("students.student_detail", enrollment_id=enrollment.id))


@students_bp.route("/eleves/renumeroter/<int:class_id>", methods=["POST"])
@login_required
def renumber_class(class_id: int):
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_edit_class_students(current_user, ac):
        record_audit_log(
            "ACCESS_DENIED",
            f"Renumérotation refusée sur {ac.name} pour {current_user.username} ({current_user.role})",
            commit=True,
        )
        abort(403)

    count = renumber_class_alphabetically(ac, commit=False)
    record_audit_log(
        "CLASS_RENUMBER",
        f"Renumérotation alphabétique contrôlée de {ac.name} ({count} élèves)",
        target_type="AcademicClass",
        target_id=ac.id,
        commit=True,
    )
    flash(f"Renumérotation alphabétique effectuée pour {count} élèves.", "success")
    return redirect(url_for("students.students_list", class_id=ac.id))


@students_bp.route("/eleves/modele-excel")
@login_required
def download_excel_template():
    data = generate_student_import_template()
    return send_file(
        io.BytesIO(data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="modele_import_eleves_yelen_notes.xlsx",
    )


@students_bp.route("/eleves/importer-excel/<int:class_id>", methods=["POST"])
@any_permission_required("students.create", "enrollments.create", module="enrollments")
def import_excel(class_id: int):
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_create_class_students(current_user, ac):
        abort(403)
    if ac.school_year.is_closed:
        flash("Impossible d'importer des élèves dans une année clôturée.", "danger")
        return redirect(url_for("students.students_list", class_id=ac.id))

    uploaded = request.files.get("excel_file")
    if not uploaded or not uploaded.filename:
        flash("Veuillez sélectionner un fichier Excel (.xlsx).", "danger")
        return redirect(url_for("students.students_list", class_id=ac.id))

    summary = import_students_from_excel(uploaded.read(), ac)
    record_audit_log(
        "EXCEL_STUDENT_IMPORT",
        f"Import Excel dans {ac.name} : {summary.imported_count} importés, "
        f"{len(summary.skipped_duplicates)} doublons ignorés, {len(summary.errors)} erreurs",
        target_type="AcademicClass",
        target_id=ac.id,
        commit=True,
    )

    if summary.imported_count:
        flash(f"{summary.imported_count} élève(s) importé(s) avec succès dans {ac.name}.", "success")
    for dup_msg in summary.skipped_duplicates[:10]:
        flash(dup_msg, "warning")
    for err_msg in summary.errors[:10]:
        flash(err_msg, "danger")

    return redirect(url_for("students.students_list", class_id=ac.id))


@students_bp.route("/eleves/exporter-excel/<int:class_id>")
@permission_required("students.export", module="students")
def export_excel(class_id: int):
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)

    include_inactive = request.args.get("inactive", "1") == "1"
    data = export_students_to_excel(ac, include_inactive=include_inactive)
    safe_cls = ac.name.replace(" ", "_")
    return send_file(
        io.BytesIO(data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"eleves_{safe_cls}_{ac.school_year.label}.xlsx",
    )


@students_bp.route("/eleves/passage-annee", methods=["GET", "POST"])
@permission_required("enrollments.validate", module="enrollments")
def year_transition():
    all_classes = [
        c
        for c in AcademicClass.query.join(SchoolYear)
        .order_by(SchoolYear.label.desc(), AcademicClass.display_order, AcademicClass.name)
        .all()
        if user_can_access_establishment(current_user, c.establishment_id)
    ]
    source_class_id = request.args.get("source_class_id", type=int) or request.form.get("source_class_id", type=int)
    target_class_id = request.args.get("target_class_id", type=int) or request.form.get("target_class_id", type=int)

    source_class = db.session.get(AcademicClass, source_class_id) if source_class_id else None
    target_class = db.session.get(AcademicClass, target_class_id) if target_class_id else None

    if request.method == "POST" and source_class and target_class:
        if target_class.school_year.is_closed:
            flash("La classe cible appartient à une année clôturée.", "danger")
            return redirect(url_for("students.year_transition", source_class_id=source_class.id))

        decisions: dict[int, str] = {}
        for enr in source_class.enrollments:
            val = request.form.get(f"decision_{enr.id}", "IGNORES")
            decisions[enr.id] = val

        try:
            counts = promote_or_repeat_students(
                source_class=source_class,
                target_class=target_class,
                decisions_by_enrollment_id=decisions,
                renumber_target=request.form.get("renumber_target") == "1",
                commit=False,
            )
            record_audit_log(
                "YEAR_TRANSITION",
                f"Passage {source_class.name} ({source_class.school_year.label}) -> "
                f"{target_class.name} ({target_class.school_year.label}) : {counts['enrolled']} inscrits",
                details=counts,
                commit=True,
            )
            flash(
                f"Opération terminée : {counts['enrolled']} élève(s) inscrit(s) dans {target_class.name}, "
                f"{counts['skipped_existing']} déjà inscrit(s) ignoré(s), {counts['marked_left']} sortie(s) enregistrée(s).",
                "success",
            )
            return redirect(url_for("students.students_list", class_id=target_class.id))
        except StudentValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    source_enrollments = (
        ClassEnrollment.query.join(Student)
        .filter(ClassEnrollment.class_id == source_class.id, ClassEnrollment.is_active.is_(True))
        .order_by(ClassEnrollment.roll_number)
        .all()
        if source_class
        else []
    )
    open_target_classes = [c for c in all_classes if not c.school_year.is_closed]
    return render_template(
        "students/transition.html",
        all_classes=all_classes,
        open_target_classes=open_target_classes,
        source_class=source_class,
        target_class=target_class,
        source_enrollments=source_enrollments,
    )
