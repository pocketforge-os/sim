#!/usr/bin/env python3
"""selftest_skin_model_optional.py — tsp-bu5e: the NEGATIVE CONTROL for skin_model's handling of
the three OPTIONAL descriptor fields it used to index directly.

WHY THIS FILE EXISTS. ``skin_model.Skin()`` used to do ``screen["display_rect"]``,
``skin["lit_body"]`` and ``vskin["lit_body"]``. All three are OPTIONAL in platform's
``schemas/capabilities.schema.json``, so for a descriptor whose own validator printed
``OK <dev>: capabilities valid``, ``Skin()`` raised a bare ``KeyError`` from inside its
constructor. That divergence was the bug (tsp-bu5e); this file is the proof it is fixed AND — the
part that matters — the proof that the fix did not simply make everything pass.

THE BAR (memory ``guards-must-be-shown-to-fail``): a passing check tells you it RAN, never that it
CAN FAIL for the reason you care about. So every row here CONSTRUCTS a specific descriptor state
and asserts the SPECIFIC observable outcome — for the failing rows, the failure MESSAGE, not merely
that something was raised. Rows that must fail for different reasons are asserted to fail for their
OWN reasons; otherwise the suite cannot tell a working guard from a broken one.

WHAT IT WAS MEASURED TO CATCH (tsp-bu5e; re-runnable, see skin/README.md "Optional descriptor
fields"). Pointed at the PRE-FIX ``skin_model.py`` all 10 rows go red. The row count that actually
proves something, though, is against the HARD adversary: the same fix with the SAME public surface
(``has_lit_body``, ``display_rect = None``, the warnings) but with the bogus arm weakened so a
present-but-unresolvable value degrades like an absent one — i.e. the plausible-and-wrong
"degrade everything" fix. That variant fails EXACTLY the four B* rows and keeps every A*/C* row
green, which is the evidence that the B arm carries its own weight rather than being incidentally
covered: absence is a declaration and degrades, a present-but-unresolvable value is a claim that
failed and stays loud (the same absent-vs-bogus split ``control/layout.py``:99-104 already draws).

NO SKIPS. If the platform descriptors are unreachable, or no longer carry the fields these rows
mutate, this exits NON-ZERO and says so. A selftest that SKIPs on missing input is fail-open and
contributes nothing to CI's exit code, which is precisely what CI would see.

    PLATFORM=/path/to/platform python3 skin/selftest_skin_model_optional.py
    python3 skin/selftest_skin_model_optional.py --platform /path/to/platform
    pf-sim selftest-skin-optional            # in the container (docker/entrypoint.sh)

Device-free, stdlib only, no qemu/rootfs/uinput. Exit 0 = PASS, 1 = a row failed, 2 = inputs
unusable (loud, never a skip).
"""
import argparse
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "control"))
sys.path.insert(0, os.path.join(HERE, "..", "fb"))

import skin_model as SM                                      # noqa: E402
import layout as L                                           # noqa: E402

DEV = "a133"          # the mutation subject; a523 is exercised unmutated in the GREEN rows
VIEW = "top"          # the edge view both descriptors carry (tsp-65jc.27)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------
class Rows:
    """Per-ROW verdicts, and a row is never allowed to abort the suite.

    This matters for the adversarial re-run: pointed at BROKEN code a row can blow up in a way
    the row did not anticipate (the pre-fix model has no ``has_lit_body`` at all, so the very
    first assertion raises ``AttributeError``). If that propagated, the suite would die on row 1
    and report one crash instead of N red rows — which is exactly the number a reviewer re-running
    this against the pre-fix state needs to count. So an unexpected exception inside a row is
    recorded as that row's FAILURE and the remaining rows still run."""

    def __init__(self):
        self.fails = []
        self.tag = "?"

    def chk(self, cond, msg):
        print(("  ok  " if cond else "FAIL  ") + msg)
        if not cond:
            self.fails.append(f"[{self.tag}] {msg}")
        return bool(cond)

    @contextlib.contextmanager
    def row(self, tag):
        self.tag = tag
        print(f"\n---- {tag} ----")
        try:
            yield
        except Exception as e:                               # noqa: BLE001 — deliberate, see above
            self.chk(False, f"row raised {type(e).__name__}: {e} — the row could not even run "
                            f"its assertions against this code")


def die(msg):
    """Inputs unusable. LOUD and non-zero — never a skip (see the module docstring)."""
    sys.stderr.write(f"selftest_skin_model_optional: FATAL {msg}\n")
    sys.exit(2)


def _mkplatform(real, edits):
    """A scratch platform dir: real ``skins/`` (symlinked — the art must resolve) + a REAL
    ``devices/`` tree whose descriptors we may rewrite. Returns the dir.

    ``edits`` maps device id -> a function over the descriptor TEXT. Each edit must actually
    change the text; an edit that matched nothing would leave the row testing the unmodified
    descriptor and silently reporting PASS (the vacuous-check trap), so it is a hard error."""
    d = tempfile.mkdtemp(prefix="pf-selftest-skin-")
    os.symlink(os.path.join(real, "skins"), os.path.join(d, "skins"))
    for dev in os.listdir(os.path.join(real, "devices")):
        src = os.path.join(real, "devices", dev, "capabilities.toml")
        if not os.path.isfile(src):
            continue
        os.makedirs(os.path.join(d, "devices", dev))
        text = open(src).read()
        if dev in edits:
            new = edits[dev](text)
            if new == text:
                die(f"mutation for '{dev}' changed nothing — the descriptor at {src} no longer "
                    f"has the shape this selftest rewrites, so the row would test NOTHING. "
                    f"Fix the mutation (or the descriptor), do not ignore this.")
            text = new
        open(os.path.join(d, "devices", dev, "capabilities.toml"), "w").write(text)
    return d


def _build(platform_dir, dev=DEV):
    """Build a Skin, capturing stderr. -> (skin_or_None, error_or_None, warn_text)."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            sk = SM.Skin(dev, platform_dir)
        return sk, None, err.getvalue()
    except Exception as e:                                   # noqa: BLE001 — the row asserts which
        return None, e, err.getvalue()


# The mutations below are matched on SHAPE (a `key = value` assignment), never on the descriptor's
# exact spelling of a value. Pinning literal text like `w = 816` would make these rows break — or
# worse, silently stop mutating — the first time the descriptor is re-measured. Any mutation that
# matches nothing is caught by _mkplatform and is FATAL, so a shape that stops matching is loud.
_ASSIGN = r"^[ \t]*{key}[ \t]*=.*$"


def _drop_key(key):
    """Delete the ``<key> = ...`` assignment line (not a comment that merely mentions the key)."""
    def fn(text):
        return re.sub(_ASSIGN.format(key=re.escape(key)) + "\n?", "", text, count=1, flags=re.M)
    return fn


def _lit_edit(view, value):
    """Edit ``lit_body`` inside ONE table: ``view='front'`` -> ``[skin]``, else
    ``[skin.views.<view>]``. ``value=None`` deletes the key (the ABSENT case), otherwise it is
    the new TOML value (the PRESENT-but-unresolvable case).

    Section-scoped on purpose: both descriptors carry two ``lit_body`` assignments, so a
    first-match rewrite would silently always hit ``[skin]`` and the per-view rows would be
    testing the front view twice while reporting the view's name."""
    header = r"^\[skin\]$" if view == "front" else r"^\[skin\.views\." + re.escape(view) + r"\]$"

    def fn(text):
        m = re.search(header, text, flags=re.M)
        if not m:
            return text                       # -> _mkplatform's "changed nothing" fatal
        nxt = re.search(r"^\[", text[m.end():], flags=re.M)
        end = m.end() + (nxt.start() if nxt else len(text) - m.end())
        head, body, rest = text[:m.end()], text[m.end():end], text[end:]
        pat = _ASSIGN.format(key="lit_body")
        body = (re.sub(pat + "\n?", "", body, count=1, flags=re.M) if value is None
                else re.sub(pat, f"lit_body = {value}", body, count=1, flags=re.M))
        return head + body + rest
    return fn


def _edit_display_rect(**kw):
    """Rewrite ``display_rect`` from its CURRENT values: ``drop='w'`` removes that key,
    ``w=0`` overrides it. Reads the live numbers out of the descriptor rather than assuming
    them, so a re-measured panel does not quietly turn these rows into no-ops."""
    def fn(text):
        m = re.search(_ASSIGN.format(key="display_rect"), text, flags=re.M)
        if not m:
            return text                       # -> _mkplatform's "changed nothing" fatal
        cur = dict(re.findall(r"(\w+)\s*=\s*(-?\d+)", m.group(0)))
        cur.update({k: str(v) for k, v in kw.items() if k != "drop"})
        cur.pop(kw.get("drop", ""), None)
        body = ", ".join(f"{k} = {cur[k]}" for k in ("x", "y", "w", "h") if k in cur)
        return text[:m.start()] + "display_rect = { " + body + " }" + text[m.end():]
    return fn


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------
def _green_row(real, r, dev):
    """C: the unmodified shipped descriptor. The row we must NOT weaken."""
    with r.row(f"C {dev}: unmodified descriptor -> GREEN"):
        sk, err, warns = _build(real, dev)
        if not r.chk(err is None, f"{dev} Skin() builds without error (got {err!r})"):
            return
        r.chk(sk.display_rect is not None,
              f"{dev} front carries a display_rect ({sk.display_rect})")
        r.chk(sk.views["front"].has_lit_body, f"{dev} front has_lit_body is True")
        r.chk(VIEW in sk.views and sk.views[VIEW].has_lit_body,
              f"{dev} '{VIEW}' view has_lit_body is True")
        # A degradation warning on a COMPLETE descriptor would mean the fix degrades unprompted.
        r.chk(warns.strip() == "", f"{dev} emits NO degradation warning (got {warns.strip()!r})")


def _a1_display_rect_absent(real, r):
    with r.row("A1 screens[0].display_rect ABSENT -> front composites no fb"):
        p = _mkplatform(real, {DEV: _drop_key("display_rect")})
        try:
            sk, err, warns = _build(p)
            if not r.chk(err is None,
                         f"Skin() does not raise (was KeyError: 'display_rect'; got {err!r})"):
                return
            r.chk(sk.display_rect is None, f"front display_rect is None (got {sk.display_rect!r})")
            r.chk("declares no display_rect" in warns,
                  f"warns that the front view composites NO framebuffer "
                  f"(stderr: {warns.strip()!r})")
            scene = sk.emit_scene("b.ppm", "l.ppm", "fb.ppm", set()).splitlines()
            # VISIBLE in the output: a missing display_rect must not read like a working screen.
            r.chk("display 0 0 0 0 none" in scene,
                  f"scene emits 'display 0 0 0 0 none' "
                  f"(got {[s for s in scene if s.startswith('display')]})")
            r.chk(any(s.startswith("fb - ") for s in scene),
                  f"scene emits 'fb -' — no framebuffer composited "
                  f"(got {[s for s in scene if s.startswith('fb')]})")
        finally:
            shutil.rmtree(p, ignore_errors=True)


def _a2_compositor_refuses(real, r):
    """The anti-'silently substitute a zero rect' row: the compositor must REFUSE, not compute."""
    with r.row("A2 screens[0].display_rect ABSENT -> compositor refuses, does NOT return zeros"):
        p = _mkplatform(real, {DEV: _drop_key("display_rect")})
        try:
            sk, err, _ = _build(p)
            if not r.chk(err is None, f"Skin() builds (got {err!r})"):
                return
            for meth, args in (("composite_rotation", ()), ("composite_scale", ()),
                               ("map_canvas_point", (10, 10))):
                try:
                    got = getattr(sk, meth)(*args)
                    r.chk(False, f"{meth}() must refuse without a display_rect, returned {got!r} "
                                 f"(a substituted zero rect would let downstream asserts run "
                                 f"against nothing)")
                except ValueError as e:
                    r.chk("has no display_rect" in str(e), f"{meth}() -> ValueError '{e}'")
                except Exception as e:                       # noqa: BLE001
                    r.chk(False, f"{meth}() raised {type(e).__name__}: {e} — wanted a designed "
                                 f"ValueError, not an incidental unpack error")
        finally:
            shutil.rmtree(p, ignore_errors=True)


def _lit_absent_row(real, r, tag, edit, where, view, other):
    """A3/A4: an ABSENT lit_body degrades to the body art, says so, and only for THAT view."""
    with r.row(tag):
        p = _mkplatform(real, {DEV: edit})
        try:
            sk, err, warns = _build(p)
            if not r.chk(err is None,
                         f"Skin() does not raise (was KeyError: 'lit_body'; got {err!r})"):
                return
            v = sk.views[view]
            r.chk(v.has_lit_body is False,
                  f"'{view}' has_lit_body is False (got {v.has_lit_body!r})")
            r.chk(v.lit_body_path == v.body_path,
                  f"'{view}' lit art falls back to its body art "
                  f"({os.path.basename(v.lit_body_path)})")
            r.chk(f"{where} declares no lit_body" in warns,
                  f"warns, naming {where} and the view (stderr: {warns.strip()!r})")
            r.chk(sk.views[other].has_lit_body is True,
                  f"the '{other}' view is UNAFFECTED (has_lit_body still True) — the fallback is "
                  f"per-view, not global")
        finally:
            shutil.rmtree(p, ignore_errors=True)


def _bogus_row(real, r, tag, edit, want):
    """B*: PRESENT-BUT-UNRESOLVABLE -> RED, and red for ITS OWN asserted reason.

    These are the rows a blanket ``.get()`` cannot pass. Each asserts its own message, so the four
    of them cannot be satisfied by one indiscriminate failure."""
    with r.row(f"{tag} -> RED"):
        p = _mkplatform(real, {DEV: edit})
        try:
            sk, err, _ = _build(p)
            if not r.chk(err is not None,
                         "Skin() RAISES (a degraded pass here would neuter the guard)"):
                return
            r.chk(isinstance(err, ValueError),
                  f"raises a DESIGNED ValueError (got {type(err).__name__}) — a KeyError or a "
                  f"TypeError here would be the old crash wearing a different hat")
            r.chk(want in str(err),
                  f"...for ITS OWN reason: message contains {want!r} (got {str(err)!r})")
        finally:
            shutil.rmtree(p, ignore_errors=True)


def run(real, r):
    # ===== C. the unmodified real descriptors — the rows we must NOT weaken =====
    for dev in ("a133", "a523"):
        _green_row(real, r, dev)

    # ===== A. ABSENT optional fields -> documented, VISIBLE degradation, no KeyError =====
    _a1_display_rect_absent(real, r)
    _a2_compositor_refuses(real, r)
    _lit_absent_row(real, r,
                    "A3 [skin].lit_body ABSENT -> front falls back to body, flagged",
                    _lit_edit("front", None), "[skin]", "front", VIEW)
    _lit_absent_row(real, r,
                    f"A4 [skin.views.{VIEW}].lit_body ABSENT -> that view falls back, front OK",
                    _lit_edit(VIEW, None), f"[skin.views.{VIEW}]",
                    VIEW, "front")

    # ===== B. PRESENT-BUT-UNRESOLVABLE -> RED, each for its OWN asserted reason =====
    for tag, edit, want in (
        ("B1 display_rect PRESENT but missing 'w'",
         _edit_display_rect(drop="w"),
         "screens[0].display_rect: rect is missing 'w'"),
        ("B2 display_rect PRESENT with w = 0",
         _edit_display_rect(w=0),
         "rect 'w' = 0 must be >= 1"),
        ("B3 [skin].lit_body PRESENT but naming a file that is not there",
         _lit_edit("front", f'"skins/{DEV}/no_such_lit.png"'),
         f"[skin] lit_body: declares art 'skins/{DEV}/no_such_lit.png' but no such file"),
        (f"B4 [skin.views.{VIEW}].lit_body PRESENT but naming a file that is not there",
         _lit_edit(VIEW, f'"skins/{DEV}/no_such_lit_top.png"'),
         f"[skin.views.{VIEW}] lit_body: declares art 'skins/{DEV}/no_such_lit_top.png' "
         f"but no such file"),
    ):
        _bogus_row(real, r, tag, edit, want)


def main():
    ap = argparse.ArgumentParser(description="negative control for skin_model's optional fields")
    ap.add_argument("--platform", default=os.environ.get("PLATFORM"),
                    help="platform checkout (default: $PLATFORM)")
    a = ap.parse_args()

    # --- inputs must be USABLE. Every failure below is fatal and non-zero; none is a skip. ---
    real = a.platform
    if not real:
        die("no platform dir — set PLATFORM or pass --platform. (Refusing to skip: a selftest "
            "that skips on missing input contributes nothing to CI's exit code.)")
    if not os.path.isdir(os.path.join(real, "devices")):
        die(f"{real} has no devices/ — not a platform checkout")
    if not os.path.isdir(os.path.join(real, "skins")):
        die(f"{real} has no skins/ — the bezel art must resolve for these rows to mean anything")
    for dev in ("a133", "a523"):
        if not os.path.isfile(os.path.join(real, "devices", dev, "capabilities.toml")):
            die(f"{real}/devices/{dev}/capabilities.toml missing — this selftest asserts on both "
                f"shipped descriptors")
    # The rows below mutate specific descriptor text. If the descriptor no longer carries it, the
    # rows would pass while testing nothing — so check up front rather than discover it as a PASS.
    desc = open(os.path.join(real, "devices", DEV, "capabilities.toml")).read()
    for needle in ("display_rect", f'lit_body = "skins/{DEV}/body_lit.png"',
                   f'lit_body = "skins/{DEV}/body_lit_top.png"'):
        if needle not in desc:
            die(f"devices/{DEV}/capabilities.toml no longer contains {needle!r} — the rows that "
                f"mutate it would test NOTHING. Update this selftest to match the descriptor.")
    d = L.load_descriptor(real, DEV)
    if VIEW not in d.get("skin", {}).get("views", {}):
        die(f"devices/{DEV} has no [skin.views.{VIEW}] — the per-view rows would test nothing")

    print(f"selftest_skin_model_optional — platform={real}")
    r = Rows()
    run(real, r)
    print(f"\n{'PASS' if not r.fails else 'FAIL'}: "
          f"{len(r.fails)} failing assertion(s)" + (":" if r.fails else ""))
    for f in r.fails:
        print(f"  - {f}")
    return 1 if r.fails else 0


if __name__ == "__main__":
    sys.exit(main())
