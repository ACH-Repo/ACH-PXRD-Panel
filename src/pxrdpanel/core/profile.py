"""What is particular to THIS plotter's data: powder diffraction patterns.

This program is one of a family of stacked-trace plotters on one handling.
The handling is shared; what a kind of data wants differently - how F frames
it, which way the x axis runs, what a new file does to the stack - is
gathered here, so a sibling changes this module rather than the handling
code.

UI-free: plain values.
"""

#: F frames in two steps, x first and then y, as a PXRD viewer does: for patterns the angle range is the question, and the y
#: range follows it.
FIT_STAGED = True


def x_reversed(doc):
    """True when the x axis runs from high to low, left to right: on a d
    axis, so a pattern keeps the look it has in 2-theta (large spacings,
    low angles, on the left). The view is still kept as (low, high); only
    the mapping onto the page turns it round (`PlotWidget.x_to_px`)."""
    from . import units
    return getattr(doc, "x_quantity", None) == units.D


#: The fitted x range is rounded to a whole grid at each end (5 and 50)
#: rather than stopping at the first and last sample (4.9996, 50.0012).
#: An end within `X_SNAP` of the axis span of a grid line is put on it.
X_ROUND = True
X_SNAP = 0.01

#: On a d axis, F frames the spacings of angles above this many degrees
#: 2-theta: a pattern measured from near 0 has spacings of thousands of
#: angstrom, which would leave the rest of it a sliver at one end.
D_FIT_FROM = 2.0

#: What F frames with nothing to plot, by quantity.
EMPTY_X = {"2theta": (5.0, 50.0), "d": (1.0, 20.0), "Q": (0.5, 5.0)}

#: A file opened into a figure goes BELOW the lowest pattern on show, one
#: step down, so a folder opens as a cascade rather than a heap of curves on
#: top of one another. The step is `arrange.suggested_step`'s.
STACK_NEW = True


#: Where a pattern IS, for the swipe that makes every curve taller in its
#: place (`PlotWidget.scale_intensity`): its background, which the peaks
#: stand up from. A low percentile rather than the minimum, so a dip or a
#: noisy background does not decide.
BASELINE_PERCENTILE = 10.0


def baseline(values, _doc=None):
    """The baseline of a curve's `values` as drawn (no offset), in the
    axis's unit. NaN where a curve breaks (sticks, ticks) is left out."""
    import numpy as np
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return 0.0
    return float(np.percentile(values, BASELINE_PERCENTILE))


#: A session keeps a COPY of every file it uses (compressed, inside it), so
#: a figure opens even after its files were moved or deleted; a moved file
#: is looked for beside the session first. A pattern is 20 to 300 kB (a
#: .brml is a zip already, and does not shrink).
EMBED_SOURCES = True


def name_label_corner(_doc=None):
    """Where a label naming a curve sits (Ctrl+T on selected curves,
    `MainWindow.name_labels`): below the curve's right end - the peaks
    stand up from it, and the high-angle end is the quiet one."""
    return "lower right"


def details(sample):
    """`[(what, value), ...]`: what `sample`'s file says of itself, for
    "Details..." - the facts that tell two files of one name apart."""
    import numpy as np
    pattern = sample.pattern
    rows = [("Wavelength", sample.wavelength_text())]
    for key, value in sorted((getattr(pattern, "head", None) or {}).items()):
        if value not in (None, ""):
            rows.append((str(key)[:1].upper() + str(key)[1:], str(value)))
    if getattr(pattern, "kind", ""):
        rows.append(("Read as", pattern.kind))
    if not sample.simulated and pattern.x is not None:
        x = np.asarray(pattern.x, dtype=float)
        rows.append(("Points", str(len(x))))
        if len(x):
            rows.append(("2-theta", "{:g} - {:g}".format(
                float(np.nanmin(x)), float(np.nanmax(x)))))
    return rows
