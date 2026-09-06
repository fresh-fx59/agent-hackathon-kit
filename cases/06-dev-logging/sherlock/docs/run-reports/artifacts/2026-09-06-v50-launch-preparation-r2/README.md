# v50 launch preparation r2

Preparation-only templates for target qualification r2, harness r4, and independent qualification r2.

Runtime SHA256: `0f95...` (pin exact value before launch). Driver SHA256: `c31...` (unchanged). The dedicated monitor must be started independently before each controller, with its exact Terra readiness artifact path supplied in `TERRA_READINESS_ARTIFACT`.

The helper hash is deliberately fail-closed until Terra publishes the final pinned helper artifact. Monitor roots follow the actual lane layout: target qualification watches `TARGET/probe-work`, with monitor dir `TARGET/probe-work.monitor` and lock `TARGET/.probe-work.review-monitor.lock`; harness and full lanes watch their exact controller root with sibling `<ROOT>.monitor` and parent lock `<ROOT basename>.review-monitor.lock`. The target probe's sealed asset verification covers the prepared root and its `probe-work` trace; monitor state stays outside the sealed asset tree. These scripts do not launch a controller, invoke a provider, consume secrets, or modify existing packages/templates.

Required monitor command shape:

```sh
dedicated-review-monitor.py watch --run-root ABS --helper ABS --prompt ABS --review-command-json ABS --monitor-dir ABS
```

After controller terminal, run `dedicated-review-monitor.py audit` with the same run root, monitor directory, and helper before acceptance.

The pinned reviewer command is [review-command-sonnet-low.json](review-command-sonnet-low.json): absolute remote Claude path, Sonnet, low effort, no tools, empty setting sources, no session persistence, and JSON output. Terra readiness and the final helper digest remain explicit launch gates.
