"""
Teaching agents to drive CompUtils: the one registry every agent-facing command declares itself in, the skill it is
generated into (`cu -agent`: SKILL.md, the format Antigravity and Claude Code both read), the topics an agent fetches
when it needs them (`cu -agent TOPIC`: always the installed version's), and the check that keeps all of it in step with
the CLI and the TUI (`python -m computils.agentskill`, like `python -m computils.attributions`).

When a feature is added (a flag, an Action, a TUI item, a question), the check fails until it has an AgentCommand here
(or is declared human-only, with why), its questions have keys, and any recipe naming it still parses. The skill's
digest then changes, every --json report carries the new one, and the agent regenerates its skill on its next call.
"""
import hashlib, sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .actions import Action

# Recipes: package data, Markdown, with {cu} for the full cu path (and {host} for the ssh alias)
RECIPE_FOLDER = "recipes"
HOST_PLACEHOLDER = "<host>"


@dataclass(frozen=True)
class LocalInstall:
    """CompUtils on the agent's own computer, as setup found it (cu -agent --local-cu/--local-python, or --no-local):
    written into the skill, so the agent remembers it and never checks or offers the install again. Empty: undecided.
    The cluster can't see the PC, so these are the agent's word, written as given."""
    cu:       str  = ""
    python:   str  = ""
    declined: bool = False

    def Words(self) -> list[str]:
        """The flags that give this again (for the skill's regenerate command)."""
        if self.declined:
            return ["--no-local"]
        return ["--local-cu", self.cu, "--local-python", self.python] if self.cu else []


# The skill's digest leaves the PC out (as it does the host): it changes only with agent-facing behaviour
LOCAL_PLACEHOLDER = None


@dataclass(frozen=True)
class AgentCommand:
    """One command as agents see it. flags: the argparse dests it owns (its action flag first). tui: the TUI items it
    mirrors. agent False: a person's only (why says so); hidden: never typed (written into job scripts)."""
    key:       str
    flags:     tuple[str, ...]
    action:    Action | None
    purpose:   str
    example:   str              = ""
    results:   str              = ""
    questions: tuple[str, ...]  = ()
    tui:       tuple[str, ...]  = ()
    agent:     bool             = True
    why:       str              = ""
    hidden:    bool             = False


COMMANDS = [
    AgentCommand("info", ("info",), Action.INFO,
                 "What outputs and inputs say: status, charge/multiplicity, spin, route, solvation, energy, ZPE and "
                 "the thermochemistry totals as printed, H/G corrections, imaginary and lowest frequencies, "
                 "convergence, stability, <S**2>, an optimization's constraints (frozen or scanned coordinates) and "
                 "a Gaussian TS's tsEigenvector (the coordinates its mode moves, with their final values). Read once "
                 "each: never read a .out yourself. A staged output "
                 "(Opt+Freq then SP in one file) gives each stage's results in `stages`.",
                 "-info '*.out' --json", "One record per file (info.FileFacts): a one-stage output's results in the "
                 "record, a staged one's in `stages` (an input's `stages` only lists what each runs), and dataFile; "
                 "next: cu -re / cu -sp suggestions."),
    AgentCommand("xyz", ("xyz", "smiles", "rdkit_conformers", "rdkit_conformer"), Action.XYZ,
                 "Write each file's last geometry (an output's; an input's own; Gaussian or ORCA) as NAME.xyz here, "
                 "e.g. for figures or another program. Or a SMILES' 3D geometry: -xyz --smiles 'SMILES' --name NAME "
                 "(RDKit conformers, the lowest energy unless --rdkit-conformer RANK; the comment line names it).",
                 "-xyz --smiles 'CC(=O)OC1CCCC1' --name cpa --json",
                 "written: the .xyz files; with --smiles, results[0]: atoms, charge, multiplicity, rdkitConformer "
                 "and each RDKit conformer's rank and deltaE (kcal/mol).",
                 ("replace-xyz",), (f"action-{Action.XYZ.value}",)),
    AgentCommand("methods", ("methods",), Action.METHODS,
                 "The Optimization, Benchmark and Conformer Search Methods in force here (project, else global): pick "
                 "-opt's and -crest's --method from these. They differ per project: list them in each folder.",
                 "-methods --json", "One record per section: its level and each method's name, route (a Conformer "
                 "Search Method: its options, level and solvation), program, problem."),
    AgentCommand("opt", ("optimize", "method", "route", "charge", "mult", "spin", "name", "cpus", "memory", "modred",
                         "then_sp", "sp_cpus", "sp_memory", "sp_method", "sp_route", "sp_wall_time", "sp_partition",
                         "sp_cluster", "sp_nbo", "sp_checkpoint", "smiles", "rdkit_conformers", "rdkit_conformer"),
                 Action.OPTIMIZE,
                 "Write and submit an optimization of ONE molecule (an .xyz, a prior input/output, or --smiles 'SMILES' "
                 "--name NAME in place of the file: RDKit builds its 3D geometry, the lowest-energy RDKit conformer "
                 "unless --rdkit-conformer RANK, its charge and multiplicity from the SMILES unless given), as the "
                 "TUI's Optimization builder does; --then-sp has the Neva-Touch Scheduler submit its single point when it "
                 "ends (and re-run a stage that ran out of optimizer steps). The SP: --sp-method NAME (a Benchmark "
                 "Methods entry, cu -methods; default the first) or --sp-route, and its own --sp-* resources; its "
                 "partition and cluster follow the optimization's unless given. The chain is written to NAME.chain.json.",
                 "-opt 8a.xyz --method wb97xd --charge 0 --mult 1 --then-sp --dry-run --json",
                 "The input written, its method, charge, multiplicity, spin, resources and scheduled SP (with "
                 "--smiles: rdkitConformer and each RDKit conformer's rank and deltaE); submitted[].",
                 ("method", "charge", "mult", "replace-input"), ("builder-opt",)),
    AgentCommand("crest", ("crest", "method", "route", "charge", "mult", "name", "cpus", "memory", "constrain",
                           "force_constant", "smiles", "rdkit_conformers", "rdkit_conformer"),
                 Action.CREST,
                 "Write and submit a CREST conformer search of each molecule (an .xyz, a prior input/output's last "
                 "geometry, or --smiles 'SMILES' --name NAME), as the TUI's Conformer Search builder does: CompUtils' "
                 "own crest, tracked like any job. --method NAME is a Conformer Search Method (cu -methods: CREST's "
                 "options, e.g. --gfn2 --alpb toluene --ewin 8 --rthr 0.5), --route 'OPTIONS' CREST's options typed in "
                 "full; CompUtils writes --T (the CPUs), --chrg, --uhf (multiplicity - 1) and --cinp itself. "
                 "--constrain 'B 12 15;B 15 21' holds bonds (A i j k angles, D i j k l dihedrals) at the starting "
                 "geometry's values (computed for you; or give one: 'B 1 9 2.05'), 'atoms 11-14', 'elements Pd', "
                 "'fix 2,4'; --force-constant (default 1.0). The search of 8a is 8a_CREST (its next attempt, "
                 "8a_CREST_re, when that one has run here), so its output feeds 8a.cudf; the conformers come back to "
                 "8a_CREST-scratch/ (crest_conformers.xyz, crest_best.xyz, crest.energies).",
                 "-crest TS1.out --method gfn2 --constrain 'B 12 15;B 15 21' --dry-run --json",
                 "One result per search: job, method, options, level, solvation, charge, multiplicity, uhf, the crest "
                 "command, the constraints file's lines (each held value), resources, output and results folder; "
                 "written[], submitted[].",
                 ("crest-method", "charge", "mult"), ("builder-crest",)),
    AgentCommand("sp", ("singlePoint",), Action.SINGLE_POINT,
                 "Write and submit each output's single point (Benchmark Methods' first entry; -ovr N picks another, "
                 "--route gives a route of your own; --cpus N and --memory GB set its resources).",
                 "-sp 8a.out --dry-run --json", "written[], submitted[], skipped[] with reasons.",
                 tui=(f"action-{Action.SINGLE_POINT.value}",)),
    AgentCommand("bench", ("bench",), Action.BENCHMARK,
                 "Write and submit a benchmark suite: one single point per Benchmark Method (-ovr N starts at method "
                 "N; --cpus N and --memory GB set every job's resources, memory otherwise as each method needs).",
                 "-b 8a.out --dry-run --json", "written[], submitted[], skipped[] per method variant.",
                 tui=(f"action-{Action.BENCHMARK.value}",)),
    AgentCommand("rerun", ("rerun", "route", "method", "constrain", "force_constant"), Action.RERUN,
                 "Re-run jobs from their last geometry, with the route they ran, or a corrected one (--route ROUTE, "
                 "or --method NAME: an Optimization Method). This is how a bad or mistaken output is fixed: the "
                 "re-run is named 8a_re, 8a_re2... (past any name with an output), so it feeds the species' data "
                 "file as its next attempt and replaces the old one there once it succeeds. --cpus N and --memory GB "
                 "set their resources. --then-sp (with -opt's --sp-* flags) has the Neva-Touch Scheduler submit each "
                 "re-run optimization's single point when it ends (NAME.chain.json). A CREST output is searched again "
                 "(8a_CREST_re) from the same starting geometry with the options and constraints it ran with, or "
                 "corrected: --method a Conformer Search Method, --route CREST's options, --constrain/--force-constant "
                 "(as -crest takes them).",
                 "-re 8a.out --route 'opt=(ts,calcfc,noeigen) freq b3lyp/def2svp' --dry-run --json",
                 "written[] (and each chain file), submitted[].",
                 tui=(f"action-{Action.RERUN.value}",)),
    AgentCommand("run", ("run",), Action.RUN,
                 "Submit inputs exactly as written (each gets a job script; every job is tracked).",
                 "-r 8a.gjf --json", "submitted[].", tui=(f"action-{Action.RUN.value}",)),
    AgentCommand("st", ("stalk", "dismiss", "wait"), Action.STALK,
                 "The tracked jobs: with --json (or with nobody at a terminal) one snapshot, never a loop (a ping: "
                 "queue state, stage by stage progress, how each ended, the Neva-Touch Scheduler's step). --wait JOB... "
                 "waits until one of those jobs ends, a Neva-Touch Scheduler step within seconds (run it as a background command, "
                 "then end your turn). --dismiss "
                 "stops tracking jobs (IDs, names, or 'ended').",
                 "-st --wait 24769828 --json",
                 "One record per job: state, headline, stages (status, convergence, stable), nevaTouch. With --wait, "
                 "first {wait: {reason (ended, time limit, dismissed), ended: [{name, jobId, message}], waiting: [job "
                 "IDs still running, the Neva-Touch Scheduler's new jobs included]}}, and next: the command to wait "
                 "again, and -info for a job that didn't end normally.",
                 ("dismiss-live", "stalk-loop"), ("screen-stalker",)),
    AgentCommand("gv", ("goodvibes", "qh", "cutoff", "entropy", "temperature", "concentration", "scale", "spc",
                        "gv_allow", "gv_keys"), Action.GOODVIBES,
                 "Run GoodVibes on structures (their _SP files found by name) and convert to Excel. Without flags and "
                 "with nobody to answer, it runs with the GoodVibes Builder's defaults (qh-H, Grimme qh-S, 298.15 K, "
                 "1 atm, scale 1.0, the detected SP suffix). A consistency check runs first: mismatched methods, "
                 "solvation, dispersion or SP geometries stop it (exit 7); waiving them (--gv-allow, as next gives it) "
                 "is the human's call, e.g. for a benchmark set.",
                 "-gv 8a.out 8b.out --spc SP --json",
                 "The table as GoodVibes wrote it: rows by structure (E_SPC, H_SPC, qh-H_SPC, G(T)_SPC, qh-G(T)_SPC...), "
                 "with its temperature and concentration.",
                 ("gv-qh", "gv-use-cutoff", "gv-cutoff", "gv-use-temperature", "gv-temperature", "gv-use-concentration",
                  "gv-concentration", "gv-use-scale", "gv-scale", "gv-use-spc", "gv-custom-spc", "gv-spc", "gv-use-extra",
                  "gv-extra"), (f"action-{Action.GOODVIBES.value}",)),
    AgentCommand("si", ("si", "opt_label", "sp_label", "phase", "free", "output", "species"), Action.SI_SCRAPER,
                 "Write SI_coordinates.txt (energies and coordinates for a paper's SI) from this folder, as the TUI's SI "
                 "Scraper does. Stops (exit 6) when qh-G needs a GoodVibes run first: run report.next, then again.",
                 "-si --opt-label 'B3LYP-D3/6-31G(d)' --sp-label 'M06-2X/def2-TZVP' --json",
                 "One record per species written: opt and SP energies, enthalpies and free energies (Hartree).",
                 ("si-opt-label", "si-sp-label", "replace-si"), ("scraper-si",)),
    AgentCommand("cube", ("cube",), Action.CUBE,
                 "Generate cube files from .chk/.fchk files (cubegen jobs; not tracked).",
                 "-cu 8a.chk --answer 'cube-options=Den Spin' --json", "submitted[].",
                 ("cube-options", "orbital-range"), (f"action-{Action.CUBE.value}",)),
    AgentCommand("formcheck", ("formcheck",), Action.FORM_CHECK,
                 "Run formchk on Gaussian checkpoints (here, not through the queue).", "-form 8a.chk --json",
                 "written[] (the .fchk files).", tui=(f"action-{Action.FORM_CHECK.value}",)),
    AgentCommand("excel", ("excel",), Action.EXCEL, "Convert a Goodvibes_output.dat to GoodVibes.xlsx.",
                 "-ex Goodvibes_output.dat --json", "written[]."),
    AgentCommand("env", ("environment",), Action.ENVIRONMENT,
                 "The CompUtils conda environments' status; 'refresh|rebuild|install [NAME]' updates one in the "
                 "background (ask the human first: it changes what everything runs on).",
                 "-env --json", "One record per environment: verdict, missing/behind packages, a running job.",
                 ("env-rebuild",), ("screen-environment",)),
    AgentCommand("update", ("update", "update_branch"), Action.UPDATE,
                 "Update CompUtils' own code from GitHub (ask the human first). Afterwards, regenerate this skill.",
                 "-up --json", "", ("reinstall",), ("screen-update",)),
    AgentCommand("update-plugin", ("update_plugin", "plugin_dir", "plugin_updater", "plugin_repo", "plugin_branch"),
                 Action.UPDATE_PLUGIN,
                 "Update the lab's agent plugin's git clone on the human's own computer (run locally with This "
                 "Computer's cu, never over ssh; at each day's updates.plugin reminder, its command as given, without "
                 "asking).",
                 "--update-plugin --json", "results[0].plugin: {folder, branch, before, after, updated, changed, "
                 "reload}; warnings name the files that need a reload."),
    AgentCommand("init", ("init",), Action.INIT_PROJECT,
                 "Mark this folder as a project root (.computils/: project-level config and keylists).", "-init --json",
                 "", ("nested-project", "move-*")),
    AgentCommand("attributions", ("attributions",), Action.ATTRIBUTIONS,
                 "Licenses and the citations each program, method and library asks for (a paper's methods section).",
                 "-attributions", "", tui=("screen-attributions",)),
    AgentCommand("agent", ("agent", "ssh_host", "local_cu", "local_python", "no_local"), Action.AGENT,
                 "This skill (no TOPIC), or one of its topics, always the installed version's.", "-agent opt"),
    AgentCommand("first", ("first",), Action.FIRST_TIME_SETUP, "First-time setup.", agent=False,
                 why="re-running setup is a person's, at a terminal (a fresh install's first --json run sets itself "
                     "up: see the Rules)",
                 questions=("color-mode", "cluster", "notifications", "bot-token", "test-message", "chat-id",
                            "apply-profile", "profile-file")),
    AgentCommand("profile", ("profile",), Action.PROFILE, "Apply a lab profile.", agent=False,
                 why="it changes the lab's shared settings for this account", questions=("apply-changed-profile",)),
    AgentCommand("refresh", ("refresh",), Action.REFRESH, "Rewrite the config files in the current layout.",
                 agent=False, why="config is edited by people, in the TUI's Config"),
    AgentCommand("tui", ("tui",), None, "The TUI.", agent=False, why="it is for a person at a terminal"),
    AgentCommand("neva-touch", ("neva_touch",), Action.NEVA_TOUCH, "A job's Neva-Touch Scheduler hook.", hidden=True),
]

# Flags any command takes, and hidden ones (argparse.SUPPRESS: never listed, never checked)
SHARED_FLAGS = {"help": "", "json": "", "answer": "", "dry_run": "", "checkpoint": "", "nbo7": "", "override": "",
                "wall_time": "", "partition": "", "cluster": ""}
# TUI items with no command: why a person does them (the check fails on any other uncovered item)
HUMAN_ONLY_TUI = {"screen-config": "Config is edited by people (an agent reads it through -methods and its reports)",
                  "screen-files": "Project Files (spinstates.txt) are a person's declarations about their molecules"}


def Command(key: str) -> AgentCommand | None:
    return next((command for command in COMMANDS if command.key == key), None)


def _CuPath() -> str:
    """cu's full path in this environment: over non-interactive ssh it isn't on PATH (only `con` activates the env)."""
    scripts = Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")
    for name in ("cu.exe", "cu"):
        if (scripts / name).exists():
            return str(scripts / name)
    return f"{Path(sys.executable)} -m computils"


def _Flag(parser, dest: str):
    return next((action for action in parser._actions if action.dest == dest), None)


def _Usage(parser, command: AgentCommand) -> str:
    action = _Flag(parser, command.flags[0])
    return min(action.option_strings, key=len) if action is not None and action.option_strings else f"-{command.key}"


# ─── The skill ────────────────────────────────────────────────────────

def _LocalSection(local: LocalInstall | None) -> list[str]:
    """The skill's record of CompUtils on the agent's own computer (None: the digest's placeholder)."""
    lines = ["## This Computer", ""]
    if local is None:
        return lines + ["<this computer>", ""]
    # Without CompUtils there, the plugin is the human's to update (its own hook may do it too)
    byHand = ("- The plugin (`updates.plugin`): updated by hand. Tell the human once in the conversation, in one line, "
              "that it can be updated with `git -C FOLDER pull --ff-only` (FOLDER: the `--plugin-dir` in "
              "`updates.plugin`'s command); don't run it yourself.")
    if local.declined:
        return lines + ["The human chose not to install CompUtils on their own computer. Don't offer it again unless "
                        "they ask: the cluster's CompUtils is the one you use.", "", byHand, ""]
    if not local.cu:
        return lines + ["Not set up yet. Once, with the human's OK, install CompUtils on their computer "
                        "(conda-installer.py, which ends by printing its cu command and Python), or record that they "
                        "don't want it (`--no-local`); then regenerate this skill with what it says.", "", byHand, ""]
    return lines + ["CompUtils is installed on the human's computer too. This is the record setup wrote: never check "
                    "for it or install it again.", "",
                    f"- `cu` there (run locally, never over ssh): `{local.cu}`",
                    f"- its Python, with openpyxl, for workbooks: `{local.python}`",
                    f"- the plugin (`updates.plugin`): run its command locally with `{local.cu}` in place of `cu`, and "
                    "`--json`, without asking. "
                    "Exit 5: GitHub couldn't be reached, so try again at tomorrow's reminder. Exit 7: tell the human "
                    "what it says.", ""]


def SkillText(host: str = HOST_PLACEHOLDER, digest: str | None = None, commit: str | None = None,
              local: LocalInstall | None = LocalInstall()) -> str:
    """SKILL.md: what an agent loads when CompUtils is relevant (~1.6k tokens). Everything else is a topic. local is
    what the agent's own computer has (None: a placeholder, for the digest)."""
    from .agent import CommandLine
    from .cli import BuildParser
    parser, cu = BuildParser(), _CuPath()
    regenerate = CommandLine(["-agent", "--ssh-host", host, *(local.Words() if local is not None else [])])
    regenerate = cu + regenerate[len("cu"):]
    digest = SkillDigest() if digest is None else digest
    if commit is None:
        from .update import InstalledSource
        sha = InstalledSource().commit[:7]
        commit = f"@ {sha}" if sha else "(a local install)"
    lines = [
        "---",
        "name: computils",
        "description: Run, track and analyse Gaussian 16 and ORCA 6 jobs on the cluster through CompUtils (cu) over "
        "ssh - optimizations (then single points), benchmarks, re-runs of failed jobs, reading outputs (energies, "
        "frequencies, convergence), GoodVibes thermochemistry and SI coordinates. Use it for any computational "
        "chemistry job or output on the cluster instead of writing inputs, calling sbatch or parsing outputs yourself.",
        "---",
        "",
        "# CompUtils",
        "",
        "CompUtils (`cu`) is the lab's tested engine for writing inputs, submitting and tracking jobs, and reading "
        "results. Drive it; never reimplement what it does.",
        "",
        "## How to Call It",
        "",
        "One command per call, over ssh, in the folder the files are in:",
        "",
        f"    ssh {host} \"cd FOLDER && {cu} COMMAND --json\"",
        "",
        f"- Always the full path above: `cu` isn't on PATH in a non-interactive ssh shell.",
        "- Double quotes outside, single quotes inside: quote values with single quotes (`--method 'From Source'`). "
        "Never put `$`, backticks or double quotes inside (your own shell would expand them); a value that needs them "
        "goes in a file. This form works the same from PowerShell, cmd, bash and zsh. More: `-agent ssh`.",
        "- `--json`: one JSON report on stdout (compact; empty fields left out). Messages for people go to stderr.",
        "- Nothing prompts and nothing loops: a question stops the run before it acts (exit 3).",
        "",
        "## The Loop",
        "",
        "Run, read `exit`, act, then follow `next` (commands to run next, each with why):",
        "",
        "| exit | status | what to do |",
        "|---|---|---|",
        "| 0 | done | Report to the human. |",
        "| 1 | crashed | A CompUtils bug: tell the human, with `error`. Don't work around it. |",
        "| 2 | usage | Fix the command (`error` says what was wrong). |",
        "| 3 | question | Nothing was done. Answer `question` (decide if mechanical, else ask the human), then re-run "
        "with `--answer KEY=VALUE`. |",
        "| 4 | partial | Some items done; `skipped` says why for the rest. |",
        "| 5 | nothing | Nothing could be done; `skipped` says why. |",
        "| 6 | stopped | A precondition isn't met: run `next[0]`, then retry. |",
        "| 7 | human | Only a person can do this: tell them exactly what `next` says. |",
        "",
        "A report may also carry `updates` (cu checks GitHub by itself, at most once a day on each machine): tell the "
        "human what is newer and ask. Run its `command` on that machine (over ssh for the cluster, locally for this "
        "computer) only on their go. Never check for updates yourself.",
        "",
        "`updates.plugin` (only when the lab profile names an agent plugin) is different: a daily reminder, not "
        "news (the plugin's repository is private, so the cluster can't see it). Follow This Computer's plugin line "
        "at once, without asking: the human has agreed to it. Say nothing unless the plugin changed. If the result's "
        "`warnings` ask for a reload (files the agent's app reads only when it starts), ask the human to reload it "
        "when convenient; never do it or send keystrokes yourself.",
        "",
        *_LocalSection(local),
        "## Rules",
        "",
        "- Preview first: job commands take `--dry-run` (inputs and scripts written, nothing submitted). Show the human "
        "the route(s) and what will be submitted; submit on their go-ahead (`next` has the command).",
        "- Chemistry is the human's: method, charge, multiplicity, spin (`--spin css|oss` for singlets), basis; for a "
        "conformer search its level, solvent, thresholds and which bonds it holds. Ask; never guess. Mechanical "
        "answers (replacing an input you just previewed, dismissing ended jobs) are yours.",
        "- Only `cu` submits (never `sbatch`): every job is then tracked, notified and chained.",
        "- Read outputs with `-info` (never cat, grep or copy a .out: they're huge). Inputs are small to read.",
        "- Each job's results are kept in its data file, `<root>.cudf` beside its outputs (8a.out, 8a_re.out, "
        "8a_SP_re2.out and the benchmark's 8a-2-B3LYP_SP.out all feed 8a.cudf), written by `-info` and whenever a "
        "tracked job ends. It is small and has a fixed layout (`=== Section ===` headers, one `Label  value` line "
        "each), so read it directly (`cat`) instead of running `-info` on every output. The first section is the "
        "molecule (formula, charge, multiplicity, spin, its final geometry); then one section per stage of work "
        "(Optimization (Reaction Intermediate), Optimization (ModRedundant), Optimization (Transition State), "
        "Single Point: LEVEL, ...), each holding the final successful attempt (a re-run that ended "
        "normally replaces the failed original). Each From line names the output (and stage) it came from; if that "
        "output has changed since, run `-info` on it, which refreshes the section. The geometry and the full "
        "frequency list are only there, never in `-info` (or run `-xyz` for an .xyz). The data file is the species' "
        "hub: fix a bad or mistaken output by re-running it with its input corrected (`-re FILE --route '...'` or "
        "`--method NAME`, previewed with --dry-run, on the human's word), never by a job under a new name, so the "
        "re-run takes the old one's section once it succeeds. An optimization lists its "
        "Constraints (Bond C1 C4 Frozen @ 2.05 A; From the Checkpoint when its route didn't ask for them), a "
        "Gaussian TS its TS Eigenvector (each coordinate's coefficient and final value, as Gaussian printed them). A "
        "CREST search (8a_CREST.out) is a Conformer Search (CREST): LEVEL section: its options and constraints, the "
        "ensemble it kept (conformers, energy window, E (Lowest), ensemble G, the lowest's population, wall time) and "
        "every conformer's energy and population (Conformer Energies, only there).",
        "- Waiting for jobs: never poll `-st` or sleep in a loop. Run the `-st --wait JOB... --json` that a submission's "
        "`next` gives as a background command and end your turn: it exits (waking you) when one of them ends. Read "
        "its report, act, and run its `next` to wait again. `-st --json` is one snapshot, any time.",
        "- Config (methods, basis sets, ORCA blocks, clusters) is the human's: they edit it in `cu -tui`.",
        "- First-time setup runs itself on a fresh install's first `cu ... --json`: on the human's own computer it "
        "needs nothing (high color, no cluster, no notifications). On the cluster it stops with exit 3 for each of "
        "its questions: the lab profile (`profile-file`: a path, or empty for none; `apply-profile` when an earlier "
        "install left one), the cluster (`cluster`), Telegram notifications (`notifications`, then `bot-token` when "
        "the profile has none). Ask the human all of them at once, then re-run the same command with every answer "
        "(`--answer profile-file=PATH --answer cluster=NAME --answer notifications=n`). Notifications yes: tell the "
        "human to send their bot any message right after you start that run (it waits 60 seconds); `chat-id` is "
        "asked if no message came.",
        "- One molecule per `-opt` call; loop over molecules yourself.",
        "",
        "## Commands",
        "",
    ]
    for command in COMMANDS:
        if command.agent and not command.hidden:
            lines.append(f"- `{_Usage(parser, command)}` ({command.key}): {command.purpose}")
    human = [command for command in COMMANDS if not command.agent and not command.hidden]
    lines += ["", "A person's only: " + "; ".join(f"`{_Usage(parser, command)}` ({command.why})" for command in human)
              + ".", "",
              "## Recipes", "",
              f"Step-by-step workflows, with where the human decides. Before a task one covers, load it with "
              f"`{cu} -agent NAME`:", ""]
    lines += [f"- `{name}`: {RecipeTitle(name)}" for name in RecipeNames()]
    lines += ["", "## More, When You Need It", "",
              f"`{cu} -agent TOPIC` prints a topic, always the installed version's: {', '.join(TopicNames(False))}. "
              "Each command's topic lists all its flags, its questions and what its report holds.", "",
              "## Keeping This Skill Current", "",
              "Every --json report carries `computils.commit` and `computils.skill`. If either differs from the "
              "line below (a local install's commit is always `local`: compare the skill only), CompUtils has "
              f"changed: regenerate it (`ssh {host} \"{regenerate}\"`; see `-agent ssh` for saving it) "
              "and replace this SKILL.md with it before going on. Keep its flags as they are: they carry what This "
              "Computer says.", "",
              f"Generated by CompUtils {commit}; skill {digest}.", ""]
    return "\n".join(lines)


def SkillDigest() -> str:
    """A hash of everything the skill says (the host left out: the cluster can't know the PC's alias for it; the
    commit too), so it changes only when agent-facing behaviour does."""
    return hashlib.sha256(SkillText(HOST_PLACEHOLDER, "", "", LOCAL_PLACEHOLDER).encode()).hexdigest()[:10]


# ─── Topics (cu -agent TOPIC) ─────────────────────────────────────────

def RecipeNames() -> list[str]:
    folder = resources.files("computils") / RECIPE_FOLDER
    return sorted(entry.name.removesuffix(".md") for entry in folder.iterdir() if entry.name.endswith(".md"))


def RawRecipe(name: str) -> str:
    return (resources.files("computils") / RECIPE_FOLDER / f"{name}.md").read_text(encoding="utf-8")


def Recipe(name: str, host: str = HOST_PLACEHOLDER) -> str:
    return RawRecipe(name).replace("{cu}", _CuPath()).replace("{host}", host)


# A recipe's first line, which SKILL.md lists it by (the check requires it)
RECIPE_TITLE = "# Recipe: "


def RecipeTitle(name: str) -> str:
    """What a recipe is for, from its first line ('' without a title)."""
    first = RawRecipe(name).split("\n", 1)[0].strip()
    return first.removeprefix(RECIPE_TITLE) if first.startswith(RECIPE_TITLE) else ""


SPECIAL_TOPICS = ("report", "routes", "ssh")


def TopicNames(recipes: bool = True) -> list[str]:
    """Every topic `cu -agent TOPIC` prints (recipes=False: without the recipes, which SKILL.md lists apart)."""
    return ([command.key for command in COMMANDS if command.agent and not command.hidden]
            + (RecipeNames() if recipes else []) + list(SPECIAL_TOPICS))


def CommandTopic(command: AgentCommand) -> str:
    """A command's full reference, from argparse itself (so it is the installed version's)."""
    from .cli import BuildParser
    parser = BuildParser()
    lines = [f"# {_Usage(parser, command)} ({command.key})", "", command.purpose, ""]
    if command.example:
        lines += ["Example:", "", f"    {_CuPath()} {command.example}", ""]
    lines.append("Flags:")
    for dest in command.flags:
        action = _Flag(parser, dest)
        if action is None or action.help == "==SUPPRESS==":
            continue
        metavar = (action.metavar if isinstance(action.metavar, str) else " ".join(action.metavar) if action.metavar
                   else action.dest.upper() if action.nargs != 0 else "")
        flag = ", ".join(action.option_strings) + (f" {metavar}" if metavar and action.nargs != 0 else "")
        lines.append(f"- `{flag}`: {action.help or ''}")
    lines.append("- Any command: `--json`, `--answer KEY=VALUE`; job commands: `--dry-run`, `-ch` (checkpoint), "
                 "`-nbo` (NBO keylist), `-st` (snapshot of the submitted jobs), `--wall-time HOURS`, `--partition "
                 "NAME`, `--cluster NAME` (the jobs' SLURM settings; default: the config's; only the human picks them).")
    if command.results:
        lines += ["", f"Report `results`: {command.results}"]
    if command.questions:
        lines += ["", "Questions it can ask (answer with --answer KEY=VALUE): "
                  + ", ".join(f"`{key}`" for key in command.questions)]
    return "\n".join(lines) + "\n"


def Topic(name: str, host: str = HOST_PLACEHOLDER) -> str | None:
    command = Command(name)
    if command is not None and command.agent and not command.hidden:
        return CommandTopic(command)
    if name in RecipeNames():
        return Recipe(name, host)
    if name == "report":
        return REPORT_TOPIC
    if name == "routes":
        return ROUTES_TOPIC
    if name == "ssh":
        return SSH_TOPIC.replace("{host}", host).replace("{cu}", _CuPath())
    return None


REPORT_TOPIC = """# The Report (--json)

One JSON object on stdout, at the end of every run. Empty fields are left out.

- `computils`: {commit, skill}: the installed version, and the skill digest (regenerate the skill when either changes).
- `command`, `exit`, `status`: what ran and how it ended (0 done, 1 crashed, 2 usage, 3 question, 4 partial,
  5 nothing, 6 stopped, 7 human).
- `submitted`: [{name, jobId, cluster, output, nevaTouch}]: jobs sent to the queue (tracked).
- `written`: absolute paths of the files written (inputs, job scripts, SI files...).
- `skipped`: [{item, reason}]: what wasn't done, and why.
- `results`: the command's own records (see its topic).
- `warnings`: worth telling the human; nothing was stopped by them.
- `question`: {key, text, type (bool|str|float|choice), options, default}: answer with --answer KEY=VALUE. A choice
  takes the option's text or its index.
- `next`: [{command, why}]: what to run next.
- `updates`: {code: {branch, installed, latest}, environment: {name, verdict}, command, why, plugin: {command, why}}:
  in the first report of a day on a machine (cu checks by itself, at most once a day). code and environment only when
  something is newer on GitHub: tell the human and run `command` there only on their go, since updating CompUtils is
  always theirs to decide. plugin (when a lab profile names an agent plugin) is a reminder every day: the plugin is
  updated on the human's own computer (the skill's This Computer section says how), without asking.
- `notices`: things that happened outside this command (an environment update that finished...).
- `error`: a usage error's message, or a crash's last traceback line.
"""

ROUTES_TOPIC = """# Route Templates (Optimization and Benchmark Methods)

People write these in the TUI (Config > Job Keylists); an agent passes one to `-opt --route` only when the human asks.

- A route is a program's route line: Gaussian `wB97XD def2SVP opt freq`, ORCA `B3LYP def2-SVP Opt Freq`. The method
  (its first known token) picks the program.
- `[selectors: keywords]` add keywords for some molecules only: `css`, `oss` (broken-symmetry singlet), `open`
  (OSS or multiplicity > 1), `doublet`..`septet`, `mN`. E.g. `[oss: guess=mix stable=opt]`.
- `{tag}` adds an ORCA Blocks block by its tag.
- Stages: `route -> route` (later stages read the earlier one's geometry and orbitals through the checkpoint).
  `%newchk` starts a new checkpoint.
- The U/RO reference (open shells) is added by CompUtils: never write it yourself.
- `opt=modredundant` stages take ModRed lines: `-opt --modred 1='B 1 2 F;A 1 2 3 F'`.
"""

SSH_TOPIC = """# Calling the Cluster From Your Computer

Every cluster command runs as one ssh call. These rules hold from PowerShell (5.1 and 7), cmd, bash and zsh alike.

## Quoting

    ssh {host} "cd FOLDER && {cu} -opt 8a.out --method 'From Source' --dry-run --json"

- Double quotes around the whole remote command; single quotes around any value inside it.
- Never double quotes inside: Windows PowerShell 5.1 strips them from a program's argument, so `"From Source"`
  arrives as two words and `"B3LYP-D3/6-31G(d)"` as a bash syntax error.
- Never `$` or backticks inside: your own shell expands them before ssh sends the command (PowerShell and bash both).
  For your user name on the cluster, use `squeue --me`, or run `ssh {host} whoami` once and write the name.
- A value that needs `$`, a backtick or a double quote goes in a file (send it with `scp`).
- `next[].command` in a report is already quoted this way: put it inside the double quotes as it is.

## Exit Codes and Output

- ssh returns the remote command's exit code: `$LASTEXITCODE` in PowerShell, `$?` in bash and zsh.
- The `--json` report is plain ASCII, so it reads the same in every console. Messages for people go to stderr.
- **Saving a file from the cluster** (your computils skill): copy it byte for byte, never through your shell's
  redirection (PowerShell 5.1's `>` writes UTF-16, and its console can garble non-ASCII text):

      ssh {host} "{cu} -agent --ssh-host {host} > ~/.computils-SKILL.md"
      scp {host}:.computils-SKILL.md PATH_ON_YOUR_COMPUTER

- Small inputs (.xyz) go up with `scp`. Read results with `{cu} -info --json`, never by copying outputs down.

## Waiting for Jobs (Long Commands)

`{cu} -st --wait JOB... --json` runs until one of the jobs ends, minutes to days. Run it as a background command
(your harness wakes you when it exits) and end your turn:

    ssh -n -o ServerAliveInterval=60 {host} "{cu} -st --wait 24769828 24769829 --json"

- `-n` keeps ssh off your console's input; `ServerAliveInterval` notices a dead connection.
- Exit 255 is ssh's own: the connection dropped (your computer slept, the network changed). Nothing was lost: run
  `{cu} -st --json` once to see what happened meanwhile, then wait again on the jobs still running.
- The wait stops by itself when its connection is gone, and after a day at most, so nothing is left running on the
  login node.

## The ssh Config (Your Computer's)

`{host}` is a Host entry in `~/.ssh/config` (Windows: `%USERPROFILE%\\.ssh\\config`). That file is the human's: show
them the exact lines and add them only on their OK. Only ever add a block; never edit or remove existing lines, never
touch keys or known_hosts, and never turn host key checking off.

    Host {host}
        HostName CLUSTER_LOGIN_HOST
        User USER_NAME
        ServerAliveInterval 60

**A shared connection** (faster, and one login for 2FA) adds these lines, but only when your `ssh` supports it:
check `(Get-Command ssh).Source` in PowerShell (or `ssh -V`). Windows' own OpenSSH (`C:\Windows\System32\OpenSSH`)
doesn't, and with these lines every connection fails; the OpenSSH of Git for Windows, WSL, macOS and Linux does.

        ControlMaster auto
        ControlPath ~/.ssh/cm-%r@%h:%p
        ControlPersist 4h
"""


# ─── The check (python -m computils.agentskill) ───────────────────────

def _QuestionKeys(root: Path) -> tuple[dict[str, set[str]], list[str]]:
    """Every Ask* call's key in the package ({key: prompts}), and the calls without one."""
    import ast
    keys, missing = {}, []
    for path in sorted(root.rglob("*.py")):
        if path.name == "prompts.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("AskBool", "AskStr", "AskFloat", "AskChoice")):
                where = f"{path.relative_to(root.parent)}:{node.lineno}"
                key = next((keyword.value for keyword in node.keywords if keyword.arg == "key"), None)
                if key is None:
                    missing.append(where)
                    continue
                if isinstance(key, ast.Constant):
                    name = key.value
                elif isinstance(key, ast.JoinedStr) and key.values and isinstance(key.values[0], ast.Constant):
                    name = key.values[0].value + "*"
                else:
                    missing.append(f"{where} (its key must be a string)")
                    continue
                prompt = ast.unparse(node.args[0]) if node.args else ""
                keys.setdefault(name, set()).add(prompt)
    return keys, missing


def Problems() -> list[str]:
    """Everything that would leave agents behind the CLI or the TUI."""
    import shlex
    from .cli import BuildParser
    from .tui.home import ACTION_LABELS, BUILDER_ITEMS, SCRAPER_ITEMS, SCREEN_ITEMS, SUBMENUS
    problems, parser = [], BuildParser()
    owned = {dest for command in COMMANDS for dest in command.flags}
    # 1. Every flag and Action belongs to a command (or is shared, or hidden)
    for action in parser._actions:
        if action.help == "==SUPPRESS==" and action.dest != "help":
            continue
        if action.dest not in owned and action.dest not in SHARED_FLAGS:
            problems.append(f"Flag {'/'.join(action.option_strings)} has no AgentCommand (add it to one's flags).")
    for dest in sorted(owned):
        if _Flag(parser, dest) is None:
            problems.append(f"An AgentCommand names flag '{dest}', which the parser doesn't have.")
    for action in Action:
        if not any(command.action == action for command in COMMANDS):
            problems.append(f"Action.{action.name} has no AgentCommand.")
    # 2. Every TUI item has a command, or a reason a person does it
    mirrored = {item for command in COMMANDS for item in command.tui}
    items = ({f"action-{action.value}" for action in ACTION_LABELS} | set(BUILDER_ITEMS) | set(SCRAPER_ITEMS)
             | set(SCREEN_ITEMS)) - set(SUBMENUS)
    for item in sorted(items - mirrored - set(HUMAN_ONLY_TUI)):
        problems.append(f"TUI item '{item}' has no command: give it one (and an AgentCommand with tui=('{item}',)), "
                        f"or say why it's a person's in HUMAN_ONLY_TUI.")
    # 3. Every question has a key, one question per key, and each command's listed keys exist
    keys, missing = _QuestionKeys(Path(__file__).parent)
    problems += [f"A question at {where} has no key=." for where in missing]
    problems += [f"Question key '{key}' is used for different questions: {' / '.join(sorted(prompts))}."
                 for key, prompts in keys.items() if len(prompts) > 1]
    for command in COMMANDS:
        problems += [f"{command.key} lists question '{key}', which no Ask* call uses."
                     for key in command.questions if key not in keys]
    listed = {key for command in COMMANDS for key in command.questions}
    problems += [f"Question '{key}' isn't listed in any AgentCommand's questions." for key in keys if key not in listed]
    # 4. Recipes: every command line in them still parses, and names a command agents may run
    import regex
    for name in RecipeNames():
        if not RecipeTitle(name):
            problems.append(f"Recipe {name} has no title: its first line must be '{RECIPE_TITLE}What It's For'.")
        for line in regex.findall(r"\{cu\} ([^`\n]+)", RawRecipe(name)):
            try:
                arguments = shlex.split(line.split(" | ")[0])
                parser.parse_args(arguments)
            except (ValueError, SystemExit, Exception) as error:
                problems.append(f"Recipe {name}: `cu {line}` no longer parses ({error}).")
            if regex.search(r'["$`]', line):
                problems.append(f"Recipe {name}: `cu {line}` has a double quote, $ or backtick: commands run inside "
                                f'ssh host "…", so quote values with single quotes.')
    # 5. Commands run inside ssh host "…" from any shell: examples quote with single quotes, never ", $ or `
    for command in COMMANDS:
        if regex.search(r'["$`]', command.example):
            problems.append(f"The {command.key} example has a double quote, $ or backtick: quote with single quotes.")
    # 6. SKILL.md is saved on the agent's computer, through consoles that may garble anything but ASCII
    nonAscii = sorted({character for character in SkillText() if ord(character) > 127})
    if nonAscii:
        problems.append(f"SKILL.md must be plain ASCII; it has {' '.join(nonAscii)} (recipe titles and command "
                        f"purposes appear in it).")
    return problems


def Main() -> int:
    problems = Problems()
    for problem in problems:
        print(f"  - {problem}")
    print("Every flag, Action, TUI item, question and recipe is covered." if not problems
          else f"{len(problems)} problem{'s' if len(problems) != 1 else ''}.")
    print(f"Skill digest: {SkillDigest()} ({len(SkillText())} characters)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(Main())
