"""Credit for everything CompUtils relies on: the programs it drives, the methods it offers, every Python package it uses
or that comes with it, its infrastructure and its development tools, with each one's license and requested citation.
Before the credits, ABOUT gives CompUtils' own license, how to credit it, and what it does and doesn't distribute.

The one source for the TUI's Attributions screen, `cu -attributions` and ATTRIBUTIONS.md. Licenses and copyright lines of
Python packages are copied from their installed metadata and LICENSE files; everything else from its official page
(checked 2026-10-01). Whenever a dependency, program or service is added, add its Credit here and run
`python -m computils.attributions`, which rewrites ATTRIBUTIONS.md and reports anything uncredited or any license that
no longer matches the installed metadata."""
import importlib.metadata as metadata
import platform
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

# The interpreter has no distribution to look up
PYTHON = "python"

REPO_URL = "https://github.com/christian-d-knox/compchem-utilities"


@dataclass(frozen=True)
class Notice:
    """A paragraph about CompUtils itself, shown before the credits."""
    title: str
    text: str


# CompUtils' own license, how to credit it, and what it does and doesn't distribute. README.md says the same.
ABOUT = [
    Notice("License", "CompUtils' license is still being decided. Until then it is provided as is, without warranty "
           "of any kind: check the inputs, job scripts and results it writes before relying on them."),
    Notice("Citing CompUtils", f"If CompUtils helped your work, please acknowledge it with a link to {REPO_URL}. "
           "Please also cite the programs, methods and libraries below that your work used."),
    Notice("Third-Party Software", "CompUtils contains no code from Gaussian, ORCA or any other program it runs, "
           "and distributes none of them: it writes their inputs, submits their jobs and reads their outputs. You "
           "need your own license for every program you run through it, and must use each one under its own terms. "
           "The Python packages it needs are installed from their own distributors (conda-forge) under their own "
           "licenses, listed below. CompUtils is not affiliated with, endorsed by or supported by Gaussian, Inc., "
           "FACCTs GmbH, the Max Planck Institute for Coal Research or anyone else credited here. Product names and "
           "trademarks belong to their owners and are used only to name the software CompUtils works with."),
]


@dataclass(frozen=True)
class Credit:
    name: str
    use: str                    # what CompUtils uses it for, or why it is here
    authors: str
    license: str                # as its authors declare it
    url: str
    distribution: str = ""      # its pip name: the version is read live and the license checked
    copyright: str = ""         # as its LICENSE file writes it
    citation: str = ""          # what its authors ask to be cited
    via: str = ""               # what pulled it in


@dataclass(frozen=True)
class Section:
    title: str
    intro: str
    credits: list[Credit]


GAUSSIAN_CITATION = (
    "Gaussian 16, Revision C.01, M. J. Frisch, G. W. Trucks, H. B. Schlegel, G. E. Scuseria, M. A. Robb, "
    "J. R. Cheeseman, G. Scalmani, V. Barone, G. A. Petersson, H. Nakatsuji, X. Li, M. Caricato, A. V. Marenich, "
    "J. Bloino, B. G. Janesko, R. Gomperts, B. Mennucci, H. P. Hratchian, J. V. Ortiz, A. F. Izmaylov, J. L. Sonnenberg, "
    "D. Williams-Young, F. Ding, F. Lipparini, F. Egidi, J. Goings, B. Peng, A. Petrone, T. Henderson, D. Ranasinghe, "
    "V. G. Zakrzewski, J. Gao, N. Rega, G. Zheng, W. Liang, M. Hada, M. Ehara, K. Toyota, R. Fukuda, J. Hasegawa, "
    "M. Ishida, T. Nakajima, Y. Honda, O. Kitao, H. Nakai, T. Vreven, K. Throssell, J. A. Montgomery, Jr., J. E. Peralta, "
    "F. Ogliaro, M. J. Bearpark, J. J. Heyd, E. N. Brothers, K. N. Kudin, V. N. Staroverov, T. A. Keith, R. Kobayashi, "
    "J. Normand, K. Raghavachari, A. P. Rendell, J. C. Burant, S. S. Iyengar, J. Tomasi, M. Cossi, J. M. Millam, "
    "M. Klene, C. Adamo, R. Cammi, J. W. Ochterski, R. L. Martin, K. Morokuma, O. Farkas, J. B. Foresman, and D. J. Fox, "
    "Gaussian, Inc., Wallingford CT, 2019. (As Revision C.01 prints it; use your own revision. Gaussian also asks that "
    "the methods used be cited.)")

SECTIONS = [
    Section("Programs CompUtils Runs", "CompUtils writes their inputs, submits their jobs and reads their outputs.", [
        Credit("Gaussian 16", "Job generation, submission and output parsing; formchk formats checkpoints and cubegen "
               "makes cube files.", "Gaussian, Inc. (M. J. Frisch et al.)", "Proprietary (commercial license from "
               "Gaussian, Inc.)", "https://gaussian.com", citation=GAUSSIAN_CITATION),
        Credit("ORCA 6.1", "Job generation, submission and output parsing.", "Frank Neese and coworkers, Max Planck "
               "Institute for Coal Research (FACCTs GmbH)", "Proprietary EULA: free for academic and personal use; "
               "commercial licenses from FACCTs", "https://www.faccts.de/orca/",
               citation="F. Neese, Software Update: The ORCA Program System, Version 6.0, WIREs Comput. Mol. Sci. "
               "2025, 15(2), e70019. doi:10.1002/wcms.70019. ORCA also writes a per-run .bibtex file of the papers "
               "behind each calculation, and asks that they be cited in the main text, not the SI."),
        Credit("GoodVibes", "The thermochemistry behind -gv and the TUI's GoodVibes runner.",
               "Paton Research Group (G. Luchini, J. V. Alegre-Requena, I. Funes-Ardoiz, R. S. Paton)", "MIT",
               "https://github.com/patonlab/GoodVibes", distribution="goodvibes",
               copyright="Copyright 2017 Robert Paton, University of Oxford",
               citation="G. Luchini, J. V. Alegre-Requena, I. Funes-Ardoiz, R. S. Paton, F1000Research 2020, 9, 291. "
               "doi:10.12688/f1000research.22758.1"),
    ]),
    Section("Methods CompUtils Offers Through GoodVibes", "References as GoodVibes itself gives them.", [
        Credit("Quasi-RRHO Entropy (Grimme)", "GoodVibes' default quasi-harmonic entropy.", "S. Grimme",
               "Published method", "https://doi.org/10.1002/chem.201200497",
               citation="S. Grimme, Chem. Eur. J. 2012, 18, 9955-9964."),
        Credit("Quasi-Harmonic Entropy (Truhlar)", "The TUI's Truhlar entropy option (--qs truhlar).",
               "R. F. Ribeiro, A. V. Marenich, C. J. Cramer, D. G. Truhlar", "Published method",
               "https://doi.org/10.1021/jp205508z",
               citation="R. F. Ribeiro, A. V. Marenich, C. J. Cramer, D. G. Truhlar, J. Phys. Chem. B 2011, 115, "
               "14556-14562."),
        Credit("Quasi-Harmonic Enthalpy (Head-Gordon)", "The Head-Gordon enthalpy correction (-q).",
               "Y. Li, J. Gomes, S. M. Sharada, A. T. Bell, M. Head-Gordon", "Published method",
               "https://doi.org/10.1021/jp509921r",
               citation="Y. Li, J. Gomes, S. M. Sharada, A. T. Bell, M. Head-Gordon, J. Phys. Chem. C 2015, 119, "
               "1840-1850."),
    ]),
    Section("Python Libraries CompUtils Uses", "Imported by CompUtils itself.", [
        Credit("Python", "The language CompUtils is written in, and its standard library (tomllib, mmap, argparse, "
               "subprocess, urllib, ...).", "Python Software Foundation", "PSF License Agreement (Python-2.0)",
               "https://www.python.org", distribution=PYTHON,
               copyright="Copyright (c) 2001-2023 Python Software Foundation; All Rights Reserved"),
        Credit("Rich", "Every coloured message, panel and prompt in the CLI; the TUI's text.", "Will McGugan / "
               "Textualize", "MIT", "https://github.com/Textualize/rich", distribution="rich",
               copyright="Copyright (c) 2020 Will McGugan"),
        Credit("Textual", "The TUI.", "Will McGugan / Textualize", "MIT", "https://github.com/Textualize/textual",
               distribution="textual", copyright="Copyright (c) 2021 Will McGugan"),
        Credit("pandas", "The GoodVibes table to Excel conversion (-ex, -gv).", "The pandas development team",
               "BSD-3-Clause", "https://pandas.pydata.org", distribution="pandas",
               copyright="Copyright (c) 2008-2011, AQR Capital Management, LLC, Lambda Foundry, Inc. and PyData "
               "Development Team; Copyright (c) 2011-2026, Open source contributors.",
               citation="The pandas development team, pandas-dev/pandas: Pandas, Zenodo, doi:10.5281/zenodo.3509134; "
               "W. McKinney, Data Structures for Statistical Computing in Python, Proceedings of the 9th Python in "
               "Science Conference, 2010, 56-61, doi:10.25080/Majora-92bf1922-00a."),
        Credit("regex", "Every search of an output or input file (memory-mapped, including reverse searches).",
               "Matthew Barnett", "Apache-2.0 AND CNRI-Python", "https://github.com/mrabarnett/mrab-regex",
               distribution="regex", copyright="Derived from CPython's re module, copyright (c) 1998-2001 by Secret "
               "Labs AB (CNRI Python 1.6 license); additions and alterations Apache-2.0"),
        Credit("XlsxWriter", "Writes GoodVibes.xlsx (as pandas' Excel engine).", "John McNamara", "BSD-2-Clause",
               "https://github.com/jmcnamara/XlsxWriter", distribution="xlsxwriter",
               copyright="Copyright (c) 2013-2025, John McNamara"),
    ]),
    Section("Pulled In by Those", "Not imported by CompUtils, but installed because the libraries above need them.", [
        Credit("markdown-it-py", "Markdown parsing for Rich and Textual.", "ExecutableBookProject (Chris Sewell); "
               "port of markdown-it by Vitaly Puzrin and Alex Kocharin", "MIT",
               "https://github.com/executablebooks/markdown-it-py", distribution="markdown-it-py",
               copyright="Copyright (c) 2020 ExecutableBookProject; Copyright (c) 2014 Vitaly Puzrin, Alex Kocharin",
               via="Rich, Textual"),
        Credit("mdurl", "URL utilities for markdown-it-py.", "Taneli Hukkinen; port of mdurl by Vitaly Puzrin and "
               "Alex Kocharin", "MIT", "https://github.com/executablebooks/mdurl", distribution="mdurl",
               copyright="Copyright (c) 2015 Vitaly Puzrin, Alex Kocharin; Copyright (c) 2021 Taneli Hukkinen; "
               "Copyright Joyent, Inc. and other Node contributors", via="markdown-it-py"),
        Credit("mdit-py-plugins", "markdown-it-py plugins.", "ExecutableBookProject (Chris Sewell)", "MIT",
               "https://github.com/executablebooks/mdit-py-plugins", distribution="mdit-py-plugins",
               copyright="Copyright (c) 2020 ExecutableBookProject", via="Textual"),
        Credit("linkify-it-py", "Link detection for markdown-it-py.", "tsutsu3; port of linkify-it by Vitaly Puzrin",
               "MIT", "https://github.com/tsutsu3/linkify-it-py", distribution="linkify-it-py",
               copyright="Copyright (c) 2020 tsutsu3; Copyright (c) 2015 Vitaly Puzrin", via="Textual (markdown-it-py[linkify])"),
        Credit("platformdirs", "Platform-specific directories.", "The platformdirs developers", "MIT",
               "https://github.com/tox-dev/platformdirs", distribution="platformdirs",
               copyright="Copyright (c) 2010-202x The platformdirs developers", via="Textual"),
        Credit("Pygments", "Syntax highlighting.", "Georg Brandl and the Pygments authors", "BSD-2-Clause",
               "https://pygments.org", distribution="Pygments",
               copyright="Copyright (c) 2006-2022 by the respective authors (see AUTHORS file)", via="Rich, Textual"),
        Credit("typing_extensions", "Backported typing features.", "Guido van Rossum, Jukka Lehtosalo, Łukasz Langa, "
               "Michael Lee and contributors", "PSF-2.0", "https://github.com/python/typing_extensions",
               distribution="typing_extensions", copyright="Copyright (c) 2001-2023 Python Software Foundation",
               via="Textual"),
        Credit("NumPy", "Arrays for pandas and GoodVibes.", "Travis E. Oliphant et al., the NumPy developers",
               "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 (bundled parts under their own licenses)",
               "https://numpy.org", distribution="numpy", copyright="Copyright (c) 2005-2025, NumPy Developers.",
               citation="C. R. Harris, K. J. Millman, S. J. van der Walt et al., Array programming with NumPy, Nature "
               "2020, 585, 357-362. doi:10.1038/s41586-020-2649-2", via="pandas, GoodVibes"),
        Credit("python-dateutil", "Date handling for pandas.", "Gustavo Niemeyer, Paul Ganssle and the dateutil "
               "contributors", "Apache-2.0 (and BSD-3-Clause for older code)", "https://github.com/dateutil/dateutil",
               distribution="python-dateutil", copyright="Copyright 2017- Paul Ganssle; Copyright 2017- dateutil "
               "contributors; Copyright (c) 2003-2011 Gustavo Niemeyer", via="pandas"),
        Credit("six", "Python 2/3 compatibility for python-dateutil.", "Benjamin Peterson", "MIT",
               "https://github.com/benjaminp/six", distribution="six", copyright="Copyright (c) 2010-2024 Benjamin "
               "Peterson", via="python-dateutil"),
        Credit("tzdata", "Time zone data for pandas (the IANA tz database, which is public domain).",
               "Paul Ganssle (Python Software Foundation)", "Apache-2.0", "https://github.com/python/tzdata",
               distribution="tzdata", copyright="Copyright (c) 2020, Paul Ganssle (Google)", via="pandas"),
        Credit("cclib", "Output parsing inside GoodVibes.", "The cclib development team", "BSD-3-Clause",
               "https://cclib.github.io", distribution="cclib",
               copyright="Copyright (c) 2024, the cclib development team",
               citation="E. Berquist, A. Dumi, S. Upadhyay et al., cclib 2.0: An updated architecture for "
               "interoperable computational chemistry, J. Chem. Phys. 2024, 161, 042501, doi:10.1063/5.0216778; "
               "N. M. O'Boyle, A. L. Tenderholt, K. M. Langner, cclib: a library for package-independent "
               "computational chemistry algorithms, J. Comput. Chem. 2008, 29, 839-845, doi:10.1002/jcc.20823",
               via="GoodVibes"),
        Credit("SciPy", "Numerical routines for cclib.", "The SciPy developers (Enthought, Inc. and contributors)",
               "BSD-3-Clause", "https://scipy.org", distribution="scipy",
               copyright="Copyright (c) 2001-2002 Enthought, Inc. 2003, SciPy Developers.",
               citation="P. Virtanen, R. Gommers, T. E. Oliphant et al., SciPy 1.0: Fundamental Algorithms for "
               "Scientific Computing in Python, Nat. Methods 2020, 17, 261-272. doi:10.1038/s41592-019-0686-2",
               via="cclib"),
        Credit("periodictable", "Element data for cclib.", "Paul Kienzle", "Public domain (some files under their "
               "own authors' licenses)", "https://github.com/python-periodictable/periodictable",
               distribution="periodictable", copyright="Copyright (c) 2009 Trustees of the Columbia University (parts)",
               via="cclib"),
        Credit("pyparsing", "Parsing for periodictable.", "Paul McGuire", "MIT", "https://github.com/pyparsing/pyparsing",
               distribution="pyparsing", copyright="Copyright (c) 2003-2025 Paul McGuire", via="periodictable"),
        Credit("packaging", "Version handling for cclib.", "Donald Stufft and individual contributors",
               "Apache-2.0 OR BSD-2-Clause", "https://github.com/pypa/packaging", distribution="packaging",
               copyright="Copyright (c) Donald Stufft and individual contributors.", via="cclib"),
    ]),
    Section("Installed with the Environment", "Installed by compUtils.yml for features still to come; CompUtils doesn't "
            "use them yet.", [
        Credit("Matplotlib", "Plotting (planned).", "John D. Hunter, Michael Droettboom and the Matplotlib "
               "Development Team", "Matplotlib License (PSF-based)", "https://matplotlib.org", distribution="matplotlib",
               copyright="Copyright (c) 2012- Matplotlib Development Team; Copyright (c) 2002-2011 John D. Hunter",
               citation="J. D. Hunter, Matplotlib: A 2D Graphics Environment, Comput. Sci. Eng. 2007, 9(3), 90-95. "
               "doi:10.1109/MCSE.2007.55"),
        Credit("ContourPy", "Contouring for Matplotlib.", "Ian Thomas and the ContourPy developers", "BSD-3-Clause",
               "https://github.com/contourpy/contourpy", distribution="contourpy",
               copyright="Copyright (c) 2021-2025, ContourPy Developers.", via="Matplotlib"),
        Credit("Cycler", "Style cycling for Matplotlib.", "Thomas A. Caswell, the Matplotlib project", "BSD-3-Clause",
               "https://github.com/matplotlib/cycler", distribution="cycler",
               copyright="Copyright (c) 2015, matplotlib project", via="Matplotlib"),
        Credit("fontTools", "Font handling for Matplotlib.", "Just van Rossum and contributors", "MIT (bundled "
               "fonts under their own licenses)", "https://github.com/fonttools/fonttools", distribution="fonttools",
               copyright="Copyright (c) 2017 Just van Rossum", via="Matplotlib"),
        Credit("Kiwi Solver", "Layout constraints for Matplotlib.", "The Nucleic Development Team", "BSD-3-Clause",
               "https://github.com/nucleic/kiwi", distribution="kiwisolver",
               copyright="Copyright (c) 2013-2025, Nucleic Development Team", via="Matplotlib"),
        Credit("Pillow", "Images for Matplotlib.", "Jeffrey A. Clark and contributors (fork of PIL by Fredrik Lundh)",
               "MIT-CMU", "https://python-pillow.github.io", distribution="pillow",
               copyright="Copyright (c) 1997-2011 by Secret Labs AB; Copyright (c) 1995-2011 by Fredrik Lundh and "
               "contributors; Copyright (c) 2010 by Jeffrey A. Clark and contributors", via="Matplotlib"),
        Credit("scikit-learn", "Machine learning (planned).", "The scikit-learn developers", "BSD-3-Clause",
               "https://scikit-learn.org", distribution="scikit-learn",
               copyright="Copyright (c) 2007-2024 The scikit-learn developers.",
               citation="F. Pedregosa, G. Varoquaux, A. Gramfort et al., Scikit-learn: Machine Learning in Python, "
               "J. Mach. Learn. Res. 2011, 12, 2825-2830."),
        Credit("joblib", "Parallel helpers for scikit-learn.", "Gael Varoquaux and the joblib developers",
               "BSD-3-Clause", "https://joblib.readthedocs.io", distribution="joblib",
               copyright="Copyright (c) 2008-2021, The joblib developers.", via="scikit-learn"),
        Credit("threadpoolctl", "Thread pool control for scikit-learn.", "Thomas Moreau and the threadpoolctl "
               "contributors", "BSD-3-Clause", "https://github.com/joblib/threadpoolctl", distribution="threadpoolctl",
               copyright="Copyright (c) 2019, threadpoolctl contributors", via="scikit-learn"),
        Credit("PySide6 (Qt for Python)", "A graphical interface (planned).", "The Qt Company", "LGPL-3.0-only (also "
               "GPL or commercial)", "https://www.qt.io/qt-for-python", distribution="PySide6"),
        Credit("Shiboken6", "The binding generator runtime behind PySide6.", "The Qt Company", "LGPL-3.0-only (also "
               "GPL or commercial)", "https://www.qt.io/qt-for-python", distribution="shiboken6", via="PySide6"),
        Credit("Qt 6", "The framework PySide6 wraps.", "The Qt Company and the Qt Project", "LGPL-3.0-only (also GPL "
               "or commercial)", "https://www.qt.io", via="PySide6"),
    ]),
    Section("Infrastructure", "What CompUtils is installed with, runs on and talks to.", [
        Credit("Slurm", "Every job is submitted with sbatch and followed with squeue.", "SchedMD LLC and the Slurm "
               "contributors", "GPL-2.0 (with an OpenSSL linking exception)", "https://slurm.schedmd.com"),
        Credit("Telegram Bot API", "Job notifications and group broadcasts.", "Telegram", "Telegram Bot Platform "
               "Developer Terms of Service", "https://core.telegram.org/bots/api"),
        Credit("conda", "Builds and updates the CompUtils environment (conda-installer.py).", "Anaconda, Inc. and the "
               "conda contributors", "BSD-3-Clause", "https://github.com/conda/conda",
               copyright="Copyright (c) 2012, Anaconda, Inc."),
        Credit("Miniconda", "Installed by conda-installer.py when no conda is found, and set to use conda-forge only.",
               "Anaconda, Inc.", "Free to install; Anaconda's Terms of Service apply to its package repositories, "
               "which CompUtils' environment doesn't use (it is built from conda-forge only)",
               "https://www.anaconda.com/docs/getting-started/miniconda/main"),
        Credit("conda-forge", "Every package in the environment comes from its channel.", "The conda-forge community",
               "Recipes BSD-3-Clause; each package keeps its own license", "https://conda-forge.org"),
        Credit("Git", "Installs CompUtils from GitHub (pip install git+..., cu --update).", "Linus Torvalds, Junio C "
               "Hamano and the Git contributors", "GPL-2.0-only", "https://git-scm.com"),
        Credit("GitHub", "Hosts CompUtils; updates read its REST API for the latest commit and the branches.",
               "GitHub, Inc.", "GitHub Terms of Service (REST API use under GitHub's API terms)",
               "https://docs.github.com/en/rest"),
        Credit("pip", "Installs CompUtils itself.", "The pip developers", "MIT", "https://pip.pypa.io",
               distribution="pip", copyright="Copyright (c) 2008-present The pip developers (see AUTHORS.txt file)"),
        Credit("setuptools", "Builds CompUtils (pyproject.toml's build backend).", "Python Packaging Authority",
               "MIT", "https://github.com/pypa/setuptools", distribution="setuptools"),
        Credit("wheel", "Builds wheels for pip.", "Daniel Holth and contributors", "MIT", "https://github.com/pypa/wheel",
               distribution="wheel", copyright="Copyright (c) 2012 Daniel Holth and contributors"),
        Credit("Bridges-2 (PSC)", "A built-in cluster target.", "Pittsburgh Supercomputing Center; supported by NSF "
               "grant OAC-1928147, allocated through ACCESS", "Center resource (acknowledge it in publications)",
               "https://www.psc.edu/resources/bridges-2/",
               citation="S. T. Brown, P. Buitrago, E. Hanna, S. Sanielevici, R. Scibek, N. A. Nystrom, Bridges-2: A "
               "Platform for Rapidly-Evolving and Data Intensive Research, PEARC '21, 2021. "
               "doi:10.1145/3437359.3465593"),
        Credit("Stampede3 (TACC)", "A built-in cluster target.", "Texas Advanced Computing Center, The University of "
               "Texas at Austin; supported by NSF award 2320757, allocated through ACCESS", "Center resource "
               "(acknowledge TACC in publications)", "https://tacc.utexas.edu/systems/stampede3/"),
    ]),
    Section("Libraries Inside ORCA", "Not CompUtils' dependencies: ORCA's output asks for them to be cited.", [
        Credit("Libint", "ORCA's two-electron integrals.", "Edward F. Valeev", "LGPL-3.0 (library); GPL-3.0 (code "
               "generator)", "http://libint.valeyev.net", citation="E. F. Valeev, Libint: A library for the "
               "evaluation of molecular integrals of many-body operators over Gaussian functions, "
               "http://libint.valeyev.net/ (cite the version ORCA was built with)"),
        Credit("Libxc", "ORCA's exchange-correlation functionals (7.0.0 in ORCA 6.1).", "Susi Lehtola, Miguel A. L. "
               "Marques and the Libxc contributors", "MPL-2.0", "https://libxc.gitlab.io",
               citation="S. Lehtola, C. Steigemann, M. J. T. Oliveira, M. A. L. Marques, Recent developments in "
               "Libxc - A comprehensive library of functionals for density functional theory, SoftwareX 2018, 7, "
               "1-5. doi:10.1016/j.softx.2017.11.002"),
        Credit("OpenBLAS", "ORCA's linear algebra (0.3.29 in ORCA 6.1).", "The OpenBLAS Project", "BSD-3-Clause",
               "https://www.openblas.net", copyright="Copyright (c) 2011-2014, The OpenBLAS Project"),
    ]),
    Section("Development Tools", "Used to build CompUtils, not to run it.", [
        Credit("memray", "Measures CompUtils' memory use (compUtilsDebug.yml).", "Bloomberg", "Apache-2.0",
               "https://github.com/bloomberg/memray", distribution="memray", copyright="Copyright 2022 Bloomberg LP"),
        Credit("Claude", "The AI pair-programmer CompUtils is developed with (Claude Code, Claude Desktop).",
               "Anthropic",
               "Not applicable (a development tool, not part of CompUtils)", "https://www.anthropic.com/claude"),
    ]),
]


def Version(credit: Credit) -> str:
    """The installed version, or '' (not a Python package, not installed, or a build that reports no real version)."""
    if credit.distribution == PYTHON:
        return platform.python_version()
    if not credit.distribution:
        return ""
    try:
        version = metadata.version(credit.distribution)
    except metadata.PackageNotFoundError:
        return ""
    # conda-forge's PySide6 build reports '6.10.2 ' (trailing space), and its cclib build 0.0.0
    version = version.strip()
    return "" if version == "0.0.0" else version


def _Fields(credit: Credit) -> list[tuple[str, str]]:
    fields = [("Use", credit.use), ("By", credit.authors), ("License", credit.license)]
    fields += [(label, value) for label, value in (("Copyright", credit.copyright), ("Cite", credit.citation),
                                                     ("Needed By", credit.via)) if value]
    return fields + [("Link", credit.url)]


def AttributionText() -> Text:
    """The page the TUI shows and `cu -attributions` prints."""
    text = Text()
    for notice in ABOUT:
        text.append(f"{notice.title}\n", "bold underline")
        text.append(f"{notice.text}\n\n")
    text.append("CompUtils relies on the work of everyone below. Thank you.\n", "bold")
    for section in SECTIONS:
        text.append(f"\n{section.title}\n", "bold underline")
        text.append(f"{section.intro}\n", "dim")
        for credit in section.credits:
            version = Version(credit)
            text.append(f"\n  {credit.name}", "bold")
            text.append(f"  {version}\n" if version else "\n", "dim")
            for label, value in _Fields(credit):
                text.append(f"    {label}: ", "dim")
                text.append(f"{value}\n")
    text.rstrip()
    return text


def Markdown() -> str:
    """ATTRIBUTIONS.md: the same credits, with the versions they were checked against."""
    lines = ["# Attributions", "",
             "Generated by `python -m computils.attributions` from `computils/attributions.py`, which also feeds "
             "`cu -attributions` and the TUI's Attributions screen. Python package licenses and copyright lines are "
             "copied from the installed packages; versions are those installed when this file was generated.", ""]
    for notice in ABOUT:
        lines += [f"## {notice.title}", "", notice.text, ""]
    lines += ["CompUtils relies on the work of everyone below. Thank you.", ""]
    for section in SECTIONS:
        lines += [f"## {section.title}", "", section.intro, ""]
        for credit in section.credits:
            version = Version(credit)
            lines += [f"### {credit.name}" + (f" ({version})" if version else ""), ""]
            lines += [f"- **{label}:** {value}" for label, value in _Fields(credit)] + [""]
    return "\n".join(lines)


# ─── Completeness and license check (python -m computils.attributions) ─

def _Key(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")

def _Roots(repo: Path) -> set[str]:
    """Every package pyproject.toml and the environment files ask for."""
    import regex, tomllib
    roots = {regex.split(r"[ <>=!~\[;]", entry)[0] for entry in
             tomllib.loads((repo / "pyproject.toml").read_text())["project"]["dependencies"]}
    roots |= set(tomllib.loads((repo / "pyproject.toml").read_text())["build-system"]["requires"][0:1])
    for yml in repo.glob("compUtils*.yml"):
        # Only the dependencies list (the channels list comes first)
        dependencies = yml.read_text().split("dependencies:", 1)[-1]
        roots |= {regex.split(r"[ <>=!~]", line.strip()[2:])[0] for line in dependencies.splitlines()
                  if line.startswith("  - ")}
    return {regex.split(r"[ <>=!~]", root)[0] for root in roots}

def _Requirements(name: str, extras: set[str]) -> list[tuple[str, set[str]]]:
    import regex
    found = []
    for requirement in metadata.requires(name) or []:
        marker = requirement.split(";", 1)[1] if ";" in requirement else ""
        extra = regex.search(r"extra\s*==\s*['\"]([^'\"]+)", marker)
        if extra and extra.group(1) not in extras:
            continue
        match = regex.match(r"\s*([A-Za-z0-9_.-]+)(?:\[([^\]]+)\])?", requirement)
        found.append((match.group(1), set((match.group(2) or "").replace(" ", "").split(",")) - {""}))
    return found

def Check(repo: Path) -> list[str]:
    """Problems: installed packages reachable from the roots with no Credit, and Credits whose license no longer
    matches what the installed package declares."""
    credited = {_Key(credit.distribution) for section in SECTIONS for credit in section.credits if credit.distribution}
    names = {_Key(credit.name) for section in SECTIONS for credit in section.credits}
    problems, seen = [], {}
    pending = [(root, set()) for root in sorted(_Roots(repo))]
    while pending:
        name, extras = pending.pop()
        key = _Key(name)
        # A package reached again with new extras (markdown-it-py[linkify]) is walked again for them
        if key in seen and extras <= seen[key]:
            continue
        firstVisit = key not in seen
        seen[key] = seen.get(key, set()) | extras
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            # Not a Python package in this environment (python, git) or not installed here (memray)
            if firstVisit and key not in credited and key not in names:
                problems.append(f"{name}: requested by the project but not credited (and not installed here)")
            continue
        if firstVisit and key not in credited:
            problems.append(f"{name}: installed and needed, but not credited")
        pending += _Requirements(name, seen[key])
    for section in SECTIONS:
        for credit in section.credits:
            if not credit.distribution or credit.distribution == PYTHON:
                continue
            try:
                declared = metadata.metadata(credit.distribution)
            except metadata.PackageNotFoundError:
                continue
            stated = declared.get("License-Expression") or (declared.get("License") or "").split("\n")[0]
            classifiers = [entry.split("::")[-1].strip() for entry in declared.get_all("Classifier") or []
                           if entry.startswith("License")]
            tokens = _LicenseTokens(stated) + [token for entry in classifiers for token in _LicenseTokens(entry)]
            if tokens and not any(token in _Key(credit.license) for token in tokens):
                problems.append(f"{credit.name}: credited as '{credit.license}', the package declares "
                                f"'{stated or '; '.join(classifiers)}'")
    return problems

def _LicenseTokens(text: str) -> list[str]:
    """The license names in a declaration, normalized (e.g. 'BSD License' -> 'bsd', 'Apache-2.0' -> 'apache-2-0')."""
    import regex
    words = {"license", "licence", "osi", "approved", "and", "with", "the", "software", "foundation", "copyright"}
    return [_Key(token) for token in regex.findall(r"[A-Za-z][A-Za-z0-9.-]{2,}", text) if token.lower() not in words][:3]


if __name__ == "__main__":
    repo = Path(__file__).resolve().parent.parent
    (repo / "ATTRIBUTIONS.md").write_text(Markdown(), encoding="utf-8", newline="\r\n")
    print(f"Wrote {repo / 'ATTRIBUTIONS.md'}")
    problems = Check(repo)
    for problem in problems:
        print(f"  - {problem}")
    print("Every needed package is credited and every license matches." if not problems
          else f"{len(problems)} to look at.")
