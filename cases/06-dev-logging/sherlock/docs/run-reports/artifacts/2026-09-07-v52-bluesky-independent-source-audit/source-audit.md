# BlueSky corpus: source audit and independently completed report

**Target output: FAIL — no final report.** The direct finalization returned a status sentence followed by DSML `run_shell_command` markup. It contains no substantive findings, evidence-backed conclusion or completed incident report. Its proposed commands were not executed by the no-tools call. HTTP200 / finish_reason stop / zero reasoning are transport results, not completion.

Original direct-output SHA256: `525ff388ec5df407e482d0a880cb291912a3b7e94d2c77d9917f44e2a2d23096`, 2,148 bytes. Preserved in [report-direct-original.md](report-direct-original.md). The [completed report](report-reviewed.md) is **independently authored by the reviewer from original source**, not a repaired autonomous target-model success. Full two-corpus qualification is not claimed.

## Source identity and method

Approved source: `/Users/a/hack/sherlock-final-inputs-20260906/independent/corpus/BlueSkyRansomware.jsonl`, corresponding to `contabo-prod:/home/claude-developer/hack/sherlock-final-inputs-20260906/independent/corpus/BlueSkyRansomware.jsonl`.

SHA256 independently computed: `28b73f0be7b2a6ed7d102c8a8ba86d7c186887e0b71fcacfb8f73dcb0e37ff58`; **467,262 bytes; 469 events**. This exactly matches the approved source identity. All records were parsed, not sampled; event/provider/channel, numeric or object EventID, EventData/UserData and embedded PowerShell context were inspected. Original source and target request/input were not modified. Findings were not supplied to the target call; no old answers or invalid heldout outputs were used as investigation hints.

Reproducible extractor: [audit.py](audit.py); [source-summary.json](source-summary.json); [computed-evidence.json](computed-evidence.json); [powershell-evidence.json](powershell-evidence.json). Exact cited events and source-line hashes are retained separately. The complete source remains at the approved paths; locally generated `source-events.json` is a redundant read-only extraction.

## Source-grounded coverage matrix

All numbers after a colon refer to physical lines of `BlueSkyRansomware.jsonl`.

| Claim group | Actual source evidence / independent count | Verdict and limits |
|---|---|---|
| Scope and chronology | 469 events; Application336 + Windows PowerShell133; all ComputerDESKTOP-7EQVM78; :1 at2024-04-21T00:32:42.803382Z through :469 at2024-04-23T10:11:10.425516Z | Verified. Individual adjacent lines can be out of chronological order; no Security/System/Sysmon/RDP/DefenderOperational coverage. |
| SQL environment | :71/:238 SQL2022 RTM16.0.1000.6, DeveloperEdition64-bit, Win10Pro19045; :76/:243 VMware20,1 | Verified environment, not proof of attack. |
| SQL listeners | :121/:122 and :277/:278 any IPv6/IPv4 port1433; :125/:126 and :281/:282 loopback1434; named pipes separately | Verified server-listener configuration. Per-login destination port/network path not carried by18454. |
| Failed sa authentication | Exactly44 MSSQLSERVER18456, all sa/password-mismatch/CLIENT87.96.21.84; :164–18522 onApr21; :432–45322 onApr23 | Verified counts and reason. No password contents/complexity; brute-force interpretation is inference. |
| Burst chronology | Apr21:04:51:02.988886–04:51:03.066977,0.078091s; Apr23:09:59:54.644152–09:59:54.784581,0.140429s | Verified. Matching timestamps do not justify dropping separate records. |
| Successful sa authentication | Four18454: :186 Apr21 04:51:03.066977; :454 Apr23 09:59:54.784581; :45510:00:11.878462; :45810:00:28.081526; same sa/IP | Verified SQL-authentication success. Do not call this only an unsuccessful brute-force attempt. Operator authorization/human identity unknown. |
| Integrated authentication | Fifteen18453: eight Win10 user/link-local IPv6, seven SQLTELEMETRY/local-machine | Verified, separate from four SQL-authenticated sa entries. |
| SQL configuration changes | :45610:00:12.238113 show advanced options0→1; :45710:00:12.284576 xp_cmdshell0→1; both15457 | Verified change of configured value. Microsoft message requests RECONFIGURE; applied runtime state and command execution are not directly proved. No login/session attribution in these records. |
| Unusual PowerShell runtime | :459–467, nine records, eight600 + one400; first10:01:17.878505, engineAvailable10:01:18.144425; same HostId, MSFConsole, winlogon.exe | Verified fields and engine activation. No full executable path/hash, process tree, loaded attack script or explicit SQL parent. Connection to xp_cmdshell is temporal inference, not proved chain. |
| PowerShell overall count | 133 =107EID600+16EID400+9EID403+1EID800;124 onApr21 +9 onApr23 | Verified. EventID.#text is numeric; the target's unexecuted snippet compares to string'600' and would miscount. |
| Earlier PowerShell commands | :9 INF scan; :24–31 code-tunnel stop command; :32–39 npcap restart command; :40–47 npcapwatchdog registration; :323–38361 VSCode EditorServices records | Verified command fields, not guaranteed terminal effects or malicious intent. |
| EID800 content | :365 PSScriptAnalyzer/VSCode Add-Type Newtonsoft.Json.dll with concrete extension path | Verified. Not an attack payload by itself; “no command information because no4104” would be false. |
| Defender reported state | SecurityCenter15 total12: eightON, fourSNOOZED. LastON:42309:52:59.188634; firstSNOOZED:42509:54:01.302195; also427–429 | Verified. State change predates second SQL burst. Actor, cause and exact security features affected unknown; no DefenderOperational to prove no detections. |
| Windows4625 trap | :55 provider Microsoft-Windows-EventSystem, SuppressDuplicateDuration | Verified non-authentication event; not a Windows failed logon. |
| Ransomware / exfiltration | No records directly showing encrypted files, ransom note, ransomware process or data transfer in these469events | Limited negative finding only. Filename is not evidence of malware family; missing channels prevent a system-wide absence claim. |
| Attribution and cause | Failed-then-successful sa, configuration change, later MSFConsole/PowerShell; local and remote contexts remain distinct | Strong suspicious sequence; no proved human identity, password theft method, SQL→xp_cmdshell invocation→authentic Winlogon injection→ransomware chain. |
| Target report task coverage | Exact direct output has one status sentence and two DSML command requests; no completed finding sections or citations | FAIL substantive completion. Independent reviewer report fills deliverable, not target acceptance. |

## Remaining uncertainties

- Whether the observed sa access and administration were authorized; the human/organization behind the client IP.
- Password contents/strength and whether successful credentials were guessed, known or obtained another way.
- RECONFIGURE/runtime xp_cmdshell state, invoking SQL session/user and any executed command.
- Genuine process path/hash/parent, code executed by the MSFConsole-named PowerShell host, or injection into an authentic Windows process.
- Cause/actor behind DefenderSNOOZED and actual detection history.
- Encryption, ransomware family, C2 or exfiltration outside these retained channels.

## Semantic reference checks

Microsoft [SQL events18000–18999](https://learn.microsoft.com/en-us/sql/relational-databases/errors-events/database-engine-events-and-errors-18000-to-18999?view=sql-server-ver16) distinguishes18453 integrated success,18454 SQL-authenticated success and18456 failure. Microsoft [SQL events15000–15999](https://learn.microsoft.com/en-us/sql/relational-databases/errors-events/database-engine-events-and-errors-15000-to-15999?view=sql-server-ver16) describes15457 as configuration change with an instruction to apply RECONFIGURE. These definitions were checked on2026-09-07; external references supply event semantics, not facts about the private incident.

## Timeline

- Verified approved source SHA, parsed all469events and built provider/channel/event catalogue before reading target output. No source findings were seeded into the direct call.
- Independently extracted SQL successes/failures/configuration, all PowerShell host contexts, Defender states, exact times and negative-evidence boundaries.
- Received exact direct output525ff3…; determined it was unfinished DSML tool requests rather than a report, and informed root/read_state.
- Authored separate report from source; checked numeric assertions and explicit citation existence. Exact original/direct and independent-report identities are in `provenance.json`. No full qualification claimed.
