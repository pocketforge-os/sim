# tsp-an4.7 — sensor/pose + broker-backed capability injection — RESULTS

Measured on **modelmaker** (x86 Threadripper) via `sensor/run-sensor.sh`
(`QEMU_TSP=/home/mm/qemu-tsp/... SDLR=/home/mm/sim-build/sdl3-render
ROOTFS=/home/mm/sim-build/harness/rootfs-arm64 PLATFORM=/home/mm/platform`).
The IDENTICAL arm64 `hwprobe-lite` binary runs under **qemu-tsp + bubblewrap (NO crun)** and the
native x86 build runs for the parity check. Descriptors are read from `platform` (a523 has the
`qmi8658` imu + pwm-vibrator rumble; a133 has NEITHER — omission, not a stub).

## Verdict: **ALL DEVICES PASS (a133 a523)** — native 0 fail, qemu 0 fail, parity 0 mismatch.

## The load-bearing loop the wall closes — APP CONSUMES THE INJECTION
`set_pose(...)` drives a SINGLE physical model (`sensor/physical_model.py`); the control surface
writes the derived accel/gyro into a **descriptor-synthesized virtual IIO device**
(`sensor/iio_synth.py`) bind-mounted at the honest ABI path `/sys/bus/iio/devices`; the app reads
`iio:device0/in_accel_*_raw` + `in_anglvel_*_raw` + `*_scale` + `*_mount_matrix` and recovers the
DEVICE frame (`device = M · raw·scale`). The host asserts the app's recovered vector == the
injected one. Mechanism pinned by a SPIKE (plain sysfs `read()`, no ioctl — strictly more robust
than the .3 input path; qemu-tsp's evdev fork isn't even exercised for sensors).

### a523 — app-reported IMU (milli-SI: mm/s², mrad/s), native == qemu-tsp BYTE-IDENTICAL
| pose (injected)        | app reply (`imu qmi8658 ax ay az  gx gy gz`) |
|------------------------|-----------------------------------------------|
| rest                   | `0 0 9807    0 0 0`        (gravity on +Z = +9.807 m/s²) |
| pitch +30°             | `0 4903 8492    0 0 0`     |
| roll −20°              | `3354 0 9215    0 0 0`     |
| pitch 20° + roll 15°   | `-2538 3239 8902    0 0 0` |
| yaw 90° (+pitch 20°)   | `0 3354 9215    0 0 0`     (== pitch-20°-only: **yaw-invariant**) |
| spin (ω=50,−30,10 °/s) | `0 0 9807    873 -524 175` (gyro = 0.873/−0.524/0.175 rad/s) |

Accel round-trip error ≤ ~0.0012 m/s² (≤ 0.5 LSB quantization @ ±8 g / int16); gyro ≤ ~0.0005 rad/s.

### Proven invariants
- **a523 reads injected pose** (single physical model → app under qemu-tsp), accel + gyro.
- **yaw-invariance** — rotation about the vertical does NOT change the gravity reading.
- **mount_matrix is LIVE** — re-injecting with a synthetic 90° axis-swap (`swap-XZ`) mount makes
  the app permute the axes; it recovers the device frame `[-3.031, 3.941, 8.454]` (== injected),
  and the test is non-vacuous (a matrix-ignoring reader would have read the swapped chip frame).
  a523's real descriptor matrix is identity (read, applied end-to-end) until SPIKE-0 measures the
  real mount.
- **ONE model, TWO clients** — a GUI tilt-bubble drag (`set_pose_from_drag`) and the test's direct
  `set_pose` drive the SAME broker → the app reads an IDENTICAL reply
  (`imu qmi8658 -3031 -4235 8310 0 0 0`). (Headless; rendering the widget is the separate,
  owner-visual-gated piece — deferred while the owner is away.)
- **native == qemu-tsp byte-identical** — a523: all 6 imu replies identical (`-ffp-contract=off`
  forbids FMA divergence). a133: 0 replies (absent).

### Honest missing-hardware + accessibility (ONE no-op shape)
- **a133**: `read_imu` → hardware-absent (app reports `imu-absent`, NO crash); `set_pose` raises
  `HardwareAbsent`.
- **rumble/haptics** unified no-op handle (never raises): a523 + haptics-on → `fired`; a523 +
  `hapticsEnabled=False` → `noop-suppressed` (E4 accessibility); a133 → `noop-absent`. "absent
  motor" and "accessibility-suppressed motor" are the SAME typed no-op shape.

### Consent / permission contract
- `assert_capability_denied("location")` passes off-hardware on both devices (privacy default-deny,
  cooperative facade — **contract, not enforcement**; honesty item 4).

## Regression — shared infra unchanged for .5/.6
`control/run-control.sh` re-run after the shared edits (`broker_stub.py`, `control_surface.py`,
`hwprobe-lite.c`, `harness/run-in-harness.sh`): **ALL DEVICES PASS**, a523 41/41 frames
byte-identical native==qemu-tsp, `set_pose` still round-trips, location denied.

## HONESTY (what this does NOT prove — stays the flash→serial→webcam hardware gate's authority)
- NOT real qmi8658 silicon/calibration/bias/noise/sample-timing. Scales are MODELED from
  representative datasheet full-scales (accel ±8 g, gyro ±2048 dps over int16) — they prove the
  raw↔SI units pipeline, not measured calibration. The DT-but-unbound R3 hazard (a523 sensors)
  stays the hardware gate's authority.
- NOT enforcement — the v0 facade is an in-process COOPERATIVE library (R-A); under qemu-user
  guest seccomp is unenforceable. `assert_capability_denied` tests a cooperative facade returning
  a value, not confinement of a hostile app.
- NOT real rumble actuation (the SIM never buzzes silicon), GPU/WiFi/timing/thermal.

## Reproduce
```
ssh mm@10.0.40.90
cd /home/mm/sim/sensor
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 SDLR=/home/mm/sim-build/sdl3-render \
ROOTFS=/home/mm/sim-build/harness/rootfs-arm64 PLATFORM=/home/mm/platform bash run-sensor.sh
```
