# Add a device = add data

Bringing a new PocketForge device into the simulator, the CI gate **and** the 3D virtual device
(pfvd) is a **data change in the [`platform`](https://github.com/pocketforge-os/platform)
repo**. You measure the device, model it in OpenSCAD, regenerate its derived files, write a
descriptor, add a catalog row and a posture row. You touch **no application code, no test code
and no CI workflow**: the device joins every gate by being committed. This is the "add a
device = add data" keystone of the fleet (infra-113 §5 / decision D6). The one line of
tooling you do touch is the exporter's model registry ([Step 2](#step-2--model-it-in-openscad-device-modelsslug)).

This guide is written for a developer who has never seen the project. It uses two worked
examples:

- the **a523 (TrimUI Smart Pro S, Allwinner A523)**, folded into the fleet exactly this way
  beside the a133 base unit, with zero per-device code;
- platform's **synthetic clamshell fixture** (`tests/caps-fixtures/clamshell/`, device id
  `synth-clamshell`): two screens, a lid hinge, three postures. Every command in this guide was
  run on it; [Walked on the clamshell fixture](#walked-on-the-clamshell-fixture) lists what each
  step printed and, where a step cannot run on a synthetic fixture, why.

> Read [`HONESTY-CONTRACT.md`](HONESTY-CONTRACT.md) first. The sim proves the **logical layer
> only**. Adding a device adds a *logical* device to the sim, CI and pfvd. The on-hardware
> graphics, GPU, WiFi, timing and enforcement bring-up for that SoC stays the job of the
> flash → serial → webcam hardware gate.

## The one chain you are extending

Everything a device needs in order to *exist* in the sim, CI and pfvd lives in `platform`, in
one consistency chain. The `.scad` is the single source of truth; every file under `skins/` is
derived from it and gated:

```
 device-models/<slug>/<slug>.scad   ← THE SEMANTIC 3D MODEL (mm-accurate, controls named by semantic id)
 device-models/<slug>/render.py     ← renders the 2D skin from the .scad
        │  device-models/regen.sh <slug>   (render.py --write, export_gltf.py --write, render_glb_views.py, gates)
        ▼
 skins/<id>/body.png, body_lit.png  ← neutral device art + pairwise-safe lit atlas (sim GUI, check-skin)
 skins/<id>/model-render.json       ← recorded hashes + derived [skin.parts] rects + display_rect
 skins/<id>/model.glb               ← the 3D model pfvd loads (one node per control, screen quad)
 skins/<id>/model-glb.json          ← glb/.scad/exporter hashes + the six views' hashes
 skins/<id>/views/*.png             ← six orthographic renders of the glb (owner review)
        │
 devices/<id>/capabilities.toml     ← THE DESCRIPTOR (inputs, sensors, screens, [model], joints, maturity)
 devices/catalog.toml               ← THE CATALOG ROW (product name, code name, platform id, package, maturity)
 ci-matrix.toml [devices]           ← THE POSTURE ROW (blocking | advisory | excluded)
```

The descriptor's `[skin.parts]` rectangles, the rendered atlas, `model-render.json`, the glb,
its review views and the descriptor's `skin_part`/`node` names **must stay in lockstep**. The
render-free CI gates (`skin-drift`, `skin-drift-glb`) enforce exactly that (see
[Step 3](#step-3--regenerate-the-derived-files-regensh) and
[What auto-joins in CI](#what-auto-joins-in-ci)).

## Step 1 — measure the device

The model is only as good as its evidence. Follow the handbook chapter
[Model a handheld](https://pocketforge-os.github.io/handbook/hardware/model-handheld/): gather
public evidence and owner photographs, take caliper measurements, and record for every
dimension whether it is measured, published or photo-derived, with a confidence. Original
photographs stay outside git. The chapter also defines the owner visual review that accepts a
model before it is integrated.

For the clamshell fixture this step does not apply: it is synthetic test data, not a device.

## Step 2 — model it in OpenSCAD (`device-models/<slug>/`)

The skin art and the 3D model are not hand-drawn: both are **rendered from an OpenSCAD model**
authored in millimetres, whose controls carry the **same semantic ids** as the descriptor. That
is what makes the highlight rectangles and the clickable 3D nodes trustworthy: they come from
geometry, not from eyeballing.

- a133 base unit → `device-models/trimui-smart-pro/trimui-smart-pro.scad` (17 semantic
  controls, including the top-edge POWER key and the two volume-rocker halves).
- a523 Pro S → `device-models/trimui-smart-pro-s/trimui-smart-pro-s.scad`, a **shared-chassis
  derivative** that carries the TG5050 identity, cooling details and an eighteenth `btn_home`
  control **without redrawing** the accepted TG5040 baseline.

A model package directory ships:

- `<slug>.scad` with a fixed coordinate system (pf-mm-v1: X left→right, Y bottom→top,
  Z rear→front), a `CONTROL_IDS = [...]` list, part selection (`PART = "shell"`,
  `PART = "control"` with `CONTROL_ID = "<id>"`, `PART = "screen"`), highlighting
  (`HIGHLIGHT = "<id>"` or `"*"`) and the palette tokens the exporter reads
  (`shell_rear_color`, `control_color`, `glass_color`);
- `render.py` (with `--write`, `--check` and, when the skin has extra views, `--write-views`)
  and `compare.py`;
- a `README.md` with the measurement/provenance table and an honest limitations section;
- optionally `fixture-contract.json`, the manufacturing handoff to `test-node-hw` (see
  [`device-models/README.md`](https://github.com/pocketforge-os/platform/blob/main/device-models/README.md#fixture-contracts--the-manufacturing-handoff)).

Then register the package with the exporter: one row in the `MODELS` table of
[`device-models/export_gltf.py`](https://github.com/pocketforge-os/platform/blob/main/device-models/export_gltf.py)
naming the slug, the skin directory id and the descriptor (or `None` for a model-only package
such as the Brick). `regen.sh` refuses a slug that is not in that table.

The model is a **1:1 nominal visual/UI model**: good for device identity, input highlighting and
layout, *not* a manufacturing tolerance drawing (each README states its limits).

Two current limits of the exporter, both relevant to articulated devices: it exports **one**
screen quad per device (a descriptor with two `[[screens]]` is refused), and it writes no
`pivot_<joint>` nodes yet. That is why the clamshell fixture's glb is written by its own
`gen_fixture.py`, in the exporter's conventions, instead of by `export_gltf.py`.

## Step 3 — regenerate the derived files: `regen.sh`

**After editing a `.scad`, run `device-models/regen.sh <slug>` and commit everything it changed;
CI refuses a stale derivative.**

```bash
device-models/regen.sh trimui-smart-pro-s     # from the platform repo root
```

[`regen.sh`](https://github.com/pocketforge-os/platform/blob/main/device-models/regen.sh) runs
`render.py --write` (and `--write-views`), `export_gltf.py --model <slug> --write`,
`render_glb_views.py skins/<id>/model.glb`, then `check-skin-drift.py` and `export_gltf.py --check`,
and prints every file it changed. It needs OpenSCAD 2021.01, Python 3.11+, numpy and Pillow, and
refuses to start without them. The end of a run on an unedited a133 model looks like this:

```text
== 4/4 gates: check-skin-drift.py, export_gltf.py --check
skin_drift=pass models=2 devices=a133,a523 glbs=3 glb_devices=a133,a523,trimui-brick views=6
glb device=a133 triangles=67077 budget=150000
glb device=a523 triangles=81072 budget=150000
glb device=trimui-brick triangles=82626 budget=150000
glb_check=pass models=3
regen=pass slug=trimui-smart-pro skin=a133 changed=0 (outputs already current)
```

The first run on a fresh checkout takes a few minutes (OpenSCAD runs once per part; later runs
reuse `device-models/.cache/`). If you did **not** edit the `.scad` and a run still reports
changed files, check `git diff`: the 2D skin renders are not always byte-stable on every host
(fonts and GL rasterisation, bead `tsp-vevy`; on one host the a133 top-view PNGs changed on one
run and came back on the next). Commit only changes that follow from your edit.

Every derived file and the gate that refuses it stale (both jobs are in platform's
`.github/workflows/skin-drift.yml`):

| Derived file | Written by | Gate (CI job) |
|---|---|---|
| `skins/<id>/body.png`, `body_lit.png` (+ `body_<view>.png`, `body_lit_<view>.png`) | `render.py --write` / `--write-views` | `check-skin-drift.py` (`skin-drift`): hashes in `model-render.json` |
| `skins/<id>/model-render.json` rects + `display_rect` | `render.py --write` | `check-skin-drift.py` (`skin-drift`): equal to the descriptor `[skin.parts]` |
| `skins/<id>/model.glb` + `model-glb.json` | `export_gltf.py --write` | `check-skin-drift.py` (`skin-drift`): hashes, glb nodes == `CONTROL_IDS` == `[skin.parts]`; `export_gltf.py --check` + Khronos validator (`skin-drift-glb`) |
| `skins/<id>/views/{front,back,left,right,top,bottom}.png` | `render_glb_views.py` | `check-skin-drift.py` (`skin-drift`) and `--views-only` (`skin-drift-glb`): hashes in `model-glb.json["views"]`, recorded against the committed glb and `render_glb_views.py` |

The generated PNGs and the glb are **checked in** on purpose: the target app, the sim and pfvd
must not need OpenSCAD at runtime. Rendering expects the `Liberation Sans` and `Ubuntu Sans`
font families (the latter is the photographed extra-bold-italic enclosure silkscreen).

**A brand-new device bootstraps in two runs.** `export_gltf.py --write` refuses a model whose
`CONTROL_IDS` differ from the descriptor's `[skin.parts]` keys, so write the descriptor with those
keys first ([Step 4](#step-4--the-descriptor-devicesidcapabilitiestoml); any rect values). The
first `regen.sh` then renders, exports and stops at its last step, the drift gate, because the
descriptor's rects are not the rendered ones yet. With one rect off by 26 px on the a133:

```text
skin_drift=fail
  - a133: btn_east rect drift: descriptor={'x': 1300, 'y': 190, 'w': 57, 'h': 55} model-render.json={'h': 55, 'w': 57, 'x': 1326, 'y': 190}
```

Copy what `render.py --write` recorded in `model-render.json` into the descriptor: the control
rects (printed as `derived_rects=`) into `[skin.parts]`, and `display_rect` (printed as
`display_rect=`) into the primary screen's `display_rect` (`screens[0]`); for a skin with extra
views, each `view_rects[<view>]=` into `[skin.views.<view>.parts]`. Then run `regen.sh` again;
`render.py` never edits the descriptor. A **model-only** package (a `MODELS` row with descriptor
`None`, like the Brick) has no descriptor and no 2D skin: `regen.sh` skips `render.py`, exports the glb,
renders the six views and runs the gates in one run.

### Verify locally with `render.py --check`

```bash
python3 device-models/trimui-smart-pro-s/render.py --check
```

`--check` re-renders from source and **fails if any PNG, capability rectangle, atlas pixel or the
non-overlap invariant has drifted**. It is the **full-fidelity local companion** to the CI
skin-drift gate: run it whenever you touch a model or its skin.

> **Honesty caveat: `render.py --check` is host-jitter-limited today (bead `tsp-vevy`).** The
> full OpenSCAD re-render is **not** wired into CI and cannot be a byte-stable green-on-main
> check: the `.scad`'s "Ubuntu Sans" variable font is absent from Debian bookworm (different
> silkscreen pixels), the render suite can time out under headless software GL, and GPU-vs-`llvmpipe`
> anti-aliasing risks flaky reds. A flaky gate would poison the gate-trust this whole effort
> exists to build, so `--check` stays the **local** full-fidelity companion and the
> **render-free** `skin-drift` gate is what runs in CI. Run `--check` on a host with the right
> fonts and expect pixel jitter to be environmental, not real drift, until `tsp-vevy` closes
> that gap.

## Step 4 — the descriptor (`devices/<id>/capabilities.toml`)

The descriptor is the single source of truth. One file describes the device to the **app**, the
**simulator**, **pfvd** and the **CI test**; there is no per-device code anywhere else. The full
key reference is platform's
[`docs/CAPABILITIES-SCHEMA.md`](https://github.com/pocketforge-os/platform/blob/main/docs/CAPABILITIES-SCHEMA.md).
It names:

- **inputs**: each control's evdev `code`, its SDL binding, and its `skin_part` (the id that ties
  it to a rectangle in the rendered skin and to a node in the glb). Power, volume, home and FN
  rows carry `class = "system"`;
- **sensors / capabilities**: what hardware the device advertises, so the sim can prove graceful
  degradation (a row omitted ⇒ a typed `hardware-absent` / no-op, not a crash);
- **the skin block**: `[skin]` (`body` / `lit_body` PNG paths), `[skin.parts]` (one `{x,y,w,h}`
  rect per control) and `screens.display_rect`.

For a523 this is `devices/a523/capabilities.toml`. It has the **same shape** as a133's: the a133
vs a523 delta is **data, not code**. a523 carries an eighteenth semantic control (`btn_home`,
the Pro S guide/home button) and the L3/R3 thumbstick clicks the base unit lacks. Nothing in the
app, the suites or the workflows knows the difference; they read it from the descriptor.

### Schema v2: what pfvd needs

`schema_version = 2` unlocks the tables the 3D simulator binds to the glb. All are optional, but
the [rung](#step-6--the-package-report-and-the-rung) you can declare depends on them. From
`devices/a133/capabilities.toml`:

```toml
schema_version = 2

[maturity]
declared = "sim-ready"           # bounded by the derived rung (E_MATURITY_EXCEEDS)

[physical]
envelope_mm = { w = 188.35, h = 79.77, d = 17.0 }

[model]
glb    = "skins/a133/model.glb"
frame  = "pf-mm-v1"
naming = "pf-semantic-v1"
source = "device-models/trimui-smart-pro"

[[screens]]
role          = "primary"        # exactly one primary, and it is screens[0]
present       = "portrait"
rotation      = "cw90"
render_canvas = { w = 1280, h = 720 }
display_rect  = { x = 332, y = 108, w = 816, h = 443 }
node          = "screen_main"    # the glb's screen quad
panel_px      = { w = 720, h = 1280 }
fourcc        = "XR24"
```

An articulated device adds `[[joints]]` with `[[joints.postures]]`. The clamshell fixture's lid
(abridged from `tests/caps-fixtures/clamshell/devices/synth-clamshell/capabilities.toml`):

```toml
[[joints]]
id       = "lid"
kind     = "hinge"               # hinge | swivel (degrees) | slide (mm)
node     = "pivot_lid"           # the glb pivot node; its children move
axis     = [-1.0, 0.0, 0.0]
range    = [0.0, 180.0]
default  = 180.0
switches = [{ ev_type = "EV_SW", code = "SW_LID", source = "gpio-keys", active = [0.0, 10.0], hysteresis = 1.0 }]
[[joints.postures]]              # postures tile the range: [lo, hi), the last [lo, hi]
id               = "closed"
range            = [0.0, 10.0]
active_screens   = []
reachable_inputs = ["vol_up", "vol_down"]
```

Validate the descriptor (and, with no id, every descriptor plus the CI matrix and the catalog):

```bash
python3 core/caps.py validate a523
python3 core/caps.py --root tests/caps-fixtures/clamshell validate   # a fixture tree
```

```text
OK    synth-clamshell: capabilities valid
SKIP  ci-matrix: no ci-matrix.toml under --root …/tests/caps-fixtures/clamshell (fixture tree)
SKIP  catalog: no devices/catalog.toml under --root …/tests/caps-fixtures/clamshell (fixture tree)
```

**Reconcile the descriptor to real silicon.** The codes/`absinfo` a real unit advertises are
verified on hardware (the HIL drift lane: `evdev-dump → pf caps probe-diff → committed fixtures`).
Until a row is silicon-reconciled it stays *advisory* ([Step 5](#step-5--the-catalog-row-and-the-posture-row)).
The sim proves the descriptor is *internally* consistent and maps cleanly to SDL3; it does
**not** prove the real chip advertises those exact codes. That is the hardware gate's authority
(see the honesty contract).

## Step 5 — the catalog row and the posture row

### The catalog row (`devices/catalog.toml`)

One `[[devices]]` row per **retail product**, named the way people know it: marketing name plus
the maker's code name, with the platform id and the model package beside it. pfvd's device
manager lists this file. The a523 row:

```toml
[[devices]]
id           = "trimui-smart-pro-s"
manufacturer = "TrimUI"
name         = "Smart Pro S"
code_name    = "TG5050"                          # ^[A-Z0-9][A-Z0-9-]*$
platform_id  = "a523"                            # devices/<dir> with the descriptor; omit for model-only
package      = "device-models/trimui-smart-pro-s"
maturity     = "sim-ready"                       # declared; bounded by the derived rung
```

Build variants (`a133-open-7x`, …) are derived from `profile.toml [device].base` and never
listed. Check the catalog:

```bash
python3 core/caps.py catalog            # declared vs derived per row
python3 core/caps.py catalog validate
```

```text
id	manufacturer	name	code_name	platform_id	declared	derived
trimui-smart-pro	TrimUI	Smart Pro	TG5040	a133	sim-ready	sim-ready
trimui-smart-pro-s	TrimUI	Smart Pro S	TG5050	a523	sim-ready	sim-ready
trimui-brick	TrimUI	Brick	TG3040	-	model-only	model-only
powkiddy-x55	Powkiddy	X55	-	-	planned	model-only
OK    catalog: 4 product(s), declared rungs within derived
```

A row that declares more than its evidence supports is an error. With the package directory
missing, the fixture's row reads:

```text
ERROR catalog 'synth-clamshell': E_CATALOG_REF package 'device-models/synth-clamshell' is not a directory
ERROR catalog 'synth-clamshell': E_CATALOG_MATURITY declared 'sim-ready' exceeds the derived rung 'planned' (sim-ready = valid descriptor + model package; model-only = model package)
```

### The posture row (`ci-matrix.toml`)

[`platform/ci-matrix.toml`](https://github.com/pocketforge-os/platform/blob/main/ci-matrix.toml) is
**the fleet keystone**: the single source of truth for *which* devices the sim CI suites run and
*each device's posture*. All three gate workflows derive their device rows and posture from here
(via `pf caps matrix`), so **adding a device to CI is a data change here, never a workflow edit.**

Three postures:

| Posture | Meaning |
|---------|---------|
| `blocking` | a suite failure **blocks merge** (the required check goes red). Promotion to blocking is deliberate: it needs (a) the descriptor silicon-reconciled, (b) baselines current vs platform `main`, (c) N stable green runs. |
| `advisory` | a suite failure **reports but does not block**. This is the **default** for any device that ships a `capabilities.toml` and is *not* listed: a new descriptor **auto-joins as advisory with zero workflow edits** (D6). Listing it explicitly is optional but self-documenting. |
| `excluded` | deliberately **not run, not gated**, and this **must be explicit**. A device directory carrying only a `profile.toml` (a build-profile variant, no `capabilities.toml`) is a `pf caps matrix validate` **error** until explicitly excluded here, so a non-participating device is **never a silent skip**. |

The current matrix, as the worked example:

```toml
[devices]
a133       = "blocking"    # base unit — live rev-5.0 DUT on pf-node-01; the sole blocking key today
a523       = "advisory"    # Pro S — sim rows STAY (device-free, pure data), hardware descoped ⇒ advisory
a133-owned = "excluded"    # profile.toml only (build-profile variant) — explicitly not gated
sdm845     = "excluded"    # profile.toml only (porting target) — explicitly not gated
```

a523 runs **advisory**: the sim rows carry the multi-device "zero per-device code" proof (they are
pure data and device-free), but a523 *hardware* work is descoped (owner, 2026-07-25; infra-113
D2), so a suite failure reports without blocking. Promoting a523 to `blocking` later is a
one-line data change here, once it meets the (a)/(b)/(c) bar. Completeness is enforced:
`pf caps matrix validate` (folded into `pf caps validate --all`) errors if a descriptor'd device
is missing a considered posture, or a profile-only device is not explicitly excluded, so the
matrix can never silently drop a device.

## Step 6 — the package report and the rung

pfvd's package report runs 13 rules over a catalog row, its descriptor and its glb, each with
`pass`/`warn`/`fail`/`skip` and a `file:line`, and derives the rung the package has earned. It
needs no session, so run it straight against your platform checkout:

```bash
pfvd-cli device report --device trimui-smart-pro-s --platform <platform>   # a catalog id
pfvd-cli device report --device <platform>/devices/a523                    # or a directory
```

On the clamshell fixture (with the catalog row of [the walk](#walked-on-the-clamshell-fixture)):

```text
Maturity: sim-ready (declared sim-ready) · skins/synth-clamshell/model.glb · 18 nodes
Verdict: declared sim-ready <= derived sim-ready ✓ · 0 errors · 2 warnings · 10 passed · 1 skipped
✓ R_NODE_SET        glTF node set == descriptor control ids · 12 / 12 · unmapped nodes: 0 · devices/synth-clamshell/capabilities.toml:61
✓ R_JOINT_PIVOT     joint pivots exist · 1 joint: lid (hinge 0..180°) · devices/synth-clamshell/capabilities.toml:151
✓ R_POSTURE_TILING  postures tile joint ranges · lid: closed / half / open · devices/synth-clamshell/capabilities.toml:162
⚠ R_MODEL_SHA       model-glb.json sha256 matches model.glb · no model-glb.json · skins/synth-clamshell/model.glb
⚠ R_PHYSICAL        [physical] rows · envelope 150 x 75 x 22 mm · model 152.00 x 75.00 x 24.00 mm · devices/synth-clamshell/capabilities.toml:23
✓ R_MATURITY        declared maturity <= derived maturity · declared sim-ready <= derived sim-ready · devices/catalog.toml:4
```

(abridged; the two warnings are expected for the fixture: its glb is not an `export_gltf.py`
export, so it has no `model-glb.json`, and it records no mass). When a rule fails, the report
names the next rung's blocker. With `skin_part = "btn_guide"` deleted:

```text
Maturity: model-only (declared sim-ready) · skins/synth-clamshell/model.glb · 18 nodes
Verdict: declared sim-ready > derived model-only ✗ · 2 errors · 2 warnings · 8 passed · 1 skipped
Next rung needs: R_NODE_SET: input 'guide' has no skin_part
```

The command's exit status is 0 whether or not the report found errors. A script gates on the
JSON envelope instead:

```bash
pfvd-cli --json device report --device <id> --platform <platform> | jq -e '.result.ok'
```

### The rungs

| Rung | What it means | What earns it |
|---|---|---|
| `planned` | a catalog row and nothing pfvd can load | the row (the X55 today: no glb yet) |
| `model-only` | orbit and inspect the 3D model; Start is disabled | a package directory and a loadable `skins/<id>/model.glb` (the Brick) |
| `sim-ready` | runs in pfvd and the sim | a v2 descriptor with `[model]`, a node for every screen and a `skin_part` for every input except `class = "system"` rows, plus the package (a133, a523) |
| verified on hardware | the real unit agrees with the descriptor | **not a rung and not inferred from the package:** the hardware gate's evidence (the HIL probe-diff lane; a `blocking` posture). The catalog has no field for it yet, so pfvd shows "verified on hardware: not recorded in the catalog" |

Declare in `[maturity] declared` and in the catalog row's `maturity` the rung the report
derives, never more; both validators refuse a declaration above the evidence.

## Step 7 — the first pfvd run

pfvd reads any platform checkout by path, so a new device is visible in pfvd before anything is
pinned. See pfvd's README for installing it (the repository is private for now).

```bash
pfvd --platform <platform>                       # device manager over your devices/catalog.toml
pfvd --device <platform>/devices/<id>            # open one sim-ready device (hosts a session)
pfvd --device <platform>/skins/<id>/model.glb    # a model-only package: orbit and inspect
```

From a script, host the session headless and drive it with `pfvd-cli`:

```bash
pfvd-cli session new --device <platform>/devices/<id>
pfvd-cli input list
pfvd-cli input tap <control>
pfvd-cli log tail
pfvd-cli session close
```

On the clamshell fixture, on the in-memory recorder backend (`--input-backend record`, no
`/dev/uinput` needed):

```text
$ pfvd-cli input tap east
ok ev=BTN_EAST:1 ev=BTN_EAST:0
$ pfvd-cli input hat dpad 1 0
ok ev=ABS_HAT0X:1 ev=ABS_HAT0Y:0
$ pfvd-cli log tail
13:48:28.940 core  serve --device …/devices/synth-clamshell
13:48:32.465 cli   input tap east
13:48:32.468 cli   input hat dpad 1 0
```

Starting the device's app (`pfvd-cli run start`, Run ▸ Start) runs the honest-mock container: the
sim image at pfvd's `sim.pin`, which carries its **own** pinned platform. A new device therefore
reaches the app runtime only after (1) its platform PR merges, (2) sim's `PLATFORM_COMMIT`
(`Dockerfile`, `docker/platform.pin`) moves past it and (3) pfvd's `sim.pin` moves to that sim
commit; until then Start fails with "the image's platform ships its own device list". Start also
needs a kernel uinput node (see pfvd's README, "Host requirement: uinput").

## Walked on the clamshell fixture

Every step above, run against `tests/caps-fixtures/clamshell` in a scratch clone of platform
(`a86adf6`) with pfvd `8073483` built from source. `ROOT` is a copy of the fixture tree with one
scratch catalog row added (the committed fixture is deliberately never in a catalog).

| Step | Command | Result on the fixture |
|---|---|---|
| 1. Measure | — | not applicable: synthetic test data, not a device |
| 2. Model | — | not applicable: the fixture has no `.scad`; its PNGs and glb come from `gen_fixture.py` (two screens and a `pivot_lid` node, which `export_gltf.py` cannot export yet) |
| 3. Regenerate | `device-models/regen.sh synth-clamshell` | `regen.sh: refusing: unknown model slug 'synth-clamshell' (known: trimui-brick trimui-smart-pro trimui-smart-pro-s)`, exit 2. The fixture's equivalent gate: `python3 tests/caps-fixtures/clamshell/gen_fixture.py --check` → `OK    synthetic clamshell artefacts match gen_fixture.py` |
| 4. Descriptor | `python3 core/caps.py --root tests/caps-fixtures/clamshell validate` | `OK    synth-clamshell: capabilities valid` (matrix and catalog SKIP: fixture tree) |
| 5. Catalog row | `python3 core/caps.py --root "$ROOT" catalog` | `synth-clamshell … SYNTH1 synth-clamshell sim-ready sim-ready`; `validate` → `OK    catalog: 1 product(s), declared rungs within derived`. A first try with `code_name = "test"` failed: `devices[0].code_name: 'test' does not match /^[A-Z0-9][A-Z0-9-]*$/` |
| 5. Posture row | — | not applicable: a fixture tree has no `ci-matrix.toml`, and the fixture must never join the gate |
| 6. Report | `pfvd-cli device report --device synth-clamshell --platform "$ROOT"` | `declared sim-ready <= derived sim-ready ✓ · 0 errors · 2 warnings · 10 passed · 1 skipped` |
| 7. First run | `pfvd --device "$ROOT/devices/synth-clamshell" --input-backend record --frames 120 --screenshot clamshell.png` | `PFVD_SHELL_SCREENSHOT … distinct_colours=75 nonblank=true`; the Device panel reads `sim-ready · verified on hardware: not recorded in the catalog`, `1 joints · posture controls arrive in milestone 2` |
| 7. Script | `pfvd-cli session new --device "$ROOT/devices/synth-clamshell" --input-backend record`, then `input tap east` | `ok ev=BTN_EAST:1 ev=BTN_EAST:0` |
| 7. App | `pfvd-cli run start --print-cmd` | not runnable here: the recorder session has no kernel node (`err 1005 uinput not writable: this session runs on the in-memory recorder …`), and no sim image ships `synth-clamshell` |

## What auto-joins in CI

Once the artifacts above are committed to `platform`, the device joins **every** gate with no
further edits:

- **The `./sim check` matrix**: `./sim check` with no args reads `ci-matrix.toml` via
  `pf caps matrix` *inside the pinned image*, so the device list and posture are data-driven.
  `./sim check` and the `sim-gate` CI run make the identical decision (see [`CLI.md`](CLI.md)).
- **The three gate workflows**: `sim-gate` (sim), `hwprobe-smoke` (pf-hwprobe) and
  `sim-descriptor-gate` (platform) all derive their device rows from the same `ci-matrix.toml`.
  The new row runs advisory automatically; there is no workflow device list to hand-edit.
- **The `skin-drift` gates** (platform): [`check-skin-drift.py`](https://github.com/pocketforge-os/platform/blob/main/device-models/check-skin-drift.py)
  **auto-discovers** by globbing `skins/*/model-render.json`, so *committing a rendered skin is
  all it takes to enrol the device*. For each discovered skin it asserts the recorded source /
  renderer / `body` / `body_lit` sha256 still match the committed files, that its control rects
  and `display_rect` **equal** the descriptor's `[skin.parts]`, that every rendered skin has a
  `model-glb.json` whose glb, `.scad`, exporter and view hashes match, and that the glb's
  control nodes equal `CONTROL_IDS` and `[skin.parts]`. The `skin-drift-glb` job adds the pinned
  Khronos validator and `export_gltf.py --check`. Both are render-free (byte-stable, no
  OpenSCAD) so they run on every relevant PR. Their guarantees are a **strict subset** of
  `render.py --check`: they catch every "edit one artifact, forget to regenerate the rest"
  drift, but a *consistent* hand-edit of a rect in **both** `model-render.json` and
  `capabilities.toml` (without re-rendering) still agrees byte-wise and slips past them. That
  narrow, semi-adversarial case is why you run `render.py --check` locally (Step 3).
- **The descriptor and catalog checks** (platform `regression-suites`): `regression/caps/test_caps.py`
  and `test_caps_v2.py` validate every descriptor, the catalog's declared-vs-derived rungs and
  the clamshell fixture.
- **pfvd**: the device manager of any pfvd pointed at the checkout lists the new catalog row,
  and the package report rates it. pfvd's own CI and its bundled catalog follow `platform.pin`, a
  one-line pfvd PR.
- **The clickable 2D picker**: `./sim gui` shows a manufacturer › device picker composed from
  the descriptors and skins; the new device appears in it with no code change (`./sim gui` is
  deprecated once pfvd reaches parity).
- **The headless suites**: `check-control` / `check-sensor` / `check-skin` run against the new
  descriptor as one more matrix row. Same source, same binary; the device delta is data.

## The whole flow, end to end

```bash
# in a platform worktree/branch:
$EDITOR device-models/trimui-smart-pro-s/trimui-smart-pro-s.scad   # 1-2. semantic 3D model from measurements (or a shared chassis)
$EDITOR device-models/export_gltf.py                               # 2.   new package only: one MODELS row
$EDITOR devices/a523/capabilities.toml                             # 4.   descriptor: inputs, sensors, [skin.parts], v2 tables
device-models/regen.sh trimui-smart-pro-s                          # 3.   skin, glb, six views, then the drift gates (new device: copy rects, run again)
python3 device-models/trimui-smart-pro-s/render.py --check         #      full-fidelity local verify (host-jitter caveat above)
$EDITOR devices/catalog.toml                                       # 5.   catalog row
$EDITOR ci-matrix.toml                                             # 5.   posture row (or leave it to auto-join as advisory)
python3 core/caps.py validate                                      #      every descriptor + the CI matrix + the catalog
python3 device-models/check-skin-drift.py                          #      render-free drift check, exactly as CI runs it
pfvd-cli device report --device trimui-smart-pro-s --platform .    # 6.   the package report: declare the rung it derives
pfvd --device devices/a523                                         # 7.   the first pfvd run
# commit + PR to platform. Then, from a sim clone, prove the sim agrees:
./sim check a523                                                   #      run just the new row, hard
```

No application code. No test code. No workflow edit. **Add a device = add data.**

## Where each piece lives (cross-repo map)

| Artifact | Repo · path |
|----------|-------------|
| Measurement guide | handbook · [Model a handheld](https://pocketforge-os.github.io/handbook/hardware/model-handheld/) |
| Semantic 3D model | `platform/device-models/<slug>/<slug>.scad` + `render.py` + `README.md` |
| Exporter registry | `platform/device-models/export_gltf.py` (`MODELS`) |
| Rendered skin | `platform/skins/<id>/{body,body_lit}.png` + `model-render.json` |
| 3D model + review views | `platform/skins/<id>/model.glb` + `model-glb.json` + `views/*.png` |
| Regenerate after a `.scad` edit | `platform/device-models/regen.sh <slug>` |
| Descriptor | `platform/devices/<id>/capabilities.toml` (schema: `platform/docs/CAPABILITIES-SCHEMA.md`) |
| Catalog row | `platform/devices/catalog.toml` |
| Posture row | `platform/ci-matrix.toml` |
| Drift gate | `platform/device-models/check-skin-drift.py` → `.github/workflows/skin-drift.yml` (`skin-drift`, `skin-drift-glb`) |
| Descriptor gate | `platform/.github/workflows/sim-descriptor-gate.yml` |
| Package report, first run | `pfvd-cli device report`, `pfvd` (pfvd repo) |
| Sim suites + CLI | `sim/{control,sensor,skin}/` · `sim/sim` · `sim/.github/workflows/sim-gate.yml` |
| Platform pin of the sim image | `sim/Dockerfile` `PLATFORM_COMMIT` · `sim/docker/platform.pin` |
| hwprobe smoke gate | `pf-hwprobe/.github/workflows/hwprobe-smoke.yml` |

## References

- [`HONESTY-CONTRACT.md`](HONESTY-CONTRACT.md): what the sim does and does **not** prove.
- [`CLI.md`](CLI.md): the `./sim` command reference and the GUI display-path tradeoff.
- [`FB0-SIDECAR.md`](FB0-SIDECAR.md): how pfvd gets an app's pixels out of `./sim run-app`.
- [`platform/device-models/README.md`](https://github.com/pocketforge-os/platform/blob/main/device-models/README.md):
  the semantic-model authoring contract, `regen.sh`, the drift gates and fixture contracts.
- [`platform/docs/CAPABILITIES-SCHEMA.md`](https://github.com/pocketforge-os/platform/blob/main/docs/CAPABILITIES-SCHEMA.md):
  every descriptor and catalog key.
- The handbook chapter [Virtual device (pfvd)](https://pocketforge-os.github.io/handbook/virtual-device/):
  what pfvd is, the tiers and the control plane.
- infra-113 §5 / decisions D6 (data-driven matrix) and D9 (skin-drift): the governing plan.
