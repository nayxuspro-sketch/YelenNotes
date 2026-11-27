@echo off
REM ============================================================================
REM OVICLOUD NOTES — Compilation automatique Windows 10/11 64 bits (PyInstaller ONEDIR)
REM Installe automatiquement Python 64 bits et toutes les dependances manquantes,
REM puis genere le dossier portable autonome : dist\YELEN-NOTES\
REM ============================================================================
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap_windows.ps1" -Action build
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
