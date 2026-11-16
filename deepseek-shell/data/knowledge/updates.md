# Update the system, and fix broken updates
Keywords: update, upgrade, updates, apt, dpkg, broken packages, lock, could not get lock, held packages, firmware update, fwupd, myos update, myos-update, new version, myos version, automatic updates, release

## Update everything (admin)
1. `apt-get update && apt-get full-upgrade -y`
2. `flatpak update -y` (system apps), and without admin: `flatpak update --user -y` (your apps)
3. Firmware (BIOS, SSD, etc. when supported): `fwupdmgr refresh && fwupdmgr get-updates`, then
   ask the user before `fwupdmgr update -y`.
Restart if the kernel or graphics driver was updated.

## Fix problems (admin)
- "Could not get lock /var/lib/dpkg/lock": another update is running. Check `ps aux | grep -E 'apt|dpkg'`;
  wait for it. Only if nothing runs: `rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock`.
- Interrupted update: `dpkg --configure -a` then `apt-get install -f -y`.
- "held back" packages: `apt-get full-upgrade -y` usually installs them.
- Repository errors: show `/etc/apt/sources.list.d/` and `/etc/apt/sources.list`; disable the
  failing third-party file by renaming it to .disabled.
- Undo a bad update with snapshots (see the snapshots guide).

## MyOS's own updates (new MyOS versions from GitHub)
MyOS installs its own new versions by itself (its apps, settings and look): every few hours
`myos-update` looks for a newer release in the GitHub repository set in /etc/myos/update.conf
(REPO=) and installs it. Debian's packages (Firefox, KDE, the kernel) are not part of this:
use "Update everything" above for those.
- Installed version and the last check: `myos-update --status`
- Is a new version out: `myos-update --check`
- Install a new version now (admin): `myos-update`
- Stop the automatic installs (admin): set `AUTO=no` in /etc/myos/update.conf (`AUTO=yes` = on again).
  "myos-update" by hand still works.
- What happened: `journalctl -u myos-update.service -n 50`. A version that failed to install
  is tried again a day later (or now with `myos-update`).
- On Btrfs a snapshot "Before MyOS update <version>" is taken first: to go back, see the
  snapshots guide. Some changes (taskbar, Start menu) show after the next sign-in.
