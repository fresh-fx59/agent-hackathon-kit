#!/usr/bin/env python3
"""Part A staging (vault spec 2026-09-27-replay-journalcheck-and-repair-loop):
`stage --mode replay --seed-run RUN` copies RUN/out/investigate/stream*.jsonl into the
trusted `seed` dir (-> control/trusted/seed/), each file pinned to RUN/seal.json
files[...], read-only, with manifest.json. A stream whose bytes differ from the seal,
or a missing seal entry, fails staging. Also: 6 repair phases on_fail continue, 7200 s."""
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import citebench  # noqa: E402

BASE = HERE / "base-run-spec-v60.json"


def make_seed(root, tamper=False, seal_missing=False, retry=False):
    seed = Path(root) / "seed-run"
    inv = seed / "out" / "investigate"
    inv.mkdir(parents=True)
    (seed / "work" / "work").mkdir(parents=True)
    (seed / "work" / "work" / "checkpoint.jsonl").write_text('{"boundary_seq": 1}\n')
    files = {}
    names = ["stream.jsonl"] + (["stream.a1.jsonl"] if retry else [])
    for n in names:
        p = inv / n
        p.write_text('{"message": {"content": []}}\n' + n + "\n")
        files["out/investigate/" + n] = hashlib.sha256(p.read_bytes()).hexdigest()
    if seal_missing:
        files.pop("out/investigate/stream.jsonl")
    (seed / "seal.json").write_text(json.dumps({"schema": 1, "files": files}))
    if tamper:
        with open(inv / "stream.jsonl", "a") as fh:
            fh.write("altered\n")
    return seed


class SeedStreamStaging(unittest.TestCase):
    def stage(self, seed):
        out = Path(tempfile.mkdtemp(prefix="cbseed-")) / "s"
        citebench.main(["stage", "--mode", "replay", "--pkg", "v62", "--run-id", "r",
                        "--out", str(out), "--base-spec", str(BASE), "--seed-run", str(seed)])
        return out

    def test_stage_copies_and_pins_stream(self):
        seed = make_seed(tempfile.mkdtemp(), retry=True)
        out = self.stage(seed)
        sd = out / "trusted-seed"
        man = json.loads((sd / "manifest.json").read_text())
        seal = json.loads((seed / "seal.json").read_text())["files"]
        self.assertEqual([m["file"] for m in man], ["stream.a1.jsonl", "stream.jsonl"])
        for m in man:
            data = (sd / m["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), m["sha256"])
            self.assertEqual(m["sha256"], seal["out/investigate/" + m["file"]])
            self.assertEqual(stat.S_IMODE(os.stat(sd / m["file"]).st_mode), 0o444)
        spec = json.loads((out / "run-spec.json").read_text())
        self.assertIn({"name": "seed", "src": str(sd)}, spec["task"]["trusted"])
        self.assertTrue((out / "wd" / "work" / "checkpoint.jsonl").is_file())

    def test_tampered_stream_fails_staging(self):
        seed = make_seed(tempfile.mkdtemp(), tamper=True)
        with self.assertRaises(SystemExit) as cm:
            self.stage(seed)
        self.assertIn("sha256", str(cm.exception.code))

    def test_missing_seal_entry_fails_staging(self):
        seed = make_seed(tempfile.mkdtemp(), seal_missing=True)
        with self.assertRaises(SystemExit) as cm:
            self.stage(seed)
        self.assertIn("seal", str(cm.exception.code))

    def test_validate_gate_paths_checks_manifest_files(self):
        seed = make_seed(tempfile.mkdtemp())
        out = self.stage(seed)
        spec = json.loads((out / "run-spec.json").read_text())
        dirs = {t["name"]: t["src"] for t in spec["task"]["trusted"]}
        os.chmod(out / "trusted-seed", 0o755)
        os.remove(out / "trusted-seed" / "stream.jsonl")
        with self.assertRaises(citebench.GateConfigError):
            citebench.validate_gate_paths(spec, dirs)

    def test_spec_six_repair_phases_continue_7200(self):
        spec = citebench.build_spec(citebench.load_base_spec(BASE), mode="replay", pkg="v62",
                                    run_id="t", stage_dir="/s")
        rep = [p for p in spec["phases"] if p["id"].startswith("repair")]
        self.assertEqual(len(rep), 6)
        self.assertTrue(all(p["gate"]["on_fail"] == "continue" for p in rep))
        self.assertEqual(spec["limits"]["run_wall_s"], 7200)


if __name__ == "__main__":
    unittest.main()
