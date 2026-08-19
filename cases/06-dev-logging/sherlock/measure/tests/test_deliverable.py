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



# --------------------------------------------------------------------------
# THE UNION IS A UNION, NOT A CONCATENATION
# --------------------------------------------------------------------------
# The module always said UNION. The code concatenated. On all six arms scored on
# 2026-08-18 both channels carried the SAME report, so every citation in every
# published total was counted twice: the six reports scored as files give
# 147 / 141 / 106 / 157 / 158 / 198 and the published composed totals were
# 294 / 268 / 212 / 314 / 316 / 396. The *rate* is unaffected (158/158 and
# 316/316 are both 100 %), which is why it hid for so long.
#
# The two channels are never byte-identical: `work/report.md` is hard-wrapped and
# the final message is not, and three arms opened the message with a preamble
# («Отчёт целиком:») the file has no line for. So equality is not the test. The
# unit is the BLOCK — a paragraph, or a fenced code block — normalised for
# whitespace, which is exactly the unit that re-wrapping preserves.
#
# What this must NOT do is silently pick one channel. AIT v16-contaminated
# answered a CONDENSED rewrite beside its file (`…access.log.2:5315` in the
# message where the file wrote the whole path), and those two channels really do
# say different things. Both are kept, and `duplication()` says so out loud.
class TheUnionCountsOneReportOnce(unittest.TestCase):

    def test_the_same_report_on_both_channels_composes_to_ONE_copy(self):
        """The whole defect, in four lines. Two channels, one report, one count."""
        rep = "# Отчёт\n\n## Находки\n\napp/a.log:10 «boom»\n"
        out = D.compose(rep, rep)
        self.assertEqual(out.count("app/a.log:10"), 1,
                         "a report handed over twice is still one report")
        self.assertEqual(out.count("## Находки"), 1)

    def test_re_wrapping_the_file_does_not_make_it_a_second_report(self):
        """The real shape: `work/report.md` is hard-wrapped, the message is not.
        Byte equality was never going to catch this — 0 of the 6 measured arms
        are byte-identical and only 1 is identical modulo whitespace."""
        msg = "# Отчёт\n\nОдин очень длинный абзац про app/a.log:10 «boom» и всё.\n"
        fil = "# Отчёт\n\nОдин очень длинный абзац про app/a.log:10\n«boom» и\nвсё.\n"
        self.assertNotEqual(msg, fil)
        out = D.compose(msg, fil)
        self.assertEqual(out.count("app/a.log:10"), 1)

    def test_a_preamble_in_the_message_is_kept_and_costs_nothing(self):
        """Three arms said «Отчёт целиком:» before pasting the report. That line
        is content the file does not have; it must survive, and it must not drag
        the whole report in behind it."""
        rep = "# Отчёт\n\napp/a.log:10 «boom»\n"
        out = D.compose("Отчёт целиком:\n\n" + rep, rep)
        self.assertIn("Отчёт целиком:", out)
        self.assertEqual(out.count("app/a.log:10"), 1)

    def test_channels_that_genuinely_differ_keep_BOTH(self):
        """The reason this module exists is that a channel can carry a finding the
        other does not. De-duplication must never cost one of those."""
        out = D.compose("# Отчёт\n\ncheckout.log:12 NPE\n",
                        "# Отчёт\n\npayments.log:9 panic\n")
        self.assertIn("checkout.log:12", out)
        self.assertIn("payments.log:9", out)

    def test_a_block_repeated_INSIDE_one_channel_is_left_alone(self):
        """De-duplication is between channels, not inside one. A report that
        writes the same row twice wrote it twice, and that is the report's fact."""
        msg = "app/a.log:10 «boom»\n\napp/a.log:10 «boom»\n"
        self.assertEqual(D.compose(msg, ""), msg)
        self.assertEqual(D.compose(msg, msg).count("app/a.log:10"), 2)

    def test_a_fenced_block_is_one_block_even_with_a_blank_line_in_it(self):
        """A ``` fence containing a blank line must not split into two blocks: the
        closing fence would then be a bare ``` block, and a bare ``` matches every
        other bare ``` in the other channel — dropping it unbalances the fence and
        turns the next heading into sample text for `score-report.py`'s parser."""
        fenced = "# Отчёт\n\n```\nline one\n\nline two\n```\n\n## Находки\n\nx.log:1 «y»\n"
        other = "# Другое\n\n```\nline one\n\nline three\n```\n"
        out = D.compose(fenced, other)
        self.assertEqual(out.count("```"), 4, "both fences stay balanced")
        self.assertIn("line three", out)


class DivergentChannelsAreFlaggedNotCollapsed(unittest.TestCase):
    """«If they differ, that is worth a warning in the record, not a silent pick.»"""

    def test_identical_channels_are_named_identical_and_warn_about_nothing(self):
        rep = "# Отчёт\n\napp/a.log:10 «boom»\n"
        d = D.duplication(rep, rep)
        self.assertEqual(d["relation"], "identical")
        self.assertIsNone(d["warning"])
        self.assertEqual(d["only_in_message"], 0)
        self.assertEqual(d["only_in_file"], 0)

    def test_a_file_that_repeats_the_message_is_named_that(self):
        """Four of the six arms: the file adds not one block. fleet-negative,
        BlueSky v16, BlueSky v19, AIT v19."""
        rep = "# Отчёт\n\napp/a.log:10 «boom»\n"
        d = D.duplication("Отчёт целиком:\n\n" + rep, rep)
        self.assertEqual(d["relation"], "file-repeats-message")
        self.assertIsNone(d["warning"])
        self.assertEqual(d["only_in_file"], 0)
        self.assertEqual(d["only_in_message"], 1)

    def test_a_message_that_repeats_the_file_is_named_that(self):
        """The collapsed-run shape, once the stub grows into a real excerpt."""
        rep = "# Отчёт\n\napp/a.log:10 «boom»\n\n## Ещё\n\napp/b.log:2 «x»\n"
        d = D.duplication("# Отчёт\n\napp/a.log:10 «boom»\n", rep)
        self.assertEqual(d["relation"], "message-repeats-file")
        self.assertIsNone(d["warning"])
        self.assertEqual(d["only_in_message"], 0)

    def test_genuinely_different_channels_RAISE_A_WARNING(self):
        """AIT v16-contaminated: 34 blocks only in the message, 34 only in the
        file, because the message is a condensed rewrite. Its two channels are
        NOT one report and the record has to say so."""
        d = D.duplication("# Отчёт\n\ncheckout.log:12 NPE\n",
                          "# Отчёт\n\npayments.log:9 panic\n")
        self.assertEqual(d["relation"], "divergent")
        self.assertEqual(d["only_in_message"], 1)
        self.assertEqual(d["only_in_file"], 1)
        self.assertIsNotNone(d["warning"])
        self.assertIn("1", d["warning"])

    def test_one_channel_runs_are_named_by_the_channel_they_used(self):
        self.assertEqual(D.duplication("msg", "")["relation"], "message-only")
        self.assertEqual(D.duplication("", "file")["relation"], "file-only")
        self.assertEqual(D.duplication("", "")["relation"], "none")
        for a, r in (("msg", ""), ("", "file"), ("", "")):
            self.assertIsNone(D.duplication(a, r)["warning"],
                              "one channel cannot disagree with itself")

    def test_a_row_is_read_through_the_same_one_definition(self):
        row = {"answer": "# Отчёт\n\nx.log:1 «y»\n",
               "artifact": "# Отчёт\n\nx.log:1 «y»\n"}
        self.assertEqual(D.duplication_of_row(row),
                         D.duplication(row["answer"], row["artifact"]))
        self.assertEqual(D.duplication_of_row({"answer": "old row"})["relation"],
                         "message-only")
        self.assertEqual(D.duplication_of_row({})["relation"], "none")



# --------------------------------------------------------------------------
# The PARTS, not only the union — 2026-08-19
# --------------------------------------------------------------------------
# The v22 negative control passed `citecheck` 110/110 on `work/report.md` and
# handed over a final message that scored 74/95 with 21 `wrong-content`, all
# inside a condensed inventory it hand-wrote AFTER checking the draft. The
# composed number is 198 citations at 89.4 % — an average that describes neither
# document. To score the channels separately a reader needs the parts, and the
# parts must come from the same module the union comes from: a scorer that reads
# `row["answer"]` itself is a second definition of "which field is which
# channel", and that is exactly how one measurement becomes two scales.
class TheChannelsAreReadableSeparately(unittest.TestCase):

    def test_both_channels_are_returned_under_the_names_channel_uses(self):
        got = D.channels("msg", "file")
        self.assertEqual(sorted(got), ["file", "message"])
        self.assertEqual(got["message"], "msg")
        self.assertEqual(got["file"], "file")

    def test_the_names_are_the_same_vocabulary_as_channel(self):
        """`channel()` already answers message|file|both|none. The parts must not
        invent a third word for the same two things."""
        self.assertEqual(set(D.channels("a", "b")), {"message", "file"})
        self.assertEqual(D.channel("a", ""), "message")
        self.assertEqual(D.channel("", "b"), "file")

    def test_an_absent_channel_is_absent_not_empty(self):
        """A run that never wrote a file has ONE channel. An empty string entry
        would make a scorer grade a document nobody produced and report 0
        citations, which reads as a report that cited nothing."""
        self.assertEqual(list(D.channels("msg", "")), ["message"])
        self.assertEqual(list(D.channels("msg", None)), ["message"])
        self.assertEqual(list(D.channels("msg", "  \n ")), ["message"])
        self.assertEqual(list(D.channels("", "file")), ["file"])
        self.assertEqual(D.channels("", ""), {})

    def test_a_row_is_split_through_the_same_one_definition(self):
        row = {"answer": "msg", "artifact": "file"}
        self.assertEqual(D.channels_of_row(row), {"message": "msg", "file": "file"})
        self.assertEqual(D.channels_of_row({"answer": "msg"}), {"message": "msg"})
        self.assertEqual(D.channels_of_row({}), {})

    def test_the_parts_recompose_to_the_union(self):
        """The one invariant that keeps the parts honest: nothing in either part
        is invented, and the union of the parts is what `compose` returns."""
        msg = "# Отчёт\n\napp/a.log:1 «x»"
        fil = "# Отчёт\n\napp/b.log:2 «y»"
        parts = D.channels(msg, fil)
        union = D.compose(msg, fil)
        for text in parts.values():
            for b in D.blocks(text):
                self.assertIn(" ".join(b.split()),
                              [" ".join(u.split()) for u in D.blocks(union)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
