# Add a device = add data

Bringing a new PocketForge device into the simulator **and** the CI gate is a **pure data change
in the [`platform`](https://github.com/pocketforge-os/platform) repo**. You write a descriptor,
render a skin from a 3D model, and add one posture row. You touch **no application code, no test
code, and no CI workflow** — the device auto-joins every gate. This is the "add a device = add
data" keystone of the fleet (infra-113 §5 / decision D6).

This guide is written for a developer who has never seen the project. It uses the **a523 (TrimUI
Smart Pro S, Allwinner A523)** as the worked example — it was folded into the fleet exactly this
way, alongside the a133 base unit, with zero per-device code.

> Read [`HONESTY-CONTRACT.md`](HONESTY-CONTRACT.md) first. The sim proves the **logical layer
> only**. Adding a device adds a *logical* device to the sim + CI; the on-hardware graphics, GPU,
> WiFi, timing, and enforcement bring-up for that SoC stays the flash → serial → webcam hardware
> gate's job.

## The one chain you are extending

Everything a device needs to *exist* in the sim and CI is four artifacts in `platform`, in one
consistency chain:

```
 devices/<id>/capabilities.toml     ← THE DESCRIPTOR (source of truth: inputs, sensors, caps, skin rects)
 device-models/<model>/<model>.scad ← THE SEMANTIC 3D MODEL (mm-accurate, controls named by semantic id)
        │  render.py --write
        ▼
 skins/<id>/body.png                ← neutral device art        │  consumed by the sim GUI and
 skins/<id>/body_lit.png            ← pairwise-safe lit atlas    │  check-skin; the [skin.parts]
 skins/<id>/model-render.json       ← recorded hashes + rects    │  rects index crops of body_lit
        │
 ci-matrix.toml [devices]           ← THE POSTURE ROW (blocking | advisory | excluded)
```

The descriptor's `[skin.parts]` rectangles, the rendered atlas, and the `model-render.json`
metadata **must stay in lockstep** — a render-free CI gate (`skin-drift`) enforces exactly that
(see [What auto-joins in CI](#what-auto-joins-in-ci)).

## Step 1 — the descriptor (`devices/<id>/capabilities.toml`)

The descriptor is the single source of truth. One file describes the device to the **app**, the
**simulator**, and the **CI test** — there is no per-device code anywhere else. It names:

- **inputs** — each control's evdev `code`, its SDL binding, and its `skin_part` (the id that ties
  it to a rectangle in the rendered skin);
- **sensors / capabilities** — what hardware the device advertises, so the sim can prove graceful
  degradation (a row omitted ⇒ a typed `hardware-absent` / no-op, not a crash);
- **the skin block** — `[skin]` (`body` / `lit_body` PNG paths) and `[skin.parts]` (one
  `{x,y,w,h}` rect per control) + `screens.display_rect`.

For a523 this is `devices/a523/capabilities.toml`. It is the **same shape** as a133's — the a133 vs
a523 delta is **data, not code**: a523 carries a fifteenth semantic control (`btn_home`, the Pro S
guide/home button) and the L3/R3 thumbstick-clicks the base unit lacks. Nothing in the app, the
suites, or the workflows knows the difference; they read it from the descriptor.

**Reconcile the descriptor to real silicon.** The codes/`absinfo` a real unit advertises are
verified on hardware (the HIL drift lane: `evdev-dump → pf caps probe-diff → committed fixtures`).
Until a row is silicon-reconciled it stays *advisory* (Step 4). The sim proves the descriptor is
*internally* consistent and maps cleanly to SDL3; it does **not** prove the real chip advertises
those exact codes — that is the hardware gate's authority (see the honesty contract).

## Step 2 — the semantic 3D model (`device-models/<model>/<model>.scad`)

The skin art is not hand-drawn — it is **rendered from an OpenSCAD model** authored in
millimetres, whose controls carry the **same semantic ids** as the descriptor. This is what makes
the highlight rectangles trustworthy: they are derived from geometry, not eyeballed.

- a133 base unit → `device-models/trimui-smart-pro/trimui-smart-pro.scad` (14 semantic controls).
- a523 Pro S → `device-models/trimui-smart-pro-s/trimui-smart-pro-s.scad`, a **shared-chassis
  derivative** that carries the TG5050 identity, cooling details, and the fifteenth `btn_home`
  control **without redrawing** the accepted TG5040 baseline.

A model directory ships an OpenSCAD source with a fixed coordinate system, a measurement/provenance
table (what is measured vs published vs photo-derived, with confidence), and a `render.py` +
`compare.py`. See [`device-models/README.md`](https://github.com/pocketforge-os/platform/blob/main/device-models/README.md)
and each model's own README for the authoring contract. The model is a **1:1 nominal visual/UI
model** — good for device identity, input highlighting, and layout; it is *not* a manufacturing
tolerance drawing (each README states its limits honestly).

## Step 3 — render the skin (`render.py`)

`render.py` turns the `.scad` model into the three checked-in skin artifacts. From the `platform`
repo root, for a523:

```bash
python3 device-models/trimui-smart-pro-s/render.py --write
```

`--write`:

- renders **`body.png`** (the neutral device);
- renders each control **one at a time** and composes **`body_lit.png`** as a **pairwise-disjoint
  atlas** — after *proving* every crop rectangle is disjoint, so a rectangular crop can never light
  a neighbouring control (this matters for the diagonal shoulder arcs — their bands are split so
  L/L2 and R/R2 stay independent);
- records the camera, source + renderer sha256, output sha256, atlas policy, and the derived
  `[skin.parts]` rectangles into **`skins/<id>/model-render.json`**.

The generated PNGs are **checked in** on purpose: the target app (and the sim) must not need
OpenSCAD at runtime. The `.scad` source + `model-render.json` make them reproducible. Rendering
expects the `Liberation Sans` + `Ubuntu Sans` font families (the latter is the photographed
extra-bold-italic enclosure silkscreen).

Then copy the derived rects into the descriptor's `[skin.parts]` (or reconcile them if you authored
the descriptor first) so `capabilities.toml [skin.parts]` **equals** the atlas rects. The two must
match exactly — the drift gate asserts it.

### Verify locally with `render.py --check`

```bash
python3 device-models/trimui-smart-pro-s/render.py --check
```

`--check` re-renders from source and **fails if any PNG, capability rectangle, atlas pixel, or the
non-overlap invariant has drifted**. It is the **full-fidelity local companion** to the CI
skin-drift gate — run it whenever you touch a model or its skin.

> **Honesty caveat — `render.py --check` is host-jitter-limited today (bead `tsp-vevy`).** The
> full OpenSCAD re-render is **not** wired into CI, and cannot be a byte-stable green-on-main check:
> the `.scad`'s "Ubuntu Sans" variable font is absent from Debian bookworm (different silkscreen
> pixels), the render suite can time out under headless software GL, and GPU-vs-`llvmpipe`
> anti-aliasing risks flaky reds. A flaky gate would poison the gate-trust this whole effort exists
> to build, so `--check` stays the **local** full-fidelity companion. The **render-free**
> `skin-drift` gate (below) is what runs in CI. Run `--check` on a host with the right fonts and
> expect that pixel jitter is environmental, not a real drift — until `tsp-vevy` closes that gap.

## Step 4 — the posture row (`ci-matrix.toml`)

[`platform/ci-matrix.toml`](https://github.com/pocketforge-os/platform/blob/main/ci-matrix.toml) is
**the fleet keystone** — the single source of truth for *which* devices the sim CI suites run and
*each device's posture*. All three gate workflows derive their device rows + posture from here (via
`pf caps matrix`), so **adding a device to CI is a data change here, never a workflow edit.**

Three postures:

| Posture | Meaning |
|---------|---------|
| `blocking` | a suite failure **blocks merge** (the required check goes red). Promotion to blocking is deliberate — it needs (a) descriptor silicon-reconciled, (b) baselines current vs platform `main`, (c) N stable green runs. |
| `advisory` | a suite failure **reports but does not block**. This is the **default** for any device that ships a `capabilities.toml` and is *not* listed — a new descriptor **auto-joins as advisory with zero workflow edits** (D6). Listing it explicitly is optional but self-documenting. |
| `excluded` | deliberately **not run, not gated** — and this **must be explicit**. A device directory carrying only a `profile.toml` (a build-profile variant, no `capabilities.toml`) is a `pf caps matrix validate` **error** until explicitly excluded here — so a non-participating device is **never a silent skip**. |

The current matrix, as the worked example:

```toml
[devices]
a133       = "blocking"    # base unit — live rev-5.0 DUT on pf-node-01; the sole blocking key today
a523       = "advisory"    # Pro S — sim rows STAY (device-free, pure data), hardware descoped ⇒ advisory
a133-owned = "excluded"    # profile.toml only (build-profile variant) — explicitly not gated
sdm845     = "excluded"    # profile.toml only (porting target) — explicitly not gated
```

a523 runs **advisory**: the sim rows carry the multi-device "zero-per-device-code" proof (they are
pure data and device-free), but a523 *hardware* work is descoped (owner, 2026-07-25; infra-113 D2),
so a suite failure reports without blocking. Promoting a523 to `blocking` later is a one-line data
change here, once it meets the (a)/(b)/(c) bar. Completeness is enforced: `pf caps matrix validate`
(folded into `pf caps validate --all`) errors if a descriptor'd device is missing a considered
posture, or a profile-only device is not explicitly excluded — so the matrix can never silently
drop a device.

## What auto-joins in CI

Once the four artifacts above are committed to `platform`, the device joins **every** gate with no
further edits:

- **The `./sim check` matrix** — `./sim check` with no args reads `ci-matrix.toml` via
  `pf caps matrix` *inside the pinned image*, so the device list + posture are data-driven. `./sim
  check` and the `sim-gate` CI run make the identical decision (see [`CLI.md`](CLI.md)).
- **The three gate workflows** — `sim-gate` (sim), `hwprobe-smoke` (pf-hwprobe), and
  `sim-descriptor-gate` (platform) all derive their device rows from the same `ci-matrix.toml`.
  The new row runs advisory automatically; no workflow device list to hand-edit.
- **The `skin-drift` gate** (platform) — [`check-skin-drift.py`](https://github.com/pocketforge-os/platform/blob/main/device-models/check-skin-drift.py)
  **auto-discovers** by globbing `skins/*/model-render.json`, so *committing a rendered skin is all
  it takes to enrol the device*. For each discovered skin it asserts the recorded source / renderer
  / `body` / `body_lit` sha256 still match the committed files, and that its control rects +
  `display_rect` **equal** the descriptor's `[skin.parts]`. It is render-free (byte-stable, no
  OpenSCAD) so it runs on every relevant PR. Its guarantees are a **strict subset** of `render.py
  --check` — it catches every "edit one artifact, forget to regenerate the rest" drift, but a
  *consistent* hand-edit of a rect in **both** `model-render.json` and `capabilities.toml` (without
  re-rendering) still agrees byte-wise and slips past it. That narrow, semi-adversarial case is why
  you run `render.py --check` locally (Step 3).
- **The clickable GUI picker** — `./sim gui` shows a manufacturer › device picker composed from the
  descriptors + skins; the new device appears in it with no code change.
- **The headless suites** — `check-control` / `check-sensor` / `check-skin` run against the new
  descriptor as one more matrix row. Same source, same binary; the device delta is data.

## The whole flow, end to end

```bash
# in a platform worktree/branch:
$EDITOR devices/a523/capabilities.toml                         # 1. descriptor (inputs, sensors, caps, [skin.parts])
$EDITOR device-models/trimui-smart-pro-s/trimui-smart-pro-s.scad  # 2. semantic 3D model (or reuse a shared chassis)
python3 device-models/trimui-smart-pro-s/render.py --write     # 3a. render body/body_lit/model-render.json
python3 device-models/trimui-smart-pro-s/render.py --check      # 3b. full-fidelity local verify (host-jitter caveat above)
$EDITOR ci-matrix.toml                                          # 4. posture row (or leave it to auto-join as advisory)
python3 device-models/check-skin-drift.py                       #     render-free drift check, exactly as CI runs it
pf caps matrix validate                                         #     completeness: no silent skips
# commit + PR to platform. Then, from a sim clone, prove the sim agrees:
./sim check a523                                                #     run just the new row, hard
./sim gui a523                                                  #     click the bezel — the a523 skin lights
```

No application code. No test code. No workflow edit. **Add a device = add data.**

## Where each piece lives (cross-repo map)

| Artifact | Repo · path |
|----------|-------------|
| Descriptor | `platform/devices/<id>/capabilities.toml` |
| Semantic 3D model | `platform/device-models/<model>/<model>.scad` + `render.py` |
| Rendered skin | `platform/skins/<id>/{body,body_lit}.png` + `model-render.json` |
| Posture row | `platform/ci-matrix.toml` |
| Drift gate | `platform/device-models/check-skin-drift.py` → `.github/workflows/skin-drift.yml` |
| Descriptor gate | `platform/.github/workflows/sim-descriptor-gate.yml` |
| Sim suites + CLI | `sim/{control,sensor,skin}/` · `sim/sim` · `sim/.github/workflows/sim-gate.yml` |
| hwprobe smoke gate | `pf-hwprobe/.github/workflows/hwprobe-smoke.yml` |

## References

- [`HONESTY-CONTRACT.md`](HONESTY-CONTRACT.md) — what the sim does and does **not** prove.
- [`CLI.md`](CLI.md) — the `./sim` command reference and the GUI display-path tradeoff.
- [`platform/device-models/README.md`](https://github.com/pocketforge-os/platform/blob/main/device-models/README.md) — the semantic-model authoring contract + the drift gate.
- infra-113 §5 / decisions D6 (data-driven matrix) & D9 (skin-drift) — the governing plan.
