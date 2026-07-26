# pocketforge-os/sim — the PocketForge virtual device simulator

Run the **identical arm64 app binary the real device runs**, off-hardware, on any Docker host —
booted against a **Virtual Device Backend** synthesized purely from the
[`platform`](https://github.com/pocketforge-os/platform) device descriptor
(`devices/<id>/capabilities.toml`). One descriptor, three consumers (broker / simulator / CI).
Think **Android's AVD, for the PocketForge fleet** — but as a *hard CI gate* that runs on every PR.

> **Read [`docs/HONESTY-CONTRACT.md`](docs/HONESTY-CONTRACT.md) first.** The sim proves the
> **logical layer only** — descriptor correctness, input mapping, capability/permission semantics,
> and graceful degradation. GPU blobs, WiFi, timing, enforcement, and per-SoC graphics stay the
> **flash → serial → webcam hardware gate's sole authority**. The two gates are complementary,
> never substitutes. This honesty is restated at every entry point on purpose.

## Quickstart — one command, zero env vars

You need **docker** (in your group, or `sudo`) and the host **`uinput`** module
(`sudo modprobe uinput`). Nothing else — no host toolchain, no paths to set. Then, from a fresh
clone:

```bash
./sim doctor         # is my host ready?  (docker, /dev/uinput, display — each ✗ names its fix)
./sim run a133       # boot one virtual device, run its headless logical suite
./sim gui a133       # the clickable .scad-rendered skin — press the bezel, watch it light
./sim check          # the full CI matrix (a133 + a523), EXACTLY as the gate runs it
./sim --help         # the whole product on one screen
```

That is the entire onboarding. `./sim` wraps the **pinned, reproducible `pocketforge-sim`
container** (built from committed refs — see [`Dockerfile`](Dockerfile) and
[`docker/README.md`](docker/README.md)), so the image bakes every pinned path (`QEMU_TSP`,
`ROOTFS`, `PLATFORM`, the app binaries, the SDL3 libs). The nested sim runs as **root *inside* the
container**, so no host root is needed for the app. First run builds the image (a fresh checkout
re-COPYs the sources, so expect a one-time multi-minute build; cached layers make repeats fast);
`./sim doctor` reports anything missing up front, each ✗ with its fix.

> **Run `./sim gui` from an interactive terminal.** On a host with a display it opens a **live**
> X11 window (`docker run -it`), which needs a TTY on stdin — so run it directly in your terminal,
> not piped through another command. Headless hosts (no `$DISPLAY`) instead run the autonomous
> Xvfb self-test, which needs no TTY. `./sim run` / `./sim check` are fully non-interactive.

Full command reference: [`docs/CLI.md`](docs/CLI.md). New to the project? Start with the
[`docs/` index](docs/README.md).

### The commands

| Command | What it does |
|---------|--------------|
| `./sim doctor` | Check the host is ready (docker, `/dev/uinput`, display) — each ✗ names its fix. |
| `./sim run <device>` | Boot one virtual device, run its **headless logical suite** (control + sensor + skin). The dev inner-loop. |
| `./sim gui [device]` | Open the **clickable `.scad`-rendered skin** — press the bezel, watch the control light. Live X11 window on a display host; autonomous Xvfb self-test when headless. Default `a133`. |
| `./sim check [devices…]` | The **full CI matrix**, exactly as [`sim-gate.yml`](.github/workflows/sim-gate.yml) runs it. With no args the device list **and** per-device posture (blocking / advisory / excluded) are **data-driven from `platform/ci-matrix.toml`**; naming devices runs just those, hard. **This is the gate** — green here == green on your PR. |
| `./sim devices` | List the virtual devices this image can run. |
| `./sim build [base\|demo\|all]` | Build the pinned image(s) without running anything. |
| `./sim shell [demo]` | Interactive debug shell inside the container (caps attached). |
| `./sim version` | Show the pinned platform / qemu-tsp / SDL3 refs. |

## Add a device = add data

Bringing a new device into the fleet — into the sim **and** the CI gate — is a **pure data change
in the `platform` repo**: no code, no workflow edits. Write the descriptor, render the skin from
the `.scad` model, add one posture row, and the device auto-joins every gate as *advisory*.

**→ The complete, cold-start walkthrough is [`docs/ADD-A-DEVICE.md`](docs/ADD-A-DEVICE.md)** — with
tonight's a523 (TrimUI Smart Pro S) fold-in as the worked example.

## How it runs the app (owner decisions, 2026-06-26)

- The identical arm64 binary runs under **[`qemu-tsp`](https://github.com/pocketforge-os/qemu-tsp)**
  (the PocketForge fork of qemu-user that translates evdev/uinput ioctls — **stock qemu-user
  translates none**) + binfmt, inside **bubblewrap** (lightweight ns/chroot), **NO crun /
  cgroups / seccomp**. This keeps the sim entirely off the unbuilt Phase-2 container substrate;
  the launcher swaps to real crun later — the app binary is identical.
- The VDB substitutes the **bottom seam only** (kernel device nodes + sensor sources): a
  `uinput` evdev device advertising exactly the descriptor's codes + `absinfo`, a virtual
  `/dev/fb0` (software-render), and broker-backed capability responses. The app cannot tell
  the sim from a quiet device.
- Sim host = **x86** (modelmaker / CI runner), viable **because of** the qemu-tsp fork.

## The three CI gates it feeds

The same suite is a required check in three repos, all deriving their device rows + posture from
the one `platform/ci-matrix.toml` keystone (see [`docs/ADD-A-DEVICE.md`](docs/ADD-A-DEVICE.md)):

| Gate (repo) | Runs | Posture |
|-------------|------|---------|
| `sim-gate` ([sim](.github/workflows/sim-gate.yml)) | `check-control` + `check-sensor` + `check-skin`, nested | **required** — a133 blocking key |
| `hwprobe-smoke` ([pf-hwprobe](https://github.com/pocketforge-os/pf-hwprobe)) | the same descriptor-generic matrix, with `pf-hwprobe.arm64` as the app | **required** — a133 blocking key |
| `sim-descriptor-gate` + `skin-drift` ([platform](https://github.com/pocketforge-os/platform)) | rebuilds the sim against the PR's descriptors / checks the `.scad → skin → [skin.parts]` chain | advisory |

## Layout

```
sim              The one-command developer CLI (this is the front door)
docs/            HONESTY-CONTRACT.md · CLI.md · ADD-A-DEVICE.md · IMAGE-PUBLISHING.md · README.md (index)
Dockerfile       The pinned, reproducible image the CLI wraps (all refs pinned; apt snapshot-pinned)
docker/          Image README + the nested-container cap set (the tsp-qc1.2 nesting verdict)
control/         Headless control-surface suite (check-control) — input mapping proof
sensor/          Headless sensor suite (check-sensor) — capability/degradation proof
skin/            Skin suite (check-skin) + the clickable window driver (sim gui)
sdl3/            Pinned stock SDL3 build tooling (gamepad-only, static; SDL3.pin = release-3.4.10)
spike3/          SPIKE-3 — uinput gamepad indistinguishable to SDL3 UNDER qemu-tsp
  baseline/      Captured proof artifacts (native vs qemu-tsp transcripts + diffs)
harness/         arm64 rootfs + bubblewrap+qemu-tsp launcher
fb/              Software-render framebuffer path (widget/layout logic, not the on-device blob path)
synth/           Descriptor → uinput device synthesis
```

## SPIKE-3 — the load-bearing proof

Proves a host-synthesized `uinput` "TRIMUI Player1" (045e:028e) is **indistinguishable to
SDL3 gamepad enumeration**, with the arm64 probe running **under qemu-tsp** (native x86
hides the stock-qemu evdev gap). It asserts (see `spike3/check-spike3.py`) that the SDL
enumeration JSON is **byte-identical** native-x86 vs arm64-under-qemu-tsp, the raw evdev probe is
byte-identical, SDL recognizes a **gamepad** whose gamecontrollerdb GUID ==
`030000005e0400008e02000010010000`, and the descriptor's `emit-sdldb a133` fields all bind and
round-trip as a live SDL mapping. Artifacts land in `spike3/baseline/`. You never run it by hand —
`./sim check` exercises the same chain in the pinned container.

## Cross-repo inputs

- `platform/devices/<id>/capabilities.toml` + `platform/core/caps.py` (`emit-sdldb`,
  `probe-diff`) — the descriptor and its tooling (source of truth).
- `platform/device-models/<model>/` + `platform/skins/<id>/` — the `.scad` semantic model and the
  rendered bezel art for the clickable skin.
- `platform/ci-matrix.toml` — the data-driven fleet matrix (device rows + posture).
- `qemu-tsp` — the evdev-ioctl-aware qemu-user fork.

Epic **tsp-an4** / kickoff `infra-104`; productized + gated to GA under `infra-113`.
