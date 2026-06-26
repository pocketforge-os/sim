# Shared sim runtime harness (Path B) — bubblewrap + qemu-tsp + arm64 rootfs

The owner-decided launcher for running the **identical arm64 OCI app** off-hardware: an
arm64 Debian-bookworm rootfs, run under **`qemu-tsp` + binfmt inside bubblewrap**, **NO
crun/cgroups/seccomp** (under qemu-user, crun's enforcement is moot anyway — honesty
contract item 4). The bottom seam (`/dev/input`) is bound from the host so the in-sandbox
app sees the VDB's `uinput` device. This is the shared substrate for tsp-an4.2/.3/.4/.5; the
launcher swaps to real crun later — the app binary is identical.

## Scripts
- `build-rootfs.sh` — `docker export` an arm64 `debian:bookworm` filesystem to a directory.
- `build-harness.sh` — rootfs + an arm64 SDL3 vendored into it (`/usr/local`).
- `run-in-harness.sh <arm64-bin> [args]` — the launcher: `bwrap` binds the rootfs as `/`,
  `--dev-bind /dev/input`, binds `qemu-tsp` + the binary, runs `qemu-tsp /app`.
- `run-spike3-harness.sh` — proves the launcher reproduces the Path-A SPIKE-3 result.

## Result (2026-06-26, modelmaker)

**PATH-B LAUNCHER: PASS** — a STATIC arm64 SDL3 probe run inside `bwrap + qemu-tsp + rootfs`
enumerates the host-synthesized gamepad **byte-identically to the Path-A baseline** (both
the builtin and descriptor mappings). Validates the launcher mechanism + the `/dev/input`
seam the GUI (.4) and control surface (.5) sit on.

## Known follow-up for tsp-an4.3 — the dynamic-app glibc/sysroot gap

The rootfs is bookworm (**glibc 2.36**); the modelmaker cross-toolchain links against the
host's **glibc 2.39**, so a *dynamic* arm64 SDL3 built that way needs `GLIBC_2.38` and will
**not** run against the bookworm rootfs (`evidence/NOTE.dynamic-glibc.txt`). The real
dynamic OCI app (E6) therefore requires the rootfs's SDL3 + the app to be built against the
**bookworm sysroot** — the clean way is a **multi-stage arm64-bookworm docker builder** that
compiles in-target and copies the artifact into the runtime rootfs (ties to the
reproducible-build epic **tsp-cv7.6**). SPIKE-3's launcher proof uses a STATIC binary, which
is glibc-independent, so this gap does not block the SPIKE-3 conclusion.
