"""Settings window: AI model, tools (add your own), safety."""
import copy
import os
import re
import threading

from PyQt6.QtCore import QRegularExpression, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QRegularExpressionValidator, QTextCursor
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                             QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                             QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                             QVBoxLayout, QWidget)

from . import __version__, hardware, paths, scripts, tools
from .config import APPROVAL_MODES
from .ollama import OllamaError, full_name
from .runner import CommandRunner
from .widgets import Signals, mono_font

TOOL_HELP = (
    "Use {name} in the command where a parameter value goes. It is inserted safely quoted, "
    "so don't put quotes around it. The value is also available as $NAME (upper case) for use "
    "inside double quotes. Example: df -h {path}"
)


def _human_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    # Decimal units with one decimal for GB, like the sizes on ollama.com and in the
    # model list (a 2.7 GB model showed as "2 GB" before).
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000:
            return f"{n:.1f} {unit}" if unit == "GB" and n < 100 else f"{n:.0f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


class ToolEditor(QDialog):
    """Create or edit one custom tool."""

    def __init__(self, spec=None, path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit tool" if spec else "Add a tool")
        self.resize(720, 640)
        self.path = path
        self.spec = None
        self.signals = Signals()
        self.signals.command_done.connect(self._show_test_result)
        spec = spec or {"name": "", "description": "", "command": "", "parameters": [],
                        "ask_before_running": True, "run_as_admin": False, "timeout": 120,
                        "enabled": True}

        form = QFormLayout()
        self.name = QLineEdit(spec.get("name", ""))
        self.name.setPlaceholderText("e.g. check_disk_space")
        self.name.setValidator(QRegularExpressionValidator(QRegularExpression(r"[a-z][a-z0-9_]{0,47}")))
        form.addRow("Name:", self.name)
        self.description = QPlainTextEdit(spec.get("description", ""))
        self.description.setPlaceholderText("Tell the AI what this tool does and when to use it.")
        self.description.setFixedHeight(70)
        form.addRow("Description:", self.description)
        self.command = QPlainTextEdit(spec.get("command", ""))
        self.command.setFont(mono_font())
        self.command.setPlaceholderText("df -h {path}")
        self.command.setFixedHeight(90)
        form.addRow("Command:", self.command)
        hint = QLabel(TOOL_HELP)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        form.addRow("", hint)

        self.params = QTableWidget(0, 4)
        self.params.setHorizontalHeaderLabels(["Parameter", "Type", "Description", "Required"])
        self.params.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.params.verticalHeader().hide()
        for p in spec.get("parameters", []):
            self._add_param_row(p)
        prow = QHBoxLayout()
        add_p = QPushButton("Add parameter")
        del_p = QPushButton("Remove parameter")
        add_p.clicked.connect(lambda: self._add_param_row({}))
        del_p.clicked.connect(lambda: self.params.removeRow(self.params.currentRow())
                              if self.params.currentRow() >= 0 else None)
        prow.addWidget(add_p)
        prow.addWidget(del_p)
        prow.addStretch(1)
        pbox = QVBoxLayout()
        pbox.addWidget(self.params)
        pbox.addLayout(prow)
        form.addRow("Parameters:", pbox)

        self.ask = QCheckBox("Ask me before the AI runs this tool")
        self.ask.setChecked(bool(spec.get("ask_before_running", True)))
        self.admin = QCheckBox("Run as administrator (asks for your password)")
        self.admin.setChecked(bool(spec.get("run_as_admin", False)))
        self.enabled = QCheckBox("Tool is switched on")
        self.enabled.setChecked(bool(spec.get("enabled", True)))
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 7200)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(int(spec.get("timeout", 120)))
        form.addRow("", self.ask)
        form.addRow("", self.admin)
        form.addRow("", self.enabled)
        form.addRow("Time limit:", self.timeout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        self.test_btn = buttons.addButton("Test…", QDialogButtonBox.ButtonRole.ActionRole)
        self.test_btn.clicked.connect(self._test)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _add_param_row(self, p: dict):
        r = self.params.rowCount()
        self.params.insertRow(r)
        self.params.setItem(r, 0, QTableWidgetItem(p.get("name", "")))
        type_box = QComboBox()
        type_box.addItems(list(tools.PARAM_TYPES))
        type_box.setCurrentText(p.get("type", "string"))
        self.params.setCellWidget(r, 1, type_box)
        self.params.setItem(r, 2, QTableWidgetItem(p.get("description", "")))
        req = QTableWidgetItem()
        req.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        req.setCheckState(Qt.CheckState.Checked if p.get("required", True) else Qt.CheckState.Unchecked)
        self.params.setItem(r, 3, req)

    def collect(self) -> dict:
        params = []
        for r in range(self.params.rowCount()):
            name = (self.params.item(r, 0).text() if self.params.item(r, 0) else "").strip()
            if not name:
                continue
            params.append({
                "name": name,
                "type": self.params.cellWidget(r, 1).currentText(),
                "description": (self.params.item(r, 2).text() if self.params.item(r, 2) else "").strip(),
                "required": self.params.item(r, 3).checkState() == Qt.CheckState.Checked,
            })
        return {
            "name": self.name.text().strip(),
            "description": self.description.toPlainText().strip(),
            "command": self.command.toPlainText().strip(),
            "parameters": params,
            "ask_before_running": self.ask.isChecked(),
            "run_as_admin": self.admin.isChecked(),
            "timeout": self.timeout.value(),
            "enabled": self.enabled.isChecked(),
        }

    def _save(self):
        spec = self.collect()
        errors = tools.validate_spec(spec)
        if errors:
            QMessageBox.warning(self, "Please fix", "\n".join(errors))
            return
        try:
            self.path = tools.save_user_tool(spec, self.path)
        except (tools.ToolError, OSError) as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self.spec = spec
        self.accept()

    def _test(self):
        spec = self.collect()
        errors = [e for e in tools.validate_spec(spec) if "Name" not in e and "built-in" not in e]
        if errors:
            QMessageBox.warning(self, "Please fix", "\n".join(errors))
            return
        args = {}
        for p in spec["parameters"]:
            value, ok = QInputDialog.getText(self, "Test value", f"Value for '{p['name']}':")
            if not ok:
                return
            args[p["name"]] = value
        try:
            command, env = tools.render_command(spec, args)
        except tools.ToolError as e:
            QMessageBox.warning(self, "Test", str(e))
            return
        if QMessageBox.question(self, "Run test", f"Run this now?\n\n{command}") != \
                QMessageBox.StandardButton.Yes:
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Running…")

        def work():
            code, out = CommandRunner().run(command, admin=spec["run_as_admin"],
                                            timeout=min(spec["timeout"], 300), extra_env=env)
            self.signals.command_done.emit((code, out))

        threading.Thread(target=work, daemon=True).start()

    def _show_test_result(self, result):
        code, out = result
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Test…")
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Test result (exit code {code})")
        dlg.resize(640, 420)
        view = QPlainTextEdit(out or "(no output)")
        view.setReadOnly(True)
        view.setFont(mono_font())
        lay = QVBoxLayout(dlg)
        lay.addWidget(view)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dlg.reject)
        lay.addWidget(close)
        dlg.exec()


class SettingsDialog(QDialog):
    def __init__(self, config, client, registry, parent=None, start_tab: str = ""):
        super().__init__(parent)
        self.setWindowTitle("DeepSeek Shell Settings")
        self.resize(820, 640)
        self.config = config
        self.client = client
        self.registry = registry
        self.original = copy.deepcopy(config.data)
        self.signals = Signals()
        self.signals.progress.connect(self._pull_progress)
        self.signals.status.connect(self._engine_status)
        self.pull_cancel = None

        self.tabs = QTabWidget()
        self.tabs.addTab(self._model_tab(), "AI model")
        self.tabs.addTab(self._tools_tab(), "Tools")
        self.tabs.addTab(self._safety_tab(), "Safety")
        self.tabs.addTab(self._about_tab(), "About")
        if start_tab == "tools":
            self.tabs.setCurrentIndex(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addWidget(buttons)
        self._refresh_models()

    # ── AI model ──────────────────────────────────────────────────────────
    def _model_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.engine_label = QLabel("Checking the AI engine…")
        lay.addWidget(self.engine_label)

        use = QGroupBox("Model in use")
        ul = QHBoxLayout(use)
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(280)
        delete = QPushButton("Delete model…")
        delete.clicked.connect(self._delete_model)
        ul.addWidget(self.model_box, 1)
        ul.addWidget(delete)
        lay.addWidget(use)

        dl = QGroupBox("Download a model")
        dlay = QVBoxLayout(dl)
        row = QHBoxLayout()
        self.catalog_box = QComboBox()
        self.catalog_box.setEditable(True)
        family = None
        for name, size, note in hardware.CATALOG:
            this_family = re.match(r"[a-z]+", name).group(0)  # deepseek, qwen, ...
            if family is not None and this_family != family:
                self.catalog_box.insertSeparator(self.catalog_box.count())
            family = this_family
            self.catalog_box.addItem(f"{name}   ({size})", name)
            self.catalog_box.setItemData(self.catalog_box.count() - 1, note, Qt.ItemDataRole.ToolTipRole)
        self.pull_btn = QPushButton("Download")
        self.pull_btn.clicked.connect(self._pull)
        row.addWidget(self.catalog_box, 1)
        row.addWidget(self.pull_btn)
        dlay.addLayout(row)
        self.catalog_note = QLabel()
        self.catalog_note.setWordWrap(True)
        self.catalog_box.currentIndexChanged.connect(self._catalog_note)
        self.catalog_box.editTextChanged.connect(self._catalog_note)  # a typed model name
        dlay.addWidget(self.catalog_note)
        self.pull_bar = QProgressBar()
        self.pull_bar.hide()
        self.pull_status = QLabel()
        dlay.addWidget(self.pull_bar)
        dlay.addWidget(self.pull_status)
        tip = QLabel("You can also type any model name from ollama.com/library.")
        tip.setStyleSheet("color: palette(placeholder-text);")
        dlay.addWidget(tip)
        lay.addWidget(dl)

        hw = hardware.detect()
        rec = hardware.recommend(hw)
        idx = self.catalog_box.findData(rec.model)
        if idx >= 0:
            self.catalog_box.setCurrentIndex(idx)
        self._catalog_note()
        hwbox = QGroupBox("This PC")
        hl = QVBoxLayout(hwbox)
        info = QLabel(f"{hw.summary()}\n\nBest fit: {rec.model} ({rec.reason})." +
                      ("".join(f"\n• {n}" for n in rec.notes)))
        info.setWordWrap(True)
        hl.addWidget(info)
        lay.addWidget(hwbox)

        adv = QGroupBox("Advanced")
        al = QFormLayout(adv)
        self.ctx = QSpinBox()
        self.ctx.setRange(0, 262144)
        self.ctx.setSingleStep(4096)
        self.ctx.setSpecialValueText("Automatic")
        self.ctx.setValue(int(self.config.get("num_ctx") or 0))
        self.temp = QDoubleSpinBox()
        self.temp.setRange(0.0, 1.5)
        self.temp.setSingleStep(0.1)
        self.temp.setValue(float(self.config.get("temperature", 0.6)))
        self.think = QCheckBox("Think before answering (smarter, slower)")
        self.think.setChecked(bool(self.config.get("think", True)))
        self.show_think = QCheckBox("Show the AI's reasoning while it thinks")
        self.show_think.setChecked(bool(self.config.get("show_thinking", False)))
        self.tool_mode = QComboBox()
        for key, label in (("auto", "Automatic"), ("native", "Native tool calling"),
                           ("text", "Text format (works with every model)")):
            self.tool_mode.addItem(label, key)
        self.tool_mode.setCurrentIndex(max(0, self.tool_mode.findData(self.config.get("tool_mode", "auto"))))
        al.addRow("Memory (context, tokens):", self.ctx)
        al.addRow("Creativity (temperature):", self.temp)
        al.addRow("", self.think)
        al.addRow("", self.show_think)
        al.addRow("How the AI uses tools:", self.tool_mode)
        lay.addWidget(adv)
        lay.addStretch(1)
        return w

    def _catalog_note(self):
        text = self.catalog_box.currentText().strip()
        name = text.split()[0] if text else ""      # same rule as _pull
        notes = {n: t for n, _s, t in hardware.CATALOG}
        if name in notes:
            note = notes[name]
        elif name:
            note = (f"{name} is not in this list. Check its download size and what it is good at "
                    "on ollama.com/library first; models marked \"tools\" there work best here.")
        else:
            note = ""
        self.catalog_note.setText(note)

    def _refresh_models(self):
        def work():
            try:
                version = self.client.version()
                models = self.client.list_models()
                self.signals.status.emit(("ok", version, models))
            except OllamaError as e:
                self.signals.status.emit(("error", str(e), []))
        threading.Thread(target=work, daemon=True).start()

    def _engine_status(self, status):
        kind, info, models = status
        if kind == "ok":
            self.engine_label.setText(f"AI engine: running (Ollama {info}).")
        else:
            self.engine_label.setText(f"AI engine: not running. {info}")
        current = self.config.get("model", "")
        self.model_box.clear()
        for m in models:
            self.model_box.addItem(f"{m['name']}   ({_human_size(m.get('size'))})", m["name"])
        idx = self.model_box.findData(current)
        if idx < 0:
            idx = self.model_box.findData(full_name(current))
        if idx >= 0:
            self.model_box.setCurrentIndex(idx)

    def _delete_model(self):
        name = self.model_box.currentData()
        if not name:
            return
        if QMessageBox.question(self, "Delete model", f"Delete {name} from this PC?") != \
                QMessageBox.StandardButton.Yes:
            return
        try:
            self.client.delete(name)
        except OllamaError as e:
            QMessageBox.warning(self, "Delete model", str(e))
        self._refresh_models()

    def _pull(self):
        if self.pull_cancel is not None:
            self.pull_cancel.set()
            return
        text = self.catalog_box.currentText().strip()
        name = text.split()[0] if text else ""
        if not name:
            return
        self.pull_cancel = threading.Event()
        self.pull_btn.setText("Cancel")
        self.pull_bar.show()
        self.pull_bar.setValue(0)

        def work(cancel=self.pull_cancel):
            try:
                for st in self.client.pull(name, cancel=cancel):
                    self.signals.progress.emit(("progress", name, st))
                self.signals.progress.emit(("cancelled" if cancel.is_set() else "done", name, {}))
            except OllamaError as e:
                self.signals.progress.emit(("error", name, {"error": str(e)}))
        threading.Thread(target=work, daemon=True).start()

    def _pull_progress(self, msg):
        kind, name, st = msg
        if kind == "progress":
            total, done = st.get("total") or 0, st.get("completed") or 0
            if total:
                self.pull_bar.setMaximum(1000)
                self.pull_bar.setValue(int(done * 1000 / total))
                self.pull_status.setText(f"{st.get('status', '')[:40]}  {_human_size(done)} of {_human_size(total)}")
            else:
                self.pull_status.setText(st.get("status", ""))
            return
        self.pull_cancel = None
        self.pull_btn.setText("Download")
        self.pull_bar.hide()
        if kind == "done":
            self.pull_status.setText(f"{name} is ready.")
            self.config["model"] = name
        elif kind == "error":
            self.pull_status.setText(f"Download failed: {st.get('error')}")
        else:
            self.pull_status.setText("Download cancelled.")
        self._refresh_models()

    # ── Tools ─────────────────────────────────────────────────────────────
    def _tools_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel("Tools are what the AI can do on this PC. Switch them on or off, or add your "
                       "own: a tool is a command with optional parameters that the AI fills in.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["On", "Tool", "From", "What it does"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda *_: self._edit_tool())
        lay.addWidget(self.table, 1)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #d14b2f;")
        lay.addWidget(self.problems)
        row = QHBoxLayout()
        for label, slot in (("Add tool…", self._add_tool), ("Edit…", self._edit_tool),
                            ("Duplicate", self._duplicate_tool), ("Delete", self._delete_tool),
                            ("Tools folder", self._open_tools_folder),
                            ("Your scripts", lambda: self._open_folder(scripts.scripts_dir())),
                            ("Your fix guides", self._open_guides_folder)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self._fill_tools()
        return w

    def _fill_tools(self):
        self.registry.reload()
        disabled = set(self.config.get("disabled_tools") or [])
        tools_list = sorted(self.registry.tools.values(),
                            key=lambda t: (t.source != "yours", t.source != "system", t.name))
        self.table.setRowCount(0)
        for t in tools_list:
            r = self.table.rowCount()
            self.table.insertRow(r)
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            enabled = t.name not in disabled and (t.spec is None or t.spec.get("enabled", True))
            on.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            on.setData(Qt.ItemDataRole.UserRole, t.name)
            self.table.setItem(r, 0, on)
            self.table.setItem(r, 1, QTableWidgetItem(t.name))
            self.table.setItem(r, 2, QTableWidgetItem(t.source))
            self.table.setItem(r, 3, QTableWidgetItem(t.description))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        if self.registry.problems:
            self.problems.setText("Some tool files could not be loaded:\n" + "\n".join(
                f"{p.name}: {'; '.join(e)}" for p, e in self.registry.problems))
        else:
            self.problems.setText("")

    def _selected_tool(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        return self.registry.tools.get(self.table.item(r, 0).data(Qt.ItemDataRole.UserRole))

    def _add_tool(self):
        if ToolEditor(parent=self).exec():
            self._fill_tools()

    def _edit_tool(self):
        t = self._selected_tool()
        if t is None:
            return
        if t.spec is None:
            QMessageBox.information(self, "Built-in tool", f"'{t.name}' is built in and cannot be "
                                    "edited. You can switch it off with its checkbox.")
            return
        path = t.path if t.source == "yours" else None
        if ToolEditor(copy.deepcopy(t.spec), path, parent=self).exec():
            self._fill_tools()

    def _duplicate_tool(self):
        t = self._selected_tool()
        if t is None or t.spec is None:
            return
        spec = copy.deepcopy(t.spec)
        spec["name"] = f"{spec['name']}_copy"[:48]
        if ToolEditor(spec, None, parent=self).exec():
            self._fill_tools()

    def _delete_tool(self):
        t = self._selected_tool()
        if t is None:
            return
        if t.source != "yours":
            QMessageBox.information(self, "Delete", "Only tools you added can be deleted. "
                                    "Switch this one off with its checkbox instead.")
            return
        if QMessageBox.question(self, "Delete tool", f"Delete the tool '{t.name}'?") == \
                QMessageBox.StandardButton.Yes:
            try:
                os.unlink(t.path)
            except OSError as e:
                QMessageBox.warning(self, "Delete", str(e))
            self._fill_tools()

    def _open_folder(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _open_tools_folder(self):
        self._open_folder(paths.USER_TOOLS_DIR)

    def _open_guides_folder(self):
        folder = paths.USER_KNOWLEDGE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        example = folder / "README.md"
        if not example.exists():
            example.write_text("# How to write your own fix guide\nKeywords: example, my notes\n\n"
                               "Put Markdown files here. The AI searches them (search_fixes) "
                               "together with the built-in guides.\n", encoding="utf-8")
        self._open_folder(folder)

    # ── Safety ────────────────────────────────────────────────────────────
    def _safety_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        box = QGroupBox("When should the AI ask you first?")
        bl = QVBoxLayout(box)
        self.approval_group = QButtonGroup(self)
        for key, label in APPROVAL_MODES.items():
            rb = QRadioButton(label)
            rb.setProperty("mode", key)
            rb.setChecked(self.config.get("approval", "smart") == key)
            self.approval_group.addButton(rb)
            bl.addWidget(rb)
        note = QLabel("For a bigger job the AI shows its plan first: “Go ahead” lets it finish the "
                      "whole job without asking again. On any question, “Allow all for this task” "
                      "does the same. Dangerous commands (formatting disks, deleting system "
                      "folders…) always ask, and administrator commands need your password.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        bl.addWidget(note)
        lay.addWidget(box)
        form = QFormLayout()
        self.snapshots = QCheckBox("Take a system snapshot before every administrator change "
                                   "(Btrfs; lets you undo)")
        self.snapshots.setChecked(bool(self.config.get("snapshots", True)))
        self.max_steps = QSpinBox()
        self.max_steps.setRange(3, 100)
        self.max_steps.setValue(int(self.config.get("max_steps", 20)))
        self.cmd_timeout = QSpinBox()
        self.cmd_timeout.setRange(30, 14400)
        self.cmd_timeout.setSuffix(" s")
        self.cmd_timeout.setValue(int(self.config.get("command_timeout", 900)))
        form.addRow("", self.snapshots)
        form.addRow("Most steps per request:", self.max_steps)
        form.addRow("Time limit per command:", self.cmd_timeout)
        lay.addLayout(form)
        log_btn = QPushButton("Show everything the AI has run…")
        log_btn.clicked.connect(self._show_log)
        lay.addWidget(log_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        return w

    def _show_log(self):
        try:
            text = paths.ACTIVITY_LOG.read_text(encoding="utf-8")
        except OSError:
            text = "Nothing has been run yet."
        dlg = QDialog(self)
        dlg.setWindowTitle("Activity log")
        dlg.resize(820, 480)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(mono_font())
        view.moveCursor(QTextCursor.MoveOperation.End)
        lay = QVBoxLayout(dlg)
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def _about_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        text = QLabel(
            f"<h3>DeepSeek Shell {__version__}</h3>"
            "<p>A local AI assistant. The DeepSeek model runs on this PC through Ollama; "
            "nothing you type leaves the computer unless a tool you approve goes online.</p>"
            f"<p>Settings: <code>{paths.CONFIG_DIR}</code><br>"
            f"Your tools: <code>{paths.USER_TOOLS_DIR}</code><br>"
            f"Your scripts: <code>{scripts.scripts_dir()}</code><br>"
            f"Your fix guides: <code>{paths.USER_KNOWLEDGE_DIR}</code><br>"
            f"Activity log: <code>{paths.ACTIVITY_LOG}</code></p>"
            "<p>Text mode for emergencies (no desktop): press Ctrl+Alt+F3, log in, and run "
            "<code>deepseek-shell --cli</code>.</p>")
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        lay.addWidget(text)
        lay.addStretch(1)
        return w

    # ── save ──────────────────────────────────────────────────────────────
    def _accept(self):
        if self.pull_cancel is not None:
            if QMessageBox.question(self, "Download running",
                                    "A model is still downloading. Close anyway (the download stops)?") != \
                    QMessageBox.StandardButton.Yes:
                return
            self.pull_cancel.set()
        model = self.model_box.currentData()
        if model:
            self.config["model"] = model
        self.config["num_ctx"] = self.ctx.value()
        self.config["temperature"] = round(self.temp.value(), 2)
        self.config["think"] = self.think.isChecked()
        self.config["show_thinking"] = self.show_think.isChecked()
        self.config["tool_mode"] = self.tool_mode.currentData()
        checked = self.approval_group.checkedButton()
        if checked is not None:
            self.config["approval"] = checked.property("mode")
        self.config["snapshots"] = self.snapshots.isChecked()
        self.config["max_steps"] = self.max_steps.value()
        self.config["command_timeout"] = self.cmd_timeout.value()
        disabled = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            name = item.data(Qt.ItemDataRole.UserRole)
            tool = self.registry.tools.get(name)
            off = item.checkState() != Qt.CheckState.Checked
            if tool is not None and tool.spec is not None and tool.source == "yours":
                if bool(tool.spec.get("enabled", True)) == off:
                    spec = dict(tool.spec, enabled=not off)
                    try:
                        tools.save_user_tool(spec, tool.path)
                    except (tools.ToolError, OSError):
                        pass
            elif off:
                disabled.append(name)
        self.config["disabled_tools"] = disabled
        try:
            self.config.save()
        except OSError as e:
            QMessageBox.warning(self, "Settings", f"Could not save the settings: {e}")
            return
        self.registry.reload()
        self.accept()

    def reject(self):
        if self.pull_cancel is not None:
            self.pull_cancel.set()
        self.config.data = self.original
        super().reject()
