#!/usr/bin/env bash
# build-rootfs.sh — build the SHARED arm64 Debian-bookworm rootfs the T1 sim runs the app
# inside (reused by tsp-an4.2/.3/.4/.5). Owner decision: run via qemu-tsp+binfmt inside
# BUBBLEWRAP, NO crun. bookworm = the device's target userland.
#
# Strategy: `docker export` an arm64 debian:bookworm container filesystem (no RUN needed, so
# no qemu-in-docker), unpack to a plain directory tree. SDL3 is vendored in separately by
# stage-sdl3.sh (bookworm ships no SDL3 package). The rootfs is a directory, not an image —
# bubblewrap binds it; nothing is installed on the host.
#
#   Usage: ./build-rootfs.sh [OUTDIR]      (default ./build/rootfs-arm64)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/build/rootfs-arm64}"
TAG="debian:bookworm"

command -v docker >/dev/null || { echo "FATAL: docker required to export the base rootfs"; exit 1; }
mkdir -p "$OUT"

echo "== pull $TAG (arm64) =="
docker pull --quiet --platform linux/arm64 "$TAG" >/dev/null
echo "== export rootfs to $OUT =="
cid=$(docker create --platform linux/arm64 "$TAG" /bin/true)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
docker export "$cid" | tar -x -C "$OUT"
# minimal sanity: arm64 ELF (follow the /bin/sh -> dash symlink with -L; or check dash).
file -L "$OUT/bin/sh" 2>/dev/null | grep -q "ARM aarch64" && echo "   rootfs is arm64 OK" || \
  { echo "FATAL: exported rootfs is not arm64"; file -L "$OUT/bin/sh"; exit 1; }
echo "DONE: $OUT"
