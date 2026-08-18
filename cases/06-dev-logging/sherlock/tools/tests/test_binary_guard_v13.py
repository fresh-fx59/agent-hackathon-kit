"""Tests for the v13 binary guard in citecheck.py and logjoin.py.

    python3 tools/tests/test_binary_guard_v13.py

WHY THIS TEST EXISTS (measured 2026-08-18)
------------------------------------------
Both readers opened every corpus file in text mode with `errors="replace"`, which
never raises.  A `.evtx` therefore decodes into mojibake that still contains long
runs of readable ASCII, so a citation *into a binary file* could be graded — and
on the real BlueSky corpus `BlueSkyRansomware.evtx:2138` with the verbatim quote
«- Code:  CORSVCC00000774» was returned as **ok** by v11.  The gate whose entire
job is to stop fabricated evidence was able to certify it.

`logjoin` had the same hole with a different consequence: it reports `absent_in`,
and absence is evidence.  A binary file scanned as text and found not to contain
an id was listed as "the id is absent here" — a false negative dressed as proof.

The binary here is SYNTHETIC (NUL bytes + an ASCII run), so the test needs no
corpus and runs anywhere.  The v11 half is a documentation assertion: if a future
refactor makes v11 refuse binaries too, this test says so instead of silently
passing.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
V13 = os.path.join(SHERLOCK, "skills", "v13", "tools")
V11 = os.path.join(SHERLOCK, "skills", "v11", "tools")

ASCII_RUN = "- Code:  CORSVCC00000774- Call:  CORSVCC00000756"
TEXT_LINE = "2024-04-23T09:54:01Z WARN realtime protection state changed to snoozed"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_corpus(root):
    """A corpus with one text log and one file that is unmistakably binary."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "app.log"), "w", encoding="utf-8") as fh:
        fh.write("2024-04-23T09:52:59Z INFO service started, build 4.2.1\n")
        fh.write(TEXT_LINE + "\n")
        fh.write("2024-04-23T09:59:54Z ERROR login failed for user sa\n")
    blob = bytearray()
    blob += b"ElfFile\x00\x00\x00\x00\x00\x00\x00\x00\x00"      # NUL in the first 8 KB
    for i in range(40):
        blob += b"\x00\x01\x02\x00" * 8
        blob += ASCII_RUN.encode() + b"\n"                       # citable-looking line
        blob += b"\x00\xff\xfe\x00" * 8 + b"\n"
    with open(os.path.join(root, "evidence.evtx"), "wb") as fh:
        fh.write(bytes(blob))
    # the line number of the first ASCII run when the binary is read as text
    with open(os.path.join(root, "evidence.evtx"), "rt", encoding="utf-8",
              errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if ASCII_RUN in line:
                return n
    raise AssertionError("the synthetic binary lost its ASCII run")


def report(path, binary_line):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Отчёт\n\n## Н-1\n\nУлика: `corpus/app.log:2` — "
                 "«realtime protection state changed to snoozed».\n\n"
                 "## Н-2\n\nУлика: `corpus/evidence.evtx:%d` — «%s».\n"
                 % (binary_line, ASCII_RUN))


def verdicts(tools_dir, work):
    out = subprocess.run(
        [sys.executable, os.path.join(tools_dir, "citecheck.py"),
         os.path.join(work, "report.md"), "--corpus", work,
         "--require-quote", "--json"],
        capture_output=True, text=True)
    data = json.loads(out.stdout)
    return ({c["citation"]: c["verdict"] for c in data["citations"]},
            out.returncode, out.stderr)


class BinaryGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="binguard-")
        cls.line = make_corpus(os.path.join(cls.tmp, "corpus"))
        report(os.path.join(cls.tmp, "report.md"), cls.line)
        cls.v13, cls.v13_rc, cls.v13_err = verdicts(V13, cls.tmp)

    def key(self, needle):
        return next(k for k in self.v13 if needle in k)

    def test_citecheck_refuses_a_citation_into_a_binary(self):
        self.assertEqual(self.v13[self.key("evidence.evtx")], "binary-file")

    def test_citecheck_says_so_on_stderr(self):
        self.assertIn("evidence.evtx", self.v13_err)

    def test_a_refused_citation_fails_the_run(self):
        self.assertNotEqual(self.v13_rc, 0,
                            "binary-file must be a failing verdict, not a note")

    def test_legitimate_text_citation_still_passes(self):
        self.assertEqual(self.v13[self.key("app.log")], "ok")

    def test_v11_graded_it_ok_which_is_why_this_test_exists(self):
        if not os.path.isdir(V11):
            self.skipTest("v11 is not present")
        v11, _rc, _err = verdicts(V11, self.tmp)
        self.assertEqual(v11[self.key("evidence.evtx")], "ok",
                         "v11 no longer grades a binary citation as ok — update "
                         "this test's premise, do not delete the guard")

    def test_logjoin_reports_the_binary_as_not_searched_not_as_absent(self):
        lj = load(os.path.join(V13, "logjoin.py"), "logjoin_v13")
        out = subprocess.run(
            [sys.executable, os.path.join(V13, "logjoin.py"), "sa",
             "--corpus", os.path.join(self.tmp, "corpus"), "--json"],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        entries = data["per_id"] if isinstance(data, dict) else data
        self.assertEqual(data.get("files_scanned"), 1,
                         "the binary must not even be scanned")
        blob = json.dumps(entries, ensure_ascii=False)
        self.assertIn("evidence.evtx", blob)
        for entry in (entries if isinstance(entries, list) else [entries]):
            if not isinstance(entry, dict):
                continue
            self.assertNotIn("evidence.evtx", entry.get("absent_in", []),
                             "a binary that was never searched must not be "
                             "reported as evidence of absence")
            self.assertIn("evidence.evtx", entry.get("not_searched_binary", []))
        self.assertTrue(hasattr(lj, "looks_binary"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
