# skin/ — tsp-an4.6: clickable skin renderer + manufacturer>device picker (the AVD north star)

A small SDL3 host GUI that renders the **clickable AVD-style skin** for a device and an
Android-style **manufacturer > device picker** (TrimUI > 5040 / 5050). It is a **client of the
.5 control surface**, not a second injection path: a click on a skin rect resolves — through the
*same* descriptor — to the *same* `control_surface.Device` call the headless test injects. ONE
descriptor (`platform/devices/<id>/capabilities.toml`) → app + simulator + test + **SKIN**.

```
   click btn_south's rect ─┐                         ┌─ headless test: dev.press("south")
                           ├─► skin_model resolves ──┤   (.5 check-control.py)
   (GUI, this wall)        │   to Action("press",    │
                           │   "south") ─► dev.press ─┘   IDENTICAL call, IDENTICAL effect
```

## Files

| file | what |
|------|------|
| `skin_model.py`   | the toolkit-agnostic **brains**: the picker (scanned from `[identity]`), the raw `[skin.parts]` rects in skin-image space, hit-test + gesture→`control_surface` Action, and the compositor geometry (canvas fb → `display_rect`). Reused by both the renderer and the proof. |
| `skin-render.c`   | the **SDL3 renderer** (tsp-osr-safe). Composites bezel `body.png` + per-control `body_lit.png` overlay + the live virtual fb (into `display_rect`) + the picker panel. `--shot OUT.ppm` (offscreen, the proof + owner artifacts) or `--window` (live mouse → stdout clicks; needs a video-capable SDL3 + a display). |
| `check-skin.py`   | the **headless proof** (sibling of `.5`'s `check-control.py`): GUI-click == headless-inject, compositor geometry, per-variant zero-code, both descriptors. Emits the owner AVD shots. |
| `build-skin-render.sh`, `run-skin.sh` | build the renderer / run the whole proof on modelmaker. |
| `gen-font.py`, `font8x13.h` | the committed bitmap font (generated once with PIL on the laptop; the C build needs no PIL/SDL_ttf/SDL_image). |
| `baseline/<id>/avd_*.png` | the rendered AVD skins (the **owner visual-OK** artifacts). |

## The coordinate-space distinction (why .6 ≠ .5)

`.5`'s `layout.py` **fits** the skin parts into the 1280×720 `render_canvas`, because the `.5`
*app* draws the controls *into* that canvas. **`.6` is different**: the GUI draws the real bezel
`body.png` at **skin-image resolution** (1480×640) and hit-tests the **raw** `[skin.parts]` rects
(skin space). So `.6` reuses `layout.load_descriptor` + `part_for_input` but **not** the
canvas-fit rects. The live fb still comes from the `.5` control surface (canvas space) and is
composited into the bezel's `display_rect`.

## Rotation is DATA, not silicon

The fb the app renders is the landscape 1280×720 `render_canvas`. It composites into the bezel's
`display_rect` (872×490, also landscape). The composite orientation is **data-driven**: we use
whichever of `{canvas, rotate(canvas, screens.rotation)}` has the aspect matching `display_rect`
— for a133/a523 that is the canvas itself (`composite_rotation = none`), confirmed empirically
(a red marker at the canvas top-left lands at the screen top-left). `screens.rotation = cw90` is
the **per-SoC panel-mount mechanism** (A133 legacy disp2 / A523 DRM-KMS) that the descriptor's
own note flags as *"per-SoC CODE, not data here"* — the sim does **not** reproduce it (HONESTY
CONTRACT item 5). It is carried as data so a variant whose `display_rect` matched the rotated
dims would Just Work with zero per-device code.

## The app's on-screen widgets (directional d-pad + stick calibration)

The live framebuffer is the shared app (`../control/hwprobe-lite.c`) drawing each control by the
KIND `layout.py` emits from the descriptor (`button`/`trigger`/`hat`/`stick`) plus a per-axis
role (`x`/`y`/`t`/`k`) — so the app draws **direction**, not just lit/dark, with zero per-device
code and no hand-typed ABI codes (roles come from the generated `evdev_codes`). The d-pad renders
as a directional cross; a stick renders as a calibration box with a vector from centre to the
deflection position (how far + which way); a pressed stick (L3/R3) gets a red border — present
only on the a523, since the a133 omits the `l3`/`r3` rows. Each widget keeps a centre hub/arm lit
when active so the `.5`/`.6` centre-region assertions still hold byte-identical native==qemu.

## Optional descriptor fields — absent vs BOGUS (tsp-bu5e)

Three fields `skin_model.py` consumes are **OPTIONAL** in platform's
`schemas/capabilities.schema.json` — `screens[].display_rect` (`$defs.screen.required` is
`[role, render_canvas, present, rotation]`), and `lit_body` on both `[skin]` and
`[skin.views.<v>]` (`$defs.skin.required` / `$defs.skinview.required` are both `[body, parts]`).
All three used to be indexed directly, so a descriptor **platform's own validator called VALID**
made `Skin()` raise a bare `KeyError` from inside its constructor. Reproduced against platform
`8dce733`: deleting any one of them prints `OK a133: capabilities valid` from `core/caps.py`
while `Skin('a133')` raises.

The ruling follows [`../control/layout.py`](../control/layout.py):99-104 — the in-repo precedent
that deliberately gives **absent** and **present-but-bogus** different outcomes (absent
`skin_part` → `continue`; present but unknown → hard `ValueError`):

| field | ABSENT | rationale |
|-------|--------|-----------|
| `screens[0].display_rect` | **degrade**: front view gets `display_rect = None` and composites no fb — exactly the state an edge view is already in | `View.display_rect = None` is already a first-class modelled state here, and every consumer already branches on it (`emit_scene` → `display 0 0 0 0 none` + `fb -`; the CLI → `"display_rect": null`). Zero coverage is lost, because `check-skin.py` independently asserts the front view HAS one — so the model degrades and the **checker** judges |
| `[skin].lit_body` | **degrade**: `lit_body_path` falls back to `body_path`, `has_lit_body = False` | "no lit art" is a legitimate art-set state. The renderer needs *a* lit texture; the honest fallback is the unlit body, so a press simply changes nothing |
| `[skin.views.<v>].lit_body` | **degrade**, per view, identically | same schema shape, same semantics — stated deliberately rather than by default |

**Absence is a DECLARATION; a present-but-unresolvable value is a CLAIM THAT FAILED.** So a
`display_rect` that is present but not a well-formed rect, and a `lit_body` that is present but
names a file that is not there, both raise a designed `ValueError` naming the device, the table
and the remedy. Degrading those identically would make a typo indistinguishable from an
intention and turn the only signal that catches descriptor drift into a no-op — the
plausible-and-wrong fix. Every degradation is also made **visible**: a `skin_model: WARN …` line
on stderr, plus `has_lit_body` / `display_rect: null` in `skin_model.py show`.

Bounds are not invented here: `_require_rect`'s (`x,y,w,h` required, `w,h >= 1`) are copied from
the schema's own `$defs.rect`, so that arm can only fire on a descriptor the platform validator
also rejects. `_resolve_art`'s existence check is the rule already applied to `body` (which
hard-fails via `png_dims`), extended to its optional sibling.

### The negative control

[`selftest_skin_model_optional.py`](selftest_skin_model_optional.py) — device-free, stdlib only,
no qemu/uinput. `./sim check` runs it as a **pre-pass** before the per-device suites (so the gate
covers it with no workflow edit), and `pf-sim selftest-skin-optional` runs it alone.

Ten rows: `C*` the two unmodified shipped descriptors (green, and asserting **no** warning is
emitted); `A*` each absent field degrading, with the warning text and the scene output asserted,
plus a row proving the compositor **refuses** rather than computing on a substituted zero rect;
`B*` four present-but-unresolvable values, each asserted to fail **for its own message**.

Measured, and re-runnable — a guard nobody has seen fail is not a guard:

- against the **pre-fix** `skin_model.py`: **all 10 rows red**;
- against the **hard adversary** — the same fix with the same public surface but the bogus arm
  weakened to degrade everything — **exactly the 4 `B*` rows red, every `A*`/`C*` row green**,
  which is what shows the `B` arm is load-bearing rather than incidentally covered;
- with the guard broken, `./sim check` itself exits non-zero (`BLOCKING suite failure … merge is
  blocked`), so the wiring is load-bearing too;
- unusable input **fails loudly** (exit 2) — a missing `PLATFORM`, a non-platform dir, or a
  descriptor that no longer carries the shape a row mutates. It never SKIPs: a skip contributes
  nothing to CI's exit code, which is exactly what CI would see.

## What this proves — and the HONESTY CONTRACT

**Proves (logical layer):** the picker lists every skinned variant from `[identity]`; a click on
each `[skin.parts]` rect injects the correct input id through the `.5` control surface (==
the headless inject); analog drags scale to `set_axis`/`set_stick`; a523 sticks are clickable
(L3/R3) while a133's are not — a pure-**DATA** difference, zero per-device code; absent controls
raise typed `hardware-absent`; the live fb composites into `display_rect` with the data-driven
rotation. Verified by **deterministic region sampling**, never a VLM (tsp-visual-inspection's
hallucination caveat).

**Does NOT prove (stays the flash→serial→webcam gate's authority):** GPU blobs / the
PowerVR→dc_sunxi→DE2.0→fb0 path (the software-render fb proves nothing on-device); real WiFi;
timing/thermal; isolation/enforcement; **per-SoC graphics** (A133 sunxifb/no-KMS vs A523
kmsdrm/Mali). The "zero per-device code" claim is for the **I/O + skin layer only**.

## Run

**Today, run the skin suite in the pinned container** via `./sim run <device>` / `./sim check`
(headless), and open the clickable skin with `./sim gui [device]` — no host paths, no rsync (see
[`../docs/CLI.md`](../docs/CLI.md)). The standalone host-dev form below is for hacking on this
component directly on a build host (needs the SDL3/rootfs staged under `$HOME/sim-build`):

```bash
QEMU_TSP=$HOME/qemu-tsp/build/qemu-tsp/qemu-aarch64 \
SDLR=$HOME/sim-build/sdl3-render \
ROOTFS=$HOME/sim-build/harness/rootfs-arm64 \
PLATFORM=$HOME/platform \
bash run-skin.sh          # => ALL DEVICES PASS (a133 a523)
```

`--window` live mode (laptop with a display + a video-capable SDL3) opens the bezel; clicks are
written to stdout as `click <skin_x> <skin_y>` / `pick <codename>` for a driver to apply to a
`control_surface.Device`. The offscreen `--shot` path is the CI/acceptance path and needs no
display. **T0 desktop preview** for fast skin iteration is the `--shot` path with a synthetic fb
— explicitly **NOT the appliance binary**.
