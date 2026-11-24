Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fso.BuildPath(baseDir, "main.py")
sh.CurrentDirectory = baseDir
sh.Run "pythonw.exe """ & scriptPath & """", 0, False
