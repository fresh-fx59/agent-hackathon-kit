#!/usr/bin/env python3
"""v33 — triagecheck refreshes a stale work/checkpoint.json to the truth.

THE MEASURED DEFECT
--------------------
A run resolved all 250 worklist rows and work/checkpoint.json still read
`{"state": "resume_triage", "resolved": 159, "unresolved": 91}` — the stale
snapshot from `checkpoint.py init` at the start of TRIAGE. Nothing ever
refreshed it. A resume from that checkpoint replays the whole TRIAGE phase —
on a paid provider, that means paying twice for work already on disk.

triagecheck.py already scans the worklist(s) to grade them, so it can
recompute the same truth `checkpoint.py init` computes (via
`inspect_worklists`) and rewrite the checkpoint atomically, as a side effect
that never touches its own exit code.

Plain python3, no pytest — prints ✓/✗ lines, returns 0/1.

    python3 cases/06-dev-logging/sherlock/tools/tests/test_triagecheck_v33_checkpoint_refresh.py
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
SKILLS = os.path.join(SHERLOCK, "skills")
V33 = os.path.join(SKILLS, "v33")
TRIAGE = os.path.join(V33, "tools", "triagecheck.py")

FAILED = []


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name + (("  — " + detail) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


HEADER = ("# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
          "# вердикт: ? не разобрано · D дефект · N норма · X данных не хватает\n")


def make_corpus(root, host="node-01", fname="app.log", lines=8):
    logdir = os.path.join(root, host, "logs")
    os.makedirs(logdir)
    body = "\n".join(
        "2031-03-01T00:%02d:00+00:00 app[1]: line %d ok" % (i, i)
        for i in range(lines)
    )
    with open(os.path.join(logdir, fname), "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    return host, fname


def worklist_rows(host, fname, n, resolved_n, prefix="g"):
    """n rows total, the first resolved_n carry a verdict, the rest are '?'."""
    rows = []
    for i in range(n):
        verdict = "N фон" if i < resolved_n else "?"
        rows.append(["%s%04d" % (prefix, i), verdict, "cat",
                    "%s/logs/%s:%d" % (host, fname, (i % 8) + 1), "n=1",
                    "line %d ok" % i])
    return rows


def write_worklist(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(HEADER)
        for r in rows:
            fh.write("\t".join(r) + "\n")


def run_triage(worklist, corpus, extra=()):
    argv = [sys.executable, TRIAGE, "--worklist", worklist,
            "--corpus", corpus, "--json"]
    p = subprocess.run(argv + list(extra), capture_output=True, text=True)
    try:
        d = json.loads(p.stdout or "{}")
    except ValueError:
        d = {}
    return p.returncode, d, p.stdout, p.stderr


def leftover_temp_files(work_dir):
    return [p for p in glob.glob(os.path.join(work_dir, ".*checkpoint.json.*"))]


def main():
    # -----------------------------------------------------------------
    # 1. stale checkpoint + fully resolved worklist -> ready_for_synthesis
    # -----------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        host, fname = make_corpus(tmp)
        worklist = os.path.join(tmp, "worklist.tsv")
        rows = worklist_rows(host, fname, n=250, resolved_n=250)
        write_worklist(worklist, rows)

        checkpoint_path = os.path.join(tmp, "checkpoint.json")
        stale = {"schema": 1, "state": "resume_triage", "total": 250,
                 "resolved": 159, "unresolved": 91, "worklists": {},
                 "updated_at": "2026-08-01T00:00:00+00:00"}
        with open(checkpoint_path, "w", encoding="utf-8") as fh:
            json.dump(stale, fh)

        rc, d, out, err = run_triage(worklist, tmp)
        with open(checkpoint_path, encoding="utf-8") as fh:
            refreshed = json.load(fh)
        check("stale checkpoint becomes ready_for_synthesis",
              refreshed["state"] == "ready_for_synthesis", json.dumps(refreshed))
        check("resolved count matches the fully-resolved worklist",
              refreshed["resolved"] == 250, str(refreshed.get("resolved")))
        check("unresolved is zero", refreshed["unresolved"] == 0)
        check("triagecheck prints a Russian refresh line",
              "checkpoint" in out and "обновлён" in out, out[:200])
        check("no leftover temp files after the atomic write",
              leftover_temp_files(tmp) == [], str(leftover_temp_files(tmp)))

    # -----------------------------------------------------------------
    # 2. genuinely unresolved worklist -> correct unresolved count, no
    #    false ready_for_synthesis claim
    # -----------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        host, fname = make_corpus(tmp)
        worklist = os.path.join(tmp, "worklist.tsv")
        rows = worklist_rows(host, fname, n=250, resolved_n=159)
        write_worklist(worklist, rows)

        checkpoint_path = os.path.join(tmp, "checkpoint.json")
        stale = {"schema": 1, "state": "resume_triage", "total": 250,
                 "resolved": 159, "unresolved": 91, "worklists": {},
                 "updated_at": "2026-08-01T00:00:00+00:00"}
        with open(checkpoint_path, "w", encoding="utf-8") as fh:
            json.dump(stale, fh)

        rc, d, out, err = run_triage(worklist, tmp)
        with open(checkpoint_path, encoding="utf-8") as fh:
            refreshed = json.load(fh)
        check("still-unresolved worklist keeps state=resume_triage",
              refreshed["state"] == "resume_triage", json.dumps(refreshed))
        check("unresolved count is recorded correctly (91)",
              refreshed["unresolved"] == 91, str(refreshed.get("unresolved")))
        check("does not claim ready_for_synthesis",
              refreshed["state"] != "ready_for_synthesis")

    # -----------------------------------------------------------------
    # 3. no checkpoint file present, no --refresh-checkpoint -> nothing created
    # -----------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        host, fname = make_corpus(tmp)
        worklist = os.path.join(tmp, "worklist.tsv")
        rows = worklist_rows(host, fname, n=10, resolved_n=10)
        write_worklist(worklist, rows)

        rc, d, out, err = run_triage(worklist, tmp)
        checkpoint_path = os.path.join(tmp, "checkpoint.json")
        check("no checkpoint.json is invented when none existed",
              not os.path.exists(checkpoint_path))
        check("no refresh line printed either", "checkpoint" not in out.lower()
              or "обновлён" not in out, out[:200])

    # -----------------------------------------------------------------
    # 3b. --refresh-checkpoint DOES create one where none existed
    # -----------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        host, fname = make_corpus(tmp)
        worklist = os.path.join(tmp, "worklist.tsv")
        rows = worklist_rows(host, fname, n=10, resolved_n=10)
        write_worklist(worklist, rows)

        rc, d, out, err = run_triage(worklist, tmp, extra=["--refresh-checkpoint"])
        checkpoint_path = os.path.join(tmp, "checkpoint.json")
        check("--refresh-checkpoint creates a checkpoint from nothing",
              os.path.exists(checkpoint_path))
        if os.path.exists(checkpoint_path):
            with open(checkpoint_path, encoding="utf-8") as fh:
                created = json.load(fh)
            check("the created checkpoint is ready_for_synthesis",
                  created["state"] == "ready_for_synthesis", json.dumps(created))

    # -----------------------------------------------------------------
    # 4. exit code is unaffected by the refresh, with and without a
    #    pre-existing checkpoint file
    # -----------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        for tmp in (tmp_a, tmp_b):
            host, fname = make_corpus(tmp)
            worklist = os.path.join(tmp, "worklist.tsv")
            rows = worklist_rows(host, fname, n=20, resolved_n=12)
            write_worklist(worklist, rows)
        checkpoint_path = os.path.join(tmp_b, "checkpoint.json")
        with open(checkpoint_path, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "state": "resume_triage", "total": 20,
                      "resolved": 5, "unresolved": 15, "worklists": {},
                      "updated_at": "2026-08-01T00:00:00+00:00"}, fh)

        rc_a, d_a, _, _ = run_triage(os.path.join(tmp_a, "worklist.tsv"), tmp_a)
        rc_b, d_b, _, _ = run_triage(os.path.join(tmp_b, "worklist.tsv"), tmp_b)
        check("exit code identical with vs. without a checkpoint file to refresh",
              rc_a == rc_b, "rc_a=%d rc_b=%d" % (rc_a, rc_b))

    # -----------------------------------------------------------------
    # 5. multi-host layout: several worklist-<host>.tsv files counted together
    # -----------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        host1, fname1 = make_corpus(tmp, host="node-01", fname="app.log")
        host2 = "node-02"
        logdir2 = os.path.join(tmp, host2, "logs")
        os.makedirs(logdir2)
        with open(os.path.join(logdir2, "app.log"), "w", encoding="utf-8") as fh:
            fh.write("\n".join("2031-03-01T00:%02d:00+00:00 app[1]: line %d ok" % (i, i)
                               for i in range(8)) + "\n")

        wl1 = os.path.join(tmp, "worklist-node-01.tsv")
        wl2 = os.path.join(tmp, "worklist-node-02.tsv")
        write_worklist(wl1, worklist_rows(host1, fname1, n=100, resolved_n=100, prefix="a"))
        write_worklist(wl2, worklist_rows(host2, "app.log", n=100, resolved_n=100, prefix="b"))

        checkpoint_path = os.path.join(tmp, "checkpoint.json")
        with open(checkpoint_path, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "state": "resume_triage", "total": 200,
                      "resolved": 100, "unresolved": 100, "worklists": {},
                      "updated_at": "2026-08-01T00:00:00+00:00"}, fh)

        # triagecheck grades one worklist at a time, but the refresh must scan
        # every worklist*.tsv in the same directory, not just the one passed.
        rc, d, out, err = run_triage(wl1, tmp)
        with open(checkpoint_path, encoding="utf-8") as fh:
            refreshed = json.load(fh)
        check("multi-host: total counts both worklists (200)",
              refreshed["total"] == 200, str(refreshed.get("total")))
        check("multi-host: resolved counts both worklists (200)",
              refreshed["resolved"] == 200, str(refreshed.get("resolved")))
        check("multi-host: state reflects the combined truth",
              refreshed["state"] == "ready_for_synthesis", json.dumps(refreshed))

    print()
    if FAILED:
        print("✗ triagecheck checkpoint refresh: %d проверок упало" % len(FAILED))
        return 1
    print("✓ triagecheck checkpoint refresh: все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
