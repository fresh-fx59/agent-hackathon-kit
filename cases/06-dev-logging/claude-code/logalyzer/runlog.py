import hashlib, json, time
from datetime import date
from pathlib import Path

class RunLog:
    def __init__(self, case_dir, argv):
        self.case_dir = Path(case_dir)
        digest = hashlib.sha256((" ".join(argv) + date.today().isoformat()).encode()).hexdigest()[:12]
        self.run_id = "run-%s" % digest
        self.run_dir = self.case_dir / "artifacts" / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._events = open(self.run_dir / "events.jsonl", "a", encoding="utf-8")
        self._t0 = time.monotonic()

    def event(self, kind, **fields):
        rec = {"kind": kind, "ts_monotonic": round(time.monotonic() - self._t0, 6)}
        rec.update(fields)
        self._events.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def summary(self, **fields):
        self._events.close()
        docs = self.case_dir / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        line = {"run_id": self.run_id}
        line.update(fields)
        with open(docs / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
