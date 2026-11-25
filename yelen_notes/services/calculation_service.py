"""Moteur mathématique et pédagogique de Yelen Notes (§5, §6, §8.3, §9, §10, §11, §12 CdC).

Toutes les opérations utilisent `decimal.Decimal` afin d'éviter toute dérive d'arrondi binaire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP
from typing import Iterable

from yelen_notes.models import (
    ABSENCE_NEUTRALIZE,
    ABSENCE_UNRANKED,
    ABSENCE_ZERO,
    ANNUAL_SIMPLE,
    ANNUAL_WEIGHTED,
    GRADE_ABSENT,
    GRADE_DISPENSE,
    GRADE_NON_SAISIE,
    GRADE_NUMERIQUE,
    ROUNDING_DOWN,
    ROUNDING_HALF_EVEN,
    ROUNDING_HALF_UP,
    TIE_COMPETITION,
    TIE_DENSE,
    AcademicClass,
    CalculationPolicy,
    ClassEnrollment,
    EvaluationPeriod,
    Grade,
    GradingScaleItem,
    StudentPeriodObservation,
    Subject,
)

ROUNDING_MAP = {
    ROUNDING_HALF_UP: ROUND_HALF_UP,
    ROUNDING_HALF_EVEN: ROUND_HALF_EVEN,
    ROUNDING_DOWN: ROUND_DOWN,
}


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def quantize_value(
    value: Decimal,
    decimal_places: int = 2,
    rounding_mode: str = ROUNDING_HALF_UP,
) -> Decimal:
    exponent = Decimal("1") if decimal_places <= 0 else Decimal("1." + ("0" * decimal_places))
    mode = ROUNDING_MAP.get(rounding_mode, ROUND_HALF_UP)
    return to_decimal(value).quantize(exponent, rounding=mode)


def format_decimal_fr(
    value: Decimal | None,
    decimal_places: int = 2,
    rounding_mode: str = ROUNDING_HALF_UP,
    empty_placeholder: str = "—",
) -> str:
    """Formate un nombre décimal selon la convention française (virgule décimale)."""
    if value is None:
        return empty_placeholder
    q = quantize_value(value, decimal_places, rounding_mode)
    return str(q).replace(".", ",")


def format_compact_decimal_fr(value: Decimal | None) -> str:
    """Formate un barème ou coefficient sans zéros inutiles (ex. 10.000 -> '10', 1.500 -> '1,5')."""
    if value is None:
        return "—"
    normalized = to_decimal(value).normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def format_french_rank(rank: int | None, sex: str, is_tie: bool) -> str:
    """Formate le rang scolaire en français (ex. '1er', '1re', '1er ex', '2e', '2e ex')."""
    if rank is None:
        return "Non classé"
    if rank == 1:
        base = "1re" if sex == "F" else "1er"
    else:
        base = f"{rank}e"
    return f"{base} ex" if is_tie else base


@dataclass
class ScaleValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_grading_scale_intervals(
    raw_intervals: list[dict],
    general_scale: Decimal,
) -> ScaleValidationResult:
    """Contrôle l'absence de chevauchements et signale tout intervalle manquant (§6 CdC)."""
    errors: list[str] = []
    warnings: list[str] = []
    scale = to_decimal(general_scale)

    if not raw_intervals:
        return ScaleValidationResult(
            is_valid=True,
            errors=[],
            warnings=["Aucun seuil d'appréciation n'est encore défini pour ce barème."],
        )

    parsed: list[tuple[int, Decimal, Decimal, str]] = []
    for idx, item in enumerate(raw_intervals, start=1):
        label = str(item.get("appreciation", "")).strip()
        if not label:
            errors.append(f"Ligne {idx} : le libellé d'appréciation est obligatoire.")
        try:
            low = to_decimal(item["min_score"])
            high = to_decimal(item["max_score"])
        except Exception:
            errors.append(f"Ligne {idx} : bornes numériques invalides.")
            continue

        if low < Decimal("0"):
            errors.append(f"Ligne {idx} (« {label} ») : la borne minimale ({low}) ne peut pas être négative.")
        if high <= low:
            errors.append(
                f"Ligne {idx} (« {label} ») : la borne maximale ({high}) doit être strictement supérieure à la borne minimale ({low})."
            )
        if high > scale:
            errors.append(
                f"Ligne {idx} (« {label} ») : la borne maximale ({high}) dépasse le barème général ({scale})."
            )
        parsed.append((idx, low, high, label))

    if errors:
        return ScaleValidationResult(is_valid=False, errors=errors, warnings=warnings)

    parsed.sort(key=lambda t: (t[1], t[2]))

    for i in range(len(parsed) - 1):
        _, low_a, high_a, label_a = parsed[i]
        _, low_b, high_b, label_b = parsed[i + 1]
        if high_a > low_b:
            errors.append(
                f"Chevauchement détecté entre « {label_a} » ([{low_a} ; {high_a}[) "
                f"et « {label_b} » ([{low_b} ; {high_b}[)."
            )
        elif high_a < low_b:
            warnings.append(
                f"Intervalle manquant détecté entre {format_compact_decimal_fr(high_a)} "
                f"et {format_compact_decimal_fr(low_b)}."
            )

    first_low = parsed[0][1]
    last_high = parsed[-1][2]
    if first_low > Decimal("0"):
        warnings.append(
            f"Les moyennes comprises entre 0 et {format_compact_decimal_fr(first_low)} ne sont pas couvertes."
        )
    if last_high < scale:
        warnings.append(
            f"Les moyennes comprises entre {format_compact_decimal_fr(last_high)} et "
            f"{format_compact_decimal_fr(scale)} ne sont pas couvertes."
        )

    return ScaleValidationResult(is_valid=(len(errors) == 0), errors=errors, warnings=warnings)


def lookup_appreciation(
    score: Decimal | None,
    scale_items: Iterable[GradingScaleItem],
    general_scale: Decimal | None = None,
) -> str:
    """Retourne l'appréciation correspondant à `score`.

    Convention de bornes (§6 CdC) :
    - [min_score, max_score[ (borne basse incluse, borne haute exclue) pour les intervalles courants
    - [min_score, max_score] (bornes basse et haute incluses) pour le dernier intervalle supérieur
    """
    if score is None:
        return ""
    items = sorted(scale_items, key=lambda it: (to_decimal(it.min_score), to_decimal(it.max_score)))
    if not items:
        return ""
    val = to_decimal(score)
    last_index = len(items) - 1
    for idx, item in enumerate(items):
        low = to_decimal(item.min_score)
        high = to_decimal(item.max_score)
        is_last = bool(item.is_last_interval) or (idx == last_index)
        if is_last:
            if low <= val <= high:
                return item.appreciation
        else:
            if low <= val < high:
                return item.appreciation
    # Tolérance si légère égalité au sommet du barème
    if general_scale is not None and val == to_decimal(general_scale):
        top_item = items[-1]
        if to_decimal(top_item.max_score) == to_decimal(general_scale):
            return top_item.appreciation
    return ""


def resolve_policy_for_class(academic_class: AcademicClass) -> CalculationPolicy | None:
    """Cherche d'abord une politique propre à la classe, puis la politique générale de l'année."""
    class_policy = CalculationPolicy.query.filter_by(
        school_year_id=academic_class.school_year_id,
        class_id=academic_class.id,
    ).first()
    if class_policy is not None:
        return class_policy
    return CalculationPolicy.query.filter_by(
        school_year_id=academic_class.school_year_id,
        class_id=None,
    ).first()


def convert_subject_score(
    raw_value: Decimal,
    subject: Subject,
    general_scale: Decimal,
) -> tuple[Decimal, Decimal]:
    """Convertit une note brute `raw_value` vers (moyenne_sur_matiere, moyenne_sur_bareme_general)."""
    x = to_decimal(raw_value)
    min_s = to_decimal(subject.min_score)
    max_s = to_decimal(subject.max_score)
    ref_s = to_decimal(subject.reference_scale)
    gen_s = to_decimal(general_scale)

    if min_s == Decimal("0"):
        on_subject_scale = (x * ref_s) / max_s
    else:
        on_subject_scale = ((x - min_s) / (max_s - min_s)) * ref_s

    if ref_s == gen_s:
        on_general_scale = on_subject_scale
    else:
        on_general_scale = (on_subject_scale * gen_s) / ref_s

    return on_subject_scale, on_general_scale


@dataclass
class SubjectGradeComputed:
    subject: Subject
    status: str
    raw_value: Decimal | None
    on_subject_scale: Decimal | None
    on_general_scale: Decimal | None
    coefficient_applied: Decimal
    weighted_points: Decimal | None
    appreciation: str
    display_raw: str
    display_on_ref: str
    display_points: str


@dataclass
class StudentPeriodComputed:
    enrollment: ClassEnrollment
    age: int
    subject_cells: dict[int, SubjectGradeComputed]
    entered_count: int
    missing_count: int
    absent_count: int
    dispensed_count: int
    total_coefficients: Decimal
    total_weighted_points: Decimal
    raw_average: Decimal | None
    rounded_average: Decimal | None
    display_average: str
    appreciation: str
    is_complete: bool
    is_rankable: bool
    unranked_reason: str
    rank: int | None = None
    is_tie: bool = False
    rank_label: str = "Non classé"
    teacher_observation: str = ""
    director_observation: str = ""
    honors_mention: str = ""


@dataclass
class SubjectClassStats:
    subject: Subject
    evaluated_count: int
    missing_count: int
    absent_count: int
    dispensed_count: int
    average_on_ref: Decimal | None
    min_on_ref: Decimal | None
    max_on_ref: Decimal | None


@dataclass
class PeriodClassResults:
    academic_class: AcademicClass
    period: EvaluationPeriod
    policy: CalculationPolicy | None
    general_scale: Decimal
    decimal_places: int
    rounding_mode: str
    subjects: list[Subject]
    active_rows: list[StudentPeriodComputed]
    ranked_rows: list[StudentPeriodComputed]
    unranked_active_rows: list[StudentPeriodComputed]
    inactive_rows: list[StudentPeriodComputed]
    all_rows_by_enrollment: dict[int, StudentPeriodComputed]
    subject_stats: dict[int, SubjectClassStats]
    active_count: int
    girls_count: int
    boys_count: int
    ranked_count: int
    incomplete_count: int
    completion_rate_percent: Decimal
    class_average: Decimal | None
    min_average: Decimal | None
    max_average: Decimal | None
    pass_count: int
    pass_rate_percent: Decimal | None


def _assign_ranks(
    rankable_items: list,
    score_getter,
    sex_getter,
    rank_setter,
    tie_mode: str = TIE_COMPETITION,
) -> list:
    """Trie par moyenne décroissante et attribue les rangs (COMPETITION avec saut ou DENSE sans saut)."""
    sorted_items = sorted(
        rankable_items,
        key=lambda item: (
            -score_getter(item),
            item.enrollment.student.last_name,
            item.enrollment.student.first_names,
        ),
    )

    scores = [score_getter(it) for it in sorted_items]
    score_counts: dict[Decimal, int] = {}
    for sc in scores:
        score_counts[sc] = score_counts.get(sc, 0) + 1

    current_rank = 0
    prev_score: Decimal | None = None
    for idx, item in enumerate(sorted_items, start=1):
        sc = score_getter(item)
        if prev_score is None or sc != prev_score:
            if tie_mode == TIE_DENSE:
                current_rank = (current_rank + 1) if prev_score is not None else 1
            else:
                current_rank = idx
            prev_score = sc
        is_tie = score_counts[sc] > 1
        rank_setter(item, current_rank, is_tie, format_french_rank(current_rank, sex_getter(item), is_tie))
    return sorted_items


def compute_period_class_results(
    academic_class: AcademicClass,
    period: EvaluationPeriod,
) -> PeriodClassResults:
    """Calcule l'intégralité des notes, moyennes, rangs et statistiques d'une classe sur une période."""
    policy = resolve_policy_for_class(academic_class)
    subjects = (
        Subject.query.filter_by(class_id=academic_class.id, is_active=True)
        .order_by(Subject.display_order, Subject.name)
        .all()
    )

    if policy is not None:
        general_scale = to_decimal(policy.general_average_scale)
        decimal_places = policy.decimal_places
        rounding_mode = policy.rounding_mode
        absence_policy = policy.absence_policy
        tie_mode = policy.tie_breaking_mode
        scale_items = list(policy.scale_items)
    else:
        # Si l'administrateur n'a pas encore créé de politique sur base vierge, repli neutre de lecture
        fallback_scale = to_decimal(subjects[0].reference_scale) if subjects else Decimal("10.00")
        general_scale = fallback_scale
        decimal_places = 2
        rounding_mode = ROUNDING_HALF_UP
        absence_policy = ABSENCE_ZERO
        tie_mode = TIE_COMPETITION
        scale_items = []

    enrollments = (
        ClassEnrollment.query.filter_by(class_id=academic_class.id)
        .order_by(ClassEnrollment.roll_number, ClassEnrollment.id)
        .all()
    )
    enrollment_ids = [e.id for e in enrollments]
    subject_ids = [s.id for s in subjects]

    grades_map: dict[tuple[int, int], Grade] = {}
    if enrollment_ids and subject_ids:
        grades_q = Grade.query.filter(
            Grade.period_id == period.id,
            Grade.enrollment_id.in_(enrollment_ids),
            Grade.subject_id.in_(subject_ids),
        ).all()
        for g in grades_q:
            grades_map[(g.enrollment_id, g.subject_id)] = g

    obs_map: dict[int, StudentPeriodObservation] = {}
    if enrollment_ids:
        obs_q = StudentPeriodObservation.query.filter(
            StudentPeriodObservation.period_id == period.id,
            StudentPeriodObservation.scope == "PERIODE",
            StudentPeriodObservation.enrollment_id.in_(enrollment_ids),
        ).all()
        for ob in obs_q:
            obs_map[ob.enrollment_id] = ob

    ref_date = academic_class.school_year.age_reference_date

    # Accumulateurs statistiques par matière (sur élèves actifs)
    subj_values_active: dict[int, list[Decimal]] = {s.id: [] for s in subjects}
    subj_missing_active: dict[int, int] = {s.id: 0 for s in subjects}
    subj_absent_active: dict[int, int] = {s.id: 0 for s in subjects}
    subj_dispensed_active: dict[int, int] = {s.id: 0 for s in subjects}

    active_rows: list[StudentPeriodComputed] = []
    inactive_rows: list[StudentPeriodComputed] = []
    all_rows_by_enrollment: dict[int, StudentPeriodComputed] = {}

    total_expected_cells = 0
    total_filled_cells = 0

    for enrollment in enrollments:
        student = enrollment.student
        age = student.age_at(ref_date)
        cells: dict[int, SubjectGradeComputed] = {}

        entered_count = 0
        missing_count = 0
        absent_count = 0
        dispensed_count = 0
        has_unranked_absence = False

        sum_coef = Decimal("0")
        sum_points = Decimal("0")

        for subj in subjects:
            grade = grades_map.get((enrollment.id, subj.id))
            status = grade.status if grade is not None else GRADE_NON_SAISIE
            coef = to_decimal(subj.coefficient)

            if enrollment.is_active:
                total_expected_cells += 1
                if status != GRADE_NON_SAISIE:
                    total_filled_cells += 1

            if status == GRADE_NUMERIQUE and grade is not None and grade.numeric_value is not None:
                entered_count += 1
                raw_val = to_decimal(grade.numeric_value)
                on_subj, on_gen = convert_subject_score(raw_val, subj, general_scale)
                subj_app = lookup_appreciation(
                    quantize_value(on_gen, decimal_places, rounding_mode),
                    scale_items,
                    general_scale,
                )
                if subj.include_in_average:
                    pts = on_gen * coef
                    sum_coef += coef
                    sum_points += pts
                    coef_used = coef
                else:
                    pts = None
                    coef_used = Decimal("0")

                if enrollment.is_active:
                    subj_values_active[subj.id].append(on_subj)

                cells[subj.id] = SubjectGradeComputed(
                    subject=subj,
                    status=status,
                    raw_value=raw_val,
                    on_subject_scale=on_subj,
                    on_general_scale=on_gen,
                    coefficient_applied=coef_used,
                    weighted_points=pts,
                    appreciation=subj_app,
                    display_raw=format_decimal_fr(raw_val, decimal_places, rounding_mode),
                    display_on_ref=format_decimal_fr(on_subj, decimal_places, rounding_mode),
                    display_points=format_decimal_fr(pts, decimal_places, rounding_mode),
                )

            elif status == GRADE_ABSENT:
                entered_count += 1
                absent_count += 1
                if enrollment.is_active:
                    subj_absent_active[subj.id] += 1

                if subj.include_in_average and absence_policy == ABSENCE_ZERO:
                    sum_coef += coef
                    coef_used = coef
                    pts = Decimal("0")
                    on_subj = Decimal("0")
                    on_gen = Decimal("0")
                    if enrollment.is_active:
                        subj_values_active[subj.id].append(Decimal("0"))
                elif subj.include_in_average and absence_policy == ABSENCE_UNRANKED:
                    has_unranked_absence = True
                    coef_used = Decimal("0")
                    pts = None
                    on_subj = None
                    on_gen = None
                else:
                    # ABSENCE_NEUTRALIZE ou matière hors moyenne
                    coef_used = Decimal("0")
                    pts = None
                    on_subj = None
                    on_gen = None

                cells[subj.id] = SubjectGradeComputed(
                    subject=subj,
                    status=status,
                    raw_value=None,
                    on_subject_scale=on_subj,
                    on_general_scale=on_gen,
                    coefficient_applied=coef_used,
                    weighted_points=pts,
                    appreciation="Absent(e)",
                    display_raw="Abs.",
                    display_on_ref="0,00" if (pts is not None and absence_policy == ABSENCE_ZERO) else "Abs.",
                    display_points=format_decimal_fr(pts, decimal_places, rounding_mode) if pts is not None else "Abs.",
                )

            elif status == GRADE_DISPENSE:
                entered_count += 1
                dispensed_count += 1
                if enrollment.is_active:
                    subj_dispensed_active[subj.id] += 1
                cells[subj.id] = SubjectGradeComputed(
                    subject=subj,
                    status=status,
                    raw_value=None,
                    on_subject_scale=None,
                    on_general_scale=None,
                    coefficient_applied=Decimal("0"),
                    weighted_points=None,
                    appreciation="Dispensé(e)",
                    display_raw="Disp.",
                    display_on_ref="Disp.",
                    display_points="—",
                )

            else:
                # NON_SAISIE : jamais transformée en zéro (§8.3 CdC) !
                if subj.include_in_average:
                    missing_count += 1
                if enrollment.is_active:
                    subj_missing_active[subj.id] += 1
                cells[subj.id] = SubjectGradeComputed(
                    subject=subj,
                    status=GRADE_NON_SAISIE,
                    raw_value=None,
                    on_subject_scale=None,
                    on_general_scale=None,
                    coefficient_applied=Decimal("0"),
                    weighted_points=None,
                    appreciation="",
                    display_raw="—",
                    display_on_ref="—",
                    display_points="—",
                )

        # Détermination de la complétude et de l'éligibilité au classement
        is_complete = len(subjects) > 0 and missing_count == 0
        unranked_reason = ""
        raw_avg: Decimal | None = None
        rounded_avg: Decimal | None = None
        is_rankable = False

        if not enrollment.is_active:
            unranked_reason = "Élève inactif"
        elif not subjects:
            unranked_reason = "Aucune matière configurée"
        elif missing_count > 0:
            unranked_reason = (
                f"{missing_count} note{'s' if missing_count > 1 else ''} non saisie{'s' if missing_count > 1 else ''}"
            )
        elif has_unranked_absence:
            unranked_reason = "Absence non classable"
        elif sum_coef <= Decimal("0"):
            unranked_reason = "Aucune matière comptabilisée"
        else:
            raw_avg = sum_points / sum_coef
            rounded_avg = quantize_value(raw_avg, decimal_places, rounding_mode)
            is_rankable = True

        # Si l'élève est inactif mais avait toutes ses notes avant désactivation, conserver sa moyenne historique
        if not enrollment.is_active and subjects and missing_count == 0 and not has_unranked_absence and sum_coef > Decimal("0"):
            raw_avg = sum_points / sum_coef
            rounded_avg = quantize_value(raw_avg, decimal_places, rounding_mode)

        gen_appreciation = (
            lookup_appreciation(rounded_avg, scale_items, general_scale)
            if rounded_avg is not None
            else ""
        )

        obs = obs_map.get(enrollment.id)
        row = StudentPeriodComputed(
            enrollment=enrollment,
            age=age,
            subject_cells=cells,
            entered_count=entered_count,
            missing_count=missing_count,
            absent_count=absent_count,
            dispensed_count=dispensed_count,
            total_coefficients=sum_coef,
            total_weighted_points=quantize_value(sum_points, decimal_places, rounding_mode) if sum_coef > 0 else Decimal("0"),
            raw_average=raw_avg,
            rounded_average=rounded_avg,
            display_average=format_decimal_fr(rounded_avg, decimal_places, rounding_mode),
            appreciation=gen_appreciation,
            is_complete=is_complete,
            is_rankable=is_rankable,
            unranked_reason=unranked_reason,
            rank_label="Inactif" if not enrollment.is_active else ("Non classé" if not is_rankable else ""),
            teacher_observation=(obs.teacher_observation or "") if obs else "",
            director_observation=(obs.director_observation or "") if obs else "",
            honors_mention=(obs.honors_mention or "") if obs else "",
        )

        all_rows_by_enrollment[enrollment.id] = row
        if enrollment.is_active:
            active_rows.append(row)
        else:
            inactive_rows.append(row)

    rankable_candidates = [r for r in active_rows if r.is_rankable and r.rounded_average is not None]
    unranked_active_rows = [r for r in active_rows if not r.is_rankable]

    def _set_period_rank(item: StudentPeriodComputed, rk: int, tie: bool, label: str) -> None:
        item.rank = rk
        item.is_tie = tie
        item.rank_label = label

    ranked_rows = _assign_ranks(
        rankable_candidates,
        score_getter=lambda r: r.rounded_average,  # type: ignore[return-value]
        sex_getter=lambda r: r.enrollment.student.sex,
        rank_setter=_set_period_rank,
        tie_mode=tie_mode,
    )

    # Statistiques de classe
    girls_count = sum(1 for r in active_rows if r.enrollment.student.sex == "F")
    boys_count = sum(1 for r in active_rows if r.enrollment.student.sex == "M")
    incomplete_count = sum(1 for r in active_rows if not r.is_complete)

    if total_expected_cells > 0:
        completion_rate = quantize_value(
            (Decimal(total_filled_cells) * Decimal("100")) / Decimal(total_expected_cells),
            1,
            ROUNDING_HALF_UP,
        )
    else:
        completion_rate = Decimal("0.0")

    if ranked_rows:
        avg_list = [r.rounded_average for r in ranked_rows if r.rounded_average is not None]
        class_avg = quantize_value(sum(avg_list) / Decimal(len(avg_list)), decimal_places, rounding_mode)
        min_avg = min(avg_list)
        max_avg = max(avg_list)
        pass_threshold = general_scale / Decimal("2")
        pass_count = sum(1 for a in avg_list if a >= pass_threshold)
        pass_rate = quantize_value(
            (Decimal(pass_count) * Decimal("100")) / Decimal(len(avg_list)),
            1,
            ROUNDING_HALF_UP,
        )
    else:
        class_avg = None
        min_avg = None
        max_avg = None
        pass_count = 0
        pass_rate = None

    subject_stats: dict[int, SubjectClassStats] = {}
    for subj in subjects:
        vals = subj_values_active[subj.id]
        if vals:
            s_avg = quantize_value(sum(vals) / Decimal(len(vals)), decimal_places, rounding_mode)
            s_min = quantize_value(min(vals), decimal_places, rounding_mode)
            s_max = quantize_value(max(vals), decimal_places, rounding_mode)
        else:
            s_avg = None
            s_min = None
            s_max = None
        subject_stats[subj.id] = SubjectClassStats(
            subject=subj,
            evaluated_count=len(vals),
            missing_count=subj_missing_active[subj.id],
            absent_count=subj_absent_active[subj.id],
            dispensed_count=subj_dispensed_active[subj.id],
            average_on_ref=s_avg,
            min_on_ref=s_min,
            max_on_ref=s_max,
        )

    return PeriodClassResults(
        academic_class=academic_class,
        period=period,
        policy=policy,
        general_scale=general_scale,
        decimal_places=decimal_places,
        rounding_mode=rounding_mode,
        subjects=subjects,
        active_rows=active_rows,
        ranked_rows=ranked_rows,
        unranked_active_rows=unranked_active_rows,
        inactive_rows=inactive_rows,
        all_rows_by_enrollment=all_rows_by_enrollment,
        subject_stats=subject_stats,
        active_count=len(active_rows),
        girls_count=girls_count,
        boys_count=boys_count,
        ranked_count=len(ranked_rows),
        incomplete_count=incomplete_count,
        completion_rate_percent=completion_rate,
        class_average=class_avg,
        min_average=min_avg,
        max_average=max_avg,
        pass_count=pass_count,
        pass_rate_percent=pass_rate,
    )


@dataclass
class AnnualSubjectSummary:
    subject: Subject
    period_values_on_ref: dict[int, Decimal | None]
    period_displays_on_ref: dict[int, str]
    annual_subject_average: Decimal | None
    display_annual_subject_average: str
    appreciation: str


@dataclass
class StudentAnnualComputed:
    enrollment: ClassEnrollment
    age: int
    period_averages: dict[int, Decimal | None]
    period_displays: dict[int, str]
    period_ranks: dict[int, str]
    subject_summaries: dict[int, AnnualSubjectSummary]
    evaluated_periods_count: int
    raw_annual_average: Decimal | None
    rounded_annual_average: Decimal | None
    display_annual_average: str
    appreciation: str
    is_rankable: bool
    unranked_reason: str
    rank: int | None = None
    is_tie: bool = False
    rank_label: str = "Non classé"
    teacher_observation: str = ""
    director_observation: str = ""
    honors_mention: str = ""


@dataclass
class AnnualClassResults:
    academic_class: AcademicClass
    periods: list[EvaluationPeriod]
    policy: CalculationPolicy | None
    general_scale: Decimal
    decimal_places: int
    rounding_mode: str
    annual_mode: str
    subjects: list[Subject]
    period_results_map: dict[int, PeriodClassResults]
    active_rows: list[StudentAnnualComputed]
    ranked_rows: list[StudentAnnualComputed]
    unranked_active_rows: list[StudentAnnualComputed]
    inactive_rows: list[StudentAnnualComputed]
    all_rows_by_enrollment: dict[int, StudentAnnualComputed]
    class_annual_average: Decimal | None
    min_annual_average: Decimal | None
    max_annual_average: Decimal | None
    pass_count: int
    pass_rate_percent: Decimal | None


def compute_annual_class_results(
    academic_class: AcademicClass,
    periods: list[EvaluationPeriod] | None = None,
) -> AnnualClassResults:
    """Calcule les moyennes annuelles, le classement annuel et le récapitulatif par période (§11, §12.3 CdC)."""
    policy = resolve_policy_for_class(academic_class)
    if periods is None:
        periods = (
            EvaluationPeriod.query.filter_by(school_year_id=academic_class.school_year_id)
            .order_by(EvaluationPeriod.display_order)
            .all()
        )

    subjects = (
        Subject.query.filter_by(class_id=academic_class.id, is_active=True)
        .order_by(Subject.display_order, Subject.name)
        .all()
    )

    if policy is not None:
        general_scale = to_decimal(policy.general_average_scale)
        decimal_places = policy.decimal_places
        rounding_mode = policy.rounding_mode
        tie_mode = policy.tie_breaking_mode
        annual_mode = policy.annual_average_mode
        require_all_periods = policy.annual_require_all_periods
        scale_items = list(policy.scale_items)
    else:
        general_scale = to_decimal(subjects[0].reference_scale) if subjects else Decimal("10.00")
        decimal_places = 2
        rounding_mode = ROUNDING_HALF_UP
        tie_mode = TIE_COMPETITION
        annual_mode = ANNUAL_SIMPLE
        require_all_periods = True
        scale_items = []

    period_results_map: dict[int, PeriodClassResults] = {
        p.id: compute_period_class_results(academic_class, p) for p in periods
    }

    enrollments = (
        ClassEnrollment.query.filter_by(class_id=academic_class.id)
        .order_by(ClassEnrollment.roll_number, ClassEnrollment.id)
        .all()
    )
    enrollment_ids = [e.id for e in enrollments]
    annual_obs_map: dict[int, StudentPeriodObservation] = {}
    if enrollment_ids:
        obs_q = StudentPeriodObservation.query.filter(
            StudentPeriodObservation.scope == "ANNUEL",
            StudentPeriodObservation.enrollment_id.in_(enrollment_ids),
        ).all()
        for ob in obs_q:
            annual_obs_map[ob.enrollment_id] = ob

    ref_date = academic_class.school_year.age_reference_date
    active_rows: list[StudentAnnualComputed] = []
    inactive_rows: list[StudentAnnualComputed] = []
    all_rows_by_enrollment: dict[int, StudentAnnualComputed] = {}

    for enrollment in enrollments:
        age = enrollment.student.age_at(ref_date)
        p_avgs: dict[int, Decimal | None] = {}
        p_displays: dict[int, str] = {}
        p_ranks: dict[int, str] = {}

        valid_period_pairs: list[tuple[EvaluationPeriod, Decimal]] = []

        for p in periods:
            p_res = period_results_map[p.id]
            st_row = p_res.all_rows_by_enrollment.get(enrollment.id)
            if st_row and st_row.rounded_average is not None:
                p_avgs[p.id] = st_row.rounded_average
                p_displays[p.id] = st_row.display_average
                p_ranks[p.id] = st_row.rank_label
                valid_period_pairs.append((p, st_row.rounded_average))
            else:
                p_avgs[p.id] = None
                p_displays[p.id] = "—"
                p_ranks[p.id] = st_row.unranked_reason if st_row and st_row.unranked_reason else "—"

        # Récapitulatif annuel par matière
        subj_summaries: dict[int, AnnualSubjectSummary] = {}
        for subj in subjects:
            s_vals_by_p: dict[int, Decimal | None] = {}
            s_disp_by_p: dict[int, str] = {}
            weighted_num = Decimal("0")
            weighted_den = Decimal("0")
            for p in periods:
                p_res = period_results_map[p.id]
                st_row = p_res.all_rows_by_enrollment.get(enrollment.id)
                cell = st_row.subject_cells.get(subj.id) if st_row else None
                if cell and cell.on_subject_scale is not None:
                    s_vals_by_p[p.id] = cell.on_subject_scale
                    s_disp_by_p[p.id] = cell.display_on_ref
                    w = to_decimal(p.annual_weight) if annual_mode == ANNUAL_WEIGHTED else Decimal("1")
                    weighted_num += cell.on_subject_scale * w
                    weighted_den += w
                else:
                    s_vals_by_p[p.id] = None
                    s_disp_by_p[p.id] = cell.display_raw if cell else "—"

            if weighted_den > Decimal("0") and (
                not require_all_periods or len([v for v in s_vals_by_p.values() if v is not None]) == len(periods)
            ):
                ann_subj_avg = quantize_value(weighted_num / weighted_den, decimal_places, rounding_mode)
                # Projection sur barème général pour appréciation de matière
                on_gen = (ann_subj_avg * general_scale) / to_decimal(subj.reference_scale)
                s_app = lookup_appreciation(
                    quantize_value(on_gen, decimal_places, rounding_mode),
                    scale_items,
                    general_scale,
                )
            elif weighted_den > Decimal("0"):
                ann_subj_avg = quantize_value(weighted_num / weighted_den, decimal_places, rounding_mode)
                on_gen = (ann_subj_avg * general_scale) / to_decimal(subj.reference_scale)
                s_app = lookup_appreciation(
                    quantize_value(on_gen, decimal_places, rounding_mode),
                    scale_items,
                    general_scale,
                )
            else:
                ann_subj_avg = None
                s_app = ""

            subj_summaries[subj.id] = AnnualSubjectSummary(
                subject=subj,
                period_values_on_ref=s_vals_by_p,
                period_displays_on_ref=s_disp_by_p,
                annual_subject_average=ann_subj_avg,
                display_annual_subject_average=format_decimal_fr(ann_subj_avg, decimal_places, rounding_mode),
                appreciation=s_app,
            )

        # Moyenne annuelle générale
        raw_annual: Decimal | None = None
        rounded_annual: Decimal | None = None
        is_rankable = False
        unranked_reason = ""

        if not periods:
            unranked_reason = "Aucune période configurée"
        elif not enrollment.is_active:
            unranked_reason = "Élève inactif"
        elif require_all_periods and len(valid_period_pairs) < len(periods):
            missing_p = len(periods) - len(valid_period_pairs)
            unranked_reason = f"{missing_p} période{'s' if missing_p > 1 else ''} incomplète{'s' if missing_p > 1 else ''}"
        elif not valid_period_pairs:
            unranked_reason = "Aucune période évaluée"
        else:
            if annual_mode == ANNUAL_WEIGHTED:
                num = sum(avg * to_decimal(p.annual_weight) for p, avg in valid_period_pairs)
                den = sum(to_decimal(p.annual_weight) for p, _ in valid_period_pairs)
                raw_annual = num / den
            else:
                raw_annual = sum(avg for _, avg in valid_period_pairs) / Decimal(len(valid_period_pairs))
            rounded_annual = quantize_value(raw_annual, decimal_places, rounding_mode)
            is_rankable = True

        ann_appreciation = (
            lookup_appreciation(rounded_annual, scale_items, general_scale)
            if rounded_annual is not None
            else ""
        )
        obs = annual_obs_map.get(enrollment.id)

        annual_row = StudentAnnualComputed(
            enrollment=enrollment,
            age=age,
            period_averages=p_avgs,
            period_displays=p_displays,
            period_ranks=p_ranks,
            subject_summaries=subj_summaries,
            evaluated_periods_count=len(valid_period_pairs),
            raw_annual_average=raw_annual,
            rounded_annual_average=rounded_annual,
            display_annual_average=format_decimal_fr(rounded_annual, decimal_places, rounding_mode),
            appreciation=ann_appreciation,
            is_rankable=is_rankable,
            unranked_reason=unranked_reason,
            rank_label="Inactif" if not enrollment.is_active else ("Non classé" if not is_rankable else ""),
            teacher_observation=(obs.teacher_observation or "") if obs else "",
            director_observation=(obs.director_observation or "") if obs else "",
            honors_mention=(obs.honors_mention or "") if obs else "",
        )
        all_rows_by_enrollment[enrollment.id] = annual_row
        if enrollment.is_active:
            active_rows.append(annual_row)
        else:
            inactive_rows.append(annual_row)

    rankable_candidates = [r for r in active_rows if r.is_rankable and r.rounded_annual_average is not None]
    unranked_active_rows = [r for r in active_rows if not r.is_rankable]

    def _set_annual_rank(item: StudentAnnualComputed, rk: int, tie: bool, label: str) -> None:
        item.rank = rk
        item.is_tie = tie
        item.rank_label = label

    ranked_rows = _assign_ranks(
        rankable_candidates,
        score_getter=lambda r: r.rounded_annual_average,  # type: ignore[return-value]
        sex_getter=lambda r: r.enrollment.student.sex,
        rank_setter=_set_annual_rank,
        tie_mode=tie_mode,
    )

    if ranked_rows:
        avg_list = [r.rounded_annual_average for r in ranked_rows if r.rounded_annual_average is not None]
        c_avg = quantize_value(sum(avg_list) / Decimal(len(avg_list)), decimal_places, rounding_mode)
        c_min = min(avg_list)
        c_max = max(avg_list)
        pass_threshold = general_scale / Decimal("2")
        p_count = sum(1 for a in avg_list if a >= pass_threshold)
        p_rate = quantize_value((Decimal(p_count) * Decimal("100")) / Decimal(len(avg_list)), 1, ROUNDING_HALF_UP)
    else:
        c_avg = None
        c_min = None
        c_max = None
        p_count = 0
        p_rate = None

    return AnnualClassResults(
        academic_class=academic_class,
        periods=periods,
        policy=policy,
        general_scale=general_scale,
        decimal_places=decimal_places,
        rounding_mode=rounding_mode,
        annual_mode=annual_mode,
        subjects=subjects,
        period_results_map=period_results_map,
        active_rows=active_rows,
        ranked_rows=ranked_rows,
        unranked_active_rows=unranked_active_rows,
        inactive_rows=inactive_rows,
        all_rows_by_enrollment=all_rows_by_enrollment,
        class_annual_average=c_avg,
        min_annual_average=c_min,
        max_annual_average=c_max,
        pass_count=p_count,
        pass_rate_percent=p_rate,
    )
