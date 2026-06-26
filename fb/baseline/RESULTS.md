# tsp-an4.4 — virtual fb + software-render headless: RESULTS (PASS, both devices)

Run on **modelmaker** (x86_64, **GPU-less path**), 2026-06-26:

```
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 SDLR=/home/mm/sim-build/sdl3-render \
ROOTFS=/home/mm/sim-build/harness/rootfs-arm64 PLATFORM=/home/mm/platform ./fb/run-fb.sh
=> ALL DEVICES PASS (a133 a523)
```

## The claim, proven

The IDENTICAL arm64 binary software-renders a known pattern to a **virtual framebuffer**
(`memfd`, the fb-handoff fd analog) on a **GPU-less host**, **byte-identically native-x86 and
under `qemu-tsp` inside bubblewrap**, with canvas size + rotation taken from the descriptor —
and dumps a PNG for assertion, without tripping `tsp-osr`.

## Assertions (both devices, all green — `check-fb.py`)

- **A. canvas geometry FROM DESCRIPTOR** — rendered fb is `1280×720` == `screens[0].render_canvas`, not hardcoded.
- **B. software-render correct** — TL red / TR green / BL blue / BR yellow / center white / bg gray sample EXACTLY (deterministic CPU rasterizer).
- **C. native-x86 == arm64-under-qemu-tsp** — canvas PPM byte-identical.
- **D. rotation honored as DATA** — present `720×1280` == `rotate(1280×720, cw90)`; canvas TL-red lands at present top-right. Logical rotation, NOT the per-SoC disp-engine silicon.
- **E. PNG artifact** — valid PNG produced (`ppm2png.py`, stdlib zlib).
- **F. tsp-osr PINNED** — `render.arm64.log`: `tsp-osr-pin: OK window(no-GL)+SDL_CreateRenderer("software") -> 'software'`. The virtual fb came up as `memfd 1280x720 (3686400 bytes)`.

## Evidence (per device, in `a133/` and `a523/`)

`canvas.png` (the 1280×720 landscape render) · `present.png` (the 720×1280 logical-rotation
present frame) · `render.x86.log` / `render.arm64.log` (memfd + tsp-osr-pin). Raw PPMs are
gitignored (regenerable, ~2.7 MB each).

## Honesty

Proves layout/widget logic + the renderer-creation recipe ONLY. NOT the on-device
`libSDL3-pocketforge` sunxifb backend, NOT the PowerVR UM/KM blob, NOT `dc_sunxi`→DE2.0→fb0,
NOT real panel rotation/timing. Per-SoC graphics diverge (A133 sunxifb/no-KMS vs A523
Mali/DRM-KMS); this software fb proves neither. On-device GPU bring-up stays the
flash→serial→webcam hardware gate's sole authority. See `../docs/HONESTY-CONTRACT.md`.
