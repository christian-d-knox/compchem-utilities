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
- **Builds route cards from templates.** Each benchmark entry is a complete route card, with optional spin groups
  (`[oss: guess=mix stable=opt]`) applied only to matching molecules, automatic U/RO references for open-shell species
  and, for ORCA, `{tags}` that pull `%` blocks from a project file.
- **Classifies spin states.** Closed-shell singlets, broken-symmetry (open-shell) singlets and higher multiplicities are
  told apart from the previous route, `<S**2>` and stability analyses, or from your own overrides.
- **Follows your jobs.** Every submitted job is tracked. The Job Stalker reports each stage as it runs (optimization
  convergence, wave-function stability, normal or error termination), and optional Telegram notifications text you
  when jobs end.
- **Runs GoodVibes.** Thermochemistry with quasi-harmonic corrections over your outputs, converted automatically into an
  Excel table, for your convenience.
- **Keeps per-project settings.** Any folder can be a project root with its own resources, benchmark list, mixed basis
  sets (`Gen`/`GenECP`), ORCA blocks and spin-state overrides.
- **Shares lab settings privately.** Configure the provided example-profile.toml to add private computing resources or
  other information (such as implementing your own notifier bot).

## Requirements

- A Linux cluster with Slurm (`sbatch`, `squeue`). Settings pre-configured for public clusters Bridges-2 (PSC) and
  Stampede3 (TACC) are built in; other clusters are added through a lab profile.
- Gaussian 16 and/or ORCA 6.1, installed and licensed on that cluster (see [Third-Party Software](#third-party-software)).
- Python 3.11 or later. The installer builds a conda environment with everything CompUtils needs.

## Installation

Download the `conda-installer.py` file from the repository and run it via `python3 conda-installer.py`. The installer
will take care of installing miniconda3 if no conda is present on the system (set to use conda-forge only), then
automatically create the CompUtils conda environment and install CompUtils inside of it. It asks nothing, and never
accepts any terms on your behalf. If your lab already has its own `profile.toml` file, CompUtils asks for it during
first-time setup (`cu -profile FILE` applies one later).

## Usage

`cu` with no arguments opens the terminal interface (set `bareCommandOpensTUI = false` in `qol.toml` to turn that off);
`cu -tui` always does. Everything below is also available as a flag. File arguments accept globs, quoted or not.

| Command | Purpose |
|---|---|
| `cu -r FILE…` | Submits input files directly |
| `cu -sp FILE…` | Generates and submits single point files based on completed jobs |
| `cu -b FILE…` | Generates and submits multiple single point files per file, according to benchmarking list |
| `cu -re FILE…` | Regenerates and resubmits a failed job |
| `cu -cu FILE…` | Generates cube files from Gaussian .chk or .fchk via cubegen |
| `cu -form FILE…` | Runs formchk on Gaussian checkpoint files |
| `cu -gv [FILE…]` | Runs GoodVibes (default: every output in cwd), then writes output to Excel |
| `cu -ex FILE` | Converts a GoodVibes output to Excel |
| `cu -st` | Follows tracked jobs via squeue, reporting on statuses such as convergence criteria. Can be included in a submission command to tag submitted jobs, or called independently |
| `cu -init` | Makes the current folder a project root. Useful for project-based overrides to global defaults |
| `cu -profile [FILE]` | Applies a lab profile |
| `cu -first` | Re-runs first-time setup |
| `cu -refresh [project]` | Rewrites the config files in the current layout, keeping your values |
| `cu --update` | Updates CompUtils from GitHub |
| `cu -attributions` | Shows the disclaimer and credits |

| Modifiers | Purpose |
|---|---|
| `-ch` | Causes generated Gaussian inputs to create .chk files |
| `-nbo` | Appends an NBO7-styled section to generated input (tested only for Gaussian at time of writing) |
| `-ovr N` | Starts single points or benchmarking from entry N in the benchmarking list |
| `--help` | Lists flags and their usage |

The terminal interface needs a window of at least 84 × 24 characters.

## Configuration

The first run walks you through setup: your cluster, colors and (optionally) Telegram text notifications, and prompts
for a lab profile (can be declined). The settings live in commented TOML files in `~/bin`:

| File | Contains |
|---|---|
| `slurm.toml` | Cluster, partition, CPUs, memory, wall time, stalking intervals |
| `programs.toml` | Method → program map, benchmark list, open-shell reference, job script boilerplate |
| `extensions.toml` | File extensions and name suffixes |
| `notifications.toml` | Telegram bot settings |
| `paths.toml` | Config directory and project marker |
| `qol.toml` | Color mode and interface preferences |

Edit them in the interface's Config screen, or by hand.

**Projects.** `cu -init` marks a folder as a project root (default: `.computils/` folder). Its `project.toml` overrides
the resources, cluster, benchmark suite and spin settings for every job inside it, and it can hold `mixedbasis.txt`,
`orcablocks.txt` and `spinstates.txt` for methods that require them.

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
