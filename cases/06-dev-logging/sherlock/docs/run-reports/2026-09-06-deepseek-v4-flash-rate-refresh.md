# DeepSeek V4 Flash rate refresh — 2026-09-06

- Status: captured public response; no paid model call.
- Source: `https://neuraldeep.ru/api/public/wallet-prices` (HTTP 200).
- Fetched/effective: `2026-09-06T20:09:07.209162Z`.
- Derived planning expiry: `2026-09-07T20:09:07.209162Z`. The target probe enforces a maximum age of 86,400 seconds; `expires_at` is metadata and is not added to the exact seven-field probe snapshot schema.
- Requested model: `deepseek-v4-flash`; billing unit: token.
- Rates: input **29.546 RUB / 1,000,000 tokens**, output **84.7 RUB / 1,000,000 tokens**, cached input **0.891 RUB / 1,000,000 tokens**.
- Converted probe rates: `2.9546e-05` prompt RUB/token and `8.47e-05` completion RUB/token.
- Raw response: `7c068b39e54f62204a58f788b12e8321aa8136876cfc3bebc2127ad5da098fd4` (8185 bytes).
- Exact probe snapshot: `a14cbcfdda02c6c627bf016f8447efffbcea330bab0315b7e30101959217e760`.
- Previous snapshot was preserved at `docs/run-reports/artifacts/2026-09-06-v50-harness-subscription-r9-failed/remote/target-preparation-r3/probe-rate-snapshot.json`.

The immutable evidence is in [`2026-09-06-deepseek-v4-flash-rate-snapshot-20260906T200907Z/`](artifacts/2026-09-06-deepseek-v4-flash-rate-snapshot-20260906T200907Z/), including the raw JSON, response headers, exact consumer snapshot, metadata, and file hashes. The snapshot uses the existing `target-contract-probe` schema and self-hash construction; cached input is recorded in metadata because the consumer schema has no cache-rate field.
