"""The house style: where a size comes from, and that it survives a restart.

UI-free, like `core/style.py` itself. Every test runs against its own
preferences file (the `own_preferences` fixture in conftest), so nothing here
can change how the person running the suite has their figures set up.
"""

import json

import pytest

from pxrdpanel.core import measure, model, session, style

from conftest import make_sample


def test_a_value_comes_from_the_object_then_the_figure_then_the_default():
    doc = model.Document()
    axis = doc.axes["x"]
    assert axis.tick_size is None                       # nothing chosen
    assert style.value(doc, axis, "tick_size") == style.builtin("tick_size")
    style.set_preference("tick_size", 11.0)
    assert style.value(doc, axis, "tick_size") == 11.0  # the user's default
    doc.style.tick_size = 7.0
    assert style.value(doc, axis, "tick_size") == 7.0   # this figure
    axis.tick_size = 14.0
    assert style.value(doc, axis, "tick_size") == 14.0  # this axis
    axis.tick_size = None
    doc.style.tick_size = None
    assert style.value(doc, axis, "tick_size") == 11.0


def test_every_styled_attribute_starts_unchosen():
    """None is "ask the house style". A number here would pin the object and
    the defaults would never reach it."""
    doc = model.Document()
    for (kind, attr), key in style.FIELDS.items():
        assert key in style.BY_KEY
    assert doc.legend.size is None
    assert doc.axes["y"].label_size is None
    assert doc.add_label("note").size is None


def test_the_defaults_survive_a_restart(own_preferences):
    style.set_preference("analysis_size", 7.5)
    style.set_preference("analysis_flush", style.FLUSH_RIGHT)
    assert style.save_preferences() == own_preferences
    style.restore_preferences({})
    assert style.preference("analysis_size") == style.builtin("analysis_size")
    style.load_preferences()
    assert style.preference("analysis_size") == 7.5
    assert style.preference("analysis_flush") == style.FLUSH_RIGHT


def test_a_default_set_back_to_the_builtin_is_forgotten():
    style.set_preference("legend_size", 12.0)
    style.set_preference("legend_size", 9.0)
    assert "legend_size" not in style.preferences()


def test_a_damaged_preferences_file_is_the_builtin_style(own_preferences):
    with open(own_preferences, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert style.load_preferences() == {}
    with open(own_preferences, "w", encoding="utf-8") as fh:
        json.dump({"style": {"tick_size": "big",
                             "analysis_flush": "sideways",
                             "line_width": 99, "no_such_thing": 3}}, fh)
    loaded = style.load_preferences()
    assert "tick_size" not in loaded
    assert "analysis_flush" not in loaded
    assert loaded["line_width"] == 8.0                  # clamped, not refused


def test_auto_flush_centres_every_ir_label():
    style.set_preference("analysis_flush", style.FLUSH_AUTO)
    doc = model.Document()
    for name in ("Peak position", "Peak area", "Peak width"):
        analysis = model.Analysis(1, None, name, {})
        flush = style.value(doc, analysis, "flush")
        assert flush == style.FLUSH_AUTO
        assert style.flush_for(analysis, flush) == style.FLUSH_CENTER


def test_a_marker_line_takes_its_own_size():
    doc = model.Document()
    label = doc.add_label("plain")
    marker = doc.add_label("(110)")
    marker.vline = 17.3
    assert style.value(doc, label, "size") == style.builtin("label_size")
    assert style.value(doc, marker, "size") == \
        style.builtin("band_marker_size")


def test_a_number_format_follows_what_the_number_is():
    doc = model.Document()
    position = model.Analysis(1, None, "Peak position", {})
    area = model.Analysis(2, None, "Peak area", {})
    assert style.value(doc, position, "number_format") == \
        style.builtin("position_format")
    assert style.value(doc, area, "number_format") == \
        style.builtin("value_format")


# ------------------------------------------------------------ in a session
def _reopen(doc, path, sample):
    session.save(doc, str(path))
    reopened, problems = session.load(
        str(path), lambda _p: model.Sample(sample.path, sample.spectrum))
    return reopened, problems


def test_the_figure_style_and_unchosen_sizes_round_trip(tmp_path):
    sample = make_sample()
    doc = model.Document()
    doc.add_sample(sample)
    doc.style.analysis_size = 7.0
    doc.style.analysis_flush = style.FLUSH_RIGHT
    doc.axes["y"].tick_size = 12.0
    reopened, problems = _reopen(doc, tmp_path / "styled.pxrdpanel", sample)
    assert problems == []
    assert reopened.style.analysis_size == 7.0
    assert reopened.style.analysis_flush == style.FLUSH_RIGHT
    assert reopened.style.legend_size is None
    assert reopened.axes["y"].tick_size == 12.0
    # not chosen stays not chosen, so the next defaults still reach it
    assert reopened.axes["x"].tick_size is None
    assert reopened.legend.size is None
    assert reopened.scans[0].line_width is None


def test_an_analysis_made_here_is_still_there_after_reopening(tmp_path):
    """It exists nowhere but in the panel: the session keeps its model and
    interval and measures it again."""
    sample = make_sample()
    doc = model.Document()
    scan = doc.add_sample(sample)
    made = measure.run("Peak area", scan, 16.8, 17.8)
    made.flush = style.FLUSH_LEFT
    made.label_size = 11.0
    made.label = "(110) {}"
    reopened, problems = _reopen(doc, tmp_path / "measured.pxrdpanel", sample)
    assert problems == []
    [back] = reopened.scans[0].analysis_objects
    assert model.number(back.fields["Area"]) == pytest.approx(
        model.number(made.fields["Area"]))
    assert back.cursors() == pytest.approx(made.cursors())
    assert back.flush == style.FLUSH_LEFT
    assert back.label_size == 11.0
    assert back.label == "(110) {}"
    assert back.visible
