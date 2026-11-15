"""Scripts the AI writes for you.

They are saved in ~/Scripts and made runnable. Optionally they get:
  * a Start menu entry, which runs the script in a small window that shows its output;
  * a schedule: a systemd user timer (the Linux "Task Scheduler") or "at login".
"""
import datetime
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "myscript-"
DAYS = {"monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
        "friday": "Fri", "saturday": "Sat", "sunday": "Sun"}


# ── places ───────────────────────────────────────────────────────────────────
def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


def scripts_dir() -> Path:
    custom = os.environ.get("DEEPSEEK_SHELL_SCRIPTS_DIR")
    return Path(custom) if custom else Path.home() / "Scripts"


def applications_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "applications"


def autostart_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart"


def units_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "systemd" / "user"


def logs_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "deepseek-shell" / "scripts"


# ── names and content ────────────────────────────────────────────────────────
def slugify(name: str) -> str:
    s = re.sub(r"\.(sh|bash|py)$", "", str(name).strip().lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-")
    if not s:
        raise ValueError("Give the script a short name, for example backup-photos.")
    return s


def pretty_name(slug: str) -> str:
    words = slug.replace("-", " ").strip()
    return words[:1].upper() + words[1:]


def one_line(text, limit: int = 150) -> str:
    return " ".join(str(text or "").split())[:limit]


def detect_language(content: str):
    """Returns (language, shebang to add, or '' when the content has one)."""
    text = content.lstrip("﻿").lstrip()
    first = text.splitlines()[0] if text else ""
    if first.startswith("#!"):
        return ("python" if "python" in first else "bash"), ""
    if re.search(r"^\s*(import \w|from [\w.]+ import |def \w+\(|class \w+|print\(|if __name__)",
                 content, re.M):
        return "python", "#!/usr/bin/env python3"
    return "bash", "#!/bin/bash"


def target_path(slug: str, content: str) -> Path:
    language, _ = detect_language(content)
    return scripts_dir() / f"{slug}{'.py' if language == 'python' else '.sh'}"


def with_header(content: str, description: str, path: Path) -> str:
    """Add a shebang when missing, and a comment saying what the script is."""
    _language, shebang = detect_language(content)
    lines = content.lstrip("﻿").lstrip("\n").splitlines()
    if shebang:
        lines.insert(0, shebang)
    if not any("Made with DeepSeek Shell" in line for line in lines[:6]):
        header = [f"# {one_line(description, 200)}"] if description else []
        header.append(f"# Made with DeepSeek Shell on {datetime.date.today().isoformat()}. "
                      f"Run it from the Start menu or with: {path}")
        lines = lines[:1] + header + lines[1:]
    return "\n".join(lines).rstrip() + "\n"


def syntax_check(path: Path, language: str) -> str:
    """Empty string when the script has no syntax errors."""
    if language == "python":
        argv = [sys.executable or "python3", "-c",
                "import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1])",
                str(path)]
    else:
        argv = ["bash", "-n", str(path)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    if r.returncode == 0:
        return ""
    return ((r.stderr or r.stdout).strip() or "syntax error").replace(str(path), path.name)


# ── schedules ────────────────────────────────────────────────────────────────
@dataclass
class Schedule:
    kind: str                       # timer | login | remove
    label: str = ""
    timer: dict = field(default_factory=dict)


def _time_of_day(t: str):
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?m\b", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        if not 1 <= hh <= 12 or mm > 59:
            raise ValueError("That time of day doesn't exist.")
        return hh % 12 + (12 if m.group(3) == "p" else 0), mm
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\b", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            raise ValueError("That time of day doesn't exist.")
        return hh, mm
    m = re.search(r"\bat\s+(\d{1,2})\b", t)
    if m:
        if int(m.group(1)) > 23:
            raise ValueError("That time of day doesn't exist.")
        return int(m.group(1)), 0
    for word, value in (("noon", (12, 0)), ("midday", (12, 0)), ("midnight", (0, 0)),
                        ("morning", (9, 0)), ("evening", (19, 0)), ("night", (22, 0))):
        if word in t:
            return value
    return None


_SIMPLE = {
    "hourly": ("hourly", "every hour"), "every hour": ("hourly", "every hour"),
    "each hour": ("hourly", "every hour"), "once an hour": ("hourly", "every hour"),
    "daily": ("daily", "every day at midnight (or as soon as the PC is on)"),
    "every day": ("daily", "every day at midnight (or as soon as the PC is on)"),
    "once a day": ("daily", "every day at midnight (or as soon as the PC is on)"),
    "weekly": ("weekly", "every Monday at midnight (or as soon as the PC is on)"),
    "every week": ("weekly", "every Monday at midnight (or as soon as the PC is on)"),
    "once a week": ("weekly", "every Monday at midnight (or as soon as the PC is on)"),
    "monthly": ("monthly", "on the 1st of every month"),
    "every month": ("monthly", "on the 1st of every month"),
    "once a month": ("monthly", "on the 1st of every month"),
}


def parse_schedule(text):
    """Turn 'daily at 18:30', 'every 15 minutes', 'at login'... into a Schedule.
    Returns None when no schedule was given. Raises ValueError when unclear."""
    t = " ".join(str(text or "").lower().replace(",", " ").split())
    if not t:
        return None
    if t in ("none", "no", "never", "off", "manual", "manually", "remove", "false", "no schedule"):
        return Schedule("remove", "not scheduled")
    if re.search(r"\b(log ?in|logon|log on|sign ?in|start ?up|boot|turn on|starts)\b", t):
        return Schedule("login", "every time you log in")
    m = re.search(r"\bevery\s+(\d+)\s*(minutes?|mins?|m)\b", t)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 1440:
            raise ValueError("Choose between 1 and 1440 minutes.")
        return Schedule("timer", f"every {n} minute{'s' * (n != 1)}",
                        {"OnBootSec": f"{min(n, 5)}min", "OnUnitActiveSec": f"{n}min"})
    m = re.search(r"\bevery\s+(\d+)\s*(hours?|hrs?|h)\b", t)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 168:
            raise ValueError("Choose between 1 and 168 hours.")
        return Schedule("timer", f"every {n} hour{'s' * (n != 1)}",
                        {"OnBootSec": "5min", "OnUnitActiveSec": f"{n}h"})
    if t in _SIMPLE:
        calendar, label = _SIMPLE[t]
        return Schedule("timer", label, {"OnCalendar": calendar, "Persistent": "true"})
    hhmm = _time_of_day(t)
    day = next((name for name in DAYS if re.search(rf"\b{name}s?\b", t)), None)
    weekdays = bool(re.search(r"\b(weekdays?|workdays?|work days?|monday to friday|mon-fri)\b", t))
    weekend = bool(re.search(r"\bweekends?\b", t))
    if hhmm or day or weekdays or weekend:
        hh, mm = hhmm or (9, 0)
        at = f"{hh:02d}:{mm:02d}"
        if weekdays:
            return Schedule("timer", f"on weekdays at {at}",
                            {"OnCalendar": f"Mon..Fri *-*-* {at}:00", "Persistent": "true"})
        if weekend:
            return Schedule("timer", f"on weekends at {at}",
                            {"OnCalendar": f"Sat,Sun *-*-* {at}:00", "Persistent": "true"})
        if day:
            return Schedule("timer", f"every {day.capitalize()} at {at}",
                            {"OnCalendar": f"{DAYS[day]} *-*-* {at}:00", "Persistent": "true"})
        return Schedule("timer", f"every day at {at}",
                        {"OnCalendar": f"*-*-* {at}:00", "Persistent": "true"})
    raw = str(text).strip()
    if shutil.which("systemd-analyze") and re.fullmatch(r"[A-Za-z0-9 *:.,/~-]+", raw):
        try:
            r = subprocess.run(["systemd-analyze", "calendar", raw], capture_output=True,
                               text=True, timeout=10)
            if r.returncode == 0:
                return Schedule("timer", raw, {"OnCalendar": raw, "Persistent": "true"})
        except (OSError, subprocess.SubprocessError):
            pass
    raise ValueError("I couldn't understand the schedule. Examples: 'daily at 18:30', "
                     "'every 15 minutes', 'hourly', 'every Monday at 9:00', 'weekdays at 8am', "
                     "'at login'.")


# ── unit and desktop files ───────────────────────────────────────────────────
def systemd_arg(text: str) -> str:
    return '"' + (text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
                  .replace("$", "$$")) + '"'


def desktop_exec_arg(text: str) -> str:
    if re.search(r'["`$\\\n]', text):
        raise ValueError("That path contains characters that can't be used in a shortcut.")
    text = text.replace("%", "%%")
    return f'"{text}"' if re.search(r"[\s'<>~|&;*?#()=]", text) else text


def unit_texts(slug: str, path: Path, description: str, schedule: Schedule):
    log = str(logs_dir() / f"{slug}.log").replace("%", "%%")
    desc = (one_line(description) or pretty_name(slug)).replace("%", "%%")
    service = (
        "[Unit]\n"
        f"Description={desc} (script made with DeepSeek Shell)\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={systemd_arg(str(path))}\n"
        f"StandardOutput=append:{log}\n"
        "StandardError=inherit\n"
        "ExecStopPost=/bin/sh -c '[ \"$$SERVICE_RESULT\" = success ] || notify-send "
        f"-a \"My scripts\" -i dialog-warning \"Script {slug} failed\" "
        "\"Ask DeepSeek Shell to fix it.\"'\n")
    timer = (
        "[Unit]\n"
        f"Description=When to run {slug}: {schedule.label.replace('%', '%%')}\n\n"
        "[Timer]\n" + "".join(f"{k}={v}\n" for k, v in schedule.timer.items()) +
        "\n[Install]\nWantedBy=timers.target\n")
    return service, timer


def menu_entry_text(slug: str, path: Path, description: str) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={pretty_name(slug)}",
        f"Comment={one_line(description) or 'Script made with DeepSeek Shell'}",
        f"Exec=deepseek-shell --run-script {desktop_exec_arg(str(path))}",
        "Icon=text-x-script",
        "Terminal=false",
        "Categories=Utility;",
        "Keywords=script;my scripts;",
        f"X-DeepSeek-Shell-Script={slug}",
    ]) + "\n"


def autostart_entry_text(slug: str, exec_line: str, description: str) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={pretty_name(slug)}",
        f"Comment={one_line(description) or 'Started at login by DeepSeek Shell'}",
        f"Exec={exec_line}",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
    ]) + "\n"


def write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def _run(argv, timeout: int = 30):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def remove_schedule(slug: str) -> None:
    timer = units_dir() / f"{PREFIX}{slug}.timer"
    service = units_dir() / f"{PREFIX}{slug}.service"
    had_units = timer.exists() or service.exists()
    if timer.exists() and shutil.which("systemctl"):
        _run(["systemctl", "--user", "disable", "--now", timer.name])
    for f in (timer, service, autostart_dir() / f"{PREFIX}{slug}.desktop"):
        if f.exists():
            f.unlink()
    if had_units and shutil.which("systemctl"):
        _run(["systemctl", "--user", "daemon-reload"])


def next_run(slug: str) -> str:
    code, out = _run(["systemctl", "--user", "list-timers", "--no-legend", f"{PREFIX}{slug}.timer"])
    if code != 0 or not out.strip():
        return ""
    words = out.splitlines()[0].split()
    return " ".join(words[:3]) if len(words) >= 3 and words[0] != "n/a" else ""


def install_schedule(slug: str, path: Path, description: str, schedule: Schedule):
    """Returns (ok, message)."""
    remove_schedule(slug)
    if schedule.kind == "remove":
        return True, "It no longer runs automatically."
    if schedule.kind == "login":
        write_text(autostart_dir() / f"{PREFIX}{slug}.desktop",
                   autostart_entry_text(slug, desktop_exec_arg(str(path)), description))
        return True, "It runs automatically every time you log in."
    service, timer = unit_texts(slug, path, description, schedule)
    logs_dir().mkdir(parents=True, exist_ok=True)
    write_text(units_dir() / f"{PREFIX}{slug}.service", service)
    write_text(units_dir() / f"{PREFIX}{slug}.timer", timer)
    if not shutil.which("systemctl"):
        return False, "The schedule is saved, but systemctl is missing, so it couldn't be switched on."
    _run(["systemctl", "--user", "daemon-reload"])
    code, out = _run(["systemctl", "--user", "enable", "--now", f"{PREFIX}{slug}.timer"])
    if code != 0:
        return False, f"The schedule is saved but couldn't be switched on: {out}"
    when = next_run(slug)
    return True, f"It runs automatically {schedule.label}." + (f" Next run: {when}." if when else "")


# ── saving ───────────────────────────────────────────────────────────────────
@dataclass
class SaveResult:
    slug: str
    path: Path
    language: str
    syntax_error: str = ""
    backup: str = ""
    menu_entry: str = ""
    schedule_ok: bool = True
    schedule_message: str = ""
    scheduled: bool = False


def save_script(name: str, content: str, description: str = "", start_menu: bool = False,
                schedule=None) -> SaveResult:
    slug = slugify(name)
    path = target_path(slug, content)
    language, _ = detect_language(content)
    res = SaveResult(slug, path, language)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
        res.backup = str(backup)
    write_text(path, with_header(content, description, path), 0o755)
    res.syntax_error = syntax_check(path, language)
    if res.syntax_error:
        return res
    menu = applications_dir() / f"{PREFIX}{slug}.desktop"
    if start_menu or menu.exists():  # keep an existing entry pointing at the new file
        write_text(menu, menu_entry_text(slug, path, description))
        res.menu_entry = str(menu)
    if schedule is not None:
        res.schedule_ok, res.schedule_message = install_schedule(slug, path, description, schedule)
        res.scheduled = schedule.kind == "timer" and res.schedule_ok
    return res


def describe(res: SaveResult) -> str:
    """What happened, written for the AI."""
    if res.syntax_error:
        return (f"Saved {res.path}, but the script has a mistake, so it was NOT added to the menu "
                f"or scheduled. Fix it and call create_script again with the same name:\n"
                f"{res.syntax_error}")
    parts = [f"Saved {res.path} ({res.language}), made it runnable, and checked it: no syntax errors."]
    if res.backup:
        parts.append(f"The previous version is kept as {res.backup}.")
    if res.menu_entry:
        parts.append(f"Start menu entry: '{pretty_name(res.slug)}' (shows the output in a window).")
    if res.schedule_message:
        parts.append(res.schedule_message)
    if res.scheduled:
        parts.append(f"Output of the automatic runs goes to {logs_dir() / (res.slug + '.log')}. "
                     "All schedules: systemctl --user list-timers.")
    parts.append(f"To test it now, run: {res.path}")
    return " ".join(parts)
