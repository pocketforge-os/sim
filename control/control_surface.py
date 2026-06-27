#!/usr/bin/env python3
"""control_surface.py — tsp-an4.5: the ONE injection-as-API control surface.

INJECTION-AS-API-FIRST (briefing §C.3 item "Injection is API-first"): there is ONE control
surface, and the GUI skin (C6) and the headless CI harness (E7) are CO-EQUAL CLIENTS of it —
the GUI is built ON this API, never the API bolted onto a GUI. This module IS that API; it is
the surface E7's CI scripts drive and the contract that becomes the CI gate (advisory->blocking).

It converges the two prior walls into the real headless device:
  * .3 (sim/synth/uinput_synth.Synth) — the descriptor->uinput resolver + the injection
    primitives (press/release/set_axis/set_stick/move_hat). Digital press -> EV_KEY 1/0; analog
    drag -> EV_ABS scaled across the descriptor min..max; hat -> ABS_HAT0X/Y.
  * .4 (sim/fb software-render) — the app lights the pressed control onto a virtual fb, dumped
    to PPM; we sample regions deterministically (sim/fb/ppm2png.read_ppm) — NOT a VLM
    (tsp-visual-inspection's hallucination caveat).
  * E2 broker (broker_stub for .5) — set_pose / set_capability / capability queries route here,
    NOT raw evdev; descriptor-honest hardware-absent no-ops for missing hardware.

The headline contract this makes real, verbatim, on BOTH descriptors, zero per-device code:

    with Device("a133", platform_dir, launcher="qemu") as dev:
        dev.press("south");            assert dev.framebuffer_region("south").is_red()
        dev.set_axis("ltrig", 0.5);    assert dev.slider("ltrig").at(0.5)
        dev.assert_capability_absent("imu")     # a133 has no IMU; a523 -> present

Run under sudo (uinput + the bound /dev/input event nodes are root-only). Launcher "qemu" runs
the IDENTICAL arm64 binary under qemu-tsp+bubblewrap (NO crun); "native" runs the x86 build for
the native==qemu parity check.
"""
import os
import select
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "synth"))
sys.path.insert(0, os.path.join(HERE, "..", "fb"))
sys.path.insert(0, os.path.join(HERE, "..", "sensor"))
sys.path.insert(0, HERE)

from uinput_synth import Synth, load_descriptor          # noqa: E402  (the .3 foundation)
from ppm2png import read_ppm                              # noqa: E402  (the .4 PPM reader)
import layout as L                                        # noqa: E402
from broker_stub import BrokerStub, HardwareAbsent, PermissionDenied  # noqa: E402
from iio_synth import IIOSynth                            # noqa: E402  (the .7 virtual IIO device)
from physical_model import pose_from_drag                 # noqa: E402  (GUI tilt-bubble gesture)

HARNESS = os.path.join(HERE, "..", "harness", "run-in-harness.sh")


# ---------- deterministic framebuffer readback (sim/fb sampling, no VLM) ----------
def _avg(rgb, w, h, cx, cy, rad=1):
    rs = gs = bs = n = 0
    for yy in range(max(0, cy - rad), min(h, cy + rad + 1)):
        for xx in range(max(0, cx - rad), min(w, cx + rad + 1)):
            o = (yy * w + xx) * 3
            rs += rgb[o]; gs += rgb[o + 1]; bs += rgb[o + 2]; n += 1
    n = max(1, n)
    return (rs // n, gs // n, bs // n)


class Region:
    """A control's drawn rect on the captured framebuffer; colour-classified, deterministically."""

    def __init__(self, frame, rect, skin_part):
        self.w, self.h, self.rgb = frame
        self.rect = rect            # (x, y, w, h) canvas space
        self.skin_part = skin_part

    def color(self):
        x, y, w, h = self.rect
        return _avg(self.rgb, self.w, self.h, x + w // 2, y + h // 2)

    def is_red(self):
        r, g, b = self.color()
        return r >= 150 and g <= 90 and b <= 90

    def is_lit(self):
        return self.is_red()

    def is_inactive(self):
        r, g, b = self.color()
        return r < 150 and abs(r - g) < 40 and abs(g - b) < 40   # grey, not red

    def __repr__(self):
        return f"<Region {self.skin_part} {self.rect} color={self.color()}>"


class Slider:
    """A trigger's proportional fill, read back as a fraction of the track width."""

    def __init__(self, frame, rect, skin_part):
        self.w, self.h, self.rgb = frame
        self.rect = rect
        self.skin_part = skin_part

    def fraction(self):
        x, y, w, h = self.rect
        row = y + h // 2
        red = 0
        for xx in range(x, min(self.w, x + w)):
            o = (row * self.w + xx) * 3
            r, g, b = self.rgb[o], self.rgb[o + 1], self.rgb[o + 2]
            if r >= 150 and g <= 90 and b <= 90:
                red += 1
        return red / float(w) if w else 0.0

    def at(self, value, tol=0.06):
        return abs(self.fraction() - value) <= tol

    def __repr__(self):
        return f"<Slider {self.skin_part} {self.rect} frac={self.fraction():.3f}>"


class ControlError(RuntimeError):
    pass


class Device:
    """The honest virtual device. ONE surface; GUI + headless test are both clients."""

    def __init__(self, device_id, platform_dir, *, launcher="qemu", outdir=None,
                 app_x86=None, app_arm64=None, qemu_tsp=None, rootfs=None, ready_timeout=40.0,
                 snap_timeout=40.0):
        self.device_id = device_id
        self.platform_dir = platform_dir
        self.launcher = launcher
        self.outdir = outdir or os.path.join("/tmp", f"ctl-{device_id}-{launcher}")
        self.app_x86 = app_x86
        self.app_arm64 = app_arm64
        self.qemu_tsp = qemu_tsp or os.environ.get("QEMU_TSP")
        self.rootfs = rootfs or os.environ.get("ROOTFS")
        self.ready_timeout = ready_timeout
        self.snap_timeout = snap_timeout

        self.desc = load_descriptor(platform_dir, device_id)
        self.canvas, self.groups = L.compute_layout(self.desc)
        self.synth = None
        self.broker = BrokerStub(self.desc)
        # the .7 virtual IIO device: synthesized from [[sensors]], driven by the broker's physical
        # model, read by the app at /sys/bus/iio/devices (qemu) / PF_IIO_ROOT (native).
        self.iio_root = os.path.join(self.outdir, "iio")
        self.iio = IIOSynth(self.desc, self.iio_root)
        self._proc = None
        self._req_fd = self._resp_fd = None
        self._frame = None          # (w, h, rgb)
        self._dirty = True
        self._snap_n = 0
        self._applog = None

    # ---------- lifecycle ----------
    def __enter__(self):
        return self.boot()

    def __exit__(self, *exc):
        self.shutdown()
        return False

    def boot(self):
        os.makedirs(os.path.join(self.outdir, "frames"), exist_ok=True)
        # 1) synthesize the descriptor's uinput device(s) FIRST so the nodes exist before the
        #    sandbox binds /dev/input and before the app opens them.
        self.synth = Synth(self.desc).create()
        nodes = [n["node"] for n in self.synth.nodes() if n["node"]]
        if not nodes:
            raise ControlError("synth produced no evdev nodes")
        # 1b) synthesize the virtual IIO device from [[sensors]] (no-op when the descriptor has no
        #     imu -> a133 reads hardware-absent). Initialized at the rest pose.
        self.iio.create()
        # 2) write the descriptor-computed layout the app draws from (ONE source of truth).
        with open(os.path.join(self.outdir, "layout.txt"), "w") as f:
            f.write(L.emit_layout_txt(self.canvas, self.groups, nodes))
        # 3) FIFOs for the snapshot handshake.
        for fifo in ("req", "resp"):
            p = os.path.join(self.outdir, fifo)
            if os.path.exists(p):
                os.unlink(p)
            os.mkfifo(p)
        # 4) launch the app.
        self._applog = open(os.path.join(self.outdir, f"app.{self.launcher}.log"), "wb")
        if self.launcher == "native":
            if not self.app_x86:
                raise ControlError("launcher=native needs app_x86")
            cmd = [self.app_x86, self.outdir]
            # native: no sandbox to remap /sys, so point the app at the synthesized IIO tree
            # directly -> native + qemu read byte-identical files.
            env = dict(os.environ, PF_IIO_ROOT=self.iio_root)
            self._proc = subprocess.Popen(cmd, stderr=self._applog, env=env)
        elif self.launcher == "qemu":
            for k, v in (("app_arm64", self.app_arm64), ("qemu_tsp", self.qemu_tsp),
                         ("rootfs", self.rootfs)):
                if not v:
                    raise ControlError(f"launcher=qemu needs {k}")
            # qemu: bind the synthesized IIO tree at the honest ABI path; the app's default
            # PF_IIO_ROOT=/sys/bus/iio/devices then reads it indistinguishably from hardware.
            env = dict(os.environ, QEMU_TSP=self.qemu_tsp, ROOTFS=self.rootfs, OUT_BIND=self.outdir,
                       IIO_BIND=self.iio_root)
            cmd = ["bash", HARNESS, self.app_arm64, "/out"]
            self._proc = subprocess.Popen(cmd, stderr=self._applog, env=env)
        else:
            raise ControlError(f"unknown launcher {self.launcher!r}")
        # 5) open FIFOs (O_RDWR: never blocks on open) and wait for "ready".
        self._req_fd = os.open(os.path.join(self.outdir, "req"), os.O_RDWR)
        self._resp_fd = os.open(os.path.join(self.outdir, "resp"), os.O_RDWR)
        line = self._read_resp(self.ready_timeout)
        if line != "ready":
            raise ControlError(f"app did not report ready (got {line!r}); see "
                               f"{self.outdir}/app.{self.launcher}.log")
        self._dirty = True
        return self

    def shutdown(self):
        try:
            if self._req_fd is not None and self._proc and self._proc.poll() is None:
                self._write_req("quit")
                self._read_resp(5.0)
        except Exception:
            pass
        for fd in (self._req_fd, self._resp_fd):
            try:
                if fd is not None:
                    os.close(fd)
            except OSError:
                pass
        self._req_fd = self._resp_fd = None
        if self._proc:
            try:
                self._proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._applog:
            self._applog.close()
            self._applog = None
        if self.synth:
            self.synth.destroy()
            self.synth = None
        if self.iio:
            self.iio.destroy()

    # ---------- the FIFO handshake ----------
    def _write_req(self, msg):
        os.write(self._req_fd, (msg + "\n").encode())

    def _read_resp(self, timeout):
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc and self._proc.poll() is not None:
                # app exited; drain any final bytes then fail
                raise ControlError(f"app process exited (code {self._proc.returncode}); see "
                                   f"{self.outdir}/app.{self.launcher}.log")
            rl, _, _ = select.select([self._resp_fd], [], [], 0.2)
            if not rl:
                continue
            chunk = os.read(self._resp_fd, 256)
            if not chunk:
                continue
            buf += chunk
            if b"\n" in buf:
                return buf.split(b"\n", 1)[0].decode().strip()
        raise ControlError(f"timeout waiting for app response after {timeout}s")

    # ---------- input injection (the C5 primitives; GUI + test share these) ----------
    def has_input(self, input_id):
        return input_id in self.synth.resolver if self.synth else \
            any(i["id"] == input_id for i in self.desc.get("inputs", []))

    def _require(self, input_id):
        if not self.has_input(input_id):
            raise HardwareAbsent(input_id, f"'{input_id}' is not a control on {self.device_id}")

    def press(self, input_id):
        self._require(input_id); self.synth.press(input_id); self._dirty = True

    def release(self, input_id):
        self._require(input_id); self.synth.release(input_id); self._dirty = True

    def set_axis(self, input_id, value, normalized=True):
        self._require(input_id); self.synth.set_axis(input_id, value, normalized); self._dirty = True

    def set_stick(self, input_id, x, y, normalized=True):
        self._require(input_id); self.synth.set_stick(input_id, x, y, normalized); self._dirty = True

    def move_hat(self, input_id, x, y):
        self._require(input_id); self.synth.move_hat(input_id, x, y); self._dirty = True

    # ---------- broker-routed capabilities (set_pose / set_capability) ----------
    def set_pose(self, **pose):
        """Drive the single physical model through the broker, then push the derived accel/gyro
        into the virtual IIO device the app reads. ONE call serves GUI + test (both land here)."""
        state = self.broker.set_pose(**pose)
        self.iio.update(self.broker.model)
        return state

    def set_pose_from_drag(self, dx, dy):
        """The GUI tilt-bubble client path: a normalized drag -> the SAME set_pose the test calls
        (pose_from_drag returns degrees). Proves 'one model, two clients' WITHOUT rendering pixels
        (so no owner visual gate)."""
        return self.set_pose(**pose_from_drag(dx, dy))

    def read_imu(self):
        """Ask the IDENTICAL app (under qemu / native) to read the injected IMU off the virtual
        IIO device and report it back. Returns the DEVICE-frame accel+gyro (SI) the app recovered
        by applying scale + the descriptor mount_matrix to the chip-frame raws, or None if the app
        reported the sensor hardware-absent. This is the app-CONSUMES-the-injection proof."""
        name = self.iio.name or "qmi8658"
        self._write_req(f"imu {name}")
        resp = self._read_resp(self.snap_timeout)
        if resp.startswith("imu-absent"):
            return None
        if not resp.startswith("imu "):
            raise ControlError(f"read_imu: unexpected app reply {resp!r}")
        # reply: "imu <name> ax ay az gx gy gz"  (milli-SI integers: mm/s^2, mrad/s)
        parts = resp.split()
        vals = [int(v) / 1000.0 for v in parts[2:8]]
        return {"accel": vals[0:3], "gyro": vals[3:6], "raw_reply": resp}

    def set_capability(self, name, value):
        return self.broker.set_capability(name, value)

    def get_capability(self, name):
        return self.broker.get_capability(name)

    def assert_capability_absent(self, name):
        return self.broker.assert_capability_absent(name)

    def assert_capability_present(self, name):
        return self.broker.assert_capability_present(name)

    def assert_capability_denied(self, name):
        return self.broker.assert_capability_denied(name)

    # ---------- actuators + accessibility preferences (unified no-op shape) ----------
    def acquire_rumble(self):
        return self.broker.acquire_rumble()

    def set_preference(self, name, value):
        return self.broker.set_preference(name, value)

    def get_preference(self, name, default=None):
        return self.broker.get_preference(name, default)

    # ---------- framebuffer capture + region assertion ----------
    def snapshot(self, name=None):
        if name is None:
            name = f"auto{self._snap_n:03d}"
            self._snap_n += 1
        host_ppm = os.path.join(self.outdir, "frames", f"{name}.ppm")
        app_dir = "/out" if self.launcher == "qemu" else self.outdir
        app_ppm = f"{app_dir}/frames/{name}.ppm"
        self._write_req(f"snap {app_ppm}")
        resp = self._read_resp(self.snap_timeout)
        if resp != "ok":
            raise ControlError(f"snapshot {name!r} failed (resp {resp!r})")
        self._frame = read_ppm(host_ppm)
        self._dirty = False
        self._last_frame_name = name
        return name

    def _ensure_frame(self):
        if self._dirty or self._frame is None:
            self.snapshot()

    def _rect_for(self, key):
        """Resolve a region key (input id OR skin_part) to its canvas rect + skin_part."""
        sp = L.part_for_input(self.desc, key) or key
        g = L.group_for_key(self.groups, sp)
        if g is None:
            raise KeyError(f"no drawable region for '{key}' on {self.device_id}")
        return g.canvas, g.skin_part

    def framebuffer_region(self, key):
        self._ensure_frame()
        rect, sp = self._rect_for(key)
        return Region(self._frame, rect, sp)

    def slider(self, key):
        self._ensure_frame()
        rect, sp = self._rect_for(key)
        return Slider(self._frame, rect, sp)

    # ---------- introspection (drives the descriptor-generic test matrix) ----------
    def inputs(self):
        return list(self.desc.get("inputs", []))


# convenience matching the briefing's `sim.boot(descriptor=...)` shape
def boot(device_id, platform_dir, **kw):
    return Device(device_id, platform_dir, **kw).boot()
