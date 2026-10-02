"""The window, driven the way a hand drives it: through Qt's own mouse and
key pipeline (`QTest` on the window's handle) where the gesture is the
point, and through the operators where it is not.

Picking reads the LAST PAINT, so a test grabs the plot before it clicks.
"""

import math

import numpy as np
import pytest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from pxrdpanel.core import crystal, measure, model, profile, readers, \
    session, units
from pxrdpanel.ui.window import MainWindow

from conftest import CU, make_sample


def curve_point(win, scan, position):
    """Where the drawn curve of `scan` is at `position`, in pane pixels."""
    plot = win.plot
    rect = plot.plot_rect()
    trace = plot._trace_of(scan)
    k = int(np.nanargmin(abs(trace.x - position)))
    return plot.to_widget(QPointF(plot.x_to_px(trace.x[k], rect),
                                  plot.sy_to_px(scan, trace.y[k], rect)))


def drag(win, start, end, steps=12):
    """A press, a drag and a release, through the window's handle."""
    handle = win.windowHandle()
    plot = win.plot
    QTest.mousePress(handle, Qt.LeftButton, Qt.NoModifier,
                     plot.mapTo(win, start.toPoint()))
    for i in range(1, steps + 1):
        point = start + (end - start) * (i / float(steps))
        QTest.mouseMove(handle, plot.mapTo(win, point.toPoint()))
    QTest.mouseRelease(handle, Qt.LeftButton, Qt.NoModifier,
                       plot.mapTo(win, end.toPoint()))


def drag_along(win, scan, x0, x1, answer):
    """Drag along a curve from x0 to x1 (x axis), answering the list."""
    asked = []
    win.ask_analysis = lambda a, b: asked.append((a, b)) or answer
    win.ask_factor = lambda default=3.0: 5.0
    win.show()
    win.plot.grab()
    drag(win, curve_point(win, scan, x0), curve_point(win, scan, x1))
    win.plot.grab()
    return asked


def card_sample(name="CARD"):
    """A card's pattern, as its reader returns one: reflections by d."""
    pattern = readers.Pattern(source=None, kind="card", reflections=[
        (5.122, 100.0, (1, 1, 0)), (3.000, 40.0, (2, 0, 0)),
        (4.226, 60.0, (1, 1, 1)), (2.500, 10.0, (2, 1, 1))])
    return model.Sample("C:/nowhere/{}.xml".format(name), pattern)


# ------------------------------------------------------------- the stack
def test_files_open_as_a_cascade(window):
    offsets = [s.offset for s in window.doc.scans]
    assert profile.STACK_NEW
    assert offsets[0] == 0.0
    assert offsets == sorted(offsets, reverse=True)          # first on top
    assert all(step < 0 for step in np.diff(offsets))


def test_the_x_axis_is_two_theta_low_to_high(window):
    plot = window.plot
    rect = plot.plot_rect()
    assert plot.view_x() == (5.0, 50.0)              # rounded to the grid
    assert plot.x_to_px(5.0, rect) == pytest.approx(rect.left())
    assert plot.x_to_px(50.0, rect) == pytest.approx(rect.right())
    assert plot.px_to_x(rect.left() + 0.25 * rect.width(), rect) == \
        pytest.approx(16.25)
    caption = window.doc.axes["x"].caption(window.doc)
    assert "\\theta" in caption and "\\degree" in caption


def test_a_stack_has_no_y_numbers_or_ticks(window):
    y = window.doc.axes["y"]
    assert not y.show_numbers and not y.show_ticks
    assert window.plot.numbered_ticks(y) == []


def test_f_frames_x_first_then_y(window):
    plot = window.plot
    plot.set_view_x(10.0, 20.0)
    plot.set_view_y(-5.0, 5.0)
    assert plot.reset_view()
    assert plot.view_x() == (5.0, 50.0)
    assert plot.view_y() == (-5.0, 5.0)              # staged: y waits
    assert plot.reset_view()
    assert plot.view_y() != (-5.0, 5.0)


def test_a_pan_turns_round_with_the_axis(window):
    """The hand moves the picture: on 2-theta and on the reversed d axis
    the same gesture brings in opposite ends."""
    plot = window.plot
    rect = plot.plot_rect()
    before = plot.view_x()
    plot.pan_by(-rect.width() * 0.1, 0.0)
    after = plot.view_x()
    two_theta_way = np.sign(after[0] - before[0])
    assert two_theta_way != 0
    window.set_x_quantity(units.D)
    before = plot.view_x()
    plot.pan_by(-rect.width() * 0.1, 0.0)
    after = plot.view_x()
    assert np.sign(after[0] - before[0]) == -two_theta_way


def test_zoom_keeps_the_angle_under_the_pointer(window):
    plot = window.plot
    rect = plot.plot_rect()
    pos = QPointF(rect.left() + 0.3 * rect.width(), rect.center().y())
    under = plot.px_to_x(pos.x())
    plot.zoom_at(pos, 2.0, both=True)
    assert plot.px_to_x(pos.x()) == pytest.approx(under)
    assert plot.view_x()[1] - plot.view_x()[0] == pytest.approx(22.5)


# ------------------------------------------------------ d, Q and lambda
def test_d_and_q_are_one_undo_step_each(window):
    doc = window.doc
    plot = window.plot
    window.set_x_quantity(units.D)
    assert doc.x_quantity == units.D
    rect = plot.plot_rect()
    lo, hi = plot.view_x()
    # the d axis runs from large spacings (low angle) on the left
    assert plot.x_to_px(hi, rect) == pytest.approx(rect.left())
    # framed on the spacings of angles above `profile.D_FIT_FROM`
    assert hi <= CU / (2 * math.sin(math.radians(profile.D_FIT_FROM / 2))) \
        * 1.2
    assert "*d*" in doc.axes["x"].caption(doc)
    window.set_x_quantity(units.Q)
    assert plot.x_to_px(plot.view_x()[0], rect) == pytest.approx(
        rect.left())
    window.undo_step()
    assert doc.x_quantity == units.D
    window.undo_step()
    assert doc.x_quantity == units.TWO_THETA
    assert plot.view_x() == (5.0, 50.0)


def test_a_pattern_without_a_wavelength_waits_for_one(window):
    sample = make_sample("BARE", wavelength=None)
    window._sample_loaded(sample)
    scan = window.doc.scans[-1]
    window.set_x_quantity(units.D)
    assert scan.missing_for(window.doc) == units.MISSING_WAVELENGTH
    assert "cannot be drawn" in window.note.text()
    window.edit_object(scan)                 # what a double-click opens
    dialog = window._dialogs[-1]
    dialog.source.wavelength.setText("Cu Ka1")
    assert sample.wavelength_override == pytest.approx(1.540598)
    assert scan.missing_for(window.doc) is None
    dialog.accept()
    window.undo_step()
    assert sample.wavelength_override is None


def test_a_typo_in_a_wavelength_changes_nothing(window):
    scan = window.doc.scans[0]
    window.edit_object(scan)
    dialog = window._dialogs[-1]
    dialog.source.wavelength.setText("Cu Kalpha7")
    assert scan.sample.wavelength_override is None
    assert dialog.source.typed_wavelength()[1]
    dialog.close()


def test_the_wavelength_operator_gives_several_at_once(window):
    scans = window.doc.scans[:2]
    window.doc.select_only(scans)
    assert window.ask_wavelength(text="Mo Ka1") == pytest.approx(0.7093)
    assert all(s.sample.wavelength == pytest.approx(0.7093) for s in scans)
    assert window.doc.mixed_wavelengths()
    window.undo_step()
    assert all(s.sample.wavelength == pytest.approx(CU) for s in scans)


def test_a_card_is_drawn_and_can_be_lines_across_the_plot(window):
    window._sample_loaded(card_sample())
    scan = window.doc.scans[-1]
    assert scan.sample.wavelength == pytest.approx(CU)
    window.doc.select_only([scan])
    window.set_drawing(crystal.DRAW_LINES)
    assert scan.draw_as == crystal.DRAW_LINES
    for other in window.doc.scans[:-1]:
        other.visible = False
    window.refresh()
    plot = window.plot
    plot.grab()
    trace = plot._trace_of(scan)
    assert trace.px is not None and len(trace.px)
    rect = plot.plot_rect()
    first = scan.sample.reflections()[0]
    at = plot.to_widget(QPointF(plot.x_to_px(first.two_theta, rect),
                                rect.top() + 0.2 * rect.height()))
    assert plot.object_at(at) is scan
    # a line is not a curve to mark an interval on
    assert plot.drag_target(at) is None
    window.undo_step()
    assert scan.draw_as == crystal.DRAW_CURVE


# ---------------------------------------------------------------- break
def test_a_break_squeezes_its_stretch_and_is_undone(window):
    plot = window.plot
    rect = plot.plot_rect()
    width_before = plot.x_to_px(40.0, rect) - plot.x_to_px(30.0, rect)
    window.set_break({"lo": 30.0, "hi": 40.0})
    width_after = plot.x_to_px(40.0, rect) - plot.x_to_px(30.0, rect)
    assert width_after == pytest.approx(0.02 * width_before * 45.0
                                        / (45.0 - 10.0 * 0.98), rel=0.01)
    for value in (10.0, 35.0, 45.0):
        assert plot.px_to_x(plot.x_to_px(value, rect), rect) == \
            pytest.approx(value)
    ticks = [v for v, _at, _t in plot.numbered_ticks(window.doc.axes["x"])]
    assert not any(30.0 < v < 40.0 for v in ticks)
    assert 20.0 in ticks and 50.0 in ticks
    plot.grab()
    trace = plot.traces[0]
    px = plot.x_to_px(np.array([30.05, 39.95]), rect)
    assert not np.any((trace.px > px[0] + 0.01) & (trace.px < px[1] - 0.01))
    assert plot.break_seam(rect) is not None
    window.undo_step()
    assert window.doc.x_break is None


def test_the_break_comes_from_a_drag_along_a_curve(window):
    scan = window.doc.scans[0]
    drag_along(window, scan, 30.0, 40.0, measure.BREAK)
    cut = window.doc.x_break
    assert cut is not None
    assert cut["lo"] == pytest.approx(30.0, abs=0.3)
    assert cut["hi"] == pytest.approx(40.0, abs=0.3)


# ------------------------------------------------------------- analyses
def test_a_drag_along_a_curve_measures_a_peak(window):
    scan = window.doc.scans[1]
    asked = drag_along(window, scan, 16.6, 18.0, "Peak position")
    assert asked, "the list was not asked"
    [analysis] = scan.analysis_objects
    assert analysis.value() == pytest.approx(17.3, abs=0.01)
    assert analysis.span is not None
    assert analysis.summary(window.doc) == "17.30\\degree"
    assert window.plot._analysis_boxes          # it was drawn
    window.undo_step()
    assert not scan.analysis_objects


def test_an_analysis_label_stands_above_its_peak(window):
    scan = window.doc.scans[0]
    analysis = measure.run("Peak position", scan, 16.8, 17.8)
    trace = window.plot._trace_of(scan)
    assert window.plot.label_offset(analysis, trace,
                                    window.plot.plot_rect()) < 0


def test_a_peak_area_is_shaded_above_its_baseline(window):
    scan = window.doc.scans[0]
    analysis = measure.run("Peak area", scan, 20.5, 21.5)
    trace = window.plot._trace_of(scan)
    xs, top, base = window.plot.area_baseline(trace, analysis)
    middle = len(xs) // 2
    straight = base[0] + (base[-1] - base[0]) * (xs[middle] - xs[0]) / (
        xs[-1] - xs[0])
    assert base[middle] == pytest.approx(straight, rel=1e-6)
    assert np.all(top >= base - 1e-9)
    assert base[0] == pytest.approx(top[0]) and base[-1] == pytest.approx(
        top[-1])


def test_a_width_is_drawn_at_half_height(window):
    scan = window.doc.scans[0]
    analysis = measure.run("Peak width", scan, 12.0, 13.0)
    ends = window.plot.width_line(window.plot._trace_of(scan), analysis)
    assert ends is not None
    left, right = ends
    fwhm = model.number(analysis.fields["FWHM"])
    assert abs(window.plot.px_to_x(left.x()) - window.plot.px_to_x(
        right.x())) == pytest.approx(fwhm, rel=1e-3)


def test_the_analysis_settings_retype_the_interval(window):
    from pxrdpanel.ui.dialogs import AnalysisSettings
    scan = window.doc.scans[0]
    analysis = measure.run("Peak position", scan, 16.8, 17.8)
    dialog = AnalysisSettings(window, analysis)
    dialog.start.setText("20.5")
    dialog.end.setText("21.5 deg")
    dialog._typed_interval()
    assert analysis.value() == pytest.approx(21.0, abs=0.01)
    assert analysis.cursors() == pytest.approx([20.5, 21.5])
    dialog.model.setCurrentIndex(dialog.model.findData("Peak width"))
    assert analysis.model_name == "Peak width"
    dialog.close()


def test_an_analysis_follows_the_axis(window):
    scan = window.doc.scans[0]
    analysis = measure.run("Peak position", scan, 16.8, 17.8)
    window.set_x_quantity(units.D)
    assert analysis.axis == units.D
    assert analysis.summary(window.doc).endswith("\\AA")
    assert analysis.value() == pytest.approx(
        CU / (2 * math.sin(math.radians(17.3 / 2))), abs=0.002)
    window.plot.grab()
    window.undo_step()
    assert analysis.value() == pytest.approx(17.3, abs=0.01)


# ------------------------------------------------ what a stretch becomes
def test_highlight_and_magnify_from_the_drag(window):
    scan = window.doc.scans[0]
    drag_along(window, scan, 30.0, 32.0, measure.HIGHLIGHT)
    [region] = window.doc.regions
    assert region.shade and not region.magnifies
    assert region.lo == pytest.approx(30.0, abs=0.3)
    x, before = [np.array(v) for v in scan.curve(window.doc)]
    drag_along(window, scan, 34.0, 36.5, measure.MAGNIFY)
    magnifier = window.doc.regions[-1]
    assert magnifier.magnifies and magnifier.factor == 5.0
    assert magnifier.scans == [scan] and not magnifier.shade
    assert magnifier.shown_text() == "\\times5"
    _x, after = scan.curve(window.doc)
    assert not np.allclose(before, after)
    window.undo_step()
    assert len(window.doc.regions) == 1
    window.plot.grab()


def test_normalise_to_the_peak_dragged(window):
    scan = window.doc.scans[0]
    drag_along(window, scan, 16.8, 17.8, measure.NORMALISE)
    assert window.doc.norm == units.NORM_BAND
    lo, _hi = window.doc.norm_band
    assert lo == pytest.approx(16.8, abs=0.3)
    assert "normalised" in window.doc.axes["y"].caption(window.doc)


# ------------------------------------------------- markers and distances
def test_marker_lines_and_the_distance_between_them(window):
    plot = window.plot
    rect = plot.plot_rect()
    a = window.add_marker_line(text="(110)",
                               at=QPointF(plot.x_to_px(17.3, rect),
                                          rect.center().y()))
    b = window.add_marker_line(text="(200) {}",
                               at=QPointF(plot.x_to_px(21.0, rect),
                                          rect.center().y()))
    assert a.vline == pytest.approx(17.3, abs=0.1)
    assert plot.label_text(b).endswith("\\degree")
    window.doc.select_only([a, b])
    span = window.add_span_between()
    assert span.ends == [a, b]
    assert span.distance() == pytest.approx(3.7, abs=0.2)
    a.vline = 17.0
    assert span.end_values()[0] == 17.0
    assert "{:.2f}\\degree".format(b.vline - 17.0) in plot.span_text(span)
    assert "\\Delta2*\\theta*" in plot.span_text(span)
    window.doc.select_only([a])
    window.remove_selected()
    assert a not in window.doc.labels
    assert span.ends == [None, b] and span.x0 == 17.0
    window.undo_step()
    assert span.ends == [a, b]
    plot.grab()


def test_a_white_page_under_a_dark_theme(window):
    """The handling keeps the program theme's colours on a page drawn in
    the light ink. The markup's accent table once had the same module
    name as the theme's (`ACCENTS`) and replaced it: a white page under
    the default theme raised KeyError 'tilde'."""
    from pxrdpanel.ui import plot as plot_module
    assert window.doc.theme != plot_module.THEME_LIGHT
    window.set_background("#ffffff")
    assert window.plot.page_colour().name() == "#ffffff"
    window.plot.grab()


def test_a_marker_line_is_under_the_curves_its_text_over(window):
    plot = window.plot
    rect = plot.plot_rect()
    window.add_marker_line(text="(110)", at=QPointF(
        plot.x_to_px(17.3, rect), rect.center().y()))
    order = []
    for name in ("_paint_vline", "_paint_trace", "_paint_text_labels"):
        real = getattr(plot, name)
        setattr(plot, name, lambda *a, _n=name, _r=real, **k: (
            order.append(_n), _r(*a, **k))[1])
    plot.grab()
    curves = [i for i, name in enumerate(order) if name == "_paint_trace"]
    assert order.index("_paint_vline") < curves[0]
    assert order.index("_paint_text_labels") > curves[-1]


def test_a_region_and_a_span_move_along_x_with_g(window):
    window.show()
    plot = window.plot
    region = window.add_region(30.0, 32.0)
    window.doc.select_only([region])
    plot.setFocus()
    plot.start_grab()
    plot._move["typed"] = "-1"
    plot._move["axis"] = "x"
    plot._update_move(plot._move["start"])
    plot._finish_move()
    assert region.lo == pytest.approx(29.0, abs=0.05)
    assert region.hi == pytest.approx(31.0, abs=0.05)


def test_every_pattern_labelled_below_its_right_end(window):
    made = window.label_edges()
    assert len(made) == 3
    assert all(label.attached for label in made)
    plot = window.plot
    rect = plot.plot_rect()
    for label in made:
        x, _y = plot.artist_point(label, rect)
        assert x > rect.right() - 0.1 * rect.width()
        assert label.dy > 0 and label.anchor == "top right"
    assert window.label_edges() == []                  # not twice


# ------------------------------------------------------------- the y axis
def test_normalisation_is_one_undo_step(window):
    offsets = [s.offset for s in window.doc.scans]
    assert window.doc.axes["y"].caption(window.doc) == units.AXIS_LABEL
    window.set_display(norm=units.NORM_GLOBAL)
    assert "normalised" in window.doc.axes["y"].caption(window.doc)
    window.undo_step()
    assert window.doc.norm == units.NORM_NONE
    assert [s.offset for s in window.doc.scans] == pytest.approx(offsets)


def test_all_together_from_the_canvas_and_f3(window):
    """A tick on the empty plot's right-click menu turns "all together,
    0 to 1" on and off; F3 has it and "each 0 to 1" beside it."""
    assert window.ops.get("view.norm_global") is not None
    assert window.ops.get("view.norm_range") is not None
    menu = window.context_menu_for(None)
    [tick] = [a for a in menu.actions() if a.text().startswith(
        "Normalise all patterns together")]
    assert tick.isCheckable() and not tick.isChecked()
    tick.trigger()
    assert window.doc.norm == units.NORM_GLOBAL
    menu = window.context_menu_for(None)
    [tick] = [a for a in menu.actions() if a.text().startswith(
        "Normalise all patterns together")]
    assert tick.isChecked()
    tick.trigger()
    assert window.doc.norm == units.NORM_NONE
    assert window.run_op("view.norm_range")
    assert window.doc.norm == units.NORM_RANGE


# --------------------------------------------------------------- output
def test_export_and_reopen(window, tmp_path):
    scan = window.doc.scans[0]
    measure.run("Peak position", scan, 16.8, 17.8)
    window.set_break({"lo": 30.0, "hi": 40.0})
    window.set_x_quantity(units.Q)
    png = window.export_image(str(tmp_path / "figure.png"), light=True)
    assert QImage(png).width() > 100
    svg = window.export_image(str(tmp_path / "figure.svg"), light=True)
    assert "pxrdpanel-axes" in open(svg, encoding="utf-8").read()
    path = window.save_session(path=str(tmp_path / "figure.pxrdpanel"))
    samples = {s.path: s for s in window.doc.samples}
    doc, problems = session.load(
        path, lambda p: model.Sample(p, samples[p].pattern))
    assert problems == [] and doc.x_break == window.doc.x_break
    assert doc.x_quantity == units.Q
    assert len(doc.scans[0].analysis_objects) == 1


def test_the_menus_and_operators_are_consistent(window):
    assert not window.ops.duplicate_keys()
    assert window.ops.get("file.open").label == "Open patterns..."
    for name in ("view.break", "view.x_two_theta", "view.x_d", "view.x_q",
                 "scan.wavelength", "scan.draw_lines"):
        assert window.ops.get(name) is not None, name
    assert window.ops.get("view.unit_t") is None
    assert window.ops.get("sample.molar_mass") is None


def test_a_drop_of_readable_files(qapp, tmp_path):
    from pxrdpanel.core import loader
    MainWindow()
    path = tmp_path / "two.xy"
    path.write_text("5.00 10\n5.02 12\n5.04 11\n")
    assert loader.looks_readable(str(path))
    assert not loader.looks_readable(str(tmp_path / "image.png"))


def test_a_session_keeps_a_copy_of_its_files(qapp, tmp_path):
    """PXRD-Panel keeps a copy of every file inside the session
    (`profile.EMBED_SOURCES`): deleted, the file is read from the copy,
    and the copy goes on into the next save."""
    import json
    from pxrdpanel.core import loader
    assert profile.EMBED_SOURCES
    data = tmp_path / "data"
    data.mkdir()
    source = data / "pattern.xy"
    xs = np.linspace(5.0, 50.0, 2251)
    ys = 100.0 + 900.0 * np.exp(-((xs - 17.3) / 0.1) ** 2)
    source.write_text("\n".join("{:.3f} {:.3f}".format(x, y)
                                for x, y in zip(xs, ys)), encoding="utf-8")
    win = MainWindow()
    win._sample_loaded(loader.read_sample(str(source)))
    saved = tmp_path / "figure.pxrdpanel"
    session.save(win.doc, str(saved))
    state = json.loads(saved.read_text(encoding="utf-8"))
    assert state["samples"][0].get("copy")
    source.unlink()
    doc, problems = session.load(str(saved), loader.read_sample)
    assert len(doc.samples) == 1 and doc.samples[0].from_copy
    assert doc.samples[0].path == str(source) and len(doc.scans) == 1
    pattern = doc.samples[0].pattern
    expected = 100.0 + 900.0 * np.exp(-((pattern.x - 17.3) / 0.1) ** 2)
    assert np.allclose(pattern.y, expected, atol=0.01)
    assert any("copy inside the session" in p for p in problems)
    again = tmp_path / "again.pxrdpanel"
    session.save(doc, str(again))
    assert json.loads(again.read_text(encoding="utf-8"))["samples"][0][
        "copy"] == state["samples"][0]["copy"]


def test_a_region_edge_is_dragged_on_its_own(window):
    """Each of a region's two limits moves by itself on the figure: the
    edge of a selected region anywhere along it. One undo step."""
    plot = window.plot
    window.add_region(15.0, 17.0)
    region = window.doc.regions[-1]
    window.doc.select_only([region])
    window.refresh()
    plot.grab()
    rect = plot.plot_rect()
    at = QPointF(plot.x_to_px(17.0, rect), rect.center().y())
    assert plot.region_edge_at(at) == (region, "hi")
    plot._start_region_edge(region, "hi")
    plot._drag_region_edge(QPointF(plot.x_to_px(18.0, rect), at.y()))
    plot._finish_region_edge()
    assert region.lo == pytest.approx(15.0)
    assert region.hi == pytest.approx(18.0, abs=0.1)
    window.undo_step()
    assert (region.lo, region.hi) == pytest.approx((15.0, 17.0))
    plot._start_region_edge(region, "lo")
    plot._drag_region_edge(QPointF(plot.x_to_px(19.0, rect), at.y()))
    plot._finish_region_edge()
    assert region.lo < region.hi
    assert region.hi == pytest.approx(19.0, abs=0.1)


def test_a_region_says_several_lines(window):
    from pxrdpanel.ui.dialogs import RegionSettings
    window.add_region(30.0, 32.0)
    region = window.doc.regions[-1]
    dialog = RegionSettings(window, region)
    dialog.text.setPlainText("amorphous\nhump")
    assert region.text == "amorphous\nhump"
    window.plot.grab()
    plot = window.plot
    box = plot.region_text_box(region)              # two lines tall
    assert box.height() > 1.5 * plot.region_font(region).pointSizeF()
    dialog.close()


def test_a_simulations_settings_change_how_it_is_made_and_drawn(window):
    window._sample_loaded(card_sample())
    scan = window.doc.scans[-1]
    window.edit_object(scan)
    dialog = window._dialogs[-1]
    rows = dialog.source
    rows.draw_as.setCurrentIndex(rows.draw_as.findData(crystal.DRAW_TICKS))
    assert scan.draw_as == crystal.DRAW_TICKS
    rows.fwhm.setValue(0.3)
    assert scan.sample.sim_fwhm == pytest.approx(0.3)
    rows.wavelength.setText("Mo Ka1")
    assert scan.sample.wavelength == pytest.approx(0.7093)
    first = scan.sample.reflections()[0]
    assert first.two_theta == pytest.approx(
        2 * math.degrees(math.asin(0.7093 / (2 * first.d))), rel=1e-4)
    rows.wavelength.setText("")             # a simulation needs one
    assert scan.sample.wavelength == pytest.approx(0.7093)
    dialog.revert()
    assert scan.draw_as == crystal.DRAW_CURVE
    assert scan.sample.wavelength == pytest.approx(CU)
    window.edit_object(scan.sample)         # the file's own window
    window._dialogs[-1].close()


# ------------------------------------------------ one pattern scaled, said
def _swipe(plot, notches):
    """A plain wheel or two-finger swipe, `notches` detents up."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent
    at = QPointF(plot.plot_rect().center())
    event = QWheelEvent(at, at, QPoint(0, 0), QPoint(0, int(120 * notches)),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    plot.wheelEvent(event)


def test_a_swipe_on_a_selection_scales_only_it(window):
    """With patterns selected the swipe makes THEM taller, each in its
    place, by its own factor; the rest and the axis stay. Leaving x1 brings
    an "x1.44" label above the curve's left end; one undo step takes it
    all back."""
    from pxrdpanel.core import profile
    doc, plot = window.doc, window.plot
    a, b, c = doc.scans
    plot.grab()
    view = plot.main_view()
    others = [list(s.curve(doc)[1]) for s in (a, c)]
    base = profile.baseline(b.curve(doc)[1])
    top = np.nanmax(b.curve(doc)[1])
    doc.select_only([b])
    _swipe(plot, 2)
    plot.commit_view()
    assert b.multiplier == pytest.approx(1.44)
    _x, y = b.curve(doc)
    assert profile.baseline(y) == pytest.approx(base, rel=1e-6)
    assert np.nanmax(y) - base == pytest.approx(1.44 * (top - base))
    assert [list(s.curve(doc)[1]) for s in (a, c)] == others
    assert plot.main_view() == pytest.approx(view)
    label = doc.multiplier_label_of(b)
    assert label is not None and label.scan is b and label.attached
    assert plot.label_text(label) == "\\times1.44"
    rect = plot.plot_rect()
    plot.grab()
    x, _y = plot.artist_point(label, rect)
    assert x < rect.left() + 0.1 * rect.width() and label.dy < 0
    assert "SCALED" in " ".join(export_notes(doc))
    window.undo_step()
    assert b.multiplier == 1.0 and doc.multiplier_label_of(b) is None
    assert np.nanmax(b.curve(doc)[1]) == pytest.approx(top)


def export_notes(doc):
    from pxrdpanel.core import export
    return export.notes_for(doc)


def test_nothing_selected_scales_every_curve_as_before(window):
    doc, plot = window.doc, window.plot
    doc.select_only([])
    plot.grab()
    _swipe(plot, 2)
    plot.commit_view()
    assert all(s.multiplier == 1.0 for s in doc.scans)
    assert not [lb for lb in doc.labels if lb.shows == "multiplier"]


def test_a_label_taken_away_is_not_put_back(window):
    doc, plot = window.doc, window.plot
    scan = doc.scans[0]
    doc.select_only([scan])
    plot.grab()
    _swipe(plot, 1)
    plot.commit_view()
    label = doc.multiplier_label_of(scan)
    doc.remove_label(label)
    doc.select_only([scan])
    _swipe(plot, 1)
    plot.commit_view()
    assert doc.multiplier_label_of(scan) is None
    assert window.run_op("label.multipliers")           # F3 brings it back
    assert doc.multiplier_label_of(scan) is not None
    # back at x1 the label says nothing and is not drawn
    window.set_multiplier(1.0, [scan])
    assert window.plot.label_text(doc.multiplier_label_of(scan)) == ""


def test_a_typed_scale_for_several_and_the_settings(window):
    doc = window.doc
    a, b, c = doc.scans
    doc.select_only([b, c])
    window.ask_multiplier(text="x15")
    assert (b.multiplier, c.multiplier) == (15.0, 15.0)
    assert doc.multiplier_label_of(b) and doc.multiplier_label_of(c)
    window.undo_step()
    assert (b.multiplier, c.multiplier) == (1.0, 1.0)
    assert doc.multiplier_label_of(b) is None
    window.edit_object(a)
    dialog = window._dialogs[-1]
    before = a.offset
    dialog.multiplier.setValue(0.2)
    assert a.multiplier == pytest.approx(0.2)
    assert a.offset != before                     # kept in its place
    assert dialog.offset.value() == pytest.approx(a.offset)
    assert doc.multiplier_label_of(a) is not None
    dialog.accept()
    window.undo_step()
    assert a.multiplier == 1.0 and a.offset == before
    assert doc.multiplier_label_of(a) is None


def test_the_scale_survives_the_session_and_never_the_measurement(
        window, tmp_path):
    doc = window.doc
    scan = doc.scans[0]
    analysis = measure.run("Peak position", scan, 16.8, 17.8)
    first = dict(analysis.fields)
    doc.select_only([scan])
    window.ask_multiplier(text="15")
    assert measure.compute("Peak position", scan, 16.8, 17.8) == first
    doc.norm = units.NORM_GLOBAL
    extent = doc.norm_extent()
    window.set_multiplier(3.0, [doc.scans[1]])
    assert doc.norm_extent() == extent           # the scale takes no part
    path = window.save_session(path=str(tmp_path / "scaled.pxrdpanel"))
    samples = {s.path: s for s in doc.samples}
    back, problems = session.load(
        path, lambda p: model.Sample(p, samples[p].pattern))
    assert problems == []
    assert back.scans[0].multiplier == 15.0
    label = back.multiplier_label_of(back.scans[0])
    assert label is not None and label.shows == "multiplier"
