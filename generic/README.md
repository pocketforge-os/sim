# Generic arm64 `/dev/fb0` capture

`generic_capture.py` preallocates a private descriptor-sized XRGB8888 shared mapping, asks the
common harness to bind it at `/dev/fb0`, and snapshots it on a fixed cadence. A snapshot
is fed unchanged into `skin-render` for an offscreen `--shot` or live `--window`.

`fb-pattern.c` intentionally has no request/response protocol. It opens and mmaps
`/dev/fb0`, draws a deterministic four-corner pattern plus frame counter 1, and exits.
`check-generic.py` requires native and arm64-under-qemu captures to be byte-identical,
compares the result with `baseline/<device>/frame.png`, and produces a skin composite.
The existing hwprobe-lite control and skin checks remain unchanged.

`fb-anim.c` is the continuous counterpart: it redraws `/dev/fb0` at 60 Hz until SIGTERM/SIGINT
and exits 0, and publishes the `fb0.hdr` seqlock header when `PF_FB0_HDR` names one. The image
ships both apps (`./sim run-app <device> bundled:fb-anim`); `test_fb_anim.py` checks fb-anim.

```bash
./sim run-app a133 ./app.arm64 --shot app.ppm
./sim run-app a133 ./app.arm64 --window -- --flag-for-the-app
```

`--fb0 <file>` (what `./sim run-app --fb0-path` passes) binds a caller-owned, pre-sized file
instead of the private buffer, and `--fb0-hdr <file>` a 64-byte header file beside it; see
`docs/FB0-SIDECAR.md`. The caller reads its own `--fb0`, so without `--window` the capture skips
the cadence snapshots and converts only the frame at a clean exit; `--no-frame` skips that too.
Host-side tests: `python3 -m unittest generic/test_run_app_cli.py generic/test_fb_anim.py`.

See `docs/HONESTY-CONTRACT.md`: this proves pixel transport and bezel composition, not
timing, vsync, GPU, display-engine, or full fbdev ioctl behavior.
