"""Reading files without freezing the window.

A pattern reads in milliseconds, but a folder of them dropped at once, on
a network drive, is not, and a frozen window is a program that looks
broken.

So every read runs on a thread pool and the window is told as each one
arrives. Two things this deliberately does NOT do: it does not parse on the
GUI thread "just for one small file" (the small file is the one that turns
out to be the slow one), and it does not hand a half-built sample to the window
(the reader either returns a `Sample` or raises, and the failure carries the
file's name so a bad file in a drop of five costs one line and not the drop).
"""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..core import loader


class _JobSignals(QObject):
    done = Signal(object, str, str)          # sample, path, error


class _Job(QRunnable):
    def __init__(self, path, signals):
        QRunnable.__init__(self)
        self.path = str(path)
        self.signals = signals

    def run(self):
        try:
            sample = loader.read_sample(self.path)
        except Exception as exc:             # the reader raises many kinds
            self.signals.done.emit(None, self.path, str(exc))
            return
        self.signals.done.emit(sample, self.path, "")


class Loader(QObject):
    """Reads files in the background and reports each one as it lands."""

    loaded = Signal(object)                  # Sample
    failed = Signal(str, str)                # path, message
    progress = Signal(int, int)              # done, total
    finished = Signal()

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        self._pool = QThreadPool(self)
        # Two at a time. The reader is numpy-heavy and mostly holds the GIL,
        # so more threads would not read faster, and a bounded pool keeps a
        # drop of twenty files from starting twenty reads at once.
        self._pool.setMaxThreadCount(2)
        self._signals = _JobSignals()
        self._signals.done.connect(self._one_done)
        self._pending = 0
        self._total = 0

    def busy(self):
        return self._pending > 0

    def load(self, paths):
        """Queue every readable path. Returns how many were queued."""
        queued = 0
        for path in paths or ():
            if not loader.looks_readable(path):
                continue
            self._pending += 1
            self._total += 1
            self._pool.start(_Job(path, self._signals))
            queued += 1
        if queued:
            self.progress.emit(self._total - self._pending, self._total)
        return queued

    def wait(self, timeout_ms=30000):
        """For tests: block until the queue is empty."""
        return self._pool.waitForDone(timeout_ms)

    def _one_done(self, sample, path, error):
        self._pending = max(0, self._pending - 1)
        if sample is None:
            self.failed.emit(path, error)
        else:
            self.loaded.emit(sample)
        self.progress.emit(self._total - self._pending, self._total)
        if not self._pending:
            self._total = 0
            self.finished.emit()

# Qt calls the handlers here by itself; an error in one is logged and
# survived rather than the end of the program (`core/log.py`).
from ..core import log as _log
_log.guard_classes(globals(), __name__)
