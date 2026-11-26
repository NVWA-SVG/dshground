Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
exitCode = shell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & root & "\tools\start-teacher.ps1""", 0, True)
If exitCode <> 0 Then MsgBox "Teacher workbench could not start. Check " & root & "\data\windows-logs", 48, "Teacher Workbench"
