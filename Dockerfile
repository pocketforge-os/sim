# pocketforge-sim — reproducible, portable BUILD + RUN of the E5 simulator toolchain (tsp-qc1.1).
#
# ONE multistage image, built from PINNED refs on ANY host, carrying everything the per-wall
# run-*.sh / check-*.py need: qemu-tsp + both SDL3 variants + an arm64 bookworm rootfs + the
# compiled apps + the platform descriptors. Retires the hand-staged /home/mm/sim-build tree and
# every host-toolchain assumption (gcc / cross-gcc / cmake / bubblewrap / sudo all live IN the image).
#
# LAYERING (unchanged from E5): the app still runs under qemu-tsp + bubblewrap, NO crun. This image
# is the reproducible OUTER tooling; the bwrap sim runs NESTED inside it (see docker/README.md for
# the run caps). DISTINCT artifact from the device OS image (tsp-1dl.4) — x86 dev/CI tooling.
#
# PINS (every external ref):
#   - debian:bookworm  -> by multi-arch index digest (amd64 build+runtime, arm64 rootfs)
#   - qemu-tsp fork     -> a pinned commit; it pins upstream qemu v8.2.2 (11aa0b1) via its UPSTREAM file
#   - SDL3              -> sim/sdl3/SDL3.pin (release-3.4.10)
#   - platform          -> a pinned commit (descriptors + skins + caps.py)
# RESIDUAL reproducible-from-clean GAP (named, not papered over — ties tsp-cv7.4.13): apt installs
#   from the live bookworm suite, not a snapshot.debian.org timestamp, so a rebuild months later may
#   pull newer point-release packages. Hardening follow-up: pin apt to a snapshot mirror.

ARG DEBIAN_DIGEST=sha256:30482e873082e906a4908c10529180aefb6f77620aea7404b909829fadc5d168
ARG ROOTFS_PLATFORM=linux/arm64
ARG QEMU_TSP_REPO=https://github.com/pocketforge-os/qemu-tsp.git
ARG QEMU_TSP_COMMIT=329c754ad34e4b8062f2a941ab35383811df70bf
# platform is PUBLIC (tsp-qc1.4) -> cloned directly at the pinned commit below (origin/main;
# descriptors match the E5 baselines byte-for-byte). Pin of record: docker/platform.pin.
ARG PLATFORM_REPO=https://github.com/pocketforge-os/platform.git
ARG PLATFORM_COMMIT=0e9512c8158fb55eb5545b5b52fe6e8b4490d359

# ───────────────────────────── toolchain base (x86) ─────────────────────────────
FROM debian:bookworm@${DEBIAN_DIGEST} AS toolchain
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates build-essential gcc g++ \
      gcc-aarch64-linux-gnu g++-aarch64-linux-gnu libc6-dev-arm64-cross \
      cmake ninja-build meson pkg-config python3 python3-venv \
      libglib2.0-dev zlib1g-dev file \
    && rm -rf /var/lib/apt/lists/*

# ───────────────────────────── qemu-tsp (evdev/uinput ioctl fork) ─────────────────────────────
FROM toolchain AS qemu
ARG QEMU_TSP_REPO
ARG QEMU_TSP_COMMIT
RUN git clone "${QEMU_TSP_REPO}" /qemu-tsp \
 && git -C /qemu-tsp checkout --quiet "${QEMU_TSP_COMMIT}" \
 && git -C /qemu-tsp rev-parse HEAD
# build.sh clones+verifies the pinned UPSTREAM qemu and applies the PocketForge patch.
RUN cd /qemu-tsp && ./build.sh
# -> /qemu-tsp/build/qemu-tsp/qemu-aarch64 (static aarch64-linux-user)

# ───────────────────────────── SDL3 (both variants, x86 + arm64 static) ─────────────────────────────
FROM toolchain AS sdl3
COPY sdl3 /src/sdl3
COPY fb   /src/fb
# gamepad-only static (SPIKE-3 / synth path)
RUN OUT=/sdl3 SRC=/sdl3/SDL /src/sdl3/build-sdl3.sh
# offscreen software-render (the check-control/check-sensor/check-skin path); reuse the SDL clone
RUN OUT=/sdl3-render SRC=/sdl3/SDL /src/fb/build-sdl3-render.sh

# ───────────────────────────── arm64 rootfs (pinned digest; NO docker export) ─────────────────────────────
# Replaces harness/build-rootfs.sh's `docker export` with a pinned multi-arch FROM — more
# reproducible and no docker-in-docker. We never RUN anything here; it is a file source.
FROM --platform=${ROOTFS_PLATFORM} debian:bookworm@${DEBIAN_DIGEST} AS rootfs-base

# vendor the dynamic arm64 SDL3 into the rootfs (harness/build-harness.sh step 2; rootfs prepopulated
# so build-harness.sh skips its docker-export branch and only does the SDL DESTDIR-install).
FROM toolchain AS rootfs
COPY --from=rootfs-base / /rootfs/rootfs-arm64/
COPY sdl3    /src/sdl3
COPY harness /src/harness
RUN OUT=/rootfs SDLSRC=/sdl3/SDL /src/harness/build-harness.sh
# -> /rootfs/rootfs-arm64 (arm64 bookworm + /usr/local libSDL3.so vendored)

# ───────────────────────────── SDL3-window (X11, software; the live --window demo, tsp-qc1.5) ─────────────────────────────
# Video-capable SDL3 (X11 ON, software renderer, GL/Vulkan/Wayland OFF) — opens a REAL window for
# the interactive skin demo. x86 only (the GUI runs on the host; the app inside still uses the
# offscreen sdl3-render under qemu-tsp). DEV convenience, NOT a CI/acceptance artifact.
FROM toolchain AS sdl3-window
RUN apt-get update && apt-get install -y --no-install-recommends \
      libx11-dev libxext-dev libxcursor-dev libxi-dev libxrandr-dev libxfixes-dev \
      libxss-dev libxinerama-dev libxtst-dev libxkbcommon-dev \
    && rm -rf /var/lib/apt/lists/*
COPY sdl3 /src/sdl3
COPY skin /src/skin
# build the video SDL3 lib AND the skin-render-window binary HERE (this stage is only built for the
# `demo` target, so the lean runtime/CI build never drags in the X11 SDL3 build).
RUN OUT=/sdl3-window SRC=/sdl3-window/SDL /src/skin/build-sdl3-window.sh && \
    gcc -O2 -I/sdl3-window/x86/include \
        -o /skin-render-window /src/skin/skin-render.c \
        /sdl3-window/x86/lib/libSDL3.a -lm -ldl -lpthread -lrt && \
    file /skin-render-window

# ───────────────────────────── apps (hwprobe-lite x86 + static arm64; skin-render) ─────────────────────────────
# -ffp-contract=off: the sensor path needs it for native==qemu byte-identical FP; harmless (stricter)
# for the control/skin paths. ONE baked binary serves check-control + check-sensor + check-skin.
FROM toolchain AS apps
COPY control /src/control
COPY skin    /src/skin
COPY --from=sdl3 /sdl3-render /sdl3-render
RUN mkdir -p /apps && \
    gcc -O2 -ffp-contract=off -I/sdl3-render/x86/include \
        -o /apps/hwprobe-lite.x86 /src/control/hwprobe-lite.c \
        /sdl3-render/x86/lib/libSDL3.a -lm -ldl -lpthread -lrt && \
    aarch64-linux-gnu-gcc -O2 -ffp-contract=off -static -I/sdl3-render/arm64/include \
        -o /apps/hwprobe-lite.arm64 /src/control/hwprobe-lite.c \
        /sdl3-render/arm64/lib/libSDL3.a -lm -ldl -lpthread -lrt && \
    gcc -O2 -I/sdl3-render/x86/include \
        -o /apps/skin-render /src/skin/skin-render.c \
        /sdl3-render/x86/lib/libSDL3.a -lm -ldl -lpthread -lrt && \
    file /apps/*

# ───────────────────────────── platform descriptors (pinned) ─────────────────────────────
FROM toolchain AS platform
ARG PLATFORM_REPO
ARG PLATFORM_COMMIT
# platform is PUBLIC (tsp-qc1.4) -> clone directly at the pinned commit, so the build is truly
# reproducible-FROM-CLEAN with NO out-of-band input (this CLOSED the former private-archive gap).
RUN git clone "${PLATFORM_REPO}" /platform \
 && git -C /platform checkout --quiet "${PLATFORM_COMMIT}" \
 && git -C /platform rev-parse HEAD \
 && rm -rf /platform/.git \
 && test -f /platform/devices/a133/capabilities.toml && test -f /platform/core/caps.py

# ───────────────────────────── runtime (slim; everything baked in) ─────────────────────────────
FROM debian:bookworm@${DEBIAN_DIGEST} AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 bubblewrap file ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# sim source tree — preserve the relative layout (control/ harness/ fb/ sensor/ skin/ synth/) that
# the python modules' ../<dir> sys.path inserts and control_surface's ../harness/run-in-harness.sh rely on.
COPY . /opt/sim

# baked artifacts from the build stages
COPY --from=qemu     /qemu-tsp/build/qemu-tsp/qemu-aarch64  /opt/pf/qemu-tsp/qemu-aarch64
COPY --from=sdl3     /sdl3                                  /opt/pf/sdl3
COPY --from=sdl3     /sdl3-render                           /opt/pf/sdl3-render
COPY --from=rootfs   /rootfs/rootfs-arm64                   /opt/pf/rootfs-arm64
COPY --from=apps     /apps                                  /opt/pf/apps
COPY --from=platform /platform                              /opt/pf/platform

# image-internal paths the check-*.py read from the environment (retires the /home/mm absolutes)
ENV QEMU_TSP=/opt/pf/qemu-tsp/qemu-aarch64 \
    ROOTFS=/opt/pf/rootfs-arm64 \
    PLATFORM=/opt/pf/platform \
    APP_X86=/opt/pf/apps/hwprobe-lite.x86 \
    APP_ARM64=/opt/pf/apps/hwprobe-lite.arm64 \
    SKIN_RENDER=/opt/pf/apps/skin-render \
    SDLR=/opt/pf/sdl3-render \
    SDLDIR=/opt/pf/sdl3

COPY docker/entrypoint.sh /usr/local/bin/pf-sim
RUN chmod +x /usr/local/bin/pf-sim
ENTRYPOINT ["/usr/local/bin/pf-sim"]
CMD ["check-control", "a133", "a523"]

# ───────────────────────────── demo (tsp-qc1.5; the interactive --window dogfood image) ─────────────────────────────
# Extends the lean runtime with the video-capable skin-render-window (X11) + the X11 client libs it
# dlopens + Xvfb (for the headless --self-test and a no-display fallback). Build with:
#   docker build --target demo -t pocketforge-sim:demo .
# Run live on a host with a real display:  (see docker/README.md "Interactive demo")
#   docker run --rm <caps> -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix pocketforge-sim:demo window a523
# The base `pocketforge-sim` (default target) stays lean for CI. HONESTY: the live window is upstream
# SDL3's X11+software path on the dev host, NOT the on-device graphics; acceptance = the loop runs.
FROM runtime AS demo
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      libx11-6 libxext6 libxcursor1 libxi6 libxrandr2 libxfixes3 \
      libxss1 libxinerama1 libxtst6 libxkbcommon0 \
      xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sdl3-window /skin-render-window /opt/pf/apps/skin-render-window
ENV SKIN_RENDER_WINDOW=/opt/pf/apps/skin-render-window
CMD ["window-selftest", "a523"]
