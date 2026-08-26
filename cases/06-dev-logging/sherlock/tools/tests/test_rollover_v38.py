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
V38 = os.path.join(SKILLS, "v38", "tools")
ROLLOVER = os.path.join(V38, "rollover.py")
CITECHECK = os.path.join(V38, "citecheck.py")

sys.path.insert(0, V38)
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

    def test_a_wrong_count_in_the_summary_is_the_whole_defect(self):
        """summary_mismatch alone must block — the table here is correct."""
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=1 каналов=1 сплошных=1 с-пропусками=0 неприменимо=3 "
            "ошибок=0\n\n| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n"))
        self.assertEqual(v["undeclared"], [])
        self.assertEqual(v["wrong"], [])
        self.assertEqual(v["malformed"], [])
        self.assertTrue(v["summary_mismatch"])
        self.assertEqual(v["blocking"], 1, cc.render_rollover(v))

    def test_a_fenced_summary_line_does_not_discharge_the_section(self):
        """Hiding «итог:» in a code fence is the same as not writing it."""
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n```\n"
            "итог: файлов=1 каналов=1 сплошных=1 с-пропусками=0 неприменимо=0 "
            "ошибок=0\n```\n\n| a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n"))
        self.assertTrue(v["summary_missing"], cc.render_rollover(v))
        self.assertGreater(v["blocking"], 0)

    def test_a_dot_slash_path_is_the_same_row(self):
        """«./a.jsonl» and «a.jsonl» are one channel, not two."""
        v = self.verdict(FINDINGS + (
            "\n# Окно записей\n\n"
            "итог: файлов=1 каналов=1 сплошных=1 с-пропусками=0 неприменимо=0 "
            "ошибок=0\n\n| ./a.jsonl | Alpha | окно=1–2 | записей=2 | нет=0 |\n"))
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
        shutil.copytree(V38, tools)
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
        base = os.path.join(self.dir, "mutant-%d" % abs(hash(new)))
        tools = os.path.join(base, "tools")
        shutil.copytree(V38, tools)
        # The enum gate (fix 6a) reads `tools/../reference/enum-tables.tsv` and
        # FAILS CLOSED when it is missing, so a mutant tree without it blocks for
        # the wrong reason and every mutation looks alive. Copy the whole skill.
        shutil.copytree(os.path.join(os.path.dirname(V38), "reference"),
                        os.path.join(base, "reference"))
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

    # ---- every term of the rollover blocking sum, pinned by mutation --------
    # Two of them used to be free: `malformed` and `summary_missing` could each
    # be deleted from the sum and all 36 tests stayed green. `malformed` is the
    # ONLY thing between the gate and a rollover table full of unparseable rows.
    def _report(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_malformed_rows_are_live_in_the_blocking_sum(self):
        """A declared section plus one unparseable row must still block."""
        bad = self._report("report-malformed.md", FINDINGS + GOOD_SECTION
                           + "| Security.jsonl | Security | мусор |\n")
        self.assertEqual(self.run_gate(CITECHECK, bad).returncode, 1)
        mutant = self._mutant('+ len(out["malformed"]) + len(out["scan_errors"]))',
                              '+ 0 + len(out["scan_errors"]))')
        r = self.run_gate(mutant, bad)
        self.assertEqual(r.returncode, 0,
                         "dropping `malformed` from the sum changed nothing — "
                         "the term was already dead:\n" + r.stdout)

    def test_summary_missing_is_live_in_the_blocking_sum(self):
        """A section with a table but no «итог:» line must still block."""
        itog = [l for l in GOOD_SECTION.splitlines() if l.startswith("итог")][0]
        bad = self._report("report-no-itog.md",
                           FINDINGS + GOOD_SECTION.replace(itog + "\n", ""))
        self.assertEqual(self.run_gate(CITECHECK, bad).returncode, 1)
        mutant = self._mutant('out["blocking"] = (int(out["summary_missing"])',
                              'out["blocking"] = (0 * int(out["summary_missing"])')
        r = self.run_gate(mutant, bad)
        self.assertEqual(r.returncode, 0,
                         "zeroing `summary_missing` changed nothing — the term "
                         "was already dead:\n" + r.stdout)

    def test_never_calling_the_check_flips_the_exit_code(self):
        """A second mutation: stub the whole verdict out. Same proof, one layer up."""
        mutant = self._mutant(
            "    rollover = rollover_evidence(report, corpus, sorted(cited_by_findings),\n"
            "                                 structural, sections)",
            "    rollover = {'blocking': 0}")
        r = self.run_gate(mutant, self.bad)
        self.assertEqual(r.returncode, 0, r.stdout)


# ----------------------------------------------- the section regex is NARROW --
class SectionRegexTest(unittest.TestCase):
    """«Ротация журналов» is an ordinary FINDING title in a log-analysis case.

    The regex is matched against section TITLES, so the `rollover|ротаци`
    aliases that used to be in it turned any such section into a rollover span
    and read every `|`-line in it as a rollover row. The arm's only escape was
    renaming its finding. One spelling — the one `rollover.py --report` emits.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rollover-re-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.corpus = os.path.join(self.dir, "corpus")
        one_file_corpus(self.corpus)

    def test_the_regex_matches_only_the_one_spelling(self):
        self.assertTrue(cc.ROLLOVER_SECTION_RE.search("Окно записей"))
        for alias in ("Ротация журналов", "rollover policy", "Ротация"):
            self.assertIsNone(cc.ROLLOVER_SECTION_RE.search(alias),
                              "%r must NOT be read as the rollover section" % alias)

    def test_an_ordinary_rotation_finding_does_not_hijack_the_gate(self):
        report = (FINDINGS + GOOD_SECTION
                  + "\n# Ротация журналов\n\n| параметр | значение |\n"
                    "| --- | --- |\n| Security MaxSize | 20 MB |\n")
        v = cc.rollover_evidence(report, self.corpus, ["a.jsonl"])
        self.assertEqual(v["malformed"], [], cc.render_rollover(v))
        self.assertEqual(v["blocking"], 0, cc.render_rollover(v))


# ------------------------------- a channel name is ADVERSARY-CONTROLLED text --
class CellEscapeTest(unittest.TestCase):
    """A `|` in a channel name used to wedge the run, permanently.

    `row_for` interpolated the channel straight into a markdown row, so the
    grader read the row back as a DIFFERENT channel and the required key could
    never match: `undeclared` + `spurious` on the tool's own output, with no
    report the producer could emit to clear it. It failed closed, so it hid
    nothing — it just made the corpus ungradeable.
    """

    HOSTILE = "Ev | X | окно=1–999 | записей=999 | нет=0"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rollover-esc-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.corpus = os.path.join(self.dir, "corpus")
        os.makedirs(self.corpus)
        write(os.path.join(self.corpus, "p.jsonl"),
              [rec(self.HOSTILE, i) for i in (1, 2, 9, 10)])

    def test_the_row_stays_a_five_cell_row(self):
        e = ro.scan_corpus(self.corpus)["entries"][0]
        row = ro.row_for(e)
        cells = cc.split_cells(row)
        self.assertEqual(len(cells), 5, row)
        self.assertEqual(cells[1], ro.cell_safe(self.HOSTILE))
        self.assertNotIn("|", cells[1])

    def test_the_tools_own_output_grades_clean(self):
        """The producer's output must satisfy the grader. It could not before."""
        scan = ro.scan_corpus(self.corpus)
        report = (FINDINGS + "\n# Окно записей\n\n"
                  + ro.render(scan, True, []) + "\n")
        v = cc.rollover_evidence(report, self.corpus, [])
        self.assertEqual(v["blocking"], 0, cc.render_rollover(v))

    def test_the_escape_is_injective(self):
        """Two different channels must never collapse onto one key."""
        self.assertNotEqual(ro.key_of("a", "X|Y"), ro.key_of("a", "X\\7cY"))
        self.assertNotEqual(ro.key_of("a", "X\nY"), ro.key_of("a", "X\\0aY"))

    def test_a_newline_in_a_channel_cannot_forge_extra_rows(self):
        row = ro.row_for({"path": "p.jsonl", "channel": "A\nB", "lo": 1,
                          "hi": 1, "records": 1, "missing": 0})
        self.assertEqual(len(row.splitlines()), 1, row)


# ---------------------------------------------- nothing may vanish in silence --
class SilenceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rollover-silent-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_channel_with_records_but_no_id_is_reported(self):
        """B has 1 000 records and no EventRecordID. It used to be dropped."""
        p = os.path.join(self.dir, "m.jsonl")
        write(p, [rec("A", i) for i in range(1, 11)]
              + [json.dumps({"Event": {"System": {"Channel": "B",
                                                  "EventID": 4624}}})] * 1000)
        got = {e["channel"]: e for e in ro.scan_file(p, "m.jsonl")}
        self.assertIn("B", got)
        self.assertEqual(got["B"]["status"], ro.NA)
        self.assertEqual(got["B"]["detail"], "поле=нет-EventRecordID")
        self.assertEqual(got["B"]["rows"], 1000)
        self.assertEqual(got["A"]["status"], ro.OK)

    def test_a_symlinked_directory_inside_the_corpus_is_walked(self):
        corpus = os.path.join(self.dir, "corpus")
        real = os.path.join(self.dir, "elsewhere")
        os.makedirs(corpus)
        os.makedirs(real)
        write(os.path.join(real, "s.jsonl"), [rec("Sym", i) for i in (1, 2, 9)])
        os.symlink(real, os.path.join(corpus, "linked"))
        scan = ro.scan_corpus(corpus)
        self.assertEqual(scan["files"], 1, scan["entries"])
        self.assertEqual([e["channel"] for e in scan["entries"]], ["Sym"])

    def test_a_symlink_loop_does_not_hang_the_walk(self):
        corpus = os.path.join(self.dir, "loop")
        os.makedirs(os.path.join(corpus, "sub"))
        write(os.path.join(corpus, "sub", "s.jsonl"), [rec("L", 1)])
        os.symlink(corpus, os.path.join(corpus, "sub", "up"))
        self.assertGreaterEqual(ro.scan_corpus(corpus)["files"], 1)

    def test_a_gzipped_jsonl_with_a_gap_is_not_neprimenimo(self):
        """A real .gz used to read as «формат=двоичный» — a gap hidden as N/A."""
        import gzip as _gz
        corpus = os.path.join(self.dir, "gz")
        os.makedirs(corpus)
        with _gz.open(os.path.join(corpus, "g.jsonl.gz"), "wt",
                      encoding="utf-8") as fh:
            for i in (1, 2, 9, 10):
                fh.write(rec("Gz", i) + "\n")
        e = ro.scan_corpus(corpus)["entries"][0]
        self.assertEqual(e["status"], ro.GAP)
        self.assertEqual((e["lo"], e["hi"], e["records"], e["missing"]),
                         (1, 10, 4, 6))

    def test_a_comment_banner_does_not_make_valid_jsonl_look_foreign(self):
        """25 header lines then real JSONL: the sniff budget must not run out."""
        p = os.path.join(self.dir, "banner.jsonl")
        write(p, ["# exported by some tool" for _ in range(25)]
              + [rec("Ban", i) for i in (1, 2, 9, 10)])
        e = ro.scan_file(p, "banner.jsonl")[0]
        self.assertEqual(e["status"], ro.GAP, e)
        self.assertEqual(e["missing"], 6)

    def test_a_space_is_a_thousands_separator_not_filler(self):
        self.assertEqual(ro._as_int("1 234"), 1234)
        self.assertEqual(ro._as_int("1\u00a0234"), 1234)
        self.assertIsNone(ro._as_int("1 0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
