#!/usr/bin/env bash
# build-harness.sh — stand up the SHARED T1 sim runtime harness (reused by tsp-an4.2/.3/.4/
# .5): an arm64 Debian-bookworm rootfs with a DYNAMIC arm64 SDL3 vendored in, runnable under
# qemu-tsp+binfmt inside BUBBLEWRAP (owner decision: NO crun). Proves the real app substrate
# — an arm64 *dynamic* ELF resolved against the rootfs's own ld-linux + libs, interpreted by
# qemu-tsp on an x86 host.
#
#   Deps: docker, bubblewrap, cmake, aarch64-linux-gnu-{gcc,g++}, git
#   Env:  OUT (default ./build)   SDLPIN (default ../sdl3/SDL3.pin)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-$HERE/build}"
ROOTFS="$OUT/rootfs-arm64"
SDLSRC="${SDLSRC:-$OUT/SDL}"
SDLPREFIX="$ROOTFS/usr/local"          # vendor SDL3 into the rootfs
PIN="${SDLPIN:-$HERE/../sdl3/SDL3.pin}"
REPO=$(sed -n 's/^repo *= *//p' "$PIN"); TAG=$(sed -n 's/^tag *= *//p' "$PIN")
mkdir -p "$OUT"

# 1) base rootfs (docker export; no RUN -> no qemu-in-docker needed)
if [ ! -x "$ROOTFS/bin/sh" ]; then
  "$HERE/build-rootfs.sh" "$ROOTFS"
fi

# 2) arm64 SDL3 SHARED, gamepad-only, installed INTO the rootfs prefix
if [ ! -e "$SDLPREFIX/lib/libSDL3.so.0" ] && [ ! -e "$SDLPREFIX/lib/aarch64-linux-gnu/libSDL3.so.0" ]; then
  [ -d "$SDLSRC/.git" ] || git clone --quiet --depth 1 --branch "$TAG" "$REPO" "$SDLSRC"
  bdir="$OUT/arm64-shared-build"; rm -rf "$bdir"
  echo "== configure SDL3 (arm64 shared, gamepad-only) =="
  cmake -S "$SDLSRC" -B "$bdir" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$HERE/../sdl3/aarch64-toolchain.cmake" \
    -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_UNIX_CONSOLE_BUILD=ON \
    -DSDL_TEST_LIBRARY=OFF -DSDL_EXAMPLES=OFF -DSDL_TESTS=OFF -DSDL_INSTALL_TESTS=OFF \
    -DSDL_VIDEO=OFF -DSDL_AUDIO=OFF -DSDL_RENDER=OFF -DSDL_GPU=OFF -DSDL_CAMERA=OFF \
    -DSDL_OPENGL=OFF -DSDL_OPENGLES=OFF -DSDL_VULKAN=OFF \
    -DSDL_X11=OFF -DSDL_WAYLAND=OFF -DSDL_DBUS=OFF -DSDL_IBUS=OFF \
    -DSDL_UDEV=OFF -DSDL_HIDAPI=OFF -DSDL_LIBUSB=OFF \
    -DSDL_PULSEAUDIO=OFF -DSDL_PIPEWIRE=OFF -DSDL_ALSA=OFF -DSDL_JACK=OFF -DSDL_SNDIO=OFF \
    -DSDL_SENSOR=OFF -DSDL_HAPTIC=OFF -DSDL_POWER=OFF -DSDL_DIALOG=OFF \
    -DSDL_JOYSTICK=ON -DCMAKE_INSTALL_PREFIX=/usr/local >/dev/null
  echo "== build + install SDL3 into rootfs =="
  cmake --build "$bdir" --parallel >/dev/null
  DESTDIR="$ROOTFS" cmake --install "$bdir" >/dev/null
fi

echo "== harness ready =="
echo "   rootfs: $ROOTFS"
find "$ROOTFS/usr/local" -name 'libSDL3.so*' -printf '   sdl3:   %p\n' 2>/dev/null | head
echo "DONE"
