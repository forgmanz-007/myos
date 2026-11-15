#!/usr/bin/env bash
# Runs as root inside the build container (or on a Debian 13 host with --native).
# Usage: container-build.sh <source dir> <output dir> <work dir>
set -euo pipefail

SRC=${1:?source dir}
OUT=${2:?output dir}
WORK=${3:?work dir}

# shellcheck source=../config.env
. "$SRC/config.env"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
# Quote a value for POSIX sh.
shq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# ── Validate settings ───────────────────────────────────────────────────────
[[ "$OS_NAME" =~ ^[A-Za-z0-9\ ._()+-]{1,32}$ ]] || die "OS_NAME may only use letters, digits, spaces and . _ ( ) + - (max 32 characters)."
[[ "$OS_ID" =~ ^[a-z0-9]{1,20}$ ]] || die "OS_ID must be 1-20 lowercase letters/digits."
[[ "$DEFAULT_MODEL" =~ ^[A-Za-z0-9._:/-]*$ ]] || die "DEFAULT_MODEL contains invalid characters."
[[ "$OLLAMA_VERSION" =~ ^[0-9a-z.-]*$ ]] || die "OLLAMA_VERSION contains invalid characters."
for p in $EXTRA_PACKAGES; do
    [[ "$p" =~ ^[a-z0-9][a-z0-9.+-]+$ ]] || die "EXTRA_PACKAGES: '$p' is not a valid Debian package name."
done
case "$KERNEL" in stable|backports) ;; *) die "KERNEL must be 'stable' or 'backports'." ;; esac
case "$BUNDLE_MODEL" in yes|no) ;; *) die "BUNDLE_MODEL must be 'yes' or 'no'." ;; esac
case "$AMD_GPU_SUPPORT" in yes|no) ;; *) die "AMD_GPU_SUPPORT must be 'yes' or 'no'." ;; esac
for v in FILE_MANAGER BUNDLE_PROTON CLAMAV_SIGNATURES VIDEO_WALLPAPERS; do
    case "${!v}" in yes|no) ;; *) die "$v must be 'yes' or 'no'." ;; esac
done
[[ "$PROTON_VERSION" =~ ^[A-Za-z0-9._-]*$ ]] || die "PROTON_VERSION contains invalid characters."
[[ "$VIDEO_WALLPAPER_VERSION" =~ ^[A-Za-z0-9._-]*$ ]] || die "VIDEO_WALLPAPER_VERSION contains invalid characters."
if [ "$BUNDLE_MODEL" = yes ] && [ -z "$DEFAULT_MODEL" ]; then
    DEFAULT_MODEL="deepseek-r1:8b"
fi

# ── Build tools ─────────────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
if ! command -v lb >/dev/null 2>&1; then
    log "Installing live-build"
    apt-get update -q
    apt-get install -y -q --no-install-recommends live-build debootstrap ca-certificates curl
fi
. /etc/os-release
if [ "${VERSION_CODENAME:-}" != "$DEBIAN_RELEASE" ]; then
    echo "Note: build host is '${VERSION_CODENAME:-unknown}', target is '$DEBIAN_RELEASE'. A Debian $DEBIAN_RELEASE host is recommended."
fi

# ── Assemble the live-build tree ────────────────────────────────────────────
log "Preparing $WORK"
mkdir -p "$WORK"
# Keep the package cache between builds, remove everything else.
find "$WORK" -mindepth 1 -maxdepth 1 ! -name cache -exec rm -rf {} +
cp -a "$SRC/iso/." "$WORK/"
chmod +x "$WORK"/auto/* "$WORK"/config/hooks/normal/*.hook.chroot

INC="$WORK/config/includes.chroot_after_packages"
log "Adding DeepSeek Shell"
bash "$SRC/deepseek-shell/install.sh" --destdir "$INC" --prefix /usr
log "Adding ClamAV Security, the .exe guard and Proton integration"
bash "$SRC/myos-security/install.sh" --destdir "$INC" --prefix /usr
# Source of the Properties-dialog tab; a hook compiles it inside the image.
install -d "$INC/usr/src/myos-compat-tab"
cp -a "$SRC/myos-security/kio-plugin/." "$INC/usr/src/myos-compat-tab/"
log "Adding the speed settings"
bash "$SRC/performance/install.sh" --destdir "$INC"
log "Adding Task Manager"
bash "$SRC/task-manager/install.sh" --destdir "$INC" --prefix /usr
log "Adding the Windows 10-style desktop"
bash "$SRC/desktop/install.sh" --destdir "$INC" --os-name "$OS_NAME"

# Settings read by auto/config (live-build) ...
{
    echo "OS_NAME=$(shq "$OS_NAME")"
    echo "OS_ID=$(shq "$OS_ID")"
    echo "DEBIAN_RELEASE=$(shq "$DEBIAN_RELEASE")"
    echo "LIVE_LOCALE=$(shq "$LIVE_LOCALE")"
    echo "LIVE_KEYBOARD=$(shq "$LIVE_KEYBOARD")"
    if [ "$KERNEL" = backports ]; then echo "BACKPORTS=true"; else echo "BACKPORTS=false"; fi
} > "$WORK/build.conf"

# ... and by the hooks inside the image.
install -d "$INC/etc/myos"
{
    echo "OS_NAME=$(shq "$OS_NAME")"
    echo "OS_ID=$(shq "$OS_ID")"
    echo "DEFAULT_MODEL=$(shq "$DEFAULT_MODEL")"
    echo "BUNDLE_MODEL=$(shq "$BUNDLE_MODEL")"
    echo "AMD_GPU_SUPPORT=$(shq "$AMD_GPU_SUPPORT")"
    echo "OLLAMA_VERSION=$(shq "$OLLAMA_VERSION")"
    echo "EXTRA_PACKAGES=$(shq "$EXTRA_PACKAGES")"
    echo "FILE_MANAGER=$(shq "$FILE_MANAGER")"
    echo "BUNDLE_PROTON=$(shq "$BUNDLE_PROTON")"
    echo "PROTON_VERSION=$(shq "$PROTON_VERSION")"
    echo "CLAMAV_SIGNATURES=$(shq "$CLAMAV_SIGNATURES")"
    echo "VIDEO_WALLPAPERS=$(shq "$VIDEO_WALLPAPERS")"
    echo "VIDEO_WALLPAPER_VERSION=$(shq "$VIDEO_WALLPAPER_VERSION")"
} > "$INC/etc/myos/build.conf"

install -d "$INC/etc/deepseek-shell"
cat > "$INC/etc/deepseek-shell/defaults.json" <<EOF
{
  "os_name": "$OS_NAME",
  "model": "$DEFAULT_MODEL"
}
EOF

if [ -n "$EXTRA_PACKAGES" ]; then
    printf '%s\n' $EXTRA_PACKAGES > "$WORK/config/package-lists/extra.list.chroot"
fi

if [ "$FILE_MANAGER" = yes ]; then
    printf '%s\n' dolphin kio-extras > "$WORK/config/package-lists/filemanager.list.chroot"
fi

if [ "$KERNEL" = backports ]; then
    sed -i "s/trixie-backports/${DEBIAN_RELEASE}-backports/" "$WORK"/config/archives/kernel-backports.pref.*
else
    rm -f "$WORK"/config/archives/kernel-backports.pref.*
fi

# ── Build ───────────────────────────────────────────────────────────────────
cd "$WORK"
log "Configuring live-build"
lb config
log "Building the image (this is the long part)"
set -o pipefail
lb build 2>&1 | tee "$OUT/build.log"

ISO=$(find "$WORK" -maxdepth 1 -name '*.iso' | head -n1)
[ -n "$ISO" ] || die "live-build finished without producing an ISO. See $OUT/build.log"
DEST="$OUT/${OS_ID}-$(date +%Y%m%d)-amd64.iso"
mv -f "$ISO" "$DEST"
(cd "$OUT" && sha256sum "$(basename "$DEST")" > "$(basename "$DEST").sha256")
# Give the files to whoever owns the output folder (not root).
chown --reference="$OUT" "$DEST" "$DEST.sha256" "$OUT/build.log" 2>/dev/null || true

log "Done: $DEST ($(du -h "$DEST" | cut -f1))"
