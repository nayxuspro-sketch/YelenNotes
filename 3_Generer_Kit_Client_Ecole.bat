@echo off
REM ============================================================================
REM YELEN NOTES — Version de deploiement Super Admin
REM Exporte une archive ZIP Version Client Ecole (sans cle privee de signature)
REM ============================================================================
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py --export-client-kit "dist\YelenNotes-Client-Ecole.zip"
) else (
    python run.py --export-client-kit "dist\YelenNotes-Client-Ecole.zip"
)
echo.
pause
endlocal
