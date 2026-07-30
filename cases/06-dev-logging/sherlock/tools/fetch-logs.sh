#!/usr/bin/env bash
# fetch-logs.sh — incremental log transport: a remote stand over SSH, or a local
# directory. It brings bytes to this machine and writes a manifest. Nothing else.
#
#   fetch-logs.sh                                  # THE PRODUCT: one tick, config auto-found
#   fetch-logs.sh --check                          # validate config+perms, print redacted plan
#   fetch-logs.sh --probe                          # --check plus ONE connectivity exec
#   fetch-logs.sh --watch --max-ticks 20           # foreground loop; Ctrl-C stops it
#   fetch-logs.sh --source local:./logs --glob '*.logsample'   # no SSH at all, no config needed
#
# Env (all have defaults):
#   SHERLOCK_STAND_CONFIG      config path (resolution step 2)
#   SHERLOCK_WATCH_ROOT        state+output root, default ./artifacts/watch
#   SHERLOCK_SSH_BIN           default `ssh`, resolved BY NAME through PATH — this is the stub
#                              seam: a stub `ssh` earlier on PATH is how tools/tests proves the
#                              exec actually happened, rather than that the file merely exists.
#   SHERLOCK_SSH_TIMEOUT       hard per-exec wall clock, default connect_timeout + 30
#   SHERLOCK_MAX_CONSEC_FAIL   abort --watch after N consecutive failed ticks, default 5.
#                              CAPPED at --max-ticks for a bounded watch, so the abort path
#                              stays reachable for a 1..4-tick run.
#   SHERLOCK_SSH_MULTIPLEX     1 = share ONE authenticated connection per tick (ControlMaster).
#                              Default 0 — the spec forbids a persistent connection; see the
#                              note above the `set -uo pipefail` line.
#   SHERLOCK_ANCHOR_BYTES      overlap re-read that proves the bytes under the cursor are still
#                              the bytes we stored, default 256 (see DEVIATION 3).
#
# Exit codes:
#    0  success. Zero new bytes IS success, not an error.
#    1  usage error: unknown flag, bad flag value, --once with --watch, unbounded --watch on a
#       non-TTY, --print-ssh-argv in local mode, running under xtrace.
#   10  config PARSE error (syntax, unknown section/key, duplicate key, continuation line).
#   11  config SEMANTIC error (missing key, auth-key arithmetic, value fails its regex,
#       password_env names an unset/empty variable).
#   12  PERMISSION refusal: config or identity_file not mode 600/400, not owned by us, not a
#       regular file, unreadable — or `stat` unavailable (FAIL CLOSED).
#   13  config file not found at any resolution step (prints the paste-ready template).
#   20  ssh exec failed (unreachable, auth rejected, host key refused, remote command failed).
#   21  password auth selected but this client is OpenSSH < 8.4 (no SSH_ASKPASS_REQUIRE).
#   22  ssh timed out. `timeout`'s own 124 is TRANSLATED to 22 so 124 never leaks.
#   23  listing unusable: the listing command failed on every form we know, or rc=0 with zero
#       parseable lines AND the directory is missing. Local mode uses this code too: a
#       toolchain that has neither `find -printf` nor `stat -c` nor `stat -f` is a 23, not a 1.
#   24  loop aborted after SHERLOCK_MAX_CONSEC_FAIL consecutive failed ticks, or a bounded
#       --watch in which EVERY tick failed.
#   25  `ssh` binary not found on PATH — the graceful-degradation signal (AGENTS.md R1).
#   30  local-mode source directory missing or unreadable.
#   40  state/output root not writable, or a state write (cursor commit) failed.
#   41  another instance holds the lock.
#   42  this root already belongs to a DIFFERENT source. Two sources sharing one root
#       interleave their bytes into one mirror the model then cites; see DEVIATION 7.
#   2-9, 14-19, 26-29, 31-39, 43+  RESERVED. 124 and 130 are never used.
#
# ---------------------------------------------------------------------------
# This script is a SOURCE implementation: resolve(spec, window) -> local bytes + manifest.
# spec = the [stand] section; window = the per-file byte cursors; output = logs/ + manifest.json.
# It NEVER parses log content. Fetched bytes flow only through BYTE-LEVEL tools:
# cat / redirection / wc -c / wc -l / sha256sum / head -c / tail -c, plus `tail -n 1` used
# ONLY to measure the length of a trailing partial line (DEVIATION 5). grep, awk and sed are
# never applied to fetched content, and nothing ever interprets a log line. Detection is the
# model's job, via SKILL.md. (AGENTS.md R4; REQUIREMENTS.md П3 rejected regex rule engines on
# measured evidence.)
# ---------------------------------------------------------------------------
#
# DECLARED DEVIATIONS from the 2026-07-28 design (`Live stand connector — Flink over SSH`):
#
#   1. `--once` IS THE DEFAULT; the spec's foreground loop survives verbatim as `--watch`.
#      WHY: the spec's loop fed a per-tick rules engine that no longer exists. v6 is a skill
#      driven by a headless `qwen -p` turn, where a default that never returns burns the whole
#      run and produces nothing. `--once` is still accepted explicitly, so the spec's contract
#      text stays literally true.
#   2. COLD START IS TAIL-CAPPED at max_bytes_per_file (default 10 MiB) instead of reading a
#      file from byte 0 on first sighting. WHY: SKILL.md's own binding rule is «Не читай весь
#      корпус, если можешь прочитать нужные 200 строк»; a spec-literal first tick on a multi-GB
#      Flink taskexecutor log saturates the link and blows the model's 8-15 call budget.
#      `--from-start` restores spec-literal behaviour. Never silent: truncated_head + skipped_bytes.
#   3. ROTATION IS DETECTED BY INODE FIRST, size-shrink second, CONTENT ANCHOR THIRD. THIS IS AN
#      ADDITION TO THE SPEC, not a ported clause. WHY: the spec's size-only rule («size < cursor
#      ⇒ reset») does not fire when a file is rotated-and-recreated ABOVE the old cursor; a
#      size-only fetcher then reads from the middle of a DIFFERENT file and emits garbled lines
#      the model will cite with a confident, real-looking `файл:строка`. The inode arrives free
#      in the same listing exec. Where the inode is unusable, the degradation is PER FILE
#      ("inode_tracked": false on that file, "inode_tracking": false on the tick) — one
#      unusable inode must never disable detection for its neighbours.
#      The ANCHOR closes the last hole neither rule can see: a file truncated IN PLACE and
#      regrown past the old cursor keeps its inode and reports a larger size, so it looks
#      exactly like an append. Every append therefore re-fetches the last ANCHOR_BYTES we
#      already hold (in the SAME exec, zero extra connections) and compares sha256 against the
#      mirror's tail. A mismatch means the bytes under the cursor are not the bytes we stored:
#      event "truncated", mirror rolled aside, cursor reset. Cost: ANCHOR_BYTES per grown file.
#   4. NO PER-TICK DETECTION. The spec's «new records -> mask -> rules engine -> RU alert» is not
#      ported: the rules engine was deleted with the logalyzer package, and REQUIREMENTS.md П3
#      rejected it on measured evidence («не сматчил ничего» on a real SSH incident). Parsing
#      content here would also violate AGENTS.md R4. The per-tick RU line is VOLUMETRIC (bytes,
#      files, line ranges) and needs zero content parsing. Detection returns to the model.
#   5. THE MIRROR ENDS ON A LINE BOUNDARY, ALWAYS. A fetched delta is trimmed back to its last
#      newline and the cursor advances only by the bytes kept; the trailing fragment is
#      re-fetched next tick. WHY: `файл:строка` is the deliverable. A mirror ending mid-line
#      makes `wc -l` — and therefore every later line number — off by one, and hands the model a
#      fragment to cite as if it were a record. The inverse guarantee is the contract the model
#      is told: truncated_head=true means the FIRST mirror line may be a fragment; the LAST
#      line never is.
#   6. AN APPEND LARGER THAN max_bytes_per_file KEEPS THE NEWEST BYTES, NOT THE OLDEST. A capped
#      append rolls the mirror aside (the gap is a real discontinuity, so it is represented the
#      same way a rotation is) and sets truncated_head + skipped_bytes. WHY: the incident is at
#      the END of a burst. Handing the model the oldest 10 MiB of a 30 MiB burst while
#      truncated_head says `false` is a silent, confident lie about coverage.
#   7. ONE ROOT BELONGS TO ONE SOURCE, enforced by a stored source fingerprint (exit 42).
#      NOT in the spec, which never considered two sources. WHY: profiles are named after the
#      config file / the literal word `local`, and mirrors are keyed by basename — so two stand
#      configs with the same filename, or two local directories, append into ONE mirror file
#      with no marker, and share one cursor set. The model then cites `app.log:1` for a line
#      that came from the other host.
#
# ALSO OVERRULED, deliberately: the spec's «fallback: sshpass if present». `sshpass -p` puts the
# password in argv, which is world-readable in `ps` for the whole run — the house secrets rule
# forbids it outright. sshpass is never used, never probed for, never suggested. If the stand
# cannot do askpass, the answer is identity_file. See exit 21.
#
# NOT A DEVIATION, recorded because it is expensive and easy to mistake for one: a tick over N
# grown files opens N+1 ssh connections (one listing + one `tail -c` per file), each a full
# authentication. That is the spec, LITERALLY, on both of its clauses — «one `stat`-sizes exec,
# then `tail -c +<offset+1>` per grown file» AND «Reconnect per poll cycle … so no persistent
# connection is ever held» (the stand force-closes idle sessions). ControlMaster/ControlPersist
# would amortise the authentications and is what most log shippers do — and it is precisely the
# persistent connection the spec forbids, so it is OFF by default and pinned off by
# tools/tests/test_fetch_logs.py::TheScriptActuallyRan::test_no_persistent_connection.
# Operators whose stand does NOT auto-logout, and who would otherwise trip sshd MaxStartups or
# fail2ban at poll_seconds=30 with many files, can opt in with SHERLOCK_SSH_MULTIPLEX=1. It is
# an opt-in and it stays one: the default must match the stand the spec was written for.

set -uo pipefail

# WHY GLOBAL and not a `( umask 077; … )` per call site: the run root, the state dir, the
# cursors, the manifest, run.log, listing.txt and the askpass helper are ALL operator data on a
# box with a provisioned guest account. Per-call-site umasks made the permissions depend on
# INVOCATION ORDER — `--probe` before the first real tick left $ROOT and state/ at 0755 and every
# artifact at 0644, and --probe-first is the order the docs recommend. One umask at the top
# cannot be got wrong by adding a call site later.
umask 077

# --------------------------------------------------------------- xtrace refusal
# `set -x` would echo the _PW= assignment and the env-prefixed exec.
case "$-" in
  *x*) printf '\033[31m✗ %s\033[0m\n' \
         "refusing to run under xtrace: it would echo the password assignment." >&2
       exit 1 ;;
esac
case "${SHELLOPTS:-}" in
  *xtrace*) printf '\033[31m✗ %s\033[0m\n' \
              "refusing to run under xtrace: it would echo the password assignment." >&2
            exit 1 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HERE   # referenced only for provenance; the script reads nothing from it
VERSION="6.0"

red()   { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; }
green() { printf '\033[32m✓ %s\033[0m\n' "$*" >&2; }
fail()  { red "$2"; exit "$1"; }

# ------------------------------------------------------------------- defaults
CFG=""
SOURCE_KIND="ssh"
LOCAL_DIR=""
MODE="once"                 # once | watch
POLL_OVERRIDE=""
MAXTICKS_OVERRIDE=""
ROOT="${SHERLOCK_WATCH_ROOT:-./artifacts/watch}"
GLOB_OVERRIDE=""
EXCLUDE_OVERRIDE=""
MAXBYTES_OVERRIDE=""
FROM_START=0
DO_CHECK=0
DO_PROBE=0
PRINT_ARGV=""               # "" | list | fetch
DRY_RUN=0
JSON_OUT=0
QUIET=0
SAW_ONCE=0
SAW_WATCH=0

SSH_BIN="${SHERLOCK_SSH_BIN:-ssh}"
MAX_CONSEC_FAIL="${SHERLOCK_MAX_CONSEC_FAIL:-5}"
SSH_MULTIPLEX="${SHERLOCK_SSH_MULTIPLEX:-0}"
ANCHOR_BYTES="${SHERLOCK_ANCHOR_BYTES:-256}"
GLOB_EXPLICIT=0             # set when --glob or a config file_glob asked for a specific mask
STRICT_EXPLICIT=0           # set when the config actually wrote strict_host_key

# [stand]
c_host=""; c_port="22"; c_user=""
c_identity_file=""; c_password_env=""; c_password=""
c_log_dir="/opt/flink/current/log"
c_file_glob="flink-*-*.log"
c_exclude_glob="*.zip *.out *_db_LOG *.gz"
c_strict_host_key="accept-new"
c_connect_timeout="15"
c_max_bytes_per_file="10485760"
# [watch]
c_poll_seconds="30"
c_max_ticks="0"

AUTH_MODE=""
_PW=""
ASKPASS=""
PROFILE=""
STATE=""
LOGDIR_LOCAL=""
LOCK_FD=""
LOCK_DIR=""
RUNNING=1
CM_DIR=""                   # per-run ControlMaster socket dir (only with SHERLOCK_SSH_MULTIPLEX=1)
SIDE_EFFECTS_OK=0           # 1 once we are past the inert modes and may touch the filesystem
EXCL=()                     # exclude_glob, word-split ONCE with pathname expansion disabled

# -------------------------------------------------------------------- template
# THE PRINTED BLOCK IS A VALID CONFIG FILE, VERBATIM. Not indented, and carrying no shell
# commands: exit 13 exists so the operator does not have to guess the file's shape, and a
# template the script's own parser rejects with exit 10 ("indented line") is worse than no
# template at all. The shell steps are printed SEPARATELY, below the block.
# Regression-guarded by GuardsRefuseTheyDoNotWarn::test_the_printed_template_actually_parses.
print_template() {
  # NOTE: every comment is on its OWN line. An inline `#` is a literal character here,
  # because a password may contain one — so a trailing comment would end up in the value.
  cat <<'TPL'
[stand]
host = your-stand.example.com
user = ops
# preferred; or: identity_file = ~/.ssh/id_ed25519
password_env = SHERLOCK_STAND_PASSWORD
# log_dir = /opt/flink/current/log   <- this is the default
TPL
}

print_next_steps() {
  cat <<'STEPS'

Then (these are shell commands, NOT part of the file above):
  mkdir -p ~/.sherlock && $EDITOR ~/.sherlock/stand.ini && chmod 600 ~/.sherlock/stand.ini
  tools/fetch-logs.sh --probe && tools/fetch-logs.sh
STEPS
}

usage() {
  cat <<'USAGE'
fetch-logs.sh — incremental log transport over SSH (or a local dir). It moves bytes
and writes a manifest; it never reads or interprets log content.

  fetch-logs.sh [--config FILE] [--source ssh|local:DIR]
                [--once | --watch] [--poll SEC] [--max-ticks N]
                [--root DIR] [--glob PAT] [--exclude 'PAT PAT'] [--max-bytes N]
                [--from-start] [--check] [--probe] [--print-ssh-argv[=list|fetch]]
                [--dry-run] [--json] [--quiet] [-h|--help] [--version]

  fetch-logs.sh                                  # one tick, config auto-found
  fetch-logs.sh --check                          # validate config+perms, redacted plan
  fetch-logs.sh --probe                          # --check plus ONE connectivity exec
  fetch-logs.sh --watch --max-ticks 20           # foreground loop; Ctrl-C stops it
  fetch-logs.sh --source local:./logs --glob '*.logsample'   # no SSH, no config

FLAGS (a flag ALWAYS overrides the same-named config key)
  --config FILE        config path; overrides all resolution steps
  --source SPEC        `ssh` (default) or `local:<DIR>` (never execs ssh, needs no config)
  --once               exactly one poll tick, then exit — THIS IS THE DEFAULT
  --watch              foreground poll loop; Ctrl-C stops it. No cron/systemd/launchd unit
  --poll SEC           override [watch] poll_seconds (1..3600)
  --max-ticks N        stop the loop after N ticks (0 = unlimited)
  --root DIR           state+output root, default ./artifacts/watch
  --glob PAT           override [stand] file_glob
  --exclude 'P1 P2'    override [stand] exclude_glob (space-separated)
  --max-bytes N        per-file per-tick byte cap
  --from-start         a file seen for the FIRST time starts at offset 0, not the tail cap
  --check              OFFLINE: parse config, enforce permissions, print a redacted summary
  --probe              --check plus exactly ONE connectivity exec
  --print-ssh-argv[=W] build and print the ssh argv without executing (W = list | fetch)
  --dry-run            full tick, inert: no fetch exec, no cursor advance
  --json               emit the tick manifest to stdout instead of the human RU lines
  --quiet              suppress the human RU lines
  --version            print the version and exit
  -h, --help           this text

ENV (all optional)
  SHERLOCK_STAND_CONFIG   config path            SHERLOCK_WATCH_ROOT     state+output root
  SHERLOCK_SSH_BIN        ssh binary name        SHERLOCK_SSH_TIMEOUT    per-exec wall clock
  SHERLOCK_MAX_CONSEC_FAIL  --watch abort after N failed ticks (capped at --max-ticks)
  SHERLOCK_SSH_MULTIPLEX  1 = one authenticated connection per tick (default 0: the spec
                          forbids a persistent connection — the stand auto-logs-out)
  SHERLOCK_ANCHOR_BYTES   overlap re-read that proves the cursor still points at our bytes

EXIT CODES
   0 success (zero new bytes is success)      20 ssh exec failed
   1 usage error                              21 password auth needs OpenSSH >= 8.4
  10 config parse error                       22 ssh timed out (124 is translated to this)
  11 config semantic error                    23 listing unusable / wrong log_dir
  12 permission refusal (chmod 600)           24 too many failed ticks / every tick failed
  13 config not found                         25 `ssh` not on PATH (degrade gracefully)
                                              30 local source dir missing
                                              40 root or state not writable   41 lock held
                                              42 this root belongs to another source

CONFIG — paste-ready (`~/.sherlock/stand.ini`, chmod 600). The block below IS the file:
USAGE
  print_template
  print_next_steps
}

# ============================================================== argument parsing
while [ $# -gt 0 ]; do
  case "$1" in
    --config)      [ $# -ge 2 ] || fail 1 "--config needs a path"; CFG="$2"; shift 2 ;;
    --config=*)    CFG="${1#--config=}"; shift ;;
    --source)      [ $# -ge 2 ] || fail 1 "--source needs a value"
                   SOURCE_SPEC="$2"; shift 2 ;;
    --source=*)    SOURCE_SPEC="${1#--source=}"; shift ;;
    --once)        SAW_ONCE=1; MODE="once"; shift ;;
    --watch)       SAW_WATCH=1; MODE="watch"; shift ;;
    --poll)        [ $# -ge 2 ] || fail 1 "--poll needs seconds"; POLL_OVERRIDE="$2"; shift 2 ;;
    --poll=*)      POLL_OVERRIDE="${1#--poll=}"; shift ;;
    --max-ticks)   [ $# -ge 2 ] || fail 1 "--max-ticks needs a number"
                   MAXTICKS_OVERRIDE="$2"; shift 2 ;;
    --max-ticks=*) MAXTICKS_OVERRIDE="${1#--max-ticks=}"; shift ;;
    --root)        [ $# -ge 2 ] || fail 1 "--root needs a path"; ROOT="$2"; shift 2 ;;
    --root=*)      ROOT="${1#--root=}"; shift ;;
    --glob)        [ $# -ge 2 ] || fail 1 "--glob needs a pattern"; GLOB_OVERRIDE="$2"; shift 2 ;;
    --glob=*)      GLOB_OVERRIDE="${1#--glob=}"; shift ;;
    --exclude)     [ $# -ge 2 ] || fail 1 "--exclude needs patterns"
                   EXCLUDE_OVERRIDE="$2"; shift 2 ;;
    --exclude=*)   EXCLUDE_OVERRIDE="${1#--exclude=}"; shift ;;
    --max-bytes)   [ $# -ge 2 ] || fail 1 "--max-bytes needs a number"
                   MAXBYTES_OVERRIDE="$2"; shift 2 ;;
    --max-bytes=*) MAXBYTES_OVERRIDE="${1#--max-bytes=}"; shift ;;
    --from-start)  FROM_START=1; shift ;;
    --check)       DO_CHECK=1; shift ;;
    --probe)       DO_CHECK=1; DO_PROBE=1; shift ;;
    --print-ssh-argv)   PRINT_ARGV="list"; shift ;;
    --print-ssh-argv=*) PRINT_ARGV="${1#--print-ssh-argv=}"; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --json)        JSON_OUT=1; shift ;;
    --quiet)       QUIET=1; shift ;;
    --version)     printf 'fetch-logs.sh %s\n' "$VERSION"; exit 0 ;;
    -h|--help)     usage; exit 0 ;;
    --)            shift; break ;;
    *)             fail 1 "unknown flag: $1 (try --help)" ;;
  esac
done

[ $SAW_ONCE -eq 1 ] && [ $SAW_WATCH -eq 1 ] && fail 1 "--once and --watch are mutually exclusive"

if [ -n "${SOURCE_SPEC:-}" ]; then
  case "$SOURCE_SPEC" in
    ssh)      SOURCE_KIND="ssh" ;;
    local:?*) SOURCE_KIND="local"; LOCAL_DIR="${SOURCE_SPEC#local:}" ;;
    *)        fail 1 "bad --source: $SOURCE_SPEC (want 'ssh' or 'local:<DIR>')" ;;
  esac
fi

case "$PRINT_ARGV" in
  ""|list|fetch) : ;;
  *) fail 1 "bad --print-ssh-argv value: $PRINT_ARGV (want 'list' or 'fetch')" ;;
esac
[ -n "$PRINT_ARGV" ] && [ "$SOURCE_KIND" = "local" ] && \
  fail 1 "--print-ssh-argv requires --source ssh"

# REFUSAL GUARD: an unbounded --watch in a non-interactive run hangs the caller's whole turn.
if [ "$MODE" = "watch" ] && [ ! -t 1 ] && [ -z "$MAXTICKS_OVERRIDE" ]; then
  WATCH_NEEDS_BOUND=1
else
  WATCH_NEEDS_BOUND=0
fi

is_uint() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
in_range() { is_uint "$1" && [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]; }

for pair in "POLL_OVERRIDE:$POLL_OVERRIDE:1:3600" "MAXTICKS_OVERRIDE:$MAXTICKS_OVERRIDE:0:1000000" \
            "MAXBYTES_OVERRIDE:$MAXBYTES_OVERRIDE:4096:1099511627776"; do
  IFS=: read -r nm val lo hi <<<"$pair"
  [ -z "$val" ] && continue
  in_range "$val" "$lo" "$hi" || fail 1 "bad value for ${nm%%_OVERRIDE}: $val (want $lo..$hi)"
done

# ================================================================ config parsing
# Exit 13 ALWAYS prints the paste-ready template — a bare "not found" leaves the
# operator to guess the file's shape, which is the whole thing this script must not do.
no_config() { red "$1"; print_template >&2; print_next_steps >&2; exit 13; }

resolve_config() {
  if [ -n "$CFG" ]; then
    [ -f "$CFG" ] || no_config "config not found: $CFG"
    return 0
  fi
  if [ -n "${SHERLOCK_STAND_CONFIG:-}" ] && [ -f "${SHERLOCK_STAND_CONFIG}" ]; then
    CFG="$SHERLOCK_STAND_CONFIG"; return 0
  fi
  if [ -n "${SHERLOCK_STAND_CONFIG:-}" ]; then
    no_config "config not found: $SHERLOCK_STAND_CONFIG (from SHERLOCK_STAND_CONFIG)"
  fi
  [ -f "./sherlock-stand.ini" ] && { CFG="./sherlock-stand.ini"; return 0; }
  [ -f "$HOME/.sherlock/stand.ini" ] && { CFG="$HOME/.sherlock/stand.ini"; return 0; }
  return 1
}

# Hard permission refusal. Regular file, owned by us, mode EXACTLY 600 or 400.
# FAIL CLOSED: if `stat` cannot tell us, we refuse.
check_perms() {
  local f="$1" what="$2" mode owner
  [ -f "$f" ] || fail 12 "$what is not a regular file: $f"
  [ -r "$f" ] || fail 12 "$what is not readable: $f"
  mode="$(stat -c '%a' -- "$f" 2>/dev/null)"
  owner="$(stat -c '%u' -- "$f" 2>/dev/null)"
  if [ -z "$mode" ] || [ -z "$owner" ]; then
    mode="$(stat -f '%OLp' -- "$f" 2>/dev/null)"
    owner="$(stat -f '%u' -- "$f" 2>/dev/null)"
  fi
  [ -n "$mode" ] && [ -n "$owner" ] || \
    fail 12 "cannot stat $what ($f): refusing to continue without a permission check"
  [ "$owner" = "$(id -u)" ] || \
    fail 12 "$what is owned by uid $owner, not by uid $(id -u): $f"
  case "$mode" in
    600|400) : ;;
    *) fail 12 "$what is readable by others: $f (mode $mode). Run: chmod 600 $f" ;;
  esac
}

# STRICT INI SUBSET. This is the input gate; it is where injection dies.
# Never source, never eval, never `declare "$k=$v"`. Values land in PRE-DECLARED
# variables through the `case` dispatch below and nowhere else.
parse_config() {
  local f="$1" section="" line lineno=0 key val rawval seen stripped
  local -A seen_keys=()
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    line="${line%$'\r'}"
    [ -z "$line" ] && continue
    # A line starting with whitespace is either blank (fine) or a continuation
    # attempt (refused: this is a strict INI SUBSET, ambiguity is never guessed).
    case "$line" in
      [$' \t']*)
        stripped="${line#"${line%%[![:space:]]*}"}"
        [ -z "$stripped" ] && continue
        fail 10 "$f:$lineno: indented line (continuations are not supported)" ;;
    esac
    case "$line" in
      '#'*|';'*) continue ;;
      '['*']')
        section="${line#[}"; section="${section%]}"
        case "$section" in
          stand|watch) : ;;
          *) fail 10 "$f:$lineno: unknown section [$section] (only [stand] and [watch])" ;;
        esac
        continue ;;
      '['*) fail 10 "$f:$lineno: malformed section header" ;;
    esac
    case "$line" in
      *=*) : ;;
      *) fail 10 "$f:$lineno: not a 'key = value' line" ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    # trim
    key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"
    val="${val#"${val%%[![:space:]]*}"}"
    # rawval = leading whitespace removed and NOTHING else. It is what an inline
    # `password` gets: trailing whitespace and quote characters are SIGNIFICANT bytes of a
    # secret, and silently dropping them means authenticating with bytes the operator never
    # typed, then reporting «стенд не пустил» — a wrong diagnosis for a parser bug.
    # Documented in fetch-logs.conf.example's GRAMMAR block.
    rawval="$val"
    val="${val%"${val##*[![:space:]]}"}"
    # one optional layer of matching quotes
    case "$val" in
      \'*\') [ ${#val} -ge 2 ] && val="${val:1:${#val}-2}" ;;
      \"*\") [ ${#val} -ge 2 ] && val="${val:1:${#val}-2}" ;;
    esac
    [[ "$key" =~ ^[a-z_][a-z0-9_]*$ ]] || \
      fail 10 "$f:$lineno: bad key name '$key' (want ^[a-z_][a-z0-9_]*\$)"
    [ -n "$section" ] || fail 10 "$f:$lineno: key '$key' outside any section"
    seen="$section.$key"
    [ -n "${seen_keys[$seen]:-}" ] && fail 10 "$f:$lineno: duplicate key '$key' in [$section]"
    seen_keys[$seen]=1
    case "$seen" in
      stand.host)               c_host="$val" ;;
      stand.port)               c_port="$val" ;;
      stand.user)               c_user="$val" ;;
      stand.identity_file)      c_identity_file="$val" ;;
      stand.password_env)       c_password_env="$val" ;;
      stand.password)           c_password="$rawval" ;;
      stand.log_dir)            c_log_dir="$val" ;;
      stand.file_glob)          c_file_glob="$val"; GLOB_EXPLICIT=1 ;;
      stand.exclude_glob)       c_exclude_glob="$val" ;;
      stand.strict_host_key)    c_strict_host_key="$val"; STRICT_EXPLICIT=1 ;;
      stand.connect_timeout)    c_connect_timeout="$val" ;;
      stand.max_bytes_per_file) c_max_bytes_per_file="$val" ;;
      watch.poll_seconds)       c_poll_seconds="$val" ;;
      watch.max_ticks)          c_max_ticks="$val" ;;
      *) fail 10 "$f:$lineno: unknown key '$key' in [$section]" ;;
    esac
  done < "$f"
}

validate_config() {
  # log_dir / file_glob / exclude_glob are validated in BOTH modes: they reach a
  # command string either way.
  [[ "$c_log_dir" =~ ^/[A-Za-z0-9._/-]+$ ]] || \
    fail 11 "log_dir must be an absolute path of [A-Za-z0-9._/-]: '$c_log_dir'"
  case "$c_log_dir" in *..*) fail 11 "log_dir must not contain '..': $c_log_dir" ;; esac
  # NOTE the bracket order: ']' must come FIRST and '-' LAST inside an ERE bracket
  # expression, or the class closes early and the pattern silently means something else.
  local glob_re='^[]A-Za-z0-9._*?[-]+$'
  [[ "$c_file_glob" =~ $glob_re ]] || \
    fail 11 "file_glob must be one path component of [A-Za-z0-9._*?[]-]: '$c_file_glob'"
  case "$c_file_glob" in
    */*|*..*) fail 11 "file_glob must not contain '/' or '..'" ;;
    -*)       fail 11 "file_glob must not start with '-': $c_file_glob" ;;
  esac
  # WORD-SPLIT WITH PATHNAME EXPANSION OFF, exactly once, into EXCL.
  # WHY: `for g in $c_exclude_glob` lets bash glob the PATTERNS against the current working
  # directory. A single `anything.zip` sitting in the CWD made `*.zip` expand to that file's
  # name, so nothing matched any log any more and .zip/.gz binaries were fetched into the
  # evidence mirror; `my report.out` in the CWD instead killed the tool with exit 11, blaming
  # a config value the operator never wrote. The value that passes validation must be the
  # value that is used — so it is split ONCE, here, and every consumer reads the array.
  set -f
  # shellcheck disable=SC2206  # deliberate word split; globbing is disabled by `set -f`
  EXCL=($c_exclude_glob)
  set +f
  local g
  for g in "${EXCL[@]}"; do
    [[ "$g" =~ $glob_re ]] || fail 11 "exclude_glob pattern is not a plain glob: '$g'"
    case "$g" in
      */*|*..*) fail 11 "exclude_glob pattern must not contain '/' or '..': '$g'" ;;
      -*)       fail 11 "exclude_glob pattern must not start with '-': '$g'" ;;
    esac
  done
  in_range "$c_max_bytes_per_file" 4096 1099511627776 || \
    fail 11 "max_bytes_per_file must be an integer >= 4096: '$c_max_bytes_per_file'"
  in_range "$c_poll_seconds" 1 3600 || \
    fail 11 "poll_seconds must be an integer 1..3600: '$c_poll_seconds'"
  in_range "$c_max_ticks" 0 1000000 || \
    fail 11 "max_ticks must be an integer >= 0: '$c_max_ticks'"

  [ "$SOURCE_KIND" = "ssh" ] || return 0

  [ -n "$c_host" ] || fail 11 "[stand] host is required in ssh mode"
  [ -n "$c_user" ] || fail 11 "[stand] user is required in ssh mode"
  case "$c_host" in -*) fail 11 "host must not start with '-': $c_host" ;; esac
  if ! [[ "$c_host" =~ ^[A-Za-z0-9._-]+$ ]] && ! [[ "$c_host" =~ ^\[[0-9A-Fa-f:]+\]$ ]]; then
    fail 11 "host must be a hostname/IPv4 of [A-Za-z0-9._-] or a bracketed IPv6: '$c_host'"
  fi
  # NORMALISE the bracketed IPv6 form the regex above accepts and conf.example documents.
  # OpenSSH does NOT strip the brackets from a positional host (measured on OpenSSH_10.3p1:
  # `ssh -G -- '[2001:db8::1]'` reports `hostname [2001:db8::1]`), and getaddrinfo cannot
  # resolve that — so the documented feature failed 100 % of the time as a nameless exit 20.
  # Brackets exist for `host:port` syntax, which this script never builds: the port is -p.
  case "$c_host" in
    \[*\]) c_host="${c_host#\[}"; c_host="${c_host%\]}"
           [ -n "$c_host" ] || fail 11 "host is an empty IPv6 literal: '[]'" ;;
  esac
  [[ "$c_user" =~ ^[A-Za-z0-9._][A-Za-z0-9._-]{0,31}$ ]] || \
    fail 11 "user is not a plain username: '$c_user'"
  in_range "$c_port" 1 65535 || fail 11 "port must be an integer 1..65535: '$c_port'"
  in_range "$c_connect_timeout" 1 300 || \
    fail 11 "connect_timeout must be an integer 1..300: '$c_connect_timeout'"
  case "$c_strict_host_key" in
    accept-new|yes|no) : ;;
    *) fail 11 "strict_host_key must be accept-new|yes|no: '$c_strict_host_key'" ;;
  esac

  # AUTH KEY ARITHMETIC — deterministic. A silent precedence would be a trap.
  local n=0
  [ -n "$c_identity_file" ] && n=$((n + 1))
  [ -n "$c_password_env" ]  && n=$((n + 1))
  [ -n "$c_password" ]      && n=$((n + 1))
  case "$n" in
    0) [ -n "${SSH_AUTH_SOCK:-}" ] || fail 11 \
         "no auth configured: set one of identity_file | password_env | password in $CFG, or run an ssh-agent"
       AUTH_MODE="agent" ;;
    1) if [ -n "$c_identity_file" ]; then AUTH_MODE="identity"
       elif [ -n "$c_password_env" ]; then AUTH_MODE="password_env"
       else AUTH_MODE="password"; fi ;;
    *) fail 11 "exactly one of identity_file | password_env | password must be set (found $n)" ;;
  esac

  if [ "$AUTH_MODE" = "identity" ]; then
    # shellcheck disable=SC2088  # this is a case PATTERN, not an expansion
    case "$c_identity_file" in
      "~/"*) c_identity_file="$HOME/${c_identity_file#\~/}" ;;
      /*)    : ;;
      *)     fail 11 "identity_file must be absolute or ~/-rooted: '$c_identity_file'" ;;
    esac
    case "$c_identity_file" in *[[:space:]]*) fail 11 "identity_file must not contain whitespace" ;; esac
    [ -e "$c_identity_file" ] || fail 11 "identity_file does not exist: $c_identity_file"
    check_perms "$c_identity_file" "identity_file"
  fi

  if [ "$AUTH_MODE" = "password_env" ]; then
    [[ "$c_password_env" =~ ^[A-Z_][A-Z0-9_]*$ ]] || \
      fail 11 "password_env must be an env var NAME of ^[A-Z_][A-Z0-9_]*\$: '$c_password_env'"
    # Safe only because the name is regex-pinned above.
    _PW="${!c_password_env:-}"
    [ -n "$_PW" ] || fail 11 "password_env names \$$c_password_env, which is unset or empty"
  elif [ "$AUTH_MODE" = "password" ]; then
    _PW="$c_password"
    [ -n "$_PW" ] || fail 11 "password is empty in $CFG"
  fi

  if [ -n "$_PW" ]; then
    # ONE validation rule for BOTH delivery paths. It used to live in the inline-`password`
    # branch only, so `password_env` happily accepted a multi-line value — and the askpass
    # channel is LINE-BASED, so ssh reads the first line and authenticates with a truncated
    # secret. That surfaces as exit 20 «стенд не пустил»: a wrong diagnosis for a secret the
    # tool itself mangled. Any control byte can end or reposition that line, so all of them
    # are refused, not just \n.
    case "$_PW" in
      *[[:cntrl:]]*) fail 11 "the password must not contain a control character (newline, tab, CR, …): the SSH_ASKPASS channel is one line, so ssh would authenticate with a truncated secret and the stand would look like it refused you" ;;
    esac
    # A password may NEVER be offered to an unverified host key. `accept-new` is
    # trust-on-first-use, and this script keeps known_hosts PER ROOT — so "first use" recurs
    # on every fresh root/CWD and the secret goes to whatever host answers on that connect.
    # With a key the worst case of a MITM is a failed handshake; with a password it is a
    # harvested credential.
    #   - explicit `no` / `accept-new` + password  -> REFUSED (they asked for the unsafe thing)
    #   - default (nothing written)                -> silently upgraded to `yes`, and the
    #     per-root known_hosts is SEEDED from ~/.ssh/known_hosts (read-only) so the trust the
    #     operator already accumulated still counts. Unknown host then fails the handshake
    #     with a named reason instead of leaking the secret.
    if [ "$STRICT_EXPLICIT" -eq 1 ] && [ "$c_strict_host_key" != "yes" ]; then
      fail 11 "strict_host_key = $c_strict_host_key cannot be combined with password auth: the password would be offered to an unverified host key. Either drop the key (the default becomes 'yes' for password auth) and pre-seed the host key with: ssh-keyscan -p $c_port $c_host >> ~/.ssh/known_hosts — or switch to identity_file = ~/.ssh/id_ed25519, where an unknown host costs a failed handshake and not a credential"
    fi
    c_strict_host_key="yes"
  fi
}

# ================================================================== small utils
# Single-quote for a remote/local shell command string. Nothing else ever builds
# a command fragment.
shq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

json_str() {
  # Escape a bash string as a JSON string body. Applied to paths and names only —
  # never to fetched log content, which this script does not read.
  #
  # EVERY C0 control byte is escaped, not just the five with short forms. A remote FILENAME is
  # attacker-controlled data (whoever can write to log_dir chooses it), and a single raw 0x1b in
  # a name made manifest.json invalid JSON — while SKILL.md tells the model the manifest IS its
  # map. Unparseable map => no map. 0x00 cannot occur: bash cannot hold it in a variable.
  local s="$1" n ch esc
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  s="${s//$'\t'/\\t}"; s="${s//$'\r'/\\r}"; s="${s//$'\n'/\\n}"
  s="${s//$'\b'/\\b}"; s="${s//$'\f'/\\f}"
  case "$s" in
    *[[:cntrl:]]*)
      for n in 1 2 3 4 5 6 7 11 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 127; do
        # `%b` keeps the FORMAT string constant and puts the octal escape in the ARGUMENT,
        # where a code point belongs (the `printf "\\$(…)"` spelling is SC2059).
        ch="$(printf '%b' "\\0$(printf '%03o' "$n")")"
        [ -n "$ch" ] || continue
        case "$s" in
          *"$ch"*) printf -v esc '\\u%04x' "$n"; s="${s//"$ch"/$esc}" ;;
        esac
      done ;;
  esac
  printf '%s' "$s"
}

has_ctrl() { case "$1" in *[[:cntrl:]]*) return 0 ;; *) return 1 ;; esac; }

safe_disp() {
  # A name that reaches a TERMINAL or run.log. A remote-controlled basename carrying
  # \033[2J clears the operator's screen, and run.log is read back by the agent as evidence —
  # so it is an injection channel into the model's context too. Control bytes are dropped for
  # DISPLAY only; the manifest keeps the escaped truth.
  printf '%s' "$1" | tr -d '[:cntrl:]'
}

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

path_key() { printf '%s' "$1" | sha1sum | cut -d' ' -f1; }

# ============================================================ ssh argv building
SSH_ARGV=()
KNOWN_HOSTS_SEEDED=0
seed_known_hosts() {
  # The per-root known_hosts is the right isolation (this script is not entitled to EDIT the
  # operator's ~/.ssh/known_hosts — pinned by ArgvCarriesTheConfiguredValues). But starting it
  # EMPTY throws away the trust the operator already has, which is what made
  # StrictHostKeyChecking=yes unusable and accept-new tempting. So: copy ~/.ssh/known_hosts in
  # ONCE, read-only, on first use. Their file is never written, never passed to ssh.
  # --print-ssh-argv and a bare --check are documented as touching nothing, so they never seed.
  [ "$SIDE_EFFECTS_OK" -eq 1 ] || return 0
  [ "$KNOWN_HOSTS_SEEDED" -eq 1 ] && return 0
  KNOWN_HOSTS_SEEDED=1
  [ -n "$STATE" ] || return 0
  [ -e "$STATE/known_hosts" ] && return 0
  [ -r "$HOME/.ssh/known_hosts" ] || return 0
  ( umask 077; mkdir -p "$STATE" ) 2>/dev/null || return 0
  cat "$HOME/.ssh/known_hosts" > "$STATE/known_hosts" 2>/dev/null || return 0
  return 0
}

build_ssh_argv() {
  local remote_cmd="$1"
  seed_known_hosts
  SSH_ARGV=(-T -n
            -o "ConnectTimeout=$c_connect_timeout"
            -o "StrictHostKeyChecking=$c_strict_host_key"
            -o "UserKnownHostsFile=$STATE/known_hosts"
            -o LogLevel=ERROR)
  # DEFAULT: no persistent connection, per the spec's transport clause (the stand
  # force-closes idle sessions). SHERLOCK_SSH_MULTIPLEX=1 is the documented opt-out for
  # operators whose stand does not auto-logout and who would otherwise pay one full password
  # authentication per grown file per tick.
  if [ "$SSH_MULTIPLEX" = "1" ] && [ -n "$CM_DIR" ]; then
    SSH_ARGV+=(-o ControlMaster=auto -o "ControlPath=$CM_DIR/s"
               -o "ControlPersist=$c_connect_timeout")
  else
    SSH_ARGV+=(-o ControlMaster=no -o ControlPath=none)
  fi
  # WHY (2026-07-30): BatchMode=yes disables ALL user interaction, and that includes
  # SSH_ASKPASS — a password run with BatchMode=yes fails 100 % of the time.
  # NumberOfPasswordPrompts=1 makes a wrong password cost one round trip, not three.
  case "$AUTH_MODE" in
    identity)
      SSH_ARGV+=(-o BatchMode=yes -o PreferredAuthentications=publickey -o IdentitiesOnly=yes) ;;
    agent)
      SSH_ARGV+=(-o BatchMode=yes -o PreferredAuthentications=publickey) ;;
    password_env|password)
      # shellcheck disable=SC2054  # the comma is inside one ssh option VALUE
      SSH_ARGV+=(-o BatchMode=no
                 -o PreferredAuthentications=password,keyboard-interactive
                 -o NumberOfPasswordPrompts=1) ;;
  esac
  SSH_ARGV+=(-p "$c_port")
  [ "$AUTH_MODE" = "identity" ] && SSH_ARGV+=(-i "$c_identity_file")
  # `--` before the host is belt-and-braces; a leading '-' is already refused at exit 11.
  SSH_ARGV+=(-l "$c_user" -- "$c_host" "$remote_cmd")
}

# The password NEVER reaches disk: the helper body references the env var, and the
# value lives only in process memory and in the child's environment.
write_askpass() {
  local d="$STATE"
  ( umask 077; mkdir -p "$d" ) || fail 40 "cannot create state dir: $d"
  ASKPASS="$d/askpass-$$.sh"
  ( umask 077; cat > "$ASKPASS" <<'ASK'
#!/bin/sh
printf '%s\n' "$SHERLOCK_ASKPASS_SECRET"
ASK
  ) || fail 40 "cannot write askpass helper"
  chmod 700 "$ASKPASS" || fail 40 "cannot chmod askpass helper"
}

cm_setup() {
  # A unix socket path is capped at ~108 bytes, and $STATE can be arbitrarily deep — so the
  # socket lives in a SHORT mktemp dir, never under the run root. If mktemp fails we simply
  # never multiplex; ControlMaster=auto also degrades on its own if the socket is unusable.
  [ "$SSH_MULTIPLEX" = "1" ] || return 0
  command -v mktemp >/dev/null 2>&1 || { SSH_MULTIPLEX=0; return 0; }
  CM_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sherlock-cm-XXXXXX" 2>/dev/null)" || CM_DIR=""
  [ -n "$CM_DIR" ] || SSH_MULTIPLEX=0
  return 0
}

cm_teardown() {
  [ -n "$CM_DIR" ] || return 0
  if [ -S "$CM_DIR/s" ] && command -v "$SSH_BIN" >/dev/null 2>&1; then
    "$SSH_BIN" -o "ControlPath=$CM_DIR/s" -O exit -- "$c_host" >/dev/null 2>&1
  fi
  rm -rf "$CM_DIR" 2>/dev/null
  CM_DIR=""
  return 0
}

cleanup() {
  [ -n "$ASKPASS" ] && rm -f "$ASKPASS"
  _PW=""
  unset SHERLOCK_ASKPASS_SECRET
  cm_teardown
  [ -n "$LOCK_DIR" ] && rmdir "$LOCK_DIR" 2>/dev/null
  return 0
}
trap cleanup EXIT
trap 'RUNNING=0' INT TERM HUP

# Preflight the client BEFORE any connection: turns the spec's open question about
# SSH_ASKPASS_REQUIRE into a named error instead of a hang.
ssh_preflight() {
  command -v "$SSH_BIN" >/dev/null 2>&1 || \
    fail 25 "'$SSH_BIN' not found on PATH — cannot fetch from a remote stand (this is not fatal to the skill)"
  case "$AUTH_MODE" in
    password_env|password) : ;;
    *) return 0 ;;
  esac
  local v maj min
  v="$("$SSH_BIN" -V 2>&1 | head -1)"
  maj="$(printf '%s' "$v" | sed -n 's/.*OpenSSH_\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1/p')"
  min="$(printf '%s' "$v" | sed -n 's/.*OpenSSH_\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\2/p')"
  if [ -n "$maj" ] && [ -n "$min" ]; then
    if [ "$maj" -lt 8 ] || { [ "$maj" -eq 8 ] && [ "$min" -lt 4 ]; }; then
      fail 21 "password auth needs OpenSSH >= 8.4 (SSH_ASKPASS_REQUIRE=force); this client is $v. Set identity_file = … in ${CFG:-the config} instead."
    fi
  fi
  return 0
}

SSH_TIMEOUT="${SHERLOCK_SSH_TIMEOUT:-}"

# WHY: bash does NOT mark an `exec {VAR}>file` descriptor close-on-exec (measured
# 2026-07-30 on bash 5.3.3). Without this, a hung ssh inherits the flock FD and keeps
# holding the lock after we die — every later run then fails with a spurious exit 41.
# Called inside the subshell that wraps each exec, so the parent keeps its lock.
close_lock_fd() { [ -n "$LOCK_FD" ] && exec {LOCK_FD}>&-; return 0; }

# The last line ssh itself printed. WHY THIS EXISTS: every exec used to end in `2>/dev/null`,
# so «Permission denied (publickey,password)», «Host key verification failed» and
# «No such file or directory» were destroyed at the door — an unreachable stand produced a
# header line, an EMPTY run.log and nothing else, and the operator had exit 20 with no
# diagnosis. The password cannot appear here: the secret only ever travels through the askpass
# child's stdout, never ssh's stderr, and SecretContainment walks every byte under the root.
SSH_ERR_LAST=""
# The RAW status, kept alongside the normalised 0|20|22. ssh reports its OWN failures as 255
# and otherwise passes the remote command's status through — so the listing path can tell
# "the stand did not let me in" (retrying another `find` form is pointless and costs another
# authentication) from "this `find` form is not supported here" (try the next form).
SSH_RAW_RC=0
ssh_err_capture() {
  local f="$1"
  SSH_ERR_LAST=""
  [ -s "$f" ] || return 0
  # tail -n 1 on ssh's OWN stderr, not on fetched content.
  SSH_ERR_LAST="$(safe_disp "$(tail -n 1 "$f" 2>/dev/null)")"
  return 0
}

# Run one ssh exec. stdout goes wherever the caller redirects it.
# Returns: 0 ok, 22 timeout, 20 anything else.
ssh_exec() {
  local remote_cmd="$1" rc
  build_ssh_argv "$remote_cmd"
  local t="${SSH_TIMEOUT:-$((c_connect_timeout + 30))}"
  local errf="/dev/null"
  [ -n "$STATE" ] && [ -d "$STATE" ] && errf="$STATE/ssh.err"
  local -a runner=()
  command -v timeout >/dev/null 2>&1 && runner=(timeout -k 5 "$t")
  case "$AUTH_MODE" in
    password_env|password)
      # WHY: a command-prefix assignment is NOT argv. /proc/<pid>/environ is mode 400,
      # owner-only; argv via `ps` is world-readable for the whole run — the same rule
      # already commented at eval/run.sh:54-56 ("this box has a provisioned guest
      # account"). Do NOT copy verify.sh:51-54, which puts a key on argv.
      # DISPLAY= is empty on purpose: measured 2026-07-30 on OpenSSH_10.3p1, `force`
      # works without it, and an empty DISPLAY also prevents a real X askpass GUI.
      # SSH_AUTH_SOCK= is empty so a loaded agent key cannot silently succeed and mask
      # a broken password path (which would then fail only on the operator's stand).
      # setsid -w removes the controlling terminal, so a client that ignores
      # ASKPASS_REQUIRE cannot fall back to a TTY prompt and hang.
      local -a pre=()
      command -v setsid >/dev/null 2>&1 && pre=(setsid -w)
      # ORDER MATTERS: setsid FIRST, timeout SECOND. The other way round, `timeout`
      # signals `setsid` and the real ssh is orphaned — it keeps the stdout pipe open
      # and the caller hangs forever waiting for EOF, which is the exact failure the
      # timeout exists to prevent. Measured 2026-07-30 with a hanging stub.
      # shellcheck disable=SC1007  # DISPLAY= / SSH_AUTH_SOCK= are DELIBERATELY empty
      ( close_lock_fd
        SSH_ASKPASS="$ASKPASS" SSH_ASKPASS_REQUIRE=force \
        SHERLOCK_ASKPASS_SECRET="$_PW" DISPLAY= SSH_AUTH_SOCK= \
          "${pre[@]}" "${runner[@]}" "$SSH_BIN" "${SSH_ARGV[@]}" </dev/null 2>"$errf" )
      rc=$? ;;
    *)
      ( close_lock_fd
        "${runner[@]}" "$SSH_BIN" "${SSH_ARGV[@]}" </dev/null 2>"$errf" )
      rc=$? ;;
  esac
  [ "$errf" = "/dev/null" ] || ssh_err_capture "$errf"
  SSH_RAW_RC="$rc"
  # `timeout`'s own 124 is translated so it never leaks out of this script.
  [ "$rc" -eq 124 ] && return 22
  [ "$rc" -eq 0 ] && return 0
  return 20
}

# The transport's reason, when it has one. English on stderr (house style), and the same
# sentence in run.log — the sink that survives the terminal closing.
name_transport_failure() {
  local what="$1" rc="$2"
  if [ -n "$SSH_ERR_LAST" ]; then
    red "$what (rc=$rc): $SSH_ERR_LAST"
  else
    red "$what (rc=$rc)"
  fi
  return 0
}

# ================================================== the two transport functions
# THESE TWO ARE THE ONLY PLACES THAT DISPATCH ON TRANSPORT. That pair IS the
# swappable seam AGENTS.md R4 demands. The listing command string is BYTE-IDENTICAL
# between ssh and local mode, so the local arm exercises the real command string.
listing_cmd() {
  printf 'find %s -maxdepth 1 -type f -name %s -printf %s' \
    "$(shq "$1")" "$(shq "$c_file_glob")" "$(shq '%s\t%i\t%p\n')"
}
listing_cmd_fallback() {
  printf 'find %s -maxdepth 1 -type f -name %s -exec stat -c %s -- {} +' \
    "$(shq "$1")" "$(shq "$c_file_glob")" "$(shq '%s %i %n')"
}
# THIRD FORM, for BSD/macOS/busybox: neither `find -printf` nor `stat -c` exists there. Local
# mode is advertised as the always-available no-SSH path, and check_perms already carries a BSD
# `stat -f` fallback — so the listing must too, or the advertised path is dead on a Mac with no
# message at all.
listing_cmd_bsd() {
  printf 'find %s -maxdepth 1 -type f -name %s -exec stat -f %s -- {} +' \
    "$(shq "$1")" "$(shq "$c_file_glob")" "$(shq '%z %i %N')"
}
fetch_cmd() {
  local path="$1" off="$2" cap="$3"
  printf 'tail -c +%s -- %s | head -c %s' "$((off + 1))" "$(shq "$path")" "$cap"
}

LISTING_FALLBACK=0
# WHY a FILE and not `out="$(…)"`: command substitution waits for EOF on the pipe,
# not for the child to exit. A hung-then-killed ssh can leave a grandchild holding
# that pipe, and the caller then blocks forever — defeating the very timeout meant
# to prevent a hang. Measured 2026-07-30. Every exec in this script redirects to a
# file for the same reason.
# Run ONE listing form. Local mode's raw shell status is normalised to the same 0|20|22
# vocabulary ssh_exec speaks, so the caller never has to know which transport it used — a
# local listing failure used to escape as the raw `1`, the code the header reserves for
# "usage error: unknown flag", with an empty stderr.
run_listing() {
  local cmd="$1" out="$2" rc
  if [ "$SOURCE_KIND" = "local" ]; then
    local errf="/dev/null"
    [ -n "$STATE" ] && [ -d "$STATE" ] && errf="$STATE/ssh.err"
    ( close_lock_fd; /bin/sh -c "$cmd" > "$out" 2>"$errf" ); rc=$?
    [ "$errf" = "/dev/null" ] || ssh_err_capture "$errf"
    SSH_RAW_RC="$rc"
    [ "$rc" -eq 124 ] && return 22
    [ "$rc" -eq 0 ] && return 0
    return 20
  fi
  ssh_exec "$cmd" > "$out"
}

list_files() {   # $1 = outfile, gets `size \t inode \t path`   (rc: 0 | 20 | 22 | 23)
  local out="$1" dir rc l x
  if [ "$SOURCE_KIND" = "local" ]; then dir="$LOCAL_DIR"; else dir="$c_log_dir"; fi
  : > "$out"
  run_listing "$(listing_cmd "$dir")" "$out"; rc=$?
  [ "$rc" -eq 0 ] && return 0
  # A TIMEOUT is a transport verdict, not a toolchain verdict: retrying two more forms would
  # just spend two more timeouts against a stand that is not answering.
  [ "$rc" -eq 22 ] && return 22
  # Likewise 255: ssh itself failed (unreachable, auth rejected, host key refused). Another
  # `find` form cannot help, and each attempt is another full password authentication.
  [ "$SOURCE_KIND" = "ssh" ] && [ "$SSH_RAW_RC" -eq 255 ] && return 20

  local form
  for form in fallback bsd; do
    LISTING_FALLBACK=1
    : > "$out"
    case "$form" in
      fallback) run_listing "$(listing_cmd_fallback "$dir")" "$out"; rc=$? ;;
      bsd)      run_listing "$(listing_cmd_bsd "$dir")" "$out"; rc=$? ;;
    esac
    [ "$rc" -eq 22 ] && return 22
    [ "$rc" -ne 0 ] && continue
    # `stat -c '%s %i %n'` / `stat -f '%z %i %N'` are space-delimited; normalise to the tab
    # shape the caller reads.
    : > "$out.tmp"
    while IFS= read -r l; do
      [ -z "$l" ] && continue
      x="${l#* }"
      printf '%s\t%s\t%s\n' "${l%% *}" "${x%% *}" "${x#* }" >> "$out.tmp"
    done < "$out"
    mv -f "$out.tmp" "$out"
    return 0
  done
  # Every form we know failed. That is "listing unusable" (23), never a usage error (1).
  return 23
}

fetch_range() {  # $1 path  $2 offset  $3 cap  $4 outfile   (rc: 0 | 20 | 22)
  local cmd rc errf; cmd="$(fetch_cmd "$1" "$2" "$3")"
  if [ "$SOURCE_KIND" = "local" ]; then
    errf="/dev/null"
    [ -n "$STATE" ] && [ -d "$STATE" ] && errf="$STATE/ssh.err"
    ( close_lock_fd; /bin/sh -c "$cmd" > "$4" 2>"$errf" ); rc=$?
    [ "$errf" = "/dev/null" ] || ssh_err_capture "$errf"
    SSH_RAW_RC="$rc"
    [ "$rc" -eq 124 ] && return 22
    [ "$rc" -eq 0 ] || return 20
    return 0
  fi
  ssh_exec "$cmd" > "$4"
}

# =================================================================== the tick
matches_exclude() {
  local base="$1" pat
  # EXCL was split ONCE in validate_config with pathname expansion disabled. Iterating
  # `$c_exclude_glob` here re-globbed the patterns against the CWD on every single file.
  for pat in "${EXCL[@]}"; do
    # shellcheck disable=SC2053   # intentional glob match, not a string compare
    [[ "$base" == $pat ]] && return 0
  done
  return 1
}

acquire_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec {LOCK_FD}>"$STATE/.lock" || fail 40 "cannot create lock file in $STATE"
    flock -n "$LOCK_FD" || fail 41 "another fetch-logs.sh instance holds $STATE/.lock"
  else
    LOCK_DIR="$STATE/.lock.d"
    mkdir "$LOCK_DIR" 2>/dev/null || { LOCK_DIR=""; fail 41 "another instance holds $STATE/.lock.d"; }
  fi
}

MF_FILES=()
MF_SKIPPED=()
MF_ERRORS=()
T_FILES=0; T_FETCHED=0; T_BYTES=0; T_ROTATED=0; T_FAILED=0
T_FAIL_RC=0          # the transport code of the first per-file failure (22 wins over 20)
INODE_TRACKING=1
RU_LINES=()

ru() { RU_LINES+=("$1"); printf '%s\n' "$1" >> "$ROOTP/run.log"; }

group_digits() {
  # 38702 -> "38 702" (non-breaking space is the house style for numbers in RU prose,
  # but run.log stays plain ASCII-safe: a regular space is used).
  printf '%s' "$1" | rev | sed 's/\([0-9]\{3\}\)/\1 /g' | rev | sed 's/^ *//'
}

# THE CURSOR COMMIT IS THE ONE WRITE THAT MUST NOT FAIL SILENTLY. It used to be the only
# unchecked write in the script: with a read-only or full state dir the tick still exited 0
# while leaking raw `bash: …: Permission denied` and `mv: cannot stat` to stderr, and every
# later tick re-fetched and re-appended the SAME bytes — so the mirror filled with duplicated
# lines the model reads as a real burst. A state-write failure is exit 40 per the header.
# Returns non-zero so the caller can `|| continue` and leave the file out of the manifest's
# success set.
commit_cursor() {
  local ckey="$1" off="$2" ino="$3" sz="$4" rp="$5" dsp="$6"
  # The SUBSHELL is load-bearing: a failing `> file` redirection is reported by the SHELL, not
  # by `printf`, so `printf … > file 2>/dev/null` still leaked
  # «fetch-logs.sh: line N: …: Permission denied» plus a follow-up `mv: cannot stat` into the
  # operator's terminal and into the agent's context. Inside a subshell the message is the
  # subshell's stderr and can be replaced with a named error.
  if ( printf '%s\t%s\t%s\t%s\n' "$off" "$ino" "$sz" "$rp" > "$STATE/cursors/$ckey.tmp" ) 2>/dev/null \
     && mv -f "$STATE/cursors/$ckey.tmp" "$STATE/cursors/$ckey" 2>/dev/null; then
    return 0
  fi
  rm -f "$STATE/cursors/$ckey.tmp" 2>/dev/null
  MF_ERRORS+=("$(json_str "cannot commit cursor for $rp")")
  T_FAILED=$((T_FAILED + 1))
  if [ "$T_FAIL_RC" -eq 0 ]; then T_FAIL_RC=40; fi
  red "cannot commit cursor for $(safe_disp "$rp") — state dir not writable: $STATE/cursors"
  ru "ошибка: $dsp — курсор не сохранён (каталог состояния не пишется); следующий заход принесёт те же байты заново"
  return 1
}

# Rewrite $1 as a byte SLICE of itself. Same subshell discipline as commit_cursor, and for a
# stronger reason: if the slice silently fails, the caller goes on to append the UNSLICED part —
# duplicating the anchor bytes into the mirror, or leaving it ending mid-line. Both are the
# fabricated-citation class, so a failure here fails the file.
#   slice_part <file> head <n>   -> keep the first n bytes
#   slice_part <file> tail <n>   -> drop the first n bytes
slice_part() {
  local f="$1" how="$2" n="$3"
  case "$how" in
    head) ( head -c "$n" "$f" > "$f.new" ) 2>/dev/null || { rm -f "$f.new"; return 1; } ;;
    tail) ( tail -c +$((n + 1)) "$f" > "$f.new" ) 2>/dev/null || { rm -f "$f.new"; return 1; } ;;
    *)    return 1 ;;
  esac
  mv -f "$f.new" "$f" 2>/dev/null || { rm -f "$f.new"; return 1; }
  return 0
}

# The map a FAILED tick must still hand the model. WHY: manifest.json used to be rewritten with
# `"files":[]` whenever the listing failed, while the previously fetched logs were still sitting
# in logs/ — so SKILL.md's «манифест — это твоя карта … карту заново не строй» and «ненулевой код
# возврата не отменяет отчёт … разбирай то, что уже забрано» could not both be obeyed. The model
# read an empty map and reported that there were no logs. The cursors already know every file we
# ever fetched, so they are the map: each becomes event "stale" — on disk, not refreshed this
# tick.
stale_files_from_cursors() {
  local cf coff cino csize cpath mirror sz
  for cf in "$STATE/cursors"/*; do
    [ -f "$cf" ] || continue
    IFS=$'\t' read -r coff cino csize cpath < "$cf"
    [ -n "${cpath:-}" ] || continue
    is_uint "${coff:-}" || coff=0
    mirror="$LOGDIR_LOCAL/${cpath##*/}"
    sz=0
    [ -f "$mirror" ] && sz="$(wc -c < "$mirror" 2>/dev/null | tr -d ' ')"
    is_uint "${sz:-}" || sz=0
    MF_FILES+=("$(file_json "$cpath" "$mirror" "${cino:-0}" "${csize:-0}" "$coff" "$coff" 0 \
                   0 "$(count_lines "$mirror")" "" false false 0 "stale" true)")
  done
  return 0
}

collect_tick() {
  MF_FILES=(); MF_SKIPPED=(); MF_ERRORS=(); RU_LINES=()
  T_FILES=0; T_FETCHED=0; T_BYTES=0; T_ROTATED=0; T_FAILED=0; T_FAIL_RC=0
  LISTING_FALLBACK=0
  # PER TICK, not per process. INODE_TRACKING is the tick-wide summary of a PER-FILE property;
  # leaving it latched meant one file with an unusable inode disabled rotation detection for
  # every other file, in that tick and in every tick afterwards — the exact garbled mid-file
  # read DEVIATION 3 exists to prevent.
  INODE_TRACKING=1

  local started; started="$(now_utc)"
  local t0; t0="$(date +%s)"
  local tick; tick="$(cat "$STATE/tick" 2>/dev/null || echo 0)"
  is_uint "$tick" || tick=0
  tick=$((tick + 1))

  local listing rc
  listing="$STATE/listing.txt"
  list_files "$listing"; rc=$?
  if [ "$rc" -ne 0 ]; then
    MF_ERRORS+=("$(json_str "listing failed (rc=$rc)${SSH_ERR_LAST:+: $SSH_ERR_LAST}")")
    T_FAILED=$((T_FAILED + 1))
    # Named on BOTH sinks, like the per-file fetch failure already was. A bare exit 20 with
    # «итого тик N: 0 файл(ов)» is indistinguishable from a clean no-change tick.
    if [ "$SOURCE_KIND" = "local" ]; then
      name_transport_failure "listing failed: $(safe_disp "$LOCAL_DIR")" "$rc"
    else
      name_transport_failure "listing failed: $c_user@$c_host:$c_port $(safe_disp "$c_log_dir")" "$rc"
    fi
    ru "ошибка: не удалось получить список файлов (код $rc)${SSH_ERR_LAST:+ — $SSH_ERR_LAST}"
    stale_files_from_cursors
    ru "итого тик $tick: 0 файл(ов), 0 Б (список не получен; в logs/ лежит то, что забрано раньше)"
    write_manifest "$tick" "$started" "$t0"
    return "$rc"
  fi
  if [ ! -s "$listing" ]; then
    # Directory present but empty of matches is SUCCESS. Directory missing is 23.
    local probe_rc=0
    if [ "$SOURCE_KIND" = "local" ]; then
      [ -d "$LOCAL_DIR" ] || probe_rc=1
    else
      ssh_exec "test -d $(shq "$c_log_dir")" >/dev/null 2>&1 || probe_rc=1
    fi
    if [ "$probe_rc" -ne 0 ]; then
      MF_ERRORS+=("$(json_str "log_dir is missing or unlistable: ${c_log_dir}")")
      write_manifest "$tick" "$started" "$t0"
      return 23
    fi
  fi

  local -A seen_paths=()
  local size inode rpath base disp ckey coff cino csize
  local n=0
  while IFS=$'\t' read -r size inode rpath; do
    [ -z "${rpath:-}" ] && continue
    is_uint "$size" || continue
    base="${rpath##*/}"
    # INPUT GATE on a remote-controlled NAME. A basename carrying a raw control byte is an
    # ANSI-escape channel into the operator's terminal and into run.log — which the agent reads
    # back as evidence — and it would become the local mirror's filename. Refused here, and
    # recorded, so the coverage discipline still sees the file instead of it silently vanishing.
    if has_ctrl "$base"; then
      MF_SKIPPED+=("{\"name\":\"$(json_str "$base")\",\"reason\":\"unsafe_name\"}")
      ru "пропущен: $(safe_disp "$base") — в имени управляющие символы (не забираем)"
      continue
    fi
    if matches_exclude "$base"; then
      MF_SKIPPED+=("{\"name\":\"$(json_str "$base")\",\"reason\":\"exclude_glob\"}")
      continue
    fi
    disp="$base"
    T_FILES=$((T_FILES + 1))
    seen_paths["$rpath"]=1
    n=$((n + 1))

    ckey="$(path_key "$rpath")"
    coff=0; cino=""; csize=""
    if [ -f "$STATE/cursors/$ckey" ]; then
      IFS=$'\t' read -r coff cino csize _ < "$STATE/cursors/$ckey"
      is_uint "${coff:-}" || coff=0
    else
      cino=""
    fi
    # PER FILE. A zero/unparseable inode disables the inode rule for THIS file only; the tick
    # summary records that it happened, but the neighbours keep their detection.
    local inode_ok=true
    if is_uint "${inode:-}" && [ "${inode:-0}" -gt 0 ]; then :; else
      inode_ok=false; INODE_TRACKING=0; inode=0
    fi

    local event="" from=0 cap="$c_max_bytes_per_file" trunc_head=false skipped_head=0
    local rotated=false
    local mirror="$LOGDIR_LOCAL/$base"

    if [ -z "$cino" ] && [ ! -f "$STATE/cursors/$ckey" ]; then
      event="new"
    elif [ "$inode_ok" = true ] && [ -n "$cino" ] && is_uint "$cino" && \
         [ "$cino" -gt 0 ] && [ "$inode" -ne "$cino" ]; then
      event="rotated"; rotated=true
    elif [ "$size" -lt "$coff" ]; then
      event="truncated"
    elif [ -n "$csize" ] && is_uint "$csize" && [ "$size" -lt "$csize" ]; then
      # Same inode, smaller than LAST TICK's size but still above our cursor: the file was
      # rewritten under us. Neither the inode rule nor «size < cursor» sees this one.
      event="truncated"
    elif [ "$size" -gt "$coff" ]; then
      event="append"; from="$coff"
    else
      event="unchanged"; from="$coff"
    fi

    # A file we are reading from byte 0 — first sighting, rotated, or truncated — gets the
    # DEVIATION 2 cold-start rule: take the TAIL, not a multi-GB prefix. Applying it to
    # rotated/truncated too is not cosmetic: a rotated file that is ALREADY larger than the cap
    # used to be read from its oldest byte with truncated_head silently false.
    case "$event" in
      new|rotated|truncated)
        if [ "$FROM_START" -eq 1 ] || [ "$size" -le "$cap" ]; then
          from=0
        else
          from=$((size - cap)); trunc_head=true; skipped_head=$from
        fi ;;
    esac

    # THE RESET IS A MUTATION, so it happens only when we are allowed to mutate. --dry-run is
    # documented as «full tick, inert»; it used to rename the live mirror out from under the
    # model on any rotation, so the file every earlier report cited simply vanished — and then
    # printed «курсор сброшен» while the cursor had not moved.
    if [ "$event" = "rotated" ] || [ "$event" = "truncated" ]; then
      if [ "$DRY_RUN" -eq 0 ]; then
        roll_mirror "$mirror"
        [ "$event" = "rotated" ] && T_ROTATED=$((T_ROTATED + 1))
      fi
    fi

    local want=$((size - from))
    [ "$want" -lt 0 ] && want=0

    local first_line=0 last_line=0 got=0 sha=""
    # THE LABEL SURVIVES A ZERO-BYTE TICK. Rotation to an EMPTY file — the normal log-rotate
    # case — has want == 0, and this branch used to relabel it "unchanged" and then write the
    # PRE-rotation cursor back with the NEW inode. The next tick therefore resumed at a stale
    # offset inside a brand-new file and reported it as a clean append with skipped_bytes: 0.
    if [ "$want" -eq 0 ] && [ "$event" != "rotated" ] && [ "$event" != "truncated" ]; then
      event="unchanged"; from="$coff"
    fi

    if [ "$want" -eq 0 ]; then
      : # nothing to fetch; the cursor still has to be committed at `from` below
    elif [ "$DRY_RUN" -eq 1 ]; then
      : # inert: no fetch exec, no cursor advance, bytes stay 0
    else
      first_line="$(count_lines "$mirror")"
      local part="$STATE/part.$n" frc=0
      local fstart="$from" anchor=0 msize=0 gap=0
      if [ "$event" = "append" ] && [ "$want" -gt "$cap" ]; then
        # DEVIATION 6: keep the NEWEST bytes. The incident is at the end of a burst, and the
        # gap is a real discontinuity, so the mirror is rolled aside exactly as a rotation is —
        # otherwise the model reads two non-adjacent segments as one continuous file.
        # Only on an APPEND: a cold start already chose its own offset (tail-capped, or byte 0
        # under --from-start where a capped tick is a resumable prefix, not a gap).
        fstart=$((size - cap)); gap=$((fstart - from))
        trunc_head=true; skipped_head=$((skipped_head + gap))
        roll_mirror "$mirror"
        first_line=0
      elif [ "$from" -gt 0 ]; then
        # DEVIATION 3, the anchor: re-read the last ANCHOR_BYTES we already hold, in the SAME
        # exec, and prove they are still there before trusting `from`.
        msize=0
        [ -f "$mirror" ] && msize="$(wc -c < "$mirror" 2>/dev/null | tr -d ' ')"
        is_uint "${msize:-}" || msize=0
        anchor="$ANCHOR_BYTES"
        [ "$anchor" -gt "$from" ] && anchor="$from"
        [ "$anchor" -gt "$msize" ] && anchor="$msize"
        fstart=$((from - anchor))
      fi
      # WHY the status is captured on its own statement and not as
      # `if ! fetch_range …; then rc=$?`: inside that branch `$?` is the status of the
      # NEGATED compound, which is ALWAYS 0. The transport's real code (20 refused /
      # 22 timed out) was therefore lost — every fetch failure recorded itself as
      # «rc=0», and a per-file timeout exited 20 instead of the documented 22, sending
      # the operator to the wrong diagnosis. Measured 2026-07-30; regression-guarded by
      # tools/tests/test_fetch_logs.py::TheFetchPathFailsSafely.
      fetch_range "$rpath" "$fstart" "$((cap + anchor))" "$part"; frc=$?
      if [ "$frc" -ne 0 ]; then
        MF_ERRORS+=("$(json_str "fetch failed for $rpath (rc=$frc)${SSH_ERR_LAST:+: $SSH_ERR_LAST}")")
        T_FAILED=$((T_FAILED + 1))
        # A timeout is the more specific diagnosis, so it wins over a plain refusal.
        if [ "$T_FAIL_RC" -eq 0 ] || [ "$frc" -eq 22 ]; then T_FAIL_RC="$frc"; fi
        # Exit 20 plus «итого тик N: 0 файл(ов), 0 Б» is indistinguishable from a clean
        # no-change tick, so the failure is named on stderr (English, house style) AND
        # in run.log (RU alert — the sink that survives the terminal closing).
        name_transport_failure "fetch failed: $(safe_disp "$rpath")" "$frc"
        ru "ошибка: $disp — не удалось забрать (код $frc)${SSH_ERR_LAST:+ — $SSH_ERR_LAST}"
        rm -f "$part"
        MF_FILES+=("$(file_json "$rpath" "$mirror" "$inode" "$size" "$from" "$from" 0 \
                       0 0 "" "$rotated" "$trunc_head" "$skipped_head" "failed" "$inode_ok")")
        continue
      fi
      got="$(wc -c < "$part" 2>/dev/null | tr -d ' ')"
      is_uint "${got:-}" || got=0

      if [ "$anchor" -gt 0 ]; then
        local a_want a_got
        a_want="$(tail -c "$anchor" "$mirror" 2>/dev/null | sha256sum 2>/dev/null | cut -d' ' -f1)"
        a_got="$(head -c "$anchor" "$part" 2>/dev/null | sha256sum 2>/dev/null | cut -d' ' -f1)"
        if [ "$a_want" != "$a_got" ]; then
          # The bytes under the cursor are NOT the bytes we stored: truncated in place and
          # regrown, or rotated with an inode we could not use. Either way `from` is a lie.
          event="truncated"; rotated=false; from=0; anchor=0; gap=0
          roll_mirror "$mirror"
          first_line=0
          trunc_head=false; skipped_head=0
          fstart=0
          if [ "$size" -gt "$cap" ]; then
            fstart=$((size - cap)); trunc_head=true; skipped_head="$fstart"
          fi
          rm -f "$part"
          fetch_range "$rpath" "$fstart" "$cap" "$part"; frc=$?
          if [ "$frc" -ne 0 ]; then
            MF_ERRORS+=("$(json_str "re-fetch after truncation failed for $rpath (rc=$frc)")")
            T_FAILED=$((T_FAILED + 1))
            if [ "$T_FAIL_RC" -eq 0 ] || [ "$frc" -eq 22 ]; then T_FAIL_RC="$frc"; fi
            name_transport_failure "re-fetch after truncation failed: $(safe_disp "$rpath")" "$frc"
            ru "ошибка: $disp — не удалось перечитать после усечения (код $frc)"
            rm -f "$part"
            MF_FILES+=("$(file_json "$rpath" "$mirror" "$inode" "$size" 0 0 0 \
                           0 0 "" false "$trunc_head" "$skipped_head" "failed" "$inode_ok")")
            continue
          fi
          got="$(wc -c < "$part" 2>/dev/null | tr -d ' ')"
          is_uint "${got:-}" || got=0
        else
          # The overlap held. Drop it: only the bytes past the cursor are new, so the appended
          # data begins at the LOGICAL cursor and offset_from in the manifest stays the cursor.
          # The anchor is a transport detail and must never leak into the reported offsets.
          if ! slice_part "$part" tail "$anchor"; then
            MF_ERRORS+=("$(json_str "cannot drop the anchor overlap for $rpath")")
            T_FAILED=$((T_FAILED + 1))
            if [ "$T_FAIL_RC" -eq 0 ]; then T_FAIL_RC=40; fi
            red "cannot drop the anchor overlap for $(safe_disp "$rpath") (state dir full or not writable)"
            ru "ошибка: $disp — не удалось отрезать перекрытие; ничего не записано"
            rm -f "$part"
            MF_FILES+=("$(file_json "$rpath" "$mirror" "$inode" "$size" "$from" "$from" 0 \
                           0 0 "" "$rotated" "$trunc_head" "$skipped_head" "failed" "$inode_ok")")
            continue
          fi
          got="$(wc -c < "$part" 2>/dev/null | tr -d ' ')"
          is_uint "${got:-}" || got=0
          fstart="$from"
        fi
      fi

      # DEVIATION 5: trim back to the last newline. A mirror that ends mid-line makes `wc -l`
      # — and therefore every line number the model cites afterwards — off by one, and offers a
      # fragment as if it were a record. The trailing fragment is re-fetched next tick.
      local keep="$got"
      if [ "$got" -gt 0 ]; then
        local frag=0
        if [ -n "$(tail -c 1 "$part" 2>/dev/null)" ]; then
          # Not newline-terminated. `tail -n 1` measures the trailing fragment's LENGTH; its
          # bytes are never inspected.
          frag="$(tail -n 1 "$part" 2>/dev/null | wc -c | tr -d ' ')"
          is_uint "${frag:-}" || frag=0
          [ "$frag" -gt "$got" ] && frag="$got"
          # A chunk with no newline at all would trim to zero and stall the cursor forever, so
          # a single line longer than the whole chunk is kept as-is.
          [ "$frag" -eq "$got" ] || keep=$((got - frag))
        fi
        if [ "$keep" -lt "$got" ] && ! slice_part "$part" head "$keep"; then
          MF_ERRORS+=("$(json_str "cannot trim the trailing partial line for $rpath")")
          T_FAILED=$((T_FAILED + 1))
          if [ "$T_FAIL_RC" -eq 0 ]; then T_FAIL_RC=40; fi
          red "cannot trim the trailing partial line for $(safe_disp "$rpath") (state dir full or not writable)"
          ru "ошибка: $disp — не удалось обрезать по переводу строки; ничего не записано"
          rm -f "$part"
          MF_FILES+=("$(file_json "$rpath" "$mirror" "$inode" "$size" "$fstart" "$fstart" 0 \
                         0 0 "" "$rotated" "$trunc_head" "$skipped_head" "failed" "$inode_ok")")
          continue
        fi
      fi
      got="$keep"

      # COMMIT ORDER: append to the mirror first; only then rewrite the cursor.
      # A crash costs at worst one re-fetched delta, never a gap.
      mkdir -p "$LOGDIR_LOCAL"
      if [ "$got" -gt 0 ]; then
        cat "$part" >> "$mirror" || {
          MF_ERRORS+=("$(json_str "cannot append to mirror $mirror")")
          red "cannot append to mirror: $(safe_disp "$mirror")"
          ru "ошибка: $disp — не удалось записать в зеркало"
          T_FAILED=$((T_FAILED + 1))
          if [ "$T_FAIL_RC" -eq 0 ]; then T_FAIL_RC=40; fi
          rm -f "$part"
          MF_FILES+=("$(file_json "$rpath" "$mirror" "$inode" "$size" "$fstart" "$fstart" 0 \
                         0 0 "" "$rotated" "$trunc_head" "$skipped_head" "failed" "$inode_ok")")
          continue; }
      else
        touch "$mirror"
      fi
      rm -f "$part"
      last_line="$(count_lines "$mirror")"
      sha="$(sha256sum "$mirror" 2>/dev/null | cut -d' ' -f1)"
      from="$fstart"
      # The cursor advances by bytes ACTUALLY WRITTEN LOCALLY, never by the remote size.
      commit_cursor "$ckey" "$((from + got))" "$inode" "$size" "$rpath" "$disp" || continue
      T_FETCHED=$((T_FETCHED + 1))
      T_BYTES=$((T_BYTES + got))
    fi

    if [ "$want" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
      # Nothing was written, so the cursor must be committed at `from` — which for a rotated or
      # truncated file is 0. Writing $coff here was the bug that survived a rotation-to-empty:
      # the stale offset was stored against the NEW inode, so the next tick skipped the head of
      # the rotated file and called it a clean append.
      commit_cursor "$ckey" "$from" "$inode" "$size" "$rpath" "$disp" || continue
    fi

    if [ "$event" = "unchanged" ]; then
      MF_FILES+=("$(file_json "$rpath" "$mirror" "$inode" "$size" "$from" "$from" 0 \
                     0 0 "" "$rotated" "$trunc_head" "$skipped_head" "unchanged" "$inode_ok")")
      ru "без изменений: $disp"
    else
      MF_FILES+=("$(file_json "$rpath" "$mirror" "$inode" "$size" "$from" "$((from + got))" "$got" \
                     "$((first_line + 1))" "$last_line" "$sha" "$rotated" "$trunc_head" \
                     "$skipped_head" "$event" "$inode_ok")")
      if [ "$DRY_RUN" -eq 1 ]; then
        if [ "$event" = "rotated" ]; then
          ru "план: ротация $disp — курсор был бы сброшен, снимок отложен (--dry-run, ничего не изменено)"
        elif [ "$event" = "truncated" ]; then
          ru "план: усечён $disp — курсор был бы сброшен, снимок отложен (--dry-run, ничего не изменено)"
        fi
      elif [ "$event" = "rotated" ]; then
        ru "ротация: $disp — курсор сброшен, прежний снимок → ${mirror}.rot${ROT_K:-1}"
      elif [ "$event" = "truncated" ]; then
        ru "усечён: $disp — курсор сброшен, прежний снимок → ${mirror}.rot${ROT_K:-1}"
      fi
      if [ "$trunc_head" = true ] && [ "$skipped_head" -gt 0 ] && [ "$DRY_RUN" -eq 0 ]; then
        ru "пропуск: $disp — $(group_digits "$skipped_head") Б не забрано (лимит $(group_digits "$cap") Б); первая строка в зеркале может быть обрывком"
      fi
      if [ "$got" -gt 0 ]; then
        ru "принесено: $disp — $(group_digits "$got") Б (строки $((first_line + 1))–$last_line) → $mirror"
      elif [ "$DRY_RUN" -eq 1 ]; then
        ru "план: $disp — $(group_digits "$want") Б со смещения $from (--dry-run, ничего не принесено)"
      fi
    fi
  done < "$listing"

  # a cursor whose file vanished from the listing: keep it, the file may come back
  local cf cpath
  for cf in "$STATE/cursors"/*; do
    [ -f "$cf" ] || continue
    IFS=$'\t' read -r _ _ _ cpath < "$cf"
    [ -z "${cpath:-}" ] && continue
    [ -n "${seen_paths[$cpath]:-}" ] && continue
    MF_FILES+=("$(file_json "$cpath" "$LOGDIR_LOCAL/${cpath##*/}" 0 0 0 0 0 0 0 "" false false 0 "disappeared" true)")
  done

  # ZERO FILES IS NOT «НЕТ ЛОГОВ». It is «this mask matched nothing», and the two send the
  # operator (and the model) to opposite conclusions. The mask is named, every time.
  if [ "$T_FILES" -eq 0 ]; then
    ru "тик $tick: под маску $c_file_glob не подошёл ни один файл — это не значит, что логов нет; попробуй --glob '*'"
  fi
  ru "итого тик $tick: $T_FETCHED файл(ов), $(group_digits "$T_BYTES") Б"
  printf '%s\n' "$tick" > "$STATE/tick"
  write_manifest "$tick" "$started" "$t0"
  # The real transport code, not a blanket 20: a timeout must stay 22 (see T_FAIL_RC).
  if [ "$T_FAILED" -gt 0 ]; then
    [ "$T_FAIL_RC" -ne 0 ] && return "$T_FAIL_RC"
    return 20
  fi
  return 0
}

count_lines() {
  [ -f "$1" ] || { printf '0'; return 0; }
  wc -l < "$1" 2>/dev/null | tr -d ' '
}

ROT_K=1
roll_mirror() {
  local m="$1" k=1
  [ -f "$m" ] || { ROT_K=1; return 0; }
  while [ -e "${m}.rot${k}" ]; do k=$((k + 1)); done
  mv -f "$m" "${m}.rot${k}"
  ROT_K="$k"
}

file_json() {
  local remote="$1" local_p="$2" inode="$3" rsize="$4" ofrom="$5" oto="$6" bytes="$7"
  local fl="$8" ll="$9" sha="${10}" rot="${11}" th="${12}" sk="${13}" ev="${14}"
  # inode_tracked is PER FILE: the tick-wide "inode_tracking" says the degradation happened
  # somewhere, this says exactly where — so a neighbour's unusable inode is not read as a
  # reason to distrust this file's address.
  local it="${15:-true}"
  printf '{"remote":"%s","local":"%s","inode":%s,"inode_tracked":%s,"remote_size":%s,"offset_from":%s,"offset_to":%s,"bytes":%s,"first_local_line":%s,"last_local_line":%s,"sha256":"%s","rotated":%s,"truncated_head":%s,"skipped_bytes":%s,"event":"%s"}' \
    "$(json_str "$remote")" "$(json_str "$local_p")" "${inode:-0}" "$it" "${rsize:-0}" \
    "${ofrom:-0}" "${oto:-0}" "${bytes:-0}" "${fl:-0}" "${ll:-0}" "$(json_str "$sha")" \
    "$rot" "$th" "${sk:-0}" "$ev"
}

join_json() {
  local IFS=,
  printf '%s' "$*"
}

INVOCATION_ID=""
mint_invocation_id() {
  INVOCATION_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-$(printf '%06x' $((RANDOM * RANDOM % 16777216)))"
}

write_manifest() {
  local tick="$1" started="$2" t0="$3"
  local finished; finished="$(now_utc)"
  local dur=$(( $(date +%s) - t0 ))
  local src
  if [ "$SOURCE_KIND" = "local" ]; then
    src="{\"kind\":\"local\",\"log_dir\":\"$(json_str "$LOCAL_DIR")\",\"file_glob\":\"$(json_str "$c_file_glob")\",\"exclude_glob\":\"$(json_str "$c_exclude_glob")\"}"
  else
    src="{\"kind\":\"ssh\",\"host\":\"$(json_str "$c_host")\",\"port\":$c_port,\"user\":\"$(json_str "$c_user")\",\"auth_mode\":\"$AUTH_MODE\",\"log_dir\":\"$(json_str "$c_log_dir")\",\"file_glob\":\"$(json_str "$c_file_glob")\",\"exclude_glob\":\"$(json_str "$c_exclude_glob")\"}"
  fi
  local dry="false"; [ "$DRY_RUN" -eq 1 ] && dry="true"
  local it="true";  [ "$INODE_TRACKING" -eq 0 ] && it="false"
  local lf="false"; [ "$LISTING_FALLBACK" -eq 1 ] && lf="true"
  local errs=""
  if [ ${#MF_ERRORS[@]} -gt 0 ]; then
    local e; for e in "${MF_ERRORS[@]}"; do errs="$errs\"$e\","; done
    errs="${errs%,}"
  fi
  {
    printf '{"schema":"sherlock.fetch-logs/1","tool":"fetch-logs.sh","tool_version":"%s",' "$VERSION"
    printf '"invocation_id":"%s","tick":%s,"dry_run":%s,' "$INVOCATION_ID" "$tick" "$dry"
    printf '"started_at":"%s","finished_at":"%s","duration_s":%s,' "$started" "$finished" "$dur"
    printf '"source":%s,' "$src"
    printf '"inode_tracking":%s,"listing_fallback":%s,' "$it" "$lf"
    printf '"files":[%s],' "$(join_json "${MF_FILES[@]+"${MF_FILES[@]}"}")"
    printf '"skipped":[%s],' "$(join_json "${MF_SKIPPED[@]+"${MF_SKIPPED[@]}"}")"
    printf '"totals":{"files":%s,"fetched":%s,"bytes":%s,"rotated":%s,"failed":%s},' \
      "$T_FILES" "$T_FETCHED" "$T_BYTES" "$T_ROTATED" "$T_FAILED"
    printf '"errors":[%s]}\n' "$errs"
  } > "$ROOTP/manifest.json.tmp"
  mv -f "$ROOTP/manifest.json.tmp" "$ROOTP/manifest.json"
  cat "$ROOTP/manifest.json" >> "$ROOTP/manifest.jsonl"
  ln -sfn "manifest.json" "$ROOTP/latest" 2>/dev/null || true
}

write_journal() {
  local tick="$1" rc="$2" started="$3" finished="$4"
  local asha; asha="$(printf '%s' "${SSH_ARGV[*]:-}" | sha256sum | cut -d' ' -f1)"
  printf '{"invocation_id":"%s","tick":%s,"mode":"%s","started_at":"%s","finished_at":"%s","rc":%s,"files":%s,"bytes":%s,"argv_sha256":"%s"}\n' \
    "$INVOCATION_ID" "$tick" "$SOURCE_KIND" "$started" "$finished" "$rc" \
    "$T_FETCHED" "$T_BYTES" "$asha" >> "$ROOTP/fetch-log.jsonl"
}

# ==================================================================== the modes
mint_invocation_id

# --- config resolution -------------------------------------------------------
if resolve_config; then
  check_perms "$CFG" "config"
  parse_config "$CFG"
elif [ "$SOURCE_KIND" = "ssh" ]; then
  no_config "no config found. Looked at: --config, \$SHERLOCK_STAND_CONFIG, ./sherlock-stand.ini, ~/.sherlock/stand.ini"
fi

[ -n "$GLOB_OVERRIDE" ]     && { c_file_glob="$GLOB_OVERRIDE"; GLOB_EXPLICIT=1; }
[ -n "$EXCLUDE_OVERRIDE" ]  && c_exclude_glob="$EXCLUDE_OVERRIDE"
[ -n "$MAXBYTES_OVERRIDE" ] && c_max_bytes_per_file="$MAXBYTES_OVERRIDE"
[ -n "$POLL_OVERRIDE" ]     && c_poll_seconds="$POLL_OVERRIDE"
[ -n "$MAXTICKS_OVERRIDE" ] && c_max_ticks="$MAXTICKS_OVERRIDE"

# `flink-*-*.log` is the SPEC's default and it belongs to the Flink stand it was written for.
# A generic local directory has no such convention, and the default silently fetched ZERO files
# and exited 0 — SKILL.md presented `--source local:./logs` as «то же самое, вообще без SSH», so
# a model following it verbatim got an empty manifest with a success code and concluded there
# were no logs. In local mode with nothing asked for explicitly, the mask is everything.
if [ "$SOURCE_KIND" = "local" ] && [ "$GLOB_EXPLICIT" -eq 0 ]; then
  c_file_glob="*"
fi

validate_config

if [ "$SOURCE_KIND" = "local" ]; then
  [ -d "$LOCAL_DIR" ] && [ -r "$LOCAL_DIR" ] || \
    fail 30 "local source directory is missing or unreadable: $LOCAL_DIR"
  LOCAL_DIR="$(cd "$LOCAL_DIR" && pwd)"
  PROFILE="local"
else
  PROFILE="$(b="${CFG##*/}"; printf '%s' "${b%.ini}")"
  [ -n "$PROFILE" ] || PROFILE="stand"
fi

ROOTP="$ROOT/$PROFILE"
STATE="$ROOTP/state"
LOGDIR_LOCAL="$ROOTP/logs"

# ONE ROOT, ONE SOURCE (exit 42). The profile is the config's basename, or the literal word
# `local`, and mirrors are keyed by BASENAME — so `--source local:./a` then `--source local:./b`,
# or two stands whose configs are both called `stand.ini`, append into ONE mirror file with no
# marker and share ONE cursor set keyed by sha1(remote path). Host bravo's first fetch then
# started at host alpha's byte offset, and the model cited `app.log:1` for a line from the other
# host. Distinguishing the profiles by a hash would silently orphan the artifacts an operator
# already has, so the collision is REFUSED and the fix is named instead.
source_identity() {
  if [ "$SOURCE_KIND" = "local" ]; then
    printf 'local\t%s\n' "$LOCAL_DIR"
  else
    printf 'ssh\t%s@%s:%s\t%s\n' "$c_user" "$c_host" "$c_port" "$c_log_dir"
  fi
}
SOURCE_ID="$(source_identity | sha256sum | cut -d' ' -f1)"

bind_source_to_root() {
  local f="$STATE/source.id" prev=""
  [ -f "$f" ] && IFS= read -r prev < "$f"
  if [ -n "$prev" ] && [ "$prev" != "$SOURCE_ID" ]; then
    fail 42 "root $ROOTP already belongs to a different source (its mirrors and cursors are that source's). Use a separate --root for this one, e.g. --root $ROOT/$PROFILE-2"
  fi
  [ -n "$prev" ] && return 0
  printf '%s\n' "$SOURCE_ID" > "$f.tmp" 2>/dev/null && mv -f "$f.tmp" "$f" 2>/dev/null && return 0
  rm -f "$f.tmp" 2>/dev/null
  fail 40 "cannot record the source fingerprint in $STATE"
}

# ONE place that creates the run root, used by --probe AND by the normal path. They used to be
# two independent `mkdir`s with different umask handling, so the permissions of $ROOT depended on
# whether --probe ran first — and --probe-first is the order the docs recommend.
ensure_root() {
  mkdir -p "$STATE/cursors" "$LOGDIR_LOCAL" 2>/dev/null || \
    fail 40 "cannot create the state/output root: $ROOTP"
  chmod 700 "$ROOT" "$ROOTP" "$STATE" "$STATE/cursors" "$LOGDIR_LOCAL" 2>/dev/null
  [ -w "$ROOTP" ] || fail 40 "root is not writable: $ROOTP"
  return 0
}

# --- --print-ssh-argv (no execution at all) ----------------------------------
if [ -n "$PRINT_ARGV" ]; then
  # The printed argv must be the argv a real tick would use, so the multiplexing decision is
  # made here too. cm_setup only mktemp's a socket DIRECTORY under $TMPDIR, which cleanup()
  # removes on exit — nothing is executed and the run root is not touched.
  [ "$SOURCE_KIND" = "ssh" ] && cm_setup
  case "$PRINT_ARGV" in
    list)  build_ssh_argv "$(listing_cmd "$c_log_dir")" ;;
    fetch) build_ssh_argv "$(fetch_cmd "$c_log_dir/$(printf '%s' "$c_file_glob" | tr -d '*?[]')" 4096 "$c_max_bytes_per_file")" ;;
  esac
  ENV_KEYS=()
  case "$AUTH_MODE" in
    password_env|password)
      ENV_KEYS=(SSH_ASKPASS SSH_ASKPASS_REQUIRE SHERLOCK_ASKPASS_SECRET DISPLAY SSH_AUTH_SOCK) ;;
  esac
  if [ "$JSON_OUT" -eq 1 ]; then
    a=""; for x in "${SSH_ARGV[@]}"; do a="$a\"$(json_str "$x")\","; done; a="${a%,}"
    k=""; for x in "${ENV_KEYS[@]+"${ENV_KEYS[@]}"}"; do k="$k\"$x\","; done; k="${k%,}"
    printf '{"argv":[%s],"env_keys":[%s],"auth_mode":"%s"}\n' "$a" "$k" "$AUTH_MODE"
  else
    printf '%s\n' "${SSH_ARGV[@]}"
    printf 'env: SSH_ASKPASS=%s SSH_ASKPASS_REQUIRE=force SHERLOCK_ASKPASS_SECRET=%s DISPLAY= SSH_AUTH_SOCK=\n' \
      "$STATE/askpass-$$.sh" "$([ -n "$_PW" ] && printf '<set>' || printf '<unset>')"
  fi
  exit 0
fi

# --- --check / --probe -------------------------------------------------------
if [ "$DO_CHECK" -eq 1 ]; then
  printf 'source        : %s\n' "$SOURCE_KIND"
  if [ "$SOURCE_KIND" = "ssh" ]; then
    printf 'host          : %s\nport          : %s\nuser          : %s\nauth_mode     : %s\n' \
      "$c_host" "$c_port" "$c_user" "$AUTH_MODE"
    printf 'log_dir       : %s\n' "$c_log_dir"
  else
    printf 'log_dir       : %s\n' "$LOCAL_DIR"
  fi
  printf 'file_glob     : %s\nexclude_glob  : %s\npoll_seconds  : %s\nroot          : %s\nconfig        : %s\n' \
    "$c_file_glob" "$c_exclude_glob" "$c_poll_seconds" "$ROOTP" "${CFG:-<none>}"
  if [ "$DO_PROBE" -eq 1 ]; then
    [ "$SOURCE_KIND" = "local" ] && { green "local source is readable"; exit 0; }
    ssh_preflight
    SIDE_EFFECTS_OK=1
    ensure_root
    bind_source_to_root
    cm_setup
    case "$AUTH_MODE" in password_env|password) write_askpass ;; esac
    if ssh_exec "true" >/dev/null; then green "connectivity: OK"; exit 0; fi
    rc=$?
    [ "$rc" -eq 22 ] && fail 22 "connectivity probe timed out${SSH_ERR_LAST:+: $SSH_ERR_LAST}"
    fail 20 "connectivity probe failed${SSH_ERR_LAST:+: $SSH_ERR_LAST}"
  fi
  exit 0
fi

# --- prepare the working tree ------------------------------------------------
if [ "$WATCH_NEEDS_BOUND" -eq 1 ] && [ "${c_max_ticks:-0}" -eq 0 ]; then
  fail 1 "refusing an unbounded --watch in a non-interactive run; use --once or --max-ticks N"
fi

if [ "$SOURCE_KIND" = "ssh" ]; then
  ssh_preflight
fi

SIDE_EFFECTS_OK=1
ensure_root
bind_source_to_root
acquire_lock

if [ "$SOURCE_KIND" = "ssh" ]; then
  cm_setup
  case "$AUTH_MODE" in password_env|password) write_askpass ;; esac
fi

# --- the loop ----------------------------------------------------------------
CONSEC_FAIL=0
TICKS=0
TICKS_FAILED=0
FINAL_RC=0
# The abort path needs MAX_CONSEC_FAIL consecutive failures, so with the default 5 a bounded
# `--watch --max-ticks 1..4` was STRUCTURALLY incapable of ever reporting a failure — and the
# design sells this mode as "scriptable from any external scheduler". Cap it at the bound.
if [ "${c_max_ticks:-0}" -gt 0 ] && [ "$MAX_CONSEC_FAIL" -gt "${c_max_ticks}" ]; then
  MAX_CONSEC_FAIL="$c_max_ticks"
fi
while [ "$RUNNING" -eq 1 ]; do
  T_STARTED="$(now_utc)"
  collect_tick
  RC=$?
  TICKS=$((TICKS + 1))
  write_journal "$(cat "$STATE/tick" 2>/dev/null || echo 0)" "$RC" "$T_STARTED" "$(now_utc)"

  if [ "$QUIET" -eq 0 ] && [ "$JSON_OUT" -eq 0 ]; then
    if [ "$SOURCE_KIND" = "ssh" ]; then
      printf 'стенд %s@%s:%s  %s  маска %s\n' "$c_user" "$c_host" "$c_port" "$c_log_dir" "$c_file_glob"
    else
      printf 'каталог %s  маска %s\n' "$LOCAL_DIR" "$c_file_glob"
    fi
    printf '%s\n' "${RU_LINES[@]+"${RU_LINES[@]}"}"
  fi
  [ "$JSON_OUT" -eq 1 ] && cat "$ROOTP/manifest.json"

  if [ "$RC" -ne 0 ]; then
    CONSEC_FAIL=$((CONSEC_FAIL + 1))
    TICKS_FAILED=$((TICKS_FAILED + 1))
    FINAL_RC="$RC"
  else
    CONSEC_FAIL=0
    FINAL_RC=0
  fi

  if [ "$MODE" = "once" ]; then break; fi
  # In --watch a per-tick failure does NOT exit: a stand reboot must not kill the watch.
  if [ "$CONSEC_FAIL" -ge "$MAX_CONSEC_FAIL" ]; then
    red "aborting: $CONSEC_FAIL consecutive failed ticks"
    exit 24
  fi
  if [ "${c_max_ticks:-0}" -gt 0 ] && [ "$TICKS" -ge "$c_max_ticks" ]; then break; fi
  [ "$RUNNING" -eq 1 ] || break
  # `sleep &` + `wait` so INT fires immediately instead of after the interval.
  sleep "$c_poll_seconds" &
  wait $! 2>/dev/null
done

if [ "$MODE" = "watch" ] && [ "$QUIET" -eq 0 ] && [ "$JSON_OUT" -eq 0 ]; then
  printf 'остановлено: тиков %s\n' "$TICKS"
fi
if [ "$MODE" = "watch" ]; then
  # `exit 0` unconditionally meant a bounded watch whose EVERY tick failed reported success to
  # its caller, and the design's own selling point is "scriptable from any external scheduler" —
  # such a scheduler saw green forever. A run in which something was fetched, or whose last tick
  # was clean, is still a success: a stand reboot mid-watch must not fail the whole run.
  if [ "$TICKS" -gt 0 ] && [ "$TICKS_FAILED" -eq "$TICKS" ]; then
    red "every tick failed ($TICKS/$TICKS)"
    exit "${FINAL_RC:-20}"
  fi
  exit 0
fi
exit "$FINAL_RC"
