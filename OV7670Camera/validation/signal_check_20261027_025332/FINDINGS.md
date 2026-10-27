# Camera noise / connection check

The reported horizontal noise was visible in the RAW GUI preview, before edge
processing. The frame counter was advancing at approximately 3.6 FPS.
The GUI's control values were recorded as x1=160, x2=259, threshold=20.

The Nano stopped answering USB commands after the old viewer was closed during
an in-flight transfer. A physical USB reconnect restored communication.

Checks after that reset:
- OV7670 PID 0x76, VER 0x73; firmware nano-ov7670-usb-1.2.
- All eight data inputs had high and low samples during the internal pattern.
- Read-back GPIO routing matched [9,10,17,18,21,38,47,48].
- PCLK, HREF and VSYNC all showed activity; this is not a scope measurement of
  frequency, voltage, signal integrity or timing margins.
- Twenty internal-pattern frames were byte-for-byte identical. Every row of
  each frame was identical to its first row. The image was clean vertical bars.
- All test-pattern and normal-frame CRC checks passed.
- An additional 100-frame continuous normal-capture run had no CRC failures.
  Saved samples did not show the previous horizontal tearing.

This did not reproduce the noise after reset. It provides no evidence of a
currently stuck/disconnected data bit or an unstable test-pattern capture.
It does not exclude intermittent wiring, power, illumination or camera faults,
and it does not establish the root cause of the previously observed bands.

A separate host shutdown issue was fixed: the worker now drains a pending frame
with bounded total/idle waits before closing USB. Regression checks verify that
cancelled bytes are consumed before port closure. Three real stop-during-capture
and reconnect cycles passed without unplugging the Nano.

No camera clock, exposure, gain or image-processing settings were changed.
The internal test pattern was disabled and the normal feed restored.
The GUI was reopened with x1=160, x2=259 and threshold=20.
Original recycler Python and Mega sketch were left unchanged.

Evidence: signal_report.json, continuous_capture.json,
shutdown_reconnect_check.json, pattern_19.png, normal_after.png,
and continuous_0.png through the selected continuous_99.png samples.
