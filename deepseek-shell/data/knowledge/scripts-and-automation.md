# Scripts and automation (make a script, run it on a schedule or at login)
Keywords: script, scripts, make a script, automate, automation, schedule, scheduled, every day, every hour, at login, startup, task scheduler, cron, timer, bash, python, backup, rename files, batch, organise files, clean downloads

## Making a script
Use create_script. It saves the script in ~/Scripts, makes it runnable, checks it for syntax
errors, and can add a Start menu entry (start_menu=true) and a schedule.
- bash for simple file and system jobs, python3 for anything with logic or text processing.
- Begin bash scripts with `set -euo pipefail`. Quote every path ("$HOME/Pictures").
- Scripts cannot ask questions while they run (no keyboard). Put settings as variables at the top.
- Print what the script does, so the user sees it in the output window.
- For scripts that move or delete files, add a DRY_RUN=1 variable at the top that only prints.
- To fix or change a script, call create_script again with the same name (a backup is kept).
- Test it with run_command (`~/Scripts/NAME.sh`) when that is safe.
- A notification at the end: `notify-send "Backup" "Finished"`.

## Schedules (like Windows Task Scheduler)
create_script's schedule takes: 'daily at 18:30', 'every 15 minutes', 'hourly', 'every 2 hours',
'every Monday at 9:00', 'weekdays at 8am', 'weekends at 10:00', 'daily', 'weekly', 'monthly',
'at login', or 'none' to stop. Timers catch up when the PC was off at the planned time.
- All schedules: `systemctl --user list-timers`
- Output of scheduled runs: ~/.local/state/deepseek-shell/scripts/NAME.log
- Run a scheduled script right now: `systemctl --user start myscript-NAME.service`
- A failed scheduled run shows a notification.
- Login scripts live in ~/.config/autostart/myscript-NAME.desktop.

## Remove a script completely (ask the user first)
`systemctl --user disable --now myscript-NAME.timer; rm -f ~/.config/systemd/user/myscript-NAME.service ~/.config/systemd/user/myscript-NAME.timer ~/.config/autostart/myscript-NAME.desktop ~/.local/share/applications/myscript-NAME.desktop; systemctl --user daemon-reload; rm -f ~/Scripts/NAME.sh`

## Useful building blocks
- Back up a folder: `rsync -a --delete "$HOME/Documents/" "/media/$USER/USBNAME/Documents/"`
  (USB drives are mounted under /media/$USER/; check with `lsblk -o NAME,LABEL,MOUNTPOINTS`).
- Delete downloads older than 30 days: `find "$HOME/Downloads" -type f -mtime +30 -print -delete`
- Sort photos by date (python3): read the file date with os.path.getmtime and move into YYYY/MM folders.
- Resize images: needs ImageMagick (admin: `apt-get install -y imagemagick`), then `magick in.jpg -resize 50% out.jpg`.
- Convert videos: needs ffmpeg (admin: `apt-get install -y ffmpeg`).
- Download a file: `curl -fL -o "$HOME/Downloads/file" URL`.
