# Normalization v2 — Task A report

## Status
DONE. TDD throughout; full repo `verify.sh` PASS; byte-identical guarantee held
(`test_e2e_pack.py` green unchanged).

## What was built

**New module `logalyzer/formats.py`:**
- `fingerprint(sample_lines) -> str` — masks each line to a shape skeleton
  (hex-runs→H, digit-runs→9, letter-runs→A, then 2+ consecutive whitespace-
  separated bare letter-tokens collapse to one — needed so free-text
  messages of differing word count don't defeat "most common skeleton"),
  takes the 5 most common skeletons, sha256 → 12 hex chars.
- `FormatStore(dir_path=None)` — JSON files at `<dir>/<fingerprint>.json`,
  schema `{fingerprint, descriptor, hit_rates, sample_skeleton, created}`.
  Default dir is `<case root>/formats.d/learned/` (created + `.gitkeep`'d,
  not gitignored); a `LOGALYZER_FORMATS_DIR` env var override exists purely
  so tests can exercise the real CLI round trip without writing committable
  artifacts into the repo during a test run (undocumented in CLI usage on
  purpose).
- `validate_descriptor(descriptor, sample_lines) -> (ok, hit_rates, reason)`
  — regex must compile and be ≤2000 chars (the catastrophic-backtracking
  guard as literally specified), must declare a `ts` named group, ts must
  hit ≥90% of non-blank sample lines (parsed via `ts_format`), and an
  optional `level` group must normalize to a known level on ≥50% of its
  own matches; other declared groups (service/logger/msg/thread) get
  informational hit rates.
- `apply_descriptor(...)` — matched lines → records ("ok" iff ts+level both
  present, else "partial"); unmatched lines fold into the previous record
  via a helper shared with `ingest.py` (same cap/truncation behavior).
- `to_utc_iso(value, ts_format)` — handles `"iso"`, `"epoch_s"`,
  `"epoch_ms"`, or an arbitrary strptime string; naive results are treated
  as already-UTC (same convention as the existing BSD-syslog handling).

**Upgraded `logalyzer/ingest.py`:**
- Timestamp bank (`_TS_ISO`/`_TS_EPOCH_MS`/`_TS_EPOCH_S`/`_TS_SYSLOG`) lost
  its `^` anchors and now `.search()`es anywhere in the line (with
  `(?<!\d)`/`(?!\d)`/`\b` boundaries so a match can't be a substring of a
  longer number/word) — continuation is now "no timestamp found anywhere."
- New `discover_domain_ids()` — UUIDs, generic `\w*_?[Ii]d[=:]value` pairs
  (key kept as found), bare hex runs ≥8 chars (must contain an a-f letter,
  so plain numeric ids aren't mislabeled). Wired into the **generic-parser
  fallback only**; the logback `_PLAIN` structured-reader branch still uses
  the original hardcoded `_INLINE_ID`, per "keep existing structured-reader
  behavior unchanged."
- New `_parse_plaintext_dialected()`: FormatStore lookup by fingerprint of
  the first ≤50 lines → `apply_descriptor` on hit (dialect label
  `learned:<fp>`); else the existing `_PLAIN`/generic-parser waterfall
  (dialect `logback` if `_PLAIN` matched any line, else `heuristic`).
- `_ingest_one_file` now reads a file's lines once and derives
  `needs_inference` (ok-rate <30% AND ≥20 lines, scoped to plaintext
  dialects only) with a fingerprint + up to 20 PII-masked sample lines,
  collected into `stats["needs_inference"]`.
- Extracted `_fold_continuation()` (shared by `_read_plaintext` and
  `formats.apply_descriptor`) — no behavior change, pure refactor.

**CLI (`cli_impl.py` + `__main__.py`):**
- `investigate`/`stats` switched to `read_all_with_stats`. `investigate`
  exits 4 with `{action, files:[{file,fingerprint,sample_lines}],
  instructions}` **only** when needs_inference files exist AND the
  correlated bundle is empty; if other files already produced evidence, it
  proceeds (exit 0) and appends a Russian limitations note naming the
  files/fingerprints. `stats` always inlines `needs_inference` and exits 0.
- New `register-format <descriptor.json> --fingerprint <fp> (--sample
  <file> | --sample-from-stats <stats.json>)`: validates, saves to
  `FormatStore` on success (exit 0 + hit_rates), else exit 1 with the
  reason; exit 2 on bad args. Usage text documents all exit codes.

## Design decisions worth flagging
- **Module-cycle avoidance**: `formats.py` has zero module-level dependency
  on `ingest.py`; `ingest.py`'s references to `formats` and `formats.py`'s
  references to `ingest` (`_CORR`, `_fold_continuation`,
  `discover_domain_ids`) are both function-scope imports.
- **Test-seam fix**: `_ingest_one_file` no longer double-reads non-gz files
  (previously: cheap 5-line sniff + a full `read_source` re-read); it reads
  once and calls `_parse_lines` for both gz and non-gz. This broke one
  existing test (`test_ingest_structured.py::test_unreadable_file_...`)
  that mocked `ingest_mod.read_source` — updated it to mock `_parse_lines`
  instead (the new shared seam); the test's actual assertions/intent are
  unchanged, confirmed still green.
- Verified via manual pack inspection that `.search()`-anywhere doesn't
  silently reshuffle `test_e2e_pack.py`'s evidence: one pack file
  (`second_incident_notification.log`, a comment-prefixed file that sniffs
  as "plaintext") does parse differently now (JSON-looking lines that were
  previously folded now match embedded ISO timestamps and become their own
  records), but its correlation_id/domain_ids never overlap the tested
  incident, so `related()` excludes it either way — then confirmed by
  running the actual e2e test, not just reasoning.

## Test summary
`bash scripts/verify.sh` (from worktree root) → **PASS** (42/42, 0 failed).
Case-06 suite: **97/97 tests pass** (69 baseline + 23 new in
`tests/test_formats.py` + 5 new in `tests/test_ingest_universal.py`, one
existing test's mock target updated for the refactor).

## Concerns / follow-ups for Task B
- None blocking. `correlate.py` and `SKILL.md` untouched as instructed.
- `formats.d/learned/` currently has only `.gitkeep` — no real-world
  descriptors learned yet; that's expected, this is the mechanism only.

---

# Review follow-up pass (2026-07-28) — 9 findings fixed

A reviewer empirically reproduced 3 CRITICAL + 4 IMPORTANT + 2 MINOR defects
in the Task A delivery above. All 9 fixed in one pass, each with a red→green
test. Summary per finding:

**CRITICAL 1 — exit-4 deadloop.** `apply_descriptor`'s "ok" quality requires
BOTH ts and level; a valid, fully-matching but level-less descriptor
(nginx-style access log) left the ok-rate stuck at 0% forever, so
`investigate` kept exiting 4 even after the correct format was registered.
Fixed in two layers: (1) `ingest.py._ingest_one_file`'s needs_inference gate
now uses the descriptor's actual **match rate** (fraction of records with a
non-empty timestamp — apply_descriptor never sets one on a folded/standalone
continuation record) instead of ok-rate, but *only* for `learned:<fp>`
dialects; (2) `cli_impl.py` gained `_unresolved_needs_inference()`, a hard
safety net that drops any needs_inference entry whose fingerprint is already
in `FormatStore` before building the exit-4 payload or the limitations note
— a registered dialect can never be re-offered for inference, full stop.
Test: `TestExitFourHandshake.test_level_less_dialect_no_deadloop_after_
registration` — register once, then two more `investigate` calls both
exit 0.

**CRITICAL 2 — content-based container detection.** `sniff_format` was
rewritten from filename-substring matching to content scoring:
`_content_classify` samples up to 20 lines and computes a JSON-syntax rate
(distinguishing "this is jsonl" from "this is kafka-enveloped jsonl" via
topic+partition/payload+type keys), a k8s-event-line hit rate (reusing
`ingest_structured._K8S`), and a Prometheus-exposition hit rate;
`_looks_like_trace_doc` does a separate whole-file single-JSON-document
check (trace isn't line-per-record) with a cheap first/last-char guard so
it doesn't cost an ordinary large plaintext file anything. Filename is now
only a tie-breaker for genuinely ambiguous content (JSON-shaped but
malformed, or a bare list/dict-without-spans) — exactly the two existing
malformed-trace-fixture tests that needed it (`test_trace_with_invalid_
json`, `test_trace_as_json_list`) still pass because of this narrow
carve-out, not despite it. Added a defense-in-depth fallback:
`_parse_lines` now re-tries any structured guess through the plaintext
waterfall (making it needs_inference-eligible again) if it comes back
>=90% unparsed on >=5 records — gated on record count specifically so it
never reinterprets a *deliberate* single "malformed trace" unparsed record.
Tests: `TestContentBasedSniffing` (5 new tests) proves the literal required
scenario (logback content in a kafka-named file parses as logback; a real
kafka jsonl file named nothing-kafka-ish still gets enriched) plus k8s/
metrics detection and the fallback guard.

**CRITICAL 3 — ReDoS.** Confirmed empirically first: `(?P<ts>(\d+)+X)`
against a 25-digit non-matching string hung for 5+ seconds under a
`timeout`-wrapped subprocess probe, and a `signal.alarm`-guarded direct
call *also* failed to interrupt it — CPython's `re` engine is a tight C
loop that never checks for pending signals mid-match, so no in-process
timeout mechanism (signal, threading) can hard-kill it; only an OS-level
process kill can, confirmed with a `multiprocessing.Process.terminate()`
probe (2.01s, reliably dead). `formats.py` now runs the actual per-line
matching (`_match_sample_worker`) in a child process via `_run_with_
budget`, hard-killed past a 2.0s default budget (overridable — tests use
smaller budgets); `validate_descriptor` rejects with a clear reason on
timeout. The same treatment covers apply time: `apply_descriptor_with_
budget` (10s default) wraps the *whole-file* application in a child
process (using its own fresh Masker — see the docstring for the pseudonym-
numbering trade-off this implies) and is now actually **wired into
`ingest.py._parse_plaintext_dialected`** (this was originally implemented
but not called from the ingest path — caught and fixed during this pass);
on breach it falls back to the heuristic waterfall and appends a
`{"file","reason"}` entry to a new `stats["warnings"]` list, never
silently. Measured validation wall time is now stored as
`validate_seconds` in the learned JSON (measured by `cmd_register_format`
around the `validate_descriptor` call, not inside it, so the timing
survives regardless of which code path — normal or timeout — returns).
Tests: `TestReDoSContainment` (4 tests: killer regex rejected within
budget, normal regex still fast, apply-time timeout falls back, apply-time
success) in `test_formats.py`; `TestLearnedFormatCache.test_apply_time_
budget_breach_falls_back_to_heuristic_with_warning` proves the ingest-path
wiring (mocks the budget function's return rather than waiting out a real
10s timeout, to keep the test fast — the timeout mechanism itself is
proven separately); `validate_seconds` persistence proven inline in the
exit-4 round-trip test.

**IMPORTANT 4 — RU fingerprint instability.** `_LETTER_RUN` was
`[A-Za-z]+`, so Cyrillic (or any non-Latin) message text passed through
skeleton masking untouched, meaning free-text word boundaries/lengths
leaked into the "shape" and two files of the same dialect with different
Russian wording never converged on a shared skeleton. Changed to
`[^\W\d_]+` with `re.UNICODE` — "a \w character that's neither digit nor
underscore," which under Python 3's default Unicode matching is exactly
"a letter in any script." Tests: `TestUnicodeSkeleton` (2 tests) — direct
Cyrillic-collapses-like-ASCII check, and a same-dialect/different-RU-
wording fingerprint-equality check mirroring the existing ASCII stability
test.

**IMPORTANT 5 — ID false positives.** The literal spec regex
`\b\w*_?[Ii]d[=:]\s?[\w-]+` matches any word ending in the letters "i","d"
— confirmed "valid: true"/"paid: confirming"/"invalid: false" all produce
bogus domain_ids. Replaced the key pattern with real id morphology: exact
`id`, snake_case `*_id`, or camelCase `*Id` (capital I required — this is
specifically what excludes "valid"/"paid", which end in lowercase "id").
Added `_looks_like_real_id_value`: drops trivial values
(true/false/null/none/yes/no case-insensitive) and anything under 4 chars;
a value with no digit at all must still be all-hex-chars to count (keeps
pure-letter hex ids like "deadbeef" while rejecting "state: ok"-shaped
false positives). Test: `TestIdDiscoveryFalsePositives` (5 tests) — the
exact reviewer prose case yields `{}`; snake_case/camelCase/exact-id keys
still captured; short/trivial values dropped even with correct morphology;
letter-only hex run still captured.

**IMPORTANT 6 — timestamp-anywhere shreds stack traces.** Once the generic
bank searches the whole line, an epoch-shaped number or a bare "Mon DD
HH:MM:SS" phrase embedded in a stack trace frame technically matches and
was becoming its own bogus record. Implemented the spec's two-pass fix:
`_generic_parse` now also returns `_ts_kind`/`_ts_start` (match position);
`_dominant_ts_pattern` pre-scans a file's non-`_PLAIN` lines, buckets each
generic-bank hit by (kind, position//10), and picks the most common
(kind, band). If the file already has an established `_PLAIN` anchor (its
real dialect is the fixed logback format), a generic pattern is only
trusted once it genuinely recurs (>=2 occurrences) — a single coincidental
hit inside continuation lines can't promote itself to a record start;
without a `_PLAIN` anchor, even one hit is trusted (preserves the many
existing single/few-line generic-format test fixtures). The main
`_read_plaintext` loop now requires `_conforms(g, dominant)` before
trusting a generic match; non-conforming lines fold exactly like a
non-match would. Test:
`TestMultilineFolding.test_stacktrace_with_coincidental_epoch_and_date_
phrase_folds_to_one_record` — a `_PLAIN`-anchored leading record plus a
4-line stack trace containing both a 13-digit "error code" and a
"May 12 10:00:00" phrase folds to exactly one record.

**IMPORTANT 7 — epoch_s gate bypass.** `(?P<ts>\d+)` + `ts_format:
"epoch_s"` matches a bare "28" and "successfully" parses to
1970-01-01T00:00:28Z — passes the 90% ts hit-rate check trivially (it
never fails to parse) while poisoning the cache with garbage. Added
`_implausible_timestamps_reason` to `validate_descriptor`: parsed sample
years must fall in [2000, 2100], and the sample's own min/max span must be
under 366 days. The killer-regex worker now also returns `iso_values` (the
successfully-parsed ISO strings) so this check runs on real parsed data,
not just the hit-rate. Tests: `TestEpochPlausibilityBounds` (3 tests) —
bare "28" rejected with a "plausible" reason; a genuine epoch_ms sample
(~2025) accepted; a sample spanning >366 days rejected.

**MINOR 8 — instructions text.** `_INFERENCE_INSTRUCTIONS` rewritten to
explicitly explain writing `sample_lines` to a file "one line per line,"
state every acceptance threshold enforced by `validate_descriptor` (ts
>=90%, level >=50% when present, year bounds 2000-2100 + span <366 days,
the 2000-char/2s ReDoS guards), and document `register-format`'s three
exit codes. Verified inline via string-content assertions in the existing
exit-4 handshake test (not a separate test class, since it's naturally
part of that same request/response flow).

**MINOR 9 — stats hit rates.** `ingest.py._ingest_one_file` now computes
and stores `ts_hit_rate`/`level_hit_rate` (fraction of a file's records
with a non-empty timestamp / a known level) on every plaintext/learned
stats entry, independent of whether they end up gating needs_inference —
diagnosing *why* a file is borderline (missing timestamps vs. missing
levels) no longer requires re-running with a debugger. Test:
`TestCliAndStats.test_cmd_stats_surfaces_ts_and_level_hit_rates_for_
plaintext_files`.

**Deferred (reviewer-flagged, explicitly out of scope for this pass):**
fingerprint cross-check at registration (verifying a submitted
descriptor's sample re-hashes to the claimed `--fingerprint` rather than
trusting the caller's value outright); basename-only `needs_inference`
file paths (currently just `p.name`, no directory context if two
same-named files collide across subdirectories); incident-2 body
cosmetics (unrelated to this module).

## Regression re-verification after the fix pass
- Full case-06 suite: **120/120 tests pass** (97 from Task A + 23 new for
  this pass, none removed).
- `bash scripts/verify.sh` from the worktree root: **PASS (42/42, 0
  failed, 0 todo)**.
- `tests/test_e2e_pack.py` re-run explicitly and stays green — byte-
  identical rules/root-cause claim re-verified after the content-sniffing
  rewrite and the timestamp-anywhere dominant-pattern change (both are the
  changes with the largest blast radius in this pass).
- One incidental bug caught during self-review, not requested by the
  reviewer: `apply_descriptor_with_budget` had been implemented in
  `formats.py` but never actually called from `ingest.py` — the ingest
  path was still invoking the unprotected `apply_descriptor` directly.
  Fixed by wiring it into `_parse_plaintext_dialected`, with a new
  `warnings` parameter threaded through `_parse_lines`/`_ingest_one_file`
  so a budget breach surfaces in `stats["warnings"]` instead of silently
  changing behavior.
