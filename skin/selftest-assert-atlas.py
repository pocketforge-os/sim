#!/usr/bin/env python3
"""selftest-assert-atlas.py — tsp-w3kx: the DEMONSTRATED NEGATIVE CONTROL for assert-atlas.py.

``assert-atlas.py`` is a guard, and the bug this selftest was written for is that it *could not
fail* for the reason it exists: it built ONE ``Skin`` (active view = ``front``) and never iterated
the others, so a device's top-view hit-boxes were compared against NOTHING. Perturbing a top-view
rect still exited 0. A check-shaped absence keeps reading as coverage — running the guard and
reading green told you it RAN, never that it COULD FAIL.

So this file does not assert that assert-atlas passes. It CONSTRUCTS the broken states, watches
each one go RED, and asserts it went red **for its own reason** (the failure MESSAGE, not merely a
non-zero exit) — while the rows that must NOT weaken stay green. Each row builds a scratch platform
tree (real descriptor + atlas copies, symlinked PNGs) and runs the real ``assert-atlas.py`` against
it as a subprocess, exactly as an operator or CI would.

    row (a)  top-view rect perturbed          -> RED, naming view `top`      [the regression test:
                                                                              exits 0 pre-fix]
    row (b)  front-view rect perturbed        -> RED, naming view `front`    [pre-existing coverage
                                                                              not lost in the rewrite]
    row (c)  control bound ONLY in a non-front view -> GREEN                 [no false FAIL — the
                                                                              opposite-direction defect]
    row (d)  pristine real a133 + a523        -> GREEN
    row (e)  atlas control bound in NO view   -> RED                         [the reverse-direction
                                                                              guard keeps its teeth]
    row (f)  descriptor part absent from that view's atlas -> RED, naming view `top`
    row (g)  descriptor view with no atlas counterpart     -> RED, naming view `rear`

Rows (c) and (e) are the same *construction* (drop a part from ``[skin.parts]``) applied to two
different controls — one bound in another view, one bound nowhere. They must land on OPPOSITE
verdicts; a fix that made everything pass would collapse them, and a fix that kept the old blanket
FAIL would collapse them the other way.

INPUTS ARE MANDATORY. If the platform tree or an expected descriptor/atlas file is missing this
exits 2 LOUDLY — it never SKIPs and contributes nothing to the exit code, because CI is exactly the
environment where the inputs go missing and a fail-open selftest is worth nothing there.

Usage: selftest-assert-atlas.py [--platform DIR]     (default PLATFORM env, else /opt/pf/platform)
Exit 0 = every row behaved as asserted. Exit 1 = a row misbehaved. Exit 2 = inputs unusable.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ASSERT_ATLAS = os.path.join(HERE, "assert-atlas.py")

# The two model-rendered devices. Both carry a `top` view in BOTH the descriptor and the atlas.
DEVICES = ("a133", "a523")


class InputError(Exception):
    """A required input is missing/unusable — always fatal, never a skip."""


# ---------------------------------------------------------------------------
# scratch platform: real files for what we mutate, symlinks for the heavy PNGs
# ---------------------------------------------------------------------------
def _require(path, what):
    if not os.path.exists(path):
        raise InputError(f"{what} missing: {path}")
    return path


def check_inputs(platform_dir):
    """Fail LOUDLY (never skip) unless every file every row needs is present and parseable."""
    _require(platform_dir, "platform dir")
    _require(ASSERT_ATLAS, "assert-atlas.py")
    for dev in DEVICES:
        _require(os.path.join(platform_dir, "devices", dev, "capabilities.toml"),
                 f"{dev} descriptor")
        atlas_path = _require(os.path.join(platform_dir, "skins", dev, "model-render.json"),
                              f"{dev} model-render.json")
        with open(atlas_path) as f:
            atlas = json.load(f)
        if "top" not in atlas.get("views", {}):
            raise InputError(
                f"{dev} model-render.json has no views.top — this selftest's whole subject is "
                f"NON-FRONT view coverage, so without it the rows would pass vacuously "
                f"(views present: {sorted(atlas.get('views', {}))})")
        for png in ("body.png", "body_lit.png", "body_top.png", "body_lit_top.png"):
            _require(os.path.join(platform_dir, "skins", dev, png), f"{dev} {png}")


def make_scratch(platform_dir, dest, devices=DEVICES):
    """A minimal writable platform: capabilities.toml + model-render.json COPIED (we mutate
    them), the bezel PNGs SYMLINKED (skin_model only reads their header for dims)."""
    for dev in devices:
        d_dev = os.path.join(dest, "devices", dev)
        d_skin = os.path.join(dest, "skins", dev)
        os.makedirs(d_dev)
        os.makedirs(d_skin)
        shutil.copy2(os.path.join(platform_dir, "devices", dev, "capabilities.toml"),
                     os.path.join(d_dev, "capabilities.toml"))
        src_skin = os.path.join(platform_dir, "skins", dev)
        for name in os.listdir(src_skin):
            src = os.path.join(src_skin, name)
            if name == "model-render.json":
                shutil.copy2(src, os.path.join(d_skin, name))
            elif os.path.isfile(src):
                os.symlink(os.path.abspath(src), os.path.join(d_skin, name))
    return dest


def _desc_path(scratch, dev):
    return os.path.join(scratch, "devices", dev, "capabilities.toml")


# ---------------------------------------------------------------------------
# descriptor surgery — targeted, section-scoped line edits on the TOML text
# ---------------------------------------------------------------------------
def _section_span(text, section):
    """(start, end) character span of the BODY of ``[section]`` (header excluded)."""
    m = re.search(r"^\[" + re.escape(section) + r"\]\s*$", text, re.M)
    if not m:
        raise InputError(f"descriptor has no [{section}] section (the row's premise is gone)")
    start = m.end()
    nxt = re.search(r"^\[", text[start:], re.M)
    return start, (start + nxt.start() if nxt else len(text))


def _part_line(text, section, part):
    start, end = _section_span(text, section)
    m = re.search(r"^" + re.escape(part) + r"\s*=.*$", text[start:end], re.M)
    if not m:
        raise InputError(f"[{section}] has no '{part}' row (the row's premise is gone)")
    return start + m.start(), start + m.end(), m.group(0)


def set_rect(text, section, part, **deltas):
    """Nudge one or more of x/y/w/h on ``part`` inside ``[section]``. Returns (text, old, new)."""
    lo, hi, line = _part_line(text, section, part)
    new_line = line
    for key, delta in deltas.items():
        m = re.search(rf"\b{key}\s*=\s*(\d+)", new_line)
        if not m:
            raise InputError(f"[{section}].{part} has no '{key}' field")
        new_line = new_line[:m.start(1)] + str(int(m.group(1)) + delta) + new_line[m.end(1):]
    return text[:lo] + new_line + text[hi:], line, new_line


def del_part(text, section, part):
    lo, hi, line = _part_line(text, section, part)
    tail = text[hi:hi + 1]
    return text[:lo] + text[hi + (1 if tail == "\n" else 0):], line


def add_part(text, section, part, rect):
    """Insert a part row at the top of ``[section]``."""
    start, _ = _section_span(text, section)
    row = (f"\n{part} = {{ x = {rect[0]}, y = {rect[1]}, "
           f"w = {rect[2]}, h = {rect[3]} }}")
    return text[:start] + row + text[start:]


def add_view(text, name, body, lit_body, parts):
    """Append a whole ``[skin.views.<name>]`` block (body/lit_body + a parts table)."""
    block = [f"\n[skin.views.{name}]",
             f'body     = "{body}"',
             f'lit_body = "{lit_body}"',
             "",
             f"[skin.views.{name}.parts]"]
    for part, (x, y, w, h) in parts.items():
        block.append(f"{part} = {{ x = {x}, y = {y}, w = {w}, h = {h} }}")
    return text.rstrip("\n") + "\n" + "\n".join(block) + "\n"


def edit_descriptor(scratch, dev, fn):
    path = _desc_path(scratch, dev)
    with open(path) as f:
        text = f.read()
    text = fn(text)
    with open(path, "w") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------
def run_assert_atlas(scratch, devices):
    p = subprocess.run([sys.executable, ASSERT_ATLAS, "--platform", scratch, *devices],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class Row:
    """One selftest row: build a broken (or pristine) platform, run the guard, assert the verdict
    AND — when it must fail — the failure MESSAGE, so a row cannot pass by failing for the wrong
    reason (an exit code alone cannot tell a working guard from a broken one)."""

    def __init__(self, key, what, build, expect_red, must_match=(), must_not_match=(),
                 devices=DEVICES, note=""):
        self.key = key
        self.what = what
        self.build = build            # fn(scratch) -> None (mutates the scratch platform)
        self.expect_red = expect_red
        self.must_match = must_match          # regexes required in the output when RED
        self.must_not_match = must_not_match  # regexes that must be ABSENT
        self.devices = devices
        self.note = note

    def run(self, platform_dir):
        with tempfile.TemporaryDirectory(prefix=f"selftest-atlas-{self.key}-") as tmp:
            scratch = make_scratch(platform_dir, os.path.join(tmp, "platform"))
            self.build(scratch)
            rc, out = run_assert_atlas(scratch, self.devices)
        problems = []
        got_red = rc != 0
        if got_red != self.expect_red:
            problems.append(f"expected {'RED' if self.expect_red else 'GREEN'} "
                            f"but exit={rc} ({'RED' if got_red else 'GREEN'})")
        for pat in self.must_match:
            if not re.search(pat, out, re.M):
                problems.append(f"output does not match required /{pat}/")
        for pat in self.must_not_match:
            if re.search(pat, out, re.M):
                problems.append(f"output matches forbidden /{pat}/")
        return problems, rc, out


def build_rows():
    rows = []

    # (a) THE REGRESSION TEST. A top-view rect drifts from the atlas. Pre-fix this exits 0 —
    #     the top view's rects were compared against nothing at all.
    rows.append(Row(
        "a", "top-view rect perturbed (a133 [skin.views.top.parts].btn_l1 x+1) -> RED naming `top`",
        lambda s: edit_descriptor(s, "a133",
                                  lambda t: set_rect(t, "skin.views.top.parts", "btn_l1", x=1)[0]),
        expect_red=True,
        must_match=[r"^FAIL\s+view top btn_l1:.*\(61, 310, 193, 26\).*\(60, 310, 193, 26\)"],
        note="exits 0 pre-fix — this row IS the bug"))

    # (b) The coverage that already existed must survive the rewrite.
    rows.append(Row(
        "b", "front-view rect perturbed (a133 [skin.parts].btn_l1 x+1) -> RED naming `front`",
        lambda s: edit_descriptor(s, "a133",
                                  lambda t: set_rect(t, "skin.parts", "btn_l1", x=1)[0]),
        expect_red=True,
        must_match=[r"^FAIL\s+view front btn_l1:.*\(76, 94, 189, 53\).*\(75, 94, 189, 53\)"]))

    # (c) THE OPPOSITE-DIRECTION DEFECT. btn_l1 is dropped from [skin.parts] and kept in
    #     [skin.views.top.parts] — a control the descriptor exposes ONLY in a non-front view.
    #     The atlas is untouched, so its front `controls` still carries a btn_l1 rect. Pre-fix,
    #     `set(controls) - set(skin.parts)` reported that as "NO descriptor [skin.parts] binding"
    #     — a wrong finding about correct data. It must be GREEN, and it must NOT be reported as
    #     unbound anywhere.
    rows.append(Row(
        "c", "control bound ONLY in the top view (a133 btn_l1 dropped from [skin.parts]) -> GREEN",
        lambda s: edit_descriptor(s, "a133",
                                  lambda t: del_part(t, "skin.parts", "btn_l1")[0]),
        expect_red=False,
        must_not_match=[r"^FAIL.*btn_l1"],
        note="pre-fix this is a FALSE FAIL"))

    # (d) The real, unmodified descriptors + atlases.
    rows.append(Row(
        "d", "pristine real a133 + a523 descriptors -> GREEN",
        lambda s: None,
        expect_red=False,
        must_not_match=[r"^FAIL"]))

    # (e) Same construction as (c), different control: btn_guide is bound in NO other view, so
    #     dropping it leaves a rendered-but-unbound rect. The reverse-direction guard must keep
    #     its teeth here — (c) and (e) landing on the same verdict would mean the fix either
    #     softened everything or fixed nothing.
    rows.append(Row(
        "e", "atlas control bound in NO view (a133 btn_guide dropped from [skin.parts]) -> RED",
        lambda s: edit_descriptor(s, "a133",
                                  lambda t: del_part(t, "skin.parts", "btn_guide")[0]),
        expect_red=True,
        must_match=[r"^FAIL\s+view front btn_guide:.*NO descriptor.*binding in ANY view"]))

    # (f) Forward direction, non-front view: the descriptor binds a part in `top` that the top
    #     atlas does not render.
    rows.append(Row(
        "f", "descriptor part absent from that view's atlas (a133 dpad added to top) -> RED",
        lambda s: edit_descriptor(s, "a133",
                                  lambda t: add_part(t, "skin.views.top.parts", "dpad",
                                                     (10, 10, 20, 20))),
        expect_red=True,
        must_match=[r"^FAIL\s+view top dpad:.*ABSENT from model-render\.json view top"]))

    # (g) A whole descriptor view the atlas never rendered (body points at the existing top PNGs
    #     so skin_model can still read their dims — the missing thing under test is the ATLAS
    #     view, not the image).
    rows.append(Row(
        "g", "descriptor view with no atlas counterpart (a133 [skin.views.rear]) -> RED",
        lambda s: edit_descriptor(s, "a133", lambda t: add_view(
            t, "rear", "skins/a133/body_top.png", "skins/a133/body_lit_top.png",
            {"trig_l": (52, 255, 200, 55)})),
        expect_red=True,
        must_match=[r"^FAIL\s+view rear:.*model-render\.json has no views\.rear"]))

    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", default=os.environ.get("PLATFORM", "/opt/pf/platform"))
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print each row's full assert-atlas output")
    a = ap.parse_args()

    try:
        check_inputs(a.platform)
    except (InputError, ValueError, json.JSONDecodeError) as e:
        # FAIL LOUDLY. A selftest that skips on missing input is fail-open, and CI is exactly
        # where the input goes missing.
        print(f"SELFTEST INPUT ERROR: {e}", file=sys.stderr)
        print("selftest-assert-atlas: FAIL (inputs unusable — NOT skipped)", file=sys.stderr)
        return 2

    print(f"selftest-assert-atlas: platform={a.platform}")
    bad = 0
    for row in build_rows():
        try:
            problems, rc, out = row.run(a.platform)
        except (InputError, OSError) as e:
            print(f"  FAIL  ({row.key}) {row.what}\n        input error: {e}")
            bad += 1
            continue
        verdict = "RED" if rc != 0 else "GREEN"
        tag = "  ok  " if not problems else "  FAIL"
        suffix = f"   [{row.note}]" if row.note else ""
        print(f"{tag}  ({row.key}) {row.what}\n        observed {verdict} (exit {rc}){suffix}")
        for p in problems:
            print(f"        !! {p}")
        if problems or a.verbose:
            print("        --- assert-atlas output ---")
            for line in out.rstrip("\n").split("\n"):
                print(f"        | {line}")
        bad += 1 if problems else 0

    n = len(build_rows())
    print(f"\nselftest-assert-atlas: {'PASS' if not bad else 'FAIL'} "
          f"({n - bad}/{n} rows behaved as asserted)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
