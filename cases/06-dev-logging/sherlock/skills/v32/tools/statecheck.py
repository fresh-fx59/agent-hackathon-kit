#!/usr/bin/env python3
"""statecheck — a census of state changes the report must answer to.

    python3 statecheck.py --corpus ./logs --out work/state.tsv
    python3 statecheck.py --corpus ./logs --report work/report.md

Why this exists. Both existing validators take the MODEL'S OWN OUTPUT as their
universe: `citecheck` grades the citations a report wrote, `triagecheck` grades
the verdicts a model wrote on rows `logmap` chose. Nothing in the kit takes the
CORPUS as input and produces an OBLIGATION on the report. That missing direction
is why a false negative is invisible: a report that never mentions the one
intrusion artefact in the corpus passes every gate with a clean sheet.

Measured: a fully validated run — every citation ok, every worklist row closed,
exit 0 — missed a `3proxy` service installed under a user SID in the middle of
an inbound RDP session, because the 16-record event group fell between
`RARE_MAX_N` and `RATE_MIN_N` in `logmap.py` and the discriminating 46-character
SID exceeded `VALUE_MAX`. v32's `odd` and `minor` axes surface it for RANKING.
This tool makes it MANDATORY: the report must say something about it, or fail.

This tool deliberately breaks `logmap.py`'s "no severity dictionary" principle.
That principle is right for RANKING — a word list that ranks is misdirection,
it teaches the model what the list's author already suspected — and wrong for
CENSUS: a word list that only says "you must say SOMETHING about this line"
fails toward noise, never toward silence. So it lives in its own tool, with its
own small versioned catalogue, and `logmap.py` stays clean.

Two things keep the census answerable instead of a wall of 12,499 rows:

  1. The catalogue is keyed on (PROVIDER, EventID), never on the EventID alone.
     Measured on a 143-file Windows corpus: bare EventID 2006 matches 11,602
     Store-service records that are not firewall rule deletions at all. The
     provider is what gives the number its meaning.

  2. Records collapse into GROUPS keyed by (file, class, ACTOR), where the actor
     is the security principal that made the change — `ModifyingUser` for a
     firewall rule, the record's `Security/UserID` for a service install. One
     citation discharges a whole group. Measured on the same corpus: 864 records
     collapse to 12 groups, and the intruder's two artefacts land in groups of
     their own because no platform component shares their SID.

The actor is the grouping key on purpose. Bulk-dispositioning fifteen platform
service installs is legitimate; the sixteenth, installed by a different
principal, can never be swallowed by that same disposition. That is the exact
shape of the miss this tool exists to prevent.

A group is ACCOUNTED when the report cites any one of its record lines as
`path:line` — in a finding, or in an explicit disposition. Unaccounted groups
are blocking. Quote quality is `citecheck --require-quote`'s job, not this
tool's; this tool only answers "did the report say anything at all about it?".

No LLM, no network, no config, stdlib only. Exit 0 when every group is
accounted (or when only the census was asked for), 1 when any group is
unaccounted, 2 on usage error.
"""
import argparse
import gzip
import json
import os
import re
import sys

VERSION = 32

# (provider, EventID) -> class label. Small, versioned, and deliberately short:
# every entry is a CHANGE OF STATE that persists after the process that made it
# exits. Volume events (logons, connections, errors) are logmap.py's business.
CATALOGUE = {
    ("Service Control Manager", 7045): "service-install",
    ("Service Control Manager", 7040): "service-starttype-change",
    ("Microsoft-Windows-Eventlog", 104): "log-cleared",
    ("Microsoft-Windows-WMI-Activity", 5860): "wmi-subscription",
    ("Microsoft-Windows-WMI-Activity", 5861): "wmi-subscription",
    ("Microsoft-Windows-TaskScheduler", 106): "task-registered",
    ("Microsoft-Windows-TaskScheduler", 140): "task-updated",
    ("Microsoft-Windows-TaskScheduler", 141): "task-deleted",
}
for _e in (4697,):
    CATALOGUE[("Microsoft-Windows-Security-Auditing", _e)] = "service-install"
for _e in (4698, 4699, 4700, 4701, 4702):
    CATALOGUE[("Microsoft-Windows-Security-Auditing", _e)] = "scheduled-task-change"
for _e in (4720, 4722, 4723, 4724, 4726, 4738, 4781):
    CATALOGUE[("Microsoft-Windows-Security-Auditing", _e)] = "account-change"
for _e in (4728, 4732, 4756):
    CATALOGUE[("Microsoft-Windows-Security-Auditing", _e)] = "admin-group-change"
for _e in (4719,):
    CATALOGUE[("Microsoft-Windows-Security-Auditing", _e)] = "audit-policy-change"
for _e in (1102,):
    CATALOGUE[("Microsoft-Windows-Security-Auditing", _e)] = "log-cleared"
for _e in (4946, 4947, 4948, 4950, 4954, 4956):
    CATALOGUE[("Microsoft-Windows-Security-Auditing", _e)] = "firewall-rule-change"
for _e in (2004, 2005, 2006, 2008, 2009, 2033):
    CATALOGUE[("Microsoft-Windows-Windows Firewall With Advanced Security", _e)] = "firewall-rule-change"
for _e in (5001, 5007, 5010, 5012, 5013):
    CATALOGUE[("Microsoft-Windows-Windows Defender", _e)] = "defender-config-change"

# Fields that carry a short human-readable "what changed", best first. Only used
# for the exemplar column — never for grouping, so it cannot explode the census.
SUBJECT_FIELDS = ("ServiceName", "RuleName", "TaskName", "TargetUserName",
                  "ImagePath", "ApplicationPath", "Query", "NewValue", "param1")
# Who made the change, best first. EventData wins over the record's own
# Security/UserID: on a firewall record the latter is the SERVICE's identity
# (S-1-5-19), identical for every rule, which would collapse the whole channel
# into one group and hide exactly the row that matters.
ACTOR_FIELDS = ("ModifyingUser", "SubjectUserSid", "SubjectUserName", "UserSid")

MAX_EXEMPLAR = 120


def _open(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _eventid(system):
    e = system.get("EventID")
    if isinstance(e, dict):
        e = e.get("#text")
    try:
        return int(e)
    except (TypeError, ValueError):
        return None


def _provider(system):
    p = system.get("Provider")
    if not isinstance(p, dict):
        return None
    a = p.get("#attributes")
    if isinstance(a, dict):
        return a.get("Name") or a.get("EventSourceName")
    return p.get("Name")


def _flatten(data):
    """EventData as a flat str->str dict, whatever shape the exporter chose."""
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (str, int, float)):
                out[k] = str(v)
            elif isinstance(v, dict) and isinstance(v.get("#text"), (str, int, float)):
                out[k] = str(v["#text"])
    return out


def classify(rec):
    """-> (class, actor, subject) for a catalogued record, else None."""
    ev = rec.get("Event") if isinstance(rec, dict) else None
    if not isinstance(ev, dict):
        return None
    system = ev.get("System")
    if not isinstance(system, dict):
        return None
    eid = _eventid(system)
    prov = _provider(system)
    if eid is None or prov is None:
        return None
    label = CATALOGUE.get((prov, eid))
    if label is None:
        return None
    data = _flatten(ev.get("EventData"))
    actor = None
    for f in ACTOR_FIELDS:
        if data.get(f):
            actor = data[f]
            break
    if actor is None:
        sec = system.get("Security")
        if isinstance(sec, dict):
            a = sec.get("#attributes")
            if isinstance(a, dict):
                actor = a.get("UserID")
            actor = actor or sec.get("UserID")
    subject = ""
    for f in SUBJECT_FIELDS:
        if data.get(f):
            subject = data[f]
            break
    return label, (actor or "-"), subject[:MAX_EXEMPLAR], eid


def census(corpus):
    """Stream the corpus once. -> list of group dicts, ordered file then line."""
    groups = {}
    order = []
    for root, dirs, files in os.walk(corpus):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, corpus)
            try:
                fh = _open(path)
            except OSError:
                continue
            with fh:
                for n, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    hit = classify(rec)
                    if hit is None:
                        continue
                    label, actor, subject, eid = hit
                    key = (rel, label, actor)
                    g = groups.get(key)
                    if g is None:
                        g = groups[key] = {"file": rel, "class": label, "actor": actor,
                                           "eventids": set(), "lines": [], "exemplar": subject}
                        order.append(key)
                    g["eventids"].add(eid)
                    g["lines"].append(n)
    return [groups[k] for k in order]


CITE_RE = re.compile(r"([^\s`'\"(),;\[\]<>]+?):(\d+)")


def cited_lines(text):
    """-> {basename_lowercase: {line numbers}} for every path:line in the text."""
    out = {}
    for path, num in CITE_RE.findall(text):
        base = os.path.basename(path).lower()
        if not base or base.isdigit():
            continue
        out.setdefault(base, set()).add(int(num))
    return out


def _names(rel):
    """Every spelling of a file a report might legitimately use."""
    base = os.path.basename(rel).lower()
    names = {rel.lower(), base}
    # Windows channel exports arrive percent-escaped: `Foo%4Operational.jsonl`.
    names.add(base.replace("%4", "/").split("/")[-1])
    return names


def _matches(group_file, cited):
    """Does a cited path token mean this corpus file?

    A channel export's name contains SPACES — `Microsoft-Windows-Windows
    Firewall With Advanced Security%4Firewall.jsonl` — so a citation harvested
    from prose starts after the last space and is a SUFFIX of the real name, not
    its basename. Measured: without this, both v32 arms' correct citation of the
    3proxy firewall rule read as no citation at all. Suffix matching is bounded
    to 8+ characters so a bare `.jsonl` cannot discharge anything.
    """
    names = _names(group_file)
    if cited in names:
        return True
    return len(cited) >= 8 and any(n.endswith(cited) for n in names)


def account(groups, report_text):
    cites = cited_lines(report_text)
    for g in groups:
        want = set(g["lines"])
        hit = None
        for cited, nums in cites.items():
            if not _matches(g["file"], cited):
                continue
            for n in sorted(nums):
                if n in want:
                    hit = n
                    break
            if hit:
                break
        g["accounted_line"] = hit
    return groups


def render(groups, out):
    out.write("file\tclass\tactor\teventids\tn\tfirst\tlines\texemplar\tstatus\n")
    for g in groups:
        lines = g["lines"]
        shown = ",".join(str(x) for x in lines[:20]) + (",…" if len(lines) > 20 else "")
        status = "-"
        if "accounted_line" in g:
            status = ("ok:%d" % g["accounted_line"]) if g["accounted_line"] else "UNACCOUNTED"
        out.write("%s\t%s\t%s\t%s\t%d\t%d\t%s\t%s\t%s\n" % (
            g["file"], g["class"], g["actor"],
            ",".join(str(e) for e in sorted(g["eventids"])),
            len(lines), lines[0], shown, g["exemplar"].replace("\t", " "), status))


def main():
    ap = argparse.ArgumentParser(description="перепись изменений состояния, на которые отчёт обязан ответить")
    ap.add_argument("--corpus", required=True, help="корень корпуса логов")
    ap.add_argument("--report", help="отчёт; без него — только перепись")
    ap.add_argument("--out", help="куда записать TSV переписи")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(a.corpus):
        sys.stderr.write("statecheck: нет корпуса: %s\n" % a.corpus)
        return 2
    groups = census(a.corpus)

    text = ""
    if a.report:
        if a.report == "-":
            text = sys.stdin.read()
        elif os.path.isfile(a.report):
            with open(a.report, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            sys.stderr.write("statecheck: нет отчёта: %s\n" % a.report)
            return 2
        account(groups, text)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            render(groups, fh)

    bad = [g for g in groups if g.get("accounted_line") is None and a.report]
    if a.json:
        json.dump({"version": VERSION,
                   "groups": [{k: (sorted(v) if isinstance(v, set) else v)
                               for k, v in g.items()} for g in groups],
                   "total_records": sum(len(g["lines"]) for g in groups),
                   "unaccounted": len(bad)},
                  sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    else:
        render(groups, sys.stdout)
        sys.stdout.write("итого: %d групп, %d записей\n"
                         % (len(groups), sum(len(g["lines"]) for g in groups)))
        if a.report:
            for g in bad:
                sys.stdout.write("НЕ ОТВЕЧЕНО: %s:%d  %s  actor=%s  n=%d  %s\n"
                                 % (g["file"], g["lines"][0], g["class"], g["actor"],
                                    len(g["lines"]), g["exemplar"]))
            sys.stdout.write("не отвечено: %d из %d\n" % (len(bad), len(groups)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
