"""Tests Excel (doublons), PDF A4, sauvegardes/restaurations, hors-ligne sans CDN et portabilité."""

from __future__ import annotations

from datetime import date
import io
from pathlib import Path
import zipfile
from openpyxl import Workbook

from yelen_notes import create_app
from yelen_notes.extensions import db
from yelen_notes.models import (
    STATUS_CLOTUREE,
    STATUS_OUVERTE,
    AcademicClass,
    EstablishmentSettings,
    EvaluationPeriod,
    SchoolYear,
    Student,
    Subject,
    User,
)
from yelen_notes.paths import build_runtime_paths, is_network_unc_path
from yelen_notes.services.backup_service import (
    create_safe_backup,
    restore_from_backup_archive,
    run_sqlite_integrity_check,
    verify_backup_archive,
)
from yelen_notes.services.calculation_service import (
    compute_annual_class_results,
    compute_period_class_results,
)
from yelen_notes.services.demo_service import populate_demo_database
from yelen_notes.services.excel_service import (
    export_annual_ranking_to_excel,
    export_period_releve_to_excel,
    export_students_to_excel,
    generate_student_import_template,
    import_students_from_excel,
)
from yelen_notes.services.grade_service import upsert_single_grade
from yelen_notes.services.pdf_service import (
    generate_annual_bulletins_pdf,
    generate_merit_ranking_pdf,
    generate_period_releve_pdf,
    generate_periodic_bulletins_pdf,
)
from yelen_notes.services.student_service import promote_or_repeat_students


def test_excel_import_duplicate_detection_and_export(app, configured_school):
    """§7.1 CdC : Import Excel avec contrôle des doublons (internes et base) + export."""
    tpl = generate_student_import_template()
    assert len(tpl) > 2000

    wb = Workbook()
    ws = wb.active
    ws.append(["Numéro", "Nom", "Prénoms", "Sexe", "Date de naissance", "Scolarité", "Classe redoublée"])
    ws.append([1, "OUÉDRAOGO", "Aminata", "F", "14/05/2015", "Public", ""])
    ws.append([2, "ouédraogo", "Aminata", "F", "14/05/2015", "Public", ""])  # Doublon interne au fichier
    ws.append([3, "KABORÉ", "Boureima", "M", "20/08/2014", "Public", "CE2 (2024-2025)"])
    buf = io.BytesIO()
    wb.save(buf)
    raw_xlsx = buf.getvalue()

    with app.app_context():
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        res1 = import_students_from_excel(raw_xlsx, cm2)
        assert res1.imported_count == 2
        assert len(res1.skipped_duplicates) == 1
        assert len(res1.errors) == 0

        # Réimporter le même fichier détecte les 2 élèves déjà présents en base
        res2 = import_students_from_excel(raw_xlsx, cm2)
        assert res2.imported_count == 0
        assert len(res2.skipped_duplicates) >= 2

        exported = export_students_to_excel(cm2)
        assert exported[:2] == b"PK"  # signature ZIP/XLSX valide


def test_pdf_and_excel_reports_generation_and_multiyear_historization(app, configured_school):
    """§10, §11, §12 & §3.2 CdC : Génération PDF A4 (relevés, classements, bulletins) + historisation."""
    with app.app_context():
        paths = app.config["RUNTIME_PATHS"]
        settings = EstablishmentSettings.get_singleton()
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        t1 = db.session.get(EvaluationPeriod, configured_school["t1_id"])
        t2 = db.session.get(EvaluationPeriod, configured_school["t2_id"])
        admin = db.session.get(User, configured_school["admin_id"])
        subjects = [
            db.session.get(Subject, configured_school["s_lec_id"]),
            db.session.get(Subject, configured_school["s_calc_id"]),
            db.session.get(Subject, configured_school["s_sport_id"]),
        ]

        wb = Workbook()
        ws = wb.active
        ws.append(["Nom", "Prénoms", "Sexe", "Date de naissance"])
        ws.append(["SAWADOGO", "Rasmata", "F", "10/03/2015"])
        ws.append(["ZONGO", "Adama", "M", "22/07/2015"])
        b = io.BytesIO()
        wb.save(b)
        import_students_from_excel(b.getvalue(), cm2)

        for enr, t1_vals, t2_vals in zip(
            cm2.enrollments,
            [["8.5", "16", "9"], ["7", "14", "DISP"]],
            [["9", "17", "8.5"], ["7.5", "15", "DISP"]],
        ):
            for s, v in zip(subjects, t1_vals):
                upsert_single_grade(enrollment=enr, period=t1, subject=s, raw_input=v, actor=admin)
            for s, v in zip(subjects, t2_vals):
                upsert_single_grade(enrollment=enr, period=t2, subject=s, raw_input=v, actor=admin)

        p_res = compute_period_class_results(cm2, t1)
        ann_res = compute_annual_class_results(cm2)

        pdf_releve = generate_period_releve_pdf(settings, paths, p_res)
        pdf_rank_p = generate_merit_ranking_pdf(settings, paths, p_res, is_annual=False)
        pdf_rank_a = generate_merit_ranking_pdf(settings, paths, ann_res, is_annual=True)
        pdf_bul_p = generate_periodic_bulletins_pdf(settings, paths, p_res)
        pdf_bul_a = generate_annual_bulletins_pdf(settings, paths, ann_res)

        for doc_bytes in (pdf_releve, pdf_rank_p, pdf_rank_a, pdf_bul_p, pdf_bul_a):
            assert doc_bytes.startswith(b"%PDF-")
            assert len(doc_bytes) > 1500

        assert export_period_releve_to_excel(settings, p_res)[:2] == b"PK"
        assert export_annual_ranking_to_excel(settings, ann_res)[:2] == b"PK"

        # Vérification du passage d'année, redoublement et conservation du directeur/titulaire historisés
        yr_old = cm2.school_year
        yr_old.status = STATUS_CLOTUREE
        yr_next = SchoolYear(
            label="2027-2028",
            age_reference_date=date(2027, 10, 1),
            status=STATUS_OUVERTE,
            default_director_name="Mme NOUVELLE Directrice",
            default_director_title="La Directrice",
        )
        db.session.add(yr_next)
        db.session.flush()
        cm2_next = AcademicClass(
            school_year_id=yr_next.id,
            name="CM2 A",
            director_name="Mme NOUVELLE Directrice",
            director_title="La Directrice",
            titulaire_name="M. NOUVEAU Titulaire",
        )
        db.session.add(cm2_next)
        db.session.flush()

        enr_list = list(cm2.enrollments)
        counts = promote_or_repeat_students(
            source_class=cm2,
            target_class=cm2_next,
            decisions_by_enrollment_id={
                enr_list[0].id: "SORTI",
                enr_list[1].id: "REDOUBLE",
            },
        )
        assert counts["enrolled"] == 1
        assert counts["marked_left"] == 1
        repeated_enr = cm2_next.enrollments[0]
        assert "CM2 A (2026-2027)" in (repeated_enr.repeated_class_detail or "")
        # L'ancienne classe conserve son ancien directeur !
        assert cm2.director_name == "M. OUÉDRAOGO Issiaka"
        assert cm2_next.director_name == "Mme NOUVELLE Directrice"


def test_sqlite_wal_backup_tamper_detection_restore_and_program_upgrade(tmp_path):
    """§14, §19 tests n°10, 11, 12 CdC : WAL, sauvegarde à chaud, intégrité, restauration et mise à jour."""
    assert is_network_unc_path(r"\\SERVEUR-ECOLE\Partage\Yelen") is True
    assert is_network_unc_path(str(tmp_path)) is False

    data_dir = tmp_path / "school_data"
    paths = build_runtime_paths(data_dir_override=data_dir)
    app1 = create_app(runtime_paths=paths)

    with app1.app_context():
        # Vérifier que le mode WAL est bien actif sur le fichier SQLite
        mode = db.session.execute(db.text("PRAGMA journal_mode;")).scalar()
        assert str(mode).lower() == "wal"

        st = Student(
            unique_code="ELV-2026-0001",
            last_name="TAPSOBA",
            first_names="Salfo",
            sex="M",
            birth_date=date(2015, 5, 1),
        )
        db.session.add(st)
        db.session.commit()

        archive_path = create_safe_backup(
            paths,
            app_version="1.0.0",
            trigger_type="MANUAL",
            created_by="admin",
            notes="Avant modification",
        )
        assert archive_path.is_file()
        ver = verify_backup_archive(archive_path)
        assert ver.is_valid is True

        # Altération de données après la sauvegarde
        db.session.add(
            Student(
                unique_code="ELV-2026-0002",
                last_name="INTRUS",
                first_names="Test",
                sex="M",
                birth_date=date(2015, 6, 1),
            )
        )
        db.session.commit()
        assert Student.query.count() == 2

        # Restauration de la sauvegarde -> retour exact à 1 élève + intégrité OK
        restore_from_backup_archive(archive_path, paths, app_version="1.0.0", restored_by="admin")
        assert Student.query.count() == 1
        assert Student.query.first().last_name == "TAPSOBA"

        ok, msg = run_sqlite_integrity_check(paths.db_path)
        assert ok is True and msg == "ok"

        # Vérifier la détection d'une archive corrompue / altérée
        corrupted_zip = tmp_path / "corrupted.zip"
        with zipfile.ZipFile(corrupted_zip, "w") as zf:
            zf.writestr("database/ecole.db", b"not-a-valid-sqlite-db")
            zf.writestr("manifest.json", '{"db_sha256": "deadbeef"}')
        bad_ver = verify_backup_archive(corrupted_zip)
        assert bad_ver.is_valid is False

        db.session.remove()
        db.engine.dispose()

    # Simulation d'une mise à jour du programme (nouvelle instance sur le même data_dir indépendant)
    app2 = create_app(runtime_paths=paths)
    with app2.app_context():
        assert Student.query.count() == 1
        db.session.remove()
        db.engine.dispose()

    # Vérification de la base de démonstration séparée (`ecole_demo.db`)
    demo_paths = build_runtime_paths(data_dir_override=data_dir, is_demo_mode=True)
    assert demo_paths.db_path.name == "ecole_demo.db"
    assert demo_paths.db_path != paths.db_path
    demo_app = create_app(runtime_paths=demo_paths)
    with demo_app.app_context():
        info = populate_demo_database(demo_paths)
        assert info["status"] == "created"
        assert Student.query.count() >= 7
        db.session.remove()
        db.engine.dispose()


def test_offline_assets_have_zero_external_cdn_links():
    """§15 & §19 test n°2 CdC : Aucune dépendance CDN, police distante ou URL externe dans les templates/CSS."""
    root = Path(__file__).resolve().parent.parent
    templates_dir = root / "resources" / "templates"
    static_dir = root / "resources" / "static"

    assert (static_dir / "bootstrap" / "bootstrap.min.css").is_file()
    assert (static_dir / "bootstrap" / "bootstrap.bundle.min.js").is_file()
    assert (static_dir / "fonts" / "DejaVuSans.ttf").is_file()
    assert (static_dir / "fonts" / "DejaVuSans-Bold.ttf").is_file()

    forbidden_markers = (
        "https://",
        "http://cdn",
        "jsdelivr",
        "unpkg.com",
        "cdnjs",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
    )
    for html_file in templates_dir.rglob("*.html"):
        content = html_file.read_text(encoding="utf-8").lower()
        for marker in forbidden_markers:
            assert marker not in content, f"Lien distant interdit '{marker}' trouvé dans {html_file}"
