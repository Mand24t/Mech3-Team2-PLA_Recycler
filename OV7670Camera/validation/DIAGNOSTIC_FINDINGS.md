# OV7670 diagnostic findings - 2026-10-26

Board: Arduino Nano ESP32 / ESP32-S3, COM6.
Running firmware: nano-ov7670-usb-1.1, compiled with arduino:esp32 2.0.18-arduino.5.
Sensor: OV7670, PID 0x76, VER 0x73, manufacturer ID 0x7FA2.

## Checks that passed

- The firmware's raw GPIO map matches the user's wiring and Arduino's Nano map.
- Read-back GPIO-matrix routing is [9, 10, 17, 18, 21, 38, 47, 48], matching D0-D7.
- Sensor configuration is YUV (COM7=0x00), as used by the driver's grayscale path.
- Full output range is selected (COM15=0xC0).
- Auto gain and exposure are enabled (COM8=0xC7).
- PCLK, HREF and VSYNC all show both logic levels during passive sampling.
- The sensor accepted its internal test pattern: SCALING_YSC changed from 0x35
  to 0xB5, then was restored to 0x35.
- All captured normal/test-pattern frames were 320x240 GRAY8, 76800 bytes,
  with valid CRC32 checksums.

## Failure localized to the upper pixel-data signal path

During 40000 passive samples in ordinary capture and another 40000 samples with
sensor-generated color bars, the Nano detected no logic highs on camera D4-D7.
Both color-bar frames also had zero in bits 4-7 of every received pixel.
Lower data bits had activity; the test-pattern images were reproducible.
The built-in pattern bypasses scene lighting and the lens.

| Camera signal | Nano header | Raw GPIO | Normal high samples | Pattern high samples |
|---|---|---:|---:|---:|
| D4 | D10 | 21 | 0 | 0 |
| D5 | D11 | 38 | 0 | 0 |
| D6 | D12 | 47 | 0 | 0 |
| D7 | D13 | 48 | 0 | 0 |

This explains the 0-15 grayscale range. It is not caused by GUI image scaling or
USB packet corruption. The likely causes include missing/incorrect data wires,
poor connector/breadboard contacts, or a camera output problem. These software
checks cannot distinguish the exact physical location of a bad connection.

Unplug USB before checking those four connections against the table. Confirm
continuity from the actual camera header signal to the Nano header, rather than
only checking the jumper's appearance. If continuity is correct and the symptom
persists, compare the signals at both ends using a logic probe/oscilloscope.
No pin reassignment or gain multiplier was applied to hide the missing bits.

The internal test pattern was switched OFF successfully after the checks.
The existing RecyclerControl_Latest.py and RecyclerMega_Latest.ino are unchanged.
Raw results: camera_diagnostics.json. Raw images: test_pattern_0.png,
test_pattern_1.png, normal_before_test.png, normal_after_test.png.

A USB/DFU stall during an upload cleared after unplugging/reconnecting the Nano;
the diagnostic upload then succeeded. The viewer uses both DTR and RTS, as
required by this installed TinyUSB CDC implementation.

References:
- https://support.arduino.cc/hc/en-us/articles/10483225565980-Select-pin-numbering-for-Nano-ESP32-in-Arduino-IDE
- https://github.com/espressif/esp32-camera/blob/master/sensors/ov7670.c
- https://github.com/espressif/esp32-camera/blob/master/target/esp32s3/ll_cam.c


## Follow-up after the user's wiring check

A repeat with unchanged firmware 1.1 and the same GPIO-matrix map showed:

- Upper bits D4-D7 now have activity during the internal test pattern.
- Lower bits D0-D3 now have zero high samples, in both ordinary and pattern capture.
- Pattern frames pass CRC and span 0-240, but all pixels are multiples of 16.
- The test pattern was disabled successfully before returning to normal capture.

All eight pixel-data wires must be connected simultaneously. The next physical
check is camera D0 -> Nano D6 (GPIO9), D1 -> D7 (GPIO10), D2 -> D8 (GPIO17),
and D3 -> D9 (GPIO18), while retaining the upper four connections.
This result still does not establish working eight-bit optical capture.

This run's raw data is saved as camera_diagnostics_after_rewire.json.


## Latest check: all eight data bits restored; reconnect fix validated

The subsequent wiring check restored activity on all eight camera data signals.
The sensor test pattern produced clean grayscale bars spanning 0-255; both
pattern frames passed CRC32. Read-back GPIO routing still matches the specified
wiring. The test pattern was disabled after the checks.
Raw data for this successful bus check: camera_diagnostics_all_eight_bits.json.

A separate reconnect fault was then reproduced: identity replies could work
while the Arduino core's logical Serial boolean prevented frame output. Firmware
1.2 removes that boolean gate from writeAll and retains actual USB writes and
the existing transfer deadline. Firmware 1.2 was compiled and uploaded.

Validation with firmware 1.2: three separate open/identify/capture/close cycles,
five frames each. All 15 frames passed CRC32. Requests took approximately
0.19-0.29 seconds per frame in this check. Normal images initially were dark,
then reached the full output range as auto exposure settled. This verifies
frame transport and visible scene capture; diameter accuracy is not calibrated.
See reconnect_validation.json and normal_settled.png.

These latest results supersede the missing-data-bit findings above, which are
retained as diagnostic history. Both original recycler files remain unchanged.
