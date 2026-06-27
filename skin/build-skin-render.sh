#!/usr/bin/env bash
# build-skin-render.sh — tsp-an4.6: build the SDL3 clickable-skin renderer.
#
# Built against the sim's SDL3-render static lib (fb/build-sdl3-render.sh: VIDEO+RENDER+software,
# GL/X11/Wayland OFF, static) — so the OFFSCREEN --shot path (the proof + the owner artifacts)
# is dependency-free and reproducible on mm. The --window LIVE path compiles into the same binary
# but needs a video-capable SDL3 + a real display at runtime (a laptop-desktop convenience, NOT
# the CI/acceptance path); on the dummy-video sdl3-render lib SDL_CreateWindow simply returns
# NULL and the binary reports it. Font is the committed, generated font8x13.h (no PIL/SDL_ttf).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SDLR="${SDLR:?set SDLR=/home/mm/sim-build/sdl3-render}"
OUT="${1:-$HERE/skin-render}"
gcc -O2 -I"$SDLR/x86/include" -o "$OUT" "$HERE/skin-render.c" \
    "$SDLR/x86/lib/libSDL3.a" -lm -ldl -lpthread -lrt
echo "built $OUT ($(file "$OUT" | cut -d, -f1-2))"
