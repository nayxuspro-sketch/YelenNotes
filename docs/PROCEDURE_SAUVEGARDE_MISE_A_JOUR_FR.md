# Procédures de Sauvegarde, Restauration, Récupération et Mise à Jour — Yelen Notes

---

## 1. Sauvegarde des données scolaires

Yelen Notes réalise ses sauvegardes à chaud via l'API transactionnelle officielle de SQLite (`sqlite3.Connection.backup()`), garantissant un fichier cohérent même si l'application est en cours d'exécution, suivi d'un contrôle systématique `PRAGMA integrity_check` et d'un calcul d'empreinte `SHA-256`.

### 1.1. Sauvegarde automatique
- Activée par défaut toutes les **24 heures** (modifiable dans **Sauvegardes & Restauration**).
- Conserve les **15 dernières générations** automatiques (nombre paramétrable) dans `<DOSSIER_DONNÉES>\backups\`.

### 1.2. Sauvegarde manuelle et copie sur clé USB (fortement recommandée)
À la fin de chaque semaine de saisie et impérativement avant/après la clôture d'un trimestre :
1. Ouvrez le menu **Sauvegardes & Restauration** (profil Administrateur ou Directeur).
2. Saisissez un libellé explicite (ex. *« Fin saisie T1 CM2 »*).
3. Vous pouvez soit :
   - Indiquer directement le chemin d'une clé USB branchée sur le serveur (ex. `E:\Sauvegardes_Yelen`) ;
   - Ou cliquer sur **« Lancer la sauvegarde vérifiée »**, puis cliquer sur **« Télécharger (USB) »** dans le tableau d'historique pour enregistrer le fichier `.zip` sur une clé USB ou un disque externe conservé en lieu sûr hors du poste principal.

---

## 2. Restauration d'une sauvegarde

Seul un **Administrateur** peut exécuter une restauration :
1. Ouvrez **Sauvegardes & Restauration** et descendez à la section **Restauration d'une sauvegarde**.
2. Sélectionnez une archive dans la liste (ou importez un fichier `.zip` depuis votre clé USB).
3. Tapez exactement le mot **`RESTAURER`** dans le champ de confirmation puis validez.
4. Déroulement automatique sécurisé :
   - Contrôle préalable de l'archive ZIP, du `SHA-256` et de l'intégrité SQLite (`PRAGMA integrity_check`).
   - Création automatique d'une sauvegarde de secours de la base actuelle (`PRE_RESTORE`).
   - Restauration atomique de la base et du logo.
   - Second contrôle d'intégrité SQLite après restauration et journalisation dans `audit_logs`.

---

## 3. Contrôle après coupure électrique ou arrêt brutal

Grâce au mode journal **SQLite WAL (`Write-Ahead Logging`)**, SQLite rejoue ou annule automatiquement les transactions inachevées lors du redémarrage suivant. Pour vérifier explicitement l'intégrité de la base après une coupure de courant :
- **Depuis l'interface :** connectez-vous en Administrateur et ouvrez **Sauvegardes & Restauration** ou **Journaux & Diagnostic** : le badge **« Intégrité base active : OK »** confirme la bonne santé du fichier.
- **En ligne de commande sur le serveur :**
  ```bat
  YelenNotes.exe --check-db
  ```
  Le programme affiche `Résultat PRAGMA integrity_check : ok` (code retour `0`). Si une corruption matérielle du disque était détectée, restaurez la dernière sauvegarde `.zip` vérifiée depuis votre clé USB.

---

## 4. Procédure de récupération du compte Administrateur

Si l'administrateur de l'école a oublié son mot de passe ou si son compte est verrouillé :
1. Accédez physiquement au poste serveur Windows de l'école.
2. Ouvrez une **Invite de commandes** dans le dossier `YELEN-NOTES\`.
3. Exécutez :
   ```bat
   YelenNotes.exe --reset-admin admin
   ```
   *(Remplacez `admin` par l'identifiant du compte administrateur concerné ; s'il n'existe pas, il sera recréé avec les droits Administrateur.)*
4. Saisissez deux fois le nouveau mot de passe d'au moins 8 caractères. L'opération déverrouille immédiatement le compte et trace un événement `ADMIN_RECOVERY_CLI` dans le journal de sécurité.

---

## 5. Procédure de mise à jour du logiciel sans perte de données

Parce que les données scolaires (`C:\ProgramData\YelenNotes\`) sont strictement séparées du dossier du programme (`YELEN-NOTES\`), une mise à jour ne supprime jamais les notes ni l'historique :

1. **Arrêtez proprement** `YelenNotes.exe` sur le poste serveur.
2. Par précaution, copiez le dossier de sauvegardes `C:\ProgramData\YelenNotes\backups\` sur une clé USB.
3. Si vous aviez personnalisé un fichier `config\yelen_notes.ini`, conservez-en une copie.
4. **Remplacez le dossier programme `YELEN-NOTES\`** par la nouvelle version fournie (et remettez votre fichier `config\yelen_notes.ini` le cas échéant).
5. Lancez le nouveau `YelenNotes.exe` :
   - Au démarrage, Yelen Notes détecte la base existante, crée automatiquement une sauvegarde préalable `PRE_MIGRATION`, applique les migrations de schéma Alembic éventuelles sans écraser les données, puis ouvre l'application à jour.
   - Vérifiez la nouvelle version affichée en haut à gauche de l'écran et dans **Journaux & Diagnostic**.
