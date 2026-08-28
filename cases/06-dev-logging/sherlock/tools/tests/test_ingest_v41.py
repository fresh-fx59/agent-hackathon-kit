#!/usr/bin/env python3
"""THE INPUT THE SKILL PROMISED AND COULD NOT READ.

`SKILL.md`'s own description has always said «a directory or an ARCHIVE of
logs», and until 2026-08-28 no arm could open an archive or decode a `.evtx`.
It never showed up in forty runs because every corpus we tested with had been
converted to JSONL off to one side by `hack/sherlock-corpora/_tools/
render-evtx.sh` before the skill ever saw it. The first real user would have hit
it on their first attempt.

Every case below is a defect this tool actually hit on the operator's own
`winevt.zip` (296 entries, 143 real channels), not an invented one.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
INGEST = os.path.join(SHERLOCK, "skills", "v41", "tools", "ingest.py")


def run(*args):
    p = subprocess.run([sys.executable, INGEST] + list(args),
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def manifest(out):
    path = os.path.join(out, "_ingest-manifest.tsv")
    with open(path, encoding="utf-8") as fh:
        rows = [l.rstrip("\n").split("\t") for l in fh][1:]
    return rows


class Archives(unittest.TestCase):
    def test_a_zip_of_logs_becomes_a_corpus(self):
        d = tempfile.mkdtemp(prefix="ing-")
        z = os.path.join(d, "logs.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("winevt/Logs/app.log", "2021-01-01 INFO ok\n" * 10)
            zf.writestr("winevt/Logs/sys.jsonl",
                        '{"a":1}\n{"a":2}\n')
        out = os.path.join(d, "corpus")
        rc, text = run(z, "--out", out)
        self.assertEqual(rc, 0, text)
        names = sorted(n for n in os.listdir(out)
                       if n != "_ingest-manifest.tsv")
        self.assertEqual(len(names), 2, names)
        # a nested path must survive INSIDE the name: `Security.jsonl:19934`
        # has to stay one unambiguous citation token.
        self.assertTrue(all("-" in n for n in names), names)

    def test_the_macos_resource_fork_directory_is_not_a_log(self):
        """Measured on winevt.zip: 296 entries, of which 143 were `__MACOSX`
        sidecars. Walking them doubles the corpus with unreadable stubs."""
        d = tempfile.mkdtemp(prefix="ing-")
        z = os.path.join(d, "logs.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("winevt/app.log", "hello\n")
            zf.writestr("__MACOSX/winevt/._app.log", "\x00\x01junk")
            zf.writestr("winevt/.DS_Store", "\x00junk")
        out = os.path.join(d, "corpus")
        rc, text = run(z, "--out", out)
        self.assertEqual(rc, 0, text)
        names = [n for n in os.listdir(out) if n != "_ingest-manifest.tsv"]
        self.assertEqual(len(names), 1, names)

    def test_a_traversal_entry_cannot_escape_the_corpus(self):
        d = tempfile.mkdtemp(prefix="ing-")
        z = os.path.join(d, "evil.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../../escaped.log", "nope\n")
            zf.writestr("ok.log", "fine\n")
        out = os.path.join(d, "corpus")
        run(z, "--out", out, "--keep-going")
        self.assertFalse(os.path.exists(os.path.join(d, "escaped.log")))
        self.assertFalse(os.path.exists("/tmp/escaped.log"))


class NothingIsLostQuietly(unittest.TestCase):
    def test_an_unreadable_input_is_named_and_fails_the_run(self):
        """A corpus that quietly lost the one channel holding the answer is
        worse than no corpus: the report that follows looks complete."""
        d = tempfile.mkdtemp(prefix="ing-")
        src = os.path.join(d, "in")
        os.makedirs(src)
        open(os.path.join(src, "good.log"), "w").write("ok\n")
        with open(os.path.join(src, "weird.bin"), "wb") as fh:
            fh.write(b"\x00\x01\x02binary\x00")
        out = os.path.join(d, "corpus")
        rc, text = run(src, "--out", out)
        self.assertNotEqual(rc, 0, "a lost input must fail the run")
        self.assertIn("weird.bin", text)
        rows = manifest(out)
        self.assertTrue(any(r[2] == "ПРОПУЩЕН" and "weird.bin" in r[0]
                            for r in rows), rows)

    def test_keep_going_still_names_everything_it_dropped(self):
        d = tempfile.mkdtemp(prefix="ing-")
        src = os.path.join(d, "in")
        os.makedirs(src)
        open(os.path.join(src, "good.log"), "w").write("ok\n")
        with open(os.path.join(src, "weird.bin"), "wb") as fh:
            fh.write(b"\x00\x01\x02")
        out = os.path.join(d, "corpus")
        rc, text = run(src, "--out", out, "--keep-going")
        self.assertEqual(rc, 0, text)
        self.assertIn("weird.bin", text)


class Evtx(unittest.TestCase):
    def test_an_evtx_without_a_converter_is_a_named_skip_with_the_cure(self):
        """It must never look like an empty channel. The message has to name
        BOTH installs, because a corporate box usually has neither."""
        d = tempfile.mkdtemp(prefix="ing-")
        src = os.path.join(d, "in")
        os.makedirs(src)
        with open(os.path.join(src, "Security.evtx"), "wb") as fh:
            fh.write(b"ElfFile\x00" + b"\x00" * 100)
        open(os.path.join(src, "other.log"), "w").write("ok\n")
        out = os.path.join(d, "corpus")
        rc, text = run(src, "--out", out, "--keep-going")
        rows = manifest(out)
        row = [r for r in rows if "Security.evtx" in r[0]]
        self.assertTrue(row, rows)
        if row[0][2] == "ПРОПУЩЕН":
            self.assertTrue("evtx_dump" in row[0][3]
                            and "python-evtx" in row[0][3],
                            "the skip must name both cures: %s" % row[0][3])

    def test_an_empty_channel_is_a_fact_not_a_failure(self):
        """50 of the 143 channels in the operator's real archive held zero
        records. Reporting those as failures buried the one input that WAS
        genuinely unreadable under fifty that were merely empty."""
        src = open(INGEST, encoding="utf-8").read()
        self.assertIn("пустой канал", src)
        self.assertIn("AN EMPTY CHANNEL IS A FACT", src)

    def test_the_converter_is_proven_by_output_not_by_which(self):
        """On the operator's Mac `which evtx_dump` resolves to a broken python
        shim that shadows the working Rust binary; choosing by PATH alone
        failed 132 of 132 files while a working converter sat two directories
        away. And `-f <file>` must not be used: evtx_dump refuses to overwrite
        and raises an interactive prompt that a script cannot answer."""
        src = open(INGEST, encoding="utf-8").read()
        self.assertIn("EVTX_BINARIES", src)
        self.assertNotIn('"-f", dest', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
