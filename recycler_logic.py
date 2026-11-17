"""Mega protocol and proportional automatic winding control. No hardware starts on import."""
import math
import threading
import time
import serial
from spooling_control import SpoolGeometry, TakeupIntegrator, spool_rpm_command
from automatic_equations import (AutomaticSettings, ProportionalPuller, MAX_RPM, MAX_DM_RPM,
                                 SWEEP_LIMIT_DEG, DiameterWindow, linear_velocity)


class ControlState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = dict(cl=0.0, dm=0.0, spark=0, fan=0, servo=None, servo_revision=0, arm=False,
                         auto=False, automatic=AutomaticSettings(), tare_request=0, stop_revision=0,
                         winding_auto=False, winding_geometry=SpoolGeometry(), winding_tare_request=0, servo_hold=None,
                         spool_auto=False, spool_direction=1, puller_disabled=False, spool_baseline_rpm=0.0,
                         servo_angle_request=0, servo_angle_target=None, servo_angle_hold=False)

    def snapshot(self):
        with self.lock:
            return self.data.copy()

    def set(self, **changes):
        with self.lock:
            self.data.update(changes)

    def request_tare(self):
        with self.lock:
            self.data.update(auto=False, cl=0.0, servo="auto_hold")
            self.data["tare_request"] += 1

    def request_winding_tare(self):
        with self.lock:
            self.data.update(winding_auto=False, servo="auto_hold", servo_hold=None, servo_angle_target=None, servo_angle_hold=False)
            self.data["winding_tare_request"] += 1

    def set_manual_sweep(self, center, speed, bounds, start, allow_speed_update=True, restart_unchanged=True):
        with self.lock:
            previous = self.data["servo"]
            speed_only = (allow_speed_update and isinstance(previous, tuple) and len(previous) == 4
                          and (previous[0], previous[2], previous[3]) == (center, bounds, start)
                          and previous[1] != speed)
            if not speed_only and (restart_unchanged or previous != (center, speed, bounds, start)):
                self.data["servo_revision"] += 1
            self.data["servo"] = (center, speed, bounds, start)
            self.data["winding_auto"] = False

    def request_servo_angle(self, angle, hold=False):
        with self.lock:
            self.data.update(servo_angle_target=angle, servo_angle_hold=hold)
            self.data["servo_angle_request"] += 1
            if hold:
                self.data["servo_hold"] = "centered"
            elif not isinstance(self.data["servo"], tuple) and not self.data["winding_auto"]:
                self.data["servo_hold"] = "angle_hold"

    def stop_all(self):
        with self.lock:
            self.data.update(cl=0.0, dm=0.0, spark=0, fan=0, servo=None, arm=False, auto=False, winding_auto=False, servo_hold=None, spool_auto=False)
            self.data.update(servo_angle_target=None, servo_angle_hold=False)
            self.data["stop_revision"] += 1


class ProtocolError(Exception):
    pass


def parse_status(line):
    if not line.startswith("STATUS "):
        raise ProtocolError("Mega did not return compatible status.")
    try:
        fields = dict(part.split("=", 1) for part in line.split()[1:])
        for key in ("CL", "DM", "FAN", "SPARK", "ARM", "SERVO", "CL_READY"):
            if not math.isfinite(float(fields[key])):
                raise ValueError
        return fields
    except (ValueError, KeyError) as error:
        raise ProtocolError("Incomplete Mega status.") from error


def oscillator_status(fields):
    if fields.get("OSC_V") != "1":
        return None
    try:
        n, theta, omega = int(fields["N"]), float(fields["THETA"]), float(fields["OMEGA"])
        tared, active = fields["TARED"], fields["OSC_AUTO"]
        if (n < 0 or not math.isfinite(theta) or not math.isfinite(omega) or omega < 0
                or not -90 <= theta <= 90 or tared not in ("0", "1") or active not in ("0", "1")
                or (tared == "1" and abs(theta) > SWEEP_LIMIT_DEG + 0.01)):
            raise ValueError
        return dict(n=n, theta=theta, omega=omega, tared=tared == "1", active=active == "1")
    except (ValueError, KeyError) as error:
        raise ProtocolError("Invalid oscillator telemetry.") from error


def winding_status(fields):
    if fields.get("WIND_V") != "1":
        return None
    try:
        layers, radius = int(fields["LAYERS"]), float(fields["RADIUS"])
        ready, active = fields["WIND_READY"], fields["WIND_ACTIVE"]
        if layers < 0 or not math.isfinite(radius) or radius <= 0 or ready not in ("0", "1") or active not in ("0", "1"):
            raise ValueError
        return dict(layers=layers, radius=radius, ready=ready == "1", active=active == "1")
    except (ValueError, KeyError) as error:
        raise ProtocolError("Invalid winding telemetry.") from error


class MegaWorker(threading.Thread):
    """Single USB owner; camera freshness checks run independently of the GUI."""
    def __init__(self, port, state, camera, events, diameter_filter=None, takeup=None):
        super().__init__(daemon=True)
        self.port_name, self.state, self.camera, self.events = port, state, camera, events
        self.stop = threading.Event()
        self.controller = None
        self.ready = self.cl_ready = False
        self.osc = None
        self.winding = None
        self.start_angle_supported = False
        self.pause_supported = False
        self.angle_supported = False
        self.connection = None
        self.diameter_filter = diameter_filter if diameter_filter is not None else DiameterWindow()
        self.takeup = takeup if takeup is not None else TakeupIntegrator()

    def transact(self, command, expected, timeout=0.8):
        self.connection.write((command + "\n").encode("ascii"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.connection.readline().decode("ascii", errors="replace").strip()
            if line.startswith("ERR") or line == "WATCHDOG_STOP":
                raise ProtocolError(line)
            if line.startswith(expected):
                return line
        raise ProtocolError(f"No acknowledgement for {command.split()[0]}.")

    def get_status(self):
        fields = parse_status(self.transact("STATUS", "STATUS "))
        self.cl_ready = fields["CL_READY"] == "1"
        self.osc = oscillator_status(fields)
        self.winding = winding_status(fields)
        self.start_angle_supported = fields.get("SWEEP_V") == "1"
        self.pause_supported = fields.get("PAUSE_V") == "1"
        self.angle_supported = fields.get("ANGLE_V") == "1"
        radius = self.winding["radius"] if self.winding else self.state.snapshot()["winding_geometry"].radius(0)
        velocity, _ = self.takeup.observe(time.monotonic(), float(fields["DM"]), radius)
        fields.update(TAKEUP_MM_S=str(velocity), LENGTH_MM=str(self.takeup.extruded_length_mm))
        return fields

    def run(self):
        try:
            self.connection = serial.Serial(self.port_name, 115200, timeout=0.1, write_timeout=0.5)
            try:
                if self.stop.wait(2.2):
                    return
                self.transact("PING", "PONG")
                self.transact("STOP", "OK STOP")
                fields = self.get_status()
                self.ready = True
                self.events.put(("connected", fields))
                last_commands = {}
                initial = self.state.snapshot()
                last_stop, last_tare = initial["stop_revision"], initial["tare_request"]
                last_angle = initial["servo_angle_request"]
                last_status = time.monotonic()
                armed, last_cl = False, 0.0
                manual_identity = manual_speed = None
                last_hold = None
                last_winding_tare = initial["winding_tare_request"]
                configured_geometry = None
                while not self.stop.is_set():
                    camera_packet, _ = self.camera.snapshot()
                    self.diameter_filter.observe(camera_packet)
                    v = self.state.snapshot()
                    if v["stop_revision"] != last_stop:
                        self.transact("STOP", "OK STOP")
                        last_commands.clear()
                        last_stop = v["stop_revision"]
                        last_tare = v["tare_request"]
                        last_winding_tare = v["winding_tare_request"]
                        armed, self.controller, last_cl = False, None, 0.0
                        manual_identity = manual_speed = None
                        last_hold = None
                        last_angle = v["servo_angle_request"]
                        self.events.put(("status", self.get_status()))
                        last_status = time.monotonic()
                        continue
                    if v["winding_auto"] or v["winding_tare_request"] != last_winding_tare:
                        try:
                            if self.winding is None:
                                raise ValueError("Upload the updated Mega sketch for RPM-synchronised spooling.")
                            geometry = v["winding_geometry"]
                            geometry.validate()
                            if geometry != configured_geometry:
                                self.transact(geometry.command(), "OK WINDCFG")
                                configured_geometry = geometry
                                self.get_status()
                            if v["winding_tare_request"] != last_winding_tare:
                                if v["dm"] != 0 or v["spool_auto"]:
                                    raise ValueError("Stop the spooler before resetting the layer reference.")
                                self.transact("DMRPM 0", "OK DMRPM")
                                last_commands["DMRPM"] = "DMRPM 0.000"
                                self.transact("WINDTARE", "OK WINDTARE")
                                self.takeup.reset()
                                last_winding_tare = v["winding_tare_request"]
                                last_commands.pop("SERVO", None)
                                self.events.put(("winding_tared", self.get_status()))
                            if v["winding_auto"] and not v["servo_hold"] and not self.winding["ready"]:
                                raise ValueError("Reset layers at the minimum angle before starting the automatic sweep.")
                        except (ValueError, OverflowError) as error:
                            self.state.set(winding_auto=False, servo=None)
                            last_winding_tare = v["winding_tare_request"]
                            self.events.put(("winding_fault", str(error)))
                            continue
                    if v["tare_request"] != last_tare:
                        if self.osc is None:
                            raise ProtocolError("Upload the updated Mega firmware before using Tare n.")
                        # Stop pulling before moving the directioner to its tare endpoint.
                        self.transact("CLRPM 0", "OK CLRPM")
                        last_cl = 0.0
                        last_commands.pop("CLRPM", None)
                        self.transact("TARE", "OK TARE")
                        fields = self.get_status()
                        if self.osc is None or not self.osc["tared"] or self.osc["n"] != 0:
                            raise ProtocolError("Mega did not confirm the oscillator tare.")
                        last_tare = v["tare_request"]
                        last_commands.pop("SERVO", None)
                        self.events.put(("tare_complete", fields))
                    cl = 0.0 if v["puller_disabled"] else v["cl"]
                    if v["auto"] and not v["puller_disabled"]:
                        packet, _ = self.camera.snapshot()
                        try:
                            if not self.cl_ready:
                                raise ValueError("Mega reports CL42T hardware unavailable.")
                            settings = v["automatic"]
                            settings.validate()
                            if self.controller is None:
                                self.controller = ProportionalPuller(settings, initial_output=max(0.0, last_cl),
                                                                     diameter_filter=self.diameter_filter)
                            self.controller.settings = settings
                            cl = round(self.controller.update(packet), 3)
                            vf = linear_velocity(cl, settings.wheel_diameter)
                            layers = self.winding["layers"] if self.winding is not None else 0
                            target_spool = spool_rpm_command(v["winding_geometry"], layers, vf)["rpm"]
                            self.events.put(("auto_output", dict(
                                rpm=cl, vf=vf, spooler_rpm=target_spool,
                                filtered_diameter=self.controller.measurement,
                                sample_count=self.controller.sample_count,
                                effective_error=self.controller.effective_error,
                                **(self.osc or {}))))
                        except (ValueError, OverflowError) as error:
                            self.state.stop_all()
                            self.events.put(("auto_fault", str(error)))
                            continue
                    else:
                        self.controller = None
                    dm = v["dm"]
                    if v["spool_auto"]:
                        # Layer counting is provided by the RPM-synchronised traverser.
                        # Hold the spooler if that traverser is paused or stopped.
                        if self.winding is None or not self.winding["ready"]:
                            self.state.set(spool_auto=False, dm=0)
                            self.events.put(("spool_fault", "Reset extrusion/layers before automatic spooling."))
                            continue
                        if not v["winding_auto"] or v["servo_hold"]:
                            dm = 0.0
                        else:
                            vf = linear_velocity(abs(cl), v["automatic"].wheel_diameter)
                            result = spool_rpm_command(v["winding_geometry"], self.winding["layers"], vf,
                                                       baseline_rpm=v["spool_baseline_rpm"] if v["puller_disabled"] else None,
                                                       direction=v["spool_direction"], limit=MAX_DM_RPM)
                            dm = result["rpm"]
                            self.events.put(("spool_output", result))
                    if v["arm"] and not armed:
                        self.transact("ARM", "OK ARM")
                        armed = True
                    if v["spark"] and not armed:
                        raise ProtocolError("Extruder must be armed before starting.")
                    if (not math.isfinite(cl) or not math.isfinite(dm)
                            or abs(cl) > MAX_RPM or abs(dm) > MAX_DM_RPM
                            or not -100 <= v["spark"] <= 100 or not 0 <= v["fan"] <= 255):
                        raise ProtocolError("Requested motor output exceeds firmware limits.")
                    if cl and not self.cl_ready:
                        raise ProtocolError("CL42T hardware unavailable.")
                    commands = [("CLRPM", f"CLRPM {cl:.3f}", "OK CLRPM"),
                                ("DMRPM", f"DMRPM {dm:.3f}", "OK DMRPM"),
                                ("SPARK", f"SPARK {v['spark']}", "OK SPARK"),
                                ("FAN", f"FAN {v['fan']}", "OK FAN")]
                    next_manual_identity = next_manual_speed = None
                    if v["servo_hold"]:
                        if not self.pause_supported:
                            raise ProtocolError("Upload the updated Mega sketch for Pause/Resume and Center/Hold.")
                        # A fast Pause click can arrive before the first sweep ACK.
                        # Establish that command once, then hold it in the same batch.
                        if v["servo_hold"] == "paused" and last_hold is None:
                            if isinstance(v["servo"], tuple) and manual_identity is None:
                                center, speed, bounds, start = v["servo"]
                                self.transact(f"SWEEP {center:.2f} {speed:.2f} {bounds:.2f} {start:.2f}", "OK SWEEP")
                                manual_identity = (v["servo_revision"], center, bounds, start)
                                manual_speed = speed
                            elif v["winding_auto"] and last_commands.get("SERVO") != "WINDSTART":
                                self.transact("WINDSTART", "OK WINDSTART")
                        if v["servo_hold"] != "angle_hold" and not (v["servo_hold"] == "centered" and v["servo_angle_target"] is not None):
                            cmd = "SWEEPCENTER" if v["servo_hold"] == "centered" else "SWEEPPAUSE"
                            commands.append(("SERVO", cmd, "OK " + cmd))
                    elif v["winding_auto"]:
                        commands.append(("SERVO", "WINDSTART", "OK WINDSTART"))
                    elif v["servo"] == "auto_hold":
                        if self.winding is not None and self.winding["ready"]:
                            commands.append(("SERVO", "WINDPAUSE", "OK WINDPAUSE"))
                        elif self.osc is not None and self.osc["tared"]:
                            commands.append(("SERVO", "AUTOPAUSE", "OK AUTOPAUSE"))
                        else:
                            commands.append(("SERVO", "SERVOSTOP", "OK SERVOSTOP"))
                    elif v["servo"] is None:
                        commands.append(("SERVO", "SERVOSTOP", "OK SERVOSTOP"))
                    else:
                        center, speed, bounds = v["servo"][:3]
                        if (not all(math.isfinite(x) for x in v["servo"])
                                or not -90 <= center - bounds <= center + bounds <= 90
                                or not 0 < speed <= 360 or bounds < 0):
                            raise ProtocolError("Servo settings exceed firmware limits.")
                        if len(v["servo"]) == 4:
                            if not self.start_angle_supported:
                                raise ProtocolError("Upload the updated Mega sketch for servo starting-angle control.")
                            start = v["servo"][3]
                            if not center - bounds <= start <= center + bounds:
                                raise ProtocolError("Servo starting angle must be inside the sweep limits.")
                            next_manual_identity = (v["servo_revision"], center, bounds, start)
                            next_manual_speed = speed
                            if last_hold in ("centered", "angle_hold"):
                                commands.append(("SERVO", f"SWEEPCONT {center:.2f} {speed:.2f} {bounds:.2f} {start:.2f}", "OK SWEEPCONT"))
                            elif last_hold == "paused" and next_manual_identity == manual_identity:
                                if speed != manual_speed:
                                    last_commands.pop("SERVO_SPEED", None)
                                    commands.append(("SERVO_SPEED", f"SWEEPSPEED {speed:.2f}", "OK SWEEPSPEED"))
                                commands.append(("SERVO", "SWEEPRESUME", "OK SWEEPRESUME"))
                            elif next_manual_identity != manual_identity:
                                last_commands.pop("SERVO", None)  # Explicit Apply can restart unchanged settings.
                                commands.append(("SERVO", f"SWEEP {center:.2f} {speed:.2f} {bounds:.2f} {start:.2f}", "OK SWEEP"))
                            elif speed != manual_speed:
                                commands.append(("SERVO", f"SWEEPSPEED {speed:.2f}", "OK SWEEPSPEED"))
                        else:
                            commands.append(("SERVO", f"SERVO {center:.2f} {speed:.2f} {bounds:.2f}", "OK SERVO"))
                    if v["servo_angle_target"] is not None and v["servo_angle_request"] != last_angle:
                        if not self.angle_supported:
                            raise ProtocolError("Upload the updated Mega sketch for Set Angle.")
                        angle = v["servo_angle_target"]
                        if not math.isfinite(angle) or not -90 <= angle <= 90:
                            raise ProtocolError("Servo angle must be within -90..90 degrees.")
                        last_commands.pop("SERVO_ANGLE", None)
                        commands.append(("SERVO_ANGLE", f"SWEEPANGLE {angle:.2f} {int(v['servo_angle_hold'])}", "OK SWEEPANGLE"))
                    for key, command, expected in commands:
                        if self.stop.is_set() or self.state.snapshot()["stop_revision"] != last_stop:
                            break
                        if key == "SERVO_ANGLE":
                            latest = self.state.snapshot()
                            if latest["servo_angle_target"] is None or latest["servo_angle_request"] != v["servo_angle_request"]:
                                continue  # STOP/reset or a newer angle superseded this request.
                        if last_commands.get(key) != command:
                            self.transact(command, expected)
                            last_commands[key] = command
                            if key == "CLRPM":
                                last_cl = cl
                            elif key == "SERVO":
                                if v["servo_hold"]:
                                    last_hold = v["servo_hold"]
                                else:
                                    last_hold = None
                                    manual_identity, manual_speed = next_manual_identity, next_manual_speed
                            elif key == "SERVO_ANGLE":
                                last_angle = v["servo_angle_request"]
                                if v["servo_hold"]:
                                    last_hold = v["servo_hold"]
                    if time.monotonic() - last_status >= 0.5:
                        fields = self.get_status()
                        if (not self.cl_ready and cl != 0) or (armed and fields["ARM"] != "1"):
                            raise ProtocolError("Mega lost its motor-ready/armed state.")
                        self.events.put(("status", fields))
                        last_status = time.monotonic()
                    self.stop.wait(0.1)
            finally:
                try:
                    self.connection.write(b"STOP\n")
                finally:
                    self.connection.close()
        except (serial.SerialException, OSError, ValueError, ProtocolError) as error:
            self.state.stop_all()
            self.events.put(("error", str(error)))
        finally:
            self.ready = False
            self.takeup.break_interval()
            self.events.put(("disconnected", None))
