' ============================================================================
' YELEN NOTES — Arret propre du serveur silencieux en arriere-plan
' ============================================================================
Option Explicit

Dim fso, sh, rootDir, exePath, pywPath
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

rootDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = rootDir

exePath = rootDir & "\YelenNotes.exe"
pywPath = rootDir & "\.venv\Scripts\pythonw.exe"

If fso.FileExists(exePath) Then
    sh.Run Chr(34) & exePath & Chr(34) & " --stop", 0, True
    sh.Run Chr(34) & exePath & Chr(34) & " --demo --stop", 0, True
ElseIf fso.FileExists(pywPath) Then
    sh.Run Chr(34) & pywPath & Chr(34) & " " & Chr(34) & rootDir & "\run.py" & Chr(34) & " --stop", 0, True
    sh.Run Chr(34) & pywPath & Chr(34) & " " & Chr(34) & rootDir & "\run.py" & Chr(34) & " --demo --stop", 0, True
End If
