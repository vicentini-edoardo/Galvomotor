Dim CONDA_ENV
CONDA_ENV = "py38"

Dim sh, fso, repoDir, srcDir, base, python
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

repoDir = fso.GetParentFolderName(WScript.ScriptFullName)
srcDir  = repoDir & "\src"
base    = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.conda\envs\" & CONDA_ENV & "\"

If fso.FileExists(base & "pythonw.exe") Then
    python = base & "pythonw.exe"
Else
    python = base & "python.exe"
End If

Dim app
Set app = CreateObject("Shell.Application")
app.ShellExecute python, "-m galvo_gui", srcDir, "open", 1
