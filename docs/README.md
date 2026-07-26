# `sim/docs` — index

The PocketForge virtual device simulator, documented. New here? Read them in this order.

| Doc | Read it for |
|-----|-------------|
| **[HONESTY-CONTRACT.md](HONESTY-CONTRACT.md)** | **Read first.** What the sim proves (the logical layer) and the **five** things it deliberately does **not** — GPU blobs, WiFi, timing, enforcement, per-SoC graphics — which stay the flash → serial → webcam hardware gate's sole authority. |
| **[CLI.md](CLI.md)** | The `./sim` command reference (`run` / `gui` / `check` / `doctor` / …), the host requirements and the errors that name them, and the `sim gui` display-path tradeoff (X11 unix-socket passthrough vs a rejected native-host GUI build). |
| **[ADD-A-DEVICE.md](ADD-A-DEVICE.md)** | The "add a device = add data" guide: descriptor + `.scad` semantic model + `render.py` skin generation + the `ci-matrix.toml` posture row + what auto-joins in CI. Written cold-start, with the a523 fold-in as the worked example. |
| **[IMAGE-PUBLISHING.md](IMAGE-PUBLISHING.md)** | The versioned-image publishing design (ghcr). **Designed and disabled** — the publish itself is **owner-gated** (outward-facing) and not yet enabled. |

Start at the repo [`README.md`](../README.md) for the one-command quickstart. Build/run internals
and the nested-container cap set live in [`../docker/README.md`](../docker/README.md).
