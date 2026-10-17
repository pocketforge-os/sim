#!/usr/bin/env python3
"""Pin every vendored canonical host-route spec file to its .SOURCE sha256.

The host-route spec (scanner, policy, cases, map schema, docs) is vendored
from pocketforge-os/runtime and the runner-label map from
pocketforge-os/pocketforge-automation (PR5 port phase, bead tsp-ihne1.56.5).
Each vendored file has a sibling <file>.SOURCE with source_repo,
source_commit, source_path and fixture_sha256. Any drift between a vendored
file and its pin fails here, and (for the map) also fails the inventory
scan itself with detail=source_sha_mismatch.

Positive and negative controls run in the same invocation: the pinned files
must verify, and a seeded corrupted copy must be rejected.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VENDORED = (
    "tests/fixtures/host-route-policy-v1.json",
    "tests/fixtures/host-route-cases-v1.jsonl",
    "tests/fixtures/input-runner-host-map-v1.schema.json",
    "tests/fixtures/input-runner-host-map-v1.json",
    "docs/host-route-policy.md",
)

REQUIRED_KEYS = ("source_repo", "source_commit", "source_path",
                 "fixture_sha256")


def parse_source(source_path):
    fields = {}
    for line in source_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"malformed .SOURCE line: {line!r}")
        fields[key.strip()] = value.strip()
    missing = [k for k in REQUIRED_KEYS if k not in fields]
    if missing:
        raise ValueError(f"{source_path}: missing keys: {','.join(missing)}")
    return fields


def check_vendored(root, relpath):
    """Return None when the pin holds, else a failure string."""
    vendored = root / relpath
    source = vendored.with_name(vendored.name + ".SOURCE")
    if not vendored.is_file():
        return f"{relpath}: vendored file is missing"
    if not source.is_file():
        return f"{relpath}: .SOURCE pin file is missing"
    try:
        fields = parse_source(source)
    except ValueError as exc:
        return str(exc)
    digest = hashlib.sha256(vendored.read_bytes()).hexdigest()
    if digest != fields["fixture_sha256"]:
        return (f"{relpath}: sha256 {digest} != pinned "
                f"{fields['fixture_sha256']}")
    return None


class VendoredSourcePinTests(unittest.TestCase):
    def test_scanner_is_pinned_by_its_own_source_file(self):
        # The scanner's .SOURCE lives beside the vendored fixtures so the
        # scanner file itself does not need a sibling in tests/.
        fields = parse_source(
            ROOT / "tests" / "fixtures" / "uinput_inventory.py.SOURCE")
        digest = hashlib.sha256(
            (ROOT / "tests" / "uinput_inventory.py").read_bytes()).hexdigest()
        self.assertEqual(digest, fields["fixture_sha256"])
        self.assertEqual(fields["source_commit"],
                         "9cfba117de3bac4d51061738bc6076e027f3c463")

    def test_every_vendored_file_matches_its_pin(self):
        failures = [f for f in
                    (check_vendored(ROOT, rel) for rel in VENDORED) if f]
        self.assertEqual(failures, [])

    def test_seeded_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            vendored = root / "docs" / "host-route-policy.md"
            vendored.write_text("corrupted\n", encoding="utf-8")
            source = root / "docs" / "host-route-policy.md.SOURCE"
            source.write_text(
                "source_repo=pocketforge-os/runtime\n"
                "source_commit=0b1b5d094e173cee3b96399e489d0bf549fdb706\n"
                "source_path=docs/host-route-policy.md\n"
                "fixture_sha256=" + "0" * 64 + "\n",
                encoding="utf-8")
            failure = check_vendored(root, "docs/host-route-policy.md")
            self.assertIsNotNone(failure)
            self.assertIn("!= pinned", failure)

    def test_missing_source_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "host-route-policy.md").write_text(
                "x\n", encoding="utf-8")
            failure = check_vendored(root, "docs/host-route-policy.md")
            self.assertIsNotNone(failure)
            self.assertIn(".SOURCE pin file is missing", failure)


if __name__ == "__main__":
    unittest.main()
