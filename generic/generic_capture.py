#!/usr/bin/env python3
"""Run a native or arm64 app with a shared XRGB8888 fb0 and capture/composite it."""
import argparse
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skin"), os.path.join(HERE, "..", "fb")]
import skin_model as SM
from ppm2png import read_png, write_ppm


def dimensions(platform, device):
    skin = SM.Skin(device, platform)
    return skin, (skin.canvas_w, skin.canvas_h)


def capture_raw(path, out, w, h):
    with open(path, "rb") as f:
        raw = f.read(w * h * 4)
    if len(raw) != w*h*4:
        raise RuntimeError(f"short framebuffer: {len(raw)} != {w*h*4}")
    rgb = bytearray(w*h*3)
    # XRGB8888 in memory on the supported little-endian host/arm64 targets is B,G,R,X. Strided
    # slices convert a 1280x720 frame in milliseconds; a per-pixel loop took most of a core.
    rgb[0::3] = raw[2::4]; rgb[1::3] = raw[1::4]; rgb[2::3] = raw[0::4]
    tmp = out + ".tmp"
    write_ppm(tmp, w, h, rgb)
    os.replace(tmp, out)


def sandbox_can_rw(uid, gid, mode):
    """True when the bwrap app (uid 0, gid 0, no capabilities) may open a file read+write.

    With no DAC override, the kernel applies exactly one permission class: owner if uid matches,
    else group if gid matches, else other.
    """
    bits = (mode >> 6) if uid == 0 else (mode >> 3) if gid == 0 else mode
    return bits & 0o6 == 0o6


def check_external(path, need, ident, flag, what):
    """Check a caller-owned file the sandboxed app maps; return its ``os.stat_result``.

    Never created or truncated here, because a host reader may already have it mapped (truncating
    would SIGBUS it). ``ident`` is the ``dev:ino`` the host checked; a different inode here means
    the path was swapped before docker bound it, and is refused.
    """
    # Every check reads one fstat of one open: O_PATH needs no read permission and never blocks on
    # a FIFO. Inside the container the path is a bind mount, so the inode checked is the one bound.
    missing = f"{flag} {path} does not exist (the caller creates and sizes it)"
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        raise RuntimeError(missing) from None
    except OSError as e:
        raise RuntimeError(f"{flag} {path} cannot be opened: {e.strerror}") from None
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError(missing)
    if ident is not None and f"{st.st_dev}:{st.st_ino}" != ident:
        raise RuntimeError(f"{flag} {path} is device:inode {st.st_dev}:{st.st_ino}, not the {ident} ./sim "
                           f"checked: the host file was replaced after the host check; re-run ./sim run-app")
    # The app runs as uid 0 with bwrap's dropped capabilities: no DAC override, so the file's mode
    # decides even though this supervisor (with caps) could open it.
    if not sandbox_can_rw(st.st_uid, st.st_gid, st.st_mode):
        cls = "u" if st.st_uid == 0 else "g" if st.st_gid == 0 else "o"
        raise RuntimeError(f"{flag} {path} (owner {st.st_uid}:{st.st_gid}, mode {st.st_mode & 0o777:o}) is not "
                           f"readable+writable by the sandboxed app; fix: chmod {cls}+rw on the host file")
    if st.st_size < need:
        raise RuntimeError(f"{flag} {path} is {st.st_size} bytes; {what}")
    return st


def prepare_fb0(external, w, h, work, ident=None):
    """Return the fb0 file the harness binds at /dev/fb0.

    Default: a private, pre-sized buffer inside ``work``. With ``external`` (``./sim run-app
    --fb0-path``) the caller owns the file and its size; it is used as-is (``check_external``).
    """
    need = w * h * 4
    if external is None:
        fb = os.path.join(work, "fb0")
        with open(fb, "wb") as f: f.truncate(need)
        return fb
    check_external(external, need, ident, "--fb0", f"this device needs {need} ({w}x{h} XRGB8888)")
    return external


HDR_SIZE = 64


def prepare_fb0_hdr(hdr, fb0, ident=None):
    """Return the caller-owned fb0.hdr file (docs/FB0-SIDECAR.md) the harness binds beside fb0."""
    st = check_external(hdr, HDR_SIZE, ident, "--fb0-hdr", f"the header needs {HDR_SIZE}")
    fb = os.stat(fb0)
    if (st.st_dev, st.st_ino) == (fb.st_dev, fb.st_ino):
        raise RuntimeError(f"--fb0-hdr {hdr} is the same file as --fb0 {fb0}: the header would overwrite pixels")
    return hdr


def app_pids(root):
    """``root`` and its descendants, except bwrap processes: the app and whatever it started.

    bwrap dies on SIGTERM without passing it into its sandbox (which would keep running), so a
    stop signal goes past it to qemu-tsp running the app. The sandbox's pid namespace is a child
    of ours, so its processes are visible here with their parent pids.
    """
    procs = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                st = f.read()
        except OSError:
            continue
        comm, rest = st[st.index("(") + 1:st.rindex(")")], st[st.rindex(")") + 2:].split()
        procs[int(d)] = (int(rest[1]), comm)
    out, todo = [], [root]
    while todo:
        pid = todo.pop()
        if pid in procs and procs[pid][1] != "bwrap":
            out.append(pid)
        todo += [c for c, (ppid, _) in procs.items() if ppid == pid]
    return out


class StopForwarder:
    """Pass SIGTERM/SIGINT on to the app, so it can exit cleanly and its status is reported.

    Under `docker run --init` (./sim run-app) only this supervisor receives `docker stop` or an
    attached Ctrl-C; without forwarding it would die and take the app down uncleanly. Install it
    before starting the app. A stop can arrive before the app exists (while bwrap sets up the
    sandbox), so ``poll``, called on every supervisor tick, passes it to app processes that have
    appeared since; each process gets each signal once. ``stopped`` records the signals, so the
    caller can end the run (the live window too).
    """
    def __init__(self):
        self.stopped, self.proc, self.sent = [], None, set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)

    def _on_signal(self, sig, _frame):
        self.stopped.append(sig)
        self.sent = {s for s in self.sent if s[1] != sig}
        self.poll()

    def poll(self):
        if self.proc is None or not self.stopped:
            return
        sig = self.stopped[-1]
        for pid in app_pids(self.proc.pid):
            if (pid, sig) in self.sent:
                continue
            self.sent.add((pid, sig))
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def start(self, proc):
        self.proc = proc
        self.poll()


def ppm_art(png, out):
    w, h, rgb = read_png(png)
    write_ppm(out, w, h, rgb)


def composite(skin, fb, out, renderer, work):
    body = os.path.join(work, "body.ppm"); lit = os.path.join(work, "lit.ppm")
    ppm_art(skin.body_path, body); ppm_art(skin.lit_body_path, lit)
    scene = skin.emit_scene(body, lit, fb, set(), title="PocketForge - Generic App")
    scene_path = os.path.join(work, "scene.txt")
    with open(scene_path, "w") as f: f.write(scene)
    return subprocess.Popen([renderer, "--scene", scene_path, "--shot", out])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True); ap.add_argument("--platform", required=True)
    ap.add_argument("--app", required=True); ap.add_argument("--launcher", choices=("native","qemu"), default="qemu")
    ap.add_argument("--qemu-tsp"); ap.add_argument("--rootfs"); ap.add_argument("--harness")
    ap.add_argument("--frame", required=True); ap.add_argument("--shot"); ap.add_argument("--skin-render")
    ap.add_argument("--cadence-ms", type=int, default=50)
    ap.add_argument("--fb0", help="caller-owned fb0 file to bind instead of a private buffer")
    ap.add_argument("--fb0-ident", help="dev:ino the host checked for --fb0 (set by ./sim run-app)")
    ap.add_argument("--fb0-hdr", help="caller-owned 64-byte fb0.hdr file the app may publish (needs --fb0)")
    ap.add_argument("--fb0-hdr-ident", help="dev:ino the host checked for --fb0-hdr (set by ./sim run-app)")
    ap.add_argument("--no-frame", action="store_true",
                    help="never convert fb0 to the --frame PPM (the caller reads its own --fb0)")
    ap.add_argument("app_args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.app_args[:1] == ["--"]:
        a.app_args = a.app_args[1:]
    if a.no_frame and a.shot:
        ap.error("--no-frame writes no frame.ppm; --shot composites it")
    if a.fb0_hdr and not a.fb0:
        ap.error("--fb0-hdr needs --fb0 (the header describes a caller-owned buffer)")
    skin, (w,h) = dimensions(a.platform, a.device)
    if not a.no_frame:
        os.makedirs(os.path.dirname(os.path.abspath(a.frame)), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pf-generic-") as work:
        # A private, pre-sized shared mapping is bindable by bwrap at /dev/fb0 and is
        # removed with this temporary directory. It has the same host/app mmap seam as
        # fb-render's memfd, while providing the pathname bwrap requires on supported CI.
        # --fb0 substitutes a caller-owned file (docs/FB0-SIDECAR.md).
        try:
            fb = prepare_fb0(a.fb0, w, h, work, a.fb0_ident)
            hdr = a.fb0_hdr and prepare_fb0_hdr(a.fb0_hdr, fb, a.fb0_hdr_ident)
        except RuntimeError as e:
            ap.error(str(e))
        env = os.environ.copy(); env.update(PF_FB_WIDTH=str(w), PF_FB_HEIGHT=str(h), PF_FB_STRIDE=str(w*4))
        env.pop("PF_FB0_HDR", None); env.pop("FB0_HDR_BIND", None)
        if a.launcher == "native":
            env["PF_FB0"] = fb; cmd = [a.app] + a.app_args
            if hdr: env["PF_FB0_HDR"] = hdr
        else:
            for name, val in (("QEMU_TSP",a.qemu_tsp),("ROOTFS",a.rootfs),("harness",a.harness)):
                if not val: ap.error(f"--{name.lower().replace('_','-')} is required for qemu")
            env.update(QEMU_TSP=a.qemu_tsp, ROOTFS=a.rootfs, FB0_BIND=fb)
            if hdr: env["FB0_HDR_BIND"] = hdr
            cmd = [a.harness, a.app] + a.app_args
        # Snapshots on cadence are the only way out of a private buffer. A caller-owned --fb0 is
        # read by its owner, so only the exit frame is kept.
        cadence = not a.no_frame and a.fb0 is None
        stop = StopForwarder()
        proc = subprocess.Popen(cmd, env=env)
        stop.start(proc)
        while proc.poll() is None:
            stop.poll()
            # Snapshot on cadence, not on mtime: mmap writes need not update inode metadata.
            if cadence:
                capture_raw(fb, a.frame, w, h)
            time.sleep(max(1,a.cadence_ms)/1000.0)
        # A signal death (negative returncode) is reported the shell's way, 128 + signal.
        if proc.returncode: return proc.returncode if proc.returncode > 0 else 128 - proc.returncode
        if a.no_frame: return 0
        capture_raw(fb, a.frame, w, h)
        if a.shot:
            if not a.skin_render: ap.error("--skin-render is required with --shot")
            shot = composite(skin, a.frame, a.shot, a.skin_render, work)
            return shot.wait()
    return 0

if __name__ == "__main__": sys.exit(main())
