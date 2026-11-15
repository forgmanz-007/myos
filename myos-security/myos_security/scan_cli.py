"""myos-scan [--cli|--json] PATH...  Scan files or folders for viruses.
Without --cli/--json and with a desktop, a window opens instead."""
import json
import os
import subprocess
import sys
from pathlib import Path

from . import store
from .checks import RUNNING, check_file
from .scanners import clamav_scan_tree, decide


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    as_text = "--cli" in argv or as_json
    paths = [Path(a).expanduser() for a in argv if not a.startswith("--")]
    if not paths:
        print("usage: myos-scan [--cli|--json] FILE_OR_FOLDER...", file=sys.stderr)
        return 2
    has_display = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    if not as_text and has_display:
        p = paths[0].resolve()
        if p.is_file():
            from .guard import run_gui
            return run_gui(p, [], False, scan_only=True)
        return subprocess.call(["myos-security", "--scan", str(p)])

    security = store.load_security()
    report, worst = [], 0
    for p in paths:
        p = p.resolve()
        if not p.exists():
            report.append({"path": str(p), "error": "not found"})
            worst = max(worst, 2)
            continue
        if p.is_file():
            sha, findings = check_file(p, security)
            v = decide(findings)
            store.add_history(str(p), sha, v.level, v.headline, "scanned")
            if v.level == "clean":
                store.remember_checked(p, sha, v.level)
            else:
                store.forget_checked(p)
            report.append({"path": str(p), "sha256": sha, "result": v.level, "headline": v.headline,
                           "reasons": v.reasons,
                           "checks": [f.to_dict() for f in findings if f.status != RUNNING]})
            worst = max(worst, {"clean": 0, "warn": 1, "block": 1}[v.level])
        else:
            hits = []
            code, err = clamav_scan_tree(p, lambda f, t: hits.append({"file": f, "threat": t}))
            for hit in hits:
                store.forget_checked(hit["file"])           # checked again before it can run
            report.append({"path": str(p), "result": "block" if hits else ("clean" if code == 0 else "error"),
                           "threats": hits, "error": err if code not in (0, 1) else ""})
            worst = max(worst, 1 if hits else (0 if code == 0 else 2))
            store.add_history(str(p), "", "block" if hits else "clean",
                              f"{len(hits)} threat(s) found" if hits else "No threats found", "folder scan")
    if as_json:
        print(json.dumps(report, indent=2))
        return worst
    for item in report:
        print(item["path"])
        if item.get("error") and "result" not in item:
            print(f"  error: {item['error']}")
            continue
        if "checks" in item:
            for c in item["checks"]:
                print(f"  {c['engine']:10} {c['summary']}" + (f"  {c['link']}" if c.get("link") else ""))
            print(f"  => {item['headline']}")
            for r in item["reasons"]:
                print(f"     - {r}")
        else:
            if item["threats"]:
                for h in item["threats"]:
                    print(f"  THREAT {h['threat']}: {h['file']}")
            print(f"  => {len(item['threats'])} threat(s) found" if item["threats"] else
                  ("  => No threats found" if item["result"] == "clean" else f"  => error: {item['error']}"))
    return worst
