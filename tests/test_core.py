"""The data layer without a window: the x quantities and the wavelength,
normalisation, the stack, the analyses, simulations, the session."""

import json
import math

import numpy as np
import pytest

from pxrdpanel.core import (arrange, crystal, export, labels, measure, model,
                            readers, session, undo, units)

from conftest import CU, PEAKS, make_pattern, make_sample


# ----------------------------------------------------------- the x axis
def test_bragg_in_both_directions():
    """d = lambda / 2 sin(theta), Q = 4 pi sin(theta) / lambda = 2 pi / d,
    and back."""
    two_theta = np.array([10.0, 20.0, 90.0])
    d = units.from_two_theta(two_theta, units.D, CU)
    q = units.from_two_theta(two_theta, units.Q, CU)
    assert d[0] == pytest.approx(CU / (2 * math.sin(math.radians(5.0))))
    assert q == pytest.approx(2 * math.pi / d)
    assert units.convert(d, units.D, units.Q) == pytest.approx(q)
    assert units.to_two_theta(d, units.D, CU) == pytest.approx(two_theta)
    assert units.convert(q, units.Q, units.TWO_THETA, CU) == pytest.approx(
        two_theta)


def test_nothing_is_made_up_past_the_ends():
    """No d at 2-theta 0; no angle for a d shorter than half the
    wavelength; no d or Q without a wavelength."""
    assert np.isnan(units.from_two_theta(np.array([0.0]), units.D, CU)[0])
    assert np.isnan(units.to_two_theta(np.array([0.5]), units.D, CU)[0])
    assert units.from_two_theta(np.array([10.0]), units.D, None) is None
    assert units.convert_one(3.0, units.D, units.TWO_THETA) is None
    assert units.convert_one(3.0, units.D, units.Q) == pytest.approx(
        2 * math.pi / 3.0)


def test_a_typed_position():
    for text, value in (("12.5", 12.5), ("12,5 deg", 12.5),
                        ("10+2.5", 12.5), ("3.2 A", 3.2),
                        ("1.1 1/A", 1.1), ("7", 7.0)):
        assert units.parse_position(text) == pytest.approx(value)
    assert units.parse_position("deg") is None
    assert units.parse_position("abc") is None


def test_a_d_axis_runs_backwards_and_says_what_it_is(document):
    from pxrdpanel.core import profile
    assert not profile.x_reversed(document)
    document.x_quantity = units.D
    assert profile.x_reversed(document)
    assert document.axes["x"].caption(document) == units.X_LABEL[units.D]
    document.x_quantity = units.Q
    assert not profile.x_reversed(document)


# ------------------------------------------------------- the wavelength
def test_a_file_without_a_wavelength_is_drawn_in_two_theta_only(document):
    doc = document
    bare = doc.add_sample(make_sample("TEST-2", wavelength=None))
    assert bare.sample.wavelength is None
    assert "not stated" in bare.sample.wavelength_text()
    assert bare.missing_for(doc) is None
    doc.x_quantity = units.D
    assert bare.missing_for(doc) == units.MISSING_WAVELENGTH
    assert bare.curve(doc) == (None, None)
    assert any("NO WAVELENGTH" in line for line in export.warnings_for(doc))
    bare.sample.wavelength_override = CU
    assert bare.missing_for(doc) is None
    assert bare.sample.wavelength_source == "set by hand"


def test_the_figure_wavelength_is_a_measured_patterns():
    doc = model.Document()
    assert doc.wavelength() is None
    assert doc.reference_wavelength() == pytest.approx(
        crystal.DEFAULT_WAVELENGTH)
    doc.add_sample(make_sample("TEST-1", wavelength=None))
    doc.add_sample(make_sample("TEST-2", wavelength=0.7093))
    assert doc.wavelength() == pytest.approx(0.7093)


def test_different_wavelengths_on_one_two_theta_axis_are_admitted():
    doc = model.Document()
    doc.add_sample(make_sample("TEST-1"))
    doc.add_sample(make_sample("TEST-2", wavelength=0.7093))
    assert doc.mixed_wavelengths()
    assert any("DIFFERENT WAVELENGTHS" in line
               for line in export.warnings_for(doc))
    doc.x_quantity = units.Q
    assert not any("DIFFERENT" in line for line in export.warnings_for(doc))


# ---------------------------------------------------------- normalisation
def test_not_normalised_until_told(document):
    axis = document.axes["y"]
    assert document.norm == units.NORM_NONE
    assert axis.caption(document) == units.AXIS_LABEL
    assert np.nanmax(document.scans[0].curve(document)[1]) > 1000
    document.norm = units.NORM_RANGE
    assert "normalised" in axis.caption(document)
    _x, y = document.scans[0].curve(document)
    assert np.nanmin(y) == pytest.approx(0.0)
    assert np.nanmax(y) == pytest.approx(1.0)


def test_all_together_keeps_the_heights_in_proportion(document):
    """One scale for every pattern on show: the lowest value of any is 0,
    the highest of any 1."""
    doc = document
    doc.add_sample(make_sample("TEST-2", scale=0.4))
    doc.norm = units.NORM_GLOBAL
    low, high = doc.norm_extent()
    for scan in doc.scans:
        _x, y = scan.curve(doc)
        assert y == pytest.approx((scan.sample.y - low) / (high - low))
    tops = [np.nanmax(s.curve(doc)[1]) for s in doc.scans]
    assert tops[0] == pytest.approx(1.0) and tops[1] < 0.6


def test_a_pattern_opened_or_hidden_rescales_the_rest(document):
    doc = document
    doc.norm = units.NORM_GLOBAL
    scan = doc.scans[0]
    scan.offset = -0.5
    assert np.nanmax(scan.curve(doc)[1]) == pytest.approx(0.5)
    taller = doc.add_sample(make_sample("TEST-2", scale=3.0))
    assert np.nanmax(scan.curve(doc)[1]) < 0.0           # follows the new
    assert scan.offset == -0.5                            # offsets stay
    taller.visible = False
    assert np.nanmax(scan.curve(doc)[1]) == pytest.approx(0.5)
    taller.visible = True
    doc.x_quantity = units.D
    taller.sample.pattern.source = None       # not drawn on d: no part
    taller._cache_key = None
    assert np.nanmax(scan.curve(doc)[1]) == pytest.approx(0.5)


def test_a_peak_is_the_yardstick(document):
    doc = document
    doc.add_sample(make_sample("TEST-2", scale=0.3))
    doc.norm, doc.norm_band = units.NORM_BAND, [12.3, 12.7]
    for scan in doc.scans:
        x, y = scan.curve(doc)
        inside = (x >= 12.3) & (x <= 12.7)
        assert y[inside].max() == pytest.approx(1.0)


def test_a_peak_a_pattern_does_not_reach_says_so(document):
    document.norm, document.norm_band = units.NORM_BAND, [60.0, 61.0]
    assert document.scans[0].missing_for(document) == units.MISSING_BAND
    assert any("NORMALISATION PEAK" in line
               for line in export.warnings_for(document))


def test_a_retyped_caption_on_a_normalised_axis_is_a_warning(document):
    document.norm = units.NORM_GLOBAL
    document.axes["y"].label = "Intensity  /  counts"
    assert any("NORMALISED" in line for line in export.warnings_for(document))
    document.axes["y"].label = "Intensity (normalised)"
    assert not export.warnings_for(document)


def test_a_change_of_normalisation_keeps_the_stack_in_order(document):
    doc = document
    doc.norm = units.NORM_NONE
    for k, scale in enumerate((0.2, 2.0, 1.0)):
        doc.add_sample(make_sample("TEST-{}".format(k + 2), scale=scale))
    for k, scan in enumerate(doc.scans):
        scan.offset = -800.0 * k
    changes = doc.set_display(norm=units.NORM_RANGE)
    for obj, name, value in changes:
        setattr(obj, name, value)
    offsets = [s.offset for s in doc.scans]
    assert offsets == sorted(offsets, reverse=True)
    steps = np.diff(offsets)
    assert steps == pytest.approx([steps[0]] * len(steps))


# ----------------------------------------------------------- magnifying
def test_a_magnified_stretch_stays_joined_to_the_curve(document):
    doc = document
    scan = doc.scans[0]
    x, before = [np.array(v) for v in scan.curve(doc)]
    region = model.Region(1, 34.0, 36.5)
    region.scans, region.factor = [scan], 3.0
    doc.regions.append(region)
    _x, after = scan.curve(doc)
    outside = (x < 34.0) | (x > 36.5)
    assert np.array_equal(before[outside], after[outside])
    inside = np.flatnonzero((x >= 34.0) & (x <= 36.5))
    assert after[inside[0]] == pytest.approx(before[inside[0]])
    assert after[inside[-1]] == pytest.approx(before[inside[-1]])
    tip = inside[np.argmax(before[inside])]
    chord = np.interp(x[tip], [x[inside[0]], x[inside[-1]]],
                      [before[inside[0]], before[inside[-1]]])
    assert after[tip] - chord == pytest.approx(3.0 * (before[tip] - chord))


# ------------------------------------------------------------- analyses
def test_a_peak_position_and_its_spacing(document):
    scan = document.scans[0]
    fields = measure.compute("Peak position", scan, 16.8, 17.8)
    position = model.number(fields["Position"])
    assert position == pytest.approx(17.3, abs=0.005)
    assert model.number(fields["d"]) == pytest.approx(
        CU / (2 * math.sin(math.radians(position / 2))), rel=1e-5)


def test_the_measurements_are_on_the_stored_intensities(document):
    """Normalised or not, offset or not, the numbers are the same."""
    scan = document.scans[0]
    first = measure.compute("Peak area", scan, 16.8, 17.8)
    document.norm = units.NORM_NONE
    scan.offset = 123.0
    again = measure.compute("Peak area", scan, 16.8, 17.8)
    assert again == first
    width = model.number(measure.compute("Peak width", scan, 16.8,
                                         17.8)["FWHM"])
    # A Lorentzian of half width 0.08 is 0.16 wide at half height; above
    # the chord between the interval's ends its own tails lift, a little
    # narrower.
    assert 0.14 < width < 0.16
    assert model.number(first["Area"]) > 0


def test_a_peak_at_the_edge_of_the_interval_is_no_peak(document):
    scan = document.scans[0]
    assert measure.compute("Peak position", scan, 17.4, 17.9) is None


def test_the_position_is_not_tied_to_the_grid():
    doc = model.Document()
    x = np.arange(10.0, 14.0, 0.05)
    pattern = readers.Pattern(x, 1000.0 / (1 + ((x - 12.013) / 0.1) ** 2),
                              readers.Source(CU))
    scan = doc.add_sample(model.Sample("C:/nowhere/coarse.xy", pattern))
    found = model.number(measure.compute("Peak position", scan, 11.0,
                                         13.0)["Position"])
    assert found == pytest.approx(12.013, abs=0.01)
    assert abs(found / 0.05 - round(found / 0.05)) > 1e-3


def test_a_change_of_axis_converts_the_figure_and_measures_again(document):
    """2-theta to d: every position on the figure converted at the figure's
    wavelength, every analysis measured again on d - and one undo step puts
    it all back."""
    doc = document
    scan = doc.scans[0]
    x = scan.x_values()
    i0, i1 = np.searchsorted(x, [16.8, 17.8])
    peak = measure.run("Peak position", scan, x[i0], x[i1], span=(i0, i1))
    width = measure.run("Peak width", scan, x[i0], x[i1], span=(i0, i1))
    region = model.Region(5, 20.0, 22.0)
    doc.regions.append(region)
    marker = doc.add_label("{}", 0.5, 0.5)
    marker.vline = 17.3
    span = model.SpanArrow(6, 17.3, 21.0)
    doc.spans.append(span)
    doc.x_break = {"lo": 30.0, "hi": 40.0, "compress": 0.02, "gap": 7.0}
    d_of = lambda t: CU / (2 * math.sin(math.radians(t / 2)))  # noqa: E731
    stack = undo.UndoStack()
    stack.set_props(doc.set_x_quantity(units.D), "x axis")
    assert doc.x_quantity == units.D
    assert (region.lo, region.hi) == pytest.approx((d_of(22.0), d_of(20.0)))
    assert marker.vline == pytest.approx(d_of(17.3))
    assert span.x1 == pytest.approx(d_of(21.0))
    assert doc.x_break["lo"] == pytest.approx(d_of(40.0))
    assert peak.axis == units.D
    assert model.number(peak.fields["Position"]) == pytest.approx(
        d_of(17.3), abs=0.002)
    assert "d" not in peak.fields
    assert "A" in width.fields["FWHM"]
    stack.undo()
    assert doc.x_quantity == units.TWO_THETA
    assert (region.lo, region.hi) == (20.0, 22.0)
    assert model.number(peak.fields["Position"]) == pytest.approx(17.3,
                                                                  abs=0.005)


def test_no_change_of_axis_without_a_wavelength():
    doc = model.Document()
    doc.add_sample(make_sample("TEST-1", wavelength=None))
    with pytest.raises(ValueError):
        doc.set_x_quantity(units.D)


def test_labels_are_templates_of_the_measurement(document):
    analysis = measure.run("Peak position", document.scans[0], 16.8, 17.8)
    assert labels.render(analysis, document).text == "17.30\\degree"
    analysis.label = "(110) {} *d* = {d}"
    assert labels.render(analysis, document).text.endswith("5.12 \\AA")
    analysis.label = "{} nm"
    problems = labels.render(analysis, document).problems
    assert problems and problems[0][0] == "unit"
    analysis.label = "lit. 17.31 deg"
    assert labels.render(analysis, document).problems[0][0] == "typed"


def test_a_spacing_without_a_wavelength_is_missing_not_made_up():
    doc = model.Document()
    scan = doc.add_sample(make_sample(wavelength=None))
    analysis = measure.run("Peak position", scan, 16.8, 17.8)
    analysis.label = "{d}"
    rendered = labels.render(analysis, doc)
    assert rendered.text.startswith("?")
    assert rendered.missing()


def test_a_marker_line_says_its_position_on_the_axis(document):
    assert labels.fill_position("(111) {}", 17.3, document) == \
        "(111) 17.30\\degree"
    assert labels.fill_position("x_{}", 1.0, document) == "x_{}"
    document.x_quantity = units.D
    assert labels.fill_position("{}", 5.12, document) == "5.12 \\AA"


# ------------------------------------------------------------ simulated
def _card_sample(doc):
    """A pattern made of reflections, as a card's reader returns one."""
    pattern = readers.Pattern(source=None, kind="card", reflections=[
        (5.122, 100.0, (1, 1, 0)), (3.000, 40.0, (2, 0, 0)),
        (2.500, 10.0, (2, 1, 1))])
    sample = model.Sample("C:/nowhere/card.xml", pattern)
    from pxrdpanel.core import loader
    loader.fit_to_figure(sample, doc)
    return doc.add_sample(sample)


def test_a_card_is_drawn_at_the_figures_wavelength(document):
    scan = _card_sample(document)
    assert scan.sample.wavelength == pytest.approx(CU)
    reflections = scan.sample.reflections()
    assert reflections[0].intensity == pytest.approx(100.0)
    assert reflections[0].two_theta == pytest.approx(
        2 * math.degrees(math.asin(CU / (2 * 5.122))))
    x, y = scan.sample.x, scan.sample.y
    assert x[np.argmax(y)] == pytest.approx(reflections[0].two_theta,
                                            abs=0.01)
    # drawn at the measured patterns' range
    assert scan.sample.sim_range == pytest.approx((5.0, 50.0))


def test_a_simulation_can_be_sticks_ticks_or_lines(document):
    doc = document
    doc.norm = units.NORM_NONE
    scan = _card_sample(doc)
    scan.draw_as = crystal.DRAW_STICKS
    x, y = scan.curve(doc)
    assert np.nanmax(y) == pytest.approx(100.0)
    assert np.isnan(x[2]) and x[0] == x[1]
    scan.draw_as = crystal.DRAW_TICKS
    _x, y = scan.curve(doc)
    assert set(np.unique(y[np.isfinite(y)])) == {0.0, crystal.TICK_HEIGHT}
    scan.draw_as = crystal.DRAW_LINES
    scan.strongest = 2
    x, y = scan.curve(doc)
    assert len(x) == 2 and np.all(np.isnan(y))
    assert scan.missing_for(doc) is None
    assert scan.factor(doc.y_key()) is None


# --------------------------------------------------------------- arrange
def test_align_puts_curves_on_top_of_one_another(document):
    doc = document
    other = doc.add_sample(make_sample("TEST-2"))
    other.offset = -3.0
    changes = arrange.align_to(doc.scans[0], [other],
                               lambda s: s.kept_curve(doc))
    assert changes[0][2] == pytest.approx(0.0, abs=1e-6)


def test_a_drag_is_one_undo_step(document):
    stack = undo.UndoStack()
    scan = document.scans[0]
    stack.begin_group("drag")
    for value in (1.0, 2.0, 3.0):
        stack.set_props([(scan, "offset", value)], "move")
    stack.end_group()
    assert scan.offset == 3.0
    stack.undo()
    assert scan.offset == 0.0


# --------------------------------------------------------------- session
def test_session_round_trip(tmp_path):
    doc = model.Document()
    first = doc.add_sample(make_sample("TEST-1"))
    second = doc.add_sample(make_sample("TEST-2", wavelength=None))
    second.sample.wavelength_override = 0.7093
    second.offset = -1.5
    card = _card_sample(doc)
    card.draw_as, card.strongest = crystal.DRAW_LINES, 2
    card.sample.sim_fwhm = 0.2
    doc.norm, doc.norm_band = units.NORM_BAND, [17.0, 17.6]
    doc.x_break = {"lo": 30.0, "hi": 40.0, "compress": 0.05, "gap": 6.0}
    made = measure.run("Peak area", first, 16.8, 17.8)
    made.label = "(110) {}"
    marker_a = doc.add_label("(110)", 0.4, 0.2)
    marker_a.vline = 17.3
    marker_b = doc.add_label("(200)", 0.6, 0.2)
    marker_b.vline = 21.0
    span = model.SpanArrow(9, 17.3, 21.0, 0.35)
    span.ends = [marker_a, marker_b]
    doc.spans.append(span)
    region = model.Region(8, 34.0, 36.0)
    region.scans, region.factor, region.shade = [second], 4.0, False
    doc.regions.append(region)
    owned = doc.add_label("TEST-2", 0.1, 0.5, second)
    owned.at, owned.dx, owned.dy = ("i", 2000), 4.0, 4.0
    for obj, attr, value in doc.set_x_quantity(units.Q):
        setattr(obj, attr, value)
    samples = {s.path: s for s in doc.samples}
    path = tmp_path / "figure.pxrdpanel"
    session.save(doc, str(path))
    with open(str(path), encoding="utf-8") as fh:
        assert json.load(fh)["format"] == "pxrdpanel-session"
    back, problems = session.load(
        str(path), lambda p: model.Sample(p, samples[p].pattern))
    assert problems == []
    assert back.x_quantity == units.Q
    assert [s.sample.path for s in back.scans] == [s.sample.path
                                                   for s in doc.scans]
    assert back.scans[1].offset == -1.5
    assert back.samples[1].wavelength == pytest.approx(0.7093)
    card_back = back.scans[2]
    assert (card_back.draw_as, card_back.strongest) == (crystal.DRAW_LINES, 2)
    assert card_back.sample.sim_fwhm == 0.2
    assert card_back.sample.sim_range == pytest.approx(card.sample.sim_range)
    assert back.norm == units.NORM_BAND
    assert back.norm_band == pytest.approx(doc.norm_band)
    assert back.x_break == doc.x_break
    again = back.scans[0].analysis_objects[0]
    assert again.label == "(110) {}" and again.axis == units.Q
    assert model.number(again.fields["Area"]) == pytest.approx(
        model.number(made.fields["Area"]))
    assert back.spans[0].ends[0] is back.labels[0]
    assert back.spans[0].end_values() == pytest.approx(span.end_values())
    assert back.regions[0].scans == [back.scans[1]]
    label = [lb for lb in back.labels if lb.scan is not None][0]
    assert label.scan is back.scans[1] and label.at == ("i", 2000)


def test_a_moved_file_costs_one_line_not_the_figure(tmp_path):
    doc = model.Document()
    doc.add_sample(make_sample("TEST-1"))
    doc.add_sample(make_sample("TEST-2"))
    path = tmp_path / "figure.pxrdpanel"
    session.save(doc, str(path))
    keep = doc.samples[0]

    def read(p):
        if p != keep.path:
            raise readers.ReadError("no such file")
        return model.Sample(p, keep.pattern)

    back, problems = session.load(str(path), read)
    assert len(back.scans) == 1 and len(problems) == 1


# ---------------------------------------------------------------- export
def test_the_csv_is_what_is_drawn(document, tmp_path):
    document.scans[0].offset = -10.0
    path = export.curves_csv(document, str(tmp_path / "out.csv"))
    rows = open(path, encoding="utf-8").read().splitlines()
    data = [r for r in rows if not r.startswith("#")]
    assert data[0] == "TEST-1 2-theta/deg,TEST-1 Intensity/a.u."
    first = [float(v) for v in data[1].split(",")]
    assert first[0] == pytest.approx(5.0)
    _x, y = document.scans[0].curve(document)
    assert first[1] == pytest.approx(y[0], rel=1e-5)


def test_the_peaks_of_the_fixture_are_where_it_says():
    pattern = make_pattern()
    for centre, _height, _half in PEAKS:
        near = (pattern.x > centre - 0.2) & (pattern.x < centre + 0.2)
        assert pattern.x[near][np.argmax(pattern.y[near])] == pytest.approx(
            centre, abs=0.011)
