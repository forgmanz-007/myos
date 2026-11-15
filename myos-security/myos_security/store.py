"""Settings and state files of ClamAV Security and the .exe launcher.

~/.config/myos/security.json      protection settings and API keys (only you can read it)
~/.config/myos/exe-settings.json  per-program Windows compatibility settings (Wine/Proton)
~/.cache/myos/scan-cache.json     recent online results, to respect API limits
~/.local/state/myos/security-history.jsonl   every check, newest last
~/.local/state/myos/checked-programs.json    programs checked on their first start
~/.local/share/myos/quarantine/   files you quarantined
~/.local/share/myos/prefixes/     Windows "C: drives" (Wine/Proton prefixes)
"""
import datetime
import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "myos"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "myos"
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "myos"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "myos"

SECURITY_FILE = CONFIG_DIR / "security.json"
EXE_FILE = CONFIG_DIR / "exe-settings.json"
SCAN_CACHE = CACHE_DIR / "scan-cache.json"
HISTORY_FILE = STATE_DIR / "security-history.jsonl"
CHECKED_FILE = STATE_DIR / "checked-programs.json"
QUARANTINE_DIR = DATA_DIR / "quarantine"
PREFIX_DIR = DATA_DIR / "prefixes"

SECURITY_DEFAULTS = {
    "check_exe": True,            # check Windows programs before they run
    "check_every_start": False,   # False: only the first start of each program (and when asked)
    "virustotal_key": "",
    "vt_upload": "ask",           # never | ask | always: upload files VirusTotal has never seen
    "vt_block_threshold": 3,      # this many engines detecting it: block
    "anyrun_key": "",
    "anyrun_mode": "ask",         # never | ask | always: run unknown files in the ANY.RUN sandbox
    "anyrun_privacy": "bylink",   # public | bylink | owner | byteam
    "anyrun_timeout": 120,        # seconds the sandbox runs the program
    "cache_minutes": 60,          # reuse online results for this long
    "auto_start_clean": True,     # start right away when everything is clean
    "default_runner": "wine",     # wine | proton (for programs without their own setting)
    "setup_hint_shown": False,
}

EXE_DEFAULTS = {
    "runner": "",                 # "" = use the default runner, or wine | proton
    "proton": "builtin",          # builtin | GE-Proton | UMU-Proton | /path/to/proton/dir
    "prefix": "shared",           # shared | separate
    "launch_options": "",         # e.g. "PROTON_ENABLE_WAYLAND=1 %command% -windowed"
}


def read_json(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data, private: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        if private:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── protection settings ──────────────────────────────────────────────────────
def load_security() -> dict:
    data = dict(SECURITY_DEFAULTS)
    data.update({k: v for k, v in read_json(SECURITY_FILE).items() if k in SECURITY_DEFAULTS})
    return data


def save_security(data: dict) -> None:
    write_json(SECURITY_FILE, {k: data.get(k, v) for k, v in SECURITY_DEFAULTS.items()}, private=True)


# ── per-program settings (shared with the Properties tab written in C++) ─────
def exe_key(path) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def get_exe_settings(path) -> dict:
    files = read_json(EXE_FILE).get("files", {})
    entry = files.get(exe_key(path), {}) if isinstance(files, dict) else {}
    result = dict(EXE_DEFAULTS)
    result.update({k: v for k, v in entry.items() if k in EXE_DEFAULTS and isinstance(v, str)})
    return result


def effective_runner(path, security=None) -> str:
    runner = get_exe_settings(path).get("runner") or ""
    if runner in ("wine", "proton"):
        return runner
    security = security or load_security()
    return security.get("default_runner", "wine") if security.get("default_runner") in ("wine", "proton") else "wine"


def set_exe_settings(path, **changes) -> None:
    data = read_json(EXE_FILE)
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    key = exe_key(path)
    entry = dict(files.get(key, {}))
    for k, v in changes.items():
        if k in EXE_DEFAULTS:
            entry[k] = str(v)
    files[key] = entry
    data["version"] = 1
    data["files"] = files
    write_json(EXE_FILE, data)


# ── online result cache ──────────────────────────────────────────────────────
def cache_get(sha256: str, engine: str, max_age_minutes: int):
    entry = read_json(SCAN_CACHE).get(sha256, {}).get(engine)
    if not entry or time.time() - entry.get("time", 0) > max_age_minutes * 60:
        return None
    return entry.get("finding")


def cache_put(sha256: str, engine: str, finding: dict) -> None:
    data = read_json(SCAN_CACHE)
    now = time.time()
    # forget entries older than a week
    data = {h: v for h, v in data.items()
            if any(now - e.get("time", 0) < 7 * 86400 for e in v.values() if isinstance(e, dict))}
    data.setdefault(sha256, {})[engine] = {"time": now, "finding": finding}
    write_json(SCAN_CACHE, data)


# ── programs that were checked on their first start ──────────────────────────
def _identity(path) -> dict:
    """What tells this file apart from an updated or replaced one. (Not the device number:
    Btrfs gives each subvolume a new one at every start.)"""
    st = os.stat(path)
    return {"inode": st.st_ino, "size": st.st_size, "modified": st.st_mtime_ns}


def _checked_files() -> dict:
    files = read_json(CHECKED_FILE).get("files")
    return files if isinstance(files, dict) else {}


def checked_before(path) -> bool:
    """True if this very file was checked and then started (or found clean when you scanned
    it). A program that was updated or replaced since counts as a new one."""
    key = exe_key(path)
    entry = _checked_files().get(key)
    if not isinstance(entry, dict):
        return False
    try:
        now = _identity(key)
    except OSError:
        return False
    return all(entry.get(k) == v for k, v in now.items())


def remember_checked(path, sha256: str, level: str) -> None:
    key = exe_key(path)
    try:
        entry = _identity(key)
    except OSError:
        return
    entry.update({"sha256": sha256, "result": level,
                  "time": datetime.datetime.now().isoformat(timespec="seconds")})
    files = {k: v for k, v in _checked_files().items() if os.path.exists(k)}    # forget removed programs
    files[key] = entry
    write_json(CHECKED_FILE, {"version": 1, "files": files})


def forget_checked(path) -> None:
    """Check this file again the next time it starts (e.g. a scan found something)."""
    files = _checked_files()
    if files.pop(exe_key(path), None) is not None:
        write_json(CHECKED_FILE, {"version": 1, "files": files})


def when_checked(path) -> str:
    """'2026-11-13T22:01:25' if this very file was checked before, else ''."""
    return _checked_files().get(exe_key(path), {}).get("time", "") if checked_before(path) else ""


# ── history ──────────────────────────────────────────────────────────────────
def add_history(path: str, sha256: str, level: str, summary: str, action: str = "") -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    item = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "path": str(path),
            "sha256": sha256, "result": level, "summary": summary, "action": action}
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def read_history(limit: int = 500) -> list:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    items = []
    for line in reversed(lines):
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    return items


# ── quarantine ───────────────────────────────────────────────────────────────
def _quarantine_index() -> list:
    data = read_json(QUARANTINE_DIR / "index.json")
    return data.get("items", []) if isinstance(data.get("items"), list) else []


def quarantine_items() -> list:
    return [i for i in _quarantine_index() if (QUARANTINE_DIR / i.get("id", "\0")).exists()]


def quarantine(path, sha256: str, threat: str) -> dict:
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(QUARANTINE_DIR, 0o700)
    src = Path(path)
    item_id = f"{int(time.time())}-{sha256[:12]}-{src.name}.quarantined"
    dest = QUARANTINE_DIR / item_id
    shutil.move(str(src), dest)
    os.chmod(dest, 0o400)  # readable by you only, never executable
    item = {"id": item_id, "original": str(src), "sha256": sha256, "threat": threat,
            "date": datetime.datetime.now().isoformat(timespec="seconds")}
    items = _quarantine_index() + [item]
    write_json(QUARANTINE_DIR / "index.json", {"items": items})
    return item


def restore_quarantined(item_id: str) -> str:
    items = _quarantine_index()
    item = next((i for i in items if i.get("id") == item_id), None)
    if item is None:
        raise FileNotFoundError(item_id)
    dest = Path(item["original"])
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}-restored{dest.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = QUARANTINE_DIR / item_id
    os.chmod(src, 0o600)
    shutil.move(str(src), dest)
    write_json(QUARANTINE_DIR / "index.json", {"items": [i for i in items if i.get("id") != item_id]})
    return str(dest)


def delete_quarantined(item_id: str) -> None:
    items = _quarantine_index()
    src = QUARANTINE_DIR / item_id
    if src.exists():
        os.chmod(src, 0o600)
        src.unlink()
    write_json(QUARANTINE_DIR / "index.json", {"items": [i for i in items if i.get("id") != item_id]})
