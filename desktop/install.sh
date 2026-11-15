#!/usr/bin/env bash
# Install the Windows 10-style Plasma setup.
#   --destdir DIR   install into an image tree (used by the ISO build)
# Without --destdir it installs onto this computer (run with sudo); it then
# applies to new users. For your current user run afterwards:
#   plasma-apply-lookandfeel -a org.myos.desktop --resetLayout
#   cp /etc/skel/.config/kglobalshortcutsrc ~/.config/   (then log out and in)
# The Start menu with tiles, the icons and the font are downloaded by the ISO build
# (iso/config/hooks/normal/0420-windows10-look.hook.chroot, using bin/myos-install-look).
set -euo pipefail
DESTDIR=""
OS_NAME="MyOS"
while [ $# -gt 0 ]; do
    case "$1" in
        --destdir) DESTDIR=$2; shift 2 ;;
        --os-name) OS_NAME=$2; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -d "$DESTDIR/usr/share/plasma/look-and-feel"
rm -rf "$DESTDIR/usr/share/plasma/look-and-feel/org.myos.desktop"
cp -r "$HERE/look-and-feel/org.myos.desktop" "$DESTDIR/usr/share/plasma/look-and-feel/"

for f in "$HERE"/xdg/*; do
    install -Dm644 "$f" "$DESTDIR/etc/xdg/$(basename "$f")"
done
# Apps read their colours from kdeglobals: add the MyOS 10 colours (white windows, blue
# selection) to the system-wide one, so every user has them from the first start.
{
    echo
    echo "# The colours of colors/MyOS10.colors"
    awk '/^\[/ { keep = ($0 ~ /^\[(Colors|ColorEffects):/ || $0 == "[WM]") } keep' "$HERE/colors/MyOS10.colors"
} >> "$DESTDIR/etc/xdg/kdeglobals"

# Windows 10 look: dark taskbar and pop-ups (Plasma style), window colours, title bars,
# and the taskbar's search box, Task View button and Show Desktop strip.
install -d "$DESTDIR/usr/share/plasma/desktoptheme" "$DESTDIR/usr/share/aurorae/themes" \
    "$DESTDIR/usr/share/plasma/plasmoids"
rm -rf "$DESTDIR/usr/share/plasma/desktoptheme/myos10" "$DESTDIR/usr/share/aurorae/themes/MyOS10"
cp -r "$HERE/plasma-style/myos10" "$DESTDIR/usr/share/plasma/desktoptheme/"
cp -r "$HERE/aurorae/MyOS10" "$DESTDIR/usr/share/aurorae/themes/"
install -Dm644 "$HERE/colors/MyOS10.colors" "$DESTDIR/usr/share/color-schemes/MyOS10.colors"
for p in "$HERE"/plasmoids/*; do
    rm -rf "$DESTDIR/usr/share/plasma/plasmoids/$(basename "$p")"
    cp -r "$p" "$DESTDIR/usr/share/plasma/plasmoids/"
done
# Web pages and programs asking for Windows' Segoe UI font get Selawik.
install -Dm644 "$HERE/fonts/60-myos-segoe-ui.conf" "$DESTDIR/etc/fonts/conf.d/60-myos-segoe-ui.conf"
# The Recycle Bin on the desktop (once per user, at the first login).
install -Dm755 "$HERE/bin/myos-desktop-icons" "$DESTDIR/usr/libexec/myos/desktop-icons"
install -Dm644 "$HERE/autostart/myos-desktop-icons.desktop" \
    "$DESTDIR/etc/xdg/autostart/myos-desktop-icons.desktop"
# Installs the downloaded Start menu, icons and font (used by the ISO build).
install -Dm755 "$HERE/bin/myos-install-look" "$DESTDIR/usr/libexec/myos/install-look"
# File Explorer like Windows 10's: File / Home / View menus above the address bar, and the
# title bar shows just the folder (KWin's title bar files, adapted; redone after updates).
install -Dm644 "$HERE/explorer/dolphinui.rc" "$DESTDIR/usr/local/share/kxmlgui5/dolphin/dolphinui.rc"
install -Dm644 "$HERE/skel/.local/state/dolphinstaterc" "$DESTDIR/etc/skel/.local/state/dolphinstaterc"
install -Dm755 "$HERE/bin/myos-title-bars" "$DESTDIR/usr/libexec/myos/title-bars"
install -Dm644 "$HERE/apt/80myos-title-bars" "$DESTDIR/etc/apt/apt.conf.d/80myos-title-bars"
cat > "$DESTDIR/etc/xdg/kcm-about-distrorc" <<CFG
[General]
LogoPath=/usr/share/icons/hicolor/scalable/apps/myos-logo.svg
Variant=$OS_NAME: Windows 10-style desktop with a local AI
CFG

install -Dm644 "$HERE/sddm/10-myos.conf" "$DESTDIR/etc/sddm.conf.d/10-myos.conf"
# The login screen shows the MyOS wallpaper.
install -Dm644 "$HERE/sddm/theme.conf.user" "$DESTDIR/usr/share/sddm/themes/breeze/theme.conf.user"
# Apps' desktop requests go to KDE's portal only (the GTK one leaked and then spun a core).
install -Dm644 "$HERE/portal/kde-portals.conf" "$DESTDIR/etc/xdg/xdg-desktop-portal/kde-portals.conf"
install -Dm644 "$HERE/skel/.config/kglobalshortcutsrc" "$DESTDIR/etc/skel/.config/kglobalshortcutsrc"
install -Dm644 "$HERE/icons/myos-start.svg" "$DESTDIR/usr/share/icons/hicolor/scalable/apps/myos-start.svg"
install -Dm644 "$HERE/icons/myos-logo.svg" "$DESTDIR/usr/share/icons/hicolor/scalable/apps/myos-logo.svg"
install -Dm644 "$HERE/wallpaper/myos.svg" "$DESTDIR/usr/share/wallpapers/myos.svg"
# Pictures and videos as wallpaper: the command, and right-click > Set as Wallpaper for videos.
install -Dm755 "$HERE/bin/myos-set-wallpaper" "$DESTDIR/usr/bin/myos-set-wallpaper"
install -Dm644 "$HERE/servicemenus/myos-video-wallpaper.desktop" \
    "$DESTDIR/usr/share/kio/servicemenus/myos-video-wallpaper.desktop"
# Start menu favorites for new users (kept after Plasma updates by an APT hook).
install -Dm755 "$HERE/bin/myos-start-menu-defaults" "$DESTDIR/usr/libexec/myos/start-menu-defaults"
install -Dm644 "$HERE/apt/80myos-start-menu" "$DESTDIR/etc/apt/apt.conf.d/80myos-start-menu"
if [ -z "$DESTDIR" ]; then
    /usr/libexec/myos/start-menu-defaults || true
    /usr/libexec/myos/title-bars || true
fi
echo "Windows 10-style desktop installed${DESTDIR:+ into $DESTDIR}."
