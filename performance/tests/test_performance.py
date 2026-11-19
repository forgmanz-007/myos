"""Tests for the speed settings: the visual effects tool (myos-performance), the
script that stops the idle virus scanner, and the settings files.
Uses fake busctl / kwriteconfig6 / kreadconfig6 / systemctl / pgrep programs."""
import configparser
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="myos-perf-")).resolve()
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["XDG_DATA_HOME"] = str(TMP / "data-home")           # the only "Start menu" these tests see
os.environ["XDG_DATA_DIRS"] = str(TMP / "data")
os.environ["MYOS_PERFORMANCE_LOGIN_DELAY"] = "0"        # the login check normally waits 20 s
LOG = TMP / "calls.log"
os.environ["FAKE_LOG"] = str(LOG)
BIN = TMP / "bin"
BIN.mkdir()
REAL_PATH = os.environ["PATH"]
os.environ["PATH"] = f"{BIN}:{REAL_PATH}"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent


def fake(name, body):
    p = BIN / name
    p.write_text("#!/bin/sh\necho \"" + name + " $*\" >> \"$FAKE_LOG\"\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def calls():
    return LOG.read_text().splitlines() if LOG.exists() else []


SUPPORT = """KWin Support Information:
Compositing
===========
Compositing is active
Compositing Type: OpenGL
OpenGL vendor string: Mesa
OpenGL renderer string: %s
OpenGL version string: 4.6
"""
VIRGL = "virgl (ANGLE (Apple, Apple M1, OpenGL 4.1 Metal - 89.4))"
NVIDIA = "NVIDIA GeForce RTX 4070/PCIe/SSE2"

# busctl answers "supportInformation" with the text in $FAKE_INFO (as busctl --json=short prints it).
fake("busctl", '''case "$*" in
  *supportInformation*) [ -n "$FAKE_BUSCTL_FAIL" ] && exit 1; cat "$FAKE_INFO" ;;
esac
exit 0
''')
fake("kwriteconfig6", "exit 0\n")
fake("kreadconfig6", 'echo "$FAKE_ANIMATION"\n')

loader = importlib.machinery.SourceFileLoader("myos_performance", str(ROOT / "bin" / "myos-performance"))
spec = importlib.util.spec_from_loader("myos_performance", loader)
perf = importlib.util.module_from_spec(spec)
loader.exec_module(perf)


def graphics(name):
    """Make the fake window manager report this renderer ('' = it can't be asked)."""
    os.environ.pop("FAKE_BUSCTL_FAIL", None)
    if not name:
        os.environ["FAKE_BUSCTL_FAIL"] = "1"
        return
    info = TMP / "info.json"
    info.write_text(json.dumps({"type": "s", "data": [SUPPORT % name]}))
    os.environ["FAKE_INFO"] = str(info)


def run(*args):
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = perf.main(list(args))
    return code, out.getvalue().strip(), err.getvalue().strip()


class VisualEffects(unittest.TestCase):
    def setUp(self):
        LOG.unlink(missing_ok=True)
        perf.state_path().unlink(missing_ok=True)
        os.environ["FAKE_ANIMATION"] = ""
        graphics(VIRGL)

    def test_which_graphics_are_weak(self):
        weak = [VIRGL, "llvmpipe (LLVM 19.1.7, 256 bits)", "SVGA3D; build: RELEASE;  LLVM;",
                "softpipe", "Software Rasterizer"]
        fine = [NVIDIA, "AMD Radeon RX 6600 (radeonsi, navi23, LLVM 19.1.7, DRM 3.61, 6.12.57)",
                "Mesa Intel(R) UHD Graphics 770 (ADL-S GT1)", "NV176", "Apple M1 (G13G B1)"]
        for name in weak:
            self.assertEqual(perf.graphics_class(SUPPORT % name), "weak", name)
        for name in fine:
            self.assertEqual(perf.graphics_class(SUPPORT % name), "ok", name)
        self.assertEqual(perf.graphics_class(""), "unknown")
        self.assertEqual(perf.graphics_class("Compositing is not active\n"), "unknown")
        self.assertEqual(perf.graphics_class("Compositing Type: QPainter\n"), "weak")
        self.assertEqual(perf.renderer(SUPPORT % NVIDIA), NVIDIA)

    def test_fast_and_normal(self):
        code, out, _ = run("fast")
        self.assertEqual((code, out), (0, "Best performance: animations and blur are off."))
        log = calls()
        self.assertIn("kwriteconfig6 --file kdeglobals --group KDE --key AnimationDurationFactor --notify 0", log)
        self.assertIn("kwriteconfig6 --file kwinrc --group Plugins --key blurEnabled --notify false", log)
        self.assertIn("kwriteconfig6 --file kwinrc --group Plugins --key contrastEnabled --notify false", log)
        self.assertIn("busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure", log)
        self.assertIn("busctl --user call org.kde.KWin /Effects org.kde.kwin.Effects unloadEffect s blur", log)
        self.assertEqual(run("status", "--short")[1], "fast fast")
        LOG.unlink()
        code, out, _ = run("normal")
        self.assertEqual((code, out), (0, "Best appearance: the desktop's usual animations and effects are on."))
        log = calls()
        self.assertIn("kwriteconfig6 --file kdeglobals --group KDE --key AnimationDurationFactor --notify --delete", log)
        self.assertIn("kwriteconfig6 --file kwinrc --group Plugins --key blurEnabled --notify --delete", log)
        self.assertIn("busctl --user call org.kde.KWin /Effects org.kde.kwin.Effects loadEffect s contrast", log)
        self.assertEqual(run("status", "--short")[1], "normal normal")

    def test_auto_follows_the_graphics(self):
        code, out, _ = run("auto")
        self.assertEqual(code, 0)
        self.assertIn("The system chooses: best performance", out)
        self.assertIn("no graphics card driver in use", out)
        self.assertEqual(run("status", "--short")[1], "auto fast")
        graphics(NVIDIA)
        self.assertIn("The system chooses: best appearance (graphics: NVIDIA GeForce RTX 4070", run("auto")[1])
        self.assertEqual(json.loads(perf.state_path().read_text()),
                         {"mode": "auto", "applied": "normal", "renderer": NVIDIA})
        graphics("")                                   # the desktop can't be asked: leave it as it is
        LOG.unlink()
        run("auto")
        self.assertEqual(run("status", "--short")[1], "auto normal")
        self.assertTrue(any("--delete" in c for c in calls()))     # "normal" written again, nothing switched to fast

    def test_login_only_acts_when_something_changed(self):
        code, out, _ = run("auto", "--login")           # first login in a virtual machine
        self.assertEqual(code, 0)
        self.assertIn("best performance", out)
        LOG.unlink()
        self.assertEqual(run("auto", "--login"), (0, "", ""))     # next login: nothing to do
        self.assertFalse(any(c.startswith("kwriteconfig6") for c in calls()))
        graphics(NVIDIA)                                 # the graphics driver got installed
        self.assertIn("best appearance", run("auto", "--login")[1])
        self.assertEqual(run("status", "--short")[1], "auto normal")
        run("fast")                                      # you chose yourself: login never changes it
        LOG.unlink()
        self.assertEqual(run("auto", "--login"), (0, "", ""))
        self.assertEqual(calls(), [])
        self.assertEqual(run("status", "--short")[1], "fast fast")

    def test_login_check_stays_out_of_the_way_of_the_desktop_starting(self):
        waits = []
        old_sleep, old_delay = perf.time.sleep, os.environ.pop("MYOS_PERFORMANCE_LOGIN_DELAY")
        perf.time.sleep = waits.append
        try:
            run("auto", "--login")
            self.assertEqual(waits, [20.0])
            waits.clear()
            run("auto")                                  # asked for by you: at once
            run("fast")
            self.assertEqual(waits, [])
        finally:
            perf.time.sleep = old_sleep
            os.environ["MYOS_PERFORMANCE_LOGIN_DELAY"] = old_delay

    def test_login_waits_for_nothing_it_cannot_know(self):
        graphics("")
        old = perf.kwin_info
        perf.kwin_info = lambda wait=0.0: ""            # (the real one would wait 40 s for the desktop)
        try:
            self.assertEqual(run("auto", "--login"), (0, "", ""))
        finally:
            perf.kwin_info = old
        self.assertFalse(perf.state_path().exists())
        self.assertFalse(any(c.startswith("kwriteconfig6") for c in calls()))

    def test_status(self):
        os.environ["FAKE_ANIMATION"] = "0"              # animations already off, nothing chosen yet
        self.assertEqual(run("status", "--short")[1], "auto fast")
        os.environ["FAKE_ANIMATION"] = "0.5"
        self.assertEqual(run("status", "--short")[1], "auto normal")
        run("fast")
        text = run("status")[1]
        self.assertIn("Visual effects: best performance (chosen by you)", text)
        self.assertIn(f"Graphics: {VIRGL} (no graphics card driver in use", text)
        self.assertEqual(run("graphics")[1], "weak")
        graphics(NVIDIA)
        self.assertEqual(run("graphics")[1], "ok")
        graphics("")
        self.assertEqual(run("graphics")[1], "unknown")
        self.assertIn("could not be checked", run("status")[1])

    def test_without_the_kde_tools(self):
        empty = TMP / "empty-bin"
        empty.mkdir(exist_ok=True)
        os.environ["PATH"] = str(empty)
        try:
            code, out, err = run("fast")
        finally:
            os.environ["PATH"] = f"{BIN}:{REAL_PATH}"
        self.assertEqual((code, out), (1, ""))
        self.assertIn("kwriteconfig6 is missing", err)
        self.assertFalse(perf.state_path().exists())

    def test_a_failed_setting_is_reported(self):
        fake("kwriteconfig6", 'echo "could not write" >&2\nexit 1\n')
        try:
            code, _out, err = run("fast")
        finally:
            fake("kwriteconfig6", "exit 0\n")
        self.assertEqual(code, 1)
        self.assertIn("AnimationDurationFactor could not be changed: could not write", err)


class IdleVirusScanner(unittest.TestCase):
    SCRIPT = ROOT / "libexec" / "clamd-idle-stop"

    def setUp(self):
        LOG.unlink(missing_ok=True)
        self.state = TMP / "clamd-idle"
        self.state.unlink(missing_ok=True)
        self.proc = TMP / "proc"
        self.service, self.socket, self.pid, self.scanning = "active", "active", 6189, False
        self.ticks(1000)
        self.memory(available=10)               # percent: short of memory unless a test says otherwise

    def memory(self, available):
        self.proc.mkdir(parents=True, exist_ok=True)
        (self.proc / "meminfo").write_text(f"MemTotal:       16000000 kB\nMemFree:          100000 kB\n"
                                           f"MemAvailable:   {160000 * available} kB\n")

    def ticks(self, used, pid=None):
        pid = pid or self.pid
        d = self.proc / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "stat").write_text(f"{pid} (clamd) S 1 {pid} {pid} 0 -1 4194560 1 0 0 0 {used - 100} 100 0 0 "
                                "20 0 3 0 5000 1 1 1\n")

    def look(self):
        fake("systemctl", f'''case "$1 $2 $3" in
  "is-active --quiet clamav-daemon.service") [ "{self.service}" = active ] && exit 0; exit 3 ;;
  "is-active --quiet clamav-daemon.socket") [ "{self.socket}" = active ] && exit 0; exit 3 ;;
  "show -p MainPID") echo {self.pid} ;;
esac
exit 0
''')
        fake("pgrep", f"exit {0 if self.scanning else 1}\n")
        env = dict(os.environ, MYOS_CLAMD_IDLE_STATE=str(self.state), MYOS_CLAMD_IDLE_PROC=str(self.proc),
                   PATH=f"{BIN}:/usr/bin:/bin")
        # the script sets its own PATH; put the fakes first again through a wrapper
        script = self.SCRIPT.read_text().replace("export PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                                                 f"export PATH={BIN}:/usr/sbin:/usr/bin:/sbin:/bin")
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def stopped(self):
        return "systemctl stop clamav-daemon.service" in calls()

    def test_stopped_when_idle_and_memory_is_short(self):
        self.assertEqual(self.look(), "")       # the first look only notes where it stands
        self.assertFalse(self.stopped())
        self.assertEqual(self.state.read_text().split(), ["6189", "1000", "0"])
        out = self.look()                       # nothing used it since: 5 idle minutes
        self.assertTrue(self.stopped())
        self.assertIn("Memory is short (10% available)", out)
        self.assertFalse(self.state.exists())

    def test_left_loaded_while_there_is_memory_to_spare(self):
        self.memory(available=60)
        for _ in range(8):                      # idle for a long time
            self.assertEqual(self.look(), "")
        self.assertFalse(self.stopped())        # scans stay instant
        self.memory(available=24)               # then memory gets short
        self.look()
        self.assertTrue(self.stopped())

    def test_never_stopped_while_it_works(self):
        self.look()
        self.ticks(1500)                        # it scanned something since the last look
        self.look()
        self.assertFalse(self.stopped())
        self.assertEqual(self.state.read_text().split()[2], "0")
        self.ticks(1510)                        # its hourly self-check: a few ticks only
        self.scanning = True                    # but a scan is running right now
        self.look()
        self.assertFalse(self.stopped())
        self.scanning = False
        self.look()
        self.assertTrue(self.stopped())

    def test_a_restarted_scanner_counts_as_new(self):
        self.look()
        self.pid = 7000
        self.ticks(1000, 7000)
        self.look()
        self.assertFalse(self.stopped())
        self.assertEqual(self.state.read_text().split(), ["7000", "1000", "0"])

    def test_left_alone_when_nothing_could_start_it_again(self):
        self.socket = "inactive"
        for _ in range(6):
            self.look()
        self.assertFalse(self.stopped())

    def test_nothing_to_do_while_it_is_not_running(self):
        self.look()
        self.assertTrue(self.state.exists())
        self.service = "inactive"
        self.look()
        self.assertFalse(self.state.exists())
        self.assertFalse(self.stopped())

    def test_odd_state_file_is_ignored(self):
        self.state.write_text("garbage here\n")
        self.look()
        self.assertEqual(self.state.read_text().split(), ["6189", "1000", "0"])


class CompressedMemory(unittest.TestCase):
    """memory-setup: zswap in front of swap on disk, else a compressed swap in RAM."""
    SCRIPT = ROOT / "libexec" / "memory-setup"

    def setUp(self):
        LOG.unlink(missing_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="mem-", dir=TMP))
        self.zswap = self.dir / "zswap"
        self.zswap.mkdir()
        for name, value in (("enabled", "N"), ("compressor", "lzo"), ("zpool", "zbud"),
                            ("max_pool_percent", "20"), ("shrinker_enabled", "N")):
            (self.zswap / name).write_text(value + "\n")
        (self.dir / "lru_gen").mkdir()
        (self.dir / "lru_gen" / "min_ttl_ms").write_text("0\n")
        self.ram(16)
        self.swaps()
        fake("zramctl", '[ -n "$FAKE_ZRAMCTL_EXIT" ] && exit "$FAKE_ZRAMCTL_EXIT"\n'
                        'case "$1" in --find) echo /dev/zram0 ;; esac\nexit 0\n')
        for tool in ("mkswap", "swapon", "swapoff", "sysctl", "modprobe"):
            fake(tool, "exit 0\n")
        os.environ.pop("FAKE_ZRAMCTL_EXIT", None)

    def ram(self, gb):
        (self.dir / "meminfo").write_text(f"MemTotal:       {gb * 1024 * 1024} kB\nMemFree:  100 kB\n")

    def swaps(self, *devices):
        text = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
        for dev in devices:
            text += f"{dev}                               partition\t8388604\t\t0\t\t-2\n"
        (self.dir / "swaps").write_text(text)

    def run_setup(self):
        script = self.SCRIPT.read_text().replace("export PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                                                 f"export PATH={BIN}:/usr/sbin:/usr/bin:/sbin:/bin")
        env = dict(os.environ, MYOS_MEMORY_SWAPS=str(self.dir / "swaps"), MYOS_MEMORY_ZSWAP=str(self.zswap),
                   MYOS_MEMORY_MEMINFO=str(self.dir / "meminfo"), MYOS_MEMORY_LRU_GEN=str(self.dir / "lru_gen"),
                   MYOS_MEMORY_STATE=str(self.dir / "mode"), MYOS_MEMORY_RETRY_WAIT="0")
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout + r.stderr

    def zswap_values(self):
        return {f.name: f.read_text().strip() for f in self.zswap.iterdir()}

    def mode(self):
        f = self.dir / "mode"
        return f.read_text().strip() if f.exists() else ""

    def test_pc_with_swap_on_disk_compresses_in_ram_first(self):
        self.swaps("/dev/nvme0n1p3")
        out = self.run_setup()
        self.assertEqual(self.zswap_values(), {"enabled": "Y", "compressor": "zstd", "zpool": "zsmalloc",
                                               "max_pool_percent": "35", "shrinker_enabled": "Y"})
        self.assertEqual(self.mode(), "zswap")
        self.assertIn("sysctl -q -w vm.swappiness=133", calls())
        self.assertFalse(any(c.startswith(("zramctl", "mkswap", "swapon")) for c in calls()))
        self.assertIn("zswap, zstd", out)
        self.assertEqual((self.dir / "lru_gen" / "min_ttl_ms").read_text().strip(), "1000")

    def test_a_leftover_swap_in_ram_is_removed_so_nothing_is_compressed_twice(self):
        self.swaps("/dev/zram0", "/swapfile")
        self.run_setup()
        self.assertEqual(self.mode(), "zswap")
        self.assertIn("swapoff /dev/zram0", calls())
        self.assertEqual(calls().count("zramctl --reset /dev/zram0"), 1)
        self.assertNotIn("swapoff /swapfile", calls())
        self.assertNotIn("sysctl -q -w vm.page-cluster=3", calls())         # was never changed in this start

    def test_a_device_that_is_still_busy_is_tried_again(self):
        # Seen on the test PC: right after the driver loads, the first try fails with "busy".
        fake("zramctl", 'n=$(grep -c "^zramctl --find" "$FAKE_LOG")\n[ "$n" -ge 5 ] || exit 1\necho /dev/zram0\n')
        os.environ["MYOS_MEMORY_RETRY_WAIT"] = "0"
        try:
            out = self.run_setup()
        finally:
            os.environ.pop("MYOS_MEMORY_RETRY_WAIT")
        self.assertEqual(self.mode(), "zram")
        self.assertIn("swapon --priority 100 /dev/zram0", calls())
        self.assertIn("Compressed memory is on (zram)", out)

    def test_formatting_and_switching_on_are_tried_again_too(self):
        # Also seen there: the device is found, then "busy" when it is formatted or switched on.
        fake("mkswap", '[ "$(grep -c "^mkswap" "$FAKE_LOG")" -ge 3 ]\n')
        fake("swapon", '[ "$(grep -c "^swapon" "$FAKE_LOG")" -ge 4 ]\n')
        self.run_setup()
        self.assertEqual(self.mode(), "zram")
        self.assertEqual(calls().count("mkswap /dev/zram0"), 3)
        self.assertEqual(calls().count("swapon --priority 100 /dev/zram0"), 4)
        self.assertEqual(sum(c.startswith("zramctl --find") for c in calls()), 1)      # one device, not several
        self.assertNotIn("zramctl --reset /dev/zram0", calls())

    def test_a_device_that_cannot_be_used_is_given_back(self):
        fake("swapon", "echo 'swapon: /dev/zram0: swapon failed: Device or resource busy' >&2\nexit 1\n")
        out = self.run_setup()
        self.assertEqual(self.mode(), "off")
        self.assertIn("zramctl --reset /dev/zram0", calls())
        self.assertIn("Device or resource busy", out)                 # the reason is in the log
        self.assertIn("could not be set up", out)

    def test_swap_in_ram_is_not_compressed_a_second_time(self):
        (self.zswap / "enabled").write_text("Y\n")              # left on by the other mode
        self.run_setup()
        self.assertEqual(self.mode(), "zram")
        self.assertEqual(self.zswap_values()["enabled"], "N")

    def test_switching_from_ram_swap_to_disk_swap_without_a_restart(self):
        self.run_setup()                                                    # no swap on disk yet
        self.assertEqual(self.mode(), "zram")
        LOG.unlink()
        self.swaps("/dev/zram0", "/swapfile")                               # the user added a swap file
        fake("zramctl", 'n=$(grep -c "^zramctl --reset" "$FAKE_LOG")\n[ "$n" -ge 3 ]\n')   # busy twice
        os.environ["MYOS_MEMORY_RETRY_WAIT"] = "0"
        try:
            self.run_setup()
        finally:
            os.environ.pop("MYOS_MEMORY_RETRY_WAIT")
        self.assertEqual(self.mode(), "zswap")
        self.assertEqual(calls().count("zramctl --reset /dev/zram0"), 3)    # tried until the device let go
        self.assertIn("sysctl -q -w vm.page-cluster=3", calls())            # undoes the RAM-swap setting

    def test_pc_without_swap_on_disk_gets_a_compressed_swap_in_ram(self):
        out = self.run_setup()
        self.assertEqual(self.mode(), "zram")
        self.assertEqual(self.zswap_values()["enabled"], "N")
        log = calls()
        self.assertIn(f"zramctl --find --algorithm zstd --size {8 * 1024 * 1024}KiB", log)   # 16 GB RAM: capped at 8
        self.assertIn("mkswap /dev/zram0", log)
        self.assertIn("swapon --priority 100 /dev/zram0", log)
        self.assertIn("sysctl -q -w vm.swappiness=150 vm.page-cluster=0", log)
        self.assertIn("no swap space on its disk", out)
        self.ram(6)                                         # a small PC: as big as its RAM
        LOG.unlink()
        self.run_setup()
        self.assertIn(f"zramctl --find --algorithm zstd --size {6 * 1024 * 1024}KiB", calls())

    def test_nothing_is_set_up_twice(self):
        self.swaps("/dev/zram0")
        self.run_setup()
        self.assertEqual(self.mode(), "zram")
        self.assertFalse(any(c.startswith(("zramctl", "mkswap", "swapon")) for c in calls()))
        self.assertIn("sysctl -q -w vm.swappiness=150 vm.page-cluster=0", calls())

    def test_pc_where_it_cannot_be_set_up_still_starts(self):
        os.environ["FAKE_ZRAMCTL_EXIT"] = "1"
        try:
            out = self.run_setup()                          # exit code 0: never blocks the start-up
        finally:
            os.environ.pop("FAKE_ZRAMCTL_EXIT")
        self.assertIn("could not be set up", out)
        self.assertEqual(self.mode(), "off")
        self.assertEqual(sum(c.startswith("zramctl --find --algorithm") for c in calls()), 10)   # it kept trying
        self.assertFalse(any(c.startswith("swapon") for c in calls()))


class AppClosed(unittest.TestCase):
    """The one notice when an app was closed because memory ran out."""
    SCRIPT = ROOT / "libexec" / "app-closed"

    def setUp(self):
        LOG.unlink(missing_ok=True)
        self.data = Path(tempfile.mkdtemp(prefix="data-", dir=TMP))
        (self.data / "applications").mkdir()
        (self.data / "applications" / "org.kde.dolphin.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=File Explorer\nIcon=system-file-manager\nExec=dolphin\n")
        (self.data / "applications" / "memhog-busy1.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=MemHog busy1\nExec=x\n")
        fake("notify-send", "exit 0\n")

    def run_it(self, unit, result="oom-kill"):
        env = dict(os.environ, MONITOR_UNIT=unit, MONITOR_SERVICE_RESULT=result, XDG_DATA_HOME=str(self.data))
        r = subprocess.run(["sh", str(self.SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [c for c in calls() if c.startswith("notify-send")]

    def test_one_notice_with_the_apps_own_name(self):
        shown = self.run_it("app-org.kde.dolphin@0a1b2c3d4e5f.service")
        self.assertEqual(shown, ["notify-send -a Memory -i system-file-manager File Explorer was closed "
                                 "Memory ran out, so the system closed File Explorer to keep the PC working."])
        LOG.unlink()
        self.assertIn("MemHog busy1 was closed", self.run_it("app-memhog\\x2dbusy1@401842e8.service")[0])
        LOG.unlink()
        self.assertIn("-i dialog-warning steam was closed", self.run_it("app-gnome-steam-4242.scope")[0])

    def test_nothing_when_the_app_failed_for_another_reason(self):
        self.assertEqual(self.run_it("app-org.kde.dolphin@0a.service", result="exit-code"), [])
        self.assertEqual(self.run_it("", result="oom-kill"), [])

    def test_installed_for_every_app_instead_of_plasmas_notice(self):
        installer = (ROOT / "install.sh").read_text()
        for target in ("/usr/libexec/myos/app-closed", "/usr/lib/systemd/user/myos-app-closed@.service",
                       "/usr/lib/systemd/user/app-.service.d/20-myos-app-closed.conf",
                       "/usr/lib/systemd/user/app-.scope.d/20-myos-app-closed.conf", "/etc/xdg/kded5rc"):
            self.assertIn(f'"$DESTDIR{target}"', installer, target)
        hook = configparser.ConfigParser(interpolation=None)              # "%p" is systemd's, not Python's
        hook.read(ROOT / "systemd" / "app-closed.conf")
        self.assertEqual(hook["Unit"]["OnFailure"], "myos-app-closed@%p-%i.service")    # one per app
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / "systemd" / "myos-app-closed@.service")
        self.assertEqual(unit["Service"]["ExecStart"], "/usr/libexec/myos/app-closed")
        kded = configparser.ConfigParser()
        kded.read(ROOT / "xdg" / "kded5rc")                               # the name Plasma 6 still reads
        self.assertEqual(kded["Module-oom-notifier"]["autoload"], "false")


class SettingsFiles(unittest.TestCase):
    def test_memory_settings(self):
        packages = (REPO / "iso/config/package-lists/system.list.chroot").read_text().split()
        self.assertNotIn("systemd-zram-generator", packages)        # replaced by memory-setup
        lines = [line for line in (ROOT / "etc" / "60-myos-memory.conf").read_text().splitlines()
                 if line and not line.startswith("#")]
        self.assertEqual(lines, ["vm.watermark_scale_factor = 125", "vm.watermark_boost_factor = 0"])
        # No systemd-oomd (see install.sh): not installed, not enabled, old settings removed.
        self.assertFalse((ROOT / "oomd").exists())
        self.assertNotIn("systemd-oomd", packages)
        installer = (ROOT / "install.sh").read_text()
        for target in ("/usr/libexec/myos/memory-setup", "/etc/firefox/defaults/pref/myos-memory.js"):
            self.assertIn(f'"$DESTDIR{target}"', installer, target)
        for old in ("/etc/systemd/oomd.conf.d/20-myos.conf", "/etc/systemd/user/app.slice.d/20-myos-oomd.conf"):
            self.assertIn(f'"$DESTDIR{old}"', installer.split("rm -f", 1)[1], old)
        self.assertNotIn("install -Dm644 \"$HERE/oomd", installer)
        # No "memory is almost full" warning: not shipped, and removed where it was installed before.
        self.assertFalse((ROOT / "libexec" / "memory-watch").exists())
        self.assertFalse((ROOT / "systemd" / "myos-memory-watch.service").exists())
        self.assertNotIn("install -Dm755 \"$HERE/libexec/memory-watch\"", installer)
        self.assertIn('rm -f "$DESTDIR/usr/libexec/myos/memory-watch"', installer)
        hook = (REPO / "iso/config/hooks/normal/0850-performance.hook.chroot").read_text()
        self.assertIn("systemctl enable myos-memory.service", hook)
        self.assertNotIn("systemd-oomd", hook)
        self.assertNotIn("memory-watch", hook)
        self.assertIn('pref("browser.tabs.unloadOnLowMemory", true);', (ROOT / "firefox" / "myos-memory.js").read_text())

    def test_units_and_installer_agree(self):
        installer = (ROOT / "install.sh").read_text()
        service = configparser.ConfigParser()
        service.read(ROOT / "systemd" / "myos-clamd-idle.service")
        helper = service["Service"]["ExecStart"]
        self.assertIn(f'"$DESTDIR{helper}"', installer)
        timer = configparser.ConfigParser()
        timer.read(ROOT / "systemd" / "myos-clamd-idle.timer")
        self.assertEqual((timer["Timer"]["OnUnitActiveSec"], timer["Install"]["WantedBy"]), ("5min", "timers.target"))
        hook = (REPO / "iso/config/hooks/normal/0850-performance.hook.chroot").read_text()
        self.assertIn("systemctl enable myos-clamd-idle.timer", hook)
        self.assertIn('bash "$SRC/performance/install.sh" --destdir "$INC"',
                      (REPO / "scripts/container-build.sh").read_text())
        for name in ("journald-10-myos.conf", "systemd-10-myos.conf"):
            cp = configparser.ConfigParser()
            cp.read(ROOT / "etc" / name)
            self.assertEqual(len(cp.sections()), 1, name)
        for script in (ROOT / "install.sh", REPO / "iso/config/hooks/normal/0850-performance.hook.chroot"):
            self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0, script)

    def test_login_helper_is_not_a_startup_app(self):
        """It is part of the system, so Task Manager's Startup tab must not offer to switch it off."""
        sys.path.insert(0, str(REPO / "task-manager"))
        from myos_taskmanager import desktopfile, startup
        entry = desktopfile.load(ROOT / "autostart" / "myos-performance.desktop")
        self.assertEqual(entry["Exec"], "myos-performance auto --login")
        self.assertTrue(startup._internal(entry))

    def test_ai_engine_settings(self):
        unit = (REPO / "deepseek-shell/data/ollama.service").read_text()
        self.assertIn("Nice=5", unit)
        self.assertIn('Environment="OLLAMA_KEEP_ALIVE=5m"', unit)


class PythonBytecode(unittest.TestCase):
    """libexec/python-bytecode: the .pyc files that live-build deletes, made again."""

    def run_script(self, dirs):
        env = dict(os.environ, MYOS_BYTECODE_DIRS=" ".join(str(d) for d in dirs), PATH=REAL_PATH)
        return subprocess.run(["sh", str(ROOT / "libexec/python-bytecode")], env=env,
                              capture_output=True, text=True, timeout=120)

    def test_compiles_and_skips_what_is_not_python_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, other = Path(tmp, "app"), Path(tmp, "other")
            (app / "pkg").mkdir(parents=True)
            other.mkdir()
            (app / "main.py").write_text("X = 1\n")
            (app / "pkg/__init__.py").write_text("")
            (other / "example.py").write_text("print 'Python 2'\n")      # some packages ship such files
            r = self.run_script([app, other, Path(tmp, "missing")])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "Python bytecode ready: 2 files.\n")
            self.assertEqual(sorted(p.name.split(".")[0] for p in Path(tmp).rglob("*.pyc")), ["__init__", "main"])
            # Again (every update runs it): nothing new, same answer.
            self.assertEqual(self.run_script([app, other]).stdout, "Python bytecode ready: 2 files.\n")

    def test_installed_and_run_last_in_the_iso_build(self):
        self.assertIn('"$HERE/libexec/python-bytecode" "$DESTDIR/usr/libexec/myos/python-bytecode"',
                      (ROOT / "install.sh").read_text())
        hooks = ROOT.parent / "iso/config/hooks/normal"
        last = hooks / "9900-python-bytecode.hook.chroot"
        self.assertIn("\n/usr/libexec/myos/python-bytecode\n", last.read_text())
        # "lb config" puts live-build's own hooks into the same folder; they run in name order,
        # and 9010-remove-python-pyc deletes every .pyc file under /usr.
        names = [p.name for p in hooks.glob("*.hook.chroot")] + ["9010-remove-python-pyc.hook.chroot",
                                                                 "9020-remove-man-cache.hook.chroot"]
        self.assertEqual(max(names), last.name)
        self.assertNotIn("compileall", (hooks / "0850-performance.hook.chroot").read_text())
        self.assertIn("\n    /usr/libexec/myos/python-bytecode || true\n",
                      (ROOT.parent / "updates/apply.sh").read_text())


if __name__ == "__main__":
    unittest.main()
