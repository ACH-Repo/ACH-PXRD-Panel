"""Saving and reopening an arrangement.

A figure of ten patterns is half an hour of stacking, colouring, labelling
and marking peaks, and it is worth nothing if it cannot be reopened. So the
arrangement is a file: which measurements, where each one sits, what colour
it is, what the axes show, where every label, marker line and region is.

**The measurements themselves are NOT copied in.** A session stores paths
and reads the files again, which keeps a session small and keeps one copy of
the data on disk. A file that has moved is reported by name instead of
failing the whole load, and what the session held of it is KEPT
(`model.MissingSource`): saved again as it was, and back on the figure once
the file is found. An analysis is stored as its model and interval and
MEASURED AGAIN on load - a session keeps the arrangement, never a copy of
the numbers.

UI-free: `load` takes the reader as an argument, so this module never
imports the reader or a window and is testable with a stub.
"""

import base64
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import zlib

from . import crystal
from . import figure as figure_module
from . import labels
from . import measure
from . import model
from . import numbers
from . import style
from . import units

FORMAT = "pxrdpanel-session"
VERSION = 1


def view_to_state(view):
    """The framing as plain data (lists), or None."""
    if not view:
        return None
    return {"x": list(view["x"]) if view.get("x") else None,
            "y": list(view["y"]) if view.get("y") else None,
            "context": list(view.get("context") or ())}


def _view_from(saved):
    """The framing back as the plot keeps it (tuples), or None."""
    if not isinstance(saved, dict):
        return None
    try:
        return {"x": tuple(float(v) for v in saved["x"]) if saved.get("x")
                else None,
                "y": tuple(float(v) for v in saved["y"]) if saved.get("y")
                else None,
                "context": tuple(saved.get("context") or ())}
    except (TypeError, ValueError, KeyError):
        return None


def _marker_at(value):
    """A stored marker place: None, ("i", sample) or ("x", position)."""
    try:
        kind, number = value
        if kind == "i":
            return ("i", int(number))
        if kind == "x":
            return ("x", float(number))
    except (TypeError, ValueError):
        pass
    return None


def _wavelength(value):
    """A stored wavelength (angstrom), or None."""
    value = _number_or_none(value)
    return value if value and 0.01 < value < 20.0 else None


def _pair(value):
    """A stored `(low, high)`, or None."""
    try:
        low, high = sorted(float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return (low, high) if high > low else None


def _number_or_none(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _chosen(value, key):
    """A stored styled value, cleaned, or None."""
    return style.BY_KEY[key].clean(value)


def _analysis_state(analysis):
    """One analysis: how it is drawn, and how to measure it again."""
    return {"model": analysis.model_name, "cursors": analysis.cursors(),
            "axis": analysis.axis,
            "span": list(analysis.span) if analysis.span else None,
            "visible": analysis.visible, "colour": analysis.colour,
            "label": analysis.label, "label_dy": analysis.label_dy,
            "shade": analysis.shade, "shading": analysis.shading,
            "label_size": analysis.label_size, "flush": analysis.flush,
            "show_interval": analysis.show_interval,
            "interval_size": analysis.interval_size,
            "number_format": analysis.number_format,
            "label_at": analysis.label_at, "z": analysis.z}


def _restore_analysis(analysis, saved):
    analysis.visible = bool(saved.get("visible", True))
    analysis.colour = saved.get("colour", "auto")
    analysis.label = saved.get("label")
    analysis.label_dy = _number_or_none(saved.get("label_dy"))
    analysis.shade = bool(saved.get("shade", True))
    shading = saved.get("shading")
    analysis.shading = shading if shading in style.SHADINGS else None
    analysis.label_size = _chosen(saved.get("label_size"), "analysis_size")
    flush = saved.get("flush")
    analysis.flush = flush if flush in style.FLUSHES else None
    analysis.show_interval = bool(saved.get("show_interval", True))
    analysis.interval_size = _chosen(saved.get("interval_size"),
                                     "interval_tick")
    analysis.number_format = labels.normalise_format(
        saved.get("number_format"))
    analysis.label_at = _number_or_none(saved.get("label_at"))
    analysis.z = _number_or_none(saved.get("z"))



# ------------------------------------------------- where a session's files are
# A session names its files by path. Moved, a file is looked for BESIDE THE
# SESSION (its folder and the folders under it, by name), and where the
# panel keeps copies (`profile.EMBED_SOURCES`) the copy inside the session is
# read when it is nowhere to be found - and the opening says which. A file
# found by none of these is KEPT (`model.MissingSource`) until the user
# finds it: by hand, or under a folder by a name like it (`similar_files`).

def _source_copy(sample):
    """The copy a session keeps of a sample's file - zlib, then base64 - or
    None. Made once per file read (`is_modified` saves often)."""
    data = getattr(sample, "source_bytes", None)
    if not data:
        return None
    cached = getattr(sample, "_source_copy", None)
    if cached is None or cached[0] is not data:
        cached = (data, base64.b64encode(zlib.compress(data, 9)).decode(
            "ascii"))
        sample._source_copy = cached
    return cached[1]


#: How many files the look beside a session reads the names of, at most.
LOOK_LIMIT = 20000


def _look_beside(session_path, wanted):
    """A file named as `wanted` is, in the session's folder or a folder
    under it, or None."""
    name = os.path.basename(str(wanted).replace("\\", "/")).lower()
    if not name or not session_path:
        return None
    folder = os.path.dirname(os.path.abspath(session_path))
    seen = 0
    for base, dirs, files in os.walk(folder):
        dirs.sort()
        for found in files:
            if found.lower() == name:
                return os.path.join(base, found)
        seen += len(files)
        if seen > LOOK_LIMIT:
            break
    return None


#: How alike a file's name must be to the one a session knows for a search
#: under a folder to offer it (`similar_files`): difflib's ratio of the two
#: names without their extensions, case ignored. "Run-A(1)" and "Run-A" are
#: 0.92 alike - and so are "Run-1" and "Run-2", which is why such a file is
#: only ever OFFERED, never taken by itself.
SIMILAR = 0.85
#: How many file names a search under a folder reads, at most.
FIND_LIMIT = 200000

#: What Windows and a browser add to a copy's name: "x (2)", "x(1)",
#: "x - Copy", "x - Kopie (3)".
_COPY_MARKS = re.compile(
    r"(\s*\(\d+\)|\s*-\s*(copy|kopie|copie|copia|kopia)(\s*\(\d+\))?)+$",
    re.IGNORECASE)


def _name_parts(path):
    stem, ext = os.path.splitext(os.path.basename(
        str(path).replace("\\", "/")))
    return stem.lower(), ext.lower()


def similar_files(folder, wanted, cutoff=SIMILAR, limit=FIND_LIMIT):
    """The files under `folder` named like the paths in `wanted`:
    `({path: [(score, found), ...]}, complete)`. A file must have the same
    extension, and its name without it be at least `cutoff` alike
    (`SIMILAR`; the same name scores 1) - or be the same name but for the
    marks of a copy ("x (1)", "x - Copy"). The likeliest come first: the
    same name, then a copy's, then a name with the same NUMBERS in it,
    then the rest by score - "Run-2" is as like "Run-1" as "Run-1(1)" is,
    and is another run. `complete` is False when the search stopped after
    `limit` names."""
    targets = []
    for path in wanted:
        stem, ext = _name_parts(path)
        targets.append((path, stem, ext, _COPY_MARKS.sub("", stem),
                        re.findall(r"\d+", stem)))
    hits = dict((path, []) for path in wanted)
    seen, complete = 0, True
    for base, dirs, files in os.walk(str(folder)):
        dirs.sort()
        for name in files:
            stem, ext = _name_parts(name)
            for path, want, want_ext, want_core, numbers in targets:
                if ext != want_ext:
                    continue
                match = difflib.SequenceMatcher(None, want, stem)
                if stem == want:
                    score, rank = 1.0, 3
                elif _COPY_MARKS.sub("", stem) == want_core:
                    score, rank = match.ratio(), 2
                else:
                    if (match.real_quick_ratio() < cutoff
                            or match.quick_ratio() < cutoff):
                        continue
                    score = match.ratio()
                    if score < cutoff:
                        continue
                    rank = 1 if re.findall(r"\d+", stem) == numbers else 0
                hits[path].append((rank, score, os.path.join(base, name)))
        seen += len(files)
        if seen > limit:
            complete = False
            break
    out = {}
    for path, found in hits.items():
        found.sort(key=lambda hit: (-hit[0], -hit[1], hit[2].lower()))
        out[path] = [(score, where) for _rank, score, where in found]
    return out, complete


def _size_text(size):
    for unit, step in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if size >= step:
            return "{:,} bytes ({:.1f} {})".format(size, size / step, unit)
    return "{:,} bytes".format(size)


def file_facts(path):
    """`[(what, value), ...]` of a file on disk for "Details...": where it
    is, how big, when it was made and changed, and a fingerprint of its
    contents - what tells two files of one name apart."""
    path = str(path)
    facts = [("File", os.path.basename(path)),
             ("Folder", os.path.dirname(os.path.abspath(path)))]
    try:
        info = os.stat(path)
    except OSError:
        facts.append(("On disk", "not there"))
        return facts

    def when(seconds):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))

    facts.append(("Size", _size_text(info.st_size)))
    if os.name == "nt":                 # st_ctime is the creation there
        facts.append(("Created", when(info.st_ctime)))
    facts.append(("Modified", when(info.st_mtime)))
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        facts.append(("SHA-256", digest.hexdigest()))
    except OSError:
        pass
    return facts


def missing_facts(gone):
    """`[(what, value), ...]` of a file the session could not read
    (`model.MissingSource`), for "Details..."."""
    facts = [("File", gone.name), ("Saved as", gone.path),
             ("Why", gone.reason or "not found")]
    folder = os.path.dirname(gone.path)
    facts.append(("Its folder", "is there" if folder and os.path.isdir(
        folder) else "is not there either"))
    title = gone.entry.get("title")
    if title:
        facts.append(("Named", str(title)))
    facts.append(("Curves kept", str(len(gone.scans))))
    facts.append(("Analyses kept", str(gone.analysis_count())))
    facts.append(("Labels kept", str(len(gone.labels))))
    return facts


def _read_entry(entry, session_path, read_sample, notes, relocated,
                found=None):
    """One of a session's files: where the user found it (`found`, by the
    saved path's `normcase`); else where it was; else beside the session;
    else the copy inside it. Raises with the reader's reason when none of
    them is there. A file read from elsewhere keeps the path the session
    knew it by until the opening is done (everything is matched by it),
    then takes its new one (`relocated`)."""
    path = entry["path"]
    shown = os.path.basename(str(path).replace("\\", "/"))
    chosen = (found or {}).get(os.path.normcase(str(path)))
    if chosen:
        sample = read_sample(chosen)
        sample.path = path
        relocated.append((sample, chosen))
        return sample
    if os.path.isfile(path):
        return read_sample(path)
    beside = _look_beside(session_path, path)
    if beside is not None:
        sample = read_sample(beside)
        sample.path = path
        relocated.append((sample, beside))
        notes.append("{}: moved - found beside the session".format(shown))
        return sample
    copy = entry.get("copy")
    if copy:
        data = zlib.decompress(base64.b64decode(copy))
        folder = tempfile.mkdtemp(prefix="panel-copy-")
        try:
            temp = os.path.join(folder, shown)
            with open(temp, "wb") as fh:
                fh.write(data)
            sample = read_sample(temp)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        sample.path = path
        sample.source_bytes = data
        sample.from_copy = True
        notes.append("{}: not where it was - read from the copy inside "
                     "the session".format(shown))
        return sample
    return read_sample(path)


def _kept_missing(doc, gone_at, problems, entry, place, exc):
    """A file that could not be read, kept for the next save and for the
    finding (`model.MissingSource`); the opening says so."""
    gone = model.MissingSource(entry.get("path", "?"), entry, exc)
    gone.index = place
    if gone.found_nowhere:
        gone.reason = "not found"
    doc.missing.append(gone)
    gone_at[os.path.normcase(gone.path)] = gone
    problems.append("{}: {} - kept in the outliner (right-click it to look "
                    "for it)".format(gone.name, gone.reason))
    return gone


def _splice(state, key, extra):
    """`extra` - `[(place, entry), ...]` - back into the list `state[key]`
    at their places; returns where each entry already there went."""
    items = list(state.get(key) or [])
    slots = [(False, k) for k in range(len(items))]
    for place, entry in sorted(extra, key=lambda pair: pair[0]):
        slots.insert(min(place, len(slots)), (True, entry))
    moved, out = {}, []
    for new, (kept, what) in enumerate(slots):
        if kept:
            out.append(what)
        else:
            moved[what] = new
            out.append(items[what])
    state[key] = out
    return moved


def _keep_missing(doc, state):
    """What the session held of its missing files (`doc.missing`) put back
    into `state` where it was - their entries, their curves', the labels
    hanging from them - and the colour links of everything else moved to
    match. A link to or from a missing file's object is not kept (its
    colour is)."""
    gone = sorted(doc.missing, key=lambda item: item.index)
    if not gone:
        return state
    for item in gone:
        state["samples"].insert(min(item.index, len(state["samples"])),
                                dict(item.entry))
    before = [len(entry.get("analyses") or ()) for entry in state["scans"]]
    scan_moves = _splice(state, "scans",
                         [pair for item in gone for pair in item.scans])
    label_moves = _splice(state, "labels",
                          [pair for item in gone for pair in item.labels])
    starts, count = [], 0
    for entry in state["scans"]:
        starts.append(count)
        count += len(entry.get("analyses") or ())
    old = []
    for index, many in enumerate(before):
        old.extend((index, k) for k in range(many))

    def moved(ref):
        kind, index = ref
        if kind in ("scans", "markers"):
            return [kind, scan_moves[int(index)]]
        if kind == "labels":
            return [kind, label_moves[int(index)]]
        if kind == "analyses":
            index, k = old[int(index)]
            return [kind, starts[scan_moves[index]] + k]
        return ref

    links = []
    for pair in state.get("colour_links") or ():
        try:
            links.append([moved(pair[0]), moved(pair[1])])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    state["colour_links"] = links
    # A span's ends name labels by their places; a region its curves by
    # their files', a missing one's kept aside (`kept_scans`).
    for span in state.get("spans") or ():
        span["ends"] = [label_moves.get(end) if isinstance(end, int) else end
                        for end in span.get("ends") or ()]
    still = set(os.path.normcase(item.path) for item in gone)
    for region, entry in zip(getattr(doc, "regions", None) or (),
                             state.get("regions") or ()):
        entry["scans"] = list(entry.get("scans") or ()) + [
            path for path in getattr(region, "kept_scans", ())
            if os.path.normcase(path) in still]
    return state


def _saved_target(doc, state, scan_at, label_at, made_at):
    """What a session's colour link - `[list, position]` - names among what
    was opened. Curves, their markers and analyses and the labels go by
    their places IN THE FILE (`scan_at`, `label_at`, `made_at`), which a
    missing file or an analysis not measured again would shift."""
    flat = [(place, k) for place, entry in enumerate(state.get("scans")
                                                     or ())
            for k in range(len(entry.get("analyses") or ()))]

    def target(ref):
        try:
            kind, index = ref
            if kind in ("scans", "markers"):
                scan = scan_at.get(int(index))
                if scan is None or kind == "scans":
                    return scan
                return getattr(scan, "marker", None)
            if kind == "labels":
                return label_at.get(int(index))
            if kind == "analyses":
                k = int(index)
                return made_at.get(flat[k]) if 0 <= k < len(flat) else None
        except (TypeError, ValueError):
            return None
        return model._colour_target(doc, ref)

    return target


def to_state(doc):
    """The document as plain data, ready for `json.dump`."""
    samples = [{"path": sample.path, "title": sample.title,
                "wavelength": sample.wavelength_override,
                "sim_range": (list(sample.sim_range) if sample.sim_range
                              else None),
                "sim_fwhm": sample.sim_fwhm}
               for sample in doc.samples]
    for entry, sample in zip(samples, doc.samples):
        copy = _source_copy(sample)
        if copy:
            entry["copy"] = copy
    scans = []
    for scan in doc.scans:
        marker = scan.marker
        scans.append({
            "path": scan.sample.path,
            "colour": scan.colour,
            "offset": scan.offset,
            "line_width": scan.line_width,
            "keep": list(scan.keep),
            "label": scan.label,
            "visible": scan.visible,
            "z": scan.z,
            "draw_as": scan.draw_as,
            "strongest": scan.strongest,
            "multiplier": scan.multiplier,
            "analyses": [_analysis_state(a) for a in scan.analysis_objects],
            "marker": {"z": marker.z,
                       "at": list(marker.at) if marker.at else None,
                       "dy": marker.dy,
                       "number_format": marker.number_format,
                       "size": marker.size,
                       "colour": marker.colour,
                       "visible": marker.visible},
        })
    axes = {}
    for which, axis in doc.axes.items():
        axes[which] = {"label": axis.label, "show_grid": axis.show_grid,
                       "minor_ticks": axis.minor_ticks,
                       "ticks_inward": axis.ticks_inward,
                       "label_size": axis.label_size,
                       "tick_size": axis.tick_size,
                       "label_along": axis.label_along,
                       "number_format": axis.number_format,
                       "mirror": axis.mirror,
                       "mirror_ticks": axis.mirror_ticks,
                       "major_step": axis.major_step,
                       "minor_count": axis.minor_count,
                       "tick_length": axis.tick_length,
                       "minor_length": axis.minor_length,
                       "side": axis.side,
                       "show_numbers": axis.show_numbers,
                       "show_ticks": axis.show_ticks,
                       "visible": axis.visible,
                       "label_gap": axis.label_gap,
                       "lock": list(axis.lock) if axis.lock else None,
                       "lock_context": (list(axis.lock_context)
                                        if axis.lock_context else None),
                       "hidden_numbers": [float(v) for v in
                                          axis.hidden_numbers or ()],
                       "hidden_context": (list(axis.hidden_context)
                                          if axis.hidden_numbers
                                          and axis.hidden_context else None)}
    saved_labels = []
    for lb in doc.labels:
        saved_labels.append({
            "text": lb.text, "x": lb.x, "y": lb.y, "colour": lb.colour,
            "size": lb.size, "bold": lb.bold, "visible": lb.visible,
            "space": lb.space, "anchor": lb.anchor, "rotation": lb.rotation,
            "z": lb.z,
            "scan": None if lb.scan is None else lb.scan.sample.path,
            "parent_offset": lb.parent_offset,
            "at": list(lb.at) if lb.at else None,
            "dx": lb.dx, "dy": lb.dy, "leader": lb.leader,
            "leader_from": lb.leader_from,
            "leader_colour": lb.leader_colour, "flush": lb.flush,
            "vline": lb.vline, "line_dashed": lb.line_dashed,
            "shows": lb.shows})
    regions = [{"lo": r.lo, "hi": r.hi, "shade": r.shade,
                "opacity": r.opacity, "factor": r.factor,
                "scans": [s.sample.path for s in r.scans],
                "text": r.text, "x": r.x, "y": r.y, "colour": r.colour,
                "size": r.size, "rotation": r.rotation, "anchor": r.anchor,
                "z": r.z, "visible": r.visible}
               for r in doc.regions]
    spans = []
    for span in doc.spans:
        ends = [doc.labels.index(end) if end in doc.labels else None
                for end in span.ends]
        spans.append({"x0": span.x0, "x1": span.x1, "ends": ends,
                      "y": span.y, "text": span.text, "place": span.place,
                      "colour": span.colour, "size": span.size,
                      "head": span.head, "line_width": span.line_width,
                      "number_format": span.number_format, "z": span.z,
                      "visible": span.visible})
    state = {
        "format": FORMAT,
        "version": VERSION,
        "axes": axes,
        "labels": saved_labels,
        "regions": regions,
        "spans": spans,
        "structures": [{"smiles": m.smiles, "atoms": m.atoms,
                        "bonds": m.bonds, "x": m.x, "y": m.y,
                        "space": m.space, "anchor": m.anchor,
                        "rotation": m.rotation, "z": m.z,
                        "visible": m.visible, "colour": m.colour,
                        "bond_length": m.bond_length,
                        "bond_width": m.bond_width,
                        "label_size": m.label_size,
                        "upright_labels": m.upright_labels,
                        "label_font": m.label_font,
                        "colour_by_element": m.colour_by_element}
                       for m in doc.structures],
        "images": [{"png": im.png, "x": im.x, "y": im.y,
                    "space": im.space, "anchor": im.anchor,
                    "rotation": im.rotation, "width": im.width,
                    "z": im.z, "visible": im.visible,
                    "mirror_h": im.mirror_h, "mirror_v": im.mirror_v}
                   for im in doc.images],
        "x_quantity": doc.x_quantity,
        "y_unit": doc.y_unit,
        "norm": doc.norm,
        "norm_band": list(doc.norm_band) if doc.norm_band else None,
        "x_break": dict(doc.x_break) if doc.x_break else None,
        "theme": doc.theme,
        "background": doc.background,
        "follow_zoom": bool(doc.follow_zoom),
        "offset_markers": bool(doc.offset_markers),
        "view": view_to_state(doc.view),
        "style": doc.style.chosen(),
        "figure": doc.figure.to_state(),
        "figure_grown": dict(doc.figure.grown),
        "legend": {"visible": doc.legend.visible, "size": doc.legend.size,
                   "show_frame": doc.legend.show_frame,
                   "sample": doc.legend.sample,
                   "spacing": doc.legend.spacing,
                   "colour": doc.legend.colour, "x": doc.legend.x,
                   "rotation": doc.legend.rotation, "z": doc.legend.z,
                   "line_width": doc.legend.line_width,
                   "y": doc.legend.y, "space": doc.legend.space,
                   "anchor": doc.legend.anchor},
        "samples": samples,
        "scans": scans,
        "colour_links": model.colour_links(doc),
    }
    return _keep_missing(doc, state)


def save(doc, path):
    """Write the session. The document remembers where it went."""
    state = to_state(doc)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    doc.path = str(path)
    return path


def load(path, read_sample, found=None):
    """Rebuild a document from a session file.

    Returns `(document, problems)`. `problems` names every file that could
    not be read, and every analysis that could not be measured again, so the
    window can say so in one line and still show everything else.
    `found`: see `from_state`.
    """
    with open(path, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    return from_state(state, path, read_sample, found)


def from_state(state, path, read_sample, found=None):
    """Rebuild a document from a session's `state`, saved at `path` (where
    a moved file is looked for). `found` maps a path the session names to
    where that file is now - a file the user found. A file that cannot be
    read is kept as it was (`doc.missing`). Returns `(document,
    problems)`."""
    if state.get("format") != FORMAT:
        raise ValueError("not a {} file".format(FORMAT))
    found = dict((os.path.normcase(str(saved)), str(now))
                 for saved, now in (found or {}).items() if now)
    doc = model.Document()
    doc.loaded_version = int(state.get("version", 1) or 1)
    problems = []
    by_path = {}
    relocated = []
    gone_at = {}
    # Where each curve, analysis and label of the FILE went (a colour link
    # names them by their places there).
    scan_at, made_at, label_at = {}, {}, {}
    for key, raw in (state.get("style") or {}).items():
        if key in style.BY_KEY and style.BY_KEY[key].figure:
            setattr(doc.style, key, style.BY_KEY[key].clean(raw))
    doc.figure = figure_module.FigureLayout().load_state(
        state.get("figure") or {"mode": figure_module.MODE_WINDOW})
    doc.figure.grown = figure_module.clean_grown(state.get("figure_grown"))
    if state.get("x_quantity") in units.QUANTITIES:
        doc.x_quantity = state["x_quantity"]
    if state.get("y_unit") in units.UNITS:
        doc.y_unit = state["y_unit"]
    if state.get("norm") in units.NORMS:
        doc.norm = state["norm"]
    band = state.get("norm_band")
    try:
        doc.norm_band = ([float(band[0]), float(band[1])] if band else None)
    except (TypeError, ValueError, IndexError):
        doc.norm_band = None
    cut = state.get("x_break")
    doc.x_break = (dict(cut) if isinstance(cut, dict) else None)
    if doc.x_break is not None and model.break_of(doc) is None:
        doc.x_break = None
    for place, entry in enumerate(state.get("samples", [])):
        try:
            sample = _read_entry(entry, path, read_sample, problems,
                                 relocated, found)
        except Exception as exc:
            _kept_missing(doc, gone_at, problems, entry, place, exc)
            continue
        title = entry.get("title")
        sample.title = str(title) if title else None
        sample.wavelength_override = _wavelength(entry.get("wavelength"))
        if sample.simulated and sample.wavelength_override is None:
            sample.wavelength_override = crystal.DEFAULT_WAVELENGTH
        sample.sim_range = _pair(entry.get("sim_range"))
        fwhm = _number_or_none(entry.get("sim_fwhm"))
        if fwhm and fwhm > 0:
            sample.sim_fwhm = fwhm
        doc.samples.append(sample)
        by_path[os.path.normcase(sample.path)] = sample
    scan_by_path = {}
    for place, entry in enumerate(state.get("scans", [])):
        key = os.path.normcase(entry.get("path", ""))
        if key in gone_at:
            gone_at[key].scans.append((place, entry))
            continue
        sample = by_path.get(key)
        if sample is None or sample.scans:
            continue
        scan = model.Scan(doc._next_id(), sample,
                          entry.get("colour")
                          or model.PALETTE[len(doc.scans)
                                           % len(model.PALETTE)], doc)
        if entry.get("draw_as") in crystal.DRAWS:
            scan.draw_as = entry["draw_as"]
        multiplier = _number_or_none(entry.get("multiplier"))
        scan.multiplier = (multiplier if multiplier and multiplier > 0
                           else 1.0)
        strongest = _number_or_none(entry.get("strongest"))
        scan.strongest = (int(strongest) if strongest is not None
                          and strongest >= 0 else None)
        scan.offset = float(_number_or_none(entry.get("offset")) or 0.0)
        scan.line_width = _chosen(entry.get("line_width"), "line_width")
        keep = entry.get("keep") or (0.0, 1.0)
        try:
            start, end = float(keep[0]), float(keep[1])
        except (TypeError, ValueError, IndexError):
            start, end = 0.0, 1.0
        if 0.0 <= start < end <= 1.0:
            scan.keep = (start, end)
        scan.label = entry.get("label")
        scan.visible = bool(entry.get("visible", True))
        scan.z = _number_or_none(entry.get("z"))
        sample.scans.append(scan)
        doc.scans.append(scan)
        scan_by_path[os.path.normcase(sample.path)] = scan
        scan_at[place] = scan
        for k, saved in enumerate(entry.get("analyses") or []):
            cursors = saved.get("cursors") or []
            made = None
            axis = saved.get("axis")
            if len(cursors) == 2:
                made = measure.run(saved.get("model", ""), scan,
                                   float(cursors[0]), float(cursors[1]),
                                   span=saved.get("span"),
                                   quantity=axis if axis in units.QUANTITIES
                                   else units.TWO_THETA)
            if made is None:
                problems.append("{}: {} could not be measured again".format(
                    scan.display_name(), saved.get("model", "an analysis")))
                continue
            _restore_analysis(made, saved)
            made_at[(place, k)] = made
        marker = entry.get("marker") or {}
        scan.marker.at = _marker_at(marker.get("at"))
        scan.marker.number_format = labels.normalise_format(
            marker.get("number_format"))
        scan.marker.dy = _number_or_none(marker.get("dy"))
        scan.marker.size = _chosen(marker.get("size"), "offset_marker_size")
        scan.marker.colour = marker.get("colour", "auto")
        scan.marker.visible = bool(marker.get("visible", True))
        scan.marker.z = _number_or_none(marker.get("z"))
    # A file that was open with no scan (every one comes with its scan, but
    # a hand-edited file may say otherwise) gets one.
    for sample in doc.samples:
        if not sample.scans:
            scan = model.Scan(doc._next_id(), sample, model.PALETTE[
                len(doc.scans) % len(model.PALETTE)], doc)
            sample.scans.append(scan)
            doc.scans.append(scan)
    doc.scans.sort(key=doc.outliner_key)
    for which, saved in (state.get("axes") or {}).items():
        axis = doc.axes.get(which)
        if axis is None or not isinstance(saved, dict):
            continue
        _restore_axis(axis, which, saved)
    for place, saved in enumerate(state.get("labels") or []):
        if (saved.get("scan") and os.path.normcase(str(saved.get("scan")))
                in gone_at):
            # Hanging from a missing file's curve: kept with the file.
            gone_at[os.path.normcase(str(saved.get("scan")))].labels.append(
                (place, saved))
            continue
        owner = scan_by_path.get(os.path.normcase(str(saved.get("scan"))))\
            if saved.get("scan") else None
        label = doc.add_label(saved.get("text", "Label"),
                              float(saved.get("x", 0.5)),
                              float(saved.get("y", 0.5)), owner)
        label.colour = saved.get("colour", "auto")
        label.size = _number_or_none(saved.get("size"))
        label.bold = bool(saved.get("bold", False))
        label.visible = bool(saved.get("visible", True))
        label.space = saved.get("space", label.space)
        label.anchor = saved.get("anchor", label.anchor)
        label.rotation = float(_number_or_none(saved.get("rotation")) or 0.0)
        label.z = _number_or_none(saved.get("z"))
        followed = _number_or_none(saved.get("parent_offset"))
        if owner is not None and followed is not None:
            label.parent_offset = followed
        leader = saved.get("leader")
        if (isinstance(leader, (list, tuple)) and len(leader) == 2
                and all(_number_or_none(v) is not None for v in leader)):
            label.leader = [float(leader[0]), float(leader[1])]
        start = saved.get("leader_from", "auto")
        label.leader_from = start if start in model.ANCHORS else "auto"
        label.shows = ("multiplier" if saved.get("shows") == "multiplier"
                       and owner is not None else None)
        label.leader_colour = saved.get("leader_colour") or "auto"
        flush = saved.get("flush")
        label.flush = flush if flush in ("left", "right", "center") else None
        label.vline = _number_or_none(saved.get("vline"))
        at = saved.get("at")
        if (owner is not None and isinstance(at, (list, tuple))
                and len(at) == 2 and at[0] == "i"
                and _number_or_none(at[1]) is not None):
            label.at = ("i", int(at[1]))
            label.dx = float(_number_or_none(saved.get("dx")) or 0.0)
            label.dy = _number_or_none(saved.get("dy"))
        label.line_dashed = bool(saved.get("line_dashed", True))
        label_at[place] = label
    for saved in state.get("regions") or []:
        lo, hi = (_number_or_none(saved.get("lo")),
                  _number_or_none(saved.get("hi")))
        if lo is None or hi is None or lo == hi:
            continue
        region = model.Region(doc._next_id(), lo, hi,
                              float(saved.get("x", 0.5)),
                              float(saved.get("y", 0.05)))
        region.shade = bool(saved.get("shade", True))
        opacity = _number_or_none(saved.get("opacity"))
        if opacity is not None:
            region.opacity = min(1.0, max(0.0, opacity))
        factor = _number_or_none(saved.get("factor"))
        if factor is not None and factor > 0:
            region.factor = factor
        region.scans = [scan_by_path[os.path.normcase(str(p))]
                        for p in saved.get("scans") or ()
                        if os.path.normcase(str(p)) in scan_by_path]
        # A missing file's curve, for when it is found.
        region.kept_scans = [str(p) for p in saved.get("scans") or ()
                             if os.path.normcase(str(p)) in gone_at]
        region.text = str(saved.get("text") or "")
        region.colour = saved.get("colour", "auto")
        region.size = _number_or_none(saved.get("size"))
        region.rotation = float(_number_or_none(saved.get("rotation"))
                                or 0.0)
        region.anchor = saved.get("anchor", region.anchor)
        region.z = _number_or_none(saved.get("z"))
        region.visible = bool(saved.get("visible", True))
        doc.regions.append(region)
    for saved in state.get("spans") or []:
        x0, x1 = (_number_or_none(saved.get("x0")),
                  _number_or_none(saved.get("x1")))
        if x0 is None or x1 is None:
            continue
        span = model.SpanArrow(doc._next_id(), x0, x1,
                               float(saved.get("y", 0.3)))
        ends = list(saved.get("ends") or [None, None])[:2]
        span.ends = [label_at.get(i) if isinstance(i, int)
                     and getattr(label_at.get(i), "vline", None) is not None
                     else None for i in ends + [None] * (2 - len(ends))]
        text = saved.get("text")
        span.text = str(text) if text is not None else None
        if saved.get("place") in model.SpanArrow.PLACES:
            span.place = saved["place"]
        span.colour = saved.get("colour", "auto")
        span.size = _number_or_none(saved.get("size"))
        for name in ("head", "line_width"):
            value = _number_or_none(saved.get(name))
            if value is not None and value > 0:
                setattr(span, name, value)
        span.number_format = labels.normalise_format(
            saved.get("number_format"))
        span.z = _number_or_none(saved.get("z"))
        span.visible = bool(saved.get("visible", True))
        doc.spans.append(span)
    for saved in state.get("structures") or []:
        if not saved.get("atoms"):
            continue
        structure = model.MoleculeArtist(
            doc._next_id(), saved.get("smiles", ""),
            {"atoms": saved["atoms"], "bonds": saved.get("bonds", [])},
            float(saved.get("x", 0.5)), float(saved.get("y", 0.5)))
        for name in ("space", "anchor", "colour"):
            if saved.get(name) is not None:
                setattr(structure, name, saved[name])
        for name in ("rotation", "bond_length", "bond_width", "label_size"):
            value = _number_or_none(saved.get(name))
            if value is not None:
                setattr(structure, name, value)
        structure.z = _number_or_none(saved.get("z"))
        structure.visible = bool(saved.get("visible", True))
        structure.upright_labels = bool(saved.get("upright_labels", True))
        structure.label_font = saved.get("label_font") or None
        structure.colour_by_element = bool(saved.get("colour_by_element",
                                                     True))
        doc.structures.append(structure)
    for saved in state.get("images") or []:
        if not saved.get("png"):
            continue
        image = model.ImageArtist(doc._next_id(), saved["png"],
                                  float(saved.get("x", 0.5)),
                                  float(saved.get("y", 0.5)),
                                  float(saved.get("width", 160.0)))
        image.space = saved.get("space", image.space)
        image.anchor = saved.get("anchor", image.anchor)
        image.rotation = float(_number_or_none(saved.get("rotation")) or 0.0)
        image.z = _number_or_none(saved.get("z"))
        image.visible = bool(saved.get("visible", True))
        image.mirror_h = bool(saved.get("mirror_h", False))
        image.mirror_v = bool(saved.get("mirror_v", False))
        doc.images.append(image)
    legend = state.get("legend") or {}
    for name, value in legend.items():
        if hasattr(doc.legend, name):
            setattr(doc.legend, name, value)
    doc.legend.rotation = float(_number_or_none(legend.get("rotation"))
                                or 0.0)
    doc.legend.z = _number_or_none(legend.get("z"))
    doc.legend.size = _chosen(legend.get("size"), "legend_size")
    doc.theme = state.get("theme", doc.theme)
    background = state.get("background")
    doc.follow_zoom = bool(state.get("follow_zoom", False))
    doc.background = (str(background) if isinstance(background, str)
                      and background.startswith("#") else None)
    doc.offset_markers = bool(state.get("offset_markers", False))
    doc.view = _view_from(state.get("view"))
    model.restore_colour_links(
        doc, state.get("colour_links"),
        _saved_target(doc, state, scan_at, label_at, made_at))
    for sample, found in relocated:
        sample.path = found
    doc.path = str(path)
    return doc, problems


def _restore_axis(axis, which, saved):
    for name, value in saved.items():
        if hasattr(axis, name) and name not in ("which",):
            setattr(axis, name, value)
    axis.number_format = numbers.normalise(saved.get("number_format"))
    step = _number_or_none(saved.get("major_step"))
    axis.major_step = step if step and step > 0 else None
    try:
        axis.minor_count = max(1, int(saved.get("minor_count", 5)))
    except (TypeError, ValueError):
        axis.minor_count = 5
    for name, default in (("tick_length", 7.0), ("minor_length", 3.0)):
        value = _number_or_none(saved.get(name))
        setattr(axis, name, value if value is not None and value >= 0
                else default)
    axis.mirror = bool(saved.get("mirror", True))
    axis.mirror_ticks = bool(saved.get("mirror_ticks", True))
    axis.show_numbers = bool(saved.get("show_numbers", which == "x"))
    axis.show_ticks = bool(saved.get("show_ticks", which == "x"))
    axis.label_size = _chosen(saved.get("label_size"), "caption_size")
    axis.label_gap = _chosen(saved.get("label_gap"), "caption_gap")
    if saved.get("side") not in (("bottom", "top") if which == "x"
                                 else ("left", "right")):
        axis.side = "bottom" if which == "x" else "left"
    axis.tick_size = _chosen(saved.get("tick_size"), "tick_size")
    lock = saved.get("lock")
    try:
        lock = [float(lock[0]), float(lock[1])] if lock else None
    except (TypeError, ValueError, IndexError):
        lock = None
    axis.lock = lock if lock and lock[1] > lock[0] else None
    context = saved.get("lock_context")
    axis.lock_context = (list(context) if axis.lock and
                         isinstance(context, list) else None)
    hidden = []
    for value in saved.get("hidden_numbers") or ():
        value = _number_or_none(value)
        if value is not None and value == value and abs(value) != \
                float("inf"):
            hidden.append(value)
    context = saved.get("hidden_context")
    axis.hidden_context = (list(context) if hidden
                           and isinstance(context, list) else None)
    axis.hidden_numbers = hidden if axis.hidden_context else []
