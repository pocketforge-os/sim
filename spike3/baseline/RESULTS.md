# SPIKE-3 (tsp-an4.2) — captured proof + results

Built/run on **modelmaker** (Threadripper x86_64, Ubuntu 24.04), 2026-06-26.
- `qemu-tsp` v8.2.2 (pin 11aa0b1) — github.com/pocketforge-os/qemu-tsp, regression PASS on this build.
- stock SDL3 **release-3.4.10**, gamepad-only static (no video/audio/udev/dbus/hidapi).
- device: host-native `uinput` "TRIMUI Player1" (045e:028e ver 0110) — the a133 code/absinfo superset.

## RESULT: PASS — indistinguishable to SDL3 gamepad enumeration UNDER qemu-tsp

| claim | evidence | result |
|-------|----------|--------|
| SDL enumeration **byte-identical** native-x86 vs arm64-under-qemu-tsp (builtin map) | `out.x86.builtin.json` == `out.arm64.builtin.json` | **identical** |
| …same with the descriptor (`emit-sdldb`) map | `out.x86.descriptor.json` == `out.arm64.descriptor.json` | **identical** |
| raw evdev C probe byte-identical native vs qemu-tsp (re-confirms tsp-an4.1 at SDL-open) | `evdev.native.txt` == `evdev.qemu-tsp.txt` | **identical** |
| SDL recognizes a **gamepad**; gamecontrollerdb GUID == a133 `sdl_guid` | `guid_gamecontrollerdb` = `030000005e0400008e02000010010000` | **match** |
| descriptor `emit-sdldb a133` fields all bind in SDL's builtin map (asymmetric subset) | `check-spike3.py` step D | **all bound** |
| one-descriptor round-trip: feeding `emit-sdldb` as the SDL map reproduces exactly the descriptor field set | `check-spike3.py` step E | **exact** |
| evdev-layer asymmetric subset (descriptor codes ⊆ probe) | `probe-diff.a133.txt` | **OK** |

### Key detail — the name-CRC in the GUID
The live device's raw GUID is `0300a3845e0400008e02000010010000`: bytes 2-3 = `a384` =
`crc16("TRIMUI Player1")`. SDL also **renames** the joystick to its built-in mapping name
("Xbox 360 Controller"); the raw evdev name survives only as that CRC. The
**gamecontrollerdb form** (CRC field zeroed) = the descriptor's `sdl_guid`. Real TrimUI
hardware reports the same evdev name → same CRC → **still indistinguishable**. The probe
therefore matches by **vid/pid** (the identity SDL preserves), not by name.

### Honest scope
SDL's builtin X360 map binds `leftstick:b9,rightstick:b10` (the shared HID superset); the
**base a133 has no L3/R3**, so its descriptor omits them and the asymmetric rule passes
(descriptor ⊆ advertised). This is INPUT-layer honesty only — no GPU/render is touched
(`SDL_VIDEODRIVER=dummy`, gamepad-only build). See `../../docs/HONESTY-CONTRACT.md`.

## Reproduce
```bash
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 \
SDLDIR=/home/mm/sim-build/sdl3 PLATFORM=/home/mm/platform \
  ../run-spike3.sh           # asserts via check-spike3.py, exit 0 on PASS
```
