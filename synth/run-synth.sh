#!/usr/bin/env bash
# run-synth.sh — tsp-an4.3 end-to-end proof on an x86_64 host with /dev/uinput.
#
# Proves a SINGLE descriptor-driven synth path (uinput_synth.plan) creates uinput device(s)
# that, read back through the kernel, ARE the descriptor — exactly, zero per-device code —
# for BOTH a133 and a523, and that the device stays indistinguishable from hardware to SDL3
# under qemu-tsp. The a133-vs-a523 delta is 100% descriptor rows (the multi-device proof).
#
# For each device:
#   * uinput_synth.py create --keepalive   -> the virtual node(s) from capabilities.toml
#   * probe_evdev.py (native)              -> a capture; caps.py probe-diff asserts subset
#   * check-synth.py                       -> ROUND-TRIP EXACT + omission/matrix
#   * raw C evdev probe native vs qemu-tsp -> byte-identical (pass-through holds)
#   * SDL3 gamepad enum native vs qemu-tsp -> byte-identical; gamepad; GUID == descriptor
#
# Needs sudo (uinput + event nodes are root-only). Env:
#   QEMU_TSP   path to qemu-tsp/build/qemu-tsp/qemu-aarch64        (required)
#   SDLDIR     SDL3 build dir holding x86/ and arm64/ installs     (required)
#   PLATFORM   platform repo checkout (core/caps.py + descriptors) (required)
#   DEVICES    space-separated device ids                          (default "a133 a523")
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
QEMU_TSP="${QEMU_TSP:?set QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64}"
SDLDIR="${SDLDIR:?set SDLDIR=/home/mm/sim-build/sdl3}"
PLATFORM="${PLATFORM:?set PLATFORM=/home/mm/platform}"
DEVICES="${DEVICES:-a133 a523}"
SPIKE3="$HERE/../spike3"
CAPS="python3 $PLATFORM/core/caps.py"
OUT="$HERE/baseline"; mkdir -p "$OUT"

WORK="$(mktemp -d)"
cleanup(){ sudo pkill -f "uinput_synth.py create" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT
cd "$WORK"

# raw C evdev probe (proven in tsp-an4.1) + the SDL3 gamepad probe (tsp-an4.2) — reused as-is.
cp "$SPIKE3/sdl3-gamepad-probe.c" .
cp "$QEMU_TSP"/../../regression/probe.c ./evdev-probe.c 2>/dev/null || cp /home/mm/qemu-tsp/regression/probe.c ./evdev-probe.c

echo "== regenerate-check the evdev code table vs the kernel ABI + caps.py vocab =="
python3 "$HERE/gen_evdev_codes.py" --platform "$PLATFORM" --check

echo "== compile probes (SDL3 x86 + static arm64; raw evdev x86 + static arm64) =="
gcc -O2 -I"$SDLDIR/x86/include" -o probe.x86 sdl3-gamepad-probe.c "$SDLDIR/x86/lib/libSDL3.a" -lm -ldl -lpthread -lrt
aarch64-linux-gnu-gcc -O2 -static -I"$SDLDIR/arm64/include" -o probe.arm64 sdl3-gamepad-probe.c "$SDLDIR/arm64/lib/libSDL3.a" -lm -ldl -lpthread -lrt
gcc -O0 -o evdev.x86 evdev-probe.c
aarch64-linux-gnu-gcc -O0 -static -o evdev.arm64 evdev-probe.c

sudo modprobe uinput 2>/dev/null || true

run_sdl(){  # $1=arch $2=mode $3=outdir
  local arch="$1" mode="$2" od="$3" env=()
  [ "$mode" = descriptor ] && env=(SDL_GAMECONTROLLERCONFIG="$MAP")
  if [ "$arch" = x86 ]; then
    sudo env "${env[@]}" SDL_VIDEODRIVER=dummy ./probe.x86 > "$od/out.$arch.$mode.json"
  else
    sudo env "${env[@]}" SDL_VIDEODRIVER=dummy "$QEMU_TSP" ./probe.arm64 > "$od/out.$arch.$mode.json"
  fi
}

OVERALL=0
for DEV in $DEVICES; do
  echo ""
  echo "############## $DEV ##############"
  OD="$OUT/$DEV"; mkdir -p "$OD"
  sudo pkill -f "uinput_synth.py create" 2>/dev/null || true; sleep 1

  echo "== synth uinput device(s) FROM descriptor (keepalive) =="
  sudo PYTHONPATH="$HERE" python3 "$HERE/uinput_synth.py" create --device "$DEV" \
       --platform "$PLATFORM" --keepalive > "$OD/nodes.json" 2> "$OD/synth.err" &
  for _ in $(seq 1 100); do grep -q '"nodes"' "$OD/nodes.json" 2>/dev/null && break; sleep 0.1; done
  grep -q '"nodes"' "$OD/nodes.json" || { echo "FAIL: synth produced no nodes"; cat "$OD/synth.err"; OVERALL=1; continue; }
  PADNODE=$(python3 -c "import json;d=json.load(open('$OD/nodes.json'));print(next(n['node'] for n in d['nodes'] if n['role']=='pad'))")
  ALLNODES=$(python3 -c "import json;d=json.load(open('$OD/nodes.json'));print(' '.join(n['node'] for n in d['nodes']))")
  echo "   nodes: $(cat "$OD/nodes.json")"

  echo "== capture (sim-owned probe) + caps.py probe-diff =="
  sudo python3 "$HERE/probe_evdev.py" $ALLNODES > "$OD/capture.json"
  $CAPS probe-diff --device "$DEV" --probe "$OD/capture.json" | tee "$OD/probe-diff.txt"

  echo "== raw C evdev probe: native vs qemu-tsp (pad node) =="
  sudo ./evdev.x86 "$PADNODE"            > "$OD/evdev.native.txt"
  sudo "$QEMU_TSP" ./evdev.arm64 "$PADNODE" > "$OD/evdev.qemu-tsp.txt"

  echo "== emit-sdldb (one-descriptor SDL mapping) + SDL3 enum native vs qemu-tsp =="
  MAP="$($CAPS emit-sdldb --device "$DEV")"; echo "$MAP" > "$OD/emit-sdldb.txt"
  echo "   $MAP"
  run_sdl x86   builtin    "$OD"
  run_sdl arm64 builtin    "$OD"
  run_sdl x86   descriptor "$OD"
  run_sdl arm64 descriptor "$OD"

  echo "== ASSERT (check-synth.py) =="
  if python3 "$HERE/check-synth.py" --device "$DEV" --platform "$PLATFORM" \
       --capture "$OD/capture.json" --probe-diff "$OD/probe-diff.txt" --out "$OD" \
       --descriptor-guid "$(echo "$MAP" | cut -d, -f1)"; then :; else OVERALL=1; fi

  sudo pkill -f "uinput_synth.py create" 2>/dev/null || true; sleep 1
done

echo ""
[ "$OVERALL" = 0 ] && echo "ALL DEVICES PASS ($DEVICES)" || echo "SOME DEVICE FAILED"
exit $OVERALL
