"""Génération des états imprimables PDF A4 via ReportLab (§10, §11, §12 CdC).

Conçu pour une lisibilité optimale à l'impression noir et blanc comme couleur,
100 % hors ligne grâce aux polices TrueType DejaVuSans embarquées localement.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from yelen_notes.models import ANNUAL_WEIGHTED, EstablishmentSettings
from yelen_notes.paths import RuntimePaths
from yelen_notes.services.calculation_service import (
    AnnualClassResults,
    PeriodClassResults,
    StudentAnnualComputed,
    StudentPeriodComputed,
    format_compact_decimal_fr,
    format_decimal_fr,
)

FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
_FONTS_REGISTERED = False


def ensure_pdf_fonts(fonts_dir: Path) -> tuple[str, str]:
    """Enregistre les polices locales DejaVuSans une seule fois pour ReportLab."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return FONT_REGULAR, FONT_BOLD

    reg_path = fonts_dir / "DejaVuSans.ttf"
    bold_path = fonts_dir / "DejaVuSans-Bold.ttf"
    if reg_path.is_file() and bold_path.is_file():
        pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(reg_path)))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold_path)))
        _FONTS_REGISTERED = True
        return FONT_REGULAR, FONT_BOLD

    return "Helvetica", "Helvetica-Bold"


def _build_styles(font_reg: str, font_bold: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "header_left": ParagraphStyle(
            "HeaderLeft",
            parent=base["Normal"],
            fontName=font_reg,
            fontSize=8.5,
            leading=11,
            alignment=1,
        ),
        "header_right": ParagraphStyle(
            "HeaderRight",
            parent=base["Normal"],
            fontName=font_reg,
            fontSize=8.5,
            leading=11,
            alignment=1,
        ),
        "doc_title": ParagraphStyle(
            "DocTitle",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=12,
            leading=15,
            alignment=1,
            spaceBefore=2 * mm,
            spaceAfter=2 * mm,
        ),
        "subtitle": ParagraphStyle(
            "DocSubTitle",
            parent=base["Normal"],
            fontName=font_reg,
            fontSize=9,
            leading=12,
            alignment=1,
            spaceAfter=3 * mm,
        ),
        "demo_banner": ParagraphStyle(
            "DemoBanner",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=8,
            leading=10,
            alignment=1,
            textColor=colors.HexColor("#7F1D1D"),
            spaceAfter=2 * mm,
        ),
        "cell": ParagraphStyle(
            "Cell",
            parent=base["Normal"],
            fontName=font_reg,
            fontSize=8,
            leading=10,
        ),
        "cell_center": ParagraphStyle(
            "CellCenter",
            parent=base["Normal"],
            fontName=font_reg,
            fontSize=8,
            leading=10,
            alignment=1,
        ),
        "cell_bold": ParagraphStyle(
            "CellBold",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=8,
            leading=10,
        ),
        "cell_bold_center": ParagraphStyle(
            "CellBoldCenter",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=8,
            leading=10,
            alignment=1,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["Normal"],
            fontName=font_reg,
            fontSize=7.5,
            leading=9.5,
        ),
    }


def _safe(text: str | None) -> str:
    return escape(str(text or ""))


def _make_logo_flowable(settings: EstablishmentSettings, paths: RuntimePaths, max_mm: float = 18.0):
    if not settings.logo_filename:
        return ""
    logo_path = paths.logos_dir / settings.logo_filename
    if not logo_path.is_file():
        return ""
    try:
        img = RLImage(str(logo_path))
        box = max_mm * mm
        iw, ih = float(img.imageWidth), float(img.imageHeight)
        if iw > 0 and ih > 0:
            ratio = min(box / iw, box / ih)
            img.drawWidth = iw * ratio
            img.drawHeight = ih * ratio
        return img
    except Exception:
        return ""


def _build_official_header(
    settings: EstablishmentSettings,
    paths: RuntimePaths,
    school_year_label: str,
    styles: dict[str, ParagraphStyle],
    usable_width: float,
) -> list:
    elements: list = []
    if paths.is_demo_mode:
        elements.append(
            Paragraph(
                "DOCUMENT DE DÉMONSTRATION — DONNÉES SCOLAIRES FICTIVES (NE PAS UTILISER OFFICIELLEMENT)",
                styles["demo_banner"],
            )
        )

    country_txt = _safe((settings.country or "Burkina Faso").upper())
    motto_txt = _safe(settings.motto or "")
    ceb_txt = _safe(settings.ceb)

    left_lines = [f"<b>{country_txt}</b>", "———"]
    if motto_txt:
        left_lines.append(f"<i>{motto_txt}</i>")
    if ceb_txt:
        left_lines.append(f"CEB : {ceb_txt}")
    left_p = Paragraph("<br/>".join(left_lines), styles["header_left"])

    school_txt = _safe(settings.school_name or "Établissement non renseigné")
    right_lines = [f"<b>{school_txt}</b>"]
    loc_parts = [p for p in (settings.locality, settings.address) if p]
    if loc_parts:
        right_lines.append(_safe(" — ".join(loc_parts)))
    if settings.phone:
        right_lines.append(f"Tél. : {_safe(settings.phone)}")
    right_lines.append(f"<b>Année scolaire : {_safe(school_year_label)}</b>")
    right_p = Paragraph("<br/>".join(right_lines), styles["header_right"])

    logo_flow = _make_logo_flowable(settings, paths)
    if logo_flow != "":
        col_widths = [usable_width * 0.41, usable_width * 0.16, usable_width * 0.43]
        header_tbl = Table([[left_p, logo_flow, right_p]], colWidths=col_widths)
    else:
        col_widths = [usable_width * 0.48, usable_width * 0.52]
        header_tbl = Table([[left_p, right_p]], colWidths=col_widths)

    header_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#1F2937")),
            ]
        )
    )
    elements.append(header_tbl)
    return elements


def _build_signature_block(
    titulaire_name: str,
    director_title: str,
    director_name: str,
    locality: str | None,
    styles: dict[str, ParagraphStyle],
    usable_width: float,
) -> Table:
    today_str = datetime.now().strftime("%d/%m/%Y")
    place_line = f"Fait à {_safe(locality)}, le {today_str}" if locality else f"Édité le {today_str}"

    left_sig = Paragraph(
        f"<b>Le / La Titulaire de la classe</b><br/><br/><br/><br/>{_safe(titulaire_name)}",
        styles["cell_center"],
    )
    right_sig = Paragraph(
        f"{place_line}<br/><b>{_safe(director_title)}</b><br/><br/><br/>{_safe(director_name)}",
        styles["cell_center"],
    )
    sig_tbl = Table([[left_sig, right_sig]], colWidths=[usable_width * 0.5, usable_width * 0.5])
    sig_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return sig_tbl


def _footer_canvas_callback(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT_REGULAR if _FONTS_REGISTERED else "Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#4B5563"))
    w, _ = doc.pagesize
    canvas.drawString(12 * mm, 8 * mm, f"Yelen Notes — Édition du {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    canvas.drawRightString(w - 12 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def generate_period_releve_pdf(
    settings: EstablishmentSettings,
    paths: RuntimePaths,
    results: PeriodClassResults,
) -> bytes:
    """Génère le relevé de notes périodique d'une classe en PDF A4 (§10 CdC)."""
    font_reg, font_bold = ensure_pdf_fonts(paths.fonts_dir)
    styles = _build_styles(font_reg, font_bold)

    use_landscape = len(results.subjects) > 4
    page_size = landscape(A4) if use_landscape else A4
    margin = 10 * mm
    usable_width = page_size[0] - 2 * margin

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=page_size,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=14 * mm,
        title=f"Relevé de notes - {results.academic_class.name} - {results.period.name}",
    )

    story: list = []
    story.extend(
        _build_official_header(
            settings,
            paths,
            results.academic_class.school_year.label,
            styles,
            usable_width,
        )
    )

    ac = results.academic_class
    story.append(
        Paragraph(
            f"RELEVÉ DE NOTES — {_safe(ac.name)} — {_safe(results.period.name.upper())}",
            styles["doc_title"],
        )
    )
    stats_line = (
        f"Effectif actif : <b>{results.active_count}</b> (Filles : {results.girls_count} | Garçons : {results.boys_count}) — "
        f"Classés : <b>{results.ranked_count}</b> — "
        f"Moyenne de la classe : <b>{format_decimal_fr(results.class_average, results.decimal_places, results.rounding_mode)} "
        f"/{format_compact_decimal_fr(results.general_scale)}</b>"
    )
    story.append(Paragraph(stats_line, styles["subtitle"]))

    # Construction du tableau principal
    header_row = [
        Paragraph("<b>N°</b>", styles["cell_bold_center"]),
        Paragraph("<b>Nom et Prénoms</b>", styles["cell_bold"]),
        Paragraph("<b>S</b>", styles["cell_bold_center"]),
    ]
    for subj in results.subjects:
        short = _safe((subj.short_label or subj.name)[:12])
        bar = format_compact_decimal_fr(subj.max_score)
        cf = format_compact_decimal_fr(subj.coefficient)
        header_row.append(
            Paragraph(f"<b>{short}</b><br/><font size=6.5>/{bar} (c.{cf})</font>", styles["cell_bold_center"])
        )
    header_row.extend(
        [
            Paragraph("<b>Pts</b>", styles["cell_bold_center"]),
            Paragraph(
                f"<b>Moy.<br/>/{format_compact_decimal_fr(results.general_scale)}</b>",
                styles["cell_bold_center"],
            ),
            Paragraph("<b>Rang</b>", styles["cell_bold_center"]),
            Paragraph("<b>Appréciation</b>", styles["cell_bold_center"]),
        ]
    )

    table_data = [header_row]
    for row in results.active_rows:
        st = row.enrollment.student
        r_cells = [
            Paragraph(str(row.enrollment.roll_number), styles["cell_center"]),
            Paragraph(_safe(st.full_name), styles["cell"]),
            Paragraph(st.sex, styles["cell_center"]),
        ]
        for subj in results.subjects:
            c = row.subject_cells[subj.id]
            r_cells.append(Paragraph(_safe(c.display_raw), styles["cell_center"]))
        r_cells.extend(
            [
                Paragraph(
                    format_decimal_fr(row.total_weighted_points, results.decimal_places, results.rounding_mode)
                    if row.is_rankable
                    else "—",
                    styles["cell_center"],
                ),
                Paragraph(f"<b>{_safe(row.display_average)}</b>", styles["cell_bold_center"]),
                Paragraph(_safe(row.rank_label), styles["cell_center"]),
                Paragraph(
                    _safe(row.appreciation if row.is_rankable else row.unranked_reason),
                    styles["cell_center"],
                ),
            ]
        )
        table_data.append(r_cells)

    # Ligne des moyennes par matière
    if results.subjects and results.active_rows:
        avg_row = [
            Paragraph("", styles["cell_center"]),
            Paragraph("<b>Moyenne par matière (sur réf.)</b>", styles["cell_bold"]),
            Paragraph("", styles["cell_center"]),
        ]
        for subj in results.subjects:
            st_sub = results.subject_stats[subj.id]
            avg_row.append(
                Paragraph(
                    f"<b>{format_decimal_fr(st_sub.average_on_ref, results.decimal_places, results.rounding_mode)}</b>",
                    styles["cell_bold_center"],
                )
            )
        avg_row.extend(
            [
                Paragraph("", styles["cell_center"]),
                Paragraph(
                    f"<b>{format_decimal_fr(results.class_average, results.decimal_places, results.rounding_mode)}</b>",
                    styles["cell_bold_center"],
                ),
                Paragraph("", styles["cell_center"]),
                Paragraph("", styles["cell_center"]),
            ]
        )
        table_data.append(avg_row)

    # Calcul dynamique des largeurs de colonnes
    fixed_no = 9 * mm
    fixed_sex = 7 * mm
    fixed_pts = 15 * mm
    fixed_moy = 15 * mm
    fixed_rk = 15 * mm
    fixed_app = 26 * mm
    fixed_name = 46 * mm if use_landscape else 38 * mm
    remaining = usable_width - (
        fixed_no + fixed_name + fixed_sex + fixed_pts + fixed_moy + fixed_rk + fixed_app
    )
    subj_count = max(1, len(results.subjects))
    per_subj_w = max(11 * mm, remaining / subj_count)
    if per_subj_w * len(results.subjects) > remaining and len(results.subjects) > 0:
        per_subj_w = remaining / len(results.subjects)

    col_widths = (
        [fixed_no, fixed_name, fixed_sex]
        + [per_subj_w] * len(results.subjects)
        + [fixed_pts, fixed_moy, fixed_rk, fixed_app]
    )

    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#374151")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 5 * mm))
    story.append(
        KeepTogether(
            [
                _build_signature_block(
                    ac.titulaire_name,
                    ac.director_title,
                    ac.director_name,
                    settings.locality,
                    styles,
                    usable_width,
                )
            ]
        )
    )

    doc.build(story, onFirstPage=_footer_canvas_callback, onLaterPages=_footer_canvas_callback)
    return buf.getvalue()


def generate_merit_ranking_pdf(
    settings: EstablishmentSettings,
    paths: RuntimePaths,
    results: PeriodClassResults | AnnualClassResults,
    is_annual: bool = False,
) -> bytes:
    """Génère le classement par ordre de mérite (périodique ou annuel) en PDF A4 (§11 CdC)."""
    font_reg, font_bold = ensure_pdf_fonts(paths.fonts_dir)
    styles = _build_styles(font_reg, font_bold)

    margin = 12 * mm
    usable_width = A4[0] - 2 * margin
    buf = io.BytesIO()

    ac = results.academic_class
    scope_title = (
        f"ANNÉE SCOLAIRE {ac.school_year.label}"
        if is_annual
        else results.period.name.upper()  # type: ignore[union-attr]
    )
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=15 * mm,
        title=f"Classement par ordre de mérite - {ac.name}",
    )

    story: list = []
    story.extend(
        _build_official_header(settings, paths, ac.school_year.label, styles, usable_width)
    )
    story.append(
        Paragraph(
            f"CLASSEMENT PAR ORDRE DE MÉRITE — {_safe(ac.name)} — {_safe(scope_title)}",
            styles["doc_title"],
        )
    )

    class_avg_val = results.class_annual_average if is_annual else results.class_average  # type: ignore[union-attr]
    story.append(
        Paragraph(
            f"Élèves classés : <b>{len(results.ranked_rows)}</b> sur {len(results.active_rows)} actifs — "
            f"Moyenne de la classe : <b>{format_decimal_fr(class_avg_val, results.decimal_places, results.rounding_mode)} "
            f"/{format_compact_decimal_fr(results.general_scale)}</b> — "
            f"Admis (≥ moyenne) : <b>{results.pass_count}</b> "
            f"({format_decimal_fr(results.pass_rate_percent, 1)} %)",
            styles["subtitle"],
        )
    )

    headers = [
        Paragraph("<b>Rang</b>", styles["cell_bold_center"]),
        Paragraph("<b>N°</b>", styles["cell_bold_center"]),
        Paragraph("<b>Nom</b>", styles["cell_bold"]),
        Paragraph("<b>Prénoms</b>", styles["cell_bold"]),
        Paragraph("<b>Sexe</b>", styles["cell_bold_center"]),
        Paragraph(
            f"<b>Moyenne /{format_compact_decimal_fr(results.general_scale)}</b>",
            styles["cell_bold_center"],
        ),
        Paragraph("<b>Appréciation</b>", styles["cell_bold_center"]),
    ]
    data = [headers]
    for row in results.ranked_rows:
        st = row.enrollment.student
        avg_disp = row.display_annual_average if is_annual else row.display_average  # type: ignore[union-attr]
        data.append(
            [
                Paragraph(f"<b>{_safe(row.rank_label)}</b>", styles["cell_bold_center"]),
                Paragraph(str(row.enrollment.roll_number), styles["cell_center"]),
                Paragraph(_safe(st.last_name), styles["cell"]),
                Paragraph(_safe(st.first_names), styles["cell"]),
                Paragraph(st.sex, styles["cell_center"]),
                Paragraph(f"<b>{_safe(avg_disp)}</b>", styles["cell_bold_center"]),
                Paragraph(_safe(row.appreciation), styles["cell_center"]),
            ]
        )

    col_widths = [
        20 * mm,
        12 * mm,
        42 * mm,
        50 * mm,
        13 * mm,
        24 * mm,
        usable_width - (161 * mm),
    ]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#374151")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(tbl)

    if results.unranked_active_rows or results.inactive_rows:
        story.append(Spacer(1, 3 * mm))
        excl_parts = []
        if results.unranked_active_rows:
            excl_parts.append(
                f"Élèves actifs non classés ({len(results.unranked_active_rows)}) : "
                + ", ".join(
                    f"{r.enrollment.student.full_name} ({r.unranked_reason})"
                    for r in results.unranked_active_rows
                )
            )
        if results.inactive_rows:
            excl_parts.append(f"Élèves inactifs exclus du classement : {len(results.inactive_rows)}.")
        story.append(Paragraph(_safe(" | ".join(excl_parts)), styles["small"]))

    story.append(Spacer(1, 6 * mm))
    story.append(
        KeepTogether(
            [
                _build_signature_block(
                    ac.titulaire_name,
                    ac.director_title,
                    ac.director_name,
                    settings.locality,
                    styles,
                    usable_width,
                )
            ]
        )
    )

    doc.build(story, onFirstPage=_footer_canvas_callback, onLaterPages=_footer_canvas_callback)
    return buf.getvalue()


def _build_single_periodic_bulletin_flowables(
    settings: EstablishmentSettings,
    paths: RuntimePaths,
    results: PeriodClassResults,
    student_row: StudentPeriodComputed,
    styles: dict[str, ParagraphStyle],
    usable_width: float,
) -> list:
    flow: list = []
    ac = results.academic_class
    st = student_row.enrollment.student
    enr = student_row.enrollment

    flow.extend(_build_official_header(settings, paths, ac.school_year.label, styles, usable_width))
    flow.append(
        Paragraph(
            f"BULLETIN SCOLAIRE — {_safe(results.period.name.upper())}",
            styles["doc_title"],
        )
    )

    # Bloc identité de l'élève
    id_left = Paragraph(
        f"<b>Élève :</b> {_safe(st.full_name)}<br/>"
        f"<b>Matricule :</b> {_safe(st.unique_code)} &nbsp;&nbsp;|&nbsp;&nbsp; <b>N° :</b> {enr.roll_number}<br/>"
        f"<b>Sexe :</b> {'Féminin (F)' if st.sex == 'F' else 'Masculin (M)'} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Né(e) le :</b> {st.birth_date.strftime('%d/%m/%Y')} ({student_row.age} ans)",
        styles["cell"],
    )
    id_right = Paragraph(
        f"<b>Classe :</b> {_safe(ac.name)} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Effectif :</b> {results.active_count}<br/>"
        f"<b>Scolarité :</b> {_safe(enr.schooling_status or '—')}<br/>"
        f"<b>Classe redoublée :</b> {_safe(enr.repeated_class_detail or 'Néant')}",
        styles["cell"],
    )
    id_tbl = Table([[id_left, id_right]], colWidths=[usable_width * 0.54, usable_width * 0.46])
    id_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#374151")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    flow.append(id_tbl)
    flow.append(Spacer(1, 3 * mm))

    # Tableau des disciplines (§12.1 CdC)
    gen_scale_str = format_compact_decimal_fr(results.general_scale)
    subj_headers = [
        Paragraph("<b>Matières</b>", styles["cell_bold"]),
        Paragraph("<b>Note obtenue</b>", styles["cell_bold_center"]),
        Paragraph("<b>Barème</b>", styles["cell_bold_center"]),
        Paragraph("<b>Moy. / Réf.</b>", styles["cell_bold_center"]),
        Paragraph("<b>Coef.</b>", styles["cell_bold_center"]),
        Paragraph("<b>Points</b>", styles["cell_bold_center"]),
        Paragraph("<b>Appréciation</b>", styles["cell_bold_center"]),
    ]
    subj_rows = [subj_headers]

    for subj in results.subjects:
        cell = student_row.subject_cells[subj.id]
        ref_str = format_compact_decimal_fr(subj.reference_scale)
        bar_str = format_compact_decimal_fr(subj.max_score)
        coef_disp = (
            format_compact_decimal_fr(cell.coefficient_applied)
            if cell.coefficient_applied > 0
            else "—"
        )
        moy_ref_disp = (
            f"{cell.display_on_ref} /{ref_str}"
            if cell.on_subject_scale is not None
            else cell.display_on_ref
        )
        subj_rows.append(
            [
                Paragraph(_safe(subj.name), styles["cell"]),
                Paragraph(f"<b>{_safe(cell.display_raw)}</b>", styles["cell_bold_center"]),
                Paragraph(f"/{bar_str}", styles["cell_center"]),
                Paragraph(_safe(moy_ref_disp), styles["cell_center"]),
                Paragraph(_safe(coef_disp), styles["cell_center"]),
                Paragraph(_safe(cell.display_points), styles["cell_center"]),
                Paragraph(_safe(cell.appreciation), styles["cell_center"]),
            ]
        )

    subj_rows.append(
        [
            Paragraph("<b>TOTAUX</b>", styles["cell_bold"]),
            Paragraph("", styles["cell_center"]),
            Paragraph("", styles["cell_center"]),
            Paragraph("", styles["cell_center"]),
            Paragraph(
                f"<b>{format_compact_decimal_fr(student_row.total_coefficients)}</b>",
                styles["cell_bold_center"],
            ),
            Paragraph(
                f"<b>{format_decimal_fr(student_row.total_weighted_points, results.decimal_places, results.rounding_mode)}</b>",
                styles["cell_bold_center"],
            ),
            Paragraph("", styles["cell_center"]),
        ]
    )

    col_w = [
        usable_width * 0.30,
        usable_width * 0.12,
        usable_width * 0.10,
        usable_width * 0.13,
        usable_width * 0.08,
        usable_width * 0.11,
        usable_width * 0.16,
    ]
    grades_tbl = Table(subj_rows, colWidths=col_w, repeatRows=1)
    grades_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F3F4F6")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#374151")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    flow.append(grades_tbl)
    flow.append(Spacer(1, 3 * mm))

    # Encadré synthèse Moyenne générale & Rang
    rank_text = (
        f"{student_row.rank_label} sur {results.ranked_count} classés"
        if student_row.is_rankable
        else f"Non classé ({student_row.unranked_reason})"
    )
    mention_line = (
        f"<br/><b>Distinction :</b> {_safe(student_row.honors_mention)}"
        if student_row.honors_mention
        else ""
    )
    sum_left = Paragraph(
        f"<font size=10><b>MOYENNE GÉNÉRALE : {_safe(student_row.display_average)} / {gen_scale_str}</b></font><br/>"
        f"<b>RANG :</b> {_safe(rank_text)}<br/>"
        f"<b>APPRÉCIATION GÉNÉRALE :</b> {_safe(student_row.appreciation or '—')}"
        f"{mention_line}",
        styles["cell"],
    )
    sum_right = Paragraph(
        f"<b>Repères de la classe :</b><br/>"
        f"Moyenne de la classe : {format_decimal_fr(results.class_average, results.decimal_places, results.rounding_mode)} / {gen_scale_str}<br/>"
        f"Plus forte moyenne : {format_decimal_fr(results.max_average, results.decimal_places, results.rounding_mode)} / {gen_scale_str}<br/>"
        f"Plus faible moyenne : {format_decimal_fr(results.min_average, results.decimal_places, results.rounding_mode)} / {gen_scale_str}",
        styles["cell"],
    )
    sum_tbl = Table([[sum_left, sum_right]], colWidths=[usable_width * 0.56, usable_width * 0.44])
    sum_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#1F2937")),
                ("LINEBEFORE", (1, 0), (1, 0), 0.5, colors.HexColor("#4B5563")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    flow.append(sum_tbl)
    flow.append(Spacer(1, 3 * mm))

    # Observations et signatures du titulaire et du directeur (§12.1 CdC)
    t_obs = _safe(student_row.teacher_observation or enr.observations or "....................................................................................................")
    d_obs = _safe(student_row.director_observation or "....................................................................................................")
    obs_left = Paragraph(
        f"<b>Observations et signature du Titulaire :</b><br/>{t_obs}<br/><br/><br/>"
        f"<b>{_safe(ac.titulaire_name)}</b>",
        styles["cell"],
    )
    obs_right = Paragraph(
        f"<b>Visa, cachet et observations de la Direction :</b><br/>{d_obs}<br/><br/>"
        f"<b>{_safe(ac.director_title)}</b><br/><b>{_safe(ac.director_name)}</b>",
        styles["cell"],
    )
    obs_tbl = Table([[obs_left, obs_right]], colWidths=[usable_width * 0.50, usable_width * 0.50])
    obs_tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#374151")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    flow.append(obs_tbl)
    return flow


def generate_periodic_bulletins_pdf(
    settings: EstablishmentSettings,
    paths: RuntimePaths,
    results: PeriodClassResults,
    enrollment_ids: Sequence[int] | None = None,
) -> bytes:
    """Produit 1 ou plusieurs bulletins périodiques dans un même PDF A4 séparés par page (§12.1, §12.2 CdC)."""
    font_reg, font_bold = ensure_pdf_fonts(paths.fonts_dir)
    styles = _build_styles(font_reg, font_bold)
    margin = 11 * mm
    usable_width = A4[0] - 2 * margin

    if enrollment_ids:
        wanted = set(enrollment_ids)
        selected_rows = [
            r for r in (results.active_rows + results.inactive_rows) if r.enrollment.id in wanted
        ]
    else:
        selected_rows = list(results.active_rows)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=10 * mm,
        bottomMargin=13 * mm,
        title=f"Bulletins - {results.academic_class.name} - {results.period.name}",
    )

    story: list = []
    for idx, st_row in enumerate(selected_rows):
        if idx > 0:
            story.append(PageBreak())
        story.extend(
            _build_single_periodic_bulletin_flowables(
                settings, paths, results, st_row, styles, usable_width
            )
        )

    if not story:
        story.append(Paragraph("Aucun élève sélectionné pour l'édition du bulletin.", styles["doc_title"]))

    doc.build(story, onFirstPage=_footer_canvas_callback, onLaterPages=_footer_canvas_callback)
    return buf.getvalue()


def _build_single_annual_bulletin_flowables(
    settings: EstablishmentSettings,
    paths: RuntimePaths,
    annual_results: AnnualClassResults,
    student_row: StudentAnnualComputed,
    styles: dict[str, ParagraphStyle],
    usable_width: float,
) -> list:
    flow: list = []
    ac = annual_results.academic_class
    st = student_row.enrollment.student
    enr = student_row.enrollment

    flow.extend(_build_official_header(settings, paths, ac.school_year.label, styles, usable_width))
    flow.append(
        Paragraph(
            f"BULLETIN SCOLAIRE ANNUEL — ANNÉE {_safe(ac.school_year.label)}",
            styles["doc_title"],
        )
    )

    id_left = Paragraph(
        f"<b>Élève :</b> {_safe(st.full_name)}<br/>"
        f"<b>Matricule :</b> {_safe(st.unique_code)} &nbsp;&nbsp;|&nbsp;&nbsp; <b>N° :</b> {enr.roll_number}<br/>"
        f"<b>Sexe :</b> {st.sex} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Né(e) le :</b> {st.birth_date.strftime('%d/%m/%Y')} ({student_row.age} ans)",
        styles["cell"],
    )
    id_right = Paragraph(
        f"<b>Classe :</b> {_safe(ac.name)} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Effectif :</b> {len(annual_results.active_rows)}<br/>"
        f"<b>Scolarité :</b> {_safe(enr.schooling_status or '—')}<br/>"
        f"<b>Classe redoublée :</b> {_safe(enr.repeated_class_detail or 'Néant')}",
        styles["cell"],
    )
    id_tbl = Table([[id_left, id_right]], colWidths=[usable_width * 0.54, usable_width * 0.46])
    id_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#374151")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    flow.append(id_tbl)
    flow.append(Spacer(1, 3 * mm))

    # Tableau annuel par discipline à travers toutes les périodes
    periods = annual_results.periods
    header = [
        Paragraph("<b>Matières</b>", styles["cell_bold"]),
        Paragraph("<b>Coef.</b>", styles["cell_bold_center"]),
    ]
    for p in periods:
        header.append(Paragraph(f"<b>{_safe(p.short_code)}</b>", styles["cell_bold_center"]))
    header.extend(
        [
            Paragraph("<b>Moy. Annuelle</b>", styles["cell_bold_center"]),
            Paragraph("<b>Appréciation</b>", styles["cell_bold_center"]),
        ]
    )

    rows = [header]
    for subj in annual_results.subjects:
        s_sum = student_row.subject_summaries[subj.id]
        line = [
            Paragraph(_safe(subj.name), styles["cell"]),
            Paragraph(format_compact_decimal_fr(subj.coefficient), styles["cell_center"]),
        ]
        for p in periods:
            line.append(Paragraph(_safe(s_sum.period_displays_on_ref.get(p.id, "—")), styles["cell_center"]))
        line.extend(
            [
                Paragraph(f"<b>{_safe(s_sum.display_annual_subject_average)}</b>", styles["cell_bold_center"]),
                Paragraph(_safe(s_sum.appreciation), styles["cell_center"]),
            ]
        )
        rows.append(line)

    # Lignes récapitulatives des moyennes et rangs périodiques
    recap_avg = [
        Paragraph("<b>MOYENNE PÉRIODIQUE</b>", styles["cell_bold"]),
        Paragraph("", styles["cell_center"]),
    ]
    recap_rank = [
        Paragraph("<b>RANG PÉRIODIQUE</b>", styles["cell_bold"]),
        Paragraph("", styles["cell_center"]),
    ]
    for p in periods:
        recap_avg.append(
            Paragraph(f"<b>{_safe(student_row.period_displays.get(p.id, '—'))}</b>", styles["cell_bold_center"])
        )
        recap_rank.append(
            Paragraph(_safe(student_row.period_ranks.get(p.id, "—")), styles["cell_center"])
        )
    recap_avg.extend(
        [
            Paragraph(f"<b>{_safe(student_row.display_annual_average)}</b>", styles["cell_bold_center"]),
            Paragraph(f"<b>{_safe(student_row.appreciation)}</b>", styles["cell_bold_center"]),
        ]
    )
    recap_rank.extend(
        [
            Paragraph(f"<b>{_safe(student_row.rank_label)}</b>", styles["cell_bold_center"]),
            Paragraph("", styles["cell_center"]),
        ]
    )
    rows.append(recap_avg)
    rows.append(recap_rank)

    p_count = max(1, len(periods))
    fixed_mat = usable_width * 0.30
    fixed_cf = usable_width * 0.08
    fixed_ann = usable_width * 0.15
    fixed_app = usable_width * 0.19
    per_p_w = (usable_width - (fixed_mat + fixed_cf + fixed_ann + fixed_app)) / p_count
    col_w = [fixed_mat, fixed_cf] + ([per_p_w] * len(periods)) + [fixed_ann, fixed_app]

    ann_tbl = Table(rows, colWidths=col_w, repeatRows=1)
    ann_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                ("BACKGROUND", (0, -2), (-1, -1), colors.HexColor("#F3F4F6")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#374151")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    flow.append(ann_tbl)
    flow.append(Spacer(1, 3 * mm))

    gen_scale_str = format_compact_decimal_fr(annual_results.general_scale)
    mode_label = (
        "Moyenne pondérée des périodes"
        if annual_results.annual_mode == ANNUAL_WEIGHTED
        else "Moyenne arithmétique des périodes"
    )
    decision_str = enr.annual_decision or "À délibérer"

    syn_left = Paragraph(
        f"<font size=10><b>MOYENNE ANNUELLE : {_safe(student_row.display_annual_average)} / {gen_scale_str}</b></font><br/>"
        f"<b>RANG ANNUEL :</b> {_safe(student_row.rank_label)} sur {len(annual_results.ranked_rows)} classés<br/>"
        f"<b>APPRÉCIATION ANNUELLE :</b> {_safe(student_row.appreciation or '—')}<br/>"
        f"<b>DÉCISION DE FIN D'ANNÉE :</b> {_safe(decision_str)}",
        styles["cell"],
    )
    syn_right = Paragraph(
        f"<b>Méthode :</b> {mode_label}<br/>"
        f"Moyenne annuelle classe : {format_decimal_fr(annual_results.class_annual_average, annual_results.decimal_places, annual_results.rounding_mode)} / {gen_scale_str}<br/>"
        f"Plus forte moyenne : {format_decimal_fr(annual_results.max_annual_average, annual_results.decimal_places, annual_results.rounding_mode)} / {gen_scale_str}<br/>"
        f"Plus faible moyenne : {format_decimal_fr(annual_results.min_annual_average, annual_results.decimal_places, annual_results.rounding_mode)} / {gen_scale_str}",
        styles["cell"],
    )
    syn_tbl = Table([[syn_left, syn_right]], colWidths=[usable_width * 0.56, usable_width * 0.44])
    syn_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#1F2937")),
                ("LINEBEFORE", (1, 0), (1, 0), 0.5, colors.HexColor("#4B5563")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    flow.append(syn_tbl)
    flow.append(Spacer(1, 3 * mm))

    t_obs = _safe(student_row.teacher_observation or "....................................................................................................")
    d_obs = _safe(student_row.director_observation or "....................................................................................................")
    obs_left = Paragraph(
        f"<b>Appréciation et signature du Titulaire :</b><br/>{t_obs}<br/><br/><br/>"
        f"<b>{_safe(ac.titulaire_name)}</b>",
        styles["cell"],
    )
    obs_right = Paragraph(
        f"<b>Décision, visa et cachet de la Direction :</b><br/>{d_obs}<br/><br/>"
        f"<b>{_safe(ac.director_title)}</b><br/><b>{_safe(ac.director_name)}</b>",
        styles["cell"],
    )
    obs_tbl = Table([[obs_left, obs_right]], colWidths=[usable_width * 0.50, usable_width * 0.50])
    obs_tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#374151")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    flow.append(obs_tbl)
    return flow


def generate_annual_bulletins_pdf(
    settings: EstablishmentSettings,
    paths: RuntimePaths,
    annual_results: AnnualClassResults,
    enrollment_ids: Sequence[int] | None = None,
) -> bytes:
    """Produit 1 ou plusieurs bulletins scolaires annuels dans un même PDF A4 (§12.2, §12.3 CdC)."""
    font_reg, font_bold = ensure_pdf_fonts(paths.fonts_dir)
    styles = _build_styles(font_reg, font_bold)
    margin = 11 * mm
    usable_width = A4[0] - 2 * margin

    if enrollment_ids:
        wanted = set(enrollment_ids)
        selected_rows = [
            r
            for r in (annual_results.active_rows + annual_results.inactive_rows)
            if r.enrollment.id in wanted
        ]
    else:
        selected_rows = list(annual_results.active_rows)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=10 * mm,
        bottomMargin=13 * mm,
        title=f"Bulletins annuels - {annual_results.academic_class.name}",
    )

    story: list = []
    for idx, st_row in enumerate(selected_rows):
        if idx > 0:
            story.append(PageBreak())
        story.extend(
            _build_single_annual_bulletin_flowables(
                settings, paths, annual_results, st_row, styles, usable_width
            )
        )

    if not story:
        story.append(Paragraph("Aucun élève sélectionné pour l'édition du bulletin annuel.", styles["doc_title"]))

    doc.build(story, onFirstPage=_footer_canvas_callback, onLaterPages=_footer_canvas_callback)
    return buf.getvalue()
