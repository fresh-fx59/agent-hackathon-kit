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
            # A real skill must exist so the run actually reaches the qwen invocation —
            # without it the earlier "no such skill" guard fires first and this test
            # would pass for the wrong reason, never touching the provider-error path.
            skills = os.path.join(d, "skills", "v6")
            os.makedirs(skills)
            with open(os.path.join(skills, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("---\nname: sherlock\n---\n")
            runs = os.path.join(d, "runs")
            env = dict(os.environ)
            env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                        "QWEN_STUB_LOG": os.path.join(d, "l"),
                        "QWEN_BIN": bad, "SHERLOCK_RUNS": runs,
                        "SHERLOCK_SKILLS": os.path.dirname(skills), "SHERLOCK_API_KEY": "stub-key"})
            p = subprocess.run(["bash", RUNNER, case_dir, "v6"], capture_output=True,
                               text=True, env=env, timeout=60)
            self.assertNotEqual(p.returncode, 0,
                                "a provider error must fail loudly, never record a row")
            rd = os.path.join(runs, os.listdir(runs)[0])
            self.assertTrue(os.path.exists(os.path.join(rd, "stream.jsonl")),
                            "the raw stream must still be captured for post-mortem")
            self.assertFalse(os.path.exists(os.path.join(rd, "report.md")),
                             "a refused run must never write report.md")
            self.assertFalse(os.path.exists(os.path.join(rd, "meta.json")),
                             "a refused run must never write meta.json")

    def test_relative_case_dir_yields_an_absolute_prompt_path(self):
        """qwen runs after `cd "$W"` into the scratch dir, so the prompt's embedded
        $CASE_DIR must be canonicalized to an absolute path before it is invoked — a
        relative one would describe a path relative to the scratch dir, not the
        caller's cwd, and the model would answer plausibly about nothing."""
        with tempfile.TemporaryDirectory() as d:
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
            rel_case_dir = os.path.relpath(case_dir, d)
            p = subprocess.run(["bash", RUNNER, rel_case_dir, "v6"], capture_output=True,
                               text=True, env=env, cwd=d, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            argv = [a.decode("utf-8", "replace")
                    for a in open(log, "rb").read().split(b"\0") if a]
            resolved = os.path.realpath(case_dir)
            self.assertTrue(any(resolved in a for a in argv),
                            "the resolved absolute case dir %r never reached the prompt "
                            "argv — a relative CASE_DIR must be canonicalized before it "
                            "is embedded" % resolved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
