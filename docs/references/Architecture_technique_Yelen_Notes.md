YELEN NOTES
ARCHITECTURE TECHNIQUE DÉFINITIVE
Application locale de gestion et de calcul des notesÉcoles primaires du Burkina Faso
Windows 10 et 11  •  Hors ligne  •  Monoposte et multiposteDistribution par dossier portable

## 1. Décisions de conception

Le projet consiste à développer une application web locale, autonome et portable pour gérer les élèves, les évaluations, les notes, les moyennes, les classements et les bulletins des écoles primaires au Burkina Faso. L'application doit fonctionner sans connexion Internet et être facile à installer et à maintenir dans des établissements disposant de moyens informatiques variés.
La décision retenue est de construire une seule application avec deux modes d'utilisation : monoposte (un ordinateur) et multiposte (plusieurs ordinateurs reliés au même réseau local). Dans les deux cas, un seul processus serveur accède à la base de données.

| Décision | Choix retenu |
| --- | --- |
| Systèmes cibles | Windows 10 et Windows 11, 64 bits |
| Modes | Monoposte et multiposte sur réseau local |
| Distribution | Dossier portable à copier-coller |
| Backend | Python 3.12 + Flask |
| Serveur HTTP local | Waitress |
| Base de données | SQLite, stockée localement sur l'ordinateur serveur |
| Packaging | PyInstaller en mode ONEDIR |
| Connexion Internet | Non requise pour l'utilisation normale |


## 2. Stack technologique


| Couche | Technologie | Utilisation |
| --- | --- | --- |
| Langage | Python 3.12 | Logique métier, calculs et orchestration |
| Framework web | Flask | Routes, vues et logique serveur |
| Serveur local | Waitress | Exécution du serveur HTTP sous Windows |
| Base de données | SQLite | Stockage local des données scolaires |
| Accès aux données | SQLAlchemy | Modèles, requêtes et transactions |
| Migrations | Flask-Migrate / Alembic | Évolution contrôlée du schéma |
| Interface | HTML5, CSS3, Bootstrap 5 | Pages et mise en forme |
| Interactions | JavaScript natif | Saisie, contrôles et confort d'utilisation |
| Templates | Jinja2 | Génération des pages HTML |
| Formulaires | Flask-WTF | Validation et protection des formulaires |
| PDF | ReportLab | Bulletins, relevés et états imprimables |
| Excel | OpenPyXL | Importation et exportation des listes |
| Packaging | PyInstaller ONEDIR | Application Windows autonome |

Python 3.12 est retenu comme version de référence pour sa maturité et sa compatibilité avec l'écosystème de bibliothèques choisi. La version exacte de chaque dépendance devra être figée dans un fichier de dépendances afin de rendre les compilations reproductibles.

## 3. Architecture des deux modes


### 3.1. Mode monoposte

L'application, le serveur Waitress et la base SQLite fonctionnent sur le même ordinateur. L'utilisateur lance YelenNotes.exe ; le programme démarre le serveur local puis ouvre le navigateur à l'adresse http://127.0.0.1:5000. Aucun réseau ni accès Internet n'est nécessaire.
Utilisateur   │Navigateur sur le même ordinateur   │  http://127.0.0.1:5000   ▼Flask + Waitress   │SQLAlchemy   │SQLite : ecole.db (disque local)

### 3.2. Mode multiposte

Un ordinateur de l'école joue le rôle de serveur. Il héberge le programme, Waitress et le fichier SQLite. Les autres ordinateurs utilisent un navigateur et se connectent à l'adresse IP locale du serveur, par exemple http://192.168.1.10:5000. Le réseau peut être un réseau Wi-Fi ou Ethernet sans accès Internet.
Ordinateur serveur de l'école  YelenNotes.exe + Flask + Waitress + SQLAlchemy                         │                    SQLite local                         ▲                         │          Réseau local Wi-Fi / Ethernet             ┌───────────┼───────────┐             ▼           ▼           ▼         Direction  Secrétariat  Enseignant         Navigateur  Navigateur   Navigateur
Les ordinateurs clients n'installent pas l'application et n'accèdent jamais directement au fichier SQLite. Ils communiquent avec l'application par HTTP sur le réseau local.

## 4. Règles essentielles pour SQLite en multiposte

SQLite convient à une école de taille modeste si les accès à la base sont centralisés sur l'ordinateur serveur. Le fichier de base de données doit rester sur un disque local de cet ordinateur.
Ne jamais placer le fichier ecole.db dans un partage réseau, un NAS ou un dossier synchronisé pour que plusieurs postes l'ouvrent directement.
Tous les accès à la base doivent passer par Flask/SQLAlchemy sur le serveur.
Activer le mode WAL lorsque les connexions à SQLite sont toutes locales à l'ordinateur serveur.
Configurer un délai d'attente raisonnable en cas de verrouillage temporaire de la base.
Utiliser des transactions courtes et gérer explicitement les erreurs de concurrence.
Prévoir des tests avec plusieurs utilisateurs qui enregistrent des notes au même moment.
SQLite permet plusieurs lectures simultanées, mais les écritures sont sérialisées : une seule transaction d'écriture peut être active à la fois. Le mode WAL améliore la coexistence des lectures et des écritures, mais ne transforme pas SQLite en base multi-écrivains. Si le nombre d'utilisateurs ou la fréquence des écritures augmente fortement, prévoir une évolution vers PostgreSQL.

## 5. Organisation du dossier portable

Le format PyInstaller ONEDIR est recommandé plutôt que ONEFILE. Il produit un dossier contenant l'exécutable et ses bibliothèques. Cette organisation facilite le diagnostic, les mises à jour et l'inspection des ressources embarquées.
YELEN-NOTES/                 (dossier du programme)├── YelenNotes.exe├── _internal/                (Python et dépendances embarqués)├── resources/│   ├── templates/│   ├── static/│   │   ├── css/│   │   ├── js/│   │   ├── bootstrap/│   │   └── fonts/│   ├── images/│   └── reports/├── config/├── README.txt└── VERSION.txtC:\ProgramData\YelenNotes\  (données indépendantes du programme)├── database\ecole.db├── backups\├── documents\bulletins\├── documents\releves\└── logs\application.log
Le chemin des données doit être configurable. Il faut prévoir les permissions Windows nécessaires et éviter de stocker les données dans le dossier du programme, qui peut être remplacé lors d'une mise à jour. Pour une installation sans droits administrateur, un répertoire de données propre à l'utilisateur peut être proposé.

## 6. Démarrage et arrêt de l'application

L'utilisateur double-clique sur YelenNotes.exe.
Le programme lit la configuration et détermine le mode monoposte ou multiposte.
Il vérifie le répertoire de données et crée la base si elle n'existe pas.
Il applique les migrations de base de données nécessaires après vérification et sauvegarde préalable.
Il démarre Waitress sur l'interface réseau appropriée.
Il ouvre automatiquement le navigateur sur l'adresse locale ou affiche l'adresse réseau à utiliser.
À la fermeture, le programme arrête proprement le serveur et libère les ressources.

| Mode | Adresse d'accès |
| --- | --- |
| Monoposte | http://127.0.0.1:5000 |
| Multiposte | http://IP-DU-SERVEUR:5000 |

En mode multiposte, le serveur doit écouter sur l'interface réseau locale (et non uniquement sur 127.0.0.1). Le pare-feu Windows doit autoriser le port choisi uniquement sur le profil réseau privé. Il est préférable de réserver une adresse IP au serveur dans le routeur ou de configurer une adresse locale stable.

## 7. Sécurité et gestion des utilisateurs


| Profil | Droits principaux |
| --- | --- |
| Administrateur | Tous les paramètres, utilisateurs, sauvegardes et opérations de maintenance |
| Directeur | Gestion des classes, élèves, notes, périodes et bulletins selon autorisation |
| Enseignant | Consultation de ses classes et saisie des notes autorisées |
| Consultation | Consultation et impression sans modification |

Connexion obligatoire par identifiant et mot de passe en mode réseau.
Stockage des mots de passe sous forme de hachages robustes, jamais en clair.
Contrôle des autorisations côté serveur pour chaque opération sensible.
Protection CSRF des formulaires et validation des données reçues.
Journalisation des actions importantes : saisies, corrections, clôtures et restaurations.
Déconnexion après une période d'inactivité configurable.
Confirmation des suppressions et possibilité de désactiver un élève sans supprimer son historique.
Prévoir une procédure documentée de récupération du compte administrateur.
Le fonctionnement hors ligne ne dispense pas de sécuriser les postes Windows, de limiter les accès physiques au serveur et de conserver des sauvegardes hors de l'ordinateur principal.

## 8. Sauvegarde, restauration et mises à jour

Les données doivent survivre au remplacement du dossier du programme.
Créer des sauvegardes automatiques et manuelles.
Utiliser l'API de sauvegarde SQLite ou une procédure cohérente avec les transactions actives ; ne pas copier simplement le fichier pendant une écriture sans précaution.
Vérifier l'intégrité des sauvegardes et tester régulièrement leur restauration.
Conserver au moins une copie sur un support externe distinct : clé USB ou disque externe.
Avant une migration de base, créer une sauvegarde vérifiée.
Ne jamais supprimer ou écraser automatiquement les anciennes données lors d'une mise à jour.
La mise à jour doit remplacer le dossier du programme, puis appliquer les migrations prévues. Le programme doit afficher clairement la version installée et la version de la base.

## 9. Configuration matérielle indicative


| Élément | Minimum indicatif | Conseil |
| --- | --- | --- |
| Système | Windows 10/11 64 bits | Windows maintenu et à jour |
| Processeur | Intel Core i3 ou équivalent | Processeur récent d'entrée de gamme |
| Mémoire | 4 Go RAM | 8 Go RAM |
| Stockage | SSD 128 Go | SSD avec espace pour les sauvegardes |
| Réseau multiposte | Routeur Wi-Fi ou Ethernet | Ethernet pour le serveur si possible |
| Postes clients | Navigateur moderne | 2 à 10 postes pour le premier déploiement |

Ces valeurs sont des repères de déploiement, pas des exigences absolues. Il faudra tester les performances sur les ordinateurs réellement utilisés dans les écoles.

## 10. Évolution de l'application


| Version | Périmètre |
| --- | --- |
| Version 1 — Locale | Élèves, classes, matières, périodes, notes, calculs, classements, bulletins PDF, sauvegarde/restauration, monoposte et multiposte SQLite. |
| Version 2 — Avancée | Multi-classes, gestion détaillée des enseignants, import/export Excel, statistiques, historique pluriannuel et permissions affinées. |
| Version 3 — Évolutive | Option de synchronisation ou de déploiement centralisé ; PostgreSQL pour les installations nécessitant davantage de concurrence. |

Il est préférable de ne pas ajouter Docker, un serveur cloud ou PostgreSQL à la première version. L'accès aux données via SQLAlchemy facilitera une évolution future, mais le passage à un autre moteur nécessitera des migrations et des tests.

## 11. Plan de développement

Valider les règles pédagogiques : barèmes, coefficients, absences, arrondis, moyennes et ex æquo.
Concevoir le modèle relationnel et les contraintes de la base de données.
Créer le projet Flask, la configuration et le système de démarrage.
Développer les paramètres, années scolaires, classes et périodes.
Développer les fiches élèves et l'historique des inscriptions.
Développer les matières, coefficients et grilles d'appréciation.
Développer la saisie des notes et les contrôles de cohérence.
Développer les calculs, classements et bulletins PDF.
Développer les utilisateurs, permissions et journaux.
Développer les sauvegardes, restaurations et migrations.
Tester le mode monoposte puis le mode multiposte sur réseau local.
Créer le dossier portable PyInstaller ONEDIR et tester sur Windows vierge.

## 12. Tests de validation obligatoires

Démarrage par simple double-clic après copie du dossier.
Fonctionnement sans Python installé sur le poste cible.
Fonctionnement sans connexion Internet et sans CDN.
Test sur Windows 10 et Windows 11 64 bits.
Accès monoposte par localhost.
Accès multiposte depuis plusieurs navigateurs du réseau local.
Saisie simultanée de notes par plusieurs utilisateurs et traitement des conflits.
Vérification des calculs sur des cas de test connus, y compris absences et notes manquantes.
Génération et impression de bulletins PDF.
Sauvegarde, restauration et contrôle d'intégrité de la base.
Mise à jour du programme sans perte des données.
Test après arrêt brutal ou redémarrage de l'ordinateur.

## 13. Consignes finales à l'agent de développement

Développer une application réellement fonctionnelle, et non une simple maquette. Respecter l'architecture définie dans ce document. Ne pas introduire de technologie supplémentaire sans justification concrète. Commencer par présenter le modèle de données, les règles de calcul et les choix de sécurité ; attendre leur validation avant de développer l'ensemble des modules.
Les calculs scolaires doivent être paramétrables et vérifiables. Les règles de moyenne annuelle, les barèmes, les coefficients, les arrondis, les seuils d'appréciation et le traitement des absences ne doivent pas être supposés : ils doivent être confirmés avec les règles pédagogiques applicables à l'établissement et au Burkina Faso.

## 14. Références techniques officielles

Flask : https://flask.palletsprojects.com/
Waitress : https://docs.pylonsproject.org/projects/waitress/en/stable/
SQLite — WAL : https://www.sqlite.org/wal.html
SQLite — When to use : https://www.sqlite.org/whentouse.html
SQLAlchemy : https://www.sqlalchemy.org/
PyInstaller : https://pyinstaller.org/
Python Windows : https://www.python.org/downloads/windows/
Fin du document
