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
| `./sim run-app <device> <arm64-bin\|bundled:name> [--shot file.ppm\|--window] [--fb0-path file [--fb0-hdr-path file]] [--no-frame] [--no-synth --input-device /dev/input/eventN] [--print-cmd] [-- args…]` | Attach a generic `/dev/fb0`, run any arm64 binary (or one the image ships) under qemu-tsp, and composite its pixels into the skin. |
| `./sim gui [device]` | Open the **clickable `.scad`-rendered skin** — press the bezel, watch the control light. Live X11 window on a display host; autonomous Xvfb self-test when headless. Default `a133`. |
| `./sim check [devices…]` | The **full CI matrix**, exactly as [`sim-gate.yml`](../.github/workflows/sim-gate.yml) runs it (control + sensor + skin). With **no args** the device list **and** per-device posture (blocking / advisory / excluded) are **data-driven from `platform/ci-matrix.toml`** (read via `pf caps matrix` inside the image) — so a device added to that matrix runs here with no CLI change. Naming devices runs just those, hard. Run this before you push. |
| `./sim devices` | List the virtual devices this image can run. |
| `./sim build [base\|demo\|all]` | Build the pinned image(s) without running anything. |
| `./sim shell [demo]` | Interactive debug shell inside the container (caps attached). |
| `./sim doctor` | Check the host is ready (docker, `/dev/uinput`, display) — each ✗ names its fix. |
| `./sim doctor --shell-suite` | Also run deterministic §15.2 modeled shell transcripts and print the F13/F14 honesty report. |
| `./sim version` | Show the pinned platform / qemu-tsp / SDL3 refs. |

`./sim --help` teaches the whole product on one screen.

### Generic framebuffer applications

```bash
./sim run-app a133 ./my-app.arm64 --shot my-app.ppm
./sim run-app a133 ./my-app.arm64 --window -- --app-option value
```

The binary needs no simulator request/response protocol: it opens and mmaps `/dev/fb0`.
The simulator exposes `PF_FB_WIDTH`, `PF_FB_HEIGHT`, and `PF_FB_STRIDE` (bytes); pixels
are XRGB8888. Capture uses a fixed cadence. `--shot` is the offscreen CI form and
`--window` is the live X11 form. Raw and composited artifacts appear in `sim-capture/`.

The image ships its own apps under `/opt/pf/apps`; `bundled:<name>` runs one without a host
binary:

```bash
./sim run-app a133 bundled:fb-anim --window     # redraws /dev/fb0 at 60 Hz until stopped
./sim run-app a133 bundled:fb-pattern --shot pattern.ppm   # one deterministic frame, exits 0
```

| App | What it does |
|-----|--------------|
| `fb-anim` | Redraws `/dev/fb0` at 60 Hz (a sweeping bar and a binary frame counter) until SIGTERM/SIGINT, then exits 0. Publishes the `fb0.hdr` header when given one (`--fb0-hdr-path`). |
| `fb-pattern` | Draws one deterministic frame (the `check-generic` baseline) and exits 0. |
| `hwprobe-lite` | The control/sensor suites' probe; it needs their io-dir and never writes `/dev/fb0`. |

A name the image does not ship fails in the container with the list it does ship.
`run-app` runs the container with `--init`, and the capture supervisor passes SIGTERM/SIGINT on to
the app (past bwrap, which would not), so `docker stop` and Ctrl-C let the app exit cleanly and its
exit status is the run's.

Host-owned screen and input (for a host viewer such as pfvd):

```bash
truncate -s $((1280*720*4)) /run/user/$(id -u)/pfvd/fb0 && chmod o+rw /run/user/$(id -u)/pfvd/fb0
./sim run-app a133 ./my-app.arm64 --fb0-path /run/user/$(id -u)/pfvd/fb0 \
  --no-synth --input-device /dev/input/event7
truncate -s 64 /run/user/$(id -u)/pfvd/fb0.hdr && chmod o+rw /run/user/$(id -u)/pfvd/fb0.hdr
./sim run-app a133 bundled:fb-anim --fb0-path /run/user/$(id -u)/pfvd/fb0 \
  --fb0-hdr-path /run/user/$(id -u)/pfvd/fb0.hdr --no-frame --no-synth --input-device /dev/input/event7
./sim run-app a133 ./my-app.arm64 --fb0-path … --print-cmd   # print the docker run argv, run nothing
```

| Flag | Effect |
|------|--------|
| `--fb0-path <host-file>` | Bind the caller's existing, pre-sized file at `/dev/fb0` (via `/run/pfvd/fb0` and `FB0_BIND`) instead of a private buffer. Never created or truncated; must be `o+rw` (`u+rw` if root-owned): the sandboxed app is uid 0 with no capabilities. Change detection is by polling; see [`FB0-SIDECAR.md`](FB0-SIDECAR.md). |
| `--fb0-hdr-path <host-file>` | With `--fb0-path`: bind a second caller-owned file (at least 64 bytes, same permission rule, not the fb0 file itself) at `/dev/fb0.hdr`, and give the app `PF_FB0_HDR=/dev/fb0.hdr`. A producer that knows when a frame is complete (`fb-anim`) publishes the PFB0 seqlock header there; see [`FB0-SIDECAR.md`](FB0-SIDECAR.md). |
| `--no-frame` | Never convert fb0 into `sim-capture/frame.ppm`. Not with `--shot`/`--window`, which composite it. With `--fb0-path` and no `--window` the capture already skips the 50 ms snapshots and keeps only the frame at a clean exit; `--no-frame` drops that one too. |
| `--no-synth` | Do not give the container `/dev/uinput` or the host `/dev/input` tree. Also drops the `c 13:* rmw` device-cgroup wildcard and adds `--cap-drop MKNOD`, so the container cannot create and open another session's input node. Needs at least one `--input-device`. |
| `--input-device /dev/input/eventN` | Pass exactly this host node with `--device` (repeatable). Only with `--no-synth`. |
| `--print-cmd` | Print the exact `docker run` argument list, shell-quoted on one line, and exit. No docker call, no build, no `sim-capture/`. |

## Requirements (and the errors that name them)

- **docker** — installed and reachable (in your `docker` group, or run under `sudo`). A missing daemon
  or a permission error prints the exact fix.
- **the host `uinput` module** — `sudo modprobe uinput` (the sim synthesizes the device through it).
- **no host root for the app** — the nested sim runs as **root inside the container** (PID-1 root), so
  only docker access is needed. `./sim doctor` reports all of the above up front.

Zero environment variables to set — the image bakes every pinned path (`QEMU_TSP`, `ROOTFS`,
`PLATFORM`, the app binaries, the SDL3 libs). `SIM_IMAGE` / `SIM_DEMO_IMAGE` override the image tags
(CI passes a run-scoped tag); `SIM_NETWORK=1` re-enables network on the otherwise-offline run phase.

## `sim gui` — driving the bezel controls

The live window turns every mouse gesture into the **same descriptor-resolved control-surface call**
the headless suite injects. The gestures:

| Control | Gesture | What it does |
|---------|---------|--------------|
| Buttons / D-pad | **left-click** (press-and-hold to keep it held) | `press`/`release` (D-pad: deflect the clicked arm) |
| Analog stick | **left-drag** the nub | `set_stick` — and the **nub visually follows the drag** within its well and **recenters when you release** |
| Trigger | **left-drag** along the slider | `set_axis` sweep 0→1 |
| **Stick click (L3/R3)** | **middle-click** the nub — or **Ctrl + left-click** it (for a trackpad with no middle button) | `press`/`release` **BTN_THUMBL / BTN_THUMBR**, held while the button is down |
| **Rotate the device** | the **`< VIEW >`** arrows at top-centre, the **Left/Right (or Tab)** keys, or a **left-drag on empty bezel** | switch between the **front** and **top-edge** views |
| Device switch | **left-click** a picker entry | swap the virtual device |

**Stick click is a per-device capability, driven purely by descriptor data — never a device-name
branch in the GUI.** The Pro S (`a523`) descriptor declares `kind = "stick-click"` inputs (`l3`/`r3`)
on the stick parts, so middle-clicking a nub emits L3/R3 and lights it. The base unit (`a133`)
declares **no** stick-click row, so the identical gesture resolves to nothing and the base emits **no**
L3/R3 — the difference is one pair of rows in `capabilities.toml`, zero GUI code. (A bare left-*tap*
on a clickable nub — a press+release with no drag — also fires the stick-click; middle-click / Ctrl+left
is the explicit, discoverable form.)

**Rotating to the top-edge view (tsp-65jc.27).** The skins are pre-baked orthographic renders, so
"rotation" is a **discrete snap** between rendered views, not free 3D. Both devices carry a **`top`**
view rendered from the same `.scad` — a dedicated top-edge shot where the **shoulders/triggers**
(`L1/L2/R1/R2`, plus the Pro S **HOME** button) are prominent and **clickable**, resolving through the
same descriptor to the same control-surface call as the front view (so "GUI click == headless inject"
holds per view). The screen (live fb) shows only on the **front** view — the top view is bezel + its
controls. A device with no extra views simply shows no rotate affordance.

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
