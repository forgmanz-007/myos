"""Run all checks for one file (used by the .exe guard, myos-scan and the app)."""
from . import store
from .scanners import (ERROR, NOT_SET_UP, AnyRun, Finding, VirusTotal, clamav_scan, sha256_file)

ENGINES = ("ClamAV", "VirusTotal", "ANY.RUN")
RUNNING = "running"


def check_file(path, security=None, on_update=None, cancel=None, online=True):
    """Returns (sha256, [Finding]). on_update(Finding) is called as results arrive."""
    security = security or store.load_security()
    results = {}

    def update(f: Finding):
        results[f.engine] = f
        if on_update:
            on_update(f)

    for e in ENGINES if online else ENGINES[:1]:
        update(Finding(e, RUNNING, "Checking…"))
    sha = sha256_file(path)
    update(clamav_scan(path))

    if online:
        minutes = int(security.get("cache_minutes", 60))
        services = (("VirusTotal", security.get("virustotal_key", ""),
                     lambda: VirusTotal(security.get("virustotal_key", ""),
                                        security.get("vt_block_threshold", 3)).lookup(sha)),
                    ("ANY.RUN", security.get("anyrun_key", ""),
                     lambda: AnyRun(security.get("anyrun_key", "")).lookup(sha)))
        for engine, key, fn in services:
            if cancel is not None and cancel.is_set():
                break
            cached = store.cache_get(sha, engine, minutes) if key else None
            if cached:
                f = Finding.from_dict(cached)
                f.summary += " (checked recently)" if "(checked recently)" not in f.summary else ""
            else:
                f = fn()
                if f.status not in (ERROR, NOT_SET_UP):
                    store.cache_put(sha, engine, f.to_dict())
            update(f)
    return sha, [results[e] for e in ENGINES if e in results]
