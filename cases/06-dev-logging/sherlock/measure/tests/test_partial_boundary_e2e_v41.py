#!/usr/bin/env python3
"""THE INTEGRATION TEST THE TWO HALVES OF FIX 6 DID NOT HAVE.

Caught in review, before a paid launch, and it is a real defect: `handoff --partial`
deliberately does NOT advance the stage, while the driver reacted ONLY to a stage
advance. So after the first batch boundary the driver would have waited, nudged the
model to carry on in the SAME session — the exact opposite of what a batch boundary
is for — and finally exited STAGE_STALLED without ever typing `/clear`.

Both halves had tests. Neither test ran them together, which is precisely how an
integration failure hides.

The fix is a MONOTONIC boundary counter in the checkpoint: `boundary_seq` rises on
every handoff, partial or full, so «a boundary happened» stops being inferred from
«the stage changed» — two different facts that only looked like one while every
boundary happened to end a stage.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
MEASURE = os.path.join(SHERLOCK, "measure")
SKILLS = os.path.join(SHERLOCK, "skills")
DRIVER = os.path.join(MEASURE, "interactive-drive.py")
CHECKPOINT = os.path.join(SKILLS, "v41", "tools", "checkpoint.py")
FAKE = os.path.join(HERE, "fixtures", "fake_qwen.py")

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\t%s\n"


def make_work(rows=6):
    d = tempfile.mkdtemp(prefix="e2e-")
    w = os.path.join(d, "work")
    os.makedirs(w)
    body = HEADER + "".join(ROW % (i, u"?") for i in range(1, rows + 1))
    open(os.path.join(w, "worklist.tsv"), "w", encoding="utf-8").write(body)
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", w],
                   capture_output=True)
    return d, w


class BoundarySignal(unittest.TestCase):
    """The checkpoint must make «a boundary happened» observable on its own."""

    def test_every_handoff_raises_a_monotonic_counter(self):
        d, w = make_work(rows=6)
        seq0 = json.load(open(os.path.join(w, "checkpoint.json")))\
            .get("boundary_seq", 0)
        self.assertEqual(seq0, 0, "a fresh checkpoint has taken no boundary")
        # two partial boundaries in the SAME stage
        for expected in (1, 2):
            p = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                                w, "--done", "triage", "--partial"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            row = json.load(open(os.path.join(w, "checkpoint.json")))
            self.assertEqual(row["stage"], "triage", "partial must not advance")
            self.assertEqual(row.get("boundary_seq"), expected,
                             "the counter must rise on a PARTIAL boundary too")
        # closing the stage raises it again
        body = HEADER + "".join(ROW % (i, u"N a.log:1 «q» n=1 фон")
                                for i in range(1, 7))
        open(os.path.join(w, "worklist.tsv"), "w", encoding="utf-8").write(body)
        p = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", w,
                            "--done", "triage"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = json.load(open(os.path.join(w, "checkpoint.json")))
        self.assertEqual(row["stage"], "draft")
        self.assertEqual(row.get("boundary_seq"), 3)

    def test_the_counter_never_goes_backwards_on_a_refresh(self):
        """`init` runs inside every stage and `triagecheck --refresh-checkpoint`
        rewrites this file too. Either dropping the counter would make the driver
        miss a boundary — or, worse, see one that never happened."""
        d, w = make_work(rows=4)
        subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", w,
                        "--done", "triage", "--partial"], capture_output=True)
        before = json.load(open(os.path.join(w, "checkpoint.json")))["boundary_seq"]
        subprocess.run([sys.executable, CHECKPOINT, "init", "--work", w],
                       capture_output=True)
        after = json.load(open(os.path.join(w, "checkpoint.json")))["boundary_seq"]
        self.assertEqual(after, before, "init must not reset the counter")
        tc = os.path.join(SKILLS, "v41", "tools", "triagecheck.py")
        subprocess.run([sys.executable, tc, "--worklist",
                        os.path.join(w, "worklist.tsv"), "--refresh-checkpoint"],
                       capture_output=True)
        after2 = json.load(open(os.path.join(w, "checkpoint.json")))\
            .get("boundary_seq")
        self.assertEqual(after2, before,
                         "the other writer must not drop the counter either")


class DriverReactsToEveryBoundary(unittest.TestCase):
    """End to end, through a real pty, against the stand-in."""

    def drive(self, mode, budget=30, extra=()):
        d, w = make_work(rows=6)
        env = dict(os.environ, FAKE_WORK=w, FAKE_CHECKPOINT=CHECKPOINT,
                   FAKE_MODE=mode, PYTHONUNBUFFERED="1")
        transcript = os.path.join(d, "t.log")
        events = os.path.join(d, "e.jsonl")
        p = subprocess.run(
            [sys.executable, DRIVER, "--work", w, "--cwd", d,
             "--prompt", "РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
             "--events", events, "--stage-budget-s", str(budget),
             "--settle-s", "0.8"] + list(extra)
            + ["--", sys.executable, "-u", FAKE],
            capture_output=True, text=True, env=env, timeout=budget * 6)
        rows = [json.loads(l) for l in open(events, encoding="utf-8")] \
            if os.path.exists(events) else []
        text = open(transcript, "rb").read().decode("utf-8", "replace")
        return p, w, rows, text

    def test_a_partial_boundary_makes_the_driver_clear_and_reseed(self):
        p, w, rows, text = self.drive("partial_then_finish")
        kinds = [r["event"] for r in rows]
        self.assertNotIn("STAGE_STALLED", kinds,
                         "a batch boundary is not a stall: %s" % kinds)
        self.assertNotIn("STAGE_TIMEOUT", kinds, kinds)
        self.assertIn("batch_boundary", kinds,
                      "the driver must NAME a partial boundary: %s" % kinds)
        self.assertEqual(p.returncode, 0,
                         "rc=%d kinds=%s" % (p.returncode, kinds))
        self.assertEqual(
            json.load(open(os.path.join(w, "checkpoint.json")))["stage"], "done")
        # It must have cleared and re-invoked the skill at the BATCH boundary too,
        # not only at the stage boundaries.
        self.assertGreaterEqual(text.count("Base directory for this skill"), 4,
                                "one re-invocation per boundary, batch included")

    def test_nudges_are_spaced_and_never_fire_in_pairs(self):
        """A NUDGE MUST BE GIVEN THE SAME SILENCE THE TARGET WAS.

        Measured on the paid run 20260827T150830Z-v41 and on the free rehearsal
        before it: the nudges arrived in PAIRS five seconds apart — 15:17:14 and
        15:17:19, then 14:10:38 and 14:10:44 — because typing a nudge does not
        touch the upstream ledger. The next pass through the loop therefore read
        the SAME quiet time and spent a second nudge on it, so `--max-nudges 3`
        bought only two real attempts and the escalation to STAGE_STALLED came
        one full interval early.

        The ledger is still the only honest idle signal; the fix is that the
        nudge now carries its own clock.
        """
        # The interval must be well ABOVE the driver's own loop cadence
        # (pump 3 s + settle 2 s ≈ 5 s), or the loop's own pace hides the bug:
        # the measured pairs were exactly one cadence apart, 5 s, against a
        # 300 s interval.
        idle = 20
        # The ledger's mtime IS the idle signal, so the test needs one that is
        # already quiet — a stand-in for a proxy that has stopped being written.
        fd, ledger = tempfile.mkstemp(suffix=".upstream.jsonl")
        os.close(fd)
        p, w, rows, text = self.drive(
            "stall", budget=120,
            extra=("--idle-nudge-s", str(idle), "--max-nudges", "3",
                   "--ledger", ledger))
        stamps = [r["at"] for r in rows if r["event"] == "nudged"]
        self.assertTrue(stamps, "the nudge never fired: %s"
                        % [r["event"] for r in rows])

        def secs(t):
            return time.mktime(time.strptime(t, "%Y-%m-%dT%H:%M:%SZ"))

        gaps = [secs(b) - secs(a) for a, b in zip(stamps, stamps[1:])]
        self.assertFalse([g for g in gaps if g < idle],
                         "nudges fired closer together than the idle interval "
                         "(%s s): gaps %s" % (idle, gaps))
        self.assertIn("STAGE_STALLED", [r["event"] for r in rows],
                      "a dead target must still end the stage")

    def test_a_real_stall_is_still_reported(self):
        """The nudge must not become a way to hide a dead session."""
        p, w, rows, text = self.drive("stall", budget=12)
        kinds = [r["event"] for r in rows]
        self.assertTrue("STAGE_STALLED" in kinds or "STAGE_TIMEOUT" in kinds,
                        kinds)
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
