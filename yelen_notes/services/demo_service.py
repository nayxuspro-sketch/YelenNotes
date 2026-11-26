"""Générateur de la base de démonstration séparée (`ecole_demo.db`) clairement identifiée (§20 CdC).

IMPORTANT : Ce module n'est JAMAIS appelé sur la base de production `ecole.db`,
laquelle demeure 100 % vierge pour être librement configurée par l'établissement.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from PIL import Image, ImageDraw

from yelen_notes.extensions import db
from yelen_notes.models import (
    ABSENCE_ZERO,
    ANNUAL_SIMPLE,
    ROLE_ADMIN,
    ROLE_ADMIN_ETAB,
    ROLE_COMPTABLE,
    ROLE_CONSULTATION,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    ROLE_PARENT,
    ROLE_SECRETAIRE,
    ROLE_SURVEILLANT,
    ROUNDING_HALF_UP,
    STATUS_BROUILLON,
    STATUS_CLOTUREE,
    STATUS_OUVERTE,
    TIE_COMPETITION,
    AcademicClass,
    AccountingEntry,
    CalculationPolicy,
    EstablishmentSettings,
    EvaluationPeriod,
    GradingScaleItem,
    ParentStudentLink,
    SchoolYear,
    StudentAbsenceRecord,
    Subject,
    TeacherClassAssignment,
    User,
    utcnow_naive,
)
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import ensure_rbac_initialized, invalidate_rbac_cache
from yelen_notes.services.grade_service import save_student_observation, upsert_single_grade
from yelen_notes.services.student_service import (
    create_and_enroll_student,
    promote_or_repeat_students,
    toggle_enrollment_active_state,
)


def _create_demo_logo(logos_dir: Path) -> str:
    """Crée un petit emblème PNG local hors ligne pour illustrer les bulletins de démonstration."""
    logos_dir.mkdir(parents=True, exist_ok=True)
    filename = "logo_demo_yelen.png"
    target = logos_dir / filename
    img = Image.new("RGBA", (180, 180), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((10, 10, 170, 170), fill=(31, 78, 120, 255), outline=(18, 46, 74, 255), width=5)
    draw.ellipse((32, 32, 148, 148), fill=(245, 196, 65, 255))
    draw.polygon([(90, 48), (102, 80), (136, 80), (108, 100), (118, 132), (90, 112), (62, 132), (72, 100), (44, 80), (78, 80)], fill=(31, 78, 120, 255))
    img.save(target, format="PNG")
    return filename


def populate_demo_database(paths: RuntimePaths) -> dict[str, str]:
    """Peuple une base `ecole_demo.db` avec des données fictives complètes et identifiées."""
    if User.query.count() > 0:
        return {"status": "already_populated", "db_path": str(paths.db_path)}

    logo_name = _create_demo_logo(paths.logos_dir)

    # 1. Comptes de démonstration pour les 4 profils RBAC
    admin = User(
        username="admin_demo",
        full_name="KABRÉ Souleymane (Admin Démo)",
        role=ROLE_ADMIN,
        is_active=True,
    )
    admin.set_password("AdminDemo2026!")

    directeur = User(
        username="directeur_demo",
        full_name="OUÉDRAOGO Issiaka (Directeur Démo)",
        role=ROLE_DIRECTEUR,
        is_active=True,
    )
    directeur.set_password("Directeur2026!")

    enseignant = User(
        username="titulaire_cm2",
        full_name="SAWADOGO Rasmata (Titulaire CM2 Démo)",
        role=ROLE_ENSEIGNANT,
        is_active=True,
    )
    enseignant.set_password("Enseignant2026!")

    lecteur = User(
        username="consultation_demo",
        full_name="ZONGO Adama (Consultation Démo)",
        role=ROLE_CONSULTATION,
        is_active=True,
    )
    lecteur.set_password("Consult2026!")

    db.session.add_all([admin, directeur, enseignant, lecteur])
    db.session.flush()

    # 2. Année N-1 clôturée (pour illustrer l'historisation direction/titulaire et le passage de classe)
    year_prev = SchoolYear(
        label="2025-2026",
        start_date=date(2025, 10, 1),
        end_date=date(2026, 6, 30),
        age_reference_date=date(2025, 10, 1),
        status=STATUS_OUVERTE,
        default_director_name="Mme TRAORÉ Mariam (Ancienne Directrice — Fictif)",
        default_director_title="La Directrice",
    )
    db.session.add(year_prev)
    db.session.flush()

    pol_prev = CalculationPolicy(
        school_year_id=year_prev.id,
        class_id=None,
        general_average_scale=Decimal("10.00"),
        decimal_places=2,
        rounding_mode=ROUNDING_HALF_UP,
        absence_policy=ABSENCE_ZERO,
        tie_breaking_mode=TIE_COMPETITION,
        annual_average_mode=ANNUAL_SIMPLE,
        annual_require_all_periods=True,
    )
    db.session.add(pol_prev)
    db.session.flush()
    for order, (lo, hi, lbl, is_last) in enumerate(
        [
            ("0", "5", "Insuffisant", False),
            ("5", "6", "Passable", False),
            ("6", "7", "Assez bien", False),
            ("7", "8.5", "Bien", False),
            ("8.5", "10", "Très bien", True),
        ],
        start=1,
    ):
        db.session.add(
            GradingScaleItem(
                policy_id=pol_prev.id,
                min_score=Decimal(lo),
                max_score=Decimal(hi),
                appreciation=lbl,
                is_last_interval=is_last,
                display_order=order,
            )
        )

    p_prev_1 = EvaluationPeriod(
        school_year_id=year_prev.id,
        name="Trimestre unique d'archive (Démo)",
        short_code="T1",
        display_order=1,
        status=STATUS_OUVERTE,
    )
    db.session.add(p_prev_1)

    cm1_prev = AcademicClass(
        school_year_id=year_prev.id,
        name="CM1",
        level_label="CM1",
        director_name="Mme TRAORÉ Mariam (Ancienne Directrice — Fictif)",
        director_title="La Directrice",
        titulaire_name="M. ILBOUDO Karim (Fictif)",
        display_order=1,
    )
    db.session.add(cm1_prev)
    db.session.flush()

    s_prev_lec = Subject(
        class_id=cm1_prev.id,
        name="Lecture",
        short_label="Lect.",
        coefficient=Decimal("2"),
        min_score=Decimal("0"),
        max_score=Decimal("10"),
        reference_scale=Decimal("10"),
        display_order=1,
    )
    s_prev_cal = Subject(
        class_id=cm1_prev.id,
        name="Opérations & Problèmes",
        short_label="Calc.",
        coefficient=Decimal("2"),
        min_score=Decimal("0"),
        max_score=Decimal("20"),
        reference_scale=Decimal("10"),
        display_order=2,
    )
    db.session.add_all([s_prev_lec, s_prev_cal])
    db.session.flush()

    prev_pupils = [
        ("KABORÉ", "Aminata", "F", date(2015, 3, 14), "Public", "8.5", "17"),
        ("OUÉDRAOGO", "Moussa", "M", date(2015, 7, 22), "Public", "8.5", "17"),
        ("SAWADOGO", "Nafissatou", "F", date(2014, 11, 5), "Public", "7.0", "14"),
        ("COMPAORÉ", "Ibrahim", "M", date(2015, 1, 30), "Public", "4.0", "8"),
    ]
    prev_enrollments = []
    for ln, fn, sx, bd, sc, g1, g2 in prev_pupils:
        enr = create_and_enroll_student(
            academic_class=cm1_prev,
            last_name=ln,
            first_names=fn,
            sex=sx,
            birth_date=bd,
            schooling_status=sc,
            commit=False,
        )
        db.session.flush()
        upsert_single_grade(
            enrollment=enr, period=p_prev_1, subject=s_prev_lec, raw_input=g1, actor=admin, commit=False
        )
        upsert_single_grade(
            enrollment=enr, period=p_prev_1, subject=s_prev_cal, raw_input=g2, actor=admin, commit=False
        )
        prev_enrollments.append(enr)

    p_prev_1.status = STATUS_CLOTUREE
    p_prev_1.closed_at = utcnow_naive()
    p_prev_1.closed_by_id = admin.id
    year_prev.status = STATUS_CLOTUREE
    year_prev.closed_at = utcnow_naive()
    year_prev.closed_by_id = admin.id
    db.session.flush()

    # 3. Année scolaire active 2026-2027 (Fictive)
    year_cur = SchoolYear(
        label="2026-2027",
        start_date=date(2026, 10, 1),
        end_date=date(2027, 6, 30),
        age_reference_date=date(2026, 10, 1),
        status=STATUS_OUVERTE,
        default_director_name="M. OUÉDRAOGO Issiaka (Directeur Fictif)",
        default_director_title="Le Directeur",
    )
    db.session.add(year_cur)
    db.session.flush()

    settings = EstablishmentSettings.get_singleton()
    settings.country = "Burkina Faso"
    settings.motto = "La Patrie ou la Mort, nous Vaincrons"
    settings.ceb = "CEB de Ouagadougou I (DÉMONSTRATION FICTIVE)"
    settings.school_name = "ÉCOLE PRIMAIRE FICTIVE « YELEN DÉMO »"
    settings.school_code = "DEMO-BF-001"
    settings.locality = "Ouagadougou"
    settings.address = "Secteur 12 — Données fictives de démonstration"
    settings.phone = "+226 25 00 00 00"
    settings.logo_filename = logo_name
    settings.active_school_year_id = year_cur.id

    pol_cur = CalculationPolicy(
        school_year_id=year_cur.id,
        class_id=None,
        general_average_scale=Decimal("10.00"),
        decimal_places=2,
        rounding_mode=ROUNDING_HALF_UP,
        absence_policy=ABSENCE_ZERO,
        tie_breaking_mode=TIE_COMPETITION,
        annual_average_mode=ANNUAL_SIMPLE,
        annual_require_all_periods=True,
    )
    db.session.add(pol_cur)
    db.session.flush()
    for order, (lo, hi, lbl, is_last) in enumerate(
        [
            ("0", "5", "Insuffisant", False),
            ("5", "6", "Passable", False),
            ("6", "7", "Assez bien", False),
            ("7", "8.5", "Bien", False),
            ("8.5", "10", "Très bien", True),
        ],
        start=1,
    ):
        db.session.add(
            GradingScaleItem(
                policy_id=pol_cur.id,
                min_score=Decimal(lo),
                max_score=Decimal(hi),
                appreciation=lbl,
                is_last_interval=is_last,
                display_order=order,
            )
        )

    t1 = EvaluationPeriod(
        school_year_id=year_cur.id,
        name="Premier Trimestre",
        short_code="T1",
        display_order=1,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 12, 20),
        status=STATUS_OUVERTE,
    )
    t2 = EvaluationPeriod(
        school_year_id=year_cur.id,
        name="Deuxième Trimestre",
        short_code="T2",
        display_order=2,
        start_date=date(2027, 1, 5),
        end_date=date(2027, 3, 28),
        status=STATUS_OUVERTE,
    )
    t3 = EvaluationPeriod(
        school_year_id=year_cur.id,
        name="Troisième Trimestre",
        short_code="T3",
        display_order=3,
        start_date=date(2027, 4, 10),
        end_date=date(2027, 6, 30),
        status=STATUS_BROUILLON,
    )
    db.session.add_all([t1, t2, t3])

    cm2 = AcademicClass(
        school_year_id=year_cur.id,
        name="CM2 A",
        level_label="CM2",
        director_name="M. OUÉDRAOGO Issiaka (Directeur Fictif)",
        director_title="Le Directeur",
        titulaire_name="Mme SAWADOGO Rasmata (Titulaire Fictive)",
        display_order=1,
    )
    cm1_cur = AcademicClass(
        school_year_id=year_cur.id,
        name="CM1",
        level_label="CM1",
        director_name="M. OUÉDRAOGO Issiaka (Directeur Fictif)",
        director_title="Le Directeur",
        titulaire_name="M. BAZIÉ Paul (Titulaire Fictif)",
        display_order=2,
    )
    db.session.add_all([cm2, cm1_cur])
    db.session.flush()

    db.session.add(
        TeacherClassAssignment(user_id=enseignant.id, class_id=cm2.id, can_edit_grades=True)
    )

    # Passage des 3 admis vers CM2 A et 1 redoublant vers CM1
    promote_or_repeat_students(
        source_class=cm1_prev,
        target_class=cm2,
        decisions_by_enrollment_id={
            prev_enrollments[0].id: "ADMIS",
            prev_enrollments[1].id: "ADMIS",
            prev_enrollments[2].id: "ADMIS",
        },
        commit=False,
    )
    promote_or_repeat_students(
        source_class=cm1_prev,
        target_class=cm1_cur,
        decisions_by_enrollment_id={prev_enrollments[3].id: "REDOUBLE"},
        commit=False,
    )

    # Ajout d'élèves complémentaires en CM2 A (cas variés : ex æquo, absence, dispense, 0 réel, inactif)
    extra_cm2 = [
        ("TAPSOBA", "Wendkouni", "M", date(2015, 5, 10), "Public", None),
        ("DIALLO", "Aïssata", "F", date(2015, 9, 2), "Public", None),
        ("SANOU", "Souleymane", "M", date(2014, 8, 19), "Public", "CM2 (2025-2026)"),
        ("ZONGO", "Mariam", "F", date(2015, 2, 11), "Transférée", None),
    ]
    for ln, fn, sx, bd, sc, rep in extra_cm2:
        create_and_enroll_student(
            academic_class=cm2,
            last_name=ln,
            first_names=fn,
            sex=sx,
            birth_date=bd,
            schooling_status=sc,
            repeated_class_detail=rep,
            commit=False,
        )
    db.session.flush()

    subjects_spec = [
        ("Rédaction / Expression écrite", "Rédac.", "1", "10", "10", True),
        ("Lecture & Compréhension", "Lect.", "2", "10", "10", True),
        ("Dictée & Questions", "Dictée", "1", "10", "10", True),
        ("Opérations & Problèmes", "Maths", "2", "20", "10", True),
        ("Histoire-Géographie & Sciences", "Éveil", "2", "20", "10", True),
        ("Éducation Physique et Sportive", "EPS", "1", "10", "10", True),
    ]
    cm2_subjects: list[Subject] = []
    for idx, (nm, sh, cf, mx, rf, ex) in enumerate(subjects_spec, start=1):
        s = Subject(
            class_id=cm2.id,
            name=nm,
            short_label=sh,
            coefficient=Decimal(cf),
            min_score=Decimal("0"),
            max_score=Decimal(mx),
            reference_scale=Decimal(rf),
            allow_exemption=ex,
            display_order=idx,
        )
        db.session.add(s)
        cm2_subjects.append(s)
    db.session.flush()

    enrs = sorted(cm2.enrollments, key=lambda e: e.roll_number)
    # T1 complet (avec ex æquo volontaire entre les 2 premiers, une dispense EPS, une absence et un 0 réel en dictée)
    t1_matrix = [
        ["8.5", "9", "8", "17", "16", "9"],      # ex æquo 1er/1re
        ["8.5", "9", "8", "17", "16", "9"],      # ex æquo 1er/1re
        ["7.5", "8", "7", "15", "14", "DISP"],   # dispensée EPS
        ["6.5", "7", "0", "13", "12", "8"],      # 0 réel en dictée
        ["6", "6.5", "5.5", "ABS", "12", "7.5"], # absent en maths (compté 0 par défaut)
        ["5", "5.5", "4.5", "10", "11", "6.5"],
        ["7", "7.5", "6.5", "14", "13", "8"],    # sera désactivée pour illustrer l'historique inactif
    ]
    for enr, row_vals in zip(enrs, t1_matrix):
        for subj, raw in zip(cm2_subjects, row_vals):
            upsert_single_grade(
                enrollment=enr, period=t1, subject=subj, raw_input=raw, actor=enseignant, commit=False
            )
        save_student_observation(
            enrollment=enr,
            period=t1,
            scope="PERIODE",
            teacher_observation="Travail régulier (donnée fictive de démonstration).",
            director_observation="Encouragements de la direction.",
            honors_mention=None,
            actor=directeur,
            commit=False,
        )

    # Désactiver le 7e élève sans perdre ses notes T1
    if len(enrs) >= 7:
        toggle_enrollment_active_state(
            enrs[6], is_active=False, reason="Mutation familiale en fin de T1 (Démo)", commit=False
        )

    # Clôturer T1 et saisir partiellement T2 (en cours)
    t1.status = STATUS_CLOTUREE
    t1.closed_at = utcnow_naive()
    t1.closed_by_id = directeur.id

    t2_partial = [
        ["9", "8.5", "8.5", "18", "17", "9"],
        ["8", "8.5", "7.5", "16", "15", "8.5"],
        ["7.5", "8", "", "15", "14", "DISP"],  # 1 note non saisie en T2 (illustre l'état Incomplet)
    ]
    for enr, row_vals in zip(enrs[:3], t2_partial):
        for subj, raw in zip(cm2_subjects, row_vals):
            if raw != "":
                upsert_single_grade(
                    enrollment=enr, period=t2, subject=subj, raw_input=raw, actor=enseignant, commit=False
                )

    # Comptes additionnels illustrant le module RBAC centralisé (§3)
    admin_etab = User(
        username="admin_etab_demo",
        full_name="ILBOUDO Clarisse (Admin Établissement Démo)",
        role=ROLE_ADMIN_ETAB,
        establishment_id=1,
        is_active=True,
    )
    admin_etab.set_password("AdminEtab2026!")

    secretaire = User(
        username="secretaire_demo",
        full_name="SANOU Fatimata (Secrétaire Démo)",
        role=ROLE_SECRETAIRE,
        establishment_id=1,
        is_active=True,
    )
    secretaire.set_password("Secretaire2026!")

    comptable = User(
        username="comptable_demo",
        full_name="BASSOLÉ Karim (Comptable Démo)",
        role=ROLE_COMPTABLE,
        establishment_id=1,
        is_active=True,
    )
    comptable.set_password("Comptable2026!")

    surveillant = User(
        username="surveillant_demo",
        full_name="TAPSOBA Moussa (Surveillant Démo)",
        role=ROLE_SURVEILLANT,
        establishment_id=1,
        is_active=True,
    )
    surveillant.set_password("Surveillant2026!")

    parent_user = User(
        username="parent_demo",
        full_name="COMPAGNÉ Amadou (Parent d'élève Démo)",
        role=ROLE_PARENT,
        establishment_id=1,
        is_active=True,
    )
    parent_user.set_password("Parent2026!")

    db.session.add_all([admin_etab, secretaire, comptable, surveillant, parent_user])
    db.session.flush()

    if enrs:
        db.session.add(
            ParentStudentLink(
                user_id=parent_user.id,
                student_id=enrs[0].student_id,
                relationship_label="Père / Tuteur légal",
            )
        )
        db.session.add(
            StudentAbsenceRecord(
                establishment_id=1,
                enrollment_id=enrs[0].id,
                event_date=date(2027, 1, 18),
                event_type="ABSENCE",
                justified=True,
                notes="Consultation médicale justifiée (Démo)",
                recorded_by_id=surveillant.id,
            )
        )
        db.session.add(
            AccountingEntry(
                establishment_id=1,
                entry_date=date(2027, 1, 10),
                entry_type="PAIEMENT",
                reference="REC-DEMO-001",
                label="Cotisation APE / Frais de scolarité T1 (Démo)",
                amount=Decimal("15000.00"),
                is_validated=True,
                recorded_by_id=comptable.id,
            )
        )

    db.session.commit()
    ensure_rbac_initialized()
    invalidate_rbac_cache()
    return {"status": "created", "db_path": str(paths.db_path)}
