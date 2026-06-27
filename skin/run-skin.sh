#!/usr/bin/env bash
# run-skin.sh — tsp-an4.6 end-to-end: prove the CLICKABLE SKIN drives the SAME control surface as
# the headless inject, for BOTH descriptors, on the IDENTICAL arm64 app under qemu-tsp + native
# parity (NO crun). Compiles the .5 app (hwprobe-lite) + builds the .6 SDL3 renderer
# (skin-render), then runs check-skin.py under sudo (uinput + the bound event nodes are root-only).
#
# Env (mirrors run-control.sh):
#   QEMU_TSP  qemu-tsp/build/qemu-tsp/qemu-aarch64                            (required)
#   SDLR      SDL3-render build dir (x86/ + arm64/; fb/build-sdl3-render.sh)  (required)
#   ROOTFS    arm64 bookworm rootfs for the harness                          (required)
#   PLATFORM  platform checkout (descriptors + skins)                        (required)
#   DEVICES   space-separated device ids                                     (default "a133 a523")
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CTRL="$HERE/../control"
QEMU_TSP="${QEMU_TSP:?set QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64}"
SDLR="${SDLR:?set SDLR=/home/mm/sim-build/sdl3-render}"
ROOTFS="${ROOTFS:?set ROOTFS=/home/mm/sim-build/harness/rootfs-arm64}"
PLATFORM="${PLATFORM:?set PLATFORM=/home/mm/platform}"
DEVICES="${DEVICES:-a133 a523}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

echo "== compile hwprobe-lite (.5 app: SDL3-render x86 + static arm64) =="
gcc -O2 -I"$SDLR/x86/include" -o "$WORK/hwprobe-lite.x86" "$CTRL/hwprobe-lite.c" \
    "$SDLR/x86/lib/libSDL3.a" -lm -ldl -lpthread -lrt
aarch64-linux-gnu-gcc -O2 -static -I"$SDLR/arm64/include" -o "$WORK/hwprobe-lite.arm64" \
    "$CTRL/hwprobe-lite.c" "$SDLR/arm64/lib/libSDL3.a" -lm -ldl -lpthread -lrt
echo "   $(file "$WORK/hwprobe-lite.arm64" | cut -d, -f1-2)"

echo "== build skin-render (.6 SDL3 renderer; --shot offscreen path) =="
SDLR="$SDLR" bash "$HERE/build-skin-render.sh" "$WORK/skin-render"

sudo modprobe uinput 2>/dev/null || true

echo "== drive clickable skin + assert (check-skin.py) =="
sudo env APP_X86="$WORK/hwprobe-lite.x86" APP_ARM64="$WORK/hwprobe-lite.arm64" \
     QEMU_TSP="$QEMU_TSP" ROOTFS="$ROOTFS" PLATFORM="$PLATFORM" SKIN_RENDER="$WORK/skin-render" \
     python3 "$HERE/check-skin.py" $DEVICES
