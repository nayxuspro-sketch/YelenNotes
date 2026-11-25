# Dossier de Conception et de Validation Préalable — Yelen Notes (Phase 1)

**Projet :** Yelen Notes — Application locale de gestion et de calcul des notes (Écoles primaires du Burkina Faso)  
**Références :** `docs/references/Cahier_des_charges_Yelen_Notes.md` (§21, §22, §24) et `docs/references/Architecture_technique_Yelen_Notes.md` (§11, §13)  
**Statut :** Soumis pour validation préalable avant développement des modules applicatifs  

---

## 1. Synthèse des décisions fonctionnelles et pédagogiques retenues

Conformément aux choix arrêtés lors du cadrage initial :

| Point de cadrage (§22 CdC) | Décision retenue | Paramétrabilité dans l'application |
|---|---|---|
| **État initial de la base** | **Base vierge (100 % à configurer par l'école)** : aucune structure de périodes, aucun barème, aucune matière et aucune grille d'appréciations ne sont imposés d'office en production. Un jeu de démonstration séparé et explicitement identifié (`ecole_demo.db`) est fourni à part. | Assistant et écrans d'administration permettant de tout créer et modifier par année et par classe. |
| **Pays et devise proposés** | Pays : `Burkina Faso` — Devise : `La Patrie ou la Mort, nous Vaincrons` | Modifiables dans *Paramètres de l'établissement*. |
| **Structure de l'année** | Nombre, intitulés, codes courts, ordre et dates des périodes libres (ex. 3 trimestres, 2 semestres, 6 séquences…). | Configurable par année scolaire ; statuts `BROUILLON`, `OUVERTE`, `CLOTUREE`. |
| **Barèmes & conversions** | Chaque classe définit son **barème de moyenne générale** (ex. `/10` ou `/20`). Chaque matière définit `note_minimale`, `note_maximale`, `moyenne_sur` (barème de référence) et `coefficient`. | Conversion proportionnelle explicite et contrôlée par matière. |
| **Traitement des absences** | **Par défaut : Absence comptée `0`** dans la matière (coefficient maintenu). | Modifiable dans la politique de calcul : `COMPTER_ZERO` (défaut), `NEUTRALISER` ou `NON_CLASSE`. |
| **Notes manquantes (`NON_SAISIE`)** | **Jamais transformées en zéro.** Un élève ayant au moins une matière active obligatoire `NON_SAISIE` a un dossier **Incomplet** et est **Non classé** tant que la saisie n'est pas achevée. | Distinction stricte en base (`NULL` + statut `NON_SAISIE` ≠ `0.00` + statut `NUMERIQUE`). |
| **Dispenses (`DISPENSE`)** | Autorisées matière par matière (`allow_exemption`). La matière et son coefficient sont neutralisés pour l'élève concerné. | Configurable par matière ; mention `Disp.` sur relevés et bulletins. |
| **Arrondis et décimales** | Arithmétique décimale exacte (`decimal.Decimal`), **2 décimales** par défaut, arrondi commercial **`ROUND_HALF_UP`** (`0,005 → 0,01`). | Nombre de décimales (`0`, `1`, `2`, `3`) et mode d'arrondi paramétrables. |
| **Classement & ex æquo** | Tri par moyenne générale décroissante. **Rang partagé avec saut** par défaut : `1er, 1er ex, 3e`. Suffixes français genrés (`1er` / `1re`, `1er ex` / `1re ex`, `2e`…). | Option alternative `DENSE` (`1er, 1er ex, 2e`) disponible dans les paramètres. |
| **Moyenne annuelle** | **Moyenne arithmétique simple** des moyennes périodiques par défaut : `(Σ M_p) / N`. | Option **moyenne pondérée** des périodes `(Σ M_p × Poids_p) / Σ Poids_p` configurable. |

---

## 2. Modèle relationnel des données (SQLAlchemy / SQLite)

### 2.1. Vue d'ensemble des entités et relations

```text
establishment_settings (1) ──────► active_school_year_id (nullable au 1er lancement)
                                          │
school_years (1) ─────────────────────────┼──────◄ (N) evaluation_periods
     │                                    ├──────◄ (N) calculation_policies
     │                                    └──────◄ (N) grading_scale_items
     ▼ (1..N)
academic_classes (historise directeur, titre directeur & titulaire par année)
     │
     ├──────◄ (N) subjects (matières, barèmes, coefficients par classe/année)
     ├──────◄ (N) teacher_class_assignments ──────► (1) users
     └──────◄ (N) class_enrollments ──────────────► (1) students (identité pluriannuelle)
                       │
                       ├──────◄ (N) grades ───────► (1) evaluation_periods & (1) subjects
                       │              │
                       │              └──────◄ (N) grade_histories (traçabilité saisies/corrections)
                       │
                       └──────◄ (N) student_period_observations (appréciations / décisions conseil)

Transverse : users (RBAC), audit_logs (journal de sécurité), backup_records (registre des sauvegardes)
```

### 2.2. Détail complet des tables et contraintes d'intégrité

> **Règle d'implémentation financière/scolaire :** aucune note, moyenne ou pondération n'utilise le type binaire `FLOAT`. Toutes les valeurs numériques pédagogiques utilisent `Numeric(precision, scale)` mappé sur `decimal.Decimal` en Python afin d'éliminer toute erreur de représentation en virgule flottante.

#### 1. `users` — Comptes utilisateurs et authentification (§2, §17 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `username` : `VARCHAR(64) UNIQUE NOT NULL` (normalisé en minuscules sans espaces superflus)
- `full_name` : `VARCHAR(150) NOT NULL`
- `password_hash` : `VARCHAR(255) NOT NULL` (hachage `scrypt` / `pbkdf2:sha256` via Werkzeug, jamais en clair)
- `role` : `VARCHAR(20) NOT NULL` — `CHECK (role IN ('ADMIN', 'DIRECTEUR', 'ENSEIGNANT', 'CONSULTATION'))`
- `is_active` : `BOOLEAN NOT NULL DEFAULT 1`
- `must_change_password` : `BOOLEAN NOT NULL DEFAULT 0`
- `failed_login_count` : `INTEGER NOT NULL DEFAULT 0`
- `locked_until` : `DATETIME NULL` (temporisation anti-force brute)
- `last_login_at` : `DATETIME NULL`
- `created_at`, `updated_at` : `DATETIME NOT NULL`

#### 2. `establishment_settings` — Paramètres généraux de l'école (§3.1, §14 CdC)
Table singleton (`id = 1`, contrainte `CHECK (id = 1)`):
- `country` : `VARCHAR(100) NOT NULL DEFAULT 'Burkina Faso'`
- `motto` : `VARCHAR(200) NOT NULL DEFAULT 'La Patrie ou la Mort, nous Vaincrons'`
- `ceb` : `VARCHAR(150) NOT NULL DEFAULT ''` (Circonscription d'Éducation de Base)
- `school_name` : `VARCHAR(200) NOT NULL DEFAULT ''`
- `school_code` : `VARCHAR(50) NULL`
- `logo_filename` : `VARCHAR(255) NULL` (stocké dans `<DATA_DIR>/logos/`, jamais dans le dossier programme)
- `address`, `locality`, `phone`, `email` : `VARCHAR NULL` (facultatifs)
- `active_school_year_id` : `INTEGER NULL FK -> school_years.id ON DELETE SET NULL`
- `session_timeout_minutes` : `INTEGER NOT NULL DEFAULT 30` (`CHECK >= 5`)
- `auto_backup_enabled` : `BOOLEAN NOT NULL DEFAULT 1`
- `auto_backup_interval_hours` : `INTEGER NOT NULL DEFAULT 24`
- `backup_directory` : `VARCHAR(500) NULL` (par défaut `<DATA_DIR>/backups/`, modifiable vers clé USB / disque externe)
- `backup_retention_count` : `INTEGER NOT NULL DEFAULT 15` (`CHECK >= 1`)
- `updated_at` : `DATETIME NOT NULL`

#### 3. `school_years` — Années scolaires (§4.1, §7 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `label` : `VARCHAR(32) UNIQUE NOT NULL` (ex. `2026-2027`)
- `start_date` : `DATE NULL`
- `end_date` : `DATE NULL`
- `age_reference_date` : `DATE NOT NULL` (date officielle de référence, ex. 1er octobre ou date de rentrée, utilisée pour calculer l'âge exact des élèves)
- `status` : `VARCHAR(20) NOT NULL DEFAULT 'OUVERTE'` — `CHECK (status IN ('BROUILLON', 'OUVERTE', 'CLOTUREE'))`
- `default_director_name` : `VARCHAR(150) NULL` (valeur par défaut proposée lors de la création des classes de l'année)
- `default_director_title` : `VARCHAR(100) NULL DEFAULT 'Le Directeur'` (ex. *Le Directeur*, *La Directrice*)
- `closed_at` : `DATETIME NULL`
- `closed_by_id` : `INTEGER NULL FK -> users.id`
- `created_at`, `updated_at` : `DATETIME NOT NULL`
- Contrainte : `CHECK (end_date IS NULL OR start_date IS NULL OR start_date <= end_date)`

#### 4. `academic_classes` — Classes rattachées à une année scolaire (§3.2, §4.2 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `school_year_id` : `INTEGER NOT NULL FK -> school_years.id ON DELETE RESTRICT`
- `name` : `VARCHAR(60) NOT NULL` (ex. `CP1`, `CM2 A`)
- `level_label` : `VARCHAR(30) NULL` (ex. `CP1`, `CP2`, `CE1`, `CE2`, `CM1`, `CM2`)
- `director_name` : `VARCHAR(150) NOT NULL` (**historisé sur la classe/année** pour figer les anciens bulletins)
- `director_title` : `VARCHAR(100) NOT NULL DEFAULT 'Le Directeur'` (**historisé sur la classe/année**)
- `titulaire_name` : `VARCHAR(150) NOT NULL` (**historisé sur la classe/année**)
- `display_order` : `INTEGER NOT NULL DEFAULT 1`
- `is_active` : `BOOLEAN NOT NULL DEFAULT 1`
- `created_at`, `updated_at` : `DATETIME NOT NULL`
- Unicité : `UNIQUE (school_year_id, name)`

#### 5. `teacher_class_assignments` — Affectation des comptes enseignants aux classes (§2 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `user_id` : `INTEGER NOT NULL FK -> users.id ON DELETE CASCADE`
- `class_id` : `INTEGER NOT NULL FK -> academic_classes.id ON DELETE CASCADE`
- `can_edit_grades` : `BOOLEAN NOT NULL DEFAULT 1`
- `created_at` : `DATETIME NOT NULL`
- Unicité : `UNIQUE (user_id, class_id)`

#### 6. `evaluation_periods` — Périodes d'évaluation configurables (§4.3 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `school_year_id` : `INTEGER NOT NULL FK -> school_years.id ON DELETE RESTRICT`
- `name` : `VARCHAR(100) NOT NULL` (libellé libre défini par l'école)
- `short_code` : `VARCHAR(20) NOT NULL` (ex. `T1`, `S1`, `P1` pour colonnes de bulletins annuels)
- `display_order` : `INTEGER NOT NULL`
- `annual_weight` : `NUMERIC(8, 3) NOT NULL DEFAULT 1.000` (`CHECK (annual_weight > 0)`)
- `start_date`, `end_date` : `DATE NULL`
- `status` : `VARCHAR(20) NOT NULL DEFAULT 'BROUILLON'` — `CHECK (status IN ('BROUILLON', 'OUVERTE', 'CLOTUREE'))`
- `closed_at` : `DATETIME NULL`
- `closed_by_id` : `INTEGER NULL FK -> users.id`
- Unicité : `UNIQUE (school_year_id, name)`, `UNIQUE (school_year_id, display_order)`

#### 7. `calculation_policies` — Règles pédagogiques et de calcul (§9.3 CdC)
Rattachée à l'année scolaire (`school_year_id`), avec surcharge facultative par classe (`class_id NULL` = règle par défaut de l'année, `class_id NOT NULL` = règle propre à une classe donnée) :
- `id` : `INTEGER PK AUTOINCREMENT`
- `school_year_id` : `INTEGER NOT NULL FK -> school_years.id ON DELETE CASCADE`
- `class_id` : `INTEGER NULL FK -> academic_classes.id ON DELETE CASCADE`
- `general_average_scale` : `NUMERIC(8, 2) NOT NULL` (`CHECK (general_average_scale > 0)` — ex. `10.00` ou `20.00`, saisi par l'administrateur)
- `decimal_places` : `INTEGER NOT NULL DEFAULT 2` (`CHECK (decimal_places BETWEEN 0 AND 4)`)
- `rounding_mode` : `VARCHAR(25) NOT NULL DEFAULT 'ROUND_HALF_UP'` (`CHECK (rounding_mode IN ('ROUND_HALF_UP', 'ROUND_HALF_EVEN', 'ROUND_DOWN'))`)
- `absence_policy` : `VARCHAR(25) NOT NULL DEFAULT 'ZERO'` (`CHECK (absence_policy IN ('ZERO', 'NEUTRALIZE', 'UNRANKED'))`)
- `tie_breaking_mode` : `VARCHAR(25) NOT NULL DEFAULT 'COMPETITION'` (`CHECK (tie_breaking_mode IN ('COMPETITION', 'DENSE'))` — `COMPETITION` = `1er, 1er ex, 3e` ; `DENSE` = `1er, 1er ex, 2e`)
- `annual_average_mode` : `VARCHAR(25) NOT NULL DEFAULT 'SIMPLE'` (`CHECK (annual_average_mode IN ('SIMPLE', 'WEIGHTED'))`)
- `annual_require_all_periods` : `BOOLEAN NOT NULL DEFAULT 1` (exiger que toutes les périodes actives soient évaluées pour classer annuellement l'élève)
- Unicité : index unique sur `(school_year_id, COALESCE(class_id, 0))`

#### 8. `grading_scale_items` — Grille des appréciations (§6 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `policy_id` : `INTEGER NOT NULL FK -> calculation_policies.id ON DELETE CASCADE`
- `min_score` : `NUMERIC(8, 3) NOT NULL` (`CHECK (min_score >= 0)`)
- `max_score` : `NUMERIC(8, 3) NOT NULL` (`CHECK (max_score > min_score)`)
- `appreciation` : `VARCHAR(100) NOT NULL`
- `is_last_interval` : `BOOLEAN NOT NULL DEFAULT 0` (indique si la borne haute `max_score` est incluse)
- `display_order` : `INTEGER NOT NULL`

#### 9. `subjects` — Matières et éléments d'évaluation par classe (§5 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `class_id` : `INTEGER NOT NULL FK -> academic_classes.id ON DELETE RESTRICT`
- `name` : `VARCHAR(120) NOT NULL`
- `short_label` : `VARCHAR(30) NULL`
- `coefficient` : `NUMERIC(8, 3) NOT NULL` — `CHECK (coefficient > 0)`
- `min_score` : `NUMERIC(8, 3) NOT NULL DEFAULT 0.000` — `CHECK (min_score >= 0)`
- `max_score` : `NUMERIC(8, 3) NOT NULL` — `CHECK (max_score > min_score)`
- `reference_scale` : `NUMERIC(8, 3) NOT NULL` (« Moyenne sur ») — `CHECK (reference_scale > 0)`
- `include_in_average` : `BOOLEAN NOT NULL DEFAULT 1` (prise en compte dans la moyenne générale)
- `allow_exemption` : `BOOLEAN NOT NULL DEFAULT 1` (autorise l'état `DISPENSE`)
- `display_order` : `INTEGER NOT NULL DEFAULT 1`
- `is_active` : `BOOLEAN NOT NULL DEFAULT 1`
- Unicité : `UNIQUE (class_id, name)`

#### 10. `students` — Identité permanente de l'élève (§7 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `unique_code` : `VARCHAR(40) UNIQUE NOT NULL` (identifiant interne généré ex. `ELV-2026-0001` ou matricule saisi, facilitant le suivi pluriannuel et la détection de doublons Excel)
- `last_name` : `VARCHAR(100) NOT NULL` (Nom de famille, stocké normalisé en majuscules pour le tri alphabétique)
- `first_names` : `VARCHAR(150) NOT NULL` (Prénoms)
- `sex` : `CHAR(1) NOT NULL` — `CHECK (sex IN ('M', 'F'))`
- `birth_date` : `DATE NOT NULL`
- `created_at`, `updated_at` : `DATETIME NOT NULL`
- Index de recherche : `INDEX idx_students_identity (last_name, first_names, birth_date)`

#### 11. `class_enrollments` — Inscription annuelle dans une classe et historique scolaire (§7, §7.1 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `student_id` : `INTEGER NOT NULL FK -> students.id ON DELETE RESTRICT`
- `class_id` : `INTEGER NOT NULL FK -> academic_classes.id ON DELETE RESTRICT`
- `roll_number` : `INTEGER NOT NULL` (`CHECK (roll_number > 0)` — numéro d'ordre dans la classe, renumérotable par fonction dédiée)
- `schooling_status` : `VARCHAR(120) NULL` (champ *Scolarité* défini par l'établissement)
- `repeated_class_detail` : `VARCHAR(150) NULL` (champ *Classe redoublée* : classe et/ou année concernée)
- `observations` : `TEXT NULL` (observations générales sur la fiche élève)
- `is_active` : `BOOLEAN NOT NULL DEFAULT 1` (désactivation sans perte de notes ni d'historique)
- `deactivated_at` : `DATETIME NULL`
- `deactivation_reason` : `VARCHAR(200) NULL`
- `annual_decision` : `VARCHAR(40) NULL` (`ADMIS`, `REDOUBLE`, `TRANSFERE`, `SORTI`, etc., utilisé pour l'assistant de passage d'année)
- `created_at`, `updated_at` : `DATETIME NOT NULL`
- Unicité : `UNIQUE (class_id, student_id)` (et contrôle applicatif/transactionnel de `roll_number` au sein de la classe)

#### 12. `grades` — Notes d'évaluation (§8, §8.3 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `enrollment_id` : `INTEGER NOT NULL FK -> class_enrollments.id ON DELETE RESTRICT`
- `period_id` : `INTEGER NOT NULL FK -> evaluation_periods.id ON DELETE RESTRICT`
- `subject_id` : `INTEGER NOT NULL FK -> subjects.id ON DELETE RESTRICT`
- `status` : `VARCHAR(15) NOT NULL DEFAULT 'NON_SAISIE'` — `CHECK (status IN ('NON_SAISIE', 'NUMERIQUE', 'ABSENT', 'DISPENSE'))`
- `numeric_value` : `NUMERIC(8, 3) NULL`
- `comment` : `VARCHAR(255) NULL`
- `version` : `INTEGER NOT NULL DEFAULT 1` (verrouillage optimiste pour détecter les conflits d'écriture multiposte simultanés)
- `entered_by_id` : `INTEGER NULL FK -> users.id`
- `updated_by_id` : `INTEGER NULL FK -> users.id`
- `created_at`, `updated_at` : `DATETIME NOT NULL`
- Contrainte stricte d'intégrité des états (§8.3 CdC) :
  `CHECK ((status = 'NUMERIQUE' AND numeric_value IS NOT NULL) OR (status IN ('NON_SAISIE', 'ABSENT', 'DISPENSE') AND numeric_value IS NULL))`
- Unicité : `UNIQUE (enrollment_id, period_id, subject_id)`

#### 13. `grade_histories` — Historique des saisies et corrections de notes (§8.2 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `grade_id` : `INTEGER NOT NULL FK -> grades.id ON DELETE CASCADE`
- `enrollment_id`, `period_id`, `subject_id` : `INTEGER NOT NULL`
- `old_status` : `VARCHAR(15) NULL`
- `old_value` : `NUMERIC(8, 3) NULL`
- `new_status` : `VARCHAR(15) NOT NULL`
- `new_value` : `NUMERIC(8, 3) NULL`
- `changed_by_id` : `INTEGER NULL FK -> users.id`
- `changed_by_username` : `VARCHAR(64) NOT NULL`
- `changed_at` : `DATETIME NOT NULL`

#### 14. `student_period_observations` — Observations de bulletins périodiques et annuels (§12.1, §12.3 CdC)
- `id` : `INTEGER PK AUTOINCREMENT`
- `enrollment_id` : `INTEGER NOT NULL FK -> class_enrollments.id ON DELETE CASCADE`
- `period_id` : `INTEGER NULL FK -> evaluation_periods.id ON DELETE CASCADE` (`NULL` lorsque `scope = 'ANNUEL'`)
- `scope` : `VARCHAR(15) NOT NULL` — `CHECK (scope IN ('PERIODE', 'ANNUEL'))`
- `teacher_observation` : `TEXT NULL`
- `director_observation` : `TEXT NULL`
- `honors_mention` : `VARCHAR(100) NULL` (ex. Tableau d'honneur, Encouragements, Félicitations — facultatif)
- `updated_by_id` : `INTEGER NULL FK -> users.id`
- `updated_at` : `DATETIME NOT NULL`

#### 15. `audit_logs` & `backup_records` — Traçabilité sécurité et sauvegardes (§14, §17 CdC)
- `audit_logs` : `id`, `created_at`, `user_id`, `username_snapshot`, `role_snapshot`, `ip_address`, `action` (ex. `AUTH_LOGIN`, `AUTH_FAIL`, `PERIOD_CLOSE`, `YEAR_CLOSE`, `BACKUP_RESTORE`, `USER_ROLE_UPDATE`, `EXCEL_IMPORT`), `target_type`, `target_id`, `summary`, `details_json`.
- `backup_records` : `id`, `created_at`, `filename`, `file_path`, `size_bytes`, `sha256_hash`, `trigger_type` (`MANUAL`, `AUTO`, `PRE_RESTORE`, `PRE_MIGRATION`), `integrity_verified` (`BOOLEAN`), `app_version`, `alembic_revision`, `created_by_username`.

---

## 3. Règles mathématiques de calcul et cas particuliers (§9, §10, §11, §12 CdC)

Tous les calculs intermédiaires utilisent `decimal.Decimal` avec une précision interne élevée (28 chiffres significatifs). L'arrondi (`ROUND_HALF_UP`, à `d = 2` décimales par défaut) est appliqué de manière uniforme et déterministe.

### 3.1. Calcul de l'âge exact de l'élève (§7.1 CdC)
Soient `D_naiss` la date de naissance de l'élève et `D_ref` la date de référence de l'année scolaire (`school_years.age_reference_date`) :
$$\text{Âge} = D_{\text{ref}}.\text{year} - D_{\text{naiss}}.\text{year} - \begin{cases} 1 & \text{si } (D_{\text{ref}}.\text{month}, D_{\text{ref}}.\text{day}) < (D_{\text{naiss}}.\text{month}, D_{\text{naiss}}.\text{day}) \\ 0 & \text{sinon} \end{cases}$$
Contrôle de cohérence : refus des dates futures ou produisant un âge négatif, alerte visuelle douce (confirmable) si l'âge est hors plage habituelle du primaire (ex. `< 4` ans ou `> 18` ans).

### 3.2. Validation et conversion de la note par matière (§5, §9.1 CdC)
Pour une matière active $m$ ayant $(N_{\min,m}, N_{\max,m}, B_m = \text{moyenne\_sur}, C_m = \text{coefficient})$ et une classe dont le barème général est $B_{\text{gén}}$ :
1. **Contrôle de saisie :** toute note numérique $x$ doit vérifier $N_{\min,m} \le x \le N_{\max,m}$. Sinon la saisie est rejetée côté client **et** côté serveur.
2. **Note ramenée au barème de matière $B_m$ :**
   - Si $N_{\min,m} = 0$ (cas standard) :
     $$M_m = x \times \frac{B_m}{N_{\max,m}}$$
   - Si $N_{\min,m} > 0$ (cas particulier éventuel configuré par l'école) :
     $$M_m = \frac{x - N_{\min,m}}{N_{\max,m} - N_{\min,m}} \times B_m$$
3. **Cohérence avec le barème général $B_{\text{gén}}$ :**
   - Lors de la création d'une matière, `reference_scale` ($B_m$) est pré-rempli avec `general_average_scale` ($B_{\text{gén}}$).
   - Si l'école configure une matière avec $B_m \neq B_{\text{gén}}$, la contribution à la moyenne générale est normalisée explicitement sur $B_{\text{gén}}$ : $M_{m,\text{gén}} = M_m \times \frac{B_{\text{gén}}}{B_m}$, et l'interface affiche un encadré explicatif de la conversion.
4. **Points pondérés de la matière :**
   $$P_m = M_{m,\text{gén}} \times C_m$$

### 3.3. Traitement des 4 états d'une note dans le calcul périodique (§8.3, §9.2 CdC)

Pour un élève actif et l'ensemble des matières actives où `include_in_average = True` :

| État de la note | Valeur retenue pour $M_{m,\text{gén}}$ | Coefficient $C_m$ au dénominateur | Impact sur le statut de classement de l'élève |
|---|---|---|---|
| `NUMERIQUE` (y compris `0`) | Valeur convertie exacte (`0` si note = `0`) | Inclus ($+C_m$) | Classable |
| `ABSENT` — mode par défaut `ZERO` | `0` | Inclus ($+C_m$) | Classable (affiché `Abs.` sur bulletin/relevé, compté `0`) |
| `ABSENT` — option `NEUTRALIZE` | Ignoré | Exclu (`0`) | Classable si au moins 1 matière évaluée |
| `ABSENT` — option `UNRANKED` | — | — | Élève marqué **Non classé (Absent)** |
| `DISPENSE` | Ignoré | Exclu (`0`) | Classable si au moins 1 autre matière évaluée (affiché `Disp.`) |
| `NON_SAISIE` (ou aucune ligne) | **Aucune (jamais `0`)** | — | Dossier **Incomplet** → **Non classé** tant que non complété |

Lorsque le dossier est complet et $\sum_{m \in E} C_m > 0$ (où $E$ est l'ensemble des matières évaluées ou comptées de l'élève) :
$$\text{Moyenne générale brute} = \frac{\sum_{m \in E} \left(M_{m,\text{gén}} \times C_m\right)}{\sum_{m \in E} C_m}$$
$$\text{Moyenne générale affichée} = \text{Arrondi}\left(\text{Moyenne générale brute}, d, \text{ROUND\_HALF\_UP}\right)$$

> **Règle de cohérence classement / affichage :** le classement et l'appréciation s'appliquent sur la **moyenne générale arrondie à $d$ décimales** afin que deux élèves affichant exactement la même moyenne sur leurs bulletins (ex. `7,45 / 10`) soient toujours déclarés **ex æquo** (`1er`, `1er ex`), évitant toute contradiction visuelle sur les documents officiels remis aux parents.

### 3.4. Algorithme de classement et gestion des ex æquo (§9.4, §11 CdC)
- Seuls les élèves **actifs** (`is_active = True`) et **classables** (aucune matière obligatoire `NON_SAISIE`, au moins une matière comptabilisée) entrent dans le classement.
- Les élèves inactifs ou incomplets figurent dans une section distincte ou avec la mention explicite `Non classé (notes incomplètes)` / `Inactif`.
- **Mode retenu par défaut (`COMPETITION` — rang partagé avec saut) :**
  - Exemple avec moyennes `[8,50 ; 8,25 ; 8,25 ; 7,90]` :
    1. `8,50` → **1er** (ou **1re** si `sexe = 'F'`)
    2. `8,25` → **2e ex** (ex æquo avec un autre élève au rang 2 : les deux portent la mention `2e ex`, ou `1er ex` / `1re ex` s'ils sont au rang 1)
    3. `8,25` → **2e ex**
    4. `7,90` → **4e** (le rang 3 est sauté car 2 élèves occupent la 2e place).
- **Mode alternatif paramétrable (`DENSE` — sans saut) :** donnerait `1er, 2e ex, 2e ex, 3e`.

### 3.5. Calcul de la moyenne annuelle (§12.3 CdC)
Pour une classe ayant les périodes actives $p \in \{1, \dots, K\}$ de l'année scolaire :
- **Mode par défaut (`SIMPLE`) :**
  $$\text{Moyenne annuelle} = \text{Arrondi}\left(\frac{1}{|K'|} \sum_{p \in K'} M_{\text{période}, p},\; d,\; \text{ROUND\_HALF\_UP}\right)$$
- **Mode alternatif (`WEIGHTED`) :**
  $$\text{Moyenne annuelle} = \text{Arrondi}\left(\frac{\sum_{p \in K'} M_{\text{période}, p} \times W_p}{\sum_{p \in K'} W_p},\; d,\; \text{ROUND\_HALF\_UP}\right)$$
- Par défaut (`annual_require_all_periods = True`), l'élève doit disposer d'une moyenne valide à chacune des périodes actives de l'année ($K' = K$) pour obtenir un rang annuel ; sinon ses moyennes périodiques disponibles sont affichées sur le bulletin annuel avec la mention `Année incomplète — Non classé`. (Paramétrable si un élève arrivé en cours d'année est autorisé par la direction à être calculé sur les périodes fréquentées.)

### 3.6. Grille des appréciations (§6 CdC)
- Intervalles ordonnés croissants $[a_i, b_i[$ pour $i < n$, et $[a_n, b_n]$ pour le dernier intervalle où $b_n = B_{\text{gén}}$.
- **Contrôles automatiques à l'enregistrement :**
  1. $0 \le a_i < b_i \le B_{\text{gén}}$ pour tout $i$.
  2. Aucun chevauchement ($b_i \le a_{i+1}$). Si chevauchement → blocage avec message explicite en français.
  3. Détection de couverture incomplète : si $a_1 > 0$, ou $b_i < a_{i+1}$, ou $b_n < B_{\text{gén}}$ → avertissement explicite indiquant exactement quelle plage de moyennes n'a pas d'appréciation.
- La même fonction d'appréciation permet d'attribuer l'appréciation générale (sur $B_{\text{gén}}$) ainsi que l'appréciation par matière (après projection proportionnelle sur $B_{\text{gén}}$).

### 3.7. Exemple chiffré de vérification (jeu de recette)
Barème général configuré par l'école : `/10`, `2` décimales, `ROUND_HALF_UP`, `Absence = 0`.
- Matières : Lecture (`/10`, coef `2`), Calcul (`/20` ramené sur `/10`, coef `2`), Sport (`/10`, coef `1`, dispense autorisée).
- **Élève A (Aminata, F)** : Lecture `8/10` (`8,00`), Calcul `15/20` → ramené `7,50/10`, Sport `Disp.`
  - Points = $(8,00 \times 2) + (7,50 \times 2) = 16,00 + 15,00 = 31,00$ ; Somme coefs = $2 + 2 = 4$.
  - Moyenne = $31,00 / 4 = 7,75 / 10$.
- **Élève B (Moussa, M)** : Lecture `9/10` (`9,00`), Calcul `Abs.` → compté `0,00/10`, Sport `8/10` (`8,00`).
  - Points = $(9,00 \times 2) + (0,00 \times 2) + (8,00 \times 1) = 26,00$ ; Somme coefs = $2 + 2 + 1 = 5$.
  - Moyenne = $26,00 / 5 = 5,20 / 10$.
- **Élève C (Issouf, M)** : Lecture `7/10`, Calcul `NON_SAISIE`, Sport `8/10`.
  - Dossier **Incomplet** → Moyenne non arrêtée, **Non classé** (jamais transformé en `0`).

---

## 4. Architecture de sécurité, concurrence SQLite et sauvegarde (§14, §16, §17 CdC & §4–8 Arch)

### 4.1. Authentification, sessions et contrôle d'accès serveur (RBAC)
1. **Connexion obligatoire :** toute route applicative (hors `/login` et ressources statiques locales) exige une session authentifiée. Au tout premier démarrage sur une base vierge sans aucun utilisateur, un assistant d'amorçage local crée le premier compte `ADMIN` avec mot de passe fort choisi par l'administrateur.
2. **Mots de passe & anti-bruteforce :** hachage salé `scrypt` (Werkzeug), jamais journalisé ni affiché ; verrouillage temporaire de 5 minutes après 5 échecs consécutifs, journalisé dans `audit_logs`.
3. **Inactivité :** expiration automatique de la session après `session_timeout_minutes` (30 min par défaut, configurable).
4. **Matrice des permissions contrôlées côté serveur (décorateurs `@role_required(...)` et `@class_access_required`) :**

| Module / Action | `ADMIN` | `DIRECTEUR` | `ENSEIGNANT` | `CONSULTATION` |
|---|:---:|:---:|:---:|:---:|
| Comptes utilisateurs, rôles, journaux d'audit | Oui | Non | Non | Non |
| Sauvegarde / Restauration / Paramètres techniques | Oui | Lecture (sauvegarde manuelle possible si autorisé) | Non | Non |
| Paramètres établissement, années, classes, périodes | Oui | Oui | Lecture (ses classes) | Lecture |
| Clôture d'une période / réouverture motivée | Oui | Oui (clôture) / Réouverture Admin ou Directeur journalisée | Non | Non |
| Matières, coefficients, grille d'appréciations | Oui | Oui (tant qu'année non clôturée) | Lecture | Lecture |
| Gestion élèves, import/export Excel, passage d'année | Oui | Oui | Lecture (ses classes) | Lecture |
| Saisie / modification des notes | Oui (période `OUVERTE`) | Oui (période `OUVERTE`) | **Uniquement ses classes affectées** et **période `OUVERTE`** | Non |
| Consultation relevés, classements, export PDF bulletins | Oui | Oui | Oui (ses classes) | Oui |

5. **Protection CSRF et validation :** `CSRFProtect` (`Flask-WTF`) activé globalement sur tous les formulaires POST/PUT/DELETE ainsi que sur les appels AJAX JSON de la grille de notes (en-tête `X-CSRFToken`). Échappement automatique Jinja2 contre les injections XSS. Pages d'erreur personnalisées en français (`403`, `404`, `409`, `500`) sans pile d'exécution technique exposée à l'écran (détails envoyés uniquement dans `logs/application.log`).
6. **Procédure de secours administrateur :** commande locale exécutable sur le poste serveur (`YelenNotes.exe --reset-admin` ou `python run.py --reset-admin`) exigeant un accès physique à la session Windows du serveur, journalisée dans `audit_logs`.

### 4.2. SQLite en monoposte et multiposte réseau local (§4 Arch)
À chaque ouverture de connexion SQLAlchemy sur `ecole.db` (événement `Engine.connect`) :
```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 10000;
PRAGMA temp_store = MEMORY;
```
- **Contrôle anti-partage réseau :** au démarrage, l'application vérifie le chemin de `ecole.db` et affiche une alerte critique si un chemin UNC (`\\serveur\...`) est détecté (§4 Arch : ne jamais ouvrir SQLite via partage SMB).
- **Gestion de la saisie simultanée multiposte :**
  - Les notes sont enregistrées par cellule ou par lot via des transactions très courtes (< 20 ms).
  - Chaque ligne `grades` porte un numéro `version`. Si deux utilisateurs modifient exactement la même note du même élève au même instant, le second reçoit un code `409 Conflit` avec l'indication en français (*« Cette note vient d'être modifiée par [Utilisateur] (nouvelle valeur : …). Voulez-vous recharger ou remplacer ? »*) — aucune perte silencieuse (§19 test n°5 CdC).

### 4.3. Sauvegarde, restauration et séparation Programme / Données (§5, §8 Arch & §14 CdC)
- **Séparation stricte des répertoires :**
  - Programme portable : `YELEN-NOTES/` (remplaçable lors d'une mise à jour sans toucher aux données).
  - Données indépendantes : sous Windows `C:\ProgramData\YelenNotes\` (avec repli automatique vers `%LOCALAPPDATA%\YelenNotes\` si le compte Windows n'a pas les droits d'écriture sur `ProgramData`, ou chemin personnalisé via `config/yelen_notes.ini` ou `YELEN_DATA_DIR`).
- **Méthode de sauvegarde à chaud sûre :** utilisation exclusive de l'API native `sqlite3.Connection.backup()` vers une archive horodatée `.zip` contenant `ecole.db` cohérente, le logo éventuel et un manifeste JSON (`app_version`, `alembic_revision`, `sha256`, date), suivie d'un `PRAGMA integrity_check` sur la copie créée.
- **Restauration sécurisée :**
  1. Vérification du manifeste, du `SHA-256` et d'un `PRAGMA integrity_check` sur la sauvegarde candidate dans un dossier temporaire.
  2. Création automatique d'une sauvegarde de sécurité `PRE_RESTORE` de la base courante.
  3. Remplacement atomique et journalisation dans `audit_logs`.

---

## 5. Maquettes fonctionnelles des écrans et documents PDF (§15, §24 CdC)

Toutes les ressources (`Bootstrap 5`, icônes SVG inline, feuilles CSS d'écran et d'impression, polices TrueType pour ReportLab, JavaScript natif) sont **100 % locales**, sans aucun appel CDN ni police distante.

### 5.1. Disposition générale de l'interface (Français intégral)
```text
+--------------------------------------------------------------------------------------------------+
| YELEN NOTES   | École Primaire Publique de ...          | Année active : [2026-2027 v]           |
|               | Mode : Monoposte (v1.0.0)               | 👤 M. OUÉDRAOGO (Directeur) [Quitter]  |
+------------------------+-------------------------------------------------------------------------+
| MENU PRINCIPAL         | FIL D'ARIANE : Accueil > Saisie des notes > CM2 A — 1er Trimestre       |
|                        |-------------------------------------------------------------------------|
| 🏠 Tableau de bord     | [Bannière d'état : Période OUVERTE | Sauvegarde auto : OK il y a 2h]    |
| 🏫 Établissement       |                                                                         |
| 📅 Années & Périodes   |                         CONTENU DU MODULE ACTIF                         |
| 🏛️ Classes & Matières  |                                                                         |
| 📐 Appréciations/Règles|                                                                         |
| 👧 Élèves & Passage    |                                                                         |
| ✏️ Saisie des notes    |                                                                         |
| 📊 Relevés & Mérite    |                                                                         |
| 📄 Bulletins PDF       |                                                                         |
| 💾 Sauvegardes         |                                                                         |
| 🔐 Utilisateurs/Journaux                                                                         |
+------------------------+-------------------------------------------------------------------------+
```

### 5.2. Tableau de bord (§13 CdC) — État « Base vierge » vs « En cours d'année »
- **Sur base vierge :** affiche un **Guide de démarrage en 5 étapes cochables** :
  1. Renseigner l'identité de l'établissement (Nom, CEB, Logo facultatif) ;
  2. Créer l'année scolaire et ses périodes d'évaluation ;
  3. Définir le barème de référence, les règles de calcul et la grille d'appréciations ;
  4. Créer les classes et configurer leurs matières & coefficients ;
  5. Inscrire ou importer les élèves (Excel) puis ouvrir la saisie des notes.
- **En cours d'exploitation :** cartes synthétiques calculées en temps réel : effectif total actifs (Filles / Garçons + ratios), statut des périodes, barres de progression de saisie par classe et par matière (`% de notes saisies`, nombre d'élèves incomplets, alertes éventuelles).

### 5.3. Écran de saisie des notes au clavier (§8 CdC)
```text
+--------------------------------------------------------------------------------------------------+
| Année: [2026-2027]  Classe: [CM2 A v]  Période: [1er Trimestre (OUVERTE) v]                      |
| Mode d'affichage : (•) Matrice Classe entière   ( ) Par matière : [Toutes v]                     |
| Filtre élève : [Rechercher nom/n°...]   [ ] Afficher uniquement notes manquantes                 |
| État : ● Toutes les modifications sont enregistrées  |  [Enregistrer tout]  [Historique saisies] |
+----+----------------------+---+---------------+----------------+---------------+-----------------+
| N° | Nom et Prénoms       | S | Lecture (/10) | Calcul (/20)   | Sport (/10)   | État / Moyenne  |
|    |                      |   | Coef 2        | Coef 2 -> /10  | Coef 1        | provisoire      |
+----+----------------------+---+---------------+----------------+---------------+-----------------+
| 01 | KABORÉ Aminata       | F | [  8,00     ] | [ 15,00      ] | [ DISP      ] | Complet | 7,75  |
| 02 | OUÉDRAOGO Moussa     | M | [  9,00     ] | [ ABS        ] | [  8,00     ] | Complet | 5,20  |
| 03 | SAWADOGO Issouf      | M | [  7,00     ] | [            ] | [  8,00     ] | ⚠ 1 manquante   |
+----+----------------------+---+---------------+----------------+---------------+-----------------+
Raccourcis clavier : Entrée / ↓ = élève suivant | Tab / → = matière suivante | A = Absent | D = Dispensé | Suppr = Vider
```
- Contrôle instantané des bornes `[min, max]` : bordure rouge + infobulle explicite si une saisie dépasse le barème (ex. `22` sur `/20`), refus d'enregistrement invalide.

### 5.4. Maquette des bulletins scolaires PDF A4 (§12 CdC — ReportLab)
Conçus pour être parfaitement lisibles en **noir et blanc** (photocopieuse/imprimante laser d'école) comme en couleur :
```text
================================================================================
BURKINA FASO                                     [LOGO]  ÉCOLE PRIMAIRE ...
La Patrie ou la Mort, nous Vaincrons                     CEB de : ...............
                                                         Année scolaire : .......
                  BULLETIN SCOLAIRE — [INTITULÉ DE LA PÉRIODE]
--------------------------------------------------------------------------------
Élève : NOM Prénoms                  N° : 01     Sexe : F    Né(e) le : ../../....
Classe : CM2 A   Effectif : 48       Âge : 11 ans   Scolarité : ...  Redoubl. : ..
--------------------------------------------------------------------------------
Matières         | Note obtenue | Barème | Moy./Réf | Coef | Points | Appréciation
-----------------+--------------+--------+----------+------+--------+-------------
Lecture          |     8,00     |  /10   |   8,00   |  2   | 16,00  | Bien
Calcul           |    15,00     |  /20   |   7,50   |  2   | 15,00  | Assez bien
Éd. Physique     |    Disp.     |  /10   |   Disp.  |  -   |   -    | Dispensée
-----------------+--------------+--------+----------+------+--------+-------------
TOTAUX           |              |        |          |  4   | 31,00  |
--------------------------------------------------------------------------------
MOYENNE GÉNÉRALE : 7,75 / 10         RANG : 3e / 48 (ou 3e ex)
APPRÉCIATION GÉNÉRALE : Assez bien   Moy. de la classe : ...  (Min: ... | Max: ...)
--------------------------------------------------------------------------------
Observations du Titulaire :                  | Observations et visa Direction :
...........................................  | [Titre historisé : Le Directeur / La Directrice]
Titulaire : M./Mme [Nom historisé]           | [Nom historisé]
Signature :                                  | Signature et cachet :
================================================================================
```
- **Bulletin annuel A4 :** reprend le même en-tête officiel et les mêmes signatures historisées, présente les moyennes par matière et/ou le récapitulatif de toutes les périodes de l'année (`P1`, `P2`, `P3`…), la **Moyenne annuelle**, le **Rang annuel**, l'**Appréciation annuelle** et la **Décision de fin d'année**.
- **Production collective :** un seul PDF multi-pages avec saut de page net entre chaque élève (classe entière ou sélection d'élèves cochés) + aperçu HTML/PDF intégré dans le navigateur avant impression.

---

## 6. Phasage d'exécution proposé dès votre validation (§21 CdC & §11 Arch)

Dès validation de ce dossier de conception, nous exécuterons les phases suivantes dans le dépôt `YelenNotes`, avec tests automatisés à chaque jalon :

1. **Phase A — Socle technique, configuration, SQLite WAL, migrations Alembic, sécurité RBAC et lancement (`Waitress`)** : structure modulaire Flask, séparation `Program` / `Data`, amorçage du 1er administrateur sur base vierge, connexion/déconnexion, protection CSRF, journaux d'audit.
2. **Phase B — Paramétrage scolaire et élèves** : établissement (avec logo local), années scolaires, classes (historisation direction/titulariat), périodes, règles de calcul, grille d'appréciations avec détection des chevauchements/trous, matières, fiches élèves, import/export Excel (`OpenPyXL`) avec contrôle de doublons, désactivation et assistant de passage de classe / redoublement.
3. **Phase C — Saisie des notes, moteur de calcul, relevés, classements et bulletins PDF (`ReportLab`)** : grille de saisie clavier avec verrouillage optimiste et historique des modifications, moteur décimal exact, relevés de notes écran/PDF, classements par ordre de mérite (PDF + Excel), bulletins périodiques et annuels (individuels et collectifs), tableau de bord réel.
4. **Phase D — Sauvegarde/restauration, base de démonstration séparée, packaging PyInstaller `ONEDIR`, suite de tests complète et documentation française** : sauvegardes manuelles/automatiques vérifiées (`sqlite3.backup` + `integrity_check`), générateur de base de démo fictive clairement identifiée, scripts `YelenNotes.spec` et lanceur Windows monoposte/multiposte, tests unitaires et fonctionnels couvrant les 12 critères de recette (§19 CdC), guides utilisateur, installation et maintenance en français, et aperçu live pour vérification immédiate.
