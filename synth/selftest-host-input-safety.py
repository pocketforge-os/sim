#!/usr/bin/env python3
"""Hermetic RED/GREEN proof that suite input never reaches the host input subsystem."""
import os
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evdev_codes as ec  # noqa: E402
import uinput_synth as us  # noqa: E402


DESC = {
    "identity": {
        "manufacturer": "PFTest",
        "model": "Safety Fixture",
        "sdl_guid": "030000005e0400008e02000010010000",
        "match": {"evdev_name": "PFTest Pad", "vid": "045e", "pid": "028e"},
    },
    "inputs": [
        {"id": "south", "kind": "button", "ev_type": "EV_KEY", "code": "BTN_A"},
        {"id": "power", "kind": "button", "ev_type": "EV_KEY", "code": "KEY_POWER",
         "class": "system", "source": "fixture-power"},
    ],
}

EVENT = struct.Struct("<qqHHi")


def input_inventory():
    out = []
    for root in ("/sys/class/input", "/dev/input"):
        try:
            out.extend(os.path.join(root, n) for n in os.listdir(root))
        except FileNotFoundError:
            pass
    return tuple(sorted(out))


class HostInputSafety(unittest.TestCase):
    def test_legacy_positive_and_private_negative_in_one_invocation(self):
        # Positive control: the pre-fix semantic plan advertises a KEY_* event node. systemd 255's
        # input_id therefore gives it ID_INPUT_KEY, 70-power-switch tags it, and logind's
        # button_suitable accepts its KEY_POWER capability.
        specs, resolver = us.plan(DESC)
        system = next(s for s in specs if s.role == "system")
        self.assertIn(ec.KEY["KEY_POWER"], system.keys)
        self.assertTrue(any(k < 0x100 for k in system.keys), "ID_INPUT_KEY positive")
        self.assertTrue(set(system.keys) & us.LOGIND_HOST_ACTION_KEYS, "logind-suitable positive")
        self.assertEqual(resolver["power"]["code_names"], ["KEY_POWER"])

        before = input_inventory()
        with tempfile.TemporaryDirectory(prefix="pf-host-input-safety-") as root:
            input_dir = os.path.join(root, "input")
            real_open = os.open

            def guarded_open(path, flags, mode=0o777):
                if os.fspath(path) == "/dev/uinput":
                    raise AssertionError("private suite backend opened /dev/uinput")
                return real_open(path, flags, mode)

            with mock.patch.object(us.os, "open", side_effect=guarded_open), \
                 mock.patch.object(us, "_ioc", side_effect=AssertionError("private backend issued ioctl")):
                synth = us.FifoSynth(DESC, input_dir).create()
                try:
                    nodes = synth.nodes()
                    self.assertEqual({n["role"] for n in nodes}, {"pad", "system"})
                    for n in nodes:
                        self.assertTrue(stat.S_ISFIFO(os.stat(n["node"]).st_mode), n)
                    system_node = next(n["node"] for n in nodes if n["role"] == "system")
                    read_fd = real_open(system_node, os.O_RDONLY | os.O_NONBLOCK)
                    try:
                        synth.press("power")
                        raw = os.read(read_fd, EVENT.size * 2)
                        self.assertEqual(len(raw), EVENT.size * 2, "press and SYN must be complete")
                        press, syn = EVENT.unpack(raw[:EVENT.size]), EVENT.unpack(raw[EVENT.size:])
                        self.assertEqual(press[2:], (ec.EV["EV_KEY"], ec.KEY["KEY_POWER"], 1))
                        self.assertEqual(syn[2:], (ec.EV["EV_SYN"], ec.SYN["SYN_REPORT"], 0))
                        synth.release("power")
                        raw = os.read(read_fd, EVENT.size * 2)
                        self.assertEqual(len(raw), EVENT.size * 2, "release and SYN must be complete")
                        release, syn = EVENT.unpack(raw[:EVENT.size]), EVENT.unpack(raw[EVENT.size:])
                        self.assertEqual(release[2:], (ec.EV["EV_KEY"], ec.KEY["KEY_POWER"], 0))
                        self.assertEqual(syn[2:], (ec.EV["EV_SYN"], ec.SYN["SYN_REPORT"], 0))
                    finally:
                        os.close(read_fd)
                finally:
                    synth.destroy()
        self.assertEqual(input_inventory(), before, "private backend changed host input inventory")

    def test_raw_uinput_filters_power_but_plan_keeps_semantics(self):
        semantic, resolver = us.plan(DESC)
        safe, _ = us.safe_uinput_plan(DESC)
        self.assertIn(ec.KEY["KEY_POWER"], next(s for s in semantic if s.role == "system").keys)
        self.assertFalse(any(ec.KEY["KEY_POWER"] in s.keys for s in safe))
        self.assertEqual(resolver["power"]["code_names"], ["KEY_POWER"])

        synth = us.Synth(DESC)
        with mock.patch.object(synth, "_emit") as emit:
            with self.assertRaises(us.HostActionRefused):
                synth.press("power")
            emit.assert_not_called()

    def test_every_named_host_action_is_detected_before_code_resolution(self):
        for name in sorted(us.HOST_ACTION_NAMES):
            with self.subTest(name=name):
                desc = dict(DESC)
                desc["inputs"] = [dict(DESC["inputs"][0]), {
                    "id": "danger", "kind": "button",
                    "ev_type": "EV_SW" if name.startswith("SW_") else "EV_KEY",
                    "code": name, "class": "system",
                }]
                self.assertEqual(us.host_action_inputs(desc), {"danger": [name]})

    def test_partial_fifo_write_is_a_hard_failure(self):
        with tempfile.TemporaryDirectory(prefix="pf-host-input-partial-") as root:
            synth = us.FifoSynth(DESC, os.path.join(root, "input")).create()
            try:
                synth._write = lambda _fd, data: len(data) - 1
                with self.assertRaisesRegex(OSError, "partial input-event write"):
                    synth.press("power")
            finally:
                synth.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
