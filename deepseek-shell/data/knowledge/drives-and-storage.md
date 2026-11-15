# Disk space, USB drives, Windows partitions
Keywords: disk, disk full, space, storage, usb, usb stick, external drive, ntfs, exfat, mount, windows partition, bitlocker, fast startup, ssd

## Free up space (show the user what will be deleted first)
- See usage: `df -h /` and the biggest folders: `du -h --max-depth=1 ~ 2>/dev/null | sort -h | tail -15`
- Package cache (admin): `apt-get clean`
- Old logs (admin): `journalctl --vacuum-size=200M`
- Unused Flatpak runtimes (admin): `flatpak uninstall -y --unused`
- AI models you don't use: `ollama list`, remove with `ollama rm NAME` (or Settings > AI model).
- Old snapshots are cleaned automatically (snapper keeps 20).
- Big programs and games: compress them in place, they keep working:
  `myos-compress --text FOLDER` (what it would save), `myos-compress --text --compress FOLDER`.
  Needs a Btrfs drive. See the Windows programs guide.

## USB sticks and external drives
They appear in the taskbar's "Disks & Devices" icon; click to open. NTFS, exFAT and FAT work.
Safely remove with the eject button there.

## Windows partition (dual boot)
- Read-only or "unclean" error: Windows was hibernated by Fast Startup. In Windows: Control Panel >
  Power Options > "Choose what the power buttons do" > untick "Turn on fast startup", then shut down.
- BitLocker-encrypted: it needs the recovery key. Admin: `apt-get install -y dislocker` or use
  `cryptsetup bitlkOpen /dev/PARTITION windows` (asks for the key).
