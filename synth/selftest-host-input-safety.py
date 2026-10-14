#!/usr/bin/env python3
"""Hermetic RED/GREEN proof that host-action descriptors fail before uinput open/ioctl."""
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
import uinput_domain_guard as udg  # noqa: E402
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
        {"id": "axis", "kind": "trigger", "ev_type": "EV_ABS", "code": "ABS_Z",
         "range": {"min": -100, "max": 100, "fuzz": 8, "flat": 20}},
    ],
}
EVENT = struct.Struct("<qqHHi")
PFVD_GUEST = udg.Probe(
    udg.Check.NO,
    udg.Check.YES,
    "ok",
    frozenset(("pocketforge,tsp", "allwinner,sun50i-a133", "allwinner,sun50i-a100")),
    "missing",
    "not-probed",
)


def inventory():
    paths = []
    for root in ("/sys/class/input", "/dev/input"):
        try:
            paths.extend(os.path.join(root, name) for name in os.listdir(root))
        except FileNotFoundError:
            pass
    return tuple(sorted(paths))


def read_events(fd):
    try:
        raw = os.read(fd, EVENT.size * 32)
    except BlockingIOError:
        return []
    if len(raw) % EVENT.size:
        raise AssertionError(f"partial input_event read: {len(raw)} bytes")
    return [EVENT.unpack(raw[i:i + EVENT.size]) for i in range(0, len(raw), EVENT.size)]


class HostInputSafety(unittest.TestCase):
    def test_safe_positive_and_host_action_negative_share_open_ioctl_spies(self):
        # The semantic plan deliberately keeps the power mapping: check-synth must continue to
        # validate descriptor fidelity without creating a device.
        # input_id therefore gives it ID_INPUT_KEY, 70-power-switch tags it, and logind's
        # button_suitable accepts its KEY_POWER capability.
        specs, resolver = us.plan(DESC)
        system = next(s for s in specs if s.role == "system")
        self.assertIn(ec.KEY["KEY_POWER"], system.keys)
        self.assertTrue(any(k < 0x100 for k in system.keys), "ID_INPUT_KEY positive")
        self.assertTrue(set(system.keys) & us.LOGIND_HOST_ACTION_KEYS, "logind-suitable positive")
        self.assertEqual(resolver["power"]["code_names"], ["KEY_POWER"])

        safe_desc = dict(DESC)
        safe_desc["inputs"] = [dict(DESC["inputs"][0])]
        with mock.patch.object(us.os, "open", return_value=41) as open_spy, \
             mock.patch.object(us, "_ioc") as ioctl_spy, \
             mock.patch.object(us.os, "write", side_effect=lambda _fd, data: len(data)), \
             mock.patch.object(us, "_resolve_node", return_value="/dev/input/event-fixture"):
            us.Synth(safe_desc, opener=open_spy, domain_probe=PFVD_GUEST).create()
            self.assertGreater(open_spy.call_count, 0, "safe positive must exercise uinput open")
            self.assertGreater(ioctl_spy.call_count, 0, "safe positive must exercise ioctl setup")

            open_spy.reset_mock()
            ioctl_spy.reset_mock()
            with self.assertRaisesRegex(us.HostActionRefused, "power: KEY_POWER"):
                us.Synth(DESC).create()
            open_spy.assert_not_called()
            ioctl_spy.assert_not_called()

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
                with mock.patch.object(us.os, "open") as open_spy, \
                     mock.patch.object(us, "_ioc") as ioctl_spy:
                    with self.assertRaises(us.HostActionRefused):
                        us.Synth(desc).create()
                    open_spy.assert_not_called()
                    ioctl_spy.assert_not_called()

    def test_private_fifo_power_path_never_touches_host_input(self):
        before = inventory()
        real_open = os.open
        with tempfile.TemporaryDirectory(prefix="pf-input-safety-") as root:
            input_dir = os.path.join(root, "input")

            def guarded_open(path, flags, mode=0o777):
                if os.fspath(path) == "/dev/uinput":
                    raise AssertionError("FIFO backend opened /dev/uinput")
                return real_open(path, flags, mode)

            with mock.patch.object(us.os, "open", side_effect=guarded_open), \
                 mock.patch.object(us, "_ioc", side_effect=AssertionError("FIFO backend used ioctl")):
                synth = us.FifoSynth(DESC, input_dir, clock_ns=lambda: 1_700_000_000_123_456_000).create()
                try:
                    nodes = synth.nodes()
                    self.assertEqual({node["role"] for node in nodes}, {"pad", "system"})
                    self.assertTrue(all(stat.S_ISFIFO(os.stat(node["node"]).st_mode)
                                        for node in nodes))
                    node = next(node["node"] for node in nodes if node["role"] == "system")
                    reader = real_open(node, os.O_RDONLY | os.O_NONBLOCK)
                    try:
                        synth.press("power")
                        synth.release("power")
                        events = read_events(reader)
                    finally:
                        os.close(reader)
                    self.assertEqual([event[2:] for event in events], [
                        (ec.EV["EV_KEY"], ec.KEY["KEY_POWER"], 1),
                        (ec.EV["EV_SYN"], ec.SYN["SYN_REPORT"], 0),
                        (ec.EV["EV_KEY"], ec.KEY["KEY_POWER"], 0),
                        (ec.EV["EV_SYN"], ec.SYN["SYN_REPORT"], 0),
                    ])
                    self.assertTrue(all(event[:2] == (1_700_000_000, 123_456)
                                        for event in events), "real timeval fields")
                finally:
                    synth.destroy()
        self.assertEqual(inventory(), before)

    def test_fifo_matches_linux_6_8_input_core_disposition(self):
        with tempfile.TemporaryDirectory(prefix="pf-input-disposition-") as root:
            synth = us.FifoSynth(DESC, os.path.join(root, "input")).create()
            node = next(node["node"] for node in synth.nodes() if node["role"] == "pad")
            reader = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
            try:
                # Unadvertised negative; advertised positive.
                self.assertFalse(synth._emit("pad", ec.EV["EV_KEY"], ec.KEY["KEY_POWER"], 1))
                self.assertFalse(synth._emit("pad", ec.EV["EV_ABS"], ec.ABS["ABS_RX"], 10))
                self.assertFalse(synth._emit("pad", ec.EV["EV_SYN"], ec.SYN["SYN_REPORT"], 0))
                self.assertEqual(read_events(reader), [])
                synth.press("south")
                self.assertEqual(len(read_events(reader)), 2)

                # Repeated state negative; explicit autorepeat value=2 positive.
                synth.press("south")
                self.assertEqual(read_events(reader), [])
                self.assertTrue(synth._emit("pad", ec.EV["EV_KEY"], ec.BTN["BTN_A"], 2))
                synth._syn("pad")
                self.assertEqual([event[4] for event in read_events(reader)], [2, 0])
                synth.release("south")
                self.assertEqual(len(read_events(reader)), 2)

                axis = ec.ABS["ABS_Z"]
                # Initial/unchanged ABS negative; changed ABS positive.
                self.assertFalse(synth._emit("pad", ec.EV["EV_ABS"], axis, 0))
                self.assertTrue(synth._emit("pad", ec.EV["EV_ABS"], axis, 40))
                synth._syn("pad")
                self.assertEqual([event[4] for event in read_events(reader)], [40, 0])
                self.assertFalse(synth._emit("pad", ec.EV["EV_ABS"], axis, 40))

                # fuzz suppresses a nearby value, then defuzzes a farther one. flat is consumer
                # metadata in Linux 6.8, so it does not dead-zone raw input_event values.
                self.assertFalse(synth._emit("pad", ec.EV["EV_ABS"], axis, 43))
                self.assertTrue(synth._emit("pad", ec.EV["EV_ABS"], axis, 46))
                synth._syn("pad")
                self.assertEqual([event[4] for event in read_events(reader)], [41, 0])
                self.assertTrue(synth._emit("pad", ec.EV["EV_ABS"], axis, 20),
                                "flat must not alter raw evdev events")
                synth._syn("pad")
                self.assertEqual(len(read_events(reader)), 2)

                # SYN_REPORT only flushes a packet that has a pending event.
                self.assertFalse(synth._emit("pad", ec.EV["EV_SYN"], ec.SYN["SYN_REPORT"], 0))
                self.assertEqual(read_events(reader), [])
            finally:
                os.close(reader)
                synth.destroy()

    def test_partial_fifo_write_is_a_hard_failure(self):
        with tempfile.TemporaryDirectory(prefix="pf-input-partial-") as root:
            synth = us.FifoSynth(DESC, os.path.join(root, "input")).create()
            try:
                synth._write = lambda _fd, data: len(data) - 1
                with self.assertRaisesRegex(OSError, "partial input-event write"):
                    synth.press("south")
            finally:
                synth.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
