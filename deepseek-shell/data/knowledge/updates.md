# Update the system, and fix broken updates
Keywords: update, upgrade, updates, apt, dpkg, broken packages, lock, could not get lock, held packages, firmware update, fwupd

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
