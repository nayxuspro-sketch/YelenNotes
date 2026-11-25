YELEN NOTES
CAHIER DES CHARGES
Application de gestion et de calcul des notesÉcoles primaires du Burkina Faso
Version 1.0  |  Novembre 2026Application hors ligne • Windows 10/11 • Monoposte et réseau local

## Fiche de contrôle du document


| Élément | Description |
| --- | --- |
| Nom du projet | Yelen Notes |
| Nature du document | Cahier des charges fonctionnel et technique |
| Public cible | Directeurs d'école, enseignants et personnels autorisés du primaire |
| Zone de déploiement | Burkina Faso |
| Systèmes cibles | Windows 10 et Windows 11, 64 bits |
| Modes d'utilisation | Monoposte et multiposte sur réseau local sans Internet |
| Distribution | Dossier portable à copier-coller |
| Statut | Document de référence pour cadrer la conception et le développement |


## 1. Présentation du projet


### 1.1. Contexte

Les écoles primaires doivent pouvoir saisir et traiter les résultats scolaires même lorsque la connexion Internet est absente ou instable. Les équipements disponibles et les compétences informatiques des utilisateurs peuvent être limités. L'application doit donc être légère, simple à prendre en main, fiable et facile à déployer.

### 1.2. Objectif général

Concevoir une application web locale permettant de gérer les paramètres scolaires, les élèves, les matières, les évaluations et les notes, puis de calculer les moyennes, établir les classements et produire les relevés et bulletins périodiques et annuels.

### 1.3. Objectifs spécifiques

Centraliser les informations d'un établissement, de ses classes et de ses élèves.
Faciliter la saisie rapide et contrôlée des notes.
Automatiser les calculs conformément aux règles pédagogiques configurées.
Produire des relevés, classements et bulletins imprimables en PDF.
Conserver l'historique des élèves et des résultats sur plusieurs années scolaires.
Permettre la sauvegarde et la restauration des données.
Fonctionner sans connexion Internet, sur un seul ordinateur ou sur un réseau local.

### 1.4. Périmètre

La première version vise les écoles primaires et la gestion des résultats d'une ou plusieurs classes au sein d'un établissement. Elle ne constitue pas, dans sa première version, une plateforme nationale connectée ni un système de gestion centralisée de plusieurs établissements distants.

## 2. Utilisateurs et droits d'accès


| Profil | Droits attendus |
| --- | --- |
| Administrateur | Configuration générale, comptes utilisateurs, paramètres, sauvegardes, restauration et accès à tous les modules. |
| Directeur / directrice | Gestion des classes, élèves, périodes, matières, notes, clôture des périodes, classements et bulletins selon les autorisations. |
| Enseignant titulaire | Consultation des élèves de ses classes, saisie et correction des notes pendant les périodes ouvertes, consultation des résultats. |
| Consultation | Consultation et impression des documents autorisés, sans modification. |

Les droits doivent être contrôlés côté serveur. Masquer un bouton dans l'interface ne suffit pas à protéger une fonction.

## 3. Paramètres de l'établissement et de l'année scolaire


### 3.1. Paramètres généraux

Pays (valeur initiale proposée : Burkina Faso).
Devise (paramétrable).
Circonscription d'Éducation de Base (CEB).
Nom de l'établissement.
Logo de l'établissement.
Adresse, localité et coordonnées de contact, facultatives.
Année scolaire active.

### 3.2. Paramètres de classe

Intitulé de la classe (ex. CP1, CP2, CE1, CE2, CM1, CM2).
Nom et prénoms du directeur ou de la directrice.
Titre ou fonction du responsable de l'établissement.
Nom et prénoms du titulaire de la classe.
Année scolaire à laquelle la classe est rattachée.
Les informations de direction et de titulariat doivent pouvoir être historisées par année scolaire afin que les anciens bulletins conservent les informations valables au moment de leur édition.

## 4. Gestion des années scolaires, classes et périodes


### 4.1. Années scolaires

Créer, modifier, clôturer et consulter les années scolaires. Une année clôturée reste consultable et ses résultats ne sont pas écrasés lors de la création de l'année suivante.

### 4.2. Classes

Créer et gérer plusieurs classes dans un établissement. Une classe est rattachée à une année scolaire. Les matières, coefficients et titulaires peuvent varier selon la classe et l'année.

### 4.3. Périodes d'évaluation


| Champ | Description |
| --- | --- |
| Intitulé | Premier trimestre, deuxième trimestre, troisième trimestre ou libellé personnalisé. |
| Date de début / fin | Dates facultatives ou obligatoires selon le paramétrage retenu. |
| Statut | Brouillon, ouverte ou clôturée. |
| Verrouillage | Une période clôturée n'accepte plus de modification ordinaire des notes. |

Le nombre et le nom des périodes doivent être configurables. Le système ne doit pas imposer définitivement trois trimestres.

## 5. Gestion des matières et des éléments d'évaluation

Les matières sont configurées pour une classe et une année scolaire. Chaque matière comprend les champs suivants :

| Champ | Règle fonctionnelle |
| --- | --- |
| Matière | Nom de la discipline ou de l'élément évalué. |
| Coefficient | Valeur numérique positive, entière ou décimale selon le besoin. |
| Note minimale | Borne basse autorisée pour une note numérique. |
| Note maximale | Borne haute autorisée. |
| Moyenne sur | Barème de référence, par exemple 10 ou 20 ; valeur décimale autorisée. |
| Ordre d'affichage | Position de la matière sur les relevés et bulletins. |
| Statut | Active ou inactive pour la classe et l'année concernées. |

L'application doit contrôler la cohérence des paramètres : note minimale inférieure à la note maximale, coefficient positif et barème strictement supérieur à zéro. Les conversions de barème doivent être explicites et testées.

## 6. Grille des appréciations

L'utilisateur autorisé définit une grille d'appréciations associant des intervalles de moyennes à des libellés textuels.

| Champ | Description |
| --- | --- |
| Moyenne minimale | Borne inférieure de l'intervalle. |
| Moyenne maximale | Borne supérieure de l'intervalle. |
| Appréciation | Texte affiché, par exemple Très bien, Bien, Assez bien, Passable ou Insuffisant. |
| Ordre | Ordre d'affichage ou de priorité. |

Les seuils et libellés sont personnalisables. L'application doit empêcher les chevauchements et signaler les intervalles manquants. La convention de bornes (par exemple borne basse incluse et borne haute exclue, sauf dernier intervalle) doit être clairement définie.

## 7. Gestion des élèves


| Champ | Description / règle |
| --- | --- |
| Numéro | Numéro d'ordre dans la classe ; possibilité de renumérotation contrôlée. |
| Nom et prénoms | Identité de l'élève. |
| Sexe | M ou F. |
| Date de naissance | Date connue, avec contrôle de validité. |
| Âge | Calcul automatique à une date de référence, idéalement la date de rentrée scolaire. |
| Scolarité | Information textuelle à définir par l'établissement. |
| Classe redoublée | Information sur le redoublement ; préciser la classe et/ou l'année concernée. |
| Observations | Champ libre facultatif. |
| Actif | Case à cocher. Un élève inactif est conservé dans l'historique. |


### 7.1. Fonctions de gestion

Ajouter, modifier et consulter une fiche élève.
Rechercher par nom, prénoms ou numéro.
Afficher la liste alphabétique des élèves de la classe.
Filtrer les élèves actifs et inactifs.
Importer une liste depuis un fichier Excel avec contrôle des doublons.
Exporter la liste vers Excel.
Désactiver un élève sans supprimer ses notes ni son historique.
Gérer le passage d'une classe à une autre et le redoublement d'une année à l'autre.
L'âge doit être calculé à partir de la date de naissance et de la date de référence, et non simplement par soustraction de l'année de naissance.

## 8. Saisie des notes


### 8.1. Organisation

La saisie est filtrée par année scolaire, classe, période et matière. L'écran principal présente les élèves en lignes et les champs de notes en colonnes, ou propose une saisie par matière selon le choix ergonomique retenu.

### 8.2. Fonctions attendues

Saisie individuelle et collective des notes.
Navigation au clavier entre les cellules.
Enregistrement automatique avec indication claire de l'état de sauvegarde, ou bouton d'enregistrement explicite.
Validation des notes selon les bornes configurées.
Signalement visuel des valeurs invalides.
Recherche et filtrage des élèves.
Possibilité de compléter les notes manquantes.
Modification autorisée tant que la période est ouverte et selon les droits.
Historique des modifications importantes : auteur, date et ancienne/nouvelle valeur lorsque pertinent.

### 8.3. États d'une note


| État | Signification |
| --- | --- |
| Non saisie | Aucune note n'a encore été enregistrée. |
| Note numérique | Une note valide, y compris zéro, a été saisie. |
| Absent | L'élève était absent à l'évaluation ; le traitement doit suivre la règle pédagogique configurée. |
| Dispensé / non concerné | L'élève n'est pas évalué dans cette matière, si ce cas est autorisé. |

Une note manquante ne doit jamais être transformée automatiquement en zéro. L'absence, le zéro réel et la donnée non saisie doivent rester distincts.

## 9. Calculs et règles pédagogiques


### 9.1. Calcul par matière

L'application contrôle la note saisie et, si nécessaire, la ramène au barème de référence configuré. La règle de conversion doit être documentée. Le coefficient est appliqué à la moyenne de matière selon la formule validée.

### 9.2. Moyenne générale pondérée

Moyenne générale = Σ (moyenne de matière × coefficient) / Σ coefficients
Cette formule constitue la règle proposée par défaut lorsque toutes les matières prises en compte disposent d'une valeur exploitable. La politique de traitement des absences, matières non notées, dispenses et notes manquantes doit être validée avant développement.

### 9.3. Paramètres de calcul

Nombre de décimales à afficher.
Règle d'arrondi (à définir et à appliquer uniformément).
Matières prises en compte dans la moyenne.
Traitement des absences et dispenses.
Seuils des appréciations.
Règle de calcul de la moyenne annuelle.
Règle de classement et traitement des ex æquo.

### 9.4. Classement

Le classement est établi par moyenne générale décroissante. La méthode de rang en cas d'ex æquo doit être choisie explicitement (rang partagé ou classement séquentiel). Les élèves inactifs ou non classables sont exclus selon des règles configurées et affichées.

## 10. Relevés de notes

Produire un relevé pour une période et une classe. Il comporte au minimum :
Nom, logo et coordonnées de l'établissement.
Année scolaire, classe et période.
Liste des élèves.
Matières, notes, barèmes et coefficients.
Totaux des points et des coefficients, lorsque pertinents.
Moyennes par matière et moyenne générale.
Appréciations et rangs.
Date d'édition et informations de validation.
Le relevé doit être consultable à l'écran, imprimable et exportable en PDF.

## 11. Classement par ordre de mérite

Classement par période et par année scolaire.
Tri décroissant selon la moyenne générale.
Affichage du rang, du nom, des prénoms, de la moyenne et de l'appréciation.
Gestion des ex æquo selon la règle retenue.
Exclusion explicite des élèves non classables et inactifs selon les paramètres.
Export PDF et, si souhaité, Excel.

## 12. Bulletins scolaires


### 12.1. Bulletin périodique individuel

Chaque bulletin présente l'identité de l'établissement et de l'élève, l'année scolaire, la classe, la période, les matières, les notes, les barèmes, les coefficients, les moyennes, les appréciations par matière, les totaux, la moyenne générale, le rang, l'appréciation générale, les observations et les emplacements de signature du titulaire et du directeur ou de la directrice.

### 12.2. Production collective

Générer le bulletin d'un élève.
Générer les bulletins de tous les élèves d'une classe.
Générer les bulletins d'une sélection d'élèves.
Assembler plusieurs bulletins dans un même fichier PDF, avec séparation claire par élève.

### 12.3. Bulletin annuel

Le bulletin annuel récapitule les résultats des périodes de l'année, les moyennes périodiques, les moyennes annuelles, les rangs, les appréciations, les observations et les signatures. La méthode de calcul de la moyenne annuelle doit être paramétrable et validée par l'établissement.

### 12.4. Présentation

Les documents PDF sont conçus pour une impression A4, avec une mise en page lisible en noir et blanc comme en couleur. Le logo, les marges, les tableaux et les zones de signature doivent être correctement positionnés. Prévoir un aperçu avant impression.

## 13. Tableau de bord

Le tableau de bord présente les informations utiles à l'utilisateur selon son profil : année scolaire active, classe sélectionnée, nombre d'élèves, effectifs filles/garçons, périodes ouvertes ou clôturées et progression de la saisie des notes. Les statistiques affichées doivent être calculées à partir des données réelles.

## 14. Sauvegarde et restauration

Sauvegarde manuelle à la demande de l'utilisateur autorisé.
Sauvegarde automatique selon une fréquence configurable.
Choix de l'emplacement de sauvegarde.
Restauration d'une sauvegarde avec avertissement et confirmation.
Vérification de l'intégrité avant et après restauration.
Conservation de plusieurs générations de sauvegardes.
Possibilité de copier les sauvegardes sur clé USB ou disque externe.
Sauvegarde préalable avant migration ou opération majeure.
La sauvegarde doit être réalisée avec une méthode sûre pour SQLite, tenant compte des transactions actives. Il faut éviter une simple copie incohérente du fichier pendant une écriture.

## 15. Exigences d'interface et d'ergonomie

Interface entièrement en français.
Présentation sobre, professionnelle et cohérente.
Navigation simple avec menu clairement structuré.
Formulaires courts, libellés explicites et contrôles de saisie.
Tableaux lisibles, recherche et filtres utiles.
Messages d'erreur compréhensibles et indications de réussite.
Compatibilité avec les résolutions courantes des ordinateurs scolaires.
Fonctionnement des ressources graphiques en local, sans CDN ni police distante.
Mise en page adaptée à l'impression.

## 16. Architecture technique et contraintes de déploiement


| Élément | Choix / exigence |
| --- | --- |
| Langage | Python 3.12, 64 bits |
| Framework | Flask |
| Serveur | Waitress |
| Base | SQLite locale sur l'ordinateur serveur |
| ORM | SQLAlchemy |
| Migrations | Flask-Migrate / Alembic |
| Interface | HTML5, CSS3, Bootstrap 5 local, JavaScript natif, Jinja2 |
| PDF / Excel | ReportLab / OpenPyXL |
| Packaging | PyInstaller ONEDIR |
| Systèmes | Windows 10 et 11, 64 bits |
| Réseau | HTTP sur réseau local privé ; aucun accès Internet obligatoire |


### 16.1. Mode monoposte

Le serveur et la base fonctionnent sur le même ordinateur. Adresse proposée : http://127.0.0.1:5000.

### 16.2. Mode multiposte

Un ordinateur héberge l'application et la base. Les autres postes accèdent par navigateur à l'adresse IP locale du serveur, par exemple http://192.168.1.10:5000. Le pare-feu Windows doit autoriser le port uniquement sur le réseau privé. Les clients ne doivent jamais ouvrir directement le fichier SQLite.

### 16.3. Distribution portable

Le dossier distribué contient l'exécutable, l'environnement Python embarqué, les bibliothèques et les ressources locales. L'application ne doit exiger ni installation de Python, ni Docker, ni serveur externe, ni téléchargement de dépendances.
YELEN-NOTES/                 Programme portable├── YelenNotes.exe├── _internal/                Dépendances embarquées├── resources/                Templates, CSS, JS, images, polices├── config/├── README.txt└── VERSION.txtC:\ProgramData\YelenNotes\  Données indépendantes├── database\ecole.db├── backups\├── documents\└── logs\
Le répertoire des données doit être configurable et distinct du dossier du programme afin qu'une mise à jour ne supprime pas les résultats scolaires.

## 17. Sécurité, confidentialité et fiabilité

Authentification pour les fonctions de gestion et pour tout accès multiposte.
Hachage robuste des mots de passe ; aucun mot de passe stocké en clair.
Contrôle des autorisations côté serveur.
Protection CSRF et validation de toutes les entrées.
Journalisation des opérations sensibles.
Gestion des erreurs sans exposer de détails techniques aux utilisateurs.
Transactions de base de données et contraintes d'intégrité.
Prévention des suppressions accidentelles.
Conservation des historiques et sauvegardes vérifiables.
Démarrage et arrêt propres du serveur.
Le déploiement hors ligne ne remplace pas la sécurité du système Windows. L'école doit protéger l'accès physique à l'ordinateur serveur, utiliser un compte Windows approprié et conserver des copies de sauvegarde sur un support séparé.

## 18. Exigences non fonctionnelles


| Critère | Exigence |
| --- | --- |
| Disponibilité | Utilisable sans Internet après installation. |
| Performance | Réponse fluide sur un ordinateur modeste et pour une classe scolaire habituelle. |
| Portabilité | Dossier copiable sur un autre PC Windows compatible. |
| Maintenabilité | Code organisé en modules, configuration documentée et dépendances figées. |
| Évolutivité | Possibilité d'ajouter des modules et, si nécessaire, un autre moteur de base de données. |
| Intégrité | Aucune note ou donnée scolaire ne doit être perdue lors d'une opération normale. |
| Utilisabilité | Prise en main possible par un utilisateur scolaire peu expérimenté. |


## 19. Tests et critères de recette


| N° | Test de validation | Résultat attendu |
| --- | --- | --- |
| 1 | Copie du dossier sur un PC Windows vierge | L'application démarre sans installer Python. |
| 2 | Utilisation sans Internet | Toutes les fonctions principales restent disponibles. |
| 3 | Mode monoposte | Accès via localhost et enregistrement correct des données. |
| 4 | Mode multiposte | Plusieurs postes accèdent au même serveur sur le réseau local. |
| 5 | Saisie concurrente | Les écritures sont traitées sans perte silencieuse ; conflits et erreurs sont gérés. |
| 6 | Contrôle des notes | Les valeurs hors barème sont refusées ou signalées. |
| 7 | Notes manquantes et absences | Les états restent distincts d'une note égale à zéro. |
| 8 | Calculs et classements | Résultats conformes à des jeux de tests validés. |
| 9 | Bulletins PDF | Documents lisibles et correctement imprimables en A4. |
| 10 | Sauvegarde / restauration | Les données restaurées sont complètes et intègres. |
| 11 | Mise à jour | Le remplacement du programme conserve les données. |
| 12 | Arrêt imprévu | La base peut être contrôlée et récupérée selon les procédures prévues. |

La recette finale doit être effectuée sur au moins un ordinateur Windows 10 et un ordinateur Windows 11, sans Python installé et sans connexion Internet. Les tests pédagogiques doivent utiliser des exemples dont les résultats attendus ont été calculés et validés à l'avance.

## 20. Livrables attendus

Code source complet et organisé.
Fichier des dépendances avec versions figées.
Modèle de données et scripts de création/migration.
Application Windows portable PyInstaller ONEDIR.
Base de démonstration et données fictives clairement identifiées.
Tests automatisés des règles de calcul et tests fonctionnels essentiels.
Modèles de relevés et bulletins PDF.
Guide utilisateur en français.
Guide d'installation et de démarrage monoposte/multiposte.
Procédure de sauvegarde et de restauration.
Procédure de mise à jour sans perte de données.

## 21. Phasage du développement

Atelier de validation des règles pédagogiques et des cas particuliers.
Conception du modèle de données et validation des relations.
Initialisation du projet, configuration et démarrage local.
Développement des paramètres, années, classes et périodes.
Développement des élèves et de l'historique scolaire.
Développement des matières, coefficients et appréciations.
Développement de la saisie des notes.
Développement des calculs, classements et relevés.
Développement des bulletins périodiques et annuels.
Développement des comptes, rôles, sauvegardes et journaux.
Tests monoposte et multiposte.
Packaging, documentation et recette sur Windows 10/11.
Chaque phase doit être testée et validée avant de passer à la suivante. L'agent de développement doit fournir les fichiers complets concernés, les instructions de lancement et les résultats des tests.

## 22. Points à valider avant le développement

Les points ci-dessous nécessitent une décision pédagogique ou organisationnelle de l'établissement. Le développeur ne doit pas inventer ces règles.

| Point à valider | Décision attendue |
| --- | --- |
| Structure de l'année | Nombre et intitulés des périodes d'évaluation. |
| Barèmes | Barèmes par matière et règles de conversion éventuelles. |
| Coefficients | Coefficients applicables à chaque matière et classe. |
| Absences | Traitement d'une absence à une évaluation. |
| Matières non notées | Règle d'exclusion ou autre traitement. |
| Arrondis | Nombre de décimales et méthode d'arrondi. |
| Appréciations | Seuils exacts et libellés validés. |
| Ex æquo | Règle de classement à appliquer. |
| Moyenne annuelle | Moyenne simple, pondérée ou autre règle officielle retenue. |
| Bulletin | Modèle officiel ou modèle propre à l'établissement. |
| Accès multiposte | Nombre de postes et profils autorisés. |


## 23. Références techniques

Flask : https://flask.palletsprojects.com/
Waitress : https://docs.pylonsproject.org/projects/waitress/en/stable/
SQLite — WAL : https://www.sqlite.org/wal.html
SQLite — When to use : https://www.sqlite.org/whentouse.html
SQLAlchemy : https://www.sqlalchemy.org/
PyInstaller : https://pyinstaller.org/
Python pour Windows : https://www.python.org/downloads/windows/

## 24. Consigne générale au prestataire ou à l'agent IA

Réaliser une application opérationnelle correspondant aux exigences de ce cahier des charges. Ne pas livrer uniquement une maquette ou des écrans statiques. Respecter les contraintes de fonctionnement hors ligne, de portabilité Windows, de conservation des données et de simplicité de maintenance. Commencer par soumettre le modèle de données, les règles de calcul et les maquettes fonctionnelles pour validation. Toute modification substantielle du périmètre ou du stack doit être justifiée et approuvée.
FIN DU CAHIER DES CHARGES
