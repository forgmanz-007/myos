"""Tests for ClamAV Security, the .exe guard and the Wine/Proton launcher.
Uses fake clamscan/clamdscan/wine/umu-run programs and a fake API server."""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="myos-sec-"))
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
    os.environ[var] = str(TMP / var.lower())
BIN = TMP / "bin"
BIN.mkdir()
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from myos_security import checks, guard, launcher, scanners, store  # noqa: E402
from myos_security.scanners import Finding, decide  # noqa: E402

EICAR_SHA = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"


def fake_program(name, script):
    p = BIN / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


# clamdscan: "service not running"; clamscan: flags files containing EVIL.
fake_program("clamdscan", 'case "$1" in --version) echo "ClamAV 1.4.3/27800/Wed Nov  4 09:21:00 2026"; exit 0;; esac\n'
             'echo "ERROR: Could not connect to clamd" >&2; exit 2\n')
fake_program("clamscan", 'case "$1" in --version) echo "ClamAV 1.4.3/27800/Wed Nov  4 09:21:00 2026"; exit 0;; esac\n'
             'for last; do :; done\n'
             'hits=$(grep -rl EVIL "$last" 2>/dev/null)\n'
             'if [ -n "$hits" ]; then for h in $hits; do echo "$h: Win.Test.EVIL FOUND"; done; exit 1; fi\nexit 0\n')
fake_program("wine", 'echo "wine $*"\n')
fake_program("umu-run", 'echo "umu $*"\n')


class FakeApi(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        key = self.headers.get("x-apikey") or self.headers.get("Authorization") or ""
        if "bad" in key:
            return self._send(401, {"error": {"code": "WrongCredentialsError", "message": "Wrong API key"}})
        if self.path.startswith("/vt/files/"):
            sha = self.path.rsplit("/", 1)[1]
            if sha == EICAR_SHA:
                return self._send(200, {"data": {"attributes": {
                    "last_analysis_stats": {"malicious": 60, "suspicious": 0, "undetected": 10, "harmless": 0},
                    "popular_threat_classification": {"suggested_threat_label": "eicar/test"}}}})
            if sha.startswith("0"):
                return self._send(429, {"error": {"code": "QuotaExceededError", "message": "Quota exceeded"}})
            return self._send(404, {"error": {"code": "NotFoundError", "message": "not found"}})
        if self.path.startswith("/vt/analyses/"):
            return self._send(200, {"data": {"attributes": {"status": "completed", "stats": {
                "malicious": 1, "suspicious": 0, "undetected": 70, "harmless": 0}}}})
        if self.path.startswith("/ar/analysis/"):
            return self._send(200, {"data": {"analysis": {"scores": {"verdict": {
                "threatLevel": 0, "threatLevelText": "No threats detected"}}}}})
        self._send(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if self.path == "/vt/files":
            assert b'name="file"' in raw
            return self._send(200, {"data": {"type": "analysis", "id": "abc"}})
        if self.path == "/ar/intelligence/api/search":
            query = json.loads(raw)["query"]
            if EICAR_SHA in query:
                return self._send(200, {"summary": {"threatLevel": 2, "tags": ["eicar"]},
                                        "sourceTasks": [{"related": "https://app.any.run/tasks/1"}]})
            return self._send(200, {"summary": {}, "sourceTasks": []})
        if self.path == "/ar/analysis":
            assert b'name="obj_type"' in raw and b"windows" in raw
            return self._send(200, {"error": False, "data": {"taskid": "task-42"}})
        self._send(404, {})


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        scanners.VirusTotal.BASE = base + "/vt"
        scanners.AnyRun.API = base + "/ar"
        scanners._wait = lambda seconds, cancel: True
        cls.files = TMP / "files"
        cls.files.mkdir()
        (cls.files / "good.exe").write_bytes(b"MZ good program")
        (cls.files / "evil.exe").write_bytes(b"MZ EVIL program")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_clamav(self):
        self.assertEqual(scanners.clamav_scan(self.files / "good.exe").status, scanners.CLEAN)
        bad = scanners.clamav_scan(self.files / "evil.exe")
        self.assertEqual(bad.status, scanners.MALICIOUS)
        self.assertIn("Win.Test.EVIL", bad.summary)
        hits = []
        code, _ = scanners.clamav_scan_tree(self.files, lambda f, t: hits.append((f, t)))
        self.assertEqual(code, 1)
        self.assertEqual([Path(f).name for f, _ in hits], ["evil.exe"])
        version = scanners.clamav_version()
        self.assertTrue(version.startswith("ClamAV 1.4.3"))
        self.assertIsNotNone(scanners.signature_age_days(version))

    def test_virustotal(self):
        vt = scanners.VirusTotal("good-key", 3)
        f = vt.lookup(EICAR_SHA)
        self.assertEqual(f.status, scanners.MALICIOUS)
        self.assertIn("60 of 70", f.summary)
        self.assertEqual(vt.lookup("f" * 64).status, scanners.UNKNOWN)
        self.assertIn("limit", vt.lookup("0" * 64).summary)
        self.assertIn("rejected", scanners.VirusTotal("bad-key").lookup("f" * 64).summary)
        self.assertEqual(scanners.VirusTotal("").lookup("f" * 64).status, scanners.NOT_SET_UP)
        up = vt.upload_and_wait(self.files / "good.exe", "f" * 64)
        self.assertEqual(up.status, scanners.SUSPICIOUS)  # 1 engine: below the block threshold

    def test_anyrun(self):
        ar = scanners.AnyRun("good-key")
        self.assertEqual(ar.auth, "API-KEY good-key")
        f = ar.lookup(EICAR_SHA)
        self.assertEqual(f.status, scanners.MALICIOUS)
        self.assertEqual(f.link, "https://app.any.run/tasks/1")
        self.assertEqual(ar.lookup("f" * 64).status, scanners.UNKNOWN)
        sb = ar.submit_and_wait(self.files / "good.exe", "bylink", 60)
        self.assertEqual(sb.status, scanners.CLEAN)
        self.assertEqual(sb.link, "https://app.any.run/tasks/task-42")
        self.assertEqual(scanners.AnyRun("").lookup("f" * 64).status, scanners.NOT_SET_UP)

    def test_decide(self):
        F = Finding
        self.assertEqual(decide([F("ClamAV", "malicious", "x")]).level, "block")
        self.assertEqual(decide([F("ClamAV", "clean", ""), F("VirusTotal", "malicious", "x")]).level, "block")
        self.assertEqual(decide([F("ClamAV", "clean", ""), F("VirusTotal", "suspicious", "x")]).level, "warn")
        self.assertEqual(decide([F("ClamAV", "clean", ""), F("VirusTotal", "not_set_up", ""),
                                 F("ANY.RUN", "not_set_up", "")]).level, "clean")
        self.assertEqual(decide([F("ClamAV", "clean", ""), F("VirusTotal", "unknown", ""),
                                 F("ANY.RUN", "not_set_up", "")]).level, "warn")
        self.assertEqual(decide([F("ClamAV", "error", "x")]).level, "warn")
        self.assertEqual(decide([F("ClamAV", "clean", ""), F("VirusTotal", "clean", ""),
                                 F("ANY.RUN", "clean", "")]).level, "clean")

    def test_check_file_and_cache(self):
        sec = dict(store.SECURITY_DEFAULTS, virustotal_key="good-key", anyrun_key="good-key")
        seen = []
        sha, findings = checks.check_file(self.files / "good.exe", sec, on_update=seen.append)
        self.assertEqual([f.engine for f in findings], ["ClamAV", "VirusTotal", "ANY.RUN"])
        self.assertEqual(findings[0].status, scanners.CLEAN)
        self.assertEqual(findings[1].status, scanners.UNKNOWN)
        self.assertEqual(seen[0].status, checks.RUNNING)
        _, again = checks.check_file(self.files / "good.exe", sec)
        self.assertIn("checked recently", again[1].summary)
        self.assertEqual(decide(again).level, "warn")

    def test_exe_settings_and_runner(self):
        exe = self.files / "good.exe"
        self.assertEqual(store.effective_runner(exe), "wine")
        store.set_exe_settings(exe, runner="proton", launch_options="DXVK_HUD=1 gamemoderun %command% -windowed")
        self.assertEqual(store.effective_runner(exe), "proton")
        data = json.loads(store.EXE_FILE.read_text())
        self.assertEqual(data["files"][os.path.realpath(exe)]["runner"], "proton")
        argv, env, cwd = launcher.build_command(exe)
        self.assertEqual(argv[0], "gamemoderun")
        self.assertTrue(argv[1].endswith("umu-run"))
        self.assertEqual(argv[2:], [os.path.realpath(exe), "-windowed"])
        self.assertEqual(env["DXVK_HUD"], "1")
        self.assertEqual(env["GAMEID"], "umu-default")
        self.assertIn(env["PROTONPATH"], ("GE-Proton",) + tuple(str(p) for p in [launcher.builtin_proton()] if p))
        self.assertTrue(env["WINEPREFIX"].endswith("prefixes/proton-default"))
        store.set_exe_settings(exe, runner="wine", prefix="separate", launch_options="")
        argv, env, cwd = launcher.build_command(exe)
        self.assertTrue(argv[0].endswith("wine"))
        self.assertIn("prefixes/wine-good-", env["WINEPREFIX"])
        self.assertEqual(cwd, str(self.files.resolve()))
        msi = self.files / "setup.msi"
        msi.write_bytes(b"x")
        argv, _, _ = launcher.build_command(msi, runner="wine")
        self.assertEqual(argv[1:3], ["msiexec", "/i"])

    def test_parse_launch_options(self):
        p = launcher.parse_launch_options
        self.assertEqual(p(""), ({}, [], []))
        self.assertEqual(p("-dx11 -nointro"), ({}, [], ["-dx11", "-nointro"]))
        self.assertEqual(p("PROTON_LOG=1 -dx11"), ({"PROTON_LOG": "1"}, [], ["-dx11"]))
        self.assertEqual(p("A=1 mangohud %command% --x"), ({"A": "1"}, ["mangohud"], ["--x"]))

    def test_quarantine(self):
        f = TMP / "q.exe"
        f.write_bytes(b"MZ EVIL")
        item = store.quarantine(f, "ab" * 32, "Win.Test.EVIL")
        self.assertFalse(f.exists())
        self.assertEqual(len(store.quarantine_items()), 1)
        restored = store.restore_quarantined(item["id"])
        self.assertTrue(Path(restored).exists())
        item = store.quarantine(restored, "ab" * 32, "again")
        store.delete_quarantined(item["id"])
        self.assertEqual(store.quarantine_items(), [])

    def test_guard_text_mode(self):
        started = []
        orig = launcher.launch
        launcher.launch = lambda *a, **k: started.append(a[0]) or 0
        try:
            store.save_security(dict(store.SECURITY_DEFAULTS))
            code = guard.run_text((self.files / "evil.exe").resolve(), [], False)
            self.assertEqual(code, 1)  # blocked, stdin is not a terminal: never started
            code = guard.run_text((self.files / "good.exe").resolve(), [], False)
            self.assertEqual(code, 0)
            self.assertEqual(len(started), 1)
        finally:
            launcher.launch = orig
        results = [h["result"] for h in store.read_history()]
        self.assertIn("block", results)

    def test_checked_only_on_the_first_start(self):
        from myos_security import scan_cli
        game = (self.files / "game.exe").resolve()
        game.write_bytes(b"MZ a game")
        started, checked = [], []
        orig = (launcher.launch, guard.check_file)
        launcher.launch = lambda *a, **k: started.append(a[0]) or 0

        def counting_check(*a, **k):
            checked.append(a[0])
            return orig[1](*a, **k)
        guard.check_file = counting_check
        shown = {k: os.environ.pop(k) for k in ("DISPLAY", "WAYLAND_DISPLAY") if k in os.environ}
        try:
            store.save_security(dict(store.SECURITY_DEFAULTS))
            self.assertFalse(store.checked_before(game))
            self.assertEqual(guard.main([str(game)]), 0)                        # first start: checked
            self.assertEqual((len(checked), len(started)), (1, 1))
            self.assertTrue(store.checked_before(game))
            self.assertTrue(store.when_checked(game).startswith("20"))
            history = len(store.read_history())
            self.assertEqual(guard.main([str(game)]), 0)                        # second start: straight away
            self.assertEqual((len(checked), len(started)), (1, 2))
            self.assertEqual(len(store.read_history()), history)                # nothing new to report
            with open(game, "ab") as f:                                          # an update
                f.write(b" version 2")
            self.assertFalse(store.checked_before(game))
            guard.main([str(game)])
            self.assertEqual((len(checked), len(started)), (2, 3))              # checked once more
            guard.main([str(game)])
            self.assertEqual(len(checked), 2)
            store.save_security({**store.SECURITY_DEFAULTS, "check_every_start": True})
            guard.main([str(game)])
            self.assertEqual(len(checked), 3)                                   # the setting for every start
            store.save_security(dict(store.SECURITY_DEFAULTS))

            evil = (self.files / "evil.exe").resolve()
            self.assertEqual(guard.main([str(evil)]), 1)                        # dangerous: not started
            self.assertFalse(store.checked_before(evil))                        # so checked every time
            guard.main([str(evil)])
            self.assertEqual(len(checked), 5)

            store.remember_checked(evil, "x", "clean")                           # say it was fine once
            self.assertTrue(store.checked_before(evil))
            scan_cli.main(["--cli", str(evil)])                                  # you scan it: found
            self.assertFalse(store.checked_before(evil))
            store.remember_checked(evil, "x", "clean")
            scan_cli.main(["--cli", str(self.files)])                            # a folder scan finds it
            self.assertFalse(store.checked_before(evil))

            fresh = (self.files / "tool.exe").resolve()
            fresh.write_bytes(b"MZ a tool")
            scan_cli.main(["--cli", str(fresh)])                                 # scanned clean by you
            self.assertTrue(store.checked_before(fresh))
            n = len(checked)
            guard.main([str(fresh)])                                             # counts as its check
            self.assertEqual(len(checked), n)
            self.assertEqual(guard.main(["--scan-only", str(fresh)]), 0)        # a scan you ask for always runs
            self.assertEqual(len(checked), n + 1)
        finally:
            launcher.launch, guard.check_file = orig
            os.environ.update(shown)
            store.save_security(dict(store.SECURITY_DEFAULTS))

    def test_double_click_runs_without_asking(self):
        import configparser
        here = Path(__file__).resolve().parents[1]
        kiorc = configparser.ConfigParser()
        kiorc.read(here / "data" / "kiorc")
        self.assertEqual(kiorc["Executable scripts"]["behaviourOnLaunch"], "execute")   # as KDE writes it
        self.assertIn('"$HERE/data/kiorc" "$DESTDIR/etc/xdg/kiorc"', (here / "install.sh").read_text())

    def test_record_of_checked_programs(self):
        a = (self.files / "a.exe").resolve()
        a.write_bytes(b"MZ a")
        store.remember_checked(a, "sha", "clean")
        record = json.loads(store.CHECKED_FILE.read_text())["files"][str(a)]
        self.assertEqual(set(record), {"inode", "size", "modified", "sha256", "result", "time"})   # no device number
        replaced = self.files / "a.new"
        replaced.write_bytes(b"MZ a")                                            # same size, other file
        os.replace(replaced, a)
        self.assertFalse(store.checked_before(a))
        store.remember_checked(a, "sha", "clean")
        b = (self.files / "b.exe").resolve()
        b.write_bytes(b"MZ b")
        store.remember_checked(b, "sha", "clean")
        b.unlink()
        store.remember_checked(a, "sha", "clean")                               # removed programs are forgotten
        self.assertNotIn(str(b), json.loads(store.CHECKED_FILE.read_text())["files"])
        store.forget_checked(a)
        self.assertFalse(store.checked_before(a))
        store.forget_checked(self.files / "never.exe")                           # nothing to forget: fine
        self.assertFalse(store.checked_before(self.files / "never.exe"))
        self.assertEqual(store.when_checked(self.files / "never.exe"), "")


if __name__ == "__main__":
    unittest.main()
