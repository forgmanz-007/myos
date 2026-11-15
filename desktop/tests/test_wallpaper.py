"""Tests for myos-set-wallpaper (pictures and video wallpapers).
Run: python3 -m unittest discover desktop/tests
Uses fake busctl / plasma-apply-wallpaperimage / notify-send programs and a local web server."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wallpaper-test-")).resolve()
HOME = TMP / "home"
HOME.mkdir()
SHARE = TMP / "share"
os.environ.update({"HOME": str(HOME), "XDG_DATA_HOME": str(HOME / ".local/share"),
                   "XDG_DATA_DIRS": str(SHARE), "FAKE_LOG": str(TMP / "calls.log")})
BIN = TMP / "bin"
BIN.mkdir()
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"

for name, body in {
    "busctl": 'printf "%s\\n" "$@" > "$FAKE_LOG.busctl"\nexit "${FAKE_BUSCTL_EXIT:-0}"',
    "plasma-apply-wallpaperimage": 'exit 0',
    "notify-send": 'exit 0',
    "xdg-user-dir": f'case "$1" in VIDEOS) echo "{HOME}/Videos" ;; PICTURES) echo "{HOME}/Pictures" ;; esac',
}.items():
    p = BIN / name
    p.write_text(f"#!/bin/sh\necho \"{name} $*\" >> \"$FAKE_LOG\"\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "myos-set-wallpaper"
loader = importlib.machinery.SourceFileLoader("set_wallpaper", str(SCRIPT))
spec = importlib.util.spec_from_loader("set_wallpaper", loader)
wp = importlib.util.module_from_spec(spec)
loader.exec_module(wp)

PLUGIN_DIR = SHARE / "plasma/wallpapers" / wp.PLUGIN


def calls():
    log = TMP / "calls.log"
    return log.read_text() if log.exists() else ""


def busctl_args():
    return (TMP / "calls.log.busctl").read_text().split("\n")


class Server(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        pages = {"/clips/rain%20loop.mp4": ("video/mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 5000),
                 "/download?id=7": ("video/webm", b"\x1aE\xdf\xa3" + b"1" * 3000),
                 "/page": ("text/html", b"<html>hi</html>"),
                 "/pic.png": ("image/png", b"\x89PNG\r\n\x1a\n" + b"2" * 100)}
        if self.path not in pages:
            self.send_error(404)
            return
        ctype, body = pages[self.path]
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WallpaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.video = HOME / "My Videos" / "ocean waves.mp4"
        cls.video.parent.mkdir()
        cls.video.write_bytes(b"fake video")
        cls.picture = HOME / "sunset.jpg"
        cls.picture.write_bytes(b"fake picture")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        for f in TMP.glob("calls.log*"):
            f.unlink()
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        (PLUGIN_DIR / "metadata.json").write_text("{}")
        os.environ.pop("FAKE_BUSCTL_EXIT", None)

    def test_kinds(self):
        self.assertEqual(wp.kind_of(Path("a.MP4")), "video")
        self.assertEqual(wp.kind_of(Path("a.gif")), "video")
        self.assertEqual(wp.kind_of(Path("a.webp")), "image")
        self.assertEqual(wp.kind_of(Path("a.txt")), "")

    def test_video_wallpaper(self):
        msg = wp.set_wallpaper(str(self.video))
        self.assertIn("Video wallpaper set: ocean waves.mp4", msg)
        args = busctl_args()
        self.assertEqual(args[:7], ["--user", "call", "org.kde.plasmashell", "/PlasmaShell",
                                    "org.kde.PlasmaShell", "evaluateScript", "s"])
        script = "\n".join(args[7:])
        self.assertIn(self.video.as_uri(), script)
        self.assertIn("%20", self.video.as_uri())
        self.assertIn('"MuteMode": 5', script)
        self.assertIn('"PauseMode": 0', script)
        msg = wp.set_wallpaper(str(self.video), sound=True, fit=True, always_play=True)
        script = "\n".join(busctl_args()[7:])
        for setting in ('"MuteMode": 4', '"FillMode": 1', '"PauseMode": 3'):
            self.assertIn(setting, script)
        self.assertIn("with sound", msg)

    def test_video_on_a_pc_without_graphics_acceleration(self):
        tool = BIN / "myos-performance"
        tool.write_text('#!/bin/sh\necho "myos-performance $*" >> "$FAKE_LOG"\necho "${FAKE_GRAPHICS:-weak}"\n')
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
        try:
            msg = wp.set_wallpaper(str(self.video))
            self.assertIn("myos-performance graphics", calls())
            self.assertIn('"PauseMode": 2', "\n".join(busctl_args()[7:]))      # plays only on the bare desktop
            self.assertIn("pauses while any window is open", msg)
            self.assertIn("without graphics acceleration", msg)
            self.assertIn("myos-set-wallpaper /usr/share/wallpapers/myos.svg", msg)
            wp.set_wallpaper(str(self.video), always_play=True)                  # your choice wins
            self.assertIn('"PauseMode": 3', "\n".join(busctl_args()[7:]))
            self.assertEqual(wp.main(["--notify", str(self.video)]), 0)          # right-click menu: say it too
            self.assertIn("notify-send -a Wallpaper", calls())
            self.assertNotIn("without graphics", wp.set_wallpaper(str(self.picture)))
            os.environ["FAKE_GRAPHICS"] = "ok"                                   # a real graphics card
            msg = wp.set_wallpaper(str(self.video))
            self.assertIn('"PauseMode": 0', "\n".join(busctl_args()[7:]))
            self.assertNotIn("without graphics acceleration", msg)
        finally:
            os.environ.pop("FAKE_GRAPHICS", None)
            tool.unlink()

    def test_needs_plugin(self):
        shutil.rmtree(PLUGIN_DIR)
        with self.assertRaises(wp.WallpaperError) as e:
            wp.set_wallpaper(str(self.video))
        self.assertIn("Smart Video Wallpaper Reborn", str(e.exception))

    def test_picture(self):
        self.assertIn("Wallpaper set: sunset.jpg", wp.set_wallpaper(str(self.picture)))
        self.assertIn(f"plasma-apply-wallpaperimage {self.picture}", calls())
        wp.set_wallpaper(str(self.picture), fit=True)
        script = "\n".join(busctl_args()[7:])
        self.assertIn('"org.kde.image"', script)
        self.assertIn('writeConfig("FillMode", 1)', script)

    def test_downloads(self):
        msg = wp.set_wallpaper(self.url + "/clips/rain%20loop.mp4")
        first = HOME / "Videos/Wallpapers/rain loop.mp4"
        self.assertTrue(first.exists(), msg)
        wp.set_wallpaper(self.url + "/clips/rain%20loop.mp4")
        self.assertTrue((HOME / "Videos/Wallpapers/rain loop-2.mp4").exists())
        wp.set_wallpaper(self.url + "/download?id=7")
        self.assertTrue((HOME / "Videos/Wallpapers/download.webm").exists())
        wp.set_wallpaper(self.url + "/pic.png")
        self.assertTrue((HOME / "Pictures/Wallpapers/pic.png").exists())
        with self.assertRaises(wp.WallpaperError) as e:
            wp.set_wallpaper(self.url + "/page")
        self.assertIn("not a picture or video", str(e.exception))
        with self.assertRaises(wp.WallpaperError) as e:
            wp.set_wallpaper("https://www.youtube.com/watch?v=abc")
        self.assertIn("yt-dlp", str(e.exception))
        with self.assertRaises(wp.WallpaperError):
            wp.set_wallpaper(self.url + "/missing.mp4")

    def test_errors_and_notify(self):
        os.environ["FAKE_BUSCTL_EXIT"] = "1"
        self.assertEqual(wp.main(["--notify", str(self.video)]), 1)
        self.assertIn("notify-send -a Wallpaper", calls())
        self.assertEqual(wp.main([str(HOME / "nope.mp4")]), 1)
        self.assertEqual(wp.main([str(HOME / "My Videos")]), 1)

    @unittest.skipUnless(shutil.which("osascript"), "needs a JavaScript engine (macOS osascript)")
    def test_script_on_desktops(self):
        stub = """
var log = [];
function Desktop(n) { this.n = n; this.cfg = {}; this.currentConfigGroup = []; this.reloaded = false; }
Desktop.prototype.writeConfig = function (k, v) { this.cfg[this.currentConfigGroup.join("/") + ":" + k] = v; };
Desktop.prototype.reloadConfig = function () { this.reloaded = true; };
var all = [new Desktop(1), new Desktop(2)];
function desktops() { return all; }
"""
        js = stub + wp.video_script("file:///home/u/a%20b.mp4") + """
JSON.stringify(all.map(function (d) { return [d.wallpaperPlugin, d.reloaded, d.cfg]; }));"""
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True,
                             text=True)
        result = json.loads(out.stdout)
        group = f"Wallpaper/{wp.PLUGIN}/General:"
        for plugin, reloaded, cfg in result:
            self.assertEqual(plugin, wp.PLUGIN)
            self.assertTrue(reloaded)
            videos = json.loads(cfg[group + "VideoUrls"])
            self.assertEqual(videos, [{"filename": "file:///home/u/a%20b.mp4", "enabled": True,
                                       "duration": 0, "customDuration": 0, "playbackRate": 0,
                                       "alternativePlaybackRate": 0, "loop": True,
                                       "dayNightCycleAssignment": 0}])
            self.assertEqual(cfg[group + "MuteMode"], 5)
            self.assertEqual(cfg[group + "LastVideo"], "file:///home/u/a%20b.mp4")


if __name__ == "__main__":
    unittest.main()
