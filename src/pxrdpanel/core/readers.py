"""Reading powder patterns: one file, one pattern.

UI-free. Every reader returns the same thing - a `Pattern`: the angles
(2-theta in degrees, ascending), the intensities as stored, the WAVELENGTH
the file states (or None), and whatever else it says about the
measurement. Measured patterns:

* **Bruker `.raw`**, RAW1.01 and RAW4.00, read from the bytes:

  - RAW1.01: a 712-byte file header, then one range header per range. In
    the file header: the date at 16, the time at 26, the sample id at 326
    (text); the anode at 608 (4 characters), and as float64 the K-alpha
    average at 616, K-alpha1 at 624, K-alpha2 at 632, K-beta at 640 and the
    alpha2 : alpha1 ratio at 648. In the range header: its length at +0
    and the step count at +4 (int32), the 2-theta start at +16 (float64;
    +8 is THETA, half of it - a pattern read from there looks plausible
    with every peak at half its angle) and the step at +176; float32
    intensities after it.
  - RAW4.00: length-prefixed records. Metadata from byte 61 (its length at
    56); type 10 is key / value (the key at +12, 24 bytes, the value from
    +36), type 30 the source, with float64 K-alpha average at +72, K-alpha1
    at +80, K-alpha2 at +88, K-beta at +96 and the ratio at +104. A range
    is a 160-byte header (scan type at +32, start +72, step +80, point
    count +88, bytes per point +136, sub-record length +140), sub-records
    (type 50 an axis: its name at +12, its start at +56), then the
    float32 intensities. The 2-theta start is taken from the "2Theta" axis
    record, never from the driving axis's.

* **Bruker `.brml`**: a zip; `Experiment0/RawData0.xml` holds one
  `<Datum>` per step, `time,1,2theta,theta,intensity`, and the tube's
  `WaveLengthAlpha1`, `WaveLengthAlpha2`, `WaveLengthRatio`.
* **Riet7 `.dat`**: a header with `Alpha1 ... Alpha2 ... Ratio ...` and a
  line `start step stop MeasureDateTime ...`, then the intensities. The
  header rounds start and step to three decimals, so its angles are only
  as good as that; the intensities are exact. A `.dat` that is not Riet7
  is read as columns.
* **Columns** (`.xy`, `.xye`, `.xys`, `.txt`, `.csv`, `.asc`, `.dat`):
  2-theta and intensity first, anything after (an esd) ignored; `;`, tab,
  `,` or spaces between them, decimal points or commas, header lines
  skipped. A column file states no wavelength.

These were checked against one another on real measurements: a RAW1.01
against the Riet7 `.dat` exported from it, a RAW4.00 against the `.brml`
it was converted from - angles, every intensity and the wavelengths
(`tests/test_readers.py`).

And two kinds of file that hold reflections rather than a measurement,
SIMULATED into a pattern by `core/crystal.py` at the wavelength the figure
asks for:

* **`.cif`**: a crystal structure (the cell, the symmetry, the atoms).
* **ICDD PDF card `.xml`**: a reflection list - `<stick_series>` of
  `<intensity>` blocks, each with `theta` (2-theta at the card's
  `<lambda>`), `da` (d in angstrom), `intensity` (relative; may carry a
  letter, "7m") and `h`, `k`, `l`. The d values are what is used: they do
  not depend on any wavelength.

**The wavelength is read, never assumed.** A file that does not state it
(a column file, a pattern exported without its tube) has None, and the
figure says so wherever a d or a Q would need it; the user can give it.
"""

import os
import re
import struct
import zipfile

import numpy as np

#: The file types read, by extension.
RAW = (".raw",)
BRML = (".brml",)
DAT = (".dat",)
TEXT = (".xy", ".xye", ".xys", ".txt", ".csv", ".asc")
CIF = (".cif",)
CARD = (".xml",)
MEASURED = RAW + BRML + DAT + TEXT
SIMULATED = CIF + CARD
READABLE = MEASURED + SIMULATED

#: What the open dialog offers.
FILTER = ("Powder patterns (*.raw *.brml *.dat *.xy *.xye *.xys *.txt *.csv"
          " *.asc *.cif *.xml);;"
          "Measured patterns (*.raw *.brml *.dat *.xy *.xye *.xys *.txt"
          " *.csv *.asc);;"
          "Structures and cards (*.cif *.xml);;"
          "All files (*)")


class ReadError(Exception):
    """A file that cannot be read as a pattern, with the reason."""


class Source(object):
    """The radiation a file states: K-alpha1 (what d and Q are taken from),
    K-alpha2 and their ratio where it gives them, and the anode."""

    def __init__(self, alpha1, alpha2=None, ratio=None, anode="",
                 average=None):
        self.alpha1 = float(alpha1)
        self.alpha2 = float(alpha2) if alpha2 else None
        self.ratio = float(ratio) if ratio else None
        self.anode = str(anode or "").strip()
        self.average = float(average) if average else None

    def describe(self):
        """"Cu K-alpha1 1.5406 A" - what the file says, in words."""
        words = "{} K-alpha1".format(self.anode) if self.anode else \
            "K-alpha1"
        return "{} {:.5g} A".format(words, self.alpha1)


def _source(alpha1, alpha2=None, ratio=None, anode="", average=None):
    """A `Source`, or None when the file's K-alpha1 is not a wavelength (a
    zero where an export left the tube out)."""
    try:
        alpha1 = float(alpha1)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(alpha1) and 0.01 < alpha1 < 20.0):
        return None
    return Source(alpha1, alpha2, ratio, anode, average)


class Pattern(object):
    """What a reader found in a file.

    A MEASURED pattern: `x` is 2-theta in degrees, ASCENDING (a reader turns
    a descending file round), `y` the intensities as stored. `source` is
    the radiation the file states (`Source`), or None. `head` is what else
    it says: `title`, `date`, `note`, as text.

    A SIMULATED one (`simulated` true) has no `x` and `y` of its own:
    `phase` (a CIF's crystal) or `reflections` (a card's `[(d, intensity,
    hkl), ...]`) are what `core/crystal.py` makes a pattern from.
    """

    def __init__(self, x=None, y=None, source=None, head=None, kind="",
                 phase=None, reflections=None):
        self.simulated = phase is not None or reflections is not None
        if not self.simulated:
            x = np.asarray(x, dtype=float)
            y = np.asarray(y, dtype=float)
            if len(x) != len(y):
                raise ReadError("{} angles for {} intensities".format(
                    len(x), len(y)))
            if len(x) < 2:
                raise ReadError("fewer than two points")
            if x[0] > x[-1]:
                x, y = x[::-1].copy(), y[::-1].copy()
        self.x = x
        self.y = y
        self.source = source
        self.head = dict(head or {})
        #: Which reader made it: "raw1", "raw4", "brml", "riet7", "text",
        #: "cif", "card".
        self.kind = kind
        self.phase = phase
        self.reflections = list(reflections) if reflections is not None \
            else None

    @property
    def wavelength(self):
        """K-alpha1 in angstrom as the file states it, or None."""
        return self.source.alpha1 if self.source is not None else None


def read(path):
    """The pattern in `path`, by its extension. Raises `ReadError`."""
    path = str(path)
    if not os.path.isfile(path):
        raise ReadError("no such file: {}".format(os.path.basename(path)))
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in RAW:
            return read_raw(path)
        if ext in BRML:
            return read_brml(path)
        if ext in DAT:
            return read_dat(path)
        if ext in TEXT:
            return read_text(path)
        if ext in CIF:
            return read_cif(path)
        if ext in CARD:
            return read_card(path)
    except ReadError:
        raise
    except (OSError, ValueError, struct.error, IndexError, KeyError,
            zipfile.BadZipFile) as exc:
        raise ReadError("{}: {}".format(type(exc).__name__, exc))
    raise ReadError("not a pattern this program reads: {}".format(
        os.path.basename(path)))


# ------------------------------------------------------------ Bruker .raw
RAW1_HEADER = 712
_RAW1_TEXT = {"date": (16, 10), "time": (26, 10), "title": (326, 60)}
_RAW1_ANODE = 608
_RAW1_AVERAGE, _RAW1_ALPHA1, _RAW1_ALPHA2, _RAW1_RATIO = 616, 624, 632, 648
_RH_NSTEPS, _RH_START_2THETA, _RH_STEP = 4, 16, 176


def read_raw(path):
    """A Bruker `.raw`: RAW1.01 (or 1.02) or RAW4.00, by its first bytes."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:7] == b"RAW4.00":
        return read_raw4(raw)
    if raw[:7] not in (b"RAW1.01", b"RAW1.02"):
        raise ReadError("a Bruker RAW file of a kind not read here (it "
                        "starts {!r}); RAW1.01 and RAW4.00 are read - "
                        "convert others with PowDLL or TOPAS".format(
                            raw[:7]))
    return read_raw1(raw)


def _text_at(raw, offset, length):
    return raw[offset:offset + length].split(b"\0")[0].decode(
        "latin-1").strip()


def read_raw1(raw):
    """RAW1.01 from its bytes (the layout is in the module's docstring)."""
    n = len(raw)
    if n < RAW1_HEADER + 184:
        raise ReadError("the file ends before its first range")
    ranges = struct.unpack_from("<i", raw, 12)[0]
    pos = RAW1_HEADER
    header_len = struct.unpack_from("<i", raw, pos)[0]
    steps = struct.unpack_from("<i", raw, pos + _RH_NSTEPS)[0]
    start = struct.unpack_from("<d", raw, pos + _RH_START_2THETA)[0]
    step = struct.unpack_from("<d", raw, pos + _RH_STEP)[0]
    if not (0 < steps < 10 ** 7) or not (0 < header_len < n):
        raise ReadError("an implausible range header ({} steps, header "
                        "{} bytes)".format(steps, header_len))
    if not (0 < step < 100):
        raise ReadError("an implausible step ({:g})".format(step))
    data = pos + header_len
    if data + steps * 4 > n:
        raise ReadError("the data run past the end of the file")
    y = np.frombuffer(raw, dtype="<f4", count=steps, offset=data).astype(
        float)
    x = start + step * np.arange(steps)
    head = {}
    for key, (offset, length) in _RAW1_TEXT.items():
        text = _text_at(raw, offset, length)
        if text:
            head[key] = text
    if ranges > 1:
        head["note"] = "{} ranges in the file; the first is read".format(
            ranges)
    f64 = lambda at: struct.unpack_from("<d", raw, at)[0]  # noqa: E731
    source = _source(f64(_RAW1_ALPHA1), f64(_RAW1_ALPHA2), f64(_RAW1_RATIO),
                     _text_at(raw, _RAW1_ANODE, 4), f64(_RAW1_AVERAGE))
    return Pattern(x, y, source, head, "raw1")


RAW4_HEADER = 61
_R4_META_LENGTH = 56
RAW4_RANGE_HEADER = 160
_R4_SCAN_TYPE, _R4_START, _R4_STEP, _R4_NPOINTS = 32, 72, 80, 88
_R4_DATUM, _R4_EXTRA = 136, 140
_R4_KEYVALUE, _R4_SOURCE, _R4_AXIS = 10, 30, 50
_R4_AVERAGE, _R4_ALPHA1, _R4_ALPHA2, _R4_RATIO = 72, 80, 88, 104
_R4_COUPLED = "Locked Coupled"


def _records(raw, start, end):
    """`[(type, offset, length), ...]` of the length-prefixed records
    between `start` and `end`; stops at one that does not fit."""
    out = []
    pos = start
    while pos + 8 <= end:
        kind, length = struct.unpack_from("<II", raw, pos)
        if length < 8 or pos + length > end:
            break
        out.append((kind, pos, length))
        pos += length
    return out


def read_raw4(raw):
    """RAW4.00 from its bytes: the first range (the layout is in the
    module's docstring)."""
    if len(raw) < RAW4_HEADER:
        raise ReadError("the file ends inside its header")
    meta_end = RAW4_HEADER + struct.unpack_from("<I", raw, _R4_META_LENGTH)[0]
    if meta_end + RAW4_RANGE_HEADER > len(raw):
        raise ReadError("the metadata run past the first range")
    head, source = {}, None
    for kind, pos, length in _records(raw, RAW4_HEADER, meta_end):
        if kind == _R4_KEYVALUE and length >= 36:
            key = raw[pos + 12:pos + 36].split(b"\0")[0].decode("latin-1")
            value = raw[pos + 36:pos + length].split(b"\0")[0].decode(
                "latin-1").strip()
            if key == "SAMPLEID" and value:
                head["title"] = value
        elif kind == _R4_SOURCE and length >= _R4_RATIO + 8:
            f64 = lambda at: struct.unpack_from(  # noqa: E731
                "<d", raw, pos + at)[0]
            source = _source(f64(_R4_ALPHA1), f64(_R4_ALPHA2),
                             f64(_R4_RATIO), "", f64(_R4_AVERAGE))
    first = _raw4_range(raw, meta_end)
    if first["scan_type"] != _R4_COUPLED:
        head["note"] = ("a \"{}\" scan; only \"{}\" scans were checked "
                        "against an export".format(first["scan_type"],
                                                   _R4_COUPLED))
    ranges, pos = 1, first["end"]
    while pos + RAW4_RANGE_HEADER <= len(raw):
        try:
            pos = _raw4_range(raw, pos)["end"]
        except ReadError:
            break
        ranges += 1
    if ranges > 1:
        head["note"] = "{} ranges in the file; the first is read".format(
            ranges)
    return Pattern(first["x"], first["y"], source, head, "raw4")


def _raw4_range(raw, pos):
    if pos + RAW4_RANGE_HEADER > len(raw):
        raise ReadError("the file ends inside a range header")
    u32 = lambda at: struct.unpack_from("<I", raw, pos + at)[0]  # noqa: E731
    points, datum, extra = u32(_R4_NPOINTS), u32(_R4_DATUM), u32(_R4_EXTRA)
    start = struct.unpack_from("<d", raw, pos + _R4_START)[0]
    step = struct.unpack_from("<d", raw, pos + _R4_STEP)[0]
    scan_type = _text_at(raw, pos + _R4_SCAN_TYPE, 24)
    if not (0 < points < 10 ** 7):
        raise ReadError("an implausible point count ({})".format(points))
    if datum != 4:
        raise ReadError("{} bytes per point; only 4 (float32) is "
                        "read".format(datum))
    if not (np.isfinite(step) and 0 < abs(step) < 100) \
            or not np.isfinite(start):
        raise ReadError("an implausible start or step")
    data = pos + RAW4_RANGE_HEADER + extra
    end = data + points * datum
    if end > len(raw):
        raise ReadError("the data run past the end of the file")
    two_theta = None
    for kind, rpos, length in _records(raw, pos + RAW4_RANGE_HEADER, data):
        if kind == _R4_AXIS and length >= 64:
            if _text_at(raw, rpos + 12, 44) == "2Theta":
                two_theta = struct.unpack_from("<d", raw, rpos + 56)[0]
                break
    if two_theta is not None:
        if abs(two_theta - start) > 1e-6:
            raise ReadError("this \"{}\" scan does not step in 2-theta (its "
                            "2-theta starts at {:g}, the scan at {:g})"
                            .format(scan_type, two_theta, start))
        start = two_theta
    elif scan_type != _R4_COUPLED:
        raise ReadError("a \"{}\" scan with no 2-theta axis: its x cannot "
                        "be named".format(scan_type))
    y = np.frombuffer(raw, dtype="<f4", count=points, offset=data).astype(
        float)
    return {"x": start + step * np.arange(points), "y": y, "end": end,
            "scan_type": scan_type}


# ----------------------------------------------------------- Bruker .brml
_BRML_RAW = re.compile(r"Experiment0/RawData(\d+)\.xml$")
_DATUM = re.compile(r"<Datum>([^<]+)</Datum>")


def _xml_value(xml, tag):
    match = re.search(r"<{}\b[^>]*\bValue=\"([^\"]*)\"".format(tag), xml)
    return match.group(1) if match else None


def read_brml(path):
    """A Bruker `.brml`: the first range's `<Datum>` rows (2-theta the
    third column, intensity the fifth) and the tube's wavelengths."""
    with zipfile.ZipFile(path, "r") as archive:
        names = sorted((int(m.group(1)), n) for n in archive.namelist()
                       for m in [_BRML_RAW.match(n)] if m)
        if not names:
            raise ReadError("no Experiment0/RawData*.xml inside")
        with archive.open(names[0][1]) as fh:
            xml = fh.read().decode("utf-8", errors="replace")
    rows = _DATUM.findall(xml)
    if not rows:
        raise ReadError("no <Datum> rows inside")
    try:
        data = np.array([row.split(",") for row in rows], dtype=float)
    except ValueError:
        raise ReadError("<Datum> rows of different lengths")
    if data.ndim != 2 or data.shape[1] < 5:
        raise ReadError("<Datum> rows of {} columns, 5 expected".format(
            data.shape[-1]))
    axis = re.search(r"<ScanAxisInfo\b[^>]*\bAxisId=\"([^\"]+)\"", xml)
    if axis is not None and axis.group(1) != "TwoTheta":
        raise ReadError("the scan's axis is {}, not 2-theta".format(
            axis.group(1)))
    head = {}
    title = re.search(r"<InfoItem Name=\"SampleName\" Value=\"([^\"]*)\"",
                      xml)
    if title is not None and title.group(1).strip():
        head["title"] = title.group(1).strip()
    if len(names) > 1:
        head["note"] = "{} ranges in the file; the first is read".format(
            len(names))
    source = _source(_xml_value(xml, "WaveLengthAlpha1"),
                     _xml_value(xml, "WaveLengthAlpha2"),
                     _xml_value(xml, "WaveLengthRatio"), "",
                     _xml_value(xml, "WaveLengthAverage"))
    return Pattern(data[:, 2], data[:, 4], source, head, "brml")


# -------------------------------------------------------------- Riet7 .dat
_RIET7_GRID = re.compile(r"(\d+[.,]\d+)\s+(\d+[.,]\d+)\s+(\d+[.,]\d+)\s+"
                         r"[Mm]easureDateTime")
_RIET7_LINE = re.compile(r"Alpha1\s+(\S+)\s+Alpha2\s+(\S+)\s+Ratio\s+(\S+)")


def read_dat(path):
    """A `.dat`: Riet7 when it has Riet7's header, else columns."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if _RIET7_GRID.search(text):
        return read_riet7(text)
    return read_text(path)


def read_riet7(text):
    """Riet7 from its text: start, step and stop from the header, the
    intensities after the header line, the tube from `Alpha1`."""
    grid = _RIET7_GRID.search(text)
    if grid is None:
        raise ReadError("no Riet7 header (start step stop MeasureDateTime)")
    start, step, stop = (float(g.replace(",", ".")) for g in grid.groups())
    if not step > 0:
        raise ReadError("a step of {:g}".format(step))
    # The intensities start on the line AFTER the header line: its date and
    # time ("21/05/2024 03:45") are digits too.
    newline = text.find("\n", grid.end())
    tail = text[newline + 1:] if newline != -1 else ""
    values = np.array(re.findall(r"-?\d+(?:\.\d+)?", tail), dtype=float)
    count = int(round((stop - start) / step)) + 1
    if len(values) < count:
        raise ReadError("{} intensities where the header promises "
                        "{}".format(len(values), count))
    x = start + np.arange(count) * step
    head = {"note": "the angles are the header's, rounded to {:g}".format(
        step)}
    line = _RIET7_LINE.search(text[:grid.start()])
    source = None
    if line is not None:
        try:
            source = _source(*[float(v.replace(",", "."))
                               for v in line.groups()])
        except ValueError:
            source = None
    anode = re.search(r"Anode\s+([A-Z][a-z]?)\b", text[:grid.start()])
    if source is not None and anode is not None:
        source.anode = anode.group(1)
    return Pattern(x, values[:count], source, head, "riet7")


# ----------------------------------------------------------------- columns
def read_text(path):
    """Columns, 2-theta and intensity first. The separator is found from
    the first line that holds numbers (`;` and a tab are separators
    whatever the decimals; a lone `,` only when the decimals are points);
    decimal commas are read; lines that do not start with two numbers are
    skipped as header."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ReadError("an empty file")
    probe = next((line for line in lines
                  if len(re.findall(r"\d", line)) >= 2), lines[0])
    separator = None
    if ";" in probe:
        separator = ";"
    elif "\t" in probe:
        separator = "\t"
    elif "," in probe and "." in probe:
        separator = ","
    rows, headers = [], []
    for line in lines:
        parts = line.split(separator) if separator else line.split()
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) < 2:
            headers.append(line)
            continue
        try:
            a = float(parts[0].replace(",", "."))
            b = float(parts[1].replace(",", "."))
        except ValueError:
            headers.append(line)
            continue
        rows.append((a, b))
    if len(rows) < 2:
        raise ReadError("no two columns of numbers")
    array = np.array(rows, dtype=float)
    x, y = array[:, 0], array[:, 1]
    if not np.all(np.diff(x) > 0) and not np.all(np.diff(x) < 0):
        raise ReadError("the first column is not an angle axis (it does "
                        "not run one way)")
    head = {"header": headers[0].strip()[:120]} if headers else {}
    return Pattern(x, y, None, head, "text")


# ---------------------------------------------------- structures and cards
def read_cif(path):
    """A CIF's crystal, for `core/crystal.py` to simulate."""
    from . import crystal
    try:
        phase = crystal.load_cif(path)
    except crystal.CifError as exc:
        raise ReadError(str(exc))
    head = {"title": phase.title}
    if phase.notes:
        head["note"] = "; ".join(phase.notes)
    return Pattern(source=None, head=head, kind="cif", phase=phase)


def read_card(path):
    """An ICDD PDF card's reflections: `[(d, intensity, hkl), ...]`, from
    the first `<stick_series>` that has any."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ReadError("not XML ({})".format(exc))
    series = root.findall(".//stick_series")
    if not series:
        raise ReadError("no <stick_series>: not a PDF card")
    card_lambda = _number(root.findtext(".//lambda"))
    reflections = []
    for one in series:
        # The block and the value inside it are BOTH called <intensity>.
        for block in one.findall("intensity"):
            d = _number(block.findtext("da"))
            two_theta = _number(block.findtext("theta"))
            if d is None and two_theta is not None and card_lambda:
                d = card_lambda / (2.0 * np.sin(np.radians(two_theta) / 2.0))
            if d is None or not d > 0:
                continue
            value = _number(block.findtext("intensity"))
            hkl = tuple(_integer(block.findtext(k)) for k in "hkl")
            reflections.append((float(d), value if value is not None
                                else 1.0, hkl if None not in hkl else None))
        if reflections:
            break
    if not reflections:
        raise ReadError("no reflections in the card")
    head = {}
    for key, tag in (("title", "chemical_name"), ("formula",
                                                  "chemical_formula"),
                     ("card", "pdf_number"), ("radiation", "patrad")):
        text = (root.findtext(".//" + tag) or "").strip()
        if text:
            head[key] = text
    if card_lambda:
        head["card_lambda"] = "{:g}".format(card_lambda)
    return Pattern(source=None, head=head, kind="card",
                   reflections=reflections)


def _number(text):
    """The leading number of `text` ("7m" is 7), or None."""
    match = re.match(r"\s*([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)",
                     str(text or ""))
    return float(match.group(1).replace(",", ".")) if match else None


def _integer(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None
