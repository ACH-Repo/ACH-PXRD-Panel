# Changelog

## Unreleased

## 0.2.0 (2026-10-02)

- The swipe (wheel, two-finger drag, middle drag) on SELECTED patterns
  makes only them taller or flatter, each in its place, by a scale factor
  of its own; with nothing selected it still scales every curve. The
  factor is said by an "xN" label above the curve's left end (move,
  restyle, hide or remove it), shown in the outliner, set by number in
  the pattern's settings or F3 "Scale the selected patterns by...", undone
  by "Scale the selected patterns back to x1". The measurements never see
  it.
- On a white page (and in every export) a colour you picked is drawn
  exactly as picked; only the default screen palette is darkened to read
  on paper. A yellow used to come out olive.
- `Ctrl+T` asks nothing: on selected curves it makes a label for each,
  saying its name, hanging from the curve (below its right end); with nothing
  selected, one free label "Label" to retype with a double-click. New
  labels are selected.
- The pick distance is 8 px unless you change it (it was 14).
- "Label the patterns at their left edge" is now "Label every pattern
  on show by its name", placed as `Ctrl+T` places them.
- A figure starts without normalisation. New: all the patterns together,
  0 to 1 - the lowest value of any pattern on show is 0, the highest 1; a
  pattern opened or hidden rescales the rest. In F3 beside "each pattern
  0 to 1", and a tick on the plot's right-click menu.

## 0.1.0 (2026-10-02)

The first version.

- Readers for Bruker `.raw` (RAW1.01 and RAW4.00) and `.brml`, Riet7
  `.dat` and column files, each checked against another export of the same
  measurement; the tube's wavelengths read where a file states them, and
  never made up where it does not.
- An x axis in 2-theta, d or Q, converted at each pattern's own
  wavelength; everything on the figure converted with it and every
  analysis measured again, in one undo step. A d axis runs from large
  spacings to small. Patterns of different wavelengths on one 2-theta axis
  are said, and stamped on exports.
- A wavelength given by hand to a pattern whose file states none, typed as
  a number, an energy or a line (`Cu Ka1`).
- CIFs and ICDD PDF cards simulated at the figure's wavelength, drawn as a
  pattern, as sticks, as a tick row or as dotted lines across the plot at
  the strongest reflections.
- Normalisation per pattern (0 to 1, the start, or to a chosen peak), said
  on the y caption; the stack's arrangement kept across it.
- A drag along a curve: peak position (with its d-spacing), peak area and
  peak width (FWHM), on the intensities as stored; highlight, magnify,
  normalise to the peak, break the axis. `{d}` in a label writes the
  d-spacing.
- Marker lines with `{}` for their position; distance arrows tied to two
  markers; highlighted and magnified regions; labels at the patterns' left
  edges; a broken x axis.
- The handling of the family's newest member, IR-Panel, with everything
  made family-wide so far: stacking, `G`/`S`/`R` (`T`, `B`, `M` and `P`
  during `S`), the swipe that scales each curve in its place, drafts while
  a hand is at work, the F3 operator search, undo for everything including
  the view, the outliner, colours that follow another object's, sessions
  that find moved files and keep copies of them, house style and presets,
  exact figure sizes with blades on every figure, PNG/SVG/CSV exports,
  three themes, the Start Menu entry.
