"""Space-group SYMBOL -> symmetry OPERATIONS (UI-free, optional backends).

A CIF is allowed to name its space group and leave the operator loop out --
`_symmetry_space_group_name_H-M 'P 21/c'` and nothing else. Our reader then
fell back to P1 and drew the asymmetric unit, silently: a quarter of the
structure, looking exactly like a clean import. That is the biggest
correctness gap the CIF work had left.

This module closes it by turning a symbol (or an International Tables number,
or a Hall symbol) into the operator list, as `x,y,z` strings so the caller
feeds them through the SAME `SymOp.from_xyz` path a file-supplied loop takes.

Backends, in order, both optional and both degraded gracefully:

* **spglib** -- the primary. Its Hall database enumerates all 530 SETTINGS of
  the 230 groups, which is the whole difficulty: `P 21/n` and `P 21/c` are
  both number 14, and expanding one file's coordinates with the other's
  operators produces a confident, completely wrong structure.
* **pymatgen** -- the backstop, standard settings only.

The tricky part is not the operators, it is matching what CIFs actually
WRITE. `P 21/c`, `P2(1)/c`, `P2_1/c` and `P 1 2_1/c 1` are one group; pymatgen
accepts only the third. So symbols are compared on a CANONICAL KEY (letters
and digits, case-folded) rather than literally, and the full symbols in the
database also register their short forms.
"""

import re
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import numpy as np

#: Where a set of operators came from. `SOURCE_FILE` never originates here --
#: it is what `cif.parse_cif` records when the file listed its own loop, which
#: ALWAYS wins over anything this module could derive.
SOURCE_FILE = "file"
SOURCE_HALL = "hall"
SOURCE_SYMBOL = "symbol"
SOURCE_NUMBER = "number"
#: The file really is P1 (or said nothing at all): one operator, no loss.
SOURCE_P1 = "p1"
#: A group was NAMED and could not be resolved -- the honest failure. The
#: caller must say so; this is the case that used to pass for a good import.
SOURCE_UNRESOLVED = "unresolved"

_AXES = ("x", "y", "z")

#: How a space group is NAMED on screen. Hermann-Mauguin is the default
#: because it is what chemists read, write and publish; Hall is unambiguous
#: but unreadable, and the file's own spelling is inconsistent between
#: programs (`p21/n`, `P 21/n`, `P2(1)/n` are all the same group).
CONVENTION_HM = "hm"
CONVENTION_HM_FULL = "hm_full"
CONVENTION_HM_STANDARD = "hm_standard"
CONVENTION_HALL = "hall"
CONVENTION_FILE = "file"
CONVENTIONS = (
    (CONVENTION_HM, "Hermann-Mauguin, short (P2_1/n)"),
    (CONVENTION_HM_FULL, "Hermann-Mauguin, full (P 1 2_1/n 1)"),
    (CONVENTION_HM_STANDARD, "Hermann-Mauguin, standard setting (P2_1/c)"),
    (CONVENTION_HALL, "Hall symbol (-P 2yn)"),
    (CONVENTION_FILE, "Exactly as written in the file (P 21/n)"),
)

#: Cached Hall-database index; `None` until first use, `{}` when no backend.
_TABLE = None                    # type: Optional[dict]


class Symmetry(object):
    """Operators plus the provenance the UI has to be able to state."""

    def __init__(self, xyz, symbol="", number=0, setting="", source="",
                 backend="", ambiguous=False):
        #: Operators as `"x,y,z"` strings, identity first.
        self.xyz = list(xyz)
        #: The database's own short symbol, not the file's spelling.
        self.symbol = symbol
        self.number = int(number or 0)
        #: spglib's setting code: "b1", "c2", "1", "2", "H", "R", or "".
        self.setting = setting
        self.source = source
        self.backend = backend
        #: Did the input leave a CHOICE of setting open? `P 21/c` does not
        #: (convention settles it); `P 21/n` and an origin-choice group do,
        #: and only then has anything really been assumed.
        self.ambiguous = bool(ambiguous)

    def __repr__(self):
        return "Symmetry({!r} #{}, {} ops, {} via {})".format(
            self.symbol, self.number, len(self.xyz), self.source, self.backend)

    @property
    def is_alternate_setting(self):
        # type: () -> bool
        """True when the group has several settings and we PICKED one.

        Worth reporting: the operators are right for that setting and wrong
        for the others, and only the file's coordinates can settle which was
        meant.
        """
        return bool(self.setting) and self.setting not in ("b1", "1")


def canonical_key(text, bars=True):
    # type: (str, bool) -> str
    """Compare-able form of a space-group symbol.

    `"P 21/c"`, `"P2(1)/c"`, `"P2_1/c"` and `"p21/C"` all collapse to `p21c`:
    everything that is not a letter or a digit is punctuation as far as
    matching goes. `bars=False` additionally drops the minus of a roto-
    inversion, which is how the pre-1990 spelling `F d 3 m` is matched to
    `Fd-3m` -- only ever as a SECOND attempt, since dropping it really does
    discard information.
    """
    keep = set("abcdefghijklmnopqrstuvwxyz0123456789")
    if bars:
        keep.add("-")
    return "".join(ch for ch in str(text).lower() if ch in keep)


def split_setting_code(symbol):
    # type: (str) -> Tuple[str, str]
    """`"Fd-3m:2"` / `"R -3 :H"` -> `("Fd-3m", "2")` / `("R -3", "H")`.

    The colon suffix is the CIF way of naming an origin choice or the axes of
    a rhombohedral group, and it is the only thing in the symbol that
    disambiguates settings which are otherwise spelled identically.
    """
    text = str(symbol or "").strip().strip("'\"")
    if ":" not in text:
        return text, ""
    head, _, tail = text.partition(":")
    return head.strip(), tail.strip().strip("'\"")


def _attr(obj, name, default=""):
    """Read a field from spglib, which returns a dataclass in 2.5+ and a plain
    dict before that (and warns about the dict interface in between)."""
    value = getattr(obj, name, None)
    if value is None:
        try:
            value = obj[name]
        except Exception:
            value = None
    return default if value is None else value


def _short_forms(full):
    # type: (str) -> List[str]
    """Short spellings of a FULL Hermann-Mauguin symbol.

    `"P 1 2_1/n 1"` -> `"P 2_1/n"`: a `1` in a symmetry direction means "no
    symmetry that way", and monoclinic CIFs almost always leave those out.
    Without this, a file saying `P 21/n` matches nothing -- and `P 21/n` is
    one of the most common space groups there is.
    """
    tokens = str(full).split()
    kept = [t for t in tokens if t != "1"]
    # "P 1" is the group P1; stripping its only symmetry direction would
    # leave a bare lattice letter, which is not a symbol at all.
    if len(kept) < 2 or len(kept) == len(tokens):
        return []
    return [" ".join(kept)]


def _build_table():
    # type: () -> dict
    """Index spglib's 530 Hall settings by every spelling we might be handed.

    First registration wins per key, and the Hall numbering runs standard
    settings first, so `p21c` lands on the c-glide unique-axis-b setting
    rather than on one of its eight alternatives.
    """
    table = {"symbol": {}, "loose": {}, "hall": {}, "number": {},
             "info": {}, "names": {}, "backend": ""}
    try:
        import spglib
    except Exception:
        return table
    table["backend"] = "spglib"
    for hall in range(1, 531):
        try:
            kind = spglib.get_spacegroup_type(hall)
        except Exception:
            continue
        if kind is None:
            continue
        short = str(_attr(kind, "international_short"))
        full = str(_attr(kind, "international_full"))
        hall_sym = str(_attr(kind, "hall_symbol"))
        number = int(_attr(kind, "number", 0) or 0)
        choice = str(_attr(kind, "choice"))
        if not number:
            continue
        table["info"][hall] = (short, number, choice)
        table["names"][hall] = (short, full, hall_sym)
        table["number"].setdefault(number, []).append(hall)
        if hall_sym:
            table["hall"].setdefault(canonical_key(hall_sym), []).append(hall)
        spellings = [short, full] + _short_forms(full)
        for text in spellings:
            if not text:
                continue
            table["symbol"].setdefault(canonical_key(text), []).append(hall)
            table["loose"].setdefault(
                canonical_key(text, bars=False), []).append(hall)
    return table


def _table():
    # type: () -> dict
    global _TABLE
    if _TABLE is None:
        _TABLE = _build_table()
    return _TABLE


def reset_cache():
    """Drop the cached database (tests that fake a backend's absence)."""
    global _TABLE
    _TABLE = None


def available():
    # type: () -> str
    """Which backend will answer: "spglib", "pymatgen" or ""."""
    if _table()["backend"]:
        return "spglib"
    try:
        import pymatgen.symmetry.groups  # noqa: F401
        return "pymatgen"
    except Exception:
        return ""


#: The only groups that have a DOUBLE GLIDE, and so the only ones the 1992
#: revision of International Tables renamed: Abm2/Aba2/Cmca/Cmma/Ccca became
#: Aem2/Aea2/Cmce/Cmme/Ccce. Older CIFs (and plenty of current software) still
#: write the old names, and spglib's database only knows the new ones.
_E_GLIDE_NUMBERS = frozenset((39, 41, 64, 67, 68))


def _e_glide_aliases(symbol):
    # type: (str) -> List[str]
    """Modern spellings to try for a pre-1992 symbol: `Cmca` -> `Cmce`.

    Generated rather than tabulated because the rename applies to six settings
    of each of the five groups, and every one of them is just "the glide that
    is really a double glide, written `e`". Each candidate is then CHECKED
    against the database and accepted only if it lands on one of those five
    groups, so this can turn an unknown symbol into a known one but never
    turn one valid symbol into a different valid symbol.
    """
    plain = str(symbol or "").strip()
    out = []
    for i, ch in enumerate(plain):
        if i and ch in "abc":
            out.append(plain[:i] + "e" + plain[i + 1:])
    return out


def _rank(choice, code, rhombohedral):
    # type: (str, str, bool) -> int
    """How much we want a candidate setting, lower being better.

    Only reached when the file did not spell the setting out, so every answer
    here is a CONVENTION rather than a fact: unique axis b, origin choice 2
    and hexagonal axes are what modern refinement software writes, and the
    caller reports that an assumption was made.
    """
    choice = (choice or "").strip()
    if code and choice.lower() == code.lower():
        return 0
    if choice in ("R", "H"):
        if rhombohedral:
            return 1 if choice == "R" else 9
        return 4 if choice == "H" else 9
    if not choice:
        return 2
    head = choice[0]
    if head == "b":
        return 3
    if choice == "2":            # the centrosymmetric origin
        return 4
    if choice == "1":
        return 8
    if head == "c":
        return 6
    if head == "a":
        return 7
    return 6


def _pick(halls, table, code="", rhombohedral=False):
    # type: (List[int], dict, str, bool) -> Optional[int]
    best, best_score = None, None
    for hall in halls:
        _, _, choice = table["info"].get(hall, ("", 0, ""))
        score = (_rank(choice, code, rhombohedral), hall)
        if best_score is None or score < best_score:
            best, best_score = hall, score
    return best


def _xyz_from_matrix(rotation, translation):
    # type: (object, object) -> str
    """One operator as CIF text. Translations are written as FRACTIONS -- a
    3-fold's 1/3 is not representable in binary, and `0.333333` would be read
    back with a rounding error that the 0.1 A de-duplication then hides in
    some cells and not others."""
    out = []
    for row in range(3):
        terms = []
        for col in range(3):
            v = int(round(float(rotation[row][col])))
            if v == 0:
                continue
            sign = "-" if v < 0 else ("+" if terms else "")
            mag = "" if abs(v) == 1 else str(abs(v))
            terms.append("{}{}{}".format(sign, mag, _AXES[col]))
        t = float(translation[row]) % 1.0
        if 1e-6 < t < 1.0 - 1e-6:
            frac = Fraction(t).limit_denominator(12)
            terms.append("{}{}/{}".format("+" if terms else "",
                                          frac.numerator, frac.denominator))
        out.append("".join(terms) or "0")
    return ",".join(out)


def _ops_for_hall(hall):
    # type: (int) -> List[str]
    import spglib
    data = spglib.get_symmetry_from_database(hall)
    if data is None:
        return []
    rotations = _attr(data, "rotations", None)
    translations = _attr(data, "translations", None)
    if rotations is None or translations is None:
        return []
    ops = [_xyz_from_matrix(r, t) for r, t in zip(rotations, translations)]
    # Identity first, so an expansion's first image is the site as written.
    ops.sort(key=lambda s: (s != "x,y,z", s))
    return ops


def _pymatgen_candidates(symbol):
    # type: (str) -> List[str]
    """Spellings to try against pymatgen, which insists on `P2_1/c`."""
    text = str(symbol or "").strip()
    if not text:
        return []
    out = [text, text.replace(" ", "")]
    parens = re.sub(r"\((\d)\)", r"_\1", text)          # P2(1)/c
    out += [parens, parens.replace(" ", "")]
    # A token of exactly two digits is an axis and its screw component.
    spaced = " ".join(re.sub(r"^([2346])(\d)$", r"\1_\2", tok)
                      for tok in text.split())
    out += [spaced, spaced.replace(" ", "")]
    # Last resort for symbols written with no spaces at all ("P21/c").
    out.append(re.sub(r"(?<![\d_])([2346])(\d)", r"\1_\2",
                      text.replace(" ", "")))
    seen, unique = set(), []
    for cand in out:
        if cand and cand not in seen:
            seen.add(cand)
            unique.append(cand)
    return unique


def _from_pymatgen(symbol, number):
    # type: (str, int) -> Optional[Symmetry]
    try:
        from pymatgen.symmetry.groups import SpaceGroup
    except Exception:
        return None
    group = None
    for cand in _pymatgen_candidates(symbol):
        try:
            group = SpaceGroup(cand)
            break
        except Exception:
            continue
    source = SOURCE_SYMBOL
    if group is None and number:
        try:
            group = SpaceGroup.from_int_number(int(number))
            source = SOURCE_NUMBER
        except Exception:
            group = None
    if group is None:
        return None
    ops = []
    for op in group.symmetry_ops:
        try:
            ops.append(_xyz_from_matrix(op.rotation_matrix,
                                        op.translation_vector))
        except Exception:
            continue
    if not ops:
        return None
    ops.sort(key=lambda s: (s != "x,y,z", s))
    return Symmetry(ops, symbol=str(group.symbol),
                    number=int(getattr(group, "int_number", 0) or 0),
                    setting="", source=source, backend="pymatgen",
                    ambiguous=True)


def operators_for(symbol="", number=0, hall="", rhombohedral=False):
    # type: (str, int, str, bool) -> Optional[Symmetry]
    """Resolve a named group to its operators, or None if we cannot.

    `hall` is tried FIRST: a Hall symbol names exactly one setting, while
    `P 21/c` names nine and only convention picks between them. `number` is
    the weakest -- International Tables numbers have no setting at all -- so
    it is used last and reported as such.

    `rhombohedral` should be True when the CELL looks rhombohedral (a=b=c
    with three equal non-right angles), which is what tells R-3 from R-3:H.
    """
    found = _lookup(symbol, number, hall, rhombohedral)
    if found is not None:
        chosen, source, ambiguous = found
        ops = _ops_for_hall(chosen)
        if ops:
            table = _table()
            short, num, choice = table["info"].get(chosen, ("", 0, ""))
            return Symmetry(ops, symbol=short, number=num, setting=choice,
                            source=source, backend="spglib",
                            ambiguous=ambiguous)
    plain, _code = split_setting_code(symbol)
    return _from_pymatgen(plain, int(number or 0))


def _lookup(symbol="", number=0, hall="", rhombohedral=False):
    # type: (str, int, str, bool) -> Optional[Tuple[int, str, bool]]
    """Find the HALL NUMBER a naming refers to -> (hall, source, ambiguous).

    Split out from `operators_for` because naming and operators are wanted
    separately: the ❖ page has to print a space group in whichever convention
    the user prefers even when the file supplied its own operator loop and
    nothing needed deriving.
    """
    table = _table()
    plain, code = split_setting_code(symbol)
    number = int(number or 0)
    if table["backend"]:
        attempts = []               # type: List[Tuple[List[int], str]]
        if hall:
            attempts.append((table["hall"].get(canonical_key(hall), []),
                             SOURCE_HALL))
        if plain:
            attempts.append((table["symbol"].get(canonical_key(plain), []),
                             SOURCE_SYMBOL))
            attempts.append((table["loose"].get(
                canonical_key(plain, bars=False), []), SOURCE_SYMBOL))
            for alias in _e_glide_aliases(plain):
                halls = table["symbol"].get(canonical_key(alias), [])
                nums = {table["info"].get(h, ("", 0, ""))[1] for h in halls}
                if len(nums) == 1 and nums.pop() in _E_GLIDE_NUMBERS:
                    attempts.append((halls, SOURCE_SYMBOL))
        if number:
            attempts.append((table["number"].get(number, []), SOURCE_NUMBER))
        for halls, source in attempts:
            if not halls:
                continue
            # A symbol matching several NUMBERS is not a match at all -- it
            # means the key was too lossy (the bar-insensitive pass can do
            # this), and guessing between two different groups is worse than
            # admitting we do not know.
            numbers = {table["info"].get(h, ("", 0, ""))[1] for h in halls}
            if len(numbers) > 1:
                continue
            chosen = _pick(halls, table, code=code,
                           rhombohedral=rhombohedral)
            if chosen is None:
                continue
            _short, _num, choice = table["info"].get(chosen, ("", 0, ""))
            # Rank 0 means the file's own `:code` selected this setting, so
            # nothing was left to convention.
            ambiguous = (len(set(halls)) > 1
                         and _rank(choice, code, rhombohedral) != 0)
            return chosen, source, ambiguous
    return None


#: International Tables number ranges -> crystal system. The boundaries are
#: fixed by the tables themselves and never change.
_SYSTEMS = ((2, "triclinic", "a"), (15, "monoclinic", "m"),
            (74, "orthorhombic", "o"), (142, "tetragonal", "t"),
            (167, "trigonal", "h"), (194, "hexagonal", "h"),
            (230, "cubic", "c"))

_CENTRINGS = {"P": "primitive", "A": "A-centred", "B": "B-centred",
              "C": "C-centred", "I": "body-centred", "F": "face-centred",
              "R": "rhombohedrally centred"}


def crystal_system(number):
    # type: (int) -> Tuple[str, str]
    """IT number -> (system name, its Bravais letter)."""
    for limit, name, letter in _SYSTEMS:
        if 1 <= int(number or 0) <= limit:
            return name, letter
    return "", ""


class Naming(object):
    """One space group under every name it answers to."""

    def __init__(self, short="", full="", hall="", number=0, setting="",
                 given=""):
        #: The STANDARD short symbol. Careful: it does not name a setting --
        #: every one of P2_1/c's nine settings has `P2_1/c` here, so showing
        #: it for a P2_1/n file would read as an outright error to anyone who
        #: knows their own compound.
        self.short = short
        self.full = full
        self.hall = hall
        self.number = int(number or 0)
        self.setting = setting
        #: Exactly what the file wrote, kept so it can still be shown.
        self.given = given

    @property
    def setting_short(self):
        # type: () -> str
        """Short Hermann-Mauguin that KEEPS the setting: `P2_1/n`, not
        `P2_1/c`. Derived by dropping the "no symmetry this way" 1s from the
        full symbol, which is exactly how CIFs spell it."""
        for candidate in _short_forms(self.full):
            return candidate.replace(" ", "")
        return self.short

    @property
    def system(self):
        return crystal_system(self.number)[0]

    @property
    def centring(self):
        """"primitive", "body-centred", ... from the lattice letter."""
        letter = (self.short or self.given or " ")[:1].upper()
        return _CENTRINGS.get(letter, "")

    @property
    def bravais(self):
        """The two-letter Bravais lattice symbol, e.g. `oP`, `mC`, `cF`."""
        letter = crystal_system(self.number)[1]
        centring = (self.short or self.given or " ")[:1].upper()
        if not letter or centring not in _CENTRINGS:
            return ""
        return letter + centring

    def text(self, convention=None):
        # type: (Optional[str]) -> str
        """The name in one convention, falling back to what the file said."""
        value = {CONVENTION_HM: self.setting_short,
                 CONVENTION_HM_FULL: self.full,
                 CONVENTION_HM_STANDARD: self.short,
                 CONVENTION_HALL: self.hall,
                 CONVENTION_FILE: self.given,
                 }.get(convention or CONVENTION_HM, "")
        return value or self.given or self.setting_short or ""


def identify(symbol="", number=0, hall="", rhombohedral=False):
    # type: (str, int, str, bool) -> Optional[Naming]
    """Resolve a naming to ALL of its names, without building operators.

    Returns None when nothing matches, in which case the caller should keep
    showing whatever the file wrote -- an unrecognised symbol is still the
    best information available about the file.
    """
    given = split_setting_code(symbol)[0] or symbol or ""
    found = _lookup(symbol, number, hall, rhombohedral)
    if found is None:
        return None
    chosen, _source, _ambiguous = found
    table = _table()
    short, full, hall_sym = table["names"].get(chosen, ("", "", ""))
    _s, num, choice = table["info"].get(chosen, ("", 0, ""))
    return Naming(short=short, full=full, hall=hall_sym, number=num,
                  setting=choice, given=str(given).strip())


def is_p1(symbol="", number=0):
    # type: (str, int) -> bool
    """Does this name mean "no symmetry beyond the identity"?

    Used to tell a file that is HONESTLY P1 (nothing to expand, nothing to
    report) from one naming a group we failed to resolve.
    """
    if int(number or 0) == 1:
        return True
    plain, _ = split_setting_code(symbol)
    return canonical_key(plain) in ("p1", "p11", "p111")


# ------------------------------------------- symmetry FROM the coordinates
#: Operators worked out from where the atoms actually are, rather than from a
#: name the file gave. This is the only source that can be WRONG about the
#: file's intent and right about the structure in front of it -- which is
#: exactly what is wanted after the user has edited a cell.
SOURCE_DERIVED = "derived"

#: Default Cartesian tolerance for deciding two atoms are symmetry-related.
#: Deliberately loose-ish: a hand-edited or force-field-relaxed cell is never
#: exact, and a tolerance that only accepts perfect symmetry would report P1
#: for every structure anyone has touched.
DEFAULT_SYMPREC = 1e-3


def content_subset(symbols, frac, grid=1000):
    # type: (list, object, int) -> List[int]
    """Indices of ONE atom per distinct site — the cell's actual content.

    A drawn crystal carries boundary copies (round 32: an atom on a face
    belongs to both faces, one at a corner to all eight), and those are exact
    lattice translations of atoms already present. Wrapping puts a copy back
    on top of its original, so a symmetry search sees the same site twice and
    spglib refuses the cell outright — measured: it returns None for every one
    of 7712836 (999 atoms), 2240539 (980) and 2478154 (28), while giving the
    file's own group for all three on their content alone.

    Keyed on a rounded grid rather than compared pairwise, so this is O(n)
    rather than O(n^2) on the structures where it matters most. The 27
    neighbouring buckets are probed as well, because a single key is fragile
    exactly where it must not be: two copies either side of a bucket edge get
    different keys and both survive. Measured on 7712836, whose content is 222
    atoms — a plain key gave 222 at grids of 100, 200, 500 and 2000 and 225 at
    1000, and those three phantom sites are enough for spglib to refuse the
    cell entirely. Wrapping is modular, so the probe wraps too.
    """
    wrapped = np.asarray(frac, dtype=float).reshape(-1, 3) % 1.0
    keys = np.rint(wrapped * grid).astype(int) % grid
    seen, keep = set(), []
    offsets = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1)
               for c in (-1, 0, 1)]
    for index, sym in enumerate(symbols):
        name = str(sym)
        i, j, k = (int(keys[index][0]), int(keys[index][1]),
                   int(keys[index][2]))
        if any((name, (i + a) % grid, (j + b) % grid, (k + c) % grid) in seen
               for a, b, c in offsets):
            continue
        seen.add((name, i, j, k))
        keep.append(index)
    return keep


def _spglib_cell(cell, symbols, frac):
    from . import elements
    lattice = np.asarray(cell.matrix(), dtype=float)
    positions = np.asarray(frac, dtype=float).reshape(-1, 3) % 1.0
    numbers = [int(elements.atomic_number(s)) for s in symbols]
    return (lattice, positions, numbers)


def _dataset(cell, symbols, frac, symprec):
    """spglib's dataset for the cell CONTENT, plus the index map back."""
    if not available() or cell is None or not len(symbols):
        return None, []
    try:
        import spglib
    except ImportError:                                  # pragma: no cover
        return None, []
    keep = content_subset(symbols, frac)
    sub_symbols = [symbols[i] for i in keep]
    sub_frac = np.asarray(frac, dtype=float).reshape(-1, 3)[keep]
    try:
        data = spglib.get_symmetry_dataset(
            _spglib_cell(cell, sub_symbols, sub_frac), symprec=float(symprec))
    except (TypeError, ValueError, RuntimeError):
        return None, keep
    return data, keep


def from_structure(cell, symbols, frac, symprec=DEFAULT_SYMPREC):
    # type: (object, list, object, float) -> Optional[Symmetry]
    """The space group the ATOMS have, not the one the file claimed.

    The primitive behind "re-evaluate the symmetry after an edit". Everything
    else in this module maps a NAME onto operators; this maps COORDINATES onto
    them, which is the only thing that can be trusted once a user has moved an
    atom. Returns None when spglib is unavailable or finds nothing -- the
    caller must then fall back to P1 rather than keep stale operators, because
    operators that no longer describe the structure will happily expand it
    into a cell that never existed.

    `symprec` is in the same units as the lattice (Angstrom here).
    """
    dataset, _keep = _dataset(cell, symbols, frac, symprec)
    if dataset is None:
        return None
    rotations = _attr(dataset, "rotations", None)
    translations = _attr(dataset, "translations", None)
    if rotations is None or translations is None:
        return None
    xyz = [_xyz_from_matrix(r, t) for r, t in zip(rotations, translations)]
    if not xyz:
        return None
    # Identity first, as everywhere else -- `expand` de-duplicates against the
    # first operator's output and a stray ordering makes that harder to read.
    ident = "x,y,z"
    if ident in xyz:
        xyz = [ident] + [t for t in xyz if t != ident]
    return Symmetry(xyz,
                    symbol=str(_attr(dataset, "international", "") or ""),
                    number=int(_attr(dataset, "number", 0) or 0),
                    source=SOURCE_DERIVED, backend="spglib")


def orbit_representatives(cell, symbols, frac, symprec=DEFAULT_SYMPREC):
    # type: (object, list, object, float) -> Optional[List[int]]
    """One atom index per symmetry orbit -- i.e. an ASYMMETRIC UNIT.

    spglib's `equivalent_atoms` labels every atom with the index of its
    orbit's representative, so the distinct labels ARE the asymmetric unit.
    Used to reduce an edited full cell back to something the symmetry can be
    applied to, without which "keep editing the asymmetric unit" has nothing
    to edit.
    """
    dataset, keep = _dataset(cell, symbols, frac, symprec)
    if dataset is None:
        return None
    equivalent = _attr(dataset, "equivalent_atoms", None)
    if equivalent is None:
        return None
    # `equivalent` indexes the CONTENT subset, so map back to the caller's
    # numbering before returning — an index into the wrong list is exactly the
    # kind of silent wrongness round 42 spent a whole round on.
    seen, out = set(), []
    for local, rep in enumerate(equivalent):
        rep = int(rep)
        if rep in seen:
            continue
        seen.add(rep)
        out.append(keep[rep] if 0 <= rep < len(keep) else keep[local])
    return sorted(set(out))
