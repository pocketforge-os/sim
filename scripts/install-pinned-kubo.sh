#!/usr/bin/env bash
# Install the pinned kubo binary for offline IPFS CID hashing (publish-image workflow,
# docs/IMAGE-PUBLISHING.md §9). Pins match pocketforge-os/image `kubo.pin` (unixfs-v1-2025
# profile) and BOTH published hashes are verified fail-closed: the dist tarball (sha512)
# and the extracted binary (sha256).
#
# Source policy (tsp-mc9m.41.1030.10): dist.ipfs.tech has been unreachable from GitHub-hosted
# runners since ~2026-10-10 (curl (28) after 300 s; 13/13 red publish-image runs). The ipfs/kubo
# GitHub release serves the BYTE-IDENTICAL upstream tarball (same sha512/sha256 — verified
# 2026-10-17 against the pins), so it is tried FIRST; dist.ipfs.tech stays as a bounded fallback.
# Every attempt is bounded (--connect-timeout/--max-time/--retry), so a total outage fails in
# under a minute with a message naming the host instead of hanging past the ghcr push.
#
# Hermetic test: tests/test_install_pinned_kubo.sh (loopback fixture; positive + negative controls).
set -euo pipefail

KUBO_VER="${KUBO_VER:-0.42.0}"
KUBO_TARBALL_SHA512="${KUBO_TARBALL_SHA512:-054c38a0cf66f7d738e25085ad62cb3a42d03d4bac329b7dd25c1d71cf18e1ce87d55b1d1b705b04c65210dca9109973579e0eb1cd72f6341ecb3311d840d156}"
KUBO_BINARY_SHA256="${KUBO_BINARY_SHA256:-a2bcd470ee4c5df3cfc7f11d8acb7b10ab6cadbf0fd8cb079c77fd303746ee4d}"
KUBO_URLS="${KUBO_URLS:-https://github.com/ipfs/kubo/releases/download/v${KUBO_VER}/kubo_v${KUBO_VER}_linux-amd64.tar.gz https://dist.ipfs.tech/kubo/v${KUBO_VER}/kubo_v${KUBO_VER}_linux-amd64.tar.gz}"
KUBO_INSTALL_DIR="${KUBO_INSTALL_DIR:-/usr/local/bin}"
KUBO_CONNECT_TIMEOUT="${KUBO_CONNECT_TIMEOUT:-10}"
KUBO_MAX_TIME="${KUBO_MAX_TIME:-300}"
KUBO_RETRY="${KUBO_RETRY:-1}"

tgz="kubo_v${KUBO_VER}_linux-amd64.tar.gz"
workdir="$(mktemp -d)"
trap 'find "$workdir" -mindepth 1 -delete; rmdir "$workdir"' EXIT
cd "$workdir"

read -r -a urls <<< "$KUBO_URLS"
verified=0
for url in "${urls[@]}"; do
  rest="${url#*://}"; host="${rest%%/*}"
  echo "kubo-install: trying ${url}"
  if ! curl -fsSL --connect-timeout "$KUBO_CONNECT_TIMEOUT" --max-time "$KUBO_MAX_TIME" \
        --retry "$KUBO_RETRY" --retry-delay 2 "$url" -o "$tgz"; then
    echo "kubo-install: download FAILED from host ${host}" >&2
    continue
  fi
  if ! echo "${KUBO_TARBALL_SHA512}  ${tgz}" | sha512sum -c -; then
    echo "kubo-install: tarball sha512 MISMATCH from host ${host} — refusing" >&2
    rm -f "$tgz"
    continue
  fi
  echo "kubo-install: tarball sha512 verified from host ${host}"
  verified=1
  break
done

if [ "$verified" != 1 ]; then
  echo "kubo-install: FATAL: no source produced a sha512-verified ${tgz}; hosts tried: ${KUBO_URLS}" >&2
  exit 1
fi

tar xzf "$tgz"
echo "${KUBO_BINARY_SHA256}  kubo/ipfs" | sha256sum -c -

if [ -w "$KUBO_INSTALL_DIR" ]; then
  install -m0755 kubo/ipfs "${KUBO_INSTALL_DIR}/ipfs"
else
  sudo install -m0755 kubo/ipfs "${KUBO_INSTALL_DIR}/ipfs"
fi
"${KUBO_INSTALL_DIR}/ipfs" --version
