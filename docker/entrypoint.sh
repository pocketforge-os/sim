#!/usr/bin/env bash
# pf-sim — the ONE container entrypoint. Runs the E5 headless suite identically on any host from
# the baked, pinned artifacts (QEMU_TSP / ROOTFS / PLATFORM / APP_X86 / APP_ARM64 / SKIN_RENDER /
# SDLR / SDLDIR are set in the image ENV — no /home/mm, no host toolchain). We are PID-1 root in
# the container, so the uinput-create + bwrap path needs no sudo. See docker/README.md for the
# required `docker run` caps (the .2 nesting verdict).
#
#   pf-sim check-control [devices...]   # default: a133 a523
#   pf-sim check-sensor  [devices...]
#   pf-sim check-skin    [devices...]
#   pf-sim shell                        # interactive debug
set -euo pipefail
SIM=/opt/sim
cmd="${1:-check-control}"; shift || true

case "$cmd" in
  check-control) exec python3 "$SIM/control/check-control.py" "$@" ;;
  check-sensor)  exec python3 "$SIM/sensor/check-sensor.py"  "$@" ;;
  check-skin)    exec python3 "$SIM/skin/check-skin.py"      "$@" ;;
  shell)         exec /bin/bash "$@" ;;
  *)
    echo "pf-sim: unknown command '$cmd'" >&2
    echo "usage: pf-sim {check-control|check-sensor|check-skin|shell} [devices...]" >&2
    exit 2 ;;
esac
