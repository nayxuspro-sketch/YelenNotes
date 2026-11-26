# Guide Utilisateur — Yelen Notes (Version 1.0)

**Public visé :** Directeurs et directrices d'école, enseignants titulaires et agents autorisés des écoles primaires du Burkina Faso.  
**Fonctionnement :** 100 % hors ligne (aucune connexion Internet requise).

---

## 1. Première mise en service (Base vierge)

Lors du tout premier lancement de Yelen Notes sur l'ordinateur de l'école :
1. Une page **« Première mise en service »** s'affiche automatiquement.
2. Créez le premier compte **Administrateur** (identifiant, nom complet et mot de passe d'au moins 8 caractères).
3. Vous arrivez sur le **Tableau de bord**, qui présente un **Guide de mise en route en 5 étapes** :
   - **Étape 1 — Établissement & Logo :** renseignez le nom complet de l'école, la CEB, la localité, le téléphone et téléversez éventuellement le logo officiel (PNG ou JPG). Le pays (*Burkina Faso*) et la devise (*La Patrie ou la Mort, nous Vaincrons*) sont pré-remplis et modifiables.
   - **Étape 2 — Années & Périodes :** créez l'année scolaire (ex. `2026-2027`) en indiquant la **date de référence pour le calcul de l'âge** (généralement le `01/10/AAAA`), puis ajoutez vos périodes d'évaluation (ex. *Premier Trimestre*, *Deuxième Trimestre*, *Troisième Trimestre*).
   - **Étape 3 — Barèmes & Appréciations :** définissez le barème de la moyenne générale (ex. `/10` ou `/20`), le nombre de décimales, la règle d'arrondi, le traitement des absences, la gestion des ex æquo et les intervalles d'appréciation (*Insuffisant*, *Passable*, *Assez bien*, *Bien*, *Très bien*…).
   - **Étape 4 — Classes & Matières :** créez chaque classe (`CP1`, `CP2`, `CE1`, `CE2`, `CM1`, `CM2`…) avec les noms du **titulaire** et du **directeur/directrice** (ces noms sont historisés pour l'année afin que les anciens bulletins ne changent jamais), puis configurez les matières, leurs barèmes et leurs coefficients. Astuce : vous pouvez copier la liste des matières d'une classe existante lors de la création d'une nouvelle classe.
   - **Étape 5 — Élèves & Inscriptions :** ajoutez les élèves un par un ou importez-les depuis un fichier Excel `.xlsx`.

---

## 2. Gestion centralisée des rôles et permissions (RBAC)

Yelen Notes intègre un moteur d'autorisation centralisé fondé sur le **principe du moindre privilège** et le **refus par défaut (*Default Deny*)** : aucune fonctionnalité ni donnée n'est accessible sans autorisation explicite.

### 2.1. Rôles prédéfinis et personnalisables
Dans **Administration → Gestion des rôles & permissions**, 8 rôles métier prédéfinis sont fournis et entièrement personnalisables (en plus de la création libre de nouveaux rôles) :

| Rôle | Périmètre par défaut |
|---|---|
| **Super administrateur (`SUPER_ADMIN`)** | Accès complet global à tous les modules, rôles, matrices de permissions, établissements, sauvegardes/restaurations et journaux d'audit. |
| **Administrateur d'établissement (`ADMIN_ETABLISSEMENT`)** | Administration complète au sein de son établissement autorisé (strictement isolé des autres établissements). |
| **Directeur / Directrice (`DIRECTEUR`)** | Pilotage pédagogique et administratif de l'établissement : années, périodes (ouverture/clôture), classes, barèmes, élèves, notes, bulletins PDF, proposition de fin d'année, suivi des absences et lecture comptable/audit. |
| **Secrétaire (`SECRETAIRE`)** | Gestion administrative des élèves, inscriptions, classes, absences et saisie de pièces comptables courantes, sans validation pédagogique finale. |
| **Enseignant (`ENSEIGNANT`)** | Périmètre strictement limité à ses classes affectées : identités/infos des élèves, ajout et paramétrage des matières et barèmes de sa classe, saisie des notes en période **OUVERTE**, observations, absences et édition des relevés/bulletins de sa classe. |
| **Comptable (`COMPTABLE`)** | Gestion et export des opérations financières dans **Comptabilité scolaire**, sans aucun accès aux données pédagogiques confidentielles (notes, relevés, bulletins, propositions). |
| **Surveillant (`SURVEILLANT`)** | Enregistrement et justification des absences, retards et observations de discipline dans **Absences & Discipline**. |
| **Parent / Tuteur (`PARENT`)** | Accès exclusif à l'**Espace Parent / Tuteur** pour consulter les moyennes, observations, absences et télécharger les bulletins PDF de ses seuls enfants rattachés. |

### 2.2. Matrice des permissions, cumul de rôles et surcharges auditées
- **Matrice interactive :** chaque module fonctionnel est découpé en permissions granulaires (`Consulter`, `Créer`, `Modifier`, `Supprimer`, `Valider`, `Exporter`, `Imprimer`, `Administrer`), modifiables par cases à cocher avec **effet immédiat** sur les sessions actives.
- **Cumul de rôles et exceptions individuelles :** un compte utilisateur peut cumuler plusieurs rôles et recevoir exceptionnellement une surcharge individuelle motivée (`ALLOW` ou `DENY`, où `DENY` prime toujours).
- **Sécurité et traçabilité :** interdiction d'auto-élévation de privilèges, blocage immédiat des comptes désactivés ou suspendus temporairement, et journalisation en lecture seule de toutes les connexions, modifications RBAC et tentatives refusées.

---

## 3. Gestion des élèves, import Excel et passage d'année

### 3.1. Importation Excel sans doublons
1. Dans **Élèves & Inscriptions**, cliquez sur **« Télécharger le modèle Excel »**.
2. Remplissez les colonnes : `Nom`, `Prénoms`, `Sexe` (`M` ou `F`), `Date de naissance` (`JJ/MM/AAAA`), ainsi que les champs facultatifs (`Numéro`, `Scolarité`, `Classe redoublée`, `Observations`).
3. Cliquez sur **« Importer depuis Excel (.xlsx) »** : Yelen Notes vérifie les dates, calcule l'âge exact à la date de référence de l'année et ignore automatiquement les doublons en les signalant.

### 3.2. Désactivation d'un élève en cours d'année
Si un élève quitte l'école en cours d'année, **ne supprimez jamais ses résultats** : ouvrez sa fiche et cliquez sur **« Désactiver l'élève »**. Il ne comptera plus dans l'effectif actif ni dans les classements suivants, mais toutes ses notes passées restent conservées dans l'historique.

### 3.3. Passage d'une classe à une autre et redoublement
En début de nouvelle année scolaire :
1. Créez la nouvelle année scolaire et ses classes d'accueil.
2. Ouvrez **Élèves & Inscriptions → Passage d'année / Redoublement**.
3. Sélectionnez la classe d'origine (année précédente) et la classe cible (nouvelle année).
4. Pour chaque élève, choisissez `Admis(e)`, `Redouble` (la mention de la classe et de l'année redoublée est ajoutée automatiquement) ou `Sorti(e)`, puis validez.

---

## 4. Saisie rapide des notes au clavier

Dans le menu **Saisie des notes** :
1. Sélectionnez la **Classe** et la **Période** (celle-ci doit être **OUVERTE**). Vous pouvez afficher soit toute la matrice de la classe, soit une seule matière.
2. Raccourcis clavier :
   - **Entrée** ou **Flèche bas (`↓`)** : descendre à l'élève suivant dans la même matière.
   - **Flèche haut (`↑`)** : remonter à l'élève précédent.
   - **Tab** : passer à la matière suivante.
3. Valeurs acceptées dans chaque cellule :
   - **Note numérique** comprise entre la note minimale et la note maximale de la matière (ex. `7,5` ou `0` pour un zéro réel).
   - **`ABS`** (ou `A`) : élève absent à l'évaluation.
   - **`DISP`** (ou `D`) : élève dispensé dans cette matière (si la dispense y est autorisée).
   - **Cellule vide** : note non encore saisie. **Une cellule vide n'est jamais transformée en zéro** ; tant qu'une matière obligatoire est vide, le dossier de l'élève est signalé *Incomplet* et *Non classé*.
4. Chaque note modifiée est enregistrée automatiquement (pastille verte en haut à droite). Vous disposez aussi du bouton **« Enregistrer toutes les notes »** et du bouton **« Historique des modifications »**.

---

## 5. Clôture d'une période et édition des documents PDF A4

1. Saisissez les appréciations personnalisées dans **Observations bulletins** (périodiques ou annuelles).
2. Vérifiez l'absence de notes manquantes sur le **Tableau de bord** ou dans **Relevés de notes**.
3. Le Directeur ou l'Administrateur peut alors clôturer la période dans **Années & Périodes** (`Statut → CLÔTURÉE`) afin de verrouiller les notes.
4. Imprimez ou téléchargez vos documents officiels au format **PDF A4** (ou **Excel `.xlsx`**) :
   - **Relevés de notes** : vue complète par classe et période avec moyennes par matière et signatures.
   - **Classement de mérite** : par ordre décroissant de moyenne (périodique ou annuel), avec renumérotation séquentielle `1..N` et gestion des ex æquo.
   - **Tableau synoptique** : fiche statistique par période d'évaluation et par discipline (ventilation **Garçons / Filles / Total**, effectif ayant composé, ayant obtenu ou non la moyenne, taux de réussite et d'échec à 2 décimales ou `N/A`, synthèse générale de la classe, orientation PDF Paysage ou Portrait).
   - **Proposition de fin d'année** : fiche récapitulative annuelle triée par ordre décroissant de moyenne générale annuelle (`N° d'ordre`, `Nom et prénom(s)`, `Âge`, `Sexe`, `Scolarité`, `Classe redoublée` `Oui`/`Non` issue de l'historique, `Moy. 1er T`, `Moy. 2e T`, `Moy. 3e T`, `Moyenne générale annuelle`, `Rang annuel`, `Proposition` automatique `Passe` / `Redouble` / `Non déterminé`), exportable en PDF A4 Paysage multi-pages et Excel, avec volet de validation modifiable des décisions définitives et conservation de trace horodatée.
   - **Bulletins scolaires PDF** : aperçu à l'écran avant impression, génération pour **un élève**, **une sélection d'élèves cochés** ou **toute la classe** dans un seul fichier PDF multi-pages prêt à imprimer en noir et blanc ou en couleur.
