#!/usr/bin/env bash
# run-spike3.sh — SPIKE-3 (tsp-an4.2) end-to-end proof, on an x86_64 host with /dev/uinput.
#
# Proves a descriptor-synthesized `uinput` "TRIMUI Player1" is INDISTINGUISHABLE from real
# TrimUI hardware to SDL3 gamepad enumeration, with the arm64 probe running UNDER qemu-tsp
# (NOT just native x86 — native hides the stock-qemu evdev gap). Two SDL runs per arch:
#   builtin    — SDL's own 045e:028e mapping (proves auto-recognition + GUID)
#   descriptor — SDL_GAMECONTROLLERCONFIG = `caps.py emit-sdldb a133` (proves the
#                ONE-descriptor -> SDL mapping path binds on the live device)
# Plus an evdev-layer re-confirm: a133 descriptor codes are a subset of the advertised
# device (caps.py probe-diff), and the raw C evdev probe is byte-identical native-vs-qemu.
#
# Needs sudo (uinput + event node are root-only). Env:
#   QEMU_TSP   path to qemu-tsp/build/qemu-tsp/qemu-aarch64        (required)
#   SDLDIR     SDL3 build dir holding x86/ and arm64/ installs     (required)
#   PLATFORM   platform repo checkout (for core/caps.py + a133)    (required)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
QEMU_TSP="${QEMU_TSP:?set QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64}"
SDLDIR="${SDLDIR:?set SDLDIR=/home/mm/sim-build/sdl3}"
PLATFORM="${PLATFORM:?set PLATFORM=/home/mm/platform}"
DEV="${DEV:-a133}"
OUT="$HERE/baseline"; mkdir -p "$OUT"
CAPS="python3 $PLATFORM/core/caps.py"

WORK="$(mktemp -d)"; trap 'sudo pkill -f "$WORK/mkuinput" 2>/dev/null || true; rm -rf "$WORK"' EXIT
cd "$WORK"
cp "$HERE/sdl3-gamepad-probe.c" "$HERE/mkuinput.c" .
cp "$PLATFORM/regression/caps/evdev-probe.py" ./evdev-probe.py
# the raw C evdev probe (proven in tsp-an4.1) — re-confirm at the SDL open() layer too.
cp "$QEMU_TSP"/../../regression/probe.c ./evdev-probe.c 2>/dev/null || \
  cp /home/mm/qemu-tsp/regression/probe.c ./evdev-probe.c

echo "== compile SDL3 probes (x86 + static arm64) =="
gcc -O2 -I"$SDLDIR/x86/include" -o probe.x86 sdl3-gamepad-probe.c \
    "$SDLDIR/x86/lib/libSDL3.a" -lm -ldl -lpthread -lrt
aarch64-linux-gnu-gcc -O2 -static -I"$SDLDIR/arm64/include" -o probe.arm64 sdl3-gamepad-probe.c \
    "$SDLDIR/arm64/lib/libSDL3.a" -lm -ldl -lpthread -lrt
echo "== compile evdev probes + mkuinput =="
gcc -O0 -o mkuinput mkuinput.c
gcc -O0 -o evdev.x86 evdev-probe.c
aarch64-linux-gnu-gcc -O0 -static -o evdev.arm64 evdev-probe.c

echo "== ensure uinput + create TRIMUI Player1 (kill any stale instance first) =="
sudo pkill -f mkuinput 2>/dev/null || true
sleep 1
sudo modprobe uinput
sudo "$WORK/mkuinput" >/dev/null 2>&1 &
sleep 1
EV=""
for d in /sys/class/input/event*; do
  if sudo grep -qx "TRIMUI Player1" "$d/device/name" 2>/dev/null; then EV="/dev/input/$(basename "$d")"; break; fi
done
[ -n "$EV" ] || { echo "FAIL: TRIMUI Player1 event node not found"; exit 1; }
echo "   device = $EV"

# descriptor's SDL mapping line (one-descriptor source of truth)
MAP="$($CAPS emit-sdldb --device "$DEV")"
echo "   emit-sdldb $DEV = $MAP"
echo "$MAP" > "$OUT/emit-sdldb.$DEV.txt"

run_sdl() {  # $1=arch(x86|arm64) $2=mode(builtin|descriptor) -> writes $OUT/out.$1.$2.json
  local arch="$1" mode="$2" env=()
  [ "$mode" = descriptor ] && env=(SDL_GAMECONTROLLERCONFIG="$MAP")
  if [ "$arch" = x86 ]; then
    sudo env "${env[@]}" SDL_VIDEODRIVER=dummy ./probe.x86 > "$OUT/out.$arch.$mode.json"
  else
    sudo env "${env[@]}" SDL_VIDEODRIVER=dummy "$QEMU_TSP" ./probe.arm64 > "$OUT/out.$arch.$mode.json"
  fi
}

echo "== SDL3 enumerate: native x86 vs arm64-under-qemu-tsp (builtin + descriptor) =="
run_sdl x86   builtin
run_sdl arm64 builtin
run_sdl x86   descriptor
run_sdl arm64 descriptor

echo "== evdev re-confirm: raw C probe (native vs qemu-tsp) + caps.py probe-diff =="
sudo ./evdev.x86 "$EV"            > "$OUT/evdev.native.txt"
sudo "$QEMU_TSP" ./evdev.arm64 "$EV" > "$OUT/evdev.qemu-tsp.txt"
sudo python3 ./evdev-probe.py "$EV"  > "$OUT/$DEV-evdev-capture.json"
$CAPS probe-diff --device "$DEV" --probe "$OUT/$DEV-evdev-capture.json" | tee "$OUT/probe-diff.$DEV.txt"

echo "== ASSERT (check-spike3.py) =="
python3 "$HERE/check-spike3.py" --out "$OUT" --device "$DEV" --emit "$MAP" \
        --descriptor-guid "$($CAPS emit-sdldb --device "$DEV" | cut -d, -f1)"
echo "DONE"
