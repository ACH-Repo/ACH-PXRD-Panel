"""CIF reader that KEEPS the crystallography (UI-free, no dependencies).

OpenBabel already parses .cif, but it hands back a bag of Cartesian atoms —
the cell and the space group are gone, so packing, the asymmetric unit and a
cell box can never be drawn. This module reads the parts that matter:

- the CELL (a, b, c, alpha, beta, gamma) and its fractional->Cartesian matrix,
- the SYMMETRY OPERATIONS as written in the file ("-x+1/2, y, -z"),
- the ASYMMETRIC UNIT (the `_atom_site_*` loop, in fractional coordinates).

`expand()` then applies the operations to get the full cell contents. Keeping
the three separable is the point: the asymmetric unit is what a chemist edits,
the expansion is what they look at, and the cell is what both hang off.

Deliberately hand-rolled: the CIF subset real structures use is small, and a
parser here means no new runtime dependency and offline tests.
"""

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import spacegroups


class CifError(ValueError):
    """Raised when a file is not usable CIF (no cell, no sites, ...)."""


# --------------------------------------------------------------------- cell
class Cell(object):
    """A unit cell. Lengths in Angstrom, angles in DEGREES."""

    def __init__(self, a, b, c, alpha=90.0, beta=90.0, gamma=90.0):
        self.a = float(a)
        self.b = float(b)
        self.c = float(c)
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.gamma = float(gamma)

    def __repr__(self):
        return ("Cell(a={:.4f}, b={:.4f}, c={:.4f}, alpha={:.3f}, "
                "beta={:.3f}, gamma={:.3f})".format(
                    self.a, self.b, self.c, self.alpha, self.beta, self.gamma))

    def to_dict(self):
        return {"a": self.a, "b": self.b, "c": self.c, "alpha": self.alpha,
                "beta": self.beta, "gamma": self.gamma}

    @classmethod
    def from_dict(cls, d):
        # type: (dict) -> Cell
        return cls(d["a"], d["b"], d["c"],
                   d.get("alpha", 90.0), d.get("beta", 90.0),
                   d.get("gamma", 90.0))

    def matrix(self):
        # type: () -> np.ndarray
        """3x3 whose ROWS are the a, b, c vectors in Cartesian space.

        The standard crystallographic setting: **a** along x, **b** in the
        xy plane. `frac @ matrix()` therefore converts a fractional row
        vector to Cartesian.
        """
        al = math.radians(self.alpha)
        be = math.radians(self.beta)
        ga = math.radians(self.gamma)
        cos_al, cos_be, cos_ga = math.cos(al), math.cos(be), math.cos(ga)
        sin_ga = math.sin(ga)
        if abs(sin_ga) < 1e-12:
            raise CifError("degenerate cell: gamma = {}".format(self.gamma))
        cx = self.c * cos_be
        cy = self.c * (cos_al - cos_be * cos_ga) / sin_ga
        cz_sq = self.c * self.c - cx * cx - cy * cy
        if cz_sq <= 0.0:
            raise CifError("cell angles are geometrically impossible: "
                           "{}".format(self))
        return np.array([
            [self.a, 0.0, 0.0],
            [self.b * cos_ga, self.b * sin_ga, 0.0],
            [cx, cy, math.sqrt(cz_sq)],
        ])

    def volume(self):
        # type: () -> float
        return float(abs(np.linalg.det(self.matrix())))

    def to_cartesian(self, frac):
        # type: (np.ndarray) -> np.ndarray
        return np.asarray(frac, dtype=float) @ self.matrix()

    def to_fractional(self, cart):
        # type: (np.ndarray) -> np.ndarray
        return np.asarray(cart, dtype=float) @ np.linalg.inv(self.matrix())

    def looks_rhombohedral(self):
        # type: () -> bool
        """Is this cell on RHOMBOHEDRAL axes rather than hexagonal ones?

        The R groups have two settings whose operators differ, and the symbol
        (`R -3 c`) is spelled the same for both -- so when a file leaves the
        operator loop out, the cell SHAPE is the only evidence of which was
        meant: rhombohedral axes give a = b = c with three equal angles that
        are not 90 degrees, hexagonal axes give gamma = 120.
        """
        lengths = (self.a, self.b, self.c)
        angles = (self.alpha, self.beta, self.gamma)
        if max(lengths) - min(lengths) > 1e-3 * max(lengths):
            return False
        if max(angles) - min(angles) > 0.1:
            return False
        return abs(angles[0] - 90.0) > 0.1

    def corners(self):
        # type: () -> np.ndarray
        """The 8 cell corners in Cartesian space, origin first."""
        f = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                      [1, 1, 0], [1, 0, 1], [0, 1, 1], [1, 1, 1]],
                     dtype=float)
        return self.to_cartesian(f)

    def edges(self):
        # type: () -> List[Tuple[int, int]]
        """The 12 box edges as index pairs into `corners()`."""
        return [(0, 1), (0, 2), (0, 3), (1, 4), (1, 5), (2, 4),
                (2, 6), (3, 5), (3, 6), (4, 7), (5, 7), (6, 7)]


# ---------------------------------------------------------- symmetry operators
_FRACTION = re.compile(r"^([+-]?\d*\.?\d+)\s*/\s*(\d*\.?\d+)$")


def _term_value(text):
    # type: (str) -> float
    """Numeric factor of one symmetry-operator term: "", "2", "1/2", "/2"."""
    s = text.strip().lstrip("*")
    if not s:
        return 1.0
    if s.startswith("/"):                      # "x/2" -> factor 1/2
        return 1.0 / float(s[1:])
    m = _FRACTION.match(s)
    if m:
        return float(m.group(1)) / float(m.group(2))
    return float(s)


def parse_symop_component(text):
    # type: (str) -> Tuple[float, float, float, float]
    """One component of a symmetry operator -> (cx, cy, cz, constant).

    Handles the spellings that occur in the wild: "x", "-x", "1/2-x",
    "-x+1/2", "0.5+y", "2x", "x/2", "-z".
    """
    s = str(text).strip().lower().replace(" ", "")
    if not s:
        raise CifError("empty symmetry component")
    coef = [0.0, 0.0, 0.0]
    const = 0.0
    for term in re.findall(r"[+-]?[^+-]+", s):
        sign = -1.0 if term.startswith("-") else 1.0
        body = term.lstrip("+-")
        if not body:
            continue
        axis = None
        for i, var in enumerate("xyz"):
            if var in body:
                axis = i
                body = body.replace(var, "", 1)
                break
        if axis is None:
            const += sign * _term_value(body)
        else:
            coef[axis] += sign * _term_value(body)
    return coef[0], coef[1], coef[2], const


class SymOp(object):
    """A crystallographic symmetry operation: frac -> R @ frac + t."""

    def __init__(self, rotation, translation, text=""):
        self.rotation = np.asarray(rotation, dtype=float).reshape(3, 3)
        self.translation = np.asarray(translation, dtype=float).reshape(3)
        self.text = text

    def __repr__(self):
        return "SymOp({!r})".format(self.text or self.as_xyz())

    @classmethod
    def from_xyz(cls, text):
        # type: (str) -> SymOp
        parts = str(text).strip().strip("'\"").split(",")
        if len(parts) != 3:
            raise CifError("symmetry op needs 3 components: {!r}".format(text))
        rot = np.zeros((3, 3))
        trans = np.zeros(3)
        for row, part in enumerate(parts):
            cx, cy, cz, const = parse_symop_component(part)
            rot[row] = (cx, cy, cz)
            trans[row] = const
        return cls(rot, trans, str(text).strip())

    def as_xyz(self):
        # type: () -> str
        out = []
        for row in range(3):
            terms = []
            for col, var in enumerate("xyz"):
                v = self.rotation[row, col]
                if abs(v) < 1e-9:
                    continue
                sign = "-" if v < 0 else ("+" if terms else "")
                mag = "" if abs(abs(v) - 1.0) < 1e-9 else "{:g}".format(abs(v))
                terms.append("{}{}{}".format(sign, mag, var))
            t = self.translation[row]
            if abs(t) > 1e-9:
                terms.append("{}{:g}".format("+" if t > 0 and terms else "", t))
            out.append("".join(terms) or "0")
        return ",".join(out)

    def apply(self, frac):
        # type: (np.ndarray) -> np.ndarray
        f = np.asarray(frac, dtype=float)
        return f @ self.rotation.T + self.translation


IDENTITY = SymOp(np.eye(3), np.zeros(3), "x,y,z")


# ----------------------------------------------------------------- CIF parsing
class CifData(object):
    """What a CIF told us: a cell, the symmetry ops, the asymmetric unit."""

    def __init__(self, cell, symops, symbols, frac, name="",
                 spacegroup="", labels=None, occupancy=None,
                 disorder_groups=None, disorder_assemblies=None,
                 it_number=0, hall="", symmetry_source="",
                 symmetry_note="", info=None):
        self.cell = cell
        self.symops = list(symops)
        self.symbols = list(symbols)
        self.frac = np.asarray(frac, dtype=float).reshape(-1, 3)
        self.name = name
        self.spacegroup = spacegroup
        #: International Tables number and Hall symbol, where the file gave
        #: them. Both are routes to the operators when the loop is missing,
        #: and the Hall symbol is the only one that names a SETTING exactly.
        self.it_number = int(it_number or 0)
        self.hall = hall
        #: How `symops` was arrived at -- `spacegroups.SOURCE_*`. Anything
        #: other than "file" means we derived them, and `symmetry_note` says
        #: so in words: a structure quietly expanded (or quietly NOT expanded)
        #: by the reader is exactly the kind of wrongness that looks right.
        self.symmetry_source = symmetry_source or spacegroups.SOURCE_FILE
        self.symmetry_note = symmetry_note
        #: Descriptive tags the file happened to carry (`_INFO_TAGS`). Every
        #: one is optional, so treat a missing key as normal, never an error.
        self.info = dict(info or {})
        self.labels = list(labels or symbols)
        #: Site occupancies, 1.0 where the file said nothing. READ and now
        #: also USED (round 38): a disordered structure lists every
        #: alternative, and drawing them all at once superimposes atoms that
        #: are never present together — which then perceives bonds that cannot
        #: exist and makes the whole cell read as a framework.
        self.occupancy = ([float(o) for o in occupancy] if occupancy
                          is not None else [1.0] * len(self.symbols))
        #: `_atom_site_disorder_group` / `_atom_site_disorder_assembly`: the
        #: crystallographer's OWN statement of which alternatives go together.
        #: Believe it when it is there; fall back on geometry when it is not.
        self.disorder_groups = list(disorder_groups
                                    or [""] * len(self.symbols))
        self.disorder_assemblies = list(disorder_assemblies
                                        or [""] * len(self.symbols))

    @property
    def is_disordered(self):
        # type: () -> bool
        return (any(o < 1.0 - 1e-6 for o in self.occupancy)
                or any(g for g in self.disorder_groups))

    @property
    def n_sites(self):
        return len(self.symbols)

    def __repr__(self):
        return "CifData({!r}, {} sites, {} symops, {!r})".format(
            self.name, self.n_sites, len(self.symops), self.spacegroup)


def _strip_esd(value):
    # type: (str) -> float
    """CIF numbers carry a standard uncertainty: "5.4321(12)" -> 5.4321."""
    s = str(value).strip().strip("'\"")
    s = re.sub(r"\(\d+\)$", "", s)
    if s in ("", ".", "?"):
        raise CifError("missing numeric value")
    return float(s)


def _tag_value(value):
    # type: (str) -> str
    """A CIF text field, with the two "no value" markers folded to empty."""
    s = str(value).strip().strip("'\"")
    return "" if s in (".", "?") else s


def _element_from_label(label, type_symbol=None):
    # type: (str, Optional[str]) -> str
    """"C12A" / "Fe1" -> "C" / "Fe". `_atom_site_type_symbol` wins when given,
    minus any charge suffix ("Fe3+")."""
    from . import elements
    for candidate in (type_symbol, label):
        if not candidate:
            continue
        text = str(candidate).strip().strip("'\"")
        text = re.sub(r"[0-9+\-].*$", "", text)     # "Fe3+" / "C12A" -> "Fe"/"C"
        if len(text) >= 2 and elements.atomic_number(text[:2]) > 0:
            return elements.symbol(elements.atomic_number(text[:2]))
        if text and elements.atomic_number(text[:1]) > 0:
            return elements.symbol(elements.atomic_number(text[:1]))
    return ""


def _tokenize_loop_values(lines, start):
    # type: (List[str], int) -> Tuple[List[str], int]
    """Values of a loop body, honouring quotes; stops at the next tag/loop."""
    values = []
    i = start
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        low = stripped.lower()
        if stripped.startswith("_") or low.startswith("loop_") \
                or low.startswith("data_"):
            break
        if stripped.startswith(";"):        # multi-line text field
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(";"):
                i += 1
            i += 1
            values.append("")
            continue
        values.extend(re.findall(r"'[^']*'|\"[^\"]*\"|\S+", stripped))
        i += 1
    return values, i


_CELL_TAGS = {
    "_cell_length_a": "a", "_cell_length_b": "b", "_cell_length_c": "c",
    "_cell_angle_alpha": "alpha", "_cell_angle_beta": "beta",
    "_cell_angle_gamma": "gamma",
}
_SYMOP_TAGS = ("_symmetry_equiv_pos_as_xyz",
               "_space_group_symop_operation_xyz",
               "_symmetry_equiv_pos_as_xyz_")
_SPACEGROUP_TAGS = ("_symmetry_space_group_name_h-m",
                    "_space_group_name_h-m_alt",
                    "_space_group_name_h-m_full")
#: Kept SEPARATE from the H-M tags (they used to share a tuple, so a file
#: carrying only a Hall symbol reported "-P 2ybc" as its space group). A Hall
#: symbol names one setting unambiguously, which makes it the BEST route to
#: the operators and the worst thing to show a chemist.
_HALL_TAGS = ("_symmetry_space_group_name_hall", "_space_group_name_hall")
_ITNUM_TAGS = ("_symmetry_int_tables_number", "_space_group_it_number")

#: Descriptive single-value tags worth keeping for the crystal page, mapped to
#: the short key it displays them under. NONE of these is guaranteed to be
#: present -- CIFs are wildly inconsistent about what they carry beyond the
#: cell and the sites -- so everything downstream shows a row only when the
#: file actually had one, and nothing here may ever become required.
_INFO_TAGS = {
    "_chemical_name_systematic": "name_systematic",
    "_chemical_name_common": "name_common",
    "_chemical_name_mineral": "name_mineral",
    "_chemical_formula_moiety": "formula_moiety",
    "_chemical_formula_sum": "formula_sum",
    "_chemical_formula_structural": "formula_structural",
    "_chemical_formula_weight": "formula_weight",
    "_cell_formula_units_z": "z",
    "_cell_volume": "volume",
    "_cell_measurement_temperature": "temperature_cell",
    "_diffrn_ambient_temperature": "temperature",
    "_diffrn_radiation_wavelength": "wavelength",
    "_diffrn_radiation_type": "radiation",
    "_exptl_crystal_density_diffrn": "density_reported",
    "_exptl_crystal_colour": "colour",
    "_exptl_crystal_description": "habit",
    "_refine_ls_r_factor_gt": "r_factor",
    "_refine_ls_wr_factor_ref": "wr_factor",
    "_refine_ls_goodness_of_fit_ref": "goodness_of_fit",
    "_symmetry_cell_setting": "cell_setting",
    "_publ_section_title": "title",
    "_journal_name_full": "journal",
    "_journal_year": "journal_year",
    "_journal_paper_doi": "doi",
    "_database_code_depnum_ccdc_archive": "ccdc",
    "_cod_database_code": "cod",
}


def _read_text_block(lines, start):
    # type: (List[str], int) -> Tuple[str, int]
    """A tag's value when it is on the FOLLOWING lines -> (text, next index).

    CIF puts long values in a `;`-delimited block on its own lines, which is
    how `_publ_section_title` and often `_chemical_name_systematic` are
    written. Reading only `_tag value` misses every one of them.
    """
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return "", start
    first = lines[i].strip()
    if not first.startswith(";"):
        # A plain value wrapped onto the next line.
        return ("", start) if first.startswith("_") else (_tag_value(first),
                                                          i + 1)
    body = [first[1:].strip()]
    i += 1
    while i < len(lines) and not lines[i].strip().startswith(";"):
        body.append(lines[i].strip())
        i += 1
    return " ".join(p for p in body if p).strip(), i + 1


def parse_cif(text, derive_symmetry=True):
    # type: (str, bool) -> CifData
    """Parse the first data block of a CIF. Raises CifError if unusable.

    `derive_symmetry` allows a file that NAMES its space group without
    listing the operators to be expanded anyway (see `_derive_symmetry`).
    Pass False to get literally what the file said, which is what a test of
    the parsing itself wants.
    """
    lines = str(text).splitlines()
    cell_vals = {}          # type: Dict[str, float]
    symop_texts = []        # type: List[str]
    spacegroup = ""
    hall = ""
    it_number = 0
    name = ""
    info = {}              # type: Dict[str, str]
    site_cols = {}          # type: Dict[str, List[str]]

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        low = stripped.lower()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if low.startswith("data_") and not name:
            name = stripped[5:].strip()
            i += 1
            continue
        if low.startswith("loop_"):
            tags = []
            i += 1
            # Blank and comment lines BETWEEN the tags are legal and some
            # writers double-space the whole file. Stopping at the first one
            # (as this did until round 40) discards the entire loop -- on a
            # double-spaced file that means the atom sites, and the reader
            # then reports a perfectly good CIF as having no atoms at all.
            while i < len(lines):
                probe = lines[i].strip()
                if not probe or probe.startswith("#"):
                    i += 1
                    continue
                if not probe.startswith("_"):
                    break
                tags.append(probe.split()[0].lower())
                i += 1
            values, i = _tokenize_loop_values(lines, i)
            if not tags:
                continue
            ncol = len(tags)
            rows = len(values) // ncol
            columns = {t: [] for t in tags}
            for r in range(rows):
                for c, tag in enumerate(tags):
                    columns[tag].append(values[r * ncol + c])
            for tag in _SYMOP_TAGS:
                if tag in columns:
                    symop_texts.extend(columns[tag])
            if any(t.startswith("_atom_site") for t in tags):
                for tag, col in columns.items():
                    site_cols.setdefault(tag, col)
            continue
        # plain "_tag value" line
        parts = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", stripped)
        tag = parts[0].lower()
        value = parts[1] if len(parts) > 1 else ""
        if tag in _CELL_TAGS:
            try:
                cell_vals[_CELL_TAGS[tag]] = _strip_esd(value)
            except (CifError, ValueError):
                pass
        elif tag in _SPACEGROUP_TAGS and not spacegroup:
            spacegroup = _tag_value(value)
        elif tag in _HALL_TAGS and not hall:
            hall = _tag_value(value)
        elif tag in _ITNUM_TAGS and not it_number:
            try:
                it_number = int(_strip_esd(value))
            except (CifError, ValueError):
                it_number = 0
        elif tag in _SYMOP_TAGS and value:
            symop_texts.append(value)
        elif tag in _INFO_TAGS and _INFO_TAGS[tag] not in info:
            text_value = _tag_value(value)
            if not text_value:
                text_value, i = _read_text_block(lines, i + 1)
                if text_value:
                    info[_INFO_TAGS[tag]] = text_value
                continue
            info[_INFO_TAGS[tag]] = text_value
        i += 1

    missing = [k for k in ("a", "b", "c") if k not in cell_vals]
    if missing:
        raise CifError(
            "no unit cell in this CIF (missing {})".format(", ".join(missing)))
    cell = Cell(cell_vals["a"], cell_vals["b"], cell_vals["c"],
                cell_vals.get("alpha", 90.0), cell_vals.get("beta", 90.0),
                cell_vals.get("gamma", 90.0))

    fx = site_cols.get("_atom_site_fract_x")
    fy = site_cols.get("_atom_site_fract_y")
    fz = site_cols.get("_atom_site_fract_z")
    if not (fx and fy and fz):
        raise CifError("no fractional atom sites (_atom_site_fract_x/y/z)")
    labels = site_cols.get("_atom_site_label", [""] * len(fx))
    types = site_cols.get("_atom_site_type_symbol", [None] * len(fx))
    occ_col = site_cols.get("_atom_site_occupancy", [])
    group_col = site_cols.get("_atom_site_disorder_group", [])
    assembly_col = site_cols.get("_atom_site_disorder_assembly", [])

    symbols, frac, kept_labels = [], [], []
    occupancy, groups, assemblies = [], [], []
    for k in range(min(len(fx), len(fy), len(fz))):
        try:
            xyz = (_strip_esd(fx[k]), _strip_esd(fy[k]), _strip_esd(fz[k]))
        except (CifError, ValueError):
            continue
        sym = _element_from_label(labels[k] if k < len(labels) else "",
                                  types[k] if k < len(types) else None)
        if not sym:
            continue
        symbols.append(sym)
        frac.append(xyz)
        kept_labels.append(labels[k] if k < len(labels) else sym)
        try:
            occupancy.append(_strip_esd(occ_col[k]) if k < len(occ_col)
                             else 1.0)
        except (CifError, ValueError):
            occupancy.append(1.0)          # "." / "?" means "assume full"
        groups.append(_tag_value(group_col[k]) if k < len(group_col) else "")
        assemblies.append(_tag_value(assembly_col[k])
                          if k < len(assembly_col) else "")
    if not symbols:
        raise CifError("no usable atom sites in this CIF")

    symops = []
    for t in symop_texts:
        t = t.strip().strip("'\"")
        if not t:
            continue
        try:
            symops.append(SymOp.from_xyz(t))
        except (CifError, ValueError):
            continue
    symops, spacegroup, source, note = _derive_symmetry(
        symops, cell, spacegroup, it_number, hall, derive_symmetry)
    return CifData(cell, symops, symbols, np.array(frac), name=name,
                   spacegroup=spacegroup, labels=kept_labels,
                   occupancy=occupancy, disorder_groups=groups,
                   disorder_assemblies=assemblies, it_number=it_number,
                   hall=hall, symmetry_source=source, symmetry_note=note,
                   info=info)


def _derive_symmetry(symops, cell, spacegroup, it_number, hall, enabled):
    # type: (List[SymOp], Cell, str, int, str, bool) -> Tuple[List[SymOp], str, str, str]
    """Settle what operators to use, and what to SAY about them.

    Until round 40 a missing operator loop meant P1 -- "the file already holds
    every atom" -- which is true of the files that omit the loop because they
    are P1, and false of the ones that omit it because they named the group
    instead. In the second case MoloM drew the asymmetric unit: a quarter of a
    P2_1/c structure, with no error, no warning and a perfectly plausible
    picture. Deriving the operators fixes that; SAYING which ones were derived
    is what keeps the fix honest, because a group has settings and only the
    file's coordinates know which one was meant.

    The file's own loop always wins. Some programs write coordinates already
    expanded to P1 while still naming the parent group in the header, so
    re-applying that group's operators would DOUBLE the structure -- one op in
    the loop means one op, and the mismatch is reported rather than acted on.
    """
    named = bool(spacegroup or hall or it_number)
    if symops:
        note = ""
        if len(symops) == 1 and named and not spacegroups.is_p1(spacegroup,
                                                               it_number):
            note = ("the file lists only the identity though it names {}; "
                    "showing the coordinates as given".format(
                        spacegroup or hall or "#{}".format(it_number)))
        return symops, spacegroup, spacegroups.SOURCE_FILE, note

    if not named or spacegroups.is_p1(spacegroup, it_number):
        # Genuinely P1 (or nothing said at all): every atom is in the file.
        return [IDENTITY], spacegroup, spacegroups.SOURCE_P1, ""

    resolved = spacegroups.operators_for(
        spacegroup, number=it_number, hall=hall,
        rhombohedral=cell.looks_rhombohedral()) if enabled else None
    named_as = spacegroup or hall or "space group #{}".format(it_number)
    if resolved is None:
        missing = enabled and not spacegroups.available()
        note = ("the file names {} but lists no symmetry operations{} - "
                "showing the asymmetric unit only".format(
                    named_as,
                    ", and neither spglib nor pymatgen is installed to "
                    "expand it" if missing else ""))
        return [IDENTITY], spacegroup, spacegroups.SOURCE_UNRESOLVED, note

    ops = []
    for text in resolved.xyz:
        try:
            ops.append(SymOp.from_xyz(text))
        except (CifError, ValueError):
            continue
    if not ops:
        return [IDENTITY], spacegroup, spacegroups.SOURCE_UNRESOLVED, ""
    route = {
        spacegroups.SOURCE_HALL: "the Hall symbol {}".format(hall),
        spacegroups.SOURCE_NUMBER: "space group number {}".format(it_number),
    }.get(resolved.source, "the space group {}".format(spacegroup))
    bits = ["no symmetry operations in the file: {} generated from "
            "{}".format(len(ops), route)]
    if resolved.source == spacegroups.SOURCE_NUMBER:
        # A number names a group but never a setting, so this is the one
        # route with nothing in the file to check the choice against.
        bits.append("an IT number carries no setting - verify the cell")
    elif resolved.ambiguous and resolved.is_alternate_setting:
        # Only where a choice was genuinely left open: saying "setting b1
        # assumed" about every ordinary P2_1/c file would be noise, and a
        # warning that fires always is a warning nobody reads.
        bits.append("setting {} assumed".format(resolved.setting))
    if resolved.backend == "pymatgen":
        bits.append("standard setting (pymatgen; install spglib for the "
                    "alternative settings)")
    # Showing the file's own spelling is right when it has one; a file that
    # gave only a Hall symbol or a number now gets a readable H-M symbol
    # instead of the blank the crystal page used to show.
    return ops, spacegroup or resolved.symbol, resolved.source, "; ".join(bits)


# -------------------------------------------------------------------- disorder
#: Draw every alternative at once (what MoloM did until round 38).
POLICY_ALL = "all"
#: Resolve OVERLAPS only: where alternatives sit on top of each other, keep
#: the most occupied one. Everything else is left alone, so a partially
#: occupied guest that overlaps nothing still shows.
POLICY_DOMINANT = "dominant"
#: Dominant, plus drop anything below `MAJOR_THRESHOLD` — "show me the ordered
#: structure", which for a MOF usually means the framework without its
#: disordered guest.
POLICY_MAJOR = "major"
DISORDER_POLICIES = (POLICY_DOMINANT, POLICY_MAJOR, POLICY_ALL)

#: Two atoms closer than this are not both really there. Chosen below every
#: real interatomic distance — the shortest bonds in chemistry are H-F at
#: 0.92 A and an X-ray riding C-H at 0.93 — and above the 0.1 A tolerance the
#: symmetry de-duplication already uses. Alternatives further apart than this
#: are the crystallographer's business to tag, which is what the disorder
#: GROUP columns are for.
DISORDER_RADIUS = 0.8
MAJOR_THRESHOLD = 0.5


def resolve_disorder(symbols, frac, cell, occupancy, groups=None,
                     assemblies=None, policy=POLICY_DOMINANT,
                     radius=DISORDER_RADIUS, threshold=MAJOR_THRESHOLD,
                     sites=None):
    # type: (list, np.ndarray, Cell, Sequence, Optional[Sequence], Optional[Sequence], str, float, float) -> Tuple[np.ndarray, dict]
    """Which atoms of a DISORDERED structure to actually draw.

    Returns `(keep_mask, report)`. A disordered CIF lists every alternative
    position, so drawing them all superimposes atoms that are never present
    together: `MIL-53-lp.cif` comes out with carbons carrying NINE neighbours
    at 0.11 A, which then perceives bonds that cannot exist and makes the
    whole cell read as one percolating framework. This is the third leg of
    round 38 — bond kinds say where a molecule ENDS, valence sanity says which
    bonds cannot BE, and this says which atoms are not simultaneously there.

    Two mechanisms, deliberately both:

    * the **disorder GROUP** columns, which is the crystallographer's own
      statement of which alternatives belong together — believed when present,
      per assembly, keeping the group carrying the most occupancy;
    * **geometric overlap**, for the (very common) files that carry occupancies
      and no grouping at all. Only a site that overlaps another one is ever
      dropped: a lone partially-occupied site is a real partial site, and a
      half-occupied atom sitting ON a symmetry element is a special position,
      not an alternative.
    """
    n = len(symbols)
    occ = np.asarray([float(o) for o in occupancy], dtype=float) \
        if occupancy is not None else np.ones(n)
    if occ.size != n:
        occ = np.ones(n)
    keep = np.ones(n, dtype=bool)
    report = {"policy": policy, "sites": n, "by_group": 0, "by_overlap": 0,
              "by_threshold": 0, "dropped": 0}
    if policy == POLICY_ALL or n == 0:
        return keep, report
    groups = list(groups or [""] * n)
    assemblies = list(assemblies or [""] * n)

    # 1) the file's own grouping, per assembly
    tagged = [i for i in range(n) if groups[i]]
    if tagged:
        by_assembly = {}
        for i in tagged:
            by_assembly.setdefault(assemblies[i], []).append(i)
        for members in by_assembly.values():
            totals = {}
            for i in members:
                totals[groups[i]] = totals.get(groups[i], 0.0) + occ[i]
            best = max(sorted(totals), key=lambda g: totals[g])
            for i in members:
                if groups[i] != best:
                    keep[i] = False
                    report["by_group"] += 1

    # 2) below the threshold, if asked
    if policy == POLICY_MAJOR:
        low = keep & (occ < float(threshold) - 1e-9)
        report["by_threshold"] = int(low.sum())
        keep &= ~low

    # 3) whatever still overlaps. Highest occupancy first, so the winner of
    # each cluster is picked before it can be dropped by a weaker neighbour.
    if float(radius) > 0 and np.any(occ < 1.0 - 1e-6):
        site_of = (np.asarray(sites) if sites is not None
                   else np.arange(n))
        m = cell.matrix()
        frac = np.asarray(frac, dtype=float).reshape(-1, 3)
        for i in np.argsort(-occ, kind="stable"):
            if not keep[i]:
                continue
            d = frac - frac[i]
            d = d - np.round(d)                     # minimum image
            dist = np.linalg.norm(d @ m, axis=1)
            close = (dist < float(radius)) & keep
            close[i] = False
            # A fully occupied atom is never an "alternative" — if two of
            # those overlap the file is broken in a way this cannot fix, and
            # valence sanity will report it instead.
            close &= occ < 1.0 - 1e-6
            if sites is not None:
                # NEVER break a symmetry orbit. Two overlapping atoms that
                # came from the SAME site are images of one another under the
                # space group, so keeping some and dropping others leaves a
                # structure that no longer obeys its own symmetry -- which is
                # exactly what turned `2240539.cif` (a plastic crystal, one
                # molecule smeared over 192 operations of Fm-3m) from VESTA's
                # neat array of cages into a chaotic blob of 184 atoms out of
                # 280. Orientational disorder is kept whole or not at all;
                # the honest picture of a smeared molecule IS the smear.
                close &= site_of != site_of[i]
            if np.any(close):
                keep &= ~close
                report["by_overlap"] += int(close.sum())
    report["dropped"] = int((~keep).sum())
    report["kept"] = int(keep.sum())
    return keep, report


# -------------------------------------------------------------------- expansion
def expand(data, tol=0.1, wrap=True, whole_molecules=True, boundary=True,
           exterior=0, disorder=POLICY_DOMINANT, report=None,
           shell_molecules=False):
    # type: (CifData, float, bool, bool, bool, int) -> Tuple[List[str], np.ndarray]
    """Apply every symmetry op to every site -> (symbols, CARTESIAN coords).

    Copies that land on an existing atom (within `tol` Angstrom, measured
    with the minimum-image convention so 0.999 and 0.001 count as touching)
    are dropped — special positions on a symmetry element would otherwise
    produce a pile of duplicate atoms.

    `whole_molecules` then reassembles fragments split by the wrap, so the
    cell shows complete molecules rather than atoms stranded on a far face.

    `boundary` repeats the atoms that lie exactly ON the cell boundary onto
    every equivalent corner, edge and face — see `boundary_images`. It is on
    by default because it is what every crystallography viewer draws, and
    without it rock salt shows a single sodium instead of eight corners.

    `exterior` additionally carries out VESTA's boundary SEARCH for that many
    shells — atoms beyond the box that are bonded to atoms inside it, so a
    chain or a framework runs on instead of ending at the wall. Off by
    default (Christian's call): it changes what an import looks like, and
    nothing that counts cell content should ever see these atoms.

    `disorder` decides what to do with partially occupied sites — see
    `resolve_disorder`. It runs on the EXPANDED atoms, not on the sites,
    because the alternatives are routinely symmetry images of one another
    rather than separate rows in the file. Pass a dict as `report` to be told
    what it did.
    """
    if data.n_sites == 0:
        return [], np.zeros((0, 3))
    matrix = data.cell.matrix()
    symbols = []            # type: List[str]
    fracs = []              # type: List[np.ndarray]
    sites = []              # type: List[int]
    for site in range(data.n_sites):
        base = data.frac[site]
        for op in data.symops:
            f = op.apply(base)
            if wrap:
                f = f - np.floor(f)          # into [0, 1)
            if not _is_new(f, fracs, matrix, tol, wrap):
                continue
            fracs.append(f)
            symbols.append(data.symbols[site])
            sites.append(site)
    if not fracs:
        return [], np.zeros((0, 3))
    out = np.asarray(fracs)
    # A structure in which NO site is fully occupied has no ordered skeleton
    # to resolve against: every atom is an alternative, nothing is dominant,
    # and picking greedily by occupancy produces a chimera that obeys neither
    # the chemistry nor the space group. `2240539.cif` is the case -- a
    # plastic crystal, one molecule smeared over 192 operations of Fm-3m, all
    # five sites at occupancy 0.21-0.43 -- where resolving turned VESTA's neat
    # array of cages into a blob of 184 atoms out of 280. The honest picture
    # of a smeared molecule IS the smear, which is what VESTA, pymatgen and
    # ASE all draw.
    wholly_disordered = bool(data.occupancy) and all(
        o < 1.0 - 1e-6 for o in data.occupancy)
    if wholly_disordered and report is not None:
        report["disorder"] = {"policy": disorder, "sites": data.n_sites,
                              "dropped": 0, "kept": len(symbols),
                              "wholly_disordered": True}
    if disorder != POLICY_ALL and data.is_disordered and not wholly_disordered:
        keep, info = resolve_disorder(
            symbols, out, data.cell,
            [data.occupancy[s] for s in sites],
            [data.disorder_groups[s] for s in sites],
            [data.disorder_assemblies[s] for s in sites],
            policy=disorder, sites=sites)
        if report is not None:
            report["disorder"] = info
        if not keep.all():
            symbols = [s for s, k in zip(symbols, keep) if k]
            out = out[keep]
            sites = [s for s, k in zip(sites, keep) if k]
        if not symbols:
            return [], np.zeros((0, 3))
    if wrap and whole_molecules:
        out = unwrap_molecules(symbols, out, data.cell,
                               geometric=wholly_disordered)
    # The boundary search runs on the CELL CONTENT, so capture it before the
    # boundary copies go on the end — those are duplicates of atoms already
    # present and would only have it search the same faces twice.
    content_symbols, content_frac = list(symbols), out
    if wrap and boundary:
        # `geometric` here for the same reason as everywhere else in this
        # function (round 42d): on a wholly disordered structure the chemistry
        # graph shatters, so a boundary atom would carry a two-atom shard
        # instead of its molecule. 2240539 has 18 atoms exactly on a face and
        # four 70-atom cages; without this they carried 21 loose atoms and the
        # corner cage was drawn ONCE instead of at all eight corners, which is
        # Christian's "only one third of the CH polyhedra are shown".
        groups = (fragment_info(symbols, out, data.cell,
                                geometric=wholly_disordered)
                  if whole_molecules else None)
        extra_symbols, extra_frac = boundary_images(symbols, out, groups)
        if extra_symbols:
            symbols = list(symbols) + extra_symbols
            out = np.vstack([out, extra_frac])
    if wrap and shell_molecules:
        # VESTA's DEFAULT picture, and the whole of it: the neighbouring
        # cells' MOLECULES that reach into this one. Deliberately NOT combined
        # with `exterior` below — the 37-file sweep against Christian's VESTA
        # exports is what settled that. A bonded shell grown off every atom is
        # right for a covalent chain (round 35's case) and catastrophic on a
        # perovskite or an intermetallic, where every atom has 6-12 neighbours
        # just outside the box: BaLiF3 went 15 -> 25 atoms and Ni6Sn8 28 -> 55,
        # burying cells that VESTA draws bare. Chains and frameworks get their
        # continuity from `BoundaryModifier` (round 39) instead, which is
        # automatic and restricted to covalent bonds crossing a face.
        groups = (fragment_info(content_symbols, content_frac, data.cell,
                                geometric=wholly_disordered)
                  if whole_molecules else None)
        add_symbols, add_frac = exterior_molecules(
            content_symbols, content_frac, groups)
        # A FRAMEWORK has no finite molecules to copy, and VESTA still draws
        # it running past the cell — so the covalent skeleton is followed one
        # shell outwards as well. Restricted to bonds involving a non-metal,
        # which is what separates a ZIF linker from an intermetallic.
        ext_symbols, ext_frac = bonded_exterior(
            content_symbols, content_frac, data.cell, depth=1,
            covalent_only=True)
        if ext_symbols:
            add_symbols = list(add_symbols) + list(ext_symbols)
            add_frac = (np.vstack([add_frac, ext_frac]) if len(add_frac)
                        else np.asarray(ext_frac))
        if len(add_symbols):
            keep = _unseen(add_symbols, add_frac, symbols, out)
            if keep.any():
                symbols = list(symbols) + [s for s, k in zip(add_symbols, keep)
                                           if k]
                out = np.vstack([out, np.asarray(add_frac)[keep]])
    if wrap and int(exterior) > 0:
        # Round 35's explicit "one more shell" operator, untouched: follow
        # every bond that leaves the cell and materialise the far end.
        ext_symbols, ext_frac = bonded_exterior(
            content_symbols, content_frac, data.cell, depth=int(exterior))
        if ext_symbols:
            keep = _unseen(ext_symbols, ext_frac, symbols, out)
            if keep.any():
                symbols = list(symbols) + [s for s, k in zip(ext_symbols, keep)
                                           if k]
                out = np.vstack([out, np.asarray(ext_frac)[keep]])
    if wrap and len(symbols) > len(sites):
        # Iterated to a FIXED POINT: removing a fragment can orphan whatever
        # was hanging off it, so one pass leaves a second generation behind
        # (2240539 went 388 -> 334 on the first sweep and still had 12 lone
        # hydrogens whose partner had just gone). Bounded, because each pass
        # must remove at least one atom to continue.
        for _pass in range(8):
            keep = _reaches_into_cell(symbols, out, matrix, len(sites),
                                      geometric=wholly_disordered)
            if keep.all():
                break
            symbols = [s for s, k in zip(symbols, keep) if k]
            out = out[keep]
    if report is not None:
        # Where the CELL CONTENT ends and the copies begin. Everything past
        # this index is a lattice translate of an atom before it — a boundary
        # copy, an exterior shell atom — which is what lets `display_bonds`
        # label the whole picture against the periodic graph without threading
        # provenance through every function that appends atoms.
        report["n_content"] = len(sites)
        # Which ASYMMETRIC-UNIT site each content atom came from. Purely
        # informational — nothing here behaves differently — but it is the
        # only way a caller can ask "what was this atom's occupancy?" without
        # re-deriving the expansion, and the 🧪 page needs exactly that to
        # tell a spatially distinct partial occupancy from a shared site.
        report["site_of"] = list(sites)
        # Which DRAWN atoms stand for a site shared by several species. Done
        # LAST, because everything above may have dropped atoms, renumbered
        # them, or appended copies — and a boundary copy of a solid-solution
        # site is still that site, so it has to inherit the composition or the
        # cell shows one pie sphere at the centre and eight plain ones at the
        # corners.
        composition = site_composition(data, tol=tol)
        if composition:
            table = {k: composition[s] for k, s in enumerate(sites)
                     if s in composition}
            table.update(_inherit_composition(
                out[:len(sites)], out[len(sites):], table, data.cell,
                offset=len(sites), tol=tol))
            if table:
                report["site_occupancy"] = {str(k): v
                                            for k, v in table.items()}
    return symbols, out @ matrix


def _inherit_composition(content, extra, table, cell, offset, tol=0.1):
    # type: (np.ndarray, np.ndarray, dict, Cell, int, float) -> dict
    """Give each appended copy the composition of the site it repeats.

    A boundary or exterior atom is an exact LATTICE TRANSLATE of one of the
    cell's own atoms, so matching on the fractional coordinate modulo 1
    identifies its source without having to thread site indices through every
    function that appends atoms.
    """
    if not len(extra) or not table:
        return {}
    matrix = cell.matrix()
    sources = sorted(table)
    ref = np.asarray(content, dtype=float)[sources]
    out = {}
    for k, row in enumerate(np.asarray(extra, dtype=float)):
        d = ref - row
        d = d - np.round(d)                      # minimum image
        near = np.linalg.norm(d @ matrix, axis=1)
        hit = int(np.argmin(near))
        if near[hit] <= tol:
            out[offset + k] = table[sources[hit]]
    return out


def shared_sites(data, tol=0.1):
    # type: (CifData, float) -> Dict[int, List[int]]
    """Sites that sit on TOP of each other -> {site index: its whole group}.

    A substitutional solid solution puts several elements on one
    crystallographic site, each with a fractional occupancy that sums to about
    one: `1547149.cif` has Nb 0.50, Ti 0.25, Ni 0.15 and Co 0.10 all at
    (0,0,0). Those are not disorder ALTERNATIVES in the round-38 sense (two
    positions for one atom); they are one position shared by four species, and
    it is what VESTA draws as a pie-slice sphere.

    Until round 42 they were lost in `expand`'s minimum-image de-duplication
    before occupancy was ever consulted, so MoloM drew that structure as pure
    NbO2 -- a composition the file never claimed, with nothing on screen or on
    the crystal page to say so.

    Grouped at the SAME tolerance the de-duplication uses, so this describes
    exactly the atoms that merging would otherwise silently discard.
    """
    frac = np.asarray(data.frac, dtype=float).reshape(-1, 3)
    n = len(frac)
    if n < 2:
        return {}
    matrix = data.cell.matrix()
    groups = {}               # type: Dict[int, List[int]]
    for i in range(n):
        if i in groups:
            continue
        d = frac - frac[i]
        d = d - np.round(d)                      # minimum image
        close = np.where(np.linalg.norm(d @ matrix, axis=1) <= tol)[0]
        if len(close) < 2:
            continue
        members = [int(k) for k in close]
        for k in members:
            groups[k] = members
    return groups


def site_composition(data, tol=0.1):
    # type: (CifData, float) -> Dict[int, List[Tuple[str, float]]]
    """`{site: [(element, occupancy), ...]}` for every SHARED site.

    Only sites genuinely shared by more than one species are returned, and
    only when their occupancies are fractional -- two symmetry-redundant rows
    for the same atom (the round-33 urea case, where pymatgen invents
    occupancy 2) are a duplicate, not a solid solution.
    """
    out = {}                  # type: Dict[int, List[Tuple[str, float]]]
    for site, members in shared_sites(data, tol=tol).items():
        parts = [(data.symbols[k], float(data.occupancy[k])) for k in members]
        elements_present = {sym for sym, _ in parts}
        if len(elements_present) < 2 and all(o >= 1.0 - 1e-6
                                             for _s, o in parts):
            continue
        if all(o >= 1.0 - 1e-6 for _s, o in parts):
            continue
        out[site] = sorted(parts, key=lambda p: -p[1])
    return out


def asym_view(data, tol=0.1):
    # type: (CifData, float) -> Tuple[List[str], np.ndarray, dict, List[List[int]]]
    """The asymmetric unit AS DRAWN: shared sites merged into one atom each.

    A CIF writes a substitutional solid solution as one `_atom_site_` row per
    species at the same coordinates - `1547149.cif` lists Nb 0.50, Ti 0.25,
    Ni 0.15 and Co 0.10 all at (0,0,0). The FULL CELL has drawn that correctly
    as a single pie sphere since round 42, because `expand`'s minimum-image
    merge collapses them; the asymmetric unit listed them verbatim and so drew
    four atoms stacked inside one another. Same structure, two pictures, and
    the one that looked broken was the one that had not lost anything.

    Only GENUINE shared sites are merged - `site_composition`'s test, i.e.
    more than one species with fractional occupancies. Two symmetry-redundant
    rows for the same atom (round 33's urea case) are a duplicate, not a solid
    solution, and collapsing those would change what the asymmetric unit shows
    for ordinary files. So the blast radius is exactly the files that have a
    solid solution in them.

    Returns `(symbols, frac, composition, rows)` where `composition` is
    `{drawn index as a STRING: [(element, occupancy), ...]}` - the shape
    `metadata["site_occupancy"]` already has, so the pie renders with no new
    drawing code - and `rows` gives, for each drawn atom, the asymmetric-unit
    rows it stands for. **`rows` is what makes the merge safe**: without it
    the write-back sees two atoms where the metadata has five and resets the
    occupancy column to 1.0, which is round 42's bug performed as a WRITE.
    """
    frac = np.asarray(data.frac, dtype=float).reshape(-1, 3)
    shared = site_composition(data, tol=tol)
    groups = shared_sites(data, tol=tol)
    symbols, out_frac, composition, rows = [], [], {}, []
    taken = set()
    for i in range(len(frac)):
        if i in taken:
            continue
        members = [i]
        parts = None
        if i in shared:
            members = sorted(groups.get(i, [i]))
            parts = shared[i]
        taken.update(members)
        # The row that REPRESENTS the site is the most occupied species, which
        # is what `site_composition` already sorts by - so the merged atom is
        # drawn in the colour of what the site mostly is, exactly as the full
        # cell draws it.
        lead = members[0]
        if parts:
            best = parts[0][0]
            for k in members:
                if data.symbols[k] == best:
                    lead = k
                    break
        drawn = len(symbols)
        symbols.append(data.symbols[lead])
        out_frac.append(frac[lead])
        # LEAD FIRST. The drawn atom carries the majority species' element, so
        # the write-back has to know which of the rows that element belongs
        # to - otherwise changing a pie atom's element either does nothing or
        # is applied to whichever row happened to sort first.
        ordered = [int(lead)] + [int(k) for k in members if int(k) != int(lead)]
        rows.append(ordered)
        if parts:
            composition[str(drawn)] = [(str(s), float(o)) for s, o in parts]
    return (symbols, np.asarray(out_frac, dtype=float).reshape(-1, 3),
            composition, rows)


def _is_new(f, fracs, matrix, tol, wrap):
    # type: (np.ndarray, List[np.ndarray], np.ndarray, float, bool) -> bool
    if not fracs:
        return True
    d = np.asarray(fracs) - f[None, :]
    if wrap:
        d = d - np.round(d)                  # minimum image
    return bool(np.min(np.linalg.norm(d @ matrix, axis=1)) > tol)


def periodic_pairs(symbols, frac, cell, slack=0.45, sanity=True, report=None):
    # type: (list, np.ndarray, Cell, float, bool, Optional[dict]) -> Tuple[List[tuple], np.ndarray]
    """Bonded pairs under the MINIMUM IMAGE convention, plus their distances.

    Ordinary bond perception measures straight-line distances, so a molecule
    straddling a cell face comes out cut in half. Here the shortest periodic
    image of each pair is used instead, with Avogadro's covalent criterion —
    and then the same VALENCE SANITY the molecular path applies, because a
    crystal is where impossible contacts actually turn up (a superimposed
    disorder alternative, a badly refined hydrogen, a file with a 0.75 A
    clash). Without it one bad contact fuses four molecules into a chain that
    percolates, and the whole cell then reads as a framework.
    """
    from . import bondgraph
    n = len(symbols)
    if n == 0:
        return [], np.zeros(0)
    graph = bondgraph.build(symbols, frac, cell, slack=slack, sanity=sanity,
                            report=report)
    # The graph carries one edge per (i, j, TRANSLATION), which is the honest
    # periodic answer and what the drawn picture is instantiated from. This
    # function answers the older, coarser question — "which atoms are bonded
    # to which?" — for the fragment walks, so the images of one pair collapse
    # to a single entry at the shortest of them, and an atom's bond to its own
    # image is not a pair at all. (`_touches_own_image` is what tells
    # `fragment_info` about those, and it needs no help from here.)
    best = {}
    for e in graph.edges:
        if e.i == e.j:
            continue
        key = (min(e.i, e.j), max(e.i, e.j))
        if key not in best or e.dist < best[key]:
            best[key] = e.dist
    pairs = sorted(best)
    return pairs, np.asarray([best[k] for k in pairs], dtype=float)


def display_bonds(symbols, coords, cell, n_content, slack=0.45, existing=None,
                  report=None, labels=None):
    # type: (list, np.ndarray, Cell, int, float, Optional[list], Optional[dict], Optional[list]) -> List[Tuple[int, int, int]]
    """The bonds of a DRAWN crystal, instantiated from the periodic graph.

    `labels` are the exact `(site, shift)` per drawn atom that `packing`
    records (`packing.exact_labels`). **Pass them whenever they exist.**
    Without them the content is taken to be the first `n_content` atoms and
    every other atom's label is recovered from its coordinate - and on any
    unwrapped crystal the first `n_content` atoms are NOT the content (round
    95: `complete_molecules` reorders and duplicates), so the graph is built
    on the wrong atoms and 205 of ferrocene's 210 come back mislabelled.
    That path is kept only for an edited structure, where no labels exist.

    Stage 5 of the pipeline, and the fix for the defect that made this whole
    module's arithmetic look sound and its pictures wrong: bonds used to be
    perceived from Cartesian coordinates AFTER the structure had been clipped
    to the cell, so anything crossing a face was simply not there. An atom
    lying exactly ON a face is drawn twice, once per face, and the two copies
    then split one coordination sphere between them — every Zn in ZIF-8 came
    out with three N instead of four, on all twelve, and adding boundary
    shells could not fix it because the ATOMS were present all along.

    Here the graph is built once on the cell content, every drawn atom is
    labelled `(content index, lattice shift)`, and the bonds are a lookup. A
    face atom's two copies carry different shifts, so each gets its own
    complete set of neighbours.

    `existing` bonds are kept verbatim (they may carry orders the user drew)
    and the graph only ADDS to them. Anything that is not a lattice translate
    of the content — a hand-drawn or edited atom — falls back to ordinary
    perception, so this degrades to the old behaviour rather than dropping it.

    **OVER-VALENCE IS DRAWN** (round 81). Christian's rule, and it is about
    what a CIF viewer is for: "We are visualising information that is recorded
    in a cif file, not the physical reality. If someone made a shit refinement,
    then the cif is flawed and the visualiser should reflect that... We should
    not be silently glossing over bad refinements."

    So the drawn bonds of a crystal are what the distance test says, and the
    valence cap is not applied here at all. A carbon with six hydrogens is
    what a methyl disordered over two orientations at full occupancy looks
    like, and drawing it that way is informative twice over: it is either a
    real physical limitation of the model or a bad refinement, and either way
    the picture should say so rather than quietly choosing a subset.

    That also dissolves the round-41 defect rather than patching it. Capping
    forced a choice about WHICH bond to sacrifice, the answer was wrong for
    crystals (every real C-C is longer than every real C-H, so "longest first"
    sacrificed the skeleton to keep duplicate hydrogens), and the atoms whose
    bonds were dropped were left floating unbonded because dropping a BOND
    does not drop an ATOM. Nothing is dropped now, so nothing floats.

    What is NOT relaxed: an impossibly short contact is still refused. That is
    not a valence judgement — no chemistry puts two nuclei at 0.5 of their
    covalent radii — and round 43's tick already exists for looking at those.
    `periodic_pairs` keeps the cap too, because it answers a different
    question: what belongs TOGETHER, for the fragment walks and the boundary
    completion. Round 42d's rule, from the other side.
    """
    from . import bondgraph, bonding
    from . import packing as packing_mod
    n = len(symbols)
    if n == 0:
        return list(existing or [])
    xyz = np.asarray(coords, dtype=float).reshape(n, 3)
    frac = cell.to_fractional(xyz)
    content = None
    if labels is not None and len(labels) == n:
        c_sym, c_frac = packing_mod.content_from(list(symbols), frac, labels)
        if c_sym is not None:
            content = (c_sym, c_frac)
    if content is None:
        if n_content <= 0:
            return list(existing or [])
        n_content = min(int(n_content), n)
        content = (list(symbols)[:n_content], frac[:n_content])
        labels = bondgraph.label_instances(frac, cell, n_content)
    graph = bondgraph.build(content[0], content[1], cell,
                            slack=slack, report=report,
                            valence=False, cap_hydrogens=False)
    bonds = [(int(i), int(j), 1) for i, j, _o in graph.instantiate(labels)]
    loose = [k for k, entry in enumerate(labels) if entry is None]
    if loose:
        # Atoms the graph cannot account for: perceive their bonds the
        # ordinary way rather than leaving them unbonded.
        extra = bonding.perceive_bonds(list(symbols), xyz,
                                       valence=False, cap_hydrogens=False)
        seen = {(i, j) for i, j, _o in bonds}
        loose_set = set(loose)
        bonds.extend((i, j, o) for i, j, o in extra
                     if (i in loose_set or j in loose_set)
                     and (i, j) not in seen)
    if existing:
        seen = {(min(int(i), int(j)), max(int(i), int(j)))
                for i, j, _o in bonds}
        # The graph is AUTHORITATIVE for the atoms it can label: a bond it
        # does not have is a bond that does not exist. Taking the union with
        # a separately perceived list instead was the round-43 trap one level
        # up — the two lists were valence-capped INDEPENDENTLY, so their union
        # was not (a disordered ZIF methyl came out with five bonds on a
        # carbon the graph had capped at four). Round 81 removed the cap from
        # both sides, so that particular disagreement is gone; the rule stays
        # because the graph is still the only thing that knows the periodic
        # answer, and a straight-line perception cannot see across a face.
        #
        # Only atoms the graph cannot account for keep their perceived bonds.
        loose_set = set(loose)
        keep = [(int(i), int(j), o) for i, j, o in existing
                if (min(int(i), int(j)), max(int(i), int(j))) not in seen
                and (int(i) in loose_set or int(j) in loose_set)]
        # An order the user drew survives; the graph supplies connectivity.
        orders = {(min(int(i), int(j)), max(int(i), int(j))): o
                  for i, j, o in existing}
        bonds = [(i, j, orders.get((min(i, j), max(i, j)), o))
                 for i, j, o in bonds] + keep
    return sorted({(min(i, j), max(i, j)): (min(i, j), max(i, j), o)
                   for i, j, o in bonds}.values())


def missing_partners(symbols, coords, cell, n_content, covalent_only=True,
                     max_added=20000):
    # type: (list, np.ndarray, Cell, int, bool, int) -> Tuple[List[str], np.ndarray]
    """Atoms named by the graph as bonded to the picture but not drawn yet.

    The bounded grow of stage 5: one shell, driven by the labelled edges
    rather than by re-perceiving geometry, so it terminates by construction
    even on a framework.

    WHICH bonds may be followed is the whole difficulty, and both rules here
    were learned the expensive way (round 42b) rather than reasoned out:

    * a COVALENT bond is followed only if it involves a NON-METAL. That is
      what separates a ZIF linker from an intermetallic, where metal-to-metal
      is covalent by design (round 38, so an SBU is not dissected) and every
      atom has 6-12 neighbours just outside any box you draw — Ni6Sn8 went
      28 -> 55 atoms and buried a cell VESTA draws bare.
    * a COORDINATION bond is followed only if the partner belongs to a
      covalent fragment of more than one atom. A ZIF's N is part of an
      imidazolate and carries a real molecule; rock salt's chloride is alone,
      and completing it sprouts a slab. `bond_kind` deliberately does not
      distinguish Zn-N from Na-Cl (both are metal-to-non-metal, and for
      "does this hold a molecule together?" both answer no), so the partner's
      own fragment is what has to be asked.

    Returns `(symbols, FRACTIONAL coords)` for the atoms to append.
    """
    from . import bondgraph, bonding
    n = len(symbols)
    if n == 0 or n_content <= 0:
        return [], np.zeros((0, 3))
    n_content = min(int(n_content), n)
    frac = cell.to_fractional(np.asarray(coords, dtype=float).reshape(n, 3))
    graph = bondgraph.build(list(symbols)[:n_content], frac[:n_content], cell)
    labels = bondgraph.label_instances(frac, cell, n_content)
    have = {entry for entry in labels if entry is not None}
    fragment_size = _covalent_fragment_sizes(graph)
    wanted = {}
    for entry in sorted(have):
        site, shift = entry
        for j, eshift, _dist in graph.neighbours(site):
            kind = bonding.bond_kind(symbols[site], symbols[j])
            if kind == bonding.COVALENT:
                if covalent_only and not (
                        not bonding.is_metal(symbols[site])
                        or not bonding.is_metal(symbols[j])):
                    continue
            elif covalent_only and fragment_size[j] < 2:
                continue
            key = (int(j), tuple(int(s) + int(t)
                                 for s, t in zip(shift, eshift)))
            if key in have or key in wanted:
                continue
            wanted[key] = graph.frac[j] + np.array(key[1], dtype=float)
            if len(wanted) >= int(max_added):
                break
    if not wanted:
        return [], np.zeros((0, 3))
    keys = sorted(wanted)
    return ([symbols[k[0]] for k in keys],
            np.asarray([wanted[k] for k in keys], dtype=float))


def _covalent_fragment_sizes(graph):
    # type: (object) -> np.ndarray
    """How many atoms are in each atom's COVALENT fragment, periodically.

    One atom means an ion in a lattice; more means a molecule worth carrying
    across a cell face. Counted over the periodic graph, so a linker split by
    the boundary still counts as one fragment.
    """
    from . import bonding
    n = len(graph.symbols)
    adjacency = [[] for _ in range(n)]
    for e in graph.edges:
        if bonding.bond_kind(graph.symbols[e.i],
                             graph.symbols[e.j]) != bonding.COVALENT:
            continue
        adjacency[e.i].append(e.j)
        adjacency[e.j].append(e.i)
    sizes = np.ones(n, dtype=int)
    seen = [False] * n
    for seed in range(n):
        if seen[seed]:
            continue
        seen[seed] = True
        group, stack = [seed], [seed]
        while stack:
            i = stack.pop()
            for j in adjacency[i]:
                if not seen[j]:
                    seen[j] = True
                    group.append(j)
                    stack.append(j)
        for k in group:
            sizes[k] = len(group)
    return sizes


def periodic_neighbours(symbols, frac, cell, slack=0.45, covalent_only=False,
                        sanity=True):
    # type: (list, np.ndarray, Cell, float, bool, bool) -> List[List[int]]
    """`periodic_pairs` as adjacency lists.

    `covalent_only` drops the metal-ligand bonds, which is how an infinite
    framework becomes a set of finite molecules — see `bonding.bond_kind`.
    Use it for the question "what is a molecule here"; use the full graph for
    "what should be drawn joined", which is not the same question.
    """
    from . import bonding
    n = len(symbols)
    adj = [[] for _ in range(n)]
    pairs, _dists = periodic_pairs(symbols, frac, cell, slack, sanity=sanity)
    for i, j in pairs:
        if covalent_only and bonding.bond_kind(symbols[i],
                                               symbols[j]) != bonding.COVALENT:
            continue
        adj[i].append(j)
        adj[j].append(i)
    return adj


def unwrap_molecules(symbols, frac, cell, tol=1e-3, geometric=False):
    # type: (list, np.ndarray, Cell, float) -> np.ndarray
    """Make every bonded fragment CONTIGUOUS across the cell boundary —
    unless that fragment is a PERIODIC NETWORK, which cannot be.

    Wrapping each atom into [0, 1) on its own strands hydrogens on the far
    face, so a molecular crystal wants its fragments walked back together
    (what CCDC/Mercury show). A FRAMEWORK is the opposite case: its bonded
    component percolates through the boundary and is infinite, so walking it
    marches the structure out across cells forever — MOF-5 came out sprawled
    over 4x2x2 cells instead of filling one.

    The two are told apart while walking: if a bond leads back to an atom
    already placed but demands a DIFFERENT periodic image than the one it
    already has, the component closes on itself through the boundary and is
    therefore periodic. Such a fragment is left plainly wrapped.
    """
    n = len(symbols)
    if n == 0:
        return frac
    # `geometric` groups on proximity rather than chemistry, for the wholly
    # disordered case — see `fragment_info`. A smeared molecule has to be made
    # contiguous as ONE entity or the boundary cannot carry it whole.
    adj = periodic_neighbours(symbols, frac, cell, sanity=not geometric)
    wrapped = np.array(frac, dtype=float)
    out = np.array(frac, dtype=float)
    seen = np.zeros(n, dtype=bool)
    for seed in range(n):
        if seen[seed]:
            continue
        seen[seed] = True
        fragment = [seed]
        stack = [seed]
        periodic = False
        while stack:
            i = stack.pop()
            for j in adj[i]:
                d = wrapped[j] - wrapped[i]
                target = out[i] + (d - np.round(d))    # nearest image of j
                if not seen[j]:
                    seen[j] = True
                    out[j] = target
                    fragment.append(j)
                    stack.append(j)
                elif np.any(np.abs(out[j] - target) > tol):
                    # Two different routes to the same atom disagree by a
                    # lattice vector: this component wraps onto itself.
                    periodic = True
        if periodic:
            out[fragment] = wrapped[fragment]          # leave it wrapped
            continue
        # Bring the fragment back so its CENTROID is in [0, 1). The nudge
        # matters: a molecule sitting exactly on a cell face (urea's, by
        # symmetry) has a centroid of exactly 1.0, and whether floor() sees
        # 1.0 or 0.99999 then decides between drawing it inside the box and
        # dumping it outside — which is what made urea's asymmetric unit
        # look like it had been left out of its own unit cell.
        shift = np.floor(out[fragment].mean(axis=0) + 1e-6)
        if np.any(shift):
            out[fragment] -= shift
    return out


def _walk_components(indices, adj, frac, matrix, radii, shifts, tol):
    # type: (Sequence, list, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float) -> List[tuple]
    """[(group, is_periodic), ...] for the components of `indices` under
    `adj`. Shared by the full-connectivity pass and the covalent-only one."""
    wanted = set(int(i) for i in indices)
    placed = np.array(frac, dtype=float)
    seen = set()
    out = []
    for seed in sorted(wanted):
        if seed in seen:
            continue
        seen.add(seed)
        group, stack, periodic = [seed], [seed], False
        while stack:
            i = stack.pop()
            for j in adj[i]:
                if j not in wanted:
                    continue
                d = frac[j] - frac[i]
                target = placed[i] + (d - np.round(d))
                if j not in seen:
                    seen.add(j)
                    placed[j] = target
                    group.append(j)
                    stack.append(j)
                elif np.any(np.abs(placed[j] - target) > tol):
                    periodic = True
        group = sorted(group)
        if not periodic:
            periodic = _touches_own_image(placed[group], radii[group],
                                          matrix, shifts)
        out.append((group, periodic))
    return out


def fragment_info(symbols, frac, cell, tol=1e-3, split_coordination=True,
                  geometric=False):
    # type: (list, np.ndarray, Cell, float, bool) -> List[tuple]
    """[(indices, is_periodic), ...] for each bonded component.

    **A periodic component is then cut at its COORDINATION bonds** (round 38,
    Christian's diagnosis). Mercury knows to stop after the carboxylate
    because a Zn-O bond is coordinative and that is the logical place to cut;
    doing the same here turns MIL-53's one 152-atom infinite component into 8
    linkers, 8 hydroxide bridges, 8 waters and 8 aluminium centres — every one
    finite, and therefore completable at the cell boundary. Rock salt cuts the
    same way into single ions, which is exactly the per-atom completion the
    boundary already wanted.

    The cut is applied ONLY to components that came back periodic. A finite
    metal complex — ferrocene, a paddlewheel with its ligands — is already a
    molecule, and dissecting it would strand the ligands from their metal at
    the boundary. Cut where it is infinite, nowhere else: that is the whole
    hierarchy.

    `is_periodic` marks a component that is INFINITE — a framework, or an
    ionic lattice like rock salt where Na and Cl fall inside the covalent
    criterion. Such a component cannot be "completed" into a molecule; a
    finite one can, and must be, or the cell shows half molecules.

    Two independent tests, because either alone gets a real case wrong:

    * a walk that reaches the same atom by two routes disagreeing by a
      lattice vector (what `unwrap_molecules` uses) — catches MOF-5;
    * whether the component BONDS TO ITS OWN LATTICE IMAGE. Rock salt has
      only two atoms in the cell, so there is no second route to find and
      the walk says "finite" — but Na(0,0,0) is 2.48 A from the Cl of the
      cell next door, which is exactly what makes it a lattice rather than
      an NaCl molecule. Urea's molecule, by contrast, is 2.7 A clear of its
      own image at the nearest approach and only H-bonded to it, which is
      beyond the covalent criterion.
    """
    from . import elements
    frac = np.asarray(frac, dtype=float).reshape(-1, 3)
    n = len(symbols)
    if n == 0:
        return []
    matrix = cell.matrix()
    radii = np.array([elements.radius_covalent(elements.atomic_number(s))
                      or 2.0 for s in symbols])
    shifts = np.array([[a, b, c] for a in (-1, 0, 1) for b in (-1, 0, 1)
                       for c in (-1, 0, 1)
                       if (a, b, c) != (0, 0, 0)], dtype=float)
    # `geometric=True` groups on PROXIMITY instead of chemistry (round 42d,
    # from Christian's reading of VESTA). The sanity filters exist to stop us
    # DRAWING bonds that cannot exist, and they are right for that; they are
    # wrong for deciding what belongs TOGETHER, because a disorder alternative
    # half an Angstrom from its partner is part of the same molecular entity
    # precisely BECAUSE it is too close to be bonded to it. On `2240539.cif`
    # (cyclohexane smeared over Fm-3m) the chemistry graph calls 474 contacts
    # impossible and shatters the molecule into 134 loose atoms and 73 pairs,
    # so nothing could be completed at the boundary; the geometric graph gives
    # exactly 4 components of 70 atoms — the four F-centred lattice points,
    # i.e. VESTA's polyhedral ball at every corner and face centre.
    #
    # It is NOT the default, and the discriminator is occupancy: round 38's
    # HpPyBz case is a spurious 0.75 A contact between two FULLY occupied
    # molecules, and grouping those together fuses two molecules that are
    # genuinely separate (a test pins it). Only a wholly disordered structure,
    # where every site is partial, gets the geometric graph.
    adj = periodic_neighbours(symbols, frac, cell, sanity=not geometric)
    whole = _walk_components(range(n), adj, frac, matrix, radii, shifts, tol)
    if not split_coordination or not any(p for _g, p in whole):
        return whole
    cov_adj = periodic_neighbours(symbols, frac, cell, covalent_only=True)
    out = []
    for group, periodic in whole:
        if not periodic:
            out.append((group, periodic))
            continue
        out.extend(_walk_components(group, cov_adj, frac, matrix, radii,
                                    shifts, tol))
    return out


def _touches_own_image(block, radii, matrix, shifts):
    # type: (np.ndarray, np.ndarray, np.ndarray, np.ndarray) -> bool
    """Does this component bond to a lattice translation of itself?"""
    cart = block @ matrix
    limit = radii[:, None] + radii[None, :] + 0.45
    for shift in shifts:
        other = cart + (shift @ matrix)[None, :]
        d = np.linalg.norm(cart[:, None, :] - other[None, :, :], axis=2)
        if np.any((d > 0.32) & (d < limit)):
            return True
    return False


def fragments(symbols, frac, cell):
    # type: (list, np.ndarray, Cell) -> List[List[int]]
    """Bonded groups of atom indices, using the minimum image."""
    adj = periodic_neighbours(symbols, frac, cell)
    n = len(symbols)
    seen = [False] * n
    out = []
    for seed in range(n):
        if seen[seed]:
            continue
        seen[seed] = True
        group, stack = [seed], [seed]
        while stack:
            i = stack.pop()
            for j in adj[i]:
                if not seen[j]:
                    seen[j] = True
                    group.append(j)
                    stack.append(j)
        out.append(sorted(group))
    return out


def direct_pairs(symbols, frac, cell, pairs, slack=0.45):
    # type: (list, np.ndarray, Cell, Sequence, float) -> List[tuple]
    """Which of `pairs` are adjacent AT THESE COORDINATES, no minimum image.

    The companion to `periodic_neighbours`: that one answers "are these two
    bonded in the crystal", this one answers "may I draw a straight line
    between them right here". They differ for exactly the pairs that meet
    only across a cell face, and drawing those is what put lines clean across
    the box in the symmetry ghosts. `unwrap_molecules` removes the problem for
    any FINITE fragment; a periodic one cannot be unwrapped at all (see
    `fragment_info`), so the drawing side still needs to be able to ask.
    """
    from . import elements
    frac = np.asarray(frac, dtype=float).reshape(-1, 3)
    pairs = [(int(i), int(j)) for i, j in pairs]
    if not pairs:
        return []
    cart = frac @ cell.matrix()
    radii = np.array([elements.radius_covalent(elements.atomic_number(s))
                      for s in symbols], dtype=float)
    radii[radii <= 0] = 2.0
    out = []
    for i, j in pairs:
        if not (0 <= i < len(cart) and 0 <= j < len(cart)):
            continue
        d = float(np.linalg.norm(cart[i] - cart[j]))
        if 0.32 < d < radii[i] + radii[j] + slack:
            out.append((i, j))
    return out


def _reaches_into_cell(symbols, frac, matrix, n_content, tol=0.02,
                       geometric=False):
    # type: (list, np.ndarray, np.ndarray, int, float, bool) -> np.ndarray
    """Mask dropping any COPY whose whole fragment lies outside the cell.

    The final honesty check on the picture, and the one rule both reference
    viewers state outright: Mercury includes a molecule when ANY of its atoms
    falls in the cell, and VESTA searches outward from atoms that are inside.
    A fragment with nothing inside is therefore not part of this cell's
    picture at all — it is a copy that some earlier step placed and then had
    no reason to keep.

    Round 42b: several mechanisms can produce one, which is exactly why the
    check belongs here rather than in each of them. `boundary_images` carries
    an atom's whole MOLECULE (round 33), so when that molecule straddles a
    face — stored split because it could not be unwrapped — translating it
    bodily throws its far half a full cell further out (H2Mg2O8P2's two Mg
    landed at z = 1.94, and the boundary modifier then made eleven of them).
    Christian saw the same thing as triazoles floating a cell away from
    Cu_trz_tet, and as 12 loose atoms round 2240539.

    Only COPIES are considered: the cell CONTENT is never touched, so Z, the
    ❖ page's count and anything counting formula units stay exactly as they
    were.

    `geometric` groups on proximity rather than chemistry, for the wholly
    disordered case — and it matters MORE here than anywhere else, because
    this function decides what survives. On the chemistry graph a cage
    shatters into shards, each judged separately, so the shards that happen to
    lie outside are dropped and what is left is a TRUNCATED cage: 2240539 came
    back with 45-, 19-, 18- and 17-atom stumps whose centroids sat at 0.93
    instead of on a lattice point. A fragment must be judged as the whole
    thing it is, or "keep the molecule if any atom is inside" is not the rule
    being applied.
    """
    from . import bonding
    n = len(symbols)
    keep = np.ones(n, dtype=bool)
    if n <= n_content:
        return keep
    # Bonds are perceived in CARTESIAN space, like everywhere else.
    cart = np.asarray(frac, dtype=float) @ matrix
    inside = np.all((frac > -tol) & (frac < 1.0 + tol), axis=1)
    if inside.all():
        return keep
    bonds = bonding.perceive_bonds(list(symbols), cart,
                                   sanity=not geometric)
    adj = {}
    for i, j, _order in bonds:
        adj.setdefault(i, []).append(j)
        adj.setdefault(j, []).append(i)
    seen = set()
    for start in range(n):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in adj.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        if any(inside[i] for i in comp):
            continue
        for i in comp:
            if i >= n_content:
                keep[i] = False
    return keep


def _unseen(new_symbols, new_frac, symbols, frac, tol=1e-3):
    # type: (list, np.ndarray, list, np.ndarray, float) -> np.ndarray
    """Boolean mask of the candidates not already drawn, compared on POSITION.

    Keyed on where an atom IS, never on `(site, image)`: that key assumes
    every input atom is the (0,0,0) image of its site, which stops being true
    the moment the input carries boundary copies (round 39's lesson, which
    cost a structure 6389 atoms).
    """
    new_frac = np.asarray(new_frac, dtype=float).reshape(-1, 3)
    if not len(new_frac):
        return np.zeros(0, dtype=bool)
    seen = set()
    quant = 1.0 / max(tol, 1e-9)
    for sym, row in zip(symbols, np.asarray(frac, dtype=float).reshape(-1, 3)):
        seen.add((sym,) + tuple(np.round(row * quant).astype(np.int64)))
    keep = np.zeros(len(new_frac), dtype=bool)
    for i, (sym, row) in enumerate(zip(new_symbols, new_frac)):
        key = (sym,) + tuple(np.round(row * quant).astype(np.int64))
        if key not in seen:
            seen.add(key)
            keep[i] = True
    return keep


def exterior_molecules(symbols, frac, groups=None, tol=1e-4, span=1):
    # type: (list, np.ndarray, Optional[list], float, int) -> Tuple[List[str], np.ndarray]
    """Every neighbouring-cell copy of a molecule that REACHES INTO the cell.

    This is what VESTA and Mercury actually draw, and it is the reason their
    pictures are ringed with molecules while ours stopped at the box. The two
    viewers wrap atoms INDIVIDUALLY into the cell and then complete each
    molecule outwards, so a molecule straddling a face is drawn whole, sitting
    half outside. MoloM instead wraps by MOLECULE (round 19, so a fragment is
    never cut in half), which pulls that same molecule bodily inside — correct
    cell CONTENT, but a picture with no context around it.

    So the rule here is Mercury's packing rule stated directly: **draw a
    molecule if any of its atoms falls inside the closed cell**, including the
    copies of it that live in the 26 surrounding cells. `boundary_images`
    already does this for molecules sitting exactly ON the boundary; this is
    the same idea with the criterion loosened from "on the face" to "reaches
    in at all".

    A PERIODIC component is infinite and is skipped: every shell of a
    framework looks as unfinished as the last, and `BoundaryModifier` is what
    closes those bonds. Returns the ADDED atoms.
    """
    frac = np.asarray(frac, dtype=float).reshape(-1, 3)
    if not len(frac):
        return [], np.zeros((0, 3))
    if groups is None:
        groups = [([i], True) for i in range(len(frac))]
    lo, hi = -tol, 1.0 + tol
    shifts = [np.array((dx, dy, dz), dtype=float)
              for dx in range(-span, span + 1)
              for dy in range(-span, span + 1)
              for dz in range(-span, span + 1)
              if (dx, dy, dz) != (0, 0, 0)]
    out_symbols = []          # type: List[str]
    out_frac = []             # type: List[np.ndarray]
    for indices, periodic in groups:
        # A one-atom component is not a molecule to complete. Repeating a
        # lone ion into its neighbouring cells just duplicates lattice sites
        # -- which is `boundary_images`' job, done there only for atoms that
        # actually lie ON a face -- and it is round 33's NaCl lesson turning
        # up one function further out: BaLiF3 grew from 15 atoms to 25.
        if periodic or len(indices) < 2:
            continue
        block = frac[list(indices)]
        for shift in shifts:
            moved = block + shift
            inside = np.all((moved > lo) & (moved < hi), axis=1)
            if not inside.any():
                continue
            for k, idx in enumerate(indices):
                out_symbols.append(symbols[idx])
                out_frac.append(moved[k])
    if not out_frac:
        return [], np.zeros((0, 3))
    return out_symbols, np.asarray(out_frac)


def boundary_images(symbols, frac, groups=None, tol=1e-4):
    # type: (list, np.ndarray, Optional[list], float) -> Tuple[List[str], np.ndarray]
    """Complete the closed cell [0,1]^3 by repeating what lies ON it.

    A site at a corner, edge or face belongs to every equivalent position of
    the closed box, and that is how crystallography is drawn everywhere
    (Mercury, VESTA, Diamond): rock salt is **eight** sodiums at the corners
    around one chlorine in the middle, not one sodium at one corner. The
    content of the cell is unchanged — Z is still Z — these are the same
    atoms seen from the neighbouring cells, and drawing them is what makes
    the picture read as a repeating structure instead of a lone fragment.

    Whether the ATOM is on the boundary decides IF a copy is made; what gets
    copied is the atom's whole MOLECULE, so the copy is a molecule rather
    than a stray carbon and oxygen floating at the far face with nothing
    attached — which is what urea looked like, its C and O sitting on the x
    face with their NH2 groups left behind on the other side. Both reference
    viewers do this: VESTA draws atoms outside the boundary that are bonded
    to atoms inside it, and Mercury includes a whole molecule when any of its
    atoms falls in the cell.

    A PERIODIC component (a framework, or an ionic lattice like rock salt
    where Na and Cl fall inside the covalent criterion) is infinite and
    cannot be completed, so only the atom itself is copied. That is what
    keeps NaCl at eight corner sodiums instead of sprouting a slab.

    `groups` is `[(indices, is_periodic), ...]` from `fragment_info`; without
    it every atom is treated as its own periodic component, i.e. per-atom.

    Returns the ADDED atoms.
    """
    frac = np.asarray(frac, dtype=float).reshape(-1, 3)
    if groups is None:
        groups = [([i], True) for i in range(len(frac))]
    member = {}
    for group, periodic in groups:
        for index in group:
            member[index] = (group, periodic)
    out_symbols = []
    out_frac = []
    seen = set()
    for index in range(len(frac)):
        group, periodic = member.get(index, ([index], True))
        carried = [index] if periodic else group
        # Which lattice shifts this copy may take. For a PERIODIC component
        # only the atom itself travels, so its own coordinates decide. For a
        # whole molecule the question is about the MOLECULE, so the options
        # are pooled over its atoms: a cage sitting on a corner has atoms on
        # the x, y and z faces but NONE with all three coordinates at zero, so
        # per-atom shifts can only ever produce seven of the eight corners.
        # That was the last of Christian's missing polyhedra on 2240539.
        probe = [index] if periodic else group
        options = []
        for axis in range(3):
            shifts = [0.0]
            if any(abs(frac[k][axis]) <= tol for k in probe):
                shifts.append(1.0)
            if any(abs(frac[k][axis] - 1.0) <= tol for k in probe):
                shifts.append(-1.0)
            options.append(shifts)
        for da in options[0]:
            for db in options[1]:
                for dc in options[2]:
                    if da == 0.0 and db == 0.0 and dc == 0.0:
                        continue
                    delta = np.array([da, db, dc])
                    for k in carried:
                        point = frac[k] + delta
                        key = (k, da, db, dc)
                        if key in seen:
                            continue
                        seen.add(key)
                        out_symbols.append(symbols[k])
                        out_frac.append(point)
    if not out_frac:
        return [], np.zeros((0, 3))
    return out_symbols, np.asarray(out_frac)


def bonded_exterior(symbols, frac, cell, depth=1, slack=0.45, tol=1e-3,
                    dup_tol=0.1, covalent_only=False,
                    finite_only=False):
    # type: (list, np.ndarray, Cell, int, float, float) -> Tuple[List[str], np.ndarray]
    """Atoms OUTSIDE the cell that are bonded to atoms inside it.

    This is VESTA's "search atoms bonded to atoms in the boundary", and it is
    a different operation from `boundary_images`: that one repeats sites lying
    exactly ON a face onto their equivalent faces, which completes the closed
    box but adds nothing beyond it. A chain or a framework does not stop at
    the face — it carries on into the next cell — so with the box alone the
    picture is a set of stubs cut off at the boundary, with the bonds that
    would explain them missing. Christian's side-by-side against VESTA is
    exactly that: MoloM's chains ended at the wall, VESTA's ran on.

    Each shell follows every bond that leaves the current set and materialises
    the periodic image at the far end. `depth` shells are taken; one is enough
    to close every bond that crosses a face, which is what makes the structure
    read as continuous. Deeper is available because a framework's node can sit
    a couple of bonds beyond the wall.

    Returns the ADDED atoms, in fractional coordinates that deliberately lie
    outside [0, 1) — that is the whole point of them. The cell CONTENT is
    untouched, so anything counting Z must keep using `expand(boundary=False)`.

    `covalent_only` and `finite_only` default OFF here, which keeps this the
    round-35 operation: an explicit "show me one more shell", valid on a chain
    or a framework that runs on forever. `BoundaryModifier` turns both ON,
    because an AUTOMATIC closure has a narrower job — completing a molecule
    that a cell face happened to cut — and drawing endless shells of a lattice
    nobody asked about is not that.
    """
    from . import bonding, elements
    frac = np.asarray(frac, dtype=float).reshape(-1, 3)
    n = len(symbols)
    if n == 0 or int(depth) < 1:
        return [], np.zeros((0, 3))
    m = cell.matrix()
    radii = np.array([elements.radius_covalent(elements.atomic_number(s))
                      for s in symbols])
    radii[radii <= 0] = 2.0
    # The 27 neighbouring images; a bond can only ever reach an adjacent cell
    # for any physical covalent radius against any physical cell edge.
    shifts = np.array([[a, b, c] for a in (-1.0, 0.0, 1.0)
                       for b in (-1.0, 0.0, 1.0) for c in (-1.0, 0.0, 1.0)])
    # Keyed by (base site, integer image) so a copy reached from two different
    # directions is added once. The base atoms are image (0,0,0) by
    # construction — `frac` here is already the unwrapped cell content.
    seen = set((i, 0, 0, 0) for i in range(n))
    start = range(n)
    if finite_only:
        # Only a FINITE fragment can be completed. A periodic component is
        # infinite by definition — a metal lattice, an intermetallic, graphite
        # — so following it outward just draws one more shell of something
        # that never ends, and every shell looks as unfinished as the last.
        # The severed-linker problem is a MOLECULE cut by a face, and this is
        # the same "molecule, unless that is impossible" hierarchy round 38
        # uses everywhere else.
        infinite = set()
        for group, periodic in fragment_info(symbols, frac, cell):
            if periodic:
                infinite.update(group)
        start = [i for i in range(n) if i not in infinite]
        if not start:
            return [], np.zeros((0, 3))
    frontier = [(i, np.zeros(3)) for i in start]
    out_symbols = []          # type: List[str]
    out_frac = []             # type: List[np.ndarray]
    # Positions that already exist, to dedupe against. The (site, image) key
    # above is NOT enough: it assumes every input atom is the (0,0,0) image of
    # its site, which is false the moment the input carries BOUNDARY COPIES —
    # `expand(boundary=True)` puts atoms outside [0,1) by design. Without this
    # a structure with 777 boundary copies grew to 6389 atoms, each copy
    # sprouting its own duplicate shell.
    placed = np.array(frac, dtype=float)
    for _ in range(int(depth)):
        nxt = []
        for site, image in frontier:
            here = frac[site] + image
            # Every image of every base site, vectorised: (n, 27, 3).
            deltas = (frac[:, None, :] + shifts[None, :, :]) - here[None, None, :]
            dist = np.linalg.norm(deltas @ m, axis=2)
            limit = (radii[:, None] + radii[site]) + slack
            hits = np.argwhere((dist > 0.32) & (dist < limit))
            for other, si in hits:
                other = int(other)
                if covalent_only and (
                        bonding.is_metal(symbols[site])
                        and bonding.is_metal(symbols[other])):
                    # Metal-to-metal is COVALENT by `bond_kind`, deliberately,
                    # so an SBU is not dissected (round 38). For growing a
                    # shell it is the wrong answer: an intermetallic is metal
                    # bonded to metal in every direction, and following it
                    # buried Ni6Sn8's 28-atom cell under 55 (round 42, against
                    # VESTA). Following the covalent skeleton means following
                    # bonds that involve a NON-metal.
                    continue
                if covalent_only and bonding.bond_kind(
                        symbols[site], symbols[other]) != bonding.COVALENT:
                    # A MOLECULE cut by a cell face is the thing that needs
                    # closing; a coordination bond is where a framework is
                    # SUPPOSED to be cut (round 38). Following those too turns
                    # rock salt's 9-atom cell into 59 and an intermetallic
                    # into a solid shell, for no gain: measured on the real
                    # files, every one of MOF-5's 24 cross-face bonds is a
                    # covalent C-C inside a linker, and every one of NaCl's is
                    # ionic.
                    continue
                new_image = image + shifts[si]
                key = (other, int(round(new_image[0])),
                       int(round(new_image[1])), int(round(new_image[2])))
                if key in seen:
                    continue
                seen.add(key)
                point = frac[other] + new_image
                # Only atoms that actually left the box are "exterior"; an
                # image landing back inside is a duplicate of something the
                # cell already contains.
                if np.all(point > -tol) and np.all(point < 1.0 + tol):
                    continue
                if float(np.min(np.linalg.norm(
                        (placed - point) @ m, axis=1))) < dup_tol:
                    continue          # something is already drawn right here
                placed = np.vstack([placed, point])
                out_symbols.append(symbols[other])
                out_frac.append(point)
                nxt.append((other, new_image))
        frontier = nxt
        if not frontier:
            break
    if not out_frac:
        return [], np.zeros((0, 3))
    return out_symbols, np.asarray(out_frac)


def _contiguous(frac, adj, group, seed):
    # type: (np.ndarray, list, Sequence, int) -> dict
    """{atom: position} for one fragment, walked out from `seed` by the
    minimum image so the piece is whole wherever the cell faces fall."""
    members = set(int(k) for k in group)
    pos = {int(seed): np.array(frac[int(seed)], dtype=float)}
    stack = [int(seed)]
    while stack:
        i = stack.pop()
        for j in adj[i]:
            if j in pos or j not in members:
                continue
            d = frac[j] - frac[i]
            pos[j] = pos[i] + (d - np.round(d))
            stack.append(j)
    return pos


def crossing_fragments(symbols, frac, cell, slack=0.45, dup_tol=0.1):
    # type: (list, np.ndarray, Cell, float, float) -> Tuple[List[str], np.ndarray]
    """Whole MOLECULES on the far side of every bond that crosses a face.

    The shell walk in `bonded_exterior` closes each cut bond with a single
    atom, which leaves a ZIF's imidazolate hanging off the face as three atoms
    of a five-ring — better than a severed stub, still not a molecule. Round
    33 settled this for the boundary copies and the same answer applies here:
    a copy carries its whole fragment, because half a ring is not a thing that
    exists.

    Only FINITE covalent fragments travel (see `fragment_info`): a lattice or
    a covalently infinite chain has no "whole molecule" to bring, and trying
    would just draw another shell of something endless.
    """
    from . import bonding
    frac = np.asarray(frac, dtype=float).reshape(-1, 3)
    n = len(symbols)
    if n == 0:
        return [], np.zeros((0, 3))
    m = cell.matrix()
    owner = {}
    for group, periodic in fragment_info(symbols, frac, cell):
        if not periodic:
            for i in group:
                owner[i] = group
    if not owner:
        return [], np.zeros((0, 3))
    adj = periodic_neighbours(symbols, frac, cell, slack=slack,
                              covalent_only=True)
    placed = np.array(frac, dtype=float)
    out_symbols, out_frac = [], []
    for i in sorted(owner):
        for j in adj[i]:
            if j not in owner:
                continue
            d = frac[j] - frac[i]
            shift = -np.round(d)
            if not np.any(shift):
                continue              # same cell: this bond is already drawn
            # ASSEMBLE THE FRAGMENT AROUND j FIRST. A fragment that itself
            # straddles the face is stored in pieces — it could not be
            # unwrapped, because the component it belongs to percolates — so
            # translating it rigidly keeps it in pieces and throws the far
            # half TWO cells out, where it hangs in space bonded to nothing.
            local = _contiguous(frac, adj, owner[j], j)
            offset = (frac[j] + shift) - local[j]
            for k, point in local.items():
                point = point + offset
                if float(np.min(np.linalg.norm(
                        (placed - point) @ m, axis=1))) < dup_tol:
                    continue
                placed = np.vstack([placed, point])
                out_symbols.append(symbols[k])
                out_frac.append(point)
    if not out_frac:
        return [], np.zeros((0, 3))
    return out_symbols, np.asarray(out_frac)


#: A fit whose atoms are further than this from being rigidly related to the
#: reference is not a rigid motion and must not be reported as one. Float
#: noise from a genuine transform is around 1e-14 A even after a long chain
#: of rotations, and any edit somebody makes on purpose is orders above this -
#: so there is no case in the gap.
RIGID_TOLERANCE = 1e-4


def rigid_from_reference(ref, cur, max_rmsd=RIGID_TOLERANCE):
    # type: (np.ndarray, np.ndarray, Optional[float]) -> Optional[Tuple[np.ndarray, np.ndarray]]
    """Kabsch fit: the rotation+translation carrying `ref` onto `cur`.

    **None when the fit is not RIGID**, which is the whole of `max_rmsd`. A
    Kabsch fit always returns something - it is a least-squares answer - and
    for atoms that have been EDITED rather than moved that answer is a
    rotation nobody performed. Measured: dragging one atom of ferrocene 6 A
    tilted the cell box by 0.41 A, because that atom was one of the 24 in the
    reference sample. Asking whether the residual is float noise is the
    difference between "the crystal was moved" and "the crystal was changed",
    and every caller here wants the first.

    Pass `max_rmsd=None` to get the old unconditional least-squares answer.

    This is how the cell box FOLLOWS its molecule. The alternative — hooking
    every transform path (grab, rotate, tumble, align, apply, undo) to move
    the cell too — was rejected: a plain grab moves atom coordinates without
    touching the object's origin, so there is no single frame to hang the
    cell off. Re-deriving the rigid motion from a handful of reference atoms
    each frame is exact for any rigid transform, costs a 3x3 SVD, and updates
    LIVE during a drag because it is computed while painting.

    Returns (R, t) with world = ref @ R.T + t, or None if it cannot be fit.
    """
    ref = np.asarray(ref, dtype=float)
    cur = np.asarray(cur, dtype=float)
    if ref.ndim != 2 or ref.shape != cur.shape or ref.shape[0] < 3:
        return None
    c_ref = ref.mean(axis=0)
    c_cur = cur.mean(axis=0)
    h = (ref - c_ref).T @ (cur - c_cur)
    try:
        u, _s, vt = np.linalg.svd(h)
    except np.linalg.LinAlgError:
        return None
    d = 1.0 if np.linalg.det(vt.T @ u.T) > 0 else -1.0
    rot = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    trans = c_cur - rot @ c_ref
    if max_rmsd is not None:
        residual = ref @ rot.T + trans - cur
        rmsd = float(np.sqrt((residual * residual).sum(axis=1).mean()))
        if rmsd > float(max_rmsd):
            return None
    return rot, trans


def reference_sample(coords, limit=24):
    # type: (np.ndarray, int) -> List[int]
    """Indices of a spread-out sample of atoms to pin the cell frame to.

    Evenly spaced rather than the first N: three atoms of one benzene ring
    are nearly coplanar, and a degenerate reference makes the recovered
    rotation unstable.
    """
    n = len(coords)
    if n <= limit:
        return list(range(n))
    step = n / float(limit)
    return sorted({int(k * step) for k in range(limit)})


def build_view(cell, asym_symbols, asym_frac, symops, mode="cell",
               na=1, nb=1, nc=1, tol=0.1, exterior=0, occupancy=None,
               disorder=POLICY_DOMINANT, report=None, shell_molecules=False,
               disorder_groups=None, disorder_assemblies=None, packed=True,
               outside=True, grow_from_copies=True):
    # type: (Cell, list, list, list, str, int, int, int, float, int, Optional[Sequence], str, Optional[dict]) -> Tuple[List[str], np.ndarray]
    """The atoms for one crystal display mode, in CARTESIAN coordinates.

    - "asym"     the asymmetric unit exactly as the file lists it
    - "cell"     one full unit cell (every symmetry operator applied)
    - "packing"  an na x nb x nc block of full cells

    `exterior` shells of VESTA-style bonded-outside-the-box atoms are added
    to the cell and packing modes (never to "asym", which is by definition
    the listed sites and nothing else).

    The DISORDER COLUMNS have to be passed in alongside the occupancies, or
    this rebuilds a different structure from the one the import produced.
    `resolve_disorder` prefers the file's own group/assembly labels and only
    falls back to geometric overlap without them, so dropping them here made
    the first toggle of any control on the ❖ page silently re-resolve the
    disorder: 7712836.cif went from 222 content atoms to 294, and from 999
    drawn to 469. That is Christian's "atoms disappear when I untick it".
    """
    data = CifData(cell, symops, asym_symbols, np.asarray(asym_frac, float),
                   occupancy=occupancy, disorder_groups=disorder_groups,
                   disorder_assemblies=disorder_assemblies)
    if mode == "asym":
        # The asymmetric unit is BY DEFINITION the listed sites, so no
        # disorder resolution here — but say so, since "asym" is also the mode
        # someone switches to when the cell looks wrong.
        #
        # SHARED SITES ARE MERGED (round 87), so a solid solution draws the
        # same pie sphere the full cell has drawn since round 42 instead of
        # four atoms stacked inside one another. `asym_rows` records what was
        # merged; `sync_asymmetric_unit` needs it to write an edit back
        # without flattening the composition.
        symbols, frac, composition, rows = asym_view(data, tol=tol)
        if report is not None:
            report["n_content"] = len(symbols)
            report["site_occupancy"] = composition
            report["asym_rows"] = rows
        return list(symbols), frac @ cell.matrix()
    # The CELL and PACKING modes go through `core.packing`, the same route an
    # import takes — otherwise opening a file gave one picture and switching
    # to "Full unit cell" gave another (NaCl 39 against 27), which is drift
    # nobody would ever attribute to the right cause. `SymmetryModifier` calls
    # this too, so it shows the same thing as well, which is the point.
    #
    # `packed=False` is for `SymmetryModifier`, which is a different feature:
    # stacking operations on a plain FRAGMENT to watch it become a cell
    # (round 33). Completing whole molecules there draws the fragment at all
    # eight corners of the invented cell, which buries the thing the user is
    # trying to see — 16 atoms became 128.
    if not packed:
        symbols, coords = expand(data, tol=tol, exterior=exterior,
                                 disorder=disorder, report=report,
                                 shell_molecules=shell_molecules)
    else:
        from . import packing as packing_mod
        if mode == "packing":
            # THE BLOCK IS BUILT AND BONDED BY `packing`, not replicated here.
            # The replication that follows below - one cell's atoms and bonds
            # repeated per offset, then a coincident-atom merge - only ever
            # joined two cells where a boundary copy happened to sit, so a
            # bond crossing an internal face with no copy on it was simply
            # not drawn. Pyrite lost its disulfide dimers (round 108). The
            # per-atom maps stayed one cell long as well, which switched off
            # every edit-propagation feature on a supercell. `block` keeps the
            # exact labels the packing already had, and everything falls out.
            symbols, coords, bonds, packed_meta = packing_mod.block(
                data, na=na, nb=nb, nc=nc, disorder=disorder, tol=tol,
                outside=outside, grow_from_copies=grow_from_copies)
        else:
            symbols, coords, bonds, packed_meta = packing_mod.pack(
                data, disorder=disorder, tol=tol, outside=outside,
                grow_from_copies=grow_from_copies)
        if report is not None:
            report["packed_bonds"] = [[int(i), int(j), int(o)]
                                      for i, j, o in bonds]
            report["packed"] = True
            report.update(packed_meta)
            # The cell CONTENT is a different question from what is drawn,
            # and the crystal page's count and the density both need it.
            #
            # Into a SEPARATE report, and only the scalars are taken across
            # (round 83). Passing `report` straight in let this call overwrite
            # the packing's per-atom maps with its own - which are keyed by
            # CONTENT index while the drawn structure is the packing's, so it
            # did not merely lose the copies, it changed what the keys mean.
            # `1547149.cif` came back with 2 of its 10 Nb carrying a
            # composition and `site_of` holding 6 entries for 21 atoms.
            content_report = {}
            expand(data, whole_molecules=False, boundary=False,
                   disorder=disorder, tol=tol, report=content_report)
            for key in ("n_content", "disorder", "dropped_bonds", "refused"):
                if content_report.get(key) is not None:
                    report[key] = content_report[key]
    if mode != "packing" or packed:
        return symbols, coords
    # From here on: the UNPACKED block only (`packed=False`, the
    # SymmetryModifier's fragment-on-an-invented-cell case, round 33). It has
    # no bonds to carry and its atoms are replicated and merged as before.
    offsets = supercell_offsets(cell, na, nb, nc)
    out_syms = []
    blocks = []
    stride = len(symbols)
    # The BONDS have to be repeated with the atoms. They were not, which is
    # Christian's "supercells do not show bonds": every cell past the first
    # drew as loose spheres, because `report["packed_bonds"]` still described
    # one cell's worth of indices while the atom list had grown by a factor of
    # na*nb*nc. Nothing failed and nothing warned — the picture was simply a
    # cloud of atoms, which is also why it survived so long.
    cell_bonds = [(int(i), int(j), int(o))
                  for i, j, o in ((report or {}).get("packed_bonds") or [])]
    out_bonds = [(i + n * stride, j + n * stride, o)
                 for n in range(len(offsets)) for i, j, o in cell_bonds]
    for off in offsets:
        out_syms.extend(symbols)
        blocks.append(coords + off[None, :])
    out_xyz = np.vstack(blocks) if blocks else np.zeros((0, 3))
    if report is not None and report.get("site_occupancy"):
        # Every copy of the cell repeats the shared sites, so the index map
        # has to be repeated with it or only the first cell would be drawn
        # with its true composition.
        base = report["site_occupancy"]
        stride = len(symbols)
        report["site_occupancy"] = {
            str(int(k) + n * stride): v
            for n in range(len(offsets)) for k, v in base.items()}
    # Each cell in the block carries its own BOUNDARY COPIES, and the copy on
    # a shared internal face is the same atom as its neighbour's — so stacking
    # them naively draws that atom twice, at exactly the same point. Ferrocene
    # came out with 1680 coincident pairs in a 2x2x2 of 1680 atoms, i.e. every
    # atom drawn twice: invisible as a count, visible as z-fighting and as
    # doubled sticks, and it makes every downstream measurement wrong.
    if len(offsets) > 1 and len(out_xyz):
        keep, canonical = _coincident_map(out_xyz, tol=tol)
        if not keep.all():
            out_syms = [s for s, k in zip(out_syms, keep) if k]
            out_xyz = out_xyz[keep]
            renumber = np.cumsum(keep) - 1
            # A dropped atom is the SAME atom as the one it landed on, so its
            # bonds are re-pointed through `canonical` and not thrown away —
            # those are exactly the bonds that cross an internal face of the
            # block, i.e. the ones holding the supercell together.
            remapped = []
            seen = set()
            for i, j, o in out_bonds:
                a = int(renumber[int(canonical[i])])
                b = int(renumber[int(canonical[j])])
                if a == b or a < 0 or b < 0:
                    continue
                key = (min(a, b), max(a, b))
                if key in seen:
                    continue        # two cells' copies of one shared-face bond
                seen.add(key)
                remapped.append((key[0], key[1], o))
            out_bonds = remapped
            if report is not None and report.get("site_occupancy"):
                report["site_occupancy"] = {
                    str(int(renumber[int(k)])): v
                    for k, v in report["site_occupancy"].items()
                    if int(k) < len(keep) and keep[int(k)]}
    if report is not None and cell_bonds:
        report["packed_bonds"] = [[int(i), int(j), int(o)]
                                  for i, j, o in out_bonds]
    return out_syms, out_xyz


def _first_of_coincident(xyz, tol=0.1):
    # type: (np.ndarray, float) -> np.ndarray
    """Keep-mask dropping any atom that repeats an EARLIER one's position."""
    return _coincident_map(xyz, tol=tol)[0]


def _coincident_map(xyz, tol=0.1):
    # type: (np.ndarray, float) -> tuple
    """`(keep, canonical)` for a list of positions.

    `keep` drops any atom that repeats an EARLIER one's position; `canonical[i]`
    is the index of the atom `i` collapses onto (itself, when it is kept). The
    second half is what lets BONDS survive the de-duplication: a dropped atom is
    not gone, it is the same physical atom as the one it landed on, so an edge
    that referenced it has to be re-pointed rather than discarded.

    Grid-hashed rather than an N^2 sweep, because a packing is the one place
    the atom count runs into six figures. The 27 neighbouring buckets are
    probed as well: two coincident points can straddle a bucket edge and land
    on different keys, which is the round-43d lesson in a new place.
    """
    xyz = np.asarray(xyz, dtype=float)
    size = max(float(tol), 1e-6)
    keys = np.floor(xyz / size).astype(np.int64)
    buckets = {}                      # type: dict
    keep = np.ones(len(xyz), dtype=bool)
    canonical = np.arange(len(xyz), dtype=np.int64)
    neighbourhood = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1)
                     for c in (-1, 0, 1)]
    for index in range(len(xyz)):
        key = tuple(int(v) for v in keys[index])
        onto = -1
        for delta in neighbourhood:
            probe = (key[0] + delta[0], key[1] + delta[1], key[2] + delta[2])
            for other in buckets.get(probe, ()):
                if float(np.linalg.norm(xyz[index] - xyz[other])) <= tol:
                    onto = other
                    break
            if onto >= 0:
                break
        if onto >= 0:
            keep[index] = False
            canonical[index] = onto
            continue
        buckets.setdefault(key, []).append(index)
    return keep, canonical


def supercell_offsets(cell, na=1, nb=1, nc=1):
    # type: (Cell, int, int, int) -> np.ndarray
    """Cartesian translations for an na x nb x nc block of cells."""
    m = cell.matrix()
    out = []
    for ia in range(int(max(na, 1))):
        for ib in range(int(max(nb, 1))):
            for ic in range(int(max(nc, 1))):
                out.append(np.array([ia, ib, ic], dtype=float) @ m)
    return np.asarray(out)
