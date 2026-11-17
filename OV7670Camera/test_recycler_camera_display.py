"""Recycler GUI integration without opening either hardware USB port."""
import time
import csv
import tempfile
import tkinter as tk
from tkinter import font as tkfont
import unittest
from unittest import mock
import Recycler_Python_Servo as recycler
from diameter_recording import DiameterRecorder


class RecyclerCameraDisplayTests(unittest.TestCase):
    def setUp(self):
        self.save_dialog_patch = mock.patch.object(recycler.filedialog, "asksaveasfilename", return_value="")
        self.save_dialog = self.save_dialog_patch.start()
        self.addCleanup(self.save_dialog_patch.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.camera = mock.Mock()
        now = time.monotonic()
        self.packet = dict(protocol=1, valid=True, diameter_mm=1.8765,
                           frame_received_at=now, published_at=now, frame_sequence=123)
        self.camera.snapshot.return_value = (self.packet, "Camera viewer connected")
        with mock.patch.object(recycler.list_ports, "comports", return_value=[]):
            self.app = recycler.Recycler(self.root, camera=self.camera)

    def tearDown(self):
        self.app.recorder.stop()
        if self.app.recorder.thread is not None:
            self.app.recorder.thread.join(timeout=2)
        if self.app.recording_exporter.thread is not None:
            self.app.recording_exporter.thread.join(timeout=2)
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()

    def test_record_buttons_save_the_displayed_measurement_without_starting_outputs(self):
        app = self.app
        with tempfile.TemporaryDirectory() as directory:
            app.recorder = DiameterRecorder(directory)
            app.start_recording_button.invoke()
            self.assertEqual(str(app.start_recording_button["state"]), "disabled")
            self.assertEqual(str(app.stop_recording_button["state"]), "normal")
            app.poll()
            app.poll()  # No duplicate row from repeated GUI polling.
            app.stop_recording_button.invoke()
            app.recorder.thread.join(timeout=2)
            app.finish_recording_save()
            app.poll_recording()
            self.assertEqual(str(app.start_recording_button["state"]), "normal")
            self.assertEqual(str(app.stop_recording_button["state"]), "disabled")
            self.assertIn("Saved |", app.recording_text.get())
            self.assertIn("Points saved: 1", app.recording_progress_text.get())
            with app.recorder.path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["mean_diameter_mm"], app.camera_diameter_text.get())
            self.assertFalse(app.state.snapshot()["auto"])
            self.assertTrue(all(app.state.snapshot()[key] == 0 for key in ("cl", "dm", "spark", "fan")))

    def test_same_diameter_is_available_to_gui_and_calculations(self):
        self.app.poll()
        self.assertEqual(recycler.camera_diameter_mm, 1.8765)
        self.assertEqual(self.app.camera_diameter_text.get(), "1.8765")
        self.assertEqual(recycler.camera_frame_sequence, 123)
        self.packet["frame_received_at"] -= 3
        self.app.poll()
        self.assertIsNone(recycler.camera_diameter_mm)
        self.assertEqual(self.app.camera_diameter_text.get(), "N/A")

    def reported_fields(self, active=False):
        return dict(CL="3" if active else "0", DM="4" if active else "0",
                    SPARK="25" if active else "0", FAN="128" if active else "0",
                    SERVO="1" if active else "0", CL_READY="1", ARM="1" if active else "0",
                    OSC_V="1", N="0", THETA="0", OMEGA="15" if active else "0",
                    TARED="0", OSC_AUTO="0")

    def test_control_titles_and_status_buttons_do_not_operate_motors(self):
        titles = dict(cl="Diameter Control Puller Motor", dm="Spooling Motor", spark="Extrusion Motor",
                      fan="Cooling Fans", servo="Spooling Traverser Servo")
        before = self.app.state.snapshot()
        for key, title in titles.items():
            self.assertEqual(self.app.control_titles[key].cget("text"), title)
            self.assertEqual(tkfont.Font(font=self.app.control_titles[key].cget("font")).actual("weight"), "bold")
            self.assertEqual(self.app.control_indicators[key].cget("foreground"), "#b91c1c")
            self.app.control_indicators[key].invoke()
            self.assertIn(title, self.app.response_text.get())
        self.assertEqual(self.app.state.snapshot(), before)

    def test_acknowledged_running_holding_and_stopped_indicators(self):
        app = self.app
        app.state.set(cl=3, dm=4, spark=25, fan=128, servo=(0, 15, 30, 0))
        fields = self.reported_fields(True)
        app.events.put(("connected", fields))
        app.poll()
        self.assertTrue(all(state == "running" for state in app.control_indicator_states.values()))
        self.assertTrue(all(button.cget("foreground") == "#16a34a" for button in app.control_indicators.values()))
        fields = fields | {"OMEGA": "0"}
        app.state.set(servo=(0, 15, 0, 0))
        app.events.put(("status", fields))
        app.poll()
        self.assertEqual(app.control_indicator_text["servo"].get(), "Holding")
        app.stop_all()
        self.assertTrue(all(button.cget("foreground") == "#b91c1c" for button in app.control_indicators.values()))
        app.events.put(("status", self.reported_fields()))
        app.poll()
        self.assertTrue(all(text.get() == "Stopped" for text in app.control_indicator_text.values()))

    def test_link_failure_flashes_all_indicators_until_disconnect_or_reconnection(self):
        app = self.app
        app.events.put(("connected", self.reported_fields()))
        app.poll()
        app.events.put(("error", "USB connection lost"))
        app.events.put(("disconnected", None))
        app.poll()
        self.assertTrue(all(state == "fault" for state in app.control_indicator_states.values()))
        app.update_control_indicators(now=100)
        first = [button.cget("foreground") for button in app.control_indicators.values()]
        app.update_control_indicators(now=100.5)
        second = [button.cget("foreground") for button in app.control_indicators.values()]
        self.assertEqual(first, ["#b91c1c"] * 5)
        self.assertEqual(second, ["#d1d5db"] * 5)
        app.disconnect()
        self.assertTrue(all(state == "stopped" for state in app.control_indicator_states.values()))
        app.events.put(("connected", self.reported_fields()))
        app.poll()
        self.assertIsNone(app.link_failure)

    def test_stale_status_and_control_specific_failures_are_distinguished(self):
        app = self.app
        app.events.put(("connected", self.reported_fields()))
        app.poll()
        app.last_mega_status_at = 100
        app.update_control_indicators(now=103)
        self.assertTrue(all(state == "fault" for state in app.control_indicator_states.values()))
        app.events.put(("status", self.reported_fields()))
        app.events.put(("auto_fault", "Camera reading stale"))
        app.poll()
        self.assertEqual(app.control_indicator_states["cl"], "fault")
        self.assertTrue(all(state == "stopped" for key, state in app.control_indicator_states.items() if key != "cl"))
        app.stop_all()  # Operator acknowledges the component fault.
        app.events.put(("winding_fault", "Traverser command unavailable"))
        app.poll()
        self.assertEqual(app.control_indicator_states["servo"], "fault")
        self.assertEqual(app.control_indicator_states["cl"], "stopped")

    def test_filter_display_counts_frames_and_deadband_changes_apply_without_clearing_history(self):
        app = self.app
        self.assertEqual(app.state.snapshot()["automatic"].deadband, .1)
        self.assertEqual(app.automatic_vars["deadband"].get(), "0.1")
        app.automatic_vars["deadband"].set("0.02")
        app.apply_automatic_settings()
        stamp = time.monotonic() - .19
        for seq in range(20):
            p = dict(protocol=1, valid=True, diameter_mm=1.8, frame_sequence=seq,
                     frame_received_at=stamp + seq * .01, published_at=stamp + seq * .01)
            self.camera.snapshot.return_value = (p, "Live")
            app.poll()
        self.assertIn("20 readings", app.filtered_diameter_text.get())
        self.assertIn("1.8000", app.filtered_diameter_text.get())
        self.assertIn("+0.0300", app.filtered_diameter_text.get())
        app.automatic_vars["deadband"].set("0.1")
        self.assertTrue(app.apply_automatic_settings())
        app.poll()
        self.assertIn("no correction", app.filtered_diameter_text.get())
        self.assertEqual(app.diameter_filter.snapshot()[1], 20)
        self.assertFalse(app.state.snapshot()["auto"])

    def test_mode_selection_does_not_start_motors_and_manual_other_outputs_remain(self):
        app = self.app
        app.mode.set("Manual")
        app.mode_changed()
        app.connected = app.cl_ready = True
        app.speed_vars["dm"].set(4)
        app.start_motor("dm")
        app.speed_vars["cl"].set(3)
        app.start_motor("cl")
        app.mode.set("Automatic")
        app.mode_changed()
        self.assertFalse(app.state.snapshot()["auto"])
        self.assertEqual(app.state.snapshot()["cl"], 0)
        self.assertEqual(app.state.snapshot()["dm"], 4)
        app.start_motor("cl")
        self.assertEqual(app.state.snapshot()["cl"], 0)
        app.oscillator_supported = app.oscillator_tared = True
        app.start_automatic()
        self.assertTrue(app.state.snapshot()["auto"])
        app.mode.set("Manual")
        app.mode_changed()
        self.assertFalse(app.state.snapshot()["auto"])
        self.assertEqual(app.state.snapshot()["cl"], 0)
        self.assertEqual(app.state.snapshot()["dm"], 4)

    def test_auto_rejects_missing_camera_and_bad_settings(self):
        app = self.app
        app.connected = app.cl_ready = True
        app.mode.set("Automatic")
        self.camera.snapshot.return_value = (None, "Disconnected")
        app.start_automatic()
        self.assertFalse(app.state.snapshot()["auto"])
        self.camera.snapshot.return_value = (self.packet, "Live")
        app.oscillator_supported = app.oscillator_tared = True
        app.automatic_vars["maximum"].set("101")
        app.start_automatic()
        self.assertFalse(app.state.snapshot()["auto"])

    def test_fan_mapping_extruder_arming_and_servo_limits(self):
        app = self.app
        app.connected = app.cl_ready = True
        app.speed_vars["fan"].set(100)
        app.start_motor("fan")
        self.assertEqual(app.state.snapshot()["fan"], 255)
        app.speed_vars["spark"].set(30)
        app.start_motor("spark")
        self.assertTrue(app.state.snapshot()["arm"])
        self.assertEqual(app.state.snapshot()["spark"], 30)
        app.servo_vars["maximum"].set("95")
        app.set_servo()
        self.assertIsNone(app.state.snapshot()["servo"])
        app.servo_vars["maximum"].set("31")
        app.set_servo()
        self.assertIsNone(app.state.snapshot()["servo"])
        app.start_servo()
        self.assertEqual(app.state.snapshot()["servo"], (0, .25, 31, 31))
        app.events.put(("auto_fault", "Camera stale"))
        app.poll()
        for key in ("cl", "dm", "spark", "fan"):
            self.assertEqual(app.state.snapshot()[key], 0)
        self.assertIn("Restart required", app.auto_status.get())
        self.assertFalse(app.state.snapshot()["auto"])

    def test_manual_start_angle_is_validated_and_speed_update_keeps_restart_revision(self):
        app = self.app
        app.connected = True
        app.servo_vars["minimum"].set("-30")
        app.servo_vars["maximum"].set("30")
        app.servo_vars["start"].set("30")
        app.set_servo()
        app.start_servo()
        first = app.state.snapshot()
        self.assertEqual(first["servo"], (0, .25, 30, 30))
        app.servo_vars["speed"].set("25")
        app.set_servo()
        self.assertEqual(app.state.snapshot()["servo_revision"], first["servo_revision"])
        app.set_servo()
        self.assertEqual(app.state.snapshot()["servo_revision"], first["servo_revision"])
        before_invalid = app.state.snapshot()["servo"]
        app.servo_vars["start"].set("31")
        app.set_servo()
        self.assertEqual(app.state.snapshot()["servo"], before_invalid)
        app.pause_supported = app.angle_supported = True
        app.set_servo(hold=True)
        self.assertEqual(app.state.snapshot()["servo_hold"], "centered")
        self.assertEqual(app.state.snapshot()["servo"], before_invalid)

    def test_spooling_modes_are_independent_and_layer_reset_requires_stopped_spooler(self):
        app = self.app
        app.connected = app.cl_ready = app.winding_supported = True
        app.state.set(cl=3, dm=4, fan=128)
        app.spooling_mode_buttons["Automatic"].invoke()
        self.assertIsInstance(app.spooling_mode_buttons["Automatic"], recycler.ttk.Radiobutton)
        self.assertTrue(app.spooling_mode_buttons["Automatic"].instate(["selected"]))
        self.assertFalse(app.spooling_mode_buttons["Manual"].instate(["selected"]))
        self.assertEqual(app.state.snapshot()["cl"], 3)
        self.assertEqual(app.state.snapshot()["dm"], 4)
        app.tare_n()
        self.assertEqual(app.state.snapshot()["winding_tare_request"], 0)
        app.state.set(dm=0)
        app.tare_n()
        self.assertEqual(app.state.snapshot()["winding_tare_request"], 1)
        fields = dict(CL="3", DM="0", FAN="128", SPARK="0", ARM="0", SERVO="1",
                      CL_READY="1", OSC_V="1", N="0", THETA="-31", OMEGA="0", TARED="0", OSC_AUTO="0",
                      WIND_V="1", LAYERS="0", RADIUS="43.5", WIND_READY="1", WIND_ACTIVE="0")
        app.events.put(("winding_tared", fields))
        app.poll()
        self.assertTrue(app.winding_ready)
        self.assertIn("Layer: 0", app.oscillator_text.get())
        app.set_servo()
        self.assertFalse(app.state.snapshot()["winding_auto"])
        app.start_servo()
        self.assertTrue(app.state.snapshot()["winding_auto"])
        self.assertFalse(app.state.snapshot()["auto"])
        self.assertEqual(app.state.snapshot()["cl"], 3)
        app.mode.set("Automatic")
        app.mode_changed()
        self.assertTrue(app.state.snapshot()["winding_auto"])
        app.start_automatic()
        self.assertTrue(app.state.snapshot()["auto"])
        app.spooling_mode_buttons["Manual"].invoke()
        self.assertTrue(app.spooling_mode_buttons["Manual"].instate(["selected"]))
        self.assertFalse(app.spooling_mode_buttons["Automatic"].instate(["selected"]))
        self.assertFalse(app.state.snapshot()["winding_auto"])
        self.assertTrue(app.state.snapshot()["auto"])
        self.assertEqual(app.state.snapshot()["fan"], 128)

    def test_asymmetric_limits_set_angle_paused_running_and_midpoint_hold(self):
        app = self.app
        app.connected = app.pause_supported = app.angle_supported = True
        self.assertNotIn("bounds", app.servo_vars)
        app.servo_vars["minimum"].set("-20")
        app.servo_vars["maximum"].set("40")
        app.servo_vars["start"].set("35")
        app.set_servo()
        self.assertEqual(app.manual_sweep_settings, (10, .25, 30, 35))
        app.start_servo()
        revision = app.state.snapshot()["servo_revision"]
        app.servo_angle_input.set("12")
        app.set_servo_angle()
        state = app.state.snapshot()
        self.assertEqual(state["servo_angle_target"], 12)
        self.assertIsNone(state["servo_hold"])
        self.assertEqual(state["servo_revision"], revision)
        app.pause_spooling()
        app.servo_angle_input.set("-15")
        app.set_servo_angle()
        self.assertEqual(app.state.snapshot()["servo_hold"], "paused")
        self.assertEqual(app.state.snapshot()["servo_angle_target"], -15)
        app.pause_spooling()
        self.assertIsNone(app.state.snapshot()["servo_hold"])
        app.center_spooling()
        self.assertEqual(app.state.snapshot()["servo_angle_target"], 10)
        app.servo_angle_input.set("50")
        previous = app.state.snapshot()["servo_angle_request"]
        app.set_servo_angle()
        self.assertEqual(app.state.snapshot()["servo_angle_request"], previous)
        app.detach_spooling()
        app.servo_angle_input.set("25")
        app.set_servo_angle()
        self.assertEqual(app.state.snapshot()["servo_hold"], "angle_hold")
        app.start_servo()
        self.assertIsNone(app.state.snapshot()["servo_hold"])
        self.assertEqual(app.state.snapshot()["servo"], (10, .25, 30, 35))
        self.assertIs(app.spool_geometry_vars["minimum_angle"], app.servo_vars["minimum"])
        app.connected = False
        app.update_controls()
        self.assertTrue(app.servo_angle_button.instate(["disabled"]))

    def test_sweep_buttons_stage_pause_resume_center_and_stop(self):
        app = self.app
        app.connected = app.pause_supported = app.angle_supported = True
        self.assertNotIn("center", app.servo_vars)
        self.assertEqual([app.servo_vars[k].get() for k in ("speed", "minimum", "maximum", "start")], ["0.25", "-31", "31", "31"])
        app.set_servo()
        self.assertIsNone(app.state.snapshot()["servo"])
        app.start_servo()
        original = app.state.snapshot()["servo"]
        app.sweep_pause_button.invoke()
        self.assertEqual(app.sweep_pause_button.cget("text"), "Resume Sweep")
        self.assertEqual(app.state.snapshot()["servo_hold"], "paused")
        app.servo_vars["speed"].set("99")  # Unsaved edits must not change Resume.
        app.sweep_pause_button.invoke()
        self.assertIsNone(app.state.snapshot()["servo_hold"])
        self.assertEqual(app.state.snapshot()["servo"], original)
        app.center_spooling()
        self.assertEqual(app.state.snapshot()["servo_hold"], "centered")
        self.assertEqual(str(app.sweep_pause_button.cget("state")), "disabled")
        app.set_servo()
        self.assertIsNone(app.state.snapshot()["servo_hold"])
        self.assertEqual(app.state.snapshot()["servo"], (0, 99, 31, 31))
        app.detach_spooling()
        self.assertIsNone(app.state.snapshot()["servo"])
        self.assertIsNone(app.state.snapshot()["servo_hold"])
        self.assertEqual(app.sweep_pause_button.cget("text"), "Pause Sweep")
        app.start_servo()
        self.assertEqual(app.state.snapshot()["servo"], (0, 99, 31, 31))

    def test_spooling_group_layout_direct_drive_and_automatic_motor_start_stop(self):
        app = self.app
        content = app.spooling_controls_section.content
        self.assertEqual(app.spooling_panel.master, content)
        self.assertEqual(app.spool_motor_panel.master, content)
        self.assertEqual(app.spool_geometry_section.master, content)
        self.assertEqual(content.pack_slaves(), [app.spool_diagnostics_panel, app.spooling_panel,
                                                app.spool_motor_panel, app.spool_geometry_section])
        self.assertNotIn("motor_ratio", app.spool_geometry_vars)
        app.connected = app.winding_supported = app.winding_ready = True
        app.spool_motor_mode_buttons["Automatic"].invoke()
        self.assertFalse(app.state.snapshot()["spool_auto"])
        app.start_motor("dm")
        self.assertFalse(app.state.snapshot()["spool_auto"])
        app.state.set(winding_auto=True, servo="auto_hold")
        app.start_motor("dm")
        self.assertTrue(app.state.snapshot()["spool_auto"])
        app.stop_motor("dm")
        self.assertFalse(app.state.snapshot()["spool_auto"])
        self.assertIsInstance(app.spool_motor_mode_buttons["Automatic"], recycler.ttk.Radiobutton)
        self.assertTrue(app.spool_motor_mode_buttons["Automatic"].instate(["selected"]))
        app.state.set(spool_auto=True)
        app.spool_motor_mode_buttons["Manual"].invoke()
        self.assertFalse(app.state.snapshot()["spool_auto"])
        self.assertTrue(app.spool_motor_mode_buttons["Manual"].instate(["selected"]))
        app.speed_vars["dm"].set(4)
        app.start_motor("dm")
        self.assertEqual(app.state.snapshot()["dm"], 4)
        app.tare_n()
        self.assertEqual(app.state.snapshot()["winding_tare_request"], 0)
        app.stop_motor("dm")
        app.tare_n()
        self.assertEqual(app.state.snapshot()["winding_tare_request"], 1)
        self.assertEqual(app.tare_button.cget("text"), "Extrusion Reset")

    def test_disabled_puller_and_inline_baseline_warning(self):
        app = self.app
        self.assertEqual(app.mode.get(), "Disabled")
        self.assertTrue(app.state.snapshot()["puller_disabled"])
        self.assertEqual(app.state.snapshot()["cl"], 0)
        self.assertEqual(str(app.motor_start_buttons["cl"].cget("state")), "disabled")
        app.mode.set("Manual")
        app.mode_changed()
        app.connected = app.cl_ready = True
        app.speed_vars["cl"].set(5)
        app.start_motor("cl")
        self.assertEqual(app.state.snapshot()["cl"], 5)
        app.mode.set("Disabled")
        app.mode_changed()
        self.assertTrue(app.state.snapshot()["puller_disabled"])
        self.assertEqual(app.state.snapshot()["cl"], 0)
        self.assertFalse(app.state.snapshot()["auto"])
        app.start_motor("cl")
        self.assertEqual(app.state.snapshot()["cl"], 0)
        self.assertEqual(app.spool_baseline_warning.winfo_manager(), "")
        app.spool_motor_mode_buttons["Automatic"].invoke()
        self.assertEqual(app.spool_baseline_warning.winfo_manager(), "pack")
        self.assertEqual(str(app.spool_baseline_warning.cget("foreground")), "#b91c1c")
        self.assertEqual(app.spool_baseline_warning.cget("text"),
                         "Warning: feedback control disabled; using manual input as baseline")
        self.assertEqual(str(app.motor_widgets["dm"][0].cget("state")), "normal")
        app.speed_vars["dm"].set(8)
        app.apply_running_motor("dm")
        self.assertEqual(app.state.snapshot()["spool_baseline_rpm"], 8)
        self.assertFalse(app.state.snapshot()["spool_auto"])
        app.winding_supported = app.winding_ready = True
        app.state.set(winding_auto=True, servo="auto_hold")
        app.start_motor("dm")
        self.assertTrue(app.state.snapshot()["spool_auto"])
        self.assertEqual(app.state.snapshot()["spool_baseline_rpm"], 8)
        app.speed_vars["dm"].set(6)
        app.apply_running_motor("dm")
        self.assertEqual(app.state.snapshot()["spool_baseline_rpm"], 6)
        self.assertEqual(app.state.snapshot()["dm"], 0)
        app.mode.set("Manual")
        app.mode_changed()
        self.assertFalse(app.state.snapshot()["puller_disabled"])
        self.assertEqual(app.spool_baseline_warning.winfo_manager(), "")
        app.mode.set("Disabled")
        app.mode_changed()
        app.spool_motor_mode_buttons["Manual"].invoke()
        self.assertEqual(app.spool_baseline_warning.winfo_manager(), "")

    def test_spooling_diagnostics_use_worker_estimates(self):
        app = self.app
        app.spool_motor_mode.set("Automatic")
        fields = self.reported_fields(True) | dict(WIND_V="1", LAYERS="3", RADIUS="48.75",
                                                  WIND_READY="1", WIND_ACTIVE="1",
                                                  TAKEUP_MM_S="20.125", LENGTH_MM="1234.5")
        app.events.put(("status", fields))
        app.poll()
        self.assertEqual(app.extruded_length_text.get(), "1234.5")
        self.assertEqual(app.motor_readouts["dm"].cget("text"), "4.000 RPM")
        for value in ("Layers: 3", "97.500 mm", "20.125 mm/s", "1.234 m"):
            self.assertIn(value, app.spool_diagnostics_text.get())

    def test_calculated_spool_rpm_is_live_preview_in_manual_without_changing_command(self):
        app = self.app
        app.mode.set("Manual")
        app.mode_changed()
        app.connected = True
        app.state.set(dm=4)
        app.manual_running["dm"] = True
        fields = self.reported_fields(True) | dict(CL="10", DM="4", WIND_V="1", LAYERS="2",
                                                  RADIUS="47", WIND_READY="1", WIND_ACTIVE="0")
        app.events.put(("status", fields))
        app.poll()
        expected = round(10 * 22.5044 / 94, 3)
        self.assertEqual(app.calculated_spool_rpm, expected)
        self.assertEqual(app.calculated_spool_label.master, app.spool_diagnostics_panel)
        self.assertNotIn("spooler_gain", app.automatic_vars)
        self.assertIn("Preview only", app.spooler_text.get())
        self.assertEqual(app.state.snapshot()["dm"], 4)
        self.assertFalse(app.state.snapshot()["spool_auto"])
        app.events.put(("status", fields | dict(CL="20", LAYERS="3", RADIUS="48.75")))
        app.poll()
        self.assertEqual(app.calculated_spool_rpm, round(20 * 22.5044 / 97.5, 3))

    def test_calculated_spool_rpm_matches_disabled_puller_automatic_command_and_hold(self):
        app = self.app
        app.connected = True
        app.speed_vars["dm"].set(8)
        app.mode.set("Disabled")
        app.mode_changed()
        app.spool_motor_mode_buttons["Automatic"].invoke()
        app.state.set(spool_auto=True, winding_auto=True, servo="auto_hold")
        expected = round(8 * 87 / 101, 3)
        fields = self.reported_fields(True) | dict(CL="0", DM=str(expected), WIND_V="1", LAYERS="4",
                                                  RADIUS="50.5", WIND_READY="1", WIND_ACTIVE="1")
        app.events.put(("status", fields))
        app.poll()
        self.assertEqual(app.calculated_spool_rpm, float(fields["DM"]))
        self.assertIn("Automatic command", app.spooler_text.get())
        self.assertIn("scaled manual baseline", app.spooler_text.get())
        app.state.set(servo_hold="paused")
        app.poll()
        self.assertEqual(app.calculated_spool_rpm, 0)
        app.state.set(servo_hold=None, spool_auto=False)
        app.speed_vars["dm"].set(6)
        app.poll()
        self.assertEqual(app.calculated_spool_rpm, round(6 * 87 / 101, 3))
        self.assertIn("Preview only", app.spooler_text.get())

    def test_motor_controls_disabled_without_mega_in_every_mode(self):
        app = self.app
        controls = [widget for widgets in app.motor_widgets.values() for widget in widgets]
        controls += list(app.motor_stop_buttons.values()) + app.servo_widgets
        controls += [app.auto_start, app.auto_stop, app.auto_apply, app.stop_all_button,
                     app.sweep_apply_button, app.sweep_start_button, app.sweep_stop_button,
                     app.sweep_pause_button, app.sweep_center_button, app.tare_button]
        for puller in ("Manual", "Automatic", "Disabled"):
            for spooler in ("Manual", "Automatic"):
                for sweep in ("Manual", "Automatic"):
                    app.mode.set(puller)
                    app.spool_motor_mode.set(spooler)
                    app.spooling_mode.set(sweep)
                    app.update_controls()
                    for widget in controls:
                        self.assertEqual(str(widget.cget("state")), "disabled",
                                         (puller, spooler, sweep, widget))

    def test_motor_controls_follow_connect_disconnect_and_mode_without_starting(self):
        app = self.app
        app.mode.set("Manual")
        app.mode_changed()
        app.events.put(("connected", self.reported_fields()))
        app.poll()
        for key, widgets in app.motor_widgets.items():
            for widget in widgets + [app.motor_stop_buttons[key]]:
                self.assertEqual(str(widget.cget("state")), "normal")
        app.mode.set("Automatic")
        app.mode_changed()
        self.assertEqual(str(app.auto_start.cget("state")), "normal")
        for widget in app.motor_widgets["cl"]:
            self.assertEqual(str(widget.cget("state")), "disabled")
        self.assertEqual(str(app.motor_stop_buttons["cl"].cget("state")), "normal")
        app.events.put(("error", "USB disconnected"))
        app.events.put(("disconnected", None))
        app.poll()
        for widgets in app.motor_widgets.values():
            self.assertTrue(all(str(widget.cget("state")) == "disabled" for widget in widgets))
        app.events.put(("connected", self.reported_fields()))
        app.poll()
        for key in ("cl", "dm", "spark", "fan"):
            self.assertEqual(app.state.snapshot()[key], 0)
        self.assertFalse(app.state.snapshot()["auto"])
        self.assertFalse(app.state.snapshot()["spool_auto"])
        self.assertEqual(str(app.auto_start.cget("state")), "normal")
        self.assertEqual(str(app.motor_widgets["spark"][0].cget("state")), "normal")

    def test_system_diagnostics_motor_commands_and_stale_status(self):
        app = self.app
        self.assertEqual(app.system_diagnostics_panel.cget("text"), "System Diagnostics")
        self.assertEqual(len(app.diagnostics_columns), 3)
        self.assertTrue(all(value.get() == "N/A" for value in app.diagnostic_motor_text.values()))
        app.events.put(("connected", self.reported_fields(True)))
        app.poll()
        self.assertEqual(app.diagnostic_motor_text["cl"].get(), "3.000 RPM")
        self.assertEqual(app.diagnostic_motor_text["dm"].get(), "4.000 RPM")
        self.assertEqual(app.diagnostic_motor_text["spark"].get(), "25%")
        self.assertEqual(app.diagnostic_motor_text["fan"].get(), "50.2%")
        self.assertIn("15.000 deg/s", app.diagnostic_motor_text["servo"].get())
        app.last_mega_status_at = time.monotonic()-3
        app.update_diagnostic_motor_readouts()
        self.assertTrue(all(value.get() == "N/A" for value in app.diagnostic_motor_text.values()))

    def test_recording_statistics_window_freeze_reset_and_csv(self):
        app = self.app
        self.assertEqual(app.statistics_text["std_dev"].get(), "N/A")
        with tempfile.TemporaryDirectory() as directory:
            app.recorder = DiameterRecorder(directory)
            app.start_recording()
            for number, value in enumerate((1.7, 1.75, 1.8), 1):
                stamp = time.monotonic()
                sample = dict(protocol=1, valid=True, diameter_mm=value, frame_sequence=number,
                              frame_received_at=stamp, published_at=stamp)
                self.camera.snapshot.return_value = (sample, "Live")
                app.poll()
                app.poll()  # Repainting the same frame must not add a sample.
            self.assertEqual(app.recording_statistics.snapshot()["sample_count"], 3)
            self.assertEqual(app.minimum_diameter_text.get(), "1.7000")
            self.assertEqual(app.maximum_diameter_text.get(), "1.8000")
            self.assertEqual(app.statistics_text["std_dev"].get(), "0.05000")
            self.assertIn("+/-", app.statistics_text["ci_95"].get())
            app.statistics_window_input.set("3")
            app.apply_statistics_window()
            self.assertEqual(app.statistics_window_slider.get(), 3)
            self.assertEqual(app.recording_statistics.window, 3)
            for invalid in ("1", "1001", "2.5", "abc", ""):
                app.statistics_window_input.set(invalid)
                app.apply_statistics_window()
                self.assertEqual(app.recording_statistics.window, 3)
                self.assertTrue(app.statistics_window_entry.instate(["invalid"]))
            app.statistics_window_slider.set(2)
            app.statistics_window_changed("2")
            self.assertEqual(app.statistics_window_input.get(), "2")
            self.assertFalse(app.statistics_window_entry.instate(["invalid"]))
            self.assertEqual(app.recording_statistics.snapshot()["window_count"], 2)
            self.assertEqual(app.state.snapshot()["automatic"].target, 1.75)
            app.stop_recording()
            frozen = app.recording_statistics.snapshot()
            app.recorder.thread.join(timeout=2)
            app.finish_recording_save()
            with app.recorder.path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[-1]["session_sample_count"], "3")
            self.assertAlmostEqual(float(rows[-1]["std_dev_mm"]), .05)
            self.assertNotEqual(rows[-1]["mean_ci_95_half_width_mm"], "")
            self.camera.snapshot.return_value = (self.packet | dict(diameter_mm=2.0), "Live")
            app.poll()
            self.assertEqual(app.recording_statistics.snapshot(), frozen)
            self.assertEqual(app.camera_diameter_text.get(), "2.0000")
            app.start_recording()
            self.assertEqual(app.recording_statistics.snapshot()["sample_count"], 0)
            self.assertEqual(app.minimum_diameter_text.get(), "N/A")
            app.stop_recording()
            app.recorder.thread.join(timeout=2)
            app.finish_recording_save()

    def test_collapsing_sections_clamps_scroll_origin_and_short_content_does_not_scroll(self):
        app = self.app
        self.root.geometry("1200x1000-10000-10000")
        self.root.deiconify()
        self.root.update()
        canvas = app.scroll_canvas
        self.assertGreater(app.scroll_content.winfo_height(), canvas.winfo_height())
        canvas.yview_moveto(1)
        self.assertGreater(canvas.canvasy(0), 0)
        app.feedback_section.toggle()
        app.main_controls_section.toggle()
        self.root.update()
        self.assertLessEqual(app.scroll_content.winfo_height(), canvas.winfo_height())
        self.assertAlmostEqual(canvas.canvasy(0), 0, delta=1)
        self.root.event_generate("<MouseWheel>", delta=1200)
        self.root.update()
        canvas.yview_scroll(-100, "units")  # Scrollbar/programmatic scrolling also stays bounded.
        self.assertAlmostEqual(canvas.canvasy(0), 0, delta=1)
        app.main_controls_section.toggle()
        self.root.update()
        canvas.yview_moveto(1)
        self.assertGreater(canvas.canvasy(0), 0)
        app.main_controls_section.toggle()
        self.root.update()
        self.assertAlmostEqual(canvas.canvasy(0), 0, delta=1)

    def test_start_stop_styles_have_color_and_disabled_grey(self):
        app = self.app
        starts = list(app.motor_start_buttons.values()) + [app.sweep_start_button, app.auto_start,
                                                          app.start_recording_button]
        stops = list(app.motor_stop_buttons.values()) + [app.sweep_stop_button, app.auto_stop,
                                                        app.stop_recording_button]
        style = recycler.ttk.Style(self.root)
        for buttons, name, color in ((starts, "Start.TButton", "#15803d"),
                                     (stops, "Stop.TButton", "#b91c1c")):
            for button in buttons:
                self.assertEqual(button.cget("style"), name)
            self.assertEqual(style.lookup(name, "background"), color)
            self.assertEqual(style.lookup(name, "background", ("disabled",)), "#e5e7eb")
            self.assertEqual(style.lookup(name, "foreground", ("disabled",)), "#6b7280")
        self.assertEqual(app.stop_all_button.cget("style"), "Emergency.Stop.TButton")
        self.assertEqual(style.lookup("Emergency.Stop.TButton", "background"), "#b91c1c")
        for state in ((), ("disabled",), ("active",)):
            self.assertEqual(style.lookup("Emergency.Stop.TButton", "foreground", state), "black")
        self.assertEqual(tkfont.Font(font=style.lookup("Emergency.Stop.TButton", "font")).actual("weight"), "bold")

    def test_recording_saves_latest_motor_commands_velocity_and_blanks_stale_data(self):
        app = self.app
        with tempfile.TemporaryDirectory() as directory:
            app.recorder = DiameterRecorder(directory)
            app.start_recording()
            fields = self.reported_fields(True) | dict(TAKEUP_MM_S="18.22123739", LENGTH_MM="640", THETA="-12.5", OMEGA="0.25")
            app.events.put(("connected", fields))
            app.poll()
            # A new Mega status uses the same camera frame without adding a diameter point.
            app.events.put(("status", fields | dict(DM="8", TAKEUP_MM_S="36.44247478", LENGTH_MM="650")))
            app.poll()
            app.poll()
            app.last_mega_status_at = time.monotonic()-3
            app.poll()
            app.stop_recording()
            app.recorder.thread.join(timeout=2)
            app.finish_recording_save()
            with app.recorder.path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 3)
            first = rows[0]
            self.assertEqual(first["puller_command_rpm"], "3")
            self.assertEqual(first["spooler_command_rpm"], "4")
            self.assertEqual(first["extruder_command_percent"], "25")
            self.assertEqual(first["cooling_fans_command_pwm"], "128")
            self.assertAlmostEqual(float(first["cooling_fans_command_percent"]), 128/255*100)
            self.assertEqual(first["traverser_command_speed_deg_s"], "0.25")
            self.assertEqual(first["traverser_command_angle_deg"], "-12.5")
            self.assertEqual(first["traverser_enabled"], "1")
            self.assertEqual(first["filament_linear_velocity_mm_s"], "18.22123739")
            self.assertEqual(first["extruded_length_mm"], "640")
            self.assertEqual(rows[1]["extruded_length_mm"], "650")
            self.assertEqual(rows[1]["spooler_command_rpm"], "8")
            self.assertEqual(rows[1]["filament_linear_velocity_mm_s"], "36.44247478")
            self.assertEqual([row["new_diameter_sample"] for row in rows], ["1", "0", "0"])
            self.assertEqual(app.recorder.progress()[1], 1)
            self.assertEqual(app.recording_statistics.snapshot()["sample_count"], 1)
            self.assertEqual(rows[-1]["mega_status_valid"], "0")
            self.assertEqual(rows[-1]["spooler_command_rpm"], "")
            self.assertEqual(rows[-1]["filament_linear_velocity_mm_s"], "")
            app.connected = False
            disconnected = app.recording_motor_snapshot()
            self.assertEqual(disconnected["mega_status_valid"], 0)
            self.assertNotIn("puller_command_rpm", disconnected)

    def test_stop_recording_prompts_after_flush_and_saves_chosen_name(self):
        from pathlib import Path
        app = self.app
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)/"renamed-run.csv"
            destination.write_text("old file")
            self.save_dialog.return_value = str(destination)
            app.recorder = DiameterRecorder(Path(directory)/"backup")
            app.start_recording()
            for number in range(20):
                stamp = time.monotonic()
                self.camera.snapshot.return_value = (self.packet | dict(frame_sequence=number,
                    frame_received_at=stamp, published_at=stamp), "Live")
                app.poll()
            app.stop_recording_button.invoke()
            self.assertTrue(app.recording_save_pending)
            self.save_dialog.assert_not_called()
            self.assertEqual(str(app.start_recording_button.cget("state")), "disabled")
            app.recorder.thread.join(timeout=2)
            app.finish_recording_save()
            self.save_dialog.assert_called_once()
            self.assertTrue(self.save_dialog.call_args.kwargs["confirmoverwrite"])
            app.recording_exporter.thread.join(timeout=2)
            app.poll_recording()
            self.assertEqual(destination.read_bytes(), app.recorder.path.read_bytes())
            with destination.open(newline="", encoding="utf-8") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 20)
            self.assertIn(str(destination), app.recording_text.get())
            self.assertEqual(str(app.start_recording_button.cget("state")), "normal")
            self.assertEqual(app.last_recording_save_directory, destination.parent)

    def test_cancel_save_keeps_original_and_does_not_prompt_twice(self):
        app = self.app
        with tempfile.TemporaryDirectory() as directory:
            app.recorder = DiameterRecorder(directory)
            app.start_recording()
            app.poll()
            app.stop_recording()
            app.recorder.thread.join(timeout=2)
            app.finish_recording_save()
            self.save_dialog.assert_called_once()
            self.assertTrue(app.recorder.path.is_file())
            self.assertIsNone(app.recording_exporter.path)
            app.stop_recording()
            app.finish_recording_save()
            self.save_dialog.assert_called_once()
            self.assertEqual(str(app.start_recording_button.cget("state")), "normal")

    def test_custom_spool_measurements_are_saved_and_restore_in_manual_mode(self):
        from pathlib import Path
        from spooling_control import load_geometry
        app = self.app
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spool.json"
            app.spooling_settings_path = path
            app.spool_geometry_vars["winding_width"].set("63")
            app.spool_geometry_vars["core_diameter"].set("92")
            self.assertTrue(app.apply_spool_geometry())
            self.assertEqual(load_geometry(path).winding_width, 63)
            with mock.patch.object(recycler.list_ports, "comports", return_value=[]):
                other = recycler.Recycler(self.root, camera=self.camera, spooling_settings_path=path)
            self.assertEqual(other.spool_geometry_vars["core_diameter"].get(), "92")
            self.assertEqual(other.spooling_mode.get(), "Manual")
            self.assertFalse(other.state.snapshot()["winding_auto"])

if __name__ == "__main__":
    unittest.main()
