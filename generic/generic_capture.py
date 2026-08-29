#!/usr/bin/env python3
"""Run a native or arm64 app with a shared XRGB8888 fb0 and capture/composite it."""
import argparse
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skin"), os.path.join(HERE, "..", "fb")]
import skin_model as SM
from ppm2png import read_png, write_ppm


def dimensions(platform, device):
    skin = SM.Skin(device, platform)
    return skin, (skin.canvas_w, skin.canvas_h)


def capture_raw(path, out, w, h):
    with open(path, "rb") as f:
        raw = f.read(w * h * 4)
    if len(raw) != w*h*4:
        raise RuntimeError(f"short framebuffer: {len(raw)} != {w*h*4}")
    rgb = bytearray(w*h*3)
    for i in range(w*h):
        # XRGB8888 in memory on the supported little-endian host/arm64 targets is B,G,R,X.
        rgb[i*3:i*3+3] = raw[i*4+2], raw[i*4+1], raw[i*4]
    tmp = out + ".tmp"
    write_ppm(tmp, w, h, rgb)
    os.replace(tmp, out)


def ppm_art(png, out):
    w, h, rgb = read_png(png)
    write_ppm(out, w, h, rgb)


def composite(skin, fb, out, renderer, work):
    body = os.path.join(work, "body.ppm"); lit = os.path.join(work, "lit.ppm")
    ppm_art(skin.body_path, body); ppm_art(skin.lit_body_path, lit)
    scene = skin.emit_scene(body, lit, fb, set(), title="PocketForge - Generic App")
    scene_path = os.path.join(work, "scene.txt")
    with open(scene_path, "w") as f: f.write(scene)
    mode = ["--window"] if out is None else ["--shot", out]
    return subprocess.Popen([renderer, "--scene", scene_path] + mode,
                            stdin=subprocess.PIPE if out is None else None, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True); ap.add_argument("--platform", required=True)
    ap.add_argument("--app", required=True); ap.add_argument("--launcher", choices=("native","qemu"), default="qemu")
    ap.add_argument("--qemu-tsp"); ap.add_argument("--rootfs"); ap.add_argument("--harness")
    ap.add_argument("--frame", required=True); ap.add_argument("--shot"); ap.add_argument("--window", action="store_true")
    ap.add_argument("--skin-render"); ap.add_argument("--cadence-ms", type=int, default=50)
    ap.add_argument("app_args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    skin, (w,h) = dimensions(a.platform, a.device)
    os.makedirs(os.path.dirname(os.path.abspath(a.frame)), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pf-generic-") as work:
        # A private, pre-sized shared mapping is bindable by bwrap at /dev/fb0 and is
        # removed with this temporary directory. It has the same host/app mmap seam as
        # fb-render's memfd, while providing the pathname bwrap requires on supported CI.
        fb = os.path.join(work, "fb0")
        with open(fb, "wb") as f: f.truncate(w*h*4)
        env = os.environ.copy(); env.update(PF_FB_WIDTH=str(w), PF_FB_HEIGHT=str(h), PF_FB_STRIDE=str(w*4))
        if a.launcher == "native":
            env["PF_FB0"] = fb; cmd = [a.app] + a.app_args
        else:
            for name, val in (("QEMU_TSP",a.qemu_tsp),("ROOTFS",a.rootfs),("harness",a.harness)):
                if not val: ap.error(f"--{name.lower().replace('_','-')} is required for qemu")
            env.update(QEMU_TSP=a.qemu_tsp, ROOTFS=a.rootfs, FB0_BIND=fb)
            cmd = [a.harness, a.app] + a.app_args
        proc = subprocess.Popen(cmd, env=env)
        window = None
        if a.window:
            if not a.skin_render: ap.error("--skin-render is required with --window")
            capture_raw(fb, a.frame, w, h)
            window = composite(skin, a.frame, None, a.skin_render, work)
        while proc.poll() is None:
            # Snapshot on cadence, not on mtime: mmap writes need not update inode metadata.
            capture_raw(fb, a.frame, w, h)
            if window and window.poll() is None:
                window.stdin.write(f"reload {os.path.join(work,'scene.txt')}\n"); window.stdin.flush()
            time.sleep(max(1,a.cadence_ms)/1000.0)
        if proc.returncode: return proc.returncode
        capture_raw(fb, a.frame, w, h)
        if a.shot:
            if not a.skin_render: ap.error("--skin-render is required with --shot")
            shot = composite(skin, a.frame, a.shot, a.skin_render, work)
            return shot.wait()
        if window:
            return window.wait()
    return 0

if __name__ == "__main__": sys.exit(main())
