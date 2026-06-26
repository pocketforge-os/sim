#!/usr/bin/env bash
# build-sdl3.sh — build stock SDL3 (pinned in SDL3.pin) twice: native x86_64 and a fully
# static aarch64 cross-build, GAMEPAD-ONLY (no video/audio/udev/dbus/hidapi). The arm64
# static lib lets the SPIKE-3 probe run under qemu-tsp with no sysroot.
#
#   Build deps (Ubuntu 24.04): cmake ninja-build gcc g++ aarch64-linux-gnu-{gcc,g++}
#                              libc6-dev-arm64-cross
#   Usage: ./build-sdl3.sh                 # build both arches
#          OUT=/path SRC=/path ./build-sdl3.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-$HERE/build}"
SRC="${SRC:-$OUT/SDL}"

REPO=$(sed -n 's/^repo *= *//p' "$HERE/SDL3.pin")
TAG=$(sed -n  's/^tag *= *//p'  "$HERE/SDL3.pin")

mkdir -p "$OUT"
if [ ! -d "$SRC/.git" ]; then
  echo "== clone $REPO @ $TAG =="
  git clone --quiet --depth 1 --branch "$TAG" "$REPO" "$SRC"
fi

# Gamepad-only, static: strip every subsystem that drags a runtime .so (so libSDL3.a has
# no undefined externals when we static-link the probe). Keep JOYSTICK (=> gamepad).
COMMON_FLAGS=(
  -G Ninja
  -DCMAKE_BUILD_TYPE=Release
  -DSDL_STATIC=ON -DSDL_SHARED=OFF
  -DSDL_TEST_LIBRARY=OFF -DSDL_EXAMPLES=OFF -DSDL_TESTS=OFF -DSDL_INSTALL_TESTS=OFF
  # headless/console build: bypass SDL's "no X11/Wayland" FATAL_ERROR (cmake/macros.cmake)
  # — we want a gamepad-only library with no windowing at all.
  -DSDL_UNIX_CONSOLE_BUILD=ON
  -DSDL_VIDEO=OFF -DSDL_AUDIO=OFF -DSDL_RENDER=OFF -DSDL_GPU=OFF -DSDL_CAMERA=OFF
  -DSDL_OPENGL=OFF -DSDL_OPENGLES=OFF -DSDL_VULKAN=OFF
  -DSDL_X11=OFF -DSDL_WAYLAND=OFF -DSDL_DBUS=OFF -DSDL_IBUS=OFF
  -DSDL_UDEV=OFF -DSDL_HIDAPI=OFF -DSDL_LIBUSB=OFF
  -DSDL_PULSEAUDIO=OFF -DSDL_PIPEWIRE=OFF -DSDL_ALSA=OFF -DSDL_JACK=OFF -DSDL_SNDIO=OFF
  -DSDL_SENSOR=OFF -DSDL_HAPTIC=OFF -DSDL_POWER=OFF -DSDL_DIALOG=OFF
  -DSDL_JOYSTICK=ON
)

build_one() {  # $1 = arch label, $2... = extra cmake flags
  local arch="$1"; shift
  local bdir="$OUT/$arch-build" pfx="$OUT/$arch"
  echo "== configure SDL3 ($arch) =="
  rm -rf "$bdir"
  cmake -S "$SRC" -B "$bdir" "${COMMON_FLAGS[@]}" "$@" -DCMAKE_INSTALL_PREFIX="$pfx" >/dev/null
  echo "== build + install SDL3 ($arch) =="
  cmake --build "$bdir" --parallel >/dev/null
  cmake --install "$bdir" >/dev/null
  echo "   installed: $(ls "$pfx"/lib/libSDL3.a)"
}

build_one x86
build_one arm64 -DCMAKE_TOOLCHAIN_FILE="$HERE/aarch64-toolchain.cmake"

echo "== SDL3 static libs ready =="
file "$OUT/x86/lib/libSDL3.a" "$OUT/arm64/lib/libSDL3.a" 2>/dev/null || true
echo "DONE"
