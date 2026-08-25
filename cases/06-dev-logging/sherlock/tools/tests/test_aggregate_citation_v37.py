#!/usr/bin/env python3
"""v37 fix-3: an AGGREGATE-EVIDENCE citation, and the ways it must fail closed.

WHY, measured. The citation gate proves a quote is GENUINE; it never proved a
quote was READ CORRECTLY, and — the cost measured here — it only ever accepted a
SINGLE-LINE quote. A claim about a population has no single line to quote:
«93 distinct source IPs in Security.jsonl, 8 of them failing authentication more
than 1000 times each» is true, checkable, and was UNCITABLE. So the model
dropped it. Measured on the full winevtx corpus:

  * v36 (sherlock-winevtx-runs-v36-full-r1) FAILED its gates and listed 12
    attacker IPs.
  * v37 (sherlock-winevtx-runs-v37-full-r1/20260825T173021Z-v37) PASSED all
    three gates — citecheck/statecheck/triagecheck, exit 0, blocking 0 — and
    listed 4, out of 93 real distinct sources.
  * Never stated at all: SubStatus 0xc0000064 ×25355 («no such account», i.e.
    background spray) versus 0xc000006a ×8098 («account exists, wrong
    password», i.e. a target list). That difference is the investigation.

Passing the gate made the report worse. This suite pins the second form and,
more importantly, pins the ways it must REFUSE — because a gate whose cheapest
path to green is a fake number is worse than no gate at all.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
V37 = os.path.normpath(os.path.join(HERE, "..", "..", "skills", "v37", "tools"))
CITE = os.path.join(V37, "cite.py")
CITECHECK = os.path.join(V37, "citecheck.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = load("_agg_citecheck", CITECHECK)


# A miniature of the real corpus: same record shape, same field paths, three
# source IPs, one of them noisy. Small enough to assert exact counts on.
def _rec(ip, sub, eid=4625, user="ADMINI"):
    return json.dumps({"Event": {
        "System": {"EventID": eid, "Channel": "Security",
                   "TimeCreated": {"#attributes": {
                       "SystemTime": "2021-06-01T18:36:04.949933Z"}}},
        "EventData": {"IpAddress": ip, "SubStatus": sub,
                      "TargetUserName": user}}}, ensure_ascii=False)


def build_corpus(root):
    lines = []
    for _ in range(10):
        lines.append(_rec("45.168.116.98", "0xc0000064"))
    for _ in range(4):
        lines.append(_rec("91.220.163.170", "0xc000006a"))
    lines.append(_rec("-", "0xc0000064"))
    lines.append(_rec("10.0.0.5", "0xc000006a", eid=4624, user="root"))
    with open(os.path.join(root, "Security.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    # every value appears the same number of times: a threshold below that
    # selects ALL of them, which is a distinct() wearing a threshold.
    with open(os.path.join(root, "spray.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(_rec(ip, "0xc0000064")
                           for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3") * 3) + "\n")
    with open(os.path.join(root, "notes.log"), "w", encoding="utf-8") as fh:
        fh.write("boot ok\nsshd: accepted password for root\nboot ok\n")
    with open(os.path.join(root, "blob.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02binary\x00garbage\x00")
    return root
# 16 records total: 4 distinct IpAddress values ("-" included), of which
# 45.168.116.98 appears 10 times.


def cite_agg(root, path, pred):
    p = subprocess.run([sys.executable, CITE, "--corpus", root, "--file", path,
                        "--aggregate", pred], capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def grade(root, body):
    """Grade a report consisting of exactly one aggregate line."""
    d = cc.aggregates_check(body, root)
    return d["items"][0] if d["items"] else None


class Corpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = build_corpus(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestProducerGraderRoundTrip(Corpus):
    """Whatever cite.py prints, citecheck must grade `ok`. One implementation."""

    def test_distinct_round_trip(self):
        rc, out, err = cite_agg(self.root, "Security.jsonl",
                                "distinct(Event.EventData.IpAddress)")
        self.assertEqual(rc, 0, err)
        self.assertIn("= 4 ", out)
        self.assertEqual(grade(self.root, out)["verdict"], "ok", out)

    def test_count_round_trip(self):
        rc, out, err = cite_agg(self.root, "Security.jsonl",
                                "count(Event.EventData.SubStatus=0xc000006a)")
        self.assertEqual(rc, 0, err)
        self.assertIn("= 5 ", out)
        self.assertEqual(grade(self.root, out)["verdict"], "ok", out)

    def test_distinct_over_round_trip(self):
        rc, out, err = cite_agg(
            self.root, "Security.jsonl",
            "distinct_over(Event.EventData.IpAddress, 5)")
        self.assertEqual(rc, 0, err)
        self.assertIn("= 1 ", out)
        self.assertEqual(grade(self.root, out)["verdict"], "ok", out)

    def test_time_window_round_trip(self):
        rc, out, err = cite_agg(
            self.root, "Security.jsonl",
            "count(Event.System.EventID=4625, "
            "Event.System.TimeCreated.#attributes.SystemTime>=2021-06-01)")
        self.assertEqual(rc, 0, err)
        self.assertIn("= 15 ", out)
        self.assertEqual(grade(self.root, out)["verdict"], "ok", out)

    def test_line_pseudofield_on_plain_text(self):
        rc, out, err = cite_agg(self.root, "notes.log", "count(line~=boot ok)")
        self.assertEqual(rc, 0, err)
        self.assertIn("= 2 ", out)
        self.assertIn("grep -c -F --", out)
        self.assertEqual(grade(self.root, out)["verdict"], "ok", out)

    def test_the_regression_that_motivates_the_fix(self):
        """A population claim proves a finding that a line quote cannot."""
        rc, agg, err = cite_agg(
            self.root, "Security.jsonl",
            "distinct(Event.EventData.IpAddress, Event.EventData.IpAddress!=-)")
        self.assertEqual(rc, 0, err)
        report = ("Н-1 · перебор\n"
                  "улики: %s\n"
                  "атрибуция: не установлена\n"
                  "исход: попытка\n" % agg)
        d = cc.check(report, self.root, require_quote=True)
        self.assertEqual(d["summary"]["ok"], 0, "no line quote exists for this")
        self.assertEqual(d["aggregates"]["ok"], 1)
        unproven, n = cc.findings_without_evidence(report, d["citations"],
                                                   d["aggregates"])
        self.assertEqual((unproven, n), ([], 1),
                         "an aggregate that verified is proof")
        # …and without the aggregate the very same finding is unproven.
        unproven, n = cc.findings_without_evidence(report, d["citations"])
        self.assertEqual(unproven, ["1"])


class TestFailsClosed(Corpus):
    def one(self, line):
        return grade(self.root, line)

    def test_count_off_by_one_fails(self):
        rc, out, _ = cite_agg(self.root, "Security.jsonl",
                              "distinct(Event.EventData.IpAddress)")
        self.assertEqual(rc, 0)
        self.assertEqual(self.one(out)["verdict"], "ok")
        bad = out.replace("= 4 ", "= 5 ")
        self.assertNotEqual(bad, out)
        item = self.one(bad)
        self.assertEqual(item["verdict"], "count-mismatch")
        self.assertIn("пересчёт даёт 4", item["detail"])
        # the refusal hands back the finished, correct line — paste, not puzzle
        self.assertIn("= 4 ", item["suggest"])

    def test_off_by_one_the_other_way_also_fails(self):
        rc, out, _ = cite_agg(self.root, "Security.jsonl",
                              "distinct(Event.EventData.IpAddress)")
        self.assertEqual(self.one(out.replace("= 4 ", "= 3 "))["verdict"],
                         "count-mismatch")

    def test_nonexistent_file_fails(self):
        item = self.one("агрегат: NoSuch.jsonl · distinct(Event.EventData.IpAddress) = 4 · `x`")
        self.assertEqual(item["verdict"], "missing-file")

    def test_path_traversal_fails(self):
        item = self.one("агрегат: ../../etc/passwd · count(line~=root) = 1 · `x`")
        self.assertEqual(item["verdict"], "missing-file")

    def test_nonexistent_field_fails(self):
        item = self.one("агрегат: Security.jsonl · distinct(Event.EventData.Nope) = 4 · `x`")
        self.assertEqual(item["verdict"], "unknown-field")

    def test_nonexistent_filter_field_fails(self):
        item = self.one("агрегат: Security.jsonl · count(Event.EventData.Nope=1) = 4 · `x`")
        self.assertEqual(item["verdict"], "unknown-field")

    def test_zero_match_fails(self):
        item = self.one("агрегат: Security.jsonl · "
                        "count(Event.EventData.IpAddress=203.0.113.9) = 0 · `x`")
        self.assertEqual(item["verdict"], "zero-match")

    def test_binary_file_fails(self):
        item = self.one("агрегат: blob.bin · count(line~=binary) = 1 · `x`")
        self.assertEqual(item["verdict"], "binary-file")

    def test_json_predicate_on_plain_text_fails(self):
        item = self.one("агрегат: notes.log · distinct(Event.EventData.IpAddress) = 3 · `x`")
        self.assertEqual(item["verdict"], "not-tabular")

    def test_malformed_lines_fail_closed(self):
        for body in (
            "агрегат: Security.jsonl · distinct(Event.EventData.IpAddress) = 4",
            "агрегат: Security.jsonl distinct(x) = 4 · `c`",
            "агрегат: Security.jsonl · frobnicate(x) = 4 · `c`",
            "агрегат: Security.jsonl · count() = 16 · `c`",
            "агрегат: Security.jsonl · distinct() = 4 · `c`",
            "агрегат: Security.jsonl · distinct_over(Event.EventData.IpAddress) = 1 · `c`",
            "агрегат: Security.jsonl · distinct_over(Event.EventData.IpAddress, x) = 1 · `c`",
            "агрегат: Security.jsonl · count(Event.EventData.IpAddress) = 4 · `c`",
            "агрегат: Security.jsonl · count(line~=a, Event.EventData.IpAddress=b) = 1 · `c`",
            "агрегат: Security.jsonl · distinct(line) = 3 · `c`",
            "агрегат: Security.jsonl · count(nested(a)=b) = 1 · `c`",
        ):
            item = self.one(body)
            self.assertEqual(item["verdict"], "malformed", body)
            self.assertTrue(item["detail"], body)

    def test_gate_exception_fails_closed(self):
        """An evaluator that raises must become a verdict, never a green pass."""
        real = cc.opener

        def boom(_path):
            raise RuntimeError("disk on fire")
        real_binary = cc.looks_binary
        cc.opener = boom
        cc.looks_binary = lambda _p: False
        try:
            item = self.one("агрегат: Security.jsonl · "
                            "distinct(Event.EventData.IpAddress) = 4 · `x`")
        finally:
            cc.opener = real
            cc.looks_binary = real_binary
        self.assertEqual(item["verdict"], "unreadable")
        self.assertIn("disk on fire", item["detail"])

    def test_blocking_reaches_the_exit_code(self):
        """Defect #4 of the last round: a count nobody adds up. One ledger."""
        rep = ("Н-1 · x\nулики: агрегат: Security.jsonl · "
               "distinct(Event.EventData.IpAddress) = 99 · `x`\n"
               "атрибуция: установлена\nисход: попытка\n")
        p = os.path.join(self.tmp.name, "r.md")
        open(p, "w", encoding="utf-8").write(rep)
        out = subprocess.run(
            [sys.executable, CITECHECK, p, "--corpus", self.root,
             "--require-quote", "--json"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 1, out.stdout[-2000:])
        d = json.loads(out.stdout)
        self.assertEqual(d["aggregates"]["blocking"], 1)
        self.assertGreaterEqual(d["blocking"], 1,
                                "_blocking_total must see the aggregate")


class TestGaming(Corpus):
    """Real attempts to reach green cheaply. This repo's history is gates gamed
    by content-free filler (12368 bytes of «не смотрел» rows beat a coverage
    check; a keepalive drip beat a first-token deadline), so the bar is: the
    cheapest path to green must be the CORRECT one."""

    def one(self, line):
        return grade(self.root, line)

    def test_gaming_predicate_that_matches_everything(self):
        """The cheapest fake number in any file: cite the whole file."""
        item = self.one("агрегат: Security.jsonl · "
                        "count(Event.System.Channel=Security) = 16 · `x`")
        self.assertEqual(item["verdict"], "too-broad")

    def test_gaming_distinct_that_is_one_per_record(self):
        """distinct(a unique key) == the record count: a row count wearing a hat."""
        item = self.one("агрегат: notes.log · count(line~=) = 3 · `x`")
        self.assertIn(item["verdict"], ("malformed", "too-broad"))

    def test_gaming_threshold_zero_lets_everything_through(self):
        item = self.one("агрегат: Security.jsonl · "
                        "distinct_over(Event.EventData.IpAddress, 0) = 4 · `x`")
        self.assertEqual(item["verdict"], "malformed")

    def test_gaming_threshold_every_value_clears(self):
        """A threshold every value clears is a distinct() wearing a hat."""
        item = self.one("агрегат: spray.jsonl · "
                        "distinct_over(Event.EventData.IpAddress, 2) = 3 · `x`")
        self.assertEqual(item["verdict"], "too-broad")

    def test_a_real_threshold_still_passes(self):
        """The guard must kill the vacuous case only, never a real one."""
        rc, out, err = cite_agg(self.root, "Security.jsonl",
                                "distinct_over(Event.EventData.IpAddress, 5)")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.one(out)["verdict"], "ok")

    def test_gaming_handwritten_command_that_lies(self):
        """The pasted command must BE the predicate, or the line is refused."""
        rc, out, _ = cite_agg(self.root, "Security.jsonl",
                              "distinct(Event.EventData.IpAddress)")
        self.assertEqual(rc, 0)
        head = out.split(" · `")[0]
        item = self.one(head + " · `wc -l Security.jsonl`")
        self.assertEqual(item["verdict"], "command-mismatch")
        self.assertIn("jq", item["expected_command"])

    def test_gaming_shell_injection_is_never_executed(self):
        """A report is untrusted input. The command is rendered and compared,
        never run — and the renderer quotes, so even the pasteable string is
        inert if a human does run it."""
        canary = os.path.join(self.tmp.name, "pwned")
        pred = cc.agg_parse_predicate(
            "count(line~=x; touch %s #)" % canary)
        rendered = cc.agg_render_command("notes.log", pred)
        self.assertIn("'x; touch %s #'" % canary, rendered)
        item = self.one("агрегат: notes.log · %s = 1 · `%s`"
                        % (pred["text"], rendered))
        self.assertIn(item["verdict"], ("zero-match", "count-mismatch"))
        self.assertFalse(os.path.exists(canary), "the gate ran the command")
        src = open(CITECHECK, encoding="utf-8").read()
        self.assertNotIn("subprocess", src)
        self.assertNotIn("os.system", src)
        self.assertNotIn("eval(", src)

    def test_gaming_aggregate_hidden_in_a_code_fence_is_not_evidence(self):
        """If the gate ignored fenced text but a human read it, a fake number
        would be free. It is not counted at all, so the finding stays unproven."""
        rc, out, _ = cite_agg(self.root, "Security.jsonl",
                              "distinct(Event.EventData.IpAddress)")
        report = "Н-1 · x\n```\nулики: %s\n```\nисход: попытка\n" % out
        d = cc.check(report, self.root, require_quote=True)
        self.assertEqual(d["aggregates"]["total"], 0)
        unproven, _n = cc.findings_without_evidence(report, d["citations"],
                                                    d["aggregates"])
        self.assertEqual(unproven, ["1"])

    def test_gaming_a_failing_aggregate_does_not_prove_a_finding(self):
        report = ("Н-1 · x\nулики: агрегат: Security.jsonl · "
                  "distinct(Event.EventData.IpAddress) = 99 · `x`\n"
                  "исход: попытка\n")
        d = cc.check(report, self.root, require_quote=True)
        unproven, _n = cc.findings_without_evidence(report, d["citations"],
                                                    d["aggregates"])
        self.assertEqual(unproven, ["1"])

    def test_gaming_unicode_lookalike_keyword(self):
        """`arperaт:` with Latin letters must not be read as the keyword."""
        item = grade(self.root, "arperat: Security.jsonl · count(a=b) = 1 · `x`")
        self.assertIsNone(item)


class TestDeterminism(Corpus):
    def test_same_input_same_answer(self):
        line = ("агрегат: Security.jsonl · "
                "distinct(Event.EventData.IpAddress) = 4 · `x`")
        a = cc.aggregates_check(line, self.root)["items"][0]
        b = cc.aggregates_check(line, self.root)["items"][0]
        self.assertEqual(a["verdict"], b["verdict"])
        self.assertEqual(a["actual"], b["actual"])

    def test_render_is_a_pure_function_of_the_predicate(self):
        p1 = cc.agg_parse_predicate("distinct(Event.EventData.IpAddress)")
        p2 = cc.agg_parse_predicate("distinct( Event.EventData.IpAddress )")
        self.assertEqual(cc.agg_render_command("a.jsonl", p1),
                         cc.agg_render_command("a.jsonl", p2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
