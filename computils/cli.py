import argparse, glob, sys
from pathlib import Path

from .defaults  import Defaults
from .prompts import AskBool, AskStr
from .intent import Intent, IntentDraft
from .actions import CubeOption, Action, FILE_ACTIONS
from . import agent

# Each action flag's argparse dest, and the Action it sets (the parser allows only one per invocation)
ACTION_FLAGS = {
    "run": Action.RUN, "singlePoint": Action.SINGLE_POINT, "bench": Action.BENCHMARK, "cube": Action.CUBE,
    "rerun": Action.RERUN, "formcheck": Action.FORM_CHECK, "excel": Action.EXCEL, "goodvibes": Action.GOODVIBES,
    "first": Action.FIRST_TIME_SETUP, "update": Action.UPDATE, "update_plugin": Action.UPDATE_PLUGIN,
    "environment": Action.ENVIRONMENT,
    "init": Action.INIT_PROJECT,
    "profile": Action.PROFILE, "refresh": Action.REFRESH, "attributions": Action.ATTRIBUTIONS,
    "neva_touch": Action.NEVA_TOUCH, "info": Action.INFO, "methods": Action.METHODS, "optimize": Action.OPTIMIZE,
    "si": Action.SI_SCRAPER, "agent": Action.AGENT, "xyz": Action.XYZ,
}


class _Parser(argparse.ArgumentParser):
    """argparse, but a usage error is raised (agent.UsageError), so Main can put it in the report (exit 2 either way)."""
    def error(self, message: str):
        self.print_usage(sys.stderr)
        raise agent.UsageError(message)


# Defines all the terminal flags the program can accept
def BuildParser() -> argparse.ArgumentParser:
    """The same argparse setup as commandLineParser, factored out for reuse."""
    parser = _Parser(prog="cu", description="CompUtils CLI")

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
    actionGroup.add_argument('--update-plugin', action='store_true',
                             help="Update the h2p-computils agent plugin on this computer (its git clone, with your "
                                  "GitHub sign-in): run on your own computer, not the cluster.")
    actionGroup.add_argument('-env', '--environment', nargs='*', metavar="ARG",
                             help="Show the CompUtils environments' status. 'refresh|rebuild|install [ENVIRONMENT]' "
                                  "updates one in the background (default: the one CompUtils runs from), e.g. "
                                  "'-env install debug'.")
    actionGroup.add_argument('-init', '--init', action='store_true', help="Mark the CWD as a project root.")
    actionGroup.add_argument('-profile', '--profile', nargs='?', const="", metavar="FILE",
                             help="Apply a lab profile (its clusters and shared settings). Without FILE, re-apply the last one.")
    actionGroup.add_argument('-refresh', '--refresh', nargs='?', const="", metavar="project",
                             help="Rewrite the config files in the current layout, keeping your values (old defaults and "
                                  "invalid values become the current default). With 'project', also the nearest project.toml.")
    actionGroup.add_argument('-attributions', '--attributions', action='store_true',
                             help="Show the license, disclaimer and credits: every program, library, method and service CompUtils relies on, with licenses and citations.")
    actionGroup.add_argument('-opt', '--optimize', nargs='*', metavar="FILE",
                             help="Write and submit an optimization of one molecule (an .xyz, a prior input or "
                                  "output, or --smiles in place of FILE), as the TUI's Optimization builder does. See "
                                  "'optimization' below.")
    actionGroup.add_argument('-si', '--si', action='store_true',
                             help="Write SI_coordinates.txt from this folder's outputs, as the TUI's SI Scraper does. "
                                  "See 'si scraper' below.")
    actionGroup.add_argument('-agent', '--agent', nargs='?', const="", metavar="TOPIC",
                             help="Print the skill that teaches an AI agent to drive CompUtils over ssh (SKILL.md), or "
                                  "one of its topics. See 'for agents and scripts' below.")
    actionGroup.add_argument('-info', '--info', nargs='+', metavar="FILE",
                             help="Show what each file says (status, charge, method, energy, thermal corrections, "
                                  "frequencies, convergence...), read once each. Read-only.")
    actionGroup.add_argument('-xyz', '--xyz', nargs='*', metavar="FILE",
                             help="Write each file's last geometry (an output's, or an input's own) as NAME.xyz, Gaussian "
                                  "or ORCA; or, with --smiles and --name, a SMILES' RDKit conformer.")
    actionGroup.add_argument('-methods', '--methods', action='store_true',
                             help="List the Optimization and Benchmark Methods in force here (project, else global).")
    # Handled in Main() before parsing; listed here for --help and so argparse rejects it alongside another action
    actionGroup.add_argument('-tui', '--tui', action='store_true', help="Open the TUI. Cannot be combined with any other flag.")
    # The Neva-Touch Scheduler's hook (nevatouch.HookCommand writes it into job scripts): never typed, so not listed
    actionGroup.add_argument('--neva-touch', nargs=3, metavar=("STEP", "FILE", "JOBID"), help=argparse.SUPPRESS)

    # Modifiers (apply to whichever action was chosen, where relevant)
    parser.add_argument('-st', '--stalk', action='store_true',
                        help="Stalk the submitted jobs. On its own, stalk every tracked job (any directory).")
    parser.add_argument('--dismiss', nargs='+', metavar="JOB",
                        help="With -st: stop tracking these jobs (IDs or names; 'ended': every ended one).")
    parser.add_argument('--wait', nargs='*', metavar="JOB",
                        help="With -st: wait until one of these jobs (IDs or names; none: every running one) ends, then "
                             "report it and exit. For agents: run it as a background command instead of polling.")
    parser.add_argument('-ch', '--checkpoint', action='store_true', help="Enable Gaussian checkpoint files.")
    parser.add_argument('-nbo', '--nbo7', action='store_true', help="Enable NBO7 keylist.")
    parser.add_argument('-ovr', '--override', type=int, default=0, help="Index override for benchmark methods (zero-indexed).")
    # A job's own SLURM settings (the builders' rows), for any job command; where the cluster's header has the line
    parser.add_argument('--wall-time', default=None, metavar="HOURS",
                        help="With a job command: the jobs' wall time in whole hours (default: the config's wallTime).")
    parser.add_argument('--partition', default=None, metavar="NAME",
                        help="With a job command: the jobs' partition (default: the config's).")
    parser.add_argument('--cluster', default=None, metavar="NAME",
                        help="With a job command: the jobs' cluster, SLURM's -M (default: the config's; only where the "
                             "submission header sends -M).")
    # -opt's settings (the Optimization builder's rows). --cpus/--memory are also the resources of the job a
    # Neva-Touch Scheduler hook generates (written into the hook)
    optGroup = parser.add_argument_group("optimization (-opt)")
    optGroup.add_argument('--method', metavar="NAME",
                          help="An Optimization Method by name (cu -methods lists them), or \"From Source\" for a prior "
                               "job's own route. Asked when not given.")
    optGroup.add_argument('--route', metavar="ROUTE",
                          help="A route of your own instead (as in Config › Job Keylists). With -sp: instead of "
                               "Benchmark Methods' entry.")
    optGroup.add_argument('--charge', type=int, default=None, help="Default: the prior job's; asked for an .xyz.")
    optGroup.add_argument('--mult', type=int, default=None, metavar="MULTIPLICITY",
                          help="Default: the prior job's; asked for an .xyz.")
    optGroup.add_argument('--spin', type=str.lower, default=None, metavar="css|oss",
                          help="A singlet's spin (default: spinstates.txt, else the prior job's, else CSS).")
    optGroup.add_argument('--name', default=None,
                          help="The job's name (default: the file's). Needed with --smiles: also -xyz's file name.")
    optGroup.add_argument('--cpus', type=int, default=None,
                          help="CPUs of the inputs written (-opt, -sp, -b, -re; default: the config's).")
    optGroup.add_argument('--memory', type=float, default=None, metavar="GB",
                          help="Memory per core in GB (-opt, -sp, -b, -re; default: as the method needs).")
    optGroup.add_argument('--modred', action='append', default=[], metavar="STAGE=LINES",
                          help="A ModRedundant stage's lines: a file, or lines separated by ';' (e.g. 1='B 1 2 F').")
    optGroup.add_argument('--then-sp', action=argparse.BooleanOptionalAction, default=None,
                          help="The Neva-Touch Scheduler submits the single point when the optimization ends "
                               "(-opt: default qol.toml's autoNTS; -re: each re-run that optimizes, default off).")
    optGroup.add_argument('--sp-cpus', type=int, default=None, help="The single point's CPUs (default: the config's).")
    optGroup.add_argument('--sp-memory', type=float, default=None, metavar="GB",
                          help="The single point's memory per core in GB (default: as its method needs).")
    optGroup.add_argument('--sp-method', default=None, metavar="NAME",
                          help="The single point's method: a Benchmark Methods entry by name (cu -methods; default: "
                               "the first).")
    optGroup.add_argument('--sp-route', default=None, metavar="ROUTE",
                          help="The single point's route of your own instead.")
    optGroup.add_argument('--sp-wall-time', default=None, metavar="HOURS",
                          help="The single point's wall time in whole hours (default: the config's).")
    optGroup.add_argument('--sp-partition', default=None, metavar="NAME",
                          help="The single point's partition (default: the optimization's).")
    optGroup.add_argument('--sp-cluster', default=None, metavar="NAME",
                          help="The single point's cluster, SLURM's -M (default: the optimization's).")
    optGroup.add_argument('--sp-nbo', action='store_true', help="The single point writes the NBO keylist (Gaussian).")
    optGroup.add_argument('--sp-checkpoint', action='store_true', help="The single point writes a checkpoint (Gaussian).")
    # A SMILES instead of a file (smiles.py): -opt's molecule, or -xyz's geometry
    smilesGroup = parser.add_argument_group("from a SMILES (-opt, -xyz)")
    smilesGroup.add_argument('--smiles', default=None, metavar="SMILES",
                             help="Build the molecule from a SMILES with RDKit instead of reading a file (quote it): "
                                  "its charge and multiplicity come from the SMILES (formal charges; radicals + 1).")
    smilesGroup.add_argument('--rdkit-conformers', type=int, default=None, metavar="N",
                             help="How many RDKit conformers to build (default: qol.toml's rdkitConformers).")
    smilesGroup.add_argument('--rdkit-conformer', type=int, default=None, metavar="RANK",
                             help="Which RDKit conformer to use, by energy (default: 1, the lowest).")
    # -gv's settings (the GoodVibes Builder's rows): any of them, or a run nobody can answer, skips the wizard
    gvGroup = parser.add_argument_group("goodvibes (-gv; without these the wizard asks, unless nobody can answer)")
    gvGroup.add_argument('--qh', action=argparse.BooleanOptionalAction, default=None,
                         help="Head-Gordon's quasi-harmonic enthalpy (default: on).")
    gvGroup.add_argument('--cutoff', type=float, default=None, metavar="CM-1", help="The qh cutoff (default: 100).")
    gvGroup.add_argument('--entropy', type=str.lower, default=None, metavar="grimme|truhlar",
                         help="The quasi-harmonic entropy (default: grimme).")
    gvGroup.add_argument('--temperature', type=float, default=None, metavar="K", help="Default: 298.15.")
    gvGroup.add_argument('--concentration', type=float, default=None, metavar="MOL/L", help="Default: none (1 atm).")
    gvGroup.add_argument('--scale', default=None, metavar="FACTOR|auto",
                         help="The vibrational scale factor (default: 1.0; auto: GoodVibes picks one from the method).")
    gvGroup.add_argument('--spc', default=None, metavar="SUFFIX|none",
                         help="The single point files' suffix (default: detected, else the config's; none: no single "
                              "point corrections). -si takes a suffix only.")
    gvGroup.add_argument('--gv-allow', default=None, metavar="CATEGORIES",
                         help="Waive the consistency check's stops where differences are intended (e.g. a benchmark): "
                              "theory, solvation, dispersion, geometry, comma separated, or all (no check pass).")
    gvGroup.add_argument('--gv-keys', default=None, metavar="KEYS", help="More keys, passed to GoodVibes as written.")

    # -si's settings (the SI Scraper's rows)
    siGroup = parser.add_argument_group("si scraper (-si)")
    siGroup.add_argument('--opt-label', default=None, metavar="TEXT",
                         help="How the SI names the optimization method, e.g. '(U)B3LYP-D3/6-31G(d)'. Asked when not given.")
    siGroup.add_argument('--sp-label', default=None, metavar="TEXT", help="How it names the single point method. Asked too.")
    siGroup.add_argument('--phase', default=None, metavar="TEXT", help="Default: 'in solution'.")
    siGroup.add_argument('--free', type=str.lower, default=None, metavar="qh|g",
                         help="Free energies: qh (GoodVibes' qh-G, the default) or g.")
    siGroup.add_argument('--output', default=None, metavar="FILE", help="Default: SI_coordinates.txt.")
    siGroup.add_argument('--species', default=None, metavar="A,B,...",
                         help="The species to write, in order (default: list.txt's, else every one that can be).")

    # A Neva-Touch Scheduler re-run's count and the original stage it starts at (written into its hook; not listed)
    parser.add_argument('--reruns', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--first-stage', type=int, default=1, help=argparse.SUPPRESS)
    # A Neva-Touch Scheduler hook's chain file (nevatouch.ChainPath; written into the hook, not listed)
    parser.add_argument('--chain', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--plugin-dir', type=Path, default=None, metavar="FOLDER",
                        help="With --update-plugin, the plugin's folder (default: ~/.gemini/config/plugins/h2p-computils).")
    parser.add_argument('--update-branch', type=str, default=None, help="With --update, install from this branch (default: the one CompUtils was installed from, else dev).")

    # For agents and scripts (agent.py): any action
    agentGroup = parser.add_argument_group("for agents and scripts")
    agentGroup.add_argument('--json', action='store_true',
                            help="Print one JSON report on stdout (messages go to stderr). With -st, one snapshot.")
    agentGroup.add_argument('--answer', action='append', default=[], metavar="KEY=VALUE",
                            help="Answer a question in advance (a run without a TTY stops at an unanswered one, exit 3).")
    agentGroup.add_argument('--dry-run', action='store_true',
                            help="Write the inputs and job scripts, but submit nothing.")
    agentGroup.add_argument('--ssh-host', default=None, metavar="ALIAS",
                            help="With -agent: the name the agent's computer reaches this cluster by (its ssh alias).")
    agentGroup.add_argument('--local-cu', default=None, metavar="PATH",
                            help="With -agent: CompUtils' cu on the agent's own computer (with --local-python), "
                                 "recorded in the skill so setup never runs again.")
    agentGroup.add_argument('--local-python', default=None, metavar="PATH",
                            help="With -agent: CompUtils' Python on the agent's own computer (with --local-cu).")
    agentGroup.add_argument('--no-local', action='store_true',
                            help="With -agent: the human chose not to install CompUtils on their own computer "
                                 "(recorded in the skill, never offered again).")

    return parser


def _LocalInstall(args):
    """-agent's record of CompUtils on the agent's own computer (agentskill.LocalInstall), checked: both paths or
    --no-local, and nothing the skill (plain ASCII) or an ssh command line (no ", $ or backticks) can't carry."""
    from .agentskill import LocalInstall
    if args.no_local:
        if args.local_cu is not None or args.local_python is not None:
            raise agent.UsageError("--no-local can't go with --local-cu or --local-python.")
        return LocalInstall(declined=True)
    if (args.local_cu is None) != (args.local_python is None):
        raise agent.UsageError("--local-cu and --local-python go together.")
    if args.local_cu is None:
        return LocalInstall()
    for flag, path in (("--local-cu", args.local_cu), ("--local-python", args.local_python)):
        if not path.strip() or not path.isascii() or any(character in path for character in '"$`'):
            raise agent.UsageError(f"{flag} takes a plain ASCII path, with no double quotes, $ or backticks.")
    return LocalInstall(args.local_cu.strip(), args.local_python.strip())


def ParseCLI(argv: list[str]) -> Intent:
    """Parse CLI args into a validated, finalized Intent.

    Sub-prompts (stalk-loop confirm, cube options, rerun keylist order,
    goodvibes wizard) happen here, populating the draft before validation.
    """
    args  = BuildParser().parse_args(argv)
    draft = IntentDraft()
    # Before any question below can be asked
    agent.SetAnswers(args.answer)
    agent.dryRun = args.dry_run

    # Set the action from whichever action flag was given
    # Given, not truthy: a bare -gv parses as []
    flag = next((dest for dest in ACTION_FLAGS if getattr(args, dest) not in (None, False)), None)
    if flag is None and not args.stalk and not args.dismiss and args.wait is None:
        raise agent.UsageError("No action specified. Run `cu --help` for usage.")
    # -st on its own stalks every tracked job (--dismiss and --wait are -st's too)
    draft.action = ACTION_FLAGS[flag] if flag else Action.STALK

    # File-bearing actions: expand globs if the shell didn't, collect all files
    if draft.action in FILE_ACTIONS:
        # A bare -gv reads every output in the CWD, as GoodVibes itself is usually run (-opt and -xyz take none with
        # --smiles)
        given = getattr(args, flag)
        for entry in given or ([f"*{Defaults.outputExtension}"] if draft.action == Action.GOODVIBES else []):
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
    if args.environment is not None:
        if len(args.environment) > 2:
            raise agent.UsageError("-env takes at most a job and an environment, e.g. '-env install debug'.")
        job, draft.environmentVariant = (args.environment + ["", ""])[:2]
        draft.environmentJob = job.lower()
    gvFlags = [args.qh, args.cutoff, args.entropy, args.temperature, args.concentration, args.scale, args.gv_allow,
               args.gv_keys]
    siFlags = [args.opt_label, args.sp_label, args.phase, args.free, args.output, args.species]
    if any(value is not None for value in gvFlags) and draft.action != Action.GOODVIBES:
        raise agent.UsageError("--qh, --cutoff, --entropy, --temperature, --concentration, --scale, --gv-allow and "
                               "--gv-keys go with -gv.")
    if any(value is not None for value in siFlags) and draft.action != Action.SI_SCRAPER:
        raise agent.UsageError("--opt-label, --sp-label, --phase, --free, --output and --species go with -si.")
    if args.spc is not None and draft.action not in (Action.GOODVIBES, Action.SI_SCRAPER):
        raise agent.UsageError("--spc goes with -gv or -si.")
    localFlags = (args.local_cu is not None, args.local_python is not None, args.no_local)
    if (args.ssh_host is not None or any(localFlags)) and draft.action != Action.AGENT:
        raise agent.UsageError("--ssh-host, --local-cu, --local-python and --no-local go with -agent.")
    if draft.action == Action.AGENT:
        draft.agentTopic, draft.agentSshHost = args.agent.strip().lower(), args.ssh_host or ""
        draft.agentLocal = _LocalInstall(args)
    if draft.action == Action.SI_SCRAPER:
        draft.siOptLabel, draft.siSpLabel, draft.siOutput = args.opt_label, args.sp_label, args.output
        draft.siPhase = args.phase if args.phase is not None else draft.siPhase
        draft.siFree = args.free or "qh"
        draft.siSpecies = [name.strip() for name in args.species.split(",") if name.strip()] if args.species else None
        if args.spc is not None and args.spc.lower() == "none":
            raise agent.UsageError("-si needs the single point files (its SP lines are theirs): --spc none goes with -gv.")
        draft.singlePointPattern = args.spc.lstrip("_") if args.spc else None
    # A SMILES instead of a file (-opt, -xyz); --name names what it makes
    draft.smiles, draft.rdkitConformers, draft.rdkitConformer = args.smiles, args.rdkit_conformers, args.rdkit_conformer
    if args.xyz is not None:
        draft.optName = args.name
    # The inputs' own resources (-opt, -sp, -b, -re; Validate refuses them elsewhere: an input as written has its own)
    draft.cpus, draft.memoryRatio = args.cpus, args.memory
    # The Neva-Touch Scheduler's next job: -opt's, and each optimizing re-run's
    thenFlags = any(value is not None for value in (args.then_sp, args.sp_cpus, args.sp_memory, args.sp_method,
                                                    args.sp_route, args.sp_wall_time, args.sp_partition,
                                                    args.sp_cluster)) or args.sp_nbo or args.sp_checkpoint
    if args.optimize is not None or args.rerun is not None:
        draft.optThenSP, draft.optSpCpus, draft.optSpMemory = args.then_sp, args.sp_cpus, args.sp_memory
        draft.optSpMethod, draft.optSpRoute, draft.optSpWallTime = args.sp_method, args.sp_route, args.sp_wall_time
        draft.optSpPartition, draft.optSpCluster = args.sp_partition, args.sp_cluster
        draft.optSpNbo, draft.optSpCheckpoint = args.sp_nbo, args.sp_checkpoint
    elif thenFlags and not args.neva_touch:
        raise agent.UsageError("--then-sp and --sp-* go with -opt or -re.")
    if args.optimize is not None:
        draft.optMethod, draft.optRoute, draft.optName = args.method, args.route, args.name
        draft.optCharge, draft.optMultiplicity, draft.optSpin = args.charge, args.mult, args.spin
        for entry in args.modred:
            stage, separator, lines = entry.partition("=")
            if not separator or not stage.strip().isdigit():
                raise agent.UsageError(f"--modred takes STAGE=LINES, e.g. 1='B 1 2 F' (got '{entry}').")
            path = Path(lines.strip()).expanduser()
            draft.optModRed[int(stage)] = (path.read_text() if path.is_file()
                                           else "\n".join(line.strip() for line in lines.split(";") if line.strip()))
    elif any(value is not None for value in (args.method, args.charge, args.mult, args.spin,
                                             args.name if args.xyz is None else None)) or args.modred:
        if not args.neva_touch:
            raise agent.UsageError("--method, --charge, --mult, --spin, --name and --modred go with -opt.")
    # -sp's route of your own (a Neva-Touch Scheduler chain step's too)
    if args.route is not None and args.optimize is None:
        from .catalog import UnknownSelectors
        unknown = UnknownSelectors(args.route)
        if unknown:
            raise agent.UsageError(f"--route: unknown selector {', '.join(unknown)}.")
        draft.route = args.route
    if args.neva_touch:
        draft.nevaTouchStep, output, draft.nevaTouchJob = args.neva_touch
        draft.files = [Path(output)]
        draft.cpus, draft.memoryRatio = args.cpus, args.memory
        draft.reRuns, draft.firstStage = args.reruns, args.first_stage
        draft.chain = args.chain

    # Modifiers
    draft.dismiss       = args.dismiss or []
    draft.wait          = args.wait
    draft.stalk         = args.stalk
    draft.checkpoint    = args.checkpoint
    draft.nbo7          = args.nbo7
    draft.indexOverride = args.override
    draft.wallTime, draft.partition, draft.cluster = args.wall_time, args.partition, args.cluster
    # Only a line the cluster's submission header has can take one (Bridges2's sends no -M)
    from .jobs import HeaderSends
    for flag, key, value in (("--wall-time", "wallTime", args.wall_time), ("--partition", "partition", args.partition),
                             ("--cluster", "cluster", args.cluster), ("--sp-wall-time", "wallTime", args.sp_wall_time),
                             ("--sp-partition", "partition", args.sp_partition),
                             ("--sp-cluster", "cluster", args.sp_cluster)):
        if value is not None and not HeaderSends(key):
            raise agent.UsageError(f"{flag} has nothing to set: {Defaults.hpcType}'s submission header has no line for "
                                   f"it (slurm.toml's submissionList).")
    draft.updateBranch  = args.update_branch
    draft.pluginDir     = args.plugin_dir

    # Sub-prompts — preserved from current CLI behavior. With --json, or with nobody at a terminal to answer (an agent's
    # ssh call), -st is one snapshot: never a loop, so never the loop question (nor with --dismiss or --wait)
    from . import prompts
    if draft.stalk and prompts.interactive and not agent.jsonOutput and not draft.dismiss and draft.wait is None:
        draft.stalkLoop = AskBool("Enable stalk looping (i.e. re-initialize until all jobs terminate)?", "Y", key="stalk-loop")

    if draft.action == Action.CUBE:
        keyText = AskStr("Enter the list of options you want for cube files generated, separated by spaces (e.g. Pot Den"
                         " Val Spin or Range)", key="cube-options")
        keyList = keyText.split()
        for key in keyList:
            try:
                option = CubeOption(key)
            except ValueError:
                agent.Warn(f"Unknown cube option: {key} (ignoring)")
                continue
            draft.cubeOptions.append(option)
        if not draft.cubeOptions:
            raise agent.UsageError(f"No known cube option given (choose from "
                                   f"{' '.join(option.value for option in CubeOption)}).")
        if CubeOption.RANGE in draft.cubeOptions:
            draft.orbitalRange = AskStr("Enter the range of MOs you want printed (e.g. 10-15)", key="orbital-range")

    if draft.action == Action.GOODVIBES:
        from . import prompts
        from .analysis import AllowArgument, goodVibesInteractive, GoodVibesDefaults, SelectionSuffix
        if prompts.interactive and all(value is None for value in gvFlags) and args.spc is None:
            goodVibesInteractive(draft)
        else:
            # The GoodVibes Builder's starting values, then the flags: what an agent (or a script) runs
            detected = SelectionSuffix(draft.files) or Defaults.singlePointExtra.lstrip("_")
            GoodVibesDefaults(draft, detected)
            if args.qh is not None:
                draft.headGordonEnthalpy = args.qh
            if args.cutoff is not None:
                draft.freqCutoff = args.cutoff
            if args.entropy is not None:
                if args.entropy not in ("grimme", "truhlar"):
                    raise agent.UsageError(f"--entropy takes grimme or truhlar (got '{args.entropy}').")
                draft.truhlarEntropy = args.entropy == "truhlar"
            if args.temperature is not None:
                draft.tempCorrection = args.temperature
            if args.concentration is not None:
                draft.concCorrection = args.concentration
            if args.scale is not None:
                try:
                    draft.vibeScale = None if args.scale.lower() == "auto" else float(args.scale)
                except ValueError:
                    raise agent.UsageError(f"--scale takes a number or auto (got '{args.scale}').") from None
            if args.spc is not None:
                draft.singlePointPattern = None if args.spc.lower() == "none" else args.spc.lstrip("_")
            if args.gv_allow is not None:
                draft.allowDifferences = AllowArgument(args.gv_allow)
            draft.extraKeys = args.gv_keys

    # Validate
    errors = draft.Validate()
    if errors:
        raise agent.UsageError(" ".join(errors))

    return draft.Finalize()