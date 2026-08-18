# prompts/

One file per dataset id. `run-bench.sh` reads `prompts/$SHERLOCK_DATASET.txt` and
refuses to run a dataset that has none — a corpus without its own question would be
scored against the answer to a different question.

`{CORPUS}` is substituted with the corpus directory at run time.

**The four security datasets are SYMLINKS to `security.txt`, on purpose.** The
negative control (`fleet-negative`: real production logs, heavily attacked, never
compromised) has to be asked *exactly* the same question as the compromised corpora,
or the comparison is rigged — a clean corpus given a gentler prompt proves nothing.
The symlinks make "identical" a property of the filesystem instead of a promise.

`bench649` keeps its historical outage prompt inline in `run-bench.sh`, because five
scored runs are already published against that exact wording.
