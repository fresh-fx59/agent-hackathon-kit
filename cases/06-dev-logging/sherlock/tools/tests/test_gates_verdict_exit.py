#!/usr/bin/env python3
"""Defect 6 of the 2026-08-25 v36 audit: the anti-false-green guard never fires.

MEASURED on sherlock-winevtx-runs-v36-full-r1. gates.json records

    "citecheck":   {"argv": [... "--json"], "blocking": null, "exit_code": 1}
    "statecheck":  {"blocking": null, "exit_code": 0}
    "triagecheck": {"blocking": null, "exit_code": 0}

`blocking` is null on ALL THREE even though --json was passed. run-bench.sh
scans stdout line by line for one that starts with "{" and json.loads()es THAT
LINE, but citecheck prints json.dumps(..., indent=1) — so the first line is a
bare "{" and every parse raises ValueError.

The consequence is the exact thing arm v36 was built to fix. run-bench.sh's own
comment says:

    # A gate is clean only if BOTH signals say so. An exit 0 with blocking>0
    # is the citecheck --ledger bug; a non-zero exit with no json is a crash.

but `(blocking or 0) > 0` can never be true, so the verdict comes from the exit
code alone — precisely the signal that comment calls a liar. In this run
citecheck exited 1 so the verdict happened to be right; a gate that exits 0 while
reporting blocking defects would have been recorded verdict=clean.

Second half: the process EXITED 0 while gates.json said "blocking". gates.json is
computed in the same process, a few hundred lines above, and never consulted. Any
automation reading $? sees success on a refused report.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.normpath(os.path.join(HERE, "..", "..", "eval", "bench", "run-bench.sh"))


def extract_parser():
    """Pull the gates.json python block out of run-bench.sh and exercise it.

    The block is embedded in a heredoc, so this test drives the REAL text rather
    than a copy that can drift away from it.
    """
    src = open(BENCH, encoding="utf-8").read()
    start = src.index('        payload = None')
    end = src.index('row["blocking"] = blocking', start) + len('row["blocking"] = blocking')
    return textwrap.dedent(src[start:end])


def extract_verdict_decision():
    """Exercise the real line that reduces exit code plus machine count."""
    src = open(BENCH, encoding="utf-8").read()
    start = src.index("        # A gate is clean only if BOTH signals say so.")
    start = src.index("        if ", start)
    end = src.index("\n", start)
    return textwrap.dedent(src[start:end])


class TestPrettyJsonIsParsed(unittest.TestCase):
    def run_parser(self, stdout):
        ns = {"json": json, "done": type("D", (), {"stdout": stdout})(), "row": {}}
        exec(extract_parser(), ns)
        return ns["row"]

    def test_one_line_json_still_parses(self):
        row = self.run_parser(json.dumps({"blocking": 7}))
        self.assertEqual(row["blocking"], 7)

    def test_pretty_json_parses(self):
        """indent=1 is what citecheck actually prints. This is the defect."""
        row = self.run_parser(json.dumps({"blocking": 7}, indent=1))
        self.assertEqual(row["blocking"], 7,
                         "pretty-printed gate JSON must be parsed; a null "
                         "blocking makes the whole both-signals check dead code")

    def test_pretty_json_with_leading_noise_parses(self):
        """Gates print a human render before the JSON on some paths."""
        row = self.run_parser("итого: 3 ссылок\n"
                              + json.dumps({"blocking": 2}, indent=1))
        self.assertEqual(row["blocking"], 2)

    def test_no_json_at_all_stays_none(self):
        row = self.run_parser("совсем не json\n")
        self.assertIsNone(row["blocking"])

    def test_missing_machine_count_fails_closed(self):
        """Exit zero cannot replace the second signal required by the contract."""
        ns = {
            "blocking": None,
            "done": type("D", (), {"returncode": 0})(),
            "out": {"verdict": "clean"},
        }
        exec(extract_verdict_decision(), ns)
        self.assertEqual(ns["out"]["verdict"], "blocking")


class TestExitCodeReflectsTheVerdict(unittest.TestCase):
    """Executes run-bench.sh's real RC block against fixtures on disk.

    A textual assertion would pass on a line that is present but unreachable —
    which is exactly the shape of the defect being fixed. So this runs it.
    """

    def rc_block(self):
        src = open(BENCH, encoding="utf-8").read()
        start = src.index("GATE_VERDICT=\"\"")
        end = src.index("fi", src.index("  RC=0")) + 2
        return src[start:end]

    def decide(self, verdict, report="a report", candidate=True, qwen_rc=0):
        trace = tempfile.mkdtemp()
        if candidate:
            with open(os.path.join(trace, "candidate.json"), "w") as fh:
                fh.write("{}")
        os.makedirs(os.path.join(trace, "work"), exist_ok=True)
        with open(os.path.join(trace, "work", "report.md"), "w") as fh:
            fh.write(report)
        if verdict is not None:
            with open(os.path.join(trace, "gates.json"), "w") as fh:
                json.dump({"verdict": verdict, "gates": {}}, fh)
        script = 'TRACE=%s\nQWEN_RC=%d\n%s\necho "RC=$RC"\n' % (
            trace, qwen_rc, self.rc_block())
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        line = [l for l in done.stdout.splitlines() if l.startswith("RC=")]
        self.assertTrue(line, done.stdout + done.stderr)
        return int(line[-1].split("=")[1])

    def test_clean_is_zero(self):
        self.assertEqual(self.decide("clean"), 0)

    def test_blocking_is_not_zero(self):
        """The v36 run exited 0 with gates.json verdict=blocking."""
        self.assertNotEqual(self.decide("blocking"), 0)

    def test_blocking_has_its_own_code(self):
        """'delivered but refused' must not be confused with 2 or 3."""
        self.assertEqual(self.decide("blocking"), 4)

    def test_no_gates_file_is_still_zero(self):
        """Older traces have no gates.json; absence must not invent a failure."""
        self.assertEqual(self.decide(None), 0)

    def test_transport_failure_still_wins(self):
        self.assertEqual(self.decide("clean", candidate=False), 2)

    def test_no_report_still_wins_over_verdict(self):
        self.assertEqual(self.decide("blocking", report=""), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
