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

1. Use three independent windows [x1,x2), [x3,x4), [x5,x6), each with its
   own start and end slider. Exclude pixels outside each window; gaps are allowed.
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
zoom; the six blue window guides remain three display pixels thick.

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


## Initial transparent rim tracker (offline experimental method)

This mode is an engineering extension, not a claim that the cited paper prescribes
this algorithm. It bypasses `slice_edges` and the dark-body/recovery rules.
`transparent_filament.py` applies the existing clipped vertical average, a centered
horizontal difference, and subtraction of a 13-sample local derivative mean to
suppress gradual shading. Absolute residuals retain both edge polarities; the
threshold is max(user threshold, median + 3 * 1.4826 * MAD, 0.5 grayscale units).
The general derivative-edge principle is documented by OpenCV:
https://docs.opencv.org/4.8.0/d5/d0f/tutorial_py_gradients.html
No OpenCV dependency is required by this implementation.

Neighboring responses consolidate into localized rim features (including both
sides of a thin rim); markers represent response-weighted rim centers. Up to eight
features enter a 16-state beam search per row. Pair scores reward balanced edge
strength and penalize boundary/width jumps; movement is limited to 3 px per row.
Candidate widths are 6 px through 85% of window width. Rims must have background
on both sides (two-pixel margin), so clipped edges are rejected. Each ROI should
contain one near-vertical strand. No target, true diameter, geometry scale or
previous frame is used to select a pair.

A path must have at least six supported rows and 65% of image rows. Up to three
missing rows can connect a track, but these rows remain unmeasured: red markers,
width arrays and averages contain no interpolated widths. Competing paths within
7% of the best score, with endpoints differing by >3 px in at least half of common
rows, reject the ROI as ambiguous. Noise, empty/ramp scenes and single rims must
not become diameters. There is no guarantee against a pair of persistent background
lines resembling a filament. Narrow ROIs and visible optical evidence are required.

The two rim centers define pixel width; this may differ from the normal mode's
intensity transition convention. Verify boundaries visually and recalibrate before
using measurements for control. The tracker is tested with synthetic dark/bright
rims, clear interiors, slope/noise, missing evidence, competing rims, inverted
previews and backward-compatible settings. These tests establish software behavior,
not live measurement accuracy.


## Current GUI transparent mode: reference subtraction

A captured live-view screenshot reproduced the failure of the initial tracker:
all views were rejected even at zero user threshold. The automatic response-MAD
cutoff and six-pixel feature restriction discarded blurred structures. Relaxing
them produced continuous lines but could bracket internal reflections, so these
unverified widths are not used by the GUI.

Capture Background now collects five seconds of frames without blocking Tk.
`background_capture.py` accumulates each unique received frame once into float64
sums and divides each pixel by the actual frame count. Fractional means are
retained. A separate worker capture queue prevents latest-preview replacement
from losing received frames during the capture window. Resolution changes or
interrupted streams abort capture; references are session-only.

The full-frame visualization uses exactly `abs(raw_current - averaged_reference)
> user_threshold`, with no gain/offset compensation or adaptive noise floor.
Every deviated pixel is red, even outside the measurement windows, whether or
not it belongs to an accepted filament track. Unchanged pixels retain grayscale
(or Light mode's inverted grayscale); no additional red boundary/center markers
are added in this reference-based preview. The `gradient_mask` field contains
this full-frame deviation mask in transparent-reference mode.

For diameter tracking, both the uncolored current frame and averaged reference
receive the existing clipped vertical averaging. Their signed difference uses
the same literal user threshold. This spatial averaging can suppress a raw
single-pixel deviation that remains visible in red; the overlay is diagnostic,
not an assertion that every red pixel is filament. No automatic illumination
normalization changes the threshold or hides an exposure difference.

For each ROI, consecutive changed pixels are grouped by residual sign with at
most one missing pixel inside a group. A touching negative shadow cannot extend
a positive band, or vice versa. Candidate widths must be >=3 px and <=85% of the
ROI, have background on both ends and >=60% changed-pixel coverage. Mean current
intensity must exceed both three-pixel flanks by max(0.5, 0.2*cutoff) levels. This
selects the user's confirmed narrow bright stripe, not an adjacent dark band;
residual sign alone does not decide brightness.

A 16-state beam tracks the two candidate endpoints independently in each ROI.
Scores reward local contrast and changed coverage; penalties are 0.25 times
endpoint displacement plus 0.5 times width change. Motion between supported rows
is limited to min(6, 3*gap) px per boundary. Missing sections up to 10% of image
height (clamped 3..20 rows) may connect a track; missing widths stay None. Completed
tracks are retained so a weak bottom section cannot erase valid upper rows. Six
and 65% of rows must contain measured foreground. Competing tracks within 10%
of the best score and differing by >3 px in at least half their common rows are
rejected. There is no cross-view width equality or desired-diameter constraint.

The old single-frame rim tracker remains an offline API experiment; the GUI sets
`require_transparent_background=True` and publishes no diameter without a reference.
Synthetic regression cases include a touching dark shadow, intermittent bright
reflections, independent widths/slope, weak lower rows and competing bright bands.
A screenshot showed the failure pattern, but no captured current/reference pair
was available for quantitative live validation of the correction.

The underlying static-scene reference principle is described in OpenCV's
background-subtraction tutorial; this implementation uses a fixed reference,
not its adaptive MOG2/KNN algorithms:
https://docs.opencv.org/4.12.0/d1/dc5/tutorial_background_subtraction.html
A transparent object may still produce changes only at internal optical features;
reference subtraction alone is not independent ground truth for physical diameter.
Known-diameter validation remains necessary. Multiple moving objects in one ROI,
camera/mirror movement, or spatially varying illumination invalidate the assumptions.


## GUI polynomial boundary fits supersede component tracking for diameter

With a reference, the GUI passes `polynomial_order` (default 3) to processing.
The full-frame raw absolute deviation mask is computed first. Within each of
three window pairs, first/last deviated pixel centers supply left/right samples
on each nonempty row. Six independent ordinary least-squares polynomials model
x as a function of y. No extreme samples are trimmed: even a single stray red
pixel can influence the requested outermost boundary.

`polynomial_boundaries.py` normalizes the observed y range to [-1,1], constructs
a Vandermonde basis and uses modified Gram-Schmidt QR with two orthogonalization
passes. Degrees 0..8 are supported without a new dependency. A minimum of
max(6, degree+1, ceil(0.65*frame_height)) distinct sample rows is enforced.
Curves are evaluated only within the observed y span, including interior gaps;
no extrapolated top/bottom widths enter the average. Nonfinite, crossing or
out-of-window pairs invalidate the entire affected view. Tiny numerical boundary
roundoff is tolerated; no meaningful clipping or degree reduction is performed.

Widths are float fitted-right minus fitted-left, averaged per view and converted
by the existing geometry/calibration code. Yellow rendering rounds curve x only
after all measurements are computed; adjacent rounded points are connected.
Red deviation pixels remain visible elsewhere, and the raw frame stays intact.
The `curve_sample_rows` field distinguishes actual sampled rows from the fitted
rows used for widths (`valid_rows`). The prior bright-component tracker remains
available to API callers omitting polynomial_order, but GUI diameter uses fits.
The one-pixel center-distance vs inclusive-interval convention change requires
checking calibration on a known filament after updating.
