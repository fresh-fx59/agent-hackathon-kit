---
title: Case 06 — Sherlock, the simplest approach (evidence-driven redesign, 11h budget)
type: reference
status: idea
created: 2026-07-28
updated: 2026-07-28
tags: [case06, logs, rca, qwen-code, skill, mcp, adapters, hackathon]
links: ["[[2026-07-28-design]]", "[[hackathon-prep-2026-07-27]]"]
---

# Sherlock — the simplest approach

Replaces the deterministic-spine architecture of `2026-07-28-design.md` for case 06.
Written under a **hard 11-hour budget** with the rule: *a partly-working demo beats a
non-working one.* Every increment ends in something demoable.

---

## 1. The evidence

Nothing here is opinion. Five independent measurements, three of them the operator's own.

### 1.1 The model out-analyses the pipeline

| Run | Substantive findings |
|---|---|
| Unaided model, bare prompt | 10 |
| Unaided model, logs only (no docs, no code) | 16 |
| Unaided model, fan-out framing | 15 |
| **`logalyzer` (2,400 LOC engine + 2,273 LOC tests)** | **~2 solid + 2 partial of 24 ledger findings** |

### 1.2 The pipeline collapses on real logs — the operator's own test

From `~/hack/logalyzer-real-world-testset/` (21 real datasets, 19 MB: loghub, nginx 51k
lines, apache 10k, pgbadger postgres incl. `.gz`, this host's systemd journal):

- **The rules engine produced nothing on a real incident.** On an SSH brute-force storm the
  demo-domain rules matched zero, and the report degraded to a timeline dump with no cause
  chain. A model recognises an SSH brute-force storm at a glance.
- **The parser contradicts itself.** Year-less BSD syslog → the heuristic stamps sentinel
  year 1900 (`ingest.py:305-317`); the descriptor validator gates years to [2000,2100]
  (`formats.py:298-318`) and rejects the 1900 its own parser produced. Format inference can
  *never* converge. The fix list is an open enumeration — BSD syslog, Android logcat `03-17`,
  Proxifier `10.30`, …
- **What did work was all reduction**: 100 % timestamp hit-rate, window selection exact
  against raw-grep ground truth (46/46, 17/17, 261/261), sub-second runs.

> Reduction works. Understanding doesn't. That is the whole design.

### 1.3 What the deterministic code genuinely beat the model at

Verified by an audit that stripped every planted hint (all comments, `BUG`/`НЕВЕРНО`
markers, `note` fields) and re-ran: the verdict stayed **bit-identical**, 142 ms,
three runs → one md5.

Real wins: reproducibility; code location that ignores comment hints; citations carrying
file+line minted by the reader that actually read the bytes; an append-only audit log.
**Not a win**: "scale beyond the context window" — at fixture scale the whole pack is 6 % of
one window. That claim only becomes true at company scale, which is exactly our case.

### 1.4 Prior art: independent teams converged on the same three layers

Surveying HolmesGPT, K8sGPT, Grafana Sift/Loki, Elastic, Datadog Bits, Splunk, Azure SRE
Agent, Coroot, OpenSearch:

- **L1 deterministic, semantics-free** — retrieval, scoping, context budgeting, redaction,
  and *unsupervised* clustering. Nobody hand-writes semantic rules per format.
- **L2 deterministic, institutional** — *the query is the reusable artifact, not the parser.*
  Azure SRE Agent: "save your team's best queries as parameterized, deterministic tools…
  the agent runs the exact query you define." Domain knowledge accumulates as saved queries.
- **L3 the LLM** — decides what to fetch next, reads any format, generates **and eliminates**
  hypotheses, writes the narrative.

Named anti-patterns we are currently committing: hand-written regexes quoting literal English
strings; hardcoded language assumptions (`rglob("*.java")`); correlation by single-ID
equality; silent ingestion of non-log input (our `README.md` bug); one analyzer per resource
type applied to an open universe; **building your own storage and query layer**.

### 1.5 Retrieval is a solved, measured problem — and the cheap options win

LogDx-CI, cost per case:

| Strategy | Score | Tokens | Cost |
|---|---|---|---|
| raw logs | 0.353 | 275k | — |
| **tail-200 (zero parsing)** | **0.614** | 6.1k | $0.012 |
| grep narrowing | 0.639 | 88k | $0.129 |
| **hybrid grep→tail router** | **0.670** | 19.8k | $0.031 |
| map-reduce LLM summary | 0.664 | 537k | $0.184 |

Three conclusions that shape the plan:

1. **The agent tool-call loop over raw files is the strongest single lever — and Qwen Coder
   CLI gives it to us for free.** Allowing follow-up tool calls raised the ceiling more than
   any preprocessing.
2. **`tail -200` scores 0.614.** Our floor is high and nearly free. Sophistication buys
   +0.056. Ship the floor first.
3. **Map-reduce summarisation is the worst trade measured** — 27–85× the tokens for equal
   quality. Do not build it.

Two more: *statistical rarity / contrastive-skew ranking outranks template mining by 2.5×*,
and *code-synthesis retrieval* (model writes and runs a script against the corpus) nearly
tripled accuracy on the hardest benchmark — again free in a coding-agent CLI.

On Drain specifically — an honest correction to an earlier framing: Drain is **not** a
per-format parser, it is semantics-free clustering, and it is universal in production. But at
real scale (Loghub-2.0, 14 datasets averaging 3.6 M lines) its fine-grained accuracy falls
0.75 → ~0.55 and 9 of 15 classical parsers cannot finish in 12 hours. So it is a
**navigation aid** — show the model the *shape* of the corpus, then let it grep back to raw
lines — never the delivered context.

### 1.6 Why "it didn't work in Qwen Coder"

Qwen Coder CLI was never installed. The kit's docs only ever say *"qwen-coder-подобный"*.
The system was built for eleven days against an imagined runtime.

**Probe of the real `@qwen-code/qwen-code@0.21.1` (installed, 9 s):** it natively supports
skills (`SKILL.md`, same `name`+`description` frontmatter as Claude Code, discovered from
`.qwen/skills/`), subagents (`agents/*.md`), MCP servers (`.qwen/settings.json` →
`mcpServers`, plus a `qwen mcp` command), hooks, `QWEN.md`/`AGENTS.md` context files, and
`-p` headless mode with `--output-format json|stream-json`.

The artifacts port nearly verbatim. **The missing thing was never a feature — it was ever
running it.**

---

## 2. The decision

Build **Sherlock**: a skill-first investigation loop where the model is the parser, the
correlator and the RCA engine, and deterministic code does only the four jobs the model
provably cannot — none of which needs to know what a log line looks like.

**Retire**: `ingest.py`, `formats.py` (format store + learned-format cache + exit-4
inference handshake), `rules_engine.py` + `rules.json`, `correlate.py`, `coderef.py`'s Java
coupling, `detect.py` (unbuilt), `streamgen/` (unbuilt), `stand/` (unbuilt).
≈ 5,100 LOC of code and tests, plus everything not yet written.

**Deferred, not rejected — clustering.** §1.5 endorses Drain-style clustering as a navigation
aid, and it is genuinely universal (semantics-free, zero format knowledge). We do not build it
in 11 hours because the ablation puts *statistical rarity ranking above template mining by
2.5×* for a fraction of the code. `sherlock map`'s "rarest line shapes" is that cheaper
substitute: mask each line to a shape skeleton, count, surface the rare ones. If the schedule
ever gains slack, a real Drain miner slots in behind the same `map` interface without touching
anything else.

**Keep, rewritten format-agnostic** (≈ 400 LOC total):

| Component | Job | Why it cannot be the model | Evidence |
|---|---|---|---|
| **`logcover`** | Enumerate every file (following into `.gz`, grouping rotated siblings into one logical stream); track what the session actually touched; **refuse to emit a report while any file is untouched** | The cost of *not* opening a file is invisible from inside the run — the model cannot know what it didn't see | recall 100 % → 73 % → 18 % on identical corpus+model, delta almost entirely explained by which files were opened |
| **`logstat`** | Grouped distributions + changepoints, `--group-by auto`, reporting whether a shift is SHARED across groups or SPECIFIC to one — **and emitting the group-bys that showed no shift** | Arithmetic over millions of rows, and the negative result is the load-bearing part | the only absence that made a run *overturn a correct root cause* rather than merely miss one |
| **`logts`** | Normalize timestamps to UTC with a confidence flag; declarative `--tz` / `--skew`; flag out-of-window records instead of silently including them | Seven encodings coexist (ISO, BSD syslog, epoch millis/float-secs/micros, dmesg monotonic, explicit offset); failures are silent and invert causality | naive fabricated a 2 h offset and simultaneously held two contradictory time models |
| **`logjoin`** | Canonicalize id spellings into one key; **answer `absent X --expected-in Y`** | An absence across five id spellings in four formats is not answerable by grepping | D05's decisive evidence is an entity that *does not appear* where it must |
| **Run ledger** | Append-only record of every investigation | Code appends facts; a model narrating "that took 40 s" is guessing | A2/A4 metrics are unmeasurable without it |
| **Case memory** | Save and retrieve past investigations | Storage is not a reasoning task | TC-03 |

**Explicitly NOT built — measured, not assumed:**

- **A citation checker.** 79/79 cited `file:line:quote` assertions verified verbatim, across
  Cyrillic, ANSI escapes, JSON-escaped Go frames and CRI-wrapped records. Zero hallucinated
  lines, zero off-by-N. *(This reverses an earlier draft of this spec, which made the claim
  checker the centrepiece. The evidence killed it.)*
- **Per-format parsers and severity dictionaries.** 100 % recall on the bespoke pipe-delimited
  format — including correctly ordering an invented severity vocabulary (`ALARM`/`FATALITY`
  above `WARN`) with no schema — and 100 % on the Russian-language log.
- **A rules/regex engine over log text.** Every planted decoy defeats it:
  `grep -E 'ERROR|FATAL'` returns 1 substring hit in the promo log and 0 in the Russian log,
  while `nginx/error.log` offers 15,132 "errors" of which 2 matter.
- **Correlation by id equality.** Five spellings of one id did not stop either capable run.

**What *is* fabricated — and the actual defense.** Not quotes: *relationships*. One run
asserted "the surviving pods were exactly 10.42.12.31/.33"; grepping every non-gz file for any
pod-name↔IP co-occurrence returns **zero hits corpus-wide** — the edge was invented to bridge
two real citations. Hence `logentity` (rank 5): a relationship store that answers
`EXISTS` / `DOES-NOT-EXIST-IN-CORPUS` with explicit nulls, so an unsupported join is refused
rather than narrated. Build it if I3 lands early; it is the highest-value item after the top
four.

**Demoted to a checkbox, done last**: masking. It appears exactly twice in the requirements —
`acceptance_criteria.md` A4 (1 of ~22) and `requirements.md` R3.5, which literally says
*«не логировать PII»*, i.e. about what we **write**, not what the model reads. The model is
in-house, regex masking is provably incomplete (audit: 3 identifier types; the operator's own
test: `sample_lines` unmasked despite `--help` promising it). Reframed as **redacting the
report that travels** into tickets, chat and the case store — smaller surface, testable,
matches R3.5's actual wording, ~30 LOC.

---

## 3. Architecture

```
             ┌──────────────────────────────────────────────┐
   user ───► │  Qwen Coder CLI                              │
             │   skill: sherlock  (SKILL.md — the procedure)│
             │   subagents: per-source scouts               │
             └───────┬──────────────────────────────┬───────┘
                     │ agent tool-call loop         │
              ┌──────▼──────┐               ┌───────▼────────┐
              │  SOURCE     │               │  sherlock CLI  │
              │  ADAPTER    │               │  (4 verbs)     │
              │  → local    │               │  map · slice   │
              │    bytes    │               │  verify · case │
              └──────┬──────┘               └───────┬────────┘
        files ┆ kubectl ┆ journalctl ┆ [MCP: loki, es, grafana, k8s]
                     └──────────────► run ledger (append-only) ◄──┘
```

The model runs the loop: **map → narrow → read raw → hypothesise → eliminate → verify →
report**. Code never interprets a log line; it only moves bytes and checks claims.

### 3.1 The source-adapter contract (answering "is this feasible?")

**Yes — and it is nearly free, because we do not write most adapters.**

The trap is building a query abstraction over LogQL / ES-DSL / PromQL / KQL. That is weeks of
work and is the named anti-pattern *"building your own storage and query layer."* We do not.

The contract is one function:

```python
# sherlock/sources/<name>.py
def resolve(spec: str, window: tuple[str, str] | None) -> Resolved:
    """Materialise a log source as local readable bytes.
    Returns Resolved(root: Path, manifest: list[FileInfo], provenance: dict).
    Implementations MUST NOT parse log content."""
```

Everything downstream (`map`, `slice`, `verify`) sees only a local directory. Adding a source
means writing `resolve()` — it does **not** touch the core.

| Adapter | Cost | When |
|---|---|---|
| `files` — dir, file, zip, `.gz` | already have the readers | I0 |
| `kubectl` — `kubectl logs`/`get events` → cache dir | ~40 LOC subprocess | I5 if time |
| `journalctl` — `journalctl -o json` → cache dir | ~25 LOC subprocess | I5 if time |
| **Loki / Elasticsearch / Grafana / k8s** | **0 LOC — existing MCP servers** | config only |

That last row is the architectural answer. `mcp.grafana.com/mcp` is hosted and zero-install;
Elastic, Kusto, ClickHouse and Kubernetes all ship MCP servers. In a *runtime that is already
an MCP client*, "add Elasticsearch" is an entry in `.qwen/settings.json`, and the skill's
procedure already says "use whatever log-access tools are available." **The extensibility
requirement is satisfied by configuration, not code.**

Ship **one** adapter (`files`) + the contract + one MCP server wired as proof. Do not build
three adapters; build the seam and demonstrate it.

### 3.2 The four CLI verbs (~400 LOC, format-agnostic)

- `sherlock map <root>` — inventory: files, sizes, time ranges, line counts, error-density
  per file, rarest line shapes. *Shows the model the shape of the corpus.* Never delivered as
  the analysis context.
- `sherlock slice <root> --around <ts>|--grep <re> --context N --budget <tokens>` — the
  hybrid grep→tail router from §1.5. Returns raw lines with `file:line` prefixes, always
  under budget.
- `sherlock verify <report.json>` — for every cited `file:line`, re-read that line and
  substring-check the quote; for every code symbol, `rg`/`ast-grep` it in the repo. Emits
  `verified | unverified | contradicted` per claim.
- `sherlock case save|find` — the knowledge layer; JSON case files + text search.

---

## 4. Increments — 11 hours, always demoable

**Rule: every increment ends green and shippable.** If the clock runs out at any boundary,
what exists still demos. Times are elapsed-from-now.

| # | Window | Deliverable | Gate (must pass to proceed) |
|---|---|---|---|
| **I0** | 0:00–0:45 | `qwen login`; `SKILL.md` → `.qwen/skills/sherlock/`; one headless run on one real dataset; `verify.sh` one-liner | Qwen Coder produces an RCA on a real log file. **Nothing else starts until this is green.** |
| **I1** | 0:45–2:45 | **Sherlock skill, zero tools.** SKILL.md = the investigation procedure + output contract (RU). Eval: 6 datasets from the real testset, skill vs no-skill | Skill beats bare baseline on the A/B. **This is the pitch.** |
| **I2** | 2:45–5:00 | **`logcover`** — coverage ledger + report gate | Recall on the 649 MB corpus rises toward the 100 % ceiling; no report emitted with untouched files |
| **I3** | 5:00–6:30 | **`logstat`** + run ledger | Reproduces the SHARED-vs-SPECIFIC discrimination that the fan-out run got wrong; `runs.jsonl` populated |
| **I4** | 6:30–8:00 | `sherlock case save/find` — self-learning | Incident #2 resolves faster than #1, measured from the ledger (acceptance A2) |
| **I5** | 8:00–9:30 | Checkbox sweep: MCP server exposing the 6 required tool names, mermaid workflows, README, report redaction, second adapter if time | `acceptance_criteria.md` A3/A4 boxes tick |
| **I6** | 9:30–11:00 | Demo script, pitch, MTTR baseline, freeze | Demo runs twice, cold, without the operator touching a keyboard |

**I1 is the product.** Everything after it is insurance and points. Given `tail-200` scores
0.614 unaided, expect I1 to carry most of the value — which is exactly why it comes first.

### 4.1 What runs in parallel

Four lanes. Only Lane A is the critical path; the rest never block it.

| Lane | Owner | Work | Depends on |
|---|---|---|---|
| **A — critical path** | operator | I0 → I1 → I2 → I3 → I4 | sequential, each gated |
| **B — eval harness** | teammate / agent | Scoring harness over the 21 real datasets + the 649 MB planted-defect corpus; the skill-vs-no-skill A/B runner | I0's headless invocation only |
| **C — checkbox artifacts** | agent | MCP server (6 tool names), mermaid workflows (Sherlock + AIOps), README, case-schema doc, Prometheus metrics stub, redaction | nothing — pure paperwork, start at 0:45 |
| **D — demo & pitch** | agent | `pitch.md`, demo runbook, MTTR manual baseline, `adoption.md` | I1's output shape |

Lane B is the highest-leverage parallel work: **it is what makes each gate answerable in
minutes instead of by eyeball.** Start it the moment I0 is green.

Lane C is genuinely independent — the organizers' A3/A4 boxes are satisfied by files
existing, and an agent can write them against the contract while Lane A builds the substance.

### 4.2 Failure policy under the clock

- Every increment is committed and demoable before the next begins. No half-merged states.
- If a gate fails twice, **stop and ship the previous increment**. Do not repair forward.
- If the clock hits 9:30 mid-increment, abandon it, `git checkout` the last green state, and
  spend the remaining 90 minutes on I6. A clean I1+I2 demo scores far better than a broken I4.

---

## 5. Acceptance-criteria map

| Criterion | How it is earned | Increment |
|---|---|---|
| A1 RCA with file+method | Model reads repo; `verify` re-greps the symbol so the citation is checked, language-agnostic | I1 + I3 |
| A1 SDD invariant violation | SDD excerpt is context for the model, not a rules DSL | I1 |
| A1 fix compiles / passes tests | Model writes the patch; CI job runs it | I5 |
| A1 TC-02 early warning ≤30 s | `map` error-density + rarity ranking over a tailed window | I2 |
| A1 TC-03 self-learning, no manual labelling | `case find` returns incident #1 as prior art for #2 | I4 |
| A1 TC-05 no false positives on happy path | Eval includes the happy-path fixture; `verify` drops unsupported claims | I3 |
| A2 ≥50 % defects diagnosed | Measured by Lane B against the frozen ledger | I1–I3 |
| A2 p95 RCA ≤60 s | Ledger records duration per run | I3 |
| A2 feedback loop ≥30 % faster | Ledger diff between case #1 and #2 | I4 |
| A3 skill manifest w/ metadata + demo prompts | `SKILL.md` frontmatter (Qwen-native format) | I1 |
| A3 MCP exposes `fetch_logs`,`fetch_trace`,`search_repo`,`run_on_stand`,`save_case`,`similar_cases` | Thin MCP wrapper over the 4 verbs + adapters | I5 |
| A3 rules versioned as YAML/JSON in git | **Reframed**: rules become *saved parameterized queries* (prior-art L2) — versioned YAML the skill reads as a checklist, not an engine | I5 |
| A3 mermaid workflows | Two diagrams | I5 |
| A3 knowledge layer schema + API | `cases.py` + schema doc | I4 |
| A4 own metrics | Prometheus text endpoint over the ledger | I5 |
| A4 structured agent logs w/ correlation_id | Ledger is JSONL and carries it | I3 |
| A4 PII masked | Report redaction before the report travels | I5 |
| A4 MCP audit log | Ledger records every tool call | I3 |
| A5 review checklist, README, metrics report | Lane C + D | I5/I6 |

Note A3's rules row: we keep a versioned rules file **because the criterion asks for one**,
but it is prompt data the model reads as a checklist — not a matching engine. That is the
prior-art L2 pattern (institutional knowledge as saved queries) and it is the one form of
"rules" that does not rot on contact with an unseen format.

---

## 6. Scale and weak-model degradation

**Scale.** `map` is O(N) over bytes and never holds a file in memory; `slice` bounds output by
token budget. A 649 MB corpus is answered from a few thousand delivered lines. If a source is
too big even to walk, that is the adapter's problem (server-side filtering), which is why
adapters take a `window`.

**Weak model.** Every measured retrieval strategy in §1.5 degrades gracefully: `tail-200`
scores 0.614 with *no* model sophistication at all. The skill's procedure is explicit steps,
not implicit competence. The claim checker is the safety net — a weaker model hallucinates
more citations, and `verify` deletes them rather than letting them reach the report. **Our
quality floor is set by code, our ceiling by the model.**

**Untested risk, stated plainly.** All model-side measurements here were produced by Claude,
not DeepSeek v4. I1's A/B on the real Qwen runtime is the first honest measurement — and it
happens in the first three hours precisely so a bad surprise arrives while there is still time.

---

## 7. What we are betting on, and what kills us

**Betting on:** the model reads unfamiliar formats better than any parser we can write
(measured: 10–16 findings vs 2); the agent tool-call loop is the strongest retrieval lever and
Qwen Coder gives it free; cheap retrieval is nearly as good as clever retrieval.

**What kills us:**

1. **Qwen free-plan rate limits during the eval.** Mitigation: cliproxyapi broker as fallback;
   Lane B caches every run so a limit never costs a repeat.
2. **DeepSeek v4 is materially weaker at long-context log reading than the model we measured.**
   Mitigation: I1's A/B surfaces it by hour 3; fallback is heavier `slice` budgets.
3. **Skill not firing** — the description doesn't match how engineers phrase requests.
   Mitigation: trigger phrases in the frontmatter, tested headlessly in I1's eval.
4. **Time.** 11 hours is not enough for I0–I6 plus polish. §4.2's abandon-and-ship policy is
   the mitigation; the plan is deliberately front-loaded so the cut lands on the cheap end.

---

## 8. Open questions

1. **Rate limits** — does the Qwen free plan sustain a ~40-run eval? Answer empirically in I0.
2. **Which 6 datasets** from the 21 form the I1 eval set? Proposal: OpenSSH (brute force —
   the one the rules engine failed), Linux/Mac (year-less syslog — the one the parser failed),
   nginx (volume), postgres-gz (compressed + folded lines), Hadoop or OpenStack
   (multi-service), journal-json (structured). Deliberately weighted to where we *know* the
   old system broke.
3. **Frozen ledger denominator** — the ~40-defect ledger from the old design assumed the
   petstore pack. For the real testset there are no labels; Lane B must either use the 649 MB
   planted-defect corpus for recall numbers or hand-label a small real subset. Proposal: use
   the planted corpus for the ≥50 % metric, and the real datasets for format-robustness.
4. **Does the corporate Qwen build match `@qwen-code/qwen-code@0.21.1`?** If it is older, the
   skills mechanism may be absent. Contingency: the same `SKILL.md` content ships as a
   `QWEN.md`/`AGENTS.md` context file, which every version supports.
