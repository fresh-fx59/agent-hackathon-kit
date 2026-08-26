#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rollover.py + the «окно записей» gate — did records vanish inside the window?

THE ANCHOR. An auditing agent once asserted «~402 000 записей вытеснено из
Security.jsonl». It was WRONG: that channel runs 402275…437190 — a span of
34 916 — and the file holds exactly 34 916 records. One subtraction settles it.
`test_anchor_shape_is_contiguous` is that subtraction on a synthetic file of the
same shape; the real 143-file corpus is not in this repo, so the shape is
reproduced rather than the bytes.

WHAT THESE TESTS PROTECT, in order of how often this project has been burned:
 1. THE WIRING. `test_removing_the_blocking_term_flips_the_exit_code` mutates
    the one line that adds rollover to the blocking sum and asserts the exit
    code goes 1 -> 0. Delete the wiring and this test dies. That is the single
    most repeated failure here: a correct function nothing calls.
 2. FAIL CLOSED. Unreadable file, corrupt JSONL, unparseable id, missing
    corpus, unloadable rollover.py — every one is a blocking defect, never
    "clean".
 3. THE CHEAPEST PATH TO GREEN IS THE CORRECT ONE. Omitting the section,
    fencing it, lying in the summary, understating a gap, and carpet-bombing
    the table with every channel are all tested and all cost more than telling
    the truth, which is one command.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
V37 = os.path.join(SKILLS, "v37", "tools")
ROLLOVER = os.path.join(V37, "rollover.py")
CITECHECK = os.path.join(V37, "citecheck.py")

sys.path.insert(0, V37)
import importlib.util


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ro = _load("_t_rollover", ROLLOVER)
cc = _load("_t_citecheck", CITECHECK)


def rec(channel, rid, note="benign heartbeat entry"):
    return json.dumps({"Event": {"System": {"Channel": channel,
                                            "EventRecordID": rid,
                                            "EventID": 4624},
                                 "EventData": {"note": note}}},
                      ensure_ascii=False, separators=(",", ":"))


def write(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


# --------------------------------------------------------------- the scan ---
class ScanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rollover-scan-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def one(self, name, lines):
        p = os.path.join(self.dir, name)
        write(p, lines)
        out = ro.scan_file(p, name)
        self.assertEqual(len(out), 1, out)
        return out[0]

    def test_anchor_shape_is_contiguous(self):
        """402275…437190 over 34 916 rows is NOT a gap. This is the anchor."""
        p = os.path.join(self.dir, "Security.jsonl")
        write(p, [rec("Security", i) for i in range(402275, 437191)])
        e = ro.scan_file(p, "Security.jsonl")[0]
        self.assertEqual((e["lo"], e["hi"]), (402275, 437190))
        self.assertEqual(e["hi"] - e["lo"] + 1, 34916)
        self.assertEqual(e["records"], 34916)
        self.assertEqual(e["missing"], 0)
        self.assertEqual(e["status"], ro.OK)

    def test_real_gap_is_detected_with_the_right_count(self):
        e = self.one("Gapped.jsonl",
                     [rec("Gapped", i) for i in
                      list(range(100, 130)) + list(range(150, 200))])
        self.assertEqual(e["status"], ro.GAP)
        self.assertEqual((e["lo"], e["hi"], e["records"]), (100, 199, 80))
        self.assertEqual(e["missing"], 20)

    def test_channels_never_share_a_span(self):
        """Two channels in one file: their ids interleave, spans do not merge."""
        p = os.path.join(self.dir, "Mixed.jsonl")
        write(p, [rec("A/Op", i) for i in range(1, 11)]
              + [rec("B/Op", i) for i in (1, 2, 9, 10)])
        got = {e["channel"]: e for e in ro.scan_file(p, "Mixed.jsonl")}
        self.assertEqual(got["A/Op"]["missing"], 0)
        self.assertEqual(got["B/Op"]["missing"], 6)

    def test_single_record_is_contiguous(self):
        e = self.one("Single.jsonl", [rec("Single", 7)])
        self.assertEqual((e["status"], e["lo"], e["hi"], e["missing"]),
                         (ro.OK, 7, 7, 0))

    def test_string_ids_are_integers(self):
        e = self.one("Str.jsonl", [
            json.dumps({"Event": {"System": {"Channel": "S",
                                             "EventRecordID": str(i)}}})
            for i in range(1, 6)])
        self.assertEqual((e["status"], e["records"], e["missing"]), (ro.OK, 5, 0))

    def test_empty_file_is_not_applicable(self):
        p = os.path.join(self.dir, "Empty.jsonl")
        open(p, "w").close()
        self.assertEqual(ro.scan_file(p, "Empty.jsonl")[0]["status"], ro.NA)

    def test_not_jsonl_is_not_applicable(self):
        e = self.one("notes.txt", ["plain text line %d" % i for i in range(40)])
        self.assertEqual(e["status"], ro.NA)
        self.assertIn("не-jsonl", e["detail"])

    def test_binary_is_not_applicable(self):
        p = os.path.join(self.dir, "bin.dat")
        with open(p, "wb") as fh:
            fh.write(b"\x00\x01\x02" * 32)
        self.assertEqual(ro.scan_file(p, "bin.dat")[0]["status"], ro.NA)

    def test_missing_field_is_not_applicable(self):
        e = self.one("NoId.jsonl", [json.dumps({"msg": "hello", "ts": i})
                                    for i in range(5)])
        self.assertEqual(e["status"], ro.NA)
        self.assertIn("EventRecordID", e["detail"])

    # ---- fail closed -----------------------------------------------------
    def test_unparseable_id_fails_closed(self):
        e = self.one("BadId.jsonl", [
            json.dumps({"Event": {"System": {"Channel": "B",
                                             "EventRecordID": "не-число"}}})])
        self.assertEqual(e["status"], ro.ERR)

    def test_corrupt_jsonl_after_a_good_record_fails_closed(self):
        e = self.one("Corrupt.jsonl", [rec("C", 1), "{broken"])
        self.assertEqual(e["status"], ro.ERR)
        self.assertIn("json", e["detail"])

    def test_unreadable_file_fails_closed(self):
        """A directory and a dangling symlink are unreadable for every uid.

        chmod 000 is useless here: the suite may run as root, and a check that
        only fails for non-root is a check that passes in CI by accident.
        """
        d = os.path.join(self.dir, "adir.jsonl")
        os.mkdir(d)
        self.assertEqual(ro.scan_file(d, "adir.jsonl")[0]["status"], ro.ERR)
        link = os.path.join(self.dir, "dangling.jsonl")
        os.symlink(os.path.join(self.dir, "nope"), link)
        self.assertEqual(ro.scan_file(link, "dangling.jsonl")[0]["status"], ro.ERR)

    def test_injected_exception_fails_closed(self):
        p = os.path.join(self.dir, "Boom.jsonl")
        write(p, [rec("Boom", 1)])
        original = ro._channel
        ro._channel = lambda obj: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            e = ro.scan_file(p, "Boom.jsonl")[0]
        finally:
            ro._channel = original
        self.assertEqual(e["status"], ro.ERR)
        self.assertIn("исключение", e["detail"])

    def test_an_error_never_disappears_from_the_corpus_summary(self):
        write(os.path.join(self.dir, "ok.jsonl"), [rec("K", 1)])
        write(os.path.join(self.dir, "bad.jsonl"), [rec("K", 1), "{broken"])
        scan = ro.scan_corpus(self.dir)
        self.assertEqual(scan["errors"], 1)
        self.assertEqual(scan["contiguous"], 1)


# ------------------------------------------------------------- the report ---
FINDINGS = """# Находки

### Н-1 · Установка службы 3proxy

**что сломано:** установлена служба 3proxy tiny proxy server.

**улики:**

- a.jsonl:1 — «"ServiceName":"3proxy tiny proxy server"»

**атрибуция: установлена**

**исход: успех**

# Отклонённые кандидаты

### К-1 · Обычный вход

**исход: норма**

- a.jsonl:2 — «"note":"benign heartbeat entry"»

# Покрытие

| a.jsonl | наблюдение | a.jsonl:1 «"ServiceName":"3proxy tiny proxy server"» |

# ВЕРДИКТ

скомпрометирована
"""

GOOD_SECTION = """
# Окно записей

итог: файлов=1 каналов=1 сплошных=1 с-пропусками=0 неприменимо=0 ошибок=0

| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |
"""


def one_file_corpus(root):
    os.makedirs(root, exist_ok=True)
    write(os.path.join(root, "a.jsonl"), [
        json.dumps({"Event": {"System": {"Channel": "Alpha", "EventRecordID": 1,
                                         "EventID": 7045},
                              "EventData": {"ServiceName":
                                            "3proxy tiny proxy server"}}},
                   ensure_ascii=False, separators=(",", ":")),
        rec("Alpha", 2)])


class GateTest(unittest.TestCase):
    """The gate re-derives the truth from the corpus; the report only declares."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rollover-gate-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.corpus = os.path.join(self.dir, "corpus")
        one_file_corpus(self.corpus)

    def verdict(self, report, cited=("a.jsonl",)):
        return cc.rollover_evidence(report, self.corpus, list(cited))

    def test_the_only_defect_in_the_baseline_report_is_the_missing_section(self):
        d = cc.check(FINDINGS, self.corpus, require_quote=True)
        d["outcomes"] = cc.outcomes_of(FINDINGS)
        ev = cc.report_evidence(FINDINGS, d)
        self.assertEqual(ev["blocking"], ev["rollover"]["blocking"])
        self.assertGreater(ev["rollover"]["blocking"], 0)

    def test_declaring_the_window_correctly_is_green(self):
        v = self.verdict(FINDINGS + GOOD_SECTION)
        self.assertEqual(v["blocking"], 0, cc.render_rollover(v))

    def test_missing_section_blocks_and_names_every_owed_window(self):
        v = self.verdict(FINDINGS)
        self.assertTrue(v["missing_section"])
        self.assertEqual(v["undeclared"], ["a.jsonl | Alpha"])
        self.assertGreaterEqual(v["blocking"], 2)

    def test_a_contiguous_finding_channel_must_still_be_declared(self):
        """The anchor rule: «нет пропусков» is a claim, and it must be stated."""
        v = self.verdict(FINDINGS + "\n# Окно записей\n\n"
                         "итог: файлов=1 каналов=1 сплошных=1 с-пропусками=0 "
                         "неприменимо=0 ошибок=0\n")
        self.assertEqual(v["undeclared"], ["a.jsonl | Alpha"])
        self.assertGreater(v["blocking"], 0)

    def test_a_channel_with_no_finding_and_no_gap_is_not_owed(self):
        """The check does not demand a second full-corpus table."""
        write(os.path.join(self.corpus, "b.jsonl"), [rec("Beta", i)
                                                     for i in range(1, 4)])
        good = FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=2 каналов=2 сплошных=2 с-пропусками=0 неприменимо=0 "
            "ошибок=0\n\n| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n")
        v = self.verdict(good)
        self.assertEqual(v["blocking"], 0, cc.render_rollover(v))

    # ---- gaming attempts -------------------------------------------------
    def test_game_hiding_the_section_in_a_code_fence(self):
        v = self.verdict(FINDINGS + "\n```\n" + GOOD_SECTION + "\n```\n")
        self.assertTrue(v["missing_section"])
        self.assertGreater(v["blocking"], 0)

    def test_game_empty_section(self):
        v = self.verdict(FINDINGS + "\n# Окно записей\n\nничего не пропало\n")
        self.assertTrue(v["summary_missing"])
        self.assertEqual(v["undeclared"], ["a.jsonl | Alpha"])

    def test_game_lying_in_the_summary(self):
        write(os.path.join(self.corpus, "g.jsonl"),
              [rec("Gap", i) for i in (1, 2, 9, 10)])
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=2 каналов=2 сплошных=2 с-пропусками=0 неприменимо=0 "
            "ошибок=0\n\n| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n"))
        self.assertTrue(v["summary_mismatch"])
        self.assertIn("g.jsonl | Gap", v["undeclared"])

    def test_game_understating_a_real_gap(self):
        write(os.path.join(self.corpus, "g.jsonl"),
              [rec("Gap", i) for i in (1, 2, 9, 10)])
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=2 каналов=2 сплошных=1 с-пропусками=1 неприменимо=0 "
            "ошибок=0\n\n| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n"
            "| g.jsonl | Gap | окно=1–10 | записей=4 | нет=0 |\n"))
        self.assertEqual(len(v["wrong"]), 1, v)
        self.assertIn("нет=6", v["wrong"][0]["на диске"])
        self.assertGreater(v["blocking"], 0)

    def test_game_carpet_bombing_every_channel_as_gapped(self):
        """Declaring everything «gapped» to be safe costs, it does not save."""
        write(os.path.join(self.corpus, "b.jsonl"), [rec("Beta", i)
                                                     for i in range(1, 4)])
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=2 каналов=2 сплошных=2 с-пропусками=0 неприменимо=0 "
            "ошибок=0\n\n| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n"
            "| b.jsonl | Beta | окно=1–3 | записей=1 | нет=2 |\n"))
        self.assertEqual(len(v["wrong"]) + len(v["spurious"]), 1, v)
        self.assertGreater(v["blocking"], 0)

    def test_game_two_summary_lines(self):
        v = self.verdict(FINDINGS + GOOD_SECTION
                         + "\nитог: файлов=9 каналов=9 сплошных=9 "
                           "с-пропусками=0 неприменимо=0 ошибок=0\n")
        self.assertTrue(v["summary_duplicate"])
        self.assertGreater(v["blocking"], 0)

    def test_game_two_rows_for_one_channel(self):
        v = self.verdict(FINDINGS + GOOD_SECTION
                         + "| a.jsonl | Alpha | окно=1–99 | записей=1 | нет=98 |\n")
        self.assertEqual(v["duplicate_rows"], ["a.jsonl | Alpha"])
        self.assertGreater(v["blocking"], 0)

    def test_game_prose_instead_of_the_row_grammar(self):
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=1 каналов=1 сплошных=1 с-пропусками=0 неприменимо=0 "
            "ошибок=0\n\n| a.jsonl | Alpha | всё в порядке |\n"))
        self.assertEqual(v["malformed"], [len((FINDINGS + (
            "\n# Окно записей\n\nитог: файлов=1 каналов=1 сплошных=1 "
            "с-пропусками=0 неприменимо=0 ошибок=0\n\n")).splitlines()) + 1])
        self.assertGreater(v["blocking"], 0)

    def test_thousand_separators_are_the_same_number(self):
        """«1 234» and «1234» are the same claim; formatting is not a defect."""
        p = os.path.join(self.corpus, "big.jsonl")
        write(p, [rec("Big", i) for i in range(1, 1235) if not 500 <= i < 510])
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=2 каналов=2 сплошных=1 с-пропусками=1 неприменимо=0 "
            "ошибок=0\n\n| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n"
            "| big.jsonl | Big | окно=1–1\u00a0234 | записей=1 224 | нет=10 |\n"))
        self.assertEqual(v["blocking"], 0, cc.render_rollover(v))

    # ---- fail closed at the gate ----------------------------------------
    def test_a_scan_error_blocks_even_with_a_perfect_section(self):
        write(os.path.join(self.corpus, "bad.jsonl"), [rec("K", 1), "{broken"])
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=2 каналов=1 сплошных=1 с-пропусками=0 неприменимо=0 "
            "ошибок=1\n\n| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n"))
        self.assertEqual(len(v["scan_errors"]), 1, v)
        self.assertGreaterEqual(v["blocking"], 1)

    def test_missing_corpus_fails_closed(self):
        v = cc.rollover_evidence(FINDINGS + GOOD_SECTION,
                                 os.path.join(self.dir, "nope"), [])
        self.assertTrue(v["scan_failed"])
        self.assertEqual(v["blocking"], 1)

    def test_unloadable_rollover_module_fails_closed(self):
        tools = os.path.join(self.dir, "tools")
        shutil.copytree(V37, tools)
        with open(os.path.join(tools, "rollover.py"), "w") as fh:
            fh.write("def scan_corpus(:\n")            # syntax error on purpose
        broken = _load("_t_broken_cc", os.path.join(tools, "citecheck.py"))
        v = broken.rollover_evidence(FINDINGS + GOOD_SECTION, self.corpus, [])
        self.assertTrue(v["scan_failed"])
        self.assertEqual(v["blocking"], 1)


# --------------------------------------------------------------- the wiring --
class WiringTest(unittest.TestCase):
    """Constraint 4: prove the contribution reaches the EXIT CODE.

    The baseline report is clean in every other respect — measured: its total
    `blocking` equals rollover's alone — so the exit code here is a pure test
    of this wiring. Delete `+ rollover["blocking"]` from the sum in
    report_evidence() and `test_removing_the_blocking_term_flips_the_exit_code`
    fails, because the mutant exits 0 on a report that owes a window.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rollover-wire-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.corpus = os.path.join(self.dir, "corpus")
        one_file_corpus(self.corpus)
        self.bad = os.path.join(self.dir, "report.md")
        with open(self.bad, "w", encoding="utf-8") as fh:
            fh.write(FINDINGS)
        self.good = os.path.join(self.dir, "report-ok.md")
        with open(self.good, "w", encoding="utf-8") as fh:
            fh.write(FINDINGS + GOOD_SECTION)

    def run_gate(self, tool, report, extra=()):
        return subprocess.run([sys.executable, tool, report, "--corpus",
                               self.corpus, "--require-quote"] + list(extra),
                              capture_output=True, text=True)

    def test_the_real_gate_blocks_an_undeclared_window(self):
        r = self.run_gate(CITECHECK, self.bad)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ОКНО ЗАПИСЕЙ", r.stdout)

    def test_the_real_gate_passes_a_declared_window(self):
        r = self.run_gate(CITECHECK, self.good)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_json_blocking_carries_the_rollover_defects(self):
        r = self.run_gate(CITECHECK, self.bad, ["--json"])
        d = json.loads(r.stdout)
        self.assertEqual(d["report_evidence"]["rollover"]["blocking"],
                         d["report_evidence"]["blocking"])
        self.assertEqual(d["blocking"], d["report_evidence"]["blocking"])
        self.assertGreater(d["blocking"], 0)

    def _mutant(self, old, new):
        tools = os.path.join(self.dir, "mutant-%d" % abs(hash(new)))
        shutil.copytree(V37, tools)
        p = os.path.join(tools, "citecheck.py")
        with open(p, encoding="utf-8") as fh:
            s = fh.read()
        self.assertEqual(s.count(old), 1,
                         "the wiring line moved: %r" % old)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(s.replace(old, new))
        return p

    def test_removing_the_blocking_term_flips_the_exit_code(self):
        """THE mutation. Original exits 1, mutant exits 0 — so the term is live."""
        self.assertEqual(self.run_gate(CITECHECK, self.bad).returncode, 1)
        mutant = self._mutant('                + rollover["blocking"])',
                              '                + 0)')
        r = self.run_gate(mutant, self.bad)
        self.assertEqual(r.returncode, 0,
                         "the mutation changed nothing — the rollover term was "
                         "already dead on the real path:\n" + r.stdout)

    def test_never_calling_the_check_flips_the_exit_code(self):
        """A second mutation: stub the whole verdict out. Same proof, one layer up."""
        mutant = self._mutant(
            "    rollover = rollover_evidence(report, corpus, sorted(cited_by_findings),\n"
            "                                 structural, sections)",
            "    rollover = {'blocking': 0}")
        r = self.run_gate(mutant, self.bad)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
