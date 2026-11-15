#!/usr/bin/env bash
# Install Task Manager.
#   --destdir DIR   install into an image tree (used by the ISO build)
#   --prefix DIR    installation prefix (default /usr)
# Without --destdir it installs onto this computer: run it with sudo.
# Needs: python3, python3-pyqt6, pkexec (polkit).
set -euo pipefail
DESTDIR=""
PREFIX=/usr
while [ $# -gt 0 ]; do
    case "$1" in
        --destdir) DESTDIR=$2; shift 2 ;;
        --prefix)  PREFIX=$2; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
D="$DESTDIR$PREFIX"
LIBEXEC="$PREFIX/libexec/myos-taskmanager"

install -d "$D/lib/myos-taskmanager"
rm -rf "$D/lib/myos-taskmanager/myos_taskmanager"
cp -r "$HERE/myos_taskmanager" "$D/lib/myos-taskmanager/"
find "$D/lib/myos-taskmanager" -name __pycache__ -type d -prune -exec rm -rf {} +

install -Dm755 "$HERE/bin/myos-taskmanager" "$D/bin/myos-taskmanager"
install -Dm755 "$HERE/libexec/end-process" "$D/libexec/myos-taskmanager/end-process"
install -Dm644 "$HERE/data/myos-taskmanager.desktop" "$D/share/applications/myos-taskmanager.desktop"
# Ctrl+Shift+Esc opens Task Manager (KDE reads shortcuts of apps listed here).
install -Dm644 "$HERE/data/myos-taskmanager.desktop" "$D/share/kglobalaccel/myos-taskmanager.desktop"
install -Dm644 "$HERE/data/myos-taskmanager.svg" "$D/share/icons/hicolor/scalable/apps/myos-taskmanager.svg"

tmp=$(mktemp)
sed "s|@LIBEXECDIR@|$LIBEXEC|g" "$HERE/data/org.myos.taskmanager.policy.in" > "$tmp"
install -Dm644 "$tmp" "$DESTDIR/usr/share/polkit-1/actions/org.myos.taskmanager.policy"
rm -f "$tmp"
echo "Task Manager installed${DESTDIR:+ into $DESTDIR}."
