"""Turning a path into a `Sample`: the reader, plus what it does not say.

UI-free. The threading that keeps a folder-sized drop from freezing the
window lives in `ui/loading.py`; everything here is a plain function, so it
can be tested against a real file without a window.

**The wavelength is read where the file states it, and never made up.** A
`.raw`, a `.brml` and a Riet7 `.dat` state the tube's; a column file does
not, and its pattern is drawn in 2-theta only until the user gives one
(`Sample.wavelength_override`). A CIF or a card has none of its own: it is
simulated at the FIGURE's (`fit_to_figure`), over the angles its measured
patterns cover.
"""

import os

from . import crystal
from . import model
from . import profile
from . import readers

#: What the open dialog and a drop will accept.
READABLE = readers.READABLE

ReadError = readers.ReadError


def reader_origin():
    """Which reader is in use, for the About box: the program's own
    (`core/readers.py`)."""
    return ("this program's own (.raw, .brml, Riet7 .dat, columns; .cif "
            "and ICDD .xml cards simulated)")


def looks_readable(path):
    """Could this path be a pattern? Extension only, deliberately loose:
    the reader is the only thing that can really tell, and refuses with a
    reason."""
    return (os.path.isfile(str(path))
            and os.path.splitext(str(path))[1].lower() in READABLE)


def read_sample(path):
    """Read one file into a `Sample`. Raises `ReadError` with the reason."""
    pattern = readers.read(path)
    sample = model.Sample(path, pattern)
    if profile.EMBED_SOURCES:
        # The file itself, for the copy a session keeps of it.
        try:
            with open(path, "rb") as fh:
                sample.source_bytes = fh.read()
        except OSError:
            sample.source_bytes = None
    notes = []
    if pattern.head.get("note"):
        notes.append(pattern.head["note"])
    if not sample.simulated and sample.wavelength is None:
        notes.append("the file does not state its wavelength; d and Q "
                     "need one (its settings)")
    if notes:
        sample.note = "{}: {}".format(sample.name, "; ".join(notes))
    return sample


def fit_to_figure(sample, doc):
    """A simulated sample made for `doc`: at the figure's wavelength
    (`Document.reference_wavelength`), over the 2-theta its measured
    patterns cover (else `crystal.DEFAULT_RANGE`). Nothing for a measured
    one."""
    if not sample.simulated:
        return sample
    wavelength = doc.reference_wavelength()
    sample.wavelength_override = wavelength
    covered = doc.measured_range(wavelength)
    if covered:
        # Not below `crystal.LOWEST`: a pattern measured from near 0 would
        # give spacings of thousands of angstrom on a d axis.
        low = max(covered[0], crystal.LOWEST)
        sample.sim_range = ((low, covered[1]) if covered[1] > low
                            else tuple(crystal.DEFAULT_RANGE))
    else:
        sample.sim_range = tuple(crystal.DEFAULT_RANGE)
    return sample


def summary(sample):
    """One line about a file that was just opened, for the note line."""
    if sample.simulated:
        reflections = sample.reflections()
        lo, hi = sample.sim_range or crystal.DEFAULT_RANGE
        return "{}: {} reflections, simulated at {} from {:g} to {:g} deg".format(
            sample.name, len(reflections),
            crystal.describe_wavelength(sample.wavelength), lo, hi)
    x = sample.x
    bits = ["{}: {} points, {:.4g} to {:.4g} deg".format(
        sample.name, len(x), float(x[0]), float(x[-1])),
        sample.wavelength_text()]
    return ", ".join(bits)
