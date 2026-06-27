# `pocketforge-sim` — containerized E5 simulator (build + run, any host)

One reproducible image carrying the whole E5 sim toolchain — **`qemu-tsp` + both SDL3 variants + an
arm64 bookworm rootfs + the compiled apps + the platform descriptors** — built from PINNED refs, with
**zero `/home/mm` hand-staging** and **zero host-toolchain assumptions**. See [`../Dockerfile`](../Dockerfile).

> **Layering (unchanged from E5):** the app still runs under `qemu-tsp` + `bubblewrap`, **NO crun**.
> This image is the reproducible *outer* tooling; the bwrap sim runs **nested** inside it. It is a
> **distinct artifact** from the device OS image (`tsp-1dl.4`) — x86 dev/CI tooling, not the appliance image.
> The HONESTY CONTRACT is unchanged: the sim proves the **logical layer only**.

## Build (modelmaker / any Docker host)

```bash
docker build -t pocketforge-sim .
```

Pinned inputs (every external ref, all cloned/pulled from clean — no out-of-band input):
`debian:bookworm@sha256:30482e87…` (multi-arch index — amd64 build+runtime, arm64 rootfs), the
`qemu-tsp` fork commit (which pins upstream qemu v8.2.2), SDL3 `release-3.4.10` (`sim/sdl3/SDL3.pin`),
and the `platform` repo cloned directly at the pinned commit (`docker/platform.pin`; platform is
**public** as of tsp-qc1.4 — this closed the former private-archive gap). **Residual
reproducible-from-clean gap (named):** apt installs from the live bookworm suite, not a
`snapshot.debian.org` timestamp — a rebuild months later may pull newer point-release packages.
Hardening follow-up: pin apt to a snapshot mirror.

## Run — the nested-container caps (the `tsp-qc1.2` spike verdict)

The sim nests cleanly. The launcher inside the container must (1) create the bwrap sandbox namespaces,
(2) do its `mount`/`pivot_root` dance, (3) open `/dev/uinput` to synthesize the device, and (4) read the
synthesized **event node** — which the kernel creates on the **host's** devtmpfs, so the container has to
see it. Empirically (modelmaker, Docker 29.1.3) the **minimal verified** run — meaningfully lighter than
`--privileged` — is:

```bash
docker run --rm \
  --device /dev/uinput \
  --device-cgroup-rule "c 13:* rmw" \
  -v /dev/input:/dev/input \
  --cap-add SYS_ADMIN \
  --security-opt apparmor=unconfined \
  --security-opt seccomp=unconfined \
  pocketforge-sim check-control a133 a523
```

(`--privileged` also works as the superset — it mounts a full devtmpfs so the event node appears natively
and disables the device cgroup.) Why each knob:

| Flag | Gate it opens |
|------|---------------|
| `--device /dev/uinput` | exposes the uinput control node (device create) |
| `-v /dev/input:/dev/input` | the synthesized `eventN` lands on the **host** devtmpfs; the bind makes it visible (the app reads it directly on the native path, and bwrap `--dev-bind`s it on the qemu path) |
| `--device-cgroup-rule "c 13:* rmw"` | Docker's default device cgroup denies major-13 (input) char devices; this allows the dynamically-created event nodes |
| `--cap-add SYS_ADMIN` | Docker's default **seccomp** gates namespace creation on `CAP_SYS_ADMIN` |
| `--security-opt apparmor=unconfined` | the default **AppArmor** profile denies bwrap's `mount`/`pivot_root` |
| `--security-opt seccomp=unconfined` | belt-and-braces for `pivot_root`/mount syscalls under the bwrap dance |

The host's `uinput` kernel module must be loaded (`modprobe uinput`). Lowering the host
`kernel.apparmor_restrict_unprivileged_userns` sysctl does **not** help the nested-Docker case (that knob
governs bwrap on the host, not inside a container) — no persistent host change is needed. Binding
`/dev/input` exposes the host's real input devices to the container — acceptable on a dedicated sim/CI host;
note it as a caveat. Rootless/podman is an untested follow-up caveat.

## Commands

```bash
pf-sim check-control [devices...]   # default: a133 a523 — the CI-gate suite
pf-sim check-sensor  [devices...]
pf-sim check-skin    [devices...]
pf-sim shell                        # interactive debug
```

Acceptance bar (carried from E5): **ALL DEVICES PASS**, **native x86 == arm64-under-qemu-tsp
BYTE-IDENTICAL** — now from a clean image with no hand-staged artifacts, on any host.

## CI gate (tsp-qc1.4 — wires E7/infra-106)

[`.github/workflows/sim-gate.yml`](../.github/workflows/sim-gate.yml) builds this image and runs
`check-control` + `check-sensor` (a133 a523) nested, on every PR to `main`. It runs on the
**self-hosted Dell device-lab runner** (org runner `trimui-build-lab`; labels `self-hosted`+`docker`)
because the nested sim needs `/dev/uinput` + the scoped cap set above. No ghcr push (build-then-run).

It is **ADVISORY** first — not a required status check, so a red run does not block merge. **To flip
to BLOCKING** once a133+a523 stay green + stable: add the `headless-suite` job to `main`'s
branch-protection required checks (Settings → Branches, or
`gh api -X PUT repos/pocketforge-os/sim/branches/main/protection ...`).

## Interactive `--window` demo (tsp-qc1.5 — the dogfood image)

A laptop-runnable image where you press the live bezel and watch the app light up. The
[`docker/` `demo` stage](../Dockerfile) adds a **video-capable `skin-render-window`** (X11 + software
renderer; `skin/build-sdl3-window.sh`) + the X11 client libs + Xvfb to the lean base image. The
[`window_driver.py`](../skin/window_driver.py) loop bridges `skin-render --window`'s mouse-event
stream to the SAME `control_surface.Device` the headless test injects through — so a live press
resolves through the descriptor (`skin_model.tap` / `.drag` → `Action`) exactly as the headless inject
does ("GUI click == headless inject", live). A picker click switches device.

**Interaction model (tsp-qc1.6).** The renderer emits a press/release-aware protocol
(`down`/`motion`/`up`, plus `pick` on a panel click), so:

- **Hold to light.** A control is lit only *while the mouse button is held* — release and it goes
  dark (not "stays lit until the next click").
- **Press vs drag on a stick.** A quick tap on a thumbstick is the **L3/R3 stick-click** (a523 only;
  a133's sticks carry no stick-click row, so a tap is an honest no-op). Hold-and-drag **moves the
  stick** (`set_stick`) — the deflection shows live in the composited app framebuffer.
- **Triggers** slide to the clicked fraction and follow a drag (the analog `set_axis` slider).
- **Chording.** The driver no longer force-deactivates the previously-pressed control, so
  `control_surface` can hold several inputs at once (the headless suite + `window-selftest` exercise
  a two-button chord). A **single physical mouse can only issue one `down` at a time**, so the live
  GUI shows one held control — that is a limit of the input device, not of the model (a scripted or
  multi-touch front-end could drive a true chord).

```bash
docker build --target demo -t pocketforge-sim:demo .          # base + video SDL3 + Xvfb

# Autonomous proof (no display needed — Xvfb): live X11 window opens + the driver loop lights controls
docker run --rm --device /dev/uinput --device-cgroup-rule "c 13:* rmw" -v /dev/input:/dev/input \
  --cap-add SYS_ADMIN --security-opt apparmor=unconfined --security-opt seccomp=unconfined \
  pocketforge-sim:demo window-selftest a523

# Live demo on a host with a real X display (e.g. the laptop) — click the bezel, watch it light:
docker run --rm --device /dev/uinput --device-cgroup-rule "c 13:* rmw" -v /dev/input:/dev/input \
  --cap-add SYS_ADMIN --security-opt apparmor=unconfined --security-opt seccomp=unconfined \
  -e DISPLAY="$DISPLAY" -v /tmp/.X11-unix:/tmp/.X11-unix \
  pocketforge-sim:demo window a523
```

HONESTY: the live window is upstream SDL3's portable X11 + software rasterizer on the **dev host**, NOT
the on-device sunxifb/PowerVR path. Acceptance is "the loop runs in the container" (the `window-selftest`
above), **not** an on-panel visual gate — the bezel-click demo is a developer convenience.
