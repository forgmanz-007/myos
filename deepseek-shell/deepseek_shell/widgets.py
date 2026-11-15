"""Chat widgets: message bubbles, tool cards and approval cards."""
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QTextCursor, QTextOption
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QSizePolicy, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

MONO = None


def mono_font() -> QFont:
    global MONO
    if MONO is None:
        MONO = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    return MONO


class MarkdownView(QTextBrowser):
    """Read-only text that grows to fit its content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("QTextBrowser { background: transparent; }")
        self.document().setDocumentMargin(2)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self.setFixedHeight(24)

    def _fit(self, *_):
        height = int(self.document().size().height()) + 6
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self._fit()

    def set_markdown(self, text: str):
        self.setMarkdown(text)
        self._fit()


class UserBubble(QFrame):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("userBubble")
        self.setStyleSheet("#userBubble { background: palette(highlight); border-radius: 12px; }"
                           "#userBubble QLabel { color: palette(highlighted-text); }")
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.addWidget(label)
        self.setMaximumWidth(620)


class Row(QWidget):
    """Puts a bubble on the left or the right."""

    def __init__(self, child: QWidget, right: bool = False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if right:
            lay.addStretch(1)
        lay.addWidget(child, 0 if right else 1)
        if not right:
            child.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


class AssistantBubble(QFrame):
    def __init__(self, show_thinking: bool, parent=None):
        super().__init__(parent)
        self.text = ""
        self.thinking = ""
        self.show_thinking = show_thinking
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)
        self.think_btn = QToolButton()
        self.think_btn.setText("Thinking…")
        self.think_btn.setCheckable(True)
        self.think_btn.setChecked(show_thinking)
        self.think_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.think_btn.setStyleSheet("QToolButton { border: none; color: palette(placeholder-text); }")
        self.think_btn.hide()
        self.think_view = QLabel()
        self.think_view.setWordWrap(True)
        self.think_view.setStyleSheet("color: palette(placeholder-text); font-style: italic;")
        self.think_view.setVisible(show_thinking)
        self.think_btn.toggled.connect(self.think_view.setVisible)
        self.body = MarkdownView()
        lay.addWidget(self.think_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.think_view)
        lay.addWidget(self.body)
        self.body.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._render)

    def add_thinking(self, delta: str):
        self.thinking += delta
        self.think_btn.show()
        self._timer.start()

    def add_content(self, delta: str):
        self.text += delta
        if not self._timer.isActive():
            self._timer.start()

    def _render(self):
        if self.thinking:
            tail = self.thinking[-1500:]
            self.think_view.setText(("…" if len(self.thinking) > 1500 else "") + tail)
        if self.text.strip():
            self.body.show()
            self.body.set_markdown(self.text)

    def finish(self, final_text=None):
        if final_text is not None:
            self.text = final_text
        if self.thinking:
            self.think_btn.setText("Show reasoning" if not self.think_btn.isChecked() else "Reasoning")
            self.think_btn.toggled.connect(
                lambda on: self.think_btn.setText("Reasoning" if on else "Show reasoning"))
        self._render()
        return bool(self.text.strip() or self.thinking.strip())


class ToolCard(QFrame):
    ICONS = {"run_command": "⌨", "read_file": "📄", "write_file": "✎", "list_directory": "📁",
             "system_report": "🩺", "search_fixes": "📚", "find_software": "🔎", "open_app": "🚀",
             "propose_plan": "🗒", "install_app": "📦", "create_script": "📝",
             "create_shortcut": "🔗", "create_tool": "🧩", "set_wallpaper": "🖼"}

    def __init__(self, name: str, args: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("toolCard")
        self.setStyleSheet("#toolCard { background: palette(alternate-base); border: 1px solid "
                           "palette(mid); border-radius: 8px; }")
        shown = ""
        for key in ("command", "path", "query", "target", "app", "name", "file", "summary"):
            if isinstance(args.get(key), str) and args[key].strip():
                shown = args[key]
                break
        if not shown:
            shown = ", ".join(f"{k}={v}" for k, v in args.items() if not isinstance(v, (list, dict)))
        if name == "create_shortcut" and args.get("where"):
            shown += f" → {args['where']}"
        if len(shown) > 300:
            shown = shown[:300] + "…"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        top = QHBoxLayout()
        self.icon = QLabel(self.ICONS.get(name, "🧩"))
        self.title = QLabel(f"<b>{_esc(name)}</b>" + (f" — <code>{_esc(shown)}</code>" if shown else ""))
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.status = QLabel("running…")
        self.status.setStyleSheet("color: palette(placeholder-text);")
        self.toggle = QToolButton()
        self.toggle.setText("Details")
        self.toggle.setCheckable(True)
        top.addWidget(self.icon)
        top.addWidget(self.title, 1)
        top.addWidget(self.status)
        top.addWidget(self.toggle)
        lay.addLayout(top)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono_font())
        self.output.setMaximumHeight(240)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.output.hide()
        self.toggle.toggled.connect(self.output.setVisible)
        lay.addWidget(self.output)

    def add_output(self, text: str):
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(text)
        if not self.toggle.isChecked() and self.output.document().lineCount() > 0:
            self.toggle.setChecked(True)

    def finish(self, result: str, ok: bool):
        self.status.setText("done" if ok else "failed")
        self.status.setStyleSheet("color: #2e9d4f;" if ok else "color: #d14b2f;")
        self.output.setPlainText(result)
        if ok:
            self.toggle.setChecked(False)


class ApprovalCard(QFrame):
    decided = pyqtSignal(str)

    RESULTS = {"allow": "Allowed", "task": "Allowed for this whole task", "always": "Always allowed",
               "deny": "Not allowed"}

    def __init__(self, req, parent=None):
        super().__init__(parent)
        self.req = req
        plan = req.kind == "plan"
        danger = req.level == "danger"
        color = "#d14b2f" if danger else ("#3d7be0" if plan else "#e0a020")
        self.setObjectName("approvalCard")
        self.setStyleSheet(f"#approvalCard {{ border: 2px solid {color}; border-radius: 10px; "
                           "background: palette(base); }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        if plan:
            title = QLabel(f"<b>Here's my plan:</b> {_esc(req.title)}")
        else:
            title = QLabel(f"<b>{_esc(req.title)}?</b>")
        title.setWordWrap(True)
        lay.addWidget(title)
        if req.reason:
            why = QLabel(f"Why: {_esc(req.reason)}")
            why.setWordWrap(True)
            lay.addWidget(why)
        if req.warning:
            warn = QLabel(f"⚠ {_esc(req.warning)}")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {color}; font-weight: bold;")
            lay.addWidget(warn)
        if plan:
            steps = QLabel("<ol style='margin: 0'>" + "".join(
                f"<li>{_esc(s.split('. ', 1)[-1])}</li>" for s in req.detail.splitlines() if s.strip())
                + "</ol>")
            steps.setTextFormat(Qt.TextFormat.RichText)
            steps.setWordWrap(True)
            lay.addWidget(steps)
            note = QLabel("If you go ahead, I'll do all of this without asking again "
                          "(dangerous commands still ask).")
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(placeholder-text);")
            lay.addWidget(note)
        else:
            detail = QPlainTextEdit(req.detail)
            detail.setReadOnly(True)
            detail.setFont(mono_font())
            lines = min(14, max(1, req.detail.count("\n") + 1))
            detail.setFixedHeight(int(detail.fontMetrics().lineSpacing() * lines + 16))
            lay.addWidget(detail)
        if req.admin:
            note = QLabel("This needs administrator rights: you will be asked for your password.")
            note.setStyleSheet("color: palette(placeholder-text);")
            lay.addWidget(note)

        buttons = QHBoxLayout()
        self.buttons = []

        def add(label, decision, default=False):
            b = QPushButton(label)
            b.clicked.connect(lambda: self._decide(decision))
            b.setDefault(default)
            buttons.addWidget(b)
            self.buttons.append(b)
            return b

        if plan:
            self.allow = add("Go ahead", "task", default=True)
            self.deny = add("Not now", "deny")
        else:
            self.allow = add("Allow", "allow", default=True)
            if req.allow_task and not danger:
                add("Allow all for this task", "task")
            if req.allow_always and not danger:
                add("Always allow this tool", "always")
            self.deny = add("Don't allow", "deny")
        buttons.addStretch(1)
        self.result = QLabel()
        buttons.addWidget(self.result)
        lay.addLayout(buttons)

    def _decide(self, decision: str):
        for b in self.buttons:
            b.setEnabled(False)
        self.result.setText(self.RESULTS.get(decision, decision))
        self.req.respond(decision)
        self.decided.emit(decision)

    def expire(self):
        if not self.req._event.is_set():
            self._decide("deny")


class Notice(QLabel):
    def __init__(self, text: str, kind: str = "info", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        colors = {"error": "#d14b2f", "info": "palette(placeholder-text)", "ok": "#2e9d4f"}
        self.setStyleSheet(f"color: {colors.get(kind, colors['info'])}; padding: 4px;")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


class Signals(QObject):
    """Deliver events from worker threads to the GUI thread."""
    event = pyqtSignal(object)
    approval = pyqtSignal(object)
    status = pyqtSignal(object)
    progress = pyqtSignal(object)
    command_done = pyqtSignal(object)


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
