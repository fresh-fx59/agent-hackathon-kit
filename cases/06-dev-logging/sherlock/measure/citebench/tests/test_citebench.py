#!/usr/bin/env python3
"""Tests for the citation micro-benchmark harness. Offline, no model."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
SHERLOCK = BENCH.parents[1]
CORPUS = SHERLOCK / "tools/tests/fixtures/v60-smalltest/corpus"
BASE = HERE / "base-run-spec-v60.json"
sys.path.insert(0, str(BENCH))
import citebench  # noqa: E402

AGG = None


def agg_line():
    global AGG
    if AGG is None:
        r = subprocess.run([sys.executable, str(SHERLOCK / "skills/v61/tools/cite.py"), "--corpus", str(CORPUS),
                            "--file", "Security.jsonl", "--aggregate",
                            "count(Event.EventData.SubStatus=0xc000006a)"], capture_output=True, text=True)
        AGG = next(l for l in r.stdout.splitlines() if l.strip().startswith("агрегат:")).strip()
    return AGG


def good_report():
    return ("## Находки\n\n### Н-1 · перебор паролей\n\n"
            "- [!PROVEN] первая попытка под ADMINI — Security.jsonl:1#TargetUserName\n"
            "- [!PROVEN] имя Test с рабочей станции Rdesktop — Security.jsonl:3#WorkstationName\n"
            "- [!PROVEN] попытка под ADMIN — Security.jsonl:4#TargetUserName\n"
            "- [!PROVEN] попытка под ADMINISTRATOR — Security.jsonl:5#TargetUserName\n"
            "- [!PROVEN] попытка под АДМИНИСТРАТОР — Security.jsonl:6#TargetUserName\n"
            "- [!PROVEN] шесть попыток с SubStatus=0xc000006a (неверный пароль) — Security.jsonl:6#TargetUserName\n"
            "> " + agg_line() + "\n")


def run_micro(pkg, text, shown=True):
    root = Path(tempfile.mkdtemp(prefix="cb-"))
    work = root / "work"
    work.mkdir()
    (work / "report.md").write_text(text, encoding="utf-8")
    if shown:
        (work / "cite-shown.tsv").write_text("".join("Security.jsonl\t%d\tx\n" % n for n in range(1, 21)),
                                            encoding="utf-8")
    r = subprocess.run([sys.executable, str(BENCH / "microcheck.py"), "--pkg", str(SHERLOCK / "skills" / pkg),
                        "--work", str(work), "--corpus", str(CORPUS)], capture_output=True, text=True)
    rows = [json.loads(l) for l in (work / "bench-rounds.jsonl").read_text().splitlines()]
    return r, rows, work


class Microcheck(unittest.TestCase):
    def test_good_report_is_clean_on_both_packages(self):
        for pkg in ("v60", "v61"):
            r, rows, _ = run_micro(pkg, good_report())
            self.assertEqual(r.returncode, 0, pkg + r.stdout + r.stderr)
            self.assertEqual(rows[-1]["blocking"], 0)
            self.assertGreaterEqual(rows[-1]["refs"], 6)
            self.assertEqual(rows[-1]["typed_quotes"], 0)

    def test_bad_report_blocks_and_logs_rounds(self):
        bad = ("## Находки\n\n### Н-1 · x\n\n"
               "- первая попытка — Security.jsonl:1 «ADMINI»\n"
               "- [!PROVEN] попытка — Security.jsonl:4#TargetUserName\n")
        r, rows, work = run_micro("v61", bad)
        self.assertEqual(r.returncode, 2)
        kinds = rows[-1]["kinds"]
        self.assertIn("too-few-claims", kinds)
        self.assertIn("assertion_unlabelled", kinds)
        self.assertEqual(rows[-1]["typed_quotes"], 1)
        subprocess.run([sys.executable, str(BENCH / "microcheck.py"), "--pkg", str(SHERLOCK / "skills/v61"),
                        "--work", str(work), "--corpus", str(CORPUS)], capture_output=True)
        self.assertEqual(len((work / "bench-rounds.jsonl").read_text().splitlines()), 2)

    def test_unshown_lines_block(self):
        r, rows, _ = run_micro("v61", good_report(), shown=False)
        self.assertEqual(r.returncode, 2)
        self.assertTrue(any(k.startswith(("render:refused", "cite:not-shown")) for k in rows[-1]["kinds"]))

    def test_version_help_is_used_v61_census(self):
        text = good_report() + "\nВсе попытки — EventID 4625.\n"
        text = text.replace("### Н-1 · перебор паролей\n\n", "### Н-1 · перебор паролей\n\nНи одна попытка не удалась (EventID 4624 нет).\n\n")
        r61, _, _ = run_micro("v61", text)
        r60, _, _ = run_micro("v60", text)
        self.assertIn("distinct(Event.System.EventID) = 1", r61.stdout)
        self.assertNotIn("distinct(Event.System.EventID)", r60.stdout)


class TypedQuoteMetric(unittest.TestCase):
    def test_rendered_quotes_are_not_typed(self):
        import microcheck
        text = ('- a — Security.jsonl:1#Status «"EventRecordID":1» «"Status":"0x"»\n'
                '- b — Security.jsonl:2 «typed by hand»\n- c «also typed»\n')
        self.assertEqual(microcheck.typed_quotes(text), 2)


class Staging(unittest.TestCase):
    def test_micro_spec_caps_and_paths(self):
        base = citebench.load_base_spec(BASE)
        spec = citebench.build_spec(base, mode="micro", pkg="v61", run_id="x", stage_dir="/s")
        self.assertEqual(spec["limits"]["max_provider_calls"], 30)
        self.assertEqual([p["id"] for p in spec["phases"]], ["micro"])
        self.assertEqual(spec["phases"][0]["limits"]["max_wall_time_s"], 300)
        self.assertNotIn("hooks", spec["qwen"]["settings"])
        self.assertNotIn("v60", json.dumps(spec))
        self.assertEqual(spec["model"]["route"], "neuraldeep-dsv4flash")
        self.assertEqual(spec["task"]["workdir_src"], "/s/wd")

    def test_replay_spec_is_draft_only(self):
        base = citebench.load_base_spec(BASE)
        spec = citebench.build_spec(base, mode="replay", pkg="v61", run_id="x", stage_dir="/s")
        self.assertEqual([p["id"] for p in spec["phases"]], ["draft"])
        self.assertIn("/work/skills/v61/tools/stopcheck.py", json.dumps(spec))

    def test_stage_micro_tree(self):
        out = Path(tempfile.mkdtemp(prefix="cbs-")) / "s"
        citebench.main(["stage", "--mode", "micro", "--pkg", "v61", "--run-id", "r", "--out", str(out),
                        "--base-spec", str(BASE)])
        for p in ("wd/corpus/Security.jsonl", "wd/bench/microcheck.py", "wd/skills/v61/tools/check.py",
                  "trusted-v61/tools/cite.py", "run-spec.json", "launch.sh"):
            self.assertTrue((out / p).exists(), p)
        self.assertTrue((out / "wd/skills-root/sherlock/tools/cite.py").exists())

    def test_seed_work_rolls_back_to_handoff(self):
        src = Path(tempfile.mkdtemp(prefix="seed-")) / "work"
        inner = src / "work"
        (inner / "validation" / "a").mkdir(parents=True)
        (inner / "report.md").write_text("draft")
        (inner / "cite-shown.tsv").write_text("x\t1\n")
        (inner / "worklist.tsv").write_text("# id\n")
        (inner / "checkpoint.jsonl").write_text('{"skill_root": "/work/skills/v60"}\n{"row": 2}\n')
        dst = src.parent / "dst"
        citebench.seed_work(src, dst, "v60", "v61")
        self.assertFalse((dst / "work/validation").exists())
        self.assertFalse((dst / "work/report.md").exists())
        self.assertFalse((dst / "work/cite-shown.tsv").exists())
        self.assertTrue((dst / "work/worklist.tsv").exists())
        self.assertEqual((dst / "work/checkpoint.jsonl").read_text(), '{"skill_root": "/work/skills/v61"}\n')


class CollectAndSummarize(unittest.TestCase):
    def fake_run(self, rounds, rub, terminal="completed"):
        run = Path(tempfile.mkdtemp(prefix="run-"))
        (run / "work/work").mkdir(parents=True)
        (run / "run-meta.json").write_text(json.dumps({
            "run_id": "r", "terminal": terminal, "spend": {"cost_rub": rub, "tokens_total": 1000},
            "phases": [{"id": "micro", "calls": 7, "duration_s": 120.5,
                        "tokens": {"prompt": 900, "completion": 100, "cached": 500}}]}))
        (run / "work/work/bench-rounds.jsonl").write_text(
            "".join(json.dumps({"round": i + 1, "blocking": b, "kinds": {}, "refs": 6, "typed_quotes": 0}) + "\n"
                    for i, b in enumerate(rounds)))
        return run

    def test_row_fields(self):
        row = citebench.collect_row(self.fake_run([4, 1, 0], 2.5), "v61", "micro", 1)
        self.assertTrue(row["pass"])
        self.assertEqual(row["rounds_to_clean"], 3)
        self.assertEqual(row["defects_per_round"], [4, 1, 0])
        self.assertEqual((row["calls"], row["rub"], row["seconds"], row["refs"]), (7, 2.5, 120.5, 6))

    def test_not_clean_is_fail(self):
        row = citebench.collect_row(self.fake_run([4, 2], 3.0, "wall_budget"), "v60", "micro", 1)
        self.assertFalse(row["pass"])
        self.assertIsNone(row["rounds_to_clean"])

    def test_summary_mean_and_spread(self):
        rows = [citebench.collect_row(self.fake_run(r, rub), "v61", "micro", i)
                for i, (r, rub) in enumerate([([2, 0], 1.0), ([3, 1, 0], 2.0), ([1, 0], 3.0)])]
        s = citebench.summarize(rows)["v61"]
        self.assertEqual(s["pass"], 3)
        self.assertEqual(s["rub"]["mean"], 2.0)
        self.assertEqual(s["rub"]["sd"], 1.0)
        self.assertEqual(s["first_round_defects"], {"mean": 2.0, "min": 1, "max": 3})


if __name__ == "__main__":
    unittest.main(verbosity=1)
