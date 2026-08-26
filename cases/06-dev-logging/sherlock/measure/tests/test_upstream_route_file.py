#!/usr/bin/env python3
"""The proxy OWNS the upstream ROUTE, so a provider swap needs no restart.

WHY THIS FILE EXISTS. `UPSTREAM_BASE`, `UPSTREAM_MODEL` and
`UPSTREAM_EXPECTED_RETURNED_IDENTITY` were read ONCE at import, so changing
provider or model meant restarting the proxy — which means restarting the run:
2h42m and ~14 CNY, measured three times. All three paid v38 runs failed on the
PROVIDER, not the harness, and the fix (CloseRouter, 1/27th the cost) is a
different base URL plus a different model id. Being unable to change route
without a restart is what turned a provider outage into a lost run.

With UPSTREAM_ROUTE_FILE set the proxy reads a small JSON object on EVERY
relayed call. This file proves the five properties that make that safe:

  1. the swap lands on the very next request, same PID, and the call goes to the
     new provider with the new model AND the new expected identity;
  2. a swap landing MID-CALL cannot change the route of the call in flight —
     base, model and expected identity always come from ONE read;
  3. every unusable route file FAILS CLOSED, with a NAMED reason code, and never
     falls back to the env values or to the route last seen;
  4. no route file => byte-identical env behaviour, and no route field in the
     ledger at all;
  5. a run that swapped mid-flight is still auditable row by row, and the audit
     still catches a real substitution on BOTH sides of the swap.

Everything here runs against STUB upstreams. No metered tokens, no real key.

    python3 measure/tests/test_upstream_route_file.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
PROXY = os.path.join(MEASURE, "upstream-log-proxy.py")
SHERLOCK = os.path.dirname(MEASURE)
KIT = os.path.dirname(os.path.dirname(os.path.dirname(SHERLOCK)))
WRITER = os.path.join(KIT, "hack", "swap-upstream-route.sh")

sys.path.insert(0, MEASURE)
from lane_guard import audit_ledger, same_family        # noqa: E402

# Two routes that are DELIBERATELY different families, because that is the real
# shape: model_family keeps the vendor prefix, so a swap that moved the base but
# not the identity would trip the lane guard on call one. If these two shared a
# family the tests could not tell a correct swap from a broken one.
MODEL_A = "alpha/alpha-flash-0731"
MODEL_B = "beta/beta-flash-0102"
WRONG_A = "alpha/alpha-pro-0731"        # same vendor, WRONG family
KEY_A = "TESTROUTEKEYAAAAAAAAAAAAAAAAAAAA1111"
KEY_B = "TESTROUTEKEYBBBBBBBBBBBBBBBBBBBB2222"


class Stub(BaseHTTPRequestHandler):
    """Plays a provider. ECHOES the model it was asked for, so a clean call is
    self-evidently clean and a substitution has to be staged on purpose."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            asked = (json.loads(raw or b"{}") or {}).get("model")
        except Exception:
            asked = None
        self.server.seen.append({"model": asked,
                                 "auth": self.headers.get("Authorization")})
        if self.server.delay:
            time.sleep(self.server.delay)
        answered = self.server.answer_as or asked
        payload = json.dumps({
            "model": answered, "stub": self.server.name,
            "usage": {"prompt_tokens": 1000,
                      "prompt_tokens_details": {"cached_tokens": 900}},
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def say(*a):
    """Evidence goes to STDERR: run.sh shows only `tail -3` and unittest's own
    'Ran N tests / OK' summary is on stderr. Same stream, right order."""
    print(*a, file=sys.stderr)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_proxy_module():
    spec = importlib.util.spec_from_file_location("upstream_log_proxy_route", PROXY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_route(path, **fields):
    """The writer's half of the torn-read contract: write elsewhere, os.replace.

    Same directory so the rename stays within one filesystem, which is what
    makes it atomic. This mirrors exactly what swap-upstream-route.sh does.
    """
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".route.")
    try:
        os.write(fd, json.dumps(fields, sort_keys=True).encode() + b"\n")
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def write_raw(path, blob, mode=0o600):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".route.")
    try:
        os.write(fd, blob if isinstance(blob, bytes) else blob.encode())
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def route_obj(base, model, identity=None, generation=0, key_file=None):
    row = {"schema": 1, "base": base, "model": model,
           "expected_returned_identity": identity or model,
           "generation": generation}
    if key_file:
        row["key_file"] = key_file
    return row


class Base(unittest.TestCase):

    def setUp(self):
        self.stubs = {}
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "trace.upstream.jsonl")
        self.route = os.path.join(self.tmp, "trace.upstream.route.json")
        self.px_port = free_port()
        self.proc = None

    def stub(self, name, answer_as=None, delay=0):
        port = free_port()
        srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
        srv.daemon_threads = True
        srv.seen, srv.name, srv.answer_as, srv.delay = [], name, answer_as, delay
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        srv.base = "http://127.0.0.1:%d/v1" % port
        self.stubs[name] = srv
        return srv

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.communicate()
        for srv in self.stubs.values():
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, env_base="http://127.0.0.1:1/v1", **extra):
        env = dict(os.environ, UPSTREAM_BASE=env_base, UPSTREAM_LOG=self.log,
                   LISTEN_PORT=str(self.px_port), RUN_TAG="route-file-test",
                   UPSTREAM_SUBSTITUTION_RETRY_MAX="0")
        for stale in ("UPSTREAM_ROUTE_FILE", "UPSTREAM_API_KEY_FILE",
                      "UPSTREAM_MODEL", "UPSTREAM_EXPECTED_RETURNED_IDENTITY"):
            env.pop(stale, None)
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

    def call(self, model="client-asked-for-this"):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps({"model": model, "messages": []}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer TESTCLIENTKEY0000000000000000"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.getcode(), response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def rows(self, want=1):
        for _ in range(300):
            try:
                with open(self.log, encoding="utf-8") as fh:
                    out = [json.loads(line) for line in fh if line.strip()]
                if len(out) >= want:
                    return out
            except OSError:
                pass
            time.sleep(0.02)
        return []

    def calls_only(self):
        return [r for r in self.rows(0) if not r.get("event")]


# ============================================================== 1. the swap
class SwapTest(Base):

    def test_a_route_swap_moves_provider_model_AND_identity_on_the_next_call(self):
        a = self.stub("A")
        b = self.stub("B")
        write_route(self.route, **route_obj(a.base, MODEL_A, generation=1))
        self.start(UPSTREAM_ROUTE_FILE=self.route)
        pid = self.proc.pid

        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["stub"], "A")
        self.assertEqual(json.loads(body)["model"], MODEL_A)

        write_route(self.route, **route_obj(b.base, MODEL_B, generation=2))

        code, body = self.call()                      # the swap. no restart.
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["stub"], "B")
        self.assertEqual(json.loads(body)["model"], MODEL_B)

        self.assertEqual([s["model"] for s in a.seen], [MODEL_A])
        self.assertEqual([s["model"] for s in b.seen], [MODEL_B])
        self.assertIsNone(self.proc.poll(), "proxy died — that is a restart")
        self.assertEqual(pid, self.proc.pid)

        rows = self.calls_only()
        self.assertEqual([r["route_base"] for r in rows], [a.base, b.base])
        self.assertEqual([r["sent_model"] for r in rows], [MODEL_A, MODEL_B])
        self.assertEqual([r["route_expected_identity"] for r in rows],
                         [MODEL_A, MODEL_B])
        self.assertEqual([r["route_generation"] for r in rows], [1, 2])
        # THE POINT: the identity moved WITH the base. Judging the second call
        # against the first identity would have been a family mismatch.
        self.assertFalse(same_family(MODEL_A, MODEL_B))
        say("    swap: pid %d | %s@%s -> %s@%s | no lane abort"
            % (pid, MODEL_A, a.base, MODEL_B, b.base))

    def test_the_route_may_carry_its_own_key_file(self):
        """A provider swap is also a credential swap; they must move together."""
        a = self.stub("A")
        b = self.stub("B")
        key_a = os.path.join(self.tmp, "a.key")
        key_b = os.path.join(self.tmp, "b.key")
        write_raw(key_a, KEY_A)
        write_raw(key_b, KEY_B)
        write_route(self.route, **route_obj(a.base, MODEL_A, key_file=key_a))
        self.start(UPSTREAM_ROUTE_FILE=self.route)
        self.assertEqual(self.call()[0], 200)
        write_route(self.route,
                    **route_obj(b.base, MODEL_B, generation=1, key_file=key_b))
        self.assertEqual(self.call()[0], 200)
        self.assertEqual(a.seen[0]["auth"], "Bearer " + KEY_A)
        self.assertEqual(b.seen[0]["auth"], "Bearer " + KEY_B)

    def test_the_writer_script_produces_a_route_the_proxy_accepts(self):
        """The shipped writer, not a hand-rolled fixture, is what operators run."""
        if not os.access(WRITER, os.X_OK):
            self.skipTest("writer not present at %s" % WRITER)
        a = self.stub("A")
        out = subprocess.run(["bash", WRITER, "--create", self.route,
                              a.base + "/", MODEL_A],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.start(UPSTREAM_ROUTE_FILE=self.route)
        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["model"], MODEL_A)
        say("    writer receipt:\n      "
            + "\n      ".join(out.stdout.strip().splitlines()))

    def test_the_writer_refuses_to_lower_the_generation(self):
        if not os.access(WRITER, os.X_OK):
            self.skipTest("writer not present at %s" % WRITER)
        base = "https://example.invalid/v1"
        first = subprocess.run(["bash", WRITER, "--create", "--generation", "7",
                                self.route, base, MODEL_A],
                               capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        down = subprocess.run(["bash", WRITER, "--generation", "3",
                               self.route, base, MODEL_A],
                              capture_output=True, text=True)
        self.assertNotEqual(down.returncode, 0)
        self.assertIn("refusing to LOWER generation", down.stderr)
        # The refusal must not have touched the live file.
        with open(self.route, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["generation"], 7)
        # And the default is one PAST the live one, never a reset.
        nxt = subprocess.run(["bash", WRITER, self.route, base, MODEL_B],
                             capture_output=True, text=True)
        self.assertEqual(nxt.returncode, 0, nxt.stderr)
        with open(self.route, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["generation"], 8)

    def test_the_writer_never_writes_a_credential(self):
        if not os.access(WRITER, os.X_OK):
            self.skipTest("writer not present at %s" % WRITER)
        key = os.path.join(self.tmp, "u.key")
        write_raw(key, KEY_A)
        out = subprocess.run(["bash", WRITER, "--create", "--key-file", key,
                              self.route, "https://example.invalid/v1", MODEL_A],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        blob = open(self.route, encoding="utf-8").read()
        self.assertIn(key, blob)                 # the PATH is named
        self.assertNotIn(KEY_A, blob)            # the VALUE never is
        self.assertNotIn(KEY_A, out.stdout + out.stderr)


# =================================================== 2. one route per call
class InFlightTest(Base):

    def test_a_swap_landing_mid_call_cannot_change_the_call_in_flight(self):
        """Rule 5, and the hazard the KEY swap did not have.

        A call must never send model A to provider B and be judged against
        identity C. So: park a call inside a slow provider, swap the route
        underneath it, and prove the parked call kept its whole tuple.
        """
        a = self.stub("A", delay=1.5)
        b = self.stub("B")
        write_route(self.route, **route_obj(a.base, MODEL_A, generation=1))
        self.start(UPSTREAM_ROUTE_FILE=self.route)
        result = {}

        def slow():
            result["code"], result["body"] = self.call()

        worker = threading.Thread(target=slow)
        worker.start()
        time.sleep(0.5)                                  # the call is in flight
        write_route(self.route, **route_obj(b.base, MODEL_B, generation=2))
        worker.join(30)

        self.assertEqual(result["code"], 200, result.get("body"))
        answered = json.loads(result["body"])
        self.assertEqual(answered["stub"], "A")
        self.assertEqual(answered["model"], MODEL_A)
        self.assertEqual(b.seen, [], "the in-flight call moved provider")
        row = self.calls_only()[0]
        self.assertEqual((row["route_base"], row["sent_model"],
                          row["route_expected_identity"], row["route_generation"]),
                         (a.base, MODEL_A, MODEL_A, 1))
        # And the lane did NOT trip: the answer was judged against ITS OWN
        # identity, not against the identity that landed while it was waiting.
        self.assertFalse(os.path.exists(self.log + ".abort.json"))
        say("    in-flight: swap during a 1.5 s call -> tuple stayed "
            "(%s, %s, gen 1)" % (MODEL_A, a.base))

    def test_a_hundred_calls_across_atomic_swaps_are_each_self_consistent(self):
        a = self.stub("A")
        b = self.stub("B")
        tuples = {(a.base, MODEL_A, MODEL_A, 1), (b.base, MODEL_B, MODEL_B, 2)}
        write_route(self.route, **route_obj(a.base, MODEL_A, generation=1))
        self.start(UPSTREAM_ROUTE_FILE=self.route)

        stop = threading.Event()
        swaps = [0]

        def swapper():
            flip = True
            while not stop.is_set():
                if flip:
                    write_route(self.route, **route_obj(b.base, MODEL_B, generation=2))
                else:
                    write_route(self.route, **route_obj(a.base, MODEL_A, generation=1))
                flip = not flip
                swaps[0] += 1
                time.sleep(0.002)

        writer = threading.Thread(target=swapper, daemon=True)
        writer.start()
        codes = []
        try:
            for _ in range(100):
                codes.append(self.call()[0])
        finally:
            stop.set()
            writer.join(5)

        self.assertEqual(set(codes), {200},
                         "a swap produced a refusal: %r" % sorted(set(codes)))
        rows = self.calls_only()
        self.assertEqual(len(rows), 100)
        for index, row in enumerate(rows, 1):
            got = (row["route_base"], row["sent_model"],
                   row["route_expected_identity"], row["route_generation"])
            self.assertIn(got, tuples, "row %d is a TORN route: %r" % (index, got))
            # The provider that answered must be the one the route named, and
            # it echoed the model, so returned == sent proves the whole path.
            self.assertEqual(row["returned_model"], row["sent_model"])
        seen_a = {s["model"] for s in a.seen}
        seen_b = {s["model"] for s in b.seen}
        self.assertLessEqual(seen_a, {MODEL_A}, "provider A saw a foreign model")
        self.assertLessEqual(seen_b, {MODEL_B}, "provider B saw a foreign model")
        gens = sorted({r["route_generation"] for r in rows})
        say("    100 calls / %d swaps: generations %r, providers A=%d B=%d, "
            "0 torn reads" % (swaps[0], gens, len(a.seen), len(b.seen)))
        self.assertEqual(gens, [1, 2], "the swapper never actually swapped")


# ================================================== 3. fail closed, by NAME
class FailClosedTest(Base):
    """Every unusable route file REFUSES, with a NAMED reason code.

    The cases live in ONE dict and the completeness test below asserts that
    every key in it was exercised AND that every reason code the proxy can
    raise appears in it. A fail-closed case that is written but never asserted
    is the exact failure this project keeps shipping.
    """

    # name -> (writer(path), fragment of the diagnosis, reason code)
    CASES = {
        "missing": (lambda p: None, "cannot be stat", "ROUTE_FILE_UNSTATABLE"),
        "empty": (lambda p: write_raw(p, ""), "is empty", "ROUTE_EMPTY"),
        "whitespace": (lambda p: write_raw(p, "  \n\t "), "is empty", "ROUTE_EMPTY"),
        "truncated_json": (lambda p: write_raw(p, '{"schema": 1, "base": "htt'),
                           "not parseable JSON", "ROUTE_UNPARSEABLE"),
        "not_an_object": (lambda p: write_raw(p, '[1, 2, 3]'),
                          "not a JSON object", "ROUTE_NOT_OBJECT"),
        "wrong_schema": (lambda p: write_raw(p, json.dumps(
            dict(route_obj("https://x.invalid/v1", MODEL_A), schema=2))),
            "schema is 2, not 1", "ROUTE_SCHEMA_UNSUPPORTED"),
        "missing_field": (lambda p: write_raw(p, json.dumps(
            {"schema": 1, "base": "https://x.invalid/v1", "model": MODEL_A})),
            "no 'expected_returned_identity' field", "ROUTE_FIELD_MISSING"),
        "blank_field": (lambda p: write_raw(p, json.dumps(
            dict(route_obj("https://x.invalid/v1", MODEL_A), model="   "))),
            "'model' is empty", "ROUTE_FIELD_BLANK"),
        "field_not_string": (lambda p: write_raw(p, json.dumps(
            dict(route_obj("https://x.invalid/v1", MODEL_A), model=7))),
            "'model' is not a string", "ROUTE_FIELD_NOT_STRING"),
        "control_char": (lambda p: write_raw(p, json.dumps(
            dict(route_obj("https://x.invalid/v1", MODEL_A),
                 model="a\r\nHost: evil"))),
            "control character", "ROUTE_FIELD_NOT_ONE_LINE"),
        "relative_base": (lambda p: write_raw(p, json.dumps(
            route_obj("/v1", MODEL_A))),
            "not an absolute http/https URL", "ROUTE_BASE_INVALID"),
        "non_http_base": (lambda p: write_raw(p, json.dumps(
            route_obj("file:///etc/passwd", MODEL_A))),
            "not an absolute http/https URL", "ROUTE_BASE_INVALID"),
        "relative_key_file": (lambda p: write_raw(p, json.dumps(
            route_obj("https://x.invalid/v1", MODEL_A, key_file="rel.key"))),
            "key_file is not an absolute path", "ROUTE_KEY_FILE_NOT_ABSOLUTE"),
        "bad_generation": (lambda p: write_raw(p, json.dumps(
            dict(route_obj("https://x.invalid/v1", MODEL_A), generation="3"))),
            "not a non-negative integer", "ROUTE_GENERATION_INVALID"),
        "carries_credential": (lambda p: write_raw(p, json.dumps(
            dict(route_obj("https://x.invalid/v1", MODEL_A), api_key=KEY_A))),
            "never a credential", "ROUTE_CARRIES_CREDENTIAL"),
        "oversized": (lambda p: write_raw(p, json.dumps(
            dict(route_obj("https://x.invalid/v1", MODEL_A), pad="x" * 5000))),
            "larger than", "ROUTE_OVERSIZED"),
        "duplicate_keys": (lambda p: write_raw(
            p, '{"schema": 1, "schema": 1, "base": "https://x.invalid/v1",'
               ' "model": "m", "expected_returned_identity": "m"}'),
            "not parseable JSON", "ROUTE_UNPARSEABLE"),
        "not_regular": (lambda p: os.mkdir(p), "not a regular file",
                        "ROUTE_NOT_REGULAR"),
        "not_utf8": (lambda p: write_raw(p, b'\xff\xfe{"schema": 1}'),
                     "not valid UTF-8", "ROUTE_NOT_UTF8"),
        "unreadable": (lambda p: write_raw(p, json.dumps(
            route_obj("https://x.invalid/v1", MODEL_A)), 0o000),
            "cannot be opened", "ROUTE_FILE_UNREADABLE"),
    }

    exercised = set()
    unrunnable = set()

    def _refuses(self, name):
        writer, fragment, code = self.CASES[name]
        if name == "unreadable" and os.geteuid() == 0:
            # Named as unrunnable rather than silently dropped: the completeness
            # gate below still accounts for it, so "skipped" can never become
            # the way a case quietly stops being checked.
            type(self).unrunnable.add(name)
            self.skipTest("root ignores file permissions")
        a = self.stub("A")
        writer(self.route)
        self.start(UPSTREAM_ROUTE_FILE=self.route, UPSTREAM_BASE=a.base)
        status, body = self.call()
        self.assertEqual(status, 503, body)
        self.assertIn("upstream route unavailable", body)
        self.assertIn(fragment, body)
        # THE POINT OF THE WHOLE CLASS: no request reached any provider. There
        # is no fallback to the env base and none to a route seen earlier.
        self.assertEqual(a.seen, [], "fell back to the env route — that is the bug")
        self.assertNotIn(KEY_A, body)
        rows = [r for r in self.rows(1) if r.get("event") == "route_unavailable"]
        self.assertTrue(rows, "the refusal was not recorded")
        self.assertEqual(rows[-1]["route_reason_code"], code)
        self.assertEqual(rows[-1]["route_refusals"].get(code), 1)
        self.assertNotIn(KEY_A, json.dumps(rows[-1]))
        type(self).exercised.add(name)
        say("    %-19s %s -> %s" % (name, code, body.strip()[:96]))

    def test_zzz_every_named_case_reached_a_verdict(self):
        """The completeness gate. Runs last (alphabetical) on purpose.

        Two directions, because both have been the bug here before: a case in
        the table that no test ran, and a reason code the proxy can raise that
        the table never names. A blocking term that is computed and never
        asserted is not a gate.
        """
        self.assertEqual(sorted(self.exercised | self.unrunnable),
                         sorted(self.CASES),
                         "a named fail-closed case was never exercised")
        self.assertLessEqual(self.unrunnable, {"unreadable"},
                             "a case became unrunnable that has no exemption")
        source = open(PROXY, encoding="utf-8").read()
        import re
        raised = set(re.findall(r'"(ROUTE_[A-Z_]+)"', source))
        raised.discard("ROUTE_UNUSABLE")          # the default, never raised
        named = {code for _, _, code in self.CASES.values()} | RACE_CODES
        self.assertEqual(raised - named, set(),
                         "the proxy can raise a reason code no test covers")
        say("    completeness: %d named cases (%d asserted, %d unrunnable here: "
            "%s), %d reason codes, 0 uncovered"
            % (len(self.CASES), len(self.exercised), len(self.unrunnable),
               sorted(self.unrunnable) or "-", len(named)))


def _install_case_tests():
    for name in FailClosedTest.CASES:
        def test(self, name=name):
            self._refuses(name)
        test.__name__ = "test_refuses_%s" % name
        test.__doc__ = "fail closed: %s" % name
        setattr(FailClosedTest, test.__name__, test)


_install_case_tests()


# The two faults that are a RACE with the writer rather than a verdict about the
# file. A black-box test cannot stage them reliably (the window is microseconds
# wide), so they are driven directly in RaceTest below — and NAMED here, so the
# completeness gate accounts for them instead of letting them slip through as
# "codes no test covers".
RACE_CODES = {"ROUTE_REPLACED_WHILE_OPENING", "ROUTE_CHANGED_WHILE_READING"}


class _Stat(object):
    """Just enough of os.stat_result for the triple guard, with one field bent."""

    def __init__(self, real, size=None, ino=None):
        self.st_mode = real.st_mode
        self.st_dev = real.st_dev
        self.st_ino = real.st_ino if ino is None else ino
        self.st_size = real.st_size if size is None else size


class RaceTest(unittest.TestCase):
    """Drive _read_route_once directly, for the races a black-box test cannot
    stage — exactly as test_upstream_key_file.py does for the key."""

    def setUp(self):
        self.mod = load_proxy_module()
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "r.json")
        write_route(self.path, **route_obj("https://x.invalid/v1", MODEL_A))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _with_bent_fstat(self, which, **bend):
        real = os.fstat
        seen = [0]

        def fake(fd):
            got = real(fd)
            seen[0] += 1
            if seen[0] == which:
                return _Stat(got, **bend)
            return got

        os.fstat = fake
        try:
            with self.assertRaises(self.mod.RouteUnavailable) as caught:
                self.mod._read_route_once(self.path)
        finally:
            os.fstat = real
        return caught.exception

    def test_a_file_replaced_while_being_opened_is_a_transient_refusal(self):
        exc = self._with_bent_fstat(1, ino=os.lstat(self.path).st_ino + 1)
        self.assertEqual(exc.code, "ROUTE_REPLACED_WHILE_OPENING")
        self.assertTrue(exc.transient)
        say("    race: replaced-while-opening -> %s (transient)" % exc.code)

    def test_a_file_that_changes_size_while_being_read_is_a_transient_refusal(self):
        exc = self._with_bent_fstat(2, size=99999)
        self.assertEqual(exc.code, "ROUTE_CHANGED_WHILE_READING")
        self.assertTrue(exc.transient)
        say("    race: changed-while-reading -> %s (transient)" % exc.code)

    def test_a_persistently_racing_writer_eventually_fails_closed(self):
        """The retry is BOUNDED. A file that never settles must refuse, not spin."""
        self.mod._ROUTE_READ_RETRY_S = 0
        calls = []

        def always_racing(path):
            calls.append(1)
            raise self.mod.RouteUnavailable("route file was replaced while "
                                            "being opened",
                                            "ROUTE_REPLACED_WHILE_OPENING",
                                            transient=True)

        self.mod._read_route_once = always_racing
        with self.assertRaises(self.mod.RouteUnavailable):
            self.mod._read_route(self.path)
        self.assertEqual(len(calls), self.mod._ROUTE_READ_RETRIES)

    def test_a_transient_race_that_settles_is_retried_and_succeeds(self):
        self.mod._ROUTE_READ_RETRY_S = 0
        seq = [self.mod.RouteUnavailable("racing", "ROUTE_CHANGED_WHILE_READING",
                                         transient=True), "settled"]

        def flaky(path):
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        self.mod._read_route_once = flaky
        self.assertEqual(self.mod._read_route(self.path), "settled")

    def test_a_non_transient_fault_is_not_retried(self):
        """A genuinely bad file must refuse on the FIRST look, not after 5."""
        calls = []

        def bad(path):
            calls.append(1)
            raise self.mod.RouteUnavailable("route file is empty", "ROUTE_EMPTY")

        self.mod._read_route_once = bad
        with self.assertRaises(self.mod.RouteUnavailable):
            self.mod._read_route(self.path)
        self.assertEqual(len(calls), 1)


class RecoveryTest(Base):

    def test_fail_closed_is_not_fail_forever(self):
        """One atomic write must fix it, on the very next request."""
        a = self.stub("A")
        self.start(UPSTREAM_ROUTE_FILE=self.route, UPSTREAM_BASE=a.base)
        self.assertEqual(self.call()[0], 503)
        write_route(self.route, **route_obj(a.base, MODEL_A))
        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["model"], MODEL_A)


# ======================================= 4. no route file => today, exactly
class EnvOnlyTest(Base):

    def test_no_route_file_means_the_env_still_governs_byte_for_byte(self):
        a = self.stub("A")
        self.start(UPSTREAM_BASE=a.base, UPSTREAM_MODEL=MODEL_A,
                   UPSTREAM_EXPECTED_RETURNED_IDENTITY=MODEL_A)
        code, body = self.call()
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["model"], MODEL_A)
        self.assertEqual(a.seen[0]["model"], MODEL_A)
        row = self.calls_only()[0]
        self.assertEqual(row["sent_model"], MODEL_A)
        # NO ROUTE FIELD AT ALL. A run that does not use the feature keeps the
        # exact ledger shape every previous run wrote, so no existing reader
        # sees a new field appear unbidden.
        for field in ("route_base", "route_generation", "route_expected_identity"):
            self.assertNotIn(field, row)
        # And the client's Authorization is still forwarded verbatim, because no
        # key file is configured on either the env or the (absent) route.
        self.assertEqual(a.seen[0]["auth"],
                         "Bearer TESTCLIENTKEY0000000000000000")
        say("    env-only: no route field in the ledger, env base/model governed")

    def test_the_env_lane_guard_still_trips_on_a_substitution(self):
        """The identity check must not have become route-only."""
        a = self.stub("A", answer_as=WRONG_A)
        self.start(UPSTREAM_BASE=a.base, UPSTREAM_MODEL=MODEL_A,
                   UPSTREAM_EXPECTED_RETURNED_IDENTITY=MODEL_A,
                   UPSTREAM_LANE_ABORT=os.path.join(self.tmp, "abort.json"))
        self.call()
        for _ in range(200):
            if os.path.exists(os.path.join(self.tmp, "abort.json")):
                break
            time.sleep(0.02)
        with open(os.path.join(self.tmp, "abort.json"), encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertEqual(marker["reason"], "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertEqual(marker["expected_returned_identity"], MODEL_A)


# ================== 4b. the writer refuses every bad write, by NAME
class WriterGuardTest(Base):
    """Every guard in swap-upstream-route.sh reaches the EXIT CODE.

    Same discipline as FailClosedTest: the cases live in one dict and the
    completeness test asserts each was exercised. A guard that prints a
    complaint and still writes the file is worse than no guard.
    """

    # name -> (argv builder, fragment of stderr)
    CASES = {
        "relative_route_path": (lambda t, d: ["--create", "relative.json",
                                              "https://x.invalid/v1", MODEL_A],
                                "must be absolute"),
        "non_http_base": (lambda t, d: ["--create", t, "ftp://x.invalid/v1",
                                        MODEL_A],
                          "absolute http/https URL"),
        "relative_base": (lambda t, d: ["--create", t, "/v1", MODEL_A],
                          "absolute http/https URL"),
        "missing_directory": (lambda t, d: ["--create", d + "/nope/r.json",
                                            "https://x.invalid/v1", MODEL_A],
                              "directory does not exist"),
        "no_create_flag": (lambda t, d: [t, "https://x.invalid/v1", MODEL_A],
                           "may not be using one"),
        "control_char_in_model": (lambda t, d: ["--create", t,
                                                "https://x.invalid/v1",
                                                "a\rb"],
                                  "printable single-line"),
        "key_secret_without_key_file": (lambda t, d: ["--create",
                                                      "--key-secret", "some_secret",
                                                      t, "https://x.invalid/v1",
                                                      MODEL_A],
                                        "needs --key-file"),
        "relative_key_file": (lambda t, d: ["--create", "--key-file", "rel.key",
                                            t, "https://x.invalid/v1", MODEL_A],
                              "key-file path must be absolute"),
        "unknown_option": (lambda t, d: ["--create", "--nope", t,
                                         "https://x.invalid/v1", MODEL_A],
                           "unknown option"),
        "too_few_args": (lambda t, d: ["--create", t, "https://x.invalid/v1"],
                         "usage:"),
        "bad_generation": (lambda t, d: ["--create", "--generation", "x", t,
                                         "https://x.invalid/v1", MODEL_A],
                           "non-negative integer"),
    }

    exercised = set()

    def _refused(self, name):
        if not os.access(WRITER, os.X_OK):
            self.skipTest("writer not present at %s" % WRITER)
        argv, fragment = self.CASES[name]
        out = subprocess.run(["bash", WRITER] + argv(self.route, self.tmp),
                             capture_output=True, text=True, cwd=self.tmp)
        self.assertNotEqual(out.returncode, 0,
                            "%s: the writer accepted it\n%s" % (name, out.stdout))
        self.assertIn(fragment, out.stderr, "%s: %r" % (name, out.stderr))
        # AND IT WROTE NOTHING. A refusal that leaves a half-route behind is the
        # same corruption arrived at by the back door.
        self.assertFalse(os.path.exists(self.route),
                         "%s: a refused write still created the route" % name)
        for stray in os.listdir(self.tmp):
            self.assertFalse(stray.startswith(".upstream.route."),
                             "%s: left a temp file behind: %s" % (name, stray))
        type(self).exercised.add(name)
        say("    writer refuses %-30s %s" % (name, out.stderr.strip().splitlines()[0]))

    def test_zzz_every_named_writer_guard_reached_the_exit_code(self):
        if not os.access(WRITER, os.X_OK):
            self.skipTest("writer not present at %s" % WRITER)
        self.assertEqual(sorted(self.exercised), sorted(self.CASES),
                         "a named writer guard was never exercised")
        say("    completeness: %d writer guards, all reached the exit code"
            % len(self.CASES))


def _install_writer_tests():
    for name in WriterGuardTest.CASES:
        def test(self, name=name):
            self._refused(name)
        test.__name__ = "test_writer_refuses_%s" % name
        test.__doc__ = "writer guard: %s" % name
        setattr(WriterGuardTest, test.__name__, test)


_install_writer_tests()


# ================================ 5. a swapped run is still auditable
class AuditTest(Base):

    def _ledger(self, rows):
        path = os.path.join(self.tmp, "audit.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return path

    def _row(self, identity, returned, base="https://x.invalid/v1", gen=1, **extra):
        row = {"requested_model": "asked", "returned_model": returned,
               "status": 200, "sent_model": identity,
               "route_generation": gen, "route_base": base,
               "route_expected_identity": identity,
               "usage": {"prompt_tokens": 1000,
                         "prompt_tokens_details": {"cached_tokens": 900}}}
        row.update(extra)
        return row

    def test_a_mid_run_swap_is_accepted_row_by_row(self):
        """The audit judges each row against the identity IT was sent under.

        With ONE global --expected this ledger is a family mismatch on every
        row of whichever half does not match — the audit would refuse exactly
        the runs the hot swap exists to rescue.
        """
        summary = {}
        path = self._ledger(
            [self._row(MODEL_A, MODEL_A, "https://a.invalid/v1", 1)] * 5
            + [self._row(MODEL_B, MODEL_B, "https://b.invalid/v1", 2)] * 5)
        self.assertIsNone(audit_ledger(path, expected_identity=MODEL_A,
                                       summary=summary))
        self.assertEqual(summary["route_rows"], 10)
        self.assertEqual(summary["route_identities"], {MODEL_A: 5, MODEL_B: 5})
        self.assertEqual(summary["route_bases"],
                         {"https://a.invalid/v1": 5, "https://b.invalid/v1": 5})
        self.assertEqual(summary["route_generations"], {"1": 5, "2": 5})
        say("    audit: 10 rows over 2 generations accepted; identities %r"
            % summary["route_identities"])

    def test_the_audit_still_catches_a_substitution_on_BOTH_sides_of_the_swap(self):
        pre = self._ledger([self._row(MODEL_A, WRONG_A, "https://a.invalid/v1", 1),
                            self._row(MODEL_B, MODEL_B, "https://b.invalid/v1", 2)])
        reason, detail = audit_ledger(pre, expected_identity=MODEL_A)
        self.assertEqual(reason, "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertIn("row 1", detail)

        post = self._ledger([self._row(MODEL_A, MODEL_A, "https://a.invalid/v1", 1),
                             self._row(MODEL_B, WRONG_A, "https://b.invalid/v1", 2)])
        reason, detail = audit_ledger(post, expected_identity=MODEL_A)
        self.assertEqual(reason, "RETURNED_MODEL_FAMILY_MISMATCH")
        self.assertIn("row 2", detail)
        self.assertIn(MODEL_B, detail)
        say("    audit: a substitution is caught on BOTH sides of the swap")

    def test_a_swapped_ledger_needs_no_global_expected_at_all(self):
        """A hot-swapped run has no single identity to pass in, so the ROWS are
        the declaration. Before this, auditing one meant inventing an
        --expected, and whichever one you picked failed half the rows."""
        path = self._ledger(
            [self._row(MODEL_A, MODEL_A, "https://a.invalid/v1", 1)] * 3
            + [self._row(MODEL_B, MODEL_B, "https://b.invalid/v1", 2)] * 3)
        self.assertIsNone(audit_ledger(path, expected_identity=""))
        # ...and it is not a hole: a wrong-family row still breaches with no
        # --expected supplied.
        bad = self._ledger([self._row(MODEL_A, WRONG_A, "https://a.invalid/v1", 1)])
        self.assertEqual(audit_ledger(bad, expected_identity="")[0],
                         "RETURNED_MODEL_FAMILY_MISMATCH")
        say("    audit: a swapped ledger is auditable with NO global --expected")

    def test_a_ledger_with_no_route_identity_and_no_expected_is_still_a_breach(self):
        legacy = {"requested_model": "asked", "returned_model": MODEL_A,
                  "status": 200, "sent_model": MODEL_A}
        self.assertEqual(audit_ledger(self._ledger([legacy]),
                                      expected_identity="")[0],
                         "EXPECTED_IDENTITY_UNKNOWN")

    def test_route_rows_that_name_no_returned_model_are_still_unmeasured(self):
        """The per-row-identity term must reach a VERDICT, not just a counter.

        Rows declare an identity and none of them names the model that answered:
        that is exactly RETURNED_MODEL_UNKNOWN, and it must fire with no global
        --expected supplied — otherwise the term is decorative.
        """
        row = self._row(MODEL_A, None, "https://a.invalid/v1", 1)
        reason, detail = audit_ledger(self._ledger([row] * 4),
                                      expected_identity="")
        self.assertEqual(reason, "RETURNED_MODEL_UNKNOWN")
        self.assertIn("per-row route identity on 4 row(s)", detail)

    def test_no_identity_check_still_means_no_identity_check(self):
        """--no-identity-check must not start failing on route rows.

        The per-row identity is a BETTER declaration than a flag — but an
        explicit opt-out is still an opt-out, and a blocking counter that
        ignores it would turn the flag into its opposite.
        """
        row = self._row(MODEL_A, WRONG_A, "https://a.invalid/v1", 1)
        summary = {}
        self.assertIsNone(audit_ledger(self._ledger([row] * 40),
                                       expected_identity="",
                                       identity_check=False, summary=summary))
        # ...and the accounting is still filled, because a reader wants to know
        # what the run ran on either way.
        self.assertEqual(summary["route_rows"], 40)

    def test_rows_with_no_route_fields_still_use_the_global_expected(self):
        legacy = {"requested_model": "asked", "returned_model": WRONG_A,
                  "status": 200, "sent_model": MODEL_A,
                  "usage": {"prompt_tokens": 1000,
                            "prompt_tokens_details": {"cached_tokens": 900}}}
        path = self._ledger([legacy])
        reason, _ = audit_ledger(path, expected_identity=MODEL_A)
        self.assertEqual(reason, "RETURNED_MODEL_FAMILY_MISMATCH")

    def test_an_unreadable_route_identity_is_a_breach_not_a_pass(self):
        """Unmeasured is never clean — the rule this whole file is built on."""
        for bad in (None, "", "   ", 7, []):
            row = self._row(MODEL_A, MODEL_A)
            row["route_expected_identity"] = bad
            reason, _ = audit_ledger(self._ledger([row]),
                                     expected_identity=MODEL_A)
            self.assertEqual(reason, "ROUTE_IDENTITY_UNREADABLE", repr(bad))

    def test_a_discarded_substitution_is_judged_against_its_own_route(self):
        row = self._row(MODEL_B, WRONG_A, "https://b.invalid/v1", 2,
                        discarded_substitution=True)
        clean = self._row(MODEL_B, MODEL_B, "https://b.invalid/v1", 2)
        summary = {}
        self.assertIsNone(audit_ledger(self._ledger([row, clean] + [clean] * 40),
                                       expected_identity=MODEL_A, summary=summary))
        self.assertEqual(summary["discarded_substitutions"], 1)


# ==================================================== 6. no leak, anywhere
class NoLeakTest(Base):

    def test_a_key_value_never_reaches_the_route_file_the_ledger_or_a_refusal(self):
        a = self.stub("A")
        key = os.path.join(self.tmp, "u.key")
        write_raw(key, KEY_A)
        write_route(self.route, **route_obj(a.base, MODEL_A, key_file=key))
        self.start(UPSTREAM_ROUTE_FILE=self.route)
        self.assertEqual(self.call()[0], 200)
        # Now break the KEY and keep the route valid: the refusal must name the
        # path and the fault, never the value.
        write_raw(key, KEY_A + " " + KEY_B)
        status, body = self.call()
        self.assertEqual(status, 503, body)
        self.assertIn("exactly one token", body)
        self.assertNotIn(KEY_A, body)
        self.assertNotIn(KEY_B, body)
        self.assertNotIn(KEY_A, open(self.route, encoding="utf-8").read())
        blob = open(self.log, encoding="utf-8").read()
        self.assertNotIn(KEY_A, blob)
        self.assertNotIn(KEY_B, blob)
        self.proc.terminate()
        out, err = self.proc.communicate(timeout=10)
        self.proc = None
        stream = (out or b"").decode() + (err or b"").decode()
        self.assertNotIn(KEY_A, stream)
        self.assertNotIn(KEY_B, stream)
        say("    no leak: refusal, ledger, route file and both proxy streams clean")


# ============================================ 7. the in-process funnel exists
class FunnelTest(unittest.TestCase):

    def test_every_route_in_the_process_comes_from_one_funnel(self):
        """The next fix advances the route in-process when the substitution cap
        is spent. That is only possible if there is exactly ONE place a route is
        obtained. Assert the funnel, so nobody re-reads the env behind it."""
        mod = load_proxy_module()
        source = open(PROXY, encoding="utf-8").read()
        self.assertTrue(hasattr(mod, "_current_route"))
        self.assertGreaterEqual(source.count("_ROUTE_SOURCE"), 2)
        # An in-process caller can retarget the funnel without a restart.
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "r.json")
            write_route(path, **route_obj("https://next.invalid/v2", MODEL_B,
                                          generation=9))
            mod._ROUTE_SOURCE["file"] = path
            route = mod._current_route()
            self.assertEqual((route.base, route.model, route.expected_identity,
                              route.generation), ("https://next.invalid/v2",
                                                  MODEL_B, MODEL_B, 9))
            # The per-route base path, not a module global: a swapped base must
            # not keep the old prefix.
            self.assertEqual(route.url_for("/v2/chat/completions"),
                             "https://next.invalid/v2/chat/completions")
            self.assertNotIn("_BASE_PATH", dir(mod))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
