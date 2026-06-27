#!/usr/bin/env python3
"""uinput_synth.py — synthesize virtual evdev device(s) PURELY from a capabilities.toml.

This is the data-driven generalization of ``spike3/mkuinput.c`` (which hand-coded ONE
device). Here there is ZERO per-device code: a133 and a523 differ ONLY by descriptor rows.
For each ``[[inputs]]`` row we register the row's ``(ev_type, code)`` + per-axis ``absinfo``
straight from the descriptor; MISSING HARDWARE IS HANDLED BY OMISSION (the a133 base is the
a523 set MINUS the home/L3/R3 rows — never a fabricated row). The synthesized device IS the
descriptor's expectation made into a kernel-visible node.

NODE GROUPING (honest topology, derived — not per-device coded): gamepad codes (BTN_*/ABS_*)
land on the 045e:028e "TRIMUI Player1" pad node (the SPIKE-3 device); system keys (KEY_*,
e.g. a523's Home) land on a SEPARATE generic node — matching caps.py's own model (emit-sdldb
excludes KEY_* from the gamepad mapping; probe-diff resolves KEY_* against ANY node) and the
a523 descriptor's note that Home is "a system key, NOT the gamepad's guide". So a133 yields
ONE node and a523 yields TWO — another facet of the omission proof.

The bus/vendor/product/version of the pad node are derived from ``identity.sdl_guid`` (the
authoritative SDL identity) and cross-checked against ``identity.match`` — so the GUID SDL
computes equals the descriptor's, with no second source of truth.

HONESTY: this advertises the descriptor's evdev INPUT codes + absinfo (the EVIOCGBIT/
EVIOCGABS probe surface). It does NOT model: LED arrays (sysfs led-class, a broker
capability -> C7), real force-feedback PLAYBACK (FF upload is out of qemu-tsp scope; the
rumble actuator is C7's broker path), sensors (IIO, broker -> C7), or any GPU/timing/
enforcement behaviour (the hardware gate's authority — see docs/HONESTY-CONTRACT.md).

Importable API (the resolver C5's control surface builds on):
    desc   = load_descriptor(platform_dir, "a523")
    specs, resolver = plan(desc)            # pure data; device-free
    synth  = Synth(desc); synth.create()    # needs /dev/uinput (root)
    synth.press("south"); synth.release("south")
    synth.set_axis("ltrig", 0.5)            # normalized 0..1 across the descriptor range
    synth.move_hat("dpad", 1, 0)
    synth.destroy()

CLI:
    uinput_synth.py plan   --device <id> --platform <dir>          # print derived spec (JSON)
    uinput_synth.py create --device <id> --platform <dir> [--keepalive]
"""
import argparse
import fcntl
import glob
import json
import os
import signal
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evdev_codes as ec  # noqa: E402

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

# --- ioctl encodings (asm-generic; identical aarch64/x86_64) ----------------
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _IOC(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


_U = "U"  # UINPUT_IOCTL_BASE
UI_DEV_CREATE = _IOC(_IOC_NONE, _U, 1, 0)
UI_DEV_DESTROY = _IOC(_IOC_NONE, _U, 2, 0)
UI_SET_EVBIT = _IOC(_IOC_WRITE, _U, 100, 4)
UI_SET_KEYBIT = _IOC(_IOC_WRITE, _U, 101, 4)
UI_SET_ABSBIT = _IOC(_IOC_WRITE, _U, 103, 4)
UI_SET_FFBIT = _IOC(_IOC_WRITE, _U, 107, 4)


def _UI_GET_SYSNAME(length):
    return _IOC(_IOC_READ, _U, 44, length)


UINPUT_MAX_NAME_SIZE = 80
ABS_CNT = 0x40  # 64


def _signed32(op):
    """fcntl.ioctl wants the request as a signed 32-bit int; READ-dir ops set bit 31."""
    return op - (1 << 32) if op >= (1 << 31) else op


def _ioc(fd, op, arg=0):
    return fcntl.ioctl(fd, _signed32(op), arg)


# --- descriptor -> spec (pure data; device-free) ---------------------------
class DeviceSpec:
    """One virtual evdev node, fully described by data pulled from the descriptor."""

    def __init__(self, role, name, bus, vid, pid, version):
        self.role = role            # "pad" | "system"
        self.name = name
        self.bus, self.vid, self.pid, self.version = bus, vid, pid, version
        self.keys = []              # [int] in registration order
        self.abs = {}               # {int code: (min, max, fuzz, flat)}
        self.node = None            # /dev/input/eventN, filled after create()

    def to_dict(self):
        rev = _reverse_names()
        return {
            "role": self.role, "name": self.name,
            "id": {"bustype": self.bus, "vendor": f"{self.vid:04x}",
                   "product": f"{self.pid:04x}", "version": f"{self.version:04x}"},
            "keys": [rev.get(k, f"0x{k:x}") for k in self.keys],
            "abs": {rev.get(c, f"0x{c:x}"): {"min": v[0], "max": v[1], "fuzz": v[2], "flat": v[3]}
                    for c, v in self.abs.items()},
            "node": self.node,
        }


_REV = None


def _reverse_names():
    """value -> canonical name, preferring the descriptor/driver spelling on collisions
    (BTN_A over BTN_SOUTH for 0x130) so the capture matches descriptor strings."""
    global _REV
    if _REV is not None:
        return _REV
    pref = ["BTN_A", "BTN_B", "BTN_C", "BTN_X", "BTN_Y", "BTN_Z"]  # over SOUTH/EAST/...
    rev = {}
    for name, val in {**ec.ABS, **ec.KEY, **ec.BTN}.items():
        if val in rev and name not in pref:
            continue
        if val in rev and rev[val] in pref:
            continue
        rev[val] = name
    _REV = rev
    return rev


def parse_sdl_guid(guid):
    """SDL3 USB joystick GUID (32 hex) -> (bus, vendor, product, version), all little-endian
    u16 at the standard offsets. bytes: [0:2]=bus [2:4]=crc [4:6]=vendor [8:10]=product
    [12:14]=version."""
    b = bytes.fromhex(guid)
    if len(b) != 16:
        raise ValueError(f"sdl_guid must be 32 hex chars, got {len(b)*2}")
    u16 = lambda i: b[i] | (b[i + 1] << 8)
    return u16(0), u16(4), u16(8), u16(12)


def load_descriptor(platform_dir, device_id):
    path = os.path.join(platform_dir, "devices", device_id, "capabilities.toml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no capabilities.toml for '{device_id}' at {path}")
    return _toml_load(path)


def _absinfo(inp, code_name):
    """(min, max, fuzz, flat) for an ABS code, from the descriptor's per-axis/range data.
    Hat axes default to -1..1 (no descriptor range — the classic dpad convention)."""
    if code_name.startswith("ABS_HAT"):
        return (-1, 1, 0, 0)
    kind = inp.get("kind")
    if kind == "stick":
        codes = [c for c in inp["code"].split(",") if c]
        ax = inp.get("x") if code_name == codes[0] else inp.get("y")
    else:  # trigger / single-axis
        ax = inp.get("range")
    if ax is None:
        return (0, 0, 0, 0)
    return (int(ax.get("min", 0)), int(ax.get("max", 0)),
            int(ax.get("fuzz", 0)), int(ax.get("flat", 0)))


def plan(desc):
    """Descriptor -> (list[DeviceSpec], resolver). Pure data; the heart of 'zero per-device
    code'. resolver: input id -> {ev_type, kind, codes:[int], code_names:[str], role, ranges}."""
    ident = desc["identity"]
    bus, vid, pid, ver = parse_sdl_guid(ident["sdl_guid"])
    match = ident.get("match", {})
    # cross-check the two identity sources (descriptor self-consistency)
    if match.get("vid") and int(match["vid"], 16) != vid:
        raise ValueError(f"identity.match.vid {match['vid']} != sdl_guid vendor {vid:04x}")
    if match.get("pid") and int(match["pid"], 16) != pid:
        raise ValueError(f"identity.match.pid {match['pid']} != sdl_guid product {pid:04x}")

    pad = DeviceSpec("pad", match.get("evdev_name") or ident.get("model", "gamepad"),
                     bus, vid, pid, ver)
    system = DeviceSpec("system", f"{ident.get('manufacturer', 'PocketForge')} System",
                        ec.BUS["BUS_HOST"], 0, 0, 0)

    resolver = {}
    for inp in desc.get("inputs", []):
        iid = inp["id"]
        ev = inp["ev_type"]
        kind = inp.get("kind")
        names = [c for c in inp["code"].split(",") if c]
        try:
            codes = [ec.code_value(n) for n in names]
        except KeyError as e:
            raise ValueError(f"input '{iid}': unknown evdev code {e}") from None
        ranges = {}
        for n, c in zip(names, codes):
            if ev == "EV_KEY":
                tgt = pad if n.startswith("BTN_") else system
                if c not in tgt.keys:
                    tgt.keys.append(c)
            elif ev == "EV_ABS":
                ai = _absinfo(inp, n)
                pad.abs[c] = ai
                ranges[n] = ai
            else:
                raise ValueError(f"input '{iid}': unsupported ev_type {ev}")
        role = "system" if (ev == "EV_KEY" and names and names[0].startswith("KEY_")) else "pad"
        resolver[iid] = {"ev_type": ev, "kind": kind, "codes": codes,
                         "code_names": names, "role": role, "ranges": ranges}

    specs = [pad] + ([system] if system.keys else [])
    return specs, resolver


# --- create / inject (needs /dev/uinput, root) ------------------------------
def _pack_uud(spec):
    name = spec.name.encode("utf-8")[:UINPUT_MAX_NAME_SIZE - 1]
    name += b"\x00" * (UINPUT_MAX_NAME_SIZE - len(name))
    ids = struct.pack("<HHHH", spec.bus, spec.vid, spec.pid, spec.version)
    ff = struct.pack("<I", 0)
    amax, amin, afuzz, aflat = ([0] * ABS_CNT for _ in range(4))
    for code, (mn, mx, fz, fl) in spec.abs.items():
        amax[code], amin[code], afuzz[code], aflat[code] = mx, mn, fz, fl
    arrays = b"".join(struct.pack(f"<{ABS_CNT}i", *a) for a in (amax, amin, afuzz, aflat))
    return name + ids + ff + arrays


def _resolve_node(fd):
    """The event node for a freshly-created uinput device, via UI_GET_SYSNAME ('inputN')."""
    buf = bytearray(64)
    try:
        n = _ioc(fd, _UI_GET_SYSNAME(len(buf)), buf)
        sysname = bytes(buf[:n]).split(b"\x00")[0].decode() if isinstance(n, int) and n > 0 \
            else bytes(buf).split(b"\x00")[0].decode()
    except OSError:
        sysname = ""
    sysname = sysname.strip() or None
    for _ in range(50):  # the eventX child appears just after UI_DEV_CREATE
        for ev in glob.glob("/sys/class/input/event*"):
            dev = os.path.join(ev, "device")
            if sysname and os.path.basename(os.path.realpath(dev)) == sysname:
                return "/dev/input/" + os.path.basename(ev)
        time.sleep(0.02)
    return None


class Synth:
    """A descriptor's whole virtual device set + the injection primitives C5 wraps."""

    def __init__(self, desc, uinput_path="/dev/uinput"):
        self.desc = desc
        self.uinput_path = uinput_path
        self.specs, self.resolver = plan(desc)
        self._fds = {}            # role -> fd
        self._node_for_role = {}  # role -> node path

    def create(self):
        for spec in self.specs:
            fd = os.open(self.uinput_path, os.O_RDWR | os.O_NONBLOCK)
            _ioc(fd, UI_SET_EVBIT, ec.EV["EV_SYN"])
            if spec.keys:
                _ioc(fd, UI_SET_EVBIT, ec.EV["EV_KEY"])
                for k in spec.keys:
                    _ioc(fd, UI_SET_KEYBIT, k)
            if spec.abs:
                _ioc(fd, UI_SET_EVBIT, ec.EV["EV_ABS"])
                for c in spec.abs:
                    _ioc(fd, UI_SET_ABSBIT, c)
            os.write(fd, _pack_uud(spec))
            _ioc(fd, UI_DEV_CREATE)
            spec.node = _resolve_node(fd)
            self._fds[spec.role] = fd
            self._node_for_role[spec.role] = spec.node
        return self

    def nodes(self):
        return [{"role": s.role, "name": s.name, "node": s.node} for s in self.specs]

    # --- injection primitives (the C5 control-surface foundation) ---
    def _emit(self, role, etype, code, value):
        # struct input_event on a 64-bit kernel = timeval{__kernel_long_t sec, usec} (8+8) +
        # u16 type + u16 code + s32 value = 24 bytes. Pack the time fields as 8-byte ("q"):
        # "<llHHi" would force STANDARD-size l=4 -> a 16-byte event -> the kernel rejects the
        # write with EINVAL (count < sizeof(input_event)). [fixed tsp-an4.5: .3 shipped this
        # primitive but never exercised the inject path — its check only probed advertised codes.]
        os.write(self._fds[role], struct.pack("<qqHHi", 0, 0, etype, code, value))

    def _syn(self, role):
        self._emit(role, ec.EV["EV_SYN"], ec.SYN["SYN_REPORT"], 0)

    def _r(self, input_id):
        r = self.resolver.get(input_id)
        if r is None:
            raise KeyError(f"no input '{input_id}' in descriptor")
        return r

    def press(self, input_id):
        r = self._r(input_id)
        if r["ev_type"] != "EV_KEY":
            raise ValueError(f"'{input_id}' is not a button (ev_type {r['ev_type']})")
        self._emit(r["role"], ec.EV["EV_KEY"], r["codes"][0], 1)
        self._syn(r["role"])

    def release(self, input_id):
        r = self._r(input_id)
        if r["ev_type"] != "EV_KEY":
            raise ValueError(f"'{input_id}' is not a button (ev_type {r['ev_type']})")
        self._emit(r["role"], ec.EV["EV_KEY"], r["codes"][0], 0)
        self._syn(r["role"])

    def set_axis(self, input_id, value, normalized=True):
        """Drive a single-axis control (trigger). normalized: value in 0..1 scaled across
        the descriptor's min..max; else value is the raw evdev value."""
        r = self._r(input_id)
        if r["ev_type"] != "EV_ABS" or len(r["codes"]) != 1:
            raise ValueError(f"'{input_id}' is not a single-axis control")
        code, name = r["codes"][0], r["code_names"][0]
        if normalized:
            mn, mx, _, _ = r["ranges"][name]
            value = int(round(mn + (mx - mn) * max(0.0, min(1.0, value))))
        self._emit(r["role"], ec.EV["EV_ABS"], code, int(value))
        self._syn(r["role"])

    def move_hat(self, input_id, x, y):
        r = self._r(input_id)
        if r["kind"] != "hat":
            raise ValueError(f"'{input_id}' is not a hat")
        self._emit(r["role"], ec.EV["EV_ABS"], r["codes"][0], int(x))
        self._emit(r["role"], ec.EV["EV_ABS"], r["codes"][1], int(y))
        self._syn(r["role"])

    def set_stick(self, input_id, x, y, normalized=True):
        """Drive a 2-axis analog stick. normalized: x,y in -1..1 (0 = centre) scaled across
        each axis's descriptor min..max (raw evdev value otherwise). The control-surface (C5)
        and GUI (C6) injection primitive for sticks; complements set_axis (single axis)."""
        r = self._r(input_id)
        if r["kind"] != "stick" or len(r["codes"]) != 2:
            raise ValueError(f"'{input_id}' is not a 2-axis stick")
        for code, name, v in zip(r["codes"], r["code_names"], (x, y)):
            if normalized:
                mn, mx, _, _ = r["ranges"][name]
                centre = (mn + mx) / 2.0
                v = int(round(centre + max(-1.0, min(1.0, v)) * (mx - mn) / 2.0))
            self._emit(r["role"], ec.EV["EV_ABS"], code, int(v))
        self._syn(r["role"])

    def destroy(self):
        for role, fd in list(self._fds.items()):
            try:
                _ioc(fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(fd)
        self._fds.clear()


def _inject_demo(synth):
    """A small known sequence for the read-path test (mkuinput's SIGUSR1 analog): press the
    accept_default button, half-pull the first trigger, release. All descriptor-resolved."""
    btn = synth.desc.get("accept_default")
    if not btn or btn not in synth.resolver:
        btn = next((i for i, r in synth.resolver.items() if r["ev_type"] == "EV_KEY"
                    and r["role"] == "pad"), None)
    trig = next((i for i, r in synth.resolver.items() if r["kind"] == "trigger"), None)
    if btn:
        synth.press(btn)
    if trig:
        synth.set_axis(trig, 0.5)
    if btn:
        synth.release(btn)


def cmd_plan(a):
    desc = load_descriptor(a.platform, a.device)
    specs, resolver = plan(desc)
    out = {"device": a.device, "specs": [s.to_dict() for s in specs], "resolver": resolver}
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


def cmd_create(a):
    desc = load_descriptor(a.platform, a.device)
    synth = Synth(desc, uinput_path=a.uinput).create()
    info = {"device": a.device, "nodes": synth.nodes()}
    json.dump(info, sys.stdout)
    print()
    sys.stdout.flush()
    if not a.keepalive:
        synth.destroy()
        return 0
    signal.signal(signal.SIGUSR1, lambda *_: _inject_demo(synth))
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
    while not stop["v"]:
        signal.pause()
    synth.destroy()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "create"):
        p = sub.add_parser(name)
        p.add_argument("--device", required=True)
        p.add_argument("--platform", required=True)
        p.add_argument("--uinput", default="/dev/uinput")
        if name == "create":
            p.add_argument("--keepalive", action="store_true",
                           help="hold the device(s) alive; SIGUSR1 injects a known sequence")
    a = ap.parse_args()
    return cmd_plan(a) if a.cmd == "plan" else cmd_create(a)


if __name__ == "__main__":
    sys.exit(main())
