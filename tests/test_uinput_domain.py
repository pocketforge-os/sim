import csv
import hashlib
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "synth"))
import uinput_domain_guard as guard  # noqa: E402
import uinput_synth as synth  # noqa: E402
from unittest import mock


class UinputDomainTest(unittest.TestCase):
    def test_probe_failures_and_malformed_identity_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sleeper = root / "sleeping-probe"
            sleeper.write_text("#!/bin/sh\nexec /bin/sleep 5\n", encoding="utf-8")
            sleeper.chmod(0o755)
            missing = root / "missing-probe"
            started = time.monotonic()
            self.assertIs(guard._command_check("--quiet", program=str(sleeper), timeout=0.05), guard.Check.ERROR)
            self.assertLess(time.monotonic() - started, 1)
            self.assertIs(guard._command_check("--quiet", program=str(missing), timeout=0.05), guard.Check.ERROR)
            self.assertIsNone(guard._run_loginctl("list-sessions", program=str(sleeper), timeout=0.05))
            self.assertIsNone(guard._run_loginctl("list-sessions", program=str(missing), timeout=0.05))
            compatible = root / "compatible"
            compatible.write_bytes(b"pocketforge,tsp\0allwinner,sun50i-a133\0allwinner,sun50i-a100")
            self.assertEqual(guard._probe_dt(compatible), ("error", frozenset()))
            marker = root / "marker"
            marker.symlink_to(root / "missing-target")
            self.assertEqual(guard._probe_marker(marker), "invalid")

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
                         "538fce952f5de6cb901c46c1919aedc44dcd436e753f7786c1a6e5fe21ad2fb9")
        with fixture.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(rows), 23)
        for row in rows:
            probe = guard.Probe(guard.Check(row["container"]), guard.Check(row["vm"]), row["dt"],
                frozenset() if row["compatibles"] == "-" else frozenset(row["compatibles"].split(";")),
                row["marker"], row["logind"])
            got = guard.evaluate(probe)
            self.assertEqual((got.allowed, got.reason, got.domain.value, got.probe or "-"),
                             (row["verdict"] == "allow", row["reason"], row["domain"], row["probe"]), row["case"])
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
