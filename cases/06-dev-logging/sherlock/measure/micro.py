#!/usr/bin/env python3
"""micro.py — hand-written capability corpora. Tier 0: the capability FLOOR.

    python3 micro.py --out cases

Each corpus isolates ONE capability from the answer key's `requires` vocabulary and
plants exactly one defect that needs it. They are deliberately tiny: if the skill
cannot stitch a stack trace across 12 hand-written lines, it will not do it across
240,000. Green here proves nothing about the corpus; RED here localises the gap for
almost no money.

`case.json` is shape-identical to slice.py's output, so run-case.sh, score_case.py
and measure.verdict need no special case. Each proof carries an `expect` string, and
tests assert the declared line range really contains it — a micro-corpus whose proof
points at the wrong line would report a coverage failure forever.
"""
import argparse
import gzip
import json
import os
import shutil

MICRO = {
    "cap-multiline-stitching": {
        "capability": "multiline stitching",
        "title": "NPE hidden in a stack trace interleaved with another thread",
        "root_cause": ("PromoCode.normalized() returns null for a lowercase code and "
                       "PromoCodeResolver calls .toUpperCase() on it"),
        "files": {
            "checkout-api.log": [
                "2026-07-28 13:44:01.100 [exec-7] INFO  c.a.c.Checkout - start order=ORD-1",
                "2026-07-28 13:44:01.101 [exec-12] WARN  c.a.c.Inventory - slow lookup 1200ms",
                "2026-07-28 13:44:01.102 [exec-7] ERROR c.a.c.Promo - Unhandled exception while applying promotion",
                "java.lang.NullPointerException: Cannot invoke \"java.lang.String.toUpperCase()\" because the return value of \"com.acme.checkout.promo.PromoCode.normalized()\" is null",
                "2026-07-28 13:44:01.102 [exec-12] ERROR c.a.c.Inventory - java.net.SocketTimeoutException: Read timed out",
                "\tat com.acme.checkout.promo.PromoCodeResolver.resolve(PromoCodeResolver.java:88)",
                "\tat com.acme.checkout.inventory.InventoryClient.get(InventoryClient.java:41)",
                "\tat com.acme.checkout.CheckoutService.apply(CheckoutService.java:203)",
                "2026-07-28 13:44:01.190 [exec-7] INFO  c.a.c.Checkout - order ORD-1 failed 500",
            ],
        },
        "proofs": [
            {"file": "checkout-api.log", "line_start": 3, "line_end": 6,
             "expect": "PromoCodeResolver.resolve(PromoCodeResolver.java:88)",
             "note": "the NPE trace is interleaved line-by-line with exec-12's timeout trace"},
        ],
    },
    "cap-json-unescaping": {
        "capability": "JSON unescaping",
        "title": "Go panic buried inside a JSON-escaped docker log field",
        "root_cause": "Retrier.flush indexes the PSP response slice by the request index",
        "files": {
            "payments-json.log": [
                '{"log":"{\\"level\\":\\"info\\",\\"msg\\":\\"batch start\\",\\"n\\":4}\\n","stream":"stdout","time":"2026-07-28T14:05:10Z"}',
                '{"log":"panic: runtime error: index out of range [3] with length 3\\n","stream":"stderr","time":"2026-07-28T14:05:12Z"}',
                '{"log":"\\tgithub.com/acme/payments-worker/internal/batch.(*Retrier).flush /src/internal/batch/retrier.go:118 +0x2a4\\n","stream":"stderr","time":"2026-07-28T14:05:12Z"}',
                '{"log":"{\\"level\\":\\"info\\",\\"msg\\":\\"payments-worker starting\\",\\"version\\":\\"1.19.4\\"}\\n","stream":"stdout","time":"2026-07-28T14:05:20Z"}',
            ],
        },
        "proofs": [
            {"file": "payments-json.log", "line_start": 2, "line_end": 3,
             "expect": "retrier.go:118",
             "note": "the panic is inside the escaped \"log\" field, not a bare line"},
        ],
    },
    "cap-cross-format-correlation": {
        "capability": "cross-format correlation",
        "title": "503s explained only by joining two formats on time, with no shared id",
        "root_cause": "inventory-svc was OOMKilled, so nginx upstreams had no ready endpoints",
        "files": {
            "nginx-error.log": [
                "2026/07/28 13:25:40 [error] 8#8: *991 upstream timed out (110: Connection timed out) while reading response header from upstream, upstream: \"http://10.42.12.20:8080/api/v1/inventory\"",
                "2026/07/28 13:25:41 [error] 8#8: *992 no live upstreams while connecting to upstream, client: 10.42.15.2",
            ],
            "k8s-events.txt": [
                "LAST SEEN   TYPE      REASON        OBJECT                              MESSAGE",
                "13:25:39    Warning   OOMKilling    pod/inventory-svc-7d9c4b8f6-2xq7z   Memory cgroup out of memory: Killed process 24417 (python3)",
                "13:25:44    Warning   BackOff       pod/inventory-svc-7d9c4b8f6-2xq7z   Back-off restarting failed container",
            ],
        },
        "proofs": [
            {"file": "k8s-events.txt", "line_start": 2, "line_end": 2,
             "expect": "OOMKilling", "note": "the cause, 1s BEFORE the nginx symptom"},
            {"file": "nginx-error.log", "line_start": 2, "line_end": 2,
             "expect": "no live upstreams", "note": "the symptom; no id links the two files"},
        ],
    },
    "cap-rare-event-needle": {
        "capability": "rare-event needle",
        "title": "One successful login hidden in a wall of failures",
        "root_cause": "a brute-force run succeeded once; the host is compromised",
        "files": {
            "auth.log": (
                ["Jul 28 03:%02d:01 node-a sshd[%d]: Failed password for invalid user admin from 186.149.227.92 port %d ssh2"
                 % (i % 60, 2000 + i, 40000 + i) for i in range(60)]
                + ["Jul 28 04:00:07 node-a sshd[2401]: Accepted password for backup from 186.149.227.92 port 44112 ssh2"]
                + ["Jul 28 04:%02d:01 node-a sshd[%d]: Failed password for invalid user oracle from 186.149.227.92 port %d ssh2"
                   % (i % 60, 2500 + i, 45000 + i) for i in range(60)]
            ),
        },
        "proofs": [
            {"file": "auth.log", "line_start": 61, "line_end": 61,
             "expect": "Accepted password for backup",
             "note": "the single success among 120 failures — summarising counts misses it"},
        ],
    },
    "cap-statistical-rate-reasoning": {
        "capability": "statistical/rate reasoning",
        "title": "TLS handshake failure rate ramps; no single line is damning",
        "root_cause": "the relay stopped accepting the legacy TLS version as pooled sockets recycled",
        "files": {
            "notify.log": (
                ["2026-07-28 09:%02d:00 INFO  smtp send ok relay=smtp-relay:587" % (i % 60)
                 for i in range(40)]
                + ["2026-07-28 10:00:00 WARN  smtp relay handshake failed: ssl3_get_record:wrong version number"]
                + ["2026-07-28 12:%02d:00 INFO  smtp send ok relay=smtp-relay:587" % (i % 60)
                   for i in range(20)]
                + ["2026-07-28 12:%02d:30 WARN  smtp relay handshake failed: ssl3_get_record:wrong version number" % (i % 60)
                   for i in range(18)]
            ),
        },
        "proofs": [
            {"file": "notify.log", "line_start": 41, "line_end": 41,
             "expect": "wrong version number",
             "note": "1 failure in 41 lines early..."},
            {"file": "notify.log", "line_start": 62, "line_end": 79,
             "expect": "wrong version number",
             "note": "...against 18 in the last 38. The RATE is the finding, not any one line."},
        ],
    },
    "cap-unknown-format": {
        "capability": "single-format read of an unknown format",
        "title": "Negative order total in a bespoke pipe-delimited log with invented severities",
        "root_cause": "stacked percentage discounts with no 100% ceiling and no final_minor >= 0 check",
        "files": {
            "promo-engine.plog": [
                "HEARTBEAT|RULE_EVAL|order=ORD-88101|rule=SUMMER26|discount_pct=5.0|ok",
                "ALARM|RULE_APPLY|order=ORD-88240|rule=SUMMER26_STACK|msg=stacked SUMMER26+LOYALTY10+WELCOME15 multiplied, no ceiling applied",
                "FATALITY|LEDGER_POST|order=ORD-88240|rule=SUMMER26_STACK|base_minor=1299900|discount_pct=137.5|final_minor=-486212|msg=ledger refused negative charge",
                "HEARTBEAT|CACHE_LOAD|rules=42|ok",
            ],
        },
        "proofs": [
            {"file": "promo-engine.plog", "line_start": 2, "line_end": 3,
             "expect": "final_minor=-486212",
             "note": "severity words are ALARM/FATALITY — no dictionary has heard of them"},
        ],
    },
    "cap-ru-severity": {
        "capability": "single-format read (Russian)",
        "title": "Stale FX rate reported only in Russian severity words",
        "root_cause": "egress to the rate source is blocked, so the adapter silently falls back to a 7-day-old cached rate",
        "files": {
            "billing-adapter-ru.log": [
                "2026-07-28 09:00:01 ИНФО  Загрузка курсов валют: источник=cbr.ru",
                "2026-07-28 09:00:06 ПРЕДУПРЕЖДЕНИЕ  Таймаут запроса курса (5000 мс), используется кэш от 2026-07-21",
                "2026-07-28 09:00:06 ИНФО  Конвертация EUR->RUB по курсу из кэша, возраст 7 дней",
                "2026-07-28 12:00:06 ПРЕДУПРЕЖДЕНИЕ  Таймаут запроса курса (5000 мс), используется кэш от 2026-07-21",
            ],
        },
        "proofs": [
            {"file": "billing-adapter-ru.log", "line_start": 2, "line_end": 3,
             "expect": "используется кэш",
             "note": "grep for ERROR/FATAL/WARN returns NOTHING in this file"},
        ],
    },
    "cap-gz-decompression": {
        "capability": "gz decompression",
        "title": "The only evidence sits inside a .gz",
        "root_cause": "egress policy blocks the rate source; proof is in the rotated, compressed log",
        "files": {},
        "gz_files": {
            "adapter.log.1.gz": [
                "2026-07-21 09:00:01 ИНФО  Загрузка курсов валют: источник=cbr.ru",
                "2026-07-21 09:00:06 ПРЕДУПРЕЖДЕНИЕ  Сетевая политика отклонила соединение с cbr.ru",
            ],
        },
        "proofs": [
            {"file": "adapter.log.1.gz", "line_start": 2, "line_end": 2,
             "expect": "cbr.ru",
             "note": "line numbers refer to the DECOMPRESSED stream, as in the answer key"},
        ],
    },
    "cap-single-format-read": {
        "capability": "single-format read",
        "title": "A plain slow-query log naming the un-indexed lookup",
        "root_cause": "a JSONB expression lookup with no supporting index seq-scans the table",
        "files": {
            "postgresql.log": [
                "2026-07-28 11:05:01 UTC LOG:  duration: 12.004 ms  statement: SELECT 1",
                "2026-07-28 11:05:41 UTC LOG:  duration: 4211.882 ms  statement: SELECT c.* FROM catalog_items c WHERE c.attrs ->> 'vendor_ref' = $1 ORDER BY c.updated_at DESC",
                "2026-07-28 11:06:02 UTC LOG:  duration: 5100.678 ms  statement: SELECT c.* FROM catalog_items c WHERE c.attrs ->> 'vendor_ref' = $1 ORDER BY c.updated_at DESC",
            ],
        },
        "proofs": [
            {"file": "postgresql.log", "line_start": 2, "line_end": 3,
             "expect": "attrs ->> 'vendor_ref'",
             "note": "the same statement shape repeating slowly IS the finding"},
        ],
    },
}


def build_micro(out_dir, cap_id):
    spec = MICRO[cap_id]
    case_dir = os.path.join(out_dir, cap_id)
    if os.path.isdir(case_dir):
        shutil.rmtree(case_dir)
    os.makedirs(case_dir, exist_ok=True)

    written = []
    for name, lines in spec.get("files", {}).items():
        with open(os.path.join(case_dir, name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        written.append(name)
    for name, lines in spec.get("gz_files", {}).items():
        with gzip.open(os.path.join(case_dir, name), "wt", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        written.append(name)

    case = {
        "case_id": cap_id,
        "kind": "capability_micro",
        "capability": spec["capability"],
        "defect_id": cap_id,
        "title": spec["title"],
        "root_cause": spec["root_cause"],
        "requires": spec["capability"],
        "files": sorted(written),
        "proof_locations": spec["proofs"],
    }
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False, indent=2)
    return case


def build_all_micro(out_dir):
    # NOT `[build_micro(out_dir, cid) and cid for cid in sorted(MICRO)]` — that
    # relies on build_micro's return value (a dict) being truthy, which is
    # clever-but-fragile: an empty-dict-shaped case would silently drop out of
    # the list. A plain loop says what it means.
    ids = []
    for cid in sorted(MICRO):
        build_micro(out_dir, cid)
        ids.append(cid)
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    for cid in build_all_micro(a.out):
        c = json.load(open(os.path.join(a.out, cid, "case.json"), encoding="utf-8"))
        print("%-34s %s" % (cid, c["capability"]))


if __name__ == "__main__":
    main()
