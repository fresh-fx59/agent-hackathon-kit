# Mac handoff — case-06 measurement runs (2026-07-29)

The 8 GB Linux box OOM-killed the session doing these runs; all **measurement
runs move to the Mac** (M1 Max, 32 GB). Build/analysis work continues on the box —
this file is only about runs. Results flow back through git: **the ledgers
(`runs-*.jsonl`) are the data; push after every batch.**

## One-time setup

```bash
git clone https://github.com/fresh-fx59/agent-hackathon-kit.git ~/hack/agent-hackathon-kit
cd ~/hack/agent-hackathon-kit && git checkout case06-simple   # or: git pull, if cloned already
npm install -g @qwen-code/qwen-code@0.21.1                    # the exact proven version

mkdir -p ~/hack/petstore-pack
unzip cases/06-dev-logging/petstore_input_pack.zip -d ~/hack/petstore-pack/
# → ~/hack/petstore-pack/petstore_input_pack  (the runner's default path)

export SHERLOCK_API_KEY=…       # linkapi key — ENVIRONMENT ONLY, never on argv
export QWEN_BIN="$(command -v qwen)"   # runners default to ~/.local/bin/qwen (Linux path)
```

`SHERLOCK_BASE_URL` (default `https://linkapi.ai/v1`) and `SHERLOCK_MODEL`
(default `[SP]deepseek-v4-flash`) are already the right defaults.

## P0 — finish the organizers' own test-pack matrix

The A2.1 «≥50 %» bar is defined **on this pack**; until 2026-07-29 it was never
run. Recorded so far: `tc01×v4`, `tc05×v4`. Missing:

```bash
cd cases/06-dev-logging/sherlock/eval/petstore
./run-tc.sh tc03 v4
./run-tc.sh tc01 none
./run-tc.sh tc03 none
./run-tc.sh tc05 none
```

Notes:
- **TC-05 is a negative test** — the correct answer raises *nothing*. Don't
  score it by "did it find things".
- The runner already passes `--approval-mode yolo` (it gates SKILL.md loading
  *and* shell — without it both arms silently run skill-less) and `--auth-type
  openai`. Never add `--safe-mode`.
- Run **sequentially**, one at a time. No parallel fan-out.
- A provider error is refused by the runner (`✗ … NOT recorded`) — that's by
  design; a failed run is not a measurement. Just re-run it.

## P1 — v5 validation (wait for `V5-ASSESSMENT-2026-07-29.md`)

`skills/v5/` is an **unmeasured draft**. Its exact validation matrix lands in
`eval/V5-ASSESSMENT-2026-07-29.md` on this branch (being written now — `git
pull` before starting P1). Core shape: the P0 petstore TCs with `v5`, the
acceptance canary **in both `--approval-mode` configurations**, and one
volume run.

## P2 — volume axis (649 MB corpus) on the corporate model

The corpus is generated, not stored (~700 MB disk):

```bash
cd cases/06-dev-logging/sherlock/eval/bench
CORPUS_OUT=~/hack/hetero-corpus CORPUS_KEY=~/hack/hetero-key python3 gen_corpus.py
SHERLOCK_CORPUS=~/hack/hetero-corpus ./run-bench.sh v4     # ≈20–45 min per arm
SHERLOCK_CORPUS=~/hack/hetero-corpus ./run-bench.sh v5     # after P1 says v5 is sane
```

(`run-bench.sh`'s usage line says `<none|v1|v2|v3>` — stale; it copies
`skills/<arm>` generically, so `v4`/`v5` are valid arms. `CORPUS_KEY` must be
outside `CORPUS_OUT` or the model can read the answers.)

## After every batch

```bash
git add sherlock/eval/petstore/runs-petstore.jsonl sherlock/eval/bench/runs-bench.jsonl
git commit -m "case06: Mac runs — <what>"
git push origin case06-simple
```

Every number quoted anywhere must trace to a ledger row — no summaries as data.
