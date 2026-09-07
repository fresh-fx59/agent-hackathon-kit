#!/usr/bin/env bash
# BlueSky diagnostic launcher: expose one staged root and one corpus below hack.
# All mounts live only in this private mount namespace.
set -euo pipefail

stage=${1:?staged root outside /home/claude-developer/hack required}
corpus=${2:?approved corpus directory required}
secret_wrapper=${3:?with-secret.sh required}
hack=/home/claude-developer/hack
name=$(basename "$stage")
target="$hack/$name"

case "$stage" in "$hack"/*) echo 'stage must be outside hack' >&2; exit 2;; esac
[ -d "$stage/skills/v52" ] || { echo 'missing staged v52 skill' >&2; exit 2; }
[ -f "$stage/prompt.txt" ] || { echo 'missing staged prompt' >&2; exit 2; }
[ -x "$stage/tools/run-clean-direct-qwen.sh" ] || { echo 'missing staged runner' >&2; exit 2; }
[ -f "$stage/tools/corporate-settings.py" ] || { echo 'missing staged settings generator' >&2; exit 2; }
[ "$(find "$corpus" -mindepth 1 -maxdepth 1 -type f | wc -l)" = 1 ] || {
  echo 'approved corpus must expose exactly one regular file' >&2; exit 2;
}

mkdir -p "$stage/corpus-source"
exec unshare --mount --fork bash -ceu '
  stage=$1 corpus=$2 target=$3 hack=$4 secret_wrapper=$5
  mount --make-rprivate /
  # Bind the sole approved corpus before hiding hack, then retain it only under
  # the staged run root.  No host mount table is changed.
  mount --bind "$corpus" "$stage/corpus-source"
  mount -t tmpfs tmpfs "$hack"
  mkdir -p "$target"
  mount --rbind "$stage" "$target"
  test ! -e "$hack/sherlock-v52-winevtx-20260907-r2/work/worklist.tsv"
  test -d "$target/corpus-source"
  "$target/tools/run-clean-direct-qwen.sh" "$target" "$target/corpus-source" \
    "$target/skills/v52" "$target/prompt.txt" \
    "$target/tools/corporate-settings.py" "$secret_wrapper"
' -- "$stage" "$corpus" "$target" "$hack" "$secret_wrapper"
