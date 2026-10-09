"""What every panel of the family does the same way.

This file is the SAME in every panel: a change to the handling they share
comes with its test here, and the file is copied to each of them. The one
part a panel writes for itself is the `stack_window` fixture in its
conftest: a window with three curves on the plot.
"""

import numpy as np
import pytest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest


def _spread(window, offsets):
    """S on the three curves, placed at `offsets` first."""
    doc, plot = window.doc, window.plot
    scans = list(doc.scans)[:3]
    for scan, offset in zip(scans, offsets):
        scan.offset = offset
    window.refresh()
    plot.grab()
    doc.select_only(scans)
    assert plot.start_spread(scans)
    return scans


# ------------------------------------------- S on curves: what holds still
def test_s_holds_the_lowest_curve_still(stack_window):
    a, b, c = _spread(stack_window, (0.5, 0.2, 0.9))    # c, a, b from the top
    plot = stack_window.plot
    step = plot._scale["step"]
    assert b.offset == pytest.approx(0.2)
    assert a.offset == pytest.approx(0.2 + step)
    assert c.offset == pytest.approx(0.2 + 2 * step)
    assert plot._scale["anchor"] == "bottom"


def test_t_b_and_m_hold_a_curve_where_it_is_now(stack_window):
    """The key takes the place where that curve IS when it is pressed:
    nothing moves then, and the step changes about it from there on. The
    line runs through the held curve itself, not through its offset."""
    a, b, c = _spread(stack_window, (0.5, 0.2, 0.9))    # c, a, b from the top
    plot = stack_window.plot
    state = plot._scale

    def places():
        return (c.offset, a.offset, b.offset)

    assert state["level"] == pytest.approx(plot._curve_level(b))
    QTest.keyClicks(plot, "0.1")                       # a step about b
    assert places() == pytest.approx((0.4, 0.3, 0.2))
    QTest.keyClick(plot, Qt.Key_T)                     # hold the top
    assert state["anchor"] == "top"
    assert places() == pytest.approx((0.4, 0.3, 0.2))  # nothing jumped
    assert state["level"] == pytest.approx(plot._curve_level(c))
    QTest.keyClicks(plot, "5")                         # step 0.15
    assert places() == pytest.approx((0.4, 0.25, 0.1))
    QTest.keyClick(plot, Qt.Key_M)                     # hold the middle
    assert places() == pytest.approx((0.4, 0.25, 0.1))
    middle = sum(plot._curve_level(s) - s.offset for s in (a, b, c)) / 3
    assert state["level"] == pytest.approx(0.25 + middle)
    QTest.keyClick(plot, Qt.Key_Backspace)             # step 0.1
    assert places() == pytest.approx((0.35, 0.25, 0.15))
    QTest.keyClick(plot, Qt.Key_B)                     # hold the bottom
    assert places() == pytest.approx((0.35, 0.25, 0.15))
    assert state["level"] == pytest.approx(plot._curve_level(b))
    QTest.keyClick(plot, Qt.Key_Backspace)
    QTest.keyClicks(plot, "3")                         # step 0.3
    assert places() == pytest.approx((0.75, 0.45, 0.15))
    QTest.keyClick(plot, Qt.Key_Return)
    assert plot._scale is None
    assert places() == pytest.approx((0.75, 0.45, 0.15))


def test_a_held_place_survives_r_and_esc_puts_all_back(stack_window):
    a, b, c = _spread(stack_window, (0.5, 0.2, 0.9))
    plot = stack_window.plot
    QTest.keyClick(plot, Qt.Key_T)
    QTest.keyClick(plot, Qt.Key_R)                     # order turned over
    assert b.offset == pytest.approx(0.9)              # the top place held
    QTest.keyClick(plot, Qt.Key_Escape)
    assert (a.offset, b.offset, c.offset) == pytest.approx((0.5, 0.2, 0.9))


def test_one_undo_step_for_a_spread_held_at_the_top(stack_window):
    a, b, c = _spread(stack_window, (0.5, 0.2, 0.9))
    plot = stack_window.plot
    QTest.keyClick(plot, Qt.Key_T)
    QTest.keyClicks(plot, "0.1")
    QTest.keyClick(plot, Qt.Key_Return)
    assert (c.offset, a.offset, b.offset) == pytest.approx((0.9, 0.8, 0.7))
    stack_window.undo_step()
    assert (a.offset, b.offset, c.offset) == pytest.approx((0.5, 0.2, 0.9))


def test_p_keeps_the_stacks_own_gaps(stack_window):
    """P during S: the gaps the stack had, in proportion (an uneven ladder
    spread as a whole), the held place and the mean step kept."""
    a, b, c = _spread(stack_window, (0.5, 0.2, 0.9))    # gaps 0.4 and 0.3
    plot = stack_window.plot

    def places():
        return (c.offset, a.offset, b.offset)

    QTest.keyClick(plot, Qt.Key_P)                     # own gaps, mean 0.35
    assert not plot._scale["even"]
    assert places() == pytest.approx((0.9, 0.5, 0.2))
    QTest.keyClicks(plot, "0.7")                       # each gap twice
    assert places() == pytest.approx((1.6, 0.8, 0.2))
    QTest.keyClick(plot, Qt.Key_T)                     # hold the top
    QTest.keyClick(plot, Qt.Key_Backspace)
    QTest.keyClicks(plot, "35")                        # mean step 0.35
    assert places() == pytest.approx((1.6, 1.2, 0.9))
    QTest.keyClick(plot, Qt.Key_P)                     # even again
    assert places() == pytest.approx((1.6, 1.25, 0.9))
    QTest.keyClick(plot, Qt.Key_P)
    QTest.keyClick(plot, Qt.Key_R)      # order turned over, gaps stay put
    assert (b.offset, a.offset, c.offset) == pytest.approx((1.6, 1.2, 0.9))
    QTest.keyClick(plot, Qt.Key_Return)
    stack_window.undo_step()
    assert (a.offset, b.offset, c.offset) == pytest.approx((0.5, 0.2, 0.9))


# ----------------------------------- the swipe: taller, each in its place
def _swipe(plot, notches):
    """A plain wheel or two-finger swipe, `notches` detents up."""
    at = QPointF(plot.plot_rect().center())
    event = QWheelEvent(at, at, QPoint(0, 0), QPoint(0, int(120 * notches)),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    plot.wheelEvent(event)


def test_a_swipe_makes_every_curve_taller_in_its_place(stack_window):
    """No curve moves up or down; each grows about its own baseline
    (`profile.baseline`: what a baseline is depends on the data).
    The axis is what is scaled - the offsets follow - so the data is never
    multiplied. A hidden curve follows too, and one undo step puts the
    offsets and the framing back."""
    window = stack_window
    doc, plot = window.doc, window.plot
    a, b, c = list(doc.scans)[:3]
    a.offset, b.offset, c.offset = 0.5, 0.2, 0.9
    window.refresh()
    plot.grab()
    plot.set_main_view(*plot.main_view())  # as shown: hiding c keeps it
    stored = [s.offset for s in doc.scans]

    def place(scan):
        return plot.sy_to_px(scan, plot._level_of(scan) + scan.offset)

    def height(scan):
        values = plot._trace_of(scan).y
        return abs(plot.sy_to_px(scan, float(np.nanmax(values)))
                   - plot.sy_to_px(scan, float(np.nanmin(values))))

    places = [place(s) for s in (a, b, c)]
    heights = [height(s) for s in (a, b, c)]
    c.visible = False                                  # hidden: follows
    window.refresh()
    _swipe(plot, 2)                                    # 1.2 ** 2 taller
    c.visible = True
    window.refresh()
    assert [place(s) for s in (a, b, c)] == pytest.approx(places, abs=0.5)
    assert [height(s) for s in (a, b, c)] == pytest.approx(
        [h * 1.44 for h in heights], rel=1e-3)
    c.visible = False
    window.refresh()
    window.undo_step()                                 # one step
    c.visible = True
    window.refresh()
    assert [s.offset for s in doc.scans] == pytest.approx(stored)
    assert [height(s) for s in (a, b, c)] == pytest.approx(heights, rel=1e-3)


# ------------------------------------------------- artists, several at once
def _bounds(plot, artist):
    rect = plot.plot_rect()
    return plot.rotated_bounds(artist, plot.artist_box(artist, rect), rect)


def _on_curve(window, scan, text):
    """A label that HANGS from its curve, at the middle sample."""
    plot = window.plot
    label = window.doc.add_label(text, 0.5, 0.5, scan)
    trace = plot._trace_of(scan)
    label.at = ("i", trace.first + len(trace.x) // 2)
    label.dx, label.dy = 4.0, 4.0
    return label


def test_align_shows_at_once_and_moves_a_label_on_a_curve(stack_window):
    """The aligned places are drawn at once (the cached picture stayed
    until the selection changed), and a label hanging from a curve moves
    too: its place is `at`, `dx`, `dy`, not x and y."""
    window = stack_window
    doc, plot = window.doc, window.plot
    free = doc.add_label("free", 0.2, 0.15)
    hanging = _on_curve(window, doc.scans[0], "hanging")
    window.refresh()
    plot.grab()
    doc.select_only([free, hanging])
    assert window.run_op("arrange.align_top")
    assert plot._cache is None                         # redrawn
    plot.grab()
    assert _bounds(plot, hanging).top() == pytest.approx(
        _bounds(plot, free).top(), abs=0.5)
    window.undo_step()
    assert hanging.dy == 4.0


def test_artists_spaced_evenly(stack_window):
    window = stack_window
    doc, plot = window.doc, window.plot
    labels = [doc.add_label(text, 0.3, y) for text, y in
              (("one", 0.15), ("two", 0.3), ("three", 0.85))]
    window.refresh()
    plot.grab()
    doc.select_only(labels)
    assert window.run_op("arrange.distribute_y")
    plot.grab()
    boxes = sorted((_bounds(plot, label) for label in labels),
                   key=lambda box: box.top())
    gaps = [b.top() - a.bottom() for a, b in zip(boxes, boxes[1:])]
    assert gaps[0] == pytest.approx(gaps[1], abs=0.5)
    assert boxes[0].top() == pytest.approx(_bounds(plot, labels[0]).top())


def test_a_box_selects_artists(stack_window):
    window = stack_window
    doc, plot = window.doc, window.plot
    near = [doc.add_label("a", 0.1, 0.1), doc.add_label("b", 0.25, 0.15)]
    far = doc.add_label("c", 0.85, 0.85)
    window.refresh()
    plot.grab()
    area = _bounds(plot, near[0]).united(_bounds(plot, near[1])).adjusted(
        -2.0, -2.0, 2.0, 2.0)
    chosen = plot.select_in_box(area.topLeft(), area.bottomRight())
    assert near[0] in chosen and near[1] in chosen and far not in chosen
    assert near[0].selected and not far.selected


# ------------------------------------------- drafts, then the full drawing
def test_a_change_is_drafted_then_drawn_in_full(stack_window, monkeypatch):
    """A change shows at once as a draft - the curves as hairlines - and is
    drawn in full when nothing has changed for `SETTLE_MS`."""
    import sys
    window, plot = stack_window, stack_window.plot
    monkeypatch.setattr(type(plot), "SETTLE_MS", 120)
    painter = sys.modules[type(plot).__module__]._DraftPainter
    drafted = []
    real = painter.drawPolyline
    monkeypatch.setattr(painter, "drawPolyline", lambda self, *a: (
        drafted.append(1), real(self, *a))[1])
    plot.grab()
    QTest.qWait(300)                  # the window settling from its setup
    plot.grab()
    assert not plot.drafting()
    window.doc.scans[0].offset += 0.1
    window.refresh()
    plot.grab()
    assert plot.drafting() and drafted                 # at once, a draft
    drafted.clear()
    QTest.qWait(300)
    plot.grab()
    assert not plot.drafting() and not drafted         # then in full


# ------------------------------------------------------------------ colours
def _model(window):
    import sys
    return sys.modules[type(window.doc).__module__]


def _settings(window, obj):
    window.edit_object(obj)
    return window.popups()[-1]


def _colour_row(dialog):
    """The object's own colour row: swatch, #rrggbb, Inherit."""
    from PySide6.QtWidgets import QWidget
    return [w for w in dialog.findChildren(QWidget)
            if getattr(w, "hex", None) is not None
            and getattr(w, "inherit", None) is not None][0]


def test_a_colour_inherited_follows_its_donor(stack_window):
    """Inherit, then a click on the donor: the colour follows it from then
    on - through undo - until a colour of its own is chosen. The session
    keeps the link."""
    window = stack_window
    doc, plot = window.doc, window.plot
    model = _model(window)
    a, b = doc.scans[0], doc.scans[1]
    note = doc.add_label("note", 0.5, 0.5)
    window.refresh()
    dialog = _settings(window, note)
    row = _colour_row(dialog)
    row.inherit.click()
    assert plot.picking()
    plot._picking(a)                                   # the click on a
    assert note.colour_from is a and note.colour == a.colour
    dialog.close()
    window.undo.set_props([(a, "colour", "#123456")], "colour")
    window.refresh()
    assert note.colour == "#123456"                    # follows
    window.undo_step()
    window.refresh()
    assert note.colour == a.colour != "#123456"
    links = model.colour_links(doc)
    note.colour_from = None
    model.restore_colour_links(doc, links)
    assert note.colour_from is a
    # a circle is refused, and a colour of its own ends the link
    b.colour_from = None
    assert model.inherits_from(note, a) and not model.inherits_from(a, note)
    dialog = _settings(window, note)
    row = _colour_row(dialog)
    row.hex.setText("#00ff00")
    row.hex.editingFinished.emit()
    assert note.colour == "#00ff00" and note.colour_from is None
    dialog.close()


def test_the_hex_field_shows_and_takes_a_colour(stack_window):
    window = stack_window
    scan = window.doc.scans[0]
    dialog = _settings(window, scan)
    row = _colour_row(dialog)
    assert row.hex.text() == scan.colour.lower()
    row.hex.setText("ff8800")                          # no # needed
    row.hex.editingFinished.emit()
    assert scan.colour == "#ff8800"
    dialog.revert()
    assert scan.colour != "#ff8800"


def test_a_label_given_to_a_curve_wears_its_colour(stack_window):
    window = stack_window
    doc = window.doc
    label = doc.add_label("mine", 0.4, 0.4)
    label.colour = "#abcdef"
    window.refresh()
    window.parent_labels([label], doc.scans[1])
    assert label.scan is doc.scans[1] and label.colour == "auto"
    window.undo_step()
    assert label.colour == "#abcdef"


# ------------------------------------------- a label on a curve: x and y
def test_a_label_on_a_curve_is_placed_by_x_and_y(stack_window):
    """The regular Place rows, not a point, a distance and a sideways
    shift: x and y say where it is, typing them puts it there - and it
    still hangs from its curve, so it goes where the curve goes."""
    window = stack_window
    doc, plot = window.doc, window.plot
    scan = doc.scans[0]
    label = _on_curve(window, scan, "hanging")
    window.refresh()
    plot.grab()
    dialog = _settings(window, label)
    place = dialog.transform
    assert dialog.hang_dy.isHidden() and dialog.hang_at.isHidden()
    assert not place.at_x.isHidden() and not place.at_y.isHidden()
    before = plot.artist_point(label)
    place.at_x.setValue(place.at_x.value() + 0.05)
    after = plot.artist_point(label)
    assert label.attached and after[0] > before[0] + 5.0
    assert abs(after[1] - before[1]) < 1.0
    lo, hi = plot.main_view()                  # a fifth of the axis up
    window.undo.set_props([(scan, "offset", scan.offset + 0.2 * (hi - lo))],
                          "move")
    window.refresh()
    plot.grab()
    assert plot.artist_point(label)[1] < after[1] - 5.0    # went with it
    dialog.close()


# ------------------------------------------------ exact size, from screen
def _figure_module(plot):
    import sys
    return sys.modules[type(plot).__module__].figure_module


def test_a_blade_makes_the_figure_exact_as_it_is_on_screen(stack_window):
    """The blades are there on every figure; taking one on a figure that
    is not of an exact size makes it exact FROM THE SCREEN - the axes box
    stays where it is - and says so in orange. One undo step back."""
    window, plot = stack_window, stack_window.plot
    figure_module = _figure_module(plot)
    layout = window.doc.figure
    layout.mode = figure_module.MODE_WINDOW
    window.refresh()
    plot.grab()
    screen = plot.exact_from_screen()
    box = plot.plot_rect()
    plot._page_handles_shown = True
    assert plot.page_margins_editable()
    plot._start_page_margin_drag("left", QPointF(4.0, 4.0))
    assert layout.mode == figure_module.MODE_SIZE
    assert (layout.width, layout.height) == pytest.approx(
        (screen["width"], screen["height"]))
    assert plot.flashing().startswith("Exact size now")
    now = plot.plot_rect()
    assert (now.left(), now.top(), now.width(), now.height()) == \
        pytest.approx((box.left(), box.top(), box.width(), box.height()),
                      abs=0.5)
    plot._finish_page_margin_drag(cancel=True)
    window.undo_step()
    assert layout.mode == figure_module.MODE_WINDOW


def test_the_size_window_goes_exact_from_the_screen(stack_window):
    import sys
    window, plot = stack_window, stack_window.plot
    figure_module = _figure_module(plot)
    layout = window.doc.figure
    layout.mode = figure_module.MODE_WINDOW
    layout.width, layout.height = 8.5, 6.5             # the stored size
    window.refresh()
    plot.grab()
    screen = plot.exact_from_screen()
    dialogs = sys.modules[type(window).__module__.rsplit(".", 1)[0]
                          + ".dialogs"]
    dialog = dialogs.FigureSettings(window, layout, plot=plot)
    dialog.mode.setCurrentIndex(dialog.mode.findData(
        figure_module.MODE_SIZE))
    assert layout.mode == figure_module.MODE_SIZE
    assert layout.width == pytest.approx(screen["width"]) != 8.5
    assert dialog.fig_w.value() == pytest.approx(screen["width"], abs=0.01)
    dialog.revert()
    assert layout.mode == figure_module.MODE_WINDOW and layout.width == 8.5


# --------------------------------------------- a session's files, moved
def test_a_moved_file_is_found_beside_the_session(stack_window, tmp_path):
    """A session names its files by path; a file that is not there any
    more is looked for beside the session (and in the folders under it),
    and its curves come back as they were. The reader is a stub: what is
    tested is where the session looks."""
    import copy
    import json
    import os
    import sys
    window = stack_window
    package = type(window.doc).__module__.split(".")[0]
    session = sys.modules[package + ".core.session"]
    state = session.to_state(window.doc)
    originals = {}
    moved = tmp_path / "moved"
    moved.mkdir()
    for sample in window.doc.samples:
        name = os.path.basename(sample.path.replace("\\", "/"))
        assert not os.path.exists(sample.path)
        originals[name.lower()] = sample
        (moved / name).write_text("stand-in")
    path = tmp_path / "figure.session"
    path.write_text(json.dumps(state), encoding="utf-8")

    def read(where):
        twin = copy.copy(originals[os.path.basename(where).lower()])
        twin.path, twin.scans = where, []
        return twin

    doc, problems = session.load(str(path), read)
    assert len(doc.samples) == len(window.doc.samples)
    assert all(os.path.dirname(s.path) == str(moved) for s in doc.samples)
    assert len(doc.scans) == len(window.doc.scans)
    assert any("found beside the session" in p for p in problems)


def test_a_flash_ticks_and_fades_green_or_orange(stack_window):
    """`flash` keeps (text, start, seconds, warn); every reader of it takes
    the first three. Unpacking all four once raised on every tick."""
    plot = stack_window.plot
    for warn in (False, True):
        plot.flash("a note", seconds=0.01, warn=warn)
        plot.grab()
        QTest.qWait(30)
        plot._flash_tick()
        assert plot.flashing() is None


# ------------------------- labels named after their curves; pick distance
def _plot_module(plot):
    import sys
    return sys.modules[type(plot).__module__]


def test_ctrl_t_names_every_selected_curve_without_asking(stack_window):
    """Ctrl+T on selected curves: one label each, saying the curve's name,
    hanging from it at the corner the panel's profile gives - nothing is
    asked first (a dialog fails the test: conftest's `no_modal_loops`)."""
    window = stack_window
    doc, plot = window.doc, window.plot
    profile = _plot_module(plot).profile
    scans = list(doc.scans)[:2]
    doc.select_only(scans)
    before = list(doc.labels)
    assert window.run_op("label.add")
    made = [label for label in doc.labels if label not in before]
    assert [label.text for label in made] == [s.display_name()
                                              for s in scans]
    assert [label.scan for label in made] == scans
    assert all(label.attached for label in made)
    assert doc.selected() == made
    plot.grab()
    rect = plot.plot_rect()
    vertical, horizontal = profile.name_label_corner(doc).split()
    for label in made:
        x, _y = plot.artist_point(label, rect)
        assert (x > rect.center().x()) == (horizontal == "right")
        assert (label.dy > 0) == (vertical == "lower")      # down is +
    doc.select_only(scans)
    window.run_op("label.add")                              # not twice
    assert len(doc.labels) == len(before) + 2
    window.undo_step()
    assert doc.labels == before


def test_ctrl_t_on_nothing_puts_one_free_label(stack_window):
    window = stack_window
    window.doc.select_only([])
    before = len(window.doc.labels)
    assert window.run_op("label.add")
    [label] = window.doc.labels[before:]
    assert label.text == "Label" and label.scan is None
    assert window.doc.selected() == [label]


def test_the_pick_distance_is_8_until_chosen(stack_window):
    style = _plot_module(stack_window.plot).style
    assert style.builtin("pick_radius") == 8.0
    assert style.preference("pick_radius") == 8.0


# ------------------------------------------------- colours on a white page
def test_a_white_page_draws_every_colour_as_on_screen(stack_window):
    """The default palette and a colour somebody chose alike: drawn on a
    white page - and so in every export - exactly as on the screen.
    Darkened to read on paper, the palette's orange came out brown."""
    import sys
    window = stack_window
    plot_module = _plot_module(window.plot)
    palette = sys.modules[type(window.doc).__module__].PALETTE
    chosen = "#ffc000"
    for colour in (chosen,) + tuple(palette):
        assert plot_module.paper_colour(colour).name() == colour.lower()
    scan = window.doc.scans[0]
    for colour in (chosen, palette[1]):
        scan.colour = colour
        window.refresh()
        with plot_module.themed(window.plot, plot_module.THEME_LIGHT):
            trace = window.plot._trace_of(scan)
            assert plot_module.trace_colour(trace).name() == colour.lower()


# ---------------------------------- settings windows: what comes first
def _rows(dialog):
    """The rows of a settings window as shown, top to bottom: each row's
    label, else its check box's text; hidden rows left out."""
    from PySide6.QtWidgets import QAbstractButton, QFormLayout
    form = dialog.findChildren(QFormLayout)[0]
    out = []
    for row in range(form.rowCount()):
        label = form.itemAt(row, QFormLayout.LabelRole)
        field = (form.itemAt(row, QFormLayout.FieldRole)
                 or form.itemAt(row, QFormLayout.SpanningRole))
        widget = field.widget() if field is not None else None
        if widget is not None and widget.isHidden():
            continue
        if label is not None and label.widget().text():
            out.append(label.widget().text())
        elif isinstance(widget, QAbstractButton):
            out.append(widget.text())
        else:
            out.append("")
    return out


def test_settings_windows_put_the_text_and_the_colour_first(stack_window):
    """A label's, a note's and a band marker's window: its text first;
    a marker's line position next; then the colour; Layer last. A band
    marker has no arrow rows, and a label's arrow rows show only once it
    is a note (Christian, 2026-10-05)."""
    window = stack_window
    plot = window.plot
    rect = plot.plot_rect()
    window.doc.select_all(False)
    middle = QPointF(rect.center().x(), rect.center().y())
    label = window.add_label(text="a label", at=middle)
    note = window.add_note(text="a note", at=middle)
    marker = window.add_marker_line(text="a marker", at=middle)
    shown = {}
    for obj in (label, note, marker):
        window.edit_object(obj)
        shown[obj] = window._dialogs[-1]
    marker_rows = _rows(shown[marker])
    assert marker_rows[:3] == ["Text", "Line at", "Colour"]
    assert marker_rows[-1] == "Layer"
    for gone in ("Points at", "Arrow from", "Arrow colour"):
        assert gone not in marker_rows
    assert not any(row.startswith("Leader") for row in marker_rows)
    note_rows = _rows(shown[note])
    assert note_rows[:2] == ["Text", "Colour"]
    assert note_rows.index("Points at") < note_rows.index("Size")
    assert note_rows[-1] == "Layer"
    dialog = shown[label]
    label_rows = _rows(dialog)
    assert label_rows[:2] == ["Text", "Colour"]
    assert "Points at" not in label_rows
    dialog.leader.setChecked(True)                  # a note now
    assert "Points at" in _rows(dialog)
    assert _rows(dialog)[-1] == "Layer"
    for dialog in shown.values():
        dialog.close()


# ------------------------------------------- F3: used last, your aliases
def _palette_rows(palette):
    """`(text, op id or None)` of every row of the F3 list."""
    from PySide6.QtCore import Qt
    rows = []
    for row in range(palette.list.topLevelItemCount()):
        item = palette.list.topLevelItem(row)
        rows.append((item.text(0), item.data(0, Qt.UserRole)))
    return rows


def test_f3_lists_the_operators_used_last_on_top(stack_window):
    """Opened empty, F3 lists the operators run from it last on top, the
    newest selected, so F3 then Enter runs it again (Christian,
    2026-10-05)."""
    from PySide6.QtCore import Qt
    window = stack_window
    palette = window.operator_palette()
    assert palette.RECENT_TITLE not in [t for t, _i in _palette_rows(palette)]
    window.palette_ran("select.all")
    window.palette_ran("view.fit")
    palette = window.operator_palette()
    rows = _palette_rows(palette)
    assert rows[0] == (palette.RECENT_TITLE, None)
    assert [i for _t, i in rows[1:3]] == ["view.fit", "select.all"]
    assert rows[3] == (palette.ALL_TITLE, None)
    assert "view.fit" not in [i for _t, i in rows[4:]]   # listed once
    assert palette.list.currentItem().data(0, Qt.UserRole) == "view.fit"
    palette.edit.setText("fit")                     # typing: no heading
    assert palette.RECENT_TITLE not in [t for t, _i in _palette_rows(palette)]


def test_your_own_alias_finds_an_operator_and_travels_in_a_file(
        stack_window, tmp_path):
    """An alias of the user's own finds its operator and is shown beside
    it; saved to a .json it installs into another installation (here,
    after a reset to factory) by dropping the file on the window, asked
    first; an operator the panel does not have is skipped."""
    import json
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    window = stack_window
    palette = window.operator_palette()
    fit = window.ops.get("view.fit")
    assert palette.add_alias(fit, "  zoom   home ") == "zoom home"
    palette.edit.setText("zoom home")
    current = palette.list.currentItem()
    assert current.data(0, Qt.UserRole) == "view.fit"
    assert current.text(1) == "zoom home"
    menu = palette.menu_for(current)
    assert [a.text() for a in menu.actions()][-1] == \
        'Remove my alias "zoom home"'
    shared = window.save_aliases(path=str(tmp_path / "mine.json"))
    window.palette_ran("view.fit")
    window.confirm_reset = lambda: True
    assert window.reset_operator_search()
    palette = window.operator_palette()
    palette.edit.setText("zoom home")
    assert palette.list.currentItem() is None or \
        palette.list.currentItem().data(0, Qt.UserRole) != "view.fit"
    assert palette.RECENT_TITLE not in [
        t for t, _i in _palette_rows(window.operator_palette())]
    # somebody's file, with one operator this panel does not have
    with open(shared, encoding="utf-8") as fh:
        stored = json.load(fh)
    stored["aliases"]["no.such.operator"] = ["nothing"]
    with open(shared, "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    asked = []
    window.confirm_aliases = (
        lambda path, app, added, unknown:
        asked.append((added, unknown)) or True)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(shared)])
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton,
                      Qt.NoModifier)
    window.dropEvent(drop)
    assert asked == [([("view.fit", "zoom home")], ["no.such.operator"])]
    palette = window.operator_palette()
    palette.edit.setText("zoom home")
    assert palette.list.currentItem().data(0, Qt.UserRole) == "view.fit"
    # dropped again: nothing new, nothing asked
    window.dropEvent(QDropEvent(QPointF(10, 10), Qt.CopyAction, mime,
                                Qt.LeftButton, Qt.NoModifier))
    assert len(asked) == 1


# --------------------------------- the source file, typed in its settings
def test_a_path_pasted_with_quotes_changes_the_source_file(stack_window,
                                                           tmp_path):
    """A file's window, and a curve's, take another path typed or pasted
    as Windows copies it (in quotes) and put that file in its place
    ("Change the source file"): the window closes first, so its own undo
    step comes before the change, and opens again on the new file. A path
    that is not a file is marked and does nothing (Christian,
    2026-10-05)."""
    import os
    from importlib import import_module
    window = stack_window
    dialogs = import_module(type(window).__module__.rsplit(".", 1)[0]
                            + ".dialogs")
    assert dialogs.clean_path('  "C:\\data\\a b.txt" ') == os.path.normpath(
        "C:/data/a b.txt")
    assert dialogs.clean_path("'C:/data/x.txt'") == os.path.normpath(
        "C:/data/x.txt")
    assert dialogs.clean_path("") == ""
    other = tmp_path / "another file.dat"
    other.write_text("not read here")
    changed = []
    window.change_source = lambda sample, path: changed.append(
        (sample, path))
    scan = window.doc.scans[0]
    sample = scan.sample
    for obj in (sample, scan):
        window.edit_object(obj)
        dialog = window._dialogs[-1]
        row = dialog.file_row
        assert row.path.text() == sample.path
        row.path.setText(str(tmp_path / "missing.dat"))
        row.path.editingFinished.emit()
        assert changed == [] and "d04040" in row.path.styleSheet()
        row.path.setText('"{}"'.format(other))       # as Ctrl+Shift+C copies
        assert row.path.text() == str(other)
        row.path.editingFinished.emit()
        assert changed == [(sample, os.path.normpath(str(other)))]
        assert not dialog.isVisible()
        reopened = window._dialogs[-1]
        assert reopened is not dialog and reopened.obj is obj
        reopened.close()
        del changed[:]


# --------------------------------------- a file the session cannot find
def _stand_ins(window, folder):
    """The window's files written into `folder` as "Measurement-k" (their
    extensions kept), each holding its number, and a reader that reads it:
    a copy of that sample. Returns the reader."""
    import copy
    import os
    samples = list(window.doc.samples)
    folder.mkdir()
    for k, sample in enumerate(samples):
        ext = os.path.splitext(sample.path)[1]
        sample.path = str(folder / "Measurement-{}{}".format(k, ext))
        with open(sample.path, "w") as fh:
            fh.write(str(k))

    def read(where):
        with open(where) as fh:                 # raises when it is not there
            twin = copy.copy(samples[int(fh.read())])
        twin.path, twin.scans = str(where), []
        return twin

    return read


def test_a_missing_file_is_kept_and_found_again(stack_window, tmp_path,
                                                monkeypatch):
    """A file a session cannot find keeps a row in the outliner, and what
    the session holds of it - its curves, their analyses, the labels on
    them - is saved again as it was; found under a folder by a name like
    its own (a copy's "(1)" ranked before another run's number), the
    figure opens again with all of it back."""
    import json
    import os
    import sys
    window = stack_window
    package = type(window.doc).__module__.split(".")[0]
    session = sys.modules[package + ".core.session"]
    model = sys.modules[package + ".core.model"]
    loader = sys.modules[package + ".core.loader"]
    monkeypatch.setattr(loader, "read_sample",
                        _stand_ins(window, tmp_path / "data"))
    doc = window.doc
    first = [s for s in doc.scans if s.sample is doc.samples[0]]
    others = [s for s in doc.scans if s.sample is not doc.samples[0]]
    doc.add_label("on the first", 0.5, 0.5, first[0])
    doc.add_label("free", 0.2, 0.2)
    others[-1].colour_from = others[0]               # a link after the gap
    window.refresh()
    path = str(tmp_path / "figure.session")
    window.save_session(path=path)
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    lost = doc.samples[0].path
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    stem, ext = os.path.splitext(os.path.basename(lost))
    copied = str(elsewhere / "{}(1){}".format(stem, ext))  # a download's
    os.rename(lost, copied)

    window.open_session(path)
    doc = window.doc
    assert [g.name for g in doc.missing] == [os.path.basename(lost)]
    gone = doc.missing[0]
    assert len(doc.samples) == len(saved["samples"]) - 1
    assert len(doc.scans) == len(others)
    assert [lb.text for lb in doc.labels] == ["free"]
    assert gone.scans and [e["text"] for _p, e in gone.labels] == [
        "on the first"]
    assert doc.scans[-1].colour_from is doc.scans[0]   # the right objects
    rows = [item for item in window.outliner._items()
            if window.outliner._object(item) is gone]
    assert rows and rows[0].text(0) == gone.name
    menu = [a.text() for a in window.context_menu_for(gone).actions()]
    assert any(t.startswith("Locate") for t in menu)
    assert "Find in a folder..." in menu and "Details..." in menu
    facts = dict(window.details_dialog(gone).rows)
    assert facts["File"] == gone.name and facts["Why"] == "not found"
    facts = dict(window.details_dialog(doc.samples[0]).rows)
    assert "Size" in facts and len(facts["SHA-256"]) == 64

    # Saved now, it is all still there, where it was.
    again = session.to_state(doc)
    for key in ("samples", "scans", "labels", "colour_links"):
        assert again[key] == saved[key], key
    assert not window.is_modified()

    # Forgotten, it is gone from the next save; undone, back.
    window.forget_missing(gone)
    assert doc.missing == [] and len(session.to_state(doc)["samples"]) == \
        len(saved["samples"]) - 1
    window.undo.undo()
    assert doc.missing == [gone]

    # Looked for under the folder: the copy is offered first, then the
    # other runs ("Measurement-1" is as like "Measurement-0").
    hits, complete = session.similar_files(str(tmp_path), [gone.path])
    assert complete
    assert [os.path.basename(p) for _s, p in hits[gone.path]][:1] == [
        os.path.basename(copied)]
    assert len(hits[gone.path]) == len(saved["samples"])
    offered = []

    def take_first(folder, found, stopped=""):
        offered.append(found)
        return dict((saved_path, hits_of[0][1])
                    for _name, saved_path, hits_of in found if hits_of)

    window.choose_found = take_first
    assert window.find_sources(str(tmp_path)) == [os.path.basename(lost)]
    doc = window.doc
    assert doc.missing == [] and len(doc.scans) == len(saved["scans"])
    back = [s for s in doc.samples if s.path == copied]
    assert len(back) == 1 and len(back[0].scans) == len(first)
    owned = [lb for lb in doc.labels if lb.text == "on the first"]
    assert owned and owned[0].scan.sample is back[0]
    assert isinstance(doc.missing, list) and window.is_modified()
    again = session.to_state(doc)
    assert [e["path"] for e in again["samples"]] == [copied] + [
        e["path"] for e in saved["samples"][1:]]
    assert again["colour_links"] == saved["colour_links"]
    assert issubclass(type(gone), model.MissingSource)


# ----------------------------------------- a hidden curve hides its labels
def test_a_hidden_curve_hides_its_labels(stack_window):
    """A label that belongs to a curve is hidden with it: not drawn, not
    picked, not caught by a box, held by no margin - its own Show left as
    it was, so it is back with the curve. A label far above the page used
    to make the top margin jump to it."""
    import sys
    from PySide6.QtCore import QPointF
    window = stack_window
    doc, plot = window.doc, window.plot
    package = type(doc).__module__.split(".")[0]
    model = sys.modules[package + ".core.model"]
    scan = doc.scans[0]
    label = doc.add_label("far above", 0.5, 0.5, scan)
    window.refresh()
    plot.grab()
    rect = plot.plot_rect()
    x, _y = plot.artist_point(label, rect)
    plot.set_artist_point(label, x, rect.top() - 150.0, rect, clamp=False)
    window.refresh()
    plot.grab()
    assert plot.page_needs()[2] > 100.0          # it holds the top margin
    scan.visible = False
    window.refresh()
    plot.grab()
    assert label.visible and not model.drawn(label)
    assert plot.page_needs()[2] < 20.0
    assert all(obj is not label for obj, _box in plot._text_boxes)
    row = [item for item in window.outliner._items()
           if window.outliner._object(item) is label]
    assert row and "hidden with its curve" in row[0].text(1)
    whole = plot.plot_rect().adjusted(-400.0, -400.0, 400.0, 400.0)
    plot.select_in_box(QPointF(whole.topLeft()), QPointF(whole.bottomRight()))
    assert not label.selected
    scan.visible = True
    window.refresh()
    plot.grab()
    assert model.drawn(label) and plot.page_needs()[2] > 100.0


# ------------------------------------------- settings copied and pasted
@pytest.fixture
def clipboard():
    """The clipboard, emptied afterwards: under the offscreen platform Qt
    ends the process as it exits while the clipboard holds data."""
    from PySide6.QtWidgets import QApplication
    yield QApplication.clipboard()
    QApplication.clipboard().clear()


def _choose(window, title):
    """Answer the Ctrl+V menu with the entry `title` starts."""
    asked = []

    def ask(choices):
        asked.append([t for _section, t, _tip, _act in choices])
        menu, acts = window.paste_menu(choices)     # built, never shown
        assert len(acts) == len(choices) and not menu.isEmpty()
        for _section, t, _tip, act in choices:
            if t.startswith(title):
                return act()
        raise AssertionError("no {!r} in {}".format(title, asked[-1]))

    window.ask_paste = ask
    return asked


def test_ctrl_v_pastes_copied_settings_onto_the_selection(stack_window,
                                                          clipboard):
    """Ctrl+C copies the selection; Ctrl+V with objects of its kind selected
    asks what to paste - all the settings, or the colour, the sizes, the
    style, the text alone - one undo step. With nothing of the kind
    selected, copied labels are pasted as new ones, as before."""
    window = stack_window
    doc = window.doc
    a, b, c = doc.scans[:3]
    a.colour = "#123456"
    a.line_width = 2.5
    doc.select_only([a])
    assert window.copy_selected() == 1
    doc.select_only([b, c])
    asked = _choose(window, "Colour")
    window.paste()
    assert "All settings" in asked[0] and "Colour" in asked[0]
    assert b.colour == c.colour == "#123456"
    assert b.line_width != 2.5                     # the colour alone
    window.undo.undo()
    assert b.colour != "#123456" and c.colour != "#123456"
    _choose(window, "All settings")
    window.paste()
    assert b.line_width == c.line_width == 2.5 and b.colour == "#123456"

    one = doc.add_label("one", 0.3, 0.3)
    two = doc.add_label("two", 0.6, 0.6)
    one.size, one.bold = 17.0, True
    window.refresh()
    doc.select_only([one])
    window.copy_selected()
    count = len(doc.labels)
    asked = _choose(window, "All settings")
    window.paste()                     # the copy still selected: a new one
    assert asked == [] and len(doc.labels) == count + 1
    doc.select_only([two])
    asked = _choose(window, "All settings")
    window.paste()
    assert asked[0][0] == "Paste as new" and "Text" in asked[0]
    assert two.size == 17.0 and two.bold and two.text == "two"
    _choose(window, "Text")
    window.paste()
    assert two.text == "one"
    doc.select_only([])
    count = len(doc.labels)
    window.paste()                                 # nothing of the kind
    assert len(doc.labels) == count + 1


def test_a_curves_analyses_paste_onto_another_curve(stack_window,
                                                     clipboard):
    """A curve copied with its analyses: pasted onto another curve they are
    measured again there with all their settings, once - a second paste
    finds them there already - and an undo takes them off."""
    import sys
    import numpy as np
    window = stack_window
    doc, plot = window.doc, window.plot
    package = type(doc).__module__.split(".")[0]
    measure = sys.modules[package + ".core.measure"]
    a, b = doc.scans[:2]
    plot.grab()
    xs = [t for t in plot.traces if t.scan is a][0].x
    low, high = (float(v) for v in np.nanpercentile(xs, [35.0, 65.0]))
    made = None
    for entry in measure.models_for(a):
        made = measure.run(entry.name, a, low, high)
        if made is not None:
            break
    assert made is not None
    made.colour = "#aa3300"
    window.refresh()
    before = len(b.analysis_objects)
    doc.select_only([a])
    window.copy_selected()
    doc.select_only([b])
    asked = _choose(window, "Analyses")
    window.paste()
    assert "Analyses (1)" in asked[0]
    new = b.analysis_objects[before:]
    assert len(new) == 1 and new[0].model_name == made.model_name
    assert new[0].colour == "#aa3300"
    window.paste()                                 # there already
    assert len(b.analysis_objects) == before + 1
    window.undo.undo()
    assert len(b.analysis_objects) == before
    window.undo.redo()
    assert b.analysis_objects[before:] == new


# ------------------------------------------- a preset carries the page
def test_a_style_preset_carries_the_pages_colour(stack_window, tmp_path):
    """The page's colour came after the presets and was never carried: a
    preset now saves it - a colour, or the theme's - and applies it in its
    undo step. One saved before says nothing of it and leaves the page."""
    import sys
    window = stack_window
    doc = window.doc
    package = type(doc).__module__.split(".")[0]
    presets = sys.modules[package + ".core.presets"]
    window.set_background("#fff3e0")
    warm = presets.from_figure(doc, "warm")
    path = presets.save(warm, str(tmp_path))
    warm = presets.read(path)
    assert warm.background == "#fff3e0"
    window.set_background(None)
    themed = presets.read(presets.save(presets.from_figure(doc, "themed"),
                                       str(tmp_path)))
    assert themed.background is None
    window.apply_preset(warm)
    assert doc.background == "#fff3e0"
    window.undo.undo()
    assert doc.background is None
    window.apply_preset(warm)
    window.apply_preset(themed)
    assert doc.background is None                # the theme's again
    old = presets.to_state(warm)
    del old["background"]                         # saved before it was kept
    old = presets.from_state(old)
    assert old.background is presets.KEEP
    window.set_background("#ffffff")
    window.apply_preset(old)
    assert doc.background == "#ffffff"
    state = presets.to_state(warm)
    state["background"] = "red; not a colour"
    assert presets.from_state(state).background is presets.KEEP
