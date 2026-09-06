# Canonical worklist selection for stage checkpoints

Status: approved for implementation after scoped Codex critique and recorded dispositions.

## Goal contribution

Sherlock must hand off actual mapped EVTX corpora into fresh stages and deliver
both requested reports. V49 subscription r1 exposed a checkpoint/Stop file-set
mismatch: checkpoint.glob(worklist*.tsv) counted worklist-index.tsv as evidence,
while the active marker/Stop correctly named only the real ledger. The handoff
was born invalid, independent of the separate monitor expiration.

## Scope and non-goals

New immutable v50; reuse authoritative ledger enumeration rather than excluding
one particular index filename. Checkpoint counts and seals must refer to the same
canonical files as Stop/finalizer. Preserve safe standalone checkpoint operation
and multi-host mappings. No gate weakening, old receipt repair, corpus-specific
exception or new service. Do not mutate v49 or resume rejected attempts.

## Acceptance

- Real logmap-generated worklist plus derived index reproduces v49 mismatch.
- V50 checkpoint and Stop agree on exact ledger names/hashes and counts.
- Unresolved actual rows still block; derived index/views/backups cannot count
  as evidence or substitute for a missing ledger.
- Single/multi-host, nested work directory, missing/malformed/unsafe advertised
  paths have explicit tested behavior; no symlink/path traversal bypass.
- Existing standalone fixtures and final gates retain supported behavior.
- Actual Qwen fixture generates the representative indexed map, proves denied
  continuation, natural Stop, fresh clear and legitimate next-stage progress.
- One Claude review, immutable register, then fresh subscription qualification
  under direct root observations and approved paid corpus acceptance.

## Timeline

- 2026-09-06 — Root source confirms broad glob only in checkpoint.inspect_worklists.
  Stop/finalizer already share active-marker manifest validation. worklist.manifest
  is a row-ID manifest, not the file-selection authority. Terra investigates the
  portable selection/fallback contract; root extends real-Qwen fixture inputs.

## Selected contract

Use one strict authoritative_worklists(work) helper shared by checkpoint
inspection (including triagecheck's imported checkpoint path) and Stop/boundary
seal selection. Reuse existing stopcheck manifest/path/hosts validation; do not
invent a parallel permissive parser. Place the helper in the existing shipped
module if dependency direction permits, otherwise one small stdlib module.

Discover workspace marker from current workspace and the work directory's
ancestors, binding marker.out exactly to the canonical work directory. A present
malformed, mismatched or unsafe advertised marker is an error, not permission to
fall back. A valid matching marker names the explicit ledgers and existing
multi-host hosts.tsv cross-check remains mandatory. No marker: exact worklist.tsv
for single-host, or validated hosts.tsv explicit worklist column for multi-host.
When hosts.tsv exists it is authoritative; invalid hosts never falls back to a
single ledger. Duplicate/traversal/symlink/nonregular/missing advertised entries
fail closed. Standalone fixtures without a marker retain exact-ledger operation.
No directory glob defines evidence membership, and no index-name blacklist.

Derived view/index/backup files cannot add counts or seals. Real ledger mutation
still invalidates pending handoff; index mutation does not. Preserve consumed
fresh-session progression and Stop checks. V49 remains immutable.

Root owns --indexed-worklist real-Qwen fixture. Terra owns v50 implementation and
focused tests. One Claude review then registration before actual Qwen. Direct root
live observation on next qualification; no automated timer standing in for review.

## Verified reproduction

- Actual r1 marker snapshot declares single worklists=[worklist.tsv]. Both actual
  files retained their checkpoint-recorded hashes; this was a membership mismatch,
  not on-disk ledger mutation. Generated index has two view rows, genuine ledger
  two evidence rows. Checkpoint total4/resolved4 is wrong; triagecheck total2 is right.
- Terra reproduced from preserved logmap-produced files at
  /Users/a/hack/sherlock-v49-logmap-index-repro.YOBlKp with only marker paths rebound:
  checkpoint init/handoff again seal both files; Pre denies seals changed.
- Source: checkpoint.py43 glob; logmap.py4838–43 explicit marker emission;
  stopcheck.manifest_worklists and _current_worklist_seals use marker membership.
  triagecheck imports checkpoint, so shared inspection must remain portable.

## Critique dispositions and precise boundaries

Codex gpt-5.6-sol low returned four blockers (17,134 input, 10,624 cached,
564 output tokens). Raw prompt/output are retained under run-report artifacts.

1. Accepted: enumerate marker candidates deterministically: current workspace
   marker first, then work-directory ancestors nearest first, deduplicated by
   canonical marker path. Validate every present candidate; conflicting authority
   is an error, never first-match success. All accepted markers must bind the same
   canonical work directory and same explicit membership/mode.
2. Accepted: advertised ledger/map paths are relative to canonical work only;
   absolute, empty, dot, parent, duplicate paths are rejected. No path component
   may be a symlink; advertised files must be regular and remain inside work.
   Reuse and strengthen the existing manifest validator where required, including
   multi-host cross-checks. Marker out can be absolute only under existing workspace
   containment rules. Reject symlink marker/work authority paths.
3. Rejected: run before registration contradicts Sherlock AGENTS.md rule 1,
   requiring an immutable registered version before execution. Focused development
   tests and Claude review precede registration; actual Qwen runs follow it.
   Any discovered runtime defect requires v51, never editing an executed v50.
4. Accepted: pending receipt binds selected authority as well as ledger seals.
   Removing/replacing the marker or hosts authority after checkpoint must reject
   handoff consumption even if selected ledger filenames happen to match. Capture
   stable authority identity/membership, not irrelevant mutable marker timestamps.
   A no-marker standalone checkpoint remains supported; changing its authority
   before consumption fails closed. Test marker removal explicitly.

- 2026-09-06 — Root inspected Codex critique against actual manifest validator and
  Sherlock execution-version rule. Three safety ambiguities accepted and clarified;
  unregistered actual execution rejected. Scoped implementation authorized.
- 2026-09-06 — Prepared fresh v50 harness/target/Winevtx/independent launch templates from the reviewed v49 scripts. All seven passed `bash -n`; no script was launched and no provider was contacted. Runtime package SHA, fresh target receipts, and paid admission remain unresolved inputs before execution.

## Scope clarification during implementation

- 2026-09-06 16:08 UTC — Terra identified that portable Pre cannot discover a
  nested work path after its marker is deleted. Root inspected evaluate_stop:
  controlled runs already reject missing markers under
  SHERLOCK_STRICT_MARKER_LIFECYCLE=1. Do not introduce a second marker/locator or
  new state lifecycle. Test removal through actual controlled Stop, and test the
  receipt authority comparison with its known work directory. The acceptance
  requirement is rejection of handoff consumption after authority change;
  unrelated inactive portable workspaces retain existing behavior. This keeps
  the change focused on the observed ledger authority mismatch.

- 2026-09-06 16:03 UTC — Timestamp correction: preceding scope clarification
  was recorded at16:03UTC, not16:08UTC. Clock tool verified actual time.

- 2026-09-06 — Root reordered old boundary/Stop fixtures to create the real
  marker before checkpoint and exposed boundary package selection by environment.
  Existing v49 boundary10tests PASS (1.319s); this aligns fixture ordering with
  actual logmap behavior before testing v50 authority preservation.

- 2026-09-06 16:11 UTC — Root legacy Stop11tests PASS (1.456s); harness
  fixture compilation and git diff check pass. Remote checkout remains clean at
  07b0382, fresh v50 roots absent, disk59GiB available. No provider contact.

- 2026-09-06 16:15 UTC — Root v50 regressions: boundary10 PASS1.472s,
  stage-pause11 PASS1.615s, finalization15 PASS2.099s. Logs retained under
  artifacts/2026-09-06-v50-selection-verification. These are offline checks;
  package remains unregistered and no actual Qwen/provider run has started.

- 2026-09-06 — Root selection8tests PASS1.223s on stable v50. New cases
  include real generated index, actual-ledger mutation, force-created triage
  checkpoint authority, marker replacement/removal, timestamp-only preservation,
  standalone multi-host membership and malformed marker rejection. One subscription
  Claude Sonnet low review launched from /tmp with tools disabled and exact diff.

## Claude review disposition

- 2026-09-06T16:24:01.169879+00:00 — One subscription Claude Sonnet5 low review returned a selection-boundary blocker. Actual usage: input2, cache-create36,843, cache-read3,289, output13,538 including12,386thinking; API150.343s. Ancillary Haiku4.5 input24,136/output20 reported separately. List-price metadata is not a subscription invoice. Raw review retained.

Accepted: search must stop at the owning workspace. When supplied workspace
contains work, validate candidates only from work up to that workspace inclusive,
never above. When checkpoint is called from unrelated cwd, ignore that cwd as
authority and infer the nearest valid matching workspace marker from work
ancestors, then stop above it. Conflicting/malformed candidates inside the owning
workspace still fail closed. Add an unrelated parent-marker regression and
verify outside-workspace CLI discovers the same authority. This refines the
earlier root-wide ancestor contract, which was too broad for independent runs.

Rejected: claimed unhandled ancestor stat/open OSError. Source _lstat already
catches OSError and load_marker catches open errors. No speculative exception
wrapper is required. Core index fix, authority/history binding, relative seal
keys and triage refresh were reviewed as sound. One focused correction/test pass
remains before registration; no additional review panel.

- 2026-09-06T16:29:00.328614+00:00 — Root verified outside-caller malformed-marker bypass (new test RED, init unexpectedly0). Removed unbounded-mode exception swallowing; valid unrelated parent markers above discovered workspace remain unvisited. Final selection10 PASS1.476s, boundary/Stop/finalization36 PASS5.152s, compile and diff check pass. Registered immutable v50 before actual execution.
