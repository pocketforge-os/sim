#!/usr/bin/env bash
# run-control.sh — tsp-an4.5 end-to-end: drive the ONE injection-as-API control surface and
# assert the headless contract for BOTH descriptors, with the IDENTICAL arm64 binary under
# qemu-tsp+bubblewrap AND the native x86 build (byte-identical-frame parity). NO crun.
#
# Compiles hwprobe-lite (the app that reads the synth uinput + lights the pressed control onto a
# virtual fb), then runs check-control.py (the CI-gate entrypoint) under sudo (uinput + the
# bound event nodes are root-only).
#
# Env:
#   QEMU_TSP  qemu-tsp/build/qemu-tsp/qemu-aarch64                       (required)
#   SDLR      SDL3-render build dir (x86/ + arm64/; fb/build-sdl3-render.sh) (required)
#   ROOTFS    arm64 bookworm rootfs for the harness                      (required)
#   PLATFORM  platform checkout (descriptors)                            (required)
#   DEVICES   space-separated device ids                                 (default "a133 a523")
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
QEMU_TSP="${QEMU_TSP:?set QEMU_TSP (baked in the pocketforge-sim image; see docker/README.md)}"
SDLR="${SDLR:?set SDLR (baked in the pocketforge-sim image)}"
ROOTFS="${ROOTFS:?set ROOTFS (baked in the pocketforge-sim image)}"
PLATFORM="${PLATFORM:?set PLATFORM (baked in the pocketforge-sim image)}"
DEVICES="${DEVICES:-a133 a523}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

echo "== compile hwprobe-lite (SDL3-render: x86 + static arm64) =="
gcc -O2 -I"$SDLR/x86/include" -o "$WORK/hwprobe-lite.x86" "$HERE/hwprobe-lite.c" \
    "$SDLR/x86/lib/libSDL3.a" -lm -ldl -lpthread -lrt
aarch64-linux-gnu-gcc -O2 -static -I"$SDLR/arm64/include" -o "$WORK/hwprobe-lite.arm64" "$HERE/hwprobe-lite.c" \
    "$SDLR/arm64/lib/libSDL3.a" -lm -ldl -lpthread -lrt
echo "   $(file "$WORK/hwprobe-lite.arm64" | cut -d, -f1-2)"

sudo modprobe uinput 2>/dev/null || true

echo "== drive control surface + assert (check-control.py) =="
sudo env APP_X86="$WORK/hwprobe-lite.x86" APP_ARM64="$WORK/hwprobe-lite.arm64" \
     QEMU_TSP="$QEMU_TSP" ROOTFS="$ROOTFS" PLATFORM="$PLATFORM" \
     python3 "$HERE/check-control.py" $DEVICES
