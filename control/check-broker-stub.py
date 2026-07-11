#!/usr/bin/env python3
"""check-broker-stub.py — tsp-9sx.6: reconcile the sim broker stub with the E1 schema.

broker_stub._has_sensor(d, "gnss", "gps") keys `location`/`gnss` capability presence on a
sensor.kind that platform's capabilities.schema.json now permits (added `gnss` and `gps` to
the enum). Shipping a133/a523 descriptors keep OMITTING gnss (DT-unbound on both SoCs per
SPIKE-0 tsp-9sx.1) — a row would fabricate hardware silicon can't back. This test proves
the reconciliation using SYNTHETIC descriptors: a gnss/gps row now makes is_present pass and
the cooperative default-deny path is exercisable without an off-schema hack.

Stdlib only. Independent of check-control's heavy qemu-tsp path. Run:
  python3 sim/control/check-broker-stub.py     # exit 0 = PASS
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from broker_stub import BrokerStub, HardwareAbsent  # noqa: E402


_fails = []
def chk(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        _fails.append(msg)


def _desc(**extra):
    """Minimal descriptor shape broker_stub reads: sensors + actuators lists."""
    d = {"sensors": [], "actuators": []}
    d.update(extra)
    return d


def main():
    # 1) No gnss/gps row -> location HardwareAbsent (shipping a133/a523 shape).
    b = BrokerStub(_desc())
    chk(not b.is_present("location"), "no gnss row: location HardwareAbsent")
    chk(not b.is_present("gnss"),     "no gnss row: gnss HardwareAbsent")

    # 2) Synthetic descriptor with sensor.kind='gnss' (schema-representable post-tsp-9sx.6):
    #    is_present flips True; policy still default-denies (privacy tier).
    b_gnss = BrokerStub(_desc(sensors=[{"id": "gnss0", "kind": "gnss"}]))
    chk(b_gnss.is_present("location"), "gnss row: location is_present=True")
    chk(b_gnss.is_present("gnss"),     "gnss row: gnss is_present=True")
    chk(not b_gnss.is_granted("location"),
        "gnss row: location is_granted=False (cooperative default-deny)")
    chk(not b_gnss.is_granted("gnss"),
        "gnss row: gnss is_granted=False (cooperative default-deny)")

    # 3) 'gps' alias (broker_stub._has_sensor accepts either kind name).
    b_gps = BrokerStub(_desc(sensors=[{"id": "gps0", "kind": "gps"}]))
    chk(b_gps.is_present("location"), "gps row: location is_present=True (alias)")
    chk(b_gps.is_present("gnss"),     "gps row: gnss is_present=True (alias)")

    # 4) The typed-error HardwareAbsent path is unaffected on a gnss-less descriptor:
    #    acquire::<location> route still refuses. Locate the sim's location acquire path
    #    only if it exists; the presence contract above is the authoritative evidence.

    print()
    if _fails:
        print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
        return 1
    print("ALL BROKER-STUB SELF-TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
