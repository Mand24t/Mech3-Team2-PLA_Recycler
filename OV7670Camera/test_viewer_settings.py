"""Saved calibration/ROI round trip and invalid-file handling."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest

import ov7670_viewer as viewer
from viewer_settings import load_settings, save_settings


class ViewerSettingsTests(unittest.TestCase):
    def test_geometry_correction_and_processing_restore_exactly(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = viewer.CameraViewer(root)
            app.sync_region_width(640)
            coords = [200, 300, 320, 420, 500, 630]
            for var, value in zip(app.region_coordinates, coords):
                var.set(value)
            app.geometry_inputs["filament_distance"].set("32.625")
            app.geometry_inputs["mirror_distance"].set("41.464")
            app.geometry_inputs["correction"].set("0.9919065")
            app.gradient_threshold_input.set("1")
            app.gradient_window_size_input.set("8")
            app.light_filament_mode.set(True)
            app.transparent_filament_mode.set(True)
            app.polynomial_order_input.set("5")
            expected = app.settings_snapshot()
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "settings.json"
                save_settings(expected, path)
                other = viewer.CameraViewer(root)
                other.restore_settings(load_settings(path))
                root.update_idletasks()
                self.assertEqual(other.settings_snapshot(), expected)
                self.assertFalse(path.with_suffix(".tmp").exists())
        finally:
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            root.destroy()

    def test_old_setups_default_to_normal_mode_and_invalid_flag_is_rejected(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = viewer.CameraViewer(root)
            old = app.settings_snapshot()
            old.pop("transparent_filament_mode")
            app.transparent_filament_mode.set(True)
            app.restore_settings(old)
            self.assertFalse(app.transparent_filament_mode.get())
            from viewer_settings import validate_settings
            old["transparent_filament_mode"] = "yes"
            with self.assertRaises(ValueError):
                validate_settings(old)
        finally:
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            root.destroy()

    def test_missing_and_corrupt_files_do_not_supply_calibration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            self.assertIsNone(load_settings(path))
            for text in ("{", '{"version":99}', '[]'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_settings(path)


if __name__ == "__main__":
    unittest.main()
