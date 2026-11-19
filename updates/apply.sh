#!/usr/bin/env bash
# Installs this version of MyOS onto the computer it runs on. myos-update runs it as admin
# from the unpacked update package of a newer version (the whole project, as on GitHub).
#
#   apply.sh                        install this version onto this computer
#   apply.sh --destdir DIR          install the files into DIR instead (used by the tests)
#   apply.sh --seed --destdir DIR   only note that the image tree DIR has this version's
#                                   downloaded and compiled parts already (the ISO build makes
#                                   them with its hooks), so that updates do not redo them
#
# In this order:
#   1. the apps and settings: each part's install.sh, with the settings the ISO was built
#      with (/etc/myos/build.conf, from config.env), like scripts/container-build.sh;
#   2. the parts the ISO build downloads or compiles (STEPS below), only when the files they
#      come from changed since they last ran here;
#   3. the one-time changes in updates/migrations/ that did not run here yet;
#   4. refreshes systemd, the apps' Python bytecode, the app list and the icon cache.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTDIR=""
SEED=no
while [ $# -gt 0 ]; do
    case "$1" in
        --destdir) DESTDIR=$2; shift 2 ;;
        --seed) SEED=yes; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done
if [ "$SEED" = yes ] && [ -z "$DESTDIR" ]; then
    echo "--seed needs --destdir" >&2
    exit 1
fi
STATE="$DESTDIR/var/lib/myos/update"
install -d "$STATE"
touch "$STATE/steps" "$STATE/migrations"

OS_NAME=MyOS
VIDEO_WALLPAPERS=yes
if [ -r "$DESTDIR/etc/myos/build.conf" ]; then
    # shellcheck source=/dev/null
    . "$DESTDIR/etc/myos/build.conf"
fi

# Build hooks and one-time changes run as admin on this computer; into a folder only with a
# stand-in (the tests set MYOS_HOOK_RUNNER).
can_run_hooks() { [ -z "$DESTDIR" ] || [ -n "${MYOS_HOOK_RUNNER:-}" ]; }
run_hook() {
    if [ -n "${MYOS_HOOK_RUNNER:-}" ]; then
        "$MYOS_HOOK_RUNNER" "$SRC/$1" </dev/null
    else
        bash "$SRC/$1" </dev/null
    fi
}

# ── 1. The apps and settings ─────────────────────────────────────────────────
part() {   # part NAME [OPTIONS...]
    local name=$1
    shift
    echo "==> $name"
    bash "$SRC/$name/install.sh" ${DESTDIR:+--destdir "$DESTDIR"} "$@"
}
if [ "$SEED" = no ]; then
    part deepseek-shell --prefix /usr
    part myos-security --prefix /usr
    part performance
    part task-manager --prefix /usr
    part desktop --os-name "$OS_NAME"
    part updates
    # Switched off in config.env: hook 0450 removed the right-click entry from the ISO.
    if [ "$VIDEO_WALLPAPERS" != yes ]; then
        rm -f "$DESTDIR/usr/share/kio/servicemenus/myos-video-wallpaper.desktop"
    fi
fi

# ── 2. Parts the ISO build downloads or compiles ─────────────────────────────
# One per line: name, build hook, and the other files it comes from. Only hooks that are
# safe to run again on an installed system belong here; never 0600-trim-apps (it removes
# apps that were installed later).
STEPS="
properties-tab  iso/config/hooks/normal/0400-properties-tab.hook.chroot  myos-security/kio-plugin
windows10-look  iso/config/hooks/normal/0420-windows10-look.hook.chroot  desktop/bin/myos-install-look
video-wallpaper iso/config/hooks/normal/0450-video-wallpaper.hook.chroot
"
step_hash() {   # one checksum over the names and contents of the files (relative to SRC)
    (cd "$SRC" && find "$@" -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) \
        | sha256sum | cut -d' ' -f1
}
recorded() { awk -v n="$1" '$1 == n { h = $2 } END { print h }' "$STATE/steps"; }
record() {
    { awk -v n="$1" '$1 != n' "$STATE/steps"; echo "$1 $2"; } > "$STATE/steps.new"
    mv "$STATE/steps.new" "$STATE/steps"
}
while read -r name hook files <&3; do
    [ -n "$name" ] && [ -f "$SRC/$hook" ] || continue
    # shellcheck disable=SC2086   # the names hold no spaces
    hash=$(step_hash "$hook" $files)
    if [ "$SEED" = no ]; then
        [ "$(recorded "$name")" != "$hash" ] || continue
        if ! can_run_hooks; then
            echo "Skipped $name: build hooks only run on the computer itself."
            continue
        fi
        echo "==> $name"
        if [ "$name" = properties-tab ]; then
            # The hook compiles the source from here (and removes it afterwards) with tools it
            # installs for a moment: refresh the package lists first.
            rm -rf "$DESTDIR/usr/src/myos-compat-tab"
            install -d "$DESTDIR/usr/src/myos-compat-tab"
            cp -a "$SRC/myos-security/kio-plugin/." "$DESTDIR/usr/src/myos-compat-tab/"
            if [ -z "$DESTDIR" ]; then apt-get update -q || true; fi
        fi
        run_hook "$hook"
    fi
    record "$name" "$hash"
done 3<<< "$STEPS"

# ── 3. One-time changes (see updates/migrations/README.md) ───────────────────
for m in "$SRC"/updates/migrations/*.sh; do
    [ -f "$m" ] || continue
    name=$(basename "$m")
    ! grep -qxF "$name" "$STATE/migrations" || continue
    if [ "$SEED" = no ]; then
        if ! can_run_hooks; then
            echo "Skipped $name: one-time changes only run on the computer itself."
            continue
        fi
        echo "==> $name"
        run_hook "updates/migrations/$name"
    fi
    echo "$name" >> "$STATE/migrations"
done

# ── 4. Refresh what the new files need ───────────────────────────────────────
if [ "$SEED" = yes ]; then
    echo "Noted this version's downloaded and compiled parts in $STATE."
    exit 0
fi
if [ -z "$DESTDIR" ]; then
    systemctl daemon-reload || true
    # The apps' and Python's bytecode (ISOs up to 2026.11.16-6 shipped without it).
    /usr/libexec/myos/python-bytecode || true
    if command -v update-desktop-database >/dev/null; then update-desktop-database -q || true; fi
    if command -v gtk-update-icon-cache >/dev/null; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
echo "$OS_NAME ${MYOS_VERSION:-} installed${DESTDIR:+ into $DESTDIR}."
