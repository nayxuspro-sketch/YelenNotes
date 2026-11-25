"""Importation et exportation Excel via OpenPyXL (§7.1, §10, §11 CdC)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from yelen_notes.models import AcademicClass, ClassEnrollment, EstablishmentSettings, Student
from yelen_notes.services.calculation_service import (
    AnnualClassResults,
    PeriodClassResults,
    format_compact_decimal_fr,
    format_decimal_fr,
)
from yelen_notes.services.student_service import (
    StudentValidationError,
    create_and_enroll_student,
    normalize_first_names,
    normalize_last_name,
)

HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
SUBHEADER_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
BOLD_FONT = Font(name="Calibri", size=10, bold=True)
BODY_FONT = Font(name="Calibri", size=10)
THIN_SIDE = Side(border_style="thin", color="BFBFBF")
CELL_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)


def _auto_fit_columns(ws) -> None:
    for col_cells in ws.columns:
        max_len = 0
        col_idx = col_cells[0].column
        for cell in col_cells:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 4, 10), 42)


def _parse_excel_date(raw_val) -> date:
    if isinstance(raw_val, datetime):
        return raw_val.date()
    if isinstance(raw_val, date):
        return raw_val
    text = str(raw_val or "").strip()
    if not text:
        raise ValueError("Date de naissance manquante.")
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Format de date non reconnu (« {text} »). Utilisez JJ/MM/AAAA.")


@dataclass
class ExcelImportResult:
    imported_count: int = 0
    skipped_duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def generate_student_import_template() -> bytes:
    """Produit un classeur modèle .xlsx prêt à remplir pour l'importation d'élèves."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Élèves à importer"

    headers = [
        "Numéro",
        "Nom",
        "Prénoms",
        "Sexe",
        "Date de naissance",
        "Scolarité",
        "Classe redoublée",
        "Observations",
        "Matricule",
    ]
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = CELL_BORDER

    # Exemple d'aide dans un second onglet afin que le 1er onglet reste propre
    help_ws = wb.create_sheet("Consignes de remplissage")
    help_rows = [
        ["Colonne", "Obligatoire", "Description et format attendu"],
        ["Numéro", "Non", "Numéro d'ordre entier positif (calculé automatiquement si laissé vide)."],
        ["Nom", "Oui", "Nom de famille de l'élève (ex. OUÉDRAOGO)."],
        ["Prénoms", "Oui", "Prénom(s) de l'élève (ex. Aminata)."],
        ["Sexe", "Oui", "M (Masculin) ou F (Féminin)."],
        ["Date de naissance", "Oui", "Date valide au format JJ/MM/AAAA (ex. 14/05/2015)."],
        ["Scolarité", "Non", "Information libre définie par l'école (ex. Public, Affecté, Nouveau…)."],
        ["Classe redoublée", "Non", "Préciser la classe et/ou l'année redoublée le cas échéant."],
        ["Observations", "Non", "Remarque libre facultative."],
        ["Matricule", "Non", "Identifiant unique interne si l'élève existe déjà d'une année précédente."],
    ]
    for r_idx, row in enumerate(help_rows, start=1):
        help_ws.append(row)
        for c_idx in range(1, 4):
            c = help_ws.cell(row=r_idx, column=c_idx)
            c.font = HEADER_FONT if r_idx == 1 else BODY_FONT
            if r_idx == 1:
                c.fill = HEADER_FILL
            c.border = CELL_BORDER

    _auto_fit_columns(ws)
    _auto_fit_columns(help_ws)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_students_from_excel(file_bytes: bytes, academic_class: AcademicClass) -> ExcelImportResult:
    """Importe une liste d'élèves depuis un fichier .xlsx avec détection des doublons (§7.1 CdC)."""
    result = ExcelImportResult()
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        result.errors.append(f"Impossible de lire le fichier Excel (.xlsx) : {exc}")
        return result

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        result.errors.append("Le fichier Excel est vide.")
        return result

    # Détection souple des en-têtes sur la ligne 1
    raw_headers = [str(c or "").strip().lower() for c in rows[0]]
    col_map: dict[str, int] = {}
    aliases = {
        "roll": ("numéro", "numero", "n°", "no", "num", "ordre"),
        "last_name": ("nom", "nom de famille", "noms"),
        "first_names": ("prénoms", "prenoms", "prénom", "prenom"),
        "sex": ("sexe", "genre", "m/f"),
        "birth_date": ("date de naissance", "naissance", "né le", "ne le", "date_naissance"),
        "schooling": ("scolarité", "scolarite", "statut scolaire"),
        "repeated": ("classe redoublée", "classe redoublee", "redoublement", "redoublée"),
        "observations": ("observations", "observation", "remarques"),
        "code": ("matricule", "code", "identifiant"),
    }
    for key, names in aliases.items():
        for idx, header in enumerate(raw_headers):
            if header in names:
                col_map[key] = idx
                break

    required = ("last_name", "first_names", "sex", "birth_date")
    missing_cols = [k for k in required if k not in col_map]
    if missing_cols:
        labels_fr = {
            "last_name": "Nom",
            "first_names": "Prénoms",
            "sex": "Sexe",
            "birth_date": "Date de naissance",
        }
        missing_str = ", ".join(labels_fr[m] for m in missing_cols)
        result.errors.append(
            f"Colonnes obligatoires introuvables sur la première ligne : {missing_str}."
        )
        return result

    seen_in_file: set[tuple[str, str, date]] = set()

    for line_no, row in enumerate(rows[1:], start=2):
        if not row or all(cell is None or str(cell).strip() == "" for cell in row):
            continue

        def _cell(field_key: str):
            idx = col_map.get(field_key)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        last_raw = str(_cell("last_name") or "").strip()
        first_raw = str(_cell("first_names") or "").strip()
        sex_raw = str(_cell("sex") or "").strip().upper()
        if sex_raw in ("MASCULIN", "GARÇON", "GARCON", "H"):
            sex_raw = "M"
        elif sex_raw in ("FÉMININ", "FEMININ", "FILLE"):
            sex_raw = "F"

        try:
            bdate = _parse_excel_date(_cell("birth_date"))
        except ValueError as exc:
            result.errors.append(f"Ligne {line_no} ({last_raw} {first_raw}) : {exc}")
            continue

        norm_key = (normalize_last_name(last_raw), normalize_first_names(first_raw).lower(), bdate)
        if norm_key in seen_in_file:
            result.skipped_duplicates.append(
                f"Ligne {line_no} : doublon interne au fichier ignoré ({norm_key[0]} {first_raw}, {bdate.strftime('%d/%m/%Y')})."
            )
            continue
        seen_in_file.add(norm_key)

        roll_val = _cell("roll")
        parsed_roll: int | None = None
        if roll_val is not None and str(roll_val).strip() != "":
            try:
                parsed_roll = int(float(str(roll_val).strip()))
            except ValueError:
                result.errors.append(f"Ligne {line_no} : numéro d'ordre invalide (« {roll_val} »).")
                continue

        try:
            create_and_enroll_student(
                academic_class=academic_class,
                last_name=last_raw,
                first_names=first_raw,
                sex=sex_raw,
                birth_date=bdate,
                roll_number=parsed_roll,
                unique_code=str(_cell("code") or "").strip() or None,
                schooling_status=str(_cell("schooling") or "").strip() or None,
                repeated_class_detail=str(_cell("repeated") or "").strip() or None,
                observations=str(_cell("observations") or "").strip() or None,
                is_active=True,
                commit=True,
            )
            result.imported_count += 1
        except StudentValidationError as exc:
            msg = str(exc)
            if "Doublon" in msg or "déjà inscrit" in msg:
                result.skipped_duplicates.append(f"Ligne {line_no} : {msg}")
            else:
                result.errors.append(f"Ligne {line_no} : {msg}")

    return result


def export_students_to_excel(academic_class: AcademicClass, include_inactive: bool = True) -> bytes:
    """Exporte la liste des élèves d'une classe au format .xlsx (§7.1 CdC)."""
    wb = Workbook()
    ws = wb.active
    ws.title = f"Élèves {academic_class.name}"[:31]

    ref_date = academic_class.school_year.age_reference_date
    ws.append(
        [
            f"Classe : {academic_class.name} — Année scolaire : {academic_class.school_year.label} "
            f"(Âge calculé au {ref_date.strftime('%d/%m/%Y')})"
        ]
    )
    ws.merge_cells("A1:K1")
    ws["A1"].font = Font(name="Calibri", size=12, bold=True)

    headers = [
        "N°",
        "Matricule",
        "Nom",
        "Prénoms",
        "Sexe",
        "Date de naissance",
        "Âge",
        "Scolarité",
        "Classe redoublée",
        "Statut",
        "Observations",
    ]
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(horizontal="center")
        c.border = CELL_BORDER

    query = ClassEnrollment.query.join(Student).filter(ClassEnrollment.class_id == academic_class.id)
    if not include_inactive:
        query = query.filter(ClassEnrollment.is_active.is_(True))
    enrollments = query.order_by(ClassEnrollment.roll_number, Student.last_name).all()

    for enr in enrollments:
        st = enr.student
        age_years = st.age_at(ref_date)
        age_label = f"{age_years} an" if age_years <= 1 else f"{age_years} ans"
        row_vals = [
            enr.roll_number,
            st.unique_code,
            st.last_name,
            st.first_names,
            st.sex,
            st.birth_date.strftime("%d/%m/%Y"),
            age_label,
            enr.schooling_status or "",
            enr.repeated_class_detail or "",
            "Actif" if enr.is_active else "Inactif",
            enr.observations or "",
        ]
        ws.append(row_vals)
        r = ws.max_row
        for c_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=r, column=c_idx)
            cell.font = BODY_FONT
            cell.border = CELL_BORDER

    _auto_fit_columns(ws)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_period_releve_to_excel(
    settings: EstablishmentSettings,
    results: PeriodClassResults,
) -> bytes:
    """Exporte le relevé complet des notes et le classement d'une période vers Excel."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Relevé de notes"

    school_label = settings.school_name or "Établissement scolaire"
    ws.append([f"{school_label} — {results.academic_class.school_year.label}"])
    ws.append([f"Relevé de notes — Classe : {results.academic_class.name} — {results.period.name}"])
    ws["A1"].font = Font(name="Calibri", size=12, bold=True)
    ws["A2"].font = BOLD_FONT

    headers = ["N°", "Nom et Prénoms", "Sexe"]
    for subj in results.subjects:
        headers.append(
            f"{subj.short_label or subj.name} (/{format_compact_decimal_fr(subj.max_score)}, coef {format_compact_decimal_fr(subj.coefficient)})"
        )
    headers.extend(
        [
            "Total Coef.",
            "Total Points",
            f"Moyenne /{format_compact_decimal_fr(results.general_scale)}",
            "Rang",
            "Appréciation",
        ]
    )
    ws.append(headers)
    header_row = ws.max_row
    for c_idx in range(1, len(headers) + 1):
        c = ws.cell(row=header_row, column=c_idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        c.border = CELL_BORDER

    for row in results.active_rows:
        st = row.enrollment.student
        line = [row.enrollment.roll_number, st.full_name, st.sex]
        for subj in results.subjects:
            cell = row.subject_cells[subj.id]
            line.append(cell.display_raw)
        line.extend(
            [
                format_compact_decimal_fr(row.total_coefficients) if row.is_rankable else "—",
                format_decimal_fr(row.total_weighted_points, results.decimal_places, results.rounding_mode)
                if row.is_rankable
                else "—",
                row.display_average,
                row.rank_label if row.is_rankable else f"Non classé ({row.unranked_reason})",
                row.appreciation,
            ]
        )
        ws.append(line)
        r = ws.max_row
        for c_idx in range(1, len(headers) + 1):
            c = ws.cell(row=r, column=c_idx)
            c.font = BODY_FONT
            c.border = CELL_BORDER

    # Onglet Classement par ordre de mérite (§11 CdC)
    ws_rank = wb.create_sheet("Ordre de mérite")
    ws_rank.append([f"{school_label} — Classement par ordre de mérite"])
    ws_rank.append(
        [f"Classe : {results.academic_class.name} — {results.period.name} ({results.academic_class.school_year.label})"]
    )
    ws_rank["A1"].font = Font(name="Calibri", size=12, bold=True)
    ws_rank["A2"].font = BOLD_FONT

    rank_headers = [
        "N°",
        "Rang",
        "Nom",
        "Prénoms",
        "Sexe",
        f"Moyenne /{format_compact_decimal_fr(results.general_scale)}",
        "Appréciation",
    ]
    ws_rank.append(rank_headers)
    hr = ws_rank.max_row
    for c_idx in range(1, len(rank_headers) + 1):
        c = ws_rank.cell(row=hr, column=c_idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.border = CELL_BORDER

    for idx, row in enumerate(results.ranked_rows, start=1):
        st = row.enrollment.student
        ws_rank.append(
            [
                idx,
                row.rank_label,
                st.last_name,
                st.first_names,
                st.sex,
                row.display_average,
                row.appreciation,
            ]
        )
        r = ws_rank.max_row
        for c_idx in range(1, len(rank_headers) + 1):
            c = ws_rank.cell(row=r, column=c_idx)
            c.font = BODY_FONT
            c.border = CELL_BORDER

    _auto_fit_columns(ws)
    _auto_fit_columns(ws_rank)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_period_ranking_to_excel(
    settings: EstablishmentSettings,
    results: PeriodClassResults,
) -> bytes:
    """Exporte uniquement le classement périodique par ordre de mérite vers Excel (§11 CdC)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Ordre de mérite"

    school_label = settings.school_name or "Établissement scolaire"
    ws.append([f"{school_label} — Classement par ordre de mérite"])
    ws.append(
        [f"Classe : {results.academic_class.name} — {results.period.name} ({results.academic_class.school_year.label})"]
    )
    ws["A1"].font = Font(name="Calibri", size=12, bold=True)
    ws["A2"].font = BOLD_FONT

    headers = [
        "N°",
        "Rang",
        "Nom",
        "Prénoms",
        "Sexe",
        f"Moyenne /{format_compact_decimal_fr(results.general_scale)}",
        "Appréciation",
    ]
    ws.append(headers)
    hr = ws.max_row
    for c_idx in range(1, len(headers) + 1):
        c = ws.cell(row=hr, column=c_idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.border = CELL_BORDER

    for idx, row in enumerate(results.ranked_rows, start=1):
        st = row.enrollment.student
        ws.append(
            [
                idx,
                row.rank_label,
                st.last_name,
                st.first_names,
                st.sex,
                row.display_average,
                row.appreciation,
            ]
        )
        r = ws.max_row
        for c_idx in range(1, len(headers) + 1):
            c = ws.cell(row=r, column=c_idx)
            c.font = BODY_FONT
            c.border = CELL_BORDER

    _auto_fit_columns(ws)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_annual_ranking_to_excel(
    settings: EstablishmentSettings,
    annual_results: AnnualClassResults,
) -> bytes:
    """Exporte le classement annuel par ordre de mérite vers Excel (§11 CdC)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Mérite annuel"

    school_label = settings.school_name or "Établissement scolaire"
    ws.append([f"{school_label} — Classement annuel par ordre de mérite"])
    ws.append([f"Classe : {annual_results.academic_class.name} — Année : {annual_results.academic_class.school_year.label}"])
    ws["A1"].font = Font(name="Calibri", size=12, bold=True)
    ws["A2"].font = BOLD_FONT

    headers = ["N°", "Rang annuel", "Nom", "Prénoms", "Sexe"]
    for p in annual_results.periods:
        headers.append(f"Moy. {p.short_code}")
    headers.extend(
        [
            f"Moyenne annuelle /{format_compact_decimal_fr(annual_results.general_scale)}",
            "Appréciation annuelle",
        ]
    )
    ws.append(headers)
    hr = ws.max_row
    for c_idx in range(1, len(headers) + 1):
        c = ws.cell(row=hr, column=c_idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.border = CELL_BORDER

    for idx, row in enumerate(annual_results.ranked_rows, start=1):
        st = row.enrollment.student
        vals = [idx, row.rank_label, st.last_name, st.first_names, st.sex]
        for p in annual_results.periods:
            vals.append(row.period_displays.get(p.id, "—"))
        vals.extend([row.display_annual_average, row.appreciation])
        ws.append(vals)
        r = ws.max_row
        for c_idx in range(1, len(headers) + 1):
            c = ws.cell(row=r, column=c_idx)
            c.font = BODY_FONT
            c.border = CELL_BORDER

    _auto_fit_columns(ws)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
