#!/usr/bin/env python3
"""check-control.py — tsp-an4.5: the HEADLESS CONTRACT, asserted (the CI-gate entrypoint).

This is the test E7 promotes to a CI gate (advisory -> blocking). It drives the ONE control
surface (control_surface.Device) over the descriptor x scenario matrix for BOTH a133 and a523
with ZERO per-device test code — the device differences fall out of the descriptor rows — and
asserts the headline contract plus the full input/capability matrix:

  HEADLINE (verbatim, auto-snapshot):
    dev.press("south");          assert dev.framebuffer_region("south").is_red()
    dev.press("ltrig");          assert dev.framebuffer_region("ltrig").is_red()  # digital L2
    dev.assert_capability_absent("imu")            # a133; a523 -> assert present

  MATRIX (generic over [[inputs]]):
    * every digital control: press -> its region lights, a DIFFERENT region stays dark;
      release -> clears. (Proves the id->code->event->decode->render binding end-to-end
      through real uinput->evdev->qemu-tsp, not faked host-side.) The TrimUI L2/R2 are DIGITAL
      buttons (tsp-5p1), so they ride this same path -> press lights trig_l/trig_r, release clears.
    * d-pad hat: deflect -> lit; centre -> dark.
    * analog stick: deflect past deadzone -> lit; centre -> dark.
    * analog trigger (latent, descriptor-driven): a kind=trigger row would sweep 0..1 as a slider;
      neither TrimUI model declares one (their L2/R2 are digital), so the sweep loop below is inert
      for a133/a523 but kept for a future analog-trigger variant — pure DATA, zero code change.
    * ABSENT controls (a133 home/l3/r3): typed hardware-absent, NEVER a crash.
    * pose / capability: set_pose works iff the descriptor has an IMU, else hardware-absent;
      privacy caps (location) are denied by the cooperative facade.

  PARITY: the IDENTICAL arm64 binary under qemu-tsp produces BYTE-IDENTICAL frames to the
  native x86 build (same software rasterizer) — the .2/.3/.4 evidence bar, carried forward.

Env (from run-control.sh): APP_X86, APP_ARM64, QEMU_TSP, ROOTFS, PLATFORM. Stdlib + the sim's
own modules only. Exit 0 = PASS.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "fb"))

from control_surface import Device, HardwareAbsent     # noqa: E402
from ppm2png import read_ppm, write_png                # noqa: E402


class Checker:
    def __init__(self, tag):
        self.tag = tag
        self.fails = []

    def chk(self, cond, msg):
        print(("  ok  " if cond else "FAIL  ") + msg)
        if not cond:
            self.fails.append(msg)
        return cond


def _other_part(dev, sp):
    for g in dev.groups:
        if g.skin_part != sp:
            return g.skin_part
    return sp


def headline(dev, c):
    """The bead's one-liner, run verbatim (auto-snapshot on region access)."""
    print(f"-- HEADLINE [{dev.device_id}] --")
    dev.press("south")
    c.chk(dev.framebuffer_region("south").is_red(), 'press("south") -> framebuffer_region("south").is_red()')
    dev.release("south")
    # L2/R2 are DIGITAL on the TrimUI models (tsp-5p1) -> press/release, NOT an analog set_axis slider.
    dev.press("ltrig")
    c.chk(dev.framebuffer_region("ltrig").is_red(), 'press("ltrig") -> framebuffer_region("ltrig").is_red() [digital L2]')
    dev.release("ltrig")
    if dev.broker.is_present("imu"):
        c.chk(dev.assert_capability_present("imu"), 'assert_capability_present("imu") [a523]')
    else:
        c.chk(dev.assert_capability_absent("imu"), 'assert_capability_absent("imu") [a133]')


def run_scenario(dev, c):
    """The descriptor-generic matrix. Returns the ordered list of named frames captured."""
    frames = []

    def snap(n):
        dev.snapshot(n)
        frames.append(n)

    # rest: nothing lit
    snap("rest")
    for g in dev.groups:
        if g.render == "trigger":
            c.chk(dev.slider(g.skin_part).fraction() < 0.02, f"rest: {g.skin_part} trigger empty")
        else:
            c.chk(not dev.framebuffer_region(g.skin_part).is_red(), f"rest: {g.skin_part} dark")

    inputs = dev.inputs()

    # digital controls (buttons + stick-clicks)
    for inp in [i for i in inputs if i["ev_type"] == "EV_KEY"]:
        iid, sp = inp["id"], inp["skin_part"]
        other = _other_part(dev, sp)
        dev.press(iid); snap(f"{iid}_press")
        c.chk(dev.framebuffer_region(sp).is_red(), f"{iid} -> {sp} lit on press")
        c.chk(not dev.framebuffer_region(other).is_red(), f"{iid} press leaves {other} dark (isolation)")
        dev.release(iid); snap(f"{iid}_release")
        c.chk(not dev.framebuffer_region(sp).is_red(), f"{iid} -> {sp} clears on release")

    # d-pad hat
    for inp in [i for i in inputs if i.get("kind") == "hat"]:
        iid, sp = inp["id"], inp["skin_part"]
        dev.move_hat(iid, 1, 0); snap(f"{iid}_deflect")
        c.chk(dev.framebuffer_region(sp).is_red(), f"{iid} hat deflect -> {sp} lit")
        dev.move_hat(iid, 0, 0); snap(f"{iid}_centre")
        c.chk(not dev.framebuffer_region(sp).is_red(), f"{iid} hat centre -> {sp} dark")

    # analog sticks
    for inp in [i for i in inputs if i.get("kind") == "stick"]:
        iid, sp = inp["id"], inp["skin_part"]
        dev.set_stick(iid, 1.0, 0.0); snap(f"{iid}_deflect")
        c.chk(dev.framebuffer_region(sp).is_red(), f"{iid} stick deflect -> {sp} lit")
        dev.set_stick(iid, 0.0, 0.0); snap(f"{iid}_centre")
        c.chk(not dev.framebuffer_region(sp).is_red(), f"{iid} stick centre -> {sp} dark")

    # analog triggers — monotonic sweep
    for inp in [i for i in inputs if i.get("kind") == "trigger"]:
        iid, sp = inp["id"], inp["skin_part"]
        fracs = []
        for v in (0.0, 0.25, 0.5, 0.75, 1.0):
            dev.set_axis(iid, v); snap(f"{iid}_{int(v*100):03d}")
            fr = dev.slider(sp).fraction()
            fracs.append(fr)
            c.chk(dev.slider(sp).at(v), f"{iid} slider at {v:.2f} (read {fr:.3f})")
        c.chk(all(b >= a - 0.01 for a, b in zip(fracs, fracs[1:])),
              f"{iid} sweep monotonic non-decreasing {['%.2f' % x for x in fracs]}")
        dev.set_axis(iid, 0.0)

    # absent controls -> typed hardware-absent, never a crash
    for iid in ("home", "l3", "r3"):
        if not dev.has_input(iid):
            try:
                dev.press(iid)
                c.chk(False, f"absent '{iid}': press should raise HardwareAbsent")
            except HardwareAbsent:
                c.chk(True, f"absent '{iid}': typed hardware-absent (no crash)")

    # pose / capability contract
    if dev.broker.is_present("imu"):
        pose = dev.set_pose(yaw=10.0, pitch=5.0, roll=0.0)
        c.chk(pose["yaw"] == 10.0 and pose["pitch"] == 5.0, "set_pose accepted + round-trips (imu present)")
        c.chk(dev.assert_capability_present("imu"), "imu capability present")
    else:
        try:
            dev.set_pose(yaw=10.0)
            c.chk(False, "set_pose should raise HardwareAbsent (no imu)")
        except HardwareAbsent:
            c.chk(True, "set_pose hardware-absent (no imu, typed no-op)")
        c.chk(dev.assert_capability_absent("imu"), "imu capability hardware-absent")
    c.chk(dev.assert_capability_denied("location"), "location capability denied (no GNSS / privacy facade)")

    return frames


def run_one(device_id, platform_dir, launcher, outdir, app_x86, app_arm64, qemu_tsp, rootfs):
    c = Checker(f"{device_id}/{launcher}")
    with Device(device_id, platform_dir, launcher=launcher, outdir=outdir,
                app_x86=app_x86, app_arm64=app_arm64, qemu_tsp=qemu_tsp, rootfs=rootfs) as dev:
        if launcher == "qemu":
            headline(dev, c)
        frames = run_scenario(dev, c)
    return c.fails, frames


KEY_FRAMES = ["rest", "south_press", "guide_press", "dpad_deflect", "ltrig_050", "ltrig_100"]


def main():
    platform_dir = os.environ.get("PLATFORM") or sys.exit("set PLATFORM")
    app_x86 = os.environ.get("APP_X86") or sys.exit("set APP_X86")
    app_arm64 = os.environ.get("APP_ARM64") or sys.exit("set APP_ARM64")
    qemu_tsp = os.environ.get("QEMU_TSP") or sys.exit("set QEMU_TSP")
    rootfs = os.environ.get("ROOTFS") or sys.exit("set ROOTFS")
    devices = sys.argv[1:] or ["a133", "a523"]
    base = os.path.join(HERE, "baseline")

    overall = 0
    for dev_id in devices:
        print(f"\n############## {dev_id} ##############")
        results = {}
        for launcher in ("native", "qemu"):
            outdir = os.path.join(base, dev_id, launcher)
            os.makedirs(outdir, exist_ok=True)
            print(f"\n==== {dev_id} / {launcher} ====")
            fails, frames = run_one(dev_id, platform_dir, launcher, outdir,
                                    app_x86, app_arm64, qemu_tsp, rootfs)
            results[launcher] = (fails, frames, outdir)
            if fails:
                overall = 1

        # native == qemu byte-identical parity (the .2/.3/.4 bar)
        print(f"\n==== {dev_id} / parity (native == qemu-tsp) ====")
        nf, nframes, nout = results["native"]
        qf, qframes, qout = results["qemu"]
        common = [f for f in nframes if f in qframes]
        mism = []
        for name in common:
            a = os.path.join(nout, "frames", f"{name}.ppm")
            b = os.path.join(qout, "frames", f"{name}.ppm")
            if not (os.path.isfile(a) and os.path.isfile(b)) or open(a, "rb").read() != open(b, "rb").read():
                mism.append(name)
        if mism:
            overall = 1
            print(f"FAIL  {len(mism)}/{len(common)} frames differ: {mism[:6]}")
        else:
            print(f"  ok  all {len(common)} frames byte-identical native==qemu-tsp")

        # commit-worthy PNG evidence from the qemu frames
        evdir = os.path.join(base, dev_id)
        for name in KEY_FRAMES:
            src = os.path.join(qout, "frames", f"{name}.ppm")
            if os.path.isfile(src):
                w, h, rgb = read_ppm(src)
                write_png(os.path.join(evdir, f"{name}.png"), w, h, rgb)

        ok = not (nf or qf or mism)
        print(f"\n{dev_id}: {'PASS' if ok else 'FAIL'} "
              f"(native {len(nf)} fail, qemu {len(qf)} fail, parity {len(mism)} mismatch)")

    print("\n" + ("ALL DEVICES PASS" if overall == 0 else "SOME DEVICE FAILED"), "(" + " ".join(devices) + ")")
    return overall


if __name__ == "__main__":
    sys.exit(main())
