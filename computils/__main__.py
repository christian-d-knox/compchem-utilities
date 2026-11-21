"""Entry point for the cu command (and `python -m computils`)."""
import sys
from pathlib import Path
from .defaults import Defaults
from .catalog  import Catalog
from .console  import ApplyTheme, console
from .project  import LoadProjectConfig
from . import agent


def Main() -> int:
    """Runs one cu command and returns its exit code (agent.Exit): what happened, for scripts and agents. With --json,
    the run's report is printed on stdout too (agent.py)."""
    argv = sys.argv[1:]
    # Help touches nothing: no config is written and no setup is asked (conda-installer.py checks the install with it)
    if any(arg in ("-h", "--help") for arg in argv):
        from .cli import BuildParser
        BuildParser().print_help()
        return 0
    # The environment check (conda-installer.py's, before an environment is swapped in): touches nothing either
    if argv == ["--self-test"]:
        from .environment import SelfTest
        return SelfTest()

    agent.Configure(argv)
    agent.Reset(_CommandName(argv))
    try:
        _Run(argv)
    except agent.QuestionNeeded as question:
        agent.report.question = question.question
        console.print(f"[warning]Needs an answer: {question}[/warning]", markup=False)
    except agent.UsageError as error:
        agent.report.usage, agent.report.error = True, str(error)
        console.print(f"cu: error: {error}", style="error", markup=False)
    except agent.NeedsHuman as human:
        agent.Human(str(human), human.then)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        # A CompUtils bug: the traceback goes to stderr, as Python would print it, and the report names it
        import traceback
        traceback.print_exc()
        agent.Crashed()
    # At most once a day, a report also says what is newer on GitHub (never in a hook: no one reads its report)
    if agent.jsonOutput and argv[:1] != ["--neva-touch"]:
        _DailyUpdateCheck(remindPlugin="--update-plugin" not in argv)
    code = agent.Finish()
    # A Neva-Touch Scheduler hook records its own outcome on the tracked job; its exit code means only "crashed or not"
    # (a stalker's fallback, nevatouch.FireStep, reads it that way)
    if argv[:1] == ["--neva-touch"] and code != agent.Exit.CRASHED:
        return 0
    return code


def _DailyUpdateCheck(remindPlugin: bool = True) -> None:
    """update.DailyCheck into the report, and a line for whoever reads stderr when CompUtils itself has news (the
    plugin's reminder is the agent's alone). Never worth failing a report over."""
    try:
        from .update import DailyCheck
        updates = DailyCheck(remindPlugin)
    except Exception:
        return
    if updates:
        agent.report.updates = updates
    if updates and updates.get("command"):
        console.print(f"[info]Update available: {updates['why']} Run `{updates['command']}` when you're ready."
                      "[/info]", highlight=False)


def _CommandName(argv: list[str]) -> str:
    """The action flag, for the report ('opt' for -opt; 'stalk' for -st on its own)."""
    flags = [arg.lstrip("-") for arg in argv if arg.startswith("-") and arg not in ("--json", "--dry-run", "--answer")]
    return flags[0] if flags else ""


def _Run(argv: list[str]) -> None:
    from . import prompts
    # Nobody can answer a question without a TTY (an agent's ssh command, a script) or with --json: one with no
    # --answer stops the run before it acts (prompts.py)
    if agent.jsonOutput or not sys.stdin.isatty():
        prompts.interactive = False
    # A Neva-Touch Scheduler hook (in a job script, or a stalker's run of one): nobody is there to answer, so nothing
    # may prompt, and the startup checks below belong to the runs people start
    nevaTouchHook = argv[:1] == ["--neva-touch"]
    if nevaTouchHook:
        prompts.interactive = False

    # Load configs and lookup data BEFORE handing control to the CLI.
    Defaults.Load()
    # Project overrides must land before the Catalog derives route templates (from jobRelatedKeylists)
    LoadProjectConfig()
    Catalog.Load()

    if nevaTouchHook:
        if Defaults.needsFirstTimeSetup:
            console.print("[error]CompUtils isn't set up for this account (run `cu -first`); the Neva-Touch Scheduler "
                          "took no step.[/error]")
            return
        from .cli      import ParseCLI
        from .dispatch import Dispatch
        Dispatch(ParseCLI(argv))
        return
    opensTUI = argv in (["-tui"], ["--tui"]) or (not argv and Defaults.bareCommandOpensTUI)
    appliesProfile = argv[:1] in (["-profile"], ["--profile"])

    # The environment's status and updates (-env), and the agent plugin's update (--update-plugin), concern the
    # installation, not the cluster: a run nobody can answer does them before setup too (an agent on a computer used
    # only for workbooks, where nobody set cu up)
    deferSetup = not prompts.interactive and argv[:1] in (["-env"], ["--environment"], ["--update-plugin"])
    # Setup is a conversation with a person: a run nobody can answer stops here, saying who must run what
    if not prompts.interactive and not deferSetup and (Defaults.needsFirstTimeSetup
                                                       or Defaults.colorMode not in ("lowColor", "hexCode")):
        raise agent.NeedsHuman("CompUtils isn't set up for this account yet: a person must run `cu` once in a terminal "
                               "on this cluster (it asks about the cluster and notifications).", "cu -first")

    # If Defaults._Validate determined that first-time setup is needed,
    # run it here rather than from inside Defaults.
    if Defaults.colorMode not in ("lowColor", "hexCode") and not deferSetup:
        from .wizards import ColorSetup
        ColorSetup()
    if Defaults.needsFirstTimeSetup and not deferSetup:
        from .wizards import firstTimeSetup
        # `cu -profile FILE` on a fresh install: setup applies it, before offering the clusters it adds
        profileFile = Path(argv[1]).expanduser() if appliesProfile and len(argv) == 2 else None
        result = firstTimeSetup(profileFile)
        # The TUI can't set the cluster (hpcType is setup-only), so it opens only once setup has set one
        if (opensTUI and result is None) or profileFile is not None:
            return
    # Setup deferred (-env before anyone set cu up): the cluster's startup checks below need it, so they wait too
    elif not Defaults.needsFirstTimeSetup:
        if not appliesProfile and sys.stdin.isatty():
            from .profile import CheckProfileSource
            CheckProfileSource()
        # Files the endings of tracked jobs that left the queue since the last run, so a stalker's first ping only
        # covers live jobs (one squeue call, and only while some tracked job is still live)
        from .stalk import CheckTracked
        CheckTracked()
        # Environment jobs that ended since the last run, each reported once (the TUI shows them itself)
        if not opensTUI:
            from .environment import Reports
            for severity, message in Reports():
                agent.Notice(severity, message)
        if opensTUI and "notifications.toml" in Defaults.generatedFiles:
            from .wizards import NotificationSetup
            NotificationSetup()

    ApplyTheme(Defaults.colorMode)

    from .cli   import ParseCLI
    from .dispatch import Dispatch

    if opensTUI:
        if not sys.stdin.isatty():
            raise agent.UsageError("The TUI needs a terminal.")
        # The TUI only builds the Intent; it then runs here exactly as a CLI invocation would
        from .tui import RunTUI, RELAUNCH
        intent = RunTUI()
        if intent == RELAUNCH:
            # Updated in the app: start again on the new code, in the folder the app was in
            import os
            os.execv(sys.executable, [sys.executable, "-m", "computils", "-tui"])
        if intent is None:
            return
    elif "-tui" in argv or "--tui" in argv:
        raise agent.UsageError("-tui cannot be combined with any other flag.")
    else:
        intent = ParseCLI(argv)
    submitted = Dispatch(intent)
    # A dry run's next step is the same command, submitting (unless the command said something more exact)
    if agent.dryRun and agent.report.written and not agent.report.next:
        agent.Next(agent.CommandLine(arg for arg in argv if arg != "--dry-run"), "Submit what was previewed.",
                   show=False)
    # Every submitted job is tracked; -st stalks them right away (with --json, or with nobody at a terminal: one
    # snapshot, never a loop that would hold an agent's shell)
    if getattr(intent, "stalk", False):
        from .stalk import jobStalking, Snapshot
        if agent.jsonOutput or not prompts.interactive:
            Snapshot(submitted)
        else:
            jobStalking(submitted, Defaults.stalkDuration, Defaults.stalkFrequency, intent.stalkLoop)


if __name__ == "__main__":
    raise SystemExit(Main())
