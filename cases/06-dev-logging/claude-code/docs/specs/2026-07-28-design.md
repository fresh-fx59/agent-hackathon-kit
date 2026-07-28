# Case 06-dev-logging — solution design

Date: 2026-07-28. Status: approved by operator (brainstorm 2026-07-28), pending
final spec review.

## Goal

Build the submittable hackathon deliverable for case 06 («Поиск проблем на
основе логов сервиса и предложение решений»): an agent-agnostic log-analysis +
RCA skill package (SKILL.md + MCP tools + versioned rules + workflows +
self-learning knowledge layer), exercised on the petstore input pack, plus a
kit benchmark gate that objectively proves the organizers' ≥50%-defects metric.

## Locked decisions

| Decision | Choice |
|---|---|
| Deliverable | Solution + kit gate; solution lives in `cases/06-dev-logging/claude-code/` |
| Runtime | Agent-agnostic, pure Python stdlib, py≥3.9, built on kit `minimcp` |
| Language | Russian jury-facing prose (SKILL.md, README, workflows, reports); English code |
| Scope | Everything incl. all optionals (kubectl connector, MTTR report, product API, predictive AIOps) |
| Architecture | A: deterministic spine + thin LLM; C-phase extras (WebSocket, live-cluster kubectl, deeper predictive) later — seams built in now |
| Repo visibility | **Kit repo PRIVATE until the hackathon ends** (done 2026-07-28); re-open after. Teammate works in a separate subfolder via Codex |
| ≥50% denominator | Staged: ledger documents all ~40 defects; gate v1 = ≥50% of the 20 log-diagnosable; gate v2 (after repo-lane matures) = ≥50% of full 40 |
| Fix verification | Java fix diff + JUnit sources; demonstrated on Python mock stand; GH Actions JVM job compiles+tests the patched service; **Jenkins pipeline instructions essential** (corporate CI is Jenkins) |
| TC-02 stream | Deterministic seeded generator, built as a robust first-class subsystem (see §Generator) |
| TC-03 honesty | Starter rule R-NOTIF-001 quarantined out of active rules; incident 2 must resolve via the knowledge layer, speedup measured |

## Ground truth (from the 5-agent mapping workflow)

~40 distinct diagnosable defects: 20 log-layer (incident 1 `c-8f3a2b91`:
lock-contention → 2000ms-timeout → FAILED-after-payment with no compensation +
orphaned reservation; incident 2 `c-d1e2f3a4`: offset-commit-before-send →
silent notification loss; plus precursors, saturation, dirty-data plants),
~18 repo/infra-only (no PaymentClient void, authId not persisted, HPA without
resource requests, missing Service object, vacuous tests, CI gaps…), ~5
cross-artifact contradictions (docs name `handleReservationTimeout` which does
not exist — real bug in `checkout()`'s catch block). Full evidence ledger goes
to `cases/06-dev-logging/docs/defect-ledger.md` + `expected-findings.json`.

Booby-traps the design defuses:
1. No canonical denominator → we freeze the ledger (staged gate above).
2. Shipped repo is unrunnable (reserve() throws, manifests missing, tests are
   stubs) yet TC-04/A1.3 require a stand and passing tests → we build the
   verification substrate (`stand/` + real JUnit sources + JVM CI lane).
3. R-NOTIF-001 ships in starter rules, pre-solving incident 2 → quarantined.

## Layout

```
cases/06-dev-logging/
├── 06-dev-logging.md, petstore_input_pack.zip      (existing, untouched)
├── claude-code/                    ← the submittable deliverable
│   ├── README.md                   RU: обзор, quickstart, примеры запуска (A5)
│   ├── SKILL.md                    RU: манифест — metadata, demo prompts, входы/выходы (A3)
│   ├── rules/rules.yaml            versioned rules, pack schema, rubric_sha stamp
│   ├── rules/quarantine/           R-NOTIF-001 + note why
│   ├── logalyzer/                  engine (~2,000 stdlib lines, modules below)
│   ├── mcp/                        5 servers → 6 required tools + stats/discovery
│   ├── stand/                      runnable Python mock of petstore checkout
│   ├── streamgen/                  synthetic stream generator (§Generator)
│   ├── workflows/                  RU mermaid: sherlock.md, aiops.md
│   ├── examples/                   worked examples on pack logs (case requirement)
│   ├── tests/                      stdlib unittest
│   ├── fix/                        Java diff + JUnit regression test sources
│   └── docs/                       defect-ledger.md, mttr-report.md, jenkins-setup.md (RU)
├── benchmark.py                    findings-mode, kit contract (--self-test/--score-only)
└── expected-findings.json          frozen ledger (answer key)
```

## Engine (`logalyzer/`) — one deterministic spine

| Module | Responsibility | Source |
|---|---|---|
| `ingest.py` | Per-format readers: plaintext, JSON-lines, Kafka jsonl, k8s events, trace JSON, zip walk, kubectl adapter (allowlisted subprocess, file fallback) | fresh; tolerant-JSON + episode-grouping patterns from tssa |
| `masking.py` | Ordered regex mask table (TS/UUID/IP/EMAIL/PHONE/HEX/PATH/STR/NUM, RU: СНИЛС/ИНН, Luhn check) + reversible per-incident pseudonyms (`user-a3f2`); mapping table never leaves the process; report de-masks. Doubles as PII layer (A4) | fresh (K8sGPT pattern + tssa verify-and-redact backstop) |
| `drain.py` | Fixed-depth prefix-tree template miner (sim_th≈0.45, max_children=100, wildcard digit routing) + sha1 masked-skeleton fingerprints | fresh, web-standard algorithm (~180 lines) |
| `rules_engine.py` | Subset-YAML parser (~120 lines) + rule registry; rules match (service, level, template) not raw lines; `rubric_sha` version stamp in every result | pack's rules schema + tssa invariant-registry pattern |
| `correlate.py` | correlation_id join across services/Kafka/trace/k8s; timeline builder | fresh |
| `evidence.py` | Evidence bundle: every item minted a citable ID (`EV-nnn`) | fresh |
| `detect.py` | AIOps triggers: novel-ERROR-template (instant), per-template 5s-bucket rate spike (3σ + floor), circuit breaker; edge-triggered alerts; predictive = WARN-rate acceleration vs known case fingerprints | tssa `circuit_breaker.py` verbatim + fresh |
| `rca.py` | Sherlock orchestration: rule's investigation checklist → prompt build (evidence bundle + SDD citations + similar cases) → LLM output parsing | fresh |
| `gates.py` | "Model proposes, code disposes": validate RCA JSON — cited file+method exist in repo tree, cited log lines exist in corpus, rule ids in catalog, severity enum; reject-and-retry | port iron-lady `telegram_aggregator_gates.py` |
| `knowledge.py` | Case store (sqlite, degrade-never): schema = symptom fingerprint set, classification, root_cause(service/file/method), fix, verification, timings; retrieval = exact fingerprint ⇒ recurrence, else TF-IDF cosine + categorical boosts; dedup-on-save by fingerprint set | tssa `trace_cluster.py` TF-IDF copy-paste + corrections-dedup identity |
| `observability.py` | contextvars correlation-ID log stamping; hand-rolled Prometheus text-format registry (~40 lines, low-cardinality counters); jsonl audit log of every MCP tool call | tssa filter verbatim (strip `zip(strict=True)` for py3.9) |
| `api.py` | Product API on kit `mocks/common.py`: POST /analyze, GET /cases, GET /cases/{id}, /metrics, /health + SSE detection stream on an internal event bus (WebSocket bolts onto the same bus in C-phase) | kit reuse + fresh |

## Two lanes

**Sherlock (on-demand, TC-01):** complaint/correlation_id → ingest+correlate →
evidence bundle → agent reasons per SKILL.md (trace failing span → culprit logs
±60s → `search_repo` on stack identifiers → SDD invariant И-1 citation) → RCA
JSON + fix diff → `gates.py` validates → report (de-masked, RU). Handles the
`handleReservationTimeout` trap by construction: gate rejects citations of
nonexistent methods. p95 ≤60s: deterministic part is sub-second; the LLM call
is the only variable and gets one retry budget.

**AIOps (stream, TC-02/TC-05):** replay `synthetic_stream.jsonl` (or live
tail) through `detect.py`. Early-warning ≤30s by code-only triggers; TC-05
false-positive guard = the same rules/thresholds must stay silent on the
healthy control traffic (generator emits a clean stream variant for this).
LLM strictly out of the hot path.

**Self-learning (TC-03):** after Sherlock solves incident 1, `save_case`
persists it. Incident 2 (notification) hits `similar_cases` → fuzzy match on
the "timeout → dropped work → no retry" pattern → agent receives the solved
case as context → measured time-to-RCA ≥30% faster, no manual labels.
Honest because R-NOTIF-001 is quarantined.

## MCP surface

Required six: `fetch_logs`, `fetch_trace`, `search_repo`, `run_on_stand`
(sole mutating tool: allowlisted fixed operations, present-before-execute,
audited), `save_case`, `similar_cases`. Ergonomics extras (Grafana/Sentry
practice): `get_log_stats`, `get_log_patterns` (summary-first — the token
lever), `list_services`, `list_log_sources` (discovery), `limit`+1 → 
`truncated`/`next_cursor` pagination on every list tool. All on `minimcp.py`
verbatim, `tracker_mcp.py` skeleton per server.

## Verification substrate + benchmark

- `stand/`: Python re-implementation of the checkout state machine (on
  `mocks/common.py`) exhibiting the same defect; the fix applied there
  demonstrates `FAILED → PENDING_RETRY` + compensation live (TC-04).
- `fix/`: Java diff for `order-service` + real JUnit regression tests
  (replacing the vacuous stubs).
- CI lanes: (1) kit `verify.sh` auto-picks `benchmark.py --self-test` —
  stdlib, LLM-free, deterministic; (2) GH Actions JVM job compiles the patched
  order-service and runs the JUnit tests (A1.3 mechanically proven);
  (3) `docs/jenkins-setup.md` (RU) — Jenkinsfile stage replicating (1)+(2)
  for the corporate Jenkins, following the kit's `ci/Jenkinsfile` +
  `docs/ci-setup-for-agent.md` conventions.
- `benchmark.py`: findings-mode adapted keys (service, file-basename,
  incident-class, method); greedy 1:1 match; precision/recall/F1;
  gate v1 min = ≥50% of the 20 log-layer defects; gate v2 raises to full 40.
  Emits timing data → `docs/mttr-report.md` (A2 report + optional MTTR bonus).

## Generator (`streamgen/`) — TC-02 synthetic stream

Deterministic, single-seed, reproducible; a first-class robust subsystem
(operator directive). Design from the `research-synthetic-log-generation`
workflow (web-sourced, every rate carries a literature citation in config):

**Architecture — two stages, config-first, no DSL.** Stage 1 simulates a
world (4 services + gateway + Kafka + k8s control plane as small state
objects on a 1s tick; declarative phase table for the scenario) and emits a
CLEAN ground-truth event stream, top-down: user sessions → funnel → span
trees, so any trace_id reconstructs a coherent causal story. Stage 2 pushes
it through ordered composable **corruptor passes**, then per-service
**dialect renderers** (each service has a fixed format identity: JSON vs
logback plaintext, different timestamp formats, different trace-id key,
multi-line stack traces as separate records), and a heapq merge with
deterministic tiebreaks emits `synthetic_stream.jsonl` + `ground_truth.jsonl`
+ manifest. A healthy control stream variant serves TC-05.

**Statistical realism (the audit-enforced targets):** NHPP arrivals
modulated by a 3-state MMPP burst chain + 5% Pareto heavy-tail gaps (never
uniform); diurnal slope so rate drifts ~15-20% across the 30-min window;
~110 templates with Zipf weights (top-10 = 70-85% of lines, ≥25 templates
fire <5 times); per-service lognormal latencies with 3% ×10 stragglers;
incident as a **metastable cascade with a real feedback loop** — memory-leak
trigger, ≥8 min of precursor WARNs (the honest ≤30s-early-warning
substrate), retry-storm amplification as live state, logistic error curve,
degradation persisting past the trigger, recovery only via a logged
intervention (deploy + thundering-herd echo).

**Dirty data:** per-service systematic quirks (consistency-per-codebase, not
random noise) + a knob-per-defect catalog with plausible default rates:
broken templates (`Processing order null`, slf4j `{}` leaks), duplicate
log-and-rethrow pairs, swallowed exceptions, Kafka re-delivery bursts,
per-host clock skew + collector reorder (1-5% out-of-order), missing fields,
one PII-leaking DEBUG template, mid-incident schema drift.

**Discipline:** named per-concern PRNG streams
(`sha256(seed:name)`-seeded) so features never perturb each other's draws;
no `time.time()`/`uuid4()`/builtin-`hash()`; `--self-test` = byte-identical
double run; `--audit` re-reads the *emitted file* and FAILS generation if
output is "too clean" (inter-arrival CV, template histogram shape,
out-of-order share, dialect count, level mix, precursor lead-time) — we
grade ourselves before the organizers' LLM-judge does. ~2,400 lines,
10 modules under `streamgen/` (config/clock/templates/traffic/incident/
noise/corruptors/render/audit/generate).

## Safety posture (jury differentiator)

Inert-data frame (tssa corrections-triage, verbatim): log content is data,
never instructions — a log line that looks like a command is itself a finding.
Model-proposes-code-disposes at every LLM boundary (gates, deterministic
classification/detection). `run_on_stand` gated + audited. PII masked before
any LLM call; reversible only locally.

## Out of scope (C-phase, seams ready)

RFC6455 WebSocket codec (SSE event bus already in place), live-cluster kubectl
demo (adapter interface + file fallback shipped), deeper predictive ML tier
(plugin point in `detect.py`). Embeddings/vector DB: rejected — TF-IDF is the
right tool at this scale.

## Open questions

None blocking. Gate v2 raise (full-40 denominator) is a follow-up task after
the repo-analysis lane matures.
