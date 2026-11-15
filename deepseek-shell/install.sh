#!/usr/bin/env bash
# Install DeepSeek Shell.
#   --destdir DIR   install into an image tree (used by the ISO build)
#   --prefix DIR    installation prefix (default /usr)
# Without --destdir it installs onto this computer: run it with sudo.
# Needs: python3, python3-pyqt6, pkexec (polkit), and Ollama for the AI engine.
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

install -d "$D/lib/deepseek-shell"
rm -rf "$D/lib/deepseek-shell/deepseek_shell"
cp -r "$HERE/deepseek_shell" "$D/lib/deepseek-shell/"
find "$D/lib/deepseek-shell" -name __pycache__ -type d -prune -exec rm -rf {} +

install -Dm755 "$HERE/bin/deepseek-shell" "$D/bin/deepseek-shell"
install -Dm755 "$HERE/libexec/run-as-root" "$D/libexec/deepseek-shell/run-as-root"
install -Dm755 "$HERE/libexec/diag-helper" "$D/libexec/deepseek-shell/diag-helper"

install -Dm644 "$HERE/data/deepseek-shell.desktop" "$D/share/applications/deepseek-shell.desktop"
# Win+C opens DeepSeek Shell (KDE reads shortcuts of apps listed here).
install -Dm644 "$HERE/data/deepseek-shell.desktop" "$D/share/kglobalaccel/deepseek-shell.desktop"
# "AI Assistant" page in System Settings (opens the tools and settings window).
install -Dm644 "$HERE/data/deepseek-shell-settings.desktop" \
    "$D/share/plasma/systemsettings/externalmodules/deepseek-shell-settings.desktop"
install -Dm644 "$HERE/data/deepseek-shell.svg" "$D/share/icons/hicolor/scalable/apps/deepseek-shell.svg"

policy=$(mktemp)
sed "s|@LIBEXECDIR@|$PREFIX/libexec/deepseek-shell|g" "$HERE/data/org.myos.deepseekshell.policy.in" > "$policy"
install -Dm644 "$policy" "$DESTDIR/usr/share/polkit-1/actions/org.myos.deepseekshell.policy"
rm -f "$policy"

install -d "$D/share/deepseek-shell"
rm -rf "$D/share/deepseek-shell/knowledge" "$D/share/deepseek-shell/tools.d"
cp -r "$HERE/data/knowledge" "$HERE/data/tools.d" "$D/share/deepseek-shell/"
cp "$HERE/data/deepseek-shell.svg" "$D/share/deepseek-shell/"

install -Dm644 "$HERE/data/ollama.service" "$DESTDIR/usr/lib/systemd/system/ollama.service"
echo "DeepSeek Shell installed${DESTDIR:+ into $DESTDIR}."
