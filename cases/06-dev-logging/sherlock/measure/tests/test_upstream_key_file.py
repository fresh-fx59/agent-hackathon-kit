#!/usr/bin/env python3
"""The proxy OWNS the upstream credential, so a key swap needs no restart.

WHY THIS FILE EXISTS. The key used to be pinned into the qwen child's
environment at launch (`run-bench.sh`: OPENAI_API_KEY="$SHERLOCK_API_KEY") and
this proxy forwarded whatever Authorization header arrived. Swapping keys meant
restarting the run — hours and real money. And the key is not interchangeable:
a key on new-api's `auto` group was measured answering as a DIFFERENT model on
6 of 20 calls, where a single-group key was 20/20 clean at the same minute.

With UPSTREAM_API_KEY_FILE set, the proxy reads the credential itself and
REPLACES the inbound header. This file proves the four properties that makes
safe: the swap lands on the next request, every unusable file FAILS CLOSED
(never a fallback to the client's header), a torn write can never be sent, and
the key value reaches no ledger row, no log line and no error body.

Everything here runs against a STUB upstream. No metered tokens, no real key.

    python3 measure/tests/test_upstream_key_file.py
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
PROXY = os.path.join(MEASURE, "upstream-log-proxy.py")

# Fake keys. These are test strings, never a credential; the real value only
# ever exists in the secrets console and in the 0600 file the run materialises.
#
# DELIBERATELY NOT `sk-`-SHAPED. record()'s scrubber redacts /\bsk-[\w-]{16,}/,
# so an sk- fixture would be masked on its way to the ledger and the no-leak
# assertions below would pass even if the proxy DID log the key — they would be
# testing the scrubber, not the proxy. These tokens survive the scrubber
# untouched, so "absent from the ledger" means absent, not redacted.
KEY_A = "TESTKEYAAAAAAAAAAAAAAAAAAAAAAAAAAAA1111"
KEY_B = "TESTKEYBBBBBBBBBBBBBBBBBBBBBBBBBBBB2222"
CLIENT_KEY = "TESTCLIENTKEYCCCCCCCCCCCCCCCCCCCC3333"


class Stub(BaseHTTPRequestHandler):
    """Plays the provider and ECHOES the bearer token it was handed."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        seen = self.headers.get("Authorization")
        self.server.seen_auth.append(seen)
        payload = json.dumps({
            "model": "deepseek-v4-flash-0731",
            "saw_authorization": seen,
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def load_proxy_module():
    """Import the proxy in-process. Its filename has a hyphen, so importlib."""
    spec = importlib.util.spec_from_file_location("upstream_log_proxy_uut", PROXY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def say(*a):
    """Evidence goes to STDERR.

    measure/tests/run.sh shows only `tail -3` of a suite, and unittest writes
    its "Ran N tests / OK" summary to stderr. Printing evidence to stdout
    pushed that summary out of the window, so the runner's output stopped
    saying whether the suite passed. Same stream, right order.
    """
    print(*a, file=sys.stderr)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def atomic_write_key(path, value, mode=0o600):
    """The writer's half of the torn-read contract: write elsewhere, os.replace.

    Same directory so the rename stays within one filesystem, which is what
    makes it atomic. This mirrors exactly what swap-upstream-key.sh does.
    """
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".key.")
    try:
        os.write(fd, value.encode())
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


class Base(unittest.TestCase):

    def setUp(self):
        self.up_port = free_port()
        self.srv = HTTPServer(("127.0.0.1", self.up_port), Stub)
        self.srv.seen_auth = []
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "upstream.jsonl")
        self.keyfile = os.path.join(self.tmp, "upstream.key")
        self.px_port = free_port()
        self.proc = None

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.out, self.err = self.proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.out, self.err = self.proc.communicate()
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, **extra):
        env = dict(os.environ,
                   UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
                   UPSTREAM_LOG=self.log, LISTEN_PORT=str(self.px_port),
                   RUN_TAG="key-file-test")
        env.pop("UPSTREAM_API_KEY_FILE", None)
        env.update(extra)
        self.proc = subprocess.Popen([sys.executable, PROXY], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(200):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port, timeout=1) as r:
                    r.read()
                return
            except Exception:
                time.sleep(0.05)
        self.fail("proxy never came up")

    def call(self, client_key=CLIENT_KEY):
        headers = {"Content-Type": "application/json"}
        if client_key is not None:
            headers["Authorization"] = "Bearer " + client_key
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps({"model": "deepseek-v4-flash", "messages": []}).encode(),
            headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.getcode(), response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def ledger_text(self):
        for _ in range(200):
            try:
                with open(self.log, encoding="utf-8") as fh:
                    text = fh.read()
                if text.strip():
                    return text
            except OSError:
                pass
            time.sleep(0.02)
        return ""

    def drain(self):
        """Stop the proxy and return everything it ever wrote (stdout+stderr)."""
        self.proc.terminate()
        out, err = self.proc.communicate(timeout=10)
        self.proc = None
        return (out or b"").decode() + (err or b"").decode()


# ----------------------------------------------------------------- the swap
class SwapTest(Base):

    def test_swap_takes_effect_on_the_next_request_without_a_restart(self):
        atomic_write_key(self.keyfile, KEY_A)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        pid_before = self.proc.pid

        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + KEY_A)

        atomic_write_key(self.keyfile, KEY_B)          # the swap. no restart.

        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + KEY_B)

        self.assertIsNone(self.proc.poll(), "proxy died — that is a restart")
        self.assertEqual(pid_before, self.proc.pid)
        say("    swap: call1 -> %s | call2 -> %s | pid %d throughout"
              % (self.srv.seen_auth[0][:16] + "...A", 
                 self.srv.seen_auth[1][:16] + "...B", pid_before))

    def test_the_client_header_is_replaced_not_merged(self):
        """Whatever qwen sends is irrelevant once the proxy owns the key."""
        atomic_write_key(self.keyfile, KEY_A)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        code, body = self.call(client_key=CLIENT_KEY)
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + KEY_A)
        self.assertNotIn(CLIENT_KEY, body)

    def test_a_client_with_no_header_at_all_still_gets_credentialled(self):
        atomic_write_key(self.keyfile, KEY_A)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        code, body = self.call(client_key=None)
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + KEY_A)

    def test_a_trailing_newline_is_stripped(self):
        """`echo key > file` is the shape an operator will produce by accident."""
        atomic_write_key(self.keyfile, KEY_A + "\n")
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        code, body = self.call()
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + KEY_A)


# ---------------------------------------------------------------- fail closed
class FailClosedTest(Base):
    """Every unusable key file REFUSES. None of them relays the client's header."""

    def _assert_refused(self, why_fragment):
        code, body = self.call()
        self.assertEqual(code, 503, body)
        self.assertIn("upstream key unavailable", body)
        self.assertIn(why_fragment, body)
        # THE POINT OF THE WHOLE TEST: no request reached the provider, and in
        # particular none reached it carrying the client's credential.
        self.assertEqual(self.srv.seen_auth, [],
                         "fell back to the client header — that is the bug")
        self.assertNotIn(CLIENT_KEY, body)
        return body

    def test_missing_file(self):
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        body = self._assert_refused("cannot be stat")
        say("    missing:    %s" % body.strip())

    def test_empty_file(self):
        atomic_write_key(self.keyfile, "")
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        body = self._assert_refused("empty or only whitespace")
        say("    empty:      %s" % body.strip())

    def test_whitespace_only_file(self):
        atomic_write_key(self.keyfile, "   \n\t \n")
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        body = self._assert_refused("empty or only whitespace")
        say("    whitespace: %s" % body.strip())

    def test_unreadable_file(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file permissions")
        atomic_write_key(self.keyfile, KEY_A, mode=0o000)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        body = self._assert_refused("cannot be opened")
        say("    unreadable: %s" % body.strip())

    def test_directory_instead_of_a_file(self):
        os.mkdir(self.keyfile)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        body = self._assert_refused("not a regular file")
        say("    directory:  %s" % body.strip())

    def test_oversized_file_is_not_a_key(self):
        atomic_write_key(self.keyfile, "x" * 5000)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        body = self._assert_refused("larger than")
        say("    oversized:  %s" % body.strip())

    def test_multi_token_file_is_not_a_key(self):
        """A two-line file is a fragment or a mistake, never a credential."""
        atomic_write_key(self.keyfile, KEY_A + "\n" + KEY_B + "\n")
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        body = self._assert_refused("exactly one token")
        say("    two-token:  %s" % body.strip())

    def test_refusal_recovers_when_the_file_is_repaired(self):
        """Fail-closed is not fail-forever: the very next request must work."""
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        self.assertEqual(self.call()[0], 503)
        atomic_write_key(self.keyfile, KEY_A)
        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + KEY_A)


# ----------------------------------------------------------------- torn write
class TornWriteTest(Base):

    def test_a_writer_racing_live_traffic_never_sends_a_truncated_key(self):
        """Hammer the proxy while a writer swaps the file over and over.

        Every token the provider saw must be one of the two WHOLE keys. A
        prefix of either — the signature of a torn read — fails the run, and so
        does a 5xx, because os.replace should never even produce a refusal.
        """
        atomic_write_key(self.keyfile, KEY_A)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        stop = threading.Event()
        whole = {"Bearer " + KEY_A, "Bearer " + KEY_B}

        swaps = [0]

        def writer():
            while not stop.is_set():
                atomic_write_key(self.keyfile, KEY_A if swaps[0] % 2 else KEY_B)
                swaps[0] += 1
                time.sleep(0.001)

        bad = []

        def caller():
            for _ in range(25):
                try:
                    code, body = self.call()
                    if code != 200:
                        bad.append(("status", code, body))
                        continue
                    saw = json.loads(body)["saw_authorization"]
                    if saw not in whole:
                        bad.append(("torn", saw))
                except Exception as exc:                     # noqa: BLE001
                    bad.append(("exception", repr(exc)))

        w = threading.Thread(target=writer, daemon=True)
        w.start()
        callers = [threading.Thread(target=caller) for _ in range(4)]
        for c in callers:
            c.start()
        for c in callers:
            c.join()
        stop.set()
        w.join(timeout=5)

        self.assertEqual(bad, [], "torn or refused during an atomic swap")
        self.assertGreaterEqual(len(self.srv.seen_auth), 100)
        self.assertTrue(set(self.srv.seen_auth) <= whole)
        say("    torn-write: %d calls during %d atomic swaps, distinct tokens seen = %d, "
              "all whole" % (len(self.srv.seen_auth), swaps[0],
                             len(set(self.srv.seen_auth))))

    def test_an_in_place_truncating_writer_is_caught_not_forwarded(self):
        """The unsanctioned `> file` writer. The reader must refuse, not send a stub.

        os.replace makes this impossible; a shell redirect does not. The reader
        does not get to assume the writer behaved.
        """
        atomic_write_key(self.keyfile, KEY_A)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        # A file whose content is a plausible-looking but SHORT key: the shape
        # check cannot see this, so the value of the format rules is bounded —
        # what actually protects the run is that the sanctioned writer renames.
        # What we CAN prove is that a zero-length in-place truncation refuses.
        with open(self.keyfile, "w"):
            pass
        code, body = self.call()
        self.assertEqual(code, 503, body)
        self.assertIn("empty or only whitespace", body)
        self.assertEqual(self.srv.seen_auth, [])


class ReaderUnitTest(unittest.TestCase):
    """Drive _read_api_key_once directly, for the races a black-box test cannot stage.

    `os.replace` is atomic, so the identity/size checks inside the reader can
    NEVER be provoked through the HTTP surface by a well-behaved writer — which
    is exactly why they need a test of their own. Without one they are
    unexercised code that a refactor can delete silently, and the thing they
    protect against (a writer that appends or truncates IN PLACE) is precisely
    the writer we do not control.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.keyfile = os.path.join(self.tmp, "upstream.key")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mod(self):
        mod = load_proxy_module()
        mod.UPSTREAM_API_KEY_FILE = self.keyfile
        return mod

    def test_a_file_that_grows_mid_read_is_refused_not_returned_short(self):
        atomic_write_key(self.keyfile, KEY_A)
        mod = self._mod()
        real_read = os.read
        state = {"grew": False}

        def growing_read(fd, n):
            chunk = real_read(fd, n)
            if not state["grew"]:
                state["grew"] = True
                # An in-place writer appending while we hold the fd open.
                with open(self.keyfile, "a") as fh:
                    fh.write("EXTRA-BYTES-APPENDED-MID-READ")
            return chunk

        mod.os.read = growing_read
        try:
            with self.assertRaises(mod.KeyUnavailable) as caught:
                mod._read_api_key_once()
        finally:
            mod.os.read = real_read
        self.assertIn("changed size while being read", str(caught.exception))
        self.assertTrue(caught.exception.transient)
        say("    torn-read/grew-mid-read: refused -> %s" % caught.exception)

    def test_a_file_that_shrinks_mid_read_is_refused(self):
        atomic_write_key(self.keyfile, KEY_A + "x" * 200)
        mod = self._mod()
        real_read = os.read
        state = {"cut": False}

        def shrinking_read(fd, n):
            chunk = real_read(fd, n)
            if not state["cut"]:
                state["cut"] = True
                with open(self.keyfile, "w") as fh:      # in-place truncation
                    fh.write("short")
            return chunk

        mod.os.read = shrinking_read
        try:
            with self.assertRaises(mod.KeyUnavailable) as caught:
                mod._read_api_key_once()
        finally:
            mod.os.read = real_read
        self.assertIn("changed size while being read", str(caught.exception))
        say("    torn-read/shrank-mid-read: refused -> %s" % caught.exception)

    def test_a_persistently_racing_writer_eventually_fails_closed(self):
        """The retry is BOUNDED. A file that never settles must refuse, not spin."""
        mod = self._mod()
        mod._KEY_READ_RETRY_S = 0
        calls = []

        def always_racing():
            calls.append(1)
            raise mod.KeyUnavailable("key file was replaced while being opened",
                                     transient=True)

        mod._read_api_key_once = always_racing
        with self.assertRaises(mod.KeyUnavailable):
            mod._read_api_key()
        self.assertEqual(len(calls), mod._KEY_READ_RETRIES)
        say("    torn-read/never-settles: failed closed after %d attempts"
              % len(calls))

    def test_a_transient_race_that_settles_is_retried_and_succeeds(self):
        mod = self._mod()
        mod._KEY_READ_RETRY_S = 0
        seq = [mod.KeyUnavailable("key file was replaced while being opened",
                                  transient=True), KEY_A]

        def flaky():
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        mod._read_api_key_once = flaky
        self.assertEqual(mod._read_api_key(), KEY_A)

    def test_a_non_transient_fault_is_not_retried(self):
        """A genuinely bad file must refuse on the FIRST look, not after 5."""
        mod = self._mod()
        calls = []

        def bad():
            calls.append(1)
            raise mod.KeyUnavailable("key file is empty or only whitespace")

        mod._read_api_key_once = bad
        with self.assertRaises(mod.KeyUnavailable):
            mod._read_api_key()
        self.assertEqual(len(calls), 1)


# -------------------------------------------------------------------- no leak
class NoLeakTest(Base):

    def _assert_absent(self, blob, needles, where):
        for needle in needles:
            self.assertNotIn(needle, blob, "%s leaked %s" % (where, needle[:10]))

    def test_the_key_reaches_no_ledger_row_no_log_line_and_no_error_body(self):
        atomic_write_key(self.keyfile, KEY_A)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        code, body = self.call()
        self.assertEqual(code, 200, body)
        ledger = self.ledger_text()
        self.assertTrue(ledger.strip(), "no ledger written — test proves nothing")
        # The stub echoes the token back in its RESPONSE body, which is exactly
        # the hostile case: a provider that quotes the caller's key. It must
        # still not survive into the ledger.
        self._assert_absent(ledger, [KEY_A, CLIENT_KEY], "ledger")
        streams = self.drain()
        self._assert_absent(streams, [KEY_A, CLIENT_KEY], "proxy stdout/stderr")
        say("    no-leak/success: ledger %d bytes, streams %d bytes, key in neither"
              % (len(ledger), len(streams)))

    def test_no_failure_path_leaks_the_key(self):
        """Walk EVERY refusal branch added by this feature and scan everything."""
        cases = [
            ("missing", None),
            ("empty", ""),
            ("whitespace", "  \n "),
            ("oversized", "x" * 5000),
            ("two-token", KEY_A + " " + KEY_B),
            ("non-printable", KEY_A + "\x01"),
        ]
        for name, content in cases:
            with self.subTest(case=name):
                self.setUp()
                try:
                    if content is not None:
                        atomic_write_key(self.keyfile, content)
                    self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
                    code, body = self.call()
                    self.assertEqual(code, 503, body)
                    self._assert_absent(body, [KEY_A, KEY_B, CLIENT_KEY],
                                        "%s error body" % name)
                    ledger = self.ledger_text()
                    self._assert_absent(ledger, [KEY_A, KEY_B, CLIENT_KEY],
                                        "%s ledger" % name)
                    streams = self.drain()
                    self._assert_absent(streams, [KEY_A, KEY_B, CLIENT_KEY],
                                        "%s streams" % name)
                    say("    no-leak/%-13s clean (ledger %d B, streams %d B)"
                          % (name + ":", len(ledger), len(streams)))
                finally:
                    self.tearDown()

    def test_a_provider_that_quotes_the_key_back_is_scrubbed_from_the_ledger(self):
        atomic_write_key(self.keyfile, KEY_A)
        self.start(UPSTREAM_API_KEY_FILE=self.keyfile)
        self.call()
        ledger = self.ledger_text()
        self.assertNotIn(KEY_A, ledger)


# --------------------------------------------------------- backward compatible
class BackwardCompatTest(Base):

    def test_unset_forwards_the_client_header_verbatim(self):
        self.start()                       # no UPSTREAM_API_KEY_FILE at all
        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + CLIENT_KEY)
        say("    compat/unset: provider saw the CLIENT header, unchanged")

    def test_empty_string_is_the_same_as_unset(self):
        """`UPSTREAM_API_KEY_FILE=""` is what the lane passes when unconfigured."""
        self.start(UPSTREAM_API_KEY_FILE="")
        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + CLIENT_KEY)
        say("    compat/empty: provider saw the CLIENT header, unchanged")

    def test_whitespace_env_is_the_same_as_unset(self):
        self.start(UPSTREAM_API_KEY_FILE="   ")
        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["saw_authorization"], "Bearer " + CLIENT_KEY)

    def test_unset_with_no_client_header_sends_none(self):
        self.start()
        code, body = self.call(client_key=None)
        self.assertEqual(code, 200, body)
        self.assertIsNone(json.loads(body)["saw_authorization"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
