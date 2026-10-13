#!/usr/bin/env bash
# run-in-harness.sh — the SHARED sim launcher: run an arm64 binary under qemu-tsp+binfmt
# inside BUBBLEWRAP against the arm64 rootfs (owner decision: NO crun/cgroups/seccomp). The
# bottom seam (/dev/input) is a session-private input-event directory (or an empty directory for
# a generic app with no input). This is the lightweight ns/chroot stand-in for crun; the app is
# identical to what real crun would run, so the launcher swaps to crun later unchanged.
#
#   Usage:  QEMU_TSP=... ROOTFS=... run-in-harness.sh /host/path/to/arm64bin [args...]
# The binary is bound in at /app.
set -euo pipefail
QEMU_TSP="${QEMU_TSP:?set QEMU_TSP (baked in the pocketforge-sim image; see docker/README.md)}"
ROOTFS="${ROOTFS:?set ROOTFS=/path/to/rootfs-arm64}"
BIN="${1:?usage: run-in-harness.sh <arm64-binary> [args...]}"; shift || true

die() { printf '%s\n' "sim: error: $*" >&2; exit 1; }

# --sim-frame opens a framebuffer directly. Catch a missing attachment here, before the launcher
# can reduce the problem to a bare open(2) error. FB0_BIND is the qemu harness attachment. Neither
# a host /dev/fb0 nor an arbitrary parent-side PF_FB0 path is inherited through bwrap's private
# --dev/rootfs, so the simulator must explicitly attach the framebuffer at /dev/fb0.
sim_frame=0
for arg in "$@"; do [ "$arg" = "--sim-frame" ] && sim_frame=1; done
if [ "$sim_frame" = "1" ] && [ -z "${FB0_BIND:-}" ]; then
  die "--sim-frame has no framebuffer attached at /dev/fb0.
    Fix: run through './sim run-app ... -- --sim-frame', or set FB0_BIND to a writable framebuffer file."
fi

# Device sessions explicitly supply their private input directory. Generic apps with no input get
# an empty /dev/input. An explicitly configured missing directory remains an actionable error.
INPUT_BIND=()
if [ -n "${PF_INPUT_DIR:-}" ]; then
  [ -d "$PF_INPUT_DIR" ] || die "$PF_INPUT_DIR is absent in this container: PF_INPUT_DIR must name this session's input directory."
  INPUT_BIND=(--dev-bind "$PF_INPUT_DIR" /dev/input)
else
  INPUT_BIND=(--dir /dev/input)
fi

# Optional WRITABLE artifact egress: OUT_BIND=<hostdir> exposes it at /out so the headless
# app can write a framebuffer dump (tsp-an4.4) the host then reads. Used by .5/E7 too.
EXTRA=()
[ -n "${OUT_BIND:-}" ] && EXTRA+=(--bind "$OUT_BIND" /out)

# Optional generic virtual framebuffer.  The supervisor gives its pre-sized memfd a
# bindable /proc/<supervisor>/fd path so bwrap can attach it where an app opens it.
# The host supervisor may read it while the app is running; no app-specific request FIFO is
# involved.  The existing hwprobe-lite OUT_BIND protocol remains entirely unchanged.
if [ -n "${FB0_BIND:-}" ]; then
  EXTRA+=(--bind "$FB0_BIND" /dev/fb0)
  EXTRA+=(--setenv PF_FB_WIDTH "${PF_FB_WIDTH:?set PF_FB_WIDTH with FB0_BIND}")
  EXTRA+=(--setenv PF_FB_HEIGHT "${PF_FB_HEIGHT:?set PF_FB_HEIGHT with FB0_BIND}")
  EXTRA+=(--setenv PF_FB_STRIDE "${PF_FB_STRIDE:-$((PF_FB_WIDTH * 4))}")
fi
# Optional fb0.hdr sidecar (docs/FB0-SIDECAR.md): a caller-owned 64-byte file beside fb0, where a
# producer that knows when a frame is complete publishes the PFB0 seqlock header.
if [ -n "${FB0_HDR_BIND:-}" ]; then
  [ -n "${FB0_BIND:-}" ] || die "FB0_HDR_BIND needs FB0_BIND (the header describes the attached framebuffer)"
  EXTRA+=(--bind "$FB0_HDR_BIND" /dev/fb0.hdr --setenv PF_FB0_HDR /dev/fb0.hdr)
fi

# Optional VIRTUAL IIO SENSOR (tsp-an4.7): IIO_BIND=<hostdir> exposes the descriptor-synthesized
# IIO tree at the honest ABI path /sys/bus/iio/devices, so the app reads the injected accel/gyro
# indistinguishably from the real qmi8658 (plain sysfs read(), no ioctl). a133 (no imu) -> the
# host passes an empty dir -> the app's scan finds nothing -> typed hardware-absent.
[ -n "${IIO_BIND:-}" ] && EXTRA+=(--ro-bind "$IIO_BIND" /sys/bus/iio/devices)

exec bwrap \
  --bind "$ROOTFS" / \
  --dev /dev \
  "${INPUT_BIND[@]}" \
  --ro-bind "$QEMU_TSP" /qemu-tsp \
  --ro-bind "$BIN" /app \
  "${EXTRA[@]}" \
  --proc /proc \
  --tmpfs /tmp \
  --setenv LD_LIBRARY_PATH /usr/local/lib/aarch64-linux-gnu:/usr/local/lib \
  --setenv SDL_VIDEODRIVER dummy \
  --unshare-pid --unshare-ipc --unshare-uts \
  /qemu-tsp /app "$@"
