# `control/` — injection-as-API control surface (tsp-an4.5)

The **ONE control surface** for the Virtual Device Simulator, and the **headless contract that
becomes the CI gate**. This is where `.3`'s descriptor→uinput resolver and `.4`'s virtual
framebuffer converge into a real, off-hardware *device* you can drive from code.

**Injection-as-API-FIRST** (briefing §C.3/§C.4): there is one API, and the GUI skin (C6) and the
headless CI harness (E7) are **co-equal clients** of it — the GUI is built *on* this API, never
the API bolted onto a GUI.

## The headline contract (real, on both descriptors, zero per-device code)

```python
from control_surface import Device
with Device("a133", PLATFORM, launcher="qemu") as dev:        # uinput + virtual fb0, qemu-tsp app, NO crun
    dev.press("south");           assert dev.framebuffer_region("south").is_red()
    dev.set_axis("ltrig", 0.5);   assert dev.slider("ltrig").at(0.5)
    dev.assert_capability_absent("imu")                       # a133 has no IMU
```

`press`/`release`/`set_axis`/`set_stick`/`move_hat`/`set_pose`/`set_capability` are the single
injection surface. Digital press → `EV_KEY 1/0`; analog drag → `EV_ABS` scaled across the
descriptor `min..max`; hat → `ABS_HAT0X/Y`. `framebuffer_region(id)` / `slider(id)` /
`assert_capability_*` are the assertion side E7 drives.

## How it stays honest — ONE descriptor, two consumers

`framebuffer_region("south").is_red()` is only meaningful if the **app** draws control `south`
where the **test** samples. They agree because both derive the rect from
[`layout.py`](layout.py), which fits the descriptor's `[skin.parts]` rects (the AVD clickable-skin
table the GUI will also click on) into `screens[0].render_canvas`. The host writes a `layout.txt`
the app draws from, and the host's own region asserts read the *same* `compute_layout` output —
**one computation, no hand-typed coordinates, no drift** (the discipline that caught the platform
`KEY_HOMEPAGE` bug). a133 vs a523 differ *only* by descriptor rows.

## Pieces

| file | role |
|------|------|
| [`control_surface.py`](control_surface.py) | the ONE API: `Device(...)`, injection primitives, `snapshot`/`framebuffer_region`/`slider`, broker-routed `set_pose`/`set_capability`. Wraps `.3`'s `Synth`. |
| [`layout.py`](layout.py) | descriptor → canvas layout (skin.parts fit transform); the shared app+test source of truth. |
| [`broker_stub.py`](broker_stub.py) | thin in-process E2 broker stub: capability presence derived from descriptor sensor/actuator rows; typed `HardwareAbsent` / cooperative `PermissionDenied`. |
| [`hwprobe-lite.c`](hwprobe-lite.c) | the IDENTICAL arm64 app: reads the synth uinput nodes, lights the pressed control onto a memfd virtual fb (`.4` tsp-osr-safe software-render), FIFO snapshot handshake. |
| [`check-control.py`](check-control.py) | **the CI-gate entrypoint** — drives the surface over the descriptor×scenario matrix, asserts headline + full input/capability matrix, checks native==qemu byte-identical parity. |
| [`run-control.sh`](run-control.sh) | end-to-end on modelmaker: compile the app (x86+arm64), run the suite under sudo. |

## Run (on modelmaker)

```bash
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 \
SDLR=/home/mm/sim-build/sdl3-render \
ROOTFS=/home/mm/sim-build/harness/rootfs-arm64 \
PLATFORM=/home/mm/platform \
./control/run-control.sh
# => ALL DEVICES PASS (a133 a523)
```

The app reads the synth uinput **under qemu-tsp inside bubblewrap** (NO crun), so the evdev/uinput
ioctl translation is exercised for real. The native x86 build runs the same scenario for the
byte-identical-frame parity check. Evidence (key-frame PNGs + `RESULTS.md`) lands in
[`baseline/`](baseline/); the per-launcher working dirs (PPMs, FIFOs, logs) are gitignored.

## CI gate (advisory → blocking)

`check-control.py` exits non-zero on any failed assertion or parity mismatch — it is the gate E7
wires into CI, **advisory first**, flipped to **blocking** once a133+a523 are both green and
stable (epic OWNER DECISION 3). It shares the descriptors with the build pipeline (one source of
truth across build, sim, test) and needs `qemu-tsp` on the runner (stock qemu-user will not work).

## Honesty (epic contract — see [`../docs/HONESTY-CONTRACT.md`](../docs/HONESTY-CONTRACT.md))

This proves the **logical layer**: descriptor correctness, input mapping (id→code→event→render),
the capability/permission **contract**, and graceful missing-hardware degradation — cheaply,
off-hardware, on every PR. It does **NOT** prove (and says so): GPU blobs / the
PowerVR→dc_sunxi→fb0 path, real WiFi flakiness, timing/perf/thermal, **enforcement** (qemu-user
stubs guest seccomp; the broker stub is cooperative, not enforced), or per-SoC graphics. Those
five stay the flash→serial→webcam hardware gate's sole authority. The frames here prove the
input→render *binding*, not the on-device render path.
