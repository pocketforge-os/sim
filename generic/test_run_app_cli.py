#!/usr/bin/env python3
"""Hermetic controls for `./sim run-app` host-boundary flags (tsp-h5ed.46.4).

No docker, no image, no /dev/uinput: `--print-cmd` must print the exact `docker run` argument
list without touching docker, and every rejected flag combination must fail with a named error.
A fake `docker` on PATH fails loudly if any test reaches it.

    python3 -m unittest generic/test_run_app_cli.py      (from the repo root)
"""
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim"
SPEC = importlib.util.spec_from_file_location("generic_capture", ROOT / "generic" / "generic_capture.py")


class RunAppCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pf-run-app-cli-")
        d = Path(self.tmp.name)
        self.app = d / "app.arm64"; self.app.write_bytes(b"\x7fELF")
        self.fb0 = d / "fb0"; self.fb0.write_bytes(bytes(16)); self.fb0.chmod(0o666)
        self.bin = d / "bin"; self.bin.mkdir()
        self.docker_log = d / "docker-called"
        fake = self.bin / "docker"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> {shlex.quote(str(self.docker_log))}\nexit 97\n")
        fake.chmod(0o755)
        # `stat` shim: FAKE_STAT=<uid gid mode> stands in for a root-/gid-0-owned fb0 the test
        # cannot create without root; unset, it is the real stat.
        shim = self.bin / "stat"
        shim.write_text('#!/bin/sh\n[ -n "$FAKE_STAT" ] && { echo "$FAKE_STAT"; exit 0; }\nexec /usr/bin/stat "$@"\n')
        shim.chmod(0o755)
        self.fake_stat = None
        self.cwd = d / "cwd"; self.cwd.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_app(self, *flags):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", NO_COLOR="1")
        env.pop("FAKE_STAT", None)
        if self.fake_stat:
            env["FAKE_STAT"] = self.fake_stat
        return subprocess.run([str(SIM), "run-app", "a133", str(self.app), *flags],
                              cwd=self.cwd, env=env, text=True, capture_output=True)

    def printed(self, *flags):
        r = self.run_app("--print-cmd", *flags)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.docker_log.exists(), "--print-cmd must not invoke docker")
        self.assertEqual(list(self.cwd.iterdir()), [], "--print-cmd must not create sim-capture/")
        argv = shlex.split(r.stdout)
        self.assertEqual(argv[:3], ["docker", "run", "--rm"])
        return argv

    def assert_rejected(self, flags, needle):
        r = self.run_app(*flags)
        self.assertNotEqual(r.returncode, 0, f"{flags} was accepted")
        self.assertIn(needle, r.stderr)
        self.assertFalse(self.docker_log.exists(), f"{flags} reached docker before failing")

    def test_default_keeps_synth_caps_and_private_fb0(self):
        # Negative control for --no-synth: without it the in-container synthesiser path stays.
        argv = self.printed()
        joined = " ".join(argv)
        self.assertIn("--device /dev/uinput", joined)
        self.assertIn("-v /dev/input:/dev/input", joined)
        self.assertNotIn("/run/pfvd/fb0", joined)
        self.assertEqual(argv[argv.index("pocketforge-sim:demo"):][:3],
                         ["pocketforge-sim:demo", "run-app", "a133"])

    def test_no_synth_passes_only_named_nodes(self):
        argv = self.printed("--no-synth", "--input-device", "/dev/input/event7",
                            "--input-device", "/dev/input/event12")
        joined = " ".join(argv)
        self.assertNotIn("/dev/uinput", joined)
        self.assertNotIn("/dev/input:/dev/input", joined)
        self.assertIn("--device /dev/input/event7 --device /dev/input/event12", joined)
        # bwrap still needs these (docker/README.md); only the uinput/input-tree access goes.
        self.assertIn("--device-cgroup-rule c 13:* rmw", joined)
        self.assertIn("--cap-add SYS_ADMIN", joined)

    def test_fb0_path_binds_host_file_and_passes_it_to_capture(self):
        argv = self.printed("--fb0-path", str(self.fb0))
        self.assertIn(f"{self.fb0}:/run/pfvd/fb0", argv)
        tail = argv[argv.index("run-app"):]
        self.assertEqual(tail[tail.index("--fb0"):][:2], ["--fb0", "/run/pfvd/fb0"])

    def test_app_args_follow_double_dash(self):
        argv = self.printed("--fb0-path", str(self.fb0), "--", "--sim-frame", "x y")
        self.assertEqual(argv[-3:], ["--", "--sim-frame", "x y"])

    def test_rejects_input_device_without_no_synth(self):
        self.assert_rejected(["--input-device", "/dev/input/event7", "--print-cmd"], "--no-synth")

    def test_rejects_no_synth_without_input_device(self):
        self.assert_rejected(["--no-synth", "--print-cmd"], "--input-device")

    def test_rejects_non_event_node(self):
        self.assert_rejected(["--no-synth", "--input-device", "/dev/uinput", "--print-cmd"],
                             "/dev/input/eventN")
        self.assert_rejected(["--no-synth", "--input-device", "/dev/input/event7x", "--print-cmd"],
                             "/dev/input/eventN")

    def test_rejects_missing_fb0_path(self):
        self.assert_rejected(["--fb0-path", str(self.fb0) + ".absent", "--print-cmd"],
                             "--fb0-path")

    def test_rejects_fb0_path_the_sandboxed_app_cannot_write(self):
        # The app is uid 0 with no capabilities inside bwrap: a caller's 0600 file is EACCES.
        self.fb0.chmod(0o600)
        self.assert_rejected(["--fb0-path", str(self.fb0), "--print-cmd"], "chmod o+rw")
        self.fb0.chmod(0o604)
        self.assert_rejected(["--fb0-path", str(self.fb0), "--print-cmd"], "chmod o+rw")

    def test_fb0_path_permission_class_follows_owner(self):
        # Positive and negative controls for each class the kernel applies to uid 0 without caps.
        for stat_out, ok, fix in (("0 0 600", True, None), ("0 0 400", False, "chmod u+rw"),
                                  ("0 0 200", False, "chmod u+rw"), ("0 0 066", False, "chmod u+rw"),
                                  ("1000 0 660", True, None), ("1000 0 606", False, "chmod g+rw"),
                                  ("1000 1000 606", True, None), ("1000 1000 660", False, "chmod o+rw")):
            with self.subTest(stat=stat_out):
                self.fake_stat = stat_out
                if ok:
                    self.printed("--fb0-path", str(self.fb0))
                else:
                    self.assert_rejected(["--fb0-path", str(self.fb0), "--print-cmd"], fix)

    def test_rejects_fb0_path_directory(self):
        self.assert_rejected(["--fb0-path", self.tmp.name, "--print-cmd"], "--fb0-path")

    def test_rejects_missing_flag_values(self):
        self.assert_rejected(["--fb0-path"], "--fb0-path needs")
        self.assert_rejected(["--no-synth", "--input-device"], "--input-device needs")

    def test_live_run_requires_existing_char_node(self):
        # Without --print-cmd the node must exist as a character device on this host.
        self.assert_rejected(["--no-synth", "--input-device", "/dev/input/event999999"],
                             "not a character device")


class ExternalFramebuffer(unittest.TestCase):
    def setUp(self):
        self.gc = importlib.util.module_from_spec(SPEC)
        try:
            SPEC.loader.exec_module(self.gc)
        except ModuleNotFoundError as e:  # skin_model needs tomllib (python >= 3.11)
            self.fail(f"generic_capture import failed: {e}")
        self.tmp = tempfile.TemporaryDirectory(prefix="pf-fb0-")

    def tearDown(self):
        self.tmp.cleanup()

    def test_private_buffer_is_created_and_sized(self):
        fb = self.gc.prepare_fb0(None, 4, 2, self.tmp.name)
        self.assertEqual(os.path.dirname(fb), self.tmp.name)
        self.assertEqual(os.path.getsize(fb), 4 * 2 * 4)

    def write_fb(self, data, mode=0o666):
        path = os.path.join(self.tmp.name, "fb0")
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, mode)
        return path

    def test_external_buffer_is_used_unchanged(self):
        path = self.write_fb(b"\x11" * 64)
        self.assertEqual(self.gc.prepare_fb0(path, 4, 2, "/nonexistent-work"), path)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"\x11" * 64, "an external fb0 must never be truncated")

    def test_external_buffer_without_other_rw_is_rejected(self):
        path = self.write_fb(bytes(32), 0o600)
        with self.assertRaisesRegex(RuntimeError, "chmod o\\+rw"):
            self.gc.prepare_fb0(path, 4, 2, self.tmp.name)

    def test_sandbox_permission_class(self):
        can = self.gc.sandbox_can_rw
        self.assertTrue(can(0, 0, 0o600)); self.assertTrue(can(1000, 1000, 0o606))
        self.assertTrue(can(1000, 0, 0o660))
        for uid, gid, mode in ((0, 0, 0o400), (0, 0, 0o200), (0, 0, 0o000), (0, 0, 0o066),
                               (1000, 0, 0o606), (1000, 1000, 0o660), (1000, 1000, 0o604)):
            with self.subTest(uid=uid, gid=gid, mode=oct(mode)):
                self.assertFalse(can(uid, gid, mode))

    def test_short_external_buffer_is_rejected(self):
        path = self.write_fb(bytes(31))
        with self.assertRaisesRegex(RuntimeError, "31 bytes.*32"):
            self.gc.prepare_fb0(path, 4, 2, self.tmp.name)

    def test_missing_external_buffer_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            self.gc.prepare_fb0(os.path.join(self.tmp.name, "absent"), 4, 2, self.tmp.name)


if __name__ == "__main__":
    unittest.main()
