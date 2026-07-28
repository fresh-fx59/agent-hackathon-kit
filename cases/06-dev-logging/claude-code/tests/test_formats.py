import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # for `logalyzer` when verify.sh runs this file standalone
import unittest, tempfile, json
from pathlib import Path
from logalyzer.masking import Masker
from logalyzer.formats import (fingerprint, FormatStore, validate_descriptor,
                               apply_descriptor, top_skeletons)

# The "alien" fixture from the design doc: a dialect the built-in heuristic
# timestamp bank cannot recognize (day/month-name/year order, timestamp
# mid-line, custom key=value framing) -- exactly the kind of format that
# should trip needs_inference and be solved via the register-format
# handshake instead.
ALIEN_LINES = [
    "level=W ts=[28/Jul/2026 15:08:38.903] svc=inventory :: reservation queue backing up",
    "level=I ts=[28/Jul/2026 15:08:39.011] svc=inventory :: reservation completed successfully",
    "level=E ts=[28/Jul/2026 15:08:41.230] svc=inventory :: reservation timed out order_id=ord-9f1",
    "level=I ts=[28/Jul/2026 15:08:41.500] svc=inventory :: compensating reserve released",
]
ALIEN_DESCRIPTOR = {
    "line_regex": (r"^level=(?:\w+)\s+ts=\[(?P<ts>[^\]]+)\]\s+svc=(?P<service>\S+)"
                   r"\s+::\s+(?P<msg>.*)$"),
    "ts_format": "%d/%b/%Y %H:%M:%S.%f",
    "notes": "mid-line-timestamp custom dialect from the design-doc example",
}

FLINK_LINES = [
    ("2026-07-28 15:08:38.903 [main] INFO  "
     "org.apache.flink.runtime.io.network.netty.NettyServer  "
     "- Transport type 'auto': using EPOLL."),
    ("2026-07-28 15:08:39.011 [flink-akka.actor.default-dispatcher-4] INFO  "
     "org.apache.flink.runtime.taskexecutor.TaskExecutor  "
     "- Connecting to ResourceManager."),
]

JSONL_LINES = [
    '{"ts":"2026-07-28T10:00:00.000Z","service":"svc","level":"INFO","msg":"hi"}',
    '{"ts":"2026-07-28T10:00:01.000Z","service":"svc","level":"ERROR","msg":"bye"}',
]


class TestFingerprint(unittest.TestCase):
    def test_stable_across_two_samples_of_the_same_dialect(self):
        # Same structural shape-mix as ALIEN_LINES (3 free-text lines + 1
        # "word word key=value" line), different literal words/keys/dates --
        # the fingerprint must depend on shape, not content.
        sample_a = ALIEN_LINES
        sample_b = [
            "level=W ts=[01/Jan/2026 00:00:00.001] svc=payment :: different words entirely here",
            "level=I ts=[02/Feb/2026 11:11:11.222] svc=payment :: another sentence of content",
            "level=E ts=[03/Mar/2026 22:22:22.333] svc=payment :: charge declined txn_id=tx-55c2",
            "level=I ts=[04/Apr/2026 09:09:09.099] svc=payment :: retrying payment shortly",
        ]
        self.assertEqual(fingerprint(sample_a), fingerprint(sample_b))

    def test_different_dialect_gives_different_fingerprint(self):
        self.assertNotEqual(fingerprint(ALIEN_LINES), fingerprint(FLINK_LINES))
        self.assertNotEqual(fingerprint(ALIEN_LINES), fingerprint(JSONL_LINES))

    def test_returns_12_hex_chars(self):
        fp = fingerprint(ALIEN_LINES)
        self.assertEqual(len(fp), 12)
        int(fp, 16)  # raises ValueError if not hex

    def test_empty_sample_does_not_crash(self):
        fp = fingerprint([])
        self.assertEqual(len(fp), 12)

    def test_top_skeletons_returns_list_of_strings(self):
        skels = top_skeletons(ALIEN_LINES)
        self.assertIsInstance(skels, list)
        self.assertLessEqual(len(skels), 5)
        for s in skels:
            self.assertIsInstance(s, str)


class TestValidateDescriptor(unittest.TestCase):
    def test_accepts_good_descriptor_for_weird_midline_timestamp_fixture(self):
        ok, hit_rates, reason = validate_descriptor(ALIEN_DESCRIPTOR, ALIEN_LINES)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")
        self.assertGreaterEqual(hit_rates["ts"], 0.90)

    def test_rejects_non_compiling_regex(self):
        bad = dict(ALIEN_DESCRIPTOR, line_regex=r"^level=(?P<ts>\d+")  # unbalanced paren
        ok, hit_rates, reason = validate_descriptor(bad, ALIEN_LINES)
        self.assertFalse(ok)
        self.assertIn("compile", reason.lower())

    def test_rejects_missing_ts_group(self):
        bad = dict(ALIEN_DESCRIPTOR, line_regex=r"^level=(?P<lvl>\w+)\s+.*$")
        ok, hit_rates, reason = validate_descriptor(bad, ALIEN_LINES)
        self.assertFalse(ok)
        self.assertIn("ts", reason.lower())

    def test_rejects_ts_hit_rate_below_threshold(self):
        # Only 1 of 4 lines is the real dialect; the rest are noise the
        # regex will never match -- hit-rate must land under 90%.
        mostly_noise = ALIEN_LINES[:1] + [
            "totally unrelated free text with no structure at all",
            "another unrelated line, still no match",
            "yet another line that will not match the pattern",
        ]
        ok, hit_rates, reason = validate_descriptor(ALIEN_DESCRIPTOR, mostly_noise)
        self.assertFalse(ok)
        self.assertLess(hit_rates["ts"], 0.90)
        self.assertIn("ts", reason.lower())

    def test_rejects_regex_over_length_cap(self):
        bad = dict(ALIEN_DESCRIPTOR, line_regex="(?P<ts>" + "a" * 2100 + ")")
        ok, hit_rates, reason = validate_descriptor(bad, ALIEN_LINES)
        self.assertFalse(ok)
        self.assertIn("2000", reason)

    def test_level_group_hit_rate_checked_when_present(self):
        # Level values are full recognized words here -- must pass the >=50%
        # normalize-to-known-level bar.
        lines = [
            "level=WARN ts=[28/Jul/2026 15:08:38.903] svc=inventory :: msg one",
            "level=INFO ts=[28/Jul/2026 15:08:39.011] svc=inventory :: msg two",
            "level=ERROR ts=[28/Jul/2026 15:08:41.230] svc=inventory :: msg three",
        ]
        descriptor = dict(ALIEN_DESCRIPTOR, line_regex=(
            r"^level=(?P<level>\w+)\s+ts=\[(?P<ts>[^\]]+)\]\s+svc=(?P<service>\S+)"
            r"\s+::\s+(?P<msg>.*)$"))
        ok, hit_rates, reason = validate_descriptor(descriptor, lines)
        self.assertTrue(ok, reason)
        self.assertGreaterEqual(hit_rates["level"], 0.50)

    def test_level_group_below_threshold_rejected(self):
        # Single-letter level codes never normalize via records.normalize_level
        # -> 0% hit-rate -> below the 50% bar -> rejected.
        lines = [
            "level=W ts=[28/Jul/2026 15:08:38.903] svc=inventory :: msg one",
            "level=I ts=[28/Jul/2026 15:08:39.011] svc=inventory :: msg two",
        ]
        descriptor = dict(ALIEN_DESCRIPTOR, line_regex=(
            r"^level=(?P<level>\w+)\s+ts=\[(?P<ts>[^\]]+)\]\s+svc=(?P<service>\S+)"
            r"\s+::\s+(?P<msg>.*)$"))
        ok, hit_rates, reason = validate_descriptor(descriptor, lines)
        self.assertFalse(ok)
        self.assertLess(hit_rates["level"], 0.50)


class TestApplyDescriptor(unittest.TestCase):
    def test_matched_lines_become_ok_records_with_iso_timestamps(self):
        recs = apply_descriptor(ALIEN_DESCRIPTOR, ALIEN_LINES, "inventory-service",
                                "alien.log", Masker())
        self.assertEqual(len(recs), 4)
        for r in recs:
            self.assertEqual(r.parse_quality, "partial")  # no level group declared
            self.assertTrue(r.timestamp.startswith("2026-07-28T15:08:"))
            self.assertTrue(r.timestamp.endswith("Z"))
        self.assertIn("reservation queue backing up", recs[0].body)
        self.assertEqual(recs[0].service, "inventory")

    def test_ok_quality_when_ts_and_level_both_present(self):
        descriptor = dict(ALIEN_DESCRIPTOR, line_regex=(
            r"^level=(?P<level>\w+)\s+ts=\[(?P<ts>[^\]]+)\]\s+svc=(?P<service>\S+)"
            r"\s+::\s+(?P<msg>.*)$"))
        lines = [
            "level=WARN ts=[28/Jul/2026 15:08:38.903] svc=inventory :: msg one",
            "level=INFO ts=[28/Jul/2026 15:08:39.011] svc=inventory :: msg two",
        ]
        recs = apply_descriptor(descriptor, lines, "inventory-service", "alien2.log", Masker())
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].parse_quality, "ok")
        self.assertEqual(recs[0].level, "WARN")

    def test_unmatched_lines_fold_into_previous_record(self):
        lines = list(ALIEN_LINES)
        lines.insert(2, "\tat com.example.Foo.bar(Foo.java:42)  -- a wrapped continuation line")
        recs = apply_descriptor(ALIEN_DESCRIPTOR, lines, "inventory-service",
                                "alien3.log", Masker())
        self.assertEqual(len(recs), 4)  # continuation folded, not a 5th record
        self.assertIn("wrapped continuation line", recs[1].body)

    def test_leading_unmatched_line_becomes_standalone_partial(self):
        lines = ["some preamble with no recognizable structure"] + ALIEN_LINES
        recs = apply_descriptor(ALIEN_DESCRIPTOR, lines, "inventory-service",
                                "alien4.log", Masker())
        self.assertEqual(len(recs), 5)
        self.assertEqual(recs[0].parse_quality, "partial")
        self.assertEqual(recs[0].timestamp, "")

    def test_domain_ids_discovered_in_matched_lines(self):
        recs = apply_descriptor(ALIEN_DESCRIPTOR, ALIEN_LINES, "inventory-service",
                                "alien5.log", Masker())
        self.assertEqual(recs[2].domain_ids.get("order_id"), "ord-9f1")

    def test_pii_masked_in_body(self):
        lines = [
            "level=I ts=[28/Jul/2026 15:08:38.903] svc=inventory :: contact ivan@example.com now"]
        recs = apply_descriptor(ALIEN_DESCRIPTOR, lines, "inventory-service",
                                "alien6.log", Masker())
        self.assertNotIn("ivan@example.com", recs[0].body)
        self.assertTrue(recs[0].redaction_applied)


class TestFormatStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store_dir = Path(self.dir.name) / "learned"

    def tearDown(self):
        self.dir.cleanup()

    def test_save_then_get_round_trips(self):
        store = FormatStore(self.store_dir)
        fp = fingerprint(ALIEN_LINES)
        skeleton = top_skeletons(ALIEN_LINES)
        doc = store.save(fp, ALIEN_DESCRIPTOR, {"ts": 1.0}, skeleton)
        self.assertEqual(doc["fingerprint"], fp)
        loaded = store.get(fp)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["descriptor"], ALIEN_DESCRIPTOR)
        self.assertEqual(loaded["hit_rates"], {"ts": 1.0})
        self.assertEqual(loaded["sample_skeleton"], skeleton)
        self.assertIn("created", loaded)

    def test_get_missing_fingerprint_returns_none(self):
        store = FormatStore(self.store_dir)
        self.assertIsNone(store.get("deadbeef1234"))

    def test_saved_file_is_named_by_fingerprint(self):
        store = FormatStore(self.store_dir)
        fp = fingerprint(ALIEN_LINES)
        store.save(fp, ALIEN_DESCRIPTOR, {"ts": 1.0}, [])
        self.assertTrue((self.store_dir / ("%s.json" % fp)).is_file())

    def test_dir_created_on_demand(self):
        nested = Path(self.dir.name) / "a" / "b" / "learned"
        store = FormatStore(nested)
        store.save("abc123def456", ALIEN_DESCRIPTOR, {}, [])
        self.assertTrue(nested.is_dir())

    def test_shipped_learned_dir_has_gitkeep(self):
        case_root = Path(__file__).resolve().parents[1]
        self.assertTrue((case_root / "formats.d" / "learned" / ".gitkeep").is_file())


if __name__ == "__main__":
    unittest.main()
