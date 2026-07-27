#!/usr/bin/env python3
"""selftest-check-synth.py — NEGATIVE CONTROL for check-synth.py section B (tsp-477r).

A guard you have not seen fail is not a guard. Section B used to hard-code a device fact by
NAME (``if a.device == "a133": check(not has_system, ...)``); tsp-bwrg.16 gave the a133
class=system KEY_VOLUMEUP/DOWN rows and the assertion went red on a *correct* descriptor. The
fix derives the expectation from the descriptor. The trap that fix invites is the opposite one:
an expectation re-derived from the very code path under test is a TAUTOLOGY that passes
unconditionally, and "delete the assertion" and "make it always true" are the same wrong answer.

So this file constructs the broken states and asserts the checker goes RED **for the specific
reason claimed** — the FAIL MESSAGE, not merely a non-zero exit — while staying GREEN on the
rows that must not be weakened. It is hermetic: every fixture (descriptor, capture, probe-diff,
SDL artifacts) is written from literal data here, so it needs no platform checkout, no
/dev/uinput, no qemu, no network. There is deliberately NO skip path — a missing input FAILS
(a selftest that SKIPs is fail-open, and CI is exactly where the input goes missing).

Run:  python3 synth/selftest-check-synth.py [-v]     (exit 0 = all rows behaved as asserted)
Wired as a device-free preflight in run-synth.sh.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import uinput_synth as us  # noqa: E402

# Defaults to the checker next to this file. PF_CHECK_SYNTH points it at a DIFFERENT
# check-synth.py so the negative control can be re-run adversarially against the PRE-FIX
# checker (`git show <sha>:synth/check-synth.py > /tmp/old.py`) and the red rows counted
# independently — do not take a transcript's word for the number.
CHECK = os.environ.get("PF_CHECK_SYNTH") or os.path.join(HERE, "check-synth.py")
GUID = "030000005e0400008e02000010010000"   # Xbox-360 wired, as the real descriptors use
PAD_NAME = "TRIMUI Player1"
SYS_NAME = "PFTest System"                  # = "<manufacturer> System" per uinput_synth.plan()

# --- descriptor fixtures (literal TOML — no platform checkout, no toml writer) --------------
HEAD = f"""
[identity]
id           = "{{dev}}"
manufacturer = "PFTest"
model        = "Fixture"
sdl_guid     = "{GUID}"
match = {{{{ evdev_name = "{PAD_NAME}", vid = "045e", pid = "028e" }}}}
"""

PAD_ROWS = """
[[inputs]]
id = "south"
kind = "button"
ev_type = "EV_KEY"
code = "BTN_A"
[[inputs]]
id = "start"
kind = "button"
ev_type = "EV_KEY"
code = "BTN_START"
[[inputs]]
id = "dpad"
kind = "hat"
ev_type = "EV_ABS"
code = "ABS_HAT0X,ABS_HAT0Y"
"""

# VOL+/- exactly as tsp-bwrg.16 writes them: class=system + an explicit off-pad `source`.
SYSTEM_ROWS = """
[[inputs]]
id = "vol_up"
kind = "button"
ev_type = "EV_KEY"
code = "KEY_VOLUMEUP"
class = "system"
source = "sunxi-keyboard"
[[inputs]]
id = "vol_down"
kind = "button"
ev_type = "EV_KEY"
code = "KEY_VOLUMEDOWN"
class = "system"
source = "sunxi-keyboard"
"""

# The descriptor says this control lives OFF the pad node, but its code is BTN_* — so plan(),
# which routes by code prefix, puts it on the pad. A real routing divergence.
OFFPAD_BTN_ROW = """
[[inputs]]
id = "l3"
kind = "stick-click"
ev_type = "EV_KEY"
code = "BTN_THUMBL"
class = "system"
source = "sunxi-keyboard"
"""

# The converse: a KEY_* row with NO `source`, i.e. the descriptor claims it IS on the primary
# gamepad node, while plan() hives every KEY_* off to a system node.
ONPAD_KEY_ROW = """
[[inputs]]
id = "home"
kind = "button"
ev_type = "EV_KEY"
code = "KEY_HOMEPAGE"
"""

L3R3_ROWS = """
[[inputs]]
id = "l3"
kind = "stick-click"
ev_type = "EV_KEY"
code = "BTN_THUMBL"
[[inputs]]
id = "r3"
kind = "stick-click"
ev_type = "EV_KEY"
code = "BTN_THUMBR"
"""


def write_platform(root, dev, toml_text):
    d = os.path.join(root, "devices", dev)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "capabilities.toml"), "w") as f:
        f.write(toml_text)
    return root


def capture_from_specs(specs):
    """A capture that EXACTLY matches plan() — so sections A/C/D/E pass and only the row's
    deliberate mutation can turn anything red."""
    return {"nodes": [json.loads(json.dumps(s.to_dict())) for s in specs]}


def write_fixtures(root, specs, capture):
    out = os.path.join(root, "out")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(root, "capture.json"), "w") as f:
        json.dump(capture, f)
    with open(os.path.join(root, "probe-diff.txt"), "w") as f:
        f.write("OK    fixture probe-diff\n")
    builtin = {"found": True, "vidpid_matches": 1, "is_gamepad": True,
               "joystick": {"guid_gamecontrollerdb": GUID}}
    for arch in ("x86", "arm64"):
        for mode in ("builtin", "descriptor"):
            with open(os.path.join(out, f"out.{arch}.{mode}.json"), "w") as f:
                json.dump(builtin, f)
    for name in ("evdev.native.txt", "evdev.qemu-tsp.txt"):
        with open(os.path.join(out, name), "w") as f:
            f.write("fixture evdev probe\n")
    return out


def run_row(row, verbose):
    """Build the row's fixtures, run check-synth.py for real, and judge its OUTPUT."""
    with tempfile.TemporaryDirectory() as root:
        plat = write_platform(os.path.join(root, "platform"), row["dev"], row["toml"])
        desc = us.load_descriptor(plat, row["dev"])
        specs, _ = us.plan(desc)
        capture = capture_from_specs(specs)
        if row.get("mutate"):
            row["mutate"](capture)
        out = write_fixtures(root, specs, capture)
        proc = subprocess.run(
            [sys.executable, CHECK, "--device", row["dev"], "--platform", plat,
             "--capture", os.path.join(root, "capture.json"),
             "--probe-diff", os.path.join(root, "probe-diff.txt"),
             "--out", out, "--descriptor-guid", GUID],
            capture_output=True, text=True)
    stdout = proc.stdout
    fails = [ln for ln in stdout.splitlines() if ln.startswith("FAIL")]
    problems = []
    # A crash is NOT a red row. Without this, a checker that dies on import produces zero FAIL
    # lines and a non-zero exit, which a red row's assertions would happily accept — the row
    # would "pass" while proving nothing about the assertion it exists to exercise.
    if "Traceback (most recent call last)" in proc.stderr:
        problems.append("checker CRASHED (traceback on stderr) — not a red row; "
                        + proc.stderr.strip().splitlines()[-1])
    if proc.returncode != row["exit"]:
        problems.append(f"exit {proc.returncode}, expected {row['exit']}")
    for want in row.get("fail_contains", []):
        if not any(want in ln for ln in fails):
            problems.append(f"no FAIL line containing {want!r}")
    for forbid in row.get("no_fail_contains", []):
        if any(forbid in ln for ln in fails):
            problems.append(f"unexpected FAIL line containing {forbid!r}")
    if row["exit"] == 0 and fails:
        problems.append(f"expected zero FAIL lines, got {len(fails)}: {fails}")
    if row["exit"] != 0 and not fails:
        problems.append("expected at least one FAIL line, got none")
    for want_ok in row.get("ok_contains", []):
        if not any(ln.startswith("  ok  ") and want_ok in ln for ln in stdout.splitlines()):
            problems.append(f"no ok line containing {want_ok!r}")
    if verbose or problems:
        print(f"----- {row['name']} -----")
        print(stdout.rstrip())
        if proc.stderr.strip():
            print("stderr:", proc.stderr.rstrip())
    return problems


def drop_node(name):
    def _m(cap):
        cap["nodes"] = [n for n in cap["nodes"] if n.get("name") != name]
    return _m


def hexify_keys(name):
    """Advertise the node's keys as raw hex ("0x73") instead of canonical evdev names. The
    VALUES still match the descriptor, so section A stays green and ONLY the canonical-name
    assertion may fire — an isolated red."""
    def _m(cap):
        for n in cap["nodes"]:
            if n.get("name") == name:
                import evdev_codes as ec
                n["keys"] = [hex(ec.CODE[k]) if k in ec.CODE else k for k in n.get("keys", [])]
    return _m


IFF = "system-key node present IFF"
PADSET = "[pad] keys are EXACTLY"
SYSSET = "[system] keys are EXACTLY"
NAMES = "canonical evdev NAMES"
# Section A's node-presence assertion. Section B deliberately does NOT duplicate it — this row
# exists so "the capture is missing a node plan() produced" is demonstrably still caught, and it
# is honest about WHICH section catches it (it is not new coverage from this change).
INCAP = "present in capture"

ROWS = [
    # --- GREEN: the rows that must NOT be weakened -------------------------------------
    dict(name="green/system-rows -> two nodes", dev="fixture", exit=0,
         toml=HEAD.format(dev="fixture") + PAD_ROWS + SYSTEM_ROWS,
         ok_contains=[IFF, PADSET, SYSSET, NAMES]),
    dict(name="green/no-system-rows, no L3R3 -> one node", dev="fixture", exit=0,
         toml=HEAD.format(dev="fixture") + PAD_ROWS,
         ok_contains=[IFF, PADSET, SYSSET]),
    dict(name="green/L3R3 declared -> pad carries them", dev="fixture", exit=0,
         toml=HEAD.format(dev="fixture") + PAD_ROWS + L3R3_ROWS,
         ok_contains=[PADSET]),
    # THE BUG: under the pre-fix device-name-keyed form these two were RED. They are the
    # regression rows — a re-run against the old check-synth.py must fail exactly here.
    dict(name="green/device id 'a133' WITH system rows (was a false red)", dev="a133", exit=0,
         toml=HEAD.format(dev="a133") + PAD_ROWS + SYSTEM_ROWS,
         ok_contains=[IFF, SYSSET]),
    dict(name="green/device id 'a523' WITHOUT system rows or L3R3", dev="a523", exit=0,
         toml=HEAD.format(dev="a523") + PAD_ROWS,
         ok_contains=[IFF, PADSET]),

    # --- RED: the guard must still fire, and for its own reason ------------------------
    dict(name="red/off-pad BTN_ row routed onto the pad by plan()", dev="fixture", exit=1,
         toml=HEAD.format(dev="fixture") + PAD_ROWS + OFFPAD_BTN_ROW,
         fail_contains=[IFF, PADSET]),
    dict(name="red/on-pad KEY_ row hived off to a system node by plan()", dev="fixture", exit=1,
         toml=HEAD.format(dev="fixture") + PAD_ROWS + ONPAD_KEY_ROW,
         fail_contains=[IFF, PADSET, SYSSET]),
    # Caught by section A, not B (see INCAP). Kept so the coverage is demonstrated, not assumed.
    dict(name="red/live capture missing the expected system node (section A)", dev="fixture",
         exit=1, toml=HEAD.format(dev="fixture") + PAD_ROWS + SYSTEM_ROWS,
         mutate=drop_node(SYS_NAME),
         fail_contains=[INCAP],
         no_fail_contains=[IFF, SYSSET]),
    dict(name="red/system node advertises hex fallbacks, not evdev names", dev="fixture", exit=1,
         toml=HEAD.format(dev="fixture") + PAD_ROWS + SYSTEM_ROWS,
         mutate=hexify_keys(SYS_NAME),
         fail_contains=[NAMES],
         # the mutation is name-only: the CODE VALUES still match, so nothing else may red
         no_fail_contains=[IFF, PADSET, SYSSET, INCAP]),
]


def main():
    verbose = "-v" in sys.argv
    if not os.path.isfile(CHECK):
        print(f"selftest-check-synth: FAIL — no checker at {CHECK}")
        return 1  # absent input FAILS; it never skips
    bad = 0
    for row in ROWS:
        problems = run_row(row, verbose)
        print(("  ok  " if not problems else "FAIL  ") + row["name"])
        for p in problems:
            print(f"        - {p}")
        bad += bool(problems)
    print()
    if bad:
        print(f"selftest-check-synth: FAIL ({bad}/{len(ROWS)} row(s) did not behave as asserted)")
        return 1
    print(f"selftest-check-synth: PASS — {len(ROWS)} rows; check-synth.py section B goes RED for "
          f"its own stated reasons and stays GREEN on the rows that must not be weakened.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
