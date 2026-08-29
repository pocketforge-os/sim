#!/usr/bin/env python3
"""Focused negative and positive controls for shell frame-content validation."""
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).with_name("check-shell.py")
SPEC = importlib.util.spec_from_file_location("check_shell", MODULE_PATH)
CHECK_SHELL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK_SHELL)


class FrameContentTest(unittest.TestCase):
    def test_blank_frame_fails(self):
        self.assertFalse(CHECK_SHELL.frame_content(bytes(64 * 4))[0])

    def test_uniform_nonzero_frame_fails(self):
        self.assertFalse(CHECK_SHELL.frame_content(b"\x01\x02\x03\x04" * 64)[0])

    def test_composed_frame_passes(self):
        background = b"\x01\x02\x03\x04"
        foreground = b"\x05\x06\x07\x08"
        frame = background * 99 + foreground
        self.assertTrue(CHECK_SHELL.frame_content(frame)[0])


if __name__ == "__main__":
    unittest.main()
