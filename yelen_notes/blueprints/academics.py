"""Gestion des classes, matières/coefficients, règles de calcul et grille des appréciations (§4.2, §5, §6, §9.3 CdC)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from yelen_notes.blueprints.dashboard import get_selected_school_year
from yelen_notes.extensions import db
from yelen_notes.models import (
    ABSENCE_POLICIES,
    ABSENCE_ZERO,
    ANNUAL_MODES,
    ANNUAL_SIMPLE,
    ROLE_ADMIN,
    ROLE_DIRECTEUR,
    ROUNDING_HALF_UP,
    ROUNDING_MODES,
    TIE_COMPETITION,
    TIE_MODES,
    AcademicClass,
    CalculationPolicy,
    GradingScaleItem,
    SchoolYear,
    Subject,
)
from yelen_notes.security import (
    get_accessible_classes_query,
    record_audit_log,
    roles_required,
    user_can_view_class,
)
from yelen_notes.services.calculation_service import (
    resolve_policy_for_class,
    validate_grading_scale_intervals,
)

academics_bp = Blueprint("academics", __name__)


def _parse_dec(raw: str | None, field_label: str) -> Decimal:
    txt = (raw or "").strip().replace(",", ".")
    if not txt:
        raise ValueError(f"Le champ « {field_label} » est obligatoire.")
    try:
        val = Decimal(txt)
    except InvalidOperation as exc:
        raise ValueError(f"Valeur numérique invalide pour « {field_label} ».") from exc
    if not val.is_finite():
        raise ValueError(f"Valeur numérique non valide pour « {field_label} ».")
    return val


@academics_bp.route("/classes", methods=["GET", "POST"])
@login_required
def classes_list():
    all_years = SchoolYear.query.order_by(SchoolYear.label.desc()).all()
    year_id = request.args.get("year_id", type=int) or request.form.get("year_id", type=int)
    selected_year = db.session.get(SchoolYear, year_id) if year_id else get_selected_school_year()
    can_edit = current_user.role in (ROLE_ADMIN, ROLE_DIRECTEUR)

    if request.method == "POST":
        if not can_edit or selected_year is None:
            abort(403)
        if selected_year.is_closed:
            flash("Impossible de créer une classe dans une année scolaire clôturée.", "danger")
            return redirect(url_for("academics.classes_list", year_id=selected_year.id))

        name = (request.form.get("name") or "").strip()
        level_label = (request.form.get("level_label") or "").strip() or None
        director_name = (request.form.get("director_name") or "").strip()
        director_title = (request.form.get("director_title") or "Le Directeur").strip()
        titulaire_name = (request.form.get("titulaire_name") or "").strip()
        display_order = request.form.get("display_order", type=int) or 1
        copy_subjects_from_id = request.form.get("copy_subjects_from_id", type=int)

        if not name or not director_name or not titulaire_name:
            flash("L'intitulé de la classe, le nom du directeur/directrice et le nom du titulaire sont obligatoires.", "danger")
            return redirect(url_for("academics.classes_list", year_id=selected_year.id))

        if AcademicClass.query.filter_by(school_year_id=selected_year.id, name=name).first():
            flash(f"La classe « {name} » existe déjà pour l'année {selected_year.label}.", "danger")
            return redirect(url_for("academics.classes_list", year_id=selected_year.id))

        ac = AcademicClass(
            school_year_id=selected_year.id,
            name=name,
            level_label=level_label,
            director_name=director_name,
            director_title=director_title,
            titulaire_name=titulaire_name,
            display_order=max(1, display_order),
            is_active=True,
        )
        db.session.add(ac)
        db.session.flush()

        copied_count = 0
        if copy_subjects_from_id:
            source_cls = db.session.get(AcademicClass, copy_subjects_from_id)
            if source_cls is not None:
                for subj in source_cls.subjects:
                    db.session.add(
                        Subject(
                            class_id=ac.id,
                            name=subj.name,
                            short_label=subj.short_label,
                            coefficient=subj.coefficient,
                            min_score=subj.min_score,
                            max_score=subj.max_score,
                            reference_scale=subj.reference_scale,
                            include_in_average=subj.include_in_average,
                            allow_exemption=subj.allow_exemption,
                            display_order=subj.display_order,
                            is_active=subj.is_active,
                        )
                    )
                    copied_count += 1

        record_audit_log(
            "CLASS_CREATE",
            f"Création de la classe '{ac.name}' ({selected_year.label})",
            target_type="AcademicClass",
            target_id=ac.id,
            details={"copied_subjects": copied_count},
            commit=True,
        )
        msg = f"Classe « {ac.name} » créée avec succès."
        if copied_count:
            msg += f" ({copied_count} matières copiées.)"
        flash(msg, "success")
        return redirect(url_for("academics.class_subjects", class_id=ac.id))

    classes = (
        get_accessible_classes_query(current_user, selected_year.id).all()
        if selected_year
        else []
    )
    all_existing_classes = AcademicClass.query.order_by(AcademicClass.id.desc()).all() if can_edit else []
    return render_template(
        "academics/classes.html",
        all_years=all_years,
        selected_year=selected_year,
        classes=classes,
        all_existing_classes=all_existing_classes,
        can_edit=can_edit,
    )


@academics_bp.route("/classes/<int:class_id>/modifier", methods=["POST"])
@roles_required(ROLE_ADMIN, ROLE_DIRECTEUR)
def update_class(class_id: int):
    ac = db.get_or_404(AcademicClass, class_id)
    if ac.school_year.is_closed:
        flash("L'année scolaire est clôturée : les informations historisées de la classe sont figées.", "danger")
        return redirect(url_for("academics.classes_list", year_id=ac.school_year_id))

    name = (request.form.get("name") or "").strip()
    director_name = (request.form.get("director_name") or "").strip()
    director_title = (request.form.get("director_title") or "Le Directeur").strip()
    titulaire_name = (request.form.get("titulaire_name") or "").strip()
    level_label = (request.form.get("level_label") or "").strip() or None
    display_order = request.form.get("display_order", type=int) or ac.display_order
    is_active = request.form.get("is_active") == "1"

    if not name or not director_name or not titulaire_name:
        flash("Intitulé, directeur/directrice et titulaire sont obligatoires.", "danger")
        return redirect(url_for("academics.classes_list", year_id=ac.school_year_id))

    dup = AcademicClass.query.filter(
        AcademicClass.school_year_id == ac.school_year_id,
        AcademicClass.name == name,
        AcademicClass.id != ac.id,
    ).first()
    if dup:
        flash(f"Une autre classe porte déjà le nom « {name} » cette année.", "danger")
        return redirect(url_for("academics.classes_list", year_id=ac.school_year_id))

    ac.name = name
    ac.level_label = level_label
    ac.director_name = director_name
    ac.director_title = director_title
    ac.titulaire_name = titulaire_name
    ac.display_order = max(1, display_order)
    ac.is_active = is_active

    record_audit_log(
        "CLASS_UPDATE",
        f"Modification de la classe '{ac.name}' ({ac.school_year.label})",
        target_type="AcademicClass",
        target_id=ac.id,
        commit=True,
    )
    flash(f"Classe « {ac.name} » mise à jour.", "success")
    return redirect(url_for("academics.classes_list", year_id=ac.school_year_id))


@academics_bp.route("/classes/<int:class_id>/matieres", methods=["GET", "POST"])
@login_required
def class_subjects(class_id: int):
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        abort(403)

    can_edit = (current_user.role in (ROLE_ADMIN, ROLE_DIRECTEUR)) and not ac.school_year.is_closed
    policy = resolve_policy_for_class(ac)
    default_ref_scale = policy.general_average_scale if policy else Decimal("10.00")

    if request.method == "POST":
        if not can_edit:
            abort(403)

        name = (request.form.get("name") or "").strip()
        short_label = (request.form.get("short_label") or "").strip() or None
        display_order = request.form.get("display_order", type=int)
        include_in_average = request.form.get("include_in_average") == "1"
        allow_exemption = request.form.get("allow_exemption") == "1"

        try:
            coef = _parse_dec(request.form.get("coefficient"), "Coefficient")
            min_s = _parse_dec(request.form.get("min_score") or "0", "Note minimale")
            max_s = _parse_dec(request.form.get("max_score"), "Note maximale")
            ref_s = _parse_dec(request.form.get("reference_scale"), "Moyenne sur")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("academics.class_subjects", class_id=ac.id))

        errors: list[str] = []
        if not name:
            errors.append("Le nom de la matière est obligatoire.")
        if coef <= 0:
            errors.append("Le coefficient doit être strictement supérieur à zéro.")
        if min_s < 0:
            errors.append("La note minimale ne peut pas être négative.")
        if min_s >= max_s:
            errors.append("La note minimale doit être strictement inférieure à la note maximale.")
        if ref_s <= 0:
            errors.append("Le barème de référence (« Moyenne sur ») doit être strictement supérieur à zéro.")
        if Subject.query.filter_by(class_id=ac.id, name=name).first():
            errors.append(f"La matière « {name} » existe déjà dans cette classe.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return redirect(url_for("academics.class_subjects", class_id=ac.id))

        if not display_order or display_order <= 0:
            max_ord = (
                db.session.query(db.func.max(Subject.display_order))
                .filter_by(class_id=ac.id)
                .scalar()
            )
            display_order = (int(max_ord) + 1) if max_ord else 1

        subj = Subject(
            class_id=ac.id,
            name=name,
            short_label=short_label,
            coefficient=coef,
            min_score=min_s,
            max_score=max_s,
            reference_scale=ref_s,
            include_in_average=include_in_average,
            allow_exemption=allow_exemption,
            display_order=display_order,
            is_active=True,
        )
        db.session.add(subj)
        db.session.flush()
        record_audit_log(
            "SUBJECT_CREATE",
            f"Ajout de la matière '{subj.name}' dans {ac.name} ({ac.school_year.label})",
            target_type="Subject",
            target_id=subj.id,
            commit=True,
        )
        flash(f"Matière « {subj.name} » ajoutée à {ac.name}.", "success")
        return redirect(url_for("academics.class_subjects", class_id=ac.id))

    subjects = Subject.query.filter_by(class_id=ac.id).order_by(Subject.display_order, Subject.name).all()
    return render_template(
        "academics/subjects.html",
        academic_class=ac,
        subjects=subjects,
        policy=policy,
        default_ref_scale=default_ref_scale,
        can_edit=can_edit,
    )


@academics_bp.route("/matieres/<int:subject_id>/modifier", methods=["POST"])
@roles_required(ROLE_ADMIN, ROLE_DIRECTEUR)
def update_subject(subject_id: int):
    subj = db.get_or_404(Subject, subject_id)
    ac = subj.academic_class
    if ac.school_year.is_closed:
        flash("Impossible de modifier une matière d'une année clôturée.", "danger")
        return redirect(url_for("academics.class_subjects", class_id=ac.id))

    name = (request.form.get("name") or "").strip()
    short_label = (request.form.get("short_label") or "").strip() or None
    display_order = request.form.get("display_order", type=int) or subj.display_order
    include_in_average = request.form.get("include_in_average") == "1"
    allow_exemption = request.form.get("allow_exemption") == "1"
    is_active = request.form.get("is_active") == "1"

    try:
        coef = _parse_dec(request.form.get("coefficient"), "Coefficient")
        min_s = _parse_dec(request.form.get("min_score") or "0", "Note minimale")
        max_s = _parse_dec(request.form.get("max_score"), "Note maximale")
        ref_s = _parse_dec(request.form.get("reference_scale"), "Moyenne sur")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("academics.class_subjects", class_id=ac.id))

    if not name or coef <= 0 or min_s < 0 or min_s >= max_s or ref_s <= 0:
        flash("Paramètres incohérents : vérifiez que Note min < Note max, Coefficient > 0 et Barème > 0.", "danger")
        return redirect(url_for("academics.class_subjects", class_id=ac.id))

    dup = Subject.query.filter(
        Subject.class_id == ac.id,
        Subject.name == name,
        Subject.id != subj.id,
    ).first()
    if dup:
        flash(f"Une autre matière porte déjà le nom « {name} ».", "danger")
        return redirect(url_for("academics.class_subjects", class_id=ac.id))

    subj.name = name
    subj.short_label = short_label
    subj.coefficient = coef
    subj.min_score = min_s
    subj.max_score = max_s
    subj.reference_scale = ref_s
    subj.display_order = max(1, display_order)
    subj.include_in_average = include_in_average
    subj.allow_exemption = allow_exemption
    subj.is_active = is_active

    record_audit_log(
        "SUBJECT_UPDATE",
        f"Mise à jour de la matière '{subj.name}' dans {ac.name}",
        target_type="Subject",
        target_id=subj.id,
        commit=True,
    )
    flash(f"Matière « {subj.name} » enregistrée.", "success")
    return redirect(url_for("academics.class_subjects", class_id=ac.id))


@academics_bp.route("/regles-et-appreciations", methods=["GET", "POST"])
@login_required
def policies_and_scales():
    all_years = SchoolYear.query.order_by(SchoolYear.label.desc()).all()
    year_id = request.args.get("year_id", type=int) or request.form.get("year_id", type=int)
    selected_year = db.session.get(SchoolYear, year_id) if year_id else get_selected_school_year()

    raw_class_id = request.args.get("class_id") or request.form.get("class_id") or ""
    target_class_id = int(raw_class_id) if raw_class_id.isdigit() else None
    target_class = db.session.get(AcademicClass, target_class_id) if target_class_id else None
    if target_class and selected_year and target_class.school_year_id != selected_year.id:
        target_class = None
        target_class_id = None

    can_edit = (
        current_user.role in (ROLE_ADMIN, ROLE_DIRECTEUR)
        and selected_year is not None
        and not selected_year.is_closed
    )

    policy = (
        CalculationPolicy.query.filter_by(
            school_year_id=selected_year.id,
            class_id=target_class_id,
        ).first()
        if selected_year
        else None
    )

    scale_warnings: list[str] = []

    if request.method == "POST":
        if not can_edit or selected_year is None:
            abort(403)

        if request.form.get("action") == "delete_override" and target_class_id and policy:
            db.session.delete(policy)
            record_audit_log(
                "POLICY_OVERRIDE_DELETE",
                f"Suppression de la règle spécifique de la classe {target_class.name if target_class else target_class_id}",
                commit=True,
            )
            flash("La règle spécifique à la classe a été supprimée ; la règle de l'année s'applique.", "info")
            return redirect(url_for("academics.policies_and_scales", year_id=selected_year.id))

        try:
            gen_scale = _parse_dec(request.form.get("general_average_scale"), "Barème de moyenne générale")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(
                url_for("academics.policies_and_scales", year_id=selected_year.id, class_id=target_class_id or "")
            )

        if gen_scale <= 0:
            flash("Le barème de moyenne générale doit être strictement positif.", "danger")
            return redirect(
                url_for("academics.policies_and_scales", year_id=selected_year.id, class_id=target_class_id or "")
            )

        decimal_places = request.form.get("decimal_places", type=int)
        if decimal_places is None or decimal_places not in (0, 1, 2, 3, 4):
            decimal_places = 2

        rounding_mode = request.form.get("rounding_mode") or ROUNDING_HALF_UP
        if rounding_mode not in ROUNDING_MODES:
            rounding_mode = ROUNDING_HALF_UP

        absence_policy = request.form.get("absence_policy") or ABSENCE_ZERO
        if absence_policy not in ABSENCE_POLICIES:
            absence_policy = ABSENCE_ZERO

        tie_mode = request.form.get("tie_breaking_mode") or TIE_COMPETITION
        if tie_mode not in TIE_MODES:
            tie_mode = TIE_COMPETITION

        annual_mode = request.form.get("annual_average_mode") or ANNUAL_SIMPLE
        if annual_mode not in ANNUAL_MODES:
            annual_mode = ANNUAL_SIMPLE

        annual_req_all = request.form.get("annual_require_all_periods") == "1"

        # Lecture des lignes de la grille des appréciations
        mins = request.form.getlist("scale_min[]")
        maxs = request.form.getlist("scale_max[]")
        labels = request.form.getlist("scale_label[]")
        raw_intervals: list[dict] = []
        for lo_s, hi_s, lb_s in zip(mins, maxs, labels):
            if not (lo_s.strip() or hi_s.strip() or lb_s.strip()):
                continue
            raw_intervals.append(
                {
                    "min_score": lo_s.strip().replace(",", "."),
                    "max_score": hi_s.strip().replace(",", "."),
                    "appreciation": lb_s.strip(),
                }
            )

        validation = validate_grading_scale_intervals(raw_intervals, gen_scale)
        if not validation.is_valid:
            for err in validation.errors:
                flash(err, "danger")
            return redirect(
                url_for("academics.policies_and_scales", year_id=selected_year.id, class_id=target_class_id or "")
            )

        if policy is None:
            policy = CalculationPolicy(
                school_year_id=selected_year.id,
                class_id=target_class_id,
                general_average_scale=gen_scale,
            )
            db.session.add(policy)
            db.session.flush()

        policy.general_average_scale = gen_scale
        policy.decimal_places = decimal_places
        policy.rounding_mode = rounding_mode
        policy.absence_policy = absence_policy
        policy.tie_breaking_mode = tie_mode
        policy.annual_average_mode = annual_mode
        policy.annual_require_all_periods = annual_req_all

        # Remplacement ordonné de la grille d'appréciations
        GradingScaleItem.query.filter_by(policy_id=policy.id).delete()
        sorted_intervals = sorted(
            raw_intervals,
            key=lambda it: (Decimal(it["min_score"]), Decimal(it["max_score"])),
        )
        for idx, item in enumerate(sorted_intervals, start=1):
            hi_dec = Decimal(item["max_score"])
            is_last = (idx == len(sorted_intervals)) and (hi_dec == gen_scale)
            db.session.add(
                GradingScaleItem(
                    policy_id=policy.id,
                    min_score=Decimal(item["min_score"]),
                    max_score=hi_dec,
                    appreciation=item["appreciation"][:100],
                    is_last_interval=is_last,
                    display_order=idx,
                )
            )

        record_audit_log(
            "CALCULATION_POLICY_SAVE",
            f"Enregistrement des règles de calcul et appréciations ({selected_year.label}"
            + (f" - {target_class.name}" if target_class else " - Année entière")
            + ")",
            target_type="CalculationPolicy",
            target_id=policy.id,
            commit=True,
        )
        for warn in validation.warnings:
            flash(warn, "warning")
        flash("Règles de calcul et grille des appréciations enregistrées avec succès.", "success")
        return redirect(
            url_for("academics.policies_and_scales", year_id=selected_year.id, class_id=target_class_id or "")
        )

    if policy is not None and policy.scale_items:
        chk = validate_grading_scale_intervals(
            [
                {
                    "min_score": str(it.min_score),
                    "max_score": str(it.max_score),
                    "appreciation": it.appreciation,
                }
                for it in policy.scale_items
            ],
            Decimal(str(policy.general_average_scale)),
        )
        scale_warnings = chk.warnings

    year_classes = (
        AcademicClass.query.filter_by(school_year_id=selected_year.id)
        .order_by(AcademicClass.display_order, AcademicClass.name)
        .all()
        if selected_year
        else []
    )
    return render_template(
        "academics/policies_scales.html",
        all_years=all_years,
        selected_year=selected_year,
        year_classes=year_classes,
        target_class=target_class,
        policy=policy,
        scale_warnings=scale_warnings,
        can_edit=can_edit,
    )
