@echo off
REM ============================================================================
REM YELEN NOTES — Compilation Windows 10/11 64 bits (Python 3.12 — PyInstaller ONEDIR)
REM Produit le dossier portable autonome : dist\YELEN-NOTES\
REM ============================================================================
setlocal
cd /d "%~dp0\.."

echo [1/4] Creation de l'environnement virtuel de compilation...
py -3.12-64 -m venv .venv_build
if errorlevel 1 (
    python -m venv .venv_build
)

echo [2/4] Installation des dependances figees...
call .venv_build\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-build.txt

echo [3/4] Compilation PyInstaller en mode ONEDIR...
pyinstaller --noconfirm --clean packaging\yelen_notes.spec

echo [4/4] Organisation finale du dossier portable dist\YELEN-NOTES\...
if not exist "dist\YELEN-NOTES\resources" xcopy /E /I /Y "resources" "dist\YELEN-NOTES\resources" >nul
if not exist "dist\YELEN-NOTES\migrations" xcopy /E /I /Y "migrations" "dist\YELEN-NOTES\migrations" >nul
if not exist "dist\YELEN-NOTES\config" mkdir "dist\YELEN-NOTES\config"
copy /Y "config\yelen_notes.example.ini" "dist\YELEN-NOTES\config\yelen_notes.example.ini" >nul
copy /Y "README.txt" "dist\YELEN-NOTES\README.txt" >nul
copy /Y "VERSION.txt" "dist\YELEN-NOTES\VERSION.txt" >nul
copy /Y "packaging\Lancer_Monoposte.bat" "dist\YELEN-NOTES\Lancer_Monoposte.bat" >nul
copy /Y "packaging\Lancer_Multiposte_Reseau.bat" "dist\YELEN-NOTES\Lancer_Multiposte_Reseau.bat" >nul
copy /Y "packaging\Lancer_Demonstration.bat" "dist\YELEN-NOTES\Lancer_Demonstration.bat" >nul
copy /Y "packaging\Autoriser_PareFeu_Reseau_Prive.bat" "dist\YELEN-NOTES\Autoriser_PareFeu_Reseau_Prive.bat" >nul

echo.
echo ============================================================================
echo SUCCES : Le dossier portable est pret dans dist\YELEN-NOTES\
echo Vous pouvez le copier-coller sur n'importe quel PC Windows 10/11 64 bits
echo sans installer Python et sans connexion Internet.
echo ============================================================================
endlocal
