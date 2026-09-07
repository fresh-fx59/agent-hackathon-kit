# Winevtx r3: independently source-reviewed editorial copy

Start with `report-reviewed.md`, then `source-audit.md`. `provenance.json` binds the unchanged original model report, reviewed report, exact diff and source identities. This review does **not** constitute autonomous target-model acceptance or full two-corpus qualification.

The original corpus is retained at `contabo-prod:/home/claude-developer/hack/sherlock-winevtx-corpus-v30-normalized-20260821/`. A read-only rsync copy was made to `/tmp/winevtx-r3-independent-source/`. `source-manifest.json` and a fresh remote `remote-source-sha256.txt` agree for all 143 files (93,196,459 bytes; 90,267 events).

Run `python3 audit.py` from this artifact directory after restoring that corpus path to reproduce the primary counts, bounds and selected evidence. The script writes alongside itself; use a throwaway copy of this artifact directory when reproducing so the reviewed artifact is preserved. It uses only Python's standard library. Never use the audit output as input to a target-model acceptance run.

`rdp-ip-correlation.json` independently records every RdpCoreTS EventID131. For each event, split ClientIP at its last colon, then search Security EventID4625 with the same IpAddress and select the minimum absolute SystemTime difference; ties are ordered by Security line/time. There are 141 events, 118 with nearest failure within 2 seconds and 137 within 10 seconds. These are temporal matches, not unique session joins.

`reviewed-citation-evidence.json` preserves the exact parsed source events and per-line hashes for 42 explicit file:line citations in the corrected report. All bounds/ranges and aggregate claims were additionally checked against the complete source. `aggregate.json`, `credential-aggregate.json`, `rdp.json`, and bounded LSM/service/session/Winlogon evidence retain the central computed results.

Full raw corpus bytes are not duplicated into this Git commit. Larger local intermediate extractions (`credential-read.json`, `firewall.json`, `misc-events.json`, `token-matches.json`) remain untracked; the corpus plus committed manifests and extraction recipe are the reproducibility boundary. The watcher separately owns committing the original model report snapshot and final runtime capture. No live source, target input or model-generated report was modified by this audit.
