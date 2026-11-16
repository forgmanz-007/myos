#!/usr/bin/env bash
# Install the automatic updates: myos-update and the timer that runs it every few hours.
#   --destdir DIR   install into an image tree (used by the ISO build)
# Without --destdir it installs onto this computer (run with sudo). The ISO build also
# writes /etc/myos/update.conf (where to look), /etc/myos/version, and enables the timer
# (iso/config/hooks/normal/0800-system.hook.chroot).
set -euo pipefail
DESTDIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --destdir) DESTDIR=$2; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -Dm755 "$HERE/bin/myos-update" "$DESTDIR/usr/bin/myos-update"
install -Dm644 "$HERE/systemd/myos-update.service" "$DESTDIR/usr/lib/systemd/system/myos-update.service"
install -Dm644 "$HERE/systemd/myos-update.timer" "$DESTDIR/usr/lib/systemd/system/myos-update.timer"
echo "Automatic updates installed${DESTDIR:+ into $DESTDIR}."
