# Fixtures

Small on purpose: every assertion in `tools/tests/` must be readable by eye.

| file | what it is | why it exists |
|---|---|---|
| `linux_syslog_excerpt.log` | **byte-identical first 120 lines** of `Linux_2k.log` from the loghub corpus (`~/hack/logalyzer-real-world-testset/real-logs/Linux/`) | Reproduces the real misattribution: line **92** is `session opened for user test`, line **106** is an `authentication failure`. A run on the corporate model cited `:106` for the `:92` claim — real file, real line, wrong content. `test_citecheck.py` encodes exactly that. |
| `corpus/api/app.log` | ISO-8601 app log, mixed levels **plus an invented vocabulary** (`ALARM`, `FATALITY`) | `logstat` must surface a severity vocabulary nobody anticipated (R2) without a dictionary that knows those words. |
| `corpus/gateway/access.log` | Apache-CLF access log, the order id spelled `ord_77421` | `logjoin` must canonicalize `ORD-77421` ≡ `ord_77421` across formats. |
| `corpus/payments/payments.log` | BSD-syslog payment daemon; **`ORD-77421` never appears** | The absence is the finding: the order the API says failed on payment was never charged. `logjoin` reports absence explicitly instead of leaving the model to notice a missing thing. |

Provenance: loghub (`logpai/loghub`) is a public research corpus, redistributed here as a
120-line excerpt for testing only.
