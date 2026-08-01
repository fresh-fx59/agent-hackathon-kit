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
import shutil
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
    """The CRITICAL-1 layout: case.json at the case root, logs under corpus/."""
    case_dir = os.path.join(d, "cases", "D01")
    os.makedirs(case_dir)
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "D01", "kind": "defect_slice", "files": ["apps/api.log"],
                   "root_cause": "x", "requires": "single-format read",
                   "proof_locations": []}, fh)
    os.makedirs(os.path.join(case_dir, "corpus", "apps"))
    with open(os.path.join(case_dir, "corpus", "apps", "api.log"), "w", encoding="utf-8") as fh:
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

    def refuse(self, d, stub_body):
        """Run with a stub qwen whose output/exit should be REFUSED. Returns
        (completed_process, run_dir). A real skill must exist so the run actually
        reaches the qwen invocation — without it the earlier "no such skill" guard
        fires first and the test passes for the wrong reason."""
        case_dir = make_case(d)
        binp = make_stub(d)
        bad = os.path.join(binp, "qwen")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write(stub_body)
        os.chmod(bad, 0o755)
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
        return p, os.path.join(runs, os.listdir(runs)[0])

    def assert_refused(self, p, rd, why):
        self.assertNotEqual(p.returncode, 0, why + " — stdout=%r" % p.stdout)
        self.assertTrue(os.path.exists(os.path.join(rd, "stream.jsonl")),
                        "the raw stream must still be captured for post-mortem")
        self.assertFalse(os.path.exists(os.path.join(rd, "report.md")),
                         "a refused run must never write report.md")
        self.assertFalse(os.path.exists(os.path.join(rd, "meta.json")),
                         "a refused run must never write meta.json")

    def test_a_provider_error_is_refused_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            p, rd = self.refuse(d, '#!/usr/bin/env bash\n'
                                'echo \'{"type":"result","result":"[API Error: 400 Upstream request failed]"}\'\n')
            self.assert_refused(p, rd, "a provider error must fail loudly, never record a row")

    def test_is_error_true_is_refused_even_when_the_text_looks_fine(self):
        """CRITICAL-4: run-bench.sh:61 checks final.is_error FIRST; this runner had
        only the text heuristic. A record with is_error true, >=400 chars and no
        leading "[API Error" was refused there and RECORDED here — the provider's
        own failure flag must outrank any text match."""
        with tempfile.TemporaryDirectory() as d:
            long_text = "Отчёт о расследовании. " * 40   # >400 chars, no error marker
            rec = json.dumps({"type": "result", "is_error": True, "error": "upstream reset",
                              "result": long_text, "num_turns": 3}, ensure_ascii=False)
            p, rd = self.refuse(d, "#!/usr/bin/env bash\ncat <<'JSON'\n%s\nJSON\n" % rec)
            self.assert_refused(p, rd, "is_error true must be refused")

    def test_a_nonzero_runner_exit_is_refused_even_with_a_complete_result(self):
        """CRITICAL-4: the exit code was captured into meta.json and then ignored, so
        a `timeout`-killed run (124) whose partial stream still held a result record
        was recorded as a normal measurement."""
        with tempfile.TemporaryDirectory() as d:
            rec = json.dumps({"type": "result", "result": "Полный отчёт " * 60,
                              "num_turns": 5}, ensure_ascii=False)
            p, rd = self.refuse(d, "#!/usr/bin/env bash\ncat <<'JSON'\n%s\nJSON\nexit 124\n" % rec)
            self.assert_refused(p, rd, "a non-zero (timeout) exit must be refused")

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
            resolved = os.path.join(os.path.realpath(case_dir), "corpus")
            self.assertTrue(any(resolved in a for a in argv),
                            "the resolved absolute corpus dir %r never reached the prompt "
                            "argv — a relative CASE_DIR must be canonicalized before it "
                            "is embedded" % resolved)


class TheAnswerIsNotInThePrompt(unittest.TestCase):
    """CRITICAL-1. The prompt must name the CORPUS, never a directory holding
    case.json (title, root_cause, every proof_location). In the one captured run,
    20260730T195412Z, record 12 was a read_file on case.json and record 13 returned
    the root cause — before a single log line had been opened."""

    def env_for(self, d, case_dir, binp, log, runs):
        skills = os.path.join(d, "skills", "v6")
        os.makedirs(skills, exist_ok=True)
        with open(os.path.join(skills, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: sherlock\n---\n")
        env = dict(os.environ)
        env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                    "QWEN_STUB_LOG": log, "QWEN_BIN": os.path.join(binp, "qwen"),
                    "SHERLOCK_SKILLS": os.path.dirname(skills),
                    "SHERLOCK_RUNS": runs, "SHERLOCK_API_KEY": "stub-key"})
        return env

    def test_the_prompt_names_the_corpus_subdir_not_the_case_root(self):
        with tempfile.TemporaryDirectory() as d:
            case_dir = make_case(d)
            binp = make_stub(d)
            log = os.path.join(d, "stub.log")
            env = self.env_for(d, case_dir, binp, log, os.path.join(d, "runs"))
            p = subprocess.run(["bash", RUNNER, case_dir, "v6"], capture_output=True,
                               text=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            argv = [a.decode("utf-8", "replace")
                    for a in open(log, "rb").read().split(b"\0") if a]
            prompt = next(a for a in argv if "Продакшн" in a)
            corpus = os.path.join(os.path.realpath(case_dir), "corpus")
            self.assertIn(corpus, prompt)
            # And the directory named in the prompt really does not hold the answer.
            self.assertFalse(os.path.exists(os.path.join(corpus, "case.json")))

    def test_a_case_json_inside_the_prompt_dir_is_refused_outright(self):
        """The guard against silent regression: if a future slice.py/micro.py change
        ever writes case.json back beside the logs, no run may happen at all."""
        with tempfile.TemporaryDirectory() as d:
            case_dir = make_case(d)
            binp = make_stub(d)
            log = os.path.join(d, "stub.log")
            runs = os.path.join(d, "runs")
            # simulate the regression
            shutil.copy(os.path.join(case_dir, "case.json"),
                        os.path.join(case_dir, "corpus", "case.json"))
            env = self.env_for(d, case_dir, binp, log, runs)
            p = subprocess.run(["bash", RUNNER, case_dir, "v6"], capture_output=True,
                               text=True, env=env, timeout=60)
            self.assertNotEqual(p.returncode, 0, "the run must be refused, not measured")
            self.assertIn("case.json", p.stderr)
            self.assertFalse(os.path.exists(log), "the CLI must never have been invoked")
            self.assertFalse(os.path.isdir(runs),
                             "a refused run must leave no orphan run dir")


class EveryRunNamesTheModelThatActuallyAnswered(unittest.TestCase):
    """`[SP]deepseek-v4-flash` is an alias that fans out to (at least) two
    upstreams ~19x apart on whether they emit a tool call, and qwen-code stamps
    only the REQUESTED name. Without a pass-through in front of the provider no
    recorded row can be attributed to an upstream — not later, not ever. So the
    runner points the CLI at a local logger by default."""

    ECHO_ENV = ("#!/usr/bin/env bash\n"
                'printf "%s" "$OPENAI_BASE_URL" > "$QWEN_STUB_LOG.baseurl"\n'
                "cat <<'JSON'\n"
                '{"type":"result","result":"'  + ("x" * 40) + '","num_turns":1,'
                '"usage":{"input_tokens":11,"output_tokens":22}}\n'
                "JSON\nexit 0\n")

    def go(self, d, extra_env):
        case_dir = make_case(d)
        binp = os.path.join(d, "bin")
        os.makedirs(binp, exist_ok=True)
        qwen = os.path.join(binp, "qwen")
        with open(qwen, "w", encoding="utf-8") as fh:
            fh.write(self.ECHO_ENV)
        os.chmod(qwen, os.stat(qwen).st_mode | stat.S_IEXEC)
        skills = os.path.join(d, "skills", "v6")
        os.makedirs(skills)
        with open(os.path.join(skills, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: sherlock\n---\n")
        log = os.path.join(d, "stub.log")
        env = dict(os.environ)
        env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                    "QWEN_STUB_LOG": log, "QWEN_BIN": qwen,
                    "SHERLOCK_SKILLS": os.path.dirname(skills),
                    "SHERLOCK_RUNS": os.path.join(d, "runs"),
                    "SHERLOCK_BASE_URL": "https://linkapi.ai/v1",
                    "SHERLOCK_API_KEY": "stub-key"})
        env.update(extra_env)
        subprocess.run(["bash", RUNNER, case_dir, "v6"], capture_output=True,
                       text=True, env=env, timeout=120)
        with open(log + ".baseurl", encoding="utf-8") as fh:
            return fh.read()

    def test_the_cli_is_pointed_at_the_local_logger_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            url = self.go(d, {})
            self.assertTrue(url.startswith("http://127.0.0.1:"),
                            "OPENAI_BASE_URL was %r" % url)

    def test_it_can_be_turned_off_and_then_talks_to_the_provider_directly(self):
        with tempfile.TemporaryDirectory() as d:
            url = self.go(d, {"SHERLOCK_UPSTREAM_LOG": "0"})
            self.assertEqual(url, "https://linkapi.ai/v1")


class TheArmMustNotBeACoinFlip(unittest.TestCase):
    """4 of 9 recorded v11 rows carry SKILL-NEVER-LOADED — 44 %, not the 8 % on
    record. The prompt never mentioned the skill, so the model had to discover
    `.qwen/skills/` and choose to call the `skill` tool. An arm measured on
    barely half its runs is not measured. So the runner puts the arm's own text
    in the prompt: same content the `skill` tool would have returned, delivered
    with certainty instead of by luck."""

    STUB = ("#!/usr/bin/env bash\n"
            'printf "%s\\0" "$@" >> "$QWEN_STUB_LOG"\n'
            "mkdir -p work && printf 'WORKING REPORT %s' \"$(head -c 400 /dev/zero | tr '\\0' x)\" > work/report.md\n"
            "cat <<'JSON'\n"
            '{"type":"result","result":"' + ("x" * 40) + '","num_turns":1,'
            '"usage":{"input_tokens":11,"output_tokens":22}}\n'
            "JSON\nexit 0\n")

    def go(self, d, marker="SHERLOCK-ARM-MARKER-9F3A"):
        case_dir = make_case(d)
        binp = os.path.join(d, "bin"); os.makedirs(binp, exist_ok=True)
        qwen = os.path.join(binp, "qwen")
        with open(qwen, "w", encoding="utf-8") as fh:
            fh.write(self.STUB)
        os.chmod(qwen, os.stat(qwen).st_mode | stat.S_IEXEC)
        skills = os.path.join(d, "skills", "v6"); os.makedirs(skills)
        with open(os.path.join(skills, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: sherlock\n---\n# Процедура\n%s\n" % marker)
        log = os.path.join(d, "stub.log")
        runs = os.path.join(d, "runs")
        env = dict(os.environ)
        env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                    "QWEN_STUB_LOG": log, "QWEN_BIN": qwen,
                    "SHERLOCK_SKILLS": os.path.dirname(skills),
                    "SHERLOCK_RUNS": runs,
                    "SHERLOCK_UPSTREAM_LOG": "0",
                    "SHERLOCK_API_KEY": "stub-key"})
        subprocess.run(["bash", RUNNER, case_dir, "v6"], capture_output=True,
                       text=True, env=env, timeout=120)
        with open(log, encoding="utf-8") as fh:
            argv = fh.read()
        rd = [os.path.join(runs, x) for x in os.listdir(runs)][0]
        return argv, rd

    def test_the_prompt_tells_the_model_to_use_the_skill(self):
        with tempfile.TemporaryDirectory() as d:
            argv, _rd = self.go(d)
            self.assertIn("log-rca", argv)
            self.assertIn("skill", argv.lower())

    def test_the_skill_body_is_NOT_pasted_into_the_prompt(self):
        """Injecting SKILL.md would measure a different system."""
        with tempfile.TemporaryDirectory() as d:
            argv, _rd = self.go(d)
            self.assertNotIn("SHERLOCK-ARM-MARKER-9F3A", argv)

    def test_the_none_arm_gets_no_injected_text(self):
        """The control arm must stay a control arm."""
        with tempfile.TemporaryDirectory() as d:
            case_dir = make_case(d)
            binp = os.path.join(d, "bin"); os.makedirs(binp, exist_ok=True)
            qwen = os.path.join(binp, "qwen")
            with open(qwen, "w", encoding="utf-8") as fh:
                fh.write(self.STUB)
            os.chmod(qwen, os.stat(qwen).st_mode | stat.S_IEXEC)
            log = os.path.join(d, "stub.log")
            env = dict(os.environ)
            env.update({"PATH": binp + os.pathsep + os.environ["PATH"],
                        "QWEN_STUB_LOG": log, "QWEN_BIN": qwen,
                        "SHERLOCK_SKILLS": os.path.join(d, "skills"),
                        "SHERLOCK_RUNS": os.path.join(d, "runs"),
                        "SHERLOCK_UPSTREAM_LOG": "0",
                        "SHERLOCK_API_KEY": "stub-key"})
            subprocess.run(["bash", RUNNER, case_dir, "none"], capture_output=True,
                           text=True, env=env, timeout=120)
            with open(log, encoding="utf-8") as fh:
                self.assertNotIn("log-rca", fh.read())


class TheWorkingReportIsEvidenceAndMustSurvive(unittest.TestCase):
    """D04 wrote an 18,186-char report naming the right root cause and delivered
    143 chars. The 18 KB lived in the scratch dir, which the runner rm -rf's on
    exit — so the only proof the model FOUND the defect was buried in the
    trajectory. Keep the artifact and measure it, so 'found but not delivered'
    becomes a number instead of an inference. It does NOT become the score."""

    def test_the_models_working_report_is_kept_beside_the_answer(self):
        with tempfile.TemporaryDirectory() as d:
            _argv, rd = TheArmMustNotBeACoinFlip().go(d)
            p = os.path.join(rd, "working-report.md")
            self.assertTrue(os.path.exists(p), "working-report.md not preserved")
            with open(p, encoding="utf-8") as fh:
                self.assertIn("WORKING REPORT", fh.read())

    def test_meta_records_both_sizes_so_the_gap_is_visible(self):
        with tempfile.TemporaryDirectory() as d:
            _argv, rd = TheArmMustNotBeACoinFlip().go(d)
            with open(os.path.join(rd, "meta.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
            self.assertEqual(meta["answer_chars"], 40)
            self.assertGreater(meta["artifact_chars"], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
