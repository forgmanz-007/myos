"""bin/myos-install-look (Start menu, icons without Microsoft logos, font), the ISO build hook
that downloads them, and bin/myos-desktop-icons (the Recycle Bin on the desktop)."""
import importlib.machinery
import importlib.util
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SCRIPT = HERE / "bin" / "myos-install-look"
HOOK = HERE.parent / "iso/config/hooks/normal/0420-windows10-look.hook.chroot"

loader = importlib.machinery.SourceFileLoader("install_look", str(SCRIPT))
spec = importlib.util.spec_from_loader("install_look", loader)
look = importlib.util.module_from_spec(spec)
loader.exec_module(look)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def link(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


def fake_we10x(root: Path) -> Path:
    """A small icon theme laid out like We10X's download (src/, links/, alternative/)."""
    write(root / "COPYING", "GPL-3.0")
    write(root / "AUTHORS", "authors")
    write(root / "src/index.theme", "[Icon Theme]\nName=We10X\nInherits=hicolor,breeze\n")
    for folder in look.ICON_FOLDERS:
        (root / "src" / folder).mkdir(parents=True, exist_ok=True)
        (root / "links" / folder).mkdir(parents=True, exist_ok=True)
    for size in look.STATUS_SIZES:
        write(root / f"src/status/{size}/battery.svg", "BATTERY")
    (root / "links/status").mkdir(parents=True, exist_ok=True)
    write(root / "src/status/symbolic-budgie/x.svg", "not copied")
    # a Windows logo with two more names, one of them in another folder
    write(root / "src/places/scalable/start-here.svg", "WINDOWS LOGO")
    link(root / "links/places/scalable/start-here-kde.svg", "start-here.svg")
    link(root / "links/places/16/start-here-kde-plasma.svg", "../scalable/start-here.svg")
    # Edge's logo as the generic web browser picture
    write(root / "src/apps/scalable/web-browser.svg", "EDGE LOGO")
    link(root / "links/apps/scalable/internet-web-browser.svg", "web-browser.svg")
    write(root / "src/apps/scalable/firefox.svg", "FIREFOX")
    # .exe and Word files
    for size in ("16", "scalable"):
        write(root / f"src/mimes/{size}/application-x-executable.svg", f"GEAR {size}")
        write(root / f"src/mimes/{size}/application-x-ms-dos-executable.svg", "WINDOWS LOGO")
        link(root / f"links/mimes/{size}/application-x-msdownload.svg", "application-x-ms-dos-executable.svg")
    write(root / "src/mimes/scalable/x-office-document.svg", "DOCUMENT")
    write(root / "src/mimes/scalable/application-vnd.ms-word.svg", "WORD LOGO")
    link(root / "links/mimes/scalable/application-vnd.openxmlformats-officedocument.wordprocessingml.document.svg",
         "application-vnd.ms-word.svg")
    write(root / "src/mimes/22/application-vnd.ms-word.svg", "WORD LOGO 22")    # no plain picture here
    # File Explorer: Microsoft's icon in src/, the plain one in alternative/
    write(root / "src/apps/scalable/file-manager.svg", "EXPLORER COPY")
    write(root / "alternative/apps/scalable/file-manager.svg", "PLAIN FOLDER")
    link(root / "links/apps/scalable/org.kde.dolphin.svg", "file-manager.svg")
    # a name that is a file in src/ and a link in links/: the link wins, as with GNU cp
    write(root / "src/actions/16/window-close.svg", "CLOSE")
    write(root / "src/actions/16/xfce-wm-close.svg", "OLD")
    link(root / "links/actions/16/xfce-wm-close.svg", "window-close.svg")
    # removed by the theme's installer too
    write(root / "src/places/scalable/user-trash-dark.svg", "DARK TRASH")
    write(root / "src/places/scalable/user-trash.svg", "TRASH")
    write(root / "src/preferences/32/preferences-system.svg", "SETTINGS")
    return root


class Icons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.dest = tmp / "icons" / "We10X"
        out = subprocess.run([sys.executable, str(SCRIPT), "icons", str(fake_we10x(tmp / "src")), str(self.dest)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.output = out.stdout

    def shows(self, name: str) -> str:
        return (self.dest / name).read_text()

    def test_microsoft_logos_are_left_out_under_every_name(self):
        for name in ("places/scalable/start-here.svg", "places/scalable/start-here-kde.svg",
                     "places/16/start-here-kde-plasma.svg", "apps/scalable/web-browser.svg",
                     "apps/scalable/internet-web-browser.svg"):
            self.assertFalse(os.path.lexists(self.dest / name), name)
        self.assertEqual(self.shows("apps/scalable/firefox.svg"), "FIREFOX")
        self.assertIn("pictures with Microsoft logos left out", self.output)

    def test_windows_programs_and_office_files_get_neutral_pictures(self):
        for size in ("16", "scalable"):
            for name in ("application-x-ms-dos-executable", "application-x-msdownload"):
                self.assertEqual(self.shows(f"mimes/{size}/{name}.svg"), f"GEAR {size}")
        self.assertEqual(self.shows("mimes/scalable/application-vnd.ms-word.svg"), "DOCUMENT")
        self.assertEqual(self.shows(
            "mimes/scalable/application-vnd.openxmlformats-officedocument.wordprocessingml.document.svg"), "DOCUMENT")
        self.assertFalse(os.path.lexists(self.dest / "mimes/22/application-vnd.ms-word.svg"))
        # the gear itself is still a picture, not a link to itself
        self.assertFalse((self.dest / "mimes/scalable/application-x-executable.svg").is_symlink())

    def test_installed_like_the_themes_own_installer(self):
        self.assertEqual(self.shows("apps/scalable/file-manager.svg"), "PLAIN FOLDER")
        self.assertEqual(self.shows("apps/scalable/org.kde.dolphin.svg"), "PLAIN FOLDER")
        self.assertEqual(self.shows("actions/16/xfce-wm-close.svg"), "CLOSE")
        self.assertFalse(os.path.lexists(self.dest / "places/scalable/user-trash-dark.svg"))
        self.assertEqual(self.shows("places/scalable/user-trash.svg"), "TRASH")
        self.assertEqual(os.readlink(self.dest / "preferences/22"), "32")
        self.assertEqual(self.shows("preferences/22/preferences-system.svg"), "SETTINGS")
        for folder in (*look.ICON_FOLDERS, "status"):
            self.assertEqual(os.readlink(self.dest / f"{folder}@2x"), folder)
        self.assertFalse((self.dest / "status/symbolic-budgie").exists())
        self.assertTrue((self.dest / "status/22/battery.svg").is_file())
        for f in ("index.theme", "COPYING", "AUTHORS"):
            self.assertTrue((self.dest / f).is_file(), f)

    def test_no_broken_links(self):
        broken = [str(p) for p in self.dest.rglob("*") if p.is_symlink() and not p.exists()]
        self.assertEqual(broken, [])

    def test_installing_again_replaces_it(self):
        src = Path(self.tmp.name) / "src"
        write(self.dest / "leftover.svg", "old")
        out = subprocess.run([sys.executable, str(SCRIPT), "icons", str(src), str(self.dest)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse((self.dest / "leftover.svg").exists())

    def test_refuses_something_else(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "icons", self.tmp.name, str(self.dest)],
                             capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("does not contain the We10X icon theme", out.stderr)


class StartMenu(unittest.TestCase):
    def install(self, version):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        src = tmp / "tiledmenu"
        write(src / "package/metadata.json", '{"KPlugin": {"Id": "com.github.zren.tiledmenu"}}')
        write(src / "package/contents/ui/KickerAppModel.qml",
              "import org.kde.plasma.private.kicker as Kicker\n\nKicker.SimpleFavoritesModel {\n}\n")
        dest = tmp / "plasmoids" / "com.github.zren.tiledmenu"
        env = dict(os.environ)
        env.pop("MYOS_PLASMA_VERSION", None)
        if version is not None:
            env["MYOS_PLASMA_VERSION"] = version
        out = subprocess.run([sys.executable, str(SCRIPT), "menu", str(src), str(dest)],
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return (dest / "contents/ui/KickerAppModel.qml").read_text()

    def test_older_plasma_gets_the_old_name(self):
        # Debian 13: plasma-desktop 4:6.3.6-1 calls it FavoritesModel
        self.assertIn("Kicker.FavoritesModel {", self.install("4:6.3.6-1"))

    def test_newer_plasma_keeps_the_new_name(self):
        for version in ("4:6.4.5-1", "6.5.0", "7.0"):
            self.assertIn("Kicker.SimpleFavoritesModel {", self.install(version))

    def test_unknown_plasma_is_left_alone(self):
        self.assertIn("Kicker.SimpleFavoritesModel {", self.install(""))

    def test_version_numbers(self):
        os.environ["MYOS_PLASMA_VERSION"] = "4:6.3.6-1"
        try:
            self.assertEqual(look.plasma_version(), (6, 3))
        finally:
            del os.environ["MYOS_PLASMA_VERSION"]

    def test_refuses_another_package(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(tmp / "package/metadata.json", '{"KPlugin": {"Id": "org.example.other"}}')
        out = subprocess.run([sys.executable, str(SCRIPT), "menu", str(tmp), str(tmp / "dest")],
                             capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse((tmp / "dest").exists())


class Font(unittest.TestCase):
    def test_fonts_and_their_licence(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ("selawk.ttf", "selawkb.ttf", "selawksl.ttf"):
            write(tmp / "zip" / name, name)
        write(tmp / "zip" / "selawk.eot", "web font, not needed")
        write(tmp / "zip" / "LICENSE.txt", "SIL Open Font License")
        out = subprocess.run([sys.executable, str(SCRIPT), "font", str(tmp / "zip"), str(tmp / "fonts")],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(sorted(p.name for p in (tmp / "fonts").iterdir()),
                         ["LICENSE.txt", "selawk.ttf", "selawkb.ttf", "selawksl.ttf"])

    def test_no_licence_no_install(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(tmp / "zip" / "selawk.ttf", "font")
        out = subprocess.run([sys.executable, str(SCRIPT), "font", str(tmp / "zip"), str(tmp / "fonts")],
                             capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)


class BuildHook(unittest.TestCase):
    def test_downloads_are_pinned_and_checked(self):
        hook = HOOK.read_text()
        self.assertTrue(hook.startswith("#!/bin/bash\n"))
        self.assertIn("set -euo pipefail", hook)
        for name in ("TILEDMENU_SHA256", "WE10X_SHA256", "SELAWIK_SHA256", "SELAWIK_LICENSE_SHA256"):
            self.assertRegex(hook, rf"\n{name}=[0-9a-f]{{64}}\n")
        for name in ("TILEDMENU_COMMIT", "WE10X_COMMIT"):
            self.assertRegex(hook, rf"\n{name}=[0-9a-f]{{40}}\n")
        self.assertEqual(hook.count('sha256sum -c'), 1)            # in fetch(), used for every download
        self.assertEqual(len(re.findall(r"^fetch ", hook, re.M)), 4)
        for job in ("menu", "icons", "font"):
            self.assertIn(f"/usr/libexec/myos/install-look {job} ", hook)
        self.assertTrue(os.access(HOOK, os.X_OK))
        r = subprocess.run(["bash", "-n", str(HOOK)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class RecycleBin(unittest.TestCase):
    """bin/myos-desktop-icons, run at every login."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.log"
        fakes = {
            "xdg-user-dirs-update": 'mkdir -p "$HOME/Bureau"',
            "xdg-user-dir": 'echo "$HOME/Bureau"',          # a French desktop folder
            "dbus-send": 'echo "dbus-send $*" >> "$FAKE_LOG"',
        }
        for name, body in fakes.items():
            p = self.bin / name
            p.write_text(f"#!/bin/sh\n{body}\n")
            p.chmod(0o755)

    def run_script(self):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "FAKE_LOG": str(self.log)}
        r = subprocess.run(["sh", str(HERE / "bin" / "myos-desktop-icons")], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_once_per_user_in_the_desktop_folder(self):
        self.run_script()
        f = self.home / "Bureau" / "recycle-bin.desktop"
        text = f.read_text()
        for line in ("Type=Link", "Name=Recycle Bin", "URL=trash:/", "Icon=user-trash-full",
                     "EmptyIcon=user-trash"):
            self.assertIn(line + "\n", text)
        self.assertTrue(f.stat().st_mode & stat.S_IXUSR)       # KDE trusts it
        self.assertFalse((self.home / "Desktop").exists())
        f.unlink()
        self.run_script()                                       # deleted: stays deleted
        self.assertFalse(f.exists())

    def test_title_bar_icons_reloaded_at_every_login(self):
        self.run_script()
        self.run_script()
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 2)
        self.assertIn("/KIconLoader org.kde.KIconLoader.iconChanged int32:0", calls[0])

    def test_autostart_entry(self):
        text = (HERE / "autostart" / "myos-desktop-icons.desktop").read_text()
        self.assertIn("Exec=/usr/libexec/myos/desktop-icons\n", text)
        self.assertIn("OnlyShowIn=KDE;\n", text)


if __name__ == "__main__":
    unittest.main()
