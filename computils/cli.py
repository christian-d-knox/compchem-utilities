import argparse, glob, os, time, subprocess
from pathlib import Path

from .console   import console
from .prompts import AskBool, AskStr
from .intent import Intent, IntentDraft
from .actions import CubeOption, Action


# Defines all the terminal flags the program can accept
def BuildParser() -> argparse.ArgumentParser:
    """The same argparse setup as commandLineParser, factored out for reuse."""
    parser = argparse.ArgumentParser(description="CompUtils CLI")

    # Action flags (mutually exclusive — one action per invocation)
    actionGroup = parser.add_mutually_exclusive_group()
    actionGroup.add_argument('-r', '--run', type=str, metavar="GLOB", help="Submit jobs as-written.")
    actionGroup.add_argument('-sp', '--singlePoint', type=str, metavar="GLOB", help="Generate + submit single-point calculations.")
    actionGroup.add_argument('-b', '--bench', type=str, metavar="GLOB", help="Generate + submit a benchmark suite.")
    actionGroup.add_argument('-cu', '--cube', type=str, metavar="GLOB", help="Generate cube files via cubegen.")
    actionGroup.add_argument('-re', '--rerun', type=str, metavar="GLOB", help="Regenerate a failed job and re-submit.")
    actionGroup.add_argument('-form', '--formcheck', type=str, metavar="GLOB", help="Run formchk on Gaussian checkpoint files.")
    actionGroup.add_argument('-ex', '--excel', type=str, metavar="FILE", help="Convert GoodVibes output to xlsx.")
    actionGroup.add_argument('-gv', '--goodvibes', action='store_true', help="Run GoodVibes interactively, then convert to xlsx.")
    actionGroup.add_argument('-first','--first', action='store_true', help="Re-run first-time setup.")
    actionGroup.add_argument('-up', '--update', action='store_true', help="Update CompUtils from GitHub.")

    # Modifiers (apply to whichever action was chosen, where relevant)
    parser.add_argument('-st', '--stalk', action='store_true', help="Enable job stalking.")
    parser.add_argument('-ch', '--checkpoint', action='store_true', help="Enable Gaussian checkpoint files.")
    parser.add_argument('-nbo', '--nbo7', action='store_true', help="Enable NBO7 keylist.")
    parser.add_argument('-ovr', '--override', type=int, default=0, help="Index override for benchmark methods (zero-indexed).")
    parser.add_argument('--update-branch', type=str, default="main", help="With --update, install from this branch (default: main).")

    return parser


def ParseCLI(argv: list[str]) -> Intent:
    """Parse CLI args into a validated, finalized Intent.

    Sub-prompts (stalk-loop confirm, cube options, rerun keylist order,
    goodvibes wizard) happen here, populating the draft before validation.
    """
    args  = BuildParser().parse_args(argv)
    draft = IntentDraft()
    pattern: str | None = None

    # Set action and gather any pattern argument
    if   args.run:         draft.action, pattern = Action.RUN,            args.run
    elif args.singlePoint: draft.action, pattern = Action.SINGLE_POINT,   args.singlePoint
    elif args.bench:       draft.action, pattern = Action.BENCHMARK,      args.bench
    elif args.cube:        draft.action, pattern = Action.CUBE,           args.cube
    elif args.rerun:       draft.action, pattern = Action.RERUN,          args.rerun
    elif args.formcheck:   draft.action, pattern = Action.FORM_CHECK,     args.formcheck
    elif args.excel:       draft.action = Action.EXCEL
    elif args.goodvibes:   draft.action = Action.GOODVIBES
    elif args.first:       draft.action = Action.FIRST_TIME_SETUP
    elif args.update:      draft.action = Action.UPDATE
    else:
        console.print("[error]No action specified. Run `cu --help` for usage.[/error]")
        raise SystemExit(2)

    # File-bearing actions: glob and store
    if pattern is not None:
        draft.files = [Path(p) for p in glob.glob(pattern)]

    # Single-file actions
    if args.excel:
        draft.excelInputFile = Path(args.excel)

    # Modifiers
    draft.stalk         = args.stalk
    draft.checkpoint    = args.checkpoint
    draft.nbo7          = args.nbo7
    draft.indexOverride = args.override
    draft.updateBranch  = args.update_branch

    # Sub-prompts — preserved from current CLI behavior
    if draft.stalk:
        draft.stalkLoop = AskBool("Enable stalk looping (i.e. re-initialize until all jobs terminate)?", "Y")

    if draft.action == Action.RERUN:
        keylistOrder = AskBool("Is your input structured as 'opt freq FUNCTIONAL' (Y) or 'FUNCTIONAL other keys' "
                               "(n)?", "Y")
        draft.skipIndex = 2 if keylistOrder else 0

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
        # Reuse the existing interactive prompts via goodVibesInteractive
        from .analysis import goodVibesInteractive
        keyList = goodVibesInteractive()
        # goodVibesInteractive returns a list of CLI-style flags; parse them back into intent fields.
        # Walking the list is more robust than re-prompting separately.
        i = 0
        while i < len(keyList):
            token = keyList[i]
            if token == "-q":
                draft.quasiharmonic = True
                i += 1
            elif token == "-f":
                draft.freqCutoff = float(keyList[i + 1]); i += 2
            elif token == "-t":
                draft.tempCorrection = float(keyList[i + 1]); i += 2
            elif token == "-c":
                draft.concCorrection = float(keyList[i + 1]); i += 2
            elif token == "-v":
                # -v appears both for the default 1.0 and a custom scale; skip if 1.0
                scale = float(keyList[i + 1])
                if scale != 1.0:
                    draft.vibeScale = scale
                i += 2
            elif token == "--spc":
                draft.singlePointPattern = keyList[i + 1]; i += 2
            else:
                # Anything else goes into extraKeys
                if draft.extraKeys is None:
                    draft.extraKeys = token
                else:
                    draft.extraKeys += " " + token
                i += 1

    # Validate
    errors = draft.Validate()
    if errors:
        for e in errors:
            console.print(f"[error]Error: {e}[/error]")
        raise SystemExit(2)

    return draft.Finalize()