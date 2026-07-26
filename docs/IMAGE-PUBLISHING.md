# Published versioned sim image (ghcr) — design (infra-113 C2 / D7)

> **Status: DESIGN + a merged-but-DISABLED publish workflow. Publishing is NOT enabled.**
> The publish itself is **owner-gated** (it is an outward-facing artifact under the org
> namespace). This document is the design; [`../.github/workflows/publish-image.yml`](../.github/workflows/publish-image.yml)
> is the ready-to-enable workflow, disabled by default. Neither creates a package, pushes an
> image, nor records an owner decision. The [owner-decision checklist](#8-owner-decision-points-the-gate)
> at the end is the crisp ask the coordinator batches to the owner; nothing here is enabled
> until those are answered. Tracking bead: **tsp-65jc.9**.

## 1. Why publish the image

The `pocketforge-sim` image ([`../Dockerfile`](../Dockerfile), [`../docker/README.md`](../docker/README.md))
carries the whole E5 sim toolchain — `qemu-tsp` + both SDL3 variants + an arm64 bookworm rootfs +
the compiled apps + the pinned platform descriptors — built from pinned refs on any host. Building
it cold is expensive: recent `sim-gate` runs (which build the image from source, then run the suite)
take **~6–14 min wall** (`success 6m21s`, `9m52s`, `12m55s`, `14m26s` across the last A1/B1/C1 runs),
dominated by compiling `qemu-tsp`, both SDL3 variants, and staging the rootfs from source. The
warm **run-only** path (image already present) is **~3 min**. So a cold build is **~4–11 min of pure
rebuild** that every consumer — the CI gate, a developer's first `./sim` run, a future platform-side
gate (D5), the pf-hwprobe lane — pays independently today.

Publishing the built image to a registry lets those consumers **pull a prebuilt, digest-identified
image** instead of rebuilding it, while keeping the reproducible-from-source path as the source of
truth and the provenance guarantee. This is the D7 "published versioned image (ghcr)" deliverable.

## 2. What gets published, and where

- **Registry:** `ghcr.io` (GitHub Container Registry) — co-located with the source repo, no extra
  credential infrastructure, native to the org.
- **Package:** `ghcr.io/pocketforge-os/sim`.
- **Target:** the lean **`runtime`** stage (the default `pocketforge-sim` image the CI gate and
  `./sim` consume). The `demo` stage (X11 window, `sim gui`) is a dev convenience and a superset of
  `runtime`; publishing it is **optional** and, if wanted, goes to a **distinct tag suffix**
  (`…:main-demo`, `…:vX.Y.Z-demo`) built with `--target demo`, never overwriting the lean tag. First
  cut publishes `runtime` only; `demo` is a follow-up if there is demand.

The published sim image is a **distinct artifact from the device OS image** (`tsp-1dl.4`) — it is
x86 dev/CI tooling, not the appliance image, and it is **not** covered by the minisign release-trust
anchor (see [§6 supply chain](#6-supply-chain--consumer-guidance)).

## 3. Versioning & tagging

The **immutable identity is the digest** (`ghcr.io/pocketforge-os/sim@sha256:…`); tags are moving
conveniences. On each publish the workflow (via `docker/metadata-action`) stamps:

| Trigger | Tags applied | Meaning |
|---|---|---|
| push to `main` (post-merge) | `main`, `sha-<short>` | `main` = the always-current trunk image; `sha-<short>` = an **immutable** handle for that exact commit |
| semver release tag `v*` | `vX.Y.Z`, `X.Y`, `latest` | released, human-referenceable versions; `latest` tracks the newest release |

- **`sha-<short>`** tags are never re-pointed — they are the pin a reproducible consumer resolves to a
  digest and records.
- **`main`** and **`latest`** are moving tags — convenient for humans, **never** what a gate or a
  reproducible build pins to (pin the digest).
- OCI labels (`org.opencontainers.image.source`, `.revision`, `.created`) are stamped so `docker
  inspect` traces an image back to its commit.

## 4. Provenance — built by CI only, never hand-pushed

The publish path is **CI-only and post-merge**:

- The publish workflow triggers **only** on `push` to `main` and on `v*` release tags — i.e. on
  **already-merged, trusted** code. It never runs on a `pull_request`, so **fork/PR code never
  drives a publish** (this also keeps it clear of the build-lab confinement invariant, tsp-20u1).
- The image is built **from the committed, pinned `Dockerfile`** in the same run that pushes it —
  there is **no path for a hand-built or locally-tagged image to reach the registry**. A human
  `docker push` to `ghcr.io/pocketforge-os/sim` is out of contract (and should be blocked by package
  write-access being restricted to the repo's Actions — see [§7](#7-tokens--permissions)).
- **Runner: GitHub-hosted `ubuntu-latest`, NOT the self-hosted `trimui-build-lab`.** Publishing only
  **builds** the image (network on: apt + pinned clones); it does **not** run the nested bwrap+qemu
  suite, so it needs none of the `/dev/uinput` + `SYS_ADMIN` + unconfined-apparmor caps the gate
  needs. Building on a GitHub-hosted runner keeps the Dell runner's exposure **unchanged** (infra-113
  **D11**: do not grow the Dell runner's scope) and avoids putting a registry-write credential on the
  host-root self-hosted runner. This is the one deliberate split from `sim-gate` (which *must* be
  self-hosted for the nested-sim caps).
- **Build-provenance attestation** (`actions/attest-build-provenance`) is wired in the workflow to
  produce a signed SLSA provenance statement binding the pushed digest to the workflow + commit —
  giving consumers a cryptographic "this digest was built by this repo's CI from this commit" check.
  It is included but easy to drop; **cosign keyless signing** is a further optional hardening flagged
  as a [future item](#future-hardening-non-blocking).

## 5. CI (and developer) consumption for speed

The seam already exists: the `./sim` CLI and every suite entrypoint honor **`SIM_IMAGE`** (and
`SIM_DEMO_IMAGE`) to point at a prebuilt tag instead of building — the gate already passes a
run-scoped `SIM_IMAGE` today. Consuming a **published** image is the same seam pointed at ghcr.

**The honesty constraint that shapes the whole consumption design:** the `sim-gate` PR check exists to
prove the **current PR's** descriptors + build. A PR that changes the image's build inputs (the
`Dockerfile`, any pin, or the `sdl3/`, `fb/`, `harness/`, `control/`, `skin/` build scripts) **must
build from source** — pulling a stale published image there would make the gate green on code it never
actually built. So consumption is **conditional**:

- **PR touches no build-input path** → the run may **pull** `ghcr.io/pocketforge-os/sim` at the
  digest published for the merge-base and run only (**~3 min** vs ~6–14 min).
- **PR touches a build-input path** → **build from source** as today; the gate tests the PR's build.

First-cut keying can be a **path filter** on the build-input set (simple, conservative — a
non-build-input PR pulls, anything else builds). A tighter follow-up computes a **content hash** of
the build-input set and pulls the published image only on an exact hash match, else rebuilds — never
trusting a tag across a build-input change.

**Scope note:** wiring the gate to consume the published image is **out of scope for C2** — it cannot
land before publishing is enabled (there is nothing to pull), and it is a change to the required gate,
so it wants its own bead once the owner enables publishing. C2 delivers the **design + the disabled
publish workflow**; the gate-consumption rewire is a **named follow-up** (file post-enablement).
Developers get the win immediately once publishing is on: `SIM_IMAGE=ghcr.io/pocketforge-os/sim
./sim check` skips the cold build.

## 6. Supply chain & consumer guidance

- **Pin by digest, never a moving tag.** Consumers (a gate, a reproducible build, a downstream repo)
  reference `ghcr.io/pocketforge-os/sim@sha256:…`, not `:main`/`:latest`. Record the digest where the
  pin lives, exactly as the Dockerfile pins its own inputs by digest/commit.
- **README verify instructions** (to add in the C3 docs overhaul, once publishing is enabled):
  ```bash
  # pull the pinned image
  docker pull ghcr.io/pocketforge-os/sim@sha256:<digest>
  # verify CI provenance (built by this repo's CI from a known commit)
  gh attestation verify oci://ghcr.io/pocketforge-os/sim@sha256:<digest> \
      --owner pocketforge-os
  ```
- **Not release-signed under the minisign anchor.** The sim image is dev/CI tooling; it is **not** a
  device release artifact and is deliberately **outside** the minisign release-trust chain
  (`../../.claude/rules/secrets.md` — that anchor is AWS-only, `pf-ci-sign`, and gates device
  releases, not x86 tooling). Its integrity story is **registry digest + CI build-provenance
  attestation**, which is the right tool for a container image. If stronger signing is wanted, it is
  **cosign keyless (OIDC)** — a separate, additive decision, not a reuse of the minisign key.
- **No closed blob in the image.** Every layer is built from **public/owned source** — `debian`
  (public digest), `qemu-tsp` (pocketforge-os, public), `platform` (public since tsp-qc1.4), SDL3
  (upstream release). The closed PowerVR blob lives only in the **device OS image**, never here. So
  publishing this image **leaks no proprietary bits** — a material input to the public-vs-private
  decision below.

## 7. Tokens & permissions

**The publish authenticates with the built-in Actions `GITHUB_TOKEN`, scoped `packages: write` at
the workflow level — NOT the `pocketforge-agent` App install token.** This distinction matters for
the owner ask:

- Agents author PRs as the `pocketforge-agent` GitHub App, whose install token is deliberately
  **`contents:write, pull_requests:write, workflows:write, metadata:read, checks:read, statuses:read`
  — and deliberately EXCLUDES `packages:write`** (`../../.claude/rules/secrets.md`). Agents push
  *code*, not *images*. **Nothing in this design changes that**, and no agent gains image-push power.
- The **workflow's** `GITHUB_TOKEN` is a separate, per-run token; granting it `packages: write` in
  the workflow `permissions:` block is the standard, recommended ghcr publish path (`docker/login-
  action` with `password: ${{ secrets.GITHUB_TOKEN }}`). It exists only for the duration of the
  publish job and cannot be assumed by an agent.
- **What the owner actually decides here** is therefore *not* "widen the agent App scope" (unneeded)
  but: **(a)** allow the repo's Actions `GITHUB_TOKEN` to publish packages (org/repo package settings
  may restrict Actions package writes; first publish also **creates** the package and sets its initial
  visibility), and **(b)** the [visibility choice below](#8-owner-decision-points-the-gate).

## 8. Owner decision points (the gate)

Publishing is enabled only after the owner answers these. Enumerated crisply for the coordinator to
batch:

1. **Enable publishing at all?** This makes `pocketforge-sim` an outward-facing artifact under the
   `pocketforge-os` org namespace, published automatically on every `main` merge + release tag.
   *(Yes → set the `SIM_PUBLISH_ENABLED` repo variable and uncomment the push/tag triggers in the
   workflow; see its header. No/defer → the design stands, the workflow stays disabled.)*
2. **Public or private package?** `ghcr.io/pocketforge-os/sim` **public** (anyone can pull — good for
   open dev + external contributors; consistent with `platform` already being public; **§6 confirms
   no proprietary bits are in the image**) vs **private/org-only**. This is genuinely the owner's
   outward-facing call.
3. **Grant the CI `GITHUB_TOKEN` package-publish permission** (per [§7](#7-tokens--permissions)):
   confirm the standard workflow-`GITHUB_TOKEN` + `packages: write` path is acceptable (it needs **no**
   change to the `pocketforge-agent` App scope), and enable Actions package-write / initial package
   visibility in the repo/org settings so the first publish succeeds.
4. **(Minor / future)** Keep **build-provenance attestation** on by default (recommended, low cost)?
   And is **cosign keyless signing** wanted as an additional layer, or is digest + attestation
   sufficient for a dev/CI tool?

### Future hardening (non-blocking)

- **cosign keyless (OIDC) signing** of the published digest, if #4 wants signing beyond attestation.
- **`snapshot.debian.org` apt pinning** (the named reproducible-from-clean apt gap, `docker/README.md`
  / infra-113 D11 / bead **tsp-65jc.17**) — orthogonal to publishing but improves what gets published.
- **Gate-consumes-published-image rewire** ([§5](#5-ci-and-developer-consumption-for-speed)) — file
  post-enablement; conditional pull keyed on the build-input set.
- **Publish the `demo` target** as a `-demo`-suffixed tag if `sim gui` demand appears.
