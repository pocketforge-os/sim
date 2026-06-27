# tsp-an4.6 — clickable skin renderer + picker — RESULTS

Measured on **modelmaker** (x86, GPU-less) with `run-skin.sh`: the IDENTICAL arm64 app
(`hwprobe-lite`) under **qemu-tsp + bubblewrap** (NO crun) + the native x86 build for parity, the
`.5` control surface, and the `.6` SDL3 renderer built against `sim-build/sdl3-render`.

```
ALL DEVICES PASS (a133 a523)
a133: PASS (native 0 fail, qemu 0 fail, parity 0) — 35 input frames byte-identical native==qemu-tsp
a523: PASS (native 0 fail, qemu 0 fail, parity 0) — 41 input frames byte-identical native==qemu-tsp
```

## What was asserted (zero per-device test code; a133 and a523 differ only by descriptor rows)

**A. GUI-click == headless-inject (the load-bearing invariant).** For every control, the click
PIXEL (from the descriptor's raw `[skin.parts]` rect) is fed through `skin_model`'s hit-test +
gesture resolver, and the resolved `control_surface` Action is asserted **equal** to the action
`.5`'s headless test injects directly — then applied, and the correct control asserted lit via the
`.5` canvas-space readback. Samples:

```
TAP btn_south -> [('press','south'),('release','south')] == inject press/release(south)
TAP stick_l   -> [('press','l3'),('release','l3')]       == inject press/release(l3)   [a523]
DRAG stick_l->edge -> [('set_stick','lstick',1.0,0.0)]   == inject set_stick(lstick,1,0)
DRAG trig_l slider->0.50 -> [('set_axis','ltrig',0.5)]   == inject set_axis(ltrig,0.5)
TAP dpad(right) -> [('move_hat','dpad',1,0),('move_hat','dpad',0,0)] == inject move_hat(dpad,1,0)/centre
```

**B. Compositor geometry.** After lighting a control, the composited bezel is rendered
(`skin-render --shot`, no picker) and sampled at `map_canvas_point(centre of the lit control's
canvas rect)` — it must be red and inside `display_rect`. Every control passes with
`composite_rotation = none` (the data-driven choice; the cw90 in the descriptor is the per-SoC
panel-mount the sim does not reproduce). Sample:

```
south press: fb-lit 'btn_south' composites into display_rect at (1104,281) col=(220,30,30) (rot=none)
home  press: fb-lit 'btn_home'  composites into display_rect at (1037,142) col=(220,30,30) (rot=none)  [a523]
```

**C. Per-variant, zero code.**

```
picker lists ['a133','a523'] (>=2 variants) from [identity]
a133 stick_l tap -> [] (non-clickable: no stick-click row, pure DATA)
a523 stick_l tap -> press/release(l3)   (clickable: l3 row present)
absent 'home'/'l3'/'r3' on a133 -> typed hardware-absent (no crash)
```

## App on-screen rendering (the live framebuffer, enriched)

The shared app (`control/hwprobe-lite.c`) draws each control by KIND (emitted by `layout.py` from
the descriptor — zero per-device code, no hand-typed ABI codes; the per-axis role comes from the
generated table):

- **D-pad** → a directional **cross**: the pressed direction's arm + the centre hub light (so
  up/down/left/right are legible, not a featureless square).
- **Analog stick** → a **calibration box**: a vector from centre to the deflection position shows
  HOW FAR + WHICH WAY (like a stick-calibration chart).
- **Stick pressed (L3/R3)** → a **red border** — present only on the a523 (the `l3`/`r3` rows
  exist there; the a133 omits them, so its sticks never show pressed: a pure-DATA difference).
- Buttons stay solid; triggers stay a proportional fill.

The centre hub/arm guarantees the region-sample at each rect centre is red iff the control is
active, so **all `.5` + `.6` assertions still pass byte-identical native==qemu** with the richer art.

## Owner visual-OK artifacts

`baseline/<id>/avd_*.png` — the rendered AVD: the picker panel (TrimUI > 5040 / 5050, selected
highlighted) + the bezel `body.png` + the active control lit (bezel overlay) + the **live arm64
app framebuffer** composited into `display_rect`:

- `avd_rest.png` — nothing pressed
- `avd_dpad_{up,down,left,right}.png` — the directional d-pad cross
- `avd_lstick_diag.png` — left stick deflected up-right (the calibration vector)
- `avd_south_press.png` — A pressed (lit on the bezel **and** in the live app screen)
- `avd_ltrig_half.png` — left trigger at 50%
- `avd_l3_press.png` — **a523 only**: the left stick PRESSED (red calibration border)

These are the frames awaiting the owner's explicit visual OK (the bead's hardware/visual gate).
