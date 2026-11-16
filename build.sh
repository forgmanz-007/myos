#!/usr/bin/env bash
# Build the installable ISO image of your OS.
#
#   ./build.sh            Build inside a Docker or Podman container.
#                         Works on Linux, on Windows (inside WSL2) and on macOS.
#   ./build.sh --native   Build directly on a Debian 13 machine (uses sudo).
#   ./build.sh --clean    Delete the download cache used by previous builds.
#
# The finished ISO lands in ./out/. Settings live in ./config.env.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
# shellcheck source=config.env
. ./config.env

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

case "$OS_ID" in
    ""|*[!a-z0-9]*) die "OS_ID in config.env must be lowercase letters and digits only (got '$OS_ID')." ;;
esac

find_engine() {
    for e in docker podman; do
        if command -v "$e" >/dev/null 2>&1; then echo "$e"; return 0; fi
    done
    return 1
}

MODE=container
case "${1:-}" in
    --native) MODE=native ;;
    --clean)
        if ENGINE=$(find_engine); then
            "$ENGINE" volume rm -f "${OS_ID}-build-cache" >/dev/null 2>&1 || true
        fi
        sudo rm -rf "/var/tmp/${OS_ID}-build" 2>/dev/null || true
        say "Build cache removed."
        exit 0 ;;
    -h|--help) usage ;;
    "") ;;
    *) die "Unknown option '$1'. Try ./build.sh --help" ;;
esac

mkdir -p out

if [ "$MODE" = native ]; then
    [ -r /etc/debian_version ] || die "--native only works on Debian. Use ./build.sh (container) instead."
    if [ "$(id -u)" -ne 0 ]; then
        say "Native build needs root, asking sudo..."
        exec sudo --preserve-env=HOME,GITHUB_TOKEN bash "$HERE/build.sh" --native
    fi
    exec bash "$HERE/scripts/container-build.sh" "$HERE" "$HERE/out" "/var/tmp/${OS_ID}-build"
fi

ENGINE=$(find_engine) || die "Docker or Podman is needed. Install Docker Desktop (Windows/macOS) or 'sudo apt install docker.io' (Linux), or use --native on Debian 13."

PLATFORM_ARGS=()
case "$(uname -m)" in
    x86_64|amd64) ;;
    *)
        warn "This computer is $(uname -m), not x86_64. The build will run under emulation and can take several hours."
        PLATFORM_ARGS=(--platform linux/amd64) ;;
esac

# A GitHub token, if set (GitHub's own build sets one): GitHub's API limits anonymous
# downloads of release information per address.
ENV_ARGS=()
if [ -n "${GITHUB_TOKEN:-}" ]; then ENV_ARGS=(-e GITHUB_TOKEN); fi

say "Building ${OS_NAME} with ${ENGINE}. This takes 20-60 minutes and needs about 25 GB of free disk space."
"$ENGINE" run --rm --privileged "${PLATFORM_ARGS[@]}" "${ENV_ARGS[@]}" \
    -v "$HERE":/src:ro \
    -v "$HERE/out":/out \
    -v "${OS_ID}-build-cache":/work/cache \
    "debian:${DEBIAN_RELEASE}" \
    bash /src/scripts/container-build.sh /src /out /work

say "Finished. Your ISO is in: $HERE/out/"
ls -lh "$HERE"/out/*.iso 2>/dev/null || true
