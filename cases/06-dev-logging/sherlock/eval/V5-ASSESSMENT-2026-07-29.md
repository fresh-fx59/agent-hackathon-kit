# v5 assessment — what the OOM-killed session left, and what it would take to ship it

Date: 2026-07-29. Branch `case06-simple`, v5 as committed in `ed83d87`.
Companion to `GAP-ANALYSIS-2026-07-29.md` §5 and `HANDOFF-MAC-2026-07-29.md` P1.

**Rule of this file:** no number is taken from a summary. Every figure traces to a
ledger row, a `wc`, or a diff. Where there is no measurement it says *unmeasured*,
never an estimate.

---

## 0. Headline

- **The no-subagent rule SURVIVED in v5.** It is at `skills/v5/SKILL.md:119–133`,
  byte-identical to `skills/v4/SKILL.md:91–105`. The head-100 diff was misleading:
  the new «Если логи лежат не в файлах» section was **inserted before** it, not in
  place of it. No regression against the measured report-loss evidence.
- **v5 is v4 + five additions, none measured.** Zero ledger rows anywhere
  (`eval/runs.jsonl` 53 rows, `eval/bench/runs-bench.jsonl` 4, `eval/petstore/runs-petstore.jsonl` 2 —
  `grep -c '"arm": "v5"'` = 0 in all three).
- **Three concrete defects in the v5 text** (§2) — one of them deletes the heading
  of the mandatory citation-verification step that four other lines still point at.
- **Recommendation: v5 does NOT supersede v4 for the hackathon as it stands.**
  Ship v4; backport one 28-line section into it (§5); keep v5 a labelled draft
  until the P0 matrix in §6 returns.

---

## 1. Full diff v4 → v5

`skills/v4/SKILL.md` 316 lines / 27 657 B → `skills/v5/SKILL.md` 550 lines / 47 827 B
(**+234 lines, +73 % bytes**). Nothing was removed except one heading (§2.1).

| # | Addition | v5 lines | Provenance | Purpose |
|---|---|---|---|---|
| A1 | «Если логи лежат не в файлах» — use Loki/ES/Grafana/kubectl/journald tools if the session has them; coverage discipline and context budget carry over with stream/index as the unit; `источник:время` is an acceptable address when there is no line number; **absence of such tools is explicitly not a problem and never a reason to ask or stall** | 91–117 | new prose | **R4** — the extensibility story, in the skill instead of only in prose |
| A2 | «Шаг 0. База знаний» + «КОРОТКИЙ ПУТЬ» + «Границы короткого пути» | 168–236 | **verbatim paste** of `knowledge/SKILL-SECTION.md` | R5 / Ф6 self-learning read path |
| A3 | «Шаг 5б. Спецификация и инварианты» — read SDD/architecture/contracts before naming a cause; report each checkable invariant as соблюдён/нарушен/проверить нечем, with the spec's own identifier verbatim; do **not** trust a `contract violation` hint inside the log itself | 333–356 | new prose | Ф3 «root cause с учётом контекста системы (SDD)» — the petstore pack ships `docs/` |
| A4 | «Если инцидента нет — так и напиши» | 387–405 | new prose (**not** a revert of v3's text) | the TC-05 / postgres-gz false-positive failure |
| A5 | «Шаг 7. Предложить карточку» + human gate + `patterns.py` helper + the mandatory `ЗНАНИЯ:` report line (report section 8) | 422–511 | **verbatim paste** of `knowledge/SKILL-SECTION.md` | R5 write path with human gate |

Verified provenance: of the 40 prose blocks in `knowledge/SKILL-SECTION.md`, 35 appear
verbatim in v5; the 5 that do not are the file's own HTML insertion comments and the
**optional `Stop`-hook frontmatter block, which v5 did NOT adopt** (no hooks in v5
frontmatter — good; that block would have added a shell dependency).

Frontmatter (`name`, `description`) is **byte-identical** to v4. The retired invalid
quote is **not** reintroduced — v5 keeps v4's corrected «520 неудачных попыток / не
упомянув единственный успешный вход» text.

### 1.1 `skills/v5/knowledge/` — a copy, not new work

`diff -rq sherlock/knowledge sherlock/skills/v5/knowledge` → **zero content
differences**. v5/knowledge is a shippable *subset* of the existing `sherlock/knowledge/`:

- copied, byte-identical: `README.md` (17 053 B), `REJECTED.md` (1 655 B),
  `patterns.py` (16 923 B), `patterns/{ssh-bruteforce-storm,proxy-dns-resolve-failure,gz-corpus-needs-shell}.md` (14 326 B)
- deliberately left behind: `DEMO.md`, `SKILL-SECTION.md`, `.gitignore`, `hooks/`,
  `measure/`, `__pycache__/` — correct, those are bench-side, not shippable.

Total v5 payload on disk: **97 784 B** (SKILL.md + knowledge) vs v4's **27 657 B**.

---

## 2. Defects in the v5 text

### 2.1 The «Шаг 6. Проверка улик» heading was deleted — HIGH

The Шаг 5б insertion overwrote the heading line instead of being placed before it:

```
v4:316   ### Шаг 6. Проверка улик — обязательно
v5:333   ### Шаг 5б. Спецификация и инварианты (если они есть)
```

The verification body (`Перед тем как выдать отчёт, перечитай каждую строку…`,
v5:358–370) is now **orphaned under the spec/invariants heading**. Consequences:

- the mandatory step reads as a sub-clause of an *optional* step («Спеки нет — шаг
  пропускается молча»). That is the reading a model does at 47 KB of prompt.
- four surviving cross-references point at a heading that no longer exists:
  v5:131 «перепроверь каждую (шаг 6)», v5:228 «Проверка улик (шаг 6) не пропускается
  никогда», v5:379 «Только проверенные (шаг 6)», plus the numbered procedure gap 5б→7.

This is the single most load-bearing paragraph in the skill — the corrected findings
reinstated the content-comparing citation check *because* the corporate model cited
`Linux_2k.log:106` for a claim whose real occurrences are at 92/585/586/587.
Demoting its heading is a regression against measured evidence.

**Fix:** restore `### Шаг 6. Проверка улик — обязательно` above v5:358 and move
Шаг 5б above it. One line + one move.

### 2.2 «все семь разделов» vs eight — MEDIUM

v5:379–386 defines **8** report sections (A5 added `8. ЗНАНИЯ`), but v5:415 still
says «ответ на него тот же: **все семь разделов**», and v5:407 «Раздел 7 обязателен»
now co-exists with a section 8 that is also declared mandatory («Она есть всегда»).
Half-applied edit: the report list was renumbered, the prose about it was not.

### 2.3 Knowledge-dir fallback paths point at the wrong install name — MEDIUM

v5:181–182 tells the model to fall back to `./.qwen/skills/**sherlock**/knowledge/patterns/`
and `~/.qwen/skills/**sherlock**/knowledge/patterns/`. The runners that will actually
measure v5 install it under **`log-rca`**:

- `eval/petstore/run-tc.sh:67` → `$W/.qwen/skills/log-rca`
- `eval/bench/run-bench.sh:35` → `$W/.qwen/skills/log-rca`
- `acceptance/r1-zero-config.sh:49` → `$QWEN_HOME/skills/log-rca`

(`verify.sh:42`, `eval/run.sh:72`, `knowledge/measure/run-kb.sh:59` use `sherlock` —
the repo is inconsistent with itself, and the paste inherited the losing side.)
The primary instruction («каталог лежит рядом с SKILL.md») should carry, so this is a
dead fallback rather than a hard break — but it is dead in exactly the harness that
decides v5's fate, and a model that takes the fallback literally will report
`ЗНАНИЯ: база пуста` on a corpus where the cards were present.

### 2.4 v5 ships three pre-loaded pattern cards — design question, not a bug

v5 bakes in `ssh-bruteforce-storm`, `proxy-dns-resolve-failure`, `gz-corpus-needs-shell`
(14 326 B). Step 0 instructs the model to read them **before opening anything**. On the
petstore pack — an order-trace incident across Java services — all three are
irrelevant, so every run pays their token cost and carries a non-zero false-match risk
against the very false-positive failure A4 is trying to fix. Whether the loop machinery
or these particular cards drive any measured delta cannot be separated without an
empty-knowledge arm (run 7 in §6).

---

## 3. The evidence v5 argues with

Two v5 additions re-enter territory v4 vacated **on measurement**, and one enters
territory nothing has measured.

| v5 addition | prior measurement | verdict |
|---|---|---|
| A4 «Если инцидента нет» | v3 carried an equivalent paragraph; v4 cut it because it was **measured inert** (12→11 claimed problems) and because the postgres-gz negative control failed in **all four arms** — no arm says «штатная работа» on 32 405 lines with zero ERROR/FATAL/WARNING | A4 is *different prose*, not the reverted paragraph, so it is a legitimate second attempt — **but it is the second attempt at a fix that already failed once**, and it must be paid for in a TC-05 run, not assumed |
| A2+A5 knowledge loop (~+150 lines) | the loop's own measurement (`knowledge/measure/`, 18 runs) shows **+25 % / +8 % steps** — wrong sign for A2.4 — and `REQUIREMENTS.md:141–150` records that the reliability fallback («6/6 vs 5/7») **does not survive recount** (6/7 with card vs 8/11 without; excluding lost reports, 8/9 without vs 6/7 with = no advantage) | the loop currently has **no measured benefit on either axis**. It is in v5 for requirement coverage (Ф6/R5/A1.5), which is a real reason — but it is not yet a performance argument |
| A1 non-file sources, A3 spec/invariants | nothing measured either way | genuinely new ground; A1 is cheap (27 lines) and explicitly self-limiting, A3 is the one addition with a plausible direct payoff on the pack (TC-01 answers already reference invariant `И-1`) |

### The size prior

The one thing this project has measured about skill size is that **bigger lost**:
v2 (24 364 B) was a *net regression* on citations (4.6 vs v3's 18.4) and its
over-broad search died at volume with `Context is too large … 184305 > 177000`.
v5 is **47 827 B of SKILL.md plus ~33 KB of knowledge markdown the model is told to
read before step 1**. That does not mean v5 will regress — v2's failure was a
procedure defect, not a byte count — but it does mean "bigger, unmeasured, at volume"
is the exact shape of the only regression this project has recorded.

---

## 4. What v5 buys that v4 cannot

Per `GAP-ANALYSIS-2026-07-29.md` §5 and A1.5: **`skills/v4/SKILL.md` contains no
knowledge-base step at all.** `grep -i 'knowledge\|карточк\|ЗНАНИЯ' skills/v4/SKILL.md`
returns nothing. So the shipped artifact does not implement:

- **Ф6** (self-learning loop) — REQUIREMENTS closes it with «`knowledge/` + human-gate (R5)»,
  but the shipped skill never reads `knowledge/`;
- **П3** — REQUIREMENTS points the jury at `knowledge/patterns/*.md` «как данные для
  модели»; in v4 no model ever reads them;
- **A1.5 / A2.4** — the organizers' self-learning criteria, `unmeasured`/`unmet`.

That is a genuine, jury-visible hole: today the self-learning loop exists as a *bench
rig*, not as part of the deliverable. v5 is the only artifact that closes it. This is
why the answer is "measure it", not "drop it".

---

## 5. Ship stance

**v4 remains the shipping artifact. v5 stays a labelled draft.** Reasons, in order:

1. v5 has **zero** ledger rows. The project's own rule is that every number traces to
   raw data; shipping v5 would mean shipping a artifact about which we can state nothing.
2. Two of its five additions re-attempt fixes that measurement already rejected once.
3. It carries three text defects (§2), one of which demotes the mandatory citation
   check — the exact defence the corrected findings reinstated.
4. v4 itself is under-measured (see §7) — promoting v5 would replace a
   partially-measured artifact with an unmeasured one three weeks of runs before the event.

**But do three cheap things now, on the box, no runs required:**

- **(a) Fix §2.1, §2.2, §2.3 in v5** — heading restore, seven→eight, `sherlock`→`log-rca`.
  ~5 minutes; without them the P0 matrix measures a typo, not a design.
- **(b) Backport A1 (non-file log sources, 27 lines / ~1.9 KB) into v4 as `skills/v4.1/`.**
  This is the R4 extensibility win the note is after, and it is separable from the
  ~20 KB knowledge machinery that carries all the risk. It is prose-only, additive,
  explicitly self-limiting («Таких инструментов нет — это не проблема»), and it does
  not touch the procedure. Then run **only** run 3 below on v4.1 to confirm
  non-inferiority on the two TCs where v4 has a paired row.
- **(c) Add a one-line pointer** (`skills/SHIPPING`, or a line in `sherlock/README.md`
  when A5.2 gets fixed) naming which version ships. Four versions plus an unmeasured
  v5 in one directory is itself a judging finding (GAP-ANALYSIS §criterion 5).

**Promotion rule for v5** — promote it only if the P0 matrix shows **all** of:
non-inferior on TC-01 (files_cited and RCA correctness), **strictly better on TC-05**
(the negative test — this is A4's whole justification), **strictly better on TC-03**
(the self-learning case — v5's reason to exist), and no report loss / no context blow-up
at volume. Anything less: ship v4(.1) and demo v5 as the labelled R5 extension, which is
an honest and defensible story on its own.

---

## 6. Minimal validation matrix

All runs on the operator's Mac (M1 Max / 32 GB). Common config, from `HANDOFF-MAC-2026-07-29.md`:
`SHERLOCK_MODEL='[SP]deepseek-v4-flash'`, `SHERLOCK_BASE_URL=https://linkapi.ai/v1`,
key in the **environment** only, `--approval-mode yolo` (the runners already pass it),
**never `--safe-mode`**, sequential — no fan-out. **Apply §5(a) before run 2.**

| # | Prio | Run | Why this run decides something |
|---|---|---|---|
| 1 | **P0** | `./acceptance/skill-loads.sh` — the canary, **both `--approval-mode` configurations** (the script runs yolo and no-yolo itself; expect `yes` / `no`) | Artifact #7: the flag gates SKILL.md loading, not just shell. On a different machine and a possibly different qwen build this must be re-confirmed **before** any v5 number is recorded, or the whole matrix may be two skill-less arms again. A canary that only runs the passing case proves nothing — that is why this script tests both. ~4 min |
| 2 | **P0** | `./acceptance/r1-zero-config.sh v5` | R1 is the PRIMARY requirement and v5 is the first version that installs a **subtree** (knowledge/ + a .py) rather than one file. Confirms it is still one-folder-copy zero-config, still self-fires without being named, and that Шаг 0 does not stall or emit `ЗНАНИЯ: база пуста` when the cards are right there (§2.3). ~3 min |
| 3 | **P0** | `run-tc.sh tc01 v5` and `run-tc.sh tc05 v5` | The only **paired** comparison available: `runs-petstore.jsonl` already holds `tc01×v4` (159 s, 10 921 chars, 15 files) and `tc05×v4` (148 s, 9 088 chars, 11 files). TC-05 is the negative test and is the direct verdict on addition A4 — the paragraph v4 cut as measured-inert. If v5 does not beat v4 here, A4 has now failed twice. ~5 min |
| 4 | **P0** | `run-tc.sh tc03 v4`, `run-tc.sh tc03 v5`, and `SHERLOCK_KNOWLEDGE=…/sherlock/knowledge/patterns run-tc.sh tc03 v5` | TC-03 is the self-learning case and v5's entire reason to exist (§4). v4 has no knowledge step, so v4×tc03 is the floor. The pre-seeded arm is the direct A2.4 measurement (TC-01 → TC-03, ≥30 %) on the organizers' own data instead of the 2 000-line proxy corpus that produced the wrong-signed result. ~8 min |
| 5 | **P1** | `run-tc.sh tc01 none`, `run-tc.sh tc03 none`, `run-tc.sh tc05 none` | A5.3 requires a pet-project baseline and `runs-petstore.jsonl` has **no `arm:"none"` row at all**. Without it a v5−v4 delta has no floor and cannot be reported to the jury. Already P0 in `HANDOFF-MAC-2026-07-29.md` for the overall matrix; P1 *for the v5 decision* because v4 is the comparator there. ~8 min |
| 6 | **P1** | `SHERLOCK_CORPUS=~/hack/hetero-corpus ./run-bench.sh v4` **and** `./run-bench.sh v5` (649 MB, seed 20260728) | The volume axis is where the only recorded regression happened (v2's `Context is too large`), and v5 is +73 % prompt plus ~33 KB of mandated pre-reads. **Both arms are required**: v4 has never been run at volume — every 649 MB number attributed to the shipping line is actually v3's (`runs-bench.jsonl` has rows for `none/v1/v2/v3` only). Running v5 alone would compare v5 against v3. ~45–90 min incl. corpus generation |
| 7 | **P1** | `run-tc.sh tc01 v5` with `skills/v5/knowledge/patterns/` emptied (label `v5-empty`) | Separates "the loop machinery" from "these three specific cards" (§2.4). All three shipped cards are irrelevant to a petstore order trace; if v5 differs from v4 only via `v5-empty`, the delta is the procedure, and the cards are pure cost. ~3 min |
| 8 | **P1** | `run-tc.sh tc01 v5` ×2 more (n=3 on the single most-compared cell) | Run-to-run variance in this ledger is enormous on identical config: `postgres-gz × v3` produced **128 chars / 0 files** on one row and **8 959 chars / 1 file** on another. One paired run cannot promote or kill a version, and artifact #5 (a mean of 56.6 hiding a median of 15) is exactly this failure. ~6 min |
| 9 | **P2** | A3 spec-step probe: `run-tc.sh tc01 v5` answer grepped for the pack's own invariant ids (`И-1`, `I-…`) against the same grep on the `tc01×v4` row already in the ledger | Addition A3 is the one with a plausible direct payoff (the pack ships `docs/` with SDD). Scored off run 3's stored `answer` field — **no extra run**, listed separately because it is a distinct claim. 0 min |
| 10 | **P2** | R4 negative check on addition A1: grep runs 2/3/4 answers for a request to connect a log source, or a stall citing its absence | A1's own guarantee is «никогда не проси подключить источник и никогда не останавливайся из-за его отсутствия». With no MCP source in existence (A3.2 unmet) this is the **only** testable half. Scored off stored answers — no extra run. 0 min |

**P0 total ≈ 20 minutes of wall clock, 8 runs.** That is enough to decide v5 vs v4.
P1 adds ~2 hours and is what makes the decision reportable to a jury.

Recording, per `HANDOFF-MAC-2026-07-29.md`: the runner refuses to record provider
errors (`✗ … NOT recorded`) — a failed run is not a measurement, re-run it. Push the
ledgers after every batch. Every number quoted anywhere must trace to a ledger row.

---

## 7. Cross-document inconsistencies found while checking (not v5's fault)

`REQUIREMENTS.md` itself is **internally consistent and fully applied** — all six
scripted substitutions from `ed83d87` landed cleanly, no half-applied sentence, no
dangling reference to the retired quote or to «инцидент бывает не всегда» (those live
in SKILL.md, and neither is referenced from REQUIREMENTS). Only cosmetic damage:
three sed-produced overlong lines (105, 122, 126) that break the file's wrap.

But it now **contradicts the vault note** `projects/active/case06-sherlock-2026-07-28.md`,
which a resuming agent reads first:

1. **Recall.** REQUIREMENTS:86/103/120 — shipping line **9,1 % (1/11)**, best arm
   **18,2 % (2/11, v2)**. The note's Current state still says «recall **18.2 %** vs the
   ≥50 % bar» without saying that figure belongs to **v2, the arm we refuse to ship**.
   Both trace to `scores.jsonl`; the note is the stale one.
2. **Self-improvement reliability.** REQUIREMENTS:141–150 explicitly retracts
   «6/6 против 5/7» as «неверна по обеим сторонам» (recount over 18 runs: 6/7 with card
   vs 8/11 without; 8/9 vs 6/7 excluding lost reports ⇒ no advantage). The note's
   *Corrected findings* still asserts «6/6 with a card vs 5/7 without» as what «does
   hold». **The note must be updated or the deck will repeat a retracted number.**
3. **Version attribution.** REQUIREMENTS calls the shipping line **v3**; the note ships
   **v4**. Both are defensible readings — v4 = v3 minus two paragraphs — but `runs.jsonl`
   holds exactly **three** v4 rows (OpenSSH, postgres-gz, postgres) and `runs-bench.jsonl`
   holds **none**, so every volume and citation figure attributed to "v3→v4" is v3's.
   Either re-measure v4 (run 6) or say "v3-line" everywhere.

Not an inconsistency: the note's «181 chars at 2.6M tokens» and REQUIREMENTS' «3,6 млн
→ 157 символов» are two different real rows — `nginx × v1` (503 s, 2 616 820 tok,
181 chars) in `runs.jsonl` and `bench649 × none` (243 s, 3 631 278 tok, 157 chars) in
`runs-bench.jsonl`. Both check out.
