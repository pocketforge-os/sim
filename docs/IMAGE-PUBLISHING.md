# Published versioned sim image (ghcr + IPFS mirror) — design (infra-113 C2 / D7)

> **Status: LIVE.** Publishing is **ENABLED** (owner decision 2026-07-26, ticket
> 01KYEE9R013A6KF9JAG5ECHMF8, bead **tsp-65jc.9**): every `main` merge + `v*` tag publishes
> `ghcr.io/pocketforge-os/sim`, gated by the `SIM_PUBLISH_ENABLED` repo-variable kill switch
> (see [`../.github/workflows/publish-image.yml`](../.github/workflows/publish-image.yml)). Each
> publish is **also mirrored to our sovereign IPFS pinset** — see [§9](#9-dual-channel-distribution--the-ipfs-sovereign-mirror)
> (bead **tsp-65jc.25**). §§1–8 below are the original design + the owner-decision record that gated
> enablement (kept as history); §§9–10 document the live dual-channel distribution and the optional
> CI-side-pin upgrade.

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

## 9. Dual-channel distribution — the IPFS sovereign mirror

> **Status: LIVE (bead tsp-65jc.25, coordinator-ruled Option B, 2026-07-26).** Every ghcr
> publish is mirrored to our own IPFS pinset. Owner context (ticket 01KYEE9R013A6KF9JAG5ECHMF8):
> the owner explicitly wants PocketForge's IPFS + pinning infra used for distribution.

The image is distributed over **two channels that carry byte-identical content**, joined by the
image **digest**:

| Channel | Role | Consumer path |
|---|---|---|
| **ghcr.io** (`ghcr.io/pocketforge-os/sim`) | **PRIMARY, operational** — native `docker pull`, lowest barrier for CI + external devs | `docker pull ghcr.io/pocketforge-os/sim@sha256:<digest>` |
| **IPFS** (`ipfs.pocketforge.org`) | **Sovereign mirror** — content-addressed, our own infra, no third-party registry dependency | fetch the OCI archive by CID, `skopeo copy`/`docker load` it (below) |

**The digest is the join key.** The CID names an **OCI archive** whose top-level manifest is the
**exact** published image manifest — so `skopeo inspect --raw` of the fetched archive hashes back to
the very `ghcr.io/pocketforge-os/sim@sha256:<digest>` you would pull from ghcr. The two channels are
provably the same artifact; the digest ties them together.

### 9.1 How the mirror is produced (the split)

Producing the mirror is deliberately **split into a credential-free CI half and a laptop/ops-side
pin** — because IPFS-publish credentials stay laptop-side by design (custody posture, `.claude/rules/`
+ tsp-77b: the pinning credential is **not** placed in cloud CI). Nothing in this mirror mints a new
secret or gives CI a pinning credential.

- **CI half** (`.github/workflows/publish-image.yml`, all plain `run:` steps + one github-owned
  action — no third-party marketplace action, org allowed-actions=`selected`): after the ghcr push,
  the workflow (1) exports the published image **by digest** as an OCI archive via `skopeo copy
  docker://…@sha256:<digest> → oci-archive:` (byte-for-byte manifest, so the archive digest == the
  ghcr digest — verified in-step), (2) computes its **deterministic IPFS CID offline** with a
  sha-pinned kubo (`ipfs add --only-hash --cid-version=1 --raw-leaves --chunker=size-1048576
  --hash sha2-256` — the `unixfs-v1-2025`/IPIP-499 profile the whole project pins with, `image/kubo.pin`),
  (3) records the **CID + ghcr digest as a pair** in the run summary, and (4) uploads the OCI archive
  as a **run artifact** (`retention-days: 90`). The CI half reaches **no** pinning API.
- **Ops-side pin** (`pocketforge-automation/scripts/pf-sim-ipfs-mirror.sh`, run from the **laptop**):
  downloads that exact run artifact and pins **those exact bytes** to the public pinset. Because the
  CID is a pure function of `(bytes, flags)` and the bytes + flags are identical, the pinned CID is
  **identical by construction** to the CI-recorded CID (proven: `--only-hash` CID == real `add` CID).

  ```bash
  # from the laptop (gh authed with actions:read; NOT inside a worktree):
  pocketforge-automation/scripts/pf-sim-ipfs-mirror.sh pin --run <publish-run-id> \
      [--expect-cid <cid>] [--expect-digest sha256:<digest>]
  ```

  The pinner pins on **both** public nodes (AWS `54.209.141.243` + Oracle `40.233.121.245`, over the
  existing `~/.ssh/oracle_ipfs_ed25519` key). Both must be pinned: the nodes run
  `Gateway.NoFetch=true` (serve only pinned blocks) behind a Route53 multivalue record, so a
  single-node pin 404s ~half the time until the other node also holds it, and there is no automated
  pinset sync today. If any recomputed or per-node CID disagrees with the CI CID, the pinner
  **fails loud** (`reason=cid_mismatch`) rather than pinning a divergent artifact — that is
  stop-the-line evidence of a profile/version drift, not something to paper over.

### 9.2 How a consumer uses the IPFS path

```bash
CID=<cid-from-the-run-summary>          # printed beside the ghcr digest
DIGEST=sha256:<digest-from-the-run-summary>

# 1) fetch the OCI archive by CID from our sovereign gateway
curl -fsSL "https://ipfs.pocketforge.org/ipfs/${CID}" -o pocketforge-sim.oci.tar

# 2) confirm it IS the published image: its OCI manifest hashes to the ghcr digest
test "sha256:$(skopeo inspect --raw oci-archive:pocketforge-sim.oci.tar | sha256sum | cut -d' ' -f1)" = "$DIGEST"

# 3) load it into the local docker daemon (equivalent to `docker load`)
skopeo copy oci-archive:pocketforge-sim.oci.tar docker-daemon:ghcr.io/pocketforge-os/sim:from-ipfs
# …then run/tag it exactly as if pulled from ghcr; it is the same image.
```

The gateway only serves **pinned** content (`NoFetch=true`), so a successful fetch is itself evidence
the artifact is pinned on our infra. `pf-sim-ipfs-mirror.sh verify --cid <cid> --digest <digest>`
automates this exact round trip (fetch → CID self-check → manifest-digest equality → `docker load`),
with `--per-node` to confirm both gateway nodes hold the pin.

## 10. Optional future upgrade — CI-side pinning (Option A, an OWNER decision)

The §9 design is **Option B**: it fulfills the owner-ratified "mirror every publish on our IPFS"
plan **with no new authority** — it changes no credential custody, mints nothing, and matches how every
other IPFS publish (vault ops, blob distribution) works today. Its one trade-off is that the pin is a
laptop/ops-side step per publish rather than fully inside the CI run.

A **fully-automatic, in-CI pin** is possible but is a genuine **owner decision**, because it reverses
the deliberate custody posture and adds attack surface:

- **What it needs:** a **new GitHub Actions secret** on `pocketforge-os/sim`, bound to a **protected
  `publish` environment** (deploy-branch-pinned to `main`) — this is the hardening design's own stated
  forward path (`pocketforge-automation/scripts/harden-dell-kubo-api.sh` header: *"If a release
  workflow ever needs to publish from CI, the token must come from a GitHub Actions secret bound to a
  PROTECTED `publish` environment … a strictly larger change, tracked separately"*). The secret is
  **either** a bearer token for an **authenticated, internet-reachable kubo add endpoint** on the AWS
  node (which **does not exist today** — the nodes' kubo API is loopback-only, so this ALSO requires
  standing that endpoint up), **or** an SSH deploy key placed in CI (an ops key into cloud CI — a
  security call).
- **Why it is owner-only:** it mints a new secret, sets a durable custody convention, and (for the
  bearer path) exposes a new authenticated write surface on production infra. None of that is an
  agent's call.

Recorded here so the trade is durable and not re-litigated (bead tsp-65jc.25 finding comment carries
the full reachability analysis). Until/unless the owner chooses Option A, the §9 laptop-side pin is
the mirror.
