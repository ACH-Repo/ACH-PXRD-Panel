"""The outliner: every object in the figure, and what state it is in.

Once patterns can be hidden, dragged anywhere and labelled one at a time, the
plot alone stops being able to say what is in the figure - a hidden pattern
is invisible by definition. So there is a list, as in Blender.

Two parts, with a line between them: the DATA on top - each file, one row,
its box showing or hiding its pattern, and under it what belongs to that
pattern (its analyses, its own labels, its offset marker while the markers
are shown) - and below the line the DECORATORS, everything drawn on the
figure that belongs to no pattern: the legend, free labels and marker
lines, highlighted regions, distance arrows, pictures and structures.

Selection is shared with the plot in both directions: clicking a row selects
the curve, and clicking a curve highlights the row.

A label row can be DRAGGED onto a file (or anything under one) to give the
label to that pattern - parenting - and onto the Decorators to free it
again. The window does the work (`MainWindow.parent_labels`). File rows are
dragged to put the files in another order: the outliner's order is the
stack's.
"""

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QStyle,
                               QStyledItemDelegate, QTreeWidget,
                               QTreeWidgetItem)

from ..core import model
from . import plot as plot_module

_ALARM = QColor(232, 76, 76)
_DIM = QColor(150, 150, 150)

#: The keys of the two rows that are not objects.
SEPARATOR = ("separator", 0)
DECORATORS = ("decorators", 0)


class _Rows(QStyledItemDelegate):
    """Every row as usual, except the separator, which is a line - and the
    row a dragged label would be dropped on, which is framed."""

    def paint(self, painter, option, index):
        if index.data(Qt.UserRole) != SEPARATOR:
            QStyledItemDelegate.paint(self, painter, option, index)
            tree = self.parent()
            target = getattr(tree, "_drop_item", None)
            if (target is not None and index.column() == 0
                    and index == tree.indexFromItem(target, 0)):
                painter.save()
                painter.setPen(QPen(option.palette.color(QPalette.Highlight),
                                    2))
                painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
                painter.restore()
            return
        painter.save()
        painter.setPen(QPen(option.palette.color(QPalette.Mid), 1))
        y = option.rect.center().y()
        painter.drawLine(option.rect.left() + 2, y, option.rect.right() - 6, y)
        painter.restore()


def first_line(text, limit=60):
    """A label's text as one row: its first line, and a mark if there is
    more (a label can run over several lines)."""
    lines = str(text).strip().splitlines() or [""]
    head = lines[0].strip()
    if len(lines) > 1:
        head += " ..."
    return head[:limit]


def decorators(doc):
    """What goes under Decorators, in order: the legend, free labels and
    marker lines, regions, distance arrows, pictures, structures."""
    if doc is None:
        return []
    return ([doc.legend]
            + [label for label in doc.labels if label.scan is None]
            + list(doc.regions) + list(doc.spans)
            + list(doc.images) + list(doc.structures))


class Outliner(QTreeWidget):
    """The data (files, what hangs on each pattern), then a line, then the
    decorators."""

    #: The user ticked or unticked something: (object, visible).
    visibility_changed = Signal(object, bool)
    selection_picked = Signal()
    activated_object = Signal(object)
    menu_for = Signal(object, object)          # object, global QPoint
    #: A sweep across the boxes began / ended: what it changes between the
    #: two is ONE undo step.
    sweep_started = Signal()
    sweep_finished = Signal()
    #: Label rows were dropped: (labels, the scan to give them to, or None
    #: to free them).
    parent_requested = Signal(object, object)
    #: A file's own box: (sample, on) - all its curves shown or hidden.
    file_toggled = Signal(object, bool)
    #: A file renamed in place (F2): (sample, new name; "" for the file's).
    sample_renamed = Signal(object, str)
    #: File rows dragged to a gap: (samples, the index of the gap).
    samples_moved = Signal(object, int)

    def __init__(self, parent=None):
        QTreeWidget.__init__(self, parent)
        self.setColumnCount(2)
        self.setHeaderLabels(["Object", "State"])
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setUniformRowHeights(True)
        self.setIndentation(14)
        # The names take the room there is and the state column is always
        # whole: past a fixed 230-pixel first column it was cut off however
        # wide the dock was.
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setMinimumSectionSize(40)
        self.setItemDelegate(_Rows(self))
        self.headerItem().setToolTip(0, "The figure's objects; tick to show.")
        self.headerItem().setToolTip(1, "What the values are, the offset, "
                                        "analyses shown.")
        self.doc = None
        self._filling = False
        #: While a sweep is live, the state it paints onto every box it
        #: passes (as in Blender), else None.
        self._sweep = None
        # Label rows drag onto scans. The tree never moves its own rows:
        # a drop is a request to the window, and the rows are rebuilt.
        # Copy, not Move: after a MOVE, Qt deletes the dragged rows itself.
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDropIndicatorShown(False)
        #: The row a drag is over and would drop on, framed by `_Rows`.
        self._drop_item = None
        #: Where dragged FILES would land: the index of the gap, drawn as a
        #: thin line between two files.
        self._drop_gap = None
        # F2 renames a file in place; a double-click still opens settings.
        self.setEditTriggers(QAbstractItemView.EditKeyPressed)
        self.itemChanged.connect(self._item_changed)
        self.itemSelectionChanged.connect(self._selection_changed)
        self.itemDoubleClicked.connect(self._double_clicked)
        self.customContextMenuRequested.connect(self._menu)

    # ----------------------------------------------------------------- fill
    def set_document(self, doc):
        self.doc = doc
        self.refill()

    def refill(self):
        """Rebuild the tree. Cheap: a figure has tens of rows, not thousands.

        Never re-entrant: `clear()` deletes the rows, and deleting a row from
        inside a signal that row is emitting is a use-after-free in Qt, not a
        Python error - the program simply disappears.
        """
        doc = self.doc
        if self._filling:
            return
        self._filling = True
        try:
            # A file or the Decorators is open unless it was CLOSED by hand;
            # a scan is closed unless it was opened. "Open unless anything
            # was open before" folded every new file away once the
            # Decorators existed on an empty figure.
            expanded = {self._key(item) for item in self._items()
                        if item.isExpanded()}
            collapsed = {self._key(item) for item in self._items()
                         if item.childCount() and not item.isExpanded()}
            self.clear()
            if doc is None:
                return
            for sample in doc.samples:
                row = QTreeWidgetItem(self)
                self._fill_file_row(row, sample)
                row.setExpanded(("sample", id(sample)) not in collapsed)
            # A file the session could not read keeps a row of its own,
            # after the files that are there: its name, and a right-click
            # that looks for it.
            for gone in doc.missing:
                self._add_missing(gone)
            if doc.samples or doc.missing:
                line = QTreeWidgetItem(self)
                line.setData(0, Qt.UserRole, SEPARATOR)
                line.setFlags(Qt.NoItemFlags)
                line.setFirstColumnSpanned(True)
            group = QTreeWidgetItem(self)
            group.setText(0, "Decorators")
            group.setData(0, Qt.UserRole, DECORATORS)
            font = QFont(self.font())
            font.setBold(True)
            group.setFont(0, font)
            group.setToolTip(0, "Drawn on the figure, belonging to no scan")
            for obj in decorators(doc):
                self._add_decorator(group, obj)
            group.setExpanded(DECORATORS not in collapsed)
            self._drop_item = None
            for item in self._items():
                for column in (0, 1):
                    if item.text(column) and not item.toolTip(column):
                        item.setToolTip(column, item.text(column))
                # Only a label (onto a scan) and a file (into another
                # place in the list) are dragged.
                key = self._key(item)
                if not key or key[0] not in ("label", "sample"):
                    item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled)
        finally:
            self._filling = False

    def _add_missing(self, gone):
        """A file the session could not read (`model.MissingSource`):
        its name in the alarm colour, never selected or dragged."""
        row = QTreeWidgetItem(self)
        row.setText(0, gone.name)
        row.setText(1, "MISSING")
        row.setData(0, Qt.UserRole, ("missing", id(gone)))
        row.setFlags(Qt.ItemIsEnabled)
        font = QFont(self.font())
        font.setBold(True)
        font.setItalic(True)
        row.setFont(0, font)
        row.setForeground(0, QBrush(_ALARM))
        row.setForeground(1, QBrush(_ALARM))
        tip = ("{} is not at {}. Kept with it: {} curve(s), {} analyses, "
               "{} label(s). Right-click to look for it.".format(
                   gone.name, gone.path, len(gone.scans),
                   gone.analysis_count(), len(gone.labels)))
        row.setToolTip(0, tip)
        row.setToolTip(1, tip)
        return row

    def _add_decorator(self, parent, obj):
        """One row under Decorators: a name, and what it is."""
        if isinstance(obj, model.TextLabel):
            return self._add_label_row(parent, obj)
        item = QTreeWidgetItem(parent)
        if isinstance(obj, model.Region):
            name = first_line(obj.shown_text()) or "Region"
            state = "{:.0f}-{:.0f}{}".format(
                obj.hi, obj.lo, " x{:g}".format(obj.factor)
                if obj.magnifies else "")
        elif isinstance(obj, model.SpanArrow):
            a, b = obj.end_values()
            name = "Distance"
            state = "{:.0f}-{:.0f}".format(max(a, b), min(a, b))
        elif isinstance(obj, model.Legend):
            name = obj.name
            state = "{} entries".format(len(obj.entries(self.doc)))
        elif isinstance(obj, model.ImageArtist):
            number = self.doc.images.index(obj) + 1
            name = ("Picture {}".format(number) if len(self.doc.images) > 1
                    else "Picture")
            state = "picture"
        else:                            # a MoleculeArtist
            name, state = (obj.smiles or obj.name), "structure"
        item.setText(0, name)
        item.setText(1, state)
        if isinstance(obj, (model.ImageArtist, model.MoleculeArtist,
                            model.Region, model.SpanArrow)):
            item.setForeground(1, QBrush(_DIM))
        item.setData(0, Qt.UserRole, (obj.kind, id(obj)))
        item.setCheckState(0, Qt.Checked if obj.visible else Qt.Unchecked)
        item.setSelected(obj.selected)
        return item

    def _fill_file_row(self, row, sample):
        """A file's row: its name in its pattern's colour, its box showing
        or hiding the pattern, its wavelength and where the pattern stands,
        and under it what belongs to the pattern."""
        doc = self.doc
        row.setText(0, sample.name)
        tips = ["File: {}".format(sample.path)]
        if sample.head.get("title"):
            tips.append("Title in the file: {}".format(sample.head["title"]))
        row.setToolTip(0, "\n".join(tips))
        flags = row.flags() | Qt.ItemIsEditable
        mine = [s for s in doc.scans if s.sample is sample]
        if mine:
            shown = sum(1 for s in mine if s.visible)
            row.setCheckState(0, Qt.Checked if shown == len(mine)
                              else Qt.Unchecked if not shown
                              else Qt.PartiallyChecked)
        else:
            flags &= ~Qt.ItemIsUserCheckable
        row.setFlags(flags)
        row.setData(0, Qt.UserRole, ("sample", id(sample)))
        font = QFont(self.font())
        font.setBold(True)
        row.setFont(0, font)
        state = [self._unit_state(sample)]
        if sample.wavelength is None:
            row.setForeground(1, QBrush(_DIM))
        scan = mine[0] if mine else None
        if scan is not None:
            # The plot's colour for it, which on the light theme is the
            # same hue darkened to read on white (`plot.for_light`).
            colour = (plot_module.paper_colour(scan.colour)
                      if plot_module.THEME == plot_module.THEME_LIGHT
                      else QColor(scan.colour))
            row.setForeground(0, QBrush(colour))
            missing = scan.missing_for(doc)
            if missing:
                state.insert(0, "NO {}".format(missing.upper()))
                row.setForeground(1, QBrush(_ALARM))
            if scan.offset:
                state.append("{:+.4g}".format(scan.offset))
            if scan.scaled:
                state.append("x{:.3g}".format(float(scan.multiplier)))
            analyses = scan.analysis_objects
            if analyses:
                state.append("{}/{} analyses".format(
                    len(scan.visible_analyses()), len(analyses)))
            row.setSelected(scan.selected)
            for analysis in analyses:
                self._add_analysis(row, analysis)
            for label in doc.labels_for(scan):
                self._add_label_row(row, label)
            if doc.offset_markers:
                self._add_marker_row(row, scan.marker)
        row.setText(1, "  ".join(state))
        return row

    @staticmethod
    def _unit_state(sample):
        """The file's wavelength, short: "1.5406 A", "sim. 1.5406 A" for a
        simulation, "no wavelength" when it states none."""
        if sample.wavelength is None:
            return "no wavelength"
        return "{}{:.5g} A".format("sim. " if sample.simulated else "",
                                   float(sample.wavelength))

    def _add_marker_row(self, parent, marker):
        """The scan's offset marker, while the figure's markers are shown -
        it is only an object then (`Document.objects`)."""
        item = QTreeWidgetItem(parent)
        item.setText(0, marker.name)
        item.setData(0, Qt.UserRole, ("offset_marker", id(marker)))
        item.setCheckState(0, Qt.Checked if marker.visible else Qt.Unchecked)
        item.setText(1, "marker")
        item.setForeground(1, QBrush(_DIM))
        item.setSelected(marker.selected)
        return item

    def _add_label_row(self, parent, label):
        item = QTreeWidgetItem(parent)
        item.setText(0, first_line(label.text))
        item.setData(0, Qt.UserRole, ("label", id(label)))
        item.setCheckState(0, Qt.Checked if label.visible else Qt.Unchecked)
        item.setText(1, "marker line" if label.vline is not None
                     else "note" if label.leader else "label")
        item.setForeground(1, QBrush(_DIM))
        if label.visible and not model.drawn(label):
            # Hidden with its curve: its own tick stays as it was.
            item.setForeground(0, QBrush(_DIM))
            item.setText(1, item.text(1) + ", hidden with its curve")
        item.setSelected(label.selected)
        return item

    def _add_analysis(self, parent, analysis):
        item = QTreeWidgetItem(parent)
        item.setText(0, analysis.summary())
        item.setData(0, Qt.UserRole, ("analysis", id(analysis)))
        item.setCheckState(0, Qt.Checked if analysis.visible else Qt.Unchecked)
        item.setSelected(analysis.selected)
        item.setText(1, analysis.model_name.lower())
        item.setForeground(1, QBrush(_DIM))
        return item

    # --------------------------------------------------- sweeping the boxes
    # Press a box and drag down the list: every box the pointer passes takes
    # the state the first one was given - as in Blender.
    # A tap-and-drag on a touchpad and a double-click-drag both start one.
    def _on_box(self, item, pos):
        """True when `pos` is on the tick box of `item`."""
        if item is None or not (item.flags() & Qt.ItemIsUserCheckable):
            return False
        cell = self.visualItemRect(item)
        left = self.visualRect(self.indexFromItem(item, 0)).left()
        width = self.style().pixelMetric(QStyle.PM_IndicatorWidth) + 8
        return cell.top() <= pos.y() <= cell.bottom() and \
            left - 2 <= pos.x() <= left + width

    def _start_sweep(self, item):
        self._sweep = (Qt.Unchecked if item.checkState(0) == Qt.Checked
                       else Qt.Checked)
        self.sweep_started.emit()
        item.setCheckState(0, self._sweep)

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint()
        item = self.itemAt(pos)
        if ev.button() == Qt.LeftButton and self._on_box(item, pos):
            self._start_sweep(item)
            ev.accept()
            return
        QTreeWidget.mousePressEvent(self, ev)

    def mouseDoubleClickEvent(self, ev):
        pos = ev.position().toPoint()
        item = self.itemAt(pos)
        if ev.button() == Qt.LeftButton and self._on_box(item, pos):
            # The second press of a double-click on a box is a box press,
            # not "open the settings".
            if self._sweep is None:
                self._start_sweep(item)
            ev.accept()
            return
        QTreeWidget.mouseDoubleClickEvent(self, ev)

    def mouseMoveEvent(self, ev):
        if self._sweep is not None:
            item = self.itemAt(ev.position().toPoint())
            if (item is not None and item.flags() & Qt.ItemIsUserCheckable
                    and item.checkState(0) != self._sweep):
                item.setCheckState(0, self._sweep)
            ev.accept()
            return
        QTreeWidget.mouseMoveEvent(self, ev)

    def mouseReleaseEvent(self, ev):
        if self._sweep is not None:
            self._sweep = None
            # After the changes the sweep queued (they are deferred by a
            # zero timer, `_item_changed`), so they land inside the step.
            QTimer.singleShot(0, self.sweep_finished.emit)
            ev.accept()
            return
        QTreeWidget.mouseReleaseEvent(self, ev)

    # ------------------------------------------------ dragging labels over
    def dragged_labels(self):
        """The labels among the selected rows: what a drag carries."""
        out = []
        for item in self.selectedItems():
            obj = self._object(item)
            if isinstance(obj, model.TextLabel):
                out.append(obj)
        return out

    def drop_target(self, item):
        """`(True, scan)` for a row that gives a label to `scan` - the
        scan's own row or anything under it - `(True, None)` for the
        Decorators and what is under them (free the label), and
        `(False, None)` for anywhere else."""
        while item is not None:
            key = self._key(item)
            if key == DECORATORS:
                return True, None
            obj = self._object(item)
            if isinstance(obj, model.Scan):
                return True, obj
            if isinstance(obj, model.Sample) and obj.scans:
                return True, obj.scans[0]
            item = item.parent()
        return False, None

    def drop_labels_on(self, item):
        """Hand the dragged labels to what `item` stands for. True when it
        was somewhere a label can go. The window is told after the drop has
        finished, since it rebuilds these rows."""
        ok, scan = self.drop_target(item)
        labels = self.dragged_labels()
        if not ok or not labels:
            return False
        QTimer.singleShot(0, lambda: self.parent_requested.emit(labels, scan))
        return True

    def _mark_drop(self, item):
        if item is not self._drop_item:
            self._drop_item = item
            self.viewport().update()

    # ------------------------------------------------ dragging files around
    def dragged_samples(self):
        """The files among the selected rows, in the outliner's order."""
        found = self.selected_samples()
        if self.doc is not None:
            found.sort(key=self.doc.samples.index)
        return found

    def _sample_rows(self):
        return [self.topLevelItem(k) for k in range(self.topLevelItemCount())
                if isinstance(self._object(self.topLevelItem(k)),
                              model.Sample)]

    def gap_at(self, y):
        """The gap between files a drop at viewport `y` goes into: 0 above
        the first file, n below the last - by which half of a file's block
        (its row and everything open under it) the pointer is in."""
        rows = self._sample_rows()
        if not rows:
            return None
        tops = [self.visualItemRect(row).top() for row in rows]
        ends = tops[1:] + [self._blocks_end(rows)]
        for k, (top, end) in enumerate(zip(tops, ends)):
            if y < top + (end - top) / 2.0:
                return k
        return len(rows)

    def _blocks_end(self, rows):
        """The bottom of the last file's block: the top of the line below
        the files, or the last visible row of that file."""
        last = rows[-1]
        bottom = self.visualItemRect(last).bottom()
        stack = [last]
        while stack:
            item = stack.pop()
            if item.isExpanded():
                for k in range(item.childCount()):
                    child = item.child(k)
                    bottom = max(bottom, self.visualItemRect(child).bottom())
                    stack.append(child)
        return bottom + 1

    def gap_y(self, gap):
        """Where the line for `gap` is drawn, in viewport pixels."""
        rows = self._sample_rows()
        if not rows or gap is None:
            return None
        if gap >= len(rows):
            return self._blocks_end(rows)
        return self.visualItemRect(rows[gap]).top()

    def _mark_gap(self, gap):
        if gap != self._drop_gap:
            self._drop_gap = gap
            self.viewport().update()

    def paintEvent(self, ev):
        QTreeWidget.paintEvent(self, ev)
        y = self.gap_y(self._drop_gap) if self._drop_gap is not None else None
        if y is None:
            return
        painter = QPainter(self.viewport())
        try:
            painter.setPen(QPen(self.palette().color(QPalette.Highlight), 2))
            painter.drawLine(QPointF(0.0, y), QPointF(
                float(self.viewport().width()), y))
        finally:
            painter.end()

    def drop_samples_at(self, gap):
        """Move the dragged files into `gap`. The window does it, after the
        drop has finished (it rebuilds these rows)."""
        samples = self.dragged_samples()
        if not samples or gap is None:
            return False
        QTimer.singleShot(0, lambda: self.samples_moved.emit(samples, gap))
        return True

    def dragEnterEvent(self, ev):
        if ev.source() is self and (self.dragged_labels()
                                    or self.dragged_samples()):
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
            return
        ev.ignore()

    def dragMoveEvent(self, ev):
        # The base class scrolls near the edges; whether the drop is
        # allowed is decided here.
        QTreeWidget.dragMoveEvent(self, ev)
        if ev.source() is self and self.dragged_samples() and \
                not self.dragged_labels():
            self._mark_drop(None)
            self._mark_gap(self.gap_at(ev.position().y()))
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
            return
        self._mark_gap(None)
        item = self.itemAt(ev.position().toPoint())
        ok, _scan = self.drop_target(item)
        if ev.source() is self and ok and self.dragged_labels():
            self._mark_drop(item)
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
        else:
            self._mark_drop(None)
            ev.ignore()

    def dragLeaveEvent(self, ev):
        self._mark_drop(None)
        self._mark_gap(None)
        QTreeWidget.dragLeaveEvent(self, ev)

    def dropEvent(self, ev):
        """Never the base class's: it would move the rows themselves."""
        item = self.itemAt(ev.position().toPoint())
        gap = self._drop_gap
        self._mark_drop(None)
        self._mark_gap(None)
        if (ev.source() is self and gap is not None
                and not self.dragged_labels()
                and self.drop_samples_at(gap)):
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
            return
        if ev.source() is self and self.drop_labels_on(item):
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
        else:
            ev.ignore()

    # ------------------------------------------------------------- plumbing
    def _items(self):
        out = []

        def walk(item):
            out.append(item)
            for k in range(item.childCount()):
                walk(item.child(k))

        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))
        return out

    @staticmethod
    def _key(item):
        return item.data(0, Qt.UserRole)

    def _object(self, item):
        doc = self.doc
        if doc is None or item is None:
            return None
        key = self._key(item)
        if not key:
            return None
        kind, ident = key[0], key[1]
        if kind in ("separator", "decorators"):
            return None
        if kind == "legend":
            return doc.legend
        if kind in ("region", "span"):
            for obj in (doc.regions if kind == "region" else doc.spans):
                if id(obj) == ident:
                    return obj
        if kind in ("image", "molecule"):
            for obj in (doc.images if kind == "image" else doc.structures):
                if id(obj) == ident:
                    return obj
        if kind == "offset_marker":
            for scan in doc.scans:
                if id(scan.marker) == ident:
                    return scan.marker
        if kind == "scan":
            for scan in doc.scans:
                if id(scan) == ident:
                    return scan
        if kind == "analysis":
            for analysis in doc.analyses():
                if id(analysis) == ident:
                    return analysis
        if kind == "label":
            for label in doc.labels:
                if id(label) == ident:
                    return label
        if kind == "sample":
            for sample in doc.samples:
                if id(sample) == ident:
                    return sample
        if kind == "missing":
            for gone in doc.missing:
                if id(gone) == ident:
                    return gone
        return None

    def sync_selection(self):
        """Follow the document's selection, without echoing it back."""
        self._filling = True
        try:
            for item in self._items():
                obj = self._object(item)
                if isinstance(obj, model.Sample):
                    item.setSelected(any(s.selected for s in obj.scans))
                else:
                    item.setSelected(bool(getattr(obj, "selected", False)))
        finally:
            self._filling = False

    def _item_changed(self, item, column):
        """A box was ticked. Tell the window - but not until Qt has finished.

        **Deferred by a zero timer on purpose.** Acting at once means the
        window rebuilds this tree while Qt is still inside the click that
        ticked the box, so `clear()` deletes the very item the view is
        holding, and the program vanishes without a traceback.

        A zero-delay `singleShot` runs as soon as the event loop is free,
        which is after the click is finished and before anything is drawn.
        """
        if self._filling or column != 0:
            return
        wanted = item.checkState(0) == Qt.Checked
        sample = self._object(item)
        if isinstance(sample, model.Sample):
            self._sample_changed(item, sample)
            return
        obj = self._object(item)
        if obj is None or not hasattr(obj, "visible"):
            return
        if bool(obj.visible) != wanted:
            QTimer.singleShot(0, lambda: self.visibility_changed.emit(
                obj, wanted))

    def _sample_changed(self, item, sample):
        """A file's row changed: its name (F2) or its box."""
        text = item.text(0).strip()
        if text != sample.name:
            name = "" if text in ("", sample.file_name) else text
            QTimer.singleShot(0, lambda: self.sample_renamed.emit(sample,
                                                                  name))
            return
        if not item.flags() & Qt.ItemIsUserCheckable:
            return
        mine = [s for s in self.doc.scans if s.sample is sample]
        state = item.checkState(0)
        if state == Qt.PartiallyChecked or not mine:
            return
        wanted = state == Qt.Checked
        if any(bool(s.visible) != wanted for s in mine):
            QTimer.singleShot(0, lambda: self.file_toggled.emit(sample,
                                                                wanted))

    def rename(self, sample):
        """Start renaming a file in place (F2, or its menu)."""
        for row in self._sample_rows():
            if self._object(row) is sample:
                self.setCurrentItem(row)
                self.editItem(row, 0)
                return row
        return None

    def _selection_changed(self):
        if self._filling or self.doc is None:
            return
        chosen = []
        for item in self.selectedItems():
            obj = self._object(item)
            if isinstance(obj, model.Obj):
                chosen.append(obj)
            elif isinstance(obj, model.Sample):
                chosen.extend(obj.scans)
            elif self._key(item) == DECORATORS:
                chosen.extend(decorators(self.doc))
        self.doc.select_only(chosen)
        self.selection_picked.emit()

    def selected_samples(self):
        """The files whose OWN rows are selected (not a curve under one)."""
        found = []
        for item in self.selectedItems():
            obj = self._object(item)
            if isinstance(obj, model.Sample) and obj not in found:
                found.append(obj)
        return found

    def _double_clicked(self, item, _column):
        obj = self._object(item)
        if obj is not None:
            # Deferred for the same reason as `_item_changed`: the settings
            # dialog is modal, and opening one inside a click that is about
            # to rebuild these rows is the same trap.
            QTimer.singleShot(0, lambda: self.activated_object.emit(obj))

    def _menu(self, pos):
        item = self.itemAt(pos)
        self.menu_for.emit(self._object(item), self.viewport().mapToGlobal(pos))

# Qt calls the handlers here by itself; an error in one is logged and
# survived rather than the end of the program (`core/log.py`).
from ..core import log as _log
_log.guard_classes(globals(), __name__)
