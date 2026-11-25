@echo off
REM ============================================================================
REM YELEN NOTES — 1 clic : Installe automatiquement ce qui manque et lance l'application
REM ============================================================================
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\bootstrap_windows.ps1" -Action run -Mode monoposte
endlocal
