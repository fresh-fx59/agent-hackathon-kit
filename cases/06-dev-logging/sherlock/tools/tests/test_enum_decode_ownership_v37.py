#!/usr/bin/env python3
"""v37 fix-6: a numeric enum quoted without a decode, and ownership before intruder.

WHY, measured on sherlock-winevtx-runs-v37-full-r1/20260825T173021Z-v37 — the
first report in this project to pass all three gates, exit 0, blocking 0:

  * Firewall.jsonl:443,444 are EventID 2004 with "Action":2 at 22:37:55.328205Z
    Firewall.jsonl:445-450 are EventID 2005 with "Action":3 at 22:38:01.613377Z
    Finding Н-3 quoted «"Action":2» VERBATIM and wrote «действие 2/3
    (разрешить)». Action=2 is BLOCK. The block→allow pair six seconds apart is
    the strongest evidence in the corpus — the block proves 3proxy ran and bound
    a socket, the allow proves a human clicked through the Windows dialog. Read
    as one «allow» it proves neither.

  * System.jsonl:1 is 2021-05-08T15:04:30.215370Z, and
    User-Profile-Service-4Operational.jsonl:1 loads C:\\Users\\root\\ntuser.dat
    at 2021-05-08T15:04:51.390916Z — 21 seconds into the very first boot, a full
    day before the RDP login. Finding Н-1 called that login by `root`
    «признак постороннего доступа». `root` IS the machine owner.

Both citations were genuine. Both readings were wrong. The citation gate cannot
tell the difference, which is what these two checks are for.

This suite pins the true positives, the legitimate cases that must NOT fire, the
fail-closed paths, the gaming attempts that were actually tried, and — the thing
this repo keeps getting wrong — the WIRING: three one-line mutations once
disabled the last gate's accounting while 30 tests stayed green.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
V37 = os.path.normpath(os.path.join(HERE, "..", "..", "skills", "v37"))
CITECHECK = os.environ.get("SHERLOCK_CITECHECK",
                           os.path.join(V37, "tools", "citecheck.py"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = load("cc6", CITECHECK)

FW = ('{"Event":{"System":{"EventID":2004,"TimeCreated":{"#attributes":'
      '{"SystemTime":"2021-05-09T22:37:55.328205Z"}}},"EventData":'
      '{"RuleName":"3proxy - tiny proxy server","Origin":1,"Protocol":6,'
      '"Action":2,"Profiles":4}}}')
FW3 = FW.replace('"Action":2', '"Action":3').replace("2004", "2005").replace(
    "22:37:55.328205", "22:38:01.613377")
PROFILE = ('{"Event":{"System":{"EventID":5,"TimeCreated":{"#attributes":'
           '{"SystemTime":"2021-05-08T15:04:51.390916Z"}}},"EventData":'
           '{"File":"C:\\\\Users\\\\root\\\\ntuser.dat","Key":'
           '"S-1-5-21-2929202171-1942120112-2054978817-1001"}}}')
BOOT = ('{"Event":{"System":{"EventID":6009,"TimeCreated":{"#attributes":'
        '{"SystemTime":"2021-05-08T15:04:30.215370Z"}}}}}')
LOGIN = ('{"Event":{"System":{"EventID":21,"TimeCreated":{"#attributes":'
         '{"SystemTime":"2021-05-09T21:43:09.396401Z"}}},"UserData":'
         '{"EventXML":{"User":"IPSERVER\\\\root","Address":"192.99.186.31"}}}}')

# The quote must be VERBATIM against PROFILE above — two backslashes, as the
# JSON on disk has them, not four.
def q(record, needle, length=64):
    """A verbatim substring of `record` at least LONG_QUOTE_MIN (40) long.
    A shorter «…» span is not a quote at all — the citation gate treats it as a
    search term, and the whole fixture would fail for the wrong reason."""
    i = record.index(needle)
    out = record[i:i + max(length, 45)]
    assert len(out) >= 40, out
    return out


Q_FW = q(FW, '"Origin"')
# Deliberately stops before any number: the К-1 candidate block quotes a NAME,
# so a legitimate rejected candidate needs no decode and the gate must stay quiet.
Q_FW_RULE = FW[FW.index('"RuleName"'):FW.index('"Origin"') + len('"Origin"')]
assert len(Q_FW_RULE) >= 40, Q_FW_RULE
Q_PROFILE = q(PROFILE, '"File"')
Q_BOOT = q(BOOT, '"EventID"')
Q_LOGIN = q(LOGIN, '"User"')
OTHER = ('{"Event":{"System":{"TimeCreated":{"#attributes":'
         '{"SystemTime":"2021-05-08T15:04:20.000000Z"}}},'
         '"EventData":{"TargetUserName":"owner"}}}')
PNP = ('{"Event":{"System":{"TimeCreated":{"#attributes":'
       '{"SystemTime":"2021-05-08T15:04:03.000000Z"}}},'
       '"EventData":{"DeviceInstanceId":"ROOT\\\\ACPI_HAL\\\\0000"}}}')

OWN_OK = ("| IPSERVER\\\\root | 2021-05-08T15:04:51Z | profile.jsonl:1 "
          "«" + Q_PROFILE + "» | профиль | владелец | — |")


# Long enough to clear LONG_QUOTE_MIN — a two-character quote is a search
# term, not evidence, and the citation gate says so.
QUOTES = {"fw.jsonl": Q_FW, "profile.jsonl": Q_PROFILE,
          "system.jsonl": Q_BOOT, "lsm.jsonl": Q_LOGIN}


def coverage_for(corpus):
    """Every file on disk needs a row — the completeness check already blocks on
    a missing one, and these fixtures must be clean for the RIGHT reason."""
    out = []
    for fn in sorted(os.listdir(corpus)):
        rec = open(os.path.join(corpus, fn), encoding="utf-8").readline()
        out.append("| %s | наблюдение | %s:1 «%s» |"
                   % (fn, fn, QUOTES.get(fn) or q(rec, '"EventData"')))
    return "\n".join(out)


def report(finding_body, ownership=OWN_OK, extra="", coverage=""):
    """A minimal report that is structurally valid for report_evidence()."""
    own = ""
    if ownership is not None:
        own = ("\n## Принадлежность учётных записей\n\n"
               "| учётная запись | первое появление | path:line «цитата» | как | вывод | раньше |\n"
               "|---|---|---|---|---|---|\n" + ownership + "\n")
    return ("# Отчёт\n\n## Находки\n\n### Н-1 · тест\n\n"
            "**что сломано:** " + finding_body + "\n\n"
            "**улики:**\n\n- fw.jsonl:1 — «" + Q_FW + "»\n\n"
            "**атрибуция: установлена**\n\n**исход: успех**\n"
            + extra +
            "\n## Отклонённые кандидаты\n\n### К-1 · нет\n\n"
            "**исход: норма**\n\n- fw.jsonl:1 — «" + Q_FW_RULE + "»\n"
            + own +
            "\n## Покрытие\n\n| путь | статус | деталь |\n|---|---|---|\n"
            + (coverage or "| fw.jsonl | наблюдение | fw.jsonl:1 «\"Action\":2» |")
            + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fix6-")
        self.corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(self.corpus)
        self._w("fw.jsonl", FW + "\n" + FW3 + "\n")
        self._w("profile.jsonl", PROFILE + "\n")
        self._w("system.jsonl", BOOT + "\n")
        self._w("lsm.jsonl", LOGIN + "\n")
        # a private, writable copy of the reference dir so table tests can
        # mangle it without touching the committed skill
        self.ref = os.path.join(self.tmp, "reference")
        shutil.copytree(os.path.join(V37, "reference"), self.ref)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _w(self, name, text):
        with open(os.path.join(self.corpus, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def blocks(self, text):
        """-> (enum result, ownership result) via the REAL report_evidence()."""
        checked = cc.check(text, self.corpus, require_quote=True)
        ev = cc.report_evidence(text, checked)
        return ev

    def enum(self, body, **kw):
        lines = body.splitlines()
        st = cc.structural_mask(lines)
        return cc.enum_decode_check(body, [("Н-1", 1, len(lines))], st,
                                    kw.get("reference_dir", self.ref))


# --------------------------------------------------------------------------
# 6a — the enum decode
# --------------------------------------------------------------------------
class EnumDecode(Base):
    def test_true_positive_the_v37_wording(self):
        """«действие 2/3 (разрешить)» — the exact sentence that passed v37."""
        r = self.enum("Action=2 quoted; действие 2/3 (разрешить).")
        kinds = sorted((i["field"], i["value"], i["kind"]) for i in r["items"])
        self.assertIn(("action", 2, "missing_decode"), kinds)
        self.assertIn(("action", 3, "missing_decode"), kinds)
        self.assertTrue(r["blocking"] >= 2)

    def test_true_positive_wrong_decode_names_the_real_meaning(self):
        r = self.enum("Action=2 (разрешить)")
        it = [i for i in r["items"] if i["field"] == "action"][0]
        self.assertEqual(it["kind"], "wrong_decode")
        self.assertEqual(it["means"], 3)
        self.assertEqual(it["expected"], "блокировать")

    def test_legitimate_correct_decode_does_not_fire(self):
        r = self.enum("Action=2 (блокировать), затем Action=3 (разрешить); "
                      "Origin=1 (локальное правило); Profiles=4 (общедоступная сеть); "
                      "Protocol=6 (tcp)")
        self.assertEqual(r["items"], [])
        self.assertEqual(r["blocking"], 0)

    def test_space_separator_and_alias_are_accepted(self):
        self.assertEqual(self.enum("LogonType 10 (rdp)")["items"], [])

    def test_no_enum_field_no_fire(self):
        """Over-firing gets a gate disabled: a bare number is not an enum."""
        r = self.enum("В журнале 33456 событий 4625 за 2021-06-01, EventID 21, "
                      "Level 4, порт 3389, строки 443-450.")
        self.assertEqual(r["items"], [])

    def test_fence_is_an_example_not_a_decode(self):
        body = "Action=2 упомянут.\n```\nAction=2 (блокировать)\n```\n"
        r = self.enum(body)
        self.assertEqual([i["kind"] for i in r["items"]], ["missing_decode"])

    def test_unknown_value_blocks_and_points_at_the_table(self):
        r = self.enum("Action=9 (что-то)")
        self.assertEqual([i["kind"] for i in r["items"]], ["unknown_value"])

    def test_unknown_decode_must_come_from_the_table(self):
        r = self.enum("LogonType=3 (сетевое подключение)")
        self.assertEqual([i["kind"] for i in r["items"]], ["unknown_decode"])

    # ---- gaming
    def test_gaming_tsv_cannot_redefine_a_builtin(self):
        with open(os.path.join(self.ref, "enum-tables.tsv"), "a",
                  encoding="utf-8") as fh:
            fh.write("Action\t2\tразрешить\tallow\thttps://example/spec\n")
        r = self.enum("Action=2 (разрешить)")
        self.assertIn("table_conflict",
                      [p["kind"] for p in r["table_problems"]])
        self.assertTrue(r["blocking"] >= 1)

    def test_gaming_tsv_row_without_a_source_is_refused(self):
        with open(os.path.join(self.ref, "enum-tables.tsv"), "a",
                  encoding="utf-8") as fh:
            fh.write("Action\t9\tчто-то\t\t\n")
        r = self.enum("Action=2 (блокировать)")
        self.assertIn("table_no_source",
                      [p["kind"] for p in r["table_problems"]])

    def test_gaming_growth_path_works_when_honest(self):
        with open(os.path.join(self.ref, "enum-tables.tsv"), "a",
                  encoding="utf-8") as fh:
            fh.write("Action\t9\tтестовое значение\t\t[MS-FASP] 2.2.30\n")
        self.assertEqual(self.enum("Action=9 (тестовое значение)")["items"], [])

    def test_gaming_deleting_the_quote_does_not_silence_the_claim(self):
        """G5, the one attack that worked on the first version of this gate."""
        r = self.enum("Правило открыто, действие 2.")
        self.assertEqual([i["kind"] for i in r["items"]], ["missing_decode"])

    # ---- fail closed
    def test_fail_closed_missing_table(self):
        os.unlink(os.path.join(self.ref, "enum-tables.tsv"))
        r = self.enum("Action=2 (блокировать)")
        self.assertIn("table_unreadable",
                      [p["kind"] for p in r["table_problems"]])
        self.assertTrue(r["blocking"] >= 1)

    def test_fail_closed_unparseable_table(self):
        with open(os.path.join(self.ref, "enum-tables.tsv"), "ab") as fh:
            fh.write(b"\xff\xfe\x00nope\n")
        r = self.enum("Action=2 (блокировать)")
        self.assertIn("table_unreadable",
                      [p["kind"] for p in r["table_problems"]])

    def test_fail_closed_malformed_row(self):
        with open(os.path.join(self.ref, "enum-tables.tsv"), "a",
                  encoding="utf-8") as fh:
            fh.write("Action\tнеЧисло\tчто-то\t\tисточник\n")
        r = self.enum("Action=2 (блокировать)")
        self.assertIn("table_malformed",
                      [p["kind"] for p in r["table_problems"]])

    def test_no_canonical_decode_contains_a_paren(self):
        """A decode with a `)` could never be written in `Field=N (decode)`."""
        for field, value, canon, _al in cc.ENUM_BUILTIN_ROWS:
            self.assertNotIn("(", canon, "%s=%s" % (field, value))
            self.assertNotIn(")", canon, "%s=%s" % (field, value))


# --------------------------------------------------------------------------
# 6b — ownership before intruder
# --------------------------------------------------------------------------
class Ownership(Base):
    def own(self, text):
        lines = text.splitlines()
        st = cc.structural_mask(lines)
        blocks = ([("Н-%s" % n, lo, hi) for n, lo, hi in cc.finding_blocks(text, st)])
        return cc.ownership_check(text, blocks, self.corpus, st)

    def test_true_positive_no_ownership_section(self):
        r = self.own(report("Вход `IPSERVER\\root` — признак постороннего доступа.",
                            ownership=None))
        self.assertTrue(r["missing_section"])
        self.assertEqual(r["missing_rows"], ["root"])
        self.assertTrue(r["blocking"] >= 2)

    def test_legitimate_correct_determination_does_not_fire(self):
        r = self.own(report("Вход `IPSERVER\\root` с внешнего адреса."))
        self.assertEqual(r["blocking"], 0, r)

    def test_trigger_is_account_driven_not_vocabulary_driven(self):
        """A characterisation the vocabulary misses must still be caught."""
        r = self.own(report("Вход `IPSERVER\\root` — это гость из интернета.",
                            ownership=None))
        self.assertEqual(r["characterised"], [])      # vocabulary missed it
        self.assertEqual(r["missing_rows"], ["root"])  # requirement still fired
        self.assertTrue(r["blocking"] >= 1)

    def test_contradiction_intruder_prose_versus_owner_row(self):
        r = self.own(report("`IPSERVER\\root` — признак постороннего доступа."))
        self.assertEqual([c["account"] for c in r["contradiction"]], ["root"])

    def test_not_earliest_is_checked_against_the_corpus(self):
        row = ("| IPSERVER\\\\root | 2021-05-09T21:43:09Z | lsm.jsonl:1 "
               "«" + Q_LOGIN + "» | удалённый вход | владелец | — |")
        r = self.own(report("Вход `IPSERVER\\root`.", ownership=row))
        self.assertEqual([x["account"] for x in r["not_earliest"]], ["root"])

    def test_evidence_must_actually_name_the_account(self):
        row = ("| IPSERVER\\\\root | 2021-05-08T15:04:30Z | system.jsonl:1 "
               "«" + Q_BOOT + "» | профиль | владелец | — |")
        r = self.own(report("Вход `IPSERVER\\root`.", ownership=row))
        self.assertTrue(r["bad_evidence"])

    def test_timestamp_must_match_the_record(self):
        row = ("| IPSERVER\\\\root | 2021-05-08T15:04:52Z | profile.jsonl:1 "
               "«" + Q_PROFILE + "» | профиль | владелец | — |")
        r = self.own(report("Вход `IPSERVER\\root`.", ownership=row))
        self.assertTrue(r["bad_evidence"])

    def test_intruder_verdict_needs_an_earlier_owner(self):
        row = OWN_OK.replace("| владелец |", "| посторонний |")
        r = self.own(report("Вход `IPSERVER\\root`.", ownership=row))
        self.assertEqual([x["account"] for x in r["no_earlier_owner"]], ["root"])

    def test_intruder_verdict_passes_with_a_verified_earlier_owner(self):
        self._w("other.jsonl", OTHER + "\n")
        rows = ("| owner | 2021-05-08T15:04:20Z | other.jsonl:1 "
                "«" + q(OTHER, '"SystemTime"') + "» | создание | владелец | — |\n"
                "| IPSERVER\\\\root | 2021-05-08T15:04:51Z | profile.jsonl:1 "
                "«" + Q_PROFILE + "» | профиль | посторонний | owner |")
        r = self.own(report("Вход `IPSERVER\\root` и `owner`.", ownership=rows))
        self.assertEqual(r["no_earlier_owner"], [])
        self.assertEqual(r["blocking"], 0, r)

    def test_cannot_determine_path_is_cheap_and_legal(self):
        row = "| ghost | не определяется | — | неизвестно | не определяется | — |"
        r = self.own(report("Учётка `HOST\\ghost` в журнале.", ownership=row))
        self.assertEqual(r["blocking"], 0, r)

    def test_gaming_cannot_determine_is_a_lie_when_the_corpus_knows(self):
        row = ("| IPSERVER\\\\root | не определяется | — | неизвестно "
               "| не определяется | — |")
        r = self.own(report("Вход `IPSERVER\\root`.", ownership=row))
        self.assertEqual([x["account"] for x in r["false_undetermined"]], ["root"])

    def test_gaming_hiding_the_name_in_prose_does_not_help(self):
        """G16: the account is named by the EVIDENCE QUOTE, not only by prose."""
        text = report("Учётная запись первого администратора вошла по RDP.",
                      ownership=None,
                      extra="\n- lsm.jsonl:1 — «" + Q_LOGIN + "»\n")
        r = self.own(text)
        self.assertEqual(r["missing_rows"], ["root"])

    def test_gaming_section_in_a_fence_is_not_a_section(self):
        text = report("Вход `IPSERVER\\root`.", ownership=None)
        text += "\n```\n## Принадлежность учётных записей\n\n" + OWN_OK + "\n```\n"
        r = self.own(text)
        self.assertTrue(r["missing_section"])

    def test_bad_kind_and_bad_verdict_are_closed_sets(self):
        row = OWN_OK.replace("| профиль |", "| как-то так |").replace(
            "| владелец |", "| наверное свой |")
        r = self.own(report("Вход `IPSERVER\\root`.", ownership=row))
        self.assertTrue(r["invalid_kind"] and r["invalid_verdict"])

    def test_malformed_row_blocks(self):
        r = self.own(report("Вход `IPSERVER\\root`.",
                            ownership="| IPSERVER\\\\root | владелец |"))
        self.assertTrue(r["malformed"])

    def test_well_known_principals_are_not_accounts(self):
        """A quote truncated to «ITY\\LOCAL SERVICE» must not invent `LOCAL`."""
        r = self.own(report("Строка «ITY\\\\LOCAL SERVICE, Processid 3012»."
                            , ownership=None))
        self.assertNotIn("local", r["cited_accounts"])

    def test_a_substring_is_not_an_account(self):
        """`ROOT\\ACPI_HAL\\0000` is a PnP device path, not the user `root`."""
        self._w("pnp.jsonl", PNP + "\n")
        found, problems = cc.corpus_first_appearance(self.corpus, ["root"])
        self.assertEqual(problems, [])
        self.assertEqual(found["root"][0], "2021-05-08T15:04:51")

    # ---- fail closed
    def test_fail_closed_unopenable_corpus_file(self):
        os.symlink("/nope/nothing", os.path.join(self.corpus, "broken.jsonl"))
        r = self.own(report("Вход `IPSERVER\\root`."))
        self.assertTrue(r["scan_problems"])
        self.assertTrue(r["blocking"] >= 1)

    def test_fail_closed_missing_corpus(self):
        found, problems = cc.corpus_first_appearance(
            os.path.join(self.tmp, "gone"), ["root"])
        self.assertTrue(problems)

    def test_fail_closed_exception_in_the_scan(self):
        found, problems = cc.corpus_first_appearance(self.corpus, ["root"])
        self.assertEqual(problems, [])
        orig = cc.looks_binary
        try:
            cc.looks_binary = lambda p: (_ for _ in ()).throw(RuntimeError("boom"))
            found, problems = cc.corpus_first_appearance(self.corpus, ["root"])
        finally:
            cc.looks_binary = orig
        self.assertTrue(problems)
        self.assertEqual(found, {})


# --------------------------------------------------------------------------
# THE WIRING. A correct function nothing calls on the real path is this repo's
# single most repeated failure. These are the tests the mutations must kill.
# --------------------------------------------------------------------------
class Wiring(Base):
    """Both checks must reach report_evidence()['blocking'] and the exit code."""

    #: the decodes Q_FW's four enum pairs require, in the one admissible form
    DECODES = ("Origin=1 (локальное правило), Protocol=6 (tcp), "
               "Action=2 (блокировать), Profiles=4 (общедоступная сеть). ")

    def rep(self, body, **kw):
        kw.setdefault("coverage", coverage_for(self.corpus))
        return report(body, **kw)

    def _run_cli(self, text, args=()):
        rp = os.path.join(self.tmp, "r.md")
        with open(rp, "w", encoding="utf-8") as fh:
            fh.write(text)
        return subprocess.run(
            [sys.executable, CITECHECK, rp, "--corpus", self.corpus,
             "--require-quote", "--json"] + list(args),
            capture_output=True, text=True)

    def test_enum_defect_reaches_report_evidence_blocking(self):
        ev = self.blocks(self.rep(
            self.DECODES.replace("Action=2 (блокировать)", "Action=2 (разрешить)")))
        self.assertTrue(ev["enum_decode"]["blocking"] >= 1)
        self.assertTrue(ev["blocking"] >= ev["enum_decode"]["blocking"])

    def test_ownership_defect_reaches_report_evidence_blocking(self):
        ev = self.blocks(self.rep(self.DECODES + "Вход `IPSERVER\\root`.",
                                  ownership=None))
        self.assertTrue(ev["ownership"]["blocking"] >= 1)
        self.assertTrue(ev["blocking"] >= ev["ownership"]["blocking"])

    def test_enum_defect_reaches_the_exit_code(self):
        clean = self._run_cli(self.rep(self.DECODES))
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        bad = self._run_cli(self.rep(
            self.DECODES.replace("Action=2 (блокировать)", "Action=2 (разрешить)")))
        self.assertEqual(bad.returncode, 1)
        d = json.loads(bad.stdout)
        self.assertTrue(d["blocking"] >= 1)
        self.assertTrue(d["report_evidence"]["enum_decode"]["blocking"] >= 1)

    def test_ownership_defect_reaches_the_exit_code(self):
        good = self._run_cli(self.rep(self.DECODES + "Вход `IPSERVER\\root`."))
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        p = self._run_cli(self.rep(self.DECODES + "Вход `IPSERVER\\root`.",
                                   ownership=None))
        self.assertEqual(p.returncode, 1)
        d = json.loads(p.stdout)
        self.assertTrue(d["report_evidence"]["ownership"]["blocking"] >= 1)

    def test_both_reach_the_ledger_total(self):
        """`_blocking_total()` must see the same number, not a second counter —
        defect #4 printed 160 where the gate printed 161."""
        bad = self.rep(
            self.DECODES.replace("Action=2 (блокировать)", "Action=2 (разрешить)"),
            ownership=None)
        ev = self.blocks(bad)
        d = {"summary": dict((v, 0) for v in cc.VERDICTS),
             "citations": [], "non_references": [],
             "outcomes": cc.outcomes_of(bad), "report_evidence": ev,
             "aggregates": {"blocking": 0, "items": [], "total": 0}}
        total = cc._blocking_total(d, bad)
        self.assertTrue(total >= ev["enum_decode"]["blocking"]
                        + ev["ownership"]["blocking"])


MUTATIONS = {
    "enum": ('+ enums["blocking"] + ownership["blocking"]',
             '+ 0 * enums["blocking"] + ownership["blocking"]'),
    "ownership": ('+ enums["blocking"] + ownership["blocking"]',
                  '+ enums["blocking"] + 0 * ownership["blocking"]'),
}


class MutationProof(unittest.TestCase):
    """Constraint 4: write the mutation that removes each check's contribution
    to the exit code, RUN it, and prove a test dies. A gate whose accounting can
    be deleted without a red test is not wired."""

    def _mutate(self, which):
        tmp = tempfile.mkdtemp(prefix="fix6-mut-")
        tools = os.path.join(tmp, "tools")
        shutil.copytree(os.path.join(V37, "tools"), tools)
        shutil.copytree(os.path.join(V37, "reference"),
                        os.path.join(tmp, "reference"))
        p = os.path.join(tools, "citecheck.py")
        src = open(p, encoding="utf-8").read()
        old, new = MUTATIONS[which]
        assert src.count(old) == 1, "the wired line moved: %r" % old
        open(p, "w", encoding="utf-8").write(src.replace(old, new))
        return tmp, p

    def _suite_against(self, citecheck):
        env = dict(os.environ, SHERLOCK_CITECHECK=citecheck)
        return subprocess.run([sys.executable, os.path.abspath(__file__)],
                              capture_output=True, text=True, env=env)

    def test_suite_is_green_against_the_real_module(self):
        p = self._suite_against(os.path.join(V37, "tools", "citecheck.py"))
        self.assertEqual(p.returncode, 0, p.stderr[-3000:])

    def test_removing_the_enum_contribution_kills_a_test(self):
        tmp, p = self._mutate("enum")
        try:
            r = self._suite_against(p)
            self.assertNotEqual(r.returncode, 0,
                                "MUTANT SURVIVED: enum blocking is not wired "
                                "to the exit code")
            self.assertIn("test_enum_defect_reaches_the_exit_code", r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_removing_the_ownership_contribution_kills_a_test(self):
        tmp, p = self._mutate("ownership")
        try:
            r = self._suite_against(p)
            self.assertNotEqual(r.returncode, 0,
                                "MUTANT SURVIVED: ownership blocking is not "
                                "wired to the exit code")
            self.assertIn("test_ownership_defect_reaches_the_exit_code", r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    # A mutant run re-enters this file with SHERLOCK_CITECHECK set; it must run
    # everything EXCEPT MutationProof, or it would recurse forever.
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    classes = [EnumDecode, Ownership, Wiring]
    if "SHERLOCK_CITECHECK" not in os.environ:
        classes.append(MutationProof)
    for c in classes:
        suite.addTests(loader.loadTestsFromTestCase(c))
    sys.exit(0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()
             else 1)
