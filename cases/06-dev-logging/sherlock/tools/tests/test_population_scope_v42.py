#!/usr/bin/env python3
"""Проза не имеет права сужать популяцию сильнее предиката. v42, fix 4.

WHY, measured. The aggregate-citation machinery of v37/v39 works: `citecheck`
re-runs every predicate over the corpus and compares exactly, and the paid
corporate run `20260827T173511Z-v41` graded all three of its headline
aggregates `ok`. The headline was still wrong:

    за 45 часов … зафиксировано 33 456 неудачных входов (4625) от 94 внешних
    адресов по 1975 словарным именам учётных записей

    агрегат: Security.jsonl · count(Event.System.EventID=4625) = 33456
    агрегат: Security.jsonl · distinct(Event.EventData.IpAddress, …=4625) = 94
    агрегат: Security.jsonl · distinct(…TargetUserName, …=4625) = 1975

Measured on that corpus: 4625 events 33456, distinct addresses 94, distinct
names 1975 — and EXTERNAL ones 33455 / 93 / 1974. One local row
(Security.jsonl:13497, `IpAddress` «-», `TargetUserName` «0xivfrh1») sat inside
all three populations. Every number was arithmetically true and answered a
different question than the sentence beside it, because the sentence said
«внешних» and no predicate said it.

This suite pins the gate that refuses that, and — as importantly — pins the
three ways it must NOT fire: no scope word, no local record in the population,
no address field at all. A gate that fires on honest reports gets switched off.
It also pins the fail-closed half: a profile that cannot be read is a refusal,
never a pass.

    python3 tools/tests/test_population_scope_v42.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
V42 = SHERLOCK / "skills" / "v42"
CITE = V42 / "tools" / "cite.py"
PROFILE = V42 / "reference" / "population-scope.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = load("citecheck_v42_scope", V42 / "tools" / "citecheck.py")

DEFECT = "agg_population_narrower_than_predicate"
UNREADABLE = "population-scope-unreadable"


def rec(ip, user, eid=4625):
    return json.dumps({"Event": {
        "System": {"EventID": eid, "Channel": "Security",
                   "TimeCreated": {"#attributes": {
                       "SystemTime": "2021-06-01T18:36:04.949933Z"}}},
        "EventData": {"IpAddress": ip, "TargetUserName": user,
                      "LogonType": "3", "SubStatus": "0xc000006d"}}},
        ensure_ascii=False)


def write(root, name, lines):
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def build_v41_shape(root, local_ip="-"):
    """A miniature of the delivered corpus WITH ITS EXACT HEADLINE NUMBERS:
    33456 events over 94 addresses and 1975 names, of which exactly ONE row is
    local. Synthetic — the real corpus never leaves its host — but the shape
    that produced the defect is reproduced digit for digit: 33455 / 93 / 1974
    external.
    """
    ips = ["45.168.116.%d" % (i + 1) for i in range(93)]
    names = ["user%04d" % i for i in range(1974)]
    lines = []
    for i in range(33455):
        lines.append(rec(ips[i % 93], names[i % 1974]))
    lines.append(rec(local_ip, "0xivfrh1"))       # the one local row
    # the corpus is not made of 4625 alone; without other events the 4625
    # predicate would match every record in the file and `too-broad` — a
    # DIFFERENT guard of the same family — would fire first.
    lines += [rec("10.0.0.5", "root", eid=4624) for _ in range(20)]
    write(root, "Security.jsonl", lines)
    return root


def cite(root, path, pred):
    p = subprocess.run([sys.executable, str(CITE), "--corpus", root,
                        "--file", path, "--aggregate", pred],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError("cite.py отказал на %r: %s" % (pred, p.stderr))
    return "улики: " + p.stdout.strip()


def grade(root, report, profile=None):
    return cc.aggregates_check(report, root, scope_profile=profile)


def verdicts(root, report, profile=None):
    return [i["verdict"] for i in grade(root, report, profile)["items"]]


class Shape(unittest.TestCase):
    """The v41 corpus shape, built once: 33456 rows is cheap, but not free."""

    LOCAL = "-"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = build_v41_shape(cls.tmp.name, cls.LOCAL)
        cls.all_count = cite(cls.root, "Security.jsonl",
                             "count(Event.System.EventID=4625)")
        cls.all_ips = cite(cls.root, "Security.jsonl",
                           "distinct(Event.EventData.IpAddress, "
                           "Event.System.EventID=4625)")
        cls.all_names = cite(cls.root, "Security.jsonl",
                             "distinct(Event.EventData.TargetUserName, "
                             "Event.System.EventID=4625)")
        ex = ", Event.EventData.IpAddress!=%s" % cls.LOCAL
        cls.ext_count = cite(cls.root, "Security.jsonl",
                             "count(Event.System.EventID=4625%s)" % ex)
        cls.ext_ips = cite(cls.root, "Security.jsonl",
                           "distinct(Event.EventData.IpAddress, "
                           "Event.System.EventID=4625%s)" % ex)
        cls.ext_names = cite(cls.root, "Security.jsonl",
                             "distinct(Event.EventData.TargetUserName, "
                             "Event.System.EventID=4625%s)" % ex)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_00_the_corpus_reproduces_the_delivered_numbers(self):
        """If the fixture drifts, every assertion below means something else."""
        self.assertIn("= 33456 ", self.all_count)
        self.assertIn("= 94 ", self.all_ips)
        self.assertIn("= 1975 ", self.all_names)
        self.assertIn("= 33455 ", self.ext_count)
        self.assertIn("= 93 ", self.ext_ips)
        self.assertIn("= 1974 ", self.ext_names)


class TestTheDefect(Shape):
    """One sentence, one aggregate: the smallest form of the failure."""

    def test_prose_says_external_predicate_does_not(self):
        report = "\n".join([
            "что сломано: зафиксировано 33 456 неудачных входов "
            "с внешних адресов.", "", self.all_count])
        item = grade(self.root, report)["items"][0]
        self.assertEqual(item["verdict"], DEFECT, item["detail"])
        self.assertEqual(item.get("defect"), DEFECT)

    def test_the_same_claim_with_the_narrower_predicate_passes(self):
        report = "\n".join([
            "что сломано: зафиксировано 33 455 неудачных входов "
            "с внешних адресов.", "", self.ext_count])
        self.assertEqual(verdicts(self.root, report), ["ok"])

    def test_the_refusal_names_the_honest_predicate_and_its_number(self):
        """v37 measured the cost of a refusal that only says «wrong»: the model
        DELETES the number (93 sources became 4). The way out must be printed."""
        report = "\n".join(["внешних адресов: 33 456 отказов.", "",
                            self.all_count])
        item = grade(self.root, report)["items"][0]
        self.assertIn("Event.EventData.IpAddress!=-", item["detail"])
        self.assertIn("33455", item["detail"])
        self.assertIsNotNone(item["suggest"])
        # what it suggests must itself grade `ok` — the gate re-grades what it
        # prints, so a suggestion it would refuse is worse than none.
        again = "\n".join(["внешних адресов: 33 455 отказов.", "",
                           "улики: " + item["suggest"]])
        self.assertEqual(verdicts(self.root, again), ["ok"], item["suggest"])


class TestDeliveredRegression(Shape):
    """The delivered headline, verbatim, with its three aggregates."""

    HEAD = ("что сломано: сервер IPSERVER атакуют по RDP: за 45 часов "
            "(2021-06-01T00:09:15Z → 2021-06-02T21:11:10Z) зафиксировано "
            "%s неудачных входов (4625) от %s внешних адресов по %s "
            "словарным именам учётных записей.")

    def test_as_delivered_all_three_are_refused(self):
        report = "\n".join([
            "### Н-1 · Массовый перебор паролей RDP с внешних адресов", "",
            self.HEAD % ("33 456", "94", "1975"), "",
            self.all_count, self.all_ips, self.all_names])
        self.assertEqual(verdicts(self.root, report), [DEFECT] * 3)
        self.assertEqual(grade(self.root, report)["blocking"], 3)

    def test_restated_honestly_all_three_pass(self):
        report = "\n".join([
            "### Н-1 · Массовый перебор паролей RDP с внешних адресов", "",
            self.HEAD % ("33 455", "93", "1 974"), "",
            self.ext_count, self.ext_ips, self.ext_names])
        d = grade(self.root, report)
        self.assertEqual([i["verdict"] for i in d["items"]], ["ok"] * 3,
                         cc.render_aggregates(d))
        self.assertEqual(d["blocking"], 0)

    def test_digit_groups_do_not_hide_the_claim(self):
        """«33456», «33 456», «33 456» (nbsp) and «33,456» are one number."""
        for form in ("33456", "33 456", "33 456", "33,456"):
            report = "\n".join(["с внешних адресов: %s отказов." % form, "",
                                self.all_count])
            self.assertEqual(verdicts(self.root, report), [DEFECT], form)


class TestLocalitySpelling(unittest.TestCase):
    """`-` is how ONE corpus spells «no address». The class is the point."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _corpus(self, local_ip):
        write(self.root, "Security.jsonl",
              [rec("45.168.116.98", "admin"), rec("45.168.116.98", "root"),
               rec("91.220.163.170", "admin"), rec(local_ip, "0xivfrh1"),
               # a non-4625 row: without it `count(EventID=4625)` matches the
               # whole file and `too-broad` fires before this gate is reached.
               rec("10.0.0.5", "root", eid=4624)])

    def test_loopback_ipv4(self):
        self._corpus("127.0.0.1")
        agg = cite(self.root, "Security.jsonl",
                   "count(Event.System.EventID=4625)")
        self.assertIn("= 4 ", agg)
        report = "\n".join(["4 попытки входа с внешних адресов.", "", agg])
        item = grade(self.root, report)["items"][0]
        self.assertEqual(item["verdict"], DEFECT, item["detail"])
        self.assertIn("Event.EventData.IpAddress!=127.0.0.1", item["detail"])
        honest = cite(self.root, "Security.jsonl",
                      "count(Event.System.EventID=4625, "
                      "Event.EventData.IpAddress!=127.0.0.1)")
        self.assertIn("= 3 ", honest)
        self.assertEqual(
            verdicts(self.root, "3 попытки входа с внешних адресов.\n\n"
                                + honest), ["ok"])

    def test_ipv6_loopback_and_localhost_are_the_same_claim(self):
        for spelling in ("::1", "localhost", "127.0.0.53"):
            with self.subTest(spelling=spelling):
                self._corpus(spelling)
                agg = cite(self.root, "Security.jsonl",
                           "count(Event.System.EventID=4625)")
                report = "4 входа извне.\n\n" + agg
                self.assertEqual(verdicts(self.root, report), [DEFECT],
                                 spelling)


class TestMustNotFire(Shape):
    """Three ways this gate must stay silent. It is switched off otherwise."""

    def test_no_scope_word_beside_an_unrestricted_predicate_passes(self):
        report = "\n".join([
            "что сломано: зафиксировано 33 456 неудачных входов (4625).",
            "", self.all_count])
        self.assertEqual(verdicts(self.root, report), ["ok"])

    def test_a_scope_word_in_a_sentence_about_another_number_is_not_this_claim(self):
        report = "\n".join([
            "что сломано: 12 успешных входов с внешних адресов.",
            "всего отказов в корпусе: 33 456.", "", self.all_count])
        self.assertEqual(verdicts(self.root, report), ["ok"])

    def test_population_without_any_address_field_is_not_this_class(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "app.jsonl", [
                json.dumps({"Event": {"System": {"EventID": eid},
                                      "EventData": {"TargetUserName": u}}})
                for u, eid in (("a", 4625), ("b", 4625), ("c", 4625),
                               ("d", 4624))])
            agg = cite(d, "app.jsonl", "count(Event.System.EventID=4625)")
            report = "3 отказа с внешних адресов.\n\n" + agg
            self.assertEqual(verdicts(d, report), ["ok"])

    def test_a_population_with_no_local_record_passes(self):
        report = "\n".join([
            "что сломано: 33 455 отказов с внешних адресов.", "",
            self.ext_count])
        self.assertEqual(verdicts(self.root, report), ["ok"])


class TestFailClosed(Shape):
    """A gate that cannot check the population has not checked it."""

    def test_missing_profile_refuses_every_aggregate(self):
        report = "33 456 отказов.\n\n" + self.all_count
        v = verdicts(self.root, report, profile="/nonexistent/scope.json")
        self.assertEqual(v, [UNREADABLE])

    def test_unparseable_profile_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "scope.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{ не json")
            v = verdicts(self.root, "33 456 отказов.\n\n" + self.all_count,
                         profile=p)
            self.assertEqual(v, [UNREADABLE])

    def test_profile_short_of_a_required_key_refuses(self):
        with open(str(PROFILE), encoding="utf-8") as fh:
            base = json.load(fh)
        for key in ("classes", "sentence_split", "digit_group_separators"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as d:
                spec = json.loads(json.dumps(base))
                spec.pop(key)
                p = os.path.join(d, "scope.json")
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump(spec, fh)
                self.assertEqual(
                    verdicts(self.root, "33 456 отказов.\n\n" + self.all_count,
                             profile=p), [UNREADABLE], key)

    def test_class_without_locality_data_refuses(self):
        with open(str(PROFILE), encoding="utf-8") as fh:
            base = json.load(fh)
        with tempfile.TemporaryDirectory() as d:
            spec = json.loads(json.dumps(base))
            spec["classes"][0]["local_values"] = []
            spec["classes"][0]["local_prefixes"] = []
            p = os.path.join(d, "scope.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(spec, fh)
            self.assertEqual(
                verdicts(self.root, "33 456 отказов.\n\n" + self.all_count,
                         profile=p), [UNREADABLE])
            with self.assertRaises(cc.ScopeError):
                cc.load_scope_profile(p)

    def test_an_unparseable_predicate_is_a_refusal_not_a_pass(self):
        body = ("улики: агрегат: Security.jsonl · count(Event.System.EventID) "
                "= 33456 · `jq -c 'x'`")
        report = "33 456 отказов с внешних адресов.\n\n" + body
        v = verdicts(self.root, report)
        self.assertEqual(v, ["malformed"])
        self.assertNotIn("ok", v)

    def test_the_shipped_profile_loads(self):
        prof = cc.load_scope_profile(str(PROFILE))
        self.assertTrue(prof["classes"][0]["_prose_re"])
        self.assertEqual(prof["classes"][0]["defect"], DEFECT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
