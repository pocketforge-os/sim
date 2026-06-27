#!/usr/bin/env python3
"""window_driver.py — the interactive ``--window`` DRIVER loop (the dogfood demo).

Bridges ``skin-render --window``'s mouse-event stream to the ONE ``control_surface.Device`` — the
SAME injection-as-API path ``check-skin.py`` drives headless, now LIVE: a press on the live bezel
resolves THROUGH the descriptor (``skin_model.Skin.tap`` / ``.drag`` -> ``Action``) to the exact
control_surface call, the control lights up on the window, and a picker click switches device.
"GUI click == headless inject", made interactive.

The renderer emits a press/release-aware protocol (tsp-qc1.6) on stdout, and re-reads its scene on
``reload <file>`` (stdin); this driver owns the other half:

    down <x> <y>      a bezel press began (left button down) -> activate the hit control, HOLD lit
    motion <x> <y>    left button held + moved -> a DRAG (analog stick / trigger only)
    up <x> <y>        release -> deactivate the gesture (HOLD: lit only while held)
    pick <codename>   a picker-panel click (on down) -> switch device

The press/release split (tsp-qc1.6) is what gives HOLD semantics (#2: lit only while held, not until
the next click), CHORDING groundwork (#3: presses release on up, not on the next down, so the
control_surface can hold several at once — the single-mouse GUI just can't issue two downs), and the
stick TAP-vs-DRAG disambiguation (#5: a quick tap = the L3/R3 stick-click; a hold-drag = move the
stick). The picker maps the renderer's ``<codename>`` (e.g. ``5050``) back to the descriptor
``device_id`` (e.g. ``a523``) — passing the codename straight to ``load_descriptor`` was the demo's
"thumbstick crash" (it 404'd on ``devices/5050/``).

Needs a VIDEO-capable ``skin-render`` (X11; ``skin/build-sdl3-window.sh``) + a display: a real
``$DISPLAY`` for the owner demo, or Xvfb for ``--self-test``. HONESTY CONTRACT unchanged — this proves
the LOGICAL layer (descriptor-correct input mapping), NOT on-panel graphics; acceptance is "the loop
runs in the container", not a visual gate.

Env (same as check-skin): APP_X86, APP_ARM64, QEMU_TSP, ROOTFS, PLATFORM, SKIN_RENDER.
Usage:
  window_driver.py <device_id> [--launcher native|qemu] [--self-test]
"""
import argparse
import os
import select
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "control"))
sys.path.insert(0, os.path.join(HERE, "..", "fb"))

import skin_model as SM                                   # noqa: E402
from control_surface import Device                        # noqa: E402
from ppm2png import read_png, write_ppm                   # noqa: E402

# ASCII hyphen — NOT an em-dash. The on-canvas title is drawn with the committed font8x13 bitmap,
# which has no U+2014 glyph (a "—" renders as mojibake); skin-render.c's WM title matches (#1).
TITLE = "PocketForge - Virtual Device"

# A pointer move past this many skin pixels (Manhattan) turns a press into a DRAG; below it, a
# down+up on a stick stays a TAP (the L3/R3 stick-click) rather than a tiny stick deflection.
DRAG_THRESHOLD = 6


def _png_to_ppm(png_path, ppm_path):
    w, h, rgb = read_png(png_path)
    write_ppm(ppm_path, w, h, rgb)
    return ppm_path


class Demo:
    """Owns the live device + skin + the skin-render subprocess, and the press->action->relight loop.

    A press ACTIVATES the hit control and HOLDS it lit (re-rendered on the window) until the matching
    release (mouse-up) — "lit only while held", the AVD-like UX (#2). Presses are tracked
    independently (a dict keyed by skin_part) so the underlying control_surface can CHORD several at
    once (#3); a single physical mouse can only issue one down at a time, so the live GUI shows one
    held control — that is a UX limit of the input device, not of the model. Sticks defer their
    action to release: a tap (no drag) is the L3/R3 stick-click, a hold-drag is ``set_stick`` (#5)."""

    def __init__(self, device_id, platform_dir, launcher, apps, workdir):
        self.platform_dir = platform_dir
        self.launcher = launcher
        self.apps = apps
        self.workdir = workdir
        self.picker = SM.build_picker(platform_dir)
        self.skin_render = os.environ.get("SKIN_RENDER")
        self.proc = None
        self._active = {}            # skin_part -> press record (see handle_down)
        self._drag_part = None       # the in-flight analog gesture (stick/trigger) skin_part
        self._open(device_id)

    # ---- device lifecycle ----
    def _open(self, device_id):
        self.device_id = device_id
        self.skin = SM.Skin(device_id, self.platform_dir)
        od = os.path.join(self.workdir, device_id)
        os.makedirs(od, exist_ok=True)
        self.dev = Device(device_id, self.platform_dir, launcher=self.launcher, outdir=od,
                          app_x86=self.apps[0], app_arm64=self.apps[1],
                          qemu_tsp=self.apps[2], rootfs=self.apps[3])
        self.dev.boot()
        self.body_ppm = _png_to_ppm(self.skin.body_path, os.path.join(od, "body.ppm"))
        self.lit_ppm = _png_to_ppm(self.skin.lit_body_path, os.path.join(od, "lit_body.ppm"))
        self.scene_path = os.path.join(od, "scene.txt")
        self._active = {}
        self._drag_part = None
        self._render()

    def _close_dev(self):
        try:
            self.dev.shutdown()
        except Exception:
            pass

    # ---- rendering ----
    def _fb_ppm(self):
        self.dev.snapshot("live")
        return os.path.join(self.dev.outdir, "frames", "live.ppm")

    def _render(self):
        lit_parts, hat_dirs = set(), {}
        for name, rec in self._active.items():
            if rec.get("lit"):
                lit_parts.add(name)
                if rec.get("hat") is not None:
                    hat_dirs[name] = rec["hat"]
        scene = self.skin.emit_scene(self.body_ppm, self.lit_ppm, self._fb_ppm(), lit_parts,
                                     picker=self.picker, selected=self.device_id,
                                     title=TITLE, hat_dirs=hat_dirs)
        with open(self.scene_path, "w") as f:
            f.write(scene)
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write(f"reload {self.scene_path}\n")
            self.proc.stdin.flush()

    @staticmethod
    def _apply(dev, actions):
        for a in actions:
            try:
                a.apply(dev)
            except Exception:
                pass

    # ---- input handling (the press/release state machine) ----
    def handle_down(self, x, y):
        """A press began at skin point (x, y). Buttons/hats/triggers activate immediately and HOLD
        lit until ``handle_up``; sticks defer (tap vs drag decided at motion/up). Returns the lit
        skin_part name (None if nothing lit — outside all parts, a re-press, or a stick down)."""
        part = self.skin.hit_test(x, y)
        if part is None:
            return None
        if part.name in self._active:        # already held (re-entrant down) — leave it be
            return part.name
        kind = part.kind
        rec = {"part": part, "kind": kind, "release": [], "hat": None,
               "lit": False, "down": (x, y), "drag": False, "last": None}
        if kind == "stick":
            # defer: a stick down could become a TAP (stick-click) or a DRAG (set_stick); nothing
            # lights yet, the decision happens in handle_motion/handle_up.
            self._active[part.name] = rec
            self._drag_part = part.name
            return None
        acts = self.skin.tap(x, y)           # button/hat -> [activate, deactivate]; trigger -> [set_axis]
        if not acts:
            return None
        acts[0].apply(self.dev)              # ACTIVATE (press / move_hat / set_axis)
        if len(acts) > 1:
            rec["release"] = acts[1:]
        elif acts[0].verb == "set_axis":     # analog trigger tap: reset to 0 on release
            rec["release"] = [SM.Action("set_axis", acts[0].input_id, 0.0)]
        if acts[0].verb == "move_hat":
            rec["hat"] = (acts[0].args[0], acts[0].args[1])
        elif acts[0].verb == "set_axis":
            self._drag_part = part.name      # a drag on the slider track follows the pointer
        rec["lit"] = True
        self._active[part.name] = rec
        self._render()
        return part.name

    def handle_motion(self, x, y):
        """Left button held + moved -> a DRAG. Only the in-flight analog gesture (stick/trigger)
        reacts: the stick deflects, the trigger slides. Digital controls ignore motion (binary)."""
        if not self._drag_part:
            return None
        rec = self._active.get(self._drag_part)
        if rec is None:
            return None
        dx0, dy0 = rec["down"]
        if not rec["drag"] and (abs(x - dx0) + abs(y - dy0)) < DRAG_THRESHOLD:
            return None                      # below the threshold -> still a tap, not a drag
        acts = self.skin.drag(dx0, dy0, x, y)   # set_stick / set_axis at the dragged-to point
        if not acts:
            return None
        rec["drag"] = True
        if acts[0] == rec["last"]:           # same resolved value -> skip the re-snapshot
            return rec["part"].name
        rec["last"] = acts[0]
        acts[0].apply(self.dev)
        if acts[0].verb == "set_stick":
            rec["release"] = [SM.Action("set_stick", acts[0].input_id, 0.0, 0.0)]
        elif acts[0].verb == "set_axis":
            rec["release"] = [SM.Action("set_axis", acts[0].input_id, 0.0)]
        rec["lit"] = True
        self._render()
        return rec["part"].name

    def handle_up(self, x, y):
        """Release -> deactivate the gesture. Ends the in-flight analog drag (a stick TAP becomes
        the L3/R3 stick-click; a stick DRAG and any trigger reset to centre/0), else releases the
        held digital control under the up point (chord-safe). Returns the released skin_part."""
        if self._drag_part and self._drag_part in self._active:
            rec = self._active.pop(self._drag_part)
            self._drag_part = None
            if rec["kind"] == "stick" and not rec["drag"]:
                # a TAP on the stick: the L3/R3 stick-click (a523) — or honestly nothing (a133,
                # whose sticks carry no stick-click row). This is the path that used to CRASH.
                self._apply(self.dev, self.skin.tap(*rec["down"]))
            else:
                self._apply(self.dev, rec["release"])
            self._render()
            return rec["part"].name
        part = self.skin.hit_test(x, y)
        target = part.name if part and part.name in self._active else None
        if target is None and len(self._active) == 1:
            target = next(iter(self._active))   # fallback: one held press, release it
        if target is None:
            return None
        rec = self._active.pop(target)
        self._apply(self.dev, rec["release"])
        self._render()
        return target

    def _release_all(self):
        for rec in list(self._active.values()):
            self._apply(self.dev, rec["release"])
        self._active = {}
        self._drag_part = None

    def handle_pick(self, code):
        """A picker click carries a CODENAME (e.g. ``5050``); map it back to the descriptor
        ``device_id`` (e.g. ``a523``) before switching. Unknown or same -> no-op (no crash)."""
        target = self._device_id_for_pick(code)
        if target is None or target == self.device_id:
            return
        self._release_all()
        self._close_dev()
        self._open(target)

    def _device_id_for_pick(self, code):
        for items in self.picker.values():
            for it in items:
                if str(it.get("codename")) == str(code) or it.get("device_id") == code:
                    return it["device_id"]
        return None

    # ---- live window loop ----
    def launch_window(self):
        self.proc = subprocess.Popen(
            [self.skin_render, "--scene", self.scene_path, "--window"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def run_live(self):
        self.launch_window()
        print(f"window_driver: live; device={self.device_id}. Press+hold the bezel; drag a stick; "
              f"click the picker; Ctrl-C / close to quit.", file=sys.stderr)
        try:
            while self.proc.poll() is None:
                line = self.proc.stdout.readline()
                if not line:
                    break
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "down" and len(parts) == 3:
                    self.handle_down(int(parts[1]), int(parts[2]))
                elif parts[0] == "up" and len(parts) == 3:
                    self.handle_up(int(parts[1]), int(parts[2]))
                elif parts[0] == "motion" and len(parts) == 3:
                    self.handle_motion(int(parts[1]), int(parts[2]))
                elif parts[0] == "pick" and len(parts) == 2:
                    self.handle_pick(parts[1])
        except KeyboardInterrupt:
            pass
        finally:
            self.quit()

    def quit(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()
        self._close_dev()


def _center(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def self_test(device_id, platform_dir, launcher, apps, workdir):
    """Autonomous proof the loop runs WITHOUT a window/display: feed synthetic press/drag/release
    events (the SAME coords the renderer would emit) through handle_down/motion/up and assert the
    app reacts. Covers what shipped UNTESTED in tsp-qc1.5 and broke: HOLD semantics (#2), CHORDING
    (#3), the PICKER device switch (#5's crash), and stick TAP/DRAG (#5). The live X11 window itself
    is smoke-tested in [1] (entrypoint `window-selftest`)."""
    import time
    d = Demo(device_id, platform_dir, launcher, apps, workdir)
    fails = []

    def chk(cond, msg):
        print(("  ok  " if cond else "FAIL  ") + msg)
        if not cond:
            fails.append(msg)

    def red(name):
        return d.dev.framebuffer_region(name).is_red()

    try:
        # [1] live-window smoke: if a display + the video skin-render are available (the DEMO image
        # under Xvfb), prove the REAL X11 window opens and stays in its event loop (not an instant
        # SDL_CreateWindow-NULL exit). This is what the offscreen sdl3-render lib CANNOT do.
        win = os.environ.get("SKIN_RENDER")
        if win and os.environ.get("DISPLAY") and os.path.basename(win) != "skin-render":
            proc = subprocess.Popen([win, "--scene", d.scene_path, "--window"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            time.sleep(1.5)
            alive = proc.poll() is None
            if not alive:
                err = (proc.stderr.read() or "").strip().splitlines()[-1:] if proc.stderr else []
                chk(False, f"live X11 window opened (skin-render-window) — exited early: {err}")
            else:
                chk(True, "live X11 window opened (skin-render-window in its event loop under Xvfb)")
                try:
                    proc.stdin.write("quit\n"); proc.stdin.flush(); proc.wait(timeout=3)
                except Exception:
                    proc.kill()

        # [2] HOLD semantics (#2): a DOWN lights the hit control and an UP releases it — lit ONLY
        # while held, NOT sticky-until-next-click. Buttons/hats use red readback; triggers use the
        # slider. (Sticks are deferred -> covered in [5].)
        held = 0
        for p in d.skin.ordered_parts():
            if p.kind == "stick":
                continue
            cx, cy = _center(p.rect)
            name = d.handle_down(cx, cy)
            if not name:
                continue
            if p.kind == "trigger":
                chk(d.dev.slider(name).fraction() > 0.30,
                    f"DOWN -> trigger {name} slider raised while held (~0.5)")
                d.handle_up(cx, cy)
                chk(d.dev.slider(name).fraction() < 0.10,
                    f"UP -> trigger {name} slider released to 0 (HOLD, not sticky)")
            else:
                chk(red(name), f"DOWN -> {p.kind} {name} lit while held")
                d.handle_up(cx, cy)
                chk(not red(name), f"UP -> {name} released (HOLD semantics, not toggle-sticky)")
            held += 1
        chk(held >= 3, f"hold-to-light exercised on >=3 controls (got {held})")

        # [3] CHORDING (#3): two buttons held at once BOTH light — the driver no longer force-
        # deactivates the prior control on a new down, so control_surface chords. (A single mouse
        # can't issue two downs live; this is the capability, the GUI's one-at-a-time is a UX note.)
        btns = [p for p in d.skin.ordered_parts() if p.kind == "button"][:2]
        if len(btns) == 2:
            a, b = btns
            acx, acy = _center(a.rect); bcx, bcy = _center(b.rect)
            d.handle_down(acx, acy)
            d.handle_down(bcx, bcy)                 # no force-deactivate of A -> both held
            chk(red(a.name) and red(b.name),
                f"CHORD: {a.name}+{b.name} both lit (control_surface chords; single-mouse is a UX limit)")
            d.handle_up(acx, acy); d.handle_up(bcx, bcy)
            chk(not red(a.name) and not red(b.name),
                "CHORD released: both controls dark after both ups")

        # [4] PICKER device switch (#5's crash): a pick carries a CODENAME (5040/5050), which the
        # driver must map back to the device_id — passing it straight to load_descriptor 404'd on
        # devices/5050/ and killed the window (the captured "thumbstick crash"). UNTESTED in .5.
        orig = d.device_id
        orig_code = next((it["codename"] for items in d.picker.values() for it in items
                          if it["device_id"] == orig), None)
        others = [it for items in d.picker.values() for it in items if it["device_id"] != orig]
        if others:
            tgt = others[0]
            d.handle_pick(tgt["codename"])         # a CODENAME, not a device_id
            chk(d.device_id == tgt["device_id"],
                f"PICK codename {tgt['codename']} -> switched to {tgt['device_id']} (codename->device_id)")
            before = d.device_id
            d.handle_pick("nope-not-a-device")
            chk(d.device_id == before, "PICK unknown codename -> no-op (no FileNotFoundError)")
            if orig_code:
                d.handle_pick(orig_code)
                chk(d.device_id == orig, f"PICK back to codename {orig_code} -> {orig}")

        # [5] STICK tap vs drag (#5): a TAP (down+up, no drag) is the L3/R3 stick-click on a523 (or
        # an honest no-op on a133); a hold-DRAG deflects the stick (set_stick) and lights it; the
        # release re-centres. The picker-crash above used to take the window down BEFORE this ran.
        sp = d.skin.parts.get("stick_l") or d.skin.parts.get("stick_r")
        if sp:
            scx, scy = _center(sp.rect)
            rx, ry, rw, rh = sp.rect
            # (a) TAP — must not crash (it was the picker-crash victim) + resolve to the stick-click
            chk(d.handle_down(scx, scy) is None,
                f"stick {sp.name} DOWN -> nothing lit yet (tap/drag undecided)")
            chk(d.handle_up(scx, scy) == sp.name, f"stick {sp.name} TAP completes (no crash)")
            if sp.stick_click is not None:
                sc_id = sp.stick_click["id"]
                chk(d.skin.tap(scx, scy) == [SM.Action("press", sc_id), SM.Action("release", sc_id)],
                    f"stick {sp.name} TAP -> stick-click press/release({sc_id}) (a523)")
            else:
                chk(d.skin.tap(scx, scy) == [],
                    f"stick {sp.name} TAP -> [] (a133: no stick-click row, pure DATA)")
            # (b) DRAG — full-right deflection lights the stick; release re-centres (dark)
            d.handle_down(scx, scy)
            d.handle_motion(rx + rw - 1, ry + rh // 2)
            chk(red(sp.name), f"stick {sp.name} DRAG -> set_stick deflects (lit)")
            d.handle_up(rx + rw - 1, ry + rh // 2)
            chk(not red(sp.name), f"stick {sp.name} DRAG release -> set_stick(0,0) (centred, dark)")

        d._release_all()
        d._render()
    finally:
        d.quit()

    print(f"\nwindow_driver self-test {device_id}: {'PASS' if not fails else 'FAIL (%d)' % len(fails)}")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("device_id")
    ap.add_argument("--launcher", default="qemu", choices=["native", "qemu"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--workdir", default="/tmp/pf-window")
    a = ap.parse_args()

    platform_dir = os.environ.get("PLATFORM") or sys.exit("set PLATFORM")
    apps = (os.environ.get("APP_X86"), os.environ.get("APP_ARM64"),
            os.environ.get("QEMU_TSP"), os.environ.get("ROOTFS"))
    for i, k in enumerate(("APP_X86", "APP_ARM64", "QEMU_TSP", "ROOTFS")):
        if not apps[i]:
            sys.exit(f"set {k}")
    os.makedirs(a.workdir, exist_ok=True)

    if a.self_test:
        return self_test(a.device_id, platform_dir, a.launcher, apps, a.workdir)
    if not os.environ.get("SKIN_RENDER"):
        sys.exit("set SKIN_RENDER (the video-capable skin-render-window binary)")
    Demo(a.device_id, platform_dir, a.launcher, apps, a.workdir).run_live()
    return 0


if __name__ == "__main__":
    sys.exit(main())
