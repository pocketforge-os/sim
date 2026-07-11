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


def _row_for(dev, iid):
    """Return the [[inputs]] row for ``iid`` in the loaded descriptor, or ``None``."""
    for r in dev.inputs():
        if r.get("id") == iid:
            return r
    return None


def _press_or_drive(dev, iid, on):
    """HEADLINE-safe 'assert this control is now ACTIVE / IDLE' primitive that respects
    the descriptor row's ``ev_type``/``kind`` — because L2/R2 have flipped between
    digital (BTN_TL2/TR2) and binary-on-analog (ABS_Z/RZ, ``semantics="binary"``) as
    the E1 descriptors were silicon-reconciled (tsp-9sx.1). Old ``dev.press("ltrig")``
    HARDCODED the digital path and now raises ``ValueError('ltrig' is not a button)``
    on today's descriptors. The MATRIX loop below already dispatches on ev_type/kind
    correctly — this helper lifts the same dispatch into the HEADLINE.

    ``on=True`` = drive to active; ``on=False`` = release to idle."""
    row = _row_for(dev, iid)
    if row is None:
        # Fall back to the digital path so we still surface a HardwareAbsent for
        # ids the descriptor omits (e.g. 'home' on a133).
        (dev.press if on else dev.release)(iid); return "digital"
    et = row.get("ev_type")
    kind = row.get("kind")
    if et == "EV_KEY":
        (dev.press if on else dev.release)(iid); return "digital"
    if kind == "trigger":
        dev.set_axis(iid, 1.0 if on else 0.0, normalized=True); return "trigger"
    if kind == "stick":
        # Deflect on X; return to centre on release.
        dev.set_stick(iid, 1.0 if on else 0.0, 0.0, normalized=True); return "stick"
    if kind == "hat":
        dev.move_hat(iid, 1 if on else 0, 0); return "hat"
    # Unrecognized shape: try the digital path last (best-effort, will raise).
    (dev.press if on else dev.release)(iid); return f"fallback({et}/{kind})"


def headline(dev, c):
    """The bead's one-liner, run verbatim (auto-snapshot on region access).

    Descriptor-dispatched (tsp-fr2n.7): each headline row picks digital
    press/release vs analog set_axis/set_stick/move_hat based on the [[inputs]]
    row's ev_type/kind, so the HEADLINE tracks descriptor evolution instead of
    baking in one wire shape per control."""
    print(f"-- HEADLINE [{dev.device_id}] --")
    _press_or_drive(dev, "south", on=True)
    c.chk(dev.framebuffer_region("south").is_red(),
          'press("south") -> framebuffer_region("south").is_red()')
    _press_or_drive(dev, "south", on=False)
    # L2/R2 shape is descriptor-driven — was digital (BTN_TL2/TR2) on the older
    # pins, is binary-on-analog (ABS_Z/RZ semantics=binary) on the silicon-
    # reconciled pins (tsp-9sx.1). _press_or_drive() picks the right wire.
    shape = _press_or_drive(dev, "ltrig", on=True)
    c.chk(dev.framebuffer_region("ltrig").is_red(),
          f'drive("ltrig", active) -> framebuffer_region("ltrig").is_red() [{shape}]')
    _press_or_drive(dev, "ltrig", on=False)
    if dev.broker.is_present("imu"):
        c.chk(dev.assert_capability_present("imu"), 'assert_capability_present("imu") [imu-present]')
    else:
        c.chk(dev.assert_capability_absent("imu"), 'assert_capability_absent("imu") [imu-absent]')


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


def _parse_argv(argv):
    """Backward-compatible arg parse (C7 / tsp-fr2n.7).

    Accepted forms (all additive; the original ``check-control.py a133 a523`` shape still works):

      * ``--only-launcher {native,qemu}`` — drive ONE launcher only. Skips the parity check
        (which needs both). Used by pf-hwprobe's ``ci/check-control-hwprobe.py`` wrapper,
        which drives ``pf-hwprobe.arm64`` under qemu-tsp only until the x86 build lands.
      * ``--outdir-base DIR`` — override the frames/PPM/PNG evidence root (defaults to
        ``sim/control/baseline``). Lets a caller keep evidence out of the sim tree.
      * ``--label TAG`` — a free-form tag stamped in the header lines so the transcript
        makes the caller (e.g. "pf-hwprobe") obvious in mixed CI logs.
      * Remaining positional args = device ids (default: ``a133 a523``).
    """
    only = None
    outbase = os.path.join(HERE, "baseline")
    label = None
    devices = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--only-launcher":
            only = argv[i + 1]; i += 2
            if only not in ("native", "qemu"):
                sys.exit(f"--only-launcher must be native|qemu (got {only!r})")
        elif a == "--outdir-base":
            outbase = argv[i + 1]; i += 2
        elif a == "--label":
            label = argv[i + 1]; i += 2
        elif a in ("-h", "--help"):
            print(__doc__ or "")
            print("usage: check-control.py [--only-launcher {native,qemu}] "
                  "[--outdir-base DIR] [--label TAG] [devices...]")
            sys.exit(0)
        elif a.startswith("--"):
            sys.exit(f"unknown flag {a!r}; try --help")
        else:
            devices.append(a); i += 1
    if not devices:
        devices = ["a133", "a523"]
    return only, outbase, label, devices


def main():
    platform_dir = os.environ.get("PLATFORM") or sys.exit("set PLATFORM")
    app_arm64 = os.environ.get("APP_ARM64") or sys.exit("set APP_ARM64")
    qemu_tsp = os.environ.get("QEMU_TSP") or sys.exit("set QEMU_TSP")
    rootfs = os.environ.get("ROOTFS") or sys.exit("set ROOTFS")
    only, outbase, label, devices = _parse_argv(sys.argv[1:])
    # APP_X86 is only required when the native launcher runs. --only-launcher qemu
    # lets a caller (pf-hwprobe C7) drive arm64-only until an x86 build lands.
    launchers = ("native", "qemu") if only is None else (only,)
    app_x86 = os.environ.get("APP_X86")
    if "native" in launchers and not app_x86:
        sys.exit("set APP_X86 (or pass --only-launcher qemu)")

    label_tag = f" [{label}]" if label else ""
    overall = 0
    for dev_id in devices:
        print(f"\n############## {dev_id}{label_tag} ##############")
        results = {}
        for launcher in launchers:
            outdir = os.path.join(outbase, dev_id, launcher)
            os.makedirs(outdir, exist_ok=True)
            print(f"\n==== {dev_id} / {launcher}{label_tag} ====")
            fails, frames = run_one(dev_id, platform_dir, launcher, outdir,
                                    app_x86, app_arm64, qemu_tsp, rootfs)
            results[launcher] = (fails, frames, outdir)
            if fails:
                overall = 1

        # native == qemu byte-identical parity — only when we ran BOTH launchers.
        mism = []
        if "native" in results and "qemu" in results:
            print(f"\n==== {dev_id} / parity (native == qemu-tsp){label_tag} ====")
            nf, nframes, nout = results["native"]
            qf, qframes, qout = results["qemu"]
            common = [f for f in nframes if f in qframes]
            for name in common:
                a = os.path.join(nout, "frames", f"{name}.ppm")
                b = os.path.join(qout, "frames", f"{name}.ppm")
                if not (os.path.isfile(a) and os.path.isfile(b)) \
                        or open(a, "rb").read() != open(b, "rb").read():
                    mism.append(name)
            if mism:
                overall = 1
                print(f"FAIL  {len(mism)}/{len(common)} frames differ: {mism[:6]}")
            else:
                print(f"  ok  all {len(common)} frames byte-identical native==qemu-tsp")
        else:
            (skip_launcher,) = tuple(x for x in ("native", "qemu") if x not in results)
            print(f"\n==== {dev_id} / parity SKIPPED (--only-launcher {only}) — "
                  f"'{skip_launcher}' leg not run ====")

        # commit-worthy PNG evidence from whichever launcher we ran (prefer qemu).
        evdir = os.path.join(outbase, dev_id)
        src_launcher = "qemu" if "qemu" in results else next(iter(results))
        _, _, src_out = results[src_launcher]
        for name in KEY_FRAMES:
            src = os.path.join(src_out, "frames", f"{name}.ppm")
            if os.path.isfile(src):
                w, h, rgb = read_ppm(src)
                write_png(os.path.join(evdir, f"{name}.png"), w, h, rgb)

        parts = [f"{k} {len(v[0])} fail" for k, v in results.items()]
        if "native" in results and "qemu" in results:
            parts.append(f"parity {len(mism)} mismatch")
        ok = all(not v[0] for v in results.values()) and not mism
        print(f"\n{dev_id}: {'PASS' if ok else 'FAIL'} ({', '.join(parts)})")

    print("\n" + ("ALL DEVICES PASS" if overall == 0 else "SOME DEVICE FAILED"),
          "(" + " ".join(devices) + ")" + label_tag)
    return overall


if __name__ == "__main__":
    sys.exit(main())
