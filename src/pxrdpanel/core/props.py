"""Copying settings from objects onto others of their kind (Ctrl+C,
Ctrl+V): the same module in every panel of the family.

Ctrl+C puts the selection on the clipboard as JSON (`state`): each object's
kind, its settings, and - for a curve or an analysis - the analyses that go
with it. Ctrl+V with objects of a copied kind selected offers what to paste
onto them (`groups`): all their settings, or the colour, the sizes, the
style, the text or the place alone; a curve also takes a copied curve's
analyses, measured again on it. What a kind's settings ARE is its settings
windows' business - their `FIELDS`, less the `INDIVIDUAL` ones that are one
object's own (a label's text, an analysis's interval) - so the window names
them. UI-free.
"""

import json

#: What a copy is, in its JSON.
FORMAT = "panel-objects"
#: Never pasted: whether an object is shown, its layer, a colour's link.
NEVER = ("visible", "z", "colour_from")
#: An object's words: offered by themselves ("Text"), never with the rest.
TEXT = ("text", "label", "word")
#: Where an object is, where that is a setting at all (the legend, the
#: arrow, a picture): offered by itself ("Place").
PLACE = ("x", "y", "space", "anchor", "rotation", "lock", "at", "dx",
         "dy")
#: A field ending so is a size.
SIZE_ENDS = ("size", "width", "length", "spacing", "gap")
#: The menu's entries, with the letter that picks each.
TITLES = (("All settings", "&All settings"), ("Colour", "&Colour"),
          ("Sizes", "Si&zes"), ("Style", "St&yle"), ("Text", "&Text"),
          ("Place", "&Place"))


def kind_of(obj):
    """What an object is, for pasting: its class's name."""
    return type(obj).__name__


def _plain(value):
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return dict((str(k), _plain(v)) for k, v in value.items())
    return value


def settings_of(obj, fields):
    """`{field: value}` of `obj` for `fields`, as JSON holds them: tuples
    as lists; a value JSON cannot hold, and `NEVER`, left out."""
    out = {}
    for name in fields:
        if name in NEVER or not hasattr(obj, name):
            continue
        value = _plain(getattr(obj, name))
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        out[name] = value
    return out


def groups(fields, individual=()):
    """`[(title, [field, ...]), ...]`: what a paste onto a kind with these
    settings offers, in the menu's order. "All settings" is everything but
    the text and the place; "Text" is an object's words, which are its own
    (`individual`) and pasted only when asked for by name."""
    shared = [n for n in fields if n not in individual and n not in NEVER]
    colour = [n for n in shared if "colour" in n]
    sizes = [n for n in shared if n not in colour and n.endswith(SIZE_ENDS)]
    place = [n for n in shared if n in PLACE]
    text = [n for n in fields if n in TEXT and n not in NEVER]
    style = [n for n in shared
             if n not in colour + sizes + place + text]
    everything = colour + sizes + style
    out = [("All settings", everything)]
    for title, names in (("Colour", colour), ("Sizes", sizes),
                         ("Style", style)):
        if names and names != everything:
            out.append((title, names))
    out += [("Text", text), ("Place", place)]
    return [(title, names) for title, names in out if names]


def menu_text(title):
    """`title` with the letter that picks it in the menu."""
    return dict(TITLES).get(title, title)


def pairs(sources, targets):
    """Which copied object goes onto which target: in order when there are
    as many of each, else the first onto every one."""
    if not sources or not targets:
        return []
    if len(sources) == len(targets):
        return list(zip(sources, targets))
    return [(sources[0], target) for target in targets]


def changes(target, settings, names):
    """`[(target, field, value), ...]`: `settings` (a copy's) for `names`
    onto `target` - a list back to a tuple where the target holds one. A
    colour pasted ends a link to another object's (`colour_from`), as a
    colour chosen by hand does."""
    out = []
    for name in names:
        if name not in settings or not hasattr(target, name):
            continue
        value = settings[name]
        if isinstance(getattr(target, name), tuple) and isinstance(value,
                                                                   list):
            value = tuple(value)
        out.append((target, name, value))
        if (name == "colour"
                and getattr(target, "colour_from", None) is not None):
            out.append((target, "colour_from", None))
    return out


def state(app, entries):
    """The clipboard's JSON for copied `entries` (the window makes them:
    `{"kind", "settings"}`, and "data", "analyses" or "analysis")."""
    return {"format": FORMAT, "app": app, "objects": list(entries)}


def read(data):
    """A copy's state from the clipboard's bytes or text, or None when it
    is not one."""
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        found = json.loads(data)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(found, dict) or found.get("format") != FORMAT:
        return None
    objects = [o for o in found.get("objects") or ()
               if isinstance(o, dict) and o.get("kind")]
    found["objects"] = objects
    return found


def copied_analyses(objects):
    """The analyses copied with `objects`, as lists of `(data, analysis
    state)` - `data` being what its curve was (`session.data_key`): one
    list per copied curve, and one for the analyses copied by
    themselves."""
    out = []
    loose = []
    for entry in objects:
        if entry.get("kind") == "Scan" and entry.get("analyses"):
            data = entry.get("data")
            out.append([(data, saved) for saved in entry["analyses"]])
        elif entry.get("kind") == "Analysis" and entry.get("analysis"):
            loose.append((entry.get("data"), entry["analysis"]))
    if loose:
        out.append(loose)
    return out
