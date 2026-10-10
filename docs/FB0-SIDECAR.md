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
  passes `--fb0 /run/pfvd/fb0` to `generic_capture.py`, which sets `FB0_BIND=/run/pfvd/fb0`; the
  harness (`harness/run-in-harness.sh`) then binds it at `/dev/fb0` inside bwrap. This is the
  same `FB0_BIND` path the default run uses for its private buffer. Without `--fb0-path`
  nothing changes.
- **Layout:** a plain fbdev-shaped buffer. XRGB8888, little-endian (bytes B, G, R, X), stride
  `width * 4`, top row first. The app sees `PF_FB_WIDTH`, `PF_FB_HEIGHT` and `PF_FB_STRIDE`.
  No header is ever placed inside this file.

## Present signal: poll

**The harness does not write `fb0.hdr`.** The app writes `/dev/fb0` directly through its own
mmap; nothing in the path sees a "frame done" moment, and an unmodified fbdev app gives none.
The consumer detects change by **polling** the buffer at its own cadence (for example a 60 Hz
timer, comparing a few 4 KiB stripes before a full upload). `generic_capture.py` still snapshots
the buffer into `sim-capture/frame.ppm` on its fixed cadence for `--shot`/`--window`; that
snapshot is a converted copy for the skin compositor, not a present signal.

Tearing is accepted: this is pixel transport, not vsync (see `HONESTY-CONTRACT.md`). A
double-buffered present would need an ioctl a plain file cannot provide.

## `fb0.hdr` — the optional sidecar

A producer that does know when a frame is complete (hwprobe-lite, pf-hwprobe, fb-pattern, a
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
