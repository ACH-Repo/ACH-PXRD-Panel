# PXRD-Panel

An interactive panel for stacked powder X-ray diffraction patterns. Drop
patterns on it, stack them into the arrangement you want, lay a phase's
reflections over them, measure the peaks, and export the figure as a PNG
or SVG (or the curves as CSV).

It reads Bruker `.raw` (RAW1.01 and RAW4.00) and `.brml`, Riet7 `.dat`, and
column files (`.xy`, `.xye`, `.xys`, `.txt`, `.csv`, `.asc`), each with its
own reader, checked against another export of the same measurement value
by value (`tests/test_readers.py`). A CIF or an ICDD PDF card (`.xml`) is
simulated into a pattern at the figure's wavelength.

```bash
pip install -e <checkout>    # from a checkout (not on PyPI)
pxrd-panel                   # start it
pxrd-panel sample.raw        # straight into a file
pxrd-panel register          # put it in the Start Menu (optional)
```

Python 3.10 or newer; PySide6 (the Essentials only), numpy, spglib and
RDKit come with it. Version 0.2.0; "PXRD-Panel" is a working title. What
changed is in `CHANGELOG.md`.

## What it does

- **Every pattern is an object** you can select, move, hide, colour and
  right-click. New files arrive stacked under the ones already open.
- **2-theta, d or Q** along x (F3: "X axis: ..."). A pattern is recorded in
  2-theta at its wavelength; d and Q are converted at each pattern's own,
  so patterns from different tubes or a synchrotron compare there. A d
  axis runs from large spacings to small, so a pattern keeps its look.
  Everything placed on the figure - regions, marker lines, arrows, the
  break - is converted with the axis, and every analysis is measured again
  on it. One undo step.
- **The wavelength is read, never assumed.** A `.raw`, a `.brml` and a
  Riet7 `.dat` state their tube's (K-alpha1 is used); a column file states
  none, and its pattern is drawn in 2-theta only - on a d or Q axis it is a
  dashed placeholder that says why - until you give one: in its settings,
  or F3 "Give the wavelength..." for several at once (`1.5406`, `Cu Ka1`,
  `Mo Ka1`, `17 keV`). Patterns of different wavelengths on one 2-theta
  axis are said in orange, and stamped on an export.
- **Normalisation, said on the axis.** None (where a figure starts), all
  the patterns together 0 to 1 (the lowest value of any is 0, the highest
  of any 1, so their heights keep their proportions, and a pattern opened
  or hidden rescales the rest), each pattern 0 to 1, or each pattern's
  chosen peak to the same strength - F3, or a tick on the plot's
  right-click menu for "all together". The y caption says "(normalised)"
  whenever it is on. Changing it keeps the stack's arrangement.
- **The x axis can be broken**: a stretch where nothing happens squeezed to
  a seam marked by slashes.
- **The stack is continuous.** Patterns go wherever you put them (`G`, or
  type a number); `S` spreads the selection evenly. A stack has no y
  numbers or ticks - its offsets make them meaningless - but the y axis's
  settings (double-click it) switch them on.
- **Three themes**: `blender-default` (dark), `light`, `boombox`. Exports
  are always light, whichever is on screen.

## Structures and cards

Drop a CIF or an ICDD card export (`.xml`) and it becomes a pattern too,
simulated at the figure's wavelength over the angles its measured patterns
cover:

- a **CIF** through the structure factors of its whole cell (scattering
  factors, the Lorentz-polarisation factor; when the file has no
  displacement parameters the high-angle intensities are too strong, and
  that is said);
- a **card** from its own d-spacings and observed intensities.

Each can be drawn four ways (its settings, or F3 "Draw the selected
simulations as: ..."): the **simulated pattern**, **sticks** (a line per
reflection, as tall as it is strong), a **tick row** (where, not how
strong), or **lines across the plot** at the strongest reflections, dotted
in its colour. Its settings hold the wavelength it is simulated at, the
2-theta range, the peak width and how many reflections to show.

## Marking and measuring

Press on a curve and drag along it: the stretch between the two crosshairs
is the interval. Let go, and a short list opens under the pointer:

| Entry | What it does |
| :-- | :-- |
| Peak position | the strongest point in the stretch, refined by a parabola through its neighbours, and its d-spacing |
| Peak area | the intensity above a straight baseline between the stretch's ends |
| Peak width (FWHM) | the full width at half height above that same baseline |
| Highlight | shade the stretch across the figure |
| Magnify... | this pattern magnified in the stretch, about its own chord, the factor written over it |
| Normalise to this peak | every pattern's peak here at the same strength |
| Break the x axis here | cut the stretch out of the axis |

The three measurements are made **on the intensities as stored**, never on
the normalised or magnified curve, along the axis as shown (2-theta, d or
Q). A peak at the very edge of its stretch is refused (widen the stretch).
To give the bounds as numbers, select one pattern, press `C`, type two
positions and `Enter`.

Double-clicking an analysis brings its cursors back as gizmos, with its
settings beside them; drag a gizmo and it is recomputed. Its caption -
`{}`, `Area = {}`, `FWHM = {}` by default - is editable text; `{}` is the
result in the house number format and `{d}` the peak's d-spacing:
`(110) {}, *d* = {d}`.

## On the figure

- **Scale factors**: a weak pattern (a glass, a simulation beside counts
  in thousands) can be drawn N times taller: select it and swipe, or type
  the factor in its settings or F3 "Scale the selected patterns by...".
  An "xN" label above its left end says so; it can be moved, hidden or
  removed. The measurements are made on the pattern as stored.
- **Marker lines** (`Ctrl+B`, or right-click the plot): a dashed line
  across the axes at a position, with an upright label on it. The line
  runs under the curves and breaks round its text. `{}` in the text writes
  the marker's position.
- **Distance arrows**: select two marker lines and "Measure the distance
  between them" draws a double arrow labelled with the distance on the
  axis, `\Delta2*\theta* = 0.25\degree`. Its ends follow the markers.
- **Regions**: a highlighted stretch, a magnified one, or both, with its
  own text (several lines). Drag either edge to move that limit alone.
- **Name labels**: `Ctrl+T` on selected patterns hangs each one's name
  below its curve's right end, in its colour, with nothing to type first;
  "Label every pattern on show by its name" does all of them. The labels
  move with their curves; a double-click retypes one.
- Captions (`Ctrl+T` with nothing selected), notes with an arrow
  (`Ctrl+Shift+T`), a legend,
  pasted pictures and skeletal structures from a SMILES.

Every text takes the same markup: `*d*` for italic, `_{a}` and `^{-1}` for
sub- and superscripts, a backslash name for a Greek letter or a symbol
(`\theta`, `\alpha`, `\AA`, `\degree`), and LaTeX between dollars as
matplotlib's mathtext takes it.

## Keys

| Key | What it does |
| :-- | :-- |
| click | select what is under the pointer (`Shift` adds) |
| drag from a curve | **mark an interval**; let go and pick from the list |
| drag from an artist | move it: a label, a marker, a region, an arrow, the legend |
| drag from empty space | box select: curves, labels, markers, any artist |
| double-click | settings for what is under the pointer |
| wheel, two-finger swipe, middle drag | every pattern taller or flatter about its own background, each in its place; with patterns SELECTED, only those, by a scale factor of their own, said by an "xN" label |
| `Shift` + swipe or middle drag | pan |
| pinch, `Ctrl` + wheel | zoom about the pointer |
| `F` / `Home` | fit the view: the x range first, then y |
| `M` | type the x range |
| `G` | grab the selection: move it, or type a number, `Enter` |
| `S` | with patterns selected, spread them evenly, the lowest held still; `T`, `B`, `M` hold the top, the bottom or the middle instead; `P` keeps the stack's own gaps in proportion |
| `R` | reset the selected offsets |
| `C` | measure by typing two positions |
| `Ctrl+T` | label the selected patterns with their names (nothing selected: a free label) |
| `Ctrl+B` | add a marker line |
| `H` / `Alt+H` | hide the selection / show everything |
| `N` | show or hide the outliner |
| `F3` | **operator search**: everything, filtered by what is selected |
| `Ctrl+Z` / `Ctrl+Y` | undo / redo, including zoom, pan and fit |
| `Ctrl+S` / `Ctrl+E` | save the session / export the figure |
| `Ctrl+,` | settings: the house style, for every figure and for this one |

A drag acts on what it starts near: start close to a curve and it marks an
interval, close to a label or a marker and it moves that, anywhere else it
draws a box. A pattern moves with `G` and nothing else. Every operator,
its key and when it is allowed is in `docs/OPERATORS.md`.

## House style, figure size, exports

`Ctrl+,` sets the sizes (axis captions, numbers, labels, marker lines,
regions, distance arrows) and the number formats, in two columns: your
default, kept on this computer, and this figure, saved in its session.

Edit > **Figure size and margins** gives the figure an exact size in
centimetres or inches with four margins, so two figures with the same
settings have identical axes boxes.

| Export | What it is for |
| :-- | :-- |
| PNG / SVG | the figure in the light palette; at an exact size, exactly that size |
| CSV | the curves as drawn, one x/y column pair per pattern |

Sessions (`.pxrdpanel`) keep the files' paths and a compressed copy of each
file, the arrangement, the x axis and every decorator: a file that was
moved is looked for beside the session, and failing that its copy is read.
Style presets (`.pxrdstyle`) keep a figure's look.

## The Start Menu and aliases

Opt-in and reversible, and it writes down what it created:

```bash
pxrd-panel register              # Start Menu entry (add --desktop for one there too)
pxrd-panel register --list       # what is registered
pxrd-panel register --remove     # take it away again
pxrd-panel alias pxp             # your own name for it
```

## Project layout

```
src/pxrdpanel/
  branding.py        every place the program says its own name
  register.py        Start Menu entry, aliases, and the manifest of both
  core/              UI-free: readers, units, model, analyses, undo, session, export
  core/crystal.py    CIFs and cards made into patterns
  core/_molom/       the crystallography, copied verbatim from ACH-MoloM
  ui/                the painted plot, the outliner, the F3 palette, dialogs
docs/OPERATORS.md    every operator, its key and when it lights up
```

## Development

```bash
python -m pytest -q
```

Measurements are not committed. The tests that need real files look for
paths in `PXRD_TESTDATA` or in an uncommitted `tests/local_testdata.txt`,
and skip when there are none. They name a real file by a hash of its file
name (`tests/conftest.py`, `hashed_name`).
