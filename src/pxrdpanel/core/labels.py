"""What an analysis label SAYS: the words are the user's, the number is not.

A label is a TEMPLATE. `{}` is the measured value, filled in by the program
every time the label is drawn, and `{d}` the d-spacing of a peak where its
wavelength is known; everything else is the user's text, with the figure
markup (`*d*` italic, `_{a}` subscript, `^{-1}` superscript, `\\theta`,
`\\AA`). The default templates are `{}` for a peak position, `Area = {}`
for a peak area and `FWHM = {}` for a width.

The rules, and why each one is where the program takes control away:

1. **The number is never typed.** It reaches a label only through `{}` and
   `{d}`, so re-measuring can never leave a stale number on the figure.
2. **A unit the quantity is not in is refused.** A result is on the x axis
   it was measured on - degrees 2-theta, angstrom or inverse angstrom (an
   area is intensity times that unit; intensity has none) - and a `{d}`
   is in angstrom. `{} nm` is drawn in the axis's unit, and the settings
   say why. A number is never drawn beside a unit it is not in.
3. **The digits are the user's** (`core/numbers.py`): rounding changes how
   exact a number looks, never what it is.
4. **Free text is free.** A number typed by hand ("lit. 12.3 deg") is
   allowed, because it may be a reference value; but one beside a unit is
   flagged in the settings as not the measurement.

UI-free, like the rest of `core/`.
"""

import re

from . import numbers
from . import units

POSITION = "position"
AREA = "area"

#: The template an analysis carries until somebody writes their own, by
#: model.
DEFAULTS = (
    ("Peak position", "{}"),
    ("Peak area", "Area = {}"),
    ("Peak width", "FWHM = {}"),
)

#: How each axis's unit may be written after a number, by quantity.
_SPELLINGS = {
    units.TWO_THETA: ("\\degree", "\\circ", "\u00b0", "deg", "degrees",
                      "degree"),
    units.D: ("\\AA", "\u00c5", "A", "angstrom"),
    units.Q: ("\\AA^{-1}", "\u00c5^{-1}", "\u00c5\u207b\u00b9", "1/A",
              "A^-1", "A-1", "1/\u00c5"),
}

#: `{}` or `{d}`, and the unit right after it if one was written. Not
#: `_{}` or `^{}`, which are markup (an empty subscript).
_PLACEHOLDER = re.compile(
    r"(?<![_^])\{(d?)\}(?:(\s*)((?:\\[A-Za-z]+|[A-Za-z\u00b0\u00c5])"
    r"[\w^{}/\u207b\u00b9-]*))?")
_TYPED = re.compile(
    r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?\s*(\u00b0|deg\b|\\degree|\u00c5|"
    r"\\AA|angstrom)", re.I)


def canonical_unit(text, quantity=units.TWO_THETA):
    """A unit as the program writes it on `quantity`'s axis, or None for
    anything that is not that axis's unit."""
    token = str(text or "").strip().replace(" ", "")
    for spelling in _SPELLINGS.get(quantity, ()):
        if token.lower() == spelling.lower():
            return units.X_UNIT[quantity]
    return None


def normalise_format(spec):
    """A number format that may carry a unit (`%.2f deg`), canonical."""
    def known(text):
        for quantity in units.QUANTITIES:
            found = canonical_unit(text, quantity)
            if found:
                return found
        return None
    return numbers.normalise(spec, known)


class Rendered(object):
    """A label as drawn: its text and what is wrong with it.

    `problems` is `[(kind, message), ...]`: "unit" (a unit the quantity
    cannot be put in), "typed" (a number written by hand beside a unit:
    not the measurement) and "missing" (a `{d}` with no wavelength)."""

    def __init__(self, text, problems=(), value_text=""):
        self.text = text
        self.problems = list(problems)
        self.value_text = value_text

    def missing(self):
        return [m for kind, m in self.problems if kind == "missing"]


def quantity_of(model_name):
    """What an analysis of this model reports: a position (a position, a
    width) or an area."""
    return AREA if "area" in str(model_name).lower() else POSITION


def default_template(analysis):
    for word, template in DEFAULTS:
        if word in analysis.model_name:
            return template
    return "%s = {}" % analysis.model_name


def template_of(analysis):
    return (analysis.label if analysis.label is not None
            else default_template(analysis))


def axis_of(analysis, doc=None):
    """The x quantity an analysis's numbers are on."""
    own = getattr(analysis, "axis", None)
    if own:
        return own
    return getattr(doc, "x_quantity", units.TWO_THETA)


def result(analysis):
    """`(value, quantity)`: the number a label shows, on its own axis."""
    from .model import number
    name = analysis.model_name
    fields = analysis.fields
    quantity = quantity_of(name)
    if quantity == AREA:
        return number(fields.get("Area")), quantity
    if "width" in name.lower():
        return number(fields.get("FWHM")), quantity
    return number(fields.get("Position")), quantity


def d_of(analysis, doc=None):
    """The d-spacing (angstrom) of an analysis's peak, or None."""
    from .model import number
    if axis_of(analysis, doc) == units.D:
        return number(analysis.fields.get("Position"))
    return number(analysis.fields.get("d"))


def unit_text(quantity, value_text):
    """`value_text` followed by `quantity`'s unit, as the figure writes
    it ("12.34\\degree", "3.21 \\AA")."""
    return "{}{}{}".format(value_text, units.X_GLUE[quantity],
                           units.X_UNIT[quantity])


def units_of(_quantity, doc=None):
    """The units a result can be shown in: its axis's."""
    return [units.X_UNIT[getattr(doc, "x_quantity", units.TWO_THETA)]]


def natural_unit(_quantity=None, doc=None):
    return units.X_UNIT[getattr(doc, "x_quantity", units.TWO_THETA)]


def number_format(analysis, doc):
    from . import style
    return style.value(doc, analysis, "number_format")


def render(analysis, doc=None):
    """The label's text, with every `{}` and `{d}` filled in, and its
    problems."""
    template = template_of(analysis)
    value, quantity = result(analysis)
    axis = axis_of(analysis, doc)
    spec = number_format(analysis, doc)
    problems = []
    fallback = numbers.POSITION if quantity == POSITION else numbers.VALUE
    shown = []

    def fill(match):
        wants_d = match.group(1) == "d"
        typed = match.group(3)
        on = units.D if wants_d else axis
        if typed and canonical_unit(typed, on) is None:
            problems.append(("unit", "'{}' is not a unit of the {}: shown "
                                     "in {}".format(typed, "d-spacing"
                                                    if wants_d else quantity,
                                                    units.X_TEXT[on])))
        number = d_of(analysis, doc) if wants_d else value
        if number is None:
            if wants_d:
                problems.append(("missing", "no wavelength for a d-spacing"))
            text = unit_text(on, "?")
        else:
            text = unit_text(on, numbers.write(number, spec, fallback))
        shown.append(text)
        return text

    text = _PLACEHOLDER.sub(fill, template)
    if analysis.label is not None:
        for match in _TYPED.finditer(_PLACEHOLDER.sub("", template)):
            problems.append(("typed", "'{}' is typed by hand, not the "
                                      "measurement; {{}} shows the measured "
                                      "value".format(match.group(0).strip())))
    return Rendered(text, problems, shown[0] if shown else "")


def position_text(value, doc=None, spec=None):
    """A position written with the house format and the axis's unit: what
    a marker line's `{}` and a distance arrow's say."""
    from . import style
    if spec is None:
        spec = (style.figure_value(doc, "position_format")
                if doc is not None else style.preference("position_format"))
    quantity = getattr(doc, "x_quantity", units.TWO_THETA)
    return unit_text(quantity, numbers.write(value, spec, numbers.POSITION))


def fill_position(template, value, doc=None, spec=None):
    """`template` with every `{}` (not `_{}` or `^{}`) as `value` on the
    axis."""
    return re.sub(r"(?<![_^])\{\}",
                  lambda _m: position_text(value, doc, spec),
                  str(template))


def span_text(span, doc=None):
    """A distance arrow's template: its own, else "Delta" and the axis's
    symbol, "= {}"."""
    if getattr(span, "text", None) is not None:
        return str(span.text)
    quantity = getattr(doc, "x_quantity", units.TWO_THETA)
    return "\\Delta{} = {{}}".format(units.X_SYMBOL[quantity])


_NAMES = ("Position", "d", "FWHM", "Area", "Height")


def results(analysis, doc=None):
    """`[(name, text), ...]`: every result an analysis carries beyond its
    cursors, written in the figure's formats - what its settings list."""
    from .model import number
    from . import style
    out = []
    axis = units.X_TEXT[axis_of(analysis, doc)]
    for key in _NAMES:
        if key not in analysis.fields:
            continue
        value = number(analysis.fields.get(key))
        if value is None:
            continue
        if key == "Height":
            out.append(("Height", numbers.write(
                value, style.figure_value(doc, "value_format"),
                numbers.VALUE)))
            continue
        spec = style.figure_value(doc, "position_format" if key != "Area"
                                  else "value_format")
        unit = "A" if key == "d" else axis
        out.append((key, "{} {}".format(numbers.write(
            value, spec, numbers.POSITION if key != "Area"
            else numbers.VALUE), unit)))
    return out
