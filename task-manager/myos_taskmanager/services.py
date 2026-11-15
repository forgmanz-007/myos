"""System services (systemd): list, start, stop, restart."""
import shutil
import subprocess
from dataclasses import dataclass

PROPERTIES = "Id,Description,LoadState,ActiveState,SubState,MainPID,UnitFileState"

# Stopping one of these takes the desktop or the whole system down.
PROTECTED = {"dbus", "dbus-broker", "sddm", "display-manager", "systemd-logind", "systemd-journald",
             "systemd-udevd", "polkit", "accounts-daemon"}

_STARTUP = {"enabled": "Automatic", "enabled-runtime": "Automatic", "disabled": "Manual",
            "static": "When needed", "indirect": "When needed", "alias": "When needed",
            "generated": "Automatic", "transient": "Temporary", "masked": "Blocked",
            "masked-runtime": "Blocked", "linked": "Manual", "linked-runtime": "Manual"}


@dataclass
class Service:
    unit: str               # "ollama.service"
    description: str
    status: str             # Running, Stopped, Finished, Failed, Starting, Stopping
    pid: int
    startup: str            # Automatic, Manual, When needed, Blocked ...

    @property
    def name(self) -> str:
        return self.unit[:-len(".service")] if self.unit.endswith(".service") else self.unit

    @property
    def protected(self) -> bool:
        return self.name in PROTECTED or self.name.startswith("user@")


def status_of(active: str, sub: str) -> str:
    if active == "active":
        return "Running" if sub == "running" else "Finished" if sub == "exited" else "Running"
    return {"failed": "Failed", "activating": "Starting", "deactivating": "Stopping",
            "reloading": "Running"}.get(active, "Stopped")


def parse_show(text: str) -> list:
    """Output of `systemctl show UNITS -p ...`: one KEY=VALUE block per unit."""
    services = []
    for block in text.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        unit = props.get("Id", "")
        if not unit.endswith(".service") or props.get("LoadState") == "not-found":
            continue
        pid = props.get("MainPID", "0")
        services.append(Service(unit, props.get("Description", ""),
                                status_of(props.get("ActiveState", ""), props.get("SubState", "")),
                                int(pid) if pid.isdigit() else 0,
                                _STARTUP.get(props.get("UnitFileState", ""), "")))
    return services


def parse_unit_files(text: str) -> dict:
    """Output of `systemctl list-unit-files --type=service --no-legend`: unit → state."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(".service") and "@." not in parts[0]:
            out[parts[0]] = parts[1]
    return out


def _run(argv, timeout=20):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def list_services() -> list:
    """Every service systemd knows; raises RuntimeError if systemd can't be asked."""
    if not shutil.which("systemctl"):
        raise RuntimeError("This system has no systemd, so services can't be listed.")
    code, out, err = _run(["systemctl", "show", "*.service", "--no-pager", f"--property={PROPERTIES}"])
    if code != 0:
        raise RuntimeError(err.strip() or "systemctl did not answer.")
    found = {s.unit: s for s in parse_show(out)}
    _code, files, _err = _run(["systemctl", "list-unit-files", "--type=service", "--no-legend",
                               "--no-pager"])
    states = parse_unit_files(files)
    # Installed, but not used since the PC started: ask systemd to describe them.
    idle = [unit for unit, state in states.items() if unit not in found and state != "alias"]
    if idle:
        code, out, _err = _run(["systemctl", "show", *idle, "--no-pager", f"--property={PROPERTIES}"])
        for service in parse_show(out) if code == 0 else []:
            found.setdefault(service.unit, service)
        for unit in idle:
            found.setdefault(unit, Service(unit, "", "Stopped", 0, _STARTUP.get(states[unit], "")))
    for service in found.values():
        if service.description == service.unit:     # systemd has no description: show none
            service.description = ""
    return sorted(found.values(), key=lambda s: s.name.lower())


def control(unit: str, verb: str) -> tuple:
    """Start, stop or restart a service: (ok, message). The system asks for the
    administrator password itself."""
    if verb not in ("start", "stop", "restart"):
        raise ValueError(verb)
    code, _out, err = _run(["systemctl", verb, unit], timeout=120)
    if code == 0:
        return True, ""
    err = err.strip()
    if "authentication" in err.lower() or "access denied" in err.lower() or "not authorized" in err.lower():
        return False, "It was not allowed (no administrator password was given)."
    return False, err or f"systemctl {verb} failed."
