#!/usr/bin/env python3
"""Tests for the v16 fix in citecheck.py — an ambiguous citation is NOT verified.

    python3 tools/tests/test_citecheck_host_qualified_v16.py

WHY THIS TEST EXISTS (measured 2026-08-18, AIT-LDS v2.1 russellmitchell testbed)
-------------------------------------------------------------------------------
`citecheck` is the tool that CERTIFIES a citation: the report says "the evidence
is at path:line", and citecheck re-reads that line and says whether the claim
holds. On a bundle collected from many machines it could certify a citation
against the WRONG MACHINE.

Two halves, both measured before the fix:

* `resolve()` returns MANY candidates for one citation. On that testbed (7,464
  files) **1,038 basenames of 2,092 live on more than one host**. `logs/auth.log`
  resolves to **10** files, `logs/audit/audit.log` to **7**, `facts.json` to
  **22**. v15 already reported this — it returns a `how` of `suffix-ambiguous` /
  `base-ambiguous` and `render()` prints `[неоднозначно: N файла]`.

* `check()` then tried EVERY candidate and kept the BEST verdict by `RANK`. So
  ambiguity always resolved IN FAVOUR OF THE CITATION: a claim that is false on
  the host it names was certified `ok` because some other machine's file of the
  same name happened to agree at that line number. Reporting the ambiguity and
  then grading it as if it were not ambiguous is the exact inversion of a gate.

How often it can bite, on the runs on disk (2026-08-18):

    what was cited                                   distinct paths   ambiguous
    ait-all-v15 worklist, as written (root-relative)       245          0   (0 %)
    the same citations shortened to a basename              92         69  (75 %)
    the same citations with the host prefix dropped        135         23  (17 %)
    ait-intranet-v15's own citations vs the WHOLE bundle    15         14  (93 %)
    cam-lds s1 citations shortened to a basename            22         13  (59 %)
    BlueSky (108 files, 108 distinct basenames)             37          0   (0 %)

So the tools' own worklists are safe by construction — `logmap.py` writes
root-relative paths, and all 9,123 citations across the 19 runs in `_runs/`
resolve `rel`. The exposure is (a) what the MODEL types into the report and
(b) a `--corpus` pointed one level above the host the report was written about.

THE RULE THIS PINS: ambiguity fails closed. More than one candidate ⇒ verdict
`ambiguous`, the candidates are named, no line is read, and the exit code is
non-zero. One candidate is a MATCH, not a guess — the basename fallback keeps
working on single-host bundles, which is what the regression half asserts.

The corpora here are synthetic, so the suite needs no dataset and runs anywhere;
the real BlueSky corpus is used when present as the single-host regression bar.
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
V15 = os.path.join(SHERLOCK, "skills", "v15", "tools")
V16 = os.path.join(SHERLOCK, "skills", "v16", "tools")
BLUESKY = os.path.expanduser(
    "~/Documents/projects/personal-os/projects/active/attachments/"
    "sherlock-cyber-bench/corpus")

# The claim is TRUE of web-2's line 3 and FALSE of web-1's and db-1's.
TRUE_LINE = ("2026-08-18T04:11:02+00:00 sshd[4412]: Accepted publickey for "
             "svc_deploy from 10.11.4.8 port 51122 ssh2")
FALSE_LINE = ("2026-08-18T04:11:02+00:00 systemd[1]: Started Daily apt "
              "download activities.")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def make_multi_host_corpus(root):
    """Three machines, one filename. Only web-2 supports the claim."""
    for host, third in (("web-1", FALSE_LINE), ("web-2", TRUE_LINE),
                        ("db-1", FALSE_LINE)):
        _write(os.path.join(root, "hosts", host, "logs", "auth.log"),
               ["2026-08-18T04:10:00+00:00 sshd[4400]: Server listening on 0.0.0.0 port 22.",
                "2026-08-18T04:10:31+00:00 sshd[4401]: Connection closed by 10.11.4.8 port 51120",
                third])
    # a name that exists exactly once — the unambiguous case must keep working
    _write(os.path.join(root, "hosts", "web-2", "logs", "onlyhere.log"),
           ["2026-08-18T04:12:00+00:00 promo[9]: ledger refused negative charge"])


def make_single_host_corpus(root):
    """The same bundle with one machine — nothing here can be ambiguous."""
    _write(os.path.join(root, "logs", "auth.log"),
           ["2026-08-18T04:10:00+00:00 sshd[4400]: Server listening on 0.0.0.0 port 22.",
            "2026-08-18T04:10:31+00:00 sshd[4401]: Connection closed by 10.11.4.8 port 51120",
            TRUE_LINE])
    _write(os.path.join(root, "logs", "onlyhere.log"),
           ["2026-08-18T04:12:00+00:00 promo[9]: ledger refused negative charge"])


CLAIM = ("Ключ атакующего принят: «Accepted publickey for svc_deploy from "
         "10.11.4.8 port 51122 ssh2» — %s")


def report_text(cite):
    return "# Отчёт\n\n## Н-1\n\n" + (CLAIM % cite) + "\n"


def run(tools_dir, report, corpus, args=()):
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8",
                                     delete=False) as fh:
        fh.write(report)
        path = fh.name
    try:
        p = subprocess.run(
            [sys.executable, os.path.join(tools_dir, "citecheck.py"), path,
             "--corpus", corpus, *args],
            capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.unlink(path)


def run_json(tools_dir, report, corpus, args=()):
    rc, out, err = run(tools_dir, report, corpus, ("--json",) + tuple(args))
    return rc, json.loads(out), err


class AmbiguityFailsClosed(unittest.TestCase):
    """More than one candidate ⇒ not verified, and the candidates are named."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="citecheck-v16-")
        cls.corpus = os.path.join(cls.tmp, "bundle")
        make_multi_host_corpus(cls.corpus)

    def test_v15_certified_it_against_the_wrong_machine(self):
        """The premise. If v15 stops doing this, say so — do not delete the fix."""
        rc, d, _ = run_json(V15, report_text("logs/auth.log:3"), self.corpus)
        self.assertEqual([c["verdict"] for c in d["citations"]], ["ok"])
        self.assertEqual(rc, 0)
        self.assertEqual(d["citations"][0]["candidates"], 3)
        self.assertTrue(d["citations"][0]["how"].endswith("ambiguous"))
        self.assertIn("web-2", d["citations"][0]["resolved"],
                      "v15 graded the citation against whichever host agreed")

    def test_an_ambiguous_citation_is_not_verified(self):
        rc, d, _ = run_json(V16, report_text("logs/auth.log:3"), self.corpus)
        self.assertEqual([c["verdict"] for c in d["citations"]], ["ambiguous"])

    def test_an_ambiguous_citation_fails_the_run(self):
        rc, _d, _ = run_json(V16, report_text("logs/auth.log:3"), self.corpus)
        self.assertNotEqual(rc, 0, "ambiguous must be a failing verdict")

    def test_the_candidates_are_named_not_counted(self):
        rc, d, _ = run_json(V16, report_text("logs/auth.log:3"), self.corpus)
        cand = d["citations"][0]["candidate_paths"]
        self.assertEqual(len(cand), 3)
        for host in ("web-1", "web-2", "db-1"):
            self.assertTrue(any(host in c for c in cand), cand)

    def test_no_candidate_is_silently_chosen(self):
        rc, d, _ = run_json(V16, report_text("logs/auth.log:3"), self.corpus)
        self.assertIsNone(d["citations"][0]["resolved"],
                          "picking one of many is the defect, not the fix")

    def test_the_rendered_output_names_the_candidates(self):
        rc, out, _ = run(V16, report_text("logs/auth.log:3"), self.corpus)
        self.assertIn("ambiguous", out)
        for host in ("web-1", "web-2", "db-1"):
            self.assertIn(host, out)

    def test_the_summary_counts_it(self):
        rc, d, _ = run_json(V16, report_text("logs/auth.log:3"), self.corpus)
        self.assertEqual(d["summary"]["ambiguous"], 1)
        self.assertEqual(d["summary"]["ok"], 0)
        self.assertEqual(d["summary"]["verified_pct"], 0.0)

    def test_the_basename_fallback_branch_is_covered_too(self):
        """`nosuchdir/auth.log` misses the suffix branch and lands in by_base."""
        M = load(os.path.join(V16, "citecheck.py"), "cc_v16_resolve")
        by_rel, by_base = M.index_corpus(self.corpus)
        cand, how = M.resolve("nosuchdir/auth.log", by_rel, by_base)
        self.assertEqual(how, "base-ambiguous")
        self.assertEqual(len(cand), 3)
        rc, d, _ = run_json(V16, report_text("nosuchdir/auth.log:3"), self.corpus)
        self.assertEqual([c["verdict"] for c in d["citations"]], ["ambiguous"])

    def test_a_host_qualified_citation_is_graded_normally(self):
        for host, want in (("web-2", "ok"), ("web-1", "wrong-content")):
            cite = "hosts/%s/logs/auth.log:3" % host
            rc, d, _ = run_json(V16, report_text(cite), self.corpus)
            self.assertEqual([c["verdict"] for c in d["citations"]], [want], cite)
            self.assertEqual(d["citations"][0]["candidates"], 1)

    def test_one_candidate_is_a_match_not_a_guess(self):
        """The bare-name path must keep working when the name is unique."""
        rc, d, _ = run_json(
            V16, "Реестр отказал в отрицательном списании: «ledger refused "
                 "negative charge» — onlyhere.log:1", self.corpus)
        self.assertEqual([c["verdict"] for c in d["citations"]], ["ok"])
        self.assertEqual(rc, 0)

    def test_an_ambiguous_citation_is_read_from_no_file(self):
        """Fail closed means fail EARLY: a refused citation costs no file read."""
        M = load(os.path.join(V16, "citecheck.py"), "cc_v16_noread")
        opened = []
        real = M.read_lines
        M.read_lines = lambda p, w: (opened.append(p), real(p, w))[1]
        try:
            M.check(report_text("logs/auth.log:3"), self.corpus)
        finally:
            M.read_lines = real
        self.assertEqual([p for p in opened if "auth.log" in p], [])


class TheLedgerCountsIt(unittest.TestCase):
    """`--ledger` is the stopping condition; an ambiguous citation must block it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="citecheck-v16-ledger-")
        cls.corpus = os.path.join(cls.tmp, "bundle")
        make_multi_host_corpus(cls.corpus)
        cls.wl = os.path.join(cls.tmp, "worklist.tsv")
        with open(cls.wl, "w", encoding="utf-8") as fh:
            fh.write("# id\tвердикт\tось\tссылка\tчастота\tзапись\n")
            fh.write("g001\tD Н-1\tcat\thosts/web-2/logs/auth.log:3\tn=1\t%s\n"
                     % TRUE_LINE)

    def test_ambiguous_blocks_the_stopping_condition(self):
        rc, out, _ = run(V16, report_text("logs/auth.log:3"), self.corpus,
                         ("--ledger", self.wl))
        self.assertNotEqual(rc, 0)
        self.assertIn("НЕ ЗАКОНЧЕНО", out)
        self.assertNotIn("можно отдавать отчёт", out)

    def test_the_host_qualified_form_lets_it_through(self):
        rc, out, _ = run(V16, report_text("hosts/web-2/logs/auth.log:3"),
                         self.corpus, ("--ledger", self.wl))
        self.assertIn("можно отдавать отчёт", out)
        self.assertEqual(rc, 0)


class SingleHostIsUntouched(unittest.TestCase):
    """v16 must be v15 exactly wherever nothing is ambiguous."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="citecheck-v16-single-")
        cls.corpus = os.path.join(cls.tmp, "bundle")
        make_single_host_corpus(cls.corpus)

    def _both(self, report, corpus, args=()):
        a = run_json(V15, report, corpus, args)
        b = run_json(V16, report, corpus, args)
        return a, b

    def _same(self, report, corpus, args=()):
        (rc15, d15, _), (rc16, d16, _) = self._both(report, corpus, args)
        self.assertEqual(rc15, rc16, report)
        for c15, c16 in zip(d15["citations"], d16["citations"]):
            for k in ("citation", "verdict", "resolved", "line", "score",
                      "matched_tokens", "claim_tokens", "how", "candidates"):
                self.assertEqual(c15.get(k), c16.get(k), "%s / %s" % (report, k))
        for k, v in d15["summary"].items():
            self.assertEqual(v, d16["summary"][k], "summary[%s]" % k)

    def test_ok_is_unchanged(self):
        self._same(report_text("logs/auth.log:3"), self.corpus)

    def test_wrong_content_is_unchanged(self):
        self._same(report_text("logs/auth.log:2"), self.corpus)

    def test_out_of_range_is_unchanged(self):
        self._same(report_text("logs/auth.log:900"), self.corpus)

    def test_bare_name_is_unchanged(self):
        self._same(report_text("auth.log:3"), self.corpus)

    def test_non_reference_is_unchanged(self):
        self._same(report_text("logs/nowhere.log:3"), self.corpus)

    def test_require_quote_is_unchanged(self):
        self._same(report_text("logs/auth.log:3"), self.corpus, ("--require-quote",))

    @unittest.skipUnless(os.path.isdir(BLUESKY),
                         "the BlueSky corpus is not on this box")
    def test_bluesky_verdicts_are_identical(self):
        """The real single-host regression bar: 108 files, 108 distinct names."""
        rep = ["# Отчёт", ""]
        names = sorted(os.listdir(os.path.join(BLUESKY, "evtx"))
                       if os.path.isdir(os.path.join(BLUESKY, "evtx")) else [])
        for n in names[:12]:
            rep.append("## Н-%d" % (len(rep)))
            rep.append("Улика: «Microsoft-Windows-Search» — evtx/%s:8" % n)
        self._same("\n".join(rep) + "\n", BLUESKY)


class RenderCanPrintEveryVerdict(unittest.TestCase):
    """Found while fixing the above, and it is the same disease.

    `render()` looked its marker up as `mark[verdict]`. v13 added `binary-file`
    and never added it to that table, so the DEFAULT (non-JSON) mode — the one
    SKILL.md tells the model to run — died with `KeyError: 'binary-file'` on
    exactly the citation the guard exists to refuse. The v13 tests all passed
    because they every one of them ran `--json`. Reproduced on v15 before fixing.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="citecheck-v16-render-")
        corpus = os.path.join(cls.tmp, "bundle")
        os.makedirs(corpus)
        blob = bytearray(b"ElfFile\x00\x00\x00\x00")
        blob += b"\x00\x01\x02\x00" * 8 + b"\n"
        blob += b"- Code:  CORSVCC00000774 alpha beta gamma\n"
        blob += b"\x00\xff\xfe\x00" * 8 + b"\n"
        with open(os.path.join(corpus, "evidence.evtx"), "wb") as fh:
            fh.write(bytes(blob))
        cls.corpus = corpus
        cls.report = ("Улика: «- Code:  CORSVCC00000774 alpha beta gamma» — "
                      "evidence.evtx:2\n")

    def test_v15_crashes_rendering_a_binary_file_verdict(self):
        rc, out, err = run(V15, self.report, self.corpus)
        self.assertIn("KeyError", err,
                      "v15 no longer crashes here — update this premise")

    def test_v16_renders_it(self):
        rc, out, err = run(V16, self.report, self.corpus)
        self.assertNotIn("Traceback", err)
        self.assertIn("binary-file", out)
        self.assertNotEqual(rc, 0)


class TheSkillTextStatesTheRule(unittest.TestCase):
    """v13's lesson: a verdict the skill text never mentions cannot be acted on."""

    SKILL = os.path.join(SHERLOCK, "skills", "v16", "SKILL.md")
    TOOLSMD = os.path.join(SHERLOCK, "skills", "v16", "reference", "tools.md")

    def test_skill_md_names_the_verdict(self):
        with open(self.SKILL, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("ambiguous", body)

    def test_skill_md_tells_the_model_to_cite_host_qualified_paths(self):
        with open(self.SKILL, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("от корня корпуса", body,
                      "the input gate must be stated, not only enforced")

    def test_tools_md_lists_the_verdict(self):
        with open(self.TOOLSMD, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("ambiguous", body)

    def test_v15_is_frozen(self):
        """v16 is a copy of v15 plus the smallest change set."""
        import filecmp
        for rel in ("SKILL.md", "reference/report-format.md",
                    "reference/code-and-spec.md", "tools/logmap.py",
                    "tools/logjoin.py"):
            a = os.path.join(SHERLOCK, "skills", "v15", rel)
            b = os.path.join(SHERLOCK, "skills", "v16", rel)
            if rel == "SKILL.md":
                continue
            self.assertTrue(filecmp.cmp(a, b, shallow=False),
                            "%s must not change in v16" % rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
