# `synth/` — uinput synthesis FROM the device descriptor (tsp-an4.3)

The data-driven generalization of `spike3/mkuinput.c`. SPIKE-3 hand-coded ONE virtual
gamepad to prove a `uinput` device is indistinguishable from hardware to SDL3 under
`qemu-tsp`. This stage drives that mechanism **purely from the E1 capability descriptor**
(`platform/devices/<id>/capabilities.toml`), so a new device is a **data change, zero sim
code**: a133 and a523 differ only by descriptor rows.

> ONE descriptor → app + simulator + test. The synthesized device IS the descriptor's
> expectation made into a kernel-visible node.

## What it does

For every `[[inputs]]` row it registers the row's `(ev_type, code)` + per-axis `absinfo`
(`min/max/fuzz/flat`) on a `uinput` device. **Missing hardware is handled by OMISSION** — the
a133 base is the a523 set MINUS the `home`/`l3`/`r3` rows (and the sensors + rumble). Never a
fabricated row.

**Node grouping (honest topology, derived — not per-device coded):**
- Gamepad codes (`BTN_*` / `ABS_*`) → the `045e:028e` **"TRIMUI Player1"** pad node (the
  SPIKE-3 device). Its bus/vendor/product/version come from `identity.sdl_guid` (the
  authoritative SDL identity), cross-checked against `identity.match`.
- System keys (`KEY_*`, e.g. a523's Home = `KEY_HOMEPAGE`) → a **separate** generic node.
  This matches caps.py's own model (`emit-sdldb` excludes `KEY_*` from the gamepad mapping;
  `probe-diff` resolves `KEY_*` against *any* node) and the a523 descriptor's note that Home
  is "a system key, NOT the gamepad's guide".

So **a133 → one node, a523 → two nodes** — another facet of the omission proof.

## Files

| file | role |
|---|---|
| `evdev_codes.py` | **generated** name→value table (kernel ABI), for exactly the schema vocab |
| `gen_evdev_codes.py` | regenerate / `--check` `evdev_codes.py` from `input-event-codes.h` + caps.py vocab |
| `uinput_synth.py` | descriptor → uinput device(s); the **id→code resolver + `press`/`release`/`set_axis`/`move_hat`** API C5 builds on; `plan`/`create` CLI |
| `probe_evdev.py` | sim-owned EVIOCG* dumper (E1 capture shape), decoded via `evdev_codes.py` |
| `check-synth.py` | per-device assertions: round-trip EXACT, omission/matrix, probe-diff, SDL, native==qemu |
| `run-synth.sh` | end-to-end matrix runner on a host with `/dev/uinput` + `qemu-tsp` + SDL3 |

The `evdev_codes.py` table is **generated, never hand-typed** — kernel ABI numbers are
sourced from `/usr/include/linux/input-event-codes.h` (+ `input.h` for `BUS_*`), valued for
exactly the vocabulary `core/caps.py` validates against. `run-synth.sh` runs
`gen_evdev_codes.py --check` first, so a kernel-header drift or a caps.py vocab change fails
the build instead of silently mis-encoding a code. (This caught a real platform bug — see
below.)

## Run (modelmaker, x86, has `/dev/uinput`)

```bash
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 \
SDLDIR=/home/mm/sim-build/sdl3 \
PLATFORM=/home/mm/platform \
  ./synth/run-synth.sh            # a133 + a523; exit 0 = PASS, artifacts in synth/baseline/<id>/
```

Device-free authoring loop (no `/dev/uinput`): `uinput_synth.py plan --device a523
--platform <dir>` prints the derived spec + resolver as JSON.

## Resolver API (the C5 foundation)

```python
import uinput_synth as us
desc = us.load_descriptor(platform_dir, "a523")
synth = us.Synth(desc).create()           # creates the node(s); needs /dev/uinput (root)
synth.press("south"); synth.release("south")
synth.set_axis("ltrig", 0.5)              # 0..1 normalized across the descriptor range
synth.move_hat("dpad", 1, 0)
synth.destroy()
```

`press(id)` → resolve `id` → descriptor code → `uinput` write. C5 (the injection-as-API
control surface) wraps these primitives behind one IPC/GUI surface; this stage exposes them.

## Honesty (see `../docs/HONESTY-CONTRACT.md`)

This advertises the descriptor's evdev **input** codes + `absinfo` — the
`EVIOCGBIT`/`EVIOCGABS` probe surface SDL3/libevdev read at `open()`. It does **NOT** model:
- **LED arrays** (`[[actuators]]` `led_array`) — sysfs led-class, a broker capability (C7).
- **Force-feedback PLAYBACK** — FF upload is out of `qemu-tsp` scope; the a523 rumble
  actuator is C7's broker path. (`EV_FF` advertisement is intentionally not synthesized here;
  `[[actuators]]` are C7, only `[[inputs]]` are this stage.)
- **Sensors** (IMU/IIO) — broker, single physical model (C7).
- GPU / timing / WiFi / isolation — the flash→serial→webcam hardware gate's sole authority.

## Platform bug found while extending to a523

`platform/regression/caps/evdev-probe.py`'s reverse `KEY` table maps `0x172` (370) →
`KEY_HOMEPAGE`, but the kernel value is **172 = 0xac** (`#define KEY_HOMEPAGE 172`). It is a
decimal/hex slip that would mislabel the real Home key during E1 SPIKE-0 on a523 hardware and
break a523 `probe-diff`. The sim sidesteps it by owning `probe_evdev.py` (decode via the
generated table); the platform fix is filed separately. This is exactly the failure mode the
"never hand-type ABI values" rule prevents.
