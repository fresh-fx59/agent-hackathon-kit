#!/usr/bin/env python3
"""v56 gate gaps G1-G4 — regression tests from the REAL v55 r1 artifacts.

Spec: vault docs/run-reports/2026-09-26-v56-gate-gaps-spec.md.
Fixtures: fixtures/v56-r1/ (r1 checkpoint journal + checkpoint.json, rules.tsv,
worklist.tsv, report.md, repair-stream lines 820/821/867/868, the first
400-620 lines of four corpus files, and the recorded Opus judge replies).

Run against another package to show the gaps:  SHERLOCK_PKG=v55 python3 <this>
Every test is written as "the gap must be closed", so on v55 each one is RED.
No network, no LLM: the judge tests use recorded replies and a fake caller.
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

SHERLOCK = Path(__file__).resolve().parents[2]
PKG = os.environ.get("SHERLOCK_PKG", "v56")
TOOLS = SHERLOCK / "skills" / PKG / "tools"
FIX = Path(__file__).resolve().parent / "fixtures" / "v56-r1"
CORPUS = FIX / "corpus"


def load(name):
    path = TOOLS / (name + ".py")
    if not path.exists():
        raise AssertionError("%s has no %s.py — gap not closed" % (PKG, name))
    spec = importlib.util.spec_from_file_location("%s_%s_t56" % (name, PKG), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tool(name, *args):
    return subprocess.run([sys.executable, str(TOOLS / (name + ".py"))] + list(args),
                          capture_output=True, text=True, timeout=600)


def copy_work():
    d = Path(tempfile.mkdtemp(prefix="v56-r1-"))
    shutil.copytree(FIX / "work", d / "work")
    return d / "work"


# ---------------------------------------------------------------- G1 journal
def journal_block(work):
    """The Stop-path decision on the journal, for either package version.

    v56: checkpoint.verify_journal (called by stopcheck on every Stop).
    v55: the only journal read is _stage_pause_decision, which returns None
    (= "not a stage-pause claim", fall through to the final path that never
    reads checkpoint.jsonl) when pending_handoff is absent.
    """
    ck = load("checkpoint")
    if hasattr(ck, "verify_journal"):
        return ck.verify_journal(str(work))
    sc = load("stopcheck")
    decision = sc._stage_pause_decision({}, str(work), [], {}, None)
    return [] if decision is None else [("v55", str(decision))]


def _row(seq, prev_raw, rid=None, sha=None, authority=True):
    import hashlib
    row = {"at": "2026-09-26T00:00:0%dZ" % seq, "boundary_seq": seq, "stage": "draft",
           "stage_partial": False, "worklist_seals": {"worklist.tsv": "0" * 64},
           "handoff_receipt_id": rid or ("%032x" % seq),
           "handoff_sha256": sha or ("%064x" % seq)}
    if authority:
        row["worklist_authority"] = {"schema": 1}
    row["prev_sha256"] = (hashlib.sha256(prev_raw.encode()).hexdigest()
                          if prev_raw is not None else None)
    return json.dumps(row, sort_keys=True)


def synthetic_journal(n=3):
    d = Path(tempfile.mkdtemp(prefix="v56-journal-"))
    raws, prev = [], None
    for seq in range(1, n + 1):
        raw = _row(seq, prev)
        raws.append(raw)
        prev = raw
    (d / "checkpoint.jsonl").write_text("\n".join(raws) + "\n", encoding="utf-8")
    last = json.loads(raws[-1])
    (d / "checkpoint.json").write_text(json.dumps({
        "boundary_seq": n, "stage": "draft",
        "pending_handoff": {"id": last["handoff_receipt_id"],
                            "handoff_sha256": last["handoff_sha256"]}}), encoding="utf-8")
    return d, raws


class G1Journal(unittest.TestCase):
    def test_real_r1_forged_row_blocks(self):
        bad = journal_block(copy_work())
        codes = {c for c, _m in bad}
        self.assertIn("journal_no_receipt", codes, bad)
        self.assertIn("journal_blank_line", codes, bad)

    def test_receipt_deleted_from_checkpoint_json_still_verified(self):
        d, _ = synthetic_journal()
        row = json.loads((d / "checkpoint.json").read_text())
        row["pending_handoff"] = None          # what r1 did
        (d / "checkpoint.json").write_text(json.dumps(row))
        extra = _row(4, None).replace('"boundary_seq": 4', '"boundary_seq": 4')
        forged = json.loads(extra)
        for k in ("handoff_receipt_id", "handoff_sha256", "worklist_authority",
                  "prev_sha256"):
            forged.pop(k, None)
        with open(d / "checkpoint.jsonl", "a") as fh:
            fh.write(json.dumps(forged) + "\n")
        row["boundary_seq"] = 4
        (d / "checkpoint.json").write_text(json.dumps(row))
        self.assertIn("journal_no_receipt", {c for c, _ in journal_block(d)})

    def test_valid_journal_passes(self):
        d, _ = synthetic_journal()
        self.assertEqual(journal_block(d), [])

    def test_gap_dup_and_chain_break_block(self):
        d, raws = synthetic_journal()
        raws2 = [raws[0], raws[2]]              # seq gap + chain break
        (d / "checkpoint.jsonl").write_text("\n".join(raws2) + "\n")
        codes = {c for c, _ in journal_block(d)}
        self.assertTrue({"journal_seq", "journal_chain_broken"} <= codes, codes)
        d, raws = synthetic_journal(2)
        dup = json.loads(raws[1])
        dup["handoff_receipt_id"] = json.loads(raws[0])["handoff_receipt_id"]
        import hashlib
        dup["prev_sha256"] = hashlib.sha256(raws[0].encode()).hexdigest()
        (d / "checkpoint.jsonl").write_text(raws[0] + "\n" + json.dumps(dup, sort_keys=True) + "\n")
        self.assertIn("journal_dup_receipt", {c for c, _ in journal_block(d)})

    def test_checkpoint_seq_must_match_journal(self):
        d, _ = synthetic_journal()
        row = json.loads((d / "checkpoint.json").read_text())
        row["boundary_seq"] = 7
        (d / "checkpoint.json").write_text(json.dumps(row))
        self.assertIn("checkpoint_seq", {c for c, _ in journal_block(d)})

    def test_stopcheck_calls_the_verifier(self):
        text = (TOOLS / "stopcheck.py").read_text(encoding="utf-8")
        self.assertIn("verify_journal(out_dir)", text,
                      "stopcheck must verify the journal on every Stop")

    def test_journalcheck_flags_r1_stream_writes(self):
        jc = load("journalcheck")
        uses, _results = jc.read_stream([str(FIX / "stream-excerpt.jsonl")])
        hits = jc.control_writes(uses)
        self.assertEqual(len(hits), 2, hits)   # source lines 820 and 867
        self.assertTrue(all("checkpoint.json" in h[2] for h in hits))

    def test_journalcheck_receipts_both_ways(self):
        jc = load("journalcheck")
        ck = load("checkpoint")
        d, raws = synthetic_journal(2)
        rows = [json.loads(r) for r in raws]

        def stream(printed):
            lines = []
            for i, r in enumerate(printed):
                lines.append(json.dumps({"message": {"content": [{
                    "type": "tool_use", "id": "t%d" % i, "name": "run_shell_command",
                    "input": {"command": "python3 tools/checkpoint.py handoff --work w --done triage"}}]}}))
                lines.append(json.dumps({"message": {"content": [{
                    "type": "tool_result", "tool_use_id": "t%d" % i,
                    "content": "block\n" + ck.RECEIPT_LINE % (
                        r["handoff_receipt_id"], r["handoff_sha256"], r["boundary_seq"])}]}}))
            p = d / "s.jsonl"
            p.write_text("\n".join(lines) + "\n")
            return str(p)

        self.assertEqual(jc.check(str(d), [stream(rows)])["blocking"], 0)
        codes = {v["code"] for v in jc.check(str(d), [stream(rows[:1])])["violations"]}
        self.assertIn("receipt_unprinted", codes)
        extra = dict(rows[1], handoff_receipt_id="f" * 32, boundary_seq=3)
        codes = {v["code"] for v in jc.check(str(d), [stream(rows + [extra])])["violations"]}
        self.assertIn("receipt_unjournaled", codes)


# ---------------------------------------------------------------- G2 triage
class G2Triage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = copy_work()
        (work / "checkpoint.json").unlink()
        r = tool("triagecheck", "--worklist", str(work / "worklist.tsv"),
                 "--rules", str(work / "rules.tsv"), "--corpus", str(CORPUS), "--json")
        cls.d = json.loads(r.stdout)

    def test_r1_match_all_claims_refused(self):
        vac = [r["id"] for r in self.d["rules"]
               if any("не может быть ложным" in p for p in r["проблемы"])]
        self.assertEqual(sorted(vac), ["R%d" % i for i in range(1, 10)])

    def test_r1_stock_rule_reasons_refused(self):
        stock = [r["id"] for r in self.d["rules"]
                 if any("ничего не говорит" in p for p in r["проблемы"])]
        self.assertEqual(len(stock), 9)

    def test_r1_duplicate_header_receipts_refused(self):
        self.assertGreaterEqual(self.d["totals"].get("повторных цитат в квитанциях", 0), 30)

    def test_r1_scripted_verdict_reasons_refused(self):
        reasons = {g["reason"] for g in self.d.get("stock_reasons", [])}
        self.assertIn("no edge data", reasons)
        self.assertIn("no level data", reasons)

    def test_synthetic_real_claim_accepted(self):
        tc = load("triagecheck")
        self.assertEqual(tc.parse_claim("адрес~10.*"), [("адрес", "~", "10.*")])
        self.assertEqual(tc.parse_claim("код=401|403"), [("код", "=", "401|403")])
        for vac in ("адрес~*", "адрес~.", "адрес~.*", "адрес~?*"):
            with self.assertRaises(tc.Unevaluable):
                tc.parse_claim(vac)
        self.assertTrue(tc.why_is_stock("закрыто правилом R4"))
        self.assertFalse(tc.why_is_stock("служебные опросы WMI от SYSTEM каждые 5 минут"))


# ---------------------------------------------------------------- G3 quotes
MINI_LINES = (45, 59, 304, 325)   # real r1 report lines


class G3Quotes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lines = (FIX / "work" / "report.md").read_text(encoding="utf-8").splitlines()
        d = Path(tempfile.mkdtemp(prefix="v56-g3-"))
        p = d / "mini.md"
        p.write_text("\n".join(lines[n - 1] for n in MINI_LINES) + "\n", encoding="utf-8")
        r = tool("citecheck", str(p), "--corpus", str(CORPUS), "--require-quote", "--json")
        cls.v = {(c["path"].split("/")[-1], c["line"]): c["verdict"]
                 for c in json.loads(r.stdout)["citations"]}

    def test_bare_eventid_quotes_are_weak(self):
        self.assertEqual(self.v[("Microsoft-Windows-WMI-Activity-4Operational.jsonl", 238)],
                         "weak-quote")
        self.assertEqual(self.v[("Security.jsonl", 1)], "weak-quote")

    def test_informative_quotes_stay_ok(self):
        # v60: a strong typed quote on a JSON line is `typed-quote` (paste the
        # reference) — still "not weak", which is what this test guards
        strong = {"ok"} | ({"typed-quote"} if int(PKG.lstrip("v") or 0) >= 60 else set())
        self.assertIn(self.v[("System.jsonl", 263)], strong)
        self.assertIn(self.v[("Security.jsonl", 6)], strong)

    def test_quote_information_unit(self):
        cc = load("citecheck")
        weak = ['"EventID":5860', '"#text":7026', '"ThreadID":124',
                '"Computer":"IPSERVER"', '"EventData":null']
        strong = ['"UserName":"root"', '"IpAddress":"223.31.121.20"',
                  '"ServiceName":"3proxy tiny proxy server"',
                  '"Flags":257,"Active":1,"EdgeTraversal":3', '"ErrorCode":2147942402']
        for q in weak:
            self.assertFalse(cc.quote_information(q)[0], q)
        for q in strong:
            self.assertTrue(cc.quote_information(q, in_header=False)[0], q)

    def test_header_boilerplate_is_weak(self):
        cc = load("citecheck")
        rcm = str(CORPUS / "rendered" /
                  "Microsoft-Windows-TerminalServices-RemoteConnectionManager-4Operational.jsonl")
        line = open(rcm, encoding="utf-8").read().splitlines()[0]
        q = line[20:80]
        self.assertIsNotNone(cc.quote_is_weak("факт «%s»" % q, line, rcm))


# ---------------------------------------------------------------- G4 report
class G4Conclusions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = tool("reportcheck", str(FIX / "work" / "report.md"), "--json")
        cls.d = json.loads(r.stdout)

    def test_r1_uncited_absolutes_block(self):
        self.assertGreaterEqual(self.d["counts"].get("absolute_uncited", 0), 2)
        details = " ".join(x["detail"] for x in self.d["defects"]
                           if x["defect"] == "absolute_uncited")
        self.assertIn("LogonType=5", details)
        self.assertIn("LogonType=2", details)

    def test_r1_success_by_owner_blocks(self):
        hits = [x for x in self.d["defects"] if x["defect"] == "success_by_owner"]
        self.assertEqual(len(hits), 1)
        self.assertIn("Н-2", hits[0]["where"])

    def _check(self, text):
        rc = load("reportcheck")
        contract = rc.load_contract(rc.DEFAULT_CONTRACT)
        sections = rc.split_sections(text, contract)
        return (rc.check_absolute_claims(sections, contract)
                + rc.check_success_by_owner(sections, contract))

    def test_synthetic_absolute_with_aggregate_passes(self):
        base = "### Н-1 · x\n\nчем опровергал: все 4624 — LogonType=5.\n"
        self.assertEqual(len(self._check(base)), 1)
        ok = base + "> агрегат: Security.jsonl · count(EventID=4624) = 5 · `jq`\n"
        self.assertEqual(self._check(ok), [])
        self.assertEqual(self._check("### Н-1 · x\n\nя прочитал запись, а не только её класс.\n"), [])
        if PKG not in ("v56", "v57"):
            self.assertEqual(self._check("### Н-1 · x\n\n**чем опровергал:** Прочитаны все записи Security.jsonl любым парсером JSON.\n"), [])
            self.assertEqual(len(self._check("### Н-1 · x\n\nпроверены все записи 4624 — входы LogonType=2 не обнаружены.\n")), 1)

    def test_synthetic_success_needs_an_outsider(self):
        table = ("## Принадлежность учётных записей\n\n| учётная запись | вывод |\n|---|---|\n"
                 "| root | владелец |\n| ADMINI | посторонний |\n")
        owner = "### Н-1 · x\n\nустановил root.\n\nисход: успех\n\n" + table
        self.assertEqual([x["defect"] for x in self._check(owner)], ["success_by_owner"])
        alien = "### Н-1 · x\n\nвошёл ADMINI, потом root.\n\nисход: успех\n\n" + table
        self.assertEqual(self._check(alien), [])

    def test_inline_aggregate_is_graded(self):
        cc = load("citecheck")
        got = cc.agg_extract("- [!PROVEN] 12 IP — агрегат: Security.jsonl · distinct(a) = 12\n")
        self.assertEqual(len(got), 1, "an inline агрегат: must be graded, not skipped")


# ---------------------------------------------------------------- G4 judge
class G4Judge(unittest.TestCase):
    def test_recorded_opus_verdicts(self):
        jd = load("judge")
        want = {"r1": "fail", "r6": "pass", "stc": "pass"}
        for name, verdict in want.items():
            d = json.loads((FIX / "judge" / (name + ".json")).read_text())
            self.assertEqual(d["model"], ["claude-opus-5-5"])
            obj = jd.validate({"verdict": d["verdict"], "verdict_check": d["verdict_check"],
                               "claims": d["claims"]})
            self.assertEqual(jd.decide(obj)[0], verdict, name)
        r1 = json.loads((FIX / "judge" / "r1.json").read_text())
        self.assertFalse(r1["verdict_check"]["follows"])

    def test_fail_closed(self):
        jd = load("judge")
        d = Path(tempfile.mkdtemp(prefix="v56-judge-"))
        rep = d / "r.md"
        rep.write_text("## ВЕРДИКТ\n\nОтвет: атаковали, но не доказано\n", encoding="utf-8")
        cases = [
            ({"ANTHROPIC_BASE_URL": "https://linkapi.example/v1"}, None),
            ({"ANTHROPIC_API_KEY": "sk-metered"}, None),
            ({}, lambda *a: ("not json at all", ["claude-opus-5-5"], {})),
            ({}, lambda *a: ('{"verdict":"pass","claims":[]}', ["m"], {})),
            ({}, lambda *a: (_ for _ in ()).throw(RuntimeError("quota"))),
        ]
        for env, caller in cases:
            res = jd.run(str(rep), str(CORPUS), str(d / "j.json"), env=env,
                         caller=caller or (lambda *a: ("{}", [], {})))
            self.assertEqual(res["status"], "judge_unavailable", (env, res.get("error")))

    def test_pass_and_fail_through_fake_caller(self):
        jd = load("judge")
        d = Path(tempfile.mkdtemp(prefix="v56-judge-"))
        rep = d / "r.md"
        rep.write_text("## ВЕРДИКТ\n\nОтвет: скомпрометирована\n", encoding="utf-8")
        bad = json.dumps({"verdict": "pass", "verdict_check": {"stated": "x", "follows": False,
                                                               "reason": "owner"}, "claims": []})
        res = jd.run(str(rep), str(CORPUS), str(d / "j.json"), env={},
                     caller=lambda *a: (bad, ["claude-opus-5-5"], {}))
        self.assertEqual(res["status"], "fail")   # never looser than our rule
        self.assertTrue((d / "j.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
