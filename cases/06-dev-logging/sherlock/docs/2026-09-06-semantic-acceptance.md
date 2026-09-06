# Semantic acceptance checklist: Winevtx and independent EVTX reports

Purpose: review final model reports only. This is not a finding set and must not
be copied into a cold-start prompt. Sources read 2026-09-06.

## Intake facts that bound every conclusion

- Winevtx input is 143 `corpus/*.jsonl` files and 90,267 event records; all
  rendered `System.Computer` values are `IPSERVER`. The independent input is
  469 records in `BlueSkyRansomware.jsonl`, all `DESKTOP-7EQVM78`.
- The Winevtx provenance says 29,913 records were structurally recovered beyond
  EVTX header-declared chunk ranges. It expressly says this does **not** prove
  current/active log membership. The independent filename proves no incident.
- Inputs exclude prior reports, findings, worklists and checkpoints. Metadata
  files are provenance, not event evidence. Sources: `winevtx/provenance/scope.md`,
  `record-count-correction.json`, `independent/provenance/scope.md`.
- Rendered rows expose raw Event XML under `Event`; there is no raw EVTX,
  allocation map, chunk-offset, or per-row recovered/current membership tag.

## Reject or require correction when a report does any of these

1. **Recovered records / active state.** Accept only “physically/structurally
   recovered evidence” for the 29,913 provenance-level records. Reject “current
   Windows log,” “active channel membership,” deletion/tamper ordering, or an
   individual record's recovery status unless it cites raw EVTX allocation/header
   evidence that is actually supplied. EventRecordID is not that evidence.
2. **Event identity and time.** Each material assertion needs the corpus path
   plus `System.Provider`, numeric `System.EventID`, `System.Channel`,
   `System.Computer`, and `System.TimeCreated.@SystemTime`. Normalize/display
   the `Z` time as UTC. Treat it as when Windows logged the event, not necessarily
   when an outside action happened. Do not merge channels/hosts by filename or
   sort distinct channels solely by EventRecordID.
3. **Attribution.** `System.Computer` identifies the machine that logged the
   event; it is not the actor. For Security 4625, `EventData.IpAddress` is a
   network source claim, `TargetUserName` is the attempted account, and neither
   alone identifies a human or proves possession of credentials. Require the
   report's stated attribution level (`event`, `attempt`, `success`, or `unknown`).
4. **4625 scope.** Treat 4625 as failed logon only when all three match:
   provider `Microsoft-Windows-Security-Auditing`, channel `Security`, EventID
   4625. Do not classify Application's unrelated EventSystem 4625 as a logon.
   Microsoft says 4625 is logged on the computer where the attempt was made.
5. **Failed attempts are not compromise.** A count/burst of valid Security 4625
   rows may support attempted authentication or password-spraying hypotheses.
   It cannot establish successful login, RDP access, account compromise, or
   actor identity without a separately cited success/corroborating event and a
   defensible field/time correlation. Explain `Status` and `SubStatus` separately;
   do not derive the precise reason from generic `Status` alone.
6. **PowerShell Event 600.** Require provider `PowerShell`, channel `Windows
   PowerShell`, and the EventData provider-state fields. Event 600 records a
   PowerShell provider state such as `Started`; its `HostApplication` string can
   support a host command-line/attempt observation, not claimed execution or
   outcome. The current inputs contain no 4103/4104 rows, so do not invent script
   block/module evidence. Stronger script-content evidence would be 4104 where
   Script Block Logging was enabled; an effect still needs independent evidence.
7. **Cross-corpus discipline.** Do not transfer a Winevtx date, host, IP, user,
   count, or finding to the independent corpus. Compare only explicitly stated
   schemas/techniques, and label that comparison as an inference.

## Minimum evidence tests for each finding

- Quote the verbatim JSON fragment and a stable `file:line`; aggregate claims
  must show the exact predicate, population/channel/provider constraints, count,
  and reproducible command.
- Include a counter-check: same EventID in another provider/channel; surrounding
  time window; and, for identity/causation, a missing-or-present corroborator.
- Separate observed fields, Windows-documented semantics, and analyst inference.
  If the corpus cannot decide membership, execution, success, ownership, or
  causality, say so in `Ограничения данных`/rejected candidates rather than
  promote it to a finding.

## Evidence gaps to retain in the final review

- No raw EVTX/header/chunk allocation data in the final input: individual physical
  placement, overwrite/deletion history, and active membership cannot be audited.
- No 4103/4104 in either corpus; Event 600 cannot fill that gap.
- A report needs its own cited successful-authentication/effect evidence before
  claiming compromise or command execution; this checklist supplies none.

## Authoritative references

- Microsoft, [4625(F): An account failed to log on](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/event-4625): failed-logon scope, logging machine, fields, and correlation context.
- Microsoft, [Event Schema Elements](https://learn.microsoft.com/en-us/windows/win32/wes/eventschema-elements): System Channel, Computer, EventRecordID and event metadata meanings.
- Microsoft, [TimeCreated element](https://learn.microsoft.com/en-us/windows/win32/wes/eventschema-timecreated-systempropertiestype-element): `SystemTime` is when the event was logged.
- Microsoft, [about_Logging (PowerShell 5.1)](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_logging?view=powershell-5.1): Script Block Logging/4104 semantics and channel.
