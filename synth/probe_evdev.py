#!/usr/bin/env python3
"""probe_evdev.py — dump what given /dev/input/event* nodes ACTUALLY advertise.

MIRRORS the E1 SPIKE-0 capture shape (platform regression/caps/evdev-probe.py) so
``pf caps probe-diff`` consumes it unchanged — but decodes code numbers through the sim's
OWN generated ``evdev_codes.py`` (kernel-ABI-sourced), NOT a hand-maintained reverse table.
That matters: the platform copy mislabels KEY_HOMEPAGE (it lists 0x172=370; the kernel value
is 172=0xac) and lacks several codes the descriptor uses, which would break the a523 round-
trip. The sim owns its verification probe so it is correct by construction (filed against
platform as a separate bug).

  python3 probe_evdev.py /dev/input/event20 /dev/input/event21 > capture.json
"""
import fcntl
import glob
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evdev_codes as ec  # noqa: E402

_IOC_READ = 2


def _IOC(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


def EVIOCGID():
    return _IOC(_IOC_READ, "E", 0x02, 8)            # struct input_id (4x u16)


def EVIOCGNAME(n):
    return _IOC(_IOC_READ, "E", 0x06, n)


def EVIOCGBIT(ev, n):
    return _IOC(_IOC_READ, "E", 0x20 + ev, n)


def EVIOCGABS(a):
    return _IOC(_IOC_READ, "E", 0x40 + a, 24)        # struct input_absinfo (6x s32)


def _signed32(op):
    return op - (1 << 32) if op >= (1 << 31) else op


def _ioctl(fd, op, arg):
    return fcntl.ioctl(fd, _signed32(op), arg)


def _rev(table, prefer=()):
    """value -> name, first spelling wins, preferring `prefer` names on collisions."""
    out = {}
    for name in prefer:
        if name in table:
            out[table[name]] = name
    for name, val in table.items():
        out.setdefault(val, name)
    return out


EV_REV = _rev(ec.EV)
KEY_REV = _rev({**ec.BTN, **ec.KEY}, prefer=("BTN_A", "BTN_B", "BTN_C", "BTN_X", "BTN_Y", "BTN_Z"))
ABS_REV = _rev(ec.ABS)


def _bits(buf):
    return [i * 8 + b for i, byte in enumerate(buf) for b in range(8) if byte & (1 << b)]


def probe(path):
    node = {"path": path}
    with open(path, "rb") as f:
        fd = f.fileno()
        try:
            buf = bytearray(256)
            n = _ioctl(fd, EVIOCGNAME(len(buf)), buf)
            node["name"] = bytes(buf[:n]).split(b"\x00")[0].decode("utf-8", "replace")
        except OSError:
            node["name"] = ""
        try:
            iid = bytearray(8)
            _ioctl(fd, EVIOCGID(), iid)
            bus, ven, prod, ver = struct.unpack("HHHH", iid)
            node.update(bustype=bus, vendor=f"{ven:04x}", product=f"{prod:04x}", version=f"{ver:04x}")
        except OSError:
            pass
        evbuf = bytearray(4)
        _ioctl(fd, EVIOCGBIT(0, len(evbuf)), evbuf)
        evs = _bits(evbuf)
        node["ev"] = [EV_REV.get(e, f"EV_{e:#x}") for e in evs]
        if ec.EV["EV_KEY"] in evs:
            kb = bytearray((0x2ff // 8) + 1)
            _ioctl(fd, EVIOCGBIT(ec.EV["EV_KEY"], len(kb)), kb)
            node["keys"] = [KEY_REV.get(c, f"0x{c:x}") for c in _bits(kb)]
        if ec.EV["EV_ABS"] in evs:
            ab = bytearray((0x3f // 8) + 1)
            _ioctl(fd, EVIOCGBIT(ec.EV["EV_ABS"], len(ab)), ab)
            absinfo = {}
            for a in _bits(ab):
                try:
                    raw = bytearray(24)
                    _ioctl(fd, EVIOCGABS(a), raw)
                    _val, mn, mx, fz, fl, res = struct.unpack("iiiiii", raw)
                    absinfo[ABS_REV.get(a, f"0x{a:x}")] = {"min": mn, "max": mx, "fuzz": fz,
                                                           "flat": fl, "resolution": res}
                except OSError:
                    pass
            node["abs"] = absinfo
        if ec.EV["EV_FF"] in evs:
            node["ev_ff"] = True
    return node


def main(argv):
    paths = argv or sorted(glob.glob("/dev/input/event*"))
    nodes = []
    for p in paths:
        try:
            nodes.append(probe(p))
        except OSError as e:
            nodes.append({"path": p, "error": str(e)})
    json.dump({"nodes": nodes}, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
