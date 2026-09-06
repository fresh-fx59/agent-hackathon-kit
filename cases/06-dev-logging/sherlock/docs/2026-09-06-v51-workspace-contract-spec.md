# Scoped specification: v51 Qwen workspace command contract

## Goal contribution

Prevent a correctly formed Sherlock command such as
`checkpoint.py resume --work ./work` from being rejected before execution when
the skill package lives beside, rather than inside, Qwen's registered project
workspace.

## Observed evidence and hypothesis

The frozen v50 r11 chat records Qwen's cwd and registered project workspace as
`<run>/workspace`, while the first `run_shell_command` sent its `directory` as
the parent `<run>`. Qwen rejected it as outside its registered workspaces. The
same command then tried `/root`; both calls were prevalidation failures. r10
shows the same initial parent-root rejection, followed by an omitted
`directory` that Qwen ran at its default workspace and that created
`<run>/workspace/work` successfully.

The runner already starts Qwen with `cd "$W"`; hooks bind `$PWD` to that same
workspace. v50 explains that `<SKILL_BASE_DIR>` is absolute but does not say
that it locates executables only while relative state paths are rooted in the
Qwen project workspace. The missing invariant induces invalid directory guesses.

## Required change

Create v51 by copying v50 without altering v50. Add one runtime instruction:

- `<SKILL_BASE_DIR>` selects immutable executable paths only.
- The default registered Qwen project workspace anchors `./work`, `./corpus`
  and `./.qwen`.
- For `run_shell_command`, omit `directory`; never derive it from the skill
  base, its parent, the run root or `/root`. An explicit directory is valid only
  for a known child of the registered workspace.

Register v51's digest in the immutable version registry. v50 must retain
digest `0f95b5a59dcc41500168d3f2dc6df3e237b8c68e9d3666bf181716983d902ee7`.

## Verification

Run the version-contract test to verify v50's unchanged digest and that v51 is
registered, immutable and contains the workspace invariant. Extend the existing
provider-free actual-Qwen loopback replay so an absolute external
`checkpoint.py` with `resume --work ./work` and no `directory` executes in the
default project workspace and leaves a receipt in `workspace/work`. The fixture
does not contact a provider.

## Non-goals

Do not add a `pwd` step, broaden registered workspace access to the run root,
weaken Qwen's directory validation, change lifecycle/monitor behavior, or run a
provider or qualification. Clear/reseed semantics remain unchanged.
