# Raw EVTX intake baseline — r1

- Purpose: verify unchanged v44 ingests original binary Winevtx logs without provider contact.
- Input: `/Users/a/Downloads/Telegram Desktop/winevt/Logs`, 143 EVTX files, 90,173,440 EVTX bytes, plus one 65,536-byte non-EVTX `$I30` file.
- Result: ingest exit1; 143 JSONL files and 90,267 records produced. Directory ingestion correctly reports an unsupported extra file rather than silently accepting incomplete input. This is not a successful whole-directory ingestion claim.
- Evidence root: `/Users/a/hack/sherlock-offline-intake-20260905-r1`; input-manifest.json, start.json, result.json, stdout.log, stderr.log, corpus/_ingest-manifest.tsv.
- Source `$I30` SHA256: `aa30eeaad9edf6f3d8bc907cb144da77f01a48ef028d4e1e9d201723766fd8e5`. Its name is consistent with NTFS directory metadata; content interpretation is not established by name alone.
- Reference: [Microsoft NTFS streams](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/c54dec26-1551-4d3a-a0ea-4fa40f848eb3), retrieved 2026-09-05, describes `$I30` as the default directory index allocation stream name.
- Next hypothesis: explicitly selecting the 143 EVTX inputs should complete while separately preserving/accounting for the non-EVTX sidecar. No ignore rule or gate change is needed.
- Model/provider/usage: not applicable. Original sources unchanged.
