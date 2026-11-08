#!/usr/bin/env python3
"""Guard against the apt 2.6 delayed-retry queue deadlock (tsp-hqm1p.142).

bookworm's apt (2.6.1, and 2.6.2) can stop for good in `apt-get update`. It
prints nothing, opens no connection and never times out. The trigger is one
index that fails transiently while another index on the same host succeeds.
The failed item is requeued with a retry delay (`Acquire::Retries::Delay`, on
by default). The good item's follow-up indexes are queued ahead of it without
waking the queue, and the Run() loop looks only at the head item of each queue.
So nothing is ever dispatched again. This is upstream LP #2003851, fixed in apt
3.0.3 and 3.1.10 (commit 5378c38d, "Fix stuck acquire queues on partial server
errors"). bookworm has no fixed apt.

The NAS apt-cacher-ng supplies the trigger. Two build stages ask for the same
InRelease at the same moment. acng revalidates it for both, and one client gets
the headers and then a closed connection (acng logs `O 290`). That hung
sim-suite for 45 minutes twice, at 2026-11-06T01:10Z and 2026-11-08T04:43Z.

Disabling the retry delay removes the deadlock and keeps the retries. A
retried item is then ready at once, and every completion cycles the queue.

There are two layers:
  * static: every 99pf-snapshot apt.conf in the Dockerfile that retries also
    sets `Acquire::Retries::Delay "false"`. This layer runs anywhere.
  * live: with apt-get on PATH and root, as in the uinput-domain-test stage,
    replay the failure against a loopback repository. The first fetch of two
    of three suites' Release files is cut after the headers.
    `apt-get update` runs with the apt.conf from the Dockerfile and must
    finish within LIVE_TIMEOUT_S.
"""

import hashlib
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
SNAPSHOT_CONF = re.compile(
    r"printf '([^']*)' > /etc/apt/apt\.conf\.d/99pf-snapshot")
# A healthy run takes about 2 s; the deadlock never ends.
LIVE_TIMEOUT_S = 45
SUITES = ("s1", "s2", "s3")
# The 2026-11-06 pattern: the outer suites fail once, the middle one is good.
CUT_ONCE = ("dists/s1/Release", "dists/s3/Release")


def snapshot_confs(text):
    """Every 99pf-snapshot apt.conf body the Dockerfile writes, unescaped."""
    return [m.group(1).replace("\\n", "\n")
            for m in SNAPSHOT_CONF.finditer(text)]


def conf_value(conf, key):
    m = re.search(r'^%s\s+"([^"]*)";' % re.escape(key), conf, re.M)
    return m.group(1) if m else None


class StaticConfigTest(unittest.TestCase):
    def test_every_retrying_conf_disables_the_retry_delay(self):
        confs = snapshot_confs(DOCKERFILE.read_text(encoding="utf-8"))
        # toolchain, shell-apps and runtime each write one.
        self.assertGreaterEqual(len(confs), 3, "99pf-snapshot writes not found")
        for conf in confs:
            retries = int(conf_value(conf, "Acquire::Retries") or "0")
            if retries == 0:
                continue
            self.assertEqual(
                conf_value(conf, "Acquire::Retries::Delay"), "false",
                "reason=apt_retry_delay_deadlock: Acquire::Retries %d with the "
                "default retry delay can deadlock apt-get update on bookworm "
                "(LP #2003851); conf was:\n%s" % (retries, conf))

    def test_negative_control_flags_the_old_conf(self):
        old = ("printf 'Acquire::Check-Valid-Until \"false\";\\n"
               "Acquire::Retries \"5\";\\n' > /etc/apt/apt.conf.d/99pf-snapshot")
        (conf,) = snapshot_confs(old)
        self.assertEqual(conf_value(conf, "Acquire::Retries"), "5")
        self.assertIsNone(conf_value(conf, "Acquire::Retries::Delay"))


def build_repo(top, arch):
    for suite in SUITES:
        d = top / "dists" / suite / "main" / ("binary-" + arch)
        d.mkdir(parents=True)
        packages = (
            "Package: pf-%s\nVersion: 1\nArchitecture: all\n"
            "Maintainer: pf <pf@invalid>\nFilename: pool/pf-%s_1_all.deb\n"
            "Size: 1\nSHA256: %s\nDescription: test\n\n"
            % (suite, suite, "0" * 64)).encode()
        (d / "Packages").write_bytes(packages)
        rel = "main/binary-%s/Packages" % arch
        (top / "dists" / suite / "Release").write_text(
            "Origin: pf-test\nLabel: pf-test\nSuite: %s\nCodename: %s\n"
            "Date: Sat, 01 Jan 2000 00:00:00 UTC\nArchitectures: %s\n"
            "Components: main\nSHA256:\n %s %d %s\n"
            % (suite, suite, arch, hashlib.sha256(packages).hexdigest(),
               len(packages), rel), encoding="ascii")


def serve(top, cut_once):
    pending = set(cut_once)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            rel = self.path.split("?", 1)[0].lstrip("/")
            with lock:
                cut = rel in pending
                pending.discard(rel)
            path = top / rel
            if not path.is_file():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if cut:
                # Headers, then a closed connection: the acng `O 290` answer.
                self.wfile.flush()
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.pending = pending
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@unittest.skipUnless(shutil.which("apt-get") and shutil.which("dpkg")
                     and os.geteuid() == 0,
                     "live replay needs apt-get and root (uinput-domain-test stage)")
class LiveReplayTest(unittest.TestCase):
    def replay(self, conf):
        arch = subprocess.run(["dpkg", "--print-architecture"], check=True,
                              capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            build_repo(tmp / "repo", arch)
            server = serve(tmp / "repo", CUT_ONCE)
            try:
                for d in ("parts", "sources.d", "prefs.d", "lists/partial",
                          "cache/archives/partial", "log"):
                    (tmp / d).mkdir(parents=True)
                (tmp / "parts" / "99pf-snapshot").write_text(conf)
                port = server.server_address[1]
                (tmp / "sources.list").write_text("".join(
                    "deb [trusted=yes] http://127.0.0.1:%d/ %s main\n"
                    % (port, s) for s in SUITES))
                (tmp / "apt.conf").write_text(
                    'Dir::Etc::parts "%(t)s/parts";\n'
                    'Dir::Etc::sourcelist "%(t)s/sources.list";\n'
                    'Dir::Etc::sourceparts "%(t)s/sources.d";\n'
                    'Dir::Etc::preferencesparts "%(t)s/prefs.d";\n'
                    'Dir::State::lists "%(t)s/lists";\n'
                    'Dir::Cache "%(t)s/cache";\n'
                    'Dir::Log "%(t)s/log";\n'
                    'APT::Architectures { "%(a)s"; };\n'
                    'APT::Sandbox::User "root";\n'
                    'Acquire::Languages "none";\n'
                    % {"t": tmp, "a": arch})
                env = {k: v for k, v in os.environ.items()
                       if k.lower() not in ("http_proxy", "https_proxy",
                                            "all_proxy", "no_proxy")}
                env["APT_CONFIG"] = str(tmp / "apt.conf")
                try:
                    run = subprocess.run(["apt-get", "update"], env=env,
                                         capture_output=True, text=True,
                                         timeout=LIVE_TIMEOUT_S)
                except subprocess.TimeoutExpired as exc:
                    out = exc.stdout or b""
                    out = out.decode() if isinstance(out, bytes) else out
                    self.fail("reason=apt_retry_delay_deadlock: apt-get update "
                              "made no progress for %d s (LP #2003851). "
                              "Output:\n%s" % (LIVE_TIMEOUT_S, out))
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                # The fault must really have been injected.
                self.assertEqual(server.pending, set(), run.stdout)
                for s in SUITES:
                    self.assertTrue(
                        list((tmp / "lists").glob("*_dists_%s_*Packages" % s)),
                        "suite %s index missing:\n%s" % (s, run.stdout))
                return run
            finally:
                server.shutdown()
                server.server_close()

    def test_dockerfile_conf_survives_a_partial_index_failure(self):
        confs = snapshot_confs(DOCKERFILE.read_text(encoding="utf-8"))
        self.assertTrue(confs, "99pf-snapshot writes not found")
        for conf in sorted(set(confs)):
            self.replay(conf)


if __name__ == "__main__":
    unittest.main()
