#!/usr/bin/env python3
"""Regression contract for the deterministic Sherlock paid-run probe."""
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "eval" / "bench"
V44 = ROOT / "skills" / "v44" / "tools"
FIXTURE = None
ORACLE = None


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURE = load_module("contract_probe_fixture", BENCH / "contract-probe-fixture.py")
ORACLE = load_module("target_contract_oracle", BENCH / "target-contract-oracle.py")


def run_json(command):
    done = subprocess.run(command, text=True, capture_output=True)
    try:
        payload, _end = json.JSONDecoder().raw_decode(done.stdout[done.stdout.index("{"):])
    except json.JSONDecodeError as exc:
        raise AssertionError("gate did not emit JSON: %s\n%s" % (command, done.stderr)) from exc
    return done.returncode, payload


def minimum_ledger(work, corpus):
    """Make the v44 ledger using the shipped worklist authority."""
    path = work / "worklist.tsv"
    (corpus / "ledger.log").write_text(
        "\n".join(["2026-08-30T10:00:00Z INFO routine health check ok"] * 80 +
                  ["2026-08-30T10:00:01Z ALERT singular contract ledger anomaly"]) + "\n",
        encoding="utf-8")
    done = subprocess.run(
        ["python3", str(V44 / "logmap.py"), str(corpus), "--out", str(work),
         "--worklist-cap", "10", "--rate-cap", "0", "--jobs", "1"],
        text=True, capture_output=True)
    if done.returncode:
        raise AssertionError(done.stderr or done.stdout)
    row = next(line.split("\t") for line in path.read_text(encoding="utf-8").splitlines()
               if line and not line.startswith("#"))
    reference = row[3]
    done = subprocess.run(
        ["python3", str(V44 / "worklist.py"), "next", "--work", str(work)],
        text=True, capture_output=True)
    if done.returncode:
        raise AssertionError(done.stderr or done.stdout)
    done = subprocess.run(
        ["python3", str(V44 / "worklist.py"), "verdict", "--work", str(work),
         "--id", row[0], "--cell", "N n=1 %s «singular contract ledger anomaly»" % reference],
        text=True, capture_output=True)
    if done.returncode:
        raise AssertionError(done.stderr or done.stdout)
    return path


def run_contract_checks(report, corpus, expectations):
    """Aggregate shipped gate results with the independent fixture oracle."""
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        gate_corpus = scratch / "corpus"
        shutil.copytree(corpus, gate_corpus)
        citation_corpus = scratch / "citation-corpus"
        citation_corpus.mkdir()
        for filename in ("Security.jsonl", "System.jsonl"):
            shutil.copy2(corpus / filename, citation_corpus / filename)
        work = scratch / "work"
        work.mkdir()
        ledger = minimum_ledger(work, gate_corpus)
        commands = [
            ["python3", str(V44 / "reportcheck.py"), str(report), "--json"],
            ["python3", str(V44 / "citecheck.py"), str(report), "--corpus", str(citation_corpus),
             "--require-quote", "--json"],
            ["python3", str(V44 / "statecheck.py"), "--corpus", str(gate_corpus),
             "--report", str(report), "--json"],
            ["python3", str(V44 / "triagecheck.py"), "--worklist", str(ledger),
             "--corpus", str(gate_corpus), "--json"],
        ]
        results = [run_json(command) for command in commands]
    oracle = ORACLE.audit_report(report, corpus, expectations)
    classes = []
    for _returncode, payload in results:
        classes.extend(item["defect"] for item in payload.get("defects", []))
        for citation in payload.get("citations", []):
            if citation.get("verdict") in {"no-quote", "wrong-content", "out-of-range"}:
                classes.append("citation_" + citation["verdict"].replace("-", "_"))
    classes.extend(oracle["failures"])
    order = ["assertion_unlabelled", "citation_no_quote", "citation_wrong_content",
             "citation_out_of_range", "external_predicate_includes_local_dash"]
    return {"classes": sorted(set(classes), key=lambda item: (order.index(item)
            if item in order else len(order), item)),
            "blocking": sum(payload.get("blocking", len(payload.get("defects", [])))
                            for _returncode, payload in results) + len(oracle["failures"]),
            "gate_payloads": [payload for _returncode, payload in results]}


class TargetContractFixtureTest(unittest.TestCase):
    def setUp(self):
        self.source = ROOT / "tools" / "tests" / "fixtures" / "target-contract-source"
        self.recipe = BENCH / "probe" / "recipe.json"
        self.reports = ROOT / "tools" / "tests" / "fixtures" / "target-contract-reports"
        self.broken = self.reports / "broken.md"
        self.canonical = self.reports / "canonical.md"
        self.temp = Path(tempfile.mkdtemp())
        self.one = self.temp / "one"
        self.two = self.temp / "two"

    def tearDown(self):
        shutil.rmtree(self.temp)

    def test_fixture_is_reproducible_and_line_addressable(self):
        first = FIXTURE.build_fixture(self.source, self.one, self.recipe, 4401)
        second = FIXTURE.build_fixture(self.source, self.two, self.recipe, 4401)
        self.assertEqual(first["output_tree_sha256"], second["output_tree_sha256"])
        self.assertEqual(first["expectations_sha256"], second["expectations_sha256"])
        self.assertEqual(first["outputs"]["Security.jsonl"]["lines"], [1, 2, 3])

    def test_broken_report_has_exactly_five_expected_defect_classes(self):
        manifest = FIXTURE.build_fixture(self.source, self.one, self.recipe, 4401)
        result = run_contract_checks(self.broken, self.one,
                                     self.one / manifest["expectations"])
        self.assertEqual(result["classes"], [
            "assertion_unlabelled", "citation_no_quote", "citation_wrong_content",
            "citation_out_of_range", "external_predicate_includes_local_dash"])

    def test_canonical_report_passes_oracle_and_all_four_gates(self):
        manifest = FIXTURE.build_fixture(self.source, self.one, self.recipe, 4401)
        expectations = self.one / manifest["expectations"]
        oracle = ORACLE.audit_report(self.canonical, self.one, expectations)
        self.assertTrue(oracle["accepted"])
        self.assertEqual(run_contract_checks(self.canonical, self.one, expectations)["blocking"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
