"""Shrinking a Windows program on the disk without changing how it works.

The Btrfs file system can keep files compressed and unpack them while they are
read. That is what "Game Compressor" and CompactGUI do on Windows with NTFS:
the files take less room, every program still sees them at full size. Here it
is done with zstd, and the folder is marked so that files added later (game
updates) are compressed as well.
"""
import errno
import fcntl
import os
import shutil
import struct
import subprocess
import zlib
from dataclasses import dataclass, field
from pathlib import Path

PROPERTY = "btrfs.compression"      # extended attribute Btrfs reads the algorithm from
ALGORITHM = "zstd"
STRONG_LEVEL = 9
BLOCK = 128 * 1024                  # Btrfs compresses data in pieces of this size
PAGE = 4096                         # ... and stores the result in units of this size
HELPER = os.environ.get("MYOS_DISK_USAGE_HELPER", "/usr/libexec/myos-security/disk-usage")
# Folders that hold many different things: there, only the program file itself is offered.
SHARED_NAMES = {"desktop", "downloads", "documents", "music", "pictures", "videos", "public",
                "templates", "drive_c", "program files", "program files (x86)", "windows",
                "system32", "syswow64", "users", "tmp", "home"}
# Folders that only hold a game's program files; the game itself is the folder above.
PROGRAM_SUBFOLDERS = {"bin", "bin32", "bin64", "binaries", "win32", "win64", "x86", "x64",
                      "x86_64", "amd64", "retail"}


class CompressError(Exception):
    pass


# ── where is it, and can it be compressed there? ─────────────────────────────
def filesystem_of(path, mountinfo: str = "/proc/self/mountinfo") -> tuple:
    """(file system type, mount point) of the drive a path is on; ('', '') if unknown."""
    target = os.path.realpath(str(path))
    best = ("", "")
    try:
        with open(mountinfo, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return best
    for line in lines:
        left, sep, right = line.partition(" - ")
        fields, after = left.split(), right.split()
        if not sep or len(fields) < 5 or not after:
            continue
        mount = fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
        inside = target == mount or target.startswith(mount.rstrip("/") + "/")
        if inside and len(mount) >= len(best[1]):
            best = (after[0], mount)
    return best


def supported(path) -> bool:
    return filesystem_of(path)[0] == "btrfs"


def why_not(path) -> str:
    """Why this drive can't compress files, in plain words ('' if it can)."""
    fstype = filesystem_of(path)[0]
    if fstype == "btrfs":
        return ""
    if not shutil.which("btrfs"):
        return "The Btrfs tools are missing (package btrfs-progs)."
    kind = f"the {fstype} file system" if fstype else "a file system that could not be identified"
    return (f"This drive uses {kind}. Compressing programs in place needs Btrfs, which this "
            "system uses when it is installed with the default settings.")


def is_shared_folder(folder) -> bool:
    """A folder that holds many unrelated things (Downloads, the home folder, a whole drive)."""
    folder = Path(os.path.realpath(str(folder)))
    if folder == Path.home() or folder.parent == folder or folder.name.lower() in SHARED_NAMES:
        return True
    return os.path.ismount(str(folder))


def default_target(exe) -> Path:
    """What "compress this program" should cover: its folder, or just the file when
    the folder is a shared one. A game that keeps its .exe in bin/ or Binaries/Win64/
    means the game's folder, where the big files are."""
    exe = Path(os.path.realpath(str(exe)))
    folder = exe.parent
    if is_shared_folder(folder):
        return exe
    for _ in range(3):
        if folder.name.lower() not in PROGRAM_SUBFOLDERS or is_shared_folder(folder.parent):
            break
        folder = folder.parent
    return folder


def short_path(path, limit: int = 70) -> str:
    """A path for a label: the home folder as ~, and the middle left out if it is very long."""
    text, home = str(path), str(Path.home())
    if text == home or text.startswith(home + os.sep):
        text = "~" + text[len(home):]
    if len(text) <= limit:
        return text
    keep = limit - 1
    return text[:keep // 3] + "…" + text[-(keep - keep // 3):]


# ── files and sizes ──────────────────────────────────────────────────────────
def files_of(target):
    """Every regular file of a folder (or the file itself), without following links."""
    target = str(target)
    if os.path.isfile(target) and not os.path.islink(target):
        yield target
        return
    for folder, _dirs, names in os.walk(target):
        for name in names:
            path = os.path.join(folder, name)
            try:
                if os.path.isfile(path) and not os.path.islink(path):
                    yield path
            except OSError:
                continue


def folders_of(target):
    if os.path.isdir(str(target)):
        for folder, _dirs, _names in os.walk(str(target)):
            yield folder


def total_size(target) -> tuple:
    """(number of files, bytes) as programs see them."""
    count = size = 0
    for path in files_of(target):
        try:
            size += os.path.getsize(path)
            count += 1
        except OSError:
            pass
    return count, size


def human(nbytes: float) -> str:
    for unit, step in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if nbytes >= step:
            return f"{nbytes / step:.1f} {unit}"
    return f"{int(nbytes)} bytes"


# ── data a file shares with another one ──────────────────────────────────────
# On Btrfs a copy (cp and File Explorer copy this way) and a snapshot share the data of
# the original until one of them changes. Compressing or unpacking writes a file's data
# anew, so a shared file would then be stored twice and the drive would get fuller, not
# emptier. Such files are left as they are.
FS_IOC_FIEMAP = 0xC020660B
FIEMAP_EXTENT_LAST = 0x1
FIEMAP_EXTENT_ENCODED = 0x8         # Btrfs: this part is stored compressed
FIEMAP_EXTENT_DATA_INLINE = 0x200   # a tiny file kept inside the file system's own index
FIEMAP_EXTENT_SHARED = 0x2000
_FIEMAP_HEAD = struct.Struct("=QQIIII")             # start, length, flags, found, room, -
_FIEMAP_EXTENT = struct.Struct("=QQQQQI12x")        # logical, physical, length, -, -, flags, -


def _parse_fiemap(buffer) -> tuple:
    """([(position in the file, length, flags)], whether that was the last part) from a FIEMAP answer."""
    found_count = _FIEMAP_HEAD.unpack_from(buffer, 0)[3]
    found, last = [], not found_count
    for i in range(found_count):
        logical, _physical, length, _r1, _r2, flags = _FIEMAP_EXTENT.unpack_from(
            buffer, _FIEMAP_HEAD.size + i * _FIEMAP_EXTENT.size)
        found.append((logical, length, flags))
        last = last or bool(flags & FIEMAP_EXTENT_LAST)
    return found, last


def extents(path, per_call: int = 128):
    """Where a file's data lies on the disk, as [(position in the file, length, flags)];
    None if the file system can't tell."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        found, start = [], 0
        while True:
            buffer = bytearray(_FIEMAP_HEAD.pack(start, 0xFFFFFFFFFFFFFFFF - start, 0, 0, per_call, 0)
                               + bytes(_FIEMAP_EXTENT.size * per_call))
            try:
                fcntl.ioctl(fd, FS_IOC_FIEMAP, buffer, True)
            except OSError:
                return None
            part, last = _parse_fiemap(buffer)
            found += part
            if last:
                return found
            start = part[-1][0] + part[-1][1]
    finally:
        os.close(fd)


def shared_bytes(path) -> int:
    """How much of a file's data is shared with a copy or a snapshot (0 if none, or unknown)."""
    return sum(length for _at, length, flags in extents(path) or () if flags & FIEMAP_EXTENT_SHARED)


def kind_of(path, size: int) -> str:
    """What compressing should do with a file: 'work'; 'shared' (its data is shared with a
    copy or a snapshot: leave it); 'small' (empty, or so small that the file system keeps it
    inside its index: nothing to gain, and rewriting it would only cost room in a snapshot)."""
    if size == 0:
        return "small"
    found = extents(path)
    if found is None:
        return "work"           # the file system can't tell: compress it
    if any(flags & FIEMAP_EXTENT_SHARED for _at, _length, flags in found):
        return "shared"
    if not found or all(flags & FIEMAP_EXTENT_DATA_INLINE for _at, _length, flags in found):
        return "small"
    return "work"


def stored_compressed(path):
    """True if some of the file's data is stored compressed, False if none is, None if unknown."""
    found = extents(path)
    if found is None:
        return None
    return any(flags & FIEMAP_EXTENT_ENCODED for _at, _length, flags in found)


# ── how much would it save? ──────────────────────────────────────────────────
@dataclass
class Estimate:
    files: int = 0
    size: int = 0           # bytes now (uncompressed)
    after: int = 0          # bytes expected on disk when compressed
    sampled: int = 0        # bytes that were actually tried
    shared: int = 0         # bytes in files left as they are (shared with a copy or a snapshot)
    shared_files: int = 0
    small: int = 0          # bytes in files too small to gain anything (also left as they are)

    @property
    def saved(self) -> int:
        return max(0, self.size - self.after)

    @property
    def percent(self) -> int:
        return round(self.saved * 100 / self.size) if self.size else 0


def _stored_size(block: bytes) -> int:
    """Disk space one piece of a file takes after compression, the way Btrfs counts:
    in whole 4 KB units, and uncompressed when compressing would not save a unit."""
    plain = -(-len(block) // PAGE) * PAGE
    packed = -(-len(zlib.compress(block, 6)) // PAGE) * PAGE
    return packed if packed < plain else plain


def estimate(target, samples: int = 400, cancel=None) -> Estimate:
    """Try compressing evenly spread pieces of the files (about 50 MB in all) and
    work out what the whole would take. zlib stands in for zstd; they land close.
    Files that share their data with a copy or a snapshot count as they are."""
    sizes, result = [], Estimate()
    for path in files_of(target):
        if cancel is not None and cancel.is_set():
            break
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        result.files += 1
        result.size += size
        kind = kind_of(path, size)
        if kind == "shared":
            result.shared += size
            result.shared_files += 1
        elif kind == "small":
            result.small += size
        else:
            sizes.append((path, size))
    packable = result.size - result.shared - result.small
    if not packable:
        result.after = result.size
        return result
    step = max(BLOCK, packable // samples)
    plain = packed = 0
    position, next_sample = 0, step // 2
    for path, size in sizes:
        if cancel is not None and cancel.is_set():
            break
        end = position + size
        if next_sample < end:
            try:
                with open(path, "rb") as f:
                    while next_sample < end:
                        offset = (next_sample - position) // BLOCK * BLOCK
                        f.seek(offset)
                        block = f.read(BLOCK)
                        if block:
                            plain += -(-len(block) // PAGE) * PAGE
                            packed += _stored_size(block)
                        next_sample += step
            except OSError:
                next_sample = max(next_sample, end)
        position = end
    result.sampled = plain
    result.after = (round(packable * packed / plain) if plain else packable) + result.shared + result.small
    return result


# ── the compression mark on files and folders ────────────────────────────────
def get_property(path) -> str:
    """'zstd' if the file or folder is marked for compression, else ''."""
    try:
        return os.getxattr(str(path), PROPERTY).decode("ascii", "replace")
    except (OSError, AttributeError):
        return ""


def set_property(path, value: str) -> bool:
    """Mark a file or folder (value '' removes the mark). False if it could not be changed."""
    try:
        if value:
            os.setxattr(str(path), PROPERTY, value.encode("ascii"))
        else:
            try:
                os.removexattr(str(path), PROPERTY)
            except OSError as e:
                if e.errno != errno.ENODATA:
                    raise
        return True
    except (OSError, AttributeError):
        return False


def is_compressed(target) -> bool:
    return get_property(target) == ALGORITHM


# ── real disk use (asks the administrator helper, which runs compsize) ───────
def parse_compsize(text: str):
    """(bytes on disk, bytes uncompressed) from `compsize -b`, None if not understood."""
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 4 and f[0] == "TOTAL" and f[2].isdigit() and f[3].isdigit():
            return int(f[2]), int(f[3])
    if "Processed 0 files" in text or "0 regular extents" in text:
        return 0, 0
    return None


def disk_usage(target):
    """(bytes on disk, bytes uncompressed) for a file or folder, None if it can't be measured
    (helper or compsize missing, or not allowed)."""
    if not shutil.which("pkexec") or not os.path.exists(HELPER):
        return None
    try:
        r = subprocess.run(["pkexec", HELPER, str(target)], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_compsize(r.stdout) if r.returncode == 0 else None


# ── compressing and undoing it ───────────────────────────────────────────────
@dataclass
class Outcome:
    files: int = 0                  # files handled
    failed: list = field(default_factory=list)      # files that could not be changed
    note: str = ""                  # something the user should know
    cancelled: bool = False
    left: int = 0                   # files left as they are: their data is shared with a copy or a snapshot
    left_size: int = 0
    changed: int = 0                # files that were actually compressed (or unpacked)
    repaired: int = 0               # files Btrfs had left partly undone, done again


def _batches(paths, max_files: int = 64, max_bytes: int = 256 * 1024 * 1024):
    batch, size = [], 0
    for path in paths:
        try:
            length = os.path.getsize(path)
        except OSError:
            length = 0
        if batch and (len(batch) >= max_files or size + length > max_bytes):
            yield batch, size
            batch, size = [], 0
        batch.append(path)
        size += length
    if batch:
        yield batch, size


def _gentle() -> list:
    """Run in the background of everything else: lowest disk and processor priority."""
    prefix = []
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    if shutil.which("nice"):
        prefix += ["nice", "-n", "19"]
    return prefix


def _defragment(batch, level: int):
    # -f: write each file out before going on to the next. Btrfs decides how to store the
    # data when it is written; written later, it would be stored by Btrfs's quick guess,
    # which leaves much of a program's code uncompressed (seen on the test PC).
    argv = _gentle() + ["btrfs", "filesystem", "defragment", "-f", f"-c{ALGORITHM}"]
    if level:
        argv += ["-L", str(level)]
    try:
        r = subprocess.run(argv + ["--"] + batch, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, r.stderr


def _read_through(batch) -> None:
    """Read the files once just before Btrfs rewrites them. On Linux 6.12, defragmenting
    skipped the last part (up to 128 MB) of big files whose layout was not in memory yet,
    which is the normal case for a game installed some time ago (seen on the test PC).
    Btrfs reads all of it for the rewrite anyway; this way it finds it in memory."""
    chunk = bytearray(1024 * 1024)
    for path in batch:
        try:
            with open(path, "rb", buffering=0) as f:
                while f.readinto(chunk):
                    pass
        except OSError:
            pass                # btrfs will report it if it really can't be read


def _flush(path) -> None:
    """Write a file's data out to the disk now."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def skipped_by_btrfs(path) -> bool:
    """True if a big part of the file is still stored uncompressed although it would pack
    well. Seen on Linux 6.12: defragmenting stopped early inside a file (after an undo,
    when the file was no longer in memory) and left its last 38 MB untouched. Data that
    really doesn't pack (videos, packed archives) is tried and not counted here."""
    found = extents(path)
    if not found:
        return False
    try:
        with open(path, "rb", buffering=0) as f:
            for at, length, flags in found:
                if flags & (FIEMAP_EXTENT_ENCODED | FIEMAP_EXTENT_DATA_INLINE) or length < 1024 * 1024:
                    continue
                f.seek(at + length // 2 // BLOCK * BLOCK)
                block = f.read(BLOCK)
                if len(block) == BLOCK and _stored_size(block) <= BLOCK * 9 // 10:
                    return True
    except OSError:
        return False
    return False


def _failed_files(batch, stderr: str) -> list:
    """Which files of a batch `btrfs` complained about."""
    bad = [path for path in batch if path in stderr]
    return bad or list(batch)


def compress(target, strong: bool = False, progress=None, cancel=None) -> Outcome:
    """Compress a file or a whole folder in place. progress(files_done, bytes_done) is
    called as it goes; cancel is a threading.Event."""
    reason = why_not(target)
    if reason:
        raise CompressError(reason)
    if not shutil.which("btrfs"):
        raise CompressError("The Btrfs tools are missing (package btrfs-progs).")
    outcome = Outcome()
    # Data that is not on the disk yet (a game that was just copied or updated) would be
    # skipped by Btrfs: write it out first.
    _sync(target)
    # First see which files there is something to gain on; the others are not touched.
    work, done_files, done_bytes = [], 0, 0
    for path in files_of(target):
        if cancel is not None and cancel.is_set():
            outcome.cancelled = True
            return outcome
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        kind = kind_of(path, size)
        if kind == "work":
            work.append(path)
            continue
        if kind == "shared":
            outcome.left += 1
            outcome.left_size += size
        done_files += 1
        done_bytes += size
    outcome.files = done_files
    if not work:                # nothing to gain: change nothing at all, not even the marks
        if progress is not None:
            progress(done_files, done_bytes)
        return outcome
    # New files in these folders (updates, save games) are compressed from now on.
    for folder in folders_of(target):
        set_property(folder, ALGORITHM)
    level = STRONG_LEVEL if strong else 0
    for batch, size in _batches(work):
        if cancel is not None and cancel.is_set():
            outcome.cancelled = True
            break
        for path in batch:
            set_property(path, ALGORITHM)
        _read_through(batch)
        code, err = _defragment(batch, level)
        if code != 0 and level and ("level" in err.lower() or "not supported" in err.lower()
                                    or "invalid argument" in err.lower()):
            level = 0           # this kernel can't take a level here: normal strength instead
            outcome.note = ("The strongest setting needs a newer Linux kernel (6.15 or later), "
                            "so the normal strength was used.")
            code, err = _defragment(batch, level)
        if code != 0:
            outcome.failed += _failed_files(batch, err)
        # Files Btrfs only did in part: writing them anew once gives them a new layout on
        # the disk, after which defragmenting does the whole file.
        for path in batch:
            if path not in outcome.failed and skipped_by_btrfs(path):
                try:
                    rewrite(path)
                    _flush(path)        # data still in memory would be skipped again
                except OSError:
                    continue
                if _defragment([path], level)[0] == 0:
                    outcome.repaired += 1
        outcome.changed += len(batch) - (len(_failed_files(batch, err)) if code != 0 else 0)
        outcome.files += len(batch)
        done_bytes += size
        if progress is not None:
            progress(outcome.files, done_bytes)
    _sync(target)
    return outcome


def rewrite(path) -> None:
    """Write a file's data again, in place and unchanged, so that the file system stores
    it with its current settings. Holes stay holes; the modification time is kept."""
    st = os.stat(path)
    fd = os.open(path, os.O_RDWR)
    try:
        offset = 0
        while offset < st.st_size:
            try:
                start = os.lseek(fd, offset, os.SEEK_DATA)
            except OSError as e:
                if e.errno == errno.ENXIO:      # only a hole is left
                    break
                start = offset
            try:
                end = os.lseek(fd, start, os.SEEK_HOLE)
            except OSError:
                end = st.st_size
            position = start
            while position < end:
                chunk = os.pread(fd, min(1024 * 1024, end - position), position)
                if not chunk:
                    break
                os.pwrite(fd, chunk, position)
                position += len(chunk)
            if end <= offset:
                break
            offset = end
    finally:
        os.close(fd)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def decompress(target, progress=None, cancel=None) -> Outcome:
    """Undo compress(): remove the marks and store the files the drive's normal way again."""
    reason = why_not(target)
    if reason:
        raise CompressError(reason)
    outcome = Outcome()
    for folder in folders_of(target):
        set_property(folder, "")
    done_bytes = 0
    for path in files_of(target):
        if cancel is not None and cancel.is_set():
            outcome.cancelled = True
            break
        try:
            size = os.path.getsize(path)
            set_property(path, "")
            packed = stored_compressed(path)
            if packed is False:
                pass                            # nothing of it is stored compressed: nothing to undo
            elif shared_bytes(path):
                outcome.left += 1               # unpacking it would store the shared data twice
                outcome.left_size += size
            else:
                rewrite(path)
                outcome.changed += 1
        except OSError:
            outcome.failed.append(path)
            size = 0
        outcome.files += 1
        done_bytes += size
        if progress is not None:
            progress(outcome.files, done_bytes)
    _sync(target)
    return outcome


def _sync(target) -> None:
    """Make sure the new layout is on the disk, so that sizes measured next are right."""
    folder = str(target) if os.path.isdir(str(target)) else os.path.dirname(str(target))
    try:
        subprocess.run(["btrfs", "filesystem", "sync", folder], capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        try:
            os.sync()
        except OSError:
            pass
