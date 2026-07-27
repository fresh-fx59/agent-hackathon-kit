#!/usr/bin/env bash
# new-case.sh -- scaffold a new eval case from a template, green from second zero.
#
#   bash scripts/new-case.sh <slug> --mode rubric|findings|selection [--dest DIR]
#
# 1. validates the slug (kebab-case) and the mode;
# 2. copies scripts/case-templates/<mode>/ to cases/<slug>/ (or DIR/<slug>/),
#    refusing to overwrite an existing target;
# 3. substitutes the slug into the scaffolded files (README paths etc.);
# 4. runs the new benchmark.py --self-test to PROVE the scaffold is green;
# 5. prints a RU next-steps checklist.
#
# The scaffold ships self-consistent placeholder fixtures, so --self-test
# passes immediately; the team replaces placeholders with real case content
# and keeps it green.  Needs only bash + coreutils + python3.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODES="rubric findings selection"

usage() {
  cat <<'EOF'
usage: bash scripts/new-case.sh <slug> --mode rubric|findings|selection [--dest DIR]

  <slug>   kebab-case case name, e.g. analytics-support-tickets
  --mode   which benchmark shape to scaffold:
             rubric     markdown artifact scored 0-100 against rubric.json
             findings   findings.json scored precision/recall/F1
             selection  selection.json scored recall/efficiency
  --dest   parent directory for the new case (default: cases/)
EOF
}

err() { printf 'error: %s\n' "$*" >&2; }

# ------------------------------------------------------------------ arguments
slug=""
mode=""
dest="$ROOT/cases"
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --mode)
      [ $# -ge 2 ] || { err "--mode needs a value"; usage; exit 2; }
      mode="$2"; shift 2 ;;
    --dest)
      [ $# -ge 2 ] || { err "--dest needs a value"; usage; exit 2; }
      dest="$2"; shift 2 ;;
    -*)
      err "unknown option: $1"; usage; exit 2 ;;
    *)
      if [ -n "$slug" ]; then err "unexpected extra argument: $1"; usage; exit 2; fi
      slug="$1"; shift ;;
  esac
done

if [ -z "$slug" ]; then
  err "missing <slug>"; usage; exit 2
fi
if ! [[ "$slug" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  err "slug must be kebab-case ([a-z0-9] groups separated by single dashes), got: $slug"
  exit 2
fi
case " $MODES " in
  *" $mode "*) ;;
  *) err "--mode must be one of: ${MODES// /|} (got: '${mode:-<empty>}')"; usage; exit 2 ;;
esac

template="$ROOT/scripts/case-templates/$mode"
if [ ! -d "$template" ]; then
  err "template dir missing: $template"; exit 1
fi

target="$dest/$slug"
if [ -e "$target" ]; then
  err "target already exists, refusing to overwrite: $target"
  exit 1
fi

# ------------------------------------------------------------ copy + rename
mkdir -p "$dest"
cp -R "$template" "$target"

# Substitute the slug into every scaffolded text file (sed is not coreutils,
# so do the replacement with python3).
python3 - "$target" "$slug" <<'PY'
import os, sys
target, slug = sys.argv[1], sys.argv[2]
for base, _dirs, files in os.walk(target):
    for name in files:
        path = os.path.join(base, name)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if "__CASE_SLUG__" in text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace("__CASE_SLUG__", slug))
PY

# --------------------------------------------------- prove the scaffold green
printf '\n== self-test of the fresh scaffold ==\n'
if ! python3 "$target/benchmark.py" --self-test; then
  err "fresh scaffold failed its own --self-test: $target/benchmark.py"
  err "this is a kit bug -- the template fixtures must be self-consistent"
  exit 1
fi

# ------------------------------------------------------------ RU next steps
case "$mode" in
  rubric)
    placeholders="rubric.json (FACT-1..FACT-3 + required_sections), expected-output.md, входные данные кейса" ;;
  findings)
    placeholders="expected-findings.json (3 находки-плейсхолдера), код для анализа (src/)" ;;
  selection)
    placeholders="testcase-map.json (TC-1..TC-5), expected-selection.json (must_run/nice_to_run/rationale), diff.patch" ;;
esac

cat <<EOF

Готово: $target (режим: $mode) — self-test зелёный с нулевой секунды.

Дальше:
  1. Замените плейсхолдеры: $placeholders.
     Полный чек-лист «что заменить» — в $target/README.md.
  2. После каждой правки эталона/рубрики: python3 $target/benchmark.py --self-test
     (должен оставаться зелёным).
  3. Прогоните агента на входных данных кейса и оцените артефакт:
     python3 $target/benchmark.py <артефакт-агента>
  4. Поставьте бенчмарк гейтом в CI: ci/gate.sh (флаг --score-only печатает
     одно число для порога).
  5. bash scripts/verify.sh — новый кейс подхватывается автоматически
     (глоб cases/*/benchmark.py).
EOF
