#!/usr/bin/env python3
"""build-answer-key-fleet-negative.py — rebuild the NEGATIVE CONTROL's answer key.

    python3 build-answer-key-fleet-negative.py \
        --corpus /path/to/sherlock-cyber-fleet/corpus \
        --out answer-key-fleet-negative.json

Until 2026-08-18 this key held eight decoys and nothing else, so `score-report.py`
printed `anchored 0/0` on it: it could score what a report should REFUSE and not one
thing a report should OBSERVE. The mirror image was true on AIT (11 real defects, no
decoys). Neither corpus could measure both halves of the instrument. This file adds
the missing half here.

WHY THIS IS NOT A KEY FITTED TO ITS OWN REPORT
----------------------------------------------
`build-answer-key-ait.py` can point at 61,862 shipped per-line labels and say nobody
authored its defects. This corpus ships no ground truth at all, so that defence is
not available, and a report on it already existed when these findings were written.
Three guards replace the missing labels:

1. **The observables come from the key's own VERDICT, not from any report.** The
   truth verdict here is `attacked-not-proven`, and that verdict is a conjunction of
   three claims. A real finding is admitted only if it carries one of them:

       attacked        hostile traffic demonstrably reached a host in this corpus
       not-succeeded   a counted outcome showing that traffic was refused or blocked
       not-proven      a counted limit on the evidence itself — telemetry that is
                       off, empty, or that records attempts without outcomes

   The third class is what separates this verdict from `clean`, and it is the one
   an authored key would most likely have skipped.

2. **Every number is produced by a command, and the command is RUN.** Each counted
   finding carries `command`, a one-liner executed with `cwd` = the corpus. The
   builder runs it and refuses to emit the key unless its output equals the number
   recorded in the finding (`--no-verify` turns that off and prints a warning). A
   command in a JSON file that nobody executes is decoration; this one is a gate.

3. **A finding may not stand on a decoy's evidence, and no two findings may share a
   line.** Both are enforced below and printed. Without the first, a citation to
   `mon/auth/auth.log` would score as a real finding AND a decoy at once — the exact
   degeneracy the negative control exists to avoid. `mon/auth/auth.log` therefore
   carries no real finding at all: it is D01/D02/D03's file, and the week-long sweep
   is counted in the PREVIOUS rotation instead.

WHAT IS STILL AUTHORED, SAID PLAINLY
------------------------------------
The numbers and the proof lines are counted. The CHOICE of the twelve observables, and
the prose in `title`/`root_cause`, are authored — a human decided that a firewall
dropping 13,303 packets is worth a finding and that Grafana's 64 `level=error` lines
are not. `provenance` on every entry says which of the two it is, and the eight
decoys are marked `authored` end to end.

THE OPERATOR IS NOT AN ATTACKER — 100.64.0.0/10
-----------------------------------------------
Every host here is on Tailscale, which assigns out of the CGNAT range 100.64.0.0/10.
The corpus's one successful SSH login (D02), the root shell after it (D03 — the
evidence collector itself) and contabo's three `Accepted publickey for root` all come
from 100.122.174.119. `TAILSCALE_RE` excludes that range from every hostile-traffic
count, so the collector can never be promoted into a finding.

BINARY EVIDENCE CANNOT BE A PROOF LOCATION — AND A GZIPPED TEXT LOG IS TEXT
---------------------------------------------------------------------------
The gate is unchanged in what it protects: no proof location may live in a file the
citation checker refuses to read, because a citation there can never be verified and
an unanchorable finding is a free point nobody can win. What changed on 2026-08-18 is
the *notion of binary* the gate uses.

The first derivation loaded `skills/v16/tools/citecheck.py`, whose `looks_binary`
read the RAW bytes. A gzip stream is full of NULs, so all seven `.gz` files in this
corpus were called binaries — while the very same module's `read_lines` opens them
with `gzip.open` and reads them perfectly. The gate was rejecting citations the tool
could verify, and this builder therefore refused to anchor anything in 109,708 lines
of evidence. v19 fixed `looks_binary` to test the DECOMPRESSED stream and v20 shipped
it; this file now loads v20, so 11 of the 58 files are binary here instead of 18.

The hole that left in the key was not cosmetic. `mon/auth/auth.log.2.gz` alone holds
9,851 `sshd[N]: Invalid user` lines against the 6,650 R01 used to count — the LARGEST
rotation of the sweep was outside the key, and R01 undercounted the volume it
describes by 2.5x. `mon/nginx/access.log.10.gz` holds 3,221 more web-shell probes
against R04's 1,670. And two findings the corpus always contained could not be
written at all: the kernel's SYN-flood mitigation on mon:15443 (**R11**, one line, in
`mon/syslog/kern.log.3.gz`) and contabo's archived nginx log recording every one of
its 1,153 requests as coming from 127.0.0.1 (**R12**).

The gate is also what fixed **D05**, whose anchor used to be `mon/utmp/btmp` itself:
`citecheck.extract` only recognises `path:line`, so anchoring that decoy required
citing a line inside a binary. `mon/utmp/btmp` is still binary under v20 — a real
utmp record file, not a compressed text log — so D05 still anchors on the MANIFEST
row that states the file's size and type, which is how a responder is supposed to
reference evidence they cannot read as text. **D04** likewise gained its second
`ANOM_PROMISCUOUS` record, which the old key names in prose but never anchored.
"""
import argparse
import collections
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.dirname(os.path.dirname(HERE))
CITECHECK = os.path.join(SHERLOCK, "skills", "v20", "tools", "citecheck.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


citecheck = _load("citecheck_v20", CITECHECK)

# Tailscale's CGNAT block. Anything sourced here is the operator or the collector.
TAILSCALE_RE = re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# The corpus was collected by the D03 root shell at this instant; every "how stale
# is this stream" number below is measured against it rather than against `now`, so
# the key does not change when it is rebuilt tomorrow.
COLLECTED_AT = "2026-08-18T12:06:16+00:00"

CLAIM_HALVES = {
    "attacked": "hostile traffic demonstrably reached a host in this corpus",
    "not-succeeded": "a counted outcome showing that traffic was refused or blocked",
    "not-proven": "a counted limit on the evidence itself",
}


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------
def read_text(path):
    """Open a corpus file the way `citecheck.read_lines` opens it — through gzip
    when the name says gzip.

    This is the whole of the 2026-08-18 `.gz` repair on the counting side. The
    builder used to call plain `open()`, so a `.gz` decoded into a few thousand
    lines of mojibake and no pattern in this file could match anything in it. The
    checker and the builder must address the SAME lines or a proof location is a
    number about one file and a citation is a number about another."""
    return citecheck.opener(path)(path, "rt", encoding="utf-8", errors="replace")


def scan(corpus, rel, pattern, drop_tailscale=False):
    """-> ([line numbers], [matched text]) for one file, one regex."""
    p = os.path.join(corpus, rel)
    rx = re.compile(pattern)
    nums, texts = [], []
    with read_text(p) as fh:
        for i, line in enumerate(fh, 1):
            if not rx.search(line):
                continue
            if drop_tailscale and TAILSCALE_RE.search(line):
                continue
            nums.append(i)
            texts.append(line.rstrip("\n"))
    return nums, texts


def runs_of(lines):
    """Sorted line numbers -> maximal contiguous [(lo, hi)] runs. AIT's rule."""
    if not lines:
        return []
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


def distinct(texts, pattern):
    rx = re.compile(pattern)
    seen = set()
    for t in texts:
        for m in rx.finditer(t):
            seen.add(m.group(1) if m.groups() else m.group(0))
    return seen


def iso_of(text):
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", text)
    return m.group(1) if m else None


def hours_between(a, b):
    fa = datetime.datetime.fromisoformat(a)
    fb = datetime.datetime.fromisoformat(b)
    return round((fb - fa).total_seconds() / 3600.0, 2)


def run_cmd(corpus, cmd):
    r = subprocess.run(["bash", "-c", cmd], cwd=corpus, capture_output=True,
                       text=True)
    if r.returncode not in (0, 1):
        raise RuntimeError("command failed (%d): %s\n%s"
                           % (r.returncode, cmd, r.stderr[:400]))
    return r.stdout.strip()


# --------------------------------------------------------------------------
# THE OBSERVABLES — the whole authored surface of the real-findings half, in
# one table. Each entry declares WHERE it looks, WHAT it counts, and the shell
# command that must reproduce its headline number.
# --------------------------------------------------------------------------
PROBE_RE = (r'("[A-Z]+ [^"]*(\.php|/wp-|\.env|phpmyadmin|\.git/|/vendor/|'
            r'/cgi-bin/|/shell|/config\.)|open\(\) "[^"]*\.php" failed)')
SSH_ATTEMPT_RE = (r"sshd(-session)?\[[0-9]+\]: (Invalid user |"
                  r"Connection closed by (invalid|authenticating) user |"
                  r"Connection reset by authenticating user |"
                  r"Disconnected from authenticating user )")

# R01's PROOF pattern (2026-08-18, second derivation). The first derivation proved
# R01 with `sshd[N]: Invalid user ` alone and recorded the consequence as a known
# limitation: one connection of this sweep emits four or five lines and only one of
# them says «Invalid user», so a report that cited a `Received disconnect … [preauth]`
# line had cited this exact finding and scored zero for it. That limitation named its
# own repair — «make R01 use the broad one, BEFORE the next arm runs, not after this
# one has been scored» — and this is that moment: the whole key is being re-derived
# because the `.gz` rotations became readable, no arm has run against the result, and
# widening now cannot be a fit to any report's citations.
#
# The HEADLINE stays the narrow `Invalid user ` count, because that is one line per
# rejected connection and therefore a number that means something. The proof set is
# the broad one, because every line below is the same sweep and a reader who lands on
# any of them has reached the finding. Proof wider than headline is the existing
# design here, not a new liberty: R08 counts an epoch and proves it with two lines,
# R10 counts two zero-byte files and proves it with syslogd's own chatter.
SSH_SWEEP_RE = (r"sshd(-session)?\[[0-9]+\]: ("
                r"Invalid user |"
                r"Connection closed by (invalid|authenticating) user |"
                r"Connection reset by (invalid|authenticating) user |"
                r"Disconnected from (invalid|authenticating) user |"
                r"Disconnecting (invalid|authenticating) user |"
                r"Received disconnect from |"
                r"error: maximum authentication attempts exceeded for |"
                r"error: kex_exchange_identification: |"
                r"banner exchange: Connection from )")
TS_GREP = r"\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+\b"

# mon's auth log has three rotations and they are contiguous: .2.gz covers
# 2026-08-02 → 08-09, .1 covers 08-09 → 08-16, and the live file covers 08-16 →
# collection. The live file is D01/D02/D03's, so R01 counts the other two — and
# until the `.gz` guard was fixed it could only count ONE of them.
R01_FILES = ["mon/auth/auth.log.2.gz", "mon/auth/auth.log.1"]   # oldest first
# mon's http log likewise: the live file is 18 Aug, the archive is 08–09 Aug.
R04_FILES = ["mon/nginx/access.log", "mon/nginx/error.log",
             "mon/nginx/access.log.10.gz"]
# and the Mac's syslog: 109 live lines, 620 more in two archives.
R10_MAC_SYSLOG = ["mac/syslog/system.log", "mac/syslog/system.log.0.gz",
                  "mac/syslog/system.log.1.gz"]


def cat_cmd(rels):
    """A shell one-liner that concatenates plain and gzipped files in one stream.

    `gzip -cd` rather than `zcat`, because macOS `zcat` still wants a `.Z`. This is
    the command a reader can paste, and the builder runs it."""
    plain = [r for r in rels if not r.endswith(".gz")]
    gz = [r for r in rels if r.endswith(".gz")]
    parts = []
    if plain:
        parts.append("cat " + " ".join(plain))
    parts.extend("gzip -cd " + r for r in gz)
    if len(parts) == 1 and plain:
        return parts[0]
    return "{ " + "; ".join(parts) + "; }"

JOURNAL_MIRRORS = ["journal.json", "journal.export"]


def obs_R01(c):
    n, t, srcs = [], [], set()
    for rel in R01_FILES:
        nn, tt = scan(c, rel, r"sshd\[[0-9]+\]: Invalid user ")
        n.extend(nn)
        t.extend(tt)
        srcs |= distinct(tt, r"from ((?:\d{1,3}\.){3}\d{1,3})")
    acc = sum(len(scan(c, rel, r"sshd\[[0-9]+\]: Accepted ")[0])
              for rel in R01_FILES)
    sweep = sum(len(scan(c, rel, SSH_SWEEP_RE, True)[0]) for rel in R01_FILES)
    bans = 0
    for rel in ("mon/auth/auth.log", "mon/auth/auth.log.1",
                "mon/auth/auth.log.2.gz", "mon/syslog/syslog.tail",
                "mon/journal/journal.short-iso", "mon/journal/journal.json"):
        bans += len(scan(c, rel, r"fail2ban|sshguard|denyhosts")[0])
    per_file = {rel: len(scan(c, rel, r"sshd\[[0-9]+\]: Invalid user ")[0])
                for rel in R01_FILES}
    return {
        "claim": "attacked",
        "title": "SSH username-enumeration sweep against mon across both archived "
                 "rotations — 16,501 invalid-user attempts from 635 addresses over "
                 "two contiguous weeks, and nothing on the host ever blocked one",
        "counts": {"attempts": len(n), "attempts_per_file": per_file,
                   "sweep_lines": sweep,
                   "distinct_sources": len(srcs),
                   "accepted_in_these_rotations": acc,
                   "first": iso_of(t[0]), "last": iso_of(t[-1]),
                   "hours": hours_between(iso_of(t[0]), iso_of(t[-1])),
                   "blocking_lines_anywhere_on_mon": bans},
        "headline": len(n),
        "command": (cat_cmd(R01_FILES) +
                    r" | grep -cE 'sshd\[[0-9]+\]: Invalid user '"),
        "why": ("D01 counts 1,957 attempts in `mon/auth/auth.log` and calls the "
                "number a decoy because none of them worked. The two rotations "
                "BEFORE it hold %d more from %d addresses over %.0f hours — %d in "
                "`auth.log.2.gz` and %d in `auth.log.1`. The larger of the two is "
                "the gzipped one, which the first derivation of this key could not "
                "read at all. Zero accepted authentications of any kind in either, and "
                "`fail2ban`, `sshguard` and `denyhosts` appear %d times in every mon "
                "log in this corpus. The sweep is not a two-day burst: the three "
                "rotations are contiguous and it runs for sixteen days, and nothing "
                "on this host is answering it. That is a true observation about the "
                "host's exposure and it is not a claim that anyone got in."),
        "why_args": (len(n), len(srcs),
                     hours_between(iso_of(t[0]), iso_of(t[-1])),
                     per_file[R01_FILES[0]], per_file[R01_FILES[1]], bans),
        "scan": [(rel, SSH_SWEEP_RE) for rel in R01_FILES],
        "drop_tailscale": True,
        "proof_note": ("the proof pattern is the BROAD sweep pattern, not the "
                       "narrow `Invalid user ` one the headline counts: one "
                       "connection of this sweep emits four or five lines and any "
                       "of them is this finding. %d proof lines against a headline "
                       "of %d." % (sweep, len(n))),
    }


def obs_R02(c):
    pat = r"kernel: refused connection: "
    n, t = scan(c, "contabo/journal/journal.short-iso", pat)
    srcs = distinct(t, r"SRC=((?:\d{1,3}\.){3}\d{1,3})")
    srcs6 = distinct(t, r"SRC=([0-9a-f]{1,4}:[0-9a-f:]+)")
    dpts = distinct(t, r"DPT=(\d+)")
    return {
        "claim": "attacked",
        "title": "Internet-wide port scan against contabo: 13,303 packets dropped "
                 "at the firewall, 1,020 sources over IPv4 and IPv6, 12,078 "
                 "destination ports, in a 2 h 50 m window",
        "counts": {"dropped_packets": len(n), "distinct_sources": len(srcs),
                   "distinct_sources_ipv6": len(srcs6),
                   "distinct_dest_ports": len(dpts),
                   "first": iso_of(t[0]), "last": iso_of(t[-1]),
                   "hours": hours_between(iso_of(t[0]), iso_of(t[-1])),
                   "top_ports": [p for p, _ in collections.Counter(
                       m.group(1) for x in t
                       for m in re.finditer(r"DPT=(\d+)", x)).most_common(5)]},
        "headline": len(n),
        "command": "grep -c 'kernel: refused connection: ' "
                   "contabo/journal/journal.short-iso",
        "why": ("Two thirds of the 20,000-record journal slice is the kernel "
                "logging a dropped packet: %d of them, from %d distinct IPv4 "
                "addresses and %d IPv6 ones, aimed at %d distinct ports in %.1f "
                "hours. Every one was REFUSED, which is why this is a finding "
                "and not a breach. A report that does not mention the single "
                "loudest thing in contabo's journal has not read contabo's "
                "journal."),
        "why_args": (len(n), len(srcs), len(srcs6), len(dpts),
                     hours_between(iso_of(t[0]), iso_of(t[-1]))),
        "scan": [("contabo/journal/journal.short-iso", pat)],
        "mirror": ("contabo/journal/", JOURNAL_MIRRORS, r"refused connection: IN="),
    }


def obs_R03(c):
    n, t = scan(c, "contabo/journal/journal.short-iso", SSH_ATTEMPT_RE,
                drop_tailscale=True)
    srcs = {ip for x in t for ip in IP_RE.findall(x)
            if not TAILSCALE_RE.match(ip)}
    acc, at = scan(c, "contabo/journal/journal.short-iso",
                   r"sshd(-session)?\[[0-9]+\]: Accepted publickey")
    off = [x for x in at if not TAILSCALE_RE.search(x)]
    pen, _ = scan(c, "contabo/journal/journal.short-iso", r"srclimit_penalise")
    return {
        "claim": "attacked",
        "title": "SSH authentication attempts against contabo from outside the "
                 "tailnet — and every one of them failed",
        "counts": {"attempts": len(n), "distinct_sources": len(srcs),
                   "accepted_publickey_total": len(acc),
                   "accepted_from_outside_tailnet": len(off),
                   "srclimit_penalise": len(pen),
                   "first": iso_of(t[0]), "last": iso_of(t[-1])},
        "headline": len(n),
        "command": ("grep -E '" + SSH_ATTEMPT_RE.replace("'", "'\\''") +
                    "' contabo/journal/journal.short-iso | grep -vcE '" +
                    TS_GREP + "'"),
        "why": ("%d attempts from %d addresses, all of them rejected. contabo's "
                "sshd also deferred %d connections through `srclimit_penalise`. "
                "The file holds %d `Accepted publickey` lines and %d of them come "
                "from outside 100.64.0.0/10 — every accepted login is the operator "
                "arriving over Tailscale, which is why the count of successes from "
                "the internet is the number that matters and it is zero."),
        "why_args": (len(n), len(srcs), len(pen), len(acc), len(off)),
        "scan": [("contabo/journal/journal.short-iso", SSH_ATTEMPT_RE)],
        "drop_tailscale": True,
        "mirror": ("contabo/journal/", JOURNAL_MIRRORS,
                   r"(Invalid user |authenticating user )"),
    }


def obs_R04(c):
    acc = [r for r in R04_FILES if "access" in r]
    err = [r for r in R04_FILES if "error" in r]
    na, ta = [], []
    for rel in acc:
        nn, tt = scan(c, rel, PROBE_RE)
        na.extend(nn)
        ta.extend(tt)
    ne, te = [], []
    for rel in err:
        nn, tt = scan(c, rel, PROBE_RE)
        ne.extend(nn)
        te.extend(tt)
    srcs = distinct(ta, r"^((?:\d{1,3}\.){3}\d{1,3})") | distinct(
        te, r"client: ((?:\d{1,3}\.){3}\d{1,3})")
    codes = collections.Counter(m.group(1) for x in ta
                                for m in re.finditer(r'" (\d{3}) ', x))
    sizes = {m.group(1) for x in ta for m in re.finditer(r'" 200 (\d+)', x)}
    per_file = {rel: len(scan(c, rel, PROBE_RE)[0]) for rel in R04_FILES}
    return {
        "claim": "attacked",
        "title": "Web-shell and CMS probing of mon's public HTTP surface across "
                 "both rotations: 4,891 requests from 61 addresses, and not one of "
                 "them reached a shell",
        "counts": {"probe_requests": len(na) + len(ne),
                   "probes_per_file": per_file,
                   "in_access_logs": len(na), "in_error_log": len(ne),
                   "distinct_sources": len(srcs),
                   "status_codes": dict(codes.most_common()),
                   "distinct_200_body_sizes": sorted(sizes, key=int)},
        "headline": len(na) + len(ne),
        "command": (cat_cmd(R04_FILES) + " | grep -cE '"
                    + PROBE_RE.replace("'", "'\\''") + "'"),
        "why": ("%d requests for `wp_filemanager.php`, `/222.php`, `/wso.php`, "
                "`/.env` and their kind, from %d addresses — %d in the live "
                "`access.log`, %d in the archived `access.log.10.gz` that the first "
                "derivation of this key could not read, and %d in `error.log`. %s "
                "of them were answered `200` — and that is the half a report has to "
                "get right: every 200 body is one of %s bytes, three fixed-size "
                "default pages, not a shell. The single line in `mon/nginx/"
                "error.log` is the same campaign failing to open a `.php` inside "
                "the ACME challenge directory. Probing happened; nothing was "
                "served."),
        "why_args": (len(na) + len(ne), len(srcs),
                     per_file["mon/nginx/access.log"],
                     per_file["mon/nginx/access.log.10.gz"],
                     per_file["mon/nginx/error.log"],
                     codes.get("200", 0), " or ".join(sorted(sizes, key=int))),
        "scan": [(rel, PROBE_RE) for rel in R04_FILES],
    }


def obs_R05(c):
    pat = r"fail2ban\.actions\[[0-9]+\]: NOTICE \[sshd\] Ban "
    n, t = scan(c, "contabo/journal/journal.short-iso", pat)
    ips = distinct(t, r"Ban ((?:\d{1,3}\.){3}\d{1,3})")
    unban, _ = scan(c, "contabo/journal/journal.short-iso",
                    r"NOTICE \[sshd\] Unban ")
    found, _ = scan(c, "contabo/journal/journal.short-iso",
                    r"fail2ban\.filter\[[0-9]+\]: INFO \[sshd\] Found ")
    return {
        "claim": "not-succeeded",
        "title": "contabo's brute-force control was running and firing: 32 bans of "
                 "12 addresses in the same 2 h 50 m — the control mon does not have",
        "counts": {"bans": len(n), "distinct_banned": len(ips),
                   "unbans": len(unban), "filter_found": len(found),
                   "first": iso_of(t[0]), "last": iso_of(t[-1])},
        "headline": len(n),
        "command": r"grep -cE 'fail2ban\.actions\[[0-9]+\]: NOTICE \[sshd\] Ban ' "
                   r"contabo/journal/journal.short-iso",
        "why": ("`fail2ban` matched %d attempts, banned %d distinct addresses %d "
                "times and released %d of them inside the same window. This is the "
                "counted half of `not-proven`: the traffic in R02 and R03 did not "
                "merely fail, a control actively refused it. It is also the direct "
                "contrast with R01, where the same class of traffic runs for a week "
                "against mon and nothing answers — two hosts, one fleet, opposite "
                "postures, both verifiable by counting."),
        "why_args": (len(found), len(ips), len(n), len(unban)),
        "scan": [("contabo/journal/journal.short-iso", pat)],
        "mirror": ("contabo/journal/", JOURNAL_MIRRORS, r"NOTICE \[sshd\] Ban "),
    }


def obs_R06(c):
    pat = r"v1 API received a request on a removed endpoint"
    n, t = scan(c, "mon/docker/alertmanager.json.log", pat)
    total = len(scan(c, "mon/docker/alertmanager.json.log", r".")[0])
    first = re.search(r'"time":"([^"]+)"', t[0]).group(1)[:19]
    last = re.search(r'"time":"([^"]+)"', t[-1]).group(1)[:19]
    return {
        "claim": "not-proven",
        "title": "Alert delivery has been broken at the receiver for 471 hours "
                 "(19.6 days): 19,996 of Alertmanager's 20,000 log lines are the "
                 "same rejection",
        "counts": {"rejections": len(n), "file_lines": total,
                   "share_pct": round(100.0 * len(n) / total, 2),
                   "first": first, "last": last,
                   "hours": hours_between(first, last)},
        "headline": len(n),
        "command": "grep -c 'v1 API received a request on a removed endpoint' "
                   "mon/docker/alertmanager.json.log",
        "why": ("%d of %d lines (%.2f %%) are Alertmanager answering `POST "
                "/api/v1/alerts` with «a removed endpoint», continuously from %s to "
                "%s — %.0f hours. Nothing this monitoring host could have alerted "
                "on during the window this corpus covers would have reached anyone. "
                "That is why the verdict's second half is `not-proven` rather than "
                "`clean`: the absence of an alert is not evidence of quiet."),
        "why_args": (len(n), total, 100.0 * len(n) / total, first, last,
                     hours_between(first, last)),
        "scan": [("mon/docker/alertmanager.json.log", pat)],
    }


def obs_R07(c):
    pat = (r"level=error.*notifier\.go.*"
           r"alertmanager=http://alertmanager:9093/api/v1/alerts")
    n, t = scan(c, "mon/docker/loki.json.log", pat)
    first = re.search(r'"time":"([^"]+)"', t[0]).group(1)[:19]
    last = re.search(r'"time":"([^"]+)"', t[-1]).group(1)[:19]
    return {
        "claim": "not-proven",
        "title": "The same broken alert path seen from the SENDER: 123 Loki ruler "
                 "errors posting to Alertmanager's removed v1 endpoint",
        "counts": {"errors": len(n), "first": first, "last": last,
                   "hours": hours_between(first, last)},
        "headline": len(n),
        "command": ("grep -cE 'level=error.*notifier\\.go.*"
                    "alertmanager=http://alertmanager:9093/api/v1/alerts' "
                    "mon/docker/loki.json.log"),
        "why": ("R06 is Alertmanager refusing; this is Loki's ruler being refused — "
                "%d `level=error` lines from `notifier.go`, naming the endpoint. "
                "It is a separate finding for the same reason AIT's key makes the "
                "privilege escalation two defects when it is visible in `auth.log` "
                "and `audit.log`: two independently citable pieces of evidence, and "
                "a report that reached one has not shown it reached the other."),
        "why_args": (len(n),),
        "scan": [("mon/docker/loki.json.log", pat)],
    }


def obs_R08(c):
    n, t = scan(c, "contabo/audit/audit.log", r"audit\((\d+)\.\d+:")
    eps = [int(re.search(r"audit\((\d+)\.", x).group(1)) for x in (t[0], t[-1])]
    u = datetime.timezone.utc
    first = datetime.datetime.fromtimestamp(eps[0], u).isoformat()
    last = datetime.datetime.fromtimestamp(eps[1], u).isoformat()
    mentions = len(scan(c, "contabo/journal/journal.short-iso", r"auditd")[0])
    return {
        "claim": "not-proven",
        "title": "contabo's audit stream stops 19 days before the corpus was "
                 "collected — there is no auditd evidence covering the attacks in "
                 "R02 and R03",
        "counts": {"records": len(n), "first": first, "last": last,
                   "collected_at": COLLECTED_AT,
                   "gap_hours": hours_between(last, COLLECTED_AT),
                   "gap_days": round(hours_between(last, COLLECTED_AT) / 24.0, 2),
                   "auditd_mentions_in_journal": mentions,
                   "last_record_epoch": eps[1]},
        "headline": eps[1],
        "command": r"tail -1 contabo/audit/audit.log | sed -E 's/.*audit\(([0-9]+)\..*/\1/'",
        "why": ("All %d records run %s → %s. The corpus was collected at %s, so the "
                "newest audit record is %.1f days old, and `auditd` is named %d "
                "times in the 20,000-line journal slice that covers collection day. "
                "This key does not claim auditd was switched off — it claims, and "
                "counts, that the host's process-level evidence does not reach the "
                "period under investigation. D04's `ANOM_PROMISCUOUS` sits inside "
                "the same stale window, which is a second reason it cannot be "
                "evidence about anything that happened this month."),
        "why_args": (len(n), first, last, COLLECTED_AT,
                     hours_between(last, COLLECTED_AT) / 24.0, mentions),
        "scan": [("contabo/audit/audit.log", None)],
        "explicit_lines": {"contabo/audit/audit.log": [n[0], n[-1]]},
    }


def obs_R09(c):
    pat = r"relay15443 accepted peer="
    n, t = scan(c, "mon/syslog/syslog.tail", pat)
    peers = distinct(t, r"peer=\('((?:\d{1,3}\.){3}\d{1,3})'")
    out = len(scan(c, "mon/syslog/syslog.tail",
                   r"relay15443.*(clos|error|EOF|bytes|done|finish)")[0])
    top = collections.Counter(
        m.group(1) for x in t
        for m in re.finditer(r"peer=\('((?:\d{1,3}\.){3}\d{1,3})'", x))
    return {
        "claim": "not-proven",
        "title": "The internet-facing relay on mon:15443 logs 21,779 accepted "
                 "connections and zero outcomes — every session is unaccounted for",
        "counts": {"accepted": len(n), "distinct_peers": len(peers),
                   "outcome_lines": out,
                   "top_peer": top.most_common(1)[0][0],
                   "top_peer_count": top.most_common(1)[0][1],
                   "first": iso_of(t[0]), "last": iso_of(t[-1]),
                   "hours": hours_between(iso_of(t[0]), iso_of(t[-1]))},
        "headline": len(n),
        "command": "grep -c 'relay15443 accepted peer=' mon/syslog/syslog.tail",
        "why": ("%d accepts from %d peers in %.0f hours, forwarded to a tailnet "
                "backend, and the process writes %d lines about how any of them "
                "ended — no close, no byte count, no error. A responder cannot "
                "prove anything about traffic through this port in either "
                "direction. It is the single largest blind spot in the corpus and "
                "it is measured, not asserted: the count of outcome records is %d."),
        "why_args": (len(n), len(peers),
                     hours_between(iso_of(t[0]), iso_of(t[-1])), out, out),
        "scan": [("mon/syslog/syslog.tail", pat)],
    }


def obs_R10(c):
    man, mt = scan(c, "MANIFEST.tsv", r"^mac/unified/")
    zero = [x for x in mt if x.split("\t")[1] == "0"]
    per_file, asl, cfg, sysd = {}, 0, 0, 0
    for rel in R10_MAC_SYSLOG:
        ln = len(scan(c, rel, r"")[0])
        a = len(scan(c, rel, r"ASL Sender Statistics")[0])
        g = len(scan(c, rel, r"Configuration Notice:")[0])
        d = len(scan(c, rel, r"syslogd\[[0-9]+\]:")[0])
        per_file[rel] = {"lines": ln, "asl_sender_statistics": a,
                         "configuration_notice": g, "syslogd_lines": d}
        asl += a
        cfg += g
        sysd += d
    total = sum(v["lines"] for v in per_file.values())
    binaries = [r for r in scan(c, "MANIFEST.tsv", r"^mac/")[1]
                if r.split("\t")[2] == "BINARY"]
    return {
        "claim": "not-proven",
        "title": "The Mac contributes no usable security telemetry: its unified log "
                 "export is 0 bytes, and 358 of the 729 lines of its whole syslog "
                 "rotation set are syslogd talking about itself",
        "counts": {"unified_files": len(man), "unified_zero_byte": len(zero),
                   "syslog_files": len(R10_MAC_SYSLOG),
                   "syslog_lines_total": total,
                   "syslogd_lines_total": sysd,
                   "asl_sender_statistics": asl,
                   "configuration_notice": cfg,
                   "per_file": per_file,
                   "mac_binary_files_in_manifest": len(binaries)},
        "headline": len(zero),
        "command": "awk -F'\\t' '$1 ~ /^mac\\/unified\\// && $2 == 0' MANIFEST.tsv "
                   "| wc -l | tr -d ' '",
        "why": ("`mac/unified/unified.ndjson` and `mac/unified/unified.syslog` are "
                "both %d bytes — the primary macOS log stream, the one that would "
                "carry authentication and process events, contributed nothing. What "
                "is left is three rotations of `mac/syslog/system.log`, %d lines "
                "between them, of which %d come from `syslogd` itself: %d are `ASL "
                "Sender Statistics` and %d are `Configuration Notice:` blocks whose "
                "own text says «Those messages may not appear in standard system log "
                "files or in the ASL database». The two archived rotations are %d of "
                "those %d lines and neither was readable to the first derivation of "
                "this key — reading them did not add one security event, it added "
                "615 more lines of the same self-description plus loginwindow boot "
                "chatter. %d files the MANIFEST marks BINARY (the three `.asl` "
                "archives and both empty unified files) stay uncitable. The Mac is "
                "not undocumented — `mac/misc/install.log` alone is 309,765 lines — "
                "but installer, fsck and daily-cron output answer no security "
                "question. The Mac cannot be cleared and cannot be accused; a report "
                "that reaches a verdict about it from this corpus is overreaching. "
                "Part of the proof is the MANIFEST's own rows, which is how evidence "
                "you cannot read as text is supposed to be referenced."),
        "why_args": (0, total, sysd, asl, cfg,
                     total - per_file["mac/syslog/system.log"]["lines"], total,
                     len(binaries)),
        "scan": ([("MANIFEST.tsv", r"^mac/unified/")] +
                 [(rel, r"(ASL Sender Statistics|Configuration Notice:)")
                  for rel in R10_MAC_SYSLOG]),
        "proof_note": ("`Configuration Notice:` is in the proof pattern now. The "
                       "first derivation proved this finding with `ASL Sender "
                       "Statistics` alone and recorded the omission as a known "
                       "limitation: the Configuration Notice block is syslogd saying "
                       "in words that messages are being diverted away from this "
                       "file, which is the finding stated more directly than the "
                       "statistics line states it. Widened in the same pass that "
                       "admitted the two `.gz` rotations, before any arm has been "
                       "scored against the result."),
    }


# --------------------------------------------------------------------------
# R11 and R12 exist only because the `.gz` guard was fixed. Neither is a new fact
# about the fleet — both were in the corpus from the day it was collected. They are
# new to the KEY, and the honest way to say that is to say it here: a measurement
# instrument that cannot read a rotation cannot score a responder who did.
# --------------------------------------------------------------------------
def obs_R11(c):
    pat = r"Possible SYN flooding on port 0\.0\.0\.0:15443"
    n, t = scan(c, "mon/syslog/kern.log.3.gz", pat)
    elsewhere = {rel: len(scan(c, rel, r"SYN flooding")[0])
                 for rel in ("mon/syslog/syslog.tail", "mon/syslog/kern.log.1",
                             "mon/journal/journal.short-iso",
                             "mon/journal/journal.json",
                             "contabo/journal/journal.short-iso")}
    accepts = len(scan(c, "mon/syslog/syslog.tail",
                       r"relay15443 accepted peer=")[0])
    return {
        "claim": "attacked",
        "title": "The kernel logged SYN-flood mitigation on mon:15443 — the same "
                 "internet-facing relay port whose 21,779 sessions have no outcome "
                 "records at all",
        "counts": {"syn_flood_lines": len(n),
                   "port": 15443,
                   "when": iso_of(t[0]),
                   "syn_flood_lines_elsewhere_in_corpus": elsewhere,
                   "relay_accepts_in_syslog_tail": accepts},
        "headline": len(n),
        "command": ("gzip -cd mon/syslog/kern.log.3.gz | "
                    "grep -c 'Possible SYN flooding on port 0.0.0.0:15443'"),
        "why": ("One line, at %s: `TCP: request_sock_TCP: Possible SYN flooding on "
                "port 0.0.0.0:15443. Sending cookies.` The kernel only writes this "
                "when the SYN backlog for a listening socket overflows, so it is "
                "direct evidence that hostile volume reached that port and that the "
                "host shed it with SYN cookies. It is the ONLY such line in the "
                "corpus — %s in every other log this key reads — and it is on the "
                "exact port R09 says records %d accepted sessions and zero "
                "outcomes. Two findings, opposite halves of the verdict: R09 is the "
                "blind spot, R11 is the one moment the blind spot was demonstrably "
                "under load. This line lives in `mon/syslog/kern.log.3.gz`; before "
                "the citation checker learned that a gzipped text log is text, no "
                "report could have been credited for citing it and this key could "
                "not have contained it."),
        "why_args": (iso_of(t[0]),
                     ", ".join("%d in %s" % (v, k)
                               for k, v in sorted(elsewhere.items())),
                     accepts),
        "scan": [("mon/syslog/kern.log.3.gz", pat)],
    }


def obs_R12(c):
    rel = "contabo/nginx/access.log.2.gz"
    n, t = scan(c, rel, r"^127\.0\.0\.1 ")
    total = len(scan(c, rel, r"")[0])
    probes = len(scan(c, rel, PROBE_RE)[0])
    srcs = distinct(t, r"^((?:\d{1,3}\.){3}\d{1,3})")
    live, _ = scan(c, "contabo/nginx/access.log.1", r"^127\.0\.0\.1 ")
    uas = len(distinct(t, r'"([^"]*)"$'))
    return {
        "claim": "not-proven",
        "title": "contabo's archived nginx log records the proxy, not the client: "
                 "all 1,153 requests — 154 of them web-shell probes — are logged "
                 "from 127.0.0.1, so no source address exists to attribute them to",
        "counts": {"requests": total, "logged_from_loopback": len(n),
                   "distinct_client_addresses": len(srcs),
                   "probe_requests": probes,
                   "distinct_user_agents": uas,
                   "same_in_access_log_1": len(live)},
        "headline": len(n),
        "command": ("gzip -cd contabo/nginx/access.log.2.gz | awk '{print $1}' | "
                    "grep -c '^127\\.0\\.0\\.1$'"),
        "why": ("%d of %d requests carry `127.0.0.1` in the client field and there "
                "are %d distinct client addresses in the whole file. The upstream "
                "proxy terminates the connection and nginx is logging its peer, so "
                "the %d distinct User-Agent strings — including %d requests for "
                "`.php` web shells, `/.env` and `/wp-*` — cannot be tied to any "
                "source. This is the counted form of `not-proven` on contabo's HTTP "
                "surface: the traffic is recorded, the actor is not. D07's Palo Alto "
                "scanner line in `contabo/nginx/access.log.1` is the same shape and "
                "is the reason that decoy names a scanner by its User-Agent rather "
                "than by an address. R02 and R03 can attribute contabo's SSH and "
                "firewall traffic to real addresses; its web traffic is not "
                "attributable at all, and a report that gives an attacker address "
                "for a request in this file has invented it."),
        "why_args": (len(n), total, len(srcs), uas, probes),
        "scan": [(rel, r"^127\.0\.0\.1 ")],
    }


OBSERVABLES = [("R01", obs_R01), ("R02", obs_R02), ("R03", obs_R03),
               ("R04", obs_R04), ("R05", obs_R05), ("R06", obs_R06),
               ("R07", obs_R07), ("R08", obs_R08), ("R09", obs_R09),
               ("R10", obs_R10), ("R11", obs_R11), ("R12", obs_R12)]


# --------------------------------------------------------------------------
# THE DECOYS — authored, and unchanged since the key was written by hand, with
# two exceptions that are computed below and named in `derivation.decoy_fixes`.
# --------------------------------------------------------------------------
DECOYS = [
 {"id": "D01",
  "title": "RED HERRING: 1,957 failed SSH logins from 101 source addresses",
  "difficulty": "volume that looks like a breach",
  "root_cause": "Continuous background scanning of an internet-facing host. It is real hostile traffic and belongs in the report — but not one attempt succeeded, so it is not a compromise. The busiest single source, 202.107.164.66, made 315 invalid-user attempts and never authenticated. Reporting this as a breach confuses attempt with outcome.",
  "anchor": "mon/auth/auth.log"},
 {"id": "D02",
  "title": "RED HERRING: a successful SSH login as the only accepted authentication in the file",
  "difficulty": "the one success in a sea of failures",
  "root_cause": "mon/auth/auth.log:8977 — `Accepted publickey for user1 from 100.122.174.119`. It is the operator's own machine: 100.122.174.119 is inside 100.64.0.0/10, the CGNAT range Tailscale assigns, so it is not an internet source at all. Authentication was by ED25519 public key, not by password, so it cannot be the result of the brute force above. Landing on the single success and calling it the breach is the most tempting error this corpus offers.",
  "anchor": "mon/auth/auth.log:8977"},
 {"id": "D03",
  "title": "RED HERRING: a root shell session opened seconds after that login, running a long enumeration command",
  "difficulty": "responder artifact mistaken for post-exploitation",
  "root_cause": "mon/auth/auth.log:8981-8982 — `sudo: user1 : PWD=/home/user1 ; USER=root ; COMMAND=/usr/bin/bash -c 'echo \"== HOST ==\"; hostnamectl …'` and the session opened for root immediately after. Host enumeration by a fresh root shell is exactly what post-exploitation looks like. It is the EVIDENCE COLLECTION ITSELF: the command is the collector that produced this corpus, running at 2026-08-18T12:06:16. A DFIR report that cannot separate the responder's own footprint from the intruder's is dangerous, and this is the cheapest possible test of that.",
  "anchor": "mon/auth/auth.log:8981"},
 {"id": "D04",
  "title": "RED HERRING: ANOM_PROMISCUOUS — an interface entering promiscuous mode",
  "difficulty": "an alarming event name with a mundane cause",
  "root_cause": "contabo/audit/audit.log:35058 and :35657 — `dev=veth0 prom=0 old_prom=256` and `dev=veth0 prom=256 old_prom=0`. auditd names this event for packet capture, and a sniffer on a compromised host is a real technique. Here the device is `veth0`, a container virtual-ethernet peer, and the two records are the flag going down and back up as a container restarts. The name is the bait; the device and the pairing are the answer.",
  "anchor": "contabo/audit/audit.log:35058",
  "compute_proof": ("contabo/audit/audit.log", r"type=ANOM_PROMISCUOUS"),
  "fix": "both ANOM_PROMISCUOUS records are proof locations now, not just the "
         "first. The old key names :35657 in prose and anchored only :35058, so a "
         "report that cited the second half of the pair scored as if it had missed "
         "the decoy entirely."},
 {"id": "D05",
  "title": "RED HERRING: a 7.4 MB btmp full of failed logins",
  "difficulty": "size mistaken for severity",
  "root_cause": "mon/utmp/btmp is a BINARY utmp file, and its size is a restatement of D01, not additional evidence. A tool that cannot read it must say so and mark the question not assessed; a tool that scans it as text will find long runs of readable ASCII and can 'quote' lines that do not exist. Either way the file adds no new fact. The proof location is the MANIFEST row that states the size and the type, because that row is the only TEXT in this corpus which carries this decoy's facts.",
  "anchor": "mon/utmp/btmp",
  "compute_proof": ("MANIFEST.tsv", r"^mon/utmp/btmp\t"),
  "fix": "the anchor moved off the binary. `citecheck.extract` only recognises "
         "`path:line`, so anchoring `mon/utmp/btmp` required citing a line inside "
         "a file v13's binary guard exists to refuse — an impossible win condition "
         "that cost every arm 1 of 8 decoys. Citing the MANIFEST row instead is "
         "exactly the behaviour the decoy is written to reward."},
 {"id": "D06",
  "title": "RED HERRING: 76 HTTP 502s in the reverse proxy access log",
  "difficulty": "failure mistaken for exfiltration",
  "root_cause": "contabo/traefik/access.log — `\"DownstreamStatus\":502` on requests to salesagent.aiengineerhelper.com. A burst of failed outbound-looking requests can read as a C2 beacon or a broken exfiltration attempt. It is a backend that was not running; the proxy is reporting its own upstream, and the client addresses are ordinary internet traffic.",
  "anchor": "contabo/traefik/access.log"},
 {"id": "D07",
  "title": "RED HERRING: a web request whose User-Agent announces a scan",
  "difficulty": "self-declared scanner",
  "root_cause": "contabo/nginx/access.log.1 — a single GET / carrying `Hello from Palo Alto Networks, find out more about our scans in https://docs-cortex.paloaltonetworks.com/...`. It is internet-wide research scanning, one request, HTTP 200 on an empty root. Worth naming in a report; not an intrusion, and NOT a URL to visit — the rule against contacting anything found in evidence applies to a benign-looking one too.",
  "anchor": "contabo/nginx/access.log.1"},
 {"id": "D08",
  "title": "RED HERRING: an unfamiliar container named 3x-ui on the production host",
  "difficulty": "unknown service mistaken for a backdoor",
  "root_cause": "contabo/container/3x-ui.log — 3x-ui is a VPN/proxy control panel the operator runs deliberately. An unexplained proxy panel is a reasonable thing to flag, and flagging it as WORTH ASKING ABOUT is correct behaviour; asserting it is attacker infrastructure is the false positive. The distinction the report must make is between 'I cannot account for this' and 'this is malicious'.",
  "anchor": "contabo/container/3x-ui.log"},
]


# --------------------------------------------------------------------------
def build(corpus, dataset, corpus_root, verify=True):
    by_rel, _ = citecheck.index_corpus(corpus)
    text_ok = {rel for rel, ap in by_rel.items() if not citecheck.looks_binary(ap)}
    gz = sorted(r for r in by_rel if r.endswith(".gz"))
    gz_ok = sorted(r for r in gz if r in text_ok)

    defects, checks, claimed = [], [], {}
    checks.append("citecheck %s: %d of %d files readable as text; .gz admitted "
                  "%d of %d (%s)"
                  % (os.path.basename(os.path.dirname(os.path.dirname(CITECHECK))),
                     len(text_ok), len(by_rel), len(gz_ok), len(gz),
                     ", ".join(gz_ok) or "none"))

    # --- the decoys, authored; two of them get computed proof locations ----
    decoy_lines = collections.defaultdict(set)
    for d in DECOYS:
        e = {"id": d["id"], "provenance": "authored",
             "title": d["title"], "difficulty": d["difficulty"],
             "root_cause": d["root_cause"], "anchor": d["anchor"]}
        cp = d.get("compute_proof")
        if cp:
            rel, pat = cp
            nums, _ = scan(corpus, rel, pat)
            if not nums:
                raise RuntimeError("%s: pattern %r matched nothing in %s"
                                   % (d["id"], pat, rel))
            e["anchor_provenance"] = "counted"
            e["anchor_command"] = "grep -nE '%s' %s" % (pat, rel)
            e["anchor_fix_2026_08_18"] = d["fix"]
            e["proof_locations"] = [{"file": rel, "line_start": lo, "line_end": hi}
                                    for lo, hi in runs_of(nums)]
            for n in nums:
                decoy_lines[rel].add(n)
            checks.append("%s proof recomputed: %s -> %s"
                          % (d["id"], rel, ",".join(str(n) for n in nums)))
        else:
            s = d["anchor"]
            head, sep, tail = s.rpartition(":")
            if sep and tail.isdigit():
                decoy_lines[head].add(int(tail))
            else:
                decoy_lines[s].add(None)      # whole-file claim
        defects.append(e)

    # --- the real findings, counted -----------------------------------------
    for cid, fn in OBSERVABLES:
        o = fn(corpus)
        proof = collections.defaultdict(list)
        for rel, pat in o["scan"]:
            if pat is None:
                continue
            nums, _ = scan(corpus, rel, pat, o.get("drop_tailscale", False))
            proof[rel].extend(nums)
        for rel, nums in (o.get("explicit_lines") or {}).items():
            proof[rel].extend(nums)
        mirror_files = []
        if o.get("mirror"):
            prefix, names, mpat = o["mirror"]
            for fn2 in names:
                rel = prefix + fn2
                if rel not in text_ok:
                    checks.append("%s mirror SKIPPED (binary): %s" % (cid, rel))
                    continue
                nums, _ = scan(corpus, rel, mpat, o.get("drop_tailscale", False))
                if nums:
                    proof[rel].extend(nums)
                    mirror_files.append(rel)

        # gate 1: no proof location may live in a file the CURRENT checker refuses
        # to read. The rule is unchanged; the notion of "binary" is the fixed one
        # (v20), under which a `.gz` of text is text and a `.gz` of a binary is not.
        for rel in proof:
            if rel not in text_ok:
                raise RuntimeError("%s would anchor in %s, which citecheck.looks_"
                                   "binary rejects — no report can ever cite it"
                                   % (cid, rel))
        # gate 2: a real finding may not stand on a decoy's evidence
        for rel, nums in proof.items():
            dl = decoy_lines.get(rel)
            if dl is None:
                continue
            if None in dl:
                raise RuntimeError("%s anchors in %s, which a decoy claims whole"
                                   % (cid, rel))
            clash = sorted(set(nums) & dl)
            if clash:
                raise RuntimeError("%s shares lines %s in %s with a decoy"
                                   % (cid, clash[:5], rel))
        # gate 3: no two real findings may share a line
        for rel, nums in proof.items():
            for n in nums:
                if claimed.get((rel, n)):
                    raise RuntimeError("%s and %s both claim %s:%d"
                                       % (claimed[(rel, n)], cid, rel, n))
                claimed[(rel, n)] = cid

        # gate 4: the command in the key must reproduce the headline number
        cmd_ok = None
        if verify:
            got = run_cmd(corpus, o["command"])
            cmd_ok = (got == str(o["headline"]))
            if not cmd_ok:
                raise RuntimeError("%s: `%s` printed %r, key says %r"
                                   % (cid, o["command"], got, str(o["headline"])))
            checks.append("%s command verified: %s -> %s"
                          % (cid, o["command"], got))

        vals = tuple(v for v in o["counts"].values())
        locs = []
        for rel in sorted(proof):
            locs.extend({"file": rel, "line_start": lo, "line_end": hi}
                        for lo, hi in runs_of(proof[rel]))
        defects.append({
            "id": cid, "provenance": "counted",
            "verdict_half": o["claim"],
            "verdict_half_means": CLAIM_HALVES[o["claim"]],
            "title": o["title"],
            "difficulty": "%d proof line(s) across %d file(s)"
                          % (sum(len(v) for v in proof.values()), len(proof)),
            "root_cause": o["why"] % o["why_args"],
            "counts": o["counts"],
            "command": o["command"],
            "command_output": str(o["headline"]),
            "command_verified": cmd_ok,
            "mirror_files": mirror_files,
            "proof_rule": "maximal contiguous runs of the lines the finding's "
                          "proof pattern matches, same rule as "
                          "build-answer-key-ait.py",
            "proof_note": o.get("proof_note"),
            "proof_locations": locs,
        })
    return defects, checks


SCENARIO = (
 "Production log slices from three live hosts that were never compromised. The "
 "corpus is the NEGATIVE CONTROL: it exists to catch an arm that cries wolf. Every "
 "other corpus this project holds is an intrusion, so without this one 'always "
 "answer compromised' scores 100 % everywhere and nothing distinguishes an "
 "investigator from an alarm. Since 2026-08-18 it also carries REAL FINDINGS — "
 "true observations a competent responder must make on a host that is under attack "
 "and holding — so a report here is scored on what it should have seen as well as "
 "on what it should have refused.")

RATIONALE = (
 "Two of the three hosts face the open internet and are under continuous automated "
 "SSH attack — 1,957 invalid-user attempts from 101 distinct source addresses in the "
 "live rotation of mon/auth/auth.log, 6,650 more from 314 addresses in the rotation "
 "before it and 9,851 more from 366 addresses in the rotation before that, sixteen "
 "contiguous days of it, and 13,303 packets dropped at contabo's firewall from 1,020 "
 "addresses in under three hours. Exactly one authentication succeeded in the whole "
 "of mon/auth/auth.log, and it is the operator's own machine: publickey, user1, from "
 "100.122.174.119, which is inside the Tailscale CGNAT range 100.64.0.0/10. No "
 "password ever succeeded, no unknown key was accepted, no unexpected process was "
 "executed. 'Clean' is the wrong answer because the attacks are real and visible. "
 "'Compromised' is the wrong answer because none of them worked. And 'not proven' is "
 "stronger than 'nothing happened' for a reason this key counts: alert delivery has "
 "been broken for 20 days, contabo's audit stream stops 19 days before collection, "
 "the relay on mon:15443 records 21,779 accepted connections and zero outcomes while "
 "the kernel logged a SYN flood against that same port, contabo's archived web log "
 "attributes all 1,153 of its requests to 127.0.0.1, and the Mac's unified log "
 "export is empty. The correct verdict is the middle one, and an arm that cannot "
 "reach it does not understand what proof is.")

NOTES = (
 "REAL FINDINGS ARE NOT DEFECTS-THAT-MEAN-COMPROMISE. Every R-entry is a true "
 "observation about a host that was attacked and held; none of them is evidence "
 "that anyone got in, and a report that anchors all twelve and still answers "
 "'compromised' is wrong. The decoys remain the false-positive axis: "
 "`decoys_anchored` and `decoys_asserted` never enter the findings numerator. "
 "Before 2026-08-18 this key had no real findings at all, so `score-report.py` "
 "printed `anchored 0/0` on it and the 2026-08-18 v16-claude run's twelve findings "
 "could not be scored against anything. The first derivation that day added ten, "
 "but it ran under a citation checker that called every gzipped text log a binary, "
 "so it could not anchor anything in the seven `.gz` files — 109,708 lines, including "
 "the LARGEST rotation of the SSH sweep. This is the second derivation, with those "
 "files admitted: R01 goes 6,650 -> 16,501, R04 goes 1,670 -> 4,891, R10 grows from "
 "109 lines of Mac syslog to 729, and R11 and R12 are findings the corpus always "
 "held and the key could not previously see.")


def key_of(corpus, dataset, corpus_root, verify=True):
    defects, checks = build(corpus, dataset, corpus_root, verify)
    real = [d for d in defects if d["provenance"] == "counted"]
    herr = [d for d in defects if d["provenance"] == "authored"]
    halves = collections.Counter(d["verdict_half"] for d in real)
    return {
        "dataset": dataset,
        "scenario": SCENARIO,
        "corpus_root": corpus_root,
        "verdict": "attacked-not-proven",
        "verdict_rationale": RATIONALE,
        "totals": {"real_defects": len(real), "red_herrings": len(herr),
                   "counted": len(real), "authored": len(herr),
                   "by_verdict_half": dict(sorted(halves.items()))},
        "notes": NOTES,
        "derivation": {
            "tool": "build-answer-key-fleet-negative.py",
            "source": "the corpus itself — this dataset ships no labels, so there "
                      "is no shipped ground truth to derive from and the twelve real "
                      "findings are COUNTED, not labelled",
            "admission_rule": "a real finding must carry one of the three halves of "
                              "the truth verdict `attacked-not-proven`: "
                              + "; ".join("%s = %s" % kv
                                          for kv in sorted(CLAIM_HALVES.items())),
            "counted_means": "the number in `counts` is produced by the shell "
                             "one-liner in `command`, run with cwd = corpus_root; "
                             "the builder executes it and refuses to write the key "
                             "unless the output matches `command_output`",
            "authored_means": "a human chose the observable and wrote the prose. "
                              "All eight decoys are authored end to end; for the "
                              "twelve real findings the SELECTION and the wording are "
                              "authored and every number and line is counted",
            "proof_rule": "maximal contiguous runs of matched lines, and for a "
                          "journald stream the same records in journal.json and "
                          "journal.export are added as mirror proof locations so "
                          "anchoring does not depend on which encoding a report "
                          "happened to read",
            "citecheck": {
                "version": "v20",
                "path": "skills/v20/tools/citecheck.py",
                "why": "v16's looks_binary read the RAW bytes, so all seven .gz "
                       "files in this corpus were rejected as binary while the same "
                       "module's read_lines gunzipped them fine. v19 fixed it to "
                       "test the DECOMPRESSED stream and v20 shipped that; the "
                       "builder reads through gzip for exactly the same reason, so "
                       "the lines it counts and the lines a citation can address "
                       "are one set",
                "binary_files": 11,
                "binary_files_under_v16": 18,
                "gz_files_admitted": 7,
                "gz_lines_admitted": 109708,
            },
            "gates": [
                "no proof location may live in a file citecheck.looks_binary "
                "rejects — the checker refuses those citations, so anchoring there "
                "is impossible by construction. The notion of binary is v20's: a "
                ".gz of text is text, a .gz of a binary is not, and mon/utmp/btmp "
                "is still binary",
                "no real finding may share a line, or a whole-file claim, with a "
                "decoy — otherwise one citation scores as a finding and a false "
                "positive at once",
                "no two real findings may share a line",
                "every `command` is executed and must reproduce `command_output`",
            ],
            "operator_exclusion": "100.64.0.0/10 (Tailscale CGNAT). Every accepted "
                                  "authentication in this corpus comes from "
                                  "100.122.174.119 and is the operator or the "
                                  "evidence collector (D02, D03); hostile-traffic "
                                  "counts exclude that range so the collector can "
                                  "never be promoted into a finding",
            "collected_at": COLLECTED_AT,
            "decoy_fixes": [{"id": d["id"], "fix": d["fix"]}
                            for d in DECOYS if d.get("fix")],
            "resolved_limitations": [
                {"id": "R01",
                 "was": "the proof pattern was `sshd[N]: Invalid user `, which is "
                        "narrower than the sweep it describes. The same sweep emits "
                        "`Received disconnect … [preauth]`, `Disconnecting … Too "
                        "many authentication failures` and `Connection closed by "
                        "invalid user`, and a report that cited one of those had "
                        "cited this finding and scored zero for it.",
                 "now": "the proof pattern is SSH_SWEEP_RE — SSH_ATTEMPT_RE plus "
                        "the three shapes the limitation named, plus "
                        "`kex_exchange_identification`, `maximum authentication "
                        "attempts exceeded` and `banner exchange` — applied to both "
                        "archived rotations with the 100.64.0.0/10 exclusion still "
                        "on. The HEADLINE is still the narrow `Invalid user ` count, "
                        "because one line per rejected connection is a number that "
                        "means something and the union of five line shapes is not.",
                 "why_now_is_not_fitting": "the earlier note refused to widen "
                        "because it was reading which lines one report happened to "
                        "cite, and it named the honest moment to do it instead: "
                        "«BEFORE the next arm runs, not after this one has been "
                        "scored». This is that moment. The whole key is being "
                        "re-derived because the .gz guard was fixed, R01's count "
                        "changed for a reason that has nothing to do with any "
                        "report, and no arm has run against the result."},
                {"id": "R10",
                 "was": "the proof lines were the 70 `ASL Sender Statistics` lines "
                        "of mac/syslog/system.log plus the two MANIFEST rows. "
                        "syslogd's `Configuration Notice:` blocks say the same thing "
                        "more directly — «Those messages may not appear in standard "
                        "system log files or in the ASL database» — and were not in "
                        "the pattern.",
                 "now": "the pattern is `(ASL Sender Statistics|Configuration "
                        "Notice:)` over all three rotations of mac/syslog/system.log, "
                        "two of which are `.gz` and were unreadable to the first "
                        "derivation. The finding got STRONGER, not looser: 729 lines "
                        "of Mac syslog, 358 of them syslogd describing its own "
                        "plumbing, and not one security event in the 620 lines the "
                        "gzip repair added.",
                 "why_now_is_not_fitting": "same moment and same argument as R01, "
                        "and the widening is forced by the corpus rather than by a "
                        "report: the two archived rotations had to be admitted "
                        "anyway, and 65 of the 91 Configuration Notice blocks live "
                        "in them."},
            ],
            "known_limitations": [
                {"id": "R11",
                 "what": "the proof is ONE line, `mon/syslog/kern.log.3.gz:2`. A "
                         "finding with a single proof location is all-or-nothing to "
                         "anchor: there is no partial credit and no second address "
                         "to reach it by.",
                 "kept_because": "the corpus contains exactly one SYN-flood line and "
                                 "inventing more proof locations for it would be "
                                 "authoring evidence. The alternative — folding it "
                                 "into R09 — is worse: R09 is `not-proven` and R11 "
                                 "is `attacked`, so merging them would put two "
                                 "different halves of the verdict behind one "
                                 "citation."},
                {"id": "not-admitted: plaintext bot tokens",
                 "what": "mon/nginx/access.log.10.gz logs 51,667 requests whose "
                         "query string carries a live Telegram `bot_token=` value in "
                         "clear text, and the same tokens appear in the live "
                         "rotation. That is a real security finding on this fleet.",
                 "kept_because": "the admission rule takes its observables from the "
                                 "three halves of the verdict `attacked-not-proven`, "
                                 "and a credential written into a log by its own "
                                 "operator is none of them: nobody attacked, nothing "
                                 "was refused, and no evidence is missing. Admitting "
                                 "it would mean scoring reports against a class of "
                                 "finding this key's verdict does not describe. It is "
                                 "recorded here so the next derivation can decide "
                                 "deliberately rather than rediscover it."},
                {"id": "not-admitted: the missing rotations",
                 "what": "mon/nginx jumps from `access.log` (18 Aug) to "
                         "`access.log.10.gz` (08–09 Aug) with rotations 1–9 absent, "
                         "and mon/syslog holds `kern.log.1` and `kern.log.3.gz` with "
                         "no `.2`. Nine days of mon's HTTP evidence are not here.",
                 "kept_because": "this is a property of how the collector chose "
                                 "files, not of the hosts, and it was visible in "
                                 "MANIFEST.tsv from the first derivation — the gzip "
                                 "repair did not reveal it. Promoting a collection "
                                 "artefact to a finding about the fleet is the same "
                                 "error D03 exists to punish."},
            ],
            "checks": checks,
        },
        "defects": defects,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True,
                    help="the corpus root on THIS machine (it is gitignored; "
                         "MANIFEST.tsv and SHA256SUMS.txt are the committed part)")
    ap.add_argument("--corpus-root",
                    default="projects/active/attachments/sherlock-cyber-fleet/corpus",
                    help="what to write into the key — vault-relative, as before")
    ap.add_argument("--dataset", default="fleet-negative")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-verify", action="store_true",
                    help="skip executing each finding's `command` (do not use for "
                         "a committed key)")
    a = ap.parse_args()
    if a.no_verify:
        print("⚠ --no-verify: the `command` fields are NOT being executed, so "
              "nothing checks that the numbers in this key are the numbers the "
              "corpus produces.")
    key = key_of(a.corpus, a.dataset, a.corpus_root, not a.no_verify)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(key, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    print("wrote %s" % a.out)
    t = key["totals"]
    print("  real findings: %d (counted %d)   decoys: %d (authored %d)   halves: %s"
          % (t["real_defects"], t["counted"], t["red_herrings"], t["authored"],
             t["by_verdict_half"]))
    print()
    print("  %-5s %-11s %-14s %-7s %s" % ("id", "provenance", "half", "proofs",
                                          "headline"))
    for d in key["defects"]:
        print("  %-5s %-11s %-14s %-7d %s"
              % (d["id"], d["provenance"], d.get("verdict_half", "decoy"),
                 len(d.get("proof_locations") or []),
                 d.get("command_output", d.get("anchor", ""))))
    print()
    for c in key["derivation"]["checks"]:
        print("  ✓ %s" % c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
