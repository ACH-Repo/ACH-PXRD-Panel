"""Style presets: a figure's look in a file, chosen from a menu.

The house style (`core/style.py`) already has a figure's own column: sizes,
formats, fonts and alignment saved with ONE session. A preset is that column
taken out of the session and given a name, so the same look - "thesis",
"poster", "ACS single column" - is put on any figure in one step, and passed
to a colleague as a file.

What a preset holds:

* **the style**: every figure setting, RESOLVED (the value the figure is
  drawn with, not "follow my defaults"), so a preset looks the same on a
  machine whose defaults differ. A hand-written preset may name only some
  settings; the others are then left as they are.
* **the size and margins**, optionally (`core/figure.py`): with them, two
  figures made with one preset have the same axes box to the hundredth of a
  millimetre - two stacks side by side in Word.
* **the frame and the furniture** every figure has: both axes - caption,
  sizes, distances, ticks, numbers, sides - and the legend's place and
  look. Nothing that belongs to one figure's data: patterns, labels, marker
  lines, analyses, regions, pictures.

* **the page's colour**: white, another, or "the theme's" (None) -
  `Document.background`, which came after the presets. A preset saved
  before they carried it says nothing of it (`KEEP`) and leaves the page
  as it is.

What it does not touch: the other objects' own choices (a label sized by
hand stays so), the handling settings (the pick distance is about a hand,
not a figure) and the screen theme (dark or light: the program's, not the
figure's).

Applying one is ONE undo step on the figure's column. The files are JSON
with the extension in `branding.PRESET_EXT`, in a `presets` folder beside
the preferences; dropping one onto the window installs it there.

UI-free, like the rest of `core`.
"""

import io
import json
import os
import re
import shutil

from .. import branding
from . import figure as figure_module
from . import labels
from . import model
from . import style

FORMAT = "style-preset"
VERSION = 1

#: A preset that says nothing of the page's colour (one saved before it
#: carried it): applying it leaves the page as it is.
KEEP = object()
#: A page colour a preset may hold, besides None (the theme's).
_PAGE = re.compile(r"^#[0-9a-fA-F]{6}$")

_AXIS_FIELDS = ("visible", "label", "label_size", "label_along", "label_gap",
                "show_numbers", "show_ticks", "tick_size",
                "number_format", "side",
                "ticks_inward", "minor_ticks", "minor_count", "major_step",
                "tick_length", "minor_length", "mirror", "mirror_ticks",
                "show_grid")

#: Which fields of which of the figure's own objects a preset carries.
OBJECT_FIELDS = {
    "axis_x": _AXIS_FIELDS,
    "axis_y": _AXIS_FIELDS,
    "legend": ("visible", "x", "y", "space", "anchor", "colour", "rotation",
               "size", "show_frame", "sample", "spacing", "line_width"),
}

#: What each carried field may hold, for checking a file: "bool", "number",
#: "number?" (or None), "int", "text?", "format?", or a tuple of choices.
_KINDS = {
    "visible": "bool", "show_numbers": "bool", "show_ticks": "bool",
    "ticks_inward": "bool",
    "minor_ticks": "bool", "mirror": "bool", "mirror_ticks": "bool",
    "show_grid": "bool", "show_frame": "bool",
    "label": "text?", "number_format": "format?", "colour": "text",
    "label_size": "number?", "label_gap": "number?", "tick_size": "number?",
    "major_step": "number?", "size": "number?", "line_width": "number?",
    "minor_count": "int", "label_along": "number", "tick_length": "number",
    "minor_length": "number", "x": "number", "y": "number",
    "rotation": "number", "sample": "number", "spacing": "number",
    "space": (model.SPACE_RELATIVE, model.SPACE_DATA),
    "anchor": tuple(model.ANCHORS),
}

#: An axis's side, by which axis it is.
_SIDES = {"axis_x": ("bottom", "top"), "axis_y": ("left", "right")}


def figure_objects(doc):
    """`{name: object}` for the objects a preset styles."""
    return {"axis_x": doc.axes["x"], "axis_y": doc.axes["y"],
            "legend": doc.legend}


def _checked(name, field, raw):
    """`(True, value)` when `raw` is a legal value of `field`, else
    `(False, None)`."""
    kind = _SIDES[name] if field == "side" and name in _SIDES \
        else _KINDS.get(field)
    if isinstance(kind, tuple):
        return (raw in kind, raw if raw in kind else None)
    if kind is None:
        return False, None
    if kind.endswith("?") and raw is None:
        return True, None
    kind = kind.rstrip("?")
    if kind == "bool":
        return (isinstance(raw, bool), raw)
    if kind in ("text", "format"):
        if not isinstance(raw, str):
            return False, None
        if kind == "format":
            cleaned = labels.normalise_format(raw)
            return (cleaned is not None, cleaned)
        return True, raw
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return False, None
    if raw != raw or abs(raw) == float("inf"):
        return False, None
    if kind == "int":
        return (raw == int(raw) and raw >= 1, int(raw))
    return True, float(raw)


class Preset(object):
    """A named look: `values` {setting key: value}; `layout` - a
    `FigureLayout` state - or None to leave the size alone; and `objects`
    {"axis_x" / "axis_y" / "legend": {field: value}}."""

    def __init__(self, name, values=None, layout=None, path="",
                 objects=None, background=KEEP):
        self.name = str(name)
        #: The page's colour: "#rrggbb", None for the theme's, or `KEEP`.
        self.background = background
        self.values = dict(values or {})
        self.layout = dict(layout) if layout else None
        self.objects = dict((k, dict(v)) for k, v in (objects or {}).items())
        self.path = str(path or "")

    def __repr__(self):
        return "Preset({!r})".format(self.name)


def folder():
    """Where presets live: beside the preferences (so a test's temporary
    preferences have their own presets too)."""
    return os.path.join(os.path.dirname(style.preferences_path()), "presets")


def from_figure(doc, name, with_layout=True):
    """This figure's look as a preset: every figure setting as it is DRAWN,
    and the size and margins when `with_layout`."""
    values = dict((setting.key, style.figure_value(doc, setting.key))
                  for setting in style.FIGURE_SETTINGS)
    layout = doc.figure.to_state() if with_layout else None
    objects = dict((name, dict((field, getattr(obj, field))
                               for field in OBJECT_FIELDS[name]))
                   for name, obj in figure_objects(doc).items())
    return Preset(name, values, layout, objects=objects,
                  background=doc.background)


def to_state(preset):
    state = {"format": FORMAT, "version": VERSION, "name": preset.name,
             "style": dict(preset.values)}
    if preset.layout:
        state["figure"] = dict(preset.layout)
    if preset.objects:
        state["objects"] = dict((k, dict(v))
                                for k, v in preset.objects.items())
    if preset.background is not KEEP:
        state["background"] = preset.background
    return state


def from_state(state, path=""):
    """A preset from its JSON state. Every value is checked the way the
    preferences are (`Setting.clean`): an unknown key or an illegal value is
    left out rather than raised about. ValueError when it is no preset."""
    if not isinstance(state, dict) or state.get("format") != FORMAT:
        raise ValueError("not a style preset")
    values = {}
    for key, raw in (state.get("style") or {}).items():
        setting = style.BY_KEY.get(key)
        if setting is None or not setting.figure:
            continue
        cleaned = setting.clean(raw)
        if cleaned is not None:
            values[key] = cleaned
    layout = state.get("figure")
    if isinstance(layout, dict):
        checked = figure_module.FigureLayout().load_state(layout)
        layout = (dict((name, getattr(checked, name))
                       for name in figure_module.FigureLayout.FIELDS
                       if name in layout)
                  if checked.is_valid() else None)
    else:
        layout = None
    objects = {}
    for name, fields in (state.get("objects") or {}).items():
        if name not in OBJECT_FIELDS or not isinstance(fields, dict):
            continue
        kept = {}
        for field, raw in fields.items():
            if field not in OBJECT_FIELDS[name]:
                continue
            ok, value = _checked(name, field, raw)
            if ok:
                kept[field] = value
        if kept:
            objects[name] = kept
    background = KEEP
    raw = state.get("background", KEEP)
    if raw is None or (isinstance(raw, str) and _PAGE.match(raw)):
        background = raw
    name = str(state.get("name") or "").strip() or os.path.splitext(
        os.path.basename(path))[0] or "Preset"
    return Preset(name, values, layout, path, objects, background)


def read(path):
    """The preset in the file at `path`. ValueError when it is none."""
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read {}: {}".format(
            os.path.basename(path), exc))
    return from_state(state, path)


def file_name(name):
    """A file name for a preset called `name`: its letters, digits, spaces
    and dashes, and the preset extension."""
    stem = re.sub(r"[^\w\- ]+", "", str(name)).strip() or "preset"
    return stem + branding.PRESET_EXT


def save(preset, where=None):
    """Write `preset` into the presets folder (or `where`), replacing one of
    the same name. Returns the path."""
    where = where or folder()
    if not os.path.isdir(where):
        os.makedirs(where)
    path = os.path.join(where, file_name(preset.name))
    temporary = path + ".tmp"
    with io.open(temporary, "w", encoding="utf-8") as fh:
        json.dump(to_state(preset), fh, indent=1)
    os.replace(temporary, path)
    preset.path = path
    return path


def available(where=None):
    """`(presets, problems)`: every preset in the folder, by name, and a
    line for each file that could not be read."""
    where = where or folder()
    found, problems = [], []
    try:
        names = sorted(os.listdir(where))
    except OSError:
        return [], []
    for entry in names:
        if not entry.lower().endswith(branding.PRESET_EXT):
            continue
        try:
            found.append(read(os.path.join(where, entry)))
        except ValueError as exc:
            problems.append(str(exc))
    found.sort(key=lambda p: p.name.lower())
    return found, problems


def install(path, where=None):
    """A preset file from elsewhere (a drop, a colleague's), copied into the
    presets folder and returned. ValueError when it is no preset."""
    preset = read(path)
    where = where or folder()
    if not os.path.isdir(where):
        os.makedirs(where)
    target = os.path.join(where, file_name(preset.name))
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(
            os.path.abspath(target)):
        shutil.copyfile(path, target)
    preset.path = target
    return preset


def changes(doc, preset):
    """`[(obj, attr, value), ...]` that put `preset` on `doc`: the figure's
    style column and, when the preset has one, its size and margins. Only
    what differs, so an undo step holds exactly what changed."""
    out = []
    for key, value in preset.values.items():
        if getattr(doc.style, key, None) != value:
            out.append((doc.style, key, value))
    if preset.layout:
        layout = doc.figure
        wanted = layout.copy().load_state(preset.layout)
        for name in figure_module.FigureLayout.FIELDS:
            if getattr(wanted, name) != getattr(layout, name):
                out.append((layout, name, getattr(wanted, name)))
    targets = figure_objects(doc)
    for name, fields in preset.objects.items():
        obj = targets.get(name)
        if obj is None:
            continue
        for field, value in fields.items():
            if getattr(obj, field, None) != value:
                out.append((obj, field, value))
    if (preset.background is not KEEP
            and getattr(doc, "background", None) != preset.background):
        out.append((doc, "background", preset.background))
    return out
