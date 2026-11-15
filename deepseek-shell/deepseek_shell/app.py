"""Main window of DeepSeek Shell."""
import base64
import os
import sys
import threading

from PyQt6.QtCore import QByteArray, Qt, QTimer
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut, QTextCursor
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                             QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from . import hardware, paths
from .agent import Agent, Event
from .config import Config
from .knowledge import Knowledge
from .ollama import Ollama, OllamaError, full_name
from .runner import CommandRunner, truncate_middle
from .settings_dialog import SettingsDialog
from .tools import ToolRegistry, format_result
from .widgets import (ApprovalCard, AssistantBubble, Notice, Row, Signals, ToolCard, UserBubble)

DIAGNOSE_PROMPT = ("Please check my computer for problems. Start with system_report, look for "
                   "missing drivers or firmware, devices without a driver, failed services and "
                   "errors. Then tell me in simple words what you found and offer to fix each problem.")

# (button, prompt, True = put the text in the box so you can finish it, False = send it now)
SUGGESTIONS = [
    ("📦  Set up an app for me", "Install and set up ", True),
    ("📝  Make me a script", "Make me a script that ", True),
    ("🩺  Check my PC for problems", DIAGNOSE_PROMPT, False),
    ("🎮  Set up gaming", "Help me set up gaming: install Steam and check that my graphics "
                         "driver is right for games.", False),
    ("🔊  My sound isn't working", "My sound isn't working. Please find out why and fix it.", False),
    ("🔄  Update everything", "Update the whole system (apt and Flatpak apps) and tell me what "
                             "changed.", False),
    ("🪟  Run a Windows program", "How do I run a Windows program (.exe) on this computer, and how "
                                  "do I switch on Proton for it?", False),
    ("🛡  Is my PC protected?", "Check that ClamAV virus protection is working and the virus "
                               "signatures are up to date.", False),
]

INSTANCE_KEY = f"deepseek-shell-{os.getuid()}"


class FixedWidthLabel(QLabel):
    """Word-wrapped label narrower than its layout. Qt layouts ask for its height at
    their own, wider width, which cut off the last line of the setup text (seen on
    Plasma 6.3); measure it at the label's real width instead."""

    def heightForWidth(self, width):
        return super().heightForWidth(min(width, self.maximumWidth()))


class InputBox(QPlainTextEdit):
    def __init__(self, on_send, parent=None):
        super().__init__(parent)
        self.on_send = on_send
        self.setPlaceholderText("Ask anything or describe a problem…  (Enter sends, Shift+Enter "
                                "adds a line, start with ! to run a command yourself)")
        self.setFixedHeight(72)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.on_send()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.client = Ollama(config["ollama_url"])
        self.registry = ToolRegistry(config)
        self.knowledge = Knowledge()
        self.runner = CommandRunner()
        self.agent = Agent(config, self.client, self.registry, self.knowledge, self.runner)
        self.history = []
        self.extra_context = []
        self.busy = False
        self.engine_ok = None
        self.models = []
        self.hw = None
        self.rec = None
        self.bubble = None
        self.cards = {}
        self.approvals = []
        self.queued = []
        self.pull_cancel = None
        self._status_running = False
        self._stick = True
        self._cmd_counter = 0

        self.signals = Signals()
        self.signals.event.connect(self._on_event)
        self.signals.approval.connect(self._on_approval)
        self.signals.status.connect(self._on_status)
        self.signals.progress.connect(self._on_pull_progress)
        self.signals.command_done.connect(self._on_command_done)

        self.setWindowTitle("DeepSeek Shell")
        self.resize(940, 740)
        self._build()
        self._restore_geometry()
        QShortcut(QKeySequence("Ctrl+N"), self, self.new_chat)
        QShortcut(QKeySequence("Ctrl+,"), self, self.open_settings)
        QShortcut(QKeySequence("Esc"), self, self.stop)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_status)
        self.timer.start(6000)
        self.check_status()

    # ── layout ────────────────────────────────────────────────────────────
    def _build(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(self.windowIcon().pixmap(28, 28))
        title = QLabel("<b>DeepSeek Shell</b>")
        self.status_label = QLabel("● starting…")
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(190)
        self.model_box.setToolTip("AI model")
        self.model_box.activated.connect(self._model_chosen)
        diag = QPushButton("🩺 Fix my PC")
        diag.setToolTip("Look for problems and offer fixes")
        diag.clicked.connect(lambda: self.send_text(DIAGNOSE_PROMPT))
        new = QPushButton("New chat")
        new.clicked.connect(self.new_chat)
        settings = QToolButton()
        settings.setText("⚙")
        settings.setToolTip("Settings: model, tools, safety (Ctrl+,)")
        settings.clicked.connect(lambda: self.open_settings())
        for w in (icon, title):
            header.addWidget(w)
        header.addSpacing(8)
        header.addWidget(self.status_label)
        header.addStretch(1)
        for w in (self.model_box, diag, new, settings):
            header.addWidget(w)
        root.addLayout(header)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.TextFormat.RichText)
        self.banner.setStyleSheet("background: #fff4d6; color: #5a4300; border-radius: 8px; padding: 8px;")
        self.banner.linkActivated.connect(self._banner_link)
        self.banner.hide()
        root.addWidget(self.banner)

        self.stack = QStackedWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat = QWidget()
        self.chat_lay = QVBoxLayout(self.chat)
        self.chat_lay.setSpacing(10)
        self.chat_lay.addStretch(1)
        self.scroll.setWidget(self.chat)
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _a, b: bar.setValue(b) if self._stick else None)
        bar.valueChanged.connect(lambda v: setattr(self, "_stick", v >= bar.maximum() - 40))
        self.stack.addWidget(self.scroll)
        self.stack.addWidget(self._setup_page())
        root.addWidget(self.stack, 1)

        row = QHBoxLayout()
        self.input = InputBox(self.send)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.hide()
        col = QVBoxLayout()
        col.addWidget(self.send_btn)
        col.addWidget(self.stop_btn)
        col.addStretch(1)
        row.addWidget(self.input, 1)
        row.addLayout(col)
        root.addLayout(row)
        self.setCentralWidget(central)
        self._show_welcome()

    def _setup_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        self.setup_title = QLabel()
        self.setup_text = FixedWidthLabel()
        self.setup_text.setWordWrap(True)
        self.setup_text.setTextFormat(Qt.TextFormat.RichText)
        self.setup_text.setFixedWidth(600)
        self.setup_btn = QPushButton()
        self.setup_btn.setMinimumHeight(36)
        self.setup_btn.clicked.connect(self._setup_action)
        self.setup_other = QPushButton("Choose another model…")
        self.setup_other.clicked.connect(lambda: self.open_settings())
        self.setup_bar = QProgressBar()
        self.setup_bar.setMaximumWidth(520)
        self.setup_bar.hide()
        self.setup_status = QLabel()
        for wdg in (self.setup_title, self.setup_text, self.setup_btn, self.setup_other,
                    self.setup_bar, self.setup_status):
            lay.addWidget(wdg, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(2)
        return w

    def _show_welcome(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        hello = QLabel("<h2>Hi! I'm your local AI.</h2><p>I run entirely on this PC. Just tell me "
                       "what you want done: install and set up apps, make scripts, change "
                       "settings, fix problems. I show you my plan and you OK it once.</p>"
                       "<p style='color:gray'>Tip: press <b>Win+C</b> to open me from anywhere.</p>")
        hello.setWordWrap(True)
        hello.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(hello)
        grid = QGridLayout()
        for i, (label, prompt, prefill) in enumerate(SUGGESTIONS):
            b = QPushButton(label)
            b.setMinimumHeight(40)
            b.setStyleSheet("text-align: left; padding-left: 12px;")
            if prefill:
                b.clicked.connect(lambda _=False, p=prompt: self.prefill(p))
            else:
                b.clicked.connect(lambda _=False, p=prompt: self.send_text(p))
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        self.welcome = box
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, box)

    def _clear_welcome(self):
        if getattr(self, "welcome", None) is not None:
            self.welcome.hide()
            self.welcome.deleteLater()
            self.welcome = None

    def _add_row(self, widget, right=False):
        row = Row(widget, right)
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, row)
        self._stick = True
        return row

    # ── status / setup ────────────────────────────────────────────────────
    def check_status(self):
        if self._status_running or self.busy:
            return
        self._status_running = True

        def work():
            try:
                models = self.client.model_names()
                result = ("ok", models)
            except OllamaError as e:
                result = ("offline", str(e))
            if self.hw is None:
                try:
                    self.hw = hardware.detect()
                    self.rec = hardware.recommend(self.hw)
                except Exception:
                    pass
            self.signals.status.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _on_status(self, result):
        kind, info = result
        if kind == "restart":  # after "Start the AI engine"
            self.setup_btn.setEnabled(True)
            QTimer.singleShot(1500, self.check_status)
            return
        self._status_running = False
        if kind == "offline":
            self.engine_ok = False
            self.status_label.setText("<span style='color:#d14b2f'>● AI engine offline</span>")
            if not self.busy:
                self._setup("offline", info)
            return
        self.engine_ok = True
        self.models = info
        if self.model_box.count() != len(info) or [self.model_box.itemText(i) for i in
                                                   range(self.model_box.count())] != info:
            self.model_box.clear()
            self.model_box.addItems(info)
        if not info:
            self.status_label.setText("<span style='color:#e0a020'>● no AI model yet</span>")
            if self.pull_cancel is None:
                self._setup("nomodel")
            return
        model = self.config.get("model")
        if model not in info and full_name(model) in info:
            model = full_name(model)
            self.config["model"] = model
            self.config.save()
        if model not in info:
            model = self.rec.model if self.rec and self.rec.model in info else info[0]
            self.config["model"] = model
            self.config.save()
        self.model_box.setCurrentText(model)
        self.status_label.setText("<span style='color:#2e9d4f'>● ready</span>")
        if self.stack.currentIndex() == 1 and self.pull_cancel is None:
            self.stack.setCurrentIndex(0)
        if self.rec and self.rec.notes and self.banner.isHidden() and not getattr(self, "_banner_done", False):
            note = self.rec.notes[0]
            fix = "install the NVIDIA driver" if "NVIDIA" in note else (
                "enable AMD GPU acceleration for the AI" if "ROCm" in note else "")
            link = f" <a href='fix:{fix}'>Fix it for me</a>" if fix else ""
            self.banner.setText(f"⚡ {note}{link}  <a href='close:'>Dismiss</a>")
            self.banner.show()
        while self.queued and not self.busy:
            self.send_text(self.queued.pop(0))

    def _banner_link(self, link: str):
        kind, _, value = link.partition(":")
        self.banner.hide()
        self._banner_done = True
        if kind == "fix" and value:
            self.send_text(f"Please {value} so the AI can use my graphics card.")

    def _setup(self, state, info=""):
        self.stack.setCurrentIndex(1)
        if state == "offline":
            self.setup_title.setText("<h2>The AI engine isn't running</h2>")
            self.setup_text.setText(
                "DeepSeek Shell runs the DeepSeek model with Ollama, which normally starts by itself "
                f"when the PC starts.<br><br><span style='color:gray'>{info}</span>")
            self.setup_btn.setText("Start the AI engine")
            self.setup_btn.setProperty("action", "start")
            self.setup_other.hide()
            return
        rec = self.rec or hardware.Recommendation("deepseek-r1:8b", 8192, "default")
        model = hardware.first_start_model(self.config.get("model"), rec)
        size = hardware.catalog_size(model)
        sized = f"{model} ({size})" if size else model
        if model == rec.model:
            choice = f"<p><b>Best fit:</b> {sized} because of your {rec.reason}.</p>"
        else:
            choice = (f"<p><b>Your chosen model:</b> {sized}<br><span style='color:gray'>"
                      f"For comparison, the best DeepSeek fit for this PC is {rec.model}.</span></p>")
        notes = "".join(f"<li>{n}</li>" for n in rec.notes)
        live = ("<p><b>You're running from the USB stick.</b> Install the system first: the model is "
                "big and would be lost when you restart.</p>" if self.hw and self.hw.live_session else "")
        self.setup_title.setText("<h2>Set up your local AI</h2>")
        self.setup_text.setText(
            "<p>The AI needs a model on this PC (one-time download, then it works offline).</p>"
            f"<p><b>This PC:</b> {self.hw.summary() if self.hw else 'checking…'}</p>"
            + choice + (f"<ul>{notes}</ul>" if notes else "") + live)
        self.setup_btn.setText(f"Download {model}" + (f"  ({size})" if size else ""))
        self.setup_btn.setProperty("action", "pull")
        self.setup_btn.setProperty("model", model)
        self.setup_other.show()

    def _setup_action(self):
        action = self.setup_btn.property("action")
        if action == "start":
            self.setup_btn.setEnabled(False)

            def work():
                self.runner.run("systemctl start ollama", admin=True, timeout=60)
                self.signals.status.emit(("restart", ""))

            threading.Thread(target=work, daemon=True).start()
            return
        if action == "pull":
            if self.pull_cancel is not None:
                self.pull_cancel.set()
                return
            model = self.setup_btn.property("model")
            self.pull_cancel = threading.Event()
            self.setup_btn.setText("Cancel download")
            self.setup_bar.show()
            self.setup_bar.setValue(0)

            def work(cancel=self.pull_cancel):
                try:
                    for st in self.client.pull(model, cancel=cancel):
                        self.signals.progress.emit(("progress", model, st))
                    self.signals.progress.emit(("cancelled" if cancel.is_set() else "done", model, {}))
                except OllamaError as e:
                    self.signals.progress.emit(("error", model, {"error": str(e)}))

            threading.Thread(target=work, daemon=True).start()

    def _on_pull_progress(self, msg):
        kind, model, st = msg
        if kind == "progress":
            total, done = st.get("total") or 0, st.get("completed") or 0
            if total:
                self.setup_bar.setMaximum(1000)
                self.setup_bar.setValue(int(done * 1000 / total))
                self.setup_status.setText(f"{done / 1e9:.2f} GB of {total / 1e9:.2f} GB")
            else:
                self.setup_status.setText(str(st.get("status", "")).capitalize())
            return
        self.pull_cancel = None
        self.setup_bar.hide()
        if kind == "done":
            self.config["model"] = model
            self.config.save()
            self.setup_status.setText("Ready!")
        elif kind == "error":
            self.setup_status.setText(f"Download failed: {st.get('error')}")
        else:
            self.setup_status.setText("Download cancelled.")
        self._setup("nomodel")
        self.check_status()

    def _model_chosen(self, _index):
        model = self.model_box.currentText()
        if model and model != self.config.get("model"):
            self.config["model"] = model
            self.config.save()
            self._add_row(Notice(f"Now using {model}."))

    # ── chatting ──────────────────────────────────────────────────────────
    def queue_prompt(self, text: str):
        if self.engine_ok and self.models and not self.busy:
            self.send_text(text)
        else:
            self.queued.append(text)

    def prefill(self, text: str):
        """Put the start of a request in the box, for the user to finish."""
        self.input.setPlainText(text)
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self.input.setFocus()

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or self.busy:
            return
        self.input.clear()
        self.send_text(text)

    def send_text(self, text: str):
        if self.busy:
            return
        if text.startswith("!"):
            self._run_user_command(text[1:].strip())
            return
        if not self.engine_ok or not self.config.get("model") or not self.models:
            self._add_row(Notice("The AI isn't ready yet. Finish the setup first.", "error"))
            return
        self._clear_welcome()
        self._add_row(UserBubble(text), right=True)
        context = ""
        if self.extra_context:
            context = "\n\n".join(self.extra_context) + "\n\n"
            self.extra_context = []
        self._set_busy(True)

        def approve(req, _cid):
            self.signals.approval.emit(req)
            return req.wait(self.agent.cancel)

        self.model_in_use = self.config.get("model", "")

        def work():
            self.history = self.agent.run(self.history, context + text, self.signals.event.emit, approve)

        threading.Thread(target=work, daemon=True).start()

    def _run_user_command(self, command: str):
        admin = command.startswith("!")
        command = command.lstrip("!").strip()
        if not command:
            return
        self._clear_welcome()
        self._add_row(UserBubble(("!! " if admin else "! ") + command), right=True)
        self._cmd_counter += 1
        cid = -self._cmd_counter
        card = ToolCard("run_command", {"command": command})
        self.cards[cid] = card
        self._add_row(card)
        self._set_busy(True)
        self.agent.cancel.clear()

        def work():
            code, out = self.runner.run(
                command, admin=admin, timeout=int(self.config.get("command_timeout", 900)),
                on_output=lambda t: self.signals.event.emit(Event("tool_output", t, cid)),
                cancel=self.agent.cancel)
            self.signals.command_done.emit((cid, command, code, out, admin))

        threading.Thread(target=work, daemon=True).start()

    def _on_command_done(self, data):
        cid, command, code, out, admin = data
        self.cards[cid].finish(format_result(code, out, admin), code == 0)
        self.extra_context.append(f"(I ran this command myself: `{command}` — exit code {code}. "
                                  f"Output:\n```\n{truncate_middle(out.strip(), 3000)}\n```)")
        self._set_busy(False)

    def _on_event(self, ev):
        kind = ev.kind
        if kind == "assistant_start":
            self.bubble = AssistantBubble(bool(self.config.get("show_thinking")))
            self._add_row(self.bubble)
        elif kind == "thinking" and self.bubble:
            self.bubble.add_thinking(ev.data)
        elif kind == "content" and self.bubble:
            self.bubble.add_content(ev.data)
        elif kind == "assistant_end" and self.bubble:
            if not self.bubble.finish(ev.data):
                self.bubble.parentWidget().hide()
                self.bubble.parentWidget().deleteLater()
            self.bubble = None
        elif kind == "tool_start":
            card = ToolCard(ev.data["name"], ev.data.get("arguments") or {})
            self.cards[ev.call_id] = card
            self._add_row(card)
        elif kind == "tool_output" and ev.call_id in self.cards:
            self.cards[ev.call_id].add_output(ev.data)
        elif kind == "tool_end" and ev.call_id in self.cards:
            self.cards[ev.call_id].finish(ev.data["result"], ev.data["ok"])
        elif kind == "error":
            self._add_row(Notice(str(ev.data), "error"))
        elif kind == "done":
            self._set_busy(False)
            for card in self.approvals:
                card.expire()
            self.approvals = []

    def _on_approval(self, req):
        card = ApprovalCard(req)
        self.approvals.append(card)
        self._add_row(card)
        card.allow.setFocus()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.send_btn.setVisible(not busy)
        self.stop_btn.setVisible(busy)
        if not busy:
            self.input.setFocus()
            while self.queued:
                self.send_text(self.queued.pop(0))
                if self.busy:
                    break

    def stop(self):
        if not self.busy:
            return
        self.agent.stop()
        for card in self.approvals:
            card.expire()
        self._add_row(Notice("Stopping…"))

    def new_chat(self):
        if self.busy:
            self.stop()
            return
        while self.chat_lay.count() > 1:
            item = self.chat_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.history, self.cards, self.extra_context = [], {}, []
        self._show_welcome()

    def open_settings(self, tab: str = ""):
        dlg = SettingsDialog(self.config, self.client, self.registry, self, start_tab=tab)
        dlg.exec()
        self.client.base_url = self.config["ollama_url"].rstrip("/")
        self.registry.reload()
        self.knowledge.reload()
        self.check_status()

    def handle_message(self, msg: str):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if msg == "settings":
            self.open_settings("tools")
        elif msg == "diagnose":
            self.queue_prompt(DIAGNOSE_PROMPT)
        elif msg.startswith("ask:") and msg[4:].strip():
            self.queue_prompt(msg[4:].strip())
        elif msg.startswith("prefill:"):
            self.prefill(msg[len("prefill:"):])

    # ── window state ──────────────────────────────────────────────────────
    def _restore_geometry(self):
        geo = self.config.get("window_geometry")
        if geo:
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(geo)))
            except Exception:
                pass

    def closeEvent(self, event):
        self.agent.stop()
        # The model stays in memory for 5 minutes after an answer. Once the window is
        # closed, one minute is enough (reopening soon stays quick, and its several
        # GB are free again shortly after). At most 2 seconds are spent on asking.
        if getattr(self, "model_in_use", "") and self.config.get("unload_on_close", True):
            ask = threading.Thread(target=self.client.release, args=(self.model_in_use, 60), daemon=True)
            ask.start()
            ask.join(2.0)
        try:
            self.config["window_geometry"] = base64.b64encode(bytes(self.saveGeometry())).decode()
            self.config.save()
        except OSError:
            pass
        super().closeEvent(event)


def app_icon() -> QIcon:
    icon = QIcon.fromTheme("deepseek-shell")
    if icon.isNull():
        icon = QIcon(str(paths.DATA_DIR / "deepseek-shell.svg"))
    return icon


def run_gui(settings: bool = False, diagnose: bool = False, ask: str = "", prefill: str = "",
            run_script: str = "") -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("deepseek-shell")
    app.setApplicationDisplayName("DeepSeek Shell")
    app.setDesktopFileName("deepseek-shell")
    app.setWindowIcon(app_icon())

    if run_script:
        from .script_runner import run_script_window
        return run_script_window(run_script)

    if settings:
        message = "settings"
    elif diagnose:
        message = "diagnose"
    elif ask:
        message = f"ask:{ask}"
    elif prefill:
        message = f"prefill:{prefill}"
    else:
        message = "show"
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE_KEY)
    if probe.waitForConnected(300):
        probe.write(message.encode("utf-8"))
        probe.flush()
        probe.waitForBytesWritten(1000)
        probe.disconnectFromServer()
        return 0

    config = Config()
    if settings:
        dlg = SettingsDialog(config, Ollama(config["ollama_url"]), ToolRegistry(config), None,
                             start_tab="tools")
        dlg.setWindowIcon(app_icon())
        dlg.exec()
        return 0

    QLocalServer.removeServer(INSTANCE_KEY)
    server = QLocalServer()
    server.listen(INSTANCE_KEY)
    win = MainWindow(config)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        data = bytearray(bytes(conn.readAll()))
        while conn.waitForReadyRead(300):  # long messages can arrive in pieces
            data += bytes(conn.readAll())
        data += bytes(conn.readAll())
        win.handle_message(bytes(data).decode("utf-8", "replace"))
        conn.disconnectFromServer()

    server.newConnection.connect(on_connection)
    win.show()
    if diagnose:
        win.queue_prompt(DIAGNOSE_PROMPT)
    elif ask:
        win.queue_prompt(ask)
    elif prefill:
        win.prefill(prefill)
    return app.exec()
