# Mech3-Team2-PLA_Recycler

## Run the interfaces

Launch.exe arranges the recycler on the left and camera viewer on the right,
each occupying half the available screen on the monitor containing the mouse
pointer. Running it again restores and arranges existing windows.

Double-click **Launch.exe** in the project folder to open both the camera viewer
and recycler controls. It uses its own folder as the working directory and
restores existing windows instead of opening duplicates. Camera settings and
calibration restore automatically; the puller starts Disabled; spooler and traverser start Manual, with
outputs stopped. Select the Mega port and connect separately. Keep the launcher
beside `.venv-camera`, `OV7670Camera`, `Recycler_Python_Servo.py` and
`recycler_logic.py`; it is a launcher, not a bundled Python installation.

You can also open `Launch Camera.exe` for the camera alone. Enter the camera
geometry so its diameter field contains a valid reading. To run the recycler
interface separately from the project folder:

```powershell
.\.venv-camera\Scripts\python.exe Recycler_Python_Servo.py
```

Select the Mega's USB port and click **Connect**. The interface checks the Mega V2
protocol and stops its outputs before enabling operation. Merely opening the
interface, connecting, or selecting a mode does not start motors. The camera and
Mega use separate USB ports; the programs exchange measurements over localhost.
Live camera monitoring also works with no Mega connected.

## Interface panels

The recycler uses the camera viewer's section-header styling and Segoe UI title
font. Click a section header to expand/collapse it; doing so preserves input
values and does not start/stop any control.

- **Connection:** Mega USB port and connection buttons.
- **Feedback Control:** Puller mode, Diameter Control Puller Motor (CL42T), Automatic
  Controls (including the nested Speed Limits and Wheel Diameter). Calculated Spool RPM
  is shown under Spooling Diagnostics.
- **Main Controls**, in order: Extrusion Motor (SPARK MAX), Cooling Fans,
  then **Spooling Controls**, containing diagnostics, Spooling Traverser Servo,
  Spooling Motor (DM stepper), and a collapsed **Spool Geometry** panel at the bottom.

The live measurement/recording panel and STOP ALL remain outside these groups.
Spool Geometry and Speed Limits and Wheel Diameter start collapsed; the main
sections start expanded.

Each bold control heading has a clickable status light. Green means the Mega
reports an active output and that output is still requested; red means stopped
or transitioning to/from stopped. A held servo stays green with **Holding**, since
its PWM control remains active. Flashing red indicates a signal/control failure.
Loss of the shared Mega connection (or status older than two seconds) flashes all
five lights; a camera-input fault marks Diameter Control Puller Motor, while a traverser
control fault marks Spooling Traverser Servo. An intentional Disconnect shows
steady red. Successful reconnection clears connection faults. STOP ALL acknowledges
component faults; retries clear the affected component fault.

Click a status light to show details in the status message; it never starts or
stops hardware. Lights use commanded/reported signal state, not encoders or motor
alarm inputs. They cannot detect a physically stalled/disconnected motor, broken
fan or overheating driver unless a corresponding error is reported. Red does not
mean driver/servo power has been disconnected.

## Record mean diameter

In the recycler interface's **Live camera measurement** panel, click **Start
Recording**, then **Stop Recording** when finished. Each session creates a new
timestamped CSV in the project's `recordings` folder. The panel shows recording
state, written row count and the full file path. Recording does not require a
Mega connection or start any outputs. Closing the interface finishes the file.

Columns are `timestamp_utc` (time logged), `elapsed_s` since Start, camera
`frame_sequence`, `mean_diameter_mm` (same four decimals as the display), `valid`
(1 or 0), and `status`. New camera frames and validity/status transitions are
recorded; duplicate heartbeat messages and unchanged GUI polls are omitted.
Missing, invalid or stale readings have a blank diameter and valid=0, so gaps
cannot be mistaken for zeros or held measurements. CSV files can open in Excel
or be used for later calculations. Disk writes run in a background thread; write
errors stop recording and are shown in the panel. Recordings are ignored by Git.

## Manual mode

Use each output's slider or numeric **Set** field, then **START**:

- **Diameter Control Puller Motor** (CL42T): D30 STEP / D31 DIR, 0-100 RPM, optional reverse.
- **Spooling Motor** (DM stepper): D40 STEP / D41 DIR, 0-20 RPM, optional reverse.
- **Extrusion Motor** (SPARK MAX): D9, 0-100% command, optional reverse. START first sends
  ARM, then the requested SPARK command. This is a percent command, not measured RPM.
- **Cooling Fans**: D6/D7, 0-100%, converted to firmware PWM values 0-255.
- **Spooling Traverser Servo**: D10. Defaults: speed **0.25 deg/s**, Min **-31 degrees**,
  Max **+31 degrees**, starting angle **+31 degrees**. Min and Max are independent
  and shared with Automatic mode. Use -90 <= Min < Max <= 90 degrees; the starting
  angle must be within these limits.

**Set** saves the inputs without starting a stopped sweep. **START** runs the last
Set values (or initial defaults), beginning at the starting angle after STOP.
A start above the range midpoint heads toward Min; otherwise it heads toward Max.
Speed-only Set during a sweep preserves its current angle and direction.
An unchanged Set does not restart it; changing range/start during a running
sweep restarts at the new start. Set while paused keeps the sweep paused.

**Pause Sweep** holds the current commanded angle and changes to **Resume Sweep**.
Resume continues the stored sweep from that angle with its saved direction and
speed; unsaved input edits have no effect. **Set Angle** moves directly to the entered angle within the applied limits.
A running sweep continues with the same speed and direction; a paused sweep stays
paused until Resume. At an endpoint, direction points inward. A stopped servo
holds the selected angle until START. Repositioning does not increment layers.
**Center / Hold** moves to the midpoint between Min and Max and waits for Set or
START to continue, keeping direction.
The physical servo still needs time to move. **STOP** disables servo pulses and
clears pause; it does not stop the spooler motor. Center/Hold and Pause keep
pulses enabled. Automatic resumes from the held position with speed determined
by current spooler RPM, preserving the layer reference within its angle limits.

Upload the updated Mega firmware for these controls. STATUS advertises
SWEEP_V=1, PAUSE_V=1 and ANGLE_V=1. SWEEP takes center, speed, half-range and start;
SWEEPSPEED changes only speed. SWEEPPAUSE / SWEEPRESUME retain sweep state,
SWEEPANGLE takes an angle and a hold flag (0 preserves state; 1 pauses).
SWEEPCENTER remains available for legacy zero parking; SWEEPCONT applies manual
settings at the current held angle. Legacy three-argument SERVO commands remain supported.

Changing an active output's setpoint updates it immediately. Changing a stopped
output's setpoint does not start it. STOP ALL stops both steppers, extruder, fans
and servo, and disarms the extruder. The window scrolls to reach all controls.

## Puller Automatic mode

The Feedback Control puller selector offers Manual, Automatic and Disabled for
the CL42T Diameter Control Puller Motor on D30/D31. Disabled stops its commanded
RPM, disables puller Start/manual edits, and bypasses camera diameter control.
The traverser and DM spooler each have their own Manual/Automatic selection;
fans and extruder remain manual. Calculated Spool RPM is shown under Spooling Diagnostics, using the same
calculation as automatic DM commands from Spooling Controls.
The existing 20-reading average, deadband, proportional gain and rate limits
continue to control puller speed.
```
filtered_diameter = average(last 20 distinct valid diameter measurements)
error = filtered_diameter - target_diameter_mm
control_error = sign(error) * max(abs(error) - deadband_mm, 0)
puller_RPM = base_RPM + K_puller * control_error
V_f = pi * wheel_diameter_mm * commanded_puller_RPM / 60
spooler_motor_RPM = 60 * V_f / (2*pi*effective_radius)
```

V_f is in mm/s. The direct-drive wheel diameter defaults to **0.886 inches =
22.5044 mm**. Puller RPM is clamped to the configured min/max (maximum 100 RPM), and
changes obey the RPM/s slew limit. The proportional correction is not accumulated;
there is no integral or derivative term. New camera frames update the diameter;
the rate-limited speed can continue approaching its target between fresh frames.
Velocity is estimated from commanded RPM, not a measured filament speed.

### Diameter smoothing and allowed variation

**Diameter tolerance [+/- mm]** is the deadband half-width, initially
**0.1 mm**. With a 1.75 mm target, a filtered diameter from 1.65 through 1.85 mm
produces no correction: the target RPM is the normal/base speed. Set the deadband
to zero to disable it. Outside the band, only the excess beyond its edge is used,
so correction starts continuously instead of jumping when a boundary is crossed.
For example, mean 1.80 mm, target 1.75 mm, deadband 0.02 mm and gain 2 produce
0.06 RPM correction (before speed/rate limits).

The GUI and motor worker share a rolling average of **20 distinct valid camera
frames**, collected even while you monitor in Manual mode or without the Mega.
Repeated heartbeat messages/GUI polls never count as extra measurements. The
Automatic panel shows the average, sample count and effective error. Until all
20 samples have arrived, Automatic ramps toward normal/base RPM with no diameter
correction. Invalid samples are omitted; a gap over two seconds or a camera frame
counter restart clears the history. Reprocessing the same frame to a different
diameter also resets the reference. Changing target/gains/deadband keeps the valid
history. Edits take effect with **Apply Settings** or START.

Averaging adds delay (roughly 9.5 frame intervals for a 20-sample moving average).
Tune gain and RPM/s rate limits with this delay in mind. Continuous deadband and
the existing speed-change limit provide additional smoothing. A missing/invalid
current camera reading still stops Automatic immediately; old averages never
substitute for a live measurement. These noise-control changes are Python-only.

## Spooling sweep: independent Manual / Automatic modes

The bottom D10 servo panel has **Manual** and **Automatic** radio buttons matching the Puller selector.
The selected dot indicates mode selection, not that hardware is moving. Switching sweep modes stops the traverser without
changing puller, SPARK or fan setpoints. An Automatic spooler holds at zero when its traverser stops. Press **Set**, then **START** to run
whichever sweep mode is selected. Manual has speed, independent Min/Max and starting-angle
inputs, with center fixed at zero and speed-only updates preserving position.

Automatic uses the spooler's DM motor RPM and emitted step pulses. It does not
require a camera or the puller's Automatic mode. Its geometry is editable and
saved in local `spooling_settings.json`, ignored by Git. Defaults agreed for this
setup are initial winding diameter **87 mm**, width **55 mm**, filament **1.75 mm**,
angle limits **-31 to +31 degrees**, and motor/spool ratio **1:1**. These are initial
measurements/estimates, not universal spool dimensions. Enter your spool's actual
measurements, and calibrate the servo endpoints to its two winding ends.

- Initial winding diameter is the bare core for an empty spool, or the current
  wound diameter if establishing a new layer-zero reference on a partly filled
  spool. It is not the flange diameter or spindle-hole diameter.
- Winding width is the clear distance between inner flange faces. The filament
  center stays half a filament diameter in from each side, so center-to-center
  traverse is `width - filament_diameter`.
- The spooler is always direct drive (1:1); there is no drive-ratio input.
  Older locally saved ratios are normalized to 1 when loaded.

```
spool_RPM = abs(DM_motor_RPM)
axial_speed_mm_s = filament_diameter * spool_RPM / 60
servo_speed_deg_s = axial_speed_mm_s * (max_angle-min_angle) / (width-filament_diameter)
effective_radius_mm = initial_winding_diameter/2 + completed_layers*filament_diameter
estimated_takeup_mm_s = 2*pi*effective_radius_mm*spool_RPM/60
```

This enforces one filament-diameter pitch per spool revolution under the linear
angle-to-distance assumption. For the default geometry at 20 motor RPM, angular
speed is approximately **0.679 deg/s**. At fixed RPM, layer radius changes take-up
speed but not pitch-derived traverse speed; there is no cosine correction or
additional inverse-radius term in the angular-speed equation.

The firmware advances the guide from **emitted DM STEP edges**, not GUI update
intervals. RPM changes therefore update the rate without returning to center or
resetting layers. Zero RPM holds the guide still. The driver's microstep setting
must match the sketch's DM_PPR (1600 pulses per motor revolution). Reverse motor
rotation is treated as winding at the same speed magnitude; use Manual for
unwinding. There is no encoder: slipping/stalled motors, guide backlash and servo
calibration still require hardware verification.

### Start, reset and pause

1. Upload the updated Mega sketch once and connect the Python interface. Select
   **Automatic** in the bottom servo panel; leave the puller mode as needed.
2. Enter measurements and click **Save geometry**. Geometry changes are locked
   while the automatic sweep is selected as running or paused; STOP before changing them.
3. Stop the spooler motor, then press **Extrusion Reset**. This
   moves the servo to its configured minimum and establishes layer 0. Wait for
   physical settling. It also sets estimated extruded length to 635 mm after the Mega acknowledges the reset. It does not start either motor.
4. Click traverser **START**, then START the spooler at the desired manual RPM.
   The guide holds until spooler rotation begins and follows RPM changes.
5. **Pause Sweep / Resume Sweep** holds/resumes its current angle and preserves the layer/round-trip
   reference. Traverser **STOP** stops servo pulses. These controls hold the
   Automatic spooler at zero RPM; the Manual spooler must be stopped separately.

Each arrival at **either** endpoint increments completed layers by one and adds
one filament diameter to effective radius. The older `n` display still counts a
full min -> max -> min round trip (two layers). Layer 0 uses the entered initial
radius. Resetting layers explicitly resets that model; on a partly filled spool,
measure and enter current wound diameter before re-establishing layer zero.
STOP ALL detaches the servo and stops outputs while retaining commanded reference
on the Mega. Board reset loses it; manual servo movement invalidates it. A new
geometry requires a new reference. No automatic motion resumes on connection.

Puller Automatic still stops all outputs if its camera measurement becomes invalid
or stale; independently running automatic spooling does not need camera readings.
Spark motor controls and all other motor start/stop widgets are unchanged.

## Spooling motor Automatic and diagnostics

**Main Controls > Spooling Controls** contains, in order: Spooling Diagnostics,
Spooling Traverser Servo, Spooling Motor, and Spool Geometry. Both servo and motor
have independent Manual/Automatic radio buttons matching the Puller selector. Selecting
a motor mode stops the spooler; START explicitly runs it. Manual retains the
existing RPM slider, Set, START, STOP and Reverse controls.

For Automatic motor operation, configure geometry, stop the spooler and use
**Extrusion Reset**. Select Automatic on the traverser and START it, then select
Automatic on the Spooling Motor and press its START. The target linear velocity
comes from the diameter-control puller's commanded RPM and wheel diameter, in
either puller mode. No extra diameter camera requirement is imposed by spooling.

```
coil_diameter_mm = initial_winding_diameter + 2*completed_layers*filament_diameter
puller_velocity_mm_s = pi*puller_wheel_diameter*abs(puller_RPM)/60
spool_motor_RPM = 60*puller_velocity_mm_s/(pi*coil_diameter_mm)
```

When Puller mode is **Disabled**, Automatic spooling instead uses the manual
spooling RPM input as the **layer-zero baseline**. The RPM slider, entry and Set
remain available in this combination; changing the baseline updates the running
Automatic spooler without changing layers or starting a stopped motor.

```
spool_motor_RPM = manual_baseline_RPM * initial_winding_diameter / coil_diameter_mm
```

For example, an 8 RPM baseline with initial diameter 87 mm becomes approximately
6.891 RPM at four 1.75 mm layers (101 mm coil diameter). This maintains the surface
velocity implied by 8 RPM at layer zero. Extrusion Reset establishes a new layer
zero at the configured initial winding diameter. Neither the camera nor a running
puller is required in Disabled mode. Re-enabling the puller restores puller-based
spooler velocity matching. The inline red warning beside Automatic spooling is
shown only when both spooler Automatic and puller Disabled are selected:
**Warning: feedback control disabled; using manual input as baseline**.

The spooler follows puller speed and slows as layers accumulate. Its existing
20 RPM limit remains; a message reports saturation when the target cannot be
met. Automatic uses the Reverse setting chosen in Manual. Calculated Spool RPM appears in Spooling Diagnostics and uses exactly the
same shared calculation, 3-decimal rounding and 20 RPM limit as the Automatic
command. In Manual mode it remains a live preview and never overwrites the manual
motor command. When the puller is Disabled it previews the scaled manual baseline,
including when spooling is Manual. An armed Automatic spooler held by the traverser
shows zero. The old Feedback Control estimate panel and display multiplier have
been removed. Automatic spooling requires the Automatic traverser
for layer counting; pausing, centering or stopping the traverser holds the spooler
at zero. Resume/START of the traverser releases that hold if motor Automatic is
still armed. Motor STOP or a mode change disarms motor Automatic; STOP ALL also
stops every output. A stopped puller gives zero Automatic spooler RPM.

Diagnostics show completed layers, current modeled coil diameter, filament
velocity `pi*coil_diameter*abs(acknowledged_spool_RPM)/60`, and estimated extruded
length in meters. Length uses trapezoidal integration of that velocity at Mega
telemetry times, independent of GUI redraws. Missing telemetry intervals over
two seconds and disconnections are not integrated. Length survives USB reconnects
within the same GUI session, and starts at zero when the GUI starts.

**Extrusion Reset** is available with a stopped spooler. It moves the traverser
to its configured minimum, resets layer/round-trip counts on the Mega, and resets
integrated travel only after acknowledgement; total extruded length returns to 635 mm. Enter the current wound diameter as the
initial diameter when resetting a partly filled spool. Geometry edits require
stopping both Automatic controls. The estimates assume no slipping or missed
steps; they measure modeled wound length, not an independently sensed extrusion
length, and reverse rotation is counted as positive traveled length.

These additions use the existing Mega WIND/DMRPM protocol. They do not upload
firmware or start hardware when the GUI opens.

## Firmware and communication

Manual commands remain supported: PING, STATUS, STOP, ARM, CLRPM, DMRPM, SPARK,
FAN, SERVO, SWEEP, SWEEPSPEED and SERVOSTOP. New level-winding commands are:

- `WINDCFG core_diameter width filament_diameter min_angle max_angle motor_ratio`
  validates and applies geometry without starting motion.
- `WINDTARE` establishes layer zero at the minimum angle.
- `WINDSTART` starts/resumes the RPM-synchronised guide.
- `WINDPAUSE` holds its current position and reference.

STATUS adds `WIND_V=1`, `LAYERS`, `RADIUS`, `WIND_READY`, `WIND_ACTIVE`, while
THETA/OMEGA report commanded angle/speed and N reports round trips. Python checks
capability before enabling this mode. Older installed sketches retain Manual and
puller control but require the new sketch for automatic level winding. The legacy
TARE/AUTOSWEEP/AUTOPAUSE protocol is retained for compatibility; the GUI uses the
new linear level-winding algorithm.
The interface checks acknowledgements and sends changed setpoints only. STATUS
polls every 0.5 seconds refresh the Mega's 2.5-second watchdog. Protocol errors,
timeouts, disconnect and shutdown stop requested outputs. If USB is lost the Mega
watchdog is the fallback. STOP ALL is a software command, not a physical E-stop.
No Nano camera firmware changes are needed. The updated Mega sketch has been
compiled; it is not automatically uploaded by Launch.exe. Hardware testing and
machine tuning remain necessary.

## Tests

```powershell
.\.venv-camera\Scripts\python.exe -m unittest discover -s OV7670Camera -p 'test_*.py'
```

## Application icon

`assets/Glossy Blue Ribbon Poop Emoji.png` is the current icon artwork.
The previous artwork is retained in `assets`.
`assets/app_icon.ico` provides 14 sizes from 16 to 256 pixels, including scaled taskbar sizes.
Launch.exe embeds it, and both interfaces load it for their window/taskbar icons.
Keep `assets` beside the Python files. Restart an interface to apply its icon;
running Launch.exe reuses open windows. Launcher rebuild instructions include
the icon flag in `OV7670Camera/launcher/README.md`.

## System Diagnostics and recording statistics

The three-column System Diagnostics panel shows:

1. Read-only motor commands confirmed by the Mega: puller/spooler RPM, extrusion
   effort and fan percentage, plus traverser angular speed/angle. These are not
   encoder measurements; disconnected or stale Mega data displays N/A.
2. Live mean diameter, recording-session minimum/maximum, camera status, and
   Start/Stop Recording. Min/Max remain N/A until recording collects a valid frame.
3. Recording statistics with a 2-1000 point window (initially 20). Type a count
   and press Enter or Set, or use the synchronized slider. The window setting
   affects diagnostics only; the puller controller retains its 20-point average.

Start Recording resets session statistics and Min/Max. Stop Recording freezes
sample collection while the live diameter and motor readouts continue updating.
Changing the slider recomputes statistics from retained samples, including after
Stop. Each valid camera frame is counted once, not once per GUI redraw. Invalid
or stale camera readings do not enter the statistics. At least two valid readings
are required for deviation and confidence intervals; partial windows show their
current sample count. Session Min/Max cover the whole recording, not just the
moving window. A new recording starts a new session; reconnecting the camera
alone does not reset the session.

- Moving mean error: average of the last N signed errors (diameter minus the
  applied target at each sample). Deadband is not subtracted.
- Rolling std dev: sample standard deviation s of the last N diameters (ddof=1).
- Moving mean std dev: average of the last N rolling standard-deviation values,
  each calculated with the same N-point window (or available startup points).
- 95% spread (approx.): +/-1.959964*s around the window mean, an estimate of
  individual-diameter spread under a normal model. This is not the configured
  motor-control tolerance or a formal statistical tolerance interval.
- 95% mean CI: window_mean +/- t(0.975, n-1)*s/sqrt(n), where n is the number of
  available points in the window. This uses the unsmoothed sample deviation,
  not the moving average of deviations. It estimates uncertainty of the mean,
  rather than filament variation. The Student-t formula assumes independent,
  approximately normal samples; correlated camera readings can understate
  uncertainty. Reference: https://www.itl.nist.gov/div898/handbook/eda/section3/eda352.htm

New CSV files retain the original timestamp, diameter, validity and status
columns, and append session/window counts, Min/Max, window mean, mean error,
standard deviations, approximate spread, and mean-CI half-width/lower/upper limits.
All diameter statistics are in millimeters. Invalid rows have blank statistics.
The history is bounded to 1999 diameter/error pairs; session Min/Max and total
count cover all accepted readings. No additional Python dependencies are needed.

START buttons are green and STOP buttons red (including recording and Automatic
actions); disabled actions are grey. The recycler scroll region follows the
current expanded content, clamps after section collapse/resize, and stays at the
top when the content fits without scrolling.

Recording progress shows elapsed HH:MM:SS and the number of valid diameter
points actually written to CSV. Invalid/status rows are excluded from the point
count. The timer stops when recording stops; the saved count continues updating
while queued rows finish writing. Both reset on a new recording. STOP ALL uses
bold black text in every button state.

## Motor commands and velocity in CSV recordings

New recordings include UTC `timestamp_utc` and `elapsed_s` since Start Recording,
plus the motor commands confirmed by fresh Mega status:

- `puller_command_rpm`, `spooler_command_rpm` (signed for reverse).
- `extruder_command_percent` (signed), `cooling_fans_command_percent`,
  `cooling_fans_command_pwm` (0-255).
- `traverser_command_speed_deg_s`, `traverser_command_angle_deg`, `traverser_enabled`.
- `filament_linear_velocity_mm_s`: spool surface-speed estimate, using current
  modeled coil radius and spooler RPM; the same value as Spooling Diagnostics.
- `puller_linear_velocity_mm_s`: separate wheel/RPM-based puller speed estimate.
- `mega_status_valid`, `mega_status_age_s`: availability and age of the source status.

Commands are not encoder measurements. Disconnected/faulted status or status over
2 seconds old produces blank command and velocity fields, not reused values.
An off traverser records speed 0 and a blank angle because it is no longer being
position-commanded. Velocity is an estimate assuming no slip or missed steps.

A row captures the latest camera and Mega snapshot when either source updates.
Motor logging therefore continues on new Mega status even without a valid camera
reading. Repeated GUI polls add no rows. `new_diameter_sample=1` identifies a new
valid diameter point; motor-only rows may repeat a still-fresh diameter but do not
increase the saved-point count or statistical sample count. Filter on this flag
when analyzing distinct diameter samples. UTC/elapsed timestamps refer to row
capture, not exposure time; Mega snapshots arrive asynchronously (normally 0.5 s).
Previous CSV files are not modified. No firmware upload is needed for this addition.

## Save a recording with a chosen filename

Click Stop Recording to finish writing queued CSV rows, then choose a filename
and location in the Save As dialog. The extension defaults to .csv, and the dialog
asks before overwriting an existing file. The last selected folder is remembered
for the current GUI session. The elapsed timer stops when Stop is pressed.

Saving copies the completed CSV in a background thread and replaces the selected
destination only after the full copy succeeds. The original in recordings/ is
retained as a backup, including when Save As is cancelled or fails. The status
shows the saved destination or the original path and any error. Start Recording
is unavailable while finishing, choosing a path, or exporting. Closing the GUI
retains an unfinished prompt's local recording and waits for any active export.
No file dialog appears on automatic shutdown or when no recording was active.

Hover over any button for a brief action description, including mode selectors,
status lights, and section toggles. Tooltips also work on disabled controls and
do not activate them. Pause/Resume help follows its current action.

## Operating Configuration

The Operating Configuration dropdown is directly below System Diagnostics and
above Diameter Feedback Control/Main Controls. The automatic puller base-speed
default is **60 RPM**; the puller still starts Disabled with zero output.

- **Save Current** asks for a name and an operating temperature in Celsius.
  Saving an existing name asks before replacing it.
- Select a saved name from the list and press **Load** to restore it. The popup
  says "For this operating configuration, set temperature to: ... C". Temperature
  is an operator reminder; this software does not command a heater.
- **Duplicate** copies all settings and temperature under a new name. **Rename**
  changes the selected name. **Delete** asks before removing that saved entry.

Saved data includes puller/spooler/traverser modes; all manual motor setpoints,
entry-box drafts and reverse selections; automatic tuning/limits/wheel diameter;
sweep input values and last Set values; spool geometry and its applied values;
diagnostic window and its entry; and selected Mega port. A snapshot of available
confirmed motor commands is retained for reference. Running/armed states,
recordings, elapsed length, layer reference and live measurements are not restored.

Loading validates the entire setup first, stops/disarms outputs, restores GUI
values and applied settings, and clears the puller's old diameter history. It
never starts a motor, sweep or recording. Stop recording and finish the Save As
operation before loading another setup. A different saved Mega port disconnects
the old port; reconnect explicitly. Use Extrusion Reset again before Automatic
spooling. An entry that had not been applied with Set remains an unapplied draft.

Camera settings are included from the open viewer's live setup, or its last saved
settings when it is closed: all six regions, grayscale-processing inputs, light
filament mode, calibration/geometry, True Diameter, Direct View, LED brightness,
port and baud rate. If no viewer setup is available, a profile can still save the
recycler settings; its status says that no camera setup was included. An open
viewer applies loaded settings through an atomic local request and acknowledges
completion in the configuration status. A closed viewer uses the saved camera
setup on its next launch. Changed camera connection parameters disconnect the
camera; reconnect explicitly. Loading optical/processing settings does not itself
connect the camera. New connections use the configured LED brightness (initial
default remains 255).

Profiles persist locally in `operating_configurations.json`. Camera request/result
files are in OV7670Camera; these files and local profiles are ignored by Git.
Existing files are replaced atomically, and duplicate names are case-insensitive.
Restart both interfaces after updating the code to enable configuration sharing.

Extruded Length is shown in System Diagnostics in millimeters and in Spooling
Diagnostics in meters. It equals **635 mm (25 inches) + integrated filament
linear velocity over time**. The fixed 635 mm accounts for filament already
between nozzle and spool. A successful Extrusion Reset zeros integrated travel
and layers, returning total length to 635 mm. Initial GUI length is also 635 mm.
The CSV includes the same total as `extruded_length_mm`; stale/disconnected motor
telemetry leaves that column blank. No Mega firmware update is required.
