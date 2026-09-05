# Developer corpus inventory — 2026-09-05

## Scope and result

Read every JSONL record in the143-file Winevtx rendering, grouped by source file,
System.Computer and System.EventID. The paired JSON records each rendered file
hash, full event-ID counts, and lexical bounds of its UTC timestamp strings.
No source file changed. This is a developer baseline for later report checking,
not a target-model report or proof that all physical-chunk records were active.
Do not copy these findings into the target cold-start prompt.

All90,267records name Computer=IPSERVER. Security.jsonl contains34,916records,
of which33,456 have EventID4625. The next largest channel file is Windows Store
Operational with23,900records. A large event count alone does not prove compromise.

Security.jsonl:1 has record417258, timestamp2021-06-01T18:36:04.949933Z,
EventID4625, LogonType3, TargetUserName ADMINI, Status0xc000006d and
SubStatus0xc0000064. Microsoft documents4625 as failed logon and the substatus
as a bad/nonexistent account. This single record cannot establish successful
access, credential compromise or an RDP session.

Primary source retrieved2026-09-05:
https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/event-4625

## Timeline

- 2026-09-05 — First filename glob selected an empty FirewallDiagnostics file
  because its filename also contains Security. Corrected to explicit
  Security.jsonl and read record1; no assertion was made from the empty file.
- 2026-09-05 — Full stdlib JSON scan completed in0.595s, reconciled90,267records,
  and wrote the complete143-file count/hash inventory. Official Microsoft
  documentation verified event/substatus semantics. Deeper causal investigation
  and accepted target report remain pending.
