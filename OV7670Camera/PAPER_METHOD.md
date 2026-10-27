# Paper-inspired edge searching

Reference: Aliaksei L. Petsiuk and Joshua M. Pearce,
[Open Source 3-D Filament Diameter Sensor for Recycling, Winding and Additive Manufacturing Machines](https://arxiv.org/pdf/2012.00191),
section 2.2, Figures 6-8.

The paper describes grayscale-gradient segmentation of one-pixel slices in
preselected regions, followed by masks, midlines, and rectification. This update
adapts that sequence to the GUI's vertical filaments by using image rows as the
cross-sectional slices.

## Implemented choices

The paper does not provide a complete edge-pairing specification. These are
explicit choices in this implementation, rather than claimed exact author code:

1. Keep the three windows [x1,x2), [x2,x3), [x3,x4). Do not inspect outer pixels.
2. Average each pixel vertically using the GUI's Gradient Window Size (default
   10), then compute abs(A[x] - A[x-1]) inside each window. Windows overlap with
   stride one row: size 10 uses y-4 through y+5. At image edges, clip to existing
   rows and normalize by their actual count, with no padding or integer rounding.
   Size 1 disables averaging. This noise-reduction step is a project-specific
   addition; it preserves one output per image row and never averages across x.
3. Mark samples strictly above the 0-255 gradient threshold. Contiguous samples
   with the same sign form transition groups. Keep each group's strongest
   gradient; equal peaks choose the first x.
4. Infer dark-on-light versus bright-on-dark polarity from the window-end
   background estimate and intensity extrema. The current camera uses dark
   filaments. Select the strongest entrance group with the matching sign.
5. Select the following opposite-sign exit that recovers at least half the
   entrance-to-core intensity contrast. This avoids treating an internal texture
   transition as the whole exit. A later same-sign background ramp cannot replace
   an already selected exit. A second comparable dark/bright rim can extend the
   pair only when another entrance and a recovering exit are both present.
6. Require two distinct peaks with space for a center marker. Check that the
   midpoint of the half-contrast body inside this pair lies between the peaks,
   with neither edge more than twice as far from it as the other. Reject a
   strongly asymmetric pair rather than measuring a weak missed edge against a
   distant background ramp. This factor-of-two check is a heuristic, not a
   calibrated optical constraint.
7. Fill the mask [left,right). Width is right-left and center is (left+right)/2.
   Boundaries now represent gradient peaks, so widths can differ from the old
   outermost-threshold coordinates even for a correctly detected filament.
8. Draw the two peaks and rounded center as three distinct one-source-pixel red
   markers per valid view/row. Preserve the original grayscale elsewhere.
9. Exclude invalid rows from the average and show N/A where no valid rows exist.
   Invalid rows have no fabricated centerline, width, or red markers.

Each window must isolate one filament with both edges visible. Extraneous
high-contrast objects or noise can still cause incorrect selection;
adjust the windows and threshold using the raw preview. The method does not
require the filament to be darker than its background.

## Stored results and limits

- centerline_x[y][view]: row centerline, possibly a half-pixel coordinate, or None.
- center_x[view]: mean valid centerline x, used for the GUI summary.
- gradient_window_size: requested vertical averaging size.
- gradient_window_rows[y]: actual number of rows in the clipped averaging window.
- gradient_mask: full-frame 0/255 thresholded-gradient mask.
- filament_mask: full-frame 0/255 filled segments; outer/invalid pixels are zero.
- Existing boundary, per-row width, valid-row count, and mean-width arrays remain.

Fractional centerlines are computed interval midpoints, not a claim of subpixel
sensor accuracy. The raw image is preserved. Red markers enlarge with preview
zoom; the four blue window guides remain three display pixels thick.

No image rectification, perpendicular-to-axis correction, texture reconstruction,
or ovality calculation is added by segmentation. The separate diameter_geometry.py
module now provides symmetric 45-degree mirror geometry, physical pixel-to-mm
conversion, and a single-press scalar correction; see README.md. The paper's reported accuracy has not
been established for this camera and optical setup.

The cited OSF source repository could not be retrieved in this session (403).
This implementation follows the paper's description with the choices above.
The author's older GitHub examples were reviewed but are not presented as an
exact implementation of the paper's gradient method.

Validation includes known-width bars, internal transitions, bright-on-dark and
transparent-style profiles, moving row centerlines, missing/single edges, outer
window exclusion, raw preservation, nine-marker valid rows, vertical averaging,
clipped-window normalization, fractional gradients, and isolated-row noise.
Vertical averaging reduces y detail and correlates neighboring row measurements.

## Right-boundary correction validation

The previous unsigned first/last-threshold rule selected a later background
brightness ramp, shifting the right edge and its midpoint. In the captured
320x240 frame (validation/boundary_raw.pgm), with windows 69/165/245/316,
threshold 9 and vertical window 20, row 30's left-section pair changed from
93/154 to 96/118. Its center changed from 123.5 to 107. Other rows whose soft
edge falls below threshold are now rejected rather than assigned a distant edge.
This is image-processing validation, not independent diameter ground truth.
