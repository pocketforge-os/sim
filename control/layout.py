#!/usr/bin/env python3
"""layout.py — tsp-an4.5: the SHARED descriptor->canvas layout (one descriptor, two consumers).

This is the single source of truth that makes the headline contract honest:

    dev.press("south"); assert dev.framebuffer_region("south").is_red()

The app (``hwprobe-lite.c``) and the host test (``check-control.py``) must agree on WHERE on
the 1280x720 render canvas each control is drawn — otherwise "region is red" would be
meaningless. They agree because BOTH derive the rect from THIS module: the host writes a
``layout.txt`` for the app to draw from, and the host's own ``framebuffer_region``/``slider``
asserts read the SAME rects from ``compute_layout``. There is exactly ONE computation; no
hand-typed coordinates, no drift (the discipline that caught the platform KEY_HOMEPAGE bug).

The rects come from the descriptor's AVD-style clickable skin: each ``[[inputs]]`` row carries
a ``skin_part`` that names a rect in ``[skin.parts]`` (skin-image pixel space). We fit the
bounding box of the USED parts into the descriptor's ``screens[0].render_canvas`` (aspect-
preserving, centered) — so a133 and a523 differ ONLY by descriptor rows (zero per-device code),
and the *same* skin_part table the GUI (C6) will click on drives the headless region asserts.

GROUPING: several inputs can share one skin_part (a523's ``l3``/``r3`` stick-clicks reuse the
``stick_l``/``stick_r`` stick parts). A control GROUP = one skin_part rect + the UNION of the
evdev codes of every input mapping to it. A group renders as a TRIGGER (proportional fill) iff
its sole input is a trigger; otherwise SOLID (lit iff any key code is pressed or any abs axis is
displaced past its deadzone). This mirrors the real device: clicking the stick or deflecting it
both light the same drawn part.

Pure data; device-free; stdlib only. Importable + CLI (``layout.py emit --device ... --platform
... --nodes /dev/input/eventN`` prints the layout.txt the app reads).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "synth"))
import evdev_codes as ec  # noqa: E402  (the .3 kernel-ABI code table — generated, not hand-typed)

try:
    import tomllib
    def _toml_load(p):
        with open(p, "rb") as f:
            return tomllib.load(f)
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore
    def _toml_load(p):
        with open(p, "rb") as f:
            return tomllib.load(f)

EV_KEY = ec.EV["EV_KEY"]   # 1
EV_ABS = ec.EV["EV_ABS"]   # 3


def load_descriptor(platform_dir, device_id):
    path = os.path.join(platform_dir, "devices", device_id, "capabilities.toml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no capabilities.toml for '{device_id}' at {path}")
    return _toml_load(path)


def _abs_range(inp, code_name):
    """(min, max) for an ABS code, from the descriptor (hat axes are the classic -1..1)."""
    if code_name.startswith("ABS_HAT"):
        return (-1, 1)
    kind = inp.get("kind")
    if kind in ("stick", "stick-click"):
        codes = [c for c in inp["code"].split(",") if c]
        ax = inp.get("x") if (codes and code_name == codes[0]) else inp.get("y")
    else:  # trigger / single axis
        ax = inp.get("range")
    if ax is None:
        return (0, 0)
    return (int(ax.get("min", 0)), int(ax.get("max", 0)))


class Group:
    """One drawn skin part = one rect + the union of codes that light it."""

    def __init__(self, skin_part, rect_skin):
        self.skin_part = skin_part
        self.rect_skin = rect_skin          # (x, y, w, h) in skin-image space
        self.render = "button"              # "button" | "trigger" | "hat" | "stick"
        self.codes = []                     # [(ev_type:int, code:int, vmin, vmax, role)]
        self.input_ids = []                 # which input ids map here (for diagnostics)
        self.canvas = None                  # (x, y, w, h) on the render canvas, after fit


def compute_layout(desc):
    """Descriptor -> (canvas dict, [Group]). The heart of zero-per-device-code rendering.

    canvas = {"w", "h", "rotation"}; groups carry both skin-space and canvas-space rects."""
    screen = desc["screens"][0]
    rc = screen["render_canvas"]
    canvas = {"w": int(rc["w"]), "h": int(rc["h"]), "rotation": screen.get("rotation", "none")}

    parts = desc.get("skin", {}).get("parts", {})
    groups = {}        # skin_part -> Group (insertion-ordered: first input wins position)
    order = []
    for inp in desc.get("inputs", []):
        sp = inp.get("skin_part")
        if not sp:
            continue
        if sp not in parts:
            raise ValueError(f"input '{inp['id']}' references skin_part '{sp}' "
                             f"absent from [skin.parts]")
        if sp not in groups:
            r = parts[sp]
            groups[sp] = Group(sp, (int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])))
            order.append(sp)
        g = groups[sp]
        g.input_ids.append(inp["id"])

        names = [c for c in inp["code"].split(",") if c]
        ev = EV_KEY if inp["ev_type"] == "EV_KEY" else EV_ABS
        kind = inp.get("kind")
        for idx, n in enumerate(names):
            code = ec.code_value(n)
            vmin, vmax = _abs_range(inp, n) if ev == EV_ABS else (0, 0)
            # role lets the app draw direction WITHOUT hand-typing ABI codes: a digital press
            # ("k"); a trigger axis ("t"); the first/second axis of a stick or hat ("x"/"y").
            if ev == EV_KEY:
                role = "k"
            elif kind == "trigger":
                role = "t"
            else:
                role = "x" if idx == 0 else "y"
            g.codes.append((ev, code, vmin, vmax, role))
        # render KIND drives the app widget (button / trigger / hat cross / stick calibration box);
        # trigger wins, then hat, then stick — a stick-click shares the stick part and keeps "stick".
        if kind == "trigger":
            g.render = "trigger"
        elif kind == "hat" and g.render != "trigger":
            g.render = "hat"
        elif kind == "stick" and g.render not in ("trigger", "hat"):
            g.render = "stick"

    ordered = [groups[sp] for sp in order]
    _fit(canvas, ordered)
    return canvas, ordered


def _fit(canvas, groups, margin=0.94):
    """Aspect-preserving, centered fit of the USED skin parts' bounding box into the render
    canvas. Distinct skin rects stay distinct (so lighting the wrong control is detectable)."""
    if not groups:
        return
    minx = min(g.rect_skin[0] for g in groups)
    miny = min(g.rect_skin[1] for g in groups)
    maxx = max(g.rect_skin[0] + g.rect_skin[2] for g in groups)
    maxy = max(g.rect_skin[1] + g.rect_skin[3] for g in groups)
    bw, bh = maxx - minx, maxy - miny
    W, H = canvas["w"], canvas["h"]
    s = min(W / bw, H / bh) * margin
    ox = (W - bw * s) / 2.0 - minx * s
    oy = (H - bh * s) / 2.0 - miny * s
    for g in groups:
        x, y, w, h = g.rect_skin
        g.canvas = (int(round(x * s + ox)), int(round(y * s + oy)),
                    max(1, int(round(w * s))), max(1, int(round(h * s))))


# ---------------------------------------------------------------------------------------------
# The SHARED skin_part gate (tsp-3x7d) — ONE predicate, both region-asserting checkers.
#
# `skin_part` is OPTIONAL in platform's schema (`$defs.input.required = ["id","kind","ev_type",
# "code"]`), and so is `class` (enum gamepad|system, default gamepad). A row may legitimately
# validate with NEITHER — the A133/A523 volume rocker (tsp-bwrg.16) is exactly that: a REAL
# physical control on the LRADC node with no drawable front-skin region, filed `class = "system"`.
# `check-control.py` / `check-skin.py` used to index `inp["skin_part"]` directly and KeyError'd on
# such a row.
#
# The predicate composes the two facts rather than picking one:
#
#     skin_part PRESENT      -> a region assertion is POSSIBLE  -> assert (as before)
#     absent + class=system  -> its absence is LEGITIMATE       -> skip (nothing to assert)
#     absent + anything else -> its absence is a DEFECT         -> FAIL the check
#
# Why not the simpler "skip whenever skin_part is absent": that predicate is total by
# construction, so a `class=gamepad` row that SHOULD carry a skin_part but lost one — precisely
# the drift these checks exist to catch — would pass silently forever, converting a real check
# into a decorative one. See the `guards-must-be-shown-to-fail` discipline; the negative control
# in `selftest_region_guard.py` pins every arm of this predicate.
#
# Why not a defaulting `.get()`: a `None` skin_part only RELOCATES the crash —
# `control_surface._rect_for()` raises `KeyError: no drawable region for '<id>'` when the group
# lookup misses. Callers must `continue`, never default.
#
# NOTE the deliberate asymmetry with the ValueError below: "no region declared" (skip/fail here)
# and "region declared but bogus" (`compute_layout`'s ValueError, above) stay DIFFERENT outcomes,
# so a typo'd skin_part can never hide behind this gate.
# ---------------------------------------------------------------------------------------------

REGION_ASSERT = "assert"    # row carries a skin_part: run the region assertions
REGION_SKIP = "skip"        # class=system with no skin_part: nothing drawable to assert
REGION_FAIL = "fail"        # no skin_part and not class=system: a defect, fail the check

DEFAULT_INPUT_CLASS = "gamepad"   # platform schema default for [[inputs]].class


def region_disposition(inp):
    """Classify ONE ``[[inputs]]`` row for the region-asserting checkers.

    Returns ``(disposition, skin_part_or_None, message)`` where disposition is one of
    ``REGION_ASSERT`` / ``REGION_SKIP`` / ``REGION_FAIL``. The message is always populated so a
    caller can put the reason in its transcript — a skip that leaves no trace reads exactly like
    coverage that never existed."""
    iid = inp.get("id", "<unnamed>")
    sp = inp.get("skin_part")
    if sp:
        return (REGION_ASSERT, sp, f"'{iid}' -> skin_part '{sp}'")
    cls = inp.get("class", DEFAULT_INPUT_CLASS)
    if cls == "system":
        return (REGION_SKIP, None,
                f"skip '{iid}': class=system and no skin_part — ambient system control with no "
                f"drawable region, nothing for a region assertion to say")
    return (REGION_FAIL, None,
            f"input '{iid}' is class={cls}"
            f"{' (default)' if 'class' not in inp else ''} but declares no skin_part — a gamepad "
            f"control the skin can neither draw nor click is either a descriptor omission or "
            f"should be class=system")


def region_rows(inputs, chk):
    """Yield ``(inp, skin_part)`` for every row a region assertion is POSSIBLE for.

    Rows that legitimately have no drawable region are consumed here (recorded as a passing check
    so the skip is visible in the transcript); rows that are missing a skin_part they should have
    are consumed here too, as a FAILED check with an actionable message. Callers therefore never
    see a row without a usable ``skin_part`` and never need their own guard.

    ``chk`` is the checker's ``Checker.chk(cond, msg)`` — identical in both checkers."""
    for inp in inputs:
        disp, sp, msg = region_disposition(inp)
        if disp == REGION_ASSERT:
            yield inp, sp
        elif disp == REGION_SKIP:
            chk(True, msg)
        else:
            chk(False, msg)


def part_for_input(desc, input_id):
    """Resolve an input id -> its skin_part (the region key)."""
    for inp in desc.get("inputs", []):
        if inp["id"] == input_id:
            return inp.get("skin_part")
    return None


def group_for_key(canvas_groups, key):
    """Resolve a region key (skin_part OR — via the caller — an input id already mapped) to its
    Group. Accepts a skin_part name directly."""
    for g in canvas_groups:
        if g.skin_part == key:
            return g
    return None


def emit_layout_txt(canvas, groups, nodes):
    """The exact text the app reads. Whitespace table (robust strtok parsing in C):

        canvas <W> <H> <rotation>
        node <path>                          (one per evdev node)
        ctl <skin_part> <kind> <x> <y> <w> <h> <ncodes> [<evtype> <code> <min> <max> <role>]*

    kind in {button,trigger,hat,stick}; evtype in {1,3}; role in {k,t,x,y}. CANVAS space."""
    lines = [f"canvas {canvas['w']} {canvas['h']} {canvas['rotation']}"]
    for n in nodes:
        lines.append(f"node {n}")
    for g in groups:
        x, y, w, h = g.canvas
        parts = [f"ctl {g.skin_part} {g.render} {x} {y} {w} {h} {len(g.codes)}"]
        for (ev, code, vmin, vmax, role) in g.codes:
            parts.append(f"{ev} {code} {vmin} {vmax} {role}")
        lines.append(" ".join(parts))
    return "\n".join(lines) + "\n"


def cmd_emit(a):
    desc = load_descriptor(a.platform, a.device)
    canvas, groups = compute_layout(desc)
    sys.stdout.write(emit_layout_txt(canvas, groups, a.nodes or []))
    return 0


def cmd_show(a):
    import json
    desc = load_descriptor(a.platform, a.device)
    canvas, groups = compute_layout(desc)
    out = {"device": a.device, "canvas": canvas,
           "groups": [{"skin_part": g.skin_part, "render": g.render, "canvas": g.canvas,
                       "inputs": g.input_ids,
                       "codes": [{"ev": e, "code": c, "min": mn, "max": mx, "role": ro}
                                 for (e, c, mn, mx, ro) in g.codes]} for g in groups]}
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("emit", "show"):
        p = sub.add_parser(name)
        p.add_argument("--device", required=True)
        p.add_argument("--platform", required=True)
        if name == "emit":
            p.add_argument("--nodes", nargs="*", help="evdev node paths to embed")
    a = ap.parse_args()
    return cmd_emit(a) if a.cmd == "emit" else cmd_show(a)


if __name__ == "__main__":
    sys.exit(main())
