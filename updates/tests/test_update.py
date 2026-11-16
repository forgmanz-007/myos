"""Automatic updates: myos-update (finds, checks and installs new versions published on
GitHub; tested against a small local stand-in for GitHub), updates/apply.sh (installs a
version on a running system; tested with stand-in parts and, on Linux, the real ones), and
how the ISO build and GitHub's workflow use them."""
import configparser
import fcntl
import hashlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parent
UPDATER = HERE / "bin/myos-update"
APPLY = HERE / "apply.sh"
REPO = "owner/myos"
ABSOLUTE = f"/tmp/myos-update-test-{os.getpid()}.txt"


def load_updater():
    loader = importlib.machinery.SourceFileLoader("myos_update", str(UPDATER))
    spec = importlib.util.spec_from_loader("myos_update", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Versions(unittest.TestCase):
    def test_versions_sort_by_date_then_build(self):
        up = load_updater()
        self.assertEqual(up.version_key("v2026.11.16-7"), (2026, 11, 16, 7))
        self.assertEqual(up.version_key("2026.11.16-7\n"), (2026, 11, 16, 7))
        self.assertGreater(up.version_key("v2026.11.16-10"), up.version_key("v2026.11.16-9"))
        self.assertGreater(up.version_key("v2026.12.1-1"), up.version_key("v2026.11.30-99"))
        for not_one in ("local", "unknown", "", "2026.11.16", "v1.2.3", "nightly", "2026.11.16-7-rc"):
            self.assertIsNone(up.version_key(not_one), not_one)

    def test_settings_file(self):
        up = load_updater()
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "update.conf"
            conf.write_text("# comment\nREPO='owner/myos'\nAUTO=no   # off\nOS_NAME='My OS 10'\nbad line\n")
            self.assertEqual(up.read_conf(conf), {"REPO": "owner/myos", "AUTO": "no", "OS_NAME": "My OS 10"})
            self.assertEqual(up.read_conf(Path(tmp) / "missing"), {})


# ── A stand-in for GitHub ────────────────────────────────────────────────────

class FakeGitHub:
    """Serves /repos/owner/myos/releases and the files of each release; notes each request."""

    def __init__(self):
        self.files = {}
        self.requests = []
        github = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                github.requests.append(self.path)
                body = github.files.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def downloads(self):
        return [r for r in self.requests if r.startswith("/dl/")]

    def publish(self, releases):
        """releases: dicts with tag and optional package (bytes), sums (text), draft,
        prerelease, digest, url (download address of the package)."""
        listing = []
        for r in releases:
            assets = []
            if r.get("package") is not None:
                path = f"/dl/{r['tag']}/myos-update.tar.gz"
                self.files[path] = r["package"]
                sums = r.get("sums", hashlib.sha256(r["package"]).hexdigest() + "  myos-update.tar.gz\n")
                self.files[path + ".sha256"] = sums.encode()
                asset = {"name": "myos-update.tar.gz", "browser_download_url": r.get("url", self.url + path)}
                if "digest" in r:
                    asset["digest"] = r["digest"]
                assets += [asset, {"name": "myos-update.tar.gz.sha256",
                                   "browser_download_url": self.url + path + ".sha256"}]
            listing.append({"tag_name": r["tag"], "draft": r.get("draft", False),
                            "prerelease": r.get("prerelease", False), "assets": assets,
                            "html_url": f"https://github.com/{REPO}/releases/tag/{r['tag']}"})
        self.files[f"/repos/{REPO}/releases?per_page=30"] = json.dumps(listing).encode()


FAKE_APPLY = """#!/bin/bash
echo "version=$MYOS_VERSION args=$* dir=$(pwd)" >> "$FAKE_LOG"
if [ -n "${FAKE_APPLY_SLEEP:-}" ]; then touch "$FAKE_LOG.started"; sleep "$FAKE_APPLY_SLEEP"; fi
exit "${FAKE_APPLY_EXIT:-0}"
"""


def package(version, apply_sh=FAKE_APPLY, extra=None, version_inside=None):
    """An update package like GitHub's workflow makes: myos/ with VERSION and updates/apply.sh."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, text, mode=0o644):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        add("myos/VERSION", (version_inside or version) + "\n")
        add("myos/updates/apply.sh", apply_sh, 0o755)
        for name, text in (extra or {}).items():
            add(name, text)
    return buf.getvalue()


class Updater(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.tmp / "root"
        (self.root / "etc/myos").mkdir(parents=True)
        self.conf(REPO=REPO)
        (self.root / "etc/myos/build.conf").write_text("OS_NAME='MyOS'\n")
        (self.root / "etc/myos/version").write_text("2026.11.14-1\n")
        self.github = FakeGitHub()
        self.addCleanup(self.github.close)
        self.log = self.tmp / "apply.log"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        snapper = bin_dir / "snapper"
        snapper.write_text('#!/bin/sh\necho "$@" >> "$FAKE_LOG.snapper"\n')
        snapper.chmod(0o755)
        self.env = dict(os.environ, MYOS_UPDATE_ROOT=str(self.root), MYOS_UPDATE_API=self.github.url,
                        FAKE_LOG=str(self.log), PATH=f"{bin_dir}:{os.environ['PATH']}")

    def conf(self, **values):
        (self.root / "etc/myos/update.conf").write_text("".join(f"{k}='{v}'\n" for k, v in values.items()))

    def run_updater(self, *args, **env):
        return subprocess.run([sys.executable, str(UPDATER), *args], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=60)

    def version(self):
        return (self.root / "etc/myos/version").read_text().strip()

    def status(self):
        return json.loads((self.root / "var/lib/myos/update/status.json").read_text())

    def applied(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_installs_the_newest_published_version(self):
        self.github.publish([
            {"tag": "v2026.11.15-3", "package": package("2026.11.15-3")},
            {"tag": "v2026.11.16-5", "package": package("2026.11.16-5")},
            {"tag": "v2026.11.17-6", "package": package("2026.11.17-6"), "draft": True},
            {"tag": "v2026.11.18-7", "package": package("2026.11.18-7"), "prerelease": True},
            {"tag": "v2026.11.19-8"},                         # no update package (yet)
            {"tag": "nightly", "package": package("nightly")},
        ])
        r = self.run_updater("--auto")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Installing MyOS 2026.11.16-5 (installed: 2026.11.14-1)", r.stdout)
        [line] = self.applied()
        self.assertTrue(line.startswith(f"version=2026.11.16-5 args=--destdir {self.root} dir="), line)
        self.assertTrue(line.endswith("/var/cache/myos-update/unpacked/myos"), line)
        self.assertEqual(self.version(), "2026.11.16-5")
        st = self.status()
        self.assertEqual((st["last_result"], st["latest"], st["failed_version"]), ("updated", "2026.11.16-5", ""))
        self.assertIn("Before MyOS update 2026.11.16-5", Path(f"{self.log}.snapper").read_text())
        self.assertFalse((self.root / "var/cache/myos-update").exists())       # cleaned up
        # Next time: nothing to do, nothing downloaded.
        self.github.requests.clear()
        r = self.run_updater("--auto")
        self.assertIn("MyOS is up to date (version 2026.11.16-5)", r.stdout)
        self.assertEqual(self.github.downloads(), [])
        self.assertEqual(len(self.applied()), 1)

    def test_check_only_tells(self):
        self.github.publish([{"tag": "v2026.11.16-5", "package": package("2026.11.16-5")}])
        r = self.run_updater("--check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("A new version is out: 2026.11.16-5 (installed: 2026.11.14-1)", r.stdout)
        self.assertEqual((self.github.downloads(), self.applied(), self.version()), ([], [], "2026.11.14-1"))
        (self.root / "etc/myos/version").write_text("2026.11.16-5\n")
        self.assertIn("MyOS is up to date (version 2026.11.16-5)", self.run_updater("--check").stdout)
        r = self.run_updater("--status")
        self.assertIn("Installed version: 2026.11.16-5", r.stdout)
        self.assertIn(f"github.com/{REPO}", r.stdout)

    def test_local_builds_take_the_newest_version(self):
        (self.root / "etc/myos/version").write_text("local\n")
        self.github.publish([{"tag": "v2026.11.15-3", "package": package("2026.11.15-3")}])
        self.assertEqual(self.run_updater().returncode, 0)
        self.assertEqual(self.version(), "2026.11.15-3")

    def test_damaged_download_is_not_installed(self):
        self.github.publish([{"tag": "v2026.11.16-5", "package": package("2026.11.16-5"),
                              "sums": "0" * 64 + "  myos-update.tar.gz\n"}])
        r = self.run_updater("--auto")
        self.assertEqual(r.returncode, 1)
        self.assertIn("damaged", r.stdout)
        self.assertEqual((self.applied(), self.version()), ([], "2026.11.14-1"))
        st = self.status()
        self.assertEqual((st["last_result"], st["failed_version"]), ("failed", "2026.11.16-5"))

    def test_githubs_own_checksum_must_agree(self):
        self.github.publish([{"tag": "v2026.11.16-5", "package": package("2026.11.16-5"),
                              "digest": "sha256:" + "1" * 64}])
        r = self.run_updater("--auto")
        self.assertEqual(r.returncode, 1)
        self.assertIn("disagree", r.stdout)
        self.assertEqual(self.github.downloads(), ["/dl/v2026.11.16-5/myos-update.tar.gz.sha256"])
        # ... and with the right one it installs.
        data = package("2026.11.16-5")
        self.github.publish([{"tag": "v2026.11.16-5", "package": data,
                              "digest": "sha256:" + hashlib.sha256(data).hexdigest()}])
        self.assertEqual(self.run_updater().returncode, 0)
        self.assertEqual(self.version(), "2026.11.16-5")

    def test_only_downloads_from_the_repository(self):
        self.github.publish([{"tag": "v2026.11.16-5", "package": package("2026.11.16-5"),
                              "url": "https://example.com/myos-update.tar.gz"}])
        r = self.run_updater("--auto")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unexpected download address", r.stdout)
        self.assertEqual(self.applied(), [])

    def test_unsafe_or_wrong_packages_are_refused(self):
        cases = [
            ("../outside.txt", package("2026.11.16-5", extra={"../outside.txt": "x"}), "could not be unpacked"),
            # (an absolute name lands inside the folder: the package then has two top folders)
            ("/abs.txt", package("2026.11.16-5", extra={ABSOLUTE: "x"}), "does not contain updates/apply.sh"),
            ("version", package("2026.11.16-5", version_inside="2026.11.15-3"), "is version 2026.11.15-3"),
        ]
        for name, data, message in cases:
            with self.subTest(name):
                self.github.publish([{"tag": "v2026.11.16-5", "package": data}])
                r = self.run_updater()
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(message, r.stdout)
                self.assertEqual((self.applied(), self.version()), ([], "2026.11.14-1"))
        self.assertEqual(list(self.tmp.rglob("outside.txt")), [])
        self.assertFalse(Path(ABSOLUTE).exists())

    def test_failed_install_is_tried_again_a_day_later(self):
        self.github.publish([{"tag": "v2026.11.16-5", "package": package("2026.11.16-5")}])
        r = self.run_updater("--auto", FAKE_APPLY_EXIT="3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ended with code 3", r.stdout)
        self.assertEqual(self.version(), "2026.11.14-1")
        # The timer does not hammer a version that failed ...
        r = self.run_updater("--auto")
        self.assertIn("trying again later", r.stdout)
        self.assertEqual(len(self.applied()), 1)
        # ... but a day later it tries again, and "sudo myos-update" tries at once.
        st_file = self.root / "var/lib/myos/update/status.json"
        st = self.status()
        st["failed_at"] -= 25 * 3600
        st_file.write_text(json.dumps(st))
        self.assertEqual(self.run_updater("--auto", FAKE_APPLY_EXIT="3").returncode, 1)
        self.assertEqual(len(self.applied()), 2)
        self.assertEqual(self.run_updater().returncode, 0)
        self.assertEqual((len(self.applied()), self.version()), (3, "2026.11.16-5"))

    def test_switched_off_or_not_set_up(self):
        self.github.publish([{"tag": "v2026.11.16-5", "package": package("2026.11.16-5")}])
        self.conf(REPO=REPO, AUTO="no")
        r = self.run_updater("--auto")
        self.assertEqual(r.returncode, 0)
        self.assertIn("switched off", r.stdout)
        self.assertEqual(self.github.requests, [])
        self.assertRegex(self.run_updater("--status").stdout, r"Automatic: +off")
        self.conf(REPO="")
        r = self.run_updater("--auto")
        self.assertIn("not set up", r.stdout)
        self.assertEqual((self.github.requests, self.applied()), ([], []))
        self.conf(REPO="not a repo")
        self.assertEqual(self.run_updater("--auto").returncode, 1)

    def test_offline_is_normal_for_the_timer(self):
        self.github.close()
        self.github.server.server_close()
        r = self.run_updater("--auto")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no connection to GitHub", r.stdout)
        self.assertEqual(self.status()["last_result"], "error")
        self.assertEqual(self.run_updater("--check").returncode, 1)
        self.github = FakeGitHub()            # for the clean-up
        self.addCleanup(self.github.close)

    def test_one_update_at_a_time(self):
        self.github.publish([{"tag": "v2026.11.16-5", "package": package("2026.11.16-5")}])
        state = self.root / "var/lib/myos/update"
        state.mkdir(parents=True)
        with open(state / "lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            r = self.run_updater("--auto")
        self.assertIn("Another update is running", r.stdout)
        self.assertEqual((self.github.requests, self.applied()), ([], []))

    def test_shutdown_waits_for_the_install(self):
        self.github.publish([{"tag": "v2026.11.16-5", "package": package("2026.11.16-5")}])
        proc = subprocess.Popen([sys.executable, str(UPDATER), "--auto"], env=dict(self.env, FAKE_APPLY_SLEEP="2"),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        started = Path(f"{self.log}.started")
        deadline = time.time() + 20
        while not started.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(started.exists())
        proc.send_signal(signal.SIGTERM)          # what systemd sends at shutdown (KillMode=mixed)
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("Stopping now", out)
        self.assertEqual(self.version(), "2026.11.16-5")


# ── apply.sh ─────────────────────────────────────────────────────────────────

PARTS = ["deepseek-shell", "myos-security", "performance", "task-manager", "desktop", "updates"]
STEP_FILES = {
    "properties-tab": ["iso/config/hooks/normal/0400-properties-tab.hook.chroot", "myos-security/kio-plugin/main.cpp",
                       "myos-security/kio-plugin/CMakeLists.txt"],
    "windows10-look": ["iso/config/hooks/normal/0420-windows10-look.hook.chroot", "desktop/bin/myos-install-look"],
    "video-wallpaper": ["iso/config/hooks/normal/0450-video-wallpaper.hook.chroot"],
}


class Apply(unittest.TestCase):
    """apply.sh with stand-in parts that only note how they were called."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())) / "with space"
        self.src = self.tmp / "myos"
        self.dest = self.tmp / "root"
        self.log = self.tmp / "calls.log"
        (self.src / "updates/migrations").mkdir(parents=True)
        shutil.copy(APPLY, self.src / "updates/apply.sh")
        for part in PARTS:
            script = self.src / part / "install.sh"
            script.parent.mkdir(parents=True, exist_ok=True)
            extra = ""
            if part == "desktop":
                extra = ('install -d "$2/usr/share/kio/servicemenus"\n'
                         'touch "$2/usr/share/kio/servicemenus/myos-video-wallpaper.desktop"\n')
            script.write_text(f'#!/bin/bash\necho "{part} $*" >> "$FAKE_LOG"\n{extra}')
        for files in STEP_FILES.values():
            for f in files:
                (self.src / f).parent.mkdir(parents=True, exist_ok=True)
                (self.src / f).write_text(f"{f} 1\n")
        runner = self.tmp / "runner"
        runner.write_text('#!/bin/bash\necho "hook $(basename "$1")" >> "$FAKE_LOG"\n'
                          'case "$1" in *"$FAIL_HOOK"*) [ -n "$FAIL_HOOK" ] && exit 1 ;; esac\nexit 0\n')
        runner.chmod(0o755)
        (self.dest / "etc/myos").mkdir(parents=True)
        (self.dest / "etc/myos/build.conf").write_text("OS_NAME='My OS 10'\nVIDEO_WALLPAPERS='yes'\n")
        self.env = dict(os.environ, FAKE_LOG=str(self.log), MYOS_HOOK_RUNNER=str(runner), FAIL_HOOK="",
                        MYOS_VERSION="2026.11.16-5")

    def apply(self, *args, **env):
        self.log.unlink(missing_ok=True)
        r = subprocess.run(["bash", str(self.src / "updates/apply.sh"), "--destdir", str(self.dest), *args],
                           env=dict(self.env, **env), capture_output=True, text=True, timeout=60)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return r, calls

    def hooks(self, calls):
        return [c.split()[1] for c in calls if c.startswith("hook ")]

    def test_installs_every_part_like_the_iso_build(self):
        r, calls = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        d = str(self.dest)
        self.assertEqual([c for c in calls if not c.startswith("hook ")], [
            f"deepseek-shell --destdir {d} --prefix /usr",
            f"myos-security --destdir {d} --prefix /usr",
            f"performance --destdir {d}",
            f"task-manager --destdir {d} --prefix /usr",
            f"desktop --destdir {d} --os-name My OS 10",
            f"updates --destdir {d}",
        ])
        self.assertIn("My OS 10 2026.11.16-5 installed", r.stdout)

    def test_downloaded_and_compiled_parts_only_when_changed(self):
        # A system that never ran them (no notes yet): all three.
        r, calls = self.apply()
        self.assertEqual(self.hooks(calls), ["0400-properties-tab.hook.chroot", "0420-windows10-look.hook.chroot",
                                             "0450-video-wallpaper.hook.chroot"])
        # The Properties tab's source is where the hook compiles it.
        self.assertEqual((self.dest / "usr/src/myos-compat-tab/main.cpp").read_text(),
                         "myos-security/kio-plugin/main.cpp 1\n")
        # Again: nothing changed, nothing runs.
        r, calls = self.apply()
        self.assertEqual(self.hooks(calls), [])
        # The look's install script changed: only that step.
        (self.src / "desktop/bin/myos-install-look").write_text("2\n")
        self.assertEqual(self.hooks(self.apply()[1]), ["0420-windows10-look.hook.chroot"])
        self.assertEqual(self.hooks(self.apply()[1]), [])
        # A new file in the Properties tab's source.
        (self.src / "myos-security/kio-plugin/extra.h").write_text("//\n")
        self.assertEqual(self.hooks(self.apply()[1]), ["0400-properties-tab.hook.chroot"])

    def test_iso_build_notes_what_it_has(self):
        r, calls = self.apply("--seed")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, [])                         # nothing installed, nothing run
        steps = (self.dest / "var/lib/myos/update/steps").read_text().split()
        self.assertEqual(steps[0::2], ["properties-tab", "windows10-look", "video-wallpaper"])
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", h) for h in steps[1::2]))
        # The first update of that system then only installs the parts.
        self.assertEqual(self.hooks(self.apply()[1]), [])

    def test_failing_step_is_tried_again(self):
        r, calls = self.apply(FAIL_HOOK="0420")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.hooks(calls), ["0400-properties-tab.hook.chroot", "0420-windows10-look.hook.chroot"])
        r, calls = self.apply()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.hooks(calls), ["0420-windows10-look.hook.chroot", "0450-video-wallpaper.hook.chroot"])

    def test_one_time_changes_run_once(self):
        (self.src / "updates/migrations/2026-12-01-b.sh").write_text("#\n")
        (self.src / "updates/migrations/2026-11-30-a.sh").write_text("#\n")
        (self.src / "updates/migrations/README.md").write_text("#\n")
        self.assertEqual(self.hooks(self.apply()[1])[-2:], ["2026-11-30-a.sh", "2026-12-01-b.sh"])
        self.assertEqual(self.hooks(self.apply()[1]), [])
        (self.src / "updates/migrations/2026-12-02-c.sh").write_text("#\n")
        self.assertEqual(self.hooks(self.apply()[1]), ["2026-12-02-c.sh"])
        # A new ISO has them all already.
        shutil.rmtree(self.dest / "var/lib/myos/update")
        self.apply("--seed")
        self.assertEqual(self.hooks(self.apply()[1]), [])

    def test_video_wallpapers_switched_off(self):
        (self.dest / "etc/myos/build.conf").write_text("OS_NAME='MyOS'\nVIDEO_WALLPAPERS='no'\n")
        self.apply()
        self.assertFalse((self.dest / "usr/share/kio/servicemenus/myos-video-wallpaper.desktop").exists())

    def test_hooks_never_run_against_another_folder_by_mistake(self):
        env = dict(self.env)
        del env["MYOS_HOOK_RUNNER"]
        self.env = env
        r, calls = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.hooks(calls), [])
        self.assertIn("Skipped windows10-look", r.stdout)
        self.assertEqual((self.dest / "var/lib/myos/update/steps").read_text(), "")


def gnu_install():
    try:
        return "GNU" in subprocess.run(["install", "--version"], capture_output=True, text=True).stdout
    except OSError:
        return False


@unittest.skipUnless(platform.system() == "Linux" and gnu_install(), "needs Linux (GNU install)")
class ApplyForReal(unittest.TestCase):
    def test_the_real_parts_install_into_a_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "etc/myos").mkdir(parents=True)
            (dest / "etc/myos/build.conf").write_text("OS_NAME='MyOS'\nVIDEO_WALLPAPERS='yes'\n")
            r = subprocess.run(["bash", str(APPLY), "--destdir", str(dest)], capture_output=True, text=True,
                               env=dict(os.environ, MYOS_HOOK_RUNNER="true"), timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for f in ("usr/bin/myos-update", "usr/lib/systemd/system/myos-update.timer",
                      "usr/bin/deepseek-shell", "usr/bin/myos-taskmanager", "usr/bin/myos-performance",
                      "usr/share/plasma/look-and-feel/org.myos.desktop/metadata.json",
                      "usr/libexec/myos/install-look", "usr/src/myos-compat-tab/CMakeLists.txt"):
                self.assertTrue((dest / f).exists(), f)
            self.assertEqual(len((dest / "var/lib/myos/update/steps").read_text().splitlines()), 3)


# ── The ISO build and GitHub ─────────────────────────────────────────────────

class Build(unittest.TestCase):
    def test_timer_and_service(self):
        units = {}
        for name in ("myos-update.service", "myos-update.timer"):
            cp = configparser.ConfigParser(strict=False, interpolation=None)
            cp.optionxform = str
            cp.read(HERE / "systemd" / name)
            units[name] = cp
        service, timer = units["myos-update.service"], units["myos-update.timer"]
        self.assertEqual(service["Service"]["ExecStart"], "/usr/bin/myos-update --auto")
        self.assertEqual(service["Service"]["Type"], "oneshot")
        self.assertEqual(service["Service"]["KillMode"], "mixed")       # see ShutdownWaits
        self.assertEqual(service["Unit"]["ConditionKernelCommandLine"], "!boot=live")
        self.assertIn("network-online.target", service["Unit"]["Wants"])
        self.assertEqual(timer["Install"]["WantedBy"], "timers.target")
        self.assertEqual((timer["Timer"]["OnBootSec"], timer["Timer"]["OnUnitActiveSec"]), ("10min", "6h"))
        if shutil.which("systemd-analyze"):
            with tempfile.TemporaryDirectory() as tmp:
                for name in units:
                    text = (HERE / "systemd" / name).read_text()
                    (Path(tmp) / name).write_text(text.replace("/usr/bin/myos-update", "/bin/true"))
                r = subprocess.run(["systemd-analyze", "verify", *(str(Path(tmp) / n) for n in units)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_iso_build(self):
        build = (PROJECT / "scripts/container-build.sh").read_text()
        self.assertIn('bash "$SRC/updates/install.sh" --destdir "$INC"\n', build)
        self.assertIn('echo "$MYOS_VERSION" > "$INC/etc/myos/version"\n', build)
        self.assertIn('echo "REPO=$(shq "$UPDATE_REPO")"\n', build)
        self.assertIn('bash "$SRC/updates/apply.sh" --seed --destdir "$INC"\n', build)
        self.assertNotRegex(build, r"\$VERSION\b")      # /etc/os-release sets VERSION
        self.assertIn("\nsystemctl enable myos-update.timer\n",
                      (PROJECT / "iso/config/hooks/normal/0800-system.hook.chroot").read_text())
        self.assertRegex((PROJECT / "config.env").read_text(), r'\nUPDATE_REPO="[\w.-]+/[\w.-]+"\n')

    def test_apply_installs_the_parts_like_the_iso_build(self):
        build = (PROJECT / "scripts/container-build.sh").read_text()
        in_build = re.findall(r'^bash "\$SRC/([\w-]+)/install\.sh" --destdir "\$INC"(.*)$', build, re.M)
        in_apply = re.findall(r"^    part ([\w-]+)(.*)$", APPLY.read_text(), re.M)
        self.assertEqual([(n, o.strip()) for n, o in in_build], [(n, o.strip()) for n, o in in_apply])
        self.assertEqual([n for n, _ in in_apply], PARTS)

    def test_steps_exist_and_never_trim_apps(self):
        text = APPLY.read_text()
        steps = re.search(r'STEPS="\n(.*?)"\n', text, re.S).group(1).split("\n")
        for line in filter(None, steps):
            _, hook, *files = line.split()
            for f in (hook, *files):
                self.assertTrue((PROJECT / f).exists(), f)
        self.assertNotIn("0600", text.replace("never 0600-trim-apps", ""))

    def test_github_publishes_what_the_updater_looks_for(self):
        flow = (PROJECT / ".github/workflows/build-iso.yml").read_text()
        up = load_updater()
        self.assertIn(f"-o out/{up.PACKAGE} HEAD", flow)
        self.assertIn(f"out/{up.PACKAGE} out/{up.PACKAGE}.sha256", flow)
        self.assertIn("--prefix=myos/ --add-file=VERSION", flow)
        self.assertIn('version="$(date -u +%Y.%m.%d)-${GITHUB_RUN_NUMBER}"', flow)
        self.assertIsNotNone(up.version_key("v2026.11.16-123"))
        self.assertIn('gh release create "v$VERSION"', flow)
        self.assertIn("contents: write", flow)

    def test_installer(self):
        s = (HERE / "install.sh").read_text()
        for wanted in ('"$HERE/bin/myos-update" "$DESTDIR/usr/bin/myos-update"',
                       '"$HERE/systemd/myos-update.service" "$DESTDIR/usr/lib/systemd/system/myos-update.service"',
                       '"$HERE/systemd/myos-update.timer" "$DESTDIR/usr/lib/systemd/system/myos-update.timer"'):
            self.assertIn(wanted, s)


if __name__ == "__main__":
    unittest.main()
