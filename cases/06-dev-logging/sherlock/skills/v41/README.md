
# Sherlock (log-rca) — running it in the corporate environment

This is the operator runbook for **this skill directory** (arm v41). `SKILL.md` is
what the model reads; this README is what a human reads before the first run.

> Source: arm **v41**, proven end to end on 2026-08-27 by paid run
> `20260827T173511Z-v41` — a clean 323-line report AND every request under the
> 262,000-token ceiling (peak 236,678), $0.178849, 107 minutes.

## Summary

You run `qwen` **interactively**, so there is no launcher. Three things to set up
once, then a session loop that is two keystrokes long.

**It takes what you were actually given.** A `.zip` of `.evtx` files is the
normal case and is handled — one command, §3.

## 1. Settings (once)

Put this in your qwen `settings.json`. Nothing generates it for you — copy it.
These are the exact values the proven run used.

```json
{
  "context": { "autoCompactThreshold": 0.7 },
  "mcp": { "excluded": [] },
  "model": {
    "chatCompression": { "maxRecentFilesToRetain": 0 },
    "generationConfig": {
      "contextWindowSize": 262000,
      "samplingParams": { "max_tokens": 20000 }
    },
    "sessionTokenLimit": 230000,
    "skipStartupContext": true
  },
  "skills": { "disabledLevels": ["bundled", "extension", "user"] },
  "tools": {
    "core": ["read_file", "write_file", "edit", "grep_search", "glob",
             "run_shell_command", "todo_write", "list_directory", "skill",
             "agent"]
  }
}
```

What each one is doing, so you can defend it:

| key | why |
|---|---|
| `contextWindowSize: 262000` | the ceiling everything else is derived from |
| `max_tokens: 20000` | `COMPACT_MAX_OUTPUT_TOKENS` is 20,000; a smaller budget starves the compaction that saves the session |
| `sessionTokenLimit: 230000` | the ONLY exact client-side check. `hard = window − 23,000` is not a send ceiling — after three failed rescues qwen sends the oversized prompt anyway |
| `skipStartupContext: true` | stops the session paying for project context it never uses |
| `maxRecentFilesToRetain: 0` | compaction keeps the summary, not a pile of file bodies |
| `disabledLevels` | no other skill loads and competes for the window |
| `tools.core` | ten tools; every extra tool schema is re-sent on every turn |

On the proven run the largest prompt ever sent was **229,978 tokens — 22 under
`sessionTokenLimit`**. That is this table working.

## 2. Install the skill (once) — and how to do the agent by hand

```
mkdir -p <PROJECT>/.qwen/skills
cp -r <KIT>/cases/06-dev-logging/sherlock/skills/v41 <PROJECT>/.qwen/skills/log-rca
```

The skill also needs one **subagent definition**. Without it the phases run
inline in the parent and the session runs out of context. Either run:

```
python3 <PROJECT>/.qwen/skills/log-rca/tools/brief.py --install-agents <PROJECT>/.qwen/agents
```

**or do it by hand, no python:** create the file

```
<PROJECT>/.qwen/agents/sherlock-triage.md
```

by copying the ready-made file that ships next to this README:

```
mkdir -p <PROJECT>/.qwen/agents
cp <PROJECT>/.qwen/skills/log-rca/agents/sherlock-triage.md <PROJECT>/.qwen/agents/
```

That is the whole install — one directory copied, one file created.

## 3. Prepare the corpus (once per investigation)

If you were handed a **directory of text or JSONL logs**, you are done — point
the task at it.

If you were handed **an archive, or `.evtx` files, or a mix** — the normal case
— run one command:

```
python3 .qwen/skills/log-rca/tools/ingest.py <THE ZIP OR FOLDER> --out ./corpus
```

It unpacks `.zip`, `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz` and `.7z`, converts
`.evtx` to JSONL, copies text and `.gz` straight through, and ignores `__MACOSX`
sidecars. Measured on a real 6 MB `winevt.zip`: **296 entries → 143 channels in
3.4 seconds.**

It **never loses input quietly.** Everything it could not read is printed and
written to `corpus/_ingest-manifest.tsv`, and the command exits non-zero. Pass
`--keep-going` only when you have read the list and accept the gap.

An empty channel is not an error — Windows ships dozens that never recorded
anything, and they appear in the manifest as «пустой канал».

**`.evtx` needs a converter — and on Windows you already have one.**

- **Windows: nothing to install.** `Get-WinEvent` ships with the OS and is used
  automatically. (Written from the documented interface; we have no Windows box
  to prove it on, so if it fails the tool says which command failed and falls
  through to the options below rather than losing the channel.)
- **Anywhere else, one command:**

      python3 .qwen/skills/log-rca/tools/ingest.py <INPUT> --out ./corpus --install-converter

  which runs `pip install --user python-evtx xmltodict` first. It is a flag and
  not automatic on purpose: installing software on a corporate machine is the
  owner's decision, so the tool offers the cure and a human takes it.
- **Or the binary:** `cargo install evtx` / `brew install evtx`.

If no converter works, the manifest names the file and every cure — it never
pretends the channel was empty.

Sanity check:

```
head -c 200 corpus/*.jsonl | head -5     # records must start with {
wc -l corpus/_ingest-manifest.tsv
```

## 4. The session loop (what you actually do)

```
cd <PROJECT>
qwen
```

1. Type `/sherlock`.
2. Paste your task: the corpus path and what you want answered.
3. **Wait.** The skill maps the corpus, builds a worklist and hands the phases to
   subagents. Do not help it; do not read files for it.
4. When it prints a **handoff block**, type `/clear`, then type `/sherlock`
   again. **That is all.** You do not need to tell it where it was: the first
   command of every Sherlock session is
   `checkpoint.py resume --work ./work`, which reads `work/checkpoint.json` and
   prints `СТУПЕНЬ СЕЙЧАС: <stage>`. A cleared session has no conversation, and
   that file is the only memory it needs.
5. Repeat step 4 at every handoff block. The proven run took **three**:
   triage → draft → repair → done.
6. At stage `done` the report is at `<PROJECT>/work/report.md`.

**Why `/clear` and not «продолжай».** `/clear` drops the loaded skill and starts
a fresh session; the reseed re-enters the skill, which re-reads the checkpoint.
On the proven run this reset the parent from 87,894 tokens to **exactly 44,736**,
twice. Without it the parent grows until it dies — that is precisely how the
run earlier the same day failed.

**`/clear` REFUSES while background work is alive.** If nothing happens, a
subagent is still running. Wait, then `/clear` again.

## 5. Do you have to check the result yourself? No.

The skill checks itself, in two places:

- its **VERIFY step** runs `citecheck`, `statecheck` and `triagecheck` and
  requires each to exit 0 before it may deliver; a non-zero gate sends it into
  the `repair` stage instead of to you;
- a **Stop hook** (`tools/stopcheck.py`, declared in the skill's own front
  matter) fires when the session tries to finish and blocks it while a gate is
  failing — «fix worklist/rules.tsv, rerun triagecheck, then deliver
  work/report.md».

So a report that reaches you has already passed all three gates. Run them again
only if you want to audit the auditor:

```
python3 .qwen/skills/log-rca/tools/citecheck.py  work/report.md --corpus <CORPUS> --require-quote --ledger work/worklist.tsv
python3 .qwen/skills/log-rca/tools/statecheck.py --corpus <CORPUS> --report work/report.md
python3 .qwen/skills/log-rca/tools/triagecheck.py --worklist work/worklist.tsv --rules work/rules.tsv --corpus <CORPUS>
```

The paid run cleared this bar unaided: citecheck 0, statecheck 0 (864 records, 0
unaccounted), triagecheck 0 with 250 of 250 rows closed.

## What goes wrong, and what it means

| symptom | cause | what to do |
|---|---|---|
| «I see no logs» / an empty map | the corpus was never ingested | run `ingest.py` — §3 |
| the session goes quiet for minutes | a subagent is working | wait; the parent is idle on purpose |
| `/clear` does nothing | background work still alive | wait, then `/clear` again |
| it starts reading `worklist.tsv` itself | it ignored the cursor | tell it to use `worklist.py next` and `verdict --from-stdin` |
| a gate exits non-zero | the report is not finished | give the failure back to the session; never hand-edit the report |
| context climbs past 230,000 | a handoff block was missed | `/clear`, `/sherlock`; it resumes from the checkpoint |

## Known gaps

- The report is written in Russian by design — the gates parse Russian literals
  verbatim.
- `.evtx` conversion needs one of two converters installed (§3). Everything else
  in the pipeline is dependency-free python.
