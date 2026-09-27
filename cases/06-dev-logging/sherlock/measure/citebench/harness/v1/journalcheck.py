#!/usr/bin/env python3
"""journalcheck (citebench harness v1) — the package journalcheck plus pinned prior streams.

    python3 journalcheck.py --tools TRUSTED/<pkg>/tools --work RUN/work/work --json \
        --stream RUN/out/*/stream*.jsonl \
        [--prior-stream TRUSTED/seed/stream.jsonl --prior-sha256 HEX]...

Part A (vault spec 2026-09-27-replay-journalcheck-and-repair-loop, Decision A2). A
citebench replay splits one logical journal across two runs: row 1 of the replay's
checkpoint.jsonl was written in the SEED run, and its receipt was printed only in the
seed's investigate stream. The package journalcheck sees only this run's streams, so it
reported a true row as `receipt_unprinted` (replay-v62-r3).

This file is NOT a skills/vNN tree (those are never edited in place). It loads the
package's own journalcheck.py/checkpoint.py from --tools and reuses every check; it adds:

  --prior-stream PATH / --prior-sha256 HEX  (repeatable, paired by position). Each file
      is sha256-checked before it is read; a mismatch exits 2 `prior_digest_mismatch`.
  origin    every printed receipt is tagged `prior` or `live`. Prior and live streams are
            parsed separately (tool_use ids are per session and may collide).
  prefix    seed_last_seq = max seq printed in the prior streams. A journal row that
            cites a prior receipt must have boundary_seq <= seed_last_seq and must come
            before every live row; otherwise `receipt_prior_out_of_prefix`.
  unjournaled  also covers prior receipts, so the seed's journal cannot be cut short.
  control_write  runs on the live streams only (the prior was gated in its own run).

A row whose receipt no trusted stream printed is still `receipt_unprinted`.
Exit 0 = clean, 1 = violations, 2 = usage / unreadable input / prior digest mismatch.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys


def load_pkg(tools):
    path = os.path.join(tools, "journalcheck.py")
    spec = importlib.util.spec_from_file_location("_pkg_journalcheck", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)     # its _sibling() loads checkpoint.py next to it
    return mod


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(jc, work, streams, priors):
    """priors = [(path, sha256)] already digest-verified."""
    bad = [{"code": c, "detail": m} for c, m in jc.CK.verify_journal(work)]
    live_uses, live_results = jc.read_stream(streams)
    printed = {}                                   # rid -> (sha, seq, origin)
    prior_info = []
    for path, sha in priors:
        uses, results = jc.read_stream([path])
        got = jc.printed_receipts(uses, results)
        prior_info.append({"path": os.path.abspath(path), "sha256": sha, "receipts": len(got)})
        for rid, (rsha, seq) in got.items():
            printed[rid] = (rsha, seq, "prior")
    for rid, (rsha, seq) in jc.printed_receipts(live_uses, live_results).items():
        printed[rid] = (rsha, seq, "live")         # a live print wins over a prior one
    prior_seqs = [seq for (_s, seq, o) in printed.values() if o == "prior"]
    seed_last_seq = max(prior_seqs) if prior_seqs else 0
    rows = jc.journal_rows(work)
    journaled = set()
    seen_live = False
    for row in rows:
        rid = row.get("handoff_receipt_id")
        if not rid:
            continue                               # already reported by verify_journal
        journaled.add(rid)
        want = printed.get(rid)
        seq = row.get("boundary_seq")
        if want is None:
            bad.append({"code": "receipt_unprinted",
                        "detail": "journal row boundary_seq=%r: receipt %s was never "
                                  "printed by checkpoint.py handoff" % (seq, rid)})
            continue
        if want[2] == "prior":
            if seen_live or not isinstance(seq, int) or seq > seed_last_seq:
                bad.append({"code": "receipt_prior_out_of_prefix",
                            "detail": "journal row boundary_seq=%r cites prior-run receipt %s; "
                                      "prior receipts cover only the leading rows 1..%d"
                                      % (seq, rid, seed_last_seq)})
                continue
        else:
            seen_live = True
        if (want[0], want[1]) != (row.get("handoff_sha256"), seq):
            bad.append({"code": "receipt_mismatch",
                        "detail": "journal row boundary_seq=%r does not match its "
                                  "printed receipt" % seq})
    for rid, (_sha, seq, origin) in sorted(printed.items(), key=lambda kv: kv[1][1]):
        if rid not in journaled:
            bad.append({"code": "receipt_unjournaled",
                        "detail": "printed receipt %s (seq %d, %s stream) is missing from "
                                  "checkpoint.jsonl" % (rid, seq, origin)})
    for path, n, what in jc.control_writes(live_uses):
        bad.append({"code": "control_write",
                    "detail": "%s:%d %s" % (os.path.basename(path), n, what)})
    return {"tool": "citebench-harness-v1/journalcheck.py", "work": os.path.abspath(work),
            "streams": [os.path.abspath(s) for s in streams], "prior_streams": prior_info,
            "seed_last_seq": seed_last_seq, "rows": len(rows),
            "printed_receipts": len(printed), "violations": bad, "blocking": len(bad)}


def _fail(a, code, detail):
    if a.json:
        print(json.dumps({"error": code, "detail": detail, "blocking": 1}, ensure_ascii=False))
    else:
        print("✗ %s: %s" % (code, detail))
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tools", required=True, help="the staged package's tools/ dir")
    ap.add_argument("--work", required=True)
    ap.add_argument("--stream", action="append", default=[], required=True)
    ap.add_argument("--prior-stream", action="append", default=[])
    ap.add_argument("--prior-sha256", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if len(a.prior_stream) != len(a.prior_sha256):
        return _fail(a, "usage", "--prior-stream and --prior-sha256 must pair one to one")
    for s in a.stream + a.prior_stream:
        if not os.path.isfile(s):
            return _fail(a, "usage", "no such stream: %s" % s)
    priors = []
    for path, want in zip(a.prior_stream, a.prior_sha256):
        got = sha256_file(path)
        if got != want.lower():
            return _fail(a, "prior_digest_mismatch",
                         "%s sha256 %s, pinned %s" % (path, got, want))
        priors.append((path, got))
    res = check(load_pkg(a.tools), a.work, a.stream, priors)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for v in res["violations"]:
            print("✗ %s: %s" % (v["code"], v["detail"]))
        print("journalcheck: %d rows, %d printed receipts (%d prior streams), %d blocking"
              % (res["rows"], res["printed_receipts"], len(priors), res["blocking"]))
    return 1 if res["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
