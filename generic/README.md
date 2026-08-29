# Generic arm64 `/dev/fb0` capture

`generic_capture.py` preallocates a private descriptor-sized XRGB8888 shared mapping, asks the
common harness to bind it at `/dev/fb0`, and snapshots it on a fixed cadence. A snapshot
is fed unchanged into `skin-render` for an offscreen `--shot` or live `--window`.

`fb-pattern.c` intentionally has no request/response protocol. It opens and mmaps
`/dev/fb0`, draws a deterministic four-corner pattern plus frame counter 1, and exits.
`check-generic.py` requires native and arm64-under-qemu captures to be byte-identical,
compares the result with `baseline/<device>/frame.png`, and produces a skin composite.
The existing hwprobe-lite control and skin checks remain unchanged.

```bash
./sim run-app a133 ./app.arm64 --shot app.ppm
./sim run-app a133 ./app.arm64 --window -- --flag-for-the-app
```

See `docs/HONESTY-CONTRACT.md`: this proves pixel transport and bezel composition, not
timing, vsync, GPU, display-engine, or full fbdev ioctl behavior.
