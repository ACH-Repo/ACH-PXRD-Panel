# Working on PXRD-Panel (a working title)

Notes for whoever (or whatever) picks this up next. **Start with
`docs/HANDOFF.md`**: how this repo came to be, what is decided and what is
open. `docs/PLAN.md` is the log. This file is about how the work is done.

PXRD-Panel is the powder-diffraction member of a family of stacked-trace
plotters. It began on 2026-10-02 as a COPY of IR-Panel (`ACH-IR-Panel`,
import name `irpanel`, itself a copy of Triplot), renamed `pxrdpanel`, with
the same file layout on purpose: a later diff of the trees shows what is
shared, which is how the common core gets drawn out. The handling - the
plot widget, gestures, undo, operators, sessions, house style, exports -
is the family's; the readers, the x quantities and the wavelength, the
simulations, the analyses and the decorators' wording are PXRD's own.

## Golden rules

1. **`core/` never imports a UI.** The model, the readers, the units, the
   analyses, the simulations, the arranging, the undo stack, the session
   and the exports are plain Python and numpy, testable without a window.
   Qt lives in `ui/` and in `register.py`.
2. **The name lives in `branding.py`.** PXRD-Panel / `pxrd-panel` is
   provisional. A rename is that module, the two entry points and the
   project name in `pyproject.toml`, and `LEGACY_NAMES` (empty until the
   first rename; `register --clean-legacy` removes what an old name
   registered).
3. **Never write a reader or a fixture from memory.** Every reader in
   `core/readers.py` was written against Christian's real files and is
   checked against another export of the same measurement
   (`tests/test_readers.py`): RAW1.01 against its Riet7 `.dat`, RAW4.00
   against its `.brml`, an ICDD card against the CIF of the same phase. A
   new format (a Panalytical `.xrdml`, a STOE `.raw`, a GSAS `.fxye`) waits
   for a real file - ask him, he has them. `tests/conftest.py` builds
   synthetic patterns in the shape a reader RETURNS, which is allowed.
4. **Nothing is assumed, and what is done is said.** The WAVELENGTH is
   read where a file states it and never made up: a pattern without one is
   drawn in 2-theta only and is a dashed placeholder ("NO WAVELENGTH") on a
   d or Q axis until the user gives one (`Sample.wavelength_override`). A
   simulation is drawn at the figure's wavelength, said wherever it is
   shown. Patterns of different wavelengths on one 2-theta axis are said
   (orange flash) and stamped on exports. Normalisation is allowed (a
   figure starts without; all together 0 to 1, each 0 to 1, or to a peak)
   and the y caption says "(normalised)" whenever it is on - the automatic
   one. A caption the user TYPED is theirs and is never stamped over
   (the "NORMALISED, and the y caption does not say so" stamp went on
   2026-10-05, his word).
5. **Python is exactly 3.10.0** (`C:\Program Files\Python310`). No `X | Y`
   annotations, no match statements.
6. **No em-dashes, ASCII in source.** A plain `-`. PowerShell 5.1 reads a
   file without a BOM as cp1252 and mangles anything else. A test checks
   (`tests/test_repo.py`). Write a Greek letter, a degree sign or an
   angstrom as an escape (`"\u00c5"`), never the character. The ONE
   exception is `core/_molom/`, copied verbatim (rule 10).
7. **Everything that ships is NEUTRAL**: `src/` and `tests/` carry no
   person, sample id, private project, machine or development history
   (the author line in `branding.py` aside). Tests name real files by a
   HASH of the file name (`conftest.hashed_name`); where the files are is
   in `tests/local_testdata.txt` (gitignored) or `PXRD_TESTDATA`. History
   belongs here and in `docs/`.
8. **Never commit or push unless Christian asks in that message.**
   Finishing a piece of work is not an implicit request to publish it.
   He runs `twine` himself.
9. **Design choices go to Christian first**; no agent fleets.
10. **`core/_molom/` is ACH-MoloM's crystallography, copied byte for
    byte** (the same five files ACH-Diffraction-Analysis-Suite carries).
    Never edit it here; `core/crystal.py` is the adapter.
    `test_the_copied_crystallography_is_not_edited_here` holds their
    hashes: a re-sync copies all five again and updates the hashes and the
    version line in `core/_molom/__init__.py` together.

## Where things are

| File | What it owns |
| :-- | :-- |
| `core/readers.py` | the readers (`.raw` RAW1.01/RAW4.00, `.brml`, Riet7 `.dat`, columns, `.cif`, ICDD `.xml`) -> `Pattern` with its `Source` (the wavelengths) |
| `core/crystal.py` | a CIF's or a card's reflections made into a pattern at a wavelength; sticks, ticks, lines; typed wavelengths (`parse_wavelength`) |
| `core/_molom/` | MoloM's CIF parser, structure factors, space groups (verbatim) |
| `core/units.py` | 2-theta, d, Q and the conversions; intensity; normalisation none / all together 0-1 / each 0-1 / peak; the captions |
| `core/loader.py` | path -> `Sample`; `fit_to_figure` (a simulation's wavelength and range from the figure) |
| `core/model.py` | `Document` (the x quantity, its wavelength, `set_x_quantity`), `Sample`, `Scan` (draw modes), `Analysis` (its `axis`), `TextLabel`, `Region`, `SpanArrow`, selection, `set_display` |
| `core/measure.py` | peak position (and d), peak area, peak width; the drag list (`ACTIONS`) |
| `core/labels.py` | what an analysis label says; `{}` and `{d}`; a marker line's `{}` |
| `core/arrange.py` | stack, distribute, align (the closed-form `y_align`) |
| `core/undo.py` | commands, gesture merging |
| `core/session.py` | the `.pxrdpanel` file |
| `core/style.py` | the house style: object -> figure -> user default -> built-in |
| `core/figure.py` | the figure's size: window, aspect ratio, or exact size and margins |
| `core/presets.py` | style presets (`.pxrdstyle`): a figure's look (and size) in a file |
| `core/chem.py` | a SMILES as atoms and bonds to draw (RDKit) |
| `core/log.py` | the log file, the excepthook, the crash file |
| `core/numbers.py` | how a number is written: the `%.3g` formats; typed sums |
| `core/shades.py` | shades of one colour for a stack (the gradient) |
| `core/export.py` | CSV, and the export warnings (no driver export: decided for the family) |
| `core/profile.py` | what is particular to PXRD: F staged, a d axis reversed, the d fit, rounded ends, new files stacked, the background |
| `core/ops.py` | the operator registry (copied from MoloM via Triplot) |
| `ui/plot.py` | the painted plot: view, the reversed and broken x axis, gestures, picking, drawing (`_paint_lines` for a simulation as lines) |
| `ui/window.py` | operators (`set_x_quantity`, `ask_wavelength`, `set_drawing`), menus, docks, drops, exports, undo |
| `ui/dialogs.py` | every settings window; `PatternRows` (wavelength, simulation, draw mode); `RegionSettings`, `SpanSettings`, `BreakDialog` |
| `ui/settings.py`, `ui/appearance.py`, `ui/colour.py`, `ui/numbox.py`, `ui/outliner.py`, `ui/palette.py`, `ui/loading.py` | as in IR-Panel |

## Traps paid for here

* **A settings window's rows are ORDERED, not built in order**
  (2026-10-05, family-wide): `_LiveDialog.FIRST_ROWS` / `LAST_ROWS` (or
  `row_order()`, which `LabelSettings` overrides per kind) name rows by
  label or "@attribute"; `_buttons` - every window's last call - moves
  them (`order_rows`). A new row joins its window's list, or it lands in
  the middle. The widgets are moved, not rebuilt: hiding a row means its
  field AND `labelForField`.

* **Every x position on the figure is in the axis's CURRENT quantity**
  (2-theta, d or Q): regions, marker lines, arrows, artists pinned to the
  data, notes' tips, the break, the normalisation peak, offset markers
  placed by x. `Document.set_x_quantity` converts ALL of them at the
  figure's wavelength (`Document.wavelength`: the first measured pattern's
  that states one) and returns `(obj, attr, value)` changes for ONE undo
  step. Anything new that stores an x must join it, or it is left in the
  old unit when the axis changes. A framing or lock carries its context
  (`[x_axis, x_unit]`) and restores as the fit in another quantity.
* **An analysis records which axis its numbers are on** (`Analysis.axis`).
  `set_x_quantity` measures every analysis AGAIN on the new axis (same
  sample `span`; typed cursors converted at the pattern's own wavelength)
  - an area or a width in d is not a converted number. An analysis whose
  pattern has no wavelength keeps its numbers and its axis. A session
  measures each again on the axis it was saved on
  (`measure.run(..., quantity=)`): never re-measure on the document's
  axis by default.
* **Cursors are stored in full** (`"{!r}"` in `measure._cursors`). With six
  significant digits an interval converted 2-theta -> Q lost its edge
  sample on reload and the area changed in the third digit.
* **A scan knows its document** (`Scan.doc`, set by `add_sample`,
  `insert_scan` and the session): `x_values()` asks it for the quantity.
  A `Scan` built anywhere else must be given one.
* **`np.interp` needs x to increase, and a d axis runs DOWN** with the
  sample index. `measure._series` turns a series ascending; `_chord` in
  `ui/plot.py` and `model._chord_at` take either order. A bare `np.interp`
  on a pattern's x is wrong in d.
* **The d axis is reversed by the DOCUMENT, not a constant**:
  `profile.x_reversed(doc)`. And its fit stops at the spacing of
  `profile.D_FIT_FROM` degrees 2-theta (`PlotWidget._fit_span`): a pattern
  measured from 0.02 degrees has spacings of thousands of angstrom. A
  2-theta of 0 has no d (NaN), never infinity.
* **A simulated sample's x and y are made when asked** (`Sample.simulation`,
  cached on wavelength, range and width). `Scan.curve` calls it FIRST so
  its cache key sees the simulation's key; a key taken before that kept a
  stale curve after the wavelength changed. A simulation starts at
  `crystal.LOWEST` (1 degree) whatever the measured patterns cover.
* **Sticks and tick rows are curves with NaN between the sticks**
  (`crystal.sticks`): the polyline code breaks runs at NaN, picking and
  fits ignore it. Anything new that reads a curve must tolerate NaN -
  `profile.baseline` uses only finite values (`np.percentile` of a NaN is
  NaN, and the swipe moved nothing).
* **"Lines across the plot" have no height**: the trace's y is all NaN,
  `_paint_trace` hands it to `_paint_lines`, which leaves pick points down
  each line every 3 figure units (24 per line missed the pointer).
  `missing_for` never reports a normalisation problem for it, `factor` is
  None (no part in a stack's conversion), and it is not a drag target.
* **The wavelength is K-alpha1** where a file states a doublet, as
  angstrom: RAW1.01 at bytes 624 (alpha1), 632 (alpha2), 648 (ratio), 616
  (average), 608 the anode; RAW4.00 in its type-30 record at +80, +88,
  +104, +72; `.brml` `WaveLengthAlpha1/2/Ratio`; Riet7 `Alpha1 ... Alpha2
  ... Ratio ...`. All four were found in the bytes and checked against the
  other export of the same scan, not taken from a spec. A database export
  (`.raw` "Exported by CCDC-toolkit") states K-alpha1 only.
* **RAW1.01's range header holds THETA at +8 and 2-THETA at +16**; read
  from +8, every peak sits at half its angle and still looks plausible.
* **A card's spacings are the truth, not its angles** (`<da>`; the angle
  only where a card has no d): drawn at the figure's wavelength, a card
  measured at 1.5418 overlays a 1.5406 pattern correctly. Its intensities
  may carry letters ("7m"). No LP factor is applied to a card's
  intensities: they were measured with it.
* **The editing tool decodes `\uXXXX`** in what it writes - and so does a
  bash heredoc fed to Python. Run
  `python <scratch>/ascii.py <files>` (non-ASCII -> escapes; made in the
  first chat, trivially rewritten: `chr(92) + "u%04x"`) after writing, or
  build the escape with `chr` in a patch script. The ASCII test catches
  what slips through.
* **A module-level name given twice replaces the first** (IR-Panel's
  `ACCENTS`), **`register.py` launches `-m` + the package's own name**,
  **never name a dialog field like a QWidget method**, **patch files
  through a script, not a heredoc**: IR-Panel's traps, still true.

* **"All together, 0 to 1" scales by every curve ON SHOW**
  (`units.NORM_GLOBAL`, `Document.norm_extent`, 2026-10-02): the lowest
  value of any visible curve's kept samples is 0, the highest 1. Opening,
  hiding or cutting a curve changes it and every curve follows; the
  OFFSETS stay where they are (Christian's choice). `norm_extent` is
  memoised on what it depends on, and `Scan.curve`'s cache key carries it -
  without that a curve kept its old scale when another was added.

* **A pattern's `multiplier` is drawn, never measured** (2026-10-02,
  PXRD only - Christian's choice): `Scan.display_values` multiplies the
  normalised values (scale and shift of the affine), so everything drawn
  from them follows (analysis marks, labels on the curve) while
  `measure` and `Document.norm_extent` read the stored values. It scales
  about 0, as his scripts did (`Y *= mult`), and `Document.rescaled`
  moves the offset so the baseline keeps its place. The swipe on a
  SELECTION changes it (`PlotWidget.scale_patterns`); with nothing
  selected the swipe is the family's (the axis). A swipe's undo step holds
  every scan's offset AND multiplier AND the labels list (`_offsets_now`),
  because leaving x1 adds an "xN" label (`TextLabel.shows ==
  "multiplier"`, `Document.new_multiplier_label`); one taken away is not
  put back except by F3 "Say the selected patterns' scale". At x1 the
  label says nothing and is not painted. Optional by his word: no stamp.

## Traps inherited from Triplot and PXRD-Panel (they hold here unchanged)

* **`QWidget.grab()` already exists.** The transform is `start_grab`; naming
  it `grab` shadowed the screenshot method and broke every test that took a
  picture.
* **A shortcut claimed twice fires neither** (Qt reports an ambiguous
  overload). `_install_shortcuts` raises at startup instead.
* **A modal `exec()` in a test hangs rather than fails.** Menus and dialogs
  are built separately from being shown; tests build them. (Walked into it
  again writing the outliner regression test: `Outliner._menu` SHOWS a menu.)
* **With the log installed, a slot error is logged and SURVIVED** (round
  21): PySide6 hands it to `sys.excepthook`, which `core/log.py` sets in
  the real program only. Tests do not install it, so there the old rule
  below still holds - and a test must not rely on either.
* **An error in a method Qt calls BY ITSELF never reaches the hook**
  (2026-09-30): `mouseMoveEvent`, `paintEvent`, `event`... - PySide6 6.11
  ends the process (an access violation, no line in the log). Every UI
  module ends with `log.guard_classes(globals(), __name__)`, which wraps
  each `...Event` and the methods in `log.HANDLERS` so the error is logged
  and survived - only while the hook is installed, so a test still sees
  it. A new UI module needs the line; `test_every_qt_handler_in_the_ui_is_
  guarded` says so. Found by dragging a label whose curve was hidden.
* **An exception inside a Qt slot is not a traceback, it is an abort.**
  PySide6 terminates the process. So a crash with no output is usually an
  ordinary Python error in a slot.
* **Never rebuild a tree from inside its own signal.** `clear()` deletes the
  row the click is still inside. The outliner defers `visibility_changed`,
  `file_toggled` and `activated_object` with `QTimer.singleShot(0, ...)`,
  and `refill` refuses to run re-entrantly.
* **An artist is not data.** `PlotWidget.is_artist` decides what may move
  along x: a region, a distance arrow or a marker line may, a pattern
  may not. Anything new that is drawn on the figure rather than measured
  belongs on the artist side.
* **A theme is a dictionary in `ui/plot.py`**, applied by `set_theme` into
  module globals. Everything drawn reads those names, so a new theme is an
  entry in `THEMES` and nothing else. `doc.theme` holds the name; the window
  applies it on every refresh, and exports force the light one.
* **A drag acts on what it starts NEAR** (Christian, round 9). Within the
  pick distance (a setting, 8 px built in) of a curve, a press-and-drag
  marks an interval and asks for the analysis on release; near an artist,
  an analysis label or an axis caption it moves it; anywhere else - or with
  Shift - it is a box select. Released unmoved, a press is a click. A
  double-click opens settings; a double-click-drag does what a drag does. A
  scan moves with `G` or a typed number and NOTHING else.
  Round 8 had "one press selects, two act", and it failed in his hands: a
  Windows touchpad's tap-and-drag arrives as ONE press plus a drag, never as
  a double-click, so it was a box every time. Test gestures through Qt's real
  pipeline (`QTest` on `window.windowHandle()`), not only by calling the
  handlers, which is how that was missed.
* **Selecting a buried object in an overlapping stack is unsolved.** Click
  cycling was considered and dropped (Christian, round 9): the second click
  of a cycle is a double-click, which opens settings. Needs another
  mechanism; see PLAN.md, open questions.
* **Zoom, pan and fit are undo steps** (round 9 reversed round 1's "undo is
  for the document"). The plot turns a gesture into ONE step - a wheel or
  pinch burst ends after `VIEW_SETTLE_MS` of stillness - and emits
  `view_committed`. Undo and redo call `plot.commit_view()` first, or a
  Ctrl+Z pressed mid-burst undoes the step before and the burst then lands
  on top and erases the redo. A framing taken in another unit restores as
  the fit, not as meaningless numbers.
* **A styled size is None until someone chooses it.** `Analysis.label_size`,
  `flush`, `Axis.label_size`/`tick_size`, `Legend.size`, `TextLabel.size`
  and `Scan.line_width` fall back on the figure's `doc.style`, then the
  user's `preferences.json`, then the built-in value (`core/style.py`).
  Never read them directly: `style.value(doc, obj, attr)`, or
  `PlotWidget.style_of`. A literal default written onto an object pins it
  and the user's defaults silently stop reaching it - which is why a
  version-1 session's sizes that equal the built-ins are read as None.
* **Tests must not touch the real preferences.** The window never loads
  them (`__main__` does); conftest's autouse `own_preferences` points
  `style.PATH_OVERRIDE` at a temporary file for every test. The pick
  distance is a HANDLING setting (`figure=False`): user-only, saved under
  `handling`, never in a session.
* **A modal dialog in a test used to hang the suite.** conftest's autouse
  `no_modal_loops` makes every `exec()`, `QMessageBox.about` and static
  file/colour/input dialog answer "cancelled" and fails the test at
  teardown naming it. It does not raise, because these run inside slots,
  where PySide6 turns an exception into an abort. A test that means to reach
  one stubs it (`window.ask_analysis = ...`).
* **The palette reaches existing widgets through the event loop**, and
  Fusion's `standardPalette()` follows the colour scheme in force when it is
  asked - taken while the dark scheme was set, the "light" palette came back
  dark. `appearance` sets the scheme first, then an explicit palette, and
  the window applies it before building any widget.
* **A QMenu fetched back through a temporary QAction wrapper** can already
  be deleted on the Python side. The window keeps `self.menus`.
* **A dialog field must not be named like a QWidget method.** `self.size`
  (a spin box) hid `QWidget.size()`, and `width`, `x`, `y` did the same; it
  only showed when something asked a dialog how big it was. A test now scans
  `dialogs.py` and `settings.py` for it - the `grab` trap, again.
* **The render cache must key on EVERY object's selection.** It keyed on
  scans and analyses only, so a selected label or arrow kept (or never got)
  its orange until something else rebuilt the plot: a click that changes
  selection only asks for a repaint.
* **An analysis being adjusted is changed IN PLACE** (`window.remeasure`):
  same object, new fields, and a default label rewritten with the new number
  (`measure.relabelled`). Replacing it left an open dialog editing an object
  no longer in the figure, and kept the old number in the label. Its gizmos
  live exactly as long as its settings dialog: letting go of one recomputes,
  closing the dialog (any way) confirms and ends them, a press elsewhere is
  an ordinary press, and Esc does not strip them. The dialog opens beside
  them (`window.place_beside`, `plot.gizmo_rect`).
* **A window shortcut fires from inside the window's pop-ups** (a Qt.Tool
  settings dialog passes its keys up), so `Ctrl+W` there closed the whole
  program. `close_step` closes the pop-up in front first (`window.popups`).
  A test of a window shortcut must `show()` the window, or it never fires.
* **A trace holds the KEPT samples only** (`trace.first` is where they start
  in the pattern). Fit, picking, arranging, CSV and analyses read them; the
  hidden ends are painted dashed in the per-event overlay while the scan is
  hovered or selected, so no export can carry them.
* **`np.interp` needs x to increase**, and a pattern's x may run either
  way (a file written 4000 to 400 keeps that order). Use `_chord` in
  `ui/plot.py` or `model._chord_at`, never a bare `np.interp` on file order.
* **A pop-up keeps its changes however it is closed** (round 11: "a change
  is a change"). `_LiveDialog.reject` - X, Esc, Ctrl+W - accepts; only the
  Revert button (`revert`) puts things back. Either way the window makes
  one undo step.
* **Unsaved changes are COMPARED, not counted**: `window.is_modified` diffs
  `session.to_state` (with the plot's live view) against the last save or
  open. Since round 17 the view IS in the file, so a zoom is a change;
  undoing back to the saved state is clean. `closeEvent` asks
  (`ask_to_save`, stubbable). The comparison must never WRITE: it runs on
  every title refresh, and copying the view onto the document there wiped
  an opened session's view before it was restored.
* **A move restores what was STORED, not what was drawn** (`_stored_of`).
  Restoring the drawn value turned "follow the house style" (None) into a
  fixed number whenever a drag was cancelled or undone.
* **An axis caption keeps `caption_gap` px from its NUMBERS** (house style,
  per axis `label_gap`), below them for x and left of the widest for y; the
  margins grow to fit. Session version 3 drops older `label_gap`s, which
  were measured from somewhere else.
* **The figure is drawn in its OWN space, 96 units per inch** (round 12,
  `core/figure.py`). 96 is Qt's logical DPI for fonts on screen and in a
  QImage, so a 12 pt font is 12/72 inch in the figure everywhere. At an
  exact size the page is scaled onto the pane (`PlotWidget.page`), so every
  mouse position goes through `to_figure` first, and the pick distance and
  drag slop are pane pixels divided by the scale. `plot_rect()` is a
  FRACTIONAL QRectF (an integer rect rounds an exact margin by 0.26 mm):
  never build a QRect from it, and `QPainter.drawLine` takes QPointF.
* **At an exact size the MARGINS decide the axes box**; numbers and captions
  live inside them and `overflow()` says what does not fit. Automatic
  margins (sized to the widest number) made "10.25" and "0.5" two boxes.
* **Exports are exact**: the PNG's DPI is written AFTER painting (fonts are
  converted with the device's DPI, which must be 96 while painting);
  QSvgGenerator defaults to 72 dpi (every font at 3/4 size until round 12)
  and its root width/height are rewritten in exact mm.
* **Interval marks**: a dash at each bound, centred ON the trace, in the
  axis colour; for an analysis whose result is a point
  (`Analysis.marks_a_point`: the peak position) straight lines bound ->
  point -> bound, no dash at the point; an area never gets a connecting
  curve. `PlotWidget.interval_marks` is the geometry.
* **Picking reads the LAST PAINT.** `_trace_at` uses the points drawn by the
  previous render, and `refresh()` (which `undo.clear()` calls) rebuilds the
  traces without them. A test that clicks after a refresh must `grab()`
  first, or the click lands on nothing and the test passes vacuously.
* **Qt may deliver a double-click's second press twice**: as a press AND
  then as the double-click, or as the double-click alone. The double-click
  handler drops any box the extra press started.
* **An analysis made in the panel exists nowhere else.** The session stores
  its model and cursors and recomputes it from the re-read file on load; a
  file's own analyses only have their styling stored, matched by `key()`.
* **A group dialog MIRRORS by diff** (round 14). `_LiveDialog._mirror`
  copies to the rest of the group only the fields whose value on the shown
  object changed since the last change, because every `_apply` writes ALL
  its fields from the widgets - copying everything would flatten each
  object's own values. A new per-object field goes in `INDIVIDUAL`, its
  widget in `GROUP_DISABLED`; the undo step reads `dialog.snapshots()`.
* **The first click of a double-click narrows the selection.** A drag or a
  settings dialog that should act on a shift-selected group must survive
  it: `mouseReleaseEvent` keeps `_click_restore`, and the double-click puts
  the selection back.
* **An analysis label never holds its number** (round 15). `label` is a
  template; `{}` is filled by `labels.render` on every draw, in the axes'
  units unless a unit follows it. Never write a measured number into a
  label, and read what a label SAYS through `render` (or
  `Analysis.summary(doc)`), never `analysis.label`.
* **`%.3g` is NOT Python's here** (`numbers.write`): significant figures,
  all written, no exponent. A format may hold no text - a unit in a format
  would print one unit's number with another's sign.
* **A point on a curve is a SAMPLE, found by walking** (`PlotWidget._walk`):
  the offset marker, like an analysis span. The walk tolerates a few units
  back before calling it a turn.
* **Enter must not close a live dialog** (`dialogs.enter_stays`): a spin
  box ignores Enter after taking its value, and QDialog then presses the
  first auto-default button - OK.
* **A plain-text tooltip is drawn on ONE line**, however long. Dialogs run
  `dialogs.readable` on show, which wraps every tooltip as rich text (and
  makes labels selectable); a tooltip set after showing is not wrapped.
* **An analysis's model and interval change through the WINDOW**
  (`change_model`, `retype_interval`, `remeasure`), each its own undo step,
  never through a dialog's snapshot: the snapshot would record the same
  change a second time.
* **S and R claim their keys through ShortcutOverride** (`PlotWidget.
  event`): M and C are window shortcuts (x range, measure), and a window
  shortcut fires before the focused widget sees the key.
* **A transform places anchors unclamped** (`set_artist_point(...,
  clamp=False)`): scaling a label about its corner moves its centre, and
  the 1 % edge clamp a drag uses moved the pivot instead.
* **`doc`, `plot` and `undo` are PROPERTIES of the current tab**
  (`MainWindow._figure`, a `FigureTab`). Never connect a signal to a
  BOUND method of them (`self.undo.end_group`): that binds the tab that
  was current at connection time. Connect a lambda that looks it up.
  Pop-ups are closed before a tab switch, so their undo step lands on
  their own figure.
* **Never name a window method after an existing one**: `stack_selected`
  already meant "stack the scans evenly", and an `enabled` predicate
  calling the new one would have re-stacked the scans on every menu
  refresh. Grep first.
* **Drawing order is the stack order** (`PlotWidget._paint_items`, sorted
  by `model.z_of`); a new drawn kind adds itself there and to
  `model.KIND_Z`, or it is never drawn.
* **Windows reports every Alt+wheel as HORIZONTAL** (Qt's platform plugin:
  Alt is a mouse's sideways scroll). The page pan reads the real direction
  from `MainWindow.nativeEvent` (WM_MOUSEWHEEL vs WM_MOUSEHWHEEL). The
  hook runs for every message: it must never raise.
* **A menu mnemonic is a shortcut too**: "&Help" took Alt+H and "Show
  everything" never fired. `test_the_menu_bar_is_file_edit_search_help`
  checks the mnemonics against the operators' keys.
* **A structure is drawn from its STORED layout** (`MoleculeArtist.atoms`,
  `.bonds`); RDKit only makes a new one. Upright labels mean the POINTS
  are turned, not the painter (`_paint_molecule`).
* **Qt's double-click interval is the system's** (500 ms on Windows); the
  plot's click rhythm re-reads the timing (`_note_press`), and a Qt
  double-click slower than 350 ms is a layer step.
* **Qt's SVG writer ignores clipping** (checked, PySide6 6.11). Anything
  inside the axes' clip must stay inside `_paint_all`'s fenced block
  (`_clip_mark`), which `clip_svg` turns into a real `clipPath` after the
  export; a PNG clips anyway. Line segments that must never leave the axes
  are also cut geometrically (`_clip_segment`).
* **An axis has three hit targets** - `axis_spine_rect` (line and ticks),
  `axis_numbers_rect`, the caption box - and `axis_hit()` says which.
  Spine and numbers are never SELECTED (`_frame_part`); `edit_object(axis,
  part=...)` picks the window. The axis's LINES and inward ticks are the
  spine too (`frame_line_gap`), ranked behind anything within the pick
  distance. `Axis.visible` is the CAPTION's: never skip the spine or the
  numbers on it (round 28: a hidden y caption made the y axis unopenable).
* **A margin holds the numbers at the box's CORNERS** (round 28,
  `_numbers_overhang` in `page_needs`): a number is centred on its tick,
  so one on the corner hangs half over the margin beside. Where numbers
  are written is `numbered_ticks` / `_number_box`, shared with
  `_paint_grid`; a hidden number (`number_hidden`, kept with its unit
  like a lock) keeps its tick.
* **An axis switching sides goes through `window._to_side`**: on an exact
  figure both margins keep their white space beyond what they hold.
* **A scale's pivot is MEASURED, not predicted**: after changing the sizes
  the anchor goes back, the box is measured, and the anchor is moved by the
  pivot's error. A box does not grow in proportion to its font.
* **`markup_runs` returns `(text, italic, script)`**, script False, "sub" or
  "sup". Every text on the figure goes through it, added labels included.
* **A painter on an image starts from the APPLICATION font.** `_paint_all`
  sets `figure_font()` first, or exports lose the house font family.
* **The fitted y range includes the analysis labels** (`data_y`, round 22),
  and measuring a label asks for the axes box, whose margin asks for the
  widest y number, which asks for the fitted range: `_fitting` answers that
  inner call with the curves alone (the box's HEIGHT does not depend on
  it). `paint_into` memoises the range for one paint (`_fit_memo`); never
  keep it longer, or a change stops refitting.
* **Outliner rows remember being CLOSED, not being open.** "Open unless
  anything was open before" folded every new file away once the Decorators
  row (always there, always open) existed on an empty figure.
* **The middle button is a view gesture** (`_nav`, `nav_kind`); a middle
  double-click must never reach the left-button double-click code, which
  opens settings.
* **A label with a parent is drawn `follow()` above its stored place**
  (round 23): `artist_point` adds its scan's offset change since
  `parent_offset`, `set_artist_point` takes it off, `_artist_origin_px`
  adds it. Anything that places a label from pixels must go through them,
  and a unit change must convert `parent_offset` with the offsets
  (`Document.set_unit`), or every owned label jumps.
* **The outliner's drag is COPY, never Move**: after a MoveAction Qt
  deletes the dragged rows itself (`clearOrRemove`). A drop is a request
  to the window, deferred, and the rows are rebuilt.
* **`figure` is a loop variable in `ui/window.py`** (the tabs); the module
  is `figure_module` there.
* **Everything drawn is on a SCALED page** (round 24): anything counted in
  pixels - the curve's decimation columns, whether a line can go without
  antialiasing - counts DEVICE pixels of the page as shown (`page()[2]`
  times the device pixel ratio), never figure units. Figure text is laid
  out unhinted (`figure_font`) so it spaces evenly at any zoom.
* **A note's move carries its tip** (`_fields_of` is `("x", "y",
  "leader")` for a note) and a marker line's its position (`("vline",
  "x", "y")`), so an undo puts both back. A marker line's text stands ON
  its line: `artist_point` / `set_artist_point` read and write `vline`,
  never the label's own x.
* **A typed number during G on an artist is a distance in the axes'
  units** (x with X, else y on its axis), `PlotWidget._typed_px`.
* **An unframed axis's range is KEPT** (`PlotWidget.kept_fit`),
  keyed by what the fit depends on besides placement (`_fit_signature`:
  the curves on the axis, truncation, units, normalisation, fit margins).
  F (`reset_view`, `fit`) and `rebuild(keep_view=False)` call
  `forget_fit`. Setting an offset without a refresh and then fitting fits
  the OLD traces - go through the undo stack or `refresh()` first.
* **Build a menu, then show it**: `context_menu_for(obj)` builds, and
  `_context_menu` execs. Patching `QMenu.exec` does NOT stop a real modal
  menu here (the test hung until killed); a submenu is kept on its
  parent's wrapper (`menu._paper`).
* **The figure's drawing theme is not always `doc.theme`**
  (`window.drawing_theme`): a page colour of the other family flips the
  ink. `refresh` sets it; the windows keep `doc.theme`.
* **A fit margin is a SHARE OF THE AXIS** (round 27): left 0.1 is the
  first tenth of the x axis empty. It was % of the data's range until
  preferences version 2 / session version 6, which `style.convert_old_fit`
  converts. Read both of an axis's through `PlotWidget.fit_pads`.
* **A white page darkens only the default palette** (`paper_colour`,
  family-wide 2026-10-02): `model.PALETTE` is chosen for a dark ground and
  is brought down to `PAPER_LUMA` on white; any other colour - picked,
  typed as hex, a gradient's - is drawn exactly as chosen. `for_light`
  itself still darkens everything: the handling colours (`ACCENTS`) go
  through it in `set_theme`, so never call it for an object's colour. A
  golden yellow came out olive on an export until then (Christian).
* **The handling colours are not the ink** (`plot.ACCENTS`): the reticle,
  band and selection follow the document's theme even when the page flips
  the ink to the other family. `set_theme(drawing, accent=doc.theme)`.
* **A decorator's page place is a fraction of the HOME frame** (round
  27b): `rel_to_px` / `px_to_rel`, never `rect.left() + x * width`. At
  home (the lock, else the fit: `PlotWidget.home`) that is the old
  arithmetic; zoomed, decorators move with the data. `_home_frames`
  must not be asked while a fit is being worked out (`_fitting`).
* **A hidden curve still holds its labels** (`PlotWidget._ghosts`,
  `_label_trace`, 2026-09-30): a trace is built for a hidden scan that a
  label hangs from, for the labels ONLY - never drawn, picked, fitted or
  exported. Without it the label fell back on its stored x and y (the
  middle of the plot). An attached label's move carries x and y as well
  (`_fields_of`, `_value_of`), for when there is no curve point at all.
* **A label on a curve is NOT placed by x and y** (`TextLabel.attached`:
  `at`, `dx`, `dy`). Its curve point comes from the drawn TRACE, so an
  offset set without a rebuild does not move it yet - go through the
  undo stack or `rebuild()`. `_fields_of` is `("at", "dx", "dy")`.
* **The OFFSETS order a stack; the outliner breaks ties**: S and "Stack
  evenly" keep the order the offsets have (`PlotWidget.stack_order`), and
  only scans on the same offset go by the outliner
  (`Document.outliner_key`: `doc.samples` as listed), its top on top. S
  keeps the LOWEST scan where it is unless T or M holds the top or the
  middle (`spread_anchor`; family-wide, 2026-10-01). The legend lists in
  the outliner's order. Change it only through `set_sample_order` (it re-sorts
  `doc.scans`, which labels in a session are matched by).
* **A spread's held place is where that curve IS when T, B or M is
  pressed** (`spread_anchor` re-reads `floor` and `ceiling` from the
  offsets): nothing moves on the key, only the line, and the step changes
  about the new place from then on. The first version re-applied the step
  from where the place was when S began, as Blender does for a pivot; the
  stack jumped on every key (Christian, 2026-10-01).
* **The spread's line runs through the held CURVE, never its offset**
  (`_spread_line`: the curve's median height). An offset is where the
  curve's zero is, and a pattern may stand far above it: a line at the
  offset ran through the curves below and looked like "y = 0".
* **The plain swipe makes every curve taller IN ITS PLACE**
  (`scale_intensity`, family-wide 2026-10-01; it scaled the axis about
  y = 0 before, which spread the stack apart). Done without multiplying
  data: the axis is scaled about 0 and each offset follows so the curve's
  baseline keeps its place - `profile.baseline` says what a baseline is
  (a pattern's background under its peaks). Read the
  range BEFORE moving the offsets: a fitted range refits to the new stack
  first and every curve jumped. Hidden curves follow too. The offsets ride
  in the swipe's undo step (`_burst_offsets`, `commit_view`), never in
  `view_state`, which a session saves as numbers.
* **Ctrl+T asks nothing** (family-wide, 2026-10-02): on selected curves
  it makes one label each, saying the curve's name, hanging from the
  curve's end at `profile.name_label_corner` (`MainWindow.name_labels`,
  `NAME_CORNERS`, `PlotWidget.end_sample` - the end AS SHOWN, walked in a
  little); on nothing, one free label "Label". A curve whose name already
  hangs from it gets no second; the new labels are selected. The pick
  distance is 8 px built in since the same day (it was 14).
* **P during S keeps the stack's own gaps** (`spread_even`, the
  `profile` of depths below the top when S began): the old swipe's
  proportional spread, about the held place, the typed step their mean.
* **A change is drawn as a DRAFT first** (`PlotWidget.drafting`,
  `_DraftPainter`, `SETTLE_MS`): every curve a hairline, then the full
  drawing once nothing has changed for 150 ms, and while a hand is at work
  (a drag, a pan, a swipe settling, G, S or R). Measured on a 150 % screen
  for ten patterns: a full redraw ~190 ms, a draft ~18 ms. The WIDTH of a
  line is what costs (a 6000-point curve: 159 ms at 1.5 px antialiased,
  46 ms without antialiasing, 4 ms as an antialiased hairline), not the
  antialiasing as such. The cache keys on `drafting()`. Tests draw in full
  (conftest `full_drawings` sets `SETTLE_MS` to 0): they read pixels.
* **A pan is drawn, not slid**: the cached picture of the whole page used
  to be moved under the pointer (frame, numbers and captions with it).
* **Point lists come from numpy** (`_polyline_fast`: the polygon's memory
  filled through `shiboken6.VoidPtr`, as pyqtgraph does), checked once at
  import on three points; `_polyline_slow` if a PySide6 ever differs.
* **Moving several artists goes through `MainWindow._move_artists`**:
  every field an artist keeps its place in (`PlotWidget._fields_of`), and
  a refresh at once. Align wrote x and y only, so a label hanging from a
  curve did not move, and nothing showed until the selection changed.
* **A box selects artists and analysis labels too**, touched by what is
  drawn of them; a label caught with its curve is moved once (G leaves a
  child alone when its parent moves).
* **A colour can FOLLOW another object's** (`Obj.colour_from`,
  `model.sync_colours`, run first in every `refresh`): the follower's own
  `colour` is kept up to date, so everything that reads `.colour` works
  unchanged. Saved as `colour_links` (`[list, position]` pairs). A colour
  chosen by hand, the hex field, the F3 colour and a gradient end the link
  (the gradient puts it back on Revert and in its undo step). `colour_from`
  joins a settings window's snapshot whenever `colour` is in it.
* **A label hanging from its curve is placed by x and y**
  (`ArtistTransform._shown_values`, `_hang_at_typed`): the numbers are those
  of a FREE copy at the same spot, so no unit is converted here (the
  axis may be 2-theta, d or Q); typed, it is hung again from there through
  `set_artist_point`. The old rows (`hang_at`, `hang_dy`, `hang_dx`) still
  exist but are never shown.
* **Given to a curve, a label wears its colour** (`parent_labels` sets
  `colour` "auto", which is "Same as parent" for an owned label).
* **Going EXACT takes the size from the screen**
  (`PlotWidget.exact_from_screen`, `MainWindow.go_exact`, the size
  window's `_mode_changed`): the page as drawn and its margins as they are,
  in the layout's unit - the stored size (8.5 x 6.5 cm) put every text, in
  points, out of all proportion to a figure laid out on a window. The
  blades show on EVERY figure; taking one on a non-exact figure makes it
  exact first (`exact_wanted`, said in orange) - the axes box stays put.
* **`_flash` holds four things** (text, start, seconds, warn): every reader
  takes `[:3]`. `_flash_tick` unpacking all four raised 40 ms after every
  "Saved" - found by the logging test, which only passes after a test that
  flashed (an older order dependence: alone, it fails at 1.1.0 too).
* **A session looks for a moved file BESIDE ITSELF** (`session._read_entry`:
  the path; else its folder and the folders under it, by name; else the
  copy inside, where `profile.EMBED_SOURCES`). A file read from elsewhere
  keeps the SAVED path until the opening is done - scans, labels and regions
  are matched by it - and takes its new path at the end (`relocated`); the
  next save heals the session. The copy is zlib + base64 of the bytes read
  (`Sample.source_bytes`, `_source_copy` caches the text).
* **A region's edges are dragged one at a time** (`region_edge_at`,
  `_edge_drag`): a selected region's anywhere along them, any other's where
  no curve is under the pointer (marking an interval on a curve wins). The
  two limits come back in order on release, one undo step.
* **A sample row is editable (F2) and may have a box**: `_item_changed`
  tells a rename from a tick by comparing the text with `sample.name`
  (`Sample.name` is the title, else the file name).
* **`undo.CallCommand` applies itself when built**: calling the action
  first as well runs it twice (closing a file lost its labels that way).
* **What a page margin HOLDS is `PlotWidget.page_needs`** (round 27e):
  the tightest a margin may go (blades, F3 "Tighten"), and what
  `overflow` checks. `needed_margins` is the AUTOMATIC layout's (with
  breathing room) and only sizes the non-exact figures.
* **A caption's box may hang over the page by its EMPTY rows**
  (`_ink_blank`, round 27f): its letters stop at the page's edge when a
  margin is tight. `page_needs` measures a caption's natural reach, never
  the clamped box, or a too-narrow margin looks wide enough.
* **Decorators follow the zoom only when `doc.follow_zoom`** (off by
  default since round 27f): `rel_to_px` is plain fractions of the axes box
  otherwise. Anything that flips it converts the places
  (`PlotWidget.reframed`), or they jump.
* **A structure's layout is atoms AND bonds' ring centres**: anything
  that moves atoms (`chem.mirrored_layout`) moves `bond["ring"]` too, or
  a ring's inner lines go outside it. Level labels on a turned structure
  are laid out AFTER the turn; `_molecule_layout` and `_paint_molecule`
  must decide the hydrogens' side the same way.
* **Room the program grows is remembered** (`FigureLayout.grown`, per
  side `[before, after]`, in the layout's unit) and given back by
  `_room_for_axes` when the axis leaves; a margin changed by hand drops
  its record. Wrap anything new that can hide a curve in it.
* **Every colour pick goes through `colour.get_colour`** (modal, live,
  Revert gives an invalid QColor); `QColorDialog` is no longer used.

## The family

This panel is one of a family of stacked-trace plotters (Triplot for DSC,
IR-Panel for infrared, PXRD-Panel for powder diffraction), each its own
app on the same
handling. A change to that shared handling goes to EVERY member, and
Christian decides which changes are family-wide: when he says so, or when
a change is plainly about the handling and he confirms it. The standing
rule and the list of members are in his global notes; the log of
family-wide changes is kept there too.

`tests/test_family.py` is the SAME file in every member: a family-wide
change comes with its test there, copied to each. Each member's conftest
provides the one fixture it needs, `stack_window`.

## Running it

```bash
python -m pytest -q                  # a few seconds; the real-file tests skip without the files
python -m pxrdpanel <file.raw>       # from a source checkout
```

A quick look at the real thing, without a window on screen, is
`QT_QPA_PLATFORM=offscreen` plus `window.plot.grab().save(...)`. Text renders
as boxes under the offscreen platform unless it is told where the fonts
are: `QT_QPA_FONTDIR=C:/Windows/Fonts` gives it the real ones (and real
metrics). The test suite runs WITHOUT it, so a test about sizes must hold
for box glyphs too.
