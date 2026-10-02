"""Per-object settings, applied as they are touched.

Live-apply with a snapshot for Cancel, which is the right rule for anything
judged by eye: a dialog you have to close before you can see what it did
makes a knob unusable. Cancel puts back what was there when the dialog
opened, because by then the object has already been changed a dozen times.

`NumberBox` exists because on a German locale Qt's decimal separator is a
comma, so a typed "0.15" is not a number and the box quietly keeps its old
value. Both forms are taken and the typed text is left alone while it is
being typed.
"""

from PySide6.QtCore import QLocale, QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QValidator
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QColorDialog,
                               QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFontComboBox, QFormLayout,
                               QHBoxLayout, QLabel, QLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

#: How much of the screen's height a settings window may take before its
#: rows scroll. Small ones never reach it.
SCREEN_SHARE = 0.85

import copy
import html
import os

from ..core import crystal
from ..core import figure as figure_module
from ..core import labels
from ..core import measure
from ..core import model as units_module
from ..core import numbers
from ..core import readers
from ..core import style
from ..core import units
from .colour import MORE_COLOURS, PLOTTER_COLOURS
from .colour import get_colour as pick_colour
from .numbox import NumberBox, WholeBox


class RangeDialog(QDialog):
    """Two numbers, typed: MestReNova's `M` for the x range.

    Built to be typed through without the mouse. The FIRST number is
    selected when it opens, so typing replaces it; Tab goes to the second,
    selected in turn; Enter takes both. A comma is a decimal point, as in
    `NumberBox`. The two may come in either order, and the dialog does not
    close on a pair that is not a range.

    Built here and shown by the window (`MainWindow.ask_x_range`), so a
    test can fill it without a modal loop.
    """

    def __init__(self, title, unit, low, high, parent=None, y=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(title)
        row = QHBoxLayout()
        self.low_edit = QLineEdit(_number_text(low), self)
        self.high_edit = QLineEdit(_number_text(high), self)
        for edit in (self.low_edit, self.high_edit):
            edit.setAlignment(Qt.AlignRight)
            edit.setMinimumWidth(80)
            edit.textEdited.connect(self._clear_mark)
        if y is not None:
            row.addWidget(QLabel("x", self))
        row.addWidget(self.low_edit)
        row.addWidget(QLabel("to", self))
        row.addWidget(self.high_edit)
        if unit:
            row.addWidget(QLabel(unit, self))
        # The y range, AFTER the x one: Tab reaches it only when wanted,
        # and Enter after the x pair leaves it as it is.
        self.y_low_edit = self.y_high_edit = None
        y_row = None
        if y is not None:
            y_unit, y_low, y_high = y
            y_row = QHBoxLayout()
            self.y_low_edit = QLineEdit(_number_text(y_low), self)
            self.y_high_edit = QLineEdit(_number_text(y_high), self)
            y_row.addWidget(QLabel("y", self))
            for edit in (self.y_low_edit, self.y_high_edit):
                edit.setAlignment(Qt.AlignRight)
                edit.setMinimumWidth(80)
                edit.textEdited.connect(self._clear_mark)
            y_row.addWidget(self.y_low_edit)
            y_row.addWidget(QLabel("to", self))
            y_row.addWidget(self.y_high_edit)
            if y_unit:
                y_row.addWidget(QLabel(y_unit, self))
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # Enter takes the pair from either box, never a button's own idea.
        buttons.button(QDialogButtonBox.Ok).setDefault(True)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        if y_row is not None:
            layout.addLayout(y_row)
        layout.addWidget(buttons)
        self.setTabOrder(self.low_edit, self.high_edit)
        if y_row is not None:
            self.setTabOrder(self.high_edit, self.y_low_edit)
            self.setTabOrder(self.y_low_edit, self.y_high_edit)
        self.low_edit.setFocus(Qt.OtherFocusReason)
        self.low_edit.selectAll()

    @staticmethod
    def _read(edit):
        return numbers.evaluate(edit.text())

    def values(self):
        """`(low, high)` in increasing order, or None if it is not a range."""
        return self._pair(self.low_edit, self.high_edit)

    def y_values(self):
        """The y pair like `values`, or None (no y row, or not a range)."""
        if self.y_low_edit is None:
            return None
        return self._pair(self.y_low_edit, self.y_high_edit)

    def _pair(self, first, second):
        low, high = self._read(first), self._read(second)
        if low is None or high is None or low == high:
            return None
        return (min(low, high), max(low, high))

    def _edits(self):
        return [e for e in (self.low_edit, self.high_edit, self.y_low_edit,
                            self.y_high_edit) if e is not None]

    def _clear_mark(self, _text=""):
        for edit in self._edits():
            edit.setStyleSheet("")

    def accept(self):
        pairs = [((self.low_edit, self.high_edit), self.values())]
        if self.y_low_edit is not None:
            pairs.append(((self.y_low_edit, self.y_high_edit),
                          self.y_values()))
        wrong = False
        for edits, value in pairs:
            if value is None:
                wrong = True
                for edit in edits:
                    edit.setStyleSheet("border: 1px solid #d04040;")
        if not wrong:
            QDialog.accept(self)


class PageSizeDialog(QDialog):
    """The figure's size in numbers: a double-click on a page handle.

    Width and height, in cm or inches. With "Keep the aspect ratio" on (the
    default) typing one fills in the other at the page's present
    proportions, so a figure is made exactly 8.5 cm wide without its shape
    changing; off, the two are free and the ratio follows them. Typed through
    like `RangeDialog`: the width is selected on opening, Tab goes to the
    height, Enter takes them. A comma is a decimal point.

    `least` is the smallest width and height, in cm, the margins leave room
    for: a smaller page is refused (marked red) rather than made.
    """

    def __init__(self, width, height, unit, parent=None, least=(0.0, 0.0)):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Figure size")
        self._unit = unit
        self._ratio = float(width) / max(1e-9, float(height))
        self._least_cm = (float(least[0]), float(least[1]))
        row = QHBoxLayout()
        self.width_edit = QLineEdit(_length_text(width), self)
        self.height_edit = QLineEdit(_length_text(height), self)
        for edit in (self.width_edit, self.height_edit):
            edit.setAlignment(Qt.AlignRight)
            edit.setMinimumWidth(70)
        self.unit_box = QComboBox(self)
        for choice in ("cm", "in"):
            self.unit_box.addItem(choice, choice)
        self.unit_box.setCurrentIndex(0 if unit == "cm" else 1)
        row.addWidget(QLabel("Width", self))
        row.addWidget(self.width_edit)
        row.addWidget(QLabel("x  Height", self))
        row.addWidget(self.height_edit)
        row.addWidget(self.unit_box)
        self.keep = QCheckBox("Keep the aspect ratio", self)
        self.keep.setChecked(True)
        self.keep.setToolTip("Typing one of the two fills in the other at "
                             "the page's present proportions.")
        self.ratio_note = QLabel(self)
        self.ratio_note.setStyleSheet("color: #9a9a9a;")
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setDefault(True)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.keep)
        layout.addWidget(self.ratio_note)
        layout.addWidget(buttons)
        self.width_edit.textEdited.connect(lambda _t: self._typed("width"))
        self.height_edit.textEdited.connect(lambda _t: self._typed("height"))
        self.unit_box.currentIndexChanged.connect(self._unit_changed)
        self.setTabOrder(self.width_edit, self.height_edit)
        self._show_ratio()
        self.width_edit.setFocus(Qt.OtherFocusReason)
        self.width_edit.selectAll()

    @staticmethod
    def _read(edit):
        value = numbers.evaluate(edit.text())
        return value if value is not None and 0 < value < 1e4 else None

    def unit(self):
        return self.unit_box.currentData()

    def values(self):
        """`(width, height, unit)`, or None while either is not a length."""
        width, height = self._read(self.width_edit), self._read(
            self.height_edit)
        if width is None or height is None:
            return None
        return width, height, self.unit()

    def _typed(self, which):
        """One of the two was typed in: with the ratio kept, the other
        follows; without, the ratio does."""
        for edit in (self.width_edit, self.height_edit):
            edit.setStyleSheet("")
        source = self.width_edit if which == "width" else self.height_edit
        other = self.height_edit if which == "width" else self.width_edit
        value = self._read(source)
        if value is None:
            return
        if self.keep.isChecked():
            other.setText(_length_text(value / self._ratio if which == "width"
                                       else value * self._ratio))
        else:
            width, height = (self._read(self.width_edit),
                             self._read(self.height_edit))
            if width and height:
                self._ratio = width / height
        self._show_ratio()

    def _unit_changed(self, _index=0):
        """Both numbers converted, so switching cm and in changes nothing."""
        new = self.unit()
        if new == self._unit:
            return
        factor = 2.54 if new == "cm" else 1.0 / 2.54
        for edit in (self.width_edit, self.height_edit):
            value = self._read(edit)
            if value is not None:
                edit.setText(_length_text(value * factor))
        self._unit = new

    def _show_ratio(self):
        self.ratio_note.setText("Aspect ratio {:.4g} : 1".format(self._ratio))

    def accept(self):
        values = self.values()
        wrong = []
        if values is None:
            wrong = [e for e in (self.width_edit, self.height_edit)
                     if self._read(e) is None]
        else:
            per_cm = 1.0 if values[2] == "cm" else 2.54
            if values[0] * per_cm <= self._least_cm[0]:
                wrong.append(self.width_edit)
            if values[1] * per_cm <= self._least_cm[1]:
                wrong.append(self.height_edit)
        for edit in wrong:
            edit.setStyleSheet("border: 1px solid #d04040;")
        if wrong:
            wrong[0].setToolTip("Smaller than the margins leave room for.")
            return
        QDialog.accept(self)


class PresetSaveDialog(QDialog):
    """Save the figure's look as a style preset: a name, and whether the
    size and margins go with it (`core/presets.py`)."""

    def __init__(self, parent=None, name="", with_layout=True, taken=()):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Save a style preset")
        self._taken = set(n.lower() for n in taken)
        self.name_edit = QLineEdit(name, self)
        self.name_edit.setPlaceholderText("thesis, poster, ACS column...")
        self.with_layout = QCheckBox("With the figure's size and margins",
                                     self)
        self.with_layout.setChecked(bool(with_layout))
        self.with_layout.setToolTip(
            "Then every figure given this preset has the same axes box.")
        self.replaces = QLabel("", self)
        self.replaces.setStyleSheet("color: #d0a040;")
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("", self.with_layout)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.replaces)
        layout.addWidget(buttons)
        self.name_edit.textChanged.connect(self._name_changed)
        self._name_changed()
        self.name_edit.setFocus(Qt.OtherFocusReason)
        self.name_edit.selectAll()

    def name(self):
        return self.name_edit.text().strip()

    def _name_changed(self, _text=""):
        self.replaces.setText("Replaces the preset of that name."
                              if self.name().lower() in self._taken else "")

    def accept(self):
        if not self.name():
            self.name_edit.setStyleSheet("border: 1px solid #d04040;")
            return
        QDialog.accept(self)


def _length_text(value):
    """A length offered for editing: to the hundredth of a millimetre."""
    return "{:.4g}".format(float(value)) if float(value) >= 100 else \
        "{:.3f}".format(float(value)).rstrip("0").rstrip(".")


def _number_text(value):
    """A limit as it should be offered for editing: short, and exact enough."""
    return "{:.6g}".format(float(value))


def readable(root):
    """Every label in `root` can be selected and copied, and every tooltip
    wraps.

    Qt draws a plain-text tooltip on ONE line, however long, which is how
    some of them ran across the whole screen; a rich-text one wraps at a
    readable width. Labels become mouse-selectable only (not by keyboard,
    so Tab still goes from field to field).
    """
    for label in root.findChildren(QLabel):
        label.setTextInteractionFlags(label.textInteractionFlags()
                                      | Qt.TextSelectableByMouse)
    for widget in [root] + root.findChildren(QWidget):
        text = widget.toolTip()
        if text and not text.startswith("<qt>"):
            widget.setToolTip("<qt>{}</qt>".format(html.escape(text)))


def markup_html(text):
    """Figure markup (`*T*_{on}`, `\\Delta`) as rich text, for a preview."""
    from .plot import markup_runs
    out = []
    for run, italic, subscript in markup_runs(text):
        piece = html.escape(run)
        if italic:
            piece = "<i>{}</i>".format(piece)
        if subscript:
            piece = "<sub>{}</sub>".format(piece)
        out.append(piece)
    return "".join(out)


def _plot_of(widget):
    """The plot a dialog's object is drawn on, or None."""
    window = _window_of(widget)
    return getattr(window, "plot", None) if window is not None else None


def arrow_offset_rows(dialog, form):
    """"Arrow Offset" and "Shift by": the distance from the curve to a
    label on an arrow, typed absolutely or moved relatively. The same rows
    for an analysis and for a y-offset marker. Positive is ABOVE the curve,
    as up is more on the y axis; in figure units (px)."""
    dialog.arrow_offset = NumberBox()
    dialog.arrow_shift = NumberBox()
    for box in (dialog.arrow_offset, dialog.arrow_shift):
        box.setDecimals(1)
        box.setRange(-2000.0, 2000.0)
        box.setSingleStep(2.0)
        box.setSuffix(" px")
    dialog.arrow_default = QCheckBox("Default")
    row = QWidget(dialog)
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.addWidget(dialog.arrow_offset, 1)
    line.addWidget(dialog.arrow_default, 0)
    dialog.arrow_offset.setToolTip("Distance from the curve to the label; "
                                   "positive is above.")
    dialog.arrow_default.setToolTip("The automatic distance.")
    dialog.arrow_shift.setToolTip("Adds to the arrow offset (Enter, or the "
                                  "arrows). A group moves together.")
    form.addRow("Arrow Offset", row)
    form.addRow("Shift by", dialog.arrow_shift)


def _window_of(widget):
    """The main window a dialog belongs to (for what only it can do: an
    undo step that recomputes an analysis), or None."""
    while widget is not None:
        if hasattr(widget, "retype_interval"):
            return widget
        widget = widget.parentWidget()
    return None


def enter_stays(dialog, ev):
    """Enter in a field COMMITS it and keeps the window open.

    These windows apply as they are touched, and their values are tuned
    against each other - the arrow's head against its tail, a size against
    a distance. Enter closing the window after every value meant reopening
    it for the next one. A spin box has already taken its value when the
    key reaches the window; this only stops the window
    from treating the same key as its OK button, and selects the field's
    text so the next number can be typed straight over it. A focused
    button still takes Enter. Returns True when the key was handled.
    """
    if ev.key() not in (Qt.Key_Return, Qt.Key_Enter):
        return False
    focus = dialog.focusWidget()
    if isinstance(focus, QPushButton):
        return False
    if isinstance(focus, QAbstractSpinBox):
        focus.interpretText()
        focus.selectAll()
    elif isinstance(focus, QLineEdit):
        focus.selectAll()
    ev.accept()
    return True


class StyleText(QWidget):
    """A number FORMAT (`%.1f`) that is chosen here or follows a default.

    Empty follows: the field then shows what it would be as grey text. A
    format that is not one (text around the number, two numbers) is marked
    and not taken - see `core/numbers.py` for why a unit may not be part of
    it. `value()` is None for "follow", as the object stores it.
    """

    changed = Signal()

    def __init__(self, own, inherited, parent=None, reset_text="Default",
                 describe=None):
        QWidget.__init__(self, parent)
        self._own = numbers.normalise(own)
        self._inherited = inherited
        self._describe = describe
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(self)
        self.edit.setToolTip(
            "Python format: %.1f, %.3g (significant figures). A unit "
            "converts: %.0f \u00b0F.")
        self.reset = QPushButton(reset_text, self)
        row.addWidget(self.edit, 1)
        row.addWidget(self.reset, 0)
        self._show()
        self.edit.textEdited.connect(self._typed)
        self.reset.clicked.connect(lambda _c=False: self._follow())

    def value(self):
        return self._own

    def set_value(self, value):
        self._own = numbers.normalise(value)
        self._show(force=True)
        self.changed.emit()

    def refresh(self):
        self._show()

    def _show(self, force=False):
        inherited = self._inherited() if self._inherited else ""
        shown = ((self._describe(inherited) if self._describe else inherited)
                 or "automatic")
        self.edit.setPlaceholderText("{}  (default)".format(shown))
        # Not while it is being typed in: the text there is the user's.
        if force or not self.edit.hasFocus():
            self.edit.setText(self._own or "")
            self.edit.setStyleSheet("")
        self.reset.setEnabled(self._own is not None)

    def _typed(self, text):
        if not text.strip():
            self.edit.setStyleSheet("")
            self._follow()
            return
        spec = numbers.normalise(text)
        if spec is None:
            self.edit.setStyleSheet("border: 1px solid #d04040;")
            return
        self.edit.setStyleSheet("")
        self._own = spec
        self.reset.setEnabled(True)
        self.changed.emit()

    def _follow(self):
        self._own = None
        self._show(force=True)
        self.changed.emit()


class FontChoice(QWidget):
    """A font FAMILY chosen here, or following a default.

    `value()` is the family, "" for the system's own, or None for "follow".
    """

    changed = Signal()

    def __init__(self, own, inherited, parent=None, reset_text="Default"):
        QWidget.__init__(self, parent)
        self._own = own
        self._inherited = inherited
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.combo = QFontComboBox(self)
        self.reset = QPushButton(reset_text, self)
        row.addWidget(self.combo, 1)
        row.addWidget(self.reset, 0)
        self._show()
        self.combo.currentFontChanged.connect(self._picked)
        self.reset.clicked.connect(lambda _c=False: self._follow())

    def value(self):
        return self._own

    def set_value(self, value):
        self._own = value
        self._show()
        self.changed.emit()

    def refresh(self):
        self._show()

    def _family_shown(self):
        family = self._own if self._own is not None else (
            self._inherited() if self._inherited else "")
        return family or QFont().family()

    def _show(self):
        self.combo.blockSignals(True)
        self.combo.setCurrentFont(QFont(self._family_shown()))
        self.combo.blockSignals(False)
        self.reset.setEnabled(self._own is not None)
        self.combo.setToolTip(
            "Chosen here" if self._own is not None
            else "Following the default: {}".format(self._family_shown()))

    def _picked(self, font):
        self._own = font.family()
        self._show()
        self.changed.emit()

    def _follow(self):
        self._own = None
        self._show()
        self.changed.emit()


class StyleNumber(QWidget):
    """A size that is either chosen HERE or taken from the house style.

    It always shows the value that is DRAWN. While it follows the default the
    box says "(default)" and the button is greyed; typing a number makes the
    size this object's own, and "Default" hands it back to the house style
    (`core/style.py`). `value()` is None for "follow", which is exactly what
    the object stores.
    """

    changed = Signal()

    def __init__(self, setting, own, inherited, parent=None,
                 reset_text="Default"):
        QWidget.__init__(self, parent)
        self.setting = setting
        self._own = own
        #: A callable, so the box follows a default that changes while it is
        #: open - the settings page edits the defaults live.
        self._inherited = inherited
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.box = NumberBox(self)
        self.box.setDecimals(setting.decimals)
        self.box.setRange(float(setting.low), float(setting.high))
        self.box.setSingleStep(float(setting.step))
        self.reset = QPushButton(reset_text, self)
        self.reset.setToolTip("Use the house style (Edit > Settings).")
        row.addWidget(self.box, 1)
        row.addWidget(self.reset, 0)
        self._show()
        self.box.valueChanged.connect(self._typed)
        self.reset.clicked.connect(lambda _c=False: self._follow())

    def value(self):
        return self._own

    def refresh(self):
        """Show the inherited value again - the default under it changed."""
        self._show()

    def _show(self):
        shown = self._own if self._own is not None else self._inherited()
        self.box.blockSignals(True)
        self.box.setSuffix(self.setting.suffix + (
            "" if self._own is not None else "  (default)"))
        self.box.setValue(float(shown))
        self.box.blockSignals(False)
        self.reset.setEnabled(self._own is not None)

    def _typed(self, value):
        self._own = float(value)
        self._show()
        self.changed.emit()

    def _follow(self):
        self._own = None
        self._show()
        self.changed.emit()


class StyleChoice(QWidget):
    """A choice (the label alignment) that may follow the house style.

    The first entry is "default (...)", naming what that currently means, and
    stands for None; the rest are the choices themselves.
    """

    changed = Signal()

    def __init__(self, choices, own, describe_default, parent=None,
                 titles=None, allow_default=True):
        QWidget.__init__(self, parent)
        self._describe = describe_default
        self._allow_default = allow_default
        titles = titles or {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox(self)
        if allow_default:
            self.combo.addItem("", None)
        for choice in choices:
            self.combo.addItem(titles.get(choice, choice), choice)
        row.addWidget(self.combo, 1)
        self.refresh()
        index = self.combo.findData(own) if own is not None else 0
        self.combo.setCurrentIndex(max(0, index))
        self.combo.currentIndexChanged.connect(
            lambda _i=0: self.changed.emit())

    def value(self):
        return self.combo.currentData()

    def set_value(self, value):
        index = self.combo.findData(value)
        if index >= 0:
            self.combo.setCurrentIndex(index)

    def refresh(self):
        if self._allow_default:
            self.combo.setItemText(0, "default ({})".format(self._describe()))


def _document_of(widget):
    """The document a dialog is about, from whichever window it belongs to.

    Needed for the figure's own style, which sits between an object and the
    user's defaults. None outside a window, which skips that level.
    """
    while widget is not None:
        doc = getattr(widget, "doc", None)
        if isinstance(doc, units_module.Document):
            return doc
        widget = widget.parentWidget()
    return None


def _style_number(dialog, obj, attr, reset_text="Default"):
    """The size field for `obj.attr`, following the house style."""
    setting = style.BY_KEY[style.key_for(obj, attr)]
    return StyleNumber(setting, getattr(obj, attr),
                       lambda: style.inherited(dialog.doc, obj, attr),
                       parent=dialog, reset_text=reset_text)


def _style_text(dialog, obj, attr, describe=None):
    """The number-format field for `obj.attr`, following the house style."""
    return StyleText(getattr(obj, attr),
                     lambda: style.inherited(dialog.doc, obj, attr) or "",
                     parent=dialog, describe=describe)


class ArtistTransform(QWidget):
    """The position block every artist gets: where it is, and from which point.

    One widget rather than the same four rows copied into each artist's
    dialog, because the position is a property of being an ARTIST - see
    `core.model.Artist`. `space` decides what the two numbers mean: a
    fraction of the plot, which keeps a caption in its corner whatever the
    view does, or the axes' own units, which pins it to a position. The
    numbers are CONVERTED when the space changes, so switching never moves
    anything.
    """

    def __init__(self, artist, plot, on_change=None, parent=None):
        QWidget.__init__(self, parent)
        self.artist = artist
        self.plot = plot
        self.on_change = on_change
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        self.space = QComboBox()
        self.space.addItem("fraction of the plot (0 to 1)",
                           units_module.SPACE_RELATIVE)
        self.space.addItem("the axes' own units", units_module.SPACE_DATA)
        self.space.setCurrentIndex(
            0 if artist.space == units_module.SPACE_RELATIVE else 1)
        form.addRow("Position in", self.space)

        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        self.at_x = NumberBox()
        self.at_y = NumberBox()
        for box in (self.at_x, self.at_y):
            box.setDecimals(4)
            box.setRange(-1e9, 1e9)
            box.setSingleStep(0.01)
            row_layout.addWidget(box)
        x, y = self._shown_values()
        self.at_x.setValue(x)
        self.at_y.setValue(y)
        self._shown = (self.at_x.value(), self.at_y.value())
        form.addRow("x, y", row)
        self._form, self._place_row = form, row

        self.anchor = QComboBox()
        for name in units_module.ANCHORS:
            self.anchor.addItem(name, name)
        index = self.anchor.findData(artist.anchor)
        self.anchor.setCurrentIndex(max(0, index))
        self.anchor.setToolTip("The point of the object that sits on "
                               "its position.")
        self.rotation = None
        if getattr(artist, "can_rotate", False):
            self.rotation = NumberBox()
            self.rotation.setDecimals(1)
            self.rotation.setRange(-360.0, 360.0)
            self.rotation.setSuffix(" \u00b0")
            self.rotation.setValue(float(getattr(artist, "rotation", 0.0)))
            self.rotation.setToolTip("Counter-clockwise, about the anchor. "
                                     "R turns it by hand.")
            form.addRow("Rotation", self.rotation)
            self.rotation.valueChanged.connect(self._apply)
        self.space.setToolTip("Fraction of the plot, or axis units.")
        self.at_x.setToolTip("Position, in the space above.")
        self.at_y.setToolTip("Position, in the space above.")
        form.addRow("Anchor", self.anchor)

        self.space.currentIndexChanged.connect(self._space_changed)
        self.at_x.valueChanged.connect(self._apply)
        self.at_y.valueChanged.connect(self._apply)
        self.anchor.currentIndexChanged.connect(self._apply)

    def hide_anchor(self):
        """No anchor to choose: a note's text sits on its arrow."""
        label = self._form.labelForField(self.anchor)
        if label is not None:
            label.setVisible(False)
        self.anchor.setVisible(False)

    def _hanging(self):
        """A label that hangs from its curve: x and y are where it IS,
        and typing them hangs it again from there."""
        return (self.plot is not None
                and bool(getattr(self.artist, "attached", False)))

    def _free_twin(self):
        """A free copy of a hanging label, in the space chosen: the numbers
        a free label at the same spot would have (in the axis's own unit,
        whatever that is - the plot converts, not this)."""
        twin = copy.copy(self.artist)
        twin.at = None
        twin.scan = None
        twin.parent_offset = None
        twin.leader = None
        twin.vline = None
        twin.space = self.space.currentData()
        return twin

    def _shown_values(self):
        artist = self.artist
        if not self._hanging():
            return float(artist.x), float(artist.y)
        rect = self.plot.plot_rect()
        px, py = self.plot.artist_point(artist, rect)
        twin = self._free_twin()
        self.plot.set_artist_point(twin, px, py, rect, clamp=False)
        return float(twin.x), float(twin.y)

    def _hang_at_typed(self):
        twin = self._free_twin()
        twin.x, twin.y = float(self.at_x.value()), float(self.at_y.value())
        rect = self.plot.plot_rect()
        px, py = self.plot.artist_point(twin, rect)
        # Hangs from there: the point along its curve and the distance from
        # it follow (`PlotWidget._place_attached`), as a drag does.
        self.plot.set_artist_point(self.artist, px, py, rect, clamp=False)

    def hide_position(self, anchor_too=False):
        """No page position to type: a label hanging from its curve is
        placed by its point and distance instead."""
        for widget in (self.space, self._place_row) + (
                (self.anchor,) if anchor_too else ()):
            label = self._form.labelForField(widget)
            if label is not None:
                label.setVisible(False)
            widget.setVisible(False)

    def _space_changed(self, _index=0):
        """Convert the stored position so the artist does not jump."""
        wanted = self.space.currentData()
        if wanted != self.artist.space and self.plot is not None:
            self.plot.convert_artist_space(self.artist, wanted)
        self.at_x.blockSignals(True)
        self.at_y.blockSignals(True)
        x, y = self._shown_values()
        self.at_x.setValue(x)
        self.at_y.setValue(y)
        self._shown = (self.at_x.value(), self.at_y.value())
        self.at_x.blockSignals(False)
        self.at_y.blockSignals(False)
        self._apply()

    def _apply(self, *_args):
        self.artist.space = self.space.currentData()
        self.artist.anchor = self.anchor.currentData()
        typed = (self.at_x.value(), self.at_y.value())
        if self._hanging():
            if typed != self._shown:
                self._hang_at_typed()
                self._shown = typed
        else:
            self.artist.x = float(typed[0])
            self.artist.y = float(typed[1])
        if self.rotation is not None:
            self.artist.rotation = float(self.rotation.value())
        if self.on_change is not None:
            self.on_change()


def screen_limit(widget):
    """The tallest a settings window may be on the screen it is on, in
    pixels, or None when there is no screen to ask."""
    screen = widget.screen() if hasattr(widget, "screen") else None
    if screen is None:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    return int(screen.availableGeometry().height() * SCREEN_SHARE)


class _LiveDialog(QDialog):
    """Common machinery: snapshot on open, restore on reject.

    One dialog can edit SEVERAL objects of its kind at once (`set_group`:
    select three peak positions, open the settings, set the size of all
    three). It
    shows the object it was opened on and MIRRORS every change onto the
    others: whatever field of the shown object just
    changed is copied to the rest, and nothing else is touched, so their
    own values of every other field stay theirs. Fields that belong to one
    object alone (`INDIVIDUAL`: a label's text, a position) are not mirrored
    and their widgets (`GROUP_DISABLED`) are greyed out.
    """

    #: Attribute names this dialog edits, for the snapshot.
    FIELDS = ()
    #: Fields that are one object's own, never set for a group.
    INDIVIDUAL = ()
    #: Widgets (attribute names on the dialog, dotted for a nested one)
    #: greyed out while a group is edited.
    GROUP_DISABLED = ()
    #: True for an object with a place in the figure's stack: its window
    #: gets a Layer field (`_layer_row`), and its z is in FIELDS.
    LAYERED = False

    def __init__(self, parent, obj, on_change=None):
        QDialog.__init__(self, parent)
        if self.LAYERED and "z" not in type(self).FIELDS:
            self.FIELDS = tuple(type(self).FIELDS) + ("z",)
        # A colour that follows another's (`colour_from`) reverts and
        # undoes with the colour.
        if "colour" in self.FIELDS and "colour_from" not in self.FIELDS:
            self.FIELDS = tuple(self.FIELDS) + ("colour_from",)
        self.obj = obj
        self.on_change = on_change
        self.doc = _document_of(parent)
        self._snapshot = {name: getattr(obj, name) for name in self.FIELDS}
        #: The OTHER objects edited along with `obj`, and what they were.
        self.group = []
        self._group_snapshots = []
        self._last = dict(self._snapshot)
        # NOT MODAL. These dialogs apply as they are touched and are meant to
        # be worked beside: a dialog that blocks the plot blocks the
        # measurement cursors it is describing, which is exactly what
        # goes wrong when adjusting an analysis. `Qt.Tool` keeps it above
        # the window it belongs to without taking the focus away from it.
        self.setModal(False)
        self.setWindowFlag(Qt.Tool, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

    def set_group(self, others):
        """Edit `others` along with the object this was opened on."""
        self.group = [o for o in others if o is not self.obj]
        self._group_snapshots = [
            (o, {name: getattr(o, name) for name in self.FIELDS})
            for o in self.group]
        self._last = {name: getattr(self.obj, name) for name in self.FIELDS}
        for path in self.GROUP_DISABLED:
            widget = self
            for part in path.split("."):
                widget = getattr(widget, part, None)
            if widget is not None:
                widget.setEnabled(False)
                widget.setToolTip("Per object: not set for a group.")
        if self.group:
            self.setWindowTitle("{}  (and {} more)".format(
                self.windowTitle(), len(self.group)))
        return self

    def _mirror(self):
        """Copy what just changed on the shown object to the rest."""
        for name in self.FIELDS:
            value = getattr(self.obj, name)
            if value == self._last.get(name):
                continue
            self._last[name] = value
            if name in self.INDIVIDUAL:
                continue
            for other in self.group:
                setattr(other, name, value)

    def _live(self, *_args):
        if self.group:
            self._mirror()
        if self.on_change is not None:
            self.on_change()

    def keyPressEvent(self, ev):
        if enter_stays(self, ev):
            return
        QDialog.keyPressEvent(self, ev)

    def showEvent(self, ev):
        readable(self)
        QDialog.showEvent(self, ev)
        self.fit()

    def fit(self):
        """Grow to what the rows need at this width. A wrapped label that
        is filled in after the window was sized - an analysis's results, a
        problem under its label - was cut off. Never past `SCREEN_SHARE` of
        the screen's height: beyond that the rows scroll
        (`_scroll_rows`)."""
        layout = self.layout()
        if layout is None:
            return
        if getattr(self, "_rows_area", None) is not None:
            self._fit_scrolled()
            return
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.activate()
        needed = (layout.totalHeightForWidth(self.width())
                  if layout.hasHeightForWidth()
                  else self.sizeHint().height())
        limit = screen_limit(self)
        if limit is not None and needed > limit:
            self._scroll_rows()
            self._fit_scrolled()
            return
        if needed > self.height():
            self.resize(self.width(), needed)

    def _scroll_rows(self):
        """Put the rows in a scroll area, keeping the buttons (the last
        row, when it is a button box) below it, always in view."""
        layout = self.layout()
        buttons = None
        last = layout.itemAt(layout.count() - 1) if layout.count() else None
        if last is not None and isinstance(last.widget(), QDialogButtonBox):
            buttons = last.widget()
            layout.removeWidget(buttons)
        inner = QWidget()
        # Takes the layout, and its widgets, off this dialog.
        inner.setLayout(layout)
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(area, 1)
        if buttons is not None:
            row = QHBoxLayout()
            row.setContentsMargins(9, 0, 9, 9)
            row.addWidget(buttons)
            outer.addLayout(row)
        self._rows_area = area
        self._rows_inner = inner
        # `fit` had pinned this window's minimum to the rows' full height
        # (SetMinimumSize on the layout now inside the area): let it go.
        self.setMinimumSize(0, 0)

    def _fit_scrolled(self):
        limit = screen_limit(self) or 600
        inner = self._rows_inner
        needed = inner.sizeHint().height() + 60
        bar = self._rows_area.verticalScrollBar().sizeHint().width()
        width = max(self.width(), inner.sizeHint().width() + bar + 4)
        self.resize(width, min(limit, needed))

    def reject(self):
        """Closed without OK - its X, Esc, Ctrl+W: KEEP what was changed.

        These apply as they are touched, and a change is a change.
        Putting everything back on closing would, after a minute of
        adjusting by eye, be exactly the loss nobody expects. Only the
        Revert button puts things back (`revert`); either way an undo step
        is made, so Ctrl+Z still takes the whole dialog back afterwards.
        """
        self.accept()

    def revert(self):
        """The Revert button: put back what the objects were when this
        opened."""
        for name, value in self._snapshot.items():
            setattr(self.obj, name, value)
        for other, saved in self._group_snapshots:
            for name, value in saved.items():
                setattr(other, name, value)
        self._last = dict(self._snapshot)
        if self.on_change is not None:
            self.on_change()
        QDialog.reject(self)

    def _layer_row(self, form):
        """Layer: where the object is drawn in the stack, as a number -
        the same order Ctrl+PgUp and Ctrl+PgDown move it in."""
        self.layer = NumberBox()
        self.layer.setDecimals(1)
        self.layer.setRange(-10000.0, 10000.0)
        self.layer.setSingleStep(1.0)
        self.layer.setValue(float(units_module.z_of(self.obj)))
        self.layer.setToolTip("Higher is drawn on top. Ctrl+PgUp / PgDn "
                              "move it one place.")
        form.addRow("Layer", self.layer)
        self.layer.valueChanged.connect(self._set_layer)

    def _set_layer(self, value):
        self.obj.z = float(value)
        self._live()

    def _buttons(self):
        """OK and Revert. OK closes; Revert undoes this dialog and closes.

        Also where a window for an object in the stack gets its Layer row,
        at the end of its form: every such window builds its buttons last."""
        if self.LAYERED and not hasattr(self, "layer"):
            forms = self.findChildren(QFormLayout)
            if forms:
                self._layer_row(forms[0])
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        revert = buttons.button(QDialogButtonBox.Cancel)
        revert.setText("Revert")
        revert.setToolTip("Undo this window's changes and close. "
                          "Closing otherwise keeps them.")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.revert)
        return buttons

    def snapshot(self):
        """What the object looked like when this opened, for the undo step."""
        return dict(self._snapshot)

    def snapshots(self):
        """`[(obj, {field: value when opened}), ...]` for every object this
        dialog edits, the shown one first."""
        return ([(self.obj, dict(self._snapshot))]
                + [(o, dict(saved)) for o, saved in self._group_snapshots])


class _HexBox(QLineEdit):
    """A colour as #rrggbb, to copy and paste between windows: a click
    selects all of it."""

    def focusInEvent(self, ev):
        QLineEdit.focusInEvent(self, ev)
        QTimer.singleShot(0, self.selectAll)


def _donor_name(obj):
    if hasattr(obj, "display_name"):
        return obj.display_name()
    text = getattr(obj, "text", None) or getattr(obj, "name", "")
    return "\"{}\"".format(text) if text else type(obj).__name__.lower()


def _colour_button(parent, get_colour, set_colour):
    """A colour row of a settings window: the swatch (the picker, dropper
    and all); the colour as #rrggbb right beside it, to copy, or to paste
    one in and press Enter, without opening anything; and, for the
    object's own colour, INHERIT - the next click on the figure names an
    object whose colour this one then FOLLOWS (`model.sync_colours`,
    saved with the session). Choosing a colour of its own ends that."""
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    button = QPushButton(row)
    button.setFixedWidth(64)
    hex_box = _HexBox(row)
    hex_box.setFixedWidth(84)
    hex_box.setToolTip("The colour as #rrggbb: copy it, or paste one in "
                       "and press Enter.")
    obj = getattr(parent, "obj", None)
    # Only the object's OWN colour follows (not, say, a note's arrow).
    follows = (obj is not None and hasattr(obj, "colour")
               and getattr(parent, "_set_colour", None) == set_colour)
    inherit = QPushButton("Inherit...", row) if follows else None
    layout.addWidget(button)
    layout.addWidget(hex_box)
    if inherit is not None:
        layout.addWidget(inherit)
    layout.addStretch(1)
    # core/model.py, imported here under that name
    doc_model = units_module

    def refresh():
        colour = QColor(get_colour())
        button.setStyleSheet(
            "background: {}; border: 1px solid #555;".format(colour.name()))
        if not hex_box.hasFocus():
            hex_box.setText(colour.name())
        if inherit is not None:
            donor = getattr(obj, "colour_from", None)
            inherit.setText("Inherits" if donor is not None
                            else "Inherit...")
            inherit.setToolTip(
                "Follows the colour of {} - click to stop following."
                .format(_donor_name(donor)) if donor is not None else
                "Click, then click the object on the figure whose colour "
                "this one follows from now on.")

    def own(name):
        """A colour of its own: no longer another's."""
        if follows:
            obj.colour_from = None
        set_colour(name)

    def live(name):
        own(name)
        refresh()

    def pick():
        # Live: the figure follows the wheel. Revert puts the colour back,
        # and a "Follow the theme" / "Same as scan" that was ticked.
        auto = getattr(parent, "auto", None)
        was_auto = auto is not None and auto.isChecked()
        colour = pick_colour(QColor(get_colour()), parent, "Pick a colour",
                             live=live)
        if colour.isValid():
            own(colour.name())
        elif was_auto:
            auto.setChecked(True)
        refresh()

    def typed():
        text = hex_box.text().strip()
        if text and not text.startswith("#"):
            text = "#" + text
        colour = QColor(text)
        if colour.isValid() and len(text) in (4, 7) and \
                colour.name() != QColor(get_colour()).name():
            own(colour.name())
        refresh()

    def inherit_clicked():
        if getattr(obj, "colour_from", None) is not None:
            obj.colour_from = None          # keeps the colour it has
            parent._live()
            refresh()
            return
        plot = getattr(parent.parentWidget(), "plot", None)
        if plot is None:
            return

        def chosen(donor):
            if (donor is None or donor is obj or not hasattr(donor, "colour")
                    or doc_model.inherits_from(donor, obj)):
                return
            obj.colour_from = donor
            colour = doc_model.own_colour(donor)
            if colour is not None:
                set_colour(colour)
            else:
                parent._live()
            refresh()

        plot.pick_object(chosen, "INHERIT a colour: click the object it "
                                 "follows - Esc gives up")

    button.clicked.connect(lambda _c=False: pick())
    hex_box.editingFinished.connect(typed)
    if inherit is not None:
        inherit.clicked.connect(lambda _c=False: inherit_clicked())
    refresh()
    row.swatch, row.hex, row.inherit = button, hex_box, inherit
    return row


class ScanSettings(_LiveDialog):
    """Everything about one pattern on the figure, and its file's
    wavelength (and, for a CIF or a card, how it is simulated and drawn)."""

    FIELDS = ("colour", "label", "offset", "line_width", "keep", "draw_as",
              "strongest", "multiplier")
    INDIVIDUAL = ("label", "offset", "multiplier")
    GROUP_DISABLED = ("label", "offset", "multiplier", "source.wavelength",
                      "source.sim_low", "source.sim_high", "source.fwhm",
                      "analyses")

    def __init__(self, parent, scan, unit, on_change=None):
        _LiveDialog.__init__(self, parent, scan, on_change)
        self.setWindowTitle("Settings for {}".format(scan.display_name()))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.label = QLineEdit(scan.label or "")
        self.label.setPlaceholderText(scan.display_name())
        self.label.setToolTip("Name in the legend. Empty: the file's name.")
        form.addRow("Label", self.label)

        form.addRow("Colour", _colour_button(
            self, lambda: scan.colour, self._set_colour))

        self.offset = NumberBox()
        self.offset.setDecimals(6)
        self.offset.setRange(-1e9, 1e9)
        self.offset.setValue(float(scan.offset))
        self.offset.setSuffix(" " + unit)
        self.offset.setToolTip("Vertical offset, in the y unit.")
        form.addRow("Offset", self.offset)

        self.multiplier = NumberBox()
        self.multiplier.setDecimals(3)
        self.multiplier.setRange(0.001, 100000.0)
        self.multiplier.setPrefix("x ")
        self.multiplier.setValue(float(scan.multiplier))
        self.multiplier.setToolTip(
            "How many times taller than measured it is drawn, in its "
            "place; 1 is as measured. The measurements never see it. Its "
            "label (\"x15\") can be moved, hidden or taken away.")
        form.addRow("Scale", self.multiplier)
        self._labels_before = (list(self.doc.labels)
                               if self.doc is not None else None)

        self.line_width = _style_number(self, scan, "line_width")
        form.addRow("Line width", self.line_width)

        # Hide the ends by POSITION along the curve: a share of its points.
        cut = QWidget(self)
        cut_row = QHBoxLayout(cut)
        cut_row.setContentsMargins(0, 0, 0, 0)
        self.cut_start = NumberBox()
        self.cut_end = NumberBox()
        for box, value in ((self.cut_start, scan.keep[0]),
                           (self.cut_end, 1.0 - scan.keep[1])):
            box.setDecimals(1)
            box.setRange(0.0, 49.0)
            box.setSingleStep(0.5)
            box.setSuffix(" %")
            box.setValue(round(100.0 * float(value), 1))
        cut_row.addWidget(QLabel("low end"))
        cut_row.addWidget(self.cut_start, 1)
        cut_row.addWidget(QLabel("high end"))
        cut_row.addWidget(self.cut_end, 1)
        cut.setToolTip("Hide the pattern's ends, by share of its points "
                       "(low and high angle). Hidden ends are left out of "
                       "fits, analyses and exports.")
        form.addRow("Hide", cut)
        self.cut_note = QLabel("")
        self.cut_note.setStyleSheet("color: #9a9a9a;")
        form.addRow("", self.cut_note)
        self._describe_cut()

        # The analyses measured on it, one box each; double-click opens an
        # analysis's own settings.
        self.analyses = QListWidget(self)
        self.analyses.setMaximumHeight(120)
        self.analyses.setToolTip("Tick to show. Double-click for settings.")
        for analysis in scan.analysis_objects:
            entry = QListWidgetItem(self._analysis_text(analysis))
            entry.setFlags(entry.flags() | Qt.ItemIsUserCheckable)
            entry.setCheckState(Qt.Checked if analysis.visible
                                else Qt.Unchecked)
            entry.setData(Qt.UserRole, analysis)
            self.analyses.addItem(entry)
        if not scan.analysis_objects:
            self.analyses.addItem("none yet: drag along the curve")
            self.analyses.setEnabled(False)
        form.addRow("Analyses", self.analyses)

        # ------------------------------------------------------ the file
        sample = scan.sample
        form.addRow("File", QLabel(sample.path))
        self.source = PatternRows(form, sample, scan, self)
        _head_rows(form, sample)

        buttons = self._buttons()
        layout.addWidget(buttons)

        self.label.textChanged.connect(self._apply)
        self.offset.valueChanged.connect(self._apply)
        self.multiplier.valueChanged.connect(self._apply)
        self.line_width.changed.connect(self._apply)
        self.cut_start.valueChanged.connect(self._apply)
        self.cut_end.valueChanged.connect(self._apply)
        self.source.changed.connect(self._apply)
        self.analyses.itemChanged.connect(self._analysis_ticked)
        self.analyses.itemDoubleClicked.connect(self._edit_analysis)
        self.resize(480, self.sizeHint().height())

    def _describe_cut(self):
        """Where the drawn part of the pattern now starts and ends."""
        x = self.obj.x_values()
        if x is None or not len(x):
            self.cut_note.setText("")
            return
        k0, k1 = self.obj.kept_range(len(x))
        ends = sorted((float(x[k0]), float(x[k1 - 1])))
        self.cut_note.setText("drawn from {:.4g} to {:.4g} {} ({} of {} "
                              "points)".format(ends[0], ends[1],
                                               self.obj.doc.x_unit
                                               if self.obj.doc else "",
                                               k1 - k0, len(x)))

    @staticmethod
    def _analysis_text(analysis):
        return analysis.summary()

    def _analysis_ticked(self, item):
        analysis = item.data(Qt.UserRole)
        if analysis is None:
            return
        analysis.visible = item.checkState() == Qt.Checked
        self._live()

    def _edit_analysis(self, item):
        """Open one analysis's own settings from this list."""
        analysis = item.data(Qt.UserRole)
        if analysis is None:
            return
        dialog = AnalysisSettings(self, analysis, on_change=self.on_change)
        self._analysis_dialog = dialog
        dialog.finished.connect(
            lambda _result, it=item, an=analysis: self._analysis_edited(it, an))
        dialog.show()
        dialog.raise_()

    def _analysis_edited(self, item, analysis):
        item.setText(self._analysis_text(analysis))
        item.setCheckState(Qt.Checked if analysis.visible else Qt.Unchecked)
        self._live()

    def _set_colour(self, name):
        self.obj.colour = name
        self._live()

    def _apply(self, *_args):
        scan = self.obj
        scan.label = self.label.text().strip() or None
        scan.offset = float(self.offset.value())
        scan.line_width = self.line_width.value()
        scan.keep = (round(self.cut_start.value() / 100.0, 6),
                     round(1.0 - self.cut_end.value() / 100.0, 6))
        self._apply_multiplier(scan)
        self.source.apply_scan(scan)
        if not self.group:
            self.source.apply_sample(scan.sample)
        self._describe_cut()
        scan._cache_key = None
        self._live()

    def _apply_multiplier(self, scan):
        """A typed scale: the pattern that factor taller IN ITS PLACE (the
        offset box follows), its "xN" label made when it leaves x1."""
        value = float(self.multiplier.value())
        if self.group or abs(value - float(scan.multiplier)) < 1e-12:
            return
        doc = self.doc
        was_scaled = scan.scaled
        if doc is not None:
            for obj, attr, new in doc.rescaled(scan, value):
                setattr(obj, attr, new)
        else:
            scan.multiplier = value
        self.offset.blockSignals(True)
        self.offset.setValue(float(scan.offset))
        self.offset.blockSignals(False)
        window = _window_of(self)
        if (not was_scaled and scan.scaled and window is not None
                and doc is not None):
            doc.labels.extend(window._new_multiplier_labels([scan]))

    def snapshots(self):
        """The pattern, the others of a group, its FILE's wavelength and
        simulation - the fields here that belong to the sample - and the
        figure's labels, which a scale may have added one to."""
        extra = []
        if self.doc is not None and self._labels_before is not None:
            extra.append((self.doc, {"labels": list(self._labels_before)}))
        return (_LiveDialog.snapshots(self)
                + [(self.obj.sample, dict(self.source.saved))] + extra)

    def revert(self):
        self.source.restore(self.obj.sample)
        if self.doc is not None and self._labels_before is not None:
            self.doc.labels[:] = self._labels_before
        self.obj._cache_key = None
        _LiveDialog.revert(self)


#: What the sample's own fields are, for a snapshot and a revert.
SAMPLE_FIELDS = ("wavelength_override", "sim_range", "sim_fwhm")


def _head_rows(form, sample):
    """What else the file says about itself, one row each."""
    for key, words in (("title", "Title in the file"), ("date", "Date"),
                       ("formula", "Formula"), ("card", "Card"),
                       ("radiation", "Card's radiation"),
                       ("card_lambda", "Card's wavelength (A)"),
                       ("note", "Note")):
        if sample.head.get(key):
            label = QLabel(str(sample.head[key]))
            label.setWordWrap(True)
            form.addRow(words, label)


class PatternRows(QObject):
    """The rows about a pattern's file in a settings window: its
    wavelength, and for a CIF or a card how it is simulated (the 2-theta
    range, the peak width) and - with its scan - how it is drawn (a profile,
    sticks, a tick row, lines across the plot) and how many reflections.

    The wavelength is typed: a number in angstrom, an energy ("17 keV") or
    a line ("Cu Ka1"); empty is the file's. What it is, and where it came
    from, is said under it - and that the file states none, when it does
    not."""

    changed = Signal()

    def __init__(self, form, sample, scan=None, parent=None):
        QObject.__init__(self, parent)
        self.sample = sample
        self.saved = dict((name, getattr(sample, name))
                          for name in SAMPLE_FIELDS)
        simulated = sample.simulated
        self.wavelength = QLineEdit()
        if simulated:
            self.wavelength.setText("{:.6g}".format(sample.wavelength))
            self.wavelength.setToolTip(
                "The wavelength it is simulated at: a number in angstrom, "
                "an energy (17 keV) or a line (Cu Ka1, Mo Ka1). The "
                "figure's when it was opened.")
        else:
            if sample.wavelength_override:
                self.wavelength.setText("{:.6g}".format(
                    sample.wavelength_override))
            self.wavelength.setPlaceholderText(
                crystal.describe_wavelength(sample.file_wavelength)
                + " (the file)" if sample.file_wavelength
                else "not stated in the file")
            self.wavelength.setToolTip(
                "K-alpha1, or the one wavelength: a number in angstrom, an "
                "energy (17 keV) or a line (Cu Ka1, Mo Ka1). Empty: the "
                "file's. d and Q need one; it is never assumed.")
        form.addRow("Simulated at" if simulated else "Wavelength",
                    self.wavelength)
        self.wavelength_note = QLabel("")
        self.wavelength_note.setWordWrap(True)
        form.addRow("", self.wavelength_note)
        self.sim_low = self.sim_high = self.fwhm = None
        self.draw_as = self.strongest = None
        if simulated:
            low, high = sample.sim_range or crystal.DEFAULT_RANGE
            rng = QWidget()
            line = QHBoxLayout(rng)
            line.setContentsMargins(0, 0, 0, 0)
            self.sim_low, self.sim_high = NumberBox(), NumberBox()
            for box, value in ((self.sim_low, low), (self.sim_high, high)):
                box.setDecimals(2)
                box.setRange(0.1, 179.0)
                box.setSuffix(" deg")
                box.setValue(float(value))
            line.addWidget(self.sim_low, 1)
            line.addWidget(QLabel("to"))
            line.addWidget(self.sim_high, 1)
            rng.setToolTip("The 2-theta range simulated, at its wavelength.")
            form.addRow("Range", rng)
            self.fwhm = NumberBox()
            self.fwhm.setDecimals(3)
            self.fwhm.setRange(0.005, 5.0)
            self.fwhm.setSingleStep(0.01)
            self.fwhm.setSuffix(" deg")
            self.fwhm.setValue(float(sample.sim_fwhm))
            self.fwhm.setToolTip("How wide a simulated peak is drawn "
                                 "(pseudo-Voigt), in degrees 2-theta.")
            form.addRow("Peak width", self.fwhm)
            if scan is not None:
                self.draw_as = QComboBox()
                for drawing in crystal.DRAWS:
                    self.draw_as.addItem(crystal.DRAW_TITLES[drawing],
                                         drawing)
                self.draw_as.setCurrentIndex(max(0, self.draw_as.findData(
                    scan.draw_as)))
                self.draw_as.setToolTip(
                    "The profile; a line per reflection as tall as it is "
                    "strong; a row of equal ticks; or dotted lines across "
                    "the plot at the strongest.")
                form.addRow("Drawn as", self.draw_as)
                self.strongest = WholeBox()
                self.strongest.setRange(0, 100000)
                self.strongest.setSpecialValueText("all")
                self.strongest.setValue(int(scan.strongest)
                                        if scan.strongest is not None
                                        else (crystal.DEFAULT_STRONGEST
                                              if scan.draw_as
                                              == crystal.DRAW_LINES else 0))
                self.strongest.setToolTip(
                    "How many reflections sticks, ticks and lines show, "
                    "the strongest first. 0: all.")
                form.addRow("Strongest", self.strongest)
        self._say()
        self.wavelength.textChanged.connect(self._typed)
        for box in (self.sim_low, self.sim_high, self.fwhm):
            if box is not None:
                box.valueChanged.connect(lambda _v: self.changed.emit())
        if self.draw_as is not None:
            self.draw_as.currentIndexChanged.connect(
                lambda _i: self.changed.emit())
            self.strongest.valueChanged.connect(
                lambda _v: self.changed.emit())

    def typed_wavelength(self):
        """`(value, error)`: the typed wavelength (None: the file's), or
        the reason it is not one."""
        text = self.wavelength.text().strip()
        if not text:
            if self.sample.simulated:
                return None, "a simulation needs a wavelength"
            return None, None
        try:
            return crystal.parse_wavelength(text), None
        except crystal.SourceError as exc:
            return None, str(exc)

    def _typed(self, *_args):
        self._say()
        self.changed.emit()

    def _say(self):
        value, error = self.typed_wavelength()
        if error:
            self.wavelength_note.setStyleSheet("color: #d04040;")
            self.wavelength_note.setText(error)
            return
        self.wavelength_note.setStyleSheet("color: #9a9a9a;")
        if value is not None:
            self.wavelength_note.setText(crystal.describe_wavelength(value))
        elif self.sample.file_wavelength:
            self.wavelength_note.setText("the file's")
        else:
            self.wavelength_note.setStyleSheet("color: #d08020;")
            self.wavelength_note.setText(
                "none: drawn in 2-theta only; d and Q need one")

    def apply_sample(self, sample):
        value, error = self.typed_wavelength()
        if not error:
            sample.wavelength_override = value
        if self.sim_low is not None:
            low, high = sorted((float(self.sim_low.value()),
                                float(self.sim_high.value())))
            if high > low:
                sample.sim_range = (low, high)
            sample.sim_fwhm = float(self.fwhm.value())

    def apply_scan(self, scan):
        if self.draw_as is None:
            return
        scan.draw_as = self.draw_as.currentData() or crystal.DRAW_CURVE
        count = int(self.strongest.value())
        scan.strongest = count if count else None

    def restore(self, sample):
        for name, value in self.saved.items():
            setattr(sample, name, value)


class _MarginsDialog(QDialog):
    """Four margins in numbers, left / right / top / bottom: the page's
    white margins or the data's inside the axes box. Built here, shown and
    applied by the window, one undo step."""

    TITLE = "Margins"
    SIDES = (("left", "Left"), ("right", "Right"), ("top", "Top"),
             ("bottom", "Bottom"))

    def __init__(self, values, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(self.TITLE)
        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.boxes = {}
        for side, words in self.SIDES:
            box = NumberBox(self)
            self._shape(box, side)
            box.setValue(float(values.get(side, 0.0)))
            box.valueChanged.connect(lambda _v: self._check())
            self.form.addRow(words, box)
            self.boxes[side] = box
        self.note = QLabel("", self)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #9a9a9a;")
        layout.addWidget(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                        | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._extra(self.buttons)
        layout.addWidget(self.buttons)
        self._check()

    def _shape(self, box, side):
        pass

    def _extra(self, buttons):
        pass

    def _check(self):
        return True

    def values(self):
        return dict((side, float(box.value()))
                    for side, box in self.boxes.items())


class PageMarginsDialog(_MarginsDialog):
    """The page's white margins of an exact figure, in its unit, each no
    less than what it holds. More white space grows the page; the axes box
    keeps its size."""

    TITLE = "Page margins"

    def __init__(self, values, least, unit, parent=None):
        self.least = dict(least)
        self.unit = unit
        _MarginsDialog.__init__(self, values, parent)
        tighten = self.buttons.button(QDialogButtonBox.Reset)
        tighten.clicked.connect(lambda _c=False: self.tighten())
        self.note.setText("White space round the axes box. More grows the "
                          "page, less shrinks it; the axes box keeps its "
                          "size. Each is at least what it holds.")

    def _shape(self, box, side):
        box.setDecimals(2)
        box.setRange(float(self.least.get(side, 0.0)), 1000.0)
        box.setSingleStep(0.05)
        box.setSuffix(" " + self.unit)
        box.setToolTip("At least {:.2f} {}: less would cut off what it "
                       "holds.".format(self.least.get(side, 0.0), self.unit))

    def _extra(self, buttons):
        buttons.addButton(QDialogButtonBox.Reset).setText("Tighten")
        buttons.button(QDialogButtonBox.Reset).setToolTip(
            "Every margin down to what it holds.")

    def tighten(self):
        for side, box in self.boxes.items():
            box.setValue(float(self.least.get(side, 0.0)))


class DataMarginsDialog(_MarginsDialog):
    """The data's margins inside the axes box: the share of the axis left
    empty beyond the data on each side (the fit-margin arrows'). Two on one
    axis must leave the data some room."""

    TITLE = "Data margins"

    def _shape(self, box, side):
        box.setDecimals(3)
        box.setRange(0.0, 0.9)
        box.setSingleStep(0.01)
        box.setToolTip("The share of the axis left empty beyond the data: "
                       "0.1 is 10 %.")

    def _check(self):
        values = self.values()
        fine = (values["left"] + values["right"] < style.FIT_MOST
                and values["bottom"] + values["top"] < style.FIT_MOST)
        for pair in (("left", "right"), ("bottom", "top")):
            bad = sum(values[s] for s in pair) >= style.FIT_MOST
            for side in pair:
                self.boxes[side].setStyleSheet(
                    "border: 1px solid #d04040;" if bad else "")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(fine)
        self.note.setText("The share of each axis left empty beyond the "
                          "data: left 0.1 is the first tenth of the x "
                          "axis." if fine else
                          "Two margins of one axis leave the data no room.")
        return fine


class SampleSettings(_LiveDialog):
    """The file: its name on the figure and its wavelength (and, for a CIF
    or a card, how it is simulated)."""

    FIELDS = ("title",) + SAMPLE_FIELDS

    def __init__(self, parent, sample, on_change=None):
        _LiveDialog.__init__(self, parent, sample, on_change)
        self.setWindowTitle("File: {}".format(sample.name))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        form.addRow("File", QLabel(sample.path))
        self.title = QLineEdit(sample.title or "")
        self.title.setPlaceholderText(sample.file_name)
        self.title.setToolTip("Its name on the figure. Empty: the file's "
                              "name.")
        form.addRow("Name", self.title)
        self.source = PatternRows(form, sample, None, self)
        if sample.simulated:
            form.addRow("Reflections", QLabel(str(len(
                sample.reflections()))))
        else:
            x = sample.x
            form.addRow("Points", QLabel("{}, {:.4g} to {:.4g} deg".format(
                len(x), float(x[0]), float(x[-1]))))
        _head_rows(form, sample)
        buttons = self._buttons()
        layout.addWidget(buttons)
        self.title.textChanged.connect(self._apply)
        self.source.changed.connect(self._apply)

    def _apply(self, *_args):
        sample = self.obj
        sample.title = self.title.text().strip() or None
        self.source.apply_sample(sample)
        for scan in sample.scans:
            scan._cache_key = None
        self._live()


def axis_title(axis, part=""):
    """"X axis", "Y axis numbers", "X axis caption": the windows of an
    axis."""
    name = "{} axis".format(axis.which.upper())
    return "{} {}".format(name, part) if part else name


class _SideRow(object):
    """"Side" in every window of an axis - its spine's, its numbers' and
    its caption's, so a double-click on the y caption offers one too. It
    shows the side the axis is DRAWN on and puts it on the one chosen,
    through the window."""

    def _side_row(self, form):
        axis = self.obj
        self.side = QComboBox()
        for side in (("bottom", "top") if axis.which == "x"
                     else ("left", "right")):
            self.side.addItem(side, side)
        plot = _plot_of(self)
        drawn = plot.axis_side(axis) if plot is not None else axis.side
        self.side.setCurrentIndex(max(0, self.side.findData(drawn)))
        self.side.setToolTip("Which side of the plot the axis is on: its "
                             "line, numbers and caption.")
        form.addRow("Side", self.side)
        self.side.currentIndexChanged.connect(lambda _i: self._side_chosen())

    def _side_chosen(self):
        window = _window_of(self)
        if window is not None:
            window.set_axis_side(self.obj, self.side.currentData())


class CaptionSettings(_SideRow, _LiveDialog):
    """An axis CAPTION: its words and its size, and nothing else.

    Separate from the axis's own settings on purpose: the tick settings
    should not be what a double-click on the label gives you - the
    label is a piece of text, the spine is the axis. Double-clicking the
    spine opens `AxisSettings`; this is what the caption opens.
    """

    FIELDS = ("label", "label_size", "label_along", "label_gap", "visible")
    INDIVIDUAL = ("label", "label_along")
    GROUP_DISABLED = ("label",)

    def __init__(self, parent, axis, doc, on_change=None):
        _LiveDialog.__init__(self, parent, axis, on_change)
        self.doc = doc
        self.setWindowTitle(axis_title(axis, "caption"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.shown = QCheckBox("Show")
        self.shown.setChecked(bool(axis.visible))
        self.shown.setToolTip("Draw the caption.")
        form.addRow("", self.shown)

        self.label = QLineEdit(axis.label or "")
        self.label.setPlaceholderText(axis.caption(doc))
        form.addRow("Text", self.label)

        self.text_size = _style_number(self, axis, "label_size")
        form.addRow("Size", self.text_size)

        self.gap = _style_number(self, axis, "label_gap")
        self.gap.setToolTip("Space between the numbers and the caption. "
                            "Dragging sets it too.")
        form.addRow("Distance", self.gap)
        self._side_row(form)

        note = QLabel("*A* italic, _{a} subscript, ^{-1} superscript, "
                      "\\nu Greek, \\tilde{\\nu} a tilde, LaTeX between "
                      "$...$. Drag the caption to move it.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9a9a9a;")
        layout.addWidget(note)

        buttons = self._buttons()
        layout.addWidget(buttons)

        self.label.textChanged.connect(self._apply)
        self.text_size.changed.connect(self._apply)
        self.gap.changed.connect(self._apply)
        self.shown.toggled.connect(self._apply)

    def _apply(self, *_args):
        self.obj.label = self.label.text().strip() or None
        self.obj.label_size = self.text_size.value()
        self.obj.label_gap = self.gap.value()
        self.obj.visible = bool(self.shown.isChecked())
        self._live()


def _x_unit(doc):
    """The x axis's unit, as plain text."""
    return doc.x_unit if doc is not None else units.X_TEXT[units.TWO_THETA]


def axis_unit(doc, axis):
    """The unit an axis's numbers are in, for its range boxes."""
    if doc is None:
        return ""
    if axis.which == "x":
        return doc.x_unit
    return doc.y_axis_unit()


class _RangeRows(object):
    """The Range and Locked rows of an axis's settings."""

    def _show_range(self):
        plot = _plot_of(self)
        shown = plot is not None and self.obj.which in plot.shown_view_axes()
        for widget in (self.low, self.high, self.locked):
            widget.setEnabled(shown)
            widget.blockSignals(True)
        if shown:
            lo, hi = plot.view_of(self.obj.which)
            self.low.setValue(float(lo))
            self.high.setValue(float(hi))
            self.locked.setChecked(plot.lock_of(self.obj.which) is not None)
        for widget in (self.low, self.high, self.locked):
            widget.blockSignals(False)

    def _typed_range(self):
        window = _window_of(self)
        lo, hi = float(self.low.value()), float(self.high.value())
        ok = hi > lo and window is not None and window.set_axis_range(
            self.obj, lo, hi)
        for box in (self.low, self.high):
            box.setStyleSheet("" if ok else "border: 1px solid #d04040;")
        if ok:
            self._show_range()

    def _lock_toggled(self, on):
        window = _window_of(self)
        if window is None:
            return
        if on:
            window.lock_axis(self.obj, float(self.low.value()),
                             float(self.high.value()))
        else:
            window.lock_axis(self.obj, False)
        self._show_range()


class AxisSettings(_SideRow, _RangeRows, _LiveDialog):
    """The SPINE: its side, its ticks and their steps, the line opposite it,
    and the grid. Opened by double-clicking the axis line; its numbers
    (`NumberSettings`) and its caption (`CaptionSettings`) have their own
    windows.

    The defaults are a stacked figure's frame: ticks inward, minor ticks,
    the opposite line with ticks and no numbers, no grid; the y axis of a
    stack with no ticks at all.
    """

    FIELDS = ("show_ticks", "ticks_inward", "tick_length", "minor_ticks",
              "minor_count", "minor_length", "major_step", "mirror",
              "mirror_ticks", "show_grid")

    def __init__(self, parent, axis, doc, on_change=None):
        _LiveDialog.__init__(self, parent, axis, on_change)
        self.doc = doc
        self.setWindowTitle(axis_title(axis))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        # The RANGE, its minimum and maximum: what the axis shows now,
        # typed back as one view step; locked, F returns to it. Through the
        # window, never this dialog's snapshot.
        self.low = NumberBox()
        self.high = NumberBox()
        unit = axis_unit(doc, axis)
        for box in (self.low, self.high):
            box.setDecimals(4)
            box.setRange(-1e9, 1e9)
            if unit:
                box.setSuffix(" " + unit)
        self.low.setToolTip("The axis's minimum. A sum works: 50-10.")
        self.high.setToolTip("The axis's maximum.")
        range_row = QWidget(self)
        range_line = QHBoxLayout(range_row)
        range_line.setContentsMargins(0, 0, 0, 0)
        range_line.addWidget(self.low, 1)
        range_line.addWidget(QLabel("to"), 0)
        range_line.addWidget(self.high, 1)
        form.addRow("Range", range_row)
        self.locked = QCheckBox("Locked: F returns to this range")
        self.locked.setToolTip("Unticked, F fits the data.")
        form.addRow("", self.locked)
        self._show_range()
        self.low.valueChanged.connect(lambda _v: self._typed_range())
        self.high.valueChanged.connect(lambda _v: self._typed_range())
        self.locked.toggled.connect(self._lock_toggled)

        self._side_row(form)

        self.ticks = QCheckBox("Ticks")
        self.ticks.setChecked(bool(getattr(axis, "show_ticks", True)))
        self.ticks.setToolTip("Draw the ticks. A stack of patterns shows none "
                              "on its y axis.")
        form.addRow("", self.ticks)

        self.inward = QCheckBox("Ticks point inward")
        self.inward.setChecked(bool(axis.ticks_inward))
        self.inward.setToolTip("Into the plot.")
        form.addRow("", self.inward)

        self.step = NumberBox()
        self.step.setDecimals(4)
        self.step.setRange(0.0, 1e6)
        self.step.setToolTip("Distance between numbered ticks, in the "
                             "axis unit.")
        self.step_auto = QCheckBox("Automatic")
        self.step_auto.setToolTip("A round step that fits the range.")
        step_row = QWidget(self)
        step_line = QHBoxLayout(step_row)
        step_line.setContentsMargins(0, 0, 0, 0)
        step_line.addWidget(self.step, 1)
        step_line.addWidget(self.step_auto, 0)
        form.addRow("Major step", step_row)

        self.tick_length = NumberBox()
        self.minor_length = NumberBox()
        for box in (self.tick_length, self.minor_length):
            box.setDecimals(1)
            box.setRange(0.0, 60.0)
            box.setSuffix(" px")
        self.tick_length.setToolTip("Length of the numbered ticks.")
        self.minor_length.setToolTip("Length of the minor ticks.")
        form.addRow("Major length", self.tick_length)

        self.minor = QCheckBox("Minor ticks")
        self.minor.setToolTip("Unnumbered ticks between the numbered ones.")
        form.addRow("", self.minor)
        self.minor_count = WholeBox()
        self.minor_count.setRange(1, 20)
        self.minor_count.setToolTip("Minor intervals per major one: 5 puts "
                                    "four ticks between two numbers.")
        form.addRow("Minor intervals", self.minor_count)
        form.addRow("Minor length", self.minor_length)

        self.mirror = QCheckBox("Line on the opposite side")
        self.mirror.setToolTip("Close the frame of the plot.")
        form.addRow("", self.mirror)
        self.mirror_ticks = QCheckBox("Ticks on it (no numbers)")
        self.mirror_ticks.setToolTip("Origin's style: the opposite line "
                                     "ticked like this one.")
        form.addRow("", self.mirror_ticks)

        self.grid = QCheckBox("Grid lines")
        self.grid.setToolTip("Lines across the plot at the numbered ticks.")
        form.addRow("", self.grid)

        note = QLabel("The numbers and the caption have their own settings: "
                      "double-click them.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9a9a9a;")
        layout.addWidget(note)

        buttons = self._buttons()
        layout.addWidget(buttons)
        self._show()

        for check in (self.ticks, self.inward, self.minor, self.mirror,
                      self.mirror_ticks, self.grid, self.step_auto):
            check.toggled.connect(self._apply)
        for box in (self.step, self.tick_length, self.minor_length):
            box.valueChanged.connect(self._apply)
        self.minor_count.valueChanged.connect(self._apply)

    def _step_shown(self):
        """The step as it is drawn, for the box while it is automatic."""
        plot = _plot_of(self)
        axis = self.obj
        if plot is None:
            return 0.0
        # Each axis its OWN range.
        lo, hi = plot.view_x() if axis.which == "x" else plot.view_y()
        return float(plot.tick_step(axis, lo, hi))

    def _show(self):
        axis = self.obj
        widgets = (self.ticks, self.inward, self.step, self.step_auto,
                   self.tick_length, self.minor, self.minor_count,
                   self.minor_length, self.mirror, self.mirror_ticks,
                   self.grid)
        for widget in widgets:
            widget.blockSignals(True)
        self.ticks.setChecked(bool(getattr(axis, "show_ticks", True)))
        self.inward.setChecked(bool(axis.ticks_inward))
        self.step_auto.setChecked(axis.major_step is None)
        self.step.setValue(float(axis.major_step or self._step_shown()))
        self.step.setEnabled(axis.major_step is not None)
        self.tick_length.setValue(float(axis.tick_length))
        self.minor.setChecked(bool(axis.minor_ticks))
        self.minor_count.setValue(int(axis.minor_count))
        self.minor_length.setValue(float(axis.minor_length))
        self.minor_count.setEnabled(bool(axis.minor_ticks))
        self.minor_length.setEnabled(bool(axis.minor_ticks))
        self.mirror.setChecked(bool(axis.mirror))
        self.mirror_ticks.setChecked(bool(axis.mirror_ticks))
        self.mirror_ticks.setEnabled(bool(axis.mirror))
        self.grid.setChecked(bool(axis.show_grid))
        for widget in widgets:
            widget.blockSignals(False)

    def _apply(self, *_args):
        axis = self.obj
        axis.show_ticks = bool(self.ticks.isChecked())
        axis.ticks_inward = bool(self.inward.isChecked())
        if self.step_auto.isChecked():
            axis.major_step = None
        else:
            axis.major_step = float(self.step.value()) or None
        axis.tick_length = float(self.tick_length.value())
        axis.minor_ticks = bool(self.minor.isChecked())
        axis.minor_count = int(self.minor_count.value())
        axis.minor_length = float(self.minor_length.value())
        axis.mirror = bool(self.mirror.isChecked())
        axis.mirror_ticks = bool(self.mirror_ticks.isChecked())
        axis.show_grid = bool(self.grid.isChecked())
        self._live()
        self._show()


class NumberSettings(_SideRow, _LiveDialog):
    """An axis's NUMBERS: shown or not, their size, their format, and
    which of them are left out. Opened by double-clicking them; the spine
    and the caption have their own."""

    FIELDS = ("show_numbers", "tick_size", "number_format",
              "hidden_numbers", "hidden_context")
    # Values in one axis's unit: never copied to another axis.
    INDIVIDUAL = ("hidden_numbers", "hidden_context")
    GROUP_DISABLED = ("hidden_numbers",)

    def __init__(self, parent, axis, doc, on_change=None):
        _LiveDialog.__init__(self, parent, axis, on_change)
        self.doc = doc
        self.setWindowTitle(axis_title(axis, "numbers"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.shown = QCheckBox("Show")
        self.shown.setChecked(bool(axis.show_numbers))
        self.shown.setToolTip("Draw the numbers; the ticks stay.")
        form.addRow("", self.shown)

        self.tick_size = _style_number(self, axis, "tick_size")
        form.addRow("Size", self.tick_size)

        # Not house style: an x axis in degrees and a y axis in counts want
        # different digits, so "automatic" is the only shared default.
        self.number_format = StyleText(axis.number_format, lambda: "",
                                       parent=self)
        # The general tooltip speaks of units, which an axis's numbers
        # cannot carry (the unit is in the caption).
        self.number_format.edit.setToolTip(
            "How every number on this axis is written. %.0f: whole "
            "numbers (50). %.1f: one decimal (50.0). %.2f: two. %.2g: two "
            "significant figures (0.51, 1.2, 15). Empty: as few digits as "
            "the spacing of the ticks needs.")
        form.addRow("Format", self.number_format)
        #: What the axis writes now, with this format and without the
        #: hidden ones: the Format box's effect, seen before looking at
        #: the plot.
        self.written = QLabel(self)
        self.written.setWordWrap(True)
        form.addRow("Written", self.written)

        self.hidden_numbers = QLineEdit(self)
        self.hidden_numbers.setPlaceholderText("none")
        self.hidden_numbers.setToolTip(
            "Numbers NOT written on this axis - their ticks stay. Type "
            "them as the axis writes them, separated by ';', ', ' or a "
            "space: \"50\" leaves the 50 at the corner of the box out. "
            "Right-click a number on the plot to hide or show that one.")
        self._show_hidden()
        form.addRow("Hidden", self.hidden_numbers)
        self._side_row(form)

        buttons = self._buttons()
        layout.addWidget(buttons)

        self.shown.toggled.connect(self._apply)
        self.tick_size.changed.connect(self._apply)
        self.number_format.changed.connect(self._apply)
        self.hidden_numbers.textEdited.connect(self._hidden_typed)
        self._show_written()

    def _context(self):
        plot = _plot_of(self)
        return (plot.axis_context(self.obj.which) if plot is not None
                else None)

    def _show_hidden(self):
        """The hidden numbers that apply in the axis's unit now."""
        axis = self.obj
        values = (axis.hidden_numbers
                  if list(axis.hidden_context or []) == (self._context()
                                                         or [])
                  else [])
        self.hidden_numbers.setText("; ".join(
            "{:g}".format(round(float(v), 10) + 0.0) for v in values))

    def _show_written(self):
        plot = _plot_of(self)
        if plot is None or not self.obj.show_numbers:
            self.written.setText("(none)" if not self.obj.show_numbers
                                 else "")
            return
        texts = [text for _v, _at, text in plot.numbered_ticks(self.obj)]
        self.written.setText(", ".join(texts) or "(none)")

    def _apply(self, *_args):
        axis = self.obj
        axis.show_numbers = bool(self.shown.isChecked())
        axis.tick_size = self.tick_size.value()
        axis.number_format = self.number_format.value()
        self._live()
        self._show_written()

    def _hidden_typed(self, text):
        """Only when typed here: numbers hidden in another unit are kept
        while the rest of the window is used."""
        typed = numbers.values(text)
        if typed is None:
            self.hidden_numbers.setStyleSheet("border: 1px solid #d04040;")
            return
        self.hidden_numbers.setStyleSheet("")
        axis = self.obj
        # Replaced, never changed in place (the snapshot).
        axis.hidden_numbers = sorted(typed)
        axis.hidden_context = self._context() if typed else None
        self._live()
        self._show_written()


class FigureSettings(_LiveDialog):
    """This figure's size and the place of its axes box - saved with it.

    Two session files (first up-scans, second up-scans) exported
    with the same settings must come out the same size, with their axes
    boxes the same size and in the same place, so that they sit side by side
    in Word without fiddling. In "an exact size" the MARGINS decide the axes
    box; the numbers and captions have to fit inside them, and this says
    when they do not.
    """

    FIELDS = figure_module.FigureLayout.FIELDS

    def __init__(self, parent, layout_obj, plot=None, on_change=None):
        _LiveDialog.__init__(self, parent, layout_obj, on_change)
        self.plot = plot
        self.setWindowTitle("Figure size and margins")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.mode = QComboBox()
        for mode in figure_module.MODES:
            self.mode.addItem(figure_module.MODE_TITLES[mode], mode)
        self.mode.setCurrentIndex(max(0, self.mode.findData(layout_obj.mode)))
        form.addRow("The figure has", self.mode)

        self.unit = QComboBox()
        for unit, title in ((figure_module.UNIT_CM, "centimetres"),
                            (figure_module.UNIT_IN, "inches")):
            self.unit.addItem(title, unit)
        self.unit.setCurrentIndex(max(0, self.unit.findData(layout_obj.unit)))
        form.addRow("Unit", self.unit)

        def pair(first, second, between="x"):
            row = QWidget(self)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(first, 1)
            line.addWidget(QLabel(between))
            line.addWidget(second, 1)
            return row

        def length():
            box = NumberBox()
            box.setDecimals(2)
            box.setRange(0.0, 200.0)
            box.setSingleStep(0.1)
            return box

        self.fig_w, self.fig_h = length(), length()
        form.addRow("Size (width x height)", pair(self.fig_w, self.fig_h))
        self.margin_l, self.margin_r = length(), length()
        self.margin_t, self.margin_b = length(), length()
        form.addRow("Margins left, right", pair(self.margin_l, self.margin_r,
                                                ","))
        form.addRow("Margins top, bottom", pair(self.margin_t, self.margin_b,
                                                ","))
        self.axes_w, self.axes_h = length(), length()
        self.axes_w.setToolTip("The axes box; the figure grows around "
                               "it.")
        self.axes_h.setToolTip(self.axes_w.toolTip())
        form.addRow("Axes box", pair(self.axes_w, self.axes_h))

        self.aspect_w, self.aspect_h = NumberBox(), NumberBox()
        for box in (self.aspect_w, self.aspect_h):
            box.setDecimals(2)
            box.setRange(0.1, 100.0)
        form.addRow("Aspect ratio", pair(self.aspect_w, self.aspect_h, ":"))

        self.dpi = WholeBox()
        self.dpi.setRange(72, 2400)
        self.dpi.setSingleStep(50)
        self.dpi.setSuffix(" dpi")
        self.dpi.setToolTip("Pixels per inch of a PNG. SVG is exact.")
        form.addRow("PNG resolution", self.dpi)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #e05a5a;")
        layout.addWidget(self.warning)
        note = QLabel("Saved with the session. Same size and margins, "
                      "same axes box; exports come out at exactly this "
                      "size.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9a9a9a;")
        layout.addWidget(note)

        self.make_default = QPushButton("Use for new figures")
        self.make_default.setToolTip("New figures start with this "
                                     "layout.")
        self.make_default.clicked.connect(lambda _c=False: self.set_default())
        buttons = self._buttons()
        row = QHBoxLayout()
        row.addWidget(self.make_default)
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

        self._show()
        for box in (self.fig_w, self.fig_h, self.margin_l, self.margin_r,
                    self.margin_t, self.margin_b, self.aspect_w,
                    self.aspect_h):
            box.valueChanged.connect(self._apply)
        self.axes_w.valueChanged.connect(self._axes_typed)
        self.axes_h.valueChanged.connect(self._axes_typed)
        self.dpi.valueChanged.connect(self._apply)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.unit.currentIndexChanged.connect(self._unit_changed)

    def _mode_changed(self, *_args):
        """Going EXACT takes its numbers from the screen
        (`exact_from_screen`), not from the stored size, so the figure keeps
        its look: the stored one set every text out of proportion."""
        fig = self.obj
        wanted = self.mode.currentData()
        if (wanted == figure_module.MODE_SIZE and fig.mode != wanted
                and self.plot is not None and self.plot.doc is not None):
            for name, value in self.plot.exact_from_screen().items():
                setattr(fig, name, value)
            fig.mode = wanted
            fig.grown = {}
            self._live()
            self._show()
            return
        self._apply()

    def _boxes(self):
        return (self.fig_w, self.fig_h, self.margin_l, self.margin_r,
                self.margin_t, self.margin_b, self.axes_w, self.axes_h,
                self.aspect_w, self.aspect_h, self.dpi)

    def _show(self):
        """Put the layout's numbers in the boxes, without writing back."""
        fig = self.obj
        for box in self._boxes():
            box.blockSignals(True)
        suffix = " " + fig.unit
        for box, value in ((self.fig_w, fig.width), (self.fig_h, fig.height),
                           (self.margin_l, fig.margin_left),
                           (self.margin_r, fig.margin_right),
                           (self.margin_t, fig.margin_top),
                           (self.margin_b, fig.margin_bottom)):
            box.setSuffix(suffix)
            box.setValue(float(value))
        axes_w, axes_h = fig.axes_size()
        for box, value in ((self.axes_w, axes_w), (self.axes_h, axes_h)):
            box.setSuffix(suffix)
            box.setValue(max(0.0, float(value)))
        self.aspect_w.setValue(float(fig.aspect_w))
        self.aspect_h.setValue(float(fig.aspect_h))
        self.dpi.setValue(int(fig.dpi))
        for box in self._boxes():
            box.blockSignals(False)
        size_mode = fig.mode == figure_module.MODE_SIZE
        for box in (self.fig_w, self.fig_h, self.margin_l, self.margin_r,
                    self.margin_t, self.margin_b, self.axes_w, self.axes_h,
                    self.dpi, self.unit):
            box.setEnabled(size_mode)
        for box in (self.aspect_w, self.aspect_h):
            box.setEnabled(fig.mode == figure_module.MODE_ASPECT)
        self._describe()

    def _describe(self):
        """What does not fit in its margin, in the layout's unit."""
        if self.plot is None:
            self.warning.setText("")
            return
        fig = self.obj
        lines = []
        for side, need, have in self.plot.overflow():
            to_unit = figure_module.PER_INCH[fig.unit] \
                / figure_module.DESIGN_DPI
            lines.append("The {} margin is {:.2f} {} and what it holds "
                         "needs {:.2f} {}: something drawn there will be "
                         "cut off.".format(side, have * to_unit, fig.unit,
                                           need * to_unit, fig.unit))
        if not fig.is_valid():
            lines.append("The margins leave no room for the axes box.")
        self.warning.setText("\n".join(lines))

    def _apply(self, *_args):
        fig = self.obj
        fig.mode = self.mode.currentData()
        fig.width = float(self.fig_w.value())
        fig.height = float(self.fig_h.value())
        fig.margin_left = float(self.margin_l.value())
        fig.margin_right = float(self.margin_r.value())
        fig.margin_top = float(self.margin_t.value())
        fig.margin_bottom = float(self.margin_b.value())
        fig.aspect_w = float(self.aspect_w.value())
        fig.aspect_h = float(self.aspect_h.value())
        fig.dpi = int(self.dpi.value())
        self._live()
        self._show()

    def _axes_typed(self, *_args):
        """An axes box typed directly: the figure grows round it."""
        self.obj.set_axes_size(float(self.axes_w.value()),
                               float(self.axes_h.value()))
        self._live()
        self._show()

    def _unit_changed(self, *_args):
        self.obj.set_unit(self.unit.currentData())
        self._live()
        self._show()

    def set_default(self):
        """This layout, for every NEW figure from now on."""
        style.set_figure_default(self.obj)
        try:
            style.save_preferences()
        except OSError:
            pass
        self.make_default.setText("New figures use this")


class LabelSettings(_LiveDialog):
    """A caption the user placed: its text, size and colour."""

    FIELDS = ("text", "colour", "size", "bold", "x", "y", "space",
              "anchor", "rotation", "leader", "leader_from", "leader_colour",
              "flush", "vline", "line_dashed", "at", "dx", "dy")
    INDIVIDUAL = ("text", "x", "y", "space", "leader", "vline", "at", "dx",
                  "dy")
    GROUP_DISABLED = ("text", "transform.space", "transform.at_x",
                      "transform.at_y")

    def __init__(self, parent, label, on_change=None):
        # A label that has followed its scan shows where it IS, not where
        # it was put (`PlotWidget.rebase`; nothing moves).
        plot = getattr(parent, "plot", None)
        if plot is not None:
            plot.rebase(label)
        _LiveDialog.__init__(self, parent, label, on_change)
        self.setWindowTitle("Marker line" if label.is_vline
                            else "Note" if label.leader else "Label")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        # Several lines: Enter breaks one, Tab goes on to the next field.
        self.text = QPlainTextEdit(label.text)
        self.text.setTabChangesFocus(True)
        self.text.setFixedHeight(3 * self.fontMetrics().lineSpacing() + 14)
        self.text.setToolTip("*A* italic, _{a} subscript, ^{-} superscript, "
                             "\\theta Greek, \\AA, LaTeX between $...$; "
                             "on a marker line {} is its position. Enter "
                             "starts a new line.")
        form.addRow("Text", self.text)

        self.text_size = _style_number(self, label, "size")
        form.addRow("Size", self.text_size)

        self.bold = QCheckBox("Bold")
        self.bold.setChecked(bool(label.bold))
        form.addRow("", self.bold)

        self.transform = ArtistTransform(label, getattr(parent, "plot", None),
                                         on_change=self._live, parent=self)
        form.addRow("Place", self.transform)

        # A label that belongs to a scan hangs from its curve:
        # where along it, and how far from it - an analysis label's rows.
        self.hang_at = QLineEdit(self)
        self.hang_at.setToolTip("The position on its curve it hangs "
                                "from, on the x axis.")
        self.hang_dy = NumberBox()
        self.hang_dx = NumberBox()
        for box in (self.hang_dy, self.hang_dx):
            box.setDecimals(1)
            box.setRange(-2000.0, 2000.0)
            box.setSuffix(" px")
        self.hang_dy.setToolTip("How far above the curve (below: "
                                "negative). Drag it up and down.")
        self.hang_dx.setToolTip("How far to the right of its point.")
        form.addRow("On the curve at", self.hang_at)
        form.addRow("Distance", self.hang_dy)
        form.addRow("Sideways", self.hang_dx)
        self._hang_rows = (self.hang_at, self.hang_dy, self.hang_dx)
        self._show_hang()
        self.hang_at.editingFinished.connect(self._typed_hang_at)
        self.hang_dy.valueChanged.connect(self._typed_hang)
        self.hang_dx.valueChanged.connect(self._typed_hang)

        form.addRow("Colour", _colour_button(
            self, lambda: (label.colour if label.colour != "auto"
                           else label.scan.colour if label.scan is not None
                           else "#cccccc"), self._set_colour))
        # Automatic is its curve's colour for a label that belongs to one
        # (`PlotWidget.label_colour`), the theme's ink for a free one -
        # and says which.
        self.auto = QCheckBox("Same as parent" if label.scan is not None
                              else "Follow the theme")
        self.auto.setToolTip("The colour of the curve it belongs to."
                             if label.scan is not None
                             else "The theme's ink.")
        self.auto.setChecked(label.colour in (None, "", "auto"))
        form.addRow("", self.auto)

        # A NOTE is a label with an arrow to a point.
        self.leader = QCheckBox("Leader arrow (a note)")
        self.leader.setChecked(bool(label.leader))
        self.leader.setToolTip("An arrow from the text to a point. Select "
                               "the note and drag the ring at its tip; near "
                               "a curve it snaps onto it.")
        form.addRow("", self.leader)

        # The point it names, typed, as well as the text's place.
        tip = QWidget(self)
        row = QHBoxLayout(tip)
        row.setContentsMargins(0, 0, 0, 0)
        self.tip_x = QLineEdit(self)
        self.tip_x.setToolTip("The position it points at, on the x axis.")
        self.tip_y = QLineEdit(self)
        self.tip_y.setToolTip("The height it points at, in its axis's "
                              "unit.")
        self.tip_unit = QLabel("", self)
        row.addWidget(self.tip_x)
        row.addWidget(QLabel("at", self))
        row.addWidget(self.tip_y)
        row.addWidget(self.tip_unit)
        form.addRow("Points at", tip)
        self.leader_from = QComboBox()
        self.leader_from.addItem("Nearest edge", "auto")
        for name in units_module.ANCHORS:
            self.leader_from.addItem(name, name)
        self.leader_from.setCurrentIndex(max(0, self.leader_from.findData(
            label.leader_from)))
        self.leader_from.setToolTip("Where on the text the arrow starts.")
        form.addRow("Arrow from", self.leader_from)
        self.arrow_colour = _colour_button(
            self, lambda: (label.leader_colour
                           if label.leader_colour not in (None, "", "auto")
                           else (label.colour if label.colour != "auto"
                                 else "#cccccc")), self._set_leader_colour)
        form.addRow("Arrow colour", self.arrow_colour)
        self.leader_auto = QCheckBox("Same as text")
        self.leader_auto.setChecked(label.leader_colour in (None, "", "auto"))
        form.addRow("", self.leader_auto)
        self._note_rows = (tip, self.leader_from, self.arrow_colour,
                           self.leader_auto)
        # On its curve a note's arrow drops straight onto its point: no
        # point to type apart from where it hangs, no edge to leave from.
        if label.attached:
            for widget in (tip, self.leader_from):
                form.labelForField(widget).setVisible(False)
                widget.setVisible(False)
        self._show_tip()

        # A MARKER LINE's position, typed, and its style.
        self.line_at = QLineEdit(self)
        self.line_at.setToolTip("Where the line stands, on the x axis.")
        self.line_dashed = QCheckBox("Dashed")
        self.line_dashed.setChecked(bool(label.line_dashed))
        line = QWidget(self)
        line_row = QHBoxLayout(line)
        line_row.setContentsMargins(0, 0, 0, 0)
        line_row.addWidget(self.line_at)
        line_row.addWidget(self.line_dashed)
        form.addRow("Line at", line)
        form.labelForField(line).setVisible(label.is_vline)
        line.setVisible(label.is_vline)
        if label.is_vline:
            self.line_at.setText("{:.6g}".format(float(label.vline)))
        self.line_at.editingFinished.connect(self._typed_line)
        self.line_dashed.toggled.connect(self._apply)

        buttons = self._buttons()
        layout.addWidget(buttons)

        self.text.textChanged.connect(self._apply)
        self.text_size.changed.connect(self._apply)
        self.bold.toggled.connect(self._apply)
        self.auto.toggled.connect(self._apply)
        self.leader.toggled.connect(self._leader_toggled)
        self.leader_from.currentIndexChanged.connect(self._apply)
        self.leader_auto.toggled.connect(self._apply)
        self.tip_x.editingFinished.connect(self._typed_tip)
        self.tip_y.editingFinished.connect(self._typed_tip)

    def _show_hang(self):
        """The rows of a label hanging from its curve, shown only then."""
        label = self.obj
        on = bool(getattr(label, "attached", False))
        form = self.findChildren(QFormLayout)[0]
        # A label on a curve is placed like every other artist, by x and y
        # in the Place rows (`ArtistTransform`, which hangs it again where
        # it is typed). The point on the curve, a distance and a sideways
        # shift in px read as confusing in use: those rows are never
        # shown now.
        for widget in self._hang_rows:
            widget.setVisible(False)
            caption = form.labelForField(widget)
            if caption is not None:
                caption.setVisible(False)
        if not on:
            return
        if label.leader:
            self.transform.hide_anchor()
        plot = getattr(self.parent(), "plot", None)
        where = plot.attached_x(label) if plot is not None else None
        for widget in self._hang_rows:
            widget.blockSignals(True)
        if where is not None:
            self.hang_at.setText("{} {}".format(
                numbers.write(float(where), "%.4g"), _x_unit(self.doc)))
        self.hang_dy.setValue(-float(plot.label_dy(label))
                              if plot is not None else 0.0)
        self.hang_dx.setValue(float(label.dx or 0.0))
        for widget in self._hang_rows:
            widget.blockSignals(False)

    def _typed_hang_at(self):
        label = self.obj
        plot = getattr(self.parent(), "plot", None)
        if not label.attached or plot is None:
            return
        where = units.parse_position(self.hang_at.text())
        at = (plot.attach_at_x(label, where)
              if where is not None else None)
        if at is None:
            self.hang_at.setStyleSheet("border: 1px solid #d04040;")
            return
        self.hang_at.setStyleSheet("")
        if tuple(at) != tuple(label.at):
            label.at = tuple(at)
            self._live()

    def _typed_hang(self, _value=0.0):
        label = self.obj
        if not label.attached:
            return
        label.dy = -float(self.hang_dy.value())
        if not label.leader:
            label.dx = float(self.hang_dx.value())
        self._live()

    def _typed_line(self):
        label = self.obj
        if not label.is_vline:
            return
        where = units.parse_position(self.line_at.text())
        if where is None:
            self.line_at.setStyleSheet("border: 1px solid #d04040;")
            return
        self.line_at.setStyleSheet("")
        if abs(float(where) - float(label.vline)) > 1e-9:
            label.vline = float(where)
            self._live()

    def _set_leader_colour(self, name):
        self.obj.leader_colour = name
        self.leader_auto.setChecked(False)
        self._live()

    def _show_tip(self):
        """The point a note names, in the axes' units; the note rows only
        while it is a note."""
        label = self.obj
        plot = getattr(self.parent(), "plot", None)
        on = bool(label.leader)
        for widget in self._note_rows:
            widget.setEnabled(on)
        if not on or plot is None:
            self.tip_x.setText("")
            self.tip_y.setText("")
            return
        self.tip_x.setText("{:.6g}".format(float(label.leader[0])))
        self.tip_y.setText("{:.5g}".format(float(label.leader[1])
                                           + label.follow()))
        self.tip_unit.setText(plot._typed_unit(label, "y"))

    def _typed_tip(self):
        """The point typed: a position, a height in the axis's unit."""
        label = self.obj
        if not label.leader:
            return
        where = units.parse_position(self.tip_x.text())
        height = numbers.evaluate(self.tip_y.text())
        if where is None or height is None:
            box = self.tip_x if where is None else self.tip_y
            box.setStyleSheet("border: 1px solid #d04040;")
            return
        self.tip_x.setStyleSheet("")
        self.tip_y.setStyleSheet("")
        new = [float(where), float(height) - label.follow()]
        if new != list(label.leader):
            label.leader = new
            self._live()

    def _set_colour(self, name):
        self.obj.colour = name
        if hasattr(self, "auto"):
            self.auto.setChecked(False)
        self._live()

    def _leader_toggled(self, on):
        """On: an arrow down and to the left of the text, to be dragged
        where it belongs. Off: a plain label again."""
        label = self.obj
        plot = getattr(self.parent(), "plot", None)
        if not on:
            label.leader = None
        elif not label.leader and plot is not None:
            rect = plot.plot_rect()
            box = plot.rotated_bounds(label, plot.artist_box(label, rect),
                                      rect)
            tip = QPointF(box.center().x(), box.bottom() + 36.0)
            label.leader = plot.leader_value(label, tip, rect)
        self._show_tip()
        self._live()

    def _apply(self, *_args):
        label = self.obj
        label.text = self.text.toPlainText() or "Label"
        label.size = self.text_size.value()
        label.bold = bool(self.bold.isChecked())
        if self.auto.isChecked():
            label.colour = "auto"
        label.leader_from = self.leader_from.currentData() or "auto"
        if self.leader_auto.isChecked():
            label.leader_colour = "auto"
        label.line_dashed = bool(self.line_dashed.isChecked())
        self._live()


class AnalysisSettings(_LiveDialog):
    """One analysis: what it is, its interval, and how it is labelled.

    The model and the interval are recomputed IN PLACE by the window
    (`MainWindow.change_model`, `retype_interval`), each as its own undo
    step, exactly as dragging a gizmo is; everything else here is styling
    and goes into this window's one step.
    """

    FIELDS = ("visible", "colour", "label", "label_size", "flush",
              "show_interval", "interval_size", "shade", "shading",
              "number_format", "label_dy")
    INDIVIDUAL = ("label",)
    GROUP_DISABLED = ("label", "model", "start", "end")

    def __init__(self, parent, analysis, on_change=None):
        _LiveDialog.__init__(self, parent, analysis, on_change)
        self.setWindowTitle(analysis.model_name)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.model = QComboBox()
        for entry in measure.MODELS:
            self.model.addItem(entry.title, entry.name)
        if self.model.findData(analysis.model_name) < 0:
            self.model.addItem(analysis.model_name, analysis.model_name)
        self.model.setCurrentIndex(self.model.findData(analysis.model_name))
        self.model.setToolTip("Analysis type. Changing it measures again on "
                              "the same interval.")
        form.addRow("Model", self.model)

        # The interval: two positions.
        self.start = QLineEdit(self)
        self.end = QLineEdit(self)
        for box, word in ((self.start, "low"), (self.end, "high")):
            box.setToolTip("The interval's {} end, on the x axis.".format(
                word))
            box.editingFinished.connect(self._typed_interval)
        form.addRow("From", self.start)
        form.addRow("To", self.end)

        self.results = QLabel("")
        self.results.setWordWrap(True)
        self.results.setToolTip("Everything the analysis measured, on the "
                                "pattern's intensities as stored.")
        form.addRow("Results", self.results)

        self.visible = QCheckBox("Show")
        self.visible.setChecked(bool(analysis.visible))
        self.visible.setToolTip("Draw this analysis.")
        form.addRow("", self.visible)

        self.interval = QCheckBox("Show interval markers")
        self.interval.setChecked(bool(analysis.show_interval))
        self.interval.setToolTip("Dashes at the interval ends, on the "
                                 "curve.")
        form.addRow("", self.interval)
        self.interval_size = _style_number(self, analysis, "interval_size")
        self.interval_size.setToolTip("Half the length of each dash.")
        form.addRow("Marker length", self.interval_size)

        # A peak area's shading, and whether it lets things show through.
        self.shade = QCheckBox("Shade the area")
        self.shade.setChecked(bool(analysis.shade))
        self.shade.setToolTip("Fill the area between the curve and the "
                              "baseline it was measured above.")
        self.opaque = StyleChoice(
            style.SHADINGS, getattr(analysis, "shading", None),
            lambda: style.SHADING_TITLES.get(
                style.inherited(self.doc, self.obj, "shading"), ""),
            parent=self, titles=style.SHADING_TITLES)
        self.opaque.setEnabled(bool(analysis.shade))
        self.opaque.setToolTip("Opaque: the colour the translucent shading "
                               "makes over the background, so nothing "
                               "behind it shows through.")
        area = analysis.slides
        form.addRow("", self.shade)
        form.addRow("Shading", self.opaque)
        for box in (self.shade, self.opaque):
            box.setVisible(area)
        form.labelForField(self.opaque).setVisible(area)

        self.text_size = _style_number(self, analysis, "label_size")
        self.text_size.setToolTip("Label size, pt.")
        form.addRow("Label size", self.text_size)

        self.flush = StyleChoice(
            style.FLUSHES, analysis.flush,
            lambda: style.FLUSH_TITLES[style.flush_for(
                analysis, style.inherited(self.doc, analysis, "flush"))],
            parent=self, titles=style.FLUSH_TITLES)
        self.flush.setToolTip("Which edge of the label sits on its arrow.")
        form.addRow("Alignment", self.flush)

        arrow_offset_rows(self, form)

        # A TEMPLATE: the words are the user's, `{}` is the measurement
        # (`core/labels.py`), so the number can never go stale or be typed.
        self.label = QLineEdit(analysis.label or "")
        self.label.setPlaceholderText(labels.default_template(analysis))
        self.label.setToolTip("Your text; {} is the measured value, on "
                              "the x axis, and {d} a peak's d-spacing: "
                              "(110) {}, *d* = {d}.")
        form.addRow("Label", self.label)

        self.number_format = _style_text(self, analysis, "number_format")
        form.addRow("Number format", self.number_format)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setToolTip("The label as drawn.")
        form.addRow("Shows", self.preview)

        form.addRow("Colour", _colour_button(
            self, lambda: (analysis.colour if analysis.colour != "auto"
                           else analysis.scan.colour), self._set_colour))
        self.auto = QCheckBox("Same as pattern")
        self.auto.setChecked(analysis.colour in (None, "", "auto"))
        self.auto.setToolTip("Use the pattern's colour.")
        form.addRow("", self.auto)

        self.note = QLabel("Drag the crosshairs to adjust the interval. "
                           "Hover over a field for an explanation.")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #9a9a9a;")
        layout.addWidget(self.note)

        buttons = self._buttons()
        layout.addWidget(buttons)

        self.visible.toggled.connect(self._apply)
        self.interval.toggled.connect(self._apply)
        self.interval_size.changed.connect(self._apply)
        self.shade.toggled.connect(self._apply)
        self.opaque.changed.connect(self._apply)
        self.text_size.changed.connect(self._apply)
        self.flush.changed.connect(self._apply)
        self.label.textChanged.connect(self._apply)
        self.number_format.changed.connect(self._apply)
        self.auto.toggled.connect(self._apply)
        self.model.currentIndexChanged.connect(self._model_chosen)
        self.arrow_offset.valueChanged.connect(self._typed_offset)
        self.arrow_default.toggled.connect(self._default_offset)
        self.arrow_shift.valueChanged.connect(self._shift_offset)
        self._refresh()
        self.resize(460, self.sizeHint().height())

    # ------------------------------------------------------------ showing
    def _refresh(self):
        """Everything that follows the analysis's numbers."""
        analysis = self.obj
        self.setWindowTitle(analysis.model_name + (
            "  (and {} more)".format(len(self.group)) if self.group else ""))
        self.label.setPlaceholderText(labels.default_template(analysis))
        cursors = analysis.cursors()
        for box, index in ((self.start, 0), (self.end, 1)):
            if len(cursors) == 2:
                box.setText("{} {}".format(numbers.write(cursors[index],
                                                         "%.5g"),
                                           _x_unit(self.doc)))
            box.setStyleSheet("")
        has_interval = len(cursors) == 2 and not self.group
        for widget in (self.start, self.end, self.model):
            widget.setEnabled(has_interval)
        found = labels.results(analysis, self.doc)
        self.results.setText("<br>".join(
            "{}: {}".format(html.escape(name), html.escape(text))
            for name, text in found) or "-")
        self._show_offset()
        self._show_preview()

    def _show_preview(self):
        """The label as it will be drawn, and anything wrong with it."""
        rendered = labels.render(self.obj, self.doc)
        lines = [markup_html(rendered.text)]
        for _kind, message in rendered.problems:
            lines.append("<span style='color:#e08030'>{}</span>".format(
                html.escape(message)))
        self.preview.setText("<br>".join(lines))
        if self.isVisible():
            self.fit()

    def adopt_measurement(self, old_label, new_label):
        """The analysis was measured again while this was open (its gizmos
        were moved, or its interval typed): show the new numbers."""
        if new_label != old_label:
            if self.label.text() == (old_label or ""):
                self.label.blockSignals(True)
                self.label.setText(new_label or "")
                self.label.blockSignals(False)
            if self._snapshot.get("label") == old_label:
                self._snapshot["label"] = new_label
        self._refresh()

    # ------------------------------------------------------------ editing
    def _typed_interval(self):
        """From or To typed: measure again on the new interval (the window
        makes it an undo step, as a gizmo drag does)."""
        cursors = self.obj.cursors()
        if len(cursors) != 2:
            return
        typed = []
        for box in (self.start, self.end):
            value = units.parse_position(box.text())
            if value is None:
                box.setStyleSheet("border: 1px solid #d04040;")
                return
            typed.append(value)
        if all(abs(a - b) < 5e-3 for a, b in zip(sorted(typed), cursors)):
            return
        window = _window_of(self)
        low, high = sorted(typed)
        if window is None or not window.retype_interval(self.obj, low, high):
            self.start.setStyleSheet("border: 1px solid #d04040;")
            self.end.setStyleSheet("border: 1px solid #d04040;")
            return
        self._refresh()

    def _model_chosen(self, _index=0):
        name = self.model.currentData()
        if not name or name == self.obj.model_name:
            return
        window = _window_of(self)
        if window is None or not window.change_model(self.obj, name):
            self.model.blockSignals(True)
            self.model.setCurrentIndex(
                self.model.findData(self.obj.model_name))
            self.model.blockSignals(False)
            return
        self._refresh()

    def _set_colour(self, name):
        self.obj.colour = name
        if hasattr(self, "auto"):
            self.auto.setChecked(False)
        self._live()

    # The label's distance from the curve: stored as the screen offset
    # (negative is above), shown the way the y axis reads, positive up.
    def _effective(self, analysis):
        if analysis.label_dy is not None:
            return float(analysis.label_dy)
        plot = _plot_of(self)
        return float(plot.effective_label_dy(analysis)) if plot else -46.0

    def _show_offset(self):
        boxes = (self.arrow_offset, self.arrow_default, self.arrow_shift)
        for box in boxes:
            box.blockSignals(True)
        self.arrow_offset.setValue(-self._effective(self.obj))
        self.arrow_default.setChecked(self.obj.label_dy is None)
        self.arrow_shift.setValue(0.0)
        for box in boxes:
            box.blockSignals(False)

    def _typed_offset(self, value):
        self.obj.label_dy = -float(value)
        self._live()
        self._show_offset()

    def _default_offset(self, on):
        self.obj.label_dy = None if on else self._effective(self.obj)
        self._live()
        self._show_offset()

    def _shift_offset(self, value):
        """Every analysis of the window, moved by the same amount."""
        if not value:
            return
        for analysis in [self.obj] + list(self.group):
            analysis.label_dy = self._effective(analysis) - float(value)
        self._last["label_dy"] = self.obj.label_dy   # moved, not mirrored
        self._live()
        self._show_offset()

    def _apply(self, *_args):
        analysis = self.obj
        analysis.visible = bool(self.visible.isChecked())
        analysis.show_interval = bool(self.interval.isChecked())
        analysis.interval_size = self.interval_size.value()
        analysis.shade = bool(self.shade.isChecked())
        analysis.shading = self.opaque.value()
        self.opaque.setEnabled(analysis.shade)
        analysis.label_size = self.text_size.value()
        analysis.flush = self.flush.value()
        analysis.label = self.label.text().strip() or None
        analysis.number_format = self.number_format.value()
        if self.auto.isChecked():
            analysis.colour = "auto"
        self._live()
        self._show_preview()


class LegendSettings(_LiveDialog):
    """The key: how big, how spaced, framed or not, and where."""

    FIELDS = ("visible", "size", "show_frame", "sample", "spacing", "colour",
              "x", "y", "space", "anchor", "rotation", "line_width")

    def __init__(self, parent, legend, on_change=None):
        _LiveDialog.__init__(self, parent, legend, on_change)
        self.setWindowTitle("Legend")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.visible = QCheckBox("Show the legend")
        self.visible.setChecked(bool(legend.visible))
        form.addRow("", self.visible)

        self.text_size = _style_number(self, legend, "size")
        form.addRow("Text size", self.text_size)

        self.sample = NumberBox()
        self.sample.setDecimals(0)
        self.sample.setRange(6.0, 80.0)
        self.sample.setValue(float(legend.sample))
        self.sample.setToolTip("Length of each colour sample.")
        form.addRow("Sample length", self.sample)

        self.spacing = NumberBox()
        self.spacing.setDecimals(2)
        self.spacing.setRange(0.8, 3.0)
        self.spacing.setSingleStep(0.05)
        self.spacing.setValue(float(legend.spacing))
        form.addRow("Line spacing", self.spacing)
        self.spacing.setToolTip("Row height, in lines.")

        self.line_width = NumberBox()
        self.line_width.setDecimals(2)
        self.line_width.setRange(0.0, 12.0)
        self.line_width.setSingleStep(0.25)
        self.line_width.setSpecialValueText("as the scans")
        self.line_width.setValue(float(legend.line_width or 0.0))
        self.line_width.setToolTip("Width of the colour samples; 0 uses "
                                   "each scan's own.")
        form.addRow("Line width", self.line_width)

        self.frame = QCheckBox("Box behind it")
        self.frame.setToolTip("A box behind the legend, for legibility.")
        self.frame.setChecked(bool(legend.show_frame))
        form.addRow("", self.frame)

        self.transform = ArtistTransform(legend, getattr(parent, "plot", None),
                                         on_change=self._live, parent=self)
        form.addRow("Place", self.transform)

        note = QLabel("Lists the shown scans, by their labels.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9a9a9a;")
        layout.addWidget(note)

        buttons = self._buttons()
        layout.addWidget(buttons)

        self.visible.toggled.connect(self._apply)
        self.frame.toggled.connect(self._apply)
        for box in (self.sample, self.spacing, self.line_width):
            box.valueChanged.connect(self._apply)
        self.text_size.changed.connect(self._apply)

    def _apply(self, *_args):
        legend = self.obj
        legend.visible = bool(self.visible.isChecked())
        legend.show_frame = bool(self.frame.isChecked())
        legend.size = self.text_size.value()
        legend.sample = float(self.sample.value())
        legend.spacing = float(self.spacing.value())
        legend.line_width = (float(self.line_width.value())
                             if self.line_width.value() > 0 else None)
        self._live()


class OffsetMarkerSettings(_LiveDialog):
    """A y-offset marker: its size, format, colour, and where it points.

    NOT the scan's offset: that belongs to the scan's own settings. The
    marker only says what it is.
    """

    FIELDS = ("size", "colour", "visible", "at", "dy", "number_format")

    def __init__(self, parent, marker, on_change=None):
        _LiveDialog.__init__(self, parent, marker, on_change)
        self.plot = getattr(parent, "plot", None) or _plot_of(parent)
        self.setWindowTitle("Offset marker: {}".format(
            marker.scan.display_name()))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.visible = QCheckBox("Show")
        self.visible.setChecked(bool(marker.visible))
        self.visible.setToolTip("Draw this marker.")
        form.addRow("", self.visible)

        self.text_size = _style_number(self, marker, "size")
        form.addRow("Size", self.text_size)

        self.number_format = _style_text(self, marker, "number_format")
        form.addRow("Number format", self.number_format)

        self.at = NumberBox()
        self.at.setDecimals(4)
        self.at.setRange(-1e6, 1e6)
        self.at.setSuffix(" " + _x_unit(self.doc))
        self.at.setToolTip("Position to point at. For a group, lines "
                           "them up.")
        self.at_auto = QCheckBox("Against the y axis")
        self.at_auto.setToolTip("Left end of the curve as shown.")
        form.addRow("Points at", self.at)
        form.addRow("", self.at_auto)

        arrow_offset_rows(self, form)

        form.addRow("Colour", _colour_button(
            self, lambda: (marker.colour if marker.colour != "auto"
                           else "#cccccc"), self._set_colour))
        self.auto = QCheckBox("Follow the theme")
        self.auto.setChecked(marker.colour in (None, "", "auto"))
        self.auto.setToolTip("Light on dark, dark on light.")
        form.addRow("", self.auto)

        buttons = self._buttons()
        layout.addWidget(buttons)
        self._show_place()

        self.visible.toggled.connect(self._apply)
        self.text_size.changed.connect(self._apply)
        self.number_format.changed.connect(self._apply)
        self.at.valueChanged.connect(self._typed_at)
        self.at_auto.toggled.connect(self._auto_at)
        self.arrow_offset.valueChanged.connect(self._typed_offset)
        self.arrow_default.toggled.connect(self._default_offset)
        self.arrow_shift.valueChanged.connect(self._shift_offset)
        self.auto.toggled.connect(self._apply)

    # The marker stores how far BELOW the curve its text starts; the
    # window says it the way the y axis does, positive up.
    def _effective(self, marker):
        if marker.dy is not None:
            return float(marker.dy)
        return float(self.plot.marker_dy(marker)) if self.plot else 0.0

    def _show_place(self):
        """The numbers as drawn, whether chosen or automatic."""
        marker = self.obj
        widgets = (self.at, self.at_auto, self.arrow_offset,
                   self.arrow_default, self.arrow_shift)
        for widget in widgets:
            widget.blockSignals(True)
        at = self.plot.marker_x(marker) if self.plot else None
        if at is None and marker.at and marker.at[0] == "x":
            at = marker.at[1]
        self.at.setValue(float(at or 0.0))
        self.at_auto.setChecked(marker.at is None)
        self.arrow_offset.setValue(-self._effective(marker))
        self.arrow_default.setChecked(marker.dy is None)
        self.arrow_shift.setValue(0.0)
        for widget in widgets:
            widget.blockSignals(False)

    def _typed_at(self, value):
        self.obj.at = ("x", float(value))
        self._apply()

    def _auto_at(self, on):
        if on:
            self.obj.at = None
        elif self.obj.at is None:
            self.obj.at = ("x", float(self.at.value()))
        self._apply()

    def _typed_offset(self, value):
        self.obj.dy = -float(value)
        self._apply()

    def _default_offset(self, on):
        self.obj.dy = None if on else self._effective(self.obj)
        self._apply()

    def _shift_offset(self, value):
        """Every marker of the window, moved by the same amount - not set
        to the same value, so a group keeps its differences."""
        if not value:
            return
        for marker in [self.obj] + list(self.group):
            marker.dy = self._effective(marker) - float(value)
        self._last["dy"] = self.obj.dy          # moved, not to be mirrored
        self._apply()

    def _set_colour(self, name):
        self.obj.colour = name
        if hasattr(self, "auto"):
            self.auto.setChecked(False)
        self._live()

    def _apply(self, *_args):
        marker = self.obj
        marker.visible = bool(self.visible.isChecked())
        marker.size = self.text_size.value()
        marker.number_format = self.number_format.value()
        if self.auto.isChecked():
            marker.colour = "auto"
        self._live()
        self._show_place()


class RegionSettings(_LiveDialog):
    """A highlighted or magnified stretch of the x axis: where, how it is
    shaded, which patterns it magnifies and by how much, and its text."""

    FIELDS = ("lo", "hi", "shade", "opacity", "factor", "scans", "text",
              "colour", "size", "y", "rotation", "anchor", "visible")
    INDIVIDUAL = ("lo", "hi", "scans", "text", "y")
    GROUP_DISABLED = ("low", "high", "text")
    LAYERED = True

    def __init__(self, parent, region, on_change=None):
        _LiveDialog.__init__(self, parent, region, on_change)
        self.setWindowTitle("Region")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.visible = QCheckBox("Show")
        self.visible.setChecked(bool(region.visible))
        form.addRow("", self.visible)

        self.low = NumberBox()
        self.high = NumberBox()
        for box, value in ((self.low, region.lo), (self.high, region.hi)):
            box.setDecimals(4)
            box.setRange(0.0, 1e6)
            box.setSuffix(" " + _x_unit(self.doc))
            box.setValue(float(value))
        stretch = QWidget(self)
        row = QHBoxLayout(stretch)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.low, 1)
        row.addWidget(QLabel("to"), 0)
        row.addWidget(self.high, 1)
        form.addRow("From", stretch)

        self.shade = QCheckBox("Shade it")
        self.shade.setChecked(bool(region.shade))
        self.shade.setToolTip("Across the whole height of the plot, behind "
                              "the patterns.")
        form.addRow("", self.shade)
        self.opacity = NumberBox()
        self.opacity.setDecimals(2)
        self.opacity.setRange(0.0, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setValue(float(region.opacity))
        self.opacity.setToolTip("How strongly it covers the page.")
        form.addRow("Opacity", self.opacity)

        self.factor = NumberBox()
        self.factor.setDecimals(2)
        self.factor.setRange(0.05, 1000.0)
        self.factor.setValue(float(region.factor))
        self.factor.setToolTip("How many times the chosen patterns are "
                               "magnified here, about their own baseline "
                               "across the stretch; 1 is not at all.")
        form.addRow("Magnify by", self.factor)
        self.scan_list = QListWidget(self)
        self.scan_list.setMaximumHeight(110)
        self.scan_list.setToolTip("The patterns it magnifies.")
        doc = self.doc
        for scan in (doc.scans if doc is not None else region.scans):
            item = QListWidgetItem(scan.display_name())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if any(s is scan
                                                 for s in region.scans)
                               else Qt.Unchecked)
            item.setData(Qt.UserRole, scan)
            self.scan_list.addItem(item)
        form.addRow("Patterns", self.scan_list)

        # Several lines, as a label's: Enter breaks one, Tab goes on.
        self.text = QPlainTextEdit(region.text or "")
        self.text.setTabChangesFocus(True)
        self.text.setFixedHeight(3 * self.fontMetrics().lineSpacing() + 14)
        self.text.setPlaceholderText(region.shown_text() or "no text")
        self.text.setToolTip("Its words over the stretch. Empty: none - or, "
                             "magnifying, its factor (x3).")
        form.addRow("Text", self.text)
        self.text_size = _style_number(self, region, "size")
        form.addRow("Text size", self.text_size)
        self.place_y = NumberBox()
        self.place_y.setDecimals(3)
        self.place_y.setRange(0.0, 1.0)
        self.place_y.setSingleStep(0.01)
        self.place_y.setValue(float(region.y))
        self.place_y.setToolTip("Where the text stands, as a share of the "
                               "plot from its top. Drag it too.")
        form.addRow("Text height", self.place_y)

        form.addRow("Colour", _colour_button(
            self, lambda: (region.colour if region.colour != "auto"
                           else "#808080"), self._set_colour))
        self.auto = QCheckBox("Automatic")
        self.auto.setChecked(region.colour in (None, "", "auto"))
        self.auto.setToolTip("Grey shading; the text in the magnified "
                             "pattern's colour, else the theme's ink.")
        form.addRow("", self.auto)

        buttons = self._buttons()
        layout.addWidget(buttons)
        self.visible.toggled.connect(self._apply)
        self.shade.toggled.connect(self._apply)
        self.auto.toggled.connect(self._apply)
        for box in (self.low, self.high, self.opacity, self.factor,
                    self.place_y):
            box.valueChanged.connect(self._apply)
        self.text.textChanged.connect(self._apply)
        self.text_size.changed.connect(self._apply)
        self.scan_list.itemChanged.connect(self._apply)
        self.resize(420, self.sizeHint().height())

    def _set_colour(self, name):
        self.obj.colour = name
        self.auto.setChecked(False)
        self._live()

    def _apply(self, *_args):
        region = self.obj
        region.visible = bool(self.visible.isChecked())
        low, high = sorted((float(self.low.value()), float(self.high.value())))
        if high > low:
            region.lo, region.hi = low, high
        region.shade = bool(self.shade.isChecked())
        region.opacity = float(self.opacity.value())
        region.factor = float(self.factor.value())
        region.scans = [self.scan_list.item(k).data(Qt.UserRole)
                        for k in range(self.scan_list.count())
                        if self.scan_list.item(k).checkState() == Qt.Checked]
        region.text = self.text.toPlainText()
        region.size = self.text_size.value()
        region.y = float(self.place_y.value())
        if self.auto.isChecked():
            region.colour = "auto"
        for scan in (self.doc.scans if self.doc is not None else ()):
            scan._cache_key = None
        self._live()


class SpanSettings(_LiveDialog):
    """A distance arrow: its ends, its height, and its text."""

    FIELDS = ("x0", "x1", "ends", "y", "text", "place", "colour", "size",
              "head", "line_width", "number_format", "visible")
    INDIVIDUAL = ("x0", "x1", "ends", "y", "text")
    GROUP_DISABLED = ("first", "second", "text")
    LAYERED = True

    def __init__(self, parent, span, on_change=None):
        _LiveDialog.__init__(self, parent, span, on_change)
        self.setWindowTitle("Distance arrow")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.visible = QCheckBox("Show")
        self.visible.setChecked(bool(span.visible))
        form.addRow("", self.visible)

        a, b = span.end_values()
        self.first = NumberBox()
        self.second = NumberBox()
        for box, value, end in ((self.first, a, span.ends[0]),
                                (self.second, b, span.ends[1])):
            box.setDecimals(4)
            box.setRange(0.0, 1e6)
            box.setSuffix(" " + _x_unit(self.doc))
            box.setValue(float(value))
            if end is not None:
                box.setEnabled(False)
                box.setToolTip("On its marker line: moves with it.")
        form.addRow("From", self.first)
        form.addRow("To", self.second)
        tied = [end for end in span.ends if end is not None]
        self.untie = QCheckBox("Keep the ends on their marker lines")
        self.untie.setChecked(bool(tied))
        self.untie.setEnabled(bool(tied))
        self.untie.setToolTip("Unticked, the ends stay where they are when "
                              "the markers move.")
        form.addRow("", self.untie)

        self.text = QLineEdit(span.text or "")
        self.text.setPlaceholderText(labels.span_text(span, self.doc))
        self.text.setToolTip("{} is the distance, on the x axis. Empty: "
                             "Delta and the axis's symbol.")
        form.addRow("Text", self.text)
        self.place = QComboBox()
        for name, words in (("above", "above the arrow"),
                            ("below", "below the arrow"),
                            ("on", "upright, on the arrow")):
            self.place.addItem(words, name)
        self.place.setCurrentIndex(max(0, self.place.findData(span.place)))
        form.addRow("Text", self.place)
        self.text_size = _style_number(self, span, "size")
        form.addRow("Text size", self.text_size)
        self.number_format = _style_text(self, span, "number_format")
        form.addRow("Number format", self.number_format)
        self.place_y = NumberBox()
        self.place_y.setDecimals(3)
        self.place_y.setRange(0.0, 1.0)
        self.place_y.setSingleStep(0.01)
        self.place_y.setValue(float(span.y))
        self.place_y.setToolTip("Where it stands, as a share of the plot "
                               "from its top. Drag it too.")
        form.addRow("Height", self.place_y)
        self.head = NumberBox()
        self.head.setDecimals(1)
        self.head.setRange(1.0, 60.0)
        self.head.setValue(float(span.head))
        self.head.setToolTip("The arrowheads' size.")
        form.addRow("Heads", self.head)
        self.line_box = NumberBox()
        self.line_box.setDecimals(2)
        self.line_box.setRange(0.1, 10.0)
        self.line_box.setSingleStep(0.1)
        self.line_box.setValue(float(span.line_width))
        form.addRow("Line width", self.line_box)
        form.addRow("Colour", _colour_button(
            self, lambda: (span.colour if span.colour != "auto"
                           else "#cccccc"), self._set_colour))
        self.auto = QCheckBox("Follow the theme")
        self.auto.setChecked(span.colour in (None, "", "auto"))
        form.addRow("", self.auto)

        buttons = self._buttons()
        layout.addWidget(buttons)
        self.visible.toggled.connect(self._apply)
        self.untie.toggled.connect(self._apply)
        self.auto.toggled.connect(self._apply)
        for box in (self.first, self.second, self.place_y, self.head,
                    self.line_box):
            box.valueChanged.connect(self._apply)
        self.text.textChanged.connect(self._apply)
        self.place.currentIndexChanged.connect(self._apply)
        self.text_size.changed.connect(self._apply)
        self.number_format.changed.connect(self._apply)
        self.resize(420, self.sizeHint().height())

    def _set_colour(self, name):
        self.obj.colour = name
        self.auto.setChecked(False)
        self._live()

    def _apply(self, *_args):
        span = self.obj
        span.visible = bool(self.visible.isChecked())
        if not self.untie.isChecked() and any(span.ends):
            # Untied: each end stays where its marker put it.
            span.x0, span.x1 = span.end_values()
            span.ends = [None, None]
            for box in (self.first, self.second):
                box.setEnabled(True)
                box.setToolTip("")
            self.untie.setEnabled(False)
        if span.ends[0] is None:
            span.x0 = float(self.first.value())
        if span.ends[1] is None:
            span.x1 = float(self.second.value())
        span.text = self.text.text() or None
        span.place = self.place.currentData()
        span.size = self.text_size.value()
        span.number_format = self.number_format.value()
        span.y = float(self.place_y.value())
        span.head = float(self.head.value())
        span.line_width = float(self.line_box.value())
        if self.auto.isChecked():
            span.colour = "auto"
        self._live()


class BreakDialog(QDialog):
    """The x axis's break in numbers: where, how narrow the squeezed
    stretch, how wide the gap in the frame."""

    def __init__(self, parent, high, low, compress=0.02, gap=7.0, unit=""):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Break the x axis")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.high = NumberBox(self)
        self.low = NumberBox(self)
        for box, value in ((self.low, low), (self.high, high)):
            box.setDecimals(3)
            box.setRange(0.0, 1e6)
            box.setSuffix(" " + unit if unit else "")
            box.setValue(float(value))
        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(self.low, 1)
        line.addWidget(QLabel("to"), 0)
        line.addWidget(self.high, 1)
        form.addRow("Cut out", row)
        self.compress = NumberBox(self)
        self.compress.setDecimals(3)
        self.compress.setRange(0.001, 1.0)
        self.compress.setSingleStep(0.01)
        self.compress.setValue(float(compress))
        self.compress.setToolTip("How much of the stretch is left, as a "
                                 "share of its width: 0.02 a hairline, 0.15 "
                                 "a visibly squeezed stretch.")
        form.addRow("Left of it", self.compress)
        self.gap = NumberBox(self)
        self.gap.setDecimals(1)
        self.gap.setRange(0.0, 60.0)
        self.gap.setSuffix(" pt")
        self.gap.setValue(float(gap))
        self.gap.setToolTip("The gap cut into the frame, with the two "
                            "slashes across it.")
        form.addRow("Gap", self.gap)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {"lo": float(self.low.value()), "hi": float(self.high.value()),
                "compress": float(self.compress.value()),
                "gap": float(self.gap.value())}

    def accept(self):
        if float(self.low.value()) == float(self.high.value()):
            for box in (self.low, self.high):
                box.setStyleSheet("border: 1px solid #d04040;")
            return
        QDialog.accept(self)


class ExportDialog(QDialog):
    """Where the figure goes, and in which colours.

    The system's save dialog has no room for a choice of its own, so this
    is the export: a file (Browse opens the system dialog for it), and the
    colours - light, for a page, or as the theme on screen.
    """

    def __init__(self, parent, path, light=True, size_text=""):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Export the figure")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        self.path_edit = QLineEdit(path, self)
        self.path_edit.setMinimumWidth(320)
        self.path_edit.setToolTip("A .svg or a .png file.")
        browse = QPushButton("Browse...", self)
        browse.setAutoDefault(False)
        browse.clicked.connect(lambda _c=False: self._browse())
        line.addWidget(self.path_edit, 1)
        line.addWidget(browse, 0)
        form.addRow("File", row)

        self.colours = QComboBox(self)
        self.colours.addItem("Light, for a page", True)
        self.colours.addItem("Same as the theme", False)
        self.colours.setCurrentIndex(0 if light else 1)
        self.colours.setToolTip("Light is dark ink on white; the theme is "
                                "what the screen shows.")
        form.addRow("Colours", self.colours)
        if size_text:
            form.addRow("Size", QLabel(size_text, self))

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        from PySide6.QtWidgets import QFileDialog
        path, _f = QFileDialog.getSaveFileName(
            self, "Export the figure", self.path_edit.text(),
            "SVG image (*.svg);;PNG image (*.png)")
        if path:
            self.path_edit.setText(path)

    def values(self):
        """`(path, light)`, or None without a file name."""
        path = self.path_edit.text().strip()
        if not path:
            return None
        if os.path.splitext(path)[1].lower() not in (".svg", ".png"):
            path += ".svg"
        return path, bool(self.colours.currentData())


def install_basic_colours():
    """The colour picker's 48 basic colours, read left to right and top to
    bottom: the house line colours first, in their order. Qt numbers
    the grid down its six rows first, so the reading order is mapped onto
    that."""
    colours = (PLOTTER_COLOURS + MORE_COLOURS)[:48]
    for order, name in enumerate(colours):
        row, column = divmod(order, 8)
        QColorDialog.setStandardColor(row + column * 6, QColor(name))
    return len(colours)


class ImageSettings(_LiveDialog):
    """A picture on the figure: shown or not, its width, where it sits."""

    FIELDS = ("width", "visible", "x", "y", "space", "anchor", "rotation")

    def __init__(self, parent, image, on_change=None):
        _LiveDialog.__init__(self, parent, image, on_change)
        self.setWindowTitle("Image")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.shown = QCheckBox("Show")
        self.shown.setChecked(bool(image.visible))
        self.shown.setToolTip("Draw the image.")
        form.addRow("", self.shown)

        self.picture_width = NumberBox()
        self.picture_width.setDecimals(1)
        self.picture_width.setRange(4.0, 4000.0)
        self.picture_width.setSuffix(" px")
        self.picture_width.setValue(float(image.width))
        self.picture_width.setToolTip("Drawn width; the height keeps the "
                                      "picture's proportions. S scales it "
                                      "by hand.")
        form.addRow("Width", self.picture_width)

        self.transform = ArtistTransform(image, getattr(parent, "plot", None),
                                         on_change=self._live, parent=self)
        form.addRow("Place", self.transform)

        buttons = self._buttons()
        layout.addWidget(buttons)
        self.shown.toggled.connect(self._apply)
        self.picture_width.valueChanged.connect(self._apply)

    def _apply(self, *_args):
        self.obj.visible = bool(self.shown.isChecked())
        self.obj.width = float(self.picture_width.value())
        self._live()


class MoleculeSettings(_LiveDialog):
    """A skeletal structure: its SMILES, its bonds and labels, its colour,
    and where it sits. The sizes start at the ACS 1996 document style's."""

    FIELDS = ("visible", "colour", "bond_length", "bond_width", "label_size",
              "upright_labels", "x", "y", "space", "anchor", "rotation",
              "smiles", "atoms", "bonds", "label_font", "colour_by_element")
    INDIVIDUAL = ("x", "y", "space", "smiles", "atoms", "bonds")
    GROUP_DISABLED = ("smiles_edit", "transform.space", "transform.at_x",
                      "transform.at_y")

    def __init__(self, parent, molecule, on_change=None):
        _LiveDialog.__init__(self, parent, molecule, on_change)
        self.setWindowTitle("Structure")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)

        self.shown = QCheckBox("Show")
        self.shown.setChecked(bool(molecule.visible))
        self.shown.setToolTip("Draw the structure.")
        form.addRow("", self.shown)

        self.smiles_edit = QLineEdit(molecule.smiles, self)
        self.smiles_edit.setToolTip("Type another SMILES and press Enter to "
                                    "redraw it (needs RDKit).")
        form.addRow("SMILES", self.smiles_edit)

        def size(low, high, step, suffix):
            box = NumberBox()
            box.setDecimals(2)
            box.setRange(low, high)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            return box

        self.bond_length = size(2.0, 400.0, 1.0, " px")
        self.bond_length.setValue(float(molecule.bond_length))
        self.bond_length.setToolTip("Bond length; ACS style is 19.2 px "
                                    "(0.2 inch).")
        form.addRow("Bond length", self.bond_length)
        self.bond_width = size(0.1, 20.0, 0.1, " px")
        self.bond_width.setValue(float(molecule.bond_width))
        self.bond_width.setToolTip("Line width of the bonds; ACS style is "
                                   "0.8 px (0.6 pt).")
        form.addRow("Bond width", self.bond_width)
        self.label_size = size(3.0, 72.0, 0.5, " pt")
        self.label_size.setValue(float(molecule.label_size))
        self.label_size.setToolTip("Size of the element labels.")
        form.addRow("Label size", self.label_size)

        self.label_font = FontChoice(
            molecule.label_font,
            lambda: (style.inherited(self.doc, molecule, "label_font")
                     or style.figure_value(self.doc, "font_family") or ""),
            parent=self)
        self.label_font.setToolTip("The element labels' typeface; Default "
                                   "is the house style's (Settings, "
                                   "Structure labels).")
        form.addRow("Label font", self.label_font)

        self.by_element = QCheckBox("Colour by element")
        self.by_element.setChecked(bool(molecule.colour_by_element))
        self.by_element.setToolTip("N blue, O red, S yellow...; the bonds "
                                   "keep the colour below.")
        form.addRow("", self.by_element)

        self.upright = QCheckBox("Labels stay upright when rotated")
        self.upright.setChecked(bool(molecule.upright_labels))
        self.upright.setToolTip("Off: the labels turn with the structure.")
        form.addRow("", self.upright)

        form.addRow("Colour", _colour_button(
            self, lambda: (molecule.colour if molecule.colour != "auto"
                           else "#cccccc"), self._set_colour))
        self.auto = QCheckBox("Follow the theme")
        self.auto.setChecked(molecule.colour in (None, "", "auto"))
        self.auto.setToolTip("Light on dark, dark on light.")
        form.addRow("", self.auto)

        self.transform = ArtistTransform(molecule,
                                         getattr(parent, "plot", None),
                                         on_change=self._live, parent=self)
        form.addRow("Place", self.transform)

        buttons = self._buttons()
        layout.addWidget(buttons)
        self.shown.toggled.connect(self._apply)
        for box in (self.bond_length, self.bond_width, self.label_size):
            box.valueChanged.connect(self._apply)
        self.upright.toggled.connect(self._apply)
        self.by_element.toggled.connect(self._apply)
        self.label_font.changed.connect(self._apply)
        self.auto.toggled.connect(self._apply)
        self.smiles_edit.editingFinished.connect(self._new_smiles)

    def _new_smiles(self):
        from ..core import chem
        text = self.smiles_edit.text().strip()
        if text == self.obj.smiles:
            return
        drawing = chem.layout(text)
        if drawing is None:
            self.smiles_edit.setStyleSheet("border: 1px solid #d04040;")
            return
        self.smiles_edit.setStyleSheet("")
        self.obj.smiles = text
        self.obj.atoms = drawing["atoms"]
        self.obj.bonds = drawing["bonds"]
        self._live()

    def _set_colour(self, name):
        self.obj.colour = name
        if hasattr(self, "auto"):
            self.auto.setChecked(False)
        self._live()

    def _apply(self, *_args):
        molecule = self.obj
        molecule.visible = bool(self.shown.isChecked())
        molecule.bond_length = float(self.bond_length.value())
        molecule.bond_width = float(self.bond_width.value())
        molecule.label_size = float(self.label_size.value())
        molecule.upright_labels = bool(self.upright.isChecked())
        molecule.colour_by_element = bool(self.by_element.isChecked())
        molecule.label_font = self.label_font.value() or None
        if self.auto.isChecked():
            molecule.colour = "auto"
        self._live()


# The windows of objects with a place in the figure's stack show it: a
# Layer field (`_LiveDialog._layer_row`).
for _kind in (ScanSettings, AnalysisSettings, LabelSettings, LegendSettings,
              OffsetMarkerSettings, ImageSettings, MoleculeSettings,
              RegionSettings, SpanSettings):
    _kind.LAYERED = True

# Qt calls the handlers here by itself; an error in one is logged and
# survived rather than the end of the program (`core/log.py`).
from ..core import log as _log
_log.guard_classes(globals(), __name__)
