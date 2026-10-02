"""Analyses computed HERE, from an interval of one pattern.

UI-free: the window collects the interval - a stretch dragged along the
curve (two sample indices) or two typed positions - and calls `run`;
everything below is arithmetic on numpy arrays.

**On the intensities as stored, on the axis as shown.** A position, an area
or a width is measured on the pattern's own intensities - never on the
normalised or magnified curve, which are how it is drawn - along the x
axis the figure shows: 2-theta, d or Q. Changing the axis measures every
analysis again on the new one (`Document.set_x_quantity`); an analysis
records which axis its numbers are on (`Analysis.axis`).

The three measurements:

* **Peak position**: the strongest point in the interval, refined by the
  parabola through it and its two neighbours, so the position is not tied
  to the sampling grid. With it, its d-spacing, when the pattern's
  wavelength is known (on a d axis the position IS one).
* **Peak area**: the intensity over the interval above the straight
  baseline between its two ends (a trapezoid sum), in intensity times the
  axis's unit.
* **Peak width**: the full width at half height of the strongest point
  above that same baseline, each side's crossing found by linear
  interpolation between the two samples either side of it.

The drag list also offers what is not a measurement but is made from the
same interval (`ACTIONS`): highlighting it, magnifying it, normalising
every pattern to it, and cutting it out of the x axis. The window does
those; they are listed here so the list reads one table.
"""

import numpy as np

from . import model
from . import units


class Measurement(object):
    """One entry in the quick-select list. `title` is what the list shows,
    `name` the model an analysis records."""

    def __init__(self, name, run=None, needs=2, note="", title="",
                 action=False):
        self.name = name
        self.title = title or name
        self.run = run
        #: How many cursors it takes (every one here: an interval).
        self.needs = needs
        self.note = note
        #: True for an entry that makes something other than an analysis.
        self.action = action


def _quantity(scan, quantity=None):
    return quantity or scan.quantity()


def _unit(scan, quantity=None):
    return units.X_TEXT.get(_quantity(scan, quantity), "")


def _series(scan, x0, x1, span=None, quantity=None):
    """`(x, I)` of the drawn samples in the interval, ASCENDING in x (a d
    axis runs the other way), or None when there are fewer than three
    measured ones or the pattern cannot be put on the axis.

    By SAMPLE where the interval was dragged along the curve (`span`, two
    indices), else by position."""
    x = scan.x_values(quantity=quantity)
    if x is None:
        return None
    values = scan.intensity()
    lo, hi = scan.kept_range(len(x))
    if span is not None:
        lo = max(lo, int(min(span)))
        hi = min(hi, int(max(span)) + 1)
        xs, ys = x[lo:hi], values[lo:hi]
    else:
        low, high = sorted((float(x0), float(x1)))
        xs, ys = x[lo:hi], values[lo:hi]
        with np.errstate(invalid="ignore"):
            inside = (xs >= low) & (xs <= high)
        xs, ys = xs[inside], ys[inside]
    measured = np.isfinite(xs) & np.isfinite(ys)
    if measured.sum() < 3:
        return None
    xs, ys = xs[measured], ys[measured]
    if xs[0] > xs[-1]:
        xs, ys = xs[::-1], ys[::-1]
    return xs, ys


def _baseline(xs, ys):
    """The straight line between the interval's first and last samples."""
    span = float(xs[-1] - xs[0])
    if abs(span) < 1e-12:
        return np.full(len(ys), float(ys[0]))
    return ys[0] + (ys[-1] - ys[0]) * (xs - xs[0]) / span


def _vertex(xs, ys, i):
    """The x of the top of the parabola through samples i-1, i, i+1 (x
    need not be evenly spaced), kept between the two neighbours; the
    sample's own x at either end of the series."""
    if i <= 0 or i >= len(xs) - 1:
        return float(xs[i])
    x = xs[i - 1:i + 2].astype(float)
    y = ys[i - 1:i + 2].astype(float)
    try:
        a, b, _c = np.polyfit(x - x[1], y, 2)
    except (ValueError, np.linalg.LinAlgError):
        return float(xs[i])
    if not a < 0:
        return float(xs[i])
    top = x[1] - b / (2.0 * a)
    return float(min(max(top, x[0]), x[2]))


def _cursors(scan, x0, x1, quantity=None):
    low, high = sorted((float(x0), float(x1)))
    unit = _unit(scan, quantity)
    # In full: an interval converted to another axis must find the same
    # samples inside it when it is read back.
    return {"Cursor x": "{!r} {}".format(low, unit),
            "Cursor x1": "{!r} {}".format(high, unit)}


def _position(scan, value, quantity=None):
    """The "Position" field, and "d" beside it where it can be had."""
    quantity = _quantity(scan, quantity)
    out = {"Position": "{:.6g} {}".format(value, _unit(scan, quantity))}
    if quantity != units.D:
        d = units.d_spacing(value, quantity, scan.sample.wavelength)
        if d is not None:
            out["d"] = "{:.6g} A".format(d)
    return out


def peak_position(scan, x0, x1, span=None, quantity=None):
    series = _series(scan, x0, x1, span, quantity)
    if series is None:
        return None
    xs, ys = series
    i = int(np.nanargmax(ys))
    if i in (0, len(xs) - 1):
        # The strongest point at an end of the interval: the curve only
        # rises towards it, so there is no peak inside to name.
        return None
    out = {"Model": "Peak position"}
    out.update(_cursors(scan, x0, x1, quantity))
    out.update(_position(scan, _vertex(xs, ys, i), quantity))
    out["Height"] = "{:.6g}".format(float(ys[i]))
    return out


def peak_area(scan, x0, x1, span=None, quantity=None):
    series = _series(scan, x0, x1, span, quantity)
    if series is None:
        return None
    xs, ys = series
    above = ys - _baseline(xs, ys)
    area = float(np.sum((above[1:] + above[:-1]) * np.diff(xs)) / 2.0)
    i = int(np.nanargmax(above))
    out = {"Model": "Peak area"}
    out.update(_cursors(scan, x0, x1, quantity))
    out["Area"] = "{:.6g} {}".format(area, _unit(scan, quantity))
    out.update(_position(scan, _vertex(xs, above, i), quantity))
    out["Height"] = "{:.6g}".format(float(above[i]))
    return out


def peak_width(scan, x0, x1, span=None, quantity=None):
    series = _series(scan, x0, x1, span, quantity)
    if series is None:
        return None
    xs, ys = series
    base = _baseline(xs, ys)
    above = ys - base
    i = int(np.nanargmax(above))
    height = float(above[i])
    if not height > 0:
        return None
    half = height / 2.0
    left = _crossing(xs, above, i, half, -1)
    right = _crossing(xs, above, i, half, +1)
    if left is None or right is None:
        return None
    base_left = float(np.interp(left, xs, base))
    base_right = float(np.interp(right, xs, base))
    out = {"Model": "Peak width"}
    out.update(_cursors(scan, x0, x1, quantity))
    out["FWHM"] = "{:.6g} {}".format(abs(right - left), _unit(scan, quantity))
    out.update(_position(scan, _vertex(xs, above, i), quantity))
    out["Height"] = "{:.6g}".format(height)
    # Where the half height is met, as stored intensity: what the figure
    # draws the width at (`PlotWidget.width_line`).
    out["Left x"] = "{:.6g}".format(left)
    out["Left I"] = "{:.6g}".format(base_left + half)
    out["Right x"] = "{:.6g}".format(right)
    out["Right I"] = "{:.6g}".format(base_right + half)
    return out


def _crossing(xs, above, i, level, step):
    """The x where `above` falls through `level`, walking from sample `i`
    by `step`; None when it never does inside the interval."""
    j = i
    while 0 <= j + step < len(xs):
        k = j + step
        if above[k] <= level:
            a, b = float(above[j]), float(above[k])
            if a == b:
                return float(xs[k])
            t = (a - level) / (a - b)
            return float(xs[j] + t * (xs[k] - xs[j]))
        j = k
    return None


def width_points(analysis):
    """`[(x, I), (x, I)]`: where a peak width meets half height, or []."""
    fields = analysis.fields
    try:
        return [(float(fields["Left x"]), float(fields["Left I"])),
                (float(fields["Right x"]), float(fields["Right I"]))]
    except (KeyError, TypeError, ValueError):
        return []


#: The quick-select list, in the order it is offered: the analyses, then
#: what the same stretch can be made into.
MODELS = (
    Measurement("Peak position", peak_position, title="Peak position",
                note="the strongest point, between the samples, and its d"),
    Measurement("Peak area", peak_area, title="Peak area",
                note="above a straight baseline"),
    Measurement("Peak width", peak_width, title="Peak width (FWHM)",
                note="full width at half height above the baseline"),
)

HIGHLIGHT = "Highlight"
MAGNIFY = "Magnify"
NORMALISE = "Normalise to this peak"
BREAK = "Break the x axis here"

ACTIONS = (
    Measurement(HIGHLIGHT, title="Highlight", action=True,
                note="shade the stretch across the figure"),
    Measurement(MAGNIFY, title="Magnify...", action=True,
                note="this pattern magnified in the stretch"),
    Measurement(NORMALISE, title="Normalise to this peak", action=True,
                note="every pattern's peak here at the same strength"),
    Measurement(BREAK, title="Break the x axis here", action=True,
                note="cut the stretch out of the x axis"),
)


def models_for(_scan=None):
    """Everything offered on a stretch of a curve: analyses and actions."""
    return list(MODELS) + list(ACTIONS)


def by_name(name):
    for entry in MODELS + ACTIONS:
        if entry.name == name:
            return entry
    return None


def is_action(name):
    entry = by_name(name)
    return entry is not None and entry.action


def run(name, scan, x0, x1, span=None, quantity=None):
    """Compute one analysis and attach it to `scan`, or return None.

    The cursors arrive on the figure's x axis (or on `quantity`'s, which
    the analysis is then measured on); `span`, where the interval was
    dragged along the curve, is the two sample indices."""
    entry = by_name(name)
    fields = compute(name, scan, x0, x1, span, quantity)
    if entry is None or entry.action or not fields:
        return None
    analysis = model.Analysis(id(fields) % 1000000, scan,
                              fields.get("Model", name), fields)
    analysis.span = clean_span(span)
    analysis.visible = True
    analysis.label = None
    analysis.axis = _quantity(scan, quantity)
    scan.analysis_objects.append(analysis)
    return analysis


def clean_span(span):
    """Two sample indices, lowest first, or None."""
    if not span or len(span) != 2 or None in tuple(span):
        return None
    low, high = sorted(int(i) for i in span)
    return (low, high) if high > low else None


def compute(name, scan, x0, x1, span=None, quantity=None):
    """The result fields of one analysis on an interval, or None: `run`
    without making an object, for an analysis measured again IN PLACE."""
    entry = by_name(name)
    if entry is None or entry.action:
        return None
    try:
        fields = entry.run(scan, x0, x1, span=clean_span(span),
                           quantity=quantity)
    except (ValueError, IndexError, FloatingPointError, ZeroDivisionError):
        return None
    return fields or None


def relabelled(analysis, fields):
    """The label an analysis carries once its fields become `fields`: the
    same one - a label is a template, its `{}` the measurement."""
    return analysis.label


def walk_to(values, start, target, lo=0, hi=None, slack=0.5):
    """The index reached walking ALONG `values` from `start` towards the
    value `target`, either way, inside `[lo, hi)`: the one that gets
    closest before the values turn away for good. How a typed position
    becomes a sample on the curve."""
    hi = len(values) if hi is None else int(hi)
    lo = int(lo)
    start = int(min(max(int(start), lo), hi - 1))
    best = start
    for step in (1, -1):
        j = start
        while lo <= j + step < hi:
            j += step
            gap = abs(float(values[j]) - target)
            if gap < abs(float(values[best]) - target):
                best = j
            elif gap > abs(float(values[best]) - target) + slack:
                break
    return best
