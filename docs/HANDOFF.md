# Handoff: the chat that built PXRD-Panel (2026-10-02)

Everything a new session needs to pick this up cold. Read this, then
`CLAUDE.md` (the rules and the traps) and `docs/PLAN.md` (the log and
what is next). `docs/OPERATORS.md` is generated from the code.

## 0. State

- Version 0.2.0 (2026-10-02). A git repository since 2026-10-02, on
  GitHub as ACH-Repo/ACH-PXRD-Panel (see PLAN.md). Not on PyPI: the
  family name is to be settled first (Christian, 2026-10-02).
- `python -m pytest -q`: 146 passed, none skipped, with the real files
  listed in `tests/local_testdata.txt` (gitignored) on disk.
- Looked at with real data, offscreen with the real fonts: three `.brml`
  patterns, a CIF as sticks and a card as lines across the plot, in
  2-theta, d and Q, and a light-theme export. Not yet used by Christian.

## 1. What was asked

Christian: "transfer what we have learned through ACH-DSC-Panel and
ACH-IR-Panel to an equivalent for PXRD"; PXRD "a lot closer to IR than
DSC", the analyses "a pretty close match between the two". His answers to
the questions put first:

| Question | Answer |
| :-- | :-- |
| Name? | **PXRD-Panel, provisional** (`pxrd-panel`, import `pxrdpanel`, `.pxrdpanel` sessions, `.pxrdstyle` presets). |
| Analyses? | **IR's three, as they are**: peak position (with d), peak area, FWHM; plus the drag actions (highlight, magnify, normalise to the peak, break). Note: FAMILY.md had him saying a PXRD peak never needs integrating; he chose the area anyway. |
| The x axis? | **2-theta, with d and Q views**; the wavelength read from the file, missing said and typed by hand. (Not chosen: recasting 2-theta to another wavelength.) |
| CIFs and cards in round 1? | **Yes, as traces and as ticks.** |

How it was built follows the family rule (his global notes): a new member
starts from the NEWEST member's code - IR-Panel's, renamed - and has
everything in the register's log (checked: all ten 2026-10-01 entries are
here, and `tests/test_family.py` is byte-identical in the three members).

## 2. What was built

From IR-Panel, removed: the four IR readers, %T / A and the unit guess,
the absorbance analyses, the reversed wavenumber axis. Kept: all the
handling, the broken x axis, regions, distance arrows, marker lines (IR's
band markers), edge labels, offset markers, normalisation.

New for PXRD (details in `CLAUDE.md` and the module docstrings):

- `core/readers.py`: Bruker `.raw` RAW1.01 and RAW4.00 and `.brml`, Riet7
  `.dat`, columns - the suite's validated logic (ACH-Diffraction-Analysis-
  Suite `core/readers.py`, `core/bruker.py`) rewritten into the panel, plus
  the WAVELENGTHS, which the suite never read: found in the bytes of
  RAW1.01 and RAW4.00 and checked against the Riet7 header and the `.brml`
  of the same scans. `.cif` and ICDD `.xml` cards as reflections.
- `core/_molom/`: MoloM's crystallography, the same five files the suite
  vendors, byte for byte (hash-checked by a test). `core/crystal.py` is
  the adapter (the suite's site bookkeeping, so a CIF gives the suite's
  pattern), plus sticks, ticks, lines and typed wavelengths
  (`parse_source`: "Cu Ka1", "17 keV", "0.7093").
- `core/units.py`: 2-theta, d, Q; normalisation (none at the start;
  all together 0 to 1, each 0 to 1, or to a peak - round 2).
- `core/model.py`: `Document.x_quantity`, `set_x_quantity` (everything
  converted, analyses measured again, one undo step), the figure's
  wavelength, mixed wavelengths; `Sample` wavelengths and simulations;
  `Scan.draw_as` (curve, sticks, tick row, lines) and `strongest`.
- `core/measure.py`: IR's three on the stored intensities, on the axis as
  shown; a peak position carries its d.
- Labels: `{d}` writes a peak's d-spacing.
- The UI: "X axis: 2-theta / d / Q", "Give the wavelength of the selected
  patterns...", "Draw the selected simulations as: ..." (F3); a pattern's
  settings hold its wavelength (said where it comes from, or that there is
  none), and a simulation's range, peak width, drawing and N strongest.
  Lines across the plot are painted by `PlotWidget._paint_lines`.
- Exports: "NO WAVELENGTH" and "DIFFERENT WAVELENGTHS on one 2-theta axis"
  are stamped; "SIMULATED ... at ..." is printed.

## 3. Open for Christian

- **Use it** on real figures and collect requests in `docs/PLAN.md`.
- ~~Git~~ Done: public on GitHub as `ACH-Repo/ACH-PXRD-Panel` since
  2026-10-05.
- **The name** (with the family's: Stackline was suggested).
- ~~Edge labels on the left?~~ Settled 2026-10-02: name labels hang
  below each pattern's right end (`Ctrl+T`, and "Label every pattern on
  show by its name").
- **Copies inside sessions** (`profile.EMBED_SOURCES`, on as in IR-Panel):
  a `.brml` is 300 kB and already a zip, so ten of them add ~3 MB to a
  session. Triplot keeps none; his call.
- ~~The default normalisation~~ Settled 2026-10-02: a figure starts
  without; "all together, 0 to 1" is new (F3, the plot's right-click).
- **Recasting 2-theta to one wavelength** (offered, not chosen): a Mo or
  synchrotron pattern shown in Cu 2-theta. d and Q do the comparison now.
- **More formats** - Panalytical `.xrdml`, STOE, GSAS `.fxye`,
  multi-range Bruker files - real files first.
- **K-alpha2**: simulations are K-alpha1 only; MoloM's core can draw the
  doublet (`components`). A measured pattern's d is K-alpha1's.
- **Background subtraction / K-alpha2 stripping**: not done, deliberately
  (MoloM's rule: a viewer does not alter somebody's data). Ask before.
- **Neutral source before any release**: the source cites MoloM and the
  suite by name in `core/_molom/__init__.py` and `core/crystal.py`
  (provenance of copied code - probably fine, but his call).

## 4. Where the real files are

Listed in `tests/local_testdata.txt` (gitignored, on this machine): RAW1.01
scans with their Riet7 `.dat` exports (a 2023 folder of his own samples),
RAW4.00 scans with their `.brml` originals, ICDD cards with the CIF of the
same phase (a colleague's project folder), structure-database `.raw`
exports, and the folder of his old PXRD plotter script (a `.xy` without a
wavelength, a refinement's observed pattern, a CIF, a card).
`tests/test_readers.py` names each file it uses by a hash of its name.
The diffraction suite's memory notes 37 more RAW4 files with references
on the Desktop (`raw4-and-cif-backend-followups`).
