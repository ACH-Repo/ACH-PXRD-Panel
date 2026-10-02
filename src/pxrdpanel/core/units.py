"""What the axes mean: 2-theta, d or Q along x; intensity up y.

UI-free on purpose: everything here is arithmetic and naming, so it can be
tested without a window.

**Three views of one x, converted honestly.** A pattern is recorded in
2-theta at its wavelength. d (angstrom) and Q (inverse angstrom) are the
same reflections without the wavelength in them:

    d = lambda / (2 sin theta)        Q = 4 pi sin(theta) / lambda = 2 pi / d

So 2-theta needs nothing, and d and Q need the pattern's wavelength. A
pattern whose file does not state one is NOT drawn in d or Q until the user
gives it - never with a wavelength made up for it - and says so where it
would be. Patterns measured at different wavelengths share a 2-theta axis
only as recorded (the same reflection at two angles); d and Q are where
they compare.

**Intensity is what the file holds** - counts, counts per second or
arbitrary units, which no file reliably says - so the y axis says
"a.u.". There is nothing to convert; `to_display` is the identity, kept
because the family's drawing code asks it.

**Normalisation is allowed, and it is written on the axis.** Scaling each
pattern to itself is the usual way to compare patterns counted for
different times; it is never silent: a normalised axis's caption says
"normalised", and its numbers are 0 to 1. Three ways:

* `global`: ALL the patterns on show together - the lowest value of any is
  0, the highest of any 1 - so their heights keep their proportions. A
  pattern opened, hidden or cut changes the scale, and every curve follows
  (`Document.norm_extent`).
* `range`: each pattern from its own lowest to its own highest value is 0
  to 1.
* `band`: a chosen peak is the yardstick - its top is 1, the pattern's
  lowest value 0: the patterns compared at equal strength of that peak.
"""

import math
import re

import numpy as np

# ------------------------------------------------------------------ x
TWO_THETA = "2theta"
D = "d"
Q = "Q"
#: In menu order.
QUANTITIES = (TWO_THETA, D, Q)

QUANTITY_TITLES = {TWO_THETA: "2\u03b8 (degrees)", D: "d (\u00c5)",
                   Q: "Q (\u00c5\u207b\u00b9)"}

#: The x caption, in the figure's markup.
X_LABEL = {TWO_THETA: "2*\\theta*  /  \\degree",
           D: "*d*  /  \\AA",
           Q: "*Q*  /  \\AA^{-1}"}
#: How the unit is written after a number, in the figure's markup, and
#: what goes between them (a degree sign sits on the number).
X_UNIT = {TWO_THETA: "\\degree", D: "\\AA", Q: "\\AA^{-1}"}
X_GLUE = {TWO_THETA: "", D: " ", Q: " "}
#: The same, as plain text (status line, CSV headers, result fields).
X_TEXT = {TWO_THETA: "deg", D: "A", Q: "1/A"}
#: The quantity's own name in words, and its symbol in markup.
X_WORDS = {TWO_THETA: "2-theta", D: "d", Q: "Q"}
X_SYMBOL = {TWO_THETA: "2*\\theta*", D: "*d*", Q: "*Q*"}

#: What a pattern needs to be drawn, when it cannot be.
MISSING_WAVELENGTH = "wavelength"
MISSING_BAND = "normalisation peak"

# ------------------------------------------------------------------ y
#: The one thing a pattern is drawn as.
UNIT_I = "I"
UNITS = (UNIT_I,)
UNIT_TITLES = {UNIT_I: "Intensity"}
AXIS_LABEL = "Intensity  /  a.u."
AXIS_LABEL_NORMALISED = "Intensity (normalised)"

#: The normalisations.
NORM_NONE = "none"
NORM_GLOBAL = "global"
NORM_RANGE = "range"
NORM_BAND = "band"
NORMS = (NORM_NONE, NORM_GLOBAL, NORM_RANGE, NORM_BAND)
NORM_TITLES = {NORM_NONE: "not normalised",
               NORM_GLOBAL: "all patterns together, 0 to 1",
               NORM_RANGE: "each pattern 0 to 1",
               NORM_BAND: "each pattern to one peak"}


def caption(_unit=UNIT_I, norm=NORM_NONE):
    """The y axis's caption, normalised or not."""
    return AXIS_LABEL if norm == NORM_NONE else AXIS_LABEL_NORMALISED


def x_caption(quantity):
    return X_LABEL.get(quantity, X_LABEL[TWO_THETA])


def to_display(values, _recorded=UNIT_I, _unit=UNIT_I):
    """`(values, None)`: intensity is drawn as it is stored."""
    if values is None:
        return None, None
    return np.asarray(values, dtype=float), None


# ------------------------------------------------------- converting x
def from_two_theta(two_theta, quantity, wavelength):
    """2-theta in degrees as `quantity`, at `wavelength` (angstrom); None
    when d or Q is asked for and there is no wavelength. A 2-theta of 0
    or less has no d (NaN)."""
    if quantity == TWO_THETA:
        return np.asarray(two_theta, dtype=float)
    if not wavelength:
        return None
    sin = np.sin(np.radians(np.asarray(two_theta, dtype=float)) / 2.0)
    if quantity == Q:
        return 4.0 * math.pi * sin / float(wavelength)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sin > 0.0, float(wavelength) / (2.0 * np.where(
            sin > 0.0, sin, 1.0)), np.nan)


def to_two_theta(values, quantity, wavelength):
    """The inverse of `from_two_theta`: NaN past the Ewald limit; None for
    d or Q without a wavelength."""
    if quantity == TWO_THETA:
        return np.asarray(values, dtype=float)
    if not wavelength:
        return None
    values = np.asarray(values, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        if quantity == Q:
            sin = values * float(wavelength) / (4.0 * math.pi)
        else:
            sin = float(wavelength) / (2.0 * values)
    sin = np.where((sin >= 0.0) & (sin <= 1.0), sin, np.nan)
    return np.degrees(2.0 * np.arcsin(sin))


def convert(values, old, new, wavelength=None):
    """Positions in `old` as `new`. d and Q convert without a wavelength
    (Q = 2 pi / d); 2-theta to or from either needs one. None when it is
    needed and missing."""
    values = np.asarray(values, dtype=float)
    if old == new:
        return values
    if {old, new} == {D, Q}:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(values > 0.0, 2.0 * math.pi / np.where(
                values > 0.0, values, 1.0), np.nan)
    two_theta = to_two_theta(values, old, wavelength)
    if two_theta is None:
        return None
    return from_two_theta(two_theta, new, wavelength)


def convert_one(value, old, new, wavelength=None):
    """`convert` for one number: a float, or None when it cannot be."""
    if value is None:
        return None
    out = convert(np.array([float(value)]), old, new, wavelength)
    if out is None or not np.isfinite(out[0]):
        return None
    return float(out[0])


def d_spacing(value, quantity, wavelength):
    """The d-spacing (angstrom) of a position on `quantity`'s axis, or
    None."""
    if quantity == D:
        return float(value)
    return convert_one(value, quantity, D, wavelength)


# --------------------------------------------------- normalising y
def normaliser(x, y, _unit, norm, band=None, extent=None):
    """`(scale, shift, missing)` that normalise `y` as `y * scale + shift`,
    or `(1, 0, None)` for none.

    Affine on purpose: the same two numbers carry a point taken off the
    curve (a peak's half height, a baseline) onto the drawn curve.
    `missing` is `MISSING_BAND` when a peak is asked for that the pattern
    does not reach (or that is not given). `extent` is what `global`
    scales by: `(low, high)` of every pattern on show."""
    if norm == NORM_GLOBAL:
        # One scale for every curve: the lowest value of any curve on show
        # is 0 and the highest 1, so their heights keep their proportions.
        if not extent:
            return 1.0, 0.0, None
        low, high = float(extent[0]), float(extent[1])
        if not high > low:
            return 1.0, 0.0, None
        return 1.0 / (high - low), -low / (high - low), None
    if norm not in (NORM_RANGE, NORM_BAND):
        return 1.0, 0.0, None
    y = np.asarray(y, dtype=float)
    finite = np.isfinite(y)
    if finite.sum() < 2:
        return 1.0, 0.0, MISSING_BAND if norm == NORM_BAND else None
    low, high = float(np.min(y[finite])), float(np.max(y[finite]))
    if norm == NORM_BAND:
        if not band or len(band) != 2:
            return 1.0, 0.0, MISSING_BAND
        lo, hi = sorted(float(b) for b in band)
        x = np.asarray(x, dtype=float)
        with np.errstate(invalid="ignore"):
            inside = finite & (x >= lo) & (x <= hi)
        if inside.sum() < 1:
            return 1.0, 0.0, MISSING_BAND
        high = float(np.max(y[inside]))
    span = high - low
    if not span > 0:
        return 1.0, 0.0, MISSING_BAND if norm == NORM_BAND else None
    return 1.0 / span, -low / span, None


def span_of(values):
    """How tall a curve is, robustly: its 1st to 99th percentile. What an
    offset is carried across a change of normalisation by, so a stack
    keeps its proportions (`Document.set_display`)."""
    if values is None:
        return None
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return None
    low, high = np.percentile(values, [1, 99])
    span = float(high - low)
    return span if span > 0 else None


def parse_position(text):
    """A typed position - a number or a sum, a comma as the decimal point,
    a unit after it ("12.5 deg", "3.2 A", "1.1 1/A") or not - or None."""
    from . import numbers
    match = _TYPED_POSITION.match(str(text or ""))
    return numbers.evaluate(match.group(1)) if match else None


_TYPED_POSITION = re.compile(
    r"^\s*(.*?)\s*(?:\u00b0|deg(?:rees?)?|\\degree|\u00c5\s*\^?\s*\{?\s*-\s*1"
    r"\s*\}?|\u00c5\u207b\u00b9|1\s*/\s*\u00c5|1\s*/\s*A|A\s*-\s*1|\\AA\^\{-1\}|"
    r"\u00c5|\\AA|A|angstrom)?\s*$", re.I)
