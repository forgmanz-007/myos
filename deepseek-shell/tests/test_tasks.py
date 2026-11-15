"""Doing jobs: plans and per-task approval, installing apps, scripts and
schedules, shortcuts, and tools the AI makes for itself.
Uses fake flatpak/systemctl/busctl programs, so nothing touches the real system."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="dss-jobs-"))
HOME = TMP / "home"
HOME.mkdir()
os.environ["HOME"] = str(HOME)
os.environ["XDG_DATA_HOME"] = str(HOME / ".local/share")
os.environ["XDG_CONFIG_HOME"] = str(HOME / ".config")
os.environ["XDG_STATE_HOME"] = str(HOME / ".local/state")
os.environ.setdefault("DEEPSEEK_SHELL_SYSCONFDIR", str(TMP / "etc"))
LOG = TMP / "calls.log"
os.environ["FAKE_LOG"] = str(LOG)
BIN = TMP / "bin"
BIN.mkdir()
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_ollama  # noqa: E402
from deepseek_shell import apps, safety, scripts, tools  # noqa: E402
from deepseek_shell.agent import Agent  # noqa: E402
from deepseek_shell.config import Config  # noqa: E402
from deepseek_shell.knowledge import Knowledge  # noqa: E402
from deepseek_shell.ollama import Ollama  # noqa: E402
from deepseek_shell.runner import CommandRunner  # noqa: E402

APPS = HOME / ".local/share/flatpak/exports/share/applications"


def fake(name, body):
    p = BIN / name
    p.write_text("#!/bin/sh\necho \"" + name + " $*\" >> \"$FAKE_LOG\"\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


fake("flatpak", f"""case "$1" in
  info) [ -f "{APPS}/$3.desktop" ] && exit 0; exit 1 ;;
  search) printf 'com.spotify.Client\\tSpotify\\tMusic streaming\\norg.example.Spotiflyer\\tSpotiFlyer\\tMusic downloader\\n' ;;
  install) for a; do id=$a; done; mkdir -p "{APPS}"
           printf '[Desktop Entry]\\nType=Application\\nName=Spotify\\nExec=flatpak run %s\\nIcon=%s\\n' "$id" "$id" > "{APPS}/$id.desktop"
           echo "Installing $id: done" ;;
esac
exit 0
""")
fake("systemctl", """case "$*" in
  *list-timers*) echo "Fri 2026-11-06 18:30:00 CET 20h left - - myscript-backup.timer myscript-backup.service" ;;
esac
exit 0
""")
fake("busctl", "exit 0\n")
fake("dpkg-query", """case "$1" in
  -L) [ "$2" = kate ] && { echo /usr/share/doc/kate; echo /usr/share/applications/org.kde.kate.desktop; exit 0; } ;;
esac
exit 1
""")
fake("xdg-user-dir", f'echo "{HOME}/Desktop"\n')


def calls():
    return LOG.read_text().splitlines() if LOG.exists() else []


class StubRunner:
    """Records commands instead of running them."""
    def __init__(self):
        self.commands = []

    def run(self, command, admin=False, **kw):
        self.commands.append((command, admin))
        return 0, "ok"


def context(config, answer="allow", runner=None, registry=None):
    asked = []

    def approve(req):
        asked.append(req)
        req.respond(answer)
        return answer in ("allow", "task", "always")
    ctx = tools.ToolContext(config=config, runner=runner or CommandRunner(), approve=approve,
                            registry=registry)
    return ctx, asked


class ScheduleTests(unittest.TestCase):
    def check(self, text, label, calendar=None, interval=None):
        s = scripts.parse_schedule(text)
        self.assertEqual(s.label, label, text)
        if calendar:
            self.assertEqual(s.timer.get("OnCalendar"), calendar, text)
        if interval:
            self.assertEqual(s.timer.get("OnUnitActiveSec"), interval, text)

    def test_parse(self):
        self.check("daily at 18:30", "every day at 18:30", "*-*-* 18:30:00")
        self.check("every day at 6pm", "every day at 18:00", "*-*-* 18:00:00")
        self.check("every 15 minutes", "every 15 minutes", interval="15min")
        self.check("every 2 hours", "every 2 hours", interval="2h")
        self.check("hourly", "every hour", "hourly")
        self.check("every Monday at 9:00", "every Monday at 09:00", "Mon *-*-* 09:00:00")
        self.check("weekdays at 8am", "on weekdays at 08:00", "Mon..Fri *-*-* 08:00:00")
        self.check("on weekends at 10", "on weekends at 10:00", "Sat,Sun *-*-* 10:00:00")
        self.check("every friday", "every Friday at 09:00", "Fri *-*-* 09:00:00")
        self.check("every morning", "every day at 09:00", "*-*-* 09:00:00")
        self.check("weekly", "every Monday at midnight (or as soon as the PC is on)", "weekly")
        self.assertEqual(scripts.parse_schedule("at login").kind, "login")
        self.assertEqual(scripts.parse_schedule("when I log in").kind, "login")
        self.assertEqual(scripts.parse_schedule("none").kind, "remove")
        self.assertIsNone(scripts.parse_schedule(""))
        for bad in ("whenever you feel like it", "at 25:00", "every 0 minutes"):
            with self.assertRaises(ValueError, msg=bad):
                scripts.parse_schedule(bad)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        LOG.unlink(missing_ok=True)

    def test_bash_script_with_menu_and_schedule(self):
        res = scripts.save_script("Backup", 'echo "backing up"\n', "Copies my documents",
                                  start_menu=True, schedule=scripts.parse_schedule("daily at 18:30"))
        path = HOME / "Scripts/backup.sh"
        self.assertEqual(res.path, path)
        text = path.read_text()
        self.assertTrue(text.startswith("#!/bin/bash\n# Copies my documents\n# Made with DeepSeek Shell"))
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(res.syntax_error, "")
        menu = (HOME / ".local/share/applications/myscript-backup.desktop").read_text()
        self.assertIn(f"Exec=deepseek-shell --run-script {path}", menu)
        self.assertIn("Name=Backup", menu)
        timer = (HOME / ".config/systemd/user/myscript-backup.timer").read_text()
        service = (HOME / ".config/systemd/user/myscript-backup.service").read_text()
        self.assertIn("OnCalendar=*-*-* 18:30:00\nPersistent=true", timer)
        self.assertIn(f'ExecStart="{path}"', service)
        self.assertIn("$$SERVICE_RESULT", service)
        self.assertIn("systemctl --user enable --now myscript-backup.timer", calls())
        message = scripts.describe(res)
        self.assertIn("every day at 18:30", message)
        self.assertIn("Next run: Fri 2026-11-06 18:30:00", message)

    def test_python_detection_and_syntax_error(self):
        good = scripts.save_script("sorter", "import os\nprint(os.getcwd())\n")
        self.assertEqual(good.language, "python")
        self.assertTrue(good.path.read_text().startswith("#!/usr/bin/env python3\n# Made with"))
        bad = scripts.save_script("broken", "#!/usr/bin/env python3\ndef x(:\n    pass\n",
                                  start_menu=True, schedule=scripts.parse_schedule("hourly"))
        self.assertTrue(bad.syntax_error)
        self.assertFalse((HOME / ".local/share/applications/myscript-broken.desktop").exists())
        self.assertFalse((HOME / ".config/systemd/user/myscript-broken.timer").exists())
        self.assertIn("has a mistake", scripts.describe(bad))
        bash_bad = scripts.save_script("oops", "if then fi\n")
        self.assertTrue(bash_bad.syntax_error)

    def test_update_keeps_backup_and_changes_schedule(self):
        scripts.save_script("cleaner", "echo 1\n", schedule=scripts.parse_schedule("hourly"))
        res = scripts.save_script("cleaner", "echo 2\n", schedule=scripts.parse_schedule("at login"))
        self.assertTrue(res.backup and Path(res.backup).exists())
        self.assertFalse((HOME / ".config/systemd/user/myscript-cleaner.timer").exists())
        auto = HOME / ".config/autostart/myscript-cleaner.desktop"
        self.assertTrue(auto.exists())
        self.assertEqual(res.path.read_text().count("Made with DeepSeek Shell"), 1)
        scripts.save_script("cleaner", "echo 3\n", schedule=scripts.parse_schedule("none"))
        self.assertFalse(auto.exists())

    def test_create_script_tool(self):
        cfg = Config()
        ctx, asked = context(cfg)
        result = tools.t_create_script({"name": "hello", "content": "echo hi", "start_menu": True,
                                        "schedule": "every 10 minutes", "description": "Says hi"}, ctx)
        self.assertEqual(len(asked), 1)
        self.assertIn("Run it automatically: every 10 minutes", asked[0].detail)
        self.assertIn("no syntax errors", result)
        ctx, asked = context(cfg, answer="deny")
        result = tools.t_create_script({"name": "wipe", "content": "rm -rf ~\n"}, ctx)
        self.assertEqual(asked[0].level, safety.DANGER)
        self.assertIn("did NOT allow", result)
        self.assertFalse((HOME / "Scripts/wipe.sh").exists())
        with self.assertRaises(tools.ToolError):
            tools.t_create_script({"name": "x", "content": "echo", "schedule": "sometimes"}, ctx)


class AppTests(unittest.TestCase):
    def setUp(self):
        LOG.unlink(missing_ok=True)
        shutil.rmtree(APPS, ignore_errors=True)

    def test_resolve_and_pick(self):
        self.assertEqual(apps.resolve_known("Spotify")["id"], "com.spotify.Client")
        self.assertEqual(apps.resolve_known("the OBS app")["id"], "com.obsproject.Studio")
        self.assertEqual(apps.resolve_known("File Explorer"),
                         {"source": "debian", "id": "dolphin", "name": "File Explorer (Dolphin)"})
        self.assertIsNone(apps.resolve_known("some unknown thing"))
        results = [("com.spotify.Client", "Spotify", ""), ("org.example.Spotiflyer", "SpotiFlyer", "")]
        self.assertEqual(apps.pick_match("spotiflyer", results), ("org.example.Spotiflyer", "SpotiFlyer"))
        self.assertIsNone(apps.pick_match("spoti", results))

    def test_install_from_flathub(self):
        cfg = Config()
        ctx, asked = context(cfg)
        result = tools.t_install_app({"app": "Spotify"}, ctx)
        self.assertIn("Installed Spotify (com.spotify.Client)", result)
        self.assertEqual(len(asked), 1)
        self.assertFalse(asked[0].admin)
        self.assertIn("flatpak install --user -y --noninteractive flathub com.spotify.Client", calls())
        self.assertIn("already installed", tools.t_install_app({"app": "spotify"}, ctx))

    def test_install_by_search(self):
        cfg = Config()
        ctx, _ = context(cfg)
        self.assertIn("Several apps match", tools.t_install_app({"app": "spoti"}, ctx))
        self.assertIn("org.example.Spotiflyer", tools.t_install_app({"app": "SpotiFlyer"}, ctx))

    def test_app_already_in_the_start_menu_is_not_installed_again(self):
        menu = HOME / ".local/share/applications"
        menu.mkdir(parents=True, exist_ok=True)
        (menu / "myos-taskmanager.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Task Manager\nExec=myos-taskmanager\n")
        (menu / "hidden-tool.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=SpotiFlyer\nExec=x\nNoDisplay=true\n")
        try:
            ctx, asked = context(Config())
            result = tools.t_install_app({"app": "task manager"}, ctx)
            self.assertIn("Task Manager is already installed", result)
            self.assertEqual((asked, calls()), ([], []))        # nothing asked, nothing downloaded
            self.assertIsNone(apps.resolve_known("task manager"))
            self.assertEqual(apps.resolve_known("system monitor")["id"], "plasma-systemmonitor")
            # an entry hidden from the Start menu does not count
            self.assertIn("org.example.Spotiflyer", tools.t_install_app({"app": "SpotiFlyer"}, ctx))
        finally:
            (menu / "myos-taskmanager.desktop").unlink()
            (menu / "hidden-tool.desktop").unlink()

    def test_install_debian_package(self):
        cfg = Config()
        runner = StubRunner()
        ctx, asked = context(cfg, runner=runner)
        result = tools.t_install_app({"app": "File Explorer"}, ctx)
        self.assertEqual(runner.commands, [("apt-get install -y dolphin", True)])
        self.assertTrue(asked[0].admin)
        self.assertIn("Installed File Explorer", result)

    def test_permission_denied_points_to_admin(self):
        # Seen in the test VM: a small model retried "mkdir /etc/..." without admin many times.
        class Denied(StubRunner):
            def run(self, command, admin=False, **kw):
                self.commands.append((command, admin))
                return 1, "mkdir: cannot create directory '/etc/x': Permission denied"
        ctx, _ = context(Config(), runner=Denied())
        self.assertIn("admin=true", tools.t_run_command({"command": "mkdir -p /etc/x"}, ctx))
        ctx, _ = context(Config(), runner=StubRunner())
        self.assertNotIn("admin=true", tools.t_run_command({"command": "mkdir -p /tmp/x"}, ctx))

    def test_find_app_by_everyday_name(self):
        # Seen in the test VM: right after "Installed Notepad (Kate)", open_app said
        # "No installed app called 'Notepad'", and the AI went round in circles.
        menu = HOME / ".local/share/applications"
        menu.mkdir(parents=True, exist_ok=True)
        kate = menu / "org.kde.kate.desktop"
        kate.write_text("[Desktop Entry]\nType=Application\nName=Kate\n"
                        "GenericName=Advanced Text Editor\nExec=kate %U\n")
        try:
            self.assertEqual(apps.find_app("Notepad")[0], "org.kde.kate.desktop")
            self.assertEqual(apps.find_app("kate")[0], "org.kde.kate.desktop")
            self.assertIsNone(apps.find_app("Calculator"))  # known name, but not installed
        finally:
            kate.unlink()

    def test_shortcuts(self):
        cfg = Config()
        ctx, _ = context(cfg)
        tools.t_install_app({"app": "Spotify"}, ctx)
        self.assertIn("Pinned Spotify", apps.make_shortcut("spotify", "taskbar"))
        pin = LOG.read_text().rsplit("busctl ", 1)[1]  # the script spans several lines
        self.assertIn('"applications:com.spotify.Client.desktop"', pin)
        self.assertIn("evaluateScript s", pin)
        apps.make_shortcut("Spotify", "startup")
        self.assertTrue((HOME / ".config/autostart/com.spotify.Client.desktop").exists())
        self.assertIn("already in the Start menu", apps.make_shortcut("spotify", "start_menu"))
        msg = apps.make_shortcut("https://www.youtube.com/watch?v=1&t=2", "desktop")
        link = (HOME / "Desktop/youtube-com.desktop")
        self.assertIn("Type=Link", link.read_text())
        self.assertTrue(os.access(link, os.X_OK), msg)
        apps.make_shortcut("https://example.org/a b%20c", "start_menu", "Example")
        entry = (HOME / ".local/share/applications/myshortcut-example.desktop").read_text()
        self.assertIn('Exec=xdg-open "https://example.org/a b%%20c"', entry)
        docs = HOME / "Documents"
        docs.mkdir(exist_ok=True)
        apps.make_shortcut(str(docs), "desktop")
        self.assertIn(f"URL={docs.as_uri()}", (HOME / "Desktop/documents.desktop").read_text())
        scripts.save_script("tidy", "echo tidy\n")
        apps.make_shortcut("tidy", "taskbar")
        self.assertTrue((HOME / ".local/share/applications/myscript-tidy.desktop").exists())
        with self.assertRaises(ValueError):
            apps.make_shortcut("no such app anywhere", "desktop")
        with self.assertRaises(ValueError):
            apps.make_shortcut("spotify", "fridge")

    @unittest.skipUnless(shutil.which("osascript"), "needs a JavaScript engine (macOS osascript)")
    def test_pin_script_javascript(self):
        stub = """
var written = null, reloaded = false;
function Tasks(value) { this.value = value; this.currentConfigGroup = []; }
Tasks.prototype.readConfig = function (k, d) { return this.value; };
Tasks.prototype.writeConfig = function (k, v) { written = v; };
Tasks.prototype.reloadConfig = function () { reloaded = true; };
var current = new Tasks(%s);
function panels() { return [{ widgets: function (t) { return t === "org.kde.plasma.icontasks" ? [current] : []; } }]; }
"""
        for initial in ('["applications:firefox.desktop"]', '"applications:firefox.desktop"'):
            js = (stub % initial) + (apps.PIN_SCRIPT % '"applications:x.desktop"') + \
                "JSON.stringify([written, reloaded]);"
            out = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True,
                                 text=True).stdout.strip()
            self.assertEqual(out, '[["applications:firefox.desktop","applications:x.desktop"],true]')


class TrustTests(unittest.TestCase):
    def test_allow_all_for_this_task(self):
        cfg = Config()
        cfg["approval"] = "smart"
        ctx, asked = context(cfg, answer="task")
        self.assertTrue(ctx.ask(tools.ApprovalRequest("t", "first", "x")))
        self.assertTrue(ctx.trust["task"])
        self.assertTrue(ctx.ask(tools.ApprovalRequest("t", "second", "y")))
        self.assertEqual(len(asked), 1)  # the second one was not asked
        ctx.ask(tools.ApprovalRequest("t", "danger", "rm -rf /", level=safety.DANGER))
        self.assertEqual(len(asked), 2)  # dangerous commands still ask
        self.assertFalse(asked[1].allow_task)

    def test_always_mode_ignores_task_trust(self):
        cfg = Config()
        cfg["approval"] = "always"
        ctx, asked = context(cfg, answer="task")
        ctx.ask(tools.ApprovalRequest("t", "a", "x"))
        ctx.ask(tools.ApprovalRequest("t", "b", "y"))
        self.assertEqual(len(asked), 2)
        self.assertFalse(ctx.trust.get("task"))

    def test_propose_plan(self):
        cfg = Config()
        cfg["approval"] = "smart"
        ctx, asked = context(cfg, answer="deny")
        self.assertIn("did NOT approve", tools.t_propose_plan(
            {"summary": "Do it", "steps": ["one", "two"]}, ctx))
        ctx, asked = context(cfg, answer="task")
        result = tools.t_propose_plan({"summary": "Do it", "steps": "1. one\n2. two"}, ctx)
        self.assertIn("approved the plan", result)
        self.assertEqual(asked[0].kind, "plan")
        self.assertEqual(asked[0].detail, "1. one\n2. two")
        self.assertTrue(ctx.trust["task"])
        cfg["approval"] = "never"
        ctx, asked = context(cfg)
        self.assertIn("No confirmation needed", tools.t_propose_plan({"steps": ["x"]}, ctx))
        self.assertEqual(asked, [])

    def test_create_tool(self):
        cfg = Config()
        cfg["approval"] = "smart"
        registry = tools.ToolRegistry(cfg)
        ctx, asked = context(cfg, registry=registry)
        result = tools.t_create_tool({"name": "say_hello", "description": "Greets someone",
                                      "command": "echo hello {who}",
                                      "parameters": [{"name": "who", "type": "str"}]}, ctx)
        self.assertIn("Saved the tool 'say_hello'", result)
        self.assertIn("say_hello", registry.tools)
        out, ok = registry.call("say_hello", {"who": "Ann"}, ctx)
        self.assertTrue(ok)
        self.assertIn("hello Ann", out)
        with self.assertRaises(tools.ToolError):
            tools.t_create_tool({"name": "run_command", "description": "x", "command": "ls"}, ctx)


class WallpaperToolTests(unittest.TestCase):
    def test_set_wallpaper_tool(self):
        fake("myos-set-wallpaper", 'echo "Video wallpaper set: $1"\n')
        cfg = Config()
        cfg["approval"] = "smart"
        ctx, asked = context(cfg)
        out, ok = tools.ToolRegistry(cfg).call(
            "set_wallpaper", {"file": "~/Videos/my clip.mp4", "sound": True, "always_play": "yes"}, ctx)
        self.assertTrue(ok)
        self.assertIn("Video wallpaper set: ~/Videos/my clip.mp4", out)
        self.assertEqual(asked, [])  # a wallpaper change doesn't need a question
        self.assertIn("myos-set-wallpaper ~/Videos/my clip.mp4 --sound --always-play", calls())
        cfg["approval"] = "always"
        ctx, asked = context(cfg, answer="deny")
        self.assertIn("did NOT allow", tools.t_set_wallpaper({"file": "x.mp4"}, ctx))
        (BIN / "myos-set-wallpaper").unlink()
        path = os.environ["PATH"]
        os.environ["PATH"] = str(BIN)       # also hides a really installed myos-set-wallpaper
        try:
            with self.assertRaises(tools.ToolError):
                tools.t_set_wallpaper({"file": "x.mp4"}, context(Config())[0])
        finally:
            os.environ["PATH"] = path


class JobFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = fake_ollama.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def run_job(self, request, answers):
        cfg = Config()
        cfg["ollama_url"] = self.url
        cfg["model"] = "fake-jobs:1b"
        cfg["approval"] = "smart"
        agent = Agent(cfg, Ollama(self.url), tools.ToolRegistry(cfg), Knowledge(), CommandRunner())
        events, asked = [], []

        def approve(req, cid):
            asked.append(req)
            req.respond(answers.pop(0) if answers else "deny")
            return req.decision in ("allow", "task")
        agent.run([], request, events.append, approve)
        return events, asked

    def test_plan_once_then_whole_job(self):
        LOG.unlink(missing_ok=True)
        shutil.rmtree(APPS, ignore_errors=True)
        events, asked = self.run_job("Install Spotify and pin it", ["task"])
        self.assertEqual([r.kind for r in asked], ["plan"])
        ends = [e.data for e in events if e.kind == "tool_end"]
        self.assertEqual(len(ends), 3)
        self.assertTrue(all(e["ok"] for e in ends))
        self.assertIn("Installed Spotify", ends[1]["result"])
        self.assertIn("Pinned Spotify", ends[2]["result"])
        self.assertTrue(any(c.startswith("busctl") for c in calls()))
        final = "".join(e.data for e in events if e.kind == "content")
        self.assertIn("All done", final)

    def test_plan_declined_stops(self):
        LOG.unlink(missing_ok=True)
        events, asked = self.run_job("Install Spotify and pin it", ["deny"])
        self.assertEqual(len(asked), 1)
        self.assertFalse(any("flatpak install" in c for c in calls()))
        self.assertIn("What would you like to change", "".join(
            e.data for e in events if e.kind == "content"))

    def test_allow_all_for_this_task(self):
        for f in ("/tmp/dss-job-a", "/tmp/dss-job-b"):
            Path(f).unlink(missing_ok=True)
        events, asked = self.run_job("make two files", ["task"])
        self.assertEqual(len(asked), 1)
        self.assertTrue(Path("/tmp/dss-job-a").exists() and Path("/tmp/dss-job-b").exists())
        for f in ("/tmp/dss-job-a", "/tmp/dss-job-b"):
            Path(f).unlink()


if __name__ == "__main__":
    unittest.main()
