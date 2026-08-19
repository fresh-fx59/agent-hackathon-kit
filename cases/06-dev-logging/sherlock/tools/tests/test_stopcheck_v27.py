#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SHERLOCK = ROOT / "cases" / "06-dev-logging" / "sherlock"
V27 = SHERLOCK / "skills" / "v27"
STOPCHECK = V27 / "tools" / "stopcheck.py"
LOGMAP = V27 / "tools" / "logmap.py"

NEEDLE = "2036-02-03T04:05:06Z type=SERVICE_START component=demo unit=put code=200"
CONTROL = "2036-02-03T04:06:00Z component=demo state=quiet code=200"


def report(good=True):
    quote = NEEDLE if good else "WRONG"
    return "\n".join([
        "# Отчёт",
        "## Находки",
        "### Н-1 · проверяемое наблюдение",
        "что сломано: проверка держит адрес.",
        "улики: host/app.log:1 «%s»" % quote,
        "чем опровергал: host/app.log:2 «%s»" % CONTROL,
        "атрибуция: не установлена",
        "исход: успех",
        "## Отклонённые кандидаты",
        "### К-1 · штатный фон",
        "что выглядело как причина: похожий запуск.",
        "улики: host/app.log:2 «%s»" % CONTROL,
        "исход: норма",
        "## Покрытие",
        "| path | status | detail |",
        "| --- | --- | --- |",
        "| host/app.log | наблюдение | host/app.log:1 «%s» |" % NEEDLE,
        "| host/empty.log | пусто | байт=0 |",
    ]) + "\n"


def worklist(status="D Н-1"):
    return ("# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
            "g001\t%s\trare\thost/app.log:1\tn=1\t%s\n"
            "g002\tN host/app.log:2 «%s»\trare\thost/app.log:2\tn=1\t%s\n"
            % (status, NEEDLE, CONTROL, CONTROL))


def load_stopcheck_module():
    spec = importlib.util.spec_from_file_location("stopcheck_v27_under_test", STOPCHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Workspace:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.corpus = self.root / "corpus"
        self.work = self.root / "work"
        self.marker = self.root / ".sherlock" / "active.json"

    def __enter__(self):
        (self.corpus / "host").mkdir(parents=True)
        (self.corpus / "host" / "app.log").write_text(NEEDLE + "\n" + CONTROL + "\n", encoding="utf-8")
        (self.corpus / "host" / "empty.log").write_text("", encoding="utf-8")
        self.work.mkdir()
        (self.work / "rules.tsv").write_text("", encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.tmp.cleanup()

    def activate(self, out=None, corpus=None, mode="single", worklists=None, hosts=None):
        self.marker.parent.mkdir(exist_ok=True)
        data = {
            "version": 27,
            "active": True,
            "workspace": str(self.root.resolve()),
            "skill_root": str(V27.resolve()),
            "corpus": str((corpus or self.corpus).resolve()),
            "out": str((out or self.work).resolve()),
            "mode": mode,
            "worklists": worklists or ["worklist.tsv"],
        }
        if mode == "multi":
            data["hosts_manifest"] = "hosts.tsv"
            data["hosts"] = hosts or [
                {"name": "alpha", "worklist": "worklist-alpha.tsv", "map": "map-alpha.txt"},
                {"name": "beta", "worklist": "worklist-beta.tsv", "map": "map-beta.txt"},
            ]
        self.marker.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")

    def finish_files(self, report_text=None, wl_text=None):
        (self.work / "report.md").write_text(report_text if report_text is not None else report(), encoding="utf-8")
        (self.work / "worklist.tsv").write_text(wl_text if wl_text is not None else worklist(), encoding="utf-8")

    def stop(self, message=None):
        payload = {"cwd": str(self.root), "hook_event_name": "Stop",
                   "last_assistant_message": report() if message is None else message}
        return self.stop_payload(payload)

    def stop_payload(self, payload):
        env = os.environ.copy()
        env["QWEN_SKILL_ROOT"] = str(V27)
        p = subprocess.run([sys.executable, str(STOPCHECK)], input=json.dumps(payload),
                           text=True, capture_output=True, cwd=str(self.root), env=env)
        self.assert_hook_json(p)
        return json.loads(p.stdout)

    @staticmethod
    def assert_hook_json(p):
        assert p.returncode == 0, p.stderr
        assert p.stderr == "", p.stderr
        json.loads(p.stdout)


class StopcheckV27(unittest.TestCase):
    def test_inactive_workspace_allows_stop(self):
        with Workspace() as w:
            out = w.stop("narrative")
            self.assertEqual("allow", out["decision"])

    def test_corrupt_or_external_marker_is_ignored_or_blocked_safely(self):
        with Workspace() as w:
            w.marker.parent.mkdir(exist_ok=True)
            w.marker.write_text("{not-json", encoding="utf-8")
            self.assertEqual("allow", w.stop("anything")["decision"])
        with Workspace() as w:
            outside = Path(tempfile.gettempdir()) / "sherlock-outside-work"
            w.activate(out=outside)
            out = w.stop("anything")
            self.assertEqual("block", out["decision"])
            self.assertIn("outside the workspace", out["reason"])

    def test_unresolved_rows_block_stop(self):
        with Workspace() as w:
            w.activate()
            w.finish_files(wl_text=worklist(status="?"))
            out = w.stop(report())
            self.assertEqual("block", out["decision"])
            self.assertIn("unresolved", out["reason"])

    def test_missing_report_blocks_stop(self):
        with Workspace() as w:
            w.activate()
            (w.work / "worklist.tsv").write_text(worklist(), encoding="utf-8")
            out = w.stop(report())
            self.assertEqual("block", out["decision"])
            self.assertIn("work/report.md", out["reason"])

    def test_triage_failure_blocks_stop(self):
        with Workspace() as w:
            w.activate()
            w.finish_files(wl_text=worklist(status="N #R1 фон"))
            (w.work / "rules.tsv").write_text(
                "R1\tзапись~SERVICE_START\tN\tтокен<=24\tпроекция не правило\n",
                encoding="utf-8")
            out = w.stop(report())
            self.assertEqual("block", out["decision"])
            self.assertIn("triagecheck", out["reason"])

    def test_cite_failure_blocks_stop(self):
        with Workspace() as w:
            w.activate()
            bad = report(good=False)
            w.finish_files(report_text=bad)
            out = w.stop(bad)
            self.assertEqual("block", out["decision"])
            self.assertIn("citecheck", out["reason"])

    def test_final_message_must_equal_verified_report(self):
        with Workspace() as w:
            w.activate()
            w.finish_files()
            out = w.stop("Отчёт готов в файле")
            self.assertEqual("block", out["decision"])
            self.assertIn("exactly equal", out["reason"])

    def test_clean_pass_retires_marker(self):
        with Workspace() as w:
            w.activate()
            w.finish_files()
            out = w.stop(report())
            self.assertEqual("allow", out["decision"])
            self.assertFalse(w.marker.exists())

    def test_logmap_reactivates_after_retirement(self):
        with Workspace() as w:
            w.activate()
            w.finish_files()
            self.assertEqual("allow", w.stop(report())["decision"])
            p = subprocess.run([sys.executable, str(LOGMAP), str(w.corpus), "--out", "./work2"],
                               cwd=str(w.root), capture_output=True, text=True)
            self.assertEqual(0, p.returncode, p.stderr)
            self.assertTrue(w.marker.exists())
            data = json.loads(w.marker.read_text(encoding="utf-8"))
            self.assertTrue(data["active"])
            self.assertEqual(str((w.root / "work2").resolve()), data["out"])
            self.assertEqual("single", data["mode"])
            self.assertEqual(["worklist.tsv"], data["worklists"])

    def test_multi_host_uses_marker_manifest_not_unresolved_combined_ledger(self):
        with Workspace() as w:
            w.activate(mode="multi", worklists=["worklist-alpha.tsv", "worklist-beta.tsv"])
            (w.work / "report.md").write_text(report(), encoding="utf-8")
            (w.work / "worklist.tsv").write_text(worklist(status="?"), encoding="utf-8")
            for host in ("alpha", "beta"):
                text = worklist() if host == "alpha" else worklist().replace("g001", "g003").replace("g002", "g004")
                (w.work / ("worklist-%s.tsv" % host)).write_text(text, encoding="utf-8")
                (w.work / ("map-%s.txt" % host)).write_text("map\n", encoding="utf-8")
            (w.work / "hosts.tsv").write_text(
                "# хост\tфайлов\tстрок\tиз них темп\tне вошло (форм)\tрабочий список\tкарта\tсвёрнуто файлов\n"
                "alpha\t1\t2\t0\t0\tworklist-alpha.tsv\tmap-alpha.txt\t0\n"
                "beta\t1\t2\t0\t0\tworklist-beta.tsv\tmap-beta.txt\t0\n",
                encoding="utf-8")
            out = w.stop(report())
            self.assertEqual("allow", out["decision"])
            self.assertFalse(w.marker.exists())

    def test_symlinked_sherlock_parent_is_not_read_or_retired(self):
        with Workspace() as w:
            external = w.root / "external-state"
            external.mkdir()
            (external / "active.json").write_text(json.dumps({
                "version": 27, "active": True, "workspace": str(w.root.resolve()),
                "skill_root": str(V27.resolve()), "corpus": str(w.corpus.resolve()),
                "out": str(w.work.resolve()), "mode": "single", "worklists": ["worklist.tsv"],
            }), encoding="utf-8")
            os.symlink(external, w.root / ".sherlock")
            out = w.stop("anything")
            self.assertEqual("allow", out["decision"])
            self.assertTrue((external / "active.json").exists())

    def test_marker_manifest_tampering_blocks_current_investigation(self):
        with Workspace() as w:
            w.activate(worklists=["../evil.tsv"])
            w.finish_files()
            out = w.stop(report())
            self.assertEqual("block", out["decision"])
            self.assertIn("unsafe worklist", out["reason"])
        with Workspace() as w:
            w.activate(mode="single", worklists=["worklist-alpha.tsv"])
            (w.work / "worklist-alpha.tsv").write_text(worklist(), encoding="utf-8")
            w.finish_files()
            out = w.stop(report())
            self.assertEqual("block", out["decision"])
            self.assertIn("single-host", out["reason"])

    def test_rules_symlink_blocks_without_following_target(self):
        with Workspace() as w:
            w.activate()
            w.finish_files()
            victim = w.root / "rules-victim.tsv"
            victim.write_text("R1\tзапись~SERVICE_START\tN\tтокен<=24\toutside\n", encoding="utf-8")
            (w.work / "rules.tsv").unlink()
            os.symlink(victim, w.work / "rules.tsv")
            out = w.stop(report())
            self.assertEqual("block", out["decision"])
            self.assertIn("rules.tsv is unsafe", out["reason"])

    def test_active_marker_wrong_json_types_never_fail_open_when_current(self):
        cases = [
            ("version", True),
            ("version", False),
            ("version", "27"),
            ("version", None),
            ("version", 27.0),
            ("version", [27]),
            ("version", {"n": 27}),
            ("active", "true"),
            ("workspace", 7),
            ("skill_root", 7),
            ("corpus", [str(Path("corpus"))]),
            ("out", {"path": "work"}),
            ("mode", ["single"]),
            ("worklists", "worklist.tsv"),
            ("worklists", ["worklist.tsv", 7]),
            ("hosts_manifest", 7),
            ("hosts", [7]),
            ("hosts", ["x"]),
            ("hosts", [{"name": 7, "worklist": "worklist-alpha.tsv", "map": "map-alpha.txt"}]),
            ("hosts", [{"name": "alpha", "worklist": 7, "map": "map-alpha.txt"}]),
            ("hosts", [{"name": "alpha", "worklist": "worklist-alpha.tsv", "map": 7}]),
            ("hosts", [{"name": "alpha", "worklist": "../evil.tsv", "map": "map-alpha.txt"}]),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                with Workspace() as w:
                    w.activate(mode="multi", worklists=["worklist-alpha.tsv"], hosts=[
                        {"name": "alpha", "worklist": "worklist-alpha.tsv", "map": "map-alpha.txt"},
                    ])
                    data = json.loads(w.marker.read_text(encoding="utf-8"))
                    data[field] = value
                    w.marker.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
                    w.finish_files()
                    (w.work / "worklist-alpha.tsv").write_text(worklist(), encoding="utf-8")
                    (w.work / "map-alpha.txt").write_text("map\n", encoding="utf-8")
                    (w.work / "hosts.tsv").write_text(
                        "# хост\tфайлов\tстрок\tиз них темп\tне вошло (форм)\tрабочий список\tкарта\tсвёрнуто файлов\n"
                        "alpha\t1\t2\t0\t0\tworklist-alpha.tsv\tmap-alpha.txt\t0\n",
                        encoding="utf-8")
                    out = w.stop(report())
                    self.assertNotEqual("Sherlock stopcheck failed open", out["reason"])
                    self.assertEqual("block", out["decision"])
                    if field == "version":
                        self.assertIn("invalid version", out["reason"])

    def test_active_marker_non_object_hook_json_never_fails_open(self):
        cases = ([], 42, None)
        for payload in cases:
            with self.subTest(payload=repr(payload)):
                with Workspace() as w:
                    w.activate()
                    w.finish_files()
                    out = w.stop_payload(payload)
                    self.assertNotEqual("Sherlock stopcheck failed open", out["reason"])
                    self.assertEqual("block", out["decision"])
                    self.assertTrue(w.marker.exists())

    def test_active_marker_malformed_cwd_never_fails_open(self):
        cases = (True, False, 7, 2.5, ["x"], {"path": "x"}, "", "   ", "\n", "abc\ndef", "abc\rdef", "\x00", "abc\x00def")
        for cwd in cases:
            with self.subTest(cwd=repr(cwd)):
                with Workspace() as w:
                    w.activate()
                    w.finish_files()
                    out = w.stop_payload({
                        "cwd": cwd,
                        "hook_event_name": "Stop",
                        "last_assistant_message": "not the verified report",
                    })
                    self.assertNotEqual("Sherlock stopcheck failed open", out["reason"])
                    self.assertEqual("block", out["decision"])
                    self.assertTrue(w.marker.exists())

    def test_getcwd_failure_blocks_without_generic_fail_open(self):
        stopcheck = load_stopcheck_module()
        old_stdin = sys.stdin
        old_getcwd = stopcheck.os.getcwd
        try:
            def failing_getcwd():
                raise OSError("simulated getcwd failure")

            stopcheck.os.getcwd = failing_getcwd
            sys.stdin = io.StringIO(json.dumps({
                "cwd": True,
                "hook_event_name": "Stop",
                "last_assistant_message": "anything",
            }))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = stopcheck.main()
            out = json.loads(buf.getvalue())
            self.assertEqual(0, rc)
            self.assertEqual("block", out["decision"])
            self.assertIn("cannot determine workspace", out["reason"])
            self.assertNotEqual("Sherlock stopcheck failed open", out["reason"])
        finally:
            stopcheck.os.getcwd = old_getcwd
            sys.stdin = old_stdin

    def test_selected_and_fallback_real_failure_blocks_without_generic_fail_open(self):
        stopcheck = load_stopcheck_module()
        old_stdin = sys.stdin
        old_real = stopcheck.real
        old_getcwd = stopcheck.os.getcwd
        try:
            def failing_real(_path):
                raise ValueError("simulated real failure")

            stopcheck.real = failing_real
            stopcheck.os.getcwd = lambda: "/fallback"
            sys.stdin = io.StringIO(json.dumps({
                "cwd": "/selected",
                "hook_event_name": "Stop",
                "last_assistant_message": "anything",
            }))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = stopcheck.main()
            out = json.loads(buf.getvalue())
            self.assertEqual(0, rc)
            self.assertEqual("block", out["decision"])
            self.assertIn("cannot determine workspace", out["reason"])
            self.assertNotEqual("Sherlock stopcheck failed open", out["reason"])
        finally:
            stopcheck.real = old_real
            stopcheck.os.getcwd = old_getcwd
            sys.stdin = old_stdin

    def test_exact_non_bool_historical_integer_version_is_ignored(self):
        with Workspace() as w:
            w.activate()
            data = json.loads(w.marker.read_text(encoding="utf-8"))
            data["version"] = 26
            w.marker.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
            out = w.stop("anything")
            self.assertEqual("allow", out["decision"])
            self.assertEqual("Sherlock inactive", out["reason"])

    def test_marker_out_symlink_parent_is_rejected_before_report_lookup(self):
        with Workspace() as w:
            real_work = w.root / "real-work"
            real_work.mkdir()
            (real_work / "worklist.tsv").write_text(worklist(), encoding="utf-8")
            (real_work / "report.md").write_text(report(), encoding="utf-8")
            link = w.root / "linked-work"
            os.symlink(real_work, link)
            w.activate(out=real_work)
            data = json.loads(w.marker.read_text(encoding="utf-8"))
            data["out"] = str(link)
            w.marker.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
            out = w.stop(report())
            self.assertEqual("block", out["decision"])
            self.assertIn("outside the workspace", out["reason"])


class LogmapStateV27(unittest.TestCase):
    def test_active_marker_temp_symlink_does_not_overwrite_target(self):
        with Workspace() as w:
            w.marker.parent.mkdir(exist_ok=True)
            victim = w.root / "victim"
            victim.write_text("do not touch", encoding="utf-8")
            os.symlink(victim, str(w.marker) + ".tmp")
            p = subprocess.run([sys.executable, str(LOGMAP), str(w.corpus), "--out", "./work"],
                               cwd=str(w.root), capture_output=True, text=True)
            self.assertEqual(0, p.returncode, p.stderr)
            self.assertEqual("do not touch", victim.read_text(encoding="utf-8"))
            self.assertTrue((Path(str(w.marker) + ".tmp")).is_symlink())
            data = json.loads(w.marker.read_text(encoding="utf-8"))
            self.assertEqual("single", data["mode"])

    def test_multi_to_single_rerun_retires_prior_host_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            corpus = root / "corpus"
            for host in ("alpha", "beta"):
                d = corpus / host / "logs"
                d.mkdir(parents=True)
                (d / "app.log").write_text(NEEDLE + "\n" + CONTROL + "\n", encoding="utf-8")
            out_dir = root / "work"
            p = subprocess.run([sys.executable, str(LOGMAP), str(corpus), "--out", str(out_dir)],
                               cwd=str(root), capture_output=True, text=True)
            self.assertEqual(0, p.returncode, p.stderr)
            self.assertTrue((out_dir / "hosts.tsv").exists())
            self.assertTrue(list(out_dir.glob("worklist-*.tsv")))
            p = subprocess.run([sys.executable, str(LOGMAP), str(corpus), "--out", str(out_dir), "--single-host"],
                               cwd=str(root), capture_output=True, text=True)
            self.assertEqual(0, p.returncode, p.stderr)
            self.assertFalse((out_dir / "hosts.tsv").exists())
            self.assertFalse(list(out_dir.glob("worklist-*.tsv")))
            self.assertFalse(list(out_dir.glob("map-*.txt")))
            marker = json.loads((root / ".sherlock" / "active.json").read_text(encoding="utf-8"))
            self.assertEqual("single", marker["mode"])
            self.assertEqual(["worklist.tsv"], marker["worklists"])

    def test_generated_output_symlinks_do_not_overwrite_targets(self):
        cases = [
            ("single", "worklist.tsv"),
            ("single", "axis3.tsv"),
            ("single", "map.txt"),
            ("multi", "worklist-region-a__node-1.tsv"),
            ("multi", "map-region-a__node-1.txt"),
            ("multi", "worklist-region-b__node-2.tsv"),
            ("multi", "map-region-b__node-2.txt"),
            ("multi", "hosts.tsv"),
        ]
        for mode, name in cases:
            with self.subTest(mode=mode, name=name):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    corpus = root / "corpus"
                    if mode == "single":
                        (corpus / "host").mkdir(parents=True)
                        (corpus / "host" / "app.log").write_text(NEEDLE + "\n" + CONTROL + "\n", encoding="utf-8")
                        argv = [sys.executable, str(LOGMAP), str(corpus), "--out", "work", "--single-host", "--jobs", "1"]
                    else:
                        for host in ("region-a/node-1", "region-b/node-2"):
                            d = corpus / host / "logs"
                            d.mkdir(parents=True)
                            (d / "app.log").write_text(NEEDLE + "\n" + CONTROL + "\n", encoding="utf-8")
                        argv = [sys.executable, str(LOGMAP), str(corpus), "--out", "work", "--host-depth", "2", "--jobs", "1"]
                    out_dir = root / "work"
                    out_dir.mkdir()
                    victim = root / ("victim-" + name.replace("/", "_"))
                    victim.write_text("do not touch", encoding="utf-8")
                    os.symlink(victim, out_dir / name)
                    p = subprocess.run(argv, cwd=str(root), capture_output=True, text=True)
                    self.assertEqual("do not touch", victim.read_text(encoding="utf-8"))
                    if p.returncode == 0:
                        self.assertTrue((out_dir / name).is_file(), p.stderr)
                        self.assertFalse((out_dir / name).is_symlink(), name)
                    else:
                        self.assertTrue((out_dir / name).is_symlink(), p.stderr)
                        self.assertIn("hosts.tsv", p.stderr + p.stdout)

    def test_untrusted_prior_hosts_manifest_fails_without_deleting_it(self):
        cases = ("malformed", "symlink", "broken-symlink")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    corpus = root / "corpus" / "host"
                    corpus.mkdir(parents=True)
                    (corpus / "app.log").write_text(NEEDLE + "\n", encoding="utf-8")
                    out_dir = root / "work"
                    out_dir.mkdir()
                    hosts = out_dir / "hosts.tsv"
                    victim = root / "victim-hosts.tsv"
                    victim.write_text("do not touch", encoding="utf-8")
                    if case == "malformed":
                        hosts.write_text("not\tenough\n", encoding="utf-8")
                    elif case == "symlink":
                        os.symlink(victim, hosts)
                    else:
                        os.symlink(root / "missing-hosts.tsv", hosts)
                    p = subprocess.run([sys.executable, str(LOGMAP), str(root / "corpus"),
                                        "--out", str(out_dir), "--single-host", "--jobs", "1"],
                                       cwd=str(root), capture_output=True, text=True)
                    self.assertNotEqual(0, p.returncode)
                    self.assertTrue(os.path.lexists(hosts))
                    self.assertEqual("do not touch", victim.read_text(encoding="utf-8"))


class StopcheckTimeoutV27(unittest.TestCase):
    def test_source_host_control_record_cannot_change_selector_result(self):
        stopcheck = load_stopcheck_module()
        with Workspace() as w:
            w.finish_files()
            wl_a = w.work / "worklist-alpha.tsv"
            wl_b = w.work / "worklist-beta.tsv"
            wl_a.write_text(
                "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
                "# sherlock-host\tbeta\n"
                "g001\tN #R1 injected host\trare\thost/app.log:1\tn=1\t%s\n" % NEEDLE,
                encoding="utf-8")
            wl_b.write_text(
                "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
                "g002\tN host/app.log:2 «%s»\trare\thost/app.log:2\tn=1\t%s\n"
                % (CONTROL, CONTROL), encoding="utf-8")
            (w.work / "rules.tsv").write_text(
                "R1\tхост=beta\tN\tтокен<=24\tinjected control must not select\n"
                "+R1\tg001\thost/app.log:1\t«%s»\tправило\n" % NEEDLE,
                encoding="utf-8")
            direct = subprocess.run([sys.executable, str(V27 / "tools" / "triagecheck.py"),
                                     "--worklist", str(wl_a), "--rules", str(w.work / "rules.tsv"),
                                     "--corpus", str(w.corpus)],
                                    capture_output=True, text=True)
            self.assertEqual(0, direct.returncode, direct.stdout + direct.stderr)
            calls = []
            old = stopcheck.run_child

            def fake_run_child(argv, deadline):
                calls.append(argv)
                self.fail("reserved source control records must block before child invocation")

            stopcheck.run_child = fake_run_child
            try:
                reason = stopcheck.check_children(
                    str(w.corpus.resolve()), str(w.work.resolve()), str((w.work / "report.md").resolve()),
                    [{"path": str(wl_a.resolve()), "host": "alpha"},
                     {"path": str(wl_b.resolve()), "host": "beta"}],
                    str(V27.resolve()), str(w.root.resolve()), time.monotonic() + stopcheck.TOTAL_TIMEOUT)
            finally:
                stopcheck.run_child = old
            self.assertIn("reserved host control", reason)
            self.assertEqual([], calls)

    def test_reserved_host_predicate_matches_trieage_consumer_grammar(self):
        stopcheck = load_stopcheck_module()

        def triage_recognizes(line):
            if not line.startswith("#"):
                return False
            cols = line.rstrip("\n").split("\t")
            return len(cols) == 2 and cols[0] == "# sherlock-host"

        cases = [
            "# sherlock-host\tbeta\n",
            "# sherlock-host\t\n",
            "# sherlock-host\t   \n",
            "# sherlock-host\tbeta\textra\n",
            "# sherlock_host\tbeta\n",
            "# sherlock-host \tbeta\n",
            " # sherlock-host\tbeta\n",
            "# SHERLOCK-HOST\tbeta\n",
            "# id\tвердикт\n",
            "g001\tD Н-1\n",
        ]
        for line in cases:
            with self.subTest(line=repr(line)):
                self.assertEqual(triage_recognizes(line), stopcheck.is_reserved_host_record(line))

    def test_blank_source_host_control_records_block_before_children(self):
        stopcheck = load_stopcheck_module()
        for payload in ("# sherlock-host\t\n", "# sherlock-host\t   \n"):
            with self.subTest(payload=repr(payload)):
                with Workspace() as w:
                    w.finish_files(wl_text=(
                        "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
                        + payload
                        + "g001\tD Н-1\trare\thost/app.log:1\tn=1\t%s\n" % NEEDLE))
                    calls = []
                    old = stopcheck.run_child

                    def fake_run_child(argv, deadline):
                        calls.append(argv)
                        self.fail("blank reserved source record must block before child invocation")

                    stopcheck.run_child = fake_run_child
                    try:
                        reason = stopcheck.check_children(
                            str(w.corpus.resolve()), str(w.work.resolve()), str((w.work / "report.md").resolve()),
                            [{"path": str((w.work / "worklist.tsv").resolve()), "host": "alpha"}],
                            str(V27.resolve()), str(w.root.resolve()), time.monotonic() + stopcheck.TOTAL_TIMEOUT)
                    finally:
                        stopcheck.run_child = old
                    self.assertIn("reserved host control", reason)
                    self.assertEqual([], calls)

    def test_triage_inert_three_column_host_comment_is_not_reserved(self):
        stopcheck = load_stopcheck_module()
        calls = []

        class Good:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run_child(argv, deadline):
            calls.append(argv)
            return Good()

        old = stopcheck.run_child
        stopcheck.run_child = fake_run_child
        try:
            with Workspace() as w:
                w.finish_files(wl_text=(
                    "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
                    "# sherlock-host\tbeta\tignored\n"
                    "g001\tD Н-1\trare\thost/app.log:1\tn=1\t%s\n" % NEEDLE))
                reason = stopcheck.check_children(
                    str(w.corpus.resolve()), str(w.work.resolve()), str((w.work / "report.md").resolve()),
                    [{"path": str((w.work / "worklist.tsv").resolve()), "host": "alpha"}],
                    str(V27.resolve()), str(w.root.resolve()), time.monotonic() + stopcheck.TOTAL_TIMEOUT)
        finally:
            stopcheck.run_child = old
        self.assertIsNone(reason)
        self.assertEqual(2, len(calls))

    def test_singleton_source_host_control_record_blocks_before_children(self):
        stopcheck = load_stopcheck_module()
        with Workspace() as w:
            w.finish_files(wl_text=(
                "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
                "# sherlock-host\tbeta\n"
                "g001\tD Н-1\trare\thost/app.log:1\tn=1\t%s\n" % NEEDLE))
            calls = []
            old = stopcheck.run_child

            def fake_run_child(argv, deadline):
                calls.append(argv)
                self.fail("singleton reserved source record must block before child invocation")

            stopcheck.run_child = fake_run_child
            try:
                reason = stopcheck.check_children(
                    str(w.corpus.resolve()), str(w.work.resolve()), str((w.work / "report.md").resolve()),
                    [str((w.work / "worklist.tsv").resolve())], str(V27.resolve()),
                    str(w.root.resolve()), time.monotonic() + stopcheck.TOTAL_TIMEOUT)
            finally:
                stopcheck.run_child = old
            self.assertIn("reserved host control", reason)
            self.assertEqual([], calls)

    def test_trusted_host_selector_empty_or_control_character_blocks_before_children(self):
        stopcheck = load_stopcheck_module()
        cases = ("", "   ", "alpha\rbeta", "alpha\nbeta", "alpha\tbeta", False, 0, [], {})
        for host in cases:
            with self.subTest(host=repr(host)):
                with Workspace() as w:
                    w.finish_files()
                    calls = []
                    old = stopcheck.run_child

                    def fake_run_child(argv, deadline):
                        calls.append(argv)
                        self.fail("unsafe trusted host selector must block before child invocation")

                    stopcheck.run_child = fake_run_child
                    try:
                        reason = stopcheck.check_children(
                            str(w.corpus.resolve()), str(w.work.resolve()), str((w.work / "report.md").resolve()),
                            [{"path": str((w.work / "worklist.tsv").resolve()), "host": host}],
                            str(V27.resolve()), str(w.root.resolve()), time.monotonic() + stopcheck.TOTAL_TIMEOUT)
                    finally:
                        stopcheck.run_child = old
                    self.assertIn("trusted host selector", reason)
                    self.assertEqual([], calls)

    def test_whole_hook_deadline_blocks_slow_pre_child_phase_promptly(self):
        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("SIGALRM/setitimer unavailable")
        stopcheck = load_stopcheck_module()
        old_total = stopcheck.TOTAL_TIMEOUT
        old_unresolved = stopcheck.unresolved_rows
        old_stdin = sys.stdin
        try:
            stopcheck.TOTAL_TIMEOUT = 0.15

            def slow_unresolved(_path, _deadline=None):
                time.sleep(0.35)
                return []

            stopcheck.unresolved_rows = slow_unresolved
            with Workspace() as w:
                w.activate()
                w.finish_files()
                sys.stdin = io.StringIO(json.dumps({
                    "cwd": str(w.root),
                    "hook_event_name": "Stop",
                    "last_assistant_message": report(),
                }))
                buf = io.StringIO()
                start = time.monotonic()
                with contextlib.redirect_stdout(buf):
                    rc = stopcheck.main()
                elapsed = time.monotonic() - start
            self.assertEqual(0, rc)
            out = json.loads(buf.getvalue())
            self.assertEqual("block", out["decision"])
            self.assertIn("deadline", out["reason"])
            self.assertLess(elapsed, 0.8)
        finally:
            sys.stdin = old_stdin
            stopcheck.unresolved_rows = old_unresolved
            stopcheck.TOTAL_TIMEOUT = old_total

    def test_signal_after_marker_unlink_does_not_become_deadline_block(self):
        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("SIGALRM/setitimer unavailable")
        stopcheck = load_stopcheck_module()
        old_retire = stopcheck.retire
        old_stdin = sys.stdin
        original_handler = signal.getsignal(signal.SIGALRM)
        original_timer = signal.getitimer(signal.ITIMER_REAL)

        def prior_handler(signum, frame):
            pass

        def unlink_then_signal(path, workspace, deadline=None):
            os.remove(path)
            signal.raise_signal(signal.SIGALRM)

        stopcheck.retire = unlink_then_signal
        try:
            signal.signal(signal.SIGALRM, prior_handler)
            signal.setitimer(signal.ITIMER_REAL, 0)
            with Workspace() as w:
                w.activate()
                w.finish_files()
                sys.stdin = io.StringIO(json.dumps({
                    "cwd": str(w.root),
                    "hook_event_name": "Stop",
                    "last_assistant_message": report(),
                }))
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = stopcheck.main()
                out = json.loads(buf.getvalue())
                self.assertEqual(0, rc)
                self.assertEqual("allow", out["decision"])
                self.assertEqual("Sherlock complete", out["reason"])
                self.assertFalse(w.marker.exists())
        finally:
            stopcheck.retire = old_retire
            sys.stdin = old_stdin
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original_handler)
            if original_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, original_timer[0], original_timer[1])

    def test_retire_runs_after_watchdog_handler_and_timer_are_restored(self):
        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("SIGALRM/setitimer unavailable")
        stopcheck = load_stopcheck_module()
        old_retire = stopcheck.retire
        old_stdin = sys.stdin
        original_handler = signal.getsignal(signal.SIGALRM)
        original_timer = signal.getitimer(signal.ITIMER_REAL)
        seen = {}

        def prior_handler(signum, frame):
            pass

        def recording_retire(path, workspace, deadline=None):
            seen["handler"] = signal.getsignal(signal.SIGALRM)
            seen["timer"] = signal.getitimer(signal.ITIMER_REAL)
            old_retire(path, workspace, deadline)

        try:
            signal.signal(signal.SIGALRM, prior_handler)
            signal.setitimer(signal.ITIMER_REAL, 5.0)
            stopcheck.retire = recording_retire
            with Workspace() as w:
                w.activate()
                w.finish_files()
                sys.stdin = io.StringIO(json.dumps({
                    "cwd": str(w.root),
                    "hook_event_name": "Stop",
                    "last_assistant_message": report(),
                }))
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = stopcheck.main()
                out = json.loads(buf.getvalue())
                self.assertEqual(0, rc)
                self.assertEqual("allow", out["decision"])
            self.assertIs(seen.get("handler"), prior_handler)
            self.assertLess(seen.get("timer", (0, 0))[0], 5.0)
            self.assertGreater(seen.get("timer", (0, 0))[0], 4.0)
        finally:
            stopcheck.retire = old_retire
            sys.stdin = old_stdin
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original_handler)
            if original_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, original_timer[0], original_timer[1])

    def test_watchdog_restores_handler_after_partial_setitimer_failure(self):
        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("SIGALRM/setitimer unavailable")
        stopcheck = load_stopcheck_module()
        original_handler = signal.getsignal(signal.SIGALRM)
        original_timer = signal.getitimer(signal.ITIMER_REAL)
        old_setitimer = stopcheck.signal.setitimer

        def prior_handler(signum, frame):
            pass

        def failing_setitimer(*args):
            raise OSError("simulated partial arm failure")

        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, prior_handler)
            stopcheck.signal.setitimer = failing_setitimer
            self.assertIsNone(stopcheck.arm_watchdog(1.0))
            self.assertIs(signal.getsignal(signal.SIGALRM), prior_handler)
        finally:
            stopcheck.signal.setitimer = old_setitimer
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original_handler)
            if original_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, original_timer[0], original_timer[1])

    def test_disarm_does_not_reschedule_expired_prior_one_shot_timer(self):
        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("SIGALRM/setitimer unavailable")
        stopcheck = load_stopcheck_module()
        original_handler = signal.getsignal(signal.SIGALRM)
        original_timer = signal.getitimer(signal.ITIMER_REAL)

        def prior_handler(signum, frame):
            pass

        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            old = (prior_handler, (0.1, 0.0), time.monotonic() - 0.2)
            stopcheck.disarm_watchdog(old)
            self.assertIs(signal.getsignal(signal.SIGALRM), prior_handler)
            self.assertEqual(0.0, signal.getitimer(signal.ITIMER_REAL)[0])
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original_handler)
            if original_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, original_timer[0], original_timer[1])

    def test_disarm_restores_periodic_timer_phase_after_elapsed_time(self):
        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("SIGALRM/setitimer unavailable")
        stopcheck = load_stopcheck_module()
        original_handler = signal.getsignal(signal.SIGALRM)
        original_timer = signal.getitimer(signal.ITIMER_REAL)

        def prior_handler(signum, frame):
            pass

        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            old = (prior_handler, (0.1, 0.2), time.monotonic() - 0.35)
            stopcheck.disarm_watchdog(old)
            restored_delay, restored_interval = signal.getitimer(signal.ITIMER_REAL)
            self.assertIs(signal.getsignal(signal.SIGALRM), prior_handler)
            self.assertAlmostEqual(0.2, restored_interval, delta=0.02)
            self.assertGreater(restored_delay, 0.12)
            self.assertLess(restored_delay, 0.18)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original_handler)
            if original_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, original_timer[0], original_timer[1])

    def test_watchdog_restores_prior_handler_and_deducted_timer_on_normal_path(self):
        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("SIGALRM/setitimer unavailable")
        stopcheck = load_stopcheck_module()
        original_handler = signal.getsignal(signal.SIGALRM)
        original_timer = signal.getitimer(signal.ITIMER_REAL)

        def prior_handler(signum, frame):
            raise AssertionError("prior timer should not fire during this test")

        try:
            signal.signal(signal.SIGALRM, prior_handler)
            signal.setitimer(signal.ITIMER_REAL, 5.0)
            old = stopcheck.arm_watchdog(1.0)
            time.sleep(0.05)
            stopcheck.disarm_watchdog(old)
            restored_delay = signal.getitimer(signal.ITIMER_REAL)[0]
            self.assertIs(signal.getsignal(signal.SIGALRM), prior_handler)
            self.assertLess(restored_delay, 5.0)
            self.assertGreater(restored_delay, 4.0)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original_handler)
            if original_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, original_timer[0], original_timer[1])

    def test_watchdog_restores_prior_handler_and_deducted_timer_on_timeout_path(self):
        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("SIGALRM/setitimer unavailable")
        stopcheck = load_stopcheck_module()
        original_handler = signal.getsignal(signal.SIGALRM)
        original_timer = signal.getitimer(signal.ITIMER_REAL)

        def prior_handler(signum, frame):
            raise AssertionError("restored prior timer fired")

        try:
            signal.signal(signal.SIGALRM, prior_handler)
            signal.setitimer(signal.ITIMER_REAL, 5.0)
            old = stopcheck.arm_watchdog(0.05)
            with self.assertRaises(stopcheck.DeadlineExceeded):
                time.sleep(0.2)
            stopcheck.disarm_watchdog(old)
            restored_delay = signal.getitimer(signal.ITIMER_REAL)[0]
            self.assertIs(signal.getsignal(signal.SIGALRM), prior_handler)
            self.assertLess(restored_delay, 5.0)
            self.assertGreater(restored_delay, 4.0)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original_handler)
            if original_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, original_timer[0], original_timer[1])

    def test_nested_host_rule_matches_per_host_and_composite_once(self):
        stopcheck = load_stopcheck_module()
        with Workspace() as w:
            nested = w.corpus / "region-a" / "node-1" / "logs"
            nested.mkdir(parents=True)
            (nested / "app.log").write_text(NEEDLE + "\n" + CONTROL + "\n", encoding="utf-8")
            other = w.corpus / "region-b" / "node-2" / "logs"
            other.mkdir(parents=True)
            (other / "app.log").write_text(CONTROL + "\n", encoding="utf-8")
            text = report().replace("host/app.log", "region-a/node-1/logs/app.log")
            (w.work / "report.md").write_text(text, encoding="utf-8")
            wl_a = w.work / "worklist-region-a__node-1.tsv"
            wl_b = w.work / "worklist-region-b__node-2.tsv"
            row_a = (
                "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
                "g001\tN #R1 host matched\trare\tregion-a/node-1/logs/app.log:1\tn=1\t%s\n" % NEEDLE)
            row_b = (
                "# id\tвердикт\tось\tссылка\tчастота\tзапись\n"
                "g002\tN region-b/node-2/logs/app.log:1 «%s»\trare\tregion-b/node-2/logs/app.log:1\tn=1\t%s\n"
                % (CONTROL, CONTROL))
            wl_a.write_text(row_a, encoding="utf-8")
            wl_b.write_text(row_b, encoding="utf-8")
            (w.work / "map-region-a__node-1.txt").write_text("map\n", encoding="utf-8")
            (w.work / "map-region-b__node-2.txt").write_text("map\n", encoding="utf-8")
            (w.work / "hosts.tsv").write_text(
                "# хост\tфайлов\tстрок\tиз них темп\tне вошло (форм)\tрабочий список\tкарта\tсвёрнуто файлов\n"
                "region-a/node-1\t1\t1\t0\t0\tworklist-region-a__node-1.tsv\tmap-region-a__node-1.txt\t0\n"
                "region-b/node-2\t1\t1\t0\t0\tworklist-region-b__node-2.tsv\tmap-region-b__node-2.txt\t0\n",
                encoding="utf-8")
            (w.work / "rules.tsv").write_text(
                "R1\tхост=region-a__node-1\tN\tтокен<=24\tnested host slug\n"
                "+R1\tg001\tregion-a/node-1/logs/app.log:1\t«%s»\tправило\n" % NEEDLE,
                encoding="utf-8")
            per_host = subprocess.run([sys.executable, str(V27 / "tools" / "triagecheck.py"),
                                       "--worklist", str(wl_a), "--rules", str(w.work / "rules.tsv"),
                                       "--corpus", str(w.corpus)],
                                      capture_output=True, text=True)
            self.assertEqual(0, per_host.returncode, per_host.stdout + per_host.stderr)
            calls = []
            old = stopcheck.run_child

            def recording_run_child(argv, deadline):
                calls.append(argv)
                return old(argv, deadline)

            stopcheck.run_child = recording_run_child
            try:
                reason = stopcheck.check_children(
                    str(w.corpus.resolve()), str(w.work.resolve()), str((w.work / "report.md").resolve()),
                    [{"path": str(wl_a.resolve()), "host": "region-a__node-1"},
                     {"path": str(wl_b.resolve()), "host": "region-b__node-2"}],
                    str(V27.resolve()), str(w.root.resolve()), time.monotonic() + stopcheck.TOTAL_TIMEOUT)
            finally:
                stopcheck.run_child = old
            self.assertIsNone(reason)
            self.assertEqual(2, len(calls))
            self.assertIn("triagecheck.py", calls[0][1])
            self.assertIn("citecheck.py", calls[1][1])

    def test_composite_ledger_rejects_duplicate_ids(self):
        stopcheck = load_stopcheck_module()
        with Workspace() as w:
            w.finish_files()
            wl1 = w.work / "worklist-alpha.tsv"
            wl2 = w.work / "worklist-beta.tsv"
            wl1.write_text(worklist(), encoding="utf-8")
            wl2.write_text(worklist(), encoding="utf-8")
            reason = stopcheck.check_children(
                str(w.corpus.resolve()), str(w.work.resolve()), str((w.work / "report.md").resolve()),
                [{"path": str(wl1.resolve()), "host": "alpha"},
                 {"path": str(wl2.resolve()), "host": "beta"}],
                str(V27.resolve()), str(w.root.resolve()), time.monotonic() + stopcheck.TOTAL_TIMEOUT)
            self.assertIn("duplicate worklist id", reason)

    def test_multi_host_ledgers_are_composed_for_one_triage_and_one_cite_run(self):
        stopcheck = load_stopcheck_module()
        calls = []

        class Good:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run_child(argv, deadline):
            calls.append(argv)
            self.assertGreater(deadline, time.monotonic())
            return Good()

        old = stopcheck.run_child
        stopcheck.run_child = fake_run_child
        try:
            with Workspace() as w:
                w.finish_files()
                wl1 = w.work / "worklist-alpha.tsv"
                wl2 = w.work / "worklist-beta.tsv"
                wl1.write_text(worklist(), encoding="utf-8")
                wl2.write_text(worklist().replace("g001", "g003").replace("g002", "g004"), encoding="utf-8")
                reason = stopcheck.check_children(
                    str(w.corpus.resolve()), str(w.work.resolve()), str((w.work / "report.md").resolve()),
                    [str(wl1.resolve()), str(wl2.resolve())], str(V27.resolve()),
                    str(w.root.resolve()), time.monotonic() + stopcheck.TOTAL_TIMEOUT)
                self.assertIsNone(reason)
        finally:
            stopcheck.run_child = old
        self.assertEqual(2, len(calls))
        self.assertIn("triagecheck.py", calls[0][1])
        self.assertIn("citecheck.py", calls[1][1])
        ledgers = {calls[0][calls[0].index("--worklist") + 1], calls[1][calls[1].index("--ledger") + 1]}
        self.assertEqual(1, len(ledgers))
        ledger = next(iter(ledgers))
        self.assertIn(".stopcheck-ledger-", Path(ledger).name)
        self.assertFalse(Path(ledger).exists())
        self.assertLess(stopcheck.TOTAL_TIMEOUT, 60)
        self.assertLessEqual(stopcheck.CHILD_TIMEOUT * 2 + 2, stopcheck.TOTAL_TIMEOUT)


class SkillMetadataV27(unittest.TestCase):
    def test_frontmatter_declares_qwen_stop_command_hook(self):
        text = (V27 / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        self.assertIsNotNone(m)
        fm = m.group(1)
        for needle in ("hooks:", "  Stop:", "    - hooks:",
                       "        - type: command",
                       "          command: \"python3 \\\"$QWEN_SKILL_ROOT/tools/stopcheck.py\\\"\""):
            self.assertIn(needle, fm)

    def test_state_machine_is_before_long_instructions_and_defers_report_format(self):
        text = (V27 / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(text.index("ОБЯЗАТЕЛЬНЫЙ АВТОМАТ v27"), text.index("## 1. Что ты производишь"))
        self.assertIn("MAP → TRIAGE → DRAFT → VERIFY → DELIVER", text)
        self.assertIn("не читай его в начале", text)
        self.assertIn("work/report.md", text)


class HistoricalArmsStayFrozen(unittest.TestCase):
    def test_v1_through_v26_match_head_bytes(self):
        prefix = "cases/06-dev-logging/sherlock/skills/"
        p = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", "HEAD", prefix],
                           capture_output=True, text=True)
        self.assertEqual(0, p.returncode, p.stderr)
        tracked = [line for line in p.stdout.splitlines()
                   if re.search(r"/v(?:[1-9]|1[0-9]|2[0-6])(?:\.1)?/", line)]
        self.assertTrue(tracked)
        for rel in tracked:
            local = ROOT / rel
            self.assertTrue(local.exists(), "%s was deleted" % rel)
            blob = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:%s" % rel],
                                  capture_output=True)
            self.assertEqual(0, blob.returncode, rel)
            self.assertEqual(blob.stdout, local.read_bytes(), "%s changed after it was frozen" % rel)

        local_files = []
        for arm in (SHERLOCK / "skills").glob("v*"):
            if not re.match(r"v(?:[1-9]|1[0-9]|2[0-6])(?:\.1)?$", arm.name):
                continue
            for path in arm.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    local_files.append(str(path.relative_to(ROOT)))
        self.assertEqual(sorted(tracked), sorted(local_files))


if __name__ == "__main__":
    unittest.main(verbosity=2)
