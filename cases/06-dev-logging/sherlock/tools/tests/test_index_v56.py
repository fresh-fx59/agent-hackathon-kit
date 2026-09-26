#!/usr/bin/env python3
"""v56 (index by content identity) + v53 spec items 1, 4 (heartbeat), 10: load-time data index for citecheck.

The index must change cost, never answers: an indexed check prints the same
bytes as the full-scan check; a chunked (memory-capped) build writes the same
index as a single-pass build; a stale or missing index is a distinct,
named signal; the model cannot write the index; checkers emit heartbeats.

    python3 tools/tests/test_index_v56.py
"""
import filecmp
import importlib.util
import json
import os
import pickle
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.normpath(os.path.join(HERE, "..", "..", "skills",
                                    os.environ.get("SHERLOCK_TEST_PACKAGE", "v56")))
TOOLS = os.path.join(PKG, "tools")
CITECHECK = os.path.join(TOOLS, "citecheck.py")
BUILD = os.path.join(TOOLS, "buildindex.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sib(name):
    """v56: the SAME path-unique sibling module the package tools bind (never sys.path)."""
    return load("sib_host_%s" % name, CITECHECK)._sibling(name)


def _rec(i):
    ips = ["45.168.116.98", "91.220.163.170", "10.0.0.5", "-", "1.1.1.%d" % (i % 50)]
    users = ["администратор", "root", "IPSERVER\\\\bob", "alice", None]
    return json.dumps({"Event": {
        "System": {"EventID": 4625 if i % 3 else 4624, "Channel": "Security",
                   "EventRecordID": 1000 + i,
                   "TimeCreated": {"#attributes": {
                       "SystemTime": "2021-06-01T18:%02d:%02d.000000Z" % (i // 60 % 60, i % 60)}}},
        "EventData": {"IpAddress": ips[i % len(ips)],
                      "SubStatus": "0xc0000064" if i % 4 else "0xc000006a",
                      "TargetUserName": users[i % len(users)],
                      "LogonType": i % 11,
                      "CommandLine": "\"C:\\3proxy\\bin64\\3proxy.exe\" --service"}}},
        ensure_ascii=False)


REPORT = """# Отчёт

## Находки

### Н-1 · Перебор паролей · попытка

- Строка `Security.jsonl:2` показывает "45.168.116.98" в поле IpAddress и "0xc0000064".
- Строка `Security.jsonl:7` содержит запись "TargetUserName": "root".
- Путь службы `System.jsonl:1`: `"C:\\3proxy\\bin64\\3proxy.exe" --service`.
- Строка `notes.log:2` говорит "service restarted after update".
- агрегат: Security.jsonl · count(Event.EventData.SubStatus=0xc000006a) = 75 · `x`
- агрегат: Security.jsonl · distinct(Event.EventData.IpAddress) = 54 · `x`
- агрегат: Security.jsonl · distinct_over(Event.EventData.IpAddress, 5) = 4 · `x`
- агрегат: Security.jsonl · count(Event.System.EventID=4625, Event.EventData.IpAddress!=-) = 160 · `x`
- агрегат: Security.jsonl · count(Event.EventData.TargetUserName~=adm) = 60 · `x`
- агрегат: Security.jsonl · count(Event.EventData.LogonType>=5) = 150 · `x`
- агрегат: notes.log · count(line~=service) = 2 · `x`

Итог: попытка.
"""


class _IndexFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.corpus = os.path.join(self.root, "corpus")
        self.work = os.path.join(self.root, "work")
        os.makedirs(os.path.join(self.corpus, "rendered"))
        os.makedirs(self.work)
        with open(os.path.join(self.corpus, "Security.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(_rec(i) for i in range(300)) + "\n")
        with open(os.path.join(self.corpus, "System.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ImagePath": "\"C:\\3proxy\\bin64\\3proxy.exe\" --service"}) + "\n")
        with open(os.path.join(self.corpus, "notes.log"), "w", encoding="utf-8") as fh:
            fh.write("boot ok\nservice restarted after update by admin\n"
                     "user CORP\\alice logged in\nservice stopped\n")
        with open(os.path.join(self.corpus, "rendered", "x.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"a": {"b": 1}, "c": "C:\\\\Users\\\\carol\\\\x"}\n\nnot json\n')
        self.report = os.path.join(self.work, "report.md")
        with open(self.report, "w", encoding="utf-8") as fh:
            fh.write(REPORT)
        self.index = os.path.join(self.root, "index")

    def tearDown(self):
        for dp, dns, _fns in os.walk(self.index):
            for d in dns:
                os.chmod(os.path.join(dp, d), 0o755)
        self.tmp.cleanup()

    def env(self, **kw):
        e = dict(os.environ)
        for k in ("SHERLOCK_INDEX_ROOT", "SHERLOCK_REQUIRE_INDEX", "SHERLOCK_HEARTBEAT",
                  "SHERLOCK_FINALIZE_STOP_RUNNING", "SHERLOCK_LIFT_SCAN_CAPS"):
            e.pop(k, None)
        e.update(kw)
        return e

    def build(self, *args, env=None):
        p = subprocess.run([sys.executable, BUILD, "--corpus", self.corpus,
                            "--index-root", self.index, *args],
                           capture_output=True, text=True, env=env or self.env())
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def check(self, *args, env=None):
        p = subprocess.run([sys.executable, CITECHECK, self.report, "--corpus", self.corpus,
                            "--require-quote", *args],
                           capture_output=True, text=True, env=env or self.env(), cwd=self.root)
        return p.returncode, p.stdout, p.stderr

class IndexV53Test(_IndexFixture):
    def test_build_layout_readonly_manifest(self):
        out = self.build()
        d = out["dir"]
        self.assertEqual(os.path.basename(d), out["data_sha256"])
        self.assertEqual(os.path.dirname(d), self.index)
        with open(os.path.join(d, "manifest.json"), encoding="utf-8") as fh:
            man = json.load(fh)
        self.assertEqual(man["checker_version"], "sherlock-v56")
        for w in man["walk"]:
            self.assertEqual(len(w["sha256"]), 64)
            self.assertIn("mtime_ns", w)
        for fn in os.listdir(d):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, fn)).st_mode), 0o444, fn)
        again = self.build()
        self.assertTrue(again.get("fresh"))

    def test_indexed_check_byte_identical_and_used(self):
        base_text = self.check(env=self.env(SHERLOCK_INDEX_ROOT=os.path.join(self.root, "none")))
        base_json = self.check("--json", env=self.env(SHERLOCK_INDEX_ROOT=os.path.join(self.root, "none")))
        self.build()
        idx_text = self.check()
        idx_json = self.check("--json")
        self.assertEqual(base_text[:2], idx_text[:2])
        self.assertEqual(base_json[:2], idx_json[:2])
        self.assertIn("агрегат", base_text[1])
        # in-process: the index really answered the reads
        cc = load("cc_v53_idx", CITECHECK)
        ccindex = sib("ccindex")
        ccindex.STATS["hits"].clear()
        st, _d = ccindex.attach_for(cc, self.corpus, self.report)
        self.assertEqual(st, "fresh")
        with open(self.report, encoding="utf-8") as fh:
            text = fh.read()
        cc.check(text, self.corpus, require_quote=True)
        for name in ("agg_evaluate", "read_lines"):
            self.assertGreater(ccindex.STATS["hits"].get(name, 0), 0, ccindex.STATS)

    def test_stale_and_missing_are_distinct_signals(self):
        rc, out, _ = self.check("--require-index")
        self.assertEqual(rc, 3)
        self.assertIn("data index missing or stale — run `tools/buildindex.py`", out)
        self.assertIn("missing", out)
        self.build()
        rc, out, _ = self.check("--require-index")
        self.assertNotEqual(rc, 3, out)
        p = os.path.join(self.corpus, "notes.log")
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1000))
        rc, out, _ = self.check("--require-index")
        self.assertEqual(rc, 3)
        self.assertIn("stale", out)
        rc_env, out_env, _ = self.check(env=self.env(SHERLOCK_REQUIRE_INDEX="1"))
        self.assertEqual(rc_env, 3)
        # not required -> honest full-scan fallback, never an error
        rc2, out2, _ = self.check()
        self.assertNotEqual(rc2, 3)
        self.build()
        with open(os.path.join(self.corpus, "new.log"), "w") as fh:
            fh.write("x\n")
        rc, out, _ = self.check("--require-index")
        self.assertEqual(rc, 3)

    def test_chunked_build_equals_single_pass(self):
        big = self.build("--force")
        small_root = os.path.join(self.root, "index2")
        p = subprocess.run([sys.executable, BUILD, "--corpus", self.corpus, "--index-root",
                            small_root, "--mem-cap-mb", "0.002"],
                           capture_output=True, text=True, env=self.env())
        self.assertEqual(p.returncode, 0, p.stderr)
        small = json.loads(p.stdout)
        self.assertGreater(small["build"]["spills"], 0)
        self.assertEqual(big["build"]["spills"], 0)
        a, b = big["dir"], small["dir"]
        names = sorted(os.listdir(a))
        self.assertEqual(names, sorted(os.listdir(b)))
        for fn in names:
            if fn == "manifest.json":
                continue
            pa, pb = os.path.join(a, fn), os.path.join(b, fn)
            if fn.endswith(".pkl"):   # sets pickle in hash order: compare values
                with open(pa, "rb") as x, open(pb, "rb") as y:
                    self.assertEqual(pickle.load(x), pickle.load(y), fn)
            else:
                self.assertTrue(filecmp.cmp(pa, pb, shallow=False), fn)
        for dp, dns, _f in os.walk(small_root):
            for dn in dns:
                os.chmod(os.path.join(dp, dn), 0o755)
        shutil.rmtree(small_root)

    def test_build_refused_inside_stop_hook(self):
        p = subprocess.run([sys.executable, BUILD, "--corpus", self.corpus,
                            "--index-root", self.index], capture_output=True, text=True,
                           env=self.env(SHERLOCK_FINALIZE_STOP_RUNNING="1"))
        self.assertEqual(p.returncode, 2)
        self.assertFalse(os.path.exists(self.index))

    def test_heartbeat_lines(self):
        heartbeat = sib("heartbeat")
        env = self.env(SHERLOCK_HEARTBEAT="1")
        _rc, _out, err = self.check(env=env)
        beats = [heartbeat.parse(x) for x in err.splitlines()]
        beats = [b for b in beats if b]
        units = {u for c, u, _n in beats if c == "citecheck"}
        self.assertTrue({"start", "citation", "file", "aggregate"} <= units, units)
        for tool, args in (("statecheck", ["--corpus", self.corpus]),
                           ("reportcheck", [self.report, "--json"]),
                           ("triagecheck", ["--help"])):
            p = subprocess.run([sys.executable, os.path.join(TOOLS, tool + ".py"), *args],
                               capture_output=True, text=True, env=env)
            got = [heartbeat.parse(x) for x in p.stderr.splitlines()]
            self.assertIn((tool, "start", 1), got, p.stderr[-500:])
        _rc, _out, err = self.check()
        self.assertNotIn("SHERLOCK-HB", err)
        # rows beats inside long scans
        os.environ["SHERLOCK_HEARTBEAT"] = "1"
        try:
            old = heartbeat.ROW_BEAT
            heartbeat.ROW_BEAT = 10
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                n = sum(1 for _ in heartbeat.lines(iter(["x\n"] * 35), "citecheck"))
            self.assertEqual(n, 35)
            self.assertEqual(buf.getvalue().count("SHERLOCK-HB citecheck rows"), 3)
        finally:
            heartbeat.ROW_BEAT = old
            os.environ.pop("SHERLOCK_HEARTBEAT", None)

    def test_boundary_denies_index_writes(self):
        bc = load("bc_v53", os.path.join(TOOLS, "boundarycheck.py"))
        ws = self.root
        deny = lambda tool, inp: bc.index_write_denied(  # noqa: E731
            {"tool_name": tool, "tool_input": inp}, ws)
        self.assertTrue(deny("write_file", {"file_path": "index/abc/manifest.json"}))
        self.assertTrue(deny("replace", {"file_path": os.path.join(ws, "index", "x")}))
        self.assertIsNone(deny("write_file", {"file_path": "work/report.md"}))
        self.assertIsNone(deny("write_file", {"file_path": "work/index.md"}))
        self.assertTrue(deny("run_shell_command", {"command": "chmod -R u+w index/"}))
        self.assertTrue(deny("run_shell_command", {"command": "rm -rf ./index"}))
        self.assertIsNone(deny("run_shell_command",
                               {"command": "python3 tools/buildindex.py --corpus corpus"}))
        self.assertIsNone(deny("run_shell_command", {"command": "ls index/"}))
        self.assertIsNone(deny("run_shell_command", {"command": "rm work/tmp.txt"}))


class EscapeNormalizerV53Test(unittest.TestCase):
    def test_json_escaped_quote_matches(self):
        cc = load("cc_v53_norm", CITECHECK)
        line = '{"ImagePath": "\\"C:\\\\3proxy\\\\bin64\\\\3proxy.exe\\" --service"}'
        claim = 'путь `"C:\\3proxy\\bin64\\3proxy.exe" --service` запуска'
        claim2 = "учётная запись `IPSERVER\\root` в строке"
        line2 = '{"User": "IPSERVER\\\\root"}'
        os.environ.pop("SHERLOCK_ESCAPE_NORMALIZER", None)
        self.assertEqual(cc.support(claim, line, 0.34, 3)[4], "quote")
        self.assertEqual(cc.support(claim2, line2, 0.34, 3)[4], "quote")
        os.environ["SHERLOCK_ESCAPE_NORMALIZER"] = "0"
        try:
            self.assertNotEqual(cc.support(claim2, line2, 0.34, 3)[4], "quote")
        finally:
            os.environ.pop("SHERLOCK_ESCAPE_NORMALIZER", None)
        # never turns a real mismatch into a quote
        self.assertNotEqual(cc.support("`IPSERVER\\alice` здесь", line2, 0.34, 3)[4], "quote")


class IndexPathIdentityTest(_IndexFixture):
    """2026-09-25 gate spec 3a: an index is identified by corpus CONTENT and
    relative paths; the absolute root is supplied by the caller at read time
    (a container-built index for /work/corpus must be found on the host)."""

    def _ccindex(self):
        ccindex = sib("ccindex")
        return ccindex

    def _move(self, copy=False):
        dst_parent = os.path.join(self.root, "b")
        os.makedirs(dst_parent)
        dst = os.path.join(dst_parent, "corpus")
        if copy:
            shutil.copytree(self.corpus, dst, copy_function=shutil.copy2)
        else:
            os.rename(self.corpus, dst)
        return dst

    def test_index_found_after_corpus_moved(self):
        self.build()
        moved = self._move(copy=True)
        st, d, _man = self._ccindex().find_index(moved, roots=[self.index])
        self.assertEqual(st, "fresh", (st, d, _man))

    def test_index_found_across_bind_path(self):
        base = load("cc_v53_bind_base", CITECHECK)
        with open(self.report, encoding="utf-8") as fh:
            text = fh.read()
        want = None
        self.build()
        p2 = self._move()
        want = base.check(text, p2, require_quote=True)
        ci = self._ccindex()
        st, d, man = ci.find_index(p2, roots=[self.index])
        self.assertEqual(st, "fresh", (st, d, man))
        cc = load("cc_v53_bind_idx", CITECHECK)
        ci.STATS["hits"].clear()
        ci.attach(cc, d, man, corpus=p2)
        got = cc.check(text, p2, require_quote=True)
        self.assertEqual(json.dumps(got, sort_keys=True, default=str),
                         json.dumps(want, sort_keys=True, default=str))
        self.assertGreater(ci.STATS["hits"].get("read_lines", 0), 0, ci.STATS)

    def test_root_mismatch_not_silent(self):
        self.build()
        p2 = self._move()
        with open(os.path.join(p2, "extra.log"), "w") as fh:
            fh.write("x\n")
        st, d, why = self._ccindex().find_index(p2, roots=[self.index])
        self.assertEqual(st, "stale", (st, d, why))
        self.assertIsNotNone(d)
        self.assertIn("data files changed", why)

    def test_strict_detects_mtime_preserving_edit(self):
        self.build()
        p = os.path.join(self.corpus, "notes.log")
        st0 = os.stat(p)
        with open(p, "r+b") as fh:
            fh.seek(0)
            fh.write(b"B")
        os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns))
        ci = self._ccindex()
        st, _d, _m = ci.find_index(self.corpus, roots=[self.index])
        self.assertEqual(st, "fresh")
        st, _d, why = ci.find_index(self.corpus, roots=[self.index], strict=True)
        self.assertEqual(st, "stale")
        self.assertIn("data content changed", why)
        rc, out, _ = self.check("--require-index")
        self.assertNotEqual(rc, 3, out)
        rc, out, _ = self.check("--require-index", "--strict-index")
        self.assertEqual(rc, 3, out)
        self.assertIn("data content changed", out)

    def test_changed_file_is_stale(self):
        self.build()
        p2 = self._move()
        p = os.path.join(p2, "notes.log")
        st0 = os.stat(p)
        with open(p, "a") as fh:
            fh.write("more\n")
        os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns))
        st, d, why = self._ccindex().find_index(p2, roots=[self.index])
        self.assertEqual(st, "stale", (st, d, why))


if __name__ == "__main__":
    unittest.main()
