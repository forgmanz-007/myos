"""Tools the AI can use: built-in ones plus custom tools that the user adds
in Settings > Tools (stored as JSON files).

Custom tool file (~/.config/deepseek-shell/tools/<name>.json):
{
  "name": "weather",
  "description": "Get the weather forecast for a city",
  "command": "curl -s --max-time 15 \"https://wttr.in/${CITY// /+}?format=3\"",
  "parameters": [
    {"name": "city", "type": "string", "description": "City name", "required": true}
  ],
  "ask_before_running": false,
  "run_as_admin": false,
  "timeout": 60,
  "enabled": true
}
Parameter values are available in the command as {name} (inserted already
safely quoted, so do not put quotes around it) and as the environment
variable $NAME (use it inside double quotes).
"""
import datetime
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import apps, hardware, paths, safety, scripts
from . import config as cfg
from .runner import (PKEXEC_DISMISSED, PKEXEC_NOT_AUTHORIZED, CommandRunner, clean_output,
                     truncate_middle)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
PARAM_TYPES = ("string", "integer", "number", "boolean")
MAX_RESULT = 8000


class ToolError(Exception):
    """A problem the AI should read and react to."""


@dataclass
class ApprovalRequest:
    tool: str
    title: str
    detail: str
    level: str = safety.CHANGE
    reason: str = ""
    admin: bool = False
    warning: str = ""
    allow_always: bool = False      # offer "Always allow this tool" (custom tools)
    allow_task: bool = True         # offer "Allow all for this task"
    kind: str = "action"            # action | plan
    decision: str = ""              # allow | task | always | deny
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    def respond(self, decision: str) -> None:
        self.decision = decision
        self._event.set()

    def wait(self, cancel=None) -> bool:
        while not self._event.wait(0.2):
            if cancel is not None and cancel.is_set():
                self.decision = "deny"
                break
        return self.decision in ("allow", "always", "task")


@dataclass
class ToolContext:
    config: "cfg.Config"
    runner: CommandRunner
    approve: object                      # callable(ApprovalRequest) -> bool
    on_output: object = None             # callable(str) for live command output
    cancel: threading.Event = field(default_factory=threading.Event)
    knowledge: object = None
    trust: dict = field(default_factory=dict)   # {"task": True}: the rest of this job runs without asking
    registry: object = None

    def ask(self, req: ApprovalRequest) -> bool:
        mode = self.config.get("approval", "smart")
        if req.level == safety.DANGER or mode == "always":
            req.allow_task = False          # these are confirmed one by one
        elif self.trust.get("task"):
            return True                     # the user allowed the whole job
        ok = bool(self.approve(req))
        if ok and req.decision == "task" and req.allow_task:
            self.trust["task"] = True
        return ok

    def output(self, text: str) -> None:
        if self.on_output:
            self.on_output(text)


@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    required: list
    func: object
    source: str = "built-in"             # built-in | system | yours
    path: Path = None
    spec: dict = None

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.properties,
                           "required": self.required}}}


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def format_result(code: int, output: str, admin: bool = False) -> str:
    if admin and code in (PKEXEC_DISMISSED, PKEXEC_NOT_AUTHORIZED) and (
            code == PKEXEC_DISMISSED or "Not authorized" in output or "Error executing" in output):
        return ("The administrator password prompt was cancelled or failed, so the command did "
                "NOT run. Tell the user and ask if they want to try again.")
    out = truncate_middle(output.strip(), MAX_RESULT) or "(no output)"
    return f"Exit code: {code}\n{out}"


# ─────────────────────────────── built-in tools ───────────────────────────────

def t_run_command(args, ctx: ToolContext) -> str:
    raw = str(args.get("command", "")).strip()
    if not raw:
        raise ToolError("'command' is empty.")
    command, had_sudo = safety.strip_sudo(raw)
    admin = _truthy(args.get("admin", False)) or had_sudo
    command = safety.ensure_noninteractive(command)
    problem = safety.interactive_problem(command)
    if problem:
        raise ToolError(problem)
    stripped = command.rstrip()
    background = stripped.endswith("&") and not stripped.endswith("&&")
    level, why = safety.classify(command)
    if safety.needs_approval(ctx.config.get("approval", "smart"), level, admin):
        req = ApprovalRequest(
            tool="run_command",
            title="Run a command as administrator" if admin else "Run a command",
            detail=command, level=level, reason=str(args.get("reason", "")), admin=admin,
            warning=f"Careful: this command {why}." if level == safety.DANGER else "")
        if not ctx.ask(req):
            return "The user did NOT allow this command. Ask what they would like to do instead."
    if background and not admin:
        CommandRunner.launch_detached(["bash", "-c", stripped[:-1]])
        return "Started in the background."
    snapshot = command if (admin and level != safety.READ and ctx.config.get("snapshots")) else None
    code, out = ctx.runner.run(command, admin=admin, timeout=int(ctx.config.get("command_timeout", 900)),
                               on_output=ctx.on_output, cancel=ctx.cancel, snapshot_label=snapshot)
    result = format_result(code, out, admin)
    if code != 0 and not admin and _NEEDS_ROOT.search(out or ""):
        # Small models kept retrying such commands in the test VM instead of asking for admin.
        result += ("\nThis needs administrator rights. If the change is really needed, run it again "
                   "with admin=true (the user is asked for their password); otherwise stop here.")
    return result


_NEEDS_ROOT = re.compile(r"Permission denied|Operation not permitted|are you root|must be (run as )?root|"
                         r"requires? (superuser|root)|Could not open lock file", re.I)


def _resolve(path: str) -> Path:
    if not path:
        raise ToolError("'path' is empty.")
    return Path(os.path.expandvars(os.path.expanduser(str(path))))


def t_read_file(args, ctx) -> str:
    p = _resolve(args.get("path", ""))
    start = max(1, int(args.get("start_line", 1) or 1))
    count = min(1000, max(1, int(args.get("max_lines", 300) or 300)))
    if p.is_dir():
        raise ToolError(f"{p} is a folder. Use list_directory.")
    try:
        with open(p, "rb") as f:
            data = f.read(2_000_000)
    except FileNotFoundError:
        raise ToolError(f"{p} does not exist.") from None
    except PermissionError:
        raise ToolError(f"Permission denied for {p}. It needs administrator rights: use "
                        f"run_command with admin=true and the command: cat {shlex.quote(str(p))}") from None
    if b"\x00" in data[:8192]:
        return f"{p} is a binary file ({p.stat().st_size} bytes), not text."
    lines = data.decode("utf-8", "replace").splitlines()
    chunk = lines[start - 1:start - 1 + count]
    more = len(lines) - (start - 1 + len(chunk))
    body = "\n".join(chunk)
    tail = f"\n[{more} more lines; call read_file with start_line={start + len(chunk)}]" if more > 0 else ""
    return f"{p} (lines {start}-{start + len(chunk) - 1} of {len(lines)}):\n{body}{tail}"


def t_write_file(args, ctx) -> str:
    p = _resolve(args.get("path", ""))
    content = str(args.get("content", ""))
    append = _truthy(args.get("append", False))
    admin = _truthy(args.get("admin", False))
    old, unreadable = None, False
    try:
        old = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
    except PermissionError:
        admin, unreadable = True, True
    new = (old or "") + content if append else content
    if unreadable:
        preview = (f"{'Add to' if append else 'Replace'} {p} (only an administrator can read it, so the "
                   f"current content can't be shown). New text:\n" + "\n".join(content.splitlines()[:60]))
    elif old is None:
        preview = f"New file {p}:\n" + "\n".join(content.splitlines()[:60])
    else:
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(), f"{p} (before)",
                                    f"{p} (after)", lineterm="", n=2)
        preview = "\n".join(list(diff)[:120]) or "(no changes)"
    req = ApprovalRequest(tool="write_file", title="Change a file as administrator" if admin
                          else "Change a file", detail=preview, level=safety.CHANGE,
                          reason=str(args.get("reason", "")), admin=admin)
    if safety.needs_approval(ctx.config.get("approval", "smart"), safety.CHANGE, admin):
        if not ctx.ask(req):
            return "The user did NOT allow this change."
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{p}.bak-{stamp}"
    if not admin:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if old is not None:
                shutil.copy2(p, backup)
            with open(p, "a" if append else "w", encoding="utf-8") as f:
                f.write(content)
            return f"Saved {p}." + (f" Backup of the old version: {backup}" if old is not None else "")
        except PermissionError:
            admin = True
    fd, tmp = tempfile.mkstemp(prefix="deepseek-shell-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(tmp, 0o644)
    q, qt, qb = shlex.quote(str(p)), shlex.quote(tmp), shlex.quote(backup)
    op = ">>" if append else ">"
    cmd = (f"set -e; mkdir -p {shlex.quote(str(p.parent))}; "
           f"if [ -e {q} ]; then cp -a {q} {qb}; fi; cat {qt} {op} {q}")
    code, out = ctx.runner.run(cmd, admin=True, timeout=60, cancel=ctx.cancel,
                               snapshot_label=f"edit {p}" if ctx.config.get("snapshots") else None)
    os.unlink(tmp)
    if code != 0:
        return format_result(code, out, admin=True)
    return f"Saved {p} (as administrator). Backup of the old version (if any): {backup}"


def t_list_directory(args, ctx) -> str:
    p = _resolve(args.get("path", "~") or "~")
    hidden = _truthy(args.get("show_hidden", False))
    try:
        entries = sorted(os.scandir(p), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    except FileNotFoundError:
        raise ToolError(f"{p} does not exist.") from None
    except NotADirectoryError:
        raise ToolError(f"{p} is a file. Use read_file.") from None
    except PermissionError:
        raise ToolError(f"Permission denied for {p}.") from None
    rows = []
    for e in entries:
        if not hidden and e.name.startswith("."):
            continue
        try:
            if e.is_symlink():
                rows.append(f"[link] {e.name} -> {os.readlink(e.path)}")
            elif e.is_dir():
                rows.append(f"[folder] {e.name}/")
            else:
                size = e.stat().st_size
                rows.append(f"{e.name}  ({_human(size)})")
        except OSError:
            rows.append(e.name)
        if len(rows) >= 300:
            rows.append("[... more entries not shown]")
            break
    return f"{p}:\n" + ("\n".join(rows) if rows else "(empty)")


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _sh(cmd: str, timeout=12) -> str:
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "LC_ALL": "C.UTF-8", "SYSTEMD_PAGER": ""})
        return clean_output((r.stdout + r.stderr).strip())
    except (OSError, subprocess.SubprocessError):
        return ""


def _diag(what: str, timeout=15) -> str:
    """Privileged read-only logs via the diag helper (no password needed)."""
    helper = os.path.join(str(paths.LIBEXEC_DIR), "diag-helper")
    policy = "/usr/share/polkit-1/actions/org.myos.deepseekshell.policy"
    if not (shutil.which("pkexec") and os.path.exists(helper) and os.path.exists(policy)):
        return _sh(f"journalctl {'-k -b' if what == 'kernel' else '-b -p err'} --no-pager -n 200",
                   timeout)
    try:
        r = subprocess.run(["pkexec", helper, what], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


_IMPORTANT_DEVICES = re.compile(r"VGA|3D|Display|Network|Ethernet|Wireless|Audio|USB controller|"
                                r"SATA|NVMe|Non-Volatile|Bluetooth|Multimedia", re.I)


def devices_without_driver(lspci_k: str) -> list:
    """PCI devices (of the kinds people care about) that have no driver loaded."""
    missing, current, has_driver = [], None, False
    for line in lspci_k.splitlines() + ["END"]:
        if line and not line[0].isspace():
            if current and not has_driver and _IMPORTANT_DEVICES.search(current):
                missing.append(current)
            current, has_driver = line, False
        elif "Kernel driver in use" in line:
            has_driver = True
    return missing


def t_system_report(args, ctx) -> str:
    hw = hardware.detect()
    parts = []
    osr = cfg.os_release()
    parts.append("== System ==")
    parts.append(f"OS: {osr.get('PRETTY_NAME', '?')} (Debian {_sh('cat /etc/debian_version')})")
    parts.append(f"Kernel: {_sh('uname -r')}   Session: {os.environ.get('XDG_SESSION_TYPE', '?')} "
                 f"{os.environ.get('XDG_CURRENT_DESKTOP', '')}")
    sb = _sh("mokutil --sb-state 2>/dev/null")
    if sb:
        parts.append(f"Secure Boot: {sb}")
    parts.append(f"Live USB session: {'yes' if hw.live_session else 'no'}")
    parts.append("== Hardware ==")
    parts.append(hw.summary())
    for g in hw.gpus:
        parts.append(f"GPU {g.name}: kernel driver = {g.driver or 'NONE'}, "
                     f"AI acceleration = {'yes' if g.accelerated else 'no'}")
    parts.append(_sh("df -h / /home 2>/dev/null | awk 'NR==1 || !seen[$1]++'"))
    parts.append("== Network ==")
    parts.append(_sh("nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>/dev/null | head -n 12")
                 or "(nmcli not available)")
    rf = _sh("rfkill list 2>/dev/null | grep -B1 -i 'blocked: yes'")
    if rf:
        parts.append("Radios switched off:\n" + rf)
    parts.append("== Audio ==")
    parts.append(_sh("wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' | head -n 15")
                 or "(no audio info)")
    parts.append("== Problems found ==")
    failed = _sh("systemctl --failed --no-legend --plain 2>/dev/null")
    parts.append("Failed services: " + (failed or "none"))
    user_failed = _sh("systemctl --user --failed --no-legend --plain 2>/dev/null")
    if user_failed:
        parts.append("Failed user services: " + user_failed)
    no_driver = devices_without_driver(_sh("lspci -k 2>/dev/null"))
    if no_driver:
        parts.append("Devices without a driver:\n" + "\n".join(no_driver))
    kernel = _diag("kernel")
    fw = sorted({l.split("]", 1)[-1].strip() for l in kernel.splitlines()
                 if re.search(r"firmware|direct firmware load", l, re.I)
                 and re.search(r"fail|missing|not found|error", l, re.I)})
    parts.append("Missing firmware:\n" + "\n".join(fw[:15]) if fw else "Missing firmware: none seen")
    errors = _diag("errors")
    if errors:
        lines = errors.splitlines()
        parts.append(f"Errors this boot ({len(lines)} lines, last 30):\n" + "\n".join(lines[-30:]))
    parts.append("== AI engine ==")
    parts.append(_sh("systemctl is-active ollama 2>/dev/null") or "?")
    return truncate_middle("\n".join(p for p in parts if p), 9000)


def t_search_fixes(args, ctx) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("'query' is empty.")
    kb = ctx.knowledge
    if kb is None:
        return "No guide library available."
    found = kb.search(query)
    if not found:
        return ("No guide matched. Diagnose step by step with read-only commands, and be careful: "
                "this is Debian 13 (trixie) with KDE Plasma 6.")
    return "\n\n---\n\n".join(truncate_middle(g.text, 4500) for g in found)


def t_find_software(args, ctx) -> str:
    query = str(args.get("query", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9 .+_-]{1,60}", query):
        raise ToolError("Give a short search word, e.g. 'steam' or 'video editor'.")
    q = shlex.quote(query)
    apt = _sh(f"apt-cache search --names-only {q} 2>/dev/null | head -n 15") or "(nothing)"
    flat = "(Flatpak not installed)"
    if shutil.which("flatpak"):
        flat = _sh(f"flatpak search --columns=application,name,description {q} 2>/dev/null "
                   "| head -n 12", timeout=40) or "(nothing; Flathub's index may still be downloading)"
    return (f"Debian packages (install with apt, admin):\n{apt}\n\n"
            f"Flathub apps (install with: flatpak install -y flathub <application id>):\n{flat}")


def t_open_app(args, ctx) -> str:
    target = str(args.get("target", "")).strip()
    if not target:
        raise ToolError("'target' is empty.")
    expanded = os.path.expanduser(target)
    if re.match(r"^[a-z][a-z0-9+.-]*://", target) or os.path.exists(expanded):
        CommandRunner.launch_detached(["xdg-open", expanded])
        return f"Opened {target}."
    found = apps.find_app(target)
    if not found:
        return (f"No installed app called '{target}'. Install it with install_app (that asks the "
                "user first).")
    _did, path, entry = found
    if shutil.which("kioclient"):
        CommandRunner.launch_detached(["kioclient", "exec", str(path)])
    elif shutil.which("gio"):
        CommandRunner.launch_detached(["gio", "launch", str(path)])
    else:
        argv = [a for a in shlex.split(entry["exec"]) if not re.fullmatch(r"%[a-zA-Z]", a)]
        CommandRunner.launch_detached(argv)
    return f"Started {entry.get('name', target)}."


# ─────────────────────────── jobs: plans, apps, scripts ──────────────────────────

def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", line) for line in value.splitlines()]
    return []


def t_propose_plan(args, ctx) -> str:
    steps = [" ".join(str(s).split()) for s in _as_list(args.get("steps"))]
    steps = [s for s in steps if s][:15]
    if not steps:
        raise ToolError("Give 'steps' as a list of short lines.")
    summary = " ".join(str(args.get("summary", "")).split()) or "Do the job you asked for"
    mode = ctx.config.get("approval", "smart")
    if mode == "never" or ctx.trust.get("task"):
        return "No confirmation needed. Go ahead with the plan."
    req = ApprovalRequest(tool="propose_plan", title=summary, kind="plan", level=safety.CHANGE,
                          detail="\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))
    if not ctx.ask(req):
        return "The user did NOT approve the plan. Ask what they would like to change."
    if mode == "always":
        return "The user approved the plan. Each change will still be confirmed one by one."
    return ("The user approved the plan. Do all the steps now without asking again (only "
            "dangerous commands still need a new OK). Check that it worked, then summarise.")


def t_install_app(args, ctx) -> str:
    query = " ".join(str(args.get("app", "")).split())
    if not query:
        raise ToolError("Say which app to install, e.g. 'Spotify'.")
    source = str(args.get("source", "auto") or "auto").lower()
    mode = ctx.config.get("approval", "smart")
    known = apps.resolve_known(query)
    if source == "auto" and not known:
        # Built-in apps (Task Manager, Firefox...) and anything already in the Start menu.
        have = apps.installed_by_name(query)
        if have:
            return f"{have[1]} is already installed (it is in the Start menu). Open it with open_app."

    if source == "debian" or (known and known["source"] == "debian" and source != "flathub"):
        package = known["id"] if known and known["source"] == "debian" else query.lower()
        name = known["name"] if known and known["source"] == "debian" else package
        if not apps.DEB_NAME.match(package):
            raise ToolError(f"'{package}' is not a Debian package name. Use find_software to look it up.")
        if apps.deb_installed(package):
            return f"{name} is already installed. Open it with open_app."
        command = f"apt-get install -y {package}"
        req = ApprovalRequest(tool="install_app", title=f"Install {name}", admin=True,
                              detail=f"Debian package '{package}' (needs your password)\n{command}")
        if safety.needs_approval(mode, safety.CHANGE, True) and not ctx.ask(req):
            return "The user did NOT allow installing it."
        code, out = ctx.runner.run(command, admin=True, timeout=3600, on_output=ctx.on_output,
                                   cancel=ctx.cancel, snapshot_label=f"install {package}"
                                   if ctx.config.get("snapshots") else None)
        if code != 0:
            return format_result(code, out, admin=True)
        return f"Installed {name}. It's in the Start menu. Open it with open_app."

    if not apps.flatpak_available():
        raise ToolError("Flatpak is missing. Install it first (admin): apt-get install -y flatpak")
    if known and known["source"] == "flathub":
        app_id, name = known["id"], known["name"]
    elif apps.FLATPAK_ID.match(query):
        app_id, name = query, query
    else:
        error = apps.ensure_user_flathub()
        if error:
            raise ToolError(f"Could not reach Flathub: {error}")
        results = apps.search_flathub(query)
        choice = apps.pick_match(query, results)
        if choice is None:
            if not results:
                return (f"Nothing called '{query}' on Flathub. Try find_software (Debian packages) "
                        "or ask the user for the exact name.")
            listing = "\n".join(f"- {n} ({i}): {d}" for i, n, d in results)
            return ("Several apps match. Ask the user which one they mean, then call install_app "
                    f"again with its id:\n{listing}")
        app_id, name = choice
    where = apps.flatpak_installed(app_id)
    if where:
        return f"{name} is already installed. Open it with open_app."
    command = apps.flatpak_install_command(app_id)
    req = ApprovalRequest(tool="install_app", title=f"Install {name}",
                          detail=f"{name} ({app_id}) from Flathub, just for you, no password needed.\n"
                                 f"{command}")
    if safety.needs_approval(mode, safety.CHANGE, False) and not ctx.ask(req):
        return "The user did NOT allow installing it."
    error = apps.ensure_user_flathub()
    if error:
        raise ToolError(f"Could not reach Flathub: {error}")
    code, out = ctx.runner.run(command, timeout=3600, on_output=ctx.on_output, cancel=ctx.cancel)
    if code != 0:
        return format_result(code, out)
    return (f"Installed {name} ({app_id}). It's in the Start menu now. Open it with open_app; pin it "
            f"or add a desktop/startup shortcut with create_shortcut. Its settings live in "
            f"~/.var/app/{app_id}/.")


def t_create_script(args, ctx) -> str:
    content = str(args.get("content", ""))
    if not content.strip():
        raise ToolError("'content' is empty: give the complete script.")
    description = " ".join(str(args.get("description", "")).split())[:200]
    start_menu = _truthy(args.get("start_menu", False))
    try:
        slug = scripts.slugify(args.get("name") or description or "my-script")
        schedule = scripts.parse_schedule(args.get("schedule", ""))
    except ValueError as e:
        raise ToolError(str(e)) from None
    path = scripts.target_path(slug, content)
    level = safety.CHANGE
    for line in content.splitlines():
        if line.strip() and not line.strip().startswith("#") and \
                safety.classify(line.strip())[0] == safety.DANGER:
            level = safety.DANGER
            break
    lines = content.strip("\n").splitlines()
    summary = [f"Save {path}" + (" (replaces the old version; a backup is kept)" if path.exists() else "")]
    if start_menu:
        summary.append("Add it to the Start menu")
    if schedule is not None:
        summary.append(f"Run it automatically: {schedule.label}" if schedule.kind != "remove"
                       else "Stop running it automatically")
    preview = "\n".join(lines[:60]) + (f"\n… ({len(lines) - 60} more lines)" if len(lines) > 60 else "")
    req = ApprovalRequest(tool="create_script", title="Save a script", level=level,
                          reason=description, detail="\n".join(summary) + "\n\n" + preview,
                          warning="Careful: this script contains a dangerous command."
                          if level == safety.DANGER else "")
    if safety.needs_approval(ctx.config.get("approval", "smart"), level, False) and not ctx.ask(req):
        return "The user did NOT allow saving this script."
    try:
        result = scripts.save_script(slug, content, description, start_menu, schedule)
    except (OSError, ValueError) as e:
        raise ToolError(f"Could not save the script: {e}") from None
    return scripts.describe(result)


def t_create_shortcut(args, ctx) -> str:
    target = str(args.get("target", "")).strip()
    where = str(args.get("where", "desktop"))
    if ctx.config.get("approval", "smart") == "always":
        req = ApprovalRequest(tool="create_shortcut", title="Make a shortcut",
                              detail=f"{target} → {where}")
        if not ctx.ask(req):
            return "The user did NOT allow the shortcut."
    try:
        return apps.make_shortcut(target, where, str(args.get("name", "") or ""))
    except (ValueError, OSError) as e:
        raise ToolError(str(e)) from None


def t_set_wallpaper(args, ctx) -> str:
    source = str(args.get("file", "")).strip()
    if not source:
        raise ToolError("Give a picture or video file, or a direct web link to one.")
    exe = shutil.which("myos-set-wallpaper")
    if not exe:
        raise ToolError("The wallpaper command (myos-set-wallpaper) is not installed.")
    argv = [exe, source]
    for flag in ("sound", "fit", "always_play"):
        if _truthy(args.get(flag, False)):
            argv.append("--" + flag.replace("_", "-"))
    if ctx.config.get("approval", "smart") == "always":
        req = ApprovalRequest(tool="set_wallpaper", title="Change the wallpaper", detail=shlex.join(argv))
        if not ctx.ask(req):
            return "The user did NOT allow changing the wallpaper."
    code, out = ctx.runner.run(shlex.join(argv), timeout=1800, on_output=ctx.on_output,
                               cancel=ctx.cancel)
    return format_result(code, out)


_TYPE_ALIASES = {"str": "string", "text": "string", "int": "integer", "float": "number",
                 "bool": "boolean", "number": "number", "integer": "integer", "boolean": "boolean",
                 "string": "string"}


def t_create_tool(args, ctx) -> str:
    raw = args.get("parameters") or []
    if isinstance(raw, dict):  # {"city": {"type": "string"}} is accepted too
        raw = [dict(v, name=k) if isinstance(v, dict) else {"name": k} for k, v in raw.items()]
    params = []
    for p in raw if isinstance(raw, list) else []:
        if isinstance(p, str):
            p = {"name": p}
        if not isinstance(p, dict):
            continue
        params.append({"name": str(p.get("name", "")).strip(),
                       "type": _TYPE_ALIASES.get(str(p.get("type", "string")).lower(), "string"),
                       "description": str(p.get("description", "")),
                       "required": bool(p.get("required", True))})
    spec = {"name": str(args.get("name", "")).strip().lower(),
            "description": " ".join(str(args.get("description", "")).split()),
            "command": str(args.get("command", "")).strip(),
            "parameters": params,
            "ask_before_running": _truthy(args.get("ask_before_running", True)),
            "run_as_admin": _truthy(args.get("run_as_admin", False)),
            "timeout": 120, "enabled": True}
    errors = validate_spec(spec)
    if errors:
        raise ToolError("\n".join(errors))
    level = safety.DANGER if safety.classify(spec["command"])[0] == safety.DANGER else safety.CHANGE
    exists = (paths.USER_TOOLS_DIR / f"{spec['name']}.json").exists()
    req = ApprovalRequest(tool="create_tool", level=level, reason=spec["description"],
                          title=f"{'Update' if exists else 'Add'} the AI tool \"{spec['name']}\"",
                          detail=json.dumps(spec, indent=2, ensure_ascii=False))
    if safety.needs_approval(ctx.config.get("approval", "smart"), level, False) and not ctx.ask(req):
        return "The user did NOT allow adding this tool."
    save_user_tool(spec)
    if ctx.registry is not None:
        ctx.registry.reload()
    return (f"Saved the tool '{spec['name']}'. You can use it right away; the user can see and "
            "change it in Settings > Tools.")


BUILTIN_SPECS = [
    ("run_command",
     "Run a bash command on this computer and get its output. Use admin=true for commands "
     "that need root (installing packages, changing system files, services). Never write sudo.",
     {"command": {"type": "string", "description": "The bash command."},
      "admin": {"type": "boolean", "description": "Run as administrator (root)."},
      "reason": {"type": "string", "description": "One short sentence for the user: why."}},
     ["command"], t_run_command),
    ("read_file", "Read a text file.",
     {"path": {"type": "string", "description": "File path, ~ allowed."},
      "start_line": {"type": "integer", "description": "First line (default 1)."},
      "max_lines": {"type": "integer", "description": "How many lines (default 300)."}},
     ["path"], t_read_file),
    ("write_file", "Create or replace a text file (a backup of the old version is kept). "
     "Use admin=true for system files under /etc.",
     {"path": {"type": "string", "description": "File path."},
      "content": {"type": "string", "description": "The complete new content."},
      "append": {"type": "boolean", "description": "Add to the end instead of replacing."},
      "admin": {"type": "boolean", "description": "Write as administrator."},
      "reason": {"type": "string", "description": "Why, in one sentence."}},
     ["path", "content"], t_write_file),
    ("list_directory", "List the files in a folder.",
     {"path": {"type": "string", "description": "Folder path (default: home)."},
      "show_hidden": {"type": "boolean", "description": "Include hidden files."}},
     [], t_list_directory),
    ("system_report", "Collect a summary of this PC: OS, hardware, drivers, network, audio, "
     "failed services, missing firmware and recent errors. Use it first when something is broken.",
     {}, [], t_system_report),
    ("search_fixes", "Search the built-in library of tested fix guides for this OS. Use it "
     "before fixing drivers, Wi-Fi, sound, Bluetooth, Windows programs, games, printers, etc.",
     {"query": {"type": "string", "description": "What is wrong or what to set up."}},
     ["query"], t_search_fixes),
    ("find_software", "Search Debian packages and Flathub apps by name.",
     {"query": {"type": "string", "description": "App name or kind of app."}},
     ["query"], t_find_software),
    ("propose_plan", "Before a job with several changes (installing and setting up apps, writing "
     "scripts, changing settings), show the user your plan and get one OK for the whole job.",
     {"summary": {"type": "string", "description": "The goal, in one short sentence."},
      "steps": {"type": "array", "items": {"type": "string"},
                "description": "The steps, one short line each."}},
     ["summary", "steps"], t_propose_plan),
    ("install_app", "Install a desktop app: from Flathub just for this user (no password), or a "
     "Debian package. Works with everyday names like 'Spotify', 'OBS', 'File Explorer', or a "
     "Flathub id like com.spotify.Client.",
     {"app": {"type": "string", "description": "The app's name or Flathub id."},
      "source": {"type": "string", "enum": ["auto", "flathub", "debian"],
                 "description": "Where to get it (default: auto)."}},
     ["app"], t_install_app),
    ("open_app", "Open an installed app (by name), a web address or a file.",
     {"target": {"type": "string", "description": "App name, URL or file path."}},
     ["target"], t_open_app),
    ("create_shortcut", "Make a shortcut to an app, script, website or file/folder: on the desktop, "
     "in the Start menu, pinned to the taskbar, or opened at startup (login).",
     {"target": {"type": "string", "description": "App name, script name/path, web address or path."},
      "where": {"type": "string", "enum": ["desktop", "start_menu", "taskbar", "startup"],
                "description": "Where the shortcut goes."},
      "name": {"type": "string", "description": "Optional label for websites and files."}},
     ["target", "where"], t_create_shortcut),
    ("set_wallpaper", "Set the desktop wallpaper to a picture or a video (a live video wallpaper "
     "that loops, starts muted, and pauses while a window is maximized). Accepts a file path or "
     "a direct web link to a .mp4/.webm/.jpg/.png file.",
     {"file": {"type": "string", "description": "Path to the picture/video, or a direct link."},
      "sound": {"type": "boolean", "description": "Play the video's sound (default: muted)."},
      "fit": {"type": "boolean", "description": "Show the whole picture/video with bars instead "
              "of filling the screen."},
      "always_play": {"type": "boolean", "description": "Keep the video playing behind "
                      "maximized windows (uses more power)."}},
     ["file"], t_set_wallpaper),
    ("create_script", "Write a script for the user: saved in ~/Scripts, made runnable and checked "
     "for syntax errors. Can add a Start menu entry and run it on a schedule. Calling it again "
     "with the same name updates the script (a backup is kept).",
     {"name": {"type": "string", "description": "Short name, e.g. backup-photos."},
      "content": {"type": "string", "description": "The complete script. Start with #!/bin/bash "
                  "or #!/usr/bin/env python3."},
      "description": {"type": "string", "description": "What it does, in one sentence."},
      "start_menu": {"type": "boolean", "description": "Add a Start menu entry to run it with a click."},
      "schedule": {"type": "string", "description": "Optional: 'daily at 18:30', 'every 15 minutes', "
                   "'hourly', 'every Monday at 9:00', 'weekdays at 8am', 'at login', or 'none' "
                   "to stop running it automatically."}},
     ["name", "content"], t_create_script),
    ("create_tool", "Save a new reusable tool for yourself (it also shows in Settings > Tools). "
     "Use it when the user asks you to remember how to do something or to add an ability.",
     {"name": {"type": "string", "description": "lower_case_name"},
      "description": {"type": "string", "description": "What it does and when to use it."},
      "command": {"type": "string", "description": "Shell command. Put {param} where a parameter "
                  "value goes (it is quoted automatically)."},
      "parameters": {"type": "array", "description": "Parameters the command needs.",
                     "items": {"type": "object", "properties": {
                         "name": {"type": "string"}, "type": {"type": "string"},
                         "description": {"type": "string"}, "required": {"type": "boolean"}}}},
      "ask_before_running": {"type": "boolean", "description": "Ask the user each time (default true)."}},
     ["name", "description", "command"], t_create_tool),
]

BUILTIN_NAMES = {s[0] for s in BUILTIN_SPECS}


def builtin_tools() -> list:
    return [Tool(n, d, p, r, f) for n, d, p, r, f in BUILTIN_SPECS]


# ─────────────────────────────── custom tools ────────────────────────────────

def validate_spec(spec) -> list:
    errors = []
    if not isinstance(spec, dict):
        return ["The file must contain a JSON object."]
    name = spec.get("name", "")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errors.append("Name must start with a letter and use only a-z, 0-9 and _ (2-48 characters).")
    elif name in BUILTIN_NAMES:
        errors.append(f"'{name}' is a built-in tool name. Choose another name.")
    if not str(spec.get("description", "")).strip():
        errors.append("Add a description so the AI knows when to use the tool.")
    if not str(spec.get("command", "")).strip():
        errors.append("The command is empty.")
    params = spec.get("parameters", [])
    if not isinstance(params, list):
        errors.append("'parameters' must be a list.")
        params = []
    seen = set()
    for p in params:
        pname = p.get("name", "") if isinstance(p, dict) else ""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", pname or ""):
            errors.append(f"Parameter name '{pname}' is invalid (use a-z, 0-9, _).")
        elif pname in seen:
            errors.append(f"Parameter '{pname}' appears twice.")
        seen.add(pname)
        if isinstance(p, dict) and p.get("type", "string") not in PARAM_TYPES:
            errors.append(f"Parameter '{pname}' has an unknown type.")
    for ph in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", str(spec.get("command", ""))):
        if ph not in seen and ph.upper() != ph:
            errors.append(f"The command uses {{{ph}}} but there is no parameter called '{ph}'.")
    return errors


def render_command(spec: dict, args: dict):
    """Fill in the parameters. Returns (command, environment)."""
    values, env = {}, {}
    for p in spec.get("parameters", []):
        name, ptype = p["name"], p.get("type", "string")
        value = args.get(name, p.get("default"))
        if value is None or value == "":
            if p.get("required"):
                raise ToolError(f"Missing parameter '{name}'.")
            value = ""
        if ptype == "integer" and value != "":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ToolError(f"Parameter '{name}' must be a whole number.") from None
        elif ptype == "number" and value != "":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ToolError(f"Parameter '{name}' must be a number.") from None
        elif ptype == "boolean":
            value = "true" if _truthy(value) else "false"
        text = str(value)
        if "\x00" in text:
            raise ToolError(f"Parameter '{name}' contains an invalid character.")
        values[name] = shlex.quote(text) if text else "''"
        env[name.upper()] = text

    def fill(m):
        return values.get(m.group(1), m.group(0))

    return re.sub(r"\{([a-z][a-z0-9_]*)\}", fill, spec["command"]), env


def make_custom_tool(spec: dict, source: str, path: Path) -> Tool:
    props, required = {}, []
    for p in spec.get("parameters", []):
        props[p["name"]] = {"type": p.get("type", "string"),
                            "description": p.get("description", "")}
        if p.get("required"):
            required.append(p["name"])
    name = spec["name"]

    def run(args, ctx: ToolContext) -> str:
        command, env = render_command(spec, args)
        admin = bool(spec.get("run_as_admin", False))
        level, why = safety.classify(command)
        trusted = name in (ctx.config.get("trusted_tools") or [])
        ask = bool(spec.get("ask_before_running", True)) and not trusted
        effective = safety.DANGER if level == safety.DANGER else (safety.CHANGE if ask else safety.READ)
        if safety.needs_approval(ctx.config.get("approval", "smart"), effective, admin):
            req = ApprovalRequest(tool=name, title=f"Use your tool \"{name}\"", detail=command,
                                  level=effective, admin=admin, allow_always=True,
                                  warning=f"Careful: this command {why}." if level == safety.DANGER else "")
            if not ctx.ask(req):
                return "The user did NOT allow this tool to run."
            if req.decision == "always" and level != safety.DANGER:
                trusted_list = list(ctx.config.get("trusted_tools") or [])
                if name not in trusted_list:
                    trusted_list.append(name)
                    ctx.config["trusted_tools"] = trusted_list
                    ctx.config.save()
        code, out = ctx.runner.run(command, admin=admin, timeout=int(spec.get("timeout", 120)),
                                   on_output=ctx.on_output, cancel=ctx.cancel, extra_env=env)
        return format_result(code, out, admin)

    return Tool(name, str(spec.get("description", "")), props, required, run,
                source=source, path=path, spec=spec)


def load_custom_specs():
    """Yield (spec, source, path, errors) for every tool file."""
    places = [(d, "system") for d in paths.SYSTEM_TOOLS_DIRS] + [(paths.USER_TOOLS_DIR, "yours")]
    for d, source in places:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                spec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                yield None, source, f, [f"Cannot read the file: {e}"]
                continue
            yield spec, source, f, validate_spec(spec)


def save_user_tool(spec: dict, old_path: Path = None) -> Path:
    errors = validate_spec(spec)
    if errors:
        raise ToolError("\n".join(errors))
    paths.USER_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    path = paths.USER_TOOLS_DIR / f"{spec['name']}.json"
    cfg.write_json_atomic(path, spec)
    if old_path and Path(old_path) != path and Path(old_path).parent == paths.USER_TOOLS_DIR:
        try:
            Path(old_path).unlink()
        except OSError:
            pass
    return path


class ToolRegistry:
    def __init__(self, config):
        self.config = config
        self.tools = {}
        self.problems = []      # (path, errors) of tool files that could not be loaded
        self.reload()

    def reload(self) -> None:
        self.tools = {t.name: t for t in builtin_tools()}
        self.problems = []
        for spec, source, path, errors in load_custom_specs():
            if errors:
                self.problems.append((path, errors))
                continue
            self.tools[spec["name"]] = make_custom_tool(spec, source, path)

    def is_enabled(self, tool: Tool) -> bool:
        if tool.name in (self.config.get("disabled_tools") or []):
            return False
        return tool.spec is None or bool(tool.spec.get("enabled", True))

    def enabled(self) -> list:
        return [t for t in self.tools.values() if self.is_enabled(t)]

    def schemas(self) -> list:
        return [t.schema() for t in self.enabled()]

    def call(self, name: str, args, ctx: ToolContext):
        """Run a tool. Returns (result_text, ok)."""
        tool = self.tools.get(name)
        if tool is None or not self.is_enabled(tool):
            names = ", ".join(t.name for t in self.enabled())
            return f"There is no tool called '{name}'. Available tools: {names}", False
        if not isinstance(args, dict):
            args = {}
        try:
            return str(tool.func(args, ctx)), True
        except ToolError as e:
            return f"Error: {e}", False
        except Exception as e:  # never let a tool crash the conversation
            return f"Error while running {name}: {type(e).__name__}: {e}", False
