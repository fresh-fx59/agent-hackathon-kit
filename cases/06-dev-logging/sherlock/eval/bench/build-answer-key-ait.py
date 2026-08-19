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
import calendar
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


# --------------------------------------------------------------------------
# THE SAME EVENT IN A SECOND RENDERING
# --------------------------------------------------------------------------
# `host_twins` above credits the OTHER MACHINE's copy of a labelled line. This is
# the other half of the same unfairness: ONE machine writing ONE stream into
# SEVERAL FILES. The negative-control corpus ships three text renderings of one
# journald stream — `mon/journal/journal.short-iso`, `mon/journal/journal.json`
# and `mon/syslog/syslog.tail` — and the v22 arm made finding R09 correctly while
# citing the journald render against a key whose proof lives in the syslog
# render. Right event, wrong file, scored as a miss.
#
# Same discipline as the cross-host rule, held verbatim: COMPUTE the equivalence,
# never assert it.
#
#   * the key is (second, host, `ident[pid]: message`), all three required;
#   * the ONE normalisation is timestamp PRECISION — `journalctl -o short-iso`
#     prints seconds and rsyslog prints microseconds, so precision is a property
#     of the RENDERING and not of the event. Timezone offsets are resolved to a
#     UTC second for the same reason;
#   * that loosening is paid for by the EQUAL-MULTIPLICITY GATE: a key maps only
#     where both files agree how many times it occurred in that second, and then
#     rank maps to rank. Where they disagree the rule refuses and counts the
#     refusal. Nothing is guessed;
#   * the PID is NOT stripped, unlike the cross-host rule. Across machines a PID
#     cannot match by construction; across renderings of one machine's stream it
#     MUST, so keeping it is free strictness;
#   * two files in different TIMEBASES never compare. A BSD syslog line carries
#     no year, so `Jan 24 03:56:47` and `2022-01-24T03:56:47Z` are refused rather
#     than assumed to be the same January.
#
# What the rule cannot do is written down instead of worked around.
RENDERING_LIMITATIONS = [
    "journalctl -o export writes ONE event as MANY physical lines (a `__CURSOR=` "
    "block, with binary fields carrying a raw length prefix). A citation "
    "addresses one physical line, so crediting a block would mean picking a line "
    "and calling it the event. The format is declared out of scope; a corpus "
    "whose only second rendering is an export loses nothing it could have cited.",
    "The rule is line-for-line and one-directional per defect: it credits the "
    "other rendering's copy of a labelled line, and says nothing about lines that "
    "rendering holds and this one does not.",
    "A binary journal (`logs/journal/<machine-id>/system.journal`) is not a "
    "rendering this rule can read, and citecheck refuses it as binary anyway — it "
    "has no physical line for a report to cite.",
]

ISO_LINE_RE = re.compile(
    r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?"
    r"(Z|[+-]\d\d:?\d\d)[ \t](\S+)[ \t](.*)$")
BSD_LINE_RE = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2} \d\d:\d\d:\d\d) (\S+) (.*)$")


def _utc_offset(tok):
    if tok == "Z":
        return 0
    tok = tok.replace(":", "")
    sign = 1 if tok[0] == "+" else -1
    return sign * (int(tok[1:3]) * 3600 + int(tok[3:5]) * 60)


def render_line(line):
    """One physical line -> (timebase, second, host, body), or None.

    `None` means "this line is in no rendering the rule can read" — never "no
    match". Only formats that put ONE event on ONE physical line are recognised,
    because that is the unit a citation addresses.
    """
    line = line.rstrip("\n")
    m = ISO_LINE_RE.match(line)
    if m:
        sec = calendar.timegm((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               int(m.group(4)), int(m.group(5)), int(m.group(6)),
                               0, 0, 0)) - _utc_offset(m.group(7))
        return ("epoch", sec, m.group(8), m.group(9))
    m = BSD_LINE_RE.match(line)
    if m:
        # No year in this form, so it gets its own timebase and can never be
        # compared with an epoch one. Saying that out loud is the point.
        return ("bsd", m.group(1), m.group(2), m.group(3))
    if line[:1] == "{":
        try:
            d = json.loads(line)
        except ValueError:
            return None
        ts, msg = d.get("__REALTIME_TIMESTAMP"), d.get("MESSAGE")
        if ts is None or msg is None:
            return None
        if not isinstance(msg, str):          # journald renders binary as a list
            return None
        ident = d.get("SYSLOG_IDENTIFIER") or d.get("_COMM") or ""
        pid = d.get("_PID")
        body = ("%s[%s]: %s" % (ident, pid, msg)) if pid else ("%s: %s"
                                                               % (ident, msg))
        return ("epoch", int(ts) // 1000000, d.get("_HOSTNAME"), body)
    return None


def render_stream(path, cache=None):
    """-> (timebase, [key-or-None per physical line]) or (None, []) if the file
    is in no recognised rendering. A file counts as recognised when a majority of
    its non-blank lines parse AND they agree on one timebase."""
    if cache is not None and path in cache:
        return cache[path]
    keys, bases, seen = [], collections.Counter(), 0
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                k = render_line(line)
                keys.append(k)
                if line.strip():
                    seen += 1
                if k:
                    bases[k[0]] += 1
    except OSError:
        keys, bases, seen = [], collections.Counter(), 0
    if not bases or sum(bases.values()) * 2 < seen or len(bases) > 1:
        out = (None, [])
    else:
        out = (list(bases)[0], keys)
    if cache is not None:
        cache[path] = out
    return out


def rendering_map(base, rel_a, rel_b, cache=None):
    """{line in A: [lines in B]} for the SAME events, plus why it refused.

    The equal-multiplicity gate lives here, and it is the whole safety property.
    """
    ba, ka = render_stream(os.path.join(base, rel_a), cache)
    bb, kb = render_stream(os.path.join(base, rel_b), cache)
    if ba is None or bb is None:
        which = rel_a if ba is None else rel_b
        return {"map": {}, "refused_keys": 0,
                "why": "%s is in no rendering this rule can read" % which}
    if ba != bb:
        return {"map": {}, "refused_keys": 0,
                "why": "different timebase (%s vs %s): a BSD syslog line carries "
                       "no year and cannot be compared with an epoch one" % (ba, bb)}
    ia, ib = collections.defaultdict(list), collections.defaultdict(list)
    for i, k in enumerate(ka, 1):
        if k:
            ia[k[1:]].append(i)
    for i, k in enumerate(kb, 1):
        if k:
            ib[k[1:]].append(i)
    out, refused = {}, 0
    for k, rows in ia.items():
        other = ib.get(k)
        if not other:
            continue
        if len(other) != len(rows):
            refused += 1
            continue
        for r, line in enumerate(rows):
            out[line] = [other[r]]
    return {"map": out, "refused_keys": refused, "why": None}


def rendering_twins(base, rel):
    """Other files under the SAME host directory. Path arithmetic, not a list.

    A file under a DIFFERENT host is the cross-host rule's business and is left
    to it: that rule is stricter (it drops the PID and must therefore keep the
    hostname), and running both over one pair would credit a line twice.
    """
    host, _sep, tail = rel.partition("/")
    if not tail:
        return []
    root = os.path.join(base, host)
    out = []
    for dp, _d, fs in os.walk(root):
        for fn in sorted(fs):
            p = os.path.join(dp, fn)
            other = os.path.relpath(p, base).replace(os.sep, "/")
            if other != rel:
                out.append(other)
    return sorted(out)


PROBE_LINES = 400


def render_probe(path, limit=PROBE_LINES):
    """The timebase of a file's first `limit` lines, or None. A cheap door: a
    641 MB suricata eve.json is JSON with no `__REALTIME_TIMESTAMP`, and reading
    it whole to learn that costs a minute per host. The probe can only make the
    rule read FEWER files, never map more of them — `rendering_map` re-reads and
    re-decides for every candidate that gets through.
    """
    bases, seen = collections.Counter(), 0
    try:
        with open(path, errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if i > limit:
                    break
                if line.strip():
                    seen += 1
                k = render_line(line)
                if k:
                    bases[k[0]] += 1
    except OSError:
        return None
    if not bases or sum(bases.values()) * 2 < seen or len(bases) > 1:
        return None
    return list(bases)[0]


def rendering_alternates_for(base, rel, lines, cache=None):
    """-> {other_rel: sorted line numbers} holding the SAME events."""
    want = set(lines)
    out = {}
    mine = render_probe(os.path.join(base, rel))
    if mine is None:
        return out
    for other in rendering_twins(base, rel):
        if render_probe(os.path.join(base, other)) != mine:
            continue
        m = rendering_map(base, rel, other, cache)
        hit = sorted({n for src, ns in m["map"].items() if src in want
                      for n in ns})
        if hit:
            out[other] = hit
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
    rend_cache, rend_defects, rend_lines = {}, 0, 0
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
        # THE SECOND RENDERING of this same host's stream. Same discipline,
        # different unfairness — and on this corpus it finds nothing, which is
        # recorded rather than treated as a reason to loosen the rule.
        rends = rendering_alternates_for(os.path.join(root, "gather"),
                                         g["file"], g["lines"], rend_cache)
        for other in sorted(rends):
            for lo, hi in runs_of(rends[other]):
                alt_locs.append({"file": other, "line_start": lo, "line_end": hi})
        if rends:
            rend_defects += 1
            rend_lines += sum(len(v) for v in rends.values())
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
            "rendering_rule": (
                "ONE machine can write ONE stream into SEVERAL FILES — journald "
                "short-iso, journald JSON and an rsyslog tail are three renderings "
                "of the same events, and a report that read the wrong one was "
                "scoring a miss for a finding it had made. A second location is "
                "credited only where the SAME EVENT is provable: same second, "
                "same host, same `ident[pid]: message`. The ONE normalisation is "
                "timestamp PRECISION — seconds versus microseconds is a property "
                "of the rendering, not of the event — and it is paid for by an "
                "equal-multiplicity gate: a key maps only where both files agree "
                "how many times it occurred in that second, and then rank maps to "
                "rank. The PID is NOT dropped here, unlike the cross-host rule: "
                "across renderings of one machine's stream it must match. Files in "
                "different timebases (a BSD syslog line carries no year) never "
                "compare. Candidates are other files under the same host "
                "directory, found by path arithmetic."),
            "rendering_alternates": rend_defects,
            "rendering_alternate_lines": rend_lines,
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
                "SECOND RENDERINGS: the rendering rule finds NOTHING on this "
                "corpus and that is the measurement, not a reason to loosen it. "
                "Debian keeps auth, kern, dnsmasq and syslog in disjoint "
                "facilities, so no two text files here hold the same event: "
                "measured 0 of 275,900 dnsmasq.log lines in any of syslog.1-4, "
                "kern.log or auth.log, and 0 of auth.log's 272 in syslog.1-4. The "
                "only journald copy AIT ships is a BINARY "
                "logs/journal/<machine-id>/system.journal, which has no physical "
                "line for a report to cite and which citecheck rejects as binary. "
                "The rule is exercised for real on the fleet corpus, where three "
                "text renderings of one journald stream do exist.",
            ] + RENDERING_LIMITATIONS[:1],
            "decoys": "none — see `notes`",
        },
        "defects": defects,
    }
    return key


def renderings_report(corpus, keypath, out=None):
    """What the rendering rule would ADD to a key it does not own.

    The hand-over path. `answer-key-fleet-negative.json` is another agent's file
    and R09 is its defect; this computes the alternates for any key + corpus,
    prints them and writes a patch. It never writes the key it was pointed at —
    adopting the patch is the owner's call, and it should be their builder that
    recomputes it rather than a one-off edit.
    """
    raw = json.load(open(keypath, encoding="utf-8"))
    defects = raw.get("defects") or []
    if isinstance(defects, dict):
        defects = [dict(v, id=k) for k, v in defects.items()]
    cache, patch = {}, {}
    total_lines = 0
    print("rendering equivalence — corpus %s" % os.path.abspath(corpus))
    print("key %s (%d defect(s))" % (os.path.abspath(keypath), len(defects)))
    for d in defects:
        cid = d.get("id") or d.get("case_id")
        byfile = collections.defaultdict(set)
        for loc in (d.get("proof_locations") or []):
            f = loc.get("file")
            lo = loc.get("line_start")
            hi = loc.get("line_end", lo)
            if not f or lo is None:
                continue
            byfile[f] |= set(range(int(lo), int(hi) + 1))
        locs = []
        for rel in sorted(byfile):
            got = rendering_alternates_for(corpus, rel, byfile[rel], cache)
            for other in sorted(got):
                for a, b in runs_of(got[other]):
                    locs.append({"file": other, "line_start": a, "line_end": b})
                total_lines += len(got[other])
        if locs:
            patch[cid] = locs
            print("  %-6s +%d run(s) in %s"
                  % (cid, len(locs), ", ".join(sorted({l["file"] for l in locs}))))
    print("  %d of %d defect(s) gain a second rendering, %d line(s) in total"
          % (len(patch), len(defects), total_lines))
    if not patch:
        print("  nothing to add — either the corpus ships one rendering per "
              "stream, or the renderings are in a form this rule refuses "
              "(see RENDERING_LIMITATIONS). That is a measurement, not a bug.")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(patch, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        print("wrote %s — the KEY was not touched" % out)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="extracted AIT-LDS dir (has gather/ and labels/)")
    ap.add_argument("--corpus", required=True, help="sanitized corpus root the "
                                                    "analyst is pointed at")
    ap.add_argument("--dataset", default="ait-russellmitchell")
    ap.add_argument("--out")
    ap.add_argument("--renderings", action="store_true",
                    help="do not build: compute what the rendering rule would add "
                         "to --key against --corpus, print it and write a patch to "
                         "--out. The key is never modified.")
    ap.add_argument("--key", help="with --renderings: any answer key to report on")
    a = ap.parse_args()
    if a.renderings:
        if not a.key:
            ap.error("--renderings needs --key")
        return renderings_report(a.corpus, a.key, a.out)
    if not a.root or not a.out:
        ap.error("--root and --out are required to build the key")
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
    print("  second renderings:     %d defect(s), %d line(s) — same second, same "
          "host, same ident[pid]: message, equal multiplicity"
          % (key["derivation"]["rendering_alternates"],
             key["derivation"]["rendering_alternate_lines"]))
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
