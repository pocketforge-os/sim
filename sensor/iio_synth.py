#!/usr/bin/env python3
"""iio_synth.py — tsp-an4.7: synthesize a virtual IIO device PURELY from the descriptor.

The sensor analog of .3's `uinput_synth.Synth`: where .3 builds a uinput evdev node from each
``[[inputs]]`` row, this builds an **IIO sysfs tree** from the ``[[sensors]]`` imu row, so the
IDENTICAL arm64 app reads ``/sys/bus/iio/devices/iio:device0/in_accel_*_raw`` exactly as it would
the real qmi8658 — indistinguishable from hardware (SPIKE-confirmed readable byte-identical under
qemu-tsp+bwrap; plain sysfs read(), no ioctl). The host bind-mounts the tree at
``/sys/bus/iio/devices`` in the sandbox (qemu launcher) or points PF_IIO_ROOT at it (native).

ZERO per-device code: a523 has an imu row -> a node exists; a133 has NO ``[[sensors]]`` -> no node
-> the app's scan finds nothing -> typed hardware-absent (the honest omission, not a stub).

The IIO mount_matrix ABI: the driver reports raws in the CHIP frame and exposes the
``in_*_mount_matrix`` attribute; *userspace* applies it to reach the device frame (that is the
whole reason the attribute exists). So we write CHIP-frame raws + the descriptor matrix, and the
app re-applies it — proving the app genuinely CONSUMES the descriptor mount_matrix (read, never
hard-coded). a523's matrix is identity today (until SPIKE-0 measures the real mount); the
non-identity transform is unit-proved in check-sensor.

HONESTY: scales are MODELED from representative qmi8658 datasheet full-scales (accel +/-8 g,
gyro +/-2048 dps over int16) — they prove the raw<->SI units pipeline, NOT measured silicon
calibration/bias/noise (hardware-gated). units come from the descriptor.
"""
import math
import os

# Representative qmi8658 full-scales (datasheet selectable ranges), int16 sample. MODELED, not
# measured silicon — labeled in RESULTS/HONESTY. raw in [-32768, 32767]; SI = raw * scale.
_ACCEL_FS_G = 8.0
_GYRO_FS_DPS = 2048.0
ACCEL_SCALE = _ACCEL_FS_G * 9.80665 / 32768.0          # m/s^2 per LSB
GYRO_SCALE = (_GYRO_FS_DPS * math.pi / 180.0) / 32768.0  # rad/s per LSB
_I16_MIN, _I16_MAX = -32768, 32767


def imu_sensor(desc):
    """Return the descriptor's accel/gyro imu sensor row, or None (a133 -> omission)."""
    for s in desc.get("sensors", []):
        k = (s.get("kind") or "").lower()
        if "accel" in k or "gyro" in k:
            return s
    return None


def _fmt_matrix(m):
    """IIO in_*_mount_matrix attribute format: rows ';'-separated, entries ', '-separated."""
    return "; ".join(", ".join(str(int(v)) if float(v).is_integer() else repr(v) for v in row)
                     for row in m)


def _clamp16(x):
    return max(_I16_MIN, min(_I16_MAX, int(round(x))))


def _w(path, val):
    with open(path, "w") as f:
        f.write(val if isinstance(val, str) else str(val))


class IIOSynth:
    """Synthesize + drive a qmi8658-shaped IIO node under ``root`` from the descriptor + the
    single physical model. No node is created when the descriptor advertises no imu."""

    def __init__(self, desc, root):
        self.desc = desc
        self.root = root                       # host dir bound at /sys/bus/iio/devices
        self.sensor = imu_sensor(desc)
        self.name = (self.sensor or {}).get("iio_device", "")
        self.mount = (self.sensor or {}).get("mount_matrix") or [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        self.has_accel = bool(self.sensor and "accel" in (self.sensor["kind"] or "").lower())
        self.has_gyro = bool(self.sensor and "gyro" in (self.sensor["kind"] or "").lower())
        self.devdir = os.path.join(root, "iio:device0")

    def present(self):
        return self.sensor is not None

    def create(self):
        """Materialize the sysfs tree. No-op (empty root) when the descriptor has no imu."""
        os.makedirs(self.root, exist_ok=True)
        if not self.present():
            return self                         # a133: scan finds nothing -> hardware-absent
        os.makedirs(self.devdir, exist_ok=True)
        _w(os.path.join(self.devdir, "name"), self.name)
        if self.has_accel:
            _w(os.path.join(self.devdir, "in_accel_scale"), repr(ACCEL_SCALE))
            _w(os.path.join(self.devdir, "in_accel_mount_matrix"), _fmt_matrix(self.mount))
        if self.has_gyro:
            _w(os.path.join(self.devdir, "in_anglvel_scale"), repr(GYRO_SCALE))
            _w(os.path.join(self.devdir, "in_anglvel_mount_matrix"), _fmt_matrix(self.mount))
        self.update(None)                       # rest pose -> initial raws
        return self

    def update(self, model):
        """Write CHIP-frame raws from the physical model (device-frame accel/gyro). chip = M^T.dev."""
        if not self.present():
            return
        from physical_model import inverse_mount
        accel_dev = model.accel() if model else [0.0, 0.0, 9.80665]
        gyro_dev = model.gyro() if model else [0.0, 0.0, 0.0]
        if self.has_accel:
            chip = inverse_mount(self.mount, accel_dev)
            for ax, v in zip("xyz", chip):
                _w(os.path.join(self.devdir, f"in_accel_{ax}_raw"), _clamp16(v / ACCEL_SCALE))
        if self.has_gyro:
            chip = inverse_mount(self.mount, gyro_dev)
            for ax, v in zip("xyz", chip):
                _w(os.path.join(self.devdir, f"in_anglvel_{ax}_raw"), _clamp16(v / GYRO_SCALE))

    def destroy(self):
        import shutil
        try:
            shutil.rmtree(self.root)
        except OSError:
            pass
