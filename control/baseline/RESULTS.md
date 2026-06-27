# tsp-an4.5 — injection-as-API control surface: RESULTS (PASS, both devices)

Run on **modelmaker** (x86_64, GPU-less), 2026-06-26:

```
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 SDLR=/home/mm/sim-build/sdl3-render \
ROOTFS=/home/mm/sim-build/harness/rootfs-arm64 PLATFORM=/home/mm/platform ./control/run-control.sh
=> ALL DEVICES PASS (a133 a523)   —   282 assertions ok, 0 fail
```

## The contract, proven

ONE injection-as-API control surface (`control_surface.Device`) drives the descriptor-synthesized
`uinput` device (`.3`) and reads the virtual framebuffer (`.4`) for BOTH descriptors with **zero
per-device test code** — a133 and a523 are two matrix rows that differ only by descriptor data.
The GUI skin (C6) and the E7 CI harness are co-equal clients of this same surface
(injection-as-API-FIRST). The headline ran verbatim on both:

```python
dev.press("south");          assert dev.framebuffer_region("south").is_red()
dev.set_axis("ltrig", 0.5);  assert dev.slider("ltrig").at(0.5)
dev.assert_capability_absent("imu")     # a133; a523 -> assert_capability_present("imu")
```

The proof is end-to-end through real `uinput`→`evdev`→`qemu-tsp`: the host injects via the control
surface, the IDENTICAL arm64 app (`hwprobe-lite`) reads the events and lights the pressed control
onto a memfd virtual fb, and the host samples the region colour deterministically
(`fb/ppm2png` PPM sampling — NOT a VLM; `tsp-visual-inspection` hallucination caveat).

## Assertions (both devices, all green — `check-control.py`)

- **Headline** — `press("south")`→region red; `set_axis("ltrig",0.5)`→slider reads 0.500;
  capability absent/present per descriptor.
- **Digital matrix** — every `EV_KEY` control (face/shoulder/system buttons + a523 stick-clicks
  l3/r3 + home) lights ITS region on press, leaves a different region dark (isolation = the
  id→code→event→decode→render binding is correct, not faked), clears on release.
- **D-pad hat** — `ABS_HAT0X/Y` deflect → dpad lit; centre → dark.
- **Analog sticks** — `set_stick` deflect past the descriptor deadzone → lit; centre → dark.
- **Analog triggers** — sweep 0→0.25→0.5→0.75→1.0 reads back `0.000 0.250 0.500 0.750 1.000`
  (slider fill scaled across the descriptor `min..max`), strictly monotonic.
- **Absent controls** — a133 `home`/`l3`/`r3` → typed `HardwareAbsent`, NOT a crash.
- **Pose / capability** — `set_pose` works iff the descriptor has an IMU (a523) else typed
  hardware-absent (a133); `location` denied by the cooperative permission facade (no GNSS).
- **PARITY** — the IDENTICAL arm64 binary under qemu-tsp produces **byte-identical** frames to the
  native x86 build: a133 **35/35**, a523 **41/41** frames identical.

## Topology proof (descriptor-derived nodes; `app.qemu.log`)

- a133: `1 node` (gamepad only; all `BTN_*`), `14 controls`.
- a523: `2 nodes` (gamepad `event2` + a SEPARATE system node `event12` for the `KEY_HOMEPAGE`
  home key), `15 controls`. The extra node + the home/l3/r3 controls are the omission/added-rows
  proof carried over from `.3`.
- Both: `virtual fb: memfd 1280x720`, `tsp-osr-pin: OK window(no-GL)+SDL_CreateRenderer("software")`.

## Found + fixed (load-bearing, not hand-waved)

`synth/uinput_synth.py::_emit` packed the event with `"<llHHi"` — `<` forces standard-size `l`=4,
i.e. a **16-byte** `input_event`, which a 64-bit kernel rejects with `EINVAL` (count <
`sizeof(input_event)`=24). `.3` shipped this primitive but its check only probed *advertised*
codes (`EVIOCGBIT`), never the inject path, so the bug was latent. `.5` is the first real consumer;
fixed to `"<qqHHi"` (8-byte time fields → 24 bytes). Generating + exercising the injection rather
than trusting it is what surfaced it.

## Evidence (per device, in `a133/` and `a523/`)

`rest.png` (all controls grey) · `south_press.png` (south/A lit red) · `guide_press.png` ·
`dpad_deflect.png` · `ltrig_050.png` / `ltrig_100.png` (half / full trigger fill) · `layout.txt`
(the descriptor-computed canvas layout the app drew from) · `app.qemu.log` (memfd + tsp-osr-pin +
node provenance). The per-launcher working dirs (full PPM frames, FIFOs, native log) are
gitignored (regenerable).

## Honesty

Proves the LOGICAL layer ONLY: descriptor correctness, input mapping (id→code→event→render), the
capability/permission CONTRACT, and graceful missing-hardware degradation. NOT the on-device GPU
blob / sunxifb / dc_sunxi→DE2.0→fb0 path, NOT real WiFi, NOT timing/thermal, NOT enforcement
(qemu-user stubs guest seccomp; the broker stub is cooperative), NOT per-SoC graphics. Those five
stay the flash→serial→webcam hardware gate's sole authority. See `../docs/HONESTY-CONTRACT.md`.
