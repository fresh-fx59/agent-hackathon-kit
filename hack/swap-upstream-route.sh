#!/usr/bin/env bash
# swap-upstream-route.sh — change the PROVIDER and MODEL of a LIVE Sherlock run.
#
#   ./swap-upstream-route.sh [--create] [--generation N] [--key-file PATH] \
#       [--key-secret NAME] <route-file> <base> <model> [expected-identity]
#
#   ./swap-upstream-route.sh \
#       /home/claude-developer/hack/sherlock-runs-v39-r1/trace.upstream.route.json \
#       https://api.closerouter.dev/v1 \
#       deepseek/deepseek-v4-flash-0731
#
# WHY THIS EXISTS. UPSTREAM_BASE, UPSTREAM_MODEL and
# UPSTREAM_EXPECTED_RETURNED_IDENTITY were read ONCE at proxy import, so changing
# provider or model meant restarting the proxy, which means restarting the run —
# 2h42m and ~14 CNY, measured three times. The three paid v38 runs all failed on
# the PROVIDER, not the harness, and the fix (CloseRouter, 1/27th the cost) is a
# different base URL plus a different model id. The proxy now reads the route
# from a JSON file on EVERY relayed call, so a swap is a file write. This script
# is that write, done safely.
#
# THE THREE FIELDS MOVE TOGETHER, ALWAYS, and that is the whole reason the route
# is one file. `model_family` keeps the vendor prefix, so
# same_family('deepseek/deepseek-v4-flash-0731', 'deepseek-v4-flash-0731') is
# FALSE (measured 2026-08-26): a CloseRouter base left with a linkapi expected
# identity trips the lane guard on the very first call. The expected identity
# therefore defaults to the model id and can only be set in the same write.
#
# WHAT MAKES IT SAFE
#   * The write is a temp file in the SAME directory followed by `mv -f`
#     (rename(2), atomic within one filesystem). A reader sees either the whole
#     old route or the whole new one — never a torn one.
#   * Mode 0600 and the run user's ownership are set on the temp file BEFORE the
#     rename, so the file is never briefly world-readable.
#   * It refuses a non-absolute route path, a missing parent directory, and a
#     path that does not already exist unless --create is given, so a typo
#     cannot quietly leave the proxy reading a file nobody is swapping.
#   * It refuses to LOWER `generation`. The generation is the operator's proof
#     of which write is live; a swap that silently goes backwards makes every
#     receipt afterwards a lie.
#   * IT WRITES NO CREDENTIAL, EVER. A route names a key_file PATH. The proxy
#     REFUSES a route file carrying a key/api_key/token/secret field, so a
#     secret cannot even be smuggled in by hand. --key-secret delegates to
#     swap-upstream-key.sh rather than reimplementing the secret path.
#
# The proxy fails CLOSED on an unusable route file: it refuses the request with a
# diagnosis (503, no credential in it) rather than falling back to the env values
# or to the route it saw last. So the worst case of a bad swap is refused calls,
# never calls billed to the wrong provider or judged against the wrong identity.
set -euo pipefail

RUN_USER="${RUN_USER:-claude-developer}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The credential swapper lives beside this script in the repo, but the
# operator's copy predates the repo `hack/` dir and sits in their home. Look in
# both rather than making --key-secret depend on where the kit is checked out.
KEY_SWAP="${SWAP_UPSTREAM_KEY:-}"
if [ -z "$KEY_SWAP" ]; then
  for candidate in "$HERE/swap-upstream-key.sh" \
                   "/home/claude-developer/hack/swap-upstream-key.sh"; do
    [ -x "$candidate" ] && { KEY_SWAP="$candidate"; break; }
  done
  KEY_SWAP="${KEY_SWAP:-$HERE/swap-upstream-key.sh}"
fi

die() { echo "swap-upstream-route.sh: $*" >&2; exit 1; }

create=0
generation=""
key_file=""
key_secret=""
args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --create) create=1 ;;
    --generation) generation="${2:?--generation needs a value}"; shift ;;
    --generation=*) generation="${1#*=}" ;;
    --key-file) key_file="${2:?--key-file needs a value}"; shift ;;
    --key-file=*) key_file="${1#*=}" ;;
    --key-secret) key_secret="${2:?--key-secret needs a value}"; shift ;;
    --key-secret=*) key_secret="${1#*=}" ;;
    -h|--help) sed -n '2,50p' "$0"; exit 0 ;;
    --) shift; while [ "$#" -gt 0 ]; do args+=("$1"); shift; done; break ;;
    -*) die "unknown option: $1" ;;
    *) args+=("$1") ;;
  esac
  shift || true
done

[ "${#args[@]}" -ge 3 ] && [ "${#args[@]}" -le 4 ] || die \
  "usage: swap-upstream-route.sh [--create] [--generation N] [--key-file PATH]
     [--key-secret NAME] <route-file> <base> <model> [expected-identity]"
dest="${args[0]}"
base="${args[1]}"
model="${args[2]}"
expected="${args[3]:-$model}"

case "$dest" in /*) ;; *) die "route-file path must be absolute: $dest" ;; esac
case "$base" in
  http://*|https://*) ;;
  *) die "base must be an absolute http/https URL: $base" ;;
esac
base="${base%/}"
[ -n "$model" ] || die "model must not be empty"
[ -n "$expected" ] || die "expected-identity must not be empty"
# A control character in a model id or a URL is a header/URL-injection vector,
# and the proxy refuses one. Refuse it here too, so the operator learns at the
# write and not on a 503 twenty minutes into a paid run.
if printf '%s' "$dest$base$model$expected" | LC_ALL=C grep -q '[^[:print:]]'; then
  die "route fields must be printable single-line strings"
fi
if [ -n "$key_file" ]; then
  case "$key_file" in /*) ;; *) die "key-file path must be absolute: $key_file" ;; esac
fi
if [ -n "$key_secret" ] && [ -z "$key_file" ]; then
  die "--key-secret needs --key-file: the secret's VALUE goes into a key file,
     and the route names that file's PATH"
fi

destdir="$(dirname "$dest")"
[ -d "$destdir" ] || die "directory does not exist: $destdir (is the run's trace dir right?)"
if [ ! -e "$dest" ] && [ "$create" -ne 1 ]; then
  die "no route file at $dest — the live run may not be using one.
     Check the proxy was started with UPSTREAM_ROUTE_FILE=$dest, then re-run
     with --create if you really mean to create it."
fi
[ ! -e "$dest" ] || [ -f "$dest" ] || die "$dest exists and is not a regular file"

# GENERATION NEVER GOES BACKWARDS. Read the live one first; default to one past
# it. An explicit --generation lower than the live one is a refusal, not a warning.
before_gen=""
before_base=""
if [ -f "$dest" ]; then
  before_gen="$(python3 - "$dest" <<'PY' || true
import json, sys
try:
    row = json.load(open(sys.argv[1], encoding="utf-8"))
    value = row.get("generation")
    print(value if isinstance(value, int) and not isinstance(value, bool) else "")
except Exception:
    print("")
PY
)"
  before_base="$(python3 - "$dest" <<'PY' || true
import json, sys
try:
    row = json.load(open(sys.argv[1], encoding="utf-8"))
    print("%s | %s | %s" % (row.get("base"), row.get("model"),
                            row.get("expected_returned_identity")))
except Exception:
    print("<unreadable>")
PY
)"
fi
if [ -z "$generation" ]; then
  if [ -n "$before_gen" ]; then generation=$(( before_gen + 1 )); else generation=1; fi
fi
case "$generation" in
  ''|*[!0-9]*) die "generation must be a non-negative integer: $generation" ;;
esac
if [ -n "$before_gen" ] && [ "$generation" -lt "$before_gen" ]; then
  die "refusing to LOWER generation $before_gen -> $generation.
     The generation is the receipt for which write is live; going backwards
     makes every later receipt a lie. Pass --generation $(( before_gen + 1 )) or higher."
fi

# The key first, if asked: a route that names a key file the run cannot read is
# a route that refuses every call. Delegated, never reimplemented — the secret
# path (with-secret.sh --file-env, atomic 0600 install) lives in one place.
if [ -n "$key_secret" ]; then
  [ -x "$KEY_SWAP" ] || die "swap-upstream-key.sh not found at $KEY_SWAP"
  echo "delegating the credential to swap-upstream-key.sh:"
  "$KEY_SWAP" --create "$key_secret" "$key_file" | sed 's/^/  | /'
fi

tmp="$(mktemp "$destdir/.upstream.route.XXXXXX")"
trap 'rm -f "$tmp"' EXIT INT TERM HUP
ROUTE_BASE="$base" ROUTE_MODEL="$model" ROUTE_EXPECTED="$expected" \
ROUTE_KEY_FILE="$key_file" ROUTE_GENERATION="$generation" \
python3 - "$tmp" <<'PY'
import json, os, sys
row = {"schema": 1,
       "base": os.environ["ROUTE_BASE"],
       "model": os.environ["ROUTE_MODEL"],
       "expected_returned_identity": os.environ["ROUTE_EXPECTED"],
       "generation": int(os.environ["ROUTE_GENERATION"])}
if os.environ.get("ROUTE_KEY_FILE"):
    row["key_file"] = os.environ["ROUTE_KEY_FILE"]
with open(sys.argv[1], "w", encoding="utf-8") as target:
    json.dump(row, target, ensure_ascii=False, sort_keys=True)
    target.write("\n")
    target.flush()
    os.fsync(target.fileno())
PY
chmod 600 "$tmp"
chown "$RUN_USER" "$tmp" 2>/dev/null || true
mv -f "$tmp" "$dest"          # rename(2): atomic, no torn read
trap - EXIT INT TERM HUP

echo "swapped: $dest"
echo "  was:        ${before_base:-<none>} (generation ${before_gen:-<none>})"
echo "  base:       $base"
echo "  model:      $model"
echo "  identity:   $expected"
echo "  key_file:   ${key_file:-<inherits UPSTREAM_API_KEY_FILE>}"
echo "  generation: $generation"
echo "  mode/owner: $(stat -c '%a %U:%G' "$dest")"
echo "  the next upstream request uses this route. No restart needed."
