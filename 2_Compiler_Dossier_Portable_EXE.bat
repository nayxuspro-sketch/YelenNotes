@echo off
REM ============================================================================
REM YELEN NOTES — 1 clic : Installe automatiquement ce qui manque et cree dist\YELEN-NOTES\YelenNotes.exe
REM ============================================================================
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\bootstrap_windows.ps1" -Action build
endlocal
