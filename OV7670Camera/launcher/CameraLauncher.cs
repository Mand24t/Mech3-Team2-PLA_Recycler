using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

internal static class CameraLauncher
{
    [STAThread]
    private static int Main()
    {
        string folder = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string python = Path.Combine(folder, @".venv-camera\Scripts\pythonw.exe");
        string viewer = Path.Combine(folder, @"OV7670Camera\ov7670_viewer.py");
        try
        {
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
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Camera Viewer Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
