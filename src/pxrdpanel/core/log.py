"""The log: what the program did, and every error, in a file.

Started from its shortcut the program has no console, so an error had
nowhere to go - and PySide6 turns an exception inside a Qt slot into the
end of the process unless an excepthook takes it. `install` fixes both: the
hook writes the traceback here and the program carries on (the work is
still open), and `faulthandler` writes the stack of a hard crash - one in
Qt itself - into a file of its own beside it.

The files live with the preferences (`branding.app_dir()`); Help > Open the
log folder shows them. Rotating: a megabyte, two old ones kept.

The hook reaches an error in a SLOT only. One inside a method Qt calls by
itself - `mouseMoveEvent`, `paintEvent`, `event` - ends the process in
PySide6 6.11 without asking `sys.excepthook`: an access violation, and not
a line in the log (dragging a label whose curve was hidden did that).
Every UI module therefore ends with `guard_classes`, which wraps those
methods so that such an error goes the same way as a slot's.

UI-free. `on_error` is how the window hears about an error it should show.
"""

import faulthandler
import functools
import inspect
import logging
import logging.handlers
import os
import platform
import sys
import time
import traceback

from .. import branding

#: The program's logger. Modules log through `logging.getLogger(NAME)`;
#: without `install` (tests, tools) nothing is written anywhere.
NAME = "pxrdpanel"
LOGGER = logging.getLogger(NAME)

#: Called with a one-line summary after an error was logged, or None.
on_error = None

_crash_file = None


def path():
    """The log file."""
    return os.path.join(branding.app_dir(), branding.EXE_NAME + ".log")


def install(target=None):
    """Log to `target` (the default file), catch every unhandled error and
    every hard crash. Returns the file's path; never raises - a program
    that cannot write its log should still start."""
    global _crash_file
    target = target or path()
    try:
        folder = os.path.dirname(target)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=1000000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        _crash_file = open(target + ".crash", "a", encoding="utf-8")
        faulthandler.enable(_crash_file)
    except OSError:
        pass
    sys.excepthook = _hook
    from .. import __version__
    LOGGER.info("%s %s started: Python %s on %s", branding.APP_NAME,
                __version__, platform.python_version(), platform.platform())
    return target


def _hook(kind, value, tb):
    """Every unhandled error: its traceback into the log, a line to the
    window. The program carries on."""
    text = "".join(traceback.format_exception(kind, value, tb))
    LOGGER.error("Unhandled %s: %s\n%s", kind.__name__, value, text)
    if sys.__stderr__ is not None:
        try:
            sys.__stderr__.write(text)
        except Exception:
            pass
    if on_error is not None:
        try:
            on_error("{}: {}".format(kind.__name__, value))
        except Exception:
            pass


#: The methods Qt calls by itself that `guard_classes` wraps besides every
#: `...Event`, and what each hands back to Qt when it failed.
HANDLERS = {"event": True, "eventFilter": False, "nativeEvent": (False, 0),
            "paint": None, "accept": None, "reject": None, "done": None}

#: The last error a guard reported and when: the same one again within
#: `QUIET_S` is not reported again, so a paint that fails on every frame
#: (and the flash that reports it, which paints) does not fill the log.
_last_guarded = [None, 0.0]
QUIET_S = 5.0


def guarded(function, fallback=None):
    """`function`, with an error inside it logged and survived WHILE the
    hook is installed (the program); otherwise (tests, tools) raised as
    ever, so a test still fails on it."""
    @functools.wraps(function)
    def handler(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            if sys.excepthook is not _hook:
                raise
            kind, value, tb = sys.exc_info()
            key = (function.__qualname__, kind, str(value))
            now = time.monotonic()
            if key != _last_guarded[0] or now - _last_guarded[1] > QUIET_S:
                _hook(kind, value, tb)
            _last_guarded[0], _last_guarded[1] = key, now
            return fallback
    handler.guarded = True
    return handler


def guard_classes(namespace, module):
    """Wrap the Qt handlers of every class `module` defines (`guarded`):
    each `...Event` method and the ones in `HANDLERS`. A UI module ends
    with `log.guard_classes(globals(), __name__)`; a test checks that none
    is missed. Returns how many were wrapped."""
    count = 0
    for value in list(namespace.values()):
        if not inspect.isclass(value) or value.__module__ != module:
            continue
        for name, attr in list(vars(value).items()):
            if not (name.endswith("Event") or name in HANDLERS):
                continue
            if not inspect.isfunction(attr) or getattr(attr, "guarded",
                                                       False):
                continue
            setattr(value, name, guarded(attr, HANDLERS.get(name)))
            count += 1
    return count
