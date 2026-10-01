import argparse, glob
from pathlib import Path

from .console   import console
from .defaults  import Defaults
from .prompts import AskBool, AskStr
from .intent import Intent, IntentDraft
from .actions import CubeOption, Action, FILE_ACTIONS

# Each action flag's argparse dest, and the Action it sets (the parser allows only one per invocation)
ACTION_FLAGS = {
    "run": Action.RUN, "singlePoint": Action.SINGLE_POINT, "bench": Action.BENCHMARK, "cube": Action.CUBE,
    "rerun": Action.RERUN, "formcheck": Action.FORM_CHECK, "excel": Action.EXCEL, "goodvibes": Action.GOODVIBES,
    "first": Action.FIRST_TIME_SETUP, "update": Action.UPDATE, "init": Action.INIT_PROJECT,
    "profile": Action.PROFILE, "refresh": Action.REFRESH,
}


# Defines all the terminal flags the program can accept
def BuildParser() -> argparse.ArgumentParser:
    """The same argparse setup as commandLineParser, factored out for reuse."""
    parser = argparse.ArgumentParser(description="CompUtils CLI")

    # Action flags (mutually exclusive — one action per invocation)
    actionGroup = parser.add_mutually_exclusive_group()
    actionGroup.add_argument('-r', '--run', nargs='+', metavar="FILE", help="Submit jobs as-written.")
    actionGroup.add_argument('-sp', '--singlePoint', nargs='+', metavar="FILE", help="Generate + submit single-point calculations.")
    actionGroup.add_argument('-b', '--bench', nargs='+', metavar="FILE", help="Generate + submit a benchmark suite.")
    actionGroup.add_argument('-cu', '--cube', nargs='+', metavar="FILE", help="Generate cube files via cubegen.")
    actionGroup.add_argument('-re', '--rerun', nargs='+', metavar="FILE", help="Regenerate a failed job and re-submit.")
    actionGroup.add_argument('-form', '--formcheck', nargs='+', metavar="FILE", help="Run formchk on Gaussian checkpoint files.")
    actionGroup.add_argument('-ex', '--excel', type=str, metavar="FILE", help="Convert GoodVibes output to xlsx.")
    actionGroup.add_argument('-gv', '--goodvibes', nargs='*', metavar="FILE",
                             help="Run GoodVibes interactively on the given outputs (default: every output here), then convert to xlsx.")
    actionGroup.add_argument('-first','--first', action='store_true', help="Re-run first-time setup.")
    actionGroup.add_argument('-up', '--update', action='store_true', help="Update CompUtils from GitHub.")
    actionGroup.add_argument('-init', '--init', action='store_true', help="Mark the CWD as a project root.")
    actionGroup.add_argument('-profile', '--profile', nargs='?', const="", metavar="FILE",
                             help="Apply a lab profile (its clusters and shared settings). Without FILE, re-apply the last one.")
    actionGroup.add_argument('-refresh', '--refresh', nargs='?', const="", metavar="project",
                             help="Rewrite the config files in the current layout, keeping your values (old defaults and "
                                  "invalid values become the current default). With 'project', also the nearest project.toml.")
    # Handled in Main() before parsing; listed here for --help and so argparse rejects it alongside another action
    actionGroup.add_argument('-tui', '--tui', action='store_true', help="Open the TUI. Cannot be combined with any other flag.")

    # Modifiers (apply to whichever action was chosen, where relevant)
    parser.add_argument('-st', '--stalk', action='store_true', help="Enable job stalking.")
    parser.add_argument('-ch', '--checkpoint', action='store_true', help="Enable Gaussian checkpoint files.")
    parser.add_argument('-nbo', '--nbo7', action='store_true', help="Enable NBO7 keylist.")
    parser.add_argument('-ovr', '--override', type=int, default=0, help="Index override for benchmark methods (zero-indexed).")
    parser.add_argument('--update-branch', type=str, default=None, help="With --update, install from this branch (default: the one CompUtils was installed from, else main).")

    return parser


def ParseCLI(argv: list[str]) -> Intent:
    """Parse CLI args into a validated, finalized Intent.

    Sub-prompts (stalk-loop confirm, cube options, rerun keylist order,
    goodvibes wizard) happen here, populating the draft before validation.
    """
    args  = BuildParser().parse_args(argv)
    draft = IntentDraft()

    # Set the action from whichever action flag was given
    # Given, not truthy: a bare -gv parses as []
    flag = next((dest for dest in ACTION_FLAGS if getattr(args, dest) not in (None, False)), None)
    if flag is None:
        console.print("[error]No action specified. Run `cu --help` for usage.[/error]")
        raise SystemExit(2)
    draft.action = ACTION_FLAGS[flag]

    # File-bearing actions: expand globs if the shell didn't, collect all files
    if draft.action in FILE_ACTIONS:
        # A bare -gv reads every output in the CWD, as GoodVibes itself is usually run
        for entry in getattr(args, flag) or [f"*{Defaults.outputExtension}"]:
            if any(c in entry for c in ("*", "?", "[")):
                draft.files.extend(Path(p) for p in glob.glob(entry))
            else:
                draft.files.append(Path(entry))

    # Single-file actions
    if args.excel:
        draft.excelInputFile = Path(args.excel)
    if args.profile:
        draft.profileFile = Path(args.profile).expanduser()
    if args.refresh is not None:
        draft.refreshScope = args.refresh

    # Modifiers
    draft.stalk         = args.stalk
    draft.checkpoint    = args.checkpoint
    draft.nbo7          = args.nbo7
    draft.indexOverride = args.override
    draft.updateBranch  = args.update_branch

    # Sub-prompts — preserved from current CLI behavior
    if draft.stalk:
        draft.stalkLoop = AskBool("Enable stalk looping (i.e. re-initialize until all jobs terminate)?", "Y")

    if draft.action == Action.CUBE:
        keyText = AskStr("Enter the list of options you want for cube files generated, separated by spaces (e.g. Pot Den"
                         " Val Spin or Range)")
        keyList = keyText.split()
        for key in keyList:
            try:
                option = CubeOption(key)
            except ValueError:
                console.print(f"[warning]Unknown cube option: {key} (ignoring)[/warning]")
                continue
            draft.cubeOptions.append(option)
        if CubeOption.RANGE in draft.cubeOptions:
            draft.orbitalRange = AskStr("Enter the range of MOs you want printed (e.g. 10-15)")

    if draft.action == Action.GOODVIBES:
        from .analysis import goodVibesInteractive
        goodVibesInteractive(draft)

    # Validate
    errors = draft.Validate()
    if errors:
        for e in errors:
            console.print(f"[error]Error: {e}[/error]")
        raise SystemExit(2)

    return draft.Finalize()