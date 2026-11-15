"""myos-run-exe: check a Windows program for viruses, then run it with Wine or
Proton. This is what happens when you double-click an .exe file.

A program is checked the first time it starts; after that it starts right away, until
it changes (an update counts as a new program). A file found dangerous is checked at
every start. Scans you start yourself (--scan-only, myos-scan, the app) always run."""
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import launcher, store
from .checks import ENGINES, RUNNING, check_file
from .scanners import (CLEAN, ERROR, MALICIOUS, NOT_SET_UP, SKIPPED, SUSPICIOUS, UNKNOWN, AnyRun,
                       Finding, VirusTotal, decide)

ICONS = {RUNNING: "⏳", CLEAN: "✅", MALICIOUS: "⛔", SUSPICIOUS: "⚠️", UNKNOWN: "❔",
         ERROR: "⚠️", NOT_SET_UP: "➖", SKIPPED: "➖"}
COLORS = {"clean": ("#e6f6ea", "#1e6b35"), "warn": ("#fff4d6", "#6b4f00"), "block": ("#fde4df", "#8a2410")}


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def notify(title: str, text: str) -> None:
    try:
        subprocess.Popen(["notify-send", "-a", "ClamAV Security", "-i", "myos-security", title, text],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


RUNNER_OVERRIDE = {"value": None}  # set by --runner=wine|proton (this start only)


def record(path: Path, sha: str, level: str, started: bool) -> None:
    """Remember the result for the next start: a program that was started (and is not
    dangerous) or found clean is not checked again; anything else is."""
    if level in ("clean", "warn") and (started or level == "clean"):
        store.remember_checked(path, sha, level)
    elif level == "block" or not started:
        store.forget_checked(path)


def start_program(path: Path, extra_args, debug=False) -> int:
    runner = RUNNER_OVERRIDE["value"] or store.effective_runner(path)
    if runner == "proton" and launcher.first_proton_run():
        notify("Preparing Proton", "First start with Proton: downloading the Steam runtime "
               "(about 500 MB). The program opens when it's ready.")
    try:
        return launcher.launch(path, extra_args=extra_args, debug=debug, runner=runner)
    except launcher.LaunchError as e:
        notify("Cannot start the program", str(e))
        print(e, file=sys.stderr)
        return 1


# ── text mode (no desktop, e.g. a console) ──────────────────────────────────
def run_text(path: Path, extra_args, debug: bool) -> int:
    security = store.load_security()
    print(f"Checking {path.name} for viruses…")
    sha, findings = check_file(path, security,
                               on_update=lambda f: f.status != RUNNING and
                               print(f"  {f.engine:10} {f.summary}"))
    verdict = decide(findings)
    print(f"Result: {verdict.headline}")
    for r in verdict.reasons:
        print(f"  - {r}")
    if verdict.level != "clean":
        if not sys.stdin.isatty():
            store.add_history(str(path), sha, verdict.level, verdict.headline, "not started")
            return 1
        prompt = "Type RUN to start it anyway: " if verdict.level == "block" else "Start it anyway? [y/N] "
        answer = input(prompt).strip()
        if (verdict.level == "block" and answer != "RUN") or \
                (verdict.level != "block" and answer.lower() not in ("y", "yes")):
            store.add_history(str(path), sha, verdict.level, verdict.headline, "not started")
            record(path, sha, verdict.level, started=False)
            return 1
    store.add_history(str(path), sha, verdict.level, verdict.headline, "started")
    record(path, sha, verdict.level, started=True)
    return start_program(path, extra_args, debug)


# ── window ──────────────────────────────────────────────────────────────────
def run_gui(path: Path, extra_args, debug: bool, scan_only: bool = False) -> int:
    from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
    from PyQt6.QtGui import QDesktopServices, QIcon
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QFrame, QGridLayout,
                                 QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout)

    class Bridge(QObject):
        finding = pyqtSignal(object)
        finished = pyqtSignal(object)
        extra = pyqtSignal(object)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("myos-run-exe")
    app.setDesktopFileName("myos-security")
    icon = QIcon.fromTheme("myos-security")
    security = store.load_security()
    state = {"sha": "", "findings": {}, "verdict": None, "action": "cancelled"}
    cancel = threading.Event()
    bridge = Bridge()

    dlg = QDialog()
    dlg.setWindowTitle(f"Checking {path.name}" if not scan_only else f"Scan: {path.name}")
    dlg.setWindowIcon(icon)
    dlg.setMinimumWidth(620)
    lay = QVBoxLayout(dlg)

    head = QLabel(f"<h3>{'Checking' if not scan_only else 'Scanning'} “{path.name}”"
                  f"{' before it runs' if not scan_only else ''}</h3>"
                  f"<span style='color:gray'>{_size(path.stat().st_size)} · {path.parent}</span>")
    head.setTextFormat(Qt.TextFormat.RichText)
    head.setWordWrap(True)
    lay.addWidget(head)

    grid = QGridLayout()
    rows = {}
    for i, engine in enumerate(ENGINES):
        icon_l, name_l, text_l = QLabel("⏳"), QLabel(f"<b>{engine}</b>"), QLabel("Waiting…")
        text_l.setWordWrap(True)
        link = QPushButton("Report")
        link.setFlat(True)
        link.hide()
        grid.addWidget(icon_l, i, 0)
        grid.addWidget(name_l, i, 1)
        grid.addWidget(text_l, i, 2)
        grid.addWidget(link, i, 3)
        rows[engine] = (icon_l, text_l, link)
    grid.setColumnStretch(2, 1)
    lay.addLayout(grid)

    banner = QLabel("Checking…")
    banner.setWordWrap(True)
    banner.setTextFormat(Qt.TextFormat.RichText)
    banner.setStyleSheet("padding: 10px; border-radius: 8px; background: palette(alternate-base);")
    lay.addWidget(banner)

    extras = QHBoxLayout()
    vt_upload = QPushButton("Upload to VirusTotal")
    sandbox = QPushButton("Test in ANY.RUN sandbox")
    setup = QPushButton("Set up online checks…")
    for b in (vt_upload, sandbox, setup):
        b.hide()
        extras.addWidget(b)
    extras.addStretch(1)
    lay.addLayout(extras)

    runner_line = QLabel()
    runner_line.setTextFormat(Qt.TextFormat.RichText)
    lay.addWidget(runner_line)

    def refresh_runner():
        r = store.effective_runner(path)
        runner_line.setText(f"Runs with: <b>{'Proton' if r == 'proton' else 'Wine'}</b> &nbsp; "
                            f"<a href='compat'>Change…</a>")
    refresh_runner()
    runner_line.setVisible(not scan_only)

    def open_compat(_link=""):
        subprocess.call([sys.executable, "-m", "myos_security", "properties", str(path)],
                        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
        refresh_runner()
    runner_line.linkActivated.connect(open_compat)

    risk = QCheckBox("I understand the risk and still want to run this file")
    risk.hide()
    lay.addWidget(risk)

    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    lay.addWidget(sep)
    buttons = QHBoxLayout()
    quarantine_btn = QPushButton("Quarantine")
    delete_btn = QPushButton("Delete")
    cancel_btn = QPushButton("Close" if scan_only else "Cancel")
    run_btn = QPushButton("Run")
    run_btn.setEnabled(False)
    run_btn.setVisible(not scan_only)
    quarantine_btn.hide()
    delete_btn.hide()
    for b in (quarantine_btn, delete_btn):
        buttons.addWidget(b)
    buttons.addStretch(1)
    buttons.addWidget(cancel_btn)
    buttons.addWidget(run_btn)
    lay.addLayout(buttons)

    def show_finding(f):
        icon_l, text_l, link = rows[f.engine]
        icon_l.setText(ICONS.get(f.status, "❔"))
        text_l.setText(f.summary)
        state["findings"][f.engine] = f
        if f.link:
            link.show()
            try:
                link.clicked.disconnect()
            except TypeError:
                pass
            link.clicked.connect(lambda _=False, u=f.link: QDesktopServices.openUrl(QUrl(u)))

    def show_verdict(_=None):
        findings = [state["findings"][e] for e in ENGINES if e in state["findings"]]
        v = decide(findings)
        state["verdict"] = v
        bg, fg = COLORS[v.level]
        reasons = "".join(f"<li>{r}</li>" for r in v.reasons)
        banner.setStyleSheet(f"padding: 10px; border-radius: 8px; background: {bg}; color: {fg};")
        banner.setText(f"<b>{v.headline}</b>" + (f"<ul>{reasons}</ul>" if reasons else ""))
        vt = state["findings"].get("VirusTotal")
        ar = state["findings"].get("ANY.RUN")
        vt_upload.setVisible(bool(vt and vt.status == UNKNOWN and security.get("vt_upload") != "never"))
        sandbox.setVisible(bool(ar and ar.status in (UNKNOWN,) and security.get("anyrun_mode") != "never"))
        setup.setVisible(any(f.status == NOT_SET_UP for f in findings))
        quarantine_btn.setVisible(v.level != "clean")
        delete_btn.setVisible(v.level == "block")
        if v.level == "block":
            quarantine_btn.setDefault(True)
            risk.setVisible(not scan_only)
            run_btn.setText("Run anyway")
            run_btn.setEnabled(False)
        else:
            run_btn.setText("Run" if v.level == "clean" else "Run anyway")
            run_btn.setEnabled(True)
            run_btn.setDefault(v.level == "clean")
        return v

    def checks_done(result):
        state["sha"] = result
        v = show_verdict()
        if v.level == "clean" and not scan_only and security.get("auto_start_clean", True) and \
                not vt_upload.isVisible() and not sandbox.isVisible():
            banner.setText("<b>No threats found.</b> Starting…")
            QTimer.singleShot(900, do_run)
        elif scan_only or v.level != "clean":
            dlg.raise_()
        auto_followups()

    def auto_followups():
        vt = state["findings"].get("VirusTotal")
        ar = state["findings"].get("ANY.RUN")
        if vt and vt.status == UNKNOWN and security.get("vt_upload") == "always":
            do_vt_upload()
        if ar and ar.status == UNKNOWN and security.get("anyrun_mode") == "always" and \
                security.get("anyrun_key"):
            do_sandbox()

    def worker():
        sha, _ = check_file(path, security, on_update=bridge.finding.emit, cancel=cancel)
        bridge.finished.emit(sha)

    def do_vt_upload():
        vt_upload.setEnabled(False)
        vt_upload.setText("Uploading…")
        show_finding(Finding("VirusTotal", RUNNING, "Uploading and analysing (1–5 minutes)…"))

        def work():
            f = VirusTotal(security.get("virustotal_key", ""),
                           security.get("vt_block_threshold", 3)).upload_and_wait(path, state["sha"], cancel)
            if f.status in (CLEAN, MALICIOUS, SUSPICIOUS):
                store.cache_put(state["sha"], "VirusTotal", f.to_dict())
            bridge.extra.emit(f)
        threading.Thread(target=work, daemon=True).start()

    def do_sandbox():
        sandbox.setEnabled(False)
        sandbox.setText("Analysing…")
        seconds = int(security.get("anyrun_timeout", 120))
        show_finding(Finding("ANY.RUN", RUNNING,
                             f"Running it in a Windows sandbox (about {seconds // 60 + 2} minutes)…"))

        def work():
            f = AnyRun(security.get("anyrun_key", "")).submit_and_wait(
                path, security.get("anyrun_privacy", "bylink"), seconds, cancel)
            if f.status in (CLEAN, MALICIOUS, SUSPICIOUS):
                store.cache_put(state["sha"], "ANY.RUN", f.to_dict())
            bridge.extra.emit(f)
        if not security.get("anyrun_key"):
            QDesktopServices.openUrl(QUrl("https://app.any.run/"))
            sandbox.setText("Opened ANY.RUN website")
            return
        threading.Thread(target=work, daemon=True).start()

    def extra_done(f):
        show_finding(f)
        show_verdict()

    def do_run():
        v = state["verdict"]
        if v is None or (v.level == "block" and not risk.isChecked()):
            return
        state["action"] = "started"
        dlg.accept()

    def do_quarantine():
        threat = "; ".join(state["verdict"].reasons) if state["verdict"] else ""
        try:
            store.quarantine(path, state["sha"], threat[:300])
        except OSError as e:
            QMessageBox.warning(dlg, "Quarantine", f"Could not move the file: {e}")
            return
        state["action"] = "quarantined"
        QMessageBox.information(dlg, "Quarantine", f"“{path.name}” was moved to quarantine. You can "
                                "restore or delete it in ClamAV Security.")
        dlg.reject()

    def do_delete():
        if QMessageBox.question(dlg, "Delete", f"Delete “{path.name}” permanently?") != \
                QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as e:
            QMessageBox.warning(dlg, "Delete", str(e))
            return
        state["action"] = "deleted"
        dlg.reject()

    bridge.finding.connect(show_finding)
    bridge.finished.connect(checks_done)
    bridge.extra.connect(extra_done)
    risk.toggled.connect(run_btn.setEnabled)
    run_btn.clicked.connect(do_run)
    cancel_btn.clicked.connect(dlg.reject)
    quarantine_btn.clicked.connect(do_quarantine)
    delete_btn.clicked.connect(do_delete)
    vt_upload.clicked.connect(do_vt_upload)
    sandbox.clicked.connect(do_sandbox)
    setup.clicked.connect(lambda: subprocess.Popen(["myos-security", "--page", "exe"]))

    threading.Thread(target=worker, daemon=True).start()
    dlg.exec()
    cancel.set()
    v = state["verdict"]
    store.add_history(str(path), state["sha"], v.level if v else "unfinished",
                      v.headline if v else "check not finished",
                      state["action"] if not scan_only else "scanned")
    if v is not None and state["action"] in ("started", "cancelled"):
        record(path, state["sha"], v.level, started=state["action"] == "started")
    if state["action"] == "started":
        return start_program(path, extra_args, debug)
    return 0 if scan_only else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    debug = "--debug" in argv
    scan_only = "--scan-only" in argv
    for a in argv:
        if a in ("--runner=wine", "--runner=proton"):
            RUNNER_OVERRIDE["value"] = a.split("=", 1)[1]
    argv = [a for a in argv if a not in ("--debug", "--scan-only", "--runner=wine", "--runner=proton")]
    if not argv:
        print("usage: myos-run-exe [--debug] [--scan-only] [--runner=wine|proton] FILE.exe [arguments…]",
              file=sys.stderr)
        return 2
    path = Path(argv[0]).expanduser().resolve()
    extra = argv[1:]
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 2
    security = store.load_security()
    if not security.get("check_exe", True) and not scan_only:
        store.add_history(str(path), "", "skipped", "checks switched off", "started")
        return start_program(path, extra, debug)
    if not scan_only and not security.get("check_every_start", False) and store.checked_before(path):
        return start_program(path, extra, debug)            # checked on its first start
    has_display = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    if debug or not has_display:
        return run_text(path, extra, debug)
    try:
        return run_gui(path, extra, debug, scan_only)
    except ImportError:
        return run_text(path, extra, debug)
