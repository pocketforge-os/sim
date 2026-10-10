#!/usr/bin/env python3
"""Hermetic controls for `fb-anim` (tsp-h5ed.46.28): the image's continuous fb0 writer.

Runs the native x86 build (the image's `FB_ANIM_X86`; else compiled from `fb-anim.c` with `cc`)
against plain files standing in for /dev/fb0 and its fb0.hdr sidecar (docs/FB0-SIDECAR.md).
No docker, no qemu, no /dev/uinput.

    python3 -m unittest generic/test_fb_anim.py      (from the repo root)
"""
import os
from pathlib import Path
import signal
import struct
import subprocess
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
W, H = 64, 32
HDR = struct.Struct("<4sHHIIIIQQ24x")


def binary(work):
    baked = os.environ.get("FB_ANIM_X86")
    if baked:
        return baked
    out = os.path.join(work, "fb-anim")
    subprocess.run(["cc", "-O2", "-o", out, str(HERE / "fb-anim.c")], check=True)
    return out


class FbAnim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="pf-fb-anim-bin-")
        cls.bin = binary(cls.build.name)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pf-fb-anim-")
        self.fb0 = os.path.join(self.tmp.name, "fb0")
        with open(self.fb0, "wb") as f:
            f.truncate(W * H * 4)
        self.hdr = os.path.join(self.tmp.name, "fb0.hdr")
        with open(self.hdr, "wb") as f:
            f.truncate(64)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill(); p.wait()
        self.tmp.cleanup()

    def start(self, header=True, **extra):
        env = dict(os.environ, PF_FB0=self.fb0, PF_FB_WIDTH=str(W), PF_FB_HEIGHT=str(H))
        env.pop("PF_FB0_HDR", None)
        if header:
            env["PF_FB0_HDR"] = self.hdr
        env.update(extra)
        p = subprocess.Popen([self.bin], env=env, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def header(self):
        with open(self.hdr, "rb") as f:
            return HDR.unpack(f.read(64))

    def frame(self):
        with open(self.fb0, "rb") as f:
            return f.read()

    def wait_seq(self, at_least, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            seq = self.header()[7]
            if seq >= at_least and seq % 2 == 0:
                return seq
            time.sleep(0.01)
        self.fail(f"header seq did not reach {at_least}: {self.header()}")

    def test_redraws_until_sigterm_and_publishes_the_header(self):
        p = self.start()
        first = self.wait_seq(4)
        magic, version, size, w, h, stride, fourcc, seq, ts = self.header()
        self.assertEqual((magic, version, size, w, h, stride, fourcc),
                         (b"PFB0", 1, 64, W, H, W * 4, 0x34325258))
        self.assertGreater(ts, 0)
        a = self.frame(); time.sleep(0.2); b = self.frame()
        self.assertNotEqual(a, b, "the frame must keep changing while the app runs")
        later = self.wait_seq(first + 6)
        self.assertIsNone(p.poll(), "fb-anim must keep running")
        # About 60 Hz: 0.2 s and more must have produced well over 3 frames (seq advances by 2).
        self.assertGreater((later - first) // 2, 3)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5), 0, p.stderr.read())
        self.assertEqual(self.header()[7] % 2, 0, "SIGTERM must leave the seqlock even")

    def test_without_a_header_the_sidecar_is_untouched(self):
        p = self.start(header=False)
        end = time.monotonic() + 5
        while self.frame() == bytes(W * H * 4) and time.monotonic() < end:
            time.sleep(0.01)
        self.assertNotEqual(self.frame(), bytes(W * H * 4), "fb-anim drew nothing")
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5), 0)
        self.assertEqual(Path(self.hdr).read_bytes(), bytes(64))

    def test_named_failures(self):
        cases = (
            (dict(PF_FB_WIDTH="0"), "PF_FB_WIDTH/HEIGHT required"),
            (dict(PF_FB0=self.fb0 + ".absent"), "fb0.absent"),
            (dict(PF_FB_HEIGHT=str(H + 1)), "smaller than"),
            (dict(PF_FB0_HDR=self.hdr + ".absent"), "fb0.hdr.absent"),
        )
        for extra, needle in cases:
            with self.subTest(extra=extra):
                p = self.start(**extra)
                self.assertNotEqual(p.wait(timeout=5), 0)
                self.assertIn(needle, p.stderr.read())
        # Positive control in the same invocation: the plain environment runs.
        p = self.start()
        self.wait_seq(2)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()
