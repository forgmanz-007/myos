"""The Task Manager window: Processes, Performance, Startup, Details and Services."""
import base64
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon, QKeySequence
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                             QMenu, QMessageBox, QPushButton, QStackedWidget, QTabWidget, QVBoxLayout,
                             QWidget)

from . import actions, apps, formats, procfs, services, startup
from .widgets import HISTORY, KEY_ROLE, ElidedLabel, Graph, KeyedTree, Spec, TotalsHeader

TABS = ["processes", "performance", "startup", "details", "services"]
T_PROCESSES, T_PERFORMANCE, T_STARTUP, T_DETAILS, T_SERVICES = range(5)
SPEEDS = {"high": 0.5, "normal": 1.0, "low": 4.0}
SECTIONS = [("app", "Apps"), ("background", "Background processes"), ("system", "System processes")]
COLORS = {"cpu": "#117dbb", "mem": "#8b12ae", "disk": "#4da60c", "net": "#a74f01", "gpu": "#117dbb"}
PERFORMANCE_TOOL = "myos-performance"
GRAY = "color: palette(placeholder-text);"


# ── small helpers ────────────────────────────────────────────────────────────
def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "myos-taskmanager.json"


def load_config() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(data: dict) -> None:
    try:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


_ICONS = {}


def themed_icon(name: str, fallback: str = "window") -> QIcon:
    key = name or fallback
    if key not in _ICONS:
        found = QIcon(name) if name.startswith("/") else QIcon.fromTheme(name) if name else QIcon()
        _ICONS[key] = found if not found.isNull() else QIcon.fromTheme(fallback)
    return _ICONS[key]


def app_icon() -> QIcon:
    found = QIcon.fromTheme("myos-taskmanager")
    if found.isNull():
        local = Path(__file__).resolve().parents[1] / "data" / "myos-taskmanager.svg"
        found = QIcon(str(local)) if local.exists() else QIcon.fromTheme("utilities-system-monitor")
    return found


def in_thread(work) -> None:
    threading.Thread(target=work, daemon=True).start()


def post(bridge, signal: str, *args) -> None:
    """Hand a result from a background thread to the window. The window may have
    been closed in the meantime; then the result is simply dropped."""
    try:
        if not sip.isdeleted(bridge):
            getattr(bridge, signal).emit(*args)
    except RuntimeError:
        pass


def run_task(text: str) -> str:
    """Open a program, folder, file or web address. Returns an error message, '' if it worked."""
    text = text.strip()
    if not text:
        return "Type the name of a program, a folder or a web address."
    path = os.path.expanduser(text)
    if os.path.exists(path):
        return "" if QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path))) \
            else f"{text} could not be opened."
    if "://" in text:
        return "" if QDesktopServices.openUrl(QUrl(text)) else f"{text} could not be opened."
    try:
        argv = shlex.split(text)
    except ValueError as e:
        return f"That command is not complete: {e}."
    if not argv or not shutil.which(argv[0]):
        return f"There is no program called \"{argv[0] if argv else text}\"."
    launcher = ["kstart", "--"] if shutil.which("kstart") else []
    try:
        subprocess.Popen(launcher + argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return f"It could not be started: {e}."
    return ""


# ── sampling in the background ───────────────────────────────────────────────
class Bridge(QObject):
    sampled = pyqtSignal(object, object)        # SystemSnapshot, process list or None
    done = pyqtSignal(str, bool, str)           # an action finished: what, ok, message
    services = pyqtSignal(object, str)          # list of services or None, error text


class Sampler(threading.Thread):
    """Reads the system once per interval and hands the result to the window."""

    def __init__(self, bridge: Bridge, root="/"):
        super().__init__(daemon=True, name="sampler")
        self.bridge = bridge
        self.root = root
        self.interval = SPEEDS["normal"]
        self.paused = False
        self.want_processes = True
        self._wake = threading.Event()
        self._quit = False
        self.nvidia = None

    def run(self):
        system = procfs.SystemSampler(self.root)
        processes = procfs.ProcessSampler(self.root)
        self.nvidia = procfs.NvidiaMonitor()
        while not self._quit:
            started = time.monotonic()
            if not self.paused:
                try:
                    snap = system.sample()
                    snap.gpus = snap.gpus + self.nvidia.latest()
                    procs = processes.sample() if self.want_processes else None
                    if not self._quit:
                        post(self.bridge, "sampled", snap, procs)
                except Exception as e:      # keep sampling; one bad read must not stop the app
                    if not self._quit:
                        print(f"myos-taskmanager: could not read the system: {e}", file=sys.stderr)
            self._wake.wait(max(0.05, self.interval - (time.monotonic() - started)))
            self._wake.clear()

    def refresh(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._quit = True
        self._wake.set()
        if self.nvidia is not None:
            self.nvidia.stop()


# ── Processes ────────────────────────────────────────────────────────────────
C_NAME, C_STATUS, C_CPU, C_MEM, C_DISK = range(5)


class ProcessesTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.top = []       # top-level rows
        self.rows = {}      # key → row, including an app's processes
        self._rendering = False
        # The first line of these headers is left free for the live totals.
        self.tree = KeyedTree(["\nName", "\nStatus", "\nCPU", "\nMemory", "\nDisk"],
                              numeric=(C_CPU, C_MEM, C_DISK), header=TotalsHeader())
        self.tree.setIconSize(QSize(16, 16))
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((C_STATUS, 100), (C_CPU, 80), (C_MEM, 110), (C_DISK, 95)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.tree.setColumnWidth(col, width)
        self.tree.sortByColumn(C_NAME, Qt.SortOrder.AscendingOrder)
        header.sortIndicatorChanged.connect(lambda *_: self.render())
        self.tree.currentItemChanged.connect(lambda *_: self._update_button())
        self.tree.customContextMenuRequested.connect(self._menu)

        self.end_btn = QPushButton("End task")
        self.end_btn.setEnabled(False)
        self.end_btn.clicked.connect(self.end_selected)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 0, 8, 0)
        bottom.addStretch(1)
        bottom.addWidget(self.end_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.addWidget(self.tree, 1)
        lay.addLayout(bottom)

    # data
    def feed(self, snap, procs) -> None:
        if procs is not None:
            self.top = apps.build_rows(procs, self.win.me, self.win.index)
            self.rows = {}
            for row in self.top:
                self.rows[row.key] = row
                for child in row.children:
                    self.rows[child.key] = child
        if snap is not None:
            mem = snap.memory
            used = mem["used"] / mem["total"] * 100 if mem.get("total") else 0
            disk = max((d["active"] for d in snap.disks.values()), default=0)
            head = self.tree.header()
            for col, value in ((C_CPU, snap.cpu), (C_MEM, used), (C_DISK, disk)):
                head.set_total(col, formats.whole_percent(value))
        self.render()

    def _spec(self, row, parent) -> Spec:
        system = row.section == "system"
        texts = [row.name, row.status, formats.percent(row.cpu),
                 "" if row.key == "kernel" else formats.memory(row.mem),
                 "" if system else formats.rate(row.disk)]
        heat = {C_CPU: formats.heat_level("cpu", row.cpu), C_MEM: formats.heat_level("mem", row.mem),
                C_DISK: formats.heat_level("disk", row.disk)}
        fallback = "applications-system" if system else "window"
        return Spec(row.key, texts, [None, None, row.cpu, row.mem, row.disk], parent,
                    icon=themed_icon(row.icon, fallback), heat=heat)

    def render(self) -> None:
        if self._rendering:
            return
        self._rendering = True
        try:
            grouped = self.tree.sortColumn() == C_NAME
            specs = []
            if grouped:
                for order, (section, title) in enumerate(SECTIONS):
                    count = sum(1 for r in self.top if r.section == section)
                    specs.append(Spec(f"section:{section}", [f"{title} ({count})", "", "", "", ""],
                                      section=order))
            for row in self.top:
                specs.append(self._spec(row, f"section:{row.section}" if grouped else None))
                specs.extend(self._spec(child, row.key) for child in row.children)
            self.tree.sync(specs)
        finally:
            self._rendering = False
        self._update_button()

    # selection and actions
    def current_row(self):
        return self.rows.get(self.tree.current_key())

    def _update_button(self) -> None:
        row = self.current_row()
        kind = self.win.danger(row.procs) if row else "never"
        self.end_btn.setText("Restart" if kind == "restart" else "End task")
        self.end_btn.setEnabled(row is not None and kind != "never")

    def end_selected(self) -> None:
        row = self.current_row()
        if row:
            self.win.end_processes(row.procs, row.name)

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        row = self.rows.get(item.data(0, KEY_ROLE)) if item else None
        if row is None:
            return
        self.tree.setCurrentItem(item)
        kind = self.win.danger(row.procs)
        menu = QMenu(self)
        end = menu.addAction("Restart" if kind == "restart" else "End task")
        end.setEnabled(kind != "never")
        end.triggered.connect(self.end_selected)
        if row.children:
            toggle = menu.addAction("Collapse" if item.isExpanded() else "Expand")
            toggle.triggered.connect(lambda: item.setExpanded(not item.isExpanded()))
        menu.addSeparator()
        first = row.procs[0] if row.procs and row.key != "kernel" else None
        details = menu.addAction("Go to details")
        details.setEnabled(first is not None)
        details.triggered.connect(lambda: self.win.show_in_details(first))
        where = menu.addAction("Open file location")
        where.setEnabled(first is not None)
        where.triggered.connect(lambda: self.win.open_location(first))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def expand_all(self, expand: bool) -> None:
        for row in self.top:
            item = self.tree.item_for(row.key)
            if item is not None and row.children:
                item.setExpanded(expand)


# ── Details ──────────────────────────────────────────────────────────────────
class DetailsTab(QWidget):
    COLS = ["Name", "PID", "Status", "User name", "CPU", "Memory", "Command line"]

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.procs = {}
        self.all = []
        self.tree = KeyedTree(self.COLS, numeric=(1, 4, 5))
        self.tree.setRootIsDecorated(False)
        header = self.tree.header()
        for col, width in enumerate((190, 70, 90, 100, 70, 100, 400)):
            self.tree.setColumnWidth(col, width)
        header.setStretchLastSection(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.currentItemChanged.connect(lambda *_: self._update_button())
        self.tree.customContextMenuRequested.connect(self._menu)
        self.end_btn = QPushButton("End task")
        self.end_btn.setEnabled(False)
        self.end_btn.clicked.connect(lambda: self.end_selected(False))
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 0, 8, 0)
        bottom.addStretch(1)
        bottom.addWidget(self.end_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.addWidget(self.tree, 1)
        lay.addLayout(bottom)

    def feed(self, procs) -> None:
        if procs is None:
            return
        self.all = procs
        self.procs = {}
        specs = []
        kernel_cpu, kernel = 0.0, False
        for p in procs:
            if p.kernel:
                kernel, kernel_cpu = True, kernel_cpu + p.cpu
                continue
            key = f"pid:{p.pid}:{p.start}"
            self.procs[key] = p
            specs.append(Spec(key, [p.name, str(p.pid), p.status, p.user, formats.percent(p.cpu),
                                    formats.memory(p.mem), p.cmdline],
                              [None, p.pid, None, None, p.cpu, p.mem, None],
                              tooltip=apps.KNOWN.get(p.name, "")))
        if kernel:
            specs.append(Spec("kernel", ["Linux kernel", "", "Running", "root",
                                         formats.percent(kernel_cpu), "", ""],
                              [None, 0, None, None, kernel_cpu, 0, None],
                              tooltip="The core of the system (all kernel threads together)"))
        self.tree.sync(specs)
        self._update_button()

    def current(self):
        return self.procs.get(self.tree.current_key())

    def _update_button(self) -> None:
        p = self.current()
        kind = self.win.danger([p]) if p else "never"
        self.end_btn.setText("Restart" if kind == "restart" else "End task")
        self.end_btn.setEnabled(p is not None and kind != "never")

    def end_selected(self, tree: bool) -> None:
        p = self.current()
        if p is None:
            return
        if tree:
            pids = set(procfs.descendants(self.all, p.pid))
            group = [q for q in self.all if q.pid in pids and not q.kernel]
            more = len(group) - 1
            label = p.name if more < 1 else f"{p.name} and the {more} process{'es' * (more != 1)} it started"
            self.win.end_processes(group, label, confirm=True)
        else:
            self.win.end_processes([p], p.name, confirm=True)

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        p = self.procs.get(item.data(0, KEY_ROLE)) if item else None
        if p is None:
            return
        self.tree.setCurrentItem(item)
        kind = self.win.danger([p])
        menu = QMenu(self)
        end = menu.addAction("Restart" if kind == "restart" else "End task")
        end.setEnabled(kind != "never")
        end.triggered.connect(lambda: self.end_selected(False))
        tree = menu.addAction("End process tree")
        tree.setEnabled(kind in ("", "system"))
        tree.triggered.connect(lambda: self.end_selected(True))
        menu.addSeparator()
        where = menu.addAction("Open file location")
        where.triggered.connect(lambda: self.win.open_location(p))
        copy = menu.addAction("Copy command line")
        copy.setEnabled(bool(p.cmdline))
        copy.triggered.connect(lambda: QApplication.clipboard().setText(p.cmdline))
        menu.exec(self.tree.viewport().mapToGlobal(pos))


# ── Performance ──────────────────────────────────────────────────────────────
class SidebarEntry(QWidget):
    """One line of the list on the left: a small graph, a title and the current value."""

    def __init__(self, title: str, color: str):
        super().__init__()
        self.graph = Graph(QColor(color))
        self.graph.setMinimumSize(0, 0)
        self.graph.setFixedSize(62, 38)
        self.title = QLabel(title)
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() + 1.5)
        self.title.setFont(font)
        self.sub = QLabel("")
        self.sub.setStyleSheet(GRAY)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(self.title)
        text.addWidget(self.sub)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(self.graph)
        lay.addLayout(text, 1)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


class PerfPage(QWidget):
    """The right side: a title, one or two graphs, big numbers and a list of facts."""

    def __init__(self, title: str, color: str, graphs: int, stats: list, facts: list):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        head = QHBoxLayout()
        self.title = QLabel(title)
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.9)
        self.title.setFont(font)
        self.subtitle = QLabel("")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        head.addWidget(self.title)
        head.addWidget(self.subtitle, 1)
        lay.addLayout(head)

        self.graphs, self.captions, self.scales, self.spans = [], [], [], []
        for _ in range(graphs):
            caption, scale = QLabel(""), QLabel("")
            for label in (caption, scale):
                label.setStyleSheet(GRAY)
            scale.setAlignment(Qt.AlignmentFlag.AlignRight)
            top = QHBoxLayout()
            top.addWidget(caption)
            top.addWidget(scale, 1)
            graph = Graph(QColor(color))
            span, zero = QLabel("60 seconds"), QLabel("0")
            for label in (span, zero):
                label.setStyleSheet(GRAY)
            zero.setAlignment(Qt.AlignmentFlag.AlignRight)
            foot = QHBoxLayout()
            foot.addWidget(span)
            foot.addWidget(zero, 1)
            lay.addLayout(top)
            lay.addWidget(graph, 1)
            lay.addLayout(foot)
            self.graphs.append(graph)
            self.captions.append(caption)
            self.scales.append(scale)
            self.spans.append(span)

        lower = QHBoxLayout()
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(28)
        self.grid.setVerticalSpacing(0)
        self.stats, self._placed = list(stats), None
        self.values, self.names = {}, {}
        for name in stats:
            caption, value = QLabel(name), QLabel("")
            caption.setStyleSheet(GRAY)
            font = value.font()
            font.setPointSizeF(font.pointSizeF() * 1.5)
            value.setFont(font)
            caption.hide()
            value.hide()
            self.values[name], self.names[name] = value, caption
        left = QVBoxLayout()
        left.addLayout(self.grid)
        left.addStretch(1)
        lower.addLayout(left)
        lower.addSpacing(24)
        info = QGridLayout()
        info.setHorizontalSpacing(14)
        info.setColumnStretch(1, 1)
        for i, name in enumerate(facts):
            caption, value = QLabel(name + ":"), ElidedLabel("")
            caption.setStyleSheet(GRAY)
            info.addWidget(caption, i, 0)
            info.addWidget(value, i, 1)
            self.values[name], self.names[name] = value, caption
        right = QVBoxLayout()
        right.addLayout(info)
        right.addStretch(1)
        lower.addStretch(1)
        lower.addLayout(right, 2)
        lay.addLayout(lower)

    def set(self, name: str, text: str) -> None:
        """Show a value; an empty text hides the line."""
        value, caption = self.values[name], self.names[name]
        if value.text() != text:
            value.setText(text)
            value.setToolTip(text if name not in self.stats else "")
        if name in self.stats:
            self._place_stats()
        else:
            value.setVisible(bool(text))
            caption.setVisible(bool(text))

    def _place_stats(self) -> None:
        """Lay the big numbers out three to a row, leaving no gap for hidden ones."""
        shown = [n for n in self.stats if self.values[n].text()]
        if shown == self._placed:
            return
        self._placed = shown
        for n in self.stats:
            for widget in (self.names[n], self.values[n]):
                self.grid.removeWidget(widget)
                widget.setVisible(n in shown)
        for i, n in enumerate(shown):
            self.grid.addWidget(self.names[n], (i // 3) * 2, i % 3)
            self.grid.addWidget(self.values[n], (i // 3) * 2 + 1, i % 3)

    def set_span(self, seconds: int) -> None:
        """How much time the graphs cover (changes with the update speed)."""
        text = f"{seconds} seconds" if seconds < 120 else f"{seconds // 60} minutes"
        for label in self.spans:
            if label.text() != text:
                label.setText(text)

    def scale(self, index: int, caption: str, top: str) -> None:
        if self.captions[index].text() != caption:
            self.captions[index].setText(caption)
        if self.scales[index].text() != top:
            self.scales[index].setText(top)


class PerformanceTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.order = []         # resource ids in list order: cpu, mem, disk:sda, net:eth0, gpu:card0
        self.entries, self.pages, self.meta = {}, {}, {}
        self.hist = {}          # id → {series name: deque}
        self.cpu = procfs.cpu_info(win.root)
        self._names = {}        # gpu id → name
        self._tick = 0
        self.list = QListWidget()
        self.list.setFixedWidth(236)
        self.list.setStyleSheet(
            "QListWidget::item { border: none; }"
            "QListWidget::item:selected { background: rgba(17, 125, 187, 50); }"
            "QListWidget::item:hover:!selected { background: rgba(17, 125, 187, 22); }")
        self.stack = QStackedWidget()
        self.list.currentRowChanged.connect(self._row_changed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.list)
        lay.addWidget(self.stack, 1)

    def _row_changed(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            if self.win.snapshot is not None:
                self._update(self.order[row], self.win.snapshot, True)

    def _series(self, rid: str, name: str) -> deque:
        return self.hist.setdefault(rid, {}).setdefault(name, deque(maxlen=HISTORY))

    def _ids(self, snap) -> list:
        return (["cpu", "mem"] + [f"disk:{n}" for n in snap.disks] + [f"net:{n}" for n in snap.nets]
                + [f"gpu:{g['id']}" for g in snap.gpus])

    def _rebuild(self, ids) -> None:
        row = self.list.currentRow()
        selected = self.order[row] if 0 <= row < len(self.order) else None
        self.list.blockSignals(True)
        self.list.clear()
        while self.stack.count():
            page = self.stack.widget(0)
            self.stack.removeWidget(page)
            page.deleteLater()
        self.entries, self.pages, self.meta = {}, {}, {}
        self.hist = {k: v for k, v in self.hist.items() if k in ids}
        self.order = list(ids)
        nets = dict(procfs.list_nets(self.win.root))
        count = {}
        for rid in ids:
            kind, _, name = rid.partition(":")
            index = count.get(kind, 0)
            count[kind] = index + 1
            meta = {"kind": kind, "name": name}
            if kind == "cpu":
                title = "CPU"
                page = PerfPage(title, COLORS[kind], 1,
                                ["Utilization", "Speed", "Processes", "Threads", "Up time"],
                                ["Sockets", "Cores", "Logical processors"])
                page.subtitle.setText(self.cpu["model"])
                page.set("Sockets", str(self.cpu["sockets"]) if self.cpu["sockets"] else "")
                page.set("Cores", str(self.cpu["cores"]) if self.cpu["cores"] else "")
                page.set("Logical processors", str(self.cpu["logical"]))
            elif kind == "mem":
                title = "Memory"
                page = PerfPage(title, COLORS[kind], 1,
                                ["In use", "Available", "Cached", "Compressed", "Swap", "Pressure"], [])
                page.names["Compressed"].setToolTip(
                    "Memory that was not used for a while is kept compressed instead of being\n"
                    "thrown out, so more apps fit into the RAM.")
                page.names["Pressure"].setToolTip(
                    "How much programs had to wait for memory lately. High means: close something.")
            elif kind == "disk":
                info = procfs.disk_info(self.win.root, name)
                meta["disk"] = info
                title = f"Disk {index} ({name})"
                page = PerfPage(title, COLORS[kind], 2, ["Active time", "Read speed", "Write speed"],
                                ["Capacity", "Type"])
                page.subtitle.setText(info["model"])
                page.set("Capacity", formats.size(info["size"]))
                page.set("Type", info["kind"])
            elif kind == "net":
                meta["type"] = nets.get(name, "Ethernet")
                same = sum(1 for other in ids[:ids.index(rid)]
                           if other.startswith("net:") and nets.get(other[4:], "Ethernet") == meta["type"])
                title = meta["type"] + (f" {same + 1}" if same else "")
                page = PerfPage(title, COLORS[kind], 1, ["Send", "Receive"],
                                ["Adapter name", "Connection type", "IPv4 address", "IPv6 address",
                                 "Signal", "Link speed"])
                page.subtitle.setText(name)
                page.set("Adapter name", name)
                page.set("Connection type", meta["type"])
            else:
                title = f"GPU {index}"
                page = PerfPage(title, COLORS[kind], 2, ["Utilization", "Dedicated memory", "Temperature"], [])
            entry = SidebarEntry(title, COLORS[kind])
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 54))
            self.list.addItem(item)
            self.list.setItemWidget(item, entry)
            self.stack.addWidget(page)
            self.entries[rid], self.pages[rid], self.meta[rid] = entry, page, meta
        self.list.blockSignals(False)
        row = self.order.index(selected) if selected in self.order else 0
        self.list.setCurrentRow(row)
        self.stack.setCurrentIndex(row)

    def feed(self, snap, visible: bool) -> None:
        ids = self._ids(snap)
        if ids != self.order:
            self._rebuild(ids)
        self._tick += 1
        current = self.order[self.stack.currentIndex()] if self.order else None
        for rid in ids:
            self._record(rid, snap)
            if visible:
                self._update(rid, snap, rid == current)

    def refresh(self, snap) -> None:
        """Redraw everything from the history (after switching to this tab)."""
        current = self.order[self.stack.currentIndex()] if self.order else None
        for rid in self.order:
            self._update(rid, snap, rid == current)

    def _gpu(self, snap, name: str):
        return next((g for g in snap.gpus if g["id"] == name), None)

    def _record(self, rid: str, snap) -> None:
        kind, name = self.meta[rid]["kind"], self.meta[rid]["name"]
        if kind == "cpu":
            self._series(rid, "use").append(snap.cpu)
        elif kind == "mem":
            self._series(rid, "use").append(snap.memory.get("used", 0))
        elif kind == "disk":
            d = snap.disks.get(name, {})
            self._series(rid, "use").append(d.get("active", 0))
            self._series(rid, "read").append(d.get("read", 0))
            self._series(rid, "write").append(d.get("write", 0))
        elif kind == "net":
            n = snap.nets.get(name, {})
            self._series(rid, "down").append(n.get("down", 0))
            self._series(rid, "up").append(n.get("up", 0))
        else:
            g = self._gpu(snap, name) or {}
            self._series(rid, "use").append(g.get("busy", 0))
            self._series(rid, "mem").append(g.get("mem_used", 0))

    def _update(self, rid: str, snap, page_shown: bool) -> None:
        entry, page, meta = self.entries[rid], self.pages[rid], self.meta[rid]
        kind, name = meta["kind"], meta["name"]
        if page_shown:
            page.set_span(round(HISTORY * self.win.sampler.interval))
        if kind == "cpu":
            use = self._series(rid, "use")
            speed = formats.speed(snap.cpu_mhz)
            entry.sub.setText(f"{formats.whole_percent(snap.cpu)}  {speed}".strip())
            entry.graph.set_data([use], 100)
            if page_shown:
                page.scale(0, "% Utilization", "100%")
                page.graphs[0].set_data([use], 100)
                page.set("Utilization", formats.whole_percent(snap.cpu))
                page.set("Speed", speed)
                page.set("Processes", str(snap.processes))
                page.set("Threads", str(snap.threads) if snap.threads else "")
                page.set("Up time", formats.uptime(snap.uptime))
        elif kind == "mem":
            mem, use = snap.memory, self._series(rid, "use")
            total = mem.get("total", 0) or 1
            share = mem.get("used", 0) / total * 100
            entry.sub.setText(f"{mem.get('used', 0) / formats.GB:.1f}/{total / formats.GB:.1f} GB "
                              f"({formats.whole_percent(share)})")
            entry.graph.set_data([use], total)
            if page_shown:
                page.subtitle.setText(formats.size(total))
                page.scale(0, "Memory usage", formats.size(total))
                page.graphs[0].set_data([use], total)
                page.set("In use", formats.size(mem.get("used", 0)))
                page.set("Available", formats.size(mem.get("available", 0)))
                page.set("Cached", formats.size(mem.get("cached", 0)))
                page.set("Swap", f"{formats.size(mem.get('swap_used', 0))} of {formats.size(mem['swap_total'])}"
                         if mem.get("swap_total") else "")
                page.set("Compressed", f"{formats.size(mem['compressed'])} (takes "
                                       f"{formats.size(mem.get('compressed_takes', 0))})"
                         if mem.get("compressed", 0) >= formats.MB else "")
                page.set("Pressure", formats.pressure(mem.get("pressure", 0.0)))
        elif kind == "disk":
            d = snap.disks.get(name, {"active": 0, "read": 0, "write": 0})
            use, read, write = (self._series(rid, s) for s in ("use", "read", "write"))
            entry.sub.setText(f"{disk_short(meta['disk']['kind'])}  {formats.whole_percent(d['active'])}")
            entry.graph.set_data([use], 100)
            if page_shown:
                top = formats.rate_scale(max(list(read) + list(write) + [0]))
                page.scale(0, "Active time", "100%")
                page.graphs[0].set_data([use], 100)
                page.scale(1, "Disk transfer rate (read: line, write: dashes)", formats.rate(top))
                page.graphs[1].set_data([read, write], top)
                page.set("Active time", formats.whole_percent(d["active"]))
                page.set("Read speed", formats.rate(d["read"]))
                page.set("Write speed", formats.rate(d["write"]))
        elif kind == "net":
            n = snap.nets.get(name, {"down": 0, "up": 0})
            down, up = self._series(rid, "down"), self._series(rid, "up")
            entry.sub.setText(f"S: {formats.bits(n['up'])}  R: {formats.bits(n['down'])}")
            top = formats.bits_scale(max(list(down) + list(up) + [0]))
            entry.graph.set_data([down, up], top)
            if page_shown:
                page.scale(0, "Throughput (receive: line, send: dashes)", formats.bits(top))
                page.graphs[0].set_data([down, up], top)
                page.set("Send", formats.bits(n["up"]))
                page.set("Receive", formats.bits(n["down"]))
                if self._tick % 5 == 1 or not page.values["IPv4 address"].text():
                    info = procfs.net_info(self.win.root, name)
                    page.set("IPv4 address", info["ipv4"] or ("Not connected" if not info["connected"] else ""))
                    page.set("IPv6 address", info["ipv6"])
                    page.set("Signal", f"{info['signal']}%" if info["signal"] >= 0 else "")
                    speed = info["speed_mbit"]
                    page.set("Link speed", "" if not speed else f"{speed / 1000:g} Gbps" if speed >= 1000
                             else f"{speed} Mbps")
        else:
            g = self._gpu(snap, name)
            if g is None:
                return
            if name not in self._names:
                self._names[name] = g.get("name") or procfs.pci_name(g.get("slot", "")) or "Graphics card"
            use, mem = self._series(rid, "use"), self._series(rid, "mem")
            total = g.get("mem_total", 0)
            temp = f"{g['temp']} °C" if g.get("temp") is not None else ""
            entry.sub.setText(f"{g['busy']}%" + (f" ({temp})" if temp else ""))
            entry.graph.set_data([use], 100)
            if page_shown:
                page.subtitle.setText(self._names[name])
                page.scale(0, "Utilization", "100%")
                page.graphs[0].set_data([use], 100)
                page.scale(1, "Dedicated GPU memory usage", formats.size(total) if total else "")
                page.graphs[1].set_data([mem], total or 1)
                page.set("Utilization", f"{g['busy']}%")
                page.set("Dedicated memory", f"{formats.size(g.get('mem_used', 0))} of {formats.size(total)}"
                         if total else "")
                page.set("Temperature", temp)


def disk_short(kind: str) -> str:
    """Short type of a disk for the list on the left."""
    return {"Hard disk (HDD)": "HDD", "SSD (NVMe)": "SSD", "Virtual disk": "Disk",
            "Memory card": "Card", "USB drive": "USB"}.get(kind, kind)


# ── Startup ──────────────────────────────────────────────────────────────────
class StartupTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.entries = {}
        self.tree = KeyedTree(["Name", "Status", "Command", "Added by"])
        self.tree.setRootIsDecorated(False)
        self.tree.setIconSize(QSize(16, 16))
        for col, width in enumerate((230, 90, 300, 80)):
            self.tree.setColumnWidth(col, width)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.currentItemChanged.connect(lambda *_: self._update_button())
        self.tree.customContextMenuRequested.connect(self._menu)
        self.note = QLabel("")
        self.note.setStyleSheet(GRAY)
        self.btn = QPushButton("Disable")
        self.btn.setEnabled(False)
        self.btn.clicked.connect(self.toggle)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 0, 8, 0)
        bottom.addWidget(self.note, 1)
        bottom.addWidget(self.btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.addWidget(self.tree, 1)
        lay.addLayout(bottom)

    def reload(self) -> None:
        self.entries = {e.file_id: e for e in startup.list_entries()}
        self.tree.sync([Spec(e.file_id, [e.name, e.status, e.command, e.source],
                             icon=themed_icon(e.icon)) for e in self.entries.values()])
        self.note.setText("" if self.entries else "No apps are set to start when you log in.")
        self._update_button()

    def current(self):
        return self.entries.get(self.tree.current_key())

    def _update_button(self) -> None:
        entry = self.current()
        self.btn.setEnabled(entry is not None)
        self.btn.setText("Enable" if entry is not None and not entry.enabled else "Disable")

    def toggle(self) -> None:
        entry = self.current()
        if entry is None:
            return
        try:
            startup.set_enabled(entry, not entry.enabled)
        except OSError as e:
            QMessageBox.warning(self, "Task Manager", f"{entry.name} could not be changed: {e}")
        self.reload()

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        entry = self.current()
        if entry is None:
            return
        menu = QMenu(self)
        menu.addAction("Enable" if not entry.enabled else "Disable").triggered.connect(self.toggle)
        menu.exec(self.tree.viewport().mapToGlobal(pos))


# ── Services ─────────────────────────────────────────────────────────────────
class ServicesTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.items = {}
        self._loading = False
        self.tree = KeyedTree(["Name", "PID", "Description", "Status", "Startup"], numeric=(1,))
        self.tree.setRootIsDecorated(False)
        for col, width in enumerate((200, 70, 300, 90, 110)):
            self.tree.setColumnWidth(col, width)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.tree.customContextMenuRequested.connect(self._menu)
        self.note = QLabel("")
        self.note.setStyleSheet(GRAY)
        self.buttons = {}
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 0, 8, 0)
        bottom.addWidget(self.note, 1)
        for verb in ("start", "stop", "restart"):
            button = QPushButton(verb.capitalize())
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, v=verb: self.control(v))
            bottom.addWidget(button)
            self.buttons[verb] = button
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.addWidget(self.tree, 1)
        lay.addLayout(bottom)
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.reload)
        win.bridge.services.connect(self._loaded)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    def reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        bridge = self.win.bridge

        def work():
            try:
                found, error = services.list_services(), ""
            except Exception as e:      # no systemd, or it does not answer
                found, error = None, str(e)
            post(bridge, "services", found, error)
        in_thread(work)

    def _loaded(self, items, error: str) -> None:
        self._loading = False
        if items is None:
            self.note.setText(error)
            return
        self.note.setText("")
        self.items = {s.unit: s for s in items}
        self.tree.sync([Spec(s.unit, [s.name, str(s.pid) if s.pid else "", s.description, s.status, s.startup],
                             [None, s.pid, None, None, None]) for s in items])
        self._update_buttons()

    def current(self):
        return self.items.get(self.tree.current_key())

    def _allowed(self, svc) -> dict:
        if svc is None:
            return {"start": False, "stop": False, "restart": False}
        running = svc.status in ("Running", "Finished", "Starting")
        return {"start": not running, "stop": running and not svc.protected,
                "restart": running and not svc.protected}

    def _update_buttons(self) -> None:
        for verb, ok in self._allowed(self.current()).items():
            self.buttons[verb].setEnabled(ok)

    def control(self, verb: str) -> None:
        svc = self.current()
        if svc is None or not self._allowed(svc)[verb]:
            return
        if verb != "start":
            text = (f"{verb.capitalize()} the service \"{svc.name}\"?\n\n"
                    "Things that depend on it can stop working until it runs again.")
            if QMessageBox.question(self, "Task Manager", text) != QMessageBox.StandardButton.Yes:
                return
        bridge, unit = self.win.bridge, svc.unit
        what = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}[verb] + f" {svc.name}"

        def work():
            ok, message = services.control(unit, verb)
            post(bridge, "done", what, ok, message)
        in_thread(work)

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        svc = self.current()
        if svc is None:
            return
        allowed = self._allowed(svc)
        menu = QMenu(self)
        for verb in ("start", "stop", "restart"):
            action = menu.addAction(verb.capitalize())
            action.setEnabled(allowed[verb])
            action.triggered.connect(lambda _checked=False, v=verb: self.control(v))
        if svc.protected:
            note = menu.addAction("Needed by the system: can't be stopped here")
            note.setEnabled(False)
        menu.exec(self.tree.viewport().mapToGlobal(pos))


# ── the window ───────────────────────────────────────────────────────────────
class RunDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create new task")
        self.setMinimumWidth(420)
        hint = QLabel("Type the name of a program, folder, document or web address, "
                      "and Task Manager will open it for you.")
        hint.setWordWrap(True)
        self.edit = QLineEdit()
        row = QHBoxLayout()
        row.addWidget(QLabel("Open:"))
        row.addWidget(self.edit, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(row)
        lay.addWidget(box)


class TaskManager(QMainWindow):
    def __init__(self, root="/", sample=True):
        super().__init__()
        self.root = root
        self.me = os.getuid()
        self.index = apps.DesktopIndex()
        self.snapshot = None
        self.procs = []
        self.config = load_config()
        self.setWindowTitle("Task Manager")
        self.resize(780, 640)

        self.bridge = Bridge()
        self.bridge.sampled.connect(self._sampled)
        self.bridge.done.connect(self._action_done)
        self.sampler = Sampler(self.bridge, root)

        self.processes = ProcessesTab(self)
        self.performance = PerformanceTab(self)
        self.startup = StartupTab(self)
        self.details = DetailsTab(self)
        self.services = ServicesTab(self)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for page, title in ((self.processes, "Processes"), (self.performance, "Performance"),
                            (self.startup, "Startup"), (self.details, "Details"),
                            (self.services, "Services")):
            self.tabs.addTab(page, title)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self._build_menus()

        geometry = self.config.get("geometry")
        if geometry:
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(geometry)))
            except Exception:
                pass
        self.set_speed(self.config.get("speed", "normal"))
        if self.config.get("tab") in TABS:
            self.go(self.config["tab"])
        if sample:
            self.sampler.start()

    # menus
    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        run = QAction("&Run new task", self)
        run.setShortcut(QKeySequence("Ctrl+N"))
        run.triggered.connect(self.run_new_task)
        leave = QAction("E&xit", self)
        leave.triggered.connect(self.close)
        file_menu.addAction(run)
        file_menu.addSeparator()
        file_menu.addAction(leave)

        if shutil.which(PERFORMANCE_TOOL):
            options = bar.addMenu("&Options")
            effects = options.addMenu("Visual effects")
            self.effect_actions = {}
            group = QActionGroup(self)
            for mode, label in (("auto", "Let the system choose"), ("fast", "Best performance"),
                                ("normal", "Best appearance")):
                action = QAction(label, self, checkable=True)
                action.triggered.connect(lambda _checked=False, m=mode: self.set_effects(m))
                group.addAction(action)
                effects.addAction(action)
                self.effect_actions[mode] = action
            effects.aboutToShow.connect(self._read_effects)

        view = bar.addMenu("&View")
        refresh = QAction("&Refresh now", self)
        refresh.setShortcut(QKeySequence("F5"))
        refresh.triggered.connect(self.refresh_now)
        view.addAction(refresh)
        speed = view.addMenu("&Update speed")
        self.speed_actions = {}
        group = QActionGroup(self)
        for key, label in (("high", "High"), ("normal", "Normal"), ("low", "Low"), ("paused", "Paused")):
            action = QAction(label, self, checkable=True)
            action.triggered.connect(lambda _checked=False, k=key: self.set_speed(k))
            group.addAction(action)
            speed.addAction(action)
            self.speed_actions[key] = action
        view.addSeparator()
        view.addAction("&Expand all").triggered.connect(lambda: self.processes.expand_all(True))
        view.addAction("&Collapse all").triggered.connect(lambda: self.processes.expand_all(False))

    def set_speed(self, key: str) -> None:
        if key not in self.speed_actions:
            key = "normal"
        self.speed_actions[key].setChecked(True)
        self.sampler.paused = (key == "paused")
        if key in SPEEDS:
            self.sampler.interval = SPEEDS[key]
        self.config["speed"] = key
        self.sampler.refresh()

    def refresh_now(self) -> None:
        self.sampler.refresh()
        index = self.tabs.currentIndex()
        if index == T_STARTUP:
            self.startup.reload()
        elif index == T_SERVICES:
            self.services.reload()

    def _read_effects(self) -> None:
        try:
            out = subprocess.run([PERFORMANCE_TOOL, "status", "--short"], capture_output=True,
                                 text=True, timeout=10).stdout.split()
        except (OSError, subprocess.SubprocessError):
            out = []
        if out and out[0] in self.effect_actions:
            self.effect_actions[out[0]].setChecked(True)

    def set_effects(self, mode: str) -> None:
        bridge = self.bridge

        def work():
            try:
                r = subprocess.run([PERFORMANCE_TOOL, mode], capture_output=True, text=True, timeout=60)
                post(bridge, "done", "Changing the visual effects", r.returncode == 0,
                     (r.stderr or r.stdout).strip() if r.returncode else "")
            except (OSError, subprocess.SubprocessError) as e:
                post(bridge, "done", "Changing the visual effects", False, str(e))
        in_thread(work)

    def run_new_task(self) -> None:
        dialog = RunDialog(self)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            error = run_task(dialog.edit.text())
            if not error:
                break
            QMessageBox.warning(self, "Task Manager", error)

    # tabs
    def go(self, tab: str) -> None:
        if tab in TABS:
            self.tabs.setCurrentIndex(TABS.index(tab))

    def _wants_processes(self) -> bool:
        return not self.isMinimized() and self.tabs.currentIndex() in (T_PROCESSES, T_DETAILS)

    def _tab_changed(self, index: int) -> None:
        self.sampler.want_processes = self._wants_processes()
        if index == T_STARTUP:
            self.startup.reload()
        elif index == T_SERVICES:
            self.services.reload()
        elif index == T_PROCESSES:
            self.processes.feed(self.snapshot, self.procs or None)
        elif index == T_DETAILS:
            self.details.feed(self.procs or None)
        elif index == T_PERFORMANCE and self.snapshot is not None:
            self.performance.refresh(self.snapshot)
        self.sampler.refresh()

    def changeEvent(self, event):
        super().changeEvent(event)
        if getattr(self, "tabs", None) is not None:     # also called while the window is being built
            self.sampler.want_processes = self._wants_processes()

    def _sampled(self, snap, procs) -> None:
        self.snapshot = snap
        if procs is not None:
            self.procs = procs
        shown = not self.isMinimized()
        index = self.tabs.currentIndex()
        self.performance.feed(snap, shown and index == T_PERFORMANCE)
        if shown and index == T_PROCESSES:
            self.processes.feed(snap, procs)
        elif shown and index == T_DETAILS:
            self.details.feed(procs)

    # actions on processes
    def danger(self, procs) -> str:
        """The strongest warning that applies to ending these processes (see apps.protection)."""
        kinds = {apps.protection(p, self.me) for p in procs}
        if "restart" in kinds and len(procs) > 1:
            kinds.discard("restart")
            kinds.add("session")
        for kind in ("never", "restart", "session", "system"):
            if kind in kinds:
                return kind
        return ""

    def end_processes(self, procs, label: str, confirm: bool = False) -> None:
        procs = [p for p in procs if not p.kernel]
        kind = self.danger(procs) if procs else "never"
        if kind == "never":
            QMessageBox.information(self, "Task Manager",
                                    f"{label} is part of the system and can't be ended.")
            return
        bridge = self.bridge
        if kind == "restart":
            in_thread(lambda: post(bridge, "done", "Restarting the desktop", *actions.restart_desktop()))
            return
        if kind == "session":
            text = (f"Do you want to end {label}?\n\nThis logs you out and closes all your apps. "
                    "Anything you have not saved will be lost.")
        elif kind == "system":
            text = (f"Do you want to end the system process {label}?\n\nParts of the system can stop "
                    "working until the PC is restarted, and unsaved data can be lost. "
                    "You will be asked for the administrator password.")
        elif confirm:
            text = f"Do you want to end {label}?\n\nAnything it has not saved will be lost."
        else:
            text = ""
        if text:
            box = QMessageBox(QMessageBox.Icon.Warning if kind else QMessageBox.Icon.Question,
                              "Task Manager", text, parent=self)
            end = box.addButton("End process", QMessageBox.ButtonRole.AcceptRole)
            cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() is not end:
                return
        own = [p.key for p in procs if p.uid == self.me]
        other = [p.key for p in procs if p.uid != self.me]

        def work():
            failed = actions.end_own(own)
            ok, message = actions.end_as_admin(other)
            if failed and ok:
                ok, message = False, "It did not react and could not be stopped."
            post(bridge, "done", f"Ending {label}", ok, message)
        in_thread(work)

    def _action_done(self, what: str, ok: bool, message: str) -> None:
        self.sampler.refresh()
        if self.tabs.currentIndex() == T_SERVICES:
            self.services.reload()
        if not ok:
            QMessageBox.warning(self, "Task Manager", f"{what} did not work.\n\n{message}".strip())

    def show_in_details(self, proc) -> None:
        if proc is None:
            return
        self.go("details")
        self.details.feed(self.procs or None)
        self.details.tree.select(f"pid:{proc.pid}:{proc.start}")

    def open_location(self, proc) -> None:
        folder = actions.exe_folder(proc.pid) if proc is not None else ""
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            QMessageBox.information(self, "Task Manager",
                                    "The program file of this process can't be looked up "
                                    "(it belongs to the system or has already ended).")

    # window
    def activate(self, tab: str = "", token: str = "") -> None:
        """Another start of Task Manager asked this window to come to the front."""
        if tab:
            self.go(tab)
        if token:
            os.environ["XDG_ACTIVATION_TOKEN"] = token
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.sampler.stop()
        self.config["geometry"] = base64.b64encode(bytes(self.saveGeometry())).decode()
        self.config["tab"] = TABS[self.tabs.currentIndex()]
        save_config(self.config)
        super().closeEvent(event)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    token = os.environ.get("XDG_ACTIVATION_TOKEN", "")
    tab = ""
    if "--tab" in argv and argv.index("--tab") + 1 < len(argv):
        tab = argv[argv.index("--tab") + 1].lower()
    if "--help" in argv or "-h" in argv:
        print("Usage: myos-taskmanager [--tab processes|performance|startup|details|services]")
        return 0
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("myos-taskmanager")
    app.setApplicationDisplayName("Task Manager")
    app.setDesktopFileName("myos-taskmanager")
    app.setWindowIcon(app_icon())

    key = f"myos-taskmanager-{os.getuid()}"
    probe = QLocalSocket()
    probe.connectToServer(key)
    if probe.waitForConnected(300):     # already open: bring that window to the front
        probe.write(json.dumps({"tab": tab, "token": token}).encode("utf-8"))
        probe.flush()
        probe.waitForBytesWritten(1000)
        probe.disconnectFromServer()
        return 0

    QLocalServer.removeServer(key)
    server = QLocalServer()
    server.listen(key)
    win = TaskManager()
    if tab:
        win.go(tab)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(300)
        try:
            message = json.loads(bytes(conn.readAll()).decode("utf-8", "replace") or "{}")
        except ValueError:
            message = {}
        conn.disconnectFromServer()
        win.activate(str(message.get("tab", "")), str(message.get("token", "")))

    server.newConnection.connect(on_connection)

    # Asked to quit (logout, shutdown, "End task" on Task Manager itself): close
    # properly, so the window size and the last tab are remembered. Python only
    # notices such a request while it runs, hence the small timer.
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, lambda *_: win.close())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(500)

    win.show()
    return app.exec()
