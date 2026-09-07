# V52 hook-root helper correction

R3's generated settings correctly register the Stop hook as
`python3 "$QWEN_SKILL_ROOT/tools/stopcheck.py"`, with the r3 skill directory
at `/home/claude-developer/hack/sherlock-v52-winevtx-20260907-r3/skills/v52`.
The reported target `/tools/stopcheck.py` means the variable expanded empty.

The direct helper's prior source blob was
`208979d695e1fbe3d1bdb2487f577bd53bd44be7`. It set `HOME` and
`OPENAI_BASE_URL` but not `QWEN_SKILL_ROOT`. Commit that follows this artifact
adds only `QWEN_SKILL_ROOT="$skill_dir"` to the same Qwen environment. It does
not modify the running r3 root, its report, its generated settings, or v52
content.

Provider-free stand-in on the target host copied the corrected helper, replaced
the Qwen binary with a fake executable, and used a fake secret wrapper. It
observed `cwd=/tmp/qwen-root-test-TOGwwT/runroot`,
`skillroot=/tmp/qwen-root-test-TOGwwT/skill`, and helper exit `0`.
`bash -n` and `git diff --check` also passed. This proves environment propagation
only; it makes no provider, report, or Stop-hook acceptance claim.
