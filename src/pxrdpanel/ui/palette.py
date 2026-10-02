"""F3: the operator palette.

Blender's command search: type a few letters, see what exists,
press Enter. Its value here is the same as there - the window stays quiet
because the twenty things you do rarely are one keystroke away instead of in
a menu - plus one thing that is specific to a plot: the list is filtered by
what is SELECTED, so "Distribute offsets evenly" appears when three scans are
selected and not when one is.

Disabled operators are listed, greyed, at the bottom rather than hidden. That
is deliberate: the palette is also how somebody finds out what the program
can do, and an empty list teaches nothing. Seeing "Measure the distance
between them" greyed out says what to do next (select two marker lines).
"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHeaderView,
                               QLineEdit, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)


class OperatorPalette(QDialog):
    """Type-to-filter operator list; Enter or double-click runs one."""

    def __init__(self, registry, ctx, parent=None, previous=""):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Operator search")
        self.setModal(True)
        self.registry = registry
        self.ctx = ctx
        self.chosen = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Search operators...")
        self.edit.setClearButtonEnabled(True)
        layout.addWidget(self.edit)
        # A two-column tree rather than a list with a tab in the text: a
        # tab in a QListWidget's label is not a tab stop, so the keys came
        # out ragged, which is exactly the sort of thing that makes a
        # palette feel unfinished.
        self.list = QTreeWidget(self)
        self.list.setColumnCount(2)
        self.list.setHeaderHidden(True)
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setUniformRowHeights(True)
        self.list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.list, 1)
        self.resize(520, 380)
        self.edit.textChanged.connect(self._refill)
        self.edit.returnPressed.connect(self._run_current)
        self.list.itemActivated.connect(lambda _i, _c=0: self._run_current())
        self.list.itemDoubleClicked.connect(
            lambda _i, _c=0: self._run_current())
        # The arrow keys belong to the LIST while the cursor is in the box,
        # which is what makes "type two letters, arrow down, Enter" work.
        self.edit.installEventFilter(self)
        self._refill(previous)
        if previous:
            self.edit.setText(previous)
            self.edit.selectAll()

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up):
                self.list.setFocus()
                self.list.keyPressEvent(event)
                self.edit.setFocus()
                return True
        return QDialog.eventFilter(self, obj, event)

    def _refill(self, text=""):
        self.list.clear()
        for op, enabled in self.registry.search(text, self.ctx):
            item = QTreeWidgetItem(self.list)
            item.setText(0, op.label)
            item.setText(1, op.shortcut or op.key or "")
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setData(0, Qt.UserRole, op.id)
            if not enabled:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        for row in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(row)
            if item.flags() & Qt.ItemIsEnabled:
                self.list.setCurrentItem(item)
                break

    def _run_current(self):
        item = self.list.currentItem()
        if item is None or not (item.flags() & Qt.ItemIsEnabled):
            return
        self.chosen = item.data(0, Qt.UserRole)
        self.accept()


class MeasurePalette(QDialog):
    """What to make of the stretch between the two cursors: an analysis,
    or a highlight, a magnification, a normalisation peak, an axis break.

    The same shape as the operator palette, on purpose: type to filter,
    arrows to walk, Enter to run - a quick-select menu as in Blender, and
    the one the hands already know.
    """

    def __init__(self, models, parent=None, previous=""):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Analyse the interval")
        self.setModal(True)
        self.models = list(models)
        self.chosen = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Peak position, peak area, highlight...")
        self.edit.setClearButtonEnabled(True)
        layout.addWidget(self.edit)
        self.list = QTreeWidget(self)
        self.list.setColumnCount(2)
        self.list.setHeaderHidden(True)
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.list, 1)
        # Small: it opens under the pointer, over the curve being measured,
        # and should cover as little of it as a menu would.
        self.resize(560, 250)
        self.edit.textChanged.connect(self._refill)
        self.edit.returnPressed.connect(self._run_current)
        self.list.itemActivated.connect(lambda _i, _c=0: self._run_current())
        # ONE click chooses, as in a menu. The hand has just let go of a
        # drag; asking it for a double-click on top is one gesture too many.
        self.list.itemClicked.connect(lambda _i, _c=0: self._run_current())
        self.edit.installEventFilter(self)
        self._refill(previous)

    def place_at(self, point):
        """Open with the top left a little above-left of `point`, on screen.

        So the first entry is under the pointer, and a list opened near an
        edge is pulled back rather than half off the screen.
        """
        target = QPoint(point.x() - 24, point.y() - 24)
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            target.setX(max(area.left(), min(target.x(),
                                             area.right() - self.width())))
            target.setY(max(area.top(), min(target.y(),
                                            area.bottom() - self.height() - 30)))
        self.move(target)
        return target

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up):
                self.list.setFocus()
                self.list.keyPressEvent(event)
                self.edit.setFocus()
                return True
        return QDialog.eventFilter(self, obj, event)

    def _refill(self, text=""):
        self.list.clear()
        words = [w for w in (text or "").lower().split() if w]
        for entry in self.models:
            hay = (entry.title + " " + entry.name + " " + entry.note).lower()
            if all(word in hay for word in words):
                item = QTreeWidgetItem(self.list)
                # The TITLE only: this list answers "what is it?", in the
                # word anybody is looking for.
                item.setText(0, entry.title)
                item.setText(1, entry.note)
                item.setData(0, Qt.UserRole, entry.name)
        if self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))

    def _run_current(self):
        item = self.list.currentItem()
        if item is None:
            return
        self.chosen = item.data(0, Qt.UserRole)
        self.accept()

# Qt calls the handlers here by itself; an error in one is logged and
# survived rather than the end of the program (`core/log.py`).
from ..core import log as _log
_log.guard_classes(globals(), __name__)
