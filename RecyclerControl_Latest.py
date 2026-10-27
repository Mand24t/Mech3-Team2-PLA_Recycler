"""PLA recycler controller - matching RecyclerMega_Latest.ino (Arduino Mega)."""
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import serial

# Change to the Arduino Mega COM port from Device Manager or Arduino IDE.
PORT = "COM5"
BAUD = 115200
SEND_PERIOD = 0.15

lock = threading.Lock()
quit_flag = threading.Event()
notices = queue.Queue()
values = {
    "extruder": 0.0, "stepper": 0.0, "fan": 0.0,
    "ex_on": False, "st_on": False, "fan_on": False,
    "reverse": False,
    "servo_mode": 0,   # 0=detach, 1=center, 2=sweep, 3=hold
    "servo_range": 10.0, "servo_speed": 15.0, "servo_offset": 0.0,
}
connected = False


def serial_worker():
    try:
        with serial.Serial(PORT, BAUD, timeout=0.2, write_timeout=1.0) as port:
            time.sleep(2.2)  # Mega typically resets when USB serial opens.
            if quit_flag.is_set():
                return
            notices.put(("connected", f"Connected: {PORT}"))
            while not quit_flag.is_set():
                with lock:
                    v = values.copy()
                ex = v["extruder"] if v["ex_on"] else 0.0
                st = v["stepper"] if v["st_on"] else 0.0
                if v["reverse"]:
                    st = -st
                fan = v["fan"] if v["fan_on"] else 0.0
                command = (f"SET,{ex:.2f},{st:.2f},{fan:.1f},"
                           f"{v['servo_mode']},{v['servo_range']:.2f},"
                           f"{v['servo_speed']:.2f},{v['servo_offset']:.2f}\n")
                port.write(command.encode("ascii"))
                quit_flag.wait(SEND_PERIOD)
            try:
                port.write(b"STOP\n")
                port.flush()
            except (serial.SerialException, OSError):
                pass
    except (serial.SerialException, OSError) as error:
        notices.put(("error", str(error)))


root = tk.Tk()
root.title("PLA Recycler | Mega Control")
root.geometry("570x780")
main = ttk.Frame(root, padding=15)
main.pack(fill="both", expand=True)
ttk.Label(main, text="PLA RECYCLER CONTROL", font=("Arial", 17, "bold")).pack(anchor="w")
connection_label = ttk.Label(main, text=f"Connecting to {PORT} ...")
connection_label.pack(anchor="w", pady=(0, 8))
status = {}
start_buttons = []


def set_enabled(key, enabled):
    if enabled and not connected:
        messagebox.showwarning("Not connected", "Connect to the Arduino first.")
        return
    with lock:
        values[key] = enabled
    status[key].config(text="RUNNING" if enabled else "STOPPED")


def add_speed(title, key, enable_key, max_value, unit, resolution, reverse=False):
    frame = ttk.LabelFrame(main, text=title, padding=7)
    frame.pack(fill="x", pady=5)
    var = tk.DoubleVar(value=0)
    show = ttk.Label(frame, text=f"0.0 {unit}")
    show.pack(anchor="e")

    def changed(value):
        number = float(value)
        with lock:
            values[key] = number
        show.config(text=f"{number:.1f} {unit}")

    tk.Scale(frame, from_=0, to=max_value, resolution=resolution,
             orient="horizontal", showvalue=False, length=480,
             variable=var, command=changed).pack()
    controls = ttk.Frame(frame)
    controls.pack(fill="x")
    entry = ttk.Entry(controls, width=7)
    entry.insert(0, "0")
    entry.pack(side="left", padx=(0, 4))

    def apply_entry():
        try:
            speed = float(entry.get())
            if not math.isfinite(speed) or not 0 <= speed <= max_value:
                raise ValueError
            var.set(speed)
            changed(speed)
        except ValueError:
            messagebox.showerror("Invalid value", f"Enter 0 to {max_value} {unit}.")

    ttk.Button(controls, text="Set", command=apply_entry).pack(side="left")
    start = ttk.Button(controls, text="START", command=lambda: set_enabled(enable_key, True))
    start.pack(side="left", padx=6)
    start_buttons.append(start)
    ttk.Button(controls, text="STOP", command=lambda: set_enabled(enable_key, False)).pack(side="left")
    if reverse:
        reverse_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Reverse", variable=reverse_var,
                        command=lambda: update_setting("reverse", reverse_var.get())).pack(side="left", padx=6)
    status[enable_key] = ttk.Label(controls, text="STOPPED")
    status[enable_key].pack(side="right")


def update_setting(key, value):
    with lock:
        values[key] = value


add_speed("Extruder screw (125:1, reversed PWM)", "extruder", "ex_on", 40.0, "estimated RPM", 0.1)
add_speed("CL42T stepper - D30 STEP / D31 DIR", "stepper", "st_on", 60.0, "RPM", 0.5, True)
add_speed("Cooling fans - DRV8871 D6/D7", "fan", "fan_on", 100.0, "%", 1)

servo_frame = ttk.LabelFrame(main, text="HPS-2027 Servo - D10 (unverified; starts detached)", padding=10)
servo_frame.pack(fill="x", pady=8)
settings_row = ttk.Frame(servo_frame)
settings_row.pack(fill="x")
servo_entries = {}
for label, key, default in [
    ("Offset (deg)", "servo_offset", "0"),
    ("+/- Range (deg)", "servo_range", "10"),
    ("Speed (deg/s)", "servo_speed", "15"),
]:
    cell = ttk.Frame(settings_row)
    cell.pack(side="left", padx=7)
    ttk.Label(cell, text=label).pack()
    entry = ttk.Entry(cell, width=10)
    entry.insert(0, default)
    entry.pack()
    servo_entries[key] = entry


def set_servo(mode):
    if mode != 0 and not connected:
        messagebox.showwarning("Not connected", "Connect to the Arduino first.")
        return
    try:
        offset = float(servo_entries["servo_offset"].get())
        move_range = float(servo_entries["servo_range"].get())
        speed = float(servo_entries["servo_speed"].get())
        center = 135 + offset
        if (not all(math.isfinite(v) for v in (offset, move_range, speed))
                or not (0 <= move_range <= 125)
                or not (0 < speed <= 180)
                or center - move_range < 10 or center + move_range > 260):
            raise ValueError
    except ValueError:
        messagebox.showerror("Servo settings", "Keep 135 + offset +/- range within 10–260 degrees; "
                             "range 0–125 degrees and speed >0 to 180 degrees/s.")
        return
    with lock:
        values.update(servo_offset=offset, servo_range=move_range,
                      servo_speed=speed, servo_mode=mode)
    servo_status.config(text={0: "DETACHED", 1: "CENTER", 2: "SWEEPING", 3: "HOLD"}[mode])


servo_controls = ttk.Frame(servo_frame)
servo_controls.pack(pady=8)
ttk.Button(servo_controls, text="CENTER", command=lambda: set_servo(1)).pack(side="left", padx=4)
ttk.Button(servo_controls, text="START SWEEP", command=lambda: set_servo(2)).pack(side="left", padx=4)
ttk.Button(servo_controls, text="HOLD", command=lambda: set_servo(3)).pack(side="left", padx=4)
ttk.Button(servo_controls, text="SERVO OFF", command=lambda: set_servo(0)).pack(side="left", padx=4)
servo_status = ttk.Label(servo_frame, text="DETACHED")
servo_status.pack()


def stop_all():
    with lock:
        values["ex_on"] = values["st_on"] = values["fan_on"] = False
        values["servo_mode"] = 0
    for label in status.values():
        label.config(text="STOPPED")
    servo_status.config(text="DETACHED")


tk.Button(main, text="STOP ALL (software command)", bg="#b91c1c", fg="white",
          font=("Arial", 12, "bold"), command=stop_all).pack(fill="x", pady=9)
ttk.Label(main, text="Commanded RPM is not measured RPM. A physical E-stop is required.").pack(anchor="w")


def poll():
    global connected
    try:
        while True:
            kind, info = notices.get_nowait()
            if kind == "connected":
                connected = True
                connection_label.config(text=info)
            elif kind == "error":
                connected = False
                stop_all()
                connection_label.config(text=f"CONNECTION ERROR: {info}")
                messagebox.showerror("Serial connection", info)
    except queue.Empty:
        pass
    if not quit_flag.is_set():
        root.after(100, poll)


def on_close():
    stop_all()
    quit_flag.set()
    root.after(300, root.destroy)


root.protocol("WM_DELETE_WINDOW", on_close)
root.after(100, poll)
threading.Thread(target=serial_worker, daemon=True).start()
root.mainloop()
