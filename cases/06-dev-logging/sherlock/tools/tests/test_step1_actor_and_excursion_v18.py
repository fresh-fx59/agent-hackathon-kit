#!/usr/bin/env python3
"""Tests for skills/v18/tools/logmap.py — the two labelled files Step 1 still
never opened, and the two general properties that reach them.

MEASURED FIRST, on 2026-08-18, with `eval/bench/score-ait.py` against AIT-LDS
v2.1's own per-line labels (russellmitchell, 22 hosts, 7,464 files):

    arm   FILES TOUCHED   vpn/logs/openvpn.log   monitoring/.../system.cpu.log
    v16       4 of 8            0 of 28                  0 of 49
    v17       6 of 8            0 of 28                  0 of 49

v17's floor bought attention for the two apache logs and nothing for these two,
and its own report says why:

  * `vpn/logs/openvpn.log` has 15 real rarity groups, so it never reaches the
    floor at all. It gets rows; they are the first occurrence of each template,
    and every template first occurs in the first 700 lines. This is a RANKING
    miss, not a budget miss.
  * `monitoring/logs/logstash/intranet-server/2022-01-24-system.cpu.log` gets
    exactly 2 floor rows (`level`, `edge`) and neither is labelled. Its two
    templates are both common (ratio 0.0010) — nothing about the attack lines
    is RARE.

WHAT THE ATTACK LINES ACTUALLY ARE — read before any code was written.

  openvpn.log lines 4331-4358 are one complete VPN session establishment:
  `TLS: Initial packet from` … `VERIFY OK` … `Peer Connection Initiated` …
  `MULTI: Learn` … `PUSH_REPLY`. The other 5,509 lines are 65 further sessions
  and 171 in-session TLS renegotiations. The block is not a new SHAPE — every
  one of its templates occurs 66 times. What is new is the PARTY: the peer
  address in it has been absent from the whole first 78 % of the file.

  system.cpu.log lines 321-369 are metricbeat samples in which
  `host.cpu.pct` sits at 1.0 (0.86 on the ramp, 0.81 on the way down) against a
  file-wide typical of 0.065 — a 15.5x level excursion inside hour 04 that
  returns to baseline in hour 05. Not a rare string; a rate.

THE TWO GENERAL PROPERTIES, stated before any label was consulted:

  axis 5  new   — a party that appears for the FIRST time after a stream has
                  established its population is a state transition. Not "rare":
                  rare and late are different claims, and on openvpn.log rarest-
                  first picks four internal pool addresses ahead of the one that
                  matters. LATENESS is the claim, and it is a claim about WHEN.
  axis 6  peak  — a measurement's signal is a LEVEL, not a shape. An excursion
                  is a departure that RETURNS: an hour whose median is
                  RATE_FACTOR above the file's own typical hour, with a
                  baseline hour on each side of it. The return test is what
                  separates an excursion from a counter, and from the drift
                  axis 3 already measures first-hour-vs-last.

ANTI-OVERFIT. The AIT answer key is readable from this repo, so a test that only
asserted "line 4331 gets a row" would be worth nothing. Every behavioural test
below runs on a SYNTHETIC log that shares the SHAPE and shares no value: a
different protocol, different field names, different addresses, different
numbers, a different clock. Neither axis may key on an address, a user name, a
host name, a tool name or a date. The two real-corpus assertions at the bottom
are the MEASUREMENT, run only when the corpus is on disk, and they are the last
thing in the file on purpose.

    python3 tools/tests/test_step1_actor_and_excursion_v18.py
"""
import contextlib
import filecmp
import hashlib
import importlib.util
import io
import os
import re
import shutil
import string
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)

AIT = "/Users/a/hack/sherlock-corpora/ait-lds-v2/extracted/gather"
NEW_KINDS = {"new", "peak"}


def _load(name, version):
    path = os.path.join(SHERLOCK, "skills", version, "tools", "logmap.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _load("logmap_v18", "v18")
V17 = _load("logmap_v17_ref", "v17")


# ---------------------------------------------------------------------------
# helpers — the tool is always driven through main(), exactly as the operator
# drives it, so nothing here can pass by calling an internal the skill never
# reaches.
# ---------------------------------------------------------------------------
def put(root, rel, text):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    return p


def run(mod, corpus, out, extra=()):
    argv = sys.argv
    sys.argv = ["logmap.py", corpus, "--out", out, "--jobs", "1"] + list(extra)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.main()
    finally:
        sys.argv = argv
    if rc:
        raise AssertionError("logmap exited %s\n%s" % (rc, buf.getvalue()))
    return buf.getvalue()


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def rows_of(path):
    out = []
    for line in read(path).splitlines():
        if line.startswith("#") or not line.strip():
            continue
        out.append(line.split("\t"))
    return out


def rows_for(path, rel):
    return [r for r in rows_of(path) if len(r) > 3 and r[3].split(":")[0] == rel]


def cited_lines(path, rel):
    """-> set of physical lines the worklist addresses in `rel`."""
    hit = set()
    for r in rows_for(path, rel):
        m = re.search(r":(\d+)(?:-(\d+))?$", r[3])
        if m:
            a = int(m.group(1))
            b = int(m.group(2) or a)
            hit.update(range(a, b + 1))
    return hit


ALPHA = string.ascii_lowercase


# ---------------------------------------------------------------------------
# fixtures — same SHAPE as the two AIT files, no value in common with them
# ---------------------------------------------------------------------------
def session_log(n=900, late_at=0.78, newcomer="203.0.113.77"):
    """A session-oriented daemon log: three peers present from the first record,
    a fourth that appears only `late_at` of the way in.

    Nothing here is openvpn: different daemon, different wording, RFC-5737
    documentation addresses, different user names, a different year. What it
    shares with openvpn.log is the SHAPE — every template is common, every
    session repeats the same six lines, and one peer arrives late."""
    peers = [("kmorel", "198.51.100.4"), ("dvraj", "198.51.100.51"),
             ("bfoss", "198.51.100.9")]
    steps = ("handshake begun proto=%d" % 2,
             "certificate accepted chain depth=1",
             "cipher negotiated suite=CHACHA20",
             "address leased pool=b",
             "route pushed metric=10",
             "session ready keepalive=30")
    out = []
    cut = int(n * late_at)
    for i in range(n):
        if i == cut:
            user, host = "kmorel", newcomer
        else:
            user, host = peers[i % len(peers)]
        out.append("2019-04-%02d %02d:%02d:%02d relayd[%d] %s@%s:%d %s"
                   % (1 + (i // 300), (i // 60) % 24, i % 60, (i * 7) % 60,
                      4000 + i % 9, user, host, 40000 + (i % 900), steps[i % 6]))
        if i == cut:
            # the newcomer's session runs to completion, like any other
            for s in steps:
                out.append("2019-04-%02d %02d:%02d:%02d relayd[%d] %s@%s:%d %s"
                           % (1 + (i // 300), (i // 60) % 24, i % 60,
                              (i * 7) % 60, 4000 + i % 9, user, newcomer,
                              40000 + (i % 900), s))
    return "\n".join(out) + "\n"


SAMPLE_FMT = ("2019-04-02T%02d:%02d:%02dZ sampler node=b7 "
              "queue_saturation=%.6f backlog=%d")


def _jitter(i):
    """A measurement wobbles — that is what makes it a measurement rather than a
    category, and METRIC_MIN_DISTINCT is the gate that says so."""
    return 1.0 + ((i * 37) % 211) / 4000.0


def sampler_log(hours=12, per_hour=200, spike_hour=5, level=0.04, peak=0.9):
    """A metric sampler: one template, one numeric field, flat except for one
    middle hour that departs and comes back.

    Not metricbeat, not JSON, not `cpu`, not `pct`: a `key=value` sampler with a
    made-up field name and a made-up node. What it shares with the AIT file is
    the SHAPE — every record is the same, the only thing that moves is a number,
    and the excursion is in the middle of the window.

    200 records an hour ON PURPOSE, twice v17's comparable-hour floor: the point
    to demonstrate is not that axis 3's floor is too high for a 45-second
    sampler (it is, and that is why AIT's metric logs get nothing), it is that
    first-hour-against-last cannot see a spike that recovers AT ALL."""
    out = []
    for h in range(hours):
        for i in range(per_hour):
            v = (peak if h == spike_hour else level) * _jitter(i)
            out.append(SAMPLE_FMT % (h, i % 60, (i * 13) % 60, v, 3 + i % 5))
    return "\n".join(out) + "\n"


def rising_log(hours=12, per_hour=200):
    """The negative control for axis 6: a counter that only ever rises. Its last
    hour is 12x its typical hour and it is NOT an excursion — it never returns,
    and drift is what axis 3 already measures."""
    out = []
    v = 0.0
    for h in range(hours):
        for i in range(per_hour):
            v += 1.0
            out.append(SAMPLE_FMT % (h, i % 60, (i * 13) % 60, v, 3 + i % 5))
    return "\n".join(out) + "\n"


def churn_log(n=900):
    """The negative control for axis 5: a field with a fresh address on nearly
    every record. There is no population, so nothing in it can be new — and an
    access log under a scanner is exactly this shape."""
    out = []
    for i in range(n):
        out.append("2019-04-02 %02d:%02d:%02d relayd[7] user%d@10.%d.%d.%d:%d "
                   "session ready keepalive=30"
                   % ((i // 60) % 24, i % 60, (i * 7) % 60, i % 5,
                      1 + i // 250, (i // 7) % 250, i % 250, 40000 + i))
    return "\n".join(out) + "\n"


def ordinary_log(n=300, marker=1):
    """An ordinary log with a clock and a genuine rare residue — the shape that
    must come out of v18 byte for byte as it came out of v17."""
    out = []
    for i in range(n):
        out.append("2022-01-24 07:%02d:%02d INFO worker=%d handled request ok"
                   % ((i // 60) % 60, i % 60, i % 5))
    out.append("2022-01-24 07:59:59 ERROR worker=%d segmentation fault in module"
               % marker)
    return "\n".join(out) + "\n"


# ===========================================================================
# axis 5 — a party that arrives late
# ===========================================================================
class ANewPartyIsAStateTransition(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.corpus = os.path.join(self.d, "corpus")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.body = session_log()
        put(self.corpus, "logs/relay.log", self.body)
        put(self.corpus, "logs/app.log", ordinary_log())
        self.newcomer_line = 1 + next(
            i for i, l in enumerate(self.body.splitlines())
            if "203.0.113.77" in l)

    def test_the_fixture_is_the_shape_the_defect_lives_in(self):
        """Asserted, not assumed: every template in this file is common, so the
        rarity axis has nothing to say about the newcomer."""
        import argparse
        args = argparse.Namespace(seed=V17.SEED, per_file_cap=40)
        rep = V17.analyse(os.path.join(self.corpus, "logs", "relay.log"),
                          "logs/relay.log", args)
        self.assertFalse(rep.gated, "fixture must NOT be gated")
        self.assertTrue(rep.groups, "fixture must have ordinary rarity groups")
        self.assertGreater(rep.records, 900)

    def test_v17_never_addresses_the_newcomer(self):
        out = os.path.join(self.d, "w17")
        run(V17, self.corpus, out)
        self.assertNotIn(self.newcomer_line,
                         cited_lines(os.path.join(out, "worklist.tsv"),
                                     "logs/relay.log"))

    def test_v18_addresses_the_newcomer(self):
        out = os.path.join(self.d, "w18")
        run(L, self.corpus, out)
        self.assertIn(self.newcomer_line,
                      cited_lines(os.path.join(out, "worklist.tsv"),
                                  "logs/relay.log"),
                      "the one record where a new party first appears")

    def test_the_row_says_which_axis_produced_it(self):
        out = os.path.join(self.d, "w18")
        run(L, self.corpus, out)
        kinds = {r[2] for r in rows_for(os.path.join(out, "worklist.tsv"),
                                        "logs/relay.log")
                 if re.search(r":%d$" % self.newcomer_line, r[3])}
        self.assertEqual({"new"}, kinds)

    def test_the_axis_keys_on_lateness_and_not_on_the_value(self):
        """The tripwire. Same shape, a different newcomer address — the row has
        to move with it. A rule that survives this cannot be keyed on a value."""
        for addr in ("192.0.2.200", "203.0.113.5", "198.18.7.19"):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            body = session_log(newcomer=addr)
            want = 1 + next(i for i, l in enumerate(body.splitlines())
                            if addr in l)
            put(corpus, "logs/relay.log", body)
            put(corpus, "logs/app.log", ordinary_log())
            run(L, corpus, out)
            self.assertIn(want, cited_lines(os.path.join(out, "worklist.tsv"),
                                            "logs/relay.log"),
                          "axis 5 missed the newcomer %s" % addr)

    def test_a_party_present_in_the_first_half_is_not_new(self):
        """Lateness is the whole claim. A peer that showed up at 20 % is part of
        the population, however few records it owns."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        body = session_log(late_at=0.20)
        early = 1 + next(i for i, l in enumerate(body.splitlines())
                         if "203.0.113.77" in l)
        put(corpus, "logs/relay.log", body)
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        rows = [r for r in rows_for(os.path.join(out, "worklist.tsv"),
                                    "logs/relay.log") if r[2] == "new"]
        self.assertEqual([], rows,
                         "a party seen at 20%% of the stream is not new")
        self.assertNotIn(early, cited_lines(os.path.join(out, "worklist.tsv"),
                                            "logs/relay.log"))

    def test_a_field_with_no_population_produces_no_new_row(self):
        """A fresh address on every record is churn, not novelty. Without this
        gate every access log under a scanner becomes 3 rows of nothing."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        put(corpus, "logs/churn.log", churn_log())
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        self.assertEqual([], [r for r in rows_for(
            os.path.join(out, "worklist.tsv"), "logs/churn.log")
            if r[2] == "new"])

    def test_too_many_newcomers_silences_the_axis(self):
        """More than ACTOR_LATE_MAX arrivals means the population was never
        established, and "new" stops meaning anything. Silence beats a confident
        wrong row."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        body = session_log().splitlines()
        cut = int(len(body) * 0.8)
        for k in range(L.ACTOR_LATE_MAX + 2):
            body.insert(cut + k * 3,
                        "2019-04-03 22:%02d:00 relayd[9] kmorel@203.0.113.%d:41000 "
                        "session ready keepalive=30" % (k, 30 + k))
        put(corpus, "logs/relay.log", "\n".join(body) + "\n")
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        self.assertEqual([], [r for r in rows_for(
            os.path.join(out, "worklist.tsv"), "logs/relay.log")
            if r[2] == "new"])

    def test_the_axis_is_bounded_per_file(self):
        out = os.path.join(self.d, "w18")
        run(L, self.corpus, out)
        n = len([r for r in rows_for(os.path.join(out, "worklist.tsv"),
                                     "logs/relay.log") if r[2] == "new"])
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(n, L.ACTOR_PER_FILE)


# ===========================================================================
# axis 6 — a level that departs and returns
# ===========================================================================
class AnExcursionIsADepartureThatReturns(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.corpus = os.path.join(self.d, "corpus")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.body = sampler_log()
        put(self.corpus, "logs/sampler.log", self.body)
        put(self.corpus, "logs/app.log", ordinary_log())
        self.spike_lo = 1 + next(
            i for i, l in enumerate(self.body.splitlines())
            if "queue_saturation=0.9" in l)
        self.spike_n = sum(1 for l in self.body.splitlines()
                           if "queue_saturation=0.9" in l)

    def test_the_fixture_is_the_shape_the_defect_lives_in(self):
        import argparse
        args = argparse.Namespace(seed=V17.SEED, per_file_cap=40)
        rep = V17.analyse(os.path.join(self.corpus, "logs", "sampler.log"),
                          "logs/sampler.log", args)
        self.assertFalse(rep.gated)
        self.assertEqual([], rep.rate_rows,
                         "axis 3 must be silent here — the shift is not "
                         "first-hour-vs-last")
        self.assertTrue(rep.bg_rows,
                        "worse than silent: v17 files this stream under "
                        "BACKGROUND, i.e. it writes down that nothing moved")

    def test_v17_never_addresses_the_excursion(self):
        out = os.path.join(self.d, "w17")
        run(V17, self.corpus, out)
        hit = cited_lines(os.path.join(out, "worklist.tsv"), "logs/sampler.log")
        self.assertFalse(any(self.spike_lo <= x < self.spike_lo + self.spike_n
                             for x in hit))

    def test_v18_addresses_the_excursion(self):
        out = os.path.join(self.d, "w18")
        run(L, self.corpus, out)
        hit = cited_lines(os.path.join(out, "worklist.tsv"), "logs/sampler.log")
        self.assertTrue(any(self.spike_lo <= x < self.spike_lo + self.spike_n
                            for x in hit),
                        "no row inside the excursion hour")

    def test_the_row_says_which_axis_produced_it_and_carries_the_numbers(self):
        out = os.path.join(self.d, "w18")
        run(L, self.corpus, out)
        rows = [r for r in rows_for(os.path.join(out, "worklist.tsv"),
                                    "logs/sampler.log") if r[2] == "peak"]
        self.assertTrue(rows, "no peak row")
        self.assertTrue(any("queue_saturation" in r[5] for r in rows),
                        "a peak row must name the field it measured: %r"
                        % [r[5] for r in rows])

    def test_the_axis_keys_on_the_ratio_and_not_on_the_numbers(self):
        """The tripwire. Different baseline, different peak, different field
        magnitude, different hour — the row has to follow the RATIO."""
        for level, peak, hour in ((11.0, 480.0, 3), (0.002, 0.05, 7),
                                  (900.0, 3300.0, 4)):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            body = sampler_log(level=level, peak=peak, spike_hour=hour)
            mark = "queue_saturation=%.6f" % peak
            lines = body.splitlines()
            lo = 1 + next(i for i, l in enumerate(lines) if mark in l)
            n = sum(1 for l in lines if l.startswith(
                "2019-04-02T%02d:" % hour))
            put(corpus, "logs/sampler.log", body)
            put(corpus, "logs/app.log", ordinary_log())
            run(L, corpus, out)
            hit = cited_lines(os.path.join(out, "worklist.tsv"),
                              "logs/sampler.log")
            self.assertTrue(any(lo <= x < lo + n for x in hit),
                            "axis 6 missed %s->%s in hour %d" % (level, peak, hour))

    def test_a_counter_is_not_an_excursion(self):
        """The negative control. A value that only rises ends 12x its typical
        hour and never returns — that is drift, which axis 3 already reports,
        and a peak row for it would be a second, weaker claim about the same
        thing."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        put(corpus, "logs/rise.log", rising_log())
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        self.assertEqual([], [r for r in rows_for(
            os.path.join(out, "worklist.tsv"), "logs/rise.log")
            if r[2] == "peak"])

    def test_an_excursion_at_the_edge_of_the_window_is_not_claimed(self):
        """At the first or last hour there is no "before" or no "after", so an
        excursion cannot be told from the tail of a trend. Refusing the claim is
        the same rule `hourly_onset` already applies to onsets."""
        for hour in (0, 11):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
            put(corpus, "logs/sampler.log", sampler_log(spike_hour=hour))
            put(corpus, "logs/app.log", ordinary_log())
            run(L, corpus, out)
            self.assertEqual([], [r for r in rows_for(
                os.path.join(out, "worklist.tsv"), "logs/sampler.log")
                if r[2] == "peak"], "claimed an excursion at hour %d" % hour)

    def test_the_axis_is_bounded_per_file(self):
        out = os.path.join(self.d, "w18")
        run(L, self.corpus, out)
        n = len([r for r in rows_for(os.path.join(out, "worklist.tsv"),
                                     "logs/sampler.log") if r[2] == "peak"])
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(n, L.PEAK_PER_FILE)

    def test_a_reused_handle_is_a_category_and_not_a_measurement(self):
        """MEASURED, and it is the only false positive three corpora produced.
        BlueSky's `json:ThreadID` is 88 values over 1,250 records with one of
        them owning 643 — a handle the runtime hands back out — and its median
        per hour genuinely goes 1852 -> 6664 -> back. Numeric, varied, and
        recovering: every test above passes on it. What it is not is a level,
        and a value that owns a quarter of the file is what says so."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        rows = []
        for h in range(12):
            for i in range(200):
                # one handle dominates; the rest are drawn from a pool that
                # shifts upward for one hour and comes back
                if i % 2 == 0:
                    v = 1784
                elif h == 5:
                    v = 6000 + (i * 7) % 900
                else:
                    v = 1000 + (i * 7) % 900
                rows.append("2019-04-02T%02d:%02d:%02dZ runtime handle=%d "
                            "step=ok" % (h, i % 60, (i * 13) % 60, v))
        put(corpus, "logs/runtime.log", "\n".join(rows) + "\n")
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        self.assertEqual([], [r for r in rows_for(
            os.path.join(out, "worklist.tsv"), "logs/runtime.log")
            if r[2] == "peak"])

    def test_a_unique_per_record_id_does_not_take_the_slot(self):
        """Two numeric fields, one measurement and one strictly-rising record
        id. Ranking measurements by "most distinct values" would hand the slot
        to the id — it wins that sort by construction — so the sort is by
        REPETITION, and this asserts the sign."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        rows = []
        rec = 0
        for h in range(12):
            for i in range(200):
                rec += 1
                v = (0.9 if h == 5 else 0.04) * _jitter(i)
                rows.append("2019-04-02T%02d:%02d:%02dZ sampler seq=%d "
                            "queue_saturation=%.6f"
                            % (h, i % 60, (i * 13) % 60, rec, v))
        body = "\n".join(rows) + "\n"
        lo = 1 + next(i for i, l in enumerate(body.splitlines())
                      if l.startswith("2019-04-02T05:"))
        put(corpus, "logs/sampler.log", body)
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        hit = cited_lines(os.path.join(out, "worklist.tsv"), "logs/sampler.log")
        self.assertTrue(any(lo <= x < lo + 200 for x in hit),
                        "the record id took the measurement's slot")

    def test_a_file_the_new_axes_can_rank_needs_no_floor(self):
        """`groups` OR `floor`, never both. A metric log that now earns a ranked
        row must stop drawing the weaker `edge`/`level` pair."""
        import argparse
        args = argparse.Namespace(seed=L.SEED, per_file_cap=40)
        rep = L.analyse(os.path.join(self.corpus, "logs", "sampler.log"),
                        "logs/sampler.log", args)
        self.assertTrue(rep.groups)
        self.assertEqual([], rep.floor)


# ===========================================================================
# the tool stays the tool
# ===========================================================================
class NothingElseMoved(unittest.TestCase):
    def test_a_corpus_where_neither_axis_fires_is_byte_identical_to_v17(self):
        """The regression bar. Where v17 already had something to say, v18 says
        exactly the same thing, byte for byte — in all three artefacts."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus = os.path.join(d, "c")
        put(corpus, "logs/app.log", ordinary_log())
        put(corpus, "logs/other.log", ordinary_log(200, marker=3))
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        run(V17, corpus, a)
        run(L, corpus, b)
        for name in ("map.txt", "worklist.tsv", "axis3.tsv"):
            self.assertTrue(filecmp.cmp(os.path.join(a, name),
                                        os.path.join(b, name), shallow=False),
                            "%s changed on a corpus with nothing to find" % name)

    def test_the_legend_appears_only_when_such_a_row_occurs(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        quiet, loud = os.path.join(d, "q"), os.path.join(d, "l")
        put(quiet, "logs/app.log", ordinary_log())
        put(loud, "logs/relay.log", session_log())
        put(loud, "logs/app.log", ordinary_log())
        qo, lo = os.path.join(d, "qo"), os.path.join(d, "lo")
        run(L, quiet, qo)
        run(L, loud, lo)
        self.assertNotIn("«новый»", read(os.path.join(qo, "worklist.tsv")))
        self.assertIn("«новый»", read(os.path.join(lo, "worklist.tsv")))

    def test_a_state_artefact_gets_no_new_or_peak_row(self):
        """Same decision the floor already made, for the same measured reason: a
        config is not a stream, and CAM-LDS scenario 1 is 8,000 of them."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        cfg = "".join("allow from 198.51.100.%d  # rule %d\n" % (i % 40, i)
                      for i in range(400))
        cfg += "".join("allow from 203.0.113.%d  # late rule %d\n" % (i, i)
                       for i in range(3))
        self.assertGreater(len(cfg), L.SMALL_FILE_BYTES)
        put(corpus, "etc/allow.conf", cfg)
        put(corpus, "logs/app.log", ordinary_log())
        run(L, corpus, out)
        self.assertEqual([], [r for r in rows_for(
            os.path.join(out, "worklist.tsv"), "etc/allow.conf")
            if r[2] in NEW_KINDS])

    def test_the_rate_axis_is_unchanged(self):
        """axis 6 answers a question axis 3 does not ask. It must not answer the
        one axis 3 does: a first-hour-vs-last drift stays axis 3's row."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus = os.path.join(d, "c")
        put(corpus, "logs/rise.log", rising_log())
        put(corpus, "logs/app.log", ordinary_log())
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        run(V17, corpus, a)
        run(L, corpus, b)
        self.assertTrue(filecmp.cmp(os.path.join(a, "axis3.tsv"),
                                    os.path.join(b, "axis3.tsv"), shallow=False))


class TheOlderArmsAreUntouched(unittest.TestCase):
    """v1…v17 each define what a measured arm ran. v18 is additive."""

    def test_v18_changed_no_tool_but_logmap(self):
        for rel in ("reference/report-format.md", "reference/code-and-spec.md",
                    "tools/logjoin.py", "tools/citecheck.py"):
            self.assertTrue(
                filecmp.cmp(os.path.join(SHERLOCK, "skills", "v17", rel),
                            os.path.join(SHERLOCK, "skills", "v18", rel),
                            shallow=False),
                "v18 changed %s, which is not what it is for" % rel)

    def test_v18_did_change_logmap(self):
        self.assertFalse(
            filecmp.cmp(os.path.join(SHERLOCK, "skills", "v17", "tools", "logmap.py"),
                        os.path.join(SHERLOCK, "skills", "v18", "tools", "logmap.py"),
                        shallow=False))

    def test_v17_is_still_v17(self):
        """The frozen arms ARE the measurement. Every AIT/BlueSky/CAM-LDS number
        this PR quotes for v17 was produced by exactly these bytes; if they move,
        the comparison stops being one. Pinned by digest rather than by a
        neighbour so the assertion cannot be satisfied by moving both."""
        for rel, want in (("tools/logmap.py",
                           "17f97cd25582944a51b106b57cefb735"),
                          ("SKILL.md", "0402712ee76d9231a84b4d0249d05c10")):
            with open(os.path.join(SHERLOCK, "skills", "v17", rel), "rb") as fh:
                got = hashlib.md5(fh.read()).hexdigest()
            self.assertEqual(want, got,
                             "skills/v17/%s changed — a frozen arm just moved"
                             % rel)

    def test_the_new_axes_are_documented_where_the_model_reads(self):
        skill = read(os.path.join(SHERLOCK, "skills", "v18", "SKILL.md"))
        tools = read(os.path.join(SHERLOCK, "skills", "v18", "reference",
                                  "tools.md"))
        for word in ("new", "peak"):
            self.assertIn(word, skill,
                          "SKILL.md never mentions the `%s` axis — a row kind "
                          "the model is not told about is noise" % word)
            self.assertIn(word, tools)


# ===========================================================================
# the measurement — the real corpus, and the last thing in this file
# ===========================================================================
@unittest.skipUnless(os.path.isdir(AIT), "AIT-LDS not on this machine")
class TheTwoFilesAitStillHid(unittest.TestCase):
    """Not a design test: a record of what the two general rules above actually
    reach on the corpus that motivated them. Both assertions are on PHYSICAL
    LINES that AIT's own label files mark as attack."""

    def _rows(self, sub):
        """The real file, alone in a temp corpus. Alone because the axes are
        computed per FILE — nothing here depends on a neighbour — and because
        the two AIT hosts these live on are 662 MB and 1.8 GB, which is a
        measurement run, not a test. The whole-testbed numbers are in the PR."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        corpus, out = os.path.join(d, "c"), os.path.join(d, "w")
        os.makedirs(os.path.join(corpus, "logs"))
        rel = "logs/" + os.path.basename(sub)
        shutil.copyfile(os.path.join(AIT, sub),
                        os.path.join(corpus, rel.replace("/", os.sep)))
        run(L, corpus, out, extra=("--single-host",))
        return (cited_lines(os.path.join(out, "worklist.tsv"), rel),
                rows_for(os.path.join(out, "worklist.tsv"), rel))

    def test_openvpn_gets_a_row_on_the_attacker_session(self):
        hit, rows = self._rows("vpn/logs/openvpn.log")
        self.assertIn(4331, hit,
                      "line 4331 is `TLS: Initial packet from` — the first of "
                      "28 labelled lines, and the first record carrying a peer "
                      "absent from the first 78 % of the file")
        self.assertIn("new", {r[2] for r in rows})

    def test_the_metric_log_gets_a_row_inside_the_cpu_excursion(self):
        hit, rows = self._rows(
            "monitoring/logs/logstash/intranet-server/2022-01-24-system.cpu.log")
        self.assertTrue(hit & set(range(321, 370)),
                        "lines 321-369 are host.cpu.pct at 1.0 against a "
                        "typical of 0.065")
        self.assertIn("peak", {r[2] for r in rows})


if __name__ == "__main__":
    unittest.main(verbosity=2)
