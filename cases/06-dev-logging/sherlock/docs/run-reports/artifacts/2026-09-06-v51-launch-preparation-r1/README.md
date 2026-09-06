# v51 launch preparation r1

Fresh, unused launch templates for the final v51 sequence. The runtime tree is
frozen as `83aea5681f948f630785e4d22f164875ee25101f0bfdb86c7d4e116db98d6740`.
The lifecycle helper is
`235d2e9a11a7a98b3390977f906d3c82315341ded3399b59fbfae66dff41b907`,
the interactive driver is
`c31be32f3d52a59423ad40b157eb550ba097e89833e47339eecbf15516b436b0`,
the dedicated monitor is
`374dd08450e95d195cf338540821a5475b19c3a0c5f4dea539df08861667ec5f`,
and its prompt is
`cd91a67a9128123934167ca2e9d65df5e7030688d72b016dc2ab084e8b420efb`.

The target preparation scripts pin the refreshed DeepSeek V4 Flash rate
snapshot at
`docs/run-reports/artifacts/2026-09-06-deepseek-v4-flash-rate-snapshot-20260906T200907Z/probe-rate-snapshot.json`
with file SHA-256
`a14cbcfdda02c6c627bf016f8447efffbcea330bab0315b7e30101959217e760`.
The snapshot's canonical unsigned rate object is self-bound by
`dfacff6758efec3203df83ce25a608061115b54d1c601d935c0478017d7d0bba`;
`target-contract-probe.py` verifies that internal hash while the preparation
wrappers verify the file hash above.
Preparation seals the target and does not contact DeepSeek or create
`probe-work`.

Run order:

1. `v51-prepare-target-qualification-r1.sh`
2. `v51-launch-harness-r1.sh`
3. after audited harness and semantic-tail acceptance,
   `v51-launch-target-qualification-r1.sh`
4. after target acceptance, `v51-launch-full-wine-r1.sh`
5. `v51-prepare-independent-qualification-r1.sh`
6. after preparation, `v51-launch-independent-qualification-r1.sh`
7. after independent target acceptance, `v51-launch-full-independent-r1.sh`

Every controller launcher sources `v51-owned-review-monitor-r1.sh`. It starts
the dedicated Sonnet monitor before creating the controller root, waits for
the exact sibling monitor directory and parent lock, verifies that the lock PID
is the owned live child, and checks that the controller root remains absent.
The monitor and controller run as `claude-developer`, so the signed mode-0600
observations remain readable without widening permissions. Early controller
failure terminates only the owned monitor and matching lock. At terminal, the
launcher waits for the monitor and runs the separate cryptographic audit.

The audit binds reviews to observations and preserves any unreviewed terminal
tail. It does not semantically accept a corpus report or terminal tail; those
remain separate post-run gates.

The subscription harness command is:

```sh
sudo env \
  TERRA_READINESS_ARTIFACT=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/docs/run-reports/2026-09-06-dedicated-monitor-validation.md \
  MONITOR_SCRIPT=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/eval/bench/dedicated-review-monitor.py \
  HELPER_PATH=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/eval/bench/lifecycle-supervisor.py \
  REVIEW_PROMPT=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/eval/bench/dedicated-review-prompt.md \
  REVIEW_COMMAND_JSON=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/docs/run-reports/artifacts/2026-09-06-v51-launch-preparation-r1/review-command-sonnet-low.json \
  bash /home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/docs/run-reports/artifacts/2026-09-06-v51-launch-preparation-r1/scripts/v51-launch-harness-r1.sh
```

The reviewer command runs `/run/current-system/sw/bin/claude` as the same
service user with Sonnet low, no tools, no setting sources, no persistent
session, and JSON output. The launchers fail closed on all pinned monitor,
prompt, command, helper, driver, target, admission, and readiness inputs.
