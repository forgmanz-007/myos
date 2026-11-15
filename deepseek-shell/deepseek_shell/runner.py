"""Run shell commands for the AI: as the user, or as root through pkexec
(the system's password dialog). Every command is written to the activity log.
"""
import datetime
import os
import re
import signal
import subprocess
import threading
import time

from . import paths

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")

PKEXEC_DISMISSED = 126
PKEXEC_NOT_AUTHORIZED = 127


def clean_output(text: str) -> str:
    """Remove colour codes and progress-bar redraws."""
    text = _ANSI.sub("", text)
    lines = []
    for line in text.split("\n"):
        if "\r" in line:
            line = [p for p in line.split("\r") if p.strip()][-1:] or [""]
            line = line[0]
        lines.append(line)
    return "\n".join(lines)


def truncate_middle(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 5
    tail = limit - head
    skipped = text[head:len(text) - tail].count("\n")
    return f"{text[:head]}\n[... {skipped} lines cut ...]\n{text[-tail:]}"


def command_env(extra=None) -> dict:
    env = os.environ.copy()
    env.update({
        "DEBIAN_FRONTEND": "noninteractive",
        "LC_ALL": "C.UTF-8",
        "PAGER": "cat",
        "SYSTEMD_PAGER": "",
        "GIT_PAGER": "cat",
        # There is no keyboard: a command that starts an editor (git commit without -m,
        # crontab -e, ...) must fail at once instead of waiting for the timeout.
        "EDITOR": "false",
        "VISUAL": "false",
        "GIT_EDITOR": "false",
        "SUDO_EDITOR": "false",
        "TERM": "dumb",
        "NO_COLOR": "1",
    })
    local_bin = os.path.expanduser("~/.local/bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = f"{env.get('PATH', '/usr/bin:/bin')}:{local_bin}"
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def log_activity(command: str, admin: bool, code) -> None:
    try:
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        who = "admin" if admin else "user"
        with open(paths.ACTIVITY_LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp}\t{who}\texit={code}\t{command}\n")
    except OSError:
        pass


class CommandRunner:
    def __init__(self, libexec_dir=None, terminal_auth: bool = False):
        self.libexec_dir = libexec_dir or paths.LIBEXEC_DIR
        # In the text (TTY) mode pkexec asks for the password on the terminal.
        self.terminal_auth = terminal_auth

    def admin_argv(self, command: str, timeout: int, snapshot_label=None) -> list:
        helper = os.path.join(str(self.libexec_dir), "run-as-root")
        argv = ["pkexec", helper, "--timeout", str(int(timeout))]
        if snapshot_label:
            argv += ["--snapshot", snapshot_label[:150]]
        return argv + ["--", command]

    def run(self, command: str, admin: bool = False, timeout: int = 900, on_output=None,
            cancel=None, snapshot_label=None, extra_env=None):
        """Run a command. Returns (exit_code, output)."""
        if admin:
            argv = self.admin_argv(command, timeout, snapshot_label)
        else:
            argv = ["bash", "-c", command]
        stdin = None if (admin and self.terminal_auth) else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, env=command_env(extra_env),
                                    start_new_session=not (admin and self.terminal_auth))
        except FileNotFoundError as e:
            log_activity(command, admin, "not-started")
            return 127, f"Could not start: {e}"

        chunks = []

        def reader():
            fd = proc.stdout.fileno()
            while True:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", "replace")
                chunks.append(text)
                if on_output:
                    try:
                        on_output(text)
                    except Exception:
                        pass

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout + (30 if admin else 0)
        stopped = None
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                stopped = "cancelled"
            elif time.monotonic() > deadline:
                stopped = f"timed out after {timeout} s"
            if stopped:
                self._stop(proc)
                break
            time.sleep(0.1)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        thread.join(timeout=2)
        try:
            proc.stdout.close()
        except OSError:
            pass
        output = clean_output("".join(chunks))
        code = proc.returncode if proc.returncode is not None else -1
        if stopped:
            output += f"\n[Command {stopped}.]"
        log_activity(command, admin, code)
        return code, output

    @staticmethod
    def _stop(proc) -> None:
        # Commands run as root through pkexec cannot be signalled by us; the
        # root helper enforces its own time limit for those.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.send_signal(sig)
                except Exception:
                    pass
            try:
                proc.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                continue

    @staticmethod
    def launch_detached(argv, extra_env=None) -> None:
        """Start a program (e.g. a GUI app) without waiting for it."""
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         env=command_env(extra_env) if extra_env else None)
