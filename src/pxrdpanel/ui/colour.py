"""The colour picker: a hue wheel, a value bar, typed numbers, a dropper.

Qt's own picker has a bar that runs to DARKER shades,
while the usual wish is a saturated colour made paler. Here the wheel is
hue by angle and saturation by radius with WHITE at the centre, so paler is
a move inwards, and darker is the bar beside it. Every number box takes a
sum ("255-20", `numbers.evaluate`); the hex field does not, because a sum
on a whole colour says nothing about which channel it means.

`get_colour` is the entry point, like `QColorDialog.getColor`: modal,
returning an invalid QColor when nothing was chosen. Like every pop-up here
it keeps its change however it is closed; Revert puts back the colour it
opened with.
"""

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QImage,
                           QLinearGradient, QPainter, QPen)
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..core import shades
from .numbox import NumberBox, WholeBox

#: The house line colours, in their order: what the colour picker's basic
#: colours start with - three blues and three reds (dark to light), a grey
#: and black, then the named ones.
PLOTTER_COLOURS = ("#002b6d", "#0048b6", "#0065ff", "#6d001a", "#b6002b",
                   "#ff003c", "#6b7280", "#000000", "#0000ff", "#008000",
                   "#ff0000", "#ffa500", "#ff00ff", "#d2691e", "#008080",
                   "#800080")
#: And after them: the panel's own screen colours, Tableau's ten,
#: Okabe and Ito's colour-blind-safe set, and greys.
MORE_COLOURS = ("#6ea8ff", "#ffb04e", "#7fd08a", "#e07b7b", "#c79bef",
                "#4fd0c8", "#d8d16a", "#f08ac0",
                "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
                "#e69f00", "#56b4e9", "#009e73", "#f0e442", "#0072b2",
                "#d55e00", "#cc79a7",
                "#ffffff", "#e0e0e0", "#c0c0c0", "#a0a0a0", "#606060",
                "#404040", "#202020")


def _hsv_to_rgb(h, s, v):
    """Vectorised HSV (all 0..1) to RGB (0..1)."""
    h6 = (np.asarray(h, dtype=float) * 6.0) % 6.0
    i = np.floor(h6).astype(int) % 6
    f = h6 - np.floor(h6)
    v = np.broadcast_to(np.asarray(v, dtype=float), h6.shape)
    s = np.broadcast_to(np.asarray(s, dtype=float), h6.shape)
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    red = np.choose(i, [v, q, p, p, t, v])
    green = np.choose(i, [t, v, v, q, p, p])
    blue = np.choose(i, [p, p, t, v, v, q])
    return red, green, blue


def hsv_image(size, value):
    """The hue/saturation disc at `value`, `size` pixels square: hue by
    angle (red to the right, counter-clockwise), saturation by radius,
    transparent outside, antialiased at the rim."""
    n = max(int(size), 2)
    centre = (n - 1) / 2.0
    radius = n / 2.0 - 1.0
    ys, xs = np.mgrid[0:n, 0:n].astype(float)
    dx, dy = xs - centre, centre - ys
    r = np.hypot(dx, dy)
    h = (np.degrees(np.arctan2(dy, dx)) % 360.0) / 360.0
    s = np.clip(r / radius, 0.0, 1.0)
    red, green, blue = _hsv_to_rgb(h, s, float(value))
    alpha = np.clip(radius + 0.5 - r, 0.0, 1.0)
    # Premultiplied, so the rim blends rather than fringes.
    argb = np.empty((n, n, 4), dtype=np.uint8)
    argb[..., 0] = np.round(blue * alpha * 255)
    argb[..., 1] = np.round(green * alpha * 255)
    argb[..., 2] = np.round(red * alpha * 255)
    argb[..., 3] = np.round(alpha * 255)
    data = argb.tobytes()
    image = QImage(data, n, n, 4 * n, QImage.Format_ARGB32_Premultiplied)
    return image.copy()


class HueWheel(QWidget):
    """Hue by angle, saturation by radius (white in the middle)."""

    picked = Signal(float, float)          # hue 0..1, saturation 0..1

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.h, self.s, self.v = 0.0, 0.0, 1.0
        self._cache = None
        self.setCursor(Qt.CrossCursor)

    def sizeHint(self):
        return QSize(230, 230)

    def set_hsv(self, h, s, v):
        self.h, self.s, self.v = h, s, v
        self.update()

    def _disc(self):
        side = float(min(self.width(), self.height()))
        return QRectF((self.width() - side) / 2.0,
                      (self.height() - side) / 2.0, side, side)

    def point_of(self, h, s):
        """Where (h, s) is on the wheel, in widget pixels."""
        disc = self._disc()
        radius = disc.width() / 2.0 - 1.0
        angle = h * 2.0 * math.pi
        return QPointF(disc.center().x() + math.cos(angle) * s * radius,
                       disc.center().y() - math.sin(angle) * s * radius)

    def paintEvent(self, _ev):
        disc = self._disc()
        side = int(disc.width())
        key = (side, round(self.v * 255))
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, hsv_image(side, self.v))
        p = QPainter(self)
        p.drawImage(disc.topLeft(), self._cache[1])
        p.setRenderHint(QPainter.Antialiasing, True)
        at = self.point_of(self.h, self.s)
        dark = QColor(0, 0, 0)
        light = QColor(255, 255, 255)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(dark if self.v > 0.5 else light, 1.5))
        p.drawEllipse(at, 6.0, 6.0)
        p.setPen(QPen(light if self.v > 0.5 else dark, 1.0))
        p.drawEllipse(at, 7.5, 7.5)
        p.end()

    def pick(self, pos):
        disc = self._disc()
        radius = max(disc.width() / 2.0 - 1.0, 1.0)
        dx = pos.x() - disc.center().x()
        dy = disc.center().y() - pos.y()
        s = min(math.hypot(dx, dy) / radius, 1.0)
        h = (math.degrees(math.atan2(dy, dx)) % 360.0) / 360.0
        if s == 0.0:
            h = self.h                     # the centre keeps its hue
        self.h, self.s = h, s
        self.update()
        self.picked.emit(h, s)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.pick(ev.position())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton:
            self.pick(ev.position())


class ValueBar(QWidget):
    """The value (brightness): full at the top, black at the bottom."""

    picked = Signal(float)

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.setFixedWidth(26)
        self.setMinimumHeight(200)
        self.h, self.s, self.v = 0.0, 0.0, 1.0
        self.setCursor(Qt.CrossCursor)

    def set_hsv(self, h, s, v):
        self.h, self.s, self.v = h, s, v
        self.update()

    def _bar(self):
        return QRectF(4, 6, self.width() - 8, self.height() - 12)

    def paintEvent(self, _ev):
        bar = self._bar()
        p = QPainter(self)
        gradient = QLinearGradient(bar.topLeft(), bar.bottomLeft())
        gradient.setColorAt(0.0, QColor.fromHsvF(self.h, self.s, 1.0))
        gradient.setColorAt(1.0, QColor(0, 0, 0))
        p.fillRect(bar, gradient)
        p.setRenderHint(QPainter.Antialiasing, True)
        y = bar.top() + (1.0 - self.v) * bar.height()
        p.setPen(QPen(self.palette().text().color(), 1.5))
        p.drawLine(QPointF(0, y), QPointF(bar.left() + 5, y))
        p.drawLine(QPointF(bar.right() - 5, y), QPointF(self.width(), y))
        p.end()

    def pick(self, pos):
        bar = self._bar()
        v = 1.0 - (pos.y() - bar.top()) / max(bar.height(), 1.0)
        self.v = min(max(v, 0.0), 1.0)
        self.update()
        self.picked.emit(self.v)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.pick(ev.position())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton:
            self.pick(ev.position())


class Swatch(QToolButton):
    """One colour to click."""

    def __init__(self, colour, parent=None, size=18):
        QToolButton.__init__(self, parent)
        self.setFixedSize(size, size)
        self.set_colour(colour)

    def set_colour(self, colour):
        self.colour = QColor(colour)
        self.setToolTip(self.colour.name())
        self.setStyleSheet("QToolButton {{ background: {}; border: 1px solid "
                           "#555; }}".format(self.colour.name()))


def screen_colour(pos=None):
    """The colour on screen under `pos` (global; the pointer's if None)."""
    pos = QCursor.pos() if pos is None else pos
    screen = QGuiApplication.screenAt(pos)
    if screen is None:
        return None
    origin = screen.geometry().topLeft()
    pixmap = screen.grabWindow(0, pos.x() - origin.x(), pos.y() - origin.y(),
                               1, 1)
    image = pixmap.toImage()
    if image.isNull():
        return None
    return QColor(image.pixel(0, 0))


class ColourDialog(QDialog):
    """The picker. `colour()` is the chosen one; `live(name)`, when given,
    hears every change as it happens, so the figure follows the wheel."""

    changed = Signal(str)

    def __init__(self, initial, parent=None, title="Pick a colour",
                 live=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(title)
        initial = QColor(initial)
        self._initial = initial if initial.isValid() else QColor(255, 255,
                                                                 255)
        self._live = live
        self._picking = None
        self._busy = False
        self._reverted = False
        self.h = max(self._initial.hsvHueF(), 0.0)
        self.s = self._initial.hsvSaturationF()
        self.v = self._initial.valueF()

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        layout.addLayout(top)
        self.wheel = HueWheel(self)
        self.wheel.setToolTip("Hue round the wheel, saturation outwards: "
                              "white in the middle, so paler is inwards.")
        self.bar = ValueBar(self)
        self.bar.setToolTip("Value: full at the top, black at the bottom.")
        top.addWidget(self.wheel, 1)
        top.addWidget(self.bar)

        side = QVBoxLayout()
        top.addLayout(side)
        grid = QGridLayout()
        side.addLayout(grid)
        self.boxes = {}
        rows = (("R", 255, ""), ("G", 255, ""), ("B", 255, ""),
                ("H", 359, "\u00b0"), ("S", 100, " %"), ("V", 100, " %"))
        for row, (name, high, suffix) in enumerate(rows):
            box = WholeBox(self)
            box.setRange(0, high)
            box.setWrapping(name == "H")
            box.setSuffix(suffix)
            box.setToolTip("A number or a sum: 255-20.")
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(box, row, 1)
            box.valueChanged.connect(
                (lambda n: lambda _v: self._typed(n))(name))
            self.boxes[name] = box
        grid.addWidget(QLabel("Hex"), len(rows), 0)
        self.hex = QLineEdit(self)
        self.hex.setMaxLength(7)
        self.hex.setToolTip("#rrggbb, rrggbb or #rgb.")
        self.hex.editingFinished.connect(self._typed_hex)
        grid.addWidget(self.hex, len(rows), 1)

        self.dropper = QPushButton("Pick from screen", self)
        self.dropper.setToolTip("Then click anywhere on the screen to take "
                                "its colour. Esc or the right button "
                                "stops.")
        self.dropper.setAutoDefault(False)
        self.dropper.clicked.connect(lambda _c=False: self.start_picking())
        side.addWidget(self.dropper)

        compare = QHBoxLayout()
        self.before = Swatch(self._initial, self, 34)
        self.before.setToolTip("As it was. Click to go back to it.")
        self.before.clicked.connect(
            lambda _c=False: self.set_colour(self._initial))
        self.after = Swatch(self._initial, self, 34)
        self.after.setToolTip("As it is now.")
        compare.addWidget(QLabel("Was"))
        compare.addWidget(self.before)
        compare.addWidget(QLabel("Now"))
        compare.addWidget(self.after)
        compare.addStretch(1)
        side.addLayout(compare)
        side.addStretch(1)

        swatches = QGridLayout()
        swatches.setSpacing(3)
        for order, name in enumerate(PLOTTER_COLOURS + MORE_COLOURS):
            swatch = Swatch(name, self)
            swatch.clicked.connect(
                (lambda c: lambda _c=False: self.set_colour(c))(name))
            swatches.addWidget(swatch, order // 16, order % 16)
        layout.addLayout(swatches)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        revert = buttons.button(QDialogButtonBox.Cancel)
        revert.setText("Revert")
        revert.setToolTip("Put back the colour it had and close. Closing "
                          "otherwise keeps the new one.")
        for button in buttons.buttons():
            button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.revert)
        layout.addWidget(buttons)

        self.wheel.picked.connect(self._wheel_picked)
        self.bar.picked.connect(self._bar_picked)
        self._show(tell=False)

    # -------------------------------------------------------------- state
    def colour(self):
        return QColor.fromHsvF(self.h, self.s, self.v)

    def set_colour(self, colour):
        colour = QColor(colour)
        if not colour.isValid():
            return
        hue = colour.hsvHueF()
        # A grey has no hue: keep the one there was, so the wheel does not
        # jump to red on the way through white.
        if hue >= 0:
            self.h = hue
        self.s, self.v = colour.hsvSaturationF(), colour.valueF()
        self._show()

    def _show(self, tell=True):
        """Every widget from (h, s, v), and the colour to whoever listens."""
        self._busy = True
        try:
            colour = self.colour()
            self.wheel.set_hsv(self.h, self.s, self.v)
            self.bar.set_hsv(self.h, self.s, self.v)
            for name, value in (("R", colour.red()), ("G", colour.green()),
                                ("B", colour.blue()),
                                ("H", int(round(self.h * 360)) % 360),
                                ("S", int(round(self.s * 100))),
                                ("V", int(round(self.v * 100)))):
                self.boxes[name].setValue(value)
            self.hex.setText(colour.name())
            self.hex.setStyleSheet("")
            self.after.set_colour(colour)
        finally:
            self._busy = False
        if tell:
            name = self.colour().name()
            self.changed.emit(name)
            if self._live is not None:
                self._live(name)

    def _wheel_picked(self, h, s):
        self.h, self.s = h, s
        self._show()

    def _bar_picked(self, v):
        self.v = v
        self._show()

    def _typed(self, name):
        if self._busy:
            return
        b = self.boxes
        if name in "RGB":
            self.set_colour(QColor(b["R"].value(), b["G"].value(),
                                   b["B"].value()))
            return
        self.h = (b["H"].value() % 360) / 360.0
        self.s = b["S"].value() / 100.0
        self.v = b["V"].value() / 100.0
        self._show()

    def _typed_hex(self):
        text = self.hex.text().strip()
        if text and not text.startswith("#"):
            text = "#" + text
        colour = QColor(text)
        if len(text) not in (4, 7) or not colour.isValid():
            self.hex.setStyleSheet("border: 1px solid #d04040;")
            return
        if colour.name() != self.colour().name():
            self.set_colour(colour)

    # ------------------------------------------------------------ dropper
    def start_picking(self):
        """The dropper: the colour under the pointer follows it until a
        click takes it; Esc or the right button puts back what there was."""
        self._picking = self.colour()
        self.setMouseTracking(True)
        self.grabMouse(Qt.CrossCursor)
        self.grabKeyboard()
        self.dropper.setText("Click to take the colour")

    def stop_picking(self, keep):
        before, self._picking = self._picking, None
        self.releaseMouse()
        self.releaseKeyboard()
        self.setMouseTracking(False)
        self.dropper.setText("Pick from screen")
        if not keep and before is not None:
            self.set_colour(before)

    def picking(self):
        return self._picking is not None

    def mouseMoveEvent(self, ev):
        if self._picking is not None:
            found = screen_colour(ev.globalPosition().toPoint())
            if found is not None:
                self.set_colour(found)
            return
        QDialog.mouseMoveEvent(self, ev)

    def mousePressEvent(self, ev):
        if self._picking is not None:
            if ev.button() == Qt.LeftButton:
                found = screen_colour(ev.globalPosition().toPoint())
                if found is not None:
                    self.set_colour(found)
                self.stop_picking(True)
            else:
                self.stop_picking(False)
            return
        QDialog.mousePressEvent(self, ev)

    def keyPressEvent(self, ev):
        if self._picking is not None:
            if ev.key() == Qt.Key_Escape:
                self.stop_picking(False)
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Enter takes a typed number (the box has it already); it does
            # not close the picker (`dialogs.enter_stays`, the same rule).
            return
        QDialog.keyPressEvent(self, ev)

    # ------------------------------------------------------------ closing
    def reject(self):
        """X or Esc keep the colour, as every pop-up does."""
        if self._picking is not None:
            self.stop_picking(False)
            return
        self.accept()

    def revert(self):
        self._reverted = True
        self.set_colour(self._initial)
        QDialog.reject(self)

    def reverted(self):
        return self._reverted


def get_colour(initial, parent=None, title="Pick a colour", live=None):
    """Modal, like `QColorDialog.getColor`: the chosen colour, or an
    invalid QColor if the picker was reverted. `live(name)` hears every
    change while it is open (and the old colour again on Revert)."""
    dialog = ColourDialog(initial, parent, title, live=live)
    dialog.exec()
    if dialog.reverted():
        return QColor()
    return dialog.colour()


class GradientDialog(QDialog):
    """Shades of one colour on the selected curves, live (F3): a base
    colour, how dark the darkest and how light the lightest, darkest at
    the top of the stack unless reversed.

    Built here and shown by the window (`MainWindow.colour_gradient`),
    which makes the one undo step when it closes. Closing keeps the colours
    however it is done; Revert puts the old ones back.
    """

    def __init__(self, scans, parent=None, on_change=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Colour gradient")
        self.scans = shades.stack_order(scans)
        self.before = [(scan, scan.colour) for scan in self.scans]
        # A gradient gives each curve a colour of its own: one that
        # FOLLOWED another object's stops (else the next refresh put the
        # donor's back over the gradient); Revert and undo bring it back.
        self.links = [(scan, getattr(scan, "colour_from", None))
                      for scan in self.scans]
        for scan in self.scans:
            scan.colour_from = None
        self._on_change = on_change
        self._reverted = False
        self.base = QColor(self.scans[0].colour if self.scans else "#1f77b4")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.base_button = Swatch(self.base, self, 28)
        self.base_button.setToolTip("The colour the shades are made of.")
        self.base_button.clicked.connect(lambda _c=False: self.pick_base())
        form.addRow("Colour", self.base_button)
        self.darkest = NumberBox(self)
        self.lightest = NumberBox(self)
        for box, value, word in ((self.darkest, -40.0, "darkest"),
                                 (self.lightest, 50.0, "lightest")):
            box.setRange(-100.0, 100.0)
            box.setDecimals(0)
            box.setSingleStep(5.0)
            box.setSuffix(" %")
            box.setValue(value)
            box.setToolTip("The {} curve: the colour mixed towards black "
                           "(below 0) or white (above 0) by this much."
                           .format(word))
            box.valueChanged.connect(lambda _v: self.apply())
        form.addRow("Darkest", self.darkest)
        form.addRow("Lightest", self.lightest)
        self.reverse = QCheckBox("Lightest at the top")
        self.reverse.setToolTip("Darkest at the top of the stack unless "
                                "ticked.")
        self.reverse.toggled.connect(lambda _on: self.apply())
        form.addRow("", self.reverse)
        self.preview = QHBoxLayout()
        self.preview.setSpacing(2)
        self._swatches = []
        for _scan in self.scans:
            swatch = Swatch(self.base, self, 18)
            swatch.setEnabled(False)
            self._swatches.append(swatch)
            self.preview.addWidget(swatch)
        self.preview.addStretch(1)
        form.addRow("Shades", self.preview)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        revert = buttons.button(QDialogButtonBox.Cancel)
        revert.setText("Revert")
        revert.setToolTip("Put back the colours the curves had and close. "
                          "Closing otherwise keeps the new ones.")
        for button in buttons.buttons():
            button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.revert)
        layout.addWidget(buttons)
        self.apply()

    def colours(self):
        """The shades, top of the stack first."""
        found = shades.shades(self.base.name(), len(self.scans),
                              self.darkest.value() / 100.0,
                              self.lightest.value() / 100.0)
        return found[::-1] if self.reverse.isChecked() else found

    def apply(self):
        for scan, swatch, name in zip(self.scans, self._swatches,
                                      self.colours()):
            scan.colour = name
            swatch.set_colour(name)
        if self._on_change is not None:
            self._on_change()

    def pick_base(self):
        was = QColor(self.base)

        def live(name):
            self.base = QColor(name)
            self.base_button.set_colour(self.base)
            self.apply()

        chosen = get_colour(was, self, "Colour of the gradient", live=live)
        live((chosen if chosen.isValid() else was).name())

    def changes(self):
        """`[(scan, "colour", new)]`, the old colours put back first so the
        window's undo step records them."""
        found = []
        for scan, old in self.before:
            new = scan.colour
            if new != old:
                scan.colour = old
                found.append((scan, "colour", new))
        for scan, link in self.links:
            if link is not None:
                scan.colour_from = link
                found.append((scan, "colour_from", None))
        return found

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            return                      # takes a typed number, stays open
        QDialog.keyPressEvent(self, ev)

    def reject(self):
        """X or Esc keep the colours, as every pop-up does."""
        self.accept()

    def revert(self):
        self._reverted = True
        for scan, old in self.before:
            scan.colour = old
        for scan, link in self.links:
            scan.colour_from = link
        if self._on_change is not None:
            self._on_change()
        QDialog.reject(self)

    def reverted(self):
        return self._reverted

# Qt calls the handlers here by itself; an error in one is logged and
# survived rather than the end of the program (`core/log.py`).
from ..core import log as _log
_log.guard_classes(globals(), __name__)
