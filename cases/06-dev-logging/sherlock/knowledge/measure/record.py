#!/usr/bin/env python3
"""Append one measurement to knowledge/measure/runs.jsonl.

Records cost (steps, wall clock, tokens) AND a deterministic quality checklist,
because "faster" only counts if the answer is still right. The checklist facts
were verified by hand against the corpora on 2026-07-28 (see RESULTS.md).

    record.py <out.json> <dataset> <arm> <rep> <elapsed_s> <dataset_dir> <cards> <ledger>
"""
import json
import os
import re
import sys

# Facts checked by hand in the corpora. Each entry: regex the answer must match.
CHECKS = {
    "OpenSSH": {
        "bruteforce": r"(?i)brute[- ]?force|перебор|подбор пароля",
        "attacker":   r"187\.141\.143\.180|5\.188\.10\.180|103\.99\.0\.122|173\.234\.31\.186",
        "escalation": r"fztu|119\.137\.62\.142",          # the single successful login
        "action":     r"(?i)PasswordAuthentication|PermitRootLogin|fail2ban|pam_faillock",
    },
    "Linux": {
        "bruteforce": r"(?i)brute[- ]?force|перебор|подбор пароля",
        "attacker":   r"150\.183\.249\.110|218\.188\.2\.4|207\.243\.167\.114|60\.30\.224\.116|195\.129\.24\.210",
        "escalation": r"(?i)uid=509|user test|пользовател\w+ test|session opened for user test",
        "action":     r"(?i)PasswordAuthentication|PermitRootLogin|fail2ban|pam_faillock",
        "coverage":   r"(?i)ftpd|ftp",                     # the OTHER signal in this corpus
    },
}

KNOW_LINE = re.compile(r"ЗНАНИЯ\s*:\s*(.{0,120})")


def line_counts(root):
    out = {}
    if not os.path.isdir(root):
        return out
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if os.path.isfile(p) and not name.endswith(".gz"):
            try:
                with open(p, "rb") as fh:
                    out[name] = sum(1 for _ in fh)
            except OSError:
                pass
    return out


def main():
    out, ds, arm, rep, elapsed, dsdir, cards, ledger = sys.argv[1:9]
    d = json.load(open(out, encoding="utf-8"))
    d = d if isinstance(d, list) else [d]
    final = next((r for r in d if r.get("type") == "result"), None)
    sysrec = next((r for r in d if r.get("type") == "system"), {})
    if final is None:
        print("  ✗ no final result record")
        return 1
    if final.get("is_error"):
        e = final.get("error") or {}
        print("  ✗ error:", e.get("message", e))
        return 1

    text = final.get("result") or ""
    u = final.get("usage") or {}

    # --- did the agent actually read the knowledge base? ---
    tool_calls, read_kb, read_cards, read_rejected, read_skill = 0, 0, 0, 0, 0
    kb_paths = []
    for r in d:
        for c in ((r.get("message") or {}).get("content") or []):
            if isinstance(c, dict) and c.get("type") == "tool_use":
                tool_calls += 1
                blob = json.dumps(c.get("input") or {}, ensure_ascii=False)
                if "knowledge" in blob:
                    read_kb += 1
                    kb_paths.append(blob[:200])
                if "patterns" in blob:
                    read_cards += 1
                if "REJECTED" in blob:
                    read_rejected += 1
                if "SKILL.md" in blob:
                    read_skill += 1

    # --- citations: file:N references, and whether N is inside the file ---
    counts = line_counts(dsdir)
    refs = re.findall(r"([A-Za-z0-9_.\-]+\.(?:log|txt|json|out)):(\d+)", text)
    in_range = sum(1 for f, n in refs if f in counts and 1 <= int(n) <= counts[f])

    checks = {k: bool(re.search(v, text)) for k, v in CHECKS.get(ds, {}).items()}
    m = KNOW_LINE.search(text)

    rec = {
        "dataset": ds, "arm": arm, "rep": int(rep), "cards_in_kb": int(cards),
        "model": sysrec.get("model"),
        "turns": final.get("num_turns"), "tool_calls": tool_calls,
        "duration_s": int(elapsed),
        "api_s": round((final.get("duration_api_ms") or 0) / 1000, 1),
        "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
        "answer_chars": len(text),
        "line_refs": len(refs), "line_refs_in_range": in_range,
        "kb_reads": read_kb, "card_reads": read_cards,
        "rejected_reads": read_rejected, "skill_reads": read_skill,
        "kb_tool_inputs": kb_paths[:8],
        "knowledge_line": (m.group(1).strip() if m else None),
        "quality": checks,
        "quality_score": sum(checks.values()),
        "quality_max": len(checks),
        "answer": text,
    }
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("  ✓ turns=%s tools=%s %ss  in/out=%s/%s  chars=%d  refs=%d(%d ok)  "
          "kb_reads=%d  quality=%d/%d %s"
          % (rec["turns"], tool_calls, elapsed, rec["input_tokens"],
             rec["output_tokens"], rec["answer_chars"], rec["line_refs"],
             in_range, read_kb, rec["quality_score"], rec["quality_max"],
             "".join("✓" if v else "✗" for v in checks.values())))
    if rec["knowledge_line"]:
        print("    ЗНАНИЯ: %s" % rec["knowledge_line"][:100])
    else:
        print("    ЗНАНИЯ: <строки нет>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
