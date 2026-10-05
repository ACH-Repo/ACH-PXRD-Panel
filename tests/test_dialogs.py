"""Every window and every right-click menu opens, on every kind of object.

Built and shown, never `exec`ed (a modal loop hangs a test): what this
catches is a window that raises while being built - an attribute of the
DSC sibling it came from, a field named like a QWidget method.
"""

import pytest

from PySide6.QtCore import QPointF

from pxrdpanel.core import measure, model
from pxrdpanel.ui.dialogs import (AxisSettings, BreakDialog, CaptionSettings,
                                NumberSettings, RegionSettings, SpanSettings)


def _everything(window):
    """One object of every kind on the figure."""
    doc = window.doc
    plot = window.plot
    rect = plot.plot_rect()
    scan = doc.scans[0]
    measure.run("Peak position", scan, 16.8, 17.8)
    measure.run("Peak area", scan, 20.5, 21.5)
    measure.run("Peak width", scan, 12.0, 13.0)
    marker = window.add_marker_line(text="\\nu(C=O) {}", at=QPointF(
        plot.x_to_px(17.3, rect), rect.center().y()))
    other = window.add_marker_line(text="\\nu_{s}", at=QPointF(
        plot.x_to_px(21.0, rect), rect.center().y()))
    doc.select_only([marker, other])
    window.add_span_between()
    window.add_region(30.0, 32.0)
    window.add_region(34.0, 36.5, scans=[scan], factor=3.0, shade=False)
    window.add_label("free text")
    window.label_edges()
    note = model.TextLabel(doc._next_id(), "a note", 0.5, 0.3)
    note.leader = [17.3, 0.5]
    doc.labels.append(note)
    window.toggle_offset_markers()
    window.toggle_legend()
    window.refresh()
    plot.grab()
    return doc


def test_every_settings_window_opens(window):
    doc = _everything(window)
    kinds = set()
    objects = [o for o in doc.objects() if not isinstance(o, model.Axis)]
    objects += list(doc.samples)
    for obj in objects:
        window.edit_object(obj)
        kinds.add(type(obj).__name__)
    for axis in doc.axes.values():
        for part in ("spine", "numbers", "caption"):
            window.edit_object(axis, part=part)
    for dialog in window.popups():
        dialog.close()
    assert {"Scan", "Analysis", "TextLabel", "Region", "SpanArrow",
            "Legend", "OffsetMarker", "Sample"} <= kinds


def test_every_right_click_menu_is_built(window):
    doc = _everything(window)
    for obj in [None] + doc.objects() + list(doc.samples):
        menu = window.context_menu_for(obj)
        assert menu is not None


def test_the_group_windows_mirror_what_changes(window):
    doc = _everything(window)
    regions = list(doc.regions)
    for region in regions:
        region.selected = True
    dialog = RegionSettings(window, regions[0])
    dialog.set_group(regions[1:])
    dialog.opacity.setValue(0.4)
    assert all(r.opacity == pytest.approx(0.4) for r in regions)
    assert regions[1].lo != regions[0].lo            # its own, not mirrored
    dialog.close()


def test_the_span_window_unties_its_ends(window):
    doc = _everything(window)
    span = doc.spans[0]
    dialog = SpanSettings(window, span)
    assert not dialog.first.isEnabled()
    dialog.untie.setChecked(False)
    assert span.ends == [None, None]
    dialog.first.setValue(17.5)
    assert span.x0 == pytest.approx(17.5)
    dialog.revert()
    assert span.ends[0] is not None


def test_the_axis_windows_and_the_break_dialog(window):
    doc = window.doc
    for kind in (AxisSettings, NumberSettings, CaptionSettings):
        dialog = kind(window, doc.axes["y"], doc)
        dialog.close()
    spine = AxisSettings(window, doc.axes["y"], doc)
    spine.ticks.setChecked(True)
    assert doc.axes["y"].show_ticks
    spine.revert()
    assert not doc.axes["y"].show_ticks
    cut = BreakDialog(window, 40.0, 30.0)
    assert cut.values()["lo"] == 30.0 and cut.values()["hi"] == 40.0


def test_the_house_style_page_and_the_operator_search(window):
    from pxrdpanel.ui.palette import MeasurePalette, OperatorPalette
    from pxrdpanel.ui.settings import SettingsDialog
    settings = SettingsDialog(window)
    settings.close()
    search = OperatorPalette(window.ops, window, window)
    search.edit.setText("break")
    assert search.list.topLevelItemCount() >= 1
    choose = MeasurePalette(measure.models_for(), window)
    choose.edit.setText("magnify")
    assert choose.list.topLevelItemCount() == 1


def test_no_dialog_field_is_named_like_a_qwidget_method():
    """`self.height = NumberBox()` hides `QWidget.height()`, and the window
    raises the first time it measures itself (it happened here)."""
    import os
    import re
    from PySide6.QtWidgets import QDialog
    taken = set(dir(QDialog))
    folder = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "pxrdpanel", "ui")
    clashes = []
    for name in ("dialogs.py", "settings.py", "colour.py"):
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                for field in re.findall(r"self\.(\w+)\s*=[^=]", line):
                    if field in taken:
                        clashes.append("{}:{} {}".format(name, number, field))
    assert not clashes, clashes


def test_a_regions_curves_show_only_while_it_magnifies(window):
    """A highlight has no curves to choose; set a factor and the list is
    there. Its text comes first, Show and Layer last (2026-10-05)."""
    from test_family import _rows
    region = window.add_region(30.0, 32.0)
    window.edit_object(region)
    dialog = window._dialogs[-1]
    rows = _rows(dialog)
    assert rows[0] == "Text" and "Patterns" not in rows
    assert rows[-2:] == ["Show", "Layer"]
    dialog.factor.setValue(3.0)
    assert "Patterns" in _rows(dialog)
    dialog.close()
