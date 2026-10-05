"""What the window is looking at: samples, scans, and the drawn objects.

UI-free. Everything the plot draws is an OBJECT with properties, because that
is what makes the Blender-style handling possible: a selection is a set of
objects, a transform writes a property, the outliner lists them, the F3
operators act on whichever ones are selected, and the undo stack records the
property that changed. A pattern that is "just an array the plot happens to
hold" can be none of those things.

The kinds:

* `Sample` - not drawn. The FILE: its pattern as read (or simulated from a
  CIF or a card), its wavelength (the file's, the user's, or none), its
  name.
* `Scan`   - the file's pattern ON THE FIGURE: a curve with a place in the
  stack, a colour, a label. One per file (the name is the family's: a DSC
  file holds several).
* `Analysis` - a peak position, a peak area or a width measured on a scan.
* Artists, drawn on the figure and not measured: labels (and notes and
  marker lines), the legend, pictures, structures, `Region` (a highlighted
  or magnified stretch of the x axis) and `SpanArrow` (a double arrow
  between two positions, labelled with the distance).

`Document` owns them, and owns the choices that apply to everything at once:
what the x axis shows (2-theta, d or Q), whether the y axis is normalised,
and whether the x axis is broken.

**Every x position on the figure is in the axis's CURRENT quantity** - a
region's ends, a marker line, a distance arrow, an artist pinned to the
data, the break, the normalisation peak. Changing the quantity converts all
of them in one undo step (`Document.set_x_quantity`), through the figure's
wavelength, and measures every analysis again on the new axis.
"""

import os
import re

import numpy as np

from . import crystal
from . import figure as figure_module
from . import style
from . import units

#: Trace colours, in the order scans are added. Chosen to stay apart on a dark
#: ground and to survive being printed in grey.
PALETTE = ("#6ea8ff", "#ffb04e", "#7fd08a", "#e07b7b", "#c79bef",
           "#4fd0c8", "#d8d16a", "#f08ac0")

#: What a framing or a lock was measured in is recorded as `[axis, unit]`
#: (`PlotWidget.axis_context`): the axis's quantity in words and its unit.
X_AXIS_NAMES = {units.TWO_THETA: "2-theta", units.D: "d",
                units.Q: "Q"}

_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


def number(value):
    """The number inside a result field ("12.345 deg"), or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


class Obj(object):
    """Anything the window can select, hide, drag or right-click."""

    kind = "object"
    #: The object whose colour this one FOLLOWS, or None (`sync_colours`).
    colour_from = None

    def __init__(self, oid, name=""):
        self.id = int(oid)
        self.name = str(name)
        #: Drawn or not. An undoable property, so hiding is a step.
        self.visible = True
        #: NOT undoable and NOT saved: a selection is where the hands are,
        #: not a decision about the figure.
        self.selected = False
        #: Where it is drawn in the stack of the figure, or None for its
        #: kind's place (`z_of`): higher is on top.
        self.z = None

    def __repr__(self):
        return "{}({!r})".format(type(self).__name__, self.name)


#: The drawing order of each kind while nobody has chosen one: highlighted
#: regions under everything, curves, then what is drawn on them, then the
#: figure's furniture.
KIND_Z = {"region": -10.0, "scan": 0.0, "analysis": 10.0,
          "offset_marker": 20.0, "span": 35.0,
          "legend": 40.0, "image": 45.0, "molecule": 46.0,
          "label": 50.0}


def z_of(obj):
    """Where `obj` is drawn in the stack: its own z, or its kind's."""
    own = getattr(obj, "z", None)
    return float(own) if own is not None else KIND_Z.get(
        getattr(obj, "kind", ""), 0.0)


class Sample(object):
    """One file: the pattern as read, and its wavelength.

    `wavelength_override` is None until somebody gives a wavelength; then
    it wins over the file. Where neither the file nor the user has said,
    there is NONE (`wavelength` is None) - never a guess - and the pattern
    is drawn in 2-theta only, the figure saying so where d or Q would need
    it.

    A SIMULATED sample (a CIF, a card) is made into a pattern at its
    wavelength - the figure's when it was opened (the loader sets
    `wavelength_override`), or the user's - over `sim_range` (2-theta),
    with peaks `sim_fwhm` wide; `x` and `y` are that pattern, computed when
    first asked and again whenever one of the three changes.
    """

    def __init__(self, path, pattern):
        self.path = str(path)
        self.pattern = pattern
        #: The file's own name, without the extension: what the outliner
        #: and a scan's default label say.
        self.file_name = os.path.splitext(os.path.basename(path))[0]
        #: The name the user gave it (F2 in the outliner), or None for the
        #: file's own. Its curve's name, the legend and exports follow it.
        self.title = None
        #: The wavelength the user gives (angstrom, K-alpha1), or None.
        self.wavelength_override = None
        #: Anything the reader had to say about the file.
        self.note = ""
        #: A simulation's 2-theta range and peak width (`core/crystal.py`).
        self.sim_range = None
        self.sim_fwhm = crystal.DEFAULT_FWHM
        self._sim_key = None
        self._sim = None
        self._list_cache = None
        self.scans = []

    @property
    def name(self):
        return self.title or self.file_name

    @property
    def simulated(self):
        return bool(getattr(self.pattern, "simulated", False))

    @property
    def spectrum(self):
        """The family's name for what was read."""
        return self.pattern

    # ------------------------------------------------------- simulation
    def simulation(self):
        """`(x, y, reflections, note)` of a simulated sample at its
        wavelength, range and width (`crystal.simulate`), cached."""
        if not self.simulated:
            return None
        wavelength = self.wavelength or crystal.DEFAULT_WAVELENGTH
        rng = tuple(self.sim_range or crystal.DEFAULT_RANGE)
        key = (float(wavelength), rng, float(self.sim_fwhm))
        if self._sim_key != key:
            self._sim = crystal.simulate(self.pattern, wavelength, rng,
                                         self.sim_fwhm)
            self._sim_key = key
        return self._sim

    def reflections(self):
        """A simulated sample's reflections at its wavelength, strongest
        first; [] for a measured one."""
        made = self.simulation()
        return list(made[2]) if made else []

    def reflection_list(self, absent=False):
        """`(reflections, note)`: a simulated sample's hkl list at its
        wavelength and range, ascending (`crystal.reflection_list`; with
        `absent`, a CIF's absences too), cached like the simulation."""
        if not self.simulated:
            return [], ""
        wavelength = self.wavelength or crystal.DEFAULT_WAVELENGTH
        rng = tuple(self.sim_range or crystal.DEFAULT_RANGE)
        key = (float(wavelength), rng, bool(absent))
        cached = self._list_cache
        if cached is None or cached[0] != key:
            cached = (key, crystal.reflection_list(self.pattern, wavelength,
                                                   rng, absent))
            self._list_cache = cached
        return cached[1]

    # ------------------------------------------------------------- data
    @property
    def x(self):
        """2-theta in degrees."""
        if self.simulated:
            return self.simulation()[0]
        return self.pattern.x

    @property
    def y(self):
        if self.simulated:
            return self.simulation()[1]
        return self.pattern.y

    @property
    def head(self):
        return self.pattern.head

    def x_as(self, quantity):
        """The angles as `quantity` (`units.TWO_THETA`, `D`, `Q`), or None
        when that needs a wavelength this sample does not have."""
        return units.from_two_theta(self.x, quantity, self.wavelength)

    # ------------------------------------------------------- wavelength
    @property
    def file_wavelength(self):
        return self.pattern.wavelength

    @property
    def wavelength(self):
        """K-alpha1 in angstrom: the user's word, else the file's; None
        when neither has said."""
        if self.wavelength_override:
            return float(self.wavelength_override)
        return self.file_wavelength

    @property
    def wavelength_source(self):
        """Where `wavelength` came from: "set by hand", "the file",
        "simulated at", or "not stated"."""
        if self.simulated:
            return "simulated at"
        if self.wavelength_override:
            return "set by hand"
        if self.file_wavelength:
            return "the file"
        return "not stated"

    @property
    def wavelength_missing(self):
        return self.wavelength is None

    def wavelength_text(self):
        """"1.5406 A (Cu K-alpha1), the file" - the wavelength and where it
        came from, or that there is none."""
        if self.wavelength is None:
            return "wavelength not stated in the file"
        if self.simulated:
            return "simulated at " + crystal.describe_wavelength(
                self.wavelength)
        return "{}, {}".format(crystal.describe_wavelength(self.wavelength),
                               self.wavelength_source)

    # The family's names for what the file says its values are: here,
    # the wavelength is the thing that may be missing.
    unit_guessed = False

    def unit_text(self):
        return self.wavelength_text()


class Scan(Obj):
    """A file's pattern on the figure: a curve with a place in the stack."""

    kind = "scan"

    def __init__(self, oid, sample, colour, doc=None):
        Obj.__init__(self, oid, "")
        self.sample = sample
        #: The figure it is on: what its x is drawn as (`x_values`).
        self.doc = doc
        self.colour = str(colour)
        #: Vertical placement, in the unit the y axis is currently showing.
        #: Continuous, moved with G or a typed number - never a slot.
        self.offset = 0.0
        #: None follows the house style (`core/style.py`); read it through
        #: `style.value`, never directly.
        self.line_width = None
        #: Which part of the pattern is DRAWN, as fractions of its samples
        #: counted from the low-angle end. The hidden ends are left out of
        #: the fit, the picking, arranging, exports and analyses, and are
        #: drawn dashed only on hover.
        self.keep = (0.0, 1.0)
        #: None means "the file's name". A typed one wins.
        self.label = None
        #: How a SIMULATED pattern is drawn (`crystal.DRAWS`): its profile,
        #: sticks, a tick row, or lines across the plot. A measured one is
        #: always a curve.
        self.draw_as = crystal.DRAW_CURVE
        #: How many reflections sticks, ticks and lines show, the strongest
        #: first; None: all (lines: `crystal.DEFAULT_STRONGEST`).
        self.strongest = None
        #: How many times taller than measured (after normalisation) the
        #: pattern is drawn: the swipe on a selection, its settings, F3.
        #: Its "xN" label says so (`TextLabel.shows`); the measurements
        #: never see it.
        self.multiplier = 1.0
        #: The analyses measured on this pattern.
        self._analyses = []
        #: Its y-offset marker, drawn while the figure's markers are on.
        self.marker = OffsetMarker(oid, self)
        self._cache_key = None
        self._cache = None

    # ------------------------------------------------------------- identity
    #: The curve's place in its file (a file holds one pattern).
    seg = 0

    def display_name(self):
        """What the legend and a label beside the curve say."""
        if self.label:
            return str(self.label)
        return self.sample.name

    @property
    def drawing(self):
        """How it is drawn: `crystal.DRAWS`, always a curve when measured."""
        if not self.sample.simulated:
            return crystal.DRAW_CURVE
        return (self.draw_as if self.draw_as in crystal.DRAWS
                else crystal.DRAW_CURVE)

    @property
    def draws_lines(self):
        """True for lines across the plot: no curve, no height, no place
        in the stack."""
        return self.drawing == crystal.DRAW_LINES

    # ----------------------------------------------------------------- data
    def quantity(self, doc=None):
        doc = doc if doc is not None else self.doc
        return getattr(doc, "x_quantity", units.TWO_THETA)

    def own_curve(self):
        """`(two_theta, y)` as stored or simulated, before any conversion:
        a simulated pattern's sticks, ticks or lines included."""
        drawing = self.drawing
        if drawing == crystal.DRAW_CURVE:
            return self.sample.x, self.sample.y
        reflections = self.sample.reflections()
        if drawing == crystal.DRAW_LINES:
            return crystal.lines(reflections, self.strongest)
        return crystal.sticks(reflections, drawing == crystal.DRAW_TICKS,
                              self.strongest)

    def drawn_reflections(self):
        """The reflections this scan DRAWS - all of them as a profile, the
        strongest N as sticks, ticks or lines - strongest first; [] for a
        measured pattern."""
        reflections = self.sample.reflections()
        drawing = self.drawing
        if drawing == crystal.DRAW_CURVE:
            return reflections
        count = self.strongest
        if drawing == crystal.DRAW_LINES and count is None:
            count = crystal.DEFAULT_STRONGEST
        return reflections[:int(count)] if count else reflections

    def x_values(self, doc=None, quantity=None):
        """The samples' positions on the figure's x axis (2-theta, d or Q),
        or on `quantity`'s; None when that needs a wavelength the sample
        does not have."""
        x, _y = self.own_curve()
        return units.from_two_theta(x, quantity or self.quantity(doc),
                                    self.sample.wavelength)

    def missing_for(self, doc):
        """What stops this pattern being drawn as `doc` shows its axes, or
        None: d or Q without a wavelength, or a normalisation peak it does
        not reach."""
        x = self.x_values(doc)
        if x is None:
            return units.MISSING_WAVELENGTH
        if self.draws_lines:
            return None
        _x, y = self.own_curve()
        return units.normaliser(x, y, doc.y_unit, doc.norm,
                                doc.norm_band, doc.global_extent())[2]

    def display_values(self, doc):
        """`(values, scale, shift)`: the pattern's intensities, and the
        normalisation that takes them to the axis (`values * scale +
        shift`), or `(None, 1, 0)` when it cannot be drawn. No offset, no
        magnification."""
        x = self.x_values(doc)
        if x is None:
            return None, 1.0, 0.0
        _x, values = self.own_curve()
        values = np.asarray(values, dtype=float)
        if self.draws_lines:
            return values, 1.0, 0.0
        scale, shift, missing = units.normaliser(
            x, values, doc.y_unit, doc.norm, doc.norm_band,
            doc.global_extent())
        if missing:
            return None, 1.0, 0.0
        # Times its multiplier, about 0 - the offset is what keeps it in
        # its place (`Document.rescaled`).
        times = float(self.multiplier or 1.0)
        return values, scale * times, shift * times

    @property
    def scaled(self):
        """True while it is drawn other than as measured (`multiplier`)."""
        return abs(float(self.multiplier or 1.0) - 1.0) > 1e-9

    def curve(self, doc):
        """`(x, y)` ready to draw: on the figure's x, normalised, magnified
        where a region asks for it, offset. `(None, None)` when it cannot
        be drawn (`missing_for`) - never a substitute."""
        if self.sample.simulated:
            self.sample.simulation()          # up to date, and so its key
        magnifiers = tuple((id(r), r.lo, r.hi, r.factor)
                           for r in doc.magnifiers_of(self))
        key = (doc.x_quantity, self.sample.wavelength, self.drawing,
               self.strongest, self.sample._sim_key, doc.norm,
               tuple(doc.norm_band or ()), doc.global_extent(),
               self.multiplier, self.offset, magnifiers)
        if self._cache_key == key:
            return self._cache
        x = self.x_values(doc)
        values, scale, shift = self.display_values(doc)
        if x is None or values is None:
            self._cache_key, self._cache = key, (None, None)
            return self._cache
        y = values * scale + shift
        for region in doc.magnifiers_of(self):
            y = magnified(x, y, region.lo, region.hi, region.factor)
        y = y + float(self.offset)
        self._cache_key, self._cache = key, (x, y)
        return self._cache

    def on_axes(self, doc, xs, values):
        """Points `(xs, values)` - values as stored, not normalised, as an
        analysis measures them - where the curve draws them: normalised,
        magnified (through the curve's own chord), offset. `(None, None)`
        when the pattern cannot be drawn."""
        own, scale, shift = self.display_values(doc)
        x = self.x_values(doc)
        if own is None or x is None:
            return None, None
        xs = np.asarray(xs, dtype=float)
        ys = np.asarray(values, dtype=float) * scale + shift
        curve = own * scale + shift
        for region in doc.magnifiers_of(self):
            ys = magnified_points(x, curve, xs, ys, region.lo, region.hi,
                                  region.factor)
            curve = magnified(x, curve, region.lo, region.hi, region.factor)
        return xs, ys + float(self.offset)

    def factor(self, doc_key):
        """How tall this pattern is as `doc_key` = (unit, norm, band) draws
        it - what an offset converts by when that changes."""
        _unit, norm, band = doc_key
        x = self.x_values()
        if x is None or self.draws_lines:
            return None
        _x, values = self.own_curve()
        values = np.asarray(values, dtype=float)
        scale, shift, missing = units.normaliser(
            x, values, units.UNIT_I, norm, band,
            self.doc.norm_extent() if norm == units.NORM_GLOBAL
            and self.doc is not None else None)
        if missing:
            return None
        return units.span_of(values * scale + shift)

    def intensity(self):
        """The intensities as stored - what positions, areas and widths are
        measured on, whatever the axis shows."""
        _x, values = self.own_curve()
        return np.asarray(values, dtype=float)

    def kept_range(self, count):
        """`(k0, k1)`: the slice of `count` samples that is drawn; at least
        two are always kept."""
        start, end = self.keep
        k0, k1 = sorted([int(count * float(start)), int(count * float(end))])
        k0 = max(0, min(k0, count))
        k1 = max(min(count, k0 + 2), min(k1, count))
        return k0, k1

    def is_truncated(self):
        return tuple(self.keep) != (0.0, 1.0)

    def kept_curve(self, doc):
        """`curve` without the hidden ends: what is drawn, fitted, arranged
        and exported."""
        x, y = self.curve(doc)
        if x is None:
            return x, y
        k0, k1 = self.kept_range(len(x))
        return x[k0:k1], y[k0:k1]

    def baseline_y(self):
        """Where this scan's zero sits: its offset."""
        return float(self.offset)

    @property
    def analysis_objects(self):
        return self._analyses

    def visible_analyses(self):
        return [a for a in self._analyses if a.visible]


def magnified(x, y, lo, hi, factor):
    """`y` with the stretch `lo <= x <= hi` magnified `factor` times about
    the straight line between the curve's own values at the stretch's two
    ends - its local baseline. The curve stays joined at both ends, and the
    peaks grow up from it, whatever the offset."""
    factor = float(factor)
    if factor == 1.0:
        return y
    x = np.asarray(x, dtype=float)
    y = np.array(y, dtype=float)
    low, high = sorted((float(lo), float(hi)))
    inside = np.flatnonzero((x >= low) & (x <= high) & np.isfinite(y))
    if len(inside) < 2:
        return y
    a, b = inside[0], inside[-1]
    base = _chord_at(x[a], y[a], x[b], y[b], x[inside])
    y[inside] = base + (y[inside] - base) * factor
    return y


def magnified_points(x, curve, xs, ys, lo, hi, factor):
    """`magnified` for loose points `(xs, ys)` on or near `curve`: those
    inside the stretch move as the curve's samples there do."""
    factor = float(factor)
    ys = np.array(ys, dtype=float)
    if factor == 1.0:
        return ys
    x = np.asarray(x, dtype=float)
    low, high = sorted((float(lo), float(hi)))
    inside = np.flatnonzero((x >= low) & (x <= high) & np.isfinite(curve))
    if len(inside) < 2:
        return ys
    a, b = inside[0], inside[-1]
    xs = np.asarray(xs, dtype=float)
    within = (xs >= x[a]) & (xs <= x[b])
    base = _chord_at(x[a], curve[a], x[b], curve[b], xs)
    ys[within] = base[within] + (ys[within] - base[within]) * factor
    return ys


def _chord_at(x0, y0, x1, y1, xs):
    span = float(x1 - x0)
    if abs(span) < 1e-12:
        return np.full(len(xs), float(y0))
    return float(y0) + (float(y1) - float(y0)) * (np.asarray(xs) - x0) / span


class Analysis(Obj):
    """One measurement on a scan, as an object that can be shown, hidden,
    moved and edited: a peak position, a peak area, a width.

    Made HERE, always (a pattern file carries no analyses), from an
    interval of the curve - two sample indices, `span` - or two typed
    positions. Its result fields are text with their unit, as they are
    listed (`labels.results`); what its label says is a template
    (`core/labels.py`) whose `{}` is the measurement.
    """

    kind = "analysis"
    #: Made here, so certain; kept for the handling the family shares.
    certain = True
    source = "panel"

    def __init__(self, oid, scan, model_name, fields):
        Obj.__init__(self, oid, model_name)
        self.scan = scan
        self.model_name = str(model_name)
        self.fields = dict(fields or {})
        self.visible = True
        #: "auto" follows the scan's colour.
        self.colour = "auto"
        #: The label's TEMPLATE, or None for the default one of its kind:
        #: the user's words, with `{}` where the measured value goes.
        self.label = None
        #: The two SAMPLE INDICES the interval runs between, or None when
        #: it was typed as positions.
        self.span = None
        #: How far from the curve the label sits, in figure units, or None
        #: for "beyond the tip of the peak".
        self.label_dy = None
        #: Shade a peak area, between the curve and its baseline.
        self.shade = True
        #: `style.SHADINGS`; None follows the house style.
        self.shading = None
        #: Half the length of its interval's dashes; None: the house style.
        self.interval_size = None
        #: The dashes at the two ends of the interval.
        self.show_interval = True
        #: The unit its number is shown in, or None for the natural one.
        self.unit = None
        #: Point size for the label, or None for the house style's.
        self.label_size = None
        #: Which edge of the label sits on its arrow, or None.
        self.flush = None
        #: How its number is written, or None for the house style's.
        self.number_format = None
        #: For a peak area, WHERE along its interval the label's arrow
        #: meets the curve (on the x axis), or None for its strongest point.
        self.label_at = None
        #: The x quantity its cursors and results are in (`units`): the
        #: axis's when it was measured, carried along when that changes.
        self.axis = None

    @property
    def marks_a_point(self):
        return False

    def value(self):
        """The position this analysis is drawn at, or None."""
        return number(self.fields.get("Position"))

    @property
    def slides(self):
        """True for a kind whose label may slide along its interval: a peak
        area, which labels an area rather than a point."""
        return "area" in self.model_name.lower()

    @property
    def quantity(self):
        from . import labels
        return labels.quantity_of(self.model_name)

    def summary(self, doc=None):
        """The label as drawn: its template with the value filled in."""
        from . import labels
        return labels.render(self, doc).text

    def cursors(self):
        """The two positions it was measured between, low first."""
        low = number(self.fields.get("Cursor x"))
        high = number(self.fields.get("Cursor x1"))
        if low is None or high is None:
            return []
        return [low, high]

    def key(self):
        """A stable identity: the model and its cursors."""
        return "|".join([self.model_name] + ["{:.4f}".format(c)
                                             for c in self.cursors()])


#: Where an artist's position is measured in.
SPACE_RELATIVE = "relative"      # fractions of the plot, 0..1
SPACE_DATA = "data"              # the axes' own units

#: The nine points of an artist that can sit on its position.
ANCHORS = ("top left", "top", "top right",
           "left", "center", "right",
           "bottom left", "bottom", "bottom right")


class Artist(Obj):
    """Anything drawn on the figure that is not data.

    What they have in common lives here:

    * a POSITION, in one of two spaces. `relative` is a fraction of the plot,
      which keeps an artist in the same corner whatever the view does;
      `data` pins it to a position and a height, which is what a note
      about a peak wants. The settings offer both and convert between them,
      so switching does not move anything.
    * an ANCHOR: which of the artist's own nine points sits on that position.
    * a COLOUR, "auto" meaning the theme's ink.

    What each KIND allows beyond that is a class flag: `can_rotate` and
    `can_scale`. The dialogs read them.
    """

    kind = "artist"
    can_rotate = False
    can_scale = False

    def __init__(self, oid, name="", x=0.5, y=0.5):
        Obj.__init__(self, oid, name)
        self.x = float(x)
        self.y = float(y)
        self.space = SPACE_RELATIVE
        self.anchor = "center"
        self.colour = "auto"
        #: Degrees, counter-clockwise, about the anchor point; only for a
        #: kind that `can_rotate` (R).
        self.rotation = 0.0

    def position(self):
        return (float(self.x), float(self.y))

    def set_position(self, x, y):
        self.x, self.y = float(x), float(y)
        return self

    def anchor_offsets(self):
        """`(fx, fy)` in 0..1: which point of the artist sits on the position.

        0 is left/top and 1 is right/bottom, so a box of width w and height h
        is drawn at `x - fx * w`, `y - fy * h`.
        """
        anchor = self.anchor if self.anchor in ANCHORS else "center"
        fx = 0.5
        fy = 0.5
        if "left" in anchor:
            fx = 0.0
        elif "right" in anchor:
            fx = 1.0
        if "top" in anchor:
            fy = 0.0
        elif "bottom" in anchor:
            fy = 1.0
        return fx, fy


class Axis(Obj):
    """An axis, as an object with its own settings.

    The defaults are a stacked figure's: ticks pointing IN, minor ticks
    between them, the frame closed with ticks on the far side, no grid; on
    the y axis of a stack of patterns, no numbers and no ticks.
    """

    kind = "axis"

    def __init__(self, oid, which):
        Obj.__init__(self, oid, "{} axis".format(which.upper()))
        self.which = which               # "x" or "y"
        #: A LOCKED range ("Lock current framing"): `[low, high]` that F
        #: and an unframed view return to instead of the fit, or None, kept
        #: with what it was measured in (`lock_context`).
        self.lock = None
        self.lock_context = None
        #: None means "say what is on this axis", which follows the unit.
        self.label = None
        self.show_grid = False
        self.minor_ticks = True
        self.ticks_inward = True
        #: Which side of the axes box this axis is drawn on.
        self.side = "bottom" if which == "x" else "left"
        #: The numbers can be hidden - a stack of offset patterns shows no y
        #: numbers at all. The caption is hidden with `visible`.
        self.show_numbers = which == "x"
        #: The ticks, numbered and minor, can be hidden too (the y axis of
        #: a stack has none: `ax.set_yticks(())`).
        self.show_ticks = which == "x"
        #: Both None until chosen: the house style decides.
        self.label_size = None
        self.tick_size = None
        #: Where the caption sits ALONG the axis, as a fraction, and how far
        #: from its numbers (None: the house style's `caption_gap`).
        self.label_along = 0.5
        self.label_gap = None
        #: How its numbers are written, or None for "as few digits as the
        #: tick spacing needs".
        self.number_format = None
        #: Numbers NOT written, as values in the axis's unit (their ticks
        #: stay), kept with what they were chosen in (`hidden_context`).
        #: Always REPLACED, never changed in place.
        self.hidden_numbers = []
        self.hidden_context = None
        #: A line on the OPPOSITE side of the axes box, closing the frame,
        #: and ticks on it (no numbers): the default.
        self.mirror = True
        self.mirror_ticks = True
        #: The numbered ticks' spacing in the axis's unit, or None for a
        #: round number that fits.
        self.major_step = None
        #: Minor intervals per major one; 1 is none.
        self.minor_count = 5
        #: Tick lengths, in figure units (96 per inch).
        self.tick_length = 7.0
        self.minor_length = 3.0

    def caption(self, doc):
        """What the caption says: the user's text, or the axis's own - the
        x quantity, or the y quantity, "normalised" when it is."""
        if self.label:
            return str(self.label)
        if self.which == "x":
            return units.x_caption(getattr(doc, "x_quantity",
                                           units.TWO_THETA))
        return units.caption(doc.y_unit, doc.norm)


class TextLabel(Artist):
    """A caption the user put on the figure, and can move and retype.

    Three shapes of one object: a plain label, a NOTE (with an arrow to a
    point, `leader`) and a MARKER LINE (a dashed vertical line across the
    axes at a position, `vline`, with its text turned upright on it). A `{}`
    in a marker line's text is its position.
    """

    kind = "label"
    can_scale = True
    can_rotate = True

    def __init__(self, oid, text="Label", x=0.5, y=0.5, scan=None):
        Artist.__init__(self, oid, "Label", x, y)
        self.text = str(text)
        #: None follows the house style.
        self.size = None
        self.bold = False
        #: The scan this label belongs to - its PARENT - or None for a free
        #: one. An owned label takes that scan's colour while its own is
        #: "auto", is listed under it in the outliner, goes when the scan
        #: goes, and moves with it.
        self.scan = scan
        #: The scan's offset when the label's position was last set, in the
        #: axis unit (Blender's "keep transform" for parenting).
        self.parent_offset = (float(scan.offset) if scan is not None
                              else None)
        #: A NOTE's arrow: `[x, y]`, the point it points at, or None
        #: for a plain label.
        self.leader = None
        #: Where on the text's box the arrow starts: "auto" or an anchor.
        self.leader_from = "auto"
        #: The arrow's own colour, or "auto" for the text's.
        self.leader_colour = "auto"
        #: How its lines line up: "left", "right", "center", or None.
        self.flush = None
        #: A MARKER LINE: the position of a vertical line across the axes
        #: that this label sits on, turned upright on a background box - or
        #: None for an ordinary label. Its `y` is its place along the line.
        self.vline = None
        #: The line dashed (`ls='--'`) or solid.
        self.line_dashed = True
        #: A label that belongs to a scan HANGS FROM ITS CURVE: `at` is the
        #: sample, `("i", n)`, and `dx`, `dy` are figure units from that
        #: point to the label's anchor (up is negative). None until attached.
        self.at = None
        self.dx = 0.0
        self.dy = None
        #: What its `{}` says besides the user's words: None, or
        #: "multiplier" - its scan's `multiplier` ("x15"), the label that
        #: comes with a scaled pattern (`Document.new_multiplier_label`).
        self.shows = None

    @property
    def is_vline(self):
        return self.vline is not None

    @property
    def attached(self):
        """True when it hangs from its scan's curve (`at`)."""
        return (self.scan is not None and self.vline is None
                and self.at is not None)

    def follow(self):
        """How far its scan has moved since the label was placed, in the
        axis unit: 0.0 for a free label and for one hanging from its curve
        (the curve carries it)."""
        if (self.scan is None or self.parent_offset is None
                or self.at is not None):
            return 0.0
        return float(self.scan.offset) - float(self.parent_offset)


class Legend(Artist):
    """Which colour is which pattern, in a corner of the figure. Off by
    default: a stack labelled at its edge needs none."""

    kind = "legend"
    can_scale = True
    can_rotate = True

    def __init__(self, oid):
        Artist.__init__(self, oid, "Legend", 0.02, 0.98)
        self.anchor = "bottom left"
        self.visible = False
        #: None follows the house style.
        self.size = None
        self.show_frame = False
        #: Length of the colour sample in front of each name, in pixels.
        self.sample = 22.0
        #: Space between rows, as a multiple of the line height.
        self.spacing = 1.25
        #: The colour samples' line width, or None for each scan's own.
        self.line_width = None

    def entries(self, doc):
        """`[(scan, text), ...]` for the patterns that are drawn."""
        return [(scan, scan.display_name()) for scan in doc.visible_scans()
                if not scan.missing_for(doc)]


class OffsetMarker(Obj):
    """A scan's offset, written beside it, as an object:
    `+0.5` under the curve with a small arrow up to it."""

    kind = "offset_marker"

    def __init__(self, oid, scan):
        Obj.__init__(self, oid, "Offset marker")
        self.scan = scan
        #: Where it points on the curve. None: the left end of the part of
        #: the curve that is SHOWN. `("i", n)`: sample n. `("x", value)`:
        #: the shown sample nearest that position (typed).
        self.at = None
        #: How far below the curve the text starts, in figure units, or None
        #: for 3 % of the plot height.
        self.dy = None
        self.size = None
        self.colour = "auto"
        self.number_format = None


class ImageArtist(Artist):
    """A picture on the figure, pasted or dropped."""

    kind = "image"
    can_scale = True
    can_rotate = True

    def __init__(self, oid, png, x=0.5, y=0.5, width=160.0):
        Artist.__init__(self, oid, "Image", x, y)
        #: The picture as PNG, base64 text: what the session stores.
        self.png = str(png)
        self.mirror_h = False
        self.mirror_v = False
        #: How wide it is drawn, in figure units.
        self.width = float(width)
        self._pixels = None


class MoleculeArtist(Artist):
    """A skeletal structure, from a pasted SMILES, drawn from the layout
    `core/chem.py` made (stored, so a figure opens without RDKit)."""

    kind = "molecule"
    can_scale = True
    can_rotate = True

    def __init__(self, oid, smiles, drawing, x=0.5, y=0.5):
        Artist.__init__(self, oid, "Structure", x, y)
        self.smiles = str(smiles)
        self.atoms = list((drawing or {}).get("atoms", []))
        self.bonds = list((drawing or {}).get("bonds", []))
        self.bond_length = 19.2
        self.bond_width = 0.8
        self.label_size = 10.0
        self.upright_labels = True
        self.label_font = None
        self.colour_by_element = True


class Region(Artist):
    """A stretch of the x axis, HIGHLIGHTED and/or MAGNIFIED.

    Highlighting and magnifying as one object: `lo` to `hi`
    (on the x axis) shaded across the whole axes box when `shade` is on,
    and every
    scan in `scans` magnified `factor` times there (`magnified`: about the
    curve's own chord across the stretch, so it stays joined). Its text
    stands over the middle of the stretch, `y` of the axes box from the top;
    a magnifying region's default text is its factor, "x3".
    """

    kind = "region"
    can_scale = True
    can_rotate = True

    def __init__(self, oid, lo, hi, x=0.5, y=0.05):
        Artist.__init__(self, oid, "Region", x, y)
        self.lo, self.hi = sorted((float(lo), float(hi)))
        self.shade = True
        #: How strongly the shading covers the page (0 to 1).
        self.opacity = 0.15
        #: How many times the patterns in `scans` are magnified; 1 is none.
        self.factor = 1.0
        self.scans = []
        #: Its words, or "" for none - or, magnifying, for its factor.
        self.text = ""
        self.size = None
        self.anchor = "top"

    @property
    def magnifies(self):
        return float(self.factor) != 1.0 and bool(self.scans)

    def shown_text(self):
        """What its text says: its own, else "x3" while it magnifies."""
        if self.text:
            return str(self.text)
        if self.magnifies:
            return "\\times{:g}".format(float(self.factor))
        return ""


class SpanArrow(Artist):
    """A double arrow between two positions, labelled with the distance
    between them (the shift of a peak between two phases, say).

    Its ends are numbers (`x0`, `x1`, on the x axis) or marker lines
    (`ends`): an end tied to a marker line stands wherever it is moved. Its
    height is `y` of the axes box from the top; its text a template whose
    `{}` is the distance, above or below the arrow or turned upright ON it
    (`place`).
    """

    kind = "span"
    can_scale = True
    can_rotate = False

    PLACES = ("above", "below", "on")

    def __init__(self, oid, x0, x1, y=0.3):
        Artist.__init__(self, oid, "Span", 0.5, y)
        self.x0 = float(x0)
        self.x1 = float(x1)
        #: The marker lines the two ends stand on, or None each.
        self.ends = [None, None]
        #: None: "Delta" and the axis's symbol (`labels.span_text`).
        self.text = None
        self.place = "above"
        self.size = None
        #: The arrowheads' size, in figure units.
        self.head = 9.0
        self.line_width = 1.0
        #: How its number is written, or None for the house style's.
        self.number_format = None

    def end_values(self):
        """`(x0, x1)` where the arrow is drawn: a tied end at its marker."""
        out = []
        for end, own in zip(self.ends, (self.x0, self.x1)):
            if end is not None and getattr(end, "vline", None) is not None:
                out.append(float(end.vline))
            else:
                out.append(float(own))
        return tuple(out)

    def distance(self):
        a, b = self.end_values()
        return abs(b - a)


# ------------------------------------------------------------------ colours
# A colour can FOLLOW another object's ("Inherit" beside every colour in a
# settings window): `colour_from` is the donor, and `sync_colours` copies
# its colour across whenever the figure is refreshed - so the two always
# match, through undo too. Choosing a colour of one's own ends it.

def own_colour(obj):
    """The colour `obj` is drawn in as far as the document knows: its own,
    or for "auto" its parent's (a label's or an analysis's curve); None
    where "auto" means the theme's ink."""
    seen = set()
    while obj is not None and id(obj) not in seen:
        seen.add(id(obj))
        colour = getattr(obj, "colour", None)
        if colour not in (None, "", "auto"):
            return str(colour)
        obj = getattr(obj, "scan", None)
    return None


def inherits_from(obj, other):
    """True when `obj` takes its colour from `other` at any remove, or IS
    it: what would close a circle of donors."""
    seen = set()
    while obj is not None and id(obj) not in seen:
        if obj is other:
            return True
        seen.add(id(obj))
        obj = getattr(obj, "colour_from", None)
    return False


def colour_objects(doc):
    """Everything in `doc` with a colour of its own."""
    found = [o for o in doc.objects() if hasattr(o, "colour")]
    seen = set(id(o) for o in found)
    for scan in doc.scans:
        marker = getattr(scan, "marker", None)
        if (marker is not None and id(marker) not in seen
                and hasattr(marker, "colour")):
            found.append(marker)
    return found


def sync_colours(doc):
    """Every follower takes its donor's colour, along chains (one pass per
    link of the longest). A donor no longer in the document leaves its
    followers as they are, still linked, so an undo that brings it back
    brings the link back. True when anything changed."""
    objs = colour_objects(doc)
    present = set(id(o) for o in objs)
    changed = False
    for _ in range(max(1, len(objs))):
        moved = False
        for obj in objs:
            donor = getattr(obj, "colour_from", None)
            if donor is None or id(donor) not in present:
                continue
            colour = own_colour(donor)
            if colour is not None and colour != obj.colour:
                obj.colour = colour
                moved = changed = True
        if not moved:
            break
    return changed


#: The lists a session names a follower or a donor in, by position.
_COLOUR_LISTS = ("scans", "labels", "regions", "spans", "images",
                 "structures")


def _colour_ref(doc, obj):
    for kind in _COLOUR_LISTS:
        for index, item in enumerate(getattr(doc, kind, None) or ()):
            if item is obj:
                return [kind, index]
    for index, item in enumerate(doc.analyses()):
        if item is obj:
            return ["analyses", index]
    for index, scan in enumerate(doc.scans):
        if getattr(scan, "marker", None) is obj:
            return ["markers", index]
    for kind in ("legend", "arrow"):
        if getattr(doc, kind, None) is obj:
            return [kind, 0]
    for name, axis in doc.axes.items():
        if axis is obj:
            return ["axes", name]
    return None


def _colour_target(doc, ref):
    try:
        kind, index = ref
        if kind in _COLOUR_LISTS:
            return getattr(doc, kind)[int(index)]
        if kind == "analyses":
            return doc.analyses()[int(index)]
        if kind == "markers":
            return doc.scans[int(index)].marker
        if kind in ("legend", "arrow"):
            return getattr(doc, kind)
        if kind == "axes":
            return doc.axes[index]
    except (TypeError, ValueError, IndexError, KeyError, AttributeError):
        return None
    return None


def colour_links(doc):
    """`[[follower, donor], ...]` as a session keeps them: each a
    `[list, position]`."""
    links = []
    for obj in colour_objects(doc):
        donor = getattr(obj, "colour_from", None)
        if donor is None:
            continue
        follower, giver = _colour_ref(doc, obj), _colour_ref(doc, donor)
        if follower is not None and giver is not None:
            links.append([follower, giver])
    return links


def restore_colour_links(doc, links):
    """The links of a session, where both ends are still there."""
    for pair in links or ():
        try:
            first, second = pair
        except (TypeError, ValueError):
            continue
        follower = _colour_target(doc, first)
        donor = _colour_target(doc, second)
        if (follower is None or donor is None
                or inherits_from(donor, follower)):
            continue
        follower.colour_from = donor


class Document(object):
    """The samples, the objects, and the choices that apply to all of it."""

    def __init__(self):
        self.samples = []
        self.scans = []
        #: The key, off until it is asked for.
        self.legend = Legend(self._next_id())
        #: The two axes, as objects with their own settings.
        self.axes = {"x": Axis(self._next_id(), "x"),
                     "y": Axis(self._next_id(), "y")}
        #: Captions, notes and marker lines.
        self.labels = []
        #: Pictures pasted or dropped onto the figure.
        self.images = []
        #: Skeletal structures pasted as SMILES.
        self.structures = []
        #: Highlighted and magnified stretches (`Region`).
        self.regions = []
        #: Double arrows between two positions (`SpanArrow`).
        self.spans = []
        #: What the x axis shows: 2-theta, d or Q (`units.QUANTITIES`).
        self.x_quantity = units.TWO_THETA
        #: What the y axis shows: intensity, the one quantity.
        self.y_unit = units.UNIT_I
        #: Normalised or not (`units.NORMS`), and the peak that is the
        #: yardstick for `units.NORM_BAND`: `[low, high]` on the x axis.
        #: Not normalised until somebody says so.
        self.norm = units.NORM_NONE
        self.norm_band = None
        #: The x axis BROKEN: `{"lo", "hi", "compress", "gap"}` - the stretch
        #: `lo` to `hi` (x axis) squeezed to `compress` of its width and the
        #: seam marked by a gap of `gap` points in the frame - or None.
        #: Always REPLACED, never changed in place (undo keeps the old one).
        self.x_break = None
        #: Which palette the window draws in (`ui/plot.py` owns the names).
        self.theme = "blender-default"
        #: The page's colour, or None for the theme's.
        self.background = None
        #: Decorators placed on the page MOVE WITH THE DATA when zoomed, or
        #: stay where they are on the page (the default).
        self.follow_zoom = False
        #: This figure's own sizes and alignments (`core/style.py`).
        self.style = style.FigureStyle()
        #: The figure's size and the place of its axes box.
        self.figure = style.figure_default() or figure_module.FigureLayout()
        #: The framing as the plot keeps it, or None for "fitted".
        self.view = None
        #: Every drawn scan labelled with its y offset (`Scan.marker`).
        self.offset_markers = False
        self.path = ""              # the session file, once saved
        self._next = 100

    # ------------------------------------------------------------------ ids
    def _next_id(self):
        value = getattr(self, "_next", 100)
        self._next = value + 1
        return value

    # -------------------------------------------------------------- content
    def objects(self):
        """Everything selectable, in draw order (later is on top)."""
        markers = ([scan.marker for scan in self.scans]
                   if self.offset_markers else [])
        return (list(self.regions) + list(self.scans) + self.analyses()
                + markers + list(self.spans) + list(self.labels)
                + list(self.images) + list(self.structures)
                + list(self.axes.values()) + [self.legend])

    def add_label(self, text="Label", x=0.5, y=0.5, scan=None):
        label = TextLabel(self._next_id(), text, x, y, scan)
        self.labels.append(label)
        return label

    def rescaled(self, scan, multiplier):
        """The changes that draw `scan` `multiplier` times as tall as
        measured (after normalisation) IN ITS PLACE: the factor, and the
        offset that keeps the curve's baseline (`profile.baseline`) where
        it is drawn now."""
        from . import profile
        multiplier = float(multiplier)
        changes = [(scan, "multiplier", multiplier)]
        values, scale, shift = scan.display_values(self)
        was = float(scan.multiplier or 1.0)
        if values is None or scan.draws_lines or not was:
            return changes
        k0, k1 = scan.kept_range(len(values))
        rest = profile.baseline(values[k0:k1] * scale + shift)
        offset = float(scan.offset) + rest * (1.0 - multiplier / was)
        return changes + [(scan, "offset", offset)]

    def multiplier_label_of(self, scan):
        """The label that says `scan`'s multiplier, or None."""
        for label in self.labels:
            if label.scan is scan and getattr(label, "shows", None) == \
                    "multiplier":
                return label
        return None

    def new_multiplier_label(self, scan, at=None):
        """A label saying `scan`'s multiplier ("x15", its `{}`), in its
        colour, hanging just above the curve's left end - `at`, the
        sample, given by the plot. Not added: the caller does that, in
        its undo step."""
        label = TextLabel(self._next_id(), "\\times{}", 0.05, 0.5, scan)
        label.shows = "multiplier"
        label.anchor = "bottom left"
        label.flush = "left"
        label.at = at
        label.dx, label.dy = 4.0, -4.0
        return label

    def labels_for(self, scan):
        """The labels that belong to one scan."""
        return [label for label in self.labels if label.scan is scan]

    def labels_of_removed(self, scan):
        """Take a scan's labels off with it, and hand them back (so the
        command that removed the scan can put them back on undo)."""
        owned = self.labels_for(scan)
        self.labels = [label for label in self.labels
                       if label.scan is not scan]
        return owned

    def remove_label(self, label):
        if label in self.labels:
            self.labels.remove(label)
        for span in self.spans:
            span.ends = [None if end is label else end for end in span.ends]
        return label

    def analyses(self):
        """Every analysis object on every scan."""
        return [a for scan in self.scans for a in scan.analysis_objects]

    def visible_analyses(self):
        return [a for scan in self.scans if scan.visible
                for a in scan.visible_analyses()]

    def magnifiers_of(self, scan):
        """The visible regions that magnify `scan`."""
        return [r for r in self.regions
                if r.visible and r.magnifies
                and any(s is scan for s in r.scans)]

    def add_sample(self, sample):
        """Add a file and its scan; returns the scan."""
        self.samples.append(sample)
        scan = Scan(self._next_id(), sample,
                    PALETTE[len(self.scans) % len(PALETTE)], self)
        sample.scans.append(scan)
        self.scans.append(scan)
        return scan

    def detach_scan(self, scan):
        """Take a scan off the plot, KEEPING its sample."""
        if scan in self.scans:
            self.scans.remove(scan)
        if scan in scan.sample.scans:
            scan.sample.scans.remove(scan)

    def insert_scan(self, scan, index=None):
        """Put a scan back where it was (or at the end)."""
        if scan in self.scans:
            return scan
        if index is None or index > len(self.scans):
            index = len(self.scans)
        self.scans.insert(index, scan)
        scan.doc = self
        if scan not in scan.sample.scans:
            scan.sample.scans.append(scan)
        if scan.sample not in self.samples:
            self.samples.append(scan.sample)
        return scan

    def remove_scan(self, scan):
        """Detach a scan and forget its sample if nothing else uses it."""
        self.detach_scan(scan)
        if not scan.sample.scans and scan.sample in self.samples:
            self.samples.remove(scan.sample)

    def close_sample(self, sample):
        """Forget a file entirely: its scan and the sample itself."""
        for scan in list(sample.scans):
            self.detach_scan(scan)
        if sample in self.samples:
            self.samples.remove(sample)

    def sample_for(self, path):
        for sample in self.samples:
            if os.path.normcase(sample.path) == os.path.normcase(str(path)):
                return sample
        return None

    # ------------------------------------------------------------ selection
    def selected(self):
        return [obj for obj in self.objects() if obj.selected]

    def selected_scans(self):
        return [s for s in self.scans if s.selected]

    def select_only(self, objs):
        wanted = set(id(o) for o in (objs or ()))
        for obj in self.objects():
            obj.selected = id(obj) in wanted

    def select_all(self, on=True):
        """Everything, or nothing. "Everything" leaves the axes out."""
        for obj in self.objects():
            obj.selected = bool(on) and not isinstance(obj, Axis)

    # ---------------------------------------------------------------- state
    def visible_scans(self):
        return [s for s in self.scans if s.visible]

    # ---------------------------------------------------------- the order
    # The OUTLINER's order is the figure's: files top to bottom as listed.
    # S and "Stack evenly" stack in it, the top of the list at the top of
    # the stack; the legend lists in it.
    def outliner_key(self, scan):
        sample = scan.sample
        at = (self.samples.index(sample) if sample in self.samples
              else len(self.samples))
        return (at,)

    def in_outliner_order(self, scans):
        return sorted(scans, key=self.outliner_key)

    def set_sample_order(self, samples):
        """The files in this order (all of them, each once), and the scans
        with them."""
        if sorted(map(id, samples)) != sorted(map(id, self.samples)):
            raise ValueError("not an order of this figure's files")
        self.samples = list(samples)
        self.scans.sort(key=self.outliner_key)

    def unit_for(self, _scan=None):
        """The unit a scan is drawn in: the y axis's."""
        return self.y_unit

    # ------------------------------------------------------------ the x axis
    @property
    def x_axis(self):
        """The x axis's quantity in words: what a framing, a lock or a
        hidden number is recorded against (`PlotWidget.axis_context`)."""
        return X_AXIS_NAMES.get(self.x_quantity, str(self.x_quantity))

    @property
    def x_unit(self):
        return units.X_TEXT.get(self.x_quantity, "")

    def wavelength(self):
        """The wavelength the figure's x is converted at: the first
        MEASURED pattern's (in the outliner's order) that states one, else
        the first simulated one's; None when no pattern has one."""
        for simulated in (False, True):
            for sample in self.samples:
                if sample.simulated == simulated and sample.wavelength:
                    return float(sample.wavelength)
        return None

    def reference_wavelength(self):
        """What a new simulation is drawn at: `wavelength`, else copper
        K-alpha1 (said where the simulation's wavelength is shown)."""
        return self.wavelength() or crystal.DEFAULT_WAVELENGTH

    def mixed_wavelengths(self):
        """The wavelengths of the measured patterns on show, when they are
        not all one: their 2-theta is then not one axis (compare in d or
        Q). [] when they agree."""
        seen = []
        for sample in self.samples:
            if (sample.simulated or not sample.wavelength
                    or not any(s.visible for s in sample.scans)):
                continue
            if all(abs(sample.wavelength - w) > 1e-4 for w in seen):
                seen.append(float(sample.wavelength))
        return seen if len(seen) > 1 else []

    def measured_range(self, wavelength=None):
        """`(low, high)` in 2-theta at `wavelength` (the reference one) that
        the measured patterns cover - what a new simulation spans - or
        None. A pattern of another wavelength is carried across through Q;
        one with none counts as it is."""
        wavelength = wavelength or self.reference_wavelength()
        lows, highs = [], []
        for sample in self.samples:
            if sample.simulated:
                continue
            x = np.asarray(sample.x, dtype=float)
            if sample.wavelength and abs(sample.wavelength
                                         - wavelength) > 1e-6:
                x = units.convert(x, units.TWO_THETA, units.Q,
                                  sample.wavelength)
                x = units.convert(x, units.Q, units.TWO_THETA, wavelength)
            x = x[np.isfinite(x)]
            if len(x):
                lows.append(float(np.min(x)))
                highs.append(float(np.max(x)))
        if not lows:
            return None
        return min(lows), max(highs)

    def set_x_quantity(self, quantity):
        """The changes - `[(obj, attr, value), ...]`, applied by the caller
        as ONE undo step - that show the x axis as `quantity`: every x
        position on the figure converted at the figure's `wavelength`, and
        every analysis measured again on the new axis (at its own
        pattern's wavelength). [] when nothing changes. Raises ValueError
        when 2-theta is involved and no pattern states a wavelength."""
        old = self.x_quantity
        if quantity == old or quantity not in units.QUANTITIES:
            return []
        wavelength = self.wavelength()
        if wavelength is None and units.TWO_THETA in (old, quantity):
            raise ValueError("no pattern in the figure states its "
                             "wavelength")

        def one(value):
            return units.convert_one(value, old, quantity, wavelength)

        def pair(lo, hi):
            a, b = one(lo), one(hi)
            return None if a is None or b is None else sorted((a, b))

        changes = [(self, "x_quantity", quantity)]
        for region in self.regions:
            ends = pair(region.lo, region.hi)
            if ends:
                changes += [(region, "lo", ends[0]), (region, "hi", ends[1])]
        for label in self.labels:
            if label.vline is not None:
                value = one(label.vline)
                if value is not None:
                    changes.append((label, "vline", value))
            if label.space == SPACE_DATA:
                value = one(label.x)
                if value is not None:
                    changes.append((label, "x", value))
            if label.leader:
                value = one(label.leader[0])
                if value is not None:
                    changes.append((label, "leader",
                                    [value, label.leader[1]]))
        for span in self.spans:
            for attr in ("x0", "x1"):
                value = one(getattr(span, attr))
                if value is not None:
                    changes.append((span, attr, value))
        for artist in list(self.images) + list(self.structures) + [
                self.legend]:
            if artist.space == SPACE_DATA:
                value = one(artist.x)
                if value is not None:
                    changes.append((artist, "x", value))
        if self.x_break:
            ends = pair(self.x_break.get("lo"), self.x_break.get("hi"))
            cut = dict(self.x_break)
            if ends:
                cut["lo"], cut["hi"] = ends
                changes.append((self, "x_break", cut))
            else:
                changes.append((self, "x_break", None))
        if self.norm_band:
            ends = pair(*self.norm_band)
            if ends:
                changes.append((self, "norm_band", list(ends)))
        for scan in self.scans:
            at = scan.marker.at
            if at and at[0] == "x":
                value = one(at[1])
                if value is not None:
                    changes.append((scan.marker, "at", ("x", value)))
        axis = self.axes["x"]
        if axis.major_step is not None:
            changes.append((axis, "major_step", None))
        changes += self._remeasured(quantity)
        return changes

    def _remeasured(self, quantity):
        """The changes that measure every analysis again on `quantity`'s
        axis: same samples, new numbers. An analysis whose pattern has no
        wavelength for it keeps what it has, and the axis it is in."""
        from . import measure
        out = []
        for analysis in self.analyses():
            own = analysis.axis or self.x_quantity
            if own == quantity:
                continue
            wavelength = analysis.scan.sample.wavelength
            cursors = [units.convert_one(c, own, quantity, wavelength)
                       for c in analysis.cursors()]
            if len(cursors) != 2 or None in cursors:
                continue
            fields = measure.compute(analysis.model_name, analysis.scan,
                                     cursors[0], cursors[1], analysis.span,
                                     quantity)
            if not fields:
                continue
            out += [(analysis, "fields", fields),
                    (analysis, "axis", quantity)]
            if analysis.label_at is not None:
                out.append((analysis, "label_at", units.convert_one(
                    analysis.label_at, own, quantity, wavelength)))
        return out

    def norm_extent(self, _unit=None):
        """`(low, high)` of the intensities of every pattern ON SHOW - its
        kept samples, on the axis as shown - or None: what "all together,
        0 to 1" scales by. A pattern opened, hidden or cut changes it, and
        every curve follows. Patterns drawn as lines across the plot have
        no height and take no part."""
        shown = [s for s in self.scans if s.visible and not s.draws_lines]
        for scan in shown:
            if scan.sample.simulated:
                scan.sample.simulation()       # its key up to date
        key = (self.x_quantity,) + tuple(
            (id(s), tuple(s.keep), id(s.sample), s.sample._sim_key,
             s.drawing, s.strongest, s.sample.wavelength) for s in shown)
        memo = getattr(self, "_extent_memo", None)
        if memo is not None and memo[0] == key:
            return memo[1]
        lows, highs = [], []
        for scan in shown:
            if scan.x_values(self) is None:
                continue
            values = np.asarray(scan.own_curve()[1], dtype=float)
            k0, k1 = scan.kept_range(len(values))
            part = values[k0:k1]
            part = part[np.isfinite(part)]
            if len(part):
                lows.append(float(part.min()))
                highs.append(float(part.max()))
        extent = (min(lows), max(highs)) if lows else None
        self._extent_memo = (key, extent)
        return extent

    def global_extent(self):
        """`norm_extent` while the patterns are normalised all together,
        else None."""
        return self.norm_extent() if self.norm == units.NORM_GLOBAL else None

    def y_key(self):
        """What the y axis shows, as one value: (unit, norm, band)."""
        return (self.y_unit, self.norm,
                tuple(self.norm_band) if self.norm_band else None)

    def y_axis_unit(self):
        """The y axis's unit as a word: "a.u." or "normalised"."""
        if self.norm != units.NORM_NONE:
            return "normalised"
        return "a.u."


    def scans_missing(self):
        """`[(scan, what), ...]`: scans on show that cannot be drawn."""
        out = []
        for scan in self.scans:
            if not scan.visible:
                continue
            missing = scan.missing_for(self)
            if missing:
                out.append((scan, missing))
        return out

    def set_display(self, unit=None, norm=None, band=False):
        """What the y axis shows - `unit`, `norm`, and `band` (False: keep)
        - carrying every offset across by ONE factor, so a stack keeps its
        order and its proportions: how much taller the patterns are, as the
        median over them (`stack_factor`). Each pattern by its own factor
        would reshuffle a stack whose patterns change height unequally - a
        normalisation does exactly that. Returns the changes, the display
        fields among them; applies none."""
        before = self.y_key()
        after = (unit if unit is not None else self.y_unit,
                 norm if norm is not None else self.norm,
                 (tuple(band) if band else None) if band is not False
                 else before[2])
        if after == before:
            return []
        changes = []
        factor = self.stack_factor(before, after)
        if factor:
            for scan in self.scans:
                if scan.offset:
                    changes.append((scan, "offset",
                                    float(scan.offset) * factor))
            for label in self.labels:
                if label.scan is not None and label.parent_offset:
                    changes.append((label, "parent_offset",
                                    float(label.parent_offset) * factor))
        if after[0] != self.y_unit:
            changes.append((self, "y_unit", after[0]))
        if after[1] != self.norm:
            changes.append((self, "norm", after[1]))
        band_value = list(after[2]) if after[2] else None
        if band_value != (list(self.norm_band) if self.norm_band else None):
            changes.append((self, "norm_band", band_value))
        return changes

    def stack_factor(self, before, after):
        """How much taller the patterns are drawn as `after` than as
        `before` (each a `y_key`): the median of their own ratios, or None
        when no pattern can be drawn both ways."""
        ratios = []
        for scan in self.scans:
            old = scan.factor(before)
            new = scan.factor(after)
            if old and new:
                ratios.append(float(new) / float(old))
        if not ratios:
            return None
        return float(np.median(ratios))


def break_of(doc):
    """The x axis's break as `(lo, hi, compress, gap)`, or None."""
    found = getattr(doc, "x_break", None)
    if not found:
        return None
    try:
        lo, hi = sorted((float(found["lo"]), float(found["hi"])))
        compress = float(found.get("compress", 0.02))
        gap = float(found.get("gap", 7.0))
    except (KeyError, TypeError, ValueError):
        return None
    if not hi > lo or not compress > 0:
        return None
    return lo, hi, min(1.0, compress), max(0.0, gap)
