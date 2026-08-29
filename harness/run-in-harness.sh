#!/usr/bin/env bash
# run-in-harness.sh — the SHARED sim launcher: run an arm64 binary under qemu-tsp+binfmt
# inside BUBBLEWRAP against the arm64 rootfs (owner decision: NO crun/cgroups/seccomp). The
# bottom seam (/dev/input) is bound from the host so the in-sandbox app sees the VDB's
# uinput device. This is the lightweight ns/chroot stand-in for crun; the app binary is
# identical to what real crun would run, so the launcher swaps to crun later unchanged.
#
#   Usage:  QEMU_TSP=... ROOTFS=... run-in-harness.sh /host/path/to/arm64bin [args...]
# Needs sudo (the uinput event node is root-only). The binary is bound in at /app.
set -euo pipefail
QEMU_TSP="${QEMU_TSP:?set QEMU_TSP (baked in the pocketforge-sim image; see docker/README.md)}"
ROOTFS="${ROOTFS:?set ROOTFS=/path/to/rootfs-arm64}"
BIN="${1:?usage: run-in-harness.sh <arm64-binary> [args...]}"; shift || true

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

# Optional VIRTUAL IIO SENSOR (tsp-an4.7): IIO_BIND=<hostdir> exposes the descriptor-synthesized
# IIO tree at the honest ABI path /sys/bus/iio/devices, so the app reads the injected accel/gyro
# indistinguishably from the real qmi8658 (plain sysfs read(), no ioctl). a133 (no imu) -> the
# host passes an empty dir -> the app's scan finds nothing -> typed hardware-absent.
[ -n "${IIO_BIND:-}" ] && EXTRA+=(--ro-bind "$IIO_BIND" /sys/bus/iio/devices)

exec bwrap \
  --bind "$ROOTFS" / \
  --dev /dev \
  --dev-bind /dev/input /dev/input \
  --ro-bind "$QEMU_TSP" /qemu-tsp \
  --ro-bind "$BIN" /app \
  "${EXTRA[@]}" \
  --proc /proc \
  --tmpfs /tmp \
  --setenv LD_LIBRARY_PATH /usr/local/lib/aarch64-linux-gnu:/usr/local/lib \
  --setenv SDL_VIDEODRIVER dummy \
  --unshare-pid --unshare-ipc --unshare-uts \
  /qemu-tsp /app "$@"
