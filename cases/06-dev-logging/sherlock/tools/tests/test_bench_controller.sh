#!/usr/bin/env bash
set -euo pipefail
exec python3 - "$@" <<'PY'
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time
import unittest


HERE = Path.cwd()
RUNNER = HERE / "eval" / "bench" / "run-bench.sh"
CONTROLLER = HERE / "eval" / "bench" / "bench-controller.sh"


def executable(path, body):
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


class ControlledRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        (self.corpus / "one.log").write_text("line\n", encoding="utf-8")
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("inspect {CORPUS}\n", encoding="utf-8")
        self.qwen_capture = self.root / "qwen-env.json"
        self.qwen = self.root / "fake-qwen"
        executable(self.qwen, """
python3 - "$QWEN_CAPTURE" <<'PY2'
import json, os, sys
names = [name for name in os.environ if name.startswith("SHERLOCK_BUDGET_") or
         "CONTROLLER" in name or "COMMITMENT" in name]
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(sorted(names), handle)
PY2
mkdir -p work
printf 'report\n' > work/report.md
printf '[{"type":"result","result":"ok","is_error":false,"session_id":"1234567890abcdef","num_turns":1,"usage":{"input_tokens":1,"output_tokens":1}}]\n'
""")

    def tearDown(self):
        self.tmp.cleanup()

    def trace(self, tag="controlled-001"):
        trace = self.runs / tag
        trace.mkdir()
        (trace / "run-manifest.json").write_text('{"sealed":true}\n', encoding="utf-8")
        return trace

    def env(self, trace, **updates):
        env = dict(os.environ)
        env.update({
            "BENCH_RUNS": str(self.runs),
            "SHERLOCK_RUN_TAG": trace.name,
            "SHERLOCK_TRACE": str(trace),
            "SHERLOCK_REQUIRE_ATTRIBUTION": "1",
            "SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS": "10",
            "SHERLOCK_BUDGET_MAX_REQUEST_BYTES": "100000",
            "SHERLOCK_BUDGET_MAX_WALL_SECONDS": "30",
            "SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES": "3",
            "SHERLOCK_EXPECTED_RETURNED_IDENTITY": "expected-model",
            "SHERLOCK_CORPUS": str(self.corpus),
            "SHERLOCK_DATASET": "fixture",
            "SHERLOCK_PROMPT_FILE": str(self.prompt),
            "SHERLOCK_API_KEY": "provider-free-fixture",
            "SHERLOCK_BASE_URL": "http://127.0.0.1:9/v1",
            "QWEN_BIN": str(self.qwen),
            "QWEN_CAPTURE": str(self.qwen_capture),
            "SHERLOCK_RESUME_MAX_ATTEMPTS": "0",
        })
        env.update(updates)
        return env

    def start_and_prove(self, trace, **updates):
        process = subprocess.Popen(["bash", str(RUNNER), "none"], env=self.env(trace, **updates),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready = trace / ".runner-ready"
        deadline = time.time() + 10
        while not ready.exists() and process.poll() is None and time.time() < deadline:
            time.sleep(.02)
        if not ready.exists():
            output = process.communicate(timeout=2)
            self.fail("runner never published readiness: %r" % (output,))
        proof = {"pid": process.pid, "process_start_ticks": 77, "pgid": process.pid,
                 "boot_id_sha256": hashlib.sha256(b"boot").hexdigest(),
                 "command_sha256": hashlib.sha256(b"command").hexdigest()}
        budget = {"schema": 1, "run_tag": trace.name, "updated_at": "fixture",
                  "attempts_charged": 0, "request_bytes": 0,
                  "consecutive_provider_failures": 0,
                  "limits": {"max_upstream_attempts": 10,
                             "max_request_bytes": 100000,
                             "max_wall_seconds": 30,
                             "max_consecutive_provider_failures": 3},
                  "verdict": "WITHIN", "reason": None}
        (trace / "upstream-budget-state.json").write_text(json.dumps(budget) + "\n")
        (trace / "controller-process.json").write_text(json.dumps(proof) + "\n", encoding="utf-8")
        return process, proof

    def test_controlled_mode_requires_both_identity_values(self):
        trace = self.trace()
        env = self.env(trace)
        del env["SHERLOCK_TRACE"]
        result = subprocess.run(["bash", str(RUNNER), "none"], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.qwen_capture.exists())
        self.assertEqual(sorted(p.name for p in trace.iterdir()), ["run-manifest.json"])

    def test_collision_is_rejected_before_proxy_or_qwen(self):
        trace = self.trace()
        (trace / "foreign-output").write_text("collision\n", encoding="utf-8")
        result = subprocess.run(["bash", str(RUNNER), "none"], env=self.env(trace),
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.qwen_capture.exists())

    def test_every_state_row_uses_one_runner_proof_and_qwen_gets_no_caps(self):
        trace = self.trace()
        process, proof = self.start_and_prove(trace)
        stdout, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0, (stdout, stderr))
        status = json.loads((trace / "status.json").read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in
                (trace / "status-events.jsonl").read_text(encoding="utf-8").splitlines()]
        for row in [status] + rows:
            self.assertEqual({key: row.get(key) for key in proof}, proof)
        self.assertEqual(json.loads(self.qwen_capture.read_text(encoding="utf-8")), [])

    def test_strict_proxy_failure_is_terminal_before_qwen(self):
        trace = self.trace()
        process, proof = self.start_and_prove(
            trace, UPSTREAM_LANE_PROXY=str(self.root / "missing-proxy"))
        process.communicate(timeout=20)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(self.qwen_capture.exists())
        status = json.loads((trace / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["phase"], "RUN_FAILED")
        self.assertEqual({key: status.get(key) for key in proof}, proof)


class ControllerPresenceTests(unittest.TestCase):
    def test_controller_entrypoint_exists(self):
        self.assertTrue(CONTROLLER.is_file())
        self.assertTrue(os.access(CONTROLLER, os.X_OK))


class ControllerFixture:
    def __init__(self, case):
        self.case = case
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.controllers = self.base / "controllers"
        self.runs = self.base / "runs"
        self.proc = self.base / "proc"
        self.controllers.mkdir(); self.runs.mkdir()
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/random/boot_id").write_text("fixture-boot\n", encoding="utf-8")
        (self.proc / "self").mkdir()
        (self.proc / "self/stat").write_text(
            "{pid} (controller) S 1 {pgid} {pgid} " + "0 " * 15 + "41\n", encoding="utf-8")
        (self.proc / "self/cmdline").write_bytes(b"fixture-controller\0")
        self.capture = self.base / "target-env.json"
        self.corpus_capture = self.base / "target-corpus.txt"
        self.term_marker = self.base / "term-marker"
        self.validator_marker = self.base / "validator-marker"
        self.validator_count = self.base / "validator-count"
        self.corpus = self.base / "corpus"; self.corpus.mkdir()
        (self.corpus / "a.log").write_text("evidence\n", encoding="utf-8")
        self.assets = {}
        for name in ("answer-key.json", "renderer.py", "prompt.txt", "scorer.py",
                     "triage.py", "stop.py", "citation.py", "target-cli"):
            path = self.base / name; path.write_text(name + "\n", encoding="utf-8")
            self.assets[name] = path
        self.skill = self.base / "skill"; self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("fixture\n", encoding="utf-8")
        self.manifest_tool = self.base / "manifest-tool.py"
        self.validator_tool = self.base / "validator-tool.py"
        self.health_tool = self.base / "health-tool.py"
        self.target_tool = self.base / "target-tool.sh"
        self._write_tools()

    def close(self):
        self.tmp.cleanup()

    def _write_tools(self):
        self.manifest_tool.write_text(r'''#!/usr/bin/env python3
import hashlib, json, os, shutil, sys
mode=sys.argv[1]
def value(flag): return sys.argv[sys.argv.index(flag)+1]
if mode == "stage":
    source=value("--source-corpus"); dest=value("--destination")
    shutil.copytree(source, dest); raise SystemExit(0)
if mode == "create":
    trace=sys.argv[2]; tag=value("--run-tag")
    identity=hashlib.sha256((tag+"-manifest").encode()).hexdigest()
    row={"schema":1,"run_tag":tag,"manifest_sha256":identity}
    path=os.path.join(trace,"run-manifest.json")
    fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,"w") as f: json.dump(row,f,sort_keys=True,separators=(",",":")); f.write("\n"); f.flush(); os.fsync(f.fileno())
    raise SystemExit(0)
if mode == "verify":
    path=os.path.join(sys.argv[2],"run-manifest.json")
    row=json.load(open(path));
    if set(row)!={"schema","run_tag","manifest_sha256"}: raise SystemExit(2)
    print(json.dumps(row)); raise SystemExit(0)
raise SystemExit(2)
''', encoding="utf-8"); self.manifest_tool.chmod(0o755)
        self.validator_tool.write_text(r'''#!/usr/bin/env python3
import hashlib,hmac,json,os,sys,time
trace=sys.argv[1]
def value(flag): return sys.argv[sys.argv.index(flag)+1]
count=os.environ.get("FAKE_VALIDATOR_COUNT")
if count:
    try: n=int(open(count).read())
    except Exception: n=0
    open(count,"w").write(str(n+1))
marker=os.environ.get("FAKE_VALIDATOR_MARKER")
if marker:
    open(marker,"w").write("ready\n")
    release=os.environ["FAKE_VALIDATOR_RELEASE"]
    while not os.path.exists(release): time.sleep(.02)
key=open(value("--commitment-key"),"rb").read(); manifest=json.load(open(os.path.join(trace,"run-manifest.json")))
candidate=os.path.join(trace,"candidate.json")
valid=os.environ.get("FAKE_VALIDITY","true")=="true" and os.path.isfile(candidate)
row={"schema":1,"valid":valid,"reasons":[] if valid else ["fixture_rejected"],"run_tag":manifest["run_tag"],"manifest_sha256":manifest["manifest_sha256"],"candidate_sha256":hashlib.sha256(open(candidate,"rb").read()).hexdigest() if os.path.isfile(candidate) else None}
payload=json.dumps(row,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode(); row["hmac_sha256"]=hmac.new(key,payload,hashlib.sha256).hexdigest()
path=os.path.join(trace,"validity.json"); fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,"w") as f: json.dump(row,f,sort_keys=True,separators=(",",":")); f.write("\n"); f.flush(); os.fsync(f.fileno())
swap=os.environ.get("FAKE_SWAP_PATH")
if swap:
    if not os.path.isabs(swap): swap=os.path.join(trace,swap)
    os.unlink(swap); os.symlink(os.environ["FAKE_SWAP_TARGET"],swap)
raise SystemExit(0)
''', encoding="utf-8"); self.validator_tool.chmod(0o755)
        self.health_tool.write_text(r'''#!/usr/bin/env python3
import datetime,json,os
now=datetime.datetime.now(datetime.timezone.utc)
def z(value): return value.isoformat().replace("+00:00","Z")
row={"schema":1,"checked_at":z(now),"expires_at":z(now+datetime.timedelta(minutes=10)),
"lane":os.environ["PROBE_LANE"],"provider":os.environ["PROBE_PROVIDER"],
"requested_model":os.environ["SHERLOCK_MODEL"],"endpoint":os.environ["PROBE_BASE_URL"],
"shape":"history","tools":25,"sizes_kb":[100,250,400],
"history":[{"size_kb":n,"status":200,"returned_model":os.environ["PROBE_EXPECTED_RETURNED_MODEL"]} for n in (100,250,400)],
"verdict":os.environ.get("FAKE_HEALTH_VERDICT","HEALTHY")}
with open(os.environ["PROBE_RECEIPT_PATH"],"w") as f: json.dump(row,f); f.write("\n")
''', encoding="utf-8"); self.health_tool.chmod(0o755)
        executable(self.target_tool, f'''
mkdir -p "{self.proc}/$$"
python3 - "{self.proc}/$$/stat" "$$" <<'PY2'
import sys
pid=int(sys.argv[2]); fields=["S",str(pid),str(pid),str(pid)]+["0"]*15+["99"]
open(sys.argv[1],"w").write(str(pid)+" (target) "+" ".join(fields)+"\\n")
PY2
printf 'fixture-command\\0' > "{self.proc}/$$/cmdline"
python3 - "{self.capture}" <<'PY2'
import json,os,sys
bad=[n for n in os.environ if "CONTROLLER" in n or "COMMITMENT" in n or n in ("SHERLOCK_MANIFEST_TOOL","SHERLOCK_VALIDATOR_TOOL","SHERLOCK_PROC_ROOT")]
json.dump(sorted(bad),open(sys.argv[1],"w"))
PY2
printf '%s\n' "$SHERLOCK_CORPUS" > "{self.corpus_capture}"
touch "$SHERLOCK_TRACE/.runner-ready"
for i in $(seq 1 200); do [ -f "$SHERLOCK_TRACE/controller-process.json" ] && break; sleep .01; done
if [ "${{FAKE_TARGET_MODE:-candidate}}" = "breach" ] || [ "${{FAKE_TARGET_MODE:-candidate}}" = "breach-exit" ]; then
  trap 'touch "{self.term_marker}"; exit 0' TERM
  python3 - "$SHERLOCK_TRACE/upstream-budget-state.json" <<'PY2'
import json,sys
p=sys.argv[1]; row=json.load(open(p)); row.update({{"verdict":"EXCEEDED","reason":"MAX_UPSTREAM_ATTEMPTS","attempts_charged":row["limits"]["max_upstream_attempts"]}}); json.dump(row,open(p,"w"));
PY2
  [ "${{FAKE_TARGET_MODE:-candidate}}" = "breach-exit" ] && exit 4
  while :; do sleep .1; done
elif [ "${{FAKE_TARGET_MODE:-candidate}}" = "wait" ]; then
  while [ ! -f "{self.base}/release-target" ]; do sleep .05; done
elif [ "${{FAKE_TARGET_MODE:-candidate}}" = "none" ]; then
  exit 4
elif [ "${{FAKE_TARGET_MODE:-candidate}}" = "symlink-dir" ]; then
  ln -s "{self.base}" "$SHERLOCK_TRACE/linked-directory"
fi
printf '{{"schema":1}}\\n' > "$SHERLOCK_TRACE/candidate.json"
''')

    def env(self, **updates):
        env = dict(os.environ)
        env.update({
            "SHERLOCK_CONTROLLER_ROOT": str(self.controllers),
            "SHERLOCK_FREE_TEST_COMMAND": "true",
            "SHERLOCK_HEALTH_COMMAND": str(self.health_tool),
            "SHERLOCK_TARGET_COMMAND": str(self.target_tool),
            "BENCH_RUNS": str(self.runs),
            "SHERLOCK_BUDGET_MAX_UPSTREAM_ATTEMPTS": "5",
            "SHERLOCK_BUDGET_MAX_REQUEST_BYTES": "5000",
            "SHERLOCK_BUDGET_MAX_WALL_SECONDS": "20",
            "SHERLOCK_BUDGET_MAX_CONSECUTIVE_PROVIDER_FAILURES": "3",
            "SHERLOCK_PROC_ROOT": str(self.proc),
            "SHERLOCK_MANIFEST_TOOL": str(self.manifest_tool),
            "SHERLOCK_VALIDATOR_TOOL": str(self.validator_tool),
            "SHERLOCK_LEDGER": str(self.base / "ledger.jsonl"),
            "SHERLOCK_CORPUS": str(self.corpus),
            "SHERLOCK_ANSWER_KEY": str(self.assets["answer-key.json"]),
            "SHERLOCK_RENDERER": str(self.assets["renderer.py"]),
            "SHERLOCK_PROMPT_FILE": str(self.assets["prompt.txt"]),
            "SHERLOCK_SKILL_ROOT": str(self.skill),
            "SHERLOCK_SCORER": str(self.assets["scorer.py"]),
            "SHERLOCK_TRIAGE_CHECKER": str(self.assets["triage.py"]),
            "SHERLOCK_STOP_CHECKER": str(self.assets["stop.py"]),
            "SHERLOCK_CITATION_CHECKER": str(self.assets["citation.py"]),
            "QWEN_BIN": str(self.assets["target-cli"]),
            "SHERLOCK_TARGET_VERSION": "fixture-1",
            "SHERLOCK_MODEL": "fixture-model",
            "SHERLOCK_PROVIDER": "fixture-provider",
            "SHERLOCK_EXPECTED_RETURNED_IDENTITY": "fixture-returned",
            "SHERLOCK_LANE": "paid",
            "SHERLOCK_BASE_URL": "http://127.0.0.1:9/v1",
            "FAKE_VALIDITY": "true",
            "FAKE_VALIDATOR_COUNT": str(self.validator_count),
        })
        env.update(updates)
        return env

    def run(self, *args, env=None, timeout=30):
        return subprocess.run(["bash", str(CONTROLLER), *args], env=env or self.env(),
                              capture_output=True, text=True, timeout=timeout)

    def controller_dir(self):
        choices=[p for p in self.controllers.iterdir() if p.is_dir() and p.name not in ("keys","records")]
        self.case.assertEqual(len(choices),1, choices)
        return choices[0]


class PersistentControllerTests(unittest.TestCase):
    def setUp(self):
        self.fx=ControllerFixture(self)

    def tearDown(self):
        self.fx.close()

    def test_done_bootstraps_and_reuses_strict_key_exact_link_receipt_and_seal(self):
        first=self.fx.run(); self.assertEqual(first.returncode,0,(first.stdout,first.stderr))
        controller=self.fx.controller_dir(); status=json.loads((controller/"status.json").read_text())
        self.assertEqual(status["phase"],"DONE")
        trace=Path(json.loads((controller/"controller-child.json").read_text())["child_trace"])
        key=self.fx.controllers/"keys/controller.key"
        self.assertEqual(stat.S_IMODE(key.stat().st_mode),0o600); self.assertEqual(len(key.read_bytes()),32)
        before=key.read_bytes()
        link=json.loads((controller/"controller-child.json").read_text())
        self.assertEqual(set(link),{"schema","parent_trace","parent_identity_sha256","child_run_tag","child_trace","child_manifest_sha256","linked_at","key_id","hmac_sha256"})
        unsigned=dict(link); signature=unsigned.pop("hmac_sha256")
        self.assertEqual(signature,hmac.new(before,json.dumps(unsigned,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode(),hashlib.sha256).hexdigest())
        receipt=json.loads((trace/"controller-receipt.json").read_text()); unsigned=dict(receipt); sig=unsigned.pop("hmac_sha256")
        self.assertEqual(sig,hmac.new(before,json.dumps(unsigned,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode(),hashlib.sha256).hexdigest())
        self.assertEqual(json.loads(self.fx.capture.read_text()),[])
        self.assertEqual(Path(self.fx.corpus_capture.read_text().strip()),controller/"staged-corpus")
        phases=[json.loads(line)["phase"] for line in (controller/"status-events.jsonl").read_text().splitlines()]
        self.assertEqual(phases,["FIXING","TESTING","HEALTH_CHECKING","READY","QWEN_RUNNING","VERIFYING","DONE"])
        self.assertTrue((trace/"trace-manifest.json").is_file()); self.assertTrue((trace/"sealed").is_file())
        resumed=self.fx.run("--resume",controller.name); self.assertEqual(resumed.returncode,0)
        self.assertEqual(key.read_bytes(),before)

    def test_invalid_existing_key_blocks_without_replacement_or_target(self):
        keydir=self.fx.controllers/"keys"; keydir.mkdir(); key=keydir/"controller.key"
        key.write_bytes(b"short"); key.chmod(0o600)
        result=self.fx.run(); self.assertNotEqual(result.returncode,0)
        self.assertEqual(key.read_bytes(),b"short"); self.assertFalse(self.fx.capture.exists())

    def test_health_failure_blocks_before_target_and_paid_phase(self):
        result=self.fx.run(env=self.fx.env(FAKE_HEALTH_VERDICT="DEGRADED"))
        self.assertNotEqual(result.returncode,0); self.assertFalse(self.fx.capture.exists())
        controller=self.fx.controller_dir(); status=json.loads((controller/"status.json").read_text())
        self.assertEqual(status["phase"],"BLOCKED")
        phases=[json.loads(line)["phase"] for line in (controller/"status-events.jsonl").read_text().splitlines()]
        self.assertNotIn("READY",phases); self.assertNotIn("QWEN_RUNNING",phases)
        resumed=self.fx.run("--resume",controller.name)
        self.assertNotEqual(resumed.returncode,0); self.assertFalse(self.fx.capture.exists())
        self.assertNotIn("Traceback",resumed.stderr)
        resumed_status=json.loads((controller/"status.json").read_text())
        self.assertEqual((resumed_status["phase"],resumed_status["reason"]),
                         ("BLOCKED","HEALTH_RECEIPT_INVALID"))

    def test_runner_and_validator_outcomes_are_not_conflated(self):
        result=self.fx.run(env=self.fx.env(FAKE_TARGET_MODE="none")); self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.fx.controller_dir()/"status.json").read_text())["phase"],"RUNNER_FAILED")
        self.fx.close(); self.fx=ControllerFixture(self)
        result=self.fx.run(env=self.fx.env(FAKE_VALIDITY="false")); self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.fx.controller_dir()/"status.json").read_text())["phase"],"REJECTED")

    def test_budget_breach_terminates_owned_group_and_is_permanent(self):
        result=self.fx.run(env=self.fx.env(FAKE_TARGET_MODE="breach"),timeout=30)
        self.assertNotEqual(result.returncode,0); self.assertTrue(self.fx.term_marker.exists())
        controller=self.fx.controller_dir(); status=json.loads((controller/"status.json").read_text())
        self.assertEqual((status["phase"],status["reason"]),("BLOCKED","MAX_UPSTREAM_ATTEMPTS"))
        capture_mtime=self.fx.capture.stat().st_mtime_ns
        resumed=self.fx.run("--resume",controller.name,env=self.fx.env())
        self.assertNotEqual(resumed.returncode,0); self.assertEqual(self.fx.capture.stat().st_mtime_ns,capture_mtime)

    def test_budget_breach_remains_controller_owned_if_child_exits_immediately(self):
        result=self.fx.run(env=self.fx.env(FAKE_TARGET_MODE="breach-exit"),timeout=30)
        self.assertNotEqual(result.returncode,0)
        controller=self.fx.controller_dir(); status=json.loads((controller/"status.json").read_text())
        self.assertEqual((status["phase"],status["reason"]),("BLOCKED","MAX_UPSTREAM_ATTEMPTS"))
        link=json.loads((controller/"controller-child.json").read_text()); trace=Path(link["child_trace"])
        receipt=json.loads((trace/"controller-receipt.json").read_text())
        self.assertEqual((receipt["verdict"],receipt["reason"]),("EXCEEDED","MAX_UPSTREAM_ATTEMPTS"))
        self.assertTrue((trace/"sealed").is_file())

    def test_fresh_controller_cannot_bypass_lock_and_exact_resume_reclaims(self):
        env=self.fx.env(FAKE_TARGET_MODE="wait")
        first=subprocess.Popen(["bash",str(CONTROLLER)],env=env,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
        deadline=time.time()+10
        while not self.fx.capture.exists() and time.time()<deadline: time.sleep(.02)
        self.assertTrue(self.fx.capture.exists())
        second=self.fx.run(env=self.fx.env()); self.assertNotEqual(second.returncode,0)
        first.send_signal(signal.SIGKILL); first.wait(timeout=5)
        controller=self.fx.controller_dir()
        resumed=subprocess.Popen(["bash",str(CONTROLLER),"--resume",controller.name],env=self.fx.env(FAKE_TARGET_MODE="wait"),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        time.sleep(.2); (self.fx.base/"release-target").write_text("go\n")
        out=resumed.communicate(timeout=20); self.assertEqual(resumed.returncode,0,out)

    def test_fresh_controller_refuses_unowned_nonterminal_crash_record(self):
        abandoned=self.fx.controllers/"controller-abandoned"; abandoned.mkdir()
        row={"schema":1,"controller_id":abandoned.name,"phase":"TESTING",
             "updated_at":"2026-08-20T00:00:00.000Z","child_run_tag":"run-abandoned",
             "child_manifest_sha256":None,"reason":None}
        (abandoned/"status.json").write_text(json.dumps(row)+"\n",encoding="utf-8")
        result=self.fx.run()
        self.assertNotEqual(result.returncode,0)
        self.assertIn("BLOCKED_EXISTING_CONTROLLER",result.stderr)
        self.assertFalse(self.fx.capture.exists())

    def test_terminal_seal_is_no_replace_and_tamper_blocks_unknown(self):
        first=self.fx.run(); self.assertEqual(first.returncode,0)
        controller=self.fx.controller_dir(); link=json.loads((controller/"controller-child.json").read_text()); trace=Path(link["child_trace"])
        seal=(trace/"trace-manifest.json").read_bytes(); (trace/"candidate.json").write_text("tampered\n")
        resumed=self.fx.run("--resume",controller.name); self.assertNotEqual(resumed.returncode,0)
        self.assertEqual((trace/"trace-manifest.json").read_bytes(),seal)
        self.assertEqual(json.loads((controller/"status.json").read_text())["phase"],"BLOCKED_UNKNOWN")

    def test_terminal_resume_finishes_missing_sealed_marker_without_replacing_manifest(self):
        first=self.fx.run(); self.assertEqual(first.returncode,0)
        controller=self.fx.controller_dir(); link=json.loads((controller/"controller-child.json").read_text())
        trace=Path(link["child_trace"]); manifest=(trace/"trace-manifest.json").read_bytes()
        (trace/"sealed").unlink()
        resumed=self.fx.run("--resume",controller.name)
        self.assertEqual(resumed.returncode,0,(resumed.stdout,resumed.stderr))
        self.assertEqual((trace/"trace-manifest.json").read_bytes(),manifest)
        self.assertTrue((trace/"sealed").is_file())

    def test_crash_after_validator_start_resumes_without_second_validation(self):
        release=self.fx.base/"release-validator"
        env=self.fx.env(FAKE_VALIDATOR_MARKER=str(self.fx.validator_marker),
                        FAKE_VALIDATOR_RELEASE=str(release))
        first=subprocess.Popen(["bash",str(CONTROLLER)],env=env,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.time()+10
        while not self.fx.validator_marker.exists() and time.time()<deadline: time.sleep(.02)
        self.assertTrue(self.fx.validator_marker.exists())
        controller=self.fx.controller_dir()
        first.kill(); first.wait(timeout=5); release.write_text("go\n")
        link=json.loads((controller/"controller-child.json").read_text()); trace=Path(link["child_trace"])
        deadline=time.time()+10
        while not (trace/"validity.json").exists() and time.time()<deadline: time.sleep(.02)
        self.assertTrue((trace/"validity.json").exists())
        resumed=self.fx.run("--resume",controller.name)
        self.assertEqual(resumed.returncode,0,(resumed.stdout,resumed.stderr))
        self.assertEqual(self.fx.validator_count.read_text(),"1")

    def test_finite_token_limit_blocks_as_usage_unavailable(self):
        result=self.fx.run(env=self.fx.env(SHERLOCK_BUDGET_MAX_INPUT_TOKENS="100"))
        self.assertNotEqual(result.returncode,0)
        self.assertIn("BLOCKED_USAGE_UNAVAILABLE",result.stderr)
        self.assertFalse(self.fx.capture.exists())

    def test_controller_and_run_authority_roots_must_be_component_disjoint(self):
        cases = [
            (self.fx.controllers, self.fx.controllers),
            (self.fx.controllers, self.fx.controllers / "child-runs"),
            (self.fx.runs / "child-controller", self.fx.runs),
        ]
        for index, (controller_root, runs_root) in enumerate(cases):
            controller_root.mkdir(parents=True, exist_ok=True)
            runs_root.mkdir(parents=True, exist_ok=True)
            env = self.fx.env(SHERLOCK_CONTROLLER_ROOT=str(controller_root),
                              BENCH_RUNS=str(runs_root))
            result = self.fx.run(env=env)
            self.assertNotEqual(result.returncode, 0, (index, result.stdout, result.stderr))
            self.assertIn("AUTHORITY_ROOT_OVERLAP", result.stderr)
            self.assertFalse((controller_root / "keys/controller.key").exists())
            self.assertFalse(self.fx.capture.exists())

    def test_terminal_seal_rejects_symlink_directory_and_file_swap(self):
        result = self.fx.run(env=self.fx.env(FAKE_TARGET_MODE="symlink-dir"))
        self.assertNotEqual(result.returncode, 0)
        controller = self.fx.controller_dir()
        self.assertEqual(json.loads((controller / "status.json").read_text())["phase"],
                         "BLOCKED_UNKNOWN")
        link = json.loads((controller / "controller-child.json").read_text())
        self.assertFalse((Path(link["child_trace"]) / "sealed").exists())

        self.fx.close(); self.fx = ControllerFixture(self)
        outside = self.fx.base / "outside"; outside.write_text("outside\n")
        # The target path is known only after the controller chooses its tag, so
        # the validator derives it from its trace argument in the fixture.
        env = self.fx.env(FAKE_SWAP_PATH="candidate.json", FAKE_SWAP_TARGET=str(outside))
        result = self.fx.run(env=env)
        self.assertNotEqual(result.returncode, 0)
        controller = self.fx.controller_dir()
        self.assertEqual(json.loads((controller / "status.json").read_text())["phase"],
                         "BLOCKED_UNKNOWN")


if __name__ == "__main__":
    unittest.main(verbosity=2)
PY
