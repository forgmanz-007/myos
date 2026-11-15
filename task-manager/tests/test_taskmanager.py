"""Tests for Task Manager. The system is a made-up /proc and /sys tree, and
systemctl, nvidia-smi and lspci are small fake programs, so the tests run anywhere."""
import os
import pwd
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="myos-tm-"))
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = str(TMP / var.lower())
os.environ["XDG_CONFIG_DIRS"] = str(TMP / "etc-xdg")
os.environ["XDG_DATA_DIRS"] = f"{TMP / 'usr-local-share'}:{TMP / 'usr-share'}"
os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BIN = TMP / "bin"
BIN.mkdir()
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
HELPER = HERE.parent / "libexec" / "end-process"
os.environ["MYOS_TASKMANAGER_HELPER"] = str(HELPER)

from myos_taskmanager import actions, apps, desktopfile, formats, procfs, services, startup  # noqa: E402

LINUX = os.path.isdir("/proc/self")
ME = 1000
KTHREAD = 0x00200040


def fake_program(name, script):
    p = BIN / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class FakeSystem:
    """A /proc and /sys tree that tests fill in and change between samples."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="root-", dir=TMP))
        (self.root / "proc").mkdir()
        self.cpu(busy=1000, idle=9000)
        self.meminfo(total=16 * 1024 * 1024, available=10 * 1024 * 1024)
        write(self.root / "proc/uptime", "19027.50 70000.00\n")
        write(self.root / "proc/loadavg", "0.50 0.40 0.30 2/612 4242\n")

    def proc(self, pid, comm, ppid=1, uid=ME, state="S", flags=0x400000, ticks=0, threads=1,
             start=5000, resident=6000, shared=1000, cmdline=None, cgroup="", io=None):
        d = self.root / "proc" / str(pid)
        d.mkdir(exist_ok=True)
        write(d / "stat", f"{pid} ({comm}) {state} {ppid} {pid} {pid} 0 -1 {flags} 0 0 0 0 "
                          f"{ticks} 0 0 0 20 0 {threads} 0 {start} 123456 {resident} 1844 1 1 0 0 0\n")
        write(d / "status", f"Name:\t{comm[:15]}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        write(d / "statm", f"30000 {resident} {shared} 10 0 4000 0\n")
        (d / "cmdline").write_bytes(("\0".join(cmdline if cmdline is not None else [comm]) + "\0").encode()
                                    if cmdline != [] else b"")
        write(d / "cgroup", f"0::{cgroup}\n")
        if io is not None:
            write(d / "io", f"rchar: 5\nwchar: 5\nread_bytes: {io[0]}\nwrite_bytes: {io[1]}\n")
        return d

    def remove(self, pid):
        import shutil
        shutil.rmtree(self.root / "proc" / str(pid))

    def cpu(self, busy, idle):
        write(self.root / "proc/stat", f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 {busy} 0 0 {idle} 0 0 0 0 0 0\n"
                                       "intr 1 2 3\n")

    def meminfo(self, total, available, cached=2 * 1024 * 1024, swap_total=0, swap_free=0):
        write(self.root / "proc/meminfo",
              f"MemTotal:       {total} kB\nMemFree:        {available // 2} kB\n"
              f"MemAvailable:   {available} kB\nBuffers:          100000 kB\nCached:         {cached} kB\n"
              f"SReclaimable:     50000 kB\nSwapTotal:      {swap_total} kB\nSwapFree:       {swap_free} kB\n"
              "HugePages_Total:       0\n")

    def disk(self, name, sectors=1000000, rotational="0", model="", vendor=""):
        b = self.root / "sys/block" / name
        write(b / "size", f"{sectors}\n")
        write(b / "queue/rotational", f"{rotational}\n")
        if model:
            write(b / "device/model", f"{model}   \n")
        if vendor:
            write(b / "device/vendor", f"{vendor}     \n")

    def diskstats(self, **disks):
        lines = []
        for name, (rd, wr, busy) in disks.items():
            lines.append(f"   8       0 {name} 100 0 {rd} 50 200 0 {wr} 70 0 {busy} 120 0 0 0 0 0 0\n")
        write(self.root / "proc/diskstats", "".join(lines))

    def net(self, name, physical=True, wifi=False, state="up", speed=None):
        b = self.root / "sys/class/net" / name
        write(b / "operstate", f"{state}\n")
        if physical:
            (b / "device").mkdir(parents=True, exist_ok=True)
        if wifi:
            (b / "wireless").mkdir(parents=True, exist_ok=True)
        if speed is not None:
            write(b / "speed", f"{speed}\n")

    def netdev(self, **nets):
        text = ("Inter-|   Receive                                                |  Transmit\n"
                " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n")
        for name, (rx, tx) in nets.items():
            text += f"{name:>6}: {rx} 10 0 0 0 0 0 0 {tx} 20 0 0 0 0 0 0\n"
        write(self.root / "proc/net/dev", text)


def desktop_entry(folder: Path, file_id: str, name: str, extra: str = "", exec_line: str = "prog") -> Path:
    return write(folder / file_id, f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n"
                                   f"Icon=some-icon\n{extra}")


APPS_LOCAL = TMP / "usr-local-share" / "applications"
APPS = TMP / "usr-share" / "applications"
desktop_entry(APPS, "firefox.desktop", "Firefox")
desktop_entry(APPS, "org.kde.dolphin.desktop", "Dolphin", "Name[de]=Delfin\n")
desktop_entry(APPS_LOCAL, "org.kde.dolphin.desktop", "File Explorer")
desktop_entry(APPS, "myos-exe-runner.desktop", "Windows Program (virus-checked)", "NoDisplay=true\n")
desktop_entry(APPS, "deepseek-shell.desktop", "DeepSeek Shell")
USER_SLICE = "/user.slice/user-1000.slice/user@1000.service"


def desktop_system():
    """A small but typical system: two apps, desktop parts, system services, kernel threads."""
    fs = FakeSystem()
    fs.proc(1, "systemd", ppid=0, uid=0, cgroup="/init.scope", cmdline=["/sbin/init"])
    fs.proc(2, "kthreadd", ppid=0, uid=0, flags=KTHREAD, cmdline=[], resident=0, shared=0)
    fs.proc(17, "rcu_sched", ppid=2, uid=0, flags=KTHREAD, cmdline=[], resident=0, shared=0, ticks=40)
    fs.proc(300, "NetworkManager", uid=0, cgroup="/system.slice/NetworkManager.service")
    fs.proc(310, "clamd", uid=110, cgroup="/system.slice/clamav-daemon.service", resident=260000, shared=2000)
    fs.proc(800, "systemd", uid=ME, cgroup=f"{USER_SLICE}/init.scope", cmdline=["/usr/lib/systemd/systemd", "--user"])
    fs.proc(900, "kwin_wayland", ppid=800, cgroup=f"{USER_SLICE}/session.slice/plasma-kwin_wayland.service")
    fs.proc(910, "plasmashell", ppid=800, cgroup=f"{USER_SLICE}/session.slice/plasma-plasmashell.service",
            resident=90000, shared=30000)
    fs.proc(920, "xwaylandvideobr", ppid=800, cmdline=["/usr/bin/xwaylandvideobridge"],
            cgroup=f"{USER_SLICE}/app.slice/app-org.kde.xwaylandvideobridge@autostart.service")
    fox = f"{USER_SLICE}/app.slice/app-firefox@2afeee41b65c4f7cae0cb14a5ec72be4.service"
    fs.proc(1000, "firefox-bin", ppid=800, cgroup=fox, cmdline=["/usr/lib/firefox/firefox-bin"], io=(0, 0),
            resident=120000, shared=40000)
    fs.proc(1001, "Web Content", ppid=1000, cgroup=fox, io=(0, 0),
            cmdline=["/usr/lib/firefox/firefox-bin", "-contentproc", "-isForBrowser"])
    fs.proc(1002, "Isolated Web Co", ppid=1000, cgroup=fox, io=(0, 0),
            cmdline=["/usr/lib/firefox/firefox-bin", "-contentproc", "-isForBrowser"])
    fs.proc(1100, "dolphin", ppid=800, io=(0, 0),
            cgroup=f"{USER_SLICE}/app.slice/app-org.kde.dolphin@77aa.service")
    return fs


def sampler(fs, ncpu=4):
    return procfs.ProcessSampler(fs.root, clk_tck=100, page_size=4096, ncpu=ncpu, uid=ME)


class Formats(unittest.TestCase):
    def test_numbers_as_text(self):
        self.assertEqual(formats.percent(0), "0%")
        self.assertEqual(formats.percent(0.31), "0.3%")
        self.assertEqual(formats.percent(12.54), "12.5%")
        self.assertEqual(formats.percent(100.0), "100%")
        self.assertEqual(formats.memory(1294 * formats.MB + 512 * 1024), "1,294.5 MB")
        self.assertEqual(formats.size(15.6 * formats.GB), "15.6 GB")
        self.assertEqual(formats.size(512 * formats.MB), "512 MB")
        self.assertEqual(formats.size(1.8 * 1024 * formats.GB), "1.8 TB")
        self.assertEqual(formats.rate(0), "0 MB/s")
        self.assertEqual(formats.rate(20 * 1024), "20 KB/s")
        self.assertEqual(formats.rate(3.25 * formats.MB), "3.2 MB/s")
        self.assertEqual(formats.bits(0), "0 Kbps")
        self.assertEqual(formats.bits(2000), "16.0 Kbps")
        self.assertEqual(formats.bits(150000), "1.2 Mbps")
        self.assertEqual(formats.bits(125e6), "1.0 Gbps")
        self.assertEqual(formats.uptime(19027), "0:05:17:07")
        self.assertEqual(formats.uptime(3 * 86400 + 61), "3:00:01:01")
        self.assertEqual(formats.speed(3520), "3.52 GHz")
        self.assertEqual(formats.speed(0), "")

    def test_graph_scale_and_tint(self):
        self.assertEqual(formats.nice_ceiling(0, 100), 100)
        self.assertEqual(formats.nice_ceiling(130, 100), 200)
        self.assertEqual(formats.nice_ceiling(4200, 100), 5000)
        self.assertEqual(formats.nice_ceiling(5000, 100), 5000)
        self.assertEqual(formats.nice_ceiling(51000, 100), 100000)
        self.assertEqual(formats.rate_scale(0), 100 * formats.KB)                 # never an empty scale
        self.assertEqual(formats.rate(formats.rate_scale(300 * formats.KB)), "0.5 MB/s")
        self.assertEqual(formats.rate(formats.rate_scale(900 * formats.KB)), "1.0 MB/s")
        self.assertEqual(formats.rate(formats.rate_scale(130 * formats.MB)), "200.0 MB/s")
        self.assertEqual(formats.bits(formats.bits_scale(0)), "100.0 Kbps")
        self.assertEqual(formats.bits(formats.bits_scale(125000)), "1.0 Mbps")
        self.assertEqual(formats.bits(formats.bits_scale(40e6)), "500.0 Mbps")
        self.assertEqual(formats.heat_level("cpu", 0), 1)
        self.assertEqual(formats.heat_level("cpu", 3), 2)
        self.assertEqual(formats.heat_level("cpu", 60), 4)
        self.assertEqual(formats.heat_level("mem", 900 * formats.MB), 3)
        self.assertEqual(formats.heat_level("disk", 0), 1)


class Processes(unittest.TestCase):
    def test_names_with_spaces_and_brackets(self):
        self.assertEqual(procfs.parse_stat("12 (Web Content) S " + "1 " * 30)[0], "Web Content")
        self.assertEqual(procfs.parse_stat("12 ((sd-pam)) S " + "1 " * 30)[0], "(sd-pam)")
        name, rest = procfs.parse_stat("12 (a) b) c) R 7 " + "1 " * 30)
        self.assertEqual((name, rest[0], rest[1]), ("a) b) c", "R", "7"))
        self.assertIsNone(procfs.parse_stat(""))
        self.assertIsNone(procfs.parse_stat("12 (short) S 1 2"))

    def test_cpu_memory_and_disk_use(self):
        fs = desktop_system()
        s = sampler(fs)
        first = {p.pid: p for p in s.sample(now=100.0)}
        self.assertEqual(first[1000].cpu, 0.0)           # nothing to compare with yet
        self.assertEqual(first[1000].mem, (120000 - 40000) * 4096)
        try:                                             # the name if this PC has such a user
            me = pwd.getpwuid(ME).pw_name
        except KeyError:
            me = str(ME)
        self.assertEqual((first[1000].user, first[1].uid, first[310].uid), (me, 0, 110))
        self.assertTrue(first[17].kernel and first[2].kernel and not first[1].kernel)
        self.assertEqual(first[17].mem, 0)
        # 2 seconds later Firefox used 1.6 s of CPU on a 4-core PC and wrote 4 MB.
        fs.proc(1000, "firefox-bin", ppid=800, ticks=160, io=(1024 * 1024, 3 * 1024 * 1024),
                cgroup=first[1000].cgroup, resident=120000, shared=40000)
        second = {p.pid: p for p in s.sample(now=102.0)}
        self.assertAlmostEqual(second[1000].cpu, 20.0)
        self.assertAlmostEqual(second[1000].disk, 2 * 1024 * 1024)
        self.assertEqual(second[1001].cpu, 0.0)
        self.assertEqual(second[300].disk, 0.0)          # other users' disk use is not readable

    def test_idle_processes_are_read_less_often(self):
        fs = desktop_system()
        s = sampler(fs)
        s.sample(now=1.0)
        # Dolphin does nothing; its memory file changes behind its back (the kernel moved pages out).
        fs.proc(1100, "dolphin", ppid=800, io=(0, 0), resident=3000, shared=1000,
                cgroup=f"{USER_SLICE}/app.slice/app-org.kde.dolphin@77aa.service")
        seen = [next(p.mem for p in s.sample(now=1.0 + n) if p.pid == 1100) for n in range(1, 7)]
        self.assertEqual(seen[0], 5000 * 4096)                 # not re-read right away...
        self.assertEqual(seen[-1], 2000 * 4096)                # ...but within a few updates
        # As soon as it uses the processor again, everything is read at once.
        fs.proc(1100, "dolphin", ppid=800, ticks=7, io=(4096, 0), resident=9000, shared=1000,
                cgroup=f"{USER_SLICE}/app.slice/app-org.kde.dolphin@77aa.service")
        p = next(q for q in s.sample(now=8.0) if q.pid == 1100)
        self.assertEqual((p.mem, p.disk), (8000 * 4096, 4096.0))

    def test_big_and_missing_files(self):
        big = TMP / "big.txt"
        big.write_text("x" * 200000 + "end")
        self.assertEqual(len(procfs.read_text(big)), 200003)
        self.assertTrue(procfs.read_text(big).endswith("end"))
        self.assertEqual(procfs.read_text(TMP / "no-such-file"), "")
        self.assertEqual(procfs.read_text(TMP), "")            # a folder
        odd = TMP / "odd.bin"
        odd.write_bytes(b"ok \xff\xfe bytes")
        self.assertTrue(procfs.read_text(odd).startswith("ok "))

    def test_full_names(self):
        fs = desktop_system()
        procs = {p.pid: p for p in sampler(fs).sample()}
        self.assertEqual(procs[920].name, "xwaylandvideobridge")     # cut at 15 characters by Linux
        self.assertEqual(procs[1002].name, "Isolated Web Co")        # Firefox names its helpers itself
        self.assertEqual(procs[1001].cmdline, "/usr/lib/firefox/firefox-bin -contentproc -isForBrowser")
        self.assertEqual(procfs.full_name("123456789012345", "/opt/x/123456789012345678 --flag=/a/b"), "123456789012345")

    def test_pid_reused_by_another_program(self):
        fs = desktop_system()
        s = sampler(fs)
        s.sample(now=10.0)
        fs.proc(1100, "kate", ticks=5, start=9999, io=(0, 0))       # same PID, a new process
        p = {q.pid: q for q in s.sample(now=11.0)}[1100]
        self.assertEqual((p.name, p.cpu), ("kate", 0.0))
        fs.remove(1100)
        self.assertNotIn(1100, {q.pid for q in s.sample(now=12.0)})

    def test_status_words(self):
        fs = FakeSystem()
        fs.proc(50, "paused", state="T")
        fs.proc(51, "zombie", state="Z")
        fs.proc(52, "busy", state="R")
        status = {p.name: p.status for p in sampler(fs).sample()}
        self.assertEqual(status, {"paused": "Suspended", "zombie": "Ended", "busy": "Running"})

    def test_process_tree(self):
        procs = sampler(desktop_system()).sample()
        self.assertEqual(procfs.descendants(procs, 1000), [1000, 1001, 1002])
        self.assertEqual(procfs.descendants(procs, 1100), [1100])
        self.assertEqual(set(procfs.descendants(procs, 800)), {800, 900, 910, 920, 1000, 1001, 1002, 1100})

    def test_unreadable_proc_is_empty(self):
        self.assertEqual(procfs.ProcessSampler(TMP / "nowhere", clk_tck=100, page_size=4096, ncpu=1,
                                               uid=ME).sample(), [])


class System(unittest.TestCase):
    def test_cpu_memory_disk_network(self):
        fs = FakeSystem()
        fs.proc(1, "systemd", uid=0)
        fs.disk("nvme0n1", sectors=2 * 1024 * 1024 * 1024 // 512)
        fs.net("enp3s0", speed=1000)
        fs.diskstats(nvme0n1=(1000, 2000, 500))
        fs.netdev(lo=(999, 999), enp3s0=(10000, 5000))
        s = procfs.SystemSampler(fs.root)
        first = s.sample(now=50.0)
        self.assertEqual((first.cpu, first.disks["nvme0n1"]["active"]), (0.0, 0.0))
        fs.cpu(busy=1300, idle=9700)                                 # 300 of 1000 ticks busy
        fs.diskstats(nvme0n1=(1000 + 4096, 2000 + 2048, 500 + 250))  # 2 MB read, 1 MB written, 250 ms busy
        fs.netdev(lo=(1999, 1999), enp3s0=(10000 + 250000, 5000 + 2000))
        snap = s.sample(now=51.0)
        self.assertAlmostEqual(snap.cpu, 30.0)
        self.assertAlmostEqual(snap.disks["nvme0n1"]["active"], 25.0)
        self.assertAlmostEqual(snap.disks["nvme0n1"]["read"], 2 * 1024 * 1024)
        self.assertAlmostEqual(snap.disks["nvme0n1"]["write"], 1024 * 1024)
        self.assertEqual(snap.nets, {"enp3s0": {"down": 250000.0, "up": 2000.0}})
        self.assertEqual(snap.memory["total"], 16 * 1024 ** 3)
        self.assertEqual(snap.memory["used"], 6 * 1024 ** 3)
        self.assertEqual(snap.memory["cached"], (2 * 1024 * 1024 + 100000 + 50000) * 1024)
        self.assertEqual((snap.memory["swap_total"], snap.memory["swap_used"]), (0, 0))
        self.assertEqual((snap.uptime, snap.threads, snap.processes), (19027.5, 612, 1))

    def test_counter_reset_does_not_go_negative(self):
        fs = FakeSystem()
        fs.disk("sda")
        fs.diskstats(sda=(5000, 5000, 900))
        s = procfs.SystemSampler(fs.root)
        s.sample(now=1.0)
        fs.diskstats(sda=(10, 10, 5))
        self.assertEqual(s.sample(now=2.0).disks["sda"], {"active": 0.0, "read": 0.0, "write": 0.0})

    def test_compressed_memory_and_pressure(self):
        fs = FakeSystem()
        self.assertEqual(procfs.read_compressed(fs.root), (0, 0))
        self.assertEqual(procfs.read_memory_pressure(fs.root), 0.0)          # no such file: unknown
        # zswap reports in /proc/meminfo, a compressed swap in RAM in /sys/block/zramN/mm_stat
        with open(fs.root / "proc/meminfo", "a") as f:
            f.write("Zswap:            204800 kB\nZswapped:         614400 kB\n")
        write(fs.root / "sys/block/zram0/mm_stat", "1073741824 300000000 314572800 0 400000000 5 0 0 0\n")
        write(fs.root / "sys/block/zram0/size", "1000\n")
        self.assertEqual(procfs.read_compressed(fs.root), (614400 * 1024 + 1073741824, 204800 * 1024 + 314572800))
        self.assertEqual(procfs.list_disks(fs.root), [])                     # zram is not a drive
        write(fs.root / "proc/pressure/memory",
              "some avg10=23.45 avg60=10.00 avg300=2.00 total=123456\nfull avg10=11.00 avg60=4.00 avg300=1.00 total=6543\n")
        self.assertEqual(procfs.read_memory_pressure(fs.root), 23.45)
        snap = procfs.SystemSampler(fs.root).sample(now=1.0)
        self.assertEqual((snap.memory["compressed_takes"], snap.memory["pressure"]),
                         (204800 * 1024 + 314572800, 23.45))
        self.assertEqual([formats.pressure(p) for p in (0, 9.9, 23.45, 61.2)],
                         ["Normal", "Normal", "High (23%)", "Very high (61%)"])

    def test_swap(self):
        fs = FakeSystem()
        fs.meminfo(total=8000000, available=2000000, swap_total=4000000, swap_free=3000000)
        mem = procfs.memory_summary(procfs.read_meminfo(fs.root))
        self.assertEqual((mem["swap_total"], mem["swap_used"]), (4000000 * 1024, 1000000 * 1024))

    def test_which_disks_count(self):
        fs = FakeSystem()
        fs.disk("nvme0n1", model="Samsung SSD 990 PRO 1TB")
        fs.disk("sda", rotational="1", model="WDC WD40EZAZ", vendor="ATA")
        fs.disk("sdb", sectors=0)                    # empty card reader
        fs.disk("vda", rotational="1")
        for skipped in ("loop0", "zram0", "dm-0", "sr0", "ram0"):
            fs.disk(skipped)
        self.assertEqual(procfs.list_disks(fs.root), ["nvme0n1", "sda", "vda"])
        nvme = procfs.disk_info(fs.root, "nvme0n1")
        self.assertEqual((nvme["kind"], nvme["model"], nvme["size"]),
                         ("SSD (NVMe)", "Samsung SSD 990 PRO 1TB", 512000000))
        hdd = procfs.disk_info(fs.root, "sda")
        self.assertEqual((hdd["kind"], hdd["model"]), ("Hard disk (HDD)", "WDC WD40EZAZ"))
        write(fs.root / "sys/block/vda/device/vendor", "0x1af4\n")    # a number, not a maker's name
        virtual = procfs.disk_info(fs.root, "vda")
        self.assertEqual((virtual["kind"], virtual["model"]), ("Virtual disk", ""))
        fs.disk("sdc", model="Ultra Fit", vendor="SanDisk")
        self.assertEqual(procfs.disk_info(fs.root, "sdc")["model"], "SanDisk Ultra Fit")

    def test_which_network_adapters_count(self):
        fs = FakeSystem()
        fs.net("lo", physical=False)
        fs.net("docker0", physical=False)
        fs.net("enp3s0", speed=2500)
        fs.net("wlp2s0", wifi=True, speed=-1)
        self.assertEqual(procfs.list_nets(fs.root), [("enp3s0", "Ethernet"), ("wlp2s0", "Wi-Fi")])
        write(fs.root / "proc/net/wireless",
              "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
              " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
              "wlp2s0: 0000   49.  -61.  -256        0      0      0      0     18        0\n")
        write(fs.root / "proc/net/if_inet6",
              "fe800000000000009c62cdfffef24f0c 02 40 20 80   wlp2s0\n"
              "2a0104f8c0c0abcd0000000000000007 02 40 00 00   wlp2s0\n"
              "fe80000000000000aaaaaaaaaaaaaaaa 03 40 20 80   enp3s0\n")
        wifi = procfs.net_info(fs.root, "wlp2s0")
        self.assertEqual((wifi["signal"], wifi["speed_mbit"], wifi["connected"]), (70, 0, True))
        self.assertEqual(wifi["ipv6"], "2a01:4f8:c0c0:abcd::7")
        wired = procfs.net_info(fs.root, "enp3s0")
        self.assertEqual((wired["signal"], wired["speed_mbit"], wired["ipv6"]),
                         (-1, 2500, "fe80::aaaa:aaaa:aaaa:aaaa"))

    def test_processor_facts(self):
        fs = FakeSystem()
        blocks = []
        for n in range(8):
            blocks.append(f"processor\t: {n}\nmodel name\t: AMD Ryzen 7 7800X3D 8-Core Processor\n"
                          f"cpu MHz\t\t: {4000 + n * 100}.000\nphysical id\t: 0\ncore id\t\t: {n // 2}\n")
        write(fs.root / "proc/cpuinfo", "\n".join(blocks) + "\n")
        info = procfs.cpu_info(fs.root)
        self.assertEqual((info["model"], info["logical"], info["cores"], info["sockets"]),
                         ("AMD Ryzen 7 7800X3D 8-Core Processor", 8, 4, 1))
        self.assertAlmostEqual(procfs.cpu_speed(fs.root), 4350.0)
        write(fs.root / "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "3000000\n")
        write(fs.root / "sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq", "5000000\n")
        self.assertAlmostEqual(procfs.cpu_speed(fs.root), 4000.0)    # the live value wins
        arm = FakeSystem()                                            # ARM: no names, no speed
        write(arm.root / "proc/cpuinfo", "processor\t: 0\nBogoMIPS\t: 48.00\n\nprocessor\t: 1\nBogoMIPS\t: 48.00\n\n")
        info = procfs.cpu_info(arm.root)
        self.assertEqual((info["model"], info["logical"], info["cores"]), ("", 2, 0))
        self.assertEqual(procfs.cpu_speed(arm.root), 0.0)

    def test_graphics_cards(self):
        fs = FakeSystem()
        card = fs.root / "sys/class/drm/card1/device"
        write(card / "gpu_busy_percent", "37\n")
        write(card / "mem_info_vram_used", f"{3 * 1024 ** 3}\n")
        write(card / "mem_info_vram_total", f"{16 * 1024 ** 3}\n")
        (fs.root / "sys/class/drm/card1-DP-1").mkdir()
        (fs.root / "sys/class/drm/card0/device").mkdir(parents=True)      # a card without load info
        gpus = procfs.read_amd_gpus(fs.root)
        self.assertEqual(len(gpus), 1)
        self.assertEqual((gpus[0]["id"], gpus[0]["busy"], gpus[0]["mem_total"]), ("card1", 37, 16 * 1024 ** 3))
        nv = procfs.parse_nvidia_line("0, NVIDIA GeForce RTX 4070, 12, 2048, 12282, 45\n")
        self.assertEqual((nv["id"], nv["name"], nv["busy"], nv["temp"]),
                         ("nvidia0", "NVIDIA GeForce RTX 4070", 12, 45))
        self.assertEqual(nv["mem_used"], 2048 * 1024 * 1024)
        odd = procfs.parse_nvidia_line("1, Tesla T4, [N/A], [N/A], 15360, [N/A]")
        self.assertEqual((odd["name"], odd["busy"], odd["mem_used"], odd["temp"]), ("NVIDIA Tesla T4", 0, 0, None))
        self.assertIsNone(procfs.parse_nvidia_line("NVIDIA-SMI has failed because it couldn't communicate"))
        self.assertEqual(procfs.short_gpu_name("Advanced Micro Devices, Inc. [AMD/ATI]",
                                               "Navi 23 [Radeon RX 6600/6600 XT/6600M]"),
                         "AMD Radeon RX 6600/6600 XT/6600M")

    def test_nvidia_monitor_reads_a_running_nvidia_smi(self):
        fake_program("nvidia-smi", 'echo "0, GeForce RTX 3060, 55, 1000, 12288, 61"\nsleep 30\n')
        mon = procfs.NvidiaMonitor()
        try:
            deadline = time.time() + 5
            while not mon.latest() and time.time() < deadline:
                time.sleep(0.05)
            got = mon.latest()
            self.assertEqual([(g["name"], g["busy"]) for g in got], [("NVIDIA GeForce RTX 3060", 55)])
        finally:
            mon.stop()
            (BIN / "nvidia-smi").unlink()
        self.assertEqual(procfs.NvidiaMonitor(exe="no-such-nvidia-smi").latest(), [])


class Apps(unittest.TestCase):
    def test_app_unit_names(self):
        parse = apps.parse_app_unit
        self.assertEqual(parse("app-firefox@2afeee41b65c.service"), ("firefox", "2afeee41b65c"))
        self.assertEqual(parse("app-org.kde.xwaylandvideobridge@autostart.service"),
                         ("org.kde.xwaylandvideobridge", "autostart"))
        self.assertEqual(parse("app-kup\\x2ddaemon@autostart.service"), ("kup-daemon", "autostart"))
        self.assertEqual(parse("app-deepseek\\x2dshell@9f.service"), ("deepseek-shell", "9f"))
        self.assertEqual(parse("app-flatpak-org.mozilla.firefox-12345.scope"), ("org.mozilla.firefox", "12345"))
        self.assertEqual(parse("app-org.kde.dolphin-abc123.scope"), ("org.kde.dolphin", "abc123"))
        for other in ("plasma-kded6.service", "session-1.scope", "dbus-:1.2-org.x@0.service", "app-.service", ""):
            self.assertIsNone(parse(other), other)
        self.assertEqual(apps.unit_of("/a/b/app-x@1.service"), "app-x@1.service")

    def test_groups(self):
        procs = sampler(desktop_system()).sample()
        rows = {r.key: r for r in apps.build_rows(procs, ME, apps.DesktopIndex())}
        by_section = {}
        for r in rows.values():
            by_section.setdefault(r.section, []).append(r.name)
        self.assertEqual(sorted(by_section["app"]), ["File Explorer", "Firefox"])     # our name wins over "Dolphin"
        self.assertIn("Desktop, taskbar and Start menu (plasmashell)", by_section["background"])
        self.assertIn("Screen sharing for older apps (xwaylandvideobridge)", by_section["background"])
        self.assertIn("Virus scanner (clamd)", by_section["system"])
        self.assertIn("Linux kernel", by_section["system"])
        self.assertEqual(len(by_section["system"]), 4)        # systemd, NetworkManager, clamd, the kernel
        fox = next(r for r in rows.values() if r.name == "Firefox")
        self.assertEqual([c.name for c in fox.children], ["firefox-bin", "Web Content", "Isolated Web Co"])
        self.assertEqual(fox.mem, sum(c.mem for c in fox.children))
        self.assertEqual(len(fox.targets), 3)
        explorer = next(r for r in rows.values() if r.name == "File Explorer")
        self.assertEqual(explorer.children, [])               # one process: nothing to unfold
        self.assertEqual(rows["kernel"].mem, 0)

    def test_windows_program_is_named_after_its_exe(self):
        fs = FakeSystem()
        unit = f"{USER_SLICE}/app.slice/app-myos\\x2dexe\\x2drunner@5c.service"
        fs.proc(2000, "python3", cgroup=unit, cmdline=["python3", "-m", "myos_security", "run-exe"])
        fs.proc(2001, "wineserver", cgroup=unit)
        fs.proc(2002, "services.exe", cgroup=unit)          # Wine's own helpers come first
        fs.proc(2003, "explorer.exe", cgroup=unit)
        fs.proc(2004, "Notepad++.exe", cgroup=unit)
        rows = apps.build_rows(sampler(fs).sample(), ME, apps.DesktopIndex())
        self.assertEqual([r.name for r in rows], ["Notepad++.exe (Windows program)"])
        self.assertIn("Windows program support (wineserver)", [c.name for c in rows[0].children])
        login = procfs.Proc(7, 1, "(sd-pam)", "S", ME, "me", False, 1, 1, 0, 0)
        self.assertEqual(apps.display_name(login), "Login session (sd-pam)")

    def test_app_without_desktop_file_is_a_background_process(self):
        fs = FakeSystem()
        fs.proc(3000, "mytool", cgroup=f"{USER_SLICE}/app.slice/app-mytool@1.service")
        rows = apps.build_rows(sampler(fs).sample(), ME, apps.DesktopIndex())
        self.assertEqual([(r.section, r.name) for r in rows], [("background", "mytool")])

    def test_what_may_be_ended(self):
        procs = {p.pid: p for p in sampler(desktop_system()).sample()}
        kind = {pid: apps.protection(p, ME) for pid, p in procs.items()}
        self.assertEqual(kind[1], "never")             # the system itself
        self.assertEqual(kind[17], "never")            # kernel thread
        self.assertEqual(kind[300], "system")          # needs the administrator
        self.assertEqual(kind[910], "restart")         # the desktop is restarted, not ended
        self.assertEqual(kind[900], "session")         # ends the session
        self.assertEqual(kind[800], "session")
        self.assertEqual(kind[1000], "")
        self.assertEqual(kind[920], "")

    def test_localized_names(self):
        entry = desktopfile.load(APPS / "org.kde.dolphin.desktop")
        old = os.environ.get("LANG")
        try:
            os.environ["LANG"] = "de_DE.UTF-8"
            self.assertEqual(desktopfile.localized(entry, "Name"), "Delfin")
            os.environ["LANG"] = "fr_FR.UTF-8"
            self.assertEqual(desktopfile.localized(entry, "Name"), "Dolphin")
        finally:
            if old is None:
                os.environ.pop("LANG", None)
            else:
                os.environ["LANG"] = old


class Startup(unittest.TestCase):
    def setUp(self):
        import shutil
        self.system = Path(os.environ["XDG_CONFIG_DIRS"]) / "autostart"
        self.user = Path(os.environ["XDG_CONFIG_HOME"]) / "autostart"
        for d in (self.system, self.user):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
        fake_program("discord", "exit 0\n")

    def names(self):
        return {e.name: e for e in startup.list_entries()}

    def test_only_real_startup_apps_are_listed(self):
        desktop_entry(self.system, "org.kde.plasmashell.desktop", "Plasma Desktop",
                      "NoDisplay=true\nOnlyShowIn=KDE;\nX-KDE-autostart-phase=0\n")
        desktop_entry(self.system, "xembedsniproxy.desktop", "XEmbed", "X-systemd-skip=true\n")
        desktop_entry(self.system, "gnome-keyring-ssh.desktop", "SSH Key Agent", "OnlyShowIn=GNOME;Unity;MATE;\n")
        desktop_entry(self.system, "nokde.desktop", "Not for KDE", "NotShowIn=KDE;\n")
        desktop_entry(self.system, "missing.desktop", "Not installed", "TryExec=no-such-program-xyz\n")
        desktop_entry(self.system, "kup-daemon.desktop", "Kup Daemon",
                      "X-KDE-autostart-condition=kuprc:Kup settings:Backups enabled:false\n")
        desktop_entry(self.system, "updater.desktop", "Update Checker", exec_line="updater --tray %u")
        desktop_entry(self.user, "discord.desktop", "Discord", "TryExec=discord\n", "discord --start-minimized")
        write(self.user / "notes.txt", "not a desktop file")
        entries = self.names()
        self.assertEqual(sorted(entries), ["Discord", "Update Checker"])
        self.assertEqual((entries["Discord"].source, entries["Discord"].status), ("You", "Enabled"))
        self.assertEqual((entries["Update Checker"].source, entries["Update Checker"].command),
                         ("System", "updater --tray"))
        # the backup tool appears once its own switch is on
        write(Path(os.environ["XDG_CONFIG_HOME"]) / "kuprc", "[Kup settings]\nBackups enabled=true\n")
        self.assertIn("Kup Daemon", self.names())
        (Path(os.environ["XDG_CONFIG_HOME"]) / "kuprc").unlink()

    def test_switch_a_system_entry_off_and_on(self):
        original = desktop_entry(self.system, "updater.desktop", "Update Checker").read_text()
        entry = self.names()["Update Checker"]
        startup.set_enabled(entry, False)
        mine = self.user / "updater.desktop"
        self.assertTrue(desktopfile.is_true(desktopfile.load(mine), "Hidden"))
        self.assertEqual(desktopfile.load(mine)["Name"], "Update Checker")
        self.assertEqual(self.names()["Update Checker"].status, "Disabled")
        self.assertEqual((self.system / "updater.desktop").read_text(), original)    # never touched
        startup.set_enabled(self.names()["Update Checker"], True)
        self.assertFalse(mine.exists())
        self.assertEqual(self.names()["Update Checker"].status, "Enabled")

    def test_switch_your_own_entry_off_and_on(self):
        path = desktop_entry(self.user, "discord.desktop", "Discord",
                             "X-Custom=keep me\n\n[Desktop Action Quit]\nName=Quit\nExec=discord --quit\n")
        before = path.read_text()
        startup.set_enabled(self.names()["Discord"], False)
        self.assertEqual(self.names()["Discord"].status, "Disabled")
        self.assertIn("[Desktop Action Quit]\nName=Quit", path.read_text())
        startup.set_enabled(self.names()["Discord"], True)
        self.assertEqual(self.names()["Discord"].status, "Enabled")
        self.assertEqual(path.read_text(), before)

    def test_your_changed_copy_of_a_system_entry_is_kept(self):
        desktop_entry(self.system, "updater.desktop", "Update Checker")
        desktop_entry(self.user, "updater.desktop", "Update Checker", "Hidden=true\n", "updater --quiet")
        entry = self.names()["Update Checker"]
        self.assertEqual((entry.status, entry.command), ("Disabled", "updater --quiet"))
        startup.set_enabled(entry, True)
        self.assertTrue((self.user / "updater.desktop").exists())
        self.assertEqual(self.names()["Update Checker"].command, "updater --quiet")
        self.assertEqual(self.names()["Update Checker"].status, "Enabled")

    def test_desktop_file_editing(self):
        text = "# comment\n[Desktop Entry]\nName=X\nHidden=false\n\n[Other]\nHidden=keep\n"
        out = desktopfile.set_keys(text, {"Hidden": "true"})
        self.assertEqual(out, "# comment\n[Desktop Entry]\nHidden=true\nName=X\n\n[Other]\nHidden=keep\n")
        self.assertEqual(desktopfile.set_keys(out, {"Hidden": None}),
                         "# comment\n[Desktop Entry]\nName=X\n\n[Other]\nHidden=keep\n")
        self.assertEqual(desktopfile.set_keys("Name=odd\n", {"Hidden": "true"}),
                         "[Desktop Entry]\nHidden=true\nName=odd\n")
        self.assertEqual(desktopfile.clean_exec("app %U --name=%c 100%% %f"), "app --name= 100%")
        self.assertEqual(desktopfile.command_name("env A=1 B=2 /usr/bin/steam -silent"), "steam")


SHOW_OUTPUT = """Id=ollama.service
Description=Ollama, the local AI engine used by DeepSeek Shell
LoadState=loaded
ActiveState=active
SubState=running
MainPID=802
UnitFileState=enabled

Id=myos-firstboot.service
Description=First boot setup (system snapshots, installer cleanup)
LoadState=loaded
ActiveState=active
SubState=exited
MainPID=0
UnitFileState=enabled

Id=clamav-daemon.service
Description=Clam AntiVirus userspace daemon
LoadState=loaded
ActiveState=inactive
SubState=dead
MainPID=0
UnitFileState=disabled

Id=broken.service
Description=Something = odd
LoadState=loaded
ActiveState=failed
SubState=failed
MainPID=0
UnitFileState=static

Id=dbus.service
Description=D-Bus System Message Bus
LoadState=loaded
ActiveState=active
SubState=running
MainPID=700
UnitFileState=static

Id=ghost.service
Description=ghost.service
LoadState=not-found
ActiveState=inactive
SubState=dead
MainPID=0
UnitFileState=
"""


class Services(unittest.TestCase):
    def setUp(self):
        write(TMP / "show.txt", SHOW_OUTPUT)
        write(TMP / "files.txt", "ollama.service enabled enabled\nssh.service disabled enabled\n"
                                 "getty@.service enabled enabled\ncups.service masked enabled\n"
                                 "dbus-org.bluez.service alias -\nrescue.service static -\n")
        write(TMP / "idle.txt", "Id=ssh.service\nDescription=OpenBSD Secure Shell server\nLoadState=loaded\n"
                                "ActiveState=inactive\nSubState=dead\nMainPID=0\nUnitFileState=disabled\n\n"
                                "Id=cups.service\nDescription=cups.service\nLoadState=masked\n"
                                "ActiveState=inactive\nSubState=dead\nMainPID=0\nUnitFileState=masked\n")
        fake_program("systemctl", f'''case "$1" in
  show) if [ "$2" = "*.service" ]; then cat "{TMP}/show.txt"; else echo "$@" > "{TMP}/show.args"; cat "{TMP}/idle.txt"; fi ;;
  list-unit-files) cat "{TMP}/files.txt" ;;
  start) echo "started $2" >> "{TMP}/systemctl.log" ;;
  stop) echo "Failed to stop $2: Access denied" >&2; exit 4 ;;
  restart) echo "Job for $2 failed because the control process exited with error code." >&2; exit 1 ;;
esac
''')

    def test_list(self):
        found = {s.name: s for s in services.list_services()}
        self.assertEqual(sorted(found), ["broken", "clamav-daemon", "cups", "dbus", "myos-firstboot", "ollama",
                                         "rescue", "ssh"])
        self.assertEqual((found["ollama"].status, found["ollama"].pid, found["ollama"].startup),
                         ("Running", 802, "Automatic"))
        self.assertEqual(found["myos-firstboot"].status, "Finished")
        self.assertEqual((found["clamav-daemon"].status, found["clamav-daemon"].startup), ("Stopped", "Manual"))
        self.assertEqual((found["broken"].status, found["broken"].description), ("Failed", "Something = odd"))
        # not used since the PC started: described by a second question to systemd
        self.assertEqual((found["ssh"].status, found["ssh"].startup, found["ssh"].description),
                         ("Stopped", "Manual", "OpenBSD Secure Shell server"))
        self.assertEqual((found["cups"].startup, found["cups"].description), ("Blocked", ""))
        self.assertEqual((found["rescue"].status, found["rescue"].startup), ("Stopped", "When needed"))
        self.assertIn("ssh.service cups.service rescue.service", (TMP / "show.args").read_text())
        self.assertTrue(found["dbus"].protected and not found["ollama"].protected)
        self.assertTrue(services.Service("user@1000.service", "", "Running", 5, "").protected)

    def test_control(self):
        self.assertEqual(services.control("ollama.service", "start"), (True, ""))
        self.assertIn("started ollama.service", (TMP / "systemctl.log").read_text())
        ok, message = services.control("ollama.service", "stop")
        self.assertEqual((ok, message), (False, "It was not allowed (no administrator password was given)."))
        ok, message = services.control("ollama.service", "restart")
        self.assertFalse(ok)
        self.assertIn("control process exited", message)
        with self.assertRaises(ValueError):
            services.control("ollama.service", "mask")


class EndingProcesses(unittest.TestCase):
    def test_helper_refuses_bad_requests(self):
        def run(*args, proc=None):
            env = dict(os.environ)
            if proc:
                env["MYOS_TASKMANAGER_PROC"] = str(proc)
            return subprocess.run(["sh", str(HELPER), *args], capture_output=True, text=True, env=env)
        self.assertEqual(run().returncode, 2)
        for bad in ("abc", "12", "12:", ":5", "1:2:3", "12:5;reboot", "-1:5", "12:5 13:6"):
            self.assertEqual(run(bad).returncode, 2, bad)
        self.assertEqual(run("1:100").returncode, 3)
        self.assertIn("needed by the system", run("1:100").stderr)
        fs = desktop_system()
        r = run("17:5000", proc=fs.root / "proc")              # a kernel thread
        self.assertEqual(r.returncode, 3)
        self.assertIn("Linux kernel", r.stderr)
        self.assertEqual(run("1100:4444", proc=fs.root / "proc").returncode, 0)   # other start time: gone
        self.assertEqual(run("99999:1", proc=fs.root / "proc").returncode, 0)

    @unittest.skipUnless(LINUX, "needs a real /proc")
    def test_end_a_program(self):
        child = subprocess.Popen(["sleep", "60"])
        start = actions.start_of(child.pid)
        self.assertGreater(start, 0)
        began = time.monotonic()
        self.assertEqual(actions.end_own([(child.pid, start)]), [])
        self.assertLess(time.monotonic() - began, 2.5)        # it quit when asked
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)
        self.assertEqual(actions.start_of(child.pid), -1)

    @unittest.skipUnless(LINUX, "needs a real /proc")
    def test_a_program_that_ignores_the_request_is_stopped_by_force(self):
        child = subprocess.Popen([sys.executable, "-c",
                                  "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                  "print('ready', flush=True)\ntime.sleep(60)"], stdout=subprocess.PIPE)
        child.stdout.readline()
        start = actions.start_of(child.pid)
        self.assertEqual(actions.end_own([(child.pid, start)], grace=0.5), [])
        self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)

    @unittest.skipUnless(LINUX, "needs a real /proc")
    def test_a_reused_pid_is_left_alone(self):
        child = subprocess.Popen(["sleep", "60"])
        try:
            self.assertEqual(actions.end_own([(child.pid, actions.start_of(child.pid) + 1)]), [])
            self.assertIsNone(child.poll())                    # still running
        finally:
            child.kill()
            child.wait()

    @unittest.skipUnless(LINUX, "needs a real /proc")
    def test_helper_ends_a_program(self):
        child = subprocess.Popen(["sleep", "60"])
        r = subprocess.run(["sh", str(HELPER), f"{child.pid}:{actions.start_of(child.pid)}"],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)

    def test_admin_helper_messages(self):
        fake_program("pkexec", 'echo "Error executing command as another user: Not authorized" >&2\nexit 126\n')
        self.assertEqual(actions.end_as_admin([(300, 5000)]),
                         (False, "It was not allowed (no administrator password was given)."))
        fake_program("pkexec", 'shift\necho "$@" > "%s/pkexec.args"\nexit 0\n' % TMP)
        self.assertEqual(actions.end_as_admin([(300, 5000), (310, 5001)]), (True, ""))
        self.assertEqual((TMP / "pkexec.args").read_text().strip(), "300:5000 310:5001")
        fake_program("pkexec", 'echo "end-process: process 300 could not be ended." >&2\nexit 1\n')
        self.assertEqual(actions.end_as_admin([(300, 5000)]),
                         (False, "end-process: process 300 could not be ended."))
        self.assertEqual(actions.end_as_admin([]), (True, ""))
        (BIN / "pkexec").unlink()


# ── the window ───────────────────────────────────────────────────────────────
try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from myos_taskmanager import app as tm
    from myos_taskmanager.widgets import KeyedTree, Spec
    QT = True
except ImportError:
    QT = False


def texts(tree, parent=None, col=0):
    root = parent or tree.invisibleRootItem()
    return [root.child(i).text(col) for i in range(root.childCount())]


@unittest.skipUnless(QT, "needs PyQt6")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def make(self, fs):
        fs.disk("nvme0n1", model="Samsung SSD 990 PRO 1TB")
        fs.net("enp3s0", speed=1000)
        fs.diskstats(nvme0n1=(0, 0, 0))
        fs.netdev(enp3s0=(0, 0))
        win = tm.TaskManager(root=fs.root, sample=False)
        win.me = ME
        self.addCleanup(win.close)
        win.system = procfs.SystemSampler(fs.root)
        win.sampling = sampler(fs)
        return win

    def tick(self, win, now):
        win._sampled(win.system.sample(now=now), win.sampling.sample(now=now))

    def test_processes_tab(self):
        fs = desktop_system()
        win = self.make(fs)
        win.go("processes")
        self.tick(win, 10.0)
        fs.cpu(busy=1500, idle=9500)
        fs.proc(1001, "Web Content", ppid=1000, ticks=200, io=(0, 0),
                cgroup=f"{USER_SLICE}/app.slice/app-firefox@2afeee41b65c4f7cae0cb14a5ec72be4.service")
        self.tick(win, 11.0)
        tree = win.processes.tree
        self.assertEqual(texts(tree), ["Apps (2)", "Background processes (4)", "System processes (4)"])
        apps_item = tree.topLevelItem(0)
        self.assertEqual(texts(tree, apps_item), ["File Explorer", "Firefox"])
        fox = apps_item.child(1)
        self.assertEqual(fox.text(tm.C_CPU), "50.0%")                    # 2 s of CPU in 1 s on 4 cores
        self.assertEqual(sorted(texts(tree, fox)), ["Isolated Web Co", "Web Content", "firefox-bin"])
        totals = tree.header().totals                                     # drawn above the column names
        self.assertEqual((totals[tm.C_CPU], totals[tm.C_MEM], totals[tm.C_DISK]), ("50%", "38%", "0%"))
        self.assertEqual(tree.headerItem().text(tm.C_CPU).strip(), "CPU")
        system = texts(tree, tree.topLevelItem(2))
        self.assertEqual(system[0], "Linux kernel")
        # sorted by CPU the list is flat, biggest first
        tree.sortByColumn(tm.C_CPU, Qt.SortOrder.DescendingOrder)
        flat = texts(tree)
        self.assertEqual(flat[0], "Firefox")
        self.assertNotIn("Apps (2)", flat)
        self.assertEqual(len(flat), 10)
        self.assertEqual(flat[1:3], ["Desktop, taskbar and Start menu (plasmashell)", "File Explorer"])   # ties: A to Z
        tree.sortByColumn(tm.C_NAME, Qt.SortOrder.AscendingOrder)
        self.assertEqual(texts(tree), ["Apps (2)", "Background processes (4)", "System processes (4)"])

    def test_selection_and_open_groups_survive_updates(self):
        fs = desktop_system()
        win = self.make(fs)
        win.go("processes")
        self.tick(win, 10.0)
        tree = win.processes.tree
        fox_key = next(k for k, r in win.processes.rows.items() if r.name == "Firefox")
        tree.item_for(fox_key).setExpanded(True)
        tree.select(fox_key)
        for now in (11.0, 12.0):
            self.tick(win, now)
        self.assertEqual(tree.current_key(), fox_key)
        self.assertTrue(tree.item_for(fox_key).isExpanded())
        tree.sortByColumn(tm.C_MEM, Qt.SortOrder.DescendingOrder)          # regrouping keeps both too
        self.assertEqual(tree.current_key(), fox_key)
        self.assertTrue(tree.item_for(fox_key).isExpanded())
        fs.remove(1002)                                                    # a tab's process ends
        self.tick(win, 13.0)
        self.assertEqual(len(texts(tree, tree.item_for(fox_key))), 2)
        for pid in (1000, 1001):                                           # Firefox closes
            fs.remove(pid)
        self.tick(win, 14.0)
        self.assertIsNone(tree.item_for(fox_key))
        self.assertNotEqual(tree.current_key(), fox_key)

    def test_end_task_button(self):
        fs = desktop_system()
        win = self.make(fs)
        win.go("processes")
        self.tick(win, 10.0)
        rows = {r.name: k for k, r in win.processes.rows.items()}
        tree, button = win.processes.tree, win.processes.end_btn
        self.assertFalse(button.isEnabled())
        tree.select(rows["Firefox"])
        self.assertEqual((button.text(), button.isEnabled()), ("End task", True))
        tree.select(rows["Desktop, taskbar and Start menu (plasmashell)"])
        self.assertEqual((button.text(), button.isEnabled()), ("Restart", True))
        tree.select("kernel")
        self.assertFalse(button.isEnabled())
        tree.select(rows["System and service manager (systemd)"])           # may be PID 1 or the user's
        self.assertEqual(win.danger(win.processes.rows["pid:1:5000"].procs), "never")
        self.assertEqual(win.danger(win.processes.rows[rows["Network (NetworkManager)"]].procs), "system")
        self.assertEqual(win.danger(win.processes.rows[rows["Window manager (kwin_wayland)"]].procs), "session")

    def test_details_tab(self):
        fs = desktop_system()
        win = self.make(fs)
        win.go("details")
        self.tick(win, 10.0)
        tree = win.details.tree
        names = texts(tree)
        self.assertEqual(len(names), 12)                                   # 11 programs + the kernel
        self.assertEqual(names, sorted(names, key=str.lower))
        tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.assertEqual(texts(tree, col=1)[:3], ["1100", "1002", "1001"])   # numbers, not text
        win.show_in_details(win.procs[0])
        self.assertEqual(tree.current_key(), f"pid:{win.procs[0].pid}:{win.procs[0].start}")
        self.assertEqual(win.details.current().pid, win.procs[0].pid)

    def test_performance_tab(self):
        fs = desktop_system()
        win = self.make(fs)
        win.go("performance")
        self.tick(win, 10.0)
        fs.cpu(busy=1250, idle=9750)
        fs.diskstats(nvme0n1=(2048, 0, 100))
        fs.netdev(enp3s0=(125000, 2500))
        self.tick(win, 11.0)
        perf = win.performance
        self.assertEqual(perf.order, ["cpu", "mem", "disk:nvme0n1", "net:enp3s0"])
        self.assertEqual([perf.entries[r].title.text() for r in perf.order],
                         ["CPU", "Memory", "Disk 0 (nvme0n1)", "Ethernet"])
        self.assertEqual(perf.entries["cpu"].sub.text(), "25%")
        self.assertEqual(perf.entries["mem"].sub.text(), "6.0/16.0 GB (38%)")
        self.assertEqual(perf.entries["disk:nvme0n1"].sub.text(), "SSD  10%")
        self.assertEqual(perf.entries["net:enp3s0"].sub.text(), "S: 20.0 Kbps  R: 1.0 Mbps")
        self.assertEqual(list(perf.hist["cpu"]["use"]), [0.0, 25.0])
        cpu = perf.pages["cpu"]
        self.assertEqual((cpu.values["Utilization"].text(), cpu.values["Up time"].text(),
                          cpu.values["Processes"].text()), ("25%", "0:05:17:07", "13"))
        self.assertFalse(cpu.values["Speed"].isVisibleTo(cpu))             # unknown here: not shown
        perf.list.setCurrentRow(2)
        disk = perf.pages["disk:nvme0n1"]
        self.assertEqual((disk.values["Read speed"].text(), disk.values["Type"].text(), disk.subtitle.text()),
                         ("1.0 MB/s", "SSD (NVMe)", "Samsung SSD 990 PRO 1TB"))
        self.assertEqual(disk.scales[1].text(), "1.0 MB/s")
        perf.list.setCurrentRow(3)
        net = perf.pages["net:enp3s0"]
        self.assertEqual((net.values["Receive"].text(), net.values["Link speed"].text(), net.scales[0].text()),
                         ("1.0 Mbps", "1 Gbps", "1.0 Mbps"))
        # a USB drive is plugged in: it gets its own entry, the others keep their history
        fs.disk("sdb", model="SanDisk Ultra")
        fs.diskstats(nvme0n1=(2048, 0, 100), sdb=(0, 0, 0))
        self.tick(win, 12.0)
        self.assertEqual(perf.order, ["cpu", "mem", "disk:nvme0n1", "disk:sdb", "net:enp3s0"])
        self.assertEqual(perf.entries["disk:sdb"].title.text(), "Disk 1 (sdb)")
        self.assertEqual(len(perf.hist["cpu"]["use"]), 3)
        self.assertEqual(perf.order[perf.list.currentRow()], "net:enp3s0")  # still the selected one
        # the Memory page also says how much is kept compressed and how hard memory is pressed
        memory = perf.pages["mem"]
        perf.list.setCurrentRow(1)
        self.assertEqual(memory.values["Pressure"].text(), "Normal")
        self.assertFalse(memory.values["Compressed"].isVisibleTo(memory))   # nothing compressed yet
        with open(fs.root / "proc/meminfo", "a") as f:
            f.write("Zswap:            512000 kB\nZswapped:        1843200 kB\n")
        write(fs.root / "proc/pressure/memory", "some avg10=45.00 avg60=1.00 avg300=0.00 total=1\n")
        self.tick(win, 13.0)
        self.assertEqual((memory.values["Compressed"].text(), memory.values["Pressure"].text()),
                         ("1.8 GB (takes 500 MB)", "Very high (45%)"))

    def test_history_is_kept_while_another_tab_is_open(self):
        fs = desktop_system()
        win = self.make(fs)
        win.go("processes")
        for now in (10.0, 11.0, 12.0):
            self.tick(win, now)
        self.assertEqual(len(win.performance.hist["cpu"]["use"]), 3)
        win.go("performance")
        self.assertEqual(len(win.performance.pages["cpu"].graphs[0].series[0]), 3)

    def test_gpu_page(self):
        fs = desktop_system()
        win = self.make(fs)
        win.go("performance")
        snap = win.system.sample(now=1.0)
        snap.gpus = [{"id": "nvidia0", "name": "NVIDIA GeForce RTX 4070", "busy": 64,
                      "mem_used": 6 * 1024 ** 3, "mem_total": 12 * 1024 ** 3, "temp": 58}]
        win._sampled(snap, None)
        perf = win.performance
        self.assertEqual(perf.order[-1], "gpu:nvidia0")
        self.assertEqual(perf.entries["gpu:nvidia0"].sub.text(), "64% (58 °C)")
        perf.list.setCurrentRow(len(perf.order) - 1)
        page = perf.pages["gpu:nvidia0"]
        self.assertEqual((page.subtitle.text(), page.values["Dedicated memory"].text()),
                         ("NVIDIA GeForce RTX 4070", "6.0 GB of 12.0 GB"))

    def test_startup_and_services_tabs(self):
        system = Path(os.environ["XDG_CONFIG_DIRS"]) / "autostart"
        user = Path(os.environ["XDG_CONFIG_HOME"]) / "autostart"
        import shutil
        for d in (system, user):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
        desktop_entry(system, "updater.desktop", "Update Checker")
        Services.setUp(self)
        win = self.make(desktop_system())
        win.go("startup")
        tree = win.startup.tree
        self.assertEqual((texts(tree), texts(tree, col=1)), (["Update Checker"], ["Enabled"]))
        tree.select("updater.desktop")
        self.assertEqual(win.startup.btn.text(), "Disable")
        win.startup.toggle()
        self.assertEqual((texts(tree, col=1), win.startup.btn.text()), (["Disabled"], "Enable"))
        win.startup.toggle()
        self.assertEqual(texts(tree, col=1), ["Enabled"])

        win.go("services")
        deadline = time.time() + 10
        while not win.services.items and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        tree = win.services.tree
        self.assertEqual(texts(tree)[:3], ["broken", "clamav-daemon", "cups"])
        tree.select("ollama.service")
        enabled = {verb: b.isEnabled() for verb, b in win.services.buttons.items()}
        self.assertEqual(enabled, {"start": False, "stop": True, "restart": True})
        tree.select("clamav-daemon.service")
        self.assertTrue(win.services.buttons["start"].isEnabled())
        tree.select("dbus.service")                                          # needed by the system
        self.assertFalse(win.services.buttons["stop"].isEnabled())

    def test_update_speed_and_settings_are_remembered(self):
        win = self.make(desktop_system())
        win.set_speed("low")
        self.assertEqual((win.sampler.interval, win.sampler.paused), (4.0, False))
        win.set_speed("paused")
        self.assertTrue(win.sampler.paused)
        win.set_speed("nonsense")
        self.assertEqual((win.sampler.interval, win.sampler.paused), (1.0, False))
        win.set_speed("high")
        win.go("details")
        win.close()
        again = tm.TaskManager(root=win.root, sample=False)
        self.addCleanup(again.close)
        self.assertEqual((again.sampler.interval, again.tabs.currentIndex()), (0.5, tm.T_DETAILS))

    def test_run_new_task(self):
        self.assertIn("Type the name", tm.run_task("  "))
        self.assertEqual(tm.run_task("no-such-program-xyz --flag"), 'There is no program called "no-such-program-xyz".')
        self.assertIn("not complete", tm.run_task('echo "unfinished'))
        fake_program("kstart", 'shift\n"$@"\n')
        fake_program("hello-task", f'echo "$@" > "{TMP}/hello.txt"\n')
        self.assertEqual(tm.run_task("hello-task one 'two words'"), "")
        deadline = time.time() + 5
        while not (TMP / "hello.txt").exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual((TMP / "hello.txt").read_text().strip(), "one two words")


@unittest.skipUnless(QT, "needs PyQt6")
class Tree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def test_sorted_only_when_the_order_changes(self):
        tree = KeyedTree(["Name", "Value"], numeric=(1,))
        self.addCleanup(tree.deleteLater)
        sorts = []
        real = tree.sort_now
        tree.sort_now = lambda: (sorts.append(1), real())

        def show(**values):
            tree.sync([Spec(k, [k, str(v)], [None, v]) for k, v in values.items()])
            return texts(tree)

        self.assertEqual(show(b=1, c=2, a=3), ["a", "b", "c"])              # by name at first
        self.assertEqual(len(sorts), 1)
        self.assertEqual(show(b=10, c=20, a=30), ["a", "b", "c"])           # numbers changed, order did not
        self.assertEqual(len(sorts), 1)
        tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.assertEqual(texts(tree), ["a", "c", "b"])
        count = len(sorts)
        self.assertEqual(show(b=10, c=20, a=30), ["a", "c", "b"])
        self.assertEqual(len(sorts), count)                                  # still nothing to do
        self.assertEqual(show(b=99, c=20, a=30), ["b", "a", "c"])           # b overtakes: sorted once
        self.assertEqual(len(sorts), count + 1)
        self.assertEqual(show(b=5, c=5, a=5), ["a", "b", "c"])              # equal values: A to Z
        self.assertEqual(show(b=5, c=5, a=5, d=5), ["a", "b", "c", "d"])    # a new row lands in place

    def test_clicking_a_number_column_shows_the_biggest_first(self):
        tree = KeyedTree(["Name", "Value"], numeric=(1,))
        self.addCleanup(tree.deleteLater)
        tree.sync([Spec(k, [k, str(v)], [None, v]) for k, v in dict(a=1, b=3, c=2).items()])
        head = tree.header()
        head.setSortIndicator(1, Qt.SortOrder.AscendingOrder)               # what a first click does
        self.assertEqual(head.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        self.assertEqual(texts(tree), ["b", "c", "a"])
        head.setSortIndicator(1, Qt.SortOrder.AscendingOrder)               # second click: the other way
        self.assertEqual(texts(tree), ["a", "c", "b"])
        head.setSortIndicator(0, Qt.SortOrder.AscendingOrder)               # names start A to Z
        self.assertEqual((head.sortIndicatorOrder(), texts(tree)), (Qt.SortOrder.AscendingOrder, ["a", "b", "c"]))

    def test_rows_move_and_leave_without_losing_state(self):
        tree = KeyedTree(["Name", "Value"], numeric=(1,))
        self.addCleanup(tree.deleteLater)
        tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        def rows(grouped, values):
            specs = [Spec("s1", ["One", ""], section=0), Spec("s2", ["Two", ""], section=1)] if grouped else []
            for key, (section, value, kids) in values.items():
                specs.append(Spec(key, [key, str(value)], [None, value], section if grouped else None))
                specs.extend(Spec(k, [k, "0"], [None, 0], key) for k in kids)
            return specs

        values = {"b": ("s2", 5, []), "a": ("s1", 30, ["a1", "a2"]), "c": ("s1", 7, [])}
        tree.sync(rows(True, values))
        self.assertEqual(texts(tree), ["One", "Two"])
        self.assertEqual(texts(tree, tree.topLevelItem(0)), ["a", "c"])
        tree.item_for("a").setExpanded(True)
        tree.select("a2")
        tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        tree.sync(rows(False, values))                       # headings go, rows move to the top level
        self.assertEqual(texts(tree), ["a", "c", "b"])
        self.assertTrue(tree.item_for("a").isExpanded())
        self.assertEqual(tree.current_key(), "a2")
        values["c"] = ("s1", 99, [])
        del values["a"]                                      # a row leaves together with its children
        tree.sync(rows(False, values))
        self.assertEqual(texts(tree), ["c", "b"])
        self.assertIsNone(tree.item_for("a1"))
        tree.sortByColumn(0, Qt.SortOrder.DescendingOrder)   # headings keep their order either way
        tree.sync(rows(True, values))
        self.assertEqual(texts(tree), ["One", "Two"])
        tree.sync([])
        self.assertEqual(texts(tree), [])


if __name__ == "__main__":
    unittest.main()
