"""The figure's size: exact, repeatable, and the same in every export.

The case in point: two session files - the crystalline samples in one,
the glasses in the other - exported with the same size settings must drop
into Word side by side with their axes boxes the same size and in the same
place.
So these check the FILES, not only the layout code: pixel counts, the DPI
Word reads, where the axis lines land, the millimetres an SVG states.
"""

import re

import pytest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent

from pxrdpanel.core import figure, model, session, style

from conftest import make_sample

PER_CM = figure.DESIGN_DPI / 2.54


@pytest.fixture
def window(qapp):
    from pxrdpanel.ui.window import MainWindow
    win = MainWindow()
    win.resize(900, 560)
    win._sample_loaded(make_sample("TEST-1"))
    win._sample_loaded(make_sample("TEST-2", scale=0.5))
    # a y axis with numbers, as the checks of its margin need
    win.doc.axes["y"].show_numbers = True
    win.doc.axes["y"].show_ticks = True
    win.undo.clear()
    return win


def _exact(win):
    """8 x 6 cm, margins 1.5 / 0.5 / 0.5 / 1.2 cm (l, r, t, b), 600 dpi."""
    layout = win.doc.figure
    layout.mode = figure.MODE_SIZE
    layout.unit = figure.UNIT_CM
    layout.width, layout.height = 8.0, 6.0
    layout.margin_left, layout.margin_right = 1.5, 0.5
    layout.margin_top, layout.margin_bottom = 0.5, 1.2
    layout.dpi = 600
    win.refresh()
    win.plot.grab()
    return layout


def test_the_axes_box_is_where_the_margins_put_it(window):
    _exact(window)
    rect = window.plot.plot_rect()
    assert rect.left() == pytest.approx(1.5 * PER_CM)
    assert rect.top() == pytest.approx(0.5 * PER_CM)
    assert rect.width() == pytest.approx(6.0 * PER_CM)
    assert rect.height() == pytest.approx(4.3 * PER_CM)
    # whatever the numbers say: other tick labels, the same box
    window.plot.set_view_y(-1234.5, 98765.4)
    assert window.plot.plot_rect() == rect


def test_two_sessions_with_the_same_layout_have_the_same_axes_box(qapp):
    from pxrdpanel.ui.window import MainWindow
    first, second = MainWindow(), MainWindow()
    for win in (first, second):
        win.resize(900, 560)
        win._sample_loaded(make_sample())
        win.doc.axes["y"].show_numbers = True
    # different data on screen: a big offset, so the y numbers differ
    second._sample_loaded(make_sample("TEST-2"))
    second.doc.scans[-1].offset = 2500.0
    _exact(first)
    second.doc.figure = first.doc.figure.copy()
    second.refresh()
    second.plot.grab()
    assert first.plot.plot_rect() == second.plot.plot_rect()


def test_an_exact_png_is_exact(window, tmp_path):
    _exact(window)
    path = window.export_image(str(tmp_path / "exact.png"))
    image = QImage(path)
    assert (image.width(), image.height()) == (round(8 / 2.54 * 600),
                                               round(6 / 2.54 * 600))
    # the DPI Word reads, so it places the picture at 8 x 6 cm
    assert image.dotsPerMeterX() == round(600 / 0.0254)
    assert image.dotsPerMeterY() == round(600 / 0.0254)
    # The axis lines where the margins say, to a pixel at 600 dpi. A line
    # is the set of columns (rows) dark along nearly the WHOLE box - a tick,
    # a curve or a label crossing it is dark only here and there.
    left = 1.5 / 2.54 * 600
    bottom = (6.0 - 1.2) / 2.54 * 600
    top = 0.5 / 2.54 * 600
    right = (8.0 - 0.5) / 2.54 * 600
    assert abs(_line(image, left, (top, bottom), True) - left) <= 1.0
    assert abs(_line(image, bottom, (left, right), False) - bottom) <= 1.0


def _line(image, around, along, vertical):
    """The middle of the line near `around` that is dark along `along`."""
    steps = [int(along[0] + (along[1] - along[0]) * f / 40.0)
             for f in range(2, 39)]
    solid = []
    for i in range(int(around) - 8, int(around) + 9):
        dark = sum(1 for j in steps
                   if (image.pixelColor(i, j) if vertical
                       else image.pixelColor(j, i)).lightness() < 100)
        if dark >= 0.9 * len(steps):
            solid.append(i)
    assert solid, "no axis line near {:.1f}".format(around)
    return (solid[0] + solid[-1] + 1) / 2.0


def test_an_exact_svg_states_its_size_in_millimetres(window, tmp_path):
    _exact(window)
    path = window.export_image(str(tmp_path / "exact.svg"))
    with open(path, encoding="utf-8") as fh:
        head = re.search(r"<svg\b[^>]*>", fh.read()).group(0)
    assert 'width="80.0000mm"' in head and 'height="60.0000mm"' in head


def test_a_scaled_page_still_picks_what_is_under_the_pointer(window):
    _exact(window)
    plot = window.plot
    dx, dy, k = plot.page()
    assert k != pytest.approx(1.0)            # really scaled onto the pane
    trace = plot.traces[0]
    point = QPointF(float(trace.px[len(trace.px) // 2]),
                    float(trace.py[len(trace.py) // 2]))
    shown = plot.to_widget(point)
    assert plot.to_figure(shown).x() == pytest.approx(point.x())
    window.doc.select_all(False)
    for kind in (QMouseEvent.Type.MouseButtonPress,
                 QMouseEvent.Type.MouseButtonRelease):
        event = QMouseEvent(kind, shown, shown, Qt.LeftButton,
                            Qt.LeftButton, Qt.NoModifier)
        (plot.mousePressEvent if kind == QMouseEvent.Type.MouseButtonPress
         else plot.mouseReleaseEvent)(event)
    assert window.doc.selected_scans() == [trace.scan]


def test_axes_sit_on_either_side_and_hide_numbers_and_caption(window):
    plot = window.plot
    x_axis, y_axis = window.doc.axes["x"], window.doc.axes["y"]
    x_axis.side, y_axis.side = "top", "right"
    window.refresh()
    plot.grab()                                        # paints
    rect = plot.plot_rect()
    assert plot.axis_label_rect(y_axis).left() > rect.right()
    assert plot.axis_label_rect(x_axis).bottom() < rect.top()
    assert plot.axis_spine_rect("y").left() > rect.right()
    assert plot.axis_spine_rect("x").bottom() < rect.top()
    left, right, top, bottom = plot.margins()
    assert right > left and top > bottom               # the room moved too
    reach = plot.axis_reach(y_axis)
    y_axis.show_numbers = False
    assert plot.axis_reach(y_axis) < reach
    y_axis.visible = False                             # the caption
    assert plot.axis_reach(y_axis) < reach - 10
    plot.grab()


def test_an_exact_margin_that_is_too_narrow_says_so(window):
    layout = _exact(window)
    layout.margin_left, layout.margin_bottom = 3.5, 2.0   # room to spare
    # ...and on the right, where the last x number reaches past the box
    # (the offscreen platform's glyphs are wide boxes).
    layout.margin_right = 1.5
    window.refresh()
    assert window.plot.overflow() == []
    layout.margin_left = 0.2
    window.refresh()
    assert [side for side, _need, _have in window.plot.overflow()] == ["left"]


def test_the_layout_survives_a_unit_switch_and_the_session(window, tmp_path):
    layout = _exact(window)
    before = layout.size_px()
    layout.set_unit(figure.UNIT_IN)
    assert layout.size_px() == pytest.approx(before)    # nothing moved
    assert layout.width == pytest.approx(8.0 / 2.54)
    window.doc.axes["y"].side = "right"
    window.doc.axes["x"].show_numbers = False
    path = tmp_path / "exact.pxrdpanel"
    session.save(window.doc, str(path))
    samples = {s.path: s for s in window.doc.samples}
    reopened, _problems = session.load(
        str(path), lambda p: model.Sample(p, samples[p].spectrum))
    assert reopened.figure.to_state() == layout.to_state()
    assert reopened.axes["y"].side == "right"
    assert reopened.axes["x"].show_numbers is False


def test_new_figures_start_from_the_default_old_sessions_do_not(
        own_preferences, tmp_path):
    sample = make_sample()
    layout = figure.FigureLayout()
    layout.mode = figure.MODE_SIZE
    layout.width = 12.0
    style.set_figure_default(layout)
    style.save_preferences()
    style.restore_preferences({}, figure_state=None)
    style.load_preferences()
    fresh = model.Document()
    assert fresh.figure.mode == figure.MODE_SIZE
    assert fresh.figure.width == 12.0
    # a session saved before there was a figure size follows the window
    doc = model.Document()
    doc.add_sample(sample)
    path = tmp_path / "old.pxrdpanel"
    session.save(doc, str(path))
    import json
    with open(str(path), encoding="utf-8") as fh:
        state = json.load(fh)
    state.pop("figure")
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    reopened, _problems = session.load(
        str(path), lambda _p: model.Sample(sample.path, sample.spectrum))
    assert reopened.figure.mode == figure.MODE_WINDOW


def test_the_figure_dialog(window):
    from pxrdpanel.ui.dialogs import FigureSettings
    layout = window.doc.figure
    dialog = FigureSettings(window, layout, plot=window.plot)
    dialog.mode.setCurrentIndex(dialog.mode.findData(figure.MODE_SIZE))
    assert layout.mode == figure.MODE_SIZE
    dialog.fig_w.setValue(10.0)
    assert layout.width == pytest.approx(10.0)
    # typing the axes box grows the figure round it, margins kept
    dialog.axes_w.setValue(7.0)
    assert layout.width == pytest.approx(7.0 + layout.margin_left
                                         + layout.margin_right)
    dialog.unit.setCurrentIndex(dialog.unit.findData(figure.UNIT_IN))
    assert layout.unit == figure.UNIT_IN
    assert dialog.fig_w.value() == pytest.approx(layout.width, abs=0.01)
    dialog.revert()
    assert layout.mode == figure.MODE_WINDOW and layout.unit == figure.UNIT_CM
