"""Edit > Settings: the house style, for every figure and for this one.

Two columns, because two things are wanted at once: defaults that persist
between sessions, and a way to override them for one particular save
file.

* **Default** is the user's own house style. It lives on this computer
  (`core/style.py`, `preferences.json`), is written when the page is closed
  with OK, and is what every figure falls back on.
* **This figure** belongs to the open document and is saved in its session
  file. Empty ("default") means "whatever the default says"; a value here
  wins for this figure alone. The change is one undo step.

A size set in an object's OWN settings (double-click a label) wins over both
columns, because that was a decision about that one object.

Below them, **Handling**: how the program responds to the hand - the pick
distance. It has a Default and nothing else, because it is about the person
at the trackpad and not about any figure.

Live, like every dialog here: the plot follows each change as it is made.
Closing the page any way keeps the changes; Revert puts back what was there
when it opened - both columns.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame,
                               QGridLayout, QLabel, QScrollArea, QVBoxLayout,
                               QWidget)

from ..core import style
from .dialogs import (FontChoice, NumberBox, StyleChoice, StyleNumber,
                      StyleText, enter_stays, readable)


class SettingsDialog(QDialog):
    """The house style, as a two-column table."""

    def __init__(self, window):
        QDialog.__init__(self, window)
        self.setWindowTitle("Settings")
        # Not modal, like the object dialogs: a size is judged by looking
        # at the plot, and a modal page hides it.
        self.setModal(False)
        self.setWindowFlag(Qt.Tool, True)
        self.main = window
        self.doc = window.doc
        self._saved_defaults = style.preferences()
        self._saved_figure = dict((s.key, getattr(self.doc.style, s.key))
                                  for s in style.FIGURE_SETTINGS)
        self.defaults = {}
        self.figure = {}

        layout = QVBoxLayout(self)
        # The rows SCROLL: the list only gets longer, and a page taller than
        # the screen puts its OK button out of reach.
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(self._scroll)
        self._scroll.setWidget(body)
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(12)
        layout.addWidget(self._scroll, 1)
        for column, text in ((1, "Default"), (2, "This figure")):
            head = QLabel("<b>{}</b>".format(text))
            grid.addWidget(head, 0, column)
        handling = [s for s in style.SETTINGS if not s.figure]
        rows = list(style.FIGURE_SETTINGS) + ([None] if handling else [])             + handling
        for row, setting in enumerate(rows, start=1):
            if setting is None:
                head = QLabel("<b>Handling</b>")
                head.setContentsMargins(0, 10, 0, 0)
                grid.addWidget(head, row, 0)
                continue
            name = QLabel(setting.title)
            if setting.note:
                name.setToolTip(setting.note)
            grid.addWidget(name, row, 0)
            key = setting.key
            if setting.kind in ("format", "font"):
                # The Default column's None is "the built-in value"; the
                # figure's is "whatever the default says".
                kind = StyleText if setting.kind == "format" else FontChoice
                default = kind(style.user_preference(key),
                               (lambda k=key: style.builtin(k)),
                               parent=self, reset_text="Built-in")
                default.changed.connect(
                    lambda k=key: self._default_changed(k))
                own = kind(getattr(self.doc.style, key),
                           (lambda k=key: style.preference(k)), parent=self)
            elif setting.kind == "choice":
                titles = setting.titles
                default = StyleChoice(setting.choices, style.preference(key),
                                      lambda: "", parent=self,
                                      titles=titles, allow_default=False)
                default.changed.connect(
                    lambda k=key: self._default_changed(k))
                own = StyleChoice(
                    setting.choices, getattr(self.doc.style, key),
                    (lambda k=key, t=titles: t.get(
                        style.preference(k), style.preference(k))),
                    parent=self, titles=titles)
            else:
                default = NumberBox(self)
                default.setDecimals(setting.decimals)
                default.setRange(float(setting.low), float(setting.high))
                default.setSingleStep(float(setting.step))
                default.setSuffix(setting.suffix)
                default.setValue(float(style.preference(key)))
                default.valueChanged.connect(
                    lambda _v=0.0, k=key: self._default_changed(k))
                own = None
                if setting.figure:
                    own = StyleNumber(setting, getattr(self.doc.style, key),
                                      (lambda k=key: style.preference(k)),
                                      parent=self)
            self.defaults[key] = default
            grid.addWidget(default, row, 1)
            if setting.note and not setting.figure:
                default.setToolTip(setting.note)
            if own is not None:
                own.changed.connect(lambda k=key: self._figure_changed(k))
                self.figure[key] = own
                grid.addWidget(own, row, 2)

        note = QLabel(
            "Default: this computer, every figure. This figure: saved in its "
            "session. An object's own setting wins over both.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9a9a9a;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel
                                   | QDialogButtonBox.RestoreDefaults)
        buttons.button(QDialogButtonBox.Cancel).setText("Revert")
        buttons.button(QDialogButtonBox.Cancel).setToolTip(
            "Undo this page's changes and close. Closing otherwise keeps "
            "them.")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.revert)
        buttons.button(QDialogButtonBox.RestoreDefaults).setText(
            "Built-in defaults")
        buttons.button(QDialogButtonBox.RestoreDefaults).setToolTip(
            "Default column back to the built-in values.")
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            lambda _c=False: self.restore_builtin())
        layout.addWidget(buttons)
        # As tall as the rows need, up to most of the screen; beyond that
        # the rows scroll and the buttons stay put.
        wanted = body.sizeHint().height() + note.sizeHint().height() \
            + buttons.sizeHint().height() + 60
        screen = window.screen()
        limit = (int(screen.availableGeometry().height() * 0.75)
                 if screen is not None else wanted)
        self.resize(max(560, body.sizeHint().width() + 40),
                    min(wanted, limit))

    # ------------------------------------------------------------ editing
    def default_value(self, key):
        widget = self.defaults[key]
        if isinstance(widget, (StyleChoice, StyleText, FontChoice)):
            return widget.value()
        return float(widget.value())

    def set_default(self, key, value):
        """Change one default as if it had been typed. For the button, and
        for tests."""
        widget = self.defaults[key]
        if isinstance(widget, (StyleText, FontChoice)):
            widget.blockSignals(True)
            widget.set_value(None if value == style.builtin(key) else value)
            widget.blockSignals(False)
            self._default_changed(key)
            return
        inner = widget.combo if isinstance(widget, StyleChoice) else widget
        inner.blockSignals(True)
        if isinstance(widget, StyleChoice):
            widget.set_value(value)
        else:
            widget.setValue(float(value))
        inner.blockSignals(False)
        self._default_changed(key)

    def _default_changed(self, key):
        style.set_preference(key, self.default_value(key))
        # The "This figure" column shows the default wherever it follows it.
        if key in self.figure:
            self.figure[key].refresh()
        self._live()

    def _figure_changed(self, key):
        setattr(self.doc.style, key, self.figure[key].value())
        self._live()

    def restore_builtin(self):
        for setting in style.SETTINGS:
            self.set_default(setting.key, setting.default)

    def _live(self):
        self.main._live_change()

    # ----------------------------------------------------------- finishing
    def accept(self):
        """Keep both columns: the defaults on disk, the figure on the stack.

        Written defensively: this runs in a Qt slot, where an exception is an
        abort rather than a traceback, and a read-only settings folder is not
        a reason to lose the figure.
        """
        try:
            style.save_preferences()
        except OSError as exc:
            self.main.note.setText("Could not save the defaults: {}".format(
                exc))
        changes = []
        for key, old in self._saved_figure.items():
            new = getattr(self.doc.style, key)
            if new != old:
                setattr(self.doc.style, key, old)
                changes.append((self.doc.style, key, new))
        if changes and self.main.doc is self.doc:
            self.main.undo.set_props(changes, "figure style")
        else:
            self._live()
        QDialog.accept(self)

    def reject(self):
        """Its X, Esc or Ctrl+W: keep the changes, like OK (see
        `_LiveDialog.reject`). Only Revert puts them back."""
        self.accept()

    def keyPressEvent(self, ev):
        """Enter takes the value and keeps the page open (`enter_stays`)."""
        if enter_stays(self, ev):
            return
        QDialog.keyPressEvent(self, ev)

    def showEvent(self, ev):
        readable(self)
        QDialog.showEvent(self, ev)

    def revert(self):
        style.restore_preferences(self._saved_defaults)
        for key, old in self._saved_figure.items():
            setattr(self.doc.style, key, old)
        self._live()
        QDialog.reject(self)

# Qt calls the handlers here by itself; an error in one is logged and
# survived rather than the end of the program (`core/log.py`).
from ..core import log as _log
_log.guard_classes(globals(), __name__)
