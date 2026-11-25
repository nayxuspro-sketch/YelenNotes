"""Service de validation, d'enregistrement concurrent et d'historisation des notes (§8 CdC)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from yelen_notes.extensions import db
from yelen_notes.models import (
    GRADE_ABSENT,
    GRADE_DISPENSE,
    GRADE_NON_SAISIE,
    GRADE_NUMERIQUE,
    ClassEnrollment,
    EvaluationPeriod,
    Grade,
    GradeHistory,
    StudentPeriodObservation,
    Subject,
    User,
    utcnow_naive,
)
from yelen_notes.services.calculation_service import format_compact_decimal_fr


class GradeValidationError(ValueError):
    """Levée lorsqu'une note saisie viole le barème ou les règles de la matière."""


@dataclass
class GradeConflictError(Exception):
    """Levée lorsqu'un autre poste a modifié la même note simultanément (§19 test n°5 CdC)."""

    enrollment_id: int
    subject_id: int
    period_id: int
    current_version: int
    current_display: str
    updated_by: str

    def __str__(self) -> str:
        return (
            f"Conflit de modification simultanée : cette note a été modifiée par "
            f"{self.updated_by} (valeur actuelle : {self.current_display})."
        )


def format_grade_raw_input(grade: Grade | None) -> str:
    """Retourne la chaîne éditable dans une cellule de saisie."""
    if grade is None or grade.status == GRADE_NON_SAISIE:
        return ""
    if grade.status == GRADE_ABSENT:
        return "ABS"
    if grade.status == GRADE_DISPENSE:
        return "DISP"
    if grade.numeric_value is not None:
        return format_compact_decimal_fr(Decimal(str(grade.numeric_value)))
    return ""


def parse_grade_input(raw_text: str | None, subject: Subject) -> tuple[str, Decimal | None]:
    """Analyse une saisie clavier/formulaire sans jamais confondre vide, 0, ABS et DISP (§8.3 CdC)."""
    cleaned = (raw_text or "").strip()
    if cleaned == "":
        return GRADE_NON_SAISIE, None

    upper = cleaned.upper()
    if upper in ("A", "ABS", "ABS.", "ABSENT", "ABSENTE"):
        return GRADE_ABSENT, None

    if upper in ("D", "DISP", "DISP.", "DISPENSE", "DISPENSÉ", "DISPENSEE", "DISPENSÉE", "NC"):
        if not subject.allow_exemption:
            raise GradeValidationError(
                f"La dispense n'est pas autorisée pour la matière « {subject.name} »."
            )
        return GRADE_DISPENSE, None

    normalized = cleaned.replace(",", ".").replace(" ", "")
    try:
        numeric = Decimal(normalized)
    except InvalidOperation as exc:
        raise GradeValidationError(
            f"Valeur « {cleaned} » invalide pour « {subject.name} ». Saisissez un nombre, ABS ou DISP."
        ) from exc

    if not numeric.is_finite():
        raise GradeValidationError(f"Valeur numérique non valide pour « {subject.name} ».")

    min_s = Decimal(str(subject.min_score))
    max_s = Decimal(str(subject.max_score))
    if numeric < min_s or numeric > max_s:
        raise GradeValidationError(
            f"La note {cleaned} pour « {subject.name} » est hors barème "
            f"(autorisé : de {format_compact_decimal_fr(min_s)} à {format_compact_decimal_fr(max_s)})."
        )

    quantized = numeric.quantize(Decimal("0.001"))
    return GRADE_NUMERIQUE, quantized


def upsert_single_grade(
    *,
    enrollment: ClassEnrollment,
    period: EvaluationPeriod,
    subject: Subject,
    raw_input: str | None,
    actor: User,
    expected_version: int | None = None,
    force_overwrite: bool = False,
    comment: str | None = None,
    commit: bool = True,
) -> tuple[Grade, bool]:
    """Valide et enregistre une note avec contrôle de concurrence et journalisation historique."""
    if enrollment.class_id != subject.class_id:
        raise GradeValidationError("La matière n'appartient pas à la classe de l'élève.")
    if enrollment.academic_class.school_year_id != period.school_year_id:
        raise GradeValidationError("La période ne correspond pas à l'année scolaire de la classe.")
    if not period.is_open_for_entry:
        raise GradeValidationError(
            f"La période « {period.name} » n'est pas ouverte à la saisie (statut : {period.status})."
        )

    new_status, new_val = parse_grade_input(raw_input, subject)

    grade = Grade.query.filter_by(
        enrollment_id=enrollment.id,
        period_id=period.id,
        subject_id=subject.id,
    ).first()

    if grade is None:
        if expected_version not in (None, 0) and not force_overwrite:
            raise GradeConflictError(
                enrollment_id=enrollment.id,
                subject_id=subject.id,
                period_id=period.id,
                current_version=0,
                current_display="Non saisie",
                updated_by="un autre utilisateur",
            )
        grade = Grade(
            enrollment_id=enrollment.id,
            period_id=period.id,
            subject_id=subject.id,
            status=new_status,
            numeric_value=new_val,
            comment=(comment or "").strip()[:255] or None,
            version=1,
            entered_by_id=actor.id,
            updated_by_id=actor.id,
        )
        db.session.add(grade)
        db.session.flush()

        changed = new_status != GRADE_NON_SAISIE
        if changed:
            db.session.add(
                GradeHistory(
                    grade_id=grade.id,
                    enrollment_id=enrollment.id,
                    period_id=period.id,
                    subject_id=subject.id,
                    old_status=GRADE_NON_SAISIE,
                    old_value=None,
                    new_status=new_status,
                    new_value=new_val,
                    changed_by_id=actor.id,
                    changed_by_username=actor.username,
                    changed_at=utcnow_naive(),
                )
            )
        if commit:
            db.session.commit()
        return grade, changed

    # Vérification du verrou optimiste
    if (
        expected_version is not None
        and not force_overwrite
        and expected_version != grade.version
    ):
        updater_name = grade.updated_by.username if grade.updated_by else "un autre poste"
        raise GradeConflictError(
            enrollment_id=enrollment.id,
            subject_id=subject.id,
            period_id=period.id,
            current_version=grade.version,
            current_display=format_grade_raw_input(grade) or "Non saisie",
            updated_by=updater_name,
        )

    old_status = grade.status
    old_val = Decimal(str(grade.numeric_value)) if grade.numeric_value is not None else None
    same_value = (old_status == new_status) and (old_val == new_val)

    if comment is not None:
        grade.comment = comment.strip()[:255] or None

    if not same_value:
        grade.status = new_status
        grade.numeric_value = new_val
        grade.version = (grade.version or 1) + 1
        grade.updated_by_id = actor.id
        grade.updated_at = utcnow_naive()
        db.session.flush()
        db.session.add(
            GradeHistory(
                grade_id=grade.id,
                enrollment_id=enrollment.id,
                period_id=period.id,
                subject_id=subject.id,
                old_status=old_status,
                old_value=old_val,
                new_status=new_status,
                new_value=new_val,
                changed_by_id=actor.id,
                changed_by_username=actor.username,
                changed_at=utcnow_naive(),
            )
        )

    if commit:
        db.session.commit()
    return grade, (not same_value)


def save_student_observation(
    *,
    enrollment: ClassEnrollment,
    period: EvaluationPeriod | None,
    scope: str,
    teacher_observation: str | None,
    director_observation: str | None,
    honors_mention: str | None,
    actor: User,
    commit: bool = True,
) -> StudentPeriodObservation:
    """Enregistre les observations et mentions destinées au bulletin périodique ou annuel."""
    period_id = period.id if (scope == "PERIODE" and period is not None) else None
    obs = StudentPeriodObservation.query.filter_by(
        enrollment_id=enrollment.id,
        period_id=period_id,
        scope=scope,
    ).first()
    if obs is None:
        obs = StudentPeriodObservation(
            enrollment_id=enrollment.id,
            period_id=period_id,
            scope=scope,
        )
        db.session.add(obs)
    obs.teacher_observation = (teacher_observation or "").strip() or None
    obs.director_observation = (director_observation or "").strip() or None
    obs.honors_mention = (honors_mention or "").strip()[:100] or None
    obs.updated_by_id = actor.id
    obs.updated_at = utcnow_naive()
    if commit:
        db.session.commit()
    return obs
