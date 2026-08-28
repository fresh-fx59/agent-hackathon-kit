#!/usr/bin/env python3
"""Внешний код отказа — не причина отказа. v42, fix 5.

WHY, measured on the delivered report of the paid run 20260827T173511Z-v41,
finding Н-1:

    корневая причина: … все попытки отклонены с кодом 0xc000006d «имя
    пользователя или пароль неверны».
    … отказы имеют статус 0xc000006d — словарные имена не подходят к
    существующим учёткам

That last clause is false and load-bearing: it is how the report argued the
brute force never touched a real account. `Status=0xc000006d` is
STATUS_LOGON_FAILURE — the deliberately uninformative OUTER code. The reason
lives in `SubStatus`, and the real corpus says the opposite:

    Status     0xc000006d = 33453,  0xc000006e = 3
    SubStatus  0xc0000064 = 25355 (нет такой учётной записи)
               0xc000006a =  8098 (НЕВЕРНЫЙ ПАРОЛЬ — имя существует)
               0xc0000072 =     3 (учётная запись отключена)
    TargetUserName = АДМИНИСТРАТОР: 8027, ALL of them 0xc000006a

No gate saw it, because neither field was in the enum table at all, so the
decode discipline of fix 6a never engaged on the field carrying the error.

This suite pins both halves: the table rows (each decoding through `citecheck`
exactly as its Microsoft source says), and the semantic gate — including the
three ways it must NOT fire, because a gate that fires on everything gets
switched off, and the fail-closed paths, because «exit 0» must never mean «I
could not look».

    python3 tools/tests/test_substatus_semantics_v42.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
V42 = SHERLOCK / "skills" / "v42"
REFERENCE = V42 / "reference"
PROFILE = REFERENCE / "logon-failure-reason.json"
TSV = REFERENCE / "enum-tables.tsv"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = load("citecheck_v42_substatus", V42 / "tools" / "citecheck.py")

DEFECT = "enum_outer_status_read_as_reason"
UNREADABLE = "logon-reason-profile-unreadable"

OUTER = "0xc000006d"
NO_SUCH_USER = "0xc0000064"
WRONG_PASSWORD = "0xc000006a"
DISABLED = "0xc0000072"
ADMIN = "АДМИНИСТРАТОР"

# The real split, digit for digit. 25355 + 8098 + 3 = 33456.
N_NO_SUCH_USER = 25355
N_WRONG_PASSWORD = 8098
N_DISABLED = 3
N_ADMIN = 8027


def rec(substatus, user, status=OUTER, ip="45.168.116.7"):
    ed = {"TargetUserName": user, "Status": status, "LogonType": 3,
          "IpAddress": ip}
    if substatus is not None:
        ed["SubStatus"] = substatus
    return json.dumps({"Event": {
        "System": {"EventID": 4625, "Channel": "Security",
                   "TimeCreated": {"#attributes": {
                       "SystemTime": "2021-06-01T18:36:04.949933Z"}}},
        "EventData": ed}}, ensure_ascii=False)


def write(root, name, lines):
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def block(text):
    """A finding block, plus the block list `report_evidence` would build."""
    body = "### Н-1 · перебор паролей RDP\n" + text + "\n"
    return body, [("Н-1", 1, len(body.splitlines()))]


def grade(root, text, **kw):
    body, blocks = block(text)
    return cc.logon_reason_check(body, blocks, root, **kw)


class Corpus(unittest.TestCase):
    """The v41 corpus shape, built once — 33 456 rows is cheap, not free."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = cls.tmp.name
        lines = []
        # 8098 wrong-password rows, of which 8027 are against АДМИНИСТРАТОР —
        # the account that really exists and really was being brute-forced.
        for i in range(N_ADMIN):
            lines.append(rec(WRONG_PASSWORD, ADMIN))
        for i in range(N_WRONG_PASSWORD - N_ADMIN):
            lines.append(rec(WRONG_PASSWORD, "root"))
        for i in range(N_NO_SUCH_USER):
            lines.append(rec(NO_SUCH_USER, "dict%05d" % i))
        for i in range(N_DISABLED):
            lines.append(rec(DISABLED, "guest"))
        write(cls.root, "Security.jsonl", lines)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class RegressionOnTheRealNumbers(Corpus):
    """The delivered sentence, refused; the honest restatement, accepted."""

    DELIVERED = ("отказы имеют статус 0xc000006d — словарные имена не подходят "
                 "к существующим учёткам")

    def test_delivered_claim_is_refused(self):
        r = grade(self.root, self.DELIVERED)
        self.assertEqual(r["blocking"], 1, r)
        it = r["items"][0]
        self.assertEqual(it["kind"], DEFECT)
        self.assertEqual(it["outer"], OUTER)
        self.assertEqual(it["reason"], "no-such-user")
        self.assertEqual(it["total"],
                         N_NO_SUCH_USER + N_WRONG_PASSWORD + N_DISABLED)
        self.assertEqual(it["support"], N_NO_SUCH_USER)

    def test_refusal_names_the_honest_evidence(self):
        """Deleting the claim instead of citing it is the v37 regression."""
        out = cc.render_logon_reason(grade(self.root, self.DELIVERED))
        for value, n in ((NO_SUCH_USER, N_NO_SUCH_USER),
                         (WRONG_PASSWORD, N_WRONG_PASSWORD),
                         (DISABLED, N_DISABLED)):
            self.assertIn(value, out)
            self.assertIn(str(n), out)
        self.assertIn("неверный пароль", out)          # the decode, from the TSV
        self.assertIn("count(Event.EventData.SubStatus=%s, "
                      "Event.EventData.Status=%s)" % (WRONG_PASSWORD, OUTER),
                      out)
        self.assertIn("не удаляй утверждение", out)

    def test_printed_aggregates_are_evaluated_not_assumed(self):
        """Every citation the refusal prints is one `citecheck` itself grades."""
        r = grade(self.root, self.DELIVERED)
        cites = [b["citation"] for b in r["items"][0]["breakdown"]]
        self.assertTrue(all(cites), cites)
        report = "### Н-1 · x\n" + "\n".join("улики: " + c for c in cites) + "\n"
        graded = cc.aggregates_check(report, self.root)
        self.assertEqual([i["verdict"] for i in graded["items"]],
                         ["ok"] * len(cites), graded)

    def test_honest_restatement_passes(self):
        honest = ("SubStatus 0xc0000064 (нет такой учётной записи) — 25355, "
                  "SubStatus 0xc000006a (неверный пароль) — 8098 попыток "
                  "против существующих учёток, SubStatus 0xc0000072 "
                  "(учётная запись отключена) — 3")
        self.assertEqual(grade(self.root, honest)["blocking"], 0)

    def test_paraphrase_of_the_same_false_claim_is_refused(self):
        """Different Russian words, same lie — this is not a one-sentence gate.

        Shares no phrase with the delivered sentence: no «словарные», no «не
        подходят», no «существующим учёткам»."""
        para = ("код 0xc000006d говорит сам за себя: перебор шёл мимо "
                "существующих учёток")
        r = grade(self.root, para)
        self.assertEqual(r["blocking"], 1, r)
        self.assertEqual(r["items"][0]["reason"], "no-such-user")

    def test_the_opposite_lie_is_refused_by_the_same_code(self):
        """The evidence side is arithmetic, not a string: «all wrong passwords»
        is refused over the same corpus, and it is not the sentence we shipped."""
        r = grade(self.root, "при статусе 0xc000006d пароль неверен у всех")
        self.assertEqual(r["blocking"], 1, r)
        self.assertEqual(r["items"][0]["reason"], "wrong-password")
        self.assertEqual(r["items"][0]["support"], N_WRONG_PASSWORD)


class AdministratorAccount(Corpus):
    """8027 tries, every one a wrong password against a real, enabled account."""

    def test_no_such_user_claim_about_the_admin_is_refused(self):
        r = grade(self.root, "по имени АДМИНИСТРАТОР статус 0xc000006d, "
                             "такой учётной записи нет")
        self.assertEqual(r["blocking"], 1, r)
        it = r["items"][0]
        self.assertEqual(it["subject"], ADMIN)
        self.assertEqual(it["total"], N_ADMIN)
        self.assertEqual(it["support"], 0)
        out = cc.render_logon_reason(r)
        self.assertIn(ADMIN, out)
        self.assertIn(str(N_ADMIN), out)

    def test_a_true_claim_about_the_admin_passes(self):
        """Scoping cuts both ways: for THIS account the corpus is unanimous."""
        r = grade(self.root, "по имени АДМИНИСТРАТОР статус 0xc000006d, "
                             "пароль неверен")
        self.assertEqual(r["blocking"], 0, cc.render_logon_reason(r))


class MustNotFire(Corpus):
    """A gate that fires on honest reports gets switched off."""

    def test_hedged_reading_of_the_outer_code_passes(self):
        """«имя пользователя или пароль неверны» IS what 0xc000006d means."""
        r = grade(self.root, "все попытки отклонены с кодом 0xc000006d "
                             "«имя пользователя или пароль неверны»")
        self.assertEqual(r["blocking"], 0, cc.render_logon_reason(r))

    def test_explicit_hedge_passes(self):
        r = grade(self.root, "статус 0xc000006d не уточняет причину: "
                             "существует имя или нет, по нему не видно")
        self.assertEqual(r["blocking"], 0, cc.render_logon_reason(r))

    def test_a_sentence_that_already_cites_substatus_passes(self):
        r = grade(self.root, "при статусе 0xc000006d SubStatus 0xc0000064 "
                             "говорит, что таких учётных записей нет")
        self.assertEqual(r["blocking"], 0, cc.render_logon_reason(r))

    def test_no_reason_claim_at_all_passes(self):
        r = grade(self.root, "все 33456 отказов имеют статус 0xc000006d")
        self.assertEqual(r["blocking"], 0, cc.render_logon_reason(r))

    def test_prose_outside_a_finding_block_is_not_graded(self):
        text = "отказы имеют статус 0xc000006d — таких учётных записей нет\n"
        r = cc.logon_reason_check(text, [], self.root)
        self.assertEqual(r["blocking"], 0)

    def test_corpus_without_the_inner_field_says_nothing(self):
        """No SubStatus in the records: this gate has not been given an answer,
        and inventing one would be the same sin in the other direction."""
        d = tempfile.mkdtemp()
        try:
            write(d, "Security.jsonl",
                  [rec(None, "u%d" % i) for i in range(50)])
            r = grade(d, "отказы имеют статус 0xc000006d — таких учётных "
                         "записей нет")
            self.assertEqual(r["blocking"], 0, cc.render_logon_reason(r))
        finally:
            shutil.rmtree(d)


class FailClosed(Corpus):
    """A gate that cannot look must never answer «clean»."""

    CLAIM = "отказы имеют статус 0xc000006d — таких учётных записей нет"

    def test_missing_profile_blocks(self):
        r = grade(self.root, self.CLAIM,
                  profile_path=str(HERE / "no-such-profile.json"))
        self.assertEqual(r["blocking"], 1)
        self.assertEqual(r["problems"][0]["kind"], UNREADABLE)

    def test_unparseable_profile_blocks(self):
        d = tempfile.mkdtemp()
        try:
            bad = os.path.join(d, "logon-failure-reason.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{ not json")
            r = grade(self.root, self.CLAIM, profile_path=bad)
            self.assertEqual(r["blocking"], 1)
            self.assertEqual(r["problems"][0]["kind"], UNREADABLE)
        finally:
            shutil.rmtree(d)

    def test_key_short_profile_blocks(self):
        d = tempfile.mkdtemp()
        try:
            data = json.loads(PROFILE.read_text(encoding="utf-8"))
            data.pop("reasons")
            bad = os.path.join(d, "logon-failure-reason.json")
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            r = grade(self.root, self.CLAIM, profile_path=bad)
            self.assertEqual(r["blocking"], 1)
            self.assertIn("reasons", r["problems"][0]["text"])
        finally:
            shutil.rmtree(d)

    def test_bad_regex_in_profile_blocks(self):
        d = tempfile.mkdtemp()
        try:
            data = json.loads(PROFILE.read_text(encoding="utf-8"))
            data["reasons"][0]["prose"] = ["(unclosed"]
            bad = os.path.join(d, "logon-failure-reason.json")
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            r = grade(self.root, self.CLAIM, profile_path=bad)
            self.assertEqual(r["blocking"], 1)
        finally:
            shutil.rmtree(d)

    def test_unreadable_corpus_file_blocks(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "Security.jsonl")
            write(d, "Security.jsonl", [rec(NO_SUCH_USER, "u1")])
            os.chmod(p, 0)
            if os.access(p, os.R_OK):          # root ignores the mode
                self.skipTest("файл всё равно читается")
            r = grade(d, self.CLAIM)
            self.assertEqual(r["blocking"], 1)
            self.assertEqual(r["problems"][0]["kind"], "scan_error")
        finally:
            os.chmod(os.path.join(d, "Security.jsonl"), 0o600)
            shutil.rmtree(d)

    def test_missing_corpus_blocks(self):
        r = grade(os.path.join(self.root, "nope"), self.CLAIM)
        self.assertEqual(r["blocking"], 1)
        self.assertEqual(r["problems"][0]["kind"], "scan_error")

    def test_too_many_subjects_blocks(self):
        d = tempfile.mkdtemp()
        try:
            write(d, "Security.jsonl",
                  [rec(NO_SUCH_USER, "u%d" % i) for i in range(40)])
            data = json.loads(PROFILE.read_text(encoding="utf-8"))
            data["max_subjects"] = 5
            bad = os.path.join(d, "profile.json")
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            r = grade(d, self.CLAIM, profile_path=bad)
            self.assertEqual(r["blocking"], 1)
            self.assertEqual(r["problems"][0]["kind"], "too_many_subjects")
        finally:
            shutil.rmtree(d)


class EnumTableRows(unittest.TestCase):
    """Every row added to the TSV decodes through `citecheck`, in hex."""

    EXPECTED = {
        "0xc000005e": "нет доступных серверов входа",
        "0xc0000064": "нет такой учётной записи",
        "0xc000006a": "неверный пароль",
        "0xc000006d": "общий отказ входа",
        "0xc000006e": "ограничение учётной записи",
        "0xc000006f": "вход вне разрешённых часов",
        "0xc0000070": "вход с недопустимой рабочей станции",
        "0xc0000071": "срок действия пароля истёк",
        "0xc0000072": "учётная запись отключена",
        "0xc0000133": "расхождение часов с контроллером домена",
        "0xc000015b": "запрошенный тип входа не разрешён",
        "0xc0000193": "срок действия учётной записи истёк",
        "0xc0000224": "требуется смена пароля",
        "0xc0000234": "учётная запись заблокирована",
    }

    def setUp(self):
        self.table, self.problems = cc.enum_table()

    def test_table_loads_clean(self):
        self.assertEqual(self.problems, [])

    def test_every_added_pair_is_in_the_table_for_both_fields(self):
        for field in ("status", "substatus"):
            for hexval, decode in self.EXPECTED.items():
                key = (field, int(hexval, 16))
                self.assertIn(key, self.table, key)
                self.assertEqual(self.table[key]["canonical"], decode)
                self.assertTrue(self.table[key]["hex"])
                self.assertEqual(cc.enum_value_text(self.table, field,
                                                    int(hexval, 16)), hexval)

    def test_every_row_carries_a_source(self):
        seen = 0
        for line in TSV.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cells = line.split("\t")
            if cells[0].lower() == "field":
                continue
            self.assertEqual(len(cells), 5, line)
            self.assertTrue(cells[4].strip().startswith("http"), line)
            if cells[0] in ("Status", "SubStatus"):
                seen += 1
        self.assertEqual(seen, 2 * len(self.EXPECTED))

    def test_nothing_added_conflicts_with_the_locked_half(self):
        self.assertEqual(cc.enum_builtin_digest(), cc.ENUM_BUILTIN_SHA256)
        self.assertFalse([k for k in self.table
                          if k[0] in ("status", "substatus")
                          and k in cc.ENUM_BUILTIN])

    def test_hex_pair_quoted_without_a_decode_blocks(self):
        body, blocks = block('улика: `{"Status":"0xc000006d"}`')
        e = cc.enum_decode_check(body, blocks)
        self.assertEqual([i["kind"] for i in e["items"]], ["missing_decode"])
        self.assertEqual(e["items"][0]["display"], "0xc000006d")
        self.assertIn("0xc000006d (общий отказ входа)",
                      cc.render_enum_decode(e))

    def test_the_v41_wording_is_read_as_a_pair(self):
        """«отклонены с кодом 0xc000006d» — the words that were on the page."""
        body, blocks = block("все попытки отклонены с кодом 0xc000006d")
        e = cc.enum_decode_check(body, blocks)
        self.assertEqual([(i["field"], i["display"]) for i in e["items"]],
                         [("status", "0xc000006d")])

    def test_a_wrong_decode_of_the_outer_code_blocks(self):
        body, blocks = block("Status=0xc000006d (неверный пароль)")
        e = cc.enum_decode_check(body, blocks)
        self.assertEqual([i["kind"] for i in e["items"]], ["wrong_decode"])
        self.assertEqual(e["items"][0]["means"], "0xc000006a")

    def test_the_right_decode_passes(self):
        body, blocks = block("Status=0xc000006d (общий отказ входа) и "
                             "SubStatus=0xc000006a (неверный пароль)")
        self.assertEqual(cc.enum_decode_check(body, blocks)["blocking"], 0)

    def test_decimal_fields_never_swallow_a_hex_token(self):
        """The value FORM is bound to the field: `LogonType 0xc000006d` is not
        a logon type, and «код 3» is not a status code."""
        body, blocks = block("LogonType 0xc000006d, код 3")
        e = cc.enum_decode_check(body, blocks)
        self.assertEqual(e["items"], [])

    def test_decimal_enums_still_work(self):
        body, blocks = block('улика: `{"Action":2}` — действие 2/3 (разрешить)')
        e = cc.enum_decode_check(body, blocks)
        self.assertIn("wrong_decode", [i["kind"] for i in e["items"]])

    def test_a_hex_row_may_not_override_the_locked_half(self):
        d = tempfile.mkdtemp()
        try:
            shutil.copy(str(TSV), os.path.join(d, "enum-tables.tsv"))
            with open(os.path.join(d, "enum-tables.tsv"), "a",
                      encoding="utf-8") as fh:
                fh.write("action\t2\tразрешить\tallow\thttps://example.invalid\n")
            _t, problems = cc.enum_table(d)
            self.assertEqual([p["kind"] for p in problems], ["table_conflict"])
        finally:
            shutil.rmtree(d)


class Wiring(Corpus):
    """Three one-line mutations once disabled a gate while 30 tests stayed green."""

    CLAIM = "отказы имеют статус 0xc000006d — таких учётных записей нет"

    def test_counter_names_are_all_exercised(self):
        self.assertEqual(cc.reason_counter_keys(),
                         frozenset(("items", "profile_problems")))

    def test_report_evidence_carries_and_counts_it(self):
        body, _blocks = block(self.CLAIM)
        ev = cc.report_evidence(body, {"corpus": self.root})
        self.assertIn("logon_reason", ev)
        self.assertEqual(ev["logon_reason"]["blocking"], 1)
        base = cc.report_evidence(
            block("отказы имеют статус 0xc000006d")[0],
            {"corpus": self.root})["blocking"]
        self.assertGreater(ev["blocking"], base)
        rendered = cc.render_report_evidence(ev)
        self.assertIn("ПРИЧИНА ОТКАЗА ВХОДА", rendered)
        # both halves of fix 5 engage on the same line: the pair is quoted
        # without a decode, AND the claim rests on the outer code.
        self.assertIn("status=0xc000006d процитировано без расшифровки",
                      rendered)

    def test_cli_exits_nonzero_on_the_delivered_claim(self):
        d = tempfile.mkdtemp()
        try:
            report = os.path.join(d, "report.md")
            with open(report, "w", encoding="utf-8") as fh:
                fh.write(block(self.CLAIM)[0])
            p = subprocess.run(
                [sys.executable, str(V42 / "tools" / "citecheck.py"), report,
                 "--corpus", self.root],
                capture_output=True, text=True)
            self.assertEqual(p.returncode, 1, p.stdout[-3000:])
            self.assertIn("ПРИЧИНА ОТКАЗА ВХОДА", p.stdout)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
