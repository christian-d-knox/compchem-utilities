"""Shared Rich Console instance for use across the package."""
from rich.console import Console
from rich.theme import Theme
from rich.panel import Panel

# Semantic style names used throughout the codebase.
# Comments mark the original termcolor name each style replaces.
appTheme = Theme({
    "error":     "bright_red",        # was: light_red
    "warning":   "bright_yellow",     # was: light_yellow
    "good":      "bright_green",      # was: light_green
    "operation": "bright_cyan",       # was: light_cyan and light_blue
    "info":      "bright_magenta",    # was: light_magenta
    "prompt":    "dark_orange",
})

console = Console(theme=appTheme)