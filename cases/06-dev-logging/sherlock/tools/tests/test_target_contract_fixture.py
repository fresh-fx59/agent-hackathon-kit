#!/usr/bin/env python3
"""Regression contract for the deterministic Sherlock paid-run probe."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


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


def no_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def run_json(name, command):
    done = subprocess.run(command, text=True, capture_output=True)
    try:
        payload = json.loads(done.stdout, object_pairs_hook=no_duplicate_json_keys)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AssertionError("gate did not emit JSON: %s\n%s" % (command, done.stderr)) from exc
    if type(payload) is not dict or "error" in payload:
        raise AssertionError("%s gate emitted malformed/error payload: %r" % (name, payload))
    blocking = payload.get("blocking")
    defects = payload.get("defects", [])
    citations = payload.get("citations", [])
    if type(blocking) is not int or blocking < 0 or type(defects) is not list or type(citations) is not list:
        raise AssertionError("%s gate emitted invalid payload types" % name)
    if any(type(item) is not dict or type(item.get("defect")) is not str for item in defects) or \
            any(type(item) is not dict or type(item.get("verdict")) is not str for item in citations):
        raise AssertionError("%s gate emitted invalid defect/citation entries" % name)
    if done.returncode not in (0, 1) or (done.returncode == 0) != (blocking == 0) or \
            (blocking == 0 and defects):
        raise AssertionError("%s gate exited %s without a valid blocking payload" %
                             (name, done.returncode))
    return {"name": name, "path": command[1], "returncode": done.returncode, "payload": payload}


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
            ("reportcheck", ["python3", str(V44 / "reportcheck.py"), str(report), "--json"]),
            ("citecheck", ["python3", str(V44 / "citecheck.py"), str(report), "--corpus", str(citation_corpus),
                            "--require-quote", "--json"]),
            ("statecheck", ["python3", str(V44 / "statecheck.py"), "--corpus", str(gate_corpus),
                            "--report", str(report), "--json"]),
            ("triagecheck", ["python3", str(V44 / "triagecheck.py"), "--worklist", str(ledger),
                             "--corpus", str(gate_corpus), "--json"]),
        ]
        results = [run_json(name, command) for name, command in commands]
    oracle = ORACLE.audit_report(report, corpus, expectations)
    classes = []
    for result in results:
        payload = result["payload"]
        classes.extend(item["defect"] for item in payload.get("defects", []))
        for citation in payload.get("citations", []):
            if citation.get("verdict") in {"no-quote", "wrong-content", "out-of-range"}:
                classes.append("citation_" + citation["verdict"].replace("-", "_"))
    classes.extend(oracle["failures"])
    order = ["assertion_unlabelled", "citation_no_quote", "citation_wrong_content",
             "citation_out_of_range", "external_predicate_includes_local_dash"]
    return {"classes": sorted(set(classes), key=lambda item: (order.index(item)
            if item in order else len(order), item)),
            "blocking": sum(result["payload"].get("blocking", len(result["payload"].get("defects", [])))
                            for result in results) + len(oracle["failures"]),
            "gates": results,
            "gate_payloads": [result["payload"] for result in results]}


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
        source_before = {name: self._file_hash(self.source / name)
                         for name in ("Security.jsonl", "System.jsonl")}
        first = FIXTURE.build_fixture(self.source, self.one, self.recipe, 4401)
        second = FIXTURE.build_fixture(self.source, self.two, self.recipe, 4401)
        self.assertEqual(first["output_tree_sha256"], second["output_tree_sha256"])
        self.assertEqual(first["expectations_sha256"], second["expectations_sha256"])
        self.assertEqual(first["outputs"]["Security.jsonl"]["lines"], [1, 2, 3])
        self.assertEqual((self.one / "Security.jsonl").read_bytes(),
                         (self.source / "Security.jsonl").read_bytes())
        self.assertEqual((self.one / "System.jsonl").read_bytes(),
                         (self.source / "System.jsonl").read_bytes())
        self.assertEqual(first["outputs"]["Security.jsonl"]["sha256"],
                         self._file_hash(self.one / "Security.jsonl"))
        self.assertEqual(source_before, {name: self._file_hash(self.source / name)
                                         for name in source_before})

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
        result = run_contract_checks(self.canonical, self.one, expectations)
        self.assertEqual(result["blocking"], 0)
        self.assertEqual(result["classes"], [])
        self.assertEqual([gate["name"] for gate in result["gates"]],
                         ["reportcheck", "citecheck", "statecheck", "triagecheck"])
        self.assertEqual([gate["path"] for gate in result["gates"]],
                         [str(V44 / (name + ".py")) for name in
                          ("reportcheck", "citecheck", "statecheck", "triagecheck")])
        self.assertTrue(all(gate["returncode"] == 0 for gate in result["gates"]))

    def _recipe_value(self):
        return json.loads(self.recipe.read_text(encoding="utf-8"))

    def _source_copy(self, name):
        copied = self.temp / name
        shutil.copytree(self.source, copied)
        return copied

    def _file_hash(self, path):
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_adversarial_destination_table_is_non_destructive(self):
        for case in ("equal", "child", "ancestor", "existing-file", "existing-directory",
                     "existing-symlink"):
            with self.subTest(case=case):
                source = self._source_copy("source-" + case)
                source_hash = self._file_hash(source / "Security.jsonl")
                if case == "equal":
                    destination = source
                elif case == "child":
                    destination = source / "new-fixture"
                elif case == "ancestor":
                    destination = source.parent
                elif case == "existing-file":
                    destination = self.temp / (case + ".out")
                    destination.write_text("marker", encoding="utf-8")
                elif case == "existing-directory":
                    destination = self.temp / (case + ".out")
                    destination.mkdir()
                    (destination / "marker").write_text("marker", encoding="utf-8")
                else:
                    target = self.temp / (case + ".target")
                    target.mkdir()
                    destination = self.temp / (case + ".out")
                    os.symlink(target, destination)
                with self.assertRaises(FIXTURE.ContractError):
                    FIXTURE.build_fixture(source, destination, self.recipe, 4401)
                self.assertTrue(source.is_dir())
                self.assertEqual(self._file_hash(source / "Security.jsonl"), source_hash)
                if case.startswith("existing"):
                    self.assertTrue(os.path.lexists(destination))
                if case == "existing-directory":
                    self.assertEqual((destination / "marker").read_text(encoding="utf-8"), "marker")

    def test_existing_destination_and_rename_failure_leave_everything_intact(self):
        source = self._source_copy("rename-source")
        destination = self.temp / "rename-destination"
        destination.mkdir()
        marker = destination / "marker"
        marker.write_text("keep", encoding="utf-8")
        before = self._file_hash(source / "System.jsonl")
        with mock.patch.object(FIXTURE.os, "replace", side_effect=OSError("injected rename failure")):
            with self.assertRaises(FIXTURE.ContractError):
                FIXTURE.build_fixture(source, destination, self.recipe, 4401)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self._file_hash(source / "System.jsonl"), before)
        new_destination = self.temp / "new-rename-destination"
        with mock.patch.object(FIXTURE.os, "replace", side_effect=OSError("injected rename failure")):
            with self.assertRaises(FIXTURE.ContractError):
                FIXTURE.build_fixture(source, new_destination, self.recipe, 4401)
        self.assertFalse(os.path.lexists(new_destination))
        self.assertEqual(self._file_hash(source / "System.jsonl"), before)

    def test_adversarial_recipe_and_source_table_is_rejected(self):
        bad_recipes = []
        for field, value in (("schema", True), ("dataset", "")):
            recipe = self._recipe_value()
            recipe[field] = value
            bad_recipes.append((field, recipe))
        for value in (True, "1", 1.5, 0):
            recipe = self._recipe_value()
            recipe["ranges"][0]["start"] = value
            bad_recipes.append(("start-%r" % value, recipe))
        for value in (True, "4401", 1.5):
            recipe = self._recipe_value()
            bad_recipes.append(("seed-%r" % value, recipe, value))
        recipe = self._recipe_value()
        recipe["required_shapes"]["authentication"]["extra"] = "x"
        bad_recipes.append(("extra-shape-key", recipe))
        recipe = self._recipe_value()
        recipe["ranges"][1]["destination"] = "Security.jsonl"
        bad_recipes.append(("canonical-collision", recipe))
        recipe = self._recipe_value()
        recipe["ranges"][1]["destination"] = "Security.jsonl/child"
        bad_recipes.append(("prefix-collision", recipe))
        recipe = self._recipe_value()
        recipe["ranges"][1]["destination"] = "\u0000"
        bad_recipes.append(("control-destination", recipe))
        for entry in bad_recipes:
            case, recipe = entry[:2]
            seed = entry[2] if len(entry) == 3 else 4401
            with self.subTest(case=case):
                with self.assertRaises(FIXTURE.ContractError):
                    FIXTURE.build_fixture(self._source_copy("recipe-" + case),
                                          self.temp / ("out-" + case), recipe, seed)
        duplicate = self.temp / "duplicate-recipe.json"
        duplicate.write_text(self.recipe.read_text(encoding="utf-8").replace(
            '"schema": 1,', '"schema": 1, "schema": 1,'), encoding="utf-8")
        with self.assertRaises(FIXTURE.ContractError):
            FIXTURE.build_fixture(self._source_copy("duplicate-recipe"), self.temp / "duplicate-out",
                                  duplicate, 4401)
        symlink_source = self._source_copy("symlink-source")
        (symlink_source / "Security-real.jsonl").write_bytes(
            (symlink_source / "Security.jsonl").read_bytes())
        (symlink_source / "Security.jsonl").unlink()
        os.symlink(symlink_source / "Security-real.jsonl", symlink_source / "Security.jsonl")
        with self.assertRaises(FIXTURE.ContractError):
            FIXTURE.build_fixture(symlink_source, self.temp / "symlink-out", self.recipe, 4401)
        duplicate_jsonl = self._source_copy("duplicate-jsonl")
        rows = duplicate_jsonl.joinpath("Security.jsonl").read_text(encoding="utf-8").splitlines()
        rows[0] = rows[0].replace('"IpAddress":"203.0.113.7"',
                                  '"IpAddress":"203.0.113.7","IpAddress":"198.51.100.9"')
        duplicate_jsonl.joinpath("Security.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaises(FIXTURE.ContractError):
            FIXTURE.build_fixture(duplicate_jsonl, self.temp / "duplicate-jsonl-out", self.recipe, 4401)

    def test_builder_detects_source_mutation_and_staged_hash_corruption(self):
        source = self._source_copy("mutation-source")
        original_verify_sources = FIXTURE._verify_source_hashes

        def mutate_source_then_verify(*args):
            source.joinpath("Security.jsonl").write_text("{}\n", encoding="utf-8")
            return original_verify_sources(*args)

        with mock.patch.object(FIXTURE, "_verify_source_hashes", side_effect=mutate_source_then_verify):
            with self.assertRaises(FIXTURE.ContractError):
                FIXTURE.build_fixture(source, self.temp / "mutation-out", self.recipe, 4401)

        source = self._source_copy("staging-source")
        original_verify_staging = FIXTURE._verify_staging

        def corrupt_stage_then_verify(staging, *args):
            (Path(staging) / "Security.jsonl").write_bytes(b"corrupt\n")
            return original_verify_staging(staging, *args)

        with mock.patch.object(FIXTURE, "_verify_staging", side_effect=corrupt_stage_then_verify):
            with self.assertRaises(FIXTURE.ContractError):
                FIXTURE.build_fixture(source, self.temp / "staging-out", self.recipe, 4401)

    def test_oracle_adversarial_table_rejects_wording_and_structure_bypasses(self):
        manifest = FIXTURE.build_fixture(self.source, self.one, self.recipe, 4401)
        expectations = self.one / manifest["expectations"]
        canonical = self.canonical.read_text(encoding="utf-8")
        variants = {
            "reworded-local-placeholder": canonical.replace(
                'external_ips=["198.51.100.9","203.0.113.7"]',
                'external_ips=["-","198.51.100.9","203.0.113.7"]'),
            "extra-id-after-verdict": canonical + "\n## F-EXTRA-1\n- [!PROVEN] extra\n",
            "values-outside-finding": canonical.replace(
                'external_ips=["198.51.100.9","203.0.113.7"]', 'external_ips=[]', 1),
            "extra-label": canonical.replace("## F-AUTH-EXTERNAL\n", "## F-AUTH-EXTERNAL\n- [!REPORTED] wrong\n", 1),
            "timeline-negation": canonical.replace("authentication_before_inventory",
                                                    "authentication_preceded_neither"),
            "fake-corpus-paths": canonical.replace("Security.jsonl", "fakea.log").replace(
                "System.jsonl", "fakeb.log"),
            "missing-field": canonical.replace('citation_files=["Security.jsonl"]\n', "", 1),
            "duplicate-field": canonical.replace('citation_files=["Security.jsonl"]\n',
                                                   'citation_files=["Security.jsonl"]\n'
                                                   'citation_files=["Security.jsonl"]\n', 1),
        }
        for case, content in variants.items():
            with self.subTest(case=case):
                report = self.temp / (case + ".md")
                report.write_text(content, encoding="utf-8")
                self.assertFalse(ORACLE.audit_report(report, self.one, expectations)["accepted"])

    def test_gate_json_wrapper_fails_closed_on_malformed_or_inconsistent_status(self):
        for completed in (
                SimpleNamespace(returncode=1, stdout='{"blocking": 0}', stderr=""),
                SimpleNamespace(returncode=0, stdout="not json", stderr="bad"),
                SimpleNamespace(returncode=0, stdout='{"error": "bad"}', stderr="")):
            with self.subTest(stdout=completed.stdout):
                with mock.patch("subprocess.run", return_value=completed):
                    with self.assertRaises(AssertionError):
                        run_json("test-gate", ["python3", "test-gate.py"])

    def test_round_two_destination_identity_and_reserved_control_table(self):
        for destination in ("probe-expectations.json", "probe-fixture-manifest.json",
                            "probe-expectations.json/child", "probe-fixture-manifest.json/child"):
            recipe = self._recipe_value()
            recipe["ranges"][0]["destination"] = destination
            with self.subTest(destination=destination):
                with self.assertRaises(FIXTURE.ContractError):
                    FIXTURE.build_fixture(self._source_copy("reserved-" + destination.replace("/", "-")),
                                          self.temp / ("reserved-out-" + destination.replace("/", "-")),
                                          recipe, 4401)

    def test_round_two_case_alias_destination_is_rejected_on_casefolding_filesystems(self):
        source = self._source_copy("CaseSource")
        alias = source.parent / "casesource"
        if not os.path.samefile(source, alias):
            self.skipTest("filesystem is case-sensitive")
        with self.assertRaises(FIXTURE.ContractError):
            FIXTURE.build_fixture(source, alias / "out", self.recipe, 4401)
        self.assertFalse((source / "out").exists())

    def test_round_three_symlink_alias_destination_is_rejected_on_casefolding_filesystems(self):
        source = self._source_copy("CaseSource")
        inner = source / "inner"
        inner.mkdir()
        alias = source.parent / "casesource"
        if not os.path.samefile(source, alias):
            self.skipTest("filesystem is case-sensitive")
        external = self.temp / "external-link"
        os.symlink(alias / "inner", external)
        with self.assertRaises(FIXTURE.ContractError):
            FIXTURE.build_fixture(source, external / "out", self.recipe, 4401)
        self.assertFalse((inner / "out").exists())

    def test_round_two_source_snapshot_is_single_read_and_last_boundary_checked(self):
        source = self._source_copy("snapshot-source")
        recipe = self._recipe_value()
        recipe["ranges"].append({"source": "Security.jsonl", "start": 1, "end": 1,
                                 "destination": "Security-copy.jsonl"})
        original_read = FIXTURE._read_regular_bytes
        reads = []

        def count_reads(root, relative, error):
            reads.append(relative)
            return original_read(root, relative, error)

        with mock.patch.object(FIXTURE, "_read_regular_bytes", side_effect=count_reads):
            manifest = FIXTURE.build_fixture(source, self.temp / "snapshot-out", recipe, 4401)
        self.assertEqual(reads.count("Security.jsonl"), 1)
        hashes = [row["source_sha256"] for row in manifest["ranges"]
                  if row["source"] == "Security.jsonl"]
        self.assertEqual(hashes, [manifest["source_sha256"]["Security.jsonl"]] * 2)

        source = self._source_copy("last-boundary-source")
        original_verify = FIXTURE._verify_source_hashes

        def mutate_after_verify(*args):
            value = original_verify(*args)
            source.joinpath("Security.jsonl").write_text("{}\n", encoding="utf-8")
            return value

        with mock.patch.object(FIXTURE, "_verify_source_hashes", side_effect=mutate_after_verify):
            with self.assertRaises(FIXTURE.ContractError):
                FIXTURE.build_fixture(source, self.temp / "last-boundary-out", self.recipe, 4401)

    def test_round_three_source_hash_check_detects_restored_metadata_change(self):
        source = self._source_copy("restored-metadata-source")
        path = source / "Security.jsonl"
        original_verify_staging = FIXTURE._verify_staging

        def change_source_after_staging(*args):
            value = original_verify_staging(*args)
            before = path.stat()
            path.write_bytes(path.read_bytes().replace(b"198.51.100.9", b"198.51.100.8"))
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            return value

        with mock.patch.object(FIXTURE, "_verify_staging", side_effect=change_source_after_staging):
            with self.assertRaises(FIXTURE.ContractError):
                FIXTURE.build_fixture(source, self.temp / "restored-metadata-out", self.recipe, 4401)

    def test_round_two_timeline_relation_is_validated_and_derived(self):
        cases = (("earlier", "09", "03", False), ("equal", "10", "00", False),
                 ("later", "11", "03", True))
        for name, hour, minute, accepted in cases:
            with self.subTest(name=name):
                source = self._source_copy("timeline-" + name)
                system = source / "System.jsonl"
                system.write_text(system.read_text(encoding="utf-8").replace("T10:03", "T%s:%s" % (hour, minute)).
                                  replace("T10:04", "T%s:%s" % (hour, "01" if minute == "00" else "04")), encoding="utf-8")
                destination = self.temp / ("timeline-out-" + name)
                if not accepted:
                    with self.assertRaises(FIXTURE.ContractError):
                        FIXTURE.build_fixture(source, destination, self.recipe, 4401)
                    continue
                manifest = FIXTURE.build_fixture(source, destination, self.recipe, 4401)
                expectation_path = destination / manifest["expectations"]
                expectation = json.loads(expectation_path.read_text(encoding="utf-8"))
                self.assertEqual(expectation["timeline"]["relation"], "authentication_before_inventory")
                expectation["timeline"]["relation"] = "authentication_after_inventory"
                expectation_path.write_text(json.dumps(expectation), encoding="utf-8")
                self.assertFalse(ORACLE.audit_report(self.canonical, destination, expectation_path)["accepted"])
        malformed = self._source_copy("timeline-malformed")
        path = malformed / "System.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace("2026-08-30T10:03:00Z", "not-a-time").
                        replace("2026-08-30T10:04:00Z", "also-not-a-time"),
                        encoding="utf-8")
        with self.assertRaises(FIXTURE.ContractError):
            FIXTURE.build_fixture(malformed, self.temp / "timeline-malformed-out", self.recipe, 4401)

    def test_round_two_oracle_excludes_fences_and_detects_indented_candidates(self):
        manifest = FIXTURE.build_fixture(self.source, self.one, self.recipe, 4401)
        expectations = self.one / manifest["expectations"]
        canonical = self.canonical.read_text(encoding="utf-8")
        for name, report_text in (
                ("fenced", "```markdown\n" + canonical + "\n```\n"),
                ("indented-extra", canonical + "\n ## F-EXTRA-1\n- [!PROVEN] extra\n"),
                ("long-open-short-close", "````\n```\n" + canonical + "\n````\n"),
                ("mixed-fence", "```\n~~~\n" + canonical + "\n```\n")):
            report = self.temp / (name + ".md")
            report.write_text(report_text, encoding="utf-8")
            self.assertFalse(ORACLE.audit_report(report, self.one, expectations)["accepted"])

    def test_round_two_gate_json_is_whole_strict_and_consistent(self):
        for completed in (
                SimpleNamespace(returncode=0, stdout='{"blocking":0} trailing', stderr=""),
                SimpleNamespace(returncode=0, stdout='{"blocking":1,"blocking":0}', stderr=""),
                SimpleNamespace(returncode=0, stdout='{"blocking":0,"defects":[{"defect":"x"}]}', stderr=""),
                SimpleNamespace(returncode=0, stdout='{"blocking":1,"defects":[{"defect":"x"}]}', stderr="")):
            with self.subTest(stdout=completed.stdout):
                with mock.patch("subprocess.run", return_value=completed):
                    with self.assertRaises(AssertionError):
                        run_json("test-gate", ["python3", "test-gate.py"])

    def test_round_three_gate_statuses_are_documented_and_not_reclassified(self):
        clear = SimpleNamespace(returncode=0, stdout='{"blocking":0,"defects":[],"citations":[]}', stderr="")
        blocked = SimpleNamespace(returncode=1, stdout='{"blocking":1,"defects":[],"citations":[]}', stderr="")
        for completed in (clear, blocked):
            with self.subTest(returncode=completed.returncode):
                with mock.patch("subprocess.run", return_value=completed):
                    self.assertEqual(run_json("test-gate", ["python3", "test-gate.py"])["returncode"],
                                     completed.returncode)
        for returncode in (2, -9):
            completed = SimpleNamespace(returncode=returncode,
                                        stdout='{"blocking":1,"defects":[],"citations":[]}', stderr="")
            with self.subTest(returncode=returncode):
                with mock.patch("subprocess.run", return_value=completed):
                    with self.assertRaises(AssertionError):
                        run_json("test-gate", ["python3", "test-gate.py"])

    def test_round_three_verdict_is_last_visible_heading(self):
        manifest = FIXTURE.build_fixture(self.source, self.one, self.recipe, 4401)
        expectations = self.one / manifest["expectations"]
        canonical = self.canonical.read_text(encoding="utf-8")
        for suffix in ("\n## Notes\ncontent\n", "\n### Appendix\ncontent\n"):
            report = self.temp / ("verdict-" + str(len(suffix)) + ".md")
            report.write_text(canonical + suffix, encoding="utf-8")
            self.assertFalse(ORACLE.audit_report(report, self.one, expectations)["accepted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
