#!/usr/bin/env bash
# build-sdl3-render.sh — build SDL3 (pinned in ../sdl3/SDL3.pin) with VIDEO + RENDER + the
# SOFTWARE renderer, but NO GPU/windowing backend (no GL/GLES/Vulkan/X11/Wayland), static,
# for x86_64 and aarch64. This is the tsp-an4.4 variant: it lets a headless app create an
# SDL software renderer on an off-screen surface and read the pixels back — on a GPU-LESS
# host, NO blob touched. (The gamepad-only ../sdl3 build stays for SPIKE-3/tsp-an4.3.)
#
# HONESTY: this is upstream SDL3's portable SOFTWARE rasterizer, explicitly NOT the on-device
# libSDL3-pocketforge sunxifb backend nor the PowerVR/dc_sunxi/DE2.0/fb0 path. It proves
# layout/widget logic only (see README.md).
#
#   Build deps: cmake ninja-build gcc g++ aarch64-linux-gnu-{gcc,g++} libc6-dev-arm64-cross
#   Usage: ./build-sdl3-render.sh           # OUT=, SRC= overridable
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PIN="$HERE/../sdl3/SDL3.pin"
OUT="${OUT:-/home/mm/sim-build/sdl3-render}"
SRC="${SRC:-/home/mm/sim-build/sdl3/SDL}"   # reuse the SPIKE-3 clone if present

REPO=$(sed -n 's/^repo *= *//p' "$PIN")
TAG=$(sed -n  's/^tag *= *//p'  "$PIN")
mkdir -p "$OUT"
if [ ! -d "$SRC/.git" ]; then
  echo "== clone $REPO @ $TAG =="
  git clone --quiet --depth 1 --branch "$TAG" "$REPO" "$SRC"
fi

# VIDEO + RENDER + software rasterizer ON; every GPU/windowing/runtime-.so backend OFF so the
# static lib has no undefined externals and no GL is ever dlopen'd (=> cannot trip tsp-osr's
# GL path). The dummy video driver (console build) gives a windowing stub; the real frame is
# a software-rendered off-screen surface the app reads back.
COMMON_FLAGS=(
  -G Ninja
  -DCMAKE_BUILD_TYPE=Release
  -DSDL_STATIC=ON -DSDL_SHARED=OFF
  -DSDL_TEST_LIBRARY=OFF -DSDL_EXAMPLES=OFF -DSDL_TESTS=OFF -DSDL_INSTALL_TESTS=OFF
  -DSDL_UNIX_CONSOLE_BUILD=ON
  -DSDL_VIDEO=ON -DSDL_RENDER=ON
  -DSDL_AUDIO=OFF -DSDL_GPU=OFF -DSDL_CAMERA=OFF
  -DSDL_OPENGL=OFF -DSDL_OPENGLES=OFF -DSDL_VULKAN=OFF -DSDL_RENDER_GPU=OFF
  -DSDL_X11=OFF -DSDL_WAYLAND=OFF -DSDL_DBUS=OFF -DSDL_IBUS=OFF
  -DSDL_UDEV=OFF -DSDL_HIDAPI=OFF -DSDL_LIBUSB=OFF
  -DSDL_PULSEAUDIO=OFF -DSDL_PIPEWIRE=OFF -DSDL_ALSA=OFF -DSDL_JACK=OFF -DSDL_SNDIO=OFF
  -DSDL_SENSOR=OFF -DSDL_HAPTIC=OFF -DSDL_POWER=OFF -DSDL_DIALOG=OFF
  -DSDL_JOYSTICK=ON
)

build_one() {
  local arch="$1"; shift
  local bdir="$OUT/$arch-build" pfx="$OUT/$arch"
  echo "== configure SDL3-render ($arch) =="
  rm -rf "$bdir"
  cmake -S "$SRC" -B "$bdir" "${COMMON_FLAGS[@]}" "$@" -DCMAKE_INSTALL_PREFIX="$pfx" >/dev/null
  echo "== build + install SDL3-render ($arch) =="
  cmake --build "$bdir" --parallel >/dev/null
  cmake --install "$bdir" >/dev/null
  echo "   installed: $(ls "$pfx"/lib/libSDL3.a)"
}

build_one x86
build_one arm64 -DCMAKE_TOOLCHAIN_FILE="$HERE/../sdl3/aarch64-toolchain.cmake"
echo "DONE — SDL3-render static libs in $OUT/{x86,arm64}"
