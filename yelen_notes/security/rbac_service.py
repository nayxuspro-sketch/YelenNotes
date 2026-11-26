"""Service centralisé de gestion des rôles, permissions granulaires (RBAC) et périmètres multi-établissements."""

from __future__ import annotations

from datetime import datetime
import re
from flask import g, has_request_context, session
from sqlalchemy import inspect

from yelen_notes.extensions import db
from yelen_notes.models import (
    ALL_PERMISSION_ACTIONS,
    PERM_CREATE,
    PERM_DELETE,
    PERM_EXPORT,
    PERM_MANAGE,
    PERM_PRINT,
    PERM_UPDATE,
    PERM_VALIDATE,
    PERM_VIEW,
    ROLE_ADMIN,
    ROLE_ADMIN_ETAB,
    ROLE_COMPTABLE,
    ROLE_CONSULTATION,
    ROLE_DIRECTEUR,
    ROLE_ENSEIGNANT,
    ROLE_PARENT,
    ROLE_SECRETAIRE,
    ROLE_SUPER_ADMIN,
    ROLE_SURVEILLANT,
    Establishment,
    EstablishmentSettings,
    ParentStudentLink,
    Permission,
    Role,
    RolePermission,
    Student,
    TeacherClassAssignment,
    User,
    UserEstablishmentScope,
    UserPermissionOverride,
    UserRole,
    utcnow_naive,
)


class RBACSecurityError(ValueError):
    """Erreur métier ou violation de sécurité lors d'une opération RBAC."""


# Ordre d'affichage officiel des modules dans la matrice RBAC (§4 & §5)
MODULES_ORDER: list[tuple[str, str]] = [
    ("dashboard", "Tableau de bord"),
    ("students", "Élèves"),
    ("enrollments", "Inscriptions"),
    ("classes", "Classes"),
    ("teachers", "Enseignants"),
    ("subjects", "Disciplines & Barèmes"),
    ("grades", "Notes"),
    ("evaluations", "Évaluations & Périodes"),
    ("bulletins", "Bulletins & Relevés"),
    ("proposition", "Proposition de fin d'année"),
    ("absences", "Absences & Discipline"),
    ("accounting", "Comptabilité"),
    ("users", "Utilisateurs"),
    ("rbac", "Rôles et permissions"),
    ("settings", "Paramètres & Sauvegardes"),
    ("audit", "Journal d'audit"),
]

MODULE_LABELS_DICT: dict[str, str] = dict(MODULES_ORDER)

# Catalogue initial complet des permissions granulaires : (code, module, resource, action, description)
PERMISSIONS_CATALOG: list[tuple[str, str, str, str, str]] = [
    # Tableau de bord
    ("dashboard.view", "dashboard", "Tableau de bord", PERM_VIEW, "Consulter le tableau de bord et ses indicateurs"),
    # Élèves
    ("students.view", "students", "Dossiers élèves", PERM_VIEW, "Consulter les fiches et listes d'élèves autorisées"),
    ("students.create", "students", "Dossiers élèves", PERM_CREATE, "Créer un nouveau dossier élève"),
    ("students.update", "students", "Identités & infos élèves", PERM_UPDATE, "Modifier l'identité et les informations scolaires des élèves"),
    ("students.delete", "students", "Statut élève", PERM_DELETE, "Désactiver ou réactiver un élève dans une classe"),
    ("students.export", "students", "Listes élèves", PERM_EXPORT, "Exporter la liste des élèves au format Excel"),
    # Inscriptions
    ("enrollments.view", "enrollments", "Inscriptions", PERM_VIEW, "Consulter les inscriptions scolaires"),
    ("enrollments.create", "enrollments", "Inscriptions", PERM_CREATE, "Inscrire des élèves individuellement ou par import Excel"),
    ("enrollments.update", "enrollments", "Inscriptions", PERM_UPDATE, "Modifier les données d'inscription et renuméroter"),
    ("enrollments.validate", "enrollments", "Passage d'année", PERM_VALIDATE, "Valider le passage d'année et les redoublements"),
    # Classes
    ("classes.view", "classes", "Classes", PERM_VIEW, "Consulter les classes autorisées"),
    ("classes.create", "classes", "Classes", PERM_CREATE, "Créer une nouvelle classe"),
    ("classes.update", "classes", "Classes", PERM_UPDATE, "Modifier les paramètres et le titulariat d'une classe"),
    ("classes.delete", "classes", "Classes", PERM_DELETE, "Désactiver ou supprimer une classe non utilisée"),
    # Enseignants
    ("teachers.view", "teachers", "Enseignants", PERM_VIEW, "Consulter la liste des enseignants et leurs classes"),
    ("teachers.create", "teachers", "Enseignants", PERM_CREATE, "Créer un profil enseignant"),
    ("teachers.update", "teachers", "Enseignants", PERM_UPDATE, "Modifier les informations d'un enseignant"),
    ("teachers.manage", "teachers", "Affectations classes", PERM_MANAGE, "Affecter les enseignants titulaires aux classes"),
    # Disciplines & Barèmes
    ("subjects.view", "subjects", "Disciplines & Barèmes", PERM_VIEW, "Consulter les matières, coefficients et barèmes"),
    ("subjects.create", "subjects", "Disciplines & Barèmes", PERM_CREATE, "Ajouter ou copier des matières et barèmes"),
    ("subjects.update", "subjects", "Disciplines & Barèmes", PERM_UPDATE, "Modifier les matières, coefficients et barèmes de classe"),
    ("subjects.delete", "subjects", "Disciplines & Barèmes", PERM_DELETE, "Désactiver une matière"),
    ("subjects.manage", "subjects", "Règles générales", PERM_MANAGE, "Administrer les barèmes et grilles d'appréciations de l'établissement"),
    # Notes
    ("grades.view", "grades", "Notes", PERM_VIEW, "Consulter les grilles de notes, moyennes et l'historique"),
    ("grades.create", "grades", "Notes", PERM_CREATE, "Saisir de nouvelles notes et observations"),
    ("grades.update", "grades", "Notes", PERM_UPDATE, "Modifier les notes et observations en période ouverte"),
    ("grades.validate", "grades", "Notes & Visas", PERM_VALIDATE, "Apposer le visa de direction et valider les résultats"),
    ("grades.delete", "grades", "Notes", PERM_DELETE, "Effacer une note saisie par erreur en période ouverte"),
    # Évaluations & Périodes
    ("evaluations.view", "evaluations", "Années & Périodes", PERM_VIEW, "Consulter les années scolaires et périodes d'évaluation"),
    ("evaluations.create", "evaluations", "Années & Périodes", PERM_CREATE, "Créer une année scolaire ou une période d'évaluation"),
    ("evaluations.update", "evaluations", "Années & Périodes", PERM_UPDATE, "Modifier les paramètres d'une année ou d'une période"),
    ("evaluations.validate", "evaluations", "Clôture périodes", PERM_VALIDATE, "Ouvrir ou clôturer une période ou une année scolaire"),
    ("evaluations.manage", "evaluations", "Réouverture admin", PERM_MANAGE, "Rouvrir exceptionnellement une année scolaire clôturée"),
    # Bulletins & Relevés
    ("bulletins.view", "bulletins", "Bulletins & Relevés", PERM_VIEW, "Consulter les relevés, classements, tableaux synoptiques et bulletins"),
    ("bulletins.create", "bulletins", "Bulletins & Relevés", PERM_CREATE, "Générer les bulletins périodiques et annuels"),
    ("bulletins.print", "bulletins", "Bulletins & Relevés", PERM_PRINT, "Imprimer et télécharger les PDF officiels"),
    ("bulletins.export", "bulletins", "Bulletins & Relevés", PERM_EXPORT, "Exporter les relevés, classements et tableaux synoptiques en Excel"),
    ("bulletins.validate", "bulletins", "Bulletins & Relevés", PERM_VALIDATE, "Valider les décisions figurant sur les bulletins annuels"),
    # Proposition de fin d'année
    ("proposition.view", "proposition", "Proposition fin d'année", PERM_VIEW, "Consulter la fiche de proposition de fin d'année"),
    ("proposition.create", "proposition", "Proposition fin d'année", PERM_CREATE, "Générer et archiver une trace de la proposition"),
    ("proposition.update", "proposition", "Proposition fin d'année", PERM_UPDATE, "Ajuster les propositions de passage ou redoublement"),
    ("proposition.validate", "proposition", "Proposition fin d'année", PERM_VALIDATE, "Valider définitivement les décisions de fin d'année"),
    ("proposition.print", "proposition", "Proposition fin d'année", PERM_PRINT, "Imprimer le PDF A4 paysage de la proposition"),
    ("proposition.export", "proposition", "Proposition fin d'année", PERM_EXPORT, "Exporter la proposition de fin d'année en Excel"),
    # Absences & Discipline
    ("absences.view", "absences", "Absences & Retards", PERM_VIEW, "Consulter le registre des absences, retards et discipline"),
    ("absences.create", "absences", "Absences & Retards", PERM_CREATE, "Enregistrer une absence, un retard ou un incident"),
    ("absences.update", "absences", "Absences & Retards", PERM_UPDATE, "Modifier ou justifier une absence ou un retard"),
    ("absences.delete", "absences", "Absences & Retards", PERM_DELETE, "Supprimer un enregistrement d'absence erroné"),
    # Comptabilité
    ("accounting.view", "accounting", "Comptabilité", PERM_VIEW, "Consulter les frais scolaires, paiements, dépenses et états financiers"),
    ("accounting.create", "accounting", "Comptabilité", PERM_CREATE, "Enregistrer un paiement, reçu ou dépense"),
    ("accounting.update", "accounting", "Comptabilité", PERM_UPDATE, "Modifier une écriture comptable non validée"),
    ("accounting.validate", "accounting", "Comptabilité", PERM_VALIDATE, "Valider les écritures et arrêtés de caisse"),
    ("accounting.export", "accounting", "Comptabilité", PERM_EXPORT, "Exporter les états financiers et reçus"),
    # Utilisateurs
    ("users.view", "users", "Comptes utilisateurs", PERM_VIEW, "Consulter les comptes utilisateurs de son périmètre"),
    ("users.create", "users", "Comptes utilisateurs", PERM_CREATE, "Créer un nouveau compte utilisateur"),
    ("users.update", "users", "Comptes utilisateurs", PERM_UPDATE, "Modifier, suspendre ou réinitialiser le mot de passe d'un compte"),
    ("users.delete", "users", "Comptes utilisateurs", PERM_DELETE, "Désactiver un compte utilisateur"),
    ("users.manage", "users", "Administration comptes", PERM_MANAGE, "Administrer l'ensemble des accès utilisateurs de son périmètre"),
    # Rôles et permissions
    ("rbac.view", "rbac", "Rôles & Permissions", PERM_VIEW, "Consulter les rôles, la matrice des permissions et les droits effectifs"),
    ("rbac.create", "rbac", "Rôles & Permissions", PERM_CREATE, "Créer de nouveaux rôles ou permissions personnalisés"),
    ("rbac.update", "rbac", "Rôles & Permissions", PERM_UPDATE, "Modifier les rôles et la matrice des permissions"),
    ("rbac.delete", "rbac", "Rôles & Permissions", PERM_DELETE, "Supprimer un rôle inutilisé ou réaffecté"),
    ("rbac.manage", "rbac", "Attribution RBAC", PERM_MANAGE, "Attribuer les rôles et permissions individuelles exceptionnelles"),
    # Paramètres & Sauvegardes
    ("settings.view", "settings", "Paramètres & Sauvegardes", PERM_VIEW, "Consulter les paramètres de l'établissement et les sauvegardes"),
    ("settings.update", "settings", "Paramètres & Sauvegardes", PERM_UPDATE, "Modifier les paramètres de l'établissement et créer des sauvegardes"),
    ("settings.manage", "settings", "Restauration & Système", PERM_MANAGE, "Gérer la restauration SQLite, les politiques système et le multi-établissements"),
    # Journal d'audit
    ("audit.view", "audit", "Journal d'audit", PERM_VIEW, "Consulter le journal d'audit et des opérations sensibles"),
    ("audit.export", "audit", "Journal d'audit", PERM_EXPORT, "Exporter le journal d'audit"),
]

ALL_PERMISSION_CODES: tuple[str, ...] = tuple(item[0] for item in PERMISSIONS_CATALOG)

# Rôles prédéfinis initiaux (§3) : (code, name, is_global_scope, description, permissions_list)
PREDEFINED_ROLES: list[tuple[str, str, bool, str, list[str]]] = [
    (
        ROLE_SUPER_ADMIN,
        "Super administrateur",
        True,
        "Accès intégral à toutes les fonctionnalités, tous les établissements et la configuration globale du logiciel.",
        list(ALL_PERMISSION_CODES),
    ),
    (
        ROLE_ADMIN_ETAB,
        "Administrateur d'établissement",
        False,
        "Gestion administrative et fonctionnelle complète de son établissement, de ses utilisateurs, classes et paramètres autorisés.",
        [
            c
            for c in ALL_PERMISSION_CODES
            if c not in ("rbac.create", "rbac.update", "rbac.delete")
        ],
    ),
    (
        ROLE_DIRECTEUR,
        "Directeur",
        False,
        "Supervision générale de l'établissement, gestion pédagogique, validation des résultats et production des rapports.",
        [
            "dashboard.view",
            "students.view", "students.create", "students.update", "students.delete", "students.export",
            "enrollments.view", "enrollments.create", "enrollments.update", "enrollments.validate",
            "classes.view", "classes.create", "classes.update", "classes.delete",
            "teachers.view", "teachers.manage",
            "subjects.view", "subjects.create", "subjects.update", "subjects.delete", "subjects.manage",
            "grades.view", "grades.create", "grades.update", "grades.validate", "grades.delete",
            "evaluations.view", "evaluations.create", "evaluations.update", "evaluations.validate",
            "bulletins.view", "bulletins.create", "bulletins.print", "bulletins.export", "bulletins.validate",
            "proposition.view", "proposition.create", "proposition.update", "proposition.validate", "proposition.print", "proposition.export",
            "absences.view", "absences.create", "absences.update", "absences.delete",
            "settings.view", "settings.update",
        ],
    ),
    (
        ROLE_SECRETAIRE,
        "Secrétaire",
        False,
        "Gestion des inscriptions, des dossiers des élèves, des informations administratives et des documents scolaires autorisés.",
        [
            "dashboard.view",
            "students.view", "students.create", "students.update", "students.export",
            "enrollments.view", "enrollments.create", "enrollments.update",
            "classes.view",
            "evaluations.view",
            "absences.view", "absences.create", "absences.update",
            "bulletins.view", "bulletins.print", "bulletins.export",
            "settings.view",
        ],
    ),
    (
        ROLE_ENSEIGNANT,
        "Enseignant",
        False,
        "Accès aux classes affectées, gestion des identités élèves et matières/barèmes de sa classe, saisie des notes en période ouverte et consultation des résultats.",
        [
            "dashboard.view",
            "students.view", "students.update", "students.export",
            "enrollments.view", "enrollments.update",
            "classes.view",
            "subjects.view", "subjects.create", "subjects.update",
            "grades.view", "grades.create", "grades.update", "grades.delete",
            "evaluations.view",
            "bulletins.view", "bulletins.create", "bulletins.print", "bulletins.export",
            "proposition.view", "proposition.create", "proposition.print", "proposition.export",
            "absences.view", "absences.create", "absences.update",
            "settings.view",
        ],
    ),
    (
        ROLE_COMPTABLE,
        "Comptable",
        False,
        "Gestion des frais scolaires, paiements, dépenses, reçus et états financiers, sans accès aux données pédagogiques confidentielles.",
        [
            "dashboard.view",
            "accounting.view", "accounting.create", "accounting.update", "accounting.validate", "accounting.export",
        ],
    ),
    (
        ROLE_SURVEILLANT,
        "Surveillant",
        False,
        "Gestion du suivi des élèves, des absences, des retards et de la discipline.",
        [
            "dashboard.view",
            "students.view",
            "classes.view",
            "absences.view", "absences.create", "absences.update", "absences.delete",
        ],
    ),
    (
        ROLE_PARENT,
        "Parent / Tuteur",
        False,
        "Consultation strictement limitée aux informations et bulletins scolaires des enfants qui lui sont rattachés.",
        [
            "dashboard.view",
            "students.view",
            "bulletins.view", "bulletins.print",
            "absences.view",
        ],
    ),
    (
        ROLE_CONSULTATION,
        "Consultation",
        False,
        "Consultation et impression des documents scolaires autorisés de l'établissement, sans aucune modification.",
        [
            "dashboard.view",
            "students.view", "students.export",
            "classes.view",
            "subjects.view",
            "evaluations.view",
            "grades.view",
            "bulletins.view", "bulletins.print", "bulletins.export",
            "proposition.view", "proposition.print", "proposition.export",
            "settings.view",
        ],
    ),
]


# Cache mémoire avec invalidation immédiate par numéro de génération (§11)
_CACHE_GENERATION: int = 1
_USER_PERMS_CACHE: dict[tuple[int, int], frozenset[str]] = {}


def invalidate_rbac_cache() -> None:
    """Invalide immédiatement le cache des permissions pour tous les utilisateurs."""
    global _CACHE_GENERATION
    _CACHE_GENERATION += 1
    _USER_PERMS_CACHE.clear()
    if has_request_context() and hasattr(g, "_rbac_req_cache"):
        g._rbac_req_cache.clear()


def normalize_legacy_role_to_rbac_code(raw_code: str | None) -> str:
    code = (raw_code or ROLE_ENSEIGNANT).strip().upper()
    if code == ROLE_ADMIN:
        return ROLE_SUPER_ADMIN
    return code


def ensure_rbac_initialized() -> None:
    """Initialise de manière idempotente l'établissement principal, les permissions, les rôles initiaux et les rattachements existants."""
    insp = inspect(db.engine)
    if not insp.has_table("rbac_permissions") or not insp.has_table("rbac_roles"):
        return

    changed = False

    # 1. Garantir l'établissement principal (id=1) synchronisé avec EstablishmentSettings
    if insp.has_table("establishments"):
        est1 = db.session.get(Establishment, 1)
        settings = EstablishmentSettings.get_singleton() if insp.has_table("establishment_settings") else None
        if est1 is None:
            est1 = Establishment(
                id=1,
                code=(settings.school_code if settings and settings.school_code else "ETAB-001"),
                name=(settings.school_name if settings and settings.school_name else "Établissement Principal"),
                ceb=(settings.ceb if settings else ""),
                locality=(settings.locality if settings else None),
                address=(settings.address if settings else None),
                phone=(settings.phone if settings else None),
                email=(settings.email if settings else None),
                is_active=True,
            )
            db.session.add(est1)
            db.session.flush()
            changed = True

    # 2. Garantir le catalogue des permissions granulaires
    existing_perms = {p.code: p for p in Permission.query.all()}
    for code, module, resource, action, desc in PERMISSIONS_CATALOG:
        if code not in existing_perms:
            perm = Permission(
                code=code,
                module=module,
                module_label=MODULE_LABELS_DICT.get(module, module.capitalize()),
                resource=resource,
                action=action,
                description=desc,
            )
            db.session.add(perm)
            existing_perms[code] = perm
            changed = True
    if changed:
        db.session.flush()

    # 3. Garantir les rôles initiaux prédéfinis (sans écraser les personnalisations existantes)
    existing_roles = {r.code: r for r in Role.query.all()}
    for r_code, r_name, is_global, r_desc, perm_codes in PREDEFINED_ROLES:
        if r_code not in existing_roles:
            role_obj = Role(
                code=r_code,
                name=r_name,
                description=r_desc,
                is_active=True,
                is_system=True,
                is_global_scope=is_global,
            )
            db.session.add(role_obj)
            db.session.flush()
            for p_code in perm_codes:
                p_obj = existing_perms.get(p_code)
                if p_obj is not None:
                    db.session.add(RolePermission(role_id=role_obj.id, permission_id=p_obj.id))
            existing_roles[r_code] = role_obj
            changed = True

    # 4. Rattacher automatiquement tout compte existant n'ayant pas encore d'entrée UserRole (§13 migration douce)
    if insp.has_table("users") and insp.has_table("rbac_user_roles"):
        users_without_roles = (
            User.query.outerjoin(UserRole, User.id == UserRole.user_id)
            .filter(UserRole.id.is_(None))
            .all()
        )
        for u in users_without_roles:
            target_code = normalize_legacy_role_to_rbac_code(u.primary_role_code or u.role)
            role_obj = existing_roles.get(target_code) or existing_roles.get(ROLE_CONSULTATION)
            if role_obj is not None:
                db.session.add(UserRole(user_id=u.id, role_id=role_obj.id))
                if not u.primary_role_code:
                    u.primary_role_code = role_obj.code
                if u.establishment_id is None and not role_obj.is_global_scope:
                    u.establishment_id = 1
                changed = True

    if changed:
        db.session.commit()
        invalidate_rbac_cache()


def ensure_user_role_synced(user: User) -> list[Role]:
    """Retourne les rôles actifs d'un utilisateur, avec synchronisation transparente si créé via `User(role=...)`."""
    if user is None or not getattr(user, "is_authenticated", True):
        return []
    if user.user_roles:
        return [ur.role for ur in user.user_roles if ur.role is not None and ur.role.is_active]

    # Fallback transparent pour les objets créés directement en Python (ex. fixtures de tests)
    target_code = normalize_legacy_role_to_rbac_code(user.primary_role_code or user.role)
    role_obj = Role.query.filter_by(code=target_code).first()
    if role_obj is None:
        ensure_rbac_initialized()
        role_obj = Role.query.filter_by(code=target_code).first()
    if role_obj is not None and user.id is not None:
        existing_link = UserRole.query.filter_by(user_id=user.id, role_id=role_obj.id).first()
        if existing_link is None:
            db.session.add(UserRole(user_id=user.id, role_id=role_obj.id))
            db.session.flush()
            invalidate_rbac_cache()
        return [role_obj] if role_obj.is_active else []
    return [role_obj] if (role_obj and role_obj.is_active) else []


def get_effective_permissions(user: User | None) -> frozenset[str]:
    """Calcule l'ensemble exact des permissions effectives d'un utilisateur (Sécurité par défaut : vide si non autorisé)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    if not user.is_active or user.is_locked() or user.is_suspended():
        return frozenset()

    uid = user.id
    if uid is not None:
        cache_key = (uid, _CACHE_GENERATION)
        if has_request_context():
            req_cache = getattr(g, "_rbac_req_cache", None)
            if req_cache is None:
                req_cache = {}
                g._rbac_req_cache = req_cache
            if cache_key in req_cache:
                return req_cache[cache_key]
        if cache_key in _USER_PERMS_CACHE:
            return _USER_PERMS_CACHE[cache_key]

    active_roles = ensure_user_role_synced(user)
    perms: set[str] = set()
    for role_obj in active_roles:
        for rp in role_obj.role_permissions:
            if rp.permission:
                perms.add(rp.permission.code)

    # Application des permissions individuelles exceptionnelles (§6 : DENY l'emporte toujours sur ALLOW)
    now = utcnow_naive()
    denied: set[str] = set()
    for ov in user.permission_overrides:
        if ov.expires_at is not None and ov.expires_at <= now:
            continue
        if ov.permission is None:
            continue
        if ov.effect == "ALLOW":
            perms.add(ov.permission.code)
        elif ov.effect == "DENY":
            denied.add(ov.permission.code)

    effective = frozenset(perms - denied)
    if uid is not None:
        _USER_PERMS_CACHE[(uid, _CACHE_GENERATION)] = effective
        if has_request_context():
            g._rbac_req_cache[(uid, _CACHE_GENERATION)] = effective
    return effective


def has_permission(user: User | None, perm_code: str) -> bool:
    """Vérifie si l'utilisateur détient explicitement la permission demandée."""
    if not perm_code:
        return False
    return perm_code in get_effective_permissions(user)


def has_any_permission(user: User | None, *perm_codes: str) -> bool:
    eff = get_effective_permissions(user)
    return any(code in eff for code in perm_codes)


def is_super_admin(user: User | None) -> bool:
    """Indique si l'utilisateur possède un rôle actif à portée globale (`SUPER_ADMIN`)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not user.is_active or user.is_locked() or user.is_suspended():
        return False
    for role_obj in ensure_user_role_synced(user):
        if role_obj.is_global_scope or role_obj.code == ROLE_SUPER_ADMIN:
            return True
    return False


def is_strictly_scoped_teacher(user: User) -> bool:
    """Vrai si l'utilisateur agit uniquement sous le périmètre restreint aux classes affectées (Enseignant)."""
    if is_super_admin(user):
        return False
    active_codes = {r.code for r in ensure_user_role_synced(user)}
    broad_roles = {ROLE_ADMIN_ETAB, ROLE_DIRECTEUR, ROLE_SECRETAIRE, ROLE_SURVEILLANT, ROLE_CONSULTATION}
    if active_codes & broad_roles:
        return False
    return ROLE_ENSEIGNANT in active_codes


def is_strictly_scoped_parent(user: User) -> bool:
    """Vrai si l'utilisateur n'accède aux données scolaires qu'en tant que Parent / Tuteur de ses enfants."""
    if is_super_admin(user):
        return False
    active_codes = {r.code for r in ensure_user_role_synced(user)}
    non_parent_roles = active_codes - {ROLE_PARENT}
    return ROLE_PARENT in active_codes and not non_parent_roles


def get_user_accessible_establishment_ids(user: User | None) -> set[int] | None:
    """Retourne `None` si portée globale (Super Admin), sinon l'ensemble des `establishment_id` autorisés (§8)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    if not user.is_active or user.is_locked() or user.is_suspended():
        return set()
    if is_super_admin(user):
        return None
    allowed: set[int] = set()
    if user.establishment_id is not None:
        allowed.add(user.establishment_id)
    for scope_row in user.extra_establishments:
        if scope_row.establishment and scope_row.establishment.is_active:
            allowed.add(scope_row.establishment_id)
    if not allowed:
        allowed.add(1)
    return allowed


def user_can_access_establishment(user: User | None, establishment_id: int | None) -> bool:
    """Vérifie côté serveur l'isolation par établissement (§8)."""
    eff_est_id = establishment_id if establishment_id is not None else 1
    allowed = get_user_accessible_establishment_ids(user)
    if allowed is None:
        return True
    return eff_est_id in allowed


def get_current_establishment_id(user: User | None = None) -> int:
    """Détermine l'établissement actif dans le contexte de session de l'utilisateur."""
    if has_request_context():
        sess_est = session.get("selected_establishment_id")
        if sess_est and (user is None or user_can_access_establishment(user, int(sess_est))):
            est = db.session.get(Establishment, int(sess_est))
            if est is not None and est.is_active:
                return est.id
    if user is not None and getattr(user, "is_authenticated", False) and user.establishment_id:
        return user.establishment_id
    return 1


def get_parent_linked_student_ids(user: User) -> set[int]:
    return {lnk.student_id for lnk in ParentStudentLink.query.filter_by(user_id=user.id).all()}


# ==============================================================================
# Opérations d'administration centralisée des rôles, permissions et utilisateurs
# ==============================================================================


def sanitize_role_code(raw_code: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9_]+", "_", (raw_code or "").strip().upper()).strip("_")
    if len(cleaned) < 2:
        raise RBACSecurityError("Le code du rôle doit comporter au moins 2 caractères alphanumériques.")
    return cleaned[:50]


def create_role(
    *,
    name: str,
    code: str,
    description: str | None = None,
    is_active: bool = True,
    copy_from_role_id: int | None = None,
) -> Role:
    clean_name = (name or "").strip()
    clean_code = sanitize_role_code(code)
    if len(clean_name) < 2:
        raise RBACSecurityError("Le nom du rôle est obligatoire (2 caractères minimum).")
    if Role.query.filter_by(code=clean_code).first():
        raise RBACSecurityError(f"Un rôle avec le code « {clean_code} » existe déjà.")
    if Role.query.filter(db.func.lower(Role.name) == clean_name.lower()).first():
        raise RBACSecurityError(f"Un rôle nommé « {clean_name} » existe déjà.")

    role = Role(
        code=clean_code,
        name=clean_name[:120],
        description=(description or "").strip() or None,
        is_active=is_active,
        is_system=False,
        is_global_scope=False,
    )
    db.session.add(role)
    db.session.flush()

    if copy_from_role_id:
        src = db.session.get(Role, copy_from_role_id)
        if src is not None:
            for rp in src.role_permissions:
                db.session.add(RolePermission(role_id=role.id, permission_id=rp.permission_id))

    invalidate_rbac_cache()
    return role


def update_role(
    *,
    role: Role,
    name: str,
    description: str | None,
    is_active: bool,
) -> Role:
    clean_name = (name or "").strip()
    if len(clean_name) < 2:
        raise RBACSecurityError("Le nom du rôle est obligatoire.")
    dup = Role.query.filter(
        db.func.lower(Role.name) == clean_name.lower(),
        Role.id != role.id,
    ).first()
    if dup is not None:
        raise RBACSecurityError(f"Un autre rôle porte déjà le nom « {clean_name} ».")

    if role.code == ROLE_SUPER_ADMIN and not is_active:
        raise RBACSecurityError("Le rôle Super administrateur ne peut pas être désactivé.")

    role.name = clean_name[:120]
    role.description = (description or "").strip() or None
    role.is_active = is_active
    role.updated_at = utcnow_naive()
    invalidate_rbac_cache()
    return role


def delete_role(role: Role, reassign_to_role_id: int | None = None) -> int:
    """Supprime un rôle s'il n'est pas utilisé ou après réaffectation contrôlée de ses utilisateurs (§3)."""
    if role.code == ROLE_SUPER_ADMIN:
        raise RBACSecurityError("Le rôle Super administrateur est indispensable et ne peut pas être supprimé.")

    assigned_links = UserRole.query.filter_by(role_id=role.id).all()
    reassigned_count = 0
    if assigned_links:
        if not reassign_to_role_id or reassign_to_role_id == role.id:
            raise RBACSecurityError(
                f"Ce rôle est attribué à {len(assigned_links)} utilisateur(s). "
                "Sélectionnez un rôle de réaffectation avant de pouvoir le supprimer."
            )
        target_role = db.session.get(Role, reassign_to_role_id)
        if target_role is None or not target_role.is_active:
            raise RBACSecurityError("Le rôle de réaffectation sélectionné est introuvable ou inactif.")

        for ur in assigned_links:
            already = UserRole.query.filter_by(user_id=ur.user_id, role_id=target_role.id).first()
            if already is None:
                db.session.add(
                    UserRole(
                        user_id=ur.user_id,
                        role_id=target_role.id,
                        assigned_by_id=ur.assigned_by_id,
                    )
                )
            user_obj = db.session.get(User, ur.user_id)
            if user_obj and user_obj.primary_role_code == role.code:
                user_obj.role = target_role.code
            db.session.delete(ur)
            reassigned_count += 1
        db.session.flush()

    db.session.delete(role)
    db.session.flush()
    invalidate_rbac_cache()
    return reassigned_count


def update_role_permissions(role: Role, permission_ids: list[int], actor: User) -> int:
    """Met à jour la matrice des permissions d'un rôle avec protection anti-escalade et anti-blocage."""
    if role.code == ROLE_SUPER_ADMIN and not is_super_admin(actor):
        raise RBACSecurityError("Seul un Super administrateur peut modifier le rôle Super administrateur.")

    valid_perms = (
        Permission.query.filter(Permission.id.in_(permission_ids)).all()
        if permission_ids
        else []
    )
    selected_codes = {p.code for p in valid_perms}

    # Empêcher de retirer les permissions RBAC vitales au rôle SUPER_ADMIN
    if role.code == ROLE_SUPER_ADMIN:
        vital = {"rbac.view", "rbac.update", "rbac.manage", "users.view", "users.manage"}
        if not vital.issubset(selected_codes):
            raise RBACSecurityError(
                "Impossible de retirer les permissions vitales d'administration RBAC au rôle Super administrateur."
            )

    # Un administrateur non-global ne peut pas accorder des permissions qu'il ne possède pas lui-même
    if not is_super_admin(actor):
        actor_perms = get_effective_permissions(actor)
        forbidden = selected_codes - actor_perms
        if forbidden:
            raise RBACSecurityError(
                "Séparation des responsabilités : vous ne pouvez pas attribuer à un rôle des permissions que vous ne détenez pas."
            )

    RolePermission.query.filter_by(role_id=role.id).delete()
    for p in valid_perms:
        db.session.add(RolePermission(role_id=role.id, permission_id=p.id))
    role.updated_at = utcnow_naive()
    db.session.flush()
    invalidate_rbac_cache()
    return len(valid_perms)


def create_custom_permission(
    *,
    code: str,
    module: str,
    module_label: str,
    resource: str,
    action: str,
    description: str,
) -> Permission:
    clean_code = re.sub(r"[^a-z0-9_.-]+", "_", (code or "").strip().lower()).strip("_.")
    clean_mod = re.sub(r"[^a-z0-9_]+", "_", (module or "").strip().lower()).strip("_")
    clean_act = (action or "").strip().upper()
    if len(clean_code) < 3 or "." not in clean_code:
        raise RBACSecurityError("Le code de permission doit être au format 'module.action' (ex. bibliotheque.view).")
    if not clean_mod:
        raise RBACSecurityError("Le code du module est obligatoire.")
    if clean_act not in ALL_PERMISSION_ACTIONS:
        raise RBACSecurityError("Action standard invalide.")
    if Permission.query.filter_by(code=clean_code).first():
        raise RBACSecurityError(f"La permission « {clean_code} » existe déjà.")

    perm = Permission(
        code=clean_code[:100],
        module=clean_mod[:60],
        module_label=(module_label or MODULE_LABELS_DICT.get(clean_mod) or clean_mod.capitalize()).strip()[:100],
        resource=(resource or module_label or clean_mod).strip()[:80],
        action=clean_act,
        description=(description or clean_code).strip()[:255],
    )
    db.session.add(perm)
    db.session.flush()

    # Accorder automatiquement toute nouvelle permission au rôle SUPER_ADMIN
    sa_role = Role.query.filter_by(code=ROLE_SUPER_ADMIN).first()
    if sa_role is not None:
        db.session.add(RolePermission(role_id=sa_role.id, permission_id=perm.id))
    db.session.flush()
    invalidate_rbac_cache()
    return perm


def count_other_active_super_admins(excluded_user_id: int) -> int:
    sa_role = Role.query.filter_by(code=ROLE_SUPER_ADMIN, is_active=True).first()
    if sa_role is None:
        return 0
    return (
        User.query.join(UserRole, User.id == UserRole.user_id)
        .filter(
            UserRole.role_id == sa_role.id,
            User.is_active.is_(True),
            User.id != excluded_user_id,
        )
        .count()
    )


def assign_roles_to_user(
    *,
    target_user: User,
    role_ids: list[int],
    actor: User,
) -> list[Role]:
    """Affecte un ou plusieurs rôles à un utilisateur avec contrôles stricts anti-auto-escalade (§6, §12)."""
    if not role_ids:
        raise RBACSecurityError("Veuillez attribuer au moins un rôle à l'utilisateur.")

    roles = Role.query.filter(Role.id.in_(role_ids)).order_by(Role.id).all()
    if not roles:
        raise RBACSecurityError("Aucun rôle valide sélectionné.")

    new_codes = {r.code for r in roles}
    old_codes = {r.code for r in ensure_user_role_synced(target_user)}

    # Interdiction absolue d'auto-attribution de nouveaux rôles / privilèges (§12)
    if actor.id == target_user.id and new_codes != old_codes:
        raise RBACSecurityError(
            "Sécurité RBAC : un utilisateur ne peut pas modifier lui-même les rôles de son propre compte."
        )

    # Un non-Super-Admin ne peut ni attribuer ni retirer le rôle SUPER_ADMIN, ni gérer un compte hors établissement
    if not is_super_admin(actor):
        if ROLE_SUPER_ADMIN in new_codes or ROLE_SUPER_ADMIN in old_codes:
            raise RBACSecurityError(
                "Seul un Super administrateur peut attribuer ou modifier un compte Super administrateur."
            )
        if not user_can_access_establishment(actor, target_user.establishment_id):
            raise RBACSecurityError(
                "Isolation multi-établissements : vous ne pouvez pas gérer un utilisateur d'un autre établissement."
            )
        actor_perms = get_effective_permissions(actor)
        for r in roles:
            r_perms = {rp.permission.code for rp in r.role_permissions if rp.permission}
            if not r_perms.issubset(actor_perms):
                raise RBACSecurityError(
                    f"Vous ne pouvez pas attribuer le rôle « {r.name} » car il comporte des permissions supérieures aux vôtres."
                )

    # Protection du dernier Super administrateur actif
    if ROLE_SUPER_ADMIN in old_codes and ROLE_SUPER_ADMIN not in new_codes:
        if count_other_active_super_admins(target_user.id) == 0:
            raise RBACSecurityError(
                "Opération refusée : le système doit conserver au moins un Super administrateur actif."
            )

    UserRole.query.filter_by(user_id=target_user.id).delete()
    for r in roles:
        db.session.add(UserRole(user_id=target_user.id, role_id=r.id, assigned_by_id=actor.id))

    # Synchroniser le rôle principal affiché / historique
    primary = roles[0]
    for candidate_code in (
        ROLE_SUPER_ADMIN,
        ROLE_ADMIN_ETAB,
        ROLE_DIRECTEUR,
        ROLE_ENSEIGNANT,
        ROLE_SECRETAIRE,
        ROLE_COMPTABLE,
        ROLE_SURVEILLANT,
        ROLE_PARENT,
        ROLE_CONSULTATION,
    ):
        found = next((r for r in roles if r.code == candidate_code), None)
        if found is not None:
            primary = found
            break
    target_user.role = primary.code
    db.session.flush()
    invalidate_rbac_cache()
    return roles


def set_user_permission_override(
    *,
    target_user: User,
    permission: Permission,
    effect: str,
    reason: str,
    actor: User,
    expires_at: datetime | None = None,
) -> UserPermissionOverride:
    """Ajoute ou met à jour une permission individuelle exceptionnelle tracée (§6)."""
    clean_effect = (effect or "").strip().upper()
    clean_reason = (reason or "").strip()
    if clean_effect not in ("ALLOW", "DENY"):
        raise RBACSecurityError("L'effet de la permission exceptionnelle doit être 'ALLOW' ou 'DENY'.")
    if len(clean_reason) < 3:
        raise RBACSecurityError(
            "Veuillez saisir un motif explicite (min. 3 caractères) pour justifier cette permission individuelle exceptionnelle."
        )
    if actor.id == target_user.id:
        raise RBACSecurityError(
            "Sécurité RBAC : un utilisateur ne peut pas s'attribuer ou modifier lui-même des permissions individuelles."
        )
    if not is_super_admin(actor):
        if not user_can_access_establishment(actor, target_user.establishment_id):
            raise RBACSecurityError("Isolation multi-établissements : utilisateur hors de votre périmètre.")
        if is_super_admin(target_user):
            raise RBACSecurityError("Impossible de modifier les permissions d'un Super administrateur.")
        if permission.code not in get_effective_permissions(actor):
            raise RBACSecurityError(
                "Vous ne pouvez pas accorder ou restreindre une permission que vous ne détenez pas vous-même."
            )

    ov = UserPermissionOverride.query.filter_by(
        user_id=target_user.id, permission_id=permission.id
    ).first()
    if ov is None:
        ov = UserPermissionOverride(
            user_id=target_user.id,
            permission_id=permission.id,
            effect=clean_effect,
            reason=clean_reason[:255],
            granted_by_id=actor.id,
            expires_at=expires_at,
        )
        db.session.add(ov)
    else:
        ov.effect = clean_effect
        ov.reason = clean_reason[:255]
        ov.granted_by_id = actor.id
        ov.expires_at = expires_at
        ov.updated_at = utcnow_naive()

    db.session.flush()
    invalidate_rbac_cache()
    return ov


def remove_user_permission_override(override_id: int, actor: User) -> UserPermissionOverride:
    ov = db.session.get(UserPermissionOverride, override_id)
    if ov is None:
        raise RBACSecurityError("Permission exceptionnelle introuvable.")
    if actor.id == ov.user_id:
        raise RBACSecurityError(
            "Sécurité RBAC : vous ne pouvez pas retirer vous-même une restriction ou permission individuelle sur votre propre compte."
        )
    if not is_super_admin(actor) and not user_can_access_establishment(actor, ov.user.establishment_id):
        raise RBACSecurityError("Isolation multi-établissements : opération non autorisée.")
    db.session.delete(ov)
    db.session.flush()
    invalidate_rbac_cache()
    return ov
