# AGENTS.md — agent-hackathon-kit

Working agreements for any coding agent in this repo.

## Harness

Case-06 implementation may use Codex. Do not block delivery on Claude Code
subscription limits. Keep model use proportionate: provider-free implementation,
tests and review need no model call; use the exact target runtime only for an
accepted target arm. The archived `cases/6-log-analisys-codex/` artifacts remain
planning/review documents, not build targets; implement only from the
authoritative Sherlock specification.

## Case 06 — what we are actually building (operator, 2026-07-28)

Not a hackathon prop. **An internal tool the whole company uses**, invoked from
the corporate **Qwen Coder CLI** by developers and DevOps/SRE engineers. Every
design decision is judged against the four requirements below; if a feature
does not serve one of them, it does not ship.

**R1 — Zero configuration. This is the primary requirement.**
The happy path is: *copy the skill folder (plus any rules/MCP it brings) into
Qwen Coder's skills directory, and start using it.* Nothing else. No env vars,
no config file to edit, no install step, no service to run, no credentials to
supply. An engineer who has never heard of this project must get a useful
answer on their first try.
Consequences, and they are binding:
- The skill must work **standalone**, with only the tools the CLI already has.
- Any helper script ships **inside the skill folder**, pure `python3` stdlib,
  no `pip install`, and the skill must still work correctly if the script
  cannot run at all (**graceful degradation, never a hard dependency**).
- Never require an MCP server for the happy path. MCP is an *optional*
  upgrade for teams that have a log backend.
- Never ask the engineer to configure a model, a key, or a base URL — the
  corporate CLI is already configured.

**R2 — Any log format.** Any language, any framework, any team's bespoke
format, any human language, including formats nobody anticipated. This is why
the model reads the logs and code does not parse them: measured 100 % recall on
a bespoke pipe-delimited format with an invented severity vocabulary, and on a
Russian-language log, with no schema and no parser.

**R3 — Any size.** From a pasted 20-line snippet to multi-GB dumps. Measured:
100 % recall on a 649 MB / 4.26 M-line corpus while reading 0.09 % of it.
The binding constraint is **coverage** (which files got opened), not context
window — recall went 100 % → 73 % → 18 % on the *same* corpus and model purely
by sampling policy.

**R4 — Extensible with ease.** Adding a new log source must not touch the core.
One contract — `resolve(spec, window) -> local bytes + manifest`, implementations
must not parse content. New backends (Elasticsearch, Loki, Grafana, Kubernetes)
arrive as **existing MCP servers**, i.e. configuration, not code.

**R5 — Self-improvement loop with a human in the loop.** The jury watches for
this specifically, and the case statement demands it («Self-learning loop:
фиксация инцидента → сохранение решения → повторное использование знаний»,
acceptance TC-03 + the ≥30 % speed-up metric). The system must get better with
use, and a person must stay in control of what it learns.

The loop, in the order it runs:

1. **Notice.** During an investigation, when the agent resolves something whose
   *signature is recognisable* — a log shape, an error chain, a failure mode —
   it drafts a **pattern card**: how to recognise it, what it turned out to mean,
   the root cause, the recommended action, and the evidence it was learned from.
2. **Confirm — the human gate.** The card is *proposed*, never saved silently.
   The engineer confirms, edits or rejects it. Nothing enters the knowledge base
   without that. This is the requirement, not a nicety: an agent that
   auto-writes its own beliefs compounds its mistakes, and no engineer will
   trust a knowledge base they did not agree to.
3. **Reuse.** At the start of every later investigation the agent reads the
   confirmed pattern cards first and checks whether the corpus matches one. A
   known pattern must resolve in a fraction of the steps a cold investigation
   takes — that is the measurable claim.
4. **Measure.** The run ledger records steps and wall-clock per investigation,
   so «incident #2 was N % faster than incident #1» is a number we can show,
   not an assertion.

Constraints that follow from R1: the knowledge base is **plain files inside the
skill folder** (`knowledge/patterns/*.md`), so it travels with the skill, is
diffable, reviewable and versionable in git, and needs no database and no
configuration. A team shares learning by committing pattern cards. Nothing about
this may require a service to be running.

**No scheduler — the loop is synchronous.** The fleet's other propose-only
runners (drift-triage, catalog-triage, the corrections producer) are driven by
systemd timers. **That mechanism is unavailable inside the corporate Qwen Coder
CLI**, and a timer would violate R1 anyway. So the trigger is the investigation
itself: the agent proposes the card at the end of the session, in prose, as a
step of the procedure — no cron, no daemon, no background job. Verified on
`@qwen-code/qwen-code@0.21.1`: the CLI supports `Stop`, `SessionStart`,
`SessionEnd`, `PreToolUse`, `PostToolUse`, `SubagentStop`, `UserPromptSubmit`,
`PreCompact`, and states that *"skills can define hooks and commands"* — so a
`Stop` hook may ship inside the skill folder as an **optional** nudge. It must
degrade gracefully: hooks disabled ⇒ nothing breaks and the prose path still
works.

This is not a workaround, it is the better design. A scheduled loop proposes a
change that a human reviews days later, out of context. Proposing in-session
asks for confirmation **at the moment of maximum context** — the engineer has
just seen the incident and knows whether the pattern is real. Async review is
the compromise you make when no human is present; here one always is.

Authoritative spec: `cases/06-dev-logging/docs/specs/2026-07-28-sherlock-simplest-approach-design.md`
(supersedes `claude-code/docs/specs/2026-07-28-design.md`, whose deterministic
parsing spine is retired — see the spec's evidence section for why).

## Folder rules

- `cases/06-dev-logging/claude-code/` — the case-06 solution (the submittable
  deliverable). All implementation code, docs, and specs live here.
  Authoritative docs: `docs/specs/2026-07-28-design.md` (design incl.
  cross-review amendments) and `docs/specs/2026-07-28-cross-review-codex.md`
  (pinned team contracts).
- `cases/6-log-analisys-codex/` — Codex artifacts only (review/planning
  archive). Must never contain `benchmark.py` or `test_*.py`:
  `scripts/verify.sh` autoglobs those repo-wide into the CI gate.
- Everything else — the rehearsal kit (mocks, MCP lib, rehearsal cases,
  scaffolder, CI). Contract for every case benchmark: `--self-test` (exit
  0/1) and `--score-only` (one numeric line); `ci/gate.sh` prints the
  `benchmark score: N (min M)` line.

## Constraints

- Pure Python stdlib, py≥3.9, agent-agnostic (must run under the corporate
  GigaCode agent unchanged). No pip installs.
- Russian prose for jury-facing artifacts (SKILL.md, README, workflows,
  reports); English code and identifiers.
- Repo stays **private until the hackathon ends** (answer keys live here).
- `bash scripts/verify.sh` must stay green; run it before pushing.
