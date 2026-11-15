"""The desktop portal settings: KDE's portal only, never the GTK one."""
import configparser
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


class Portal(unittest.TestCase):
    def test_only_kdes_portal_is_used(self):
        cp = configparser.ConfigParser()
        cp.optionxform = str
        cp.read(HERE / "portal" / "kde-portals.conf")
        preferred = dict(cp["preferred"])
        self.assertEqual(preferred["default"], "kde")
        self.assertEqual(preferred["org.freedesktop.impl.portal.Settings"], "kde")      # not "kde;gtk"
        self.assertEqual(preferred["org.freedesktop.impl.portal.Lockdown"], "none")     # no GTK fallback
        self.assertNotIn("gtk", " ".join(preferred.values()))

    def test_installed_where_the_portal_looks(self):
        installer = (HERE / "install.sh").read_text()
        self.assertIn('"$HERE/portal/kde-portals.conf" "$DESTDIR/etc/xdg/xdg-desktop-portal/kde-portals.conf"',
                      installer)


if __name__ == "__main__":
    unittest.main()
