# Steps 3 and 4 — links between sources, checking and delivery

> Moved out of `SKILL.md` in v41, verbatim. A `/clear` between stages re-pays the
> skill body, and a triage session never acts on any of this: 13,526 bytes on every
> triage turn for procedure it will not use. The text below is unchanged — every
> command, every gate invocation and every literal the python gates parse.
>
> **Read this file completely when you reach step 3, not at the start.**

## 6. Step 3. Links between sources

An error almost never starts where it is visible. Go against the flow: the user's
error → the service → its dependency → the infrastructure.

**As soon as you have an identifier — run it through `logjoin.py` before grepping
files by hand.** It reconciles spellings (`ORD-77421` ↔ `ord_77421`), names the
files where the identifier is **NOT** present (`absent_in`: an absence where it
was supposed to be is evidence, and you will not see it yourself), and refuses to
confirm a non-existent link between two entities (`verdict: not-in-corpus`).

    python3 <SKILL_BASE_DIR>/tools/logjoin.py ORD-77421 --corpus <LOG_DIR>
    python3 <SKILL_BASE_DIR>/tools/logjoin.py c-8f3a2b91 10.42.12.31 --corpus <LOG_DIR> --json

**When there is no identifier yet, there is a window.** `--window` searches for
nothing: it builds `[login, logout]` intervals of interactive sessions from
external addresses and lays the state changes from the `statecheck` census out
across them:

    python3 <SKILL_BASE_DIR>/tools/logjoin.py --window --corpus <LOG_DIR>

Inside a window the groups go **from the rarest to the most frequent**, and each
group's subject is printed next to it. The first login to a machine registers two
hundred firewall rules in one second — that is one output line, not two hundred,
and the single-record group standing above it is the reason to look.
`--all-addresses` adds local sessions.

An identifier **gets renamed between services** (`correlation_id`, `trace_id`,
`X-Request-ID`) and may be truncated. Nothing found — switch to time: a ±60
second window around the anchor in every source. **Measure** the clock difference
between sources, do not assume it: find one event in two sources and subtract.
Read files under 4 KB in full before the map — the offset is usually recorded
there.

## 7. Step 4. Checking and delivery

**NEVER TYPE THE COVERAGE TABLE BY HAND EITHER:**

    python3 <SKILL_BASE_DIR>/tools/covermap.py --corpus <LOG_DIR> --worklist ./work/worklist.tsv --header

One row per corpus file, quoting the `logmap`-flagged line — preferring the one triage called a defect. Every file must appear: `citecheck` blocks on any that does not, and «не смотрел» does NOT discharge a file, because nothing can check it. Paste the output as the «Покрытие» section; statuses are read off the files, never invented. **A COVERAGE ROW MAY NOT ANSWER A FILE WITH LINE 1** — line 1 is the oldest record and the reference `logmap` prints for almost every group, so quoting it proves the file was opened and nothing more (measured: 81 of 93 rows in the last gate-clean run, Opera, PowerShell and DPAPI among them). `citecheck` counts it `cov_inadmissible_line` and blocks. The set of lines a file may be answered with is CLOSED — the flagged lines above 1, or line 1 when it is the only flag, or any line of a file of two lines or fewer, or the single last line when `logmap` flagged nothing — so line 2 is no cheaper than line 1. `covermap.py` picks from that set for you; do not guess against it. «нечитабельно» on a file that reads AND yields a quotable line is `cov_false_unreadable` and blocks too — `covermap.py` asks that exact question before it writes «нечитабельно», so its own output never trips it. The worklist is NOT a cheaper answer either: `logmap` writes `work/worklist.manifest.json` with the ids it emitted, and `citecheck --ledger` blocks on every id that is missing from the list you hand back — close a row with a verdict, never delete it.

**AND NEVER TYPE THE RECORD WINDOW BY HAND:**

    python3 <SKILL_BASE_DIR>/tools/rollover.py --corpus <LOG_DIR> --report --required-only --cite <файл-улики> [--cite …]

A Windows channel is a ring buffer: what you hold is a WINDOW, not a history. `EventRecordID` is monotonic per channel, so `(max − min + 1)` against the number of records says outright whether records are missing inside your window. Paste the output as the «Окно записей» section — **a TOP-LEVEL heading: `# Окно записей` at h1, or `## Окно записей` placed AFTER the «Покрытие» section, never nested inside it** (nested, its rows are read as «Покрытие» rows and the span runs on past its own table: measured on the recorded v37 report that is **12** blocking defects — 6 duplicate coverage paths + 6 no-address coverage lines — and not one of them says the word rollover; at h1 or h2-after-«Покрытие» the same report is exit 0) — with one `--cite` per corpus file your findings lean on. `citecheck` re-computes all of it from the corpus and blocks on: no section, an `итог:` line whose six counts disagree with the disk, a channel with a gap that you did not declare, a channel your findings cite that you did not declare, a wrong number, a row the corpus does not support, and every file whose window could not be read. **Where a channel shows `нет=N` with N>0, say so in prose too: inside that window «такого события нет» does NOT mean «этого не было».** An interior hole is usually a filtered export rather than a wrap — report the loss, do not diagnose eviction.

**NEVER TYPE A CITATION BY HAND** — that is what re-reading the address means now:

    python3 <SKILL_BASE_DIR>/tools/cite.py --corpus <LOG_DIR> System.jsonl:263 --contains 3proxy

It prints `путь:строка — «дословная цитата»`; paste it verbatim, then write your claim beside it. The quote comes from `citecheck`'s own builder, so what `cite.py` prints, `citecheck` accepts; `--contains` centres it on the token that matters. **A refusal means the claim is wrong, not the tool** — re-read the line or drop the claim, never paste a citation it declined. Why a tool: `reference/tools.md`.

**A CLAIM ABOUT A POPULATION HAS ITS OWN CITATION** — «93 разных источника, 8 из них больше 1000 отказов» has no single line to quote, so do not delete it and do not fake a line for it. Ask for the number and paste what comes back:

    python3 <SKILL_BASE_DIR>/tools/cite.py --corpus <LOG_DIR> --file Security.jsonl --aggregate 'distinct(Event.EventData.IpAddress, Event.EventData.IpAddress!=-)'

Whatever it prints is the whole line, ending in a long `jq` command. **Copy that
line; never retype it and never abbreviate the command** — an `…` in the command
is `command-mismatch`, and this is the one form that must not be hand-typed.

`citecheck` **re-runs the predicate over the corpus and compares the count exactly** — the trailing command is a rendering for a human, never executed. Use it for every census, ratio and «сколько всего» in the report: how many distinct sources, how many records of one code versus another, how many in a window. Predicates: `count(поле=значение, …)`, `distinct(поле, …)`, `distinct_over(поле, N, …)`; operators `=`, `!=`, `~=` (substring, ≥2 characters), `>=`/`<=` (lexicographic, so **ISO-8601 values only** — a bare number is refused, because lexicographic order lies about numbers).

**An aggregate proves the number, and only the number.** It says nothing about
*who* or *why*: a verified count beside an unrelated claim is a defect the gate
cannot yet see (`reference/report-format.md`, «Что агрегат не доказывает»), so
the aggregate you paste must be the one your sentence is actually about. And a
predicate broad enough to match every record that merely HAS the field is
`too-broad` — «сколько записей имеют это поле» is not a census.

**THE SENTENCE AND THE PREDICATE MUST DESCRIBE THE SAME POPULATION.** A number
can be exact and still answer a different question than the words beside it. The
delivered run `20260827T173511Z-v41` wrote «зафиксировано 33 456 неудачных
входов (4625) от 94 внешних адресов по 1975 словарным именам учётных записей»
over three predicates that filtered on nothing but `EventID=4625`. All three
recomputed exactly; all three counted the **whole** 4625 population, one local
record included (`IpAddress` «-»). The external figures were 33 455 / 93 / 1 974.
Every headline number in that report was wrong by one row, and no gate saw it.

So: **if the sentence narrows the population — «внешних», «публичных»,
«извне», «удалённых» — the predicate must narrow it too.** The existing language
already says it; nothing new is needed:

    count(Event.System.EventID=4625, Event.EventData.IpAddress!=-)
    distinct(Event.EventData.IpAddress, Event.System.EventID=4625, Event.EventData.IpAddress!=-)
    distinct(Event.EventData.TargetUserName, Event.System.EventID=4625, Event.EventData.IpAddress!=-)

`citecheck` finds the sentence that states the number, reads it for a narrowing
word, and asks the corpus whether the predicate's population still contains the
records that word excludes. If it does, the aggregate is
`agg_population_narrower_than_predicate` and blocks — and **the refusal prints
the honest predicate and its number**, so paste that one instead. Beware the
opposite escape: **do not delete the number.** v37 measured what that costs — a
report that dropped a census kept 4 of 93 source addresses. Narrow the predicate,
or drop the narrowing word from the sentence; never drop the fact. How the corpus
spells «no address» (`-`, `127.0.0.1`, `::1`, `localhost`) and which words count
as narrowing are DATA, in
`<SKILL_BASE_DIR>/reference/population-scope.json` — a new corpus gets a new
profile, never a hand-waved exception.

**THE OUTER STATUS CODE IS NOT THE REASON.** The same run wrote «отказы имеют
статус 0xc000006d — словарные имена не подходят к существующим учёткам», and
that clause is how it argued the brute force never touched a real account.
`Status=0xc000006d` is `STATUS_LOGON_FAILURE`: the deliberately uninformative
OUTER code Windows shows for any failed logon. The reason lives in `SubStatus`,
and in that corpus it says the opposite — 25 355 `0xc0000064` (no such user),
**8 098 `0xc000006a` (wrong password against an account that EXISTS)**, 3
`0xc0000072` (disabled). All 8 027 tries against `АДМИНИСТРАТОР` were wrong
passwords against a real, enabled account.

So: **a claim about WHY a logon failed must rest on `SubStatus`, never on
`Status` alone.** `citecheck` reads the code out of your sentence, selects the
records carrying exactly that code, tallies their `SubStatus`, and blocks with
`enum_outer_status_read_as_reason` when they do not unanimously support what you
wrote. **The refusal prints the full breakdown and a ready aggregate per value:**

    count(Event.EventData.SubStatus=0xc0000064, Event.EventData.Status=0xc000006d)

Paste those and rewrite the sentence — **do not delete it.** Deleting the claim
instead of citing it correctly is the v37 regression this project already has a
name for. Saying what the outer code honestly means («имя пользователя или
пароль неверны», «код не уточняет причину») does not block; nor does a sentence
that already cites `SubStatus`. Both halves are DATA: the reason vocabulary in
`<SKILL_BASE_DIR>/reference/logon-failure-reason.json`, the code decodes in
`reference/enum-tables.tsv`. And every `Status`/`SubStatus` you name inside a
`### Н-n` / `### К-n` block needs its decode like any other enum:
`Status=0xc000006d (общий отказ входа)`.

Measured: the run that passed all three gates named **4** attacker IPs of 93, and never stated that `0xc0000064` (нет такой учётки) fired 25355 times against `0xc000006a` (учётка есть, пароль неверный) 8098 — the difference between noise and a target list. It did not fail to cite that; nothing told it the form existed. Full grammar and every refusal: `reference/report-format.md`.

Then run the report through the check — this is **one** call:

    python3 <SKILL_BASE_DIR>/tools/citecheck.py work/report.md --corpus <LOG_DIR> --require-quote --ledger ./work/worklist.tsv

And the second check — on what the triage of the list itself rests:

    python3 <SKILL_BASE_DIR>/tools/triagecheck.py --worklist ./work/worklist.tsv --rules ./work/rules.tsv --corpus <LOG_DIR>

It sorts the closed lines into three buckets — **by name** (own reference with a
quote, or a finding block), **by rule** (`#R1` from `rules.tsv`) and **without
support** (neither of the two) — and prints the share of the third. A non-zero
third bucket means part of the list is closed by a verdict with nothing behind it;
undelivered receipts mean a rule was declared but not verified. Both numbers get
fixed before delivery. With several hosts, run it over
`work/worklist-<хост>.tsv`, just like the ledger.

`wrong-content`, `out-of-range`, `missing-file`, `binary-file`, `ambiguous`,
"no quote", `не-ссылка`, a missing `исход:`, a missing `атрибуция:`, an
empty/missing `## Отклонённые кандидаты`, `## Покрытие` or
`## Окно записей` section, a repeated
`Н-n` or `К-n`, a candidate without a quote, a coverage line without a verifiable
address, someone else's quote, a repeated/ambiguous path or one escaping through
`..`, and a no-address coverage line that does not follow the closed grammar or
the file's fact — fix all of these before delivery. Quote a **verbatim chunk of
the line**: a retelling does not count as a quote.
Mask in a quote only the personal fragment itself (`user@mail.ru` →
`u***@mail.ru`) — everything else verbatim, otherwise the check stops matching
the original line.

The Russian shapes the check parses — the `Н-n` and `К-n` blocks, their
`что сломано:`, `улики:`, `чем опровергал:`, `атрибуция:` and
`исход: успех|попытка|норма` lines, the `## Отклонённые кандидаты` and
`## Покрытие` sections and the four no-address words `пусто`, `двоичный`,
`нечитабельно`, `не смотрел` with their `байт=0` / `формат=двоичный` detail form
— are all defined in `<SKILL_BASE_DIR>/reference/report-format.md`. You MUST read
that file immediately before writing the draft.

Two verdicts the check refuses rather than fails: **`ambiguous`** — the
reference means several files at once, so re-write the path from the corpus root
WITH the machine name; and **`binary-file`** — the reference leads into `.evtx`,
`.pcap`, a dump or an executable, so render it to text into a separate directory
(`evtx_dump -o jsonl`, `tshark -T fields`, `strings`), cite a line of the render,
and never quote the binary itself. You MUST read
`<SKILL_BASE_DIR>/reference/report-format.md` §"Вердикты `ambiguous` и
`binary-file`" before you rewrite either kind of reference.

**An investigation is carried through to a fix if the code is nearby.** Before
writing the report, check once whether the sources lie nearby: `ls`, is there a
`.git`, `pom.xml`, `go.mod`, `package.json`, `pyproject.toml`, a `src/`
directory. The check costs one call. There is code — propose the **minimal** fix
per finding, file and line. There is no code — the section is skipped
**silently**, and you invent nothing. You MUST read
`<SKILL_BASE_DIR>/reference/code-and-spec.md` before proposing any fix, and when
a specification lies nearby.

### `work/report.md` is a DRAFT. Delivery is the last message.

The file exists only because `citecheck` can read a file, not your message.
**The user will never see that file.** It is not on his system, it is in your
temporary folder, and it will be deleted.

So **a green `citecheck` does not mean "done"** — it means "now you may deliver".
The last step is always the same:

    cat work/report.md

and **all** of the output, whole and verbatim, becomes your final message.

**WHAT YOU DELIVER IS WHAT YOU CHECKED.** A retyped reference is a **new claim**:
what was checked was the pair "phrase + address". An abridgement is allowed only
if it passes the check itself — put it in a file and check it with the draft:

    python3 <SKILL_BASE_DIR>/tools/citecheck.py work/report.md --corpus <LOG_DIR> --delivered handover.md

A non-zero return means the delivery cites something outside the confirmed set,
or its own `исход:`, `атрибуция:`, rejected candidates or coverage are not green.
Deliver the draft verbatim and the check passes by itself.

The phrases "отчёт в файле", "отчёт готов", "работа завершена", "см. выше" ARE
the failure. The only correct ending is the report itself.

### The census of state changes

`citecheck` judges your references and `triagecheck` your verdicts; neither sees
what you kept quiet about. The third check comes from the corpus to the report:

    python3 <SKILL_BASE_DIR>/tools/statecheck.py --corpus <LOG_DIR> --report work/report.md

**Exit criterion:** it exits zero — every group of state changes it found is
named in the report, as a finding or as an explicit `норма` with a quote. You
MUST read `<SKILL_BASE_DIR>/reference/tools.md` §"Перепись изменений состояния"
to know what it counts as a state change and how a group is closed.


### The operator's contract — `reportcheck`

`citecheck`, `triagecheck` and `statecheck` all grade the SKILL'S format. None of
them has ever read the CUSTOMER'S requirements, because those do not live in the
skill — they arrive in the prompt. That gap is what made the paid run
`20260827T173511Z-v41` a false positive: three green gates, and a delivered
report that broke five explicit written requirements. The fourth check closes it:

    python3 <SKILL_BASE_DIR>/tools/reportcheck.py work/report.md

**NEVER TYPE THE LABELS, THE INVENTORY OR THE ВЕРДИКТ BY HAND AND HOPE** — same
discipline as `cite.py` and `covermap.py`: run the gate, read the count, fix what
it names. It blocks, each under its own name and count, on
`assertion_unlabelled`, `label_unknown`, `inventory_missing`,
`inventory_unsourced`, `missing_data_section_absent`, `verdict_section_absent`,
`verdict_not_last`, `verdict_not_one_of_three` and `verdict_uncited`.

**Exit criterion:** it exits zero. It is fail-closed — an unreadable report or an
unparseable profile is exit 2, a refusal, never a pass.

The requirements are DATA, not prose inside the checker: the shipped profile is
`<SKILL_BASE_DIR>/reference/report-contract.corporate.json`, and it is the
default. If THIS operator asked for something else — other labels, other
mandatory sections, another verdict vocabulary — copy the profile, edit it, and
point the gate at the copy:

    python3 <SKILL_BASE_DIR>/tools/reportcheck.py work/report.md --contract work/report-contract.json

A different customer gets a new profile, never a hand-waved exception and never
an edit to the gate. You MUST read
`<SKILL_BASE_DIR>/reference/report-format.md` §«Контракт заказчика» before
writing the draft, so the report conforms as it is written rather than being
blocked afterwards.

`stopcheck` runs this gate too, beside `triagecheck` and `citecheck`: delivery is
blocked on it, so skipping it only postpones the same refusal.
