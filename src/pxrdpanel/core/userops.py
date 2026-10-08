"""What the user added to the operator search (F3): the operators used
last, and aliases of their own (Christian, 2026-10-05, family-wide).

UI-free. Kept in `operators.json` beside the preferences
(`style.preferences_path`), so it belongs to the user and the
installation, never to a figure; a test's preferences are a temporary
file, and so is this. Written as soon as anything changes.

* RECENT - the ids of the operators last run from F3, newest first,
  `RECENT_COUNT` of them. The palette lists them on top and selects the
  newest, so F3 then Enter runs it again.
* ALIASES - `{operator id: [alias, ...]}`: words of the user's own that
  find an operator, beside its built-in ones. An alias is a search word
  and nothing else - it cannot run anything - so a file of them from
  somebody else is harmless.
* SHARING - `export` writes a file of aliases (`FORMAT`); dropped on the
  plot (or installed from the menu) it is read by `read_shared`, which
  knows one by its contents, not its name, and merged by `merge`: what
  this panel has is added, an id it does not know is reported.
* FACTORY - `reset` forgets both.
"""

import json
import os

from .. import branding
from . import style

#: What a file of shared aliases says it is.
FORMAT = "operator-aliases"
#: How many operators F3 remembers.
RECENT_COUNT = 5
#: The longest alias, in characters: a search word, not a sentence.
LONGEST = 40

_recent = []
_aliases = {}
#: The file `_recent` and `_aliases` were read from: read again whenever
#: the preferences move (every test has its own).
_loaded = [None]


def path():
    """`operators.json`, beside the preferences."""
    return os.path.join(os.path.dirname(style.preferences_path()),
                        "operators.json")


def clean_alias(text):
    """An alias as stored: its words, single spaces; None when it is not
    one (empty, too long, or not text)."""
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())
    if not text or len(text) > LONGEST:
        return None
    return text


def _clean_table(table):
    """`{id: [alias, ...]}` from whatever a file holds: text only,
    each alias once (case aside), empty entries dropped."""
    out = {}
    if not isinstance(table, dict):
        return out
    for op_id, entries in table.items():
        if not isinstance(op_id, str):
            continue
        if isinstance(entries, str):
            entries = [entries]
        if not isinstance(entries, (list, tuple)):
            continue
        kept = []
        for entry in entries:
            alias = clean_alias(entry)
            if alias and alias.lower() not in [a.lower() for a in kept]:
                kept.append(alias)
        if kept:
            out[op_id] = kept
    return out


def _ensure():
    """Read the file the first time it is needed, and again when the
    preferences have moved."""
    if _loaded[0] != path():
        load()


def load(where=None):
    """Read the file; a missing or unreadable one is none of either."""
    where = where or path()
    _loaded[0] = where
    del _recent[:]
    _aliases.clear()
    try:
        with open(where, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(stored, dict):
        return
    recent = stored.get("recent")
    if isinstance(recent, list):
        for op_id in recent:
            if isinstance(op_id, str) and op_id not in _recent:
                _recent.append(op_id)
        del _recent[RECENT_COUNT:]
    _aliases.update(_clean_table(stored.get("aliases")))


def save(where=None):
    where = where or path()
    _loaded[0] = where
    folder = os.path.dirname(where)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(where, "w", encoding="utf-8") as fh:
        json.dump({"format": "operators", "version": 1,
                   "recent": list(_recent),
                   "aliases": dict((k, list(v))
                                   for k, v in sorted(_aliases.items()))},
                  fh, indent=1)
    return where


# ------------------------------------------------------------------ recent
def recent():
    """The ids of the operators last run from F3, newest first."""
    _ensure()
    return list(_recent)


def note_used(op_id):
    """`op_id` was just run from F3: it goes to the front."""
    _ensure()
    if op_id in _recent:
        _recent.remove(op_id)
    _recent.insert(0, str(op_id))
    del _recent[RECENT_COUNT:]
    save()


# ----------------------------------------------------------------- aliases
def aliases():
    """A copy of every alias, `{id: [alias, ...]}`."""
    _ensure()
    return dict((k, list(v)) for k, v in _aliases.items())


def aliases_of(op_id):
    _ensure()
    return list(_aliases.get(op_id, ()))


def add_alias(op_id, text):
    """Give `op_id` the alias `text`. Returns the alias as stored, or None
    when it is not one or the operator has it already."""
    _ensure()
    alias = clean_alias(text)
    if alias is None:
        return None
    have = _aliases.get(op_id, [])
    if alias.lower() in [a.lower() for a in have]:
        return None
    _aliases[op_id] = have + [alias]
    save()
    return alias


def remove_aliases(op_id, alias=None):
    """Take `alias` (all of them, when None) off `op_id`. Returns how many
    went."""
    _ensure()
    have = _aliases.get(op_id, [])
    if alias is None:
        gone = len(have)
        _aliases.pop(op_id, None)
    else:
        kept = [a for a in have if a.lower() != str(alias).lower()]
        gone = len(have) - len(kept)
        if kept:
            _aliases[op_id] = kept
        else:
            _aliases.pop(op_id, None)
    if gone:
        save()
    return gone


def reset():
    """Back to the factory: no aliases of the user's, nothing recent."""
    _ensure()
    del _recent[:]
    _aliases.clear()
    save()


# ----------------------------------------------------------------- sharing
def export(where):
    """Write every alias to `where` as a file somebody else can drop on
    their panel. Returns the path, or None when there are none."""
    _ensure()
    if not _aliases:
        return None
    with open(where, "w", encoding="utf-8") as fh:
        json.dump({"format": FORMAT, "version": 1,
                   "app": branding.APP_NAME,
                   "aliases": dict((k, list(v))
                                   for k, v in sorted(_aliases.items()))},
                  fh, indent=1)
    return where


def is_shared_file(where):
    """True for a file of shared aliases: a `.json` that SAYS it is one
    (any other `.json` is somebody else's business)."""
    if not str(where).lower().endswith(".json"):
        return False
    try:
        return read_shared(where) is not None
    except ValueError:
        return False


def read_shared(where):
    """`(app, {id: [alias, ...]})` from a shared file. Raises ValueError
    with the reason when it is not one."""
    try:
        with open(where, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except OSError as exc:
        raise ValueError("could not read it: {}".format(exc))
    except ValueError:
        raise ValueError("not JSON")
    if not isinstance(stored, dict) or stored.get("format") != FORMAT:
        raise ValueError("not a file of operator aliases")
    return str(stored.get("app") or ""), _clean_table(stored.get("aliases"))


def merge(table, known):
    """What installing `table` would do here, where `known(op_id)` says
    whether an operator exists: `(added, unknown)` - `[(id, alias)]` new
    to this installation, and the ids this panel does not have. Nothing
    is changed; `install` does it."""
    _ensure()
    added, unknown = [], []
    for op_id, entries in sorted(table.items()):
        if not known(op_id):
            unknown.append(op_id)
            continue
        have = [a.lower() for a in _aliases.get(op_id, ())]
        for alias in entries:
            if alias.lower() not in have:
                added.append((op_id, alias))
                have.append(alias.lower())
    return added, unknown


def install(added):
    """Add what `merge` found, `[(id, alias)]`, at once."""
    _ensure()
    for op_id, alias in added:
        have = _aliases.setdefault(op_id, [])
        if alias.lower() not in [a.lower() for a in have]:
            have.append(alias)
    if added:
        save()
    return len(added)
