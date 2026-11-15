# PC is slow, fans are loud, memory is full
Keywords: slow, lag, freeze, performance, speed up, faster, cpu, ram, memory, memory full, out of memory, app was closed, fan, hot, temperature, swap, zram, zswap, compressed memory, power profile, task manager, end task, not responding, visual effects, animations, startup apps

## Find the cause
- Task Manager is built in: Ctrl+Shift+Esc, or open_app "Task Manager". Processes tab: what uses
  the CPU, memory and disk (click a column to sort), End task for a stuck app. Performance tab:
  graphs. Startup tab: apps that start at login. Services tab: system services.
- In a terminal: `ps aux --sort=-%cpu | head -n 12` and `ps aux --sort=-%mem | head -n 12`
- Memory: `free -h`. The AI model uses a lot of memory while loaded. It unloads 5 minutes after
  the last answer, or one minute after the DeepSeek Shell window is closed.
- Temperatures: admin `apt-get install -y lm-sensors`, then `sensors`.
- Disk full makes everything slow: `df -h /`.

## Fixes
- Visual effects: `myos-performance status` shows the setting and the graphics in use.
  `myos-performance fast` = best performance (animations and blur off), `myos-performance normal`
  = best appearance, `myos-performance auto` = the system chooses (the default). Without a
  graphics card driver (virtual machine, or NVIDIA driver not installed yet) "auto" picks fast.
  Also in Task Manager > Options > Visual effects.
- A video wallpaper keeps the processor busy when there is no graphics acceleration. Back to a
  picture: `myos-set-wallpaper /usr/share/wallpapers/myos.svg`.
- Apps that start at login: Task Manager > Startup > Disable (or delete the file in ~/.config/autostart).
- A stuck program: Task Manager > End task, or `pkill -f NAME` (ask first).
- Power mode: `powerprofilesctl list`, then `powerprofilesctl set performance` (or the battery icon).
- Memory is compressed like on a Mac, so more apps fit: `cat /run/myos-memory-mode` says how.
  "zswap" = this PC has swap space on its disk; memory is compressed in RAM first and only the
  coldest part goes to the disk. "zram" = no swap on disk; a compressed swap inside the RAM.
  Task Manager > Performance > Memory shows "Compressed" and "Pressure"; in a terminal:
  `grep -E "Zswap|Swap" /proc/meminfo`, `swapon --show`, `cat /proc/pressure/memory`.
  It is set up at every start by /usr/libexec/myos/memory-setup (myos-memory.service).
- If `cat /run/myos-memory-mode` says "off", compressed memory could not be set up:
  `journalctl -b -u myos-memory.service` says why, admin `systemctl restart myos-memory.service`
  tries again.
- More apps at once on a PC with little RAM: add swap space on the disk. On Btrfs: admin
  `btrfs filesystem mkswapfile --size 8g /swapfile`, `swapon /swapfile`, and the line
  `/swapfile none swap sw 0 0` in /etc/fstab. From the next start the mode is "zswap".
- When memory is completely full, the kernel closes the app that uses the most instead of
  freezing (`journalctl -b -k | grep -i "out of memory"` shows what was closed). There is no
  warning before; afterwards one notice "<App> was closed" names it (/usr/libexec/myos/app-closed,
  started by systemd for app units that failed with "oom-kill"; Plasma's own notice is switched
  off in /etc/xdg/kded5rc because it repeated itself). systemd-oomd is deliberately not used.
  To see how full memory is: Task Manager > Performance > Memory ("Pressure"), or `free -h`.
- Firefox unloads tabs that were not looked at for a while when memory runs low
  (/etc/firefox/defaults/pref/myos-memory.js); they reload when clicked.
- The virus scanner (clamd) holds about 1 GB while loaded. When less than a quarter of the
  memory is free and nothing has used the scanner for 5 minutes, it is stopped by itself
  (`systemctl status myos-clamd-idle.timer`); the next scan starts it again, which takes a while.
- File search indexing is off by default; System Settings > Search > File Search switches it on.
- Unload the AI model now: `ollama stop MODEL` (ollama ps shows what is loaded).
- xdg-desktop-portal-gtk is not used on MyOS (/etc/xdg/xdg-desktop-portal/kde-portals.conf):
  on Debian 13 it leaked file handles and then kept a processor core busy. If it shows up busy
  in Task Manager anyway, check that file and log out and in again.
