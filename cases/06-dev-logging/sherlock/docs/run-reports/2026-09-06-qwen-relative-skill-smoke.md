# Relative skill catalogue — installed Qwen experiment

## Result

Qwen Code0.22.0 on contabo-prod discovered the unique mock skill description
RELATIVE_SKILL_DISCOVERY_20260906 through settings.skills.directories containing
`../skill-catalogue`, while launched from a fresh child workspace. All three raw
request pairs contain the skill description. Allow/deny/error scenarios used only
a loopback SSE mock with a dummy key, as claude-developer with isolated HOME and
QWEN_HOME. No Sherlock runtime was modified or loaded.

| Mode | Marker | Hooks | Seconds | CLI exit |
| --- | --- | --- | --- | --- |
| allow | created | matching Pre/Post | 5.956 | 0 |
| explicit deny | absent | Pre only | 4.890 | 0 |
| hook exit7 | created | matching Pre/Post | 4.818 | 0 |

Installed primary source chunk-T6XLJRQY.js96198–96223 resolves custom directories
with expandHomeDir followed by path.resolve and explicitly warns that relative
paths resolve against process cwd. The experiment independently verifies this.
Raw requests, responses, hooks, settings and outputs are preserved locally in
/Users/a/hack/sherlock-qwen-hook-smoke-20260906-r3 and the matching remote hack
folder. The paired JSON contains complete SHA256 file inventory.

## Decision

Monitored runs use fresh TRACE/workspace and sibling TRACE/skill-catalogue.
Stable settings use `../skill-catalogue`; exact bytes can be reused across cold
runs while the discovered package stays outside the writable project. This proves
directory discovery only. Actual lifecycle-helper wiring remains unverified.

## Timeline

- Read installed Qwen code after finding the old absolute target path could
  prevent independent harness/full-run settings equality.
- Extended the mock script with an explicit relative-skill option and compiled it.
- Ran three loopback scenarios; all discovered the unique mock skill description.
- Sent actual results to both integration workers, copied raw evidence locally,
  and generated complete file inventories. No production service changed.
