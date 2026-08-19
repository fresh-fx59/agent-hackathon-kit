# DESIGN-EVIDENCE — the measurements the Sherlock tools are built out of

**This file is deliberately NOT part of any shipped arm.** `eval/bench/run-bench.sh`
installs a skill with

```sh
cp -r "$SKILLS/$ARM" "$W/.qwen/skills/log-rca"
```

so every byte inside `skills/v20/` lands in the workspace of the agent under
measurement — its `SKILL.md`, its `reference/`, and the **source of its tools**.
A comment that names the corpus, quotes its file count, or says which file is
attack traffic is therefore readable by the model being scored on that corpus.
That is the leak this document exists to close: the sentences below used to live
in `tools/*.py`, and they now live one directory above the arms, where `cp -r
skills/<arm>` cannot reach them.

**Nothing here was deleted from the tools.** Each site in the source keeps the
general property it was teaching — the thing a reader needs in order not to
re-introduce a fixed bug — plus a pointer of the form `EVIDENCE §E7`. This file
holds the other half: the number, the corpus, and the date.

`tools/tests/test_skill_source_integrity_v20.py` enforces the bargain in both
directions. Every `EVIDENCE §E<n>` written in an arm must resolve to a section
here; every section here must be pointed at by an arm; and the numbers listed in
`RELOCATED_NUMBERS` must still be in this file. Delete a section and the suite
goes red, which is the point — this is evidence, not documentation.

**Corpora referred to below** (none of these names may appear inside an arm):

| short name | what it is |
|---|---|
| `bench649` | the synthetic 649 MB / 31-file / 4 259 018-line benchmark with 11 planted defects and 2 decoys; answer key `eval/bench/answer-key.json` |
| BlueSky | the Windows intrusion corpus, one host, 108 files; key `answer-key-bluesky.json` |
| AIT-LDS (russellmitchell) | the 22-host Linux testbed, 7 464 files; key `answer-key-ait-russellmitchell.json` |
| CAM-LDS scenario 1 | the 5-host Linux testbed, 9 059 files, no shipped key |
| fleet-negative | the three-host production negative control; key `answer-key-fleet-negative.json` |

---

## E1 — the level axis is discovered, never listed

**Rule in the source.** `logmap.py` carries no severity vocabulary at all; the
level axis is found by shape and its whole value histogram is printed.

**Measured 2026-07-31 (v11), on `bench649`.** Across that one corpus the level
axis was, per file, the 6th pipe column, a Cyrillic `KEY=value` field, a numeric
JSON field, and the 3rd whitespace token. Any fixed word list is blind to at
least one of the four.

**What it forbids.** Adding `ERROR|WARN|FATAL` as a constant — it would score
four files right and one file silently wrong.

## E2 — seven time shapes, and the combined-log-format row

**Rule in the source.** Seven timestamp shapes are probed, the epoch shape is
key-agnostic and float-aware, and a file with no usable time axis is announced
loudly instead of dropped from the rate pass.

**Measured 2026-07-31 (v11), on `bench649`.** An hour extractor that understands
only `HH:MM:SS` returns nothing — silently, not as an error — on the two files
that hold the only rate-shaped defects in that corpus. Separately, without the
`clf` row (`10/Oct/2000:13:55:36`, whose hour sits behind a colon and is
rejected by the bare-clock shape's left guard) the two largest files of that
corpus — 1.2 M records, 28 % of it — get **no time axis at all**.

## E3 — a count is not an observation, and a rotated file is not a file

**Rule in the source.** Every repeated group carries first-seen, last-seen and
the share of the capture window it spans; and `<name>.log` + `<name>.log.1.gz`
are stitched into one stream, ordered by the clock rather than by the suffix.

**Measured 2026-07-31 (v11), on `bench649`.** One shape occurred **seven times
inside 284 seconds** of a ten-hour capture. Because the capture straddled a
rotation, it rendered as `n=4` in one file and `n=3` in another — routine
background, read the wrong way round. Analysed apart, every count is halved and
a before/after comparison lands on opposite sides of the cut, so it measures the
rotation instead of the incident.

## E4 — a number glued to a name is not a measurement

**Rule in the source.** A numeric slot is rejected when the digits are glued to
a name by `-_/.`; they survive when separated by punctuation that separates
(`rt=0.003`, `took 1490ms`, `"duration":8`).

**Measured 2026-07-31 (v11), on `bench649`.** Without the test the rate axis
reported the `1.1` out of `HTTP/1.1` as **p50 1.100 s** for an endpoint whose
real response time is **0.002 s**, and the digits of an item code as **p50 56323
/ p99 99468**. A confident wrong number is worse than a missing one, because a
hypothesis gets refuted with it.

## E5 — an epoch axis has to be a clock

**Rule in the source.** `epoch_axis_is_real()` demands that the column advances
and that it covers one capture rather than three centuries.

**Measured 2026-07-31 (v11), on a corpus the tool had not been developed
against.** 16-digit block identifiers divide into a perfectly plausible
microsecond epoch, so every record got an hour and the file was handed a time
axis made of random numbers.

## E6 — the outcome axis is separate, and it knows no protocol

**Rule in the source.** Result codes are discovered by their only shape — three
digits, one narrow range, few values, nearly every record — never by field name
or position; and a column that answers `200` on every probed record is accepted.

**Measured 2026-07-31 (v11), on a 134 MB access log.** The level axis came out
as the request method, the path and the user-agent, and **never** the status
code, while a plain count of codes ≥ 500 per ten minutes went from 0 to **1354**
and landed exactly on the incident minute. `200` scores badly *because* it is a
number, and the three slots it could win are taken. On the same file the first
**4000** records are healthy, so a "needs at least two distinct codes" rule
rejected the status column — and the thing worth finding was the 5xx that
started an hour later. A false positive here costs one line of the map; a false
negative costs the finding.

## E7 — `RARE_MAX_N = 5`, the rarity cut

**Rule in the source.** Axis 1's residue is groups of five records or fewer.

**Measured 2026-07-31 (v11), on `bench649`.** Independently of the design,
**every one of the 13 answer cards** of that corpus is anchored at or below this
cut.

## E8 — `MAP_HOST_BYTES = 150000`, the per-host map budget

**Rule in the source.** The map is capped in bytes per host; a file that earned
a worklist row always keeps its full block, and what is left of the cap goes to
the rest **cheapest-first**; `map.txt` becomes an index and each host gets
`map-<host>.txt`; every folded file is still named, with its size and kind.

**Measured 2026-08-18 (v17).**

| bundle | undivided `map.txt` |
|---|---|
| BlueSky — 1 host, 108 files | 109 019 B (under the cap, untouched) |
| AIT-LDS — 22 hosts, 7 464 files | **7 762 064 B ≈ 1.94 M tokens** |
| CAM-LDS scenario 1 — 5 hosts, 9 059 files | **11 306 386 B** |

and `SKILL.md` told the model to read it. Of the AIT bytes, **5 213 280 of
7 762 064** were sub-4 KB files quoted verbatim, **2 327 946** of them on one
host — which is why the leftover budget is spent cheapest-first: ordering by
size puts hundreds of small files inside a 150 KB budget instead of two 70 KB
ones. N hosts cost N budgets: on that bundle, 22 × the rows of a single-host
run, and the bill is printed in `hosts.tsv` rather than discovered later.

## E9 — `METRIC_MAX_DOMINANT = 0.25`, the reused handle

**Rule in the source.** A measurement is a field where no single value owns a
quarter of the records — the same 0.25 the level axis must exceed, so the two
axes partition the numeric fields on one threshold.

**Measured 2026-08-18 (v18), on BlueSky.** `json:ThreadID` is 88 distinct values
over 1 250 records with one of them owning **643** — a reused thread handle, not
a measurement. Without this line it produced the only false peak row across
three corpora.

## E10 — `STATE_SHARE` and `STATE_PER_FILE_CAP`, the blocklist trap

**Rule in the source.** State artefacts draw from a small separate share of the
worklist, and no single state file may take more than `STATE_PER_FILE_CAP` rows.

**Measured 2026-08-18 (v14), on one AIT-LDS host.** Without the per-file cap,
two threat-intel IP blocklists — `configs/suricata/rules/ciarmy.rules` and
`tor.rules` — took **15 rows each**. A file of nothing but hostile addresses is
a rare-value goldmine to a format-blind counter and pure misdirection to a
reader: those addresses are what the sensor was told to *watch for*, not what
the host did.

## E11 — `metric_axes()` is name-keyed and ranked by repetition

**Rule in the source.** Measurements are looked up by field NAME, not by numeric
slot, and ranked by how much they repeat rather than by how many values they
have.

**Measured 2026-08-18 (v18), on the AIT-LDS metric log
`monitoring/logs/logstash/intranet-server/2022-01-24-system.cpu.log`.** The
field is `pct` inside `host.cpu`; on another corpus it will be `rt`, `took_ms`
or the 6th pipe column, so a name list would be blind to two of the three. That
file is written by Logstash, which emits its JSON keys in a different order on
every line: the same slot index is `host.cpu.pct` on one record and the exponent
of `2.0E-4` on the next, so `judge_slots` correctly threw all ten slots away as
"part of a name". And ranking: `duration` is **1 918 distinct values over 1 920
records** while `pct` is **276** — sorted by variety, the collection timer takes
the slot from the CPU.

## E12 — stream or state: the arithmetic separator

**Rule in the source.** A log stream is a file whose lines carry timestamps and
whose timestamps do not go backwards. No filename list, no path rule, no word
list. `state` is a budget, never a bin.

**Measured 2026-08-18 (v14).** A real evidence bundle is a copy of `/var/log`
next to a copy of `/etc`:

* CAM-LDS scenario 1 — five hosts, each with a full `/var/log` and a full
  `/etc`. Step 1 spent **113 of its 250** worklist rows on files under
  `configs/`; the format-blind arm spent **158 of 250**.
* One AIT-LDS host holds **31 log files and 695 config files**.

and it is worse than wasted budget: on that host the loudest config file was a
suricata ruleset, and what landed in the worklist was

```
configs/suricata/rules/compromised.rules:3  n=1
# Rules to block known hostile or compromised hosts. These lists are updated…
```

A threat-intel ruleset is a list of what to LOOK FOR; handed to a model as a
rare-value anomaly it is actively misleading, not merely noisy.

Accuracy of the separator, against each bundle's own `logs/` vs `configs/`
split:

| corpus | precision | recall | text files shed |
|---|---|---|---|
| CAM-LDS scenario 1 | 1.000 | 0.847 | 8 486 of 8 571 |
| AIT-LDS | 1.000 | 0.924 | 6 769 of 7 178 |

Zero config files were called a stream in either corpus.

**And `state` never means discard.** Re-run on BlueSky the classifier returns
8 state files, and all eight are the attacker's own toolkit —
`Invoke-SMBExec.ps1`, `Invoke-PowerDump.ps1`, `ichigo-lite.ps1`, the C2 web
root. On AIT-LDS it returns the attacker's TLS `premaster.txt`. A planted
`authorized_keys`, an edited `sshd_config` and a dropped webshell are all
evidence that legitimately has no clock.

## E13 — one timestamp is not disorder

**Rule in the source.** A file whose dominant shape occurs once scores
monotonicity 1.0, not 0.0.

**Measured 2026-08-18 (v14), on BlueSky.** Scoring it 0.0 demoted **six real,
tiny Windows channels** out of the stream class. Undefined is not disorder.

## E14 — a delimited table can keep its clock in a column

**Rule in the source.** When no line-substring shape matches, `_column_axis()`
asks whether ANY column is numeric and non-decreasing — still arithmetic, and it
never needs to know what the column is called.

**Measured 2026-08-18 (v14), on BlueSky's `pcap/tcp-streams.tsv`.** Its clock is
`t_start` = `2.826147000`, seconds since the capture began, and no date appears
anywhere in the file. Every line-substring shape misses it, so a genuinely
ordered stream reads as a config.

## E15 — the host partition is an input gate

**Rule in the source.** A bundle from N machines is N corpora. The partition is
read off the SHAPE of the paths — the signature of a root is the set of its
immediate child directory names, and the partition is the shallowest depth at
which one non-empty signature repeats across at least two roots **and at least
half of them**. `--host-depth` / `--single-host` override it.

**Measured 2026-08-18 (v15), on AIT-LDS.** The testbed is **22 hosts and 173
distinct log files** — 1.4 worklist rows per file at a 250-row cap — and the
privilege escalation that opens the intrusion is **eight marked lines inside a
272-line `intranet_server/logs/auth.log`**. Pointed at the whole testbed, v13
and v14 each touched **1 of the 8** marked files and **0 of the 8** marked
lines; pointed at one host, the same code touched **3 of 8** and **all 8**. Same
tool, same corpus, same budget — only the denominator changed.

Three details of the rule, each measured:

* **Why structural, not a hostname list.** A word list is a per-corpus
  maintenance cost. Both AIT-LDS and CAM-LDS give every host exactly `configs/`
  + `logs/`, because one collector wrote all of them. Rename every host to
  gibberish and the answer does not move.
* **Why the whole signature must repeat.** Inside one host, `configs/` and
  `logs/` BOTH contain an `apache2/`, so "two roots share a child name" declared
  `gather/intranet_server` — a single machine — a two-host bundle. `configs/`
  holds 60 directories and `logs/` holds 4; no collector wrote those two as
  peers.
* **Why the majority clause.** BlueSky is `evtx/{host,incident}` + `pcap/*` +
  `toolkit/*`: three roots, three different signatures, two of them empty — so
  no partition, and its worklist stays byte for byte what v14 produced.

**Relocated 2026-08-18 (v21).** Until v21 the multi-host preamble *printed* this
measurement into `map.txt` — «250 строк на 22 хоста — это 11 строк на машину, и
улика из восьми строк в файле на 272 строки в них не попадает» — from two sites,
`hosts_block()` and `render_index()`. Three things wrong with that, and they are
the reason this paragraph is here and not there: **22** is a tally of one testbed
and false on any other bundle; **eight lines in a 272-line file** is the answer
key's own `labelled_lines` / `file_total_lines` for A08, i.e. the shape of the
needle handed to the model that is supposed to find it; and **250** was written
as a literal next to the same paragraph's `%d`-formatted `--worklist-cap`, so a
run with any other cap printed two different budgets, one of them false. The
tools now state the property — a common cap divided among machines leaves each
one a share too thin to hold a short piece of evidence inside a long file — and
nothing else. Measured effect: `worklist.tsv` and `axis3.tsv` byte-identical on
all three corpora; `map.txt` moves by exactly two lines of prose per bundle.

## E16 — key framing: one record happened at one instant

**Rule in the source.** A correlation token is accepted as a record frame only
when the lines it groups share one timestamp, and among the candidates the tool
prefers the field that yields the MOST records.

**Measured 2026-08-18 (v14), on two auditd files.**

| corpus | candidate | share of groups at one instant |
|---|---|---|
| CAM-LDS | `kv:msg` (the real audit id) | 1.00 |
| AIT-LDS | `kv:pid` / `ws:2` / `ws:5` / `kv:ses` | 0.40 / 0.40 / 0.43 / 0.14 |

Contiguity alone cannot tell a record id from a slowly-changing attribute; both
group consecutive lines. The AIT-LDS file is **2 316 lines carrying 2 308
distinct audit ids** — nearly one event per line — and grouping it by pid
produced **539** records, inventing multi-line events out of unrelated
consecutive activity by one process. Sorting candidates by run length instead of
by record count picked `kv:pid` over `kv:msg` on that file: **535 records where
there were 2 316 lines and ~579 events**. Under-grouping loses a little context;
over-grouping invents events that never happened.

## E17 — axis 6 draws from its own generator

**Rule in the source.** The excursion reservoirs use a separate `random.Random`,
never the one the numeric slots use.

**Measured 2026-08-18 (v18), on CAM-LDS scenario 1.** Sharing the generator
changes the draw sequence the slot reservoirs see, and axis 3's percentiles move
on every file big enough to overflow one: `firedtimes` p50 went **640 → 681**
for no reason but this. A new axis may add rows; it may not silently restate an
old axis's numbers.

## E18 — axis 0, the floor, and why only streams get one

**Rule in the source.** A file that produces no axis-1/2 row gets at least one
FLOOR row — `code`, `level`, `burst`, `edge`, in that order of evidential
strength — and the axis column says which, because a floor row is a weaker claim
than a rare one. Only files that draw the STREAM budget get a floor.

**Measured 2026-08-18 (v17), on AIT-LDS.** Until v17 a file whose every record
has its own shape fell through to no worklist rows at all: the apache access log
that is **90.2 % attack traffic** and its sibling error log that is **100 %
attack** both got **ZERO** rows, while 2 089 rows went to files with no marked
lines at all. The quieter path to zero was the same:
`monitoring/logs/logstash/intranet-server/2022-01-24-system.cpu.log` holds
**1 920 records in exactly 2 templates**, both common — not gated, ratio 0.0010,
no rare group — and **49 of its lines are marked attack**. It got nothing.

**Why `code` is first, and what `level` buys.** On the AIT apache access log the
non-dominant outcome code is `404`, and the model is handed the record where it
first appears rather than a count it cannot open. The rarest value of the level
axis is what surfaces the malformed requests (`"-"`, **4.2 %** of that file's
method column) that a template count cannot see.

**Why the floor is stream-only, measured 2026-08-18 (v17) on CAM-LDS scenario
1.** Three of its four axes are meaningless on a state artefact — a config has
no fullest hour and its "first record" is its first line — and the arithmetic is
brutal: that bundle is 9 059 files, ~8 000 of them configs, and thousands are
gated precisely because a config's lines are all different. With state files
admitted, the config share of the worklist went from **62/475 (13.1 %) to
716/1250 (57.3 %)** — exactly the defect v14 was written to remove.

## E19 — the worklist's two budgets

**Rule in the source.** Round-robin across files, rarest first, so no chatty file
eats the budget — but streams and state artefacts draw from SEPARATE budgets,
and the split decides which rows make the cut, never where they sit.

**Measured.** The single most expensive failure this project has measured was a
run that never opened **12 of 28** files (2026-07-31, v11 era). Round-robin alone
is not enough on real evidence: CAM-LDS scenario 1 holds **8 486 config files
against 85 logs** and one AIT-LDS host **695 against 31**, so equal turns give
the configs ~99 % of them — measured before the split, **113 of 250** rows (and
**158 of 250** for the format-blind arm) cited a config rather than a log
(2026-08-18, v14). And emission order matters on its own: on BlueSky the state
files are the attacker's own toolkit, and emitting state last moved those rows
from position **38 to 154** — same rows, worse read. With the second pass the
BlueSky worklist is byte-identical to v13's.

## E20 — everything shown must be citable

**Rule in the source.** A file quoted whole into the map is quoted WITH its
physical line numbers, and a quote cut at the truncation limit says so.

**Measured 2026-08-18 (v19), on BlueSky.** Under 4 KB a file is inlined, and it
used to be inlined with no addresses at all: **26 of 108 files** came out that
way, 25 of them with no worklist row either, and three of those carry the whole
proof of one planted defect and part of another. The model could read a
343-byte artefact, see exactly what it was, and have nothing legal to write next
to the claim — and a finding that cannot be cited is a finding `citecheck` must
reject.

## E21 — a background row for the same message SHAPE

**Rule in the source.** When a candidate's exact template has no background row,
`_sibling_bg()` offers one whose message shape is a token-boundary prefix of it,
at least four tokens deep.

**Measured 2026-07-31 (v11), on `bench649`.** The decoy line there is `eviction
pass took #ms for # entries (threshold 500ms)` — one occurrence — while
**14 042** lines carry `eviction pass took #ms for # entries` and are measured
flat all day. Different templates, so an exact join can never connect them, and
the rate argument that refutes the decoy sits in a row the candidate never
mentions.

## E22 — the rate floor is a TYPICAL hour, not the window

**Rule in the source.** An hour is comparable when it clears a floor derived
from the median hour, never from the total.

**Measured 2026-07-31 (v11).** Against the whole window the floor tightens as
the window widens: on a stitched ten-hour stream whose quiet half ran at a fifth
of the busy half, a 5 %-of-total floor threw away all seven quiet hours and
compared the incident with itself. A relative floor also deletes the shape most
worth finding — a ramp is rare at the start by definition.

## E23 — axis 5, the actor: ranked by lateness, not by rarity

**Rule in the source.** A party that first appears after the stream has
established its population is a state transition — a claim about WHEN, not about
how rare it is. Structural gates only: `ACTOR_CARD_MAX` (a field with a fresh
value on every line has no population), `ACTOR_MIN_N`, `ACTOR_LATE_MAX`.

**Measured 2026-08-18 (v18), on AIT-LDS `vpn/logs/openvpn.log`.** The file holds
15 ordinary rarity groups and **28 marked lines**, and every one of its
templates first occurs in the first 700 of **5 537** lines — so all 15 rarity
rows point at the wrong end of the file. The 28 marked lines are one complete
VPN session establishment whose peer address is absent from the first **78 %**
of the file. Rarest-first does not find it: four internal pool addresses (n=4,
10, 10, 16) rank ahead of the one that matters (n=51). Latest-first returns
exactly one row, and it is that peer.

## E24 — axis 6, the excursion: a departure that RETURNS

**Rule in the source.** The hour whose median is furthest above the file's own
typical hour is offered, and only when a baseline hour sits on each side of it —
which is also what keeps a counter out, since a counter never comes back. High
side only.

**Measured 2026-08-18 (v17), on AIT-LDS
`monitoring/logs/logstash/intranet-server/2022-01-24-system.cpu.log`.** The file
has **1 920 records in 2 common templates, 49 of them marked**, and
`host.cpu.pct` sits at **1.0 through hour 04** against a file-wide typical of
**0.065** — ×15.5, with hours 03 and 05 at baseline. Axis 3 saw nothing: its
comparable-hour floor is 100 records an hour and a 45-second sampler produces
**80**, which is why axis 3 is silent on every metric log in that bundle. The
same file also shows why the excursion is high-side only: `idle` is `1 - total`,
so a bounded metric's floor is its normal state and a low excursion fires
everywhere.

## E25 — what counts as a citation

**Rule in the source.** A token is a citation if, and only if, it resolves
against the corpus index. There is no extension list.

**Measured 2026-07-31 (v11), on `bench649` and its answer key.** The previous
version decided "is this a citation?" from a hard-coded extension list, and
anything with an unlisted extension — or none — was dropped **silently**, with
no output line: **21 of 108 proof locations (19.4 %)** were un-citable, across 5
files, and for **two of the thirteen cards the loss was total**. A report that
found them could not prove it. Resolution still rejects `13:31`,
`127.0.0.1:8317` and `ORD-88240:11`, because none of those is a file.

## E26 — ambiguity fails closed

**Rule in the source.** More than one candidate file for a cited path ⇒ verdict
`ambiguous`, the candidates are named, and no line is read from any of them. One
candidate is a match, not a guess.

**Measured 2026-08-18 (v16), on AIT-LDS v2.1 (22 hosts, 7 464 files).**
**1 038 of that testbed's 2 092 basenames live on more than one machine** — so
`logs/auth.log` means 10 different files and `facts.json` means 22. The old
`check()` graded every candidate and KEPT THE BEST verdict, so a claim that is
false on the host it names came back `ok` because some other machine's file of
the same name agreed at that line number. The ambiguity was printed; it was
simply not allowed to change the answer — a gate that reports its own bypass.

## E27 — a gzipped text log is text

**Rule in the source.** `looks_binary()` reads THROUGH gzip, so the binary guard
and the reader agree; a `.gz` whose decompressed content holds a NUL is still
binary, and a `.gz` that is not valid gzip is unreadable, hence binary.

**Measured 2026-08-18 (v19), on the fleet-negative corpus.** The guard used to
read the raw bytes, and a gzip stream is full of NULs, so **7 of 7 `.gz` files
in that corpus** — all of them plain text once decompressed — came back
verdict `binary-file`, ok 0, while `read_lines()` opened exactly those files
with `gzip.open` and read them perfectly. The guard was rejecting citations the
tool could verify.

## E28 — why `citecheck` exists at all

**Rule in the source.** `wrong-content` is the verdict the whole tool is for: a
real file and a real line number that does not say what the claim says.

**Measured 2026-07-28, on the corporate model.** A run cited `Linux_2k.log:106`
as evidence for a «session opened for user test» claim. Line 106 is an
authentication-failure line; the 36 real occurrences are at 92, 585, 586, 587…
Real file, real line number, wrong content — the shape that survives any range
check. An earlier decision killed this checker on the strength of 79/79
verbatim-verified citations, but that was measured on a *strong* model. Never
generalise a capability finding across model tiers: the weaker the model, the
more a deterministic guard earns its keep.

**Measurement artefact #6, same date.** The old `line_refs` metric counted any
`:\d+`, so ordinary log timestamps inflated it — an OpenSSH baseline scored 114
"refs" with zero real citations.

## E29 — why `logjoin` exists

**Rule in the source.** One identifier across every file in one pass:
canonicalised spelling, an explicit `absent_in` list, and a refusal to confirm a
link between two ids without a co-occurring line.

**Measured 2026-07-31 (v11), on `bench649`.** The skill's coverage problem was
already fixed — **25 of 30 files cited, against 0 without the skill** — but
recall stayed at **18.2 %** against a ≥ 50 % bar, and the remaining gap is
multi-hop depth: each extra hop costs another round of grep-and-read and the
context budget punishes it. On that corpus the decisive evidence for a whole
card was an entity **missing** where it had to be, and a model cannot notice a
thing that is not there. One run asserted a pod↔IP relationship with zero
co-occurrences anywhere in the corpus: two real citations bridged by a
fabricated edge — `verdict: not-in-corpus` is the guard against that.

---

## v22 — a bulk dismissal is a rule with receipts, or it is nothing

**What the tool does.** `skills/v22/tools/triagecheck.py` reads a worklist and a
`work/rules.tsv`, sorts every CLOSED row into **поимённо** (its own `путь:N` with
a quote, or a `D` pointing at a finding block), **по правилу** (`#R<k>`), or
**без опоры**, and refuses (a) a rule condition it cannot evaluate, (b) a rule
without the receipts it demands, (c) a receipt whose quote `citecheck` does not
verify or whose address is not the row it receipts.

**Why it is a tool and not a paragraph. Measured 2026-08-18 (v19), on AIT-LDS
(russellmitchell), clean root `_blind/incident-alpha`, 21 hosts / 7 402 files.**
Trace `eval/bench/runs/20260818T174158Z-v19-claude-ait`. The analyst disposed of
**2 473 of 2 560 worklist rows — 96.6 %** in one Bash call, with a regex it had
written at runtime over the worklist's own excerpt column; every row got
`N фон: n=<count>; 0 совпадений с маркерами инцидента
(nmap|3x6-.|kennedy|wp_meta|Successful su|192.168.230.122)`. Each of those rows
therefore carried a verdict **and** a digit, so `citecheck --ledger` counted them
closed and printed «можно отдавать отчёт». Two of the swallowed rows, `g1091` and
`g1093`, address `internal_share/logs/audit/audit.log:667-668` and their excerpt
column contains the literal `unit=put` — the exfiltration service, i.e. defect
A11. **No tool result in the whole run ever contained `unit=put`; the file was
never opened.** The report's rejected table said «ничего относящегося» with no
line reference at all. v19's rejection rule and v21's assignment rule were both
satisfied in form and defeated in substance, which is why v22 is an artefact
rather than a fourth sentence.

**Replay, 2026-08-18, on that run's own `work/` directory.**

| branch | what was fed in | what `triagecheck` says |
|---|---|---|
| A — the run as it happened | its real `worklist.tsv`, no `rules.tsv` | `строк всего 2560 · поимённо 87 (3.4 %) · по правилу 0 · без опоры 2473 (96.6 %)`, exit 1 |
| B — the marker list filed as a rule | `R1  запись!~*nmap\|3x6-.\|kennedy\|wp_meta\|Successful su\|192.168.230.122*` | refused: «правило не может опираться на текст записи» — `непроверяемых правил: 1`, exit 1 |
| C — the same rows over an axis | `R1  ось=cat\|rare\|level\|edge\|burst\|bg\|rate\|code\|new\|peak`, all 2 473 rows tagged `#R1` | accepted, and it demands **72 receipts** (`k = max(3, ⌈√2473⌉=50, F=72)`), one per file the rule dismisses an excursion from — **2.9 % of the coverage** |

Branch C is the number that matters. The 72 demanded rows span 72 distinct
files, and `internal_share/logs/audit/audit.log` — the file the v19 run never
opened — is one of them (`g1083`, line 85). **One of the 72 sits on a labelled
attack line**: `g1735`, `monitoring/logs/logstash/intranet-server/2022-01-24-system.cpu.log:321`,
the metric excursion of defect A09. Under a uniform draw of the same size the
chance of at least one hit is 47 %; under excursion-first stratification the
draw is not uniform, and the guarantee it does give is not probabilistic — every
file the rule throws an excursion out of has to be opened and quoted once.

**Why `k = min(N, max(3, ⌈√N⌉, F))` and not one citation per row.** 2 560
citations is not a discipline, it is a different way to lose the run, and it
would have been met the same way the marker list was. Sub-linear keeps bulk
possible; the two properties that keep it honest are arithmetic:
`m · √(N/m) = √(mN) ≥ √N`, so **splitting a wide rule into m narrow ones never
costs fewer receipts**, and the `F` term makes the number of files a rule
dismisses excursions from a lower bound on the reading it owes. On the branch-C
rule the excursion pool is **789 of 2 473 rows** (`rare` 786, `new` 2, `peak` 1)
across 72 files, and 17 of the 2 473 rows sit on labelled attack lines — a
density of 0.69 %, which is exactly why a random sample is not the mechanism and
the closed selector language is.

**The regression, 2026-08-18, v22 against v21, same machine.** BlueSky
(`_sanitized/bluesky`) and CAM-LDS scenario 1 (`cam-lds/s1`): **every file
`logmap.py` writes is byte-identical**, so `worklist.tsv` 248 rows and
`axis3.tsv` are unchanged by construction; BlueSky Step 1 against
`answer-key-bluesky.json` is **W=50 real 8/11, anchors 14/31, decoys 6/6**
(W=0 4/11, W=10 5/11), CAM-LDS is **617 rows, 62 config = 10.0 %**. AIT clean
root: **8 of 8 labelled files, 59 of 61 862 lines, 2 560 rows** by
`eval/bench/score-ait.py`. v22 adds one file — `tools/triagecheck.py` — and
edits `SKILL.md` §5/§7/§8 and `reference/tools.md`; Step 1 is not touched.

**The gap this does not close.** A rule may still be wide, wrong and cheap: one
rule over every axis closes 96.6 % of the list for 72 receipts. What changed is
that the shape is now printed as a number next to it — `самое широкое правило:
R1 — 2473 строк (96.6 % закрытых)` — instead of living in a transcript, and that
the 72 receipts are reads the run demonstrably did not do.

---

## v23 — a record is a transaction, and what you deliver is what you checked

### A correlation key must identify a transaction, not a moment (amends §E16)

**Rule in the source.** `detect_key_framing()` rejects a candidate whose values
stop being distinct once the timestamp is removed from them, and `probe()` frames
a fully-parsed common-log-format line as `line` before the key search is ever
reached.

**Why E16 was not enough.** E16's decisive test is «the lines of one record
happened at one instant», with the clock as the corroborating witness. When the
candidate field IS the clock, that test is **1.00 by construction** — the witness
and the accused are the same object, and all four tests pass on a timestamp:

| test | on a one-request-per-line access log keyed by its own stamp |
|---|---|
| mean run ≥ 1.5 | passes — every request inside one second shares the value |
| distinct ≥ 0.05·n | passes — one value per second is plenty of values |
| every value one contiguous run | passes — an access log is written in time order |
| lines of one record at one instant | **1.00, by construction** |

**Measured 2026-08-19 (v23), fleet negative-control corpus.** One nginx combined
log, strictly one line per request: `gzcat … | wc -l` = **1 153**. v22 framed it
`key:ws:3` — whitespace field 3 is `[<dd>/<Mon>/<yyyy>:<hh>:<mm>:<ss>` — and
reported **записей 292**, a 3.95× undercount. Every count derived from records
followed it, and the analyst copied 292 into the report while the answer key
counts 1,153. v23 reports **строк 1153 · записей 1153 · кадрирование line**.

**The residue test, and the constant it broke.** The discriminator is
`clock_residue(v)`: remove the timestamp `_axis_key()` would parse out of the
value and re-ask the distinct-value question of what is left. A bare stamp
leaves nothing; `<epoch>.<ms>:<serial>` leaves its serial. The first
implementation **disarmed the auditd grouping E16 exists for**, because
`field_candidates()` caps a value at `VALUE_MAX = 24` for histogram cost and a
real audit id with a five-digit serial is 27 characters — the serial is
truncated off, so a hundred consecutive events shared one residue. Frequency and
identity were sharing a constant. `make_extractor(axis, cap=…)` separates them;
the identity test reads the value whole at `IDENT_VALUE_MAX = 200`.

**The single-line grammar gate.** `CLF_LINE_RE` requires a FULL parse — host,
ident, user, bracketed stamp, quoted request line, status, size — and is
consulted below `cri`/`block`/`anchor`, so it can only ever claim a file that
would have fallen through to the last-resort key search. **Syslog is deliberately
not in it**: `LEAD_TS_RE` already frames a strict `ts host proc[pid]: msg` line
one record per line AND keeps the wrapped continuation lines a strict grammar
would split off, so adding syslog here could only make that class worse. Both
gates earn their keep — the four `key:ws:4` vhost access logs put a vhost name
in front of the request line, so `CLF_LINE_RE` does not match them and only the
residue test rejects their clock.

**Blast radius, 2026-08-19, v22 vs v23 `logmap.py`, same box, same flags.**

| corpus | files whose framing moved | worklist | score |
|---|---|---|---|
| BlueSky `_sanitized/bluesky` | **0** | `worklist.tsv`/`map.txt`/`axis3.tsv` **byte-identical** | Step 1 W=50 real **8/11**, anchors 14/31, decoys 6/6, **248 rows** — unchanged |
| CAM-LDS s1 | **0** (its 4 key framings survive: 2× `kv:msg`, `ws:2`, `ws:9`) | **byte-identical** | **617 rows, 62 config = 10.0 %** — unchanged |
| AIT clean root `_blind/incident-alpha` | **19 of 423**, every one an HTTP access log: 15× `key:ws:3` + 4× `key:ws:4` → `line` | 2 560 → **2 615** rows | files touched **8/8**, lines covered **59 → 62** |
| fleet negative control | **1 of 37** | 252 → **252** rows, 3 changed | — |

Every moved file lands exactly on its own line count. The three AIT key framings
that survive are the ones with no clock in them (`kv:msg` 4 588 lines → 2 960
records, `ws:0`, `ws:2`).

**Justifying the AIT worklist move by line class.** The +55 is **+71 on the 19
clock-keyed files and −16 on 17 neighbours** that lost the difference to the
per-host 250-row budget; two hosts were **under** budget before and now fill it
(217 → 248, 190 → 224). Axis mix: `rare` +69, `out` 0 → 15, `code` −12,
`level` −15, `edge` −12, `burst` −9 — the fallback «опора» axes shrink because
the shape axes finally have real shapes to rank. The distortion this removes is
visible in one histogram: on `intranet_server/…-access.log.2` the method axis
read **«GET 67.8 %, POST 17.0 %, OPTIONS 11.0 %»** over 264 fused records and now
reads **«GET 96.2 %, POST 2.7 %, OPTIONS 0.7 %»** over 4 000. Grouping by the
clock made rare things look common. Attack coverage moved with it: the file with
the densest labelling (90.2 % of its lines) went from **1 to 4** covered lines
while its worklist rows went 5 → 6, and no label was lost (`wpscan` 2 → 4,
`attacker_http` 3 → 6, `foothold` 5 → 8; `dnsteal` 36, `escalate` 18,
`exfiltration-service` 2 unchanged).

### What you deliver is what you checked

**What the tool does.** `citecheck.py --delivered <файл>` runs the SAME `check()`
over the hand-over against the same corpus, and additionally names every
delivered citation that was not in the report's verified set. Non-zero exit on
either. Hand over the checked draft verbatim and the check passes by
construction.

**Why a subset test alone is not the mechanism. Measured 2026-08-19 on
`eval/bench/runs/20260818T212438Z-v22-claude-fleetneg`.** That run was the first
on this corpus to fall below 100 % citation integrity — **89.4 %** — and the two
channels score very differently:

| channel | citations | ok | wrong-content | не-ссылка |
|---|---|---|---|---|
| `report.md` — the artefact the run checked | 110 | **110** | 0 | 0 |
| `final-message.txt` — the artefact it delivered | 95 | **74** | **21** | 3 |

The brief for this version proposed the subset arithmetic: the delivered
citations must be a subset of the verified ones. Computed on this run, that test
catches **1 of 21**. The verified set holds 68 distinct `(path, line, range)`
triples, and **20 of the 21 failing citations are already in it** — same file,
same line, re-typed under a different sentence inside a condensed inventory
section. What changed was the claim, not the citation.

Extending the key to `(citation, claim)` separates perfectly — **0 of 21** bad
pairs appear verbatim in the report — but only **29 of the 74 good** citations
survive a verbatim-pair test, so it would raise **45 false alarms**. That is why
the pair test is not shipped and the delivered text is simply re-checked instead:
re-checking is the same arithmetic the draft already passes, and it grades a
re-typed claim exactly as it grades a new one.

**The gate on the real failure.** `citecheck.py report.md --corpus … --delivered
final-message.txt` prints `итого: 110 ссылок — ok 110` and then
`ПОСТАВКА … 95 ссылок — ok 74, wrong-content 21` plus
`НЕ БЫЛО В ПРОВЕРЕННОМ НАБОРЕ: 1 — contabo/traefik/access.log:3-7`, and exits
**1**. The same command with `--delivered report.md` exits **0**.

**Where the rule lives.** `SKILL.md` §7 already said the last message is the
hand-over and §8 already named point 5 as the one stopping condition no command
checks. v23 makes half of point 5 checkable and leaves the other half — the act
of pasting the text into the message — where it was, because nothing inside the
skill can observe it. The scorer-side half already exists outside the skill:
`measure/deliverable.py` prints `CHANNELS DIVERGE` when the two channels share
too few blocks, which is how this defect was found. That file is not part of the
skill and was not touched.

**Delta.** v23 changes four files against v22 — `tools/logmap.py`,
`tools/citecheck.py`, `SKILL.md`, `reference/tools.md` — and adds and removes
none. `logjoin.py` and `triagecheck.py` are byte-identical.
