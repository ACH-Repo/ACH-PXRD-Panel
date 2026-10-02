"""Simulated powder X-ray diffraction from a crystal MoloM already has open.

The one piece of physics that needs no new file format: the cell, the symmetry
operators, the asymmetric unit and the site occupancies are all in
`Structure.metadata` already, and a structure factor is a closed-form sum over
them. For a framework group it is also the measurement people actually take.

UI-free and offline, like everything in `core/`. The plot window is a separate
concern and this module knows nothing about it.

**Where the settings live, and why it is not here.** MoloM shows several
crystals in one scene, so a pattern is per-STRUCTURE: wavelength, step, peak
width and range belong in that structure's `metadata`, beside `polyhedra` and
`show_cell`, which is what makes them ride undo and the savefile for free.
This module takes them as arguments and stores nothing.

**Q or 2-theta is a real choice, not a display preference.** `two_theta`
depends on the wavelength; `q = 4 pi sin(theta) / lambda` does not. So two
structures simulated at different wavelengths CANNOT honestly share a 2-theta
axis - the same reflection would sit at two angles for no physical reason -
while they can always share a Q axis. `common_axis` is what a caller should
ask before drawing several patterns together.

**The formula**, and the part that is easy to get wrong is the scattering
factor: the vendored coefficients give the DIFFERENCE from Z, not a
Cromer-Mann sum (see `core/scattering.py`).

    s      = sin(theta) / lambda
    f_j(s) = Z_j - 41.78214 * s^2 * sum_i a_i exp(-b_i s^2)
    F(hkl) = sum_j f_j * occ_j * exp(2 pi i (h x_j + k y_j + l z_j)) * exp(-B_j s^2)
    LP     = (1 + cos^2 2theta) / (sin^2 theta * cos theta)
    I(hkl) = |F|^2 * LP

Systematic absences need no special-casing: they fall out of `|F|^2` being
zero. Multiplicity needs none either - every `hkl` in the sphere is
enumerated and reflections landing at the same angle are merged, which
accumulates it. That is also what makes this checkable against an independent
implementation, and the tests do exactly that.

**Debye-Waller is only applied when the file carried a displacement
parameter.** A pattern computed at B = 0 has visibly wrong high-angle
intensities, so `Pattern.note` says when that is what happened rather than
letting a plausible-looking curve stand unqualified.
"""

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import scattering
from .elements import atomic_number

#: Characteristic emission lines, in Angstrom (International Tables Vol. C,
#: Table 4.2.2.1). A lab tube emits the K-alpha DOUBLET, not one line: an
#: unmonochromated pattern carries K-alpha2 at half the intensity of
#: K-alpha1, which is why every peak past about 40 degrees looks split.
LINES = {
    "Cu": {"Ka1": 1.540598, "Ka2": 1.544426, "Kb": 1.392250},
    "Mo": {"Ka1": 0.709300, "Ka2": 0.713590, "Kb": 0.632288},
    "Co": {"Ka1": 1.788965, "Ka2": 1.792850, "Kb": 1.620790},
    "Cr": {"Ka1": 2.289700, "Ka2": 2.293606, "Kb": 2.084870},
    "Fe": {"Ka1": 1.936042, "Ka2": 1.939980, "Kb": 1.756610},
    "Ag": {"Ka1": 0.559422, "Ka2": 0.563813, "Kb": 0.497069},
}

#: The standard K-alpha2 : K-alpha1 intensity ratio. It is a property of the
#: atom (the 2p3/2 and 2p1/2 level degeneracies), not of the instrument, so
#: 0.5 is right for every tube here - and it is why the doublet is written
#: "2:1" the way round it is.
KA2_RATIO = 0.5

#: hc in keV Angstrom, so `lambda = ENERGY_ANGSTROM / E`. A synchrotron user
#: states an ENERGY and never a wavelength, so both have to be accepted.
ENERGY_ANGSTROM = 12.398419843320026

#: What the source box offers. Each value is a spec string that
#: `parse_source` reads, so the presets and anything typed by hand go through
#: exactly one parser.
SOURCES = (
    ("Cu Ka1", "Cu Ka1"),
    ("Cu Ka1+Ka2 (2:1)", "Cu Ka1+Ka2 2:1"),
    ("Cu Ka (weighted mean)", "1.541874"),
    ("Mo Ka1", "Mo Ka1"),
    ("Mo Ka1+Ka2 (2:1)", "Mo Ka1+Ka2 2:1"),
    ("Co Ka1", "Co Ka1"),
    ("Co Ka1+Ka2 (2:1)", "Co Ka1+Ka2 2:1"),
    ("Cr Ka1", "Cr Ka1"),
    ("Fe Ka1", "Fe Ka1"),
    ("Ag Ka1", "Ag Ka1"),
    ("Synchrotron 12 keV", "12 keV"),
    ("Synchrotron 0.4 A", "0.4"),
)

#: Backwards compatibility with round 94, and still the honest single-line
#: table: one wavelength per name.
WAVELENGTHS = {"Cu Ka1": LINES["Cu"]["Ka1"],
               "Cu Ka": 1.541874,          # K-alpha1/K-alpha2 weighted mean
               "Mo Ka1": LINES["Mo"]["Ka1"],
               "Co Ka1": LINES["Co"]["Ka1"],
               "Cr Ka1": LINES["Cr"]["Ka1"],
               "Fe Ka1": LINES["Fe"]["Ka1"],
               "Ag Ka1": LINES["Ag"]["Ka1"]}
DEFAULT_WAVELENGTH = WAVELENGTHS["Cu Ka1"]
#: EMPTY, meaning "whatever `wavelength` says". A single line is fully
#: described by its number, so only a source that cannot be written as one -
#: a doublet, or an energy somebody wants to keep seeing as an energy - needs
#: the text, and a savefile does not carry a string saying "as shipped".
DEFAULT_SOURCE = ""

#: `|F|^2` at or below this counts as systematically absent. Not zero: the
#: sum is floating point, and a reflection extinguished by a screw axis comes
#: out at 1e-25 rather than at 0. Well below any real weak reflection, which
#: on these structures is of order 1.
ABSENT_F2 = 1e-9

#: Axis units. Q is the one that survives a wavelength difference.
AXIS_TWO_THETA = "two_theta"
AXIS_Q = "q"

#: Reflections closer together than this in 2-theta are one peak. Chosen well
#: below any real instrumental width, so merging can only ever combine
#: reflections that are genuinely coincident (the symmetry-equivalent set).
MERGE_TOL_DEG = 1e-5

#: Below this fraction of the strongest peak a reflection is dropped from the
#: returned list. A full sphere for a large cell is tens of thousands of
#: reflections, almost all of them numerically zero.
MIN_RELATIVE = 1e-6

#: The default range. 50 degrees rather than 90 because it is where a lab
#: pattern's information is - a molecular crystal has almost nothing above it,
#: and the sum is over a sphere of radius 1/d_min, so the cost grows as the
#: CUBE of how far you ask. Christian's call, and it is 3x less work.
DEFAULT_TWO_THETA = (5.0, 50.0)
DEFAULT_FWHM = 0.10             # degrees 2-theta

#: How many stored samples span one FWHM when the step is left to derive
#: itself. The right invariant is points PER PEAK rather than a fixed number
#: of degrees: 0.01 deg is 10 points across a 0.1 deg peak and only 2 across
#: a 0.02 deg one, so a fixed step is simultaneously too coarse for a sharp
#: pattern and wasteful for a broad one.
#:
#: 20 rather than 10 because it costs nothing, measured: the min/max envelope
#: reduces to about one point per pixel column whatever is stored, so paint
#: time is FLAT in the stored density - 8 traces repaint in 44 ms at 144k
#: points and 48 ms at 36k - and the whole cost is in building the profile,
#: 6.2 -> 9.1 ms for those eight. What it buys is a curve that survives being
#: enlarged, which is what an SVG figure is for.
STORED_PER_FWHM = 20.0

#: 0 means "derive it from the FWHM" - see `step_for`. A savefile that
#: carries an explicit step still gets exactly that step.
DEFAULT_STEP = 0.0

def step_for(fwhm, step=0.0):
    # type: (float, float) -> float
    """The sampling step of the stored profile, in the units of the axis.

    An explicit `step` wins; 0 derives one from the peak width, which is the
    quantity that decides whether a curve looks like a curve.
    """
    if step:
        return abs(float(step))
    fwhm = abs(float(fwhm))
    if fwhm <= 0.0:
        return 0.01
    return fwhm / STORED_PER_FWHM


#: How far from its centre each peak shape is still worth evaluating, in
#: FWHM. They are wildly different and were both 12 until it was measured:
#: a GAUSSIAN at 12 FWHM is exp(-399), i.e. exactly zero in double precision,
#: and is already down to 1.5e-11 at 3; a LORENTZIAN at 12 FWHM is still
#: 1.7e-3 of the peak, because its tail goes as 1/d^2 and never really stops.
#:
#: So the Gaussian is being evaluated over four times more axis than it can
#: contribute to, and narrowing its window is exact: the profile is unchanged
#: to 0.000000% of the peak and a pure-Gaussian ferrocene pattern goes from
#: 1.75 to 0.79 ms, i.e. 2.2x. 3 rather than 2 because at 2 FWHM the error
#: becomes measurable (0.0007%) and there is no reason to sit that close to
#: the edge for a tenth of a millisecond.
#:
#: It buys the PSEUDO-VOIGT nothing, which is worth recording because it is
#: the default and the arithmetic suggests otherwise. Splitting its two
#: halves into separate windows was built and measured SLOWER, 1.44 -> 1.53
#: ms: the extra slice and the extra accumulate into `y` cost more than the
#: exponentials they remove. So it keeps one window.
REACH_GAUSSIAN = 3.0
REACH_LORENTZIAN = 12.0

SHAPE_GAUSSIAN = "gaussian"
SHAPE_LORENTZIAN = "lorentzian"
SHAPE_PSEUDO_VOIGT = "pseudo_voigt"
SHAPES = (SHAPE_GAUSSIAN, SHAPE_LORENTZIAN, SHAPE_PSEUDO_VOIGT)


class Reflection(object):
    """One merged reflection: where it is, how strong, and what it is called."""

    __slots__ = ("h", "k", "l", "d", "two_theta", "q", "intensity",
                 "multiplicity", "equivalents", "f2", "lp")

    def __init__(self, hkl, d, two_theta, q, intensity, equivalents=None,
                 f2=0.0, lp=1.0):
        self.h, self.k, self.l = (int(v) for v in hkl)
        self.d = float(d)
        self.two_theta = float(two_theta)
        self.q = float(q)
        self.intensity = float(intensity)
        #: |F|^2 summed over the equivalents, BEFORE the Lorentz-polarisation
        #: factor. Kept separately because it is the wavelength-independent
        #: half: `s = sin(theta)/lambda = 1/(2d)` does not depend on lambda, so
        #: a second emission line places the SAME |F|^2 at its own angle with
        #: its own LP. That is what makes a K-alpha1/alpha2 doublet one
        #: calculation rather than two.
        self.f2 = float(f2)
        self.lp = float(lp)
        #: Every `hkl` that landed at this angle. Its length is the
        #: multiplicity, and it is what a hover readout should show - "(1 1 1)
        #: and 7 more" is the honest label for a cubic reflection.
        self.equivalents = [tuple(int(v) for v in e)
                            for e in (equivalents or [tuple(hkl)])]
        self.multiplicity = len(self.equivalents)

    @property
    def hkl(self):
        return (self.h, self.k, self.l)

    @property
    def absent(self):
        """Systematically absent: allowed by the lattice, extinguished by the
        symmetry. `|F|^2` being zero IS the definition - nothing here knows
        what an F-centred lattice is, which is what makes the agreement with
        an independent program meaningful."""
        return self.f2 <= ABSENT_F2

    def label(self):
        # type: () -> str
        return "({} {} {})".format(self.h, self.k, self.l)

    def __repr__(self):
        return "<Reflection {} 2th={:.3f} I={:.1f} x{}>".format(
            self.label(), self.two_theta, self.intensity, self.multiplicity)


class Pattern(object):
    """A simulated diffractogram plus what it was computed from."""

    __slots__ = ("reflections", "wavelength", "two_theta_range", "note",
                 "name", "components")

    def __init__(self, reflections, wavelength, two_theta_range, note="",
                 name="", components=None):
        self.reflections = list(reflections)
        self.wavelength = float(wavelength)
        #: `[(wavelength, weight), ...]`. One entry for a monochromatic
        #: source; two for a K-alpha doublet, which is what an unmonochromated
        #: lab pattern really is.
        self.components = ([(float(w), float(k)) for w, k in components]
                           if components else [(float(wavelength), 1.0)])
        self.two_theta_range = tuple(float(v) for v in two_theta_range)
        #: Anything the caller should say out loud - above all that no
        #: displacement parameters were available.
        self.note = str(note or "")
        self.name = str(name or "")

    def __len__(self):
        return len(self.reflections)

    def strongest(self):
        # type: () -> float
        return max((r.intensity for r in self.reflections), default=0.0)

    def x(self, axis=AXIS_TWO_THETA):
        return np.array([r.two_theta if axis == AXIS_TWO_THETA else r.q
                         for r in self.reflections], dtype=float)

    def y(self):
        return np.array([r.intensity for r in self.reflections], dtype=float)


# ------------------------------------------------------------- the source
class SourceError(ValueError):
    """What the user typed is not a radiation source."""


_LINE_ALIASES = {"ka1": "Ka1", "ka2": "Ka2", "kalpha1": "Ka1",
                 "kalpha2": "Ka2", "a1": "Ka1", "a2": "Ka2",
                 "kb": "Kb", "kb1": "Kb", "kbeta": "Kb", "ka": "Ka1+Ka2",
                 "kalpha": "Ka1+Ka2"}


def parse_source(text):
    # type: (str) -> list
    """`[(wavelength, weight), ...]` for whatever the user wrote.

    ONE parser for the presets and for anything typed by hand, so a source
    that can be chosen can also be written down - which matters because the
    two cases people actually need are exactly the ones no preset list can
    cover: a synchrotron energy, and a tube doublet at a ratio somebody wants
    to change.

    Accepted, all case-insensitively:

    - `1.5406`, `1.5406 A`               - a wavelength in Angstrom
    - `17.5 keV`, `17.5kev`              - an ENERGY, converted by hc/E
    - `Cu Ka1`, `Mo Ka2`, `Co Kb`        - a named emission line
    - `Cu Ka`, `Cu Ka1+Ka2`              - the doublet at the standard 2:1
    - `Cu Ka1+Ka2 2:1`, `Cu Ka1 Cu Ka2 3:1`  - the doublet at a stated ratio
    - `1.5406+1.5444 2:1`                - two wavelengths, likewise

    The RATIO is written the way a diffractionist says it out loud - "two to
    one" for alpha1 twice as strong as alpha2 - so it is the ratio of
    INTENSITIES in the order the components are listed, and it is normalised
    so the first component has weight 1.
    """
    raw = str(text or "").strip()
    if not raw:
        raise SourceError("no source given")
    # A DECIMAL COMMA is a decimal point. Half of Europe types one, and a
    # source box that refuses "1,5406" leaves the pattern at whatever it was
    # before - a whole diffractogram at the wrong angles for a reason nothing
    # on screen explains. Only between digits, so a comma separating two
    # components is untouched.
    raw = re.sub(r"(?<=\d),(?=\d)", ".", raw)
    ratio = None
    # A trailing "a:b[:c]" is the ratio; anything else is part of the source.
    match = re.search(r"(\d+(?:\.\d+)?(?:\s*:\s*\d+(?:\.\d+)?)+)\s*$", raw)
    if match:
        ratio = [float(v) for v in match.group(1).split(":")]
        raw = raw[:match.start()].strip()
    if not raw:
        raise SourceError("a ratio needs something to be a ratio of")
    terms = _source_terms(raw)
    if not terms:
        raise SourceError("could not read {!r} as a radiation source".format(
            str(text).strip()))
    if ratio is None:
        weights = [KA2_RATIO if i else 1.0 for i in range(len(terms))]             if len(terms) == 2 else [1.0] * len(terms)
    else:
        if len(ratio) != len(terms):
            raise SourceError(
                "the ratio has {} part(s) and the source has {}".format(
                    len(ratio), len(terms)))
        if ratio[0] <= 0:
            raise SourceError("the first part of a ratio cannot be zero")
        weights = [v / ratio[0] for v in ratio]
    return [(float(w), float(k)) for w, k in zip(terms, weights)]


def _source_terms(raw):
    # type: (str) -> list
    """The WAVELENGTHS in a source string, in the order written."""
    out = []
    element = None
    # A UNIT written apart from its number ("17.5 keV", "1.54 A") is joined
    # back on, so the tokeniser below never meets a bare unit - which is how
    # people write both and is the commonest thing to get wrong here.
    raw = re.sub(r"(\d)\s+(keV|eV|angstrom|ang|A)(?![A-Za-z])",
                 lambda m: m.group(1) + m.group(2), raw,
                 flags=re.IGNORECASE)
    # `+` and `,` separate components; whitespace does too, except that an
    # element and its line belong together ("Cu Ka1").
    for token in re.split(r"[+,]|\s+", raw.replace("/", "+")):
        token = token.strip()
        if not token:
            continue
        low = token.lower()
        if low.endswith("kev") or low.endswith("ev"):
            digits = re.sub(r"k?ev$", "", low).strip()
            try:
                energy = float(digits)
            except ValueError:
                raise SourceError("{!r} is not an energy".format(token))
            if energy <= 0:
                raise SourceError("an energy must be positive")
            # eV as well as keV, because a soft-X-ray beamline states one and
            # a hard one the other, and the factor of a thousand between them
            # is the kind of slip nobody notices in a plot.
            kev = energy if low.endswith("kev") else energy / 1000.0
            out.append(ENERGY_ANGSTROM / kev)
            element = None
            continue
        if low in ("a", "ang", "angstrom"):
            continue                          # a unit on the number before it
        if token.capitalize() in LINES:
            element = token.capitalize()
            continue
        alias = _LINE_ALIASES.get(low.replace("-", "").replace("_", ""))
        if alias is not None:
            if element is None:
                raise SourceError(
                    "{!r} needs an element in front of it, e.g. 'Cu {}'"
                    .format(token, token))
            for part in alias.split("+"):
                value = LINES[element].get(part)
                if value is None:
                    raise SourceError(
                        "{} has no {} line".format(element, part))
                out.append(value)
            continue
        try:
            value = float(re.sub(r"(angstrom|ang|a)$", "", low).strip())
        except ValueError:
            raise SourceError("could not read {!r}".format(token))
        if value <= 0:
            raise SourceError("a wavelength must be positive")
        out.append(value)
        element = None
    return out


def source_label(components):
    # type: (list) -> str
    """A short human label for a component list, for a legend or a readout."""
    components = list(components or ())
    if not components:
        return ""
    if len(components) == 1:
        return "{:.5f} A".format(components[0][0])
    parts = " + ".join("{:.5f}".format(w) for w, _k in components)
    ratio = " : ".join(_trim(k) for _w, k in components)
    return "{} A  ({})".format(parts, ratio)


def _trim(value):
    text = "{:.4g}".format(float(value))
    return text


def energy_kev(wavelength):
    """The photon energy of a wavelength, in keV. The readout a synchrotron
    user checks."""
    return ENERGY_ANGSTROM / float(wavelength)


def components_of(settings):
    """The `(wavelength, weight)` list a settings dict describes.

    `source` wins where it is set, because it can express a doublet and a
    bare `wavelength` cannot; the number remains for savefiles written before
    this and for anything that only ever needs one line.
    """
    text = str((settings or {}).get("source") or "").strip()
    if text:
        try:
            return parse_source(text)
        except SourceError:
            pass
    return [(float((settings or {}).get("wavelength")
                   or DEFAULT_WAVELENGTH), 1.0)]


# ------------------------------------------------------------ axis conversion
def q_from_two_theta(two_theta_deg, wavelength):
    """Q = 4 pi sin(theta) / lambda, in inverse Angstrom."""
    theta = np.radians(np.asarray(two_theta_deg, dtype=float)) / 2.0
    return 4.0 * math.pi * np.sin(theta) / float(wavelength)


def two_theta_from_q(q, wavelength):
    """The inverse. Values beyond the Ewald limit come back as NaN rather
    than raising - a caller plotting a shared Q axis will legitimately ask
    about angles a longer wavelength cannot reach."""
    arg = np.asarray(q, dtype=float) * float(wavelength) / (4.0 * math.pi)
    arg = np.where(np.abs(arg) <= 1.0, arg, np.nan)
    return np.degrees(2.0 * np.arcsin(arg))


def common_axis(wavelengths, tol=1e-9):
    # type: (Sequence[float], float) -> str
    """Which axis several patterns can HONESTLY share.

    2-theta only when they were all computed at the same wavelength; Q
    otherwise. Not a preference - plotting two wavelengths on one 2-theta axis
    puts the same reflection at two angles for no physical reason.
    """
    values = [float(w) for w in wavelengths if w]
    if not values:
        return AXIS_TWO_THETA
    return (AXIS_TWO_THETA
            if max(values) - min(values) <= tol else AXIS_Q)


# ------------------------------------------------------- scattering factors
def form_factor(symbol, s2):
    """f(s) for one element at s^2 = (sin(theta)/lambda)^2.

    Falls back to the bare atomic number for anything the table does not
    cover, which is the high-angle limit rather than a guess - and is flagged
    by `missing_species` so a caller can say so.
    """
    z = atomic_number(symbol)
    params = scattering.PARAMS.get(_table_symbol(symbol))
    if not params:
        return float(z) * np.ones_like(np.asarray(s2, dtype=float))
    s2 = np.asarray(s2, dtype=float)
    total = np.zeros_like(s2)
    for a, b in params:
        total = total + a * np.exp(-b * s2)
    return float(z) - scattering.PREFACTOR * s2 * total


def _table_symbol(symbol):
    text = str(symbol or "").strip()
    return text[:1].upper() + text[1:2].lower() if text else ""


def missing_species(symbols):
    # type: (Sequence[str]) -> List[str]
    """Species with no tabulated form factor, in first-seen order."""
    out, seen = [], set()
    for sym in symbols:
        key = _table_symbol(sym)
        if key and key not in seen and key not in scattering.PARAMS:
            seen.add(key)
            out.append(key)
    return out


# --------------------------------------------------------------- the pattern
def _hkl_range(cell, d_min):
    """How far along each reciprocal axis a sphere of radius 1/d_min reaches.

    Derived from the RECIPROCAL cell lengths, not the direct ones: `h` is
    bounded by `|a*| h <= 1/d_min`. Using the direct lengths gets a skewed
    cell wrong in exactly the way round 44's minimum-image guard was wrong.
    """
    matrix = np.asarray(cell.matrix(), dtype=float)
    recip = np.linalg.inv(matrix).T          # rows are a*, b*, c*
    lengths = np.linalg.norm(recip, axis=1)
    limit = 1.0 / float(d_min)
    return [max(1, int(math.floor(limit / max(v, 1e-12)))) for v in lengths]


#: Reflections are summed in blocks of this many. The structure-factor sum is
#: an (n_reflections x n_atoms) array, so a big cell over a wide range would
#: otherwise ask for gigabytes; a block keeps the working set small while
#: still doing the arithmetic in numpy rather than in Python.
_CHUNK = 4096


def _element_factors(symbols):
    """`(index per atom, one f-curve builder per distinct element)`.

    A structure has tens of atoms and a handful of ELEMENTS, so the form
    factor is evaluated per element and broadcast, not per atom. On a
    42-atom cell that is 3 curves instead of 42.
    """
    order, index = [], []
    seen = {}
    for sym in symbols:
        key = _table_symbol(sym)
        if key not in seen:
            seen[key] = len(order)
            order.append(key)
        index.append(seen[key])
    return np.asarray(index, dtype=int), order


def _f_of_s2(elements, s2):
    """`(n_elements, n_s2)` form factors, vectorised over both."""
    s2 = np.asarray(s2, dtype=float)
    out = np.empty((len(elements), s2.size), dtype=float)
    for row, key in enumerate(elements):
        z = float(atomic_number(key))
        params = scattering.PARAMS.get(key)
        if not params:
            out[row] = z
            continue
        total = np.zeros_like(s2)
        for a, b in params:
            total += a * np.exp(-b * s2)
        out[row] = z - scattering.PREFACTOR * s2 * total
    return out


def compute(cell, symbols, frac, occupancy=None, wavelength=DEFAULT_WAVELENGTH,
            two_theta_range=DEFAULT_TWO_THETA, debye_waller=None, name="",
            components=None, keep_absent=False):
    """The pattern for one crystal. `frac` is the FULL cell contents.

    Not the asymmetric unit: the sum runs over every atom in the cell, which
    is what `cif.expand` already produces and what makes the arithmetic the
    textbook one rather than a symmetry-aware special case.

    `components` is `[(wavelength, weight), ...]` for a source with more than
    one emission line. The reflections are enumerated ONCE, for the first
    (strongest) component: `s = sin(theta)/lambda = 1/(2d)` does not depend on
    the wavelength, so `|F|^2` is shared and only the ANGLE and the
    Lorentz-polarisation factor differ. `profile` places the rest.

    `keep_absent` keeps the reflections that are systematically absent, which
    is what an hkl LIST wants and what a pattern does not - they are a third
    of the list on a centred lattice and every one of them is a flat zero.
    """
    frac = np.asarray(frac, dtype=float)
    components = [(float(w), float(k)) for w, k in (components or ())]
    if components:
        wavelength = components[0][0]
    else:
        components = [(float(wavelength), 1.0)]
    if frac.ndim != 2 or frac.shape[0] == 0:
        return Pattern([], wavelength, two_theta_range, "no atoms", name,
                       components=components)
    symbols = list(symbols)
    occ = (np.ones(len(symbols)) if occupancy is None
           else np.asarray(occupancy, dtype=float))
    dw = (np.zeros(len(symbols)) if debye_waller is None
          else np.asarray(debye_waller, dtype=float))

    lo, hi = (float(two_theta_range[0]), float(two_theta_range[1]))
    lo = max(lo, 1e-3)
    hi = min(hi, 179.9)
    lam = float(wavelength)
    d_min = lam / (2.0 * math.sin(math.radians(hi) / 2.0))
    d_max = lam / (2.0 * math.sin(math.radians(lo) / 2.0))

    matrix = np.asarray(cell.matrix(), dtype=float)
    recip = np.linalg.inv(matrix).T
    hmax, kmax, lmax = _hkl_range(cell, d_min)

    # The whole sphere at once. Merging coincident reflections is what
    # accumulates the multiplicity and what makes a systematic absence fall
    # out of |F|^2 rather than out of a rule, so every hkl is enumerated -
    # including the Friedel mates, whose count is part of the multiplicity a
    # table reports (rock salt 111 is x8, not x4).
    grid = np.stack(np.meshgrid(np.arange(-hmax, hmax + 1),
                                np.arange(-kmax, kmax + 1),
                                np.arange(-lmax, lmax + 1),
                                indexing="ij"), axis=-1).reshape(-1, 3)
    grid = grid[np.any(grid != 0, axis=1)]
    g = grid.astype(float) @ recip
    g_len = np.linalg.norm(g, axis=1)
    good = g_len > 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(good, 1.0 / np.where(good, g_len, 1.0), 0.0)
    s = g_len / 2.0                       # sin(theta)/lambda, wavelength-free
    arg = s * lam
    good &= (d >= d_min) & (d <= d_max) & (np.abs(arg) <= 1.0)
    grid, d, s, arg = grid[good], d[good], s[good], arg[good]
    theta = np.arcsin(arg)
    two_theta = np.degrees(2.0 * theta)
    inside = (two_theta >= lo) & (two_theta <= hi)
    grid, d, s, theta, two_theta = (grid[inside], d[inside], s[inside],
                                    theta[inside], two_theta[inside])
    absent_note = missing_species(symbols)
    factor_note = ("no tabulated scattering factor for {} (used Z)".format(
        ", ".join(absent_note)) if absent_note else "")
    if not len(grid):
        return Pattern([], lam, (lo, hi),
                       "; ".join(x for x in ("no reflections in range",
                                             factor_note) if x), name,
                       components=components)

    index, elements = _element_factors(symbols)
    s2 = s * s
    f2 = np.empty(len(grid), dtype=float)
    for start in range(0, len(grid), _CHUNK):
        stop = min(start + _CHUNK, len(grid))
        block = slice(start, stop)
        f_el = _f_of_s2(elements, s2[block])            # (n_elem, n_block)
        f = f_el[index]                                 # (n_atoms, n_block)
        amp = f * (occ[:, None] * np.exp(-dw[:, None] * s2[None, block]))
        phase = 2.0 * math.pi * (frac @ grid[block].T.astype(float))
        real = np.einsum("ij,ij->j", amp, np.cos(phase))
        imag = np.einsum("ij,ij->j", amp, np.sin(phase))
        f2[block] = real * real + imag * imag
    lorentz = ((1.0 + np.cos(2.0 * theta) ** 2)
               / (np.sin(theta) ** 2 * np.cos(theta)))
    intensity = f2 * lorentz
    if not keep_absent:
        real_peak = f2 > ABSENT_F2
        if not real_peak.any():
            return Pattern([], lam, (lo, hi),
                           "; ".join(x for x in ("no reflections in range",
                                                 factor_note) if x), name,
                           components=components)
        grid, d, two_theta = grid[real_peak], d[real_peak], two_theta[real_peak]
        f2, lorentz = f2[real_peak], lorentz[real_peak]
        intensity = intensity[real_peak]

    # MERGE by angle. Reflections landing together are one peak, which is
    # where the multiplicity comes from - no symmetry is consulted anywhere.
    keys = np.round(two_theta / MERGE_TOL_DEG).astype(np.int64)
    order = np.argsort(keys, kind="stable")
    keys = keys[order]
    starts = np.flatnonzero(np.concatenate(([True], keys[1:] != keys[:-1])))
    ends = np.concatenate((starts[1:], [len(keys)]))
    grid, d, two_theta = grid[order], d[order], two_theta[order]
    f2, lorentz, intensity = f2[order], lorentz[order], intensity[order]

    merged = []
    peak = 0.0
    for a, b in zip(starts, ends):
        total = float(intensity[a:b].sum())
        merged.append((a, b, total))
        peak = max(peak, total)
    peak = peak or 1.0
    reflections = []
    for a, b, total in merged:
        if not keep_absent and total / peak < MIN_RELATIVE:
            continue
        hkls = [tuple(int(v) for v in row) for row in grid[a:b]]
        reflections.append(Reflection(
            _representative(hkls), float(d[a]), float(two_theta[a]),
            float(q_from_two_theta(two_theta[a], lam)), total,
            equivalents=hkls, f2=float(f2[a:b].sum()),
            lp=float(lorentz[a])))
    reflections.sort(key=lambda r: r.two_theta)
    notes = []
    if debye_waller is None or not np.any(dw):
        notes.append("no displacement parameters in the file, so B = 0 - "
                     "high-angle intensities are overestimated")
    if factor_note:
        notes.append(factor_note)
    return Pattern(reflections, lam, (lo, hi), "; ".join(notes), name,
                   components=components)


def _representative(hkls):
    """The hkl a merged peak is NAMED by: the one a crystallographer would
    write, i.e. all-positive first, then lexicographically largest."""
    return max(hkls, key=lambda t: (sum(1 for v in t if v >= 0), t))


# ---------------------------------------------------------------- the profile
def peak_positions(pattern, axis=AXIS_TWO_THETA):
    """`[(x, height), ...]` for every emission line of the source.

    ONE reflection becomes as many peaks as the source has components. `|F|^2`
    is shared - `s = sin(theta)/lambda = 1/(2d)` has no wavelength in it - so
    only the ANGLE moves and the Lorentz-polarisation factor is re-evaluated
    there. That is why a K-alpha doublet costs one calculation and not two,
    and why the splitting grows with angle exactly as a real pattern's does.
    """
    out = []
    for lam, weight in pattern.components:
        if weight <= 0.0:
            continue
        for r in pattern.reflections:
            arg = lam / (2.0 * r.d)
            if abs(arg) > 1.0:
                continue                 # past this line's Ewald limit
            theta = math.asin(arg)
            sin_t, cos_t = math.sin(theta), math.cos(theta)
            if sin_t <= 0.0 or cos_t <= 0.0:
                continue
            lp = (1.0 + math.cos(2.0 * theta) ** 2) / (sin_t * sin_t * cos_t)
            height = r.f2 * lp * weight
            if height <= 0.0:
                continue
            if axis == AXIS_Q:
                # Q is wavelength-INDEPENDENT, so every component of the
                # source puts a reflection at the SAME Q. A doublet therefore
                # does not split on this axis, which is not a bug: it is the
                # reason the axis exists.
                x = 4.0 * math.pi * sin_t / lam
            else:
                x = math.degrees(2.0 * theta)
            out.append((x, height))
    return out


def profile_at(pattern, x, axis=AXIS_TWO_THETA, fwhm=DEFAULT_FWHM,
               shape=SHAPE_PSEUDO_VOIGT, eta=0.5, peaks=None):
    """The profile evaluated at ARBITRARY positions `x`, unnormalised.

    Splitting this out is what lets a plot sample the curve AT THE PIXELS it
    is about to draw, instead of resampling a stored grid that was chosen for
    some other zoom level. The stored grid is wrong at both ends: at low zoom
    it is thousands of points landing on the same pixel, and at high zoom it
    is a dozen points across the width of the window, i.e. a polygon.

    **What makes it cheap is the thing that makes a peak a peak**: a profile
    of known FWHM contributes nothing measurable beyond a few widths, so each
    peak only touches the samples near it and the rest of the axis costs
    nothing at all. `x` must be SORTED, which it is when it comes from a
    pixel grid.

    `peaks` is `peak_positions(pattern, axis)` where the caller already has
    it - it is the same list at every zoom level, so recomputing it per frame
    would be the one avoidable cost here.
    """
    x = np.asarray(x, dtype=float)
    y = np.zeros_like(x)
    if peaks is None:
        peaks = peak_positions(pattern, axis)
    if not peaks or x.size == 0:
        return y
    w = abs(float(fwhm)) or DEFAULT_FWHM
    sigma = w / (2.0 * math.sqrt(2.0 * math.log(2.0)))
    half = w / 2.0
    g_reach = w * REACH_GAUSSIAN
    l_reach = w * REACH_LORENTZIAN
    for centre, height in peaks:
        if shape == SHAPE_GAUSSIAN:
            i0, i1 = np.searchsorted(x, [centre - g_reach, centre + g_reach])
            if i1 > i0:
                dx = x[i0:i1] - centre
                y[i0:i1] += height * np.exp(-0.5 * (dx / sigma) ** 2)
        elif shape == SHAPE_LORENTZIAN:
            i0, i1 = np.searchsorted(x, [centre - l_reach, centre + l_reach])
            if i1 > i0:
                dx = x[i0:i1] - centre
                y[i0:i1] += height / (1.0 + (dx / half) ** 2)
        else:
            # ONE window for the pseudo-Voigt, deliberately, even though its
            # Gaussian half is dead long before `l_reach`. Windowing the two
            # halves separately was tried and MEASURED SLOWER (1.44 -> 1.53 ms
            # on ferrocene): the second slice and the second accumulate into
            # `y` cost more than the exponentials they save. The saving is
            # only collectable where there is nothing else to slice for,
            # which is the pure-Gaussian branch above.
            i0, i1 = np.searchsorted(x, [centre - l_reach, centre + l_reach])
            if i1 > i0:
                dx = x[i0:i1] - centre
                g = np.exp(-0.5 * (dx / sigma) ** 2)
                lo_r = 1.0 / (1.0 + (dx / half) ** 2)
                y[i0:i1] += height * (eta * lo_r + (1.0 - eta) * g)
    return y


def profile(pattern, axis=AXIS_TWO_THETA, fwhm=DEFAULT_FWHM,
            step=DEFAULT_STEP, shape=SHAPE_PSEUDO_VOIGT, eta=0.5,
            x_range=None, normalise=True):
    """The continuous curve on a regular grid: `(x, y)` sampled on `step`.

    `fwhm` and `step` are always in the units of `axis`, so switching a shared
    plot from 2-theta to Q does not silently reinterpret a width as a
    different physical quantity. A caller that wants to keep an angular width
    across a unit switch has to convert it, and should - the two are not the
    same number.

    Pseudo-Voigt by default because a real diffractometer peak is neither a
    Gaussian nor a Lorentzian, and `eta` is the one knob that spans them.

    A grid is what an EXPORT wants. Anything drawing on a screen should use
    `profile_at` on its own pixel positions instead - see its docstring.
    """
    if x_range is None:
        if axis == AXIS_Q:
            lo = float(q_from_two_theta(pattern.two_theta_range[0],
                                        pattern.wavelength))
            hi = float(q_from_two_theta(pattern.two_theta_range[1],
                                        pattern.wavelength))
        else:
            lo, hi = pattern.two_theta_range
    else:
        lo, hi = (float(x_range[0]), float(x_range[1]))
    step = step_for(fwhm, step)
    n = max(2, int(round((hi - lo) / step)) + 1)
    x = np.linspace(lo, hi, n)
    y = profile_at(pattern, x, axis=axis, fwhm=fwhm, shape=shape, eta=eta)
    if normalise:
        top = float(y.max()) if y.size else 0.0
        if top > 0.0:
            y = y * (100.0 / top)
    return x, y


# ----------------------------------------------- what a structure remembers
#: Per-structure settings live in `Structure.metadata` under this key, beside
#: `polyhedra` and `show_cell` - so they ride undo and the savefile, and the
#: plot window owns no per-structure state at all. Deleting a crystal takes
#: its trace with it because the trace was never the window's.
METADATA_KEY = "pxrd"

DEFAULTS = {
    #: The source AS WRITTEN, which is what can express a doublet or an
    #: energy. Empty means the bare `wavelength` below, which is what a
    #: savefile written before sources existed carries.
    "source": DEFAULT_SOURCE,
    "wavelength": DEFAULT_WAVELENGTH,
    "two_theta_min": DEFAULT_TWO_THETA[0],
    "two_theta_max": DEFAULT_TWO_THETA[1],
    "fwhm": DEFAULT_FWHM,
    "step": DEFAULT_STEP,
    "shape": SHAPE_PSEUDO_VOIGT,
    "eta": 0.5,
    "enabled": True,
    "colour": "",
    "offset": 0.0,
}


def settings_of(structure):
    # type: (object) -> dict
    """This crystal's PXRD settings, defaults filled in."""
    meta = getattr(structure, "metadata", None) or {}
    stored = meta.get(METADATA_KEY) or {}
    out = dict(DEFAULTS)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in out:
                out[key] = value
    return out


def set_settings(structure, **changes):
    """Change some of them, leaving the rest alone.

    `source` and `wavelength` are ONE setting written two ways, so they are
    kept in step here rather than left to disagree: setting a source records
    its primary line as the wavelength (which is what a savefile written
    before sources existed would carry), and setting a bare wavelength clears
    the source, because a number cannot describe a doublet and pretending it
    still did would silently ignore what the caller just asked for.
    """
    meta = getattr(structure, "metadata", None)
    if meta is None:
        return
    current = settings_of(structure)
    current.update({k: v for k, v in changes.items() if k in DEFAULTS})
    if "source" in changes:
        try:
            current["wavelength"] = parse_source(current["source"])[0][0]
        except (SourceError, IndexError):
            pass
    elif "wavelength" in changes:
        current["source"] = ""
    # Only what DIFFERS from the defaults is stored, so a savefile does not
    # carry ten keys per crystal saying "as shipped".
    trimmed = {k: v for k, v in current.items() if v != DEFAULTS[k]}
    if trimmed:
        meta[METADATA_KEY] = trimmed
    else:
        meta.pop(METADATA_KEY, None)


# ------------------------------------------- from a structure MoloM has open
def _cell_frame_fractional(structure, cell):
    """The structure's OWN atoms as fractional coordinates of its cell.

    The fallback path, for a crystal with no stored asymmetric unit - an
    edited cell frozen into P1 (round 52), or a molecule that was simply
    given a box. The atoms are in the VIEWPORT's frame by then, so the rigid
    motion has to come off first or every phase in the sum is wrong: the same
    recovery `cell_corners_world` uses to make the box follow its molecule,
    reference sample first and the recorded pose behind it.
    """
    from . import cif as cif_mod
    coords = np.asarray(structure.coords, dtype=float).reshape(-1, 3)
    meta = getattr(structure, "metadata", None) or {}
    fit = None
    idx, ref = meta.get("cell_ref_idx"), meta.get("cell_ref_xyz")
    if idx and ref and not any(int(i) >= len(coords) for i in idx):
        fit = cif_mod.rigid_from_reference(np.asarray(ref, dtype=float),
                                           coords[[int(i) for i in idx]])
    if fit is None:
        rot, shift = meta.get("cell_pose_rot"), meta.get("cell_pose_shift")
        if rot and shift:
            fit = (np.asarray(rot, dtype=float).reshape(3, 3),
                   np.asarray(shift, dtype=float).reshape(3))
    if fit is not None:
        rot, shift = fit
        coords = (coords - np.asarray(shift)[None, :]) @ np.asarray(rot)
    return coords @ np.linalg.inv(np.asarray(cell.matrix(), dtype=float))


def cell_contents(structure):
    """`(cell, symbols, fractional, occupancy)` for one crystal, or None.

    **Regenerated from the asymmetric unit and the operators, never read off
    the drawn atoms.** A structure factor is a property of the CELL, so
    asymmetric-unit view, full cell and a 3x3x3 packing must all give the
    same pattern - and the drawn atoms are none of those three reliably:
    they carry boundary copies (an atom on a face is drawn twice, which would
    count it twice), they may be a supercell, and they are in the viewport's
    frame rather than the cell's.

    `boundary=False, whole_molecules=False` for exactly that reason: the cell
    CONTENT, which is what `Z` formula units means.
    """
    from . import cif as cif_mod
    meta = getattr(structure, "metadata", None) or {}
    stored = meta.get("cell")
    if not stored:
        return None
    try:
        cell = (stored if isinstance(stored, cif_mod.Cell)
                else cif_mod.Cell.from_dict(stored))
    except (KeyError, TypeError, ValueError):
        return None
    asym_symbols = meta.get("asym_symbols")
    asym_frac = meta.get("asym_frac")
    if not asym_symbols or asym_frac is None or not len(asym_frac):
        symbols = list(structure.symbols)
        if not symbols:
            return None
        return (cell, symbols, _cell_frame_fractional(structure, cell),
                np.ones(len(symbols)))
    data = cif_mod.CifData(
        cell,
        [cif_mod.SymOp.from_xyz(t) for t in (meta.get("symops") or ["x,y,z"])],
        list(asym_symbols), np.asarray(asym_frac, dtype=float),
        occupancy=meta.get("asym_occupancy"),
        disorder_groups=meta.get("asym_disorder_groups"),
        disorder_assemblies=meta.get("asym_disorder_assemblies"))
    report = {}
    symbols, cart = cif_mod.expand(
        data, whole_molecules=False, boundary=False,
        disorder=meta.get("disorder_policy") or cif_mod.POLICY_DOMINANT,
        report=report)
    if not symbols:
        return None
    frac = np.asarray(cart, dtype=float) @ np.linalg.inv(
        np.asarray(cell.matrix(), dtype=float))
    # The site each expanded atom came from - a LIST, one entry per content
    # atom - so a partially occupied site scatters as the file says it does
    # rather than as a whole atom.
    site_of = list(report.get("site_of") or ())
    site_occ = list(data.occupancy or ())
    # A SHARED site is several species on one position, and `expand`'s
    # minimum-image merge has already discarded all but the first of them
    # (round 42's ordering flaw). For a picture that costs a pie sphere; for
    # a structure factor it is the wrong scatterer, so the site is put back
    # together here - one term per species at the one position, which is what
    # the sum wants anyway.
    composition = cif_mod.site_composition(data, tol=0.1)
    out_symbols, out_frac, out_occ = [], [], []
    for i, sym in enumerate(symbols):
        site = int(site_of[i]) if i < len(site_of) else -1
        parts = composition.get(site)
        if parts:
            for element, share in parts:
                out_symbols.append(element)
                out_frac.append(frac[i])
                out_occ.append(float(share))
            continue
        out_symbols.append(sym)
        out_frac.append(frac[i])
        out_occ.append(float(site_occ[site])
                       if 0 <= site < len(site_occ) else 1.0)
    return (cell, out_symbols, np.asarray(out_frac, dtype=float),
            np.asarray(out_occ, dtype=float))


def pattern_for(structure, name="", **overrides):
    """This crystal's pattern, using the settings stored on it.

    Returns None for anything that is not a crystal - the caller has a
    molecule, and a powder pattern of a molecule is not a thing.
    """
    contents = cell_contents(structure)
    if contents is None:
        return None
    cell, symbols, frac, occ = contents
    settings = settings_of(structure)
    settings.update({k: v for k, v in overrides.items() if k in DEFAULTS})
    keep_absent = bool(overrides.get("keep_absent"))
    return compute(cell, symbols, frac, occupancy=occ,
                   components=components_of(settings),
                   two_theta_range=(float(settings["two_theta_min"]),
                                    float(settings["two_theta_max"])),
                   keep_absent=keep_absent,
                   name=name or getattr(structure, "name", "") or "")
