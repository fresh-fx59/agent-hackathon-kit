# Universal ingest hotfix — report

## Status

DONE. All requirements from `docs/specs/2026-07-28-design.md` §"Universal
ingest" implemented, TDD'd, green, committed as one commit.

## Commit

`45ea27be9d772908efe4184cecc2ef103daf6bbd` on branch `case06-universal-ingest`
(worktree `/home/claude-developer/hack/agent-hackathon-kit/.worktrees/case06-universal-ingest`).

Message: `case06 phase1 hotfix: universal ingest — any file/format, gz,
visible skips, generic parser + multi-line folding`

## Test summary

- New: `tests/test_ingest_universal.py` — 19/19 passing (single file of any
  name; directory walk incl. extensionless/`.out`/rotated names; `.gz`
  transparent read; binary skip with visible reason; nested-zip skip with
  visible reason; oversized-file skip; stats dict shape; generic parser
  (ISO w/o brackets, ISO with offset tz, epoch-millis, epoch-seconds,
  syslog forced-partial + windowless-visible via `rules_engine._match`, no-ts
  returns None); multi-line folding (stack trace fold, leading-lines-no-prev,
  20-line cap + truncation marker); the exact Flink log4j fixture from the
  spec (ok quality, correct level/logger, 3-line fabricated stack trace
  folded); CLI E2E single-file `--logs` through `cmd_investigate` (exit 0);
  `cmd_stats` JSON has `files`+`skipped` plus legacy
  `records_total`/`unparsed`/`by_service` keys.
- Existing: all 48 pre-existing tests still green, including
  `tests/test_e2e_pack.py` (same rules fire, same root cause — byte-identical
  behavior on the organizers' pack proven).
- Extended `tests/test_skill_doc.py` by one required substring
  (`"отдельный файл"`) to TDD-pin the new SKILL.md line.

Total: 67/67 tests green (48 pre-existing + 19 new).

## verify.sh summary

`bash scripts/verify.sh` from the worktree repo root: **41 passed, 0 failed,
0 todo — RESULT: PASS** (unit tests, mock services, MCP smoke, case
benchmarks --self-test all green).

## What changed

- `logalyzer/ingest.py`: `read_all(root, masker)` now accepts a directory,
  `.zip`, or a single file of any name. Directory walk now includes every
  regular file regardless of extension. New `read_all_with_stats(root,
  masker) -> (records, stats)` where `stats = {"files": {name: {"format",
  "ok", "partial", "unparsed"}}, "skipped": [{"file", "reason"}]}`;
  `read_all` delegates to it. Explicit, visible (never silent) exclusions:
  binary files (null byte in first 8KB), nested `.zip` found inside a
  directory (only the top-level `--logs` path may be a zip), files >200MB.
  `.gz` is read transparently via `gzip.open` (text mode, `errors="replace"`).
  New generic text parser (`_generic_parse`) as a fallback timestamp bank —
  ISO 8601 (optional tz), epoch-millis (13 digits), epoch-seconds (10
  digits), syslog month-name (no year → normalized with an 1900 placeholder
  and forced `parse_quality="partial"`, documented in a comment referencing
  `rules_engine._eval_sequence`'s elapsed_ms math). Multi-line folding
  applies uniformly to the existing logback path and the new generic path: a
  line with no recognized leading timestamp folds into the previous record's
  body (masked) instead of becoming a standalone record; capped at 20 folded
  lines with a `"… [truncated]"` marker; only becomes a standalone
  partial/unparsed record when there's no previous record.
- `logalyzer/records.py`: extended `_ALIASES` with `SEVERE`/`CRITICAL` →
  `ERROR`.
- `logalyzer/cli_impl.py`: `cmd_stats` now calls `read_all_with_stats` and
  prints `files`+`skipped` alongside the existing
  `records_total`/`unparsed`/`by_service` keys.
- `SKILL.md`: one added bullet under «Входные данные» noting `--logs`
  accepts a single file of any name/format (`"отдельный файл"`).
- `examples/investigate-incident-1.md`: refreshed the `stats` output block
  (records_total 77→70, `second_incident_notification` 8→1) to stay
  accurate — that file's leading `#`-comments make it sniff as plaintext,
  and folding now collapses its unparseable lines into one record. Does not
  affect the incident-1 (`c-8f3a2b91`) evidence bundle or report, which use
  a different `correlation_id` and are unaffected (verified: `investigate`
  output — rules, root cause, timeline, code_recommendations — identical to
  before).

## Byte-identical-behavior verification

Ran `tests/test_e2e_pack.py` (rules fire + root cause assertions) and
manually re-ran the full documented `investigate` command against the
organizers' pack: same 3 rules (`R-ORD-001`, `R-INV-001`, `R-ORD-002`), same
root cause (`OrderCheckoutService.checkout`, line 80), same 5
`code_recommendations`, same timeline. The only pack file affected by
folding (`second_incident_notification.log`) carries a different
`correlation_id` and never enters the `c-8f3a2b91` evidence bundle, so it
cannot change the report.

## Concerns / known limitations (not blocking, in scope for a future pass if wanted)

1. `examples/investigate-incident-1.md`'s trace-span timeline lines
   (`"span POST /api/v1/checkout ERROR"` etc.) were already stale relative
   to current output *before* this change (a pre-existing drift from an
   earlier, unrelated span-naming fix — `test_ingest_structured.py`'s
   "Finding 1" comment). Left untouched since it's out of scope for this
   ingest hotfix; only the `stats`-block numbers this change actually
   affects were refreshed.
2. `.gz` size cap is enforced on the on-disk (compressed) file size, not the
   decompressed size, per the spec's literal "files > 200MB" wording — a
   highly-compressible 200MB `.gz` could decompress to several GB in memory.
   Not addressed since it wasn't asked for and would need a streaming
   line-by-line gzip reader to fix properly.
3. Directory walk now includes literally every regular file; if `--logs` is
   pointed at a directory containing non-log clutter (e.g. a `.git/`
   subfolder), plaintext files there (`.git/config`, `.git/HEAD`) would be
   ingested as bogus records rather than skipped. Not in the spec's scope
   (real motivating case is Flink log directories), flagging in case the
   operator wants a VCS/dotfile exclusion later.
   **RESOLVED by fast-follow #1 below.**

## Fast-follow commit (post-review, same session)

Coordinator review verdict: MERGE-READY with two Important fast-follows,
folded in as one additional commit.

**Commit:** `<see below>` — "case06 universal ingest: skip hidden dirs in
walk; cap decompressed gz size"

1. **Hidden path components excluded from the directory walk**
   (`read_all_with_stats`, the `rglob("*")` loop): any file whose path
   relative to `root` has a component starting with `.` (e.g. `.git/HEAD`,
   `.git/config`, `.DS_Store`) is now skipped before ever reaching
   `_ingest_one_file` — not ingested, and (per review instruction) not
   listed in `stats["skipped"]` either, since a dotfile/dotdir isn't "a log
   of an unknown format," it's not a log at all (same convention as
   `.gitignore`/`find -not -path '*/.*'`). This directly resolves known
   limitation #3 above (pointing `--logs` at a project root no longer
   ingests `.git/HEAD` as a bogus record).
   Test: `test_hidden_path_components_skipped_from_walk` — a dir with
   `.git/HEAD` + `.git/config` + a real `.log`; asserts only the `.log` is
   ingested and neither hidden file appears in `stats["files"]` or
   `stats["skipped"]`.

2. **Bounded decompressed-size cap for `.gz` files** (`_read_gz_lines`):
   previously read the whole decompressed stream unboundedly via
   `f.read()`, checking only the on-disk (compressed) size beforehand — a
   highly-compressible small `.gz` could still decompress to gigabytes
   (decompression-bomb risk), inconsistent with the rigor already applied
   to `.zip` via `_ZIP_MAX_UNCOMPRESSED`. Fixed: `_read_gz_lines` now
   streams in 64KB chunks with a running decompressed-byte counter and
   raises a new internal `_DecompressedTooLarge` once the total exceeds
   `_MAX_FILE_BYTES` (200MB, the same cap used for on-disk size). This is
   NOT surfaced as a crash/generic "ingest error" record: `_ingest_one_file`
   catches `_DecompressedTooLarge` specifically (before the generic
   `except Exception` fallback) and records it as a normal, visible
   `stats["skipped"]` entry with a decompressed-size reason — same
   mechanism as the existing on-disk size cap.
   Test: `test_gz_decompressed_size_cap_skips_with_reason` — writes a
   highly repetitive payload that gzips to under 1000 bytes on disk but
   decompresses to 20000 bytes, monkeypatches `ingest_mod._MAX_FILE_BYTES`
   to 1000 (so the on-disk pre-check doesn't fire — the payload's
   compressed size is asserted `< 1000` first), then asserts the file lands
   in `stats["skipped"]` with a reason containing "decompressed", not in
   the returned records.

Both new tests verified RED before the fix (via `git stash` of
`ingest.py` alone, tests re-run, confirmed failing with the exact expected
symptoms: hidden files leaking into `refs`, oversized-gz file leaking into
`refs` instead of `skipped`) and GREEN after.

### Fast-follow test summary

21/21 in `tests/test_ingest_universal.py` (19 from the original commit + 2
new), all 48 pre-existing tests still green — 69/69 total.

### Fast-follow verify.sh summary

`bash scripts/verify.sh` from the worktree root: **41 passed, 0 failed, 0
todo — RESULT: PASS.**
