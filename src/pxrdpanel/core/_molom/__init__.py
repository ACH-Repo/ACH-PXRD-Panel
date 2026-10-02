"""A verbatim copy of ACH-MoloM's crystallography core. Do not edit these files.

Source:   https://github.com/ACH-Repo/ACH-MoloM  (molom/core/)
Version:  molom 0.6.0
Commit:   8422d0b97d1adaf31fd50bc0bf9561b955254d3f
Licence:  MIT, same author and same terms as this package.

The same five modules, byte for byte, that ACH-Diffraction-Analysis-Suite
carries in `achdiff/core/_molom/` (checked identical when copied), so a
pattern simulated here is the one the suite's tools draw. Re-sync by copying
the five files over and updating the version and commit above; anything this
program needs that upstream does not provide belongs in `core/crystal.py`,
which is the adapter, not in here.

Why copied rather than depended on: `molom` is a desktop application whose
install pulls PyOpenGL, RDKit and Open Babel. These modules are pure Python
and numpy (spglib for space-group names and for a CIF that names its group
but lists no operators), about 220 KB.

    cif.py          parse_cif, Cell, expand, site_composition
    pxrd.py         compute, the Pattern and Reflection types, profile,
                    parse_source, the emission lines
    spacegroups.py  identify, crystal_system
    scattering.py   form-factor coefficients          (pxrd needs it)
    elements.py     atomic numbers                    (both need it)

`cif.py` also carries bonding, fragment and packing code for MoloM's
viewport, which lazily imports modules that are deliberately NOT copied.
`expand` is called with `whole_molecules=False, boundary=False`, the
cell-contents path, which touches none of it; a change that reached one
would fail with an ImportError naming the module, not with a wrong number.

These files are the one place the source is not ASCII (`tests/test_repo.py`
leaves them out): they are copies, never edited here.
"""
