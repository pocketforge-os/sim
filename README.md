# pocketforge-os/sim — Virtual Device Simulator (E5)

An **honest mock**: run the **identical arm64 OCI app binary** the device runs, off-hardware,
against a **Virtual Device Backend** synthesized purely from the
[`platform`](https://github.com/pocketforge-os/platform) device descriptor
(`devices/<id>/capabilities.toml`) — as a **hard CI gate**. One descriptor, three consumers
(broker / simulator / CI). Epic **tsp-an4** / kickoff `infra-104`.

> **Read [`docs/HONESTY-CONTRACT.md`](docs/HONESTY-CONTRACT.md) first.** The sim proves the
> **logical layer only** (descriptor correctness, input mapping, capability/permission
> semantics, graceful degradation). GPU blobs, WiFi, timing, enforcement, and per-SoC
> graphics stay the **flash → serial → webcam hardware gate's sole authority**.

## How it runs the app (owner decisions, 2026-06-26)

- The identical arm64 binary runs under **[`qemu-tsp`](https://github.com/pocketforge-os/qemu-tsp)**
  (the PocketForge fork of qemu-user that translates evdev/uinput ioctls — **stock qemu-user
  translates none**) + binfmt, inside **bubblewrap** (lightweight ns/chroot), **NO crun /
  cgroups / seccomp**. This keeps E5 entirely off the unbuilt Phase-2 container substrate;
  the launcher swaps to real crun later — the app binary is identical.
- The VDB substitutes the **bottom seam only** (kernel device nodes + sensor sources): a
  `uinput` evdev device advertising exactly the descriptor's codes + `absinfo`, a virtual
  `/dev/fb0` (software-render), and broker-backed capability responses. The app cannot tell
  the sim from a quiet device.
- Sim host = **x86** (modelmaker / CI runner), viable **because of** the qemu-tsp fork.

## Layout

```
sdl3/        Pinned stock SDL3 build tooling (gamepad-only, static; SDL3.pin = release-3.4.10)
spike3/      tsp-an4.2 SPIKE-3 — uinput gamepad indistinguishable to SDL3 UNDER qemu-tsp
  baseline/  Captured proof artifacts (native vs qemu-tsp transcripts + diffs)
harness/     (incoming, tsp-an4.2/.3/.4) arm64 rootfs + bubblewrap+qemu-tsp launcher
docs/        HONESTY-CONTRACT.md and design notes
```

## SPIKE-3 (tsp-an4.2) — the load-bearing proof

Proves a host-synthesized `uinput` "TRIMUI Player1" (045e:028e) is **indistinguishable to
SDL3 gamepad enumeration**, with the arm64 probe running **under qemu-tsp** (native x86
hides the stock-qemu evdev gap). Run on an x86 host with `/dev/uinput`:

```bash
# prerequisites built once on the sim host (modelmaker):
#   qemu-tsp:  github.com/pocketforge-os/qemu-tsp -> ./build.sh
#   SDL3:      sim/sdl3 -> OUT=/path ./build-sdl3.sh        (x86 + static arm64)
#   platform:  a checkout for core/caps.py + devices/a133/capabilities.toml
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 \
SDLDIR=/home/mm/sim-build/sdl3 \
PLATFORM=/home/mm/platform \
  sim/spike3/run-spike3.sh
```

It asserts (see `spike3/check-spike3.py`): the SDL enumeration JSON is **byte-identical
native-x86 vs arm64-under-qemu-tsp** (builtin and descriptor mappings), the raw evdev probe
is byte-identical, SDL recognizes a **gamepad** whose gamecontrollerdb GUID ==
`030000005e0400008e02000010010000`, the descriptor's `emit-sdldb a133` fields all bind, and
that line round-trips as a live SDL mapping. Artifacts land in `spike3/baseline/`.

## Cross-repo inputs

- `platform/devices/<id>/capabilities.toml` + `platform/core/caps.py` (`emit-sdldb`,
  `probe-diff`) — the descriptor and its tooling (source of truth).
- `platform/skins/<id>/` — bezel art for the clickable skin (tsp-an4.6).
- `qemu-tsp` — the evdev-ioctl-aware qemu-user fork (built/verified in tsp-an4.1).
