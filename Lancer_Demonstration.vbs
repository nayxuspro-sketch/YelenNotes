' ============================================================================
' YELEN NOTES — Lancement silencieux sans console (Base de Demonstration)
' ============================================================================
Option Explicit

Dim fso, sh, rootDir, exePath, pywPath, psCmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

rootDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = rootDir

exePath = rootDir & "\YelenNotes.exe"
pywPath = rootDir & "\.venv\Scripts\pythonw.exe"

If fso.FileExists(exePath) Then
    sh.Run Chr(34) & exePath & Chr(34) & " --mode monoposte --demo", 0, False
ElseIf fso.FileExists(pywPath) Then
    sh.Run Chr(34) & pywPath & Chr(34) & " " & Chr(34) & rootDir & "\run.py" & Chr(34) & " --mode monoposte --demo", 0, False
Else
    psCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & _
            Chr(34) & rootDir & "\packaging\bootstrap_windows.ps1" & Chr(34) & " -Action run -Mode monoposte -Demo -NoPause"
    sh.Run psCmd, 0, False
End If
