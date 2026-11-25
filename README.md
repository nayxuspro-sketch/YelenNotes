# Yelen Notes

**Application web locale, hors ligne et portable de gestion et de calcul des notes scolaires pour les écoles primaires du Burkina Faso.**

Conçue conformément au [Cahier des charges](docs/references/Cahier_des_charges_Yelen_Notes.md), à l'[Architecture technique](docs/references/Architecture_technique_Yelen_Notes.md) et au [Dossier de conception validé](docs/conception/01_modele_donnees_calculs_securite_maquettes.md).

---

## Caractéristiques principales

- **100 % hors ligne :** aucun CDN, aucune police distante, aucun service cloud requis. Bootstrap 5, JavaScript natif et polices TrueType (`DejaVuSans`) embarqués localement dans `resources/`.
- **Base de production 100 % vierge et paramétrable :** aucune règle pédagogique imposée d'office ; guide de démarrage en 5 étapes permettant à l'école de configurer ses années, périodes, barèmes, coefficients et grilles d'appréciations. Base de démonstration fictive fournie séparément (`--demo` -> `ecole_demo.db`).
- **Deux modes d'exploitation avec un seul exécutable :**
  - **Monoposte :** `http://127.0.0.1:5000` (un seul PC).
  - **Multiposte réseau local :** `http://<IP-SERVEUR>:5000` via le serveur HTTP **Waitress** et **SQLite en mode WAL** local au serveur (détection des conflits simultanés HTTP `409` sans perte silencieuse).
- **Moteur mathématique exact (`decimal.Decimal`) :** conversions de barème explicites, distinction stricte entre note numérique (y compris `0`), absence (`ABS`), dispense (`DISP`) et note non saisie (jamais transformée en `0`), arrondi `ROUND_HALF_UP` paramétrable, gestion des ex æquo (`1er`/`1re`, `1er ex`/`1re ex`, `3e`), moyennes périodiques et annuelles.
- **États officiels imprimables (ReportLab PDF A4 & OpenPyXL Excel) :** relevés de notes, classements par ordre de mérite (périodiques et annuels), bulletins scolaires périodiques et annuels (individuels, par sélection ou classe entière) avec aperçu avant impression et historisation pluriannuelle de la direction et du titulariat.
- **Sécurité et fiabilité :** RBAC contrôlé côté serveur (`ADMIN`, `DIRECTEUR`, `ENSEIGNANT`, `CONSULTATION`), hachage `scrypt`, protection CSRF (formulaires & AJAX), verrouillage anti-force brute, expiration d'inactivité, journaux d'audit (`audit_logs`), sauvegardes SQLite à chaud vérifiées (`sqlite3.backup()` + `PRAGMA integrity_check` + `SHA-256`) et restauration sécurisée.

---

## Organisation du dépôt

```text
YelenNotes/
├── run.py                          # Point d'entrée Waitress & utilitaires CLI
├── requirements.txt                # Dépendances Python avec versions figées
├── requirements-build.txt          # Dépendances incluant PyInstaller pour compilation Windows
├── VERSION.txt                     # Version applicative (1.0.0)
├── README.txt                      # Lisez-moi destiné au dossier portable Windows
├── config/
│   └── yelen_notes.example.ini     # Modèle de configuration monoposte/multiposte/stockage
├── resources/                      # Templates Jinja2, Bootstrap 5 local, CSS, JS, polices TTF
├── yelen_notes/                    # Code source modulaire (models, security, services, blueprints)
├── migrations/                     # Migrations de schéma Flask-Migrate / Alembic
├── packaging/                      # Spécification PyInstaller ONEDIR et scripts Windows (.bat)
├── tests/                          # Suite de tests automatisés pytest
└── docs/                           # Références, dossier de conception et guides en français
```

---

## Lancement en développement / évaluation

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1. Démarrer en mode monoposte sur base vierge (http://127.0.0.1:5000)
.venv/bin/python run.py --mode monoposte

# 2. Démarrer en mode multiposte réseau local (http://0.0.0.0:5000)
.venv/bin/python run.py --mode multiposte

# 3. Démarrer sur la base de démonstration fictive séparée (ecole_demo.db)
.venv/bin/python run.py --demo

# 4. Vérifier l'intégrité SQLite ou récupérer le compte administrateur
.venv/bin/python run.py --check-db
.venv/bin/python run.py --reset-admin admin
```

---

## Exécution des tests automatisés

```bash
.venv/bin/pytest -v
```

---

## Compilation et lancement 1-clic sous Windows 10/11 (Installation 100 % automatique)

Deux lanceurs automatiques sont fournis directement à la racine du dossier (ils détectent ou installent automatiquement Python 64 bits, créent l'environnement virtuel et installent toutes les bibliothèques manquantes) :

- **`1_Installer_et_Lancer_Directement.bat`** : installe automatiquement tout ce qui manque et ouvre immédiatement Yelen Notes dans votre navigateur.
- **`2_Compiler_Dossier_Portable_EXE.bat`** *(ou `packaging\build_windows_portable.bat`)* : installe automatiquement tout ce qui manque et produit le dossier autonome **`dist\YELEN-NOTES\`** (`YelenNotes.exe`) prêt à être copié-collé sur clé USB pour n'importe quel PC d'école sans Python ni connexion Internet.

---

## Documentation complète en français

- [Guide utilisateur](docs/GUIDE_UTILISATEUR_FR.md)
- [Guide d'installation et de déploiement monoposte / multiposte](docs/GUIDE_INSTALLATION_DEPLOIEMENT_FR.md)
- [Procédure de sauvegarde, restauration, récupération admin et mise à jour](docs/PROCEDURE_SAUVEGARDE_MISE_A_JOUR_FR.md)
- [Dossier de conception et règles de calcul validées](docs/conception/01_modele_donnees_calculs_securite_maquettes.md)
