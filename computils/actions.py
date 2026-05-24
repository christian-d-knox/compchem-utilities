"""Closed sets of values used by Intent classes and the dispatcher."""
from enum import Enum

# Part-for-Parcel each CLI argument that PERFORMS a task
class Action(Enum):
    """The top-level action one invocation of cu performs.

    One CLI invocation produces exactly one Intent, which has exactly
    one Action. Modifier flags (-st, -ch, -nbo, -ovr) are fields on
    JobIntent subclasses, not separate Actions.
    """
    RUN              = "run"        # -r
    SINGLE_POINT     = "sp"         # -sp
    BENCHMARK        = "bench"      # -b
    CUBE             = "cube"       # -cu
    RERUN            = "rerun"      # -re
    FORM_CHECK       = "formcheck"  # -form
    # Formatting the Excel is a task, just like running GoodVibes is
    EXCEL            = "excel"      # -ex
    GOODVIBES        = "goodvibes"  # -gv
    FIRST_TIME_SETUP = "first"      # -first
    UPDATE           = "update"     # -up

# gimmeCubes() requires multiple selections at runtime
class CubeOption(Enum):
    """Cube file types selectable in `cu -cu`."""
    POTENTIAL = "Pot"
    DENSITY   = "Den"
    VALENCE   = "Val"
    SPIN      = "Spin"
    RANGE     = "Range"   # requires orbitalRange to be set