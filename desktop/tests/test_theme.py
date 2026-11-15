"""The Windows 10 look: title bars (Aurorae theme), taskbar style (Plasma style), window colours,
font alias, and how install.sh puts them in place."""
import configparser
import filecmp
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
AURORAE = HERE / "aurorae"
STYLE = HERE / "plasma-style"
SVG = "{http://www.w3.org/2000/svg}"


def ini(path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read(path)
    return cp


def parts(svg_path) -> dict:
    """id -> (x, y, width, height) of the first rectangle in each group (it fixes the size)."""
    out = {}
    for g in ET.parse(svg_path).getroot().iter(f"{SVG}g"):
        r = g.find(f"{SVG}rect")
        if g.get("id") and r is not None:
            out[g.get("id")] = tuple(float(r.get(k)) for k in ("x", "y", "width", "height"))
    return out


def rects(svg_path) -> dict:
    """id -> (width, height) of every rectangle with an id (the hint-* parts)."""
    return {r.get("id"): (float(r.get("width")), float(r.get("height")))
            for r in ET.parse(svg_path).getroot().iter(f"{SVG}rect") if r.get("id")}


def same_tree(test, a: Path, b: Path) -> None:
    files_a = sorted(str(p.relative_to(a)) for p in a.rglob("*") if p.is_file())
    files_b = sorted(str(p.relative_to(b)) for p in b.rglob("*") if p.is_file())
    test.assertEqual(files_a, files_b)
    for f in files_a:
        test.assertTrue(filecmp.cmp(a / f, b / f, shallow=False), f"{f} differs from what the script makes")


class TitleBars(unittest.TestCase):
    """KWin's Aurorae engine draws the window frame from MyOS10/decoration.svg."""
    theme = AURORAE / "MyOS10"

    def test_committed_files_are_what_the_script_makes(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable, str(AURORAE / "make-theme.py"), d], check=True,
                           capture_output=True)
            same_tree(self, Path(d), self.theme)

    def test_frame_parts_have_the_sizes_aurorae_expects(self):
        rc = ini(self.theme / "MyOS10rc")["Layout"]
        n = {k: int(v) for k, v in rc.items()}
        top = n["PaddingTop"] + n["TitleEdgeTop"] + n["TitleHeight"] + n["TitleEdgeBottom"]
        left = n["PaddingLeft"] + n["BorderLeft"]
        right = n["PaddingRight"] + n["BorderRight"]
        bottom = n["BorderBottom"] + n["PaddingBottom"]
        found = parts(self.theme / "decoration.svg")
        for prefix in ("decoration", "decoration-inactive"):
            with self.subTest(prefix=prefix):
                self.assertEqual(found[f"{prefix}-topleft"][2:], (left, top))
                self.assertEqual(found[f"{prefix}-top"][3], top)
                self.assertEqual(found[f"{prefix}-topright"][2:], (right, top))
                self.assertEqual(found[f"{prefix}-left"][2], left)
                self.assertEqual(found[f"{prefix}-right"][2], right)
                self.assertEqual(found[f"{prefix}-bottomleft"][2:], (left, bottom))
                self.assertEqual(found[f"{prefix}-bottom"][3], bottom)
                self.assertEqual(found[f"{prefix}-bottomright"][2:], (right, bottom))
                self.assertIn(f"{prefix}-center", found)
        # Maximized: just the white title bar, no frame.
        for prefix in ("decoration-maximized", "decoration-maximized-inactive"):
            self.assertEqual(found[f"{prefix}-center"][3], n["TitleHeight"])

    def test_windows_10_buttons(self):
        rc = ini(self.theme / "MyOS10rc")
        self.assertEqual(rc["Layout"]["ButtonWidthClose"], "46")      # wide buttons
        self.assertEqual(rc["Layout"]["ButtonSpacing"], "0")
        self.assertEqual(rc["General"]["TitleAlignment"], "Left")
        states = ("active", "hover", "pressed", "deactivated", "inactive", "hover-inactive",
                  "pressed-inactive", "deactivated-inactive")
        for button in ("close", "minimize", "maximize", "restore"):
            with self.subTest(button=button):
                found = parts(self.theme / f"{button}.svg")
                for state in states:
                    self.assertIn(f"{state}-center", found)
        close = (self.theme / "close.svg").read_text()
        hover = re.search(r'<g id="hover-center".*?</g>', close).group(0)
        self.assertIn('fill="#e81123"', hover)        # the red close button
        self.assertIn('stroke="#ffffff"', hover)       # with a white X

    def test_kwin_uses_the_theme(self):
        for cfg, group in ((ini(HERE / "xdg" / "kwinrc"), "org.kde.kdecoration2"),):
            self.assertEqual(cfg[group]["library"], "org.kde.kwin.aurorae")
            self.assertEqual(cfg[group]["theme"], "__aurorae__svg__MyOS10")
            self.assertEqual(cfg[group]["ButtonsOnRight"], "IAX")    # minimize, maximize, close
        defaults = (HERE / "look-and-feel/org.myos.desktop/contents/defaults").read_text()
        self.assertIn("theme=__aurorae__svg__MyOS10", defaults)
        meta = ini(self.theme / "metadata.desktop")["Desktop Entry"]
        self.assertEqual(meta["X-KDE-PluginInfo-Name"], "MyOS10")


class TaskbarStyle(unittest.TestCase):
    """The "MyOS 10" Plasma style: flat dark taskbar, square pop-ups, Windows 10 task buttons."""
    style = STYLE / "myos10"

    def test_committed_pictures_are_what_the_script_makes(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable, str(STYLE / "make-style.py"), d], check=True,
                           capture_output=True)
            made = sorted(p.relative_to(d) for p in Path(d).rglob("*.svg"))
            have = sorted(p.relative_to(self.style) for p in self.style.rglob("*.svg"))
            self.assertEqual(made, have)
            for f in made:
                self.assertTrue(filecmp.cmp(Path(d) / f, self.style / f, shallow=False), str(f))

    def test_taskbar_is_flat_and_fills_its_height(self):
        for name in ("widgets", "translucent/widgets", "solid/widgets"):
            svg = self.style / name / "panel-background.svg"
            ids = {**parts(svg), **rects(svg)}
            self.assertFalse([i for i in ids if i.startswith("shadow")], f"{name}: no shadow")
            for side in ("top", "bottom", "left", "right"):
                self.assertLess(ids[f"hint-{side}-margin"][0 if side in ("left", "right") else 1], 0.001)
            self.assertIn("center", ids)

    def test_task_buttons_like_windows_10(self):
        svg = self.style / "widgets" / "tasks.svg"
        hints = rects(svg)
        # 40 px taskbar: 24 px icons; buttons 40 + 8 = 48 px wide.
        self.assertEqual(hints["normal-hint-top-margin"][1] + hints["normal-hint-bottom-margin"][1], 16)
        self.assertEqual(hints["normal-hint-left-margin"][0] + hints["normal-hint-right-margin"][0], 8)
        text = svg.read_text()
        for prefix in ("normal", "focus", "hover", "focus-hover", "launcher-hover", "minimized",
                       "attention", "progress"):
            self.assertIn(f'id="{prefix}-center"', text)
        # Open program: a short blue line under it and no background; the front one lit up.
        normal = re.search(r'<g id="normal-center">.*?</g>', text).group(0)
        self.assertEqual(normal.count("<rect"), 1)
        self.assertIn("#76b9ed", re.search(r'<g id="normal-bottom">.*?</g>', text).group(0))
        self.assertNotIn("#76b9ed", re.search(r'<g id="normal-bottomleft">.*?</g>', text).group(0))
        self.assertIn("#76b9ed", re.search(r'<g id="focus-bottomleft">.*?</g>', text).group(0))
        self.assertIn("#ffffff", re.search(r'<g id="focus-center">.*?</g>', text).group(0))
        # Pinned, closed programs have no frame of their own.
        self.assertNotIn('id="center"', text)
        self.assertNotIn('id="south-center"', text)

    def test_style_description_and_colours(self):
        import json
        meta = json.loads((self.style / "metadata.json").read_text())
        self.assertEqual(meta["KPlugin"]["Id"], "myos10")
        colors = ini(self.style / "colors")
        self.assertEqual(colors["Colors:Window"]["ForegroundNormal"], "255,255,255")
        self.assertEqual(colors["Colors:Selection"]["BackgroundNormal"], "0,120,215")
        self.assertEqual(ini(HERE / "xdg" / "plasmarc")["Theme"]["name"], "myos10")


class WindowColours(unittest.TestCase):
    scheme = HERE / "colors" / "MyOS10.colors"

    def test_white_windows_black_text_blue_selection(self):
        c = ini(self.scheme)
        for group in ("Window", "View", "Header"):
            self.assertEqual(c[f"Colors:{group}"]["BackgroundNormal"], "255,255,255")
            self.assertEqual(c[f"Colors:{group}"]["ForegroundNormal"], "0,0,0")
        self.assertEqual(c["Colors:Selection"]["BackgroundNormal"], "0,120,215")
        self.assertEqual(c["Colors:Selection"]["ForegroundNormal"], "255,255,255")
        self.assertEqual(c["General"]["ColorScheme"], "MyOS10")
        keys = set(c["Colors:Window"])
        for group in ("Button", "Complementary", "Header", "Header][Inactive", "Selection",
                      "Tooltip", "View"):
            self.assertEqual(set(c[f"Colors:{group}"]), keys, group)

    def test_install_adds_the_colours_to_the_system_defaults(self):
        installer = (HERE / "install.sh").read_text()
        program = re.search(r"awk '([^']+)' \"\$HERE/colors/MyOS10.colors\"", installer).group(1)
        out = subprocess.run(["awk", program, str(self.scheme)], capture_output=True, text=True,
                             check=True).stdout
        groups = re.findall(r"^\[.*\]$", out, re.M)
        self.assertIn("[Colors:Selection]", groups)
        self.assertIn("[Colors:Header][Inactive]", groups)
        self.assertIn("[WM]", groups)
        self.assertNotIn("[General]", groups)          # the name stays in /etc/xdg/kdeglobals
        self.assertNotIn("[KDE]", groups)
        self.assertNotIn("SPDX", out)
        self.assertIn("ColorScheme=MyOS10", (HERE / "xdg" / "kdeglobals").read_text())

    def test_font_and_icons_for_everyone(self):
        g = ini(HERE / "xdg" / "kdeglobals")
        for key in ("font", "menuFont", "toolBarFont"):
            self.assertTrue(g["General"][key].startswith("Selawik,10,"), key)
        self.assertTrue(g["WM"]["activeFont"].startswith("Selawik,"))
        self.assertEqual(g["Icons"]["Theme"], "We10X")
        self.assertEqual(ini(HERE / "xdg" / "kcminputrc")["Mouse"]["cursorTheme"], "Breeze_Light")

    def test_segoe_ui_requests_get_selawik(self):
        root = ET.parse(HERE / "fonts" / "60-myos-segoe-ui.conf").getroot()
        alias = root.find("alias")
        self.assertEqual(alias.find("family").text, "Segoe UI")
        self.assertEqual(alias.find("accept/family").text, "Selawik")


class Installer(unittest.TestCase):
    def test_everything_is_installed_where_plasma_looks(self):
        s = (HERE / "install.sh").read_text()
        for wanted in (
            'cp -r "$HERE/plasma-style/myos10" "$DESTDIR/usr/share/plasma/desktoptheme/"',
            'cp -r "$HERE/aurorae/MyOS10" "$DESTDIR/usr/share/aurorae/themes/"',
            '"$HERE/colors/MyOS10.colors" "$DESTDIR/usr/share/color-schemes/MyOS10.colors"',
            'cp -r "$p" "$DESTDIR/usr/share/plasma/plasmoids/"',
            '"$DESTDIR/etc/fonts/conf.d/60-myos-segoe-ui.conf"',
            '"$DESTDIR/etc/xdg/autostart/myos-desktop-icons.desktop"',
            '"$DESTDIR/usr/libexec/myos/desktop-icons"',
            '"$DESTDIR/usr/libexec/myos/install-look"',
            '"$DESTDIR/usr/share/sddm/themes/breeze/theme.conf.user"',
        ):
            self.assertIn(wanted, s)
        # the colours are added after the xdg files are copied (or they would be overwritten)
        self.assertLess(s.index('for f in "$HERE"/xdg/*; do'), s.index('>> "$DESTDIR/etc/xdg/kdeglobals"'))
        r = subprocess.run(["bash", "-n", str(HERE / "install.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
