#!/usr/bin/env python3
"""FIX 6 (v42): make the pre-send estimator ACTUALLY conservative.

The estimator's docstring claimed a deliberately conservative estimate. The paid
run says otherwise. Replaying the complete ledger of run 20260827T173511Z-v41 —
341 rows, 337 ANSWERED calls, the whole population — against the provider's own
`usage.prompt_tokens`:

    under-estimates at the shipped 3.40 ... 42 of 337
    worst deficit ......................... 6,434 tokens (est 219,480, actual 225,914)
    implied true chars/token .............. min 3.2898  median 3.5480  max 5.2974

The root cause is a units error: 3.40 was calibrated from `request_bytes`, while
`estimate_prompt_tokens` divides CHARACTERS of the utf-8 decode. The corpus is
largely Cyrillic, so bytes run well above characters and the byte-derived divisor
is much too high for a character-fed estimator. The run never breached its 262,000
gate only because its peak prompt+budget was 236,678 — luck, not the wall.

THE FIXTURE. The real ledger is 430 KB and lives on the run host; it is NOT
vendored. `fixtures/v41-prompt-token-calibration.json` carries one distilled record
per answered call — `request_chars`, `request_bytes`, `estimated_at_340`,
`actual_prompt_tokens`, `request_max_tokens`. That is exactly the evidence the
claim needs: the estimator is a pure function of the character count, so the
character count plus the provider's count is the whole proof. `request_chars` is
reconstructed as round(estimated_at_340 * 3.40) and verified EXACT against the one
captured body of the peak call (bf1ee18eb50f4a3b9999047f03309f73.req.json.gz:
819,822 bytes, 760,553 characters; reconstruction 760,553).
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
PROXY = os.path.join(MEASURE, "upstream-log-proxy.py")
FIXTURE = os.path.join(HERE, "fixtures", "v41-prompt-token-calibration.json")


def load_proxy(env=None):
    """Import the hyphenated proxy module under a chosen environment."""
    import importlib.util
    saved = dict(os.environ)
    if env is not None:
        os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("proxy_under_test", PROXY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.clear()
        os.environ.update(saved)


CALLS = json.load(open(FIXTURE, encoding="utf-8"))


class RecordedLedgerReplay(unittest.TestCase):

    def setUp(self):
        self.proxy = load_proxy({"UPSTREAM_CHARS_PER_TOKEN": ""})

    def test_the_fixture_is_the_whole_answered_population(self):
        self.assertEqual(CALLS["ledger_rows"], 341)
        self.assertEqual(CALLS["answered_calls"], 337)
        self.assertEqual(len(CALLS["calls"]), 337)
        self.assertEqual(CALLS["source_run"], "20260827T173511Z-v41")

    def test_the_shipped_340_did_under_estimate_42_of_337(self):
        """The defect, still visible in the recorded numbers."""
        under = [c for c in CALLS["calls"]
                 if c["estimated_at_340"] < c["actual_prompt_tokens"]]
        self.assertEqual(len(under), 42)
        worst = max(c["actual_prompt_tokens"] - c["estimated_at_340"] for c in under)
        self.assertEqual(worst, 6434)

    def test_zero_under_estimates_across_every_answered_call(self):
        """THE CLAIM. Replayed through the real function, not a formula."""
        under = []
        surplus = []
        for c in CALLS["calls"]:
            body = b"x" * c["request_chars"]          # 1 byte == 1 char, ascii
            est = self.proxy.estimate_prompt_tokens(body)
            surplus.append(est - c["actual_prompt_tokens"])
            if est < c["actual_prompt_tokens"]:
                under.append((c, est))
        self.assertEqual(under, [], "the gate under-counted %d calls" % len(under))
        self.assertGreater(min(surplus), 0)

    def test_the_calibration_would_not_have_refused_the_run_it_came_from(self):
        """Conservative, not useless: peak estimate+budget stays under the gate."""
        peak = max(self.proxy.estimate_prompt_tokens(b"x" * c["request_chars"])
                   + (c["request_max_tokens"] or 0) for c in CALLS["calls"])
        self.assertLess(peak, 262000, "the new divisor would refuse legal calls")

    def test_the_divisor_is_below_the_observed_minimum(self):
        self.assertLessEqual(self.proxy.UPSTREAM_CHARS_PER_TOKEN,
                             self.proxy.UPSTREAM_CHARS_PER_TOKEN_OBSERVED_MIN)


class ScriptSensitivity(unittest.TestCase):
    """Cyrillic and ASCII tokenize differently; one divisor must cover both."""

    def setUp(self):
        self.proxy = load_proxy({"UPSTREAM_CHARS_PER_TOKEN": ""})

    def test_cyrillic_body_is_counted_in_characters_not_bytes(self):
        text = "Проверка журнала событий безопасности. " * 500
        body = json.dumps({"messages": [{"role": "user", "content": text}]},
                          ensure_ascii=False).encode("utf-8")
        chars = len(body.decode("utf-8"))
        self.assertGreater(len(body), chars, "the corpus really is 2-byte heavy")
        est = self.proxy.estimate_prompt_tokens(body)
        self.assertEqual(est, int(chars / self.proxy.UPSTREAM_CHARS_PER_TOKEN))

    def test_ascii_body_uses_the_same_divisor(self):
        body = json.dumps({"messages": [{"role": "user", "content": "a" * 20000}]}
                          ).encode("utf-8")
        est = self.proxy.estimate_prompt_tokens(body)
        self.assertEqual(est, int(len(body) / self.proxy.UPSTREAM_CHARS_PER_TOKEN))

    def test_the_same_text_estimates_higher_than_it_did_at_340(self):
        body = b"a" * 340000
        self.assertGreater(self.proxy.estimate_prompt_tokens(body), int(340000 / 3.40))


class UnsafeOverrideIsClamped(unittest.TestCase):

    def test_an_override_above_the_ceiling_is_clamped_and_announced(self):
        mod = load_proxy({"UPSTREAM_CHARS_PER_TOKEN": "4.0"})
        self.assertEqual(mod.UPSTREAM_CHARS_PER_TOKEN,
                         mod.UPSTREAM_CHARS_PER_TOKEN_CALIBRATED)

    def test_the_clamp_is_loud(self):
        """A silent clamp is a lie to the operator; it must reach stderr."""
        env = dict(os.environ, UPSTREAM_CHARS_PER_TOKEN="9.9",
                   UPSTREAM_PER_REQUEST_TOKEN_GATE="0", LISTEN_PORT="0")
        out = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util,sys;"
             "spec=importlib.util.spec_from_file_location('p', %r);"
             "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
             "print(m.UPSTREAM_CHARS_PER_TOKEN)" % PROXY],
            env=env, capture_output=True, text=True)
        self.assertIn("REFUSING UPSTREAM_CHARS_PER_TOKEN=9.9", out.stderr)
        self.assertEqual(out.stdout.strip(), "3.1")

    def test_a_more_conservative_override_is_honoured(self):
        mod = load_proxy({"UPSTREAM_CHARS_PER_TOKEN": "1"})
        self.assertEqual(mod.UPSTREAM_CHARS_PER_TOKEN, 1.0)

    def test_garbage_and_zero_fall_back_to_the_calibrated_value(self):
        for raw in ("", "0", "-2", "abc"):
            mod = load_proxy({"UPSTREAM_CHARS_PER_TOKEN": raw})
            self.assertEqual(mod.UPSTREAM_CHARS_PER_TOKEN,
                             mod.UPSTREAM_CHARS_PER_TOKEN_CALIBRATED, raw)


class DegenerateBodies(unittest.TestCase):

    def setUp(self):
        self.proxy = load_proxy({"UPSTREAM_CHARS_PER_TOKEN": ""})

    def test_empty_and_unparseable_bodies_return_without_exploding(self):
        for body in (b"", None, b"\xff\xfe\x00garbage", b"{not json"):
            est = self.proxy.estimate_prompt_tokens(body)
            self.assertIsInstance(est, int)
            self.assertGreaterEqual(est, 0)


class RefusalContractUnchanged(unittest.TestCase):
    """FIX 6 must not touch the wording qwen parses. Belt and braces beside
    test_pre_send_token_gate.py, which pins it against the bundle's regexes."""

    def test_the_dialect_survives(self):
        mod = load_proxy({"UPSTREAM_CHARS_PER_TOKEN": ""})
        text = mod.pre_send_refusal_text(262000, 260000, 6700)
        self.assertIn("maximum context length is 262000 tokens", text)
        self.assertIn("however you requested 266700 tokens", text)
        self.assertIn("(260000 in the messages, 6700 for the completion)", text)
        self.assertIn("[proxy pre-send gate: context_length_exceeded]", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
