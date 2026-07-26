# `./sim` — the one-command developer CLI (infra-113 C1)

The front door to the simulator. It wraps the **pinned, reproducible `pocketforge-sim` container**
(built from committed refs — see [`../Dockerfile`](../Dockerfile) and [`../docker/README.md`](../docker/README.md))
so a developer runs the identical arm64 device app off-hardware with **zero hand-set env vars and zero
host toolchain**. It is a thin, honest wrapper: every command below invokes the same `pf-sim …`
container entrypoint the CI gate runs, with the exact caps the nested bwrap+qemu-tsp sim needs.

> Read [`HONESTY-CONTRACT.md`](HONESTY-CONTRACT.md) first. The sim proves the **logical layer only**
> (descriptor correctness, input mapping, capability/permission semantics, graceful degradation).
> GPU blobs, WiFi, timing, enforcement, and per-SoC graphics stay the hardware gate's sole authority.

## Commands

| Command | What it does |
|---------|--------------|
| `./sim run <device>` | Boot one virtual device and run its **headless logical suite** (control + sensor + skin). The dev inner-loop. |
| `./sim gui [device]` | Open the **clickable `.scad`-rendered skin** — press the bezel, watch the control light. Live X11 window on a display host; autonomous Xvfb self-test when headless. Default `a133`. |
| `./sim check [devices…]` | The **full CI matrix**, exactly as [`sim-gate.yml`](../.github/workflows/sim-gate.yml) runs it (control + sensor + skin over `a133 a523`). Run this before you push. |
| `./sim build [base\|demo\|all]` | Build the pinned image(s) without running anything. |
| `./sim shell [demo]` | Interactive debug shell inside the container (caps attached). |
| `./sim doctor` | Check the host is ready (docker, `/dev/uinput`, display) — each ✗ names its fix. |
| `./sim version` | Show the pinned platform / qemu-tsp / SDL3 refs. |

`./sim --help` teaches the whole product on one screen.

## Requirements (and the errors that name them)

- **docker** — installed and reachable (in your `docker` group, or run under `sudo`). A missing daemon
  or a permission error prints the exact fix.
- **the host `uinput` module** — `sudo modprobe uinput` (the sim synthesizes the device through it).
- **no host root for the app** — the nested sim runs as **root inside the container** (PID-1 root), so
  only docker access is needed. `./sim doctor` reports all of the above up front.

Zero environment variables to set — the image bakes every pinned path (`QEMU_TSP`, `ROOTFS`,
`PLATFORM`, the app binaries, the SDL3 libs). `SIM_IMAGE` / `SIM_DEMO_IMAGE` override the image tags
(CI passes a run-scoped tag); `SIM_NETWORK=1` re-enables network on the otherwise-offline run phase.

## `sim gui` — the display-path tradeoff (the deliberate decision)

`sim gui` needs to put a **real window** on your screen from **inside the container**. Two shapes were
possible:

1. **X11 unix-socket passthrough — CHOSEN.** The container's `skin-render-window` (upstream SDL3, X11 +
   software renderer, built in the `demo` image stage) draws to the **host's X server** over the bound
   `/tmp/.X11-unix` socket, with `$DISPLAY` and the host's xauth cookie forwarded in (read-only). No
   graphics leave the container except X protocol on a unix socket; nothing is built on the host.
   - **Wayland hosts** run their compositor's **XWayland** ($DISPLAY is set), so the same X-socket
     passthrough works unchanged — no Wayland-native path is needed.
   - The container app is root, so `sim gui` scopes local X access to root (`xhost +local:root`) when
     `xhost` is present; the xauth cookie covers cookie-authenticated servers without touching `xhost`.
   - When there is **no reachable display** (a headless build host like modelmaker), `sim gui` runs the
     **autonomous Xvfb self-test** instead (`window-selftest`) — a real X11 window under a virtual
     framebuffer plus the click→light driver loop — so the command is always useful, never a dead end.

2. **A native-host GUI build — REJECTED.** Building `skin-render-window` + the video SDL3 on the host
   would drag the whole X11/SDL toolchain back onto the developer's machine — **exactly the host
   toolchain assumption the container exists to kill** (the point of `tsp-qc1.1`). It would also fork
   the render path (host binary vs container binary) and break "one pinned image, any host."

**Honesty (unchanged).** The live window is upstream SDL3's portable X11 + software rasterizer on the
**dev host**, **not** the on-device sunxifb/PowerVR path. Acceptance for `gui` is "the clickable loop
runs and a press resolves through the descriptor to the lit control" — a developer convenience proving
the **logical** input→action→light binding, **not** an on-panel visual gate (that stays the
flash→serial→webcam hardware gate's authority).

## Relationship to the CI gate

`./sim check` runs the **same three suite entrypoints** (`check-control`, `check-sensor`, `check-skin`)
over the same devices, with the **same container caps** and the same `--network none` run posture, that
[`sim-gate.yml`](../.github/workflows/sim-gate.yml) runs on the self-hosted device-lab runner. "Green
locally with `./sim check`" is the same decision the gate makes on your PR.
