"""The settings rows and right-column rendering every job screen shares (the Optimization builder, and the Single
Point, Benchmark, Re-Run and Run as Written screens): a method list, resources, SLURM settings, Checkpoint and NBO,
pre-filling, and one stage shown as it will be written (Renders As)."""
import regex
from rich.text import Text

from ..catalog  import STAGE_JOIN
from ..defaults import Defaults
from ..jobs     import HeaderSends
from .common    import NUMBER, Setting, SettingsList
from .inspect   import SPAN_STYLES, RouteSpans, Styled
from .lists     import Cut

LEFT_WIDTH = 30          # the settings column, its borders included (the frame's junction sits on its right edge)
FILE_NAME = r"[\w.\-]"   # the characters a job name takes
SLURM_NAME = r"[\w.,\-]" # a partition's or cluster's (SLURM takes a comma-separated list of either)
# A method's name in its row, beside "(•) " and its program: the settings column less the list's scrollbar
METHOD_NAME_WIDTH = LEFT_WIDTH - 17
# A geometry row in an input (collapsed in Renders As)
COORDINATE_ROW = r"^\s*[A-Z][a-z]?\S*\s+-?\d+\.\d+\s+-?\d+\.\d+\s+-?\d+\.\d+"
_COORDINATE_ROW_RE = regex.compile(COORDINATE_ROW)
# SLURM rows: their config key
SLURM_KEYS = {"wall-time": "wallTime", "partition": "partition", "cluster": "cluster"}


def ProgramOf(extensionType: str) -> str:
    return "ORCA" if extensionType == Defaults.orcaExtension else "G16"


def MethodRows(choices: dict, problems: dict[str, str], choice: str, customKey: str | None, customProblem: str = "",
               label: str = "Method") -> list[Setting]:
    """A method list: one line per choice ({key: (label, template, input extension, route)}), its program in its own
    column (names cut to the room left), a greyed row saying why, then Custom (customKey None: no Custom row). The
    first usable one starts chosen."""
    width = min(max((len(name) for name, *_ in choices.values()), default=0), METHOD_NAME_WIDTH)
    rows = [Setting(key, "", problems.get(key, ""), on=False, choice=choice,
                    fixed=f"{Cut(name, width).ljust(width)}  {ProgramOf(extension)}",
                    available=template is not None and key not in problems)
            for key, (name, template, extension, route) in choices.items()]
    if customKey is not None:
        rows.append(Setting(customKey, "", customProblem, fixed="Custom", on=False, choice=choice))
    usable = next((row for row in rows if row.available), None)
    if usable is not None:
        usable.on = True
    if rows:
        rows[0].label = label
    return rows


def ResourceRows(prefix: str = "", cpus: int | None = None, resources: bool = True) -> list[Setting]:
    """CPUs, memory per core (pre-filled per method by the screen), then the SLURM rows, from the config: only the
    lines the cluster's submission header has (Bridges2's sends no -M, so it has no Cluster row). resources False:
    the SLURM rows alone (Run as Written: an input's CPUs and memory are its own)."""
    rows = ([Setting(f"{prefix}cpus", "CPUs", value=str(cpus or Defaults.CPU), accepts=r"\d"),
             Setting(f"{prefix}memory", "Memory per Core", value="", unit="GB", accepts=NUMBER)] if resources else [])
    rows += [Setting(f"{prefix}wall-time", "Wall Time", value=str(Defaults.wallTime), unit="Hours", accepts=r"\d"),
             Setting(f"{prefix}partition", "Partition", value=Defaults.partition, accepts=SLURM_NAME),
             Setting(f"{prefix}cluster", "Cluster", value=Defaults.cluster, accepts=SLURM_NAME)]
    return [row for row in rows if row.key.removeprefix(prefix) not in SLURM_KEYS
            or HeaderSends(SLURM_KEYS[row.key.removeprefix(prefix)])]


def OutputRows(prefix: str = "") -> list[Setting]:
    """Checkpoint (%chk) and NBO (the $NBO keylist, read by the last stage: pop=nbo7read), both Gaussian's."""
    return [Setting(f"{prefix}checkpoint", "Checkpoint", on=False), Setting(f"{prefix}nbo", "NBO", on=False)]


def RouteMeaning(route: str) -> Text | str:
    """What the chosen method runs, under its name (qol.toml: routeUnderEachMethod): a line per stage, numbered when
    there are several, each cut (…) to the column, so the list never bleeds (the Stages view shows them whole).
    Text: a str "[oss: guess=mix]" would be read as markup."""
    if not (Defaults.routeUnderEachMethod and route):
        return ""
    stages = route.split(STAGE_JOIN)
    width = LEFT_WIDTH - 7
    lines = ([Cut(f"{number} {stage}", width) for number, stage in enumerate(stages, 1)] if len(stages) > 1
             else [Cut(route, width)])
    return Text("\n".join(lines))


def ListValue(settings: SettingsList, key: str) -> str | None:
    """A row's typed value, None when the list has no such row (a SLURM line the header lacks)."""
    rows = [row for row in settings.rows if row.setting.key == key]
    return rows[0].value.strip() if rows else None


def FillValue(row, default: str, last: dict, key: str) -> None:
    """Pre-fill a row (memory per its method, a partition following the Opt's): a value still at the last pre-fill
    (or empty) follows the new one; a typed value is kept. last: each row's last pre-fill, by key."""
    if row.value in ("", last.get(key, "")) and row.value != default:
        row.SetValue(default)
    last[key] = default


def RendersAsText(text: str, extension: str, base: str = "", note: str = "") -> Text:
    """One stage (or a whole one-stage input) as it will be written, shortened: Link 0 on one line, the geometry as
    its atom count, blank lines left out; the route coloured as the Builder's preview colours one against base, the
    method's route as written (the method, and what CompUtils adds: the U/RO reference, geom=allcheck, guess=read,
    nofreeze). A marked geometry line (optimize.COORDINATES_MARKER, ‹…›) is kept as it is. note: a warning line
    after it (what the preview can't know)."""
    lines = text.replace("\r\n", "\n").split("\n")
    link0 = [line.strip() for line in lines if line.startswith("%")]
    rows, atoms = [], 0
    if link0 and extension == Defaults.gaussianExtension:
        rows.append(Styled(" · ".join(link0), "info"))
        lines = [line for line in lines if not line.startswith("%")]
    for line in lines + [""]:
        if _COORDINATE_ROW_RE.match(line):
            atoms += 1
            continue
        if atoms:
            rows.append(Styled(f"‹{atoms} Atoms›", "info"))
            atoms = 0
        if not line.strip():
            continue
        if line.startswith("‹"):
            rows.append(Styled(line, "info"))
            continue
        marker = line[:1]
        if marker in "#!" and base:
            route = Text(marker + " ")
            for part, origin in RouteSpans(line[1:].strip(), base):
                route.append_text(Styled(part, SPAN_STYLES[origin]))
            rows.append(route)
        else:
            rows.append(Text(line))
    if note:
        rows.append(Styled(note, "warning"))
    return Text("\n").join(rows)
