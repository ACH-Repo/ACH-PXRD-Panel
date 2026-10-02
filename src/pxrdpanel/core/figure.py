"""The figure's size: free with the window, a fixed aspect ratio, or EXACT.

The case in point: a stack of first up-scans in one session file,
second up-scans in another, both dropped into Word side by side - and they
have to be the same size, with their axes boxes the same size and in the
same place, without fiddling. That rules out any layout that sizes itself:

* the figure has a PHYSICAL size, width x height in cm or inches;
* its MARGINS are physical too, and they fix the axes box exactly - the
  numbers and the captions live inside them. A margin that sized itself to
  the widest tick label would make "10.25" and "0.5" two different boxes.

The drawing is laid out in a space of `DESIGN_DPI` units per inch - the
logical DPI Qt uses for fonts on screen and in a QImage - so a 12 pt font is
12/72 inch in the figure, on screen (scaled to fit the pane) and in every
export. Lengths here are kept in the chosen unit and converted when the unit
changes, so switching cm <-> in never moves anything.

UI-free: plain attributes, so the undo stack and the session file treat it
like any other object.
"""

MODE_WINDOW = "window"      # the figure is whatever the pane is
MODE_ASPECT = "aspect"      # the pane shows it at a fixed width : height
MODE_SIZE = "size"          # exact physical size and margins
MODES = (MODE_WINDOW, MODE_ASPECT, MODE_SIZE)
MODE_TITLES = {MODE_WINDOW: "follow the window",
               MODE_ASPECT: "a fixed aspect ratio",
               MODE_SIZE: "an exact size"}

UNIT_CM = "cm"
UNIT_IN = "in"
UNITS = (UNIT_CM, UNIT_IN)
PER_INCH = {UNIT_CM: 2.54, UNIT_IN: 1.0}

#: Drawing units per inch: Qt's logical DPI for fonts on screen and in a
#: QImage (checked at 150 % display scaling: 96, with the device
#: pixel ratio doing the rest). An SVG generator defaults to 72 and is set to
#: this explicitly on export.
DESIGN_DPI = 96.0

#: The lengths a layout holds, all in its `unit`.
LENGTHS = ("width", "height", "margin_left", "margin_right", "margin_top",
           "margin_bottom")


class FigureLayout(object):
    """How big the figure is, and where its axes box sits inside it."""

    kind = "figure"

    def __init__(self):
        self.mode = MODE_WINDOW
        self.unit = UNIT_CM
        # A single-column journal figure, with room for 12 pt numbers and a
        # 14 pt caption on the left and below.
        self.width = 8.5
        self.height = 6.5
        self.margin_left = 1.9
        self.margin_right = 0.3
        self.margin_top = 0.3
        self.margin_bottom = 1.5
        #: Width : height for MODE_ASPECT.
        self.aspect_w = 4.0
        self.aspect_h = 3.0
        #: Pixels per inch of a PNG export. An SVG is exact at any size.
        self.dpi = 600
        #: Room the PROGRAM grew a margin by for an axis that appeared on
        #: its side (`MainWindow._room_for_axes`): `{side: [before, after]}`
        #: in `unit`. When that axis goes, the margin goes back to `before`
        #: (never below what is still drawn there) - unless it was set by
        #: hand since, when it is no longer `after`. Not part of a
        #: preset.
        self.grown = {}

    # ------------------------------------------------------------ lengths
    def to_px(self, value):
        """A length in this layout's unit, in drawing units."""
        return float(value) / PER_INCH[self.unit] * DESIGN_DPI

    def size_px(self):
        """`(width, height)` of the whole figure in drawing units."""
        return self.to_px(self.width), self.to_px(self.height)

    def margins_px(self):
        """`(left, right, top, bottom)` in drawing units."""
        return (self.to_px(self.margin_left), self.to_px(self.margin_right),
                self.to_px(self.margin_top), self.to_px(self.margin_bottom))

    def axes_size(self):
        """`(width, height)` of the axes box, in the layout's unit."""
        return (self.width - self.margin_left - self.margin_right,
                self.height - self.margin_top - self.margin_bottom)

    def set_axes_size(self, width, height):
        """Size the figure so its axes box is `width` x `height`, margins
        unchanged."""
        self.width = float(width) + self.margin_left + self.margin_right
        self.height = float(height) + self.margin_top + self.margin_bottom

    def inches(self):
        return (self.width / PER_INCH[self.unit],
                self.height / PER_INCH[self.unit])

    def set_unit(self, unit):
        """Change the unit, CONVERTING every length so nothing moves."""
        if unit not in UNITS or unit == self.unit:
            return
        factor = PER_INCH[unit] / PER_INCH[self.unit]
        for name in LENGTHS:
            setattr(self, name, getattr(self, name) * factor)
        self.grown = dict((side, [pair[0] * factor, pair[1] * factor])
                          for side, pair in self.grown.items())
        self.unit = unit

    def is_valid(self):
        axes_w, axes_h = self.axes_size()
        return (min(self.width, self.height) > 0 and axes_w > 0
                and axes_h > 0 and min(getattr(self, n) for n in LENGTHS) >= 0
                and self.aspect_w > 0 and self.aspect_h > 0 and self.dpi > 0)

    # ------------------------------------------------------------- state
    FIELDS = ("mode", "unit") + LENGTHS + ("aspect_w", "aspect_h", "dpi")

    def to_state(self):
        return dict((name, getattr(self, name)) for name in self.FIELDS)

    def load_state(self, state):
        """Take what is legal from a saved state; keep the rest."""
        if not isinstance(state, dict):
            return self
        if state.get("mode") in MODES:
            self.mode = state["mode"]
        if state.get("unit") in UNITS:
            self.unit = state["unit"]
        for name in LENGTHS + ("aspect_w", "aspect_h"):
            try:
                value = float(state[name])
            except (KeyError, TypeError, ValueError):
                continue
            if value >= 0:
                setattr(self, name, value)
        try:
            self.dpi = max(30, min(2400, int(state.get("dpi", self.dpi))))
        except (TypeError, ValueError):
            pass
        return self

    def copy(self):
        made = FigureLayout().load_state(self.to_state())
        made.grown = clean_grown(self.grown)
        return made


#: The page's four margins, as `FigureLayout` names them.
MARGIN_SIDES = ("left", "right", "top", "bottom")


def clean_grown(value):
    """A `FigureLayout.grown` read from a file: what is legal of it."""
    out = {}
    if not isinstance(value, dict):
        return out
    for side, pair in value.items():
        try:
            before, after = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if side in MARGIN_SIDES and 0 <= before <= after:
            out[side] = [before, after]
    return out
