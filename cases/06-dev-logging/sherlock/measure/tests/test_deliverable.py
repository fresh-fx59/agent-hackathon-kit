#!/usr/bin/env python3
"""Tests for measure/deliverable.py — what a run actually produced.

WHY THIS MODULE EXISTS, from the trajectory that forced it. On 2026-08-02 the
649 MB run (`eval/bench/runs/20260802T221034Z-v11`) said, in its own words:

    «**ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ!** 45/45 ссылок OK … Теперь финальный шаг —
      вывести отчёт полностью.»

and then called `read_file` on its own `work/report.md`. The report was
finished — 19,991 chars, `citecheck` 45/45 — and the final message was 101
chars. 18,758,431 input tokens recorded nothing.

The model did not disobey. It output the report. `read_file` puts the output in
a TOOL RESULT, and `--output-format json` surfaces only the `result` record, so
the harness could not see the channel the report was delivered on. **No wording
fixes this**: every phrasing of "output the report" is satisfiable by a tool the
model already has, which is why two instruction edits (`ebf39ca`, `6490599`)
both failed.

So the deliverable is the UNION of every channel, defined once here and imported
by both the runner that records it and the scorer that judges it — two copies is
how one measurement quietly becomes two scales.
→ [[measurement-artifacts-discipline]]
"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.dirname(HERE)
_spec = importlib.util.spec_from_file_location(
    "deliverable", os.path.join(MEASURE, "deliverable.py"))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)


class TheUnionKeepsBothChannels(unittest.TestCase):

    def test_both_channels_survive_composition(self):
        """Neither channel may be dropped: the message can carry a finding the
        file does not, and on the collapsed run the file carried all eleven."""
        out = D.compose("checkout.log:12 NPE", "# Отчёт\npayments.log:9 panic")
        self.assertIn("checkout.log:12", out)
        self.assertIn("payments.log:9", out)

    def test_a_message_only_run_is_byte_identical_to_its_answer(self):
        """Every row recorded before 2026-08-03 is message-only. If composition
        changed those by even a separator, the 0-of-11 baseline and the 7–8-of-11
        arm would stop being comparable to their own published numbers."""
        self.assertEqual(D.compose("just the answer", ""), "just the answer")
        self.assertEqual(D.compose("just the answer", None), "just the answer")
        self.assertEqual(D.compose("just the answer", "   \n "), "just the answer")

    def test_an_artifact_only_run_is_byte_identical_to_its_report(self):
        self.assertEqual(D.compose("", "# Отчёт\nx.log:1 y"), "# Отчёт\nx.log:1 y")
        self.assertEqual(D.compose(None, "# Отчёт\nx.log:1 y"), "# Отчёт\nx.log:1 y")

    def test_nothing_at_all_composes_to_nothing(self):
        """A run that produced neither channel must stay empty, so the scorer
        can refuse it instead of putting a delivery failure on the recall axis."""
        self.assertEqual(D.compose("", ""), "")
        self.assertEqual(D.compose(None, None), "")
        self.assertFalse(D.compose("  ", " \n ").strip())

    def test_the_two_channels_are_visibly_separated(self):
        """The judge reads the union as one document. An unmarked concatenation
        would let the report's last sentence run into the report's own title."""
        out = D.compose("итог", "# Отчёт")
        self.assertIn("report.md", out, "the seam must name where the file starts")
        self.assertLess(out.index("итог"), out.index("# Отчёт"),
                        "the final message comes first — it is what was said last")


class TheChannelIsRecordedNotInferredLater(unittest.TestCase):

    def test_no_file_is_message_delivery(self):
        self.assertEqual(D.channel("a full report, in chat", ""), "message")

    def test_a_full_report_beside_a_stub_message_is_file_delivery(self):
        """The real numbers from the collapsed run: 101 chars beside 19,991."""
        self.assertEqual(D.channel("Отчёт готов. Все проверки пройдены." * 3,
                                   "x" * 19991), "file")

    def test_a_report_delivered_on_both_channels_says_so(self):
        """Rep 2 (`20260802T153821Z`) answered 25,559 chars beside a 34,398-char
        file. That run did deliver, and must not be tarred as a collapse."""
        self.assertEqual(D.channel("x" * 25559, "y" * 34398), "both")

    def test_an_empty_message_beside_a_report_is_file_delivery(self):
        self.assertEqual(D.channel("", "y" * 100), "file")
        self.assertEqual(D.channel(None, "y" * 100), "file")

    def test_neither_channel_is_named_honestly(self):
        """`message` would claim a delivery that never happened."""
        self.assertEqual(D.channel("", ""), "none")
        self.assertEqual(D.channel(None, None), "none")


class ARowIsReadThroughOneDefinition(unittest.TestCase):

    def test_a_row_without_an_artifact_field_still_reads(self):
        """Thirteen rows predate the field entirely. `of_row` must not KeyError
        on them, or the whole ledger becomes unscoreable at once."""
        self.assertEqual(D.of_row({"answer": "old row"}), "old row")

    def test_a_row_with_both_reads_as_the_union(self):
        row = {"answer": "msg", "artifact": "file body"}
        self.assertEqual(D.of_row(row), D.compose("msg", "file body"))

    def test_a_row_with_neither_reads_as_empty(self):
        self.assertEqual(D.of_row({}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
