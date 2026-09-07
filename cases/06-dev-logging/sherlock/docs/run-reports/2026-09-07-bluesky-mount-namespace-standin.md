# BlueSky mount-namespace isolation stand-in

Held-out BlueSky r3 is invalid: its model used a broad glob below
`/home/claude-developer/hack` and then read a historical Winevtx worklist. The
trigger was an observed producer/gate mismatch: no `work/rules.tsv` was emitted,
`triagecheck` required it, and the model searched for rules globally. This note
records the filesystem boundary for the next diagnostic; it does not change v52
content or claim that the rules contract is repaired.

`eval/bench/run-isolated-direct-qwen.sh` runs the existing direct helper inside
`unshare --mount`. It makes propagation private, bind-mounts the approved corpus
into an otherwise staged fresh root, overlays a private tmpfs on
`/home/claude-developer/hack`, and recursively bind-mounts only the staged root
back under that path. Qwen, the secret wrapper, and system runtime stay outside
the hidden hack tree; no host mount table is changed.

The target-host provider-free fake-runner stand-in passed. In the namespace it
observed:

```
cwd=/home/claude-developer/hack/sherlock-isolated-stage-IpKAzU
corpus=/home/claude-developer/hack/sherlock-isolated-stage-IpKAzU/corpus-source
skill=/home/claude-developer/hack/sherlock-isolated-stage-IpKAzU/skills/v52
old=denied
```

The fake runner required the approved `BlueSkyRansomware.jsonl`, supplied skill,
and absence of the prior absolute Winevtx r2 worklist. It exited zero. `bash -n`
and `git diff --check` also passed. This establishes the access boundary only;
the subsequent paid run must retain raw identity, corpus, exit, and report
evidence separately.
