# `tools/` — three scripts the skill may call, and must survive without

Each one is a **single self-contained `python3` file, stdlib only, zero config**
(AGENTS.md R1). No pip, no network, no LLM, no env var, no state on disk. They
ship inside the skill folder, so installing the skill installs them; and because
`run_shell_command` is denied in some environments, **the skill must produce the
same report when none of them can run** — slower, not worse. They are leverage,
never a dependency.

| tool | one line | the measurement that produced it |
|---|---|---|
| `citecheck.py` | does the cited line actually *say* what the report claims? | a run cited `Linux_2k.log:106` for a «session opened for user test» claim; line 106 is an auth-failure line, the real ones are 92/585/586/587 — **real file, real line, wrong content** |
| `logstat.py` | one cheap call per file so the model can *choose* what to open | recall 100 % → 73 % → 18 % on one corpus and one model, decided almost entirely by which files got opened |
| `logjoin.py` | follow one id across every file — and name the files it is **missing** from | recall stuck at 18.2 % vs a ≥50 % bar; coverage was fixed, **multi-hop depth** was not |

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

## How the skill should reference these (NOT yet wired — v4/v5 untouched)

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
./tests/run.sh          # all three suites
python3 tests/test_citecheck.py -v
```

Fixtures are tiny and readable — see `tests/fixtures/README.md`. The load-bearing
one is `linux_syslog_excerpt.log`: the byte-identical first 120 lines of the real
`Linux_2k.log`, so line 92 and line 106 are the same lines the failing run saw.
`test_citecheck.py` also re-runs that assertion against the **full real file**
when `~/hack/logalyzer-real-world-testset/` is present, and skips cleanly when it
is not.
