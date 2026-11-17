"""Manual Mega controls and proportional puller / winding-oscillator equations."""
import math
import queue
import time
from pathlib import Path
from dataclasses import asdict
import json
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from serial.tools import list_ports
from OV7670Camera.measurement_link import MeasurementReceiver, live_diameter
from recycler_logic import ControlState, MegaWorker, oscillator_status, winding_status, MAX_RPM, MAX_DM_RPM
from spooling_control import SpoolGeometry, SETTINGS_PATH as SPOOL_SETTINGS_PATH, load_geometry, save_geometry, TakeupIntegrator, spool_rpm_command
from automatic_equations import (AutomaticSettings, DiameterWindow, AVERAGE_WINDOW,
                                 deadband_error, linear_velocity)
from diameter_recording import DiameterRecorder, RecordingExporter
from system_diagnostics import RecordingStatistics, MAX_WINDOW
from operating_configurations import OperatingConfigurationStore, CONFIGURATION_PATH, validate_configuration
from OV7670Camera.viewer_settings import load_settings as load_camera_settings, request_configuration, CONFIGURATION_RESULT_PATH
from OV7670Camera.gui_components import CollapsibleSection, TITLE_FONT, configure_styles, configure_app_identity, add_tooltip

camera_diameter_mm = None
camera_frame_sequence = None


class Recycler:
    def __init__(self, root, camera=None, spooling_settings_path=None, configuration_path=CONFIGURATION_PATH):
        self.root = root
        self.camera = camera if camera is not None else MeasurementReceiver()
        self.diameter_filter = DiameterWindow()
        self.filtered_diameter_text = tk.StringVar(value="Control average: collecting 0/20 readings.")
        self.recorder = DiameterRecorder()
        self.recording_exporter = RecordingExporter()
        self.recording_save_pending = False
        self.recording_save_dialog_active = False
        self.last_recording_save_directory = None
        self.recording_text = tk.StringVar(value="Not recording.")
        self.recording_progress_text = tk.StringVar(value="Elapsed: 00:00:00 | Points saved: 0")
        self.recording_statistics = RecordingStatistics()
        self.statistics_window = tk.IntVar(value=20)
        self.statistics_window_input = tk.StringVar(value="20")
        self.statistics_window_error = tk.StringVar(value="")
        self.minimum_diameter_text = tk.StringVar(value="N/A")
        self.maximum_diameter_text = tk.StringVar(value="N/A")
        self.statistics_text = {key: tk.StringVar(value="N/A") for key in
                                ("mean_error", "std_dev", "mean_std_dev", "spread_95", "ci_95")}
        self.statistics_status = tk.StringVar(value="Start Recording to collect statistics.")
        self.diagnostic_motor_text = {key: tk.StringVar(value="N/A")
                                      for key in ("cl", "dm", "spark", "fan", "servo")}
        self.configuration_store = OperatingConfigurationStore(configuration_path)
        self.configuration_selection = tk.StringVar()
        self.configuration_status = tk.StringVar(value="Save or load an operating setup.")
        self.configuration_temperature = tk.StringVar(value="")
        self.configuration_profiles = []
        self.camera_configuration_pending = None
        self.state = ControlState()
        self.state.set(puller_disabled=True)
        self.spooling_settings_path = Path(spooling_settings_path) if spooling_settings_path is not None else None
        geometry_error = ""
        try:
            geometry = load_geometry(self.spooling_settings_path) if self.spooling_settings_path is not None else SpoolGeometry()
        except (OSError, ValueError) as error:
            geometry, geometry_error = SpoolGeometry(), str(error)
        self.state.set(winding_geometry=geometry)
        self.spooling_mode = tk.StringVar(value="Manual")
        self.winding_supported = self.winding_ready = False
        self.pause_supported = self.angle_supported = False
        self.manual_sweep_settings = ((geometry.minimum_angle + geometry.maximum_angle)/2, 0.25,
                                      (geometry.maximum_angle - geometry.minimum_angle)/2, geometry.maximum_angle)
        self.geometry_needs_reference = True
        self.winding_layers = 0
        self.takeup = TakeupIntegrator()
        self.extruded_length_text = tk.StringVar(value=f"{self.takeup.extruded_length_mm:.1f}")
        self.spool_motor_mode = tk.StringVar(value="Manual")
        self.spool_diagnostics_text = tk.StringVar(value="Layers: 0 | Coil diameter: -- mm | Filament velocity: -- mm/s | Extruded length: 0.635 m")
        self.spool_motor_text = tk.StringVar(value="Manual mode. Automatic adjusts RPM as the coil grows.")
        self.reported_spooler_rpm = None
        self.spooling_status = tk.StringVar(value="Manual mode. Automatic sweep follows spooler RPM.")
        self.geometry_status = tk.StringVar(value=f"Could not restore geometry: {geometry_error}" if geometry_error else "")
        self.events = queue.Queue()
        self.worker = None
        self.connected = self.cl_ready = self.closing = False
        self.oscillator_supported = self.oscillator_tared = False
        self.oscillator_n = 0
        self.spooler_text = tk.StringVar(value="Calculated Spool RPM: N/A (Mega disconnected)")
        self.calculated_spool_rpm = None
        self.oscillator_text = tk.StringVar(value="Waiting for spool data.")
        self.velocity_text = tk.StringVar(value="Puller speed estimate: N/A mm/s")
        self.port = tk.StringVar()
        self.connection_text = tk.StringVar(value="")
        self.response_text = tk.StringVar(value="Outputs stopped. Puller disabled.")
        self.mode = tk.StringVar(value="Disabled")
        self.camera_diameter_text = tk.StringVar(value="N/A")
        self.camera_status_text = tk.StringVar(value="Waiting for camera viewer")
        self.auto_status = tk.StringVar(value="Puller disabled.")
        self.speed_vars, self.reverse_vars, self.motor_widgets = {}, {}, {}
        self.manual_running = dict.fromkeys(("cl", "dm", "spark", "fan"), False)
        self.motor_status = {}
        self.motor_start_buttons = {}
        self.motor_stop_buttons = {}
        self.motor_readouts = {}
        self.control_titles, self.control_indicators, self.control_indicator_text = {}, {}, {}
        self.control_indicator_states, self.control_indicator_details = {}, {}
        self.control_faults = {}
        self.link_failure = None
        self.disconnect_requested = False
        self.last_mega_status = None
        self.last_mega_status_at = None
        root.title("PLA Recycler | Manual / Automatic")
        root.geometry(f"1200x{min(1080, root.winfo_screenheight() - 70)}")
        root.minsize(min(1000, max(360, root.winfo_screenwidth()//2-32)),
                     min(600, max(360, root.winfo_screenheight()-100)))
        root.protocol("WM_DELETE_WINDOW", self.close)
        configure_styles(root)
        outer = ttk.Frame(root, padding=14)
        outer.pack(fill="both", expand=True)
        frame_color = ttk.Style(root).lookup("TFrame", "background") or root.cget("background")
        canvas = tk.Canvas(outer, highlightthickness=0, background=frame_color)
        self.scroll_canvas = canvas
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        main = ttk.Frame(canvas, padding=3)
        self.scroll_content = main
        window = canvas.create_window(0, 0, window=main, anchor="nw")
        pending_scroll_update = None

        def update_scroll_bounds(event=None):
            nonlocal pending_scroll_update
            pending_scroll_update = None
            viewport = max(1, canvas.winfo_height())
            requested = main.winfo_reqheight()
            # Canvas may defer resizing an offscreen embedded window. Apply
            # its requested height explicitly, including after pack_forget.
            canvas.itemconfigure(window, height=requested)
            content = max(viewport, requested)
            # A scroll region shorter than the viewport lets Tk introduce a
            # negative origin when collapsing sections, leaving blank space.
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), content))
            top = max(0, min(canvas.canvasy(0), content-viewport))
            canvas.yview_moveto(top/content)

        def schedule_scroll_bounds(event=None):
            nonlocal pending_scroll_update
            if pending_scroll_update is None:
                pending_scroll_update = root.after_idle(update_scroll_bounds)

        def resize_viewport(event):
            canvas.itemconfigure(window, width=event.width)
            schedule_scroll_bounds()

        def scroll_wheel(event):
            if main.winfo_height() > canvas.winfo_height():
                canvas.yview_scroll(int(-event.delta / 120), "units")

        main.bind("<Configure>", schedule_scroll_bounds)
        canvas.bind("<Configure>", resize_viewport)
        root.bind("<Configure>", schedule_scroll_bounds, add="+")
        root.bind("<<SectionToggled>>", schedule_scroll_bounds, add="+")
        root.bind("<MouseWheel>", scroll_wheel)
        ttk.Label(main, text="PLA RECYCLER CONTROL", font=TITLE_FONT).pack(anchor="w")
        self.connection_section = CollapsibleSection(main, "Connection")
        self.connection_section.pack(fill="x", pady=(8, 0))
        connection = self.connection_section.content
        ttk.Label(connection, text="Arduino Mega USB port").pack(side="left", padx=(0, 8))
        self.port_box = ttk.Combobox(connection, textvariable=self.port, width=28, state="readonly")
        self.port_box.pack(side="left", fill="x", expand=True)
        for label, callback in (("Refresh", self.refresh_ports), ("Connect", self.connect), ("Disconnect", self.disconnect)):
            add_tooltip(ttk.Button(connection, text=label, command=callback),
                        {"Refresh": "Refresh the available Mega USB ports.",
                         "Connect": "Connect to the selected Mega port.",
                         "Disconnect": "Stop outputs and disconnect the Mega."}[label]).pack(side="left", padx=4)
        ttk.Label(main, textvariable=self.connection_text, wraplength=730).pack(anchor="w")
        self.system_diagnostics_panel = ttk.LabelFrame(main, text="System Diagnostics", padding=8)
        self.system_diagnostics_panel.pack(fill="x", pady=5)
        columns = []
        for column in range(3):
            self.system_diagnostics_panel.columnconfigure(column, weight=1, uniform="diagnostics")
            frame = ttk.Frame(self.system_diagnostics_panel, padding=(8, 2))
            frame.grid(row=0, column=column, sticky="nsew")
            columns.append(frame)
        motors, camera_panel, statistics_panel = columns
        self.diagnostics_columns = columns
        ttk.Label(motors, text="Motor commands", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        motors.columnconfigure(0, weight=1)
        for row, (key, name) in enumerate((("cl", "Diameter Control Puller Motor"),
                                          ("dm", "Spooling Motor"), ("spark", "Extrusion Motor"),
                                          ("fan", "Cooling Fans"), ("servo", "Spooling Traverser Servo")), 1):
            ttk.Label(motors, text=name, wraplength=175).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Label(motors, textvariable=self.diagnostic_motor_text[key], wraplength=135).grid(
                row=row, column=1, sticky="e", padx=(8, 0))
        ttk.Label(motors, text="Extruded Length [mm]", font=("Segoe UI", 10, "bold")).grid(
            row=6, column=0, sticky="w", pady=(10, 0))
        ttk.Label(motors, textvariable=self.extruded_length_text).grid(
            row=6, column=1, sticky="e", padx=(8, 0), pady=(10, 0))
        ttk.Label(camera_panel, text="Mean diameter [mm]", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(camera_panel, textvariable=self.camera_diameter_text, font=TITLE_FONT).pack(anchor="w")
        limits = ttk.Frame(camera_panel)
        limits.pack(fill="x", pady=3)
        for label, variable in (("Min [mm]", self.minimum_diameter_text), ("Max [mm]", self.maximum_diameter_text)):
            ttk.Label(limits, text=label).pack(side="left", padx=(0, 5))
            ttk.Label(limits, textvariable=variable).pack(side="left", padx=(0, 12))
        ttk.Label(camera_panel, text="Min/Max: recording session.").pack(anchor="w")
        ttk.Label(camera_panel, textvariable=self.camera_status_text, wraplength=290).pack(anchor="w", pady=4)
        recording_buttons = ttk.Frame(camera_panel)
        recording_buttons.pack(fill="x", pady=(4, 3))
        self.start_recording_button = ttk.Button(recording_buttons, text="Start Recording", style="Start.TButton", command=self.start_recording)
        self.start_recording_button.pack(side="left")
        self.stop_recording_button = ttk.Button(recording_buttons, text="Stop Recording", style="Stop.TButton", command=self.stop_recording,
                                                state="disabled")
        self.stop_recording_button.pack(side="left", padx=6)
        ttk.Label(camera_panel, textvariable=self.recording_progress_text, wraplength=290).pack(anchor="w", pady=3)
        ttk.Label(camera_panel, textvariable=self.recording_text, wraplength=290).pack(anchor="w")
        ttk.Label(statistics_panel, text="Recording statistics [mm]", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        window_controls = ttk.Frame(statistics_panel)
        window_controls.pack(fill="x", pady=3)
        ttk.Label(window_controls, text="Window:").pack(side="left")
        self.statistics_window_entry = ttk.Entry(window_controls, textvariable=self.statistics_window_input, width=7)
        self.statistics_window_entry.pack(side="left", padx=6)
        self.statistics_window_entry.bind("<Return>", self.apply_statistics_window)
        self.statistics_window_entry.bind("<FocusOut>", self.apply_statistics_window)
        ttk.Label(window_controls, text="points").pack(side="left")
        add_tooltip(ttk.Button(window_controls, text="Set", command=self.apply_statistics_window),
                    "Apply the diagnostic averaging window.").pack(side="left", padx=6)
        ttk.Label(statistics_panel, textvariable=self.statistics_window_error, foreground="#b91c1c").pack(anchor="w")
        self.statistics_window_slider = tk.Scale(statistics_panel, from_=2, to=MAX_WINDOW, orient="horizontal",
                                                 variable=self.statistics_window, showvalue=False, resolution=1,
                                                 command=self.statistics_window_changed)
        self.statistics_window_slider.pack(fill="x")
        statistic_rows = ttk.Frame(statistics_panel)
        statistic_rows.pack(fill="x")
        statistic_rows.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("mean_error", "Moving mean error"), ("std_dev", "Rolling std dev"),
                                            ("mean_std_dev", "Moving mean std dev"), ("spread_95", "Estimated 95% diameter spread"),
                                            ("ci_95", "95% confidence interval for mean diameter"))):
            ttk.Label(statistic_rows, text=label, wraplength=165).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(statistic_rows, textvariable=self.statistics_text[key], wraplength=175).grid(
                row=row, column=1, sticky="e", padx=(8, 0))
        ttk.Label(statistics_panel, textvariable=self.statistics_status, wraplength=290).pack(anchor="w", pady=4)
        self.operating_configuration_section = CollapsibleSection(main, "Operating Configuration")
        self.operating_configuration_section.pack(fill="x", pady=(5, 10))
        configuration_row = ttk.Frame(self.operating_configuration_section.content)
        configuration_row.pack(fill="x")
        self.configuration_box = ttk.Combobox(configuration_row, textvariable=self.configuration_selection,
                                             state="readonly", width=30, postcommand=self.refresh_configurations)
        self.configuration_box.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.configuration_box.bind("<<ComboboxSelected>>", lambda event: self.configuration_selected())
        self.configuration_buttons = {}
        for label, callback, help_text in (
                ("Save Current", self.save_operating_configuration, "Save all current settings with a name and temperature."),
                ("Load", self.load_operating_configuration, "Stop outputs and restore the selected setup."),
                ("Duplicate", self.duplicate_operating_configuration, "Copy the selected setup under a new name."),
                ("Rename", self.rename_operating_configuration, "Rename the selected saved setup."),
                ("Delete", self.delete_operating_configuration, "Delete the selected saved setup.")):
            button = add_tooltip(ttk.Button(configuration_row, text=label, command=callback), help_text)
            button.pack(side="left", padx=3)
            self.configuration_buttons[label] = button
        ttk.Label(self.operating_configuration_section.content, textvariable=self.configuration_temperature).pack(anchor="w", pady=(4, 0))
        ttk.Label(self.operating_configuration_section.content, textvariable=self.configuration_status, wraplength=990).pack(anchor="w")
        self.feedback_section = CollapsibleSection(main, "Diameter Feedback Control")
        self.feedback_section.pack(fill="x", pady=(5, 10))
        feedback = self.feedback_section.content
        modes = ttk.Frame(feedback)
        modes.pack(fill="x", pady=5)
        ttk.Label(modes, text="Puller mode:").pack(side="left")
        for name in ("Manual", "Automatic", "Disabled"):
            add_tooltip(ttk.Radiobutton(modes, text=name, value=name, variable=self.mode, command=self.mode_changed),
                        lambda value=name: self.mode_tooltip("puller", value)).pack(side="left", padx=8)
        self.add_motor(feedback, "Diameter Control Puller Motor", "cl", MAX_RPM, "RPM", True,
                       "CL42T / D30 STEP, D31 DIR")
        auto = ttk.LabelFrame(feedback, text="Automatic Controls", padding=8)
        auto.pack(fill="x", pady=5)
        self.automatic_vars, self.automatic_entries = {}, []

        def add_settings(parent, fields):
            parent.columnconfigure(2, weight=1)
            for row, (label, key, default, explanation) in enumerate(fields):
                ttk.Label(parent, text=label, wraplength=220).grid(
                    row=row, column=0, sticky="w", padx=(0, 8), pady=6)
                var = tk.StringVar(value=default)
                entry = ttk.Entry(parent, textvariable=var, width=12)
                entry.grid(row=row, column=1, sticky="w", padx=(0, 12), pady=6)
                ttk.Label(parent, text=explanation, wraplength=330).grid(
                    row=row, column=2, sticky="w", pady=6)
                self.automatic_vars[key] = var
                self.automatic_entries.append(entry)

        puller_settings = ttk.LabelFrame(auto, text="Diameter and response", padding=8)
        puller_settings.pack(fill="x", pady=(0, 7))
        add_settings(puller_settings, [
            ("Target diameter [mm]", "target", "1.75",
             "Desired filament diameter."),
            ("Base puller speed [RPM]", "base", "60",
             "Speed at the target diameter."),
            ("Correction gain [RPM/mm]", "kp", "1",
             "RPM change per mm outside the tolerance."),
            ("Diameter tolerance [+/- mm]", "deadband", "0.1",
             "No correction within this band. 0 disables the band."),
        ])
        ttk.Label(puller_settings, textvariable=self.filtered_diameter_text, wraplength=690).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.automatic_setup_section = CollapsibleSection(auto, "Speed Limits and Wheel Diameter", expanded=False)
        self.automatic_setup_section.pack(fill="x", pady=(0, 5))
        self.automatic_setup_button = self.automatic_setup_section.toggle_button
        self.automatic_setup_frame = self.automatic_setup_section.content
        add_settings(self.automatic_setup_frame, [
            ("Minimum speed [RPM]", "minimum", "0",
             "Automatic minimum; excludes startup and stopping."),
            ("Maximum speed [RPM]", "maximum", "100",
             "Automatic speed limit; up to 100 RPM."),
            ("Speed change limit [RPM/s]", "slew", "2",
             "Maximum RPM change per second."),
            ("Puller wheel diameter [mm]", "wheel_diameter", "22.5044",
             "Converts motor RPM to filament speed; direct drive."),
        ])
        actions = ttk.Frame(auto)
        actions.pack(fill="x", pady=5)

        self.auto_start = ttk.Button(actions, text="Start Automatic", style="Start.TButton", command=self.start_automatic)
        self.auto_start.pack(side="left")
        self.auto_stop = ttk.Button(actions, text="Stop Automatic", style="Stop.TButton", command=self.stop_automatic)
        self.auto_stop.pack(side="left", padx=6)
        self.auto_apply = ttk.Button(actions, text="Apply Settings", command=self.apply_automatic_settings)
        self.auto_apply.pack(side="left")
        ttk.Label(auto, text="Apply Settings saves edits. Start Automatic also applies them.",
                  wraplength=710).pack(anchor="w")
        ttk.Label(auto, textvariable=self.auto_status, wraplength=710).pack(anchor="w", pady=(4, 0))
        ttk.Label(auto, textvariable=self.velocity_text).pack(anchor="w")
        self.main_controls_section = CollapsibleSection(main, "Main Controls")
        self.main_controls_section.pack(fill="x", pady=(0, 10))
        main_controls = self.main_controls_section.content
        ttk.Label(main_controls, text="Status: green = active, red = stopped, flashing red = fault. Click for details. Motion is not measured.", wraplength=990).pack(anchor="w", pady=(0, 6))
        for title, key, maximum, unit, reverse, details in (
                ("Extrusion Motor", "spark", 100, "% command", True, "SPARK MAX / D9"),
                ("Cooling Fans", "fan", 100, "%", False, "D6-D7")):
            self.add_motor(main_controls, title, key, maximum, unit, reverse, details)
        self.spooling_controls_section = CollapsibleSection(main_controls, "Spooling Controls")
        self.spooling_controls_section.pack(fill="x", pady=5)
        spooling_controls = self.spooling_controls_section.content
        self.spool_diagnostics_panel = ttk.LabelFrame(spooling_controls, text="Spooling Diagnostics", padding=8)
        self.spool_diagnostics_panel.pack(fill="x", pady=5)
        ttk.Label(self.spool_diagnostics_panel, textvariable=self.spool_diagnostics_text,
                  wraplength=900).pack(anchor="w")
        self.calculated_spool_label = ttk.Label(self.spool_diagnostics_panel, textvariable=self.spooler_text, wraplength=900)
        self.calculated_spool_label.pack(anchor="w", pady=(4, 0))
        self.tare_button = ttk.Button(self.spool_diagnostics_panel, text="Extrusion Reset", command=self.tare_n)
        self.tare_button.pack(anchor="w", pady=5)
        ttk.Label(self.spool_diagnostics_panel, text="Stop the spooler before reset. Reset moves to minimum, zeros layers and sets length to 635 mm.",
                  wraplength=900).pack(anchor="w")
        self.spooling_panel = ttk.LabelFrame(spooling_controls, padding=8)
        self.add_control_heading(self.spooling_panel, "servo", "Spooling Traverser Servo", "D10")
        servo = self.spooling_panel
        servo.pack(fill="x", pady=5)
        sweep_modes = ttk.Frame(servo)
        sweep_modes.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        self.spooling_selection = tk.StringVar(value=self.spooling_mode.get())
        self.spooling_mode_buttons = {}
        for name in ("Manual", "Automatic"):
            button = ttk.Radiobutton(sweep_modes, text=name, value=name, variable=self.spooling_selection,
                                     command=lambda value=name: self.set_spooling_mode(value))
            button.pack(side="left", padx=8)
            self.spooling_mode_buttons[name] = button
            add_tooltip(button, lambda value=name: self.mode_tooltip("traverser", value))
        ttk.Label(servo, text="Set saves inputs. START runs the sweep. Center holds midway between the limits.",
                  wraplength=710).grid(row=1, column=0, columnspan=4, sticky="w")
        self.servo_vars = {}
        self.servo_widgets = []
        self.servo_limit_widgets = []
        for col, (label, key, default) in enumerate((("Speed [deg/s]", "speed", "0.25"),
                                                   ("Min angle [deg]", "minimum", f"{geometry.minimum_angle:g}"),
                                                   ("Max angle [deg]", "maximum", f"{geometry.maximum_angle:g}"),
                                                   ("Starting angle [deg]", "start", f"{geometry.maximum_angle:g}"))):
            ttk.Label(servo, text=label).grid(row=2, column=col, padx=5)
            var = tk.StringVar(value=default)
            entry = ttk.Entry(servo, textvariable=var, width=14)
            entry.grid(row=3, column=col, padx=5)
            (self.servo_limit_widgets if key in ("minimum", "maximum") else self.servo_widgets).append(entry)
            self.servo_vars[key] = var
        sweep_actions = ttk.Frame(servo)
        sweep_actions.grid(row=4, column=0, columnspan=4, sticky="w", pady=5)
        self.sweep_apply_button = ttk.Button(sweep_actions, text="Set", command=self.set_servo)
        self.sweep_start_button = ttk.Button(sweep_actions, text="START", style="Start.TButton", command=self.start_servo)
        self.sweep_stop_button = ttk.Button(sweep_actions, text="STOP", style="Stop.TButton", command=self.detach_spooling)
        self.sweep_center_button = ttk.Button(sweep_actions, text="Center / Hold", command=self.center_spooling)
        self.sweep_pause_button = ttk.Button(sweep_actions, text="Pause Sweep", command=self.pause_spooling)
        for button in (self.sweep_apply_button, self.sweep_start_button, self.sweep_stop_button,
                       self.sweep_center_button, self.sweep_pause_button):
            button.pack(side="left", padx=(0, 6))
        ttk.Label(servo, text="Starting angle must be between Min and Max. Speed changes preserve position and direction.",
                  wraplength=710).grid(row=5, column=0, columnspan=4, sticky="w", pady=4)
        angle_actions = ttk.Frame(servo)
        angle_actions.grid(row=6, column=0, columnspan=4, sticky="w", pady=4)
        ttk.Label(angle_actions, text="Angle [deg]").pack(side="left", padx=5)
        self.servo_angle_input = tk.StringVar(value=f"{geometry.maximum_angle:g}")
        self.servo_angle_entry = ttk.Entry(angle_actions, textvariable=self.servo_angle_input, width=12)
        self.servo_angle_entry.pack(side="left", padx=5)
        self.servo_angle_button = ttk.Button(angle_actions, text="Set Angle", command=self.set_servo_angle)
        self.servo_angle_button.pack(side="left", padx=5)
        add_tooltip(self.servo_angle_button, "Move directly to this angle. A paused sweep stays paused; a running sweep continues.")
        ttk.Label(servo, textvariable=self.oscillator_text, wraplength=710).grid(
            row=7, column=0, columnspan=4, sticky="w")
        ttk.Label(servo, textvariable=self.spooling_status, wraplength=710).grid(
            row=8, column=0, columnspan=4, sticky="w", pady=4)
        self.add_motor(spooling_controls, "Spooling Motor", "dm", MAX_DM_RPM, "RPM", True,
                       "DM stepper / D40 STEP, D41 DIR; direct drive")
        self.spool_geometry_section = CollapsibleSection(spooling_controls, "Spool Geometry", expanded=False)
        self.spool_geometry_section.pack(fill="x", pady=7)
        spool_setup = self.spool_geometry_section.content
        spool_setup.columnconfigure(2, weight=1)
        self.spool_geometry_vars, self.spool_geometry_entries = {}, []
        geometry_fields = [
            ("Initial coil diameter [mm]", "core_diameter", "Bare core if empty; current coil diameter at reset if partly filled."),
            ("Winding width [mm]", "winding_width", "Inside flange spacing; reserves edge clearance."),
            ("Filament diameter [mm]", "filament_diameter", "Strand diameter and winding pitch."),
        ]
        for row, (label, key, explanation) in enumerate(geometry_fields):
            ttk.Label(spool_setup, text=label, wraplength=210).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=f"{getattr(geometry, key):g}")
            entry = ttk.Entry(spool_setup, textvariable=var, width=12)
            entry.grid(row=row, column=1, sticky="w", padx=8)
            ttk.Label(spool_setup, text=explanation, wraplength=330).grid(row=row, column=2, sticky="w", pady=4)
            self.spool_geometry_vars[key] = var
            self.spool_geometry_entries.append(entry)
        self.spool_geometry_vars.update(minimum_angle=self.servo_vars["minimum"], maximum_angle=self.servo_vars["maximum"])
        self.save_geometry_button = ttk.Button(spool_setup, text="Save geometry", command=self.apply_spool_geometry)
        self.save_geometry_button.grid(row=6, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Label(spool_setup, textvariable=self.geometry_status, wraplength=690).grid(row=7, column=0, columnspan=3, sticky="w")
        if self.spooling_settings_path is not None and not self.spooling_settings_path.exists() and not geometry_error:
            self.apply_spool_geometry()
        self.stop_all_button = ttk.Button(main, text="STOP ALL", style="Emergency.Stop.TButton", command=self.stop_all)
        self.stop_all_button.pack(fill="x", pady=8)
        ttk.Label(main, textvariable=self.response_text, wraplength=730).pack(anchor="w")
        ttk.Label(main, text="Speeds are commanded, not measured. A physical E-stop is required.").pack(anchor="w")
        for button, explanation in (
                (self.start_recording_button, "Start a CSV recording and reset statistics."),
                (self.stop_recording_button, "Stop recording and choose a CSV filename."),
                (self.auto_start, "Start diameter-based puller control."),
                (self.auto_stop, "Stop automatic control and the puller."),
                (self.auto_apply, "Apply diameter-control settings."),
                (self.tare_button, "Reset layers, set length to 635 mm and move the guide to minimum."),
                (self.sweep_apply_button, "Save or update sweep settings."),
                (self.sweep_start_button, "Start or resume the selected sweep."),
                (self.sweep_stop_button, "Stop the sweep and disable servo pulses."),
                (self.sweep_center_button, "Move midway between Min and Max and hold. Set or START resumes."),
                (self.sweep_pause_button, lambda: "Resume from the held angle." if self.state.snapshot()["servo_hold"] == "paused"
                                                else "Pause at the current angle."),
                (self.save_geometry_button, "Save spool dimensions and sweep limits."),
                (self.stop_all_button, "Stop all outputs and disarm the extruder.")):
            add_tooltip(button, explanation)
        self.refresh_configurations()
        self.refresh_ports()
        self.update_controls()
        root.after(100, self.poll)

    def refresh_configurations(self, selected_id=None):
        try:
            self.configuration_profiles = self.configuration_store.list()
            names = [profile["name"] for profile in self.configuration_profiles]
            self.configuration_box.configure(values=names)
            if selected_id is not None:
                self.configuration_selection.set(next((profile["name"] for profile in self.configuration_profiles if profile["id"] == selected_id), ""))
            elif self.configuration_selection.get() not in names:
                self.configuration_selection.set(names[0] if names else "")
            self.configuration_selected()
        except (OSError, ValueError) as error:
            self.configuration_status.set(f"Configurations unavailable: {error}")

    def selected_configuration(self):
        name = self.configuration_selection.get()
        match = next((profile for profile in self.configuration_profiles if profile["name"] == name), None)
        if match is None:
            raise ValueError("Select a saved operating configuration.")
        return self.configuration_store.get(match["id"])

    def configuration_selected(self):
        match = next((profile for profile in self.configuration_profiles if profile["name"] == self.configuration_selection.get()), None)
        self.configuration_temperature.set(f"Operating temperature: {match['temperature_c']:g} C" if match else "")
        for label in ("Load", "Duplicate", "Rename", "Delete"):
            self.configuration_buttons[label].configure(state="normal" if match else "disabled")

    def capture_operating_configuration(self):
        packet, _ = self.camera.snapshot()
        camera_settings = packet.get("camera_settings") if isinstance(packet, dict) else None
        if camera_settings is None:
            camera_settings = load_camera_settings()
        applied = self.state.snapshot()
        return validate_configuration(dict(
            version=1,
            modes=dict(puller=self.mode.get(), spooler=self.spool_motor_mode.get(), traverser=self.spooling_mode.get()),
            mega_port=self.port.get(),
            automatic_inputs={key: var.get() for key, var in self.automatic_vars.items()},
            automatic_applied=asdict(applied["automatic"]),
            motors={key: dict(setpoint=var.get(), entry=self.motor_widgets[key][1].get(), reverse=self.reverse_vars[key].get())
                    for key, var in self.speed_vars.items()},
            servo_inputs={key: var.get() for key, var in self.servo_vars.items()},
            servo_applied=dict(speed=self.manual_sweep_settings[1],
                               minimum=self.manual_sweep_settings[0]-self.manual_sweep_settings[2],
                               maximum=self.manual_sweep_settings[0]+self.manual_sweep_settings[2], start=self.manual_sweep_settings[3]),
            servo_angle_input=self.servo_angle_input.get(),
            geometry_inputs={key: var.get() for key, var in self.spool_geometry_vars.items()},
            geometry_applied=asdict(applied["winding_geometry"]),
            statistics_window=self.recording_statistics.window, statistics_window_input=self.statistics_window_input.get(),
            confirmed_commands={key: value for key, value in self.recording_motor_snapshot().items() if not key.startswith("_")},
            camera=camera_settings))

    def configuration_error(self, error):
        self.configuration_status.set(str(error))
        messagebox.showerror("Operating Configuration", str(error), parent=self.root)

    def save_operating_configuration(self):
        try:
            settings = self.capture_operating_configuration()
            name = simpledialog.askstring("Save Operating Configuration", "Configuration name:",
                                          initialvalue=self.configuration_selection.get(), parent=self.root)
            if name is None:
                return
            temperature = simpledialog.askfloat("Operating Temperature", "Operating temperature [C]:", parent=self.root)
            if temperature is None:
                return
            existing = next((entry for entry in self.configuration_store.list() if entry["name"].casefold() == name.strip().casefold()), None)
            if existing and not messagebox.askyesno("Replace Configuration", f"Replace '{existing['name']}' with current settings?", parent=self.root):
                return
            profile = self.configuration_store.save(name, temperature, settings, existing["id"] if existing else None)
            self.refresh_configurations(profile["id"])
            self.configuration_status.set("Current settings saved." if settings["camera"] is not None else "Recycler settings saved; no camera setup was available.")
        except (OSError, ValueError, tk.TclError) as error:
            self.configuration_error(error)

    def apply_operating_configuration(self, settings):
        settings = validate_configuration(settings)
        self.stop_all()
        if self.worker is not None and settings["mega_port"] != self.port.get():
            self.disconnect()
        self.port.set(settings["mega_port"])
        self.mode.set(settings["modes"]["puller"])
        self.spool_motor_mode.set(settings["modes"]["spooler"])
        self.spooling_mode.set(settings["modes"]["traverser"])
        for key, value in settings["automatic_inputs"].items():
            self.automatic_vars[key].set(value)
        for key, motor in settings["motors"].items():
            self.speed_vars[key].set(motor["setpoint"])
            self.reverse_vars[key].set(motor["reverse"])
            self.motor_readouts[key].configure(text=f"{motor['setpoint']:g} {'RPM' if key in ('cl', 'dm') else '%'}")
            entry = self.motor_widgets[key][1]
            previous_state = str(entry.cget("state"))
            entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, motor["entry"])
            entry.configure(state=previous_state)
        for key, value in settings["servo_inputs"].items():
            self.servo_vars[key].set(value)
        for key, value in settings["geometry_inputs"].items():
            self.spool_geometry_vars[key].set(value)
        servo = settings["servo_applied"]
        self.manual_sweep_settings = ((servo["minimum"]+servo["maximum"])/2, servo["speed"],
                                      (servo["maximum"]-servo["minimum"])/2, servo["start"])
        self.servo_angle_input.set(settings["servo_angle_input"])
        geometry = SpoolGeometry(**settings["geometry_applied"])
        if self.spooling_settings_path is not None:
            save_geometry(geometry, self.spooling_settings_path)
        self.state.set(automatic=AutomaticSettings(**settings["automatic_applied"]), winding_geometry=geometry,
                       puller_disabled=self.mode.get() == "Disabled", spool_baseline_rpm=self.speed_vars["dm"].get(),
                       spool_direction=-1 if self.reverse_vars["dm"].get() else 1)
        self.statistics_window.set(settings["statistics_window"])
        self.statistics_window_changed(settings["statistics_window"])
        self.statistics_window_input.set(settings["statistics_window_input"])
        self.diameter_filter.clear()
        self.winding_ready = False
        self.geometry_needs_reference = True
        self.spooling_status.set("Configuration loaded. Extrusion Reset required before Automatic spooling.")
        self.auto_status.set("Configuration loaded. Puller stopped.")
        self.spool_motor_text.set("Configuration loaded. Spooler stopped.")
        self.update_controls()
        # Scale callbacks can be queued until the next Tk redraw. Restore exact
        # entry drafts afterward so saved Set values and drafts both survive.
        self.root.after_idle(lambda: self.restore_configuration_entries(settings))

    def restore_configuration_entries(self, settings):
        for key, motor in settings["motors"].items():
            entry = self.motor_widgets[key][1]
            previous_state = str(entry.cget("state"))
            entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, motor["entry"])
            entry.configure(state=previous_state)
        self.statistics_window_input.set(settings["statistics_window_input"])

    def load_operating_configuration(self):
        try:
            if self.recorder.busy or self.recording_save_pending or self.recording_save_dialog_active or self.recording_exporter.busy:
                raise ValueError("Stop recording and finish saving before loading a configuration.")
            profile = self.selected_configuration()
            self.apply_operating_configuration(profile["settings"])
            camera_settings = profile["settings"].get("camera")
            if camera_settings is not None:
                self.camera_configuration_pending = request_configuration(camera_settings, profile["name"])
            self.configuration_status.set("Loaded; all outputs stopped. Camera setup queued." if camera_settings is not None else "Loaded; all outputs stopped.")
            messagebox.showinfo("Operating Temperature", f"For this operating configuration, set temperature to: {profile['temperature_c']:g} C", parent=self.root)
        except (OSError, ValueError, tk.TclError) as error:
            self.configuration_error(error)

    def duplicate_operating_configuration(self):
        try:
            selected = self.selected_configuration()
            name = simpledialog.askstring("Duplicate Configuration", "New configuration name:", initialvalue=selected["name"]+" copy", parent=self.root)
            if name is None:
                return
            profile = self.configuration_store.duplicate(selected["id"], name)
            self.refresh_configurations(profile["id"])
            self.configuration_status.set("Configuration duplicated.")
        except (OSError, ValueError) as error:
            self.configuration_error(error)

    def rename_operating_configuration(self):
        try:
            selected = self.selected_configuration()
            name = simpledialog.askstring("Rename Configuration", "Configuration name:", initialvalue=selected["name"], parent=self.root)
            if name is None:
                return
            profile = self.configuration_store.rename(selected["id"], name)
            self.refresh_configurations(profile["id"])
            self.configuration_status.set("Configuration renamed.")
        except (OSError, ValueError) as error:
            self.configuration_error(error)

    def delete_operating_configuration(self):
        try:
            selected = self.selected_configuration()
            if not messagebox.askyesno("Delete Configuration", f"Delete '{selected['name']}'?", parent=self.root):
                return
            self.configuration_store.delete(selected["id"])
            self.refresh_configurations()
            self.configuration_status.set("Configuration deleted.")
        except (OSError, ValueError) as error:
            self.configuration_error(error)

    def check_camera_configuration_result(self):
        if self.camera_configuration_pending is None:
            return
        try:
            if not CONFIGURATION_RESULT_PATH.exists() or CONFIGURATION_RESULT_PATH.stat().st_size > 16384:
                return
            result = json.loads(CONFIGURATION_RESULT_PATH.read_text(encoding="utf-8"))
            if result.get("id") == self.camera_configuration_pending:
                self.camera_configuration_pending = None
                self.configuration_status.set("Configuration loaded in both interfaces; outputs stopped." if result.get("ok") else
                                              f"Recycler loaded; camera setup failed: {result.get('error', 'unknown error')}")
        except (OSError, ValueError, AttributeError):
            pass

    def start_recording(self):
        if self.recording_save_pending or self.recording_save_dialog_active or self.recording_exporter.busy:
            return
        try:
            self.recorder.start()
        except (OSError, RuntimeError) as error:
            self.recording_text.set(f"Could not start recording: {error}")
            return
        self.recording_exporter = RecordingExporter()
        self.recording_statistics.start()
        self.update_statistics_readout()
        self.poll_recording()

    def stop_recording(self):
        was_recording = self.recorder.recording
        self.recorder.stop()
        self.recording_statistics.stop()
        self.update_statistics_readout()
        if was_recording and not self.recording_save_pending:
            self.recording_save_pending = True
            self.root.after(50, self.finish_recording_save)
        self.poll_recording()

    def finish_recording_save(self):
        if not self.recording_save_pending:
            return
        if self.closing:
            self.recording_save_pending = False
            return
        if self.recorder.busy:
            self.root.after(50, self.finish_recording_save)
            return
        self.recording_save_pending = False
        self.recording_save_dialog_active = True
        self.poll_recording()
        source = self.recorder.path
        try:
            documents = Path.home()/"Documents"
            directory = self.last_recording_save_directory or (documents if documents.is_dir() else Path.home())
            selected = filedialog.asksaveasfilename(
                parent=self.root, title="Save diameter recording", initialdir=str(directory),
                initialfile=source.name, defaultextension=".csv",
                filetypes=[("CSV files", "*.csv")], confirmoverwrite=True)
            if selected and not self.closing:
                destination = Path(selected)
                self.last_recording_save_directory = destination.parent
                self.recording_exporter.start(source, destination)
        except (OSError, RuntimeError, tk.TclError) as error:
            self.recording_exporter.error = str(error)
        finally:
            self.recording_save_dialog_active = False
        if not self.closing:
            self.poll_recording()

    def poll_recording(self):
        busy, recording = self.recorder.busy, self.recorder.recording
        if not recording:
            self.recording_statistics.stop()
        saving = self.recording_save_pending or self.recording_save_dialog_active or self.recording_exporter.busy
        self.start_recording_button.configure(state="disabled" if busy or saving else "normal")
        self.stop_recording_button.configure(state="normal" if recording else "disabled")
        rows, error = self.recorder.snapshot()
        elapsed, saved = self.recorder.progress()
        seconds = int(elapsed)
        self.recording_progress_text.set(
            f"Elapsed: {seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d} | Points saved: {saved}")
        if error:
            self.recording_text.set(f"{error} File: {self.recorder.path}")
        elif self.recording_exporter.error:
            self.recording_text.set(f"Save failed: {self.recording_exporter.error} | Original kept: {self.recorder.path}")
        elif self.recorder.path is not None:
            state = "Recording" if recording else "Finishing" if busy else "Saving" if saving else "Saved"
            self.recording_text.set(f"{state} | {self.recording_exporter.path or self.recorder.path}")

    def statistics_window_changed(self, value):
        count = int(float(value))
        self.recording_statistics.set_window(count)
        self.statistics_window_input.set(str(count))
        self.statistics_window_error.set("")
        self.statistics_window_entry.state(["!invalid"])
        self.update_statistics_readout()

    def apply_statistics_window(self, event=None):
        try:
            count = int(self.statistics_window_input.get().strip())
            if not 2 <= count <= MAX_WINDOW:
                raise ValueError
        except ValueError:
            self.statistics_window_error.set(f"Enter 2-{MAX_WINDOW} whole points.")
            self.statistics_window_entry.state(["invalid"])
            return "break"
        self.statistics_window.set(count)
        self.statistics_window_changed(count)
        return "break"

    def update_statistics_readout(self):
        result = self.recording_statistics.snapshot()
        for key, variable in (("min_diameter", self.minimum_diameter_text), ("max_diameter", self.maximum_diameter_text)):
            variable.set("N/A" if result[key] is None else f"{result[key]:.4f}")
        for key, variable in self.statistics_text.items():
            value = result[key]
            if value is None:
                variable.set("N/A")
            elif key == "ci_95":
                variable.set(f"{result['mean_diameter']:.5f} +/- {value:.5f}")
            elif key == "spread_95":
                variable.set(f"+/- {value:.5f}")
            else:
                variable.set(f"{value:+.5f}" if key == "mean_error" else f"{value:.5f}")
        count = result["sample_count"]
        if count == 0:
            text = "Waiting for valid readings." if self.recording_statistics.active else "Start Recording to collect statistics."
        else:
            text = ("Recording" if self.recording_statistics.active else "Stopped") + f" | {count} readings | window {result['window_count']}/{result['window_size']}"
            if result["window_count"] < 2:
                text += " | Need 2 readings for deviation/CI."
        self.statistics_status.set(text)
        return result

    def update_diagnostic_motor_readouts(self):
        fresh = (self.connected and not self.link_failure and self.last_mega_status_at is not None
                 and time.monotonic()-self.last_mega_status_at <= 2)
        if not fresh:
            for variable in self.diagnostic_motor_text.values():
                variable.set("N/A")
            return
        fields = self.last_mega_status
        for key, field in (("cl", "CL"), ("dm", "DM")):
            self.diagnostic_motor_text[key].set(f"{float(fields[field]):.3f} RPM")
        self.diagnostic_motor_text["spark"].set(f"{float(fields['SPARK']):g}%")
        self.diagnostic_motor_text["fan"].set(f"{float(fields['FAN'])/255*100:.1f}%")
        if fields.get("SERVO") == "0":
            self.diagnostic_motor_text["servo"].set("0 deg/s (off)")
        elif "OMEGA" in fields:
            self.diagnostic_motor_text["servo"].set(f"{float(fields['OMEGA']):.3f} deg/s\n{float(fields['THETA']):.2f} deg")
        else:
            self.diagnostic_motor_text["servo"].set("N/A")

    def recording_motor_snapshot(self):
        """Latest confirmed commands at row capture time; never reuse stale values."""
        age = None if self.last_mega_status_at is None else max(0.0, time.monotonic()-self.last_mega_status_at)
        fresh = bool(self.connected and not self.link_failure and age is not None and age <= 2)
        result = dict(mega_status_valid=int(fresh), mega_status_age_s=age,
                      _status_received_at=self.last_mega_status_at)
        if not fresh:
            return result
        fields = self.last_mega_status
        servo_enabled = int(float(fields["SERVO"]) != 0)
        result.update(
            puller_command_rpm=float(fields["CL"]), spooler_command_rpm=float(fields["DM"]),
            extruder_command_percent=float(fields["SPARK"]),
            cooling_fans_command_percent=float(fields["FAN"])/255*100,
            cooling_fans_command_pwm=float(fields["FAN"]), traverser_enabled=servo_enabled,
            traverser_command_speed_deg_s=(float(fields["OMEGA"]) if "OMEGA" in fields else None) if servo_enabled else 0.0,
            traverser_command_angle_deg=float(fields["THETA"]) if servo_enabled and "THETA" in fields else None,
            filament_linear_velocity_mm_s=float(fields["TAKEUP_MM_S"]) if "TAKEUP_MM_S" in fields else None,
            extruded_length_mm=float(fields["LENGTH_MM"]) if "LENGTH_MM" in fields else None,
            puller_linear_velocity_mm_s=linear_velocity(abs(float(fields["CL"])), self.state.snapshot()["automatic"].wheel_diameter),
        )
        return result

    def refresh_ports(self):
        ports = [p.device for p in list_ports.comports() if p.vid is not None]
        self.port_box["values"] = ports
        if not self.port.get() and ports:
            self.port.set(ports[0])

    def connect(self):
        if self.worker is not None:
            return
        if not self.port.get():
            self.response_text.set("Select a Mega USB port.")
            return
        self.stop_all()
        self.disconnect_requested = False
        self.worker = MegaWorker(self.port.get(), self.state, self.camera, self.events,
                                 diameter_filter=self.diameter_filter, takeup=self.takeup)
        self.worker.start()
        self.connection_text.set("Connecting to Mega...")
        self.update_controls()

    def disconnect(self):
        self.disconnect_requested = True
        self.link_failure = None
        self.stop_all()
        if self.worker is not None:
            self.worker.stop.set()
        self.connected = False
        self.update_controls()

    def mode_tooltip(self, control, mode):
        descriptions = {
            ("puller", "Manual"): (
                "Uses the puller RPM slider or entered speed, with no diameter correction.\n"
                "Set the speed, then press motor START.\n"
                "No camera required."),
            ("puller", "Automatic"): (
                "Base RPM + gain x diameter error outside the tolerance, using a 20-point average.\n"
                "Requires a live calibrated camera. Set target, base RPM, gain and limits, "
                "then press Start Automatic."),
            ("puller", "Disabled"): (
                "Commands zero puller RPM and bypasses diameter control.\n"
                "Automatic spooling can still run using the manual spooler RPM as its layer-zero baseline."),
            ("traverser", "Manual"): (
                "Uses Speed, Min/Max angles and Starting angle.\n"
                "Enter settings, press Set, then START.\n"
                "Sweep speed does not follow spooler RPM; no camera required."),
            ("traverser", "Automatic"): (
                "Calculates sweep speed from spooler RPM and Spool Geometry to lay one filament width per revolution.\n"
                "Save geometry, stop the spooler and click Extrusion Reset. "
                "START the traverser, then run the spooler in Manual or Automatic.\n"
                "Zero spooler RPM holds position. No camera required."),
            ("spooler", "Manual"): (
                "Uses the spooler RPM slider or entered speed; RPM does not decrease automatically as layers grow.\n"
                "Set the speed, then press motor START.\n"
                "No camera or puller feedback required."),
        }
        if control == "spooler" and mode == "Automatic":
            if self.mode.get() == "Disabled":
                source = ("Uses manual spooler RPM as the layer-zero baseline, reducing RPM as the coil grows. "
                          "Set that baseline using the spooler slider or entry.\n")
            else:
                source = ("Matches filament speed calculated from puller RPM and wheel diameter, "
                          "reducing spooler RPM as the coil grows. The puller must run in Manual or Automatic.\n")
            return (source + "Requires saved Spool Geometry, Extrusion Reset and a running "
                    "Automatic traverser. Press spooler START. Pausing the traverser holds the spooler at zero.")
        return descriptions[(control, mode)]

    def add_control_heading(self, panel, key, title, details):
        heading = ttk.Frame(panel)
        label = ttk.Label(heading, text=title, font=("Segoe UI", 10, "bold"))
        label.pack(side="left")
        background = ttk.Style(self.root).lookup("TFrame", "background") or self.root.cget("background")
        indicator = tk.Button(heading, text="\u25cf", font=("Segoe UI", 11, "bold"), width=2,
                              foreground="#b91c1c", background=background,
                              activebackground=background, activeforeground="#b91c1c",
                              borderwidth=1, relief="flat", padx=0, pady=0, cursor="hand2",
                              command=lambda: self.show_control_status(key))
        indicator.pack(side="left", padx=(5, 2))
        text = tk.StringVar(value="Stopped")
        ttk.Label(heading, textvariable=text, foreground="#4b5563").pack(side="left", padx=(0, 10))
        ttk.Label(heading, text=details).pack(side="left")
        panel.configure(labelwidget=heading)
        self.control_titles[key] = label
        add_tooltip(indicator, "Show this control's status details.")
        self.control_indicators[key] = indicator
        self.control_indicator_text[key] = text
        self.control_indicator_states[key] = "stopped"
        self.control_indicator_details[key] = "Control deactivated."

    def show_control_status(self, key):
        self.response_text.set(f"{self.control_titles[key].cget('text')}: {self.control_indicator_details[key]}")

    def update_control_indicators(self, now=None):
        now = time.monotonic() if now is None else now
        values = self.state.snapshot()
        reported = self.last_mega_status or {}
        link_fault = self.link_failure
        if (self.connected and self.last_mega_status_at is not None
                and now - self.last_mega_status_at > 2):
            link_fault = "Mega status lost (over 2 s)."
        for key, indicator in self.control_indicators.items():
            fault = link_fault or self.control_faults.get(key)
            if key == "cl" and self.connected and not self.cl_ready and self.mode.get() != "Disabled":
                fault = fault or "Puller unavailable. Check the Mega and driver."
            if fault:
                state, caption, detail = "fault", "Signal failed", str(fault)
                color = "#b91c1c" if int(now * 2) % 2 == 0 else "#d1d5db"
            else:
                field = {"cl": "CL", "dm": "DM", "spark": "SPARK", "fan": "FAN", "servo": "SERVO"}[key]
                active = self.connected and float(reported.get(field, 0)) != 0
                if key == "servo":
                    requested = (isinstance(values["servo"], tuple) or values["winding_auto"]
                                 or bool(values["servo_hold"])
                                 or (values["servo"] == "auto_hold" and (self.winding_ready or self.oscillator_tared)))
                elif key == "cl":
                    requested = values["auto"] or values["cl"] != 0
                else:
                    requested = values[key] != 0 or (key == "dm" and values["spool_auto"])
                if active and requested:
                    state, color = "running", "#16a34a"
                    caption = "Holding" if key == "servo" and float(reported.get("OMEGA", -1)) == 0 else "Running"
                    detail = "Active command confirmed; motion is not measured."
                else:
                    state, color = "stopped", "#b91c1c"
                    caption = "Stopping" if active and not requested else "Starting" if self.connected and requested else "Stopped"
                    detail = ("Waiting for Mega confirmation." if caption != "Stopped"
                              else "Output stopped; driver/servo may remain powered.")
            self.control_indicator_states[key] = state
            self.control_indicator_text[key].set(caption)
            self.control_indicator_details[key] = detail
            indicator.configure(foreground=color, activeforeground=color)
            if key in self.motor_status:
                if state == "running" and key == "cl" and values["auto"]:
                    self.motor_status[key].configure(text=f"AUTO {float(reported['CL']):.3f} RPM")
                else:
                    self.motor_status[key].configure(text=caption.upper())

    def add_motor(self, parent, title, key, maximum, unit, reverse, details=""):
        panel = ttk.LabelFrame(parent, padding=7)
        self.add_control_heading(panel, key, title, details)
        panel.pack(fill="x", pady=4)
        if key == "dm":
            self.spool_motor_panel = panel
            modes = ttk.Frame(panel)
            modes.pack(anchor="w", pady=(0, 5))
            self.spool_motor_selection = tk.StringVar(value=self.spool_motor_mode.get())
            self.spool_motor_mode_buttons = {}
            for name in ("Manual", "Automatic"):
                button = ttk.Radiobutton(modes, text=name, value=name, variable=self.spool_motor_selection,
                                         command=lambda value=name: self.set_spool_motor_mode(value))
                button.pack(side="left", padx=8)
                self.spool_motor_mode_buttons[name] = button
                add_tooltip(button, lambda value=name: self.mode_tooltip("spooler", value))
            self.spool_baseline_warning = ttk.Label(modes, foreground="#b91c1c",
                text="Warning: feedback control disabled; using manual input as baseline", wraplength=580)
            ttk.Label(panel, textvariable=self.spool_motor_text, wraplength=900).pack(anchor="w")
        var = tk.DoubleVar(value=0)
        entry_value = tk.StringVar(value="0")
        self.speed_vars[key] = var
        readout = ttk.Label(panel, text=f"0.0 {unit}")
        readout.pack(anchor="e")
        self.motor_readouts[key] = readout
        scale = tk.Scale(panel, from_=0, to=maximum, resolution=0.1 if key in ("cl", "dm") else 1,
                         orient="horizontal", showvalue=False, variable=var,
                         command=lambda v: (entry_value.set(f"{float(v):g}"),
                                            readout.config(text=f"{float(v):.1f} {unit}"),
                                            self.apply_running_motor(key)))
        scale.pack(fill="x")
        row = ttk.Frame(panel)
        row.pack(fill="x")
        entry = ttk.Entry(row, width=9, textvariable=entry_value)
        entry.pack(side="left")
        def apply_entry():
            try:
                value = float(entry.get())
                if not math.isfinite(value) or not 0 <= value <= maximum:
                    raise ValueError
                var.set(value)
                readout.config(text=f"{value:.1f} {unit}")
                self.apply_running_motor(key)
            except ValueError:
                self.response_text.set(f"Enter 0 to {maximum} {unit}.")
        apply = ttk.Button(row, text="Set", command=apply_entry)
        apply.pack(side="left", padx=4)
        add_tooltip(apply, "Apply the entered speed; stopped motors stay off.")
        start = ttk.Button(row, text="START", style="Start.TButton", command=lambda: self.start_motor(key))
        start.pack(side="left", padx=4)
        add_tooltip(start, lambda: "Start automatic spooling." if key == "dm" and self.spool_motor_mode.get() == "Automatic"
                    else "Start this motor at the set speed.")
        self.motor_start_buttons[key] = start
        if key == "dm":
            self.spool_auto_start = start
        stop = ttk.Button(row, text="STOP", style="Stop.TButton", command=lambda: self.stop_motor(key))
        stop.pack(side="left")
        add_tooltip(stop, "Stop this motor.")
        self.motor_stop_buttons[key] = stop
        widgets = [scale, entry, apply, start]
        self.reverse_vars[key] = tk.BooleanVar(value=False)
        if reverse:
            check = ttk.Checkbutton(row, text="Reverse", variable=self.reverse_vars[key], command=lambda: self.apply_running_motor(key))
            check.pack(side="left", padx=6)
            widgets.append(check)
            add_tooltip(check, "Reverse motor direction.")
        label = ttk.Label(row, text="STOPPED")
        label.pack(side="right")
        self.motor_status[key], self.motor_widgets[key] = label, widgets

    def apply_running_motor(self, key):
        if key == "dm" and self.spool_motor_mode.get() == "Automatic":
            if self.mode.get() == "Disabled":
                value = self.speed_vars["dm"].get()
                if not math.isfinite(value) or not 0 <= value <= MAX_DM_RPM:
                    self.response_text.set("Baseline RPM must be 0-20.")
                    return
                self.state.set(spool_baseline_rpm=value,
                               spool_direction=-1 if self.reverse_vars["dm"].get() else 1)
            return
        if self.manual_running[key] and not (key == "cl" and self.mode.get() != "Manual"):
            value = self.speed_vars[key].get()
            if key == "fan":
                value = round(value * 255 / 100)
            elif self.reverse_vars[key].get():
                value = -value
            if key == "spark":
                value = round(value)
            self.state.set(**{key: value})

    def start_motor(self, key):
        if not self.connected or (key == "cl" and not self.cl_ready):
            self.response_text.set("Connect the Mega and check motor readiness.")
            return
        if key == "cl" and self.mode.get() != "Manual":
            return
        if key == "dm" and self.spool_motor_mode.get() == "Automatic":
            values = self.state.snapshot()
            if not self.winding_ready or not values["winding_auto"] or values["servo_hold"]:
                self.spool_motor_text.set("Reset extrusion, then START the Automatic traverser first.")
                return
            self.manual_running["dm"] = False
            self.control_faults.pop("dm", None)
            baseline = self.speed_vars["dm"].get()
            if not math.isfinite(baseline) or not 0 <= baseline <= MAX_DM_RPM:
                self.response_text.set("Baseline RPM must be 0-20.")
                return
            self.state.set(spool_auto=True, dm=0, spool_baseline_rpm=baseline,
                           spool_direction=-1 if self.reverse_vars["dm"].get() else 1)
            self.spool_motor_text.set("Automatic: scaled manual baseline; max 20 RPM."
                                      if self.mode.get() == "Disabled" else
                                      "Automatic: matches puller speed; max 20 RPM.")
            self.update_controls()
            return
        self.manual_running[key] = True
        self.control_faults.pop(key, None)
        if key == "spark":
            self.state.set(arm=True)
        self.apply_running_motor(key)
        self.motor_status[key].config(text="RUNNING")
        self.update_controls()

    def stop_motor(self, key):
        self.control_faults.pop(key, None)
        self.manual_running[key] = False
        self.state.set(**{key: 0})
        if key == "dm":
            self.state.set(spool_auto=False)
        if key == "cl":
            self.stop_automatic()
        self.motor_status[key].config(text="STOPPED")
        self.update_controls()

    def mode_changed(self):
        self.state.set(puller_disabled=self.mode.get() == "Disabled",
                       spool_baseline_rpm=self.speed_vars["dm"].get())
        self.stop_automatic()
        self.response_text.set("Puller disabled; output 0 RPM." if self.mode.get() == "Disabled"
                               else f"{self.mode.get()} mode selected. Puller stopped; press START to run.")
        self.update_controls()

    def set_spool_motor_mode(self, name):
        if name not in ("Manual", "Automatic"):
            return
        if name != self.spool_motor_mode.get():
            self.stop_motor("dm")
            self.spool_motor_mode.set(name)
            self.motor_readouts["dm"].configure(text=f"{self.speed_vars['dm'].get():.1f} RPM" if name == "Manual" else "0.000 RPM")
            self.spool_motor_text.set(f"{name} mode. Press START. Automatic requires the Automatic traverser.")
        self.update_controls()

    def apply_spool_geometry(self):
        try:
            geometry = SpoolGeometry(**{key: float(var.get()) for key, var in self.spool_geometry_vars.items()})
            geometry.validate()
            previous = self.state.snapshot()["winding_geometry"]
            if (self.state.snapshot()["winding_auto"] or self.state.snapshot()["spool_auto"]) and geometry != previous:
                raise ValueError("Stop the automatic sweep before changing its geometry.")
            if self.spooling_settings_path is not None:
                save_geometry(geometry, self.spooling_settings_path)
            self.state.set(winding_geometry=geometry)
            if geometry != previous:
                self.winding_ready = False
                self.geometry_needs_reference = True
            self.geometry_status.set("Spool geometry saved."
                                     if self.spooling_settings_path is not None else "Spool geometry applied.")
            return True
        except (OSError, ValueError, OverflowError) as error:
            self.geometry_status.set(str(error))
            return False

    def set_spooling_mode(self, name):
        if name not in ("Manual", "Automatic"):
            return
        if name != self.spooling_mode.get():
            self.detach_spooling()
            self.spooling_mode.set(name)
            self.spooling_status.set(f"{name} mode. Set inputs, then START.")
        self.update_controls()

    def pause_spooling(self):
        values = self.state.snapshot()
        if not self.connected or not self.pause_supported:
            self.spooling_status.set("Pause/Resume requires a connected Mega with updated firmware.")
            return
        if values["servo_hold"] == "paused":
            self.state.set(servo_hold=None)
            self.spooling_status.set("Sweep resumed.")
        elif values["servo_hold"] not in ("centered", "angle_hold") and (isinstance(values["servo"], tuple) or values["winding_auto"]):
            self.state.set(servo_hold="paused")
            self.spooling_status.set("Sweep paused. Press Resume Sweep to continue.")
        self.update_controls()

    def applied_servo_limits(self):
        values = self.state.snapshot()
        if values["winding_auto"]:
            geometry = values["winding_geometry"]
            return geometry.minimum_angle, geometry.maximum_angle
        if isinstance(values["servo"], tuple):
            center, _, half = values["servo"][:3]
            return center-half, center+half
        center, _, half, _ = self.manual_sweep_settings
        return center-half, center+half

    def read_servo_limits(self):
        minimum, maximum = (float(self.servo_vars[k].get()) for k in ("minimum", "maximum"))
        if not all(math.isfinite(x) for x in (minimum, maximum)) or not -90 <= minimum < maximum <= 90:
            raise ValueError("Sweep: -90 <= Min < Max <= 90 deg.")
        return minimum, maximum

    def set_servo_angle(self, hold=False):
        if not self.connected or not self.angle_supported:
            self.spooling_status.set("Set Angle requires updated Mega firmware.")
            return
        try:
            minimum, maximum = self.applied_servo_limits()
            angle = (minimum+maximum)/2 if hold else float(self.servo_angle_input.get())
            if not math.isfinite(angle) or not minimum <= angle <= maximum:
                raise ValueError(f"Angle must be between {minimum:g} and {maximum:g} deg (applied limits).")
        except (ValueError, OverflowError) as error:
            self.spooling_status.set(str(error))
            return
        self.control_faults.pop("servo", None)
        self.state.request_servo_angle(angle, hold=hold)
        held = self.state.snapshot()["servo_hold"]
        self.spooling_status.set(f"Moving to {angle:g} deg. " +
                                ("Set or START resumes." if hold else "Sweep remains paused." if held else "Sweep continues."))
        self.update_controls()

    def center_spooling(self):
        self.set_servo_angle(hold=True)

    def detach_spooling(self):
        self.control_faults.pop("servo", None)
        self.state.set(winding_auto=False, servo=None, servo_hold=None, servo_angle_target=None, servo_angle_hold=False)
        self.spooling_status.set("Sweep stopped; servo pulses off. START uses the starting angle.")
        self.update_controls()

    def update_spooling_readout(self):
        if not self.connected:
            self.calculated_spool_rpm = None
            self.spooler_text.set("Calculated Spool RPM: N/A (Mega disconnected)")
            return
        values = self.state.snapshot()
        puller_rpm = abs(float((self.last_mega_status or {}).get("CL", 0)))
        velocity = linear_velocity(puller_rpm, values["automatic"].wheel_diameter)
        try:
            result = spool_rpm_command(values["winding_geometry"], self.winding_layers, velocity,
                                       baseline_rpm=self.speed_vars["dm"].get() if values["puller_disabled"] else None,
                                       direction=-1 if self.reverse_vars["dm"].get() else 1,
                                       limit=MAX_DM_RPM)
        except (ValueError, OverflowError, tk.TclError) as error:
            self.calculated_spool_rpm = None
            self.spooler_text.set(f"Calculated Spool RPM: unavailable ({error})")
            return
        holding = values["spool_auto"] and (not values["winding_auto"] or values["servo_hold"])
        self.calculated_spool_rpm = 0.0 if holding else result["rpm"]
        usage = "Automatic command" if values["spool_auto"] else "Preview only"
        source = "scaled manual baseline" if values["puller_disabled"] else "puller velocity"
        suffix = " | holding for traverser" if holding else " | 20 RPM limit" if result["limited"] else ""
        self.spooler_text.set(f"Calculated Spool RPM: {self.calculated_spool_rpm:.3f} RPM | {usage} | {source}{suffix}")

    def read_automatic_settings(self):
        settings = AutomaticSettings(**{key: float(var.get()) for key, var in self.automatic_vars.items()})
        settings.validate()
        return settings

    def apply_automatic_settings(self):
        try:
            settings = self.read_automatic_settings()
        except (ValueError, OverflowError) as error:
            self.auto_status.set(str(error))
            return False
        self.state.set(automatic=settings)
        self.auto_status.set("Settings applied. Tune gain on the machine.")
        return True

    def tare_n(self):
        if not self.connected or not self.winding_supported:
            self.spooling_status.set("Reset requires a connected Mega with updated winding firmware.")
            return
        if (self.state.snapshot()["dm"] != 0 or self.state.snapshot()["spool_auto"]
                or (self.reported_spooler_rpm is not None and abs(self.reported_spooler_rpm) > .001)):
            self.spooling_status.set("Stop the spooler before Extrusion Reset.")
            return
        if not self.apply_spool_geometry():
            return
        self.winding_ready = False
        self.control_faults.pop("servo", None)
        self.state.request_winding_tare()
        self.spooling_status.set("Resetting layers and length to 635 mm. Wait for the guide to reach minimum.")

    def start_automatic(self):
        if self.mode.get() != "Automatic" or not self.connected or not self.cl_ready:
            self.auto_status.set("Select Automatic and connect the Mega.")
            return
        packet, _ = self.camera.snapshot()
        if live_diameter(packet) is None:
            self.auto_status.set("Live camera diameter required.")
            return
        if not self.apply_automatic_settings():
            return
        self.manual_running["cl"] = False
        self.control_faults.pop("cl", None)
        self.state.set(auto=True, cl=0.0)
        self.auto_status.set("Automatic puller running.")
        self.update_controls()

    def stop_automatic(self):
        self.control_faults.pop("cl", None)
        was_automatic = self.state.snapshot()["auto"]
        self.state.set(auto=False, cl=0.0)
        if was_automatic:
            self.velocity_text.set("Puller speed estimate: 0 mm/s (stopped)")
        self.manual_running["cl"] = False
        self.motor_status["cl"].config(text="STOPPED")
        self.auto_status.set("Automatic puller stopped.")
        self.update_controls()

    def update_controls(self):
        automatic = self.mode.get() == "Automatic"
        spool_automatic = self.spool_motor_mode.get() == "Automatic"
        for key, widgets in self.motor_widgets.items():
            enabled = self.connected
            if key == "cl":
                enabled = enabled and self.cl_ready and self.mode.get() == "Manual"
            elif key == "dm":
                enabled = enabled and (not spool_automatic or self.mode.get() == "Disabled")
            for widget in widgets:
                widget.configure(state="normal" if enabled else "disabled")
            self.motor_stop_buttons[key].configure(state="normal" if self.connected else "disabled")
        running = self.state.snapshot()["auto"]
        sweep_automatic = self.spooling_mode.get() == "Automatic"
        for widget in self.servo_widgets:
            widget.configure(state="normal" if self.connected and not sweep_automatic else "disabled")
        self.spooling_selection.set(self.spooling_mode.get())
        self.spool_motor_selection.set(self.spool_motor_mode.get())
        if spool_automatic and self.mode.get() == "Disabled":
            self.spool_baseline_warning.pack(side="left", padx=8)
        else:
            self.spool_baseline_warning.pack_forget()
        self.spool_auto_start.configure(state="normal" if self.connected and not self.state.snapshot()["spool_auto"] else "disabled")
        winding_running = self.state.snapshot()["winding_auto"] or self.state.snapshot()["spool_auto"]
        for entry in self.spool_geometry_entries:
            entry.configure(state="disabled" if winding_running else "normal")
        for entry in self.servo_limit_widgets:
            entry.configure(state="normal" if self.connected and not winding_running else "disabled")
        self.save_geometry_button.configure(state="disabled" if winding_running else "normal")
        self.sweep_apply_button.configure(state="normal" if self.connected else "disabled")
        values = self.state.snapshot()
        held = values["servo_hold"]
        active = isinstance(values["servo"], tuple) or values["winding_auto"]
        self.sweep_start_button.configure(state="normal" if self.connected and (not active or held) else "disabled")
        self.sweep_stop_button.configure(state="normal" if self.connected else "disabled")
        self.sweep_center_button.configure(state="normal" if self.connected and self.angle_supported else "disabled")
        for widget in (self.servo_angle_entry, self.servo_angle_button):
            widget.configure(state="normal" if self.connected and self.angle_supported else "disabled")
        self.sweep_pause_button.configure(text="Resume Sweep" if held == "paused" else "Pause Sweep",
                                         state="normal" if self.connected and self.pause_supported and active
                                         and held not in ("centered", "angle_hold") else "disabled")
        self.tare_button.configure(state="normal" if self.connected and self.winding_supported
                                   and not self.state.snapshot()["spool_auto"] and self.state.snapshot()["dm"] == 0
                                   and (self.reported_spooler_rpm is None or abs(self.reported_spooler_rpm) < .001) else "disabled")
        self.auto_start.configure(state="normal" if self.connected and self.cl_ready and automatic and not running else "disabled")
        self.auto_stop.configure(state="normal" if self.connected else "disabled")
        self.auto_apply.configure(state="normal" if self.connected else "disabled")
        self.stop_all_button.configure(state="normal" if self.connected else "disabled")
        self.port_box.configure(state="disabled" if self.worker is not None else "readonly")
        self.update_control_indicators()

    def set_servo(self, hold=False):
        if hold:
            self.center_spooling()
            return
        if not self.connected:
            self.response_text.set("Connect the Mega first.")
            return
        values = self.state.snapshot()
        if self.spooling_mode.get() == "Automatic":
            if not self.apply_spool_geometry():
                return
        else:
            try:
                minimum, maximum = self.read_servo_limits()
                speed, start = (float(self.servo_vars[k].get()) for k in ("speed", "start"))
                if (not all(math.isfinite(x) for x in (speed, start))
                        or not 0 < speed <= 360 or not minimum <= start <= maximum):
                    raise ValueError("Sweep: starting angle must be within Min/Max; speed >0-360 deg/s.")
            except (ValueError, OverflowError) as error:
                self.response_text.set(str(error))
                return
            self.manual_sweep_settings = ((minimum+maximum)/2, speed, (maximum-minimum)/2, start)
            if isinstance(values["servo"], tuple):
                self.state.set_manual_sweep(*self.manual_sweep_settings, restart_unchanged=False)
        if values["servo_hold"] == "centered":
            self.start_servo()
            return
        self.spooling_status.set("Sweep settings saved." + (" Sweep remains paused." if values["servo_hold"] else " Press START if stopped."))
        self.update_controls()

    def start_servo(self):
        if not self.connected:
            self.response_text.set("Connect the Mega first.")
            return
        values = self.state.snapshot()
        if self.spooling_mode.get() == "Automatic":
            if not self.winding_supported or not self.winding_ready:
                self.spooling_status.set("Updated winding firmware and Extrusion Reset required.")
                return
            self.state.set(winding_auto=True, servo="auto_hold", servo_hold=None)
            self.spooling_status.set("Automatic sweep running; holds at zero spooler RPM.")
        else:
            self.state.set_manual_sweep(*self.manual_sweep_settings, restart_unchanged=False)
            self.state.set(servo_hold=None)
            self.geometry_needs_reference = True
            self.winding_ready = False
            self.spooling_status.set("Manual sweep running.")
        self.control_faults.pop("servo", None)
        self.update_controls()

    def stop_all(self):
        self.control_faults.clear()
        self.state.stop_all()
        self.oscillator_tared = False
        self.spooling_status.set("All outputs stopped. Press START to restart.")
        for key in self.manual_running:
            self.manual_running[key] = False
            self.motor_status[key].config(text="STOPPED")
        self.auto_status.set("Automatic stopped.")
        self.response_text.set("STOP ALL requested.")
        self.update_controls()

    def poll(self):
        global camera_diameter_mm, camera_frame_sequence
        if self.closing:
            return
        packet, link_status = self.camera.snapshot()
        camera_diameter_mm = live_diameter(packet)
        self.diameter_filter.observe(packet)
        filtered, count = self.diameter_filter.snapshot()
        settings = self.state.snapshot()["automatic"]
        if camera_diameter_mm is None or filtered is None:
            self.filtered_diameter_text.set("Control average: waiting for live diameter.")
        elif count < AVERAGE_WINDOW:
            self.filtered_diameter_text.set(
                f"Control average: {filtered:.4f} mm | {count}/20 readings; base speed until ready.")
        else:
            error = deadband_error(filtered - settings.target, settings.deadband)
            self.filtered_diameter_text.set(
                f"Control average (20 readings): {filtered:.4f} mm | "
                + (f"within +/-{settings.deadband:g} mm band; no correction."
                   if error == 0 else f"error beyond band: {error:+.4f} mm."))
        camera_frame_sequence = packet.get("frame_sequence") if packet is not None else None
        self.camera_diameter_text.set(f"{camera_diameter_mm:.4f}" if camera_diameter_mm is not None else "N/A")
        if camera_diameter_mm is not None:
            self.camera_status_text.set(f"Live | camera frame {camera_frame_sequence}")
        else:
            reason = packet.get("reason", "Camera measurement unavailable") if packet is not None else link_status
            if packet is not None and packet.get("valid") is True:
                reason = "Camera reading expired or invalid"
            self.camera_status_text.set(reason)
        if self.recorder.recording:
            self.recording_statistics.observe(packet, camera_diameter_mm, settings.target)
        diagnostics = self.update_statistics_readout()
        while True:
            try:
                kind, info = self.events.get_nowait()
            except queue.Empty:
                break
            if kind in ("connected", "status", "tare_complete", "winding_tared"):
                self.pause_supported = info.get("PAUSE_V") == "1"
                self.angle_supported = info.get("ANGLE_V") == "1"
                self.last_mega_status = dict(info)
                self.last_mega_status_at = time.monotonic()
                self.reported_spooler_rpm = float(info["DM"])
                if self.spool_motor_mode.get() == "Automatic":
                    self.motor_readouts["dm"].configure(text=f"{self.reported_spooler_rpm:.3f} RPM")
                if "LENGTH_MM" in info:
                    self.extruded_length_text.set(f"{float(info['LENGTH_MM']):.1f}")
                    self.spool_diagnostics_text.set(
                        f"Layers: {info.get('LAYERS', '0')} | Coil diameter: {2 * float(info.get('RADIUS', '43.5')):.3f} mm | "
                        f"Filament velocity: {float(info['TAKEUP_MM_S']):.3f} mm/s | "
                        f"Extruded length: {float(info['LENGTH_MM']) / 1000:.3f} m (estimate)")
                telemetry = oscillator_status(info)
                self.oscillator_supported = telemetry is not None
                self.oscillator_tared = telemetry is not None and telemetry["tared"]
                if telemetry is not None:
                    self.oscillator_n = telemetry["n"]
                    self.oscillator_text.set(
                        f"n: {telemetry['n']} full trips | commanded angle: {telemetry['theta']:.2f} deg | "
                        f"speed: {telemetry['omega']:.4f} deg/s | "
                        + ("tared" if telemetry["tared"] else "tare required"))
                else:
                    self.oscillator_text.set("Update Mega firmware for sweep diagnostics.")
                winding = winding_status(info)
                self.winding_supported = winding is not None
                if kind == "winding_tared":
                    self.geometry_needs_reference = False
                self.winding_ready = winding is not None and winding["ready"] and not self.geometry_needs_reference
                if winding is not None:
                    self.winding_layers = winding["layers"]
                    self.oscillator_text.set(
                        f"Layer: {winding['layers']} | "
                        f"trips: {info.get('N', '?')} | angle: {float(info['THETA']):.2f} deg | "
                        f"speed: {float(info['OMEGA']):.3f} deg/s | "
                        + ("reference set" if self.winding_ready else "reset layers required"))
                if kind == "winding_tared":
                    self.spooling_status.set("Reset complete. Let the guide settle, then START traverser and spooler.")
                if kind == "tare_complete":
                    self.spooling_status.set("Sweep referenced. Extrusion Reset required for Automatic spooling.")
            if kind == "connected":
                self.link_failure = None
                self.control_faults.clear()
                self.connected, self.cl_ready = True, info["CL_READY"] == "1"
                self.connection_text.set(f"Connected: {self.port.get()}")
            elif kind == "status":
                self.cl_ready = info["CL_READY"] == "1"
                self.response_text.set(f"Commands: puller {info['CL']} RPM | spooler {info['DM']} RPM | extruder {info['SPARK']}% | fans {float(info['FAN']) / 255 * 100:.0f}%")
                settings = self.state.snapshot()["automatic"]
                vf = linear_velocity(abs(float(info["CL"])), settings.wheel_diameter)
                self.velocity_text.set(f"Puller speed estimate: {vf:.3f} mm/s")
            elif kind == "auto_output" and self.state.snapshot()["auto"]:
                self.motor_status["cl"].config(text=f"AUTO {info['rpm']:.3f} RPM")
                self.velocity_text.set(f"Puller speed estimate: {info['vf']:.3f} mm/s")
            elif kind == "spool_output" and self.state.snapshot()["spool_auto"]:
                self.spool_motor_text.set(f"Automatic: {info['rpm']:.3f} RPM | "
                                         + ("20 RPM limit; target speed not reached." if info['limited']
                                            else "Scaled manual baseline." if self.mode.get() == "Disabled"
                                            else "Matching puller speed."))
            elif kind == "spool_fault":
                self.control_faults["dm"] = str(info)
                self.spool_motor_text.set(f"Automatic spooling stopped: {info}")
            elif kind == "winding_fault":
                self.control_faults["servo"] = str(info)
                self.spooling_status.set(f"Automatic sweep stopped: {info}")
            elif kind == "auto_fault":
                self.stop_all()
                self.control_faults["cl"] = str(info)
                self.auto_status.set(f"Automatic stopped: {info} Restart required.")
            elif kind == "error":
                self.link_failure = str(info)
                self.connected = False
                self.stop_all()
                self.connection_text.set(f"Mega error: {info}")
            elif kind == "disconnected":
                if not self.disconnect_requested and not self.closing:
                    self.link_failure = self.link_failure or "Unexpected Mega disconnection."
                self.connected = False
                self.winding_ready = self.winding_supported = False
                self.geometry_needs_reference = True
                self.reported_spooler_rpm = None
                self.spool_diagnostics_text.set(f"Layers: {self.winding_layers} | Filament velocity: -- (disconnected) | Extruded length: {self.takeup.extruded_length_mm / 1000:.3f} m")
                self.worker = None
                self.stop_all()
                if not self.connection_text.get().startswith("Mega error:"):
                    self.connection_text.set("Mega disconnected.")
        self.recorder.record(packet, camera_diameter_mm, self.camera_status_text.get(), diagnostics,
                             self.recording_motor_snapshot())
        self.poll_recording()
        self.check_camera_configuration_result()
        self.update_diagnostic_motor_readouts()
        self.update_spooling_readout()
        self.update_controls()
        self.root.after(100, self.poll)

    def close(self):
        self.closing = True
        self.recorder.stop()
        self.recording_statistics.stop()
        self.stop_all()
        if self.worker is not None:
            self.worker.stop.set()
        self.camera.close()
        self.root.after(50, self.finish_close)

    def finish_close(self):
        if (self.worker is not None and self.worker.is_alive()) or self.recorder.busy or self.recording_exporter.busy:
            self.root.after(50, self.finish_close)
        else:
            self.root.destroy()


def main():
    configure_app_identity("PLARecycler.Controls")
    root = tk.Tk()
    Recycler(root, spooling_settings_path=SPOOL_SETTINGS_PATH)
    root.mainloop()


if __name__ == "__main__":
    main()
