#!/usr/bin/env python3
"""Labels live at a POSITION, not anywhere in the text.

The 32 blockers on 20260830T190815Z-v42 were corpus usernames (IPSERVER, ADMINI,
ALINA), service names (ESENT, MSDTC, UMFD) and SQL (SELECT, FROM, WHERE) read as
labels, because the contract scanned every uppercase token in the assertion. The
model's response was to append 15 tokens to labels.ignore — the growing
special-case list. There is now no list to grow: a label is recognised only at a
fixed syntactic position, and validated against exactly three literals.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
V43 = os.path.join(SHERLOCK, "skills", "v43")
FAILED = []

spec = importlib.util.spec_from_file_location(
    "reportcheck43", os.path.join(V43, "tools", "reportcheck.py"))
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

CONTRACT = rc.load_contract(os.path.join(V43, "reference", "report-contract.corporate.json"))

CITE = "winevt.zip.d-Logs-Security.jsonl:1841"


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


def defects(body, role="finding"):
    """Run check_labels over a single synthetic section."""
    sections = [{"role": role, "title": "Н-1",
                 "lines": body.splitlines(), "body": body}]
    return [d["defect"] for d in rc.check_labels(sections, CONTRACT)]


# --- the contract itself -------------------------------------------------
check("ignore" not in CONTRACT["labels"],
      "labels.ignore still exists — the special-case list must be gone")
check("candidate" not in CONTRACT["labels"],
      "labels.candidate still exists — the free-text scan must be gone")
check(sorted(CONTRACT["labels"]["allowed"]) == ["INFERENCE", "PROVEN", "REPORTED"],
      "allowed is not exactly the three literals: %r" % CONTRACT["labels"]["allowed"])

# --- the 32-blocker class is gone ---------------------------------------
evidence = ("- [!PROVEN] `%s` «\"TargetUserName\":\"ADMINI\"» — SELECT FROM WHERE, "
            "IPSERVER, ALINA, ESENT, MSDTC, UMFD, NT AUTHORITY\\LOCAL SERVICE\n" % CITE)
check(defects(evidence) == [],
      "corpus identifiers in evidence still read as labels: %r" % defects(evidence))

# --- position: list item -------------------------------------------------
check(defects("- [!PROVEN] `%s` текст\n" % CITE) == [],
      "a correct list item must produce no defect")
check(defects("- PROVEN: `%s` текст\n" % CITE) == ["assertion_unlabelled"],
      "the old inline `- PROVEN:` form must now be unlabelled")

# --- position: paragraph -------------------------------------------------
check(defects("> [!REPORTED]\n> `%s` текст\n" % CITE) == [],
      "a correct paragraph marker line must produce no defect")
check(defects("[!INFERENCE]\n`%s` текст\n" % CITE) == [],
      "a marker line without the blockquote marker must also pass")

# --- position: table row -------------------------------------------------
table = ("| метка | наблюдение |\n"
         "| --- | --- |\n"
         "| [!PROVEN] | `%s` «\"Status\":\"0xc000006d\"» (PROVEN) |\n" % CITE)
check(defects(table) == [],
      "a table row labelled in its label column must pass, even with (PROVEN) "
      "text in another cell: %r" % defects(table))
bad_table = ("| метка | наблюдение |\n"
             "| --- | --- |\n"
             "|  | `%s` «\"x\":\"y\"» (PROVEN) |\n" % CITE)
check(defects(bad_table) == ["assertion_unlabelled"],
      "a table row with (PROVEN) mid-cell and an empty label column must be "
      "unlabelled: %r" % defects(bad_table))

# --- validation: the word, separately from recognition -------------------
check(defects("- [!PROBABLE] `%s` текст\n" % CITE) == ["label_unknown"],
      "[!PROBABLE] must raise exactly label_unknown, not assertion_unlabelled: "
      "%r" % defects("- [!PROBABLE] `%s` текст\n" % CITE))

# --- misplaced marker ----------------------------------------------------
mid = "- текст [!PROVEN] в середине `%s`\n" % CITE
check(sorted(defects(mid)) == ["assertion_unlabelled", "label_position"],
      "an inline mid-sentence marker must raise label_position and remain "
      "unlabelled: %r" % defects(mid))

# --- two markers ---------------------------------------------------------
two = "- [!PROVEN] [!REPORTED] `%s` текст\n" % CITE
check(defects(two) == ["label_conflict"],
      "two markers in one unit must raise exactly label_conflict: %r" % defects(two))

# --- no marker at all ----------------------------------------------------
check(defects("- `%s` текст без метки\n" % CITE) == ["assertion_unlabelled"],
      "a cited unit with no marker must be unlabelled")

# --- exempt roles untouched ---------------------------------------------
for role in ("inventory", "missing_data", "coverage", "window", "verdict"):
    check(defects("- `%s` текст\n" % CITE, role=role) == [],
          "exempt role %s must produce no label defect" % role)

# --- the real report from the paid run -----------------------------------
FIX = os.path.join(HERE, "fixtures", "v43")


def report_defects(name):
    path = os.path.join(FIX, name)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    sections = rc.split_sections(text, CONTRACT)
    return [d["defect"] for d in rc.check_labels(sections, CONTRACT)]


old = report_defects("report-20260830-v42.md")
check(old.count("label_unknown") == 0,
      "the OLD report must raise no label_unknown under v43 — the 32 blockers "
      "were false: %d" % old.count("label_unknown"))
# The old report has 47 `- LABEL:` bullets total (43 PROVEN + 1 REPORTED + 3
# INFERENCE), but 4 of those are aggregate lines with a `jq` query instead of
# a path:line citation, so check_labels correctly does not require a label on
# them — verified empirically, not a migration bug. 43 is the real floor.
check(old.count("assertion_unlabelled") >= 43,
      "the OLD report uses `- PROVEN:`-style bullets, so under v43 they must "
      "be unlabelled until migrated: %d" % old.count("assertion_unlabelled"))

new = report_defects("report-20260830-v43.md")
check(new == [],
      "the MIGRATED report must be label-clean: %r" % sorted(set(new)))

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
