"""The Windows 10 taskbar: the layout script (run in a JavaScript engine with stand-ins for
Plasma's script functions), the taskbar widgets MyOS adds, and the Win+S shortcut."""
import base64
import configparser
import json
import os
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
LAYOUT = HERE / "look-and-feel/org.myos.desktop/contents/layouts/org.kde.plasma.desktop-layout.js"
PLASMOIDS = HERE / "plasmoids"
ALL_APPS = ["org.kde.dolphin.desktop", "firefox.desktop", "deepseek-shell.desktop",
            "systemsettings.desktop", "myos-security.desktop", "myos-taskmanager.desktop"]

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtQml import QJSEngine
    from PyQt6.QtWidgets import QApplication
except ImportError:          # the tests below that need it are skipped (Debian's PyQt6 has no QtQml)
    QJSEngine = None

# Plasma's layout script functions, as far as the script uses them.
STUBS = r"""
var panels = [];
function Widget(type) { this.type = type; this.config = {}; this.group = ""; }
Widget.prototype = {
    set currentConfigGroup(g) { this.group = g.join("/"); },
    writeConfig: function (key, value) { this.config[this.group + "/" + key] = value; }
};
function Panel() { this.widgets = []; panels.push(this); }
Panel.prototype.addWidget = function (type) { var w = new Widget(type); this.widgets.push(w); return w; };
var desktop = new Widget("desktop");
function desktopsForActivity() { return [desktop]; }
function currentActivity() { return "activity"; }
"""


_APP = []


def run_layout(widgets, apps):
    """Runs the layout script; returns (panel, desktop) as plain data."""
    if not QApplication.instance():
        _APP.append(QApplication(sys.argv[:1]))      # kept for the whole test run; a QApplication,
                                                     # since other tests open windows in the same run
    engine = QJSEngine()
    engine.evaluate(STUBS)
    engine.globalObject().setProperty("knownWidgetTypes", engine.toScriptValue(list(widgets)))
    engine.evaluate("var installedApps = %s; function applicationExists(id) { return installedApps.indexOf(id) >= 0; }"
                    % json.dumps(list(apps)))
    result = engine.evaluate(LAYOUT.read_text())
    if result.isError():
        raise AssertionError(f"layout script failed: {result.toString()}")
    data = json.loads(engine.evaluate(
        "JSON.stringify({panels: panels.map(function (p) { return {location: p.location, height: p.height,"
        " floating: p.floating, widgets: p.widgets.map(function (w) { return {type: w.type, config: w.config}; })}; }),"
        " desktop: desktop.config, wallpaper: desktop.wallpaperPlugin})").toString())
    return data


EVERYTHING = ["com.github.zren.tiledmenu", "org.myos.searchbox", "org.myos.taskview", "org.myos.showdesktop",
              "org.kde.plasma.kickoff", "org.kde.plasma.icontasks", "org.kde.plasma.systemtray",
              "org.kde.plasma.digitalclock", "org.kde.plasma.showdesktop"]


@unittest.skipIf(QJSEngine is None, "needs PyQt6 with QtQml")
class Layout(unittest.TestCase):
    def test_windows_10_taskbar(self):
        data = run_layout(EVERYTHING, ALL_APPS)
        self.assertEqual(len(data["panels"]), 1)
        panel = data["panels"][0]
        self.assertEqual((panel["location"], panel["height"], panel["floating"]), ("bottom", 40, False))
        types = [w["type"] for w in panel["widgets"]]
        self.assertEqual(types, ["com.github.zren.tiledmenu", "org.myos.searchbox", "org.myos.taskview",
                                 "org.kde.plasma.icontasks", "org.kde.plasma.systemtray",
                                 "org.kde.plasma.digitalclock", "org.myos.showdesktop"])
        start, search, _, tasks, _, clock, _ = (w["config"] for w in panel["widgets"])
        self.assertEqual(start["General/icon"], "myos-start")
        self.assertEqual(start["Shortcuts/global"], "Alt+F1")
        self.assertTrue(start["General/hideSearchField"])       # typing searches, like Windows 10
        self.assertEqual(search["Shortcuts/global"], "Meta+S")
        self.assertEqual(tasks["General/launchers"], ["applications:org.kde.dolphin.desktop",
                                                      "applications:firefox.desktop",
                                                      "applications:deepseek-shell.desktop"])
        self.assertTrue(clock["Appearance/showDate"])
        self.assertEqual(clock["Appearance/dateDisplayFormat"], 2)
        self.assertFalse(clock["Appearance/autoFontAndSize"])

    def test_start_menu_tiles(self):
        start = run_layout(EVERYTHING, ALL_APPS)["panels"][0]["widgets"][0]["config"]
        tiles = json.loads(base64.b64decode(start["General/tileModel"]))
        groups = [(t["label"], t["y"]) for t in tiles if t.get("tileType") == "group"]
        self.assertEqual(groups, [("Everyday", 0), ("System", 3)])
        apps = [(t["url"], t["x"], t["y"], t["w"], t["h"]) for t in tiles if "url" in t]
        self.assertEqual(apps, [("org.kde.dolphin.desktop", 0, 1, 2, 2), ("firefox.desktop", 2, 1, 2, 2),
                                ("deepseek-shell.desktop", 4, 1, 2, 2), ("systemsettings.desktop", 0, 4, 2, 2),
                                ("myos-security.desktop", 2, 4, 2, 2), ("myos-taskmanager.desktop", 4, 4, 2, 2)])
        self.assertEqual([t.get("label") for t in tiles if t.get("url") == "systemsettings.desktop"], ["Settings"])
        # The script's own Base64 is the standard one.
        self.assertEqual(base64.b64encode(base64.b64decode(start["General/tileModel"])).decode(),
                         start["General/tileModel"])

    def test_apps_that_are_not_installed_get_no_tile_or_pin(self):
        apps = [a for a in ALL_APPS if a not in ("org.kde.dolphin.desktop", "myos-taskmanager.desktop")]
        panel = run_layout(EVERYTHING, apps)["panels"][0]
        tiles = json.loads(base64.b64decode(panel["widgets"][0]["config"]["General/tileModel"]))
        urls = [t["url"] for t in tiles if "url" in t]
        self.assertNotIn("org.kde.dolphin.desktop", urls)
        self.assertNotIn("myos-taskmanager.desktop", urls)
        self.assertEqual(len(urls), 4)
        tasks = panel["widgets"][3]["config"]
        self.assertNotIn("applications:org.kde.dolphin.desktop", tasks["General/launchers"])

    def test_works_without_the_downloaded_menu_and_our_widgets(self):
        stock = [w for w in EVERYTHING if not w.startswith(("com.github", "org.myos"))]
        panel = run_layout(stock, ALL_APPS)["panels"][0]
        types = [w["type"] for w in panel["widgets"]]
        self.assertEqual(types, ["org.kde.plasma.kickoff", "org.kde.plasma.icontasks", "org.kde.plasma.systemtray",
                                 "org.kde.plasma.digitalclock", "org.kde.plasma.showdesktop"])
        self.assertEqual(panel["widgets"][0]["config"]["General/icon"], "myos-start")

    def test_desktop_wallpaper_and_icons(self):
        data = run_layout(EVERYTHING, ALL_APPS)
        self.assertEqual(data["wallpaper"], "org.kde.image")
        self.assertEqual(data["desktop"]["Wallpaper/org.kde.image/General/Image"],
                         "file:///usr/share/wallpapers/myos.svg")
        self.assertEqual(data["desktop"]["General/arrangement"], 1)      # columns from the top left
        self.assertEqual(data["desktop"]["General/iconSize"], 2)         # 48 px


class Widgets(unittest.TestCase):
    """The taskbar widgets MyOS adds: search box, Task View button, Show Desktop strip."""

    def test_packages(self):
        ids = sorted(p.name for p in PLASMOIDS.iterdir() if p.is_dir())
        self.assertEqual(ids, ["org.myos.searchbox", "org.myos.showdesktop", "org.myos.taskview"])
        for pid in ids:
            with self.subTest(widget=pid):
                meta = json.loads((PLASMOIDS / pid / "metadata.json").read_text())
                self.assertEqual(meta["KPackageStructure"], "Plasma/Applet")
                self.assertEqual(meta["KPlugin"]["Id"], pid)
                qml = (PLASMOIDS / pid / "contents/ui/main.qml").read_text()
                self.assertIn("PlasmoidItem {", qml)
                self.assertIn("Plasmoid.CanFillArea", qml)      # full taskbar height, like Windows
                self.assertEqual(qml.count("{"), qml.count("}"))
                for image in re.findall(r'Qt\.resolvedUrl\("\.\./images/([^"]+)"\)', qml):
                    self.assertTrue((PLASMOIDS / pid / "contents/images" / image).is_file(), image)

    def test_task_view_opens_kwins_overview(self):
        qml = (PLASMOIDS / "org.myos.taskview/contents/ui/main.qml").read_text()
        for wanted in ('"service": "org.kde.kglobalaccel"', '"path": "/component/kwin"',
                       '"member": "invokeShortcut"', '"arguments": ["Overview"]'):
            self.assertIn(wanted, qml)

    def test_search_box(self):
        qml = (PLASMOIDS / "org.myos.searchbox/contents/ui/main.qml").read_text()
        self.assertIn('i18n("Type here to search")', qml)
        self.assertIn("Milou.ResultsView", qml)
        self.assertIn("Keys.forwardTo: [results]", qml)     # arrow keys and Enter reach the results
        self.assertIn("results.setQueryString(input.text)", qml)

    def test_show_desktop_strip_reaches_the_corner(self):
        qml = (PLASMOIDS / "org.myos.showdesktop/contents/ui/main.qml").read_text()
        self.assertIn("import org.kde.plasma.private.showdesktop", qml)
        self.assertIn("showdesktop.toggleDesktop()", qml)
        self.assertIn("anchors.rightMargin: root.vertical ? 0 : -Kirigami.Units.smallSpacing", qml)

    def test_win_s_belongs_to_the_search_box(self):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        cp.read(HERE / "skel/.config/kglobalshortcutsrc")
        krunner = cp["services][org.kde.krunner.desktop"]["_launch"].split("\\t")
        self.assertNotIn("Meta+S", krunner)          # else KRunner would keep it
        self.assertIn("Meta+R", krunner)
        self.assertIn('search.writeConfig("global", "Meta+S")', LAYOUT.read_text())
        self.assertIn("Meta+Tab", cp["kwin"]["Overview"])   # Win+Tab: Task View


if __name__ == "__main__":
    unittest.main()
