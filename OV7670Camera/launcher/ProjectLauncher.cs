using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

internal static class ProjectLauncher
{
    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);

    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out ScreenPoint point);
    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromPoint(ScreenPoint point, uint flags);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetWindowPos(IntPtr window, IntPtr insertAfter,
                                           int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr window, out WindowRect rect);
    [DllImport("dwmapi.dll")]
    private static extern int DwmGetWindowAttribute(IntPtr window, int attribute,
                                                   out WindowRect rect, int size);
    [StructLayout(LayoutKind.Sequential)]
    private struct WindowRect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ScreenPoint { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    private struct MonitorInfo { public int Size; public WindowRect Monitor, Work; public uint Flags; }

    private static System.Drawing.Rectangle WorkArea()
    {
        ScreenPoint cursor;
        MonitorInfo info = new MonitorInfo();
        info.Size = Marshal.SizeOf(typeof(MonitorInfo));
        if (!GetCursorPos(out cursor) || !GetMonitorInfo(MonitorFromPoint(cursor, 2), ref info))
            throw new InvalidOperationException("Could not read the monitor's available screen area.");
        return System.Drawing.Rectangle.FromLTRB(info.Work.Left, info.Work.Top, info.Work.Right, info.Work.Bottom);
    }

    private static bool Fits(IntPtr window, System.Drawing.Rectangle target)
    {
        WindowRect visible;
        if (DwmGetWindowAttribute(window, 9, out visible, Marshal.SizeOf(typeof(WindowRect))) != 0)
            return false;
        return Math.Abs(visible.Left-target.Left) <= 2 && Math.Abs(visible.Top-target.Top) <= 2 &&
               Math.Abs(visible.Right-target.Right) <= 2 && Math.Abs(visible.Bottom-target.Bottom) <= 2;
    }

    private static void PlaceWindow(IntPtr window, System.Drawing.Rectangle target)
    {
        ShowWindow(window, 9); // Restore a minimized/maximized window before placing it.
        int x = target.Left, y = target.Top, width = target.Width, height = target.Height;
        WindowRect outer, visible;
        // Account for Windows' invisible resize borders so visible frames meet.
        if (GetWindowRect(window, out outer) &&
            DwmGetWindowAttribute(window, 9, out visible, Marshal.SizeOf(typeof(WindowRect))) == 0)
        {
            x -= visible.Left - outer.Left;
            y -= visible.Top - outer.Top;
            width += (visible.Left - outer.Left) + (outer.Right - visible.Right);
            height += (visible.Top - outer.Top) + (outer.Bottom - visible.Bottom);
        }
        if (!SetWindowPos(window, IntPtr.Zero, x, y, width, height, 0x0004 | 0x0010))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    }

    private static IntPtr FocusWindow(string title)
    {
        foreach (string name in new string[] { "pythonw", "python" })
        {
            foreach (Process candidate in Process.GetProcessesByName(name))
            {
                using (candidate)
                {
                    try
                    {
                        if (candidate.MainWindowTitle != title) continue;
                        IntPtr window = candidate.MainWindowHandle;
                        if (window == IntPtr.Zero) continue;
                        ShowWindow(window, 9);
                        SetForegroundWindow(window);
                        return window;
                    }
                    catch (InvalidOperationException) { }
                    catch (System.ComponentModel.Win32Exception) { }
                }
            }
        }
        return IntPtr.Zero;
    }

    private static IntPtr EnsureWindow(string folder, string python, string script,
                                     string arguments, string title, string mutexName)
    {
        using (Mutex launchLock = new Mutex(false, mutexName))
        {
            bool ownsLock = false;
            try
            {
                try { ownsLock = launchLock.WaitOne(20000); }
                catch (AbandonedMutexException) { ownsLock = true; }
                if (!ownsLock)
                    throw new InvalidOperationException("Another launcher is still opening " + title + ". Try again shortly.");
                IntPtr existing = FocusWindow(title);
                if (existing != IntPtr.Zero) return existing;
                ProcessStartInfo start = new ProcessStartInfo();
                start.FileName = python;
                start.Arguments = "\"" + Path.Combine(folder, script) + "\"" + arguments;
                start.WorkingDirectory = folder;
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                using (Process process = Process.Start(start))
                {
                    if (process == null)
                        throw new InvalidOperationException("Windows could not start " + title + ".");
                    for (int attempt = 0; attempt < 150; ++attempt)
                    {
                        IntPtr opened = FocusWindow(title);
                        if (opened != IntPtr.Zero) return opened;
                        if (process.HasExited)
                            throw new InvalidOperationException(title + " exited during startup. Run its Python file from PowerShell to see the error.");
                        Thread.Sleep(100);
                    }
                    throw new InvalidOperationException("Timed out waiting for " + title + ". It may still be starting; try again shortly.");
                }
            }
            finally
            {
                if (ownsLock) launchLock.ReleaseMutex();
            }
        }
    }

    [STAThread]
    private static int Main()
    {
        try
        {
            try
            {
                if (!SetProcessDpiAwarenessContext(new IntPtr(-4))) SetProcessDPIAware();
            }
            catch (EntryPointNotFoundException) { SetProcessDPIAware(); }
            // Keep monitor bounds, GetWindowRect and DWM in the same physical
            // coordinate system even when the Python windows use DPI scaling.
            SetThreadDpiAwarenessContext(new IntPtr(-4));
            System.Drawing.Rectangle workArea = WorkArea();
            string folder = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string python = Path.Combine(folder, @".venv-camera\Scripts\pythonw.exe");
            foreach (string required in new string[] { python,
                Path.Combine(folder, @"OV7670Camera\ov7670_viewer.py"),
                Path.Combine(folder, @"OV7670Camera\measurement_link.py"),
                Path.Combine(folder, @"OV7670Camera\viewer_settings.py"),
                Path.Combine(folder, "Recycler_Python_Servo.py"),
                Path.Combine(folder, "recycler_logic.py") })
            {
                if (!File.Exists(required))
                    throw new FileNotFoundException("Required file missing: " + required
                        + "\nKeep Launch.exe in the project folder beside .venv-camera and OV7670Camera.");
            }
            Directory.SetCurrentDirectory(folder);
            // Share the camera-only launcher's mutex to prevent competing launches.
            IntPtr camera = EnsureWindow(folder, python, @"OV7670Camera\ov7670_viewer.py", " --connect",
                         "OV7670 | USB Camera Viewer", @"Local\FilamentCameraLauncher");
            IntPtr recycler = EnsureWindow(folder, python, "Recycler_Python_Servo.py", "",
                         "PLA Recycler | Manual / Automatic", @"Local\FilamentRecyclerLauncher");
            int leftWidth = workArea.Width / 2;
            System.Drawing.Rectangle left = new System.Drawing.Rectangle(workArea.Left, workArea.Top,
                                                                         leftWidth, workArea.Height);
            System.Drawing.Rectangle right = new System.Drawing.Rectangle(workArea.Left + leftWidth, workArea.Top,
                                                                          workArea.Width-leftWidth, workArea.Height);
            // A Tk title can exist before its initial geometry has settled.
            // Recheck after resize messages, instead of trusting one early call.
            Thread.Sleep(300);
            int stable = 0;
            for (int attempt = 0; attempt < 15 && stable < 3; ++attempt)
            {
                if (!Fits(recycler, left)) PlaceWindow(recycler, left);
                if (!Fits(camera, right)) PlaceWindow(camera, right);
                Thread.Sleep(150);
                stable = Fits(recycler, left) && Fits(camera, right) ? stable+1 : 0;
            }
            if (!Fits(recycler, left) || !Fits(camera, right))
                throw new InvalidOperationException("A window could not fit its half of the screen. Close both interfaces and relaunch.");
            SetForegroundWindow(recycler);
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Filament Recycler Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
