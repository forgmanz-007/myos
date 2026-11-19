#!/usr/bin/env bash
# Install the speed and memory settings: visual effects that fit the graphics,
# compressed memory (like macOS), one app closed instead of the PC freezing when
# memory runs out (by the kernel, see memory-setup), a virus scanner that frees its
# memory when memory is short, and faster shutdown.
#   --destdir DIR   install into an image tree (used by the ISO build)
# Without --destdir it installs onto this computer (run with sudo);
# iso/config/hooks/normal/0850-performance.hook.chroot does the rest.
set -euo pipefail
DESTDIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --destdir) DESTDIR=$2; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -Dm755 "$HERE/bin/myos-performance" "$DESTDIR/usr/bin/myos-performance"
install -Dm755 "$HERE/libexec/clamd-idle-stop" "$DESTDIR/usr/libexec/myos/clamd-idle-stop"
# Python bytecode for faster app starts (run by the ISO build's last hook and by updates).
install -Dm755 "$HERE/libexec/python-bytecode" "$DESTDIR/usr/libexec/myos/python-bytecode"
install -Dm644 "$HERE/systemd/myos-clamd-idle.service" "$DESTDIR/usr/lib/systemd/system/myos-clamd-idle.service"
install -Dm644 "$HERE/systemd/myos-clamd-idle.timer" "$DESTDIR/usr/lib/systemd/system/myos-clamd-idle.timer"
install -Dm644 "$HERE/etc/60-myos-memory.conf" "$DESTDIR/etc/sysctl.d/60-myos-memory.conf"
# Compressed memory (zswap in front of the disk's swap, or zram without one).
install -Dm755 "$HERE/libexec/memory-setup" "$DESTDIR/usr/libexec/myos/memory-setup"
install -Dm644 "$HERE/systemd/myos-memory.service" "$DESTDIR/usr/lib/systemd/system/myos-memory.service"
rm -f "$DESTDIR/etc/systemd/zram-generator.conf"      # the earlier, fixed-size setup
# No systemd-oomd: tested, it closed an idle app before the one causing the trouble, and
# twice two apps where one was enough; the kernel's guard (memory-setup) came first anyway
# and closes the app using the most. Its settings are removed where they were installed.
rm -f "$DESTDIR/etc/systemd/oomd.conf.d/20-myos.conf" "$DESTDIR/etc/systemd/system/-.slice.d/20-myos-oomd.conf" \
      "$DESTDIR/etc/systemd/system/user@.service.d/20-myos-oomd.conf" "$DESTDIR/etc/systemd/user/app.slice.d/20-myos-oomd.conf"
# No "memory is almost full" warning (it was taken out: it would only nag). When an app
# is closed for lack of memory, one notice says which one (instead of Plasma's, which
# repeated itself).
install -Dm755 "$HERE/libexec/app-closed" "$DESTDIR/usr/libexec/myos/app-closed"
install -Dm644 "$HERE/systemd/myos-app-closed@.service" "$DESTDIR/usr/lib/systemd/user/myos-app-closed@.service"
rm -f "$DESTDIR/usr/lib/systemd/user/myos-app-closed.service"
install -Dm644 "$HERE/systemd/app-closed.conf" "$DESTDIR/usr/lib/systemd/user/app-.service.d/20-myos-app-closed.conf"
install -Dm644 "$HERE/systemd/app-closed.conf" "$DESTDIR/usr/lib/systemd/user/app-.scope.d/20-myos-app-closed.conf"
install -Dm644 "$HERE/xdg/kded5rc" "$DESTDIR/etc/xdg/kded5rc"
rm -f "$DESTDIR/etc/xdg/kded6rc"
rm -f "$DESTDIR/usr/libexec/myos/memory-watch" "$DESTDIR/usr/libexec/myos/memory-notice" \
      "$DESTDIR/usr/lib/systemd/user/myos-memory-watch.service" \
      "$DESTDIR/etc/systemd/user/graphical-session.target.wants/myos-memory-watch.service"
# Firefox: unload tabs that were not looked at for a while when memory runs low.
install -Dm644 "$HERE/firefox/myos-memory.js" "$DESTDIR/etc/firefox/defaults/pref/myos-memory.js"
install -Dm644 "$HERE/etc/journald-10-myos.conf" "$DESTDIR/etc/systemd/journald.conf.d/10-myos.conf"
install -Dm644 "$HERE/etc/systemd-10-myos.conf" "$DESTDIR/etc/systemd/system.conf.d/10-myos.conf"
install -Dm644 "$HERE/etc/systemd-10-myos.conf" "$DESTDIR/etc/systemd/user.conf.d/10-myos.conf"
install -Dm644 "$HERE/autostart/myos-performance.desktop" "$DESTDIR/etc/xdg/autostart/myos-performance.desktop"
install -Dm644 "$HERE/xdg/baloofilerc" "$DESTDIR/etc/xdg/baloofilerc"
echo "Speed settings installed${DESTDIR:+ into $DESTDIR}."
