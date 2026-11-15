#!/usr/bin/env bash
# Install ClamAV Security, the .exe guard and the Wine/Proton launcher.
#   --destdir DIR   install into an image tree (used by the ISO build)
#   --prefix DIR    installation prefix (default /usr)
# Without --destdir it installs onto this computer: run it with sudo.
# Needs: python3, python3-pyqt6, clamav, clamdscan; for programs: wine, umu-launcher.
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
LIBEXEC="$PREFIX/libexec/myos-security"

install -d "$D/lib/myos-security"
rm -rf "$D/lib/myos-security/myos_security"
cp -r "$HERE/myos_security" "$D/lib/myos-security/"
find "$D/lib/myos-security" -name __pycache__ -type d -prune -exec rm -rf {} +

for b in myos-security myos-run-exe myos-exe-properties myos-scan myos-compress; do
    install -Dm755 "$HERE/bin/$b" "$D/bin/$b"
done
install -Dm755 "$HERE/libexec/run-exe-binfmt" "$D/libexec/myos-security/run-exe-binfmt"
install -Dm755 "$HERE/libexec/update-signatures" "$D/libexec/myos-security/update-signatures"
install -Dm755 "$HERE/libexec/disk-usage" "$D/libexec/myos-security/disk-usage"
sed -i.bak "s|/usr/bin/myos-run-exe|$PREFIX/bin/myos-run-exe|" "$D/libexec/myos-security/run-exe-binfmt"
rm -f "$D/libexec/myos-security/run-exe-binfmt.bak"

install -Dm644 "$HERE/data/myos-security.desktop" "$D/share/applications/myos-security.desktop"
install -Dm644 "$HERE/data/myos-exe-runner.desktop" "$D/share/applications/myos-exe-runner.desktop"
install -Dm644 "$HERE/data/kiorc" "$DESTDIR/etc/xdg/kiorc"      # double-click runs, no "Execute?" question
# Windows programs opened from other apps (Chrome, xdg-open): to the check instead of KDE's refusal.
install -Dm755 "$HERE/libexec/kde-open" "$DESTDIR/usr/local/bin/kde-open"
install -Dm644 "$HERE/data/servicemenu-exe.desktop" "$D/share/kio/servicemenus/myos-exe.desktop"
install -Dm644 "$HERE/data/servicemenu-scan.desktop" "$D/share/kio/servicemenus/myos-scan.desktop"
install -Dm644 "$HERE/data/servicemenu-compress.desktop" "$D/share/kio/servicemenus/myos-compress.desktop"
install -Dm644 "$HERE/data/myos-security.svg" "$D/share/icons/hicolor/scalable/apps/myos-security.svg"

tmp=$(mktemp)
sed "s|@LIBEXECDIR@|$LIBEXEC|g" "$HERE/data/myos-exe.binfmt.in" > "$tmp"
install -Dm644 "$tmp" "$DESTDIR/usr/lib/binfmt.d/myos-exe.conf"
sed "s|@LIBEXECDIR@|$LIBEXEC|g" "$HERE/data/org.myos.security.policy.in" > "$tmp"
install -Dm644 "$tmp" "$DESTDIR/usr/share/polkit-1/actions/org.myos.security.policy"
rm -f "$tmp"
echo "ClamAV Security installed${DESTDIR:+ into $DESTDIR}."
