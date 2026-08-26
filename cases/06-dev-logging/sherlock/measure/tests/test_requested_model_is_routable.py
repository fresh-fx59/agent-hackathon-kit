#!/usr/bin/env python3
"""The default requested model must be an id the provider actually LISTS.

WHY THIS TEST EXISTS — read it before you "fix" the model id again.

PR #77 replaced the floating alias `[SP]deepseek-v4-flash` with the dated
snapshot `[SP]deepseek-v4-flash-0731` in all nine request-path scripts, on the
theory that linkapi was silently substituting `deepseek-v4-pro-0813` and
splitting the prompt cache across two pools. The substitution was real. The fix
was not: the pinned launch (v38, 2026-08-26) died on its first call —

    13 calls, ALL HTTP 503, zero billed usage. The upstream ledger:
    "sent_model": "[SP]deepseek-v4-flash-0731", "status": 503, "usage": null,
    "upstream_error": "{\\"error\\":{\\"code\\":\\"model_not_found\\",\\"message\\":
       \\"No available channel for model [SP]deepseek-v4-flash-0731 under
        group auto (distributor)\\"}}"

`GET https://linkapi.ai/v1/models` then returned 130 models, of which EXACTLY
FOUR contain `deepseek-v4`:

    [SP]deepseek-v4-flash    [SP]deepseek-v4-pro
    [次]deepseek-v4-flash    [次]deepseek-v4-pro

There is no routable dated id at this provider. `deepseek-v4-flash-0731` is a
value the provider RETURNS in the response body; it can never be SENT.

A test cannot call the API (no network, no key, and a metered call is exactly
what this must save). So the invariant is encoded structurally: no default
requested model may carry a dated/pinned release suffix. If a provider ever
does list a dated id, widen ROUTABLE below — deliberately, with the /v1/models
output that proves it, not by deleting the check.

The defence against a provider that substitutes anyway lives on the RETURNED
side, in measure/lane_guard.py. It is the only defence there is.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.dirname(HERE)                    # .../sherlock/measure
CASE = os.path.dirname(CASE)                    # .../sherlock

# Every script that puts a model id on the wire, plus the health probe whose
# receipt run-manifest.py:validate_health cross-checks against the runner's
# requested model (E_HEALTH_IDENTITY_MISMATCH) — so it must agree with them.
REQUEST_PATH = [
    "eval/bench/run-bench.sh",
    "measure/run-case.sh",
    "eval/run.sh",
    "measure/one-defect.sh",
    "eval/petstore/run-tc.sh",
    "knowledge/measure/run-kb.sh",
    "acceptance/r1-zero-config.sh",
    "acceptance/skill-loads.sh",
    "measure/probes/lane-health.sh",
    "measure/probes/upstream-split.sh",
]

# The four ids linkapi lists that contain `deepseek-v4`, verbatim from
# GET https://linkapi.ai/v1/models on 2026-08-26.
ROUTABLE = {
    "[SP]deepseek-v4-flash",
    "[SP]deepseek-v4-pro",
    "[次]deepseek-v4-flash",
    "[次]deepseek-v4-pro",
}

# A trailing release stamp: -0731, -20260731, -2026-07-31, -latest, -preview.
DATED = re.compile(r"-(?:\d{3,8}|\d{4}-\d{2}-\d{2}|latest|preview)$")

# `SHERLOCK_MODEL` given a default, in any of the shapes the scripts use:
#   MODEL="${SHERLOCK_MODEL:-...}"      --model "${SHERLOCK_MODEL:-...}"
DEFAULTED = re.compile(r"\$\{SHERLOCK_MODEL:-([^}]*)\}")
# …and the launcher-style literal assignment `SHERLOCK_MODEL='...'`.
LITERAL = re.compile(r"^\s*SHERLOCK_MODEL=(['\"])(.*?)\1", re.M)


def defaults_in(rel):
    with open(os.path.join(CASE, rel), encoding="utf-8") as fh:
        body = fh.read()
    # Comments explain the 503 on purpose; they are not on the wire.
    code = "\n".join(l for l in body.split("\n")
                     if not l.lstrip().startswith("#"))
    return (DEFAULTED.findall(code)
            + [m.group(2) for m in LITERAL.finditer(code)])


class TheDefaultRequestedModelIsRoutable(unittest.TestCase):

    def test_every_request_path_script_names_a_model(self):
        """A silent rename would make the rest of this file vacuous."""
        for rel in REQUEST_PATH:
            with self.subTest(rel):
                self.assertTrue(defaults_in(rel),
                                "no SHERLOCK_MODEL default found in %s — did it "
                                "move? this test cannot guard what it cannot see"
                                % rel)

    def test_no_default_carries_a_dated_release_suffix(self):
        """The 503 above. `-0731` is a response value, never a request id."""
        for rel in REQUEST_PATH:
            for got in defaults_in(rel):
                with self.subTest(rel=rel, model=got):
                    self.assertIsNone(
                        DATED.search(got),
                        "%s defaults to %r, which carries a dated release "
                        "suffix. linkapi answers such a request with HTTP 503 "
                        "model_not_found (v38, 2026-08-26: 13/13 calls, zero "
                        "billed usage); /v1/models lists only %s. Send the "
                        "alias and let measure/lane_guard.py catch a "
                        "substitution on the returned side."
                        % (rel, got, ", ".join(sorted(ROUTABLE))))

    def test_every_deepseek_default_is_an_id_the_provider_lists(self):
        for rel in REQUEST_PATH:
            for got in defaults_in(rel):
                if "deepseek" not in got.lower():
                    continue
                with self.subTest(rel=rel, model=got):
                    self.assertIn(got, ROUTABLE,
                                  "%s defaults to %r, which is not one of the "
                                  "four deepseek-v4 ids GET /v1/models returned "
                                  "on 2026-08-26" % (rel, got))

    def test_the_health_probe_agrees_with_the_bench_runner(self):
        """run-manifest.py:validate_health cross-checks the receipt's model
        against the run's requested model. If these two drift, every run fails
        E_HEALTH_IDENTITY_MISMATCH — which is how the pin reached production
        without anyone noticing the probe had to move with it."""
        self.assertEqual(defaults_in("measure/probes/lane-health.sh"),
                         defaults_in("eval/bench/run-bench.sh"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
