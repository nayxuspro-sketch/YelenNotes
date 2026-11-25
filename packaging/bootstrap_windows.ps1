<#
.SYNOPSIS
    Installation, reparation et lancement/compilation 100 % automatiques de Yelen Notes (Windows 10/11 64 bits).
.DESCRIPTION
    Compatible Windows PowerShell 5.1 et PowerShell 7+.
    1. Detecte Python 3.12 (64 bits) ou l'installe automatiquement (via 'py install 3.12-64', 'winget' ou python.org),
       ou utilise un Python 3.11-3.14 (64 bits) deja present sur le poste.
    2. Cree ou repare automatiquement l'environnement virtuel (.venv ou .venv_build) et pip.
    3. Installe automatiquement toutes les dependances manquantes (et PyInstaller 6.22+ en mode build).
    4. Lance directement l'application (-Action run) ou genere dist\YELEN-NOTES\YelenNotes.exe (-Action build).
#>
param(
    [ValidateSet("run", "build")]
    [string]$Action = "build",
    [ValidateSet("monoposte", "multiposte")]
    [string]$Mode = "monoposte",
    [switch]$Demo,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $RootDir

function Write-Step([string]$Title) {
    Write-Host ""
    Write-Host "============================================================================" -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host "============================================================================" -ForegroundColor Cyan
}

function Invoke-SilentPythonCheck([string]$Exe, [string[]]$PrefixArgs = @()) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $probe = "import sys, struct; ok = (sys.version_info >= (3, 11) and struct.calcsize('P') == 8); print('YELEN_PY_OK:' + sys.executable if ok else ''); sys.exit(0 if ok else 1)"
        $cmdArgs = @() + $PrefixArgs + @("-c", $probe)
        $outLines = & $Exe @cmdArgs 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 0 -and $outLines) {
            foreach ($line in $outLines) {
                $s = $line.ToString().Trim()
                if ($s.StartsWith("YELEN_PY_OK:")) {
                    $pyPath = $s.Substring(12).Trim()
                    if ($pyPath -and (Test-Path -LiteralPath $pyPath)) {
                        return $pyPath
                    }
                }
            }
        }
    } catch {
        # Ignorer silencieusement et passer au candidat suivant
    } finally {
        $ErrorActionPreference = $prevEap
    }
    return $null
}

function Invoke-ExternalCommand([string]$Exe, [string[]]$CmdArgs) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @CmdArgs
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Find-Or-Install-Python {
    Write-Step "[1/4] Verification et installation automatique de Python 64 bits..."

    # 1. Verifier si le lanceur `py` est present
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $py312 = Invoke-SilentPythonCheck "py" @("-3.12-64")
        if ($py312) {
            Write-Host "  [OK] Python 3.12 (64 bits) deja installe : $py312" -ForegroundColor Green
            return $py312
        }

        # Tenter d'installer automatiquement Python 3.12-64 via Python Install Manager (`py install 3.12-64`)
        Write-Host "  [AUTO] Python 3.12-64 absent : installation automatique via 'py install 3.12-64'..." -ForegroundColor Yellow
        $pyInstallCode = Invoke-ExternalCommand "py" @("install", "3.12-64")
        if ($pyInstallCode -eq 0) {
            $py312After = Invoke-SilentPythonCheck "py" @("-3.12-64")
            if ($py312After) {
                Write-Host "  [OK] Python 3.12 (64 bits) installe automatiquement : $py312After" -ForegroundColor Green
                return $py312After
            }
        }

        # Sinon utiliser tout Python 64 bits compatible (3.11 a 3.14+) deja installe via `py`
        foreach ($tag in @("-3.13-64", "-3.14-64", "-3.11-64", "-3")) {
            $candidate = Invoke-SilentPythonCheck "py" @($tag)
            if ($candidate) {
                Write-Host "  [OK] Python 64 bits compatible detecte ($tag) : $candidate" -ForegroundColor Green
                return $candidate
            }
        }
    }

    # 2. Verifier la commande `python` dans le PATH
    if (Get-Command "python" -ErrorAction SilentlyContinue) {
        $pyInPath = Invoke-SilentPythonCheck "python"
        if ($pyInPath) {
            Write-Host "  [OK] Python 64 bits compatible detecte dans le PATH : $pyInPath" -ForegroundColor Green
            return $pyInPath
        }
    }

    # 3. Scanner les emplacements Windows standard (ex: AppData\Local\Programs\Python\Python314\python.exe)
    $knownLocations = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python314\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    foreach ($loc in $knownLocations) {
        if ($loc -and (Test-Path -LiteralPath $loc)) {
            $valid = Invoke-SilentPythonCheck $loc
            if ($valid) {
                Write-Host "  [OK] Python 64 bits detecte sur le disque : $valid" -ForegroundColor Green
                return $valid
            }
        }
    }

    # 4. Aucun Python compatible sur le poste : installation automatique via winget
    if (Get-Command "winget" -ErrorAction SilentlyContinue) {
        Write-Host "  [AUTO] Installation automatique de Python 3.12 (64 bits) via winget..." -ForegroundColor Yellow
        $wgCode = Invoke-ExternalCommand "winget" @(
            "install", "--exact", "--id", "Python.Python.3.12",
            "--architecture", "x64", "--scope", "user",
            "--accept-package-agreements", "--accept-source-agreements"
        )
        foreach ($loc in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
            if (Test-Path -LiteralPath $loc) {
                $valid = Invoke-SilentPythonCheck $loc
                if ($valid) {
                    Write-Host "  [OK] Python 3.12 installe via winget : $valid" -ForegroundColor Green
                    return $valid
                }
            }
        }
    }

    # 5. Dernier recours : telechargement direct de l'installeur officiel Python 3.12.9 (64 bits) depuis python.org
    $installerUrl = "https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe"
    $installerPath = Join-Path $env:TEMP "python-3.12.9-amd64.exe"
    Write-Host "  [AUTO] Telechargement automatique de Python 3.12.9 (64 bits) depuis python.org..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
    Write-Host "  [AUTO] Installation automatique de Python 3.12.9 en cours..." -ForegroundColor Yellow
    $proc = Start-Process -FilePath $installerPath -ArgumentList "/passive InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1 Include_venv=1" -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "L'installeur officiel Python a retourne le code d'erreur $($proc.ExitCode)."
    }

    $installedExe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $installedExe) {
        Write-Host "  [OK] Python 3.12.9 installe avec succes : $installedExe" -ForegroundColor Green
        return $installedExe
    }

    throw "Impossible d'installer automatiquement Python 64 bits. Verifiez que votre PC est connecte a Internet pour cette premiere preparation."
}

try {
    $BasePython = Find-Or-Install-Python

    $VenvName = if ($Action -eq "build") { ".venv_build" } else { ".venv" }
    $VenvDir = Join-Path $RootDir $VenvName
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"

    Write-Step "[2/4] Creation / verification de l'environnement virtuel ($VenvName)..."
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if (Test-Path -LiteralPath $VenvDir) {
            Write-Host "  [AUTO] Nettoyage d'un dossier $VenvName incomplet..." -ForegroundColor Yellow
            Remove-Item -LiteralPath $VenvDir -Recurse -Force
        }
        Write-Host "  [AUTO] Creation de l'environnement virtuel avec : $BasePython" -ForegroundColor Yellow
        $venvCode = Invoke-ExternalCommand $BasePython @("-m", "venv", $VenvDir)
        if ($venvCode -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
            if (Test-Path -LiteralPath $VenvDir) {
                Remove-Item -LiteralPath $VenvDir -Recurse -Force
            }
            $venvCode = Invoke-ExternalCommand $BasePython @("-m", "venv", "--without-pip", $VenvDir)
        }
        if ($venvCode -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
            throw "Echec de la creation de l'environnement virtuel dans $VenvDir."
        }
    }
    Write-Host "  [OK] Environnement virtuel pret : $VenvPython" -ForegroundColor Green

    # Verifier et installer automatiquement pip / wheel / setuptools (meme sur pythoncore sans ensurepip)
    $pipCheck = Invoke-ExternalCommand $VenvPython @("-m", "pip", "--version")
    if ($pipCheck -ne 0) {
        Write-Host "  [AUTO] Installation automatique de pip dans l'environnement virtuel..." -ForegroundColor Yellow
        $BootstrapPipScript = Join-Path $RootDir "packaging\ensure_pip_bootstrap.py"
        $epCode = Invoke-ExternalCommand $VenvPython @($BootstrapPipScript)
        if ($epCode -ne 0) {
            throw "Impossible d'initialiser pip dans l'environnement virtuel (verifiez votre connexion Internet)."
        }
    }

    Write-Step "[3/4] Installation automatique de toutes les bibliotheques manquantes..."
    $upCode = Invoke-ExternalCommand $VenvPython @("-m", "pip", "install", "--upgrade", "pip", "wheel")
    if ($upCode -ne 0) {
        Write-Host "  [INFO] Poursuite avec la version actuelle de pip..." -ForegroundColor DarkYellow
    }

    $ReqFile = if ($Action -eq "build") { "requirements-build.txt" } else { "requirements.txt" }
    $ReqPath = Join-Path $RootDir $ReqFile
    Write-Host "  [AUTO] Installation des dependances depuis $ReqFile..." -ForegroundColor Yellow
    $reqCode = Invoke-ExternalCommand $VenvPython @("-m", "pip", "install", "-r", $ReqPath)

    if ($reqCode -ne 0) {
        Write-Host "  [AUTO] Adaptation automatique des paquets compatibles pour votre version de Python..." -ForegroundColor Yellow
        $fallbackPackages = @(
            "Flask>=3.0,<4.0",
            "waitress>=3.0,<4.0",
            "Flask-SQLAlchemy>=3.1,<4.0",
            "Flask-Migrate>=4.0,<5.0",
            "Flask-Login>=0.6.3,<1.0",
            "Flask-WTF>=1.2,<2.0",
            "WTForms>=3.1,<4.0",
            "SQLAlchemy>=2.0.36,<3.0",
            "alembic>=1.14,<2.0",
            "openpyxl>=3.1.5,<4.0",
            "reportlab>=4.2,<6.0",
            "pillow>=11.0,<14.0",
            "pytest>=8.0,<10.0"
        )
        if ($Action -eq "build") {
            $fallbackPackages += "pyinstaller>=6.18,<7.0"
        }
        $fbArgs = @("-m", "pip", "install", "--upgrade") + $fallbackPackages
        $fbCode = Invoke-ExternalCommand $VenvPython $fbArgs
        if ($fbCode -ne 0) {
            throw "Echec de l'installation des bibliotheques Python. Verifiez votre connexion Internet."
        }
    }
    Write-Host "  [OK] Toutes les bibliotheques sont installees !" -ForegroundColor Green

    if ($Action -eq "build") {
        Write-Step "[4/4] Compilation du dossier portable autonome dist\YELEN-NOTES\..."
        $SpecPath = Join-Path $RootDir "packaging\yelen_notes.spec"
        $pyiCode = Invoke-ExternalCommand $VenvPython @("-m", "PyInstaller", "--noconfirm", "--clean", $SpecPath)
        if ($pyiCode -ne 0) {
            throw "La compilation PyInstaller a echoue (code $pyiCode)."
        }

        $DistTarget = Join-Path $RootDir "dist\YELEN-NOTES"
        $DistConfig = Join-Path $DistTarget "config"
        if (-not (Test-Path -LiteralPath $DistConfig)) {
            New-Item -ItemType Directory -Path $DistConfig -Force | Out-Null
        }

        if (-not (Test-Path -LiteralPath (Join-Path $DistTarget "resources"))) {
            Copy-Item -LiteralPath (Join-Path $RootDir "resources") -Destination (Join-Path $DistTarget "resources") -Recurse -Force
        }
        if (-not (Test-Path -LiteralPath (Join-Path $DistTarget "migrations"))) {
            Copy-Item -LiteralPath (Join-Path $RootDir "migrations") -Destination (Join-Path $DistTarget "migrations") -Recurse -Force
        }

        Copy-Item -LiteralPath (Join-Path $RootDir "config\yelen_notes.example.ini") -Destination (Join-Path $DistConfig "yelen_notes.example.ini") -Force
        Copy-Item -LiteralPath (Join-Path $RootDir "README.txt") -Destination (Join-Path $DistTarget "README.txt") -Force
        Copy-Item -LiteralPath (Join-Path $RootDir "VERSION.txt") -Destination (Join-Path $DistTarget "VERSION.txt") -Force
        Copy-Item -LiteralPath (Join-Path $RootDir "packaging\Lancer_Monoposte.bat") -Destination (Join-Path $DistTarget "Lancer_Monoposte.bat") -Force
        Copy-Item -LiteralPath (Join-Path $RootDir "packaging\Lancer_Multiposte_Reseau.bat") -Destination (Join-Path $DistTarget "Lancer_Multiposte_Reseau.bat") -Force
        Copy-Item -LiteralPath (Join-Path $RootDir "packaging\Lancer_Demonstration.bat") -Destination (Join-Path $DistTarget "Lancer_Demonstration.bat") -Force
        Copy-Item -LiteralPath (Join-Path $RootDir "packaging\Autoriser_PareFeu_Reseau_Prive.bat") -Destination (Join-Path $DistTarget "Autoriser_PareFeu_Reseau_Prive.bat") -Force

        $BuiltExe = Join-Path $DistTarget "YelenNotes.exe"
        if (-not (Test-Path -LiteralPath $BuiltExe)) {
            throw "L'executable compile est introuvable : $BuiltExe"
        }

        Write-Host ""
        Write-Host "============================================================================" -ForegroundColor Green
        Write-Host "  SUCCES TOTAL : Le dossier portable autonome est pret !" -ForegroundColor Green
        Write-Host "  Dossier     : $DistTarget" -ForegroundColor Green
        Write-Host "  Executable  : $BuiltExe" -ForegroundColor Green
        Write-Host "  Vous pouvez copier-coller ce dossier YELEN-NOTES sur cle USB et sur" -ForegroundColor Green
        Write-Host "  n'importe quel PC Windows 10/11 (64 bits) sans Python ni Internet." -ForegroundColor Green
        Write-Host "============================================================================" -ForegroundColor Green

        if (-not $NoPause) {
            Write-Host ""
            $ans = Read-Host "Voulez-vous lancer YelenNotes.exe tout de suite ? (O/N)"
            if ($ans -match "^[OoYy]") {
                Start-Process -FilePath $BuiltExe -WorkingDirectory $DistTarget
            }
        }
    }
    else {
        Write-Step "[4/4] Lancement de Yelen Notes (mode $Mode)..."
        $RunArgs = @((Join-Path $RootDir "run.py"), "--mode", $Mode)
        if ($Demo) {
            $RunArgs += "--demo"
        }
        $runCode = Invoke-ExternalCommand $VenvPython $RunArgs
        if ($runCode -ne 0 -and -not $NoPause) {
            Read-Host "Le serveur s'est arrete (code $runCode). Appuyez sur Entree pour fermer"
        }
    }
}
catch {
    Write-Host ""
    Write-Host "============================================================================" -ForegroundColor Red
    Write-Host "  ERREUR : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "============================================================================" -ForegroundColor Red
    if (-not $NoPause) {
        Read-Host "Appuyez sur Entree pour fermer cette fenetre"
    }
    exit 1
}
