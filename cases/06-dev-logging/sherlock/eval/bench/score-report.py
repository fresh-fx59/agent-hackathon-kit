#!/usr/bin/env python3
"""score-report.py — how good is the REPORT, in one record.

    # free: no judge, no network, no money. This is the default on purpose.
    python3 score-report.py --key answer-key-bluesky.json \
        --corpus /path/to/corpus --report work/report.md

    # add the judged column (cliproxyapi broker, subscription — never linkapi)
    JUDGE_API_KEY=... python3 score-report.py ... --judge

    # or pull the report out of the run ledger, both delivery channels united
    python3 score-report.py --key answer-key.json --corpus … --ledger runs-bench.jsonl \
        --dataset bench649 --arm v11 --trace 20260802T221034Z

Everything this project measured before now scored a WORKLIST — Step 1's 250-row
attention budget. That is an upper bound on what the skill can find and says
nothing about what it told the reader. The deliverable is a report; this scores
one, on four axes that are deliberately never summed into a single number.

    verdict    compromised / attacked-not-proven / clean, or `absent`
    anchored   did the report CITE this defect's proof?      ← free, primary
    asserted   did the report CLAIM this defect?             ← judged, optional
    citations  do the cited lines say what the report says?  ← free, v16 citecheck

WHY `anchored` IS THE PRIMARY FINDINGS NUMBER. Every real defect in the key
carries `proof_locations`. Whether the report put one of them in front of the
reader is path-and-integer arithmetic against the corpus index: no judge, no
token, no money, and therefore replayable over every trajectory this project has
ever stored. It is also the number a judge cannot drift on.

WHY IT IS CALLED `anchored` AND NOT `found`. Citing the right line is not
understanding it. A report can quote the exact line that proves the intrusion and
conclude the box is clean. `asserted` is the separate, judged column, and the two
never share a cell. When no judge ran, `asserted` is None — never 0. An unasked
question recorded as a negative answer is the same class of lie as an unmeasured
cost recorded as 0.

WHY DECOYS GET THEIR OWN TWO NUMBERS. `decoys_anchored` and `decoys_asserted`
never touch the findings numerator, and the denominator is real defects only —
otherwise a report that names everything it sees beats one that discriminates.
This is the number the field actually reports: OpenSec measures FP rate directly,
and Sonnet 4.6's 100 % containment comes with a 92.5 % false-positive rate. The
negative control (`answer-key-fleet-negative.json`) has NO real defects at all, so
these two are the only numbers it produces.

WHY AN AMBIGUOUS CITATION ANCHORS NOTHING. v16 made ambiguity fail closed inside
citecheck because a corpus that ships `auth.log` on ten hosts turns a bare
`auth.log:8977` into a confident claim about a machine the report never named.
Anchoring is a second door into the same room, so it obeys the same rule.

REUSE, DON'T FORK. `score-verdict.py` decides the verdict, `score-bench.py`
decides the judged column (including the inverted decoy prompt), `deliverable.py`
decides what a run actually handed over, and `skills/v16/tools/citecheck.py`
decides citation integrity. A second copy of any of them is a second,
incomparable scale.

FAIL LOUD. Missing corpus, empty report, judge transport failure: all raise. A
transport error recorded as "not found" is indistinguishable from a real miss.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.dirname(os.path.dirname(HERE))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# v16, named explicitly. `tools/citecheck.py` in the working tree is a v5-v10
# snapshot with no `ambiguous` verdict at all — loading it would silently delete
# the one column this scorer promises to keep visible. Pinning the path is the
# whole guard; `citecheck_sha` in the record is the receipt.
CITECHECK_PATH = os.path.join(SHERLOCK, "skills", "v16", "tools", "citecheck.py")
citecheck = _load("citecheck_v16", CITECHECK_PATH)
score_case = _load("score_case", os.path.join(SHERLOCK, "measure", "score_case.py"))
score_bench = _load("score_bench", os.path.join(HERE, "score-bench.py"))
score_verdict = _load("score_verdict", os.path.join(HERE, "score-verdict.py"))
deliverable = _load("deliverable", os.path.join(SHERLOCK, "measure", "deliverable.py"))


def _probe_judge_limit():
    """How many characters of the report the judge actually sees — MEASURED.

    `score_case.build_prompt` slices the report and the number lives there, not
    here. Hard-coding 120000 in a second file is how a cap starts lying after
    somebody edits the first one, so it is probed instead: feed a long run of one
    character and see how much of it survives into the prompt.
    """
    probe = "A" * 200000
    p = score_case.build_prompt({"case_id": "PROBE", "title": "", "root_cause": ""},
                                probe)
    runs = re.findall(r"A+", p)
    return max((len(r) for r in runs), default=0)


JUDGE_PROMPT_LIMIT = _probe_judge_limit()


# --------------------------------------------------------------------------
# what the key says the proof is
# --------------------------------------------------------------------------
def proof_spans(d):
    """One defect -> [(relpath, line_lo, line_hi)]; (relpath, None, None) = whole file.

    Two key shapes are real and both ship in this directory. `answer-key.json` and
    `answer-key-bluesky.json` carry a list of `proof_locations`
    ({file, line_start, line_end, …}). `answer-key-fleet-negative.json` — the only
    corpus whose right answer is the middle verdict — carries no proof locations
    at all: each decoy has a single `anchor` string, sometimes `path:line` and
    sometimes a bare path. A scorer that reads only the first shape cannot score
    the one run that separates an investigator from an alarm.
    """
    out = []
    for pl in (d.get("proof_locations") or []):
        f = (pl.get("file") or "").replace("\\", "/").lstrip("./").strip()
        if not f:
            continue
        lo = pl.get("line_start")
        if lo is None:
            out.append((f, None, None))
            continue
        hi = pl.get("line_end")
        out.append((f, int(lo), int(hi if hi is not None else lo)))
    if out:
        return out
    a = d.get("anchor")
    if isinstance(a, str) and a.strip():
        s = a.strip().replace("\\", "/").lstrip("./")
        head, sep, tail = s.rpartition(":")
        if sep and head and tail.isdigit():
            return [(head, int(tail), int(tail))]
        return [(s, None, None)]
    return []


# --------------------------------------------------------------------------
# what the report actually pointed at
# --------------------------------------------------------------------------
def cited_spans(report, root):
    """Every citation in the report, resolved to ONE corpus file, as a line span.

    Reuses citecheck's own extractor, corpus index and resolver — the same three
    functions that decide citation integrity — so "the report cited X" means
    exactly one thing in this project instead of two.

    Ambiguity is dropped, counted and printed. So is a citation that resolves to
    nothing. Neither is silently folded into "the report did not cite it": a
    report that names ten hosts' `auth.log` without saying which has a real defect
    and it is a different one from missing the evidence.
    """
    by_rel, by_base = citecheck.index_corpus(root)
    if not by_rel:
        raise RuntimeError("corpus index is empty: %s holds no files. Scoring a "
                           "report against an absent corpus would report every "
                           "citation as unresolved and every defect as missed."
                           % root)
    spans, ambiguous, unresolved, capped = [], [], [], []
    for c in citecheck.extract(report):
        cand, how = citecheck.resolve(c["path"], by_rel, by_base)
        if cand and how.endswith("ambiguous"):
            ambiguous.append({"citation": c["raw"], "candidates": len(cand)})
            continue
        if not cand:
            # citecheck's own gate, reused rather than reinvented: a token is
            # worth reporting as a LOST citation only if its last segment reads
            # like a filename. `11:00` and `10.42.12.20:8080` match the citation
            # regex and resolve to nothing, and counting them made the warning
            # say "15 citations resolved to no file" about a report whose real
            # count was 6. A noisy warning is one nobody reads.
            if citecheck.looks_like_path(c["path"]):
                unresolved.append(c["raw"])
            continue
        lo = c["line"]
        asked = c["range_end"] or lo
        hi = min(asked, lo + citecheck.MAX_RANGE)
        if asked > hi:
            capped.append({"citation": c["raw"], "asked_to": asked, "read_to": hi})
        spans.append((cand[0], lo, max(lo, hi)))
    return {"spans": spans, "ambiguous": ambiguous, "unresolved": unresolved,
            "capped": capped, "files": by_rel}


def _overlaps(span, plo, phi):
    _, lo, hi = span
    if plo is None:                      # a whole-file anchor
        return True
    return lo <= phi and plo <= hi


def anchor_hits(spans, proofs):
    """How many of this defect's proof locations the report pointed at.

    Every proof location inside a cited RANGE counts, not just the first. A
    citation is an address the reader is told to read around, so all of it is in
    front of them — the same rule score-ait.py had to be corrected into.
    """
    n = 0
    for (f, plo, phi) in proofs:
        if any(rel == f and _overlaps((rel, lo, hi), plo, phi)
               for (rel, lo, hi) in spans):
            n += 1
    return n


# --------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------
def score(raw_key, report, root, call=None, min_overlap=0.34, min_tokens=3,
          require_quote=False):
    """One report + one key + one corpus -> one record. `call` is the judge, or None."""
    if not os.path.isdir(root):
        raise RuntimeError("no such corpus directory: %s" % root)
    if not (report or "").strip():
        raise RuntimeError("the report is empty — nothing to score. An empty "
                           "deliverable is a delivery defect, and recording it as "
                           "0-of-N findings would make it look like a bad "
                           "investigation instead of an absent one.")

    key = score_bench.load_key(raw_key)
    dataset = raw_key.get("dataset") if isinstance(raw_key, dict) else None

    # --- axis 1: the verdict, delegated whole -----------------------------
    truth = raw_key.get("verdict") if isinstance(raw_key, dict) else None
    got, how = score_verdict.extract(report)
    verdict = {"truth": truth, "reported": got, "read_from": how,
               "correct": (got == truth) if truth else None}

    # --- axis 2: anchoring, free ------------------------------------------
    cited = cited_spans(report, root)
    spans = cited["spans"]
    per = {}
    unanchorable, missing_proof_files = [], set()
    anchored = anchorable = decoys_anchored = decoys = 0
    for cid in sorted(key):
        d = dict(key[cid])
        d.setdefault("case_id", cid)
        herring = score_bench.is_herring(d)
        proofs = proof_spans(d)
        for (f, _lo, _hi) in proofs:
            if f not in cited["files"]:
                missing_proof_files.add(f)
        hits = anchor_hits(spans, proofs) if proofs else 0
        is_anchored = bool(proofs) and hits > 0
        per[cid] = {"defect": cid, "herring": herring,
                    "title": d.get("title", ""),
                    "proof_locations": len(proofs),
                    "anchorable": bool(proofs),
                    "anchored": is_anchored, "anchor_hits": hits,
                    "asserted": None, "why": None}
        if herring:
            decoys += 1
            decoys_anchored += 1 if is_anchored else 0
            continue
        if not proofs:
            unanchorable.append(cid)
            continue
        anchorable += 1
        anchored += 1 if is_anchored else 0

    # --- axis 3: citation integrity, free ---------------------------------
    cc = citecheck.check(report, root, min_overlap, min_tokens, require_quote)
    ccs = dict(cc["summary"])

    # --- axis 4: the judged column, opt-in --------------------------------
    asserted = decoys_asserted = total_real = None
    if call is not None:
        dropped = max(0, len(report) - JUDGE_PROMPT_LIMIT)
        if dropped:
            print("⚠ JUDGE TRUNCATION: the report is %d chars and the judge prompt "
                  "carries %d — %d chars dropped, unseen by every judged verdict "
                  "below. The free columns above read the WHOLE report."
                  % (len(report), JUDGE_PROMPT_LIMIT, dropped))
        jres = score_bench.score(raw_key, report, call)
        asserted = jres["found"]
        total_real = jres["total"]
        decoys_asserted = jres["false_positives"]
        for r in jres["rows"]:
            if r["defect"] in per:
                per[r["defect"]]["asserted"] = bool(r["found"])
                per[r["defect"]]["why"] = r.get("why")
    else:
        total_real = sum(1 for cid in key if not score_bench.is_herring(key[cid]))

    rec = {
        "dataset": dataset,
        "corpus": os.path.abspath(root),
        "corpus_files": len(cited["files"]),
        "report_chars": len(report),
        "verdict": verdict,
        "anchored": anchored, "anchorable": anchorable,
        "anchored_pct": (round(100.0 * anchored / anchorable, 1)
                         if anchorable else None),
        "asserted": asserted, "total_real": total_real,
        "asserted_pct": (round(100.0 * asserted / total_real, 1)
                         if (asserted is not None and total_real) else None),
        "decoys": decoys, "decoys_anchored": decoys_anchored,
        "decoys_asserted": decoys_asserted,
        "unanchorable": unanchorable,
        "proof_files_absent_from_corpus": sorted(missing_proof_files),
        "ambiguous_citations": len(cited["ambiguous"]),
        "unresolved_citations": len(cited["unresolved"]),
        "capped_citations": cited["capped"],
        "citecheck": ccs,
        "citecheck_version": "v16",
        "citecheck_sha": hashlib.sha1(
            open(CITECHECK_PATH, "rb").read()).hexdigest(),
        "judged": call is not None,
        "judge_model": score_case.JUDGE_MODEL if call is not None else None,
        "judge_prompt_limit": JUDGE_PROMPT_LIMIT if call is not None else None,
        "per_defect": [per[cid] for cid in sorted(per)],
    }
    render(rec, cited)
    return rec


# --------------------------------------------------------------------------
# the human table
# --------------------------------------------------------------------------
def render(rec, cited):
    v = rec["verdict"]
    print("dataset   : %s" % rec["dataset"])
    print("corpus    : %s (%d files)" % (rec["corpus"], rec["corpus_files"]))
    print("report    : %d chars" % rec["report_chars"])
    if v["truth"]:
        mark = "✓" if v["correct"] else "✗"
        print("verdict   : truth=%s  reported=%s  %s"
              % (v["truth"], v["reported"], mark))
    else:
        print("verdict   : this key declares no verdict — NOT SCORED "
              "(reported=%s)" % v["reported"])
    print("            read from: %s" % v["read_from"])

    pct = "—" if rec["anchored_pct"] is None else "%.0f %%" % rec["anchored_pct"]
    print("anchored  : %d / %d real defects (%s)   ← judge-free, cites the proof"
          % (rec["anchored"], rec["anchorable"], pct))
    if rec["asserted"] is None:
        print("asserted  : — / %s               ← NOT MEASURED (no judge ran; "
              "this is None, not 0)" % rec["total_real"])
    else:
        ap = "—" if rec["asserted_pct"] is None else "%.0f %%" % rec["asserted_pct"]
        print("asserted  : %d / %d real defects (%s)   ← judged by %s"
              % (rec["asserted"], rec["total_real"], ap, rec["judge_model"]))
    da = "—" if rec["decoys_asserted"] is None else str(rec["decoys_asserted"])
    print("decoys    : anchored %d / %d · asserted %s / %d   ← false positives, "
          "never in the numerator above"
          % (rec["decoys_anchored"], rec["decoys"], da, rec["decoys"]))

    s = rec["citecheck"]
    vp = "—" if s.get("verified_pct") is None else "%.1f %%" % s["verified_pct"]
    print("citations : %d total — ok %d (%s), wrong-content %d, out-of-range %d, "
          "missing-file %d, no-quote %d, ambiguous %d, binary-file %d, "
          "unverifiable %d; не-ссылок %d"
          % (s.get("total", 0), s.get("ok", 0), vp, s.get("wrong-content", 0),
             s.get("out-of-range", 0), s.get("missing-file", 0),
             s.get("no-quote", 0), s.get("ambiguous", 0),
             s.get("binary-file", 0), s.get("unverifiable", 0),
             s.get("не-ссылка", 0)))

    # Everything below is a thing that was dropped, capped or could not be
    # scored. It prints even when it is zero-cost to ignore, because a cap the
    # reader cannot see is the same as no cap at all.
    if rec["ambiguous_citations"]:
        print("⚠ %d ambiguous citation(s) anchored NOTHING — the path matched more "
              "than one file, so it cannot be attributed to a host:"
              % rec["ambiguous_citations"])
        for a in cited["ambiguous"][:8]:
            print("    %s → %d candidates" % (a["citation"], a["candidates"]))
        if len(cited["ambiguous"]) > 8:
            print("    … and %d more" % (len(cited["ambiguous"]) - 8))
    if rec["unresolved_citations"]:
        print("⚠ %d citation(s) resolved to no file in this corpus: %s"
              % (rec["unresolved_citations"],
                 ", ".join(cited["unresolved"][:6])))
    for c in rec["capped_citations"]:
        print("⚠ SPAN CAPPED: %s asks to line %d, citecheck reads at most %d lines "
              "and stopped at %d. The rest was NOT checked and cannot anchor."
              % (c["citation"], c["asked_to"], citecheck.MAX_RANGE, c["read_to"]))
    if rec["unanchorable"]:
        print("⚠ %d real defect(s) carry no proof locations and left the anchoring "
              "denominator: %s"
              % (len(rec["unanchorable"]), ", ".join(rec["unanchorable"])))
    if rec["proof_files_absent_from_corpus"]:
        print("⚠ the key names %d proof file(s) that are NOT in this corpus — key "
              "and corpus may not match: %s"
              % (len(rec["proof_files_absent_from_corpus"]),
                 ", ".join(rec["proof_files_absent_from_corpus"][:6])))

    print()
    print("%-5s %-9s %-9s %-9s %s"
          % ("id", "kind", "anchored", "asserted", "title"))
    for r in rec["per_defect"]:
        kind = "DECOY" if r["herring"] else "real"
        if not r["anchorable"]:
            anc = "n/a"
        else:
            anc = "%s %d/%d" % ("✓" if r["anchored"] else "·",
                                r["anchor_hits"], r["proof_locations"])
        asr = "—" if r["asserted"] is None else ("✓" if r["asserted"] else "·")
        if r["herring"] and r["asserted"]:
            asr = "✗ FP"
        print("%-5s %-9s %-9s %-9s %s"
              % (r["defect"], kind, anc, asr, r["title"][:60]))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Score a report: verdict, anchored findings (free), asserted "
                    "findings (judged), citation integrity.")
    ap.add_argument("--key", required=True)
    ap.add_argument("--corpus", help="corpus root; defaults to the key's corpus_root")
    ap.add_argument("--report", help="a report FILE, or - for stdin")
    ap.add_argument("--ledger", help="take the report from this run ledger instead")
    ap.add_argument("--arm")
    ap.add_argument("--trace", help="substring of trace_dir — score THAT run")
    ap.add_argument("--dataset")
    ap.add_argument("--judge", action="store_true",
                    help="also fill the judged `asserted` column (needs "
                         "JUDGE_API_KEY; broker/subscription, never a metered API)")
    ap.add_argument("--min-overlap", type=float, default=0.34)
    ap.add_argument("--min-tokens", type=int, default=3)
    ap.add_argument("--require-quote", action="store_true")
    ap.add_argument("--label", help="free-text note recorded on the ledger row")
    ap.add_argument("--out", default=os.path.join(HERE, "scores-report.jsonl"))
    ap.add_argument("--json", action="store_true", help="also print the record")
    a = ap.parse_args()

    raw_key = json.load(open(a.key, encoding="utf-8"))
    root = a.corpus or raw_key.get("corpus_root")
    if not root:
        sys.exit("no --corpus and the key declares no corpus_root")
    if not os.path.isabs(root):
        # answer-key-fleet-negative.json stores a RELATIVE corpus_root while the
        # other two store absolute ones. Resolving it against cwd silently would
        # make the same command mean different things from different directories.
        cand = os.path.abspath(root)
        if not os.path.isdir(cand):
            sys.exit("the key's corpus_root %r is relative and does not resolve "
                     "from here (%s). Pass --corpus explicitly." % (root, os.getcwd()))
        print("note: key corpus_root %r is relative; resolved to %s" % (root, cand))
        root = cand

    if a.report and a.ledger:
        sys.exit("--report and --ledger are two different sources; pick one")
    if a.report:
        text = (sys.stdin.read() if a.report == "-"
                else open(a.report, encoding="utf-8", errors="replace").read())
        source = {"report_path": a.report, "arm": a.arm, "trace_dir": None,
                  "delivered_in": None}
    elif a.ledger:
        rows = [json.loads(l) for l in open(a.ledger, encoding="utf-8") if l.strip()]
        if not rows:
            sys.exit("no rows in %s" % a.ledger)
        run = score_bench.select_row(rows, a.arm, a.trace,
                                     a.dataset or raw_key.get("dataset"))
        score_bench.check_key_matches_dataset(raw_key, run)
        text = deliverable.of_row(run)
        source = {"report_path": None, "arm": run.get("arm"),
                  "trace_dir": run.get("trace_dir"),
                  "delivered_in": deliverable.channel_of_row(run)}
        if source["delivered_in"] == "file":
            print("⚠ DELIVERY: this run's report is in work/report.md, not in its "
                  "final message. The scores below measure the REPORT; delivery "
                  "is a separate, open defect.")
    else:
        sys.exit("give either --report or --ledger")

    call = None
    if a.judge:
        def call(prompt, _inner=score_case.http_call):
            """Retry the TRANSPORT, never the verdict — score-bench.py's rule.

            A dropped socket is not evidence about the report, so it is retried; a
            verdict that parses is used exactly once, because re-asking a judge
            until it agrees is how a score stops meaning anything.
            """
            last = None
            for attempt in range(1, 4):
                try:
                    return _inner(prompt)
                except Exception as e:                  # transport only
                    last = e
                    print("  ⚠ judge transport failed (attempt %d): %s"
                          % (attempt, str(e)[:120]))
                    time.sleep(2.0 * attempt)
            raise last

    rec = score(raw_key, text, root, call, a.min_overlap, a.min_tokens,
                a.require_quote)
    rec.update(source)
    rec["key"] = os.path.abspath(a.key)
    rec["label"] = a.label
    if a.json:
        print(json.dumps(rec, ensure_ascii=False, indent=1))
    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
