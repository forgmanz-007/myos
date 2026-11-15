"""ClamAV Security: protection status, scans, .exe protection settings
(VirusTotal / ANY.RUN keys, Wine or Proton by default), quarantine, history."""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QListWidget, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                             QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import launcher, store
from .scanners import AnyRun, VirusTotal, clamav_scan_tree, clamav_version, signature_age_days

PAGES = ["Protection", "Scan", "Windows programs", "Quarantine", "History"]
UPDATE_HELPER = "/usr/libexec/myos-security/update-signatures"


class Bridge(QObject):
    status = pyqtSignal(object)
    hit = pyqtSignal(object)
    scan_done = pyqtSignal(object)
    key_test = pyqtSignal(object)
    updated = pyqtSignal(object)


def _service_active(name: str) -> str:
    try:
        return subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class SecurityWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClamAV Security")
        self.resize(900, 620)
        self.security = store.load_security()
        self.bridge = Bridge()
        self.bridge.status.connect(self._show_status)
        self.bridge.hit.connect(self._add_hit)
        self.bridge.scan_done.connect(self._scan_finished)
        self.bridge.key_test.connect(self._key_tested)
        self.bridge.updated.connect(self._signatures_updated)
        self.scan_cancel = None
        self.scan_started = 0.0

        root = QWidget()
        lay = QHBoxLayout(root)
        self.nav = QListWidget()
        self.nav.addItems(PAGES)
        self.nav.setFixedWidth(190)
        self.stack = QStackedWidget()
        for build in (self._status_page, self._scan_page, self._exe_page, self._quarantine_page,
                      self._history_page):
            self.stack.addWidget(build())
        self.nav.currentRowChanged.connect(self._page_changed)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.nav.setCurrentRow(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._scan_tick)

    def _page_changed(self, row: int):
        self.stack.setCurrentIndex(row)
        if row == 0:
            self.refresh_status()
        elif row == 3:
            self._fill_quarantine()
        elif row == 4:
            self._fill_history()

    def go(self, page: str):
        names = {"status": 0, "scan": 1, "exe": 2, "quarantine": 3, "history": 4}
        self.nav.setCurrentRow(names.get(page, 0))

    # ── Protection ────────────────────────────────────────────────────────
    def _status_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.status_head = QLabel("<h2>Checking…</h2>")
        self.status_head.setTextFormat(Qt.TextFormat.RichText)
        self.status_list = QLabel()
        self.status_list.setTextFormat(Qt.TextFormat.RichText)
        self.status_list.setWordWrap(True)
        lay.addWidget(self.status_head)
        lay.addWidget(self.status_list)
        row = QHBoxLayout()
        quick = QPushButton("Quick scan")
        quick.clicked.connect(lambda: (self.go("scan"), self.start_scan(self.quick_paths())))
        self.update_btn = QPushButton("Update virus signatures")
        self.update_btn.clicked.connect(self.update_signatures)
        keys = QPushButton("Set up online checks…")
        keys.clicked.connect(lambda: self.go("exe"))
        for b in (quick, self.update_btn, keys):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return w

    def refresh_status(self):
        def work():
            version = clamav_version()
            info = {
                "version": version,
                "age": signature_age_days(version),
                "freshclam": _service_active("clamav-freshclam"),
                "daemon": _service_active("clamav-daemon"),
                "socket": _service_active("clamav-daemon.socket"),
            }
            self.bridge.status.emit(info)
        threading.Thread(target=work, daemon=True).start()

    def _show_status(self, info):
        s = store.load_security()
        ok, items = True, []

        def line(good, text):
            nonlocal ok
            ok = ok and good is not False
            mark = "✅" if good else ("➖" if good is None else "⚠️")
            items.append(f"<p>{mark} {text}</p>")

        if info["version"]:
            parts = info["version"].split("/")
            age = info["age"]
            line(age is not None and age <= 3,
                 f"ClamAV {parts[0].replace('ClamAV ', '')} · virus signatures "
                 + (("from today" if age == 0 else "from yesterday" if age == 1 else
                     f"from {age} days ago") if age is not None else "date unknown")
                 + ("" if age is None or age <= 3 else " (out of date: click Update)"))
        else:
            line(False, "ClamAV is not installed.")
        line(info["freshclam"] == "active", "Automatic signature updates: "
             + ("on" if info["freshclam"] == "active" else f"not running ({info['freshclam']})"))
        scanner = "running" if info["daemon"] == "active" else (
            "starts when needed" if info["socket"] == "active" else "not available (slower scans)")
        line(info["daemon"] == "active" or info["socket"] == "active", f"Fast scanner service: {scanner}")
        line(bool(s.get("check_exe", True)),
             ("Windows programs (.exe) are checked every time they start" if s.get("check_every_start")
              else "Windows programs (.exe) are checked the first time they start")
             if s.get("check_exe", True) else "Checking Windows programs before they run is OFF")
        line(True if s.get("virustotal_key") else None,
             "VirusTotal online check: " + ("on" if s.get("virustotal_key") else "not set up (free key)"))
        line(True if s.get("anyrun_key") else None,
             "ANY.RUN sandbox check: " + ("on" if s.get("anyrun_key") else "not set up (needs an ANY.RUN API plan)"))
        self.status_head.setText("<h2>🛡 Your PC is protected</h2>" if ok else
                                 "<h2>⚠️ Something needs attention</h2>")
        self.status_list.setText("".join(items))

    def update_signatures(self):
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Updating…")

        def work():
            if os.path.exists(UPDATE_HELPER) and shutil.which("pkexec"):
                r = subprocess.run(["pkexec", UPDATE_HELPER], capture_output=True, text=True, timeout=600)
                self.bridge.updated.emit((r.returncode, (r.stdout + r.stderr).strip()))
            else:
                self.bridge.updated.emit((1, "The update helper is not installed."))
        threading.Thread(target=work, daemon=True).start()

    def _signatures_updated(self, result):
        code, text = result
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Update virus signatures")
        if code != 0:
            QMessageBox.warning(self, "Update", f"The update did not finish.\n\n{text[-600:]}")
        self.refresh_status()

    # ── Scan ──────────────────────────────────────────────────────────────
    def quick_paths(self) -> list:
        home = Path.home()
        return [str(p) for p in (home / "Downloads", home / "Desktop", store.PREFIX_DIR) if p.exists()]

    def _scan_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<h2>Scan for viruses</h2>"))
        row = QHBoxLayout()
        quick = QPushButton("Quick scan")
        quick.setToolTip("Downloads, Desktop and your Windows programs")
        quick.clicked.connect(lambda: self.start_scan(self.quick_paths()))
        full = QPushButton("Full scan")
        full.setToolTip("Your whole home folder (can take a long time)")
        full.clicked.connect(lambda: self.start_scan([str(Path.home())]))
        custom = QPushButton("Choose a folder…")
        custom.clicked.connect(self._choose_scan)
        self.stop_scan_btn = QPushButton("Stop")
        self.stop_scan_btn.clicked.connect(lambda: self.scan_cancel and self.scan_cancel.set())
        self.stop_scan_btn.hide()
        for b in (quick, full, custom, self.stop_scan_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.scan_label = QLabel("")
        lay.addWidget(self.scan_label)
        self.hits = QTableWidget(0, 2)
        self.hits.setHorizontalHeaderLabels(["File", "Threat"])
        self.hits.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.hits.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.hits.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.hits, 1)
        actions = QHBoxLayout()
        q = QPushButton("Quarantine selected")
        q.clicked.connect(self._quarantine_selected)
        actions.addWidget(q)
        actions.addStretch(1)
        lay.addLayout(actions)
        return w

    def _choose_scan(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to scan", str(Path.home()))
        if folder:
            self.start_scan([folder])

    def start_scan(self, targets: list):
        if self.scan_cancel is not None or not targets:
            return
        self.hits.setRowCount(0)
        self.scan_cancel = threading.Event()
        self.scan_started = time.monotonic()
        self.stop_scan_btn.show()
        self.timer.start(1000)
        self._scan_tick()

        def work(cancel=self.scan_cancel):
            codes, errors = [], []
            for t in targets:
                if cancel.is_set():
                    break
                code, err = clamav_scan_tree(t, lambda f, threat: self.bridge.hit.emit((f, threat)), cancel)
                codes.append(code)
                if err:
                    errors.append(err)
            self.bridge.scan_done.emit((targets, codes, errors, cancel.is_set()))
        threading.Thread(target=work, daemon=True).start()

    def _scan_tick(self):
        secs = int(time.monotonic() - self.scan_started)
        self.scan_label.setText(f"Scanning… {secs // 60}:{secs % 60:02d}  ·  {self.hits.rowCount()} threat(s) so far")

    def _add_hit(self, hit):
        f, threat = hit
        store.forget_checked(f)         # a program with a threat is checked again before it runs
        r = self.hits.rowCount()
        self.hits.insertRow(r)
        self.hits.setItem(r, 0, QTableWidgetItem(f))
        self.hits.setItem(r, 1, QTableWidgetItem(threat))

    def _scan_finished(self, result):
        targets, codes, errors, cancelled = result
        self.timer.stop()
        self.scan_cancel = None
        self.stop_scan_btn.hide()
        n = self.hits.rowCount()
        if cancelled:
            text = f"Scan stopped. {n} threat(s) found so far."
        elif n:
            text = f"⚠️ {n} threat(s) found. Quarantine them below."
        elif any(c not in (0, 1) for c in codes):
            text = "The scan could not finish: " + "; ".join(errors)[:300]
        else:
            text = "✅ No threats found."
        self.scan_label.setText(text)
        store.add_history(", ".join(targets), "", "block" if n else "clean", text, "folder scan")

    def _quarantine_selected(self):
        rows = sorted({i.row() for i in self.hits.selectedIndexes()}, reverse=True)
        for r in rows:
            f, threat = self.hits.item(r, 0).text(), self.hits.item(r, 1).text()
            try:
                from .scanners import sha256_file
                store.quarantine(f, sha256_file(f), threat)
                self.hits.removeRow(r)
            except OSError as e:
                QMessageBox.warning(self, "Quarantine", f"{f}: {e}")

    # ── Windows programs ──────────────────────────────────────────────────
    def _exe_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<h2>Windows programs (.exe)</h2>"))
        s = self.security
        self.check_exe = QCheckBox("Check Windows programs for viruses the first time they start (recommended)")
        self.check_exe.setChecked(bool(s.get("check_exe", True)))
        self.every_start = QCheckBox("Check them again every time they start")
        self.every_start.setChecked(bool(s.get("check_every_start", False)))
        self.every_start.setEnabled(self.check_exe.isChecked())
        self.check_exe.toggled.connect(self.every_start.setEnabled)
        every_note = QLabel("A program that changes (an update) is checked again once. A file found "
                            "dangerous is checked every time. To check a program again any time: "
                            "right-click it > Windows program > Scan for viruses.")
        every_note.setWordWrap(True)
        every_note.setStyleSheet("color: gray;")
        self.auto_start = QCheckBox("Start it right away when all checks are clean")
        self.auto_start.setChecked(bool(s.get("auto_start_clean", True)))
        lay.addWidget(self.check_exe)
        lay.addWidget(self.every_start)
        lay.addWidget(every_note)
        lay.addWidget(self.auto_start)

        vt = QGroupBox("VirusTotal (checks the file's fingerprint against 70+ antivirus engines)")
        vf = QFormLayout(vt)
        self.vt_key = QLineEdit(s.get("virustotal_key", ""))
        self.vt_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.vt_key.setPlaceholderText("Paste your API key")
        vt_row = QHBoxLayout()
        vt_row.addWidget(self.vt_key, 1)
        vt_test = QPushButton("Test")
        vt_test.clicked.connect(lambda: self._test_key("VirusTotal"))
        vt_get = QPushButton("Get a free key")
        vt_get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.virustotal.com/gui/join-us")))
        vt_row.addWidget(vt_test)
        vt_row.addWidget(vt_get)
        vf.addRow("API key:", vt_row)
        self.vt_upload = QComboBox()
        for value, label in (("ask", "Ask me"), ("always", "Always upload"), ("never", "Never upload")):
            self.vt_upload.addItem(label, value)
        self.vt_upload.setCurrentIndex(max(0, self.vt_upload.findData(s.get("vt_upload", "ask"))))
        vf.addRow("Files VirusTotal has never seen:", self.vt_upload)
        self.vt_threshold = QSpinBox()
        self.vt_threshold.setRange(1, 20)
        self.vt_threshold.setValue(int(s.get("vt_block_threshold", 3)))
        vf.addRow("Block when this many engines detect it:", self.vt_threshold)
        privacy = QLabel("Only the fingerprint is sent, unless a file is uploaded. Uploaded files "
                         "can be seen by security researchers, so don't upload private files.")
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: gray;")
        vf.addRow(privacy)
        lay.addWidget(vt)

        ar = QGroupBox("ANY.RUN (runs the program in an online Windows sandbox and watches what it does)")
        af = QFormLayout(ar)
        self.ar_key = QLineEdit(s.get("anyrun_key", ""))
        self.ar_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ar_key.setPlaceholderText("Paste your API key (needs an ANY.RUN plan with API access)")
        ar_row = QHBoxLayout()
        ar_row.addWidget(self.ar_key, 1)
        ar_test = QPushButton("Test")
        ar_test.clicked.connect(lambda: self._test_key("ANY.RUN"))
        ar_get = QPushButton("ANY.RUN website")
        ar_get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://app.any.run/")))
        ar_row.addWidget(ar_test)
        ar_row.addWidget(ar_get)
        af.addRow("API key:", ar_row)
        self.ar_mode = QComboBox()
        for value, label in (("ask", "Ask me"), ("always", "Always (slow: a few minutes)"),
                             ("never", "Never")):
            self.ar_mode.addItem(label, value)
        self.ar_mode.setCurrentIndex(max(0, self.ar_mode.findData(s.get("anyrun_mode", "ask"))))
        af.addRow("Sandbox-test unknown files:", self.ar_mode)
        self.ar_privacy = QComboBox()
        for value, label in (("bylink", "Only people with the link"), ("owner", "Only me"),
                             ("byteam", "My team"), ("public", "Public")):
            self.ar_privacy.addItem(label, value)
        self.ar_privacy.setCurrentIndex(max(0, self.ar_privacy.findData(s.get("anyrun_privacy", "bylink"))))
        af.addRow("Who can see sandbox reports:", self.ar_privacy)
        lay.addWidget(ar)

        run = QGroupBox("How Windows programs start")
        rf = QFormLayout(run)
        self.default_runner = QComboBox()
        self.default_runner.addItem("Wine (best for normal programs)", "wine")
        self.default_runner.addItem("Proton (best for games)", "proton")
        self.default_runner.setCurrentIndex(max(0, self.default_runner.findData(s.get("default_runner", "wine"))))
        rf.addRow("Default for new programs:", self.default_runner)
        b = launcher.builtin_proton()
        umu = shutil.which("umu-run")
        info = QLabel(f"Built-in Proton: {b.name if b else 'none (downloads GE-Proton when needed)'} · "
                      f"Proton launcher (umu): {'installed' if umu else 'missing'}. "
                      "Switch a single program: right-click it > Properties > Windows compatibility.")
        info.setWordWrap(True)
        info.setStyleSheet("color: gray;")
        rf.addRow(info)
        lay.addWidget(run)

        self.key_result = QLabel()
        self.key_result.setWordWrap(True)
        lay.addWidget(self.key_result)
        save = QPushButton("Save")
        save.clicked.connect(self._save_exe_settings)
        lay.addWidget(save, 0, Qt.AlignmentFlag.AlignRight)
        lay.addStretch(1)
        # A long page scrolls instead of squeezing its boxes (their last lines were cut off).
        scroll = QScrollArea()
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    def _test_key(self, which: str):
        self.key_result.setText(f"Testing {which}…")

        def work():
            if which == "VirusTotal":
                err = VirusTotal(self.vt_key.text()).test_key() if self.vt_key.text().strip() else "No key entered."
            else:
                err = AnyRun(self.ar_key.text()).test_key() if self.ar_key.text().strip() else "No key entered."
            self.bridge.key_test.emit((which, err))
        threading.Thread(target=work, daemon=True).start()

    def _key_tested(self, result):
        which, err = result
        self.key_result.setText(f"✅ {which}: the key works." if not err else f"⚠️ {which}: {err}")

    def _save_exe_settings(self):
        s = store.load_security()
        s.update({
            "check_exe": self.check_exe.isChecked(),
            "check_every_start": self.every_start.isChecked(),
            "auto_start_clean": self.auto_start.isChecked(),
            "virustotal_key": self.vt_key.text().strip(),
            "vt_upload": self.vt_upload.currentData(),
            "vt_block_threshold": self.vt_threshold.value(),
            "anyrun_key": self.ar_key.text().strip(),
            "anyrun_mode": self.ar_mode.currentData(),
            "anyrun_privacy": self.ar_privacy.currentData(),
            "default_runner": self.default_runner.currentData(),
        })
        store.save_security(s)
        self.security = s
        self.key_result.setText("Saved.")

    # ── Quarantine & History ──────────────────────────────────────────────
    def _quarantine_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<h2>Quarantine</h2>Files moved here can't run. Restore a file only "
                             "if you are sure it is safe."))
        self.qtable = QTableWidget(0, 3)
        self.qtable.setHorizontalHeaderLabels(["File", "Why", "Date"])
        self.qtable.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.qtable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.qtable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.qtable, 1)
        row = QHBoxLayout()
        restore = QPushButton("Restore")
        restore.clicked.connect(self._restore)
        delete = QPushButton("Delete forever")
        delete.clicked.connect(self._delete_quarantined)
        row.addWidget(restore)
        row.addWidget(delete)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def _fill_quarantine(self):
        items = store.quarantine_items()
        self.qtable.setRowCount(0)
        for item in items:
            r = self.qtable.rowCount()
            self.qtable.insertRow(r)
            name = QTableWidgetItem(item.get("original", ""))
            name.setData(Qt.ItemDataRole.UserRole, item.get("id"))
            self.qtable.setItem(r, 0, name)
            self.qtable.setItem(r, 1, QTableWidgetItem(item.get("threat", "")))
            self.qtable.setItem(r, 2, QTableWidgetItem(item.get("date", "")))

    def _selected_quarantine(self):
        r = self.qtable.currentRow()
        return self.qtable.item(r, 0).data(Qt.ItemDataRole.UserRole) if r >= 0 else None

    def _restore(self):
        item_id = self._selected_quarantine()
        if not item_id:
            return
        if QMessageBox.question(self, "Restore", "Restore this file? It was quarantined because it "
                                "looked dangerous.") != QMessageBox.StandardButton.Yes:
            return
        try:
            dest = store.restore_quarantined(item_id)
            QMessageBox.information(self, "Restore", f"Restored to {dest}")
        except OSError as e:
            QMessageBox.warning(self, "Restore", str(e))
        self._fill_quarantine()

    def _delete_quarantined(self):
        item_id = self._selected_quarantine()
        if item_id and QMessageBox.question(self, "Delete", "Delete this file forever?") == \
                QMessageBox.StandardButton.Yes:
            store.delete_quarantined(item_id)
            self._fill_quarantine()

    def _history_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("<h2>History</h2>"))
        self.htable = QTableWidget(0, 4)
        self.htable.setHorizontalHeaderLabels(["When", "File", "Result", "What happened"])
        self.htable.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.htable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.htable, 1)
        return w

    def _fill_history(self):
        labels = {"clean": "✅ clean", "warn": "⚠️ warning", "block": "⛔ dangerous"}
        self.htable.setRowCount(0)
        for item in store.read_history():
            r = self.htable.rowCount()
            self.htable.insertRow(r)
            self.htable.setItem(r, 0, QTableWidgetItem(item.get("time", "").replace("T", " ")))
            self.htable.setItem(r, 1, QTableWidgetItem(item.get("path", "")))
            self.htable.setItem(r, 2, QTableWidgetItem(labels.get(item.get("result"), item.get("result", ""))))
            self.htable.setItem(r, 3, QTableWidgetItem(f"{item.get('action', '')}: {item.get('summary', '')}"))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("myos-security")
    app.setApplicationDisplayName("ClamAV Security")
    app.setDesktopFileName("myos-security")
    app.setWindowIcon(QIcon.fromTheme("myos-security"))
    win = SecurityWindow()
    if "--page" in argv and argv.index("--page") + 1 < len(argv):
        win.go(argv[argv.index("--page") + 1])
    win.show()
    if "--scan" in argv and argv.index("--scan") + 1 < len(argv):
        win.go("scan")
        win.start_scan([argv[argv.index("--scan") + 1]])
    return app.exec()
