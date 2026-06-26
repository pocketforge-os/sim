# `fb/` — virtual framebuffer + software-render headless (tsp-an4.4)

Runs the IDENTICAL arm64 binary the device would run, **headless on a GPU-LESS host**, and
renders a known pattern to a **virtual framebuffer** with SDL3's portable software
rasterizer — then dumps it to PNG for assertion. The AVD "ATD disables hardware rendering"
analog: prove layout/widget logic off-hardware, in CI, with no silicon and no GPU.

## What it does

- **Virtual `/dev/fb0` analog = a `memfd`.** `fb-render.c` `memfd_create`s a buffer (the
  supervisor→app fb-handoff fd), `mmap`s it, wraps it as an `SDL_Surface`, and renders into
  it. The host reads the memfd-backed dump back — exactly the seam the supervisor→app
  fb-handoff binds on-device, minus the silicon. (memfd is the bead's first-listed option;
  modelmaker has no `vfb` module and its real `/dev/fb0` is the host console — never touched.)
- **Software renderer, GPU-less.** `SDL_CreateSoftwareRenderer(surface)` — no window, no GL,
  no `/dev/dri`. Built against a VIDEO+RENDER **software-only** SDL3 (`build-sdl3-render.sh`:
  GL/GLES/Vulkan/X11/Wayland all OFF), static, so it runs under `qemu-tsp` with no sysroot.
- **Canvas + rotation are DATA from the descriptor.** `run-fb.sh` reads `screens[0]`
  (`render_canvas` + `rotation`) from `capabilities.toml` and passes them in — nothing is
  hardcoded. The app renders the **landscape 1280×720** canvas; the `cw90` **present**
  rotation (→ 720×1280 portrait) is applied as a logical transform, **NOT** the per-SoC
  disp-engine silicon.
- **PNG dump.** The app writes a raw `P6` PPM (trivial under qemu, no deps); `ppm2png.py`
  (stdlib `zlib`, no libpng/Pillow) makes the PNG artifact CI/the skin compositor (C6) wants.

## tsp-osr — pinned, not tripped

[`tsp-osr`](../../) is the open SDL3 RENDER segfault: a NULL renderer created on a window
**without** `SDL_WINDOW_OPENGL`. This stage avoids it two ways and **pins the safe recipe**
for the on-window app path (C6/E6):

1. The readback path uses `SDL_CreateSoftwareRenderer(surface)` — no window, no GL, so it
   **structurally cannot** trip it.
2. The window recipe an on-window app uses is pinned: a non-`OPENGL` window +
   `SDL_CreateRenderer(win, "software")` (forcing the software driver so SDL never enters the
   GL path). The run logs `tsp-osr-pin: OK ... -> 'software'` — it succeeds, no segfault.

## Run (modelmaker, x86, GPU-less is fine)

```bash
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 \
SDLR=/home/mm/sim-build/sdl3-render \
ROOTFS=/home/mm/sim-build/harness/rootfs-arm64 \
PLATFORM=/home/mm/platform \
  ./fb/run-fb.sh                 # a133 + a523; exit 0 = PASS; artifacts in fb/baseline/<id>/
```

`build-sdl3-render.sh` builds the software-only SDL3 once (reuses the SPIKE-3 SDL clone).

## Result (2026-06-26, modelmaker) — PASS, both devices

The IDENTICAL arm64 binary renders **byte-identically native-x86 and under
`qemu-tsp`+bubblewrap** on a GPU-less host. Assertions (`check-fb.py`): A canvas geometry ==
descriptor; B test-pattern regions exact (deterministic software render); C native == qemu
byte-identical; D rotation honored as data (cw90 → 720×1280; TL-red → present top-right);
E valid PNG produced; F tsp-osr-safe recipe pinned. Evidence: `fb/baseline/{a133,a523}/`
(`canvas.png`, `present.png`, `render.*.log`) + `RESULTS.md`. (Raw PPMs are gitignored —
regenerable, multi-MB.)

## Honesty

Proves **layout / widget logic + the renderer-creation recipe** only, off-hardware. It is
**NOT** the on-device path and must not be read as such: NOT the `libSDL3-pocketforge`
sunxifb backend, NOT the closed PowerVR UM/KM, NOT `dc_sunxi`→DE2.0→fb0, NOT the real panel
rotation/timing. A sim "it renders" proves **nothing** about on-device GPU bring-up — that
stays the flash→serial→webcam hardware gate's sole authority (`tsp-osr`, `capture-screen.sh`).
Per-SoC graphics diverge (A133 sunxifb/no-KMS/fb0 vs A523 Mali/DRM-KMS); this software fb
proves neither. See `../docs/HONESTY-CONTRACT.md`.
