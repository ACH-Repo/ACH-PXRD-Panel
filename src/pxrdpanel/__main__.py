"""The entry point: `pxrd-panel`, `pxrd-panel-gui`, or `python -m pxrdpanel`.

Files named on the command line are opened, so the program can be the "open
with" target for a pattern and can be started from a data folder with
`pxrd-panel *.raw`.

Two subcommands sit beside that, and neither of them starts a window:
`register` puts the program in the Start Menu (and `--remove` takes it out
again), and `alias` gives it whatever name the user would rather type. Both
are opt-in and reversible; see `register.py` for why that matters.
"""

import argparse
import sys

from . import __version__, branding


#: Words that mean "do not open a window". Checked BEFORE argparse, because a
#: subcommand and an optional list of file paths cannot both be positional:
#: `paths` with nargs="*" swallows the subcommand name and argparse then
#: rejects its arguments ("invalid choice: 'irp'").
COMMANDS = ("register", "alias")


def build_parser():
    parser = argparse.ArgumentParser(
        prog=branding.EXE_NAME,
        description="{} - {}".format(branding.APP_NAME, branding.DESCRIPTION),
        epilog="subcommands: {} (each takes --help)".format(
            ", ".join(COMMANDS)))
    parser.add_argument("paths", nargs="*",
                        help="patterns (.raw, .brml, .dat, .xy, .cif, .xml, "
                             "...) or a saved session")
    parser.add_argument("--version", action="version",
                        version="{} {}".format(branding.APP_NAME, __version__))
    return parser


def build_command_parser():
    parser = argparse.ArgumentParser(prog=branding.EXE_NAME)
    subs = parser.add_subparsers(dest="command", required=True)

    reg = subs.add_parser(
        "register", help="put {} in the Start Menu".format(branding.APP_NAME))
    reg.add_argument("--remove", action="store_true",
                     help="take it out again")
    reg.add_argument("--desktop", action="store_true",
                     help="a desktop shortcut as well (Windows)")
    reg.add_argument("--clean-legacy", action="store_true",
                     help="remove what this program's older names left behind")
    reg.add_argument("--list", action="store_true",
                     help="show what is registered")
    reg.add_argument("--dry-run", action="store_true",
                     help="say what would happen and change nothing")

    ali = subs.add_parser("alias",
                          help="start the program under another name too")
    ali.add_argument("name", nargs="?", help="the name you would rather type")
    ali.add_argument("--remove", action="store_true", help="take it away")
    ali.add_argument("--list", action="store_true", help="show the aliases")
    ali.add_argument("--dry-run", action="store_true")
    return parser


def _register(args):
    from . import register
    if args.list:
        for line in register.describe():
            print(line)
        return 0
    if args.clean_legacy:
        removed = register.clean_legacy(dry_run=args.dry_run)
        print("would remove:" if args.dry_run else "removed:",
              len(removed), "item(s)")
        for path in removed:
            print("   ", path)
        return 0
    if args.remove:
        removed = register.unregister(dry_run=args.dry_run)
        print("would remove:" if args.dry_run else "removed:", len(removed))
        for path in removed:
            print("   ", path)
        return 0
    made = register.register(desktop=args.desktop, dry_run=args.dry_run)
    print("would write:" if args.dry_run else "wrote:")
    for kind, path in made:
        print("    {:<14} {}".format(kind, path))
    print("started by: {}".format(" ".join(register.launcher())))
    return 0


def _alias(args):
    from . import register
    if args.list or not args.name:
        found = register.aliases()
        print("\n".join(found) if found
              else "no aliases (try: {} alias irp)".format(branding.EXE_NAME))
        return 0
    path = register.alias(args.name, remove=args.remove, dry_run=args.dry_run)
    verb = "would remove" if (args.remove and args.dry_run) else (
        "removed" if args.remove else
        ("would write" if args.dry_run else "wrote"))
    print("{} {}".format(verb, path))
    if not args.remove and not args.dry_run:
        print("open a new terminal, then: {}".format(args.name))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # The first start under a new name brings the old name's preferences,
    # presets and registration manifest along (`branding.py`), BEFORE the
    # log, the style or `register --clean-legacy` read that folder.
    branding.adopt_legacy_dir()
    if argv and argv[0] in COMMANDS:
        args = build_command_parser().parse_args(argv)
        return _register(args) if args.command == "register" else _alias(args)
    args = build_parser().parse_args(argv)
    # Imported HERE rather than at the top, so `--version`, `register` and
    # `alias` do not pay for Qt - which is most of the start-up time.
    from PySide6.QtWidgets import QApplication
    from .core import log, style
    from .ui.window import MainWindow

    # The log first: an error from here on goes into it, and one inside a Qt
    # slot no longer ends the program (`core/log.py`).
    log.install()

    # The user's house style (Edit > Settings). Loaded HERE and not by the
    # window, so a window made by a test or a tool never reads the defaults
    # of whoever happens to run it.
    style.load_preferences()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(branding.APP_NAME)
    app.setOrganizationName(branding.SETTINGS_ORG)
    app.setApplicationDisplayName(branding.APP_NAME)
    window = MainWindow()
    log.on_error = window.show_error
    # Where it was last time; maximized the first time.
    if window.restore_layout():
        window.show()
    else:
        window.showMaximized()
    sessions = [p for p in args.paths
                if str(p).lower().endswith(branding.SESSION_EXT)]
    if sessions:
        window.open_session(sessions[0])
    others = [p for p in args.paths if p not in sessions]
    if others:
        window.open_files(others)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
