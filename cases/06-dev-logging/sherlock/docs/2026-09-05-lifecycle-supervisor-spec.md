# Scoped task: controlled-run evidence lifecycle and supervision

## Goal and boundary

Before another paid target run, preserve required generated evidence and stop
when exact observation becomes stale. Existing600second request watchdog remains;
no aggregate cost/call/wall ceiling. This follows finalization repair and does not
change any gate/oracle or produce target findings. Implement controlled runner
support, not a general orchestration platform.

## Interfaces

Use one run-scoped observer directory under the existing external TRACE, outside
the Qwen work tree. Fresh exclusive directory, mode0700, fixed nonce matching
sealed launch identity. Never reuse another run's journal/snapshots. Record this
path and helper/settings hashes in sealed launch data. Observability receipts are
retained with the run; no cleanup of original failed evidence.

Observer input: CLI observation records generated when the root monitor reads
current capture/tool/gate state. Store schema1, run nonce, sequence, boot identity,
monotonic timestamp, wall timestamp, last completed request/tool references and
known pending operation. An automatic timer cannot impersonate a completed root
observation. Read every<=60seconds during paid work; initial observation required
before first provider contact. The root then focuses on monitoring, not unrelated
implementation.

Proxy pre-dispatch check: freshness<=60s, sequence monotonic, correct nonce and
boot identity. Missing/malformed/stale observer state blocks contact and appends
a permanent fault. Guard owned processes while a request is in flight: existing
verified boot_id/start_ticks/process-group ownership must prevent killing a reused
PID or unrelated sibling process. Stop the owned controller/proxy/Qwen tree on
lost supervision, preserve captures, and record termination outcomes. Restoring
a heartbeat never erases the old fault; continuation needs a distinct segment.

## Evidence lifecycle

Wire the pinned Qwen PreToolUse, PostToolUse and PostToolUseFailure events to a
small command hook whose exact available input/output is appended by sequence
and tool ID to the external trace. Before each tool, snapshot currently existing
required generated evidence under content-addressed SHA256 names. After each
tool, reconcile expected files and preserve new/changed valid versions. Record
new missing files as integrity failure and stop controlled execution. The
pre-tool snapshot must allow exact recovery even if the next tool removes files.

Required file set comes from the actual active/worklist manifests plus canonical
worklist.tsv, worklist.manifest.json and rules.tsv when first generated; freeze
registered identity/lifecycle separately from report drafts. Missing-before-ever-
created is a draft completeness issue; disappearance after registration is an
integrity failure. Do not confuse content revision with deletion. Snapshot all
manifest-referenced ledgers; no first/last-N sampling or fixed file-count cutoff.
Reject links/traversal and changing-while-read data. Atomic writes, exclusive
run ownership, and full hashes make partial/corrupt snapshot attempts visible.

Instruction guards can discourage destructive tools, but shell regexes do not
establish universal prevention. The tested contract is pre-tool byte retention,
post-tool loss detection, dispatch blocking and owned termination. Hook failures
and missing expected hook pairs invalidate observability; nonzero command status
must use Qwen's actual blocking contract, not silently continue. Existing Stop
hook remains active. Verify actual pinned CLI invocation against local mock
provider traffic and fake corpus; no paid contact required.

## Tests and acceptance

- Dummy provider counter remains0 on missing, stale, wrong-nonce or regressed
  observer state; healthy pending work continues beyond arbitrary aggregate time.
- In-flight mock request stops on expired supervision; unrelated process survives.
- Pre-tool create/register → remove → post-tool sequence preserves exact bytes,
  records permanent fault, blocks next dispatch; renamed/replaced paths cannot
  satisfy expected identity by keeping the same file count.
- Actual pinned Qwen smoke test emits matching pre/post tool IDs and external
  artifacts; intentional hook error produces visible blocking outcome.
- Existing proxy accounting, request watchdog, controller ownership and replay
  regressions remain green. One scoped review, standard run report including
  failures, then paid qualification under standing operator authorization.

## Research applicability

Official Qwen hook contracts retrieved2026-09-05:
https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/ .
Use installed implementation as execution truth; latest documentation can differ
from the pinned runtime. Original r15 lacked proof of continuous<=60s observation;
never retroactively label that run continuously supervised.

## Final critique rulings

Codex gpt-5.6-sol low,2026-09-05: accepted precision requirements below.

- Root observation CLI is the single intended writer, uses an exclusive lock and
  capability scoped to the launch; do not expose capability in Qwen prompt/env.
  Same-UID arbitrary-code compromise is outside this operational-failure contract;
  do not claim cryptographic proof of human attention or a hostile-process boundary.
- Independent guardian checks freshness at most1second apart while proxy/target
  is alive. At expiry, block dispatch immediately and invoke existing verified
  owned-process TERM/KILL path. Record actual latency; tests require bounded
  completion using existing cleanup grace period. Guardian death invalidates
  its liveness and blocks proxy dispatch; parent/controller must observe death.
- Publish observation through same-directory temporary file, fsync and atomic
  rename, directory fsync. Both observer and proxy use the same host/boot
  monotonic clock; reject another boot, future timestamps or regressed sequence.
- Faulted segment is terminal. Restart requires a fresh launch nonce, fresh
  process tree and sequence0 linked to predecessor; standing operator approval
  permits justified retries after diagnosis, no repeated permission question.
- A file created and deleted inside one tool invocation before any registration
  cannot be recovered by boundary snapshots. Record this limitation; tool input
  and result remain captured. Authoritative registration is a successful
  pre/post snapshot receipt, not an inferred filename. Missing required final
  evidence still fails finalization. Do not claim prevention of all deletion.
- Hook journal key is session_id + tool_use_id + event phase, with sequence and
  request reference where available. Concurrent tools need serialization around
  snapshot/registry mutations; duplicate events idempotent only with identical
  hash, unmatched pre/post pairs remain explicit incomplete evidence.
- Snapshot uses one verified read, stat before/after and content digest, atomic
  exclusive object publication. A changing file fails that observation without
  silent retry; preserve diagnostic and stop. Hook timeout/failure blocks rather
  than degrades silently; retain exact timeout and validate installed semantics.
- Sealed launch records standing authorization for nullable aggregate limits.
  Root retains the existing verified owned-stop command and invokes it on a
  demonstrated loop, integrity fault or unsound direction. Do not add aggregate
  limits against the operator's explicit instruction.
