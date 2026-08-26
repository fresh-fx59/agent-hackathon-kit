#!/usr/bin/env python3
"""fix 5c — reaching synthesis and then not writing.

MEASURED, v38 paid run 20260826T132832Z-v38: 7 `write_file` calls in 2 h 42 m
(2 of them failed), 181 `read_file`, 162 `run_shell_command`, 3 `agent` calls
consuming 65 minutes — and NOT ONE write to `report.md`. Every write went to
helper python scripts. After `checkpoint.json` reached `ready_for_synthesis` at
15:35Z the run made 124 more upstream calls, 58 of them discarded substitutions,
and died at 16:10Z holding the 192-byte stub.

Three things had to change, and all three are asserted here:

  1. SKILL.md must order the report written INCREMENTALLY from the moment
     `checkpoint.json` reads `ready_for_synthesis` — a run that dies mid-synthesis
     must leave a partial report worth reading, because on this lane runs DO die
     mid-synthesis.
  2. SKILL.md must forbid reading a gate's source. A gate message that is not
     enough is a DEFECT IN THE GATE, to be written down and moved past — not a
     puzzle to solve by grepping the grader. That is where the last 90 minutes went.
  3. The stopping condition must reject a stub. `stopcheck` now blocks delivery
     unless `report.md` holds a real `### Н-n` / `### К-n` block or says
     «Находок нет: …» outright, and blocks while the placeholder's
     «СИНТЕЗ НЕ ЗАВЕРШЁН» line still stands.

THE HOUSE DEFECT, guarded explicitly below: a blocking term that is computed and
printed but absent from the exit code and unasserted by any test. Eight PRs in a
row shipped one. So the terms live in a NAMED dict, `substance_defect_keys()`
enumerates them, and `test_every_named_counter_reaches_the_verdict` walks that
list and insists each key on its own turns Stop into `block`.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
SKILLS = SHERLOCK / "skills"
SKILL = SKILLS / "v39"
STOPCHECK = SKILL / "tools" / "stopcheck.py"

REAL_BLOCK = ("# Отчёт Sherlock\n\n## Находки\n\n### Н-1 · 3proxy установлен\n\n"
              "что сломано: служба 3proxy\n\nулики: host/app.log:1 «event»\n\n"
              "исход: успех\n")


def load_stopcheck():
    spec = importlib.util.spec_from_file_location("v39_stopcheck", str(STOPCHECK))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Workspace:
    """The same harness test_stopcheck_v28.py uses, pointed at v39."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.corpus = self.root / "corpus"
        self.work = self.root / "work"
        self.marker = self.root / ".sherlock" / "active.json"

    def __enter__(self):
        (self.corpus / "host").mkdir(parents=True)
        (self.corpus / "host" / "app.log").write_text("event\n", encoding="utf-8")
        self.work.mkdir()
        self.activate(["worklist.tsv"])
        (self.work / "worklist.tsv").write_text(
            "# id\tverdict\taxis\tref\tfrequency\trecord\n"
            "g001\tD\trare\thost/app.log:1\tn=1\tevent\n", encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.tmp.cleanup()

    def activate(self, worklists):
        self.marker.parent.mkdir(exist_ok=True)
        self.marker.write_text(json.dumps({
            # 36 on purpose: v39's validate_active_marker still pins the marker
            # schema version logmap writes. Not this fix's business to change.
            "version": 36, "active": True,
            "workspace": str(self.root.resolve()),
            "skill_root": str(SKILL.resolve()),
            "corpus": str(self.corpus.resolve()),
            "out": str(self.work.resolve()),
            "mode": "single", "worklists": worklists,
        }) + "\n", encoding="utf-8")

    def report(self, text):
        (self.work / "report.md").write_text(text, encoding="utf-8")

    def stop(self, last_message=""):
        env = os.environ.copy()
        env["QWEN_SKILL_ROOT"] = str(SKILL)
        payload = {"cwd": str(self.root), "hook_event_name": "Stop",
                   "last_assistant_message": last_message}
        run = subprocess.run([sys.executable, str(STOPCHECK)],
                             input=json.dumps(payload), text=True,
                             capture_output=True, cwd=str(self.root), env=env)
        assert run.returncode == 0, run.stderr
        assert run.stderr == "", run.stderr
        return json.loads(run.stdout)


class AStubCannotReachDelivery(unittest.TestCase):
    def test_the_192_byte_stub_is_refused(self):
        """The literal artifact the paid run died holding."""
        cp = importlib.util.spec_from_file_location(
            "v39_checkpoint", str(SKILL / "tools" / "checkpoint.py"))
        mod = importlib.util.module_from_spec(cp)
        cp.loader.exec_module(mod)
        stub = mod.render_placeholder({"state": "ready_for_synthesis",
                                       "resolved": 262, "total": 262,
                                       "unresolved": 0})
        with Workspace() as w:
            w.report(stub)
            result = w.stop(stub.strip())
        self.assertEqual("block", result["decision"])
        self.assertIn("report.md", result["reason"])

    def test_an_empty_shaped_report_is_refused(self):
        with Workspace() as w:
            w.report("# Отчёт Sherlock\n\nвсё хорошо\n")
            result = w.stop("# Отчёт Sherlock\n\nвсё хорошо")
        self.assertEqual("block", result["decision"])
        self.assertIn("Н-n", result["reason"])

    def test_the_placeholder_marker_alone_blocks(self):
        """A report with real findings that still carries the marker is refused —
        incremental synthesis means the marker outlives the first real block."""
        mod = load_stopcheck()
        text = REAL_BLOCK + "\nСИНТЕЗ НЕ ЗАВЕРШЁН — удали эту строку.\n"
        defects = mod.report_substance_defects(text)
        self.assertEqual(0, defects["no_finding_block"])
        self.assertEqual(1, defects["synthesis_incomplete_marker"])
        with Workspace() as w:
            w.report(text)
            result = w.stop(text.strip())
        self.assertEqual("block", result["decision"])

    def test_an_explicit_no_findings_statement_is_accepted(self):
        """The honest empty answer must stay reachable, or the gate teaches
        fabrication — the same rule as «не определяется» in 6b."""
        mod = load_stopcheck()
        text = "# Отчёт Sherlock\n\nНаходок нет: корпус пуст, 0 записей.\n"
        self.assertEqual(0, sum(mod.report_substance_defects(text).values()))

    def test_a_real_finding_block_passes_the_substance_check(self):
        mod = load_stopcheck()
        self.assertEqual(0, sum(mod.report_substance_defects(REAL_BLOCK).values()))
        candidate = ("# Отчёт\n\n## Отклонённые кандидаты\n\n### К-2 · ничего\n\n"
                     "исход: норма\n")
        self.assertEqual(0, sum(mod.report_substance_defects(candidate).values()))


    def test_stopcheck_accepts_every_head_citecheck_calls_a_block(self):
        """Two gates that disagree about a finding head would trap the arm in a
        report neither accepts. stopcheck's shape is deliberately the LOOSER of
        the two — it may never refuse what citecheck counts as a block."""
        import importlib.util as iu
        spec = iu.spec_from_file_location("v39_citecheck",
                                          str(SKILL / "tools" / "citecheck.py"))
        cc = iu.module_from_spec(spec)
        spec.loader.exec_module(cc)
        mod = load_stopcheck()
        heads = ("### Н-1 · x", "## Н-2 · y", "- Н-3 · z", "**Н-4 · w**",
                 "### К-1 · c", "Н-5 · v", "#### K-6 · q")
        for head in heads:
            self.assertTrue(cc.FINDING_HEAD_RE.match(head)
                            or cc.CANDIDATE_HEAD_RE.match(head),
                            "fixture is wrong, citecheck does not see %r" % head)
            text = "# Отчёт\n\n%s\n\nисход: успех\n" % head
            self.assertEqual(0, mod.report_substance_defects(text)["no_finding_block"],
                             "stopcheck refuses a block citecheck accepts: %r" % head)
        for not_a_head in ("## Находки", "просто текст", "### Н-n"):
            text = "# Отчёт\n\n%s\n" % not_a_head
            self.assertEqual(1, mod.report_substance_defects(text)["no_finding_block"],
                             not_a_head)


class NamedCountersReachTheVerdict(unittest.TestCase):
    """THE HOUSE DEFECT. A term that is printed but not summed is invisible."""

    def setUp(self):
        self.mod = load_stopcheck()

    TRIGGERS = {
        "no_finding_block": "# Отчёт Sherlock\n\nничего структурного\n",
        "synthesis_incomplete_marker":
            REAL_BLOCK + "\nСИНТЕЗ НЕ ЗАВЕРШЁН — удали эту строку.\n",
    }

    def test_the_key_list_is_the_whole_dict(self):
        self.assertEqual(tuple(sorted(self.mod.report_substance_defects("").keys())),
                         self.mod.substance_defect_keys())

    def test_every_named_counter_is_exercised_by_this_suite(self):
        self.assertEqual(sorted(self.mod.substance_defect_keys()),
                         sorted(self.TRIGGERS))

    def test_every_named_counter_reaches_the_verdict(self):
        for key, text in sorted(self.TRIGGERS.items()):
            defects = self.mod.report_substance_defects(text)
            self.assertEqual(1, defects[key], key)
            self.assertTrue(self.mod.substance_reason(defects), key)
            with Workspace() as w:
                w.report(text)
                result = w.stop(text.strip())
            self.assertEqual("block", result["decision"],
                             "%s is computed but does not block" % key)

    def test_a_clean_report_is_not_blocked_by_the_substance_check(self):
        self.assertEqual({}, {k: v for k, v in
                              self.mod.report_substance_defects(REAL_BLOCK).items()
                              if v})
        self.assertIsNone(self.mod.substance_reason(
            self.mod.report_substance_defects(REAL_BLOCK)))


class SkillTeachesIt(unittest.TestCase):
    def setUp(self):
        self.body = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    def test_incremental_writing_is_ordered_not_suggested(self):
        self.assertIn("WRITE IT INCREMENTALLY", self.body)
        self.assertIn("ready_for_synthesis", self.body)
        self.assertIn("СИНТЕЗ НЕ ЗАВЕРШЁН", self.body)

    def test_reading_the_grader_is_forbidden(self):
        self.assertIn("NEVER read or grep a gate's source code", self.body)
        self.assertIn("Чего я не знаю", self.body)

    def test_the_stopping_condition_rejects_a_stub(self):
        section = self.body.split("## 8. Stopping condition", 1)[1].split("## 9.", 1)[0]
        self.assertIn("### Н-n", section)
        self.assertIn("Находок нет:", section)
        self.assertIn("СИНТЕЗ НЕ ЗАВЕРШЁН", section)


class FrozenArmsUntouched(unittest.TestCase):
    def test_v38_stopcheck_has_no_substance_check(self):
        body = (SKILLS / "v38" / "tools" / "stopcheck.py").read_text(encoding="utf-8")
        self.assertNotIn("report_substance_defects", body)

    def test_v38_skill_does_not_teach_incremental_writing(self):
        body = (SKILLS / "v38" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("WRITE IT INCREMENTALLY", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
