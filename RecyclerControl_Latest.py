
import tkinter as tk
from tkinter import ttk, messagebox
import serial
from serial.tools import list_ports
import threading
import queue
import time
import math

# =========================================
# RECYCLER PYTHON CONTROL INTERFACE
# =========================================

BAUD = 115200

BG = "#101827"
PANEL = "#1e293b"
FG = "#f8fafc"
MUTED = "#94a3b8"
BLUE = "#2563eb"
RED = "#b91c1c"


class Recycler:

    def __init__(self, root):
        self.root = root

        root.title("Recycler | Motor Control")
        root.geometry("780x850")
        root.minsize(720, 740)
        root.configure(bg=BG)

        self.ser = None
        self.worker = None

        self.closing = threading.Event()

        self.outgoing = queue.Queue()
        self.incoming = queue.Queue()

        self.connected = False
        self.ready = False
        self.armed = False
        self.cl_ready = False

        self.port = tk.StringVar()

        self.status = tk.StringVar(
            value="Disconnected"
        )

        self.response = tk.StringVar(
            value="Connect to Arduino Mega."
        )

        self.values = {}

        self.make_ui()
        self.refresh_ports()

        root.after(100, self.poll)

        root.protocol(
            "WM_DELETE_WINDOW", self.close
        )

    # =====================================
    # BUTTON HELPER
    # =====================================

    def btn(self, parent, label,
            action, color=BLUE):

        return tk.Button(
            parent,
            text=label,
            command=action,
            bg=color,
            fg="white",
            activebackground=color,
            activeforeground="white",
            relief="flat",
            padx=13,
            pady=9,
            font=("Segoe UI", 10, "bold")
        )

    # =====================================
    # BUILD INTERFACE
    # =====================================

    def make_ui(self):

        tk.Label(
            self.root,
            text="RECYCLER",
            bg=BG,
            fg=FG,
            font=("Segoe UI", 25, "bold")
        ).pack(
            anchor="w",
            padx=25,
            pady=(18, 0)
        )

        tk.Label(
            self.root,
            text="Arduino Mega 2560 | RPM Control",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 11)
        ).pack(anchor="w", padx=25)

        # Connection panel

        top = tk.Frame(
            self.root,
            bg=PANEL,
            padx=15,
            pady=14
        )

        top.pack(
            fill="x",
            padx=25,
            pady=16
        )

        line = tk.Frame(top, bg=PANEL)
        line.pack(fill="x")

        self.ports = ttk.Combobox(
            line,
            textvariable=self.port,
            state="readonly",
            width=16
        )

        self.ports.pack(
            side="left",
            padx=(0, 8)
        )

        self.btn(
            line,
            "Refresh",
            self.refresh_ports,
            "#475569"
        ).pack(side="left", padx=3)

        self.btn(
            line,
            "Connect",
            self.connect
        ).pack(side="left", padx=3)

        self.btn(
            line,
            "Disconnect",
            self.disconnect,
            "#475569"
        ).pack(side="left", padx=3)

        tk.Label(
            top,
            textvariable=self.status,
            bg=PANEL,
            fg="#7dd3fc",
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(10, 0))

        # Device grid

        grid = tk.Frame(
            self.root,
            bg=BG
        )

        grid.pack(
            fill="both",
            expand=True,
            padx=20
        )

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        devices = [
            (
                "DM556",
                "Stepper - signed RPM",
                "DMRPM",
                -20, 20
            ),
            (
                "CL42T",
                "Stepper - fault lockout active",
                "CLRPM",
                -20, 20
            ),
            (
                "FANS",
                "PWM output - 0 to 255",
                "FAN",
                0, 255
            ),
            (
                "SPARK MAX",
                "RC command - percent",
                "SPARK",
                -100, 100
            )
        ]

        for index, device in enumerate(devices):

            name, subtitle, command, lo, hi = device

            card = tk.Frame(
                grid,
                bg=PANEL,
                padx=16,
                pady=14
            )

            card.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=5,
                pady=5
            )

            tk.Label(
                card,
                text=name,
                bg=PANEL,
                fg=FG,
                font=("Segoe UI", 14, "bold")
            ).pack(anchor="w")

            tk.Label(
                card,
                text=subtitle,
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 9)
            ).pack(
                anchor="w",
                pady=(3, 12)
            )

            value = tk.StringVar(value="0")
            self.values[command] = value

            row = tk.Frame(card, bg=PANEL)
            row.pack(fill="x")

            tk.Entry(
                row,
                textvariable=value,
                font=("Segoe UI", 17),
                bg="#334155",
                fg=FG,
                insertbackground=FG,
                relief="flat",
                width=10
            ).pack(side="left")

            if command.endswith("RPM"):
                unit = "RPM"
            elif command == "FAN":
                unit = "PWM"
            else:
                unit = "%"

            tk.Label(
                row,
                text=unit,
                bg=PANEL,
                fg=MUTED
            ).pack(side="left", padx=10)

            tk.Label(
                card,
                text=f"Allowed: {lo} to {hi}",
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 9)
            ).pack(
                anchor="w",
                pady=(8, 5)
            )

            button = self.btn(
                card,
                "Apply",
                lambda c=command, a=lo, b=hi:
                    self.apply(c, a, b)
            )

            button.pack(fill="x")

            if command == "CLRPM":
                button.configure(state="disabled")
                self.cl_button = button

        # D10 servo controls (independent of existing motor cards)
        servo_card = tk.Frame(
            self.root, bg=PANEL, padx=16, pady=12
        )
        servo_card.pack(fill="x", padx=25, pady=(10, 0))

        tk.Label(
            servo_card, text="D10 SERVO SWEEP",
            bg=PANEL, fg=FG,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w")

        tk.Label(
            servo_card,
            text="Center offset, angular speed, +/- sweep range",
            bg=PANEL, fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 8))

        self.servo_center = tk.StringVar(value="0")
        self.servo_speed = tk.StringVar(value="30")
        self.servo_bounds = tk.StringVar(value="45")

        servo_fields = tk.Frame(servo_card, bg=PANEL)
        servo_fields.pack(fill="x")
        for col, (label, variable) in enumerate((
            ("Center (deg)", self.servo_center),
            ("Speed (deg/s)", self.servo_speed),
            ("Bounds (+/- deg)", self.servo_bounds)
        )):
            field = tk.Frame(servo_fields, bg=PANEL)
            field.grid(row=0, column=col, padx=(0, 16), sticky="w")
            tk.Label(
                field, text=label, bg=PANEL, fg=MUTED,
                font=("Segoe UI", 9)
            ).pack(anchor="w")
            tk.Entry(
                field, textvariable=variable,
                font=("Segoe UI", 13), width=12,
                bg="#334155", fg=FG,
                insertbackground=FG, relief="flat"
            ).pack(anchor="w", pady=(3, 0))

        servo_buttons = tk.Frame(servo_card, bg=PANEL)
        servo_buttons.pack(fill="x", pady=(10, 0))
        self.btn(
            servo_buttons, "Start / Apply Sweep", self.apply_servo
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn(
            servo_buttons, "Stop Servo", self.stop_servo, "#475569"
        ).pack(side="left", fill="x", expand=True)

        # Bottom controls

        controls = tk.Frame(
            self.root,
            bg=BG
        )

        controls.pack(
            fill="x",
            padx=25,
            pady=(12, 4)
        )

        self.btn(
            controls,
            "Arm SPARK MAX",
            self.arm,
            "#475569"
        ).pack(side="left")

        self.btn(
            controls,
            "Read Status",
            lambda: self.send("STATUS"),
            "#475569"
        ).pack(side="left", padx=8)

        self.btn(
            self.root,
            "STOP ALL",
            self.stop,
            RED
        ).pack(
            fill="x",
            padx=25,
            pady=12
        )

        tk.Label(
            self.root,
            textvariable=self.response,
            bg=BG,
            fg="#7dd3fc",
            font=("Consolas", 10),
            wraplength=730
        ).pack(
            padx=25,
            pady=(0, 16)
        )

    # =====================================
    # SERIAL PORTS
    # =====================================

    def refresh_ports(self):

        ports = [
            p.device
            for p in list_ports.comports()
        ]

        self.ports["values"] = ports

        if ports and self.port.get() not in ports:
            self.port.set(ports[0])

    # =====================================
    # CONNECT
    # =====================================

    def connect(self):

        if self.connected:
            return

        if not self.port.get():
            messagebox.showerror(
                "Port",
                "Select a COM port."
            )
            return

        try:
            self.ser = serial.Serial(
                self.port.get(),
                BAUD,
                timeout=0.1,
                write_timeout=0.5
            )

        except (serial.SerialException, OSError) as e:
            messagebox.showerror(
                "Connection failed",
                str(e)
            )
            return

        self.closing.clear()

        self.connected = True
        self.ready = False
        self.armed = False

        self.status.set(
            "Waiting for Arduino startup..."
        )

        self.worker = threading.Thread(
            target=self.serial_worker,
            daemon=True
        )

        self.worker.start()

    # =====================================
    # SERIAL WORKER
    # =====================================

    def serial_worker(self):

        started = time.monotonic()
        next_ping = started + 2.5

        partial = bytearray()

        try:
            while not self.closing.is_set():

                now = time.monotonic()

                if now >= next_ping:
                    self.outgoing.put("PING")
                    next_ping = now + 0.5

                try:
                    command = self.outgoing.get_nowait()
                except queue.Empty:
                    command = None

                if command:
                    self.ser.write(
                        (command + "\n").encode("ascii")
                    )

                chunk = self.ser.read(
                    self.ser.in_waiting or 1
                )

                if chunk:
                    partial.extend(chunk)

                    while b"\n" in partial:

                        line, _, rest = partial.partition(
                            b"\n"
                        )

                        partial = bytearray(rest)

                        self.incoming.put(
                            line.decode(
                                errors="replace"
                            ).strip()
                        )

                    if len(partial) > 512:
                        partial.clear()

        except (serial.SerialException, OSError) as e:
            self.incoming.put(
                "CONNECTION_LOST " + str(e)
            )

        finally:
            try:
                self.ser.close()
            except Exception:
                pass

    # =====================================
    # SEND COMMAND
    # =====================================

    def send(self, command):

        if not self.connected or not self.ready:
            self.response.set(
                "Arduino not ready."
            )
            return False

        self.outgoing.put(command)

        self.response.set(
            "Sent: " + command
        )

        return True

    # =====================================
    # APPLY MOTOR VALUE
    # =====================================

    def apply(self, command, lo, hi):

        try:
            value = float(
                self.values[command].get()
            )

            if not math.isfinite(value):
                raise ValueError()

            if not lo <= value <= hi:
                raise ValueError()

            if command in ("FAN", "SPARK"):
                if not value.is_integer():
                    raise ValueError()

        except ValueError:
            messagebox.showwarning(
                "Invalid input",
                f"Enter a number from {lo} to {hi}."
            )
            return

        if command == "CLRPM":
            if not self.cl_ready and value != 0:
                messagebox.showwarning(
                    "CL42T fault",
                    "CL42T is locked out."
                )
                return

        if command == "SPARK":
            if value != 0 and not self.armed:
                messagebox.showwarning(
                    "SPARK MAX",
                    "Arm SPARK MAX first."
                )
                return

        if value.is_integer():
            number = str(int(value))
        else:
            number = str(value)

        self.send(
            command + " " + number
        )

    # =====================================
    # D10 SERVO SWEEP
    # =====================================

    def apply_servo(self):
        try:
            center = float(self.servo_center.get())
            speed = float(self.servo_speed.get())
            bounds = float(self.servo_bounds.get())
            if not all(math.isfinite(x) for x in (center, speed, bounds)):
                raise ValueError()
            if (not -90 <= center <= 90 or
                    not 0 < speed <= 360 or
                    not 0 <= bounds <= 90 or
                    center - bounds < -90 or
                    center + bounds > 90):
                raise ValueError()
        except ValueError:
            messagebox.showwarning(
                "Invalid servo settings",
                "Center must be -90..90, speed >0..360 deg/s, "
                "bounds 0..90, and center +/- bounds within -90..90."
            )
            return

        def fmt(x):
            return str(int(x)) if x.is_integer() else str(x)

        self.send("SERVO " + " ".join(fmt(x) for x in (center, speed, bounds)))

    def stop_servo(self):
        self.send("SERVOSTOP")

    # =====================================
    # ARM SPARK MAX
    # =====================================

    def arm(self):

        confirmed = messagebox.askyesno(
            "Arm SPARK MAX",
            "Is the motor area clear, and have "
            "you verified RC PWM neutral?"
        )

        if not confirmed:
            return

        if self.send("ARM"):
            self.response.set(
                "Waiting for Arduino ARM confirmation."
            )

    # =====================================
    # STOP ALL
    # =====================================

    def stop(self):

        # Clear pending commands before STOP
        while True:
            try:
                self.outgoing.get_nowait()
            except queue.Empty:
                break

        if self.connected:
            self.outgoing.put("STOP")

        self.armed = False

        for variable in self.values.values():
            variable.set("0")

        self.response.set(
            "STOP requested."
        )

    # =====================================
    # PROCESS ARDUINO RESPONSES
    # =====================================

    def poll(self):

        try:
            while True:

                line = self.incoming.get_nowait()

                if line in (
                    "RECYCLER_READY_V2",
                    "PONG"
                ):
                    if not self.ready:
                        self.ready = True
                        self.status.set(
                            "Arduino responding at 115200 baud"
                        )
                        self.send("STATUS")

                elif line.startswith("STATUS "):

                    fields = dict(
                        item.split("=", 1)
                        for item in line.split()[1:]
                        if "=" in item
                    )

                    self.cl_ready = (
                        fields.get("CL_READY") == "1"
                    )

                    self.cl_button.configure(
                        state=(
                            "normal"
                            if self.cl_ready
                            else "disabled"
                        )
                    )

                    self.response.set(line)

                elif line == "OK ARM":
                    self.armed = True
                    self.response.set(
                        "SPARK MAX armed in software"
                    )

                elif line.startswith("ERR"):
                    self.response.set(line)

                elif line == "WATCHDOG_STOP":
                    self.armed = False
                    self.response.set(
                        "WATCHDOG_STOP"
                    )

                elif line.startswith("CONNECTION_LOST"):
                    self.status.set(line)
                    self.connected = False
                    self.ready = False
                    self.armed = False

                elif line and line != "PONG":
                    self.response.set(line)

        except queue.Empty:
            pass

        self.root.after(
            100,
            self.poll
        )

    # =====================================
    # DISCONNECT
    # =====================================

    def disconnect(self):

        if self.connected:
            self.stop()

            self.root.after(
                200,
                self.finish_disconnect
            )
        else:
            self.finish_disconnect()

    def finish_disconnect(self):

        self.closing.set()

        self.connected = False
        self.ready = False
        self.armed = False

        self.status.set("Disconnected")

    # =====================================
    # CLOSE
    # =====================================

    def close(self):

        if self.connected:
            self.stop()

            self.root.after(
                250,
                self.close_now
            )
        else:
            self.close_now()

    def close_now(self):

        self.closing.set()
        self.root.destroy()


if __name__ == "__main__":

    root = tk.Tk()

    Recycler(root)

    root.mainloop()
