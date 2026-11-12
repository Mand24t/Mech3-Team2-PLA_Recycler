using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

internal static class CameraLauncher
{
    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);

    private static bool FocusExistingViewer()
    {
        foreach (string name in new string[] { "pythonw", "python" })
        {
            foreach (Process candidate in Process.GetProcessesByName(name))
            {
                using (candidate)
                {
                    try
                    {
                        if (candidate.MainWindowTitle != "OV7670 | USB Camera Viewer") continue;
                        IntPtr window = candidate.MainWindowHandle;
                        if (window == IntPtr.Zero) continue;
                        ShowWindowAsync(window, 9); // Restore a minimized viewer.
                        SetForegroundWindow(window);
                        return true;
                    }
                    catch (InvalidOperationException) { } // Process exited while being inspected.
                    catch (System.ComponentModel.Win32Exception) { }
                }
            }
        }
        return false;
    }

    [STAThread]
    private static int Main()
    {
        string folder = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string python = Path.Combine(folder, @".venv-camera\Scripts\pythonw.exe");
        string viewer = Path.Combine(folder, @"OV7670Camera\ov7670_viewer.py");
        Mutex launchLock = new Mutex(false, @"Local\FilamentCameraLauncher");
        bool ownsLock = false;
        try
        {
            // Serialize rapid double-clicks until the Python window appears.
            try { ownsLock = launchLock.WaitOne(15000); }
            catch (AbandonedMutexException) { ownsLock = true; }
            if (!ownsLock) return 0;
            if (FocusExistingViewer()) return 0;
            if (!File.Exists(python))
                throw new FileNotFoundException("The camera Python environment was not found. Keep this launcher in the project folder beside .venv-camera and OV7670Camera.", python);
            if (!File.Exists(viewer))
                throw new FileNotFoundException("The camera viewer was not found. Keep the OV7670Camera folder beside this launcher.", viewer);

            Directory.SetCurrentDirectory(folder);
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = python;
            start.Arguments = "\"" + viewer + "\" --connect";
            start.WorkingDirectory = folder;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (Process process = Process.Start(start))
            {
                if (process == null)
                    throw new InvalidOperationException("Windows could not start the camera viewer.");
                for (int attempt = 0; attempt < 100; ++attempt)
                {
                    if (FocusExistingViewer() || process.HasExited) break;
                    Thread.Sleep(100);
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Camera Viewer Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            if (ownsLock) launchLock.ReleaseMutex();
            launchLock.Dispose();
        }
    }
}
