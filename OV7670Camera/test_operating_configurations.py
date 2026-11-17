"""Operating setup persistence and safe GUI restoration, without hardware."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest import mock

import Recycler_Python_Servo as recycler
from operating_configurations import OperatingConfigurationStore, validate_configuration


def camera_settings():
    return dict(version=1, frame_width=320, regions=[5, 83, 111, 215, 224, 320],
                geometry=dict(focal_length="3.37", sensor_width="2.304", lens_offset="3.4",
                              filament_distance="32.625", mirror_distance="41.464", correction="0.99"),
                threshold="8", gradient_window_size="10", light_filament_mode=True, transparent_filament_mode=False, polynomial_order="3",
                direct_view="Middle", true_diameter="1.75", port="COM6", baud="921600", illumination=123)


class OperatingConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.camera = mock.Mock()
        self.camera.snapshot.return_value = (dict(camera_settings=camera_settings()), "Viewer connected")
        with mock.patch.object(recycler.list_ports, "comports", return_value=[]):
            self.app = recycler.Recycler(self.root, camera=self.camera,
                spooling_settings_path=Path(self.directory.name)/"spool.json",
                configuration_path=Path(self.directory.name)/"configurations.json")

    def tearDown(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.directory.cleanup()

    def test_defaults_location_complete_save_and_load_leave_outputs_stopped(self):
        app = self.app
        self.assertEqual(app.automatic_vars["base"].get(), "60")
        self.assertEqual(app.state.snapshot()["automatic"].base, 60)
        siblings = app.system_diagnostics_panel.master.pack_slaves()
        self.assertLess(siblings.index(app.system_diagnostics_panel), siblings.index(app.operating_configuration_section))
        self.assertLess(siblings.index(app.operating_configuration_section), siblings.index(app.feedback_section))
        app.mode.set("Manual")
        app.mode_changed()
        app.spool_motor_mode.set("Automatic")
        app.spooling_mode.set("Automatic")
        app.port.set("COM7")
        app.automatic_vars["base"].set("80")
        app.apply_automatic_settings()
        app.speed_vars["cl"].set(60)
        app.speed_vars["dm"].set(8)
        app.speed_vars["spark"].set(35)
        app.speed_vars["fan"].set(80)
        app.reverse_vars["cl"].set(True)
        for key, entry_value in (("cl", "55"), ("dm", "8"), ("spark", "35"), ("fan", "80")):
            entry = app.motor_widgets[key][1]
            entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, entry_value)
        app.servo_vars["speed"].set("1.2")
        app.servo_vars["minimum"].set("-20")
        app.servo_vars["maximum"].set("40")
        app.servo_angle_input.set("12")
        app.servo_vars["start"].set("-20")
        app.spool_geometry_vars["core_diameter"].set("95")
        app.apply_spool_geometry()
        app.statistics_window_changed(30)
        app.statistics_window_input.set("40")
        app.state.set(cl=20, dm=4, fan=100, spark=20, arm=True, auto=True, spool_auto=True)
        with mock.patch.object(recycler.simpledialog, "askstring", return_value="PLA Blue"), \
             mock.patch.object(recycler.simpledialog, "askfloat", return_value=210):
            app.save_operating_configuration()
        self.assertEqual(app.state.snapshot()["cl"], 20)  # Saving does not stop running commands.
        profile = app.selected_configuration()
        self.assertEqual(profile["settings"]["motors"]["cl"]["setpoint"], 60)
        self.assertEqual(profile["settings"]["motors"]["cl"]["entry"], "55")
        self.assertEqual(profile["settings"]["camera"]["illumination"], 123)
        app.speed_vars["cl"].set(1)
        app.automatic_vars["base"].set("20")
        with mock.patch.object(recycler, "request_configuration", return_value="camera-request") as request, \
             mock.patch.object(recycler.messagebox, "showinfo") as info:
            app.load_operating_configuration()
        self.root.update_idletasks()
        self.assertEqual(app.speed_vars["cl"].get(), 60)
        self.assertEqual(app.motor_widgets["cl"][1].get(), "55")
        self.assertEqual(app.automatic_vars["base"].get(), "80")
        self.assertEqual(app.state.snapshot()["automatic"].base, 80)
        self.assertEqual(app.servo_vars["speed"].get(), "1.2")
        self.assertEqual(app.spool_geometry_vars["core_diameter"].get(), "95")
        self.assertEqual(app.recording_statistics.window, 30)
        self.assertEqual(app.statistics_window_input.get(), "40")
        self.assertEqual(app.mode.get(), "Manual")
        self.assertEqual(app.spool_motor_mode.get(), "Automatic")
        self.assertEqual(app.spooling_mode.get(), "Automatic")
        for key in ("cl", "dm", "spark", "fan"):
            self.assertEqual(app.state.snapshot()[key], 0)
        for key in ("auto", "spool_auto", "winding_auto", "arm"):
            self.assertFalse(app.state.snapshot()[key])
        self.assertIsNone(app.state.snapshot()["servo"])
        self.assertFalse(app.winding_ready)
        request.assert_called_once_with(profile["settings"]["camera"], "PLA Blue")
        self.assertEqual(info.call_args.args[1], "For this operating configuration, set temperature to: 210 C")

    def test_load_updates_open_camera_and_acknowledges_without_starting_hardware(self):
        import ov7670_viewer as viewer
        from OV7670Camera.viewer_settings import request_configuration
        app = self.app
        camera_app = viewer.CameraViewer(self.root)
        folder = Path(self.directory.name)
        request_path, result_path = folder/"camera_request.json", folder/"camera_result.json"
        camera_app.configuration_request_path = request_path
        camera_app.configuration_result_path = result_path
        camera_app.settings_path = folder/"viewer.json"
        profile = app.configuration_store.save("PLA Camera", 220, app.capture_operating_configuration())
        app.refresh_configurations(profile["id"])
        def request(settings, name):
            return request_configuration(settings, name, settings_path=camera_app.settings_path, request_path=request_path)
        with mock.patch.object(recycler, "request_configuration", side_effect=request), \
             mock.patch.object(recycler.messagebox, "showinfo"):
            app.load_operating_configuration()
        self.assertTrue(request_path.is_file())
        camera_app.configuration_checked_at = 0
        camera_app.check_operating_configuration()
        self.assertEqual(camera_app.settings_snapshot(), camera_settings())
        self.assertIsNone(camera_app.worker)
        self.assertTrue(json.loads(result_path.read_text())["ok"])
        with mock.patch.object(recycler, "CONFIGURATION_RESULT_PATH", result_path):
            app.check_camera_configuration_result()
        self.assertIsNone(app.camera_configuration_pending)
        self.assertIn("both interfaces", app.configuration_status.get())
        # A processed request must not override later manual edits or re-run on every poll.
        camera_app.gradient_threshold_input.set("25")
        camera_app.configuration_checked_at = 0
        camera_app.check_operating_configuration()
        self.assertEqual(camera_app.gradient_threshold_input.get(), "25")
        camera_app.measurement_publisher = mock.Mock()
        camera_app.publish_measurement()
        self.assertEqual(camera_app.measurement_publisher.publish.call_args.args[0]["camera_settings"]["threshold"], "25")

    def test_loading_changed_mega_port_disconnects_and_never_starts_outputs(self):
        app = self.app
        settings = app.capture_operating_configuration()
        settings["camera"] = None
        settings["mega_port"] = "COM8"
        settings["modes"]["puller"] = "Manual"
        settings["motors"]["cl"]["setpoint"] = 60
        profile = app.configuration_store.save("New port", 210, settings)
        app.refresh_configurations(profile["id"])
        app.connected = app.cl_ready = True
        app.port.set("COM7")
        app.worker = mock.Mock()
        app.state.set(cl=15, dm=5, fan=128, arm=True)
        with mock.patch.object(recycler.messagebox, "showinfo"):
            app.load_operating_configuration()
        app.worker.stop.set.assert_called_once()
        self.assertFalse(app.connected)
        self.assertEqual(app.port.get(), "COM8")
        self.assertEqual(app.speed_vars["cl"].get(), 60)
        for key in ("cl", "dm", "spark", "fan"):
            self.assertEqual(app.state.snapshot()[key], 0)
        self.assertFalse(app.state.snapshot()["arm"])
        app.worker = None

    def test_legacy_symmetric_profiles_migrate_and_asymmetric_values_round_trip(self):
        app = self.app
        settings = app.capture_operating_configuration()
        settings["servo_inputs"] = dict(speed="1", bounds="25", start="20")
        settings["servo_applied"] = dict(speed=1, bounds=25, start=20)
        settings.pop("servo_angle_input")
        upgraded = validate_configuration(settings)
        self.assertEqual(upgraded["servo_applied"], dict(speed=1, minimum=-25, maximum=25, start=20))
        self.assertEqual(float(upgraded["servo_angle_input"]), 20)
        self.assertEqual(upgraded["geometry_inputs"]["minimum_angle"], -25)
        app.apply_operating_configuration(upgraded)
        self.assertEqual(float(app.servo_vars["minimum"].get()), -25)
        app.servo_vars["minimum"].set("-15")
        app.servo_vars["maximum"].set("40")
        app.servo_angle_input.set("11")
        profile = app.configuration_store.save("Offset spool", 210, app.capture_operating_configuration())
        app.apply_operating_configuration(profile["settings"])
        self.assertEqual(app.servo_vars["maximum"].get(), "40")
        self.assertEqual(app.servo_angle_input.get(), "11")
        self.assertIsNone(app.state.snapshot()["servo"])

    def test_list_duplicate_rename_delete_and_reopen_persistence(self):
        app = self.app
        settings = app.capture_operating_configuration()
        original = app.configuration_store.save("PLA", 205, settings)
        app.refresh_configurations(original["id"])
        with mock.patch.object(recycler.simpledialog, "askstring", return_value="PLA Copy"):
            app.duplicate_operating_configuration()
        duplicate = app.selected_configuration()
        self.assertNotEqual(duplicate["id"], original["id"])
        self.assertEqual(duplicate["settings"], original["settings"])
        with mock.patch.object(recycler.simpledialog, "askstring", return_value="PLA Fast"):
            app.rename_operating_configuration()
        self.assertEqual(app.selected_configuration()["name"], "PLA Fast")
        with mock.patch.object(recycler.messagebox, "askyesno", return_value=True):
            app.delete_operating_configuration()
        reopened = OperatingConfigurationStore(app.configuration_store.path)
        self.assertEqual([p["name"] for p in reopened.list()], ["PLA"])
        self.assertEqual(reopened.get(original["id"])["temperature_c"], 205)
        self.assertFalse(app.configuration_store.path.with_suffix(".tmp").exists())

    def test_cancelled_save_and_invalid_settings_do_not_write(self):
        app = self.app
        with mock.patch.object(recycler.simpledialog, "askstring", return_value=None):
            app.save_operating_configuration()
        self.assertFalse(app.configuration_store.path.exists())
        invalid = app.capture_operating_configuration()
        invalid["motors"]["cl"]["setpoint"] = 101
        with self.assertRaises(ValueError):
            validate_configuration(invalid)
        original = app.configuration_store.save("PLA", 205, app.capture_operating_configuration())
        before = app.configuration_store.path.read_bytes()
        with self.assertRaises(ValueError):
            app.configuration_store.save("pla", 210, original["settings"])
        with self.assertRaises(ValueError):
            app.configuration_store.save("Bad", float("nan"), original["settings"])
        self.assertEqual(app.configuration_store.path.read_bytes(), before)
        app.configuration_store.path.write_text('{"version":1,"profiles":[{}]}')
        with self.assertRaises(ValueError):
            app.configuration_store.list()

    def test_replacing_existing_profile_keeps_id_and_changes_settings(self):
        store = self.app.configuration_store
        settings = self.app.capture_operating_configuration()
        original = store.save("PLA", 200, settings)
        settings["motors"]["dm"]["setpoint"] = 7
        changed = store.save("PLA", 215, settings, replace_id=original["id"])
        self.assertEqual(changed["id"], original["id"])
        self.assertEqual(len(store.list()), 1)
        self.assertEqual(store.get(original["id"])["temperature_c"], 215)
        self.assertEqual(store.get(original["id"])["settings"]["motors"]["dm"]["setpoint"], 7)


if __name__ == "__main__":
    unittest.main()
