# Sleep, shutdown and battery problems
Keywords: sleep, suspend, hibernate, wake up, resume, shutdown hangs, battery, laptop, lid, power button

## Check
- Logs from the last failed resume (admin): `journalctl -b -1 -p err` and `journalctl -b -1 | grep -iE 'suspend|resume|PM:'`
- Sleep mode in use: `cat /sys/power/mem_sleep` ([s2idle] or [deep]).

## Fixes
- NVIDIA: driver 550+ handles sleep with its services (admin):
  `systemctl enable nvidia-suspend.service nvidia-resume.service nvidia-hibernate.service`
  and write /etc/modprobe.d/nvidia-power.conf with `options nvidia NVreg_PreserveVideoMemoryAllocations=1`,
  then `update-initramfs -u` and restart.
- Wakes up immediately: often a USB device or network card. List wake sources: `cat /proc/acpi/wakeup`.
- Shutdown hangs: look at `journalctl -b -1 -n 80` for the service that blocks.
- Lid and power button actions: System Settings > Power Management.
- Battery health (laptops): `upower -i $(upower -e | grep BAT)`.
