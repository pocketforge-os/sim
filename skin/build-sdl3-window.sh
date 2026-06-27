#!/usr/bin/env bash
# build-sdl3-window.sh — tsp-qc1.5: SDL3 with VIDEO + RENDER + the SOFTWARE renderer + X11 ON, x86
# only, for the LIVE --window demo (skin-render-window). Unlike fb/build-sdl3-render.sh (dummy
# video, offscreen-only), this opens a REAL X11 window. GL/Vulkan/Wayland stay OFF — tsp-osr-safe
# (software renderer; X11-only keeps the runtime dep surface small + container X-forward friendly).
#
# HONESTY: upstream SDL3's portable X11 + software rasterizer on the DEV host — NOT the on-device
# sunxifb/PowerVR path. A laptop convenience for the dogfood demo, NOT a CI/acceptance artifact.
#
#   Build deps: cmake ninja-build gcc pkg-config + X11 dev headers (libx11-dev libxext-dev
#               libxcursor-dev libxi-dev libxrandr-dev libxfixes-dev libxkbcommon-dev)
#   Usage: OUT=/sdl3-window SRC=/sdl3/SDL ./build-sdl3-window.sh        # x86 only
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PIN="$HERE/../sdl3/SDL3.pin"
OUT="${OUT:-/sdl3-window}"
SRC="${SRC:-$OUT/SDL}"
REPO=$(sed -n 's/^repo *= *//p' "$PIN"); TAG=$(sed -n 's/^tag *= *//p' "$PIN")
mkdir -p "$OUT"
[ -d "$SRC/.git" ] || git clone --quiet --depth 1 --branch "$TAG" "$REPO" "$SRC"

cmake -S "$SRC" -B "$OUT/x86-build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DSDL_STATIC=ON -DSDL_SHARED=OFF \
  -DSDL_TEST_LIBRARY=OFF -DSDL_EXAMPLES=OFF -DSDL_TESTS=OFF -DSDL_INSTALL_TESTS=OFF \
  -DSDL_VIDEO=ON -DSDL_RENDER=ON -DSDL_X11=ON \
  -DSDL_AUDIO=OFF -DSDL_GPU=OFF -DSDL_CAMERA=OFF \
  -DSDL_OPENGL=OFF -DSDL_OPENGLES=OFF -DSDL_VULKAN=OFF -DSDL_RENDER_GPU=OFF \
  -DSDL_WAYLAND=OFF -DSDL_DBUS=OFF -DSDL_IBUS=OFF \
  -DSDL_UDEV=OFF -DSDL_HIDAPI=OFF -DSDL_LIBUSB=OFF \
  -DSDL_PULSEAUDIO=OFF -DSDL_PIPEWIRE=OFF -DSDL_ALSA=OFF -DSDL_JACK=OFF -DSDL_SNDIO=OFF \
  -DSDL_SENSOR=OFF -DSDL_HAPTIC=OFF -DSDL_POWER=OFF -DSDL_DIALOG=OFF \
  -DSDL_JOYSTICK=ON \
  -DCMAKE_INSTALL_PREFIX="$OUT/x86" >/dev/null
cmake --build "$OUT/x86-build" --parallel >/dev/null
cmake --install "$OUT/x86-build" >/dev/null
echo "DONE — SDL3-window (X11, software) static lib in $OUT/x86"
file "$OUT/x86/lib/libSDL3.a" 2>/dev/null || true
