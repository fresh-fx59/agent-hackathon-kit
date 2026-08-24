#!/usr/bin/env python3
"""v32 P6: sessions × state changes, as a table instead of a noticing.

The corpus's decisive fact was an INTERVAL: an inbound RDP session with a
service install inside it. Both halves were present, in two different channels;
nothing joined them but a model reading two timestamps. Under v31 neither arm
made the link unaided.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
TOOLS = ROOT / "cases" / "06-dev-logging" / "sherlock" / "skills" / "v32" / "tools"
FAILED = []
USER_SID = "S-1-5-21-2929202171-1942120112-2054978817-1001"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def ev(provider, eid, when, data=None, security=None, userdata=False):
    e = {"System": {"Provider": {"#attributes": {"Name": provider}},
                    "EventID": eid,
                    "TimeCreated": {"#attributes": {"SystemTime": when}}}}
    if security:
        e["System"]["Security"] = {"#attributes": {"UserID": security}}
    key = "UserData" if userdata else "EventData"
    e[key] = {"EventXML": data or {}} if userdata else (data or {})
    return json.dumps({"Event": e}, ensure_ascii=False)


TS = "Microsoft-Windows-TerminalServices-LocalSessionManager"


def corpus(root):
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "TerminalServices.jsonl").write_text("\n".join([
        ev(TS, 21, "2021-05-09T21:43:09Z",
           {"User": "IPSERVER\\root", "SessionID": 2, "Address": "192.99.186.31"}, userdata=True),
        ev(TS, 21, "2021-05-11T09:00:00Z",
           {"User": "IPSERVER\\admin", "SessionID": 3, "Address": "10.0.0.5"}, userdata=True),
        ev(TS, 23, "2021-05-09T23:00:00Z",
           {"User": "IPSERVER\\root", "SessionID": 2, "Address": "192.99.186.31"}, userdata=True),
    ]) + "\n", encoding="utf-8")
    (d / "System.jsonl").write_text("\n".join([
        ev("Service Control Manager", 7045, "2021-05-08T10:00:00Z",
           {"ServiceName": "PlatformSvc"}, security="S-1-5-18"),          # before
        ev("Service Control Manager", 7045, "2021-05-09T22:25:01Z",
           {"ServiceName": "3proxy tiny proxy server"}, security=USER_SID),  # inside
        ev("Service Control Manager", 7045, "2021-05-12T10:00:00Z",
           {"ServiceName": "LaterSvc"}, security="S-1-5-18"),             # after
    ]) + "\n", encoding="utf-8")
    return root


def run(corpus_dir, *extra):
    cmd = [sys.executable, str(TOOLS / "logjoin.py"), "--window",
           "--corpus", corpus_dir] + list(extra)
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p


def main():
    with tempfile.TemporaryDirectory() as td:
        c = corpus(os.path.join(td, "corpus"))

        p = run(c, "--json")
        check("--window exits 0", p.returncode == 0, p.stderr[-300:])
        d = json.loads(p.stdout)
        check("both sessions are found", d["sessions_total"] == 2, str(d["sessions_total"]))
        check("only the routable session is reported",
              d["sessions_reported"] == 1, str(d["sessions_reported"]))
        w = d["windows"][0]
        check("the session carries its address and user",
              w["session"]["address"] == "192.99.186.31"
              and "root" in w["session"]["user"], json.dumps(w["session"])[:200])
        check("the session's own close ends the window", w["session"]["closed"])
        lines = [c_["line"] for c_ in w["changes"]]
        check("the install inside the window is joined", lines == [2], str(lines))
        check("the change is placed in time, not just listed",
              w["changes"][0]["after_s"] == 41 * 60 + 52,
              str(w["changes"][0]["after_s"]))
        check("groups are keyed by actor, rarest first",
              w["groups"][0]["actor"] == USER_SID, json.dumps(w["groups"])[:200])

        p = run(c, "--all-addresses", "--json")
        d = json.loads(p.stdout)
        check("--all-addresses shows the local session too",
              d["sessions_reported"] == 2, str(d["sessions_reported"]))

        p = run(c)
        check("the text render names the joined install",
              "System.jsonl:2" in p.stdout and "3proxy" in p.stdout, p.stdout[:400])
        check("the text render dates the session",
              "2021-05-09T21:43:09Z" in p.stdout, p.stdout[:300])

    # A corpus with no Windows sessions at all must say so, not crash.
    with tempfile.TemporaryDirectory() as td:
        c = os.path.join(td, "corpus"); os.makedirs(c)
        Path(c, "app.log").write_text("2021-05-09T21:43:09Z plain text line\n", encoding="utf-8")
        p = run(c)
        check("a corpus with no sessions exits 0 and says so",
              p.returncode == 0 and "ни одного сеанса" in p.stdout, p.stdout[:200])

    print()
    if FAILED:
        print("✗ logjoin --window: %d проверок упало" % len(FAILED))
        return 1
    print("✓ logjoin --window: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
