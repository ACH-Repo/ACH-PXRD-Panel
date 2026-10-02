"""Arranging scans: the operators that move several at once.

UI-free, and every function returns CHANGES - `[(scan, "offset", value), ...]`
- rather than applying them. The window pushes that list through the undo
stack, so "distribute evenly" is one Ctrl+Z like every other action, and these
functions stay testable without a window.

The baseline alignment is the one piece of real arithmetic: minimising
`sum w (ref - (curve + c))^2` over one number `c` has a closed form,
`c = weighted mean of (ref - curve)`, so no optimiser is needed and neither
is scipy.
"""

import numpy as np


def _monotonic(x, y):
    """`(x, y)` sorted and de-duplicated, ready for `np.interp`.

    A curve's x need not be sorted (a pattern file may run from high to
    low position), so anything that interpolates has to sort first. Duplicate x values are averaged rather than dropped, because
    dropping one silently prefers whichever sample came first.

    A flagged sample (NaN in either) is left out: `np.interp` over one gives
    NaN, and a NaN offset is a curve drawn nowhere and saved so (review
    F11).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    measured = np.isfinite(x) & np.isfinite(y)
    x, y = x[measured], y[measured]
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    unique, index, inverse = np.unique(x, return_index=True,
                                       return_inverse=True)
    if len(unique) != len(x):
        counts = np.bincount(inverse)
        y = np.bincount(inverse, weights=y) / np.maximum(counts, 1)
        x = unique
    return x, y


def align_to(reference, scans, curve_of, window=(0.0, 1.0), points=2000):
    """Offsets that lay each scan on top of `reference` where they overlap.

    `curve_of(scan)` gives `(x, y)` as drawn. `window` is the part of the
    common x range the curves should agree over, as fractions - the default
    is all of it, and `(0, 0.4)` is "match the start", which
    is what you want when the interesting difference is at the end.

    A scan with no overlap is left where it is rather than moved to a
    meaningless place.
    """
    ref_x, ref_y = curve_of(reference)
    if ref_x is None or len(ref_x) < 2:
        return []
    ref_x, ref_y = _monotonic(ref_x, ref_y)
    if len(ref_x) < 2:
        return []
    changes = []
    for scan in scans:
        if scan is reference:
            continue
        x, y = curve_of(scan)
        if x is None or len(x) < 2:
            continue
        x, y = _monotonic(x, y)
        if len(x) < 2:
            continue
        lo = max(float(ref_x[0]), float(x[0]))
        hi = min(float(ref_x[-1]), float(x[-1]))
        if hi <= lo:
            continue
        grid = np.linspace(lo, hi, int(points))
        a = np.interp(grid, ref_x, ref_y)
        b = np.interp(grid, x, y)
        frac = (grid - lo) / max(hi - lo, 1e-12)
        weight = np.where((frac >= window[0]) & (frac <= window[1]), 1.0, 0.1)
        shift = float(np.sum(weight * (a - b)) / np.sum(weight))
        changes.append((scan, "offset", float(scan.offset) + shift))
    return changes


def stack(scans, step, first=0.0):
    """Give the scans offsets `first`, `first + step`, ... in list order.

    The order is the one the caller hands over, which is the outliner's -
    what you see top to bottom is what gets stacked top to bottom.
    """
    changes = []
    for i, scan in enumerate(scans):
        changes.append((scan, "offset", float(first) + i * float(step)))
    return changes


def distribute(scans):
    """Even spacing between the topmost and bottommost scan, order kept.

    The two ends stay where they are - somebody put them there - and
    everything between them is spread evenly, which is the gesture you want
    after dragging eight scans roughly into place.
    """
    scans = [s for s in scans]
    if len(scans) < 3:
        return []
    ordered = sorted(scans, key=lambda s: s.offset)
    lo = float(ordered[0].offset)
    hi = float(ordered[-1].offset)
    if hi == lo:
        return []
    step = (hi - lo) / (len(ordered) - 1)
    changes = []
    for i, scan in enumerate(ordered):
        value = lo + i * step
        if abs(value - scan.offset) > 1e-12:
            changes.append((scan, "offset", value))
    return changes


def suggested_step(scans, curve_of, fraction=0.6):
    """A sensible vertical spacing for a stack, in the current unit.

    The tallest scan's own span times `fraction`: enough that curves do not
    sit on top of one another, not so much that the stack runs off the plot.
    Measured rather than guessed, because a spacing that is right in counts
    is wrong by a factor of a thousand once normalised.
    """
    spans = []
    for scan in scans:
        x, y = curve_of(scan)
        if y is None or not len(y):
            continue
        y = np.asarray(y, dtype=float)
        y = y[np.isfinite(y)]
        if not len(y):
            continue
        spans.append(float(np.max(y) - np.min(y)))
    if not spans:
        return 0.0
    return max(spans) * float(fraction)
