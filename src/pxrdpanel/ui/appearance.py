"""The rest of the window, in the plot's colours.

The plot paints its own theme (`ui/plot.py`, `THEMES`). Everything else - the
outliner, the menus, the status bar, every dialog - is Qt's, and it followed
the system's light look while the plot beside it was dark. When the plot
is dark, so should every window be.

So the application palette follows the plot's theme: dark greys (Fusion
style plus a Blender-ish palette) for `blender-default`, Fusion's own
light palette for `light`, the Boombox skin for `boombox`. Fusion in every
case, because a palette only fully applies under a style that draws with
it, and switching styles between two themes would change the widgets'
shapes as well as their colours.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from . import plot as plot_module

#: The theme the application palette was last set for, so a refresh that
#: changes nothing does not repaint every widget in the program.
_applied = None


def dark_palette():
    """The dark palette: Fusion's greys, Blender-ish."""
    p = QPalette()
    grey = QColor(53, 53, 53)
    base = QColor(42, 42, 42)
    text = QColor(220, 220, 220)
    disabled = QColor(128, 128, 128)
    p.setColor(QPalette.Window, grey)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor(58, 58, 58))
    p.setColor(QPalette.ToolTipBase, QColor(42, 42, 42))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor(140, 140, 140))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, grey)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(255, 120, 120))
    p.setColor(QPalette.Link, QColor(110, 160, 220))
    p.setColor(QPalette.Highlight, QColor(70, 105, 150))
    p.setColor(QPalette.HighlightedText, QColor(240, 240, 240))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    p.setColor(QPalette.Disabled, QPalette.Highlight, QColor(80, 80, 80))
    return p


def light_palette():
    """Fusion's classic light palette, written out.

    Not `standardPalette()`: that follows the colour scheme in force at the
    moment it is asked, so taken while the dark scheme was still set it
    handed back a DARK palette and the "light" theme stayed dark."""
    p = QPalette()
    window = QColor(239, 239, 239)
    text = QColor(20, 20, 20)
    disabled = QColor(150, 150, 150)
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, QColor(255, 255, 255))
    p.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
    p.setColor(QPalette.ToolTipBase, QColor(255, 255, 220))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor(120, 120, 120))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, window)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(200, 30, 30))
    p.setColor(QPalette.Link, QColor(30, 90, 180))
    p.setColor(QPalette.Highlight, QColor(48, 140, 198))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def boombox_palette():
    """The Boombox skin: brushed-metal greys, parchment text, the LCD
    green."""
    p = QPalette()
    window = QColor("#232323")
    text = QColor("#d6d6c2")
    disabled = QColor("#6b7178")
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, QColor("#2a2d31"))
    p.setColor(QPalette.AlternateBase, QColor("#2e3236"))
    p.setColor(QPalette.ToolTipBase, QColor("#0c130c"))
    p.setColor(QPalette.ToolTipText, QColor("#39ff7a"))
    p.setColor(QPalette.PlaceholderText, QColor("#9a9a86"))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#3a3f44"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor("#ff5555"))
    p.setColor(QPalette.Link, QColor("#39ff7a"))
    p.setColor(QPalette.Highlight, QColor("#2f7d4a"))
    p.setColor(QPalette.HighlightedText, QColor("#caffd9"))
    p.setColor(QPalette.Mid, QColor("#15171a"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    p.setColor(QPalette.Disabled, QPalette.Highlight, QColor("#3a3f44"))
    return p


#: The window's palette for each of the plot's themes.
PALETTES = {plot_module.THEME_DARK: dark_palette,
            plot_module.THEME_LIGHT: light_palette,
            plot_module.THEME_BOOMBOX: boombox_palette}


def apply(theme, app=None):
    """Give the whole application the palette that goes with `theme`.

    Returns True when anything changed. Also tells Qt which colour scheme is
    in force where it can be told (Qt 6.8+), which is what darkens the
    Windows title bar to match.
    """
    global _applied
    app = app or QApplication.instance()
    if app is None or theme == _applied:
        return False
    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    dark = theme != plot_module.THEME_LIGHT
    # The scheme FIRST, the palette after it: a scheme change makes Qt
    # re-derive colours, and the explicit palette has to be the last word.
    hints = QGuiApplication.styleHints()
    try:
        hints.setColorScheme(Qt.ColorScheme.Dark if dark
                             else Qt.ColorScheme.Light)
    except AttributeError:
        pass                       # an older Qt: the title bar stays as it is
    app.setPalette(PALETTES.get(theme, dark_palette)())
    _applied = theme
    return True


def applied():
    """The theme the application is currently dressed in. For tests."""
    return _applied
