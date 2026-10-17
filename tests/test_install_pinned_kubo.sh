#!/usr/bin/env bash
# Hermetic test for scripts/install-pinned-kubo.sh (the publish-image workflow's
# "Install pinned kubo" step, tsp-mc9m.41.1030.10).
#
# Positive + negative controls in ONE invocation, no external network:
#   P1  workflow YAML parses and delegates to the script
#   P2  script defaults match the pins in pocketforge-os/image kubo.pin
#   P3  positive control: local HTTP fixture, correct sha512+sha256 -> rc 0, binary installed
#   N1  negative control: wrong sha512 -> rc != 0, message names the host, nothing installed
#   N2  negative control: unreachable host (RFC 5737 TEST-NET-1) -> rc != 0, bounded, names host
#   F1  fallback control: unreachable first URL, good second URL -> rc 0 via fallback
#
# Requires: bash, coreutils, tar, python3 (http.server). Runs on the GitHub-hosted
# runner and on the laptop; no sudo, no docker, no internet.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/install-pinned-kubo.sh"
WORKFLOW="$REPO_ROOT/.github/workflows/publish-image.yml"

PINNED_VER=0.42.0
PINNED_SHA512=054c38a0cf66f7d738e25085ad62cb3a42d03d4bac329b7dd25c1d71cf18e1ce87d55b1d1b705b04c65210dca9109973579e0eb1cd72f6341ecb3311d840d156
PINNED_SHA256=a2bcd470ee4c5df3cfc7f11d8acb7b10ab6cadbf0fd8cb079c77fd303746ee4d

fails=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; fails=$((fails + 1)); }

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/test-install-pinned-kubo.XXXXXX")"
cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; fi
  find "$WORKDIR" -mindepth 1 -delete
  rmdir "$WORKDIR"
}
trap cleanup EXIT

# ── P1: workflow parses and delegates ──────────────────────────────────────────
if python3 -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))' "$WORKFLOW" 2>"$WORKDIR/p1.err"; then
  pass "P1a workflow YAML parses"
else
  fail "P1a workflow YAML parse: $(cat "$WORKDIR/p1.err")"
fi
if grep -q 'install-pinned-kubo.sh' "$WORKFLOW" \
   && grep -Eq 'KUBO_TARBALL_SHA512|sha512sum -c' "$SCRIPT" 2>/dev/null; then
  pass "P1b workflow delegates to the script and script keeps hash checks"
else
  fail "P1b workflow must call scripts/install-pinned-kubo.sh and the script must keep sha512sum -c"
fi

# ── P2: script defaults carry the exact pins ──────────────────────────────────
if [ -f "$SCRIPT" ] \
   && grep -qF "KUBO_VER:-$PINNED_VER" "$SCRIPT" \
   && grep -qF "$PINNED_SHA512" "$SCRIPT" \
   && grep -qF "$PINNED_SHA256" "$SCRIPT"; then
  pass "P2 script defaults match kubo.pin ($PINNED_VER, sha512, sha256)"
else
  fail "P2 script defaults do not match the pinned values"
fi

[ -f "$SCRIPT" ] || { echo "FAIL: $SCRIPT missing; aborting remaining controls" >&2; exit 1; }

# ── fixture: fake kubo tarball served over loopback HTTP ──────────────────────
FIX="$WORKDIR/fixture"
SERVE="$WORKDIR/serve"
mkdir -p "$FIX/kubo" "$SERVE" "$WORKDIR/bin"
printf '#!/bin/sh\necho "ipfs version 0.0.0-fixture"\n' > "$FIX/kubo/ipfs"
chmod 0755 "$FIX/kubo/ipfs"
TGZ_NAME="kubo_v${PINNED_VER}_linux-amd64.tar.gz"
tar czf "$SERVE/$TGZ_NAME" -C "$FIX" kubo
FIX_SHA512="$(sha512sum "$SERVE/$TGZ_NAME" | cut -d' ' -f1)"
FIX_SHA256="$(sha256sum "$FIX/kubo/ipfs" | cut -d' ' -f1)"

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
( cd "$SERVE" && exec python3 -m http.server --bind 127.0.0.1 "$PORT" ) >"$WORKDIR/http.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 50); do
  curl -fsS -m 2 -o /dev/null "http://127.0.0.1:$PORT/$TGZ_NAME" && break
  sleep 0.1
done

GOOD_URL="http://127.0.0.1:$PORT/$TGZ_NAME"
DEAD_URL="http://192.0.2.1:9/$TGZ_NAME"   # RFC 5737 TEST-NET-1: guaranteed unreachable

run_script() { # run_script <outfile> <errfile> env...
  local out="$1" err="$2"; shift 2
  env KUBO_CONNECT_TIMEOUT="${KUBO_CONNECT_TIMEOUT:-3}" KUBO_RETRY="${KUBO_RETRY:-0}" \
      KUBO_MAX_TIME="${KUBO_MAX_TIME:-30}" KUBO_INSTALL_DIR="$WORKDIR/bin" "$@" \
      bash "$SCRIPT" >"$out" 2>"$err"
}

# ── P3: positive control ───────────────────────────────────────────────────────
rm -f "$WORKDIR/bin/ipfs"
if run_script "$WORKDIR/p3.out" "$WORKDIR/p3.err" \
     KUBO_URLS="$GOOD_URL" KUBO_TARBALL_SHA512="$FIX_SHA512" KUBO_BINARY_SHA256="$FIX_SHA256"; then
  if [ -x "$WORKDIR/bin/ipfs" ] && grep -q '0.0.0-fixture' "$WORKDIR/p3.out"; then
    pass "P3 positive control: verified download installs a working binary"
  else
    fail "P3 rc 0 but binary not installed / version not printed"
  fi
else
  fail "P3 positive control failed rc!=0: $(tail -3 "$WORKDIR/p3.err")"
fi

# ── N1: wrong sha512 -> refuse, name the host, install nothing ────────────────
rm -f "$WORKDIR/bin/ipfs"
if run_script "$WORKDIR/n1.out" "$WORKDIR/n1.err" \
     KUBO_URLS="$GOOD_URL" KUBO_TARBALL_SHA512="$(printf '0%.0s' $(seq 1 128))" \
     KUBO_BINARY_SHA256="$FIX_SHA256"; then
  fail "N1 wrong sha512 must fail closed but rc=0"
elif grep -q "127.0.0.1:$PORT" "$WORKDIR/n1.err" && [ ! -e "$WORKDIR/bin/ipfs" ]; then
  pass "N1 wrong sha512 fails closed, message names the host, nothing installed"
else
  fail "N1 rc!=0 but message does not name the host or a binary was installed"
fi

# ── N2: unreachable host -> bounded failure naming the host ───────────────────
rm -f "$WORKDIR/bin/ipfs"
t0=$(date +%s)
if KUBO_CONNECT_TIMEOUT=2 run_script "$WORKDIR/n2.out" "$WORKDIR/n2.err" \
     KUBO_URLS="$DEAD_URL" KUBO_TARBALL_SHA512="$FIX_SHA512" KUBO_BINARY_SHA256="$FIX_SHA256"; then
  fail "N2 unreachable host must fail but rc=0"
else
  t1=$(date +%s)
  if [ $((t1 - t0)) -le 20 ] && grep -q '192.0.2.1' "$WORKDIR/n2.err" && [ ! -e "$WORKDIR/bin/ipfs" ]; then
    pass "N2 unreachable host fails in $((t1 - t0))s (bounded) and message names the host"
  else
    fail "N2 took $((t1 - t0))s or message lacks the host: $(tail -3 "$WORKDIR/n2.err")"
  fi
fi

# ── F1: dead first source, good fallback ──────────────────────────────────────
rm -f "$WORKDIR/bin/ipfs"
if KUBO_CONNECT_TIMEOUT=2 run_script "$WORKDIR/f1.out" "$WORKDIR/f1.err" \
     KUBO_URLS="$DEAD_URL $GOOD_URL" KUBO_TARBALL_SHA512="$FIX_SHA512" KUBO_BINARY_SHA256="$FIX_SHA256" \
   && [ -x "$WORKDIR/bin/ipfs" ]; then
  pass "F1 falls back to the second source after the first is unreachable"
else
  fail "F1 fallback did not succeed: $(tail -3 "$WORKDIR/f1.err" 2>/dev/null)"
fi

if [ "$fails" -ne 0 ]; then
  echo "RESULT: FAIL ($fails control(s) failed)" >&2
  exit 1
fi
echo "RESULT: PASS (all controls)"
