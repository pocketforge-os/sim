# tsp-an4.3 — uinput synth-FROM-descriptor: RESULTS (PASS, both devices)

Run on **modelmaker** (x86_64, `/dev/uinput`), 2026-06-26:

```
QEMU_TSP=$HOME/qemu-tsp/build/qemu-tsp/qemu-aarch64 \
SDLDIR=$HOME/sim-build/sdl3 PLATFORM=$HOME/platform ./synth/run-synth.sh
=> ALL DEVICES PASS (a133 a523)
```

## The claim, proven

A **single** descriptor-driven synth path (`uinput_synth.plan`) creates `uinput` device(s)
that, read back through the kernel, **ARE the descriptor — exactly, with zero per-device
code** — for both a133 and a523. The a133↔a523 difference is **100% descriptor rows**.

| | a133 (Smart Pro) | a523 (Smart Pro S) |
|---|---|---|
| synth nodes | **1** (pad) | **2** (pad + system) |
| pad buttons | 9 (no L3/R3) | 11 (+`BTN_THUMBL`,`BTN_THUMBR`) |
| pad axes | `X Y Z RX RY RZ HAT0X HAT0Y` | same |
| system node | — (omitted) | `KEY_HOMEPAGE` (172/0xac) |
| `emit-sdldb` delta | no `leftstick/rightstick` | gains `leftstick:b9,rightstick:b10` |

The Pro→Pro-S delta (`home`/`l3`/`r3`) **lights up from descriptor rows with no sim code
change** — including the second (system-key) node, the extra pad buttons, and the resulting
SDL mapping fields. Missing hardware is **omission**, never a fabricated row.

## Assertions (both devices, all green)

- **A. ROUND-TRIP EXACT** — the live kernel advertises EXACTLY the codes + `absinfo`
  (min/max/fuzz/flat) that `plan(descriptor)` registered: no extra, no missing, ranges equal,
  per node. (descriptor → ioctls → kernel → `EVIOCG*` probe, compared to the descriptor.)
- **B. OMISSION / MATRIX** — a133: one node, no `BTN_THUMBL/THUMBR`, no system node. a523:
  two nodes, pad gains `BTN_THUMBL/THUMBR`, system node carries `KEY_HOMEPAGE`.
- **C. caps.py `probe-diff`** (independent asymmetric-subset logic) reports `OK` for both.
- **D. SDL3 native-x86 == arm64-under-`qemu-tsp`** byte-identical (builtin AND descriptor
  mappings); recognized as a GAMEPAD; gamecontrollerdb GUID == descriptor `sdl_guid`
  (`030000005e0400008e02000010010000`); exactly one `045e:028e` device.
- **E. raw C evdev probe byte-identical native vs `qemu-tsp`** — the `qemu-tsp` pass-through
  (tsp-an4.1) holds for this descriptor-synthesized device too.

Plus: `gen_evdev_codes.py --check` passes — the committed name→value table matches the kernel
ABI headers + `caps.py` vocab (no hand-typed codes; drift fails the run).

## Evidence (per device, in `a133/` and `a523/`)

`nodes.json` (synth node set) · `capture.json` (sim-owned `EVIOCG*` dump) · `probe-diff.txt`
(caps.py) · `emit-sdldb.txt` · `out.{x86,arm64}.{builtin,descriptor}.json` (SDL enum) ·
`evdev.{native,qemu-tsp}.txt` (raw C probe).

## Honesty

Proves the **logical input layer** only — descriptor correctness, code/absinfo round-trip,
the zero-per-device-code claim, SDL recognition under qemu-tsp. Does **not** prove GPU/WiFi/
timing/enforcement/per-SoC graphics (the flash→serial→webcam hardware gate's sole authority),
and does not model LED arrays, FF playback, or sensors (broker capabilities — C7). See
`../docs/HONESTY-CONTRACT.md`.

## Platform bug surfaced (filed separately)

`platform/regression/caps/evdev-probe.py` maps `KEY_HOMEPAGE` to `0x172` (370); the kernel
value is `172` (`0xac`). Decimal/hex slip; would mislabel the real Home key in E1 SPIKE-0 and
break a523 `probe-diff`. The sim owns `probe_evdev.py` (generated-table decode) so it is
correct by construction.
