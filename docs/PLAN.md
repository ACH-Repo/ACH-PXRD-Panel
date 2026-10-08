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

## Round 4 (2026-10-05): the hkl tab, the plane under the pointer

Christian, with a screenshot of a CIF's settings mocked up as two tabs
(General, hkl): bring MoloM's reflection list into the line settings of
a simulated pattern, without the rest of MoloM's window, and let hovering
over a simulated peak say which lattice plane it belongs to.

* `crystal.Reflection` keeps what MoloM's merged reflection knows: the
  `equivalents` (their count the multiplicity), |F|^2, LP; a card's has
  none (`multiplicity` None). `crystal.reflection_list` is the hkl list,
  with a CIF's absences on request (`keep_absent`), cached on the sample
  (`Sample.reflection_list`).
* A simulated pattern's settings: tabs General (as before, the rows
  scrolling inside it) and hkl (`dialogs.ReflectionTable`: sortable, in
  order of angle at first, absences greyed, "Copy the list" as
  tab-separated text). It follows the wavelength and range typed on
  General, and widens the window to its columns.
* Hover: on a simulated pattern the status line names the reflection
  under the pointer - "(1 1 1) and 7 more: d, 2-theta, I; also ..." -
  among those the scan DRAWS (`Scan.drawn_reflections`: the N strongest
  as sticks, ticks or lines), within the pick distance, or a peak width on
  a profile (`PlotWidget.reflections_under`, `reflection_readout`).
* Seen on his ZIF-4 CIF: it is P1 with angles of 90.000x, so every
  reflection is "and 1 more" (its Friedel mate), the Pbca extinctions are
  small but not zero (|F|^2 1e-4 for (1 0 0)) and are NOT called absent,
  and a family like {1 1 1} is four peaks 0.001 degrees apart. The file,
  not the code.

## Round 5 (2026-10-05): a Normalise submenu, units from the caption

Christian: the normalise options should open like Background, into all
four choices; and an axis's unit should change by double-clicking its
caption. For IR-Panel and PXRD-Panel; Triplot's unit changes wait (time
axes, /mol, the second y axis - his "own can of worms").

* `MainWindow.norm_menu` (`NORM_CHOICES`): None, Individual, Global, To
  a peak...; exclusive, the one in force ticked, each `set_display`'s
  one undo step. The single "all together" tick is gone.
* `CaptionSettings`: a "Shows" row where the axis can show something else
  - `MainWindow.axis_choices(axis)` lists it, `set_axis_shows` does it
  through the window (`set_x_quantity`), its own undo step, never the dialog's
  snapshot. A typed caption does not follow; an orange line says so.
* He reported that the normalisation could not be undone with Ctrl+Z
  (IR). Not reproduced: the tick from the right-click menu and a real
  Ctrl+Z through the window's handle both undid it, curves and y range.
  The new test undoes and redoes every entry of the submenu.
* Gone (his word): the stamp "NORMALISED, and the y caption does not
  say so", which a typed y caption on a normalised figure put on the plot
  and every export. A typed caption is the user's.

## Round 6 (2026-10-05): settings windows in order, family-wide

Christian, with two screenshots (a region's "Spectra" list, a band
marker's "Arrow colour"): text boxes at the top of their pop-ups, the rows
in an intuitive order - what is most likely edited after making the
object, and what only the window can edit, first; text and colour very
high. Asked: why a region has a spectra list (it is what a MAGNIFYING
region magnifies - one class for highlight and magnify), and why band
markers have arrow rows (labels, notes and markers share `LabelSettings`;
the rows belonged to notes). His word: all three panels, the arrow rows
off band markers, the proposed order.

* `_LiveDialog.FIRST_ROWS` / `LAST_ROWS` / `row_order()`, applied by
  `_buttons` through `order_rows`. Orders: band marker Text, Line at,
  Colour...; note Text, Colour, the arrow...; label Text, Colour, Size...,
  Leader arrow; region Text, From, Colour, Shade...; distance arrow Text,
  Colour, From, To...; analysis Label, Shows, Colour, Number format,
  Model...; offset marker Number format, Colour...; structure SMILES,
  Colour...; caption Text, Shows, Size...; Show and Layer last.
* A note's rows are shown only while it is a note (they were greyed);
  never on a band marker. A region's curve list only while it magnifies.
* Test: `test_settings_windows_put_the_text_and_the_colour_first` in
  `tests/test_family.py`, and the region's in `tests/test_dialogs.py`.

## Round 7 (2026-10-05): F3 remembers, aliases of your own, family-wide

Christian: show the F3 option used last when the search opens again; and
custom aliases, user/installation specific, shareable as a .json dropped
on the viewport, with a reset to factory. His choices: a recent list (not
the last query typed back), family-wide.

* `core/userops.py` (the same file in every member): `recent` /
  `note_used` (5, newest first), `aliases` / `add_alias` /
  `remove_aliases`, `export` / `read_shared` / `merge` / `install`,
  `reset`; kept in `operators.json` beside the preferences.
* `OperatorRegistry.search(..., extra=)` searches the user's aliases, and
  ranks an operator whose alias starts with what was typed first.
* `OperatorPalette`: "Used last" and "Every operator" headings when the
  box is empty, the newest recent selected; a column for the user's
  aliases (greyed); right-click: "Add an alias...", "Remove my alias".
  The window's `previous` text was never set (dead since Triplot); the
  window builds the palette in `operator_palette` and records a choice
  in `palette_ran`.
* Edit > Operator search: save the aliases as..., install from a file...,
  reset to factory (asked). A dropped alias file installs (asked, with
  what it adds; unknown operators skipped and said).
* Tests in `tests/test_family.py`.

## Round 8 (2026-10-05): the source file, typed or pasted

Christian (a PXRD file window): set the source file there too, by
browsing or by pasting a full path, the quotes Windows adds stripped. His
word: all three panels, the file's window and the curve's.

* `dialogs.SourceRow` (a path box and Browse...) in `SampleSettings` and
  `ScanSettings` (Triplot's curve window had no File row; it has one now,
  after Analyses). `clean_path`: spaces, quotes, file:/// links.
* `_LiveDialog._source_chosen`: the window closes, `change_source` runs
  (its own undo step), the window opens again on the new file.
  `MainWindow.ask_source_path` is the file dialog both use.
* Test in `tests/test_family.py`; end to end with a real .xy in `tests/test_window.py`.

## Round 9 (2026-10-07): colours as on the screen, family-wide

From Triplot (Christian: an orange curve exported "nearly brown").
`paper_colour` drew the default palette darkened to `PAPER_LUMA` on a
white page, so every export: its orange became brown. Shown the
choices (the same hue and saturation at 3:1 or 3.5:1 contrast, or
exactly as on screen), he chose the screen colours as they are, for
the whole family. `for_light` is the handling colours' only now. Test
in `tests/test_family.py`.

## Round 10 (2026-10-08): missing files kept, found again - family-wide

Christian (2026-10-08): a Triplot session copied about "lost its data
source" - its file had been saved from Downloads as `x(1).tri` and lay
beside the session as `x.tri`. The look beside a session is by exact
name, so the file was missing, and the outliner showed nothing at all.
Here a session keeps a copy of each file, so this is a session saved
before there were copies, or one whose copy cannot be read.
He chose, for the whole family:

* **The file keeps a row** (`model.MissingSource`, `doc.missing`): its
  name in red, MISSING, its saved path on the tooltip. What the session
  held of it - the file's entry, its curves', the labels hanging from
  them - is KEPT and put back into every save at its place
  (`session._keep_missing`), colour links, span ends and region curves
  moved to match. Before, the next save lost all of it.
* **Right-click: Locate...** (by hand), **Find in a folder...** (his
  idea: `os.walk` under a folder he names, the same extension, the name
  at least 0.85 alike by difflib - `session.similar_files`), Details...,
  Forget (one undo step). A copy's "(1)" / " - Copy" counts as the same
  name and comes first, then names with the same numbers: "Run-2" is as
  alike to "Run-1" as "Run-1(1)" is, so a search only OFFERS
  (`FoundFilesDialog`, the likeliest chosen), and the automatic look
  beside the session stays by exact name. Found, the figure is opened
  again from its own state with the new place (`found_sources`,
  `session.from_state(..., found=...)`): everything back, the undo
  history cleared, changed until saved.
* **Details...** on every file's right-click menu (his request: two files
  of one name): folder, size, created and modified, SHA-256 of the
  contents, and what the run records (`profile.details`); Copy puts it
  on the clipboard. Not modal, so two can stand side by side.
* Found on the way: a colour link names objects by their place in the
  file, and with a file missing the places moved - a link joined the
  wrong curves. `from_state` now restores them through the places in
  the FILE (`_saved_target`).

Test in `tests/test_family.py`; spans and regions in
`tests/test_window.py`.

## Next (suggested; Christian decides)

- Use it on real figures and collect requests here.
- The open choice in `docs/HANDOFF.md` section 3 (session copies).
- The shared core of the three members - now that there are three, a diff
  of `src/dscpanel`, `src/irpanel` and `src/pxrdpanel` shows it.
