#!/usr/bin/env python3
"""Provider-free contract tests for authoritative benchmark validity."""
import hashlib
import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
TOOL = SHERLOCK / "eval" / "bench" / "validate-run.py"
MANIFEST_TEST = HERE / "test_run_manifest.py"


def load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RM_TEST = load_path("validity_manifest_fixture", MANIFEST_TEST)


STOP = r'''#!/usr/bin/env python3
import json, os, tempfile
class ActiveStateError(Exception): pass
def load_marker(workspace, deadline=None):
    path = os.path.join(workspace, ".sherlock", "active.json")
    with open(path, encoding="utf-8") as src: row=json.load(src)
    return row, path, None
def manifest_worklists(marker, out_dir, deadline=None):
    return [{"path": os.path.join(out_dir, p), "rel": p, "host": None}
            for p in marker["worklists"]]
def compose_worklists(items, out_dir, deadline=None):
    fd, path = tempfile.mkstemp(prefix=".combined-", suffix=".tsv", dir=out_dir)
    seen = set()
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        for item in items:
            with open(item["path"], encoding="utf-8") as src:
                for line in src:
                    if line.strip() and not line.startswith("#"):
                        rid = line.split("\t", 1)[0]
                        if rid in seen: raise ActiveStateError("duplicate id")
                        seen.add(rid)
                    out.write(line)
    return os.path.realpath(path), path
'''

TRIAGE = r'''#!/usr/bin/env python3
import argparse, json, re
def read_worklist(path):
    rows=[]
    with open(path, encoding="utf-8") as src:
      for line in src:
        if not line.strip() or line.startswith("#"): continue
        c=line.rstrip("\n").split("\t") + [""]*6
        rows.append({"id":c[0], "verdict":c[1], "ось":c[2], "ref":c[3],
                     "хост":"host", "n":int(re.search(r"n\s*=\s*(\d+)",c[4]).group(1)) if re.search(r"n\s*=\s*(\d+)",c[4]) else None,
                     "всплеск":"нет", "запись":c[5]})
    return rows
def main():
    p=argparse.ArgumentParser(); p.add_argument("--worklist", required=True)
    p.add_argument("--rules"); p.add_argument("--corpus", required=True); p.add_argument("--json", action="store_true")
    a=p.parse_args(); rows=read_worklist(a.worklist)
    opened=sum(1 for r in rows if r["verdict"] in ("?", "UNSUPPORTED"))
    d={"rows":len(rows), "blocking":opened, "buckets":{"не разобрано":opened},
       "totals":{"дубликатов строк рабочего списка":0}}
    print(json.dumps(d)); return 1 if opened else 0
if __name__ == "__main__": raise SystemExit(main())
'''

CITE = r'''#!/usr/bin/env python3
import argparse, json, os, sys, time
p=argparse.ArgumentParser(); p.add_argument("report"); p.add_argument("--corpus"); p.add_argument("--require-quote", action="store_true")
p.add_argument("--ledger"); p.add_argument("--delivered"); p.add_argument("--json", action="store_true"); a=p.parse_args()
text=open(a.delivered, encoding="utf-8").read()
mode=os.environ.get("FAKE_UNUSED", "")
if "CHECKER_TIMEOUT" in text: time.sleep(60)
if "CHECKER_REPORT_ECHO" in text: print(text); raise SystemExit(0)
if "CHECKER_SECRET" in text: print("api_key=fixture-secret"); raise SystemExit(0)
if "CHECKER_MALFORMED" in text: print("not-json"); raise SystemExit(0)
if "CHECKER_NON_OBJECT" in text: print("[]"); raise SystemExit(0)
if "CHECKER_OVERSIZED" in text: print("x"*2000000); raise SystemExit(0)
bad="CITATION_FAIL" in text
print(json.dumps({"summary":{"bad":int(bad)}, "ledger":{"unresolved_total":int(bad)},
                  "delivered":{"not_in_checked":[]}}))
raise SystemExit(1 if bad else 0)
'''

LOGMAP = r'''#!/usr/bin/env python3
import argparse, json, os
p=argparse.ArgumentParser(); p.add_argument("corpus"); p.add_argument("--out", required=True)
p.add_argument("--worklist-cap"); p.add_argument("--per-file-cap"); p.add_argument("--rate-cap")
p.add_argument("--map-cap"); p.add_argument("--seed"); p.add_argument("--jobs"); a=p.parse_args()
os.makedirs(a.out, exist_ok=True); os.makedirs(".sherlock", exist_ok=True)
open(os.path.join(a.out,"worklist.tsv"),"w").write("G1\tREFERENCE\tA1\thost/a.log:1\tn=1\trecord\n")
json.dump({"version":28,"active":True,"workspace":os.path.realpath("."),"corpus":os.path.realpath(a.corpus),
 "out":os.path.realpath(a.out),"skill_root":"fixture","mode":"single","worklists":["worklist.tsv"]},
 open(".sherlock/active.json","w")); print("mapped")
'''


class ValidityFixture:
    def __init__(self, root, *, prompt="investigate\n", skill_extra="", delivered="clean report",
                 artifact=None, corpus_extra="", settings_pre="{}\n", settings_saved="{}\n"):
        self.root = Path(root)
        self.fx = RM_TEST.Fixture(root)
        self.fx.prompt.write_text(prompt, encoding="utf-8")
        (self.fx.skill / "SKILL.md").write_text("procedure\n" + skill_extra, encoding="utf-8")
        self._tool(self.fx.stop, STOP)
        self._tool(self.fx.triage, TRIAGE)
        self._tool(self.fx.citation, CITE)
        self._tool(self.fx.skill / "tools" / "logmap.py", LOGMAP)
        self.fx.key_data["defects"][0].update({
            "title": "Hidden answer title alpha omega",
            "description": "Private description never present in corpus bytes",
            "root_cause": "Private root cause never present in corpus bytes",
            "trap": "Private attacker-only evidence never present in corpus",
        })
        self.fx.key_data["defects"][0]["proof_locations"][0].update(
            {"line_start": 2, "line_end": 2})
        if corpus_extra:
            corpus_file = self.fx.corpus / "host" / "a.log"
            corpus_file.write_text(corpus_file.read_text(encoding="utf-8") + corpus_extra,
                                   encoding="utf-8")
            self.fx.key_data["files"] = [RM_TEST.file_row(self.fx.corpus, item["path"])
                                         for item in self.fx.key_data["files"]]
        self.fx.write_key()
        self.fx.stage(); self.manifest = self.fx.create()
        self.trace = self.fx.trace
        (self.trace / "qwen-settings-pre.json").write_text(settings_pre, encoding="utf-8")
        (self.trace / "qwen-settings.json").write_text(settings_saved, encoding="utf-8")
        self.work = self.trace / "work"; self.work.mkdir()
        self.write_target_worklist()
        (self.trace / ".sherlock").mkdir()
        self.write_marker()
        self.result = {"type":"result", "is_error":False, "num_turns":2,
                       "usage":{"input_tokens":10,"output_tokens":5}, "result":delivered}
        self.write_result()
        if artifact is not None:
            (self.work / "report.md").write_text(artifact, encoding="utf-8")
        self.write_upstream([{"run_tag":"run-001", "requested_model":"qwen-target",
                              "returned_model":"qwen-real", "status":200}])
        self.write_candidate()
        self.ledger = self.root / "quality.jsonl"

    @staticmethod
    def _tool(path, body):
        path.write_text(body, encoding="utf-8"); path.chmod(0o755)

    def write_marker(self):
        row={"version":28,"active":True,"workspace":str(self.trace),"corpus":str(self.fx.staged),
             "out":str(self.work),"skill_root":str(self.fx.skill),"mode":"single",
             "worklists":["worklist.tsv"]}
        (self.trace / ".sherlock" / "active.json").write_text(json.dumps(row), encoding="utf-8")

    def write_target_worklist(self, body="G1\tPROVEN\tA1\thost/a.log:1\tn=1\trecord\n"):
        (self.work / "worklist.tsv").write_text(body, encoding="utf-8")

    def write_result(self):
        (self.trace / "out.json").write_text(json.dumps([self.result]), encoding="utf-8")

    def write_upstream(self, rows):
        (self.trace / "upstream-completed.jsonl").write_text(
            "".join(json.dumps(r)+"\n" for r in rows), encoding="utf-8")

    def write_candidate(self, **updates):
        row={"schema":1,"run_tag":"run-001","result_stream":"out.json","work_root":"work",
             "artifact":"work/report.md","upstream_completed":"upstream-completed.jsonl",
             "transport":{"exit_code":None,"status":"success","duration_s":None},
             "usage":{"turns":2,"input_tokens":10,"output_tokens":5}}
        row.update(updates)
        (self.trace / "candidate.json").write_text(json.dumps(row), encoding="utf-8")

    def validate(self):
        module=load_path("validate_run_%s" % id(self), TOOL)
        module.CHECKER_TIMEOUT=0.25
        return module.validate_run(str(self.trace), str(self.fx.commitment),
                                   str(self.fx.commitment_key), str(self.ledger))


class BenchValidityTests(unittest.TestCase):
    def fixture(self, **kwargs):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        return ValidityFixture(temp.name, **kwargs)

    def test_minimal_message_run_seals_then_appends_once(self):
        fx=self.fixture(); row=fx.validate()
        self.assertTrue(row["valid"], row)
        self.assertEqual(row["delivery"]["channel"], "message")
        sealed=(fx.trace/"validity.json").read_bytes()
        self.assertRegex(row["hmac_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(fx.ledger.read_text().splitlines()), 1)
        persisted=(fx.trace/"validity.json").read_text()+fx.ledger.read_text()
        self.assertNotIn("qwen-target",persisted); self.assertNotIn("qwen-real",persisted)
        self.assertNotIn("clean report",persisted)
        again=fx.validate()
        self.assertEqual(again, row); self.assertEqual((fx.trace/"validity.json").read_bytes(), sealed)
        self.assertEqual(len(fx.ledger.read_text().splitlines()), 1)
        self.assertEqual(json.loads((fx.trace/"status.json").read_text())["phase"], "ACCEPTED")

    def test_missing_delivery_and_artifact_only_are_rejected_without_ledger(self):
        fx=self.fixture(delivered="")
        row=fx.validate(); self.assertFalse(row["valid"]); self.assertIn("missing_deliverable", row["reasons"])
        self.assertFalse(fx.ledger.exists())
        fx=self.fixture(delivered="", artifact="surviving report")
        fx.result.update({"is_error":True,"error":"provider failed","result":"", "usage":{}}); fx.write_result()
        fx.write_candidate(transport={"exit_code":None,"status":"error","duration_s":None},
                           usage={"turns":None,"input_tokens":None,"output_tokens":None})
        fx.write_upstream([{"run_tag":"run-001","returned_model":None,"status":502}])
        row=fx.validate(); self.assertFalse(row["valid"]); self.assertTrue(row["artifact_only"])
        self.assertIsNone(row["usage"]["input_tokens"]); self.assertFalse(fx.ledger.exists())

    def test_inventory_is_exact_and_direct_checkers_are_authoritative(self):
        cases=[("G1\tPROVEN\tA1\thost/a.log:1\tn=1\trecord\nG2\tPROVEN\tA1\thost/a.log:1\tn=1\tx\n", "inventory_mismatch"),
               ("G1\t?\tA1\thost/a.log:1\tn=1\trecord\n", "triage_failed"),
               ("G1\tUNSUPPORTED\tA1\thost/a.log:1\tn=1\trecord\n", "triage_failed")]
        for body, reason in cases:
            with self.subTest(reason=reason):
                fx=self.fixture(); fx.write_target_worklist(body)
                row=fx.validate(); self.assertFalse(row["valid"]); self.assertIn(reason,row["reasons"])

    def test_returned_identity_must_be_successful_homogeneous_and_exact(self):
        cases=[([{"run_tag":"run-001","returned_model":"other","status":200}],"identity_wrong"),
               ([{"run_tag":"run-001","returned_model":None,"status":200}],"identity_missing"),
               ([{"run_tag":"run-001","returned_model":"qwen-real","status":200},
                 {"run_tag":"run-001","returned_model":"other","status":200}],"identity_mixed")]
        for rows, reason in cases:
            with self.subTest(reason=reason):
                fx=self.fixture(); fx.write_upstream(rows); row=fx.validate()
                self.assertFalse(row["valid"]); self.assertIn(reason,row["reasons"])

    def test_contamination_v1_distinguishes_hidden_output_from_corpus_facts(self):
        for where in ("prompt","skill","message","artifact","workspace"):
            with self.subTest(where=where):
                hidden="Hidden answer title alpha omega"
                kwargs={}
                if where=="prompt": kwargs["prompt"]=hidden
                if where=="skill": kwargs["skill_extra"]=hidden
                if where=="message": kwargs["delivered"]=hidden
                if where=="artifact": kwargs["artifact"]=hidden
                fx=self.fixture(**kwargs)
                if where=="workspace": (fx.work/"hidden.txt").write_text(hidden,encoding="utf-8")
                row=fx.validate(); self.assertFalse(row["valid"]); self.assertIn("contaminated",row["reasons"])
                self.assertEqual(row["contamination"]["schema"],"contamination-v1")
                self.assertNotIn(hidden,json.dumps(row))

    def test_contamination_audits_settings_transcript_and_staged_corpus_as_bound_sources(self):
        hidden="Hidden answer title alpha omega"
        cases=(("settings_pre",{"settings_pre":json.dumps({"note":hidden})}),
               ("settings_saved",{"settings_saved":json.dumps({"note":hidden})}),
               ("staged_corpus",{"corpus_extra":"\nfixture:F-01\n"}),
               ("transcript",{}))
        for source,kwargs in cases:
            with self.subTest(source=source):
                fx=self.fixture(**kwargs)
                if source=="transcript":
                    (fx.trace/"out.json").write_text(json.dumps([
                        {"type":"system","note":hidden},fx.result]),encoding="utf-8")
                row=fx.validate(); self.assertFalse(row["valid"],row)
                self.assertIn("contaminated",row["reasons"])
                self.assertTrue(any(hit["source"]==source for hit in row["contamination"]["hits"]))
                bound={item["source"] for item in row["contamination"]["sources"]}
                self.assertTrue({"settings_pre","settings_saved","staged_corpus","transcript"} <= bound)
                self.assertNotIn(hidden,json.dumps(row))

    def test_delivery_divergence_is_fact_and_exact_union_is_checked(self):
        fx=self.fixture(delivered="message block", artifact="file block")
        row=fx.validate(); self.assertTrue(row["delivery"]["divergent"]); self.assertTrue(row["valid"])
        fx=self.fixture(delivered="message block", artifact="CITATION_FAIL")
        row=fx.validate(); self.assertTrue(row["delivery"]["divergent"]); self.assertIn("citation_failed",row["reasons"])

    def test_checker_failures_are_bounded_and_raw_output_is_never_persisted(self):
        for text, reason in (("CHECKER_MALFORMED","checker_malformed"),
                             ("CHECKER_NON_OBJECT","checker_malformed"),
                             ("CHECKER_OVERSIZED","checker_oversized"),
                             ("CHECKER_TIMEOUT","checker_timeout"),
                             ("CHECKER_REPORT_ECHO","checker_output_sensitive"),
                             ("CHECKER_SECRET","checker_output_sensitive")):
            with self.subTest(reason=reason):
                fx=self.fixture(delivered=text); row=fx.validate()
                self.assertFalse(row["valid"]); self.assertIn(reason,row["reasons"])
                saved=(fx.trace/"validity.json").read_text()
                self.assertNotIn(text,saved); self.assertLess(len(saved),20000)

    def test_manifest_candidate_and_ledger_conflicts_fail_closed(self):
        fx=self.fixture(); row=fx.validate(); before=(fx.trace/"validity.json").read_bytes()
        fx.write_candidate(run_tag="different")
        with self.assertRaisesRegex(Exception,"validity_conflict"):
            fx.validate()
        self.assertEqual((fx.trace/"validity.json").read_bytes(),before)
        fx=self.fixture(); row=fx.validate()
        ledger_row=json.loads(fx.ledger.read_text()); ledger_row["validity_sha256"]="0"*64
        fx.ledger.write_text(json.dumps(ledger_row)+"\n",encoding="utf-8")
        with self.assertRaisesRegex(Exception,"ledger_conflict"): fx.validate()

        fx=self.fixture(); fx.validate()
        ledger_row=json.loads(fx.ledger.read_text()); ledger_row["transport"]["status"]="forged"
        fx.ledger.write_text(json.dumps(ledger_row)+"\n",encoding="utf-8")
        with self.assertRaisesRegex(Exception,"ledger_conflict"): fx.validate()
        self.assertEqual(len(fx.ledger.read_text().splitlines()),1)

    def test_rerun_repairs_missing_or_partial_ledger_tail(self):
        fx=self.fixture(); row=fx.validate(); fx.ledger.unlink(); fx.validate()
        self.assertEqual(len(fx.ledger.read_text().splitlines()),1)
        fx.ledger.write_bytes(fx.ledger.read_bytes()+b'{"partial"')
        fx.validate(); self.assertEqual(len(fx.ledger.read_text().splitlines()),1)

    def test_seal_then_accepted_state_then_ledger_crash_repairs_without_revalidation(self):
        fx=self.fixture(); module=load_path("validate_ledger_crash",TOOL)
        original_validate=module.validate_fresh; original_append=module.append_ledger
        calls={"fresh":0}
        def counted(*args,**kwargs):
            calls["fresh"]+=1; return original_validate(*args,**kwargs)
        def crash(*args,**kwargs): raise module.ValidityError("ledger_io")
        module.validate_fresh=counted; module.append_ledger=crash
        with self.assertRaisesRegex(Exception,"ledger_io"):
            module.validate_run(str(fx.trace),str(fx.fx.commitment),str(fx.fx.commitment_key),str(fx.ledger))
        self.assertTrue((fx.trace/"validity.json").is_file())
        self.assertEqual(json.loads((fx.trace/"status.json").read_text())["phase"],"ACCEPTED")
        self.assertFalse(fx.ledger.exists())
        module.append_ledger=original_append
        row=module.validate_run(str(fx.trace),str(fx.fx.commitment),str(fx.fx.commitment_key),str(fx.ledger))
        self.assertTrue(row["valid"]); self.assertEqual(calls["fresh"],1)
        self.assertEqual(len(fx.ledger.read_text().splitlines()),1)

    def test_flock_retries_eintr(self):
        fx=self.fixture(); module=load_path("validate_eintr",TOOL)
        original=module.fcntl.flock; calls={"count":0}
        def flaky(fd,operation):
            calls["count"]+=1
            if calls["count"]==1: raise OSError(errno.EINTR,"interrupted")
            return original(fd,operation)
        module.fcntl.flock=flaky
        self.assertTrue(module.validate_run(str(fx.trace),str(fx.fx.commitment),
                                            str(fx.fx.commitment_key),str(fx.ledger))["valid"])
        self.assertGreaterEqual(calls["count"],3)

    def test_sealed_evidence_is_rehashed_before_ledger_repair(self):
        for relative in ("out.json","upstream-completed.jsonl","work/report.md","work/worklist.tsv"):
            with self.subTest(relative=relative):
                fx=self.fixture(artifact="artifact")
                fx.validate(); fx.ledger.unlink()
                path=fx.trace/relative; path.write_bytes(path.read_bytes()+b" \n")
                with self.assertRaisesRegex(Exception,"validity_conflict"): fx.validate()
                self.assertFalse(fx.ledger.exists())

    def test_trace_swap_fails_closed_and_ledger_conflict_preserves_authenticated_state(self):
        fx=self.fixture(); module=load_path("validate_swap",TOOL)
        original=module.MANIFEST.verify_manifest
        def swap(trace, commitment, key):
            row=original(trace,commitment,key); held=Path(str(trace)+"-held")
            os.rename(trace,held); shutil.copytree(held,trace); return row
        module.MANIFEST.verify_manifest=swap
        with self.assertRaisesRegex(Exception,"trace_identity_changed"):
            module.validate_run(str(fx.trace),str(fx.fx.commitment),str(fx.fx.commitment_key),str(fx.ledger))
        fx=self.fixture(); fx.ledger.write_text(json.dumps({"run_tag":"run-001","validity_sha256":"0"*64})+"\n")
        with self.assertRaisesRegex(Exception,"ledger_conflict"): fx.validate()
        self.assertEqual(json.loads((fx.trace/"status.json").read_text())["phase"],"ACCEPTED")

    def test_post_bind_swap_and_verified_checker_mutation_fail_closed(self):
        fx=self.fixture(); module=load_path("validate_post_bind_swap",TOOL)
        original=module.bind_verified_trace
        def swap(trace, trace_fd, manifest):
            original(trace,trace_fd,manifest); held=Path(str(trace)+"-held")
            os.rename(trace,held); shutil.copytree(held,trace)
        module.bind_verified_trace=swap
        with self.assertRaisesRegex(Exception,"trace_identity_changed"):
            module.validate_run(str(fx.trace),str(fx.fx.commitment),str(fx.fx.commitment_key),str(fx.ledger))
        fx=self.fixture(); module=load_path("validate_authority_mutation",TOOL)
        original=module.bind_verified_trace
        def mutate(trace, trace_fd, manifest):
            original(trace,trace_fd,manifest); fx.fx.citation.write_text("print('not-json')\n",encoding="utf-8")
        module.bind_verified_trace=mutate
        row=module.validate_run(str(fx.trace),str(fx.fx.commitment),str(fx.fx.commitment_key),str(fx.ledger))
        self.assertFalse(row["valid"]); self.assertIn("authority_changed",row["reasons"])

    def test_cli_malformed_input_has_stable_json_without_traceback(self):
        fx=self.fixture(); (fx.trace/"candidate.json").write_text("[]",encoding="utf-8")
        p=subprocess.run([sys.executable,str(TOOL),str(fx.trace),"--commitment-file",str(fx.fx.commitment),
                          "--commitment-key",str(fx.fx.commitment_key),"--ledger",str(fx.ledger),"--json"],
                         text=True,capture_output=True)
        self.assertEqual(p.returncode,1,p.stderr); self.assertEqual(p.stderr,"")
        self.assertIn("candidate_invalid",json.loads(p.stdout)["reasons"]); self.assertNotIn("Traceback",p.stdout)

    def test_real_v28_helpers_expose_required_inventory_contract(self):
        stop=load_path("real_stop",SHERLOCK/"skills/v28/tools/stopcheck.py")
        triage=load_path("real_triage",SHERLOCK/"skills/v28/tools/triagecheck.py")
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); one=root/"one.tsv"; two=root/"two.tsv"
            one.write_text("G1\t?\tA1\thost/a.log:1\tn=1\trecord\n",encoding="utf-8")
            two.write_text("G1\t?\tA1\thost/a.log:1\tn=1\trecord\n",encoding="utf-8")
            row=triage.read_worklist(str(one))[0]
            self.assertEqual((row["id"],row["хост"],row["ось"],row["ref"],row["n"],row["запись"]),
                             ("G1","host","A1","host/a.log:1",1,"record"))
            with self.assertRaises(stop.ActiveStateError):
                stop.compose_worklists([str(one),str(two)],str(root))

    def test_real_v28_bare_ordinal_ids_are_not_bluesky_leakage_but_composite_ids_are(self):
        module=load_path("validity_real_contamination",TOOL)
        key=SHERLOCK/"eval/bench/answer-key-bluesky.json"; skill=SHERLOCK/"skills/v28"
        blobs=[path.read_bytes() for path in sorted(skill.rglob("*")) if path.is_file() and ".git" not in path.parts]
        args=({"artifacts":{"answer_key":{"path":str(key)}}},"","","",[],
              key.read_bytes(),b"",blobs,[],b"{}",b"{}",b"[]")
        row=module.contamination(*args)
        self.assertEqual(row["hit_count"],0,row)
        for injected,expected in ((b"bare D0100 token",False),(b"selected bluesky:D01 material",True)):
            altered=list(args); altered[7]=blobs+[injected]
            hit=module.contamination(*altered)
            self.assertEqual(any(item["category"]=="answer_id" for item in hit["hits"]),expected,hit)
        key_row=json.loads(key.read_text(encoding="utf-8"))
        hidden=next(item["title"] for item in key_row["defects"] if len(item.get("title","")) >= 24)
        altered=list(args); altered[7]=blobs+[hidden.encode()]
        hit=module.contamination(*altered)
        self.assertTrue(any(item["category"]=="title" for item in hit["hits"]),hit)


if __name__ == "__main__":
    unittest.main()
