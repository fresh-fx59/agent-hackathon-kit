#!/usr/bin/env python3
"""Tests for skills/v11/tools/citecheck.py — the arm-local fork of the checker.

Two changes are measured, not stylistic, and both are asserted here.

1. The extension list is GONE. It decided "is this a citation?" from a hard-coded
   set of suffixes, so a citation to `inhouse/x.plog`, `syslog/node-a/dmesg` or
   `misc/ordersync-prod-2026-07-28` produced NO OUTPUT LINE AT ALL. Measured
   against one 649 MB answer key: 21 of 108 proof locations were un-citable, and
   for two of thirteen cards the loss was total — a report that found them could
   not prove it. A token is a citation now iff it resolves in the corpus.
2. `--ledger` turns the stopping condition into four numbers and an exit code.

    python3 tools/tests/test_citecheck_v11.py
"""
import gzip
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
CC = os.path.join(SHERLOCK, "skills", "v11", "tools", "citecheck.py")

_spec = importlib.util.spec_from_file_location("citecheck_v11", CC)
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)


def corpus(d):
    """A miniature of the shapes that broke the old gate."""
    def w(rel, lines, gz=False):
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        op = gzip.open if gz else open
        with op(p, "wt", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    w("apps/app.log", ["2026-07-28 09:00:00 start ok",
                       "2026-07-28 09:00:01 reservation timeout for order",
                       "2026-07-28 09:00:02 done"])
    w("inhouse/bespoke.plog", ["20260728|120047.000|+0300|svc|CHATTER|q",
                              "20260728|173351.000|+0300|svc|FATALITY|"
                              "ledger refused negative charge"])
    w("misc/ordersync-prod-2026-07-28", ["batch 1 compared 634 records",
                                         "giving up after 5 attempts"])
    w("syslog/node-a/syslog", ["Jul 28 09:00:00 node-a kubelet: probe succeeded",
                               "Jul 28 13:31:02 node-a sudo: scale replicas=2"])
    w("syslog/node-b/syslog", ["Jul 28 08:12:51 node-b kernel: table full",
                               "Jul 28 09:02:47 node-b chronyd: clock wrong"])
    w("old.log.gz", ["line one", "line two", "the needle on line three"], gz=True)
    return d


def run_human(report_text, root, *extra):
    """The RENDERED output — what the model actually reads. `run()` asks for
    --json, so a message-quality assertion made against it tests nothing."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(report_text)
        path = fh.name
    try:
        return subprocess.run([sys.executable, CC, path, "--corpus", root, *extra],
                              capture_output=True, text=True)
    finally:
        os.unlink(path)


def run(report_text, root, *extra):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(report_text)
        path = fh.name
    try:
        p = subprocess.run([sys.executable, CC, path, "--corpus", root,
                            "--json", *extra], capture_output=True, text=True)
        return p, json.loads(p.stdout)
    finally:
        os.unlink(path)


class TheCorpusIndexIsTheGateNotAnExtensionList(unittest.TestCase):

    def test_a_bespoke_extension_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- inhouse/bespoke.plog:2 — «ledger refused negative "
                          "charge»", d)
            self.assertEqual(got["summary"]["total"], 1)
            self.assertEqual(got["citations"][0]["verdict"], "ok")

    def test_a_file_with_no_extension_at_all_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- misc/ordersync-prod-2026-07-28:2 — «giving up after "
                          "5 attempts»", d)
            self.assertEqual(got["summary"]["total"], 1)
            self.assertEqual(got["citations"][0]["verdict"], "ok")

    def test_the_old_gate_would_have_dropped_both_in_silence(self):
        """Negative control against the version this forked from: if the old
        checker still scored these, the fix would be decorative."""
        old = os.path.join(SHERLOCK, "skills", "v10", "tools", "citecheck.py")
        if not os.path.exists(old):
            self.skipTest("v10 bundle not present")
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            body = ("- inhouse/bespoke.plog:2 — «ledger refused negative charge»\n"
                    "- misc/ordersync-prod-2026-07-28:2 — «giving up after 5 "
                    "attempts»\n")
            p = os.path.join(d, "r.md")
            open(p, "w", encoding="utf-8").write(body)
            r = subprocess.run([sys.executable, old, p, "--corpus", d, "--json"],
                               capture_output=True, text=True)
            self.assertEqual(json.loads(r.stdout)["summary"]["total"], 0,
                             "the old gate was supposed to drop these silently")

    def test_a_clock_an_ip_and_an_order_id_are_still_not_citations(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("замечено в 13:31 на 127.0.0.1:8317 по заказу "
                          "ORD-88240:11 — ничего из этого не ссылка", d)
            self.assertEqual(got["summary"]["total"], 0)
            self.assertEqual(got["summary"]["не-ссылка"], 0,
                             "printing a timestamp as a lost citation is noise")

    def test_a_file_shaped_token_that_resolves_to_nothing_is_REPORTED(self):
        """Today a dropped citation produced no output line at all, so the model
        could not tell "checked and fine" from "never checked"."""
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- logs/does-not-exist.log:5 — «что-то»", d)
            self.assertEqual(got["summary"]["не-ссылка"], 1)
            self.assertEqual(got["non_references"][0]["path"],
                             "logs/does-not-exist.log")

    def test_a_source_path_quoted_INSIDE_a_log_line_is_not_a_reference(self):
        """A goroutine frame cites its own source file. Counting that made a
        correct report unable to pass the ledger."""
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run('- apps/app.log:2 — «2026-07-28 09:00:01 reservation '
                          'timeout for order, see payments/retrier.go:104»', d)
            self.assertEqual(got["summary"]["не-ссылка"], 0)
            self.assertEqual(got["summary"]["total"], 1)

    def test_a_bare_basename_that_matches_two_files_is_flagged_ambiguous(self):
        """One corpus holds syslog/node-a/syslog and syslog/node-b/syslog."""
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- syslog:2 — «table full»", d)
            self.assertEqual(got["summary"]["ambiguous"], 1)

    def test_gz_line_numbers_are_in_the_decompressed_stream(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- old.log.gz:3 — «the needle on line three»", d)
            self.assertEqual(got["citations"][0]["verdict"], "ok")


class TheOldVerdictsStillWork(unittest.TestCase):

    def test_wrong_content_is_still_caught(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run('- apps/app.log:1 — "reservation timeout for order '
                          'blocked the checkout thread pool entirely"', d)
            self.assertEqual(got["citations"][0]["verdict"], "wrong-content")

    def test_out_of_range(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- apps/app.log:9999 — «что-то про start ok»", d)
            self.assertEqual(got["citations"][0]["verdict"], "out-of-range")

    def test_a_bad_citation_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            p, _got = run("- apps/app.log:9999 — «start ok»", d)
            self.assertEqual(p.returncode, 1)

    def test_a_clean_report_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            p, _got = run("- apps/app.log:1 — «2026-07-28 09:00:00 start ok»", d)
            self.assertEqual(p.returncode, 0)

    def test_a_range_is_a_legal_citation(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- apps/app.log:1-3 — «reservation timeout for order»", d)
            self.assertEqual(got["citations"][0]["verdict"], "ok")


class RequireQuoteIsTheSecondGate(unittest.TestCase):
    """Reported-but-mis-cited is a failure mode the ledger alone leaves open."""

    def test_a_verbatim_substring_passes(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- apps/app.log:2 — «reservation timeout for order»", d,
                          "--require-quote")
            self.assertEqual(got["citations"][0]["verdict"], "ok")
            self.assertEqual(got["citations"][0]["via"], "quote")

    def test_a_paraphrase_fails(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- apps/app.log:2 — тут написано про таймаут "
                          "резервирования заказа", d, "--require-quote")
            self.assertEqual(got["citations"][0]["verdict"], "no-quote")

    def test_a_paraphrase_is_tolerated_WITHOUT_the_flag(self):
        """Calling a true cross-language claim a fabrication would teach the model
        to delete good evidence — worse than a decorative citation. So the strict
        mode is opt-in, and used only at the final gate."""
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            _p, got = run("- apps/app.log:2 — тут написано про таймаут "
                          "резервирования заказа", d)
            self.assertNotEqual(got["citations"][0]["verdict"], "no-quote")


class ARefusalMustSayHowToPassIt(unittest.TestCase):
    """D07 burned 11.15 M tokens and produced no row, fighting this message.

    The run copied four log lines into the report verbatim after an em-dash and
    no delimiters — `- file:50539 — 2026-07-28 11:05:12.771 DEBUG …`. `no-quote`
    was the CORRECT verdict, and the text it printed was «нет дословной цитаты
    этой строки — процитируй её кусок буквально». From the model's side that
    reads as a contradiction: it had copied the line literally. So it did what
    D04 did before it — 40 turns reading citecheck.py's own source to work out
    what the checker wanted — and timed out at 2700 s.

    The gate was right and unpassable in practice, because a refusal that does
    not name the accepted form is not actionable. Four delimiters are accepted;
    the message named none of them. It now names them and shows the fix built
    from the offending line, so the next move is a paste, not an investigation.
    """

    def test_the_message_names_the_delimiters_it_accepts(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            p = run_human("- apps/app.log:2 — reservation timeout for order", d,
                          "--require-quote")
            for delim in ("«", "»", '"', "`"):
                self.assertIn(delim, p.stdout,
                              "a refusal that hides the accepted form sends the "
                              "model into the source:\n%s" % p.stdout)

    def test_the_message_shows_a_ready_made_example_from_the_offending_line(self):
        """Not a generic template: the fix, built from THIS line, pasteable."""
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            p = run_human("- apps/app.log:2 — reservation timeout for order", d,
                          "--require-quote")
            self.assertIn("например", p.stdout, p.stdout)
            self.assertRegex(p.stdout, r"apps/app\.log:2 — «[^»]{4,}»",
                             "the example must be a complete citation line:\n%s"
                             % p.stdout)

    def test_the_example_it_prints_actually_passes_the_gate(self):
        """An example that does not pass is worse than none: it would send the
        model back to the source with one more contradiction to explain."""
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            p = run_human("- apps/app.log:2 — reservation timeout for order", d,
                          "--require-quote")
            example = None
            for line in p.stdout.splitlines():
                if "например" in line:
                    example = line.split("например:", 1)[1].strip()
            self.assertIsNotNone(example, p.stdout)
            _p2, got2 = run("- " + example, d, "--require-quote")
            self.assertEqual(got2["citations"][0]["verdict"], "ok",
                             "the tool's own suggested fix was rejected by the "
                             "tool: %r" % example)


class TheLedgerIsTheStoppingCondition(unittest.TestCase):

    WORKLIST = [
        "# id\tвердикт\tось\tссылка\tчастота\tзапись",
        "g001\t?\trare\tapps/app.log:2\tn=1\treservation timeout for order",
        "g002\t?\trare\tinhouse/bespoke.plog:2\tn=1\tFATALITY",
        "g003\t?\tcat\tsyslog/node-a/syslog:2\tn=1\tsudo scale",
        "g004\t?\tbg\tapps/app.log:1\tn=99\tstart ok",
        "g005\t?\trare\told.log.gz:3\tn=1\tneedle",
    ]

    def ledger(self, d, rows, report="- apps/app.log:2 — «reservation timeout "
                                     "for order»", extra=()):
        wl = os.path.join(d, "worklist.tsv")
        open(wl, "w", encoding="utf-8").write("\n".join(rows) + "\n")
        rp = os.path.join(d, "r.md")
        open(rp, "w", encoding="utf-8").write(report + "\n")
        p = subprocess.run([sys.executable, CC, rp, "--corpus", d,
                            "--ledger", wl, *extra],
                           capture_output=True, text=True)
        return p

    def test_a_fresh_worklist_blocks_the_report(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            p = self.ledger(d, self.WORKLIST)
            self.assertEqual(p.returncode, 1)
            self.assertIn("неразобранных строк: 5 из 5", p.stdout)
            self.assertIn("НЕ ЗАКОНЧЕНО", p.stdout)

    def test_three_verdicts_close_exactly_three_rows(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            rows = list(self.WORKLIST)
            rows[1] = rows[1].replace("\t?\t", "\tD Н-2\t", 1)
            rows[2] = rows[2].replace("\t?\t", "\tX нет логов за окно\t", 1)
            rows[3] = rows[3].replace("\t?\t", "\tN доля 12,7% → 12,4%\t", 1)
            p = self.ledger(d, rows)
            self.assertIn("неразобранных строк: 2 из 5", p.stdout)

    def test_a_norm_verdict_without_a_number_is_rejected(self):
        """«Выглядит штатно» is not a verdict. Normality is proved by frequency,
        and it is the only way the red herrings are refutable at all."""
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            rows = list(self.WORKLIST)
            rows[1] = rows[1].replace("\t?\t", "\tN выглядит штатно\t", 1)
            p = self.ledger(d, rows)
            self.assertIn("неразобранных строк: 5 из 5", p.stdout)
            self.assertIn("без цифры", p.stdout)

    def test_a_finding_pointer_counts_as_adjudicated(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            rows = list(self.WORKLIST)
            rows[1] = rows[1].replace("\t?\t", "\tН-2\t", 1)
            p = self.ledger(d, rows)
            self.assertIn("неразобранных строк: 4 из 5", p.stdout)

    def test_a_range_verdict_closes_the_whole_range(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            rows = list(self.WORKLIST) + [
                "g001-g004\tN доля 12,7% → 12,4%\trare\t-\tn=0\tсводно"]
            p = self.ledger(d, rows)
            self.assertIn("неразобранных строк: 1 из 6", p.stdout)

    def test_a_finding_with_no_confirmed_citation_is_counted(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            rows = [r.replace("\t?\t", "\tX файл обрезан ротацией\t", 1)
                    for r in self.WORKLIST]
            p = self.ledger(d, rows, report="### Н-1 · сломалось\nбез единой улики")
            self.assertIn("находок без подтверждённой цитаты: 1 из 1", p.stdout)
            self.assertEqual(p.returncode, 1)

    def test_all_four_zero_means_go(self):
        with tempfile.TemporaryDirectory() as d:
            corpus(d)
            rows = [r.replace("\t?\t", "\tX файл обрезан ротацией\t", 1)
                    for r in self.WORKLIST]
            p = self.ledger(
                d, rows,
                report="### Н-1 · таймаут\n- apps/app.log:2 — «reservation "
                       "timeout for order»",
                extra=("--require-quote",))
            self.assertEqual(p.returncode, 0, p.stdout[-2000:])
            self.assertIn("можно писать отчёт", p.stdout)


class VerdictClassification(unittest.TestCase):

    def test_open_states(self):
        for cell in ("?", "", "-", "N", "н", "мимо"):
            self.assertEqual(C.classify_verdict(cell)[0], "open", cell)

    def test_closed_states(self):
        for cell in ("D Н-2", "Д Н-3", "Х нет данных за окно", "Н-2",
                     "N доля 25,4% → 24,5%"):
            self.assertEqual(C.classify_verdict(cell)[0], "closed", cell)

    def test_no_verdict_closes_on_its_letter_alone(self):
        """REGRESSION. `X` and `D` used to close a row for free, so the cheapest
        way to empty a 250-row worklist — herrings included — was one sed:
        `s/\t?\t/\tX\t/`. That returned «можно писать отчёт», and the previous
        version of this suite asserted it as the PASS condition. Each verdict must
        now carry its own proof: N a number, D a finding pointer, X a reason."""
        for cell in ("D", "Д", "X", "Х", "x", "х", "N", "Н"):
            self.assertEqual(C.classify_verdict(cell)[0], "open", cell)

    def test_range_ids_expand_for_every_dash_a_model_might_type(self):
        """REGRESSION. RANGE_ID_RE accepts [-–—] but the width was computed with
        cell.split("-"), ASCII only — so an en-dash range built ids like
        g00000041, closed nothing, and left the counter stuck with no diagnostic.
        A Russian-writing model types en-dashes."""
        want = ["g041", "g042", "g043", "g044"]
        for dash in ("-", "\u2013", "\u2014"):
            self.assertEqual(C._ids_of("g041%sg044" % dash), want, dash)

    def test_range_ids_expand(self):
        self.assertEqual(C._ids_of("g041-g044"),
                         ["g041", "g042", "g043", "g044"])
        self.assertEqual(C._ids_of("g007"), ["g007"])


class RequireQuoteMustNotDependOnTheQUOTEDelimiter(unittest.TestCase):
    """D04 spent 123 of its 146 turns — 14.2M of 16.4M input tokens, 87 % of the
    run — failing to pass `--require-quote` on citations that were verbatim
    correct. It then delivered a 161-char message and scored `collapse`.

    Root cause, reproduced here from the real report line: the checker accepts
    FOUR quote delimiters (`"` `«»` `“”` and a backtick, QUOTE_RE) but protects
    only THREE of them from the bare-filename stripper (LONG_QUOTE_RE has no
    backtick branch). So `c.title` and `c.attrs` inside a backtick-quoted SQL
    line were blanked out as if they were filenames, and the quote could no
    longer be found in the line it was copied from verbatim.

    Log lines are FULL of dotted identifiers — Java logger names
    (`c.a.catalog.repo.VendorRefLookupRepository`), Python modules
    (`inventory.db`), SQL column lists. Backticks are also the markdown
    convention the report format itself uses. The gate was therefore
    unpassable for the commonest way to quote the commonest kind of log line.
    """

    # verbatim from cases/D04/corpus, apps/catalog-svc/catalog-svc.log:50539
    LINE = ("2026-07-28 11:05:12.771 DEBUG 1 --- [http-nio-8080-exec-7] "
            "c.a.catalog.repo.VendorRefLookupRepository: executing SELECT "
            "c.sku_id, c.title, c.attrs FROM catalog_items c WHERE c.attrs "
            "->> 'vendor_ref' = ? ORDER BY c.updated_at DESC -- took 1188ms "
            "rows=3")

    def _corpus(self, d):
        p = os.path.join(d, "apps", "catalog-svc")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "catalog-svc.log"), "w",
                  encoding="utf-8") as fh:
            fh.write("filler\nfiller\nfiller\n" + self.LINE + "\n")
        return d                                  # the line above is :4

    def test_backtick_quoted_evidence_passes_require_quote(self):
        """The claim quotes the line verbatim. The delimiter must not decide."""
        with tempfile.TemporaryDirectory() as d:
            self._corpus(d)
            report = ("- `apps/catalog-svc/catalog-svc.log:4` — vendor_ref "
                      "lookup, 1188 ms: `executing SELECT c.sku_id, c.title, "
                      "c.attrs FROM catalog_items c WHERE c.attrs ->> "
                      "'vendor_ref' = ? ORDER BY c.updated_at DESC -- took "
                      "1188ms rows=3`\n")
            _p, got = run(report, d, "--require-quote")
            self.assertEqual(got["citations"][0]["verdict"], "ok")
            self.assertEqual(got["citations"][0]["via"], "quote")

    def test_the_same_evidence_in_guillemets_already_passed(self):
        """The control. If this ever fails the test above proves nothing."""
        with tempfile.TemporaryDirectory() as d:
            self._corpus(d)
            report = ("- `apps/catalog-svc/catalog-svc.log:4` — vendor_ref "
                      "lookup, 1188 ms: «executing SELECT c.sku_id, c.title, "
                      "c.attrs FROM catalog_items c WHERE c.attrs ->> "
                      "'vendor_ref' = ? ORDER BY c.updated_at DESC -- took "
                      "1188ms rows=3»\n")
            _p, got = run(report, d, "--require-quote")
            self.assertEqual(got["citations"][0]["verdict"], "ok")
            self.assertEqual(got["citations"][0]["via"], "quote")

    def test_a_verbatim_quote_wins_over_a_longer_inexact_one(self):
        """D04 also wrote a SHORT verbatim quote beside the long code span.

        `support` returned on the first quote that cleared the token floor, so
        the long one short-circuited the verdict to `quote-tokens` and the
        short verbatim one was never tried. Longest-first is the right search
        ORDER; returning before every quote has had its verbatim chance is not.
        """
        with tempfile.TemporaryDirectory() as d:
            self._corpus(d)
            report = ("- `apps/catalog-svc/catalog-svc.log:4` — vendor_ref "
                      "lookup: \"executing SELECT c.sku_id ... ORDER BY "
                      "c.updated_at DESC and then some paraphrase\" "
                      "«took 1188ms rows=3»\n")
            _p, got = run(report, d, "--require-quote")
            self.assertEqual(got["citations"][0]["verdict"], "ok")
            self.assertEqual(got["citations"][0]["via"], "quote")


if __name__ == "__main__":
    unittest.main(verbosity=2)
