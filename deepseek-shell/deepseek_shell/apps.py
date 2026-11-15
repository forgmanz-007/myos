"""Finding, installing and pinning apps, and making shortcuts."""
import configparser
import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.parse
from pathlib import Path

from . import scripts

FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"
FLATPAK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")
DEB_NAME = re.compile(r"^[a-z0-9][a-z0-9.+-]+$")

# Everyday names -> where the app comes from. Windows names map to the closest equivalent.
KNOWN = [
    (("google chrome", "chrome"), "flathub", "com.google.Chrome", "Google Chrome"),
    (("brave", "brave browser"), "flathub", "com.brave.Browser", "Brave"),
    (("chromium",), "flathub", "org.chromium.Chromium", "Chromium"),
    (("discord",), "flathub", "com.discordapp.Discord", "Discord"),
    (("spotify",), "flathub", "com.spotify.Client", "Spotify"),
    (("vlc", "vlc media player", "video player", "media player"), "flathub", "org.videolan.VLC", "VLC"),
    (("libreoffice", "office", "microsoft office", "word", "excel", "powerpoint"), "flathub",
     "org.libreoffice.LibreOffice", "LibreOffice (works with Word, Excel and PowerPoint files)"),
    (("onlyoffice",), "flathub", "org.onlyoffice.desktopeditors", "ONLYOFFICE"),
    (("gimp", "photoshop", "photo editor"), "flathub", "org.gimp.GIMP", "GIMP"),
    (("krita", "drawing app"), "flathub", "org.kde.krita", "Krita"),
    (("inkscape", "illustrator"), "flathub", "org.inkscape.Inkscape", "Inkscape"),
    (("obs", "obs studio", "screen recorder"), "flathub", "com.obsproject.Studio", "OBS Studio"),
    (("kdenlive", "video editor"), "flathub", "org.kde.kdenlive", "Kdenlive"),
    (("audacity", "audio editor"), "flathub", "org.audacityteam.Audacity", "Audacity"),
    (("handbrake",), "flathub", "fr.handbrake.ghb", "HandBrake"),
    (("blender",), "flathub", "org.blender.Blender", "Blender"),
    (("vs code", "vscode", "visual studio code"), "flathub", "com.visualstudio.code", "Visual Studio Code"),
    (("telegram",), "flathub", "org.telegram.desktop", "Telegram"),
    (("signal",), "flathub", "org.signal.Signal", "Signal"),
    (("zoom",), "flathub", "us.zoom.Zoom", "Zoom"),
    (("slack",), "flathub", "com.slack.Slack", "Slack"),
    (("thunderbird", "email app", "mail app", "outlook"), "flathub", "org.mozilla.Thunderbird", "Thunderbird"),
    (("steam",), "flathub", "com.valvesoftware.Steam", "Steam"),
    (("heroic", "epic games", "epic games launcher", "gog"), "flathub",
     "com.heroicgameslauncher.hgl", "Heroic Games Launcher"),
    (("lutris",), "flathub", "net.lutris.Lutris", "Lutris"),
    (("bottles",), "flathub", "com.usebottles.bottles", "Bottles"),
    (("prism launcher",), "flathub", "org.prismlauncher.PrismLauncher", "Prism Launcher"),
    (("obsidian",), "flathub", "md.obsidian.Obsidian", "Obsidian"),
    (("qbittorrent",), "flathub", "org.qbittorrent.qBittorrent", "qBittorrent"),
    (("file explorer", "file manager", "explorer", "dolphin"), "debian", "dolphin", "File Explorer (Dolphin)"),
    (("terminal", "command prompt", "cmd", "konsole"), "debian", "konsole", "Terminal (Konsole)"),
    (("snipping tool", "screenshot tool", "screenshots", "spectacle"), "debian", "kde-spectacle",
     "Snipping Tool (Spectacle)"),
    (("system monitor", "kde system monitor"), "debian", "plasma-systemmonitor", "System Monitor"),
    (("notepad", "text editor", "kate"), "debian", "kate", "Notepad (Kate)"),
    (("calculator", "kcalc"), "debian", "kcalc", "Calculator"),
    (("photos", "image viewer", "gwenview"), "debian", "gwenview", "Photos (Gwenview)"),
    (("pdf reader", "okular"), "debian", "okular", "PDF reader (Okular)"),
    (("7-zip", "7zip", "winrar", "zip", "archive manager", "ark"), "debian", "ark", "Zip files (Ark)"),
    (("paint", "ms paint", "kolourpaint"), "debian", "kolourpaint", "Paint (KolourPaint)"),
]


def _norm(text: str) -> str:
    t = re.sub(r"[^a-z0-9+.# -]", " ", str(text).lower())
    t = re.sub(r"\b(the|an?|app|application|program|software)\b", " ", t)
    return " ".join(t.split())


def resolve_known(query: str):
    q = _norm(query)
    for names, source, ident, display in KNOWN:
        if q in names or q == ident.lower():
            return {"source": source, "id": ident, "name": display}
    return None


def _run(argv, timeout: int = 60):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "", str(e)


# ── installed apps (.desktop files) ──────────────────────────────────────────
def application_dirs() -> list:
    home = Path.home()
    dirs = [Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "applications"]
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d:
            dirs.append(Path(d) / "applications")
    dirs += [home / ".local/share/flatpak/exports/share/applications",
             Path("/var/lib/flatpak/exports/share/applications")]
    unique = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def desktop_entries() -> dict:
    """desktop file id -> path; earlier folders win (like the Start menu does)."""
    found = {}
    for d in application_dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.desktop")):
            did = str(f.relative_to(d)).replace(os.sep, "-")
            found.setdefault(did, f)
    return found


def read_entry(path) -> dict:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(path, encoding="utf-8")
        return dict(cp["Desktop Entry"])
    except (configparser.Error, KeyError, UnicodeDecodeError, OSError):
        return {}


def known_desktop_ids(name: str) -> list:
    """Start menu entries of an app known by an everyday name: "Notepad" is installed
    as the Debian package kate, whose entry is org.kde.kate.desktop (named "Kate")."""
    known = resolve_known(name)
    if not known:
        return []
    if known["source"] == "flathub":
        return [known["id"] + ".desktop"]
    code, out, _ = _run(["dpkg-query", "-L", known["id"]], timeout=15)
    if code != 0:
        return []
    return [os.path.basename(f) for f in out.splitlines()
            if f.startswith("/usr/share/applications/") and f.endswith(".desktop")]


def find_app(name: str):
    """Returns (desktop id, path, entry) for an installed app, or None."""
    wanted = str(name).lower().strip().removesuffix(".desktop")
    if not wanted:
        return None
    entries = desktop_entries()
    # The names install_app understands come first, so that right after
    # "Installed Notepad (Kate)" the AI also finds it as Notepad (found in the test VM).
    for did in known_desktop_ids(wanted):
        path = entries.get(did)
        entry = read_entry(path) if path else {}
        if entry.get("exec"):
            return did, path, entry
    best, best_rank = None, 99
    for did, path in entries.items():
        entry = read_entry(path)
        if entry.get("type", "Application") != "Application" or not entry.get("exec"):
            continue
        hidden = "true" in (entry.get("nodisplay", "").lower(), entry.get("hidden", "").lower())
        names = {did.lower().removesuffix(".desktop"), entry.get("name", "").lower(),
                 entry.get("genericname", "").lower()}
        names.discard("")
        if wanted in names:
            rank = 0 if not hidden else 2
        elif any(wanted in n for n in names):
            rank = 1 if not hidden else 3
        else:
            continue
        if rank < best_rank:
            best, best_rank = (did, path, entry), rank
    return best


def installed_by_name(name: str):
    """(desktop id, name) of a Start menu app called exactly this, else None."""
    wanted = str(name).lower().strip()
    if not wanted:
        return None
    for did, path in desktop_entries().items():
        entry = read_entry(path)
        if entry.get("type", "Application") != "Application" or not entry.get("exec"):
            continue
        if "true" in (entry.get("nodisplay", "").lower(), entry.get("hidden", "").lower()):
            continue
        if entry.get("name", "").lower() == wanted:
            return did, entry["name"]
    return None


# ── Flatpak and Debian packages ──────────────────────────────────────────────
def flatpak_available() -> bool:
    return shutil.which("flatpak") is not None


def ensure_user_flathub() -> str:
    """Make sure the per-user Flathub remote exists. Returns an error text or ''."""
    code, out, err = _run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                           FLATHUB_REPO], timeout=180)
    return "" if code == 0 else (err or out).strip() or "unknown error"


def flatpak_installed(app_id: str) -> str:
    for scope in ("--user", "--system"):
        if _run(["flatpak", "info", scope, app_id], timeout=30)[0] == 0:
            return scope.strip("-")
    return ""


def _parse_search(text: str) -> list:
    results = []
    for line in text.splitlines():
        cols = [c.strip() for c in line.split("\t")]
        if len(cols) >= 2 and FLATPAK_ID.match(cols[0]):
            results.append((cols[0], cols[1], cols[2] if len(cols) > 2 else ""))
    return results


def search_flathub(query: str, limit: int = 8) -> list:
    """(app id, name, summary) for apps on Flathub that match."""
    argv = ["flatpak", "search", "--user", "--columns=application,name,description", query]
    code, out, _ = _run(argv, timeout=90)
    results = _parse_search(out) if code == 0 else []
    if not results:  # the app list may not be downloaded yet
        _run(["flatpak", "update", "--user", "--appstream", "flathub"], timeout=300)
        code, out, _ = _run(argv, timeout=90)
        results = _parse_search(out) if code == 0 else []
    return results[:limit]


def pick_match(query: str, results: list):
    """The one result that clearly matches, as (app id, name), else None."""
    q = query.lower().strip()
    for test in (lambda r: r[1].lower() == q, lambda r: r[1].lower().startswith(q),
                 lambda r: q in r[0].lower()):
        hits = [r for r in results if test(r)]
        if len(hits) == 1:
            return hits[0][0], hits[0][1]
    if len(results) == 1:
        return results[0][0], results[0][1]
    return None


def flatpak_install_command(app_id: str) -> str:
    return f"flatpak install --user -y --noninteractive flathub {shlex.quote(app_id)}"


def deb_installed(package: str) -> bool:
    code, out, _ = _run(["dpkg-query", "-W", "-f=${Status}", package], timeout=15)
    return code == 0 and "install ok installed" in out


# ── shortcuts ────────────────────────────────────────────────────────────────
WHERE_ALIASES = {"start": "start_menu", "menu": "start_menu", "startmenu": "start_menu",
                 "start_menu": "start_menu", "pin": "taskbar", "task_bar": "taskbar",
                 "taskbar": "taskbar", "autostart": "startup", "login": "startup",
                 "at_login": "startup", "startup": "startup", "desktop": "desktop"}

PIN_SCRIPT = """
var id = %s;
panels().forEach(function (panel) {
    panel.widgets("org.kde.plasma.icontasks").forEach(function (tasks) {
        tasks.currentConfigGroup = ["General"];
        var list = tasks.readConfig("launchers", []);
        if (typeof list === "string") { list = list ? list.split(",") : []; }
        if (list.indexOf(id) < 0) {
            list.push(id);
            tasks.writeConfig("launchers", list);
            tasks.reloadConfig();
        }
    });
});
"""


def desktop_dir() -> Path:
    code, out, _ = _run(["xdg-user-dir", "DESKTOP"], timeout=5)
    path = out.strip() if code == 0 else ""
    return Path(path) if path and path != str(Path.home()) else Path.home() / "Desktop"


def pin_to_taskbar(desktop_id: str) -> str:
    """Pin an app (by .desktop id) to the taskbar. Returns an error text or ''."""
    if not shutil.which("busctl"):
        return "busctl is not available"
    script = PIN_SCRIPT % json.dumps("applications:" + desktop_id)
    code, out, err = _run(["busctl", "--user", "call", "org.kde.plasmashell", "/PlasmaShell",
                           "org.kde.PlasmaShell", "evaluateScript", "s", script], timeout=20)
    return "" if code == 0 else (err or out).strip() or "the taskbar did not respond"


def _pin_message(name: str, desktop_id: str) -> str:
    err = pin_to_taskbar(desktop_id)
    if not err:
        return f"Pinned {name} to the taskbar."
    return (f"Couldn't pin {name} automatically ({err}). The user can right-click it in the "
            "Start menu and choose 'Pin to Task Manager'.")


def _app_shortcut(found, where: str) -> str:
    did, path, entry = found
    name = entry.get("name") or did
    if where == "start_menu":
        return f"{name} is already in the Start menu."
    if where == "taskbar":
        return _pin_message(name, did)
    folder = desktop_dir() if where == "desktop" else scripts.autostart_dir()
    dest = folder / did
    scripts.write_text(dest, Path(path).read_text(encoding="utf-8", errors="replace"),
                       0o755 if where == "desktop" else 0o644)
    if where == "desktop":
        return f"Added a desktop shortcut to {name}."
    return f"{name} now opens automatically when the user logs in. (To undo, delete {dest}.)"


def _launcher_entry(slug: str, name: str, exec_line: str, icon: str, comment: str) -> str:
    return "\n".join(["[Desktop Entry]", "Type=Application", f"Name={name}",
                      f"Comment={comment}", f"Exec={exec_line}", f"Icon={icon}",
                      "Terminal=false", "Categories=Utility;",
                      f"X-DeepSeek-Shell-Shortcut={slug}"]) + "\n"


def _open_target(kind: str, value: str, name: str, slug: str, where: str, icon: str) -> str:
    """Shortcut that opens a web address or a file/folder with its default app."""
    if where == "desktop":
        url = value if kind == "url" else Path(value).as_uri()
        text = "\n".join(["[Desktop Entry]", "Type=Link", f"Name={name}", f"URL={url}",
                          f"Icon={icon}"]) + "\n"
        scripts.write_text(desktop_dir() / f"{slug}.desktop", text, 0o755)
        return f"Added a desktop shortcut '{name}'."
    exec_line = f"xdg-open {scripts.desktop_exec_arg(value)}"
    if where == "startup":
        scripts.write_text(scripts.autostart_dir() / f"myshortcut-{slug}.desktop",
                           scripts.autostart_entry_text(slug, exec_line, f"Opens {name}"))
        return f"{name} now opens automatically when the user logs in."
    did = f"myshortcut-{slug}.desktop"
    scripts.write_text(scripts.applications_dir() / did,
                       _launcher_entry(slug, name, exec_line, icon, f"Opens {value}"))
    if where == "start_menu":
        return f"Added '{name}' to the Start menu."
    return _pin_message(name, did)


def _script_shortcut(path: Path, where: str) -> str:
    slug = scripts.slugify(path.stem)
    name = scripts.pretty_name(slug)
    if where == "startup":
        scripts.write_text(scripts.autostart_dir() / f"{scripts.PREFIX}{slug}.desktop",
                           scripts.autostart_entry_text(slug, scripts.desktop_exec_arg(str(path)), ""))
        return f"The script {name} now runs automatically when the user logs in."
    entry = scripts.menu_entry_text(slug, path, f"Runs {path.name}")
    did = f"{scripts.PREFIX}{slug}.desktop"
    menu = scripts.applications_dir() / did
    if where == "desktop":
        scripts.write_text(desktop_dir() / did, entry, 0o755)
        return f"Added a desktop shortcut that runs {name}."
    scripts.write_text(menu, entry)
    if where == "start_menu":
        return f"Added '{name}' to the Start menu."
    return _pin_message(name, did)


def _is_script(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix in (".sh", ".py") and path.parent == scripts.scripts_dir():
        return True
    try:
        with open(path, "rb") as f:
            starts_like_script = f.read(2) == b"#!"
    except OSError:
        return False
    return starts_like_script and os.access(path, os.X_OK)


def make_shortcut(target: str, where: str, label: str = "") -> str:
    where = WHERE_ALIASES.get(str(where).strip().lower().replace(" ", "_").replace("-", "_"), "")
    if not where:
        raise ValueError("'where' must be desktop, start_menu, taskbar or startup.")
    target = str(target).strip()
    if not target:
        raise ValueError("Say what the shortcut should open.")
    if re.match(r"^https?://", target, re.I):
        host = urllib.parse.urlparse(target).netloc.removeprefix("www.")
        name = label.strip() or host or "Website"
        return _open_target("url", target, name, scripts.slugify(name), where, "internet-web-browser")
    path = Path(os.path.expandvars(os.path.expanduser(target)))
    if path.is_absolute() and path.exists():
        if _is_script(path):
            return _script_shortcut(path, where)
        name = label.strip() or path.name or str(path)
        icon = "folder" if path.is_dir() else "text-x-generic"
        return _open_target("path", str(path), name, scripts.slugify(name), where, icon)
    found = find_app(target)
    if found:
        return _app_shortcut(found, where)
    try:
        slug = scripts.slugify(target)
    except ValueError:
        slug = ""
    for ext in (".sh", ".py"):
        candidate = scripts.scripts_dir() / f"{slug}{ext}"
        if slug and candidate.exists():
            return _script_shortcut(candidate, where)
    raise ValueError(f"I couldn't find an app, script or file called '{target}'. Install it first "
                     "(install_app), or give the full path or web address.")
