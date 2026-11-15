"""Sorts processes into the three groups of the Processes tab: apps (one row per
app, however many processes it runs), background processes and system processes.

The desktop starts every app in its own systemd unit, named after the app's
.desktop file ("app-firefox@1a2b.service"); that is how apps are recognized."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import desktopfile

_APP_UNIT = re.compile(r"^app-(?P<body>.+)\.(?P<type>scope|service)$")
EXE_RUNNER_ID = "myos-exe-runner"       # the "Windows Program (virus-checked)" launcher
# Wine's own helper programs: they run next to every Windows program.
WINE_HELPERS = {"services.exe", "winedevice.exe", "plugplay.exe", "explorer.exe", "svchost.exe",
                "rpcss.exe", "conhost.exe", "start.exe", "wineboot.exe", "tabtip.exe",
                "winemenubuilder.exe", "steam.exe"}

# What well-known processes are, in plain words.
KNOWN = {
    "plasmashell": "Desktop, taskbar and Start menu",
    "kwin_wayland": "Window manager",
    "kwin_x11": "Window manager",
    "kwin_wayland_wrapper": "Window manager starter",
    "Xwayland": "Support for older apps",
    "kded6": "Desktop services",
    "ksmserver": "Session manager",
    "kglobalacceld": "Keyboard shortcuts",
    "krunner": "Search",
    "kactivitymanagerd": "Recent files",
    "polkit-kde-authentication-agent-1": "Password prompts",
    "org_kde_powerdevil": "Power management",
    "xdg-desktop-portal": "App permissions",
    "xdg-desktop-portal-kde": "App permissions and file dialogs",
    "xdg-desktop-portal-gtk": "App permissions for other apps",
    "xdg-document-portal": "App file access",
    "xdg-permission-store": "App permission list",
    "pipewire": "Sound and video",
    "pipewire-pulse": "Sound",
    "wireplumber": "Sound devices",
    "baloo_file": "File search index",
    "kaccess": "Accessibility",
    "at-spi-bus-launcher": "Accessibility",
    "at-spi2-registryd": "Accessibility",
    "xembedsniproxy": "Tray icons of older apps",
    "gmenudbusmenuproxy": "App menus",
    "xwaylandvideobridge": "Screen sharing for older apps",
    "kwalletd6": "Password wallet",
    "gnome-keyring-daemon": "Password storage",
    "dconf-service": "App settings",
    "xsettingsd": "Settings for older apps",
    "ssh-agent": "SSH keys",
    "gcr-ssh-agent": "SSH keys",
    "obexd": "Bluetooth file transfer",
    "mpris-proxy": "Bluetooth media buttons",
    "startplasma-wayland": "Desktop session",
    "startplasma-x11": "Desktop session",
    "(sd-pam)": "Login session",
    "wineserver": "Windows program support",
    "systemd": "System and service manager",
    "dbus-daemon": "Message bus",
    "dbus-broker": "Message bus",
    "dbus-broker-launch": "Message bus",
    "NetworkManager": "Network",
    "wpa_supplicant": "Wi-Fi",
    "ModemManager": "Mobile broadband",
    "sddm": "Login screen",
    "sddm-helper": "Login screen",
    "ollama": "AI engine",
    "llama-server": "AI model",
    "clamd": "Virus scanner",
    "freshclam": "Virus signature updates",
    "systemd-journald": "System log",
    "systemd-logind": "Logins",
    "systemd-udevd": "Device manager",
    "systemd-resolved": "Network names",
    "systemd-timesyncd": "Clock sync",
    "systemd-networkd": "Network",
    "systemd-oomd": "Memory guard",
    "polkitd": "Permissions",
    "udisksd": "Drives",
    "upowerd": "Battery and power",
    "bluetoothd": "Bluetooth",
    "cupsd": "Printing",
    "cups-browsed": "Printer discovery",
    "fwupd": "Firmware updates",
    "accounts-daemon": "User accounts",
    "power-profiles-daemon": "Power modes",
    "avahi-daemon": "Network discovery",
    "rtkit-daemon": "Sound priority",
    "cron": "Scheduled jobs",
    "sshd": "Remote login",
    "agetty": "Text console",
    "snapperd": "System snapshots",
    "packagekitd": "Software updates",
    "flatpak-system-helper": "App installer",
    "unattended-upgr": "Automatic updates",
}

# Ending one of these ends the whole desktop session (all open apps close).
ENDS_SESSION = {"kwin_wayland", "kwin_x11", "kwin_wayland_wrapper", "ksmserver",
                "startplasma-wayland", "startplasma-x11", "systemd", "(sd-pam)",
                "dbus-daemon", "dbus-broker", "dbus-broker-launch"}


def unit_of(cgroup: str) -> str:
    return cgroup.rstrip("/").rsplit("/", 1)[-1] if cgroup else ""


def _unescape(text: str) -> str:
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)


def parse_app_unit(unit: str):
    """'app-org.kde.dolphin@1a2b.service' → ('org.kde.dolphin', '1a2b'); None if not an app unit."""
    m = _APP_UNIT.match(unit)
    if not m:
        return None
    body = m.group("body")
    if m.group("type") == "service":
        body, _, instance = body.partition("@")
    else:
        head, _, tail = body.rpartition("-")
        body, instance = (head, tail) if head else (tail, "")
    if "-" in body:                     # "flatpak-org.mozilla.firefox": drop the launcher's name
        body = body.split("-", 1)[1]    # (dashes of the app id itself are written as \x2d)
    return (_unescape(body), _unescape(instance)) if body else None


class DesktopIndex:
    """Finds an app's .desktop file by its id and remembers the answer."""

    def __init__(self, dirs=None):
        if dirs is None:
            home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
            rest = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
            dirs = [home] + [d for d in rest.split(":") if d]
            for extra in ("/var/lib/flatpak/exports/share", home + "/flatpak/exports/share"):
                if extra not in dirs:
                    dirs.append(extra)
        self.dirs = [Path(d) / "applications" for d in dirs]
        self._cache = {}

    def lookup(self, app_id: str):
        """{'name', 'icon'} of the app, or None if there is no such .desktop file."""
        if app_id not in self._cache:
            found = None
            for d in self.dirs:
                entry = desktopfile.load(d / f"{app_id}.desktop")
                if entry.get("Name"):
                    found = {"name": desktopfile.localized(entry, "Name"), "icon": entry.get("Icon", "")}
                    break
            self._cache[app_id] = found
        return self._cache[app_id]


@dataclass
class Row:
    key: str                    # "app:<cgroup>", "pid:<pid>:<start>" or "kernel"
    section: str                # app | background | system
    name: str
    icon: str = ""
    cpu: float = 0.0
    mem: int = 0
    disk: float = 0.0
    procs: list = field(default_factory=list)       # the processes behind this row
    children: list = field(default_factory=list)    # rows of an app's processes
    status: str = ""

    @property
    def targets(self) -> list:
        return [p.key for p in self.procs]


def display_name(proc) -> str:
    known = KNOWN.get(proc.name)
    return f"{known} ({proc.name.strip('()')})" if known else proc.name


def _proc_row(proc, section: str) -> Row:
    return Row(f"pid:{proc.pid}:{proc.start}", section, display_name(proc), "", proc.cpu, proc.mem,
               proc.disk, [proc], status="" if proc.status == "Running" else proc.status)


def build_rows(procs, me: int, index: DesktopIndex) -> list:
    """Rows of the Processes tab, unsorted."""
    rows, groups, kernel = [], {}, []
    for p in procs:
        if p.kernel:
            kernel.append(p)
            continue
        if p.uid != me:
            rows.append(_proc_row(p, "system"))
            continue
        parsed = parse_app_unit(unit_of(p.cgroup))
        app = index.lookup(parsed[0]) if parsed and parsed[1] != "autostart" else None
        if not app:
            rows.append(_proc_row(p, "background"))
            continue
        row = groups.get(p.cgroup)
        if row is None:
            row = groups[p.cgroup] = Row(f"app:{p.cgroup}", "app", app["name"], app["icon"])
            row.app_id = parsed[0]
            rows.append(row)
        row.procs.append(p)
    for row in groups.values():
        row.cpu = sum(p.cpu for p in row.procs)
        row.mem = sum(p.mem for p in row.procs)
        row.disk = sum(p.disk for p in row.procs)
        if row.app_id == EXE_RUNNER_ID:     # name a Windows program after its .exe
            exes = [p.name for p in row.procs if p.name.lower().endswith(".exe")]
            own = [name for name in exes if name.lower() not in WINE_HELPERS]
            if own or exes:
                row.name = f"{(own or exes)[0]} (Windows program)"
        if len(row.procs) > 1:
            row.children = [_proc_row(p, "app") for p in row.procs]
        if any(p.state in "Tt" for p in row.procs) and all(p.state in "TtZ" for p in row.procs):
            row.status = "Suspended"
    if kernel:
        rows.append(Row("kernel", "system", "Linux kernel", "", sum(p.cpu for p in kernel), 0, 0.0,
                        kernel))
    return rows


def protection(proc, me: int) -> str:
    """How dangerous ending this process is:
    '' (fine), 'restart' (the desktop: restart it instead), 'session' (logs you out),
    'system' (another user's or the system's process), 'never' (the PC stops working)."""
    if proc.kernel or proc.pid in (1, 2):
        return "never"
    if proc.uid != me:
        return "system"
    if proc.name == "plasmashell":
        return "restart"
    if proc.name in ENDS_SESSION:
        return "session"
    return ""
