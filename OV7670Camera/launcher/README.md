# Camera GUI launcher

Double-click **Launch Camera.exe** in the project root. It uses its own folder
as the working directory and starts `.venv-camera\Scripts\pythonw.exe` with
`OV7670Camera\ov7670_viewer.py --connect`, without a console window. If the camera viewer is already open, the launcher
restores that window instead of opening a second copy. Rapid launcher clicks
are serialized to avoid competing starts.

Keep the EXE beside `.venv-camera` and `OV7670Camera`. This is a launcher using
the existing installation, not a bundled copy of Python and the application.
A Windows shortcut to the EXE can be placed elsewhere.

To rebuild from PowerShell in the project root:

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /platform:anycpu /optimize+ /reference:System.Windows.Forms.dll '/out:Launch Camera.exe' '.\OV7670Camera\launcher\CameraLauncher.cs'
```

The launcher always uses normal application defaults. Values entered during
previous GUI sessions are not saved automatically.


Windows opens each serial port exclusively. If another viewer or a Serial
Monitor owns the Nano port, a second connection may report Access is denied.
Use the already-open viewer or close the other port owner first. Two Python
processes for one viewer can be normal: the virtual-environment launcher and
its actual Python process. The EXE prevents duplicate launches through this
launcher; starting the Python script directly still creates a separate instance.
# Combined launcher

`Launch.exe` at the project root opens both GUIs using the existing virtual
environment and project files. Its source is `ProjectLauncher.cs`. It shares the
camera launch mutex with `Launch Camera.exe`, and uses a separate recycler mutex
so rapid clicks restore windows rather than starting duplicate GUI processes.
The camera attempts USB detection; the recycler waits for explicit connection
and motor-start actions. Existing windows retain their current state. Launch.exe tiles the recycler on
the left and camera viewer on the right, each filling half the available work
area on the monitor containing the mouse pointer. The taskbar remains accessible.
Launching again restores and repositions existing windows without duplicating them.

Rebuild from the project root in PowerShell:

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /platform:anycpu /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /win32icon:assets\app_icon.ico /out:Launch.exe '.\OV7670Camera\launcher\ProjectLauncher.cs'
```


The combined launcher embeds `assets/app_icon.ico`. Both Python interfaces use
the same icon for their windows and taskbar buttons. Keep the `assets` folder
with the project; the original image is stored there unchanged.

To regenerate the ICO from the original PNG, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\OV7670Camera\launcher\build_icon.ps1
```

Then rebuild Launch.exe with the command above. The icon stores lossless RGBA
PNG frames at 14 sizes, avoiding the damaged transparency produced by the older
GDI icon conversion and covering common Windows display scales.

Both Python interfaces opt into per-monitor DPI awareness before creating Tk
windows, matching the launcher. The launcher uses physical monitor work-area
coordinates and rechecks placement after startup geometry settles. Minimum window
sizes adapt to smaller displays. After updating this code, fully close both old
interfaces once before launching again; existing processes retain their old DPI
mode until restarted. The current application icon remains embedded.
