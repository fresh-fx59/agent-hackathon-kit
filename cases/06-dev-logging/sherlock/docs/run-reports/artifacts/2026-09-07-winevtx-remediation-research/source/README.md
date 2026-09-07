# Winevtx remediation research: source evidence

This directory contains a read-only, reproducible extraction for the planned Sherlock changes.
The source is `/tmp/winevtx-r3-independent-source`, the 143-file, 90,267-event Winevtx corpus.
It does not modify the corpus, runtime, existing reports, or vault notes.

## Timeline

- 2026-09-07: Read the existing independent audit and its exact evidence files. Result: it reports FAIL for credential outcomes, May `root`/3proxy attribution, timeline ordering, PowerShell coverage, and broad firewall negatives.
- 2026-09-07: Added `extract_evidence.py`, which reads every JSONL file and emits exact file/line/field evidence. Result: extraction is reproducible from the local corpus.
- 2026-09-07: Ran the extractor and verified source hashes. Result: output is saved in `evidence.json`; corpus content was unchanged.
- 2026-09-07: Correction: the earlier memo incorrectly called source line references “191/199 HostApplication counts” and repeated a PowerShell path. Raw recount found 200 `HostApplication=` records, all containing `Write-Host`, in `rendered/Windows-PowerShell.jsonl`; 25 of its records have Event ID 400. `rendered/Microsoft-Windows-PowerShell-4Operational.jsonl` has 75 records and no `HostApplication=` fields in this extraction. Result: prior-audit line references are no longer presented as counts.
- 2026-09-07: Correction: the extractor now hashes and counts all 143 source files and computes the May interval from raw Local Session Manager timestamps. Result: `evidence.json` records 13m06.147843s from reconnect to service install and 68m58.811265s from install to next disconnect.

## Findings

1. Credential Manager Event ID 5379 has successful reads. The existing exact evidence is `credential-read.json`; it includes `Security.jsonl:242`, `ReturnCode: 0`, and `CountOfCredentialsReturned: 1`. The audit aggregates 109 successful reads. This is evidence of a successful read, not proof of theft.
2. May session evidence directly contains `root` and SID `...-1001`; Local Session Manager records include `UserIP` and session transitions. The existing exact evidence is `lsm.json`, `rcm-admin.json`, and `services.json`. The 3proxy service record is `System.jsonl:263`, with `ImagePath` containing `3proxy.exe`, automatic start, and `LocalSystem`; `Security.jsonl:13511` maps SID `...-1001` to `root`. The service install is 13m06.147843s after the 2021-05-09 22:11:55.638239Z reconnect, before the next disconnect.
3. PowerShell evidence is present in `rendered/Windows-PowerShell.jsonl`: 200 `HostApplication=` records, all containing `Write-Host`; 25 records have Event ID 400. The separate `Microsoft-Windows-PowerShell-4Operational.jsonl` file has 75 records and no `HostApplication=` field in this extraction. Absence of 4103/4104 does not justify saying there are no command fields.
4. Firewall evidence includes incoming allow actions beyond the 3proxy rule. The audit found 74 Direction=1, Action=3 events, including mDNS port 5353. Therefore broad “no other incoming allow rules” claims are invalid; the existence of a rule alone does not prove malicious use.

## Generic failure classes for the Sherlock specification

- Treating a failed read, a zero return code, and a returned-credential count as interchangeable.
- Converting an absent event type into an absence of all related evidence.
- Using broad substring searches without provider, channel, Event ID, and payload-shape checks.
- Claiming a person, process execution, compromise, or benign intent from a configured service field alone.
- Failing to preserve exact source-relative file and line citations for every material claim.
- Treating temporal overlap as causal proof, or forcing every RDP failure into one connection.

## Reproduction

```sh
python3 extract_evidence.py /tmp/winevtx-r3-independent-source > evidence.json
sha256sum /tmp/winevtx-r3-independent-source/Security.jsonl /tmp/winevtx-r3-independent-source/System.jsonl
```

The full-file manifest and detailed exact records remain in the prior audit artifact:
`2026-09-07-v52-winevtx-r3-independent-source-audit/source-manifest.json`,
`credential-read.json`, `lsm.json`, `services.json`, and `firewall.json`.

The prior manifest covers all 143 files and hashes to
`62ac6f61dd7485fcfd8c89266f8c28be8a459dc34f13b921238574fb54d9e735`.
The prior remote hash receipt is `remote-source-sha256.txt`, SHA256
`8ea4ba053b204fadea6a0e108d2e743125aa30f12d14132d228f1e7800f355c5`.
