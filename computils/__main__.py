"""Entry point for the cu command (and `python -m computils`)."""
import sys
from .defaults import Defaults
from .catalog  import Catalog
from .console  import ApplyTheme


def Main() -> None:
    # Load configs and lookup data BEFORE handing control to the CLI.
    Defaults.Load()
    Catalog.Load()

    # If Defaults._Validate determined that first-time setup is needed,
    # run it here rather than from inside Defaults.
    if getattr(Defaults, "needsFirstTimeSetup", False):
        from .wizards import firstTimeSetup
        firstTimeSetup()
    elif Defaults.colorMode not in ("lowColor", "highColor"):
        from .wizards import ColorSetup
        ColorSetup()

    ApplyTheme(Defaults.colorMode)
    # In Step 4 this entire block becomes:
    #     from .cli      import ParseCLI
    #     from .dispatch import Dispatch
    #     intent = ParseCLI(sys.argv[1:])
    #     Dispatch(intent)
    # For Step 1, preserve existing dispatch via commandLineParser:
    from .cli   import commandLineParser
    from .stalk import jobStalking

    commandLineParser()

    if Catalog.isStalking:
        jobStalking(
            Catalog.stalkingSet,
            Defaults.stalkDuration,
            Defaults.stalkFrequency,
        )


if __name__ == "__main__":
    Main()