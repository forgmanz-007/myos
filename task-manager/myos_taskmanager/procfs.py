"""Processes and system counters, read from /proc and /sys.

Every reader takes the filesystem root, so the tests can point it at a made-up tree."""
import ipaddress
import os
import pwd
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

PF_KTHREAD = 0x00200000     # /proc/PID/stat flag of kernel threads
SECTOR = 512                # /proc/diskstats counts 512-byte sectors on every disk


_CHUNK = 65536


def read_text(path) -> str:
    """Contents of a small file, '' if it can't be read. Uses plain file descriptors:
    hundreds of /proc files are read every second, and this costs a third of open()."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return ""
    try:
        data = os.read(fd, _CHUNK)
        if len(data) == _CHUNK:                 # a big file: keep reading to the end
            parts = [data]
            while True:
                more = os.read(fd, _CHUNK)
                if not more:
                    break
                parts.append(more)
            data = b"".join(parts)
    except OSError:
        return ""
    finally:
        os.close(fd)
    return data.decode("utf-8", "replace")


def _int(text, default=0) -> int:
    try:
        return int(str(text).strip())
    except ValueError:
        return default


# ── processes ────────────────────────────────────────────────────────────────
@dataclass
class Proc:
    pid: int
    ppid: int
    name: str
    state: str          # R, S, D, T, Z, I ... as in /proc/PID/stat
    uid: int
    user: str
    kernel: bool        # a kernel thread, not a program
    threads: int
    start: int          # start time in clock ticks since boot; (pid, start) names one process
    ticks: int          # CPU time used so far (user + system)
    mem: int            # bytes of its own memory (resident minus shared)
    cmdline: str = ""
    cgroup: str = ""
    cpu: float = 0.0    # percent of the whole PC (all cores busy = 100)
    disk: float = 0.0   # bytes read + written per second (own processes only)

    @property
    def key(self):
        return (self.pid, self.start)

    @property
    def status(self) -> str:
        return {"T": "Suspended", "t": "Suspended", "Z": "Ended", "X": "Ended"}.get(self.state, "Running")


def parse_stat(text: str):
    """/proc/PID/stat → (name, fields after the name). The name may hold spaces and brackets."""
    left, right = text.find("("), text.rfind(")")
    if left < 0 or right < left:
        return None
    rest = text[right + 1:].split()
    return (text[left + 1:right], rest) if len(rest) >= 22 else None


def cgroup_path(text: str) -> str:
    """The cgroup v2 path from /proc/PID/cgroup ('0::/user.slice/...')."""
    for line in text.splitlines():
        if line.startswith("0::"):
            return line[3:].strip()
    return ""


def parse_io(text: str) -> int:
    """Bytes read from and written to storage so far, from /proc/PID/io (-1 if unreadable)."""
    total, found = 0, False
    for line in text.splitlines():
        if line.startswith(("read_bytes:", "write_bytes:")):
            total += _int(line.split(":", 1)[1])
            found = True
    return total if found else -1


def full_name(comm: str, argv0: str) -> str:
    """Linux cuts process names at 15 characters; take the full name from the command line."""
    if len(comm) == 15 and argv0:
        base = os.path.basename(argv0)
        if base.startswith(comm) and " " not in base and len(base) <= 64:
            return base
    return comm


class ProcessSampler:
    """Lists all processes; CPU and disk use are measured between two calls of sample()."""

    REFRESH_EVERY = 20      # samples between re-reads of a process's command line and group
    REFRESH_IDLE = 5        # samples between re-reads of memory and disk use of a process
                            # that used no processor time (most processes, most of the time)

    def __init__(self, root="/", clk_tck=None, page_size=None, ncpu=None, uid=None):
        self.proc = os.path.join(str(root), "proc")
        self.clk = clk_tck or os.sysconf("SC_CLK_TCK")
        self.page = page_size or os.sysconf("SC_PAGE_SIZE")
        self.ncpu = ncpu or os.cpu_count() or 1
        self.me = os.getuid() if uid is None else uid
        self._static = {}
        self._prev = {}
        self._last = None
        self._tick = 0
        self._users = {}

    def _user(self, uid: int) -> str:
        if uid not in self._users:
            try:
                self._users[uid] = pwd.getpwuid(uid).pw_name
            except (KeyError, OSError):
                self._users[uid] = str(uid)
        return self._users[uid]

    def _load_static(self, base: str, kernel: bool, old) -> dict:
        uid = 0
        for line in read_text(base + "/status").splitlines():
            if line.startswith("Uid:"):
                uid = _int(line.split()[1])
                break
        raw = "" if kernel else read_text(base + "/cmdline")
        return {
            "uid": uid,
            "user": self._user(uid),
            "argv0": raw.split("\0", 1)[0],
            "cmdline": raw.replace("\0", " ").strip(),
            "cgroup": "" if kernel else cgroup_path(read_text(base + "/cgroup")),
            "reads": (old["reads"] + 1) if old else 1,
        }

    def _read_one(self, pid: int, elapsed: float):
        base = f"{self.proc}/{pid}"
        parsed = parse_stat(read_text(base + "/stat"))
        if not parsed:
            return None
        comm, f = parsed
        try:
            state, ppid, flags = f[0], int(f[1]), int(f[6])
            ticks, threads, start = int(f[11]) + int(f[12]), int(f[17]), int(f[19])
        except ValueError:
            return None
        key = (pid, start)
        kernel = bool(flags & PF_KTHREAD) or pid == 2
        st = self._static.get(key)
        # A new process is re-read a few times (the desktop moves it into its app
        # group right after starting it), later only now and then.
        if st is None or st["reads"] < 3 or (self._tick + pid) % self.REFRESH_EVERY == 0:
            st = self._load_static(base, kernel, st)
            self._static[key] = st
        prev = self._prev.get(key)
        mem, io = 0, -1
        if kernel:
            pass
        elif prev is None or ticks != prev[0] or (self._tick + pid) % self.REFRESH_IDLE == 0:
            statm = read_text(base + "/statm").split()
            if len(statm) >= 3:
                mem = max(0, (_int(statm[1]) - _int(statm[2])) * self.page)
            if st["uid"] == self.me:
                io = parse_io(read_text(base + "/io"))
        else:                                   # it did not run: nothing changed
            mem, io = prev[2], prev[1]
        proc = Proc(pid, ppid, full_name(comm, st["argv0"]), state, st["uid"], st["user"], kernel,
                    threads, start, ticks, mem, st["cmdline"], st["cgroup"])
        if prev and elapsed > 0:
            proc.cpu = max(0.0, (ticks - prev[0]) / self.clk / elapsed / self.ncpu * 100)
            if io >= 0 and prev[1] >= 0:
                proc.disk = max(0.0, (io - prev[1]) / elapsed)
        self._prev[key] = (ticks, io, mem)
        return proc

    def sample(self, now=None) -> list:
        now = time.monotonic() if now is None else now
        elapsed = (now - self._last) if self._last is not None else 0.0
        self._last = now
        self._tick += 1
        try:
            entries = os.listdir(self.proc)
        except OSError:
            entries = []
        procs = []
        for entry in entries:
            if entry.isdigit():
                proc = self._read_one(int(entry), elapsed)
                if proc:
                    procs.append(proc)
        procs.sort(key=lambda p: p.pid)
        alive = {p.key for p in procs}
        self._prev = {k: v for k, v in self._prev.items() if k in alive}
        self._static = {k: v for k, v in self._static.items() if k in alive}
        return procs


def descendants(procs, pid: int) -> list:
    """pid and every process started by it, deepest last."""
    children = {}
    for p in procs:
        children.setdefault(p.ppid, []).append(p.pid)
    out, queue = [], [pid]
    while queue:
        cur = queue.pop(0)
        if cur in out:
            continue
        out.append(cur)
        queue.extend(children.get(cur, []))
    return out


# ── whole-system counters ────────────────────────────────────────────────────
def read_cpu_times(root="/") -> tuple:
    """(busy, total) CPU time of all cores together, in clock ticks."""
    for line in read_text(os.path.join(str(root), "proc/stat")).splitlines():
        if line.startswith("cpu "):
            vals = [_int(v) for v in line.split()[1:9]]
            vals += [0] * (8 - len(vals))
            idle = vals[3] + vals[4]            # idle + waiting for the disk
            return sum(vals) - idle, sum(vals)
    return 0, 0


def read_meminfo(root="/") -> dict:
    """/proc/meminfo in bytes."""
    out = {}
    for line in read_text(os.path.join(str(root), "proc/meminfo")).splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            out[name] = _int(parts[0]) * (1024 if len(parts) > 1 and parts[1] == "kB" else 1)
    return out


def memory_summary(mem: dict) -> dict:
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", mem.get("MemFree", 0))
    swap_total = mem.get("SwapTotal", 0)
    return {
        "total": total,
        "used": max(0, total - available),
        "available": available,
        "cached": mem.get("Cached", 0) + mem.get("Buffers", 0) + mem.get("SReclaimable", 0),
        "swap_total": swap_total,
        "swap_used": max(0, swap_total - mem.get("SwapFree", 0)),
    }


def read_compressed(root="/", mem: dict = None) -> tuple:
    """Memory that is kept compressed instead of being thrown out or written to disk:
    (bytes of data, bytes of RAM it takes). Counts zswap (from /proc/meminfo) and
    compressed swap devices in RAM (zram)."""
    mem = read_meminfo(root) if mem is None else mem
    data, held = mem.get("Zswapped", 0), mem.get("Zswap", 0)
    base = os.path.join(str(root), "sys/block")
    try:
        devices = [d for d in os.listdir(base) if d.startswith("zram")]
    except OSError:
        devices = []
    for dev in devices:
        f = read_text(f"{base}/{dev}/mm_stat").split()
        if len(f) >= 3:
            data += _int(f[0])
            held += _int(f[2])
    return data, held


def read_memory_pressure(root="/") -> float:
    """Percent of the last 10 seconds in which programs had to wait for memory (0 if unknown)."""
    for line in read_text(os.path.join(str(root), "proc/pressure/memory")).splitlines():
        if line.startswith("some "):
            m = re.search(r"avg10=([0-9.]+)", line)
            try:
                return float(m.group(1)) if m else 0.0
            except ValueError:
                return 0.0
    return 0.0


def read_uptime(root="/") -> float:
    parts = read_text(os.path.join(str(root), "proc/uptime")).split()
    try:
        return float(parts[0])
    except (IndexError, ValueError):
        return 0.0


def read_thread_count(root="/") -> int:
    """Number of threads on the system (4th field of /proc/loadavg: running/total)."""
    parts = read_text(os.path.join(str(root), "proc/loadavg")).split()
    return _int(parts[3].split("/")[-1]) if len(parts) > 3 and "/" in parts[3] else 0


def cpu_info(root="/") -> dict:
    """Name, core counts and current speed of the processor."""
    root = str(root)
    text = read_text(os.path.join(root, "proc/cpuinfo"))
    model, logical, cores, sockets = "", 0, set(), set()
    physical = core = None
    for line in text.splitlines() + [""]:
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "processor":
            logical += 1
        elif key == "model name" and not model:
            model = value
        elif key == "physical id":
            physical = value
        elif key == "core id":
            core = value
        elif not line.strip():
            if physical is not None:
                sockets.add(physical)
                if core is not None:
                    cores.add((physical, core))
            physical = core = None
    try:
        cpus = [d for d in os.listdir(os.path.join(root, "sys/devices/system/cpu"))
                if re.fullmatch(r"cpu\d+", d)]
    except OSError:
        cpus = []
    return {
        "model": re.sub(r"\s+", " ", model),
        "logical": logical or len(cpus) or (os.cpu_count() or 1),
        "cores": len(cores),
        "sockets": len(sockets),
    }


def cpu_speed(root="/") -> float:
    """Current speed of the processor in MHz (average of all cores), 0 if unknown."""
    base = os.path.join(str(root), "sys/devices/system/cpu")
    try:
        cpus = [d for d in os.listdir(base) if re.fullmatch(r"cpu\d+", d)]
    except OSError:
        cpus = []
    freqs = []
    for d in cpus:
        khz = _int(read_text(f"{base}/{d}/cpufreq/scaling_cur_freq"), -1)
        if khz > 0:
            freqs.append(khz / 1000)
    if not freqs:
        for line in read_text(os.path.join(str(root), "proc/cpuinfo")).splitlines():
            if line.startswith("cpu MHz"):
                try:
                    freqs.append(float(line.split(":", 1)[1]))
                except ValueError:
                    pass
    return sum(freqs) / len(freqs) if freqs else 0.0


# ── disks ────────────────────────────────────────────────────────────────────
_SKIP_DISKS = ("loop", "ram", "zram", "dm-", "sr", "fd", "md", "nbd")


def list_disks(root="/") -> list:
    """Real drives (no partitions, no virtual devices, no empty card readers)."""
    base = os.path.join(str(root), "sys/block")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    return [n for n in names if not n.startswith(_SKIP_DISKS)
            and _int(read_text(f"{base}/{n}/size")) > 0]


def disk_info(root, name: str) -> dict:
    base = os.path.join(str(root), "sys/block", name)
    model = " ".join(read_text(base + "/device/model").split())
    vendor = " ".join(read_text(base + "/device/vendor").split())
    if model and vendor and vendor.upper() != "ATA" and not vendor.lower().startswith("0x"):
        model = f"{vendor} {model}"
    if name.startswith("nvme"):
        kind = "SSD (NVMe)"
    elif name.startswith("mmcblk"):
        kind = "Memory card"
    elif name.startswith(("vd", "xvd")):
        kind = "Virtual disk"
    elif "/usb" in os.path.realpath(base):
        kind = "USB drive"
    elif read_text(base + "/queue/rotational").strip() == "1":
        kind = "Hard disk (HDD)"
    else:
        kind = "SSD"
    return {"name": name, "model": model, "kind": kind,
            "size": _int(read_text(base + "/size")) * SECTOR}


def read_diskstats(root="/") -> dict:
    """name → (sectors read, sectors written, milliseconds busy)."""
    out = {}
    for line in read_text(os.path.join(str(root), "proc/diskstats")).splitlines():
        f = line.split()
        if len(f) >= 13:
            out[f[2]] = (_int(f[5]), _int(f[9]), _int(f[12]))
    return out


# ── network ──────────────────────────────────────────────────────────────────
def list_nets(root="/") -> list:
    """Network adapters that are real hardware: [(name, 'Wi-Fi' or 'Ethernet')]."""
    base = os.path.join(str(root), "sys/class/net")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for n in names:
        if n == "lo" or not os.path.exists(f"{base}/{n}/device"):
            continue
        wifi = os.path.exists(f"{base}/{n}/wireless") or os.path.exists(f"{base}/{n}/phy80211")
        out.append((n, "Wi-Fi" if wifi else "Ethernet"))
    return out


def read_netdev(root="/") -> dict:
    """name → (bytes received, bytes sent)."""
    out = {}
    for line in read_text(os.path.join(str(root), "proc/net/dev")).splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9:
            out[name.strip()] = (_int(f[0]), _int(f[8]))
    return out


def ipv4_address(iface: str) -> str:
    try:
        import fcntl
        import socket
        import struct
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            packed = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", iface.encode()[:15]))
        return socket.inet_ntoa(packed[20:24])
    except Exception:   # not connected, no such adapter, or not Linux
        return ""


def ipv6_address(root, iface: str) -> str:
    """The adapter's IPv6 address: a global one if it has one, else the link-local one."""
    best = ""
    for line in read_text(os.path.join(str(root), "proc/net/if_inet6")).splitlines():
        f = line.split()
        if len(f) >= 6 and f[5] == iface:
            try:
                addr = ipaddress.IPv6Address(int(f[0], 16)).compressed
            except ValueError:
                continue
            if f[3] == "00":
                return addr
            best = best or addr
    return best


def wifi_signal(root, iface: str) -> int:
    """Wi-Fi signal quality in percent, -1 if unknown."""
    for line in read_text(os.path.join(str(root), "proc/net/wireless")).splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if name.strip() == iface and len(f) >= 2:
            try:
                return max(0, min(100, round(float(f[1].rstrip(".")) / 70 * 100)))
            except ValueError:
                return -1
    return -1


def net_info(root, iface: str) -> dict:
    base = os.path.join(str(root), "sys/class/net", iface)
    speed = _int(read_text(base + "/speed"), -1)
    return {
        "connected": read_text(base + "/operstate").strip() in ("up", "unknown")
                     and read_text(base + "/carrier").strip() != "0",
        "speed_mbit": speed if speed > 0 else 0,
        "ipv4": ipv4_address(iface) if str(root) == "/" else "",
        "ipv6": ipv6_address(root, iface),
        "signal": wifi_signal(root, iface),
    }


# ── graphics cards ───────────────────────────────────────────────────────────
def read_amd_gpus(root="/") -> list:
    """AMD cards report their load in /sys: [{id, slot, busy, mem_used, mem_total}]."""
    base = os.path.join(str(root), "sys/class/drm")
    try:
        cards = sorted(d for d in os.listdir(base) if re.fullmatch(r"card\d+", d))
    except OSError:
        return []
    out = []
    for card in cards:
        dev = f"{base}/{card}/device"
        busy = read_text(dev + "/gpu_busy_percent").strip()
        if not busy.isdigit():
            continue
        out.append({"id": card, "slot": os.path.basename(os.path.realpath(dev)),
                    "busy": min(100, int(busy)),
                    "mem_used": _int(read_text(dev + "/mem_info_vram_used")),
                    "mem_total": _int(read_text(dev + "/mem_info_vram_total"))})
    return out


NVIDIA_QUERY = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu"


def parse_nvidia_line(line: str):
    """One line of nvidia-smi --query-gpu=NVIDIA_QUERY --format=csv,noheader,nounits."""
    f = [x.strip() for x in line.split(",")]
    if len(f) < 6 or not f[0].isdigit():
        return None

    def num(text):
        try:
            return float(text)
        except ValueError:      # "[N/A]" on cards that do not report a value
            return -1.0

    mib = 1024 * 1024
    return {"id": f"nvidia{f[0]}", "name": f[1] if f[1].upper().startswith("NVIDIA") else f"NVIDIA {f[1]}",
            "busy": max(0, min(100, int(num(f[2])))) if num(f[2]) >= 0 else 0,
            "mem_used": int(max(0.0, num(f[3])) * mib), "mem_total": int(max(0.0, num(f[4])) * mib),
            "temp": int(num(f[5])) if num(f[5]) >= 0 else None}


# ── everything except the process list, with rates ───────────────────────────
@dataclass
class SystemSnapshot:
    time: float = 0.0
    cpu: float = 0.0                # percent
    memory: dict = field(default_factory=dict)
    disks: dict = field(default_factory=dict)   # name → {active, read, write}  (%, bytes/s, bytes/s)
    nets: dict = field(default_factory=dict)    # name → {down, up}  bytes/s
    gpus: list = field(default_factory=list)
    uptime: float = 0.0
    processes: int = 0
    threads: int = 0
    cpu_mhz: float = 0.0


class SystemSampler:
    def __init__(self, root="/"):
        self.root = str(root)
        self._last = None
        self._cpu = None
        self._disks = {}
        self._nets = {}

    def sample(self, now=None) -> SystemSnapshot:
        now = time.monotonic() if now is None else now
        elapsed = (now - self._last) if self._last is not None else 0.0
        self._last = now
        snap = SystemSnapshot(time=now)

        busy, total = read_cpu_times(self.root)
        if self._cpu and total > self._cpu[1]:
            snap.cpu = max(0.0, min(100.0, (busy - self._cpu[0]) / (total - self._cpu[1]) * 100))
        self._cpu = (busy, total)

        mem = read_meminfo(self.root)
        snap.memory = memory_summary(mem)
        snap.memory["compressed"], snap.memory["compressed_takes"] = read_compressed(self.root, mem)
        snap.memory["pressure"] = read_memory_pressure(self.root)

        stats = read_diskstats(self.root)
        for name in list_disks(self.root):
            cur, prev = stats.get(name), self._disks.get(name)
            entry = {"active": 0.0, "read": 0.0, "write": 0.0}
            if cur and prev and elapsed > 0:
                entry = {"active": max(0.0, min(100.0, (cur[2] - prev[2]) / (elapsed * 1000) * 100)),
                         "read": max(0.0, (cur[0] - prev[0]) * SECTOR / elapsed),
                         "write": max(0.0, (cur[1] - prev[1]) * SECTOR / elapsed)}
            snap.disks[name] = entry
        self._disks = stats

        counters = read_netdev(self.root)
        for name, _kind in list_nets(self.root):
            cur, prev = counters.get(name), self._nets.get(name)
            entry = {"down": 0.0, "up": 0.0}
            if cur and prev and elapsed > 0:
                entry = {"down": max(0.0, (cur[0] - prev[0]) / elapsed),
                         "up": max(0.0, (cur[1] - prev[1]) / elapsed)}
            snap.nets[name] = entry
        self._nets = counters

        snap.gpus = read_amd_gpus(self.root)
        snap.uptime = read_uptime(self.root)
        snap.cpu_mhz = cpu_speed(self.root)
        snap.threads = read_thread_count(self.root)
        try:
            snap.processes = sum(1 for e in os.listdir(os.path.join(self.root, "proc")) if e.isdigit())
        except OSError:
            snap.processes = 0
        return snap


class NvidiaMonitor:
    """NVIDIA cards are read through nvidia-smi. One copy keeps running and reports
    every 2 seconds, which costs far less than starting it for every update."""

    def __init__(self, exe="nvidia-smi"):
        import shutil
        import threading
        self._latest = {}
        self._proc = None
        self._lock = threading.Lock()
        self.exe = shutil.which(exe)
        if self.exe:
            threading.Thread(target=self._run, daemon=True, name="nvidia-smi").start()

    def _run(self):
        import subprocess
        try:
            self._proc = subprocess.Popen(
                [self.exe, f"--query-gpu={NVIDIA_QUERY}", "--format=csv,noheader,nounits", "-l", "2"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return
        for line in self._proc.stdout:
            gpu = parse_nvidia_line(line)
            if gpu:
                with self._lock:
                    self._latest[gpu["id"]] = (time.monotonic(), gpu)

    def latest(self) -> list:
        now = time.monotonic()
        with self._lock:
            return [gpu for seen, gpu in self._latest.values() if now - seen < 10]

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()


def pci_name(slot: str) -> str:
    """Name of the card in a PCI slot ('0000:03:00.0') as lspci knows it, '' if unknown."""
    import shlex
    import shutil
    import subprocess
    if not slot or not shutil.which("lspci"):
        return ""
    try:
        out = subprocess.run(["lspci", "-mm", "-s", slot], capture_output=True, text=True,
                             timeout=5).stdout
        fields = shlex.split(out.splitlines()[0]) if out.strip() else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return short_gpu_name(fields[2], fields[3]) if len(fields) >= 4 else ""


def short_gpu_name(vendor: str, device: str) -> str:
    """'Advanced Micro Devices, Inc. [AMD/ATI]' + 'Navi 23 [Radeon RX 6600]' → 'AMD Radeon RX 6600'."""
    short = "AMD" if "AMD" in vendor or "ATI" in vendor else "NVIDIA" if "NVIDIA" in vendor.upper() \
        else "Intel" if "Intel" in vendor else vendor
    m = re.search(r"\[([^\]]+)\]\s*$", device)
    return f"{short} {m.group(1) if m else device}".strip()
