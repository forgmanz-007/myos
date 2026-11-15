"""deepseek-shell --run-script FILE: run one of your scripts in a small window
that shows what it prints. If it fails, one click asks the AI to fix it."""
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess, Qt, QTimer
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                             QPushButton, QVBoxLayout)

from .widgets import mono_font


def describe_script(path: Path) -> str:
    """The description line the AI wrote at the top of the script."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:6]
    except OSError:
        return ""
    for line in lines[1:]:
        if line.startswith("# ") and "Made with DeepSeek Shell" not in line:
            return line[2:].strip()
    return ""


def send_to_ai(kind: str, text: str) -> None:
    """Open DeepSeek Shell with a question (kind 'ask') or a prefilled message ('prefill')."""
    flag = "--ask" if kind == "ask" else "--prefill"
    QProcess.startDetached(sys.executable, ["-m", "deepseek_shell", flag, text])


class ScriptRunner(QDialog):
    def __init__(self, path: Path, args=None):
        super().__init__()
        self.path = path
        self.args = list(args or [])
        self.exit_code = None
        self.setWindowTitle(f"{path.name} (script)")
        self.resize(760, 480)
        lay = QVBoxLayout(self)
        about = describe_script(path)
        head = QLabel(f"<b>{path.name}</b>" + (f" — {about}" if about else "") +
                      f"<br><span style='color:gray'>{path}</span>")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        lay.addWidget(head)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono_font())
        lay.addWidget(self.output, 1)
        self.status = QLabel()
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.stop_btn = QPushButton("Stop")
        self.again_btn = QPushButton("Run again")
        self.fix_btn = QPushButton("Ask the AI to fix it")
        self.change_btn = QPushButton("Change this script…")
        self.close_btn = QPushButton("Close")
        for b in (self.stop_btn, self.again_btn, self.fix_btn, self.change_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self.stop_btn.clicked.connect(self.stop)
        self.again_btn.clicked.connect(self.start)
        self.fix_btn.clicked.connect(self.ask_fix)
        self.change_btn.clicked.connect(
            lambda: send_to_ai("prefill", f"Change my script {self.path}: "))
        self.close_btn.clicked.connect(self.close)

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.setWorkingDirectory(str(Path.home()))
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)
        self.start()

    def _command(self):
        if os.access(self.path, os.X_OK):
            return str(self.path), self.args
        if self.path.suffix == ".py":
            return sys.executable or "python3", [str(self.path)] + self.args
        return "bash", [str(self.path)] + self.args

    def start(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        self.output.clear()
        self.exit_code = None
        self.status.setText("Running…")
        self.status.setStyleSheet("")
        self.stop_btn.setEnabled(True)
        self.again_btn.setEnabled(False)
        self.fix_btn.hide()
        program, argv = self._command()
        self.proc.start(program, argv)

    def stop(self):
        if self.proc.state() == QProcess.ProcessState.NotRunning:
            return
        self.proc.terminate()
        QTimer.singleShot(3000, lambda: self.proc.state() != QProcess.ProcessState.NotRunning
                          and self.proc.kill())

    def _read(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def _finished(self, code, status):
        self._read()
        crashed = status == QProcess.ExitStatus.CrashExit
        self.exit_code = -1 if crashed else code
        self.stop_btn.setEnabled(False)
        self.again_btn.setEnabled(True)
        if not crashed and code == 0:
            self.status.setText("✅ Finished.")
            self.status.setStyleSheet("color: #2e9d4f;")
        else:
            self.status.setText("⛔ Stopped." if crashed else f"⚠ The script failed (exit code {code}).")
            self.status.setStyleSheet("color: #d14b2f;")
            self.fix_btn.show()
        if not self.output.toPlainText().strip():
            self.output.setPlainText("(the script printed nothing)")

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.status.setText(f"⚠ Could not start the script: {self.proc.errorString()}")
            self.status.setStyleSheet("color: #d14b2f;")
            self.stop_btn.setEnabled(False)
            self.again_btn.setEnabled(True)
            self.fix_btn.show()

    def ask_fix(self):
        out = self.output.toPlainText().strip()[-3000:]
        send_to_ai("ask", f"My script {self.path} failed (exit code {self.exit_code}). This is what "
                          f"it printed:\n```\n{out}\n```\nPlease find the problem and fix the script.")

    def closeEvent(self, event):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()
            self.proc.waitForFinished(2000)
        super().closeEvent(event)


def run_script_window(path, args=None) -> int:
    path = Path(path).expanduser().resolve()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("deepseek-shell-script")
    app.setDesktopFileName("deepseek-shell")
    if not path.is_file():
        print(f"Script not found: {path}", file=sys.stderr)
        return 2
    win = ScriptRunner(path, args)
    win.show()
    win.exec()
    return 0 if win.exit_code == 0 else 1
