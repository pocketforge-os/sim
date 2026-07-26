#!/usr/bin/env python3
"""assert-atlas.py — tsp-65jc.1 (infra-113 A1): PROVE, by assertion (not eyeball), that the
clickable-skin atlas the sim GUI + check-skin actually consume is the SAME atlas the platform's
semantic OpenSCAD device model rendered.

The chain the a133 3D-model skin must satisfy end to end:

    device-models/trimui-smart-pro/*.scad  --render.py-->  skins/a133/model-render.json
                                                                    ||  (rect equality, HERE)
    devices/a133/capabilities.toml [skin.parts]  ==  skin_model.Skin(...).parts  (hit-test rects)

`skin_model.Skin` is the ONE source the SDL3 GUI renderer (skin-render.c) and the headless proof
(check-skin.py) both hit-test through, so `Skin(dev).parts[sp].rect` IS the pixel a GUI click lands
on. This script asserts every one of those rects equals `model-render.json`'s `controls[<sp>]` rect
(and the atlas dims / canvas agree), for every device whose descriptor references a model render.
A device with no model-render.json (e.g. a523's old-style bezel art) is SKIPPED with a note — it is
not yet a .scad-rendered skin (platform PR #68, descoped lane).

Exit 0 = every model-rendered skin's descriptor rects match its atlas exactly. Exit 1 = a mismatch
(the skin would silently diverge from the .scad render — the exact drift D9 gates on platform).

Usage: assert-atlas.py [--platform DIR] [devices...]   (default PLATFORM env; default a133 a523)
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import skin_model as SM  # noqa: E402


def _load_atlas(platform_dir, device_id):
    """Return (controls_dict, meta) from skins/<id>/model-render.json, or (None, None)."""
    path = os.path.join(platform_dir, "skins", device_id, "model-render.json")
    if not os.path.isfile(path):
        return None, None
    with open(path) as f:
        j = json.load(f)
    return j.get("controls", {}), j


def check_device(platform_dir, device_id):
    print(f"\n############## {device_id} ##############")
    controls, meta = _load_atlas(platform_dir, device_id)
    if controls is None:
        print(f"  skip  {device_id}: no skins/{device_id}/model-render.json "
              f"(not a .scad-rendered skin — descoped/old-style bezel art)")
        return None  # not a failure; just not model-backed

    skin = SM.Skin(device_id, platform_dir)
    fails = []

    # atlas canvas == skin (bezel PNG) dims
    canvas = meta.get("canvas", {})
    if canvas:
        ok = (int(canvas.get("w", -1)) == skin.skin_w and int(canvas.get("h", -1)) == skin.skin_h)
        print(("  ok  " if ok else "FAIL  ") +
              f"atlas canvas {canvas.get('w')}x{canvas.get('h')} == bezel PNG {skin.skin_w}x{skin.skin_h}")
        if not ok:
            fails.append("canvas dims")

    # every descriptor [skin.parts] rect (== the GUI/check-skin hit-test rect) == atlas control rect
    for sp in sorted(skin.parts):
        drect = tuple(skin.parts[sp].rect)  # (x,y,w,h) from descriptor via skin_model
        if sp not in controls:
            print(f"FAIL  {sp}: in descriptor [skin.parts] {drect} but ABSENT from model-render.json")
            fails.append(sp)
            continue
        a = controls[sp]
        arect = (int(a["x"]), int(a["y"]), int(a["w"]), int(a["h"]))
        ok = drect == arect
        print(("  ok  " if ok else "FAIL  ") +
              f"{sp}: hit-test/[skin.parts] {drect} == model-render.json {arect}")
        if not ok:
            fails.append(sp)

    # atlas controls with no matching descriptor part (a rendered-but-unbound control)
    extra = sorted(set(controls) - set(skin.parts))
    for sp in extra:
        print(f"FAIL  {sp}: in model-render.json but has NO descriptor [skin.parts] binding")
        fails.append(sp)

    n = len(skin.parts)
    print(f"\n{device_id}: {'PASS' if not fails else 'FAIL'} "
          f"({n} parts checked, {len(fails)} mismatch)")
    return not fails


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", default=os.environ.get("PLATFORM"))
    ap.add_argument("devices", nargs="*", default=None)
    a = ap.parse_args()
    if not a.platform:
        sys.exit("set PLATFORM (or pass --platform DIR)")
    devices = a.devices or ["a133", "a523"]

    results = [check_device(a.platform, d) for d in devices]
    checked = [r for r in results if r is not None]
    overall = 0 if all(checked) else 1
    modelled = sum(1 for r in results if r is not None)
    print("\n" + ("ATLAS EQUALITY PROVEN" if overall == 0 else "ATLAS MISMATCH") +
          f" ({modelled} model-rendered skin(s) checked of {len(devices)} device(s))")
    return overall


if __name__ == "__main__":
    sys.exit(main())
