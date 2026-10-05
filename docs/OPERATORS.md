# Operators

Every user-facing action, as registered in `ui/window.py` through
`core/ops.py`. The menus, the keyboard and the F3 palette all read that one
registry, so an action cannot exist in one of them and not in the others.

**Lights up when** is the `enabled` predicate. F3 lists an operator that is
not allowed right now, greyed out rather than hidden, because the palette is
also how somebody finds out what the program can do.

This file is GENERATED. After adding an operator, run:

    python tools/gen_operators.py > docs/OPERATORS.md

## File

| Operator | Key | Lights up when | Note |
| :-- | :-- | :-- | :-- |
| Open patterns... | `Ctrl+O` | always |  |
| New figure | `Ctrl+N` | always |  |
| Open a session... | `Ctrl+Shift+O` | always |  |
| Save the session | `Ctrl+S` | a pattern is open |  |
| Save the session as... | `Ctrl+Shift+S` | a pattern is open |  |
| Export the figure... | `Ctrl+E` | a pattern is open |  |
| Export the curves as CSV... |  | a pattern is open |  |
| Close the pop-up in front, the tab, or the window | `Ctrl+W` | always |  |

## Edit

| Operator | Key | Lights up when | Note |
| :-- | :-- | :-- | :-- |
| Copy the selected labels | `Ctrl+C` | always |  |
| Paste (a picture, a SMILES, text) | `Ctrl+V` | always |  |
| Paste as a text label | `Ctrl+Alt+V` | always |  |
| Undo | `Ctrl+Z` | there is something to undo | also walks back zoom, pan and fit, one gesture at a time |
| Redo | `Ctrl+Y` | there is something to redo |  |
| Figure size and margins... |  | always | exact cm/in and margins, saved with the session |
| Apply a style preset... |  | always |  |
| Save this figure's style as a preset... |  | always |  |
| Open the style presets folder |  | always |  |

## Select

| Operator | Key | Lights up when | Note |
| :-- | :-- | :-- | :-- |
| Select everything | `Ctrl+A` | a pattern is open |  |
| Select nothing | `Alt+A` | something is selected |  |
| Invert the selection | `Ctrl+I` | always |  |
| Select every scan of this sample |  | a pattern is selected |  |
| Select every offset marker |  | the offset markers are shown |  |

## Transform

| Operator | Key | Lights up when | Note |
| :-- | :-- | :-- | :-- |
| Mirror horizontally (left-right) | `Ctrl+Shift+H` | always |  |
| Mirror vertically (top-bottom) | `Ctrl+Shift+V` | always |  |
| Move the selection | `G, then a number, Enter (Esc cancels)` | something is selected | then a number, Enter; Shift is precision, Ctrl snaps |
| Scale the selection | `S, then move or type a factor, Enter (Esc cancels); with only scans selected, spreads them evenly; T, B or M holds the top, the bottom or the middle still; P keeps their own gaps` | something that scales is selected |  |
| Align the artists' left edges | `Ctrl+Shift+Alt+L` | always |  |
| Align the artists' right edges | `Ctrl+Shift+Alt+R` | always |  |
| Align the artists' top edges | `Ctrl+Shift+Alt+T` | always |  |
| Align the artists' bottom edges | `Ctrl+Shift+Alt+B` | always |  |
| Align the artists' centres, side to side | `Ctrl+Shift+Alt+C` | always |  |
| Align the artists' middles, up and down | `Ctrl+Shift+Alt+M` | always |  |
| Space the artists evenly across |  | always |  |
| Space the artists evenly down |  | always |  |
| Stack the selected scans evenly |  | two or more patterns selected |  |
| Distribute the offsets evenly |  | three or more patterns selected |  |
| Align the selection to the active scan |  | two or more patterns selected | closed-form fit to the first selected scan |
| Reset the offsets of the selected scans | `R, with scans selected` | a selected pattern has an offset, or a label or the legend is selected (then R rotates) |  |
| Rotate the selection | `R, with a label or the legend selected` | a label or the legend is selected |  |
| Swap the places of the two selected scans |  | two patterns selected |  |

## Object

| Operator | Key | Lights up when | Note |
| :-- | :-- | :-- | :-- |
| Text bigger | `Ctrl+Up` | always |  |
| Text smaller | `Ctrl+Down` | always |  |
| Bring to front | `Ctrl+Shift+PgUp` | always |  |
| Bring forward | `Ctrl+PgUp` | always |  |
| Send backward | `Ctrl+PgDown` | always |  |
| Send to back | `Ctrl+Shift+PgDown` | always |  |
| Settings for the selection... | `double-click` | something is selected | double-click does the same |
| Hide the selection | `H` | something is selected |  |
| Show everything | `Alt+H` | something is hidden |  |
| Remove the selection | `Del` | something is selected |  |
| Remove every scan of this file |  | a pattern is selected |  |
| Colour for the selection... |  | a pattern is selected |  |
| Colour gradient on the selection... |  | two or more patterns selected |  |
| Show every analysis of the selected scans |  | a selected pattern has an analysis |  |
| Hide every analysis of the selected scans |  | a selected pattern shows an analysis |  |
| Show or hide the y-offset markers |  | a pattern is open |  |
| Scale the selected patterns by... |  | a pattern with a height is selected | or swipe with them selected; an xN label says it |
| Scale the selected patterns back to x1 |  | a selected pattern is scaled |  |
| Say the selected patterns' scale (xN) |  | a selected pattern is scaled |  |
| Give the wavelength of the selected patterns... |  | a pattern is selected | a number, an energy (keV) or a line (Cu Ka1); empty: the file's |
| Draw the selected simulations as: simulated pattern |  | a selected simulation is drawn otherwise |  |
| Draw the selected simulations as: sticks |  | a selected simulation is drawn otherwise |  |
| Draw the selected simulations as: tick row |  | a selected simulation is drawn otherwise |  |
| Draw the selected simulations as: lines across the plot |  | a selected simulation is drawn otherwise | the N strongest reflections, dotted, across the plot |
| Highlight a region... |  | a pattern is open | or drag along a curve and pick Highlight |
| Magnify a region of the selected patterns... |  | a pattern is selected | about the curve's own chord |
| Add a distance arrow... |  | a pattern is open |  |
| Label every pattern on show by its name |  | a pattern is open | placed as Ctrl+T places them |
| Show or hide the legend |  | always | or its tick in the outliner |
| Align labels left | `Ctrl+L` | an analysis, or a pattern with one shown, is selected | the selected labels, else all on the selected scans |
| Align labels right | `Ctrl+R` | an analysis, or a pattern with one shown, is selected |  |
| Align labels centred | `Ctrl+M` | an analysis, or a pattern with one shown, is selected |  |
| Legend settings... |  | always |  |
| Add a label (on curves: their names) | `Ctrl+T` | always | on selected curves: their names, nothing asked |
| Add a marker line... | `Ctrl+B, or right-click the plot` | always | {} in the text is its position |
| Add a note with an arrow... | `Ctrl+Shift+T, or right-click a curve` | always |  |
| Give the selected labels to the selected scan | `Ctrl+P, or drag the label onto the scan in the outliner` | labels and one pattern are selected |  |
| Free the selected labels from their scan | `Alt+P` | a selected label belongs to a pattern |  |

## View

| Operator | Key | Lights up when | Note |
| :-- | :-- | :-- | :-- |
| Fit the page to the window | `Alt+F` | always |  |
| Tighten the page margins |  | the figure has an exact size |  |
| Decorators follow the zoom (on / off) |  | always |  |
| Lock the current framing |  | always |  |
| Unlock the framing (F fits the data) |  | the framing is locked |  |
| Fit the view | `F or Home` | a pattern is open |  |
| Set the x range... | `M` | a pattern is open |  |
| X axis: 2theta (degrees) |  | the x axis is not 2-theta | as measured, each pattern at its own wavelength |
| X axis: d (A) |  | the x axis is not d | at each pattern's wavelength; positions and analyses follow |
| X axis: Q (1/A) |  | the x axis is not Q | at each pattern's wavelength; positions and analyses follow |
| Do not normalise |  | the patterns are normalised |  |
| Normalise all patterns together, 0 to 1 |  | they are not normalised together | lowest of all on show 0, highest 1; also the plot's right-click, Normalise |
| Normalise each pattern 0 to 1 |  | they are not normalised 0 to 1 |  |
| Normalise each pattern to a peak... |  | a pattern is open | the peak dragged is 1, the offsets keep their places |
| Break the x axis... |  | a pattern is open | or drag along a curve and pick Break from the list |
| Join the x axis again |  | the x axis is broken |  |
| Theme: blender-default |  | always |  |
| Theme: light |  | always |  |
| Theme: boombox |  | always |  |
| Show or hide the outliner | `N` | always | the dock on the right |
| X axis: ticks and frame... |  | always |  |
| X axis numbers... |  | always |  |
| X axis caption... |  | always |  |
| Y axis: ticks and frame... |  | always |  |
| Y axis numbers... |  | always |  |
| Y axis caption... |  | always |  |

## Analyse

| Operator | Key | Lights up when | Note |
| :-- | :-- | :-- | :-- |
| Measure on the selected pattern | `C, or drag along a curve` | one pattern is selected | the typed route; double-click-drag a curve is the quick one |
| Analyse the interval... | `Enter, with both cursors down` | both cursors are down |  |
| Stop measuring |  | a measurement is under way |  |
| Measure the distance between them |  | two marker lines are selected | two marker lines selected: the arrow follows them |

## App

| Operator | Key | Lights up when | Note |
| :-- | :-- | :-- | :-- |
| Search operators... | `F3` | always | also the Search button on the menu bar |
| Open the log folder |  | always |  |
| About PXRD-Panel |  | always | Help menu: version, readers, Qt |
| Settings... | `Ctrl+,` | always | sizes, label alignment, pick distance |

