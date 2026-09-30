"""
Lab profiles: a lab's own clusters and shared settings, kept out of the public repo.

A profile is a TOML file the lab shares privately (e.g. readable only by its group on the cluster). The layout is in
example-profile.toml:
    name = "..."          shown when the profile is applied
    [settings]            global config keys (the notification bot, the benchmark suite, ...)
    [clusters.NAME]       a cluster setup offers: its submissionList (unless NAME is built in), plus its settings

Applying a profile writes its settings (and the active cluster's) into the global TOML files, as a wizard's answers
would be. A copy is kept at <binDirectory>/profile.toml: its clusters are read from it on every Load, and its header
remembers the source file, so a changed source is offered on the next run (CheckProfileSource).
"""
import hashlib
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from .console  import console
from .defaults import Defaults, Cluster, BUILTIN_CLUSTERS
from .prompts  import AskBool, AskStr

PROFILE_FILE = "profile.toml"
# Keys a profile can't set: the cluster comes from [clusters], and the others belong to this account
_NOT_SETTINGS = {"hpcType", "submissionList", "binDirectory", "colorMode"}
# The copy's header lines. TOML comments, so the copy still parses; conda-installer.py writes the same ones
_NOTE_LINE = "#@ A copy of your lab profile, kept by CompUtils. Edit the original, then run: cu -profile"
_SOURCE_PREFIX = "#@source "
_PENDING_LINE = "#@pending"
_DECLINED_PREFIX = "#@declined "


@dataclass
class Profile:
    name: str
    settings: dict
    clusters: list[Cluster]


@dataclass
class InstalledCopy:
    """<binDirectory>/profile.toml: the profile's text, plus what its header records."""
    body: str
    source: str = ""      # where it was applied from
    pending: bool = False  # placed by the installer, not applied yet
    declined: str = ""    # digest of a changed source the user chose not to apply


def _CopyPath() -> Path:
    return Path(Defaults.binDirectory) / PROFILE_FILE


def _Digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ReadCopy() -> InstalledCopy | None:
    try:
        lines = _CopyPath().read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None
    copy = InstalledCopy("")
    index = 0
    while index < len(lines) and lines[index].startswith("#@"):
        line = lines[index].rstrip("\r\n")
        if line.startswith(_SOURCE_PREFIX):
            copy.source = line[len(_SOURCE_PREFIX):].strip()
        elif line == _PENDING_LINE:
            copy.pending = True
        elif line.startswith(_DECLINED_PREFIX):
            copy.declined = line[len(_DECLINED_PREFIX):].strip()
        index += 1
    copy.body = "".join(lines[index:])
    return copy


def _WriteCopy(copy: InstalledCopy) -> bool:
    """Save the copy, readable by this account only (it can hold the bot token)."""
    header = [_NOTE_LINE] + ([_SOURCE_PREFIX + copy.source] if copy.source else []) \
             + ([_PENDING_LINE] if copy.pending else []) + ([_DECLINED_PREFIX + copy.declined] if copy.declined else [])
    path = _CopyPath()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
            file.write("\n".join(header) + "\n" + copy.body)
        path.chmod(0o600)
        return True
    except OSError as error:
        console.print(f"[error]\\[profile] Could not write {path}: {error}[/error]")
        return False


def _CheckedSettings(table: dict, where: str) -> dict:
    """A table's config keys, type-checked like the config files. Keys a profile can't set are dropped with a warning."""
    settings = {}
    for key, rawValue in table.items():
        if key not in Defaults._TYPES or key in _NOT_SETTINGS:
            console.print(f"[warning]\\[profile] '{key}' in {where} is not a setting a profile can set. Ignored.[/warning]")
            continue
        value = Defaults._Checked(key, rawValue, where, "Ignored.")
        if value is not None:
            settings[key] = value
    return settings


def _Table(data: dict, key: str, source: str) -> dict:
    table = data.get(key, {})
    if isinstance(table, dict):
        return table
    console.print(f"[warning]\\[profile] '{key}' in {source} should be a [{key}] table. Ignored.[/warning]")
    return {}


def ReadProfile(text: str, source: str) -> Profile | None:
    """A profile's contents, checked like the config files. None (with the reason printed) if it isn't valid TOML."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        console.print(f"[error]\\[profile] {source} is not valid TOML: {error}[/error]")
        return None
    for key in data.keys() - {"name", "settings", "clusters"}:
        console.print(f"[warning]\\[profile] Unknown entry '{key}' in {source}. Ignored.[/warning]")
    settings = _CheckedSettings(_Table(data, "settings", source), f"{source} [settings]")
    clusters = []
    for name, table in _Table(data, "clusters", source).items():
        where = f"{source} [clusters.{name}]"
        if not isinstance(table, dict):
            console.print(f"[warning]\\[profile] {where} should be a table. Skipped.[/warning]")
            continue
        builtin = next((cluster for cluster in BUILTIN_CLUSTERS if cluster.hpcType == name), None)
        header = table.get("submissionList")
        if header is not None and not (isinstance(header, list) and all(isinstance(line, str) for line in header)):
            console.print(f"[warning]\\[profile] submissionList in {where} should be a list of header lines.[/warning]")
            header = None
        if header is None and builtin is None:
            console.print(f"[warning]\\[profile] {where} has no submissionList (its SLURM header). Skipped.[/warning]")
            continue
        own = _CheckedSettings({key: value for key, value in table.items() if key != "submissionList"}, where)
        clusters.append(Cluster(name, header or builtin.submissionList, {**(builtin.settings if builtin else {}), **own}))
    name = data.get("name")
    return Profile(name if isinstance(name, str) and name else Path(source).stem, settings, clusters)


def InstalledProfile() -> Profile | None:
    """The installed copy's profile (Defaults.Load reads its clusters on every run), or None without one."""
    copy = _ReadCopy()
    return ReadProfile(copy.body, str(_CopyPath())) if copy is not None else None


def _WarnIfShared(source: Path) -> None:
    # Group-readable is the point (labmates share it); readable by every account on the cluster is not
    try:
        shared = os.name == "posix" and source.stat().st_mode & stat.S_IROTH
    except OSError:
        return
    if shared:
        console.print(f"[warning]{source} can be read by every account on this machine. If it holds secrets "
                      f"(botToken), run: chmod o-r {source}[/warning]")


def ApplyProfile(source: Path | None = None) -> bool:
    """Apply a lab profile file. Without one, re-apply the remembered source, or the copy if that can't be read.
    Returns True if it was applied."""
    copy = _ReadCopy()
    if source is None and copy is None:
        console.print("[error]No lab profile has been applied yet. Give its file: cu -profile FILE[/error]")
        return False
    if source is None and copy.source:
        source = Path(copy.source)
    text = None
    if source is not None:
        source = source.expanduser().resolve()
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            if copy is None or copy.source != str(source):
                console.print(f"[error]Could not read the lab profile {source}: {error}[/error]")
                return False
            console.print(f"[warning]Could not read {source} ({error}). Re-applying the saved copy instead.[/warning]")
    if text is None:
        text = copy.body
    profile = ReadProfile(text, str(source or _CopyPath()))
    if profile is None:
        return False
    if source is not None:
        _WarnIfShared(source)
    if not _WriteCopy(InstalledCopy(text, str(source) if source is not None else copy.source)):
        return False

    # Written as the global values (a project's overrides come back with the reload)
    Defaults.ResetProjectOverrides()
    values = dict(profile.settings)
    cluster = next((cluster for cluster in profile.clusters if cluster.hpcType == Defaults.hpcType), None)
    if cluster is not None:
        values.update(cluster.settings, submissionList=cluster.submissionList)
    changed = [key for key, value in values.items() if getattr(Defaults, key) != value]
    for key in changed:
        setattr(Defaults, key, values[key])
    Defaults._SaveKeys(changed)
    from .project import ReloadConfig
    ReloadConfig()
    # Key names only: values can be secrets
    console.print(f"[good]Applied the lab profile {escape(profile.name)} "
                  f"({len(profile.clusters)} {'cluster' if len(profile.clusters) == 1 else 'clusters'}). "
                  f"{'Changed: ' + ', '.join(changed) if changed else 'No settings changed'}.[/good]")
    return True


def SetupProfile(profileFile: Path | None = None) -> None:
    """First-time setup's profile step: the file given, else the copy the installer left, else ask (Enter skips)."""
    if profileFile is not None and ApplyProfile(profileFile):
        return
    copy = _ReadCopy()
    if copy is not None:
        if copy.pending:
            ApplyProfile()
        return
    console.print("[info]If your lab gave you a CompUtils lab profile (its clusters and shared settings), "
                  "enter its file now.[/info]")
    while True:
        answer = AskStr("Lab profile file (Enter to skip)", "")
        if not answer or ApplyProfile(Path(answer)):
            return


def CheckProfileSource() -> None:
    """Each interactive run (Main): apply a copy the installer left, and offer a source file that has changed."""
    copy = _ReadCopy()
    if copy is None:
        return
    if copy.pending:
        ApplyProfile()
        return
    if not copy.source:
        return
    try:
        text = Path(copy.source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return  # e.g. on another cluster, where the lab's shared folder doesn't exist
    if text == copy.body or _Digest(text) == copy.declined:
        return
    if AskBool("Your lab profile has changed. Apply it now?", "y"):
        ApplyProfile()
        return
    copy.declined = _Digest(text)
    _WriteCopy(copy)
    console.print("[info]Kept your current settings. Run cu -profile to apply it later.[/info]")
