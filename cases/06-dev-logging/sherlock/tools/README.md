# `tools/` — four scripts the skill may call, and must survive without

Three are **single self-contained `python3` files**; the fourth is a single
**`bash` script**. All are **stdlib / coreutils only, zero config** (AGENTS.md
R1). No pip, no network, no LLM. They ship inside the skill folder, so installing
the skill installs them; and because `run_shell_command` is denied in some
environments, **the skill must produce the same report when none of them can
run** — slower, not worse. They are leverage, never a dependency.

`fetch-logs.sh` is the one that touches the outside world, so it carries the
extra caveats: it is *optional* (the skill's happy path is ordinary files on
disk), it needs an *opt-in* config that lives outside the skill folder and
outside `$QWEN_HOME`, and a missing `ssh` is a clean exit 25 that SKILL.md tells
the agent to shrug at.

| tool | one line | the measurement that produced it |
|---|---|---|
| `citecheck.py` | does the cited line actually *say* what the report claims? | a run cited `Linux_2k.log:106` for a «session opened for user test» claim; line 106 is an auth-failure line, the real ones are 92/585/586/587 — **real file, real line, wrong content** |
| `logstat.py` | one cheap call per file so the model can *choose* what to open | recall 100 % → 73 % → 18 % on one corpus and one model, decided almost entirely by which files got opened |
| `logjoin.py` | follow one id across every file — and name the files it is **missing** from | recall stuck at 18.2 % vs a ≥50 % bar; coverage was fixed, **multi-hop depth** was not |
| `fetch-logs.sh` | bring a remote stand's logs here, incrementally, and describe exactly which bytes arrived | operator requirement 2026-07-28 (a Flink stand reachable only over SSH); **no RCA measurement yet**. The `SSH_ASKPASS` mechanism was verified on OpenSSH_10.3p1 (2026-07-30) and against a stub; **the real stand is unproven** |

## citecheck.py — the content-comparing citation checker

```
python3 citecheck.py report.md --corpus ./logs        # human output, exit 1 if bad
python3 citecheck.py - --corpus ./logs --json         # from stdin, for scoring
```

Verdict per citation: `ok` / `wrong-content` / `out-of-range` / `missing-file` /
`unverifiable`. Exit 1 if any of the first four bad ones appear.

How it decides, in ~40 lines of actual logic:

1. Pull `file:line` out of the report — inline, `92-94` ranges, and markdown
   table rows. **A path without a letter-bearing extension is not a citation**,
   which is what keeps `20:29:26`, `22:22` and `127.0.0.1:8317` out. (The old
   `line_refs` metric counted any `:\d+` and scored an OpenSSH baseline at 114
   "refs" with zero real citations.)
2. Re-read that line from the corpus, streaming, stopping at the line asked for.
3. Compare **content**: if the claim carries a quote, the quote must be in the
   line; otherwise the claim's words must overlap the line's words
   (`--min-overlap`, default 0.34).

### Calibrated on real transcripts, not on its fixtures

Run over the **18 saved run transcripts** in `knowledge/measure/**/raw/` against
the real loghub corpora (no LLM, no network — the transcripts and the logs are
both on disk):

| citations | ok | wrong-content | out-of-range | missing-file | unverifiable |
|---|---|---|---|---|---|
| 617 | 465 | **39** | 0 | 0 | 113 |

Those 39 are **21 distinct claims** (a transcript repeats a citation in its draft
and again in its final report). Hand-checked, one by one, against the log:
**19 of 21 are real misattributions** — the model quoting one line and numbering
another, usually off by one or two:

```
Linux_2k.log:14  claims «logrotate: ALERT exited abnormally with [1]»
line 14 is       «su(pam_unix)[21416]: session opened for user cyrus by (uid=0)»
```

The remaining 2 are claims that span several lines of a cited *range*, where the
best single line supports only part of the quote. And the checker independently
re-found the original `Linux_2k.log:106` case in `Linux-warm-rep1.json` — the
same misattribution that put this tool back on the build list.

Three thresholds exist only because that calibration produced false positives,
each now pinned by a test in `CalibratedAgainstRealTranscripts`:

* a quote shorter than 4 comparable tokens is a **search term**, not evidence
  («всегда искать "Accepted password"»);
* a claim whose comparable tokens are all **numbers** is a list of line numbers,
  not a claim about line 16's content;
* stripping a backticked `` `file:line` `` must take the backticks with it, or
  the quote matcher pairs the wrong delimiters and reads prose as a quoted line.

The cost of those floors is the `unverifiable` column: **18 % of citations are
not judged at all**. That is the intended trade — a checker that deletes true
evidence is worse than one that stays quiet.

The one subtlety worth knowing: **only comparable tokens are compared.** A
Russian sentence about an English log line shares no words by construction, so
that is reported `unverifiable`, not `wrong-content`, and does not fail the exit
code. Flagging a true cross-language claim as a fabrication would teach the model
to delete good evidence — worse than the problem being solved.

## logstat.py — corpus triage without reading the corpus

```
python3 logstat.py ./logs                  # every file: size, span, levels, shapes
python3 logstat.py app.log nginx.log.gz --top 8 --json
```

Per file: bytes, exact line count, first/last timestamp **as the raw substring
found** plus which pattern matched, severity histogram, the uppercase vocabulary
actually present (so `ALARM`/`FATALITY`/`ТРЕВОГА` surface with no dictionary),
distinct line-shape count, top-N repeated shapes and the rarest ones.

Three deliberate refusals:

* **Nothing is normalised.** No UTC conversion, no year inference. The retired
  pipeline stamped year 1900 on year-less syslog and then rejected it with its
  own validator. Here: no timestamp ⇒ `null`.
* **Nothing is extrapolated.** Past `--max-lines` (default 100 000) the analysis
  pass switches to stride sampling; `sampled`, `analysed_lines` and `sample_rate`
  say so and the counts stay **raw over the sample**. The line count itself is
  always exact.
* **`distinct_shapes` is a signal, not decoration.** 81 shapes over 51 462 nginx
  lines means shape ranking will find the incident; 4 708 shapes over 10 000
  apache lines means it will not, and the model should sample instead.

Measured cost on this box (4-core, python3.13): the 17 MB / 127 891-line
`logalyzer-real-world-testset` corpus, 20 files, **10.2 s, 18 MB RSS**. Lower
`--max-lines` for a faster first look.

## logjoin.py — the multi-hop primitive

```
python3 logjoin.py ORD-77421 --corpus ./logs
python3 logjoin.py c-8f3a2b91 10.42.12.31 --corpus ./logs --json
```

Per file: matching line numbers (capped by `--max-hits`, count stays exact),
first/last timestamp of the matching lines, and a sample line. Then two things
grep does not do:

* **`absent_in`** — the files where the id never appears. D05's decisive evidence
  was an entity missing where it had to be, and a model cannot notice a thing
  that is not there.
* **co-occurrence** — give it two ids and it answers, corpus-wide, whether any
  single line contains both: `confirmed` or `not-in-corpus`. This is the
  deterministic form of the SKILL.md rule «никогда не выдумывай СВЯЗЬ между
  сущностями». One run asserted a pod↔IP relationship with zero co-occurrences
  anywhere in the corpus — two real citations bridged by an invented edge.

Ids are canonicalised by default (`ORD-77421` ≡ `ord_77421` ≡ `ord.77421`);
`--no-canon` turns that off. Matching is boundary-aware, so `7742` does **not**
match `ORD-77421`; `--substring` opts out.

## fetch-logs.sh — incremental log transport over SSH (or a local dir)

```
fetch-logs.sh                                    # one tick, config auto-found
fetch-logs.sh --check                            # validate config+perms, redacted plan
fetch-logs.sh --source local:./logs --glob '*.logsample'   # no SSH, no config at all
```

One tick = **one** listing exec (`find … -printf '%s\t%i\t%p\n'`), then one
`tail -c +<offset+1> … | head -c <cap>` per file that grew. Bytes land in an
append-only local mirror under `artifacts/watch/<profile>/logs/`, and
`manifest.json` records, per file, the byte window taken and **the local line
range it occupies** — which is what lets the model address exactly the new
material as `файл:строка`.

### It implements the R4 source contract, and that is the whole point

AGENTS.md R4 says a new log source must not touch the core: one contract,
`resolve(spec, window) -> local bytes + manifest`, and **implementations must not
parse content**. This script is exactly that. `spec` is the `[stand]` block,
`window` is the per-file byte cursors, the output is `logs/*` plus
`manifest.json`. It moves and hashes bytes and nothing else: fetched content
flows only through `cat`, shell redirection, `wc -c` and `sha256sum` — `grep`,
`awk` and `sed` are never applied to it, and `tools/tests/test_fetch_logs.py`
asserts that mechanically.

That refusal is not fastidiousness. REQUIREMENTS.md П3 is built on the measured
finding that the previous implementation's regex rule engine **matched nothing**
on a real SSH brute-force incident. Reinstating a matcher inside the fetcher
would contradict the case's own central argument. Detection stays with the model,
via the normal SKILL.md procedure.

### The parts worth knowing

* **Secrets are contained structurally, not filtered.** The password reaches
  `ssh` through `SSH_ASKPASS` + `SSH_ASKPASS_REQUIRE=force`, i.e. through the
  environment — never argv, which `ps` shows to every user on the box. It is
  never interpolated into any message, path, manifest field or log line, so there
  is nothing to redact. A runtime scrubber (`sed "s/$PW/***/g"`) is **forbidden
  in writing**: it would put the password into `sed`'s own argv, and the
  redaction would *be* the leak. `sshpass` is never used for the same reason.
* **The chmod-600 check is a refusal, not a warning** (exit 12), applied to the
  config unconditionally and to `identity_file` whenever set. If `stat` is
  unavailable it fails **closed**.
* **Rotation is detected by inode first**, size-shrink second. This is an
  **addition to the 2026-07-28 spec**, declared as such in the script header: a
  file rotated and recreated *above* the old cursor never trips `size < cursor`,
  and a size-only fetcher then reads from the middle of a different file and
  hands the model garbled lines it would cite with a confident, real-looking
  address. The inode arrives free in the same listing exec.
* **`--source local:<dir>` is a first-class mode**, not a test stub: same cursor
  machinery, same manifest, same exit codes, and a **byte-identical** listing
  command string, so the local arm exercises the real code path.
* **`--once` is the default**; the spec's foreground loop survives as `--watch`,
  which refuses to run unbounded on a non-TTY (exit 1) so an agent turn cannot
  hang on it.
* **Cold start is tail-capped** at `max_bytes_per_file` (10 MiB), because
  SKILL.md's own rule is «не читай весь корпус». Never silent: `truncated_head`
  and `skipped_bytes` go in the manifest. `--from-start` restores spec-literal
  behaviour.

Four deviations from the spec — `--once` default, tail-capped cold start, the
inode rule, and no per-tick detection — are enumerated in the script's own header
with their reasons, so a later reader cannot mistake any of them for a ported
clause.

### What is NOT proven

The password path is proven for the **mechanism only**. Measured 2026-07-30 on
this box (OpenSSH_10.3p1): with `SSH_ASKPASS_REQUIRE=force` and an **empty**
`DISPLAY`, the helper is executed, the prompt arrives as `argv[1]`, and the
helper's stdout is consumed as the secret. That exercises OpenSSH's
`read_passphrase()` path — the same code path an sshd password prompt uses — but
it is **not** an end-to-end password authentication against a real sshd. The
operator's stand's OpenSSH version and PAM stack are unknown. Exits 21 (client
too old) and 22 (timeout) exist so that discovery is a fast, named error instead
of a hang. If the stand cannot do askpass, the answer is `identity_file` — not
`sshpass`.

Everything else inherited from the spec — `poll_seconds = 30`, the
`flink-*-*.log` glob, `/opt/flink/current/log`, the observed `auto-logout` — is a
single operator observation from 2026-07-28. Those are **defaults, not facts**.

## How the skill should reference these (v6 wires fetch-logs.sh; v1–v5 untouched)

`skills/v6/` is the only bundle that references any of these tools, and it
references exactly one: `fetch-logs.sh`, from the new
«Если логи на удалённом хосте» section, phrased so the skill degrades to v5
behaviour when the script, the config or `ssh` is absent. The three insertions
below are still **not wired** anywhere.

Three insertions, each phrased so the skill degrades to the current behaviour
when the script cannot run (`run_shell_command` denied ⇒ do the same thing by
hand, slower):

* **Step 1 (карта)** — before listing files by hand, try
  `python3 <skill>/tools/logstat.py <dir>`. One call replaces the `ls`/`du`/
  `wc -l`/`head`/`tail`/`grep -c` sequence and, unlike them, ranks by *shape*.
  If it fails, fall back to the existing shell block verbatim.
* **Step 3 (корреляция)** — when an id is in play, `logjoin.py <id> --corpus <dir>`
  instead of one grep per file. Its `absent_in` is what step 3 has no way to
  produce today, and its co-occurrence verdict is the mechanical form of the
  existing rule «никогда не выдумывай СВЯЗЬ между сущностями».
* **Step 6 (проверка улик)** — after drafting, `citecheck.py <draft> --corpus <dir>`
  and delete or re-read anything reported `wrong-content` / `out-of-range` /
  `missing-file`. `unverifiable` is not a defect and must not be deleted.

Only the step-6 insertion changes what ships in the report; steps 1 and 3 change
only the cost of getting there. Measure them separately.

## Tests

```
./tests/run.sh          # every suite
python3 tests/test_citecheck.py -v
python3 tests/test_fetch_logs.py     # transport: 117 tests, no network, no credentials
python3 tests/test_bundle_copy.py    # the skills/v6 copies match tools/ byte for byte
```

`test_fetch_logs.py` is a Python suite that drives a bash script on purpose:
`run.sh` globs `test_*.py` only, so a `.sh` test would be silently skipped and
the suite would still print green — i.e. the liveness test the spec calls
«required by operator» would appear to pass while never running. Its load-bearing
arms delete a stub-`ssh` argv journal and assert it **reappeared** (proof the
script ran, not merely that it exists), and walk every byte the script produced
looking for the password literal.

`test_bundle_copy.py` closes a hole nothing in this repo covered: the bundle
copies (`skills/v6/tools/*`) held byte-identity by discipline alone, so a fix to
`tools/fetch-logs.sh` could silently fail to reach the shipped skill.

**Known-red baseline, not caused by v6:** `test_citecheck`, `test_logstat` and
`test_logjoin` currently fail because their fixtures are absent —
`git ls-files tools/tests/fixtures` returns only `README.md`, since root
`.gitignore` (`*.log`, `logs/`) swallowed every `*.log` fixture and they were
never committed. The fixture files are gone from disk, not merely ignored, so the
`.gitignore` negation added for future fixtures does not resurrect them. The two
new suites generate every corpus at runtime and are immune.

Fixtures are tiny and readable — see `tests/fixtures/README.md`. The load-bearing
one is `linux_syslog_excerpt.log`: the byte-identical first 120 lines of the real
`Linux_2k.log`, so line 92 and line 106 are the same lines the failing run saw.
`test_citecheck.py` also re-runs that assertion against the **full real file**
when `~/hack/logalyzer-real-world-testset/` is present, and skips cleanly when it
is not.
