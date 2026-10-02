"""Every place the program says its own name, in ONE module.

"PXRD-Panel" is a working title and a rename is expected (its DSC sibling
went from a working title to its release name the same way). A name that is
spelled out in twenty places is a name nobody dares to change. So:

* the window title, the Start Menu shortcut, the file-type id, the settings
  folder and the manifest of what was registered all read from here;
* `LEGACY_NAMES` lists every name this program has been called before, so a
  release under a new name can take the OLD name's shortcuts, file type and
  aliases away. That is the whole deregistration plan: the new version knows
  what the old one installed, because the old name is still written down.

What a rename costs, with this module in place:

1. change `APP_NAME`, `EXE_NAME`, `PROG_ID` and `SETTINGS_ORG`/`SETTINGS_APP`
   here, and add the outgoing values to `LEGACY_NAMES`;
2. change the two entry-point lines in `pyproject.toml` (and the
   distribution's name there, if it goes too);
3. uninstall the old distribution (this removes the old command), install
   the new one;
4. run `<new command> register --clean-legacy`, which reads `LEGACY_NAMES`
   and the registration manifest and removes what the old name left behind.

The user's folder (`app_dir`: preferences, presets, the manifest) is named
after the program too, so the first start under a new name copies the old
one's across (`adopt_legacy_dir`) - the house style, the presets and the
window's place survive, and so does the manifest the cleanup reads.

Nothing else in the program contains the name as a literal. A test enforces
that (`tests/test_branding.py`).
"""

import os
import sys

#: What the program calls itself in the window title and the Start Menu.
APP_NAME = "PXRD-Panel"

#: The command, and the base name of every shortcut and shim that is created.
EXE_NAME = "pxrd-panel"

#: The windowed entry point, which a shortcut prefers so no console appears.
EXE_NAME_GUI = "pxrd-panel-gui"

#: The Explorer file-type id for the session file, under HKCU\Software\Classes.
PROG_ID = "PxrdPanel.Session"

#: The session file's extension: it names what the file holds (a PXRD
#: figure), and survives a rename of the program.
SESSION_EXT = ".pxrdpanel"

#: A style preset's extension (`core/presets.py`): JSON inside, named so a
#: dropped one is known for what it is.
PRESET_EXT = ".pxrdstyle"

#: QSettings coordinates. Changing these forgets the user's window geometry,
#: which is why they are written down rather than derived from APP_NAME.
SETTINGS_ORG = "ACH"
SETTINGS_APP = "PXRD-Panel"

#: Names this program has gone by. (name, exe, prog_id) for each, oldest
#: first. `register --clean-legacy` walks this list and removes anything it
#: finds: Start Menu shortcuts, desktop shortcuts, PATH shims and the file
#: association. Add the outgoing values here as part of a rename; never
#: remove an entry, because somebody's machine may still be carrying it.
LEGACY_NAMES = ()

#: A one-line description, for shortcut tooltips and the About box.
DESCRIPTION = "Stacked powder diffraction patterns"

#: The program's public page, for the About box. It carries the name, so it
#: is written here and nowhere else (and changes with a rename).
HOMEPAGE = "https://github.com/ACH-Repo/ACH-PXRD-Panel"

#: The copyright line from LICENSE, for the About box.
COPYRIGHT = ("Christian Nelle (@p3rAsperaAdAstra), AG Henke, "
             "TU Dortmund. MIT licence.")


def app_dir(app_name=None, exe_name=None):
    """Where the registration manifest and any user data live.

    Per user, never per install, so a reinstall does not lose track of the
    shortcuts the last install made. With a name, the folder a program of
    that name used (a `LEGACY_NAMES` entry's first two).
    """
    app_name = app_name or APP_NAME
    exe_name = exe_name or EXE_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, app_name)
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/" + app_name)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, exe_name)


def adopt_legacy_dir():
    """Copy the newest older name's folder to `app_dir()`, the first time.

    Only when `app_dir()` does not exist yet: once it does, it is this
    name's own and nothing is copied over it. The old folder stays where it
    is (an older install may still be using it); its logs are not copied,
    because a log is named after the program that wrote it. Returns the
    folder copied from, or None. Never raises: a copy that fails leaves the
    program starting with the built-in style, as on a new machine.
    """
    import shutil
    target = app_dir()
    try:
        if os.path.exists(target):
            return None
        for app_name, exe_name, _prog_id in reversed(LEGACY_NAMES):
            source = app_dir(app_name, exe_name)
            if os.path.isdir(source) and source != target:
                shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                    "*.log", "*.log.*"))
                return source
    except (OSError, shutil.Error):
        return None
    return None


def manifest_path():
    """The file listing what this program has registered on this machine.

    Written by `register`, read by `register --remove` and
    `register --clean-legacy`. It exists so that removal takes away exactly
    what was added, rather than guessing at paths a future version might get
    wrong.
    """
    return os.path.join(app_dir(), "registration.json")


def window_title(subject=""):
    """`APP_NAME`, with the open session or file in front of it."""
    return "{} - {}".format(subject, APP_NAME) if subject else APP_NAME
