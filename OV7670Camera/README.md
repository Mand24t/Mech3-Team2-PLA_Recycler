# OV7670 USB viewer - first stage

Standalone files for an Arduino Nano ESP32 and an OV7670 without an external FIFO.
The existing recycler Python and Mega sketch are not imported or modified.

## Run

From the project directory in PowerShell:

    .\.venv-camera\Scripts\python.exe OV7670Camera\ov7670_viewer.py

The isolated environment was created and pyserial installed during setup.
For a fresh environment, use Python 3.11 or newer with Tkinter:

    py -3.13 -m venv .venv-camera
    .\.venv-camera\Scripts\python.exe -m pip install -r OV7670Camera\requirements-camera.txt

Click **Detect connected camera** after connecting the Nano with a USB data cable.
A single recognized Nano/ESP32 USB candidate is opened and queried. Otherwise select
the appropriate USB port and click **Connect selected port**. Bluetooth ports are
excluded. The viewer does not flash firmware or enter the bootloader.

A detected USB port is NOT proof that the camera works. A successful camera
connection requires the matching identity response, OV7670 PID 0x76, grayscale
format, and valid frame packets. Firmware version and sensor VER are displayed.
Existing unknown Nano firmware may use another protocol; in that case detection
reports that no compatible firmware replied. An OV7670 itself has configuration
registers, not a USB interface or a PC-streaming program.

The collapsible connection panel contains the USB port and baud rate for the
Nano ESP32 + OV7670. The raw grayscale frame and a synchronized processed view are displayed side by
side. Six sliders give Left, Middle, and Right independent start/end x
coordinates in source pixels. All six guides are blue and 3 display pixels thick.
Each pair must satisfy 0 <= start < end <= image width. Moving one endpoint
clamps it only against its partner; it never moves or constrains another view.
Windows may have gaps or overlap. Pixels outside all windows remain visible but
are excluded from measurements. Regions use [start,end), so the end may equal
image width. Defaults are Left 20-106, Middle 106-213, Right 213-320, preserving
the previous search coverage; coincident guides are drawn in the same position
until separated. Each pair scales independently when resolution changes.
Moving a slider immediately reprocesses the latest frame.
Horizontal-gradient boundary detection and per-row pixel widths are now
implemented as described below. Physical geometry and scalar calibration now
provide diameter in millimetres. Further optical calibration
and recycler communication are not implemented yet.

## Nano firmware

Open NanoOV7670USB/NanoOV7670USB.ino in Arduino IDE.
Select **Arduino Nano ESP32** from **Arduino ESP32 Boards**. The installed version
used for compilation was arduino:esp32 2.0.18-arduino.5. Normal USB mode uses native
USB CDC; keep a USB serial connection enabled. The sketch's serial baud is 921600.
The GUI asserts both DTR and RTS for this Nano TinyUSB CDC driver, without a 1200-baud bootloader touch.
Close the viewer connection and any Serial Monitor before uploading a sketch.
The user-authorized sketch was uploaded successfully over DFU and the physical OV7670 identified itself with PID 0x76, VER 0x73, MIDH 0x7F, MIDL 0xA2. Three 320x240 GRAY8 frames passed CRC checks. Initial frames were very dark (0-15 pixel range); useful optical image quality has not yet been established.

Supplied wiring, translated to raw GPIOs for esp_camera:

| OV7670 | Nano header | ESP32 GPIO |
|---|---|---|
| VCC | 3V3 | - |
| GND | GND | - |
| SIOD / SDA | A4 | 11 |
| SIOC / SCL | A5 | 12 |
| VSYNC | D2 | 5 |
| HREF | D3 | 6 |
| PCLK | D4 | 7 |
| XCLK | D5 | 8 |
| D0 | D6 | 9 |
| D1 | D7 | 10 |
| D2 | D8 | 17 |
| D3 | D9 | 18 |
| D4 | D10 | 21 |
| D5 | D11 | 38 |
| D6 | D12 | 47 |
| D7 | D13 | 48 |
| RESET | A0 | 1 |
| PWDN | A7 | 14 |

RESET is now wired to A0 (raw GPIO1), driven by the camera driver's reset
sequence. Firmware 1.4 drives PWDN on A7 (raw GPIO14) LOW at startup and
passes the pin to the camera driver, which leaves it LOW during normal capture.
The illumination LED connects to A6 (raw GPIO13) through a current-limiting
series resistor, returning to GND.

Capture is 320 x 240, 8-bit grayscale, one PSRAM frame buffer, nominal XCLK 10 MHz.
No external FIFO, Wi-Fi, RGB transfer, JPEG, or threshold-to-one-bit conversion
is used. The ESP camera driver selects the OV7670 YUV mode and retains luminance
for grayscale frames; the color sensor does not become a physically monochrome
sensor. Reducing transferred/stored bytes does not guarantee a particular frame
rate, especially with the full parallel pixel bus still running.

Each request discards a queued frame before obtaining a fresh frame. The PC keeps
only the latest complete verified frame. Frame age means time since PC receipt,
not measured exposure-to-display latency. A stopped or old image is labelled stale.

Reference pin map:
https://github.com/espressif/arduino-esp32/blob/master/variants/arduino_nano_nora/pins_arduino.h

Reference driver:
https://github.com/espressif/esp32-camera
https://github.com/espressif/esp32-camera/blob/master/sensors/ov7670.c

## USB protocol version 1

Host sends ASCII newline-terminated requests:

- OV7670_HELLO: replies with OV7670_INFO followed by a JSON object and newline.
  Fields include protocol, board, firmware, chip/revision, sensor PID/VER,
  camera_ready, pixel_format, width, height, and error.
- OV7670_FRAME: replies with one binary frame, or OV7670_ERROR plus text/newline.

Frame header is 20 bytes, little endian: magic OVF1 (4 bytes), sequence (uint32),
width (uint16), height (uint16), byte count (uint32), pixel CRC32 (uint32).
Payload is width*height grayscale bytes in row-major order, with no separator.
CRC is standard IEEE CRC32, matching Python zlib.crc32(payload).
The host bounds dimensions and payload size and rejects incomplete/corrupt frames.
Frame numbers count transmissions; no cross-device clock synchronization is assumed.

The host accepts 160x120, 320x240, and 640x480 packets, but this sketch is configured
only for 320x240. Changing its resolution also requires matching firmware constants.
There is no auto-conversion of unknown firmware streams.

## Validation

    .\.venv-camera\Scripts\python.exe -B -m unittest discover -s OV7670Camera -p test_ov7670_viewer.py -v
    .\.venv-camera\Scripts\python.exe -B OV7670Camera\ov7670_viewer.py --list-ports

Tests emulate fragmented USB reads, identity responses, corrupt frames, cancellation,
and port closure. A hidden Tkinter window verifies grayscale rendering and controls.
They are software checks, not evidence of working physical capture.

Firmware compile (the executable may need its full Arduino IDE installation path):

    arduino-cli compile --fqbn arduino:esp32:nano_nora --build-path OV7670Camera/.build-nano OV7670Camera/NanoOV7670USB

Physical sensor identification, frame quality, and throughput require the connected
board. USB descriptors identify the Nano interface; PID/VER registers identify the
sensor and revision. They do not prove a camera module supplier or PCB revision.

## Diagnosing the missing upper data bits

Active firmware is now nano-ov7670-usb-1.1. It adds backward-compatible diagnostic
commands; normal capture and the USB packet format remain the same:

- OV7670_DIAG: passive GPIO sampling, GPIO-matrix readback, and sensor registers.
- OV7670_TEST_ON / OV7670_TEST_OFF: enable/disable the sensor's internal color bars.

With the viewer disconnected, run:

    .\.venv-camera\Scripts\python.exe -B OV7670Camera\camera_diagnostics.py --port COM6

The script restores normal camera capture in a finally block and saves raw PNGs
and camera_diagnostics.json under validation. Physical checks found that camera
D4-D7 had no detected high levels during both ordinary capture and the internal
color-bar pattern. Lower data bits and synchronization signals were active.
This localizes the issue to the upper data signal path; the exact physical cause
requires wiring/continuity checks. Correct CRCs prove USB packet integrity, not
that the physical camera data arrived correctly at the Nano.

See validation/DIAGNOSTIC_FINDINGS.md for the checked connections and results.


## Current hardware status

Firmware nano-ov7670-usb-1.2 is uploaded. All eight data bits now have activity
and the internal pattern spans 0-255. A separate frame-write reconnect bug was
fixed by removing the stale logical Serial boolean gate; actual USB writes and
the transfer timeout remain in use. Fifteen normal frames passed CRC across
three separate serial connections. Normal brightness rose after exposure
settled. Current evidence is in validation/reconnect_validation.json,
validation/camera_diagnostics_all_eight_bits.json and validation/normal_settled.png.
Earlier missing-bit findings are retained as history in DIAGNOSTIC_FINDINGS.md.


## Gradient control and pixel-width diagnostics

The Control Panel contains the six region sliders and a numerical
**Gradient Threshold (0-255)** entry. The default threshold is 20. Finite numbers
in the inclusive range 0-255 are accepted; invalid/empty input keeps the last
valid threshold applied and displays an explicit message. Editing the threshold
or moving a window boundary reprocesses the latest frame immediately.

**Gradient Window Size** defaults to 10 and accepts positive whole numbers.
Before computing the horizontal gradient, each pixel is averaged with vertical
neighbors at the same x coordinate. Size 10 includes the current row, four rows
above, and five below. The window slides one row at a time, retaining a width
measurement for every y. At the top and bottom it is clipped to existing rows
and divided by the actual number of samples, without padding. Fractional
averages are retained. Size 1 disables averaging; invalid input keeps the last
valid setting. Larger windows reduce row-specific noise but can smooth genuine
changes along y, and adjacent measurements share samples. This does not change
the raw preview or average across frames or horizontal regions.

The three full-height windows are [x1, x2), [x3, x4), and [x5, x6), named
Left, Middle, Right. Pixels outside these independent pairs are ignored. Each window must contain one
filament with both edges visible; it may be dark, bright, or show internal rims.

The processor now uses paper-inspired per-slice segmentation, adapted from
section 2.2 of https://arxiv.org/pdf/2012.00191. Each image row is searched
for signed horizontal-gradient groups. The strongest entrance transition is
paired with the following opposite-sign exit that recovers half the contrast.
The strongest gradient in each group sets the boundary. A later background ramp
cannot replace an exit merely because it is farther right. Comparable second
rims require an additional entrance/exit pair. Polarity is inferred to support
dark-on-light and bright-on-dark profiles; this setup normally uses dark filaments.

A half-contrast body check rejects pairs with more than a 2:1 imbalance between
the two edge-to-body-center distances. This heuristic helps reject a distant
background ramp when the true blurred edge is below threshold. Missing or
ambiguous edges produce gaps and no width. Lowering the threshold may recover a
soft edge, but may also admit noise. Each window should isolate one filament.

Width is right boundary x minus left boundary x, matching the filled [left,right)
interval. The centerline is its midpoint. These peak positions can differ from
the previous outermost above-threshold coordinates. The pairing, contrast, and
asymmetry rules are project choices; see PAPER_METHOD.md for assumptions and
limits. Vertical averaging precedes gradient calculation; there is no temporal
averaging, image rectification, or perpendicular-width correction. Physical
conversion and scalar calibration are described below.

The processed RGB image preserves grayscale values except for one-source-pixel
red markers at both boundaries and the computed center of each valid row segment.
A fully detected row has nine red pixels. Invalid rows have no fabricated width,
centerline, or markers. Red pixels enlarge with zoom. All six blue guides remain
three display pixels thick and are overlaid on the processed preview.

The Diagnostics Panel below both views displays average pixel widths for Left,
Middle, and Right, using only valid rows of the latest frame. N/A means no
valid rows; counts and center coordinates are shown below the boxes. The
**Instantaneous Mean Diameter [mm]** box is read-only and is calculated when geometry
and all three view measurements are valid. It stays blank when these are missing.

The immutable result is available as CameraViewer.processing_result, with the
associated CameraViewer.processing_frame_sequence. Important fields:

- widths_px[y][region]: pixel width, or None; regions 0/1/2 are Left/Middle/Right.
- left_boundary_x[y][region], right_boundary_x[y][region]: detected coordinates.
- centerline_x[y][region]: centerline derived from that row's mask interval, or None.
- center_x[region]: mean centerline over valid rows, or None.
- gradient_window_size: requested vertical averaging size.
- gradient_window_rows[y]: actual sample count after clipping at the image edges.
- gradient_mask: full-frame 0/255 mask of above-threshold gradients.
- filament_mask: full-frame 0/255 mask of complete segmented intervals.
- mean_widths_px[region], valid_rows[region]: per-frame diagnostics.
- processed_rgb: the RGB overlay without the GUI's blue guide strokes.

Storage is in memory for the latest frame; automatic CSV/history logging is not
enabled. The camera-independent implementation is filament_processing.py.
Known-width image tests cover widths, outer edges, internal highlights/rims,
bright profiles, moving centerlines, exact red-pixel counts, threshold limits,
region isolation, and invalid-row exclusion.

    .\.venv-camera\Scripts\python.exe -B -m unittest discover -s OV7670Camera -p 'test_*.py' -v


## USB shutdown and restored controls

The viewer drains a pending frame before closing its USB connection, with a
2-second total deadline and 0.5-second idle deadline. This prevents an ordinary
mid-frame cancellation from immediately abandoning the Nano's outgoing frame.
Three physical stop-during-capture/reconnect cycles passed with this change.

Startup options can restore controls without changing their normal defaults:

    .\.venv-camera\Scripts\python.exe OV7670Camera\ov7670_viewer.py --connect --left-start 20 --left-end 100 --middle-start 110 --middle-end 205 --right-start 220 --right-end 320 --threshold 20 --gradient-window-size 10

See validation/signal_check_20261027_025332/FINDINGS.md for the subsequent noise
check: all eight bits active, twenty identical internal test-pattern frames,
and a 100-frame normal run with no CRC errors. Earlier tearing was not reproduced
after the USB reset; intermittent electrical/optical causes remain possible.


The former four-coordinate measurement API remains available for compatibility:

    measure_filaments(pixels, width, height, x1, x2, x3, x4, gradient_threshold=20, gradient_window_size=10)

Changing an outer guide trims only the search domain; it does not crop the
preview or modify incoming camera pixels. Black-border exclusion and clipped
edges are covered by the measurement tests.


## Physical geometry and single-press calibration

The Control Panel now contains Lens Focal Length (initial 3.37 mm), Sensor Active
Width (initial 2.304 mm), Lens Reference Offset (initial estimated 3.4 mm),
Sensor-to-Filament Distance (blank), Sensor-to-Mirror Meeting Point (blank),
Correction Coefficient (initial 1.0), and True Diameter (blank).

The focal-length default is provisional, from the Arducam B0361 specification:
https://www.arducam.com/blog/product/arducam_ov7670_camera_module_with_adapter_board_b0361/
That module is documented as EFL 3.37 mm and 640x480 active pixels at 3.6 um pitch.
Your actual lens has not been identified. Active width is calculated as
640 * 0.0036 = 2.304 mm; it is the active sensor extent represented by the WHOLE
frame, not the width of a GUI search region. At full-field 320-pixel output,
effective sensor spacing is 0.0072 mm/output pixel. The upstream OV7670 driver
uses the same capture window for VGA and QVGA, with downsampling enabled for QVGA:
https://github.com/espressif/esp32-camera/blob/master/sensors/ov7670.c
This supports the nominal full-field default but is not a physical calibration
or a readback verification of this device's entire capture window. If firmware
cropping changes, update the represented sensor width. GUI zoom has no effect.

Both mirrors are fixed internally at 45 degrees in a symmetric V arrangement.
All installation distances are axial, measured from the sensor plane in mm.
Let B be the sensor-to-lens optical reference offset, L the sensor-to-filament
distance, and A the sensor-to-mirror meeting point distance. Require 0 <= B < L < A.
The filament and mirror meeting point are assumed centered on the optical axis.
In cross-section the mirror planes are z=A+x and z=A-x. Reflection of (0,L)
gives virtual centers (+/-(A-L), A), so both reflected views have axial depth A.
Do not use the slanted ray length as the pinhole depth.

With focal length F, represented sensor width S, and output width N:

    direct scale [mm/pixel] = (S / N) * (L - B) / F
    mirror scale [mm/pixel] = (S / N) * (A - B) / F

The Direct view selector defaults to Middle; Left and Right then use the mirror
scale. Change it if the camera orientation puts the direct image elsewhere.
The base diameter is the equal-weight mean of the three independently converted
mean pixel widths; it is not weighted by their valid-row counts. The displayed
mean is correction * base diameter. This is a pinhole approximation, without
lens distortion or local filament-tilt correction. The lens reference offset
cannot be reliably inferred from the sensor model. Its 3.4 mm initial value is
a rough estimate based on the provisional focal length, not a measured specification.

Generate Correction Coefficient is a normal momentary push button. Each click
samples the current uncorrected mean and sets coefficient = True Diameter / base
mean, replacing the existing coefficient. It never compounds the current
coefficient or recalibrates on future frames. The sampled display equals the
reference to its displayed precision; subsequent frames continue measuring.
Calibration requires valid geometry, a positive finite coefficient and reference,
all three measured means, and a frame received within two seconds. Invalid
geometry or missing measurements clears the diameter, while stale measurements
are labelled and cannot be used for calibration. Values are in memory for this
session; automatic settings persistence is not implemented.


### Concise control labels

All Control Panel setup inputs are marked (constant). Only Filament Distance
and Correction Coefficient are marked (variable). Constants remain editable
for setup; the labels do not lock the entries or sliders. Mirror Apex Distance
means the sensor-to-mirror-meeting-point distance. Lens Reference Offset means
the sensor-to-effective-lens-reference distance. The True Diameter reference is
constant for each calibration sample. Set Correction is the existing single-press
coefficient generator, with no change to its behavior.

Startup values can also be restored using --focal-length, --sensor-width,
--lens-offset, --filament-distance, --mirror-distance, --correction, and
--true-diameter. These options populate the same validated GUI fields.


### Collapsible sections

Click the Camera Connection header to hide/show camera selection, USB port,
and baud rate. Click Control Panel to hide/show all processing and geometry
controls. Both sections start expanded; hiding preserves every value and keeps
capture and processing running. Connection buttons, connection status, frame
views, and diagnostics stay visible. The Filament Distance and Correction
Coefficient labels and entered values are red to identify the variable controls.


## A6 illumination slider (firmware 1.5)

The Control Panel contains LED Brightness - A6 [0-255] (variable), in red.
The default is 255 (full brightness, continuous HIGH at approximately 3.3 V).
Zero is continuous LOW/off. Intermediate
values control PWM duty at 20 kHz with 8-bit resolution. This adjusts average LED
brightness, not a DAC or a variable HIGH voltage. A series current-limiting
resistor is installed for this LED. High-current illumination needs a suitable
external driver rather than being powered directly by a GPIO.

NanoOV7670USB firmware 1.3 introduced RESET on A0/GPIO1 and illumination on
A6/GPIO13. Camera XCLK retains LEDC timer 0/channel 0; illumination uses timer
1/channel 2. Raw GPIO APIs avoid Nano Arduino pin-number remapping ambiguity.
Firmware 1.5 latches A6 HIGH before enabling its output. The board and viewer
start each connection at 255. The viewer attempts to turn the LED off on orderly
disconnect. At 0/255 the firmware uses explicit LEDC idle LOW/HIGH rather than
a PWM counter endpoint; intermediate levels resume PWM on the same channel. An abrupt program
termination may leave the previous output active until the board resets.

The identity reply advertises led_control:true when PWM initialization succeeds.
Older camera firmware still streams normally, but the slider is disabled with an
upgrade message. The viewer sends OV7670_LED <integer 0..255> and waits for
OV7670_LED {"value":<integer>} before another frame request. Only the acquisition
worker accesses the USB stream, and rapid slider changes coalesce to the newest
value between frames. Applied brightness is shown only after acknowledgement.
The frame packet format is unchanged. Firmware must be uploaded to use this
slider; editing the PC files alone does not update the Nano.


### Illumination troubleshooting

A6 is raw GPIO13, not raw GPIO6. Firmware 1.5 enables input readback on that
output and adds OV7670_LED_STATUS, which replies with OV7670_DIAG JSON containing
led_gpio, led_value, led_ready, high_samples, samples, sample_us, and pwm_hz.
A static HIGH should read all samples HIGH, and static LOW all samples LOW;
intermediate PWM should produce both levels. This reads the GPIO logic threshold,
not voltage, current, or emitted light. Measure A6 to GND with a multimeter to
check the voltage under load. Then check the series path A6 -> 220 ohm resistor
-> LED anode (+) -> LED cathode (-) -> GND. Resistor position in the same series
path may be before or after the LED. An LED's flat edge usually identifies its
cathode. Do not infer an LED is lit solely from a successful USB acknowledgement.


## Access denied when connecting

The Nano's COM port can be used by only one serial client at a time. The rebuilt
Launch Camera.exe restores an existing camera viewer rather than launching a
second copy. Leave the connected viewer running and use that window. Close
Arduino Serial Monitor or another separately launched Python viewer before
connecting. Direct Python launches do not use the EXE's duplicate-launch check.


## Light filament mode

The Control Panel button labelled light filament mode is an on/off toggle,
initially off. When on, each grayscale pixel in a working copy becomes 255-I
before vertical averaging, gradient calculations, polarity selection, or edge
searching. The processed preview uses this inverted image, then adds the red
markers and blue guides in their normal colors. The raw preview always displays
the original camera pixels. Clicking again restores normal processing, and each
click reprocesses the current frame immediately without waiting for a new one.
The selection remains in effect for subsequent frames during the GUI session.

FilamentMeasurement.light_filament_mode records the processing polarity.
measure_filaments(..., light_filament_mode=True) uses the same preprocessing
path outside the GUI. Inversion does not increase contrast or gradient magnitude;
the existing polarity-aware detector can therefore return the same numerical
widths for a frame in either mode. This option changes the actual working pixels
and processed display, without changing thresholds or edge-pairing rules.
No camera firmware upload is required.


## Independent filament windows

The primary measurement API is now:

    measure_filament_windows(pixels, width, height,
                             ((left_start, left_end), (middle_start, middle_end),
                              (right_start, right_end)),
                             gradient_threshold=20, gradient_window_size=10,
                             light_filament_mode=False)

Three pairs are required, but pairs need not touch or be globally ordered.
Left/Middle/Right identify the assigned measurement slots; overlapping windows
measure their contents independently. Keep one filament and both its edges in
each window. All six coordinates participate in the frame-processing cache, so
changing either endpoint updates widths and derived diameter immediately.

Use --left-start/--left-end, --middle-start/--middle-end, and
--right-start/--right-end to restore six coordinates at startup. The old
--first-x/--second-x/--third-x/--fourth-x options still map shared boundaries
to the three formerly adjacent windows; explicit start/end options override
individual coordinates. --light-filament-mode enables inversion at startup.
