# Collaboration: a 2-person git flow for a 24-hour sprint

Two people, one deadline, no time for merge archaeology. The flow below is
optimized for exactly that — it is trunk-based, commit-happy, and uses the
benchmark as the merge gate.

## The flow

- **Trunk (`main`) is always demoable.** If the demo had to happen right
  now, `main` is what you'd run. Anything that breaks `verify.sh` does not
  land on `main`.
- **Short-lived branches, hours not days.** Branch per task
  (`feat/tracker-auth`, `fix/rubric-weights`), merge as soon as green,
  delete the branch. If a branch is older than ~3 hours, it's too big —
  split it.
- **Commit every green step.** The unit of commit is "verify.sh passes and
  something improved". Small commits are your undo button at 4 a.m.:
  `git log --oneline` becomes the story of the night, and `git revert` beats
  debugging when tired.
- **Tag every benchmark high-score:** `git tag score-71-t14`. At feature
  freeze (T+16 in `docs/playbook-24h.md`) you check out the best tag, not
  your latest hope.

## Benchmarks as pre-merge CI

There is no CI server at 3 a.m. — the CI is a shell command both of you run
before every merge:

```bash
bash scripts/verify.sh && python3 cases/<your-case>/benchmark.py out/artifact.json
```

Rules:

- **Green `verify.sh` is the merge bar.** Never merge on "it should work".
- **The benchmark score goes in the merge commit message**
  (`merge: smarter selection — score 71 (was 63)`). This builds the
  time→score table for the demo for free (see `docs/strategy.md`).
- A change that drops the score doesn't merge, however elegant. Park it on
  its branch and move on.

## Same box vs personal PCs

**Rehearsal (before the event), personal PCs:** normal GitHub flow — both
teammates are collaborators on the repo, sync via push/pull. Run
mocks locally; they bind `127.0.0.1` only, so no port fights between
machines.

**Same box (shared VM at the event):** the tempting mistake is editing one
working copy together. Don't. Instead:

- **Two users / two sessions, two clones**, one per person
  (`~/work-a/`, `~/work-b/`), syncing through the internal forge (or through
  a bare repo on the box: `git init --bare ~/hub.git` and use it as the
  remote — works with zero network).
- **One shared mock instance.** Whoever owns integration starts
  `python3 mocks/run_all.py` once; the other person points at the same ports
  (8801-8804). Don't run two copies — the second will fail to bind, and
  that's correct behavior.
- Shared tmux with two windows beats screen-sharing when pairing on the
  same box; but pair only on the hard parts — parallel work on separate
  files is the default.

## Conflict avoidance by file ownership

Merge conflicts are pure loss at a hackathon, and they are avoidable: agree
on ownership at case intake and hold it (role split in
`docs/playbook-24h.md`).

| Area | Owner |
|---|---|
| `mcp/`, `mocks/`, agent/MCP configs, scripts | Engineer (driver/integrator) |
| `tracks/*/skill.md`, case artifacts, rubric/expected files, `benchmark.py` | Case-owner (content/benchmark) |
| `sdd/` spec & tasks | Case-owner writes, engineer reviews |
| Demo assets (script, score table, recording) | Case-owner |

- Need a change in the other person's file? **Say it out loud, get an ack,
  then edit.** The ack takes 5 seconds; the surprise conflict takes 20
  minutes.
- The tasks checklist (`sdd/tasks-template.md`) carries an owner column —
  the file ownership above is just its projection; keep the two consistent.
- If a conflict happens anyway: the file's owner resolves it, the other
  person watches. Never both silently resolving in parallel.
