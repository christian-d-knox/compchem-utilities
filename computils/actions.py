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
    UPDATE_PLUGIN    = "plugin"     # --update-plugin: the agent plugin's git clone on this computer (plugin.py)
    ENVIRONMENT      = "env"        # -env
    INIT_PROJECT     = "init"       # -init
    PROFILE          = "profile"    # -profile
    REFRESH          = "refresh"    # -refresh
    STALK            = "stalk"      # -st on its own
    ATTRIBUTIONS     = "attributions"  # -attributions
    NEVA_TOUCH       = "nevatouch"  # --neva-touch: a job's Neva-Touch Scheduler hook (hidden: written into job scripts)
    INFO             = "info"       # -info: what files say (info.FileFacts), read-only
    METHODS          = "methods"    # -methods: the Optimization and Single Point Methods in force here
    OPTIMIZE         = "opt"        # -opt: the Optimization builder, headless (optimize.py)
    SI_SCRAPER       = "si"         # -si: the SI Scraper, headless (scrape.py)
    AGENT            = "agent"      # -agent [TOPIC]: the agent skill, or one of its topics (agentskill.py)
    XYZ              = "xyz"        # -xyz: each file's last geometry as NAME.xyz
    CREST            = "crest"      # -crest: a CREST conformer search of each molecule (crest.py)
    CANCEL           = "cancel"     # -cancel: cancel tracked jobs (stalk.Cancel)
    SCRAPE           = "scrape"     # -scrape: a bounded regex search of files (fileops.ExtractMatches), read-only

# Actions that submit SLURM jobs, which -st can follow
JOB_ACTIONS = (Action.RUN, Action.SINGLE_POINT, Action.BENCHMARK, Action.CUBE, Action.RERUN, Action.CREST)

# Actions that take a batch of files (IntentDraft.Validate, and the TUI's Continue)
FILE_ACTIONS = (Action.RUN, Action.SINGLE_POINT, Action.BENCHMARK, Action.CUBE, Action.RERUN, Action.FORM_CHECK,
                Action.GOODVIBES, Action.INFO, Action.OPTIMIZE, Action.XYZ, Action.CREST, Action.SCRAPE)

# The Neva-Touch Scheduler's steps: what a job's hook does once the job ends. SP submits the single point (an opt's);
# DONE ends the chain (the single point's own hook only reports)
NTS_SP, NTS_DONE = "SP", "Done"
NTS_STEPS = (NTS_SP, NTS_DONE)

# Spin-state class of a molecule, set once in dispatch by spin.ClassifySpin()
class SpinState(Enum):
    """CSS = closed-shell singlet, OSS = open-shell (broken-symmetry) singlet, OPEN = multiplicity > 1."""
    CSS  = "css"
    OSS  = "oss"
    OPEN = "open"

# gimmeCubes() requires multiple selections at runtime
class CubeOption(Enum):
    """Cube file types selectable in `cu -cu`."""
    POTENTIAL = "Pot"
    DENSITY   = "Den"
    VALENCE   = "Val"
    SPIN      = "Spin"
    RANGE     = "Range"   # requires orbitalRange to be set