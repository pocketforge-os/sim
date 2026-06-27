#!/usr/bin/env python3
"""check-skin.py — tsp-an4.6: the clickable skin drives the SAME control surface as the headless
inject — proven on the IDENTICAL arm64 app under qemu-tsp, for BOTH descriptors, ZERO per-device
test code. The .6 wall's headless contract (a sibling of .5's check-control.py).

Three things are asserted, all flowing through skin_model (one descriptor -> app + sim + test +
SKIN):

  A. GUI-CLICK == HEADLESS-INJECT (the load-bearing invariant). For every control, take its
     [skin.parts] rect, compute a click/drag PIXEL, and feed it through skin_model's hit-test +
     gesture resolver. The resolved control_surface Action MUST EQUAL the action .5's headless
     test injects directly (press / move_hat / set_stick / set_axis). Then APPLY it and assert
     the correct control lit via the .5 canvas-space readback (framebuffer_region / slider).
     => a GUI click is the same injection-as-API call, not a second path.

  B. COMPOSITOR GEOMETRY. Render the composited bezel (skin-render --shot, no picker) with the
     live fb snapshot, and sample the bezel at skin_model.map_canvas_point(centre of the lit
     control's canvas rect). It must be lit — proving the live fb landed inside display_rect with
     the DATA-driven composite rotation. (Routine self-check via deterministic sampling, NOT a
     VLM — tsp-visual-inspection's hallucination caveat.)

  C. PER-VARIANT / ZERO-CODE. The picker lists both variants from [identity]; a133 sticks are
     NON-clickable (tap -> no action) while a523's are (l3/r3) — a pure-DATA difference; absent
     controls raise typed hardware-absent; analog drags scale. a133 vs a523 differ only by rows.

Owner artifacts: full AVD shots (bezel + lit control + live fb + picker) per device, baseline/<id>/.

Env (from run-skin.sh): APP_X86, APP_ARM64, QEMU_TSP, ROOTFS, PLATFORM, SKIN_RENDER. Run under
sudo (uinput). Exit 0 = PASS.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "control"))
sys.path.insert(0, os.path.join(HERE, "..", "fb"))

from control_surface import Device, HardwareAbsent          # noqa: E402
from ppm2png import read_ppm, read_png, write_ppm, write_png  # noqa: E402
import skin_model as SM                                      # noqa: E402

SKIN_RENDER = os.environ.get("SKIN_RENDER")


class Checker:
    def __init__(self, tag):
        self.tag, self.fails = tag, []

    def chk(self, cond, msg):
        print(("  ok  " if cond else "FAIL  ") + msg)
        if not cond:
            self.fails.append(msg)
        return cond


def _center(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def _avg(rgb, w, h, cx, cy, rad=3):
    cx, cy = int(round(cx)), int(round(cy))
    rs = gs = bs = n = 0
    for yy in range(max(0, cy - rad), min(h, cy + rad + 1)):
        for xx in range(max(0, cx - rad), min(w, cx + rad + 1)):
            o = (yy * w + xx) * 3
            rs += rgb[o]; gs += rgb[o + 1]; bs += rgb[o + 2]; n += 1
    n = max(1, n)
    return rs // n, gs // n, bs // n


def _is_red(c):
    r, g, b = c
    return r >= 150 and g <= 100 and b <= 100


def _png_to_ppm(png_path, ppm_path):
    w, h, rgb = read_png(png_path)
    write_ppm(ppm_path, w, h, rgb)
    return ppm_path


def _canvas_group(dev, skin_part):
    for g in dev.groups:
        if g.skin_part == skin_part:
            return g
    return None


def _render_shot(skin, dev, body_ppm, lit_ppm, fb_ppm, lit_parts, out_ppm,
                 picker=None, selected=None, title="", hat_dirs=None):
    scene = skin.emit_scene(body_ppm, lit_ppm, fb_ppm, lit_parts,
                            picker=picker, selected=selected, title=title, hat_dirs=hat_dirs)
    scene_path = out_ppm + ".scene"
    with open(scene_path, "w") as f:
        f.write(scene)
    subprocess.run([SKIN_RENDER, "--scene", scene_path, "--shot", out_ppm],
                   check=True, stderr=subprocess.DEVNULL)
    return out_ppm


def run_device(device_id, platform_dir, launcher, outdir, apps, do_render):
    c = Checker(f"{device_id}/{launcher}")
    skin = SM.Skin(device_id, platform_dir)

    # C. picker lists both variants from [identity] (device-free, asserted once on qemu)
    if launcher == "qemu":
        picker = SM.build_picker(platform_dir)
        names = [it["device_id"] for items in picker.values() for it in items]
        c.chk(device_id in names and len(names) >= 2,
              f"picker lists {names} (>=2 variants, incl {device_id}) from [identity]")

    # bezel PPMs for the renderer (decoded from PNG with the stdlib reader — no PIL on mm)
    body_ppm = lit_ppm = None
    if do_render:
        body_ppm = _png_to_ppm(skin.body_path, os.path.join(outdir, "body.ppm"))
        lit_ppm = _png_to_ppm(skin.lit_body_path, os.path.join(outdir, "lit_body.ppm"))

    with Device(device_id, platform_dir, launcher=launcher, outdir=outdir,
                app_x86=apps[0], app_arm64=apps[1], qemu_tsp=apps[2], rootfs=apps[3]) as dev:
        inputs = dev.inputs()
        dev.snapshot("rest")

        def light_check(sp, lit, tag):
            r = dev.framebuffer_region(sp)
            c.chk(r.is_red() == lit, f"{tag}: {sp} {'lit' if lit else 'dark'} (canvas readback)")

        def compositor_check(sp, frame_name, tag):
            if not do_render:
                return
            g = _canvas_group(dev, sp)
            if g is None:
                return
            cx, cy = _center(g.canvas)                     # lit widget centre in CANVAS space
            bx, by = skin.map_canvas_point(cx, cy)         # -> bezel (skin) space, no picker -> ==PPM
            fb_ppm = os.path.join(outdir, "frames", f"{frame_name}.ppm")
            shot = os.path.join(outdir, f"shot_{frame_name}.ppm")
            _render_shot(skin, dev, body_ppm, lit_ppm, fb_ppm, {sp}, shot)
            w, h, rgb = read_ppm(shot)
            col = _avg(rgb, w, h, bx, by)
            inside = (skin.display_rect[0] <= bx < skin.display_rect[0] + skin.display_rect[2] and
                      skin.display_rect[1] <= by < skin.display_rect[1] + skin.display_rect[3])
            c.chk(inside and _is_red(col),
                  f"{tag}: fb-lit '{sp}' composites into display_rect at ({bx:.0f},{by:.0f}) "
                  f"col={col} (rot={skin.composite_rotation()})")

        # ---- A + B over the descriptor-generic input matrix ----
        for inp in inputs:
            iid, kind, sp = inp["id"], inp.get("kind"), inp["skin_part"]
            part = skin.parts[sp]
            rx, ry, rw, rh = part.rect
            cx, cy = _center(part.rect)

            if inp["ev_type"] == "EV_KEY" and kind in ("button", "stick-click"):
                gui = skin.tap(cx, cy)
                exp = [SM.Action("press", iid), SM.Action("release", iid)]
                c.chk(gui == exp, f"TAP {sp} -> {[a.as_tuple() for a in gui]} == inject press/release({iid})")
                gui[0].apply(dev); dev.snapshot(f"{iid}_press")
                light_check(sp, True, f"{iid} press")
                compositor_check(sp, f"{iid}_press", f"{iid} press")
                gui[1].apply(dev); dev.snapshot(f"{iid}_release")
                light_check(sp, False, f"{iid} release")

            elif kind == "hat":
                gx, gy = rx + int(rw * 0.85), ry + rh // 2          # click RIGHT -> +x
                gui = skin.tap(gx, gy)
                exp = [SM.Action("move_hat", iid, 1, 0), SM.Action("move_hat", iid, 0, 0)]
                c.chk(gui == exp, f"TAP {sp}(right) -> {[a.as_tuple() for a in gui]} == inject move_hat({iid},1,0)/centre")
                gui[0].apply(dev); dev.snapshot(f"{iid}_deflect")
                light_check(sp, True, f"{iid} hat deflect")
                compositor_check(sp, f"{iid}_deflect", f"{iid} hat")
                gui[1].apply(dev); dev.snapshot(f"{iid}_centre")
                light_check(sp, False, f"{iid} hat centre")

            elif kind == "stick":
                gui = skin.drag(cx, cy, rx + rw, ry + rh // 2)       # full RIGHT deflection
                exp = [SM.Action("set_stick", iid, 1.0, 0.0)]
                c.chk(gui == exp, f"DRAG {sp}->edge -> {[a.as_tuple() for a in gui]} == inject set_stick({iid},1,0)")
                gui[0].apply(dev); dev.snapshot(f"{iid}_deflect")
                light_check(sp, True, f"{iid} stick deflect")
                compositor_check(sp, f"{iid}_deflect", f"{iid} stick")
                back = skin.drag(cx, cy, cx, cy)                     # to centre
                c.chk(back == [SM.Action("set_stick", iid, 0.0, 0.0)],
                      f"DRAG {sp}->centre == inject set_stick({iid},0,0)")
                back[0].apply(dev); dev.snapshot(f"{iid}_centre")
                light_check(sp, False, f"{iid} stick centre")

            elif kind == "trigger":
                fracs = []
                for v in (0.0, 0.25, 0.5, 0.75, 1.0):
                    to_x = rx + v * rw
                    gui = skin.drag(cx, cy, to_x, cy)
                    c.chk(gui == [SM.Action("set_axis", iid, round(v, 4))],
                          f"DRAG {sp} slider->{v:.2f} -> {[a.as_tuple() for a in gui]} == inject set_axis({iid},{v})")
                    gui[0].apply(dev); dev.snapshot(f"{iid}_{int(v*100):03d}")
                    fr = dev.slider(sp).fraction(); fracs.append(fr)
                    c.chk(dev.slider(sp).at(v), f"{iid} slider at {v:.2f} (read {fr:.3f})")
                c.chk(all(b >= a - 0.01 for a, b in zip(fracs, fracs[1:])),
                      f"{iid} sweep monotonic {['%.2f' % x for x in fracs]}")
                if do_render:
                    compositor_check(sp, f"{iid}_100", f"{iid} trigger@1.0")
                dev.set_axis(iid, 0.0)

        # C. a133 sticks NON-clickable (tap -> no action); a523 -> l3/r3 (already covered above)
        for sp in ("stick_l", "stick_r"):
            p = skin.parts.get(sp)
            if p and p.stick_click is None:
                ccx, ccy = _center(p.rect)
                c.chk(skin.tap(ccx, ccy) == [],
                      f"a133 {sp} tap -> [] (non-clickable: no stick-click row, pure DATA)")

        # C. absent controls -> typed hardware-absent (never a crash)
        for iid in ("home", "l3", "r3"):
            if not dev.has_input(iid):
                try:
                    dev.press(iid); c.chk(False, f"absent '{iid}': press should raise")
                except HardwareAbsent:
                    c.chk(True, f"absent '{iid}': typed hardware-absent (no crash)")

        # ---- owner AVD gallery (full composition with picker), qemu only ----
        # Drives explicit states to showcase the directional D-pad + the stick calibration box
        # (position vector + the a523-only pressed state). Frame names are prefixed "avd_" so
        # they don't collide with the parity-checked matrix frames (qemu-only -> not in parity).
        if do_render and launcher == "qemu":
            picker = SM.build_picker(platform_dir)

            def reset_all():
                for j in inputs:
                    jid, jk = j["id"], j.get("kind")
                    try:
                        if j["ev_type"] == "EV_KEY":
                            dev.release(jid)
                        elif jk == "hat":
                            dev.move_hat(jid, 0, 0)
                        elif jk == "stick":
                            dev.set_stick(jid, 0.0, 0.0)
                        elif jk == "trigger":
                            dev.set_axis(jid, 0.0)
                    except HardwareAbsent:
                        pass

            # (name, setup, lit_parts, label, hat_dir) — hat_dir feeds the bezel directional light
            gallery = [
                ("rest",       lambda: None,                              set(),         "rest", (0, 0)),
                ("dpad_up",    lambda: dev.move_hat("dpad", 0, -1),       {"dpad"},      "D-pad UP", (0, -1)),
                ("dpad_down",  lambda: dev.move_hat("dpad", 0, 1),        {"dpad"},      "D-pad DOWN", (0, 1)),
                ("dpad_left",  lambda: dev.move_hat("dpad", -1, 0),       {"dpad"},      "D-pad LEFT", (-1, 0)),
                ("dpad_right", lambda: dev.move_hat("dpad", 1, 0),        {"dpad"},      "D-pad RIGHT", (1, 0)),
                ("lstick_diag", lambda: dev.set_stick("lstick", 0.7, -0.7), {"stick_l"}, "L-stick up-right", (0, 0)),
                ("south_press", lambda: dev.press("south"),              {"btn_south"}, "A pressed", (0, 0)),
                ("ltrig_press", lambda: dev.press("ltrig"),              {"trig_l"},    "L2 pressed (digital)", (0, 0)),
            ]
            if dev.has_input("l3"):   # a523 only: the stick PRESSED (L3) — a133 omits the row
                gallery.append(("l3_press", lambda: dev.press("l3"), {"stick_l"},
                                "L3 stick pressed (5050 only)", (0, 0)))

            evdir = os.path.join(HERE, "baseline", device_id)
            os.makedirs(evdir, exist_ok=True)
            shots = []
            for name, setup, lit_parts, label, hat_dir in gallery:
                reset_all()
                setup()
                snap = f"avd_{name}"
                dev.snapshot(snap)
                fb_ppm = os.path.join(outdir, "frames", f"{snap}.ppm")
                out_ppm = os.path.join(outdir, f"{snap}.ppm")
                _render_shot(skin, dev, body_ppm, lit_ppm, fb_ppm, lit_parts, out_ppm,
                             picker=picker, selected=device_id, title=f"{device_id}  {label}",
                             hat_dirs={"dpad": hat_dir})
                w, h, rgb = read_ppm(out_ppm)
                write_png(os.path.join(evdir, f"avd_{name}.png"), w, h, rgb)
                shots.append(name)
            reset_all()
            c.chk(len(shots) >= 6, f"owner AVD gallery rendered: {shots}")

    return c.fails


def main():
    platform_dir = os.environ.get("PLATFORM") or sys.exit("set PLATFORM")
    apps = (os.environ.get("APP_X86"), os.environ.get("APP_ARM64"),
            os.environ.get("QEMU_TSP"), os.environ.get("ROOTFS"))
    for i, k in enumerate(("APP_X86", "APP_ARM64", "QEMU_TSP", "ROOTFS")):
        if not apps[i]:
            sys.exit(f"set {k}")
    if not SKIN_RENDER:
        sys.exit("set SKIN_RENDER (path to the built skin-render binary)")
    devices = sys.argv[1:] or ["a133", "a523"]
    base = os.path.join(HERE, "baseline")

    overall = 0
    for dev_id in devices:
        print(f"\n############## {dev_id} ##############")
        res = {}
        for launcher in ("native", "qemu"):
            outdir = os.path.join(base, dev_id, launcher)
            os.makedirs(os.path.join(outdir, "frames"), exist_ok=True)
            print(f"\n==== {dev_id} / {launcher} ====")
            fails = run_device(dev_id, platform_dir, launcher, outdir, apps,
                               do_render=(launcher == "qemu"))
            res[launcher] = (fails, outdir)
            if fails:
                overall = 1

        # native == qemu byte-identical parity on the shared input frames (.2/.3/.4/.5 bar)
        print(f"\n==== {dev_id} / parity (native == qemu-tsp) ====")
        nout, qout = res["native"][1], res["qemu"][1]
        nframes = {f for f in os.listdir(os.path.join(nout, "frames"))} if os.path.isdir(os.path.join(nout, "frames")) else set()
        qframes = {f for f in os.listdir(os.path.join(qout, "frames"))} if os.path.isdir(os.path.join(qout, "frames")) else set()
        common = sorted(nframes & qframes)
        mism = []
        for name in common:
            a = os.path.join(nout, "frames", name); b = os.path.join(qout, "frames", name)
            if open(a, "rb").read() != open(b, "rb").read():
                mism.append(name)
        if mism:
            overall = 1
            print(f"FAIL  {len(mism)}/{len(common)} frames differ: {mism[:6]}")
        else:
            print(f"  ok  all {len(common)} input frames byte-identical native==qemu-tsp")

        ok = not (res["native"][0] or res["qemu"][0] or mism)
        print(f"\n{dev_id}: {'PASS' if ok else 'FAIL'} "
              f"(native {len(res['native'][0])} fail, qemu {len(res['qemu'][0])} fail, parity {len(mism)})")

    print("\n" + ("ALL DEVICES PASS" if overall == 0 else "SOME DEVICE FAILED"), "(" + " ".join(devices) + ")")
    return overall


if __name__ == "__main__":
    sys.exit(main())
