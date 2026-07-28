# Codex artifacts — archive (review/planning only)

Статус (2026-07-28): **разработка кейса 06 продолжается в Claude Code**, в
`cases/06-dev-logging/claude-code/`. Codex использовался для кросс-ревью;
эта папка — архив его артефактов:

- `research_codex.md` — исследование кейса (912 строк).
- `case.md` — конспект кейса.
- `openspec/` — OpenSpec-инкремент `log-analysis-mvp-increment`
  (proposal/design/tasks + 3 capability-спеки). Принятые идеи уже перенесены
  в основной спек через кросс-ревью
  (`cases/06-dev-logging/claude-code/docs/specs/2026-07-28-cross-review-codex.md`);
  кодить напрямую по OpenSpec-инкременту не нужно.
- `.codex/` — скиллы OpenSpec для Codex-харнесса.

Правило: в этой папке не должно появляться `benchmark.py` или `test_*.py` —
`scripts/verify.sh` собирает их по всему репо в общий CI-гейт.
