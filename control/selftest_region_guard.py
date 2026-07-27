#!/usr/bin/env python3
"""selftest_region_guard.py — tsp-3x7d: the NEGATIVE CONTROL for the shared skin_part gate.

A guard you have not seen FAIL is not a guard. `layout.region_rows()` decides, for every
``[[inputs]]`` row, whether a region assertion is possible / legitimately impossible / a defect —
so the only way to know it still guards anything is to construct each broken state and watch the
right arm fire, for the right REASON.

This file is that proof, kept as a running test rather than a transcript: a transcript proves it
failed once, a selftest keeps proving it. It is DEVICE-FREE (no qemu, no uinput, no root, stdlib
only) and runs as a pre-pass inside ``check-control.py`` — so it executes on every ``./sim check``
and in the sim-gate CI job, with no workflow edit needed.

ROWS (each shown to go the stated way; A and B fail for DIFFERENT, individually asserted reasons):

  A  class=gamepad row with skin_part REMOVED      -> RED   (region_rows -> FAILED check; the
                                                             message text is asserted, not just
                                                             "something failed")
  B  row with a BOGUS skin_part (not in the parts  -> RED   (ValueError from compute_layout —
     table)                                                  proven to fire from inside a real
                                                             `Device(...)` construction, which is
                                                             what BOTH checkers do)
  C  class=system row with NO skin_part            -> GREEN (skipped, no crash, skip recorded)
  D  the unmodified real descriptors               -> GREEN (zero failures, Device constructs)
  E  the PRE-FIX statements, verbatim              -> KeyError on C's row, AND the direct-index
                                                     text is absent from both checkers today
  F  class=system row that DOES carry a skin_part  -> still ASSERTED (class alone never exempts)
  G  the CHECKERS' OWN input loops, driven         -> each REPORTS the stripped row itself
  H  four adjacent shapes                          -> explicit gamepad / unknown class / empty
                                                     skin_part / system+bogus all behave

ROW G EXISTS BECAUSE A-F WERE NOT ENOUGH, and the gap is worth naming because it is the exact
disease this whole bead is about. Rows A-F/H exercise ``layout.region_rows`` IN ISOLATION, and row
E only greps for the removed literal. Nothing in that set asserts the CHECKERS actually CALL the
gate — so the guard defended the LIBRARY and not the WIRING. The coordinator proved the hole
rather than theorising it: rewriting both checkers' loops to a local
``sp = inp.get("skin_part"); if not sp: continue`` — the blanket-skip anti-pattern the ruling
rejects, and the single most likely future regression because it looks tidier — passed this file
at exit 0 while silently dropping a gamepad row that had lost its skin_part. Row G drives the
checkers' REAL loop functions against a real (unbooted) Device and asserts each checker reports
the stripped row ITSELF, so a call site that stops consulting the gate goes red. A source scan for
``L.region_rows(`` rides along as belt-and-suspenders, never as the primary assertion.

Row A is the one that matters most: the plausible-and-wrong fix — skip whenever ``skin_part`` is
absent — is total by construction and would turn row A green, silently converting a real check
into a decorative one. If A ever goes green, the gate has been neutered.

FAIL-LOUD CONTRACT: every input this test needs is REQUIRED. A missing descriptor, an
unreadable checker source, an un-importable module — each is a FAILURE, never a skip. In CI an
absent input is exactly what a silently-broken gate looks like.

Run standalone:   python3 control/selftest_region_guard.py --platform /opt/pf/platform
"""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import layout as L  # noqa: E402

# The descriptors this control REQUIRES. Absent => FAIL (never skip). Override deliberately with
# PF_REGION_GUARD_DEVICES="a133 a523 ..." when a platform pin genuinely ships a different set.
REQUIRED_DEVICES = tuple(
    (os.environ.get("PF_REGION_GUARD_DEVICES") or "a133 a523").split()
)

# The EXACT statements this bead removed, reconstructed verbatim. Row E executes them against a
# class=system row (they raise KeyError — the bug) and then asserts the indexing text is gone from
# both checkers, so the pair proves the guard replaced something real rather than decorating it.
PRE_FIX_STATEMENTS = {
    "check-control.py": 'iid, sp = inp["id"], inp["skin_part"]',
    "check-skin.py": 'iid, kind, sp = inp["id"], inp.get("kind"), inp["skin_part"]',
}
CHECKER_SOURCES = {
    "check-control.py": os.path.join(HERE, "check-control.py"),
    "check-skin.py": os.path.join(HERE, "..", "skin", "check-skin.py"),
}
DIRECT_INDEX = 'inp["skin_part"]'

BOGUS_PART = "btn_definitely_not_a_real_skin_part"
SYSTEM_ROW_ID = "pf_selftest_system_row"


class Checker:
    """Same shape as the checkers' own Checker (chk(cond, msg) -> bool, collects failures)."""

    def __init__(self, tag):
        self.tag, self.fails = tag, []

    def chk(self, cond, msg):
        print(("  ok  " if cond else "FAIL  ") + msg)
        if not cond:
            self.fails.append(msg)
        return bool(cond)


class _Recorder:
    """A chk sink that RECORDS instead of judging — this test asserts on what the gate reported,
    so the gate's own failures must not leak into our result."""

    def __init__(self):
        self.passed, self.failed = [], []

    def chk(self, cond, msg):
        (self.passed if cond else self.failed).append(msg)
        return bool(cond)


# ---------------------------------------------------------------------------------------------
# descriptor text surgery — we mutate the REAL descriptor's text, so the broken states under test
# are the real rows with one thing wrong, not a hand-built fixture that could drift from reality.
# ---------------------------------------------------------------------------------------------

def _read_descriptor_text(platform_dir, device_id):
    path = os.path.join(platform_dir, "devices", device_id, "capabilities.toml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no capabilities.toml for '{device_id}' at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _input_block_span(lines, input_id):
    """(start, end) line indices of the ``[[inputs]]`` block whose ``id`` is ``input_id``."""
    start = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == "[[inputs]]":
            start = i
            continue
        if start is not None and s.startswith("["):
            start = None
            continue
        if start is not None and s.startswith("id ") and "=" in s:
            if s.split("=", 1)[1].strip().strip('"') == input_id:
                end = len(lines)
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("["):
                        end = j
                        break
                return start, end
    raise LookupError(f"no [[inputs]] row with id = {input_id!r}")


def _drop_skin_part(text, input_id):
    lines = text.splitlines()
    a, b = _input_block_span(lines, input_id)
    kept = [ln for k, ln in enumerate(lines)
            if not (a <= k < b and ln.strip().startswith("skin_part"))]
    if len(kept) == len(lines):
        raise LookupError(f"row {input_id!r} has no skin_part line to drop")
    return "\n".join(kept) + "\n"


def _rewrite_skin_part(text, input_id, new_value):
    lines = text.splitlines()
    a, b = _input_block_span(lines, input_id)
    hit = False
    for k in range(a, b):
        if lines[k].strip().startswith("skin_part"):
            lines[k] = f'skin_part = "{new_value}"'
            hit = True
    if not hit:
        raise LookupError(f"row {input_id!r} has no skin_part line to rewrite")
    return "\n".join(lines) + "\n"


def _append_system_row(text, input_id):
    """Append the post-tsp-bwrg.16 shape: class=system, NO skin_part.

    Synthesized rather than read from the descriptor ON PURPOSE — the baked platform pin may
    predate the real VOL± rows, and this control must pin the predicate on BOTH sides of that pin
    bump. When the pin does catch up, row D exercises the real rows too."""
    return text.rstrip("\n") + (
        "\n\n[[inputs]]\n"
        f'id = "{input_id}"\n'
        'kind = "button"\n'
        'ev_type = "EV_KEY"\n'
        'code = "KEY_VOLUMEUP"\n'
        'class = "system"\n'
    )


def _mutated_platform(platform_dir, device_id, new_text, root):
    """A throwaway platform tree: symlinks to the real one, with ONE descriptor replaced."""
    top = os.path.join(root, device_id)
    os.makedirs(os.path.join(top, "devices", device_id), exist_ok=True)
    for entry in os.listdir(platform_dir):
        if entry == "devices":
            continue
        os.symlink(os.path.join(platform_dir, entry), os.path.join(top, entry))
    real_devices = os.path.join(platform_dir, "devices")
    for entry in os.listdir(real_devices):
        if entry == device_id:
            continue
        os.symlink(os.path.join(real_devices, entry), os.path.join(top, "devices", entry))
    real_dev_dir = os.path.join(real_devices, device_id)
    for entry in os.listdir(real_dev_dir):
        if entry == "capabilities.toml":
            continue
        os.symlink(os.path.join(real_dev_dir, entry),
                   os.path.join(top, "devices", device_id, entry))
    with open(os.path.join(top, "devices", device_id, "capabilities.toml"), "w",
              encoding="utf-8") as f:
        f.write(new_text)
    return top


def _first_drawable_gamepad_row(desc):
    """The row rows A/B mutate: a normal, non-system control that carries a skin_part."""
    for inp in desc.get("inputs", []):
        if inp.get("skin_part") and inp.get("class", L.DEFAULT_INPUT_CLASS) != "system":
            return inp
    raise LookupError("descriptor has no non-system input carrying a skin_part")


def _construct_device(platform_dir, device_id):
    """Construct (never boot) a real ``Device``. ``Device.__init__`` calls ``compute_layout``, so
    this is the honest proof that the ValueError path fires from INSIDE both checkers — both build
    a Device exactly this way before any of their loops run. Import is deferred so a checker that
    already imported control_surface pays nothing twice."""
    import control_surface as CS  # noqa: E402
    return CS.Device(device_id, platform_dir, launcher="native",
                     outdir=os.path.join(tempfile.gettempdir(), f"region-guard-{device_id}"))


# ---------------------------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------------------------

def _row_A_missing_skin_part_on_gamepad(c, desc, device_id):
    """A: a gamepad row that LOST its skin_part must go RED, with an actionable message."""
    victim = _first_drawable_gamepad_row(desc)
    vid = victim["id"]
    broken = dict(victim)
    broken.pop("skin_part")
    rec = _Recorder()
    yielded = list(L.region_rows([broken], rec.chk))

    c.chk(not yielded,
          f"[A/{device_id}] gamepad '{vid}' minus skin_part yields NO assertable row "
          f"(got {len(yielded)})")
    c.chk(len(rec.failed) == 1 and not rec.passed,
          f"[A/{device_id}] gamepad '{vid}' minus skin_part -> exactly 1 FAILED check "
          f"(failed={len(rec.failed)}, passed={len(rec.passed)})")
    msg = rec.failed[0] if rec.failed else ""
    # Assert the TEXT, not merely "it failed": A and B must be distinguishable from each other.
    for needle in (f"'{vid}'", "class=gamepad", "no skin_part", "class=system"):
        c.chk(needle in msg,
              f"[A/{device_id}] failure message names {needle!r} (msg: {msg!r})")
    c.chk("absent from [skin.parts]" not in msg,
          f"[A/{device_id}] failure message is the MISSING-skin_part message, NOT the bogus-"
          f"skin_part one (the two must stay distinguishable)")


def _row_B_bogus_skin_part(c, platform_dir, desc, device_id, tmproot):
    """B: a PRESENT but unknown skin_part must stay a HARD ValueError — and must fire from inside
    a real Device construction, which is the path both checkers actually take."""
    victim = _first_drawable_gamepad_row(desc)
    vid = victim["id"]

    # B1: the gate does NOT swallow it — the row is still handed out for assertion...
    rec = _Recorder()
    bogus_row = dict(victim, skin_part=BOGUS_PART)
    yielded = list(L.region_rows([bogus_row], rec.chk))
    c.chk(len(yielded) == 1 and not rec.failed and not rec.passed,
          f"[B/{device_id}] a bogus skin_part is NOT consumed by the skin_part gate "
          f"(it is compute_layout's job) — yielded={len(yielded)} failed={len(rec.failed)}")

    # B2: ...and compute_layout raises, naming the offending part.
    text = _rewrite_skin_part(_read_descriptor_text(platform_dir, device_id), vid, BOGUS_PART)
    mut = _mutated_platform(platform_dir, device_id, text, os.path.join(tmproot, "bogus"))
    bad_desc = L.load_descriptor(mut, device_id)
    try:
        L.compute_layout(bad_desc)
        c.chk(False, f"[B/{device_id}] compute_layout ACCEPTED bogus skin_part {BOGUS_PART!r} "
                     f"— the typo path is dead")
    except ValueError as e:
        c.chk(BOGUS_PART in str(e) and "absent from [skin.parts]" in str(e),
              f"[B/{device_id}] compute_layout -> ValueError naming the bogus part ({e})")

    # B3: the SAME ValueError fires from `Device(...)` — the construction both checkers perform
    # before any loop runs, so a typo cannot reach (or hide behind) the per-view parts guard.
    try:
        _construct_device(mut, device_id)
        c.chk(False, f"[B/{device_id}] Device(...) constructed with a bogus skin_part — the "
                     f"checkers would NOT see the ValueError")
    except ValueError as e:
        c.chk(BOGUS_PART in str(e),
              f"[B/{device_id}] Device(...) construction raises the same ValueError ({e}) "
              f"— the hard-error path is reachable from inside BOTH checkers")


def _row_C_system_row_skipped(c, platform_dir, device_id, tmproot):
    """C: class=system with no skin_part is skipped — green, no crash, and the skip is RECORDED."""
    text = _append_system_row(_read_descriptor_text(platform_dir, device_id), SYSTEM_ROW_ID)
    mut = _mutated_platform(platform_dir, device_id, text, os.path.join(tmproot, "system"))
    desc = L.load_descriptor(mut, device_id)
    sys_row = next((i for i in desc["inputs"] if i["id"] == SYSTEM_ROW_ID), None)
    if sys_row is None:
        c.chk(False, f"[C/{device_id}] synthesized class=system row did not parse back — "
                     f"the mutation itself is broken")
        return None

    rec = _Recorder()
    yielded = list(L.region_rows([sys_row], rec.chk))
    c.chk(not yielded and not rec.failed and len(rec.passed) == 1,
          f"[C/{device_id}] class=system row with no skin_part -> skipped, no failure "
          f"(yielded={len(yielded)} failed={len(rec.failed)} passed={len(rec.passed)})")
    c.chk(rec.passed and SYSTEM_ROW_ID in rec.passed[0] and "class=system" in rec.passed[0],
          f"[C/{device_id}] the skip is VISIBLE in the transcript "
          f"({rec.passed[0] if rec.passed else '<nothing recorded>'})")

    # And the whole mutated descriptor still walks + constructs cleanly (this is the KeyError).
    rec2 = _Recorder()
    list(L.region_rows(desc["inputs"], rec2.chk))
    c.chk(not rec2.failed,
          f"[C/{device_id}] full descriptor + a class=system row -> 0 failures "
          f"(got {rec2.failed})")
    try:
        _construct_device(mut, device_id)
        c.chk(True, f"[C/{device_id}] Device(...) constructs with a skin_part-less system row")
    except Exception as e:  # noqa: BLE001 — any exception here is the bug this bead fixes
        c.chk(False, f"[C/{device_id}] Device(...) raised on a skin_part-less system row: "
                     f"{type(e).__name__}: {e}")
    return sys_row


def _row_D_real_descriptor_green(c, desc, platform_dir, device_id):
    """D: the unmodified real descriptor produces ZERO failures and every row it hands out is
    usable. This is the row that goes green today on the pinned platform AND after the pin bump
    brings in the real class=system VOL± rows."""
    rec = _Recorder()
    rows = list(L.region_rows(desc.get("inputs", []), rec.chk))
    c.chk(not rec.failed,
          f"[D/{device_id}] unmodified descriptor -> 0 failures from the skin_part gate "
          f"(got {rec.failed})")
    c.chk(bool(rows),
          f"[D/{device_id}] unmodified descriptor still yields assertable rows "
          f"({len(rows)} of {len(desc.get('inputs', []))}) — the gate did not swallow everything")
    parts = desc.get("skin", {}).get("parts", {})
    orphan = [sp for _, sp in rows if sp not in parts]
    c.chk(not orphan, f"[D/{device_id}] every yielded skin_part exists in [skin.parts] ({orphan})")
    skipped = len(desc.get("inputs", [])) - len(rows)
    print(f"  ..  [D/{device_id}] {len(rows)} assertable / {skipped} skipped "
          f"({len(rec.passed)} recorded skips)")


def _row_E_pre_fix_code_crashes(c, sys_row):
    """E: the code this bead REMOVED raises KeyError on exactly the row the gate now handles —
    and that code is gone from both checkers. Either half alone proves nothing: the first shows
    the failure was real, the second shows it is no longer reachable."""
    if sys_row is None:
        c.chk(False, "[E] no class=system row available — cannot prove the pre-fix crash")
        return
    for name, stmt in PRE_FIX_STATEMENTS.items():
        try:
            exec(compile(stmt, f"<pre-fix {name}>", "exec"), {}, {"inp": dict(sys_row)})
            c.chk(False, f"[E] pre-fix statement from {name} did NOT raise on a class=system "
                         f"row — the guard guards nothing: {stmt}")
        except KeyError as e:
            c.chk("skin_part" in str(e),
                  f"[E] pre-fix statement from {name} raises KeyError({e}) on a class=system row "
                  f"— this is the bug tsp-3x7d fixes")

    for name, path in CHECKER_SOURCES.items():
        if not os.path.isfile(path):
            c.chk(False, f"[E] checker source {name} not found at {path} — cannot verify the "
                         f"direct index is gone (absent input FAILS, never skips)")
            continue
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        offenders = [ln.strip() for ln in src.splitlines()
                     if DIRECT_INDEX in ln and not ln.strip().startswith("#")]
        c.chk(not offenders,
              f"[E] {name} contains no un-guarded {DIRECT_INDEX} outside a comment "
              f"(found: {offenders})")


def _load_checker(name):
    """Import a checker by PATH — their filenames carry dashes, so they are not importable by
    name. A fresh module object each call, so a spy installed on one does not leak into the other.
    Importing runs only module-level code (both checkers' work is inside functions)."""
    path = CHECKER_SOURCES[name]
    if not os.path.isfile(path):
        raise FileNotFoundError(f"checker source {name} not found at {path}")
    spec = importlib.util.spec_from_file_location(
        "pf_region_guard_" + name.replace("-", "_").replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stub_device_class():
    """A real ``control_surface.Device`` with only the DEVICE-touching methods replaced.

    Subclassing (rather than duck-typing a fake) keeps every piece of real logic row G depends on:
    ``Device.__init__`` still loads the descriptor and runs ``compute_layout``, ``has_input`` still
    falls back to the descriptor rows when nothing was synthesized, and the broker / capability /
    pose surface is the genuine article. Only uinput, qemu, and the framebuffer are stubbed —
    exactly the parts that need hardware."""
    import control_surface as CS  # noqa: E402

    class _Region:
        def __init__(self, lit):
            self._lit = lit

        def is_red(self):
            return self._lit

    class _Slider:
        def __init__(self, frac):
            self._frac = frac

        def fraction(self):
            return self._frac

        def at(self, value, tol=0.06):
            return abs(self._frac - value) <= tol

    class _StubDevice(CS.Device):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._lit = set()
            self._axis = {}

        # lifecycle: never boot anything
        def boot(self):
            return self

        def shutdown(self):
            return None

        def __enter__(self):
            return self.boot()

        def __exit__(self, *exc):
            return False

        def snapshot(self, name=None):
            return name or "auto"

        def _sp(self, input_id):
            return __import__("layout").part_for_input(self.desc, input_id)

        # injection: model "the named control's region is lit", nothing more
        def press(self, input_id):
            self._require(input_id)
            sp = self._sp(input_id)
            if sp:
                self._lit.add(sp)

        def release(self, input_id):
            self._require(input_id)
            self._lit.discard(self._sp(input_id))

        def move_hat(self, input_id, x, y):
            self._require(input_id)
            sp = self._sp(input_id)
            if sp:
                self._lit.add(sp) if (x, y) != (0, 0) else self._lit.discard(sp)

        def set_stick(self, input_id, x, y, normalized=True):
            self._require(input_id)
            sp = self._sp(input_id)
            if sp:
                self._lit.add(sp) if (x * x + y * y) ** 0.5 > 0.2 else self._lit.discard(sp)

        def set_axis(self, input_id, value, normalized=True):
            self._require(input_id)
            sp = self._sp(input_id)
            if sp:
                self._axis[sp] = float(value)
                self._lit.add(sp) if float(value) > 0.02 else self._lit.discard(sp)

        def framebuffer_region(self, key):
            rect, sp = self._rect_for(key)
            return _Region(sp in self._lit)

        def slider(self, key):
            rect, sp = self._rect_for(key)
            return _Slider(self._axis.get(sp, 0.0))

    return _StubDevice


class _Spy:
    """Wraps ``layout.region_rows`` so row G can prove the checker CALLED it — the one assertion
    that survives even if the stubbed run trips over something further down."""

    def __init__(self, real):
        self._real = real
        self.calls = 0

    def __call__(self, inputs, chk):
        self.calls += 1
        return self._real(inputs, chk)


def _row_G_checkers_call_the_gate(c, platform_dir, desc, device_id, tmproot):
    """G: drive each checker's REAL input loop against a descriptor whose gamepad row lost its
    skin_part, and assert the CHECKER reports it. This is the wiring proof rows A-F cannot give."""
    victim = _first_drawable_gamepad_row(desc)
    vid = victim["id"]
    text = _drop_skin_part(_read_descriptor_text(platform_dir, device_id), vid)
    mut = _mutated_platform(platform_dir, device_id, text, os.path.join(tmproot, "wiring"))
    stub_cls = _stub_device_class()
    outdir = os.path.join(tmproot, "wiring-out")
    os.makedirs(os.path.join(outdir, "frames"), exist_ok=True)

    def _reported(fails):
        return [m for m in fails if vid in m and "no skin_part" in m]

    # --- check-control.py: run_scenario() over the stubbed device ---
    try:
        cc = _load_checker("check-control.py")
        spy = _Spy(cc.L.region_rows)
        cc.L.region_rows = spy
        try:
            dev = stub_cls(device_id, mut, launcher="native", outdir=outdir)
            inner = cc.Checker("stub")
            # Swallow the CHECKER's own transcript: it is SUPPOSED to print FAIL lines here (that
            # is the evidence), but letting them through makes this file's output look like it
            # failed — and a reader scanning for ^FAIL would be misled about which run failed.
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    cc.run_scenario(dev, inner)
            except Exception as e:  # noqa: BLE001 — the stub may trip later; the gate ran first
                print(f"  ..  [G/{device_id}] check-control run_scenario stopped early "
                      f"({type(e).__name__}: {e}) — asserting on what it recorded before that")
            c.chk(spy.calls > 0,
                  f"[G/{device_id}] check-control's loops CALL layout.region_rows "
                  f"({spy.calls} call(s)) — a local .get()+continue would call it 0 times")
            hits = _reported(inner.fails)
            c.chk(bool(hits),
                  f"[G/{device_id}] check-control REPORTS '{vid}' losing its skin_part "
                  f"(fails={inner.fails})")
        finally:
            cc.L.region_rows = spy._real
    except Exception as e:  # noqa: BLE001
        c.chk(False, f"[G/{device_id}] could not drive check-control's loop: "
                     f"{type(e).__name__}: {e}")

    # --- check-skin.py: run_device() over the stubbed device (do_render=False, native) ---
    try:
        cs = _load_checker("check-skin.py")
        spy = _Spy(cs.L.region_rows)
        real_device = cs.Device
        cs.L.region_rows = spy
        cs.Device = stub_cls
        try:
            captured = {}
            real_checker = cs.Checker

            class _Capturing(real_checker):
                def __init__(self, tag):
                    super().__init__(tag)
                    captured.setdefault("fails", self.fails)

            cs.Checker = _Capturing
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    cs.run_device(device_id, mut, "native", outdir, (None, None, None, None),
                                  do_render=False)
            except Exception as e:  # noqa: BLE001
                print(f"  ..  [G/{device_id}] check-skin run_device stopped early "
                      f"({type(e).__name__}: {e}) — asserting on what it recorded before that")
            finally:
                cs.Checker = real_checker
            c.chk(spy.calls > 0,
                  f"[G/{device_id}] check-skin's input loop CALLS layout.region_rows "
                  f"({spy.calls} call(s)) — a local .get()+continue would call it 0 times")
            hits = _reported(captured.get("fails") or [])
            c.chk(bool(hits),
                  f"[G/{device_id}] check-skin REPORTS '{vid}' losing its skin_part "
                  f"(fails={captured.get('fails')})")
        finally:
            cs.L.region_rows = spy._real
            cs.Device = real_device
    except Exception as e:  # noqa: BLE001
        c.chk(False, f"[G/{device_id}] could not drive check-skin's loop: "
                     f"{type(e).__name__}: {e}")

    # --- belt-and-suspenders only: the call is visible in the source of BOTH ---
    for name, path in CHECKER_SOURCES.items():
        if not os.path.isfile(path):
            c.chk(False, f"[G] checker source {name} not found at {path}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        c.chk("L.region_rows(" in src,
              f"[G] {name} calls L.region_rows( in source (secondary check — the behavioural "
              f"assertions above are the real ones)")


def _row_H_adjacent_shapes(c, desc, device_id):
    """H: four schema-legal shapes that behave correctly but were previously untested."""
    victim = _first_drawable_gamepad_row(desc)
    base = dict(victim)
    base.pop("skin_part")

    # H1: EXPLICIT class="gamepad" (row A only covers the class-ABSENT default path)
    rec = _Recorder()
    list(L.region_rows([dict(base, **{"class": "gamepad"})], rec.chk))
    c.chk(len(rec.failed) == 1 and "(default)" not in rec.failed[0],
          f"[H/{device_id}] explicit class=gamepad without skin_part -> FAIL, and the message "
          f"does NOT claim '(default)' (msg: {rec.failed[0] if rec.failed else None!r})")

    # H2: an UNKNOWN class value is NOT a system exemption, and the message must not contradict
    # itself by calling it a gamepad control.
    rec = _Recorder()
    list(L.region_rows([dict(base, **{"class": "wibble"})], rec.chk))
    c.chk(len(rec.failed) == 1 and "class=wibble" in rec.failed[0],
          f"[H/{device_id}] unknown class=wibble without skin_part -> FAIL naming the class "
          f"(msg: {rec.failed[0] if rec.failed else None!r})")
    c.chk(rec.failed and "gamepad control" not in rec.failed[0],
          f"[H/{device_id}] the unknown-class message does not contradict itself with the noun "
          f"'gamepad control'")

    # H3: an EMPTY-STRING skin_part is an absence, not a region (it would index nothing)
    rec = _Recorder()
    yielded = list(L.region_rows([dict(base, skin_part="")], rec.chk))
    c.chk(not yielded and len(rec.failed) == 1,
          f"[H/{device_id}] empty-string skin_part is treated as ABSENT -> FAIL, not yielded "
          f"(yielded={len(yielded)} failed={len(rec.failed)})")

    # H4: class=system with a BOGUS skin_part is still handed out — the system exemption is for
    # the ABSENCE of a region, so a system row naming a bad part must still hit compute_layout's
    # ValueError rather than being quietly skipped.
    rec = _Recorder()
    yielded = list(L.region_rows(
        [dict(victim, **{"class": "system", "skin_part": BOGUS_PART})], rec.chk))
    c.chk(len(yielded) == 1 and not rec.passed and not rec.failed,
          f"[H/{device_id}] class=system with a BOGUS skin_part is NOT skipped — it stays "
          f"compute_layout's ValueError to raise (yielded={len(yielded)})")


def _row_F_system_row_with_skin_part_still_asserted(c, desc, device_id):
    """F: `class` alone never exempts a row. A system control that DOES declare a drawable region
    is still asserted — the exemption is for the ABSENCE of a region, not for the class."""
    victim = _first_drawable_gamepad_row(desc)
    row = dict(victim, **{"class": "system"})
    rec = _Recorder()
    yielded = list(L.region_rows([row], rec.chk))
    c.chk(len(yielded) == 1 and yielded[0][1] == victim["skin_part"] and not rec.passed,
          f"[F/{device_id}] class=system WITH a skin_part is still asserted, not skipped "
          f"(yielded={len(yielded)})")


# ---------------------------------------------------------------------------------------------

def run(platform_dir):
    """Run every row. Returns the list of failure messages (empty == pass)."""
    c = Checker("region-guard")
    if not platform_dir or not os.path.isdir(platform_dir):
        c.chk(False, f"PLATFORM dir missing or unreadable: {platform_dir!r} — an absent input is "
                     f"a FAILURE here, never a skip")
        return c.fails

    # If layout.py itself lost the gate (e.g. a revert), every row below would explode with an
    # AttributeError and this file would fail by TRACEBACK rather than by assertion — a red that
    # says "the test is broken" where it should say "the guard is gone". Name the missing API.
    missing = [n for n in ("region_rows", "region_disposition", "REGION_ASSERT", "REGION_SKIP",
                           "REGION_FAIL", "DEFAULT_INPUT_CLASS") if not hasattr(L, n)]
    if missing:
        c.chk(False, f"layout.py no longer exposes the shared skin_part gate (missing: "
                     f"{', '.join(missing)}) — the guard this file exists to pin is GONE")
        return c.fails

    # A device list that resolves to NOTHING would run zero rows and still print "PASS (0 fail)",
    # exit 0 — a vacuous pass, and the precise shape this file's FAIL-LOUD CONTRACT forbids. Note
    # the empty string is already safe ("".split() == []) but WHITESPACE-ONLY is not obviously so
    # (" ".split() == [] too), which is why this is an explicit assertion and not a comment.
    if not REQUIRED_DEVICES:
        c.chk(False, "PF_REGION_GUARD_DEVICES resolved to an EMPTY device list — zero rows would "
                     "run and this file would report a vacuous PASS. Name the devices explicitly "
                     "or unset it (default: 'a133 a523')")
        return c.fails

    tmproot = tempfile.mkdtemp(prefix="pf-region-guard-")
    try:
        for device_id in REQUIRED_DEVICES:
            try:
                desc = L.load_descriptor(platform_dir, device_id)
            except Exception as e:  # noqa: BLE001
                c.chk(False, f"[{device_id}] REQUIRED descriptor unavailable ({e}) — set "
                             f"PF_REGION_GUARD_DEVICES deliberately if this platform ships a "
                             f"different set; absent input FAILS, never skips")
                continue
            root = os.path.join(tmproot, device_id)
            os.makedirs(root, exist_ok=True)
            _row_A_missing_skin_part_on_gamepad(c, desc, device_id)
            _row_B_bogus_skin_part(c, platform_dir, desc, device_id, root)
            sys_row = _row_C_system_row_skipped(c, platform_dir, device_id, root)
            _row_D_real_descriptor_green(c, desc, platform_dir, device_id)
            _row_E_pre_fix_code_crashes(c, sys_row)
            _row_F_system_row_with_skin_part_still_asserted(c, desc, device_id)
            _row_G_checkers_call_the_gate(c, platform_dir, desc, device_id, root)
            _row_H_adjacent_shapes(c, desc, device_id)
    finally:
        shutil.rmtree(tmproot, ignore_errors=True)

    print(f"region-guard negative control: {'PASS' if not c.fails else 'FAIL'} "
          f"({len(c.fails)} fail)")
    return c.fails


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    platform_dir = os.environ.get("PLATFORM")
    if "--platform" in argv:
        platform_dir = argv[argv.index("--platform") + 1]
    if not platform_dir:
        sys.exit("set PLATFORM (or pass --platform DIR)")
    return 1 if run(platform_dir) else 0


if __name__ == "__main__":
    sys.exit(main())
