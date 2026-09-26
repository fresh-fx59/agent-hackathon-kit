#!/usr/bin/env python3
"""v59 Stop-hook fix: watchdog, ceiling, verdict cache, K=2, reference delivery.

Spec: vault docs/specs/2026-09-24-stophook-timeout-fix-spec.md, acceptance
ladder step 1. The watchdog tests use the REAL default limits (50 s / 5 s).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SHERLOCK = Path(__file__).resolve().parents[2]
V58 = SHERLOCK / "skills" / "v60"
TOOLS = V58 / "tools"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GW = load("gatewatch_v60_under_test", TOOLS / "gatewatch.py")

# The harness gives the Stop hook (only) a verdict-signing key file; tests do too.
_KEYDIR = tempfile.mkdtemp(prefix="v59-verdict-key-")
KEY_FILE = os.path.join(_KEYDIR, "verdict-hmac.key")
with open(KEY_FILE, "wb") as _fh:
    _fh.write(os.urandom(32))
os.environ[GW.VERDICT_KEY_ENV] = KEY_FILE

BUSY_SILENT = "import time\nt=time.time()\nwhile time.time()-t<%s: pass\n"
SLEEPER = "import time\ntime.sleep(%s)\n"


class Watchdog(unittest.TestCase):
    def test_real_defaults(self):
        self.assertEqual(GW.HEARTBEAT_LIMIT_S, 50.0)
        self.assertEqual(GW.CPU_STALL_LIMIT_S, 5.0)
        self.assertEqual(GW.TIMEOUT_K, 2)

    def test_no_heartbeat_killed_at_50s(self):
        r = GW.run_watched([sys.executable, "-c", BUSY_SILENT % 90], ceiling=120)
        self.assertEqual(r.cause, "no heartbeat")
        self.assertEqual(r.returncode, 124)
        self.assertAlmostEqual(r.elapsed, 50.0, delta=1.0)

    def test_no_cpu_killed_at_5s(self):
        r = GW.run_watched([sys.executable, "-c", SLEEPER % 60], ceiling=120)
        self.assertEqual(r.cause, "no CPU")
        self.assertAlmostEqual(r.elapsed, 5.0, delta=1.0)

    def test_io_silent_busy_15s_not_killed(self):
        r = GW.run_watched([sys.executable, "-c", BUSY_SILENT % 15], ceiling=120)
        self.assertIsNone(r.cause)
        self.assertEqual(r.returncode, 0)
        self.assertGreaterEqual(r.elapsed, 15.0)

    def test_heartbeats_reset_the_watchdog(self):
        code = ("import sys,time\nt=time.time()\nn=0\n"
                "while time.time()-t<3:\n n+=1\n if n%200000==0:\n"
                "  sys.stderr.write('SHERLOCK-HB citecheck citation %d\\n'%n); sys.stderr.flush()\n")
        r = GW.run_watched([sys.executable, "-c", code], ceiling=60, hb_limit=1.0)
        self.assertIsNone(r.cause)
        self.assertGreater(r.beats, 0)
        self.assertEqual(GW.stderr_tail(r.stderr), "")

    def test_ceiling_kill(self):
        r = GW.run_watched([sys.executable, "-c", BUSY_SILENT % 30], ceiling=1.0)
        self.assertEqual(r.cause, "ceiling")

    def test_timeout_message_is_not_failure(self):
        msg = GW.timeout_message("citecheck", 30.4, "no heartbeat")
        self.assertEqual(msg, "citecheck timed out after 30 s (no heartbeat) — not a "
                              "citation error; do not edit the report")
        self.assertNotIn("failed", msg)


class Ceiling(unittest.TestCase):
    def ceiling(self, value):
        env = dict(os.environ)
        env.pop(GW.OUTER_TIMEOUT_ENV, None)
        if value is not None:
            env[GW.OUTER_TIMEOUT_ENV] = value
        code = ("import sys; sys.path.insert(0,%r); import stopcheck, gatewatch;"
                "print(stopcheck.TOTAL_TIMEOUT, gatewatch.ceiling_s())" % str(TOOLS))
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                             text=True, check=True).stdout.split()
        return [float(x) for x in out]

    def test_ceiling_from_env_and_default(self):
        self.assertEqual(self.ceiling(None), [590.0, 590.0])
        self.assertEqual(self.ceiling("100"), [90.0, 90.0])

    def test_no_hardcoded_24_23_50(self):
        text = (TOOLS / "stopcheck.py").read_text(encoding="utf-8")
        self.assertNotIn("TOTAL_TIMEOUT = 50", text)
        self.assertNotIn("CHILD_TIMEOUT = 24", text)


def make_run(root, hang_gate=None):
    """A work dir + a package copy of v59 whose gate scripts are stubs."""
    root = Path(root)
    package = root / "package"
    shutil.copytree(V58, package, ignore=shutil.ignore_patterns("__pycache__"))
    for gate in ("reportcheck", "citecheck", "statecheck", "triagecheck"):
        body = ('import time\ntime.sleep(60)\n' if gate == hang_gate else
                'print(\'{"blocking": 0}\')\n')
        (package / "tools" / (gate + ".py")).write_text(body, encoding="utf-8")
    work, corpus = root / "work", root / "corpus"
    work.mkdir()
    corpus.mkdir()
    (corpus / "a.log").write_text("line\n", encoding="utf-8")
    (work / "worklist.tsv").write_text("# id\tвердикт\nrow\tD Н-1\n", encoding="utf-8")
    (work / "rules.tsv").write_text("", encoding="utf-8")
    (work / "report.md").write_text("report\n", encoding="utf-8")
    return package, work, corpus


class CacheAndFault(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ.pop("SHERLOCK_INDEX_ROOT", None)

    def tearDown(self):
        self._tmp.cleanup()

    def finalize(self, package, work, corpus, **kw):
        fin = load("finalize_v59_%d" % time.monotonic_ns(), package / "tools" / "finalize.py")
        rc = fin.run(package, work, corpus, **kw)
        return fin, rc

    def test_clean_run_writes_cache_and_detail(self):
        package, work, corpus = make_run(self.root)
        fin, rc = self.finalize(package, work, corpus)
        self.assertEqual(rc, 0)
        key = fin.verdict_key(package, work, corpus)
        entry = GW.cache_read(str(self.root), key)
        self.assertEqual(entry["verdict"], "clean")
        detail = json.loads(Path(GW.detail_path(str(self.root))).read_text())
        self.assertEqual(detail["cache_key"], key)
        for gate in ("reportcheck", "citecheck", "statecheck", "triagecheck"):
            g = detail["gates"][gate]
            for field in ("exit_code", "elapsed_s", "kill_cause", "ceiling_s", "stderr_tail"):
                self.assertIn(field, g)

    def test_two_timeouts_on_same_inputs_is_checker_fault(self):
        package, work, corpus = make_run(self.root, hang_gate="citecheck")
        fin, rc = self.finalize(package, work, corpus)
        self.assertEqual(rc, 2)
        key = fin.verdict_key(package, work, corpus)
        first = GW.cache_read(str(self.root), key)
        self.assertEqual((first["verdict"], first["timeouts"]), ("timeout", 1))
        self.assertIn("citecheck timed out after 5 s (no CPU) — not a citation error", first["reason"])
        fin, rc = self.finalize(package, work, corpus)
        second = GW.cache_read(str(self.root), key)
        self.assertEqual((second["verdict"], second["timeouts"]), ("checker_fault", 2))
        self.assertIn("checker_fault", second["reason"][:220])

    def _stop(self):
        stop = load("stopcheck_v59_%d" % time.monotonic_ns(), TOOLS / "stopcheck.py")
        original = stop.GW.data_sha256_for
        self.addCleanup(setattr, stop.GW, "data_sha256_for", original)
        return stop

    def test_cache_hit_starts_zero_gate_processes(self):
        package, work, corpus = make_run(self.root)
        stop = self._stop()
        fin = stop._load_finalize(str(package))
        stop.GW.data_sha256_for = lambda c, r=None: "d" * 64
        key = fin.verdict_key(package, work, corpus, "d" * 64)
        launched = []
        stop.run_finalize = lambda *a, **k: launched.append(a) or (True, None)
        real_popen = subprocess.Popen
        subprocess.Popen = lambda *a, **k: launched.append(a) or real_popen(*a, **k)
        try:
            for verdict, reason, want in (("clean", None, None),
                                          ("blocking", "Sherlock: stored reason", "Sherlock: stored reason")):
                GW.cache_write(str(self.root), key, {"verdict": verdict, "reason": reason, "timeouts": 0})
                t = time.monotonic()
                got = stop.cached_gates(str(corpus), str(work), str(work / "report.md"),
                                        str(package), str(self.root), time.monotonic() + 60, "")
                self.assertLess(time.monotonic() - t, 1.0)
                self.assertEqual(got, want)
        finally:
            subprocess.Popen = real_popen
        self.assertEqual(launched, [])

    def test_checker_fault_request_retries_once_at_double_limits(self):
        package, work, corpus = make_run(self.root)
        stop = self._stop()
        fin = stop._load_finalize(str(package))
        stop.GW.data_sha256_for = lambda c, r=None: "d" * 64
        key = fin.verdict_key(package, work, corpus, "d" * 64)
        GW.cache_write(str(self.root), key, {"verdict": "timeout", "reason": "x", "timeouts": 1})
        calls = []
        stop.run_finalize = lambda c, o, r, d, m=1.0: calls.append(m) or (False, "blocked")
        args = (str(corpus), str(work), str(work / "report.md"), str(package), str(self.root))
        stop.cached_gates(*args, time.monotonic() + 60, "CHECKER-FAULT: citecheck slow")
        stop.cached_gates(*args, time.monotonic() + 60, "CHECKER-FAULT: citecheck slow")
        self.assertEqual(calls, [2.0, 1.0])

    def test_missing_index_blocks_without_gates(self):
        package, work, corpus = make_run(self.root)
        stop = self._stop()
        stop.run_finalize = lambda *a, **k: self.fail("gates must not run")
        got = stop.cached_gates(str(corpus), str(work), str(work / "report.md"),
                                str(package), str(self.root), time.monotonic() + 60, "")
        self.assertIn("data index missing or stale", got)


class Delivery(unittest.TestCase):
    def test_reference_delivery(self):
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            report = Path(root) / "work" / "report.md"
            report.parent.mkdir()
            data = "отчёт\n".encode()
            report.write_bytes(data)
            good = "REPORT work/report.md sha256=%s bytes=%d" % (hashlib.sha256(data).hexdigest(), len(data))
            self.assertTrue(GW.reference_matches(good, str(report), root))
            self.assertFalse(GW.reference_matches(good.replace("bytes=", "bytes=1"), str(report), root))
            self.assertFalse(GW.reference_matches("REPORT work/other.md sha256=%s bytes=%d"
                                                  % ("0" * 64, len(data)), str(report), root))
            self.assertIsNone(GW.reference_matches("отчёт", str(report), root))

    def test_checker_fault_parse(self):
        self.assertEqual(GW.checker_fault_request("x\nCHECKER-FAULT: citecheck slow gate\n"),
                         ("citecheck", "slow gate"))
        self.assertIsNone(GW.checker_fault_request("no fault"))


class _PinPackage:
    def _pin_package(self):
        old = os.environ.get("SHERLOCK_TEST_PACKAGE")
        os.environ["SHERLOCK_TEST_PACKAGE"] = "v59"
        self.addCleanup(lambda: os.environ.__setitem__("SHERLOCK_TEST_PACKAGE", old)
                        if old is not None else os.environ.pop("SHERLOCK_TEST_PACKAGE", None))


class HookEndToEnd(_PinPackage, unittest.TestCase):
    """Real stopcheck + finalize + buildindex on the v45 canonical fixture."""

    def test_miss_then_cache_hit_by_reference(self):
        import hashlib
        self._pin_package()
        fixture = load("finalize_v45_fixture", Path(__file__).with_name("test_finalize_v45.py"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, corpus, work = fixture.FinalizeV45("run").canonical_fixture(root)
            marker = root / ".sherlock" / "active.json"; marker.parent.mkdir()
            marker.write_text(json.dumps({"version": 36, "active": True,
                "workspace": str(root), "skill_root": str(package.resolve()),
                "corpus": str(corpus.resolve()), "out": str(work.resolve()),
                "mode": "single", "worklists": ["worklist.tsv"]}) + "\n", encoding="utf-8")
            env = {**os.environ, "QWEN_SKILL_ROOT": str(package)}
            env.pop("SHERLOCK_INDEX_ROOT", None)
            built = subprocess.run([sys.executable, str(package / "tools/buildindex.py"),
                                    "--corpus", str(corpus), "--index-root", str(root / "index")],
                                   capture_output=True, text=True, env=env)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)

            def stop(message):
                event = {"cwd": str(root), "hook_event_name": "Stop", "last_assistant_message": message}
                r = subprocess.run([sys.executable, str(package / "tools/stopcheck.py")],
                                   input=json.dumps(event), text=True, capture_output=True,
                                   cwd=root, env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                return json.loads(r.stdout)

            first = stop("not the report")
            self.assertEqual(first["decision"], "block")
            self.assertIn("REPORT work/report.md", first["reason"])
            attempts = list((work / "validation").iterdir())
            self.assertEqual(len(attempts), 1)
            data = (work / "report.md").read_bytes()
            ref = "REPORT work/report.md sha256=%s bytes=%d" % (hashlib.sha256(data).hexdigest(), len(data))
            t = time.monotonic()
            second = stop(ref)
            self.assertLess(time.monotonic() - t, 5.0)
            self.assertEqual(second["decision"], "allow", second)
            self.assertEqual(len(list((work / "validation").iterdir())), 1)  # cache hit: no new pass
            detail = json.loads((root / "index" / "verdicts" / "stopcheck-detail.json").read_text())
            self.assertEqual(detail["cache"], "hit")
            self.assertEqual(detail["msg_chars"], len(ref))


class StaleAndForgery(_PinPackage, unittest.TestCase):
    """Verifier findings on PR #102: stale pass via untracked input; forged verdicts."""

    def _fixture(self, root):
        self._pin_package()
        fx = load("finalize_v45_fixture_sf", Path(__file__).with_name("test_finalize_v45.py"))
        package, corpus, work = fx.FinalizeV45("run").canonical_fixture(root)
        self.marker = root / ".sherlock" / "active.json"
        self.marker.parent.mkdir()
        self.marker_body = json.dumps({"version": 36, "active": True, "workspace": str(root),
            "skill_root": str(package.resolve()), "corpus": str(corpus.resolve()),
            "out": str(work.resolve()), "mode": "single", "worklists": ["worklist.tsv"]}) + "\n"
        self.rearm()
        self.env = {**os.environ, "QWEN_SKILL_ROOT": str(package)}
        self.env.pop("SHERLOCK_INDEX_ROOT", None)
        subprocess.run([sys.executable, str(package / "tools/buildindex.py"), "--corpus", str(corpus),
                        "--index-root", str(root / "index")], check=True, capture_output=True, env=self.env)
        return package, corpus, work

    def rearm(self):
        for p in self.marker.parent.iterdir():
            p.unlink()
        self.marker.write_text(self.marker_body, encoding="utf-8")

    def stop(self, root, msg, env=None):
        r = subprocess.run([sys.executable, str(self.package / "tools/stopcheck.py")],
                           input=json.dumps({"cwd": str(root), "hook_event_name": "Stop",
                                             "last_assistant_message": msg}),
                           text=True, capture_output=True, cwd=root, env=env or self.env)
        return json.loads(r.stdout)

    def ref(self, work):
        import hashlib
        d = (work / "report.md").read_bytes()
        return "REPORT work/report.md sha256=%s bytes=%d" % (hashlib.sha256(d).hexdigest(), len(d))

    def test_key_covers_every_gate_argv_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.package, corpus, work = self._fixture(root)
            fin = load("finalize_v59_inputs", self.package / "tools" / "finalize.py")
            names = set(fin.gate_inputs(self.package, work, corpus))
            self.assertTrue({"work/report.md", "work/worklist.tsv", "work/rules.tsv"} <= names, names)

    def test_mutating_each_tracked_input_misses_and_blocks(self):
        """Verifier repro_stale.py, generalised to every tracked input."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.package, corpus, work = self._fixture(root)
            fin = load("finalize_v59_mut", self.package / "tools" / "finalize.py")
            self.assertEqual(self.stop(root, self.ref(work))["decision"], "allow")
            self.rearm()
            self.assertEqual(self.stop(root, self.ref(work))["decision"], "allow")  # control: hit
            for name, path in fin.gate_inputs(self.package, work, corpus).items():
                with self.subTest(input=name):
                    original = path.read_bytes()
                    key_before = fin.verdict_key(self.package, work, corpus)
                    try:
                        if name.endswith("rules.tsv"):
                            path.write_text("GARBAGE not a rule\tR99\n", encoding="utf-8")
                        else:
                            path.write_bytes(original + b"\n")  # smallest change
                        self.assertNotEqual(fin.verdict_key(self.package, work, corpus), key_before)
                        self.rearm()
                        n = len(list((work / "validation").iterdir()))
                        got = self.stop(root, self.ref(work))
                        self.assertGreater(len(list((work / "validation").iterdir())), n,
                                           "cache hit on stale input: %s" % got)
                        fresh = subprocess.run([sys.executable, str(self.package / "tools/finalize.py"),
                                                "--work", str(work), "--corpus", str(corpus),
                                                "--package", str(self.package)],
                                               capture_output=True, text=True, env=self.env)
                        want = "allow" if fresh.returncode == 0 else "block"
                        self.assertEqual(got["decision"], want, got)
                        if name.endswith("rules.tsv"):
                            self.assertEqual(got["decision"], "block")  # verifier repro
                    finally:
                        path.write_bytes(original)
                        self.rearm()
                    self.assertEqual(self.stop(root, self.ref(work))["decision"], "allow")

    def test_hand_written_and_unsigned_verdicts_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.package, corpus, work = self._fixture(root)
            (work / "rules.tsv").write_text("GARBAGE not a rule\tR99\n", encoding="utf-8")
            fin = load("finalize_v59_forge", self.package / "tools" / "finalize.py")
            key = fin.verdict_key(self.package, work, corpus)
            vdir = Path(GW.cache_dir(str(root)))
            vdir.mkdir(parents=True, exist_ok=True)
            # 1. forged: plausible entry, wrong/absent signature
            for forged in ({"key": key, "verdict": "clean", "reason": None},
                           {"key": key, "verdict": "clean", "reason": None, "sig": "0" * 64}):
                (vdir / (key + ".json")).write_text(json.dumps(forged), encoding="utf-8")
                self.assertIsNone(GW.cache_read(str(root), key))
                self.rearm()
                self.assertEqual(self.stop(root, self.ref(work))["decision"], "block")
            # 2. model-shell finalize (no key in env) writes an unsigned entry -> never a hit
            (work / "rules.tsv").write_bytes(b"")
            shell_env = dict(self.env); shell_env.pop(GW.VERDICT_KEY_ENV, None)
            f = subprocess.run([sys.executable, str(self.package / "tools/finalize.py"), "--work", str(work),
                                "--corpus", str(corpus), "--package", str(self.package)],
                               capture_output=True, text=True, env=shell_env)
            k2 = json.loads(f.stdout)["cache_key"]
            self.assertIsNone(GW.cache_read(str(root), k2))
            self.assertNotIn("sig", json.loads((vdir / (k2 + ".json")).read_text()))



class SiblingImportIsolation(unittest.TestCase):
    """v59 root cause: bare sibling imports bound one package's gate to another's checker.

    Two package copies in ONE process (hook stopcheck vs expected-root finalize,
    trusted tree vs model tree, or a test that loaded a since-deleted copy) must
    each use their OWN gatewatch/ccindex/heartbeat, and deleting one copy must not
    break the other.
    """

    def _copy(self, root, tag):
        pkg = Path(root) / tag
        shutil.copytree(V58, pkg, ignore=shutil.ignore_patterns("__pycache__"))
        return pkg

    def test_each_package_binds_its_own_siblings(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa, pb = self._copy(a, "A"), self._copy(b, "B")
            (pb / "tools" / "gatewatch.py").write_text(
                (pb / "tools" / "gatewatch.py").read_text().replace(
                    'CHECKER_VERSION = "sherlock-v59"', 'CHECKER_VERSION = "sherlock-B"'))
            fa = load("fin_iso_a_%d" % time.monotonic_ns(), pa / "tools" / "finalize.py")
            fb = load("fin_iso_b_%d" % time.monotonic_ns(), pb / "tools" / "finalize.py")
            self.assertEqual(fa.GW.CHECKER_VERSION, "sherlock-v59")
            self.assertEqual(fb.GW.CHECKER_VERSION, "sherlock-B")
            self.assertEqual(Path(fa.GW.__file__).resolve(), (pa / "tools" / "gatewatch.py").resolve())
            self.assertEqual(Path(fb.GW.HB.__file__).resolve(), (pb / "tools" / "heartbeat.py").resolve())
            sa = load("stop_iso_a_%d" % time.monotonic_ns(), pa / "tools" / "stopcheck.py")
            self.assertIs(sa.GW, fa.GW)          # same package -> one shared module

    def test_deleted_package_does_not_poison_a_later_one(self):
        with tempfile.TemporaryDirectory() as a:
            pa = self._copy(a, "A")
            fa = load("fin_iso_del_a_%d" % time.monotonic_ns(), pa / "tools" / "finalize.py")
            fa.GW.cache_dir(a)                    # forces ccindex load from A
        with tempfile.TemporaryDirectory() as b:
            pb = self._copy(b, "B")
            fb = load("fin_iso_del_b_%d" % time.monotonic_ns(), pb / "tools" / "finalize.py")
            fb.GW.cache_dir(b)
            import sys as _s
            cc = [m for k, m in _s.modules.items() if k.startswith("sherlock_sib_") and k.endswith("_ccindex")
                  and Path(getattr(m, "__file__", "")).resolve().parent == (pb / "tools").resolve()]
            self.assertTrue(cc, "B's gatewatch must load B's ccindex")

    def test_no_tool_uses_sys_path_or_bare_sibling_import(self):
        import re as _re
        names = "|".join(p.stem for p in TOOLS.glob("*.py"))
        bad = []
        for p in TOOLS.glob("*.py"):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if _re.match(r"\s*sys\.path\.(insert|append)\(", line) or \
                        _re.match(r"\s*(import|from)\s+(%s)\b" % names, line):
                    bad.append("%s:%d: %s" % (p.name, n, line.strip()))
        self.assertEqual(bad, [])

if __name__ == "__main__":
    unittest.main()
