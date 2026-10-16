#!/usr/bin/env python3
"""Hermetic controls for `./sim run-app` host-boundary flags (tsp-h5ed.46.4).

No docker, no image, no /dev/uinput: `--print-cmd` must print the exact `docker run` argument
list without touching docker, and every rejected flag combination must fail with a named error.
A fake `docker` on PATH fails loudly if any test reaches it.

    python3 -m unittest generic/test_run_app_cli.py      (from the repo root)
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim"
SPEC = importlib.util.spec_from_file_location("generic_capture", ROOT / "generic" / "generic_capture.py")


class SimGuiDeprecation(unittest.TestCase):
    """`./sim gui` is deprecated in favour of pfvd (D9): its help and its startup say so and point
    at `pfvd --platform <checkout>`; nothing else changes (the same docker preflight follows)."""

    NOTICE = "./sim gui is deprecated: use pfvd, the 3D device simulator (pfvd --platform <platform checkout>"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pf-sim-gui-")
        self.bin = Path(self.tmp.name) / "bin"; self.bin.mkdir()
        fake = self.bin / "docker"
        fake.write_text("#!/bin/sh\nexit 97\n")  # `docker info` fails: the preflight stops the run
        fake.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def sim(self, *args):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", NO_COLOR="1")
        return subprocess.run([str(SIM), *args], env=env, text=True, capture_output=True,
                              stdin=subprocess.DEVNULL)

    def test_help_marks_gui_deprecated(self):
        r = self.sim("help")  # the usage text goes to stderr
        self.assertEqual(r.returncode, 0, r.stderr)
        gui = [l for l in r.stderr.splitlines() if l.lstrip().startswith("gui")]
        self.assertEqual(len(gui), 1, r.stderr)
        block = r.stderr[r.stderr.index(gui[0]):r.stderr.index("run-app <device>")]
        self.assertIn("Deprecated: use pfvd", block)
        self.assertIn("pfvd --platform <checkout>", block)
        # Negative control: no other command is marked deprecated.
        self.assertEqual(r.stderr.count("Deprecated"), 1, r.stderr)

    def test_gui_startup_prints_the_notice_first_then_behaves_as_before(self):
        r = self.sim("gui", "a133")
        self.assertNotEqual(r.returncode, 0, "the fake docker cannot run the window")
        lines = r.stderr.splitlines()
        self.assertTrue(lines and self.NOTICE in lines[0], r.stderr)
        # Unchanged behaviour: the same docker preflight runs right after the notice.
        self.assertIn("docker", "\n".join(lines[1:]))
        # Negative control: another command prints no notice.
        other = self.sim("run", "a133")
        self.assertNotIn("deprecated", other.stderr)


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
        # `stat` shim: FAKE_STAT=<uid gid mode dev:ino type> stands in for a root-/gid-0-owned fb0
        # the test cannot create without root; unset, it is the real stat.
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

    def test_default_uses_private_input_and_private_fb0(self):
        argv = self.printed()
        joined = " ".join(argv)
        self.assertNotIn("/dev/uinput", joined)
        self.assertNotIn("/dev/input", joined)
        self.assertNotIn("--device-cgroup-rule", joined)
        self.assertIn("--cap-add SYS_ADMIN", joined)
        self.assertNotIn("/run/pfvd/fb0", joined)
        # No host display/clipboard route either (uinput incident PR5, tsp-ihne1.56.5).
        self.assertNotIn("DISPLAY", joined)
        self.assertNotIn(".X11-unix", joined)
        self.assertEqual(argv[argv.index("pocketforge-sim:demo"):][:3],
                         ["pocketforge-sim:demo", "run-app", "a133"])

    def test_run_app_has_an_init_to_forward_sigterm(self):
        # PID 1 without a handler ignores SIGTERM, so `docker stop` would wait out its timeout.
        argv = self.printed()
        self.assertEqual(argv[argv.index("--rm") + 1], "--init")
        self.assertEqual(argv.count("--init"), 1)

    def test_host_node_and_window_flags_are_rejected(self):
        # tsp-ihne1.56.5 (uinput incident PR5): run-app no longer accepts caller-owned host
        # evdev nodes or a live host-display window. Each removed flag is an unknown flag, in
        # every combination, and nothing reaches docker. The default private-input route stays
        # as the positive control in the same invocation.
        for flags in (["--no-synth", "--input-device", "/dev/input/event7", "--print-cmd"],
                      ["--no-synth", "--input-device", "/dev/uinput", "--print-cmd"],
                      ["--input-device", "/dev/input/event7", "--print-cmd"],
                      ["--no-synth", "--print-cmd"],
                      ["--window", "--print-cmd"],
                      ["--shot", "x.ppm", "--window", "--print-cmd"]):
            with self.subTest(flags=flags):
                self.assert_rejected(flags, "unknown flag")
        d = self.printed()
        self.assertNotIn("--device", d)
        self.assertNotIn("--cap-drop", d)

    def test_fb0_path_binds_host_file_and_passes_it_to_capture(self):
        argv = self.printed("--fb0-path", str(self.fb0))
        self.assertIn(f"{self.fb0}:/run/pfvd/fb0", argv)
        tail = argv[argv.index("run-app"):]
        self.assertEqual(tail[tail.index("--fb0"):][:2], ["--fb0", "/run/pfvd/fb0"])

    def test_fb0_path_pins_the_checked_inode(self):
        # The host check's dev:ino travels with the bind, so a swap before docker binds is refused.
        argv = self.printed("--fb0-path", str(self.fb0))
        st = self.fb0.stat()
        tail = argv[argv.index("run-app"):]
        self.assertEqual(tail[tail.index("--fb0-ident"):][:2], ["--fb0-ident", f"{st.st_dev}:{st.st_ino}"])
        # Negative control: no --fb0-path, no pin.
        self.assertNotIn("--fb0-ident", self.printed())

    def test_app_args_follow_double_dash(self):
        argv = self.printed("--fb0-path", str(self.fb0), "--", "--sim-frame", "x y")
        self.assertEqual(argv[-3:], ["--", "--sim-frame", "x y"])

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
                self.fake_stat = f"{stat_out} 1:2 regular file"
                if ok:
                    self.printed("--fb0-path", str(self.fb0))
                else:
                    self.assert_rejected(["--fb0-path", str(self.fb0), "--print-cmd"], fix)

    def test_rejects_fb0_path_directory(self):
        self.assert_rejected(["--fb0-path", self.tmp.name, "--print-cmd"], "--fb0-path")

    def test_rejects_fb0_path_fifo(self):
        fifo = Path(self.tmp.name) / "fifo"; os.mkfifo(fifo, 0o666)
        self.assert_rejected(["--fb0-path", str(fifo), "--print-cmd"], "existing regular file")
        self.printed("--fb0-path", str(self.fb0))  # positive control, same invocation

    def test_rejects_missing_flag_values(self):
        self.assert_rejected(["--fb0-path"], "--fb0-path needs")

    def capture_args(self, argv):
        return argv[argv.index("run-app"):]

    def test_no_frame_is_passed_to_capture(self):
        self.assertIn("--no-frame", self.capture_args(self.printed("--fb0-path", str(self.fb0), "--no-frame")))
        # Negative control: without the flag the capture keeps its frame.ppm.
        self.assertNotIn("--no-frame", self.printed("--fb0-path", str(self.fb0)))

    def test_no_frame_conflicts_with_a_frame_consumer(self):
        self.assert_rejected(["--no-frame", "--shot", "x.ppm", "--print-cmd"], "--no-frame writes no frame")
        self.printed("--no-frame")  # positive control, same invocation

    def make_hdr(self, mode=0o666, size=64):
        hdr = Path(self.tmp.name) / "fb0.hdr"
        hdr.write_bytes(bytes(size)); hdr.chmod(mode)
        return hdr

    def test_fb0_hdr_path_binds_a_second_file(self):
        hdr = self.make_hdr()
        argv = self.printed("--fb0-path", str(self.fb0), "--fb0-hdr-path", str(hdr))
        self.assertIn(f"{hdr}:/run/pfvd/fb0.hdr", argv)
        self.assertIn(f"{self.fb0}:/run/pfvd/fb0", argv)
        st = hdr.stat()
        tail = self.capture_args(argv)
        self.assertEqual(tail[tail.index("--fb0-hdr"):][:4],
                         ["--fb0-hdr", "/run/pfvd/fb0.hdr", "--fb0-hdr-ident", f"{st.st_dev}:{st.st_ino}"])
        # Negative control: no header flag, no header bind.
        self.assertNotIn("fb0.hdr", " ".join(self.printed("--fb0-path", str(self.fb0))))

    def test_rejects_fb0_hdr_path_misuse(self):
        hdr = self.make_hdr()
        self.assert_rejected(["--fb0-hdr-path", str(hdr), "--print-cmd"], "needs --fb0-path")
        self.assert_rejected(["--fb0-path", str(self.fb0), "--fb0-hdr-path", str(hdr) + ".absent",
                              "--print-cmd"], "--fb0-hdr-path must name an existing regular file")
        self.assert_rejected(["--fb0-path", str(self.fb0), "--fb0-hdr-path", str(self.fb0),
                              "--print-cmd"], "the same file")
        self.assert_rejected(["--fb0-path", str(self.fb0), "--fb0-hdr-path"], "--fb0-hdr-path needs")
        hdr.chmod(0o600)
        self.assert_rejected(["--fb0-path", str(self.fb0), "--fb0-hdr-path", str(hdr), "--print-cmd"],
                             "chmod o+rw")
        hdr.chmod(0o666)
        self.printed("--fb0-path", str(self.fb0), "--fb0-hdr-path", str(hdr))  # positive control

    def test_bundled_app_runs_from_the_image(self):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", NO_COLOR="1")
        r = subprocess.run([str(SIM), "run-app", "a133", "bundled:fb-anim", "--print-cmd"],
                           cwd=self.cwd, env=env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = shlex.split(r.stdout)
        self.assertNotIn("/run-app", " ".join(argv), "a bundled app binds no host binary")
        self.assertEqual(self.capture_args(argv)[:3], ["run-app", "a133", "/opt/pf/apps/fb-anim.arm64"])
        # Negative control: a host binary is still bound read-only at /run-app.
        host = self.printed()
        self.assertIn(f"{self.app}:/run-app:ro", host)
        self.assertEqual(self.capture_args(host)[:3], ["run-app", "a133", "/run-app"])

    def test_rejects_bad_bundled_names(self):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", NO_COLOR="1")
        for spec in ("bundled:", "bundled:../etc/passwd", "bundled:a/b", "bundled:-x"):
            with self.subTest(spec=spec):
                r = subprocess.run([str(SIM), "run-app", "a133", spec, "--print-cmd"],
                                   cwd=self.cwd, env=env, text=True, capture_output=True)
                self.assertNotEqual(r.returncode, 0, f"{spec} was accepted")
                self.assertIn("bundled:<name>", r.stderr)
        self.assertFalse(self.docker_log.exists())


class InContainerRunApp(unittest.TestCase):
    """The image side of run-app: the entrypoint's app check and the harness's /dev/input check.

    Fake `python3` and `bwrap` on PATH record their argv, so nothing runs for real.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pf-run-app-image-")
        d = Path(self.tmp.name)
        self.bin = d / "bin"; self.bin.mkdir()
        self.log = d / "called"
        for name in ("python3", "bwrap"):
            fake = self.bin / name
            fake.write_text(f"#!/bin/sh\necho {name} \"$@\" >> {shlex.quote(str(self.log))}\n")
            fake.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin", QEMU_TSP="/q", ROOTFS="/r",
                        SKIN_RENDER="/s", PLATFORM="/p")
        self.app = d / "app.arm64"; self.app.write_bytes(b"\x7fELF")

    def tearDown(self):
        self.tmp.cleanup()

    def called(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_entrypoint_names_a_missing_app(self):
        r = subprocess.run(["bash", str(ROOT / "docker" / "entrypoint.sh"), "run-app", "a133",
                            "/opt/pf/apps/no-such-app.arm64"], env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("no app at /opt/pf/apps/no-such-app.arm64", r.stderr)
        self.assertEqual(self.called(), "")
        # Positive control: an existing app reaches generic_capture.
        r = subprocess.run(["bash", str(ROOT / "docker" / "entrypoint.sh"), "run-app", "a133", str(self.app)],
                           env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"generic_capture.py --device a133 --platform /p --app {self.app}", self.called())

    def test_entrypoint_rejects_the_host_display_window_verb(self):
        # tsp-ihne1.56.5: `pf-sim window` (the host-DISPLAY demo mode) is gone; window-selftest
        # (its own Xvfb :99) stays. The verb must fail closed, with or without DISPLAY set.
        for env in (self.env, dict(self.env, DISPLAY=":0")):
            with self.subTest(display=env.get("DISPLAY")):
                r = subprocess.run(["bash", str(ROOT / "docker" / "entrypoint.sh"), "window", "a133"],
                                   env=env, text=True, capture_output=True)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("unknown command 'window'", r.stderr)
                self.assertEqual(self.called(), "")
        # Positive control, same invocation: the private-display selftest verb is still wired.
        self.assertIn("window-selftest", (ROOT / "docker" / "entrypoint.sh").read_text())

    def harness(self, input_dir):
        env = dict(self.env, PF_INPUT_DIR=str(input_dir))
        return subprocess.run([str(ROOT / "harness" / "run-in-harness.sh"), str(self.app)],
                              env=env, text=True, capture_output=True)

    def test_harness_names_an_absent_input_dir(self):
        absent = Path(self.tmp.name) / "no-input"
        r = self.harness(absent)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"{absent} is absent", r.stderr)
        self.assertEqual(self.called(), "", "bwrap must not start without /dev/input")
        # Positive control: the same run with the directory present reaches bwrap and binds it.
        present = Path(self.tmp.name) / "input"; present.mkdir()
        r = self.harness(present)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"--dev-bind {present} /dev/input", self.called())

    def test_harness_without_input_gets_an_empty_private_directory(self):
        r = subprocess.run([str(ROOT / "harness" / "run-in-harness.sh"), str(self.app)],
                           env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--dir /dev/input", self.called())

    def test_harness_binds_the_header_beside_fb0(self):
        present = Path(self.tmp.name) / "input"; present.mkdir()
        env = dict(self.env, PF_INPUT_DIR=str(present), FB0_BIND="/f", FB0_HDR_BIND="/h",
                   PF_FB_WIDTH="4", PF_FB_HEIGHT="2")
        r = subprocess.run([str(ROOT / "harness" / "run-in-harness.sh"), str(self.app)],
                           env=env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--bind /h /dev/fb0.hdr --setenv PF_FB0_HDR /dev/fb0.hdr", self.called())
        # Negative control: a header without a framebuffer is refused before bwrap.
        self.log.unlink()
        env.pop("FB0_BIND")
        r = subprocess.run([str(ROOT / "harness" / "run-in-harness.sh"), str(self.app)],
                           env=env, text=True, capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FB0_HDR_BIND needs FB0_BIND", r.stderr)
        self.assertEqual(self.called(), "")


class HostDesktopRoutesRemoved(unittest.TestCase):
    """Static negative controls (tsp-ihne1.56.5, uinput incident PR5).

    The host display/input routes are deleted, not gated: no host X11 projection (DISPLAY /
    Xauthority / /tmp/.X11-unix / xhost), no caller-owned host evdev nodes, no live `--window`
    demo verb. The private routes stay: `window-selftest` creates its OWN Xvfb :99 inside the
    container, which is a legitimate private display.
    """

    def text(self, *parts):
        return (ROOT.joinpath(*parts)).read_text()

    def test_sim_cli_has_no_host_display_or_node_route(self):
        sim = self.text("sim")
        for needle in ("xhost", "XAUTHORITY", ".X11-unix", "xdpyinfo",
                       "--no-synth", "--input-device", "--window"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, sim)
        # Positive control: the autonomous private-Xvfb selftest is still the gui route.
        self.assertIn("window-selftest", sim)

    def test_entrypoint_has_no_host_display_verb(self):
        entry = self.text("docker", "entrypoint.sh")
        self.assertNotIn('DISPLAY:?', entry)
        self.assertNotIn("needs a real X display", entry)
        self.assertNotRegex(entry, r"(?m)^\s*window\)")
        # Positive controls: the private-display selftest keeps its own Xvfb :99.
        self.assertIn("window-selftest)", entry)
        self.assertIn("Xvfb :99", entry)

    def test_capture_and_image_have_no_window_route(self):
        self.assertNotIn('"--window"', self.text("generic", "generic_capture.py"))
        dockerfile = self.text("Dockerfile")
        self.assertNotIn("-e DISPLAY", dockerfile)
        self.assertNotIn("-v /tmp/.X11-unix", dockerfile)

    def test_gui_never_projects_the_host_display(self):
        # Behavioural control: even with DISPLAY set in the caller's environment, `sim gui`
        # invokes the private-Xvfb `window-selftest` and forwards no host display state.
        tmp = tempfile.TemporaryDirectory(prefix="pf-sim-gui-route-")
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        bin_ = d / "bin"; bin_.mkdir()
        log = d / "docker-called"
        fake = bin_ / "docker"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> {shlex.quote(str(log))}\nexit 0\n")
        fake.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", NO_COLOR="1",
                   DISPLAY=":0", XAUTHORITY=str(d / "Xauthority"))
        r = subprocess.run([str(SIM), "gui", "a133"], env=env, text=True,
                           capture_output=True, cwd=d, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = log.read_text()
        self.assertIn("window-selftest", calls)
        self.assertNotIn("DISPLAY", calls)
        self.assertNotIn(".X11-unix", calls)
        self.assertNotIn("Xauthority", calls)
        self.assertNotIn("window a133", calls)


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

    def ident(self, path):
        st = os.stat(path)
        return f"{st.st_dev}:{st.st_ino}"

    def test_external_buffer_matching_host_ident_is_accepted(self):
        path = self.write_fb(bytes(32))
        self.assertEqual(self.gc.prepare_fb0(path, 4, 2, self.tmp.name, self.ident(path)), path)

    def test_replaced_external_buffer_is_rejected(self):
        # The host checked one inode; the path now names another (swapped before the bind).
        path = self.write_fb(bytes(32))
        checked = self.ident(path)
        other = os.path.join(self.tmp.name, "other")
        with open(other, "wb") as f:
            f.write(bytes(32))
        os.chmod(other, 0o666)
        os.replace(other, path)
        self.assertNotEqual(self.ident(path), checked)
        with self.assertRaisesRegex(RuntimeError, "replaced after the host check"):
            self.gc.prepare_fb0(path, 4, 2, self.tmp.name, checked)
        # Positive control: the same file against its own ident.
        self.assertEqual(self.gc.prepare_fb0(path, 4, 2, self.tmp.name, self.ident(path)), path)

    def test_external_fifo_is_rejected_without_blocking(self):
        # The check opens the path once (O_PATH): a FIFO must not block it and is not a buffer.
        fifo = os.path.join(self.tmp.name, "fifo"); os.mkfifo(fifo, 0o666)
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            self.gc.prepare_fb0(fifo, 4, 2, self.tmp.name)
        self.assertEqual(self.gc.prepare_fb0(self.write_fb(bytes(32)), 4, 2, self.tmp.name),
                         os.path.join(self.tmp.name, "fb0"))

    def test_short_external_buffer_is_rejected(self):
        path = self.write_fb(bytes(31))
        with self.assertRaisesRegex(RuntimeError, "31 bytes.*32"):
            self.gc.prepare_fb0(path, 4, 2, self.tmp.name)

    def test_missing_external_buffer_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            self.gc.prepare_fb0(os.path.join(self.tmp.name, "absent"), 4, 2, self.tmp.name)

    def write_hdr(self, size=64, mode=0o666):
        path = os.path.join(self.tmp.name, "fb0.hdr")
        with open(path, "wb") as f:
            f.write(bytes(size))
        os.chmod(path, mode)
        return path

    def test_header_file_checks(self):
        fb = self.write_fb(bytes(64))  # large enough that only the same-file check can refuse it
        hdr = self.write_hdr()
        self.assertEqual(self.gc.prepare_fb0_hdr(hdr, fb, self.ident(hdr)), hdr)
        for size, mode, err in ((63, 0o666, "63 bytes; the header needs 64"),
                                (64, 0o600, "chmod o\\+rw")):
            with self.subTest(size=size, mode=oct(mode)):
                with self.assertRaisesRegex(RuntimeError, err):
                    self.gc.prepare_fb0_hdr(self.write_hdr(size, mode), fb)
        with self.assertRaisesRegex(RuntimeError, "--fb0-hdr .* is the same file as --fb0"):
            self.gc.prepare_fb0_hdr(fb, fb)
        with self.assertRaisesRegex(RuntimeError, "replaced after the host check"):
            self.gc.prepare_fb0_hdr(self.write_hdr(), fb, "1:2")

    def test_conversion_matches_the_per_pixel_reference(self):
        w, h = 7, 3
        raw = bytes((i * 37 + 11) % 256 for i in range(w * h * 4))
        path = self.write_fb(raw)
        out = os.path.join(self.tmp.name, "out.ppm")
        self.gc.capture_raw(path, out, w, h)
        want = bytearray()
        for i in range(w * h):
            want += bytes((raw[i * 4 + 2], raw[i * 4 + 1], raw[i * 4]))
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"P6\n%d %d\n255\n" % (w, h) + bytes(want))
        # Negative control: a short buffer is still an error, never a partial frame.
        with self.assertRaisesRegex(RuntimeError, "short framebuffer"):
            self.gc.capture_raw(path, out, w, h + 1)


class SignalForwarding(unittest.TestCase):
    """`docker stop` / Ctrl-C reach generic_capture (PID 2 under --init); it must pass them to the app.

    The qemu launcher's chain is harness -> bwrap -> app. Real bwrap dies on SIGTERM without
    passing it on, so the fake `bwrap` here (a script named bwrap, comm "bwrap") does the same:
    only a signal sent to the app itself lets the app's handler run and the chain exit 0.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pf-signal-")
        d = Path(self.tmp.name)
        self.mark = d / "mark"; self.ready = d / "ready"
        self.bwrap = d / "bwrap"
        # SLOW_BWRAP=<file>: announce, then pause before starting the app (sandbox setup).
        self.bwrap.write_text('#!/bin/sh\n[ -z "$SLOW_BWRAP" ] || { : > "$SLOW_BWRAP"; sleep 0.5; }\n"$@" &\nwait $!\n')
        self.harness = d / "harness"
        self.procs = []
        self.harness.write_text(f'#!/bin/sh\nexec {shlex.quote(str(self.bwrap))} "$@"\n')
        for p in (self.bwrap, self.harness):
            p.chmod(0o755)

    def tearDown(self):
        for p in self.procs:  # a failing run must not leave its app looping
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
        self.tmp.cleanup()

    def app(self, trap, loop=True):
        app = Path(self.tmp.name) / "app"
        app.write_text("#!/bin/sh\n" + (f"trap 'echo $1 > {shlex.quote(str(self.mark))}; exit 0' TERM INT\n" if trap else "")
                       + f"echo up > {shlex.quote(str(self.ready))}\n"
                       + ("while :; do sleep 0.02; done\n" if loop else "sleep 0.2\n"))
        app.chmod(0o755)
        return app

    # The capture, run in a child python with the platform lookup stubbed out.
    RUN = ("import os, signal, subprocess, sys; sys.path[:0] = [%r]; import generic_capture as gc\n"
           "gc.dimensions = lambda p, d: (None, (4, 2))\n"
           "gc.composite = lambda *a: subprocess.Popen(['sleep', '30'], stdin=subprocess.PIPE, text=True)\n"
           "if %r:\n"
           "    real = subprocess.Popen\n"
           "    def early(*a, **k):\n"
           "        p = real(*a, **k); os.kill(os.getpid(), signal.SIGTERM); return p\n"
           "    gc.subprocess.Popen = early\n"
           "sys.argv[1:] = %r; sys.exit(gc.main())")

    def argv(self, app, launcher, frame):
        return ["--device", "x", "--platform", "/p", "--launcher", launcher, "--app", str(app),
                "--qemu-tsp", "/q", "--rootfs", "/r", "--harness", str(self.harness),
                "--frame", str(Path(self.tmp.name) / "frame.ppm"), *frame, "--", "tag"]

    def capture(self, app, launcher, sig, early=False):
        for p in (self.mark, self.ready):
            if p.exists(): p.unlink()
        # early: a SIGTERM arrives while the app is being started (before Popen returns).
        argv = self.argv(app, launcher, ["--no-frame"])
        p = subprocess.Popen([sys.executable, "-c", self.RUN % (str(ROOT / "generic"), early, argv)],
                             start_new_session=True)
        self.procs.append(p)
        if early:  # the stop may end the app before it is up
            return p.wait(timeout=10)
        end = time.monotonic() + 10
        while not self.ready.exists() and time.monotonic() < end:
            time.sleep(0.01)
        self.assertTrue(self.ready.exists(), "the app never started")
        if sig is not None:
            p.send_signal(sig)
        return p.wait(timeout=10)

    def test_sigterm_reaches_the_app_through_bwrap(self):
        self.assertEqual(self.capture(self.app(trap=True), "qemu", signal.SIGTERM), 0)
        self.assertEqual(self.mark.read_text(), "tag\n", "the app's TERM handler did not run")
        # Negative control: an app with no handler dies of the forwarded TERM, reported as 128+15.
        self.assertEqual(self.capture(self.app(trap=False), "qemu", signal.SIGTERM), 143)
        self.assertFalse(self.mark.exists())

    def test_sigint_and_native_launcher(self):
        self.assertEqual(self.capture(self.app(trap=True), "native", signal.SIGINT), 0)
        self.assertEqual(self.mark.read_text(), "tag\n")
        self.assertEqual(self.capture(self.app(trap=False), "native", signal.SIGINT), 130)

    def test_a_stop_while_the_app_starts_is_not_lost(self):
        # The signal lands before the supervisor has the app's pid: it must survive, then pass the
        # stop on. The app may not have its handler yet, so it dies of TERM: 143, not the
        # supervisor's own death (-15). Control: the same run without the early signal exits 0.
        self.assertEqual(self.capture(self.app(trap=False), "qemu", None, early=True), 143)
        self.assertEqual(self.capture(self.app(trap=True), "qemu", signal.SIGTERM), 0)

    def test_a_stop_before_the_app_exists_reaches_it(self):
        # The stop lands while bwrap is still setting up: no app process to signal yet. The
        # supervisor passes it on once the app appears instead of losing it (which would hang).
        slow = Path(self.tmp.name) / "bwrap-up"
        env = dict(os.environ, SLOW_BWRAP=str(slow))
        app = self.app(trap=False)
        p = subprocess.Popen([sys.executable, "-c", self.RUN % (str(ROOT / "generic"), False,
                              self.argv(app, "qemu", ["--no-frame"]))], env=env, start_new_session=True)
        self.procs.append(p)
        end = time.monotonic() + 10
        while not slow.exists() and time.monotonic() < end:
            time.sleep(0.005)
        self.assertTrue(slow.exists(), "the fake bwrap never started")
        self.assertFalse(self.ready.exists(), "the app must not be up yet")
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=10), 143)
        # Control: a stop once the app is up reaches its handler.
        self.assertEqual(self.capture(self.app(trap=True), "qemu", signal.SIGTERM), 0)


class CaptureCadence(unittest.TestCase):
    """How often generic_capture converts fb0 to frame.ppm while the app runs (native launcher)."""
    def setUp(self):
        self.gc = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(self.gc)
        self.tmp = tempfile.TemporaryDirectory(prefix="pf-cadence-")
        self.calls = 0
        real = self.gc.capture_raw
        def counted(*a):
            self.calls += 1
            return real(*a)
        self.gc.capture_raw = counted
        self.gc.dimensions = lambda platform, device: (None, (4, 2))
        self.frame = os.path.join(self.tmp.name, "out", "frame.ppm")

    def tearDown(self):
        self.tmp.cleanup()

    def run_capture(self, *flags):
        self.calls = 0
        argv = ["generic_capture.py", "--device", "x", "--platform", "/p", "--launcher", "native",
                "--app", "/bin/sleep", "--frame", self.frame, "--cadence-ms", "10", *flags, "--", "0.4"]
        old = sys.argv
        sys.argv = argv
        try:
            return self.gc.main()
        finally:
            sys.argv = old

    def external(self):
        path = os.path.join(self.tmp.name, "fb0")
        with open(path, "wb") as f:
            f.write(bytes(32))
        os.chmod(path, 0o666)
        return path

    def test_private_buffer_snapshots_on_cadence(self):
        self.assertEqual(self.run_capture(), 0)
        self.assertGreater(self.calls, 5)
        self.assertTrue(os.path.exists(self.frame))

    def test_caller_owned_fb0_without_a_consumer_converts_once(self):
        # The caller reads its own file; no consumer of the cadence snapshots is attached.
        self.assertEqual(self.run_capture("--fb0", self.external()), 0)
        self.assertEqual(self.calls, 1, "only the final snapshot at exit")
        self.assertTrue(os.path.exists(self.frame))
        # Control in the same invocation: the private buffer still snapshots on cadence.
        self.assertEqual(self.run_capture(), 0)
        self.assertGreater(self.calls, 5)

    def test_no_frame_never_converts(self):
        self.assertEqual(self.run_capture("--fb0", self.external(), "--no-frame"), 0)
        self.assertEqual(self.calls, 0)
        self.assertFalse(os.path.exists(self.frame))

    def test_no_frame_refuses_a_frame_consumer(self):
        args = ["--no-frame", "--shot", "/x.ppm"]
        with self.assertRaises(SystemExit) as cm, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.run_capture(*args)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--no-frame writes no frame", err.getvalue())


if __name__ == "__main__":
    unittest.main()
