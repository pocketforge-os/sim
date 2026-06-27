# `pocketforge-sim` — build + nested-run evidence (tsp-qc1.1 / tsp-qc1.2)

Reproducible containerized BUILD of the E5 sim toolchain + the proof it RUNS nested. Built and run on
**modelmaker** (mm@10.0.40.90, Docker 29.1.3 + buildx 0.30.1), 2026-06-27.

## Build (tsp-qc1.1)

```
docker build -t pocketforge-sim .          # from a clean clone of pocketforge-os/sim
```

- Image: `pocketforge-sim:latest` — **878 MB**, manifest `sha256:e6d92130f263575998ad34b160f96173ff5fe968cbc94967099d2a22b948b6e4`.
- **Pinned inputs (every external ref):**
  - `debian:bookworm@sha256:30482e873082e906a4908c10529180aefb6f77620aea7404b909829fadc5d168` (multi-arch index — amd64 build+runtime, arm64 rootfs via `--platform`).
  - `qemu-tsp` fork commit `329c754ad34e4b8062f2a941ab35383811df70bf` (its `UPSTREAM` pins upstream qemu **v8.2.2** / `11aa0b1`); built `qemu-aarch64 version 8.2.2 (v8.2.2-dirty)` — the "dirty" = the PocketForge evdev/uinput ioctl patch.
  - SDL3 `release-3.4.10` (`sim/sdl3/SDL3.pin`) — both variants, x86 + arm64 static.
  - `platform` commit `716e350ed9e1fb50ebbfde113d5c2d1c8a522822` (origin/main), vendored as a pinned `git archive` (`docker/platform.pin`) — descriptor SHAs match the E5 baselines byte-for-byte (a133 `fb73b2b8…`, a523 `7b9a3079…`).
- **Baked artifacts** (verified present at image-internal paths):
  - `/opt/pf/qemu-tsp/qemu-aarch64` (static aarch64-linux-user)
  - `/opt/pf/apps/hwprobe-lite.x86` (x86-64 pie), `/opt/pf/apps/hwprobe-lite.arm64` (ARM aarch64 **static**), `/opt/pf/apps/skin-render` (x86-64)
  - `/opt/pf/rootfs-arm64/` (arm64 bookworm + `usr/local/lib/libSDL3.so.0.4.10` vendored)
  - `/opt/pf/sdl3/{x86,arm64}` + `/opt/pf/sdl3-render/{x86,arm64}` static libs
  - `/opt/pf/platform/` (devices a133/a523/sdm845 + skins + core/caps.py)
- **No `/home/mm` hand-staging, no host-toolchain assumption** — the whole toolchain (gcc + cross-gcc + cmake + ninja + meson + bubblewrap) lives in the image; the runtime stage is slim (python3 + bubblewrap).

### Named reproducible-from-clean gaps (not papered over — ties `tsp-cv7.4.13` discipline)

1. **apt is not snapshot-pinned.** `apt-get install` pulls from the live bookworm suite, so a rebuild months later may get newer point-release packages. Hardening follow-up: pin apt to a `snapshot.debian.org` timestamp.
2. ~~platform private-archive boundary~~ **CLOSED (tsp-qc1.4)** — `platform` was made public, so the Dockerfile now clones it directly at the pinned commit (`docker/platform.pin`). The build is truly reproducible-from-clean with no out-of-band input.

## Nested run (tsp-qc1.2) — the suite passes INSIDE the container

Run with the minimal verified cap set (see `docker/README.md`):

```
docker run --rm --device /dev/uinput --device-cgroup-rule "c 13:* rmw" \
  -v /dev/input:/dev/input --cap-add SYS_ADMIN \
  --security-opt apparmor=unconfined --security-opt seccomp=unconfined \
  pocketforge-sim {check-control|check-sensor} a133 a523
```

**`check-control a133 a523` → ALL DEVICES PASS:**
```
a133: PASS (native 0 fail, qemu 0 fail, parity 0 mismatch)   all 35 frames byte-identical native==qemu-tsp
a523: PASS (native 0 fail, qemu 0 fail, parity 0 mismatch)   all 41 frames byte-identical native==qemu-tsp
```

**`check-sensor a133 a523` → ALL DEVICES PASS:**
```
a133: PASS (native 0 fail, qemu 0 fail, parity 0 mismatch)   0 imu replies (a133 hardware-absent — no IMU)
a523: PASS (native 0 fail, qemu 0 fail, parity 0 mismatch)   all 6 imu replies byte-identical native==qemu-tsp
```

Identical to the E5 host baselines (a133 35/35, a523 41/41 control frames; a523 6 IMU replies) — now from a
clean image with **zero hand-staged artifacts**. The bwrap+qemu-tsp+uinput launcher nests cleanly with a
scoped (non-`--privileged`) run; no persistent host change required. HONESTY CONTRACT unchanged: this proves
the **logical layer only**; GPU/WiFi/timing/enforcement/per-SoC graphics stay the hardware gate's authority.

## Portability — second host (tsp-qc1.3)

The `pf-sim` entrypoint runs the suite **identically on any host** from the baked image-internal ENV (no
`/home/mm`, no host env-var setup). Proven on a **second host** — the laptop (matt-laptop, Docker 29.1.3):

- **Run-only** (image transferred `docker save | gzip | docker load` from modelmaker): `check-control` and
  `check-sensor` for `a133 a523` → **ALL DEVICES PASS**, byte-identical native==qemu-tsp (a133 35/35,
  a523 41/41 frames; a523 6 IMU replies) — identical to the modelmaker run.
- **Build-from-clean** (same Dockerfile + pins, built on the laptop with buildx 0.30.1): produces an
  equivalent working image; the suite passes identically. This is the full *reproducible-from-clean on ANY
  host* claim, not just *runs on the one box where someone hand-staged it*.

The `/home/mm` absolute-path coupling is retired from the run path: the `run-*.sh` host-dev wrappers now
emit portable hints (the vars are baked in the image), and the container path (`pf-sim` → `check-*.py` →
`run-in-harness.sh`) reads only image-internal ENV. `grep -rn /home/mm` over the run scripts is clean (only
documentation comments in the Dockerfile/entrypoint note *what was retired*).
