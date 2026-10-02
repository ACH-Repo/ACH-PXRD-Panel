"""Putting the program in the Start Menu, and taking it out again.

Two rules:

1. **It is opt-in and you run it yourself.** A program that quietly claims a
   file type or writes a shortcut the first time it runs is a program people
   learn to distrust. So nothing here happens at start-up; `pxrd-panel
   register` does it and `pxrd-panel register --remove` undoes it.
2. **It writes a manifest of everything it created.** Removal then takes away
   exactly what was added, rather than guessing at paths a later version
   might get wrong - and that manifest, together with
   `branding.LEGACY_NAMES`, is the whole answer to "we will probably rename
   this later": the new name knows what the old one installed and can clean
   it up with `--clean-legacy`.

Everything is per user. Nothing here needs administrator rights, nothing is
written outside the user's own profile, and no registry key outside
`HKCU\\Software\\Classes`.

Aliases are the same machinery pointed at a name the user picks:
`pxrd-panel alias irp` makes `irp` work in a terminal on any platform - a
`.cmd` shim beside the installed command on Windows, a symlink in
`~/.local/bin` elsewhere - and records it so `--remove` can take it away.
"""

import json
import os
import shutil
import subprocess
import sys

from . import branding


def _load():
    try:
        with open(branding.manifest_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"entries": []}


def _save(manifest):
    os.makedirs(branding.app_dir(), exist_ok=True)
    with open(branding.manifest_path(), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)


def _record(manifest, kind, path, name=""):
    entry = {"kind": kind, "path": str(path), "name": name,
             "app": branding.APP_NAME}
    if entry not in manifest["entries"]:
        manifest["entries"].append(entry)


def launcher():
    """The command a shortcut should run: the installed exe if there is one.

    The windowed entry point is preferred, so no console window appears
    behind the program. Falling back to `python -m <this package>` keeps
    this working from a source checkout that was never pip-installed.
    """
    for name in (branding.EXE_NAME_GUI, branding.EXE_NAME):
        found = shutil.which(name)
        if found:
            return [found]
    python = sys.executable
    if sys.platform == "win32":
        windowed = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.isfile(windowed):
            python = windowed
    return [python, "-m", __name__.split(".")[0]]


# --------------------------------------------------------------- Windows
def _windows_start_menu():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Microsoft", "Windows", "Start Menu",
                        "Programs")


def _make_shortcut(link_path, command, description):
    """Write a .lnk through PowerShell's WScript.Shell.

    Via PowerShell rather than pywin32, so the program does not need a
    dependency whose only job is to make one file.
    """
    target = command[0]
    arguments = " ".join('"{}"'.format(a) for a in command[1:])
    script = (
        "$s = New-Object -ComObject WScript.Shell; "
        "$l = $s.CreateShortcut('{link}'); "
        "$l.TargetPath = '{target}'; "
        "$l.Arguments = '{args}'; "
        "$l.Description = '{desc}'; "
        "$l.WorkingDirectory = '{cwd}'; "
        "$l.Save()").format(
            link=link_path.replace("'", "''"),
            target=target.replace("'", "''"),
            args=arguments.replace("'", "''"),
            desc=description.replace("'", "''"),
            cwd=os.path.expanduser("~").replace("'", "''"))
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", script], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return link_path


def _windows_scripts_dir():
    """Where pip put the command, which is a directory already on PATH.

    The fallback is the USER scripts directory rather than the interpreter's
    own: a Python under `C:\\Program Files` has a Scripts folder that needs
    administrator rights to write to, so an alias written there fails for
    exactly the people who did not install Python themselves. The user
    scheme is where `pip install --user` puts a command anyway.
    """
    found = shutil.which(branding.EXE_NAME) or shutil.which(
        branding.EXE_NAME_GUI)
    if found:
        return os.path.dirname(found)
    import sysconfig
    try:
        path = sysconfig.get_path("scripts", "nt_user")
        if path:
            return path
    except (KeyError, ValueError):
        pass
    return os.path.join(os.path.dirname(sys.executable), "Scripts")


# ----------------------------------------------------------------- POSIX
def _desktop_entry_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
        "~/.local/share")
    return os.path.join(base, "applications")


def _write_desktop_entry(path, command, name, description):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[Desktop Entry]\n")
        fh.write("Type=Application\n")
        fh.write("Name={}\n".format(name))
        fh.write("Comment={}\n".format(description))
        fh.write("Exec={} %F\n".format(" ".join(command)))
        fh.write("Terminal=false\n")
        fh.write("Categories=Science;Education;\n")
    return path


# ------------------------------------------------------------------- API
def register(desktop=False, dry_run=False):
    """Make the program findable: Start Menu, or a .desktop entry."""
    manifest = _load()
    command = launcher()
    made = []
    if sys.platform == "win32":
        link = os.path.join(_windows_start_menu(),
                            branding.APP_NAME + ".lnk")
        made.append(("start menu", link))
        if desktop:
            made.append(("desktop", os.path.join(
                os.path.expanduser("~/Desktop"),
                branding.APP_NAME + ".lnk")))
        if not dry_run:
            for kind, path in made:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _make_shortcut(path, command, branding.DESCRIPTION)
                _record(manifest, kind, path, branding.APP_NAME)
    else:
        path = os.path.join(_desktop_entry_dir(),
                            branding.EXE_NAME + ".desktop")
        made.append(("desktop entry", path))
        if not dry_run:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _write_desktop_entry(path, command, branding.APP_NAME,
                                 branding.DESCRIPTION)
            _record(manifest, "desktop entry", path, branding.APP_NAME)
    if not dry_run:
        _save(manifest)
    return made


def unregister(names=None, dry_run=False):
    """Remove what was registered, for this name or for the ones given."""
    manifest = _load()
    wanted = set(names or [branding.APP_NAME])
    removed, kept = [], []
    for entry in manifest["entries"]:
        if entry.get("app") not in wanted:
            kept.append(entry)
            continue
        path = entry.get("path", "")
        removed.append(path)
        if not dry_run and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                kept.append(entry)
    if not dry_run:
        manifest["entries"] = kept
        _save(manifest)
    return removed


def clean_legacy(dry_run=False):
    """Take away what the program's PREVIOUS names left behind.

    This is the deregistration half of a rename. `branding.LEGACY_NAMES` is
    the list a renamed release ships; everything here works from it, so the
    old name never has to be remembered by hand.
    """
    names = [entry[0] for entry in branding.LEGACY_NAMES]
    removed = unregister(names, dry_run=dry_run) if names else []
    # Shortcuts made before the manifest existed, or by a version that could
    # not write it, are looked for by name as well.
    for app_name, exe_name, _prog_id in branding.LEGACY_NAMES:
        guesses = []
        if sys.platform == "win32":
            guesses.append(os.path.join(_windows_start_menu(),
                                        app_name + ".lnk"))
            guesses.append(os.path.join(os.path.expanduser("~/Desktop"),
                                        app_name + ".lnk"))
            guesses.append(os.path.join(_windows_scripts_dir(),
                                        exe_name + ".cmd"))
        else:
            guesses.append(os.path.join(_desktop_entry_dir(),
                                        exe_name + ".desktop"))
            guesses.append(os.path.expanduser("~/.local/bin/" + exe_name))
        for path in guesses:
            if os.path.exists(path):
                removed.append(path)
                if not dry_run:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
    return removed


def alias(name, remove=False, dry_run=False):
    """Make `name` start the program too, and remember that it was made."""
    manifest = _load()
    command = launcher()
    if sys.platform == "win32":
        path = os.path.join(_windows_scripts_dir(), name + ".cmd")
        body = '@echo off\r\n"{}" %*\r\n'.format(" ".join(command))
    else:
        path = os.path.expanduser(os.path.join("~/.local/bin", name))
        body = "#!/bin/sh\nexec {} \"$@\"\n".format(" ".join(command))
    if remove:
        if not dry_run and os.path.exists(path):
            os.remove(path)
        manifest["entries"] = [e for e in manifest["entries"]
                               if e.get("path") != path]
        if not dry_run:
            _save(manifest)
        return path
    if not dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
        if sys.platform != "win32":
            os.chmod(path, 0o755)
        _record(manifest, "alias", path, branding.APP_NAME)
        _save(manifest)
    return path


def aliases():
    """Every alias this program knows it made."""
    return [e["path"] for e in _load()["entries"] if e.get("kind") == "alias"]


def describe():
    """What is registered right now, as lines for a terminal."""
    manifest = _load()
    if not manifest["entries"]:
        return ["nothing registered (run: {} register)".format(
            branding.EXE_NAME)]
    return ["{:<14} {}{}".format(e.get("kind", "?"), e.get("path", "?"),
                                 "" if os.path.exists(e.get("path", ""))
                                 else "   (gone)")
            for e in manifest["entries"]]
