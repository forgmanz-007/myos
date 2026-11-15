"""Start Windows programs with Wine or with Proton (through umu-launcher)."""
import hashlib
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from . import store

SYSTEM_TOOLS = Path("/usr/share/steam/compatibilitytools.d")
USER_TOOLS = Path.home() / ".local/share/Steam/compatibilitytools.d"


class LaunchError(Exception):
    pass


def builtin_proton():
    """The GE-Proton that came with the system, or None."""
    marker = SYSTEM_TOOLS / ".builtin"
    try:
        name = marker.read_text(encoding="utf-8").strip()
        if name and (SYSTEM_TOOLS / name / "proton").exists():
            return SYSTEM_TOOLS / name
    except OSError:
        pass
    for d in sorted(SYSTEM_TOOLS.glob("*"), reverse=True) if SYSTEM_TOOLS.is_dir() else []:
        if (d / "proton").exists():
            return d
    return None


def proton_choices() -> list:
    """(value, label) pairs for the Proton version menu."""
    choices = []
    b = builtin_proton()
    choices.append(("builtin", f"Built in ({b.name})" if b else "Built in (not installed: downloads GE-Proton)"))
    choices.append(("GE-Proton", "Latest GE-Proton (downloads when needed)"))
    choices.append(("UMU-Proton", "Latest UMU-Proton, Valve's Proton (downloads when needed)"))
    for base in (USER_TOOLS, SYSTEM_TOOLS):
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if (d / "proton").exists() and (b is None or d != b):
                    choices.append((str(d), d.name))
    return choices


def proton_path(choice: str) -> str:
    if choice in ("", "builtin"):
        b = builtin_proton()
        return str(b) if b else "GE-Proton"
    return choice


def prefix_dir(exe: Path, runner: str, mode: str) -> Path:
    if mode == "separate":
        digest = hashlib.sha1(str(exe).encode()).hexdigest()[:8]
        slug = re.sub(r"[^A-Za-z0-9]+", "-", exe.stem).strip("-").lower()[:40] or "program"
        return store.PREFIX_DIR / f"{runner}-{slug}-{digest}"
    return store.PREFIX_DIR / f"{runner}-default"


_ENV_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def parse_launch_options(text: str):
    """Steam-style launch options, e.g. 'DXVK_HUD=1 gamemoderun %command% -windowed'.
    Returns (env, wrapper, args): variables, programs to put in front, program arguments."""
    try:
        tokens = shlex.split(text or "")
    except ValueError:
        tokens = (text or "").split()
    env, wrapper = {}, []
    if "%command%" in tokens:
        i = tokens.index("%command%")
        before, args = tokens[:i], tokens[i + 1:]
    else:
        n = 0
        while n < len(tokens) and _ENV_TOKEN.match(tokens[n]):
            n += 1
        before, args = tokens[:n], tokens[n:]
    for t in before:
        if _ENV_TOKEN.match(t) and not wrapper:
            k, v = t.split("=", 1)
            env[k] = v
        else:
            wrapper.append(t)
    return env, wrapper, args


def build_command(exe, settings=None, runner=None, extra_args=None, debug=False):
    """Return (argv, env, cwd) to start the program."""
    exe = Path(store.exe_key(exe))
    settings = settings or store.get_exe_settings(exe)
    runner = runner or store.effective_runner(exe)
    opt_env, wrapper, opt_args = parse_launch_options(settings.get("launch_options", ""))
    args = opt_args + list(extra_args or [])
    prefix = prefix_dir(exe, runner, settings.get("prefix", "shared"))
    prefix.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(opt_env)
    ext = exe.suffix.lower()
    if ext == ".msi":
        target = ["msiexec", "/i", str(exe)]
    elif ext in (".bat", ".cmd"):
        target = ["cmd", "/c", str(exe)]
    else:
        target = [str(exe)]

    if runner == "proton":
        umu = shutil.which("umu-run")
        if not umu:
            raise LaunchError("Proton is not available: umu-launcher is not installed. "
                              "Ask DeepSeek Shell to install it, or switch this program to Wine.")
        env.update({"WINEPREFIX": str(prefix), "GAMEID": env.get("GAMEID", "umu-default"),
                    "PROTONPATH": proton_path(settings.get("proton", "builtin"))})
        if debug:
            env["UMU_LOG"] = "debug"
        argv = wrapper + [umu] + target + args
    else:
        wine = shutil.which("wine")
        if not wine:
            raise LaunchError("Wine is not installed. Ask DeepSeek Shell to install it, "
                              "or switch this program to Proton.")
        env["WINEPREFIX"] = str(prefix)
        if not debug:
            env.setdefault("WINEDEBUG", "-all")
        argv = wrapper + [wine] + target + args
    return argv, env, str(exe.parent)


def first_proton_run() -> bool:
    """True when umu still has to download the Steam runtime (first Proton start)."""
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "umu"
    return not any(data.glob("steamrt*")) if data.is_dir() else True


def launch(exe, settings=None, extra_args=None, debug=False, runner=None) -> int:
    argv, env, cwd = build_command(exe, settings, runner=runner, extra_args=extra_args, debug=debug)
    if debug:
        print("Running:", " ".join(shlex.quote(a) for a in argv), flush=True)
        print("WINEPREFIX=" + env.get("WINEPREFIX", ""), "PROTONPATH=" + env.get("PROTONPATH", "-"), flush=True)
        return subprocess.call(argv, env=env, cwd=cwd)
    log_dir = store.STATE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / f"{Path(exe).stem[:60]}.log", "w", encoding="utf-8")
    subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                     start_new_session=True)
    return 0
