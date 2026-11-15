"""myos-exe-properties FILE: Windows compatibility settings for one program
(Wine or Proton, Proton version, separate C: drive, launch options).
The Properties dialog's "Windows compatibility" tab edits the same settings."""
import subprocess
import sys
from pathlib import Path

from . import launcher, store


def main(argv=None) -> int:
    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtGui import QDesktopServices, QIcon
    from PyQt6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog,
                                 QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
                                 QRadioButton, QVBoxLayout)

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: myos-exe-properties FILE.exe", file=sys.stderr)
        return 2
    path = Path(argv[0]).expanduser().resolve()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setDesktopFileName("myos-security")
    settings = store.get_exe_settings(path)
    default = store.load_security().get("default_runner", "wine")

    dlg = QDialog()
    dlg.setWindowTitle(f"{path.name}: Windows compatibility")
    dlg.setWindowIcon(QIcon.fromTheme("wine", QIcon.fromTheme("application-x-ms-dos-executable")))
    dlg.setMinimumWidth(560)
    lay = QVBoxLayout(dlg)
    title = QLabel(f"<h3>{path.name}</h3><span style='color:gray'>{path.parent}</span>")
    title.setTextFormat(Qt.TextFormat.RichText)
    lay.addWidget(title)

    box = QGroupBox("Run this program with")
    bl = QVBoxLayout(box)
    group = QButtonGroup(dlg)
    rb_default = QRadioButton(f"Default ({'Proton' if default == 'proton' else 'Wine'}, set in ClamAV Security)")
    rb_wine = QRadioButton("Wine: best for normal programs and installers")
    rb_proton = QRadioButton("Proton: best for games (Valve's Steam Play technology, DirectX via Vulkan)")
    for rb, value in ((rb_default, ""), (rb_wine, "wine"), (rb_proton, "proton")):
        rb.setProperty("value", value)
        group.addButton(rb)
        bl.addWidget(rb)
        rb.setChecked(settings.get("runner", "") == value)
    lay.addWidget(box)

    form = QFormLayout()
    proton = QComboBox()
    for value, label in launcher.proton_choices():
        proton.addItem(label, value)
    idx = proton.findData(settings.get("proton", "builtin"))
    proton.setCurrentIndex(idx if idx >= 0 else 0)
    form.addRow("Proton version:", proton)

    prefix = QComboBox()
    prefix.addItem("Shared with other Windows programs", "shared")
    prefix.addItem("Separate, only for this program", "separate")
    prefix.setCurrentIndex(max(0, prefix.findData(settings.get("prefix", "shared"))))
    form.addRow("Windows C: drive:", prefix)

    options = QLineEdit(settings.get("launch_options", ""))
    options.setPlaceholderText("e.g.  PROTON_ENABLE_WAYLAND=1 gamemoderun %command% -windowed")
    form.addRow("Launch options:", options)
    lay.addLayout(form)

    def selected_runner():
        b = group.checkedButton()
        return b.property("value") if b is not None else ""

    def effective():
        return selected_runner() or default

    def update_enabled():
        proton.setEnabled(effective() == "proton")
    group.buttonClicked.connect(lambda *_: update_enabled())
    update_enabled()

    def open_drive():
        current = dict(settings, prefix=prefix.currentData())
        d = launcher.prefix_dir(path, effective(), current["prefix"])
        drive = d / ("pfx/drive_c" if effective() == "proton" else "drive_c")
        target = drive if drive.exists() else d
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    checked = store.when_checked(path)
    note = QLabel("It is checked for viruses the first time it starts (ClamAV, VirusTotal, ANY.RUN), "
                  "and whenever you click Scan for viruses."
                  + (f" Last checked: {checked.replace('T', ' ')[:16]}." if checked else " Not checked yet."))
    note.setWordWrap(True)
    note.setStyleSheet("color: gray;")
    lay.addWidget(note)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                               QDialogButtonBox.StandardButton.Cancel)
    drive_btn = buttons.addButton("Open its C: drive", QDialogButtonBox.ButtonRole.ActionRole)
    scan_btn = buttons.addButton("Scan for viruses", QDialogButtonBox.ButtonRole.ActionRole)
    drive_btn.clicked.connect(open_drive)
    scan_btn.clicked.connect(lambda: subprocess.Popen(["myos-run-exe", "--scan-only", str(path)]))
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)

    if dlg.exec():
        store.set_exe_settings(path, runner=selected_runner(), proton=proton.currentData() or "builtin",
                               prefix=prefix.currentData(), launch_options=options.text().strip())
        return 0
    return 1
