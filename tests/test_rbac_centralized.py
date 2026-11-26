"""Tests automatisés complets du module centralisé RBAC, multi-établissements et audit (§1 à §14)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from flask_migrate import upgrade as alembic_upgrade
import sqlalchemy as sa

from tests.conftest import extract_csrf_token
from yelen_notes import create_app, initialize_database
from yelen_notes.extensions import db
from yelen_notes.models import (
    ROLE_ADMIN_ETAB,
    ROLE_COMPTABLE,
    ROLE_CONSULTATION,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    ROLE_PARENT,
    ROLE_SECRETAIRE,
    ROLE_SUPER_ADMIN,
    ROLE_SURVEILLANT,
    STATUS_OUVERTE,
    AcademicClass,
    AuditLog,
    ClassEnrollment,
    Establishment,
    ParentStudentLink,
    Permission,
    Role,
    SchoolYear,
    Student,
    User,
    UserRole,
    utcnow_naive,
)
from yelen_notes.paths import build_runtime_paths
from yelen_notes.security import (
    assign_roles_to_user,
    ensure_rbac_initialized,
    has_permission,
    invalidate_rbac_cache,
)
from yelen_notes.services.student_service import create_and_enroll_student


def _login(client, username: str, password: str, follow_redirects: bool = True):
    r_get = client.get("/connexion")
    tok = extract_csrf_token(r_get.data)
    return client.post(
        "/connexion",
        data={"csrf_token": tok, "username": username, "password": password},
        follow_redirects=follow_redirects,
    )


def test_predefined_roles_catalog_default_deny_and_matrix_crud(app, configured_school):
    """§2, §3, §4, §5, §10 : Catalogue des 8 rôles prédéfinis, refus par défaut, CRUD rôles, matrice et extensibilité."""
    admin_client = app.test_client()
    assert _login(admin_client, "admin", "AdminPass2026!").status_code == 200

    with app.app_context():
        ensure_rbac_initialized()
        expected_codes = {
            ROLE_SUPER_ADMIN,
            ROLE_ADMIN_ETAB,
            ROLE_DIRECTEUR,
            ROLE_SECRETAIRE,
            ROLE_ENSEIGNANT,
            ROLE_COMPTABLE,
            ROLE_SURVEILLANT,
            ROLE_PARENT,
            ROLE_CONSULTATION,
        }
        existing_codes = {r.code for r in Role.query.all()}
        assert expected_codes.issubset(existing_codes)

    # Accès à l'interface Administration -> Gestion des rôles et permissions
    r_hub = admin_client.get("/administration/roles-et-permissions")
    assert r_hub.status_code == 200
    tok = extract_csrf_token(r_hub.data)

    # Création d'un rôle personnalisé VIDE (0 permission -> principe absolu Default Deny §2)
    r_create = admin_client.post(
        "/administration/roles-et-permissions/roles/creer",
        data={
            "csrf_token": tok,
            "name": "Stagiaire Archiviste",
            "code": "STAGIAIRE_ARCHIVISTE",
            "description": "Rôle test sans aucune permission initiale",
        },
        follow_redirects=True,
    )
    assert r_create.status_code == 200
    assert "STAGIAIRE_ARCHIVISTE" in r_create.data.decode("utf-8")

    with app.app_context():
        custom_role = Role.query.filter_by(code="STAGIAIRE_ARCHIVISTE").first()
        assert custom_role is not None
        custom_role_id = custom_role.id

        stagiaire = User(
            username="stagiaire1",
            full_name="Stagiaire Test",
            role="STAGIAIRE_ARCHIVISTE",
            establishment_id=1,
            is_active=True,
        )
        stagiaire.set_password("Stagiaire2026!")
        db.session.add(stagiaire)
        db.session.flush()
        db.session.add(UserRole(user_id=stagiaire.id, role_id=custom_role.id))
        db.session.commit()
        invalidate_rbac_cache()
        stagiaire_id = stagiaire.id

    # Vérifier le REFUS PAR DÉFAUT (Default Deny) sur toutes les routes pour ce rôle vide
    stag_client = app.test_client()
    _login(stag_client, "stagiaire1", "Stagiaire2026!")
    for url in ["/", "/eleves", "/notes", "/comptabilite", "/administration/roles-et-permissions"]:
        assert stag_client.get(url).status_code == 403, f"Default deny violé sur {url}"

    # Accorder uniquement 'dashboard.view' via la matrice HTTP -> effet immédiat !
    with app.app_context():
        dash_perm = Permission.query.filter_by(code="dashboard.view").first()
        assert dash_perm is not None
        dash_perm_id = dash_perm.id

    tok = extract_csrf_token(
        admin_client.get(f"/administration/roles-et-permissions?tab=matrix&role_id={custom_role_id}").data
    )
    r_mat = admin_client.post(
        f"/administration/roles-et-permissions/matrice/{custom_role_id}",
        data={"csrf_token": tok, "permission_ids[]": [str(dash_perm_id)]},
        follow_redirects=True,
    )
    assert r_mat.status_code == 200
    assert stag_client.get("/").status_code == 200
    assert stag_client.get("/eleves").status_code == 403

    # Extensibilité (§1.4) : ajout d'une nouvelle permission granulaire sans réécrire le cœur
    tok = extract_csrf_token(admin_client.get("/administration/roles-et-permissions?tab=matrix").data)
    r_perm = admin_client.post(
        "/administration/roles-et-permissions/permissions/creer",
        data={
            "csrf_token": tok,
            "code": "bibliotheque.view",
            "module": "bibliotheque",
            "module_label": "Bibliothèque scolaire",
            "resource": "Ouvrages",
            "action": "VIEW",
            "description": "Consulter le catalogue de la bibliothèque",
        },
        follow_redirects=True,
    )
    assert r_perm.status_code == 200
    assert "bibliotheque.view" in r_perm.data.decode("utf-8")

    # Suppression d'un rôle attribué : refusée sans réaffectation, autorisée avec réaffectation (§3)
    tok = extract_csrf_token(admin_client.get("/administration/roles-et-permissions?tab=roles").data)
    r_del_fail = admin_client.post(
        f"/administration/roles-et-permissions/roles/{custom_role_id}/supprimer",
        data={"csrf_token": tok},
        follow_redirects=True,
    )
    assert r_del_fail.status_code == 200
    with app.app_context():
        assert db.session.get(Role, custom_role_id) is not None
        consult_role_id = Role.query.filter_by(code=ROLE_CONSULTATION).first().id

    tok = extract_csrf_token(admin_client.get("/administration/roles-et-permissions?tab=roles").data)
    r_del_ok = admin_client.post(
        f"/administration/roles-et-permissions/roles/{custom_role_id}/supprimer",
        data={"csrf_token": tok, "reassign_to_role_id": str(consult_role_id)},
        follow_redirects=True,
    )
    assert r_del_ok.status_code == 200
    with app.app_context():
        assert db.session.get(Role, custom_role_id) is None
        reassigned_user = db.session.get(User, stagiaire_id)
        assert ROLE_CONSULTATION in reassigned_user.active_role_codes


def test_mandatory_security_scenarios_accountant_teacher_parent_and_anti_self_escalation(
    app, configured_school
):
    """§12 scénarios 1, 2, 3, 4, 7, 8 + isolation Parent/Tuteur et cumul multi-rôles + surcharges ALLOW/DENY."""
    with app.app_context():
        ensure_rbac_initialized()
        r_comp = Role.query.filter_by(code=ROLE_COMPTABLE).first()
        r_surv = Role.query.filter_by(code=ROLE_SURVEILLANT).first()
        r_sec = Role.query.filter_by(code=ROLE_SECRETAIRE).first()
        r_par = Role.query.filter_by(code=ROLE_PARENT).first()
        r_adm_etab = Role.query.filter_by(code=ROLE_ADMIN_ETAB).first()

        comptable = User(username="comptable1", full_name="Comptable Test", role=ROLE_COMPTABLE, establishment_id=1, is_active=True)
        comptable.set_password("Comptable2026!")

        parent = User(username="parent1", full_name="Parent Élève 1", role=ROLE_PARENT, establishment_id=1, is_active=True)
        parent.set_password("ParentPass2026!")

        polyvalent = User(username="agent_poly", full_name="Secrétaire + Surveillant", role=ROLE_SECRETAIRE, establishment_id=1, is_active=True)
        polyvalent.set_password("Polyvalent2026!")

        admin_etab = User(username="admin_etab1", full_name="Admin Établissement 1", role=ROLE_ADMIN_ETAB, establishment_id=1, is_active=True)
        admin_etab.set_password("AdminEtab2026!")

        db.session.add_all([comptable, parent, polyvalent, admin_etab])
        db.session.flush()

        db.session.add(UserRole(user_id=comptable.id, role_id=r_comp.id))
        db.session.add(UserRole(user_id=parent.id, role_id=r_par.id))
        db.session.add(UserRole(user_id=polyvalent.id, role_id=r_sec.id))
        db.session.add(UserRole(user_id=polyvalent.id, role_id=r_surv.id))
        db.session.add(UserRole(user_id=admin_etab.id, role_id=r_adm_etab.id))

        # Inscrire 2 élèves en CM2, et rattacher parent1 UNIQUEMENT au 1er élève
        cm2 = db.session.get(AcademicClass, configured_school["cm2_id"])
        enr1 = create_and_enroll_student(
            academic_class=cm2, last_name="KABORÉ", first_names="Amina", sex="F", birth_date=date(2015, 3, 10)
        )
        enr2 = create_and_enroll_student(
            academic_class=cm2, last_name="TRAORÉ", first_names="Moussa", sex="M", birth_date=date(2015, 7, 22)
        )
        db.session.add(ParentStudentLink(user_id=parent.id, student_id=enr1.student_id))
        db.session.commit()
        invalidate_rbac_cache()

        comptable_id = comptable.id
        polyvalent_id = polyvalent.id
        admin_etab_id = admin_etab.id
        enr1_id, enr2_id = enr1.id, enr2.id
        ce1_id = configured_school["ce1_id"]
        cm2_id = configured_school["cm2_id"]
        t1_id = configured_school["t1_id"]
        titulaire_id = User.query.filter_by(username="titulaire_cm2").first().id

    # --- SCÉNARIO §12.1 & §12.2 & §12.3 : Enseignant ne peut ni modifier ses permissions, ni accéder aux URLs admin, ni toucher une autre classe ---
    t_client = app.test_client()
    assert _login(t_client, "titulaire_cm2", "Titulaire2026!").status_code == 200
    assert t_client.get("/administration/roles-et-permissions").status_code == 403
    assert t_client.get(f"/administration/roles-et-permissions/utilisateur/{titulaire_id}").status_code == 403
    assert t_client.get("/comptabilite").status_code == 403
    assert t_client.get(f"/notes?class_id={ce1_id}&period_id={t1_id}")  # filtre automatiquement sur ses seules classes
    # Tentative directe sur API de notes d'une classe non assignée (CE1)
    with app.app_context():
        ce1 = db.session.get(AcademicClass, ce1_id)
        enr_ce1 = create_and_enroll_student(
            academic_class=ce1, last_name="OUATTARA", first_names="Ali", sex="M", birth_date=date(2016, 1, 5)
        )
        db.session.commit()
        enr_ce1_id = enr_ce1.id

    tok_t = extract_csrf_token(t_client.get(f"/notes?class_id={cm2_id}&period_id={t1_id}").data)
    r_unassigned_grade = t_client.post(
        "/notes/api/cellule",
        json={
            "enrollment_id": enr_ce1_id,
            "period_id": t1_id,
            "subject_id": configured_school["s_lec_id"],
            "raw_input": "8",
            "expected_version": 0,
        },
        headers={"X-CSRFToken": tok_t},
    )
    assert r_unassigned_grade.status_code in (400, 403)

    # --- SCÉNARIO §12.4 : Comptable ne voit aucune info pédagogique confidentielle (notes, relevés, bulletins, proposition) ---
    c_client = app.test_client()
    assert _login(c_client, "comptable1", "Comptable2026!").status_code == 200
    dash_html = c_client.get("/").data.decode("utf-8")
    assert "Comptabilité scolaire" in dash_html
    assert "Saisie des notes" not in dash_html  # Masqué dans le menu UI (§7.1)

    assert c_client.get("/comptabilite").status_code == 200
    assert c_client.get("/comptabilite/exporter").status_code == 200
    for forbidden_pedagogical_url in [
        "/notes",
        "/notes/observations",
        "/resultats/releve",
        "/resultats/classement",
        "/resultats/bulletins",
        "/resultats/tableau-synoptique",
        "/resultats/proposition-fin-annee",
        "/administration/roles-et-permissions",
    ]:
        assert (
            c_client.get(forbidden_pedagogical_url).status_code == 403
        ), f"Le comptable ne doit pas accéder à {forbidden_pedagogical_url}"

    # --- SCÉNARIO §12.7 : Retrait de permission prend effet IMMÉDIATEMENT sur la session active ---
    admin_client = app.test_client()
    _login(admin_client, "admin", "AdminPass2026!")
    with app.app_context():
        comp_role = Role.query.filter_by(code=ROLE_COMPTABLE).first()
        comp_role_id = comp_role.id
        remaining_perm_ids = [
            str(rp.permission_id)
            for rp in comp_role.role_permissions
            if rp.permission.code != "accounting.export"
        ]
    tok_adm = extract_csrf_token(
        admin_client.get(f"/administration/roles-et-permissions?tab=matrix&role_id={comp_role_id}").data
    )
    admin_client.post(
        f"/administration/roles-et-permissions/matrice/{comp_role_id}",
        data={"csrf_token": tok_adm, "permission_ids[]": remaining_perm_ids},
        follow_redirects=True,
    )
    # Sans reconnexion, la requête suivante du comptable sur /comptabilite/exporter est bloquée immédiatement !
    assert c_client.get("/comptabilite/exporter").status_code == 403
    assert c_client.get("/comptabilite").status_code == 200

    # --- SCÉNARIO §12.8 : Un Administrateur d'établissement ne peut PAS s'auto-élever ni modifier ses propres rôles/surcharges ---
    ae_client = app.test_client()
    assert _login(ae_client, "admin_etab1", "AdminEtab2026!").status_code == 200
    with app.app_context():
        sa_role_id = Role.query.filter_by(code=ROLE_SUPER_ADMIN).first().id
        ae_role_id = Role.query.filter_by(code=ROLE_ADMIN_ETAB).first().id
        audit_export_perm_id = Permission.query.filter_by(code="audit.export").first().id

    tok_ae = extract_csrf_token(
        ae_client.get(f"/administration/roles-et-permissions/utilisateur/{admin_etab_id}").data
    )
    # 1) Tentative d'auto-attribution du rôle SUPER_ADMIN sur son propre compte
    ae_client.post(
        f"/administration/roles-et-permissions/utilisateur/{admin_etab_id}/roles",
        data={"csrf_token": tok_ae, "role_ids[]": [str(sa_role_id)]},
        follow_redirects=True,
    )
    with app.app_context():
        ae_reloaded = db.session.get(User, admin_etab_id)
        assert ROLE_SUPER_ADMIN not in ae_reloaded.active_role_codes

    # 2) Tentative d'auto-attribution d'une surcharge ALLOW sur soi-même
    tok_ae = extract_csrf_token(
        ae_client.get(f"/administration/roles-et-permissions/utilisateur/{admin_etab_id}").data
    )
    ae_client.post(
        f"/administration/roles-et-permissions/utilisateur/{admin_etab_id}/exception",
        data={
            "csrf_token": tok_ae,
            "permission_id": str(audit_export_perm_id),
            "effect": "ALLOW",
            "reason": "Auto-élévation illégitime",
        },
        follow_redirects=True,
    )
    with app.app_context():
        ae_reloaded = db.session.get(User, admin_etab_id)
        assert len(ae_reloaded.permission_overrides) == 0

    # 3) Tentative par Admin Établissement de modifier la matrice de son propre rôle ADMIN_ETABLISSEMENT -> refusée !
    tok_ae = extract_csrf_token(
        ae_client.get(f"/administration/roles-et-permissions?tab=matrix&role_id={ae_role_id}").data
    )
    ae_client.post(
        f"/administration/roles-et-permissions/matrice/{ae_role_id}",
        data={"csrf_token": tok_ae, "permission_ids[]": [str(audit_export_perm_id)]},
        follow_redirects=True,
    )
    with app.app_context():
        # Vérifier qu'une trace SECURITY_VIOLATION DENIED figure au journal d'audit
        sec_logs = AuditLog.query.filter_by(action="SECURITY_VIOLATION", status="DENIED").all()
        assert len(sec_logs) >= 2

    # --- Cumul multi-rôles + Surcharge individuelle DENY qui prime sur ALLOW (§6) ---
    poly_client = app.test_client()
    assert _login(poly_client, "agent_poly", "Polyvalent2026!").status_code == 200
    assert poly_client.get("/suivi-absences").status_code == 200  # via SURVEILLANT / SECRETAIRE
    assert poly_client.get(f"/eleves/exporter-excel/{cm2_id}").status_code == 200  # via SECRETAIRE

    with app.app_context():
        st_export_perm_id = Permission.query.filter_by(code="students.export").first().id

    tok_adm = extract_csrf_token(
        admin_client.get(f"/administration/roles-et-permissions/utilisateur/{polyvalent_id}").data
    )
    admin_client.post(
        f"/administration/roles-et-permissions/utilisateur/{polyvalent_id}/exception",
        data={
            "csrf_token": tok_adm,
            "permission_id": str(st_export_perm_id),
            "effect": "DENY",
            "reason": "Restriction temporaire d'export nominatif",
        },
        follow_redirects=True,
    )
    # Effet immédiat : DENY écrase la permission du rôle SECRETAIRE !
    assert poly_client.get(f"/eleves/exporter-excel/{cm2_id}").status_code == 403

    # --- Isolation stricte du Parent / Tuteur sur ses seuls enfants rattachés (§3.8 & §7.3) ---
    p_client = app.test_client()
    r_p_login = _login(p_client, "parent1", "ParentPass2026!")
    assert r_p_login.status_code == 200
    p_html = p_client.get("/espace-parent").data.decode("utf-8")
    assert "KABORÉ" in p_html
    assert "TRAORÉ" not in p_html  # L'autre élève de CM2 n'apparaît pas !

    # Téléchargement du bulletin de son propre enfant -> 200 PDF
    r_own_pdf = p_client.get(f"/espace-parent/bulletin/{enr1_id}?period_id={t1_id}")
    assert r_own_pdf.status_code == 200
    assert r_own_pdf.mimetype == "application/pdf"

    # Tentative d'accès au bulletin ou à la fiche d'un autre enfant -> 403 !
    assert p_client.get(f"/espace-parent/bulletin/{enr2_id}?period_id={t1_id}").status_code == 403
    assert p_client.get(f"/eleves/{enr2_id}").status_code == 403
    assert p_client.get(f"/eleves/{enr1_id}").status_code == 200


def test_multi_establishment_isolation_and_immediate_deactivation_or_suspension(
    app, configured_school
):
    """§8, §12 scénarios 5 & 6 : Isolation stricte inter-établissements et blocage immédiat d'un compte désactivé/suspendu."""
    admin_client = app.test_client()
    _login(admin_client, "admin", "AdminPass2026!")

    # Super Admin crée un second établissement (ETAB-ANNEXE) via l'interface RBAC
    tok = extract_csrf_token(
        admin_client.get("/administration/roles-et-permissions?tab=establishments").data
    )
    r_est = admin_client.post(
        "/administration/etablissements/creer",
        data={
            "csrf_token": tok,
            "code": "ETAB-ANNEXE",
            "name": "École Primaire Annexe de Baskuy",
            "ceb": "CEB Baskuy",
            "locality": "Ouagadougou",
        },
        follow_redirects=True,
    )
    assert r_est.status_code == 200

    with app.app_context():
        est_b = Establishment.query.filter_by(code="ETAB-ANNEXE").first()
        assert est_b is not None
        est_b_id = est_b.id

        yr_b = SchoolYear(
            establishment_id=est_b.id,
            label="2026-2027-B",
            start_date=date(2026, 10, 1),
            end_date=date(2027, 6, 30),
            age_reference_date=date(2026, 10, 1),
            status=STATUS_OUVERTE,
            default_director_name="Mme ZONGO Awa",
            default_director_title="La Directrice",
        )
        db.session.add(yr_b)
        db.session.flush()

        cls_b = AcademicClass(
            establishment_id=est_b.id,
            school_year_id=yr_b.id,
            name="CE2-Baskuy",
            level_label="CE2",
            director_name="Mme ZONGO Awa",
            director_title="La Directrice",
            titulaire_name="M. BADO Jean",
            display_order=1,
            is_active=True,
        )
        db.session.add(cls_b)
        db.session.flush()

        enr_b = create_and_enroll_student(
            academic_class=cls_b,
            last_name="SANFO",
            first_names="Ibrahim",
            sex="M",
            birth_date=date(2017, 2, 14),
        )

        r_adm_etab = Role.query.filter_by(code=ROLE_ADMIN_ETAB).first()
        admin_a = User(
            username="admin_etab_a",
            full_name="Admin Établissement A",
            role=ROLE_ADMIN_ETAB,
            establishment_id=1,
            is_active=True,
        )
        admin_a.set_password("AdminA2026!")

        user_b = User(
            username="sec_etab_b",
            full_name="Secrétaire Établissement B",
            role=ROLE_SECRETAIRE,
            establishment_id=est_b.id,
            is_active=True,
        )
        user_b.set_password("SecB2026!")

        db.session.add_all([admin_a, user_b])
        db.session.flush()
        db.session.add(UserRole(user_id=admin_a.id, role_id=r_adm_etab.id))
        db.session.add(
            UserRole(user_id=user_b.id, role_id=Role.query.filter_by(code=ROLE_SECRETAIRE).first().id)
        )
        db.session.commit()
        invalidate_rbac_cache()

        cls_b_id = cls_b.id
        enr_b_id = enr_b.id
        user_b_id = user_b.id

    # --- SCÉNARIO §12.5 : Admin de l'Établissement A ne peut accéder à AUCUNE donnée de l'Établissement B ---
    client_a = app.test_client()
    assert _login(client_a, "admin_etab_a", "AdminA2026!").status_code == 200

    # Ne voit ni les utilisateurs de B, ni la classe CE2-Baskuy
    users_a_html = client_a.get("/administration/utilisateurs").data.decode("utf-8")
    assert "sec_etab_b" not in users_a_html
    classes_a_html = client_a.get("/classes").data.decode("utf-8")
    assert "CE2-Baskuy" not in classes_a_html

    # Accès direct par ID aux ressources de l'établissement B -> 403 strict !
    assert client_a.get(f"/classes/{cls_b_id}/matieres").status_code == 403
    assert client_a.get(f"/eleves/{enr_b_id}").status_code == 403
    assert client_a.get(f"/administration/roles-et-permissions/utilisateur/{user_b_id}").status_code == 403

    tok_a = extract_csrf_token(client_a.get("/").data)
    assert (
        client_a.post(
            "/administration/etablissements/basculer",
            data={"csrf_token": tok_a, "establishment_id": str(est_b_id)},
        ).status_code
        == 403
    )

    # En revanche, le Super Administrateur peut basculer vers l'établissement B et voir ses données (§8)
    tok_sa = extract_csrf_token(admin_client.get("/").data)
    r_sw = admin_client.post(
        "/administration/etablissements/basculer",
        data={"csrf_token": tok_sa, "establishment_id": str(est_b_id)},
        follow_redirects=True,
    )
    assert r_sw.status_code == 200
    assert "CE2-Baskuy" in admin_client.get("/classes").data.decode("utf-8")

    # --- SCÉNARIO §12.6 : Utilisateur suspendu ou désactivé bloqué IMMÉDIATEMENT même avec session ouverte ---
    client_b = app.test_client()
    assert _login(client_b, "sec_etab_b", "SecB2026!").status_code == 200
    assert client_b.get("/").status_code == 200

    # 1) Suspension temporaire du compte pendant sa session active
    with app.app_context():
        ub = db.session.get(User, user_b_id)
        ub.suspended_until = utcnow_naive() + timedelta(days=3)
        ub.suspension_reason = "Audit administratif"
        db.session.commit()
        invalidate_rbac_cache()

    r_after_suspend = client_b.get("/", follow_redirects=False)
    assert r_after_suspend.status_code in (302, 401, 403)
    # Nouvelle tentative de connexion pendant la suspension -> 403 !
    r_relogin_susp = _login(client_b, "sec_etab_b", "SecB2026!", follow_redirects=False)
    assert r_relogin_susp.status_code == 403

    # 2) Levée de suspension puis désactivation complète en pleine session
    with app.app_context():
        ub = db.session.get(User, user_b_id)
        ub.suspended_until = None
        ub.is_active = True
        db.session.commit()
        invalidate_rbac_cache()

    assert _login(client_b, "sec_etab_b", "SecB2026!").status_code == 200
    assert client_b.get("/").status_code == 200

    with app.app_context():
        ub = db.session.get(User, user_b_id)
        ub.is_active = False
        db.session.commit()
        invalidate_rbac_cache()

    r_after_deact = client_b.get("/", follow_redirects=False)
    assert r_after_deact.status_code == 302
    assert "/connexion" in r_after_deact.headers["Location"]
    assert _login(client_b, "sec_etab_b", "SecB2026!", follow_redirects=False).status_code == 401


def test_alembic_upgrade_from_v1_0_preserves_legacy_accounts_and_data(tmp_path: Path):
    """§14 : Vérifie la migration Alembic réelle d'une base SQLite v1.0 (`1604003a9e33`) vers v1.1 (`2b8f91c4d5e6`) sans perte."""
    data_dir = tmp_path / "legacy_v1_data"
    paths = build_runtime_paths(data_dir_override=data_dir)

    # 1. Construire l'application sans auto_init_db afin d'appliquer uniquement la révision v1.0
    legacy_app = create_app(runtime_paths=paths, auto_init_db=False)
    migrations_dir = str(Path(__file__).resolve().parents[1] / "migrations")

    with legacy_app.app_context():
        alembic_upgrade(directory=migrations_dir, revision="1604003a9e33")

        # Insérer des comptes v1.0 bruts (sans colonnes v1.1) via SQL natif fidèle au schéma v1.0
        from werkzeug.security import generate_password_hash

        pw_hash = generate_password_hash("LegacyPass2026!")
        now_iso = "2026-10-05 08:00:00"
        db.session.execute(
            sa.text(
                """
                INSERT INTO establishment_settings
                (id, country, motto, ceb, school_name, session_timeout_minutes,
                 auto_backup_enabled, auto_backup_interval_hours, backup_retention_count, updated_at)
                VALUES (1, 'Burkina Faso', 'La Patrie ou la Mort, nous Vaincrons',
                        'CEB Ouaga I', 'École Legacy v1.0', 30, 1, 24, 15, :now)
                """
            ),
            {"now": now_iso},
        )
        for idx, (uname, fname, rcode) in enumerate(
            [
                ("legacy_admin", "Ancien Admin", "ADMIN"),
                ("legacy_dir", "Ancien Directeur", "DIRECTEUR"),
                ("legacy_ens", "Ancien Enseignant", "ENSEIGNANT"),
                ("legacy_cons", "Ancien Lecteur", "CONSULTATION"),
            ],
            start=1,
        ):
            db.session.execute(
                sa.text(
                    """
                    INSERT INTO users
                    (id, username, password_hash, full_name, role, is_active,
                     must_change_password, failed_login_count, created_at, updated_at)
                    VALUES (:id, :u, :ph, :fn, :r, 1, 0, 0, :now, :now)
                    """
                ),
                {"id": idx, "u": uname, "ph": pw_hash, "fn": fname, "r": rcode, "now": now_iso},
            )
        db.session.commit()
        db.session.remove()
        db.engine.dispose()

    # 2. Relancer Yelen Notes v1.1 avec initialize_database() automatique -> migration vers head + seeding RBAC
    upgraded_app = create_app(runtime_paths=paths, auto_init_db=True)
    with upgraded_app.app_context():
        u_admin = User.query.filter_by(username="legacy_admin").first()
        u_dir = User.query.filter_by(username="legacy_dir").first()
        u_ens = User.query.filter_by(username="legacy_ens").first()
        u_cons = User.query.filter_by(username="legacy_cons").first()

        assert u_admin is not None and u_admin.check_password("LegacyPass2026!")
        assert ROLE_SUPER_ADMIN in u_admin.active_role_codes
        assert has_permission(u_admin, "rbac.manage") is True

        assert u_dir is not None and ROLE_DIRECTEUR in u_dir.active_role_codes
        assert has_permission(u_dir, "proposition.validate") is True
        assert has_permission(u_dir, "rbac.manage") is False

        assert u_ens is not None and ROLE_ENSEIGNANT in u_ens.active_role_codes
        assert has_permission(u_ens, "grades.update") is True
        assert has_permission(u_ens, "accounting.view") is False

        assert u_cons is not None and ROLE_CONSULTATION in u_cons.active_role_codes
        assert has_permission(u_cons, "bulletins.view") is True
        assert has_permission(u_cons, "grades.update") is False

        est_default = db.session.get(Establishment, 1)
        assert est_default is not None
        assert est_default.name == "École Legacy v1.0"

        db.session.remove()
        db.engine.dispose()
