<!-- Loaded on demand from SKILL.md §5. Read it BEFORE closing any worklist
     line in bulk: a rule that cannot be computed asserts nothing. -->

# Bulk closure — by a rule, not by a list of words

## Contents

- **The rule and its five columns** — `work/rules.tsv`, the selection condition,
  the measured claim (`токен`, `код`, `адрес`) and the human rationale.
- **What cannot be closed by a rule** — the `new` and `peak` axes are claims
  about time, so they are closed by name, with `путь:N` and a quote.
- **Marking the closed line** — `N #R1 фон`, ranges, and `g005 Н-2` for a line
  that became a finding.
- **Why a list of words is not a rule** — the `запись` column is a projection.
- **The price of a rule: receipts** — `k = min(N, max(3, ⌈√N⌉, F))`, the five
  TSV columns of a `+R…` line, and `правило` vs `кандидат`.
- **The rate and outcome lines** — `S…`, `B…`, `O…`, the `частота` window,
  `ВСПЛЕСК`, and stitching a `ПОТОК` across rotation.
- **Отказ и атрибуция доказываются как утверждение** — проекция против записи,
  вложенный `ключ=значение`, четыре правила атрибуции.

Triaging every line of a big list by name is unrealistic, and nobody expects it
of you. Lines of one kind are closed by **one rule** — but the rule has to be
recorded, and it must be expressed through what **step 1 computed**: `ось`,
`хост`, `путь`, `файл`, `n` (frequency), `всплеск`, `id`. Rules live in
`work/rules.tsv`, one line per rule — five columns:

    R1	ось=cat && n<=3	N	токен<=24	каталог форм: доля до окна и внутри совпала
    R2	путь~*/rules/*	N	адрес~10.*	список наблюдения, а не наблюдение

The fourth column is the **claim**: what the rule asserts about EVERY line it
closes, asserted in a way that can be measured. There are three fields, and all
of them are measured on the **real line from the file**, not on the `запись`
column:

| field | what it measures | example |
|---|---|---|
| `токен` | the length of the longest chunk of the line between separators | `токен<=24` — "there are no long opaque chunks here" |
| `код` | every three-digit result code in the line | `код=200\|304` — "they answered with success only" |
| `адрес` | every IPv4 in the line | `адрес~10.*\|192.168.*` — "all addresses are internal" |

The operators and `&&` are the same as in the selection condition. **A claim is
not written in words.** A domain guess in a free-form phrase is not a claim but
an impression: it cannot be refuted, and therefore it is not refuted. The
rationale in words goes in the fifth column, for a human.

The tool computes the claim **on all the lines the rule closed** and prints the
measured maximum next to the claim itself. It did not add up — the rule asserts
something other than what those lines say: narrow it, or admit it is a finding.
Raising the bound so the rule passes is allowed — but then the tool will demand a
receipt for exactly that **boundary line**, the very one the bound was raised
for.

**The `new` and `peak` axes cannot be closed by a rule at all.** `new` means
"there was no such participant in the first half of the stream", `peak` — "the
measure went up threefold and came back". These are claims about time, not a
repeating shape; there is no class here, so there is no rule either. Close such
lines **by name**: with their own `путь:N` reference plus a quote, or with a
finding block. There are only a handful of them per corpus — that is cheap.

Mark a line closed by a rule with its number: `N #R1 фон`. A range is allowed
too: `g041-g068 N доля 12,7% → 12,4%` closes 28 lines with one line. Mark a line
that became a finding with a reference to the block: `g005 Н-2`.

**A list of words invented on the fly is not a rule.** Running a regex of markers
you just picked yourself over the `запись` column and stamping the result "0
matches — background" is not a measurement but the shape of one. The `запись`
column is a **projection**, not the file; a word that is not on your list will
give zero matches whatever the line contains, and nobody will open the file
meanwhile. The tool will not accept such a condition — not out of strictness, but
because it cannot compute it, and a rule that cannot be computed asserts nothing.

**A rule has a price — receipts.** A rule that closed N lines must bring
`k = min(N, max(3, ⌈√N⌉, F))` receipts: the worklist line, its `путь:N`
reference and a verbatim quote that `citecheck` verifies. `F` is how many
different files the rule closes on the `rare`/`new`/`peak` axes. **Which lines to
receipt is named by the tool**, out of the rule's own coverage: pick them
yourself and the verified ones would be exactly those already read. Excursions
(`rare`, `new`, `peak`) go into the sample first: there step 1 said "this record
does not look like its neighbours", and discarding such a line in bulk is the
most expensive of all.

This is how it looks in `rules.tsv` — a receipt starts with `+`:

    +R1	g0041	edge-1/logs/relayd.log:145	«дословный кусок строки»	правило

A receipt has **exactly five** TSV columns; the fifth is closed: `правило` or
`кандидат`. A sixth column is an error, not a place for a comment. If the line
you read turned out to be an independent suspicion, write `кандидат` — the check
will stop delivery and ask you to move the line out of the bulk rule and into a
finding, even if the line was not in the mandatory sample. Receipts for an
unknown rule, for someone else's line, and duplicates also stop delivery: every
`+R…` line counts. Leaving a candidate closed by a rule is not allowed. The full
format, the condition fields and what the check prints are documented with
`triagecheck.py` in the skill's tool reference, which SKILL.md points you to.

Lines `S…` and `B…` are the rate axis. `S…` shifted, `B…` is background that did
**not** shift; both kinds must be closed. A negative result ("there is no shift
in this breakdown") is full-fledged evidence, and its place is the section
`## Отклонённые кандидаты`. Lines `O…` are the outcome axis: how many records with
a result code of the given class fell into each interval; `фон 0/интервал → пик N
в HH:MM` names the minute.

In the `частота` column, repeating lines carry a window: `n=7 · 12:58:11→13:02:55
4м=0.8% окна ВСПЛЕСК ×126`. `ВСПЛЕСК` means all occurrences fitted into a narrow
slice of the capture — that is not background. A file marked in the map as
`ПОТОК` is stitched across rotation (`x.log` + `x.log.1.gz`) and counted as one
stream; references still point into the slice where the record physically lies.

## Отказ и атрибуция доказываются как утверждение

**A rejection is proven the same way as an assertion.** To reject a candidate is
an assertion, and the bar of proof is the same. "There is nothing like that in
these records" is legitimate only about what you **read in full**, and
illegitimate about what was not in a **projection**: neither the map, nor the
worklist, nor your own summary shows a record in full. They show its shape — the
fields that made it into the template. A field not shown is not a field absent.

This applies especially to a **nested `ключ=значение` block inside one field**:
when a record carries several more pairs inside itself, glued with escaped `\r\n`
and `\t` (the typical case — an engine or an agent that puts its context on one
line), the first pair makes it into the template while the decisive one may be
the third or the tenth. So: before writing "no signs of", **expand one record of
that group in full** — to the end of the line, including everything after the
escaped newlines — and list which pairs it actually contains. A rejection without
a record read is a guess; it goes into the report with the same `путь:N` or
`путь:N-M` reference as a finding.

**Attributing evidence is also an assertion.** You already prove a rejection; the
same proves your choice of conclusion. One record often fits two conclusions at
once — and then "which one to file it under" is decided by you, and a decision
without grounds is no different from a guess. Four rules:

- **A matching address is not a matching event.** The same host, port,
  identifier or account name can appear in two different events. What
  distinguishes them is **direction, who initiated, and when**. Until those three
  are named, a shared address does not link the records to one another.
- **A conclusion's window does not swallow everything that fell into it.** If a
  conclusion has a time interval, a record inside the interval does not yet
  belong to it. Say which records you attribute to the conclusion and which merely
  happened to be nearby.
- **One piece of evidence — one conclusion, and why that one.** If a record was
  given to conclusion A while it also fit B, write in one line **why A and not
  B**. An unnamed alternative is not the absence of an alternative.
- **"This is background" and "this happened earlier" are assertions too.** Moving
  a found piece of evidence into normality or into the period before the event is
  allowed only with the same support as an accusation: a `путь:N` reference to the
  record that shows it. Otherwise the evidence stays evidence, and the line stays
  `?`.

Check yourself at the end: **every piece of evidence you found lies in exactly
one conclusion**, and not one vanished between "found" and "written".
