#!/usr/bin/env bash
# run-spike3-harness.sh — prove the SHARED Path-B LAUNCHER (bubblewrap + qemu-tsp + arm64
# rootfs + the /dev/input device seam) runs an arm64 binary that enumerates the
# host-synthesized uinput gamepad IDENTICALLY to the static Path-A run. This validates the
# launcher mechanism the GUI (.4) and headless control surface (.5) sit on.
#
# SCOPE / HONEST FINDING (glibc): the rootfs is Debian bookworm (glibc 2.36); the modelmaker
# cross-toolchain links against the host's glibc (2.39), so a *dynamic* arm64 SDL3 built that
# way needs GLIBC_2.38 and will NOT run against the bookworm rootfs. The DYNAMIC-app harness
# (tsp-an4.3) must build the rootfs's SDL3 + the app against the BOOKWORM SYSROOT — ideally a
# multi-stage arm64-bookworm docker builder (ties to the reproducible-build epic tsp-cv7.6).
# We DEMONSTRATE that gap below (recorded to NOTE.dynamic-glibc.txt) and prove the launcher
# with a STATIC binary, which is glibc-independent.
#
#   Env: QEMU_TSP, OUT (harness build dir), SDLSTATIC (path-A arm64 STATIC SDL3 install),
#        PLATFORM (emit-sdldb), BASELINE (path-A baseline dir)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
QEMU_TSP="${QEMU_TSP:?set QEMU_TSP=...}"
OUT="${OUT:-$HERE/build}"
ROOTFS="$OUT/rootfs-arm64"
SDLSTATIC="${SDLSTATIC:?set SDLSTATIC (baked in the pocketforge-sim image)}"
PLATFORM="${PLATFORM:?set PLATFORM (baked in the pocketforge-sim image)}"
BASELINE="${BASELINE:-$HERE/../spike3/baseline}"
DEV="${DEV:-a133}"
RES="$HERE/build/spike3-harness"; mkdir -p "$RES"
CAPS="python3 $PLATFORM/core/caps.py"

SDLLIBDIR=$(dirname "$(find "$ROOTFS/usr/local/lib" -name 'libSDL3.so' | head -1)")
SDLINC="$ROOTFS/usr/local/include"

WORK="$(mktemp -d)"; trap 'sudo pkill -f "$WORK/mkuinput" 2>/dev/null || true; rm -rf "$WORK"' EXIT
cp "$HERE/../spike3/sdl3-gamepad-probe.c" "$HERE/../spike3/mkuinput.c" "$WORK/"

echo "== compile STATIC arm64 probe (glibc-independent; runs in any rootfs) =="
aarch64-linux-gnu-gcc -O2 -static -I"$SDLSTATIC/include" -o "$WORK/probe.static" \
    "$WORK/sdl3-gamepad-probe.c" "$SDLSTATIC/lib/libSDL3.a" -lm -ldl -lpthread -lrt
echo "== compile DYNAMIC arm64 probe (links rootfs SDL3 — to DEMONSTRATE the glibc gap) =="
aarch64-linux-gnu-gcc -O2 -I"$SDLINC" -o "$WORK/probe.dyn" "$WORK/sdl3-gamepad-probe.c" \
    -L"$SDLLIBDIR" -lSDL3 -Wl,-rpath-link,"$SDLLIBDIR"
gcc -O0 -o "$WORK/mkuinput" "$WORK/mkuinput.c"

echo "== create TRIMUI Player1 (host-native) =="
sudo pkill -f mkuinput 2>/dev/null || true; sleep 1
sudo modprobe uinput
sudo "$WORK/mkuinput" >/dev/null 2>&1 &
sleep 1

MAP="$($CAPS emit-sdldb --device "$DEV")"
run_static() { # $1 = mode -> $RES/out.harness.$1.json
  local mode="$1" env=(); [ "$mode" = descriptor ] && env=(SDL_GAMECONTROLLERCONFIG="$MAP")
  sudo env "${env[@]}" QEMU_TSP="$QEMU_TSP" ROOTFS="$ROOTFS" \
      bash "$HERE/run-in-harness.sh" "$WORK/probe.static" > "$RES/out.harness.$mode.json"
}

echo "== run STATIC probe inside bubblewrap+qemu-tsp+rootfs =="
run_static builtin
run_static descriptor

echo "== DEMONSTRATE the dynamic/glibc gap (expected to fail until tsp-an4.3 sysroot build) =="
if sudo env QEMU_TSP="$QEMU_TSP" ROOTFS="$ROOTFS" bash "$HERE/run-in-harness.sh" "$WORK/probe.dyn" \
     > "$RES/out.harness.dynamic.json" 2> "$RES/NOTE.dynamic-glibc.txt"; then
  echo "  NOTE: dynamic probe RAN (rootfs glibc satisfies the cross-built SDL3 — sysroot already matched)"
else
  echo "  NOTE: dynamic probe failed as expected (see NOTE.dynamic-glibc.txt) — sysroot build is tsp-an4.3"
  head -2 "$RES/NOTE.dynamic-glibc.txt" | sed 's/^/        /'
fi

echo "== compare STATIC harness run vs Path-A baseline =="
fail=0
for mode in builtin descriptor; do
  if diff -u "$BASELINE/out.x86.$mode.json" "$RES/out.harness.$mode.json" >/dev/null; then
    echo "  ok  harness(static,in-sandbox) $mode == path-A baseline ($mode)"
  else
    echo "FAIL  harness $mode differs from path-A baseline"
    diff -u "$BASELINE/out.x86.$mode.json" "$RES/out.harness.$mode.json" | head -20; fail=1
  fi
done
[ $fail -eq 0 ] && echo "PATH-B LAUNCHER: PASS — arm64 binary under bwrap+qemu-tsp+rootfs enumerates the gamepad == Path-A" \
                || { echo "PATH-B LAUNCHER: FAIL"; exit 1; }
