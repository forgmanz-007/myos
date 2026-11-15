"""myos-compress PATH: make a Windows program (or any folder) smaller on the disk.

    myos-compress game.exe            opens the window (also reached from the file's
                                      Properties > Windows compatibility > Compress)
    myos-compress --text PATH         says what it is, how big, and what compressing would save
    myos-compress --text --compress [--strong] [--file-only] PATH
    myos-compress --text --undo PATH
"""
import argparse
import sys
import threading
from pathlib import Path

from . import compress as cz

INTRO = ("Compressing makes the files smaller on the disk. The program keeps working exactly as "
         "before, because the system unpacks the files while they are read. Files that are added "
         "later, such as updates, are compressed too.")


def describe_target(target: Path) -> str:
    return f"the folder {target}" if target.is_dir() else f"the file {target}"


def saving_line(est, on_disk) -> str:
    """'After compressing: about 34.1 GB (saves about 14.1 GB, 29%)'."""
    now = on_disk[0] if on_disk else est.size
    saved = max(0, now - est.after)
    if est.shared:
        # The disk figure counts shared data its own way: only what the other files gain is real.
        saved = min(saved, max(0, est.size - est.after))
    if not est.size or saved < max(1024 * 1024, now // 100):
        if est.shared * 2 >= est.size > 0:
            return ("Compressing would save almost nothing here: most of these files share their data "
                    "with a copy or a snapshot, and those are left as they are.")
        return "Compressing would save almost nothing here: these files are already packed."
    return (f"After compressing: about {cz.human(now - saved)} "
            f"(saves about {cz.human(saved)}, {round(saved * 100 / now)}%)")


def shared_line(est) -> str:
    """What is left out because a copy or a snapshot shares its data ('' if nothing)."""
    if not est.shared:
        return ""
    n = est.shared_files
    return (f"{cz.human(est.shared)} in {n:,} file{'s' * (n != 1)} will be left as "
            f"{'they are' if n != 1 else 'it is'}: {'they share their' if n != 1 else 'it shares its'} "
            "data with a copy or a snapshot, and compressing would store it twice.")


def left_line(outcome, undo: bool = False) -> str:
    n = outcome.left
    if not n:
        return ""
    return (f"{n:,} file{'s' * (n != 1)} ({cz.human(outcome.left_size)}) {'were' if n != 1 else 'was'} left as "
            f"{'they were' if n != 1 else 'it was'}: {'they share their' if n != 1 else 'it shares its'} data "
            f"with a copy or a snapshot, and {'unpacking' if undo else 'compressing'} would store it twice. "
            "Once the copy or the snapshot is deleted, run this again.")


def lead(what: str, outcome, usual: str) -> str:
    """How the result starts: as usual, unless nothing at all had to be or could be changed."""
    if outcome.changed or outcome.failed or outcome.cancelled or not outcome.left:
        return usual
    return "Nothing was changed. "


def result_line(before, after, outcome, undo: bool = False) -> str:
    parts = []
    if before and after:
        saved = before[0] - after[0]
        if undo:
            parts.append(f"It now takes {cz.human(after[0])} on the disk (it was {cz.human(before[0])}).")
        elif saved >= max(4096, before[0] // 200):
            parts.append(f"It now takes {cz.human(after[0])} on the disk instead of {cz.human(before[0])} "
                         f"({cz.human(saved)} saved).")
        elif outcome.left:
            parts.append(f"It takes {cz.human(after[0])} on the disk, about the same as before.")
        else:
            parts.append(f"It takes {cz.human(after[0])} on the disk, about the same as before: "
                         "these files were already as small as they get.")
    elif outcome.files:
        parts.append(f"{outcome.files:,} files were handled.")
    if outcome.failed:
        n = len(outcome.failed)
        parts.append(f"{n:,} file{'s' * (n != 1)} could not be changed (in use, or you may not write to "
                     f"{'them' if n != 1 else 'it'}). Close the program and try again.")
    if outcome.left:
        parts.append(left_line(outcome, undo))
    if outcome.note:
        parts.append(outcome.note)
    if outcome.cancelled:
        parts.append("Stopped before the end; what was done so far stays.")
    return " ".join(parts) or "Nothing to do."


# ── in a terminal ────────────────────────────────────────────────────────────
def run_text(args) -> int:
    path = Path(args.path).expanduser()
    if not path.exists():
        print(f"{path} does not exist.", file=sys.stderr)
        return 2
    target = Path(path).resolve() if (args.file_only or path.is_dir()) else cz.default_target(path)
    reason = cz.why_not(target)
    files, size = cz.total_size(target)
    print(f"Target: {describe_target(target)} ({files:,} file{'s' * (files != 1)}, {cz.human(size)})")
    if reason:
        print(reason)
        return 1 if (args.compress or args.undo) else 0
    before = cz.disk_usage(target)
    if before:
        print(f"On the disk now: {cz.human(before[0])}")
    print("Marked for compression: " + ("yes" if cz.is_compressed(target) else "no"))

    shown = {"tenth": 0}

    def progress(_done, done_bytes):
        tenth = done_bytes * 10 // size if size else 10
        if tenth > shown["tenth"] and tenth < 10:
            shown["tenth"] = tenth
            print(f"  {tenth * 10}%", flush=True)

    try:
        if args.undo:
            outcome = cz.decompress(target, progress)
            print(lead("undo", outcome, "Compression undone. ") + result_line(before, cz.disk_usage(target), outcome, undo=True))
            return 1 if outcome.failed else 0
        if args.compress:
            outcome = cz.compress(target, strong=args.strong, progress=progress)
            print(lead("compress", outcome, "Compressed. ") + result_line(before, cz.disk_usage(target), outcome))
            return 1 if outcome.failed else 0
    except cz.CompressError as e:
        print(str(e), file=sys.stderr)
        return 1
    est = cz.estimate(target)
    print(saving_line(est, before))
    if shared_line(est):
        print(shared_line(est))
    print("Compress it with: myos-compress --text --compress " + str(path))
    return 0


# ── the window ───────────────────────────────────────────────────────────────
def run_gui(path: Path) -> int:
    from PyQt6.QtCore import QObject, Qt, pyqtSignal
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                                 QProgressBar, QPushButton, QRadioButton, QVBoxLayout)

    class Bridge(QObject):
        scanned = pyqtSignal(int, object, object, object)       # run number, (files, size), estimate, on disk
        progress = pyqtSignal(int, int)
        finished = pyqtSignal(str, object, object, object)      # what, before, after, outcome or error text

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setDesktopFileName("myos-security")
    path = path.resolve()
    is_file = path.is_file()
    default = cz.default_target(path) if is_file else path
    folder = default if default.is_dir() else path.parent

    dlg = QDialog()
    dlg.setWindowTitle(f"Compress {path.name}")
    dlg.setWindowIcon(QIcon.fromTheme("archive-insert", QIcon.fromTheme("application-x-archive")))
    dlg.setMinimumWidth(600)
    lay = QVBoxLayout(dlg)
    title = QLabel(f"<h3>{path.name}</h3>")
    title.setTextFormat(Qt.TextFormat.RichText)
    intro = QLabel(INTRO)
    intro.setWordWrap(True)
    lay.addWidget(title)
    lay.addWidget(intro)

    rb_folder = QRadioButton()
    rb_file = QRadioButton(f"Only this file: {path.name}")
    choose = QPushButton("Choose another folder…")
    lay.addWidget(rb_folder)
    if is_file:
        lay.addWidget(rb_file)
    row = QHBoxLayout()
    row.addWidget(choose)
    row.addStretch(1)
    lay.addLayout(row)

    info = QLabel("")
    info.setWordWrap(True)
    info.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    info.setMinimumHeight(info.fontMetrics().lineSpacing() * 3)      # room for its three lines
    info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(info)
    strong = QCheckBox("Strongest compression (takes longer now, is not slower to use afterwards)")
    lay.addWidget(strong)
    bar = QProgressBar()
    bar.setVisible(False)
    status = QLabel("")
    status.setWordWrap(True)
    lay.addWidget(bar)
    lay.addWidget(status)

    buttons = QHBoxLayout()
    compress_btn = QPushButton("Compress")
    undo_btn = QPushButton("Undo compression")
    stop_btn = QPushButton("Stop")
    close_btn = QPushButton("Close")
    stop_btn.setVisible(False)
    buttons.addWidget(compress_btn)
    buttons.addWidget(undo_btn)
    buttons.addWidget(stop_btn)
    buttons.addStretch(1)
    buttons.addWidget(close_btn)
    lay.addLayout(buttons)

    bridge = Bridge()
    state = {"folder": folder, "run": 0, "size": (0, 0), "on_disk": None, "busy": False,
             "cancel": threading.Event()}

    def target() -> Path:
        return path if (is_file and rb_file.isChecked()) else state["folder"]

    def fit():
        """Make the window tall enough for its texts (they wrap, and change); never shrinks it."""
        lay.activate()
        width = max(dlg.width(), lay.totalMinimumSize().width())
        height = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        if width > dlg.width() or height > dlg.height():
            dlg.resize(width, max(height, dlg.height()))

    def set_folder(new: Path):
        state["folder"] = new
        rb_folder.setText(f"The whole folder: {cz.short_path(new)}")
        rb_folder.setToolTip(str(new))

    def refresh():
        """Look at the chosen target again: size, what it takes now, what compressing would save."""
        state["run"] += 1
        run, chosen = state["run"], target()
        reason = cz.why_not(chosen)
        compress_btn.setEnabled(not reason and not state["busy"])
        undo_btn.setEnabled(not reason and not state["busy"] and cz.is_compressed(chosen))
        strong.setEnabled(not reason)
        if reason:
            info.setText(reason)
            fit()
            return
        info.setText("Looking at the files…")

        def work():
            size = cz.total_size(chosen)
            on_disk = cz.disk_usage(chosen)
            bridge.scanned.emit(run, size, cz.estimate(chosen), on_disk)
        threading.Thread(target=work, daemon=True).start()

    def scanned(run, size, est, on_disk):
        if run != state["run"]:
            return                              # an answer for an earlier choice
        state["size"], state["on_disk"] = size, on_disk
        lines = [f"Size: {cz.human(size[1])} in {size[0]:,} file{'s' * (size[0] != 1)}"]
        if on_disk:
            lines.append(f"On the disk now: {cz.human(on_disk[0])}")
        if cz.is_compressed(target()):
            lines.append("Already compressed. Compress again to include files that were added since.")
        else:
            lines.append(saving_line(est, on_disk))
        if shared_line(est):
            lines.append(shared_line(est))
        info.setText("\n".join(lines))
        fit()

    def busy(on: bool, text: str = ""):
        state["busy"] = on
        for widget in (compress_btn, undo_btn, choose, rb_folder, rb_file, strong, close_btn):
            widget.setEnabled(not on)
        stop_btn.setVisible(on)
        bar.setVisible(on)
        if on:
            bar.setRange(0, max(1, state["size"][1]))
            bar.setValue(0)
            status.setText(text)
        fit()

    def start(what: str):
        chosen, before = target(), state["on_disk"]
        state["cancel"] = threading.Event()
        busy(True, "Compressing…" if what == "compress" else "Undoing the compression…")

        def work():
            try:
                if what == "compress":
                    outcome = cz.compress(chosen, strong.isChecked(), bridge.progress.emit, state["cancel"])
                else:
                    outcome = cz.decompress(chosen, bridge.progress.emit, state["cancel"])
                bridge.finished.emit(what, before, cz.disk_usage(chosen), outcome)
            except (cz.CompressError, OSError) as e:
                bridge.finished.emit(what, before, None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def progressed(files: int, done: int):
        bar.setValue(min(done, bar.maximum()))
        status.setText(f"{files:,} of {state['size'][0]:,} files…")

    def finished(what, before, after, outcome):
        busy(False)
        if isinstance(outcome, str):
            status.setText(outcome)
        else:
            usual = "Done. " if what == "compress" else "The compression was undone. "
            status.setText(lead(what, outcome, usual) + result_line(before, after, outcome, undo=(what == "undo")))
        fit()
        refresh()

    def pick():
        chosen = QFileDialog.getExistingDirectory(dlg, "Folder to compress", str(state["folder"]))
        if chosen:
            set_folder(Path(chosen))
            rb_folder.setChecked(True)
            refresh()

    bridge.scanned.connect(scanned)
    bridge.progress.connect(progressed)
    bridge.finished.connect(finished)
    rb_folder.toggled.connect(lambda _on: refresh())
    choose.clicked.connect(pick)
    compress_btn.clicked.connect(lambda: start("compress"))
    undo_btn.clicked.connect(lambda: start("undo"))
    stop_btn.clicked.connect(lambda: (state["cancel"].set(), status.setText("Stopping after the current files…")))
    close_btn.clicked.connect(dlg.accept)

    set_folder(folder)
    whole = not is_file or default != path
    rb_folder.blockSignals(True)
    (rb_folder if whole else rb_file).setChecked(True)
    rb_folder.blockSignals(False)
    refresh()
    dlg.adjustSize()                    # the height that fits the wrapped texts at this width
    fit()
    dlg._myos = (bridge, state, refresh, start, target, fit)    # kept alive; also lets tests drive the window
    if getattr(run_gui, "return_dialog", False):
        return dlg
    dlg.exec()
    state["cancel"].set()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="myos-compress", description="Make a program or folder smaller "
                                     "on the disk; it keeps working as before (needs a Btrfs drive).")
    parser.add_argument("path", help="a Windows program (.exe) or a folder")
    parser.add_argument("--text", action="store_true", help="work in the terminal instead of opening a window")
    parser.add_argument("--compress", action="store_true", help="with --text: compress now")
    parser.add_argument("--undo", action="store_true", help="with --text: undo the compression")
    parser.add_argument("--strong", action="store_true", help="strongest compression (slower to compress)")
    parser.add_argument("--file-only", action="store_true", help="only the file itself, not its folder")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.text or args.compress or args.undo:
        return run_text(args)
    path = Path(args.path).expanduser()
    if not path.exists():
        print(f"{path} does not exist.", file=sys.stderr)
        return 2
    return run_gui(path)
