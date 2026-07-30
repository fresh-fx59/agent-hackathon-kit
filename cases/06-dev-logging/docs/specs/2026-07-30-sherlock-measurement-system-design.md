# Sherlock measurement system — design

**Date:** 2026-07-30
**Status:** approved (operator, 2026-07-30) — not yet implemented
**Sibling:** [2026-07-28-sherlock-simplest-approach-design.md](2026-07-28-sherlock-simplest-approach-design.md)

## Why this exists

The skill demonstrably works at the thing it was built for. Same model, same
649 MB corpus, same question:

| arm | result |
|---|---|
| no skill | **157 characters — no report at all** |
| v5 | full 12,940-char report, 21/30 files cited, 69 line references |

But recall is low and we cannot currently say *why*. Measured, gpt-5.5 judge,
11 real planted defects (2 red herrings excluded from the denominator):

| arm | found | FP |
|---|---|---|
| none | 0/11 | 0 |
| v1 | 0/11 | 0 |
| v2 | 2/11 | 2 |
| v3 | 1/11 | 4 |
| v5 | 3/11 | 3 |

Three problems block improvement:

1. **We throw the evidence away.** `run-bench.sh` uses `--output-format json`
   (final answer only) and `trap 'rm -rf "$W"' EXIT`. Every measurement so far
   discarded the step-by-step record of what the model actually did.
2. **Every iteration costs a full 649 MB run** — ~12 minutes and ~1.4 M input
   tokens on a *metered* provider, to move one aggregate number.
3. **The aggregate is noisier than the effect.** n=1 per arm, and two judges
   scored the *same* v5 report **3/11 and 5/11**. Any change smaller than
   ~2 defects is unprovable today.

The prior observability attempt (`logalyzer/runlog.py`, commit `035583f`)
instrumented the Python tool's own steps, not the model conversation, and died
with the stripped `logalyzer` package. Its artifacts are instructive: of 13
surviving runs, `docs/runs.jsonl` holds **13 summary lines** while all 13
`events.jsonl` are **empty (0 lines)**. The summary survived; the per-step record
never materialised — the same failure `run-bench.sh` repeats today. Reviving
`runlog.py` is not the answer (it never saw the model conversation at all);
capturing `stream-json` is.

## Goals

- Iterate on **one defect at a time**, cheaply, without re-running the corpus.
- Capture **every model turn and tool call** of every run, permanently.
- Answer, per miss, **"never opened the evidence"** vs **"opened it and failed to
  connect it"** — deterministically, with no LLM call.
- Make the analysis a **fixed procedure**, not a fresh improvisation each session.

## Non-goals

- Not a general-purpose LLM observability platform. It serves case 06.
- Not replacing the full-corpus benchmark — that stays the acceptance gate.
- Not auto-editing the skill. The analysis layer is **propose-only**.

## Architecture

Three layers, each usable alone.

### Layer 1 — Cases

Two kinds behind one interface. Every case carries `case.json`: what it targets,
which defect it should surface, the expected root cause.

**Defect slices** — `slice.py` reads `proof_locations` from the answer key
(exact `file`, `line_start`, `line_end`, `note` per proof; D01 has 3, D02 has 14,
D03 has 19) and emits `cases/D01/` … `cases/D11/`. It keeps **whole files** that
contain proofs and drops the rest: 28 files → 2–4. Whole files, not line
windows, so realistic within-file noise survives.

**Capability micro-corpora** — `cases/cap-*/`, hand-written, one per capability
drawn from the answer key's own `requires` vocabulary:
`cap-multiline-stitching`, `cap-cross-format-correlation`,
`cap-json-unescaping`, `cap-ru-severity`, `cap-no-error-lines`.

> **A slice is an easier task than the corpus** — smaller haystack, less noise.
> Slice-green does **not** imply corpus-green.

### The three-tier gate (interaction effects)

Iterating on a single slice has a documented failure mode: *"a partial run can
see a narrow task slice, reach a confident pairwise conclusion within that
slice, and miss groups that would change the completed-benchmark decision"*, and
*"partial runs miss interaction effects where a change to one component breaks
another"* ([Braintrust](https://www.braintrust.dev/articles/ai-agent-evaluation-framework),
[arXiv 2607.12338](https://arxiv.org/html/2607.12338v1)). Fixing D11's coverage
by widening a search instruction can silently blow D03's context budget.

So a change is promoted through three tiers, cheapest first:

| tier | what runs | cost | proves |
|---|---|---|---|
| 1 — iterate | the one slice being fixed | seconds | the fix does anything at all |
| 2 — regress | **all 11 slices** | minutes | the fix broke nothing else |
| 3 — accept | the full 649 MB corpus | ~12 min, metered | it survives real noise and scale |

Tier 2 is the answer to interaction effects and is cheap enough to be
non-negotiable: **no change is accepted on a tier-1 pass alone.** Tier 3 remains
the only result quotable as a benchmark number.

### Layer 2 — Capture

One runner, `run-case.sh <case> <arm>`, for slices, micro-corpora and the full
corpus alike. Three changes from today's `run-bench.sh`:
`--output-format stream-json`, tee to a run directory, and **never delete it**.

```
runs/2026-07-30T14-02-D01-v6/
  stream.jsonl   every model turn, tool call, tool result
  report.md      the final answer
  meta.json      arm, model, case, timings, tokens, exit code
```

Verified 2026-07-30 that `stream-json` carries what the analysis needs:
`tool_use` records include `file_path` / `offset` / `limit`, and `tool_result`
records report `"Read lines 2-3 of 4 from <path>"`. Combined with the key's exact
proof line ranges, **whether the run ever looked at the evidence is computable**.

**On OpenTelemetry.** The OTel GenAI semantic conventions now cover agent
orchestration and tool calls — fixed span names `invoke_agent` / `chat` /
`execute_tool`, standard attributes for model, tokens and tool arguments; client
spans left experimental in early 2026, agent spans still experimental but stable
in practice ([OpenTelemetry](https://opentelemetry.io/blog/2026/genai-observability/),
[Greptime](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)).
We do **not** adopt the SDK: it is a pip dependency, and AGENTS.md R1 requires
stdlib-only. But the captured `stream.jsonl` maps cleanly onto that vocabulary,
so `measure.py` **names its derived fields after the OTel GenAI attributes**
where they correspond. Cost today: nothing. Benefit: we do not invent private
terminology, and exporting to any OTel-native backend later is a mapping, not a
redesign.

The existing hard guard is preserved: a provider/runtime error is **never**
recorded as a measurement. (Two such rows polluted the ledger on 2026-07-28;
16 attempts were correctly refused on 2026-07-30.)

### Layer 3 — Analysis

**`measure.py` — deterministic, free, offline, reproducible.**

| check | how |
|---|---|
| proof reach | did any read/grep touch a `proof_locations` line range? |
| file coverage | files opened vs files holding this defect's proofs |
| citation integrity | `tools/citecheck.py` (exists, 18 tests): wrong-content / out-of-range / missing-file |
| report conformance | all 8 mandated sections present, and the `ЗНАНИЯ:` line? |
| collapse | answer length + phrases SKILL.md forbids («отчёт выше», «как я уже показал») |
| budget profile | tool-call count; the "one too-wide call" SKILL.md names as the run-killer |
| red herring | did the report name a D12/D13 signature as a cause? |

**`score-case.py` — the one semantic question.** gpt-5.5 via the cliproxyapi
broker answers found/not-found against that case's `root_cause`.

Judge choice is deliberate: gpt-5.5 is neutral to both the model under test
(deepseek) and the **skill's author (Claude)**, and it reproduces the historical
`scores.jsonl` column exactly (v2 → 2/11, 2 FP), so numbers stay comparable
across sessions. It is on the subscription path, so it costs no money.
Claude remains available as a *second opinion* on why a score landed — a
different job from keeping score. See `docs/conventions.md` §Model & effort
economy in the vault.

**The payoff — the deterministic layer explains the judge's verdict:**

| judge | deterministic | diagnosis | what to build |
|---|---|---|---|
| not found | never read proof lines | **coverage** failure | ship `logstat`, file-selection guidance |
| not found | did read proof lines | **reasoning** failure | synthesis guidance, or a correlating tool |
| found | citecheck: wrong-content | **fabricated evidence** | the most dangerous outcome; currently invisible |

This is the standard three-level split — *final response tells you **what** went
wrong, trajectory tells you **where**, single-step tells you **why*** — with the
industry-standard division of labour: **deterministic checks for tool selection,
argument construction and format compliance; LLM-as-judge only for response
quality and goal alignment**
([Braintrust](https://www.braintrust.dev/articles/ai-agent-evaluation-framework),
[Arize](https://arize.com/blog/what-is-an-evaluation-harness/)).

### Classifying the fix: harness layer, not just "the prompt"

Misses are attributed with the **ETCLOVG** taxonomy from *HarnessFix*
([arXiv 2606.06324](https://arxiv.org/html/2606.06324v2)), which separates
*harness* flaws — the editable runtime around the model: prompts, tool specs,
orchestration — from model reasoning limits. Its empirical finding matters here:
across 30 repos, **harness flaws span all seven layers rather than being confined
to prompts**. Our own evidence already agrees:

| ETCLOVG layer | observed in this case |
|---|---|
| **T**ool interface | `logstat.py` / `logjoin.py` were built for the coverage and multi-hop gaps and **never shipped into the skill bundle** — SKILL.md mentions them zero times |
| **C**ontext / memory | v6's SKILL.md grew 54 KB → 62 KB; the skill's own doctrine names context budget as the top cause of total failure |
| **L**ifecycle / orchestration | the `none`/v1 collapses (157 and 106 chars) are subagent fan-out losing the report, not a reasoning failure |
| **V**erification | `citecheck.py` exists but nothing enforces it, so fabricated citations are currently unmeasured |

The point of the taxonomy is to stop the reflex of answering every miss by
editing SKILL.md. Three of the four rows above are fixed by shipping or wiring
code, not by writing more prose.

This turns "3/11" into "3/11, of which N misses never opened the file and M read
it and failed to connect it" — which names the next fix instead of guessing it.
It also means most iterations need **no judge call at all**: a run that never
reached the proof lines has already failed, and why.

**The skill** — `.claude/skills/sherlock-measure/` in the vault (ADR 0013).
Fixed procedure, so two sessions a week apart are comparable:

1. Read `scores.jsonl` + `measure.py` output for the runs in scope.
2. Bucket every miss: coverage / reasoning / fabricated-evidence / collapse.
3. Sub-classify coverage misses by the key's `requires` taxonomy.
4. Rank fixes by *defects unblocked × cheapness*; name **one** next change.
5. Cite a `stream.jsonl` record or a computed metric for every claim.
6. Emit a fixed-shape report.

Deterministic checks live in `measure.py`, **not** in the skill's prose: prose
cannot be unit-tested. The skill is **propose-only** — it never edits SKILL.md,
matching the corrections-producer and the knowledge-card loop, which both
require an explicit human yes.

## Data flow

```
answer-key ──► slice.py ──────────────► cases/
arm + case ──► run-case.sh ───────────► runs/<id>/{stream.jsonl,report.md,meta.json}
runs/ + answer-key ──► measure.py ────► metrics.jsonl      (deterministic)
runs/ + answer-key ──► score-case.py ─► scores.jsonl       (judge)
metrics + scores ──► sherlock-measure ► diagnosis + ONE proposed fix
```

## Constraints

- **The answer key must never enter the public kit repo** — it would spoil the
  case for the hackathon. Code lives in the repo; corpus and key stay outside it,
  passed by env var, exactly as `run-bench.sh` already does with
  `SHERLOCK_CORPUS`.
- **`stream.jsonl` is ground truth.** Every claim the analysis makes must cite a
  record in it — the same evidence discipline Sherlock imposes on itself, turned
  on our own measurements.
- **Model under test stays `[SP]deepseek-v4-flash` via linkapi** — that is the
  corporate harness this case must compare against, and the only reason to spend
  on the metered provider. Everything else runs on subscription.
- Stdlib only, no pip, no network in tests (AGENTS.md R1).

## Testing

A measurement system that isn't tested is just another opinion. Repo convention:
stdlib `unittest`, hermetic temp dirs, zero network, wired into
`tools/tests/run.sh`.

- **`slice.py`** — fixture key + fixture corpus produce the expected file set. A
  proof line falling outside its own slice is a **hard error**, not a warning.
- **`measure.py`** — fixture `stream.jsonl` files with known properties. The
  load-bearing case: **read the right file but the wrong line range ⇒
  `proof_reach = false`**, because the entire diagnosis rests on that
  distinction. Also: collapse detection, missing section 7, wide-call detection.
- **`run-case.sh`** — a stub `qwen` first on PATH (the technique already proven
  in `test_fetch_logs.py`), asserting the runner really invoked it, wrote
  `stream.jsonl`, and **did not delete the run dir**.
- **`score-case.py`** — stubbed judge; assert ledger row shape and that a
  provider error is refused rather than recorded.
- **Every check gets a negative control**, proven to go RED on a deliberately
  broken fixture. An assertion nobody has seen fail is not evidence. (Applied
  today: the password-containment test was sabotaged, confirmed red with the
  leaking file named, then restored.)

Note `tools/tests/run.sh` executes `python3 <file>`, so any new test class must
sit **above** the `if __name__ == "__main__"` block or it is silently skipped —
this cost 14 tests going unnoticed on 2026-07-30.

## Increment 1 — definition of done

1. `slice.py` produces 11 defect slices, tests green.
2. `run-case.sh` captures one full `stream.jsonl` for one case on deepseek.
3. `measure.py` emits a coverage/reasoning verdict for that case, tests green.
4. **First real deliverable:** a table saying, for each of v6's 8 misses, whether
   it *never opened the evidence* or *opened it and failed to connect it*.

Item 4 decides whether the next fix is `logstat` (coverage) or SKILL.md
synthesis guidance (reasoning).

## Known risks

- **Slices are easier than the corpus, and partial runs hide interaction
  effects.** Mitigated by the three-tier gate: no change is accepted on a
  single-slice pass; tier 2 (all 11 slices) is mandatory, and only a tier-3
  full-corpus run may be quoted as a benchmark number.
- **n=1 is below the decision threshold.** Benchmark-replay work finds a partial
  score can sit close to the final score yet still miss the task group that flips
  the pairwise conclusion ([arXiv 2607.12338](https://arxiv.org/html/2607.12338v1)).
  Per-defect pass/fail is far less noisy than an 11-way aggregate, which is the
  main reason slices are worth building — but a *corpus* number still needs
  replication before any "vN beats vN-1" claim.
- **Judge strictness may be wrong.** gpt-5.5 refused D02 and D05 credit where a
  second reader granted it, because v5 named the defect but got the *mechanism*
  wrong. One data point. Watch it; do not switch judges on it — switching
  silently invalidates every historical number.
- **The provider is unreliable.** `[SP]deepseek-v4-flash` returned
  `400 Upstream request failed` on 16 consecutive bench attempts over ~2.5 h on
  2026-07-30. Slices reduce exposure (smaller, faster runs) but do not remove it;
  the runner must keep failing loudly rather than recording a bad row.

## Open questions

- Should capability micro-corpora (`cases/cap-*`) be authored in increment 1, or
  deferred until the defect slices show which capabilities actually fail? The
  spec assumes **deferred** — the `requires` taxonomy is free from the key, so
  the real failure distribution should drive which micro-corpora are worth
  writing.

## External sources consulted (2026-07-30)

- [HarnessFix: Diagnosing and Repairing Agent Harness Flaws](https://arxiv.org/html/2606.06324v2) —
  ETCLOVG taxonomy; harness flaws span all seven layers, not just prompts;
  85.0% step-level fault localisation; 6.3–18.4% gains from harness repair alone.
- [Braintrust — AI agent evaluation framework](https://www.braintrust.dev/articles/ai-agent-evaluation-framework) —
  deterministic checks for tool selection/arguments/format, LLM-judge for quality;
  partial runs miss interaction effects.
- [Arize — What is an evaluation harness?](https://arize.com/blog/what-is-an-evaluation-harness/) —
  span / trace / trajectory / session levels; failed trajectories become the next
  regression cases.
- [LangChain — trajectories vs outputs](https://www.langchain.com/resources/llm-evaluation-framework) —
  response = what, trajectory = where, single step = why.
- [OpenTelemetry — GenAI observability](https://opentelemetry.io/blog/2026/genai-observability/)
  and [Greptime on the GenAI semconv](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions) —
  `invoke_agent` / `chat` / `execute_tool` span names and attribute vocabulary.
- [How Many Tasks Are Enough for Agent Benchmark Decisions?](https://arxiv.org/html/2607.12338v1) —
  a partial score can be close to the final score and still flip the conclusion.
