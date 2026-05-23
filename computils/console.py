"""Shared Rich Console instance for use across the package."""
from rich.console import Console
from rich.theme import Theme
from rich.panel import Panel

from .defaults import Defaults
from wizards import ColorSetup

# Semantic style names used throughout the codebase.
# Comments mark the original termcolor name each style replaces.
lowColorTheme = Theme({
    "error":     "bright_red",        # was: light_red
    "warning":   "bright_yellow",     # was: light_yellow
    "good":      "bright_green",      # was: light_green
    "operation": "bright_cyan",       # was: light_cyan and light_blue
    "info":      "bright_magenta",    # was: light_magenta
    "prompt":    "dark_orange",       # probably still needs adjustment
})

hexCodeTheme = Theme({
    "error": "#FF0000",
    "warning": "#FFFF00",
    "good": "#00FF00",
    "operation": "#00FFFF",
    "info": "#FF00FF",
    "prompt": "D75F00",
})

if Defaults.colorMode == "lowColor":
    theme = lowColorTheme
elif Defaults.colorMode == "hexCode":
    theme = hexCodeTheme
else:
    ColorSetup()

console = Console(theme=theme)