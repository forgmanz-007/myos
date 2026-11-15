"""Apps that start when you log in: the .desktop files in ~/.config/autostart and
/etc/xdg/autostart. Switching one off writes Hidden=true into your own copy, which
is the standard way and is also what the desktop itself looks at."""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import desktopfile

MARKER = "X-MyOS-Disabled-By-TaskManager"
# Keys that mark the desktop's own parts (taskbar, window manager helpers...).
_INTERNAL_KEYS = ("X-KDE-autostart-phase", "X-systemd-skip", "X-GNOME-Autostart-Phase")


def user_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart"


def system_dirs() -> list:
    dirs = (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")
    return [Path(d) / "autostart" for d in dirs if d]


def _desktops() -> list:
    return [d for d in (os.environ.get("XDG_CURRENT_DESKTOP") or "KDE").split(":") if d]


def _condition_met(condition: str) -> bool:
    """X-KDE-autostart-condition=file:group:key:default → is that setting switched on?"""
    parts = condition.split(":")
    if len(parts) < 3:
        return True
    rcfile, group, key = parts[0], parts[1], parts[2]
    value = parts[3] if len(parts) > 3 else "false"
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for base in [config_home] + [Path(d) for d in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":") if d]:
        try:
            text = (base / rcfile).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        inside = False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("["):
                inside = (s == f"[{group}]")
            elif inside and s.split("=", 1)[0].strip() == key and "=" in s:
                return s.split("=", 1)[1].strip().lower() == "true"
    return value.strip().lower() == "true"


def _starts_here(entry: dict) -> bool:
    """Would this desktop start the entry at all?"""
    if entry.get("Type", "Application") != "Application" or not entry.get("Exec"):
        return False
    desktops = _desktops()
    only = [d for d in entry.get("OnlyShowIn", "").split(";") if d]
    if only and not any(d in only for d in desktops):
        return False
    if any(d in entry.get("NotShowIn", "").split(";") for d in desktops):
        return False
    if entry.get("TryExec") and not shutil.which(entry["TryExec"]):
        return False
    if entry.get("X-KDE-autostart-condition") and not _condition_met(entry["X-KDE-autostart-condition"]):
        return False
    return True


def _internal(entry: dict) -> bool:
    return desktopfile.is_true(entry, "NoDisplay") or any(k in entry for k in _INTERNAL_KEYS)


@dataclass
class StartupEntry:
    file_id: str            # "discord.desktop"
    name: str
    command: str
    icon: str
    enabled: bool
    source: str             # "You" (your own entry) or "System" (installed for all users)
    user_path: Path         # where your own copy is, or would be
    system_path: Path = None

    @property
    def status(self) -> str:
        return "Enabled" if self.enabled else "Disabled"


def list_entries() -> list:
    udir = user_dir()
    found = {}
    for sdir in reversed(system_dirs()):            # earlier directories win
        try:
            files = sorted(sdir.glob("*.desktop"))
        except OSError:
            files = []
        for f in files:
            found[f.name] = f
    try:
        user_files = {f.name: f for f in sorted(udir.glob("*.desktop"))}
    except OSError:
        user_files = {}

    entries = []
    for file_id in sorted(set(found) | set(user_files)):
        system = desktopfile.load(found[file_id]) if file_id in found else None
        mine = desktopfile.load(user_files[file_id]) if file_id in user_files else None
        shown = mine if (mine and mine.get("Exec")) else (system or mine or {})
        if system is not None and _internal(system):
            continue                                # part of the desktop itself
        if desktopfile.is_true(system or {}, "Hidden") and mine is None:
            continue
        if not _starts_here(shown):
            continue
        enabled = not desktopfile.is_true(mine, "Hidden") if mine is not None else True
        entries.append(StartupEntry(
            file_id=file_id,
            name=desktopfile.localized(shown, "Name") or file_id[:-len(".desktop")],
            command=desktopfile.clean_exec(shown.get("Exec", "")),
            icon=shown.get("Icon", ""),
            enabled=enabled,
            source="System" if system is not None else "You",
            user_path=udir / file_id,
            system_path=found.get(file_id)))
    entries.sort(key=lambda e: e.name.lower())
    return entries


def set_enabled(entry: StartupEntry, enabled: bool) -> None:
    """Switch a startup app on or off for this user. Raises OSError if the file can't be written."""
    path = entry.user_path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = None
    if enabled:
        if text is None:
            return                                   # nothing switches it off
        if entry.system_path is not None and desktopfile.is_true(desktopfile.parse(text), MARKER):
            path.unlink()                            # our own "off" copy: the system entry applies again
            return
        path.write_text(desktopfile.set_keys(text, {"Hidden": None, MARKER: None}), encoding="utf-8")
        return
    if text is None:                                 # a system entry: make your own switched-off copy
        text = entry.system_path.read_text(encoding="utf-8", errors="replace")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktopfile.set_keys(text, {"Hidden": "true", MARKER: "true"}), encoding="utf-8")
        return
    path.write_text(desktopfile.set_keys(text, {"Hidden": "true"}), encoding="utf-8")
