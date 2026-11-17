"""Least-squares x(y) fits of outermost per-row foreground pixel centers.

Use normalized y coordinates and reorthogonalized QR, without extra packages.
No trimming or expected-diameter prior changes which red pixels enter the fit.
"""
import math


def least_squares_curve(samples, order):
    if type(order) is not int or not 0 <= order <= 8:
        raise ValueError("Polynomial order must be a whole number from 0 to 8.")
    if len(samples) < order+1:
        raise ValueError("Not enough distinct rows for the selected polynomial order.")
    lo, hi = samples[0][0], samples[-1][0]
    if hi <= lo:
        raise ValueError("Need measurements on distinct rows.")
    normalized = [(2*(y-lo)/(hi-lo)-1) for y, _ in samples]
    q, r = [], [[0.0]*(order+1) for _ in range(order+1)]
    for j in range(order+1):
        column = [y**j for y in normalized]
        # A second orthogonalization pass protects the higher orders.
        for _ in range(2):
            for i, basis in enumerate(q):
                projection = sum(a*b for a, b in zip(basis, column))
                r[i][j] += projection
                column = [a-projection*b for a, b in zip(column, basis)]
        norm = math.sqrt(sum(value*value for value in column))
        if norm < 1e-10:
            raise ValueError("Polynomial fit is singular; reduce its order.")
        r[j][j] = norm
        q.append([value/norm for value in column])
    target = [sum(basis[i]*sample[1] for i, sample in enumerate(samples)) for basis in q]
    coefficients = [0.0]*(order+1)
    for j in range(order, -1, -1):
        coefficients[j] = (target[j]-sum(r[j][k]*coefficients[k] for k in range(j+1, order+1)))/r[j][j]
    def evaluate(y):
        normalized_y = 2*(y-lo)/(hi-lo)-1
        value = 0.0
        for coefficient in reversed(coefficients):
            value = value*normalized_y+coefficient
        return value
    return evaluate


def fit_deviation_boundaries(mask, width, height, bounds, order):
    if type(order) is not int or not 0 <= order <= 8:
        raise ValueError("Polynomial order must be a whole number from 0 to 8.")
    tracks, statuses, counts = [], [], []
    for start, end in bounds:
        left, right = [], []
        for y in range(height):
            points = [x for x in range(start, end) if mask[y*width+x]]
            if points:
                left.append((y, points[0]))
                right.append((y, points[-1]))
        counts.append(len(left))
        empty = [(None, None)]*height
        if len(left) < max(6, order+1, math.ceil(height*.65)):
            tracks.append(empty)
            statuses.append(f"need more red-pixel rows for order {order} ({len(left)}/{height})")
            continue
        try:
            lcurve, rcurve = least_squares_curve(left, order), least_squares_curve(right, order)
            pairs = [(lcurve(y), rcurve(y)) if left[0][0] <= y <= left[-1][0] else (None, None)
                     for y in range(height)]
            # Reject an invalid fit as a whole, rather than clipping it into a
            # plausible diameter. A small tolerance handles numerical roundoff.
            if any(not math.isfinite(l+r) or l < start-1e-7 or r > end-1+1e-7 or r-l <= 1e-7
                   for l, r in pairs if l is not None):
                raise ValueError("curves cross or leave the window; reduce order or adjust bounds")
            tracks.append(pairs)
            statuses.append(f"order {order} fit: {len(left)}/{height} sample rows")
        except ValueError as error:
            tracks.append(empty)
            statuses.append(str(error))
    return tracks, statuses, tuple(counts)
