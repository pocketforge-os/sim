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

EVERY VIEW, not just the front (tsp-w3kx). Skins went multi-view in tsp-65jc.27: a device carries
`front` (from `[skin]` + `[skin.parts]`, the atlas's TOP-LEVEL `controls`) plus rendered edge views
such as `top` (from `[skin.views.<name>]` + `[skin.views.<name>.parts]`, the atlas's
`views.<name>.controls`). This script used to build one `Skin` — whose active view is `front` — and
never iterate the rest, so a non-front view's hit-boxes were compared against NOTHING and the guard
could not fail for the reason it exists. It now walks `skin.view_names()` and names the VIEW in
every line, so an operator can tell which view drifted.

The reverse direction ("a rendered control nobody binds") is now computed STRICTLY PER VIEW —
`views.<v>.controls` minus `[skin.views.<v>.parts]` — and every leftover is a FAIL (tsp-w3kx
coordinator ruling, 2026-07-27). Atlas and descriptor are kept in lockstep PER VIEW by generation
(`render.py --write-views`; "controls not visible from a view are simply absent here"), so a
control the FRONT atlas renders while the FRONT table does not bind it is a REAL defect — that
render draws a hit-box which resolves to nothing on a front-view tap — and downgrading it would
neuter exactly what this direction exists to catch. A genuinely view-only control (in
`views.top.controls` + `[skin.views.top.parts]`, absent from BOTH front tables) yields no leftover
on either side, so per-view subtraction fixes the old false FAIL without any loosening. Where a
leftover IS bound in some other view the FAIL message says so — diagnosis, not absolution.

Exit 0 = every model-rendered skin's descriptor rects match its atlas exactly, in every view.
Exit 1 = a mismatch (the skin would silently diverge from the .scad render — the exact drift D9
gates on platform).

Usage: assert-atlas.py [--platform DIR] [devices...]   (default PLATFORM env; default a133 a523)

The DEMONSTRATED NEGATIVE CONTROL for this guard — the proof it can actually go red, per view,
for each of its own reasons — is `selftest-assert-atlas.py` beside this file. A passing guard only
ever tells you it RAN.
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


def _view_atlas(meta, view_name):
    """The (controls, canvas) the atlas holds for one VIEW, plus whether the view exists there.

    front lives at the atlas's top level (unchanged semantics); every other view lives under
    `views.<name>` (written by render.py --write-views, tsp-65jc.27)."""
    if view_name == "front":
        return meta.get("controls", {}), meta.get("canvas", {}), True
    v = meta.get("views", {}).get(view_name)
    if v is None:
        return {}, {}, False
    return v.get("controls", {}), v.get("canvas", {}), True


def _parts_table(view_name):
    """The descriptor table a view's rects come from — for messages that point somewhere real."""
    return "[skin.parts]" if view_name == "front" else f"[skin.views.{view_name}.parts]"


def check_device(platform_dir, device_id):
    print(f"\n############## {device_id} ##############")
    controls, meta = _load_atlas(platform_dir, device_id)
    if controls is None:
        print(f"  skip  {device_id}: no skins/{device_id}/model-render.json "
              f"(not a .scad-rendered skin — descoped/old-style bezel art)")
        return None  # not a failure; just not model-backed

    skin = SM.Skin(device_id, platform_dir)
    views = skin.view_names()
    fails = []
    checked = 0

    # Which views bind each control, across the WHOLE descriptor. This does NOT soften the
    # reverse-direction check below — that stays strict per view — it only lets a FAIL say where
    # else the control IS bound, which is the difference between a usable diagnosis and a bare
    # "unbound". Being bound elsewhere never excuses a view whose own render draws a hit-box the
    # descriptor does not bind in that view.
    bound_in = {}
    for vn in views:
        for sp in skin.set_view(vn).parts:
            bound_in.setdefault(sp, []).append(vn)

    for vn in views:
        v = skin.set_view(vn)
        acontrols, acanvas, present = _view_atlas(meta, vn)
        print(f"\n  ---- view {vn} ----")
        if not present:
            print(f"FAIL  view {vn}: descriptor has [skin.views.{vn}] but "
                  f"model-render.json has no views.{vn} (this view was never rendered)")
            fails.append(f"{vn}:<view>")
            continue

        # atlas canvas == this view's skin (bezel PNG) dims
        if acanvas:
            ok = (int(acanvas.get("w", -1)) == v.skin_w and int(acanvas.get("h", -1)) == v.skin_h)
            print(("  ok  " if ok else "FAIL  ") +
                  f"view {vn}: atlas canvas {acanvas.get('w')}x{acanvas.get('h')} "
                  f"== bezel PNG {v.skin_w}x{v.skin_h}")
            if not ok:
                fails.append(f"{vn}:canvas dims")

        # every descriptor rect for this view (== the GUI/check-skin hit-test rect when this view
        # is active) == the atlas control rect for the SAME view
        for sp in sorted(v.parts):
            checked += 1
            drect = tuple(v.parts[sp].rect)   # (x,y,w,h) from descriptor via skin_model
            if sp not in acontrols:
                print(f"FAIL  view {vn} {sp}: in descriptor {_parts_table(vn)} {drect} but "
                      f"ABSENT from model-render.json view {vn}")
                fails.append(f"{vn}:{sp}")
                continue
            a = acontrols[sp]
            arect = (int(a["x"]), int(a["y"]), int(a["w"]), int(a["h"]))
            ok = drect == arect
            print(("  ok  " if ok else "FAIL  ") +
                  f"view {vn} {sp}: hit-test/{_parts_table(vn)} {drect} "
                  f"== model-render.json view {vn} {arect}")
            if not ok:
                fails.append(f"{vn}:{sp}")

        # atlas controls this view renders but the descriptor does not bind HERE. STRICT per view:
        # every leftover is a FAIL. This view's render draws a hit-box that resolves to nothing on
        # a tap in this view, whatever other views may do.
        for sp in sorted(set(acontrols) - set(v.parts)):
            elsewhere = [o for o in bound_in.get(sp, []) if o != vn]
            where = (f" (it IS bound in view(s) {', '.join(elsewhere)} — so this is per-view "
                     f"drift, not an unknown control)" if elsewhere else "")
            print(f"FAIL  view {vn} {sp}: in model-render.json view {vn} but has NO descriptor "
                  f"{_parts_table(vn)} binding{where}")
            fails.append(f"{vn}:{sp}")

    # a rendered view the descriptor never adopted (the mirror of the missing-view case above)
    for vn in sorted(set(meta.get("views", {})) - set(views)):
        print(f"FAIL  view {vn}: model-render.json renders views.{vn} but the descriptor has "
              f"no [skin.views.{vn}] (nothing binds it)")
        fails.append(f"{vn}:<view>")

    print(f"\n{device_id}: {'PASS' if not fails else 'FAIL'} "
          f"({checked} parts checked across {len(views)} view(s): {', '.join(views)}; "
          f"{len(fails)} mismatch)")
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
