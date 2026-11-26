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
    ROLE_ENSEIGNANT,
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
    any_permission_required,
    get_accessible_classes_query,
    get_accessible_years_query,
    has_permission,
    is_strictly_scoped_teacher,
    permission_required,
    record_audit_log,
    user_can_access_establishment,
    user_can_manage_class_subjects,
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
@permission_required("classes.view", module="classes")
def classes_list():
    all_years = get_accessible_years_query(current_user).all()
    year_id = request.args.get("year_id", type=int) or request.form.get("year_id", type=int)
    selected_year = db.session.get(SchoolYear, year_id) if year_id else get_selected_school_year()
    if selected_year and not user_can_access_establishment(current_user, selected_year.establishment_id):
        abort(403)
    can_edit = has_permission(current_user, "classes.create")

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
            establishment_id=selected_year.establishment_id or 1,
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
            if source_cls is not None and user_can_access_establishment(current_user, source_cls.establishment_id):
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
    all_existing_classes = (
        get_accessible_classes_query(current_user).all()
        if can_edit
        else []
    )
    return render_template(
        "academics/classes.html",
        all_years=all_years,
        selected_year=selected_year,
        classes=classes,
        all_existing_classes=all_existing_classes,
        can_edit=can_edit,
    )


@academics_bp.route("/classes/<int:class_id>/modifier", methods=["POST"])
@permission_required("classes.update", module="classes")
def update_class(class_id: int):
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_access_establishment(current_user, ac.establishment_id or ac.school_year.establishment_id):
        abort(403)
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


@academics_bp.route("/matieres-et-baremes")
@permission_required("subjects.view", module="subjects")
def subjects_entry_shortcut():
    """Accès direct aux matières et barèmes de la classe accessible de l'utilisateur."""
    selected_year = get_selected_school_year()
    accessible_classes = (
        get_accessible_classes_query(current_user, selected_year.id).all()
        if selected_year
        else []
    )
    if accessible_classes:
        return redirect(url_for("academics.class_subjects", class_id=accessible_classes[0].id))
    return redirect(url_for("academics.classes_list"))


@academics_bp.route("/classes/<int:class_id>/matieres", methods=["GET", "POST"])
@permission_required("subjects.view", module="subjects")
def class_subjects(class_id: int):
    ac = db.get_or_404(AcademicClass, class_id)
    if not user_can_view_class(current_user, ac.id):
        record_audit_log(
            "ACCESS_DENIED",
            f"Accès refusé aux matières de {ac.name} pour {current_user.username} ({current_user.role})",
            commit=True,
        )
        abort(403)

    can_edit = user_can_manage_class_subjects(current_user, ac)
    policy = resolve_policy_for_class(ac)
    default_ref_scale = policy.general_average_scale if policy else Decimal("10.00")

    if request.method == "POST":
        if not can_edit or not has_permission(current_user, "subjects.create"):
            record_audit_log(
                "ACCESS_DENIED",
                f"Ajout/copie de matière refusé sur {ac.name} pour {current_user.username} ({current_user.role_label})",
                module="subjects",
                status="DENIED",
                commit=True,
            )
            abort(403)

        if request.form.get("action") == "copy_subjects":
            if not has_permission(current_user, "subjects.create"):
                abort(403)
            source_id = request.form.get("source_class_id", type=int)
            source_cls = db.session.get(AcademicClass, source_id) if source_id else None
            if (
                source_cls is None
                or source_cls.id == ac.id
                or not user_can_access_establishment(current_user, source_cls.establishment_id)
            ):
                flash("Veuillez sélectionner une classe source valide.", "danger")
                return redirect(url_for("academics.class_subjects", class_id=ac.id))

            existing_names = {
                s.name.strip().lower() for s in Subject.query.filter_by(class_id=ac.id).all()
            }
            max_ord = (
                db.session.query(db.func.max(Subject.display_order))
                .filter_by(class_id=ac.id)
                .scalar()
                or 0
            )
            copied = 0
            for src_s in source_cls.subjects:
                if src_s.name.strip().lower() in existing_names:
                    continue
                max_ord += 1
                db.session.add(
                    Subject(
                        class_id=ac.id,
                        name=src_s.name,
                        short_label=src_s.short_label,
                        coefficient=src_s.coefficient,
                        min_score=src_s.min_score,
                        max_score=src_s.max_score,
                        reference_scale=src_s.reference_scale,
                        include_in_average=src_s.include_in_average,
                        allow_exemption=src_s.allow_exemption,
                        display_order=max_ord,
                        is_active=src_s.is_active,
                    )
                )
                existing_names.add(src_s.name.strip().lower())
                copied += 1

            record_audit_log(
                "SUBJECT_COPY",
                f"Copie de {copied} matière(s) depuis {source_cls.name} ({source_cls.school_year.label}) vers {ac.name}",
                target_type="AcademicClass",
                target_id=ac.id,
                commit=True,
            )
            if copied:
                flash(f"{copied} matière(s) et leurs barèmes ont été copiés dans {ac.name}.", "success")
            else:
                flash("Toutes les matières de la classe source figurent déjà dans cette classe.", "info")
            return redirect(url_for("academics.class_subjects", class_id=ac.id))

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
    accessible_classes = get_accessible_classes_query(current_user, ac.school_year_id).all()
    copyable_classes = (
        [
            c
            for c in AcademicClass.query.join(SchoolYear)
            .filter(AcademicClass.id != ac.id)
            .order_by(SchoolYear.label.desc(), AcademicClass.display_order, AcademicClass.name)
            .all()
            if user_can_access_establishment(current_user, c.establishment_id) and len(c.subjects) > 0
        ]
        if can_edit
        else []
    )
    return render_template(
        "academics/subjects.html",
        academic_class=ac,
        accessible_classes=accessible_classes,
        copyable_classes=copyable_classes,
        subjects=subjects,
        policy=policy,
        default_ref_scale=default_ref_scale,
        can_edit=can_edit,
    )


@academics_bp.route("/matieres/<int:subject_id>/modifier", methods=["POST"])
@permission_required("subjects.update", module="subjects")
def update_subject(subject_id: int):
    subj = db.get_or_404(Subject, subject_id)
    ac = subj.academic_class
    if not user_can_manage_class_subjects(current_user, ac):
        record_audit_log(
            "ACCESS_DENIED",
            f"Modification de matière refusée sur {subj.name} ({ac.name}) pour {current_user.username} ({current_user.role})",
            commit=True,
        )
        abort(403)

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
@permission_required("subjects.view", module="subjects")
def policies_and_scales():
    all_years = get_accessible_years_query(current_user).all()
    year_id = request.args.get("year_id", type=int) or request.form.get("year_id", type=int)
    selected_year = db.session.get(SchoolYear, year_id) if year_id else get_selected_school_year()
    if selected_year and not user_can_access_establishment(current_user, selected_year.establishment_id):
        abort(403)

    year_classes = (
        get_accessible_classes_query(current_user, selected_year.id).all()
        if selected_year
        else []
    )

    raw_class_id = request.args.get("class_id")
    if raw_class_id is None and request.method == "POST":
        raw_class_id = request.form.get("class_id") or ""

    # Pour un enseignant titulaire, sélectionner par défaut sa classe s'il n'a pas demandé explicitement la règle année entière
    if (
        raw_class_id is None
        and request.method == "GET"
        and is_strictly_scoped_teacher(current_user)
        and request.args.get("scope") != "year"
        and year_classes
    ):
        target_class = year_classes[0]
        target_class_id = target_class.id
    else:
        raw_class_str = (raw_class_id or "").strip()
        target_class_id = int(raw_class_str) if raw_class_str.isdigit() else None
        target_class = db.session.get(AcademicClass, target_class_id) if target_class_id else None
        if target_class and selected_year and target_class.school_year_id != selected_year.id:
            target_class = None
            target_class_id = None

    if target_class is not None and not user_can_view_class(current_user, target_class.id):
        record_audit_log(
            "ACCESS_DENIED",
            f"Accès refusé aux barèmes de {target_class.name} pour {current_user.username} ({current_user.role_label})",
            commit=True,
        )
        abort(403)

    if target_class is not None:
        can_edit = user_can_manage_class_subjects(current_user, target_class)
    else:
        can_edit = (
            has_permission(current_user, "subjects.manage")
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
    year_default_policy = (
        CalculationPolicy.query.filter_by(
            school_year_id=selected_year.id,
            class_id=None,
        ).first()
        if (selected_year and target_class_id is not None)
        else None
    )
    display_policy = policy or year_default_policy

    scale_warnings: list[str] = []

    if request.method == "POST":
        if not can_edit or selected_year is None:
            record_audit_log(
                "ACCESS_DENIED",
                f"Modification des barèmes/règles refusée sur {request.path} (class_id={target_class_id}) pour {current_user.username} ({current_user.role})",
                commit=True,
            )
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

    if display_policy is not None and display_policy.scale_items:
        chk = validate_grading_scale_intervals(
            [
                {
                    "min_score": str(it.min_score),
                    "max_score": str(it.max_score),
                    "appreciation": it.appreciation,
                }
                for it in display_policy.scale_items
            ],
            Decimal(str(display_policy.general_average_scale)),
        )
        scale_warnings = chk.warnings

    return render_template(
        "academics/policies_scales.html",
        all_years=all_years,
        selected_year=selected_year,
        year_classes=year_classes,
        target_class=target_class,
        policy=display_policy,
        has_class_override=(policy is not None and target_class is not None),
        using_inherited_policy=(policy is None and year_default_policy is not None and target_class is not None),
        scale_warnings=scale_warnings,
        can_edit=can_edit,
    )
