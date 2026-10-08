"""Write docs/OPERATORS.md from the operator registry itself.

    python tools/gen_operators.py > docs/OPERATORS.md

MoloM keeps a hand-written OPERATORS.md and it drifts, because a list of
actions maintained beside the code is a list nobody updates in the same
commit. Here the document is GENERATED from the registry, so it cannot be
wrong about what exists, what key it has or when it is allowed. The only
hand-written parts are the prose at the top and the notes below, which is
the part a generator cannot supply.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: The one-line "why" for the operators where the label is not the whole
#: story. Everything else gets a blank.
NOTES = {
    "scan.multiplier": "or swipe with them selected; an xN label says it",
    "transform.grab": "then a number, Enter; Shift is precision, Ctrl snaps",
    "arrange.align": "closed-form fit to the first selected scan",
    "view.outliner": "the dock on the right",
    "object.settings": "double-click does the same",
    "measure.start": "the typed route; double-click-drag a curve is the quick one",
    "view.x_two_theta": "as measured, each pattern at its own wavelength",
    "view.x_d": "at each pattern's wavelength; positions and analyses follow",
    "view.x_q": "at each pattern's wavelength; positions and analyses follow",
    "scan.wavelength": "a number, an energy (keV) or a line (Cu Ka1); empty: the file's",
    "scan.draw_lines": "the N strongest reflections, dotted, across the plot",
    "view.norm_global": "lowest of all on show 0, highest 1; also the plot's right-click, Normalise",
    "label.add": "on selected curves: their names, nothing asked",
    "view.norm_band": "the peak dragged is 1, the offsets keep their places",
    "view.break": "or drag along a curve and pick Break from the list",
    "region.add": "or drag along a curve and pick Highlight",
    "region.magnify": "about the curve's own chord",
    "span.between": "two marker lines selected: the arrow follows them",
    "label.vline": "{} in the text is its position",
    "label.edges": "placed as Ctrl+T places them",
    "app.settings": "sizes, label alignment, pick distance",
    "app.about": "Help menu: version, readers, Qt",
    "app.operator_search": "also the Search button on the menu bar",
    "app.aliases_save": "a .json to share: dropped on a panel, it installs them",
    "app.aliases_install": "or drop the file on the window",
    "app.search_reset": "your aliases and the recent list; asked first",
    "edit.undo": "also walks back zoom, pan and fit, one gesture at a time",
    "legend.toggle": "or its tick in the outliner",
    "figure.layout": "exact cm/in and margins, saved with the session",
    "analysis.flush_left": "the selected labels, else all on the selected scans",
}

#: `enabled` is a predicate, so it cannot describe itself. These are its
#: sentences. An operator with no entry is always allowed.
WHEN = {
    "scan.multiplier": "a pattern with a height is selected",
    "scan.unscale": "a selected pattern is scaled",
    "label.multipliers": "a selected pattern is scaled",
    "view.norm_global": "they are not normalised together",
    "file.session_save": "a pattern is open",
    "file.session_save_as": "a pattern is open",
    "file.export_image": "a pattern is open",
    "file.export_csv": "a pattern is open",
    "view.fit": "a pattern is open",
    "view.x_range": "a pattern is open",
    "view.norm_band": "a pattern is open",
    "view.norm_none": "the patterns are normalised",
    "view.norm_range": "they are not normalised 0 to 1",
    "view.break": "a pattern is open",
    "view.unbreak": "the x axis is broken",
    "view.unlock_framing": "the framing is locked",
    "figure.tighten_margins": "the figure has an exact size",
    "figure.offset_markers": "a pattern is open",
    "select.all": "a pattern is open",
    "select.none": "something is selected",
    "select.offset_markers": "the offset markers are shown",
    "transform.grab": "something is selected",
    "object.settings": "something is selected",
    "object.hide": "something is selected",
    "object.remove": "something is selected",
    "object.remove_file": "a pattern is selected",
    "object.colour": "a pattern is selected",
    "object.colour_gradient": "two or more patterns selected",
    "select.same_sample": "a pattern is selected",
    "arrange.stack": "two or more patterns selected",
    "arrange.align": "two or more patterns selected",
    "arrange.distribute": "three or more patterns selected",
    "arrange.swap": "two patterns selected",
    "arrange.reset": "a selected pattern has an offset, or a label or the "
                     "legend is selected (then R rotates)",
    "transform.rotate": "a label or the legend is selected",
    "transform.scale": "something that scales is selected",
    "object.show_all": "something is hidden",
    "edit.undo": "there is something to undo",
    "edit.redo": "there is something to redo",
    "measure.start": "one pattern is selected",
    "measure.apply": "both cursors are down",
    "measure.cancel": "a measurement is under way",
    "region.add": "a pattern is open",
    "region.magnify": "a pattern is selected",
    "span.add": "a pattern is open",
    "span.between": "two marker lines are selected",
    "label.edges": "a pattern is open",
    "label.parent": "labels and one pattern are selected",
    "label.unparent": "a selected label belongs to a pattern",
    "analysis.show": "a selected pattern has an analysis",
    "analysis.hide": "a selected pattern shows an analysis",
    "analysis.flush_left": "an analysis, or a pattern with one shown, is "
                           "selected",
    "analysis.flush_right": "an analysis, or a pattern with one shown, is "
                            "selected",
    "analysis.flush_center": "an analysis, or a pattern with one shown, is "
                             "selected",
    "view.x_two_theta": "the x axis is not 2-theta",
    "view.x_d": "the x axis is not d",
    "view.x_q": "the x axis is not Q",
    "scan.wavelength": "a pattern is selected",
    "scan.draw_curve": "a selected simulation is drawn otherwise",
    "scan.draw_sticks": "a selected simulation is drawn otherwise",
    "scan.draw_ticks": "a selected simulation is drawn otherwise",
    "scan.draw_lines": "a selected simulation is drawn otherwise",
}

#: Symbols in labels, spelled out for an ASCII file.
SPELLED = {chr(0x3b8): "theta", chr(0xc5) + chr(0x207b) + chr(0xb9): "1/A",
           chr(0xc5): "A"}

ORDER = ("File", "Edit", "Select", "Transform", "Object", "View",
         "Analyse", "App")

HEAD = """# Operators

Every user-facing action, as registered in `ui/window.py` through
`core/ops.py`. The menus, the keyboard and the F3 palette all read that one
registry, so an action cannot exist in one of them and not in the others.

**Lights up when** is the `enabled` predicate. F3 lists an operator that is
not allowed right now, greyed out rather than hidden, because the palette is
also how somebody finds out what the program can do.

This file is GENERATED. After adding an operator, run:

    python tools/gen_operators.py > docs/OPERATORS.md
"""


def main():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from pxrdpanel.ui.window import MainWindow
    window = MainWindow()
    lines = [HEAD]
    by_category = {}
    for op in window.ops.all():
        by_category.setdefault(op.category, []).append(op)
    for category in ORDER + tuple(sorted(set(by_category) - set(ORDER))):
        entries = by_category.get(category)
        if not entries:
            continue
        lines.append("## {}\n".format(category))
        lines.append("| Operator | Key | Lights up when | Note |")
        lines.append("| :-- | :-- | :-- | :-- |")
        for op in entries:
            key = op.shortcut or op.key or ""
            when = WHEN.get(op.id, "always")
            if op.id in ("view.unit_t", "view.unit_a"):
                when = "the y axis shows the other"
            lines.append("| {} | {} | {} | {} |".format(
                op.label, "`{}`".format(key) if key else "", when,
                NOTES.get(op.id, "")))
        lines.append("")
    # ASCII, whatever the console's code page: printed into a file by a
    # Windows shell, a degree sign arrived as one cp1252 byte, which is
    # not UTF-8 at all.
    text = "\n".join(lines)
    for char, word in (("\u00b0", "deg"), ("\u2212", "-")):
        text = text.replace(char, word)
    for symbol, words in SPELLED.items():
        text = text.replace(symbol, words)
    print(text.encode("ascii", "backslashreplace").decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
