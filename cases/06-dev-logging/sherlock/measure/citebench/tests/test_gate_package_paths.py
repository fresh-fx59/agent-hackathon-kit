#!/usr/bin/env python3
"""Gate-argv package path must always point at the staged (--pkg) package,
never at the base spec's package (base-run-spec-v60.json is named/pinned to
v60, and is reused as the template for every --pkg).

RED against pre-fix citebench.py: `_swap_pkg` does two literal string
replacements — "/%s/" % old (needs a *trailing* slash) and "skills/%s" % old.
Neither matches a bare, unslashed trailing path segment such as
`--package $QWR_TRUSTED_DIR/v60` (end of the argv string, no trailing "/"),
so that one argument silently keeps referencing the base package (v60) while
every other v60 path in the same argv gets swapped to the target --pkg. Since
only the *tested* package is staged (trusted-<pkg>/), the gate then looks for
finalize.py/journalcheck.py under a package directory that was never staged
at all, and every repair round fails before the report is ever evaluated.

Also covers: stage()-time validation that every literal $QWR_TRUSTED_DIR/<x>
path referenced by a gate argv actually exists in the staged trusted dir,
so a stale/mistyped package path fails loudly at stage/validate time (a
distinct `gate_error`), before any paid provider call — never silently as
a normal `gate_failed` after the model already ran and got charged.
"""
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
BASE = HERE / "base-run-spec-v60.json"
sys.path.insert(0, str(BENCH))
import citebench  # noqa: E402


def _gate_argv_strings(spec):
    out = []
    for p in spec["phases"]:
        gate = p.get("gate")
        if gate:
            out.extend(gate["argv"])
    return out


class GatePackagePathSwap(unittest.TestCase):
    def test_no_gate_argv_references_the_base_package_after_swap(self):
        base = citebench.load_base_spec(str(BASE))
        spec = citebench.build_spec(base, mode="replay", pkg="v63", run_id="t3",
                                    stage_dir="/tmp/citebench-t3", base_pkg="v60")
        for s in _gate_argv_strings(spec):
            # every whole-word "v60" path segment must have been swapped to v63
            leftover = re.findall(r"(?<![A-Za-z0-9_])v60(?![A-Za-z0-9_])", s)
            self.assertFalse(leftover, "gate argv still references base pkg v60: %r" % s)
            self.assertIn("v63", s if "hostgate.py" in s or "buildindex.py" in s else s + " v63")

    def test_package_flag_specifically_points_at_the_staged_pkg(self):
        base = citebench.load_base_spec(str(BASE))
        spec = citebench.build_spec(base, mode="replay", pkg="v63", run_id="t4",
                                    stage_dir="/tmp/citebench-t4", base_pkg="v60")
        for s in _gate_argv_strings(spec):
            m = re.search(r"--package (\S+)", s)
            if m:
                self.assertTrue(m.group(1).endswith("/v63"),
                                "--package must point at the staged pkg v63, got %r in %r"
                                % (m.group(1), s))


class ValidateGatePathsAtStageTime(unittest.TestCase):
    def test_validate_rejects_a_gate_argv_path_missing_from_the_staged_trusted_dir(self):
        # A spec whose gate argv references a package dir that was never staged
        # (e.g. the pre-fix leftover /v60/ path) must be rejected loudly, as a
        # distinct condition from a normal report-gate failure.
        self.assertTrue(hasattr(citebench, "validate_gate_paths"),
                        "citebench.py must expose validate_gate_paths(spec, trusted_dirs)")
        spec = {"phases": [{"id": "repair", "gate": {"argv": [
            "bash", "-c",
            "python3 $QWR_TRUSTED_DIR/v60/tools/hostgate.py --package $QWR_TRUSTED_DIR/v60"]}}]}
        trusted_dirs = {"v63": str(HERE)}  # only v63 staged; v60 missing on purpose
        with self.assertRaises(citebench.GateConfigError):
            citebench.validate_gate_paths(spec, trusted_dirs)

    def test_validate_passes_when_every_gate_path_exists(self):
        spec = {"phases": [{"id": "repair", "gate": {"argv": [
            "bash", "-c",
            "python3 $QWR_TRUSTED_DIR/v63/tools/hostgate.py --package $QWR_TRUSTED_DIR/v63"]}}]}
        trusted_dirs = {"v63": str(BENCH.parents[0] / "citebench")}  # exists
        # tools/hostgate.py need not exist under this stand-in dir for the
        # package-dir check, but the file-path check below should still run;
        # use a dir that actually contains a tools/hostgate.py-like layout:
        trusted_dirs = {"v63": str(HERE)}
        (HERE / "tools").mkdir(exist_ok=True)
        (HERE / "tools" / "hostgate.py").write_text("# stub\n")
        try:
            citebench.validate_gate_paths(spec, trusted_dirs)  # must not raise
        finally:
            (HERE / "tools" / "hostgate.py").unlink()
            (HERE / "tools").rmdir()


if __name__ == "__main__":
    unittest.main()
