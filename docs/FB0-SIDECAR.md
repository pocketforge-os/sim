# Host-owned `/dev/fb0` and the `fb0.hdr` sidecar (tsp-h5ed.46.4)

How a host viewer (pfvd) gets an app's pixels out of `./sim run-app` without a copy step, and the
header format a cooperative producer may publish beside the buffer. Design source: pfvd research
R5c §2.2 (screen, F2) and §2.3 (input, F3).

## The buffer

```bash
mkdir -p /run/user/$(id -u)/pfvd
truncate -s $((1280*720*4)) /run/user/$(id -u)/pfvd/fb0        # the caller creates + sizes it
chmod o+rw /run/user/$(id -u)/pfvd/fb0                          # the sandboxed app must open it
./sim run-app a133 ./app.arm64 --fb0-path /run/user/$(id -u)/pfvd/fb0
```

- **Who creates it:** the caller (pfvd-core) creates the file on tmpfs, sizes it to at least
  `width * height * 4` for the device's screen, and maps it read-only once. `./sim` and
  `generic_capture.py` **never create or truncate** a `--fb0-path` file: a reader may already have
  it mapped, and shrinking a mapped file gives that reader `SIGBUS`. A missing file, a directory,
  or a file shorter than the device needs is a named error before the app starts.
- **Permissions:** inside bwrap the app is uid 0 / gid 0 with every capability dropped, so there
  is no DAC override and exactly one permission class applies: the owner bits of a root-owned
  file, the group bits of a gid-0 file, otherwise the *other* bits. A caller-owned file must be
  `o+rw`; a root-owned one `u+rw`. `./sim` and `generic_capture.py` refuse it otherwise and name
  the `chmod`. Keep it in a `0700` directory (`/run/user/$UID/...`) so only the caller can reach it.
- **How it crosses:** by path. `./sim` adds `-v <fb0-path>:/run/pfvd/fb0` to `docker run` and
  passes `--fb0 /run/pfvd/fb0 --fb0-ident <dev:ino>` to `generic_capture.py`, which checks the
  bound inode (see [Check, then bind](#check-then-bind)) and sets `FB0_BIND=/run/pfvd/fb0`; the
  harness (`harness/run-in-harness.sh`) then binds it at `/dev/fb0` inside bwrap. This is the
  same `FB0_BIND` path the default run uses for its private buffer. Without `--fb0-path`
  nothing changes.
- **Layout:** a plain fbdev-shaped buffer. XRGB8888, little-endian (bytes B, G, R, X), stride
  `width * 4`, top row first. The app sees `PF_FB_WIDTH`, `PF_FB_HEIGHT` and `PF_FB_STRIDE`.
  No header is ever placed inside this file.

## Check, then bind

`docker run -v` and `--device` take host paths, and the daemon resolves them when it creates the
container. A file descriptor cannot be handed across, so every host-side check is a
check-then-use against a path. On the trusted single-user host this guards against accidents
(a file replaced or a node renumbered between the check and the bind). It does not defend
against a hostile local user.

- **`--fb0-path`:** `./sim` resolves the path and runs one `stat`, so the type, owner, mode and
  `dev:ino` all come from the same inode. It passes that `dev:ino` as `--fb0-ident`. Inside the
  container, `generic_capture.py` opens `/run/pfvd/fb0` once (`O_PATH`, which needs no read
  permission and does not block on a FIFO) and takes every check from one `fstat` of that fd:
  regular file, the sandbox permission class, size, and `dev:ino` equal to the host's. A docker
  bind mount keeps `st_dev`/`st_ino`, so if the path named a different file when docker bound
  it, the run is refused before the app starts ("replaced after the host check").
  `/run/pfvd/fb0` is a mount point that the host cannot rename or replace, so the harness's
  later bind of that path into bwrap is the inode just checked.
  **Residual window:** the swap is detected, not prevented. Docker has already bound the other
  file read-write into the container, but nothing writes to it before the refusal. A `chmod`
  on the same inode after the in-container check is the owner's own change. It is not a swap.
  The native launcher (`--launcher native`, no container and no `--fb0-ident`) hands the path
  straight to the app and keeps the plain window.

## Present signal: poll

**The harness does not write `fb0.hdr`.** The app writes `/dev/fb0` directly through its own
mmap; nothing in the path sees a "frame done" moment, and an unmodified fbdev app gives none.
The consumer detects change by **polling** the buffer at its own cadence (for example a 60 Hz
timer, comparing a few 4 KiB stripes before a full upload). `generic_capture.py` snapshots
the buffer into `sim-capture/frame.ppm` on its fixed cadence for its private
buffer; that snapshot is a converted copy for the skin compositor, not a present signal. With
`--fb0-path` the caller reads its own file, so the capture skips the cadence snapshots and keeps only the frame at a clean exit; `--no-frame` skips that one too.

Tearing is accepted: this is pixel transport, not vsync (see `HONESTY-CONTRACT.md`). A
double-buffered present would need an ioctl a plain file cannot provide.

## `fb0.hdr` — the optional sidecar

A producer that does know when a frame is complete (fb-anim, hwprobe-lite, pf-hwprobe, fb-pattern, a
future in-container presenter) may publish a 64-byte header in a **sibling** file `fb0.hdr`, so
`fb0` itself stays a plain buffer. A consumer must work without it: absent, short, or bad-magic
header means "poll the buffer".

All fields little-endian, offsets in bytes:

| Off | Size | Field | Value |
|----:|-----:|-------|-------|
| 0 | 4 | `magic` | ASCII `PFB0` (`50 46 42 30`) |
| 4 | 2 | `version` | `1` |
| 6 | 2 | `header_size` | `64` |
| 8 | 4 | `width` | pixels |
| 12 | 4 | `height` | pixels |
| 16 | 4 | `stride` | bytes per row (`>= width * 4`) |
| 20 | 4 | `fourcc` | DRM fourcc of the pixel format; `XR24` (`0x34325258`, `DRM_FORMAT_XRGB8888`) |
| 24 | 8 | `seq` | frame counter, see below |
| 32 | 8 | `timestamp_ns` | `CLOCK_MONOTONIC` at the last completed frame (one kernel, so host and container clocks agree) |
| 40 | 24 | reserved | zero |

`seq` is a seqlock. The writer sets it to an odd value before touching `fb0` or any header field,
writes the frame and `timestamp_ns` (and any changed geometry), and as its **last** store (a
release store) sets `seq` to the next even value. A reader reads `seq` (acquire), copies the
header fields and frame it uses, and reads `seq` again; the copy and its metadata are whole only
if both reads are equal and even. A reader that sees `seq` unchanged since its last upload can
skip the frame.

`width`, `height` and `stride` must agree with the `PF_FB_*` values the app received and with the
`fb0` file size (`stride * height <= size`). A consumer that finds a mismatch ignores the header
and reports it.

### Getting the header out of the container

`fb0` crosses as one bound file, so a container producer needs a second one for the header:

```bash
truncate -s 64 /run/user/$(id -u)/pfvd/fb0.hdr && chmod o+rw /run/user/$(id -u)/pfvd/fb0.hdr
./sim run-app a133 bundled:fb-anim --fb0-path /run/user/$(id -u)/pfvd/fb0 \
  --fb0-hdr-path /run/user/$(id -u)/pfvd/fb0.hdr --no-frame
```

The caller creates the header file (at least 64 bytes, zero-filled is fine: bad magic means
"poll") beside `fb0`, where a consumer looks for it. The same rules as `--fb0-path` apply: an
existing regular file, the sandbox permission class, never created or truncated, and the
`dev:ino` checked on the host and again in the container (`--fb0-hdr-ident`). It must not be the
`fb0` file itself. `./sim` binds it at `/run/pfvd/fb0.hdr`; `generic_capture.py --fb0-hdr` checks
it and sets `FB0_HDR_BIND`; the harness binds it at `/dev/fb0.hdr` and gives the app
`PF_FB0_HDR=/dev/fb0.hdr`. A producer that sees no `PF_FB0_HDR` publishes nothing.

`fb-anim` (in the image, `bundled:fb-anim`) is the reference producer: it redraws at 60 Hz and
writes the header around every frame. `fb-pattern` and `hwprobe-lite` publish no header.
