#!/usr/bin/env python3
"""FIX 7: two constraints disagreed, the harness took the smaller one, and the
run clipped its own memory four times.

WHAT HAPPENED. Run 20260827T173511Z-v41 shipped `max_tokens: 6700`. Six of its
341 ledger rows ended `finish_reason=length`, every one of them at exactly 6,700
completion tokens. FOUR of the six were qwen COMPACTING OR SNAPSHOTTING ITSELF -
verified here against the run's own request bodies, not taken on trust:

    18:51:51  compaction      «You are the component that summarizes a
                               conversation when its context window is about to
                               overflow ... will become the agent's ONLY memory»
    18:52:46  state_snapshot  «produce the <state_snapshot> XML»
    18:54:26  state_snapshot  same
    18:55:36  compaction      same summariser prompt
    18:44:25  work            an ordinary turn after a tool result
    18:45:31  work            the same turn, retried

Every one is an HTTP 200 carrying a real answer and finish_reason=length, not
the gateway error chunk the lane's 90 s clock produces - row 274 ran 94,927 ms
and still returned a full 6,700-token completion. The provider's clock did not
cut them. The harness's own budget did.

WHY 6,700 EVER LAUNCHED - the root cause, and it is two independent failures:

  1. `eval/bench/run-bench.sh` NEVER CALLED `measure/corporate-settings.py`.
     Its only launch check was `lane_guard.generation_window_refusal`, which
     knows one constraint - "does the budget fit the 90 s window?" - and 6,700
     fits 6,743, so it said yes. `prove()`'s problems could not block a launch
     path that never asked it anything.
  2. And had it been asked, it would have PASSED. `prove()`'s reserve check was
     written `if max_tokens < SUMMARY_RESERVE and not generation_window_s`, and
     the `if generation_window_s:` branch above it put the reserve verdict in
     `lines` as a "CONSEQUENCE, STATED" note. Declaring a generation window
     therefore DISARMED the compaction check - the one check that exists to
     stop exactly this.

WHAT THIS FILE PINS. The conflict blocks instead of silently choosing; a lane
where both constraints CAN be satisfied still launches; a clipped completion is
named by the proxy while the run is alive and turned into a verdict by
lane_guard; the recorded six are recognised; and the 262,000 arithmetic proof
still holds under all of it.
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, MEASURE)

from lane_guard import (COMPACTION_OUTPUT_CLIPPED, COMPACTION_SUMMARY_RESERVE,
                        audit_ledger, clipped_compaction_breach,
                        compaction_request_class, completion_clipped,
                        fitting_max_output_tokens, last_message_text)
from test_declared_budgets import Rig                              # noqa: E402

SETTINGS = os.path.join(MEASURE, "corporate-settings.py")
FIXTURE = os.path.join(HERE, "fixtures", "v41-length-stops.json")
GATE = 262000
CR_WINDOW_S = "90"          # CloseRouter's own upstream generation timeout
CR_TOKENS_PER_S = "122.6"
# The window a 20,000-token compaction summary actually needs: 20000/122.6 =
# 163.1 s of generation plus the 35 s first-token reserve. 90 s is not it.
WIDE_WINDOW_S = "220"
DELIVERS = {"QWEN_STUB_REPORT": "# stub\n\napps/api.log:1 ok\n"}


def settings(args):
    p = subprocess.run([sys.executable, SETTINGS] + args, capture_output=True,
                       text=True)
    return p.returncode, p.stdout, p.stderr


def fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class TheConflictBlocksInsteadOfChoosingTheSmallerNumber(unittest.TestCase):

    def test_the_exact_v41_configuration_is_refused(self):
        rc, out, err = settings(["check-budget", "--window", str(GATE),
                                 "--max-tokens", "6700",
                                 "--generation-window-s", CR_WINDOW_S,
                                 "--session-token-limit", "0"])
        self.assertNotEqual(rc, 0, "the v41 budget was accepted again:\n" + out)
        self.assertIn("✗", out)
        for number in ("6743", "20000", "90"):
            self.assertIn(number, out, "the refusal never names %s" % number)

    def test_the_refusal_names_both_ways_out(self):
        _rc, out, _err = settings(["check-budget", "--window", str(GATE),
                                   "--max-tokens", "6700",
                                   "--generation-window-s", CR_WINDOW_S,
                                   "--session-token-limit", "0"])
        self.assertIn("198", out, "the refusal never says how long a window fits")
        self.assertIn("sessionTokenLimit", out,
                      "the refusal never names the other resolution")

    def test_prove_itself_no_longer_passes_the_v41_configuration(self):
        """THE REGRESSION THAT SHIPPED. Declaring a window used to disarm the
        compaction check; `prove` exited 0 on the configuration that died."""
        rc, out, _err = settings(["prove", "--gate", str(GATE),
                                  "--max-tokens", "6700",
                                  "--generation-window-s", CR_WINDOW_S])
        self.assertNotEqual(rc, 0,
                            "prove still passes 6,700 under a 90 s window:\n" + out)

    def test_prove_still_refuses_6700_with_no_window_at_all(self):
        rc, out, _err = settings(["prove", "--gate", str(GATE),
                                  "--max-tokens", "6700"])
        self.assertNotEqual(rc, 0, out)

    def test_a_lane_whose_window_admits_the_reserve_agrees(self):
        rc, out, _err = settings(["check-budget", "--window", str(GATE),
                                  "--max-tokens", str(COMPACTION_SUMMARY_RESERVE),
                                  "--generation-window-s", WIDE_WINDOW_S])
        self.assertEqual(rc, 0, out)
        self.assertIn("✓", out)

    def test_a_budget_over_the_window_is_still_refused(self):
        """The OTHER side must not be suppressed by the fix either."""
        rc, out, _err = settings(["check-budget", "--window", str(GATE),
                                  "--max-tokens", "32768",
                                  "--generation-window-s", WIDE_WINDOW_S])
        self.assertNotEqual(rc, 0, out)
        self.assertIn("cannot finish inside", out)

    def test_an_unreachable_compaction_resolves_the_conflict_by_fact(self):
        """The ONLY non-refusal, and it is a property of the settings.

        auto-compaction fires at 222,700 on a 262,000 window; a session limit of
        200,000 plus one whole 6,700-token budget is 206,700, so the session
        ends before qwen can compact and the reserve is never asked for."""
        rc, out, _err = settings(["check-budget", "--window", str(GATE),
                                  "--max-tokens", "6700",
                                  "--generation-window-s", CR_WINDOW_S,
                                  "--session-token-limit", "200000"])
        self.assertEqual(rc, 0, out)
        self.assertIn("RESOLVED, NOT SUPPRESSED", out)

    def test_a_session_limit_that_does_not_actually_prevent_compaction_fails(self):
        """216,001 + 6,700 is over the 222,700 auto threshold: still reachable."""
        rc, out, _err = settings(["check-budget", "--window", str(GATE),
                                  "--max-tokens", "6700",
                                  "--generation-window-s", CR_WINDOW_S,
                                  "--session-token-limit", "216001"])
        self.assertNotEqual(rc, 0, out)

    def test_the_262000_arithmetic_proof_still_holds(self):
        """FIX 4's ceiling is what makes any of this enforceable. Re-run whole."""
        rc, out, _err = settings(["prove", "--gate", str(GATE)])
        self.assertEqual(rc, 0, out)
        self.assertIn("248900", out, "the worst reachable request moved")
        self.assertIn("239000", out)
        self.assertIn("13100", out)
        self.assertIn("sessionTokenLimit", out)


class TheLauncherRefusesTheConflictBeforeSpendingMoney(Rig, unittest.TestCase):

    REFUSAL = "the generation window and the compaction reserve disagree"

    def test_the_v41_launch_is_now_blocked(self):
        argv, p = self.go(dict(DELIVERS,
                               SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S,
                               SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S,
                               SHERLOCK_MAX_OUTPUT_TOKENS="6700"))
        self.assertNotEqual(p.returncode, 0,
                            "the run that clipped four compactions launched again")
        self.assertEqual(argv, [], "qwen was launched anyway")
        self.assertIn(self.REFUSAL, p.stderr, p.stderr[-2000:])
        self.assertIn("20000", p.stderr)

    def test_a_lane_where_both_constraints_hold_still_launches(self):
        argv, p = self.go(dict(DELIVERS,
                               SHERLOCK_GENERATION_WINDOW_S=WIDE_WINDOW_S,
                               SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S,
                               SHERLOCK_MAX_OUTPUT_TOKENS="20000"))
        self.assertNotIn(self.REFUSAL, p.stderr)
        self.assertTrue(argv, "qwen was never launched:\n%s" % p.stderr[-2000:])
        window = self.inputs()["generation_window"]
        self.assertEqual(window["max_output_tokens"], 20000)

    def test_a_lane_with_no_window_is_untouched(self):
        """THE linkapi GUARANTEE, again: 32,768 and no window must still run."""
        argv, p = self.go(dict(DELIVERS, SHERLOCK_MAX_OUTPUT_TOKENS="32768"))
        self.assertNotIn(self.REFUSAL, p.stderr)
        self.assertTrue(argv, p.stderr[-2000:])

    def test_the_session_limit_that_resolves_the_conflict_REACHES_the_target(self):
        """OTHERWISE THE ESCAPE IS A BYPASS. The conflict check accepts a budget
        below the compaction reserve on one ground — that
        model.sessionTokenLimit ends the session before qwen can compact. That
        ground is a lie unless the key is actually written into the settings
        the target reads, so the launch and the settings must carry the same
        number from the same variable."""
        argv, p = self.go(dict(DELIVERS,
                               SHERLOCK_GENERATION_WINDOW_S=CR_WINDOW_S,
                               SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S,
                               SHERLOCK_MAX_OUTPUT_TOKENS="6700",
                               SHERLOCK_CONTEXT_WINDOW="262000",
                               SHERLOCK_SESSION_TOKEN_LIMIT="200000"))
        self.assertNotIn(self.REFUSAL, p.stderr, p.stderr[-1500:])
        self.assertTrue(argv, "qwen was never launched:\n%s" % p.stderr[-2000:])
        sealed = os.path.join(self.trace(), "qwen-settings-pre.json")
        with open(sealed, encoding="utf-8") as fh:
            settings = json.load(fh)
        self.assertEqual(settings["model"]["sessionTokenLimit"], 200000)
        self.assertEqual(
            settings["model"]["generationConfig"]["samplingParams"]["max_tokens"],
            6700)
        self.assertEqual(self.inputs()["output_budget"]["session_token_limit"],
                         200000)

    def test_no_session_limit_leaves_the_settings_exactly_as_they_were(self):
        self.go(dict(DELIVERS, SHERLOCK_MAX_OUTPUT_TOKENS="32768"))
        with open(os.path.join(self.trace(), "qwen-settings-pre.json"),
                  encoding="utf-8") as fh:
            settings = json.load(fh)
        self.assertNotIn("sessionTokenLimit", settings["model"])

    def test_the_conflict_is_legible_in_the_run_s_own_artifacts(self):
        self.go(dict(DELIVERS,
                     SHERLOCK_GENERATION_WINDOW_S=WIDE_WINDOW_S,
                     SHERLOCK_OUTPUT_TOKENS_PER_S=CR_TOKENS_PER_S,
                     SHERLOCK_MAX_OUTPUT_TOKENS="20000"))
        proof = os.path.join(self.trace(), "output-budget-proof.txt")
        self.assertTrue(os.path.exists(proof), "no proof written to the trace")
        with open(proof, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("CONSTRAINT 1", text)
        self.assertIn("CONSTRAINT 2", text)
        budget = self.inputs()["output_budget"]
        self.assertEqual(budget["compaction_summary_reserve"], 20000)
        self.assertEqual(budget["max_output_tokens"], 20000)
        self.assertEqual(budget["fitting_max_output_tokens"],
                         fitting_max_output_tokens(220, 122.6, 35))


class TheSixRecordedLengthStopsAreRecognised(unittest.TestCase):
    """Against the DISTILLED fixture, never the 430 KB ledger."""

    def setUp(self):
        self.doc = fixture()
        self.rows = self.doc["rows"]

    def test_the_fixture_is_the_run_it_claims_to_be(self):
        self.assertEqual(self.doc["_ledger_rows"], 341)
        self.assertEqual(self.doc["_finish_reasons"]["length"], 6)
        self.assertEqual(len(self.rows), 6)

    def test_every_one_of_the_six_is_seen_as_clipped(self):
        self.assertEqual(sum(1 for r in self.rows if completion_clipped(r)), 6)

    def test_each_was_cut_at_exactly_the_requested_budget(self):
        for row in self.rows:
            self.assertEqual(row["usage"]["completion_tokens"],
                             row["request_max_tokens"],
                             "row %s was not cut at the budget" % row["ts"])

    def test_the_classifier_finds_the_four_that_were_the_agent_s_memory(self):
        got = [compaction_request_class(r["last_message_head"])
               for r in self.rows]
        self.assertEqual(got, [r["expected_class"] for r in self.rows])
        self.assertEqual(got.count("compaction"), 2)
        self.assertEqual(got.count("state_snapshot"), 2)
        self.assertEqual(got.count("work"), 2)

    def test_the_two_work_turns_are_NOT_called_compactions(self):
        """A verdict that ends runs must not fire on an ordinary long answer."""
        work = [r for r in self.rows if r["expected_class"] == "work"]
        self.assertEqual(len(work), 2)
        for row in work:
            self.assertEqual(row["last_message_role"], "tool")

    def test_the_ledger_verdict_names_the_run_integrity_failure(self):
        rows = [dict(r, clipped_request_class=r["expected_class"])
                for r in self.rows]
        verdict = clipped_compaction_breach(rows)
        self.assertIsNotNone(verdict, "four clipped compactions passed the audit")
        self.assertEqual(verdict[0], COMPACTION_OUTPUT_CLIPPED)
        self.assertIn("4 of this run", verdict[1])
        self.assertIn("20000", verdict[1])

    def test_a_clean_run_produces_no_verdict(self):
        self.assertIsNone(clipped_compaction_breach(
            [{"finish_reason": "tool_calls"}, {"finish_reason": "stop"}]))

    def test_a_clipped_ORDINARY_turn_is_counted_but_not_a_breach(self):
        self.assertIsNone(clipped_compaction_breach(
            [{"finish_reason": "length", "clipped_request_class": "work"}]))

    def test_an_unstamped_length_row_cannot_be_judged_here(self):
        """A pre-fix-7 ledger has no class. It must not be guessed at."""
        self.assertIsNone(clipped_compaction_breach([{"finish_reason": "length"}]))


class TheAuditCountsAndJudgesThem(unittest.TestCase):

    def ledger(self, rows):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    def base_row(self, **over):
        row = {"returned_model": "DeepSeek-V4-Flash",
               "requested_model": "DeepSeek-V4-Flash", "status": 200,
               "request_bytes": 100, "attempt": 1,
               "usage": {"prompt_tokens": 1000, "completion_tokens": 10,
                         "prompt_tokens_details": {"cached_tokens": 900}},
               "finish_reason": "tool_calls"}
        row.update(over)
        return row

    def test_the_six_are_counted_from_finish_reason_alone(self):
        """What makes a run recorded BEFORE fix 7 still re-readable."""
        doc = fixture()
        rows = [self.base_row() for _ in range(10)]
        for row in doc["rows"]:
            rows.append(self.base_row(
                finish_reason="length", ts=row["ts"],
                request_max_tokens=row["request_max_tokens"],
                usage={"prompt_tokens": row["usage"]["prompt_tokens"],
                       "completion_tokens": row["usage"]["completion_tokens"],
                       "prompt_tokens_details": {"cached_tokens": 0}}))
        summary = {}
        audit_ledger(self.ledger(rows), expected_identity="DeepSeek-V4-Flash",
                     cache_guard=False, summary=summary,
                     compaction_clip_guard=False)
        self.assertEqual(summary["length_stop_calls"], 6)
        self.assertEqual(summary["clipped_compaction_calls"], 0,
                         "an unstamped row was guessed to be a compaction")
        self.assertEqual(len(summary["length_stop_detail"]), 6)
        self.assertEqual(
            [d["completion_tokens"] for d in summary["length_stop_detail"]],
            [r["usage"]["completion_tokens"] for r in doc["rows"]])

    def test_a_stamped_compaction_clip_fails_the_audit(self):
        doc = fixture()
        rows = [self.base_row() for _ in range(5)]
        for row in doc["rows"]:
            rows.append(self.base_row(
                finish_reason="length", ts=row["ts"],
                clipped_request_class=row["expected_class"],
                request_max_tokens=row["request_max_tokens"],
                usage={"prompt_tokens": row["usage"]["prompt_tokens"],
                       "completion_tokens": row["usage"]["completion_tokens"],
                       "prompt_tokens_details": {"cached_tokens": 0}}))
        summary = {}
        verdict = audit_ledger(self.ledger(rows),
                              expected_identity="DeepSeek-V4-Flash",
                              cache_guard=False, summary=summary)
        self.assertIsNotNone(verdict, "the v41 shape passed the audit again")
        self.assertEqual(verdict[0], COMPACTION_OUTPUT_CLIPPED)
        self.assertEqual(summary["length_stop_calls"], 6)
        self.assertEqual(summary["clipped_compaction_calls"], 4)

    def test_a_clean_ledger_still_passes(self):
        summary = {}
        verdict = audit_ledger(self.ledger([self.base_row() for _ in range(5)]),
                               expected_identity="DeepSeek-V4-Flash",
                               cache_guard=False, summary=summary)
        self.assertIsNone(verdict, verdict)
        self.assertEqual(summary["length_stop_calls"], 0)


class TheClassifierReadsARealRequestBody(unittest.TestCase):
    """`last_message_text` must survive both content shapes and every garbage."""

    def test_a_string_content_body(self):
        body = json.dumps({"messages": [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "First, reason in your <analysis> block. "
                                        "Then, produce the <state_snapshot> XML."}]})
        self.assertEqual(compaction_request_class(last_message_text(body.encode())),
                         "state_snapshot")

    def test_a_parts_content_body(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "You are the component that summarizes a "
                                     "conversation when its context window is "
                                     "about to overflow."}]}]}
        self.assertEqual(compaction_request_class(last_message_text(body)),
                         "compaction")

    def test_garbage_is_work_not_a_breach(self):
        for junk in (None, b"", b"{", b"[]", {"messages": []},
                     {"messages": [{"content": None}]}, "not json"):
            self.assertEqual(compaction_request_class(last_message_text(junk)),
                             "work", repr(junk))

    def test_an_earlier_compaction_in_the_history_does_not_match(self):
        """Only the LAST message decides — a summariser prompt quoted earlier in
        the transcript would otherwise mark every later turn a compaction."""
        body = {"messages": [
            {"role": "user", "content": "You are the component that summarizes "
                                        "a conversation"},
            {"role": "tool", "content": "Command: ls\nok"}]}
        self.assertEqual(compaction_request_class(last_message_text(body)), "work")


import test_upstream_log_proxy as proxybase                        # noqa: E402

COMPACTION_PROMPT = (
    "You are the component that summarizes a conversation when its context "
    "window is about to overflow. The summary you produce will become the "
    "agent's ONLY memory of everything that happened before this point.")


class TheProxyNamesTheClipWhileTheRunIsAlive(proxybase.ProxyCase):
    """END TO END. The classification has to happen HERE, not in the gate.

    The proxy is the only component holding both halves at once: the REQUEST
    body says whether the turn was qwen compacting itself, and the response
    says whether it was cut. `interactive-drive.py` never sees either, and the
    acceptance gate reads the report file a day later — which is exactly how
    v41's four clipped compactions were found by a human replaying a ledger.
    """

    def post_last(self, text):
        return self.post({"model": "[SP]deepseek-v4-flash",
                          "messages": [{"role": "user", "content": "work"},
                                       {"role": "user", "content": text}]})

    def test_a_clipped_compaction_is_stamped_and_shouted(self):
        self.start("json_length")
        code, _body = self.post_last(COMPACTION_PROMPT)
        self.assertEqual(code, 200)
        row = self.lines(1)[-1]
        self.assertEqual(row["finish_reason"], "length")
        self.assertIs(row["completion_clipped"], True)
        self.assertEqual(row["clipped_request_class"], "compaction")

    def test_a_clipped_state_snapshot_is_stamped(self):
        self.start("json_length")
        self.post_last("First, reason in your <analysis> block. Then, produce "
                       "the <state_snapshot> XML.")
        self.assertEqual(self.lines(1)[-1]["clipped_request_class"],
                         "state_snapshot")

    def test_a_clipped_ORDINARY_turn_is_stamped_work(self):
        self.start("json_length")
        self.post_last("Command: ls -la\nok")
        row = self.lines(1)[-1]
        self.assertIs(row["completion_clipped"], True)
        self.assertEqual(row["clipped_request_class"], "work")

    def test_a_clean_row_keeps_the_exact_ledger_shape_it_had(self):
        """A new key must not appear unbidden on a run that was never clipped."""
        self.start("json_toolcall")
        self.post()
        row = self.lines(1)[-1]
        self.assertNotIn("completion_clipped", row)
        self.assertNotIn("clipped_request_class", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
