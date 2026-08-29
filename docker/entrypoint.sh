#!/usr/bin/env bash
# pf-sim — the ONE container entrypoint. Runs the E5 headless suite identically on any host from
# the baked, pinned artifacts (QEMU_TSP / ROOTFS / PLATFORM / APP_X86 / APP_ARM64 / SKIN_RENDER /
# SDLR / SDLDIR are set in the image ENV — no host-staging, no host toolchain). We are PID-1 root in
# the container, so the uinput-create + bwrap path needs no sudo. See docker/README.md for the
# required `docker run` caps (the .2 nesting verdict).
#
#   pf-sim check-control [devices...]   # bare default per check-control.py; CI passes matrix ids
#   pf-sim check-sensor  [devices...]
#   pf-sim check-skin    [devices...]
#   pf-sim check-broker-stub            # broker_stub presence/policy unit-test (tsp-9sx.6; no qemu)
#   pf-sim selftest-region-guard        # skin_part-gate negative control (tsp-3x7d; no qemu)
#   pf-sim selftest-skin-optional       # optional-descriptor-field negative control (tsp-bu5e; no qemu)
#   pf-sim check-synth-selftest         # negative control for check-synth.py section B (tsp-477r;
#                                       # hermetic — no descriptor, no /dev/uinput, no qemu)
#   pf-sim matrix <list|validate ...>   # the data-driven CI gate matrix (infra-113 B4): derive
#                                       # device rows + posture from the BAKED platform's
#                                       # ci-matrix.toml — the single source CI consumes so the
#                                       # device list is never hardcoded (`pf caps matrix`).
#   pf-sim window <device>              # interactive --window demo (DEMO image; needs a real $DISPLAY)
#   pf-sim window-selftest [device]     # autonomous: live-window smoke + driver loop (Xvfb; DEMO image)
#   pf-sim shell                        # interactive debug
set -euo pipefail
SIM=/opt/sim
PLATFORM="${PLATFORM:-/opt/pf/platform}"
WIN="${SKIN_RENDER_WINDOW:-/opt/pf/apps/skin-render-window}"
cmd="${1:-check-control}"; shift || true

case "$cmd" in
  check-control) exec python3 "$SIM/control/check-control.py" "$@" ;;
  check-sensor)  exec python3 "$SIM/sensor/check-sensor.py"  "$@" ;;
  check-skin)    exec python3 "$SIM/skin/check-skin.py"      "$@" ;;
  check-generic) exec python3 "$SIM/generic/check-generic.py" "$@" ;;
  check-shell) exec python3 "$SIM/shell/check-shell.py" --authority "$SESSION_AUTHORITY_ARM64" \
    --launcher "$SHELL_ARM64" --qemu-tsp "$QEMU_TSP" --rootfs "$ROOTFS" \
    --harness "$SIM/harness/run-in-harness.sh" --platform "$PLATFORM" \
    --skin-render "$SKIN_RENDER" "$@" ;;
  run-app)
    dev="${1:?usage: pf-sim run-app <device> <arm64-binary> [--shot path] [--window] [-- app-args]}"; app="${2:?missing arm64 binary}"; shift 2
    exec python3 "$SIM/generic/generic_capture.py" --device "$dev" --platform "$PLATFORM" \
      --app "$app" --launcher qemu --qemu-tsp "$QEMU_TSP" --rootfs "$ROOTFS" \
      --harness "$SIM/harness/run-in-harness.sh" --skin-render "$SKIN_RENDER" \
      --frame /out/frame.ppm "$@" ;;
  check-broker-stub) exec python3 "$SIM/control/check-broker-stub.py" "$@" ;;
  # The skin_part-gate negative control (tsp-3x7d; no qemu, no uinput). It ALSO runs as a
  # pre-pass inside check-control, so the gate covers it with no workflow edit — this verb is
  # for running it alone while iterating on the predicate.
  selftest-region-guard) exec python3 "$SIM/control/selftest_region_guard.py" "$@" ;;
  # The optional-descriptor-field negative control (tsp-bu5e; no qemu, no uinput). ./sim runs it
  # as a device-free PRE-PASS before the per-device suites, so the gate covers it with no workflow
  # edit — this verb is for running it alone while iterating on the absent-vs-bogus predicate.
  selftest-skin-optional) exec env PLATFORM="$PLATFORM" python3 "$SIM/skin/selftest_skin_model_optional.py" "$@" ;;
  # The negative control for check-synth.py's descriptor-derived node-topology assertions
  # (tsp-477r). check-synth.py itself needs /dev/uinput + qemu + SDL (run-synth.sh, not this
  # suite), so unlike the two verbs above this one is NOT yet wired into any gate pre-pass —
  # tsp-3x7d-coord ruled that gating the negative control of a checker that never runs is a
  # guard for a guard that is not there. Bead tsp-khld owns that posture decision: which of
  # sim's checkers belong in the blocking gate, each wired TOGETHER WITH its negative control.
  check-synth-selftest) exec python3 "$SIM/synth/selftest-check-synth.py" "$@" ;;
  matrix)
    # The data-driven CI gate matrix, read from the BAKED platform descriptors (infra-113 B4 /
    # D6). The device list + per-device posture come from platform ci-matrix.toml — CI derives
    # its rows from HERE instead of a hardcoded device list, so adding a device = adding data.
    exec python3 "$PLATFORM/core/caps.py" matrix "$@" ;;
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
    echo "usage: pf-sim {check-control|check-sensor|check-skin|check-generic|check-shell|run-app|check-broker-stub|selftest-region-guard|selftest-skin-optional|check-synth-selftest|matrix|window|window-selftest|shell} [args...]" >&2
    exit 2 ;;
esac
