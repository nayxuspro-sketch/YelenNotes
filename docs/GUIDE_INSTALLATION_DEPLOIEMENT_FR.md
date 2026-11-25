# Guide d'Installation et de Déploiement (Monoposte & Multiposte) — Yelen Notes

**Systèmes cibles :** Windows 10 et Windows 11 (64 bits)  
**Prérequis réseau :** Aucun accès Internet requis. Fonctionne sur un seul PC (monoposte) ou sur réseau local Wi-Fi / câble Ethernet (multiposte).

---

## 1. Structure du dossier portable Windows (`YELEN-NOTES`)

Yelen Notes est distribué sous forme d'un dossier portable autonome compilé avec **PyInstaller (`ONEDIR`)**. Aucun interpréteur Python, aucun Docker et aucun serveur externe n'ont besoin d'être installés sur l'ordinateur de l'école.

```text
YELEN-NOTES\                        <-- Dossier programme (copiable sur tout PC Windows 64 bits)
├── YelenNotes.exe                  <-- Exécutable principal
├── Lancer_Monoposte.bat            <-- Raccourci démarrage sur 1 seul ordinateur
├── Lancer_Multiposte_Reseau.bat    <-- Raccourci démarrage serveur réseau local
├── Lancer_Demonstration.bat        <-- Démarrage sur la base fictive séparée (ecole_demo.db)
├── Autoriser_PareFeu_Reseau_Prive.bat
├── _internal\                      <-- Environnement Python 3.12 et bibliothèques embarquées
├── resources\                      <-- Templates HTML, Bootstrap 5 local, CSS, JS, polices TTF
├── migrations\                     <-- Scripts de migration de schéma Alembic
├── config\
│   └── yelen_notes.example.ini
├── README.txt
└── VERSION.txt
```

Les **données scolaires** sont stockées dans un dossier **totalement indépendant** du programme :
```text
C:\ProgramData\YelenNotes\          (ou %LOCALAPPDATA%\YelenNotes\ sans droits admin)
├── database\ecole.db               <-- Base SQLite locale (mode WAL activé)
├── backups\                        <-- Archives de sauvegarde vérifiées (.zip)
├── documents\bulletins\            <-- Bulletins PDF générés
├── documents\releves\              <-- Relevés et classements PDF générés
├── logos\                          <-- Logo de l'établissement
└── logs\application.log            <-- Journal technique
```

---

## 2. Déploiement en mode Monoposte (1 seul ordinateur)

1. Copiez le dossier `YELEN-NOTES` sur le disque dur de l'ordinateur (par exemple `C:\Applications\YELEN-NOTES\`).
2. Créez éventuellement un raccourci sur le Bureau vers `YelenNotes.exe` (ou `Lancer_Monoposte.bat`).
3. Double-cliquez sur `YelenNotes.exe` :
   - Le serveur local Waitress démarre sur `http://127.0.0.1:5000`.
   - Le navigateur par défaut (Edge, Chrome ou Firefox) s'ouvre automatiquement sur l'application.
4. À la fin du travail : déconnectez-vous dans le navigateur, puis fermez la fenêtre du serveur (ou appuyez sur `Ctrl+C`) pour un arrêt propre.

---

## 3. Déploiement en mode Multiposte (Réseau local de l'école sans Internet)

### 3.1. Principe d'architecture obligatoire
- **Un seul ordinateur** de l'école joue le rôle de **poste serveur** : il héberge `YELEN-NOTES` et le fichier `ecole.db` sur son disque dur local (SSD recommandé).
- Les autres ordinateurs (direction, secrétariat, enseignants) sont reliés au même routeur Wi-Fi ou switch Ethernet local. **Ils n'installent rien** et se connectent simplement avec leur navigateur web.
- **Règle absolue :** ne placez jamais `ecole.db` dans un dossier partagé Windows (`\\SERVEUR\...`), sur un NAS ou dans un dossier synchronisé (OneDrive, Dropbox). Tous les accès passent par HTTP via le serveur Waitress.

### 3.2. Configuration du poste serveur (une seule fois)
1. Connectez le poste serveur au routeur local de l'école (par câble Ethernet de préférence) et vérifiez dans Windows que le réseau est défini en profil **« Réseau privé »** (et non Public).
2. Recommandation : attribuez une adresse IP locale fixe au poste serveur (ex. `192.168.1.10`) dans les paramètres réseau ou dans le routeur Wi-Fi.
3. Faites un clic droit sur `Autoriser_PareFeu_Reseau_Prive.bat` → **Exécuter en tant qu'administrateur** afin d'autoriser le port TCP `5000` uniquement sur le réseau privé.
4. Pour rendre le mode multiposte permanent sans utiliser le fichier `.bat`, copiez `config\yelen_notes.example.ini` sous le nom `config\yelen_notes.ini` et remplacez `mode = monoposte` par `mode = multiposte`.

### 3.3. Utilisation quotidienne en multiposte
1. Sur le poste serveur, lancez `Lancer_Multiposte_Reseau.bat` (ou `YelenNotes.exe` si `yelen_notes.ini` est configuré en `multiposte`).
2. Repérez l'adresse affichée dans la console, par exemple : `http://192.168.1.10:5000`.
3. Sur chaque poste client du réseau local, ouvrez le navigateur et saisissez `http://192.168.1.10:5000`, puis connectez-vous avec votre identifiant et mot de passe personnels.

---

## 4. Compilation du dossier portable depuis le code source (Développeurs / Maintenance)

Sur un poste de compilation Windows 10/11 64 bits disposant de **Python 3.12 (64 bits)** :
```bat
packaging\build_windows_portable.bat
```
Ce script installe les versions exactes figées dans `requirements-build.txt`, compile l'application avec PyInstaller en mode `ONEDIR` et assemble le dossier prêt à distribuer dans `dist\YELEN-NOTES\`.
