#!/usr/bin/env python3
"""Every ledger row must be self-describing.

Two forensic passes on 20260901T002401Z-v43 and 20260831T214240Z-v43 lost time
because `pre_send_refused` rows carry a different schema with no discriminator,
and because `ts` is second-resolution — a qwen auto-compaction at 22:51:31Z and
a driver boundary at 22:51:32Z were separable only by decoding a gzipped body.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.normpath(os.path.join(HERE, ".."))
PROXY = os.path.join(MEASURE, "upstream-log-proxy.py")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


src = open(PROXY, encoding="utf-8").read()

check('row.setdefault("kind"' in src,
      "record() does not stamp a `kind` discriminator on every row")
check('row["ts_ms"]' in src,
      "record() does not stamp a millisecond timestamp")
check('"messages_count"' in src,
      "the proxy never records how many messages the request carried — the "
      "only field that proves /clear actually cleared")
check('"session_id"' in src,
      "the proxy never records a session id")

# Exercise record() for real rather than trusting the grep.
tmp = tempfile.mkdtemp(prefix="ledger-v44-")
log = os.path.join(tmp, "upstream.jsonl")
script = (
    "import os, sys\n"
    "sys.argv = ['proxy']\n"
    "os.environ['UPSTREAM_LOG'] = %r\n"
    "import importlib.util\n"
    "spec = importlib.util.spec_from_loader('proxy', None)\n"
) % log
probe = os.path.join(tmp, "probe.py")
with open(probe, "w", encoding="utf-8") as fh:
    fh.write(
        "import os, sys, json, importlib.util\n"
        "os.environ['UPSTREAM_LOG'] = %r\n"
        "spec = importlib.util.spec_from_file_location('proxy', %r)\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "m.record(event='pre_send_refused', estimated_prompt_tokens=242345)\n"
        "m.record(usage={'prompt_tokens': 10}, finish_reason='stop',\n"
        "         messages_count=2, session_id='s1')\n"
        % (log, PROXY))
proc = subprocess.run([sys.executable, probe], capture_output=True, text=True)
check(proc.returncode == 0,
      "the probe could not import the proxy: %s" % proc.stderr[-400:])

rows = []
if os.path.exists(log):
    rows = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()]
check(len(rows) == 2, "expected 2 rows, got %d" % len(rows))
if len(rows) == 2:
    refusal, call = rows
    check(refusal.get("kind") == "refusal",
          "a refusal row is not marked kind=refusal: %r" % refusal.get("kind"))
    check(call.get("kind") == "call",
          "a call row is not marked kind=call: %r" % call.get("kind"))
    for row in rows:
        check(isinstance(row.get("ts_ms"), int) and row["ts_ms"] > 0,
              "row has no integer ts_ms: %r" % row.get("ts_ms"))
    check(call.get("messages_count") == 2, "messages_count not preserved")
    check(call.get("session_id") == "s1", "session_id not preserved")

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
