#!/usr/bin/env python3
"""A bare «код» cannot block as an unknown status.

The single citecheck blocker on 20260830T190815Z-v42 was
`{"block": "Н-3", "field": "status", "kind": "unknown_value", "line": 63,
"value": 3221549076}` (0xC004F014). Report line 63 reads «...09.05 21:43:32
SPP 1003 перечислил SKU с кодом 0xC004F014 (нелиц...» -- a Windows
software-licensing SKU code, not a logon status. 0xC004F014 occurs 4150
times in the corpus Application log and 0 times as a "Status" field.

The mechanism: the `status` field-alias map lists the bare Russian word for
"code" (`код`/`кода`/`кодом`/`коде`) alongside the unambiguous `статус`/
`ntstatus`/`код статуса`/`код ошибки`. `_enum_decode_re`/`_enum_prose_re`
anchor on ANY of those and read the following value as a status, so «SKU с
кодом 0xC004F014» was misread as field=status.

Fix: `unknown_value` may BLOCK only when the field name was matched via an
UNAMBIGUOUS alias. Under an ambiguous bare-«код» alias, a value absent from
the table is not evidence of a bad decode -- it is evidence the word meant
something other than "status". `missing_decode`/`wrong_decode` are UNCHANGED
under both alias classes, which is what keeps the real v41 sentence
«отклонены с кодом 0xc000006d» checked (its value IS in the table).

Not pytest: a plain python3 script with a FAILED list, OK/FAILED <n>, rc.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
V43 = os.path.join(SHERLOCK, "skills", "v43")
FAILED = []

spec = importlib.util.spec_from_file_location(
    "citecheck43", os.path.join(V43, "tools", "citecheck.py"))
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

REFERENCE_DIR = os.path.join(V43, "reference")
REPORT_FIXTURE = os.path.join(HERE, "fixtures", "v43",
                               "report-20260830-v42.md")


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


def unknown_values(report_text, block_lo=1, block_hi=None):
    """Run enum_decode_check over the WHOLE text as one block, return the
    blocking `unknown_value` items."""
    lines = report_text.splitlines()
    hi = block_hi or len(lines)
    result = cc.enum_decode_check(report_text, [("Н-1", block_lo, hi)],
                                   reference_dir=REFERENCE_DIR)
    return [it for it in result["items"] if it["kind"] == "unknown_value"], result


def kinds(result):
    return [it["kind"] for it in result["items"]]


# --- 1. the real sentence must NOT block -----------------------------------
REAL_SENTENCE = (
    "Н-3\n"
    "09.05 21:43:32 SPP 1003 перечислил SKU с кодом 0xC004F014 "
    "(нелицензировано)\n"
)
uv, result = unknown_values(REAL_SENTENCE)
check(len(uv) == 0,
      "real SKU sentence must not produce a blocking unknown_value, got %r" % uv)
check(result["blocking"] == 0,
      "real SKU sentence must not block at all, got blocking=%d (%r)"
      % (result["blocking"], result["items"]))

# --- 2. the v41 sentence must still be checked ------------------------------
V41_SENTENCE = "Н-3\nотклонены с кодом 0xc000006d (отказ входа)\n"
uv2, result2 = unknown_values(V41_SENTENCE)
check(len(uv2) == 0,
      "v41 sentence: 0xc000006d IS in the table, must not be unknown_value, got %r"
      % uv2)
check("missing_decode" not in kinds(result2) and "wrong_decode" not in kinds(result2),
      "v41 sentence with a correct decode must be entirely clean, got %r"
      % result2["items"])

V41_WRONG_DECODE = "Н-3\nотклонены с кодом 0xc000006d (неверный пароль)\n"
_, result3 = unknown_values(V41_WRONG_DECODE)
check(any(it["kind"] == "wrong_decode" for it in result3["items"]),
      "a deliberately wrong decode for a value IN the table must still raise "
      "wrong_decode (decode checking must be unaffected by the ambiguous-alias "
      "fix), got %r" % result3["items"])

# --- 3. an unambiguous alias with a junk value must STILL block ------------
JUNK_UNAMBIGUOUS = "Н-3\nстатус 0xDEADBEEF (что-то)\n"
uv4, result4 = unknown_values(JUNK_UNAMBIGUOUS)
check(len(uv4) == 1,
      "«статус» is unambiguous; a junk value absent from the table must still "
      "block exactly once, got %r" % uv4)
check(result4["blocking"] >= 1, "junk unambiguous value must block")

# --- 4. enum-tables.tsv is untouched: 72 rows, no C004F014 row -------------
table, problems = cc.enum_table(REFERENCE_DIR)
check(problems == [], "enum_table() must load with zero problems, got %r" % problems)
check(len(table) == 72, "enum table must have exactly 72 rows, got %d" % len(table))
check(not any(v == 0xC004F014 for _f, v in table),
      "0xC004F014 must NOT have been added to enum-tables.tsv as a row")
with open(os.path.join(REFERENCE_DIR, "enum-tables.tsv"),
          "r", encoding="utf-8") as fh:
    tsv_text = fh.read()
check("C004F014" not in tsv_text.upper(),
      "enum-tables.tsv text must not mention C004F014 in any form")

# --- 5. the real report is clean of this blocker (trace copy, read-only) ---
if not os.path.exists(REPORT_FIXTURE):
    FAILED.append("missing fixture: %s (Task 3 should have staged it)" % REPORT_FIXTURE)
else:
    with open(REPORT_FIXTURE, "r", encoding="utf-8") as fh:
        report_text = fh.read()
    lines = report_text.splitlines()
    result5 = cc.enum_decode_check(report_text, [("Н-3", 1, len(lines))],
                                    reference_dir=REFERENCE_DIR)
    blockers_c004 = [it for it in result5["items"]
                     if it.get("value") == 3221549076]
    check(blockers_c004 == [],
          "real report must have zero items for value 3221549076 (0xC004F014), "
          "got %r" % blockers_c004)
    # make sure line 63 is really the SKU sentence we think it is, so this
    # assertion is testing the right thing
    if len(lines) >= 63:
        check("0xC004F014" in lines[62] or "SKU" in lines[62],
              "line 63 of the fixture no longer looks like the SKU sentence "
              "this test targets: %r" % lines[62])

if FAILED:
    print("FAILED %d" % len(FAILED))
    for f in FAILED:
        print(" -", f)
    sys.exit(1)
print("OK")
