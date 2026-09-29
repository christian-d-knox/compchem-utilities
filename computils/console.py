"""Shared Rich Console instance for use across the package."""
from rich.console import Console
from rich.theme import Theme
from rich.box import Box, SQUARE
from rich import panel


class Panel(panel.Panel):
    """Rich's Panel with square corners, the house style for boxes in the CLI and the TUI. Import Panel from here."""
    def __init__(self, *args, box: Box = SQUARE, **kwargs) -> None:
        super().__init__(*args, box=box, **kwargs)


# Semantic style names used throughout the codebase.
# Comments mark the original termcolor name each style replaces.
lowColorTheme = Theme({
    "error":     "bright_red",        # was: light_red
    "warning":   "bright_yellow",     # was: light_yellow
    "good":      "bright_green",      # was: light_green
    "operation": "bright_cyan",       # was: light_cyan and light_blue
    "info":      "bright_magenta",    # was: light_magenta
    "prompt":    "#875F00",           # probably still needs adjustment, ideally darker yellow
})

hexCodeTheme = Theme({
    "error": "#FF0000",
    "warning": "#FFFF00",
    "good": "#00FF00",
    "operation": "#00FFFF",
    "info": "#FF00FF",
    "prompt": "#D75F00",
})

console = Console(theme=lowColorTheme)

# Updates the theme after initially booting in low color mode
def ApplyTheme(themeName: str) -> None:
    # lowColor is already active (and anything else falls back to it)
    if themeName == "hexCode":
        console.push_theme(hexCodeTheme)