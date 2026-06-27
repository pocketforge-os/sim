#!/usr/bin/env python3
"""window_driver.py — tsp-qc1.5: the interactive ``--window`` DRIVER loop (the dogfood demo).

Bridges ``skin-render --window``'s click stream to the ONE ``control_surface.Device`` — the SAME
injection-as-API path ``check-skin.py`` drives headless, now LIVE: a click on the live bezel resolves
THROUGH the descriptor (``skin_model.Skin.tap`` -> ``Action``) to the exact control_surface call, the
control lights up on the window, and a picker click switches device. "GUI click == headless inject",
made interactive.

The renderer (``skin-render --window``) emits ``click <skin_x> <skin_y>`` / ``pick <device_id>`` on
stdout and re-reads its scene on ``reload <file>`` (stdin); this driver owns the other half.

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

TITLE = "PocketForge — Virtual Device"


def _png_to_ppm(png_path, ppm_path):
    w, h, rgb = read_png(png_path)
    write_ppm(ppm_path, w, h, rgb)
    return ppm_path


class Demo:
    """Owns the live device + skin + the skin-render subprocess, and the click->action->relight loop.

    Demo behaviour: a click ACTIVATES the hit control and keeps it lit (re-rendered on the window)
    until the next click, which first de-activates the previous control. That gives a clear
    "currently-pressed control is lit" UX while exercising the real control_surface calls."""

    def __init__(self, device_id, platform_dir, launcher, apps, workdir):
        self.platform_dir = platform_dir
        self.launcher = launcher
        self.apps = apps
        self.workdir = workdir
        self.picker = SM.build_picker(platform_dir)
        self.skin_render = os.environ.get("SKIN_RENDER")
        self.proc = None
        self._active = None          # (part_name, [deactivate Actions], hat_dir or None)
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
        self._active = None
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
        if self._active:
            name, _deact, hat = self._active
            lit_parts.add(name)
            if hat is not None:
                hat_dirs[name] = hat
        scene = self.skin.emit_scene(self.body_ppm, self.lit_ppm, self._fb_ppm(), lit_parts,
                                     picker=self.picker, selected=self.device_id,
                                     title=TITLE, hat_dirs=hat_dirs)
        with open(self.scene_path, "w") as f:
            f.write(scene)
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write(f"reload {self.scene_path}\n")
            self.proc.stdin.flush()

    # ---- input handling ----
    def _deactivate(self):
        if self._active:
            for a in self._active[1]:
                try:
                    a.apply(self.dev)
                except Exception:
                    pass
            self._active = None

    def handle_click(self, x, y):
        """Resolve a bezel click through the descriptor and apply it live."""
        self._deactivate()
        part = self.skin.hit_test(x, y)
        acts = self.skin.tap(x, y)
        if not part or not acts:
            self._render()
            return None
        acts[0].apply(self.dev)                              # ACTIVATE
        # de-activation: buttons/hats give [activate, deactivate]; a trigger tap is a single
        # set_axis -> reset it to 0 on the next click.
        if len(acts) > 1:
            deact = acts[1:]
        elif acts[0].verb == "set_axis":
            deact = [SM.Action("set_axis", acts[0].input_id, 0.0)]
        else:
            deact = []
        hat = None
        if acts[0].verb == "move_hat":
            hat = (acts[0].args[0], acts[0].args[1])
        self._active = (part.name, deact, hat)
        self._render()
        return part.name

    def handle_pick(self, code):
        if code == self.device_id:
            return
        self._deactivate()
        self._close_dev()
        self._open(code)

    # ---- live window loop ----
    def launch_window(self):
        self.proc = subprocess.Popen(
            [self.skin_render, "--scene", self.scene_path, "--window"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def run_live(self):
        self.launch_window()
        print(f"window_driver: live; device={self.device_id}. Click the bezel; Ctrl-C / close to quit.",
              file=sys.stderr)
        try:
            while self.proc.poll() is None:
                line = self.proc.stdout.readline()
                if not line:
                    break
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "click" and len(parts) == 3:
                    self.handle_click(int(parts[1]), int(parts[2]))
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
    """Autonomous proof the loop runs WITHOUT a window/display: feed synthetic clicks (the SAME
    coords a tap would emit) through handle_click and assert the app lights the hit control — the
    live-driver analog of check-skin's GUI-click==inject assertion. The live X11 window itself is
    smoke-tested separately (entrypoint `window-selftest`)."""
    import time
    d = Demo(device_id, platform_dir, launcher, apps, workdir)
    fails = []

    def chk(cond, msg):
        print(("  ok  " if cond else "FAIL  ") + msg)
        if not cond:
            fails.append(msg)

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

        # [2] the driver loop: synthetic clicks -> the app lights the hit control. Rects can overlap
        # (last-painted wins), so assert on the ACTUALLY-hit control handle_click returns, not the
        # iterated part. _deactivate() runs first each click, so only the current control is lit.
        lit, dark, seen = 0, 0, set()
        for p in d.skin.ordered_parts():
            cx, cy = _center(p.rect)
            acts = d.skin.tap(cx, cy)
            lit_name = d.handle_click(cx, cy)
            if lit_name and acts and acts[0].verb in ("press", "move_hat") and lit_name not in seen:
                seen.add(lit_name)
                chk(d.dev.framebuffer_region(lit_name).is_red(),
                    f"click -> ({acts[0].verb} {acts[0].input_id}) lights {lit_name} live")
                lit += 1
            elif not acts:
                # a non-clickable part (e.g. a133 stick w/o stick-click) -> no action, nothing lights
                chk(not d.dev.framebuffer_region(p.name).is_red(),
                    f"click {p.name} -> descriptor-honest no-op (stays dark)")
                dark += 1
        chk(lit >= 3, f"live driver lit >=3 distinct controls through the loop (got {lit})")
        d._deactivate()
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
