"""The painted plot: a PXRD pattern window, taught what a pattern stack is.

The navigation, the pixmap cache and the per-column decimation come straight
from a PXRD viewer's pattern window and are deliberately key for key the
same: `Z` cycles zoom, `P` cycles pan, `Esc` leaves the mode, `F` or `Home`
resets, right-click opens the menu for whatever is under the cursor. The
point is that hands which know that window already know this one.

What is particular to powder patterns, and where:

* **The x axis is 2-theta, d or Q, may run backwards and may be broken.**
  A d axis is drawn from large spacings to small, and a stack may have
  nothing to say over a stretch of it. Both live in ONE mapping,
  `x_to_px` / `px_to_x`: the view is still (low, high), the page shows it
  turned round on a d axis, and a break squeezes its
  stretch through a piecewise-linear warp (`warp_x`). Everything that places
  anything at a position goes through those two, and everything that
  moves the view along x (zoom, pan, typed distances) works in the warped
  space, so a break and the reversal need no special case anywhere else.
* **The stack is continuous.** A pattern is moved to wherever it should
  sit, in the axis's unit, and the offset it was given is drawn beside it
  as a number with an arrow. There are no slots to swap.
* **Normalising is a choice, and said.** The y axis's caption follows it.
* **Objects are selected and transformed.** Click to select, Shift+click to
  add, `G` to grab with typed numbers and `Esc` to cancel: Blender's
  viewport handling, in a plot.
* **A pattern that cannot be drawn is still there.** One with no
  wavelength on a d or Q axis is drawn as a dashed placeholder at its own
  offset with a blinking red label, not left out and not quietly plotted
  at a wavelength made up for it.
* **A simulation may be lines across the plot** (`_paint_lines`): a CIF's
  or a card's strongest reflections, dotted, top to bottom.
"""

import contextlib
import math
import re
import time

import numpy as np
import shiboken6

from PySide6.QtCore import (QByteArray, QPoint, QPointF, QRect, QRectF,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QFontMetricsF,
                           QImage, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF, QTransform)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core import figure as figure_module
from ..core import (crystal, labels, measure, model, numbers, profile, style,
                    units)

#: The ways this program draws.
#:
#: `blender-default` is the screen theme: pale ink on a dark ground, the same
#: choice the PXRD window makes. `light` is the same plot on white,
#: which is what a document or a paper wants - and what every export uses,
#: because a dark figure does not survive being dropped into a page.
#:
#: `boombox` is brushed-metal greys, parchment text and an LCD-green accent.
#:
#: Everything that has a colour reads it from here, so a third theme is a
#: dictionary and not a hunt through the drawing code.
THEME_DARK = "blender-default"
THEME_LIGHT = "light"
THEME_BOOMBOX = "boombox"

THEMES = {
    THEME_DARK: {
        "_BG": QColor(38, 38, 38),
        # Round the figure when it is shown at a fixed size or aspect: the
        # page edge has to be visible, or its size means nothing on screen.
        "_SURROUND": QColor(26, 26, 26),
        "_AXIS": QColor(150, 150, 150),
        "_GRID": QColor(58, 58, 58),
        "_TEXT": QColor(205, 205, 205),
        "_TEXT_DIM": QColor(150, 150, 150),
        "_INK": QColor(228, 228, 228),      # the arrow and other artists
        "_CURSOR": QColor(240, 200, 90),
        "_BAND": QColor(240, 200, 90, 60),
        "_BAND_EDGE": QColor(240, 200, 90),
        "_SELECT": QColor(255, 170, 60),
        "_ALARM": QColor(232, 76, 76),
    },
    THEME_LIGHT: {
        "_BG": QColor(255, 255, 255),
        "_SURROUND": QColor(170, 170, 170),
        "_AXIS": QColor(40, 40, 40),
        "_GRID": QColor(226, 226, 226),
        "_TEXT": QColor(20, 20, 20),
        "_TEXT_DIM": QColor(60, 60, 60),
        "_INK": QColor(26, 26, 26),
        "_CURSOR": QColor(190, 130, 20),
        "_BAND": QColor(190, 130, 20, 50),
        "_BAND_EDGE": QColor(190, 130, 20),
        "_SELECT": QColor(220, 120, 20),
        "_ALARM": QColor(200, 30, 30),
    },
    # The palette: surface #2a2d31, window #232323, text #d6d6c2, muted
    # #9a9a86, headings #3a3f44, the LCD green #39ff7a, hover #ffd23b.
    THEME_BOOMBOX: {
        "_BG": QColor("#2a2d31"),
        "_SURROUND": QColor("#1c1c1c"),
        "_AXIS": QColor("#9a9a86"),
        "_GRID": QColor("#3a3f44"),
        "_TEXT": QColor("#d6d6c2"),
        "_TEXT_DIM": QColor("#9a9a86"),
        "_INK": QColor("#e2e2cf"),
        # The reticle in the LCD green.
        "_CURSOR": QColor("#39ff7a"),
        "_BAND": QColor(57, 255, 122, 50),
        "_BAND_EDGE": QColor("#39ff7a"),
        "_SELECT": QColor("#39ff7a"),
        "_ALARM": QColor("#ff5555"),
    },
}

#: The theme in force. Module state rather than a parameter threaded through
#: forty paint calls; `themed()` swaps it for the duration of an export.
THEME = THEME_DARK
globals().update(THEMES[THEME_DARK])

#: The handling colours (`ACCENTS`) are brought down to this relative
#: luminance on a light ground; an object's colour never is (`paper_colour`).
#: The number was measured against tab10 / ColorBrewer / Okabe-Ito rather
#: than taken from the WCAG floor, which is darker than any of them.
PAPER_LUMA = 0.42


#: The colours of the HANDLING rather than the figure: the reticle, the
#: zoom band and the selection. They belong to the program's theme, not to
#: the ink the page is drawn in (otherwise Boombox on a white page gets the
#: light theme's amber reticle).
ACCENTS = ("_CURSOR", "_BAND", "_BAND_EDGE", "_SELECT")


def set_theme(name, accent=None):
    """Switch the palette. Returns the name that is now in force.

    `accent` is the theme whose handling colours (`ACCENTS`) to keep when
    the figure is drawn in another one; on the light ink they are darkened
    to read on the paper, hue kept.
    """
    global THEME
    if name not in THEMES:
        return THEME
    THEME = name
    globals().update(THEMES[name])
    if accent in THEMES and accent != name:
        for key in ACCENTS:
            colour = QColor(THEMES[accent][key])
            if name == THEME_LIGHT:
                alpha = colour.alpha()
                colour = for_light(colour)
                colour.setAlpha(alpha)
            globals()[key] = colour
    return THEME


#: The shading of an integral: this much of the curve's colour over the page.
SHADE_ALPHA = 55


def shade_fill(colour, page, opaque=False):
    """The fill of a shaded integral. Opaque, it is the colour the
    translucent fill makes over `page` - the same look with nothing behind
    it showing through."""
    fill = QColor(colour)
    if not opaque:
        fill.setAlpha(SHADE_ALPHA)
        return fill
    a = SHADE_ALPHA / 255.0
    page = QColor(page)
    return QColor.fromRgbF(
        a * fill.redF() + (1 - a) * page.redF(),
        a * fill.greenF() + (1 - a) * page.greenF(),
        a * fill.blueF() + (1 - a) * page.blueF())


def _flipped(image, horizontal, vertical):
    """`image` mirrored: Qt 6.9's `flipped`, else the older `mirrored`."""
    if hasattr(image, "flipped"):
        if horizontal and vertical:
            return image.flipped(Qt.Horizontal | Qt.Vertical)
        return image.flipped(Qt.Horizontal if horizontal else Qt.Vertical)
    return image.mirrored(horizontal, vertical)


def mixed(colour, page, share):
    """`colour` at `share` over `page`, as one opaque colour."""
    colour, page = QColor(colour), QColor(page)
    return QColor.fromRgbF(
        share * colour.redF() + (1 - share) * page.redF(),
        share * colour.greenF() + (1 - share) * page.greenF(),
        share * colour.blueF() + (1 - share) * page.blueF())


def for_light(colour):
    """`colour`, darkened enough to read on white, hue kept.

    Scaling the three channels by one factor preserves the hue, which is the
    thing that tells two traces apart; a colour already dark enough is left
    exactly as it is, which protects one the user picked by hand.
    """
    colour = QColor(colour)
    luma = (0.2126 * colour.redF() + 0.7152 * colour.greenF()
            + 0.0722 * colour.blueF())
    if luma <= PAPER_LUMA:
        return colour
    factor = PAPER_LUMA / max(luma, 1e-6)
    return QColor.fromRgbF(colour.redF() * factor, colour.greenF() * factor,
                           colour.blueF() * factor)


def paper_colour(colour):
    """An object's colour as a white page - and so every export - draws
    it: exactly as on the screen, the default palette included. Darkened
    to read on paper (the palette is chosen for a dark ground), its orange
    came out brown. Only the handling colours - reticle, selection -
    follow the page (`set_theme`, `for_light`)."""
    return QColor(colour)

#: Room for the y numbers, which this plot has and the PXRD one does not.
_LEFT = 66
_RIGHT = 14
_TOP = 12
_BOTTOM = 42

CURVE_WIDTH = 1.0
SELECTED_EXTRA = 1.0

#: One wheel notch, Mestrenova's step.
WHEEL_STEP = 1.2
#: Trackpads report pixels and wheels report eighths of a degree; both are
#: brought to "notches" before either is believed, by the same two numbers
#: the PXRD window uses.
PANE_STEP_PIXELS = 60.0
PANE_WHEEL_UNITS = 120.0

#: How far the cursor must travel before a press becomes a drag. Cumulative
#: from the press: a trackpad delivers one or two pixels per event, so a
#: per-event threshold never trips.
DRAG_SLOP = 4

#: A burst of wheel, swipe or pinch events is ONE undo step, and it ends
#: when the fingers have been still this long.
VIEW_SETTLE_MS = 450

#: An offset marker's label hangs this fraction of the plot's height
#: BELOW its curve.
OFFSET_MARKER_DROP = 0.03
#: An unplaced marker points this far (figure units) in from the left end
#: of its curve, or from the y axis when the curve runs past it: tight
#: against the axis.
OFFSET_MARKER_INSET = 6.0

#: Figure units per typographic point: the figure is 96 units per inch.
PT = 96.0 / 72.0

#: Tried in order after the house style's family, when that one is not on
#: the machine: Bahnschrift, the default, ships with Windows 10 and 11 and
#: with nothing else, so a Mac or Linux gets the nearest plain sans.
FALLBACK_FAMILIES = ("Segoe UI", "Helvetica Neue", "Arial",
                     "Liberation Sans", "DejaVu Sans")

#: Half the length of the dash at each bound of an analysis's interval,
#: centred on the trace. Small on purpose: it marks where the stretch ends,
#: it is not an annotation in its own right.

#: The blink for a pattern that cannot be drawn: two flashes, then a
#: long pause. Tuned to be noticeable in the corner of the eye and NOT to
#: strobe: 10 ticks of 160 ms is 1.6 s of which 0.64 s is lit, then 3.2 s of
#: nothing.
#: How the status line names the x axis's quantity.
_X_SYMBOLS = {units.TWO_THETA: "2\u03b8", units.D: "d", units.Q: "Q"}

BLINK_MS = 160
BLINK_ON = (0, 1, 3, 4)
BLINK_PERIOD = 30


class Trace(object):
    """One scan, prepared for one particular view.

    Built fresh by `rebuild`, thrown away when anything changes. It holds the
    converted arrays so that painting, hit-testing and exporting all read the
    same numbers.
    """

    __slots__ = ("scan", "x", "y", "colour", "missing", "px", "py",
                 "hidden", "first")

    def __init__(self, scan, x, y, colour, missing=None, hidden=(), first=0):
        self.scan = scan
        #: The KEPT samples only (see `Scan.keep`): everything that fits,
        #: picks, arranges or measures reads these.
        self.x = x
        self.y = y
        #: The truncated ends as `[(x, y), ...]`, drawn dashed on hover.
        self.hidden = list(hidden)
        #: Where `x[0]` sits in the pattern's own arrays, so a sample picked
        #: here can be named in the file's terms (an analysis's `span`).
        self.first = int(first)
        self.colour = QColor(colour)
        #: What stops this scan being drawn, or None. A trace with a reason
        #: has no arrays and is drawn as a placeholder.
        self.missing = missing
        #: The screen points last drawn, kept for hit-testing so that what
        #: the cursor picks is what the eye sees.
        self.px = None
        self.py = None

    @property
    def name(self):
        return self.scan.display_name()


@contextlib.contextmanager
def themed(_plot, name):
    """Draw in another theme for the duration, then put the old one back.

    Used by the exports, which are always light: it restores in a `finally`,
    so an exception mid-paint cannot leave the window drawn for paper.
    """
    previous = THEME
    set_theme(name)
    try:
        yield
    finally:
        set_theme(previous)


def paper_palette(plot, light=True):
    """Backwards-compatible alias: the light theme, for an export."""
    return themed(plot, THEME_LIGHT if light else THEME)


class PlotWidget(QWidget):
    """The plot. Owns the view and the gestures; the document owns the data."""

    hovered = Signal(str)
    mode_changed = Signal(str)
    view_changed = Signal()
    #: (object under the cursor or None, global position)
    context_menu = Signal(object, QPoint)
    selection_changed = Signal()
    #: A gesture finished and these property changes should become one undo
    #: step: [(obj, name, value), ...], and a label for the menu.
    transform_done = Signal(list, str)
    #: Double-click on an object: the window opens its settings.
    activated = Signal(object)
    #: Double-click on one of the page's handles: the window asks for the
    #: figure's size in numbers.
    page_size_asked = Signal()
    #: A margin arrow was let go or typed: (side, share of the axis). The
    #: window sets the figure's fit margin and refits that axis, one step.
    margin_done = Signal(str, float)
    #: A page-margin blade let go, typed or double-clicked: (side, the
    #: margin in the layout's unit). The window sets it, one step.
    page_margin_done = Signal(str, float)
    #: A blade was taken on a figure that is not of an exact size: the
    #: window makes it exact, from the screen (`MainWindow.go_exact`).
    exact_wanted = Signal()
    #: A blade or a fit-margin arrow double-clicked: the window opens the
    #: page's (or the data's) margins in numbers.
    page_margins_asked = Signal()
    data_margins_asked = Signal()
    #: Two cursors are down and Enter was pressed: (scan, x0, x1, analysis
    #: being edited or None, span or None). Both are positions on x;
    #: the span, when the cursors came from the curve, is the two sample
    #: indices they sit on.
    measure_ready = Signal(object, float, float, object, object)
    #: A zoom or pan finished: (view before, view after, "zoom"/"pan"/"fit"),
    #: for the window to put on the undo stack. See `commit_view`.
    view_committed = Signal(object, object, str)

    #: BOX first: in a stack of patterns the feature is a region in both
    #: directions - a band of one curve - so the first Z is the box.
    ZOOM_CYCLE = ("zoom_box", "zoom_h", "zoom_v", None)
    PAN_CYCLE = ("pan_h", "pan_v", "pan_free", None)
    MODE_TEXT = {
        "zoom_h": "ZOOM horizontal - drag a range (Esc exits)",
        "zoom_v": "ZOOM vertical - drag a range (Esc exits)",
        "zoom_box": "ZOOM box - drag a rectangle (Esc exits)",
        "pan_h": "PAN horizontal - drag (Esc exits)",
        "pan_v": "PAN vertical - drag (Esc exits)",
        "pan_free": "PAN free - drag (Esc exits)",
    }
    #: What the status line says when no tool is armed. Box select is the
    #: resting state, so Esc always lands somewhere useful.
    SELECT_TEXT = ("SELECT - drag a curve to analyse, a label to move it, "
                   "empty space for a box (Shift adds); G moves scans")
    #: While a double-click-drag is marking an interval on a curve.
    INTERVAL_TEXT = "ANALYSE - release to choose what to compute (Esc cancels)"
    MODE_CURSOR = {"zoom_h": Qt.SizeHorCursor, "zoom_v": Qt.SizeVerCursor,
                   "zoom_box": Qt.CrossCursor, "pan_h": Qt.SizeHorCursor,
                   "pan_v": Qt.SizeVerCursor, "pan_free": Qt.SizeAllCursor}
    #: A middle-button drag, by what it does (`nav_kind`).
    NAV_TEXT = {
        "scale": "TALLER or flatter, each curve in its place - drag up "
                 "for taller",
        "zoom": "ZOOM both axes about where the drag started",
        "pan": "PAN - the figure follows the pointer",
        "page_zoom": "PAGE zoom - drag up to look closer",
        "page_pan": "PAGE move - the page follows the pointer",
    }
    NAV_CURSOR = {"scale": Qt.SizeVerCursor, "zoom": Qt.SizeVerCursor,
                  "pan": Qt.ClosedHandCursor, "page_zoom": Qt.SizeVerCursor,
                  "page_pan": Qt.ClosedHandCursor}

    def __init__(self, document=None, parent=None):
        QWidget.__init__(self, parent)
        #: The font the figure starts from; the house style's family is set
        #: on a copy of it (`figure_font`).
        self._base_font = QFont(self.font())
        self.setMinimumHeight(240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.doc = document
        self.traces = []
        #: Hidden curves that labels hang from (`_label_trace`): never
        #: drawn, picked, fitted or exported.
        self._ghosts = []
        self.y_margin = 0.06
        self._view_x = None
        self._view_y = None
        self._cursor = None
        self._mode = None
        self._drag = None           # a navigation drag (zoom band / pan)
        self._move = None           # a transform: mouse drag or G
        self._box = None            # a box selection being dragged
        self._measure = None        # cursors being placed on a scan
        self._interval = None       # a drag along a curve, marking a stretch
        self._press = None          # a press near an object, not yet a drag
        self._nav = None            # a middle-button drag: the mouse's swipe
        self._leader_drag = None    # a note's arrow tip being moved
        self._view_burst = None     # (view before, label) while zooming
        self._view_settle = QTimer(self)
        self._view_settle.setSingleShot(True)
        self._view_settle.setInterval(VIEW_SETTLE_MS)
        self._view_settle.timeout.connect(self.commit_view)
        self._flash = None          # (text, started) - "Saved" and the like
        self._axis_hit = None       # 'caption' or 'spine'
        self._cursor_handles = []   # [(index, QRect)] while measuring
        self._cursor_drag = None    # a measure cursor being dragged
        self._cache = None
        self._cache_key = None
        self._label_boxes = []      # [(trace, QRect)] from the last render
        self._analysis_boxes = []   # [(Analysis, QRect)] from the last render
        self._marker_boxes = []     # [(OffsetMarker, QRectF)], likewise
        #: (object clicked, what was selected before) when a click narrowed
        #: a selection - undone if the click becomes a double-click.
        self._click_restore = None
        #: A live `S` scale, or None.
        self._scale = None
        #: When the last presses were, for the click RHYTHM: faster than
        #: `DOUBLE_CLICK_S` is a double-click, up to `CYCLE_S` steps to the
        #: next object under the pointer. `_clock` is replaceable in tests.
        self._clock = time.monotonic
        self._presses = []
        self._axis_boxes = []       # [(Axis, QRect)] for the captions
        self._text_boxes = []       # [(TextLabel, QRect)]
        self._blink = 0
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(BLINK_MS)
        self._blink_timer.timeout.connect(self._tick_blink)
        #: Set only while painting into an export device, where the sampling
        #: resolution is a choice rather than the screen's.
        self._columns_override = None

    # --------------------------------------------------------------- content
    def set_document(self, doc):
        self.doc = doc
        self.rebuild(keep_view=False)

    def rebuild(self, keep_view=True):
        """Rebuild every trace from the document, then redraw."""
        self._span_memo = {}
        traces = []
        ghosts = []
        doc = self.doc
        if doc is not None:
            traces = [self._make_trace(doc, scan) for scan in doc.scans
                      if scan.visible]
            # A label keeps hanging where its curve was while the curve is
            # hidden (without this it fell back on the middle of the plot).
            hidden = []
            for label in doc.labels:
                scan = label.scan if getattr(label, "attached", False) \
                    else None
                if (scan is not None and not scan.visible
                        and scan in doc.scans and scan not in hidden):
                    hidden.append(scan)
            ghosts = [trace for trace in (self._make_trace(doc, scan)
                                          for scan in hidden)
                      if trace.x is not None]
        self.traces = traces
        self._ghosts = ghosts
        if not keep_view:
            self._view_x = self._view_y = None
            self.forget_fit()
        self._sync_blink()
        self.invalidate()

    @staticmethod
    def _make_trace(doc, scan):
        """One scan's `Trace` for the document as it is now."""
        x, y = scan.curve(doc)
        if x is None:
            return Trace(scan, x, y, scan.colour, scan.missing_for(doc))
        k0, k1 = scan.kept_range(len(x))
        # Each hidden end overlaps the kept part by one sample, so the
        # dashed line meets the solid one instead of a gap.
        hidden = []
        if k0 > 0:
            hidden.append((x[:k0 + 1], y[:k0 + 1]))
        if k1 < len(x):
            hidden.append((x[k1 - 1:], y[k1 - 1:]))
        return Trace(scan, x[k0:k1], y[k0:k1], scan.colour, None, hidden, k0)

    def invalidate(self):
        self._sync_font()
        self._cache = None
        self.update()

    def figure_font(self):
        """The figure's font: the house style's FAMILY, at the base size.

        Every text on the figure starts from this and sets only its own
        size (and italic, per character, through the markup), so one
        setting changes the typeface of all of it."""
        font = QFont(self._base_font)
        family = (style.figure_value(self.doc, "font_family")
                  if self.doc is not None else "")
        if family:
            font.setFamilies([family] + [f for f in FALLBACK_FAMILIES
                                         if f != family])
            font.setStyleHint(QFont.SansSerif)
        # Laid out as the outlines are, not snapped to the pixel grid of
        # 96-unit design space: hinted advances, scaled onto a zoomed page,
        # spaced the letters of every number and caption unevenly. It
        # also makes the layout the same at any zoom and in every
        # export, which is what an exact figure promises.
        font.setHintingPreference(QFont.PreferNoHinting)
        return font

    def molecule_font(self, molecule):
        """A structure's element-label font, at its label size: its own
        family, else the house style's (`structure_font`, Arial Rounded MT
        built in), else - empty - the figure's."""
        font = QFont(self.figure_font())
        family = (style.value(self.doc, molecule, "label_font")
                  if self.doc is not None else molecule.label_font)
        if family:
            font.setFamilies([family] + [f for f in FALLBACK_FAMILIES
                                         if f != family])
        font.setPointSizeF(max(3.0, float(molecule.label_size)))
        return font

    def _sync_font(self):
        """The widget's own font follows the figure's, so that measuring
        the margins (which reads `self.font()`) sees the same typeface."""
        font = self.figure_font()
        if font != self.font():
            self.setFont(font)

    def style_of(self, obj, attr):
        """The value `obj.attr` is drawn with, the house style filling in.

        Every size and alignment the drawing uses is read HERE rather than
        off the object, because None on the object means "not chosen" - see
        `core/style.py`.
        """
        return style.value(self.doc, obj, attr)

    def drawable(self):
        return [t for t in self.traces if t.missing is None]

    # ---------------------------------------------------------------- ranges
    def x_sides(self):
        """The fit margins at the LOW and the HIGH end of x: with the
        axis reversed (d), the low end is on the right."""
        return (("right", "left") if profile.x_reversed(self.doc)
                else ("left", "right"))

    def _fit_span(self, trace):
        """`(low, high)` of a trace's x as F frames it: on a d axis, not
        past the spacing of `profile.D_FIT_FROM` degrees 2-theta (the d of
        a pattern measured from near 0 runs to thousands of angstrom)."""
        x = trace.x
        doc = self.doc
        wavelength = trace.scan.sample.wavelength
        if (doc is not None and doc.x_quantity == units.D and wavelength
                and x is not None):
            cap = float(units.from_two_theta(profile.D_FIT_FROM, units.D,
                                             wavelength))
            with np.errstate(invalid="ignore"):
                x = x[x <= cap]
        return _finite_span(x)

    def raw_x(self):
        """`(low, high)` of the drawn curves' x, or None."""
        lows, highs = [], []
        # A paint asks for the range dozens of times; the spans only change
        # when the traces are rebuilt.
        memo = self.__dict__.setdefault("_span_memo", {})
        for trace in self.drawable():
            key = id(trace.x)
            if key not in memo:
                memo[key] = self._fit_span(trace)
            span = memo[key]
            if span is not None:
                lows.append(span[0])
                highs.append(span[1])
        if not lows:
            return None
        lo, hi = min(lows), max(highs)
        if hi <= lo:
            return (lo, lo + 1.0)
        return lo, hi

    def data_x(self):
        """The x range F frames: the curves', the fit margins round it, the
        ends on a whole grid (`profile.X_ROUND`: 5 and 50, not the first
        and last sample's 4.9996 and 50.0012)."""
        raw = self.raw_x()
        if raw is None:
            return profile.EMPTY_X.get(getattr(self.doc, "x_quantity", None),
                                       profile.EMPTY_X[units.TWO_THETA])
        lo, hi = self.padded(raw[0], raw[1], *self.x_sides())
        if profile.X_ROUND:
            lo, hi = rounded_ends(lo, hi, profile.X_SNAP)
        return lo, hi

    def fit_pad(self, side):
        """The share of the AXIS F leaves empty on `side` (house style "Fit
        margin"; left 0.1 is the first tenth of the x axis)."""
        return float(style.figure_value(self.doc, "fit_" + side)
                     if self.doc is not None
                     else style.preference("fit_" + side))

    def fit_pads(self, low_side, high_side):
        """Both margins of an axis, scaled down together should they leave
        less than `style.FIT_MOST` for the data."""
        a = max(0.0, self.fit_pad(low_side))
        b = max(0.0, self.fit_pad(high_side))
        if a + b > style.FIT_MOST:
            scale = style.FIT_MOST / (a + b)
            a, b = a * scale, b * scale
        return a, b

    def padded(self, lo, hi, low_side, high_side):
        """The axis range that leaves the fit margins round (lo, hi)."""
        a, b = self.fit_pads(low_side, high_side)
        span = (hi - lo) / (1.0 - a - b)
        return lo - a * span, hi + b * span

    def data_y(self):
        """The fitted y range: every drawn curve, and every analysis label
        on them with its arrow (otherwise a label over the strongest peak is
        cut off by F).

        A label is sized in drawing units and hangs a fixed distance from
        its curve, so how much range it needs depends on the range: the top
        is raised until each label above its point fits under the top of the
        axes box (and the bottom lowered for each one below), a few rounds
        of `hi = y + reach / height * (hi - lo)`, which settles quickly
        while the labels take less than the box.

        Worked out once per paint (`paint_into` holds `_fit_memo`).
        """
        memo = getattr(self, "_fit_memo", False)
        if isinstance(memo, dict) and "y" in memo:
            return memo["y"]
        found = self._fit_y()
        if isinstance(memo, dict) and not getattr(self, "_fitting", False):
            memo["y"] = found
        return found

    def _fit_y(self):
        lo, hi = self._curves_y()
        if getattr(self, "_fitting", False):
            # Measuring the labels asks for the axes box, whose margin asks
            # for the widest y number, which asks for this range: the curves
            # alone are the answer there (the box's HEIGHT does not change).
            return lo, hi
        fitted = self.__dict__.setdefault("_fitted", {})
        last = fitted.get("y")
        moving = getattr(self, "_move", None)
        if (last is not None and moving is not None
                and any(isinstance(o, model.Analysis)
                        for o in moving["objs"])):
            # An analysis label being dragged: the frame holds still under
            # the hand and fits again once it is let go.
            return last
        self._fitting = True
        try:
            rect = self.plot_rect()
            reach = self.label_reach(rect)
        finally:
            self._fitting = False
        if not reach:
            fitted["y"] = (lo, hi)
            return lo, hi
        height = max(1.0, rect.height())
        # Labels taller than about half the box cannot all fit: cap each
        # one's share, so the range stays finite and the curves visible.
        reach = [(y, min(above / height, 0.45), min(below / height, 0.45))
                 for y, above, below in reach]
        top, bottom = hi, lo
        for _round in range(40):
            span = top - bottom
            new_top = max([hi] + [y + up * span for y, up, _down in reach])
            new_bottom = min([lo] + [y - down * span
                                     for y, _up, down in reach])
            settled = (abs(new_top - top) + abs(new_bottom - bottom)
                       <= 1e-9 * max(span, 1e-12))
            top, bottom = new_top, new_bottom
            if settled:
                break
        fitted["y"] = (bottom, top)
        return bottom, top

    def label_reach(self, rect):
        """`[(y, above, below), ...]`: for each analysis label a fitted view
        draws, the curve's height it points at (data units) and how far the
        label and its arrow reach above and below that point (drawing units).
        The same geometry `_paint_analysis_label` draws, less the painting.
        """
        doc = self.doc
        if doc is None:
            return []
        lo, hi = self.view_x()
        base = self.figure_font()
        out = []
        for trace in self.drawable():
            for analysis in trace.scan.visible_analyses():
                value = self.label_x(analysis)
                if value is None or not (lo <= value <= hi):
                    continue
                index = self._sample_near(
                    trace, value, self._analysis_slice(trace, analysis))
                if index is None:
                    continue
                font = QFont(base)
                font.setPointSizeF(max(5.0, float(self.style_of(
                    analysis, "label_size"))))
                text = labels.render(analysis, doc).text
                half = markup_size(text, font)[1] / 2.0 + 3.0
                offset = self.label_offset(analysis, trace, rect)
                out.append((float(trace.y[index]),
                            max(0.0, half - offset), max(0.0, half + offset)))
        return out

    def _curves_y(self):
        """The curves' y range, with the fit margins either side."""
        lows, highs = [], []
        memo = self.__dict__.setdefault("_span_memo", {})
        for trace in self.drawable():
            key = ("y", id(trace.y))
            if key not in memo:
                memo[key] = _finite_span(trace.y)
            span = memo[key]
            if span is not None:
                lows.append(span[0])
                highs.append(span[1])
        for trace in self.traces:
            if trace.missing is not None:
                lows.append(float(trace.scan.offset))
                highs.append(float(trace.scan.offset))
        if not lows:
            return (0.0, 100.0)
        lo, hi = min(lows), max(highs)
        if hi <= lo:
            lo, hi = lo - 0.5, hi + 0.5
        return self.padded(lo, hi, "bottom", "top")

    def view_x(self):
        return self._view_x or self.home("x")

    def view_y(self):
        return self._view_y or self.home("y")

    # ---------------------------------------------------------- the home
    # Where F goes, and what an unframed axis shows: an axis's LOCKED range
    # ("Lock current framing", and min and max in an axis's settings)
    # where it has one in the axes' present units, else
    # the fit. Decorators on the page are placed in this frame too, so a
    # zoom takes them along with the data (`rel_to_px`).
    def axis_context(self, which):
        """What a range on axis `which` is measured in: a range in
        2-theta says nothing about d, nor a normalised one about counts."""
        doc = self.doc
        if doc is None:
            return None
        if which == "x":
            return [doc.x_axis, getattr(doc, "x_unit", "")]
        band = list(doc.norm_band) if doc.norm_band else []
        return [doc.y_unit, doc.norm] + [float(b) for b in band]

    def lock_of(self, which):
        """Axis `which`'s locked range, or None (none, or another unit's)."""
        doc = self.doc
        axis = doc.axes.get(which) if doc is not None else None
        lock = getattr(axis, "lock", None)
        if not lock or list(axis.lock_context or []) != self.axis_context(
                which):
            return None
        lo, hi = float(lock[0]), float(lock[1])
        return (lo, hi) if hi > lo else None

    def home(self, which):
        """Where F returns axis `which`: its lock, else the kept fit."""
        return self.lock_of(which) or self.kept_fit(which)

    def _home_frames(self):
        """`(home x, view x, home y, view y)`, or None when they are the
        same (nothing is zoomed) or cannot be asked (a fit is being worked
        out: it must not ask where artists are)."""
        if (self.doc is None or getattr(self, "_fitting", False)
                or getattr(self, "_homing", False)
                or not getattr(self.doc, "follow_zoom", False)):
            return None
        self._homing = True
        try:
            hx, vx = self.home("x"), self.view_x()
            hy, vy = self.home("y"), self.view_y()
        finally:
            self._homing = False
        if _close(hx, vx) and _close(hy, vy):
            return None
        if not (hx[1] > hx[0] and vx[1] > vx[0] and hy[1] > hy[0]
                and vy[1] > vy[0]):
            return None
        return hx, vx, hy, vy

    #: A view this close to its home (a share of the home range at each
    #: end) is not "zoomed".
    HOME_TOLERANCE = 0.02

    def zoomed(self):
        """True while the view is noticeably not its home (`home`):
        decorators on the page are then cut at the axes."""
        frames = self._home_frames()
        if frames is None:
            return False
        (hx0, hx1), (vx0, vx1), (hy0, hy1), (vy0, vy1) = frames

        def near(h0, h1, v0, v1):
            span = (h1 - h0) * self.HOME_TOLERANCE
            return abs(v0 - h0) <= span and abs(v1 - h1) <= span

        return not (near(hx0, hx1, vx0, vx1) and near(hy0, hy1, vy0, vy1))

    def rel_to_px(self, fx, fy, rect=None):
        """A place on the page's plot as fractions of the axes box (a
        decorator's `relative` space) in figure units. The fractions are
        of the box AT HOME: zoomed in, a decorator moves with the data it
        sits over; at home it is exactly where it was put."""
        rect = rect or self.plot_rect()
        frames = self._home_frames()
        px = rect.left() + fx * rect.width()
        py = rect.top() + fy * rect.height()
        if frames is None:
            return px, py
        hx, vx, hy, vy = frames
        x = self.px_to_x(px, rect, hx)
        y = self.px_to_y(py, rect, hy)
        return (float(self.x_to_px(x, rect, vx)),
                float(self.y_to_px(y, rect, vy)))

    def px_to_rel(self, px, py, rect=None):
        """`rel_to_px` backwards: the fractions of the box at home."""
        rect = rect or self.plot_rect()
        frames = self._home_frames()
        if frames is not None:
            hx, vx, hy, vy = frames
            x = self.px_to_x(px, rect, vx)
            y = self.px_to_y(py, rect, vy)
            px = float(self.x_to_px(x, rect, hx))
            py = float(self.y_to_px(y, rect, hy))
        return ((px - rect.left()) / max(1.0, rect.width()),
                (py - rect.top()) / max(1.0, rect.height()))

    def fresh_home(self, which):
        """`home` with the fit worked out now (to compare with)."""
        lock = self.lock_of(which)
        if lock:
            return lock
        return self.data_x() if which == "x" else self.data_y()

    def kept_fit(self, which):
        """An unframed axis's range: the fit, worked out ONCE and kept
        (so the plot is never rescaled without F because a label moved).
        Worked out again by F, and when the set of curves on that axis
        changes (one shown or hidden, a unit, a truncation, a break)."""
        signature = self._fit_signature(which)
        kept = self.__dict__.setdefault("_kept_fits", {}).get(which)
        if kept is not None and kept[0] == signature:
            return kept[1]
        value = self.data_x() if which == "x" else self.data_y()
        if not getattr(self, "_fitting", False):
            self._kept_fits[which] = (signature, value)
        return value

    def _fit_signature(self, which):
        """What an axis's fit depends on besides where things were moved."""
        doc = self.doc
        if doc is None:
            return None
        traces = self.drawable() if which == "x" else self.traces
        if which == "x":
            extra = (doc.x_axis, self.fit_pad("left"), self.fit_pad("right"))
        else:
            extra = (doc.y_key(), self.fit_pad("bottom"), self.fit_pad("top"))
        return (tuple(sorted((id(t.scan), tuple(t.scan.keep),
                              t.missing is None) for t in traces)) + extra)

    def forget_fit(self, *which):
        """Work the fit out again next time (all axes without names)."""
        kept = self.__dict__.setdefault("_kept_fits", {})
        for name in (which or ("x", "y")):
            kept.pop(name, None)

    def set_view_x(self, lo, hi):
        if hi > lo:
            self._view_x = (float(lo), float(hi))
            self.invalidate()
            self.view_changed.emit()

    def set_view_y(self, lo, hi):
        if hi > lo:
            self._view_y = (float(lo), float(hi))
            self.invalidate()
            self.view_changed.emit()

    # ----------------------------------------------------------- view history
    # Zoom and pan are on the undo stack (zooming in on a band several
    # times goes back step by step with Ctrl+Z). The plot decides what ONE
    # step is and hands it over as `view_committed`.
    def view_state(self):
        """The framing, as the undo stack keeps it: the limits as stored -
        None meaning "fitted" - plus what they are in."""
        doc = self.doc
        context = ((doc.x_axis, doc.y_key()) if doc is not None else None)
        return {"x": self._view_x, "y": self._view_y, "context": context}

    def restore_view(self, state):
        """Go back to a framing. One taken on other axes (the unit changed
        since) is meaningless as numbers, so it goes back to the fit."""
        self._view_settle.stop()
        self._view_burst = None
        self._burst_offsets = None
        context = state.get("context")
        if context is not None and not isinstance(context, tuple):
            context = tuple(context)
        mine = self.view_state()["context"]
        if context and mine and _same_context(context, mine):
            self._view_x, self._view_y = state["x"], state["y"]
        elif not context:
            self._view_x, self._view_y = state["x"], state["y"]
        else:
            self._view_x = self._view_y = None
        offsets = state.get("offsets")
        if offsets:
            for scan, value, multiplier in offsets["scans"]:
                scan.offset = value
                scan.multiplier = multiplier
            if self.doc is not None:
                self.doc.labels[:] = offsets["labels"]
            self.rebuild()
        self.invalidate()
        self.view_changed.emit()

    def _view_begin(self, label):
        """A view gesture is starting (or continuing): remember where from."""
        if self._view_burst is None:
            self._view_burst = (self.view_state(), label)
            self._burst_offsets = self._offsets_now()

    def _offsets_now(self):
        """Every scan's offset and multiplier, and the labels (a swipe on
        a selection may add an "xN" one), as a swipe's undo step keeps
        them - never in `view_state`: that one is saved with the session,
        as numbers."""
        doc = self.doc
        if doc is None:
            return None
        return {"scans": [(scan, float(scan.offset), float(scan.multiplier))
                          for scan in doc.scans],
                "labels": list(doc.labels)}

    def _view_touched(self, label):
        """One event of a wheel or pinch burst. The burst is one step, and it
        ends when the fingers have been still for `VIEW_SETTLE_MS`."""
        self._view_begin(label)
        self._view_settle.start()

    def commit_view(self):
        """End the view gesture in progress and hand it over as one step.
        Called by the settle timer, by the end of a zoom box or a pan drag,
        and by the window before an undo or redo."""
        self._view_settle.stop()
        burst, self._view_burst = self._view_burst, None
        if burst is None:
            return False
        self.update()                   # the smooth pass after the drafts
        before, label = burst
        after = self.view_state()
        # A swipe moves the offsets with the framing (`scale_intensity`):
        # they go into the same step, and only into the step.
        offsets = getattr(self, "_burst_offsets", None)
        self._burst_offsets = None
        now = self._offsets_now()
        if offsets is not None and offsets != now:
            self.view_committed.emit(dict(before, offsets=offsets),
                                     dict(after, offsets=now), label)
            return True
        if (before["x"], before["y"]) == (after["x"], after["y"]):
            return False
        self.view_committed.emit(before, after, label)
        return True

    def at_home_x(self):
        """True when F would not change x."""
        return _close(self.view_x(), self.fresh_home("x"))

    def at_home_y(self):
        return _close(self.view_y(), self.fresh_home("y"))

    def reset_view(self):
        """`F`: staged, as a pattern viewer does it (`profile.FIT_STAGED`):
        x first, then y; at once when the profile says so."""
        if self.at_home_x() and self.at_home_y():
            return False
        if not profile.FIT_STAGED:
            self.fit()
            return True
        self._view_begin("fit")
        if not self.at_home_x():
            self._view_x = None
            self.forget_fit("x")
        else:
            self._view_y = None
            self.forget_fit("y")
        self.invalidate()
        self.view_changed.emit()
        self.commit_view()
        return True

    def shown_view_axes(self):
        """The axes with a range on show."""
        return ["x", "y"]

    def view_of(self, which):
        return self.view_x() if which == "x" else self.view_y()

    def set_axis_view(self, which, lo, hi):
        """Frame one axis at (lo, hi), as ONE view step (undoable)."""
        if not hi > lo:
            return False
        self._view_begin("zoom")
        if which == "x":
            self._view_x = (float(lo), float(hi))
        else:
            self._view_y = (float(lo), float(hi))
        self.invalidate()
        self.view_changed.emit()
        self.commit_view()
        return True

    def fit_axis(self, which):
        """F for one axis only: "x" or "y"."""
        self._view_begin("fit")
        if which == "x":
            self._view_x = None
            self.forget_fit("x")
        else:
            self._view_y = None
            self.forget_fit("y")
        self.invalidate()
        self.view_changed.emit()
        self.commit_view()

    def fit(self):
        self._view_begin("fit")
        self._view_x = self._view_y = None
        self.forget_fit()
        self.invalidate()
        self.view_changed.emit()
        self.commit_view()

    # --------------------------------------------------------------- mapping
    # ------------------------------------------------------------------ page
    #: Pane pixels kept round a figure shown at a fixed size or aspect.
    PAGE_PAD = 26

    def layout_mode(self):
        """How the figure is sized: `core/figure.py`'s window, aspect, size."""
        doc = self.doc
        layout = getattr(doc, "figure", None) if doc is not None else None
        if layout is None or not layout.is_valid():
            return figure_module.MODE_WINDOW
        return layout.mode

    def canvas_size(self):
        """The figure's own size, in drawing units (96 per inch).

        The pane itself; the largest rectangle of the aspect ratio that fits
        in it; or the EXACT physical size - which does not depend on the pane
        at all, so an export comes out the same whatever the window is doing.
        """
        mode = self.layout_mode()
        width, height = float(self.width()), float(self.height())
        if mode == figure_module.MODE_SIZE:
            return self.doc.figure.size_px()
        if mode == figure_module.MODE_ASPECT:
            layout = self.doc.figure
            ratio = float(layout.aspect_w) / float(layout.aspect_h)
            room_w = max(10.0, width - 2 * self.PAGE_PAD)
            room_h = max(10.0, height - 2 * self.PAGE_PAD)
            if room_w / room_h > ratio:
                return room_h * ratio, room_h
            return room_w, room_w / ratio
        return width, height

    #: What was last scrolled on Windows with Alt held, which Qt reports as
    #: horizontal whichever way the fingers went: the window reads the real
    #: direction from the system's message (`MainWindow.nativeEvent`).
    native_wheel = "vertical"

    def page(self):
        """`(dx, dy, k)`: where the figure's corner sits in the pane, and how
        many pane pixels one drawing unit takes: the fitted page, then the
        page's own zoom and pan (Alt, Alt+Shift) on top."""
        dx, dy, k = self._page_fit()
        zoom = getattr(self, "_page_zoom", 1.0)
        pan_x, pan_y = getattr(self, "_page_pan", (0.0, 0.0))
        if zoom == 1.0 and not pan_x and not pan_y:
            return dx, dy, k
        canvas_w, canvas_h = self.canvas_size()
        centre_x, centre_y = dx + canvas_w * k / 2.0, dy + canvas_h * k / 2.0
        k2 = k * zoom
        return (centre_x - canvas_w * k2 / 2.0 + pan_x,
                centre_y - canvas_h * k2 / 2.0 + pan_y, k2)

    def page_zoomed(self):
        """True while the page is zoomed or moved away from its fit."""
        return (getattr(self, "_page_zoom", 1.0) != 1.0
                or getattr(self, "_page_pan", (0.0, 0.0)) != (0.0, 0.0))

    def zoom_page(self, factor, at=None):
        """Zoom the PAGE - the whole figure on the pane, like a document
        in Word - about the pane point `at`, keeping what is under it where
        it is. The data and the figure's proportions do not change; this is
        looking closer, not a new framing."""
        if factor <= 0:
            return
        dx, dy, k = self.page()
        at = at or QPointF(self.width() / 2.0, self.height() / 2.0)
        fig_x, fig_y = (at.x() - dx) / k, (at.y() - dy) / k
        zoom = min(40.0, max(0.1, getattr(self, "_page_zoom", 1.0) * factor))
        self._page_zoom = zoom
        self._page_pan = (0.0, 0.0)
        new_dx, new_dy, new_k = self.page()
        self._page_pan = (at.x() - fig_x * new_k - new_dx,
                          at.y() - fig_y * new_k - new_dy)
        self.invalidate()

    def pan_page(self, dx, dy):
        """Move the page on the pane by (dx, dy) pane pixels."""
        pan_x, pan_y = getattr(self, "_page_pan", (0.0, 0.0))
        self._page_pan = (pan_x + dx, pan_y + dy)
        self.invalidate()

    # ------------------------------------------------- the page's handles
    #: Pane pixels of a page handle's square.
    PAGE_HANDLE = 8.0

    def page_on_pane(self):
        """The page, in pane pixels."""
        canvas_w, canvas_h = self.canvas_size()
        return QRectF(self.to_widget(QPointF(0.0, 0.0)),
                      self.to_widget(QPointF(canvas_w, canvas_h)))

    def page_handles(self, page=None):
        """`[((hx, hy), square), ...]`: the four handles at the middles of
        the page's (or `page`'s) edges, in pane pixels."""
        page = page or self.page_on_pane()
        size = self.PAGE_HANDLE
        out = []
        # The edge squares only: the corner ones were in the way of the
        # blades and the arrows.
        for hx in (0.0, 0.5, 1.0):
            for hy in (0.0, 0.5, 1.0):
                if (hx == 0.5) == (hy == 0.5):
                    continue
                x = page.left() + hx * page.width()
                y = page.top() + hy * page.height()
                out.append(((hx, hy), QRectF(x - size / 2.0, y - size / 2.0,
                                             size, size)))
        return out

    def page_handle_at(self, pane_pos):
        """Which handle the pane point is on, when they are shown."""
        if not getattr(self, "_page_handles_shown", False):
            return None
        for handle, square in self.page_handles():
            if square.adjusted(-3.0, -3.0, 3.0, 3.0).contains(pane_pos):
                return handle
        return None

    def in_page_margin(self, pos):
        """True for a figure point on the page but outside the axes box,
        and on nothing - not a caption, the numbers or the spine: where a
        click shows the page's handles."""
        canvas_w, canvas_h = self.canvas_size()
        rect = self.plot_rect()
        inside_page = (0.0 <= pos.x() <= canvas_w
                       and 0.0 <= pos.y() <= canvas_h)
        return (inside_page and not rect.contains(pos)
                and not self.objects_at(pos))

    def _start_page_drag(self, handle, pane_pos):
        self._page_drag = {"handle": handle, "start": QPointF(pane_pos),
                           "page": self.page_on_pane(),
                           "k": self.page()[2]}
        self._page_drag["proposal"] = QRectF(self._page_drag["page"])
        self.update()

    def _drag_page(self, pane_pos):
        """Where the page would be if let go here: the dragged edges move,
        the opposite ones stay."""
        drag = self._page_drag
        hx, hy = drag["handle"]
        page = drag["page"]
        dx = pane_pos.x() - drag["start"].x()
        dy = pane_pos.y() - drag["start"].y()
        left, right = page.left(), page.right()
        top, bottom = page.top(), page.bottom()
        least = 40.0
        if hx == 0.0:
            left = min(left + dx, right - least)
        elif hx == 1.0:
            right = max(right + dx, left + least)
        if hy == 0.0:
            top = min(top + dy, bottom - least)
        elif hy == 1.0:
            bottom = max(bottom + dy, top + least)
        drag["proposal"] = QRectF(left, top, right - left, bottom - top)
        self.hovered.emit(self._page_readout())
        self.update()

    @staticmethod
    def _page_size_of(drag):
        """A drag's proposed page, in figure units."""
        proposal = drag["proposal"]
        return proposal.width() / drag["k"], proposal.height() / drag["k"]

    def _page_readout(self):
        width, height = self._page_size_of(self._page_drag)
        doc = self.doc
        if self.layout_mode() == figure_module.MODE_SIZE:
            unit = doc.figure.unit
            per = figure_module.PER_INCH[unit] / figure_module.DESIGN_DPI
            return "PAGE {:.2f} x {:.2f} {}".format(width * per,
                                                    height * per, unit)
        return "PAGE aspect {:.3g} : 1".format(width / max(1e-6, height))

    def _finish_page_drag(self, cancel=False):
        """Let go of a page handle: the figure takes the new size (an exact
        figure) or the new aspect ratio (any other), as ONE undo step, and
        the page is fitted to the window again, as Alt+F does."""
        drag, self._page_drag = self._page_drag, None
        if drag is None:
            return
        if cancel or drag["proposal"] == drag["page"] or self.doc is None:
            self.update()
            return
        width, height = self._page_size_of(drag)
        layout = self.doc.figure
        if self.layout_mode() == figure_module.MODE_SIZE:
            per = (figure_module.PER_INCH[layout.unit]
                   / figure_module.DESIGN_DPI)
            least_w = layout.margin_left + layout.margin_right + 0.2
            least_h = layout.margin_top + layout.margin_bottom + 0.2
            changes = [(layout, "width", round(max(least_w, width * per), 3)),
                       (layout, "height",
                        round(max(least_h, height * per), 3))]
        else:
            changes = [(layout, "mode", figure_module.MODE_ASPECT),
                       (layout, "aspect_w",
                        round(width / max(1e-6, height), 4)),
                       (layout, "aspect_h", 1.0)]
        self.transform_done.emit(changes, "figure size")
        self.fit_page()

    def _paint_page_handles(self, p):
        """The page's handles, and while one is dragged the page it would
        make, dashed."""
        drag = getattr(self, "_page_drag", None)
        if not getattr(self, "_page_handles_shown", False) and drag is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        if drag is not None:
            p.setPen(QPen(_SELECT, 1.0, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(drag["proposal"])
        p.setPen(QPen(_SELECT, 1.0))
        p.setBrush(QColor(255, 255, 255))
        # While dragged, the handles ride on the page it would make.
        for _handle, square in self.page_handles(
                drag["proposal"] if drag is not None else None):
            p.drawRect(square)
        p.restore()

    # ------------------------------------------------ the margin gizmos
    # An arrow on the page's edge per fit margin,
    # shown with the page's handles, at the line where the data begins. A
    # drag shows that line dashed and lets go of it as the margin (the
    # share of the axis beyond it left empty, the axis refitted); a click
    # selects the arrow, and a typed number is then the margin, as G and a
    # number are for a move. A value that cannot be drawn (negative, or
    # leaving the data no room with the opposite margin) flashes the line
    # red and changes nothing.
    #: Pane pixels of an arrow: half its width, and how far it reaches out
    #: past the page's edge and in over it.
    GIZMO_HALF = 6.0
    GIZMO_OUT = 12.0
    GIZMO_IN = 5.0
    #: How long a refused value keeps the line red, in seconds.
    MARGIN_FLASH_SECONDS = 0.35

    def _margin_state(self):
        return self.__dict__.setdefault("_margin", {
            "drag": None, "selected": None, "typed": "", "flash": None})

    def margin_share(self, side):
        """Where the data begins on `side` in the view as it is, as a share
        of the axes box from that side (the fit's margin when unframed).
        Measured on the page, so a reversed or broken x axis needs nothing
        of its own."""
        rect = self.plot_rect()
        if side in ("left", "right"):
            raw = self.raw_x()
            if raw is None:
                return self.fit_pad(side)
            ends = sorted(float(self.x_to_px(v, rect)) for v in raw)
            gap = (ends[0] - rect.left() if side == "left"
                   else rect.right() - ends[1])
            share = gap / max(rect.width(), 1e-12)
        else:
            lo, hi = self.data_y()
            a, b = self.fit_pads("bottom", "top")
            span = hi - lo
            raw_lo, raw_hi = lo + a * span, hi - b * span
            v_lo, v_hi = self.view_y()
            width = max(v_hi - v_lo, 1e-12)
            share = ((raw_lo - v_lo) / width if side == "bottom"
                     else (v_hi - raw_hi) / width)
        return min(max(share, 0.0), 1.0)

    def margin_line(self, side, share=None):
        """The margin's line in FIGURE units: an x for left and right, a y
        for bottom and top. `share` of the axes box from its side (the
        current place when None)."""
        rect = self.plot_rect()
        if share is None:
            share = self.margin_share(side)
        if side == "left":
            return rect.left() + share * rect.width()
        if side == "right":
            return rect.right() - share * rect.width()
        if side == "bottom":
            return rect.bottom() - share * rect.height()
        return rect.top() + share * rect.height()

    #: Each margin's arrow: the page edge it stands on, and the way it
    #: points (into the page). Left and top margins sit at the top-left,
    #: right and bottom at the bottom-right.
    GIZMO_EDGES = {"left": ("top", (0.0, 1.0)),
                   "right": ("bottom", (0.0, -1.0)),
                   "top": ("left", (1.0, 0.0)),
                   "bottom": ("right", (-1.0, 0.0))}

    def margin_gizmo(self, side, share=None):
        """The arrow of `side` as a polygon in pane pixels."""
        page = self.page_on_pane()
        edge, (dx, dy) = self.GIZMO_EDGES[side]
        line = self.margin_line(side, share)
        if side in ("left", "right"):
            along = self.to_widget(QPointF(line, 0.0)).x()
            base = QPointF(along, page.top() if edge == "top"
                           else page.bottom())
        else:
            along = self.to_widget(QPointF(0.0, line)).y()
            base = QPointF(page.left() if edge == "left" else page.right(),
                           along)
        nx, ny = -dy, dx
        half, out, inner = self.GIZMO_HALF, self.GIZMO_OUT, self.GIZMO_IN
        shape = QPolygonF()
        for u, v in ((-half, -out), (half, -out), (half, -1.0),
                     (0.0, inner), (-half, -1.0)):
            shape.append(QPointF(base.x() + u * nx + v * dx,
                                 base.y() + u * ny + v * dy))
        # A page filling the pane has its edge ON the pane's: bring the
        # arrow in, or it is drawn off the widget.
        box = shape.boundingRect()
        shift = 0.0
        if dy > 0:
            shift = max(0.0, -box.top())
        elif dy < 0:
            shift = max(0.0, box.bottom() - self.height())
        elif dx > 0:
            shift = max(0.0, -box.left())
        else:
            shift = max(0.0, box.right() - self.width())
        if shift:
            shape.translate(dx * shift, dy * shift)
        return shape

    def margin_gizmo_at(self, pane_pos):
        """Which margin's arrow the pane point is on, when they are shown."""
        if not getattr(self, "_page_handles_shown", False) or self.doc is None:
            return None
        for side in style.FIT_SIDES:
            box = self.margin_gizmo(side).boundingRect()
            if box.adjusted(-3.0, -3.0, 3.0, 3.0).contains(pane_pos):
                return side
        return None

    def margin_limit(self, side):
        """The largest share `side` may take beside its opposite's."""
        return max(0.0, style.FIT_MOST
                   - max(0.0, self.fit_pad(style.FIT_OPPOSITE[side])))

    def margin_valid(self, side, share):
        return (share is not None and share == share and share >= 0.0
                and share < self.margin_limit(side))

    def _share_at(self, side, pane_pos):
        """The share of the axes box between `side` and the pointer."""
        rect = self.plot_rect()
        pos = self.to_figure(pane_pos)
        if side == "left":
            share = (pos.x() - rect.left()) / max(rect.width(), 1e-9)
        elif side == "right":
            share = (rect.right() - pos.x()) / max(rect.width(), 1e-9)
        elif side == "bottom":
            share = (rect.bottom() - pos.y()) / max(rect.height(), 1e-9)
        else:
            share = (pos.y() - rect.top()) / max(rect.height(), 1e-9)
        # Just inside the limit, so a drag can never be refused.
        return min(max(share, 0.0), self.margin_limit(side) - 1e-3)

    def _margin_readout(self, side, share, typed=None):
        words = "{} MARGIN".format(side.upper())
        if typed is not None:
            return "{} = {}  (a share of the axis; Enter)".format(words,
                                                                 typed)
        return "{} {:.3f} of the axis".format(words, share)

    def _start_margin_drag(self, side, pane_pos):
        state = self._margin_state()
        state["drag"] = {"side": side, "start": QPointF(pane_pos),
                         "share": self.margin_share(side), "moved": False}
        state["typed"] = ""
        self.hovered.emit(self._margin_readout(side,
                                               state["drag"]["share"]))
        self.update()

    def _drag_margin(self, pane_pos):
        drag = self._margin_state()["drag"]
        if not drag["moved"]:
            dist = max(abs(pane_pos.x() - drag["start"].x()),
                       abs(pane_pos.y() - drag["start"].y()))
            if dist < DRAG_SLOP:
                return
            drag["moved"] = True
        drag["share"] = self._share_at(drag["side"], pane_pos)
        self.hovered.emit(self._margin_readout(drag["side"], drag["share"]))
        self.update()

    def _finish_margin_drag(self, cancel=False):
        """Let go: moved, the margin is set; a click selects the arrow for
        a typed number."""
        state = self._margin_state()
        drag, state["drag"] = state["drag"], None
        if drag is None:
            return
        if cancel:
            self.update()
            return
        if drag["moved"]:
            state["selected"] = drag["side"]
            self.margin_done.emit(drag["side"], round(drag["share"], 4))
        else:
            state["selected"] = drag["side"]
            state["typed"] = ""
            self.setFocus(Qt.MouseFocusReason)
            self.hovered.emit("{} MARGIN {:.3f}: type a share of the axis "
                              "(0.1 is 10 %), Enter".format(
                                  drag["side"].upper(),
                                  self.margin_share(drag["side"])))
        self.update()

    def margin_selected(self):
        return self._margin_state()["selected"]

    def select_margin(self, side):
        state = self._margin_state()
        state["selected"] = side
        state["typed"] = ""
        self.update()

    def type_margin(self, text):
        """A typed margin for the selected arrow, as if typed and Entered.
        True if it was taken; a refused one flashes the line red."""
        state = self._margin_state()
        side = state["selected"]
        if side is None:
            return False
        value = numbers.evaluate(text)
        state["typed"] = ""
        if not self.margin_valid(side, value):
            state["flash"] = (side, time.monotonic()
                              + self.MARGIN_FLASH_SECONDS)
            QTimer.singleShot(int(self.MARGIN_FLASH_SECONDS * 1000) + 20,
                              self.update)
            other = self.fit_pad(style.FIT_OPPOSITE[side])
            self.hovered.emit(
                "{} is not a margin that can be drawn: 0 up to {:.2f} "
                "beside the {} margin's {:.3f}".format(
                    text, self.margin_limit(side),
                    style.FIT_OPPOSITE[side], other))
            self.update()
            return False
        self.margin_done.emit(side, round(float(value), 4))
        self.update()
        return True

    def _margin_key(self, ev):
        """Keys for a selected margin arrow. True if the key was used."""
        state = self._margin_state()
        side = state["selected"]
        if side is None:
            return False
        if not getattr(self, "_page_handles_shown", False):
            state["selected"], state["typed"] = None, ""
            return False
        key, text = ev.key(), ev.text()
        if key == Qt.Key_Escape:
            if state["typed"]:
                state["typed"] = ""
            else:
                state["selected"] = None
            self.hovered.emit("")
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            if state["typed"]:
                self.type_margin(state["typed"])
        elif key == Qt.Key_Backspace:
            state["typed"] = state["typed"][:-1]
            self.hovered.emit(self._margin_readout(side, 0.0,
                                                   state["typed"]))
        elif text and (text.isdigit() or text in ".,+-*/()"):
            state["typed"] += text
            self.hovered.emit(self._margin_readout(side, 0.0,
                                                   state["typed"]))
        else:
            return False
        self.update()
        return True

    def _paint_margin_gizmos(self, p):
        """The four arrows, and the dashed line of the one dragged, typed
        or refused."""
        if not getattr(self, "_page_handles_shown", False) or self.doc is None:
            return
        state = self._margin_state()
        drag = state["drag"]
        flash = state["flash"]
        if flash is not None and time.monotonic() > flash[1]:
            flash = state["flash"] = None
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        # The line: where a drag or a typed number would put the data's edge.
        line_side, line_share, colour = None, None, QColor(_SELECT)
        if drag is not None:
            line_side, line_share = drag["side"], drag["share"]
        elif state["selected"] and state["typed"]:
            value = numbers.evaluate(state["typed"])
            if self.margin_valid(state["selected"], value):
                line_side, line_share = state["selected"], value
        if flash is not None:
            line_side, line_share = flash[0], None
            colour = QColor(_ALARM)
        if line_side is not None:
            rect = self.plot_rect()
            at = self.margin_line(line_side, line_share)
            if line_side in ("left", "right"):
                a = self.to_widget(QPointF(at, rect.top()))
                b = self.to_widget(QPointF(at, rect.bottom()))
            else:
                a = self.to_widget(QPointF(rect.left(), at))
                b = self.to_widget(QPointF(rect.right(), at))
            p.setPen(QPen(colour, 2.0 if flash is not None else 1.2,
                          Qt.DashLine))
            p.drawLine(a, b)
        for side in style.FIT_SIDES:
            share = drag["share"] if drag and drag["side"] == side else None
            shape = self.margin_gizmo(side, share)
            active = (side == state["selected"]
                      or (drag is not None and drag["side"] == side))
            refused = flash is not None and flash[0] == side
            edge = QColor(_ALARM) if refused else QColor(_SELECT)
            p.setPen(QPen(edge, 1.2))
            p.setBrush(edge if active or refused else QColor(255, 255, 255))
            p.drawPolygon(shape)
        p.restore()

    # ------------------------------------------- the page-margin gizmos
    # The WHITE margins round the axes box of an exact figure. A pointed oval,
    # a blade: symmetrical, and not an arrow, which sets the data's margin
    # inside the box - just outside the page at its corners the arrows leave
    # free: the right and top margins at the upper right, the left and bottom
    # ones at the lower left. Each blade stands at the very EDGE of the page:
    # pulled in it cuts white space off, pulled out it adds some - the page
    # grows or shrinks and the axes box keeps its size - and afterwards it
    # stands at the new corner. Never into what the margin holds
    # (`page_needs`). Clicked, a typed number (the margin, in the figure's
    # unit); double-clicked, a window with all four (`page_margins_asked`).
    #: Pane pixels of a blade: half its length, half its width, and how far
    #: its middle sits OUT from the page's edge (clear of the other blade
    #: of its corner and of the page's squares).
    BLADE_HALF = (8.0, 4.0)
    BLADE_OUT = 11.0
    #: The largest margin a blade can make, in cm.
    MOST_MARGIN_CM = 30.0

    #: Each page margin's blade: the page edge it stands on, and the edge
    #: of the page it moves.
    BLADE_EDGES = {"right": "top", "top": "right", "left": "bottom",
                   "bottom": "left"}

    def _page_margin_state(self):
        return self.__dict__.setdefault("_page_margin", {
            "drag": None, "selected": None, "typed": "", "flash": None})

    def page_margins_editable(self):
        """The blades are there on every figure whose page is clicked; only
        an exact figure has margins of its own, and taking a blade on any
        other makes it exact first (`exact_wanted`)."""
        return (self.doc is not None
                and getattr(self, "_page_handles_shown", False))

    def exact_from_screen(self):
        """The layout of an EXACT figure that looks as this one does now:
        the page as big as it is drawn, its margins as they are, in the
        layout's unit. Going exact with the stored size (8.5 x 6.5 cm, or
        whatever was last typed) put every text, drawn in points, out of
        all proportion to a figure laid out on a whole window."""
        layout = self.doc.figure
        per = figure_module.DESIGN_DPI / figure_module.PER_INCH[layout.unit]
        width, height = self.canvas_size()
        rect = self.plot_rect()
        values = {"width": width, "height": height,
                  "margin_left": rect.left(),
                  "margin_right": width - rect.right(),
                  "margin_top": rect.top(),
                  "margin_bottom": height - rect.bottom()}
        return {name: round(max(0.0, value / per), 4)
                for name, value in values.items()}

    def _per_unit(self):
        layout = self.doc.figure
        return (figure_module.PER_INCH[layout.unit]
                / figure_module.DESIGN_DPI)

    # ------------------------------------------------ what a margin holds
    def _ink_blank(self, text, font, edge):
        """How many drawing units of `text`'s box (as `axis_label_rect`
        makes it) are EMPTY on its `edge` ("top" or "bottom") - the font's
        leading and descent, which a margin need not keep (or tightening
        still leaves extra margin where there are axis labels). Drawn once
        off screen and cached."""
        key = (text, font.family(), font.pointSizeF(), font.italic(),
               font.bold(), edge)
        cache = self.__dict__.setdefault("_ink_cache", {})
        if key in cache:
            return cache[key]
        width, height = markup_size(text, font)
        width, height = width + 8.0, height + 2.0
        if width <= 0 or height <= 0:
            cache[key] = 0.0
            return 0.0
        # Four pixels to a drawing unit: a margin to a quarter of one.
        k = 4
        image = QImage(int(math.ceil(width * k)), int(math.ceil(height * k)),
                       QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        p = QPainter(image)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)
            p.scale(k, k)
            draw_markup(p, QRectF(0.0, 0.0, width, height), text, font,
                        QColor(0, 0, 0))
        finally:
            p.end()
        rows = []
        for y in range(image.height()):
            if any(QColor.fromRgba(image.pixel(x, y)).alpha() > 8
                   for x in range(image.width())):
                rows.append(y)
        if not rows:
            blank = 0.0
        elif edge == "top":
            blank = rows[0] / float(k)
        else:
            blank = (image.height() - 1 - rows[-1]) / float(k)
        cache[key] = blank
        return blank

    def page_needs(self):
        """`(left, right, top, bottom)` in drawing units: what each page
        margin HOLDS - an axis's ticks, numbers and caption to the last
        drawn pixel, whatever decorator reaches out of the axes box, else
        the frame's line. What an exact margin is checked against
        (`overflow`) and tightened to."""
        rect = self.plot_rect()
        need = {"left": 1.0, "right": 1.0, "top": 1.0, "bottom": 1.0}

        base = self.figure_font()
        for axis in self.shown_axes():
            side = self.axis_side(axis)
            reach = self.tick_extent(axis)
            if axis.visible and self.doc is not None:
                font = QFont(base)
                font.setPointSizeF(self.style_of(axis, "label_size"))
                text = axis.caption(self.doc)
                # Where the caption's box reaches UNCLAMPED (as
                # `axis_label_rect` lays it out before keeping it on the
                # page): past the ticks, the numbers and the gap.
                depth = markup_size(text, font)[1] + 2.0
                far = reach + self.caption_gap(axis) + depth
                # A y caption is turned 90 degrees anticlockwise: its top
                # faces left, its bottom right. An x caption stands upright.
                facing = {"left": "top", "right": "bottom", "top": "top",
                          "bottom": "bottom"}[side]
                # To the caption's last drawn pixel, and no further.
                need[side] = max(need[side],
                                 far - self._ink_blank(text, font, facing))
                continue
            need[side] = max(need[side], reach + 1.0)
        for side, over in self._numbers_overhang(rect, base).items():
            need[side] = max(need[side], over)
        for artist in decorators_of(self.doc):
            if not getattr(artist, "visible", True):
                continue
            try:
                box = self.rotated_bounds(artist, self.artist_box(artist,
                                                                  rect), rect)
            except Exception:
                continue
            if box is None:
                continue
            need["left"] = max(need["left"], rect.left() - box.left())
            need["right"] = max(need["right"], box.right() - rect.right())
            need["top"] = max(need["top"], rect.top() - box.top())
            need["bottom"] = max(need["bottom"], box.bottom() - rect.bottom())
        return tuple(need[side] for side in figure_module.MARGIN_SIDES)

    def _numbers_overhang(self, rect, base):
        """How far the axes' numbers reach PAST the ends of their axes, to
        their last drawn pixel: `{side: drawing units}`. An x number on a
        tick at the corner of the box is centred on it, so half of it is
        beside the box - over the left margin, which with the y axis on the
        right holds nothing else, and was cut to nothing (the 50 at the
        start of a figure's x axis). A y number likewise above or below."""
        out = {}
        for axis in self.shown_axes():
            if not getattr(axis, "show_numbers", True):
                continue
            font = QFont(base)
            font.setPointSizeF(self.style_of(axis, "tick_size"))
            metrics = QFontMetricsF(font)
            for _value, at, text in self.numbered_ticks(axis, rect):
                ink = metrics.tightBoundingRect(text)
                if ink.isEmpty():
                    continue
                if axis.which == "x":
                    # Centred on the tick (`_number_box`, AlignHCenter).
                    start = at - metrics.horizontalAdvance(text) / 2.0
                    reach = {"left": rect.left() - (start + ink.left()),
                             "right": start + ink.right() - rect.right()}
                else:
                    # Centred on the tick in a box one line high.
                    baseline = at - metrics.height() / 2.0 + metrics.ascent()
                    reach = {"top": rect.top() - (baseline + ink.top()),
                             "bottom": baseline + ink.bottom() - rect.bottom()}
                for side, over in reach.items():
                    if over > out.get(side, 0.0):
                        out[side] = over
        return out

    def least_page_margins(self):
        """`page_needs` in the layout's unit, rounded UP to 0.01: the least
        each page margin can be without cutting anything off."""
        per = self._per_unit()
        return tuple(round(math.ceil(need * per / 0.01 - 1e-6) * 0.01, 4)
                     for need in self.page_needs())

    def least_page_margin(self, side):
        return self.least_page_margins()[
            figure_module.MARGIN_SIDES.index(side)]

    def most_page_margin(self, _side=None):
        """The largest margin a blade makes (`MOST_MARGIN_CM`)."""
        unit = self.doc.figure.unit
        return self.MOST_MARGIN_CM / 2.54 * figure_module.PER_INCH[unit]

    def page_margin_valid(self, side, value):
        return (value is not None and value == value
                and self.least_page_margin(side) - 1e-9 <= value
                <= self.most_page_margin(side) + 1e-9)

    # ------------------------------------------------------ the blades
    def page_edge_px(self, side, value=None):
        """Where the page's `side` edge is on the pane - or would be with
        the margin at `value` (the layout's unit): the page grows or
        shrinks by the difference, the axes box stays."""
        page = self.page_on_pane()
        edge = {"left": page.left(), "right": page.right(),
                "top": page.top(), "bottom": page.bottom()}[side]
        if value is None:
            return edge
        k = self.page()[2]
        delta = (float(value) - getattr(self.doc.figure, "margin_" + side)) \
            / self._per_unit() * k
        return edge + (delta if side in ("right", "bottom") else -delta)

    def page_margin_blade(self, side, value=None):
        """The blade of `side` as a QPainterPath in pane pixels: at the
        page's corner, just outside the edge it stands on, lying along the
        edge it moves."""
        page = self.page_on_pane()
        moved = self.page_edge_px(side, value)
        if side == "right":           # on the top edge, at the right end
            centre = QPointF(moved, page.top() - self.BLADE_OUT)
            along = QPointF(0.0, 1.0)
        elif side == "left":          # on the bottom edge, at the left end
            centre = QPointF(moved, page.bottom() + self.BLADE_OUT)
            along = QPointF(0.0, 1.0)
        elif side == "top":           # on the right edge, at the top end
            centre = QPointF(page.right() + self.BLADE_OUT, moved)
            along = QPointF(1.0, 0.0)
        else:                         # on the left edge, at the bottom end
            centre = QPointF(page.left() - self.BLADE_OUT, moved)
            along = QPointF(1.0, 0.0)
        across = QPointF(along.y(), along.x())
        half, width = self.BLADE_HALF
        tip_a = centre - along * half
        tip_b = centre + along * half
        path = QPainterPath(tip_a)
        path.quadTo(centre + across * (2.0 * width), tip_b)
        path.quadTo(centre - across * (2.0 * width), tip_a)
        path.closeSubpath()
        return path

    def _on_page_square(self, pane_pos):
        """True ON one of the page's squares (no slop): there the page's
        handle wins over a blade beside it."""
        if not getattr(self, "_page_handles_shown", False):
            return False
        return any(square.contains(pane_pos)
                   for _handle, square in self.page_handles())

    def page_margin_blade_at(self, pane_pos):
        """Which page margin's blade the pane point is on, when shown."""
        if not self.page_margins_editable():
            return None
        for side in figure_module.MARGIN_SIDES:
            box = self.page_margin_blade(side).boundingRect()
            if box.adjusted(-3.0, -3.0, 3.0, 3.0).contains(pane_pos):
                return side
        return None

    def _page_margin_at(self, side, pane_pos):
        """The margin, in the layout's unit, that puts the page's edge
        under the pointer - never into what it holds."""
        page = self.page_on_pane()
        k = max(self.page()[2], 1e-9)
        edge = {"left": page.left(), "right": page.right(),
                "top": page.top(), "bottom": page.bottom()}[side]
        at = pane_pos.x() if side in ("left", "right") else pane_pos.y()
        outward = (at - edge) if side in ("right", "bottom") else (edge - at)
        value = (getattr(self.doc.figure, "margin_" + side)
                 + outward / k * self._per_unit())
        return min(max(value, self.least_page_margin(side)),
                   self.most_page_margin(side))

    def _page_margin_readout(self, side, value, typed=None):
        unit = self.doc.figure.unit
        words = "{} WHITE MARGIN".format(side.upper())
        least = self.least_page_margin(side)
        if typed is not None:
            return "{} = {} {} (at least {:.2f}; Enter)".format(
                words, typed, unit, least)
        return "{} {:.2f} {} (at least {:.2f}) - in cuts, out adds".format(
            words, value, unit, least)

    def _start_page_margin_drag(self, side, pane_pos):
        if self.layout_mode() != figure_module.MODE_SIZE:
            self.exact_wanted.emit()
            if self.layout_mode() != figure_module.MODE_SIZE:
                return
        state = self._page_margin_state()
        value = getattr(self.doc.figure, "margin_" + side)
        state["drag"] = {"side": side, "start": QPointF(pane_pos),
                         "value": value, "moved": False}
        state["typed"] = ""
        self._margin_state()["selected"] = None
        self.hovered.emit(self._page_margin_readout(side, value))
        self.update()

    def _drag_page_margin(self, pane_pos):
        drag = self._page_margin_state()["drag"]
        if not drag["moved"]:
            dist = max(abs(pane_pos.x() - drag["start"].x()),
                       abs(pane_pos.y() - drag["start"].y()))
            if dist < DRAG_SLOP:
                return
            drag["moved"] = True
        drag["value"] = self._page_margin_at(drag["side"], pane_pos)
        self.hovered.emit(self._page_margin_readout(drag["side"],
                                                    drag["value"]))
        self.update()

    def _finish_page_margin_drag(self, cancel=False):
        state = self._page_margin_state()
        drag, state["drag"] = state["drag"], None
        if drag is None:
            return
        state["selected"] = drag["side"]
        if cancel:
            self.update()
            return
        if drag["moved"]:
            self.page_margin_done.emit(drag["side"],
                                       round(drag["value"], 4))
        else:
            state["typed"] = ""
            self.setFocus(Qt.MouseFocusReason)
            self.hovered.emit("{}: type it in {}, Enter; double-click for "
                              "all four".format(
                                  self._page_margin_readout(
                                      drag["side"], getattr(
                                          self.doc.figure,
                                          "margin_" + drag["side"])),
                                  self.doc.figure.unit))
        self.update()

    def page_margin_selected(self):
        return self._page_margin_state()["selected"]

    def select_page_margin(self, side):
        state = self._page_margin_state()
        state["selected"] = side
        state["typed"] = ""
        self.update()

    def tighten_page_margin(self, side):
        """That margin down to what it holds (the page shrinks)."""
        self.page_margin_done.emit(side, self.least_page_margin(side))

    def type_page_margin(self, text):
        """A typed page margin for the selected blade, in the figure's
        unit. True if taken; one that would cut something off flashes the
        page's edge red."""
        state = self._page_margin_state()
        side = state["selected"]
        if side is None:
            return False
        value = numbers.evaluate(text)
        state["typed"] = ""
        if not self.page_margin_valid(side, value):
            state["flash"] = (side, time.monotonic()
                              + self.MARGIN_FLASH_SECONDS)
            QTimer.singleShot(int(self.MARGIN_FLASH_SECONDS * 1000) + 20,
                              self.update)
            self.hovered.emit(
                "{} {} would not do: at least {:.2f} {} (less cuts off "
                "what the margin holds)".format(
                    text, self.doc.figure.unit,
                    self.least_page_margin(side), self.doc.figure.unit))
            self.update()
            return False
        self.page_margin_done.emit(side, round(float(value), 4))
        self.update()
        return True

    def _page_margin_key(self, ev):
        """Keys for a selected blade. True if the key was used."""
        state = self._page_margin_state()
        side = state["selected"]
        if side is None:
            return False
        if not self.page_margins_editable():
            state["selected"], state["typed"] = None, ""
            return False
        key, text = ev.key(), ev.text()
        if key == Qt.Key_Escape:
            if state["typed"]:
                state["typed"] = ""
            else:
                state["selected"] = None
            self.hovered.emit("")
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            if state["typed"]:
                self.type_page_margin(state["typed"])
        elif key == Qt.Key_Backspace:
            state["typed"] = state["typed"][:-1]
            self.hovered.emit(self._page_margin_readout(side, 0.0,
                                                        state["typed"]))
        elif text and (text.isdigit() or text in ".,+-*/()"):
            state["typed"] += text
            self.hovered.emit(self._page_margin_readout(side, 0.0,
                                                        state["typed"]))
        else:
            return False
        self.update()
        return True

    def _paint_page_margin_blades(self, p):
        """The four blades, and the dashed edge of the page the one
        dragged, typed or refused would make."""
        if not self.page_margins_editable():
            return
        state = self._page_margin_state()
        drag = state["drag"]
        flash = state["flash"]
        if flash is not None and time.monotonic() > flash[1]:
            flash = state["flash"] = None
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        line_side, line_value, colour = None, None, QColor(_SELECT)
        if drag is not None and drag["moved"]:
            line_side, line_value = drag["side"], drag["value"]
        elif state["selected"] and state["typed"]:
            value = numbers.evaluate(state["typed"])
            if self.page_margin_valid(state["selected"], value):
                line_side, line_value = state["selected"], value
        if flash is not None:
            line_side, line_value = flash[0], None
            colour = QColor(_ALARM)
        if line_side is not None:
            page = self.page_on_pane()
            at = self.page_edge_px(line_side, line_value)
            if line_side in ("left", "right"):
                a, b = QPointF(at, page.top()), QPointF(at, page.bottom())
            else:
                a, b = QPointF(page.left(), at), QPointF(page.right(), at)
            p.setPen(QPen(colour, 2.0 if flash is not None else 1.2,
                          Qt.DashLine))
            p.drawLine(a, b)
        for side in figure_module.MARGIN_SIDES:
            value = (drag["value"] if drag and drag["moved"]
                     and drag["side"] == side else None)
            blade = self.page_margin_blade(side, value)
            active = (side == state["selected"]
                      or (drag is not None and drag["side"] == side))
            refused = flash is not None and flash[0] == side
            edge = QColor(_ALARM) if refused else QColor(_SELECT)
            p.setPen(QPen(edge, 1.2))
            p.setBrush(edge if active or refused else QColor(255, 255, 255))
            p.drawPath(blade)
        p.restore()

    #: The pointer over each handle.
    HANDLE_CURSORS = {(0.0, 0.0): Qt.SizeFDiagCursor,
                      (1.0, 1.0): Qt.SizeFDiagCursor,
                      (1.0, 0.0): Qt.SizeBDiagCursor,
                      (0.0, 1.0): Qt.SizeBDiagCursor,
                      (0.0, 0.5): Qt.SizeHorCursor,
                      (1.0, 0.5): Qt.SizeHorCursor,
                      (0.5, 0.0): Qt.SizeVerCursor,
                      (0.5, 1.0): Qt.SizeVerCursor}

    def fit_page(self):
        """Alt+F: the page back to fit the pane."""
        self._page_zoom = 1.0
        self._page_pan = (0.0, 0.0)
        self.invalidate()

    def _page_fit(self):
        """The page as it fits the pane, before its own zoom and pan. A
        figure of exact size is scaled to fit; the drawing inside it is not
        re-laid-out."""
        mode = self.layout_mode()
        if mode == figure_module.MODE_WINDOW:
            return 0.0, 0.0, 1.0
        canvas_w, canvas_h = self.canvas_size()
        width, height = float(self.width()), float(self.height())
        k = 1.0
        if mode == figure_module.MODE_SIZE:
            k = max(0.05, min((width - 2 * self.PAGE_PAD) / canvas_w,
                              (height - 2 * self.PAGE_PAD) / canvas_h))
        return (width - canvas_w * k) / 2.0, (height - canvas_h * k) / 2.0, k

    def to_figure(self, pos):
        """A point in the pane, in the figure's drawing units."""
        dx, dy, k = self.page()
        return QPointF((pos.x() - dx) / k, (pos.y() - dy) / k)

    def to_widget(self, point):
        """A point of the figure, in the pane's pixels."""
        dx, dy, k = self.page()
        return QPointF(point.x() * k + dx, point.y() * k + dy)

    def _slop(self):
        """`DRAG_SLOP` screen pixels, in drawing units."""
        return DRAG_SLOP / self.page()[2]

    def margins(self):
        """`(left, right, top, bottom)`, in drawing units.

        EXACT for a figure of exact size: the axes box goes where the margins
        put it and nowhere else, and the numbers and captions live inside
        them (`overflow` says what does not fit). Two figures with the same
        size and margins then have the same axes box to the hundredth of a
        millimetre, whatever their numbers say - two stacks side by side in
        Word line up.

        Otherwise sized to the FONTS: the numbers and captions decide how
        much room the plot gives them, on whichever side each axis is.
        """
        doc = self.doc
        if doc is None:
            return 34.0, float(_RIGHT), float(_TOP), 26.0
        if self.layout_mode() == figure_module.MODE_SIZE:
            return doc.figure.margins_px()
        return self.needed_margins()

    def needed_margins(self):
        """What the numbers and captions need on each side, in drawing
        units: the automatic margins, and what an exact one is checked
        against."""
        doc = self.doc
        sides = {"left": 8.0, "right": float(_RIGHT), "top": float(_TOP),
                 "bottom": 8.0}
        for axis in self.shown_axes():
            side = self.axis_side(axis)
            sides[side] = max(sides[side], self.axis_reach(axis))
        return sides["left"], sides["right"], sides["top"], sides["bottom"]

    def shown_axes(self):
        """The axes drawn: x and y."""
        doc = self.doc
        if doc is None:
            return []
        return [doc.axes["x"], doc.axes["y"]]

    def shown_y_axes(self):
        """The y axes drawn: the one."""
        doc = self.doc
        return [] if doc is None else [doc.axes["y"]]

    def y_mirrored(self, axis):
        """True when the y axis closes the box on the far side."""
        return bool(getattr(axis, "mirror", False))

    def overflow(self):
        """`[(side, needed, set), ...]` in drawing units, for each margin of
        a figure of exact size that is too narrow for what it holds."""
        if self.doc is None or self.layout_mode() != figure_module.MODE_SIZE:
            return []
        out = []
        # What each margin HOLDS, not the automatic layout's breathing
        # room: an exact 0.3 cm right margin with no axis on it was
        # reported as cutting numbers off.
        for side, need, have in zip(("left", "right", "top", "bottom"),
                                    self.page_needs(), self.margins()):
            if need > have + 0.5:
                out.append((side, need, have))
        return out

    def axis_side(self, axis):
        """Which side of the axes box an axis is drawn on."""
        side = getattr(axis, "side", None)
        if axis.which == "x":
            return side if side in ("bottom", "top") else "bottom"
        return side if side in ("left", "right") else "left"

    def axis_reach(self, axis):
        """How far an axis's ticks, numbers and caption reach out from the
        axes box, in drawing units."""
        reach = self.tick_extent(axis)
        if axis.visible:
            font = QFont(self.font())
            font.setPointSizeF(self.style_of(axis, "label_size"))
            reach += self.caption_gap(axis) + QFontMetrics(font).height() + 4
        return reach + 2.0

    def caption_gap(self, axis):
        """Pixels between an axis's numbers and its caption."""
        return float(_clamp(self.style_of(axis, "label_gap"), 0.0, 80.0))

    def _tick_out(self, axis):
        """How far ticks pointing OUT of the box reach into the margin."""
        return (0.0 if axis.ticks_inward
                else float(getattr(axis, "tick_length", 7.0)))

    def tick_extent(self, axis):
        """How far an axis's ticks and numbers reach out from it, in drawing
        units: the numbers' height beside the x axis, the widest of them
        beside the y axis - the edge a caption keeps its distance from."""
        out = self._tick_out(axis)
        if not getattr(axis, "show_numbers", True):
            return out + 3.0
        font = QFont(self.font())
        font.setPointSizeF(self.style_of(axis, "tick_size"))
        metrics = QFontMetrics(font)
        if axis.which == "x":
            return out + 3.0 + metrics.height()
        return out + 8.0 + self._widest_number(axis, metrics)

    def tick_step(self, axis, lo, hi):
        """The spacing of an axis's numbered ticks: the axis's own
        `major_step`, or a round number that gives about eight (x) or six
        (y). A step so small it would draw hundreds falls back to the
        automatic one."""
        which = getattr(axis, "which", "x")
        chosen = getattr(axis, "major_step", None)
        span = hi - lo
        if not (np.isfinite(span) and span > 0):
            # Never reached from a range this program fits (they skip
            # flagged samples), but a NaN here raised inside paintEvent,
            # which aborts the program (review F11).
            span = 1.0
        if chosen and chosen > 0 and span / float(chosen) <= 200:
            return float(chosen)
        return _nice_step(span, 8 if which == "x" else 6)

    def _widest_number(self, axis, metrics):
        """The widest number the y axis writes."""
        which = getattr(axis, "which", "y")
        lo, hi = self.view_y()
        step = self.tick_step(axis, lo, hi)
        widest = 0
        value = math.ceil(lo / step) * step
        while value <= hi + 1e-9:
            if not self.number_hidden(axis, value, step):
                widest = max(widest, metrics.horizontalAdvance(
                    self.tick_text(axis, value, which)))
            value += step
        return float(widest)

    def number_hidden(self, axis, value, step):
        """True for a numbered tick whose number is not written: one of
        `axis.hidden_numbers`, chosen in the unit the axis is in now."""
        hidden = getattr(axis, "hidden_numbers", None)
        if not hidden or self.doc is None:
            return False
        if list(getattr(axis, "hidden_context", None) or []) != \
                self.axis_context(axis.which):
            return False
        # The tick is stepped to in floating point: 50.00000000001.
        near = abs(float(step)) * 1e-6
        return any(abs(value - float(h)) <= near for h in hidden)

    def _axis_view(self, which):
        """`((lo, hi), to_px)` of axis `which`, as `_paint_grid` uses them."""
        if which == "x":
            return self.view_x(), self.x_to_px
        return self.view_y(), self.y_to_px

    def numbered_ticks(self, axis, rect=None, hidden=False):
        """`[(value, at, text), ...]`: the numbers `axis` writes - each
        numbered tick's value, where it is along the axis in drawing units,
        and its text. The hidden ones are left out unless `hidden`."""
        rect = rect or self.plot_rect()
        which = axis.which
        (lo, hi), to_px = self._axis_view(which)
        step = self.tick_step(axis, lo, hi)
        out = []
        if not getattr(axis, "show_numbers", True):
            return out
        value = math.ceil(lo / step) * step
        while value <= hi + 1e-9:
            if which == "x" and self.in_break(value):
                value += step
                continue
            if hidden or not self.number_hidden(axis, value, step):
                out.append((value, float(to_px(value, rect)),
                            self.tick_text(axis, value, which)))
            value += step
        return out

    def number_step(self, axis):
        """The spacing of `axis`'s numbered ticks in the current view."""
        (lo, hi), _to_px = self._axis_view(axis.which)
        return self.tick_step(axis, lo, hi)

    def number_boxes(self, axis, rect=None):
        """`[(value, text, box, hidden), ...]`: where each number of `axis`
        is written, as `_paint_grid` places it - the hidden ones too, so
        one can be found again to show it."""
        rect = rect or self.plot_rect()
        if not getattr(axis, "show_numbers", True):
            return []
        font = QFont(self.figure_font())
        font.setPointSizeF(self.style_of(axis, "tick_size"))
        metrics = QFontMetrics(font)
        side = self.axis_side(axis)
        step = self.number_step(axis)
        out = []
        for value, at, text in self.numbered_ticks(axis, rect, hidden=True):
            box, _align = self._number_box(axis, side, at, text, metrics,
                                           rect)
            out.append((value, text, box,
                        self.number_hidden(axis, value, step)))
        return out

    def number_at(self, pos):
        """`(axis, value, text, hidden)` for the axis number nearest `pos`
        (figure units) in a numbers band, or None: what a right-click there
        hides or shows again."""
        if self.doc is None or pos is None:
            return None
        rect = self.plot_rect()
        best = None
        for axis in self.shown_axes():
            if not self.axis_numbers_rect(axis.which).contains(pos):
                continue
            for value, text, box, hidden in self.number_boxes(axis, rect):
                # An x number's box is twice its width, centred on its
                # tick: the gap to the middle decides.
                gap = math.hypot(box.center().x() - pos.x(),
                                 box.center().y() - pos.y())
                if best is None or gap < best[0]:
                    best = (gap, axis, value, text, hidden)
        return best[1:] if best is not None else None

    def _number_box(self, axis, side, at, text, metrics, rect):
        """`(box, align)`: where `_paint_grid` writes one number."""
        clear = self._tick_out(axis)
        line = {"bottom": rect.bottom(), "top": rect.top(),
                "left": rect.left(), "right": rect.right()}[side]
        width = metrics.horizontalAdvance(text)
        height = metrics.height()
        if side == "bottom":
            return (QRectF(at - width, line + clear + 3, 2 * width,
                           height + 2), Qt.AlignHCenter)
        if side == "top":
            return (QRectF(at - width, line - clear - 3 - height - 2,
                           2 * width, height + 2),
                    int(Qt.AlignHCenter | Qt.AlignBottom))
        if side == "left":
            return (QRectF(line - clear - 8 - width, at - height / 2.0,
                           width, height),
                    int(Qt.AlignRight | Qt.AlignVCenter))
        return (QRectF(line + clear + 8, at - height / 2.0, width, height),
                int(Qt.AlignLeft | Qt.AlignVCenter))

    @staticmethod
    def tick_text(axis, value, which):
        """An axis number: the axis's own format, or as few digits as the
        tick spacing needs."""
        spec = getattr(axis, "number_format", None)
        if spec:
            return numbers.write(value, spec)
        # "+ 0.0": a tick stepped onto zero in floating point is -2.8e-17,
        # which rounds to -0.0 and was written "-0" (in one real file).
        return "{:g}".format(round(value, 6 if which == "x" else 10) + 0.0)

    def plot_rect(self):
        """The axes box, in drawing units - FRACTIONAL, so an exact margin
        stays exact rather than being rounded to a whole unit (a quarter of
        a millimetre, six pixels at 600 dpi)."""
        left, right, top, bottom = self.margins()
        canvas_w, canvas_h = self.canvas_size()
        return QRectF(left, top, max(10.0, canvas_w - left - right),
                      max(10.0, canvas_h - top - bottom))

    # The x axis: turned round on a d axis (`profile.x_reversed`) and
    # possibly BROKEN (`Document.x_break`). The break is a piecewise-linear
    # warp of x, so the view stays (low, high) on the axis and every x in
    # the program goes through these.
    def x_break(self):
        """`(lo, hi, compress, gap)` of the x axis's break, or None."""
        return model.break_of(self.doc) if self.doc is not None else None

    def warp_x(self, value):
        """An x in the axis's warped space: itself, or with the axis
        broken, the stretch lo..hi squeezed to `compress` of its width and
        everything above it moved down by what was taken out."""
        value = np.asarray(value, dtype=float)
        cut = self.x_break()
        if cut is None:
            return value
        lo, hi, compress, _gap = cut
        width = hi - lo
        return np.where(value <= lo, value,
                        np.where(value >= hi, value - width + width * compress,
                                 lo + (value - lo) * compress))

    def unwarp_x(self, warped):
        """`warp_x` backwards."""
        warped = np.asarray(warped, dtype=float)
        cut = self.x_break()
        if cut is None:
            return warped
        lo, hi, compress, _gap = cut
        width = hi - lo
        kept = width * compress
        return np.where(warped <= lo, warped,
                        np.where(warped >= lo + kept, warped + width - kept,
                                 lo + (warped - lo) / compress))

    def x_to_px(self, value, rect=None, view=None):
        rect = rect or self.plot_rect()
        lo, hi = view or self.view_x()
        u0, u1 = float(self.warp_x(lo)), float(self.warp_x(hi))
        frac = (self.warp_x(value) - u0) / max(u1 - u0, 1e-12)
        if profile.x_reversed(self.doc):
            frac = 1.0 - frac
        out = rect.left() + frac * rect.width()
        return float(out) if np.ndim(out) == 0 else out

    def px_to_x(self, px, rect=None, view=None):
        rect = rect or self.plot_rect()
        lo, hi = view or self.view_x()
        frac = (np.asarray(px, dtype=float) - rect.left()) / max(
            1.0, rect.width())
        if profile.x_reversed(self.doc):
            frac = 1.0 - frac
        u0, u1 = float(self.warp_x(lo)), float(self.warp_x(hi))
        out = self.unwarp_x(u0 + frac * (u1 - u0))
        return float(out) if np.ndim(out) == 0 else out

    def shifted_x(self, view, dx_px, rect=None):
        """The x view `view` moved `dx_px` pixels towards the right of the
        page, in the warped space (so across a break, too)."""
        rect = rect or self.plot_rect()
        lo, hi = view
        u0, u1 = float(self.warp_x(lo)), float(self.warp_x(hi))
        du = dx_px / max(1.0, rect.width()) * (u1 - u0)
        if profile.x_reversed(self.doc):
            du = -du
        return (float(self.unwarp_x(u0 + du)), float(self.unwarp_x(u1 + du)))

    def zoomed_x(self, view, anchor, factor):
        """The x view `view` zoomed `factor` times about the position
        `anchor`, which keeps its place on the page."""
        lo, hi = view
        u0, u1 = float(self.warp_x(lo)), float(self.warp_x(hi))
        ua = float(self.warp_x(anchor))
        span = (u1 - u0) / factor
        frac = (ua - u0) / max(u1 - u0, 1e-12)
        return (float(self.unwarp_x(ua - span * frac)),
                float(self.unwarp_x(ua + span * (1 - frac))))

    def x_distance_px(self, distance, rect=None, at=None):
        """How many pixels `distance` (x axis) takes on the page, towards
        higher positions (negative with the axis reversed), at `at`."""
        rect = rect or self.plot_rect()
        at = self.px_to_x(rect.center().x(), rect) if at is None else at
        return (float(self.x_to_px(at + distance, rect))
                - float(self.x_to_px(at, rect)))

    def y_to_px(self, value, rect=None, view=None):
        rect = rect or self.plot_rect()
        lo, hi = view or self.view_y()
        return (rect.top() + rect.height()
                * (1.0 - (value - lo) / max(hi - lo, 1e-12)))

    def px_to_y(self, px, rect=None, view=None):
        rect = rect or self.plot_rect()
        lo, hi = view or self.view_y()
        return lo + (1.0 - (px - rect.top()) / max(1.0, rect.height())) * (hi - lo)

    def y_per_px(self, rect=None, scan=None):
        rect = rect or self.plot_rect()
        lo, hi = self.view_y()
        return (hi - lo) / max(1.0, rect.height())

    # One y axis. The family's handling asks for a scan's own axis and for
    # the MAIN one (a sibling may have a second); here they are the y axis,
    # kept as names so the handling reads the same.
    def view_for(self, _scan):
        return self.view_y()

    def sy_to_px(self, _scan, value, rect=None):
        return self.y_to_px(value, rect)

    def px_to_sy(self, _scan, px, rect=None):
        return self.px_to_y(px, rect)

    def main_axis(self):
        return "y"

    def main_view(self):
        return self.view_y()

    def set_main_view(self, lo, hi):
        self.set_view_y(lo, hi)

    def _set_main_stored(self, pair):
        """Store the y range without the redraw `set_view_y` asks for (a
        pan in progress blits the cache)."""
        self._view_y = pair

    def artist_scan(self, artist):
        """The scan an artist placed in data units belongs to, or None."""
        return getattr(artist, "scan", None)

    def ay_to_px(self, _artist, value, rect=None):
        return self.y_to_px(value, rect)

    def px_to_ay(self, _artist, px, rect=None):
        return self.px_to_y(px, rect)

    # ------------------------------------------------------------ navigation
    def cycle_mode(self, cycle):
        current = self._mode if self._mode in cycle else None
        index = cycle.index(current) if current in cycle else -1
        self.set_mode(cycle[(index + 1) % len(cycle)])

    def set_mode(self, mode):
        self._drag = None
        self._mode = mode
        self._box = None
        self._sync_pointer()
        self.mode_changed.emit(self.MODE_TEXT.get(mode, self.SELECT_TEXT))
        self.update()

    def mode(self):
        return self._mode

    def _sync_pointer(self):
        """Hide the system pointer where the reticle is standing in for it.

        Two pointers on one plot is one too many, and the reticle IS the
        pointer in plain select mode. A zoom or pan mode brings its own
        cursor back, and so does the area outside the axes, where there is no
        reticle to take over.
        """
        nav = getattr(self, "_nav", None)
        if nav is not None:
            self.setCursor(self.NAV_CURSOR.get(nav["kind"], Qt.ArrowCursor))
            return
        if self._mode:
            self.setCursor(self.MODE_CURSOR.get(self._mode, Qt.ArrowCursor))
            return
        inside = (self._cursor is not None
                  and self.plot_rect().contains(self._cursor))
        if inside and self._measure is not None:
            # The reticle is not drawn while cursors or gizmos are up, so
            # the system pointer must be: hidden, it left nothing on screen
            # to aim at a gizmo with. Over a handle
            # it says the handle moves sideways.
            over = (self._cursor_drag is not None
                    or self.cursor_at(self._cursor) is not None)
            self.setCursor(Qt.SizeHorCursor if over else Qt.CrossCursor)
            return
        self.setCursor(Qt.BlankCursor if inside else Qt.ArrowCursor)

    def wheelEvent(self, ev):
        """Trackpad first.

        * **Two fingers = pan.** A swipe moves the view by the distance the
          fingers moved, in both directions, because that is what dragging a
          sheet of paper does. With Shift the axes swap, so a horizontal pan
          is available on a trackpad that only reports vertical scrolling.
        * **Pinch = zoom.** A Windows precision touchpad sends a pinch as
          Ctrl+wheel, so Ctrl+wheel zooms BOTH axes about the cursor. (A
          native pinch gesture, where Qt delivers one, arrives in `event`
          below and lands in the same place.)
        * **The plain swipe or wheel makes every curve taller or flatter
          IN ITS PLACE** (`scale_intensity`): MestReNova's gesture, and
          a PXRD viewer's. Each curve grows
          about its own middle height and none of them moves up or down.

        Still, the data is never scaled by a gesture - a curve whose height
        changed under the hand would be a curve whose axis is a lie. The
        AXIS is scaled about y = 0, and each curve's offset follows so that
        it keeps its place: the y numbers, where shown, stay true. (Scaling
        the axis about y = 0 alone spread the stack apart as it grew; S with
        P does that.)

        On a mouse the swipe is the MIDDLE-BUTTON DRAG, as in Blender, with
        the same modifiers (`nav_kind`).
        """
        mods = ev.modifiers()
        pixels = ev.pixelDelta()
        angles = ev.angleDelta()
        if mods & Qt.AltModifier:
            self._wheel_page(ev, mods, pixels, angles)
            ev.accept()
            return
        if not pixels.isNull():
            dx, dy = float(pixels.x()), float(pixels.y())
        else:
            # Some touchpads report notches rather than pixels, so they are
            # converted to a distance instead of being read as "a mouse
            # wheel". Treating them as a wheel is what made a two-finger
            # swipe zoom when it should have panned.
            dx = angles.x() / PANE_WHEEL_UNITS * PANE_STEP_PIXELS
            dy = angles.y() / PANE_WHEEL_UNITS * PANE_STEP_PIXELS
        if mods & Qt.ControlModifier:
            # Pinch, which Windows delivers as Ctrl+wheel: zoom both axes
            # about the cursor.
            notches = (dy or dx) / PANE_STEP_PIXELS
            if notches:
                self._view_touched("zoom")
                self.zoom_at(self.to_figure(ev.position()),
                             WHEEL_STEP ** notches, both=True)
        elif mods & Qt.ShiftModifier:
            # Shift turns the swipe into a PAN, and an omnidirectional one:
            # both components are used as they arrive, so the canvas follows
            # the fingers instead of being locked to an axis.
            if dx or dy:
                self._view_touched("pan")
                self.pan_by(-dx, -dy)
        elif dy or dx:
            # The plain swipe: every curve taller or flatter in its place,
            # the gesture a stack needs most (`scale_intensity`).
            self._view_touched("taller or flatter")
            self.scale_intensity(WHEEL_STEP ** ((dy or dx)
                                                / PANE_STEP_PIXELS))
        ev.accept()

    def _wheel_page(self, ev, mods, pixels, angles):
        """Alt: the PAGE, never the data. A swipe zooms it, like a
        document; with Shift the swipe moves it around. The data axes and
        the figure's proportions are left alone.

        Windows reports every Alt+wheel as horizontal (it is how Alt gives
        sideways scrolling to a mouse), so the amount is whichever component
        arrived and the DIRECTION is the one the system message said
        (`native_wheel`).
        """
        if not pixels.isNull():
            amount_x, amount_y = float(pixels.x()), float(pixels.y())
        else:
            amount_x = angles.x() / PANE_WHEEL_UNITS * PANE_STEP_PIXELS
            amount_y = angles.y() / PANE_WHEEL_UNITS * PANE_STEP_PIXELS
        amount = amount_y or amount_x
        if mods & Qt.ShiftModifier:
            if amount_x and amount_y:
                self.pan_page(amount_x, amount_y)
            elif PlotWidget.native_wheel == "horizontal":
                self.pan_page(amount, 0.0)
            else:
                self.pan_page(0.0, amount)
            return
        if amount:
            self.zoom_page(WHEEL_STEP ** (amount / PANE_STEP_PIXELS),
                           ev.position())

    # ----------------------------------------------------- the middle button
    @staticmethod
    def nav_kind(mods):
        """What a middle-button drag does: the swipe with the same modifier.

        A two-finger swipe on a trackpad is a middle-button drag on a mouse
        (Blender's navigation button). Plain makes the curves taller or
        flatter in their places (`scale_intensity`), Shift pans,
        Ctrl zooms both axes, Alt the page (Shift moves it). Latched at the
        press, so a modifier touched mid-drag does not change what the hand
        is doing.
        """
        if mods & Qt.AltModifier:
            return "page_pan" if mods & Qt.ShiftModifier else "page_zoom"
        if mods & Qt.ControlModifier:
            return "zoom"
        if mods & Qt.ShiftModifier:
            return "pan"
        return "scale"

    def _start_nav(self, ev):
        kind = self.nav_kind(ev.modifiers())
        at = QPointF(ev.position())
        self._nav = {"kind": kind, "at": at, "last": at}
        if not kind.startswith("page"):
            # The page is looking closer, not a framing: never an undo step.
            self._view_begin("pan" if kind == "pan" else "zoom")
        self._sync_pointer()
        self.hovered.emit(self.NAV_TEXT[kind])
        self.update()

    def _drag_nav(self, at):
        """One move of a middle-button drag, by the pane pixels since the
        last. The same distance as a swipe does the same amount: one
        `PANE_STEP_PIXELS` is one wheel notch. Up is taller, and closer."""
        nav = self._nav
        dx, dy = at.x() - nav["last"].x(), at.y() - nav["last"].y()
        nav["last"] = QPointF(at)
        if not dx and not dy:
            return
        factor = WHEEL_STEP ** (-dy / PANE_STEP_PIXELS)
        kind = nav["kind"]
        if kind == "scale":
            self.scale_intensity(factor)
        elif kind == "zoom":
            self.zoom_at(self.to_figure(nav["at"]), factor, both=True)
        elif kind == "pan":
            k = self.page()[2]
            self.pan_by(-dx / k, -dy / k)
        elif kind == "page_zoom":
            self.zoom_page(factor, nav["at"])
        else:
            self.pan_page(dx, dy)

    def _end_nav(self):
        self._nav = None
        self.invalidate()
        self.commit_view()
        self._sync_pointer()
        self.hovered.emit("")
        self.update()

    def event(self, ev):
        """Catch a native pinch, where the platform sends one - and, while
        S or R is live, claim every key before the window's shortcuts see
        it: M there chooses a pivot, not the x range."""
        # getattr: Qt sends events while the widget is still being built.
        if (getattr(self, "_scale", None) is not None
                and ev.type() == ev.Type.ShortcutOverride):
            ev.accept()
            return True
        try:
            is_gesture = ev.type() == ev.Type.NativeGesture
        except AttributeError:
            is_gesture = False
        if is_gesture and ev.gestureType() == Qt.ZoomNativeGesture:
            self._view_touched("zoom")
            self.zoom_at(self.to_figure(ev.position()),
                         1.0 + float(ev.value()), both=True)
            return True
        return QWidget.event(self, ev)

    def zoom_at(self, pos, factor, both=False):
        """Zoom the view about the cursor: y always, x as well when `both`."""
        if factor <= 0:
            return
        lo, hi = self.main_view()
        anchor = self.px_to_y(pos.y(), view=(lo, hi))
        span = (hi - lo) / factor
        frac = (anchor - lo) / max(hi - lo, 1e-12)
        self.set_main_view(anchor - span * frac, anchor + span * (1 - frac))
        if both:
            self.set_view_x(*self.zoomed_x(self.view_x(),
                                           self.px_to_x(pos.x()), factor))

    def scale_y(self, factor):
        """Scale the main y axis about y = 0: zero keeps its place on
        screen. (The other y axis, when both are drawn, follows M and F.)"""
        if factor <= 0:
            return
        lo, hi = self.main_view()
        self.set_main_view(lo / factor, hi / factor)

    def scale_intensity(self, factor):
        """The plain swipe and the middle drag: every curve on the main y
        axis `factor` times taller, about its OWN middle height
        (`_level_of`), each where it is on the page. Nothing is multiplied:
        the axis is scaled about y = 0 (`scale_y`) and each curve's offset
        is changed so its middle height keeps its place - the data stays as
        measured, and the y numbers, where shown, stay true. Hidden curves
        on the axis follow too, so they come back in their places. The
        other y axis is left alone. One undo step per gesture, the offsets
        with the framing (`commit_view`)."""
        doc = self.doc
        if factor <= 0 or doc is None:
            return
        chosen = self.scaled_selection()
        if chosen:
            self.scale_patterns(chosen, factor)
            return
        # The range BEFORE the offsets move: a fitted one would otherwise
        # refit to the new stack first, and every curve jumped.
        lo, hi = self.main_view()
        mass = self.main_axis() == "y2"
        for scan in doc.scans:
            if bool(getattr(scan, "is_mass", False)) != mass:
                continue
            rest = self._level_of(scan)
            scan.offset = (float(scan.offset) + rest) / factor - rest
        self.rebuild()
        self.set_main_view(lo / factor, hi / factor)

    def scaled_selection(self):
        """The patterns a swipe scales ONE BY ONE: the selected ones that
        are drawn with a height (not lines across the plot). Empty: the
        swipe scales every curve, as the axis."""
        doc = self.doc
        if doc is None:
            return []
        return [s for s in doc.selected_scans()
                if s.visible and not s.draws_lines
                and s.missing_for(doc) is None]

    def scale_patterns(self, scans, factor):
        """The swipe on a selection: each of `scans` `factor` times taller
        in its place, by its own `multiplier` (`Document.rescaled`) - the
        other curves and the axis untouched. A pattern leaving x1 gets its
        "xN" label, hanging above its curve's left end, unless it has one;
        a label taken away is not put back. One undo step per gesture, with
        the framing (`commit_view`)."""
        doc = self.doc
        rect = self.plot_rect()
        for scan in scans:
            was = float(scan.multiplier or 1.0)
            for obj, attr, value in doc.rescaled(scan, was * factor):
                setattr(obj, attr, value)
            if abs(was - 1.0) < 1e-9 and doc.multiplier_label_of(scan) is None:
                trace = self._trace_of(scan)
                k = self.end_sample(trace, "left", rect)
                if k is not None:
                    doc.labels.append(doc.new_multiplier_label(
                        scan, ("i", trace.first + int(k))))
        self.rebuild()

    def _level_of(self, scan):
        """Where a curve is above its own offset, drawn or not: the
        baseline of its kept values as the axis shows them, the offset
        taken out - what a baseline is depends on the data
        (`profile.baseline`: a pattern's background under its peaks).
        0 for a curve that cannot be drawn."""
        trace = self._trace_of(scan)
        if trace is None and self.doc is not None:
            trace = self._make_trace(self.doc, scan)
        if trace is None or trace.y is None or not len(trace.y):
            return 0.0
        values = np.asarray(trace.y, dtype=float)
        values = values[np.isfinite(values)]
        if not len(values):
            return 0.0
        return (profile.baseline(values, self.doc)
                - float(scan.offset))

    def pan_by(self, dx_px, dy_px):
        """Move the view by a distance in PIXELS."""
        rect = self.plot_rect()
        y0, y1 = self.main_view()
        dy = -dy_px / max(1.0, rect.height()) * (y1 - y0)
        self._view_x = self.shifted_x(self.view_x(), dx_px, rect)
        self._set_main_stored((y0 + dy, y1 + dy))
        self.invalidate()
        self.view_changed.emit()

    # ------------------------------------------------------------- selection
    def pick_radius(self):
        """How close, in pixels, the pointer must be to an object to mean it.

        The user's own setting (Edit > Settings, "Pick distance"): it decides
        what a press lands on - and so whether a drag acts on an object or
        draws a box - and a trackpad and a mouse want different numbers.
        """
        # In PANE pixels, whatever the figure's scale on screen.
        return float(style.preference("pick_radius")) / self.page()[2]

    def objects_at(self, pos, radius=None):
        """What is under or NEAR the cursor, nearest first.

        Everything within the pick distance counts, measured to the drawn
        curve or to the edge of a label's box, and the nearest wins; a tie
        (the pointer inside two things) goes to whatever is drawn on top -
        artists over analysis labels over curves. It used to be exact boxes
        in a fixed order, which made a label a much smaller target than a
        curve, and "near" meant something different for each kind.

        The axis captions and spines keep their own exact bands: they sit in
        the margin, and a generous radius there would swallow the corner of
        the plot where a box select starts.
        """
        doc = self.doc
        if doc is None:
            return []
        radius = self.pick_radius() if radius is None else float(radius)
        # Either flavour of point: a mouse event carries a QPointF, a
        # rectangle's centre a QPoint, and both are handed to this.
        point = QPointF(pos.x(), pos.y())
        hits = []                  # (distance, rank, obj, axis hit or None)

        def near(obj, box, rank):
            gap = _rect_distance(QRectF(box), point)
            if gap <= radius:
                hits.append((gap, rank, obj, None))

        # A tie - the pointer inside two things - goes to the one drawn on
        # top: the rank is the stack order, turned round.
        rect_here = self.plot_rect()
        for region in doc.regions:
            if not region.visible:
                continue
            box = self.region_text_box(region, rect_here)
            if box is not None:
                near(region, box, -model.z_of(region))
            # Its two edges, like a marker line's line.
            if rect_here.top() <= point.y() <= rect_here.bottom():
                for edge in (region.lo, region.hi):
                    x = float(self.x_to_px(edge, rect_here))
                    if (rect_here.left() - radius <= x
                            <= rect_here.right() + radius
                            and abs(point.x() - x) <= radius):
                        hits.append((abs(point.x() - x),
                                     -model.z_of(region), region, None))
        for span in doc.spans:
            if not span.visible:
                continue
            gap = self.span_gap(span, point, rect_here)
            if gap is not None and gap <= radius:
                hits.append((gap, -model.z_of(span), span, None))
        legend_box = self.rotated_bounds(doc.legend, self.legend_rect())
        if legend_box is not None:
            near(doc.legend, legend_box, -model.z_of(doc.legend))
        for label, box in self._text_boxes:
            near(label, box, -model.z_of(label))
        rect_now = self.plot_rect()
        for label in doc.labels:
            x = self.vline_px(label, rect_now) if label.visible else None
            if (x is not None and rect_now.top() <= point.y()
                    <= rect_now.bottom() and abs(point.x() - x) <= radius):
                hits.append((abs(point.x() - x), -model.z_of(label), label,
                             None))
        for image, box in getattr(self, "_image_boxes", ()):
            near(image, box, -model.z_of(image))
        for analysis, box in self._analysis_boxes:
            near(analysis, box, -model.z_of(analysis))
        for marker, box in self._marker_boxes:
            near(marker, box, -model.z_of(marker))
        for axis, box in self._axis_boxes:
            gap = _rect_distance(QRectF(box), point)
            if gap <= 4.0:
                hits.append((gap, 4, axis, "caption"))
        for axis in self.shown_axes():
            # `visible` is the CAPTION's: the spine and the numbers are
            # drawn, and double-clicked, whether it is shown or not (with
            # the y caption hidden, the y axis could not be opened at all).
            which = axis.which
            if self.axis_spine_rect(which).contains(point):
                hits.append((0.0, 5, axis, "spine"))
            elif self.axis_numbers_rect(which).contains(point):
                hits.append((0.0, 5, axis, "numbers"))
            else:
                # ON the axis's lines - its own and the one closing the box
                # - and over the ticks pointing in: the spine as well, but
                # only when nothing else is within the pick distance, so a
                # curve along the frame is still a curve.
                gap = self.frame_line_gap(axis, point)
                if gap is not None:
                    hits.append((radius + gap, 5, axis, "spine"))
        for trace, box in self._label_boxes:
            if QRectF(box).contains(point):
                hits.append((0.0, 6, trace.scan, None))
        for trace in self.traces:
            gap = self._gap_to(trace, point)
            if gap is not None and gap <= radius:
                hits.append((gap, -model.z_of(trace.scan), trace.scan, None))
        hits.sort(key=lambda hit: (hit[0], hit[1]))
        found = []
        for _gap, _rank, obj, axis_hit in hits:
            if obj in found:
                continue
            if axis_hit is not None and not any(
                    isinstance(o, model.Axis) for o in found):
                self._axis_hit = axis_hit
            found.append(obj)
        return found

    def object_at(self, pos):
        found = self.objects_at(pos)
        return found[0] if found else None

    def _nearest_trace(self, pos):
        """`(trace, distance)` for the drawn curve nearest the cursor.

        Measured against the points that were actually drawn (`px`/`py` from
        the last render): what the cursor picks is what the eye sees, a
        magnified stretch and a broken axis included.
        """
        best, best_gap = None, 1e30
        for trace in self.traces:
            if trace.px is None or not len(trace.px):
                if trace.missing is not None:
                    gap = abs(self.sy_to_px(trace.scan, trace.scan.offset) - pos.y())
                    if gap < best_gap:
                        best, best_gap = trace, gap
                continue
            gap = float(np.min(np.hypot(trace.px - pos.x(),
                                        trace.py - pos.y())))
            if gap < best_gap:
                best, best_gap = trace, gap
        return best, best_gap

    def _gap_to(self, trace, pos):
        """How far `pos` is from a drawn curve, or None."""
        if trace.px is None or not len(trace.px):
            if getattr(trace, "missing", None) is not None:
                return abs(self.sy_to_px(trace.scan, trace.scan.offset) - pos.y())
            return None
        return float(np.min(np.hypot(trace.px - pos.x(), trace.py - pos.y())))

    def _trace_at(self, pos):
        """The trace whose drawn curve is within the pick distance, or None."""
        trace, gap = self._nearest_trace(pos)
        return trace if gap <= self.pick_radius() else None

    def drag_target(self, pos):
        """What a press-and-drag starting at `pos` acts on, or None.

        `("interval", scan)` on a CURVE - marking a stretch of it to analyse;
        `("move", obj)` on anything drawn on the figure that moves; None
        everywhere else, and there the drag draws a box. The name readout
        beside a hovered curve and an axis SPINE are not targets: there is no
        curve under the pointer to mark, and a spine does not move.
        """
        obj = self.object_at(pos)
        if obj is None:
            return None
        if isinstance(obj, model.Scan):
            trace = self._trace_at(pos)
            if (trace is not None and trace.scan is obj
                    and not obj.draws_lines):
                return ("interval", obj)
            return None
        if self._frame_part(obj):
            return None
        if self._fields_of(obj):
            return ("move", obj)
        return None

    def select_in_box(self, start, end, add=False):
        """Select everything inside the dragged rectangle.

        A trace counts as inside when any of the points that were DRAWN for
        it falls in the box, which is the same "what you see is what you
        pick" rule the click uses; an artist - a label, a marker line, a
        region, the legend, a picture - and an analysis's label when the box
        touches what is drawn of it. A label caught with its own curve is
        harmless: G moves a child with its parent, never twice. A box
        smaller than the drag slop is a click on empty space, and clears the
        selection.
        """
        doc = self.doc
        if doc is None:
            return []
        box = QRectF(QPointF(min(start.x(), end.x()), min(start.y(), end.y())),
                     QPointF(max(start.x(), end.x()), max(start.y(), end.y())))
        if box.width() < self._slop() and box.height() < self._slop():
            if not add:
                doc.select_all(False)
            self.selection_changed.emit()
            self.update()
            return []
        chosen = []
        for trace in self.traces:
            if trace.px is None or not len(trace.px):
                if trace.missing is not None:
                    y = self.sy_to_px(trace.scan, trace.scan.offset)
                    if box.top() <= y <= box.bottom():
                        chosen.append(trace.scan)
                continue
            inside = ((trace.px >= box.left()) & (trace.px <= box.right())
                      & (trace.py >= box.top()) & (trace.py <= box.bottom()))
            if bool(inside.any()):
                chosen.append(trace.scan)
        for marker, marker_box in self._marker_boxes:
            if box.intersects(QRectF(marker_box)):
                chosen.append(marker)
        for analysis, analysis_box in self._analysis_boxes:
            if (analysis not in chosen
                    and box.intersects(QRectF(analysis_box))):
                chosen.append(analysis)
        rect = self.plot_rect()
        for obj in doc.objects():
            if (not self.is_artist(obj) or not getattr(obj, "visible", True)
                    or obj in chosen):
                continue
            bounds = self.rotated_bounds(obj, self.artist_box(obj, rect),
                                         rect)
            if bounds is not None and box.intersects(bounds):
                chosen.append(obj)
        if add:
            for obj in chosen:
                obj.selected = True
        else:
            doc.select_only(chosen)
        self.selection_changed.emit()
        self.update()
        return chosen

    def select_at(self, pos, add=False):
        obj = self.object_at(pos)
        doc = self.doc
        if doc is None:
            return None
        if obj is not None and self._frame_part(obj):
            # The spine and the numbers are never selected: a click there
            # is a click on nothing selectable, and clears like one.
            obj = None
        if obj is None:
            if not add:
                doc.select_all(False)
                self.selection_changed.emit()
                self.update()
            return None
        if add:
            obj.selected = not obj.selected
        else:
            # A click makes this the ONLY selected thing, even inside a
            # bigger selection. Keeping the others only made sense while a
            # press on a selected curve could drag the whole selection, and
            # a press no longer moves anything.
            doc.select_only([obj])
        self.selection_changed.emit()
        self.update()
        return obj

    # ------------------------------------------------------------ transforms
    def start_grab(self, objs=None):
        """Start a `G` move: no button held, the cursor drives it.

        Blender's grab, in a plot, and the two kinds of object move
        differently ON PURPOSE:

        * a **scan** moves in y only. Shifting a pattern along the
          position axis would be a claim about the measurement rather than
          about the figure, so it does not exist, not even as a locked axis.
        * an **artist** (a label, a marker line, a region) moves freely in
          both. It is on the figure, not data.
        """
        doc = self.doc
        if doc is None:
            return False
        objs = [o for o in (objs if objs is not None else doc.selected())
                if self._fields_of(o)]
        # A label whose scan moves too goes WITH it, not twice (Blender
        # leaves a child alone when its parent is transformed).
        moving = set(id(o) for o in objs)
        objs = [o for o in objs
                if not (isinstance(o, model.TextLabel)
                        and o.scan is not None and id(o.scan) in moving)]
        if not objs:
            return False
        pos = self._cursor or QPointF(self.plot_rect().center())
        self._move = {
            "objs": objs,
            "start": pos,
            "origin": [self._value_of(o) for o in objs],
            "stored": [self._stored_of(o) for o in objs],
            "typed": "",
            "axis": None,
            "keyboard": True,
            "moved": True,
        }
        self.mode_changed.emit(
            "GRAB - move, type a number, X/Y to lock an axis, Enter, Esc")
        self.update()
        return True

    @staticmethod
    def is_artist(obj):
        """True for the things that are figure furniture rather than data.

        The x restriction applies to MEASUREMENTS. An artist - a caption, a
        marker line, a region - goes where it is put, in both directions.
        """
        # The BASE CLASS, not a list of kinds: a new artist should not have
        # to be added here to be draggable. Listing them by name is how the
        # legend arrived unable to move.
        return isinstance(obj, model.Artist)

    @staticmethod
    def _fields_of(obj):
        """Which properties a drag writes, per kind of object.

        * artists: both plot fractions.
        * an analysis: the distance from its label to the curve, and nothing
          else. The movement is locked vertically so a label stays over the
          feature it names, and the leader arrow stretches to match.
        * an axis: along the axis, and away from it - both clamped into the
          margin by `axis_label_rect`, so a caption cannot be dragged over
          the data.
        * a scan: its offset, in data units.
        """
        if isinstance(obj, model.TextLabel) and getattr(obj, "vline",
                                                        None) is not None:
            return ("vline", "x", "y")
        if isinstance(obj, model.Region):
            return ("lo", "hi", "y")
        if isinstance(obj, model.SpanArrow):
            return ("x0", "x1", "y")
        if getattr(obj, "attached", False):
            # x and y too: where it stands when there is no curve point to
            # hang from (`_artist_origin_px`).
            return ("at", "dx", "dy", "x", "y")
        if isinstance(obj, model.TextLabel) and getattr(obj, "leader",
                                                        None):
            return ("x", "y", "leader")
        if PlotWidget.is_artist(obj):
            return ("x", "y")
        if isinstance(obj, model.OffsetMarker):
            return ("at", "dy")
        if isinstance(obj, model.Analysis):
            return ("label_dy", "label_at")
        if isinstance(obj, model.Axis):
            return ("label_along", "label_gap")
        return ("offset",)

    def _value_of(self, obj):
        """An object's transformable state, as a tuple of its own fields.

        An analysis whose `label_dy` is still None (the automatic side) hands
        over the offset it is BEING DRAWN at, so a drag starts where the eye
        sees the label rather than jumping.
        """
        if isinstance(obj, model.Analysis):
            # Where the label is drawn, and where along its interval it
            # stands as STORED (None, the peak, stays None unless slid).
            return (self.effective_label_dy(obj), obj.label_at)
        if isinstance(obj, model.OffsetMarker):
            # The sample it is DRAWN at, so a drag starts under the hand
            # (and a moved marker is pinned to a point ON its curve).
            return (self.marker_at(obj), self.marker_dy(obj))
        if getattr(obj, "attached", False):
            return (tuple(obj.at), float(obj.dx or 0.0), self.label_dy(obj),
                    float(obj.x), float(obj.y))
        if isinstance(obj, model.TextLabel) and getattr(obj, "vline",
                                                        None) is not None:
            return (float(obj.vline), float(obj.x), float(obj.y))
        if isinstance(obj, model.TextLabel) and getattr(obj, "leader",
                                                        None):
            return (float(obj.x), float(obj.y), tuple(obj.leader))
        # `style_of`, not getattr: a caption's gap is None until dragged.
        return tuple(float(self.style_of(obj, name))
                     for name in self._fields_of(obj))

    def _stored_of(self, obj):
        """An object's transformable fields EXACTLY as stored, None and all.

        What a cancelled or undone move must put back. Writing back the value
        it was DRAWN at instead would turn "follow the house style" into a
        fixed number the moment a drag was cancelled."""
        return tuple(getattr(obj, name) for name in self._fields_of(obj))

    @staticmethod
    def label_x(analysis):
        """Where an analysis's label stands, on x: its value, or for a
        peak area slid along it, that place, kept inside its interval."""
        value = analysis.value()
        at = getattr(analysis, "label_at", None)
        if at is None or not getattr(analysis, "slides", False):
            return value
        cursors = analysis.cursors()
        if len(cursors) != 2:
            return value
        low, high = sorted(cursors)
        return min(high, max(low, float(at)))

    def _slid(self, analysis, start, dx_px, rect):
        """The label's place after sliding `dx_px` along the axis."""
        if start is None:
            start = self.label_x(analysis)
        if start is None:
            return None
        value = self.px_to_x(self.x_to_px(start, rect) + dx_px, rect)
        cursors = analysis.cursors()
        if len(cursors) == 2:
            low, high = sorted(cursors)
            value = min(high, max(low, value))
        return value

    def effective_label_dy(self, analysis):
        """The offset an analysis label is drawn at, automatic or chosen."""
        if analysis.label_dy is not None:
            return float(analysis.label_dy)
        rect = self.plot_rect()
        for trace in self.traces:
            if trace.scan is analysis.scan:
                return self.label_offset(analysis, trace, rect)
        return -46.0

    def _apply_value(self, obj, value):
        if getattr(obj, "attached", False) and len(value) == 3 and \
                isinstance(value[0], (tuple, list)):
            obj.at = tuple(value[0])
            obj.dx = float(value[1] or 0.0)
            obj.dy = None if value[2] is None else float(value[2])
            return
        if getattr(obj, "vline", None) is not None and len(value) > 2:
            obj.vline, obj.x, obj.y = (float(value[0]), float(value[1]),
                                       float(value[2]))
            return
        if isinstance(obj, model.Region):
            obj.lo, obj.hi = sorted((float(value[0]), float(value[1])))
            obj.y = float(min(0.99, max(0.01, value[2])))
            return
        if isinstance(obj, model.SpanArrow):
            obj.x0, obj.x1 = float(value[0]), float(value[1])
            obj.y = float(min(0.99, max(0.01, value[2])))
            return
        if self.is_artist(obj):
            # Clamped only where the numbers ARE fractions. A data-space
            # artist stores a position and a height, and clamping those
            # to 0..1 is what sent one to "0.99 deg" the moment it was
            # moved.
            if getattr(obj, "space", model.SPACE_RELATIVE) == model.SPACE_DATA:
                obj.x, obj.y = float(value[0]), float(value[1])
            else:
                obj.x = float(min(0.99, max(0.01, value[0])))
                obj.y = float(min(0.99, max(0.01, value[1])))
            if len(value) > 2 and isinstance(obj, model.TextLabel):
                obj.leader = list(value[2]) if value[2] else None
        elif isinstance(obj, model.Analysis):
            obj.label_dy = float(value[0])
            if len(value) > 1:
                obj.label_at = value[1]
        elif isinstance(obj, model.OffsetMarker):
            obj.at = value[0]
            obj.dy = float(value[1])
        elif isinstance(obj, model.Axis):
            obj.label_along = float(min(1.0, max(0.0, value[0])))
            obj.label_gap = float(max(0.0, value[1]))
        else:
            obj.offset = float(value[0])

    def keep_inside(self, artist, rect=None):
        """Push an artist back inside the axes box - its WHOLE box, text
        and all - wherever a move left it. Past the spines it only covered
        the numbers or left the figure. One wider or
        taller than the box sits against its left or top edge."""
        rect = rect or self.plot_rect()
        box = self.rotated_bounds(artist, self.artist_box(artist, rect), rect)
        if box is None:
            return artist
        shift_x = shift_y = 0.0
        if box.width() > rect.width() or box.left() < rect.left():
            shift_x = rect.left() - box.left()
        elif box.right() > rect.right():
            shift_x = rect.right() - box.right()
        if box.height() > rect.height() or box.top() < rect.top():
            shift_y = rect.top() - box.top()
        elif box.bottom() > rect.bottom():
            shift_y = rect.bottom() - box.bottom()
        if shift_x or shift_y:
            x, y = self.artist_point(artist, rect)
            self.set_artist_point(artist, x + shift_x, y + shift_y, rect,
                                  clamp=False)
        return artist

    def _artist_origin_px(self, artist, origin, rect):
        """Where an artist stood when the gesture began, in pixels (a
        label with a parent as drawn, following its scan)."""
        follow = self.follow_px(artist, rect)
        if (getattr(artist, "attached", False) and len(origin) >= 3
                and isinstance(origin[0], (tuple, list))):
            point = self.attach_point(artist, rect, at=tuple(origin[0]))
            if point is not None:
                dx = 0.0 if artist.leader else float(origin[1] or 0.0)
                dy = (float(origin[2]) if origin[2] is not None
                      else self.NOTE_DY)
                return (point.x() + dx, point.y() + dy)
            # No point on its curve (the curve cannot be drawn in this
            # unit): it stands at its own x and y, like `artist_point`.
            if len(origin) >= 5:
                px, py = self.rel_to_px(float(origin[3]), float(origin[4]),
                                        rect)
                return (px, py + follow)
            return self.artist_point(artist, rect)
        if getattr(artist, "vline", None) is not None and len(origin) > 2:
            return (float(self.x_to_px(origin[0], rect)),
                    self.rel_to_px(0.0, float(origin[2]), rect)[1])
        if isinstance(artist, (model.Region, model.SpanArrow)):
            return (self._middle_px(origin[0], origin[1], rect),
                    self.rel_to_px(0.0, float(origin[2]), rect)[1])
        if getattr(artist, "space", model.SPACE_RELATIVE) == model.SPACE_DATA:
            return (float(self.x_to_px(origin[0], rect)),
                    float(self.ay_to_px(artist, origin[1], rect)) + follow)
        px, py = self.rel_to_px(float(origin[0]), float(origin[1]), rect)
        return (px, py + follow)

    def _typed_value(self):
        state = self._move
        if not state or not state["typed"]:
            return None
        try:
            return float(state["typed"].replace(",", "."))
        except ValueError:
            return None

    def _typed_px(self, artist, typed, axis, rect):
        """`(dx, dy)` in pixels for a typed distance `typed` in the axes'
        units: x with an X lock, else y (up is more) on the artist's axis."""
        if axis == "x":
            return self.x_distance_px(typed, rect), 0.0
        lo, hi = self.view_y()
        return 0.0, -typed / max(hi - lo, 1e-12) * rect.height()

    def _typed_unit(self, artist, axis):
        """The unit a typed distance for `artist` is in."""
        doc = self.doc
        if doc is None:
            return ""
        if axis == "x":
            return doc.x_unit
        return doc.y_axis_unit()

    def _update_move(self, pos, mods=Qt.NoModifier):
        """Move everything in the gesture to where the cursor now is.

        Pixels to values, per kind of object:

        * an artist takes the cursor's own motion, in plot fractions. DOWN on
          the screen is a bigger `y`, because the fraction is measured from
          the top of the plot - getting that backwards is what made dragging
          the arrow feel inverted.
        * a scan takes the motion in DATA units, where up is more, because
          that is what the y axis says.
        """
        state = self._move
        typed = self._typed_value()
        rect = self.plot_rect()
        dx_px = pos.x() - state["start"].x()
        dy_px = pos.y() - state["start"].y()
        if mods & Qt.ShiftModifier:                # precision, as in Blender
            dx_px *= 0.1
            dy_px *= 0.1
        axis = state.get("axis")
        if axis == "x":
            dy_px = 0.0
        elif axis == "y":
            dx_px = 0.0
        for obj, origin in zip(state["objs"], state["origin"]):
            if self.is_artist(obj):
                # Worked in PIXELS and handed back in the artist's own
                # space, so a data-space artist moves under the hand
                # exactly like a relative one. A typed number is a distance
                # in the AXES' units, so a decorator's move takes typed
                # numbers too: along x with X, in the x axis's unit;
                # otherwise up, in its y axis's.
                if (axis == "x" and typed is None
                        and isinstance(obj, model.TextLabel)
                        and obj.leader and obj.scan is not None
                        and not obj.attached
                        and self._slide_note(obj, origin, dx_px, rect)):
                    self.keep_inside(obj, rect)
                    continue
                move_x, move_y = dx_px, dy_px
                if typed is not None:
                    move_x, move_y = self._typed_px(obj, typed, axis, rect)
                ox, oy = self._artist_origin_px(obj, origin, rect)
                self.set_artist_point(obj, ox + move_x, oy + move_y, rect,
                                      clamp=False)
                self.keep_inside(obj, rect)
                continue
            if isinstance(obj, model.Analysis):
                if axis == "x" and obj.slides:
                    # G, then X: an integration's label slides ALONG its
                    # interval, and never out of it.
                    self._apply_value(obj, (origin[0], self._slid(
                        obj, origin[1], dx_px, rect)))
                else:
                    # Vertical: the label stays over its feature and the
                    # leader arrow stretches.
                    self._apply_value(obj, (origin[0] + dy_px, origin[1]))
                continue
            if isinstance(obj, model.OffsetMarker):
                # ALONG its curve, sample by sample, as far as the hand
                # moved sideways - a curve that doubles back is walked, not
                # looked up by position - and the number up or down.
                self._apply_value(obj, (self._walked(obj, origin[0], dx_px,
                                                     rect),
                                        origin[1] + dy_px))
                continue
            if isinstance(obj, model.Axis):
                # The gap grows AWAY from the axes box, whichever side the
                # axis is on: down under a bottom axis, up over a top one,
                # left of a left one, right of a right one.
                side = self.axis_side(obj)
                if obj.which == "x":
                    away = dy_px if side == "bottom" else -dy_px
                    self._apply_value(obj, (
                        origin[0] + dx_px / max(1.0, rect.width()),
                        origin[1] + away))
                else:
                    away = -dx_px if side == "left" else dx_px
                    self._apply_value(obj, (
                        origin[0] - dy_px / max(1.0, rect.height()),
                        origin[1] + away))
                continue
            if typed is not None:
                delta = typed
            else:
                own = obj if isinstance(obj, model.Scan) else None
                # up on screen is more, in the scan's own axis's unit
                delta = -dy_px * self.y_per_px(rect, own)
                if mods & Qt.ControlModifier:
                    lo, hi = (self.view_for(own) if own is not None
                              else self.view_y())
                    snap = _nice_step(hi - lo, 20)
                    delta = round(delta / snap) * snap
            self._apply_value(obj, (origin[0] + delta,))
        for obj in state["objs"]:
            if isinstance(obj, model.Scan):
                obj._cache_key = None
        self.rebuild()

    def _finish_move(self, cancel=False):
        state, self._move = self._move, None
        if state is None:
            return
        stored = state.get("stored") or [None] * len(state["objs"])
        if cancel:
            for obj, origin, raw in zip(state["objs"], state["origin"],
                                        stored):
                self._restore(obj, origin, raw)
            self.rebuild()
            self.mode_changed.emit(self.MODE_TEXT.get(self._mode,
                                                      self.SELECT_TEXT))
            return
        changes = []
        for obj, origin, raw in zip(state["objs"], state["origin"], stored):
            value = self._value_of(obj)
            if value == origin:
                self._restore(obj, origin, raw)
                continue
            # Hand the window the BEFORE state as the current value, so the
            # undo step it builds covers the whole gesture rather than the
            # last pixel of it - and restores what was STORED, None and all.
            self._restore(obj, origin, raw)
            for name, new in zip(self._fields_of(obj), value):
                changes.append((obj, name, new))
        self.mode_changed.emit(self.MODE_TEXT.get(self._mode,
                                                   self.SELECT_TEXT))
        if changes:
            self.transform_done.emit(changes,
                                     self._move_label(list(state["objs"])))
        else:
            self.rebuild()

    #: What the undo history calls moving one of these.
    MOVE_NAMES = ((model.Legend, "legend"),
                  (model.TextLabel, "label"), (model.ImageArtist, "image"),
                  (model.Region, "region"), (model.SpanArrow, "arrow"),
                  (model.MoleculeArtist, "structure"),
                  (model.Analysis, "analysis label"),
                  (model.OffsetMarker, "offset marker"),
                  (model.Axis, "axis caption"))

    @classmethod
    def _move_label(cls, objs):
        """"move arrow", "move legend", "move 2 scan(s)" - what was moved.

        Recorded as "move arrow" for every artist, the undo menu would say
        that about a label."""
        scans = [obj for obj in objs if isinstance(obj, model.Scan)]
        if len(objs) == 1 and not scans:
            for kind, name in cls.MOVE_NAMES:
                if isinstance(objs[0], kind):
                    return "move " + name
        if scans and len(scans) == len(objs):
            return "move {} scan(s)".format(len(scans))
        return "move {} object(s)".format(len(objs))

    def _restore(self, obj, origin, raw):
        """Put an object back as it was before a move: exactly as stored when
        that is known, else at the value it was drawn with."""
        if raw is None:
            self._apply_value(obj, origin)
            return
        for name, value in zip(self._fields_of(obj), raw):
            setattr(obj, name, value)

    def moving(self):
        """True while a grab or a drag is live. For tests and the status bar."""
        return self._move is not None

    # ------------------------------------------------ S and R: Blender's
    #: What `S` scales, per kind of artist: every size it has, so the whole
    #: object grows or shrinks together. Only figure furniture: nothing here
    #: stands for a measurement.
    SCALE_FIELDS = ((model.Legend, ("size", "sample")),
                    (model.Region, ("size",)),
                    (model.SpanArrow, ("size", "head")),
                    (model.TextLabel, ("size",)),
                    (model.ImageArtist, ("width",)),
                    (model.MoleculeArtist, ("bond_length", "bond_width",
                                            "label_size")))

    #: The point a scale or a rotation is ABOUT, as `(fx, fy)` of the
    #: artist's box - 0 left/top, 1 right/bottom, as `Artist.anchor` - and
    #: where it starts: a scale keeps the bottom left where it is, a
    #: rotation turns about the centre.
    PIVOT_START = {"scale": (0.0, 1.0), "rotate": (0.5, 0.5)}
    #: How often the snapped rotation steps, in degrees (Ctrl).
    ROTATE_SNAP = 15.0

    @classmethod
    def scale_fields(cls, obj):
        if not getattr(obj, "can_scale", False):
            return ()
        for kind, fields in cls.SCALE_FIELDS:
            if isinstance(obj, kind):
                return fields
        return ()

    def can_transform(self, mode, obj):
        if mode == "scale":
            return bool(self.scale_fields(obj))
        return self.is_artist(obj) and bool(getattr(obj, "can_rotate", False))

    def start_scale(self, objs=None):
        """Blender's S: the cursor's distance from the pivot scales the
        selected artists - further out is bigger - until a click or Enter;
        Esc or the right button puts them back. A typed number is the
        factor; Shift is precision, Ctrl snaps to tenths. X, Y, M and C
        choose the point it is about (`pivot_key`).

        With only SCANS selected, S spreads them instead (`start_spread`)."""
        doc = self.doc
        chosen = list(objs) if objs is not None else (
            doc.selected() if doc is not None else [])
        if chosen and all(isinstance(o, model.Scan) for o in chosen):
            return self.start_spread(chosen)
        return self._start_transform("scale", objs)

    def start_spread(self, scans):
        """S on scans: EVENLY spaced offsets. One place of the stack holds
        still and is the neutral line: the LOWEST scan (B, where S starts),
        the TOP one (T) or the MIDDLE between them (M), each where it is
        when its key is pressed (`spread_anchor`); the others stand at
        whole steps from it. The order is the one the offsets already have -
        prearranging a little is how it is chosen - and only where they tie
        (laid on top of one another) the outliner's, its top at the top; R
        turns it over while the spread is live.
        Moving the pointer away from the line widens the step, towards it
        closes the stack onto it. A typed number is the STEP in the axis
        unit; Ctrl snaps it to a round number, Shift is precision. Enter or
        a click keeps it, Esc or the right button puts it back."""
        doc = self.doc
        if doc is None or self._move is not None or self._scale is not None:
            return False
        listed = [s for s in doc.scans if s in scans and s.visible]
        if len(listed) < 2:
            return False
        # Top of the stack first: by offset, ties in the outliner's order.
        ordered = self.stack_order(listed)
        offsets = [float(s.offset) for s in ordered]
        low, high = min(offsets), max(offsets)
        step = (high - low) / (len(ordered) - 1)
        if step <= 0:
            # Laid on top of one another: start from the tallest curve's
            # height, as "Stack evenly" does.
            spans = [float(np.nanmax(t.y) - np.nanmin(t.y))
                     for t in self.traces
                     if t.scan in ordered and t.y is not None and len(t.y)]
            step = 0.6 * max(spans) if spans else 1.0
        # The axis's range, kept with the rest (the frame holds still).
        lo, hi = self.view_y()
        cursor = self._cursor or QPointF(self.plot_rect().center())
        # The frame holds still under the hand; a fitted one fits the new
        # stack once it is let go.
        framed = self._view_y
        self._view_y = self.view_y()
        self._scale = {"mode": "spread", "entries": [], "scans": ordered,
                       "framed": framed,
                       "floor": low, "ceiling": high, "anchor": "bottom",
                       "view": (lo, hi), "base": step, "step": step,
                       "even": True,
                       "profile": [offsets[0] - o for o in offsets],
                       "stored": [s.offset for s in ordered], "typed": "",
                       "factor": 1.0, "turn": 0.0, "pivot": (0.0, 0.0),
                       "last": None}
        zero = self._spread_line()
        self._scale["start"] = max(abs(cursor.y() - zero), 20.0)
        self._apply_spread(step)
        self._transform_readout()
        self.update()
        return True

    def _curve_level(self, scan):
        """A drawn curve's middle height, in its axis's unit (its offset
        in), or 0 when it is not drawn."""
        trace = self._trace_of(scan)
        if trace is None or trace.y is None or not len(trace.y):
            return 0.0
        values = np.asarray(trace.y, dtype=float)
        values = values[np.isfinite(values)]
        return float(np.median(values)) if len(values) else 0.0

    @staticmethod
    def _round_step(step):
        """A round spacing near `step`: 1, 2 or 5 times a power of ten, or
        halfway between them - what Ctrl snaps to."""
        if step <= 0:
            return step
        decade = 10.0 ** math.floor(math.log10(step))
        choices = [decade * m for m in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0,
                                        6.0, 8.0, 10.0)]
        return min(choices, key=lambda c: abs(c - step))

    def stack_order(self, scans):
        """`scans` from the top of the stack down: by their offsets, and
        where offsets tie by the outliner's order, its top first."""
        doc = self.doc
        return sorted(scans, key=lambda s: (-float(s.offset),)
                      + tuple(doc.outliner_key(s)))

    #: The keys that choose what a spread holds still (`spread_anchor`).
    SPREAD_KEYS = {Qt.Key_T: "top", Qt.Key_B: "bottom", Qt.Key_M: "middle"}

    def _apply_spread(self, step):
        """Each scan at its depth below the top, top first - a step apart,
        or (P) the gaps the stack had when S began, scaled so their mean is
        the step - with the place the `anchor` names where it was: the last
        scan of the order - the lowest - on the floor, the first on the
        ceiling, or the middle of the stack halfway between them."""
        state = self._scale
        state["step"] = step
        count = len(state["scans"])
        if state.get("even", True):
            depths = [i * step for i in range(count)]
        else:
            profile = state["profile"]
            own = profile[-1] / (count - 1)
            depths = [d * step / own for d in profile]
        total = depths[-1]
        anchor = state.get("anchor", "bottom")
        middle = 0.5 * (state["floor"] + state["ceiling"])
        for depth, scan in zip(depths, state["scans"]):
            if anchor == "top":
                scan.offset = state["ceiling"] - depth
            elif anchor == "middle":
                scan.offset = middle + 0.5 * total - depth
            else:
                scan.offset = state["floor"] + total - depth
        self.rebuild()

    def spread_even(self):
        """P while S spreads scans: even gaps, or the gaps the stack had
        when S began kept in proportion - an uneven ladder (groups of
        curves) spread or closed as a whole, which the swipe did before it
        kept every curve in its place. The held place and the mean step are
        kept; R still turns the order over, the pattern of gaps staying
        where it is. A stack laid on top of one another has no gaps of its
        own, and P does nothing there."""
        state = self._scale
        if state is None or state.get("mode") != "spread":
            return False
        if state["profile"][-1] <= 0:
            return False
        state["even"] = not state.get("even", True)
        self._apply_spread(state["step"])
        self._transform_readout()
        self.update()
        return True

    def _spread_line(self):
        """Put the neutral line through the held place AS DRAWN, and return
        it on the page: the middle height of the curve standing there (the
        mean of all of them, for the middle), never its bare offset. An
        offset is where the curve's zero is, and a curve can sit far from
        it - a pattern stands on its background, which may be far up -
        so a line at the offset ran through the other curves, and a
        distance from it made every step huge."""
        state = self._scale
        scans = state["scans"]
        anchor = state.get("anchor", "bottom")
        if anchor == "top":
            value, there = state["ceiling"], scans[:1]
        elif anchor == "middle":
            value, there = 0.5 * (state["floor"] + state["ceiling"]), scans
        else:
            value, there = state["floor"], scans[-1:]
        level = value + sum(self._curve_level(s) - float(s.offset)
                            for s in there) / len(there)
        state["level"] = level
        state["zero"] = self.sy_to_px(scans[0], level)
        return state["zero"]

    def spread_anchor(self, anchor):
        """T, B or M while S spreads scans: hold the top scan, the bottom
        one or the middle of the stack still, WHERE IT IS NOW - nothing
        moves when the key is pressed; the line moves to the held place and
        the step changes about it from then on. The step reached so far is
        kept, and the pointer is measured from the new line, so the step
        does not jump either. Esc still puts everything back as it was
        before S."""
        state = self._scale
        if state is None or state.get("mode") != "spread":
            return False
        now = [float(s.offset) for s in state["scans"]]
        state["floor"], state["ceiling"] = min(now), max(now)
        state["anchor"] = anchor
        self._apply_spread(state["step"])
        zero = self._spread_line()
        if self._cursor is not None:
            state["start"] = (max(abs(self._cursor.y() - zero), 20.0)
                              / max(state["factor"], 0.05))
        self._transform_readout()
        self.update()
        return True

    def flip_spread(self):
        """R during a spread: the order turned over, the held place (the
        `anchor`) where it was."""
        state = self._scale
        if state is None or state.get("mode") != "spread":
            return False
        state["scans"] = list(reversed(state["scans"]))
        # what Esc puts back runs alongside the scans
        state["stored"] = list(reversed(state["stored"]))
        state["flipped"] = not state.get("flipped", False)
        self._apply_spread(state["step"])
        self._transform_readout()
        self.update()
        return True

    def _update_spread(self, pos=None, mods=Qt.NoModifier):
        state = self._scale
        typed = self._typed_number()
        if typed is not None:
            step = typed
        else:
            pos = pos or self._cursor
            factor = state["factor"]
            if pos is not None:
                factor = abs(pos.y() - state["zero"]) / state["start"]
                if mods & Qt.ShiftModifier:
                    factor = 1.0 + (factor - 1.0) * 0.1
            state["factor"] = max(0.0, factor)
            step = state["base"] * state["factor"]
            if mods & Qt.ControlModifier:
                step = self._round_step(step)
        self._apply_spread(max(0.0, step))
        self._transform_readout()

    def start_rotate(self, objs=None):
        """Blender's R: the cursor's angle about the pivot turns the
        selected artists. A typed number is degrees (counter-clockwise);
        Ctrl snaps to 15. X, Y, M and C choose the pivot."""
        return self._start_transform("rotate", objs)

    def _start_transform(self, mode, objs):
        doc = self.doc
        if doc is None or self._move is not None or self._scale is not None:
            return False
        objs = [o for o in (objs if objs is not None else doc.selected())
                if self.can_transform(mode, o)]
        if not objs:
            return False
        rect = self.plot_rect()
        entries = []
        for obj in objs:
            fields = (self.scale_fields(obj) if mode == "scale"
                      else ("rotation",))
            entries.append({
                "obj": obj, "fields": fields,
                "anchor": self.artist_point(obj, rect),
                "box": self.artist_box(obj, rect),
                "rotation": float(getattr(obj, "rotation", 0.0) or 0.0),
                "origin": tuple(float(self.style_of(obj, f) or 0.0)
                                for f in fields),
                "stored": tuple(getattr(obj, f) for f in fields + ("x",
                                                                     "y"))})
        self._scale = {"mode": mode, "entries": entries, "typed": "",
                       "pivot": self.PIVOT_START[mode], "last": None,
                       "factor": 1.0, "turn": 0.0}
        self._reference()
        self._transform_readout()
        self.update()
        return True

    def scaling(self):
        return self._scale is not None

    def artist_box(self, obj, rect=None):
        """An artist's box before its rotation, in figure units."""
        rect = rect or self.plot_rect()
        box = None
        if isinstance(obj, model.Region):
            box = self.region_text_box(obj, rect)
        elif isinstance(obj, model.SpanArrow):
            box = self.span_box(obj, rect)
        elif isinstance(obj, model.Legend):
            box = self.legend_rect(rect)
        elif isinstance(obj, model.TextLabel):
            box = self._label_box(obj, rect, QFont(self.figure_font()))
        elif isinstance(obj, model.ImageArtist):
            box = self._image_box(obj, rect)
        elif isinstance(obj, model.MoleculeArtist):
            box = self._molecule_layout(obj, rect)[0]
        if box is None or box.isEmpty():
            x, y = self.artist_point(obj, rect)
            box = QRectF(x - 10.0, y - 10.0, 20.0, 20.0)
        return box

    def _pivot_point(self, entry, pivot=None):
        """Where the pivot is on the screen, for one artist: the chosen
        point of its box, turned with the artist about its anchor."""
        fx, fy = pivot or self._scale["pivot"]
        box = entry["box"]
        ax, ay = entry["anchor"]
        ox = box.left() + fx * box.width() - ax
        oy = box.top() + fy * box.height() - ay
        turn = math.radians(-entry["rotation"])
        return (ax + ox * math.cos(turn) - oy * math.sin(turn),
                ay + ox * math.sin(turn) + oy * math.cos(turn))

    def _centre_of_pivots(self):
        points = [self._pivot_point(e) for e in self._scale["entries"]]
        if not points:
            centre = self.plot_rect().center()
            return centre.x(), centre.y()
        return (sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points))

    def _reference(self):
        """Measure the gesture from where the cursor is NOW, keeping the
        factor or angle reached so far - so choosing another pivot halfway
        through does not jump."""
        state = self._scale
        cx, cy = self._centre_of_pivots()
        pos = self._cursor or QPointF(cx + 60.0, cy)
        dx, dy = pos.x() - cx, pos.y() - cy
        state["start"] = max(math.hypot(dx, dy), 10.0) / max(state["factor"],
                                                             1e-6)
        state["angle0"] = math.atan2(dy, dx) - state["turn"]

    def pivot_key(self, key):
        """X, Y, M, C while S or R is live: which point it is about.

        * X puts the pivot on the LEFT edge; X again, the right; and back.
        * Y puts it on the TOP edge; Y again, the bottom; and back.
        * M is the middle OF THE EDGE just chosen: after X, halfway up the
          left (or right) edge; after Y, halfway along the top (or bottom).
        * C is the centre.

        So `S X M` scales about the middle of the left edge, `S X X` about
        the bottom right, `S Y` about the top left, `R C` turns about the
        centre. The pivot is drawn while it is live.
        """
        state = self._scale
        fx, fy = state["pivot"]
        if key == "x":
            fx = (1.0 if fx == 0.0 else 0.0) if state["last"] == "x" else 0.0
        elif key == "y":
            fy = (1.0 if fy == 0.0 else 0.0) if state["last"] == "y" else 0.0
        elif key == "m":
            if state["last"] == "x":
                fy = 0.5
            elif state["last"] == "y":
                fx = 0.5
            else:
                fx = fy = 0.5
        elif key == "c":
            fx = fy = 0.5
        state["pivot"] = (fx, fy)
        state["last"] = key if key in ("x", "y") else (
            state["last"] if key == "m" else None)
        self._reference()
        self._update_transform()

    @staticmethod
    def pivot_name(pivot):
        fx, fy = pivot
        if (fx, fy) == (0.5, 0.5):
            return "centre"
        across = {0.0: "left", 0.5: "", 1.0: "right"}[fx]
        down = {0.0: "top", 0.5: "middle", 1.0: "bottom"}[fy]
        return " ".join(w for w in (down, across) if w)

    def _transform_readout(self):
        state = self._scale
        if state["mode"] == "spread":
            unit = (self.doc.unit_for(state["scans"][0])
                    if self.doc is not None else "")
            held = {"top": "the top", "middle": "the middle"}.get(
                state.get("anchor"), "the lowest")
            count = len(state["scans"])
            if state.get("even", True):
                gaps, other = "{:.4g} {} apart".format(
                    state["step"], unit), "their own gaps"
            else:
                own = state["profile"][-1] / (count - 1)
                gaps, other = "their own gaps x{:.3g}".format(
                    state["step"] / own), "even gaps"
            text = ("SPREAD {} scans {}, {} held still - T top, B bottom, "
                    "M middle, P {}; away from the line wider, type the "
                    "step, Ctrl round, R {}, Enter, Esc").format(
                        count, gaps, held, other,
                        "outliner order again" if state.get("flipped")
                        else "turns the order over")
            self.mode_changed.emit(text)
            self.hovered.emit(text)
            return
        if state["mode"] == "scale":
            what = "SCALE x{:.3g}".format(state["factor"])
        else:
            what = "ROTATE {:+.1f} deg".format(-math.degrees(state["turn"]))
        text = ("{} about the {} - X / Y edges, M middle, C centre; type a "
                "number, Enter, Esc").format(what,
                                             self.pivot_name(state["pivot"]))
        self.mode_changed.emit(text)
        self.hovered.emit(text)

    def _typed_number(self):
        text = self._scale["typed"]
        if not text or text in "-+.,":
            return None
        try:
            return float(text.replace(",", "."))
        except ValueError:
            return None

    def _update_transform(self, pos=None, mods=Qt.NoModifier):
        state = self._scale
        if state["mode"] == "spread":
            self._update_spread(pos, mods)
            return
        rect = self.plot_rect()
        pos = pos or self._cursor
        cx, cy = self._centre_of_pivots()
        typed = self._typed_number()
        if state["mode"] == "scale":
            if typed is not None:
                factor = typed
            elif pos is None:
                factor = state["factor"]
            else:
                factor = math.hypot(pos.x() - cx, pos.y() - cy) / state["start"]
                if mods & Qt.ShiftModifier:
                    factor = 1.0 + (factor - 1.0) * 0.1
                if mods & Qt.ControlModifier:
                    factor = round(factor * 10.0) / 10.0
            state["factor"] = max(0.05, min(20.0, factor))
        else:
            if typed is not None:
                turn = -math.radians(typed)
            elif pos is None:
                turn = state["turn"]
            else:
                turn = (math.atan2(pos.y() - cy, pos.x() - cx)
                        - state["angle0"])
                if mods & Qt.ShiftModifier:
                    turn *= 0.1
                if mods & Qt.ControlModifier:
                    step = math.radians(self.ROTATE_SNAP)
                    turn = round(turn / step) * step
            state["turn"] = turn
        for entry in state["entries"]:
            obj = entry["obj"]
            px, py = self._pivot_point(entry)
            ax, ay = entry["anchor"]
            if state["mode"] == "scale":
                factor = state["factor"]
                for name, value in zip(entry["fields"], entry["origin"]):
                    setattr(obj, name, max(0.1, value * factor))
                # The pivot stays EXACTLY put: the anchor goes back where it
                # was, the box is measured as it now is, and the anchor is
                # moved by however far the pivot then is from where it
                # belongs. Predicting the box instead (factor x the old one)
                # let everything but a bottom-left anchor wander, because a
                # box does not grow in proportion: font sizes step, and a
                # legend's padding does not scale.
                self.set_artist_point(obj, ax, ay, rect, clamp=False)
                live = dict(entry, anchor=(ax, ay),
                            box=self.artist_box(obj, rect))
                qx, qy = self._pivot_point(live)
                nx, ny = ax + (px - qx), ay + (py - qy)
            else:
                turn = state["turn"]
                degrees = entry["rotation"] - math.degrees(turn)
                obj.rotation = (degrees + 180.0) % 360.0 - 180.0
                vx, vy = ax - px, ay - py
                nx = px + vx * math.cos(turn) - vy * math.sin(turn)
                ny = py + vx * math.sin(turn) + vy * math.cos(turn)
            self.set_artist_point(obj, nx, ny, rect, clamp=False)
        self._transform_readout()
        self.invalidate()

    def _finish_transform(self, cancel=False):
        state, self._scale = self._scale, None
        if state is None:
            return
        if state["mode"] == "spread":
            now = [s.offset for s in state["scans"]]
            for scan, old in zip(state["scans"], state["stored"]):
                scan.offset = old
            changes = ([] if cancel else
                       [(s, "offset", v) for s, v, old in
                        zip(state["scans"], now, state["stored"]) if v != old])
            self._view_y = state["framed"]
            self.rebuild()
            self.mode_changed.emit(self.MODE_TEXT.get(self._mode,
                                                       self.SELECT_TEXT))
            if changes:
                self.transform_done.emit(changes, "spread {} scans".format(
                    len(state["scans"])))
            return
        idle = (state["factor"] == 1.0 if state["mode"] == "scale"
                else state["turn"] == 0.0)
        changes = []
        for entry in state["entries"]:
            obj = entry["obj"]
            names = entry["fields"] + ("x", "y")
            now = tuple(getattr(obj, n) for n in names)
            for name, value in zip(names, entry["stored"]):
                setattr(obj, name, value)
            if cancel or idle:
                continue
            changes.extend((obj, n, v) for n, v, old in
                           zip(names, now, entry["stored"]) if v != old)
        self.mode_changed.emit(self.MODE_TEXT.get(self._mode,
                                                   self.SELECT_TEXT))
        if changes:
            self.transform_done.emit(changes, "{} {} object(s)".format(
                state["mode"], len(state["entries"])))
        self.invalidate()

    # The old names, kept for what already calls them.
    def _update_scale(self, pos=None, mods=Qt.NoModifier):
        self._update_transform(pos, mods)

    def _finish_scale(self, cancel=False):
        self._finish_transform(cancel)

    #: The keys S and R keep for themselves while they are live, so the
    #: window's own (M is the x range, C measures) do not fire.
    TRANSFORM_KEYS = {Qt.Key_X: "x", Qt.Key_Y: "y", Qt.Key_M: "m",
                      Qt.Key_C: "c"}

    def _key_during_scale(self, ev):
        key, text = ev.key(), ev.text()
        state = self._scale
        if key == Qt.Key_Escape:
            self._finish_transform(cancel=True)
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._finish_transform()
        elif key == Qt.Key_R and state["mode"] == "spread":
            self.flip_spread()
        elif key in self.SPREAD_KEYS and state["mode"] == "spread":
            self.spread_anchor(self.SPREAD_KEYS[key])
        elif key == Qt.Key_P and state["mode"] == "spread":
            self.spread_even()
        elif key in self.TRANSFORM_KEYS and state["mode"] == "spread":
            pass                       # X, Y and C: a spread has no box
        elif key in self.TRANSFORM_KEYS:
            self.pivot_key(self.TRANSFORM_KEYS[key])
        elif key == Qt.Key_Backspace:
            state["typed"] = state["typed"][:-1]
            self._update_transform()
        elif text and (text.isdigit() or text in ".,"
                       or (text == "-" and state["mode"] == "rotate"
                           and not state["typed"])):
            state["typed"] += text
            self._update_transform()
        else:
            ev.ignore()
            return
        ev.accept()

    def _paint_scale(self, p):
        """The live S or R: each artist's box, dashed, the pivot on it, and
        a dashed line from the pivot to the cursor - Blender's cue."""
        state = self._scale
        if state is None:
            return
        rect = self.plot_rect()
        p.setRenderHint(QPainter.Antialiasing, True)
        if state["mode"] == "spread":
            # The neutral line, and the pointer's distance from it. Nothing
            # else: a spread has no boxes and no pivots (drawing a line to
            # "the centre of the pivots" divided by zero mid-paint, and that
            # took the whole program down).
            zero = self.sy_to_px(state["scans"][0], state.get("level", 0.0),
                                 rect)
            p.setPen(QPen(_SELECT, 1.0, Qt.DashLine))
            p.drawLine(QPointF(rect.left(), zero), QPointF(rect.right(), zero))
            if self._cursor is not None:
                p.setPen(QPen(_CURSOR, 1.0, Qt.DashLine))
                p.drawLine(QPointF(self._cursor.x(), zero),
                           QPointF(self._cursor))
            p.setRenderHint(QPainter.Antialiasing, False)
            return
        for entry in state["entries"]:
            obj = entry["obj"]
            box = self.artist_box(obj, rect)
            ax, ay = self.artist_point(obj, rect)
            turn = QTransform()
            turn.translate(ax, ay)
            turn.rotate(-float(getattr(obj, "rotation", 0.0) or 0.0))
            turn.translate(-ax, -ay)
            p.setPen(QPen(_SELECT, 1.0, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(turn.map(QPolygonF(box)))
            live = dict(entry, anchor=(ax, ay), box=box,
                        rotation=float(getattr(obj, "rotation", 0.0) or 0.0))
            px, py = self._pivot_point(live)
            p.setPen(QPen(_SELECT, 1.5))
            p.drawEllipse(QPointF(px, py), 4.0, 4.0)
            p.drawLine(QPointF(px - 7, py), QPointF(px + 7, py))
            p.drawLine(QPointF(px, py - 7), QPointF(px, py + 7))
        if self._cursor is not None:
            cx, cy = self._centre_of_pivots()
            p.setPen(QPen(_CURSOR, 1.0, Qt.DashLine))
            p.drawLine(QPointF(cx, cy), QPointF(self._cursor))
        p.setRenderHint(QPainter.Antialiasing, False)

    # ------------------------------------------------------------- measuring
    #: How the measuring gesture reads, step by step, in the status line.
    MEASURE_TEXT = (
        "MEASURE - click the first point, or type a position",
        "MEASURE - click the second point, or type a position",
        "MEASURE - Enter to choose the analysis, Esc to step back",
    )

    def start_measure(self, scan=None, cursors=None, editing=None,
                      span=None):
        """Begin placing cursors on one scan.

        One scan, always: an analysis is about a single curve, and a gesture
        that could mean two of them is a gesture that means nothing. The
        cursors are positions on x, so changing what the y axis shows
        mid-measurement moves nothing.

        `editing` is an existing analysis being adjusted: confirming replaces
        it instead of adding another.
        """
        doc = self.doc
        if doc is None:
            return False
        if scan is None:
            chosen = doc.selected_scans()
            if len(chosen) != 1:
                self.hovered.emit(
                    "Select one scan to measure on")
                return False
            scan = chosen[0]
        span = list(span) if span else None
        if span:
            # A span names the samples; the positions follow from them,
            # paired index for index.
            cursors = [self._x_of(scan, i) for i in span]
        self._measure = {"scan": scan, "cursors": list(cursors or []),
                         "typed": "", "editing": editing, "span": span}
        self.mode_changed.emit(self._measure_text())
        self._sync_pointer()
        self.update()
        return True

    def _trace_of(self, scan):
        for trace in self.traces:
            if trace.scan is scan:
                return trace
        return None

    def sample_at(self, trace, pos, rect=None):
        """The sample of a drawn curve nearest `pos`, as an index into the
        pattern's OWN arrays, or None.

        Nearest in the plane, not in position: in a stack, the position
        under the pointer names a point on every curve, and only the one the
        pointer is actually near is meant.
        """
        if trace is None or trace.x is None or not len(trace.x):
            return None
        rect = rect or self.plot_rect()
        px = self.x_to_px(trace.x, rect)
        py = self.sy_to_px(trace.scan, trace.y, rect)
        gaps = np.hypot(px - pos.x(), py - pos.y())
        if not np.isfinite(gaps).any():
            return None
        index = int(np.nanargmin(gaps))
        return trace.first + index

    @staticmethod
    def _x_of(scan, index):
        """The position of one of a scan's samples, on the x axis."""
        return float(scan.x_values()[int(index)])

    def _sample_point(self, trace, index, rect=None):
        """Where a sample (the pattern's index) is drawn, clipped to the
        kept part."""
        rect = rect or self.plot_rect()
        local = int(_clamp(int(index) - trace.first, 0, len(trace.x) - 1))
        return QPointF(float(self.x_to_px(trace.x[local], rect)),
                       float(self.sy_to_px(trace.scan, trace.y[local], rect)))

    def _x_at(self, px):
        """The position under a pixel column."""
        return float(self.px_to_x(px))

    # ------------------------------------------------ the interval gesture
    def start_interval(self, scan, pos):
        """A double-click landed on a curve: it may become an interval.

        Nothing is marked yet. If the pointer moves, the press becomes a drag
        along the curve and `drag_interval` draws the stretch; if it does
        not, the release opens the scan's settings as any double-click does.
        """
        self._interval = {"scan": scan, "start": pos, "live": False}
        return True

    def drag_interval(self, pos):
        """Stretch the interval from where the double-click landed to `pos`."""
        state = self._interval
        if state is None:
            return False
        if not state["live"]:
            if math.hypot(pos.x() - state["start"].x(),
                          pos.y() - state["start"].y()) < self._slop():
                return True
            editing = None
            measuring = self._measure
            if measuring is not None and measuring["scan"] is state["scan"]:
                # Adjusting an analysis and dragging a new stretch of ITS
                # curve: the new stretch is that analysis's new interval.
                editing = measuring.get("editing")
            if not self.start_measure(state["scan"], editing=editing):
                self._interval = None
                return False
            trace = self._trace_of(state["scan"])
            start = self.sample_at(trace, state["start"])
            if start is not None:
                # BY SAMPLE along the curve: where the press landed and
                # where the pointer is now, each the nearest point of the
                # drawn curve.
                self._measure["span"] = [start, start]
                first = self._x_of(state["scan"], start)
            else:
                first = self._x_at(state["start"].x())
            self._measure["cursors"] = [first, first]
            self._measure["gesture"] = True
            state["live"] = True
        span = self._measure.get("span")
        if span is not None:
            index = self.sample_at(self._trace_of(state["scan"]), pos)
            span[1] = index
            self._measure["cursors"][1] = self._x_of(state["scan"],
                                                           index)
        else:
            self._measure["cursors"][1] = self._x_at(pos.x())
        self.mode_changed.emit(self.INTERVAL_TEXT)
        self.update()
        return True

    def finish_interval(self):
        """The button came up: ask for the analysis, or open the settings.

        A drag hands the two positions to the window at once - that IS the
        confirmation, there is no Enter to press. A double-click that never
        moved is an ordinary double-click, and opens the scan.
        """
        state, self._interval = self._interval, None
        if state is None:
            return False
        if not state["live"]:
            self.activated.emit(state["scan"])
            return True
        measuring = self._measure
        rect = self.plot_rect()
        span = measuring.get("span")
        trace = self._trace_of(measuring["scan"])
        if span is not None and trace is not None:
            a, b = self._sample_point(trace, span[0], rect), \
                self._sample_point(trace, span[1], rect)
            if (abs(span[1] - span[0]) < 2
                    or math.hypot(a.x() - b.x(), a.y() - b.y()) < self._slop()):
                self.end_measure()
                return False
            # In ORDER ALONG THE CURVE, cursors paired with their samples.
            span.sort()
            measuring["cursors"] = [self._x_of(measuring["scan"], i)
                                    for i in span]
            self.measure_confirm()
            return True
        low, high = sorted(measuring["cursors"])
        wide = abs(float(self.x_to_px(high, rect))
                   - float(self.x_to_px(low, rect)))
        if wide < self._slop():
            self.end_measure()
            return False
        measuring["cursors"] = [low, high]
        self.measure_confirm()
        return True

    def cancel_interval(self):
        state, self._interval = self._interval, None
        if state is not None and state["live"]:
            self.end_measure()
        return state is not None

    def interval(self):
        """The double-click-drag in progress, or None. For tests."""
        return self._interval

    def measuring(self):
        """The measurement in progress, or None. For tests and the window."""
        return self._measure

    def _measure_text(self):
        state = self._measure
        if state is None:
            return self.MODE_TEXT.get(self._mode, self.SELECT_TEXT)
        step = min(len(state["cursors"]), 2)
        text = self.MEASURE_TEXT[step]
        if state["typed"]:
            text += "   [{}]".format(state["typed"])
        return text

    def measure_place(self, pos=None, value=None):
        """Put the next cursor down, from a click or from a typed number.

        A typed number is a position on the x axis.
        """
        state = self._measure
        if state is None:
            return False
        if value is None:
            if pos is None:
                return False
            where = self._x_at(pos.x())
        else:
            where = float(value)
        if len(state["cursors"]) >= 2:
            state["cursors"][-1] = where
        else:
            state["cursors"].append(where)
        state["typed"] = ""
        self.mode_changed.emit(self._measure_text())
        self.update()
        return True

    def cursor_at(self, pos):
        """Which measure cursor is under `pos`, or None.

        The handle on the curve, or anywhere along its dashed line: picking
        a cursor up should not require hitting a nine-pixel circle.
        """
        if self._measure is None:
            return None
        point = pos.toPoint() if hasattr(pos, "toPoint") else QPoint(pos)
        for index, box in self._cursor_handles:
            if box.contains(point):
                return index
        rect = self.plot_rect()
        best, best_gap = None, 1e30
        for index, where in enumerate(self._measure["cursors"]):
            x = float(self.x_to_px(where, rect))
            gap = abs(x - point.x())
            if gap < best_gap:
                best, best_gap = index, gap
        return best if best_gap <= 6 else None

    def start_cursor_drag(self, index, pos):
        self._cursor_drag = {"index": int(index), "start": pos}
        self.mode_changed.emit(self._measure_text())
        return True

    def drag_cursor(self, pos):
        """Move the cursor being held to where the pointer is."""
        state = self._cursor_drag
        if state is None or self._measure is None:
            return False
        span = self._measure.get("span")
        if span is not None:
            # A gizmo of an analysis made along the curve follows the
            # curve: the nearest sample to the pointer, whichever branch.
            sample = self.sample_at(self._trace_of(self._measure["scan"]),
                                    pos)
            if sample is not None and 0 <= state["index"] < len(span):
                span[state["index"]] = sample
                self._measure["cursors"][state["index"]] = \
                    self._x_of(self._measure["scan"], sample)
                self.mode_changed.emit(self._measure_text())
                self.update()
            return True
        where = self._x_at(pos.x())
        index = state["index"]
        if 0 <= index < len(self._measure["cursors"]):
            self._measure["cursors"][index] = where
            self.mode_changed.emit(self._measure_text())
            self.update()
        return True

    def end_cursor_drag(self):
        """Let go of a gizmo. While an analysis made here is being ADJUSTED,
        letting go is the confirmation: it is recomputed at once, so the
        number follows the hand instead of waiting for an Enter."""
        held, self._cursor_drag = self._cursor_drag, None
        self.update()
        state = self._measure
        if (held is not None and state is not None
                and state.get("editing") is not None
                and len(state["cursors"]) >= 2):
            self.measure_confirm()

    def editing(self):
        """The analysis whose gizmos are up, or None."""
        return (self._measure or {}).get("editing")

    def gizmo_rect(self):
        """Where the measure cursors are, in GLOBAL coordinates, or None.

        The span between them over the plot's full height, widened by the
        handles and by the position readout drawn to the right of each
        (90 px): what a settings dialog must not cover while it is open
        beside them.
        """
        state = self._measure
        if not state or not state["cursors"]:
            return None
        rect = self.plot_rect()
        xs = [float(self.x_to_px(c, rect))
              for c in state["cursors"]]
        left = max(rect.left(), min(xs) - 24)
        right = min(self.canvas_size()[0], max(xs) + 100)
        corner = self.to_widget(QPointF(left, rect.top()))
        far = self.to_widget(QPointF(right, rect.bottom()))
        top_left = self.mapToGlobal(corner.toPoint())
        return QRect(top_left, QSize(max(1, int(far.x() - corner.x())),
                                     max(1, int(far.y() - corner.y()))))

    def measure_back(self):
        """One Esc: drop the typed number, then a cursor, then the gesture.

        Every Esc goes back one step in the chronology rather than throwing
        the whole thing away.
        """
        state = self._measure
        if state is None:
            return False
        if state["typed"]:
            state["typed"] = ""
        elif state.get("editing") is not None:
            # The gizmos of an analysis being adjusted belong to its settings
            # dialog: closing THAT ends them. Esc here must not strip one.
            return True
        elif state["cursors"]:
            state["cursors"].pop()
        else:
            self._measure = None
            self.mode_changed.emit(self.SELECT_TEXT)
            self.update()
            return True
        self.mode_changed.emit(self._measure_text())
        self.update()
        return True

    def measure_key(self, ev):
        """Keys while cursors are being placed. True when one was used."""
        state = self._measure
        if state is None:
            return False
        key, text = ev.key(), ev.text()
        if key == Qt.Key_Escape:
            return self.measure_back()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if state["typed"]:
                try:
                    self.measure_place(value=float(
                        state["typed"].replace(",", ".")))
                except ValueError:
                    state["typed"] = ""
                return True
            if len(state["cursors"]) >= 2:
                self.measure_confirm()
            return True
        if key == Qt.Key_Backspace:
            state["typed"] = state["typed"][:-1]
            self.mode_changed.emit(self._measure_text())
            return True
        if text and (text.isdigit() or text in "-+.,"):
            state["typed"] += text
            self.mode_changed.emit(self._measure_text())
            self.update()
            return True
        return False

    def measure_confirm(self):
        """Hand the two cursors to the window, which asks what to compute."""
        state = self._measure
        if state is None or len(state["cursors"]) < 2:
            return False
        span = state.get("span")
        self.measure_ready.emit(state["scan"], float(state["cursors"][0]),
                                float(state["cursors"][1]), state["editing"],
                                tuple(span) if span else None)
        return True

    def set_measure_cursors(self, cursors, span=None):
        """Put the gizmos at `cursors` (x axis) - the interval was typed."""
        if self._measure is None:
            return False
        self._measure["cursors"] = [float(c) for c in cursors]
        self._measure["span"] = span
        self.update()
        return True

    def end_measure(self):
        self._measure = None
        self.mode_changed.emit(self.MODE_TEXT.get(self._mode,
                                                  self.SELECT_TEXT))
        self._sync_pointer()
        self.update()

    def _paint_measure(self, p):
        """The crosshairs, the span between them, and their positions."""
        state = self._measure
        if state is None:
            return
        rect = self.plot_rect()
        trace = None
        for candidate in self.traces:
            if candidate.scan is state["scan"]:
                trace = candidate
                break
        colour = QColor(_SELECT)
        span = state.get("span")
        positions, heights = [], []
        for k, where in enumerate(state["cursors"]):
            if span is not None and trace is not None and k < len(span) \
                    and trace.x is not None and len(trace.x):
                # On the SAMPLE the gesture picked.
                point = self._sample_point(trace, span[k], rect)
                positions.append(point.x())
                heights.append(point.y())
                continue
            positions.append(float(self.x_to_px(where, rect)))
            heights.append(None)
        if len(positions) == 2:
            span = QRectF(min(positions), rect.top(),
                          abs(positions[1] - positions[0]), rect.height())
            fill = QColor(colour)
            fill.setAlpha(28)
            p.fillRect(span, fill)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 0.5))
        p.setFont(font)
        self._cursor_handles = []
        for index, x in enumerate(positions):
            held = (self._cursor_drag is not None
                    and self._cursor_drag.get("index") == index)
            p.setPen(QPen(colour, 1.6 if held else 1.2, Qt.DashLine))
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            y = heights[index]
            if y is None and trace is not None:
                y = self._curve_y_at(trace, self.px_to_x(x, rect), rect)
            if y is not None:
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setPen(QPen(colour, 1.8 if held else 1.4))
                p.drawEllipse(QPointF(x, y), 6.0, 6.0)
                p.drawLine(QPointF(x - 10, y), QPointF(x + 10, y))
                p.drawLine(QPointF(x, y - 10), QPointF(x, y + 10))
                p.setRenderHint(QPainter.Antialiasing, False)
                # The grab handle, so a cursor can be picked up and moved
                # rather than only replaced by clicking again.
                self._cursor_handles.append(
                    (index, QRect(int(x - 9), int(y - 9), 18, 18)))
            label = "{:.1f}".format(state["cursors"][index])
            p.setPen(QPen(colour, 1.0))
            p.drawText(QRectF(x + 5, rect.top() + 4 + 14 * index, 90, 14),
                       int(Qt.AlignLeft | Qt.AlignVCenter), label)
        if state["typed"]:
            p.setPen(QPen(colour, 1.0))
            p.drawText(QRectF(rect.left() + 8, rect.top() + 4, 200, 16),
                       int(Qt.AlignLeft | Qt.AlignVCenter),
                       "= {}".format(state["typed"]))

    # ----------------------------------------------------------------- input
    def keyPressEvent(self, ev):
        key = ev.key()
        if getattr(self, "_picking", None) is not None and \
                key == Qt.Key_Escape:
            self._picking = None
            self.mode_changed.emit(self.MODE_TEXT.get(self._mode,
                                                      self.SELECT_TEXT))
            ev.accept()
            return
        if self._press is not None and key == Qt.Key_Escape:
            self._press = None              # the button is down; forget it
            ev.accept()
            return
        if (getattr(self, "_edge_drag", None) is not None
                and key == Qt.Key_Escape):
            self._finish_region_edge(cancel=True)
            ev.accept()
            return
        if self._interval is not None:
            # Mid-drag, the only key that means anything is Esc: drop the
            # interval and leave the curve exactly as it was.
            if key == Qt.Key_Escape:
                self.cancel_interval()
                self.mode_changed.emit(self.SELECT_TEXT)
            ev.accept()
            return
        if self._measure is not None and self.measure_key(ev):
            ev.accept()
            return
        if self._move is not None:
            self._key_during_move(ev)
            return
        if self._scale is not None:
            self._key_during_scale(ev)
            return
        if not ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier
                                 | Qt.MetaModifier) and (
                                     self._page_margin_key(ev)
                                     or self._margin_key(ev)):
            ev.accept()
            return
        chord = bool(ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier
                                       | Qt.MetaModifier))
        if chord and key in (Qt.Key_Z, Qt.Key_P):
            # Ctrl+Z is Undo: should it ever reach the plot (its shortcut
            # disabled, or eaten elsewhere) it must not become Z, the zoom
            # modes.
            QWidget.keyPressEvent(self, ev)
            return
        if key == Qt.Key_Z:
            self.cycle_mode(self.ZOOM_CYCLE)
        elif key == Qt.Key_P:
            self.cycle_mode(self.PAN_CYCLE)
        elif key == Qt.Key_Escape:
            self._box = None
            if getattr(self, "_page_drag", None) is not None:
                self._finish_page_drag(cancel=True)
            if self._margin_state()["drag"] is not None:
                self._finish_margin_drag(cancel=True)
            if self._page_margin_state()["drag"] is not None:
                self._finish_page_margin_drag(cancel=True)
            self._margin_state()["selected"] = None
            self._page_margin_state()["selected"] = None
            self._page_handles_shown = False
            self.set_mode(None)
        elif key in (Qt.Key_F, Qt.Key_Home) and not (
                ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier
                                  | Qt.MetaModifier)):
            self.reset_view()
        elif key == Qt.Key_G:
            if not self.start_grab():
                self.hovered.emit("Nothing selected to move")
        elif ev.text() and (ev.text().isdigit() or ev.text() in "-+.,") \
                and self._scale is None:
            # TYPING A NUMBER IS A MOVE. Selecting a scan already makes it
            # the thing being worked on, so requiring G first would be a
            # second gesture for no reason. G still exists for anyone with
            # it in their fingers.
            if self.start_grab():
                self._move["typed"] = ev.text()
                self._update_move(self._cursor or self._move["start"])
                self.hovered.emit(self._move_readout())
            else:
                self.hovered.emit("Select a scan first, then type a number")
        else:
            QWidget.keyPressEvent(self, ev)
            return
        ev.accept()

    def _key_during_move(self, ev):
        key, text = ev.key(), ev.text()
        if key in (Qt.Key_Escape,):
            self._finish_move(cancel=True)
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._finish_move()
        elif key in (Qt.Key_X, Qt.Key_Y):
            # Blender's axis locks. X is only offered for what moves along
            # x - artists, markers, an axis caption: a scan has no x
            # freedom to lock, so saying so beats a key that does nothing.
            # On a caption both are SCREEN directions: X keeps an x caption
            # at its height and a y caption at its distance from the axis.
            wanted = "x" if key == Qt.Key_X else "y"
            if wanted == "x" and not any(
                    self.is_artist(o) or isinstance(o, (model.OffsetMarker,
                                                        model.Axis))
                    or getattr(o, "slides", False)
                    for o in self._move["objs"]):
                self.hovered.emit("A scan does not move along x")
            else:
                self._move["axis"] = (None if self._move.get("axis") == wanted
                                      else wanted)
                self._update_move(self._cursor or self._move["start"])
        elif key == Qt.Key_Backspace:
            self._move["typed"] = self._move["typed"][:-1]
            self._update_move(self._cursor or self._move["start"])
        elif text and (text.isdigit() or text in "-+.,"):
            self._move["typed"] += text
            self._update_move(self._cursor or self._move["start"])
        else:
            ev.ignore()
            return
        ev.accept()

    def pick_object(self, callback, text):
        """The next left click on the figure names an object, handed to
        `callback`; Esc gives up. How "Inherit" learns its donor."""
        self._picking = callback
        self.mode_changed.emit(text)
        self.hovered.emit(text)

    def picking(self):
        return getattr(self, "_picking", None) is not None

    def mousePressEvent(self, ev):
        pos = self.to_figure(ev.position())
        picking = getattr(self, "_picking", None)
        if picking is not None and ev.button() == Qt.LeftButton:
            self._picking = None
            self.mode_changed.emit(self.MODE_TEXT.get(self._mode,
                                                      self.SELECT_TEXT))
            picking(self.object_at(pos))
            ev.accept()
            return
        if self._scale is not None:
            self._finish_transform(cancel=ev.button() == Qt.RightButton)
            ev.accept()
            return
        if self._move is not None:
            # A click commits a keyboard grab, and the right button cancels
            # it - Blender's rule, and the one anybody who uses G expects.
            self._finish_move(cancel=ev.button() == Qt.RightButton)
            ev.accept()
            return
        if ev.button() == Qt.RightButton:
            obj = self.object_at(pos)
            # The spine and the numbers get their menu, never the selection
            # (it would light the caption up).
            if (obj is not None and not obj.selected
                    and not self._frame_part(obj)):
                self.doc.select_only([obj])
                self.selection_changed.emit()
            self.context_menu.emit(obj, ev.globalPosition().toPoint())
            ev.accept()
            return
        if ev.button() == Qt.MiddleButton:
            self._start_nav(ev)
            ev.accept()
            return
        if ev.button() != Qt.LeftButton:
            QWidget.mousePressEvent(self, ev)
            return
        blade = self.page_margin_blade_at(ev.position())
        if blade is not None and not self._on_page_square(ev.position()):
            self._start_page_margin_drag(blade, ev.position())
            ev.accept()
            return
        side = self.margin_gizmo_at(ev.position())
        if side is not None:
            self._page_margin_state()["selected"] = None
            self._start_margin_drag(side, ev.position())
            ev.accept()
            return
        self._margin_state()["selected"] = None
        self._page_margin_state()["selected"] = None
        handle = self.page_handle_at(ev.position())
        if handle is not None:
            self._start_page_drag(handle, ev.position())
            ev.accept()
            return
        if self._measure is not None:
            held = self.cursor_at(pos)
            if held is not None:
                self.start_cursor_drag(held, pos)
                ev.accept()
                return
            if self._measure.get("editing") is None:
                # Placing cursors by click is the TYPED route (`C`). While an
                # analysis is being adjusted, its gizmos are dragged, and a
                # press anywhere else is an ordinary press.
                self.measure_place(pos)
                ev.accept()
                return
        if self._mode:
            rect = self.plot_rect()
            self._drag = {"px": pos.x(), "py": pos.y(),
                          "x": self.px_to_x(pos.x(), rect),
                          "y": self.px_to_y(pos.y(), rect, self.main_view()),
                          "view_x": self.view_x(),
                          "view_y": self.main_view(),
                          "mode": self._mode}
            self._view_begin("zoom" if self._mode.startswith("zoom")
                             else "pan")
            ev.accept()
            return
        # No mode armed. Released where it was pressed, a press is a CLICK
        # and selects what is under it (or clears). Dragged, what it does
        # depends on what it started NEAR - within the pick distance:
        #
        # * a curve: mark an interval along it, and ask for the analysis on
        #   release. Never a move: a scan moves with G and nothing else.
        # * anything drawn on the figure (the arrow, the legend, a label, an
        #   analysis label, an axis caption): move it.
        # * nothing: a box select. With Shift it is ALWAYS a box, adding to
        #   the selection, which is how a box starts on top of a curve.
        #
        # A handler for "double-click-drag" alone is never reached on a
        # trackpad: tap-then-drag arrives as ONE press and a drag, not as a
        # double-click, so it would be a box every time. Deciding
        # by proximity makes the gesture work however the hardware sends it;
        # a real double-click-drag still does the same thing.
        edge = (self.region_edge_at(pos)
                if ev.button() == Qt.LeftButton
                and not ev.modifiers() & Qt.ShiftModifier else None)
        if edge is not None:
            self._start_region_edge(*edge)
            ev.accept()
            return
        tipped = self.leader_at(pos)
        if tipped is not None:
            # A selected note's arrow tip: it moves, and snaps onto a curve.
            self._leader_drag = {"label": tipped, "stored": tipped.leader}
            ev.accept()
            return
        add = bool(ev.modifiers() & Qt.ShiftModifier)
        cycle = self._note_press(pos)
        target = None if add else self.drag_target(pos)
        if target is not None:
            self._press = {"kind": target[0], "obj": target[1],
                           "start": pos, "cycle": cycle}
        else:
            self._box = {"start": pos, "now": pos, "add": add,
                         "moved": False, "cycle": cycle}
        ev.accept()

    #: The click rhythm: two presses at one place
    #: closer than this are a double-click (settings); between this and
    #: `CYCLE_S` the second one steps to the next object under the pointer,
    #: so one buried in a stack can be reached; slower is a new click.
    DOUBLE_CLICK_S = 0.35
    CYCLE_S = 0.70

    def _note_press(self, pos):
        """Remember a press; True when it is the slow second of a pair at
        the same place - a layer step, not a click."""
        now = self._clock()
        previous = [(t, p) for t, p in self._presses if now - t > 0.03]
        self._presses = (self._presses + [(now, QPointF(pos))])[-4:]
        if not previous:
            return False
        then, where = previous[-1]
        near = (abs(where.x() - pos.x()) <= self.pick_radius()
                and abs(where.y() - pos.y()) <= self.pick_radius())
        return near and self.DOUBLE_CLICK_S <= now - then <= self.CYCLE_S

    def cycle_at(self, pos):
        """Select the NEXT object under the pointer, nearest first and then
        down the stack; round again after the last."""
        found = [o for o in self.objects_at(pos)
                 if not isinstance(o, model.Axis)]
        if len(found) < 2:
            return self.select_at(pos)
        current = next((i for i, o in enumerate(found) if o.selected), -1)
        chosen = found[(current + 1) % len(found)]
        self.doc.select_only([chosen])
        self.selection_changed.emit()
        self.hovered.emit("Layer {} of {}: {}".format(
            found.index(chosen) + 1, len(found),
            getattr(chosen, "name", "") or chosen.kind))
        self.update()
        return chosen

    def _press_became_drag(self, pos, mods=Qt.NoModifier):
        """A press near an object has moved far enough: act on the object."""
        press, self._press = self._press, None
        obj = press["obj"]
        if self.doc is not None and not obj.selected:
            self.doc.select_only([obj])
            self.selection_changed.emit()
        if press["kind"] == "interval":
            self.start_interval(obj, press["start"])
            self.drag_interval(pos)
            return
        objs = self.drag_group(obj)
        self._move = {"objs": objs, "start": press["start"],
                      "origin": [self._value_of(o) for o in objs],
                      "stored": [self._stored_of(o) for o in objs],
                      "typed": "",
                      "axis": None, "keyboard": False, "moved": True}
        self._update_move(pos, mods)
        self.hovered.emit(self._move_readout())

    def drag_group(self, obj):
        """What a drag on `obj` moves: `obj`, and when it is part of the
        selection every other selected thing that moves by hand - several
        analysis labels stretch their arrows together, several offset
        markers travel together. Never a scan (a scan
        moves with G) and never an axis caption, which is the frame."""
        doc = self.doc
        if doc is None or not obj.selected:
            return [obj]
        return [obj] + [o for o in doc.selected()
                        if o is not obj and self._fields_of(o)
                        and not isinstance(o, (model.Scan, model.Axis))]

    def mouseMoveEvent(self, ev):
        pos = self.to_figure(ev.position())
        self._cursor = pos
        self._sync_pointer()
        if ev.buttons() != Qt.NoButton:
            self._handling = True       # drawn as a draft until released
        if self._nav is not None:
            self._drag_nav(ev.position())
            return
        if self._leader_drag is not None:
            self._drag_leader(pos)
            return
        if getattr(self, "_edge_drag", None) is not None:
            self._drag_region_edge(pos)
            return
        if getattr(self, "_page_drag", None) is not None:
            self._drag_page(ev.position())
            return
        if self._margin_state()["drag"] is not None:
            self._drag_margin(ev.position())
            return
        if self._page_margin_state()["drag"] is not None:
            self._drag_page_margin(ev.position())
            return
        if self._scale is not None:
            self._update_transform(pos, ev.modifiers())
            self.update()
            return
        handle = self.page_handle_at(ev.position())
        if handle is not None:
            self.setCursor(self.HANDLE_CURSORS.get(handle, Qt.ArrowCursor))
        side = (self.margin_gizmo_at(ev.position())
                or self.page_margin_blade_at(ev.position()))
        if side is not None:
            self.setCursor(Qt.SizeHorCursor if side in ("left", "right")
                           else Qt.SizeVerCursor)
        if self._cursor_drag is not None:
            self.drag_cursor(pos)
            return
        if self._interval is not None:
            self.drag_interval(pos)
            return
        if self._press is not None:
            start = self._press["start"]
            if (abs(pos.x() - start.x()) >= self._slop()
                    or abs(pos.y() - start.y()) >= self._slop()):
                self._press_became_drag(pos, ev.modifiers())
            return
        if self._box is not None:
            box = self._box
            box["now"] = pos
            if (not box["moved"]
                    and abs(pos.x() - box["start"].x()) < self._slop()
                    and abs(pos.y() - box["start"].y()) < self._slop()):
                return
            box["moved"] = True
            self.hovered.emit("SELECT box - release to take everything inside")
            self.update()
            return
        state = self._move
        if state is not None:
            if not state["keyboard"]:
                if (not state["moved"]
                        and abs(pos.y() - state["start"].y()) < self._slop()
                        and abs(pos.x() - state["start"].x()) < self._slop()):
                    return
                state["moved"] = True
            self._update_move(pos, ev.modifiers())
            self.hovered.emit(self._move_readout())
            return
        drag = self._drag
        if drag is not None and drag["mode"].startswith("pan"):
            rect = self.plot_rect()
            # Measured in PIXELS through the PRESS-time view, never the live
            # one: reading the live limits back feeds the motion into itself
            # and the pan accelerates away.
            y0, y1 = drag["view_y"]
            dy = ((pos.y() - drag["py"]) / max(1.0, rect.height()) * (y1 - y0))
            if drag["mode"] in ("pan_h", "pan_free"):
                self._view_x = self.shifted_x(drag["view_x"],
                                              drag["px"] - pos.x(), rect)
            if drag["mode"] in ("pan_v", "pan_free"):
                self._set_main_stored((y0 + dy, y1 + dy))
            # Drawn as it is, a draft while the hand moves: sliding the
            # cached picture moved the whole PAGE with the data - frame,
            # numbers, captions - until the button was let go.
            self.invalidate()
            self.view_changed.emit()
            return
        if (ev.buttons() == Qt.NoButton and self.doc is not None
                and self.doc.regions and self.region_edge_at(pos)):
            self.hovered.emit("Drag to move this edge of the region "
                              "(Esc puts it back)")
        else:
            self.hovered.emit(self.readout(pos))
        self.update()

    def mouseReleaseEvent(self, ev):
        if getattr(self, "_handling", False):
            # The hand has stopped: one smooth redraw of what it did.
            self._handling = False
            self.update()
        if self._nav is not None and ev.button() == Qt.MiddleButton:
            self._end_nav()
            ev.accept()
            return
        if self._leader_drag is not None:
            self._finish_leader()
            ev.accept()
            return
        if getattr(self, "_edge_drag", None) is not None:
            self._finish_region_edge()
            ev.accept()
            return
        if getattr(self, "_page_drag", None) is not None:
            self._finish_page_drag()
            ev.accept()
            return
        if self._margin_state()["drag"] is not None:
            self._finish_margin_drag()
            ev.accept()
            return
        if self._page_margin_state()["drag"] is not None:
            self._finish_page_margin_drag()
            ev.accept()
            return
        if self._cursor_drag is not None:
            self.end_cursor_drag()
            ev.accept()
            return
        if self._interval is not None:
            self.finish_interval()
            ev.accept()
            return
        if self._press is not None:
            # Near an object and never moved: a click on it. A click on one
            # of several selected things narrows the selection to it - but
            # if it turns out to be the first half of a double-click, the
            # double-click puts the selection back and acts on all of it.
            press, self._press = self._press, None
            self._page_handles_shown = False
            if press.get("cycle"):
                self._click_restore = None
                self.cycle_at(press["start"])
                ev.accept()
                return
            before = (list(self.doc.selected()) if self.doc is not None
                      else [])
            chosen = self.select_at(press["start"])
            self._click_restore = ((chosen, before)
                                   if chosen is not None and chosen in before
                                   and len(before) > 1 else None)
            ev.accept()
            return
        if self._box is not None:
            box, self._box = self._box, None
            if box["moved"]:
                self.select_in_box(box["start"], box["now"], add=box["add"])
            elif box.get("cycle"):
                self.cycle_at(box["start"])
            else:
                self.select_at(box["start"], add=box["add"])
            # A click on the page's margin shows its handles; any other
            # click puts them away.
            self._page_handles_shown = (not box["moved"]
                                        and self.in_page_margin(box["start"]))
            self.update()
            ev.accept()
            return
        if self._move is not None and not self._move["keyboard"]:
            # Only a double-click-drag gets here: a single press no longer
            # starts a move. Still, it is a double-click.
            waiting = self._move.get("activate")
            if self._move["moved"]:
                self._finish_move()
            else:
                self._move = None
                if waiting is not None:
                    self.open_object(waiting)
            ev.accept()
            return
        drag, self._drag = self._drag, None
        if drag is None or ev.button() != Qt.LeftButton:
            QWidget.mouseReleaseEvent(self, ev)
            return
        mode = drag["mode"]
        if mode.startswith("pan"):
            self.invalidate()
            self.commit_view()
            return
        rect = self.plot_rect()
        end = self.to_figure(ev.position())
        x1 = self.px_to_x(_clamp(end.x(), rect.left(), rect.right()),
                          rect, drag["view_x"])
        y1 = self.px_to_y(_clamp(end.y(), rect.top(), rect.bottom()),
                          rect, drag["view_y"])
        if mode in ("zoom_h", "zoom_box") and abs(x1 - drag["x"]) > 1e-12:
            self.set_view_x(min(drag["x"], x1), max(drag["x"], x1))
        if mode in ("zoom_v", "zoom_box") and abs(y1 - drag["y"]) > 1e-12:
            self.set_main_view(min(drag["y"], y1), max(drag["y"], y1))
        self.commit_view()
        self.update()

    def mouseDoubleClickEvent(self, ev):
        """Double-click opens the settings - unless it turns into a DRAG.

        The second press is treated as a press that remembers what to open if
        nothing moves, and the release decides: still, and it was a
        double-click; moved, and it was a double-click-DRAG, which acts on
        whatever it landed on:

        * on a CURVE it marks an interval, and letting go asks which analysis
          to run on it: marking an interval is the trivial and obvious
          gesture. It never moves the scan - a scan moves with G, and
          nothing else.
        * on an ARTIST (the arrow, the legend, a label), an analysis label or
          an axis caption it moves that thing.
        """
        if ev.button() == Qt.MiddleButton:
            # Two quick middle presses are two drags, never "settings" (Qt
            # may or may not have sent this one as a press first).
            if self._nav is None:
                self._start_nav(ev)
            ev.accept()
            return
        blade = (self.page_margin_blade_at(ev.position())
                 if ev.button() == Qt.LeftButton else None)
        if blade is not None:
            # A blade double-clicked: the page's margins in numbers.
            self._page_margin_state()["drag"] = None
            self.update()
            self.page_margins_asked.emit()
            ev.accept()
            return
        if (ev.button() == Qt.LeftButton
                and self.margin_gizmo_at(ev.position()) is not None):
            # A margin arrow double-clicked: the data's margins in numbers.
            self._margin_state()["drag"] = None
            self.update()
            self.data_margins_asked.emit()
            ev.accept()
            return
        if (ev.button() == Qt.LeftButton
                and self.page_handle_at(ev.position()) is not None):
            # A page handle: the size in numbers. The
            # drag the second press may have started is dropped unchanged.
            self._page_drag = None
            self.update()
            self.page_size_asked.emit()
            ev.accept()
            return
        pos = self.to_figure(ev.position())
        # Whatever an extra press of the pair started, the double-click owns
        # the gesture now.
        self._box = None
        self._press = None
        slow = self._measure is None and self._note_press(pos)
        if slow and not isinstance(self.object_at(pos), model.Axis):
            # Qt calls it a double-click up to the SYSTEM's interval (half
            # a second on Windows); slower than DOUBLE_CLICK_S it is a step
            # to the next object under the pointer. Never on an axis: the
            # step leaves axes out, so there it did nothing at all.
            self._click_restore = None
            self.cycle_at(pos)
            ev.accept()
            return
        if self._measure is not None:
            held = self.cursor_at(pos)
            if held is not None:
                self.start_cursor_drag(held, pos)
                ev.accept()
                return
        obj = self.object_at(pos)
        if obj is None:
            QWidget.mouseDoubleClickEvent(self, ev)
            return
        restore, self._click_restore = self._click_restore, None
        if restore is not None and restore[0] is obj and self.doc is not None:
            for other in restore[1]:
                other.selected = True
            self.selection_changed.emit()
        if isinstance(obj, model.Scan):
            if self.doc is not None and not obj.selected:
                self.doc.select_only([obj])
                self.selection_changed.emit()
            trace = self._trace_at(pos)
            if trace is not None and trace.scan is obj:
                self.start_interval(obj, pos)
            else:
                # Its NAME, not its curve: there is no stretch of curve under
                # the pointer to mark, so this is only a double-click.
                self.activated.emit(obj)
            ev.accept()
            return
        if (self.doc is not None and not obj.selected
                and not self._frame_part(obj)):
            self.doc.select_only([obj])
            self.selection_changed.emit()
        if self._fields_of(obj) and not self._frame_part(obj):
            objs = self.drag_group(obj)
            self._move = {"objs": objs, "start": pos,
                          "origin": [self._value_of(o) for o in objs],
                          "stored": [self._stored_of(o) for o in objs],
                          "typed": "", "axis": None, "keyboard": False,
                          "moved": False, "activate": obj}
        else:
            self.open_object(obj)
        ev.accept()

    def open_object(self, obj):
        """What a double-click that did not move does: open the thing.

        An analysis made HERE also gets its cursors back, so its interval can
        be adjusted and the analysis recomputed. That used to happen on the
        press, which made a panel analysis's label the one label that could
        not be double-click-dragged - and every new analysis is a panel one.
        """
        several = (self.doc is not None and obj.selected
                   and sum(isinstance(o, model.Analysis)
                           for o in self.doc.selected()) > 1)
        if (isinstance(obj, model.Analysis) and obj.source == "panel"
                and self.doc is not None and not several):
            cursors = obj.cursors()
            if len(cursors) == 2:
                self.doc.select_only([obj.scan])
                self.selection_changed.emit()
                self.start_measure(obj.scan, cursors, editing=obj,
                                   span=obj.span)
        # ...and its settings, because opening an analysis should show the
        # analysis. The cursors adjust the interval; the dialog the rest.
        self.activated.emit(obj)

    def enterEvent(self, ev):
        self.setFocus(Qt.MouseFocusReason)
        QWidget.enterEvent(self, ev)

    def leaveEvent(self, ev):
        self._cursor = None
        self.setCursor(Qt.ArrowCursor)
        self.hovered.emit("")
        self.update()
        QWidget.leaveEvent(self, ev)

    def resizeEvent(self, ev):
        QWidget.resizeEvent(self, ev)
        self.invalidate()

    # -------------------------------------------------------------- readouts
    def readout(self, pos):
        """What the status line says: where the cursor is, on which scan."""
        doc = self.doc
        if doc is None:
            return ""
        x = self.px_to_x(pos.x())
        y = self.px_to_y(pos.y())
        text = "{} = {:.4g} {}".format(_X_SYMBOLS.get(doc.x_quantity, "x"),
                                       x, doc.x_unit)
        if doc.x_quantity != units.D:
            d = units.d_spacing(x, doc.x_quantity, doc.wavelength())
            if d is not None and np.isfinite(d):
                text += " (d = {:.4g} A)".format(d)
        text += "   y = {:.4g} {}".format(y, doc.y_axis_unit())
        trace = self._trace_at(pos)
        if trace is not None:
            text += "   |   {}".format(trace.name)
            if trace.missing:
                text += "   NO {}".format(trace.missing.upper())
            else:
                said = self.reflection_readout(trace.scan, pos)
                if said:
                    text += "   " + said
                elif trace.scan.offset:
                    text += "   offset {:+.5g} {}".format(
                        trace.scan.offset, doc.unit_for(trace.scan))
        return text

    def reflections_under(self, scan, pos):
        """The reflections of a simulated `scan` the pointer at `pos` is
        over, nearest first: those it DRAWS (`Scan.drawn_reflections`)
        within the pick distance along x - or, on a profile, within one
        peak width, the reach of a drawn peak. [] for a measured one."""
        sample = scan.sample
        if not sample.simulated or self.doc is None:
            return []
        reach_px = self.pick_radius()
        xs = np.array([self.px_to_x(pos.x() + step)
                       for step in (-reach_px, 0.0, reach_px)])
        angles = units.to_two_theta(xs, self.doc.x_quantity,
                                    sample.wavelength)
        if angles is None or not np.isfinite(angles[1]):
            return []
        sides = np.abs(angles - angles[1])
        reach = float(np.nanmax(sides)) if np.isfinite(sides).any() else 0.0
        if scan.drawing == crystal.DRAW_CURVE:
            reach = max(reach, float(sample.sim_fwhm))
        return crystal.reflections_near(scan.drawn_reflections(),
                                        float(angles[1]), reach)

    def reflection_readout(self, scan, pos):
        """"(1 1 1) and 7 more: d = 3.135 A, 2-theta = 28.44 deg, I = 100;
        also (2 0 0)" - which lattice plane the peak under the pointer
        belongs to (MoloM's readout), or ""."""
        near = self.reflections_under(scan, pos)
        if not near:
            return ""
        first = near[0]
        text = "{}{}d = {:.4g} A, {} = {:.4g} deg, I = {:.3g}".format(
            first.name(), ": " if first.hkl else "reflection at ", first.d,
            _X_SYMBOLS[units.TWO_THETA], first.two_theta, first.intensity)
        others = [r.label() or "d = {:.4g} A".format(r.d) for r in near[1:]]
        if others:
            text += "; also {}{}".format(
                ", ".join(others[:3]),
                " and {} more".format(len(others) - 3)
                if len(others) > 3 else "")
        return text

    def _move_readout(self):
        state = self._move
        if state is None:
            return ""
        first = state["objs"][0]
        unit = ((self.doc.unit_for(first) if isinstance(first, model.Scan)
                 else self.doc.y_axis_unit()) if self.doc else "")
        if self.is_artist(first):
            unit = self._typed_unit(first, state.get("axis"))
        if state["typed"]:
            return "GRAB {} {}   (Enter to confirm, Esc to cancel)".format(
                state["typed"], unit)
        first = state["objs"][0]
        lock = ("  [{} locked]".format(state["axis"].upper())
                if state.get("axis") else "")
        if self.is_artist(first):
            return "MOVE {} to x {:.2f}, y {:.2f} of the plot{}".format(
                first.kind, first.x, first.y, lock)
        if isinstance(first, model.Analysis) and state.get("axis") == "x":
            return "SLIDE the label along its interval{}".format(lock)
        if isinstance(first, model.Analysis):
            return "MOVE the label {:+.0f} px (X slides an integral's){}".format(
                first.label_dy - state["origin"][0][0],
                "  - {} together".format(len(state["objs"]))
                if len(state["objs"]) > 1 else "")
        if isinstance(first, model.OffsetMarker):
            return "MOVE {} offset marker(s){}".format(
                sum(isinstance(o, model.OffsetMarker)
                    for o in state["objs"]), lock)
        if isinstance(first, model.Axis):
            return "MOVE the {} caption (it stays in the margin)".format(
                first.which)
        # Two significant figures while it moves;
        # a typed number is shown as typed (GRAB, above).
        return "MOVE {} {}   (type a number, Enter, or Esc){}".format(
            numbers.write(first.offset - state["origin"][0][0], "%+.2g"),
            unit, lock)

    # -------------------------------------------------------------- blinking
    def _sync_blink(self):
        need = any(t.missing for t in self.traces)
        if need and not self._blink_timer.isActive():
            self._blink_timer.start()
        elif not need and self._blink_timer.isActive():
            self._blink_timer.stop()
            self._blink = 0

    def _tick_blink(self):
        self._blink = (self._blink + 1) % BLINK_PERIOD
        if self._blink in BLINK_ON or (self._blink - 1) % BLINK_PERIOD in BLINK_ON:
            self.update()

    def blink_lit(self):
        """Whether the alarm is showing this instant.

        A double flash with a long pause, rather than a steady strobe: it has
        to be noticeable at the edge of vision and must not make the window
        unpleasant to work in while a pattern waits to be sorted out.
        """
        return (self._move is None and self._drag is None
                and self._blink in BLINK_ON)

    # -------------------------------------------------------------- painting
    def _key(self):
        doc = self.doc
        # The selection of EVERY object: a selected artist, analysis label or
        # axis caption is drawn orange into the cache, and a click that only
        # changes selection just asks for a repaint. With artists missing
        # here, clicking off a label left it orange until something else
        # rebuilt the plot.
        selection = (tuple(obj.selected for obj in doc.objects())
                     if doc is not None else ())
        return (selection, self.drafting(), self.canvas_size(), self.page(),
                self.width(), self.height(), self.devicePixelRatioF(),
                self.view_x(), self.view_y(),
                doc.y_key() if doc else "",
                tuple(sorted((doc.x_break or {}).items())) if doc else (),
                ((doc.offset_markers,
                  style.figure_value(doc, "offset_marker_size"),
                  tuple((s.marker.at, s.marker.dy, s.marker.size,
                         s.marker.colour, s.marker.visible,
                         s.marker.number_format)
                        for s in doc.scans))
                 if doc else ()),
                (tuple(style.figure_value(doc, key) for key in (
                    "font_family", "position_format", "value_format",
                    "offset_format", "band_marker_size", "region_size",
                    "span_size"))
                 + tuple((a.number_format, tuple(a.hidden_numbers or ()),
                          tuple(a.hidden_context or ()))
                         for a in doc.axes.values())
                 if doc else ()),
                tuple((r.lo, r.hi, r.shade, r.opacity, r.factor,
                       tuple(id(s) for s in r.scans), r.text, r.x, r.y,
                       r.colour, r.size, r.rotation, r.anchor, r.visible,
                       r.z) for r in doc.regions) if doc else (),
                tuple((s.x0, s.x1, tuple(id(e) for e in s.ends), s.y,
                       s.text, s.place, s.colour, s.size, s.head,
                       s.line_width, s.number_format, s.visible, s.z,
                       s.end_values()) for s in doc.spans) if doc else (),
                tuple((id(t.scan), t.scan.offset,
                       t.colour.rgb(), t.scan.selected, t.missing,
                       tuple((id(a), a.visible, a.selected, a.colour,
                              a.label, a.number_format, a.show_interval,
                              a.model_name,
                              tuple(a.cursors()) if a.visible else (),
                              tuple(a.span) if a.span else None)
                             for a in t.scan.analysis_objects),
                       len(t.x) if t.x is not None else 0)
                      for t in self.traces))

    def paintEvent(self, _ev):
        # The painter is ENDED whatever happens: a Python error while it was
        # open (between a save and its restore) did not stay a logged error
        # - Qt was left with a half-used painter and the process died with
        # an access violation (S spreading scans did that).
        painter = QPainter(self)
        try:
            self._paint_widget(painter)
        finally:
            painter.end()

    def _paint_widget(self, painter):
        key = self._key()
        if self._cache is None or self._cache_key != key:
            full_due, self._full_due = getattr(self, "_full_due", False), False
            # Drawn before (an invalidation empties the cache but keeps the
            # key), and a change while nothing was moving: a draft now, the
            # full drawing when it settles (`_settled`).
            if (getattr(self, "_cache_key", None) is not None
                    and not full_due and self.SETTLE_MS > 0
                    and not self.drafting()):
                self._settle_later()
                key = self._key()
            self._cache = self._render()
            self._cache_key = key
        painter.drawPixmap(0, 0, self._cache)
        # Only what follows the cursor is painted per event, so a mouse move
        # is a blit and a few lines rather than a rebuild of the curves.
        painter.save()
        dx, dy, k = self.page()
        painter.translate(dx, dy)
        painter.scale(k, k)
        painter.setClipRect(self.plot_rect())
        self._paint_names(painter)
        self._paint_cursor(painter)
        self._paint_band(painter)
        self._paint_select_box(painter)
        self._paint_scale(painter)
        self._paint_offsets(painter)
        self._paint_alarms(painter)
        self._paint_hidden(painter)
        self._paint_measure(painter)
        painter.restore()
        self._paint_page_handles(painter)
        self._paint_margin_gizmos(painter)
        self._paint_page_margin_blades(painter)
        self._paint_flash(painter)

    # ------------------------------------------------------------- the flash
    #: How long "Saved ..." stays up, in seconds; it holds for the first third
    #: and fades for the rest.
    FLASH_SECONDS = 2.2

    def flash(self, text, seconds=None, warn=False):
        """A message that fades out over the top of the plot - green, or
        orange (`warn`) for something the program did on its own that the
        user should know about.

        The status bar already says what happened, and saving is
        exactly the moment nobody is looking at the bottom of the window.
        Painted per event and never into the cache, so an export cannot
        carry it.
        """
        self._flash = (str(text), time.monotonic(),
                       float(seconds or self.FLASH_SECONDS), bool(warn))
        self.update()
        QTimer.singleShot(40, self._flash_tick)

    def flashing(self):
        """The text on screen right now, or None. For tests."""
        return self._flash[0] if self._flash else None

    def _flash_tick(self):
        if not self._flash:
            return
        _text, started, seconds = self._flash[:3]
        if time.monotonic() - started >= seconds:
            self._flash = None
            self.update()
            return
        self.update()
        QTimer.singleShot(40, self._flash_tick)

    def _paint_flash(self, p):
        if not self._flash:
            return
        text, started, seconds = self._flash[:3]
        warn = len(self._flash) > 3 and self._flash[3]
        left = seconds - (time.monotonic() - started)
        if left <= 0:
            return
        # Hold at full opacity for the first third, then fade - a message
        # that starts fading at once is one you read half of.
        alpha = int(235 * min(1.0, left / (seconds * 0.66)))
        p.save()
        font = QFont(self.font())
        font.setPointSizeF(max(11.0, font.pointSizeF() * 1.25))
        font.setBold(True)
        p.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 26
        height = metrics.height() + 12
        # In PANE pixels, over the middle of the axes box wherever the page
        # sits and however it is scaled.
        rect = self.plot_rect()
        centre = self.to_widget(QPointF(rect.center().x(), rect.top()))
        x = centre.x() - width // 2
        y = centre.y() + 18
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        if warn:
            p.setBrush(QColor(92, 54, 12, int(alpha * 0.88)))
        else:
            p.setBrush(QColor(38, 74, 48, int(alpha * 0.85)))
        p.drawRoundedRect(QRectF(x, y, width, height), 5, 5)
        p.setPen(QColor(255, 196, 120, alpha) if warn
                 else QColor(170, 235, 180, alpha))
        p.drawText(QPointF(x + 13, y + metrics.ascent() + 6), text)
        p.restore()

    def hidden_shown(self):
        """The traces whose truncated ends are on screen right now: the one
        under the pointer and the selected ones."""
        hovered = (self._trace_at(self._cursor)
                   if self._cursor is not None and self._move is None
                   else None)
        return [t for t in self.traces
                if t.hidden and (t is hovered or t.scan.selected)]

    def _paint_hidden(self, p):
        """A scan's truncated ends, DASHED, while it is hovered or selected.

        So what was cut is never invisible to the person working on it, and
        never in the figure: painted per event, like the name readout, so no
        export can carry it.
        """
        shown = self.hidden_shown()
        if not shown:
            return
        rect = self.plot_rect()
        limit = max(50, 2 * rect.width())
        p.setRenderHint(QPainter.Antialiasing, True)
        for trace in shown:
            colour = trace_colour(trace)
            colour.setAlpha(170)
            pen = QPen(colour, self.style_of(trace.scan, "line_width")
                       * CURVE_WIDTH, Qt.DashLine)
            p.setPen(pen)
            for x, y in trace.hidden:
                stride = max(1, int(len(x) // limit))
                # Flagged samples (NaN) break the line; a NaN point in a
                # polyline draws nothing sensible.
                for xs, ys in _finite_runs(x[::stride], y[::stride]):
                    p.drawPolyline(_polyline(
                        self.x_to_px(xs, rect),
                        self.sy_to_px(trace.scan, ys, rect)))
        p.setRenderHint(QPainter.Antialiasing, False)

    def page_colour(self):
        """The page: its own colour (`Document.background`) or the
        theme's."""
        own = getattr(self.doc, "background", None) if self.doc else None
        return QColor(own) if own else QColor(_BG)

    def _surround(self):
        return (self.page_colour()
                if self.layout_mode() == figure_module.MODE_WINDOW
                and not self.page_zoomed() else _SURROUND)

    def drafting(self):
        """True while a hand is at work - a drag, a pan, a swipe or a pinch
        still settling, a G, S or R: the figure is drawn as a DRAFT then,
        every curve a hairline (`_DraftPainter`), and once more in full
        when the hand stops (the cache keys on this)."""
        timer = getattr(self, "_draft_timer", None)
        return bool(getattr(self, "_handling", False)
                    or self._nav is not None
                    or self._view_burst is not None
                    or self._move is not None
                    or self._scale is not None
                    or (timer is not None and timer.isActive()))

    #: How long the figure stays a draft after the last change - a typed
    #: number, a click, a step of a spin box - before its one full redraw,
    #: in ms. A full redraw of ten patterns on a 150 % screen takes ~0.2 s, a
    #: draft ~0.02 s: every change shows at once, and the full drawing
    #: comes when the hand has stopped. 0 draws every change in full (the
    #: tests do, to read pixels).
    SETTLE_MS = 150

    def _settle_later(self):
        """Keep drawing drafts until nothing has changed for `SETTLE_MS`."""
        timer = getattr(self, "_draft_timer", None)
        if timer is None:
            timer = self._draft_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._settled)
        timer.start(int(self.SETTLE_MS))

    def _settled(self):
        self._full_due = True
        self.update()

    def _render(self):
        """The figure, on its page, into the cache.

        A figure of fixed size or aspect is drawn in its OWN drawing units
        and scaled onto the pane (`page`), so what is on screen is the export
        at a zoom, not a re-layout of it.
        """
        ratio = float(self.devicePixelRatioF() or 1.0)
        pixmap = QPixmap(max(1, int(round(self.width() * ratio))),
                         max(1, int(round(self.height() * ratio))))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(self._surround())
        painter = _DraftPainter(pixmap) if self.drafting() else QPainter(
            pixmap)
        dx, dy, k = self.page()
        painter.translate(dx, dy)
        painter.scale(k, k)
        canvas_w, canvas_h = self.canvas_size()
        painter.fillRect(QRectF(0.0, 0.0, canvas_w, canvas_h),
                         self.page_colour())
        self.paint_into(painter)
        painter.end()
        return pixmap

    #: The colours of the two marks an SVG export finds its clip by
    #: (`clip_svg`). One unit wide, at 1/255 opacity: invisible even where
    #: the marks were never taken out.
    CLIP_OPEN = "#0a0b0c"
    CLIP_CLOSE = "#0c0b0a"

    def _clip_mark(self, p, rect, opening):
        """Mark where the axes' clip starts and ends, for an SVG export.

        Qt's SVG writer ignores `setClipRect`, so an SVG drew a curve past
        the y range, a shaded integral and an interval dash straight over
        the margin. While `self._svg_clip` is a list, the clipped part is
        fenced by two marks and the clip rectangle, in the file's
        coordinates, is recorded; `clip_svg` then wraps what is between
        the marks in a real `clipPath`.
        """
        if getattr(self, "_svg_clip", None) is None:
            return
        if opening:
            self._svg_clip.append(p.transform().mapRect(QRectF(rect)))
        mark = QColor(self.CLIP_OPEN if opening else self.CLIP_CLOSE)
        mark.setAlpha(1)
        p.fillRect(QRectF(rect.left(), rect.top(), 1.0, 1.0), mark)

    def paint_into(self, painter, columns=None):
        """Draw the whole plot onto any painter, at any resolution.

        The screen and the SVG export go through this one method, so a figure
        that disagrees with the window is not possible - the rule this project
        inherits from the PXRD window, where an export that quietly differs
        from the screen was judged worse than no export.
        """
        previous, self._columns_override = self._columns_override, columns
        # The fitted y range, worked out once for the whole paint (`data_y`).
        memo, self._fit_memo = getattr(self, "_fit_memo", False), {}
        try:
            self._paint_all(painter)
        finally:
            self._columns_override = previous
            self._fit_memo = memo

    @staticmethod
    def _crisp(p):
        """True where a frame line may go without antialiasing and stay
        even: one drawing unit is a whole number of device pixels. A page
        scaled 1.4 onto a 150 % screen is not, and an aliased line there is
        two or three pixels wide by where it falls - choppy spines and
        ticks. There it is antialiased like the curves."""
        device = p.device()
        ratio = float(device.devicePixelRatioF() or 1.0) if device else 1.0
        scale = abs(p.transform().m11()) * ratio
        return scale >= 1.0 and abs(scale - round(scale)) < 0.01

    def _paint_all(self, p):
        # A painter on an image or an SVG starts from the APPLICATION font,
        # not this widget's: set the figure's, or exports lose the family.
        p.setFont(self.figure_font())
        rect = self.plot_rect()
        self._label_boxes = []
        self._analysis_boxes = []
        self._marker_boxes = []
        self._image_boxes = []
        self._axis_boxes = []
        self._text_boxes = []
        frame_aa = not self._crisp(p)
        p.setRenderHint(QPainter.Antialiasing, frame_aa)
        self._paint_grid(p, rect)
        if not self.traces:
            p.setPen(_TEXT_DIM)
            canvas_w, canvas_h = self.canvas_size()
            p.drawText(QRectF(0.0, 0.0, canvas_w, canvas_h), Qt.AlignCenter,
                       "Nothing to plot.\nDrop patterns here: .raw, .brml, "
                       ".dat, .xy, .cif, .xml")
            # What was put on the figure is drawn anyway: a structure or a
            # label pasted into an empty tab vanished without a word.
            p.setRenderHint(QPainter.Antialiasing, True)
            for _z, _order, _inside, draw in self._paint_items(p, rect):
                draw()
            p.setRenderHint(QPainter.Antialiasing, frame_aa)
            self._paint_frame(p, rect)
            return
        p.setRenderHint(QPainter.Antialiasing, True)
        # In the STACK ORDER: every object's z (`model.z_of`), its kind's
        # place while nobody has chosen one. What is measured - curves,
        # analyses, markers - is CLIPPED to the axes: a zoomed-in view
        # still has points off both sides, and without the clip they are
        # drawn across the margins, the numbers and the caption. The
        # furniture is not clipped.
        clipped = False
        for _z, _order, inside, draw in self._paint_items(p, rect):
            if inside and not clipped:
                p.save()
                p.setClipRect(rect)
                self._clip_mark(p, rect, True)
                clipped = True
            elif clipped and not inside:
                self._clip_mark(p, rect, False)
                p.restore()
                clipped = False
            draw()
        if clipped:
            self._clip_mark(p, rect, False)
            p.restore()
        p.setRenderHint(QPainter.Antialiasing, frame_aa)
        self._paint_frame(p, rect)

    def _paint_items(self, p, rect):
        """`[(z, order, clipped, draw), ...]` for everything on the figure,
        bottom first."""
        doc = self.doc
        items = []

        def add(obj, inside, draw):
            items.append((model.z_of(obj), len(items), inside, draw))

        for trace in self.traces:
            if trace.missing is None:
                add(trace.scan, True,
                    lambda t=trace: self._paint_trace(p, rect, t))
                for analysis in trace.scan.visible_analyses():
                    add(analysis, True,
                        lambda t=trace, a=analysis: self._paint_analyses(
                            p, rect, t, only=a))
                if doc is not None and doc.offset_markers:
                    add(trace.scan.marker, True,
                        lambda t=trace: self._paint_offset_markers(
                            p, rect, only=t))
            else:
                add(trace.scan, True,
                    lambda t=trace: self._paint_placeholder(p, rect, t))
        if doc is not None:
            # Zoomed, the decorators move with the data (`rel_to_px`) and
            # what leaves the shot is cut at the axes like the curves; at
            # home they may sit in the margins as placed.
            cut = self.zoomed()
            for region in doc.regions:
                add(region, True, lambda r=region: self._paint_region(
                    p, rect, r))
            for span in doc.spans:
                add(span, cut, lambda s=span: self._paint_span(p, rect, s))
            add(doc.legend, cut, lambda: self._paint_legend(p, rect))
            for label in doc.labels:
                if getattr(label, "vline", None) is not None:
                    # A marker line's line is under the curves, its
                    # text where the label is.
                    items.append((min(model.z_of(label), 0.0) - 0.5,
                                  len(items), True,
                                  lambda lb=label: self._paint_vline(
                                      p, lb, rect)))
                add(label, cut, lambda lb=label: self._paint_text_labels(
                    p, rect, only=lb))
            for image in getattr(doc, "images", ()):
                add(image, cut, lambda im=image: self._paint_image(
                    p, rect, im))
            for structure in getattr(doc, "structures", ()):
                add(structure, cut, lambda m=structure: self._paint_molecule(
                    p, rect, m))
        items.sort(key=lambda item: (item[0], item[1]))
        return items

    # ------------------------------------------------------------ the pieces
    def _paint_frame(self, p, rect):
        """The two axis lines, each on its side, and their captions.

        The captions are the AXIS OBJECTS talking: their text, their size and
        their position along the axis, all draggable and all editable from
        the axis's own settings. Their boxes are kept for hit-testing, so a
        double-click on a caption opens the axis rather than doing nothing.
        """
        doc = self.doc
        p.setPen(QPen(_AXIS, 1))
        x_side = self.axis_side(doc.axes["x"]) if doc else "bottom"
        y_line = rect.bottom() if x_side == "bottom" else rect.top()
        seam = self.break_seam(rect)
        lines = [y_line]
        self._x_frame_line(p, rect, y_line, seam)
        # Each y axis drawn has its line on its side; one alone may close
        # the box on the far side (Origin's style, and the default).
        y_axes = (self.shown_y_axes() if doc is not None
                  else [model.Axis(0, "y")])
        for axis in y_axes:
            side = self.axis_side(axis)
            at = rect.left() if side == "left" else rect.right()
            p.drawLine(QPointF(at, rect.top()), QPointF(at, rect.bottom()))
            if doc is not None and self.y_mirrored(axis):
                far = rect.right() if side == "left" else rect.left()
                p.drawLine(QPointF(far, rect.top()),
                           QPointF(far, rect.bottom()))
        if doc is not None and doc.axes["x"].mirror:
            y_far = rect.top() if x_side == "bottom" else rect.bottom()
            self._x_frame_line(p, rect, y_far, seam)
            lines.append(y_far)
        if seam is not None:
            self._paint_break_marks(p, seam, lines)
        if doc is None:
            return
        for axis in self.shown_axes():
            which = axis.which
            if not axis.visible:
                continue
            font = QFont(p.font())
            font.setPointSizeF(self.style_of(axis, "label_size"))
            p.setFont(font)
            colour = QColor(_SELECT) if axis.selected else QColor(_TEXT)
            box = self.axis_label_rect(axis, rect, p)
            text = axis.caption(doc)
            if which == "x":
                draw_markup(p, box, text, font, colour)
            else:
                p.save()
                p.translate(box.center())
                p.rotate(-90)
                draw_markup(p, QRectF(-box.height() / 2.0, -box.width() / 2.0,
                                      box.height(), box.width()),
                            text, font, colour)
                p.restore()
            self._axis_boxes.append((axis, box.toRect()))

    #: A break's seam: a pair of slashes across the gap, each
    #: `BREAK_MARK` points wide and rising 0.6 of that, set 0.3 of the gap
    #: either side of the seam.
    BREAK_MARK = 9.0

    def break_seam(self, rect=None):
        """`(left, right, middle)` of the x axis's break on the page - the
        gap cut into the frame, `gap` points wide round the seam - or None
        (no break, or its seam out of the view)."""
        cut = self.x_break()
        if cut is None:
            return None
        rect = rect or self.plot_rect()
        lo, hi, _compress, gap = cut
        middle = float(self.x_to_px(0.5 * (lo + hi), rect))
        if not rect.left() < middle < rect.right():
            return None
        half = 0.5 * gap * PT
        return middle - half, middle + half, middle

    def in_break(self, value):
        """True for a position strictly inside the axis's break, where
        nothing is written (a tick, a number)."""
        cut = self.x_break()
        return cut is not None and cut[0] < value < cut[1]

    def _x_frame_line(self, p, rect, y, seam):
        """One horizontal line of the frame, cut at the break's seam."""
        if seam is None:
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            return
        p.drawLine(QPointF(rect.left(), y), QPointF(seam[0], y))
        p.drawLine(QPointF(seam[1], y), QPointF(rect.right(), y))

    def _paint_break_marks(self, p, seam, lines):
        """The two slashes on each horizontal line of the frame."""
        width = self.BREAK_MARK * PT / 2.0
        rise = 0.6 * width
        spread = 0.3 * (seam[1] - seam[0])
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(_AXIS, 1.0))
        for y in lines:
            for dx in (-spread, spread):
                x = seam[2] + dx
                p.drawLine(QPointF(x - width, y + rise),
                           QPointF(x + width, y - rise))
        p.restore()

    def axis_spine_rect(self, which):
        """The band that counts as the SPINE: the axis line and its ticks.

        Three targets per axis, each with its own window: the spine opens the
        ticks - lengths, steps, the opposite line; the numbers open their size
        and format; the caption opens the caption. OUTSIDE the axes box, on the
        axis's own side: a band reaching even a few pixels inside would swallow
        the click that starts a box select in the corner, which is exactly what
        it did.
        """
        rect = self.plot_rect()
        doc = self.doc
        if doc is None:
            return QRectF()
        axis = doc.axes[which]
        depth = max(6.0, self._tick_out(axis) + 3.0)
        return self._band(axis, rect, 0.0, depth)

    def axis_numbers_rect(self, which):
        """The band the axis's numbers sit in, beyond its spine band."""
        rect = self.plot_rect()
        doc = self.doc
        if doc is None:
            return QRectF()
        axis = doc.axes[which]
        if not getattr(axis, "show_numbers", True):
            return QRectF()
        start = max(6.0, self._tick_out(axis) + 3.0)
        return self._band(axis, rect, start,
                          max(4.0, self.tick_extent(axis) + 2.0 - start))

    def frame_line_gap(self, axis, point):
        """How far `point` is from one of `axis`'s LINES - its own, and the
        one closing the box when it is mirrored - while it is on the line
        (within 3 units) or over the ticks pointing INTO the box; else None.

        The spine band is outside the box, so with ticks pointing in (the
        default) the line and the ticks a double-click naturally goes for
        were not part of the axis at all."""
        rect = self.plot_rect()
        side = self.axis_side(axis)
        inward = (float(getattr(axis, "tick_length", 7.0))
                  if axis.ticks_inward else 0.0)
        lines = [(side, max(3.0, inward + 1.0))]
        mirrored = (bool(getattr(axis, "mirror", False)) if axis.which == "x"
                    else self.y_mirrored(axis))
        if mirrored:
            far = {"bottom": "top", "top": "bottom", "left": "right",
                   "right": "left"}[side]
            ticked = getattr(axis, "mirror_ticks", False)
            lines.append((far, max(3.0, inward + 1.0) if ticked else 3.0))
        best = None
        for where, depth in lines:
            if where in ("bottom", "top"):
                if not rect.left() - 1.0 <= point.x() <= rect.right() + 1.0:
                    continue
                inside = (rect.bottom() - point.y() if where == "bottom"
                          else point.y() - rect.top())
            else:
                if not rect.top() - 1.0 <= point.y() <= rect.bottom() + 1.0:
                    continue
                inside = (point.x() - rect.left() if where == "left"
                          else rect.right() - point.x())
            if -3.0 <= inside <= depth:
                best = abs(inside) if best is None else min(best, abs(inside))
        return best

    def _band(self, axis, rect, start, depth):
        """A band along an axis, `start` to `start + depth` out of the box."""
        side = self.axis_side(axis)
        if side == "bottom":
            return QRectF(rect.left(), rect.bottom() + 1 + start,
                          rect.width(), depth)
        if side == "top":
            return QRectF(rect.left(), rect.top() - 1 - start - depth,
                          rect.width(), depth)
        if side == "left":
            return QRectF(rect.left() - 1 - start - depth, rect.top(),
                          depth, rect.height())
        return QRectF(rect.right() + 1 + start, rect.top(), depth,
                      rect.height())

    def axis_hit(self):
        """Which part of an axis the last pick landed on: "caption",
        "spine" or "numbers"."""
        return self._axis_hit

    def _frame_part(self, obj):
        """True for an axis picked by its spine or numbers: those open
        their settings on a double-click and are never SELECTED - no
        orange, and the caption does not light up for them."""
        return isinstance(obj, model.Axis) and self._axis_hit != "caption"

    def axis_label_rect(self, axis, rect=None, painter=None):
        """Where an axis caption sits, as a QRectF.

        `label_along` runs from 0 to 1 ALONG the axis; the caption keeps
        `caption_gap` from the axis's NUMBERS, on the axis's own side: below
        or above them for x, left or right of the widest for y. Kept on the
        figure and never over the data.
        """
        rect = rect or self.plot_rect()
        canvas_w, canvas_h = self.canvas_size()
        font = QFont(painter.font() if painter is not None else self.font())
        font.setPointSizeF(self.style_of(axis, "label_size"))
        text = axis.caption(self.doc) if self.doc else ""
        width, height = markup_size(text, font)
        width += 8
        height += 2
        gap = self.caption_gap(axis)
        reach = self.tick_extent(axis)
        side = self.axis_side(axis)
        # Kept on the page - but only its LETTERS: the empty part of its box
        # (leading, descent) may hang over the edge, or a margin tightened
        # to the caption's last pixel pushed it back in.
        facing = {"left": "top", "right": "bottom", "top": "top",
                  "bottom": "bottom"}[side]
        blank = self._ink_blank(text, font, facing) if text else 0.0
        if axis.which == "x":
            span = max(1.0, rect.width() - width)
            left = rect.left() + _clamp(axis.label_along, 0.0, 1.0) * span
            if side == "bottom":
                top = _clamp(rect.bottom() + reach + gap, rect.bottom() + 1.0,
                             max(rect.bottom() + 1.0,
                                 canvas_h - height + blank))
            else:
                top = _clamp(rect.top() - reach - gap - height, -blank,
                             max(-blank, rect.top() - height - 1))
            return QRectF(left, top, width, height)
        span = max(1.0, rect.height() - width)
        centre_y = rect.bottom() - _clamp(axis.label_along, 0.0, 1.0) * span
        if side == "left":
            left = _clamp(rect.left() - reach - gap - height, -blank,
                          max(-blank, rect.left() - height - 1))
        else:
            left = _clamp(rect.right() + reach + gap, rect.right() + 1.0,
                          max(rect.right() + 1.0, canvas_w - height + blank))
        return QRectF(left, centre_y - width, height, width)

    def label_colour(self, label):
        """A caption's ink: its own colour, its scan's, or the theme's.

        A label that belongs to a line reads as part of that line, so "auto"
        means the scan's colour there and the theme's ink for a free one.
        """
        if label.selected:
            return QColor(_SELECT)
        if label.colour not in (None, "", "auto"):
            return (paper_colour(label.colour) if THEME == THEME_LIGHT
                    else QColor(label.colour))
        owner = getattr(label, "scan", None)
        if owner is not None:
            return (paper_colour(owner.colour) if THEME == THEME_LIGHT
                    else QColor(owner.colour))
        return QColor(_INK)

    @staticmethod
    def image_pixels(image):
        """The picture of an `ImageArtist`, decoded once."""
        if image._pixels is None:
            pixels = QImage()
            pixels.loadFromData(QByteArray.fromBase64(
                image.png.encode("ascii")))
            image._pixels = pixels
        return image._pixels

    def _image_box(self, image, rect):
        """An image's box before rotation: its width, the picture's own
        proportions, placed by its anchor."""
        pixels = self.image_pixels(image)
        width = max(4.0, float(image.width))
        height = (width * pixels.height() / float(pixels.width())
                  if not pixels.isNull() and pixels.width() else width)
        px, py = self.artist_point(image, rect)
        fx, fy = image.anchor_offsets()
        return QRectF(px - fx * width, py - fy * height, width, height)

    def _paint_image(self, p, rect, image):
        if not image.visible:
            return
        box = self._image_box(image, rect)
        with self._rotated(p, image, rect):
            p.save()
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            pixels = self.image_pixels(image)
            if image.mirror_h or image.mirror_v:
                pixels = _flipped(pixels, bool(image.mirror_h),
                                  bool(image.mirror_v))
            p.drawImage(box, pixels)
            if image.selected:
                p.setPen(QPen(_SELECT, 1.0, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(box)
            p.restore()
        self._image_boxes.append(
            (image, self.rotated_bounds(image, box, rect).toRect()))

    def _molecule_layout(self, molecule, rect):
        """`(box, points, pad)`: a structure's box before rotation, each
        atom's place in it, and `(left, top)` - where the atoms' corner sits
        inside the box.

        The box is what is DRAWN: the atoms, a margin for the bonds, and
        every label as it is written, hydrogens on their side. It used to
        be the atoms plus a fixed margin, which an `H2N` on the outside ran
        past - the selection cut through it."""
        length = max(2.0, float(molecule.bond_length))
        font = self.molecule_font(molecule)
        metrics = QFontMetrics(font)
        atoms = molecule.atoms or [{"x": 0.0, "y": 0.0}]
        low_x = min(a["x"] for a in atoms)
        high_y = max(a["y"] for a in atoms)
        local = [((a["x"] - low_x) * length, (high_y - a["y"]) * length)
                 for a in atoms]
        # The bonds reach past the atoms by half a double bond's spacing or
        # a wedge's wide end; round that up.
        margin = max(0.12 * length, 2.0 * float(molecule.bond_width))
        left = min(x for x, _y in local) - margin
        right = max(x for x, _y in local) + margin
        top = min(y for _x, y in local) - margin
        bottom = max(y for _x, y in local) + margin
        from ..core import chem
        neighbours = dict((i, []) for i in range(len(local)))
        for bond in molecule.bonds or ():
            a, b = int(bond["a"]), int(bond["b"])
            if a < len(local) and b < len(local):
                neighbours[a].append(b)
                neighbours[b].append(a)
        half = metrics.height() / 2.0 + 1.0
        # Labels kept level on a turned structure are laid out AFTER the
        # turn - their hydrogens on the side away from the bonds as drawn
        # - so the box takes each one as drawn and turns it back
        # (an OH's H stuck out of the selection otherwise).
        angle = float(getattr(molecule, "rotation", 0.0) or 0.0)
        level = bool(molecule.upright_labels) and abs(angle) > 1e-9
        spin = QTransform()
        spin.rotate(-angle)
        back = spin.inverted()[0]
        for index, atom in enumerate(molecule.atoms or ()):
            if not atom.get("show"):
                continue
            x, y = local[index]
            others = neighbours[index]
            if level:
                ahead = [spin.map(QPointF(local[o][0] - x, local[o][1] - y))
                         for o in others]
                to_left = bool(ahead) and (
                    sum(q.x() for q in ahead) / len(ahead) > 1e-6)
            else:
                to_left = bool(others) and (
                    sum(local[o][0] for o in others) / len(others) > x + 1e-6)
            text = chem.label_of(atom, hydrogens_left=to_left)
            element = markup_size(atom["el"], font)[0]
            whole = markup_size(text, font)[0]
            start = (-element / 2.0 if not to_left
                     else element / 2.0 - whole)
            # The label's corners about its atom, as drawn, then turned back.
            corners = [QPointF(start - 2.0, -half),
                       QPointF(start + whole + 2.0, -half),
                       QPointF(start - 2.0, half),
                       QPointF(start + whole + 2.0, half)]
            if level:
                corners = [back.map(c) for c in corners]
            for corner in corners:
                left = min(left, x + corner.x())
                right = max(right, x + corner.x())
                top = min(top, y + corner.y())
                bottom = max(bottom, y + corner.y())
        width, height = right - left, bottom - top
        px, py = self.artist_point(molecule, rect)
        fx, fy = molecule.anchor_offsets()
        box = QRectF(px - fx * width, py - fy * height, width, height)
        pad = (-left, -top)
        points = [QPointF(box.left() + pad[0] + x, box.top() + pad[1] + y)
                  for x, y in local]
        return box, points, pad

    def _turn_of(self, artist, rect):
        """The QTransform that turns an artist about its anchor."""
        turn = QTransform()
        angle = float(getattr(artist, "rotation", 0.0) or 0.0)
        if angle:
            ax, ay = self.artist_point(artist, rect)
            turn.translate(ax, ay)
            turn.rotate(-angle)
            turn.translate(-ax, -ay)
        return turn

    def _paint_molecule(self, p, rect, molecule):
        """A skeletal structure, as lines and text - vector in every export.

        The bonds are cut short at a labelled atom; a double bond in a ring
        has its second line inside the ring, one elsewhere is a centred
        pair; a triple bond is three lines. Rotated, the ATOMS turn and, by
        default, the labels stay upright.
        """
        from ..core import chem
        if not molecule.visible or not molecule.atoms:
            return
        box, points, pad = self._molecule_layout(molecule, rect)
        turn = self._turn_of(molecule, rect)
        length = max(2.0, float(molecule.bond_length))
        if molecule.colour in (None, "", "auto"):
            ink = QColor(_INK)
        else:
            ink = (paper_colour(molecule.colour) if THEME == THEME_LIGHT
                   else QColor(molecule.colour))
        font = self.molecule_font(molecule)
        metrics = QFontMetrics(font)
        upright = bool(molecule.upright_labels)
        p.save()
        if upright:
            # Turn the POINTS, not the painter: labels are drawn level.
            points = [turn.map(q) for q in points]
        else:
            p.setTransform(turn, True)
        low_x = min(a["x"] for a in molecule.atoms)
        high_y = max(a["y"] for a in molecule.atoms)

        def place(x, y):
            """A point of the structure (bond lengths, y up) on the page."""
            unturned = QPointF(box.left() + pad[0] + (x - low_x) * length,
                               box.top() + pad[1] + (high_y - y) * length)
            return turn.map(unturned) if upright else unturned

        shown = [bool(a.get("show")) for a in molecule.atoms]
        clear = 0.55 * metrics.height()
        pen = QPen(ink, max(0.1, float(molecule.bond_width)))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        gap = 0.18 * length
        for bond in molecule.bonds:
            a, b = int(bond["a"]), int(bond["b"])
            if a >= len(points) or b >= len(points):
                continue
            start, end = QPointF(points[a]), QPointF(points[b])
            span = math.hypot(end.x() - start.x(), end.y() - start.y())
            if span < 1e-6:
                continue
            ux, uy = (end.x() - start.x()) / span, (end.y() - start.y()) / span
            if shown[a]:
                start = QPointF(start.x() + ux * clear, start.y() + uy * clear)
            if shown[b]:
                end = QPointF(end.x() - ux * clear, end.y() - uy * clear)
            nx, ny = -uy, ux
            order = int(bond.get("order", 1))
            stereo = bond.get("stereo") if order == 1 else None
            if stereo in ("wedge", "hash"):
                self._paint_stereo_bond(p, start, end, stereo, length,
                                        float(molecule.bond_width), ink)
                continue
            if order == 2 and bond.get("ring"):
                centre = place(*bond["ring"])
                side = ((end.x() - start.x()) * (centre.y() - start.y())
                        - (end.y() - start.y()) * (centre.x() - start.x()))
                sign = 1.0 if side > 0 else -1.0
                trim = 0.12 * span
                p.drawLine(start, end)
                p.drawLine(QPointF(start.x() + ux * trim + nx * gap * sign,
                                   start.y() + uy * trim + ny * gap * sign),
                           QPointF(end.x() - ux * trim + nx * gap * sign,
                                   end.y() - uy * trim + ny * gap * sign))
            elif order == 2:
                for sign in (-0.5, 0.5):
                    p.drawLine(QPointF(start.x() + nx * gap * sign,
                                       start.y() + ny * gap * sign),
                               QPointF(end.x() + nx * gap * sign,
                                       end.y() + ny * gap * sign))
            elif order == 3:
                for sign in (-1.0, 0.0, 1.0):
                    p.drawLine(QPointF(start.x() + nx * gap * sign,
                                       start.y() + ny * gap * sign),
                               QPointF(end.x() + nx * gap * sign,
                                       end.y() + ny * gap * sign))
            else:
                p.drawLine(start, end)
        # The labels: the element centred on its atom, its hydrogens on the
        # side away from the bonds.
        neighbours = dict((i, []) for i in range(len(points)))
        for bond in molecule.bonds:
            a, b = int(bond["a"]), int(bond["b"])
            if a < len(points) and b < len(points):
                neighbours[a].append(b)
                neighbours[b].append(a)
        for index, atom in enumerate(molecule.atoms):
            if not shown[index]:
                continue
            here = points[index]
            others = neighbours[index]
            left = bool(others) and (
                sum(points[o].x() for o in others) / len(others) > here.x()
                + 1e-6)
            text = chem.label_of(atom, hydrogens_left=left)
            element = markup_size(atom["el"], font)[0]
            whole = markup_size(text, font)[0]
            start = (here.x() - element / 2.0 if not left
                     else here.x() + element / 2.0 - whole)
            label_box = QRectF(start, here.y() - metrics.height() / 2.0,
                               whole, metrics.height())
            draw_markup(p, label_box, text, font,
                        element_colour(atom["el"], ink)
                        if molecule.colour_by_element else ink)
        p.restore()
        if molecule.selected:
            p.save()
            p.setPen(QPen(_SELECT, 1.0, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(turn.map(QPolygonF(box)))
            p.restore()
        self._image_boxes.append(
            (molecule, self.rotated_bounds(molecule, box, rect).toRect()))

    #: A stereo bond in the ACS 1996 document style: the wide end of a
    #: wedge is the "bold width", 2.0 pt on a 14.4 pt bond; a hash's rungs
    #: are 2.5 pt apart. As fractions of the bond length, so they scale with
    #: the structure.
    WEDGE_WIDE = 2.0 / 14.4
    HASH_SPACING = 2.5 / 14.4

    def _paint_stereo_bond(self, p, start, end, kind, length, width, ink):
        """A wedge (towards the viewer) or a hash (away) from `start`, the
        stereocentre and narrow end, to `end`."""
        dx, dy = end.x() - start.x(), end.y() - start.y()
        span = math.hypot(dx, dy)
        if span < 1e-6:
            return
        ux, uy = dx / span, dy / span
        nx, ny = -uy, ux
        half_wide = self.WEDGE_WIDE * length / 2.0
        half_narrow = max(0.05, width / 2.0)
        p.save()
        if kind == "wedge":
            p.setPen(QPen(ink, 0.3))
            p.setBrush(ink)
            p.drawPolygon(QPolygonF([
                QPointF(start.x() + nx * half_narrow,
                        start.y() + ny * half_narrow),
                QPointF(end.x() + nx * half_wide, end.y() + ny * half_wide),
                QPointF(end.x() - nx * half_wide, end.y() - ny * half_wide),
                QPointF(start.x() - nx * half_narrow,
                        start.y() - ny * half_narrow)]))
        else:
            pen = QPen(ink, max(0.1, width))
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            rungs = max(3, int(round(span / (self.HASH_SPACING * length))))
            for k in range(rungs + 1):
                t = k / float(rungs)
                half = half_narrow + (half_wide - half_narrow) * t
                cx, cy = start.x() + dx * t, start.y() + dy * t
                p.drawLine(QPointF(cx + nx * half, cy + ny * half),
                           QPointF(cx - nx * half, cy - ny * half))
        p.restore()

    def _label_box(self, label, rect, base):
        """A label's box before rotation, and the font it is drawn in."""
        font = QFont(base)
        font.setPointSizeF(self.style_of(label, "size"))
        font.setBold(bool(label.bold))
        metrics = QFontMetrics(font)
        lines = self.label_text(label).split("\n")
        width = max(markup_size(line, font)[0] for line in lines) + 6
        height = metrics.height() * len(lines) + 2
        px, py = self.artist_point(label, rect)
        fx, fy = label.anchor_offsets()
        if getattr(label, "attached", False) and label.leader:
            # Its text over the point as an analysis label's: the flush
            # edge on the arrow, the middle `dy` from the curve.
            fx = {"left": 0.0, "right": 1.0}.get(_flush_of(label), 0.5)
            fy = 0.5
        return QRectF(px - fx * width, py - fy * height, width, height)

    @contextlib.contextmanager
    def _rotated(self, p, artist, rect):
        """Draw `artist` turned by its rotation about its anchor point."""
        angle = float(getattr(artist, "rotation", 0.0) or 0.0)
        if not angle:
            yield
            return
        ax, ay = self.artist_point(artist, rect)
        p.save()
        p.translate(ax, ay)
        p.rotate(-angle)
        p.translate(-ax, -ay)
        try:
            yield
        finally:
            p.restore()

    def rotated_bounds(self, artist, box, rect=None):
        """The screen box around `box` turned with `artist`: what it is
        picked by."""
        angle = float(getattr(artist, "rotation", 0.0) or 0.0)
        if not angle or box is None:
            return box
        rect = rect or self.plot_rect()
        ax, ay = self.artist_point(artist, rect)
        turn = QTransform()
        turn.translate(ax, ay)
        turn.rotate(-angle)
        turn.translate(-ax, -ay)
        return turn.mapRect(QRectF(box))

    def _paint_text_labels(self, p, rect, only=None):
        """The captions the user has put on the figure, and a note's arrow."""
        doc = self.doc
        if doc is None:
            return
        for label in doc.labels:
            if not label.visible or (only is not None and label is not only):
                continue
            if (getattr(label, "shows", None) == "multiplier"
                    and label.scan is not None and not label.scan.scaled):
                continue                # x1: nothing to say
            box = self._label_box(label, rect, p.font())
            font = QFont(p.font())
            font.setPointSizeF(self.style_of(label, "size"))
            font.setBold(bool(label.bold))
            if (getattr(label, "vline", None) is not None
                    and self.vline_px(label, rect) is None):
                continue
            with self._rotated(p, label, rect):
                p.save()
                # The same markup as every other text on the figure -
                # `*T*`, `_{g}`, and LaTeX between dollars. Drawn as plain
                # text, "(A)$_{1.00}$" stayed literal.
                draw_lines(p, box, self.label_text(label), font,
                           self.label_colour(label), _flush_of(label))
                p.restore()
            bounds = self.rotated_bounds(label, box, rect)
            self._paint_leader(p, label, bounds, rect)
            self._text_boxes.append((label, bounds.toRect()))

    def label_text(self, label):
        """What a label says: its text - a marker line's `{}` its
        position, in the house format."""
        text = str(label.text)
        if getattr(label, "vline", None) is not None and "{}" in text:
            return labels.fill_position(text, float(label.vline), self.doc)
        if (getattr(label, "shows", None) == "multiplier"
                and label.scan is not None):
            if not label.scan.scaled:
                return ""
            return re.sub(r"(?<![_^])\{\}", "{:.3g}".format(
                float(label.scan.multiplier)), text)
        return text

    def vline_px(self, label, rect=None):
        """A marker line's x in figure units, or None off the axes."""
        if getattr(label, "vline", None) is None:
            return None
        rect = rect or self.plot_rect()
        x = float(self.x_to_px(label.vline, rect))
        if not rect.left() <= x <= rect.right():
            return None
        return x

    def _paint_vline(self, p, label, rect):
        """A marker line's line: across the axes at its position, in the
        label's colour, dashed unless asked otherwise, 0.6 pt, and broken
        where its
        text stands. Broken, not papered over: a white box behind the text
        also cut the curves that ran under it. Nothing off the axes."""
        if not label.visible:
            return
        x = self.vline_px(label, rect)
        if x is None:
            return
        colour = self.label_colour(label)
        pen = QPen(colour, 0.6 * 96.0 / 72.0)
        if getattr(label, "line_dashed", True):
            pen.setDashPattern([6.0, 4.0])
        p.save()
        p.setPen(pen)
        top, bottom = rect.top(), rect.bottom()
        bounds = self.rotated_bounds(
            label, self._label_box(label, rect, self.figure_font()), rect)
        if bounds is not None and bounds.left() <= x <= bounds.right():
            gap = bounds.adjusted(0.0, -2.0, 0.0, 2.0)
            if gap.top() > top:
                p.drawLine(QPointF(x, top), QPointF(x, gap.top()))
            if gap.bottom() < bottom:
                p.drawLine(QPointF(x, gap.bottom()), QPointF(x, bottom))
        else:
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
        p.restore()

    # ------------------------------------------------------------ regions
    # A highlighted (and / or magnified) stretch of the x axis, one object
    # (`model.Region`). Its text
    # stands over the MIDDLE OF THE STRETCH AS DRAWN (with a break, not the
    # middle on x), `y` of the axes box from the top.
    def _middle_px(self, lo, hi, rect=None):
        """The middle of the stretch lo..hi on the page."""
        rect = rect or self.plot_rect()
        return 0.5 * (float(self.x_to_px(lo, rect))
                      + float(self.x_to_px(hi, rect)))

    def _own_colour(self, obj):
        colour = getattr(obj, "colour", "auto")
        if colour in (None, "", "auto"):
            return None
        return paper_colour(colour) if THEME == THEME_LIGHT else QColor(colour)

    def region_fill(self, region):
        """The shading's colour, before its opacity: its own, else grey."""
        return self._own_colour(region) or QColor(128, 128, 128)

    def region_ink(self, region):
        """A region's text: its own colour, else the pattern it magnifies
        (a magnifier's "x3" in the curve's colour), else
        the theme's ink."""
        if region.selected:
            return QColor(_SELECT)
        own = self._own_colour(region)
        if own is not None:
            return own
        if region.magnifies and len(region.scans) == 1:
            colour = region.scans[0].colour
            return (paper_colour(colour) if THEME == THEME_LIGHT
                    else QColor(colour))
        return QColor(_INK)

    def region_font(self, region):
        font = QFont(self.figure_font())
        font.setPointSizeF(max(4.0, float(self.style_of(region, "size"))))
        return font

    def region_text_box(self, region, rect=None):
        """Where a region's text is written (before its rotation), or None
        when it says nothing."""
        text = region.shown_text()
        if not text:
            return None
        rect = rect or self.plot_rect()
        font = self.region_font(region)
        lines = text.split("\n")
        width = max(markup_size(line, font)[0] for line in lines) + 6
        height = QFontMetrics(font).height() * len(lines) + 2
        px, py = self.artist_point(region, rect)
        fx, fy = region.anchor_offsets()
        return QRectF(px - fx * width, py - fy * height, width, height)

    def region_edge_at(self, pos):
        """`(region, "lo" or "hi")` when `pos` is on one EDGE of a region -
        of a selected one anywhere along it, of any other where no curve is
        under the pointer (marking an interval on a curve wins there) - or
        None. The nearest edge, a selected region's first."""
        doc = self.doc
        if doc is None or not getattr(doc, "regions", None):
            return None
        rect = self.plot_rect()
        if not rect.contains(pos):
            return None
        reach = self.pick_radius()
        on_curve = None
        best = None
        for region in doc.regions:
            if not region.visible:
                continue
            for side in ("lo", "hi"):
                gap = abs(pos.x() - float(self.x_to_px(getattr(region, side),
                                                       rect)))
                if gap > reach:
                    continue
                if not region.selected:
                    if on_curve is None:
                        on_curve = self._trace_at(pos) is not None
                    if on_curve:
                        continue
                rank = (0 if region.selected else 1, gap)
                if best is None or rank < best[0]:
                    best = (rank, region, side)
        return None if best is None else (best[1], best[2])

    def _start_region_edge(self, region, side):
        doc = self.doc
        if not region.selected:
            doc.select_only([region])
            self.selection_changed.emit()
        self._edge_drag = {"region": region, "side": side,
                           "stored": (region.lo, region.hi)}
        self.hovered.emit(self._region_edge_readout(region))
        self.invalidate()

    def _drag_region_edge(self, pos):
        drag = self._edge_drag
        rect = self.plot_rect()
        setattr(drag["region"], drag["side"],
                float(self.px_to_x(pos.x(), rect)))
        self.hovered.emit(self._region_edge_readout(drag["region"]))
        self.invalidate()

    def _region_edge_readout(self, region):
        low, high = sorted((float(region.lo), float(region.hi)))
        return "REGION from {} to {} {} - Esc puts it back".format(
            numbers.write(low, "%.4g"), numbers.write(high, "%.4g"),
            self.doc.x_unit if self.doc is not None else "")

    def _finish_region_edge(self, cancel=False):
        """The edge let go of: one undo step, the two limits in order."""
        drag, self._edge_drag = self._edge_drag, None
        region = drag["region"]
        now = sorted((float(region.lo), float(region.hi)))
        region.lo, region.hi = drag["stored"]
        if not cancel and tuple(now) != tuple(sorted(drag["stored"])):
            self.transform_done.emit([(region, "lo", now[0]),
                                      (region, "hi", now[1])],
                                     "region edge")
        self.hovered.emit("")
        self.invalidate()

    def _paint_region(self, p, rect, region):
        if not region.visible:
            return
        left, right = sorted((float(self.x_to_px(region.lo, rect)),
                              float(self.x_to_px(region.hi, rect))))
        band = QRectF(left, rect.top(), max(0.5, right - left), rect.height())
        if region.shade:
            fill = QColor(self.region_fill(region))
            fill.setAlphaF(min(1.0, max(0.0, float(region.opacity))))
            p.fillRect(band, fill)
        if region.selected:
            p.save()
            p.setPen(QPen(_SELECT, 1.0, Qt.DashLine))
            p.drawLine(QPointF(left, rect.top()), QPointF(left, rect.bottom()))
            p.drawLine(QPointF(right, rect.top()),
                       QPointF(right, rect.bottom()))
            # A grip on each edge: either is dragged on its own
            # (`region_edge_at`).
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(_SELECT))
            middle = rect.center().y()
            for x in (left, right):
                p.drawRoundedRect(QRectF(x - 3.0, middle - 11.0, 6.0, 22.0),
                                  2.0, 2.0)
            p.restore()
        box = self.region_text_box(region, rect)
        if box is None:
            return
        with self._rotated(p, region, rect):
            p.save()
            draw_lines(p, box, region.shown_text(), self.region_font(region),
                       self.region_ink(region), "center")
            p.restore()

    # ---------------------------------------------------- distance arrows
    # A double arrow between two positions at
    # `y` of the axes box from the top, its distance written above, below
    # or upright on it (`model.SpanArrow`).
    def span_font(self, span):
        font = QFont(self.figure_font())
        font.setPointSizeF(max(4.0, float(self.style_of(span, "size"))))
        return font

    def span_text(self, span):
        """What a distance arrow says: its template, `{}` the distance."""
        return labels.fill_position(labels.span_text(span, self.doc),
                                    span.distance(), self.doc,
                                    self.style_of(span, "number_format"))

    def span_ink(self, span):
        if span.selected:
            return QColor(_SELECT)
        return self._own_colour(span) or QColor(_INK)

    def span_geometry(self, span, rect=None):
        """`(x0, x1, y, box, text, font)` of a distance arrow on the page;
        `box` is where its text goes (for "on", the upright text's box)."""
        rect = rect or self.plot_rect()
        a, b = span.end_values()
        x0, x1 = float(self.x_to_px(a, rect)), float(self.x_to_px(b, rect))
        y = self.rel_to_px(0.0, float(span.y), rect)[1]
        font = self.span_font(span)
        text = self.span_text(span)
        width, height = markup_size(text, font)
        width += 6
        middle = 0.5 * (x0 + x1)
        pad = 3.0
        if span.place == "on":
            box = QRectF(middle - height / 2.0, y - width / 2.0, height,
                         width)
        elif span.place == "below":
            box = QRectF(middle - width / 2.0, y + pad, width, height)
        else:
            box = QRectF(middle - width / 2.0, y - pad - height, width,
                         height)
        return x0, x1, y, box, text, font

    def span_box(self, span, rect=None):
        """A distance arrow's whole box: its line and its text."""
        x0, x1, y, box, _text, _font = self.span_geometry(span, rect)
        half = max(2.0, float(span.head)) / 2.0
        return box.united(QRectF(min(x0, x1), y - half, abs(x1 - x0),
                                 2.0 * half))

    def span_gap(self, span, point, rect=None):
        """How far `point` is from a distance arrow, its line or its text."""
        x0, x1, y, box, _text, _font = self.span_geometry(span, rect)
        left, right = sorted((x0, x1))
        if left <= point.x() <= right:
            line = abs(point.y() - y)
        else:
            line = math.hypot(min(abs(point.x() - left),
                                  abs(point.x() - right)), point.y() - y)
        return min(line, _rect_distance(box, point))

    def _paint_span(self, p, rect, span):
        if not span.visible:
            return
        x0, x1, y, box, text, font = self.span_geometry(span, rect)
        ink = self.span_ink(span)
        head = max(2.0, float(span.head))
        length, half = 0.6 * head, 0.25 * head
        left, right = sorted((x0, x1))
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(ink, max(0.1, float(span.line_width)))
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        if right - left > 2.0 * length:
            p.drawLine(QPointF(left + length, y), QPointF(right - length, y))
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        for tip, way in ((left, 1.0), (right, -1.0)):
            p.drawPolygon(QPolygonF([
                QPointF(tip, y), QPointF(tip + way * length, y - half),
                QPointF(tip + way * length, y + half)]))
        if span.place == "on":
            # Upright ON the arrow, on the paper so the line does not run
            # through the letters.
            p.setBrush(self.page_colour())
            p.drawRect(box)
            p.translate(box.center())
            p.rotate(-90)
            draw_markup(p, QRectF(-box.height() / 2.0, -box.width() / 2.0,
                                  box.height(), box.width()), text, font, ink)
        else:
            draw_markup(p, box, text, font, ink)
        p.restore()

    # ------------------------------------------------------------- notes
    #: A note's arrowhead, in figure units: its length and half its width.
    LEADER_HEAD = (6.0, 2.4)

    def leader_tip(self, label, rect=None):
        """Where a note's arrow points, in figure units, or None."""
        leader = getattr(label, "leader", None)
        if not leader:
            return None
        rect = rect or self.plot_rect()
        if getattr(label, "attached", False):
            return self.attach_point(label, rect)
        return QPointF(float(self.x_to_px(leader[0], rect)),
                       float(self.ay_to_px(label, leader[1], rect))
                       + self.follow_px(label, rect))

    def leader_value(self, label, point, rect=None):
        """`[position, y]` that puts `label`'s tip at `point` (figure
        units), as its scan now stands. Changes nothing."""
        rect = rect or self.plot_rect()
        x = self.px_to_x(point.x(), rect)
        y = self.px_to_ay(label, point.y() - self.follow_px(label, rect),
                          rect)
        return [float(x), float(y)]

    def curve_point_near(self, pos, rect=None):
        """The nearest DRAWN point of any curve within the pick distance of
        `pos`, or None: where a note's tip snaps to."""
        best = None
        for trace in self.drawable():
            if trace.px is None or not len(trace.px):
                continue
            gaps = np.hypot(trace.px - pos.x(), trace.py - pos.y())
            i = int(np.argmin(gaps))
            if gaps[i] <= self.pick_radius() and (best is None
                                                   or gaps[i] < best[0]):
                best = (float(gaps[i]), QPointF(float(trace.px[i]),
                                                 float(trace.py[i])),
                        trace)
        return None if best is None else (best[1], best[2])

    def _paint_leader(self, p, label, bounds, rect):
        """A note's arrow: from the edge of its text nearest the point, to
        the point, with a filled head; a ring at the tip while selected, to
        grab it by."""
        tip = self.leader_tip(label, rect)
        if tip is None or bounds is None or bounds.contains(tip):
            return
        if getattr(label, "attached", False):
            # Straight from the text's edge to the point, as an analysis
            # label's arrow.
            start = QPointF(tip.x(), bounds.bottom() if
                            bounds.center().y() < tip.y() else bounds.top())
        else:
            start = self.leader_start(label, bounds, tip)
        dx, dy = tip.x() - start.x(), tip.y() - start.y()
        span = math.hypot(dx, dy)
        if span < 3.0:
            return
        ux, uy = dx / span, dy / span
        start = QPointF(start.x() + ux * 2.0, start.y() + uy * 2.0)
        length, half = self.LEADER_HEAD
        colour = self.leader_colour(label)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(colour, 0.9))
        base = QPointF(tip.x() - ux * length, tip.y() - uy * length)
        p.drawLine(start, base)
        p.setBrush(colour)
        p.setPen(Qt.NoPen)                   # one colour, no edge of its own
        p.drawPolygon(QPolygonF([
            tip, QPointF(base.x() - uy * half, base.y() + ux * half),
            QPointF(base.x() + uy * half, base.y() - ux * half)]))
        if label.selected:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_SELECT, 1.2))
            p.drawEllipse(tip, 3.5, 3.5)
        p.restore()

    @staticmethod
    def leader_start(label, bounds, tip):
        """Where a note's arrow leaves its text: the chosen point of the
        box (`leader_from`), or the point of the box nearest the tip."""
        chosen = getattr(label, "leader_from", "auto")
        if chosen in model.ANCHORS:
            fx = 0.0 if "left" in chosen else 1.0 if "right" in chosen else 0.5
            fy = (0.0 if "top" in chosen else 1.0 if "bottom" in chosen
                  else 0.5)
            return QPointF(bounds.left() + fx * bounds.width(),
                           bounds.top() + fy * bounds.height())
        return QPointF(min(max(tip.x(), bounds.left()), bounds.right()),
                       min(max(tip.y(), bounds.top()), bounds.bottom()))

    def leader_colour(self, label):
        """A note arrow's colour: its own, else the text's."""
        own = getattr(label, "leader_colour", "auto")
        if own in (None, "", "auto"):
            return self.label_colour(label)
        return (paper_colour(own) if THEME == THEME_LIGHT else QColor(own))

    def note_flush_changes(self, label, side):
        """Ctrl+L / R / M on a label: `[(obj, field, value), ...]`. Its
        lines line up that way; a NOTE also puts that edge of its text over
        the point, the arrow leaving straight from it - what the same keys
        do to an analysis label."""
        changes = [(label, "flush", side)]
        if getattr(label, "attached", False):
            return changes              # its flush edge IS over the point
        rect = self.plot_rect()
        tip = self.leader_tip(label, rect)
        if tip is None:
            return changes
        box = self.rotated_bounds(label, self.artist_box(label, rect), rect)
        if box is None:
            return changes
        row = "bottom" if box.center().y() < tip.y() else "top"
        edge = {"left": box.left(), "right": box.right(),
                "center": box.center().x()}[side]
        px, py = self.artist_point(label, rect)
        before = (label.x, label.y)
        self.set_artist_point(label, px + (tip.x() - edge), py, rect,
                              clamp=False)
        after = (label.x, label.y)
        label.x, label.y = before
        changes += [(label, "x", after[0]), (label, "y", after[1]),
                    (label, "leader_from",
                     row if side == "center" else "{} {}".format(row, side))]
        return changes

    def _slide_note(self, label, origin, dx_px, rect):
        """G, then X, on a note that belongs to a scan: its tip WALKS
        along the curve by `dx_px` and the text goes with it. False when
        there is no curve to walk."""
        trace = self._trace_of(label.scan)
        leader = origin[2] if len(origin) > 2 else None
        if (trace is None or trace.x is None or not len(trace.x)
                or not leader):
            return False
        state = self._move
        starts = state.setdefault("note_start", {})
        if id(label) not in starts:
            tip = QPointF(float(self.x_to_px(leader[0], rect)),
                          float(self.ay_to_px(label, leader[1], rect))
                          + self.follow_px(label, rect))
            index = self.sample_at(trace, tip, rect)
            starts[id(label)] = (index, tip)
        index, tip = starts[id(label)]
        if index is None:
            return False
        px, _py, shown = self._shown_samples(trace, rect)
        k0 = int(index) - trace.first
        if not (0 <= k0 < len(px) and shown[k0]):
            return False
        k = self._walk(px, shown, k0, px[k0] + dx_px)
        new_tip = self._sample_point(trace, trace.first + k, rect)
        ox, oy = self._artist_origin_px(label, origin, rect)
        self.set_artist_point(label, ox + new_tip.x() - tip.x(),
                              oy + new_tip.y() - tip.y(), rect, clamp=False)
        label.leader = self.leader_value(label, new_tip, rect)
        return True

    def leader_at(self, pos):
        """A SELECTED note whose arrow tip is within the pick distance of
        `pos`, or None: pressing there moves the tip."""
        doc = self.doc
        if doc is None:
            return None
        for label in doc.labels:
            if not (label.selected and label.visible and label.leader):
                continue
            if getattr(label, "attached", False):
                continue                # the whole note slides instead
            tip = self.leader_tip(label)
            if tip is not None and math.hypot(
                    tip.x() - pos.x(), tip.y() - pos.y()) <= self.pick_radius():
                return label
        return None

    def _drag_leader(self, pos):
        """The tip follows the pointer, snapping onto a curve near it."""
        state = self._leader_drag
        near = self.curve_point_near(pos)
        point = near[0] if near is not None else pos
        state["label"].leader = self.leader_value(state["label"], point)
        self.invalidate()

    def _finish_leader(self, cancel=False):
        state, self._leader_drag = self._leader_drag, None
        label = state["label"]
        new, label.leader = label.leader, state["stored"]
        if not cancel and new != state["stored"]:
            self.transform_done.emit([(label, "leader", new)],
                                     "move a note's arrow")
        self.invalidate()

    # ------------------------------------------------ labels on a curve
    # A label that belongs to a scan hangs from its curve like an analysis
    # label: a sample `at` and a distance (`dx`, `dy`, figure units) from
    # it. It rides the curve through offsets and zooms; a drag slides it
    # along the curve and changes the distance; a note's arrow drops
    # straight from its text onto the point.
    #: Where a new note's text stands above its point, in figure units.
    NOTE_DY = -40.0

    def _label_trace(self, scan):
        """The curve a label hangs from: drawn, or hidden (`_ghosts`)."""
        trace = self._trace_of(scan)
        if trace is not None:
            return trace
        for ghost in self._ghosts:
            if ghost.scan is scan:
                return ghost
        return None

    def attach_point(self, label, rect=None, at=None):
        """The point of the curve an attached label hangs from, in figure
        units, or None (no curve in this unit, or the sample not kept). A
        hidden curve still holds its labels where they were."""
        at = at if at is not None else getattr(label, "at", None)
        if not at or at[0] != "i" or getattr(label, "scan", None) is None:
            return None
        trace = self._label_trace(label.scan)
        if trace is None or trace.x is None or not len(trace.x):
            return None
        k = int(at[1]) - trace.first
        if not 0 <= k < len(trace.x):
            return None
        rect = rect or self.plot_rect()
        x = float(self.x_to_px(trace.x[k], rect))
        y = float(self.sy_to_px(trace.scan, trace.y[k], rect))
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        return QPointF(x, y)

    @staticmethod
    def label_dy(label):
        dy = getattr(label, "dy", None)
        return float(dy) if dy is not None else PlotWidget.NOTE_DY

    def _place_attached(self, label, px, py, rect):
        """`set_artist_point` for a label on its curve: the sample walks
        ALONG the curve towards the new place (a note's point under its
        text; a plain label keeps its sideways distance where it can) and
        the rest is the distance. False when there is no curve."""
        trace = self._label_trace(label.scan)
        if trace is None or trace.x is None or not len(trace.x):
            return False
        xs, ys, shown = self._shown_samples(trace, rect)
        k = int(label.at[1]) - trace.first
        if not 0 <= k < len(xs):
            return False
        note = bool(label.leader)
        aim = px if note else px - float(label.dx or 0.0)
        if shown[k]:
            k = self._walk(xs, shown, k, aim)
        label.at = ("i", trace.first + k)
        label.dx = 0.0 if note else float(px - xs[k])
        label.dy = float(py - ys[k])
        return True

    def attachment(self, label, scan=None, rect=None):
        """`(at, dx, dy)` that hang `label` from `scan`'s curve (its own
        scan's when None) exactly where it is drawn now - a note by its
        arrow's tip - or None when that curve is not drawn."""
        scan = scan if scan is not None else getattr(label, "scan", None)
        trace = self._trace_of(scan) if scan is not None else None
        if trace is None or trace.x is None or not len(trace.x):
            return None
        rect = rect or self.plot_rect()
        px, py = self.artist_point(label, rect)
        tip = self.leader_tip(label, rect) if label.leader else None
        index = self.sample_at(trace, tip if tip is not None
                               else QPointF(px, py), rect)
        if index is None:
            return None
        point = self._sample_point(trace, index, rect)
        if label.leader:
            box = self.rotated_bounds(label, self.artist_box(label, rect),
                                      rect)
            middle = box.center().y() if box is not None else py
            return (("i", int(index)), 0.0, float(middle - point.y()))
        return (("i", int(index)), float(px - point.x()),
                float(py - point.y()))

    def reframed(self, follow):
        """`[(artist, field, value), ...]` that keep every decorator placed
        on the page where it is drawn now once `doc.follow_zoom` is
        `follow` - their fractions of the home frame, or of the axes box as
        shown. Changes nothing."""
        doc = self.doc
        if doc is None:
            return []
        rect = self.plot_rect()
        drawn = []
        for artist in decorators_of(doc):
            if getattr(artist, "attached", False):
                continue
            if (getattr(artist, "space", model.SPACE_RELATIVE)
                    == model.SPACE_DATA):
                continue
            drawn.append((artist, self.rel_to_px(float(artist.x),
                                                 float(artist.y), rect)))
        before = doc.follow_zoom
        changes = []
        try:
            doc.follow_zoom = bool(follow)
            for artist, (px, py) in drawn:
                fx, fy = self.px_to_rel(px, py, rect)
                if (getattr(artist, "vline", None) is None
                        and not isinstance(artist, (model.Region,
                                                    model.SpanArrow))
                        and abs(fx - artist.x) > 1e-9):
                    changes.append((artist, "x", float(fx)))
                if abs(fy - artist.y) > 1e-9:
                    changes.append((artist, "y", float(fy)))
        finally:
            doc.follow_zoom = before
        return changes

    def attach_all(self):
        """Hang every label that belongs to a scan but not yet from its
        curve (an older session) where it is drawn. Returns how many."""
        doc = self.doc
        if doc is None:
            return 0
        done = 0
        for label in doc.labels:
            if (label.scan is None or label.vline is not None
                    or label.at is not None):
                continue
            found = self.attachment(label)
            if found is None:
                continue
            label.at, label.dx, label.dy = found
            done += 1
        if done:
            self.invalidate()
        return done

    def attach_at_x(self, label, position, rect=None):
        """The sample of `label`'s curve nearest `position` as shown
        (typed in its settings), or None."""
        trace = self._label_trace(label.scan)
        if trace is None or trace.x is None or not len(trace.x):
            return None
        rect = rect or self.plot_rect()
        xs, _ys, shown = self._shown_samples(trace, rect)
        aim = float(self.x_to_px(position, rect))
        pool = shown if shown.any() else np.isfinite(xs)
        k = int(np.argmin(np.where(pool, np.abs(xs - aim), np.inf)))
        return ("i", trace.first + k)

    def attached_x(self, label):
        """The position an attached label hangs at, or None."""
        if not getattr(label, "attached", False):
            return None
        return self._x_of(label.scan, int(label.at[1]))

    def _paint_grid(self, p, rect):
        """Ticks, numbers and (only if asked for) grid lines, each axis on
        its own side.

        A stacked figure's frame: ticks pointing IN, minor ticks between the
        numbered ones, mirrored on the far side, NO GRID; the y axis of a
        stack with neither ticks nor numbers. Nothing inside a break.
        """
        doc = self.doc
        rows = [("x", self.view_x(), self.x_to_px),
                ("y", self.view_y(), self.y_to_px)]
        for which, view, to_px in rows:
            axis = doc.axes[which] if doc else model.Axis(0, which)
            side = self.axis_side(axis)
            font = QFont(p.font())
            font.setPointSizeF(self.style_of(axis, "tick_size"))
            p.setFont(font)
            metrics = QFontMetrics(font)
            lo, hi = view
            step = self.tick_step(axis, lo, hi)
            subdivisions = max(1, int(getattr(axis, "minor_count", 5) or 1))
            minor = step / float(subdivisions)
            length = float(getattr(axis, "tick_length", 7.0))
            minor_length = float(getattr(axis, "minor_length", 3.0))
            ticks = getattr(axis, "show_ticks", True)
            # +1 points into the box, -1 out of it, from each side's line.
            inward = 1.0 if axis.ticks_inward else -1.0
            into = {"bottom": -1.0, "top": 1.0, "left": 1.0, "right": -1.0}[side]
            line = {"bottom": rect.bottom(), "top": rect.top(),
                    "left": rect.left(), "right": rect.right()}[side]
            # The opposite line takes ticks too when asked (no numbers).
            far = {"bottom": rect.top(), "top": rect.bottom(),
                   "left": rect.right(), "right": rect.left()}[side]
            mirrored = (getattr(axis, "mirror_ticks", False)
                        and (getattr(axis, "mirror", False) if which == "x"
                             else self.y_mirrored(axis)))
            numbers = getattr(axis, "show_numbers", True)

            def tick(at, size):
                if not ticks:
                    return
                ends = [(line, line + into * inward * size)]
                if mirrored:
                    ends.append((far, far - into * inward * size))
                for start, end in ends:
                    if which == "x":
                        p.drawLine(QPointF(at, start), QPointF(at, end))
                    else:
                        p.drawLine(QPointF(start, at), QPointF(end, at))

            value = math.ceil(lo / step) * step
            while value <= hi + 1e-9:
                if which == "x" and self.in_break(value):
                    value += step
                    continue
                at = float(to_px(value, rect))
                if axis.show_grid:
                    p.setPen(QPen(_GRID, 1))
                    if which == "x":
                        p.drawLine(QPointF(at, rect.top()),
                                   QPointF(at, rect.bottom()))
                    else:
                        p.drawLine(QPointF(rect.left(), at),
                                   QPointF(rect.right(), at))
                p.setPen(QPen(_AXIS, 1))
                tick(at, length)
                if numbers and not self.number_hidden(axis, value, step):
                    # Measured from the FONT: fixed boxes clipped bigger
                    # numbers instead of making room. A hidden number keeps
                    # its tick.
                    text = self.tick_text(axis, value, which)
                    p.setPen(_TEXT_DIM)
                    box, align = self._number_box(axis, side, at, text,
                                                  metrics, rect)
                    p.drawText(box, align, text)
                value += step
            if not ticks or not axis.minor_ticks or subdivisions < 2:
                continue
            p.setPen(QPen(_AXIS, 1))
            value = math.ceil(lo / minor) * minor
            while value <= hi + 1e-9:
                if not (which == "x" and self.in_break(value)):
                    tick(float(to_px(value, rect)), minor_length)
                value += minor

    def columns(self, rect):
        """Sample columns, in DEVICE pixels.

        `rect.width()` is logical, so on a 150% display a per-logical-pixel
        envelope is a staircase with 1.5-device-pixel treads. Reduce at the
        resolution the screen actually has and the treads disappear.
        """
        if self._columns_override:
            return max(1, int(self._columns_override))
        # ...and at the PAGE's zoom: an exact figure scaled 1.4 onto the
        # pane had 1.4-pixel treads, a visible staircase.
        return max(1, int(round(rect.width() * self.page()[2]
                                * float(self.devicePixelRatioF() or 1.0))))

    def _polylines(self, trace, rect, to_py=None):
        """The curve as polylines, decimated per pixel column.

        * the visible part is taken with a MASK, dilated by one sample so a
          stretch entering the view is not cut short;
        * the mask is split into CONTIGUOUS RUNS, so a curve that leaves the
          view and comes back is two polylines rather than one with a
          straight line drawn across the gap - and inside an x axis break
          the samples are left out, so the curve is cut at the seam rather
          than joined across it by a line the data never had.

        Within a run, the decimation groups CONSECUTIVE samples that fall in
        the same pixel column.
        """
        x, y = trace.x, trace.y
        if x is None or not len(x):
            return [], None, None
        lo, hi = self.view_x()
        # A sample the instrument flagged as empty is NaN (an SDT run's last
        # few): the curve breaks there rather than being drawn through it.
        with np.errstate(invalid="ignore"):
            inside = (x >= lo) & (x <= hi) & np.isfinite(y)
        if not inside.any():
            return [], None, None
        keep = inside.copy()
        keep[1:] |= inside[:-1]
        keep[:-1] |= inside[1:]
        keep &= np.isfinite(x) & np.isfinite(y)
        cut = self.x_break()
        if cut is not None:
            with np.errstate(invalid="ignore"):
                keep &= ~((x > cut[0]) & (x < cut[1]))
        idx = np.flatnonzero(keep)
        breaks = np.flatnonzero(np.diff(idx) > 1)
        runs = np.split(idx, breaks + 1)
        columns = self.columns(rect)
        left, width = rect.left(), max(1, rect.width())
        polys, all_px, all_py = [], [], []
        for run in runs:
            if len(run) < 2:
                continue
            xs, ys = x[run], y[run]
            px = np.asarray(self.x_to_px(xs, rect), dtype=float)
            py = (to_py(ys, rect) if to_py is not None
                  else self.sy_to_px(trace.scan, ys, rect))
            # A few samples per column are drawn as they are: a pattern is
            # thousands of points, not millions, and thinning below that
            # only costs shape.
            if len(px) > 4 * columns:
                col = np.clip(((px - left) / width * columns).astype(np.int64),
                              0, columns - 1)
                px, py = _m4(px, py, col)
            polys.append(_polyline(px, py))
            all_px.append(px)
            all_py.append(py)
        if not polys:
            return [], None, None
        return polys, np.concatenate(all_px), np.concatenate(all_py)

    def _paint_trace(self, p, rect, trace):
        if trace.scan.draws_lines:
            self._paint_lines(p, rect, trace)
            return
        polys, px, py = self._polylines(trace, rect)
        trace.px, trace.py = px, py
        width = self.style_of(trace.scan, "line_width") * CURVE_WIDTH
        if trace.scan.selected:
            halo = QColor(_SELECT)
            halo.setAlpha(90)
            p.setPen(QPen(halo, width + 4.0))
            for poly in polys:
                p.drawPolyline(poly)
            width += SELECTED_EXTRA
        p.setPen(QPen(trace_colour(trace), width))
        for poly in polys:
            p.drawPolyline(poly)

    def _paint_lines(self, p, rect, trace):
        """A simulated pattern drawn as LINES ACROSS THE PLOT: a fine
        dotted line at each of its strongest reflections, top to bottom of
        the axes box, in its colour - the suite's reflection markers. Its
        picking points run down each line."""
        lo, hi = self.view_x()
        cut = self.x_break()
        width = self.style_of(trace.scan, "line_width") * CURVE_WIDTH * 0.7
        if trace.scan.selected:
            width += SELECTED_EXTRA
        pen = QPen(trace_colour(trace), width, Qt.DotLine)
        p.setPen(pen)
        all_px, all_py = [], []
        steps = np.arange(rect.top(), rect.bottom() + 1.0, 3.0)
        for value in np.asarray(trace.x, dtype=float):
            if not (np.isfinite(value) and lo <= value <= hi):
                continue
            if cut is not None and cut[0] < value < cut[1]:
                continue
            x = float(self.x_to_px(value, rect))
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            all_px.append(np.full(len(steps), x))
            all_py.append(steps)
        if all_px:
            trace.px, trace.py = np.concatenate(all_px), np.concatenate(all_py)
        else:
            trace.px = trace.py = None

    def _paint_placeholder(self, p, rect, trace):
        """A scan that cannot be drawn in this unit, drawn as its absence.

        A dashed line at its own offset: it keeps its place in the stack, it
        can be picked and right-clicked, and it is plainly not a measurement.
        The alarm label beside it is painted per event so it can blink.
        """
        trace.px = trace.py = None
        y = self.sy_to_px(trace.scan, trace.scan.offset, rect)
        if not (rect.top() - 4 <= y <= rect.bottom() + 4):
            return
        colour = trace_colour(trace)
        colour.setAlpha(120)
        p.setPen(QPen(colour, 1.0, Qt.DashLine))
        p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

    def _paint_analyses(self, p, rect, trace, only=None):
        """The analyses switched on for this scan, drawn on its curve: a
        peak area shaded between the curve and the baseline it was measured
        above, a width as a line across the peak at half its height, the
        interval's dashes, and the label with its arrow to the peak."""
        doc = self.doc
        if doc is None:
            return
        lo, hi = self.view_x()
        for analysis in trace.scan.visible_analyses():
            if only is not None and analysis is not only:
                continue
            value = self.label_x(analysis)
            if value is None or not (lo <= value <= hi):
                continue
            anchor_y = self._curve_y_at(trace, value, rect,
                                        self._analysis_slice(trace, analysis))
            if anchor_y is None:
                continue
            colour = (QColor(_SELECT) if analysis.selected
                      else self.analysis_colour(analysis, trace))
            if analysis.shade and analysis.slides:
                self._paint_area(p, rect, trace, analysis, colour)
            if "width" in analysis.model_name.lower():
                self._paint_width(p, rect, trace, analysis, colour)
            # After the shading, so the dashes sit ON TOP of trace and fill.
            self._paint_interval(p, rect, trace, analysis)
            self._paint_analysis_label(p, rect, trace, analysis, value,
                                       anchor_y, colour)

    def _covered_samples(self, trace, analysis):
        """The trace's samples an analysis covers, as indices into its
        arrays (measured ones only), or None: its own stretch where it was
        measured along the curve, else every sample between its two
        positions."""
        if trace.x is None or trace.y is None or not len(trace.x):
            return None
        stretch = self._analysis_slice(trace, analysis)
        if stretch is not None:
            index = np.arange(stretch[0], stretch[1] + 1)
        else:
            cursors = analysis.cursors()
            if len(cursors) != 2:
                return None
            low, high = sorted(cursors)
            with np.errstate(invalid="ignore"):
                index = np.flatnonzero((trace.x >= low) & (trace.x <= high))
        if len(index) < 3:
            return None
        index = index[np.isfinite(trace.x[index])
                      & np.isfinite(trace.y[index])]
        return index if len(index) >= 3 else None

    def _covered(self, trace, analysis):
        """`(xs, ys)`: the part of the drawn curve an analysis covers."""
        index = self._covered_samples(trace, analysis)
        if index is None:
            return None
        return trace.x[index], trace.y[index]

    def area_baseline(self, trace, analysis):
        """`(xs, curve, baseline)` of a peak area as the axes draw them: the
        curve over its stretch and the straight baseline it was measured
        above, both as drawn (normalised, magnified, offset). None when it
        cannot be drawn."""
        index = self._covered_samples(trace, analysis)
        doc = self.doc
        scan = trace.scan
        values = scan.intensity()
        if index is None or doc is None or values is None:
            return None
        xs = trace.x[index]
        chord = _chord(xs, values[index + trace.first])
        _bx, base = scan.on_axes(doc, xs, chord)
        if base is None:
            return None
        return xs, trace.y[index], base

    def _paint_area(self, p, rect, trace, analysis, colour):
        """The shaded area of a peak: what was integrated."""
        found = self.area_baseline(trace, analysis)
        if found is None:
            return
        xs, top, base = found
        px = np.asarray(self.x_to_px(xs, rect), dtype=float)
        upper = np.asarray(self.sy_to_px(trace.scan, top, rect), dtype=float)
        lower = np.asarray(self.sy_to_px(trace.scan, base, rect), dtype=float)
        shape = QPolygonF()
        for a, b in zip(px.tolist(), upper.tolist()):
            shape.append(QPointF(a, b))
        for a, b in zip(px.tolist()[::-1], lower.tolist()[::-1]):
            shape.append(QPointF(a, b))
        fill = shade_fill(colour, self.page_colour(),
                          self.style_of(analysis, "shading")
                          == style.SHADING_OPAQUE)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPolygon(shape)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(colour, 0.8, Qt.SolidLine))
        p.drawPolyline(_polyline(px, lower))

    def width_line(self, trace, analysis, rect=None):
        """The two points where a peak width meets half height, on the
        page, or None."""
        doc = self.doc
        points = measure.width_points(analysis)
        if doc is None or len(points) != 2:
            return None
        rect = rect or self.plot_rect()
        xs = np.array([pt[0] for pt in points], dtype=float)
        shown = np.array([pt[1] for pt in points], dtype=float)
        bx, by = trace.scan.on_axes(doc, xs, shown)
        if bx is None or not np.all(np.isfinite(by)):
            return None
        px = self.x_to_px(bx, rect)
        py = self.sy_to_px(trace.scan, by, rect)
        return (QPointF(float(px[0]), float(py[0])),
                QPointF(float(px[1]), float(py[1])))

    def _paint_width(self, p, rect, trace, analysis, colour):
        """A peak width: a line across the peak at half its height, a short
        upright stop at each end."""
        ends = self.width_line(trace, analysis, rect)
        if ends is None:
            return
        a, b = ends
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(colour, 1.0))
        p.drawLine(a, b)
        for end in (a, b):
            p.drawLine(QPointF(end.x(), end.y() - 3.0),
                       QPointF(end.x(), end.y() + 3.0))
        p.restore()

    def label_offset(self, analysis, trace, rect):
        """How far the label sits from the curve, in figure units.

        `label_dy` of None means "work it out": beyond the tip of the peak,
        above it (below a negative area) - so the arrow never crosses it.
        Once it
        has been dragged the stored number wins."""
        if analysis.label_dy is not None:
            return float(analysis.label_dy)
        return -46.0 if self.peak_points_up(analysis, trace) else 46.0

    def peak_points_up(self, analysis, trace):
        """True when the peak rises on screen: always, unless it is a
        peak area that came out negative (the curve under its baseline).
        The curve's own chord is not asked: a sloped interval fooled it."""
        up = True
        area = model.number(analysis.fields.get("Area"))
        if area is not None and area < 0:
            up = not up
        return up

    def to_axis(self, value):
        """A stored position, in the axis's unit: itself (kept as a name
        the family's handling shares)."""
        return value

    def interval_marks(self, trace, analysis, rect=None):
        """What marks an analysis's interval, in pixels: `(dashes, [])` - a
        short upright dash at each bound, centred ON the trace. Every
        analysis with two cursors has them; `show_interval` decides whether
        they are PAINTED, not whether they are here."""
        rect = rect or self.plot_rect()
        cursors = analysis.cursors()
        if len(cursors) != 2 or trace.x is None or not len(trace.x):
            return [], []
        stretch = self._analysis_slice(trace, analysis)
        bounds = []
        if stretch is not None:
            for local in stretch:
                bounds.append(QPointF(
                    float(self.x_to_px(trace.x[local], rect)),
                    float(self.sy_to_px(trace.scan, trace.y[local], rect))))
        else:
            for x in sorted(cursors):
                y = self._curve_y_at(trace, x, rect)
                if y is None:
                    return [], []
                bounds.append(QPointF(float(self.x_to_px(x, rect)),
                                      float(y)))
        tick = float(self.style_of(analysis, "interval_size"))
        dashes = [(QPointF(b.x(), b.y() - tick),
                   QPointF(b.x(), b.y() + tick)) for b in bounds]
        return dashes, []

    def _paint_interval(self, p, rect, trace, analysis):
        """The interval's dashes, in the axis colour: structure, not data."""
        dashes, lines = self.interval_marks(trace, analysis, rect)
        if not analysis.show_interval:
            dashes = []
        if not (dashes or lines):
            return
        p.setPen(QPen(QColor(_AXIS), 1.0))
        for a, b in lines + dashes:
            # Cut to the axes box HERE, not only by the painter's clip: a
            # bound outside the view put its dash on the margin of an
            # exported figure.
            kept = _clip_segment(a, b, rect)
            if kept is not None:
                p.drawLine(*kept)

    def _paint_analysis_label(self, p, rect, trace, analysis, value, anchor_y,
                              colour):
        """The label, and the arrow from it to what it names.

        The label sits `label_dy` from the curve and the arrow is drawn
        between the two, so moving the label lengthens or shortens the
        arrow. `flush` says which edge of the text sits on the arrow."""
        font = QFont(p.font())
        font.setPointSizeF(max(5.0, float(self.style_of(analysis,
                                                        "label_size"))))
        p.setFont(font)
        text = labels.render(analysis, self.doc).text
        x = float(self.x_to_px(value, rect))
        label_y = float(anchor_y) + self.label_offset(analysis, trace, rect)
        width, height = markup_size(text, font)
        width += 6
        flush = self.analysis_flush(analysis)
        if flush == style.FLUSH_LEFT:
            left = x - 3.0
        elif flush == style.FLUSH_RIGHT:
            left = x - width + 3.0
        else:
            left = x - width / 2.0
        box = QRectF(left, label_y - height / 2.0, width, height)
        # The arrow runs from the edge of the text to the curve, so the head
        # lands ON the band and not inside the writing.
        start = box.bottom() + 2 if label_y < anchor_y else box.top() - 2
        step = 5.0 if label_y < anchor_y else -5.0
        p.setPen(QPen(colour, 1.0, Qt.SolidLine))
        p.drawLine(QPointF(x, start), QPointF(x, anchor_y - step))
        head = QPolygonF([QPointF(x, anchor_y),
                          QPointF(x - 3.0, anchor_y - step),
                          QPointF(x + 3.0, anchor_y - step)])
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(colour)
        p.setPen(Qt.NoPen)
        p.drawPolygon(head)
        p.restore()
        p.setBrush(Qt.NoBrush)
        draw_markup(p, box, text, font, colour)
        self._analysis_boxes.append((analysis, box.toRect()))

    def _shown_samples(self, trace, rect):
        """`(px, py, shown)` for a trace's kept samples: where each is on
        screen, and whether it is inside the axes box - the curve as it is
        actually SHOWN, truncated and framed."""
        px = np.asarray(self.x_to_px(trace.x, rect), dtype=float)
        py = np.asarray(self.sy_to_px(trace.scan, trace.y, rect), dtype=float)
        shown = (np.isfinite(px) & np.isfinite(py)
                 & (px >= rect.left()) & (px <= rect.right())
                 & (py >= rect.top()) & (py <= rect.bottom()))
        return px, py, shown

    def end_sample(self, trace, side, rect=None):
        """The sample at a curve's `side` end ("left" or "right") AS SHOWN
        - kept, and inside the axes box - walked `OFFSET_MARKER_INSET` in
        along the curve, as an index into the trace's kept samples; None
        when nothing of it is shown. Where a name label hangs."""
        rect = rect or self.plot_rect()
        if trace is None or trace.x is None or not len(trace.x):
            return None
        px, _py, shown = self._shown_samples(trace, rect)
        if not shown.any():
            return None
        if side == "right":
            start = int(np.argmax(np.where(shown, px, -np.inf)))
            return self._walk(px, shown, start,
                              px[start] - OFFSET_MARKER_INSET)
        start = int(np.argmin(np.where(shown, px, np.inf)))
        return self._walk(px, shown, start, px[start] + OFFSET_MARKER_INSET)

    @staticmethod
    def _walk(px, shown, k, target, slack=3.0):
        """From sample `k`, ALONG the curve (either way, sample by sample)
        towards screen x = `target`: the shown sample that gets closest
        before the curve turns away for good or leaves the view.

        A step that goes slightly the wrong way on the page is walked
        through; turning away by more than `slack` stops it.
        """
        n = len(px)
        best = k
        for step in (1, -1):
            j = k
            while 0 <= j + step < n and shown[j + step]:
                j += step
                gap = abs(px[j] - target)
                if gap < abs(px[best] - target):
                    best = j
                elif gap > abs(px[best] - target) + slack:
                    break
        return best

    def marker_sample(self, marker, trace=None, rect=None):
        """The sample a marker points at, as an index into its trace's KEPT
        samples, or None when that point is not on the shown curve.

        Unplaced, it is the leftmost point of the curve AS SHOWN - kept,
        and inside the view - walked a few units along the curve, so it
        sits tight against the y axis wherever the curve reaches it and at
        the shown end of one that starts later. Never the raw data: a
        truncated start or a stretch outside the view is not a place to
        point at."""
        rect = rect or self.plot_rect()
        trace = trace or self._trace_of(marker.scan)
        if trace is None or trace.x is None or not len(trace.x):
            return None
        px, _py, shown = self._shown_samples(trace, rect)
        if not shown.any():
            return None
        at = marker.at
        if at and at[0] == "i":
            k = int(at[1]) - trace.first
            return k if 0 <= k < len(px) and shown[k] else None
        if at and at[0] == "x":
            aim = float(self.x_to_px(float(at[1]), rect))
            return int(np.argmin(np.where(shown, np.abs(px - aim), np.inf)))
        start = int(np.argmin(np.where(shown, px, np.inf)))
        return self._walk(px, shown, start, px[start] + OFFSET_MARKER_INSET)

    def marker_at(self, marker):
        """Where a marker is drawn, as it would be STORED: `("i", sample)`
        in the pattern's own numbering; what it holds when not drawn."""
        trace = self._trace_of(marker.scan)
        k = self.marker_sample(marker, trace)
        return marker.at if k is None else ("i", trace.first + k)

    def _walked(self, marker, at, dx_px, rect):
        """A marker's place `at`, moved `dx_px` along its curve."""
        if not dx_px or not at or at[0] != "i":
            return at
        trace = self._trace_of(marker.scan)
        if trace is None or trace.x is None or not len(trace.x):
            return at
        px, _py, shown = self._shown_samples(trace, rect)
        k = int(at[1]) - trace.first
        if not (0 <= k < len(px) and shown[k]):
            return at
        return ("i", trace.first + self._walk(px, shown, k, px[k] + dx_px))

    def marker_x(self, marker):
        """The x a marker points at, in the axis's unit, or None."""
        trace = self._trace_of(marker.scan)
        k = self.marker_sample(marker, trace)
        return None if k is None else float(trace.x[k])

    def marker_text(self, marker):
        """The offset, written in the marker's (or the house) format, in
        the y axis's unit."""
        return numbers.write(float(marker.scan.offset),
                             self.style_of(marker, "number_format"),
                             numbers.OFFSET)

    def marker_dy(self, marker, rect=None):
        """How far below its curve a marker's text starts (above if < 0)."""
        if marker.dy is not None:
            return float(marker.dy)
        return OFFSET_MARKER_DROP * (rect or self.plot_rect()).height()

    def marker_colour(self, marker):
        if marker.selected:
            return QColor(_SELECT)
        if marker.colour in (None, "", "auto"):
            return QColor(_INK)
        return (paper_colour(marker.colour) if THEME == THEME_LIGHT
                else QColor(marker.colour))

    def _paint_offset_markers(self, p, rect, only=None):
        """The offset markers: each drawn scan's offset,
        `+0.5`, under its curve with a small arrow up to it, the left edge
        of the text on the arrow. One object per scan (`Scan.marker`),
        picked, moved and styled like any label."""
        doc = self.doc
        if doc is None or not doc.offset_markers:
            return
        for trace in self.drawable():
            if only is not None and trace is not only:
                continue
            marker = trace.scan.marker
            if not marker.visible:
                continue
            k = self.marker_sample(marker, trace, rect)
            if k is None:
                continue
            x = float(self.x_to_px(trace.x[k], rect))
            anchor = float(self.sy_to_px(trace.scan, trace.y[k], rect))
            font = QFont(p.font())
            font.setPointSizeF(max(4.0, float(self.style_of(marker, "size"))))
            p.setFont(font)
            metrics = QFontMetrics(font)
            colour = self.marker_colour(marker)
            text = self.marker_text(marker)
            dy = self.marker_dy(marker, rect)
            width = metrics.horizontalAdvance(text) + 6.0
            height = float(metrics.height())
            below = dy >= 0
            top = anchor + dy if below else anchor + dy - height
            box = QRectF(x - 3.0, top, width, height)
            start = box.top() if below else box.bottom()
            tip = 1.0 if below else -1.0         # the head points at the curve
            p.setPen(QPen(colour, 0.8))
            if abs(start - anchor) > 4.0:
                p.drawLine(QPointF(x, start), QPointF(x, anchor + 3.0 * tip))
            p.save()
            p.setBrush(colour)
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(x, anchor),
                                     QPointF(x - 2.0, anchor + 4.0 * tip),
                                     QPointF(x + 2.0, anchor + 4.0 * tip)]))
            p.restore()
            p.setBrush(Qt.NoBrush)
            p.drawText(box, Qt.AlignCenter, text)
            self._marker_boxes.append((marker, box.united(
                QRectF(x - 3.0, min(anchor, start), 6.0,
                       abs(start - anchor)))))

    def analysis_flush(self, analysis):
        """"left", "center" or "right" for this label, never "auto"."""
        return style.flush_for(analysis, self.style_of(analysis, "flush"))

    def analysis_colour(self, analysis, trace=None):
        """An analysis is its scan's colour unless it was given its own."""
        if analysis.colour in (None, "", "auto"):
            colour = (trace_colour(trace) if trace is not None
                      else QColor(analysis.scan.colour))
            # The shade 75 % of the curve's colour makes over the page, as
            # ONE solid colour (an arrow's head and shaft overlapped darker
            # where they were translucent).
            return mixed(colour, self.page_colour(), 190 / 255.0)
        return (paper_colour(analysis.colour) if THEME == THEME_LIGHT
                else QColor(analysis.colour))

    def _analysis_slice(self, trace, analysis):
        """`(a, b)`: the stretch of the drawn curve an analysis covers, as
        inclusive indices into `trace.x`, or None when it was not measured
        along the curve (typed positions)."""
        span = getattr(analysis, "span", None)
        if not span or trace.x is None or not len(trace.x):
            return None
        last = len(trace.x) - 1
        a = int(_clamp(span[0] - trace.first, 0, last))
        b = int(_clamp(span[1] - trace.first, 0, last))
        return (a, b) if b > a else None

    def _curve_y_at(self, trace, value, rect, within=None):
        """The screen y of the drawn curve nearest x = `value`, or None.

        "Nearest sample" rather than an interpolation: the sample the
        analysis was computed from is the one that matters.
        """
        index = self._sample_near(trace, value, within)
        if index is None:
            return None
        return self.sy_to_px(trace.scan, float(trace.y[index]), rect)

    @staticmethod
    def _sample_near(trace, value, within=None):
        """The index of the kept sample nearest x = `value` (inside the
        stretch `within`), or None. A drawn one: a sample with no value (the
        break between two sticks) has no y to put a point at."""
        if trace.x is None or not len(trace.x):
            return None
        lo, hi = within if within is not None else (0, len(trace.x) - 1)
        xs = trace.x[lo:hi + 1]
        if not len(xs):
            return None
        with np.errstate(invalid="ignore"):
            gaps = np.abs(xs - value)
            if trace.y is not None:
                gaps = np.where(np.isfinite(trace.y[lo:hi + 1]), gaps,
                                np.nan)
        if not np.isfinite(gaps).any():
            return None
        return lo + int(np.nanargmin(gaps))

    def _paint_names(self, p):
        """Which curve is which - for the one under the cursor. Nothing
        else: a selected curve is orange already, and its name sat over
        whatever was drawn at the right edge.

        These are a READOUT, not part of the figure: they appear when you
        point at a curve and go when you stop. A caption that belongs in the
        figure is a `TextLabel`, which is an object you place and keep.
        Painted per event rather than into the cache, because they follow the
        cursor.
        """
        rect = self.plot_rect()
        hovered = (self._trace_at(self._cursor)
                   if self._cursor is not None and self._move is None
                   else None)
        wanted = [t for t in self.traces if t is hovered]
        if not wanted:
            self._label_boxes = []
            return
        metrics = QFontMetrics(p.font())
        boxes = []
        for trace in wanted:
            y = None
            if trace.py is not None and len(trace.py):
                y = float(trace.py[np.argmax(trace.px)])
            elif trace.missing is not None:
                y = self.sy_to_px(trace.scan, trace.scan.offset, rect)
            if y is None or not (rect.top() - 20 <= y <= rect.bottom() + 20):
                continue
            text = trace.name
            width = metrics.horizontalAdvance(text)
            box = QRect(int(rect.right()) - width - 10, int(y) - 9,
                        width + 8, 17)
            box = _free_slot(box, [b for _t, b in boxes], rect)
            background = QColor(_BG)
            background.setAlpha(190)
            p.fillRect(box, background)
            p.setPen(QPen(_SELECT if trace.scan.selected
                          else trace_colour(trace), 1))
            p.drawText(box, int(Qt.AlignLeft | Qt.AlignVCenter), text)
            boxes.append((trace, box))
        self._label_boxes = boxes

    def follow_px(self, artist, rect=None):
        """How far a label is drawn below its stored place because its scan
        moved (negative: up) - `TextLabel.follow` in pixels. Zero for
        anything that is not a label with a parent."""
        follow = artist.follow() if hasattr(artist, "follow") else 0.0
        if not follow:
            return 0.0
        rect = rect or self.plot_rect()
        return float(self.ay_to_px(artist, follow, rect)
                     - self.ay_to_px(artist, 0.0, rect))

    def artist_point(self, artist, rect=None):
        """Where an artist sits, in pixels, whichever space it is stored in
        - and, for a label with a parent, as far up as its scan has moved."""
        rect = rect or self.plot_rect()
        if getattr(artist, "vline", None) is not None:
            # A marker line's text stands ON its line, at its own height.
            return (float(self.x_to_px(artist.vline, rect)),
                    self.rel_to_px(0.0, float(artist.y), rect)[1])
        if isinstance(artist, model.Region):
            return (self._middle_px(artist.lo, artist.hi, rect),
                    self.rel_to_px(0.0, float(artist.y), rect)[1])
        if isinstance(artist, model.SpanArrow):
            a, b = artist.end_values()
            return (self._middle_px(a, b, rect),
                    self.rel_to_px(0.0, float(artist.y), rect)[1])
        if getattr(artist, "attached", False):
            point = self.attach_point(artist, rect)
            if point is not None:
                dx = 0.0 if artist.leader else float(artist.dx or 0.0)
                return (point.x() + dx, point.y() + self.label_dy(artist))
        if getattr(artist, "space", "relative") == model.SPACE_DATA:
            return (float(self.x_to_px(artist.x, rect)),
                    float(self.ay_to_px(artist, artist.y, rect))
                    + self.follow_px(artist, rect))
        px, py = self.rel_to_px(float(artist.x), float(artist.y), rect)
        return (px, py + self.follow_px(artist, rect))

    def set_artist_point(self, artist, px, py, rect=None, clamp=True):
        """Put an artist at a pixel position, in its own space.

        A drag keeps a relative artist's anchor inside the plot; a scale or
        rotation (`clamp=False`) must not, or the point it is about moves
        whenever the anchor would pass the edge."""
        rect = rect or self.plot_rect()
        if (getattr(artist, "attached", False)
                and self._place_attached(artist, px, py, rect)):
            return artist
        if isinstance(artist, (model.Region, model.SpanArrow)):
            # Sideways moves the stretch (the arrow's free ends) by a
            # distance on x, up and down the text.
            now = self.artist_point(artist, rect)[0]
            delta = (float(self.px_to_x(px, rect))
                     - float(self.px_to_x(now, rect)))
            if isinstance(artist, model.Region):
                artist.lo += delta
                artist.hi += delta
            else:
                if artist.ends[0] is None:
                    artist.x0 += delta
                if artist.ends[1] is None:
                    artist.x1 += delta
            fy = self.px_to_rel(px, py, rect)[1]
            artist.y = float(_clamp(fy, 0.01, 0.99) if clamp else fy)
            return artist
        if getattr(artist, "vline", None) is not None:
            # Sideways moves the LINE (a position), up and down slides
            # the text along it.
            artist.vline = float(self.px_to_x(px, rect))
            fx, fy = self.px_to_rel(px, py, rect)
            if clamp:
                fy = _clamp(fy, 0.01, 0.99)
            artist.set_position(fx, fy)
            return artist
        py = py - self.follow_px(artist, rect)
        if getattr(artist, "space", "relative") == model.SPACE_DATA:
            artist.set_position(self.px_to_x(px, rect),
                                self.px_to_ay(artist, py, rect))
        else:
            fx, fy = self.px_to_rel(px, py, rect)
            if clamp:
                fx, fy = _clamp(fx, 0.01, 0.99), _clamp(fy, 0.01, 0.99)
            artist.set_position(fx, fy)
        return artist

    def placed_under(self, label, scan, rect=None):
        """`(x, y, parent_offset, leader)` that keep `label` - and a note's
        arrow tip - exactly where it is drawn once it belongs to `scan`
        (None: free). Changes nothing."""
        rect = rect or self.plot_rect()
        px, py = self.artist_point(label, rect)
        tip = self.leader_tip(label, rect)
        saved = (label.scan, label.parent_offset, label.x, label.y,
                 label.leader, label.at)
        try:
            label.scan = scan
            label.at = None                 # placed by x and y here
            label.parent_offset = (None if scan is None
                                   else float(scan.offset))
            self.set_artist_point(label, px, py, rect, clamp=False)
            leader = (self.leader_value(label, tip, rect) if tip is not None
                      else label.leader)
            return label.x, label.y, label.parent_offset, leader
        finally:
            (label.scan, label.parent_offset, label.x, label.y,
             label.leader, label.at) = saved

    def rebase(self, label):
        """Store an owned label's place as drawn NOW, with its scan's offset
        now, so a settings window shows where it is. Nothing moves."""
        if (getattr(label, "scan", None) is None or not label.follow()
                or getattr(label, "attached", False)):
            return label
        (label.x, label.y, label.parent_offset,
         label.leader) = self.placed_under(label, label.scan)
        return label

    def convert_artist_space(self, artist, space, rect=None):
        """Change which space an artist is stored in, keeping it in place."""
        rect = rect or self.plot_rect()
        px, py = self.artist_point(artist, rect)
        artist.space = space
        self.set_artist_point(artist, px, py, rect)
        return artist

    def legend_rect(self, rect=None, painter=None):
        """Where the legend sits and how big it is, or None when it is off."""
        doc = self.doc
        if doc is None or not doc.legend.visible:
            return None
        legend = doc.legend
        entries = legend.entries(doc)
        if not entries:
            return None
        rect = rect or self.plot_rect()
        font = QFont(painter.font() if painter is not None else self.font())
        font.setPointSizeF(self.style_of(legend, "size"))
        metrics = QFontMetrics(font)
        width = 0
        for _scan, text in entries:
            width = max(width, markup_size(text, font)[0])
        width += legend.sample + 18
        height = metrics.height() * legend.spacing * len(entries) + 10
        px, py = self.artist_point(legend, rect)
        fx, fy = legend.anchor_offsets()
        return QRectF(px - fx * width, py - fy * height, width, height)

    def _paint_legend(self, p, rect):
        """The key: a colour sample and a name per drawn scan."""
        doc = self.doc
        if doc is None or not doc.legend.visible:
            return
        legend = doc.legend
        box = self.legend_rect(rect, p)
        if box is None:
            return
        with self._rotated(p, legend, rect):
            p.save()
            self._paint_legend_box(p, legend, box)
            p.restore()

    def _paint_legend_box(self, p, legend, box):
        doc = self.doc
        font = QFont(p.font())
        font.setPointSizeF(self.style_of(legend, "size"))
        p.setFont(font)
        metrics = QFontMetrics(font)
        if legend.show_frame:
            backing = self.page_colour()
            backing.setAlpha(210)
            p.setBrush(backing)
            edge = QColor(_SELECT) if legend.selected else QColor(_GRID)
            p.setPen(QPen(edge, 1.0))
            p.drawRoundedRect(box, 3, 3)
            p.setBrush(Qt.NoBrush)
        elif legend.selected:
            p.setPen(QPen(_SELECT, 1.0, Qt.DashLine))
            p.drawRect(box)
        step = metrics.height() * legend.spacing
        y = box.top() + 5 + metrics.height() / 2.0
        for scan, text in legend.entries(doc):
            colour = (paper_colour(scan.colour) if THEME == THEME_LIGHT
                      else QColor(scan.colour))
            width = (float(legend.line_width) if legend.line_width
                     else max(1.2, self.style_of(scan, "line_width")
                              * CURVE_WIDTH))
            p.setPen(QPen(colour, width))
            p.drawLine(QPointF(box.left() + 8, y),
                       QPointF(box.left() + 8 + legend.sample, y))
            ink = (QColor(_SELECT) if legend.selected
                   else (QColor(_TEXT) if legend.colour in (None, "", "auto")
                         else (paper_colour(legend.colour)
                               if THEME == THEME_LIGHT
                               else QColor(legend.colour))))
            text_box = QRectF(box.left() + legend.sample + 14,
                              y - metrics.height() / 2.0,
                              box.width() - legend.sample - 20,
                              metrics.height())
            draw_markup(p, QRectF(text_box.left() + markup_size(text, font)[0]
                                  / 2.0, text_box.top(), 0, text_box.height()),
                        text, font, ink)
            y += step

    # --------------------------------------------------- per-event overlays
    #: The reticle's circle radius, and how far its ticks reach past it.
    RETICLE_RADIUS = 13.0
    RETICLE_TICK = 6.0

    def _paint_cursor(self, p):
        """A RETICLE where the cursor is, not a line down the plot.

        The dashed vertical was borrowed from the PXRD window, where the x
        position is the whole question. Here it mostly gets in the way of
        reading a stack, so the plain cursor is a ring with four ticks and a
        small cross - a sight, which is what a pointer on a figure is.
        """
        if (self._cursor is None or self._measure is not None
                or self._nav is not None):
            return
        rect = self.plot_rect()
        x, y = float(self._cursor.x()), float(self._cursor.y())
        if not (rect.left() <= x <= rect.right()
                and rect.top() <= y <= rect.bottom()):
            return
        # The ring IS the pick distance: what it encloses is what a press there
        # acts on. Its ticks, its cross and its line shrink with it below the
        # built-in size, or a small ring would be all tick and no ring.
        k = self.page()[2]
        radius = self.pick_radius()
        shrink = min(1.0, float(style.preference("pick_radius"))
                     / self.RETICLE_RADIUS)
        tick = self.RETICLE_TICK * shrink / k
        inset = 3.0 * shrink / k
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(_CURSOR, max(0.6, 1.2 * shrink) / k))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(x, y), radius, radius)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            p.drawLine(QPointF(x + dx * (radius - inset),
                               y + dy * (radius - inset)),
                       QPointF(x + dx * (radius + tick),
                               y + dy * (radius + tick)))
        p.drawLine(QPointF(x - inset, y), QPointF(x + inset, y))
        p.drawLine(QPointF(x, y - inset), QPointF(x, y + inset))
        p.setRenderHint(QPainter.Antialiasing, False)

    def _paint_select_box(self, p):
        """The selection rectangle: dashed, in the selection colour, so it
        cannot be mistaken for the zoom band."""
        box = self._box
        # Only once it IS a box: every press starts one now, and a click
        # that never became a drag should not leave a dot behind.
        if box is None or not box.get("moved"):
            return
        rect = QRectF(box["start"], box["now"]).normalized()
        fill = QColor(_SELECT)
        fill.setAlpha(40)
        p.fillRect(rect, fill)
        p.setPen(QPen(_SELECT, 1, Qt.DashLine))
        p.drawRect(rect)

    def _paint_band(self, p):
        drag = self._drag
        if drag is None or not drag["mode"].startswith("zoom") \
                or self._cursor is None:
            return
        rect = self.plot_rect()
        x0, y0 = drag["px"], drag["py"]
        x1 = _clamp(self._cursor.x(), rect.left(), rect.right())
        y1 = _clamp(self._cursor.y(), rect.top(), rect.bottom())
        if drag["mode"] == "zoom_h":
            band = QRectF(min(x0, x1), rect.top(), abs(x1 - x0),
                          rect.height())
        elif drag["mode"] == "zoom_v":
            band = QRectF(rect.left(), min(y0, y1), rect.width(),
                          abs(y1 - y0))
        else:
            band = QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0),
                          abs(y1 - y0))
        p.fillRect(band, _BAND)
        p.setPen(QPen(_BAND_EDGE, 1, Qt.DashLine))
        p.drawRect(band)

    def _paint_offsets(self, p, force=False):
        """The offset arrow: how far a scan has been moved, as a number.

        This is what replaces a PXRD window's stack slots: if a scan can sit
        anywhere, the figure has to say where it is sitting. Drawn for the
        scans being moved and for the selected ones, rather than for all of
        them, because eight of these at once is a mess.
        """
        doc = self.doc
        if doc is None:
            return
        rect = self.plot_rect()
        scans = [t.scan for t in self.traces
                 if t.scan.selected or (self._move is not None
                                        and t.scan in self._move["objs"])]
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        p.setFont(font)
        for scan in scans:
            if not scan.offset:
                continue
            zero = self.sy_to_px(scan, 0.0, rect)
            here = self.sy_to_px(scan, scan.offset, rect)
            if abs(zero - here) < 4:
                continue
            x = rect.left() + 26
            colour = QColor(scan.colour)
            # Quiet and symmetrical: the same short cap at both ends, no
            # dashed line across to the axis.
            p.setPen(QPen(colour, 1.0))
            p.drawLine(QPointF(x, zero), QPointF(x, here))
            # A dimension line: a short horizontal cap at each end, not an
            # arrowhead - the two ends are levels.
            for end in (zero, here):
                p.drawLine(QPointF(x - 5, end), QPointF(x + 5, end))
            p.drawText(QPointF(x + 8, (zero + here) / 2.0 + 4),
                       "{} {}".format(numbers.write(scan.offset, "%+.2g"),
                                      doc.unit_for(scan)))

    def _paint_alarms(self, p):
        """The blinking red label on a pattern that cannot be drawn.

        Painted per event rather than into the cache, because it blinks, and
        blinking a cached pixmap would mean rebuilding the curves twice a
        second. Not drawn at all while a gesture is live: an alarm that
        flashes under the hand during a drag is an annoyance.
        """
        if not self.blink_lit():
            return
        rect = self.plot_rect()
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(7.0, font.pointSizeF() - 0.5))
        p.setFont(font)
        metrics = QFontMetrics(font)
        for trace in self.traces:
            if not trace.missing:
                continue
            y = self.sy_to_px(trace.scan, trace.scan.offset, rect)
            if not (rect.top() - 10 <= y <= rect.bottom() + 10):
                continue
            text = "NO {}".format(trace.missing.upper())
            width = metrics.horizontalAdvance(text) + 12
            box = QRect(int(rect.left()) + 8, int(y) - 9, width, 18)
            p.setBrush(_ALARM)
            p.setPen(QPen(_ALARM.lighter(140), 1))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QColor(255, 255, 255))
            p.drawText(box, Qt.AlignCenter, text)
            p.setBrush(Qt.NoBrush)

    # ----------------------------------------------------------------- paper
    def darken_for_paper(self):
        """Trace colours as they would print, parallel to `traces`."""
        return [paper_colour(t.colour) for t in self.traces]


# ------------------------------------------------------------------ helpers
def _free_slot(box, taken, bounds, step=15, tries=6):
    """`box`, moved up in steps until it clears `taken`, or left where it was.

    Deliberately simple: a handful of nudges, always upward, and it gives up
    rather than searching. A label that has moved a long way from the thing it
    labels is worse than two labels that touch.
    """
    for _ in range(tries):
        if all(not box.intersects(other) for other in taken):
            break
        box = box.translated(0, -step)
    if box.top() < bounds.top():
        box = box.translated(0, bounds.top() - box.top())
    return box


def trace_colour(trace):
    """A trace's colour as the current theme draws it.

    The default palette is chosen for a dark ground, so on white it is
    brought down to `PAPER_LUMA`; a colour somebody chose is drawn as chosen
    (`paper_colour`). Resolved HERE, at paint time, rather than
    stored on the Trace: the theme can change between two paints and the
    colour the user picked must not be overwritten by a rendering choice.
    """
    return paper_colour(trace.colour) if THEME == THEME_LIGHT else QColor(trace.colour)


def _m4(px, py, col):
    """The samples that draw a curve exactly at one point per column: in
    every run of samples sharing a column, the first, the highest, the
    lowest and the last, EACH AT ITS OWN x AND IN THE ORDER MEASURED (M4).

    The old reduction drew each column as a vertical bar at one x - top
    first, then bottom - so a steep flank was a staircase, and a RISING one
    doubled back on itself at every column: a "choppy" curve, which no
    amount of antialiasing could smooth."""
    n = len(px)
    starts = np.flatnonzero(np.concatenate(([True], col[1:] != col[:-1])))
    ends = np.append(starts[1:], n) - 1
    group = np.repeat(np.arange(len(starts)), np.diff(np.append(starts, n)))
    keep = np.zeros(n, dtype=bool)
    keep[starts] = True
    keep[ends] = True
    # The first index of each group's extreme: sort by (group, value) and
    # take each group's first (least) and last (greatest) entry.
    order = np.lexsort((py, group))
    grouped = group[order]
    first = np.flatnonzero(np.concatenate(([True],
                                           grouped[1:] != grouped[:-1])))
    last = np.append(first[1:], n) - 1
    keep[order[first]] = True
    keep[order[last]] = True
    return px[keep], py[keep]


def _finite_span(values):
    """`(low, high)` of the measured samples of `values`, or None."""
    if values is None or not len(values):
        return None
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    return float(values.min()), float(values.max())


def _finite_runs(x, y):
    """`[(x, y), ...]`: the stretches of a curve with no flagged (NaN)
    sample in either, two samples or more each - what can be drawn as a
    polyline."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    good = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    if not len(good):
        return []
    runs = np.split(good, np.flatnonzero(np.diff(good) > 1) + 1)
    return [(x[run], y[run]) for run in runs if len(run) > 1]



class _DraftPainter(QPainter):
    """The painter of a draft, while a hand is at work
    (`PlotWidget.drafting`): every CURVE is a hairline, one device pixel,
    smooth. Measured on a 150 % screen, a 6000-point curve took 159 ms as
    a 1.5 px antialiased line, 46 ms as the same line without
    antialiasing - and 4 ms as an antialiased hairline: the width is what
    costs, not the smoothing. Everything else is drawn as always; the
    curves get their width back in the one smooth redraw when the hand
    stops."""

    def drawPolyline(self, *args):
        pen = self.pen()
        if pen.style() == Qt.NoPen or pen.widthF() == 0.0:
            QPainter.drawPolyline(self, *args)
            return
        thin = QPen(pen)
        thin.setWidthF(0.0)
        QPainter.setPen(self, thin)
        QPainter.drawPolyline(self, *args)
        QPainter.setPen(self, pen)

def _polyline_slow(px, py):
    poly = QPolygonF()
    for a, b in zip(np.asarray(px).tolist(), np.asarray(py).tolist()):
        poly.append(QPointF(a, b))
    return poly


def _polyline_fast(px, py):
    """The points written straight into the polygon's memory from numpy,
    as pyqtgraph does: built one `QPointF` at a time in Python, a redraw
    of ten patterns spent most of its time here (150 000 calls)."""
    xs = np.asarray(px, dtype=float)
    ys = np.asarray(py, dtype=float)
    count = len(xs)
    poly = QPolygonF()
    if not count:
        return poly
    poly.resize(count)
    memory = np.frombuffer(shiboken6.VoidPtr(poly.data(), count * 16, True),
                           dtype=np.float64).reshape(count, 2)
    memory[:, 0] = xs
    memory[:, 1] = ys
    return poly


def _fast_polylines_work():
    """True when this PySide6 hands out the polygon's memory as expected:
    checked once, on three points, before a curve is ever drawn that way."""
    try:
        poly = _polyline_fast([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
        return [(p.x(), p.y()) for p in poly] == [(1.0, 4.0), (2.0, 5.0),
                                                  (3.0, 6.0)]
    except Exception:          # noqa: BLE001 - any failure: the slow way
        return False


_polyline = _polyline_fast if _fast_polylines_work() else _polyline_slow



#: What a label may contain, spelled the way somebody used to LaTeX would
#: write it: Greek letters, the angstrom, and a few symbols.
MARKUP_SYMBOLS = {
    "\\Delta": "\u0394", "\\delta": "\u03b4", "\\alpha": "\u03b1",
    "\\beta": "\u03b2", "\\gamma": "\u03b3", "\\Gamma": "\u0393",
    "\\epsilon": "\u03b5", "\\varepsilon": "\u03b5", "\\zeta": "\u03b6",
    "\\eta": "\u03b7", "\\theta": "\u03b8", "\\Theta": "\u0398",
    "\\kappa": "\u03ba", "\\lambda": "\u03bb", "\\Lambda": "\u039b",
    "\\mu": "\u03bc", "\\nu": "\u03bd", "\\xi": "\u03be", "\\pi": "\u03c0",
    "\\Pi": "\u03a0", "\\rho": "\u03c1", "\\sigma": "\u03c3",
    "\\Sigma": "\u03a3", "\\tau": "\u03c4", "\\phi": "\u03c6",
    "\\varphi": "\u03c6", "\\Phi": "\u03a6", "\\chi": "\u03c7",
    "\\psi": "\u03c8", "\\Psi": "\u03a8", "\\omega": "\u03c9",
    "\\Omega": "\u03a9", "\\degree": "\u00b0", "\\circ": "\u00b0",
    "\\pm": "\u00b1", "\\mp": "\u2213", "\\times": "\u00d7",
    "\\cdot": "\u00b7", "\\infty": "\u221e", "\\approx": "\u2248",
    "\\sim": "\u223c", "\\leq": "\u2264", "\\le": "\u2264",
    "\\geq": "\u2265", "\\ge": "\u2265", "\\neq": "\u2260",
    "\\rightarrow": "\u2192", "\\to": "\u2192", "\\leftarrow": "\u2190",
    "\\uparrow": "\u2191", "\\downarrow": "\u2193", "\\AA": "\u00c5",
    "\\%": "%", "\\$": "$", "\\{": "{", "\\}": "}", "\\_": "_", "\\#": "#",
    "\\&": "&",
}

#: LaTeX's spaces: math mode ignores typed spaces, so these are how a
#: caption like `$2\theta \quad / \quad ^\circ$` is
#: spaced.
MATH_SPACES = {"\\qquad": "\u2003\u2003", "\\quad": "\u2003",
               "\\,": "\u2009", "\\:": "\u2005", "\\;": "\u2005",
               "\\ ": " ", "\\!": ""}

_COMMAND = re.compile(r"\\([A-Za-z]+|.)")
#: Accents over a letter, as combining marks: `\tilde{\nu}` is the
#: position's tilde nu.
MARK_ACCENTS = {"tilde": "\u0303", "hat": "\u0302", "bar": "\u0304",
           "dot": "\u0307", "vec": "\u20d7"}
_ACCENT = re.compile(r"\\(tilde|hat|bar|dot|vec)(?![A-Za-z])\s*")
#: Script levels a run can be on.
SUB, SUP = "sub", "sup"


def _symbol(text, index):
    """`(glyph, next index)` for the backslash command at `text[index]`, or
    None when it is not one this knows."""
    for name, glyph in MATH_SPACES.items():
        if text.startswith(name, index):
            return glyph, index + len(name)
    match = _COMMAND.match(text, index)
    if match and match.group(0) in MARKUP_SYMBOLS:
        return MARKUP_SYMBOLS[match.group(0)], match.end()
    return None


def _group(text, index):
    """The inside of the `{...}` at `text[index]` (braces nest), and the
    index after it."""
    depth = 0
    for end in range(index, len(text)):
        if text[end] == "{" and (end == 0 or text[end - 1] != "\\"):
            depth += 1
        elif text[end] == "}" and text[end - 1] != "\\":
            depth -= 1
            if depth == 0:
                return text[index + 1:end], end + 1
    return text[index + 1:], len(text)


def _script_arg(text, index):
    """What a `_` or `^` applies to: a `{group}`, one command, or one
    character."""
    if index >= len(text):
        return "", index
    if text[index] == "{":
        return _group(text, index)
    if text[index] == "\\":
        match = _COMMAND.match(text, index)
        return match.group(0), match.end()
    return text[index], index + 1


def _math(text, runs, script=False, upright=False, spaces=False):
    r"""LaTeX math mode: letters italic, digits and signs upright, spaces
    ignored, `_` and `^` scripts, `\mathrm{}` / `\text{}` upright, the
    Greek letters and symbols of `MARKUP_SYMBOLS`."""
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace() and not spaces:
            index += 1
            continue
        if char in "_^":
            argument, index = _script_arg(text, index + 1)
            _math(argument, runs, SUB if char == "_" else SUP, upright,
                  spaces)
            continue
        if char == "{":
            inner, index = _group(text, index)
            _math(inner, runs, script, upright, spaces)
            continue
        if char == "}":
            index += 1
            continue
        if char == "\\":
            accent = _ACCENT.match(text, index)
            if accent:
                argument, index = _script_arg(text, accent.end())
                start = len(runs)
                _math(argument, runs, script, upright, spaces)
                if len(runs) > start:
                    part, italic, level = runs[start]
                    runs[start] = (part[:1] + MARK_ACCENTS[accent.group(1)]
                                   + part[1:], italic, level)
                continue
            match = re.match(r"\\(mathrm|mathit|mathbf|mathsf|text|textrm|"
                             r"textit|rm)\s*", text[index:])
            if match and text[index + match.end():index + match.end() + 1] \
                    == "{":
                inner, index = _group(text, index + match.end())
                kind = match.group(1)
                _math(inner, runs, script,
                      upright=kind not in ("mathit", "textit"),
                      spaces=kind.startswith("text"))
                continue
            found = _symbol(text, index)
            if found is not None:
                runs.append((found[0], False, script))
                index = found[1]
                continue
        runs.append((char, char.isalpha() and not upright, script))
        index += 1


def _unescaped(text, char, start):
    """The index of the next `char` in `text` not preceded by a backslash."""
    index = text.find(char, start)
    while index > 0 and text[index - 1] == "\\":
        index = text.find(char, index + 1)
    return index


def markup_runs(text):
    r"""`[(text, italic, script), ...]` for a label; `script` is False,
    `SUB` or `SUP`.

    The figure's own markup, which a pattern figure actually needs:

    * `*A*` sets a quantity symbol cursive;
    * `_{a}` lowers a subscript and `^{-1}` raises a superscript;
    * `\tilde{\nu}` puts a tilde over a letter (the position);
    * a backslash name writes a Greek letter or a symbol (`\Delta`).

    And LaTeX between dollars, as matplotlib's mathtext takes it, so a label
    written for matplotlib reads the same here: `$\alpha$-quartz`,
    `(A)$_{1.00}$`, `$2\theta \quad / \quad ^\circ$`. Rich
    text would drag a QTextDocument
    into a painted plot for this; a tokeniser and three flags do it.
    """
    text = str(text)
    runs, italic, index, plain = [], False, 0, []

    def flush():
        if plain:
            runs.append(("".join(plain), italic, False))
            del plain[:]

    while index < len(text):
        char = text[index]
        if char == "$":
            close = _unescaped(text, "$", index + 1)
            if close > index:
                flush()
                _math(text[index + 1:close], runs)
                index = close + 1
                continue
        if char == "\\":
            accent = _ACCENT.match(text, index)
            if accent:
                argument, index = _script_arg(text, accent.end())
                for name, glyph in sorted(MARKUP_SYMBOLS.items(),
                                          key=lambda item: -len(item[0])):
                    argument = argument.replace(name, glyph)
                plain.append(argument[:1] + MARK_ACCENTS[accent.group(1)]
                             + argument[1:])
                continue
            found = _symbol(text, index)
            if found is not None:
                plain.append(found[0])
                index = found[1]
                continue
        if char == "*":
            flush()
            italic = not italic
            index += 1
            continue
        if char in "_^" and index + 1 < len(text):
            flush()
            argument, index = _script_arg(text, index + 1)
            for name, glyph in sorted(MARKUP_SYMBOLS.items(),
                                      key=lambda item: -len(item[0])):
                argument = argument.replace(name, glyph)
            runs.append((argument, italic, SUB if char == "_" else SUP))
            continue
        plain.append(char)
        index += 1
    flush()
    merged = []
    for run in runs:
        if not run[0]:
            continue
        if merged and merged[-1][1:] == run[1:]:
            merged[-1] = (merged[-1][0] + run[0],) + run[1:]
        else:
            merged.append(run)
    return merged


def _run_font(font, italic, script):
    styled = QFont(font)
    styled.setItalic(italic)
    if script:
        styled.setPointSizeF(max(4.0, font.pointSizeF() * 0.72))
    return styled


def markup_size(text, font):
    """`(width, height)` the drawn text will occupy."""
    width = 0
    height = QFontMetrics(font).height()
    for part, italic, subscript in markup_runs(text):
        width += QFontMetrics(_run_font(font, italic, subscript)) \
            .horizontalAdvance(part)
    return width, height


def draw_markup(p, box, text, font, colour):
    """Draw text centred in `box`, honouring the markup."""
    width, _height = markup_size(text, font)
    x = box.center().x() - width / 2.0
    metrics = QFontMetrics(font)
    baseline = box.center().y() + metrics.ascent() / 2.0 - 1
    shift = {SUB: metrics.height() * 0.18, SUP: -metrics.ascent() * 0.38}
    p.setPen(colour)
    for part, italic, script in markup_runs(text):
        styled = _run_font(font, italic, script)
        p.setFont(styled)
        p.drawText(QPointF(x, baseline + shift.get(script, 0.0)), part)
        x += QFontMetrics(styled).horizontalAdvance(part)
    p.setFont(font)


def _nice_step(span, target=8):
    raw = span / float(target)
    power = 10.0 ** math.floor(math.log10(max(raw, 1e-12)))
    for mult in (1, 2, 5, 10):
        if raw <= mult * power:
            return mult * power
    return 10.0 * power


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _chord(xs, ys):
    """The straight line from the first point to the last, at every x.

    Not `np.interp`, which needs x to INCREASE and silently returns nonsense
    for a stretch that runs the other way.
    """
    span = float(xs[-1] - xs[0])
    if abs(span) < 1e-12:
        return np.full(len(ys), float(ys[0]))
    return ys[0] + (ys[-1] - ys[0]) * (xs - xs[0]) / span


def decorators_of(doc):
    """Everything placed on the page rather than measured: the legend,
    labels, pictures, structures, distance arrows, and the regions that
    write something."""
    out = [doc.legend] + list(doc.labels)
    out += list(getattr(doc, "images", ()))
    out += list(getattr(doc, "structures", ()))
    out += list(getattr(doc, "spans", ()))
    out += [r for r in getattr(doc, "regions", ()) if r.shown_text()]
    return out


def rounded_ends(lo, hi, snap):
    """`(lo, hi)` put on a whole grid: the power of ten below a tenth of
    the span (1 for 5 to 50 degrees). An end within `snap` of the span of
    a grid line goes onto it; one further off goes OUT to the next."""
    span = float(hi - lo)
    if not span > 0:
        return lo, hi
    grid = 10.0 ** math.floor(math.log10(span / 10.0))
    tolerance = float(snap) * span
    out = []
    for value, outward in ((lo, math.floor), (hi, math.ceil)):
        nearest = round(value / grid) * grid
        if abs(nearest - value) <= tolerance:
            out.append(float(nearest))
        else:
            out.append(float(outward(value / grid) * grid))
    if out[1] <= out[0]:
        return lo, hi
    return out[0], out[1]


def _same_context(a, b):
    """True when two framings' contexts are the same axes (tuples and lists
    alike, as a session reads them back)."""
    def flat(value):
        if isinstance(value, (list, tuple)):
            return tuple(flat(v) for v in value)
        return value
    return flat(a) == flat(b)


def _rect_distance(box, point):
    """Pixels from `point` to the nearest edge of `box`; 0 inside it."""
    dx = max(box.left() - point.x(), 0.0, point.x() - box.right())
    dy = max(box.top() - point.y(), 0.0, point.y() - box.bottom())
    return math.hypot(dx, dy)


def _close(a, b, tol=1e-9):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def clip_svg(path, rects):
    """Wrap each fenced stretch of an SVG in a clipPath (see
    `PlotWidget._clip_mark`). The writer puts every change of pen or brush
    in a sibling `<g>`, so the stretch between the two marks is a run of
    whole groups, and a `<g clip-path>` around it is balanced."""
    if not rects:
        return False
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    defs = []
    for number, box in enumerate(rects):
        name = "pxrdpanel-axes-{}".format(number)
        opening = text.find('fill="{}"'.format(PlotWidget.CLIP_OPEN))
        closing = text.find('fill="{}"'.format(PlotWidget.CLIP_CLOSE),
                            opening + 1)
        if opening < 0 or closing < 0:
            break
        open_group = text.rfind("<g", 0, opening)
        open_end = text.find("</g>", opening)
        close_group = text.rfind("<g", 0, closing)
        close_end = text.find("</g>", closing)
        if min(open_group, open_end, close_group, close_end) < 0:
            break
        text = (text[:open_group]
                + '<g clip-path="url(#{})">'.format(name)
                + text[open_end + len("</g>"):close_group]
                + "</g>"
                + text[close_end + len("</g>"):])
        defs.append('<clipPath id="{}"><rect x="{:.4f}" y="{:.4f}" '
                    'width="{:.4f}" height="{:.4f}"/></clipPath>'.format(
                        name, box.left(), box.top(), box.width(),
                        box.height()))
    if not defs:
        return False
    head = text.find(">", text.find("<svg")) + 1
    text = text[:head] + "\n<defs>" + "".join(defs) + "</defs>" + text[head:]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def _clip_segment(a, b, rect):
    """The part of the segment a-b inside `rect`, or None (Liang-Barsky)."""
    x0, y0, x1, y1 = a.x(), a.y(), b.x(), b.y()
    dx, dy = x1 - x0, y1 - y0
    low, high = 0.0, 1.0
    for p_, q_ in ((-dx, x0 - rect.left()), (dx, rect.right() - x0),
                   (-dy, y0 - rect.top()), (dy, rect.bottom() - y0)):
        if p_ == 0:
            if q_ < 0:
                return None
            continue
        t = q_ / p_
        if p_ < 0:
            low = max(low, t)
        else:
            high = min(high, t)
        if low > high:
            return None
    return (QPointF(x0 + low * dx, y0 + low * dy),
            QPointF(x0 + high * dx, y0 + high * dy))


def _flush_of(artist):
    """How an artist's lines line up: by the side of its anchor."""
    chosen = getattr(artist, "flush", None)
    if chosen in ("left", "right", "center"):
        return chosen
    anchor = str(getattr(artist, "anchor", "center"))
    return ("left" if "left" in anchor else
            "right" if "right" in anchor else "center")


def draw_lines(p, box, text, font, colour, flush="center"):
    """Text over several lines (a newline breaks one), each in the markup,
    lined up left, centre or right in `box`."""
    height = QFontMetrics(font).height()
    for row, line in enumerate(str(text).split("\n")):
        width = markup_size(line, font)[0]
        if flush == "left":
            left = box.left() + 3.0
        elif flush == "right":
            left = box.right() - 3.0 - width
        else:
            left = box.center().x() - width / 2.0
        draw_markup(p, QRectF(left, box.top() + 1.0 + row * height, width,
                              height), line, font, colour)


#: "Colour by element" for structure labels: the usual hues, dark enough to
#: read on white paper; lightened on the dark theme. Anything not listed
#: (carbon, hydrogen) keeps the structure's ink.
ELEMENT_COLOURS = {"N": "#2848d8", "O": "#d82020", "S": "#b89400",
                   "P": "#e07000", "F": "#2f9e2f", "Cl": "#1f9a1f",
                   "Br": "#a52a2a", "I": "#8a1e9e", "B": "#c06050",
                   "Si": "#8c7a50", "Se": "#b07800", "Li": "#8a3fd0",
                   "Na": "#8a3fd0", "K": "#8a3fd0", "Mg": "#2e8b2e",
                   "Ca": "#2e8b2e", "Fe": "#c05020", "Cu": "#b06a30",
                   "Zn": "#6070a0"}


def element_colour(symbol, ink):
    """An element label's colour when structures colour by element."""
    name = ELEMENT_COLOURS.get(str(symbol))
    if name is None:
        return QColor(ink)
    colour = QColor(name)
    return colour if THEME == THEME_LIGHT else colour.lighter(150)

# Qt calls the handlers here by itself; an error in one is logged and
# survived rather than the end of the program (`core/log.py`).
from ..core import log as _log
_log.guard_classes(globals(), __name__)
