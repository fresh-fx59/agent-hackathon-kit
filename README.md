# agent-hackathon-kit

> **Кратко по-русски:** это набор для подготовки к 24-часовому корпоративному
> хакатону по трекам PDLC (аналитика / разработка / тестирование). Внутри —
> репетиционная среда: локальные моки корпоративных систем (трекер, анализатор
> качества, git-платформа, TMS), MCP-серверы поверх них, шаблоны skill.md по
> трекам, три прогонных кейса с бенчмарками и сжатый SDD-процесс на 24 часа.
> Всё на чистом Python ≥3.9 без единой внешней зависимости — заводится в любой
> закрытой корпоративной среде командой `python3`. Стратегия и планы — в
> `docs/` (по-русски), инженерные справочники — по-английски.

A rehearsal environment plus reusable primitives for **PDLC-track agent
hackathons**: the kind where the organizers provide pre-built "core agents"
(analytics / development / testing) and teams win by shipping the artifacts
that make those agents useful on a real case — **MCP tools, skill.md files,
supporting components, and worked examples** — judged against benchmarks.

All three known example cases share one pipeline shape:

```
unstructured input  →  skill-driven analysis  →  structured artifact
                    →  MCP write into a corporate system
                    →  benchmark vs a manual baseline
```

This kit ships that pipeline end-to-end per track, runnable against local
mocks, so on case-announcement day only the case-specific edges change.

## Quickstart (3 commands)

```bash
git clone https://github.com/fresh-fx59/agent-hackathon-kit.git && cd agent-hackathon-kit
bash scripts/verify.sh        # full self-check: tests, mock boot, MCP smoke, benchmark self-tests
python3 mocks/run_all.py      # start all 4 corporate-system mocks (Ctrl-C to stop)
```

Requirements: Python ≥ 3.9. **Nothing else.** No pip, no venv, no Node.

## Repo map

| Path | What it is |
|------|------------|
| `docs/` | Strategy, 24h playbook, case-intake worksheet, MCP cheatsheet, transfer & collaboration guides |
| `tracks/` | Per-track `skill.md` prompts (RU): analytics (meeting→BR), development (tech-debt), testing (smart regression) |
| `sdd/` | Compressed 24h spec-driven-development flow + RU templates (spec / plan / tasks) |
| `mcp/` | `lib/minimcp.py` stdlib MCP stdio micro-framework + 4 servers (`tracker`, `quality`, `forge`, `tms`) + client config examples |
| `mocks/` | Local stand-ins for corporate systems: tracker :8801 (Jira-like), quality :8802 (SonarQube-like), forge :8803 (GitLab-like), tms :8804 |
| `cases/` | Three rehearsal cases with inputs, expected artifacts, and scoring `benchmark.py` (each has `--self-test`) |
| `scripts/` | `verify.sh` (full self-check), `run_mocks.sh`, `bootstrap.sh` (env sanity) |

## Where to read next

- Hackathon facts & unknowns → [`docs/brief.md`](docs/brief.md) (RU)
- How to pick a track and win on benchmarks → [`docs/strategy.md`](docs/strategy.md) (RU)
- Official judging criteria (mentors + jury, sanitized) → [`docs/judging-criteria.md`](docs/judging-criteria.md) (RU)
- Hour-by-hour plan for a team of 2 → [`docs/playbook-24h.md`](docs/playbook-24h.md) (RU)
- First 60 minutes after the case drops → [`docs/case-intake.md`](docs/case-intake.md) (RU)
- MCP on one page + debugging → [`docs/mcp-cheatsheet.md`](docs/mcp-cheatsheet.md) (EN)
- Moving into the corporate environment → [`docs/transfer.md`](docs/transfer.md) (EN)
- 2-person git flow → [`docs/collaboration.md`](docs/collaboration.md) (EN)
- Jenkins / messenger-bot / design-export patterns → [`docs/integrations.md`](docs/integrations.md) (EN)
- The technical contracts (ports, API shapes, schemas) → [`docs/design.md`](docs/design.md) (EN)
- Full corporate SDD starter (commands, skills, slides, RU) — the companion
  repo → [fresh-fx59/corp-sdd](https://github.com/fresh-fx59/corp-sdd)

## Transfer to a restricted corporate environment — checklist

The target environment can **pull from public GitHub but never push**, and its
package situation is unknown. The kit is built for exactly that:

- [ ] `git clone` / `git pull` from this public repo inside the corporate env
      (one-way sync by design — see [`docs/transfer.md`](docs/transfer.md)).
- [ ] Run `bash scripts/bootstrap.sh` — confirms `python3 >= 3.9` and prints next steps.
- [ ] Run `bash scripts/verify.sh` — if it passes, everything works: the kit is
      **stdlib-only**, so there is nothing to install and nothing to break.
- [ ] Point MCP servers at real systems via env vars (`TRACKER_URL`,
      `QUALITY_URL`, `FORGE_URL`, `TMS_URL`) — no code changes needed.
- [ ] Adapt auth: real systems need tokens/headers; the mocks need none.
      Grep for `urllib` call sites and add the header in one place.

## License

MIT — see [LICENSE](LICENSE).
