#!/usr/bin/env python3
"""FIX 2: the PRE-SEND wall. Nothing else on this lane is one.

VERIFIED IN THE INSTALLED qwen-code 0.22.0 BUNDLE, and this is why the fix has to
live in the proxy rather than in a setting:

  * `hard = W - 23,000` does NOT block a send.
    `shouldForceFromHard = !exactRoute && isHardTier && hardRescueFailureCount < 3`;
    once three hard-tier rescues have failed the code logs «hard-tier rescue
    skipped after N failed attempts; relying on reactive overflow recovery», sets
    the compression info to NOOP, and `shouldStopAfterHardRescue(false, …)`
    returns false — the oversized prompt goes out. Run r6 put 334,339 tokens on
    the wire that way.
  * `model.sessionTokenLimit` is exact but reactive: it compares the PREVIOUS
    response's reported prompt_tokens, so it cannot stop the turn that balloons.

The proxy is the last place that sees a request before it leaves the box, so it is
the only true wall available. THE REFUSAL MUST BE RECOVERABLE, and the shape is
not a matter of taste — qwen only compacts and retries when
`getContextLengthExceededInfo(error).isExceeded`, which requires the error text to
match one of CONTEXT_LENGTH_PATTERNS (and none of TIMEOUT_PATTERNS):

    /\\bcontext[_\\s-]?length[_\\s-]?exceeded\\b/i
    /\\bmaximum context length\\b/i
    /\\bprompt\\s+(?:is\\s+)?too long\\b/i               … and five more

and `parseTokenCounts` recovers the numbers from
«maximum context length is N tokens … requested M tokens», which it feeds to the
reactive compression as `actualTokens` / `limitTokens`. So the refusal speaks the
provider's own dialect on purpose: a refusal qwen cannot classify would kill the
run, which is worse than the breach it prevents.
"""
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_upstream_log_proxy as base                      # the proven harness


class PreSendGate(base.ProxyCase):

    def start_gated(self, gate, chars_per_token=None, mode="json_toolcall"):
        import subprocess
        self.srv.mode = mode
        env = dict(os.environ,
                   UPSTREAM_BASE="http://127.0.0.1:%d/v1" % self.up_port,
                   UPSTREAM_LOG=self.log,
                   LISTEN_PORT=str(self.px_port),
                   UPSTREAM_PER_REQUEST_TOKEN_GATE=str(gate))
        if chars_per_token is not None:
            env["UPSTREAM_CHARS_PER_TOKEN"] = str(chars_per_token)
        self.proc = subprocess.Popen([sys.executable, base.PROXY], env=env,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/healthz" % self.px_port,
                        timeout=1) as r:
                    r.read()
                return
            except Exception:
                time.sleep(0.05)
        self.fail("proxy never came up")

    def post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.px_port,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer test"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def rows(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    # ------------------------------------------------------------------
    def test_a_legal_request_is_forwarded_untouched(self):
        self.start_gated(gate=262000)
        code, _ = self.post({"model": "m", "max_tokens": 6700,
                             "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.srv.seen), 1, "a legal call must reach upstream")

    def test_an_illegal_request_never_reaches_the_provider(self):
        # Sized from the gate, not from a magic number: whatever the calibrated
        # ratio is, 1,200,000 characters is over 262,000 tokens for any divisor
        # this project would defend (it is 352,941 tokens even at 3.40).
        self.start_gated(gate=262000)
        big = "x" * 1200000
        code, payload = self.post({"model": "m", "max_tokens": 20000,
                                   "messages": [{"role": "user",
                                                 "content": big}]})
        self.assertEqual(self.srv.seen, [],
                         "the provider must never see an illegal request")
        self.assertEqual(code, 400, payload[:200])
        row = json.loads(payload)
        msg = row["error"]["message"]
        self.assertIn("maximum context length", msg.lower())
        self.assertIn("262000", msg)
        self.assertEqual(row["error"].get("code"), "context_length_exceeded")

    def test_the_refusal_speaks_the_dialect_qwen_can_recover_from(self):
        """The exact patterns from the installed bundle, applied to our text."""
        import re
        self.start_gated(gate=262000)
        _, payload = self.post({"model": "m", "max_tokens": 20000,
                                "messages": [{"role": "user",
                                              "content": "y" * 1200000}]})
        text = json.dumps(json.loads(payload))
        patterns = [r"\bcontext[_\s-]?length[_\s-]?exceeded\b",
                    r"\bmaximum context length\b"]
        self.assertTrue(any(re.search(p, text, re.I) for p in patterns), text[:300])
        timeouts = [r"\btimed?\s*out\b", r"\btimeout\b"]
        self.assertFalse(any(re.search(p, text, re.I) for p in timeouts),
                         "a timeout word would disqualify the overflow class")
        # parseTokenCounts' OpenAI branch: "maximum context length is N tokens
        # ... requested M tokens" — both numbers, so reactive compression gets
        # actualTokens and limitTokens instead of guessing.
        m = re.search(r"maximum context length is\s*(\d[\d,]*)\s*tokens?"
                      r"[\s\S]*?(?:resulted in|requested|used)\s*(\d[\d,]*)\s*tokens?",
                      text, re.I)
        self.assertIsNotNone(m, "the numbers must be recoverable: " + text[:300])
        self.assertEqual(m.group(1), "262000")
        self.assertGreater(int(m.group(2).replace(",", "")), 262000)

    def test_the_refusal_is_recorded_as_its_own_class_not_as_a_call(self):
        self.start_gated(gate=262000)
        self.post({"model": "m", "max_tokens": 20000,
                   "messages": [{"role": "user", "content": "z" * 1200000}]})
        rows = self.rows()
        self.assertTrue(rows, "the refusal must be recorded")
        row = rows[-1]
        self.assertEqual(row.get("event"), "pre_send_refused",
                         "an event, so no accounting reads it as a billed call")
        self.assertIsNone(row.get("returned_model"),
                          "no model answered, so none may be named")
        self.assertEqual(row.get("request_max_tokens"), 20000)
        self.assertGreaterEqual(row.get("estimated_prompt_tokens", 0), 200000)
        self.assertEqual(row.get("per_request_token_gate"), 262000)

    def test_zero_declares_no_gate_and_changes_nothing(self):
        self.start_gated(gate=0)
        code, _ = self.post({"model": "m", "max_tokens": 20000,
                             "messages": [{"role": "user",
                                           "content": "q" * 1200000}]})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.srv.seen), 1,
                         "with no gate declared the proxy must not judge")

    def test_the_default_divisor_is_at_or_below_the_measured_minimum(self):
        """THE SAFETY DIRECTION, pinned against real data rather than taste.

        UPDATED BY FIX 6 (v42). The old bound here was 3.441 — a BYTE ratio
        (`request_bytes / usage.prompt_tokens`), while the estimator divides
        CHARACTERS. Replaying the complete ledger of run 20260827T173511Z-v41 (337
        answered calls) showed the character ratio bottoming out at 3.2898, so the
        shipped 3.40 under-estimated 42 of 337 calls, worst deficit 6,434 tokens.
        The divisor is now 3.10 and this test pins it at or below the observed
        CHARACTER minimum.

        A divisor at or BELOW the observed minimum over-estimates the token count,
        so the wall can refuse a legal request but cannot pass an illegal one. A
        divisor above it would be a wall with a hole. This test fails if anyone
        ever raises the default past the measured floor, which is the only way this
        gate can silently stop being one.
        """
        import re
        src = open(base.PROXY, encoding="utf-8").read()
        m = re.search(r'UPSTREAM_CHARS_PER_TOKEN\s*=\s*_calibrated_chars_per_token\(\s*\n?\s*'
                      r'os\.environ\.get\("UPSTREAM_CHARS_PER_TOKEN",\s*'
                      r'"([0-9.]+)"', src)
        self.assertIsNotNone(m, "the default divisor is not where it was")
        self.assertLessEqual(
            float(m.group(1)), 3.2898,
            "a divisor above the measured minimum character ratio (3.2898) "
            "under-estimates the prompt, which turns this wall into a suggestion")

    def test_the_estimate_is_conservative_not_optimistic(self):
        """A gate that under-counts is not a wall.

        The estimate divides the WHOLE serialised request by chars-per-token, so
        it must never come out below the provider's own count for the same body.
        Pinned by forcing a deliberately pessimistic ratio: at 1 char per token
        even a small body is refused, which proves the knob is live and that the
        direction of the inequality is the safe one.
        """
        self.start_gated(gate=1000, chars_per_token=1)
        code, payload = self.post({"model": "m", "max_tokens": 10,
                                   "messages": [{"role": "user",
                                                 "content": "a" * 2000}]})
        self.assertEqual(code, 400, payload[:200])
        self.assertEqual(self.srv.seen, [])

    def test_the_tool_schemas_count_toward_the_estimate(self):
        """MEASURED on the v40 paid run: the tool schemas alone were 113,061
        characters of the peak request. An estimate that ignored them would miss
        a third of the prompt."""
        self.start_gated(gate=262000)
        schema = {"type": "function",
                  "function": {"name": "t", "description": "d" * 700000,
                               "parameters": {"type": "object"}}}
        code, payload = self.post({"model": "m", "max_tokens": 20000,
                                   "messages": [{"role": "user", "content": "hi"}],
                                   "tools": [schema, schema]})
        self.assertEqual(code, 400, payload[:200])
        self.assertEqual(self.srv.seen, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
