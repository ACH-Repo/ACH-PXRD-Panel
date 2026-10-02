# Plan and log

What is built, what is next, what is undecided. The decisions behind the
repo are in `docs/HANDOFF.md`; the family's register is in Christian's
global notes (`panel-family.md`), and the family plan (IR, then PXRD, on a
shared core) is `docs/FAMILY.md` in ACH-DSC-Panel.

## Round 1, 2026-10-02: the first version (0.1.0)

Built in one chat from IR-Panel's code (the family's newest member, as the
family rule asks) and the PXRD material already on his machine: the
diffraction suite's readers and quick plot, MoloM's crystallography and
PXRD window.

1. The copy: IR-Panel's tree as `src/pxrdpanel`, the name in
   `branding.py`; the suite ran unchanged (110 passed) before any PXRD
   change, so every later failure was the PXRD layer's.
2. Readers for `.raw` (RAW1.01, RAW4.00), `.brml`, Riet7 `.dat` and
   columns, with the wavelengths found in the bytes and checked against a
   second export of the same scan; CIFs and ICDD cards as reflections.
3. MoloM's crystallography copied verbatim; CIFs and cards simulated at
   the figure's wavelength, as a pattern, sticks, a tick row or lines
   across the plot. A calculated card and its CIF agree to 4 decimals in
   d (`test_a_cif_and_the_card_of_the_same_phase_agree`).
4. The x axis in 2-theta, d or Q, every position on the figure converted
   and every analysis measured again in one undo step; d reversed; the
   wavelength never assumed; mixed wavelengths said.
5. The drag list: peak position (and d), peak area, FWHM; highlight,
   magnify, normalise to the peak, break.
6. Tests (133), the operator doc, README, CHANGELOG, CLAUDE.md; a look at
   real data in all three quantities.

## Round 2 (2026-10-02): labels by name, the pick distance, normalising together

Christian, 2026-10-02: labels "spawned with default names without having
to type one first", for all selected lines at once, "across panel
plotters"; lower right of each line in PXRD, upper left in IR (if
transmission), upper right in DSC; and the default pick distance 8
"across projects". Family-wide, the same patch in the three members and
two tests in `tests/test_family.py`:

* `Ctrl+T` asks nothing: on selected curves a label each, the curve's
  name, hanging from its end at `profile.name_label_corner`
  (`MainWindow.name_labels`, `PlotWidget.end_sample`); on nothing, a free
  "Label". New labels are selected. His answer for IR in absorbance:
  lower left (the mirror of transmittance).
* The pick distance is 8 px built in (was 14).
* Not family-wide (DSC is never normalised), IR-Panel and PXRD-Panel:
  "all together, 0 to 1" (`units.NORM_GLOBAL`) - the lowest value of any
  curve on show is 0, the highest 1, recomputed as curves are opened,
  hidden or cut; the offsets stay (his choice). In F3 beside "each 0 to
  1", and a tick on the empty plot's right-click menu. IR starts with it;
  PXRD starts without normalisation (it started with each 0 to 1).
* IR-Panel and PXRD-Panel's "label at their left edge" became "Label
  every curve on show by its name", placed like `Ctrl+T` (his choice).

## Round 3 (2026-10-02): colours on white, one pattern scaled

Christian, with two screenshots (dark and white page) and his H2adp
figure script: why a yellow turns olive on white; and with all-together
normalisation the simulated patterns are tiny - make the swipe scale only
the selection, with a per-pattern factor shown as "xN" like his scripts'
`mults`, at the lower left of the patterns.

* The white page (family-wide, his choice): only the default palette is
  darkened (`paper_colour`); a picked colour is drawn as picked.
* PXRD only (his choice): `Scan.multiplier`, the selective swipe, the
  "xN" label (optional: no stamp when hidden), F3 and the settings.

## Next (suggested; Christian decides)

- Use it on real figures and collect requests here.
- The open choice in `docs/HANDOFF.md` section 3 (session copies).
- Git: initialise and push (his call).
- The shared core of the three members - now that there are three, a diff
  of `src/dscpanel`, `src/irpanel` and `src/pxrdpanel` shows it.
