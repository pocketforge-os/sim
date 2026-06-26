#!/usr/bin/env python3
"""check-synth.py — assert the tsp-an4.3 claims for ONE device from captured artifacts.

The load-bearing claim: a SINGLE descriptor-driven synth path (uinput_synth.plan) produces
a uinput device set that, read back through the kernel, IS the descriptor — exactly, with
zero per-device code — for both a133 and a523. Stdlib only; non-zero exit on any failure.

Claims:
  A. ROUND-TRIP EXACT — for every synth node, the LIVE kernel advertises EXACTLY the codes
     + absinfo that plan(descriptor) said to register (no extra, no missing, ranges equal).
     This is descriptor -> ioctls -> kernel -> EVIOCG* probe, compared to the descriptor.
  B. OMISSION / MATRIX — a133 has ONE node and no BTN_THUMBL/THUMBR; a523 has TWO nodes
     (adds a system node carrying KEY_HOMEPAGE) and the pad gains BTN_THUMBL/THUMBR. The
     a133-vs-a523 delta is pure descriptor data.
  C. probe-diff (caps.py, independent logic) reports OK — descriptor codes are a subset of
     the live probe under the asymmetric rule.
  D. SDL3 under qemu-tsp == native x86 (byte-identical, builtin + descriptor mappings);
     the device is recognized as a gamepad; its gamecontrollerdb GUID == descriptor sdl_guid.
  E. raw C evdev probe byte-identical native vs qemu-tsp (qemu-tsp pass-through holds for
     THIS descriptor-synthesized device, re-confirming tsp-an4.1 at the SDL-open layer).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evdev_codes as ec  # noqa: E402
import uinput_synth as us  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def read(p):
    with open(p) as f:
        return f.read()


def load(p):
    with open(p) as f:
        return json.load(f)


def code_val(name):
    if name in ec.CODE:
        return ec.CODE[name]
    return int(name, 16)  # "0x.." fallback (should not occur with the sim's own probe)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--platform", required=True)
    ap.add_argument("--capture", required=True)         # probe_evdev.py over the synth nodes
    ap.add_argument("--probe-diff", required=True)       # caps.py probe-diff output text
    ap.add_argument("--out", required=True)              # dir with SDL + raw-evdev artifacts
    ap.add_argument("--descriptor-guid", required=True)
    a = ap.parse_args()

    desc = us.load_descriptor(a.platform, a.device)
    specs, _ = us.plan(desc)
    cap = load(a.capture)
    cap_by_name = {n.get("name"): n for n in cap["nodes"]}

    print("A. round-trip EXACT (live kernel == plan(descriptor))")
    check(len([n for n in cap["nodes"] if n.get("name") in {s.name for s in specs}]) == len(specs),
          f"node count: capture has the {len(specs)} synth node(s) plan() produced")
    for spec in specs:
        node = cap_by_name.get(spec.name)
        if node is None:
            check(False, f"[{spec.role}] node '{spec.name}' present in capture")
            continue
        got_keys = {code_val(k) for k in node.get("keys", [])}
        want_keys = set(spec.keys)
        check(got_keys == want_keys,
              f"[{spec.role}] keys exact (extra={sorted(got_keys-want_keys)}, "
              f"missing={sorted(want_keys-got_keys)})")
        got_abs = {code_val(c) for c in node.get("abs", {})}
        want_abs = set(spec.abs)
        check(got_abs == want_abs,
              f"[{spec.role}] abs codes exact (extra={sorted(got_abs-want_abs)}, "
              f"missing={sorted(want_abs-got_abs)})")
        for cname, info in node.get("abs", {}).items():
            cv = code_val(cname)
            if cv not in spec.abs:
                continue
            mn, mx, fz, fl = spec.abs[cv]
            got = (info["min"], info["max"], info["fuzz"], info["flat"])
            check(got == (mn, mx, fz, fl),
                  f"[{spec.role}] {cname} absinfo == descriptor (min/max/fuzz/flat)"
                  f" got={got} want={(mn, mx, fz, fl)}")

    print("B. omission / matrix")
    pad = next(s for s in specs if s.role == "pad")
    has_l3r3 = {ec.BTN["BTN_THUMBL"], ec.BTN["BTN_THUMBR"]} <= set(pad.keys)
    has_system = any(s.role == "system" for s in specs)
    if a.device == "a133":
        check(not has_l3r3, "a133: pad has NO BTN_THUMBL/THUMBR (omission)")
        check(not has_system, "a133: NO system-key node (no KEY_* rows)")
    elif a.device == "a523":
        check(has_l3r3, "a523: pad HAS BTN_THUMBL+BTN_THUMBR (added by data)")
        sysnode = cap_by_name.get(f"{desc['identity']['manufacturer']} System")
        check(has_system and sysnode is not None, "a523: system-key node present")
        if sysnode is not None:
            check("KEY_HOMEPAGE" in sysnode.get("keys", []),
                  "a523: system node advertises KEY_HOMEPAGE (172, not 0x172)")

    print("C. caps.py probe-diff (independent asymmetric subset check)")
    pd = read(a.probe_diff)
    check("ERROR" not in pd, "probe-diff has no ERROR lines")
    check("OK    " in pd, "probe-diff reports OK")

    print("D. SDL3 native-x86 == arm64-under-qemu-tsp; gamepad + GUID")
    nb, qb = load(os.path.join(a.out, "out.x86.builtin.json")), load(os.path.join(a.out, "out.arm64.builtin.json"))
    nd, qd = load(os.path.join(a.out, "out.x86.descriptor.json")), load(os.path.join(a.out, "out.arm64.descriptor.json"))
    check(nb == qb, "SDL builtin enumeration identical (native == qemu-tsp)")
    check(nd == qd, "SDL descriptor-mapping enumeration identical (native == qemu-tsp)")
    check(nb.get("found") is True, "device enumerated by SDL")
    check(nb.get("vidpid_matches") == 1, "exactly one 045e:028e device (no stale duplicate)")
    check(nb.get("is_gamepad") is True, "SDL recognizes it as a GAMEPAD")
    gdb = nb.get("joystick", {}).get("guid_gamecontrollerdb")
    check(gdb == a.descriptor_guid, f"gamecontrollerdb GUID {gdb} == descriptor {a.descriptor_guid}")

    print("E. raw C evdev probe byte-identical native vs qemu-tsp (pad node)")
    check(read(os.path.join(a.out, "evdev.native.txt")) == read(os.path.join(a.out, "evdev.qemu-tsp.txt")),
          "raw evdev C probe byte-identical (native == qemu-tsp)")

    print()
    if FAILS:
        print(f"tsp-an4.3 [{a.device}]: FAIL ({len(FAILS)} assertion(s))")
        return 1
    print(f"tsp-an4.3 [{a.device}]: PASS — descriptor-synthesized uinput device(s) are EXACTLY "
          f"the descriptor (round-trip), zero per-device code, indistinguishable to SDL3 under qemu-tsp.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
