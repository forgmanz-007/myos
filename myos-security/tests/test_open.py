"""kde-open in /usr/local/bin: Windows programs that other apps open (Chrome, xdg-open) go to
myos-run-exe; everything else goes to KDE's own kde-open unchanged."""
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
WRAPPER = HERE / "libexec" / "kde-open"


class OpeningFromOtherApps(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="kde-open-")).resolve()
        self.log = self.dir / "log"
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        for name in ("real-kde-open", "myos-run-exe", "systemd-run"):
            p = self.bin / name
            p.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{self.log}"\n')   # printf: echo may eat backslashes
            p.chmod(0o755)
        self.runtime = self.dir / "runtime"
        (self.runtime / "systemd").mkdir(parents=True)
        self.games = self.dir / "My Games"
        self.games.mkdir()
        for name in ("setup.exe", "Installer.MSI", "start.bat", "notes.txt"):
            (self.games / name).write_bytes(b"MZ test")

    def run_it(self, *args, cwd=None, user_manager=False):
        if user_manager:                       # a session with systemd's user manager
            s = socket.socket(socket.AF_UNIX)
            s.bind(str(self.runtime / "systemd" / "private"))
            self.addCleanup(s.close)
        env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin", XDG_RUNTIME_DIR=str(self.runtime),
                   MYOS_REAL_KDE_OPEN=str(self.bin / "real-kde-open"), MYOS_RUN_EXE="myos-run-exe")
        if not user_manager:
            env["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.log.unlink(missing_ok=True)
        r = subprocess.run(["sh", str(WRAPPER), *args], env=env, cwd=cwd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_windows_programs_go_to_the_check(self):
        exe = self.games / "setup.exe"
        self.assertEqual(self.run_it(str(exe)), [f"myos-run-exe {exe}"])
        url = "file://" + str(exe).replace(" ", "%20")
        self.assertEqual(self.run_it(url), [f"myos-run-exe {exe}"])                      # as Chrome passes it
        self.assertEqual(self.run_it("file://localhost" + str(exe).replace(" ", "%20")), [f"myos-run-exe {exe}"])
        self.assertEqual(self.run_it(str(self.games / "Installer.MSI")), [f"myos-run-exe {self.games / 'Installer.MSI'}"])
        self.assertEqual(self.run_it(str(self.games / "start.bat")), [f"myos-run-exe {self.games / 'start.bat'}"])
        self.assertEqual(self.run_it("setup.exe", cwd=self.games), [f"myos-run-exe {exe}"])   # a relative path

    def test_started_as_an_app_of_its_own(self):
        exe = self.games / "setup.exe"
        calls = self.run_it(str(exe), user_manager=True)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("systemd-run --user --scope --quiet --collect --slice=app.slice "
                                            "--unit=app-myos\\x2dexe\\x2drunner-"), calls[0])
        self.assertTrue(calls[0].endswith(f".scope myos-run-exe {exe}"), calls[0])

    def test_everything_else_goes_to_kde_unchanged(self):
        real = "real-kde-open"
        self.assertEqual(self.run_it("https://example.com/setup.exe"), [f"{real} https://example.com/setup.exe"])
        self.assertEqual(self.run_it(str(self.games / "notes.txt")), [f"{real} {self.games / 'notes.txt'}"])
        self.assertEqual(self.run_it(str(self.games / "missing.exe")), [f"{real} {self.games / 'missing.exe'}"])
        self.assertEqual(self.run_it("file://otherhost/setup.exe"), [f"{real} file://otherhost/setup.exe"])
        self.assertEqual(self.run_it("--help"), [f"{real} --help"])
        exe = str(self.games / "setup.exe")
        self.assertEqual(self.run_it("--new-window", exe), [f"{real} --new-window {exe}"])     # options: KDE's business

    def test_installed_ahead_of_kdes_own(self):
        installer = (HERE / "install.sh").read_text()
        self.assertIn('"$HERE/libexec/kde-open" "$DESTDIR/usr/local/bin/kde-open"', installer)
        self.assertTrue(os.access(WRAPPER, os.X_OK))


if __name__ == "__main__":
    unittest.main()
