#!/usr/bin/env bash
# v64: prove one-address cite.py output (stdout, stderr, exit) is byte-identical to v63.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SK="$HERE/../../skills"; C="$HERE/fixtures/v60-smalltest/corpus"; RC=0
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
for a in "Security.jsonl:15#TargetUserName" Security.jsonl:12 System.jsonl:8 \
         Security.jsonl@417272 Nope.jsonl:1 Security.jsonl:999 "Security.jsonl:15#NoSuchField"; do
  for v in v63 v64; do
    python3 "$SK/$v/tools/cite.py" --corpus "$C" "$a" >"$T/cite-$v.out" 2>&1; echo "exit=$?" >>"$T/cite-$v.out"
  done
  if diff "$T/cite-v63.out" "$T/cite-v64.out"; then echo "identical: $a"; else echo "DIFFERS: $a"; RC=1; fi
done
exit $RC
