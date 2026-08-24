#!/usr/bin/env python3
"""statecheck: the census must make a false negative fail.

The shape under test is the v31 miss: fifteen platform service installs and one
installed by a different principal. A report that dispositions the platform
fifteen and never mentions the sixteenth must NOT pass.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "..", "skills", "v32", "tools", "statecheck.py")
FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def rec(provider, eid, data=None, security=None):
    ev = {"System": {"Provider": {"#attributes": {"Name": provider}},
                     "EventID": {"#attributes": {"Qualifiers": 16384}, "#text": eid}}}
    if security:
        ev["System"]["Security"] = {"#attributes": {"UserID": security}}
    ev["EventData"] = data or {}
    return json.dumps({"Event": ev}, ensure_ascii=False)


SYS_SID = "S-1-5-18"
USER_SID = "S-1-5-21-2929202171-1942120112-2054978817-1001"


def build(tmp):
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus)
    lines = []
    for i in range(15):
        lines.append(rec("Service Control Manager", 7045,
                         {"ServiceName": "PlatformSvc%d" % i,
                          "ImagePath": "C:\\WINDOWS\\system32\\svc%d.exe" % i},
                         security=SYS_SID))
    lines.append(rec("Service Control Manager", 7045,
                     {"ServiceName": "3proxy tiny proxy server",
                      "ImagePath": "\"C:\\3proxy\\bin64\\3proxy.exe\" --service"},
                     security=USER_SID))          # line 16 — the whole point
    lines.append(rec("EventLog", 6009, {}, security=SYS_SID))   # not catalogued
    with open(os.path.join(corpus, "System.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    fw = []
    for i in range(30):
        fw.append(rec("Microsoft-Windows-Windows Firewall With Advanced Security", 2004,
                      {"RuleName": "Platform rule %d" % i,
                       "ModifyingUser": "S-1-5-80-3088073201-1464728630"},
                      security="S-1-5-19"))
    fw.append(rec("Microsoft-Windows-Windows Firewall With Advanced Security", 2005,
                  {"RuleName": "3proxy - tiny proxy server",
                   "ApplicationPath": "C:\\3proxy\\bin64\\3proxy.exe",
                   "ModifyingUser": USER_SID},
                  security="S-1-5-19"))           # line 31
    with open(os.path.join(corpus, "Firewall%4Operational.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(fw) + "\n")

    # A volume channel that shares an EventID with the firewall catalogue.
    with open(os.path.join(corpus, "Store%4Operational.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(50):
            fh.write(rec("Microsoft-Windows-StoreAgent", 2006, {"param1": "x"}) + "\n")
    return corpus


def run(corpus, report=None, as_json=True):
    cmd = [sys.executable, TOOL, "--corpus", corpus]
    if report:
        cmd += ["--report", report]
    if as_json:
        cmd += ["--json"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    payload = json.loads(p.stdout) if as_json and p.stdout.strip() else None
    return p.returncode, payload, p.stdout + p.stderr


def main():
    with tempfile.TemporaryDirectory() as tmp:
        corpus = build(tmp)

        rc, js, _ = run(corpus)
        check("census exits 0 with no report", rc == 0)
        keys = {(g["file"], g["class"], g["actor"]) for g in js["groups"]}
        check("provider keying ignores a foreign EventID 2006",
              not any(g["file"].startswith("Store") for g in js["groups"]),
              "Store channel leaked into the census")
        check("platform service installs collapse into one group",
              ("System.jsonl", "service-install", SYS_SID) in keys)
        check("the odd principal gets a group of its own",
              ("System.jsonl", "service-install", USER_SID) in keys)
        check("firewall groups key on ModifyingUser, not the service identity",
              ("Firewall%4Operational.jsonl", "firewall-rule-change", USER_SID) in keys,
              "grouped by Security/UserID S-1-5-19 — the intruder rule was swallowed")
        intruder = [g for g in js["groups"] if g["actor"] == USER_SID and g["file"] == "System.jsonl"][0]
        check("the odd install cites its own line", intruder["lines"] == [16])
        check("census counts every catalogued record, none sampled",
              js["total_records"] == 15 + 1 + 30 + 1, str(js["total_records"]))

        # The v31 report shape: the platform bulk dispositioned, the intruder absent.
        false_negative = os.path.join(tmp, "fn.md")
        with open(false_negative, "w", encoding="utf-8") as fh:
            fh.write("## Находки\nШтатные установки служб, System.jsonl:1 — норма.\n"
                     "Правила брандмауэра, Firewall%4Operational.jsonl:1 — норма.\n")
        rc, js, out = run(corpus, false_negative)
        check("a report that misses the odd install FAILS", rc == 1, "exit %d" % rc)
        check("the failure names the missed line", js["unaccounted"] == 2, out[:400])

        # Accounting the odd rows — anywhere in the report, finding or disposition.
        full = os.path.join(tmp, "full.md")
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("Штатные установки, System.jsonl:1 — норма.\n"
                     "3proxy установлен: System.jsonl:16.\n"
                     "Правила: Firewall%4Operational.jsonl:1 и Firewall%4Operational.jsonl:31.\n")
        rc, js, out = run(corpus, full)
        check("a report that answers every group PASSES", rc == 0, out[:400])
        check("no group left unaccounted", js["unaccounted"] == 0)

        # Any line of a group discharges it — one citation, not fifteen.
        partial = os.path.join(tmp, "partial.md")
        with open(partial, "w", encoding="utf-8") as fh:
            fh.write("System.jsonl:9 System.jsonl:16 Firewall%4Operational.jsonl:22 "
                     "Firewall%4Operational.jsonl:31\n")
        rc, _, out = run(partial and corpus, partial)
        check("one line discharges its whole group", rc == 0, out[:300])

        # A citation to the right file at a line that is not in the group is not an answer.
        wrong = os.path.join(tmp, "wrong.md")
        with open(wrong, "w", encoding="utf-8") as fh:
            fh.write("System.jsonl:17 Firewall%4Operational.jsonl:1 System.jsonl:1\n")
        rc, js, out = run(corpus, wrong)
        check("an off-group line number does not discharge a group", rc == 1, out[:300])

        # A channel export whose name contains spaces: the citation harvested
        # from prose is a SUFFIX of the file name, not its basename.
        spaced = os.path.join(corpus, "Windows Firewall With Advanced Security%4Firewall.jsonl")
        os.rename(os.path.join(corpus, "Firewall%4Operational.jsonl"), spaced)
        suffix = os.path.join(tmp, "suffix.md")
        with open(suffix, "w", encoding="utf-8") as fh:
            fh.write("System.jsonl:1 System.jsonl:16\n"
                     "правило: Security%4Firewall.jsonl:31 и Security%4Firewall.jsonl:1\n")
        rc, js, out = run(corpus, suffix)
        check("a suffix citation of a spaced file name counts", rc == 0, out[:400])

    print()
    if FAILED:
        print("✗ statecheck: %d проверок упало" % len(FAILED))
        return 1
    print("✓ statecheck: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
