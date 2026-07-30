# Sherlock Measurement System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a per-defect measurement rig that answers, for every missed defect, whether the run *never opened the evidence* (coverage failure) or *opened it and failed to connect it* (reasoning failure) — deterministically, with no LLM call.

**Architecture:** Three layers. `slice.py` turns the answer key's `proof_locations` into per-defect case directories containing whole files. `run-case.sh` runs the skill against a case with `--output-format stream-json` and keeps the run directory forever. `measure.py` computes deterministic verdicts from `stream.jsonl`; `score_case.py` asks the one semantic question via a gpt-5.5 judge. `gate.sh` enforces the three-tier promotion rule.

**Tech Stack:** Python 3 stdlib only (no pip), bash + coreutils, `unittest`, the qwen CLI. Judge over HTTP via `urllib` to the cliproxyapi broker.

## Global Constraints

- **Stdlib only. No pip, no network in tests.** (AGENTS.md R1) — this includes NOT adopting the OpenTelemetry SDK.
- **The answer key and corpus must never enter this repo.** Paths arrive by env var: `SHERLOCK_CORPUS`, `SHERLOCK_ANSWER_KEY`. The repo holds code only. Putting the key in this public repo would spoil the case.
- **Model under test:** `[SP]deepseek-v4-flash` via `https://linkapi.ai/v1`, key `eval_linkapi_key`. This is the only sanctioned metered call.
- **Judge:** `gpt-5.5` via `http://127.0.0.1:8317/v1`, key `eval_broker_api_key` (subscription). Transport that works: plain `urllib` with an explicit `User-Agent`. `secret-curl.sh` returns `Missing API key` against this broker — do not use it to test broker auth.
- **Secrets never on argv.** Use `/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh <name> --env VAR -- <cmd>`.
- **A provider/runtime error is never recorded as a measurement.** Preserve the `run-bench.sh` guard: refuse rows whose text starts with `[API Error`.
- **Proof line numbers are 1-based physical lines** in the file as written. `nginx/access.log.1.gz` numbers refer to the DECOMPRESSED stream. Three files have truncated tails (`nginx/access.log`, `istio/ingressgateway-access.json.log`, `misc/inventory-svc-partial.log`) so `wc -l` reports one fewer line than the key's `lines` field.
- **Red herrings are identified the same way `eval/score.py` does it:** `"RED HERRING"` appears in `title + description` (upper-cased). There is no `red_herring` boolean in the data — D12 and D13 are the herrings.
- **New test classes must sit ABOVE `if __name__ == "__main__"`** — `tests/run.sh` executes `python3 <file>`, so a class defined after `unittest.main()` is silently skipped. This cost 14 unnoticed tests on 2026-07-30.
- **Every check needs a negative control**: prove it goes RED on a deliberately broken fixture before trusting it.

---

## File Structure

All new files live under `cases/06-dev-logging/sherlock/measure/`.

| file | responsibility |
|---|---|
| `slice.py` | answer key + corpus → per-defect case dirs (whole files, `case.json`) |
| `measure.py` | `stream.jsonl` + report → deterministic metrics and a verdict |
| `score_case.py` | one case's report → judge found/not-found (the one semantic call) |
| `run-case.sh` | run an arm against a case, capture `stream.jsonl`, never delete the run dir |
| `gate.sh` | three-tier promotion: one slice → all slices → full corpus |
| `README.md` | how to run it, and what a slice-green result does NOT prove |
| `tests/run.sh` | aggregator, mirrors `tools/tests/run.sh` |
| `tests/test_slice.py`, `tests/test_measure.py`, `tests/test_run_case.py`, `tests/test_score_case.py` | suites |
| `tests/fixtures/` | miniature answer key, miniature corpus, hand-written `stream.jsonl` files |

Python files use underscores where a test imports them (`score_case.py`). `slice.py` and `measure.py` are imported directly; `slice` is a builtin *function*, not a module, so the name does not shadow anything.

---

### Task 1: `slice.py` — per-defect case directories

**Files:**
- Create: `cases/06-dev-logging/sherlock/measure/slice.py`
- Create: `cases/06-dev-logging/sherlock/measure/tests/test_slice.py`
- Create: `cases/06-dev-logging/sherlock/measure/tests/run.sh`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `is_herring(defect) -> bool`, `proof_files(defect) -> list[str]`, `build_case(key: dict, corpus_dir: str, out_dir: str, defect_id: str) -> dict`. `build_case` writes `<out_dir>/<defect_id>/` containing the copied log files plus `case.json`, and returns the parsed `case.json`. `case.json` keys: `case_id`, `kind` (`"defect_slice"`), `defect_id`, `title`, `root_cause`, `requires`, `files` (list of relative paths), `proof_locations` (copied verbatim from the key).

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Tests for slice.py — per-defect case construction.

The load-bearing property is that slicing keeps WHOLE FILES, so the answer key's
1-based physical proof line numbers remain valid inside the slice with no
renumbering. A slice that shifted line numbers would silently invalidate every
proof-reach check built on top of it.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import slice as slicer  # noqa: E402


def tiny_key():
    return {
        "defects": [
            {"id": "D01", "title": "NPE in promo", "description": "boom",
             "root_cause": "normalized() returns null", "requires": "single-format read",
             "proof_locations": [
                 {"file": "apps/api.log", "line_start": 3, "line_end": 3, "note": "the NPE"},
                 {"file": "inhouse/promo.plog", "line_start": 2, "line_end": 2, "note": "input"},
             ]},
            {"id": "D12", "title": "RED HERRING: SYN flood", "description": "not a defect",
             "root_cause": "n/a", "requires": "statistical/rate reasoning to REFUTE",
             "proof_locations": [{"file": "syslog/node-b", "line_start": 1, "line_end": 1,
                                  "note": "noise"}]},
        ]
    }


def make_corpus(root):
    files = {
        "apps/api.log": "one\ntwo\nNullPointerException here\nfour\n",
        "inhouse/promo.plog": "hdr\ncode=summer26 rejected\n",
        "syslog/node-b": "syn flood\n",
        "unrelated/other.log": "nothing to see\n",
    }
    for rel, body in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
    return root


class BuildCase(unittest.TestCase):
    def test_slice_contains_only_proof_files(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            case = slicer.build_case(tiny_key(), corpus, out, "D01")
            self.assertEqual(sorted(case["files"]),
                             ["apps/api.log", "inhouse/promo.plog"])
            self.assertFalse(os.path.exists(os.path.join(out, "D01", "unrelated/other.log")),
                             "a file with no proof must not be copied into the slice")

    def test_whole_files_are_copied_so_line_numbers_still_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            slicer.build_case(tiny_key(), corpus, out, "D01")
            got = open(os.path.join(out, "D01", "apps/api.log"), encoding="utf-8").read()
            self.assertEqual(got, open(os.path.join(corpus, "apps/api.log"),
                                       encoding="utf-8").read())
            line3 = got.splitlines()[2]
            self.assertIn("NullPointerException", line3,
                          "proof at line 3 must still be at line 3 inside the slice")

    def test_case_json_carries_what_the_judge_needs(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            case = slicer.build_case(tiny_key(), corpus, out, "D01")
            self.assertEqual(case["case_id"], "D01")
            self.assertEqual(case["kind"], "defect_slice")
            self.assertEqual(case["root_cause"], "normalized() returns null")
            self.assertEqual(case["requires"], "single-format read")
            self.assertEqual(len(case["proof_locations"]), 2)
            on_disk = json.load(open(os.path.join(out, "D01", "case.json"), encoding="utf-8"))
            self.assertEqual(on_disk, case)

    def test_missing_source_file_is_a_hard_error(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            os.remove(os.path.join(corpus, "apps/api.log"))
            with self.assertRaises(FileNotFoundError):
                slicer.build_case(tiny_key(), corpus, os.path.join(d, "cases"), "D01")

    def test_proof_line_beyond_end_of_file_is_a_hard_error(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            key = tiny_key()
            key["defects"][0]["proof_locations"][0]["line_end"] = 9999
            with self.assertRaises(ValueError):
                slicer.build_case(key, corpus, os.path.join(d, "cases"), "D01")


class HerringDetection(unittest.TestCase):
    def test_red_herring_detected_from_title(self):
        self.assertTrue(slicer.is_herring(tiny_key()["defects"][1]))
        self.assertFalse(slicer.is_herring(tiny_key()["defects"][0]))

    def test_build_all_skips_herrings(self):
        with tempfile.TemporaryDirectory() as d:
            corpus = make_corpus(os.path.join(d, "corpus"))
            out = os.path.join(d, "cases")
            ids = slicer.build_all(tiny_key(), corpus, out)
            self.assertEqual(ids, ["D01"])
            self.assertFalse(os.path.exists(os.path.join(out, "D12")),
                             "a red herring is not a defect and gets no slice")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_slice.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'slice'`

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""slice.py — turn one planted defect into its own small corpus.

    python3 slice.py --key <answer-key.json> --corpus <dir> --out <dir> [--only D01]

Why whole files, not line windows: the answer key's proof line numbers are 1-based
PHYSICAL lines. Copying a whole file keeps every one of them valid inside the slice
with no renumbering, and keeps the within-file noise that makes the task realistic.
A line-window slice would be smaller and would quietly invalidate proof_reach.

A slice is EASIER than the full corpus. Slice-green does not imply corpus-green;
see measure/README.md and the three-tier gate.
"""
import argparse
import json
import os
import shutil
import sys


def is_herring(defect):
    """Same rule as eval/score.py — the data carries no boolean flag."""
    blob = "%s %s" % (defect.get("title", ""), defect.get("description", ""))
    return bool(defect.get("red_herring")) or "RED HERRING" in blob.upper()


def proof_files(defect):
    return sorted({p["file"] for p in defect.get("proof_locations", [])})


def _count_lines(path):
    n = 0
    with open(path, "rb") as fh:
        for _ in fh:
            n += 1
    return n


def build_case(key, corpus_dir, out_dir, defect_id):
    defect = next((d for d in key["defects"] if d["id"] == defect_id), None)
    if defect is None:
        raise KeyError("no defect %s in the answer key" % defect_id)

    case_dir = os.path.join(out_dir, defect_id)
    if os.path.isdir(case_dir):
        shutil.rmtree(case_dir)
    os.makedirs(case_dir, exist_ok=True)

    files = proof_files(defect)
    for rel in files:
        src = os.path.join(corpus_dir, rel)
        if not os.path.isfile(src):
            raise FileNotFoundError("proof file missing from corpus: %s" % src)
        dst = os.path.join(case_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    # A proof that points past the end of its file means the key and the corpus have
    # drifted apart. Fail loudly: every downstream verdict is built on these numbers.
    # .gz is exempt — its numbers are in the DECOMPRESSED stream, which we do not expand.
    for p in defect.get("proof_locations", []):
        if p["file"].endswith(".gz"):
            continue
        n = _count_lines(os.path.join(case_dir, p["file"]))
        if p["line_end"] > n + 1:      # +1 tolerates a truncated tail (no trailing newline)
            raise ValueError("proof %s:%d is past EOF (%d lines) — key/corpus drift"
                             % (p["file"], p["line_end"], n))

    case = {
        "case_id": defect_id,
        "kind": "defect_slice",
        "defect_id": defect_id,
        "title": defect.get("title", ""),
        "root_cause": defect.get("root_cause") or defect.get("description", ""),
        "requires": defect.get("requires", ""),
        "files": files,
        "proof_locations": defect.get("proof_locations", []),
    }
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False, indent=2)
    return case


def build_all(key, corpus_dir, out_dir):
    ids = []
    for d in key["defects"]:
        if is_herring(d):
            continue
        build_case(key, corpus_dir, out_dir, d["id"])
        ids.append(d["id"])
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("SHERLOCK_ANSWER_KEY"), required=False)
    ap.add_argument("--corpus", default=os.environ.get("SHERLOCK_CORPUS"), required=False)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", help="build just this defect id")
    a = ap.parse_args()
    if not a.key or not a.corpus:
        sys.exit("set --key/--corpus or SHERLOCK_ANSWER_KEY/SHERLOCK_CORPUS")
    key = json.load(open(a.key, encoding="utf-8"))
    if a.only:
        c = build_case(key, a.corpus, a.out, a.only)
        print("%s: %d file(s)" % (c["case_id"], len(c["files"])))
    else:
        for cid in build_all(key, a.corpus, a.out):
            c = json.load(open(os.path.join(a.out, cid, "case.json"), encoding="utf-8"))
            print("%s: %d file(s)  [%s]" % (cid, len(c["files"]), c["requires"]))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_slice.py`
Expected: PASS — `Ran 7 tests`, `OK`

- [ ] **Step 5: Create the test aggregator**

```bash
cat > cases/06-dev-logging/sherlock/measure/tests/run.sh <<'EOF'
#!/usr/bin/env bash
# Run every measurement-rig test. No pip, no network, no LLM.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RC=0
for t in "$HERE"/test_*.py; do
  echo "=== $(basename "$t")"
  python3 "$t" 2>&1 | tail -3
  [ "${PIPESTATUS[0]}" -eq 0 ] || RC=1
done
if [ $RC -eq 0 ]; then printf '\033[32m✓ measure: all suites green\033[0m\n';
else printf '\033[31m✗ measure: a suite failed\033[0m\n'; fi
exit $RC
EOF
chmod +x cases/06-dev-logging/sherlock/measure/tests/run.sh
cases/06-dev-logging/sherlock/measure/tests/run.sh
```
Expected: `✓ measure: all suites green`

- [ ] **Step 6: Verify against the REAL answer key (never committed)**

Run:
```bash
cd cases/06-dev-logging/sherlock/measure
SHERLOCK_ANSWER_KEY=$HOME/hack/case06-measure/hetero-answer-key/answer-key.json \
SHERLOCK_CORPUS=$HOME/hack/case06-measure/hetero-corpus \
python3 slice.py --out /tmp/cases-check
```
Expected: exactly 11 lines, D01…D11, no D12/D13. File counts must be
D09=1, D01=2, D02=2, D10=2, D11=2, D07=3, D08=3, D06=4, D04=5, D05=10, D03=11.
If any count differs, the key and corpus have drifted — stop and report, do not adjust the numbers to match.

- [ ] **Step 7: Commit**

```bash
git add cases/06-dev-logging/sherlock/measure/slice.py \
        cases/06-dev-logging/sherlock/measure/tests/test_slice.py \
        cases/06-dev-logging/sherlock/measure/tests/run.sh
git commit -m "case06 measure: slice.py builds per-defect case dirs from proof_locations"
```

---

### Task 2: `measure.py` — trajectory layer (what the run actually read)

**Files:**
- Create: `cases/06-dev-logging/sherlock/measure/measure.py`
- Create: `cases/06-dev-logging/sherlock/measure/tests/test_measure.py`

**Interfaces:**
- Consumes: `case.json` from Task 1.
- Produces: `read_events(stream_path) -> list[dict]` where each dict is
  `{"tool": str, "file": str|None, "line_start": int|None, "line_end": int|None, "range_known": bool, "raw": str}`;
  and `proof_reach(events, proof_locations) -> dict` returning
  `{"reached": [...], "not_reached": [...], "unknown": [...], "files_opened": [...], "files_with_proofs": [...], "verdict": "reached"|"not_reached"|"unknown"}`.

Field names follow the OTel GenAI vocabulary where they correspond (`gen_ai.tool.name` → `tool`), per the spec's OpenTelemetry note.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Tests for measure.py — deterministic verdicts from a captured run.

The load-bearing case is `test_right_file_wrong_lines_is_not_reached`: the whole
diagnosis rests on telling "never opened the evidence" apart from "opened it and
failed to connect it". If that distinction breaks, every verdict downstream is noise.

The three-valued verdict matters too. A shell-based read (`sed -n`, `grep`) often
cannot be resolved to a line range. Calling that "not reached" would manufacture
coverage failures that never happened, so it is reported as `unknown`.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import measure  # noqa: E402

PROOFS = [
    {"file": "apps/api.log", "line_start": 178977, "line_end": 178996, "note": "the NPE"},
]


def stream(*records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def tool_use(name, inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def tool_result(text):
    return {"type": "user",
            "message": {"content": [{"type": "tool_result", "content": text}]}}


class ReadEvents(unittest.TestCase):
    def test_read_file_offset_and_limit_become_a_line_range(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        ev = measure.read_events(p)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["range_known"])
        self.assertEqual(ev[0]["line_start"], 178971)
        self.assertEqual(ev[0]["line_end"], 179010)

    def test_tool_result_text_is_preferred_over_the_input(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 1, "limit": 2}),
                   tool_result("Read lines 2-3 of 4 from /c/apps/api.log"))
        ev = measure.read_events(p)
        self.assertEqual((ev[0]["line_start"], ev[0]["line_end"]), (2, 3))

    def test_shell_read_records_the_file_but_leaves_the_range_unknown(self):
        p = stream(tool_use("run_shell_command",
                            {"command": "grep -n 'NullPointer' /c/apps/api.log"}))
        ev = measure.read_events(p)
        self.assertEqual(ev[0]["file"], "/c/apps/api.log")
        self.assertFalse(ev[0]["range_known"])


class ProofReach(unittest.TestCase):
    def test_reading_the_proof_lines_counts_as_reached(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "reached")

    def test_right_file_wrong_lines_is_not_reached(self):
        # THE load-bearing case: it opened the file, but nowhere near the evidence.
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 0, "limit": 200}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")
        self.assertIn("apps/api.log", r["files_opened"])

    def test_never_opening_the_file_is_not_reached(self):
        p = stream(tool_use("read_file", {"file_path": "/c/other.log",
                                          "offset": 0, "limit": 10}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")
        self.assertEqual(r["files_opened"], ["other.log"])

    def test_unresolvable_shell_read_of_the_proof_file_is_unknown_not_a_failure(self):
        p = stream(tool_use("run_shell_command",
                            {"command": "sed -n '178977,178996p' /c/apps/api.log"}))
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "unknown",
                         "an unresolvable range must not be reported as a coverage failure")

    def test_empty_stream_is_not_reached(self):
        p = stream()
        r = measure.proof_reach(measure.read_events(p), PROOFS)
        self.assertEqual(r["verdict"], "not_reached")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_measure.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'measure'`

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""measure.py — deterministic verdicts from a captured run. No LLM, no network.

Answers the question the judge cannot: when a defect was missed, was the evidence
never opened (a COVERAGE failure — fix the tools or the file-selection guidance),
or was it read and not connected (a REASONING failure — fix synthesis)?

Three-valued on purpose. A shell read (`sed -n`, `grep`) often cannot be resolved to
a line range; reporting that as "not reached" would manufacture coverage failures
that never happened. Unresolvable reads of a proof file are `unknown`.

Field names follow the OTel GenAI attribute vocabulary where they correspond, so we
do not invent private terminology. We deliberately do NOT depend on the OTel SDK
(pip dependency; AGENTS.md R1 is stdlib-only).
"""
import json
import os
import re

READ_TOOLS = {"read_file", "read_many_files", "glob", "search_file_content",
              "grep_search", "list_directory"}
SHELL_TOOLS = {"run_shell_command", "shell", "bash"}

_RESULT_RANGE = re.compile(r"Read lines (\d+)-(\d+) of \d+ from (\S+)")
_PATHISH = re.compile(r"(/[^\s'\"]+|[\w./-]+\.(?:log|json|txt|plog|gz|out))")


def _blocks(rec, kind):
    msg = rec.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind]


def read_events(stream_path):
    """Every tool call that could have put log bytes in front of the model."""
    events = []
    pending = None
    with open(stream_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue

            for b in _blocks(rec, "tool_use"):
                name = b.get("name") or ""
                inp = b.get("input") or {}
                ev = {"tool": name, "file": None, "line_start": None,
                      "line_end": None, "range_known": False,
                      "raw": json.dumps(inp, ensure_ascii=False)[:400]}
                if name in READ_TOOLS:
                    ev["file"] = inp.get("file_path") or inp.get("path") or inp.get("absolute_path")
                    off, lim = inp.get("offset"), inp.get("limit")
                    if isinstance(off, int) and isinstance(lim, int) and lim > 0:
                        ev["line_start"] = off + 1
                        ev["line_end"] = off + lim
                        ev["range_known"] = True
                elif name in SHELL_TOOLS:
                    cmd = inp.get("command") or ""
                    ev["raw"] = cmd[:400]
                    m = _PATHISH.search(cmd)
                    if m:
                        ev["file"] = m.group(1)
                if ev["file"] or ev["tool"]:
                    events.append(ev)
                    pending = ev

            for b in _blocks(rec, "tool_result"):
                content = b.get("content")
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                m = _RESULT_RANGE.search(text or "")
                if m and pending is not None:
                    # The result is authoritative: it reports what was ACTUALLY read.
                    pending["line_start"] = int(m.group(1))
                    pending["line_end"] = int(m.group(2))
                    pending["range_known"] = True
                    pending["file"] = m.group(3)
    return events


def _tail(path):
    """Compare on the trailing path so /tmp/case/apps/api.log matches apps/api.log."""
    return (path or "").replace("\\", "/").lstrip("./")


def _same_file(event_path, proof_rel):
    e, p = _tail(event_path), _tail(proof_rel)
    return e.endswith(p) or p.endswith(os.path.basename(e)) and os.path.basename(e) == os.path.basename(p)


def proof_reach(events, proof_locations):
    reached, not_reached, unknown = [], [], []
    for pr in proof_locations:
        state = "not_reached"
        for ev in events:
            if not ev.get("file") or not _same_file(ev["file"], pr["file"]):
                continue
            if not ev["range_known"]:
                state = "unknown"
                continue
            if ev["line_start"] <= pr["line_end"] and ev["line_end"] >= pr["line_start"]:
                state = "reached"
                break
        {"reached": reached, "not_reached": not_reached, "unknown": unknown}[state].append(
            "%s:%d-%d" % (pr["file"], pr["line_start"], pr["line_end"]))

    files_opened = sorted({os.path.basename(_tail(e["file"])) for e in events if e.get("file")})
    files_with_proofs = sorted({os.path.basename(p["file"]) for p in proof_locations})
    verdict = "reached" if reached else ("unknown" if unknown else "not_reached")
    return {"reached": reached, "not_reached": not_reached, "unknown": unknown,
            "files_opened": files_opened, "files_with_proofs": files_with_proofs,
            "verdict": verdict}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_measure.py`
Expected: PASS — `Ran 9 tests`, `OK`

- [ ] **Step 5: Negative control — prove the load-bearing check can fail**

Run:
```bash
cd cases/06-dev-logging/sherlock/measure
cp measure.py /tmp/measure.bak
python3 - <<'PY'
s = open("measure.py").read()
s = s.replace('verdict = "reached" if reached else', 'verdict = "reached" if True else')
open("measure.py", "w").write(s)
PY
python3 tests/test_measure.py 2>&1 | tail -3
cp /tmp/measure.bak measure.py && rm /tmp/measure.bak
python3 tests/test_measure.py 2>&1 | tail -3
```
Expected: sabotaged run reports `FAILED` naming `test_right_file_wrong_lines_is_not_reached`; restored run reports `OK`.
An assertion nobody has watched fail is not evidence. Record both outputs in the commit message.

- [ ] **Step 6: Commit**

```bash
git add cases/06-dev-logging/sherlock/measure/measure.py \
        cases/06-dev-logging/sherlock/measure/tests/test_measure.py
git commit -m "case06 measure: trajectory layer — proof-reach from stream.jsonl (3-valued)"
```

---

### Task 3: `measure.py` — report layer and the combined verdict

**Files:**
- Modify: `cases/06-dev-logging/sherlock/measure/measure.py` (append)
- Modify: `cases/06-dev-logging/sherlock/measure/tests/test_measure.py` (append ABOVE `if __name__`)

**Interfaces:**
- Consumes: `read_events`, `proof_reach` from Task 2.
- Produces: `report_checks(report_text) -> dict` with keys `sections_present` (list), `sections_missing` (list), `has_knowledge_line` (bool), `collapsed` (bool), `collapse_reason` (str|None), `chars` (int); `budget_profile(events) -> dict` with `tool_calls` (int), `by_tool` (dict); and `verdict(case, stream_path, report_text, judge_found) -> dict` with `diagnosis` in `{"coverage", "reasoning", "fabricated_evidence", "collapse", "ok", "inconclusive"}`.

- [ ] **Step 1: Write the failing test (append above `if __name__`)**

```python
REPORT_OK = """
## 1. Что произошло
Каскад.
## 2. Корневая причина
Не хватало индекса.
## 3. Цепочка причин
шаг
## 4. Улики
apps/api.log:178977
## 5. Немедленные действия
поднять
## 6. Исправление в коде
файл
## 7. Чего я не знаю
многого
## 8. ЗНАНИЯ
ЗНАНИЯ: база пуста — обычное расследование
"""

REPORT_COLLAPSED = "Все агенты завершили работу. Отчёт выше уже содержит все находки."


class ReportChecks(unittest.TestCase):
    def test_all_eight_sections_detected(self):
        r = measure.report_checks(REPORT_OK)
        self.assertEqual(r["sections_missing"], [])
        self.assertTrue(r["has_knowledge_line"])
        self.assertFalse(r["collapsed"])

    def test_missing_root_cause_and_unknowns_are_named(self):
        text = REPORT_OK.replace("## 2. Корневая причина", "").replace("## 7. Чего я не знаю", "")
        r = measure.report_checks(text)
        self.assertIn("Корневая причина", r["sections_missing"])
        self.assertIn("Чего я не знаю", r["sections_missing"])

    def test_collapse_detected_by_banned_phrase(self):
        r = measure.report_checks(REPORT_COLLAPSED)
        self.assertTrue(r["collapsed"])
        self.assertIn("отчёт выше", r["collapse_reason"].lower())

    def test_collapse_detected_by_length(self):
        r = measure.report_checks("слишком коротко")
        self.assertTrue(r["collapsed"])


class BudgetProfile(unittest.TestCase):
    def test_counts_calls_by_tool(self):
        p = stream(tool_use("read_file", {"file_path": "/c/a.log", "offset": 0, "limit": 5}),
                   tool_use("read_file", {"file_path": "/c/b.log", "offset": 0, "limit": 5}),
                   tool_use("run_shell_command", {"command": "ls /c"}))
        b = measure.budget_profile(measure.read_events(p))
        self.assertEqual(b["tool_calls"], 3)
        self.assertEqual(b["by_tool"]["read_file"], 2)


class CombinedVerdict(unittest.TestCase):
    CASE = {"case_id": "D01", "proof_locations": PROOFS}

    def test_missed_and_never_read_is_coverage(self):
        p = stream(tool_use("read_file", {"file_path": "/c/other.log", "offset": 0, "limit": 5}))
        v = measure.verdict(self.CASE, p, REPORT_OK, judge_found=False)
        self.assertEqual(v["diagnosis"], "coverage")

    def test_missed_but_did_read_is_reasoning(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        v = measure.verdict(self.CASE, p, REPORT_OK, judge_found=False)
        self.assertEqual(v["diagnosis"], "reasoning")

    def test_collapse_outranks_everything(self):
        p = stream()
        v = measure.verdict(self.CASE, p, REPORT_COLLAPSED, judge_found=False)
        self.assertEqual(v["diagnosis"], "collapse")

    def test_found_is_ok(self):
        p = stream(tool_use("read_file", {"file_path": "/c/apps/api.log",
                                          "offset": 178970, "limit": 40}))
        v = measure.verdict(self.CASE, p, REPORT_OK, judge_found=True)
        self.assertEqual(v["diagnosis"], "ok")

    def test_unknown_reach_is_inconclusive_not_coverage(self):
        p = stream(tool_use("run_shell_command",
                            {"command": "sed -n '178977,178996p' /c/apps/api.log"}))
        v = measure.verdict(self.CASE, p, REPORT_OK, judge_found=False)
        self.assertEqual(v["diagnosis"], "inconclusive")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_measure.py`
Expected: FAIL — `AttributeError: module 'measure' has no attribute 'report_checks'`

- [ ] **Step 3: Write minimal implementation (append to `measure.py`)**

```python
# --------------------------------------------------------------- report layer
SECTIONS = ["Что произошло", "Корневая причина", "Цепочка причин", "Улики",
            "Немедленные действия", "Исправление в коде", "Чего я не знаю", "ЗНАНИЯ"]

# SKILL.md forbids these outright: the user sees ONLY the final message, so a
# reference to an earlier one means no report was delivered at all.
BANNED = ["отчёт выше", "как я уже показал", "результаты приведены ранее",
          "см. предыдущее сообщение", "отчёт уже готов выше"]

MIN_REPORT_CHARS = 2000


def report_checks(report_text):
    text = report_text or ""
    low = text.lower()
    present = [s for s in SECTIONS if s.lower() in low]
    missing = [s for s in SECTIONS if s.lower() not in low]
    banned_hit = next((b for b in BANNED if b in low), None)
    collapsed, reason = False, None
    if banned_hit:
        collapsed, reason = True, "banned phrase: %s" % banned_hit
    elif len(text) < MIN_REPORT_CHARS:
        collapsed, reason = True, "report is %d chars (< %d)" % (len(text), MIN_REPORT_CHARS)
    return {"sections_present": present, "sections_missing": missing,
            "has_knowledge_line": "знания:" in low,
            "collapsed": collapsed, "collapse_reason": reason, "chars": len(text)}


def budget_profile(events):
    by_tool = {}
    for e in events:
        by_tool[e["tool"]] = by_tool.get(e["tool"], 0) + 1
    return {"tool_calls": len(events), "by_tool": by_tool}


def verdict(case, stream_path, report_text, judge_found):
    events = read_events(stream_path)
    reach = proof_reach(events, case.get("proof_locations", []))
    report = report_checks(report_text)
    budget = budget_profile(events)

    # Order matters. A collapsed report means no investigation was delivered at all,
    # so neither "coverage" nor "reasoning" describes what happened.
    if report["collapsed"]:
        diagnosis = "collapse"
    elif judge_found:
        diagnosis = "ok"
    elif reach["verdict"] == "reached":
        diagnosis = "reasoning"
    elif reach["verdict"] == "unknown":
        diagnosis = "inconclusive"
    else:
        diagnosis = "coverage"

    return {"case_id": case.get("case_id"), "diagnosis": diagnosis,
            "judge_found": bool(judge_found), "requires": case.get("requires", ""),
            "reach": reach, "report": report, "budget": budget}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_measure.py`
Expected: PASS — `Ran 19 tests`, `OK`

- [ ] **Step 5: Commit**

```bash
git add cases/06-dev-logging/sherlock/measure/measure.py \
        cases/06-dev-logging/sherlock/measure/tests/test_measure.py
git commit -m "case06 measure: report layer + combined coverage/reasoning verdict"
```

---

### Task 4: `run-case.sh` — capture that keeps the evidence

**Files:**
- Create: `cases/06-dev-logging/sherlock/measure/run-case.sh`
- Create: `cases/06-dev-logging/sherlock/measure/tests/test_run_case.py`

**Interfaces:**
- Consumes: a case dir from Task 1.
- Produces: `runs/<UTC-timestamp>-<case_id>-<arm>/` containing `stream.jsonl`, `report.md`, `meta.json`. `meta.json` keys: `case_id`, `arm`, `model`, `started_at`, `duration_s`, `exit_code`, `input_tokens`, `output_tokens`, `answer_chars`.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Tests for run-case.sh — the capture runner.

The point of this suite is the thing every previous measurement got wrong:
run-bench.sh discarded its temp dir, so no run ever left a trace behind. These tests
assert the runner really invoked the CLI, really wrote stream.jsonl, and did NOT
delete the run directory afterwards.

A stub `qwen` first on PATH keeps it network-free: same technique as
tools/tests/test_fetch_logs.py.
"""
import json
import os
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
RUNNER = os.path.join(MEASURE, "run-case.sh")

STUB = r"""#!/usr/bin/env bash
printf '%s\0' "$@" >> "$QWEN_STUB_LOG"
cat <<'JSON'
{"type":"system","subtype":"init","model":"[SP]deepseek-v4-flash"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"read_file","input":{"file_path":"/c/apps/api.log","offset":0,"limit":10}}]}}
{"type":"result","result":"# Отчёт\nдлинный текст","num_turns":2,"usage":{"input_tokens":11,"output_tokens":22}}
JSON
exit 0
"""


def make_case(d):
    case_dir = os.path.join(d, "cases", "D01")
    os.makedirs(case_dir)
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "D01", "kind": "defect_slice", "files": ["apps/api.log"],
                   "root_cause": "x", "requires": "single-format read",
                   "proof_locations": []}, fh)
    os.makedirs(os.path.join(case_dir, "apps"))
    with open(os.path.join(case_dir, "apps", "api.log"), "w", encoding="utf-8") as fh:
        fh.write("one\ntwo\n")
    return case_dir


def make_stub(d):
    binp = os.path.join(d, "bin")
    os.makedirs(binp, exist_ok=True)
    p = os.path.join(binp, "qwen")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(STUB)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return binp


class TheRunnerActuallyRan(unittest.TestCase):
    def go(self, d):
        case_dir = make_case(d)
        binp = make_stub(d)
        log = os.path.join(d, "stub.log")
        skills = os.path.join(d, "skills", "v6")
        os.makedirs(skills)
        with open(os.path.join(skills, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: sherlock\n---\n")
        env = dict(os.environ)
        env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                    "QWEN_STUB_LOG": log, "QWEN_BIN": os.path.join(binp, "qwen"),
                    "SHERLOCK_SKILLS": os.path.dirname(skills),
                    "SHERLOCK_RUNS": os.path.join(d, "runs"),
                    "SHERLOCK_API_KEY": "stub-key"})
        p = subprocess.run(["bash", RUNNER, case_dir, "v6"], capture_output=True,
                           text=True, env=env, timeout=60)
        return p, log, os.path.join(d, "runs")

    def test_it_invokes_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p, log, _ = self.go(d)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(os.path.exists(log), "the CLI was never invoked")

    def test_it_keeps_the_run_directory(self):
        with tempfile.TemporaryDirectory() as d:
            p, _, runs = self.go(d)
            dirs = os.listdir(runs)
            self.assertEqual(len(dirs), 1, "expected exactly one run dir, got %r" % dirs)
            rd = os.path.join(runs, dirs[0])
            for f in ("stream.jsonl", "report.md", "meta.json"):
                self.assertTrue(os.path.exists(os.path.join(rd, f)), "missing %s" % f)

    def test_stream_jsonl_holds_every_record(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, runs = self.go(d)
            rd = os.path.join(runs, os.listdir(runs)[0])
            kinds = [json.loads(l)["type"] for l in open(os.path.join(rd, "stream.jsonl"))
                     if l.strip()]
            self.assertEqual(kinds, ["system", "assistant", "result"])

    def test_meta_records_the_arm_and_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, runs = self.go(d)
            rd = os.path.join(runs, os.listdir(runs)[0])
            meta = json.load(open(os.path.join(rd, "meta.json"), encoding="utf-8"))
            self.assertEqual(meta["arm"], "v6")
            self.assertEqual(meta["case_id"], "D01")
            self.assertEqual(meta["input_tokens"], 11)
            self.assertEqual(meta["output_tokens"], 22)

    def test_a_provider_error_is_refused_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            case_dir = make_case(d)
            binp = make_stub(d)
            bad = os.path.join(binp, "qwen")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('#!/usr/bin/env bash\n'
                         'echo \'{"type":"result","result":"[API Error: 400 Upstream request failed]"}\'\n')
            os.chmod(bad, 0o755)
            env = dict(os.environ)
            env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                        "QWEN_STUB_LOG": os.path.join(d, "l"),
                        "QWEN_BIN": bad, "SHERLOCK_RUNS": os.path.join(d, "runs"),
                        "SHERLOCK_SKILLS": d, "SHERLOCK_API_KEY": "stub-key"})
            p = subprocess.run(["bash", RUNNER, case_dir, "v6"], capture_output=True,
                               text=True, env=env, timeout=60)
            self.assertNotEqual(p.returncode, 0,
                                "a provider error must fail loudly, never record a row")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_run_case.py`
Expected: FAIL — `bash: .../run-case.sh: No such file or directory`

- [ ] **Step 3: Write minimal implementation**

```bash
#!/usr/bin/env bash
# run-case.sh — run one arm against one case and KEEP the evidence.
#
#   run-case.sh <case-dir> <arm>
#
# The one thing this does that run-bench.sh did not: `--output-format stream-json`
# instead of `json`, teed to a run directory that is NEVER deleted. Every previous
# measurement discarded the step-by-step record, which is why we could report a
# score but never a cause.
set -uo pipefail

CASE_DIR="${1:?usage: run-case.sh <case-dir> <arm>}"
ARM="${2:?usage: run-case.sh <case-dir> <arm>}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QWEN="${QWEN_BIN:-$HOME/.local/bin/qwen}"
SKILLS="${SHERLOCK_SKILLS:-$(cd "$HERE/.." && pwd)/skills}"
RUNS="${SHERLOCK_RUNS:-$HERE/runs}"
BASE_URL="${SHERLOCK_BASE_URL:-https://linkapi.ai/v1}"
MODEL="${SHERLOCK_MODEL:-[SP]deepseek-v4-flash}"
TIMEOUT="${SHERLOCK_TIMEOUT:-2700}"
: "${SHERLOCK_API_KEY:?set SHERLOCK_API_KEY (use with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ...)}"

[ -d "$CASE_DIR" ] || { echo "✗ no such case: $CASE_DIR" >&2; exit 1; }
CASE_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["case_id"])' \
  "$CASE_DIR/case.json")" || { echo "✗ unreadable case.json" >&2; exit 1; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$RUNS/$STAMP-$CASE_ID-$ARM"
mkdir -p "$RUN_DIR" || { echo "✗ cannot create $RUN_DIR" >&2; exit 1; }

W="$(mktemp -d "${TMPDIR:-/tmp}/runcase-XXXXXX")"
trap 'rm -rf "$W"' EXIT        # the SCRATCH dir goes; the RUN dir stays.
export QWEN_HOME="$W/home"; mkdir -p "$QWEN_HOME"

if [ "$ARM" != "none" ]; then
  [ -f "$SKILLS/$ARM/SKILL.md" ] || { echo "✗ no skill at $SKILLS/$ARM/SKILL.md" >&2; exit 1; }
  mkdir -p "$W/.qwen/skills"
  cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca" || exit 1
fi

PROMPT="Продакшн деградировал. Логи со всей платформы лежат в $CASE_DIR.
Найди ВСЕ проблемы и инциденты, определи корневую причину каждой и предложи,
что делать. Ссылайся на конкретные строки в формате файл:строка."

START=$(date +%s)
# The key travels by ENVIRONMENT, never argv: /proc/<pid>/cmdline is world-readable.
( cd "$W" && OPENAI_API_KEY="$SHERLOCK_API_KEY" OPENAI_BASE_URL="$BASE_URL" \
    QWEN_CODE_SUPPRESS_YOLO_WARNING=1 \
    timeout "$TIMEOUT" "$QWEN" --auth-type openai --model "$MODEL" \
      --approval-mode yolo -p "$PROMPT" --output-format stream-json </dev/null \
) > "$RUN_DIR/stream.jsonl" 2> "$RUN_DIR/stderr.txt"
RC=$?
ELAPSED=$(( $(date +%s) - START ))

python3 - "$RUN_DIR" "$CASE_ID" "$ARM" "$ELAPSED" "$RC" "$MODEL" <<'PY'
import json, sys, os
run_dir, case_id, arm, elapsed, rc, model = sys.argv[1:7]
final = None
for line in open(os.path.join(run_dir, "stream.jsonl"), encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get("type") == "result":
        final = r
if final is None:
    print("  ✗ no final result record — NOT recorded"); sys.exit(2)

text = final.get("result") or ""
# Same guard as run-bench.sh: qwen reports some provider failures as a SUCCESSFUL
# record whose result is the error text. Two such rows polluted a ledger on 2026-07-28.
if text.lstrip().startswith("[API Error") or ("[API Error" in text and len(text) < 400):
    print("  ✗ provider/run error, NOT recorded: %s" % text[:160].replace("\n", " "))
    sys.exit(3)

with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as fh:
    fh.write(text)
u = final.get("usage") or {}
meta = {"case_id": case_id, "arm": arm, "model": model,
        "started_at": os.path.basename(run_dir).split("-")[0],
        "duration_s": int(elapsed), "exit_code": int(rc),
        "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
        "answer_chars": len(text), "turns": final.get("num_turns")}
with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as fh:
    json.dump(meta, fh, ensure_ascii=False, indent=2)
print("  ✓ %s/%s  %ss  chars=%d  -> %s" % (case_id, arm, elapsed, len(text), run_dir))
PY
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
chmod +x cases/06-dev-logging/sherlock/measure/run-case.sh
cd cases/06-dev-logging/sherlock/measure && python3 tests/test_run_case.py
```
Expected: PASS — `Ran 5 tests`, `OK`

- [ ] **Step 5: Commit**

```bash
git add cases/06-dev-logging/sherlock/measure/run-case.sh \
        cases/06-dev-logging/sherlock/measure/tests/test_run_case.py
git commit -m "case06 measure: run-case.sh captures stream-json and keeps the run dir"
```

---

### Task 5: `score_case.py` — the one semantic question

**Files:**
- Create: `cases/06-dev-logging/sherlock/measure/score_case.py`
- Create: `cases/06-dev-logging/sherlock/measure/tests/test_score_case.py`

**Interfaces:**
- Consumes: `case.json` (Task 1), `report.md` (Task 4).
- Produces: `build_prompt(case, report) -> str`; `parse_verdict(text) -> dict` with keys `found` (bool), `why` (str); `score(case, report, call) -> dict` where `call(prompt) -> str` is injected so tests need no network.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Tests for score_case.py — the judge call, with the transport injected.

No network: `score()` takes a `call(prompt) -> str` so the HTTP layer can be stubbed.
The judge is gpt-5.5 on the cliproxyapi broker, chosen because it is neutral to BOTH
the model under test (deepseek) and the skill's author (Claude), and because it
reproduces the historical scores.jsonl column.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import score_case  # noqa: E402

CASE = {"case_id": "D04", "title": "un-indexed JSONB vendor_ref lookup",
        "root_cause": "catalog-svc 4.7.2 introduced a seq-scan with no expression index",
        "requires": "cross-format correlation", "proof_locations": []}


class Prompt(unittest.TestCase):
    def test_prompt_carries_the_root_cause_and_the_report(self):
        p = score_case.build_prompt(CASE, "мой отчёт про индекс")
        self.assertIn("catalog-svc 4.7.2", p)
        self.assertIn("мой отчёт про индекс", p)
        self.assertIn("D04", p)


class ParseVerdict(unittest.TestCase):
    def test_plain_json(self):
        v = score_case.parse_verdict('{"found": true, "why": "names the index"}')
        self.assertTrue(v["found"])

    def test_fenced_json_is_unwrapped(self):
        v = score_case.parse_verdict('```json\n{"found": false, "why": "no"}\n```')
        self.assertFalse(v["found"])

    def test_unparseable_output_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            score_case.parse_verdict("I think it probably found it, yes")


class Score(unittest.TestCase):
    def test_score_uses_the_injected_transport(self):
        seen = {}

        def fake(prompt):
            seen["prompt"] = prompt
            return '{"found": true, "why": "identifies the missing index"}'

        r = score_case.score(CASE, "отчёт", fake)
        self.assertTrue(r["found"])
        self.assertEqual(r["case_id"], "D04")
        self.assertIn("catalog-svc 4.7.2", seen["prompt"])

    def test_a_transport_error_is_not_a_not_found(self):
        def boom(prompt):
            raise RuntimeError("400 Upstream request failed")

        with self.assertRaises(RuntimeError):
            score_case.score(CASE, "отчёт", boom)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_score_case.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'score_case'`

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""score_case.py — ask the judge the ONE question deterministic checks cannot.

    JUDGE_API_KEY=... python3 score_case.py --case <case-dir> --run <run-dir>

Judge: gpt-5.5 via the cliproxyapi broker (subscription). It is neutral to both the
model under test (deepseek) and the skill's author (Claude), and it reproduces the
historical eval/scores.jsonl column, so numbers stay comparable across sessions.

Transport note: plain urllib with an explicit User-Agent works against this broker.
secret-curl.sh's -K config path returns "Missing API key" — do not use it here.
A transport failure RAISES; it must never be silently recorded as "not found",
which would look identical to a real miss.
"""
import argparse
import json
import os
import sys
import urllib.request

JUDGE_URL = os.environ.get("JUDGE_BASE_URL", "http://127.0.0.1:8317/v1")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gpt-5.5")

PROMPT = """You are grading an incident-investigation report against ONE known defect.

DEFECT {case_id}: {title}
ROOT CAUSE: {root_cause}

THE REPORT UNDER TEST:
{report}

Did the report identify THIS defect? Count it as found only if it identifies the same
underlying problem — different wording is fine, a vague gesture at the same area is
NOT. Partial credit does not exist.

Return STRICT JSON, no prose, no markdown fence:
{{"found": true|false, "why": "<one sentence>"}}"""


def build_prompt(case, report):
    return PROMPT.format(case_id=case.get("case_id", "?"), title=case.get("title", ""),
                         root_cause=case.get("root_cause", ""), report=(report or "")[:120000])


def parse_verdict(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        d = json.loads(t)
    except ValueError:
        raise ValueError("judge did not return JSON: %r" % (text or "")[:200])
    if "found" not in d:
        raise ValueError("judge JSON has no 'found' key: %r" % t[:200])
    return {"found": bool(d["found"]), "why": d.get("why", "")}


def http_call(prompt):
    key = os.environ.get("JUDGE_API_KEY")
    if not key:
        sys.exit("set JUDGE_API_KEY (with-secret.sh eval_broker_api_key --env JUDGE_API_KEY -- ...)")
    body = json.dumps({"model": JUDGE_MODEL,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(JUDGE_URL.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json",
                                          "User-Agent": "sherlock-measure/1.0"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    return out["choices"][0]["message"]["content"]


def score(case, report, call=http_call):
    v = parse_verdict(call(build_prompt(case, report)))
    return {"case_id": case.get("case_id"), "found": v["found"], "why": v["why"],
            "judge_model": JUDGE_MODEL}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    case = json.load(open(os.path.join(a.case, "case.json"), encoding="utf-8"))
    report = open(os.path.join(a.run, "report.md"), encoding="utf-8").read()
    r = score(case, report)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd cases/06-dev-logging/sherlock/measure && python3 tests/test_score_case.py`
Expected: PASS — `Ran 6 tests`, `OK`

- [ ] **Step 5: Prove the live judge transport works (one call, subscription path)**

Run:
```bash
cd cases/06-dev-logging/sherlock/measure
mkdir -p /tmp/jc && cat > /tmp/jc/case.json <<'EOF'
{"case_id":"D04","title":"un-indexed JSONB vendor_ref lookup",
 "root_cause":"catalog-svc 4.7.2 introduced a seq-scan with no expression index"}
EOF
mkdir -p /tmp/jr && printf 'catalog-svc 4.7.2 добавил запрос по attrs->>vendor_ref без индекса\n' > /tmp/jr/report.md
/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh \
  eval_broker_api_key --env JUDGE_API_KEY -- python3 score_case.py --case /tmp/jc --run /tmp/jr
```
Expected: one JSON line with `"found": true`. If it returns `Missing API key`, the secret name is wrong — `eval_broker_api_key` is the working one; `cliproxyapi_api_key` 401s against this broker.

- [ ] **Step 6: Commit**

```bash
git add cases/06-dev-logging/sherlock/measure/score_case.py \
        cases/06-dev-logging/sherlock/measure/tests/test_score_case.py
git commit -m "case06 measure: score_case.py — per-defect judge on the broker"
```

---

### Task 6: `gate.sh` — the three-tier promotion rule, plus README

**Files:**
- Create: `cases/06-dev-logging/sherlock/measure/gate.sh`
- Create: `cases/06-dev-logging/sherlock/measure/README.md`
- Modify: `cases/06-dev-logging/sherlock/tools/tests/run.sh` — no change needed; instead add a line to `cases/06-dev-logging/README.md` pointing at `measure/tests/run.sh`.

**Interfaces:**
- Consumes: `run-case.sh` (Task 4), `score_case.py` (Task 5), `measure.py` (Tasks 2–3).
- Produces: `gate.sh <tier> <arm> [case_id]` writing `results.jsonl` rows: `{case_id, arm, diagnosis, judge_found, requires, run_dir, tier}`.

- [ ] **Step 1: Write `gate.sh`**

```bash
#!/usr/bin/env bash
# gate.sh — promote a change through three tiers, cheapest first.
#
#   gate.sh 1 v6 D11     # iterate: the one slice being fixed
#   gate.sh 2 v6         # regress: ALL slices — mandatory before acceptance
#   gate.sh 3 v6         # accept:  the full 649MB corpus (metered)
#
# Tier 2 exists because partial runs miss interaction effects: fixing D11's coverage
# by widening a search instruction can silently blow D03's context budget. NO CHANGE
# IS ACCEPTED ON A TIER-1 PASS ALONE, and only a tier-3 number may be quoted as a
# benchmark result — a slice is an easier task than the corpus.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIER="${1:?usage: gate.sh <1|2|3> <arm> [case_id]}"
ARM="${2:?usage: gate.sh <1|2|3> <arm> [case_id]}"
ONLY="${3:-}"
CASES="${SHERLOCK_CASES:-$HERE/cases}"
RESULTS="${SHERLOCK_RESULTS:-$HERE/results.jsonl}"

run_one() {
  local case_dir="$1" out rd
  out="$("$HERE/run-case.sh" "$case_dir" "$ARM")" || { echo "$out"; return 1; }
  echo "$out"
  rd="${out##*-> }"
  python3 "$HERE/report-case.py" --case "$case_dir" --run "$rd" --tier "$TIER" \
    --results "$RESULTS"
}

case "$TIER" in
  1) [ -n "$ONLY" ] || { echo "tier 1 needs a case id" >&2; exit 1; }
     run_one "$CASES/$ONLY" ;;
  2) rc=0
     for c in "$CASES"/D*; do [ -d "$c" ] || continue; run_one "$c" || rc=1; done
     exit $rc ;;
  3) : "${SHERLOCK_CORPUS:?tier 3 needs SHERLOCK_CORPUS}"
     echo "tier 3: run eval/bench/run-bench.sh $ARM against the full corpus," \
          "then score with eval/score.py. Only this number is quotable." >&2
     exit 0 ;;
  *) echo "bad tier: $TIER" >&2; exit 1 ;;
esac
```

- [ ] **Step 2: Write `report-case.py` (the glue gate.sh calls)**

```python
#!/usr/bin/env python3
"""report-case.py — judge one run, compute the deterministic verdict, append a row."""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import measure, score_case  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--case", required=True)
ap.add_argument("--run", required=True)
ap.add_argument("--tier", default="1")
ap.add_argument("--results", required=True)
a = ap.parse_args()

case = json.load(open(os.path.join(a.case, "case.json"), encoding="utf-8"))
report = open(os.path.join(a.run, "report.md"), encoding="utf-8").read()
stream = os.path.join(a.run, "stream.jsonl")

judged = score_case.score(case, report)
v = measure.verdict(case, stream, report, judged["found"])
row = {"case_id": v["case_id"], "arm": json.load(
           open(os.path.join(a.run, "meta.json"), encoding="utf-8"))["arm"],
       "tier": a.tier, "diagnosis": v["diagnosis"], "judge_found": v["judge_found"],
       "why": judged["why"], "requires": v["requires"],
       "files_opened": len(v["reach"]["files_opened"]),
       "proofs_reached": len(v["reach"]["reached"]),
       "tool_calls": v["budget"]["tool_calls"], "run_dir": a.run}
with open(a.results, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print("  %s %s -> %s" % (row["case_id"], row["arm"], row["diagnosis"]))
```

- [ ] **Step 3: Write `README.md`**

```markdown
# measure/ — the Sherlock measurement rig

Answers, for every missed defect: **was the evidence never opened (coverage), or
opened and not connected (reasoning)?** Deterministically, with no LLM call.

    # build per-defect slices (key and corpus live OUTSIDE this repo)
    SHERLOCK_ANSWER_KEY=~/hack/case06-measure/hetero-answer-key/answer-key.json \
    SHERLOCK_CORPUS=~/hack/case06-measure/hetero-corpus \
    python3 slice.py --out cases

    # tier 1 — iterate on one defect
    with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ./gate.sh 1 v6 D11
    # tier 2 — MANDATORY before accepting anything
    with-secret.sh eval_linkapi_key --env SHERLOCK_API_KEY -- ./gate.sh 2 v6

## What a green slice does NOT prove

A slice is a smaller, quieter haystack than the 649 MB corpus. **Slice-green does
not imply corpus-green**, and a tier-1 pass proves only that the fix does something
— not that it broke nothing else. Only a tier-3 full-corpus number may be quoted as
a benchmark result.

## Judge

`gpt-5.5` via the cliproxyapi broker, secret `eval_broker_api_key` (subscription).
Neutral to both the model under test and the skill's author, and it reproduces the
historical `eval/scores.jsonl` column. Do not switch judges casually: the same v5
report scored 3/11 and 5/11 under two different judges.
```

- [ ] **Step 4: Verify the rig end-to-end with the stub (no network)**

Run:
```bash
cd cases/06-dev-logging/sherlock/measure && chmod +x gate.sh && tests/run.sh
```
Expected: `✓ measure: all suites green` — 4 suites, ~27 tests.

- [ ] **Step 5: Commit**

```bash
git add cases/06-dev-logging/sherlock/measure/gate.sh \
        cases/06-dev-logging/sherlock/measure/report-case.py \
        cases/06-dev-logging/sherlock/measure/README.md
git commit -m "case06 measure: three-tier gate + README stating what a slice does not prove"
```

---

### Task 7: Produce the v6 miss table

**Files:**
- Create: `cases/06-dev-logging/docs/2026-07-30-v6-miss-diagnosis.md`

**Interfaces:**
- Consumes: everything above.

> **Blocked on the provider.** `[SP]deepseek-v4-flash` returned
> `400 Upstream request failed` on 16 consecutive attempts over ~2.5 h on
> 2026-07-30, and no prior run captured a `stream.jsonl` (they were all discarded).
> Tasks 1–6 are fully implementable and testable offline; this task needs the
> provider back. Do not fabricate the table from the existing reports — the whole
> point is the trajectory data we do not yet have.

- [ ] **Step 1: Build the slices**

Run:
```bash
cd cases/06-dev-logging/sherlock/measure
SHERLOCK_ANSWER_KEY=$HOME/hack/case06-measure/hetero-answer-key/answer-key.json \
SHERLOCK_CORPUS=$HOME/hack/case06-measure/hetero-corpus \
python3 slice.py --out cases
```
Expected: 11 lines, D01…D11.

- [ ] **Step 2: Run tier 2 for v6 (all 11 slices)**

Run:
```bash
cd cases/06-dev-logging/sherlock/measure
/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh \
  eval_linkapi_key --env SHERLOCK_API_KEY -- ./gate.sh 2 v6
```
Expected: 11 rows appended to `results.jsonl`. If the provider is still returning
400, the runner exits non-zero per case and records nothing — retry later rather
than recording anything.

- [ ] **Step 3: Write the table**

Run:
```bash
cd cases/06-dev-logging/sherlock/measure
python3 - <<'PY' > ../../docs/2026-07-30-v6-miss-diagnosis.md
import json, collections
rows = [json.loads(l) for l in open("results.jsonl") if l.strip()]
rows = [r for r in rows if r["arm"] == "v6"]
print("# v6 miss diagnosis\n")
print("| defect | judge | diagnosis | requires | files opened | proofs reached | tool calls |")
print("|---|---|---|---|---|---|---|")
for r in sorted(rows, key=lambda x: x["case_id"]):
    print("| %s | %s | **%s** | %s | %d | %d | %d |" % (
        r["case_id"], "found" if r["judge_found"] else "missed", r["diagnosis"],
        r["requires"], r["files_opened"], r["proofs_reached"], r["tool_calls"]))
c = collections.Counter(r["diagnosis"] for r in rows if not r["judge_found"])
print("\n## Misses by cause\n")
for k, v in c.most_common():
    print("- **%s**: %d" % (k, v))
PY
```

- [ ] **Step 4: Commit**

```bash
git add cases/06-dev-logging/docs/2026-07-30-v6-miss-diagnosis.md \
        cases/06-dev-logging/sherlock/measure/results.jsonl
git commit -m "case06 measure: v6 miss diagnosis — coverage vs reasoning per defect"
```

---

## Self-Review

**Spec coverage.** Layer 1 cases → Task 1 (defect slices). Capability micro-corpora
(`cases/cap-*`) are **deliberately not implemented** — the spec's open question
resolves them as deferred until the real failure distribution says which are worth
writing; `slice.py`'s `kind` field already distinguishes them. Layer 2 capture →
Task 4. Layer 3 deterministic → Tasks 2–3; judge → Task 5; three-tier gate →
Task 6; the analysis *skill* is **not** in increment 1 (it consumes `results.jsonl`,
which does not exist until Task 7 runs) and is the first task of increment 2.

**Placeholders.** None: every step carries runnable code or an exact command with
its expected output.

**Type consistency.** `build_case` returns the dict written to `case.json`;
`case.json` keys are consumed unchanged by `run-case.sh` (`case_id`),
`score_case.py` (`case_id`, `title`, `root_cause`) and `measure.verdict`
(`case_id`, `requires`, `proof_locations`). `verdict()` consumes
`judge_found: bool`, which is exactly `score()["found"]`. `read_events` →
`proof_reach` → `verdict` share the event dict shape defined in Task 2.

**Known gap, stated rather than hidden.** `measure.py` resolves line ranges for
`read_file`-style calls; shell reads (`sed -n`, `grep -n`) resolve to a file but not
a range, and are reported `unknown` → `inconclusive`, never as a coverage failure.
If a large share of real runs land in `inconclusive`, increment 2 should parse
`sed -n 'N,Mp'` and `head`/`tail -n` arguments — do not paper over it by defaulting
`unknown` to `not_reached`.
