# Camera GUI launcher

Double-click **Launch Camera.exe** in the project root. It uses its own folder
as the working directory and starts `.venv-camera\Scripts\pythonw.exe` with
`OV7670Camera\ov7670_viewer.py --connect`, without a console window.

Keep the EXE beside `.venv-camera` and `OV7670Camera`. This is a launcher using
the existing installation, not a bundled copy of Python and the application.
A Windows shortcut to the EXE can be placed elsewhere.

To rebuild from PowerShell in the project root:

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /platform:anycpu /optimize+ /reference:System.Windows.Forms.dll '/out:Launch Camera.exe' '.\OV7670Camera\launcher\CameraLauncher.cs'
```

The launcher always uses normal application defaults. Values entered during
previous GUI sessions are not saved automatically.
