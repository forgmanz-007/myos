# Keyboard layout and system language
Keywords: keyboard, layout, language, typing, wrong letters, qwertz, azerty, input method, locale, translation, switch language

- Layouts: System Settings > Keyboard > Layouts > Add. Switch with Win+Space (like Windows).
- System language: System Settings > Region & Language, then log out and in.
  The language must be generated (admin): `apt-get install -y locales-all` (all languages), or
  run `dpkg-reconfigure locales` only interactively (not possible here).
- Firefox language: Firefox Settings > General > Language.
- Chinese, Japanese, Korean input (admin): `apt-get install -y fcitx5 fcitx5-chinese-addons fcitx5-mozc fcitx5-hangul kde-config-fcitx5`
  then System Settings > Keyboard > Virtual Keyboard > Fcitx 5, and log out and in.
- Check the current setup: `localectl status`.
