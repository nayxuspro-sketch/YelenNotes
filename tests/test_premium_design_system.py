"""Tests automatisés du Design System Premium Enterprise SaaS (§1–§16).

Vérifie :
- Police locale Inter (.woff2) servie 100 % hors ligne & zéro URL externe dans CSS/JS/HTML
- Absence stricte d'emojis dans l'ensemble des gabarits HTML (§3)
- Tokens CSS centralisés (Mode Clair & Mode Sombre `[data-theme="dark"]`) + persistance JS
- Architecture à 3 zones (Sidebar réductible, Topbar + Breadcrumbs, Espace de travail)
- Adaptation stricte du Tableau de bord et de la navigation aux permissions RBAC
- Identité administrative distincte sur papier blanc des documents scolaires A4 (§9)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re

from tests.conftest import extract_csrf_token
from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_COMPTABLE,
    AcademicClass,
    Role,
    User,
    UserRole,
)
from yelen_notes.security import ensure_rbac_initialized, invalidate_rbac_cache
from yelen_notes.services.student_service import create_and_enroll_student

REPO_ROOT = Path(__file__).resolve().parent.parent

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002702-\U000027B0"
    "]+",
    flags=re.UNICODE,
)


def _login(client, username: str, password: str):
    login_page = client.get("/connexion")
    tok = extract_csrf_token(login_page.data)
    return client.post(
        "/connexion",
        data={"csrf_token": tok, "username": username, "password": password},
        follow_redirects=True,
    )


def test_design_system_offline_inter_fonts_zero_emojis_and_theme_tokens(client):
    """§3, §12, §13, §14 : Police Inter locale, zéro emoji, zéro CDN, tokens Light & Dark."""
    fonts_dir = REPO_ROOT / "resources" / "static" / "fonts"
    for weight in ("400", "500", "600", "700"):
        for subset in ("latin", "latin-ext"):
            fname = f"inter-{subset}-{weight}-normal.woff2"
            assert (fonts_dir / fname).is_file(), f"Police locale manquante : {fname}"
            resp = client.get(f"/static/fonts/{fname}")
            assert resp.status_code == 200
            assert len(resp.data) > 5000

    # Aucun lien distant dans CSS et JS
    css_text = (REPO_ROOT / "resources" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    js_text = (REPO_ROOT / "resources" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for forbidden in ("https://", "http://", "googleapis", "gstatic", "jsdelivr", "unpkg"):
        assert forbidden not in css_text
        assert forbidden not in js_text

    # Tokens centralisés Mode Clair & Mode Sombre + impression A4
    assert '[data-theme="light"]' in css_text
    assert '[data-theme="dark"]' in css_text
    assert "--yn-sidebar-bg" in css_text
    assert "--yn-primary" in css_text
    assert "@media print" in css_text
    assert "yn_theme_preference" in js_text
    assert "yn_sidebar_collapsed" in js_text

    # Zéro emoji dans l'ensemble des templates HTML (§3)
    templates_dir = REPO_ROOT / "resources" / "templates"
    html_files = list(templates_dir.rglob("*.html"))
    assert len(html_files) >= 30
    for tpl in html_files:
        content = tpl.read_text(encoding="utf-8")
        match = EMOJI_PATTERN.search(content)
        assert match is None, f"Emoji interdit trouvé dans {tpl}: {match.group(0)!r}"


def test_three_zone_shell_dashboard_rbac_and_school_documents_identity(app, configured_school):
    """§4, §5, §6, §9, §14 : App Shell 3 zones, KPI/menus filtrés par RBAC et feuilles A4 blanches."""
    with app.app_context():
        ensure_rbac_initialized()
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        create_and_enroll_student(
            academic_class=cm2,
            last_name="OUÉDRAOGO",
            first_names="Salif",
            sex="M",
            birth_date=date(2015, 4, 12),
        )
        create_and_enroll_student(
            academic_class=cm2,
            last_name="SAWADOGO",
            first_names="Nafissatou",
            sex="F",
            birth_date=date(2015, 9, 3),
        )
        role_comp = Role.query.filter_by(code=ROLE_COMPTABLE).first()
        comptable = User(
            username="comptable_design",
            full_name="Comptable Design",
            role=ROLE_COMPTABLE,
            establishment_id=1,
            is_active=True,
        )
        comptable.set_password("Comptable2026!")
        db.session.add(comptable)
        db.session.flush()
        db.session.add(UserRole(user_id=comptable.id, role_id=role_comp.id))
        db.session.commit()
        invalidate_rbac_cache()
        cm2_id = configured_school["cm2_id"]
        t1_id = configured_school["t1_id"]

    # 1. Administrateur : App Shell 3 zones + KPI complets (y compris Situation financière) + Graphiques SVG
    admin_client = app.test_client()
    r_adm = _login(admin_client, "admin", "AdminPass2026!")
    assert r_adm.status_code == 200
    adm_html = r_adm.data.decode("utf-8")
    assert "yn-app-shell" in adm_html
    assert "yn-sidebar" in adm_html
    assert "yn-topbar" in adm_html
    assert "yn-breadcrumbs" in adm_html
    assert "data-yn-theme-toggle" in adm_html
    assert "yn-sidebar-collapse-btn" in adm_html
    assert "Effectif actif" in adm_html
    assert "Équipe enseignante" in adm_html
    assert "Taux de réussite" in adm_html
    assert "Situation financière" in adm_html
    assert "Répartition par sexe" in adm_html

    # 2. Enseignant : voit ses KPI pédagogiques, mais JAMAIS la situation financière ni l'admin RBAC
    teacher_client = app.test_client()
    r_tch = _login(teacher_client, "titulaire_cm2", "Titulaire2026!")
    assert r_tch.status_code == 200
    tch_html = r_tch.data.decode("utf-8")
    assert "Effectif actif" in tch_html
    assert "Taux de réussite" in tch_html
    assert "Situation financière" not in tch_html
    assert "Comptabilité scolaire" not in tch_html
    assert "Rôles & Permissions" not in tch_html

    # 3. Comptable : voit la Situation financière & Comptabilité, mais JAMAIS la saisie des notes ni les bulletins
    comp_client = app.test_client()
    r_cmp = _login(comp_client, "comptable_design", "Comptable2026!")
    assert r_cmp.status_code == 200
    cmp_html = r_cmp.data.decode("utf-8")
    assert "Situation financière" in cmp_html
    assert "Comptabilité scolaire" in cmp_html
    assert "Saisie des notes" not in cmp_html
    assert "Bulletins scolaires PDF" not in cmp_html

    # 4. Identité administrative distincte sur papier blanc (§9) pour tous les états scolaires
    for doc_url in (
        f"/resultats/bulletins?class_id={cm2_id}&period_id={t1_id}",
        f"/resultats/releve?class_id={cm2_id}&period_id={t1_id}",
        f"/resultats/classement?class_id={cm2_id}&period_id={t1_id}",
        f"/resultats/tableau-synoptique?class_id={cm2_id}&period_id={t1_id}",
        f"/resultats/proposition-fin-annee?class_id={cm2_id}",
    ):
        resp_doc = admin_client.get(doc_url)
        assert resp_doc.status_code == 200
        doc_html = resp_doc.data.decode("utf-8")
        assert ("yn-paper-preview" in doc_html) or ("yn-synoptic-sheet" in doc_html)
