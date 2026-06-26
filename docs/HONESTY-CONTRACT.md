# Simulator Honesty Contract

The PocketForge virtual device simulator (epic **tsp-an4 / infra-104 / E5**) proves the
**LOGICAL layer ONLY**, cheaply, off-hardware, in CI, on every PR:

- descriptor correctness (one `capabilities.toml` → app + simulator + test);
- input mapping (evdev codes + `absinfo` → SDL3 gamepad enumeration);
- capability / permission semantics (consent contract, typed errors);
- graceful missing-hardware degradation (row omission → typed `hardware-absent`/no-op);
- accessibility-preference propagation (e.g. `hapticsEnabled` off → silent no-op);
- per-variant button/skin coverage (a133 vs a523 = data, not code).

It is **NOT** honest about the following **FIVE** things, and **MUST say so**. These stay
the **flash → serial → webcam hardware gate's SOLE authority**. The two are
complementary, never substitutes.

1. **GPU blobs.** The closed PowerVR UM/KM and the `dc_sunxi` → DE2.0 → fb0 path are
   **not** reproduced. A sim "cube renders" proves **nothing** on-device. SPIKE-3 and the
   T1 harness run SDL3 with **no video backend** (`SDL_VIDEODRIVER=dummy`, gamepad-only
   build) precisely so they touch **no GPU blob** — they are honest about **input only**.
   Software-render fb (tsp-an4.4) proves widget/layout logic, not the on-device blob path.

2. **Real WiFi flakiness.** The XR819/xr829 lossy-link reality (DHCP-up-but-TCP-dead)
   cannot be mocked faithfully; network E2E stays on hardware.

3. **Timing / perf / thermal.** qemu-user timing ≠ A53/A55; no 24 h MEMCG GPU soak, no
   fan/thermal behavior.

4. **Isolation / enforcement.** Guest seccomp is **unenforceable** under qemu-user
   (`PR_SET_SECCOMP` → EINVAL, `seccomp(2)` unimplemented), and the v0 in-process facade is
   **cooperative, not enforced**. The owner-decided launcher is **bubblewrap + qemu-tsp +
   binfmt, NO crun/cgroups** — under qemu-user, crun's seccomp+cgroup enforcement is moot
   anyway. So the sim proves the capability/permission **contract + ergonomics**, **not**
   that a hostile app is confined. Real enforcement (out-of-process broker + namespaces +
   seccomp) is post-Phase-2 and stays hardware/substrate-gated.

   > Note: the evdev-probe honesty itself exists ONLY because of the `qemu-tsp` fork —
   > stock qemu-user translates **zero** evdev/uinput ioctls (an arm64 app gets `ENOTTY`
   > on `EVIOCGID`/`EVIOCGNAME`/`EVIOCGBIT`/`EVIOCGABS`). See `pocketforge-os/qemu-tsp`.

5. **Per-SoC graphics.** The two SoCs use **divergent** display stacks (A133
   sunxifb/no-KMS/fb0 vs A523 kmsdrm/Mali/DRM-KMS); the sim's software-render fb proves
   **neither**, and the descriptor does **not** model the SDL backend / rotation mechanism /
   GPU-userland (those are per-SoC **code + build**, not data). The "zero per-device code"
   proof is for the **I/O layer only**; graphics bring-up stays a per-SoC hardware gate.

## What SPIKE-3 (tsp-an4.2) specifically proves / does not prove

| Proven (off-hardware, under qemu-tsp)                                   | NOT proven (hardware gate) |
|--------------------------------------------------------------------------|----------------------------|
| A host-synthesized `uinput` "TRIMUI Player1" (045e:028e) is **byte-identical** to native at the evdev probe layer, and **indistinguishable to SDL3 gamepad enumeration** (GUID, gamepad recognition, button/axis map). | That the **real silicon** advertises these exact codes (E1 SPIKE-0 reconciles the descriptor to real `EVIOCGBIT`/`EVIOCGABS` on return). |
| The a133 descriptor's `emit-sdldb` mapping binds on the live virtual device (one-descriptor → SDL path). | Any rendering, panel rotation, LED, rumble, WiFi, timing. |
