# CompUtils

**Your one-stop shop for computational chemistry on HPC clusters.**

CompUtils (`cu`) writes, submits, follows, and analyzes Gaussian 16 and ORCA 6 jobs on Slurm clusters. It can be
accessed from the command line or from a full-screen terminal interface.

## AI Usage Disclosure
- CompUtils began in 2024 as an entirely human-made project to condense common scripts into a single resource.
- Starting in 2026, Anthropic's Claude Desktop and Claude Code have become integral to the process to facilitate a
  faster development cycle. Most of the modern code and documentation in the project is written by AI, with some
  entirely human components remaining.
- The development of CompUtils is human-directed and human-approved. Testing is done in two phases, initially simulated
  tests by AI, then human-tested on real jobs in real workflows and environments.

## What It Does

- **Generates and submits jobs.** Single points, benchmarking methods, re-runs of failed jobs, and cube
  files, all from finished outputs: CompUtils reads the final geometry, charge and multiplicity, writes the new input
  and its Slurm script, and submits it. Jobs written by hand are submitted as they are (`-r`).
- **Treats Gaussian and ORCA equally.** Outputs are recognized by their contents, and a method is sent to the program
  you map it to in your config.
- **Builds route cards from templates.** Each benchmark or optimization method is a complete route card, with optional
  spin groups (`[oss: guess=mix stable=opt]`) and metal groups (`[metal: freq=noraman]`) applied only to matching
  molecules, automatic U/RO references for open-shell species, the project's solvent where a method says `{smd}`
  (written as each program needs it: Gaussian's `SCRF=(SMD,…)`, ORCA's SMD block, CREST's `--alpb`), named Gen/GenECP
  basis sets (`{example}`) and, for ORCA, `{tags}` that pull `%` blocks into the input. A method can run in stages (Gaussian
  `--Link1--`, e.g. ModRed → Stability → OptTS), each later stage reading the geometry and orbitals from the checkpoint.
- **Classifies spin states.** Closed-shell singlets, broken-symmetry (open-shell) singlets and higher multiplicities are
  told apart from the previous route, `<S**2>` and stability analyses, or from your own overrides.
- **Runs CREST conformer searches.** From a finished job's geometry, an .xyz or a SMILES: the lab's options as named
  methods, constraints written the way ModRed lines are (`B 12 15` holds a bond at its starting length), and the
  conformers, ensemble and energies read back from CREST's output.
- **Follows your jobs.** Every submitted job is tracked. The Job Stalker reports each stage as it runs (optimization
  convergence, wave-function stability, normal or error termination), and optional Telegram notifications text you
  when jobs end.
- **Runs GoodVibes.** Thermochemistry with quasi-harmonic corrections over your outputs, converted automatically into an
  Excel table, for your convenience.
- **Keeps per-project settings.** Any folder can be a project root with its own resources, methods, mixed basis sets
  (`Gen`/`GenECP`), ORCA blocks and spin-state overrides.
- **Shares lab settings privately.** Configure the provided example-profile.toml to add private computing resources or
  other information (such as implementing your own notifier bot).

## Requirements

- A Linux cluster with Slurm (`sbatch`, `squeue`). Settings pre-configured for public clusters Bridges-2 (PSC) and
  Stampede3 (TACC) are built in; other clusters are added through a lab profile.
- Gaussian 16 and/or ORCA 6.1, installed and licensed on that cluster (see [Third-Party Software](#third-party-software)).
- Python 3.11 or later. The installer builds a conda environment with everything CompUtils needs (it starts on any
  Python 3.6+). CompUtils also installs on a Windows, Mac or Linux computer, e.g. for the files you copy down.

## Installation

Download the `conda-installer.py` file from the repository and run it via `python3 conda-installer.py` (on Windows,
`python conda-installer.py`). The same file works on Linux (a cluster's login node or a workstation), Windows and
macOS (Apple Silicon or Intel). The installer will take care of installing miniconda3 if no conda is present on the
system (set to use conda-forge only), then automatically create the CompUtils conda environment for your system
(`compUtils.yml`; `compUtilsWindows.yml` on Windows, `compUtilsMac.yml` on a Mac) and install CompUtils inside of
it. It asks nothing, and never accepts any terms on your behalf. If your
lab already has its own `profile.toml` file, CompUtils asks for it during first-time setup (`cu -profile FILE` applies
one later). An AI agent driving CompUtils can do the first-time setup for you: on your own computer it needs nothing
(high color, no cluster, no notifications), and on the cluster it asks you, through the agent, for your lab profile,
the cluster and whether you want notifications.

- **Linux:** activate the environment (`con`, an alias the installer adds, or `conda activate compUtils`), then `cu`.
- **Windows:** the installer writes a `cu` command (`cu.cmd` in `%LOCALAPPDATA%\Programs\CompUtils`, added to your user
  PATH), so `cu` works in any new PowerShell or cmd window with nothing to activate.
- **A Windows computer with no Python at all:** install Miniconda first (its silent installer, to
  `%USERPROFILE%\miniconda3`), then run the installer with Miniconda's Python and `--conda-forge-only`:
  `& "$HOME\miniconda3\python.exe" conda-installer.py --conda-forge-only` (PowerShell).
- **Mac:** the installer writes a `cu` command (`~/.local/bin/cu`, put first on your PATH in `~/.zprofile`, ahead of
  macOS' own, unrelated `/usr/bin/cu`), so `cu` works in any new terminal with nothing to activate. GoodVibes comes
  from pip there (conda-forge has no Apple Silicon build of 3.2).
- **A Mac without Python** (no Command Line Tools, so `python3` only offers to install them): install Miniconda first
  (`bash Miniconda3-latest-MacOSX-$(uname -m).sh -b -p ~/miniconda3`), then run
  `~/miniconda3/bin/python conda-installer.py --conda-forge-only`.

The environments also carry `openpyxl`, so an AI agent can build Excel workbooks with formulas from `GoodVibes.xlsx`.

## Usage

`cu` with no arguments opens the terminal interface (set `bareCommandOpensTUI = false` in `qol.toml` to turn that off);
`cu -tui` always does. Everything below is also available as a flag. File arguments accept globs, quoted or not.

| Command | Purpose |
|---|---|
| `cu -r FILE…` | Submits input files directly |
| `cu -sp FILE…` | Generates and submits single point files based on completed jobs (`--route ROUTE` for a route of your own) |
| `cu -b FILE…` | Generates and submits multiple single point files per file, according to benchmarking list |
| `cu -re FILE…` | Re-runs a job from its last geometry, with the route it ran or a corrected one (`--route`, or `--method` for an Optimization Method), named as a re-run (8a_re, 8a_re2) so it feeds the species' data file (`--then-sp` with the `--sp-*` flags: each re-run optimization's single point once it ends) |
| `cu -cu FILE…` | Generates cube files from Gaussian .chk or .fchk via cubegen |
| `cu -form FILE…` | Runs formchk on Gaussian checkpoint files |
| `cu -gv [FILE…]` | Runs GoodVibes (default: every output in cwd), then writes output to Excel. A consistency check runs first and stops on mismatched methods, solvation, dispersion or SP geometries; `--gv-allow` waives them where intended (e.g. benchmarks) |
| `cu -ex FILE` | Converts a GoodVibes output to Excel |
| `cu -st` | Follows tracked jobs via squeue, reporting on statuses such as convergence criteria. Can be included in a submission command to tag submitted jobs, or called independently |
| `cu -init` | Makes the current folder a project root. Useful for project-based overrides to global defaults |
| `cu -profile [FILE]` | Applies a lab profile |
| `cu -first` | Re-runs first-time setup |
| `cu -refresh [project]` | Rewrites the config files in the current layout, keeping your values |
| `cu --update` | Updates CompUtils from GitHub |
| `cu --update-plugin` | Updates your lab's agent plugin (a git clone its lab profile names) on your own computer, with your GitHub sign-in |
| `cu -attributions` | Shows the disclaimer and credits |
| `cu -opt FILE` | Writes and submits an optimization of one molecule (an .xyz or a prior job, or `--smiles 'SMILES' --name NAME`: RDKit builds its 3D geometry), as the Optimization builder does; `--then-sp` with `--sp-method NAME` (or `--sp-route`) and `--sp-*` resources has the Neva-Touch Scheduler submit that single point when it ends |
| `cu -crest FILE` | Writes and submits a CREST conformer search of each molecule (an .xyz, a prior job, or `--smiles 'SMILES' --name NAME`), as the Conformer Search builder does: `--method NAME` (a Conformer Search Method) or `--route 'OPTIONS'`, `--constrain 'B 12 15;B 15 21'` (bonds held at their starting lengths; also `A`, `D`, `atoms`, `elements`, `fix`), `--force-constant`. The search of 8a is 8a_CREST, whose output feeds `8a.cudf`; its conformers come back to `8a_CREST-scratch/`. `cu -re 8a_CREST.out` searches again, corrected or as it ran |
| `cu -si` | Writes SI_coordinates.txt from the folder's outputs, as the SI Scraper does |
| `cu -info FILE…` | Shows what outputs and inputs say: status, energies, frequencies, convergence, an optimization's constraints and a Gaussian TS's eigenvector, stage by stage for a staged job. Each job's results are kept in its data file, `<root>.cudf` (8a.out, 8a_re.out and 8a_SP.out all feed `8a.cudf`): one section per stage of work, each the final successful attempt, also written whenever a tracked job ends |
| `cu -xyz FILE…` | Writes each file's last geometry (an output's, or an input's own) as `NAME.xyz`; `--smiles 'SMILES' --name NAME` writes a SMILES' RDKit conformer |
| `cu -methods` | Lists the Optimization and Single Point Methods in force in this folder |
| `cu -scrape FILE… --find 'REGEX'` | Finds lines in an output by a regular expression, for a value `-info` doesn't report: one read of the file, only the matching lines (`--latest N`, `--earliest N` or `--all`; `--after`/`--before` lines around each, or a table `--until` its last line), its groups as printed |
| `cu -cancel JOB…` | Cancels tracked jobs (IDs or names) after asking: recorded at once, notified like any ending, and the Neva-Touch Scheduler takes no step after a cancelled job. The Job Stalker's `c` does the same |
| `cu -agent [TOPIC]` | Prints the skill that teaches an AI agent to use CompUtils (see below) |

| Modifiers | Purpose |
|---|---|
| `-ch` | Causes generated Gaussian inputs to create .chk files |
| `-nbo` | Appends an NBO7-styled section to generated input (tested only for Gaussian at time of writing) |
| `-ovr N` | Starts single points or benchmarking from entry N in the benchmarking list |
| `--wall-time HOURS`, `--partition NAME`, `--cluster NAME` | The jobs' own SLURM settings, for any job command (default: the config's; `--cluster` only where the cluster's header sends `-M`) |
| `--cpus N`, `--memory GB` | The generated inputs' CPUs and memory per core, for `-sp`, `-b`, `-re`, `-opt` and `-crest` (default: the config's, memory as each method needs) |
| `--dry-run` | Writes the inputs and job scripts without submitting them |
| `--json` | Prints one machine-readable report (for scripts and AI agents) |
| `--help` | Lists flags and their usage |

The terminal interface needs a window of at least 84 × 24 characters.

### Using CompUtils with an AI Agent

An AI agent (Google's Antigravity, Claude Code, …) can drive CompUtils over ssh instead of writing inputs or parsing
outputs itself. Every command can run without a person at the keyboard: `--json` gives a report with an exit code,
what was submitted, skipped and why, and what to run next. A question stops the run before anything is done, and the
agent answers it with `--answer KEY=VALUE` (chemistry decisions are left to you).

To teach your agent, run this on your computer (once; it keeps itself up to date afterwards), replacing `cluster` with
your ssh name for the cluster and the path with your environment's:

```
ssh cluster "~/miniconda3/envs/compUtils/bin/cu -agent --ssh-host cluster > ~/.computils-SKILL.md"
scp cluster:.computils-SKILL.md SKILL.md
```

(`scp` copies it byte for byte: Windows PowerShell's `>` would save it as UTF-16), and put `SKILL.md` in a `computils` folder in your agent's skills folder (Antigravity: `~/.gemini/config/skills/computils/`
for every workspace; Claude Code: `~/.claude/skills/computils/`).

The skill also records whether CompUtils is installed on your own computer (`--local-cu PATH --local-python PATH`,
or `--no-local`), so the agent never asks again. Once a day, the first `--json` report on a machine also says
whether a newer CompUtils (or environment) is on GitHub (`updates`); the agent tells you, and you decide. When
your lab profile names an agent plugin (`agentPluginFolder`, `agentPluginUpdater`, `agentPluginRepo`,
`agentPluginBranch`), the same report reminds the agent to update it on your own computer (`updates.plugin`: a
private repository the cluster can't see), which it does by itself with `cu --update-plugin`, asking you to reload
the agent's app when a file it reads only at startup changed. A lab that keeps its profile in that plugin
(`agentPluginProfile`, its path there) gets it relayed the same way: when the plugin's copy is newer than the
cluster's, `cu --update-plugin` on your computer copies it to your cluster account and asks you before applying it
there (over ssh, by the alias your skill was generated with).

## Configuration

The first run walks you through setup: your cluster, colors and (optionally) Telegram text notifications, and prompts
for a lab profile (can be declined). The settings live in commented TOML files in `~/bin`:

| File | Contains |
|---|---|
| `slurm.toml` | Cluster, partition, CPUs, memory, wall time, stalking intervals |
| `programs.toml` | Method → program map, open-shell reference, job script boilerplate |
| `extensions.toml` | File extensions and name suffixes |
| `notifications.toml` | Telegram bot settings |
| `paths.toml` | Config directory and project marker |
| `qol.toml` | Color mode and interface preferences |

Edit them in the interface's Config screen, or by hand.

**Job keylists.** The methods CompUtils writes jobs with live in one settings file, `keylists.cusf` (in `~/bin`,
and optionally in a project): Optimization Methods (the routes the Optimization builder offers), Single Point
Methods (entry 0 is the single-point default; Benchmark runs them all), Conformer Search Methods (CREST's options),
Mixed Basis (`Gen`/`GenECP`, one set or several named ones) and ORCA Blocks. Edit it in the interface's Config › Job
Keylists, one section at a time; a section a project doesn't have follows the global one. A later stage of a method
is a line starting with `>`. A lab profile can carry the lab's standard sections (`[keylists]`), written at the
global level when it's applied. A project's solvent is `solvent = "THF"` in its `project.toml`. A file still named
`jobRelatedKeylists` (and its `Benchmark Methods` header) is read as before until `cu -refresh` renames it; older
`mixedbasis.txt`, `orcablocks.txt` and `optmethods.txt` files, and a `benchmarkMethods` list in the TOML files, are
likewise read until `cu -refresh` merges them in.

**Projects.** `cu -init` marks a folder as a project root (default: `.computils/` folder). Its `project.toml` overrides
the resources, cluster and spin settings for every job inside it, and it can hold its own `keylists.cusf` and a
`spinstates.txt`.

**Lab profiles.** A lab profile is a private TOML file holding a group's clusters and shared settings, such as a
notification bot. Keep it somewhere only your lab can read. `example-profile.toml` documents the layout.

## License

CompUtils' license is still being decided. Until then it is provided as is, without warranty of any kind: check the
inputs, job scripts and results it writes before relying on them.

## Citing CompUtils

If CompUtils helped your work, please acknowledge it with a link to this repository. Please also cite the programs,
methods and libraries your work used: [ATTRIBUTIONS.md](ATTRIBUTIONS.md) lists the citation each one asks for.

## Third-Party Software

CompUtils contains no code from Gaussian, ORCA or any other program it runs, and distributes none of them: it writes
their inputs, submits their jobs and reads their outputs. You need your own license for every program you run through
it, and must use each one under its own terms. The Python packages it needs are installed from their own distributors
(conda-forge) under their own licenses. CompUtils itself is pip-installed.

CompUtils is not affiliated with, endorsed by, or supported by Gaussian, Inc., FACCTs GmbH, the Max Planck Institute
for Coal Research or anyone else credited in [ATTRIBUTIONS.md](ATTRIBUTIONS.md). Product names and trademarks belong
to their owners and are used only to name the software CompUtils works with.

Text notifications sent through Telegram and done so through a bot you or your lab create, under Telegram's own Terms.

## Attributions

CompUtils relies on the work of many people: the programs it drives, the methods it offers through GoodVibes, every
Python package it installs, and the clusters and services it runs on. [ATTRIBUTIONS.md](ATTRIBUTIONS.md) credits each
one with its license and requested citation; `cu -attributions` and the interface's Attributions screen show the same
list.
