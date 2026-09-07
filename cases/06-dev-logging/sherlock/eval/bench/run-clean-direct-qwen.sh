#!/usr/bin/env bash
# Direct diagnostic runner: caller supplies a fresh root, corpus, prompt and skill.
# Its cwd is Qwen's only documented workspace selector, so it is an input boundary.
set -euo pipefail

root=${1:?fresh run root required}
corpus=${2:?corpus directory required}
skill_dir=${3:?directory containing selected skill required}
prompt_file=${4:?prompt file required}
settings_generator=${5:?corporate-settings.py path required}
secret_wrapper=${6:?with-secret.sh path required}

mkdir -p "$root/work" "$root/home" "$root/.qwen"
ln -s "$corpus" "$root/corpus"
python3 "$settings_generator" emit-run --skill-directory "$skill_dir" > "$root/.qwen/settings.json"
(cd "$root" && pwd) > "$root/workspace-cwd-precontact.txt"
cd "$root"
set +e
HOME="$root/home" QWEN_SKILL_ROOT="$skill_dir" OPENAI_BASE_URL=https://api.neuraldeep.ru/v1 \
  "$secret_wrapper" neuraldeep_api_key --env OPENAI_API_KEY -- \
  /home/claude-developer/.local/bin/qwen --auth-type openai --model deepseek-v4-flash \
  --max-session-turns -1 --max-tool-calls -1 --openai-logging \
  --openai-logging-dir "$root/openai-logs" --output-format json "$(cat "$prompt_file")" \
  > "$root/qwen-output.json" 2> "$root/qwen-stderr.log"
rc=$?
printf '%s\n' "$rc" > "$root/qwen-exit-status.txt"
printf '%s %s\n' "$(date -u +%FT%TZ)" "$rc" > "$root/qwen-terminal.txt"
exit "$rc"
