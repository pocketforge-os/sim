# sim/sensor — tsp-an4.7: sensor/pose model + broker-backed capability injection

The LAST E5 core wall. Models the device's **sensors + pose as a SINGLE physical model**
(yaw/pitch/roll/position + angular velocity, AVD `setPhysicalModel` style) routed through the
**capability broker/facade — NOT raw evdev** — and proves the capability/permission CONTRACT
off-hardware against the IDENTICAL arm64 app. A **client of the .5 control surface**: the SAME
`set_pose(...)` a GUI tilt-bubble drag drives, the headless test drives.

## The app-consumes-the-injection mechanism (SPIKE-pinned: virtual IIO device)
The v0 capability facade is an **in-process library** that reads sensors via **IIO sysfs directly**
(infra-101: "direct evdev/**IIO**/FF/sysfs"; R-A locks v0 in-process — there is no out-of-process
broker until Phase-2, so a "broker IPC" path would invent a protocol E2 hasn't landed). So the sim
synthesizes a **virtual IIO device** — the sensor analog of .3's synthesized uinput:

- `physical_model.py` — the ONE rigid-body state (orientation + position + angular velocity) →
  derived **accel** (gravity reaction projected into the device frame) + **gyro** (angular
  velocity). Deterministic, no wall-clock (reproducible + byte-identical parity). Also the
  `pose_from_drag` GUI-gesture mapping (one model, two clients).
- `iio_synth.py` — synthesizes `/sys/bus/iio/devices/iio:device0/{name,in_accel_*_raw,
  in_accel_scale,in_accel_mount_matrix,in_anglvel_*}` PURELY from the descriptor `[[sensors]]` row.
  Writes CHIP-frame raws (`chip = Mᵀ·device`) so the app re-applies the descriptor `mount_matrix`
  to recover the device frame — proving it genuinely CONSUMES the matrix (read, never hard-coded).
  No `[[sensors]]` → no node → the app's scan finds nothing → hardware-absent (a133 omission).
- `control/hwprobe-lite.c` (the IDENTICAL app) gains an `imu <name>` FIFO command: scan
  `$PF_IIO_ROOT` (default `/sys/bus/iio/devices`), read raws + scale + mount_matrix, apply the
  matrix, report DEVICE-frame milli-SI. Plain sysfs `read()` — no ioctl.
- `control/broker_stub.py` grows `set_pose` to drive the physical model + the unified rumble/
  haptics **no-op shape** (`acquire_rumble().pulse()` → `fired`/`noop-absent`/`noop-suppressed`,
  never raises) + accessibility preferences (`hapticsEnabled`, E4).
- `control/control_surface.py` wires the IIO synth into `boot()` (qemu: bind at
  `/sys/bus/iio/devices`; native: `PF_IIO_ROOT`), `set_pose` → IIO update, `read_imu()`,
  `set_pose_from_drag()`, rumble/preference passthrough.
- `harness/run-in-harness.sh` gains the conditional `IIO_BIND` bind (the .4/.5 `OUT_BIND` pattern).

## Run (modelmaker)
```
cd /home/mm/sim/sensor
QEMU_TSP=/home/mm/qemu-tsp/build/qemu-tsp/qemu-aarch64 SDLR=/home/mm/sim-build/sdl3-render \
ROOTFS=/home/mm/sim-build/harness/rootfs-arm64 PLATFORM=/home/mm/platform bash run-sensor.sh
```
`check-sensor.py` is the CI-gate extension (sibling to `control/check-control.py`). Exit 0 = PASS
over the descriptor × launcher matrix, ZERO per-device test code. Results + the honesty contract:
[baseline/RESULTS.md](baseline/RESULTS.md).

## What this is NOT honest about
Real qmi8658 silicon/calibration/noise/timing, enforcement (cooperative facade; qemu-user can't
enforce seccomp), real rumble actuation, GPU/WiFi/thermal — those stay the flash→serial→webcam
hardware gate's SOLE authority (epic HONESTY CONTRACT).
