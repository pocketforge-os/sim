#!/usr/bin/env python3
"""check-spike3.py — assert the SPIKE-3 (tsp-an4.2) load-bearing claims from the captured
artifacts. Stdlib only. Exits non-zero on any failed assertion.

Claims:
  A. INDISTINGUISHABLE under qemu-tsp: the SDL enumeration JSON is byte-identical
     native-x86 vs arm64-under-qemu-tsp, for BOTH the builtin and the descriptor mapping.
  B. The raw evdev C probe is byte-identical native vs qemu-tsp (re-confirms tsp-an4.1 at
     the SDL open() layer).
  C. The device is recognized as a gamepad and its gamecontrollerdb-form GUID == the a133
     descriptor's sdl_guid.
  D. ASYMMETRIC descriptor<-subset->SDL: every `emit-sdldb` field binds (same source) in
     SDL's builtin enumeration (descriptor codes subset-of what SDL sees).
  E. ONE-descriptor round-trip: feeding `emit-sdldb` as the SDL mapping reproduces EXACTLY
     the descriptor's field set on the live device.
  F. caps.py probe-diff is OK (no ERROR) — evdev-layer asymmetric subset holds.
"""
import argparse, json, os, sys

FAILS = []
def check(cond, msg):
    print(("  ok  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)

def load(out, name):
    with open(os.path.join(out, name)) as f:
        return json.load(f)

def read(out, name):
    with open(os.path.join(out, name)) as f:
        return f.read()

def bindings_dict(j):
    """{output_field: input_source} from a probe JSON's gamepad.bindings."""
    d = {}
    for b in j.get("gamepad", {}).get("bindings", []):
        d.setdefault(b["out"], b["in"])
    return d

def parse_emit(line):
    """emit-sdldb line -> (guid, {field: source})."""
    parts = [p for p in line.strip().split(",") if p]
    guid = parts[0]
    fields = {}
    for tok in parts[2:]:
        if ":" not in tok:
            continue
        k, v = tok.split(":", 1)
        if k in ("platform", "crc", "hint", "sdk"):
            continue
        fields[k] = v
    return guid, fields

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--emit", required=True)        # emit-sdldb line
    ap.add_argument("--descriptor-guid", required=True)
    a = ap.parse_args()
    out = a.out
    _, emit_fields = parse_emit(a.emit)

    nb = load(out, f"out.x86.builtin.json")
    qb = load(out, f"out.arm64.builtin.json")
    nd = load(out, f"out.x86.descriptor.json")
    qd = load(out, f"out.arm64.descriptor.json")

    print("A. indistinguishable native-x86 vs arm64-under-qemu-tsp")
    check(nb == qb, "SDL builtin enumeration JSON identical (native == qemu-tsp)")
    check(nd == qd, "SDL descriptor-mapping enumeration JSON identical (native == qemu-tsp)")

    print("B. raw evdev C probe identical (re-confirm tsp-an4.1 at SDL-open layer)")
    check(read(out, "evdev.native.txt") == read(out, "evdev.qemu-tsp.txt"),
          "evdev C probe byte-identical (native == qemu-tsp)")

    print("C. gamepad recognition + GUID")
    check(nb.get("found") is True, "device enumerated by SDL")
    check(nb.get("vidpid_matches") == 1, "exactly one 045e:028e device (no stale duplicate)")
    check(nb.get("is_gamepad") is True, "SDL recognizes it as a GAMEPAD")
    gdb = nb.get("joystick", {}).get("guid_gamecontrollerdb")
    check(gdb == a.descriptor_guid,
          f"gamecontrollerdb GUID {gdb} == descriptor sdl_guid {a.descriptor_guid}")

    print("D. descriptor fields are a SUBSET of SDL's builtin bindings (same source)")
    bb = bindings_dict(nb)
    for field, src in sorted(emit_fields.items()):
        check(bb.get(field) == src,
              f"builtin binds {field} -> {src} (SDL: {bb.get(field)})")

    print("E. one-descriptor round-trip: emit-sdldb mapping reproduces the field set")
    db = bindings_dict(nd)
    check(set(db.keys()) == set(emit_fields.keys()),
          f"descriptor-run field set == emit-sdldb field set "
          f"(extra={sorted(set(db)-set(emit_fields))}, missing={sorted(set(emit_fields)-set(db))})")
    for field, src in sorted(emit_fields.items()):
        check(db.get(field) == src, f"descriptor-run binds {field} -> {src} (SDL: {db.get(field)})")

    print("F. caps.py probe-diff (evdev-layer asymmetric subset)")
    pd = read(out, f"probe-diff.{a.device}.txt")
    check("ERROR" not in pd, "probe-diff has no ERROR lines")
    check("OK    " in pd, "probe-diff reports OK (descriptor codes subset-of probe)")

    print()
    if FAILS:
        print(f"SPIKE-3: FAIL ({len(FAILS)} assertion(s) failed)")
        sys.exit(1)
    print("SPIKE-3: PASS — uinput TRIMUI Player1 is indistinguishable to SDL3 gamepad "
          "enumeration under qemu-tsp; GUID + button/axis map match the a133 descriptor.")

if __name__ == "__main__":
    main()
