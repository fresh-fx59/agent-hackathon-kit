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
ROLLOVER = os.path.join(V37, "rollover.py")


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


# ==========================================================================
# fix-4: the parts of this form that had NO mutation-resistant test.
#
# A review applied 24 single-line mutants to citecheck.py and ran the 30 tests
# above. TEN survived. The three that mattered most were the whole blocking
# ACCOUNTING — delete all three call sites that add `aggregates.blocking` into
# the stop number and the suite still passed while a report with a false
# aggregate exited 0:
#
#   none  with-ledger rc=1 | none no-ledger rc=1
#   M19   with-ledger rc=1 | M19  no-ledger rc=0   <- one line, gate fails open
#   ALL3  with-ledger rc=0 | ALL3 no-ledger rc=0   <- and 30/30 still OK
#
# `test_blocking_reaches_the_exit_code` above missed it because its fixture
# report is blocking for OTHER reasons — a bad line citation, a missing quote —
# so `assertGreaterEqual(d["blocking"], 1)` passed on unrelated defects, and it
# exercised only `--json`, never the plain render, never `--ledger`.
#
# So the fixture here is a report that citecheck grades COMPLETELY GREEN — rc
# 0, blocking 0, every citation ok, coverage complete, outcomes consistent —
# and the only thing the bad variant adds is one aggregate whose count is
# wrong. Then rc and the printed `blocking` can only be about the aggregate.
# ==========================================================================


def _cite_line(root, path, lineno):
    p = subprocess.run([sys.executable, CITE, "--corpus", root,
                        "%s:%d" % (path, lineno)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _rollover_section(root):
    """The «Окно записей» section, from the real producer.

    v38 makes a report that cites corpus files owe this section; without it
    `report_evidence()` counts a blocking defect and these tests would then be
    measuring the rollover term instead of the aggregate one.
    """
    p = subprocess.run([sys.executable, ROLLOVER, "--corpus", root,
                        "--report", "--required-only"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def green_report(root, extra=""):
    """A report on the mini corpus that citecheck grades with ZERO defects."""
    return """# Находки

### Н-1 · Перебор паролей с внешнего адреса
улики: %s
%sатрибуция: не установлена
исход: попытка

# Отклонённые кандидаты

### К-1 · Вход по SSH как кандидат на компрометацию
улики: %s
исход: норма

# Принадлежность учётных записей

Added for v37 fix-6b: the finding quotes a record carrying
`"TargetUserName":"ADMINI"`, so the report must say whose account that is
before it may lean on it. Without this the control is blocking for a REAL
reason and the three accounting tests below would grade the wrong defect.

| учётная запись | первое появление | path:line «цитата» | как | вывод | раньше |
| --- | --- | --- | --- | --- | --- |
| ADMINI | 2021-06-01T18:36:04Z | %s | удалённый вход | не определяется | — |

# Покрытие

| файл | статус | наблюдение |
| --- | --- | --- |
| Security.jsonl | наблюдение | %s |
| spray.jsonl | наблюдение | %s |
| notes.log | наблюдение | %s |
| blob.bin | двоичный | формат=двоичный |

%s

# Разбор рабочего списка

# Чего я не знаю

# ВЕРДИКТ
attacked-not-proven
""" % (_cite_line(root, "Security.jsonl", 1), extra,
       _cite_line(root, "notes.log", 2),
       _cite_line(root, "Security.jsonl", 1),
       _cite_line(root, "Security.jsonl", 2),
       _cite_line(root, "spray.jsonl", 1),
       _cite_line(root, "notes.log", 3), _rollover_section(root))


class TestBlockingAccounting(unittest.TestCase):
    """Three call sites carry `aggregates.blocking` into the stop number:
    `_blocking_total` (the `--json` field), `ledger()`'s `total` (the `--ledger`
    exit), and `blocking_defects` in `main()` (every exit). One test per site,
    each on a report whose ONLY defect is the aggregate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "corpus")
        os.makedirs(self.root)
        build_corpus(self.root)
        self.out = os.path.join(self.tmp.name, "out")
        os.makedirs(self.out)
        # the aggregate is TRUE (4 distinct IpAddress values) in the control and
        # off by 95 in the variant; nothing else differs between the two.
        rc, agg, err = cite_agg(self.root, "Security.jsonl",
                                "distinct(Event.EventData.IpAddress)")
        assert rc == 0, err
        self.good_agg = agg
        self.bad_agg = agg.replace("= 4 ", "= 99 ")
        assert self.bad_agg != agg
        self.ledger = os.path.join(self.out, "worklist.tsv")
        with open(self.ledger, "w", encoding="utf-8") as fh:
            fh.write("# id\tвердикт\tось\tссылка\n")
            fh.write("W-1\tN 1\tчастота\tSecurity.jsonl:2\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, agg_line):
        extra = ("улики: %s\n" % agg_line) if agg_line else ""
        p = os.path.join(self.out, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(green_report(self.root, extra))
        return p

    def run_gate(self, path, *extra):
        return subprocess.run(
            [sys.executable, CITECHECK, path, "--corpus", self.root,
             "--require-quote"] + list(extra), capture_output=True, text=True)

    # ---- the control: without the aggregate this report is spotless --------
    def test_control_report_is_green_in_all_three_branches(self):
        """If the control were blocking for any other reason, the three tests
        below would pass on an unrelated defect — which is exactly how the old
        test missed all three mutants."""
        p = self.write("control.md", None)
        for extra in ([], ["--json"], ["--ledger", self.ledger]):
            r = self.run_gate(p, *extra)
            self.assertEqual(r.returncode, 0, (extra, r.stdout[-3000:]))
        r = self.run_gate(p, "--json")
        self.assertEqual(json.loads(r.stdout)["blocking"], 0)
        # and a TRUE aggregate does not make it blocking either
        p2 = self.write("control-agg.md", self.good_agg)
        r = self.run_gate(p2, "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        d = json.loads(r.stdout)
        self.assertEqual(d["aggregates"]["blocking"], 0)
        self.assertEqual(d["blocking"], 0)

    # ---- M17: `_blocking_total` drops the aggregate term -------------------
    def test_false_aggregate_json_branch(self):
        p = self.write("bad.md", self.bad_agg)
        r = self.run_gate(p, "--json")
        d = json.loads(r.stdout)
        self.assertEqual(d["aggregates"]["blocking"], 1, r.stdout[-2000:])
        self.assertEqual(d["blocking"], 1,
                         "the ONLY defect is the aggregate, so the stop number "
                         "must be exactly 1 — `_blocking_total` has to add it")
        self.assertEqual(r.returncode, 1, r.stdout[-3000:])

    # ---- M19: `blocking_defects` in main() drops the aggregate -------------
    def test_false_aggregate_plain_branch(self):
        p = self.write("bad2.md", self.bad_agg)
        r = self.run_gate(p)
        self.assertIn("count-mismatch", r.stdout)
        self.assertEqual(r.returncode, 1,
                         "plain render: `blocking_defects` must see the "
                         "aggregate — this branch has no ledger to hide behind")

    # ---- M18: `ledger()`'s total drops the aggregate -----------------------
    def test_false_aggregate_ledger_branch(self):
        p = self.write("bad3.md", self.bad_agg)
        r = self.run_gate(p, "--ledger", self.ledger)
        self.assertIn("count-mismatch", r.stdout)
        self.assertIn("ИТОГ: НЕ ЗАКОНЧЕНО — осталось 1", r.stdout)
        self.assertEqual(r.returncode, 1, r.stdout[-3000:])

    def test_false_aggregate_ledger_and_json_together(self):
        """The combination the reviewer used to show all three sites at once."""
        p = self.write("bad4.md", self.bad_agg)
        r = self.run_gate(p, "--ledger", self.ledger, "--json")
        self.assertEqual(r.returncode, 1, r.stdout[-3000:])
        self.assertEqual(json.loads(r.stdout)["blocking"], 1)


class TestRenderedCommandAgreesWithTheGate(Corpus):
    """The rendered `jq` must return the number the gate computed. It did not.

    `_agg_str` returns None for a field that is absent or JSON null and the
    record is then EXCLUDED. The rendered jq said `(.f|tostring) != "v"`, and
    in jq a missing path is `null` whose `tostring` is the string "null" — so
    `!=` and `contains` both SUCCEEDED on a record that has no such field. On
    the real corpus `count(Event.EventData.IpAddress!=-)`: gate 33455, pasted
    command 34728. The only reason to ship a command is that a human can
    reproduce the number.
    """

    def setUp(self):
        super().setUp()
        if subprocess.run(["sh", "-c", "command -v jq"],
                          capture_output=True).returncode != 0:
            self.skipTest("jq not installed")
        # records where the field is ABSENT, and one where it is JSON null —
        # the two shapes the old rendering counted and the evaluator did not.
        with open(os.path.join(self.root, "sparse.jsonl"), "a",
                  encoding="utf-8") as fh:
            for _ in range(3):
                fh.write(json.dumps({"Event": {"System": {"EventID": 4625},
                                               "EventData": {}}}) + "\n")
            fh.write(json.dumps({"Event": {"System": {"EventID": 4625},
                                           "EventData": {"IpAddress": None}}})
                     + "\n")
            fh.write(json.dumps({"Event": {"System": {"EventID": 4625},
                                           "EventData": {"IpAddress": "-"}}})
                     + "\n")
            for ip in ("8.8.8.8", "9.9.9.9", "8.8.8.8"):
                fh.write(json.dumps({"Event": {"System": {"EventID": 4625},
                                               "EventData": {"IpAddress": ip}}})
                         + "\n")

    def paste(self, out):
        """Run the command the citation ships, from the corpus root."""
        cmd = out.split(" · `", 1)[1].rstrip("`")
        r = subprocess.run(["sh", "-c", cmd], cwd=self.root,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return int(r.stdout.strip())

    def check(self, path, pred):
        rc, out, err = cite_agg(self.root, path, pred)
        self.assertEqual(rc, 0, err)
        claimed = int(out.split(" = ")[1].split(" ")[0])
        self.assertEqual(self.paste(out), claimed,
                         "gate and pasted command disagree: %s" % out)
        self.assertEqual(grade(self.root, out)["verdict"], "ok", out)

    def test_not_equal_over_a_sparse_field(self):
        self.check("sparse.jsonl", "count(Event.EventData.IpAddress!=-)")

    def test_substring_over_a_sparse_field(self):
        self.check("sparse.jsonl", "count(Event.EventData.IpAddress~=8.8)")

    def test_equality_over_a_sparse_field(self):
        self.check("sparse.jsonl", "count(Event.EventData.IpAddress=8.8.8.8)")

    def test_distinct_over_a_sparse_field(self):
        self.check("sparse.jsonl", "distinct(Event.EventData.IpAddress)")

    def test_distinct_over_threshold_over_a_sparse_field(self):
        self.check("sparse.jsonl",
                   "distinct_over(Event.EventData.IpAddress, 1)")

    def test_ordered_comparisons(self):
        self.check("Security.jsonl",
                   "count(Event.System.TimeCreated.#attributes.SystemTime"
                   ">=2021-06-01, Event.EventData.SubStatus=0xc000006a)")
        self.check("Security.jsonl",
                   "count(Event.System.TimeCreated.#attributes.SystemTime"
                   "<=2021-07-01, Event.EventData.SubStatus=0xc000006a)")

    def test_the_flagship_shapes(self):
        self.check("Security.jsonl",
                   "distinct(Event.EventData.IpAddress, "
                   "Event.EventData.IpAddress!=-)")
        self.check("Security.jsonl", "count(line~=nothing-here)"
                   if False else
                   "count(Event.EventData.SubStatus~=0xc000006a)")


class TestPathsWithSpaces(unittest.TestCase):
    """«producer and grader cannot drift» was false for any path with a space.

    `AGG_BODY_RE` had `path` as `[^\s·]+` and `cite.py --aggregate` printed the
    path unescaped, so `cite.py` PRINTED a line that `citecheck` then graded
    `malformed` — and SKILL.md forbids hand-editing it («отказ — это не повод
    переписать строку руками»). An unfixable loop. Windows event-log exports
    with a space in the name are ordinary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as fh:
            for eid in (4625, 4625, 4624):
                fh.write(json.dumps({"Event": {"System": {"EventID": eid}}})
                         + "\n")
        return name

    def round_trip(self, name):
        rc, out, err = cite_agg(self.root, name, "count(Event.System.EventID=4625)")
        self.assertEqual(rc, 0, err)
        self.assertIn("= 2 ", out)
        item = grade(self.root, out)
        self.assertEqual(item["verdict"], "ok", out)
        self.assertEqual(item["path"], name)
        return out

    def test_space_in_the_filename(self):
        out = self.round_trip(self.put("My Log.jsonl"))
        self.assertIn('"My Log.jsonl"', out)

    def test_interpunct_in_the_filename(self):
        out = self.round_trip(self.put("Odd·Name.jsonl"))
        self.assertIn('"Odd·Name.jsonl"', out)

    def test_plain_name_stays_unquoted(self):
        out = self.round_trip(self.put("Plain.jsonl"))
        self.assertIn("агрегат: Plain.jsonl · ", out)

    def test_a_quote_in_the_path_is_refused_not_mangled(self):
        pred = cc.agg_parse_predicate("count(Event.System.EventID=4625)")
        with self.assertRaises(cc.AggError):
            cc.agg_render_citation('we"ird.jsonl', pred, 2)


class TestPopulationGaming(Corpus):
    """`too-broad` fired only on `actual == population`, so a predicate that
    matched every record which merely HAS the field walked through. On the real
    corpus `count(Event.EventData.IpAddress!=zzzz) = 33643` and
    `count(Event.EventData.SubStatus~=0) = 33456` both graded ok — «how many
    records have this field» dressed as a census."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.root, "mixed.jsonl"), "w",
                  encoding="utf-8") as fh:
            for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
                fh.write(json.dumps({"Event": {"EventData": {"IpAddress": ip},
                                               "System": {"EventID": 4625}}})
                         + "\n")
            for _ in range(5):      # no IpAddress at all
                fh.write(json.dumps({"Event": {"EventData": {},
                                               "System": {"EventID": 4624}}})
                         + "\n")

    def test_matches_every_record_that_has_the_field(self):
        rc, out, err = cite_agg(self.root, "mixed.jsonl",
                                "count(Event.EventData.IpAddress!=zzzz)")
        self.assertEqual(rc, 1)
        self.assertIn("too-broad", err)
        item = grade(self.root, "агрегат: mixed.jsonl · "
                     "count(Event.EventData.IpAddress!=zzzz) = 3 · `x`")
        self.assertEqual(item["verdict"], "too-broad")
        self.assertIn("3 из 3", item["detail"])

    def test_single_character_substring_is_refused_at_the_parser(self):
        with self.assertRaises(cc.AggError):
            cc.agg_parse_predicate("count(Event.EventData.SubStatus~=0)")
        rc, _out, err = cite_agg(self.root, "Security.jsonl",
                                 "count(Event.EventData.SubStatus~=0)")
        self.assertEqual(rc, 1)
        self.assertIn("~=", err)

    def test_a_real_filter_over_a_sparse_field_still_passes(self):
        """The guard must kill the vacuous case only. Two of three records with
        the field, not three of three."""
        rc, out, err = cite_agg(self.root, "mixed.jsonl",
                                "count(Event.EventData.IpAddress!=1.1.1.1)")
        self.assertEqual(rc, 0, err)
        self.assertIn("= 2 ", out)
        self.assertEqual(grade(self.root, out)["verdict"], "ok")


class TestOrderedComparisonsAreISO8601Only(unittest.TestCase):
    """`>=`/`<=` are lexicographic and the docstring's whole defence of that is
    «the window predicate this exists for is an ISO-8601 timestamp». Nothing
    enforced it, so `count(Event.System.EventID<=5) = 33841` verified on the
    real corpus — 4625/4624 events, a numerically absurd claim that the gate
    and the pasted command AGREED on."""

    def test_a_bare_number_is_refused(self):
        for pred in ("count(Event.System.EventID<=5)",
                     "count(Event.System.EventID>=1000)",
                     "count(Event.EventData.LogonType<=9)"):
            with self.assertRaises(cc.AggError, msg=pred):
                cc.agg_parse_predicate(pred)

    def test_free_text_is_refused(self):
        with self.assertRaises(cc.AggError):
            cc.agg_parse_predicate("count(Event.EventData.TargetUserName>=root)")

    def test_iso_8601_shapes_are_accepted(self):
        for v in ("2021-06-01", "2021-06-01T18", "2021-06-01T18:36",
                  "2021-06-01T18:36:04", "2021-06-01T18:36:04.949933Z",
                  "2021-06-01 18:36:04", "2021-06-01T18:36:04+03:00"):
            p = cc.agg_parse_predicate(
                "count(Event.System.TimeCreated.#attributes.SystemTime>=%s)" % v)
            self.assertEqual(p["filters"][0][2], v)

    def test_line_pseudofield_still_rejects_ordering(self):
        with self.assertRaises(cc.AggError):
            cc.agg_parse_predicate("count(line>=2021-06-01)")


class TestMutantsThatSurvived(Corpus):
    """One test per surviving mutant from the review's 24-mutant set, each
    written so that the mutation — and nothing else — makes it fail."""

    def one(self, line):
        return grade(self.root, line)

    def cited(self, path, pred_text, count):
        """A citation with the CORRECT rendered command, so the verdict under
        test is not masked by `command-mismatch`."""
        pred = cc.agg_parse_predicate(pred_text)
        return grade(self.root, cc.agg_render_citation(path, pred, count))

    def test_M3_oserror_must_be_unreadable_not_ok(self):
        """M3: the OSError arm returning `ok`. A file the gate cannot read is
        not a file the gate has verified."""
        real = cc.opener
        real_binary = cc.looks_binary

        def boom(_path):
            raise OSError(13, "Permission denied")
        cc.opener = boom
        cc.looks_binary = lambda _p: False
        try:
            item = self.one("агрегат: Security.jsonl · "
                            "distinct(Event.EventData.IpAddress) = 4 · `x`")
        finally:
            cc.opener = real
            cc.looks_binary = real_binary
        self.assertEqual(item["verdict"], "unreadable")
        self.assertIn("Permission denied", item["detail"])

    def test_M9_not_equal_is_not_equality(self):
        """M9: `!=` inverted to `==` inside `_agg_cmp`."""
        self.assertTrue(cc._agg_cmp("a", "!=", "b"))
        self.assertFalse(cc._agg_cmp("a", "!=", "a"))
        # 16 records, 1 of them "-": != selects the other 15
        item = self.cited("Security.jsonl",
                          "count(Event.EventData.IpAddress!=-)", 15)
        self.assertEqual(item["verdict"], "ok", item["detail"])
        self.assertEqual(item["actual"], 15,
                         "`!=` must select everything that is NOT the value")
        item = self.cited("Security.jsonl",
                          "count(Event.EventData.IpAddress!=-)", 1)
        self.assertEqual(item["verdict"], "count-mismatch")

    def test_M11_distinct_over_is_strictly_greater(self):
        """M11: `>` relaxed to `>=` — the boundary was untested. spray.jsonl has
        three values, each exactly 3 times: a threshold of 3 selects NONE."""
        item = self.cited("spray.jsonl",
                          "distinct_over(Event.EventData.IpAddress, 3)", 3)
        self.assertEqual(item["verdict"], "zero-match",
                         "«more than 3» must exclude a value seen exactly 3")
        item = self.cited("spray.jsonl",
                          "distinct_over(Event.EventData.IpAddress, 2)", 3)
        self.assertEqual(item["verdict"], "too-broad")

    def test_M13_not_tabular_still_fires(self):
        """M13: the `not-tabular` arm removed. A JSON field on a text file must
        not silently become `unknown-field` or a zero."""
        item = self.cited("notes.log",
                          "count(Event.EventData.IpAddress=1.1.1.1)", 1)
        self.assertEqual(item["verdict"], "not-tabular")
        self.assertIn("JSONL", item["detail"])

    def test_M13_mostly_unparsed_is_also_not_tabular(self):
        """The second arm: a file with a couple of JSON lines in a sea of prose
        is not a JSONL corpus either."""
        p = os.path.join(self.root, "mostly-prose.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"Event": {"EventData": {"IpAddress": "1.1.1.1"}}})
                     + "\n")
            fh.write("this is prose\n" * 5)
        item = self.cited("mostly-prose.log",
                          "count(Event.EventData.IpAddress=1.1.1.1)", 1)
        self.assertEqual(item["verdict"], "not-tabular")

    def test_M14_binary_file_still_fires(self):
        """M14: the `looks_binary` check removed. blob.bin must never be
        counted — and must be named `binary-file`, not misread as text."""
        item = self.cited("blob.bin", "count(line~=binary)", 1)
        self.assertEqual(item["verdict"], "binary-file")
        self.assertIn("двоичный", item["detail"])

    def test_M16_ambiguous_still_fires(self):
        """M16: the `ambiguous` arm removed, so a basename meaning two files
        would silently be graded against whichever one sorted first."""
        for d in ("hostA", "hostB"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
            with open(os.path.join(self.root, d, "sys.log"), "w",
                      encoding="utf-8") as fh:
                fh.write("boot ok\n")
        item = self.cited("sys.log", "count(line~=boot ok)", 1)
        self.assertEqual(item["verdict"], "ambiguous")
        self.assertEqual(item["actual"], None,
                         "an ambiguous path must never be evaluated")
        self.assertIn("2", item["detail"])

    def test_M2_ordered_comparison_includes_the_boundary(self):
        """M2: `>=` weakened to `>`. Every record in Security.jsonl carries the
        same SystemTime, so a bound EQUAL to it must select all 16 — under `>`
        it selects none, and a window predicate that silently drops the instant
        it was asked about is how an incident window loses its first event."""
        ts = "2021-06-01T18:36:04.949933Z"
        self.assertTrue(cc._agg_cmp(ts, ">=", ts))
        self.assertTrue(cc._agg_cmp(ts, "<=", ts))
        item = self.cited(
            "Security.jsonl",
            "count(Event.System.TimeCreated.#attributes.SystemTime>=%s, "
            "Event.EventData.SubStatus=0xc000006a)" % ts, 5)
        self.assertEqual(item["verdict"], "ok", item["detail"])
        item = self.cited(
            "Security.jsonl",
            "count(Event.System.TimeCreated.#attributes.SystemTime<=%s, "
            "Event.EventData.SubStatus=0xc000006a)" % ts, 5)
        self.assertEqual(item["verdict"], "ok", item["detail"])

    def test_M13_a_file_with_no_records_at_all_is_not_tabular(self):
        """M13: the `parsed == 0` arm. Its sibling (`unparsed > parsed`) covers
        a file full of prose, so only a file with NEITHER records NOR unparsed
        content — blank lines — separates the two arms. Without this arm such a
        file falls through to `unknown-field`, which reads as «you typoed the
        field», not «there is nothing here to count»."""
        with open(os.path.join(self.root, "blank.jsonl"), "w",
                  encoding="utf-8") as fh:
            fh.write("\n\n\n")
        item = self.cited("blank.jsonl",
                          "count(Event.EventData.IpAddress=1.1.1.1)", 1)
        self.assertEqual(item["verdict"], "not-tabular", item["detail"])
        self.assertIn("ни одной JSON-записи", item["detail"])

    def test_M24_substring_is_not_equality(self):
        """M24: `~=` collapsed to `==`."""
        self.assertTrue(cc._agg_cmp("0xc0000064", "~=", "c00000"))
        self.assertFalse(cc._agg_cmp("0xc0000064", "~=", "zzzz"))
        item = self.cited("Security.jsonl",
                          "count(Event.EventData.SubStatus~=c000006a)", 5)
        self.assertEqual(item["verdict"], "ok",
                         "`~=` must match a substring, not the whole value")


if __name__ == "__main__":
    unittest.main(verbosity=2)
