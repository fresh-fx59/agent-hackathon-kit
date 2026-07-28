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
