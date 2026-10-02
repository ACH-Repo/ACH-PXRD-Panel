"""Number boxes that take a sum as well as a number.

"255-20" in a box is 235. Every number box of the program is one of these
(`NumberBox`, `WholeBox`).
"""

from PySide6.QtCore import QLocale
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

from ..core import numbers


class _Sums(object):
    """A spin box that takes a SUM as well as a number: "255-20" is 235
    worked out on Enter or when the box is left.
    `core.numbers.evaluate`: + - * / and brackets, a comma is a decimal
    point. A sum past the range is clamped to it."""

    def _setup(self):
        locale = QLocale(QLocale.C)
        locale.setNumberOptions(QLocale.OmitGroupSeparator)
        self.setLocale(locale)
        self.setKeyboardTracking(False)

    def _core(self, text):
        """The typed text without the box's prefix and suffix."""
        text = str(text).strip()
        suffix, prefix = self.suffix().strip(), self.prefix().strip()
        if suffix and text.endswith(suffix):
            text = text[:-len(suffix)]
        if prefix and text.startswith(prefix):
            text = text[len(prefix):]
        return text.strip()

    def _sum(self, text):
        """The value of a typed sum, clamped; None if it is not one."""
        core = self._core(text)
        if not numbers.is_sum(core):
            return None
        value = numbers.evaluate(core)
        if value is None:
            return None
        return min(max(value, self.minimum()), self.maximum())

    def validate(self, text, pos):
        core = self._core(text)
        if numbers.is_sum(core):
            # Still being typed ("255-") is fine; Enter works it out.
            state = (QValidator.Acceptable
                     if numbers.evaluate(core) is not None
                     else QValidator.Intermediate)
            return state, text, pos
        state, _fixed, position = self._base.validate(
            self, self._normalise(text), pos)
        return state, text, position

    def valueFromText(self, text):
        value = self._sum(text)
        if value is not None:
            return self._cast(value)
        return self._base.valueFromText(self, self._normalise(text))

    def fixup(self, text):
        value = self._sum(text)
        if value is not None:
            return self.textFromValue(self._cast(value))
        return text

    @staticmethod
    def _normalise(text):
        return str(text).strip().replace(",", ".")


class NumberBox(_Sums, QDoubleSpinBox):
    """A spin box that reads "1.5" and "1,5" alike, takes a sum, and
    commits on Enter."""

    _base = QDoubleSpinBox
    _cast = float

    def __init__(self, parent=None):
        QDoubleSpinBox.__init__(self, parent)
        self._setup()


class WholeBox(_Sums, QSpinBox):
    """`NumberBox` for whole numbers: a sum is rounded."""

    _base = QSpinBox

    def __init__(self, parent=None):
        QSpinBox.__init__(self, parent)
        self._setup()

    @staticmethod
    def _cast(value):
        return int(round(value))

# Qt calls the handlers here by itself; an error in one is logged and
# survived rather than the end of the program (`core/log.py`).
from ..core import log as _log
_log.guard_classes(globals(), __name__)
