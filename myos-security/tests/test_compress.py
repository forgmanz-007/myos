"""Tests for "Compress to save disk space" (myos-compress). The Btrfs parts are
replaced by a fake `btrfs` program and a remembered set of marks, so the tests
run on any file system; the real thing is tried on a Btrfs drive in the test VM."""
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="myos-compress-")).resolve()
HOME = TMP / "home"
HOME.mkdir()
os.environ["HOME"] = str(HOME)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BIN = TMP / "bin"
BIN.mkdir()
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"
os.environ["FAKE_LOG"] = str(TMP / "calls.log")
os.environ["MYOS_DISK_USAGE_HELPER"] = str(TMP / "disk-usage")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from myos_security import compress as cz  # noqa: E402
from myos_security import compress_dialog as ui  # noqa: E402

LOG = TMP / "calls.log"


def fake(name, body):
    p = BIN / name
    p.write_text("#!/bin/sh\necho \"" + name + " $*\" >> \"$FAKE_LOG\"\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def calls():
    return LOG.read_text().splitlines() if LOG.exists() else []


def game(name="Witcher"):
    """A made-up game folder: a program, text-like data that packs well, and packed data that doesn't."""
    root = HOME / "Games" / name
    (root / "bin").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "bin" / "game.exe").write_bytes(b"MZ" + b"\x00code\x90" * 60000)
    (root / "data" / "strings.txt").write_bytes(b"The quick brown fox jumps over the lazy dog. " * 40000)
    (root / "data" / "textures.pak").write_bytes(os.urandom(1_500_000))
    (root / "readme.txt").write_text("hello\n")
    return root


# A made-up PC: system and home folders on Btrfs, an older games drive on ext4. The mount
# points are real folders under the test folder, so that resolving links changes nothing.
PC = TMP / "pc"
for sub in ("home/u/Games", "boot/efi/EFI", "mnt/Old Games/doom", "homework", "usr/bin"):
    (PC / sub).mkdir(parents=True)
MOUNTINFO = f"""22 1 259:2 / {PC} rw,noatime shared:1 - btrfs /dev/nvme0n1p2 rw,compress=lzo,ssd,discard=async,subvol=/@
35 22 259:2 /@home {PC}/home rw,noatime shared:2 - btrfs /dev/nvme0n1p2 rw,compress=lzo,ssd,subvol=/@home
41 22 259:1 / {PC}/boot/efi rw,relatime shared:3 - vfat /dev/nvme0n1p1 rw,fmask=0077
52 22 8:17 / {PC}/mnt/Old\\040Games rw,relatime shared:4 - ext4 /dev/sdb1 rw
60 35 0:55 / {PC}/home/u/.cache/doc rw,nosuid shared:5 - fuse.portal portal rw
"""


class Where(unittest.TestCase):
    def test_which_drive_a_path_is_on(self):
        info = TMP / "mountinfo"
        info.write_text(MOUNTINFO)
        self.assertEqual(cz.filesystem_of(PC / "home/u/Games/x.exe", info), ("btrfs", f"{PC}/home"))
        self.assertEqual(cz.filesystem_of(PC / "usr/bin/ls", info), ("btrfs", str(PC)))
        self.assertEqual(cz.filesystem_of(PC / "mnt/Old Games/doom/doom.exe", info), ("ext4", f"{PC}/mnt/Old Games"))
        self.assertEqual(cz.filesystem_of(PC / "boot/efi/EFI", info), ("vfat", f"{PC}/boot/efi"))
        self.assertEqual(cz.filesystem_of(PC / "homework/x", info), ("btrfs", str(PC)))      # not under home
        self.assertEqual(cz.filesystem_of(PC, info), ("btrfs", str(PC)))
        self.assertEqual(cz.filesystem_of("/x", TMP / "missing"), ("", ""))

    def test_what_compress_this_program_covers(self):
        root = game()
        self.assertEqual(cz.default_target(root / "bin" / "game.exe"), root)           # the game, not just bin/
        (root / "launcher.exe").write_bytes(b"MZ")
        self.assertEqual(cz.default_target(root / "launcher.exe"), root)               # its own folder
        unreal = HOME / "Games" / "Big Game" / "BigGame" / "Binaries" / "Win64"
        unreal.mkdir(parents=True)
        (unreal / "BigGame-Win64-Shipping.exe").write_bytes(b"MZ")
        self.assertEqual(cz.default_target(unreal / "BigGame-Win64-Shipping.exe"), unreal.parent.parent)
        for stays in (HOME / "bin", HOME / "Downloads" / "x64"):                      # never a shared folder
            stays.mkdir(parents=True, exist_ok=True)
            (stays / "tool.exe").write_bytes(b"MZ")
            self.assertEqual(cz.default_target(stays / "tool.exe"), stays)
        for shared in ("Downloads", "Desktop", "Documents"):
            (HOME / shared).mkdir(exist_ok=True)
            exe = HOME / shared / "setup.exe"
            exe.write_bytes(b"MZ")
            self.assertEqual(cz.default_target(exe), exe)                             # only the file
        (HOME / "tool.exe").write_bytes(b"MZ")
        self.assertEqual(cz.default_target(HOME / "tool.exe"), HOME / "tool.exe")       # home folder
        drive = HOME / ".wine" / "drive_c" / "Program Files" / "App"
        drive.mkdir(parents=True)
        (drive / "app.exe").write_bytes(b"MZ")
        self.assertEqual(cz.default_target(drive / "app.exe"), drive)
        self.assertTrue(cz.is_shared_folder(drive.parent))                             # "Program Files"
        self.assertTrue(cz.is_shared_folder("/"))

    def test_paths_for_labels(self):
        self.assertEqual(cz.short_path(HOME / "Games" / "Doom"), "~/Games/Doom")
        self.assertEqual(cz.short_path(HOME), "~")
        self.assertEqual(cz.short_path("/mnt/games/Doom"), "/mnt/games/Doom")
        self.assertFalse(cz.short_path(str(HOME) + "work/x", limit=999).startswith("~"))   # not inside home
        long = HOME / ".local/share/Steam/steamapps/common" / ("Very Long Game Name " * 4).strip() / "Binaries"
        short = cz.short_path(long)
        self.assertEqual(len(short), 70)
        self.assertTrue(short.startswith("~/.local/share/Steam") and short.endswith("/Binaries") and "…" in short)

    def test_files_and_sizes(self):
        root = game("Sizes")
        os.symlink(root / "readme.txt", root / "link.txt")
        files, size = cz.total_size(root)
        self.assertEqual(files, 4)                                                    # links are not counted
        self.assertEqual(size, sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()))
        self.assertEqual(cz.total_size(root / "readme.txt"), (1, 6))
        self.assertEqual(sorted(Path(f).name for f in cz.folders_of(root)), ["Sizes", "bin", "data"])
        self.assertEqual([cz.human(n) for n in (512, 4096, 5 * 1024 ** 2, 48.2 * 1024 ** 3)],
                         ["512 bytes", "4.0 KB", "5.0 MB", "48.2 GB"])

    def test_why_a_drive_cannot_compress(self):
        old = cz.filesystem_of
        cz.filesystem_of = lambda path, mountinfo="": ("ext4", "/")
        try:
            self.assertIn("uses the ext4 file system", cz.why_not("/x"))
            self.assertFalse(cz.supported("/x"))
            cz.filesystem_of = lambda path, mountinfo="": ("btrfs", "/")
            self.assertEqual(cz.why_not("/x"), "")
        finally:
            cz.filesystem_of = old


class Estimating(unittest.TestCase):
    def test_estimate_tells_packed_data_from_data_that_packs(self):
        root = game("Estimate")
        text = cz.estimate(root / "data" / "strings.txt")
        self.assertEqual(text.size, 1_800_000)
        self.assertLess(text.after, text.size * 0.1)                 # repeats: shrinks to a few percent
        self.assertGreater(text.percent, 90)
        packed = cz.estimate(root / "data" / "textures.pak")
        self.assertEqual((packed.after, packed.saved, packed.percent), (1_500_000, 0, 0))    # nothing to gain
        whole = cz.estimate(root)
        self.assertEqual(whole.files, 4)
        self.assertTrue(1_500_000 < whole.after < whole.size * 0.6, (whole.after, whole.size))
        self.assertEqual(cz.estimate(HOME / "no-such-folder").size, 0)

    def test_small_pieces_count_in_whole_disk_units(self):
        self.assertEqual(cz._stored_size(b"a" * 100), 4096)          # can't be less than one unit
        self.assertEqual(cz._stored_size(b"a" * 131072), 4096)       # 128 KB of the same letter: one unit
        self.assertEqual(cz._stored_size(os.urandom(131072)), 131072)   # random: stays as it is
        self.assertEqual(cz._stored_size(os.urandom(5000)), 8192)

    def test_estimate_can_be_stopped(self):
        stop = threading.Event()
        stop.set()
        self.assertEqual(cz.estimate(game("Stop"), cancel=stop).sampled, 0)

    def test_reading_where_the_data_lies(self):
        self.assertEqual((cz._FIEMAP_HEAD.size, cz._FIEMAP_EXTENT.size), (32, 56))     # as the kernel lays them out
        S, E, L = cz.FIEMAP_EXTENT_SHARED, cz.FIEMAP_EXTENT_ENCODED, cz.FIEMAP_EXTENT_LAST
        answer = (cz._FIEMAP_HEAD.pack(0, 2 ** 64 - 1, 0, 2, 128, 0)
                  + cz._FIEMAP_EXTENT.pack(0, 1 << 30, 65536, 0, 0, S)
                  + cz._FIEMAP_EXTENT.pack(65536, 2 << 30, 4096, 0, 0, E | L)
                  + bytes(56 * 126))
        found, last = cz._parse_fiemap(answer)
        self.assertEqual(found, [(0, 65536, S), (65536, 4096, E | L)])
        self.assertTrue(last)
        self.assertEqual(cz._parse_fiemap(cz._FIEMAP_HEAD.pack(0, 0, 0, 0, 128, 0)), ([], True))   # no data at all
        old = cz.extents
        try:
            cz.extents = lambda path: found
            self.assertEqual(cz.shared_bytes("x"), 65536)
            self.assertTrue(cz.stored_compressed("x"))
            cz.extents = lambda path: [(0, 4096, L)]
            self.assertEqual(cz.shared_bytes("x"), 0)
            self.assertFalse(cz.stored_compressed("x"))
            cz.extents = lambda path: None                          # a file system that can't tell
            self.assertEqual(cz.shared_bytes("x"), 0)
            self.assertIsNone(cz.stored_compressed("x"))
        finally:
            cz.extents = old
        self.assertIsNone(cz.extents(HOME / "no-such-file"))
        real = cz.extents(game("Fiemap") / "readme.txt")                # a real answer, where there is one
        self.assertTrue(real is None or isinstance(real, list), real)

    def test_files_shared_with_a_copy_or_a_snapshot_count_as_they_are(self):
        root = game("SharedEstimate")
        S, L = cz.FIEMAP_EXTENT_SHARED, cz.FIEMAP_EXTENT_LAST
        old = cz.extents
        cz.extents = lambda path: [(0, 9, S | L)] if str(path).endswith("strings.txt") else [(0, 9, L)]
        try:
            est = cz.estimate(root)
        finally:
            cz.extents = old
        self.assertEqual((est.files, est.shared, est.shared_files), (4, 1_800_000, 1))
        self.assertGreaterEqual(est.after, 1_800_000 + 1_500_000)      # left as it is, plus the packed data
        self.assertEqual(ui.shared_line(est), "1.7 MB in 1 file will be left as it is: it shares its data "
                                              "with a copy or a snapshot, and compressing would store it twice.")
        self.assertEqual(ui.shared_line(cz.Estimate(1, 10, 5)), "")
        mostly = cz.Estimate(files=2, size=10 << 20, after=10 << 20, shared=9 << 20, shared_files=1)
        self.assertIn("most of these files share their data with a copy or a snapshot", ui.saving_line(mostly, None))
        # seen on the test PC: the disk figure (549 MB) is above the files' size (537 MB) because of how
        # it counts; that difference must not show up as a saving when the files are shared
        copy = cz.Estimate(files=11131, size=536_500_000, after=536_500_000, shared=532_700_000, shared_files=7393)
        self.assertIn("would save almost nothing", ui.saving_line(copy, (549_200_000, 536_500_000)))
        part = cz.Estimate(files=3, size=30 << 20, after=20 << 20, shared=10 << 20, shared_files=1)
        self.assertEqual(ui.saving_line(part, (31 << 20, 30 << 20)), "After compressing: about 21.0 MB (saves about 10.0 MB, 32%)")

    def test_tiny_files_count_as_they_are(self):
        root = game("TinyEstimate")
        I, L = cz.FIEMAP_EXTENT_DATA_INLINE, cz.FIEMAP_EXTENT_LAST
        old = cz.extents
        cz.extents = lambda path: [(0, 6, I | L)] if str(path).endswith("strings.txt") else [(0, 9, L)]
        try:
            est = cz.estimate(root)
        finally:
            cz.extents = old
        self.assertEqual(est.small, 1_800_000)
        self.assertGreaterEqual(est.after, 1_800_000 + 1_500_000)

    def test_what_the_user_reads(self):
        est = cz.Estimate(files=10, size=48 * 1024 ** 3, after=34 * 1024 ** 3)
        self.assertEqual(ui.saving_line(est, None), "After compressing: about 34.0 GB (saves about 14.0 GB, 29%)")
        # the drive already compresses a little by itself (41 GB on disk now): only the extra counts
        self.assertEqual(ui.saving_line(est, (41 * 1024 ** 3, 48 * 1024 ** 3)),
                         "After compressing: about 34.0 GB (saves about 7.0 GB, 17%)")
        self.assertIn("almost nothing", ui.saving_line(cz.Estimate(1, 1000, 1000), None))
        done = cz.Outcome(files=3)
        self.assertEqual(ui.result_line((48 << 30, 48 << 30), (34 << 30, 48 << 30), done),
                         "It now takes 34.0 GB on the disk instead of 48.0 GB (14.0 GB saved).")
        self.assertIn("about the same as before", ui.result_line((5 << 20, 5 << 20), (5 << 20, 5 << 20), done))
        self.assertEqual(ui.result_line((34 << 30, 48 << 30), (41 << 30, 48 << 30), done, undo=True),
                         "It now takes 41.0 GB on the disk (it was 34.0 GB).")
        done.failed = ["/a/b.exe"]
        done.note = "A note."
        line = ui.result_line(None, None, done)
        self.assertIn("3 files were handled.", line)
        self.assertIn("1 file could not be changed", line)
        self.assertTrue(line.endswith("A note."))


class Compressing(unittest.TestCase):
    def setUp(self):
        LOG.unlink(missing_ok=True)
        self.marks = {}
        self._old = (cz.why_not, cz.set_property, cz.get_property, cz.extents)
        cz.extents = lambda path: None          # what a file system that can't tell answers
        cz.why_not = lambda target: ""
        cz.set_property = lambda path, value: self.marks.__setitem__(str(path), value) or True
        cz.get_property = lambda path: self.marks.get(str(path), "")
        fake("btrfs", 'case "$*" in *defragment*-L*) [ -n "$FAKE_NO_LEVEL" ] && { echo "ERROR: compression level not supported" >&2; exit 1; } ;; esac\n'
                      'case "$*" in *defragment*) for f; do case "$f" in *locked*) echo "ERROR: cannot open $f: Text file busy" >&2; bad=1 ;; esac; done ;; esac\n'
                      'exit ${bad:-0}\n')
        for tool in ("ionice", "nice"):
            fake(tool, 'while [ $# -gt 0 ]; do case "$1" in -c|-n) shift 2 ;; *) break ;; esac; done\nexec "$@"\n')
        os.environ.pop("FAKE_NO_LEVEL", None)

    def tearDown(self):
        cz.why_not, cz.set_property, cz.get_property, cz.extents = self._old

    def defrag_calls(self):
        return [c for c in calls() if c.startswith("btrfs filesystem defragment")]

    def test_compress_a_folder(self):
        root = game("Compress")
        seen = []
        outcome = cz.compress(root, progress=lambda files, size: seen.append((files, size)))
        self.assertEqual((outcome.files, outcome.failed, outcome.note, outcome.cancelled), (4, [], "", False))
        run = self.defrag_calls()
        self.assertEqual(len(run), 1)
        self.assertTrue(run[0].startswith("btrfs filesystem defragment -f -czstd -- "), run[0])     # -f: see _defragment
        for name in ("game.exe", "strings.txt", "textures.pak", "readme.txt"):
            self.assertIn(name, run[0])
        # folders are marked, so files added later (updates) are compressed too; files as well
        for folder in (root, root / "bin", root / "data"):
            self.assertEqual(self.marks[str(folder)], "zstd")
        self.assertEqual(self.marks[str(root / "bin" / "game.exe")], "zstd")
        self.assertTrue(cz.is_compressed(root))
        self.assertEqual(seen[-1], (4, cz.total_size(root)[1]))
        self.assertIn("btrfs filesystem sync " + str(root), calls())
        log = calls()
        self.assertLess(log.index("btrfs filesystem sync " + str(root)),                # pending data written first
                        next(i for i, c in enumerate(log) if c.startswith("btrfs filesystem defragment")))
        self.assertTrue(any(c.startswith("ionice -c 3 nice -n 19 btrfs") for c in calls()))     # in the background

    def test_files_are_read_just_before_they_are_rewritten(self):
        root = game("ReadFirst")
        order, old = [], cz._read_through
        cz._read_through = lambda batch: order.append(("read", sorted(batch)))
        real_defrag = cz._defragment

        def defrag(batch, level):
            order.append(("rewrite", sorted(batch)))
            return real_defrag(batch, level)
        cz._defragment = defrag
        try:
            cz.compress(root)
        finally:
            cz._read_through, cz._defragment = old, real_defrag
        self.assertEqual([what for what, _ in order], ["read", "rewrite"])
        self.assertEqual(order[0][1], order[1][1])                      # the same files
        cz._read_through([str(root / "data" / "strings.txt"), str(HOME / "gone.bin")])   # a missing file is no error

    def test_parts_btrfs_skipped_are_done_again(self):
        root = game("Skipped")
        strings, pak = str(root / "data" / "strings.txt"), str(root / "data" / "textures.pak")
        # Both files have a big part stored uncompressed; strings.txt would pack, the .pak is random.
        cz.extents = lambda path: [(0, 1_800_000, cz.FIEMAP_EXTENT_LAST)] if path in (strings, pak) else None
        self.assertTrue(cz.skipped_by_btrfs(strings))
        self.assertFalse(cz.skipped_by_btrfs(pak))                       # tried, doesn't pack: fine as it is
        rewritten, flushed, old = [], [], (cz.rewrite, cz._flush)
        cz.rewrite, cz._flush = rewritten.append, flushed.append
        try:
            outcome = cz.compress(root)
        finally:
            cz.rewrite, cz._flush = old
        self.assertEqual(rewritten, [strings])
        self.assertEqual(flushed, [strings])                              # on the disk before trying again
        run = self.defrag_calls()
        self.assertEqual(len(run), 2)
        self.assertTrue(run[1].endswith("-- " + strings), run[1])       # only that file, once more
        self.assertEqual((outcome.repaired, outcome.failed), (1, []))
        cz.extents = lambda path: [(0, 9, cz.FIEMAP_EXTENT_ENCODED | cz.FIEMAP_EXTENT_LAST)]
        self.assertFalse(cz.skipped_by_btrfs(strings))                    # all compressed
        cz.extents = lambda path: None
        self.assertFalse(cz.skipped_by_btrfs(strings))                    # can't tell: leave it

    def test_only_one_file(self):
        exe = game("OneFile") / "bin" / "game.exe"
        outcome = cz.compress(exe)
        self.assertEqual(outcome.files, 1)
        self.assertEqual(list(self.marks), [str(exe)])                 # the folder is left alone
        self.assertTrue(self.defrag_calls()[0].endswith("-- " + str(exe)))

    def test_strongest_setting_and_kernels_that_lack_it(self):
        root = game("Strong")
        cz.compress(root, strong=True)
        self.assertIn("-czstd -L 9 --", self.defrag_calls()[0])
        LOG.unlink()
        os.environ["FAKE_NO_LEVEL"] = "1"
        outcome = cz.compress(root, strong=True)
        run = self.defrag_calls()
        self.assertEqual(len(run), 2)                                   # tried, then normal strength
        self.assertNotIn("-L", run[1])
        self.assertIn("newer Linux kernel", outcome.note)
        self.assertEqual(outcome.failed, [])

    def test_files_shared_with_a_copy_or_a_snapshot_are_left_alone(self):
        root = game("LeaveShared")
        S, L = cz.FIEMAP_EXTENT_SHARED, cz.FIEMAP_EXTENT_LAST
        cz.extents = lambda path: [(0, 9, S | L)] if path.endswith("strings.txt") else [(0, 9, L)]
        outcome = cz.compress(root)
        run = self.defrag_calls()
        self.assertEqual(len(run), 1)
        self.assertNotIn("strings.txt", run[0])                          # writing it anew would store it twice
        self.assertIn("game.exe", run[0])
        self.assertEqual((outcome.files, outcome.left, outcome.left_size, outcome.failed, outcome.changed),
                         (4, 1, 1_800_000, [], 3))
        self.assertNotIn(str(root / "data" / "strings.txt"), self.marks)
        self.assertEqual(self.marks[str(root / "data")], "zstd")         # new files there are still compressed
        line = ui.result_line((3 << 20, 3 << 20), (3 << 20, 3 << 20), outcome)
        self.assertIn("1 file (1.7 MB) was left as it was: it shares its data with a copy or a snapshot, "
                      "and compressing would store it twice. Once the copy or the snapshot is deleted, "
                      "run this again.", line)
        self.assertNotIn("as small as they get", line)
        LOG.unlink()
        self.marks.clear()
        cz.extents = lambda path: [(0, 9, S | L)]                        # everything shared: nothing to do
        outcome = cz.compress(root)
        self.assertEqual(self.defrag_calls(), [])
        self.assertEqual((outcome.files, outcome.left, outcome.changed), (4, 4, 0))
        self.assertEqual(self.marks, {})                                 # not even a mark: nothing changes
        self.assertEqual(ui.lead("compress", outcome, "Done. "), "Nothing was changed. ")
        self.assertEqual(ui.lead("compress", cz.Outcome(files=4, changed=4), "Done. "), "Done. ")

    def test_tiny_and_empty_files_are_not_touched(self):
        root = game("Tiny")
        (root / "empty.cfg").write_bytes(b"")
        I, L = cz.FIEMAP_EXTENT_DATA_INLINE, cz.FIEMAP_EXTENT_LAST
        cz.extents = lambda path: [(0, 6, I | L)] if path.endswith("readme.txt") else [(0, 9, L)]
        seen = []
        outcome = cz.compress(root, progress=lambda files, size: seen.append((files, size)))
        run = self.defrag_calls()
        self.assertNotIn("readme.txt", run[0])                           # kept inside the file system's index
        self.assertNotIn("empty.cfg", run[0])
        self.assertIn("strings.txt", run[0])
        self.assertEqual((outcome.files, outcome.left), (5, 0))          # handled, nothing left out for sharing
        self.assertEqual(seen[-1], (5, cz.total_size(root)[1]))          # the bar still reaches the end
        self.assertEqual(cz.kind_of(str(root / "empty.cfg"), 0), "small")
        cz.extents = lambda path: []
        self.assertEqual(cz.kind_of("x", 10), "small")                   # no data on the disk at all
        cz.extents = lambda path: None
        self.assertEqual(cz.kind_of("x", 10), "work")                    # can't tell: compress it

    def test_undo_only_unpacks_what_is_packed_and_not_shared(self):
        root = game("UndoSkip")
        S, E, L = cz.FIEMAP_EXTENT_SHARED, cz.FIEMAP_EXTENT_ENCODED, cz.FIEMAP_EXTENT_LAST

        def where(path):
            if path.endswith("readme.txt"):
                return [(0, 6, L)]                                       # stored plainly: nothing to undo
            if path.endswith("strings.txt"):
                return [(0, 9, E | S | L)]                               # packed, and kept in a snapshot too
            return [(0, 9, E | L)]
        cz.extents = where
        rewritten, old = [], cz.rewrite
        cz.rewrite = rewritten.append
        try:
            outcome = cz.decompress(root)
        finally:
            cz.rewrite = old
        self.assertEqual(sorted(rewritten), sorted([str(root / "bin" / "game.exe"), str(root / "data" / "textures.pak")]))
        self.assertEqual((outcome.files, outcome.left, outcome.failed), (4, 1, []))
        self.assertIn("unpacking would store it twice", ui.result_line(None, None, outcome, undo=True))
        self.assertEqual(self.marks[str(root / "data" / "strings.txt")], "")    # the mark goes in any case

    def test_files_in_use_are_reported(self):
        root = game("InUse")
        (root / "bin" / "locked.dll").write_bytes(b"x" * 5000)
        outcome = cz.compress(root)
        self.assertEqual(outcome.failed, [str(root / "bin" / "locked.dll")])
        self.assertEqual(outcome.files, 5)

    def test_many_files_go_in_batches_and_can_be_stopped(self):
        root = HOME / "Games" / "Many"
        root.mkdir(parents=True)
        for n in range(150):
            (root / f"f{n:03d}.dat").write_bytes(b"x" * 10)
        stop = threading.Event()
        seen = []

        def progress(files, _size):
            seen.append(files)
            if files >= 128:
                stop.set()
        outcome = cz.compress(root, progress=progress, cancel=stop)
        self.assertEqual(seen, [64, 128])
        self.assertTrue(outcome.cancelled)
        self.assertEqual(len(self.defrag_calls()), 2)

    def test_undo_stores_the_files_the_normal_way_again(self):
        root = game("Undo")
        cz.compress(root)
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        old = time.time() - 86400
        os.utime(root / "data" / "strings.txt", (old, old))
        (root / "bin" / "game.exe").chmod(0o444)                         # can't be written: reported
        outcome = cz.decompress(root)
        (root / "bin" / "game.exe").chmod(0o644)
        self.assertEqual(outcome.files, 4)
        self.assertEqual(outcome.failed, [str(root / "bin" / "game.exe")])
        for p, data in before.items():
            self.assertEqual(p.read_bytes(), data, p)                    # not a byte changed
        self.assertAlmostEqual((root / "data" / "strings.txt").stat().st_mtime, old, delta=2)
        self.assertEqual(self.marks[str(root)], "")
        self.assertEqual(self.marks[str(root / "data" / "strings.txt")], "")
        self.assertFalse(cz.is_compressed(root))

    def test_rewrite_keeps_holes_and_content(self):
        sparse = HOME / "sparse.bin"
        with open(sparse, "wb") as f:
            f.write(b"start")
            f.seek(8 * 1024 * 1024)
            f.write(b"end")
        blocks = sparse.stat().st_blocks
        data = sparse.read_bytes()
        cz.rewrite(sparse)
        self.assertEqual(sparse.read_bytes(), data)
        self.assertLessEqual(sparse.stat().st_blocks, max(blocks, 64))  # the hole was not filled in
        empty = HOME / "empty.bin"
        empty.write_bytes(b"")
        cz.rewrite(empty)
        self.assertEqual(empty.read_bytes(), b"")

    def test_refuses_where_it_cannot_work(self):
        cz.why_not = lambda target: "This drive uses the ext4 file system."
        with self.assertRaises(cz.CompressError):
            cz.compress(game("Refuse"))
        with self.assertRaises(cz.CompressError):
            cz.decompress(HOME / "Games" / "Refuse")
        self.assertEqual(self.defrag_calls(), [])


class Measuring(unittest.TestCase):
    OUTPUT = ("Processed 5321 files, 80123 regular extents (80123 refs), 12 inline.\n"
              "Type       Perc     Disk Usage   Uncompressed Referenced  \n"
              "TOTAL       70%  36614195200  51753517056  51753517056\n"
              "none       100%  20000000000  20000000000  20000000000\n"
              "zstd        52%  16614195200  31753517056  31753517056\n")

    def test_reading_compsize(self):
        self.assertEqual(cz.parse_compsize(self.OUTPUT), (36614195200, 51753517056))
        self.assertEqual(cz.parse_compsize("Processed 0 files.\n"), (0, 0))
        self.assertIsNone(cz.parse_compsize("ERROR: ioctl failed\n"))

    def test_asking_the_helper(self):
        helper = Path(os.environ["MYOS_DISK_USAGE_HELPER"])
        helper.unlink(missing_ok=True)
        self.assertIsNone(cz.disk_usage("/x"))                          # no helper installed
        helper.write_text("#!/bin/sh\nexit 0\n")
        fake("pkexec", f'shift\ncat <<OUT\n{self.OUTPUT}OUT\n')
        self.assertEqual(cz.disk_usage("/home/u/Games"), (36614195200, 51753517056))
        self.assertTrue(any(c == f"pkexec {helper} /home/u/Games" for c in calls()))
        fake("pkexec", 'echo "Not authorized" >&2\nexit 126\n')
        self.assertIsNone(cz.disk_usage("/home/u/Games"))
        (BIN / "pkexec").unlink()

    def test_helper_only_answers_for_your_own_files(self):
        helper = Path(__file__).resolve().parents[1] / "libexec" / "disk-usage"
        import subprocess

        def run(*args, uid=None):
            env = {k: v for k, v in os.environ.items() if k != "PKEXEC_UID"}
            if uid is not None:
                env["PKEXEC_UID"] = str(uid)
            return subprocess.run(["sh", str(helper), *args], capture_output=True, text=True, env=env)
        self.assertEqual(run().returncode, 2)
        self.assertEqual(run(str(HOME)).returncode, 2)                  # not started through pkexec
        self.assertEqual(run(str(HOME / "missing"), uid=os.getuid()).returncode, 2)
        if os.path.exists("/usr/bin/stat") and sys.platform.startswith("linux"):
            self.assertEqual(run(str(HOME), uid=os.getuid() + 1).returncode, 3)     # someone else's folder
            self.assertIn(run(str(HOME), uid=os.getuid()).returncode, (0, 1, 4))    # allowed (compsize may be missing)


class TextMode(unittest.TestCase):
    def run_text(self, *args):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ui.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_drive_that_cannot_compress(self):
        old = cz.why_not
        cz.why_not = lambda target: "This drive uses the ext4 file system. Compressing programs in place needs Btrfs."
        try:
            exe = game("Text") / "bin" / "game.exe"
            code, out, _ = self.run_text("--text", str(exe))
            self.assertEqual(code, 0)
            self.assertIn(f"Target: the folder {exe.parent.parent} (4 files, ", out)      # the game, not just bin/
            self.assertIn("needs Btrfs", out)
            self.assertEqual(self.run_text("--compress", str(exe))[0], 1)
        finally:
            cz.why_not = old
        self.assertEqual(self.run_text("--text", str(HOME / "nope.exe"))[0], 2)

    def test_status_compress_and_undo(self):
        marks = {}
        old = (cz.why_not, cz.set_property, cz.get_property, cz.disk_usage)
        cz.why_not = lambda target: ""
        cz.set_property = lambda path, value: marks.__setitem__(str(path), value) or True
        cz.get_property = lambda path: marks.get(str(path), "")
        sizes = iter([(3_000_000, 3_300_000),                              # looking
                      (3_000_000, 3_300_000), (1_700_000, 3_300_000),      # compressing: before, after
                      (1_700_000, 3_300_000), (3_300_000, 3_300_000)])     # undoing: before, after
        cz.disk_usage = lambda target: next(sizes)
        fake("btrfs", "exit 0\n")
        try:
            root = game("TextRun")
            code, out, _ = self.run_text("--text", str(root))
            self.assertEqual(code, 0)
            self.assertIn("On the disk now: 2.9 MB", out)
            self.assertIn("Marked for compression: no", out)
            self.assertIn("After compressing: about", out)
            code, out, _ = self.run_text("--compress", "--strong", str(root))
            self.assertEqual(code, 0)
            self.assertIn("Compressed. It now takes 1.6 MB on the disk instead of 2.9 MB (1.2 MB saved).", out)
            code, out, _ = self.run_text("--undo", str(root))
            self.assertEqual(code, 0)
            self.assertIn("Compression undone.", out)
            self.assertEqual(marks[str(root)], "")
        finally:
            cz.why_not, cz.set_property, cz.get_property, cz.disk_usage = old


class Starting(unittest.TestCase):
    def test_a_file_in_the_current_folder_cannot_break_the_apps(self):
        # Seen in the test VM: a bisect.py in the home folder stopped the .exe launcher, because
        # "python3 -m" looks in the current folder first. -P makes Python skip that folder.
        repo = Path(__file__).resolve().parents[2]
        # (files only: running the tests can leave a __pycache__ folder in a bin folder)
        wrappers = [p for p in repo.glob("*/bin/*") if p.is_file() and "python3" in p.read_text(errors="replace")]
        self.assertGreaterEqual(len(wrappers), 7)
        for p in wrappers:
            for line in p.read_text().splitlines():
                if line.startswith("exec python3"):
                    self.assertIn("python3 -P -m ", line, p)


try:
    from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QRadioButton
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "needs PyQt6")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def wait(self, cond, seconds=10):
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def open(self, path):
        ui.run_gui.return_dialog = True
        try:
            dlg = ui.run_gui(Path(path))
        finally:
            ui.run_gui.return_dialog = False
        self.addCleanup(dlg.deleteLater)
        return dlg

    def texts(self, dlg):
        return "\n".join(w.text() for w in dlg.findChildren(QLabel))

    def button(self, dlg, text):
        return next(b for b in dlg.findChildren(QPushButton) if b.text() == text)

    def test_drive_without_compression_explains_itself(self):
        old = cz.why_not
        cz.why_not = lambda target: "This drive uses the ext4 file system. Compressing programs in place needs Btrfs."
        try:
            dlg = self.open(game("WinNo") / "bin" / "game.exe")
            self.assertIn("needs Btrfs", self.texts(dlg))
            self.assertFalse(self.button(dlg, "Compress").isEnabled())
            self.assertFalse(self.button(dlg, "Undo compression").isEnabled())
        finally:
            cz.why_not = old

    def test_compress_from_the_window(self):
        marks = {}
        old = (cz.why_not, cz.set_property, cz.get_property, cz.disk_usage)
        cz.why_not = lambda target: ""
        cz.set_property = lambda path, value: marks.__setitem__(str(path), value) or True
        cz.get_property = lambda path: marks.get(str(path), "")
        measured = {"n": 0}

        def usage(target):
            measured["n"] += 1
            return (1_700_000, 3_300_000) if marks.get(str(target)) == "zstd" else (3_300_000, 3_300_000)
        cz.disk_usage = usage
        fake("btrfs", "exit 0\n")
        try:
            exe = game("WinYes") / "bin" / "game.exe"
            dlg = self.open(exe)
            self.assertTrue(self.wait(lambda: "After compressing" in self.texts(dlg) or "almost nothing" in self.texts(dlg)))
            self.assertIn(f"The whole folder: {cz.short_path(exe.parent.parent)}",
                          [b.text() for b in dlg.findChildren(QRadioButton)])
            self.assertIn("Only this file: game.exe", [b.text() for b in dlg.findChildren(QRadioButton)])
            self.assertIn("Size: ", self.texts(dlg))
            self.assertFalse(self.button(dlg, "Undo compression").isEnabled())
            self.button(dlg, "Compress").click()
            self.assertTrue(self.wait(lambda: "Done." in self.texts(dlg)), self.texts(dlg))
            self.assertIn("It now takes 1.6 MB on the disk instead of 3.1 MB", self.texts(dlg))
            self.assertEqual(marks[str(exe.parent.parent)], "zstd")           # the game's folder, not bin/
            self.assertEqual(marks[str(exe.parent)], "zstd")
            self.assertTrue(self.wait(lambda: self.button(dlg, "Undo compression").isEnabled()))
            self.assertTrue(self.wait(lambda: "Already compressed" in self.texts(dlg)))
            self.button(dlg, "Undo compression").click()
            self.assertTrue(self.wait(lambda: "The compression was undone." in self.texts(dlg)), self.texts(dlg))
            self.assertEqual(marks[str(exe.parent.parent)], "")
        finally:
            cz.why_not, cz.set_property, cz.get_property, cz.disk_usage = old

    def clipped(self, dlg):
        """Labels whose text needs more height than they got."""
        dlg.show()
        for _ in range(5):
            self.app.processEvents()
        return [w.text()[:40] for w in dlg.findChildren(QLabel)
                if w.isVisible() and w.text() and w.height() < w.heightForWidth(w.width())]

    def test_no_text_is_cut_off(self):
        old = (cz.why_not, cz.get_property, cz.disk_usage)
        cz.why_not = lambda target: ""
        cz.get_property = lambda path: ""
        cz.disk_usage = lambda target: (3_300_000, 3_300_000)
        try:
            deep = game("A Game With A Really Long Name, Deluxe Anniversary Edition (Remastered)") / "bin" / "game.exe"
            dlg = self.open(deep)
            self.assertEqual(self.clipped(dlg), [])
            self.assertTrue(self.wait(lambda: "On the disk now" in self.texts(dlg)), self.texts(dlg))
            self.assertEqual(self.clipped(dlg), [])                     # after the three info lines arrived
            radio = next(b for b in dlg.findChildren(QRadioButton) if b.text().startswith("The whole folder"))
            self.assertEqual(radio.toolTip(), str(deep.parent.parent))
            self.assertLessEqual(len(radio.text()), len("The whole folder: ") + 70)
            cz.why_not = lambda target: ("This drive uses the ext4 file system. Compressing programs in place "
                                         "needs Btrfs, which this system uses when it is installed with the "
                                         "default settings. " * 3)
            dlg._myos[2]()                                              # look again: now a long explanation
            self.assertEqual(self.clipped(dlg), [])
        finally:
            cz.why_not, cz.get_property, cz.disk_usage = old


if __name__ == "__main__":
    unittest.main()
