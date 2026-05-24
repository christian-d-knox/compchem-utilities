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
    if Defaults.colorMode not in ("lowColor", "hexCode"):
        from .wizards import ColorSetup
        ColorSetup()
    if Defaults.needsFirstTimeSetup:
        from .wizards import firstTimeSetup
        firstTimeSetup()

    ApplyTheme(Defaults.colorMode)
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