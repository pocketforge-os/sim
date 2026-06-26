#!/usr/bin/env bash
# run-fb.sh — tsp-an4.4 end-to-end: software-render a known pattern to a VIRTUAL framebuffer
# on a GPU-LESS host, dump it to PNG, and assert — both native-x86 and with the IDENTICAL
# arm64 binary under qemu-tsp inside bubblewrap (the shared sim harness, NO crun).
#
# Per device: canvas size + rotation come from the descriptor (screens[0]); the renderer is
# the tsp-osr-safe software path. Proves layout/widget logic + the renderer recipe on a host
# with NO GPU. Does NOT prove the on-device PowerVR/sunxifb path (HONESTY — see README.md).
#
# Env:
#   QEMU_TSP  qemu-tsp/build/qemu-tsp/qemu-aarch64                      (required)
#   SDLR      SDL3-render build dir (x86/ + arm64/; build-sdl3-render.sh) (required)
#   ROOTFS    arm64 bookworm rootfs for the harness                     (required)
#   PLATFORM  platform checkout (descriptors)                           (required)
#   DEVICES   space-separated device ids                                (default "a133 a523")
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
QEMU_TSP="${QEMU_TSP:?set QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64}"
SDLR="${SDLR:?set SDLR=/home/mm/sim-build/sdl3-render}"
ROOTFS="${ROOTFS:?set ROOTFS=/home/mm/sim-build/harness/rootfs-arm64}"
PLATFORM="${PLATFORM:?set PLATFORM=/home/mm/platform}"
DEVICES="${DEVICES:-a133 a523}"
HARNESS="$HERE/../harness/run-in-harness.sh"
OUT="$HERE/baseline"; mkdir -p "$OUT"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

echo "== compile fb-render (SDL3-render: x86 + static arm64) =="
gcc -O2 -I"$SDLR/x86/include" -o "$WORK/fb-render.x86" "$HERE/fb-render.c" \
    "$SDLR/x86/lib/libSDL3.a" -lm -ldl -lpthread -lrt
aarch64-linux-gnu-gcc -O2 -static -I"$SDLR/arm64/include" -o "$WORK/fb-render.arm64" "$HERE/fb-render.c" \
    "$SDLR/arm64/lib/libSDL3.a" -lm -ldl -lpthread -lrt
echo "   $(file "$WORK/fb-render.arm64" | cut -d, -f1-2)"

OVERALL=0
for DEV in $DEVICES; do
  echo ""
  echo "############## $DEV ##############"
  OD="$OUT/$DEV"; mkdir -p "$OD"
  read -r W H ROT < <(python3 - "$PLATFORM" "$DEV" <<'PY'
import sys, tomllib
plt, dev = sys.argv[1], sys.argv[2]
d = tomllib.load(open(f"{plt}/devices/{dev}/capabilities.toml", "rb"))
s = d["screens"][0]; rc = s["render_canvas"]
print(rc["w"], rc["h"], s.get("rotation", "none"))
PY
)
  echo "   descriptor screen: ${W}x${H} rotation=$ROT"

  echo "== render NATIVE x86 =="
  "$WORK/fb-render.x86" --canvas "${W}x${H}" --rotation "$ROT" \
      --out "$OD/canvas.x86.ppm" --present-out "$OD/present.x86.ppm" 2> "$OD/render.x86.log"

  echo "== render arm64 UNDER qemu-tsp + bubblewrap (identical binary) =="
  sudo OUT_BIND="$OD" QEMU_TSP="$QEMU_TSP" ROOTFS="$ROOTFS" bash "$HARNESS" "$WORK/fb-render.arm64" \
      --canvas "${W}x${H}" --rotation "$ROT" \
      --out /out/canvas.arm64.ppm --present-out /out/present.arm64.ppm 2> "$OD/render.arm64.log" || true
  grep -q 'tsp-osr-pin' "$OD/render.arm64.log" || echo "   (note: see $OD/render.arm64.log)"

  echo "== PPM -> PNG artifact =="
  python3 "$HERE/ppm2png.py" "$OD/canvas.x86.ppm" "$OD/canvas.png"

  echo "== ASSERT (check-fb.py) =="
  if python3 "$HERE/check-fb.py" --device "$DEV" --platform "$PLATFORM" --out "$OD"; then :; else OVERALL=1; fi
done

echo ""
[ "$OVERALL" = 0 ] && echo "ALL DEVICES PASS ($DEVICES)" || echo "SOME DEVICE FAILED"
exit $OVERALL
