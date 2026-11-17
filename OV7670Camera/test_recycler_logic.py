"""Control direction, limits and actual worker command sequences with a fake Mega."""
from contextlib import contextmanager
import queue
import time
import unittest
from unittest import mock

import recycler_logic as logic
from spooling_control import SpoolGeometry


def packet(value=1.85, stamp=None, seq=1):
    now = time.monotonic() if stamp is None else stamp
    return dict(protocol=1, valid=True, diameter_mm=value,
                frame_received_at=now, published_at=now, frame_sequence=seq)


class FakeMega:
    def __init__(self):
        self.commands = []
        self.replies = queue.Queue()
        self.is_open = True
        self.armed = False
        self.cl_ready = True
        self.osc_supported = True
        self.start_angle_supported = True
        self.tared = False
        self.n = 0
        self.theta = 0.0
        self.auto_active = False
        self.winding_supported = True
        self.winding_ready = self.winding_active = False
        self.layers = 0
        self.geometry = SpoolGeometry()
        self.cl_rpm = self.dm_rpm = 0.0
        self.reject = None
        self.on_command = lambda command: None

    def write(self, data):
        command = data.decode().strip()
        self.commands.append(command)
        self.on_command(command)
        name, *args = command.split()
        if name == self.reject:
            reply = "ERR TEST_REJECTION"
        elif name == "PING":
            reply = "PONG"
        elif name == "STATUS":
            reply = f"STATUS CL={self.cl_rpm} DM={self.dm_rpm} FAN=0 SPARK=0 ARM={int(self.armed)} SERVO=0 CL_READY={int(self.cl_ready)}"
            if self.osc_supported:
                reply += f" OSC_V=1 N={self.n} THETA={self.theta} OMEGA=0 TARED={int(self.tared)} OSC_AUTO={int(self.auto_active)}"
            if self.start_angle_supported:
                reply += " SWEEP_V=1 PAUSE_V=1 ANGLE_V=1"
            if self.winding_supported:
                reply += (f" WIND_V=1 LAYERS={self.layers} RADIUS={self.geometry.radius(self.layers)}"
                          f" WIND_READY={int(self.winding_ready)} WIND_ACTIVE={int(self.winding_active)}")
                reply = reply.replace("OMEGA=0 ", f"OMEGA={self.geometry.angular_speed(self.dm_rpm) if self.winding_active else 0} ")
        elif name == "WINDCFG":
            geometry = SpoolGeometry(*(float(x) for x in args))
            geometry.validate()
            if geometry != self.geometry:
                self.winding_ready = self.winding_active = False
            self.geometry = geometry
            reply = "OK WINDCFG"
        elif name == "WINDTARE":
            self.winding_ready, self.winding_active = True, False
            self.layers = self.n = 0
            self.theta = self.geometry.minimum_angle
            reply = "OK WINDTARE"
        elif name == "WINDSTART":
            self.winding_active = self.winding_ready
            reply = "OK WINDSTART" if self.winding_ready else "ERR WIND_TARE_REQUIRED"
        elif name == "WINDPAUSE":
            self.winding_active = False
            reply = "OK WINDPAUSE"
        elif name == "TARE":
            self.tared, self.n, self.theta = True, 0, -31.36
            self.auto_active = False
            reply = "OK TARE"
        elif name == "AUTOSWEEP":
            self.auto_active = True
            reply = "OK AUTOSWEEP" if self.tared and len(args) == 2 else "ERR TARE_REQUIRED"
        elif name == "AUTOPAUSE":
            self.auto_active = False
            reply = "OK AUTOPAUSE"
        elif name == "STOP":
            self.armed = False
            self.auto_active = False
            self.winding_active = False
            self.cl_rpm = self.dm_rpm = 0.0
            reply = "OK STOP"
        elif name == "ARM":
            self.armed = True
            reply = "OK ARM"
        elif name in ("CLRPM", "DMRPM"):
            limit = 100 if name == "CLRPM" else 20
            reply = f"OK {name}" if len(args) == 1 and abs(float(args[0])) <= limit else "ERR RPM_LIMIT"
            if reply.startswith("OK"):
                if name == "CLRPM": self.cl_rpm = float(args[0])
                else: self.dm_rpm = float(args[0])
        elif name == "SPARK":
            reply = "OK SPARK" if len(args) == 1 and (self.armed or int(args[0]) == 0) else "ERR SPARK_NOT_ARMED"
        elif name == "FAN":
            reply = "OK FAN" if len(args) == 1 and 0 <= int(args[0]) <= 255 else "ERR FAN_RANGE"
        elif name == "SWEEP":
            self.tared = False
            self.winding_ready = self.winding_active = False
            if len(args) == 4:
                self.theta = float(args[3])
                reply = "OK SWEEP"
            else:
                reply = "ERR SWEEP_ARGS"
        elif name in ("SWEEPPAUSE", "SWEEPRESUME"):
            reply = "OK " + name
        elif name == "SWEEPANGLE":
            self.theta = float(args[0])
            reply = "OK SWEEPANGLE"
        elif name == "SWEEPCENTER":
            self.theta = 0
            reply = "OK SWEEPCENTER"
        elif name == "SWEEPCONT":
            reply = "OK SWEEPCONT"
        elif name == "SWEEPSPEED":
            reply = "OK SWEEPSPEED" if len(args) == 1 else "ERR SERVO_SPEED"
        elif name == "SERVO":
            self.tared = False
            self.winding_ready = self.winding_active = False
            reply = "OK SERVO" if len(args) == 3 else "ERR SERVO_ARGS"
        elif name == "SERVOSTOP":
            self.auto_active = False
            self.winding_active = False
            reply = "OK SERVOSTOP"
        else:
            reply = "ERR UNKNOWN"
        self.replies.put((reply + "\n").encode())

    def readline(self):
        try:
            return self.replies.get(timeout=0.02)
        except queue.Empty:
            return b""

    def close(self):
        self.is_open = False


class ControlTests(unittest.TestCase):
    def test_angle_jump_preserves_running_program_pause_and_layer_count(self):
        with self.worker() as (worker, state, camera, device, events):
            state.set_manual_sweep(10, .25, 30, 40)
            self.wait_for(lambda: "SWEEP 10.00 0.25 30.00 40.00" in device.commands)
            state.request_servo_angle(15)
            self.wait_for(lambda: "SWEEPANGLE 15.00 0" in device.commands)
            state.set(servo_hold="paused")
            self.wait_for(lambda: "SWEEPPAUSE" in device.commands)
            device.layers, device.n = 3, 2
            state.request_servo_angle(-10)
            self.wait_for(lambda: "SWEEPANGLE -10.00 0" in device.commands)
            self.assertEqual(device.theta, -10)
            self.assertEqual(state.snapshot()["servo_hold"], "paused")
            self.assertEqual((device.layers, device.n), (3, 2))
            state.set(servo_hold=None)
            self.wait_for(lambda: "SWEEPRESUME" in device.commands)
            self.assertEqual(sum(c.startswith("SWEEP ") for c in device.commands), 1)
            state.request_servo_angle(10, hold=True)
            self.wait_for(lambda: "SWEEPANGLE 10.00 1" in device.commands)
            state.request_servo_angle(12)
            self.wait_for(lambda: "SWEEPANGLE 12.00 0" in device.commands)
            self.assertNotIn("SWEEPCENTER", device.commands)
            state.set(servo_hold=None)
            self.wait_for(lambda: "SWEEPCONT 10.00 0.25 30.00 40.00" in device.commands)

    def wait_for(self, condition):
        until = time.monotonic() + 3
        while time.monotonic() < until:
            if condition():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for worker")

    @contextmanager
    def worker(self, fake=None):
        fake = fake or FakeMega()
        state, events = logic.ControlState(), queue.Queue()
        camera = mock.Mock()
        camera.snapshot.return_value = (packet(), "Live")
        worker = logic.MegaWorker("FAKE", state, camera, events)
        original_wait = worker.stop.wait
        with mock.patch.object(logic.serial, "Serial", return_value=fake), \
                mock.patch.object(worker.stop, "wait", side_effect=lambda seconds: False if seconds == 2.2 else original_wait(seconds)):
            worker.start()
            try:
                self.wait_for(lambda: worker.ready)
                yield worker, state, camera, fake, events
            finally:
                worker.stop.set()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())

    def test_pause_resume_and_center_keep_manual_phase_without_restart(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.set_manual_sweep(0, .25, 31, 31)
            self.wait_for(lambda: "SWEEP 0.00 0.25 31.00 31.00" in fake.commands)
            fake.theta = 12.75  # Simulate progress after initial start.
            state.set(servo_hold="paused")
            self.wait_for(lambda: "SWEEPPAUSE" in fake.commands)
            state.set_manual_sweep(0, .5, 31, 31, restart_unchanged=False)
            state.set(servo_hold=None)
            self.wait_for(lambda: "SWEEPRESUME" in fake.commands)
            self.assertEqual(fake.theta, 12.75)
            self.assertIn("SWEEPSPEED 0.50", fake.commands)
            self.assertLess(fake.commands.index("SWEEPSPEED 0.50"), fake.commands.index("SWEEPRESUME"))
            state.set(servo_hold="centered")
            self.wait_for(lambda: "SWEEPCENTER" in fake.commands)
            self.assertEqual(fake.theta, 0)
            state.set(servo_hold=None)
            self.wait_for(lambda: "SWEEPCONT 0.00 0.50 31.00 31.00" in fake.commands)
            self.assertEqual(fake.theta, 0)
            self.assertEqual(sum(c.startswith("SWEEP ") for c in fake.commands), 1)
            state.set(servo=None)
            self.wait_for(lambda: fake.commands[-1] == "SERVOSTOP")
            state.set_manual_sweep(0, .5, 31, 31)
            self.wait_for(lambda: "SWEEP 0.00 0.50 31.00 31.00" in fake.commands)
            self.assertEqual(fake.theta, 31)

    def test_automatic_pause_resume_preserves_layers_and_other_motor_commands(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.request_winding_tare()
            self.wait_for(lambda: worker.winding is not None and worker.winding["ready"])
            state.set(winding_auto=True, servo="auto_hold", dm=3)
            self.wait_for(lambda: "WINDSTART" in fake.commands)
            fake.theta, fake.layers = 8.5, 4
            state.set(servo_hold="paused")
            self.wait_for(lambda: "SWEEPPAUSE" in fake.commands)
            state.set(servo_hold=None)
            self.wait_for(lambda: fake.commands.count("WINDSTART") == 2)
            self.assertEqual((fake.theta, fake.layers, fake.dm_rpm), (8.5, 4, 3))
            self.assertEqual(fake.commands.count("WINDTARE"), 1)

    def test_pause_before_initial_command_still_establishes_manual_sweep(self):
        # Both desired-state updates happen before the worker dispatches.
        fake = FakeMega()
        with self.worker(fake) as (worker, running, camera, device, events):
            running.set_manual_sweep(0, .25, 31, 31)
            running.set(servo_hold="paused")
            self.wait_for(lambda: "SWEEPPAUSE" in device.commands)
            self.assertIn("SWEEP 0.00 0.25 31.00 31.00", device.commands)
            running.set(servo_hold=None)
            self.wait_for(lambda: "SWEEPRESUME" in device.commands)

    def test_automatic_spool_rpm_falls_with_layers_and_holds_for_paused_traverser(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.request_winding_tare()
            self.wait_for(lambda: worker.winding is not None and worker.winding["ready"])
            state.set(winding_auto=True, servo="auto_hold", spool_auto=True, cl=10)
            expected = round(SpoolGeometry().target_motor_rpm(logic.linear_velocity(10, 22.5044), 0), 3)
            self.wait_for(lambda: fake.dm_rpm == expected)
            fake.layers = 3
            expected_later = round(SpoolGeometry().target_motor_rpm(logic.linear_velocity(10, 22.5044), 3), 3)
            self.wait_for(lambda: fake.dm_rpm == expected_later)
            self.assertLess(expected_later, expected)
            self.assertAlmostEqual(SpoolGeometry().takeup_speed(fake.dm_rpm, 3),
                                   logic.linear_velocity(10, 22.5044), delta=.01)
            state.set(servo_hold="paused")
            self.wait_for(lambda: fake.dm_rpm == 0)
            state.set(servo_hold=None)
            self.wait_for(lambda: fake.dm_rpm == expected_later)
            state.set(cl=0)
            self.wait_for(lambda: fake.dm_rpm == 0)
            state.stop_all()
            self.assertFalse(state.snapshot()["spool_auto"])

    def test_automatic_spool_limit_and_acknowledged_extrusion_reset(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.request_winding_tare()
            self.wait_for(lambda: worker.winding is not None and worker.winding["ready"])
            state.set(winding_auto=True, servo="auto_hold", spool_auto=True, cl=100)
            self.wait_for(lambda: fake.dm_rpm == 20)
            self.wait_for(lambda: worker.takeup.length_mm > 0)
            state.set(spool_auto=False, dm=0, winding_auto=False, servo="auto_hold")
            self.wait_for(lambda: fake.dm_rpm == 0)
            fake.layers = 5
            before = fake.commands.count("WINDTARE")
            state.request_winding_tare()
            self.wait_for(lambda: fake.commands.count("WINDTARE") > before)
            self.wait_for(lambda: worker.takeup.length_mm == 0)
            self.assertEqual(fake.layers, 0)
            self.assertEqual(fake.dm_rpm, 0)
            self.assertEqual(worker.takeup.extruded_length_mm, 635)
            with events.mutex:
                resets = [info for kind, info in events.queue if kind == "winding_tared"]
            self.assertEqual(float(resets[-1]["LENGTH_MM"]), 635)

    def test_bypassed_puller_uses_manual_layer_zero_rpm_without_camera(self):
        with self.worker() as (worker, state, camera, fake, events):
            camera.snapshot.return_value = (None, "Disconnected")
            fake.cl_ready = False
            state.request_winding_tare()
            self.wait_for(lambda: worker.winding is not None and worker.winding["ready"])
            state.set(puller_disabled=True, auto=True, cl=50, winding_auto=True,
                      servo="auto_hold", spool_auto=True, spool_baseline_rpm=8)
            self.wait_for(lambda: fake.dm_rpm == 8)
            self.assertEqual(fake.cl_rpm, 0)
            self.assertNotIn("CLRPM 50.000", fake.commands)
            fake.layers = 4
            expected = round(8 * 87 / (87 + 2 * 4 * 1.75), 3)
            self.wait_for(lambda: fake.dm_rpm == expected)
            self.assertLess(expected, 8)
            state.set(spool_baseline_rpm=6)
            expected = round(6 * 87 / (87 + 2 * 4 * 1.75), 3)
            self.wait_for(lambda: fake.dm_rpm == expected)
            state.set(spool_baseline_rpm=0)
            self.wait_for(lambda: fake.dm_rpm == 0)
            # Re-enabling a stopped puller must stop using the bypass baseline.
            fake.cl_ready = True
            state.set(puller_disabled=False, auto=False, cl=0, spool_baseline_rpm=10)
            self.wait_for(lambda: fake.commands.count("STATUS") >= 4)
            self.assertEqual(fake.dm_rpm, 0)

    def test_matching_protocol_servo_not_restarted_and_stop_before_close(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.set(cl=3, dm=-4, arm=True, spark=25, fan=128, servo=(0, 15, 10))
            self.wait_for(lambda: "SERVO 0.00 15.00 10.00" in fake.commands)
            self.wait_for(lambda: fake.commands.count("STATUS") >= 3)
            self.assertEqual(fake.commands.count("SERVO 0.00 15.00 10.00"), 1)
            self.assertLess(fake.commands.index("ARM"), fake.commands.index("SPARK 25"))
            self.assertIn("DMRPM -4.000", fake.commands)
            self.assertFalse(any(c.startswith("SET") for c in fake.commands))
            state.stop_all()
            self.wait_for(lambda: fake.commands.count("STOP") >= 2)
        self.assertEqual(fake.commands[-1], "STOP")
        self.assertFalse(fake.is_open)

    def test_auto_camera_loss_stops_all_and_does_not_resume(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.request_tare()
            self.wait_for(lambda: fake.tared)
            state.set(auto=True, automatic=logic.AutomaticSettings(), dm=2, fan=64)
            self.wait_for(lambda: any(c.startswith("CLRPM 0.200") for c in fake.commands))
            time.sleep(0.15)
            self.assertLessEqual(worker.controller.output, 5.1)
            camera.snapshot.return_value = (packet(stamp=time.monotonic() - 3), "Live")
            self.wait_for(lambda: not state.snapshot()["auto"])
            self.wait_for(lambda: fake.commands.count("STOP") >= 2)
            self.assertEqual(state.snapshot()["dm"], 0)
            self.assertEqual(state.snapshot()["fan"], 0)
            camera.snapshot.return_value = (packet(), "Live")
            time.sleep(0.15)
            self.assertFalse(state.snapshot()["auto"])

    def test_stop_interrupts_the_rest_of_an_old_command_batch(self):
        with self.worker() as (worker, state, camera, fake, events):
            fake.on_command = lambda command: state.stop_all() if command == "CLRPM 7.000" else None
            state.set(cl=7, dm=11)
            self.wait_for(lambda: "CLRPM 7.000" in fake.commands)
            self.wait_for(lambda: fake.commands.count("STOP") >= 2)
            self.assertNotIn("DMRPM 11.000", fake.commands)

    def test_winding_follows_spooler_without_camera_and_preserves_layers_on_pause(self):
        with self.worker() as (worker, state, camera, fake, events):
            camera.snapshot.return_value = (None, "Disconnected")
            state.request_winding_tare()
            self.wait_for(lambda: worker.winding is not None and worker.winding["ready"])
            self.assertEqual(fake.theta, -31)
            self.assertEqual(fake.layers, 0)
            state.set(winding_auto=True)
            self.wait_for(lambda: fake.winding_active)
            self.assertEqual(fake.dm_rpm, 0)
            state.set(dm=6)
            self.wait_for(lambda: fake.dm_rpm == 6)
            fake.layers, fake.n, fake.theta = 1, 0, 31
            self.wait_for(lambda: worker.winding["layers"] == 1)
            self.assertAlmostEqual(worker.winding["radius"], 45.25)
            self.assertAlmostEqual(worker.osc["omega"], SpoolGeometry().angular_speed(6))
            start_count = fake.commands.count("WINDSTART")
            state.set(dm=12)
            self.wait_for(lambda: fake.dm_rpm == 12)
            self.assertEqual(fake.commands.count("WINDSTART"), start_count)
            state.set(winding_auto=False, servo="auto_hold")
            self.wait_for(lambda: not fake.winding_active)
            self.assertEqual(fake.layers, 1)
            self.assertEqual(fake.theta, 31)
            state.set(winding_auto=True)
            self.wait_for(lambda: fake.winding_active)
            self.assertEqual(fake.commands.count("WINDTARE"), 1)
            self.assertEqual(sum(c.startswith("WINDCFG") for c in fake.commands), 1)
            self.assertFalse(any(c.startswith("AUTOSWEEP") for c in fake.commands))

    def test_old_firmware_manual_and_puller_do_not_require_winding_capability(self):
        fake = FakeMega()
        fake.osc_supported = fake.winding_supported = False
        with self.worker(fake) as (worker, state, camera, fake, events):
            state.set(cl=3, dm=2)
            self.wait_for(lambda: "DMRPM 2.000" in fake.commands)
            self.assertIsNone(worker.winding)
            state.set(auto=True)
            self.wait_for(lambda: worker.controller is not None)
            self.assertTrue(state.snapshot()["auto"])
            state.set(winding_auto=True)
            self.wait_for(lambda: not state.snapshot()["winding_auto"])
            self.assertTrue(state.snapshot()["auto"])
            self.assertEqual(state.snapshot()["dm"], 2)
            self.assertFalse(any(c.startswith("WINDSTART") for c in fake.commands))

    def test_puller_accepts_100_rpm_both_directions_while_dm_stays_at_20(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.set(cl=100, dm=20)
            self.wait_for(lambda: "CLRPM 100.000" in fake.commands and "DMRPM 20.000" in fake.commands)
            state.set(cl=-100)
            self.wait_for(lambda: "CLRPM -100.000" in fake.commands)
            state.set(dm=21)
            self.wait_for(lambda: not worker.is_alive())
            self.assertNotIn("DMRPM 21.000", fake.commands)
            self.assertEqual(state.snapshot()["cl"], 0)

    def test_puller_over_100_is_rejected_before_transmission(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.set(cl=100.01)
            self.wait_for(lambda: not worker.is_alive())
            self.assertNotIn("CLRPM 100.010", fake.commands)

    def test_start_angle_speed_only_update_and_explicit_restart_use_distinct_commands(self):
        with self.worker() as (worker, state, camera, fake, events):
            state.set_manual_sweep(0, 15, 30, 30)
            self.wait_for(lambda: "SWEEP 0.00 15.00 30.00 30.00" in fake.commands)
            self.wait_for(lambda: fake.theta == 30)
            fake.theta = -12.5  # Simulated progress between commands.
            state.set_manual_sweep(0, 25, 30, 30)
            self.wait_for(lambda: "SWEEPSPEED 25.00" in fake.commands)
            self.assertEqual(fake.theta, -12.5)
            self.assertEqual(sum(c.startswith("SWEEP ") for c in fake.commands), 1)
            state.set_manual_sweep(0, 25, 30, 30)  # Same settings: deliberate Apply/restart.
            self.wait_for(lambda: "SWEEP 0.00 25.00 30.00 30.00" in fake.commands)
            self.wait_for(lambda: fake.theta == 30)
            state.set_manual_sweep(0, 25, 30, -30)
            self.wait_for(lambda: "SWEEP 0.00 25.00 30.00 -30.00" in fake.commands)

    def test_acknowledged_error_stops_and_disconnects(self):
        with self.worker() as (worker, state, camera, fake, events):
            fake.reject = "CLRPM"
            state.set(cl=3)
            self.wait_for(lambda: not worker.is_alive())
            self.assertEqual(state.snapshot()["cl"], 0)
            reports = list(events.queue)
            self.assertTrue(any(kind == "error" and "ERR TEST_REJECTION" in info for kind, info in reports))


if __name__ == "__main__":
    unittest.main()
