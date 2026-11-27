Option Explicit
Dim fso, sh, rootDir
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
rootDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = rootDir
sh.Run Chr(34) & rootDir & "\YelenNotes.exe" & Chr(34) & " --mode monoposte", 0, False
