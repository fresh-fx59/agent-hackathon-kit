# v50 launch preparation r9

Runnable launch templates for target qualification r2, harness qualification
r11, and the two full corpus lanes. The frozen v50 runtime SHA-256 remains
`0f95b5a59dcc41500168d3f2dc6df3e237b8c68e9d3666bf181716983d902ee7`;
the driver remains
`c31be32f3d52a59423ad40b157eb550ba097e89833e47339eecbf15516b436b0`.

Each `v50-launch-*.sh` controller launcher sources
`scripts/v50-owned-review-monitor-r1.sh`. The helper derives the only valid
monitor topology from the lane root, starts the monitor while that root is
still absent, waits until the exact sibling monitor directory and parent lock
exist, verifies that the lock PID is the child it owns and is live, and checks
the run root is still absent before allowing the controller to start. It kills
that child and removes only its matching lock if the controller fails before a
terminal receipt. At a normal terminal boundary it waits for the monitor,
runs the separate binding audit, and returns failure if controller, monitor, or
audit failed.

The binding audit does not semantically accept the report or the unreviewed
terminal tail. Admission still requires the separate post-run semantic review
of the corpus report and the `terminal_unreviewed_snapshot_sha256` object.

Target qualification is deliberately two-phase. First run
`v50-prepare-target-qualification-r2.sh`; this creates and seals `TARGET` but
does not create `TARGET/probe-work`. Then run
`v50-launch-target-qualification-r2.sh`; its monitored controller root is
exactly `TARGET/probe-work`, its artifact directory is
`TARGET/probe-work.monitor`, and its lock is
`TARGET/.probe-work.review-monitor.lock`. Independent target qualification has
the same layout under its independent target root. Harness and full lanes use
their normal controller root, sibling `<ROOT>.monitor`, and parent
`.<ROOT basename>.review-monitor.lock`.

After fresh target preparation creates and seals the r5 target without running
the paid target probe, the complete subscription harness r11 command is:

```sh
sudo env \
  TERRA_READINESS_ARTIFACT=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/docs/run-reports/2026-09-06-dedicated-monitor-validation.md \
  MONITOR_SCRIPT=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/eval/bench/dedicated-review-monitor.py \
  HELPER_PATH=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/eval/bench/lifecycle-supervisor.py \
  REVIEW_PROMPT=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/eval/bench/dedicated-review-prompt.md \
  REVIEW_COMMAND_JSON=/home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/docs/run-reports/artifacts/2026-09-06-v50-launch-preparation-r9/review-command-sonnet-low.json \
  bash /home/claude-developer/hack/wt-v42/cases/06-dev-logging/sherlock/docs/run-reports/artifacts/2026-09-06-v50-launch-preparation-r9/scripts/v50-launch-harness-r11.sh
```

The pinned reviewer command runs remote Claude as `claude-developer`, with that
account's HOME, Sonnet low, no tools, no settings sources, no session
persistence, and JSON output. Every launch remains fail-closed on the monitor,
prompt, command, helper, driver, target, and readiness pins. The standalone
watch and audit scripts are retained for diagnosis; acceptance uses the owned
combined lane launchers above.

The owned helper drops to `claude-developer` with the pinned Nix `setpriv`
binary before starting the monitor. That exec-style transition preserves the
shell child PID, so the authenticated lock still binds the exact process the
launcher owns. The monitor and its 0600 observation files therefore share the
controller's uid and remain readable without widening permissions. The audit
runs under the same uid.

The reviewer command no longer contains a redundant `sudo`; it runs from the
monitor's clean service-user environment and pins
`/run/current-system/sw/bin/claude`. Before this launcher was admitted, the
exact command resolved to Claude Code 2.1.263 and a live subscription probe
with Sonnet low, empty tools, empty setting sources, no session persistence,
and JSON output exited zero. Its JSON envelope reported `is_error: false`,
exact result `{"schema":1,"probe":"same-monitor-uid-ok"}`, and canonical
`claude-sonnet-5` model usage, with an auxiliary Haiku entry.

The provider-free ownership proof used the real lifecycle helper and monitor
cycle under uid 1000/gid 100. It published a signed sequence-zero observation
owned by that uid at mode 0600, then read it as `claude-developer`. A separate
owned-process proof bound the shell PID to the lock PID, asserted 0700 monitor
and 0600 lock ownership, propagated controller exit 7, removed the lock, and
left no child or run root. Exact probe stdout, stderr, exits, and identity
evidence are retained in the preceding
`2026-09-06-v50-launch-preparation-r7/probe/` artifact. The updated
initial-permit classification was separately exercised against the preserved
r9 startup snapshot and archived r3 terminal fault under the exact service UID
and reviewer argv. Exact inputs and outputs are retained in
`2026-09-06-dedicated-monitor-sonnet-lifecycle-replay-r1/`.
