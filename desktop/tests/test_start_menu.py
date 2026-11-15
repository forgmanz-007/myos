"""Tests for bin/myos-start-menu-defaults (Start menu favorites for new users)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "myos-start-menu-defaults"
WANT = ("firefox.desktop,org.kde.dolphin.desktop,deepseek-shell.desktop,myos-security.desktop,"
        "systemsettings.desktop")
# Same structure as Plasma 6.3's org.kde.plasma.kickoff/contents/config/main.xml.
MAIN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<kcfg xmlns="http://www.kde.org/standards/kcfg/1.0">
  <kcfgfile name=""/>
    <group name="General">
        <entry name="icon" type="String">
            <default>start-here-kde-symbolic</default>
        </entry>
        <entry name="favorites" type="StringList">
            <label>List of general favorites. Supported values are menu id's.</label>
            <default>preferred://browser,org.kde.kontact.desktop,systemsettings.desktop,org.kde.dolphin.desktop,org.kde.discover.desktop</default>
        </entry>
        <entry name="systemFavorites" type="StringList">
            <label>List of system action favorites.</label>
            <default>suspend,hibernate,reboot,shutdown</default>
        </entry>
        <entry name="favoritesPortedToKAstats" type="Bool">
            <default>false</default>
        </entry>
    </group>
</kcfg>
"""


class StartMenuDefaultsTests(unittest.TestCase):
    def run_script(self, path):
        return subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True,
                              env=dict(os.environ, MYOS_KICKOFF_CONFIG=str(path)))

    def test_sets_only_the_favorites(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "main.xml"
            f.write_text(MAIN_XML)
            r = self.run_script(f)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = f.read_text()
            self.assertIn(f"<default>{WANT}</default>", out)
            self.assertNotIn("discover", out)
            for kept in ("<default>start-here-kde-symbolic</default>",
                         "<default>suspend,hibernate,reboot,shutdown</default>",
                         "<default>false</default>"):
                self.assertIn(kept, out)
            self.assertEqual(out.count("\n"), MAIN_XML.count("\n"))
            # APT runs it after every package change: running again changes nothing.
            r = self.run_script(f)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(f.read_text(), out)

    def test_no_plasma_no_problem(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_script(Path(d) / "missing.xml").returncode, 0)

    def test_unknown_format_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "main.xml"
            f.write_text("<kcfg><group name='General'></group></kcfg>\n")
            r = self.run_script(f)
            self.assertEqual(r.returncode, 1)
            self.assertIn("could not set the Start menu favorites", r.stderr)


if __name__ == "__main__":
    unittest.main()
