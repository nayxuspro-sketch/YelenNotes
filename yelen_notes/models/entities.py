"""Modèles relationnels SQLAlchemy de Yelen Notes (15 tables conformes au dossier validé)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from flask_login import UserMixin
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from werkzeug.security import check_password_hash, generate_password_hash

from yelen_notes.extensions import db


def utcnow_naive() -> datetime:
    """Horodatage UTC sans fuseau pour compatibilité homogène SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Constantes métier
ROLE_ADMIN = "ADMIN"
ROLE_DIRECTEUR = "DIRECTEUR"
ROLE_ENSEIGNANT = "ENSEIGNANT"
ROLE_CONSULTATION = "CONSULTATION"
ALL_ROLES = (ROLE_ADMIN, ROLE_DIRECTEUR, ROLE_ENSEIGNANT, ROLE_CONSULTATION)

ROLE_LABELS = {
    ROLE_ADMIN: "Administrateur",
    ROLE_DIRECTEUR: "Directeur / Directrice",
    ROLE_ENSEIGNANT: "Enseignant titulaire",
    ROLE_CONSULTATION: "Consultation uniquement",
}

STATUS_BROUILLON = "BROUILLON"
STATUS_OUVERTE = "OUVERTE"
STATUS_CLOTUREE = "CLOTUREE"
LIFECYCLE_STATUSES = (STATUS_BROUILLON, STATUS_OUVERTE, STATUS_CLOTUREE)

GRADE_NON_SAISIE = "NON_SAISIE"
GRADE_NUMERIQUE = "NUMERIQUE"
GRADE_ABSENT = "ABSENT"
GRADE_DISPENSE = "DISPENSE"
GRADE_STATUSES = (GRADE_NON_SAISIE, GRADE_NUMERIQUE, GRADE_ABSENT, GRADE_DISPENSE)

ABSENCE_ZERO = "ZERO"
ABSENCE_NEUTRALIZE = "NEUTRALIZE"
ABSENCE_UNRANKED = "UNRANKED"
ABSENCE_POLICIES = (ABSENCE_ZERO, ABSENCE_NEUTRALIZE, ABSENCE_UNRANKED)

ROUNDING_HALF_UP = "ROUND_HALF_UP"
ROUNDING_HALF_EVEN = "ROUND_HALF_EVEN"
ROUNDING_DOWN = "ROUND_DOWN"
ROUNDING_MODES = (ROUNDING_HALF_UP, ROUNDING_HALF_EVEN, ROUNDING_DOWN)

TIE_COMPETITION = "COMPETITION"  # 1er, 1er ex, 3e
TIE_DENSE = "DENSE"              # 1er, 1er ex, 2e
TIE_MODES = (TIE_COMPETITION, TIE_DENSE)

ANNUAL_SIMPLE = "SIMPLE"
ANNUAL_WEIGHTED = "WEIGHTED"
ANNUAL_MODES = (ANNUAL_SIMPLE, ANNUAL_WEIGHTED)


class User(UserMixin, db.Model):
    """Compte utilisateur et rôle RBAC (§2 et §17 CdC)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'DIRECTEUR', 'ENSEIGNANT', 'CONSULTATION')",
            name="ck_users_role",
        ),
    )

    id = db.Column(Integer, primary_key=True)
    username = db.Column(String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(String(150), nullable=False)
    password_hash = db.Column(String(255), nullable=False)
    role = db.Column(String(20), nullable=False, default=ROLE_ENSEIGNANT)
    is_active = db.Column(Boolean, nullable=False, default=True)
    must_change_password = db.Column(Boolean, nullable=False, default=False)
    failed_login_count = db.Column(Integer, nullable=False, default=0)
    locked_until = db.Column(DateTime, nullable=True)
    last_login_at = db.Column(DateTime, nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    class_assignments = db.relationship(
        "TeacherClassAssignment",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password, method="scrypt")

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def is_locked(self, now: datetime | None = None) -> bool:
        ref = now or utcnow_naive()
        return bool(self.locked_until and self.locked_until > ref)

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)


class EstablishmentSettings(db.Model):
    """Paramètres généraux de l'établissement (singleton id=1, §3.1 et §14 CdC)."""

    __tablename__ = "establishment_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_establishment_singleton"),
        CheckConstraint("session_timeout_minutes >= 5", name="ck_establishment_timeout_min"),
        CheckConstraint("backup_retention_count >= 1", name="ck_establishment_backup_retention"),
    )

    id = db.Column(Integer, primary_key=True, default=1)
    country = db.Column(String(100), nullable=False, default="Burkina Faso")
    motto = db.Column(
        String(200),
        nullable=False,
        default="La Patrie ou la Mort, nous Vaincrons",
    )
    ceb = db.Column(String(150), nullable=False, default="")
    school_name = db.Column(String(200), nullable=False, default="")
    school_code = db.Column(String(50), nullable=True)
    logo_filename = db.Column(String(255), nullable=True)
    address = db.Column(String(255), nullable=True)
    locality = db.Column(String(120), nullable=True)
    phone = db.Column(String(60), nullable=True)
    email = db.Column(String(120), nullable=True)
    active_school_year_id = db.Column(
        Integer,
        ForeignKey("school_years.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_timeout_minutes = db.Column(Integer, nullable=False, default=30)
    auto_backup_enabled = db.Column(Boolean, nullable=False, default=True)
    auto_backup_interval_hours = db.Column(Integer, nullable=False, default=24)
    backup_directory = db.Column(String(500), nullable=True)
    backup_retention_count = db.Column(Integer, nullable=False, default=15)
    last_auto_backup_at = db.Column(DateTime, nullable=True)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    active_school_year = db.relationship("SchoolYear", foreign_keys=[active_school_year_id])

    @classmethod
    def get_singleton(cls) -> "EstablishmentSettings":
        instance = db.session.get(cls, 1)
        if instance is None:
            instance = cls(id=1)
            db.session.add(instance)
            db.session.commit()
        return instance


class SchoolYear(db.Model):
    """Année scolaire (§4.1 CdC)."""

    __tablename__ = "school_years"
    __table_args__ = (
        CheckConstraint(
            "status IN ('BROUILLON', 'OUVERTE', 'CLOTUREE')",
            name="ck_school_years_status",
        ),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR start_date <= end_date",
            name="ck_school_years_dates",
        ),
    )

    id = db.Column(Integer, primary_key=True)
    label = db.Column(String(32), unique=True, nullable=False)
    start_date = db.Column(Date, nullable=True)
    end_date = db.Column(Date, nullable=True)
    age_reference_date = db.Column(Date, nullable=False)
    status = db.Column(String(20), nullable=False, default=STATUS_OUVERTE)
    default_director_name = db.Column(String(150), nullable=True)
    default_director_title = db.Column(String(100), nullable=False, default="Le Directeur")
    closed_at = db.Column(DateTime, nullable=True)
    closed_by_id = db.Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    classes = db.relationship(
        "AcademicClass",
        back_populates="school_year",
        order_by="AcademicClass.display_order, AcademicClass.name",
    )
    periods = db.relationship(
        "EvaluationPeriod",
        back_populates="school_year",
        order_by="EvaluationPeriod.display_order",
    )
    policies = db.relationship(
        "CalculationPolicy",
        back_populates="school_year",
        cascade="all, delete-orphan",
    )

    @property
    def is_closed(self) -> bool:
        return self.status == STATUS_CLOTUREE


class AcademicClass(db.Model):
    """Classe rattachée à une année scolaire avec historisation direction/titulaire (§3.2, §4.2 CdC)."""

    __tablename__ = "academic_classes"
    __table_args__ = (
        UniqueConstraint("school_year_id", "name", name="uq_academic_class_year_name"),
    )

    id = db.Column(Integer, primary_key=True)
    school_year_id = db.Column(
        Integer,
        ForeignKey("school_years.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name = db.Column(String(60), nullable=False)
    level_label = db.Column(String(30), nullable=True)
    director_name = db.Column(String(150), nullable=False)
    director_title = db.Column(String(100), nullable=False, default="Le Directeur")
    titulaire_name = db.Column(String(150), nullable=False)
    display_order = db.Column(Integer, nullable=False, default=1)
    is_active = db.Column(Boolean, nullable=False, default=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    school_year = db.relationship("SchoolYear", back_populates="classes")
    subjects = db.relationship(
        "Subject",
        back_populates="academic_class",
        order_by="Subject.display_order, Subject.name",
    )
    enrollments = db.relationship(
        "ClassEnrollment",
        back_populates="academic_class",
        order_by="ClassEnrollment.roll_number",
    )
    teacher_assignments = db.relationship(
        "TeacherClassAssignment",
        back_populates="academic_class",
        cascade="all, delete-orphan",
    )
    policy_override = db.relationship(
        "CalculationPolicy",
        back_populates="academic_class",
        uselist=False,
    )


class TeacherClassAssignment(db.Model):
    """Affectation d'un compte enseignant à une classe donnée (§2 CdC)."""

    __tablename__ = "teacher_class_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "class_id", name="uq_teacher_class_assignment"),
    )

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = db.Column(
        Integer,
        ForeignKey("academic_classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    can_edit_grades = db.Column(Boolean, nullable=False, default=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)

    user = db.relationship("User", back_populates="class_assignments")
    academic_class = db.relationship("AcademicClass", back_populates="teacher_assignments")


class EvaluationPeriod(db.Model):
    """Période d'évaluation configurable (§4.3 CdC)."""

    __tablename__ = "evaluation_periods"
    __table_args__ = (
        UniqueConstraint("school_year_id", "name", name="uq_period_year_name"),
        UniqueConstraint("school_year_id", "display_order", name="uq_period_year_order"),
        CheckConstraint(
            "status IN ('BROUILLON', 'OUVERTE', 'CLOTUREE')",
            name="ck_periods_status",
        ),
        CheckConstraint("annual_weight > 0", name="ck_periods_weight_positive"),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR start_date <= end_date",
            name="ck_periods_dates",
        ),
    )

    id = db.Column(Integer, primary_key=True)
    school_year_id = db.Column(
        Integer,
        ForeignKey("school_years.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name = db.Column(String(100), nullable=False)
    short_code = db.Column(String(20), nullable=False)
    display_order = db.Column(Integer, nullable=False)
    annual_weight = db.Column(Numeric(8, 3), nullable=False, default=Decimal("1.000"))
    start_date = db.Column(Date, nullable=True)
    end_date = db.Column(Date, nullable=True)
    status = db.Column(String(20), nullable=False, default=STATUS_BROUILLON)
    closed_at = db.Column(DateTime, nullable=True)
    closed_by_id = db.Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    school_year = db.relationship("SchoolYear", back_populates="periods")

    @property
    def is_open_for_entry(self) -> bool:
        return self.status == STATUS_OUVERTE and not self.school_year.is_closed


class CalculationPolicy(db.Model):
    """Politique de calcul et d'arrondi rattachée à une année scolaire ou à une classe (§9.3 CdC)."""

    __tablename__ = "calculation_policies"
    __table_args__ = (
        UniqueConstraint("school_year_id", "class_id", name="uq_policy_year_class"),
        CheckConstraint("general_average_scale > 0", name="ck_policy_scale_positive"),
        CheckConstraint("decimal_places >= 0 AND decimal_places <= 4", name="ck_policy_decimals"),
        CheckConstraint(
            "rounding_mode IN ('ROUND_HALF_UP', 'ROUND_HALF_EVEN', 'ROUND_DOWN')",
            name="ck_policy_rounding",
        ),
        CheckConstraint(
            "absence_policy IN ('ZERO', 'NEUTRALIZE', 'UNRANKED')",
            name="ck_policy_absence",
        ),
        CheckConstraint(
            "tie_breaking_mode IN ('COMPETITION', 'DENSE')",
            name="ck_policy_tie_breaking",
        ),
        CheckConstraint(
            "annual_average_mode IN ('SIMPLE', 'WEIGHTED')",
            name="ck_policy_annual_mode",
        ),
    )

    id = db.Column(Integer, primary_key=True)
    school_year_id = db.Column(
        Integer,
        ForeignKey("school_years.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    class_id = db.Column(
        Integer,
        ForeignKey("academic_classes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    general_average_scale = db.Column(Numeric(8, 2), nullable=False)
    decimal_places = db.Column(Integer, nullable=False, default=2)
    rounding_mode = db.Column(String(25), nullable=False, default=ROUNDING_HALF_UP)
    absence_policy = db.Column(String(25), nullable=False, default=ABSENCE_ZERO)
    tie_breaking_mode = db.Column(String(25), nullable=False, default=TIE_COMPETITION)
    annual_average_mode = db.Column(String(25), nullable=False, default=ANNUAL_SIMPLE)
    annual_require_all_periods = db.Column(Boolean, nullable=False, default=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    school_year = db.relationship("SchoolYear", back_populates="policies")
    academic_class = db.relationship("AcademicClass", back_populates="policy_override")
    scale_items = db.relationship(
        "GradingScaleItem",
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="GradingScaleItem.display_order, GradingScaleItem.min_score",
    )


class GradingScaleItem(db.Model):
    """Intervalle de la grille des appréciations (§6 CdC)."""

    __tablename__ = "grading_scale_items"
    __table_args__ = (
        CheckConstraint("min_score >= 0 AND max_score > min_score", name="ck_scale_bounds"),
    )

    id = db.Column(Integer, primary_key=True)
    policy_id = db.Column(
        Integer,
        ForeignKey("calculation_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    min_score = db.Column(Numeric(8, 3), nullable=False)
    max_score = db.Column(Numeric(8, 3), nullable=False)
    appreciation = db.Column(String(100), nullable=False)
    is_last_interval = db.Column(Boolean, nullable=False, default=False)
    display_order = db.Column(Integer, nullable=False, default=1)

    policy = db.relationship("CalculationPolicy", back_populates="scale_items")


class Subject(db.Model):
    """Matière ou élément d'évaluation configuré pour une classe (§5 CdC)."""

    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("class_id", "name", name="uq_subject_class_name"),
        CheckConstraint("coefficient > 0", name="ck_subject_coef_positive"),
        CheckConstraint("min_score >= 0", name="ck_subject_min_non_negative"),
        CheckConstraint("max_score > min_score", name="ck_subject_max_gt_min"),
        CheckConstraint("reference_scale > 0", name="ck_subject_scale_positive"),
    )

    id = db.Column(Integer, primary_key=True)
    class_id = db.Column(
        Integer,
        ForeignKey("academic_classes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name = db.Column(String(120), nullable=False)
    short_label = db.Column(String(30), nullable=True)
    coefficient = db.Column(Numeric(8, 3), nullable=False, default=Decimal("1.000"))
    min_score = db.Column(Numeric(8, 3), nullable=False, default=Decimal("0.000"))
    max_score = db.Column(Numeric(8, 3), nullable=False)
    reference_scale = db.Column(Numeric(8, 3), nullable=False)
    include_in_average = db.Column(Boolean, nullable=False, default=True)
    allow_exemption = db.Column(Boolean, nullable=False, default=True)
    display_order = db.Column(Integer, nullable=False, default=1)
    is_active = db.Column(Boolean, nullable=False, default=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    academic_class = db.relationship("AcademicClass", back_populates="subjects")


class Student(db.Model):
    """Identité permanente d'un élève conservée sur plusieurs années scolaires (§7 CdC)."""

    __tablename__ = "students"
    __table_args__ = (
        CheckConstraint("sex IN ('M', 'F')", name="ck_student_sex"),
        Index("idx_students_identity", "last_name", "first_names", "birth_date"),
    )

    id = db.Column(Integer, primary_key=True)
    unique_code = db.Column(String(40), unique=True, nullable=False, index=True)
    last_name = db.Column(String(100), nullable=False)
    first_names = db.Column(String(150), nullable=False)
    sex = db.Column(String(1), nullable=False)
    birth_date = db.Column(Date, nullable=False)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    enrollments = db.relationship(
        "ClassEnrollment",
        back_populates="student",
        order_by="ClassEnrollment.created_at.desc()",
    )

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_names}".strip()

    def age_at(self, ref_date: date) -> int:
        """Calcule l'âge exact en années révolues à `ref_date` (§7.1 CdC)."""
        before_birthday = (ref_date.month, ref_date.day) < (
            self.birth_date.month,
            self.birth_date.day,
        )
        return ref_date.year - self.birth_date.year - (1 if before_birthday else 0)


class ClassEnrollment(db.Model):
    """Inscription d'un élève dans une classe pour une année donnée (§7, §7.1 CdC)."""

    __tablename__ = "class_enrollments"
    __table_args__ = (
        UniqueConstraint("class_id", "student_id", name="uq_enrollment_class_student"),
        CheckConstraint("roll_number > 0", name="ck_enrollment_roll_positive"),
    )

    id = db.Column(Integer, primary_key=True)
    student_id = db.Column(
        Integer,
        ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    class_id = db.Column(
        Integer,
        ForeignKey("academic_classes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    roll_number = db.Column(Integer, nullable=False)
    schooling_status = db.Column(String(120), nullable=True)
    repeated_class_detail = db.Column(String(150), nullable=True)
    observations = db.Column(Text, nullable=True)
    is_active = db.Column(Boolean, nullable=False, default=True)
    deactivated_at = db.Column(DateTime, nullable=True)
    deactivation_reason = db.Column(String(200), nullable=True)
    annual_decision = db.Column(String(40), nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    student = db.relationship("Student", back_populates="enrollments")
    academic_class = db.relationship("AcademicClass", back_populates="enrollments")
    grades = db.relationship("Grade", back_populates="enrollment")
    period_observations = db.relationship(
        "StudentPeriodObservation",
        back_populates="enrollment",
        cascade="all, delete-orphan",
    )


class Grade(db.Model):
    """Note d'un élève pour une période et une matière (§8, §8.3 CdC)."""

    __tablename__ = "grades"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "period_id", "subject_id", name="uq_grade_cell"),
        CheckConstraint(
            "status IN ('NON_SAISIE', 'NUMERIQUE', 'ABSENT', 'DISPENSE')",
            name="ck_grade_status",
        ),
        CheckConstraint(
            "(status = 'NUMERIQUE' AND numeric_value IS NOT NULL) OR "
            "(status IN ('NON_SAISIE', 'ABSENT', 'DISPENSE') AND numeric_value IS NULL)",
            name="ck_grade_value_consistency",
        ),
    )

    id = db.Column(Integer, primary_key=True)
    enrollment_id = db.Column(
        Integer,
        ForeignKey("class_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    period_id = db.Column(
        Integer,
        ForeignKey("evaluation_periods.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subject_id = db.Column(
        Integer,
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status = db.Column(String(15), nullable=False, default=GRADE_NON_SAISIE)
    numeric_value = db.Column(Numeric(8, 3), nullable=True)
    comment = db.Column(String(255), nullable=True)
    version = db.Column(Integer, nullable=False, default=1)
    entered_by_id = db.Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by_id = db.Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    enrollment = db.relationship("ClassEnrollment", back_populates="grades")
    period = db.relationship("EvaluationPeriod")
    subject = db.relationship("Subject")
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    histories = db.relationship(
        "GradeHistory",
        back_populates="grade",
        cascade="all, delete-orphan",
        order_by="GradeHistory.changed_at.desc()",
    )


class GradeHistory(db.Model):
    """Historique horodaté des saisies et corrections de notes (§8.2 CdC)."""

    __tablename__ = "grade_histories"

    id = db.Column(Integer, primary_key=True)
    grade_id = db.Column(Integer, ForeignKey("grades.id", ondelete="CASCADE"), nullable=False, index=True)
    enrollment_id = db.Column(Integer, nullable=False, index=True)
    period_id = db.Column(Integer, nullable=False, index=True)
    subject_id = db.Column(Integer, nullable=False, index=True)
    old_status = db.Column(String(15), nullable=True)
    old_value = db.Column(Numeric(8, 3), nullable=True)
    new_status = db.Column(String(15), nullable=False)
    new_value = db.Column(Numeric(8, 3), nullable=True)
    changed_by_id = db.Column(Integer, ForeignKey("users.id"), nullable=True)
    changed_by_username = db.Column(String(64), nullable=False)
    changed_at = db.Column(DateTime, nullable=False, default=utcnow_naive)

    grade = db.relationship("Grade", back_populates="histories")


class StudentPeriodObservation(db.Model):
    """Observations du titulaire et du directeur sur bulletin périodique ou annuel (§12 CdC)."""

    __tablename__ = "student_period_observations"
    __table_args__ = (
        CheckConstraint("scope IN ('PERIODE', 'ANNUEL')", name="ck_obs_scope"),
        UniqueConstraint("enrollment_id", "period_id", "scope", name="uq_student_obs"),
    )

    id = db.Column(Integer, primary_key=True)
    enrollment_id = db.Column(
        Integer,
        ForeignKey("class_enrollments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period_id = db.Column(
        Integer,
        ForeignKey("evaluation_periods.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scope = db.Column(String(15), nullable=False, default="PERIODE")
    teacher_observation = db.Column(Text, nullable=True)
    director_observation = db.Column(Text, nullable=True)
    honors_mention = db.Column(String(100), nullable=True)
    updated_by_id = db.Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)

    enrollment = db.relationship("ClassEnrollment", back_populates="period_observations")
    period = db.relationship("EvaluationPeriod")


class AuditLog(db.Model):
    """Journal de sécurité et des opérations sensibles (§17 CdC)."""

    __tablename__ = "audit_logs"

    id = db.Column(Integer, primary_key=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive, index=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username_snapshot = db.Column(String(64), nullable=False, default="système")
    role_snapshot = db.Column(String(20), nullable=True)
    ip_address = db.Column(String(64), nullable=True)
    action = db.Column(String(60), nullable=False, index=True)
    target_type = db.Column(String(60), nullable=True)
    target_id = db.Column(String(60), nullable=True)
    summary = db.Column(String(300), nullable=False)
    details_json = db.Column(Text, nullable=True)


class BackupRecord(db.Model):
    """Registre des sauvegardes créées et vérifiées (§14 CdC)."""

    __tablename__ = "backup_records"

    id = db.Column(Integer, primary_key=True)
    created_at = db.Column(DateTime, nullable=False, default=utcnow_naive, index=True)
    filename = db.Column(String(255), nullable=False)
    file_path = db.Column(String(500), nullable=False)
    size_bytes = db.Column(Integer, nullable=False)
    sha256_hash = db.Column(String(64), nullable=False)
    trigger_type = db.Column(String(30), nullable=False, default="MANUAL")
    integrity_verified = db.Column(Boolean, nullable=False, default=True)
    app_version = db.Column(String(32), nullable=False)
    schema_version = db.Column(String(32), nullable=False)
    created_by_username = db.Column(String(64), nullable=False, default="système")
    notes = db.Column(String(255), nullable=True)
