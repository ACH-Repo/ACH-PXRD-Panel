"""The readers, against real files, and against one another.

Every real file is named by a hash of its file name (`conftest.hashed_name`)
and found through `tests/local_testdata.txt`; without them these skip.

The same measurements exist in more than one format - a Bruker RAW1.01
beside the Riet7 `.dat` exported from it, a RAW4.00 beside the `.brml` it
was converted from, an ICDD card beside the CIF of the same phase - so
each reader is checked against another: a mistake in one would have to be
made identically in an unrelated format to pass.
"""

import math

import numpy as np
import pytest

from pxrdpanel.core import crystal, loader, model, readers, units

from conftest import real

#: A RAW1.01 and the Riet7 `.dat` exported from it (two such pairs).
RAW1, RAW1_DAT = "sha:f9d23e6ab392", "sha:a5a9e044f2ea"
RAW1_B, RAW1_B_DAT = "sha:c5aa734acdc7", "sha:bcfdadd0b656"
#: A RAW4.00 and the `.brml` it was converted from (two such pairs).
RAW4, BRML = "sha:fceeaad0cc53", "sha:2947d39f0897"
RAW4_B, BRML_B = "sha:1a03cfc5d8c8", "sha:a7aa86f574b8"
#: A RAW1.01 written by a structure database's export: no tube in it.
RAW1_EXPORT = "sha:8de8f1c4695e"
#: A column file with no wavelength, measured from near 0 degrees.
XY_BARE = "sha:cbd72ac20987"
#: A refinement program's observed pattern, as columns.
XY_FIT = "sha:f08cb5ebe99d"
#: An ICDD card (observed), and a card and the CIF of the same phase.
CARD = "sha:a0b46fda9bc9"
CARD_PAIR, CIF_PAIR = "sha:ce1cdc6d9210", "sha:c1408b51bfd5"
#: A CIF of an organic acid.
CIF = "sha:a2e4f3fcde23"


@pytest.mark.parametrize("raw, dat", [(RAW1, RAW1_DAT),
                                      (RAW1_B, RAW1_B_DAT)])
def test_a_raw1_equals_its_riet7_export(raw, dat):
    a = readers.read(real(raw))
    b = readers.read(real(dat))
    assert (a.kind, b.kind) == ("raw1", "riet7")
    assert len(a.x) == len(b.x)
    # The .dat header rounds its start and step to three decimals; its
    # intensities are exact.
    assert np.max(np.abs(a.x - b.x)) < 1e-3
    assert np.array_equal(a.y, b.y)
    for pattern in (a, b):
        assert pattern.source.alpha1 == pytest.approx(1.5406)
        assert pattern.source.alpha2 == pytest.approx(1.54439)
        assert pattern.source.ratio == pytest.approx(0.5)
    assert a.source.anode == "Cu"


@pytest.mark.parametrize("raw, brml", [(RAW4, BRML), (RAW4_B, BRML_B)])
def test_a_raw4_equals_the_brml_it_was_made_from(raw, brml):
    a = readers.read(real(raw))
    b = readers.read(real(brml))
    assert (a.kind, b.kind) == ("raw4", "brml")
    assert len(a.x) == len(b.x)
    # The .brml writes its angles to four decimals.
    assert np.max(np.abs(a.x - b.x)) < 1e-4
    assert np.array_equal(a.y, b.y)
    for pattern in (a, b):
        assert pattern.source.alpha1 == pytest.approx(1.5406)
        assert pattern.source.alpha2 == pytest.approx(1.54439)
        assert pattern.source.ratio == pytest.approx(0.5)
    assert a.head["title"] == b.head["title"]


def test_the_raw1_start_is_two_theta_not_theta():
    """The range header's theta (+8) is half the 2-theta (+16): read from
    the wrong one, every peak sits at half its angle."""
    a = readers.read(real(RAW1))
    b = readers.read(real(RAW1_DAT))
    assert a.x[0] == pytest.approx(b.x[0], abs=1e-3)
    assert a.x[0] > 2.0


def test_an_export_without_a_tube_has_no_wavelength_made_up():
    pattern = readers.read(real(RAW1_EXPORT))
    assert pattern.kind == "raw1"
    assert pattern.source is None or pattern.source.alpha2 is None
    # what it states, it states; nothing is added
    if pattern.source is not None:
        assert pattern.source.anode == ""


def test_a_column_file_states_no_wavelength():
    sample = loader.read_sample(real(XY_BARE))
    assert sample.pattern.kind == "text"
    assert sample.wavelength is None
    assert "does not state its wavelength" in sample.note
    doc = model.Document()
    scan = doc.add_sample(sample)
    doc.x_quantity = units.D
    assert scan.missing_for(doc) == units.MISSING_WAVELENGTH


def test_a_refinement_programs_columns_read():
    pattern = readers.read(real(XY_FIT))
    assert pattern.kind == "text"
    assert np.all(np.diff(pattern.x) > 0)
    assert len(pattern.x) > 1000


def test_a_card_is_read_by_its_spacings():
    pattern = readers.read(real(CARD))
    assert pattern.kind == "card" and pattern.simulated
    d, intensity, hkl = pattern.reflections[0]
    assert d > 1.0 and intensity > 0 and len(hkl) == 3
    # each reflection's d is the card's angle at the card's wavelength
    card_lambda = float(pattern.head["card_lambda"])
    x, _y, found, _note = crystal.simulate(pattern, card_lambda, (5, 50))
    assert found[0].intensity == pytest.approx(100.0)
    assert found[0].two_theta == pytest.approx(
        2 * math.degrees(math.asin(card_lambda / (2 * found[0].d))))


def test_a_cif_and_the_card_of_the_same_phase_agree():
    """A calculated card and the CIF it was calculated from: the CIF's
    simulation puts every one of the card's ten strongest reflections at
    the same spacing, and in the same order of strength."""
    card = readers.read(real(CARD_PAIR))
    cif = readers.read(real(CIF_PAIR))
    wavelength = float(card.head["card_lambda"])
    _x, _y, from_card, _n = crystal.simulate(card, wavelength, (5, 50))
    _x, _y, from_cif, note = crystal.simulate(cif, wavelength, (5, 50))
    assert "displacement" in note           # said, not hidden
    for reflection in from_card[:10]:
        nearest = min(from_cif, key=lambda r: abs(r.d - reflection.d))
        assert nearest.d == pytest.approx(reflection.d, abs=5e-4)
        assert nearest.intensity == pytest.approx(reflection.intensity,
                                                  abs=15.0)
    assert from_cif[0].hkl == from_card[0].hkl


def test_a_cif_is_simulated_at_the_figures_wavelength():
    doc = model.Document()
    doc.add_sample(loader.read_sample(real(RAW4)))
    sample = loader.fit_to_figure(loader.read_sample(real(CIF)), doc)
    assert sample.wavelength == pytest.approx(1.5406)
    assert sample.sim_range[0] == pytest.approx(4.0001, abs=1e-3)
    assert len(sample.reflections()) > 10
    assert np.nanmax(sample.y) == pytest.approx(100.0)
    assert "simulated at" in sample.wavelength_text()


def test_every_reader_turns_out_ascending_two_theta():
    for name in (RAW1, RAW1_DAT, RAW4, BRML, XY_BARE):
        pattern = readers.read(real(name))
        assert np.all(np.diff(pattern.x) > 0)


def test_a_file_that_is_not_a_pattern_says_why(tmp_path):
    bad = tmp_path / "notes.xy"
    bad.write_text("hello\nworld\n", encoding="utf-8")
    with pytest.raises(readers.ReadError):
        readers.read(str(bad))
    raw = tmp_path / "odd.raw"
    raw.write_bytes(b"RAW2.00" + b"\0" * 2000)
    with pytest.raises(readers.ReadError) as found:
        readers.read(str(raw))
    assert "RAW1.01 and RAW4.00" in str(found.value)


def test_columns_with_decimal_commas_and_semicolons(tmp_path):
    path = tmp_path / "comma.csv"
    path.write_text("2Theta;Intensity\n5,00;10,5\n5,02;11,0\n5,04;12,5\n",
                    encoding="utf-8")
    pattern = readers.read(str(path))
    assert pattern.x == pytest.approx([5.0, 5.02, 5.04])
    assert pattern.y == pytest.approx([10.5, 11.0, 12.5])
    assert pattern.source is None
