================================================================================
OVICLOUD NOTES — Application locale de gestion et de calcul des notes scolaires
Écoles primaires du Burkina Faso — Version 1.5.0
================================================================================
Nom commercial mis à jour vers OviCloud Notes. Les noms techniques historiques YelenNotes.exe,
YELEN-NOTES et les chemins de données sont conservés pour protéger les licences et installations existantes.


FONCTIONNEMENT HORS LIGNE (WINDOWS 10 / WINDOWS 11 — 64 BITS)
Aucune connexion Internet ni installation de Python n'est requise.

1. DÉMARRAGE EN MODE MONOPOSTE (UN SEUL ORDINATEUR)
   - Double-cliquez sur "YelenNotes.exe" (ou "Lancer_Monoposte.bat").
   - Votre navigateur s'ouvre automatiquement sur : http://127.0.0.1:5000
   - Lors du tout premier lancement, créez le compte Administrateur de l'école,
     puis suivez le guide en 5 étapes affiché sur le tableau de bord.
   - Ne fermez la fenêtre noire du serveur qu'une fois votre travail terminé.

2. DÉMARRAGE EN MODE MULTIPOSTE (RÉSEAU LOCAL WI-FI OU ETHERNET)
   - Sur l'ordinateur serveur de l'école uniquement :
     a) Exécutez une première fois "Autoriser_PareFeu_Reseau_Prive.bat"
        (clic droit -> Exécuter en tant qu'administrateur).
     b) Lancez "Lancer_Multiposte_Reseau.bat".
   - La fenêtre affiche l'adresse IP locale du serveur (ex. http://192.168.1.10:5000).
   - Sur les autres postes de l'école, ouvrez simplement le navigateur et tapez
     cette adresse. N'installez rien sur les postes clients et ne partagez jamais
     directement le fichier ecole.db sur le réseau.

3. EMPLACEMENT DES DONNÉES SCOLAIRES (SÉPARÉES DU PROGRAMME)
   - Par défaut : C:\ProgramData\YelenNotes\ (ou %LOCALAPPDATA%\YelenNotes\)
   - Remplacer le dossier YELEN-NOTES lors d'une mise à jour ne supprime jamais
     vos données scolaires.
   - Effectuez régulièrement des sauvegardes sur une clé USB via le menu
     "Sauvegardes & Restauration".

4. BASE DE DÉMONSTRATION SÉPARÉE
   - Pour découvrir le logiciel avec des données fictives sans toucher à la base
     réelle de l'école, lancez "Lancer_Demonstration.bat".

5. RÉCUPÉRATION DU MOT DE PASSE ADMINISTRATEUR SUR LE SERVEUR
   - Ouvrez une invite de commandes dans ce dossier et exécutez :
     YelenNotes.exe --reset-admin admin

Consultez le dossier docs/ pour les guides complets d'utilisation et de maintenance.
================================================================================
