"""Entry point for the cu command (and `python -m computils`)."""
import sys
from .defaults import Defaults
from .catalog  import Catalog
from .console  import ApplyTheme, console
from .project  import LoadProjectConfig


def Main() -> None:
    # Load configs and lookup data BEFORE handing control to the CLI.
    Defaults.Load()
    # Project overrides must land before the Catalog derives route templates from benchmarkMethods
    LoadProjectConfig()
    Catalog.Load()

    # If Defaults._Validate determined that first-time setup is needed,
    # run it here rather than from inside Defaults.
    if Defaults.colorMode not in ("lowColor", "hexCode"):
        from .wizards import ColorSetup
        ColorSetup()
    if Defaults.needsFirstTimeSetup:
        from .wizards import firstTimeSetup
        firstTimeSetup()

    ApplyTheme(Defaults.colorMode)

    from .cli   import ParseCLI
    from .dispatch import Dispatch

    argv = sys.argv[1:]
    if argv in (["-tui"], ["--tui"]) or (not argv and Defaults.bareCommandOpensTUI):
        # The TUI only builds the Intent; it then runs here exactly as a CLI invocation would
        from .tui import RunTUI
        intent = RunTUI()
        if intent is None:
            return
    elif "-tui" in argv or "--tui" in argv:
        console.print("[error]-tui cannot be combined with any other flag.[/error]")
        raise SystemExit(2)
    else:
        intent = ParseCLI(argv)
    Dispatch(intent)


if __name__ == "__main__":
    Main()