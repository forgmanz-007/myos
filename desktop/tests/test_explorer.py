"""File Explorer like Windows 10's: the layout file (File / Home / View menus, address row),
the settings that keep its menu bar for new users, and the title bar without " — Dolphin"
(bin/myos-title-bars)."""
import base64
import configparser
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
LAYOUT = HERE / "explorer" / "dolphinui.rc"
TITLE_BARS = HERE / "bin" / "myos-title-bars"
HOOK = HERE.parent / "iso/config/hooks/normal/0420-windows10-look.hook.chroot"
DOLPHIN_ACTIONS = {line.strip() for line in (HERE / "tests/data/dolphin-25.04-actions.txt").read_text().splitlines()
                   if line.strip() and not line.startswith("#")}
# Dolphin rewrites these texts itself whenever the selection changes; renaming them does nothing.
DOLPHIN_SETS_TEXT = {"edit_cut", "edit_copy", "copy_location", "movetotrash", "deletefile", "renamefile",
                     "duplicate"}

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtCore import QByteArray, Qt
    from PyQt6.QtWidgets import QApplication, QDockWidget, QLabel, QMainWindow, QToolBar
except ImportError:          # the tests below that need it are skipped
    QApplication = None
try:
    from PyQt6.QtQml import QJSEngine
except ImportError:          # Debian's PyQt6 has no QtQml
    QJSEngine = None


_APP = []


def qt_app():
    """One QApplication, kept for the whole test run (a plain QCoreApplication would not allow
    windows, and Qt needs the object to stay alive)."""
    if not QApplication.instance():
        _APP.append(QApplication(sys.argv[:1]))
    return QApplication.instance()


def kxmlgui_version(text: str) -> str:
    """The version number as KDE reads it (KXMLGUIClient::findVersionNumber): the first
    "version" after the first "gui" after the first "<", then = and digits in quotes."""
    pos = text.index("<")
    pos = text.lower().index("gui", pos) + 2
    pos = text.lower().index("version", pos) + len("version")
    rest = text[pos:].lstrip()
    if not rest.startswith("="):
        return ""
    m = re.match(r'"(\d+)"', rest[1:].lstrip())
    return m.group(1) if m else ""


class Layout(unittest.TestCase):
    root = ET.parse(LAYOUT).getroot()

    def test_kde_reads_the_same_version_as_dolphins_own(self):
        # Dolphin 25.04 builds in version 43. Equal: ours is used (it comes first). Lower: ignored.
        self.assertEqual(self.root.get("name"), "dolphin")
        self.assertEqual(self.root.get("version"), "43")
        self.assertEqual(kxmlgui_version(LAYOUT.read_text()), "43")

    def test_windows_10_menus(self):
        menubar = self.root.find("MenuBar")
        self.assertEqual(menubar.get("noMerge"), "1")        # only these menus, not KDE's seven
        menus = [(m.get("name"), m.find("text").text) for m in menubar.findall("Menu")]
        self.assertEqual(menus, [("file", "&File"), ("home", "&Home"), ("settings", "&View")])
        # Every menu has a title (else KDE shows "No text"), and KDE's "Show Toolbar" entry
        # needs a menu called "settings".
        for menu in self.root.iter("Menu"):
            self.assertIsNotNone(menu.find("text"), menu.get("name"))

    def test_address_row(self):
        toolbars = self.root.findall("ToolBar")
        self.assertEqual([t.get("name") for t in toolbars], ["mainToolBar"])
        self.assertEqual([a.get("name") for a in toolbars[0].findall("Action")],
                         ["go_back", "go_forward", "go_up", "url_navigators", "view_redisplay",
                          "toggle_search", "hamburger_menu"])

    def test_only_actions_that_dolphin_has(self):
        menus_and_toolbar = list(self.root.find("MenuBar").iter("Action")) + list(self.root.find("ToolBar").iter("Action"))
        unknown = sorted({a.get("name") for a in menus_and_toolbar} - DOLPHIN_ACTIONS)
        self.assertEqual(unknown, [])
        props = self.root.find("ActionProperties")
        self.assertEqual(sorted({a.get("name") for a in props} - DOLPHIN_ACTIONS - {"go_home", "stop", "icons",
                         "compact", "details", "view_zoom_in", "view_zoom_reset", "view_zoom_out", "toggle_filter"}), [])

    def test_windows_names(self):
        texts = {a.get("name"): a.get("text") for a in self.root.find("ActionProperties") if a.get("text")}
        self.assertEqual(texts["file_new"], "Open new window")
        self.assertEqual(texts["options_configure"], "Options")
        self.assertEqual(texts["help_about_app"], "About File Explorer")
        self.assertEqual(texts["view_redisplay"], "Refresh")
        self.assertEqual(set(texts) & DOLPHIN_SETS_TEXT, set())

    def test_menu_bar_kept_for_new_users(self):
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        cp.read(HERE / "xdg" / "dolphinrc")
        # Dolphin hides its menu bar and side panels while this is below 200 (its first start).
        self.assertGreaterEqual(int(cp["General"]["Version"]), 200)
        state = HERE / "skel/.local/state/dolphinstaterc"
        self.assertRegex(state.read_text(), r"\n\[State\]\nState=[A-Za-z0-9+/]+=*\n")


@unittest.skipIf(QApplication is None, "needs PyQt6")
class WindowState(unittest.TestCase):
    def test_new_users_see_only_the_places_panel(self):
        app = qt_app()
        blob = re.search(r"^State=(.+)$", (HERE / "skel/.local/state/dolphinstaterc").read_text(), re.M).group(1)
        window = QMainWindow()
        window.setCentralWidget(QLabel("view"))
        docks = {}
        for name, area in (("placesDock", Qt.DockWidgetArea.LeftDockWidgetArea),
                           ("foldersDock", Qt.DockWidgetArea.LeftDockWidgetArea),
                           ("infoDock", Qt.DockWidgetArea.RightDockWidgetArea),
                           ("terminalDock", Qt.DockWidgetArea.BottomDockWidgetArea)):
            dock = QDockWidget(name, window)
            dock.setObjectName(name)          # Dolphin's names
            dock.setWidget(QLabel(name))
            window.addDockWidget(area, dock)
            docks[name] = dock
        toolbar = QToolBar("main", window)
        toolbar.setObjectName("mainToolBar")
        window.addToolBar(toolbar)
        window.show()
        self.assertTrue(window.restoreState(QByteArray(base64.b64decode(blob))))
        app.processEvents()
        self.assertEqual({n: d.isVisible() for n, d in docks.items()},
                         {"placesDock": True, "foldersDock": False, "infoDock": False, "terminalDock": False})
        self.assertTrue(toolbar.isVisible())
        window.close()


def fake_kwin(folder: Path, caption_line="        text: decoration.client.caption\n") -> Path:
    folder.mkdir(parents=True)
    (folder / "aurorae.qml").write_text("Decoration {\n    Text {\n        id: caption\n" + caption_line + "    }\n}\n")
    (folder / "AuroraeButton.qml").write_text("Item {}\n")
    return folder


class TitleBars(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.dest = self.tmp / "local/share/kwin/aurorae"

    def run_script(self, src):
        env = dict(os.environ, MYOS_KWIN_AURORAE=str(src), MYOS_TITLE_BARS_DEST=str(self.dest))
        return subprocess.run(["sh", str(TITLE_BARS)], env=env, capture_output=True, text=True)

    def test_copy_without_the_program_name(self):
        src = fake_kwin(self.tmp / "kwin")
        r = self.run_script(src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["AuroraeButton.qml", "aurorae.qml"])
        qml = (self.dest / "aurorae.qml").read_text()
        self.assertIn('text: decoration.client.caption.replace(/ — Dolphin$/, "")', qml)
        self.assertEqual(qml.count("decoration.client.caption"), 1)
        self.assertEqual(oct(self.dest.stat().st_mode & 0o777), "0o755")
        self.assertEqual(oct((self.dest / "aurorae.qml").stat().st_mode & 0o777), "0o644")
        # again (APT runs it after every package change): same result, no leftovers
        self.assertEqual(self.run_script(src).returncode, 0)
        self.assertEqual((self.dest / "aurorae.qml").read_text(), qml)
        self.assertEqual(sorted(p.name for p in self.dest.parent.iterdir()), ["aurorae"])

    def test_unknown_kwin_file_means_kwins_own(self):
        self.run_script(fake_kwin(self.tmp / "kwin"))
        self.assertTrue(self.dest.exists())
        changed = fake_kwin(self.tmp / "kwin2", "        text: someOtherCaption\n")
        r = self.run_script(changed)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.dest.exists())
        self.assertIn("not as expected", r.stderr)

    def test_no_kwin_no_copy(self):
        self.run_script(fake_kwin(self.tmp / "kwin"))
        r = self.run_script(self.tmp / "missing")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.dest.exists())

    @unittest.skipIf(QJSEngine is None or QApplication is None, "needs PyQt6 with QtQml")
    def test_only_the_file_managers_name_goes(self):
        qt_app()
        self.run_script(fake_kwin(self.tmp / "kwin"))
        expr = re.search(r"text: (.+)$", (self.dest / "aurorae.qml").read_text(), re.M).group(1)
        engine = QJSEngine()
        for caption, shown in (("Downloads — Dolphin", "Downloads"),
                               ("notes.txt — KWrite", "notes.txt — KWrite"),
                               ("Mozilla Firefox", "Mozilla Firefox"),
                               ("Dolphin and friends — Kate", "Dolphin and friends — Kate")):
            engine.globalObject().setProperty("caption", caption)
            result = engine.evaluate(expr.replace("decoration.client.caption", "caption"))
            self.assertEqual(result.toString(), shown)


class Installation(unittest.TestCase):
    def test_installed_for_everyone(self):
        s = (HERE / "install.sh").read_text()
        for wanted in ('"$HERE/explorer/dolphinui.rc" "$DESTDIR/usr/local/share/kxmlgui5/dolphin/dolphinui.rc"',
                       '"$HERE/skel/.local/state/dolphinstaterc" "$DESTDIR/etc/skel/.local/state/dolphinstaterc"',
                       '"$HERE/bin/myos-title-bars" "$DESTDIR/usr/libexec/myos/title-bars"',
                       '"$HERE/apt/80myos-title-bars" "$DESTDIR/etc/apt/apt.conf.d/80myos-title-bars"',
                       "    /usr/libexec/myos/title-bars || true\n"):
            self.assertIn(wanted, s)
        self.assertIn("\n/usr/libexec/myos/title-bars\n", HOOK.read_text())
        apt = (HERE / "apt/80myos-title-bars").read_text()
        self.assertIn('DPkg::Post-Invoke { "if [ -x /usr/libexec/myos/title-bars ]; then '
                      '/usr/libexec/myos/title-bars || true; fi"; };', apt)


if __name__ == "__main__":
    unittest.main()
