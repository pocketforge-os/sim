#!/usr/bin/env python3
"""check-sensor.py — tsp-an4.7: the HEADLESS SENSOR/POSE CONTRACT, asserted (CI-gate extension).

Sibling to .5's check-control.py: same ONE control surface (control_surface.Device), same
descriptor x launcher matrix, ZERO per-device test code — now proving the SENSOR/POSE + actuator
capability contract end-to-end through the IDENTICAL arm64 app:

  APP-CONSUMES-THE-INJECTION (the load-bearing .7 loop):
    * a523: set_pose(orientation) -> the app reads the injected accel off the synthesized virtual
      IIO device (/sys/bus/iio/devices, qemu-bound) and recovers the SAME device-frame vector
      (scale + descriptor mount_matrix applied IN THE APP). Gyro likewise from angular velocity.
    * yaw-invariance: rotating about the vertical does NOT change the gravity reading.
    * native == qemu-tsp BYTE-IDENTICAL app replies (the .2/.3/.4/.5 parity bar, for sensors).
    * mount_matrix is LIVE: re-inject with a synthetic 90-degree axis-swap mount -> the app
      permutes the axes accordingly (proves it is not dead code masked by a523's identity matrix).

  ONE MODEL, TWO CLIENTS:
    * a GUI tilt-bubble drag (set_pose_from_drag) and the test's direct set_pose drive the SAME
      broker -> the app reads IDENTICAL values. (Headless; rendering the widget is the separate,
      owner-visual-gated piece, deferred while the owner is away.)

  HONEST MISSING-HARDWARE + a11y (ONE no-op shape):
    * a133: read_imu -> hardware-absent (app reports imu-absent, NO crash); set_pose raises.
    * rumble: a523 pulse() -> fired; hapticsEnabled=False -> noop-suppressed; a133 -> noop-absent.
      "absent motor" and "accessibility-suppressed motor" are the SAME typed no-op handle (E4).

  CONSENT/PERMISSION CONTRACT:
    * assert_capability_denied("location") passes off-hardware (privacy default-deny, cooperative
      facade — contract not enforcement; honesty item 4).

Env (from run-sensor.sh): APP_X86, APP_ARM64, QEMU_TSP, ROOTFS, PLATFORM. Exit 0 = PASS.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "control"))
sys.path.insert(0, HERE)

from control_surface import Device, HardwareAbsent          # noqa: E402
from broker_stub import (RUMBLE_FIRED, RUMBLE_NOOP_ABSENT,   # noqa: E402
                         RUMBLE_NOOP_SUPPRESSED)

ACCEL_TOL = 0.02     # m/s^2 (quantization: 0.5 LSB ~ 0.0012, + milli rounding)
GYRO_TOL = 0.01      # rad/s

# the injected pose scenario (orientation in degrees; angular velocity in deg/s). label -> kwargs.
POSES = [
    ("rest",         {}),
    ("pitch+30",     {"pitch": 30.0}),
    ("roll-20",      {"roll": -20.0}),
    ("pitch20roll15", {"pitch": 20.0, "roll": 15.0}),
    ("yaw90",        {"yaw": 90.0, "pitch": 20.0}),     # yaw must NOT change accel vs pitch-only
    ("spin",         {"wx": 50.0, "wy": -30.0, "wz": 10.0}),
]
SWAP_XZ = [[0, 0, 1], [0, 1, 0], [1, 0, 0]]   # synthetic mount: prove the app applies the matrix


class Checker:
    def __init__(self, tag):
        self.tag = tag
        self.fails = []

    def chk(self, cond, msg):
        print(("  ok  " if cond else "FAIL  ") + msg)
        if not cond:
            self.fails.append(f"[{self.tag}] {msg}")
        return cond


def _close(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def run_imu(dev, c, launcher):
    """Drive the pose scenario; return {label: app_reply_string} for the parity compare."""
    replies = {}
    for label, kw in POSES:
        base = dict(yaw=0.0, pitch=0.0, roll=0.0, wx=0.0, wy=0.0, wz=0.0)  # absolute pose each step
        base.update(kw)
        dev.set_pose(**base)
        got = dev.read_imu()
        c.chk(got is not None, f"imu[{label}]: app reports a reading (not absent)")
        if got is None:
            continue
        replies[label] = got["raw_reply"]
        exp_a = dev.broker.model.accel()
        exp_g = dev.broker.model.gyro()
        c.chk(_close(got["accel"], exp_a, ACCEL_TOL),
              f"imu[{label}]: app accel {[round(v,3) for v in got['accel']]} == injected "
              f"{[round(v,3) for v in exp_a]}")
        c.chk(_close(got["gyro"], exp_g, GYRO_TOL),
              f"imu[{label}]: app gyro {[round(v,4) for v in got['gyro']]} == injected "
              f"{[round(v,4) for v in exp_g]}")
    # yaw-invariance: yaw90+pitch20 accel == pitch+30? no — compare yaw90(pitch20) vs pitch20-only.
    dev.set_pose(yaw=0.0, pitch=20.0, roll=0.0, wx=0.0, wy=0.0, wz=0.0)
    a_noyaw = dev.read_imu()["accel"]
    dev.set_pose(yaw=90.0, pitch=20.0, roll=0.0)
    a_yaw = dev.read_imu()["accel"]
    c.chk(_close(a_noyaw, a_yaw, ACCEL_TOL),
          f"yaw-invariance: accel unchanged by yaw ({[round(v,3) for v in a_noyaw]})")
    return replies


def run_mount_matrix(dev, c):
    """Prove the app APPLIES the descriptor mount_matrix: re-inject with a synthetic 90-deg axis
    swap; the app must permute the axes (else identity would mask dead code)."""
    # baseline identity at a distinctive pose (all three axes distinct)
    dev.iio.mount = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    dev.iio.create()
    dev.set_pose(pitch=25.0, roll=18.0)
    ident = dev.read_imu()["accel"]
    # synthetic swap-XZ mount: chip = M^T.device written by the synth; app recovers M.chip = device
    dev.iio.mount = SWAP_XZ
    dev.iio.create()
    dev.set_pose(pitch=25.0, roll=18.0)
    swapped = dev.read_imu()["accel"]
    exp = dev.broker.model.accel()
    c.chk(_close(swapped, exp, ACCEL_TOL),
          f"mount_matrix LIVE: app recovers device frame {[round(v,3) for v in swapped]} == "
          f"injected {[round(v,3) for v in exp]} under swap-XZ mount")
    # and the swap is non-vacuous (x,z meaningfully differ so identity-ignoring would have failed)
    c.chk(abs(ident[0] - ident[2]) > 0.5,
          f"mount_matrix test non-vacuous (ident accel x={ident[0]:.3f} z={ident[2]:.3f} differ)")
    dev.iio.mount = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    dev.iio.create()


def run_two_clients(dev, c):
    """One model, two clients: a GUI tilt-bubble drag and an equivalent direct set_pose drive the
    SAME broker -> the app reads IDENTICAL values."""
    dx, dy = 0.4, -0.6
    dev.set_pose_from_drag(dx, dy)
    via_drag = dev.read_imu()["raw_reply"]
    # the equivalent direct pose (pose_from_drag: pitch = dy*45, roll = dx*45)
    dev.set_pose(yaw=0.0, pitch=dy * 45.0, roll=dx * 45.0, wx=0.0, wy=0.0, wz=0.0)
    via_test = dev.read_imu()["raw_reply"]
    c.chk(via_drag == via_test,
          f"GUI drag == headless inject: identical app read ({via_drag!r})")


def run_actuators_and_consent(dev, c, has_imu, has_rumble):
    """Launcher-independent broker contract: rumble/haptics ONE no-op shape; location denied."""
    # rumble / haptics unified no-op shape (E4)
    r = dev.acquire_rumble()
    if has_rumble:
        c.chk(r.pulse(40) == RUMBLE_FIRED, "rumble: present + haptics-on -> fired")
        dev.set_preference("hapticsEnabled", False)
        c.chk(dev.acquire_rumble().pulse(40) == RUMBLE_NOOP_SUPPRESSED,
              "rumble: hapticsEnabled=False -> noop-suppressed (a11y, same shape as absent)")
        dev.set_preference("hapticsEnabled", True)
    else:
        c.chk(r.pulse(40) == RUMBLE_NOOP_ABSENT, "rumble: absent motor -> noop-absent (no crash)")
        # absence and suppression share the handle shape: still no crash with haptics toggled
        dev.set_preference("hapticsEnabled", False)
        c.chk(dev.acquire_rumble().pulse(40) == RUMBLE_NOOP_ABSENT,
              "rumble: absent stays noop-absent regardless of preference (one no-op shape)")
        dev.set_preference("hapticsEnabled", True)
    # pose hardware-absent on a133
    if not has_imu:
        try:
            dev.set_pose(pitch=10.0)
            c.chk(False, "set_pose should raise HardwareAbsent (no imu)")
        except HardwareAbsent:
            c.chk(True, "set_pose hardware-absent (no imu, typed no-op)")
    # consent / privacy default-deny
    c.chk(dev.assert_capability_denied("location"),
          "location denied off-hardware (privacy default-deny, cooperative facade)")


def run_one(device_id, platform_dir, launcher, outdir, app_x86, app_arm64, qemu_tsp, rootfs):
    c = Checker(f"{device_id}/{launcher}")
    replies = {}
    with Device(device_id, platform_dir, launcher=launcher, outdir=outdir,
                app_x86=app_x86, app_arm64=app_arm64, qemu_tsp=qemu_tsp, rootfs=rootfs) as dev:
        has_imu = dev.broker.is_present("imu")
        has_rumble = dev.broker.is_present("rumble")
        if has_imu:
            replies = run_imu(dev, c, launcher)
            run_mount_matrix(dev, c)
            run_two_clients(dev, c)
        else:
            got = dev.read_imu()
            c.chk(got is None, "a133: read_imu -> hardware-absent (app reports absent, no crash)")
        run_actuators_and_consent(dev, c, has_imu, has_rumble)
    return c.fails, replies


def main():
    platform_dir = os.environ.get("PLATFORM") or sys.exit("set PLATFORM")
    app_x86 = os.environ.get("APP_X86") or sys.exit("set APP_X86")
    app_arm64 = os.environ.get("APP_ARM64") or sys.exit("set APP_ARM64")
    qemu_tsp = os.environ.get("QEMU_TSP") or sys.exit("set QEMU_TSP")
    rootfs = os.environ.get("ROOTFS") or sys.exit("set ROOTFS")
    devices = sys.argv[1:] or ["a133", "a523"]
    base = os.path.join(HERE, "baseline")

    overall = 0
    evidence = {}
    for dev_id in devices:
        print(f"\n############## {dev_id} ##############")
        results = {}
        for launcher in ("native", "qemu"):
            outdir = os.path.join(base, dev_id, launcher)
            os.makedirs(outdir, exist_ok=True)
            print(f"\n==== {dev_id} / {launcher} ====")
            fails, replies = run_one(dev_id, platform_dir, launcher, outdir,
                                     app_x86, app_arm64, qemu_tsp, rootfs)
            results[launcher] = (fails, replies)
            if fails:
                overall = 1

        # native == qemu byte-identical app replies (the parity bar, for sensors)
        print(f"\n==== {dev_id} / parity (native == qemu-tsp) ====")
        nf, nrep = results["native"]
        qf, qrep = results["qemu"]
        common = [k for k in nrep if k in qrep]
        mism = [k for k in common if nrep[k] != qrep[k]]
        if mism:
            overall = 1
            print(f"FAIL  {len(mism)}/{len(common)} imu replies differ: {mism}")
        else:
            print(f"  ok  all {len(common)} imu replies byte-identical native==qemu-tsp")
        evidence[dev_id] = {"native_replies": nrep, "qemu_replies": qrep,
                            "parity_mismatch": mism}
        ok = not (nf or qf or mism)
        print(f"\n{dev_id}: {'PASS' if ok else 'FAIL'} "
              f"(native {len(nf)} fail, qemu {len(qf)} fail, parity {len(mism)} mismatch)")

    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "sensor-evidence.json"), "w") as f:
        json.dump(evidence, f, indent=2, sort_keys=True)
    print("\n" + ("ALL DEVICES PASS" if overall == 0 else "SOME DEVICE FAILED"),
          "(" + " ".join(devices) + ")")
    return overall


if __name__ == "__main__":
    sys.exit(main())
