#!/usr/bin/env python3
"""check-fb.py — assert the tsp-an4.4 claims for ONE device from captured artifacts.

Claims:
  A. CANVAS GEOMETRY FROM DESCRIPTOR — the rendered framebuffer's dimensions == the
     descriptor's screens[0].render_canvas (landscape), NOT hardcoded.
  B. SOFTWARE RENDER CORRECT — the known test-pattern regions have the expected colors
     (deterministic; GPU-less software rasterizer). Proves layout/widget logic.
  C. NATIVE == QEMU-TSP — the canvas PPM rendered native-x86 is byte-identical to the one
     rendered by the arm64 binary under qemu-tsp inside bubblewrap (same software rasterizer).
  D. ROTATION HONORED AS DATA — the "present" frame's dimensions == the descriptor rotation
     of the canvas (cw90: 1280x720 -> 720x1280), and a known corner maps to the rotated
     position. The rotation is DATA (logical), NOT the per-SoC disp-engine silicon.
  E. PNG ARTIFACT — a valid PNG was produced from the dump (the CI/skin-composite artifact).
  F. tsp-osr PINNED — the non-OPENGL window + forced "software" renderer recipe succeeded.

Stdlib only. Non-zero exit on any failure.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppm2png import read_ppm  # noqa: E402

try:
    import tomllib
    def _toml(p):
        with open(p, "rb") as f:
            return tomllib.load(f)
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore
    def _toml(p):
        with open(p, "rb") as f:
            return tomllib.load(f)

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def px(rgb, w, x, y):
    o = (y * w + x) * 3
    return rgb[o], rgb[o + 1], rgb[o + 2]


def rotated_dims(w, h, rot):
    return (h, w) if rot in ("cw90", "cw270") else (w, h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--platform", required=True)
    ap.add_argument("--out", required=True)   # dir with canvas.x86.ppm, canvas.arm64.ppm, ...
    a = ap.parse_args()

    desc = _toml(os.path.join(a.platform, "devices", a.device, "capabilities.toml"))
    screen = desc["screens"][0]
    rc = screen["render_canvas"]
    W, H, ROT = rc["w"], rc["h"], screen.get("rotation", "none")

    cx = os.path.join(a.out, "canvas.x86.ppm")
    cq = os.path.join(a.out, "canvas.arm64.ppm")
    pp = os.path.join(a.out, "present.x86.ppm")
    png = os.path.join(a.out, "canvas.png")

    print("A. canvas geometry from descriptor")
    w, h, rgb = read_ppm(cx)
    check((w, h) == (W, H), f"canvas {w}x{h} == descriptor render_canvas {W}x{H}")

    print("B. software-render test pattern correct (deterministic regions)")
    qw, qh = W // 4, H // 4
    samples = [
        ("TL red",    qw // 2,         qh // 2,         (220, 30, 30)),
        ("TR green",  W - qw // 2,     qh // 2,         (30, 200, 30)),
        ("BL blue",   qw // 2,         H - qh // 2,     (40, 60, 220)),
        ("BR yellow", W - qw // 2,     H - qh // 2,     (230, 210, 20)),
        ("center wht", W // 2,         H // 2,          (240, 240, 240)),
        ("bg gray",   W // 2,          qh // 2,         (24, 24, 24)),
    ]
    for name, x, y, want in samples:
        got = px(rgb, w, min(x, w - 1), min(y, h - 1))
        check(got == want, f"{name} @({x},{y}) == {want} (got {got})")

    print("C. native-x86 == arm64-under-qemu-tsp (byte-identical)")
    with open(cx, "rb") as f1, open(cq, "rb") as f2:
        check(f1.read() == f2.read(), "canvas PPM byte-identical (native == qemu-tsp)")

    print("D. rotation honored as DATA (descriptor screens.rotation)")
    ow, oh, prgb = read_ppm(pp)
    ew, eh = rotated_dims(W, H, ROT)
    check((ow, oh) == (ew, eh), f"present {ow}x{oh} == rotate({W}x{H},{ROT}) {ew}x{eh}")
    # cw90 maps canvas TL (red) -> present top-right; verify red landed there.
    if ROT == "cw90":
        got = px(prgb, ow, ow - 1 - (qh // 2), qw // 2)
        check(abs(got[0] - 220) < 30 and got[1] < 60, f"cw90: canvas TL-red -> present top-right (got {got})")
    elif ROT == "none":
        got = px(prgb, ow, qw // 2, qh // 2)
        check(got == (220, 30, 30), f"none: TL stays red (got {got})")

    print("E. PNG artifact produced")
    ok_png = os.path.isfile(png) and open(png, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    check(ok_png, "canvas.png exists with valid PNG signature")

    print("F. tsp-osr-safe renderer recipe pinned")
    log = os.path.join(a.out, "render.arm64.log")
    txt = open(log).read() if os.path.isfile(log) else ""
    check("tsp-osr-pin: OK" in txt, "non-OPENGL window + SDL_CreateRenderer(\"software\") OK (no segfault)")

    print()
    if FAILS:
        print(f"tsp-an4.4 [{a.device}]: FAIL ({len(FAILS)} assertion(s))")
        return 1
    print(f"tsp-an4.4 [{a.device}]: PASS — descriptor-driven software-render to a virtual fb on a "
          f"GPU-less host; native==qemu; rotation from data; tsp-osr-safe; PNG dumped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
