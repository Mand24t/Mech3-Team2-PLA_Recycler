"""Polarity-independent rim-pair tracking for near-vertical transparent strands.

Each ROI must contain one strand and some background on either side. This is
an image-evidence method, not a diameter prior: calibration/target diameter is
never used to select a width. Persistent background lines can still resemble
rims, so ambiguous paths and insufficient row support are rejected.
"""
from math import ceil, log1p
from statistics import median


def rim_candidates(values, start, end, threshold):
    """Remove slowly varying illumination from signed horizontal derivatives.

    Pairing uses unsigned response so either bright or dark refractive rims
    work. Opposite sides of a thin rim are consolidated into one rim location.
    Returns at most eight features, to bound tracking time.
    """
    if end-start < 10:
        return [], []
    derivative = [(values[x+1]-values[x-1])/2 for x in range(start+1, end-1)]
    prefix = [0.0]
    for value in derivative:
        prefix.append(prefix[-1]+value)
    response = []
    for i, value in enumerate(derivative):
        lo, hi = max(0, i-6), min(len(derivative), i+7)
        response.append(abs(value-(prefix[hi]-prefix[lo])/(hi-lo)))
    background = median(response)
    deviation = median(abs(value-background) for value in response)
    cutoff = max(float(threshold), background+3*1.4826*deviation, 0.5)
    groups = []
    active = None
    marked = []
    for i, value in enumerate(response):
        x = start+1+i
        # A feature needs background on both sides; clipped rims are invalid.
        if value <= cutoff or x < start+2 or x >= end-2:
            continue
        marked.append(x)
        if active is None or x-active[-1][0] > 3:
            active = []
            groups.append(active)
        active.append((x, value))
    features = []
    for group in groups:
        if group[-1][0]-group[0][0] > 6:
            continue  # Broad illumination variation, not a localized rim.
        energy = sum(value for _, value in group)
        x = int(sum(x*value for x, value in group)/energy+0.5)
        strength = max(value for _, value in group)
        features.append((x, strength/cutoff))
    return sorted(sorted(features, key=lambda item: item[1], reverse=True)[:8]), marked


def track_transparent_edges(pixels, width, height, start, end, threshold, gradient_mask):
    """Return supported row pairs, plus a short status suitable for the viewer.

    A small beam search rewards balanced rim strength and continuity of BOTH
    edges. Missing rows stay missing; no interpolated width enters statistics.
    At most three missing rows can connect tracks. No temporal data is reused.
    """
    empty = [(None, None)]*height
    if height < 6 or end-start < 10:
        return empty, "need a taller frame or wider window"
    # score, left, right, last-supported y, supported count, row history
    beam = []
    for y in range(height):
        offset = y*width
        features, marked = rim_candidates(pixels[offset:offset+width], start, end, threshold)
        for x in marked:
            gradient_mask[offset+x] = 255
        proposals = {}
        for i, (left, ls) in enumerate(features):
            for right, rs in features[i+1:]:
                span = right-left
                if not 6 <= span <= 0.85*(end-start):
                    continue
                strength = 2*ls*rs/(ls+rs)
                quality = 1+log1p(min(strength, 20))
                quality += 0.3*span/(end-start)
                quality -= 0.2*abs((left+right)/2-(start+end)/2)/(end-start)
                best = (quality, left, right, y, 1, (None,)*y+((left, right),))
                for score, old_left, old_right, last_y, hits, history in beam:
                    gap = y-last_y
                    dl, dr = abs(left-old_left), abs(right-old_right)
                    if gap > 4 or max(dl, dr) > 3*gap:
                        continue
                    penalty = 0.25*(dl+dr)+0.35*abs(span-(old_right-old_left))
                    candidate = (score+quality-penalty, left, right, y, hits+1, history+((left, right),))
                    if candidate[0] > best[0]:
                        best = candidate
                proposals[(left, right, y)] = best
        for score, left, right, last_y, hits, history in beam:
            if y-last_y <= 3:
                key = (left, right, last_y)
                proposals[key] = (score-1.5, left, right, last_y, hits, history+(None,))
        beam = sorted(proposals.values(), key=lambda state: state[0], reverse=True)[:16]
    required = max(6, ceil(height*0.65))
    supported = [state for state in beam if state[4] >= required]
    if not supported:
        return empty, "no consistent pair; check bounds, threshold and lighting"
    best = supported[0]
    # Reject a similarly strong alternative representing substantially different
    # boundaries. Duplicate paths to the same final rims are not alternatives.
    for alternative in supported[1:]:
        if alternative[0] < best[0]*0.93:
            continue
        common = [(a, b) for a, b in zip(best[5], alternative[5]) if a is not None and b is not None]
        different = sum(max(abs(a[0]-b[0]), abs(a[1]-b[1])) > 3 for a, b in common)
        if common and different >= len(common)*0.5:
            return empty, "ambiguous rims; narrow this view window"
    return [pair if pair is not None else (None, None) for pair in best[5]], "rim pair tracked"


def background_difference_tracks(current, reference, width, height, bounds, threshold, mask):
    """Measure changed pixels against a filament-free, fixed-scene reference.

    The user threshold is used literally, without exposure normalization or an
    automatic noise floor. All widths require current foreground evidence; row
    continuity selects one locally bright component. Missing widths stay missing.
    """
    empty = [[(None, None)]*height for _ in bounds]
    if height < 6:
        return empty, ["need at least six image rows"]*3
    # Literal user threshold: no automatic gain, offset or noise-floor changes.
    residuals = [[current[y*width+x]-reference[y*width+x] for x in range(width)] for y in range(height)]
    cutoffs = [float(threshold)]*height
    mask[:] = bytes(255 if abs(value) > threshold else 0 for row in residuals for value in row)
    tracks, statuses = [], []
    for lo, hi in bounds:
        pairs, status = bright_band_track(current, residuals, cutoffs, width, height, lo, hi)
        tracks.append(pairs)
        statuses.append(status)
    return tracks, statuses


def bright_band_track(current, residuals, cutoffs, width, height, lo, hi):
    """Select one locally bright changed component and track both boundaries.

    Changed shadows must not extend a bright stripe's width. Signed residual
    components separate touching bright/dark changes; actual image contrast
    chooses the bright stripe even if it dims a brighter reference background.
    There is no width assumption shared between the three optical views.
    """
    # score, left, right, last supported row, valid count, history
    beam, completed = [], []
    maximum_gap = max(3, min(20, height//10))
    required = max(6, ceil(height*.65))
    for y in range(height):
        row = current[y*width:(y+1)*width]
        residual, cutoff = residuals[y], cutoffs[y]
        groups = []
        group, polarity = [], 0
        for x in range(lo, hi):
            sign = 1 if residual[x] > cutoff else -1 if residual[x] < -cutoff else 0
            if not sign:
                continue
            if group and (x-group[-1] > 2 or sign != polarity):
                groups.append(group)
                group = []
            group.append(x)
            polarity = sign
        if group:
            groups.append(group)
        candidates = []
        for points in groups:
            left, right = points[0], points[-1]+1
            span = right-left
            if left <= lo or right >= hi or not 3 <= span <= .85*(hi-lo) or len(points)/span < .6:
                continue
            body = sum(row[left:right])/span
            flank_left = sum(row[max(lo, left-3):left])/(left-max(lo, left-3))
            flank_right = sum(row[right:min(hi, right+3)])/(min(hi, right+3)-right)
            contrast = min(body-flank_left, body-flank_right)
            if contrast <= max(.5, cutoff*.2):
                continue
            quality = 1+log1p(contrast/max(.5, cutoff))+len(points)/span
            candidates.append((left, right, quality))
        proposals = {}
        for left, right, quality in candidates:
            best = (quality, left, right, y, 1, (None,)*y+((left, right),))
            for score, old_left, old_right, last_y, hits, history in beam:
                gap = y-last_y
                dl, dr = abs(left-old_left), abs(right-old_right)
                if gap > maximum_gap+1 or max(dl, dr) > min(6, 3*gap):
                    continue
                penalty = .25*(dl+dr)+.5*abs((right-left)-(old_right-old_left))
                candidate = (score+quality-penalty, left, right, y, hits+1, history+((left, right),))
                if candidate[0] > best[0]:
                    best = candidate
            proposals[(left, right, y)] = best
        for score, left, right, last_y, hits, history in beam:
            if y-last_y <= maximum_gap:
                proposals[(left, right, last_y)] = (score-.5, left, right, last_y, hits, history+(None,))
        beam = sorted(proposals.values(), key=lambda state: state[0], reverse=True)[:16]
        completed.extend(state for state in beam if state[4] >= required)
        completed = sorted(completed, key=lambda state: state[0], reverse=True)[:32]
    if not completed:
        return [(None, None)]*height, "no consistent bright stripe; check bounds/background"
    best = completed[0]
    for alternative in completed[1:]:
        if alternative[0] < best[0]*.9:
            continue
        common = [(a, b) for a, b in zip(best[5], alternative[5]) if a is not None and b is not None]
        different = sum(max(abs(a[0]-b[0]), abs(a[1]-b[1])) > 3 for a, b in common)
        if common and different >= len(common)*.5:
            return [(None, None)]*height, "multiple bright stripes; narrow this view window"
    return [pair if pair is not None else (None, None) for pair in best[5]]+[(None, None)]*(height-len(best[5])), "bright stripe tracked"
