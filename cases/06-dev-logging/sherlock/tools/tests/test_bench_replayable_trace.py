#!/usr/bin/env python3
"""A finished run can be re-validated with the source corpus taken away.

The defect this pins down cost a false accusation. The runner stages the corpus
before the model sees it — `stage-corpus.py` renames every path containing `%`
or whitespace under a `rendered/` prefix — and that staged tree used to die with
the temp workspace. Validating the delivered report afterwards therefore meant
pointing the gates at the ORIGINAL corpus, where none of the `rendered/…` paths
exist, which reports the model's citations as fabricated. They were not.

So the test does the only thing that actually proves the fix: it runs the real
runner, lets it delete its workspace, then RENAMES THE SOURCE CORPUS AWAY and
makes it unreadable before replaying. Anything that still works cannot be
reconstructing.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
RUNNER = SHERLOCK / "eval" / "bench" / "run-bench.sh"

# Two of these three names need renaming, so the staged tree is genuinely a
# different shape from the source — a fixture where every name is already safe
# would pass this test while proving nothing.
FIXTURE_FILES = {
    "Microsoft-Windows-PowerShell%4Operational.jsonl": '{"event":"one"}\n',
    "Internet Explorer.jsonl": '{"event":"two"}\n',
    "System.jsonl": '{"event":"three"}\n',
}


class BenchReplayableTrace(unittest.TestCase):
    def _run(self, tmp, arm="v35", retired_marker=False, controlled_settings=None):
        corpus = tmp / "corpus"
        runs = tmp / "runs"
        corpus.mkdir()
        for name, body in FIXTURE_FILES.items():
            (corpus / name).write_text(body, encoding="utf-8")
        prompt = tmp / "prompt.txt"
        prompt.write_text("Investigate {CORPUS} thoroughly.", encoding="utf-8")

        fake = tmp / "fake-qwen"
        marker_code = ""
        if retired_marker:
            marker_code = (
                "assert os.environ.get('SHERLOCK_STRICT_MARKER_LIFECYCLE') == '1'\n"
                "(root / '.sherlock').mkdir(exist_ok=True)\n"
                "marker = {'version':36,'active':True,'workspace':str(root),"
                "'skill_root':str(root / '.qwen/skills/log-rca'),'corpus':str(root / 'corpus'),"
                "'out':str(root / 'work'),'mode':'single','worklists':['worklist.tsv']}\n"
                "(root / '.sherlock/completed.json').write_text(json.dumps(marker) + '\\n')\n"
            )
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib\n"
            "root = pathlib.Path.cwd()\n"
            "(root / 'work').mkdir(exist_ok=True)\n"
            "(root / 'work/report.md').write_text('# report\\n\\nrendered/System.jsonl:1\\n')\n"
            "(root / 'work/worklist.tsv').write_text('# id\\tverdict\\nA001\\tD thing\\n')\n"
            "(root / 'work/rules.tsv').write_text('# rule\\n')\n"
            + marker_code +
            "print(json.dumps([{'type':'result','session_id':"
            "'44444444-4444-4444-4444-444444444444','is_error':False,"
            "'num_turns':2,'result':'done'}]))\n",
            encoding="utf-8")
        fake.chmod(0o755)

        env = dict(os.environ)
        env.update({
            "SHERLOCK_API_KEY": "dummy",
            "SHERLOCK_CORPUS": str(corpus),
            "SHERLOCK_PROMPT_FILE": str(prompt),
            "SHERLOCK_DATASET": "fixture",
            "SHERLOCK_UPSTREAM_LOG": "0",
            "SHERLOCK_RESUME_MAX_ATTEMPTS": "0",
            "QWEN_BIN": str(fake),
            "BENCH_RUNS": str(runs),
        })
        controlled_trace = None
        if controlled_settings is not None:
            runs.mkdir()
            trace = runs / "controlled-subscription"
            trace.mkdir()
            (trace / "run-manifest.json").write_text(json.dumps({
                "input_identity": {
                    "settings_sha256": hashlib.sha256(controlled_settings).hexdigest(),
                },
            }) + "\n", encoding="utf-8")
            settings = tmp / "approved-settings.json"
            settings.write_bytes(controlled_settings)
            env.update({
                "SHERLOCK_RUN_TAG": trace.name,
                "SHERLOCK_TRACE": str(trace),
                "SHERLOCK_LANE": "subscription",
                "SHERLOCK_SETTINGS": str(settings),
            })
            controlled_trace = trace
        publisher = None
        if controlled_trace is not None:
            def publish_controller_proof():
                ready = controlled_trace / ".runner-ready"
                for _ in range(500):
                    if ready.is_file():
                        (controlled_trace / "controller-process.json").write_text(json.dumps({
                            "pid": 1, "process_start_ticks": 1, "pgid": 1,
                            "boot_id_sha256": "0" * 64,
                            "command_sha256": "1" * 64,
                        }) + "\n", encoding="utf-8")
                        return
                    time.sleep(0.01)
            publisher = threading.Thread(target=publish_controller_proof)
            publisher.start()
        run = subprocess.run(["bash", str(RUNNER), arm], env=env,
                             text=True, capture_output=True, timeout=180)
        if publisher is not None:
            publisher.join(timeout=1)
        trace = next(runs.iterdir())
        return corpus, trace, run

    def test_controlled_subscription_seals_and_uses_the_approved_settings(self):
        with tempfile.TemporaryDirectory() as raw:
            approved = subprocess.check_output([
                "python3", str(SHERLOCK / "measure" / "corporate-settings.py"),
                "emit-run", "--window", "262000", "--max-tokens", "32000",
                "--session-token-limit", "230000", "--timeout", "900000",
                "--max-retries", "0", "--skill-directory", str(Path(raw) / "skills"),
                "--exclude-tool", "agent", "--no-auto-compact",
            ])
            _corpus, trace, run = self._run(
                Path(raw).resolve(), arm="v44", retired_marker=True,
                controlled_settings=approved)
            self.assertEqual((trace / "corporate-settings.json").read_bytes(), approved,
                             run.stdout + run.stderr)
            self.assertTrue((trace / "qwen-settings-pre.json").is_file(),
                            run.stdout + run.stderr)
            self.assertEqual((trace / "qwen-settings-pre.json").read_bytes(), approved,
                             run.stdout + run.stderr)

    def test_retired_marker_is_preserved_as_an_inert_trace_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            _corpus, trace, run = self._run(Path(raw), arm="v44", retired_marker=True)
            marker_path = trace / ".sherlock" / "active.json"
            self.assertTrue(marker_path.is_file(), run.stdout + run.stderr)
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertIs(marker["active"], False)
            self.assertIs(marker["sealed"], True)
            for field in ("workspace", "out", "corpus", "skill_root"):
                self.assertTrue(Path(marker[field]).resolve().is_relative_to(trace.resolve()),
                                (field, marker[field], trace))

    def test_trace_is_self_contained_after_the_corpus_disappears(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            corpus, trace, run = self._run(tmp)

            # WHAT WAS SENT. The prompt used to survive only as model-side text
            # inside the trajectory; a run whose input cannot be read back
            # cannot be reproduced or argued about.
            sent = (trace / "prompt-sent.txt").read_text(encoding="utf-8")
            self.assertIn("Investigate", sent)
            inputs = json.loads((trace / "run-inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(inputs["corpus_source"], str(corpus.resolve()))
            self.assertEqual(len(inputs["prompt_sha256"]), 64)
            self.assertTrue(inputs["staged_root"])

            # THE SCORED TREE, kept and digested — and it is a DIFFERENT shape
            # from the source, which is the whole reason it must be kept.
            staged = trace / "staged-corpus"
            self.assertTrue((staged / "rendered" / "Microsoft-Windows-PowerShell-4Operational.jsonl").is_file())
            self.assertTrue((staged / "rendered" / "Internet-Explorer.jsonl").is_file())
            self.assertTrue((staged / "System.jsonl").is_file())
            self.assertFalse((corpus / "rendered").exists(),
                             "the source corpus must stay flat; staging is the runner's copy")
            digest = (trace / "staged-corpus.sha256").read_text(encoding="utf-8")
            self.assertEqual(len(digest.strip().splitlines()), len(FIXTURE_FILES))

            # THE GRADER ITSELF, so a replay is not at the mercy of the repo.
            self.assertTrue((trace / "gate-tools" / "citecheck.py").is_file())

            # THE GATES' OWN VERDICTS, with argv and exit code, so "the model
            # said it was clean" and "it is clean" stop being the same artifact.
            gates = json.loads((trace / "gates.json").read_text(encoding="utf-8"))
            self.assertIn(gates["verdict"], ("clean", "blocking"))
            for name in ("citecheck", "triagecheck", "statecheck"):
                row = gates["gates"][name]
                self.assertTrue(row["argv"], name)
                self.assertIn("exit_code", row, name)
                self.assertEqual(len(row["tool_sha256"] or ""), 64, name)

            # NOW TAKE THE SOURCE CORPUS AWAY. This is the assertion that
            # matters: after this, any reconstruction path is dead.
            gone = tmp / "corpus-gone"
            corpus.rename(gone)
            gone.chmod(0o000)
            try:
                replay = subprocess.run(["bash", str(trace / "replay.sh")],
                                        text=True, capture_output=True, timeout=600)
                self.assertIn("citecheck rc=", replay.stdout, replay.stderr)
                self.assertIn("triagecheck rc=", replay.stdout, replay.stderr)
                self.assertIn("statecheck rc=", replay.stdout, replay.stderr)
                self.assertNotIn("no staged-corpus", replay.stderr)
                self.assertNotIn("no gate-tools", replay.stderr)
            finally:
                gone.chmod(0o755)

    def test_the_wrong_corpus_root_is_what_produced_the_false_verdict(self):
        # The negative case, so the test demonstrates the two trees really are
        # different and that the fix picked the right one. A citation written
        # against the staged tree must NOT resolve against the flat source.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            corpus, trace, _ = self._run(tmp)
            # A file whose name NEEDED renaming — System.jsonl is already safe
            # and stays at the root, so it would prove nothing here.
            cited = "rendered/Microsoft-Windows-PowerShell-4Operational.jsonl"
            self.assertTrue((trace / "staged-corpus" / cited).is_file())
            self.assertFalse((corpus / cited).exists())


if __name__ == "__main__":
    unittest.main()
