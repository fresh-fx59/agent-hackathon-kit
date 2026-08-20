# Task 1 report: atomic run-state contract

## RED

Wrote `test_run_state.py` first, then ran:

```text
python3 -m unittest cases/06-dev-logging/sherlock/tools/tests/test_run_state.py -v
```

Result: FAIL during import with `ModuleNotFoundError: No module named 'run_state'`.

## GREEN

Implemented the standard-library-only state module and ran:

```text
python3 -m unittest cases/06-dev-logging/sherlock/tools/tests/test_run_state.py -v && python3 -m py_compile cases/06-dev-logging/sherlock/measure/run_state.py && git diff --check
```

Result: 5 tests passed (`OK`), compilation passed, and `git diff --check` reported no errors.

## Files changed

- `cases/06-dev-logging/sherlock/measure/run_state.py`: atomic snapshot replacement, locked append-only JSONL events, schema normalization, secret-shaped value rejection, and `set`/`event` CLI.
- `cases/06-dev-logging/sherlock/tools/tests/test_run_state.py`: tests for replacement, append-only behavior, concurrent readers, secret rejection without echo, and CLI operation.

## Self-review

- Snapshots are written to a same-directory temporary file, flushed, fsynced, and atomically replaced; temporary files are cleaned up.
- Event rows are serialized under an advisory `fcntl.flock`, flushed, and fsynced; prior bytes are not rewritten.
- Both APIs normalize all required snapshot keys and generate timestamps/PID locally.
- Secret-shaped strings are rejected before persistence, and the exception intentionally omits the rejected value.
- No credentials or model response text are accepted as a special field or emitted by diagnostics.

## Concerns

The secret-shape detector is intentionally conservative and may reject benign strings containing common credential prefixes. CLI values are strings except `attempt`, which is converted to an integer.

## Commit

`c2ae4d45b5292667c1e8684d39a69d05a1f7f4a1`
