#!/usr/bin/env python3
"""The paid runner can resume saved v29 work in one fresh v30 session."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
RUNNER = SHERLOCK / "eval" / "bench" / "run-bench.sh"


class BenchV30Resume(unittest.TestCase):
    def test_seeded_work_uses_safe_paths_long_timeout_and_no_resume(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            corpus = tmp / "corpus"
            seed = tmp / "seed"
            runs = tmp / "runs"
            corpus.mkdir()
            seed.mkdir()
            (corpus / "Microsoft-Windows-PowerShell%4Operational.jsonl").write_text(
                '{"event":"bad"}\n', encoding="utf-8")
            (seed / "worklist.tsv").write_text(
                "# id\tverdict\nA001\tD problem\n", encoding="utf-8")
            prompt = tmp / "prompt.txt"
            prompt.write_text("Investigate {CORPUS}.", encoding="utf-8")
            fake = tmp / "fake-qwen"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib\n"
                "root = pathlib.Path.cwd()\n"
                "settings = json.loads((root / '.qwen/settings.json').read_text())\n"
                "cfg = settings['model']['generationConfig']\n"
                "assert cfg['timeout'] == 900000\n"
                "assert cfg['maxRetries'] == 0\n"
                "assert (root / 'corpus/rendered/Microsoft-Windows-PowerShell-4Operational.jsonl').is_file()\n"
                "assert (root / 'work/checkpoint.json').is_file()\n"
                "(root / 'work/report.md').write_text('# Final report\\n\\nPROVEN: rendered/Microsoft-Windows-PowerShell-4Operational.jsonl:1\\n')\n"
                "print(json.dumps([{'type':'result','session_id':'33333333-3333-3333-3333-333333333333','is_error':False,'num_turns':1,'result':'done'}]))\n",
                encoding="utf-8")
            fake.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "SHERLOCK_API_KEY": "dummy",
                "SHERLOCK_CORPUS": str(corpus),
                "SHERLOCK_SEED_WORK": str(seed),
                "SHERLOCK_PROMPT_FILE": str(prompt),
                "SHERLOCK_DATASET": "fixture",
                "SHERLOCK_UPSTREAM_LOG": "0",
                "QWEN_BIN": str(fake),
                "BENCH_RUNS": str(runs),
            })
            run = subprocess.run(["bash", str(RUNNER), "v30"], env=env,
                                 text=True, capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            trace = next(runs.iterdir())
            settings = json.loads((trace / "qwen-settings-pre.json").read_text())
            self.assertEqual(settings["model"]["generationConfig"]["timeout"], 900000)
            self.assertEqual(settings["model"]["generationConfig"]["maxRetries"], 0)
            attempts = (trace / "attempts.jsonl").read_text().splitlines()
            self.assertEqual(len(attempts), 1)
            self.assertTrue((trace / "work" / "checkpoint.json").is_file())
            self.assertIn("Final report", (trace / "work" / "report.md").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
