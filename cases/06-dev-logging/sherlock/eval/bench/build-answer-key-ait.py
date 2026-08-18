#!/usr/bin/env python3
"""build-answer-key-ait.py — derive an AIT-LDS answer key MECHANICALLY from the
shipped labels. Nobody authored the defects in `answer-key-ait-russellmitchell.json`;
this script did, and re-running it reproduces the file byte-for-byte.

    python3 build-answer-key-ait.py \
        --root /path/to/ait-lds-v2/extracted \
        --corpus /path/to/sanitized/ait-russellmitchell \
        --dataset ait-russellmitchell \
        --out answer-key-ait-russellmitchell.json

WHY THIS IS NOT AN AUTHORED KEY, AND WHY THAT MATTERS
-----------------------------------------------------
`answer-key.json` and `answer-key-bluesky.json` were written by a human (or by a
model under review) reading the corpus and deciding what the defects were. A
reader is entitled to ask whether the key was bent to fit the arm. Here it cannot
have been: AIT-LDS v2.1 ships `labels/<host>/logs/<path>` as one JSON object per
labelled line — `{"line": N, "labels": [...], "rules": {...}}` — and every number
below is a `groupby` over that file. Measured on the russellmitchell scenario:
**61,862 labelled lines across 8 files, 22 distinct label names.**

Line number is also exactly the currency `citecheck` and `score-report.py`'s
`anchored` axis already speak, so no translation layer was invented either.

HOW LABELS BECOME DEFECTS
-------------------------
1. **A fixed label→phase map** (`PHASE`, below) turns the 22 label names into six
   attack phases. The map is written out in full rather than inferred, because a
   clustering heuristic over 22 strings would be a second thing to trust.
   Three labels are deliberately NOT in it — `attacker`, `attacker_http` and
   `foothold`. They co-occur with every phase and mark *whose* line it is, not
   *what* the line is; letting them assign a phase would put all 61,862 lines in
   one bucket.
2. **A defect is one (file × phase).** Not one per file, and not one per phase.
   Per-file alone would merge `dirb`'s 4,462 scan lines with the 3 lines of
   `webshell_upload` that are the actual break-in, in the same `access.log.2`.
   Per-phase alone would merge `inet-firewall`'s DNS view of the escalation with
   `intranet_server`'s `auth.log` view, and a report that reached one host would
   score as if it reached both.
3. **Subset merge, to a fixpoint.** Within one file, a phase whose line set is a
   subset of another phase's line set is folded into it — including the mutual
   case, identical line sets. Otherwise a defect would exist that no report can
   miss while hitting its superset, which is a free point. Every merge this
   performed is printed and recorded in the key's `derivation.merges`.
4. **`proof_locations` are maximal contiguous runs** of labelled lines, so every
   line inside a proof span really is labelled. Collapsing a phase to a few wide
   ranges would credit unlabelled lines between the needles.

WHY NOT ONE POOLED RECALL NUMBER — THE SAME ARGUMENT `score-ait.py` MAKES
------------------------------------------------------------------------
The needle-to-haystack ratio spans three orders of magnitude:

    inet-firewall/logs/dnsmasq.log        54,035 labelled of 275,900   (19.6 %)
    intranet_server/.../access.log.2       7,695 labelled of   8,530   (90.2 %)
    intranet_server/logs/auth.log              8 labelled of     272   ( 2.9 %)
    intranet_server/logs/audit/audit.log       9 labelled of   2,316   ( 0.4 %)
    internal_share/logs/audit/audit.log        2 labelled of     732   ( 0.3 %)

`score-ait.py` refuses to pool these and counts FILES TOUCHED instead. This key
carries the same refusal into `score-report.py`'s own arithmetic, and it needs no
new scorer to do it: `anchored` counts DEFECTS, one per (file × phase), so the
8-line privilege escalation in `auth.log` is worth exactly what the 53,054-line
DNS exfiltration is worth. A report that finds only the loud thing scores 1 of N,
not 86 % — which is what a lines-based key would have printed.

DECOYS: THIS KEY HAS NONE, AND THAT IS A STATEMENT, NOT AN OMISSION
-------------------------------------------------------------------
`answer-key.json`, `answer-key-bluesky.json` and `answer-key-fleet-negative.json`
all carry red herrings, and `score-report.py` scores false positives off them.
AIT-LDS ships no red-herring labels. Deriving some would mean picking benign
events that *look* alarming and asserting they are not the intrusion — the second
half is mechanical (no label = not the attacker), but the first half is authoring,
and an authored decoy inside a mechanically-derived key is the one thing this file
must not contain. So `red_herrings` is 0, every `decoys_*` column
`score-report.py` prints on this key is 0/0, and **`decoys_anchored` is NOT
MEASURABLE on AIT.** False-positive discrimination is measured on the other three
keys; do not read this key's 0/0 as a clean bill.

A CORPUS PROPERTY WORTH KNOWING BEFORE READING ANY SCORE
--------------------------------------------------------
The DNS exfiltration is not the finale of the attack chain in this log — it is
present from the first line. `gather/inet-firewall/logs/dnsmasq.log:1` is
`Jan 21 00:00:09 … query[A] …customers_2017.xlsx.email-19.kennedy-mendoza.info
from 10.143.0.103`, already labelled `dnsteal`, and the same source runs ~4,900
queries a day on all four captured days (Jan 21: 4,962 · Jan 22: 4,948 ·
Jan 23: 4,929 · Jan 24: 2,854), while the recon→webshell→escalation chain is
confined to Jan 24 03:00–14:00. A report that presents the exfiltration as the
CONSEQUENCE of the Jan-24 foothold has the causality backwards, and no axis in
`score-report.py` can see that. Counted with:
`grep -oE '^Jan [0-9]+|kennedy-mendoza\\.info from [0-9.]+' … | uniq-count`.
"""
import argparse
import collections
import json
import os
import re
import sys

# 22 label names → 6 phases. `attacker`, `attacker_http`, `foothold` are
# context markers and map to nothing on purpose (see docstring).
PHASE = {
    "service_scan": "recon", "dns_scan": "recon", "network_scan": "recon",
    "traceroute": "recon", "dirb": "recon", "wpscan": "recon",
    "attacker_vpn": "vpn",
    "webshell_upload": "webshell", "webshell_cmd": "webshell",
    "escalate": "escalate", "escalated_command": "escalate",
    "escalated_sudo_command": "escalate", "escalated_sudo_session": "escalate",
    "attacker_change_user": "escalate",
    "crack_passwords": "cracking",
    "dnsteal": "exfil", "dnsteal-received": "exfil", "dnsteal-dropped": "exfil",
    "exfiltration-service": "exfil",
}
CONTEXT = {"attacker", "attacker_http", "foothold"}
ORDER = {"recon": 1, "vpn": 2, "webshell": 3, "escalate": 4, "cracking": 5,
         "exfil": 6}

TITLE = {
    "recon": "Reconnaissance / scanning traffic",
    "vpn": "Attacker VPN session",
    "webshell": "Web shell upload and command execution",
    "escalate": "Privilege escalation to root",
    "cracking": "Offline password cracking",
    "exfil": "DNS-tunnelled data exfiltration",
}


# --------------------------------------------------------------------------
# THE SAME EVENT ON A SECOND MACHINE
# --------------------------------------------------------------------------
# AIT labels ONE host's copy of traffic that two hosts logged. `inet-firewall`
# and `inet-dns` are a forwarding pair: every query that crossed the firewall
# also crossed the resolver, and the labels are only on the firewall. An analyst
# who proved the DNS story from `inet-dns/logs/dnsmasq.log` was right and scored
# a miss.
#
# The fix is COMPUTED, never asserted. Two lines are the same event if, and only
# if, their timestamp and their message body match byte for byte after removing
# the ONE field that can never match across machines: the syslog PID, which
# identifies a daemon instance on one box. Nothing else is normalised — the
# hostname least of all.
#
# That last sentence is the safety property and it is load-bearing. This testbed
# provisioned itself with `useradd[493]: new user: name=ait` in the SAME SECOND on
# 21 machines. A rule that also dropped the hostname would have called those 21
# lines one event and handed every `auth.log` defect twenty free alternates —
# exactly the key-loosening this pass exists to avoid. Because the hostname stays,
# `logs/auth.log` (present on ten hosts) gains NOTHING, and the only pair that
# matches is the one whose log format carries no hostname at all.
SYSLOG_TS_RE = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2} \d\d:\d\d:\d\d) (.*)$")
PID_RE = re.compile(r"\[\d+\]:")


def event_key(line):
    """(timestamp, message-body-without-the-pid), or None if this is not a
    syslog-framed line. `None` means "cannot be compared", never "no match"."""
    m = SYSLOG_TS_RE.match(line.rstrip("\n"))
    if not m:
        return None
    return (m.group(1), PID_RE.sub(":", m.group(2), count=1))


def host_twins(root, rel):
    """Files at the same path under a DIFFERENT host directory.

    `inet-firewall/logs/dnsmasq.log` -> `inet-dns/logs/dnsmasq.log`. Path
    arithmetic, not a list of pairs somebody wrote down.
    """
    gather = os.path.join(root, "gather")
    host, _sep, tail = rel.partition("/")
    if not tail:
        return []
    out = []
    for other in sorted(os.listdir(gather)) if os.path.isdir(gather) else []:
        if other == host:
            continue
        p = os.path.join(gather, other, tail)
        if os.path.isfile(p):
            out.append(other + "/" + tail)
    return out


def index_events(root, rel, cache):
    """{event_key: [line numbers]} for one file, read once."""
    if rel not in cache:
        idx = collections.defaultdict(list)
        p = os.path.join(root, "gather", rel)
        try:
            with open(p, errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    k = event_key(line)
                    if k:
                        idx[k].append(i)
        except OSError:
            pass
        cache[rel] = idx
    return cache[rel]


def alternates_for(root, rel, lines, cache):
    """-> {other_rel: sorted line numbers} that hold the SAME events."""
    twins = host_twins(root, rel)
    if not twins:
        return {}
    own = {}
    p = os.path.join(root, "gather", rel)
    want = set(lines)
    try:
        with open(p, errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if i in want:
                    k = event_key(line)
                    if k:
                        own[i] = k
    except OSError:
        return {}
    if not own:
        return {}
    keys = set(own.values())
    out = {}
    for t in twins:
        idx = index_events(root, t, cache)
        hit = set()
        for k in keys:
            hit |= set(idx.get(k, ()))
        if hit:
            out[t] = sorted(hit)
    return out


def load_labels(root):
    """-> {rel: {line: [labels]}} — one dict per shipped label file, verbatim."""
    out, lab = {}, os.path.join(root, "labels")
    if not os.path.isdir(lab):
        sys.exit("no labels/ under %s — this is not an extracted AIT-LDS root" % root)
    for dp, _d, fs in os.walk(lab):
        for fn in fs:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, lab).replace(os.sep, "/")
            marks = {}
            with open(p, errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if "line" in d:
                        marks[int(d["line"])] = d.get("labels") or []
            if marks:
                out[rel] = marks
    return out


def runs_of(lines):
    """Sorted line numbers -> maximal contiguous [(lo, hi)] runs."""
    ns = sorted(lines)
    out, s, prev = [], ns[0], ns[0]
    for n in ns[1:]:
        if n == prev + 1:
            prev = n
        else:
            out.append((s, prev))
            s = prev = n
    out.append((s, prev))
    return out


def file_lines(root, rel):
    p = os.path.join(root, "gather", rel)
    if not os.path.exists(p):
        return 0
    n = 0
    with open(p, errors="replace") as fh:
        for _ in fh:
            n += 1
    return n


def preview(root, rel, line, width=140):
    p = os.path.join(root, "gather", rel)
    try:
        with open(p, errors="replace") as fh:
            for i, t in enumerate(fh, 1):
                if i == line:
                    return t.rstrip("\n")[:width]
    except OSError:
        pass
    return ""


def build(root, corpus, dataset):
    labels = load_labels(root)
    groups, merges = [], []
    unmapped = collections.Counter()

    for rel in sorted(labels):
        byphase = collections.defaultdict(set)
        for n, ls in labels[rel].items():
            ph = {PHASE[l] for l in ls if l in PHASE}
            if not ph:
                for l in ls:
                    if l not in CONTEXT:
                        unmapped[l] += 1
                continue
            for x in ph:
                byphase[x].add(n)

        # Subset merge to a fixpoint, deterministic order. A merged group keeps
        # BOTH phase names: `crack_passwords` and `escalate` label the identical
        # 49 lines of the monitoring CPU log, and titling that group "privilege
        # escalation" alone would describe the evidence wrongly in the key
        # itself.
        phases = {p: set(v) for p, v in byphase.items()}
        held = {p: {p} for p in phases}
        changed = True
        while changed:
            changed = False
            for a in sorted(phases, key=lambda p: (len(phases[p]), p)):
                for b in sorted(phases, key=lambda p: (-len(phases[p]), p)):
                    if a == b or a not in phases or b not in phases:
                        continue
                    if phases[a] <= phases[b]:
                        merges.append({"file": rel, "absorbed": a, "into": b,
                                       "absorbed_lines": len(phases[a]),
                                       "into_lines": len(phases[b]),
                                       "identical": phases[a] == phases[b]})
                        phases[b] = phases[b] | phases[a]
                        held[b] = held[b] | held[a]
                        del phases[a]
                        del held[a]
                        changed = True
                        break
                if changed:
                    break

        for ph, lines in phases.items():
            names = collections.Counter()
            for n in lines:
                for l in labels[rel][n]:
                    names[l] += 1
            groups.append({
                "file": rel, "phase": ph, "phases": sorted(held[ph],
                                                           key=lambda x: ORDER[x]),
                "lines": lines,
                "labels": dict(names.most_common()),
                "total_lines": file_lines(root, rel),
            })

    if unmapped:
        sys.exit("labels with no phase and not in CONTEXT: %r — extend PHASE, do "
                 "not let them fall through silently" % dict(unmapped))

    groups.sort(key=lambda g: (ORDER[g["phase"]], g["file"]))

    defects = []
    ev_cache = {}
    alt_defects = alt_lines = 0
    for i, g in enumerate(groups, 1):
        rs = runs_of(g["lines"])
        first = rs[0][0]
        alts = alternates_for(root, g["file"], g["lines"], ev_cache)
        alt_locs = []
        for other in sorted(alts):
            for lo, hi in runs_of(alts[other]):
                alt_locs.append({"file": other, "line_start": lo, "line_end": hi})
        if alt_locs:
            alt_defects += 1
            alt_lines += sum(len(v) for v in alts.values())
        defects.append({
            "id": "A%02d" % i,
            "title": "%s — %s" % (" + ".join(TITLE[p] for p in g["phases"]),
                                  g["file"]),
            "file": g["file"],
            "phase": g["phase"],
            "phases": g["phases"],
            "difficulty": "%d labelled lines of %d in the file (%.2f %%)" % (
                len(g["lines"]), g["total_lines"],
                100.0 * len(g["lines"]) / g["total_lines"] if g["total_lines"] else 0),
            "root_cause": ("AIT-LDS v2.1 labels %d line(s) of %s with %s. Derived "
                           "mechanically from labels/%s; no human decided this."
                           % (len(g["lines"]), g["file"],
                              ", ".join("%s×%d" % (k, v)
                                        for k, v in list(g["labels"].items())[:6]),
                              g["file"])),
            "labels": g["labels"],
            "labelled_lines": len(g["lines"]),
            "file_total_lines": g["total_lines"],
            "first_labelled_line": first,
            "first_labelled_line_preview": preview(root, g["file"], first),
            "proof_locations": [{"file": g["file"], "line_start": lo,
                                 "line_end": hi} for lo, hi in rs],
            # The SAME events on another machine's copy — computed, not asserted.
            # Empty is a statement too: it means no other host logged this, or
            # logged it in a format the rule cannot compare.
            "alternate_proof_locations": alt_locs,
        })

    key = {
        "dataset": dataset,
        "scenario": ("AIT Log Data Set v2.1, scenario `russellmitchell` "
                     "(processed_russellmitchell_scenario, 2022-01-21 → 2022-01-25). "
                     "22-host enterprise testbed; a scripted multi-stage intrusion "
                     "against intranet_server plus continuous DNS exfiltration. "
                     "Ground truth is the dataset's own per-line labels, not an "
                     "author's reading of the logs."),
        "corpus_root": corpus,
        "verdict": "compromised",
        "verdict_rationale": (
            "The dataset ships positive labels for a web shell UPLOAD and 32 web "
            "shell COMMAND requests on intranet_server "
            "(apache2/intranet.smith.russellmitchell.com-access.log.2:8495-8529), "
            "followed by root-level escalation visible in two independent places on "
            "the same host — logs/auth.log:145-152 and logs/audit/audit.log:1860-1868 "
            "(escalated_sudo_command, attacker_change_user). Access was obtained and "
            "used, so the verdict is the first of the three, not the middle one."),
        "totals": {"real_defects": len(defects), "red_herrings": 0},
        "notes": (
            "NO RED HERRINGS. AIT-LDS ships none, and inventing some would make an "
            "authored claim inside a mechanically-derived key. `decoys_anchored` and "
            "`decoys_asserted` are therefore 0/0 on this key and are NOT MEASURABLE "
            "on it — read false-positive discrimination off answer-key.json, "
            "answer-key-bluesky.json or answer-key-fleet-negative.json instead."),
        "derivation": {
            "tool": "build-answer-key-ait.py",
            "source": "AIT-LDS v2.1 labels/<host>/logs/<path>, one JSON object per "
                      "labelled physical line",
            "labelled_lines": sum(len(v) for v in labels.values()),
            "label_files": len(labels),
            "distinct_label_names": len({l for v in labels.values()
                                         for ls in v.values() for l in ls}),
            "defect_rule": "one defect per (labelled file × attack phase), phases "
                           "from a fixed 19-label map; `attacker`, `attacker_http` "
                           "and `foothold` are context markers and assign no phase",
            "merge_rule": "within a file, a phase whose line set is a subset of "
                          "another phase's is folded into it (fixpoint)",
            "merges": merges,
            "proof_rule": "maximal contiguous runs of labelled lines",
            "cross_host_rule": (
                "AIT labels ONE host's copy of traffic two hosts logged. A second "
                "location is credited only where the SAME EVENT is provable: same "
                "timestamp, and message body byte-identical after removing the "
                "syslog PID `[N]` — the one field that cannot match across "
                "machines by construction. NOTHING else is normalised, the "
                "hostname least of all: this testbed wrote `useradd[493]: new "
                "user: name=ait` in the same second on 21 hosts, and a rule that "
                "dropped the hostname would have called those one event. "
                "Candidate files are found by path arithmetic (<other-host>/<same "
                "tail>), not from a list."),
            "alternate_defects": alt_defects,
            "alternate_lines": alt_lines,
            "known_limitations": [
                "audit.log: its lines are `type=SYSCALL msg=audit(<epoch>:<id>)` "
                "and carry no syslog timestamp, so `event_key` returns None for "
                "every one of them. `logs/audit/audit.log` exists on seven hosts "
                "and A07/A11 gain nothing — not because the events are unique, "
                "but because this rule cannot compare them. Not widened: an epoch "
                "-plus-serial identifier is per-host state, and matching on the "
                "message alone would drop the timestamp requirement entirely.",
                "apache access.log/error.log: `[24/Jan/2022:03:56:47 +0000]` is "
                "not the syslog timestamp form either, so A02/A03/A06 are "
                "uncomparable. They have no same-tail twin on another host in "
                "this corpus anyway (the path carries the vhost name), so nothing "
                "is lost today — but a corpus where two hosts served the same "
                "vhost would need the rule extended.",
                "The rule is line-for-line and one-directional per defect: it "
                "credits the other host's copy of a LABELLED line. It says "
                "nothing about lines the other host logged and this one did not.",
            ],
            "decoys": "none — see `notes`",
        },
        "defects": defects,
    }
    return key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="extracted AIT-LDS dir "
                                                  "(has gather/ and labels/)")
    ap.add_argument("--corpus", required=True, help="sanitized corpus root the "
                                                    "analyst is pointed at")
    ap.add_argument("--dataset", default="ait-russellmitchell")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    key = build(a.root, a.corpus, a.dataset)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(key, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    print("wrote %s" % a.out)
    print("  defects: %d   red herrings: %d   labelled lines: %d   label files: %d"
          % (key["totals"]["real_defects"], key["totals"]["red_herrings"],
             key["derivation"]["labelled_lines"], key["derivation"]["label_files"]))
    print("  cross-host alternates: %d defect(s), %d line(s) — same timestamp, "
          "same message body, PID removed and nothing else"
          % (key["derivation"]["alternate_defects"],
             key["derivation"]["alternate_lines"]))
    for m in key["derivation"]["merges"]:
        print("  merged %s(%d) into %s(%d)%s in %s"
              % (m["absorbed"], m["absorbed_lines"], m["into"], m["into_lines"],
                 " [identical]" if m["identical"] else " [subset]", m["file"]))
    print()
    print("  %-4s %-9s %-9s %-52s %-7s %s"
          % ("id", "phase", "lines", "file", "proofs", "alt"))
    for d in key["defects"]:
        alt = d["alternate_proof_locations"]
        print("  %-4s %-9s %-9d %-52s %-7d %s"
              % (d["id"], d["phase"], d["labelled_lines"], d["file"],
                 len(d["proof_locations"]),
                 ("%d run(s) in %s" % (len(alt), alt[0]["file"])) if alt else "—"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
