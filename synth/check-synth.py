#!/usr/bin/env python3
"""check-synth.py — assert the tsp-an4.3 claims for ONE device from captured artifacts.

The load-bearing claim: a SINGLE descriptor-driven synth path (uinput_synth.plan) produces
a uinput device set that, read back through the kernel, IS the descriptor — exactly, with
zero per-device code — for both a133 and a523. Stdlib only; non-zero exit on any failure.

Claims:
  A. ROUND-TRIP EXACT — for every synth node, the LIVE kernel advertises EXACTLY the codes
     + absinfo that plan(descriptor) said to register (no extra, no missing, ranges equal).
     This is descriptor -> ioctls -> kernel -> EVIOCG* probe, compared to the descriptor.
  B. NODE TOPOLOGY / OMISSION — plan()'s node set and per-node key sets equal what the
     DESCRIPTOR ITSELF declares. The expectation is derived from each [[inputs]] row's
     `source` (the evdev NODE NAME the control lives on; absent = identity.match.evdev_name,
     the primary gamepad node) — an INDEPENDENT derivation from plan(), which groups by evdev
     code PREFIX (BTN_* -> pad, KEY_* -> system). The two disagree the moment a row is
     dropped, fabricated, or routed to the wrong node. NO DEVICE NAME appears in any
     assertion: whether a device yields one node or two, and whether its pad carries
     BTN_THUMBL/THUMBR, is descriptor DATA and is asserted as such (see synth/README.md
     "B is descriptor-derived"). Negative control: synth/selftest-check-synth.py.
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


def declared_node_membership(desc):
    """Descriptor -> (primary-node EV_KEY code names, off-pad EV_KEY code names, {source: [names]}).

    The descriptor's OWN statement of node topology, and deliberately NOT the one plan() uses.
    caps.py (tsp-bwrg.16): ``source`` = the evdev NODE NAME a control lives on; ABSENT means the
    primary gamepad node, i.e. ``identity.match.evdev_name``. plan() instead groups by evdev code
    PREFIX (BTN_* -> pad node, KEY_* -> system node). Asserting one against the other is what
    gives section B content: a row dropped, fabricated, or routed to the wrong node makes the two
    disagree. A single shared derivation (or an expectation read back out of plan()) would be a
    tautology that passes unconditionally — the failure mode this function exists to avoid.

    ABS rows are excluded: they are pad-only by construction in plan() and are already asserted
    exactly against the live kernel in section A.
    """
    primary = desc.get("identity", {}).get("match", {}).get("evdev_name")
    pad_names, sys_names, sources = [], [], {}
    for inp in desc.get("inputs", []):
        if inp.get("ev_type") != "EV_KEY":
            continue
        src = inp.get("source")
        on_pad = src is None or (primary is not None and src == primary)
        for name in [c for c in inp.get("code", "").split(",") if c]:
            (pad_names if on_pad else sys_names).append(name)
            if not on_pad:
                sources.setdefault(src, []).append(name)
    return pad_names, sys_names, sources


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

    # B. The expectation is DERIVED FROM THE DESCRIPTOR, never from the device NAME. The old form
    # hard-coded `if a.device == "a133": check(not has_system, ...)`, which asserted a hardware
    # fact the descriptor later superseded (tsp-bwrg.16 added class=system KEY_VOLUMEUP/DOWN rows
    # to the a133) and went red on a correct descriptor. Note what is NOT here: an assertion keyed
    # on BTN_THUMBL/THUMBR. It is not deleted — it is SUBSUMED, and strictly strengthened, by the
    # pad-set equality below, which pins the pad node to EXACTLY the descriptor's primary-node
    # rows. That reds if L3/R3 are fabricated on a descriptor that omits them AND if they are
    # dropped from one that declares them, for every device, with no device name in the loop.
    print("B. node topology / omission (plan() vs the descriptor's OWN declared node membership)")
    want_pad_names, want_sys_names, sys_sources = declared_node_membership(desc)
    want_pad = {code_val(n) for n in want_pad_names}
    want_sys = {code_val(n) for n in want_sys_names}
    pad = next(s for s in specs if s.role == "pad")
    sysspecs = [s for s in specs if s.role == "system"]
    got_pad = set(pad.keys)
    got_sys = {k for s in sysspecs for k in s.keys}

    check(bool(sysspecs) == bool(want_sys_names),
          f"system-key node present IFF the descriptor declares EV_KEY row(s) off the primary "
          f"node: descriptor declares {len(want_sys_names)} such code(s) on "
          f"{sorted(sys_sources)}, plan() produced {len(sysspecs)} system node(s)")
    check(got_pad == want_pad,
          f"[pad] keys are EXACTLY the descriptor's primary-node EV_KEY rows "
          f"(extra={sorted(got_pad - want_pad)}, missing={sorted(want_pad - got_pad)})")
    check(got_sys == want_sys,
          f"[system] keys are EXACTLY the descriptor's off-pad EV_KEY rows "
          f"(extra={sorted(got_sys - want_sys)}, missing={sorted(want_sys - got_sys)})")
    for spec in sysspecs:
        sysnode = cap_by_name.get(spec.name)
        if sysnode is None:
            continue  # section A already reds on a missing node — do not double-report it here
        unresolved = [n for n in sysnode.get("keys", []) if n not in ec.CODE]
        check(not unresolved,
              f"[system] live node advertises canonical evdev NAMES, not hex fallbacks "
              f"(e.g. KEY_HOMEPAGE as 172, not 0x172) (unresolved={unresolved})")

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
