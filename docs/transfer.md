# Transfer: moving the kit into a restricted corporate environment

The target environment: hand-made VMs, an agent CLI (qwen-coder-class) with a
DeepSeek-class model (256k context), MCP support, Jenkins, a GitLab-like
forge — and a firewall that allows **pulling from public GitHub but never
pushing back**. This doc is the transfer procedure and the habits that make
the kit survive there.

## 1. Get the code in (pull-only, one-way by design)

```bash
git clone https://github.com/fresh-fx59/agent-hackathon-kit.git
cd agent-hackathon-kit
```

- Updates during the event: `git pull` (fast-forward only; you never commit
  in this clone — treat it as read-only vendor code and copy what you adapt
  into your team's working repo on the internal forge).
- Nothing ever flows outward: no push remote, no telemetry, no network calls
  beyond `127.0.0.1` in any kit code. If security asks, that sentence plus
  `grep -rn "urllib" mcp/ mocks/` is the audit.
- If GitHub is mirrored internally, clone the mirror — the kit has no
  submodules and no LFS, a plain clone is complete.

## 2. Why stdlib-only matters (and how to keep it)

The corporate package situation is unknown: pip may be blocked, an internal
PyPI mirror may be stale, and one failed `pip install` costs an hour of your
24. The kit's answer is a hard guarantee: **everything runs on bare
`python3` ≥ 3.9 stdlib** — mocks, MCP servers, benchmarks, tests.

Keep the guarantee while you extend the kit during the event:

- No `pip install`, ever. If you're tempted, there's a stdlib answer:
  HTTP → `urllib.request` / `http.server`; JSON → `json`;
  diffs → `difflib`; parsing → `re` + `html.parser`; concurrency →
  `threading`; CLI → `argparse`.
- Avoid 3.10+ syntax: no `match` statements, no `X | Y` in runtime
  annotations (the VM's python may be 3.9).
- Set a named `User-Agent: agent-hackathon-kit/0.1` on urllib requests —
  corporate proxies and WAFs commonly reject the default one.

## 3. Verify the environment in one command

```bash
bash scripts/bootstrap.sh   # checks python3 >= 3.9, prints next steps
bash scripts/verify.sh      # tests + mock boot + MCP smoke + benchmark self-tests
```

If `verify.sh` passes, the whole kit works on that VM — there is nothing
else to install. Run it again after any adaptation; it doubles as your CI
(see `collaboration.md`).

## 4. What to adapt first

In order of payoff:

1. **Backend URLs** — point MCP servers at real systems via env
   (`TRACKER_URL`, `QUALITY_URL`, `FORGE_URL`, `TMS_URL`) in the agent's
   `mcpServers` config. Zero code changes. See `docs/mcp-cheatsheet.md`.
2. **Auth headers** — real systems want a token. Each server does HTTP
   through one helper; add
   `req.add_header("Authorization", "Bearer " + os.environ["TRACKER_TOKEN"])`
   there, keep the token in env, never in code.
3. **API shape deltas** — real tracker/forge/TMS responses will differ from
   the mocks in field names. Adapt the thin mapping inside the MCP server
   (that's why the servers are thin); the tool names and schemas the agent
   sees can stay identical.
4. **skill.md wrapper** — if the core agent wants its own skill format,
   transplant the kit skill body (`tracks/*/skill.md`) into their wrapper;
   the content is plain markdown on purpose.

## 5. Prompting a 256k-context DeepSeek-class model

Long-context models are strong but order- and structure-sensitive. Habits
that pay off:

- **Reference material first, task last.** Put the transcript / code /
  diff / schema at the TOP of the prompt, then the instructions, and restate
  the actual question as the LAST lines. Long-context models weight the end
  of the prompt heavily; a task buried mid-prompt gets lost.
- **Delimit everything.** Fence each input in tags
  (`<transcript>…</transcript>`, `<diff>…</diff>`) and refer to the tags in
  the task. Makes chunk boundaries and citations reliable.
- **Chunk big codebases deliberately.** 256k ≈ roughly 25-30k lines of code —
  don't paste a whole repo. Do a two-pass map-then-read: pass 1 gets file
  list + signatures and returns "files worth reading"; pass 2 sends only
  those files in full. For diffs, send the diff plus only the touched files.
- **Ask for structured JSON matching the kit schemas.** End the task with
  "Output ONLY valid JSON matching this schema:" and paste the schema (e.g.
  `findings.json`: `[{file, line, category, severity, description,
  fix_hint}]`, or `selection.json`: `{selected:[{id, reason}], strategy}` —
  all in `docs/design.md`). Then validate with `json.loads` and retry once
  with the parse error appended. That validate-retry loop is worth more than
  any prompt wording.
- **One artifact per call.** Don't ask for the analysis AND the tracker
  write-up in one shot; produce the artifact, verify it (benchmark!), then
  let MCP tools do the writing.
- **Determinism for benchmarks:** low temperature if the CLI exposes it, and
  keep the exact prompt used for your best score under git — the score table
  in the demo must be reproducible.
