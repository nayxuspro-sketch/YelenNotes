"""Service de gestion des élèves, renumérotation et passage d'une année à l'autre (§7 CdC)."""

from __future__ import annotations

from datetime import date

from yelen_notes.extensions import db
from yelen_notes.models import AcademicClass, ClassEnrollment, Student, utcnow_naive


class StudentValidationError(ValueError):
    """Erreur fonctionnelle lors de la création ou modification d'une fiche élève."""


def normalize_last_name(raw: str) -> str:
    return " ".join((raw or "").strip().split()).upper()


def normalize_first_names(raw: str) -> str:
    parts = (raw or "").strip().split()
    return " ".join(p[:1].upper() + p[1:] if p else "" for p in parts)


def generate_student_code(reference_year: int | None = None) -> str:
    year_num = reference_year or date.today().year
    prefix = f"ELV-{year_num}-"
    last = (
        Student.query.filter(Student.unique_code.like(f"{prefix}%"))
        .order_by(Student.id.desc())
        .first()
    )
    next_seq = 1
    if last and last.unique_code.startswith(prefix):
        tail = last.unique_code[len(prefix) :]
        if tail.isdigit():
            next_seq = int(tail) + 1
    while True:
        code = f"{prefix}{next_seq:04d}"
        if not Student.query.filter_by(unique_code=code).first():
            return code
        next_seq += 1


def next_roll_number(class_id: int) -> int:
    max_roll = (
        db.session.query(db.func.max(ClassEnrollment.roll_number))
        .filter(ClassEnrollment.class_id == class_id)
        .scalar()
    )
    return (int(max_roll) + 1) if max_roll else 1


def validate_student_birth_date(birth_date: date, ref_date: date) -> int:
    """Vérifie la cohérence de la date de naissance et retourne l'âge exact à `ref_date`."""
    if birth_date >= ref_date:
        raise StudentValidationError(
            "La date de naissance doit être antérieure à la date de référence de l'année scolaire."
        )
    before_bday = (ref_date.month, ref_date.day) < (birth_date.month, birth_date.day)
    age = ref_date.year - birth_date.year - (1 if before_bday else 0)
    if age < 2 or age > 30:
        raise StudentValidationError(
            f"Date de naissance incohérente pour le primaire (âge calculé : {age} ans)."
        )
    return age


def create_and_enroll_student(
    *,
    academic_class: AcademicClass,
    last_name: str,
    first_names: str,
    sex: str,
    birth_date: date,
    roll_number: int | None = None,
    unique_code: str | None = None,
    schooling_status: str | None = None,
    repeated_class_detail: str | None = None,
    observations: str | None = None,
    is_active: bool = True,
    commit: bool = True,
) -> ClassEnrollment:
    """Crée une identité élève (ou rattache si matricule fourni) et l'inscrit dans la classe."""
    clean_last = normalize_last_name(last_name)
    clean_first = normalize_first_names(first_names)
    clean_sex = (sex or "").strip().upper()

    if not clean_last or not clean_first:
        raise StudentValidationError("Le nom et les prénoms de l'élève sont obligatoires.")
    if clean_sex not in ("M", "F"):
        raise StudentValidationError("Le sexe de l'élève doit être 'M' ou 'F'.")

    validate_student_birth_date(birth_date, academic_class.school_year.age_reference_date)

    # Contrôle anti-doublon dans la même classe
    existing_in_class = (
        ClassEnrollment.query.join(Student)
        .filter(
            ClassEnrollment.class_id == academic_class.id,
            Student.last_name == clean_last,
            db.func.lower(Student.first_names) == clean_first.lower(),
            Student.birth_date == birth_date,
        )
        .first()
    )
    if existing_in_class is not None:
        raise StudentValidationError(
            f"Doublon détecté : l'élève {clean_last} {clean_first} né(e) le "
            f"{birth_date.strftime('%d/%m/%Y')} est déjà inscrit(e) dans {academic_class.name} (N° {existing_in_class.roll_number})."
        )

    student = None
    if unique_code and unique_code.strip():
        student = Student.query.filter_by(unique_code=unique_code.strip()).first()

    if student is None:
        code = (unique_code or "").strip() or generate_student_code(
            academic_class.school_year.age_reference_date.year
        )
        student = Student(
            unique_code=code,
            last_name=clean_last,
            first_names=clean_first,
            sex=clean_sex,
            birth_date=birth_date,
        )
        db.session.add(student)
        db.session.flush()
    else:
        already_same_year = (
            ClassEnrollment.query.join(AcademicClass)
            .filter(
                ClassEnrollment.student_id == student.id,
                AcademicClass.school_year_id == academic_class.school_year_id,
            )
            .first()
        )
        if already_same_year is not None:
            raise StudentValidationError(
                f"Cet élève ({student.unique_code}) est déjà inscrit dans la classe "
                f"{already_same_year.academic_class.name} pour cette année scolaire."
            )

    assigned_roll = roll_number if (roll_number and roll_number > 0) else next_roll_number(academic_class.id)
    enrollment = ClassEnrollment(
        student_id=student.id,
        class_id=academic_class.id,
        roll_number=assigned_roll,
        schooling_status=(schooling_status or "").strip()[:120] or None,
        repeated_class_detail=(repeated_class_detail or "").strip()[:150] or None,
        observations=(observations or "").strip() or None,
        is_active=is_active,
    )
    db.session.add(enrollment)
    if commit:
        db.session.commit()
    return enrollment


def update_student_and_enrollment(
    *,
    enrollment: ClassEnrollment,
    last_name: str,
    first_names: str,
    sex: str,
    birth_date: date,
    roll_number: int | None = None,
    unique_code: str | None = None,
    schooling_status: str | None = None,
    repeated_class_detail: str | None = None,
    observations: str | None = None,
    commit: bool = True,
) -> dict[str, dict[str, str]]:
    """Met à jour l'identité (`Student`) et les informations scolaires (`ClassEnrollment`) d'un élève.

    Retourne le dictionnaire des champs effectivement modifiés (`{"champ": {"old": ..., "new": ...}}`).
    """
    ac = enrollment.academic_class
    student = enrollment.student

    clean_last = normalize_last_name(last_name)
    clean_first = normalize_first_names(first_names)
    clean_sex = (sex or "").strip().upper()
    clean_code = (unique_code or "").strip()[:40] if unique_code is not None else student.unique_code
    if not clean_code:
        clean_code = student.unique_code

    if not clean_last or not clean_first:
        raise StudentValidationError("Le nom et les prénoms de l'élève sont obligatoires.")
    if clean_sex not in ("M", "F"):
        raise StudentValidationError("Le sexe de l'élève doit être 'M' ou 'F'.")

    validate_student_birth_date(birth_date, ac.school_year.age_reference_date)

    if clean_code != student.unique_code:
        existing_code = Student.query.filter(
            Student.unique_code == clean_code,
            Student.id != student.id,
        ).first()
        if existing_code is not None:
            raise StudentValidationError(
                f"Le matricule « {clean_code} » est déjà attribué à l'élève {existing_code.full_name}."
            )

    duplicate_in_class = (
        ClassEnrollment.query.join(Student)
        .filter(
            ClassEnrollment.class_id == ac.id,
            ClassEnrollment.id != enrollment.id,
            Student.last_name == clean_last,
            db.func.lower(Student.first_names) == clean_first.lower(),
            Student.birth_date == birth_date,
        )
        .first()
    )
    if duplicate_in_class is not None:
        raise StudentValidationError(
            f"Doublon détecté : l'élève {clean_last} {clean_first} né(e) le "
            f"{birth_date.strftime('%d/%m/%Y')} figure déjà dans {ac.name} (N° {duplicate_in_class.roll_number})."
        )

    new_roll = roll_number if (roll_number is not None and roll_number > 0) else enrollment.roll_number
    new_schooling = (schooling_status or "").strip()[:120] or None
    new_repeated = (repeated_class_detail or "").strip()[:150] or None
    new_obs = (observations or "").strip() or None

    changes: dict[str, dict[str, str]] = {}

    def _track(field_label: str, old_val: object, new_val: object) -> None:
        old_str = "" if old_val is None else str(old_val)
        new_str = "" if new_val is None else str(new_val)
        if old_str != new_str:
            changes[field_label] = {"old": old_str, "new": new_str}

    _track("unique_code", student.unique_code, clean_code)
    _track("last_name", student.last_name, clean_last)
    _track("first_names", student.first_names, clean_first)
    _track("sex", student.sex, clean_sex)
    _track("birth_date", student.birth_date.isoformat(), birth_date.isoformat())
    _track("roll_number", enrollment.roll_number, new_roll)
    _track("schooling_status", enrollment.schooling_status, new_schooling)
    _track("repeated_class_detail", enrollment.repeated_class_detail, new_repeated)
    _track("observations", enrollment.observations, new_obs)

    if changes:
        student.unique_code = clean_code
        student.last_name = clean_last
        student.first_names = clean_first
        student.sex = clean_sex
        student.birth_date = birth_date
        student.updated_at = utcnow_naive()

        enrollment.roll_number = new_roll
        enrollment.schooling_status = new_schooling
        enrollment.repeated_class_detail = new_repeated
        enrollment.observations = new_obs
        enrollment.updated_at = utcnow_naive()

    if commit:
        db.session.commit()
    return changes


def renumber_class_alphabetically(academic_class: AcademicClass, commit: bool = True) -> int:
    """Renumérote de 1 à N par ordre alphabétique (actifs d'abord, puis inactifs conservés)."""
    enrollments = (
        ClassEnrollment.query.join(Student)
        .filter(ClassEnrollment.class_id == academic_class.id)
        .all()
    )
    sorted_enrollments = sorted(
        enrollments,
        key=lambda e: (
            0 if e.is_active else 1,
            e.student.last_name,
            e.student.first_names,
            e.student.birth_date,
        ),
    )
    for idx, enrollment in enumerate(sorted_enrollments, start=1):
        enrollment.roll_number = idx
        enrollment.updated_at = utcnow_naive()
    if commit:
        db.session.commit()
    return len(sorted_enrollments)


def toggle_enrollment_active_state(
    enrollment: ClassEnrollment,
    is_active: bool,
    reason: str | None = None,
    commit: bool = True,
) -> ClassEnrollment:
    """Active ou désactive un élève sans jamais supprimer ses notes ni son historique (§7.1 CdC)."""
    enrollment.is_active = is_active
    if not is_active:
        enrollment.deactivated_at = utcnow_naive()
        enrollment.deactivation_reason = (reason or "").strip()[:200] or "Désactivé en cours d'année"
    else:
        enrollment.deactivated_at = None
        enrollment.deactivation_reason = None
    enrollment.updated_at = utcnow_naive()
    if commit:
        db.session.commit()
    return enrollment


def promote_or_repeat_students(
    *,
    source_class: AcademicClass,
    target_class: AcademicClass,
    decisions_by_enrollment_id: dict[int, str],
    renumber_target: bool = True,
    commit: bool = True,
) -> dict[str, int]:
    """Transfère les élèves sélectionnés d'une année scolaire vers une classe de l'année suivante (§7.1 CdC).

    `decisions_by_enrollment_id` associe `enrollment.id` -> `'ADMIS'` | `'REDOUBLE'` | `'SORTI'`.
    Seuls `'ADMIS'` et `'REDOUBLE'` créent une inscription dans `target_class`.
    """
    if source_class.school_year_id == target_class.school_year_id:
        raise StudentValidationError(
            "Le passage annuel ou redoublement doit cibler une classe d'une autre année scolaire."
        )

    counts = {"enrolled": 0, "skipped_existing": 0, "marked_left": 0}
    current_roll = next_roll_number(target_class.id)

    for enr_id, decision in decisions_by_enrollment_id.items():
        enrollment = db.session.get(ClassEnrollment, enr_id)
        if enrollment is None or enrollment.class_id != source_class.id:
            continue

        clean_dec = (decision or "").strip().upper()
        if clean_dec not in ("ADMIS", "REDOUBLE", "SORTI", "IGNORES"):
            continue
        if clean_dec == "IGNORES":
            continue

        enrollment.annual_decision = clean_dec
        if clean_dec == "SORTI":
            counts["marked_left"] += 1
            continue

        # Vérifier si déjà inscrit dans l'année cible
        already = (
            ClassEnrollment.query.join(AcademicClass)
            .filter(
                ClassEnrollment.student_id == enrollment.student_id,
                AcademicClass.school_year_id == target_class.school_year_id,
            )
            .first()
        )
        if already is not None:
            counts["skipped_existing"] += 1
            continue

        repeated_info = enrollment.repeated_class_detail
        if clean_dec == "REDOUBLE":
            tag = f"{source_class.name} ({source_class.school_year.label})"
            repeated_info = f"{repeated_info} ; {tag}" if repeated_info else tag

        new_enr = ClassEnrollment(
            student_id=enrollment.student_id,
            class_id=target_class.id,
            roll_number=current_roll,
            schooling_status=enrollment.schooling_status,
            repeated_class_detail=repeated_info,
            observations=None,
            is_active=True,
        )
        current_roll += 1
        db.session.add(new_enr)
        counts["enrolled"] += 1

    db.session.flush()
    if renumber_target and counts["enrolled"] > 0:
        renumber_class_alphabetically(target_class, commit=False)
    if commit:
        db.session.commit()
    return counts
