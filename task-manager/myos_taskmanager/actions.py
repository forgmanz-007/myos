"""Ending processes. Your own processes are asked to quit and, if they are still
there a moment later, stopped by force. Other users' and system processes go
through a small helper that runs as administrator (the system asks for the password)."""
import os
import shutil
import signal
import subprocess
import time

from .procfs import parse_stat, read_text

GRACE = 3.0     # seconds a process gets to quit by itself
HELPER = os.environ.get("MYOS_TASKMANAGER_HELPER", "/usr/libexec/myos-taskmanager/end-process")


def start_of(pid: int, root="/") -> int:
    """Start time of the process (its identity together with the PID), -1 if it is gone."""
    parsed = parse_stat(read_text(os.path.join(str(root), "proc", str(pid), "stat")))
    if not parsed or parsed[1][0] in "ZX":      # a zombie has already ended
        return -1
    try:
        return int(parsed[1][19])
    except ValueError:
        return -1


def alive(pid: int, start: int) -> bool:
    return start_of(pid) == start


def end_own(targets, grace: float = None) -> list:
    """End [(pid, start)]: SIGTERM, then SIGKILL after the grace time.
    Returns the PIDs that could not be ended."""
    grace = GRACE if grace is None else grace
    failed = []
    pending = []
    for pid, start in targets:
        if pid <= 1 or not alive(pid, start):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            pending.append((pid, start))
        except ProcessLookupError:
            pass
        except PermissionError:
            failed.append(pid)
    deadline = time.monotonic() + grace
    while pending and time.monotonic() < deadline:
        time.sleep(0.1)
        pending = [(p, s) for p, s in pending if alive(p, s)]
    for pid, start in pending:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except PermissionError:
            failed.append(pid)
    deadline = time.monotonic() + 2.0
    while pending and time.monotonic() < deadline:
        time.sleep(0.05)
        pending = [(p, s) for p, s in pending if alive(p, s)]
    return failed + [p for p, _s in pending if p not in failed]


def end_as_admin(targets) -> tuple:
    """End other users' processes through the administrator helper: (ok, message)."""
    if not targets:
        return True, ""
    if not shutil.which("pkexec"):
        return False, "pkexec is missing, so administrator rights can't be asked for."
    try:
        r = subprocess.run(["pkexec", HELPER] + [f"{pid}:{start}" for pid, start in targets],
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    if r.returncode in (126, 127) and "end-process" not in r.stderr:
        return False, "It was not allowed (no administrator password was given)."
    return False, (r.stderr.strip().splitlines() or ["The process could not be ended."])[-1]


def restart_desktop() -> tuple:
    """Restart the taskbar and Start menu (plasmashell), like restarting Windows Explorer."""
    if shutil.which("systemctl"):
        unit = "plasma-plasmashell.service"
        try:
            active = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True,
                                    text=True, timeout=10).stdout.strip() == "active"
            if active:
                r = subprocess.run(["systemctl", "--user", "restart", unit], capture_output=True,
                                   text=True, timeout=60)
                return (r.returncode == 0), r.stderr.strip()
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
    if shutil.which("plasmashell"):
        try:
            subprocess.Popen(["plasmashell", "--replace"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return True, ""
        except OSError as e:
            return False, str(e)
    return False, "The desktop (plasmashell) was not found."


def exe_folder(pid: int) -> str:
    """Folder of the program file a process runs, '' if it can't be read."""
    try:
        return os.path.dirname(os.readlink(f"/proc/{pid}/exe").replace(" (deleted)", ""))
    except OSError:
        return ""
