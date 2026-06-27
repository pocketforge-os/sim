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
#   pf-sim window <device>              # interactive --window demo (DEMO image; needs a real $DISPLAY)
#   pf-sim window-selftest [device]     # autonomous: live-window smoke + driver loop (Xvfb; DEMO image)
#   pf-sim shell                        # interactive debug
set -euo pipefail
SIM=/opt/sim
WIN="${SKIN_RENDER_WINDOW:-/opt/pf/apps/skin-render-window}"
cmd="${1:-check-control}"; shift || true

case "$cmd" in
  check-control) exec python3 "$SIM/control/check-control.py" "$@" ;;
  check-sensor)  exec python3 "$SIM/sensor/check-sensor.py"  "$@" ;;
  check-skin)    exec python3 "$SIM/skin/check-skin.py"      "$@" ;;
  window)
    # the interactive live demo (DEMO image) — needs a real X display forwarded in
    : "${DISPLAY:?'pf-sim window' needs a real X display (-e DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix); for a headless check use 'window-selftest'}"
    exec env SKIN_RENDER="$WIN" python3 "$SIM/skin/window_driver.py" "${1:-a523}" "${@:2}" ;;
  window-selftest)
    # autonomous proof (DEMO image): start Xvfb MANUALLY (xvfb-run hangs in some container setups),
    # then run window_driver --self-test, which smokes the live X11 window AND drives the
    # click->light loop ("the loop runs in the container"). Needs xvfb + skin-render-window.
    dev="${1:-a523}"
    Xvfb :99 -screen 0 1480x640x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
    xvfb_pid=$!
    export DISPLAY=:99
    for _ in $(seq 1 25); do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.2; done
    env SKIN_RENDER="$WIN" python3 "$SIM/skin/window_driver.py" "$dev" --self-test
    rc=$?
    kill "$xvfb_pid" 2>/dev/null || true
    exit "$rc" ;;
  shell)         exec /bin/bash "$@" ;;
  *)
    echo "pf-sim: unknown command '$cmd'" >&2
    echo "usage: pf-sim {check-control|check-sensor|check-skin|window|window-selftest|shell} [devices...]" >&2
    exit 2 ;;
esac
