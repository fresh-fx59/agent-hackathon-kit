#!/usr/bin/env python3
"""v42 fix 8 — a verdict the CURSOR did not write must be caught and NAMED.

    python3 tools/tests/test_ledger_cursor_v42.py

WHY THIS TEST EXISTS
--------------------
v41 narrowed the ledger's read/write path to `tools/worklist.py`, and `SKILL.md`
step 2 said it in words: «read the worklist through the CURSOR, never as a file».
The independent review of the paid run `20260827T173511Z-v41` then found that the
parent session had edited ledger row `g228` directly, with a file-editing tool,
after that instruction. The boundary was documented and tooled and NOT ENFORCED:
`triagecheck` and `citecheck --ledger` read the resulting file, and two files with
the same bytes grade the same whoever wrote them. A cell that never went through
`apply_verdicts` was never subjected to the cursor's own refusals either — unknown
id, placeholder verdict, a tab that forges a column.

WHAT IS ASSERTED, in the order the review would ask it:

  1. a cursor write passes; the same cell written by hand is REFUSED;
  2. the g228 REGRESSION in shape — exactly one row's verdict appears without a
     cursor write, and the refusal NAMES that row;
  3. a legitimate multi-row batch still passes: no false positive on normal work;
  4. RECOVERY — re-submitting the same text through the cursor clears the defect,
     because a refusal whose only cure is deleting the row is the v37 regression
     this project already has a name for;
  5. FAIL-CLOSED — a missing, empty, truncated or unparseable journal, a missing
     key, a broken chain link: refusal, never a silent pass;
  6. FORGERY — a hand edit that also fabricates a journal entry is caught, because
     the entry carries an HMAC over the chain link and the key is a file no brief
     mentions. The honest limit is asserted too: an agent that READS the key can
     mint a valid entry, and the test says so out loud rather than pretending;
  7. `reseal` recovers a genuinely lost witness and REFUSES to launder a detected
     off-cursor write, and every reseal stays visible in the delivered directory;
  8. the block lands through `triagecheck`, which already owns the worklist
     contract — so `stopcheck` inherits it, exactly as fixes 2-5 landed.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILLS = os.path.join(SHERLOCK, "skills")
ARM = os.environ.get("SHERLOCK_SKILL", os.path.join(SKILLS, "v42"))
TOOL = os.path.join(ARM, "tools", "worklist.py")
TRIAGE = os.path.join(ARM, "tools", "triagecheck.py")
FAILED = []

HEADER = (u"# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
          u"# вердикт: ? не разобрано · D дефект · N норма · X данных не хватает\n")
GOOD = u"N a.log:%d «%s» n=%d фон"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(args, stdin=b"", tool=None):
    e = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.Popen([sys.executable, tool or TOOL] + args,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=e)
    out, err = p.communicate(stdin)
    return (p.returncode, out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"))


def make(rows=8, manifest=True):
    """A work dir shaped like one `logmap` builds, plus a one-line corpus."""
    d = tempfile.mkdtemp(prefix="ledger-")
    work = os.path.join(d, "work")
    corpus = os.path.join(d, "corpus")
    os.makedirs(work)
    os.makedirs(corpus)
    lines, ids = [HEADER], []
    with io.open(os.path.join(corpus, "a.log"), "w", encoding="utf-8") as fh:
        for i in range(1, rows + 2):
            fh.write(u"line %d token%d tail\n" % (i, i))
    for i in range(1, rows + 1):
        rid = u"g%03d" % i
        ids.append(rid)
        lines.append(u"%s\t?\tcat\ta.log:%d\tn=%d · окно\t{\"k\":\"v%d\"}\n"
                     % (rid, i, i, i))
    with io.open(os.path.join(work, "worklist.tsv"), "w", encoding="utf-8") as fh:
        fh.write(u"".join(lines))
    if manifest:
        with io.open(os.path.join(work, "worklist.manifest.json"), "w",
                     encoding="utf-8") as fh:
            fh.write(json.dumps({"schema": 1, "tool": "logmap.py",
                                 "rows": len(ids), "ids": ids,
                                 "sha256": "0" * 64}) + "\n")
    return work, corpus, ids


def ledger(work):
    return os.path.join(work, "worklist.tsv")


def journal(work):
    return os.path.join(work, "worklist.provenance.jsonl")


def hand_edit(work, rid, cell):
    """Exactly what a file-editing tool does — the g228 move."""
    path = ledger(work)
    out = []
    for raw in io.open(path, encoding="utf-8"):
        if raw.startswith(rid + u"\t"):
            cells = raw.rstrip(u"\n").split(u"\t")
            cells[1] = cell
            raw = u"\t".join(cells) + u"\n"
        out.append(raw)
    io.open(path, "w", encoding="utf-8").write(u"".join(out))


def close_rows(work, pairs):
    payload = u"".join(u"%s\t%s\n" % (rid, cell) for rid, cell in pairs)
    return run(["verdict", "--work", work, "--from-stdin"],
               payload.encode("utf-8"))


def verify(work):
    return run(["verify", "--work", work, "--json"])


def state(work):
    rc, out, err = verify(work)
    try:
        return rc, json.loads(out)
    except ValueError:
        return rc, {"state": "UNPARSEABLE", "raw": out + err}


def main():
    check("the arm has the cursor", os.path.exists(TOOL), TOOL)
    if not os.path.exists(TOOL):
        print("✗ FAILED: " + ", ".join(FAILED))
        return 1

    # 1. a cursor write passes; the same cell by hand is refused ------------
    work, corpus, ids = make()
    rc, _o, err = close_rows(work, [("g001", GOOD % (1, "token1", 1))])
    check("a cursor write exits 0", rc == 0, err)
    check("the cursor created its journal", os.path.exists(journal(work)))
    check("the cursor created its key 0600",
          os.path.exists(os.path.join(work, ".worklist-witness.key"))
          and oct(os.stat(os.path.join(work, ".worklist-witness.key")).st_mode
                  & 0o777) == "0o600")
    rc, d = state(work)
    check("a ledger written only by the cursor VERIFIES",
          rc == 0 and d["state"] == "ok", d)

    hand_edit(work, "g002", GOOD % (2, "token2", 2))
    rc, d = state(work)
    check("the SAME cell written by hand is REFUSED",
          rc != 0 and d["state"] == "off_cursor", d.get("state"))

    # 2. the g228 regression, in shape -------------------------------------
    work, corpus, ids = make(rows=250)
    batch = [(rid, GOOD % (i + 1, "token%d" % (i + 1), i + 1))
             for i, rid in enumerate(ids) if rid != "g228"]
    for i in range(0, len(batch), 20):
        rc, _o, err = close_rows(work, batch[i:i + 20])
        check("batch %d closes through the cursor" % (i // 20), rc == 0, err)
    rc, d = state(work)
    check("249 rows closed by the cursor still VERIFY", rc == 0
          and d["state"] == "ok", d.get("state"))
    hand_edit(work, "g228", u"N;Application.jsonl:6;\"crashpad_log\"")
    rc, d = state(work)
    check("REGRESSION g228: one hand-written verdict among 250 is refused",
          rc != 0 and d["state"] == "off_cursor", d.get("state"))
    named = [o["id"] for o in d.get("off_cursor", [])]
    check("the refusal NAMES g228 and only g228", named == ["g228"], named)
    _rc, text, _e = run(["verify", "--work", work])
    check("the refusal prints the ledger cell it will not accept",
          "crashpad_log" in text, text[-400:])
    check("the refusal prints the invocation that WOULD have been legitimate",
          "worklist.py verdict --work" in text and "--from-stdin" in text,
          text[-400:])
    check("the refusal says do not delete the row (the v37 regression)",
          "НЕ УДАЛЯЙ" in text, text[-400:])

    # 3. recovery: do it properly, the defect clears, triage is kept --------
    rc, _o, err = close_rows(
        work, [("g228", u"N;Application.jsonl:6;\"crashpad_log\"")])
    check("re-submitting the same text through the cursor exits 0", rc == 0, err)
    rc, d = state(work)
    check("RECOVERY: after the proper write the ledger verifies again",
          rc == 0 and d["state"] == "ok", d.get("state"))
    row = [l for l in io.open(ledger(work), encoding="utf-8")
           if l.startswith("g228\t")]
    check("recovery kept the triage work, byte for byte",
          row and row[0].split("\t")[1] == u"N;Application.jsonl:6;\"crashpad_log\"",
          row[:1])
    body = [l for l in io.open(ledger(work), encoding="utf-8")
            if l.strip() and not l.startswith("#")]
    check("recovery lost no row", len(body) == 250, len(body))

    # 4. fail-closed on every shape of a damaged witness --------------------
    def damaged(name, mutate, expect="broken"):
        w, _c, i = make()
        # three entries — genesis plus two writes — so that dropping the middle
        # one leaves a chain that must fail on its own terms, not merely a
        # baseline that happens to disagree with the file.
        close_rows(w, [(i[0], GOOD % (1, "token1", 1))])
        close_rows(w, [(i[1], GOOD % (2, "token2", 2))])
        mutate(w)
        rc, dd = state(w)
        check("fail-closed: %s" % name,
              rc != 0 and dd["state"] == expect, dd.get("state"))
        return w

    damaged("the journal is missing entirely",
            lambda w: os.unlink(journal(w)))
    damaged("the journal is empty",
            lambda w: io.open(journal(w), "w", encoding="utf-8").write(u""))
    damaged("the journal is truncated mid-line", lambda w: io.open(
        journal(w), "w", encoding="utf-8").write(
            io.open(journal(w), encoding="utf-8").read()[:-40]))
    damaged("the journal is not JSON", lambda w: io.open(
        journal(w), "a", encoding="utf-8").write(u"{not json\n"))
    damaged("the key is gone",
            lambda w: os.unlink(os.path.join(w, ".worklist-witness.key")))

    def drop_middle(w):
        lines = io.open(journal(w), encoding="utf-8").read().splitlines(True)
        io.open(journal(w), "w", encoding="utf-8").write(
            u"".join(lines[:1] + lines[2:]))
    damaged("an entry is dropped out of the middle of the chain", drop_middle)

    def tamper_bytes(w):
        path = ledger(w)
        s = io.open(path, encoding="utf-8").read()
        io.open(path, "w", encoding="utf-8").write(
            s.replace(u"{\"k\":\"v3\"}", u"{\"k\":\"EDITED\"}"))
    damaged("a column OTHER than the verdict was edited by hand", tamper_bytes)

    def delete_row(w):
        path = ledger(w)
        out = [l for l in io.open(path, encoding="utf-8")
               if not l.startswith("g004\t")]
        io.open(path, "w", encoding="utf-8").write(u"".join(out))
    damaged("a row was DELETED instead of closed", delete_row)

    # 5. forgery: fabricating the bookkeeping is not enough -----------------
    work, _c, ids = make()
    close_rows(work, [(ids[0], GOOD % (1, "token1", 1))])
    entries = [json.loads(l) for l in io.open(journal(work), encoding="utf-8")
               if l.strip()]
    hand_edit(work, "g002", GOOD % (2, "token2", 2))
    forged = dict(entries[-1])
    forged.update({"seq": len(entries), "kind": "verdict",
                   "writes": {"g002": GOOD % (2, "token2", 2)},
                   "rows": None, "prev": entries[-1]["link"],
                   "at": "2026-08-28T00:00:00+00:00", "note": ""})
    # the forger can do the arithmetic — it recomputes the chain link honestly
    import hashlib
    LINKED = ("schema", "seq", "at", "kind", "writes", "rows", "ledger", "prev",
              "note")
    forged["ledger"] = hashlib.sha256(
        open(ledger(work), "rb").read()).hexdigest()
    forged["link"] = hashlib.sha256(json.dumps(
        {k: forged.get(k) for k in LINKED}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    forged["mac"] = "0" * 64                       # what it cannot do: the MAC
    io.open(journal(work), "a", encoding="utf-8").write(
        json.dumps(forged, ensure_ascii=False, sort_keys=True) + u"\n")
    rc, d = state(work)
    check("a bypass that ALSO fabricates the journal entry is caught by the MAC",
          rc != 0 and d["state"] == "broken", d.get("state"))
    check("the refusal points at the fabricated entry",
          any("MAC" in b for b in d.get("broken", [])), d.get("broken"))

    # THE HONEST LIMIT, asserted rather than implied: the key is a file on the
    # same filesystem, and an agent that decides to read it can mint a valid
    # entry. Nothing in this sandbox prevents that; what the witness buys is
    # that the ACCIDENTAL bypass — the one that actually happened on g228 —
    # cannot produce an entry at all, and the deliberate one has to touch a key
    # no brief, reference or SKILL.md step ever names.
    work2, _c2, ids2 = make()
    close_rows(work2, [(ids2[0], GOOD % (1, "token1", 1))])
    import hmac as _hmac
    key = bytes.fromhex(io.open(os.path.join(work2, ".worklist-witness.key"),
                                encoding="utf-8").read().strip())
    hand_edit(work2, "g002", GOOD % (2, "token2", 2))
    prev = [json.loads(l) for l in io.open(journal(work2), encoding="utf-8")
            if l.strip()][-1]
    e = {"schema": 1, "seq": prev["seq"] + 1, "at": "2026-08-28T00:00:00+00:00",
         "kind": "verdict", "writes": {"g002": GOOD % (2, "token2", 2)},
         "rows": None,
         "ledger": hashlib.sha256(open(ledger(work2), "rb").read()).hexdigest(),
         "prev": prev["link"], "note": ""}
    e["link"] = hashlib.sha256(json.dumps(
        {k: e.get(k) for k in LINKED}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    e["mac"] = _hmac.new(key, e["link"].encode("ascii"),
                         hashlib.sha256).hexdigest()
    io.open(journal(work2), "a", encoding="utf-8").write(
        json.dumps(e, ensure_ascii=False, sort_keys=True) + u"\n")
    rc, d = state(work2)
    check("DOCUMENTED LIMIT: an agent that reads the key CAN forge a valid "
          "entry — tamper-evident, not tamper-proof",
          rc == 0 and d["state"] == "ok", d.get("state"))
    check("and the docstring says so, so the limit is not a surprise",
          "EVIDENT, not tamper-proof" in
          io.open(TOOL, encoding="utf-8").read())

    # 6. reseal recovers a lost witness and refuses to launder --------------
    work, _c, ids = make()
    close_rows(work, [(ids[0], GOOD % (1, "token1", 1))])
    hand_edit(work, "g002", GOOD % (2, "token2", 2))
    rc, out, err = run(["reseal", "--work", work, "--reason", "мне так удобнее"])
    check("reseal REFUSES while the journal is intact — it cannot launder an "
          "off-cursor write", rc != 0, out + err)
    check("and it points at the cursor instead",
          "verdict --work" in (out + err), (out + err)[:300])
    os.unlink(journal(work))
    rc, out, err = run(["reseal", "--work", work])
    check("reseal without --reason is refused", rc != 0, out + err)
    rc, out, err = run(["reseal", "--work", work,
                        "--reason", "журнал потерян при передаче стадии"])
    check("reseal rebuilds a genuinely LOST baseline", rc == 0, err)
    rc, d = state(work)
    check("after a reseal the ledger verifies", rc == 0 and d["state"] == "ok",
          d.get("state"))
    check("and the reseal stays visible in the delivered directory",
          d.get("resealed") and "потерян" in d["resealed"][0]["note"],
          d.get("resealed"))
    _rc, text, _e = run(["verify", "--work", work])
    check("verify prints the reseal loudly even when the state is ok",
          "ПЕРЕСТАВЛЕНА" in text, text)

    # 7. a hand-made fixture with no manifest is graded, not blocked --------
    work, _c, ids = make(manifest=False)
    hand_edit(work, "g001", GOOD % (1, "token1", 1))
    rc, d = state(work)
    check("no journal and no logmap manifest = unwitnessed, graded not blocked "
          "(the rule citecheck.worklist_removed already uses)",
          rc == 0 and d["state"] == "unwitnessed", d.get("state"))
    work, _c, ids = make(manifest=True)
    hand_edit(work, "g001", GOOD % (1, "token1", 1))
    rc, d = state(work)
    check("a logmap-built directory with closed rows and NO journal is refused",
          rc != 0 and d["state"] == "broken", d.get("state"))

    # 8. the block lands through triagecheck -------------------------------
    def triage(work, corpus):
        rules = os.path.join(work, "rules.tsv")
        if not os.path.exists(rules):
            io.open(rules, "w", encoding="utf-8").write(u"# нет правил\n")
        return run(["--worklist", ledger(work), "--rules", rules,
                    "--corpus", corpus], tool=TRIAGE)

    work, corpus, ids = make()
    close_rows(work, [(rid, u"N a.log:%d «token%d» n=%d фон" % (i + 1, i + 1, i + 1))
                      for i, rid in enumerate(ids)])
    rc0, out0, err0 = triage(work, corpus)
    check("triagecheck on a fully cursor-written ledger reports no witness "
          "defect", "ledger_write_off_cursor" not in out0, out0[-600:])
    base_blocking = rc0
    hand_edit(work, "g003", u"N a.log:3 «token3» n=3 фон правка руками")
    rc1, out1, err1 = triage(work, corpus)
    check("triagecheck BLOCKS on an off-cursor verdict", rc1 != 0, out1[-600:])
    check("triagecheck names the defect ledger_write_off_cursor",
          "ledger_write_off_cursor" in out1, out1[-600:])
    check("triagecheck names the offending row", "g003" in out1, out1[-800:])
    check("and prints the legitimate invocation, not «delete it»",
          "--from-stdin" in out1, out1[-800:])
    rc, jout, _e = run(["--worklist", ledger(work), "--rules",
                        os.path.join(work, "rules.tsv"), "--corpus", corpus,
                        "--json"], tool=TRIAGE)
    j = json.loads(jout)
    check("the defect is COUNTED separately, like fixes 2-5",
          j["totals"]["вердиктов не через курсор"] == 1,
          j["totals"].get("вердиктов не через курсор"))
    check("and it is summed into blocking", j["blocking"] >= 1, j["blocking"])
    close_rows(work, [("g003", u"N a.log:3 «token3» n=3 фон правка руками")])
    rc2, out2, _e = triage(work, corpus)
    check("RECOVERY through triagecheck too: the proper write clears it",
          rc2 == base_blocking and "ledger_write_off_cursor" not in out2,
          out2[-600:])
    os.unlink(journal(work))
    rc3, out3, _e = triage(work, corpus)
    check("triagecheck fails closed on a destroyed witness",
          rc3 != 0 and "ledger_witness_broken" in out3, out3[-600:])
    j = json.loads(run(["--worklist", ledger(work), "--rules",
                        os.path.join(work, "rules.tsv"), "--corpus", corpus,
                        "--json"], tool=TRIAGE)[1])
    check("ledger_witness_broken is its own counted defect",
          j["totals"]["свидетель курсора сломан"] == 1,
          j["totals"].get("свидетель курсора сломан"))

    # 9. the human-facing text says the same thing -------------------------
    skill = io.open(os.path.join(ARM, "SKILL.md"), encoding="utf-8").read()
    check("SKILL.md step 2 documents the witness",
          "worklist.py verify" in skill, "")
    check("SKILL.md §8 makes an off-cursor verdict a stopping condition",
          "ledger_write_off_cursor" in skill, "")
    tools_md = io.open(os.path.join(ARM, "reference", "tools.md"),
                       encoding="utf-8").read()
    check("reference/tools.md documents worklist.py and its witness",
          "worklist.py" in tools_md and "provenance.jsonl" in tools_md, "")

    check("v41 does NOT gain the witness — it is frozen",
          "provenance" not in io.open(
              os.path.join(SKILLS, "v41", "tools", "worklist.py"),
              encoding="utf-8").read())

    print(("✗ FAILED: " + ", ".join(FAILED)) if FAILED
          else "✓ only the cursor can close a row, and a hand edit is named")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
