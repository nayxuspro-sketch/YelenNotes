@echo off
REM ============================================================================
REM Autorise le port TCP 5000 de Yelen Notes UNIQUEMENT sur le profil reseau Prive
REM (Doit etre execute une seule fois en tant qu'Administrateur Windows sur le serveur)
REM ============================================================================
netsh advfirewall firewall delete rule name="Yelen Notes (Reseau Local Prive)" >nul 2>&1
netsh advfirewall firewall add rule name="Yelen Notes (Reseau Local Prive)" dir=in action=allow protocol=TCP localport=5000 profile=private
echo Regle de pare-feu Windows creee sur le profil Prive (TCP 5000).
pause
