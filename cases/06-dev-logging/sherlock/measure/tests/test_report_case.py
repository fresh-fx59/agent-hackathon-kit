#!/usr/bin/env python3
"""Tests for report-case.py — the ONLY thing that writes the measurement artifact.

test_gate.py stubs this file out entirely (it is testing gate.sh's orchestration),
so until now nothing exercised the row that actually gets quoted. That is how a live
row came to report files_opened=3 for a case whose corpus held one log file.

No network and no metered judge: SHERLOCK_JUDGE_STUB feeds report-case.py the judge's
JSON from a file, and every row it produces is stamped judge_stub:true so a stubbed
number can never be mistaken for a measured one.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
REPORTER = os.path.join(MEASURE, "report-case.py")
FIXTURE = os.path.join(HERE, "fixtures", "real-stream-excerpt.jsonl")

REPORT_BODY = ("## Что произошло\nNPE в PromoCodeResolver, checkout-api.log:3-6.\n"
               "## Корневая причина\nPromoCode.normalized() возвращает null.\n") * 20

# The fixture was captured on 2026-07-30, under the PRE-SPLIT layout where the corpus
# files sat directly in the case dir next to case.json. Its absolute paths are
# therefore that machine's, and they are baked into the recorded tool calls.
#
# Coverage is a question about CONTAINMENT — is the directory that was scanned an
# ancestor of the proof? — so a stream whose paths point at a different case dir than
# the one under measurement is not a realistic run, and testing against it measures
# the mismatch instead of the behaviour. Rebasing onto the temp case dir's corpus/ is
# what a real run of this case actually looks like.
CAPTURED_ROOT = ("/home/claude-developer/hack/agent-hackathon-kit/cases/06-dev-logging"
                 "/sherlock/measure/cases/cap-multiline-stitching")

# `meta=NORMAL` builds the meta.json a completed run leaves behind. Anything else is a
# way for a test to say what went wrong with it, and `None` is a real option (no file
# at all), so the default cannot be None.
NORMAL = object()


class Harness(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="report-case-test-")

    def build(self, report=REPORT_BODY, kind="capability_micro", stream=None,
              judge='{"found": true, "why": "identifies the NPE"}',
              model="[SP]deepseek-v4-flash", meta=NORMAL):
        case_dir = os.path.join(self.d, "cases", "cap-multiline-stitching")
        os.makedirs(os.path.join(case_dir, "corpus"), exist_ok=True)
        with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "cap-multiline-stitching", "kind": kind,
                       "title": "NPE hidden in a stack trace", "root_cause": "null promo",
                       "requires": "multiline stitching", "files": ["checkout-api.log"],
                       "proof_locations": [{"file": "checkout-api.log", "line_start": 3,
                                            "line_end": 6, "note": "the NPE"}]}, fh)
        run_dir = os.path.join(self.d, "runs", "r1")
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(report)
        # The full shape run-case.sh actually writes, cost fields included — a fixture
        # that omits them cannot catch a reporter that drops them.
        normal = {"case_id": "cap-multiline-stitching", "arm": "v6", "model": model,
                  "started_at": "20260731T073237Z", "duration_s": 78, "exit_code": 0,
                  "input_tokens": 129801, "output_tokens": 2261,
                  "answer_chars": len(report), "turns": 4}
        # meta=NORMAL -> that record; a dict is merged over it (a key set to None
        # models a provider whose final record carried no usage); a str is written
        # verbatim (malformed JSON); None writes no meta.json at all.
        if meta is None:
            body_meta = None
        elif meta is NORMAL:
            body_meta = json.dumps(normal, ensure_ascii=False)
        elif isinstance(meta, str):
            body_meta = meta
        else:
            body_meta = json.dumps(dict(normal, **meta), ensure_ascii=False)
        if body_meta is not None:
            with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as fh:
                fh.write(body_meta)
        # The stream is the REAL captured one unless a test supplies its own. Either
        # way it is rebased onto THIS case's corpus, so containment is asked of the
        # case actually under measurement. Tests that build a stream from scratch
        # carry no captured prefix, so for them the rebase is a no-op.
        body = stream if stream is not None else open(FIXTURE, encoding="utf-8").read()
        body = body.replace(CAPTURED_ROOT, os.path.join(case_dir, "corpus"))
        with open(os.path.join(run_dir, "stream.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(body)
        judge_path = os.path.join(self.d, "judge.json")
        with open(judge_path, "w", encoding="utf-8") as fh:
            fh.write(judge)
        return case_dir, run_dir, judge_path

    def run_reporter(self, case_dir, run_dir, judge_path, tier="0"):
        results = os.path.join(self.d, "results.jsonl")
        env = dict(os.environ)
        env["SHERLOCK_JUDGE_STUB"] = judge_path
        # No JUDGE_API_KEY: a real call would raise, so reaching the network fails loudly.
        env.pop("JUDGE_API_KEY", None)
        p = subprocess.run([sys.executable, REPORTER, "--case", case_dir, "--run", run_dir,
                            "--tier", tier, "--results", results],
                           capture_output=True, text=True, env=env, timeout=60)
        rows = []
        if os.path.exists(results):
            with open(results, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        return p, rows


class EveryFieldOfTheEmittedRow(Harness):
    def test_the_row_is_exactly_what_a_real_run_should_produce(self):
        p, rows = self.run_reporter(*self.build())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["case_id"], "cap-multiline-stitching")
        self.assertEqual(row["arm"], "v6")
        self.assertEqual(row["tier"], "0")
        self.assertEqual(row["diagnosis"], "ok")
        self.assertIs(row["judge_found"], True)
        self.assertEqual(row["why"], "identifies the NPE")
        self.assertEqual(row["requires"], "multiline stitching")
        # Important-8: the real stream touches a directory, case.json and ONE log file.
        self.assertEqual(row["files_opened"], 1)
        self.assertEqual(row["proofs_reached"], 1)
        self.assertEqual(row["reach_verdict"], "reached")
        self.assertEqual(row["proofs_unknown"], [])
        self.assertIsNone(row["collapse_reason"])
        self.assertEqual(row["report_chars"], len(REPORT_BODY))
        # 4 tool_use blocks in the excerpt: list_directory, read_file(case.json),
        # run_shell_command(wc -l), read_file(checkout-api.log).
        self.assertEqual(row["tool_calls"], 4)
        self.assertTrue(row["run_dir"].endswith("runs/r1"))
        self.assertIs(row["judge_stub"], True)
        self.assertIn("JUDGE STUB ACTIVE", p.stdout)

    def test_a_judge_miss_on_a_read_proof_is_a_reasoning_row(self):
        p, rows = self.run_reporter(*self.build(judge='{"found": false, "why": "no"}'))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["diagnosis"], "reasoning")
        self.assertEqual(rows[0]["proofs_reached"], 1)

    def test_a_dir_scan_only_run_is_inconclusive_never_coverage(self):
        only_dir = "\n".join(l for l in open(FIXTURE, encoding="utf-8").read().splitlines()
                             if "list_directory" in l)
        p, rows = self.run_reporter(*self.build(stream=only_dir + "\n",
                                                judge='{"found": false, "why": "no"}'))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["reach_verdict"], "unknown")
        self.assertEqual(rows[0]["diagnosis"], "inconclusive")
        self.assertEqual(rows[0]["files_opened"], 0)


class CoverageMustBeReachableFromTheReporter(Harness):
    """The containment fix in measure.py is worthless unless report-case.py hands it
    the corpus root. This is the wiring test: without it the parameter defaults to
    None, every run degrades to "cannot exclude", and `coverage` — the whole point of
    the module — can never appear in the artifact. `logstat.py` shipping nowhere is
    the same failure mode; assert the plumbing, not just the function."""

    def scan_of(self, path):
        return json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "call_x", "name": "list_directory",
             "input": {"path": path}}]}}, ensure_ascii=False) + "\n"

    def test_a_scan_of_an_unrelated_directory_is_a_coverage_row(self):
        case_dir, run_dir, judge = self.build(
            stream=self.scan_of("/var/log/somewhere-else"),
            judge='{"found": false, "why": "no"}')
        p, rows = self.run_reporter(case_dir, run_dir, judge)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["reach_verdict"], "not_reached")
        self.assertEqual(rows[0]["diagnosis"], "coverage")

    def test_a_scan_of_the_corpus_itself_stays_inconclusive(self):
        case_dir, run_dir, judge = self.build(judge='{"found": false, "why": "no"}')
        case_dir2, run_dir2, judge2 = case_dir, run_dir, judge
        with open(os.path.join(run_dir2, "stream.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(self.scan_of(os.path.join(case_dir2, "corpus")))
        p, rows = self.run_reporter(case_dir2, run_dir2, judge2)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["reach_verdict"], "unknown")
        self.assertEqual(rows[0]["diagnosis"], "inconclusive")


class TheRowNamesTheModelThatProducedIt(Harness):
    """A row that does not name its model cannot be compared to anything.

    On 2026-07-31 the provider under test changed mid-project: linkapi began
    returning 400 on every call and the same deepseek-v4-flash was reached through
    CloseRouter instead. `meta.json` recorded the model all along, but the emitted
    row dropped it — so rows from two different providers sat in one ledger,
    indistinguishable. That is the same defect already fixed for the JUDGE in
    score.py (`judge_model`), one layer down: identical numbers from different
    engines silently averaged together."""

    def test_the_row_carries_the_model_from_meta(self):
        p, rows = self.run_reporter(*self.build(model="[SP]deepseek-v4-flash"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["model"], "[SP]deepseek-v4-flash")

    def test_a_different_provider_is_visible_in_the_row(self):
        p, rows = self.run_reporter(*self.build(model="closerouter/cr-deepseek-v4-flash"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["model"], "closerouter/cr-deepseek-v4-flash",
                         "two providers in one ledger must never look identical")


class TheRowRecordsTheConditionsTheArmRanUnder(Harness):
    """Two rows of the same arm are only comparable if the arm ran the same way.

    `skill_delivery` ("named" vs "tool-only") says whether the prompt named the skill,
    and `subagent_available` says whether the `agent` tool was on the CLI. Both are
    ARM CONDITIONS, not decoration: on 2026-08-02 taking the `agent` tool away
    converted D11 and D01 from base-model greens into mechanism greens on the first
    attempt. run-case.sh has written both into meta.json all along and the emitted row
    dropped both — so rows produced with fan-out ON and fan-out OFF sit in one ledger
    looking identical, which is exactly the defect already fixed for `model`.

    Same rule as cost: unrecorded is null, never a guessed default. Back-filling an
    absent `skill_delivery` with "tool-only" invents a measurement of a run nobody
    observed, and `subagent_available: False` would claim fan-out was off on the rows
    where it was on.
    """

    def test_the_row_carries_the_arm_conditions_from_meta(self):
        p, rows = self.run_reporter(*self.build(
            meta={"skill_delivery": "named", "subagent_available": False}))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["skill_delivery"], "named")
        self.assertIs(rows[0]["subagent_available"], False)

    def test_fan_out_on_is_visible_in_the_row(self):
        p, rows = self.run_reporter(*self.build(
            meta={"skill_delivery": "tool-only", "subagent_available": True}))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["skill_delivery"], "tool-only")
        self.assertIs(rows[0]["subagent_available"], True,
                      "a fan-out run must never look like a fan-out-free one")

    def test_conditions_absent_from_meta_are_null_not_a_default(self):
        p, rows = self.run_reporter(*self.build())  # NORMAL meta predates both keys
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIsNone(rows[0]["skill_delivery"])
        self.assertIsNone(rows[0]["subagent_available"])

    def test_a_non_boolean_subagent_flag_is_null_not_truthy(self):
        """`"false"` is a true string. Coercing it would report fan-out ON for a run
        that had it OFF — the one direction that turns a mechanism green back into an
        unattributable one."""
        p, rows = self.run_reporter(*self.build(meta={"subagent_available": "false"}))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIsNone(rows[0]["subagent_available"])


class TheRowCarriesWhatTheRunCost(Harness):
    """Quality with no price attached is half a measurement.

    run-case.sh has captured duration_s / input_tokens / output_tokens / turns into
    meta.json since the rig was built, but the four numbers stopped at the run dir and
    never reached results.jsonl — so nine scored rows compared arms with no idea that
    one of them spent 6.0M input tokens and 20 minutes to produce a 530-char report.

    The rule these tests exist to hold: an unmeasured cost is null, NEVER 0. `0`
    tokens is a real observation, "no usage block in the final record" is not, and
    once they are the same value no average can tell them apart again.
    """

    COST = ("duration_s", "input_tokens", "output_tokens", "turns")

    def test_the_row_carries_the_cost_from_meta(self):
        p, rows = self.run_reporter(*self.build())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["duration_s"], 78)
        self.assertEqual(rows[0]["input_tokens"], 129801)
        self.assertEqual(rows[0]["output_tokens"], 2261)
        self.assertEqual(rows[0]["turns"], 4)

    def test_a_missing_meta_nulls_the_cost_and_still_writes_the_row(self):
        """meta.json is written LAST by run-case.sh. A run that died after the report
        has one and not the other, and the judged verdict is still real — losing that
        row to a KeyError would throw away the expensive half over the cheap half."""
        p, rows = self.run_reporter(*self.build(meta=None))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(rows), 1, "the row must survive a missing meta.json")
        self.assertEqual(rows[0]["diagnosis"], "ok")
        for k in self.COST:
            self.assertIsNone(rows[0][k], k)
        self.assertIn("unreadable meta.json", p.stdout, "silence would hide the gap")

    def test_a_malformed_meta_nulls_the_cost_and_still_writes_the_row(self):
        p, rows = self.run_reporter(*self.build(meta="{oops, not JSON"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(rows), 1)
        for k in self.COST:
            self.assertIsNone(rows[0][k], k)
        self.assertIn("unreadable meta.json", p.stdout)

    def test_a_null_token_field_stays_null_and_never_becomes_zero(self):
        """Some providers return a final record with no `usage`, so run-case.sh writes
        `input_tokens: null` — a normal outcome, not corruption. It must arrive in the
        ledger as null while the fields that WERE measured keep their real values."""
        p, rows = self.run_reporter(*self.build(meta={"input_tokens": None,
                                                      "turns": None}))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIsNone(rows[0]["input_tokens"])
        self.assertIsNone(rows[0]["turns"])
        self.assertEqual(rows[0]["duration_s"], 78, "one absence must not null the rest")
        self.assertEqual(rows[0]["output_tokens"], 2261)

    def test_a_zero_cost_is_kept_as_zero(self):
        """The other direction of the same rule: a measured 0 is data. If `or None`
        ever creeps in here, a genuinely free run becomes an unmeasured one."""
        p, rows = self.run_reporter(*self.build(meta={"output_tokens": 0,
                                                      "duration_s": 0}))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["output_tokens"], 0)
        self.assertEqual(rows[0]["duration_s"], 0)

    def test_a_non_numeric_cost_is_null_not_the_garbage(self):
        p, rows = self.run_reporter(*self.build(meta={"turns": "many",
                                                      "input_tokens": True}))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIsNone(rows[0]["turns"])
        self.assertIsNone(rows[0]["input_tokens"], "True must not arrive as 1 token")


class TheJudgeIsSkippedWhenThereIsNothingToJudge(Harness):
    """Important-10: the deterministic collapse check is free, the judge is metered."""

    def test_a_collapsed_report_is_diagnosed_without_calling_the_judge(self):
        # The stub file is deliberately NOT valid judge JSON: if the judge is called
        # at all, parse_verdict raises and the run fails loudly.
        case_dir, run_dir, judge = self.build(
            report="Отчёт выше уже содержит все находки.", judge="THIS IS NOT JSON")
        p, rows = self.run_reporter(case_dir, run_dir, judge)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["diagnosis"], "collapse")
        self.assertIs(rows[0]["judge_found"], False)
        self.assertIn("judge skipped", rows[0]["why"])
        self.assertIn("banned phrase", rows[0]["collapse_reason"])

    def test_a_short_micro_report_still_reaches_the_judge(self):
        """The other half of Important-7, at the artifact layer: 700 chars is under
        the full-corpus floor of 2000 but over the micro floor, so it must be judged,
        not written off as a collapse."""
        case_dir, run_dir, judge = self.build(report="а" * 700, kind="capability_micro")
        p, rows = self.run_reporter(case_dir, run_dir, judge)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["diagnosis"], "ok")
        self.assertIsNone(rows[0]["collapse_reason"])

    def test_the_same_report_on_a_defect_slice_is_a_collapse(self):
        case_dir, run_dir, judge = self.build(report="а" * 700, kind="defect_slice",
                                              judge="THIS IS NOT JSON")
        p, rows = self.run_reporter(case_dir, run_dir, judge)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(rows[0]["diagnosis"], "collapse")


class TheCeilingMustBeVisibleInTheLedger(unittest.TestCase):
    """qwen-code refuses at 177,000 prompt tokens. D06 PASSES at 162,438 — 91 % of
    it — so every case runs on ~15k of margin and nothing in results.jsonl said
    so. Peak context and how much corpus was pulled had to be recomputed by hand
    from the trajectories every time the question came up."""

    def test_row_carries_peak_context_and_read_volume(self):
        with tempfile.TemporaryDirectory() as d:
            run = os.path.join(d, "run"); os.makedirs(run)
            with open(os.path.join(run, "stream.jsonl"), "w", encoding="utf-8") as fh:
                for it, lim in ((31412, None), (124432, 60), (162438, 20)):
                    msg = {"role": "assistant", "usage": {"input_tokens": it},
                           "content": ([{"type": "tool_use", "name": "read_file",
                                         "input": {"absolute_path": "/c/a.log",
                                                   "offset": 10, "limit": lim}}]
                                       if lim else [])}
                    fh.write(json.dumps({"type": "assistant", "message": msg}) + "\n")
            import importlib.util
            here = os.path.dirname(os.path.abspath(__file__))
            rc = os.path.join(os.path.dirname(here), "report-case.py")
            spec = importlib.util.spec_from_file_location("report_case_mod", rc)
            mod = importlib.util.module_from_spec(spec)
            sys.argv = ["report-case.py", "--case", d, "--run", run,
                        "--results", os.path.join(d, "r.jsonl")]
            try:
                spec.loader.exec_module(mod)
            except SystemExit:
                pass
            except Exception:
                pass
            got = mod.trajectory_facts(run)
            self.assertEqual(got["peak_input_tokens"], 162438)
            self.assertEqual(got["lines_read"], 80)
            self.assertEqual(got["read_calls"], 2)
            # Headroom is measured against the window the RUN ACTUALLY HAD, not a
            # constant. 177,000 stopped being the ceiling on 2026-08-02 — it was a
            # model-id parsing artifact — and a hardcoded number here would have
            # every future row quietly reporting headroom against a dead limit.
            self.assertEqual(got["ceiling_headroom"], 400000 - 162438)

    def test_the_row_counts_what_the_retries_re_uploaded(self):
        """The ledger's input_tokens now UNDER-REPORTS the bill.

        The proxy retries a burst transparently, so qwen-code never sees those
        attempts and never counts them. On D11 that was **15.82 MB re-uploaded
        across 51 retried calls** against a recorded 3,273,084 input tokens — the
        row understated real upload by roughly half. A cost axis that silently
        omits half the cost is worse than no cost axis. `unmeasured is null,
        never 0` cuts both ways: measured-and-dropped is the same lie.
        """
        import importlib.util
        with tempfile.TemporaryDirectory() as d:
            run = os.path.join(d, "run")
            os.makedirs(run)
            with open(os.path.join(run, "stream.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("")
            with open(os.path.join(run, "upstream.jsonl"), "w", encoding="utf-8") as fh:
                for attempt, status, nbytes in ((1, 400, 1000), (2, 400, 1000),
                                                (3, 200, 1000), (1, 200, 500)):
                    fh.write(json.dumps({"attempt": attempt, "status": status,
                                         "request_bytes": nbytes}) + "\n")
            spec = importlib.util.spec_from_file_location(
                "rc_mod3", os.path.join(MEASURE, "report-case.py"))
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except (SystemExit, Exception):
                pass
            got = mod.trajectory_facts(run)
            self.assertEqual(got["retry_calls"], 2, "retried attempts not counted")
            self.assertEqual(got["retry_bytes"], 2000)
            self.assertEqual(got["upstream_calls"], 4)

    def test_headroom_follows_the_configured_window(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rc_mod2", os.path.join(MEASURE, "report-case.py"))
        mod = importlib.util.module_from_spec(spec)
        old = os.environ.get("SHERLOCK_CONTEXT_WINDOW")
        os.environ["SHERLOCK_CONTEXT_WINDOW"] = "250000"
        try:
            try:
                spec.loader.exec_module(mod)
            except (SystemExit, Exception):
                pass
            self.assertEqual(mod.CONTEXT_CEILING, 250000)
        finally:
            if old is None:
                os.environ.pop("SHERLOCK_CONTEXT_WINDOW", None)
            else:
                os.environ["SHERLOCK_CONTEXT_WINDOW"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
