import csv
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "synth"))
import uinput_domain_guard as guard  # noqa: E402
import uinput_synth as synth  # noqa: E402
from unittest import mock


class UinputDomainTest(unittest.TestCase):
    def test_synth_creator_refuses_before_its_opener_and_allows_guest(self):
        desc = {
            "identity": {
                "manufacturer": "PFTest", "model": "Guard Fixture",
                "sdl_guid": "030000005e0400008e02000010010000",
                "match": {"evdev_name": "PFTest Pad", "vid": "045e", "pid": "028e"},
            },
            "inputs": [{"id": "south", "kind": "button", "ev_type": "EV_KEY", "code": "BTN_A"}],
        }
        bare = guard.Probe(guard.Check.NO, guard.Check.NO, "missing", frozenset(),
                           "missing", "not-probed")
        guest = guard.Probe(guard.Check.NO, guard.Check.YES, "ok",
                            frozenset(("pocketforge,tsp", "allwinner,sun50i-a133",
                                       "allwinner,sun50i-a100")), "missing", "not-probed")
        calls = []
        with self.assertRaisesRegex(guard.UnsafeUinputDomain, "reason=unsafe_uinput_domain"):
            synth.Synth(desc, opener=lambda *args: calls.append(args) or 61,
                        domain_probe=bare).create()
        self.assertEqual(calls, [])
        with mock.patch.object(synth, "_ioc"), \
             mock.patch.object(synth.os, "write", return_value=1), \
             mock.patch.object(synth, "_resolve_node", return_value="mock-event"):
            synth.Synth(desc, opener=lambda *args: calls.append(args) or 61,
                        domain_probe=guest).create()
        self.assertEqual(len(calls), 1)

    def test_shared_fixture_and_mocked_opener(self):
        fixture = ROOT / "tests/fixtures/uinput-domain-v1.tsv"
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),
                         "6f58e75f72b48a3e426417a55056b688b9c1e4cba2cf7a9e92fea686e47a050d")
        with fixture.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(rows), 18)
        for row in rows:
            probe = guard.Probe(guard.Check(row["container"]), guard.Check(row["vm"]), row["dt"],
                frozenset() if row["compatibles"] == "-" else frozenset(row["compatibles"].split(";")),
                row["marker"], row["logind"])
            got = guard.evaluate(probe)
            self.assertEqual((got.allowed, got.reason, got.domain.value),
                             (row["verdict"] == "allow", row["reason"], row["domain"]), row["case"])
            calls = []
            if got.allowed:
                self.assertEqual(guard.guarded_open("mock", 0, probe=probe,
                    opener=lambda *args: calls.append(args) or 51), 51)
            else:
                with self.assertRaisesRegex(guard.UnsafeUinputDomain, "reason=unsafe_uinput_domain"):
                    guard.guarded_open("mock", 0, probe=probe,
                                       opener=lambda *args: calls.append(args) or 51)
            self.assertEqual(len(calls), int(got.allowed), row["case"])


if __name__ == "__main__":
    unittest.main()
