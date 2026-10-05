"""Patterns made from reflections: a CIF's crystal, an ICDD card's list.

UI-free. The crystallography is ACH-MoloM's, copied verbatim into
`core/_molom/` (see its docstring); this module is the adapter, as
`achdiff/core/cif.py` is in ACH-Diffraction-Analysis-Suite - the loading
and the site bookkeeping below follow that module, so a CIF simulated here
gives the pattern the suite's quick plot draws.

**A simulation has a wavelength because the FIGURE has one.** A structure
or a card fixes d-spacings, not angles; the angle of each reflection is
2 asin(lambda / 2d) for whatever wavelength it is drawn at, and so is its
Lorentz-polarisation factor. The loader takes the figure's wavelength
(`Document.reference_wavelength`) and the user can change it in the
file's settings; nothing here has one of its own.

What the two kinds give:

* a CIF: the structure factors summed over the full cell
  (`_molom.pxrd.compute`, MoloM's: Cromer-Mann-style scattering factors,
  the LP factor, Debye-Waller only where the file has displacement
  parameters - its `note` says when it had none), drawn as a pseudo-Voigt
  profile of FWHM `fwhm` (degrees 2-theta);
* a card: its observed relative intensities at its d values, drawn with
  the same profile. No LP factor is applied to them: they were measured
  with it.

Both are scaled so the strongest point is 100.
"""

import math
import os

import numpy as np

from ._molom import cif as _cif
from ._molom import pxrd as _pxrd
from ._molom import spacegroups as _spacegroups

#: The width a simulated peak is drawn with, degrees 2-theta (the suite's
#: quick plot uses the same).
DEFAULT_FWHM = 0.1
#: The 2-theta range a simulation covers when the figure has no measured
#: pattern to take one from.
DEFAULT_RANGE = (5.0, 50.0)
#: The lowest angle a simulation starts at, whatever the measured patterns
#: cover.
LOWEST = 1.0
#: What a simulation is drawn at when nothing in the figure states a
#: wavelength: copper K-alpha1, said wherever it is shown.
DEFAULT_WAVELENGTH = _pxrd.DEFAULT_WAVELENGTH
#: How many reflections a "lines" drawing shows, the strongest first, until
#: somebody says otherwise (the suite's `-r` default).
DEFAULT_STRONGEST = 10

#: How a simulated pattern can be drawn.
DRAW_CURVE = "curve"        # the profile, a curve like any other
DRAW_STICKS = "sticks"      # a line per reflection, as tall as it is strong
DRAW_TICKS = "ticks"        # a row of equal ticks: where, not how strong
DRAW_LINES = "lines"        # dotted lines across the plot, the N strongest
DRAWS = (DRAW_CURVE, DRAW_STICKS, DRAW_TICKS, DRAW_LINES)
DRAW_TITLES = {DRAW_CURVE: "Simulated pattern", DRAW_STICKS: "Sticks",
               DRAW_TICKS: "Tick row", DRAW_LINES: "Lines across the plot"}
#: A tick row's ticks, on the profile's scale (its strongest point is 100).
TICK_HEIGHT = 25.0

#: The emission lines a wavelength can be named by (`parse_source`).
SourceError = _pxrd.SourceError


class CifError(ValueError):
    """A CIF that could not be turned into something to diffract from."""


class Phase(object):
    """One crystal from a CIF: its cell, its FULL cell contents (the
    asymmetric unit expanded by the symmetry), its name."""

    def __init__(self, cell, symbols, frac, occupancy, naming=None,
                 title="", notes=()):
        self.cell = cell
        self.symbols = list(symbols)
        self.frac = np.asarray(frac, dtype=float).reshape(-1, 3)
        self.occupancy = np.asarray(occupancy, dtype=float)
        self.naming = naming
        self.title = str(title)
        self.notes = list(notes)

    @property
    def space_group(self):
        if self.naming is None:
            return ""
        return str(getattr(self.naming, "setting_short", "")
                   or getattr(self.naming, "short", "")
                   or getattr(self.naming, "given", "") or "")


def load_cif(path):
    """Read a CIF into a `Phase`. Raises `CifError` with the reason."""
    path = str(path)
    name = os.path.basename(path)
    try:
        # errors="replace": a stray Latin-1 byte in a publication title is
        # no reason to refuse a structure.
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = _cif.parse_cif(fh.read())
    except OSError as exc:
        raise CifError("could not read {}: {}".format(name, exc))
    except Exception as exc:                          # noqa: BLE001
        raise CifError("could not parse {}: {}".format(name, exc))
    report = {}
    try:
        symbols, cart = _cif.expand(data, whole_molecules=False,
                                    boundary=False, report=report)
    except Exception as exc:                          # noqa: BLE001
        raise CifError("could not expand {}: {}".format(name, exc))
    if not symbols:
        raise CifError("{} has no atoms to diffract from".format(name))
    frac = data.cell.to_fractional(cart)
    symbols, frac, occupancy = _site_contents(data, symbols, frac, report)
    notes = []
    missing = _pxrd.missing_species(symbols)
    if missing:
        notes.append("no scattering data for " + ", ".join(sorted(missing))
                     + "; those atoms contribute nothing")
    if getattr(data, "symmetry_note", ""):
        notes.append(str(data.symmetry_note))
    try:
        naming = _spacegroups.identify(
            symbol=data.spacegroup or "", number=int(data.it_number or 0),
            rhombohedral=data.cell.looks_rhombohedral())
    except Exception:                                 # noqa: BLE001
        naming = None
    info = getattr(data, "info", {}) or {}
    title = (info.get("name_common") or info.get("name_mineral")
             or info.get("formula_sum") or data.name or "")
    return Phase(data.cell, symbols, frac, occupancy, naming, title, notes)


def _site_contents(data, symbols, frac, report):
    """Per-atom occupancies for the expanded cell, shared sites split into
    one term per species, an over-full site (written as atoms per site,
    4.0 on a four-fold site) brought back to 1 - the suite's bookkeeping,
    for the same pattern."""
    site_of = list(report.get("site_of") or ())
    site_occ = list(data.occupancy or ())
    composition = _cif.site_composition(data, tol=0.1)
    scale = _overfull_site_scale(site_occ, composition)
    out_symbols, out_frac, out_occ = [], [], []
    for i, symbol in enumerate(symbols):
        site = int(site_of[i]) if i < len(site_of) else -1
        factor = scale.get(site, 1.0)
        parts = composition.get(site)
        if parts:
            for element, share in parts:
                out_symbols.append(element)
                out_frac.append(frac[i])
                out_occ.append(float(share) * factor)
            continue
        out_symbols.append(symbol)
        out_frac.append(frac[i])
        raw = float(site_occ[site]) if 0 <= site < len(site_occ) else 1.0
        out_occ.append(raw * factor)
    return (out_symbols, np.asarray(out_frac, dtype=float),
            np.asarray(out_occ, dtype=float))


def _overfull_site_scale(site_occ, composition):
    """`{site: factor}` bringing an asymmetric-unit site that totals more
    than 1 back to 1; a site totalling less is left as written."""
    scale = {}
    for site in set(range(len(site_occ))) | set(composition):
        parts = composition.get(site)
        if parts:
            total = sum(float(share) for _element, share in parts)
        elif 0 <= site < len(site_occ):
            total = float(site_occ[site])
        else:
            continue
        if total > 1.0:
            scale[site] = 1.0 / total
    return scale


# ------------------------------------------------------------ reflections
class Reflection(object):
    """One reflection as drawn: its d, its angle at the wavelength asked
    for, how strong (the strongest is 100), and its name.

    A CIF's reflection is MERGED (MoloM's `pxrd.compute`): every hkl that
    lands at the same angle is one peak, `equivalents` lists them, and
    their count is the multiplicity; `f2` is |F|^2 summed over them, `lp`
    the Lorentz-polarisation factor. A card's has none of the three
    (`equivalents` holds its own hkl, `f2` and `lp` are None)."""

    __slots__ = ("d", "two_theta", "intensity", "hkl", "equivalents", "f2",
                 "lp")

    def __init__(self, d, two_theta, intensity, hkl=None, equivalents=None,
                 f2=None, lp=None):
        self.d = float(d)
        self.two_theta = float(two_theta)
        self.intensity = float(intensity)
        self.hkl = tuple(hkl) if hkl else None
        self.equivalents = ([tuple(e) for e in equivalents] if equivalents
                            else ([self.hkl] if self.hkl else []))
        self.f2 = None if f2 is None else float(f2)
        self.lp = None if lp is None else float(lp)

    @property
    def multiplicity(self):
        """How many hkl land here; None for a card, which does not say."""
        return len(self.equivalents) if self.f2 is not None else None

    @property
    def q(self):
        """Q in 1/A: 2 pi / d, whatever the wavelength."""
        return 2.0 * math.pi / self.d

    @property
    def absent(self):
        """Allowed by the lattice, extinguished by the symmetry: |F|^2 is
        (as good as) zero. Only a CIF's list holds these."""
        return self.f2 is not None and self.f2 <= _pxrd.ABSENT_F2

    def label(self):
        return "({} {} {})".format(*self.hkl) if self.hkl else ""

    def name(self):
        """"(1 1 1) and 7 more": the plane, and how many others land with
        it (MoloM's readout)."""
        text = self.label()
        count = self.multiplicity
        if text and count and count > 1:
            text += " and {} more".format(count - 1)
        return text


def two_theta_of(d, wavelength):
    """2-theta in degrees of spacing `d` at `wavelength`, or None past the
    Ewald limit (a d shorter than half the wavelength)."""
    ratio = float(wavelength) / (2.0 * float(d))
    if not 0.0 < ratio <= 1.0:
        return None
    return math.degrees(2.0 * math.asin(ratio))


def simulate(pattern, wavelength, two_theta_range, fwhm=DEFAULT_FWHM):
    """`(x, y, reflections, note)` for a simulated `readers.Pattern` (a
    CIF's or a card's): the profile on a 2-theta grid over
    `two_theta_range`, the strongest point 100; the reflections inside the
    range, strongest first; and what qualifies the intensities, or ""."""
    lo, hi = sorted(float(v) for v in two_theta_range)
    lo = max(lo, 0.01)
    hi = min(hi, 179.0)
    fwhm = abs(float(fwhm)) or DEFAULT_FWHM
    step = _pxrd.step_for(fwhm)
    count = max(2, int(round((hi - lo) / step)) + 1)
    x = np.linspace(lo, hi, count)
    note = ""
    if pattern.phase is not None:
        phase = pattern.phase
        computed = _pxrd.compute(phase.cell, phase.symbols, phase.frac,
                                 occupancy=phase.occupancy,
                                 wavelength=float(wavelength),
                                 two_theta_range=(lo, hi))
        y = _pxrd.profile_at(computed, x, fwhm=fwhm)
        found = [_from_molom(r) for r in computed.reflections]
        note = computed.note
    else:
        found = []
        for d, intensity, hkl in pattern.reflections or ():
            angle = two_theta_of(d, wavelength)
            if angle is not None and lo <= angle <= hi:
                found.append(Reflection(d, angle, intensity, hkl))
        y = _profile(x, [(r.two_theta, r.intensity) for r in found], fwhm)
    top = float(np.max(y)) if len(y) else 0.0
    if top > 0:
        y = y * (100.0 / top)
    strongest = max((r.intensity for r in found), default=0.0)
    if strongest > 0:
        for r in found:
            r.intensity = r.intensity * 100.0 / strongest
    found.sort(key=lambda r: -r.intensity)
    return x, y, found, note


def _from_molom(r):
    """A `Reflection` from one of MoloM's merged ones."""
    return Reflection(r.d, r.two_theta, r.intensity, r.hkl,
                      equivalents=r.equivalents, f2=r.f2, lp=r.lp)


def reflection_list(pattern, wavelength, two_theta_range, absent=False):
    """`(reflections, note)`: the hkl LIST of a simulated pattern over
    `two_theta_range` at `wavelength`, ascending in angle, the strongest
    100. For a CIF, `absent` adds the reflections the lattice allows and
    the symmetry extinguishes (and the faint ones a pattern leaves out) -
    what an hkl list is opened to see (MoloM's "Reflections (hkl)" tab). A
    card's list is its own reflections in range."""
    lo, hi = sorted(float(v) for v in two_theta_range)
    lo = max(lo, 0.01)
    hi = min(hi, 179.0)
    if pattern.phase is not None:
        phase = pattern.phase
        computed = _pxrd.compute(phase.cell, phase.symbols, phase.frac,
                                 occupancy=phase.occupancy,
                                 wavelength=float(wavelength),
                                 two_theta_range=(lo, hi),
                                 keep_absent=bool(absent))
        found = [_from_molom(r) for r in computed.reflections]
        note = computed.note
    else:
        found = []
        for d, intensity, hkl in pattern.reflections or ():
            angle = two_theta_of(d, wavelength)
            if angle is not None and lo <= angle <= hi:
                found.append(Reflection(d, angle, intensity, hkl))
        note = ""
    strongest = max((r.intensity for r in found), default=0.0)
    if strongest > 0:
        for r in found:
            r.intensity = r.intensity * 100.0 / strongest
    found.sort(key=lambda r: r.two_theta)
    return found, note


def reflections_near(reflections, two_theta, reach):
    """The reflections within `reach` degrees of `two_theta`, nearest
    first: what the pointer is over, for the hover readout."""
    near = [r for r in reflections if abs(r.two_theta - two_theta) <= reach]
    near.sort(key=lambda r: abs(r.two_theta - two_theta))
    return near


def _profile(x, peaks, fwhm, eta=0.5):
    """A pseudo-Voigt (half Lorentzian, half Gaussian) of FWHM `fwhm` at
    each `(centre, height)`, on the sorted grid `x`, each evaluated only
    within `_pxrd.REACH_LORENTZIAN` widths of its centre."""
    y = np.zeros_like(x)
    sigma = fwhm / (2.0 * math.sqrt(2.0 * math.log(2.0)))
    half = fwhm / 2.0
    reach = fwhm * _pxrd.REACH_LORENTZIAN
    for centre, height in peaks:
        i0, i1 = np.searchsorted(x, [centre - reach, centre + reach])
        if i1 > i0:
            dx = x[i0:i1] - centre
            y[i0:i1] += height * (eta / (1.0 + (dx / half) ** 2)
                                  + (1.0 - eta) * np.exp(
                                      -0.5 * (dx / sigma) ** 2))
    return y


def sticks(reflections, ticks=False, strongest=None):
    """`(x, y)` in 2-theta for reflections drawn as sticks (or a tick row):
    a line from 0 up to each reflection's intensity (or `TICK_HEIGHT`),
    NaN between them so a curve breaks there. Ascending in x; the
    `strongest` N only, when given."""
    chosen = list(reflections)
    if strongest:
        chosen = chosen[:int(strongest)]
    chosen.sort(key=lambda r: r.two_theta)
    x = np.full(3 * len(chosen), np.nan)
    y = np.full(3 * len(chosen), np.nan)
    for i, r in enumerate(chosen):
        x[3 * i] = x[3 * i + 1] = r.two_theta
        y[3 * i] = 0.0
        y[3 * i + 1] = TICK_HEIGHT if ticks else r.intensity
    return x, y


def lines(reflections, strongest=None):
    """`(x, y)` for the dotted lines across the plot: the positions of the
    `strongest` N (all when 0), ascending; y all NaN - a line has no
    height of its own."""
    count = DEFAULT_STRONGEST if strongest is None else int(strongest)
    chosen = list(reflections)[:count] if count else list(reflections)
    x = np.array(sorted(r.two_theta for r in chosen), dtype=float)
    return x, np.full(len(x), np.nan)


def parse_wavelength(text):
    """A wavelength in angstrom from what somebody typed: a number
    ("1.5406", "1,5406 A"), an energy ("17 keV") or a line ("Cu Ka1", "Mo
    Ka1"); the FIRST line of a doublet. Raises `SourceError`."""
    return float(_pxrd.parse_source(text)[0][0])


def describe_wavelength(wavelength):
    """"1.5406 A (Cu K-alpha1)" - the number, and the line it is, if any."""
    value = float(wavelength)
    for element, table in sorted(_pxrd.LINES.items()):
        for line, known in sorted(table.items()):
            if abs(known - value) < 5e-5:
                name = {"Ka1": "K-alpha1", "Ka2": "K-alpha2",
                        "Kb": "K-beta"}.get(line, line)
                return "{:.5g} A ({} {})".format(value, element, name)
    return "{:.5g} A".format(value)
