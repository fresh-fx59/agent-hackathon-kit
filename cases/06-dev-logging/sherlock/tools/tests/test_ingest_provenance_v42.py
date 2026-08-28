#!/usr/bin/env python3
"""THE CORPUS KNEW NOTHING ABOUT THE ARCHIVE IT CAME FROM.

Found on 2026-08-28 by the first end-to-end run through the front door: a real
143-file `winevt.zip` ingested cleanly in 5.1s — and the manifest it wrote named
every source as `/tmp/ingest-t8sdmbwh/Logs/Security.evtx`, a directory that had
already been deleted when the command returned. The archive the customer handed
over appeared nowhere, and neither did its digest.

That is a chain-of-custody hole, not a cosmetic one. A report cites
`Security.jsonl:19934`; the only thing tying that line to the evidence the
customer sent is the manifest, and the manifest pointed at a temp path. «Which
dump did you look at?» had no answer on disk.

Two things are fixed and pinned here: the manifest opens with the sha256 of
every top-level input, and an extracted entry is named
`<archive>!<path inside it>` — never the temp directory.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
INGEST = os.path.join(SHERLOCK, "skills", "v42", "tools", "ingest.py")


def run(*args):
    p = subprocess.run([sys.executable, INGEST] + list(args),
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def read_manifest(out):
    path = os.path.join(out, "_ingest-manifest.tsv")
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip("\n") for l in fh]
    head = [l for l in lines if l.startswith("#")]
    body = [l.split("\t") for l in lines if not l.startswith("#")][1:]
    return head, body


def a_zip(d):
    z = os.path.join(d, "winevt.zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Logs/app.log", "2021-01-01 INFO ok\n" * 10)
        zf.writestr("Logs/sys.jsonl", '{"a":1}\n{"a":2}\n')
    return z


class Provenance(unittest.TestCase):
    def test_the_source_archive_is_hashed_into_the_manifest(self):
        d = tempfile.mkdtemp(prefix="ing42-")
        z = a_zip(d)
        want = hashlib.sha256(open(z, "rb").read()).hexdigest()
        out = os.path.join(d, "corpus")
        rc, text = run(z, "--out", out)
        self.assertEqual(rc, 0, text)
        head, _ = read_manifest(out)
        self.assertTrue(head, "manifest has no provenance header")
        blob = "\n".join(head)
        self.assertIn("sha256:" + want, blob, blob)
        self.assertIn(z, blob, blob)
        self.assertIn("архив", blob, blob)
        # and the human running it sees the same digest without opening a file
        self.assertIn(want, text, text)

    def test_an_extracted_file_is_named_by_its_archive_not_by_tmp(self):
        d = tempfile.mkdtemp(prefix="ing42-")
        z = a_zip(d)
        out = os.path.join(d, "corpus")
        rc, text = run(z, "--out", out)
        self.assertEqual(rc, 0, text)
        _, body = read_manifest(out)
        self.assertEqual(len(body), 2, body)
        for row in body:
            self.assertFalse(row[0].startswith("/tmp/ingest-"), row)
            self.assertTrue(row[0].startswith(z + "!"), row)
        self.assertEqual(sorted(r[0] for r in body),
                         [z + "!Logs/app.log", z + "!Logs/sys.jsonl"])

    def test_a_plain_directory_is_recorded_too_and_keeps_real_paths(self):
        """No archive to hash is a fact, not a silence: the directory is still
        named in the header, and its files keep the path the analyst can open."""
        d = tempfile.mkdtemp(prefix="ing42-")
        src = os.path.join(d, "logs")
        os.makedirs(src)
        with open(os.path.join(src, "app.log"), "w") as fh:
            fh.write("2021-01-01 INFO ok\n")
        out = os.path.join(d, "corpus")
        rc, text = run(src, "--out", out)
        self.assertEqual(rc, 0, text)
        head, body = read_manifest(out)
        self.assertIn("каталог", "\n".join(head), head)
        self.assertIn(src, "\n".join(head), head)
        self.assertEqual(body[0][0], os.path.join(src, "app.log"), body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
