#!/usr/bin/env python3
"""skin_model.py — tsp-an4.6: the clickable-skin GEOMETRY + picker (the toolkit-agnostic brains).

The AVD north star expressed as DATA. This is the descriptor->skin logic that the SDL3 GUI
renderer (``skin-render.c``) and the headless proof (``check-skin.py``) BOTH consume — exactly
as .5's ``layout.py`` is shared by the app and the test. ONE descriptor -> app + sim + test +
SKIN, no second source of truth.

It owns four things:

  1. The manufacturer>device PICKER (the two-tier Android-AVD leaf). Built by SCANNING the
     platform ``devices/`` dir and grouping by ``[identity].manufacturer`` — ``TrimUI > 5040
     (Smart Pro)`` / ``TrimUI > 5050 (Smart Pro S)`` fall out of the descriptors' identity rows,
     ZERO hand-typed device names. A new variant = a new descriptor + skin folder, zero code.

  2. The RAW ``[skin.parts]`` rects in SKIN-image space (the bezel PNG's pixel space, 1480x640)
     — NOT ``layout.py``'s canvas-FIT rects. This is the COORDINATE-SPACE distinction that makes
     .6 different from .5: the .5 *app* draws controls INTO the 1280x720 render_canvas, so
     layout.py fits the parts into that canvas; the .6 GUI draws the real bezel ``body.png`` at
     skin resolution and hit-tests the raw ``[skin.parts]`` rects directly.

  3. HIT-TEST + GESTURE -> a control_surface ACTION. A click on ``btn_south``'s rect resolves to
     ``("press","south")``; a drag on a trigger to ``("set_axis","ltrig",frac)``; a drag on a
     stick to ``("set_stick",...)``; a bare tap on a stick to its stick-click (l3/r3, a523-only).
     The GUI applies the SAME action the headless test injects -> "GUI click == headless inject"
     holds BY CONSTRUCTION, because both paths come through this resolver. The disambiguation
     (tap=click vs drag=axis on the shared stick part) is the load-bearing logic .6 adds.

  4. The COMPOSITOR geometry: where the live virtual fb (the 1280x720 ``render_canvas`` the app
     draws) lands inside the bezel's ``display_rect``. The orientation is DATA-DRIVEN: we
     composite whichever of {canvas, rotate(canvas, screens.rotation)} has the aspect matching
     ``display_rect``. For a133/a523 that is the canvas itself (display_rect 872x490 ~ 1280x720,
     NOT the cw90-rotated 720x1280) — confirmed by the descriptor's own note: "the app draws a
     LANDSCAPE 1280x720 canvas; the disp engine rotates it onto the physically-portrait panel...
     the rotation MECHANISM is per-SoC CODE, not data here." So ``rotation`` is the per-SoC
     panel-mount mechanism the sim does NOT reproduce (HONESTY CONTRACT item 5); it is carried as
     data so a variant whose ``display_rect`` matched the rotated dims would Just Work with zero
     per-device code.

Pure data; device-free; stdlib only. Importable + a small CLI (``skin_model.py picker
--platform ...`` / ``... show --device ... --platform ...``).
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "control"))
sys.path.insert(0, os.path.join(HERE, "..", "fb"))

import layout as L                       # noqa: E402  (reuse load_descriptor + part_for_input)
from ppm2png import png_dims             # noqa: E402  (skin dims from the bezel PNG header)

load_descriptor = L.load_descriptor      # re-export (one parser, no drift)


# ---------------------------------------------------------------------------
# 1. PICKER — manufacturer > device, scanned from the descriptors (zero hand-typing)
# ---------------------------------------------------------------------------
def discover_devices(platform_dir):
    """Scan ``<platform>/devices/*/capabilities.toml`` -> identity rows. Skips non-TSP boards
    that lack a clickable skin (no ``[skin]`` table) so the picker only lists skinned variants."""
    root = os.path.join(platform_dir, "devices")
    out = []
    for did in sorted(os.listdir(root)):
        path = os.path.join(root, did, "capabilities.toml")
        if not os.path.isfile(path):
            continue
        desc = L._toml_load(path)
        if "skin" not in desc:                       # only skinned variants are AVD-pickable
            continue
        ident = desc.get("identity", {})
        out.append({
            "device_id": ident.get("id", did),
            "manufacturer": ident.get("manufacturer", "?"),
            "model": ident.get("model", did),
            "codename": str(ident.get("codename", did)),
        })
    return out


def build_picker(platform_dir):
    """The two-tier leaf: {manufacturer: [ {label, codename, model, device_id}, ... ]}.

    label = ``<codename>  <model>`` (e.g. "5040  Smart Pro") — the Android-AVD device line."""
    tree = {}
    for d in discover_devices(platform_dir):
        leaf = {"label": f"{d['codename']}  {d['model']}",
                "codename": d["codename"], "model": d["model"], "device_id": d["device_id"]}
        tree.setdefault(d["manufacturer"], []).append(leaf)
    for items in tree.values():
        items.sort(key=lambda x: x["codename"])
    return tree


# ---------------------------------------------------------------------------
# 2-4. SKIN — raw rects, hit-test/gesture->action, compositor geometry
# ---------------------------------------------------------------------------
ROTATIONS = {"none": 0, "cw90": 90, "cw180": 180, "cw270": 270}


def _rotated_dims(w, h, rot):
    return (h, w) if rot in ("cw90", "cw270") else (w, h)


class Action:
    """A control_surface call: ``getattr(dev, verb)(input_id, *args)``. Equality lets the proof
    assert a GUI-resolved action is IDENTICAL to the headless test's direct inject."""

    __slots__ = ("verb", "input_id", "args")

    def __init__(self, verb, input_id, *args):
        self.verb = verb
        self.input_id = input_id
        self.args = tuple(args)

    def apply(self, dev):
        return getattr(dev, self.verb)(self.input_id, *self.args)

    def as_tuple(self):
        return (self.verb, self.input_id) + self.args

    def __eq__(self, other):
        return isinstance(other, Action) and self.as_tuple() == other.as_tuple()

    def __hash__(self):
        return hash(self.as_tuple())

    def __repr__(self):
        return f"Action{self.as_tuple()}"


class Part:
    """One clickable skin rect + the inputs (possibly several kinds) that map to it."""

    def __init__(self, name, rect):
        self.name = name
        self.rect = rect                 # (x, y, w, h) in SKIN-image space
        self.inputs = []                 # input dicts mapping here

    def _of_kind(self, *kinds):
        return [i for i in self.inputs if i.get("kind") in kinds]

    @property
    def button(self):
        b = self._of_kind("button")
        return b[0] if b else None

    @property
    def hat(self):
        h = self._of_kind("hat")
        return h[0] if h else None

    @property
    def stick(self):
        s = self._of_kind("stick")
        return s[0] if s else None

    @property
    def stick_click(self):
        s = self._of_kind("stick-click")
        return s[0] if s else None

    @property
    def trigger(self):
        t = self._of_kind("trigger")
        return t[0] if t else None

    @property
    def kind(self):
        """The dominant render/interaction kind (for the renderer + diagnostics)."""
        if self.trigger:
            return "trigger"
        if self.hat:
            return "hat"
        if self.stick:
            return "stick"
        return "button"

    def contains(self, x, y):
        rx, ry, rw, rh = self.rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    def frac(self, x, y):
        """(fx, fy) of a point within the rect, each in [0,1]."""
        rx, ry, rw, rh = self.rect
        fx = 0.0 if rw == 0 else min(1.0, max(0.0, (x - rx) / float(rw)))
        fy = 0.0 if rh == 0 else min(1.0, max(0.0, (y - ry) / float(rh)))
        return fx, fy


class Skin:
    """The clickable skin for one device, in SKIN-image space. Built purely from the descriptor."""

    def __init__(self, device_id, platform_dir):
        self.device_id = device_id
        self.platform_dir = platform_dir
        self.desc = load_descriptor(platform_dir, device_id)

        skin = self.desc.get("skin")
        if not skin:
            raise ValueError(f"{device_id}: descriptor has no [skin] (not AVD-pickable)")
        self.body_path = os.path.join(platform_dir, skin["body"])
        self.lit_body_path = os.path.join(platform_dir, skin["lit_body"])
        self.skin_w, self.skin_h = png_dims(self.body_path)

        screen = self.desc["screens"][0]
        rc = screen["render_canvas"]
        self.canvas_w, self.canvas_h = int(rc["w"]), int(rc["h"])
        dr = screen["display_rect"]
        self.display_rect = (int(dr["x"]), int(dr["y"]), int(dr["w"]), int(dr["h"]))
        self.rotation = screen.get("rotation", "none")

        # Raw [skin.parts] rects, grouped with the inputs that light them (insertion order =
        # descriptor order = paint order).
        raw = skin.get("parts", {})
        self.parts = {}
        self._order = []
        for inp in self.desc.get("inputs", []):
            sp = inp.get("skin_part")
            if not sp:
                continue
            if sp not in raw:
                raise ValueError(f"input '{inp['id']}' references skin_part '{sp}' "
                                 f"absent from [skin.parts]")
            if sp not in self.parts:
                r = raw[sp]
                self.parts[sp] = Part(sp, (int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])))
                self._order.append(sp)
            self.parts[sp].inputs.append(inp)

    # -------- parts / hit-test --------
    def ordered_parts(self):
        return [self.parts[sp] for sp in self._order]

    def part_for_input(self, input_id):
        return L.part_for_input(self.desc, input_id)

    def hit_test(self, x, y):
        """Topmost part containing (x, y) — last-painted wins on overlap. None if outside all."""
        for sp in reversed(self._order):
            if self.parts[sp].contains(x, y):
                return self.parts[sp]
        return None

    # -------- gesture -> action(s) (the shared GUI/test path) --------
    def tap(self, x, y):
        """A TAP (press+release, no drag) at skin point (x, y) -> the action sequence.

        button  -> press,release ; stick w/ stick-click (a523) -> the stick-click press,release ;
        hat -> deflect toward the clicked direction then centre ; stick w/o stick-click (a133) ->
        [] (sticks aren't clickable on the base unit — a pure-DATA difference, no code) ;
        trigger -> set the slider to the clicked fraction (a tap on the slider track)."""
        p = self.hit_test(x, y)
        if p is None:
            return []
        if p.button:
            i = p.button["id"]
            return [Action("press", i), Action("release", i)]
        if p.stick_click:                         # bare tap on a clickable stick -> L3/R3
            i = p.stick_click["id"]
            return [Action("press", i), Action("release", i)]
        if p.hat:
            dx, dy = self._hat_dir(p, x, y)
            return [Action("move_hat", p.hat["id"], dx, dy), Action("move_hat", p.hat["id"], 0, 0)]
        if p.trigger:
            fx, fy = p.frac(x, y)
            return [Action("set_axis", p.trigger["id"], self._slider_value(fx, fy))]
        return []                                  # stick with no stick-click: not clickable

    def drag(self, x, y, to_x, to_y):
        """A DRAG within a part from (x,y) to (to_x,to_y) -> the analog action.

        stick -> set_stick to the normalized offset of the END point from the rect centre
        ([-1,1]^2); trigger -> set_axis to the slider fraction at the END point. A drag that
        starts on a non-analog part is a no-op (digital controls use tap)."""
        p = self.hit_test(x, y)
        if p is None:
            return []
        if p.stick:
            fx, fy = p.frac(to_x, to_y)
            nx, ny = (fx - 0.5) * 2.0, (fy - 0.5) * 2.0
            return [Action("set_stick", p.stick["id"], round(nx, 4), round(ny, 4))]
        if p.trigger:
            fx, fy = p.frac(to_x, to_y)
            return [Action("set_axis", p.trigger["id"], self._slider_value(fx, fy))]
        return []

    @staticmethod
    def _hat_dir(part, x, y):
        """Which d-pad direction a click maps to: the dominant axis of the offset from centre."""
        fx, fy = part.frac(x, y)
        ox, oy = fx - 0.5, fy - 0.5
        if abs(ox) >= abs(oy):
            return (1 if ox > 0 else -1, 0)
        return (0, 1 if oy > 0 else -1)            # evdev ABS_HAT0Y: +1 = down (screen y-down)

    @staticmethod
    def _slider_value(fx, fy):
        """Owner UI = 'slider_above': a horizontal slider; fraction is the x position."""
        return round(fx, 4)

    # -------- compositor geometry (canvas fb -> bezel display_rect) --------
    def composite_rotation(self):
        """DATA-DRIVEN: 'none' or screens.rotation, whichever orientation's aspect matches the
        descriptor's display_rect. For a133/a523 -> 'none' (the landscape canvas). Per-device
        CODE never branches on the SoC; the descriptor's display_rect decides."""
        _, _, dw, dh = self.display_rect
        target = dw / float(dh) if dh else 1.0
        cands = [("none", (self.canvas_w, self.canvas_h)),
                 (self.rotation, _rotated_dims(self.canvas_w, self.canvas_h, self.rotation))]
        best, berr = "none", None
        for rot, (w, h) in cands:
            err = abs((w / float(h) if h else 1.0) - target)
            if berr is None or err < berr:
                best, berr = rot, err
        return best

    def composite_scale(self):
        """The (sx, sy) that STRETCH the (rotation-applied) canvas to FILL display_rect exactly.
        Aspect matches to within ~0.3% so the stretch is imperceptible but coverage is exact —
        and the renderer (skin-render.c) and ``map_canvas_point`` use the SAME mapping so the
        proof's sampled point lands where the renderer drew it."""
        rot = self.composite_rotation()
        sw, sh = _rotated_dims(self.canvas_w, self.canvas_h, rot)
        _, _, dw, dh = self.display_rect
        return (dw / float(sw), dh / float(sh))

    def map_canvas_point(self, cx, cy):
        """Map a point in the app's render_canvas to its (x, y) on the bezel (skin space), after
        the data-driven composite rotation + stretch-fill into display_rect. Used by the proof to
        verify a canvas-lit control lands at the right spot on the composited bezel."""
        rot = self.composite_rotation()
        W, H = self.canvas_w, self.canvas_h
        if rot == "cw90":
            rx, ry = H - 1 - cy, cx
        elif rot == "cw180":
            rx, ry = W - 1 - cx, H - 1 - cy
        elif rot == "cw270":
            rx, ry = cy, W - 1 - cx
        else:
            rx, ry = cx, cy
        sx, sy = self.composite_scale()
        dx, dy, _, _ = self.display_rect
        return (dx + rx * sx, dy + ry * sy)

    # -------- scene emission (the protocol skin-render.c reads) --------
    def lit_parts_for_actions(self, dev, actions):
        """Which skin_parts are currently lit, given the live device state (for the renderer's
        body_lit overlay). We light the skin_part of each input named by a non-zeroing action."""
        lit = set()
        for a in actions:
            sp = self.part_for_input(a.input_id)
            if sp is None:
                continue
            zeroing = (a.verb == "release"
                       or (a.verb == "set_axis" and a.args and a.args[0] == 0.0)
                       or (a.verb == "set_stick" and a.args[:2] == (0.0, 0.0))
                       or (a.verb == "move_hat" and a.args[:2] == (0, 0)))
            (lit.discard if zeroing else lit.add)(sp)
        return lit

    def emit_scene(self, body_ppm, lit_body_ppm, fb_ppm, lit_parts, *, title="", picker=None,
                   selected=None):
        """The whitespace protocol skin-render.c parses (robust strtok in C):

            skin <body_ppm> <lit_body_ppm> <skin_w> <skin_h>
            display <x> <y> <w> <h> <composite_rotation>
            fb <fb_ppm|-> <canvas_w> <canvas_h>
            part <skin_part> <kind> <x> <y> <w> <h> <lit:0|1>     (one per skin rect)
            picker <manufacturer> <codename> <selected:0|1> <model...>   (model = rest-of-line,
                                                                          may contain spaces)
            title <text...>

        NOTE: paths (body/lit_body/fb) must be space-free (they are, under the sim baseline dir);
        only <model> and <title> may contain spaces, and both are the rest-of-line.
        """
        lines = [f"skin {body_ppm} {lit_body_ppm} {self.skin_w} {self.skin_h}"]
        x, y, w, h = self.display_rect
        lines.append(f"display {x} {y} {w} {h} {self.composite_rotation()}")
        fb = fb_ppm if fb_ppm else "-"
        lines.append(f"fb {fb} {self.canvas_w} {self.canvas_h}")
        for p in self.ordered_parts():
            rx, ry, rw, rh = p.rect
            lines.append(f"part {p.name} {p.kind} {rx} {ry} {rw} {rh} "
                         f"{1 if p.name in lit_parts else 0}")
        if picker:
            for man, items in picker.items():
                for it in items:
                    sel = 1 if it["device_id"] == selected else 0
                    lines.append(f"picker {man} {it['codename']} {sel} {it['model']}")
        if title:
            lines.append(f"title {title}")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI (device-free smoke checks)
# ---------------------------------------------------------------------------
def _cmd_picker(a):
    print(json.dumps(build_picker(a.platform), indent=2))
    return 0


def _cmd_show(a):
    s = Skin(a.device, a.platform)
    out = {
        "device": s.device_id,
        "skin": [s.skin_w, s.skin_h],
        "canvas": [s.canvas_w, s.canvas_h],
        "display_rect": list(s.display_rect),
        "rotation": s.rotation,
        "composite_rotation": s.composite_rotation(),
        "composite_scale": [round(v, 5) for v in s.composite_scale()],
        "parts": [{"name": p.name, "kind": p.kind, "rect": list(p.rect),
                   "inputs": [i["id"] for i in p.inputs]} for p in s.ordered_parts()],
    }
    print(json.dumps(out, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("picker"); p.add_argument("--platform", required=True)
    p = sub.add_parser("show")
    p.add_argument("--device", required=True); p.add_argument("--platform", required=True)
    a = ap.parse_args()
    return _cmd_picker(a) if a.cmd == "picker" else _cmd_show(a)


if __name__ == "__main__":
    sys.exit(main())
