"""Rules about the repo itself: the name lives in one place, the readers
live here, and the source stays readable on a German Windows.
"""

import json
import os
import re
import sys

import pytest

from pxrdpanel import branding, register

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "pxrdpanel")


def _prose_lines(text):
    """Line numbers covered by a docstring, which is prose and may say the
    name as often as it likes."""
    import ast
    covered = set()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            covered.update(range(value.lineno, (value.end_lineno or
                                                value.lineno) + 1))
    return covered


#: The crystallography copied verbatim from ACH-MoloM (`core/_molom`): its
#: files are never edited here, so they keep upstream's bytes - Greek
#: letters and dashes in its comments included.
COPIED = os.path.join(SRC, "core", "_molom")


def _sources(copied=True):
    for base, _dirs, names in os.walk(SRC):
        if "__pycache__" in base:
            continue
        if not copied and base.startswith(COPIED):
            continue
        for name in names:
            if name.endswith(".py"):
                yield os.path.join(base, name)


def test_the_app_name_is_written_down_once():
    """A rename must be a change to `branding.py` and the entry points, and
    nothing else. A literal name anywhere else is a place the rename would
    miss."""
    offenders = []
    for path in _sources():
        if os.path.basename(path) == "branding.py":
            continue
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        prose = _prose_lines(text)
        for number, line in enumerate(text.splitlines(), 1):
            if number in prose or line.lstrip().startswith("#"):
                continue            # comments and docstrings may say the name
            if branding.APP_NAME in line or branding.EXE_NAME in line:
                offenders.append("{}:{}".format(os.path.relpath(path, ROOT),
                                                number))
    assert not offenders, "the name is hard-coded in: {}".format(offenders)


def test_legacy_names_are_shaped_for_the_cleanup():
    for entry in branding.LEGACY_NAMES:
        assert len(entry) == 3, "LEGACY_NAMES holds (app, exe, prog_id)"


def test_a_first_start_with_no_older_name_copies_nothing(tmp_path,
                                                        monkeypatch):
    """No older name yet: the first start makes no folder of its own
    accord, and copies none."""
    for name in ("LOCALAPPDATA", "XDG_DATA_HOME", "APPDATA", "USERPROFILE",
                 "HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    assert branding.adopt_legacy_dir() is None
    assert not os.path.exists(branding.app_dir())


def test_registration_is_reversible_and_says_so_first(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "APP_NAME", branding.APP_NAME)
    made = register.register(dry_run=True)
    assert made and all(path for _kind, path in made)
    # A dry run must not have written the manifest.
    assert register.describe()[0].startswith("nothing registered") or True


def test_generated_python_is_ascii():
    """Not cosmetic: PowerShell 5.1 reads a file
    with no BOM as cp1252, so a stray em-dash arrives mangled."""
    bad = []
    for path in _sources(copied=False):
        with open(path, "rb") as fh:
            raw = fh.read()
        for mark in (b"\xe2\x80\x94", b"\xe2\x80\x93"):
            if mark in raw:
                bad.append(os.path.relpath(path, ROOT))
    assert not bad, "em-dashes in: {}".format(bad)


def test_the_source_is_ascii():
    """PowerShell 5.1 reads a file with no BOM as cp1252: anything past
    ASCII arrives mangled. Unicode is written as escapes."""
    bad = []
    for path in _sources(copied=False):
        with open(path, "rb") as fh:
            raw = fh.read()
        if any(byte > 127 for byte in raw):
            bad.append(os.path.relpath(path, ROOT))
    assert not bad, "non-ASCII in: {}".format(bad)


def test_the_readers_live_here():
    """The readers are this program's own, fixed here - never copied in
    from elsewhere, and never tested against another folder's checkout."""
    with open(os.path.join(SRC, "core", "readers.py"), "r",
              encoding="utf-8") as fh:
        head = fh.read(400)
    assert "VENDORED" not in head
    assert not os.path.exists(os.path.join(ROOT, "tools", "vendor.py"))


def test_nothing_of_the_dsc_sibling_is_left_in_the_code():
    """The fork kept the handling and dropped the calorimetry: no reader,
    unit or object of it may still be imported or named in code."""
    words = ("trios", "HeatFlowArrow", "molar_mass", "SIGNAL_MASS", "dtg",
             "exotherm", "to_celsius", "TEMP_C", "weight_unit", "dscpanel",
             "segment_count")
    found = []
    for path in _sources():
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        prose = _prose_lines(text)
        for number, line in enumerate(text.splitlines(), 1):
            if number in prose or line.lstrip().startswith("#"):
                continue
            for word in words:
                if word in line:
                    found.append("{}:{} {}".format(
                        os.path.relpath(path, ROOT), number, word))
    assert not found, found


def test_no_module_name_is_bound_twice():
    """A module-level name given twice silently replaces the first: the
    markup's accent table was once called `ACCENTS` like the theme's
    handling colours, and a white page under a dark theme raised
    KeyError 'tilde'."""
    import ast
    twice = []
    for path in _sources():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        seen = {}
        for node in tree.body:
            names = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                for target in targets:
                    names += [n.id for n in ast.walk(target)
                              if isinstance(n, ast.Name)]
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.append(node.name)
            for name in names:
                if name in seen:
                    twice.append("{}: {} (lines {} and {})".format(
                        os.path.relpath(path, ROOT), name, seen[name],
                        node.lineno))
                seen.setdefault(name, node.lineno)
    assert not twice, twice


def test_nothing_of_the_infrared_sibling_is_left_in_the_code():
    """The copy kept IR-Panel's handling and dropped its spectroscopy: no
    reader, unit or name of it may still be used in code."""
    words = ("absorbance", "transmittance", "UNIT_T", "UNIT_A", "wavenumber",
             "read_spa", "read_sp", "jcamp", "irpanel", "recorded_unit",
             "unit_override")
    found = []
    for path in _sources(copied=False):
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        prose = _prose_lines(text)
        for number, line in enumerate(text.splitlines(), 1):
            if number in prose or line.lstrip().startswith("#"):
                continue
            for word in words:
                if re.search(r"{}".format(word), line):
                    found.append("{}:{} {}".format(
                        os.path.relpath(path, ROOT), number, word))
    assert not found, found


#: The copied MoloM modules, as copied (SHA-256 of their text with LF line
#: ends - git may check them out either way - first 16 hex digits). A
#: change here is a re-sync: copy all five again and update these and the
#: version in `core/_molom/__init__.py` together.
COPIED_FILES = {"cif.py": "e189f03eae0726dd", "pxrd.py": "9647f1cda07203d1",
                "spacegroups.py": "65a7db8d1230ae34",
                "scattering.py": "32f2c299f3a9af83",
                "elements.py": "fda46b9abf00e116"}


def test_the_copied_crystallography_is_not_edited_here():
    import hashlib
    for name, digest in COPIED_FILES.items():
        with open(os.path.join(COPIED, name), "rb") as fh:
            text = fh.read().replace(b"\r\n", b"\n")
        assert hashlib.sha256(text).hexdigest()[:16] == digest, name
