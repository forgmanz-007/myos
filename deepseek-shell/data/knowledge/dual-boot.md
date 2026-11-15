# Dual boot with Windows: wrong clock, Windows missing from boot menu
Keywords: dual boot, windows, clock wrong, time wrong, grub, boot menu, os-prober, bios, uefi, boot order

## Clock is wrong after using Windows
Linux stores UTC in the hardware clock, Windows stores local time. Easiest fix on this side
(admin): `timedatectl set-local-rtc 1 --adjust-system-clock`

## Windows is not in the boot menu
Admin:
1. `apt-get install -y os-prober`
2. write /etc/default/grub.d/20-os-prober.cfg containing `GRUB_DISABLE_OS_PROBER=false`
3. `update-grub`

## Choose what starts by default
Admin: write /etc/default/grub.d/30-default.cfg with `GRUB_DEFAULT=saved` and
`GRUB_SAVEDEFAULT=true` (remembers the last choice), then `update-grub`.

## The PC boots straight into Windows
Open the UEFI/BIOS boot menu (often F12, F11, F8 or Esc right after power on) and choose the
"debian" entry; in the BIOS boot order move "debian" to the top.
