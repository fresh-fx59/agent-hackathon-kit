#!/usr/bin/env python3
"""Tests for tools/fetch-logs.sh — the incremental log transport.

The load-bearing tests are the ones that prove the SCRIPT ACTUALLY RAN, not that the
file exists on disk. A transport that silently no-ops, short-circuits, or falls back to
reading a local directory when it was told to use SSH would still print a plausible
manifest — and the model would then investigate stale bytes and cite them with a
confident, real-looking `файл:строка`. So: a stub `ssh` is placed first on PATH, its
argv journal is DELETED, and the test asserts the journal REAPPEARED with the exact
number of invocations, the exact ssh options and the exact byte offsets on the wire.

The second load-bearing test is `SecretContainment.test_password_appears_nowhere`: it
walks every byte the script produced — stdout, stderr, manifests, journals, run.log,
the askpass helper and the stub's own argv record — and greps for the password literal.
That is what enforces «never in argv, never in logs, never in a report». It is also why
a runtime sed-scrubber is forbidden in the script: the scrubber would put the password
into sed's own argv, world-readable in `ps`.

NOT TESTED HERE, deliberately: password authentication against a real sshd. The
SSH_ASKPASS + SSH_ASKPASS_REQUIRE=force mechanism was verified twice — against the stub
below, and separately against this box's own OpenSSH_10.3p1 passphrase prompt
(2026-07-30) — but never against the operator's stand, whose OpenSSH version and PAM
stack are unknown. Exit 21 and exit 22 exist to make that discovery fast and clean
instead of a hang.

Every test is network-free and credential-free: stub-ssh, local mode, --print-ssh-argv
or --dry-run. `--once` removes every sleep, so every arm is deterministic.

    python3 tools/tests/test_fetch_logs.py
"""
import filecmp
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
FETCH = os.path.join(TOOLS, "fetch-logs.sh")

PASSWORD = "hunter2-Xq9"

# A stub `ssh` that records every invocation's argv and the env keys that matter, then
# serves canned stdout. `printf '%s\0'` keeps arguments unambiguous even when they
# contain spaces; `---\0` separates invocations.
SSH_STUB = r"""#!/usr/bin/env bash
printf '%s\0' "$@" >> "$SSH_STUB_LOG"
printf 'ENV_SECRET=%s\n' "${SHERLOCK_ASKPASS_SECRET+set}" >> "$SSH_STUB_META"
printf 'ASKPASS=%s REQUIRE=%s DISPLAY=[%s] AUTHSOCK=[%s]\n' \
  "${SSH_ASKPASS:-}" "${SSH_ASKPASS_REQUIRE:-}" "${DISPLAY-UNSET}" "${SSH_AUTH_SOCK-UNSET}" \
  >> "$SSH_STUB_META"
# THE SECRET'S REAL CHANNEL. The helper the script actually wrote is captured HERE, at exec
# time, while it still exists — after the run the EXIT trap has deleted it, which is why a
# post-mortem walk of the root cannot see a helper that embedded the plaintext.
if [ -n "${SSH_ASKPASS:-}" ] && [ -r "${SSH_ASKPASS:-}" ]; then
  printf '=== askpass body\n' >> "$SSH_STUB_META"
  cat "$SSH_ASKPASS" >> "$SSH_STUB_META"
  printf '=== askpass stdout\n' >> "$SSH_STUB_META"
  SHERLOCK_ASKPASS_SECRET="${SHERLOCK_ASKPASS_SECRET:-}" "$SSH_ASKPASS" >> "$SSH_STUB_META" 2>&1
  printf '=== askpass end\n' >> "$SSH_STUB_META"
fi
printf -- '---\0' >> "$SSH_STUB_LOG"
case " $* " in
  (*" -V "*)     echo "${SSH_STUB_VERSION:-OpenSSH_10.3p1, OpenSSL 3.6.2}" >&2 ;;
  (*-printf*)    [ -f "$SSH_STUB_LIST" ] && cat "$SSH_STUB_LIST" ;;
  (*"tail -c "*) [ -n "${SSH_STUB_FETCH_SLEEP:-}" ] && sleep "$SSH_STUB_FETCH_SLEEP"
                 # HONOUR THE REQUESTED RANGE. Serving a fixed blob for every offset made the
                 # whole ssh arm blind to the cursor arithmetic it is supposed to prove: the
                 # bytes on the wire were right by luck, never by offset. The remote command
                 # is `tail -c +N -- 'path' | head -c M`; N and M are lifted out of it and
                 # applied to the file that stands in for the remote log.
                 rc="${SSH_STUB_FETCH_RC:-${SSH_STUB_RC:-0}}"
                 if [ -f "${SSH_STUB_FULL:-}" ]; then
                   _cmd="${*: -1}"
                   _n="${_cmd#*tail -c +}"; _n="${_n%% *}"
                   _m="${_cmd##*head -c }"; _m="${_m%% *}"
                   case "$_n" in (''|*[!0-9]*) _n=1 ;; esac
                   case "$_m" in (''|*[!0-9]*) _m=1048576 ;; esac
                   tail -c +"$_n" -- "$SSH_STUB_FULL" | head -c "$_m"
                 elif [ -f "$SSH_STUB_BODY" ]; then
                   cat "$SSH_STUB_BODY"
                 fi
                 exit "$rc" ;;
  (*"test -d"*)  : ;;
esac
exit "${SSH_STUB_RC:-0}"
"""
# SSH_STUB_FETCH_RC / SSH_STUB_FETCH_SLEEP exist so the LISTING can succeed while the
# FETCH fails. Without them `SSH_STUB_RC` fails the listing first and the per-file fetch
# path is never entered — which is exactly how it stayed untested. See
# TheFetchPathFailsSafely.

HANGING_STUB = r"""#!/usr/bin/env bash
case " $* " in (*" -V "*) echo "OpenSSH_10.3p1" >&2; exit 0 ;; esac
sleep 999
"""


def write_exec(path, body):
    """Write an executable helper script."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)
    return path


def write_cfg(path, body, mode=0o600):
    """Write a config file at an exact mode (the permission guard is a hard refusal)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body).lstrip())
    os.chmod(path, mode)
    return path


def path_without_ssh(d):
    """A PATH with every tool the script needs — and no `ssh` at all."""
    binp = os.path.join(d, "nossh-bin")
    os.makedirs(binp, exist_ok=True)
    for tool in ("bash", "sh", "find", "tail", "head", "stat", "sha1sum", "sha256sum",
                 "wc", "cat", "mv", "rm", "mkdir", "cut", "tr", "date", "sed", "touch",
                 "ln", "id", "rmdir", "flock", "timeout", "setsid", "printf"):
        real = shutil.which(tool)
        link = os.path.join(binp, tool)
        if real and not os.path.exists(link):
            os.symlink(real, link)
    assert not os.path.exists(os.path.join(binp, "ssh"))
    return binp


def run(*args, env=None, cwd=None, timeout=90):
    """Run fetch-logs.sh; return (rc, manifest_or_None, stdout, stderr)."""
    e = dict(os.environ)
    e.pop("SHERLOCK_STAND_CONFIG", None)
    e.pop("SHERLOCK_WATCH_ROOT", None)
    e.pop("SHERLOCK_SSH_BIN", None)
    if env:
        for k, v in env.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
    p = subprocess.run(["bash", FETCH, *args], capture_output=True, text=True,
                       env=e, cwd=cwd, timeout=timeout)
    data = None
    if "--json" in args and p.stdout.strip():
        try:
            data = json.loads(p.stdout)
        except ValueError:
            data = None
    return p.returncode, data, p.stdout, p.stderr


def manifest(root, profile):
    with open(os.path.join(root, profile, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def val_after(argv, flag):
    """The value PAIRED with a flag. Asserting `flag in argv` proves nothing about the
    value, which is where the interesting regressions live."""
    assert flag in argv, "%s is absent from argv: %r" % (flag, argv)
    i = argv.index(flag)
    assert i + 1 < len(argv), "%s is the last element — it carries no value" % flag
    return argv[i + 1]


def invocations(journal):
    """Split the stub's argv journal into one argv list per invocation."""
    if not os.path.exists(journal):
        return []
    raw = open(journal, "rb").read()
    recs = raw.split(b"---\x00")[:-1]          # the final piece is the trailing tail
    out = []
    for r in recs:
        out.append([a.decode("utf-8", "replace") for a in r.split(b"\x00") if a])
    return out


class Stand:
    """A throwaway stand: a stub ssh on PATH, a config, and a scratch root."""

    def __init__(self, d, auth="password_env", extra=""):
        self.d = d
        self.bin = os.path.join(d, "bin"); os.makedirs(self.bin, exist_ok=True)
        self.journal = os.path.join(d, "journal")
        self.meta = os.path.join(d, "meta")
        self.listing = os.path.join(d, "listing")
        self.body = os.path.join(d, "body")
        # The bytes that stand in for the remote FILE, accumulated across serve() calls so the
        # stub can answer any offset the script asks for.
        self.full = os.path.join(d, "full")
        self.root = os.path.join(d, "out")
        self.ssh = write_exec(os.path.join(self.bin, "ssh"), SSH_STUB)
        if auth == "identity":
            self.key = os.path.join(d, "id_key")
            with open(self.key, "w", encoding="utf-8") as fh:
                fh.write("not-a-real-key\n")
            os.chmod(self.key, 0o600)
            authline = "identity_file = %s" % self.key
        else:
            authline = "password_env = SHERLOCK_STAND_PASSWORD"
        self.cfg = write_cfg(os.path.join(d, "stand.ini"), """
            [stand]
            host = stand.example
            user = flink
            %s
            log_dir = /opt/flink/current/log
            file_glob = flink-*-*.log
            %s
            [watch]
            poll_seconds = 1
            """ % (authline, extra))

    def env(self, **over):
        e = {"PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
             "SSH_STUB_LOG": self.journal, "SSH_STUB_META": self.meta,
             "SSH_STUB_LIST": self.listing, "SSH_STUB_BODY": self.body,
             "SSH_STUB_FULL": self.full,
             "SHERLOCK_STAND_PASSWORD": PASSWORD}
        e.update(over)
        return e

    def serve(self, size, body, inode=111,
              path="/opt/flink/current/log/flink-taskexecutor-1.log", append=True):
        """Publish a listing row, and grow the stand-in remote file by `body`.

        `append=True` (the default) models the real thing: a log that grows. The stub then
        answers `tail -c +N | head -c M` from the accumulated file, so an offset the script got
        wrong shows up as wrong BYTES and not merely as a wrong argv string. `append=False` is
        for rotation/truncation, where the remote file is replaced.
        """
        with open(self.listing, "w", encoding="utf-8") as fh:
            fh.write("%d\t%d\t%s\n" % (size, inode, path))
        with open(self.body, "w", encoding="utf-8") as fh:
            fh.write(body)
        with open(self.full, "a" if append else "w", encoding="utf-8") as fh:
            fh.write(body)

    def tick(self, *extra, **kw):
        return run("--config", self.cfg, "--root", self.root, "--once",
                   *extra, env=self.env(**kw.pop("env", {})), **kw)


# ---------------------------------------------------------------- P1: it really ran

class TheScriptActuallyRan(unittest.TestCase):
    """The difference between «the file is on disk» and «the file executed»."""

    def test_journal_reappears_after_deletion(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            s.serve(18, "line1\nline2\nline3\n")
            s.tick()
            os.remove(s.journal)
            self.assertFalse(os.path.exists(s.journal))
            rc, _, _, err = s.tick()
            self.assertEqual(rc, 0, "a clean tick must exit 0: %s" % err)
            self.assertTrue(os.path.exists(s.journal),
                            "the stub ssh was never executed — the script did not run it")

    def test_exactly_two_execs_per_grown_file_tick(self):
        """The spec's «one stat-sizes exec, then tail -c per grown file»."""
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(18, "line1\nline2\nline3\n")
            rc, _, _, err = s.tick()
            self.assertEqual(rc, 0, err)
            inv = invocations(s.journal)
            self.assertEqual(len(inv), 2,
                             "want exactly one listing + one fetch, got %d" % len(inv))
            self.assertIn("-printf", inv[0][-1], "invocation 1 must be the listing")
            self.assertIn("tail -c ", inv[1][-1], "invocation 2 must be the fetch")

    def test_no_persistent_connection(self):
        """«Reconnect per poll cycle — no persistent connection is ever held.»"""
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(18, "line1\nline2\nline3\n")
            s.tick()
            first = invocations(s.journal)[0]
            self.assertIn("ControlMaster=no", first, "ControlMaster must be disabled")
            self.assertIn("ControlPath=none", first, "ControlPath must be disabled")

    def test_two_ticks_are_two_separate_connections(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(18, "line1\nline2\nline3\n")
            s.tick()
            s.serve(24, "line4\n")
            s.tick()
            self.assertEqual(len(invocations(s.journal)), 4,
                             "two ticks must be four distinct ssh invocations")

    def test_cursor_arithmetic_reaches_the_wire(self):
        """The offset in the manifest must be the offset in the remote command.

        With the anchor (DEVIATION 3) the wire offset is `cursor - anchor`, because the tick
        re-reads the last `anchor` bytes it already holds to prove they are still there. Both
        halves are pinned here: anchor=0 must put the cursor itself on the wire, and a
        non-zero anchor must move it back by EXACTLY that many bytes and no more.
        """
        for anchor in (0, 8):
            with tempfile.TemporaryDirectory() as d:
                s = Stand(d, auth="identity")
                s.serve(18, "line1\nline2\nline3\n")
                s.tick(env={"SHERLOCK_ANCHOR_BYTES": str(anchor)})
                off = manifest(s.root, "stand")["files"][0]["offset_to"]
                self.assertEqual(off, 18)
                os.remove(s.journal)
                s.serve(24, "line4\n")
                s.tick(env={"SHERLOCK_ANCHOR_BYTES": str(anchor)})
                fetch = [i for i in invocations(s.journal) if "tail -c " in i[-1]][0]
                self.assertIn("tail -c +%d " % (off - anchor + 1), fetch[-1],
                              "anchor=%d: the wire offset must come from the stored cursor, "
                              "not a local var" % anchor)
                f = manifest(s.root, "stand")["files"][0]
                self.assertEqual((f["offset_from"], f["bytes"]), (18, 6),
                                 "anchor=%d: the anchor is a transport detail — it must never "
                                 "leak into the reported offsets or be counted as new bytes"
                                 % anchor)
                mirror = os.path.join(s.root, "stand", "logs", "flink-taskexecutor-1.log")
                self.assertEqual(open(mirror, "rb").read(), b"line1\nline2\nline3\nline4\n",
                                 "anchor=%d: and the mirror must hold the file, once" % anchor)

    def test_stub_reachable_via_sherlock_ssh_bin_too(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(18, "line1\nline2\nline3\n")
            # A PATH that deliberately holds NO ssh, so the only way to reach one is
            # SHERLOCK_SSH_BIN. Both resolution paths must work.
            env = s.env(PATH=path_without_ssh(d), SHERLOCK_SSH_BIN=s.ssh)
            rc, _, _, err = run("--config", s.cfg, "--root", s.root, "--once", env=env)
            self.assertEqual(rc, 0, err)
            self.assertTrue(os.path.exists(s.journal),
                            "SHERLOCK_SSH_BIN must be an equally valid way to reach ssh")


# ------------------------------------------------------- P2: it cannot fake success

class NegativeControls(unittest.TestCase):
    """A script that never called ssh could not have observed these failures."""

    def test_ssh_failure_is_exit_20(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(18, "x\n")
            rc, _, _, _ = s.tick(env={"SSH_STUB_RC": "255"})
            self.assertEqual(rc, 20, "a non-zero ssh must be exit 20")
            self.assertTrue(manifest(s.root, "stand")["errors"],
                            "the failure must be recorded in manifest.errors[]")

    def test_hang_becomes_exit_22_not_a_hang(self):
        """The residual failure mode of the spec's open ASKPASS question must be
        a coded error in seconds, never an unbounded wait."""
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            write_exec(s.ssh, HANGING_STUB)
            rc, _, _, _ = s.tick(env={"SHERLOCK_SSH_TIMEOUT": "2"}, timeout=45)
            self.assertEqual(rc, 22, "a hanging ssh must be exit 22 (124 is translated)")

    def test_old_openssh_with_password_is_exit_21_before_connecting(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            s.serve(18, "x\n")
            rc, _, _, err = s.tick(env={"SSH_STUB_VERSION": "OpenSSH_8.1p1, OpenSSL 1.1.1"})
            self.assertEqual(rc, 21, "OpenSSH < 8.4 + password auth must refuse up front")
            self.assertIn("identity_file", err, "the message must name the way out")
            inv = invocations(s.journal)
            self.assertEqual(len(inv), 1, "only the version probe may run")
            self.assertEqual(inv[0], ["-V"], "no connection may be attempted")

    def test_missing_ssh_is_exit_25_and_writes_nothing(self):
        """AGENTS.md R1: the skill must survive the script being unable to run."""
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            os.remove(s.ssh)
            rc, _, _, err = s.tick(env={"SHERLOCK_SSH_BIN": "/nonexistent-ssh"})
            self.assertEqual(rc, 25, "a missing ssh is the graceful-degradation signal")
            self.assertFalse(os.path.exists(s.root), "nothing may be written: %s" % err)


# ------------------------------------------------------------ P3: invocation receipt

class InvocationReceipt(unittest.TestCase):
    """Proof of invocation in LOCAL mode, where no ssh ever runs."""

    def test_every_tick_appends_exactly_one_journal_line(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            with open(os.path.join(src, "a.logsample"), "w", encoding="utf-8") as fh:
                fh.write("one\n")
            root = os.path.join(d, "out")
            for _ in range(2):
                rc, _, _, err = run("--source", "local:" + src, "--glob", "*.logsample",
                                    "--root", root, "--once", "--quiet")
                self.assertEqual(rc, 0, err)
            lines = [json.loads(l) for l
                     in open(os.path.join(root, "local", "fetch-log.jsonl"), encoding="utf-8")]
            self.assertEqual(len(lines), 2, "one journal line per tick")
            self.assertEqual([l["tick"] for l in lines], [1, 2], "the tick counter is monotone")
            self.assertNotEqual(lines[0]["invocation_id"], lines[1]["invocation_id"],
                                "each run mints a fresh id — a stale artifact cannot fake this")
            self.assertEqual(lines[-1]["invocation_id"],
                             manifest(root, "local")["invocation_id"],
                             "the manifest and the journal must describe the same run")

    def test_journal_records_a_hash_never_the_argv(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(4, "hi\n")
            s.tick()
            line = json.loads(open(os.path.join(s.root, "stand", "fetch-log.jsonl"),
                                   encoding="utf-8").readline())
            self.assertEqual(len(line["argv_sha256"]), 64, "argv is recorded as a hash")
            self.assertNotIn("stand.example", json.dumps(line),
                             "the journal must not carry the argv verbatim")


# ------------------------------------------------------------------- P4: liveness

class Liveness(unittest.TestCase):
    """«The CLI is really firing» — the operator's own acceptance, run without SSH."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.src = os.path.join(self.d, "logs"); os.makedirs(self.src)
        self.root = os.path.join(self.d, "out")
        self.f = os.path.join(self.src, "app.logsample")

    def tearDown(self):
        self.tmp.cleanup()

    def tick(self, *extra):
        rc, _, out, err = run("--source", "local:" + self.src, "--glob", "*.logsample",
                              "--root", self.root, "--once", *extra)
        return rc, manifest(self.root, "local"), out, err

    def one(self, m, name="app.logsample"):
        for f in m["files"]:
            if f["remote"].endswith(name):
                return f
        self.fail("%s missing from the manifest" % name)

    def test_a_first_tick_takes_the_whole_small_file(self):
        open(self.f, "w", encoding="utf-8").write("a\nb\nc\n")
        rc, m, _, err = self.tick()
        self.assertEqual(rc, 0, err)
        f = self.one(m)
        self.assertEqual((f["event"], f["offset_from"], f["bytes"]), ("new", 0, 6))

    def test_b_an_error_burst_is_the_next_tick_delta_and_nothing_else(self):
        open(self.f, "w", encoding="utf-8").write("a\nb\nc\n")
        self.tick()
        with open(self.f, "a", encoding="utf-8") as fh:
            fh.write("ERROR boom\nERROR boom2\n")
        rc, m, out, _ = self.tick()
        f = self.one(m)
        self.assertEqual(rc, 0)
        self.assertEqual(f["offset_from"], 6, "tick 2 must resume exactly where tick 1 stopped")
        self.assertEqual(f["bytes"], 23, "only the appended bytes may be fetched")
        self.assertEqual((f["first_local_line"], f["last_local_line"]), (4, 5),
                         "the model must be able to address exactly the new material")
        mirror = os.path.join(self.root, "local", "logs", "app.logsample")
        self.assertEqual(open(mirror, "rb").read(), open(self.f, "rb").read(),
                         "the mirror must be byte-identical to the source")
        self.assertIn("принесено", out, "the RU tick line goes to stdout")
        runlog = open(os.path.join(self.root, "local", "run.log"), encoding="utf-8").read()
        self.assertIn("принесено", runlog, "...and to the run log — the spec's two sinks")

    def test_c_no_change_is_success_not_an_error(self):
        open(self.f, "w", encoding="utf-8").write("a\n")
        self.tick()
        rc, m, _, _ = self.tick()
        self.assertEqual(rc, 0, "zero new bytes IS success")
        self.assertEqual(self.one(m)["event"], "unchanged")
        self.assertEqual(m["totals"]["bytes"], 0)

    def test_d_truncation_resets_the_cursor(self):
        open(self.f, "w", encoding="utf-8").write("aaaaaaaaaaaaaaaaaaaa\n")
        self.tick()
        open(self.f, "w", encoding="utf-8").write("b\n")
        rc, m, _, _ = self.tick()
        f = self.one(m)
        self.assertEqual((f["event"], f["offset_from"]), ("truncated", 0),
                         "the spec's «size < cursor ⇒ reset to 0», kept verbatim")

    def test_e_rotation_above_the_old_cursor_is_caught_by_inode(self):
        """THE CASE THE SPEC MISSES. Without the inode rule this tick reads from the
        middle of a DIFFERENT file and hands the model garbled lines it would then
        cite with a real-looking address."""
        open(self.f, "w", encoding="utf-8").write("old\n" * 5)
        self.tick()
        os.rename(self.f, self.f + ".gone")
        open(self.f, "w", encoding="utf-8").write("NEW\n" * 40)   # LARGER than before
        rc, m, _, _ = self.tick()
        f = self.one(m)
        self.assertEqual(f["event"], "rotated", "a recreated file must be detected by inode")
        self.assertEqual(f["offset_from"], 0, "a rotated file is re-read from the start")
        self.assertTrue(f["rotated"])
        mirror = os.path.join(self.root, "local", "logs", "app.logsample")
        self.assertTrue(os.path.exists(mirror + ".rot1"),
                        "the pre-rotation mirror is rolled aside so old citations stay valid")
        self.assertEqual(open(mirror, "rb").read(), b"NEW\n" * 40,
                         "the new mirror must be the new file, with no garbled prefix")

    def test_f_exclusions_are_skipped_not_fetched(self):
        open(self.f, "w", encoding="utf-8").write("a\n")
        open(os.path.join(self.src, "x_db_LOG.logsample"), "w").close()
        open(os.path.join(self.src, "y.zip.logsample"), "w").close()
        rc, _, _, _ = run("--source", "local:" + self.src, "--glob", "*.logsample",
                          "--exclude", "*_db_LOG.logsample *.zip.logsample",
                          "--root", self.root, "--once", "--quiet")
        m = manifest(self.root, "local")
        self.assertEqual(rc, 0)
        skipped = sorted(s["name"] for s in m["skipped"])
        self.assertEqual(skipped, ["x_db_LOG.logsample", "y.zip.logsample"])
        self.assertTrue(all(s["reason"] == "exclude_glob" for s in m["skipped"]))
        names = [f["remote"].split("/")[-1] for f in m["files"]]
        self.assertNotIn("y.zip.logsample", names, "an excluded file must not be fetched")

    def test_g_local_mode_never_execs_ssh(self):
        d2 = tempfile.mkdtemp(dir=self.d)
        journal = os.path.join(d2, "journal")
        binpath = os.path.join(d2, "bin"); os.makedirs(binpath)
        write_exec(os.path.join(binpath, "ssh"), SSH_STUB)
        open(self.f, "w", encoding="utf-8").write("a\n")
        rc, _, _, err = run("--source", "local:" + self.src, "--glob", "*.logsample",
                            "--root", self.root, "--once", "--quiet",
                            env={"PATH": binpath + os.pathsep + os.environ["PATH"],
                                 "SSH_STUB_LOG": journal, "SSH_STUB_META": journal + ".m",
                                 "SSH_STUB_LIST": "", "SSH_STUB_BODY": ""})
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(journal),
                         "local mode must not touch ssh even when ssh is right there")

    def test_h_cold_start_is_tail_capped_and_from_start_overrides(self):
        open(self.f, "w", encoding="utf-8").write("Z" * 20000 + "\n")
        rc, _, _, _ = run("--source", "local:" + self.src, "--glob", "*.logsample",
                          "--root", self.root, "--max-bytes", "4096", "--once", "--quiet")
        f = self.one(manifest(self.root, "local"))
        self.assertEqual(rc, 0)
        self.assertEqual(f["offset_from"], 20001 - 4096, "the first sighting takes the tail")
        self.assertTrue(f["truncated_head"], "a capped cold start is never silent")
        self.assertEqual(f["skipped_bytes"], 20001 - 4096)

        root2 = os.path.join(self.d, "out2")
        run("--source", "local:" + self.src, "--glob", "*.logsample", "--root", root2,
            "--max-bytes", "4096", "--from-start", "--once", "--quiet")
        f2 = self.one(manifest(root2, "local"))
        self.assertEqual(f2["offset_from"], 0, "--from-start restores spec-literal behaviour")


# --------------------------------------- P5: argv builder + secret containment

class ArgvBuilder(unittest.TestCase):
    """Spec liveness arm (b): the ssh argv is buildable and inspectable with no network."""

    def argv(self, auth):
        d = tempfile.mkdtemp()
        s = Stand(d, auth=auth)
        rc, data, out, err = run("--config", s.cfg, "--root", s.root,
                                 "--print-ssh-argv", "--json",
                                 env=s.env(SHERLOCK_SSH_BIN="/nonexistent"))
        self.assertEqual(rc, 0, "building argv must never touch the network: %s" % err)
        self.assertFalse(os.path.exists(s.journal), "no ssh may be executed")
        return json.loads(out)

    def test_identity_mode_argv(self):
        d = self.argv("identity")
        a = d["argv"]
        self.assertEqual(d["auth_mode"], "identity")
        self.assertEqual(a[:2], ["-T", "-n"], "-T keeps the bytes exact; -n closes stdin")
        self.assertIn("BatchMode=yes", a)
        self.assertIn("PreferredAuthentications=publickey", a)
        self.assertIn("IdentitiesOnly=yes", a)
        self.assertIn("-i", a, "an identity file must be passed with -i")
        self.assertEqual(d["env_keys"], [], "key auth needs no askpass environment")

    def test_password_mode_argv(self):
        d = self.argv("password_env")
        a = d["argv"]
        self.assertEqual(d["auth_mode"], "password_env")
        self.assertIn("BatchMode=no", a,
                      "BatchMode=yes disables ALL interaction, askpass included")
        self.assertIn("PreferredAuthentications=password,keyboard-interactive", a)
        self.assertIn("NumberOfPasswordPrompts=1", a)
        self.assertEqual(d["env_keys"],
                         ["SSH_ASKPASS", "SSH_ASKPASS_REQUIRE",
                          "SHERLOCK_ASKPASS_SECRET", "DISPLAY", "SSH_AUTH_SOCK"])

    def test_double_dash_precedes_the_host(self):
        a = self.argv("identity")["argv"]
        self.assertEqual(a[a.index("--") + 1], "stand.example",
                         "-- before the host is belt-and-braces against a leading '-'")

    def test_no_argv_element_is_the_password(self):
        a = self.argv("password_env")["argv"]
        self.assertFalse(any(PASSWORD in x for x in a),
                         "the password must never reach argv — `ps` is world-readable")


class ArgvCarriesTheConfiguredValues(unittest.TestCase):
    """Requirement: «assert host/port/user/identity flags are assembled correctly».

    A mutation campaign on 2026-07-30 found the original argv tests toothless: they
    asserted the option NAMES but never the VALUES. Three mutations SURVIVED all 60
    tests green — hardcoding `-p 22`, deleting `-l "$c_user"`, and pointing `-i` at
    `/dev/null`. A transport that connects as the wrong user, on the wrong port, with
    the wrong key fails only on the operator's stand, which is the one place there is
    no test. So every assertion below is a flag/value PAIR, and the port is
    deliberately NON-default (2222) so a hardcoded 22 cannot satisfy it.
    """

    def argv(self, extra="", auth="identity"):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        s = Stand(d, auth=auth, extra=extra)
        rc, _, out, err = run("--config", s.cfg, "--root", s.root,
                              "--print-ssh-argv", "--json",
                              env=s.env(SHERLOCK_SSH_BIN="/nonexistent"))
        self.assertEqual(rc, 0, "building argv must never touch the network: %s" % err)
        self.assertFalse(os.path.exists(s.journal), "no ssh may be executed")
        return json.loads(out), s

    def test_port_is_the_configured_port_not_a_hardcoded_22(self):
        d, _ = self.argv(extra="port = 2222")
        self.assertEqual(val_after(d["argv"], "-p"), "2222",
                         "the configured port must reach argv; a hardcoded 22 would "
                         "connect to the wrong service and only fail on the stand")

    def test_port_defaults_to_22_when_unconfigured(self):
        d, _ = self.argv()
        self.assertEqual(val_after(d["argv"], "-p"), "22", "the documented default")

    def test_user_reaches_argv_as_the_value_of_dash_l(self):
        d, _ = self.argv()
        self.assertEqual(val_after(d["argv"], "-l"), "flink",
                         "dropping -l silently connects as the LOCAL username")

    def test_identity_file_reaches_argv_as_the_value_of_dash_i(self):
        d, s = self.argv()
        self.assertEqual(val_after(d["argv"], "-i"), s.key,
                         "the configured key must be the key offered, not any other path")

    def test_host_is_the_element_before_the_remote_command(self):
        d, _ = self.argv()
        a = d["argv"]
        self.assertEqual(a[-2], "stand.example", "the host is the penultimate element")
        self.assertIn("find ", a[-1], "the remote command is the final element")
        self.assertEqual(a[a.index("--") + 1], a[-2], "-- must immediately precede the host")

    def test_known_hosts_is_private_to_the_run_root(self):
        """A stand's host key must never be written into the operator's own
        ~/.ssh/known_hosts — the script is not entitled to edit that file."""
        d, s = self.argv()
        opts = [x for x in d["argv"] if x.startswith("UserKnownHostsFile=")]
        self.assertEqual(len(opts), 1, "exactly one UserKnownHostsFile option")
        p = opts[0].split("=", 1)[1]
        self.assertTrue(p.startswith(os.path.realpath(s.root))
                        or p.startswith(s.root), "known_hosts must live under the root: %s" % p)
        self.assertNotIn("/.ssh/", p, "the user's own known_hosts must never be touched")


class TheFetchPathFailsSafely(unittest.TestCase):
    """Requirement: «make the stub ssh exit non-zero; assert fetch-logs.sh exits with the
    contract's documented non-zero code and prints a diagnosable message, and does NOT
    hang or leave a corrupt cursor.»

    The pre-existing `test_ssh_failure_is_exit_20` sets SSH_STUB_RC=255 for EVERY
    invocation, so the LISTING fails first and the per-file fetch path is never reached.
    The mutation campaign proved it: replacing `return 20` in fetch_range() with
    `return 0` left all 60 tests green. These arms fail the fetch while the listing
    SUCCEEDS, which is both the realistic failure (a log rotated away mid-tick, a
    per-file permission error) and the dangerous one — it is the only path that could
    advance a cursor over bytes that were never written.
    """

    def stand(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        s = Stand(d, auth="identity")
        s.serve(18, "line1\nline2\nline3\n")
        return s

    LOG = "flink-taskexecutor-1.log"

    def test_a_a_failing_fetch_is_exit_20_and_the_listing_really_succeeded(self):
        s = self.stand()
        rc, _, _, _ = s.tick(env={"SSH_STUB_FETCH_RC": "255"})
        self.assertEqual(rc, 20, "a non-zero fetch is the contract's exit 20")
        inv = invocations(s.journal)
        self.assertEqual(len(inv), 2,
                         "this arm is void unless the listing SUCCEEDED and the fetch ran; "
                         "got %d invocation(s)" % len(inv))
        self.assertIn("tail -c ", inv[1][-1], "invocation 2 must be the fetch that failed")

    def test_b_the_error_names_the_file_and_the_real_return_code(self):
        s = self.stand()
        s.tick(env={"SSH_STUB_FETCH_RC": "255"})
        errs = " ".join(manifest(s.root, "stand")["errors"])
        self.assertIn(self.LOG, errs, "the error must name the file that failed")
        self.assertNotIn("rc=0", errs,
                         "`rc=$?` inside `if ! cmd; then` captures the status of the "
                         "NEGATED compound, which is always 0 — so the real code was lost "
                         "and every fetch failure reported itself as a success code")
        self.assertIn("rc=20", errs, "the recorded code must be the transport's real code")

    def test_c_a_failing_fetch_is_visible_without_opening_the_manifest(self):
        """Exit 20 plus a summary line reading «итого тик 1: 0 файл(ов), 0 Б» is
        indistinguishable from a clean no-change tick. An operator — and the agent
        reading this output — must be told which file failed."""
        s = self.stand()
        rc, _, out, err = s.tick(env={"SSH_STUB_FETCH_RC": "255"})
        self.assertEqual(rc, 20)
        self.assertIn(self.LOG, err, "a failed fetch must name the file on stderr")
        runlog = open(os.path.join(s.root, "stand", "run.log"), encoding="utf-8").read()
        self.assertIn(self.LOG, runlog,
                      "...and in run.log — the spec's second sink, which is what "
                      "survives after the terminal is closed")

    def test_d_a_failing_fetch_leaves_no_cursor_and_no_mirror(self):
        """The commit order is what makes a crash cost one re-fetched delta and never a
        gap: bytes land in the mirror only after the fetch exits 0, and the cursor is
        rewritten only after that append returns 0."""
        s = self.stand()
        s.tick(env={"SSH_STUB_FETCH_RC": "255"})
        cursors = os.path.join(s.root, "stand", "state", "cursors")
        self.assertEqual(os.listdir(cursors) if os.path.isdir(cursors) else [], [],
                         "a cursor written for a fetch that failed would silently skip "
                         "those bytes forever")
        mirror = os.path.join(s.root, "stand", "logs", self.LOG)
        self.assertFalse(os.path.exists(mirror),
                         "no mirror may be created from a fetch that produced nothing")
        f = manifest(s.root, "stand")["files"][0]
        self.assertEqual((f["event"], f["bytes"]), ("failed", 0))
        self.assertEqual(manifest(s.root, "stand")["totals"]["failed"], 1)

    def test_e_a_failing_fetch_loses_no_bytes_the_next_tick_refetches_them(self):
        """The whole point of the commit order: recovery must be automatic and complete."""
        s = self.stand()
        s.tick(env={"SSH_STUB_FETCH_RC": "255"})
        rc, _, _, err = s.tick()                      # same listing, fetch now works
        self.assertEqual(rc, 0, err)
        f = manifest(s.root, "stand")["files"][0]
        self.assertEqual((f["event"], f["offset_from"], f["bytes"]), ("new", 0, 18),
                         "the retry must start at 0 and take every byte — no gap")
        mirror = os.path.join(s.root, "stand", "logs", self.LOG)
        self.assertEqual(open(mirror, "rb").read(), b"line1\nline2\nline3\n",
                         "and the mirror must hold exactly the source bytes")

    def test_f_a_hanging_fetch_is_exit_22_not_20(self):
        """22 and 20 send the operator to different places: 22 says the stand or the link
        is slow, 20 says it refused. Collapsing a timeout into 20 is a wrong diagnosis."""
        s = self.stand()
        t0 = time.time()
        rc, _, _, _ = s.tick(env={"SSH_STUB_FETCH_SLEEP": "999",
                                  "SHERLOCK_SSH_TIMEOUT": "2"}, timeout=60)
        elapsed = time.time() - t0
        self.assertEqual(rc, 22, "a fetch timeout must surface as the contract's 22")
        self.assertLess(elapsed, 40,
                        "and it must be bounded by the timeout guard, not by the test "
                        "runner: took %.1fs" % elapsed)

    def test_g_a_failed_fetch_never_advances_the_tick_into_a_corrupt_state(self):
        """Two failing ticks in a row must stay recoverable — the counter advances, the
        cursors do not."""
        s = self.stand()
        for _ in range(2):
            rc, _, _, _ = s.tick(env={"SSH_STUB_FETCH_RC": "255"})
            self.assertEqual(rc, 20)
        self.assertEqual(manifest(s.root, "stand")["tick"], 2, "the tick counter is monotone")
        cursors = os.path.join(s.root, "stand", "state", "cursors")
        self.assertEqual(os.listdir(cursors) if os.path.isdir(cursors) else [], [])
        rc, _, _, err = s.tick()
        self.assertEqual(rc, 0, "the profile must still be usable: %s" % err)
        self.assertEqual(manifest(s.root, "stand")["files"][0]["bytes"], 18)


class SecretContainment(unittest.TestCase):
    """«password never logged, never enters reports» — enforced by grepping every byte."""

    def test_password_appears_nowhere(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            s.serve(18, "line1\nline2\nline3\n")
            rc, _, out, err = s.tick()
            self.assertEqual(rc, 0, err)
            self.assertNotIn(PASSWORD, out, "not on stdout")
            self.assertNotIn(PASSWORD, err, "not on stderr")
            for base, _, files in os.walk(s.root):
                for name in files:
                    p = os.path.join(base, name)
                    self.assertNotIn(PASSWORD.encode(), open(p, "rb").read(),
                                     "the password leaked into %s" % p)
            self.assertNotIn(PASSWORD.encode(), open(s.journal, "rb").read(),
                             "the password reached the ssh argv")

    def test_the_askpass_helper_on_disk_holds_no_secret(self):
        """THE MUTATION HOLE THIS CLOSES. The old pair of tests could not tell a correct
        helper from one whose body embedded the plaintext: `test_password_appears_nowhere`
        walks the root only AFTER the process exited, and by then the EXIT trap has deleted the
        helper; the other test grepped a helper the TEST rewrote itself, never the bytes the
        script wrote. A mutant with `<<ASK` (unquoted) and `printf '%s\\n' '$_PW'` left all 73
        tests green while writing the password to disk on every run.

        The stub now captures the helper's real body — and its real stdout — at exec time.
        """
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            s.serve(18, "x\n")
            rc, _, _, err = s.tick()
            self.assertEqual(rc, 0, err)
            meta = open(s.meta, encoding="utf-8").read()
            self.assertIn("=== askpass body", meta,
                          "the stub never saw an askpass helper — this arm proves nothing")
            body = meta.split("=== askpass body", 1)[1].split("=== askpass stdout", 1)[0]
            stdout = meta.split("=== askpass stdout", 1)[1].split("=== askpass end", 1)[0]
            self.assertNotIn(PASSWORD, body,
                             "the helper's BODY, as written to disk by the script, contains "
                             "the plaintext password")
            self.assertIn("SHERLOCK_ASKPASS_SECRET", body,
                          "the helper must REFERENCE the env var, which is what keeps the "
                          "secret off disk")
            self.assertEqual(stdout.strip("\n"), PASSWORD,
                             "...and executing it must still yield the secret, or the channel "
                             "is merely broken rather than safe: got %r" % stdout)
            self.assertNotIn(PASSWORD, meta.replace(stdout, ""),
                             "the password may appear ONLY as the helper's stdout")

    def test_the_askpass_heredoc_is_quoted_at_the_source_level(self):
        """`<<'ASK'` vs `<<ASK` is one apostrophe, and it decides whether the secret is
        written to disk. An unquoted heredoc also expands `$SHERLOCK_ASKPASS_SECRET` to the
        empty string at write time, which no behavioural test can distinguish from a stand
        that simply refused the password."""
        body = open(FETCH, encoding="utf-8").read()
        self.assertIn("<<'ASK'", body,
                      "the askpass heredoc must be QUOTED so its body is written verbatim")
        helper = body.split("<<'ASK'", 1)[1].split("\nASK\n", 1)[0]
        self.assertIn("$SHERLOCK_ASKPASS_SECRET", helper,
                      "the helper body must read the secret from the environment")
        for forbidden in ("$_PW", "${_PW", "$c_password"):
            self.assertNotIn(forbidden, helper,
                             "the helper body must not name a variable holding the secret "
                             "value (%s): a quoted heredoc writes it literally" % forbidden)

    def test_the_secret_is_delivered_by_environment(self):
        """Delivered — and delivered the right way. Both halves matter."""
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            s.serve(18, "x\n")
            s.tick()
            meta = open(s.meta, encoding="utf-8").read()
            self.assertIn("ENV_SECRET=set", meta, "the secret must arrive, by environment")
            self.assertIn("REQUIRE=force", meta, "SSH_ASKPASS_REQUIRE=force must be set")
            self.assertIn("DISPLAY=[]", meta,
                          "DISPLAY is deliberately EMPTY: measured unnecessary on >= 8.4, "
                          "and an empty value stops a real X askpass dialog spawning")
            self.assertIn("AUTHSOCK=[]", meta,
                          "SSH_AUTH_SOCK is emptied so an agent key cannot mask a broken "
                          "password path that would then fail only on the operator's stand")

    def test_askpass_helper_is_removed(self):
        """Cleanup only. What the helper CONTAINED while it existed is
        test_the_askpass_helper_on_disk_holds_no_secret — this arm used to try to do both and
        did the second one against a helper it wrote itself."""
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            s.serve(18, "x\n")
            s.tick()
            state = os.path.join(s.root, "stand", "state")
            leftovers = [f for f in os.listdir(state) if f.startswith("askpass-")]
            self.assertEqual(leftovers, [], "the askpass helper must be cleaned up on exit")

    def test_sshpass_is_never_reached_for(self):
        body = open(FETCH, encoding="utf-8").read()
        code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("sshpass", code,
                         "sshpass -p puts the password on argv; the spec's fallback is "
                         "deliberately overruled and must stay overruled")


# ------------------------------------------------------------------- guard arms

class GuardsRefuseTheyDoNotWarn(unittest.TestCase):
    """Every guard must produce a specific non-zero code, not a warning."""

    def guard(self, body, want, mode=0o600, args=(), env=None, extra_env=None):
        with tempfile.TemporaryDirectory() as d:
            cfg = write_cfg(os.path.join(d, "stand.ini"), body, mode)
            e = {"SHERLOCK_STAND_PASSWORD": PASSWORD}
            if extra_env:
                e.update(extra_env)
            if env is not None:
                e = env
            rc, _, _, err = run("--config", cfg, "--root", os.path.join(d, "out"),
                                *(args or ("--check",)), env=e)
            self.assertEqual(rc, want, "want exit %d, got %d (%s)" % (want, rc, err.strip()))
            return err

    GOOD = """
        [stand]
        host = h.example
        user = u
        password_env = SHERLOCK_STAND_PASSWORD
        """

    def test_world_readable_config_is_refused(self):
        err = self.guard(self.GOOD, 12, mode=0o644)
        self.assertIn("chmod 600", err, "the message must name the exact fix")

    def test_unknown_key_names_the_line(self):
        err = self.guard(self.GOOD + "bogus_key = 1\n", 10)
        self.assertIn(":5", err, "the parse error must name the line number")

    def test_unknown_section(self):
        self.guard("[nope]\nhost = h\n", 10)

    def test_duplicate_key(self):
        self.guard(self.GOOD + "host = h2.example\n", 10)

    def test_continuation_line(self):
        self.guard(self.GOOD + "   still the host\n", 10)

    def test_two_auth_keys_is_ambiguity_and_ambiguity_is_refused(self):
        self.guard(self.GOOD + "password = x\n", 11)

    def test_zero_auth_keys_without_an_agent(self):
        self.guard("[stand]\nhost = h.example\nuser = u\n", 11,
                   env={"PATH": os.environ["PATH"]})

    def test_password_env_naming_an_unset_variable(self):
        self.guard("""
            [stand]
            host = h.example
            user = u
            password_env = DEFINITELY_UNSET_VARIABLE_XYZ
            """, 11)

    def test_host_starting_with_a_dash(self):
        self.guard("""
            [stand]
            host = -oProxyCommand=id
            user = u
            password_env = SHERLOCK_STAND_PASSWORD
            """, 11)

    def test_command_substitution_in_log_dir_never_executes(self):
        """The injection arm: refuse, AND prove nothing ran."""
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            cfg = write_cfg(os.path.join(d, "evil.ini"), """
                [stand]
                host = stand.example
                user = flink
                password_env = SHERLOCK_STAND_PASSWORD
                log_dir = /opt/$(id)/log
                """)
            rc, _, _, _ = run("--config", cfg, "--root", s.root, "--once", env=s.env())
            self.assertEqual(rc, 11, "a shell metacharacter in log_dir must be refused")
            self.assertFalse(os.path.exists(s.journal),
                             "nothing may be executed on a rejected config")

    def test_missing_config_prints_the_template(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, _, err = run("--config", os.path.join(d, "nope.ini"), "--check")
            self.assertEqual(rc, 13)
            self.assertIn("[stand]", err, "exit 13 must print a paste-ready template")


class UsageErrors(unittest.TestCase):
    """Everything that is the caller's mistake is exit 1, never a partial run."""

    def test_once_and_watch_together(self):
        rc, _, _, _ = run("--once", "--watch")
        self.assertEqual(rc, 1)

    def test_unbounded_watch_in_a_non_interactive_run_is_refused(self):
        """This is what stops an agent turn hanging on a loop that never returns."""
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            rc, _, _, err = run("--source", "local:" + src, "--watch",
                                "--root", os.path.join(d, "out"))
            self.assertEqual(rc, 1)
            self.assertIn("--max-ticks", err, "the message must name the way out")

    def test_bounded_watch_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            open(os.path.join(src, "a.logsample"), "w", encoding="utf-8").write("x\n")
            root = os.path.join(d, "out")
            rc, _, _, err = run("--source", "local:" + src, "--glob", "*.logsample",
                                "--root", root, "--watch", "--max-ticks", "2",
                                "--poll", "1", "--quiet", timeout=30)
            self.assertEqual(rc, 0, err)
            lines = open(os.path.join(root, "local", "fetch-log.jsonl"),
                         encoding="utf-8").readlines()
            self.assertEqual(len(lines), 2, "--max-ticks must actually bound the loop")

    def test_print_ssh_argv_in_local_mode(self):
        rc, _, _, _ = run("--source", "local:/tmp", "--print-ssh-argv")
        self.assertEqual(rc, 1)

    def test_bad_source_spec(self):
        rc, _, _, _ = run("--source", "weird:x", "--check")
        self.assertEqual(rc, 1)

    def test_unknown_flag(self):
        rc, _, _, _ = run("--frobnicate")
        self.assertEqual(rc, 1)

    def test_missing_local_directory_is_30(self):
        rc, _, _, _ = run("--source", "local:/nonexistent-dir-xyz", "--once")
        self.assertEqual(rc, 30)

    def test_xtrace_is_refused(self):
        """set -x would echo the password assignment and the env-prefixed exec."""
        p = subprocess.run(["bash", "-x", FETCH, "--version"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("xtrace", p.stderr)

    def test_help_and_version(self):
        rc, _, out, _ = run("--help")
        self.assertEqual(rc, 0)
        self.assertIn("[stand]", out, "--help must carry the paste-ready config")
        rc, _, out, _ = run("--version")
        self.assertEqual(rc, 0)
        self.assertIn("6.0", out)


class Lock(unittest.TestCase):
    """Two instances writing one cursor set would corrupt it silently."""

    def test_a_held_lock_is_exit_41(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            open(os.path.join(src, "a.logsample"), "w", encoding="utf-8").write("x\n")
            root = os.path.join(d, "out")
            run("--source", "local:" + src, "--glob", "*.logsample",
                "--root", root, "--once", "--quiet")
            lock = os.path.join(root, "local", "state", ".lock")
            holder = subprocess.Popen(["flock", lock, "sleep", "10"])
            try:
                time.sleep(0.6)
                rc, _, _, _ = run("--source", "local:" + src, "--glob", "*.logsample",
                                  "--root", root, "--once", "--quiet")
                self.assertEqual(rc, 41, "a concurrent instance must be refused")
            finally:
                holder.kill()
                holder.wait()


class DryRun(unittest.TestCase):
    """--dry-run must be genuinely inert, not merely quiet."""

    def test_lists_but_fetches_nothing_and_advances_no_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(18, "line1\nline2\nline3\n")
            rc, data, _, err = run("--config", s.cfg, "--root", s.root, "--once",
                                   "--dry-run", "--json", "--quiet", env=s.env())
            self.assertEqual(rc, 0, err)
            self.assertTrue(data["dry_run"])
            self.assertEqual(data["totals"]["bytes"], 0, "no bytes may be fetched")
            self.assertTrue(all(f["bytes"] == 0 for f in data["files"]))
            self.assertEqual(len(invocations(s.journal)), 1,
                             "only the listing exec may run in a dry run")
            cursors = os.path.join(s.root, "stand", "state", "cursors")
            self.assertEqual(os.listdir(cursors) if os.path.isdir(cursors) else [], [],
                             "no cursor may be advanced")


class WriteDiscipline(unittest.TestCase):
    """The mirror is EVIDENCE. A synthetic line would be a fabricated citation
    carrying a real line number — the worst thing this repo can ship."""

    def test_the_script_never_writes_a_byte_it_did_not_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            f = os.path.join(src, "a.logsample")
            root = os.path.join(d, "out")
            open(f, "w", encoding="utf-8").write("one\ntwo\n")
            run("--source", "local:" + src, "--glob", "*.logsample",
                "--root", root, "--once", "--quiet")
            with open(f, "a", encoding="utf-8") as fh:
                fh.write("three\nfour\n")
            run("--source", "local:" + src, "--glob", "*.logsample",
                "--root", root, "--once", "--quiet")
            mirror = os.path.join(root, "local", "logs", "a.logsample")
            self.assertEqual(open(mirror, "rb").read(), open(f, "rb").read(),
                             "no banner, no separator, no header — the mirror IS the log")

    def test_the_script_never_greps_what_it_fetched(self):
        """AGENTS.md R4: a source implementation must not parse content. Enforced as a
        stated header invariant, and here as a mechanical check on the code."""
        body = open(FETCH, encoding="utf-8").read()
        code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("grep", code, "no grep may touch fetched bytes")
        self.assertNotIn("awk", code, "no awk may touch fetched bytes")


class Manifest(unittest.TestCase):
    """The manifest is the other half of R4's `local bytes + manifest`."""

    def test_shape_and_the_absence_of_any_password_field(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            s.serve(18, "line1\nline2\nline3\n")
            s.tick()
            m = manifest(s.root, "stand")
            self.assertEqual(m["schema"], "sherlock.fetch-logs/1")
            self.assertEqual(m["source"]["kind"], "ssh")
            self.assertEqual(m["source"]["auth_mode"], "password_env",
                             "the manifest records the MODE, never the secret")
            blob = json.dumps(m)
            self.assertNotIn("password\"", blob.replace("password_env", ""),
                             "no password field may exist anywhere in the schema")
            for key in ("tick", "invocation_id", "totals", "files", "skipped", "errors",
                        "inode_tracking", "listing_fallback"):
                self.assertIn(key, m, "manifest is missing %s" % key)

    def test_local_mode_omits_host_user_and_auth(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            open(os.path.join(src, "a.logsample"), "w", encoding="utf-8").write("x\n")
            root = os.path.join(d, "out")
            run("--source", "local:" + src, "--glob", "*.logsample",
                "--root", root, "--once", "--quiet")
            src_block = manifest(root, "local")["source"]
            self.assertEqual(src_block["kind"], "local")
            for k in ("host", "port", "user", "auth_mode"):
                self.assertNotIn(k, src_block, "%s has no meaning in local mode" % k)


class ShippedConfigExample(unittest.TestCase):
    """The example config IS the operator surface. If it does not parse, it is a lie."""

    EXAMPLE = os.path.join(TOOLS, "fetch-logs.conf.example")

    def test_it_exists_and_carries_no_credential(self):
        self.assertTrue(os.path.exists(self.EXAMPLE))
        text = open(self.EXAMPLE, encoding="utf-8").read()
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("password") and "=" in s and not s.startswith("#"):
                self.fail("the committed example must never carry a populated password: %s" % s)

    def test_it_parses(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "stand.ini")
            key = os.path.join(d, "id_ed25519")
            open(key, "w", encoding="utf-8").write("x\n")
            os.chmod(key, 0o600)
            text = open(self.EXAMPLE, encoding="utf-8").read()
            text = text.replace("identity_file = ~/.ssh/id_ed25519",
                                "identity_file = " + key)
            open(cfg, "w", encoding="utf-8").write(text)
            os.chmod(cfg, 0o600)
            rc, _, out, err = run("--config", cfg, "--root", os.path.join(d, "out"),
                                  "--check")
            self.assertEqual(rc, 0, "the shipped example must parse: %s" % err)
            self.assertIn("auth_mode     : identity", out)

    def test_check_never_prints_a_secret(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d)
            rc, _, out, err = run("--config", s.cfg, "--root", s.root, "--check",
                                  env=s.env())
            self.assertEqual(rc, 0)
            self.assertNotIn(PASSWORD, out + err)
            self.assertIn("auth_mode     : password_env", out,
                          "--check reports WHICH mode, never the value")


class ItIsExecutableAndSelfContained(unittest.TestCase):

    def test_exec_bit_and_shebang_and_house_style(self):
        self.assertTrue(os.stat(FETCH).st_mode & stat.S_IXUSR, "must be executable")
        body = open(FETCH, encoding="utf-8").read()
        self.assertTrue(body.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("set -uo pipefail", body)
        self.assertNotIn("set -euo", body, "house style is -uo, never -e")

    def test_no_python_and_no_pip(self):
        body = open(FETCH, encoding="utf-8").read()
        code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("python", code, "the transport is pure bash + coreutils + ssh")
        self.assertNotIn("pip install", code)

    def test_syntax(self):
        p = subprocess.run(["bash", "-n", FETCH], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


# ==========================================================================================
# REGRESSION ARMS — one class per defect class found by adversarial review, 2026-07-30.
# Every arm below reproduced a real, measured failure before the fix.
# ==========================================================================================

class LocalStand:
    """A local-mode stand: a source dir, a root, and helpers that read the manifest back."""

    def __init__(self, d, glob="*.logsample", name="app.logsample"):
        self.d = d
        self.src = os.path.join(d, "logs"); os.makedirs(self.src, exist_ok=True)
        self.root = os.path.join(d, "out")
        self.glob = glob
        self.name = name
        self.f = os.path.join(self.src, name)

    def write(self, text, mode="w"):
        with open(self.f, mode, encoding="utf-8") as fh:
            fh.write(text)

    def tick(self, *extra, **kw):
        args = ["--source", "local:" + self.src, "--root", self.root, "--once"]
        if self.glob is not None:
            args += ["--glob", self.glob]
        return run(*args, *extra, **kw)

    def m(self):
        return manifest(self.root, "local")

    def one(self, name=None):
        want = name or self.name
        for f in self.m()["files"]:
            if f["remote"].endswith(want):
                return f
        raise AssertionError("%s missing from the manifest: %r"
                             % (want, [f["remote"] for f in self.m()["files"]]))

    def mirror(self, name=None):
        return os.path.join(self.root, "local", "logs", name or self.name)

    def cursor(self):
        cdir = os.path.join(self.root, "local", "state", "cursors")
        files = [os.path.join(cdir, f) for f in os.listdir(cdir)] if os.path.isdir(cdir) else []
        return open(files[0], encoding="utf-8").read().strip() if len(files) == 1 else None


class ContentGenerationBoundaries(unittest.TestCase):
    """A cursor may NEVER be reused across a change of the file's content generation.

    Every arm here produced a mirror whose line N and line N+1 came from two different
    generations of the log, with `skipped_bytes: 0` and `truncated_head: false` asserting that
    nothing was missed. That is a fabricated citation carrying a real line number — the worst
    output this repo can produce.
    """

    def test_rotation_to_an_EMPTY_file_resets_the_cursor(self):
        """The NORMAL log-rotate case: `mv app.log app.log.1; : > app.log`.

        The new file has a new inode and size 0, so the delta is zero — and a zero-byte tick
        used to be relabelled "unchanged", after which the PRE-rotation offset was written back
        against the NEW inode. The next tick then resumed at a stale offset inside a brand-new
        file and reported it as a clean append.
        """
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d)
            s.write("OLD line\n" * 100)                    # 900 bytes
            rc, _, _, err = s.tick("--quiet")
            self.assertEqual(rc, 0, err)
            self.assertTrue(s.cursor().startswith("900\t"), s.cursor())

            os.rename(s.f, s.f + ".1")
            open(s.f, "w").close()                          # rotate to EMPTY, new inode
            rc, _, out, err = s.tick()
            self.assertEqual(rc, 0, err)
            f = s.one()
            self.assertEqual(f["event"], "rotated",
                             "a rotation with a zero-byte delta is still a rotation")
            self.assertEqual(f["offset_from"], 0, "and its cursor is 0")
            self.assertIn("ротация", out, "the RU line must say rotation, not «без изменений»")
            self.assertTrue(s.cursor().startswith("0\t"),
                            "the stored cursor must be 0, not the pre-rotation offset: %s"
                            % s.cursor())

            s.write("NEW line\n" * 200)                     # grows past the OLD cursor
            rc, _, _, err = s.tick("--quiet")
            self.assertEqual(rc, 0, err)
            head = open(s.mirror(), "rb").read().split(b"\n", 1)[0]
            self.assertEqual(head, b"NEW line",
                             "the mirror must start at the rotated file's FIRST line, not at "
                             "whatever byte the stale cursor pointed at")
            self.assertEqual(open(s.mirror(), "rb").read(), b"NEW line\n" * 200,
                             "and hold the whole new generation, exactly once")

    def test_truncation_in_place_then_regrowth_is_caught_by_the_anchor(self):
        """`: > app.log` keeps the inode and the file then regrows PAST the old cursor, so
        neither the inode rule nor «size < cursor» fires. The anchor does: the bytes under the
        cursor are no longer the bytes we stored."""
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d)
            s.write("OLD %d ....\n" % 1 * 1)
            s.write("".join("OLD %d ....\n" % i for i in range(1, 21)))
            rc, _, _, err = s.tick("--quiet")
            self.assertEqual(rc, 0, err)
            ino_before = os.stat(s.f).st_ino

            open(s.f, "w").close()                                    # truncate IN PLACE
            s.write("".join("NEW %d ....\n" % i for i in range(1, 61)), mode="a")
            self.assertEqual(os.stat(s.f).st_ino, ino_before,
                             "this arm is void unless the inode really was preserved")

            rc, _, out, err = s.tick()
            self.assertEqual(rc, 0, err)
            f = s.one()
            self.assertEqual((f["event"], f["offset_from"]), ("truncated", 0),
                             "a same-inode rewrite must be declared, not read as an append")
            body = open(s.mirror(), "rb").read()
            self.assertEqual(body, ("".join("NEW %d ....\n" % i for i in range(1, 61))).encode(),
                             "the mirror must be the new generation only — no seam between "
                             "dead and live content presented as one continuous file")
            self.assertTrue(os.path.exists(s.mirror() + ".rot1"),
                            "the pre-truncation snapshot is rolled aside so old citations "
                            "remain checkable")

    def test_an_append_bigger_than_the_cap_keeps_the_NEWEST_bytes(self):
        """The incident is at the END of a burst. Handing the model the OLDEST cap bytes with
        truncated_head=false was a silent, confident lie about coverage."""
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d)
            s.write("first gen line\n" * 20)
            rc, _, _, err = s.tick("--max-bytes", "4096", "--quiet")
            self.assertEqual(rc, 0, err)

            s.write("appended line ..............................\n" * 600, mode="a")
            s.write("LAST LINE: the real root cause is here\n", mode="a")
            rc, _, out, err = s.tick("--max-bytes", "4096")
            self.assertEqual(rc, 0, err)
            f = s.one()
            self.assertTrue(f["truncated_head"],
                            "a capped append is a coverage gap and must never be silent")
            self.assertGreater(f["skipped_bytes"], 0, "the gap must be counted, in bytes")
            body = open(s.mirror(), "rb").read()
            self.assertIn(b"LAST LINE: the real root cause is here", body,
                          "the NEWEST bytes are the ones the model must get")
            self.assertTrue(os.path.exists(s.mirror() + ".rot1"),
                            "the older segment is rolled aside: two non-adjacent segments must "
                            "never be concatenated into one apparently continuous file")
            self.assertIn("пропуск", out, "and the skip is named in the RU output")

    def test_the_mirror_always_ends_on_a_line_boundary(self):
        """DEVIATION 5. A mirror ending mid-line makes `wc -l` — and therefore every line
        number the model cites afterwards — off by one, and offers a fragment as a record."""
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d)
            s.write("complete\ncomplete\npartial-no-newline")
            rc, _, _, err = s.tick("--quiet")
            self.assertEqual(rc, 0, err)
            body = open(s.mirror(), "rb").read()
            self.assertEqual(body, b"complete\ncomplete\n",
                             "the trailing fragment must be left for the next tick")
            f = s.one()
            self.assertEqual(f["bytes"], 18)
            self.assertEqual((f["first_local_line"], f["last_local_line"]), (1, 2))

            s.write("-now-complete\n", mode="a")
            rc, _, _, err = s.tick("--quiet")
            self.assertEqual(rc, 0, err)
            self.assertEqual(open(s.mirror(), "rb").read(),
                             b"complete\ncomplete\npartial-no-newline-now-complete\n",
                             "and the fragment must be re-fetched WHOLE, not duplicated")

    def test_a_single_line_longer_than_the_chunk_does_not_stall_the_cursor(self):
        """The newline trim must not be able to trim everything: a log with one enormous line
        would otherwise re-fetch the same bytes forever and never advance."""
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d)
            s.write("Z" * 8000)                     # 8000 bytes, no newline anywhere
            rc, _, _, err = s.tick("--max-bytes", "4096", "--from-start", "--quiet")
            self.assertEqual(rc, 0, err)
            self.assertEqual(s.one()["bytes"], 4096,
                             "the chunk is kept as-is rather than trimmed to 0")

            rc, _, _, err = s.tick("--max-bytes", "4096", "--quiet")
            self.assertEqual(rc, 0, err)
            f = s.one()
            self.assertEqual((f["offset_from"], f["bytes"]), (4096, 3904),
                             "tick 2 must resume at the cursor and take the rest")
            self.assertEqual(os.path.getsize(s.mirror()), 8000)

            rc, _, _, err = s.tick("--max-bytes", "4096", "--quiet")
            self.assertEqual(rc, 0, err)
            self.assertEqual(s.one()["event"], "unchanged",
                             "and a static file must settle, not re-fetch forever")


class InodeDegradationIsPerFile(unittest.TestCase):
    """One file with an unusable inode must not disable rotation detection for its neighbours.

    INODE_TRACKING was a process-global that the tick never reset, so the FIRST file with a
    zero inode turned the inode rule off for every other file in that tick and in every tick
    afterwards — reproducing the garbled mid-file read DEVIATION 3 exists to prevent.
    """

    def build(self, d, inode_a):
        src = os.path.join(d, "logs"); os.makedirs(src, exist_ok=True)
        binp = os.path.join(d, "bin"); os.makedirs(binp, exist_ok=True)
        # A stub `find` so the inode column is ours to control.
        write_exec(os.path.join(binp, "find"), '#!/usr/bin/env bash\ncat "$FIND_OUT"\n')
        a = os.path.join(src, "flink-a-1.log")
        b = os.path.join(src, "flink-b-1.log")
        open(a, "w", encoding="utf-8").write("aaaa\n")
        open(b, "w", encoding="utf-8").write("old line\n" * 5)          # 45 bytes
        listing = os.path.join(d, "find_out")
        root = os.path.join(d, "out")
        env = {"PATH": binp + os.pathsep + os.environ["PATH"], "FIND_OUT": listing}

        def serve(size_b, inode_b):
            with open(listing, "w", encoding="utf-8") as fh:
                fh.write("5\t%s\t%s\n%d\t%d\t%s\n" % (inode_a, a, size_b, inode_b, b))

        def tick():
            return run("--source", "local:" + src, "--glob", "flink-*-*.log",
                       "--root", root, "--once", "--quiet", env=env)

        serve(45, 555)
        rc, _, _, err = tick()
        assert rc == 0, err
        # b is ROTATED: a brand-new file, new inode, LARGER than the old cursor.
        open(b, "w", encoding="utf-8").write("NEWLINE-CAUSE\n" * 20)    # 280 bytes
        serve(280, 556)
        rc, _, _, err = tick()
        assert rc == 0, err
        m = manifest(root, "local")
        entry = [f for f in m["files"] if f["remote"].endswith("flink-b-1.log")][0]
        return m, entry, open(os.path.join(root, "local", "logs", "flink-b-1.log"), "rb").read()

    def test_a_neighbour_with_a_usable_inode_is_the_control(self):
        with tempfile.TemporaryDirectory() as d:
            m, b, body = self.build(d, inode_a=111)
            self.assertTrue(m["inode_tracking"])
            self.assertEqual((b["event"], b["offset_from"]), ("rotated", 0))
            self.assertEqual(body, b"NEWLINE-CAUSE\n" * 20)

    def test_a_zero_inode_on_ONE_file_does_not_blind_the_others(self):
        with tempfile.TemporaryDirectory() as d:
            m, b, body = self.build(d, inode_a=0)
            self.assertFalse(m["inode_tracking"],
                             "the tick must still REPORT that a degradation happened")
            a = [f for f in m["files"] if f["remote"].endswith("flink-a-1.log")][0]
            self.assertFalse(a["inode_tracked"], "and name the file it happened to")
            self.assertTrue(b["inode_tracked"],
                            "...but file b's inode was perfectly usable")
            self.assertEqual((b["event"], b["offset_from"]), ("rotated", 0),
                             "b must still be detected as rotated")
            self.assertEqual(body, b"NEWLINE-CAUSE\n" * 20,
                             "and its mirror must not be the old content with a garbled "
                             "partial line spliced onto it")


class DryRunIsInert(unittest.TestCase):
    """«full tick, inert: no fetch exec, no cursor advance» — including on a rotation, which
    used to rename the live mirror out from under the model and then print «курсор сброшен»
    while the cursor had not in fact moved."""

    def test_a_rotation_seen_in_dry_run_changes_nothing_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d)
            s.write("KEEP 1\nKEEP 2\n")
            rc, _, _, err = s.tick("--quiet")
            self.assertEqual(rc, 0, err)
            before = open(s.mirror(), "rb").read()
            cursor_before = s.cursor()

            os.rename(s.f, s.f + ".1")
            s.write("FRESH 1\nFRESH 2\nFRESH 3\n")
            rc, _, out, err = s.tick("--dry-run")
            self.assertEqual(rc, 0, err)

            self.assertTrue(os.path.exists(s.mirror()),
                            "the file every earlier report cited must still exist")
            self.assertEqual(open(s.mirror(), "rb").read(), before,
                             "and be byte-identical: --dry-run may not touch the mirror")
            self.assertFalse(os.path.exists(s.mirror() + ".rot1"),
                             "no snapshot may be created by an inert run")
            self.assertEqual(s.cursor(), cursor_before, "and no cursor may move")
            self.assertIn("план", out,
                          "a dry run must describe the PLAN, not claim «курсор сброшен»")
            self.assertNotIn("курсор сброшен", out,
                             "«курсор сброшен» in a run that reset no cursor is a false report")


class StateWritesAreChecked(unittest.TestCase):
    """The cursor commit was the only unchecked write in the script.

    The cursor write is blocked here by putting a DIRECTORY where its `.tmp` file must go.
    A read-only `cursors/` would be the more obvious stand-in for the operator's real case
    (read-only or full state dir), but ensure_root() deliberately re-tightens the run root's
    modes on every run — an operator upgrading from the version that left them 0755 must get
    them repaired — so a chmod the test applies would simply be undone. The code path under
    test is identical: the write fails, and the tick must say so.
    """

    def test_a_failed_cursor_commit_fails_the_tick_and_does_not_duplicate_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d)
            s.write("a\n")
            rc, _, _, err = s.tick("--quiet")
            self.assertEqual(rc, 0, err)
            cursors = os.path.join(s.root, "local", "state", "cursors")
            ckey = hashlib.sha1(os.path.realpath(s.f).encode()).hexdigest()
            self.assertTrue(os.path.exists(os.path.join(cursors, ckey)),
                            "this arm is void unless the cursor key is the one the script "
                            "uses: %r" % os.listdir(cursors))
            os.makedirs(os.path.join(cursors, ckey + ".tmp"))
            s.write("more\n", mode="a")

            sizes = []
            for _ in range(3):
                rc, _, out, err = s.tick()
                self.assertEqual(rc, 40,
                                 "a state-write failure is exit 40 per the header, not 0")
                self.assertNotIn("Permission denied", err,
                                 "the raw bash error must not leak: %r" % err)
                self.assertNotIn("cannot stat", err,
                                 "nor the raw `mv` error: %r" % err)
                self.assertIn("cursor", err, "the failure must be NAMED: %r" % err)
                sizes.append(os.path.getsize(s.mirror()))

            self.assertEqual(len(set(sizes)), 1,
                             "the mirror must not keep growing: every tick re-appended the "
                             "same bytes and handed the model a burst that never happened "
                             "(sizes: %r)" % sizes)
            self.assertEqual(open(s.mirror(), "rb").read(), b"a\nmore\n",
                             "and it must hold the log exactly once")


class OneRootBelongsToOneSource(unittest.TestCase):
    """Two sources sharing a root interleaved their bytes into one mirror with no marker, and
    shared one cursor set keyed by sha1(remote path)."""

    def test_a_second_local_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a"); os.makedirs(a)
            b = os.path.join(d, "b"); os.makedirs(b)
            open(os.path.join(a, "app.log"), "w").write("AAA from dir a\n")
            open(os.path.join(b, "app.log"), "w").write("BBB from dir b\n")
            root = os.path.join(d, "out")
            rc, _, _, err = run("--source", "local:" + a, "--glob", "*.log",
                                "--root", root, "--once", "--quiet")
            self.assertEqual(rc, 0, err)
            rc, _, _, err = run("--source", "local:" + b, "--glob", "*.log",
                                "--root", root, "--once", "--quiet")
            self.assertEqual(rc, 42, "a second source in the same root must be refused")
            self.assertIn("--root", err, "and the message must name the way out: %r" % err)
            self.assertEqual(open(os.path.join(root, "local", "logs", "app.log"), "rb").read(),
                             b"AAA from dir a\n",
                             "dir b's bytes must never reach dir a's mirror")

    def test_two_stands_whose_configs_share_a_filename_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "out")
            cfgs = []
            for host in ("alpha.example.com", "bravo.example.com"):
                sub = os.path.join(d, host); os.makedirs(sub)
                cfgs.append(write_cfg(os.path.join(sub, "stand.ini"), """
                    [stand]
                    host = %s
                    user = ops
                    password_env = SHERLOCK_STAND_PASSWORD
                    log_dir = /opt/flink/current/log
                    """ % host))
            binp = os.path.join(d, "bin"); os.makedirs(binp)
            write_exec(os.path.join(binp, "ssh"), SSH_STUB)
            listing = os.path.join(d, "listing")
            open(listing, "w", encoding="utf-8").write("")
            env = {"PATH": binp + os.pathsep + os.environ["PATH"],
                   "SSH_STUB_LOG": os.path.join(d, "j"),
                   "SSH_STUB_META": os.path.join(d, "m"),
                   "SSH_STUB_LIST": listing, "SSH_STUB_BODY": "",
                   "SHERLOCK_STAND_PASSWORD": PASSWORD}
            rc, _, _, err = run("--config", cfgs[0], "--root", root, "--once", "--quiet",
                                env=env)
            self.assertEqual(rc, 0, err)
            rc, _, _, err = run("--config", cfgs[1], "--root", root, "--once", "--quiet",
                                env=env)
            self.assertEqual(rc, 42,
                             "both configs are called stand.ini, so both claim the profile "
                             "`stand` — and would share one cursor set: bravo's first fetch "
                             "would start at alpha's byte offset")


class ExcludeGlobIsDataNotAPathnamePattern(unittest.TestCase):
    """`for g in $c_exclude_glob` let bash glob the PATTERNS against the current working
    directory, so the value that passed validation was not the value that got used."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.path.join(d, "cwd"); os.makedirs(self.cwd)
        self.src = os.path.join(d, "logs"); os.makedirs(self.src)
        open(os.path.join(self.src, "app.log"), "w", encoding="utf-8").write("a\n")
        open(os.path.join(self.src, "archive.zip"), "w").close()
        open(os.path.join(self.src, "rotated.zip"), "w").close()
        self.d = d

    def go(self, root):
        rc, _, _, err = run("--source", "local:" + self.src, "--glob", "*",
                            "--root", os.path.join(self.d, root), "--once", "--quiet",
                            cwd=self.cwd)
        return rc, err

    def result(self, root):
        m = manifest(os.path.join(self.d, root), "local")
        return (sorted(s["name"] for s in m["skipped"]),
                sorted(f["remote"].split("/")[-1] for f in m["files"]))

    def test_control_the_exclusions_work(self):
        rc, err = self.go("out1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.result("out1"),
                         (["archive.zip", "rotated.zip"], ["app.log"]))

    def test_a_matching_file_in_the_CWD_must_not_disable_the_exclusions(self):
        open(os.path.join(self.cwd, "anything.zip"), "w").close()
        rc, err = self.go("out2")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.result("out2"),
                         (["archive.zip", "rotated.zip"], ["app.log"]),
                         "a .zip in the CWD made `*.zip` expand to that name, so nothing "
                         "matched and .zip binaries were fetched into the evidence mirror")

    def test_an_awkward_filename_in_the_CWD_must_not_kill_the_tool(self):
        for name in ("my report.out", "-weird.gz", "backup #1.zip"):
            open(os.path.join(self.cwd, name), "w").close()
        rc, err = self.go("out3")
        self.assertEqual(rc, 0,
                         "the CWD's contents must not become an exit 11 blaming a config "
                         "value the operator never wrote: %r" % err)
        self.assertEqual(self.result("out3"),
                         (["archive.zip", "rotated.zip"], ["app.log"]))


class ZeroFilesIsNotNoLogs(unittest.TestCase):
    """`flink-*-*.log` is the SPEC's default and belongs to the Flink stand. On a generic
    directory it fetched nothing and exited 0, and SKILL.md's local example carried no --glob —
    so a model following it got an empty manifest with a success code."""

    def test_local_mode_with_no_glob_takes_every_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            open(os.path.join(src, "app.log"), "w", encoding="utf-8").write(
                "2026-07-30 10:00:00.000 [main] ERROR o.a.f.Task - boom\n")
            open(os.path.join(src, "service-2026-07-30.log"), "w",
                 encoding="utf-8").write("line\n")
            root = os.path.join(d, "out")
            rc, _, _, err = run("--source", "local:" + src, "--root", root, "--once",
                                "--quiet")
            self.assertEqual(rc, 0, err)
            m = manifest(root, "local")
            self.assertEqual(m["source"]["file_glob"], "*",
                             "a generic local directory has no Flink naming convention")
            self.assertEqual(sorted(f["remote"].split("/")[-1] for f in m["files"]),
                             ["app.log", "service-2026-07-30.log"])

    def test_a_config_or_flag_glob_still_wins_in_local_mode(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            open(os.path.join(src, "app.log"), "w", encoding="utf-8").write("a\n")
            open(os.path.join(src, "other.txt"), "w", encoding="utf-8").write("b\n")
            root = os.path.join(d, "out")
            rc, _, _, err = run("--source", "local:" + src, "--glob", "*.log",
                                "--root", root, "--once", "--quiet")
            self.assertEqual(rc, 0, err)
            names = [f["remote"].split("/")[-1] for f in manifest(root, "local")["files"]]
            self.assertEqual(names, ["app.log"], "--glob must still be honoured")

    def test_a_zero_file_tick_names_the_mask_that_matched_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            open(os.path.join(src, "app.log"), "w", encoding="utf-8").write("a\n")
            root = os.path.join(d, "out")
            rc, _, out, err = run("--source", "local:" + src, "--glob", "flink-*-*.log",
                                  "--root", root, "--once")
            self.assertEqual(rc, 0, err)
            self.assertEqual(manifest(root, "local")["totals"]["files"], 0)
            self.assertIn("flink-*-*.log", out,
                          "«0 файл(ов)» alone reads as «there are no logs»; the mask that "
                          "matched nothing must be named: %r" % out)


class AFailedTickStillHandsOverAMap(unittest.TestCase):
    """manifest.json was rewritten with `"files":[]` on a listing failure while the previously
    fetched logs were still on disk, so SKILL.md's «манифест — это твоя карта» and «разбирай
    то, что уже забрано» could not both be obeyed."""

    def test_a_listing_failure_leaves_the_fetched_files_in_the_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(18, "line1\nline2\nline3\n")
            rc, _, _, err = s.tick()
            self.assertEqual(rc, 0, err)
            mirror = os.path.join(s.root, "stand", "logs", "flink-taskexecutor-1.log")
            self.assertEqual(os.path.getsize(mirror), 18)

            rc, _, out, err = s.tick(env={"SSH_STUB_RC": "255"})
            self.assertEqual(rc, 20)
            m = manifest(s.root, "stand")
            self.assertTrue(m["errors"], "the failure must be recorded")
            names = [f["remote"].split("/")[-1] for f in m["files"]]
            self.assertIn("flink-taskexecutor-1.log", names,
                          "the bytes are still on disk; a map that omits them tells the "
                          "model there is nothing to analyse")
            e = [f for f in m["files"] if f["remote"].endswith("taskexecutor-1.log")][0]
            self.assertEqual(e["event"], "stale",
                             "and it must say plainly that this entry was not refreshed")
            self.assertEqual(os.path.getsize(mirror), 18, "the mirror is untouched")


class TheListingFailureIsDiagnosable(unittest.TestCase):
    """A listing failure used to escape local mode as the raw `1` — the code the header
    reserves for "usage error: unknown flag" — with a completely empty stderr."""

    def stub_toolchain(self, d, bsd_stat=False):
        """A toolchain with no GNU `find -printf` and no GNU `stat -c`.

        With `bsd_stat=True` the stub also EMULATES BSD `stat -f '%z %i %N'` — it cannot simply
        pass `-f` through to GNU stat, where `-f` means "file system" and would report
        `cannot read file system information for '%z %i %N'`. Emulating it is the only way to
        exercise the third listing form on a GNU box.
        """
        binp = os.path.join(d, "bin"); os.makedirs(binp, exist_ok=True)
        real_find = shutil.which("find")
        real_stat = shutil.which("stat")
        write_exec(os.path.join(binp, "find"),
                   '#!/usr/bin/env bash\n'
                   'for a in "$@"; do [ "$a" = "-printf" ] && '
                   '{ echo "find: unknown primary or operator: -printf" >&2; exit 1; }; done\n'
                   'exec %s "$@"\n' % real_find)
        body = ['#!/usr/bin/env bash',
                'for a in "$@"; do [ "$a" = "-c" ] && '
                '{ echo "stat: illegal option -- c" >&2; exit 1; }; done']
        if bsd_stat:
            body += [
                'if [ "${1:-}" = "-f" ]; then',
                '  [ "${2:-}" = "%z %i %N" ] || { echo "stat: bad format" >&2; exit 1; }',
                '  shift 2; [ "${1:-}" = "--" ] && shift',
                '  for f in "$@"; do %s -c "%%s %%i %%n" -- "$f" || exit 1; done' % real_stat,
                '  exit 0',
                'fi']
        else:
            body.append('for a in "$@"; do [ "$a" = "-f" ] && '
                        '{ echo "stat: illegal option -- f" >&2; exit 1; }; done')
        body.append('exec %s "$@"' % real_stat)
        write_exec(os.path.join(binp, "stat"), "\n".join(body) + "\n")
        return {"PATH": binp + os.pathsep + os.environ["PATH"]}

    def test_no_listing_form_works_at_all_is_exit_23_with_a_named_reason(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d, glob="*.log", name="app.log")
            s.write("L1\n")
            env = dict(os.environ, **self.stub_toolchain(d))
            rc, _, out, err = s.tick(env=env)
            self.assertEqual(rc, 23,
                             "«listing unusable» is 23; `1` means the CALLER made a mistake")
            self.assertTrue(err.strip(),
                            "an empty stderr leaves the operator with no diagnosis at all")
            self.assertIn("listing failed", err, err)
            runlog = open(os.path.join(s.root, "local", "run.log"), encoding="utf-8").read()
            self.assertIn("ошибка", runlog,
                          "and run.log — the sink that survives the terminal — must say so")

    def test_a_BSD_toolchain_is_supported_not_merely_diagnosed(self):
        """Local mode is advertised as the always-available no-SSH path, and check_perms
        already carries a BSD `stat -f` fallback. Without a BSD listing form the advertised
        path was dead on macOS."""
        with tempfile.TemporaryDirectory() as d:
            s = LocalStand(d, glob="*.log", name="app.log")
            s.write("L1\nL2\n")
            env = dict(os.environ, **self.stub_toolchain(d, bsd_stat=True))
            rc, _, _, err = s.tick("--quiet", env=env)
            self.assertEqual(rc, 0, "the BSD `stat -f` form must carry the listing: %s" % err)
            self.assertTrue(s.m()["listing_fallback"],
                            "and the manifest must record that a fallback form was used")
            self.assertEqual(open(s.mirror(), "rb").read(), b"L1\nL2\n")


class BoundedWatchReportsItsOutcome(unittest.TestCase):
    """`exit 0` unconditionally meant a bounded watch whose every tick failed reported success
    to its caller — and this mode is sold as "scriptable from any external scheduler"."""

    def stand(self, d):
        s = Stand(d, auth="identity", extra="")
        s.serve(5000, "")
        return s

    def test_a_watch_in_which_every_tick_failed_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.stand(d)
            rc, _, _, _ = run("--config", s.cfg, "--root", s.root, "--once",
                              env=s.env(SSH_STUB_FETCH_RC="1"))
            self.assertEqual(rc, 20, "--once already reported this correctly")
            shutil.rmtree(os.path.join(s.root), ignore_errors=True)
            rc, _, _, _ = run("--config", s.cfg, "--root", s.root, "--watch",
                              "--max-ticks", "3", "--poll", "1", "--quiet",
                              env=s.env(SSH_STUB_FETCH_RC="1"), timeout=60)
            self.assertNotEqual(rc, 0,
                                "three failed ticks out of three must not be reported as "
                                "success; SHERLOCK_MAX_CONSEC_FAIL must also be capped at "
                                "--max-ticks so the abort path is reachable at all")

    def test_a_watch_that_fetched_something_still_succeeds(self):
        """A stand reboot mid-watch must not fail the whole run."""
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            open(os.path.join(src, "a.logsample"), "w", encoding="utf-8").write("x\n")
            root = os.path.join(d, "out")
            rc, _, _, err = run("--source", "local:" + src, "--glob", "*.logsample",
                                "--root", root, "--watch", "--max-ticks", "2", "--poll", "1",
                                "--quiet", timeout=60)
            self.assertEqual(rc, 0, err)


class ThePrintedTemplateIsAConfigFile(unittest.TestCase):
    """exit 13 exists so the operator does not have to guess the file's shape. A template the
    script's own parser rejects with exit 10 is worse than no template."""

    def template(self, d):
        rc, _, out, err = run("--config", os.path.join(d, "nope.ini"), "--check")
        self.assertEqual(rc, 13)
        text = err if "[stand]" in err else out
        block = text.split("[stand]", 1)[1]
        block = "[stand]" + block.split("\nThen (", 1)[0]
        return block.rstrip() + "\n"

    def test_the_printed_template_actually_parses(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "pasted.ini")
            with open(cfg, "w", encoding="utf-8") as fh:
                fh.write(self.template(d))
            os.chmod(cfg, 0o600)
            rc, _, _, err = run("--config", cfg, "--root", os.path.join(d, "out"), "--check",
                                env=dict(os.environ, SHERLOCK_STAND_PASSWORD=PASSWORD))
            self.assertNotEqual(rc, 10,
                                "the file the tool hands the operator must parse; got a "
                                "PARSE error: %s" % err.strip())
            self.assertEqual(rc, 0, err)

    def test_the_template_block_carries_no_shell_commands(self):
        with tempfile.TemporaryDirectory() as d:
            for line in self.template(d).splitlines():
                self.assertFalse(line.startswith((" ", "\t")),
                                 "an indented line is refused by the parser: %r" % line)
                self.assertNotIn("chmod 600", line,
                                 "shell steps belong outside the config block: %r" % line)
                self.assertNotIn("fetch-logs.sh", line,
                                 "shell steps belong outside the config block: %r" % line)


class PermissionsDoNotDependOnInvocationOrder(unittest.TestCase):
    """--probe's own `mkdir` was missing the umask the normal path used, so $ROOT and every
    artifact were world-readable if --probe ran first — and --probe-first is the order the
    docs recommend."""

    def modes(self, root, profile):
        out = {}
        for rel in ("", profile, os.path.join(profile, "state"),
                    os.path.join(profile, "logs")):
            p = os.path.join(root, rel) if rel else root
            if os.path.isdir(p):
                out[rel or "."] = oct(os.stat(p).st_mode & 0o777)
        for rel in ("manifest.json", "manifest.jsonl", "run.log", "fetch-log.jsonl",
                    os.path.join("state", "listing.txt")):
            p = os.path.join(root, profile, rel)
            if os.path.isfile(p):
                out[rel] = oct(os.stat(p).st_mode & 0o777)
        return out

    def test_probe_first_gives_the_same_tight_modes_as_a_bare_tick(self):
        with tempfile.TemporaryDirectory() as d:
            s = Stand(d, auth="identity")
            s.serve(4, "AAAA")
            probed = os.path.join(d, "out-probed")
            rc, _, _, err = run("--config", s.cfg, "--root", probed, "--probe", env=s.env())
            self.assertEqual(rc, 0, err)
            rc, _, _, err = run("--config", s.cfg, "--root", probed, "--once", "--quiet",
                                env=s.env())
            self.assertEqual(rc, 0, err)

            bare = os.path.join(d, "out-bare")
            rc, _, _, err = run("--config", s.cfg, "--root", bare, "--once", "--quiet",
                                env=s.env())
            self.assertEqual(rc, 0, err)

            a, b = self.modes(probed, "stand"), self.modes(bare, "stand")
            self.assertEqual(a, b,
                             "security posture must not depend on whether --probe ran first")
            for rel, mode in a.items():
                self.assertNotIn(mode[-1], "4567",
                                 "%s is other-readable (%s) — this box has a provisioned "
                                 "guest account" % (rel, mode))
                self.assertNotIn(mode[-2], "4567",
                                 "%s is group-readable (%s)" % (rel, mode))


class RemoteControlledNamesAreContained(unittest.TestCase):
    """A remote FILENAME is attacker-controlled data. A raw 0x1b in one made manifest.json
    invalid JSON — and SKILL.md tells the model the manifest IS its map."""

    NAME = "flink-\033[2J\033[31mPWNED-y.log"

    def test_a_control_byte_in_a_name_keeps_the_manifest_parseable(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            with open(os.path.join(src, self.NAME), "w", encoding="utf-8") as fh:
                fh.write("x\n")
            root = os.path.join(d, "out")
            rc, _, out, err = run("--source", "local:" + src, "--glob", "flink-*-*.log",
                                  "--root", root, "--once")
            self.assertEqual(rc, 0, err)
            raw = open(os.path.join(root, "local", "manifest.json"), "rb").read()
            m = json.loads(raw)              # must not raise
            self.assertNotIn(b"\x1b", raw, "no raw escape byte may reach the manifest")
            self.assertEqual([s["reason"] for s in m["skipped"]], ["unsafe_name"],
                             "the file is refused, and the refusal is recorded so the "
                             "coverage discipline still sees it")
            self.assertEqual(m["totals"]["files"], 0, "and it is never fetched")

    def test_no_escape_sequence_reaches_the_terminal_or_the_run_log(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "logs"); os.makedirs(src)
            with open(os.path.join(src, self.NAME), "w", encoding="utf-8") as fh:
                fh.write("x\n")
            root = os.path.join(d, "out")
            rc, _, out, err = run("--source", "local:" + src, "--glob", "flink-*-*.log",
                                  "--root", root, "--once")
            self.assertEqual(rc, 0, err)
            self.assertNotIn("\033[2J", out,
                             "a remote-controlled name cleared the operator's screen")
            runlog = open(os.path.join(root, "local", "run.log"), "rb").read()
            self.assertNotIn(b"\x1b[2J", runlog,
                             "run.log is read back by the agent as evidence — it is an "
                             "injection channel into the model's context too")


class TheTransportReasonSurvives(unittest.TestCase):
    """Every ssh exec ended in `2>/dev/null`, so «Permission denied (publickey,password)» was
    destroyed at the door: an unreachable stand produced a header line, an EMPTY run.log and
    nothing else."""

    REASON = "flink@stand.example: Permission denied (publickey,password)."

    def stand(self, d):
        s = Stand(d)
        binp = s.bin
        write_exec(os.path.join(binp, "ssh"),
                   '#!/usr/bin/env bash\n'
                   'case " $* " in (*" -V "*) echo "OpenSSH_10.3p1" >&2; exit 0 ;; esac\n'
                   'echo %r >&2\nexit 255\n' % self.REASON)
        return s

    def test_the_reason_reaches_stderr_and_run_log(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.stand(d)
            rc, _, out, err = s.tick()
            self.assertEqual(rc, 20)
            self.assertIn("Permission denied", err,
                          "sshd's own reason is the diagnosis: %r" % err)
            runlog = open(os.path.join(s.root, "stand", "run.log"), encoding="utf-8").read()
            self.assertTrue(runlog.strip(), "run.log must not be empty on a failed tick")
            self.assertIn("Permission denied", runlog, runlog)

    def test_the_probe_names_the_reason_too(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.stand(d)
            rc, _, _, err = run("--config", s.cfg, "--root", s.root, "--probe", env=s.env())
            self.assertEqual(rc, 20)
            self.assertIn("Permission denied", err, err)

    def test_the_captured_stderr_still_contains_no_secret(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.stand(d)
            s.tick()
            for base, _, files in os.walk(s.root):
                for name in files:
                    p = os.path.join(base, name)
                    self.assertNotIn(PASSWORD.encode(), open(p, "rb").read(),
                                     "capturing ssh's stderr must not open a leak: %s" % p)


class APasswordIsNeverOfferedToAnUnverifiedHost(unittest.TestCase):
    """accept-new is trust-on-first-use, and known_hosts is PER ROOT — so "first use" recurs on
    every fresh root and the secret goes to whatever host answers."""

    def argv(self, d, extra="", env=None):
        cfg = write_cfg(os.path.join(d, "stand.ini"), """
            [stand]
            host = stand.example
            user = flink
            password_env = SHERLOCK_STAND_PASSWORD
            %s
            """ % extra)
        e = dict(os.environ, SHERLOCK_STAND_PASSWORD=PASSWORD)
        if env:
            e.update(env)
        rc, data, out, err = run("--config", cfg, "--root", os.path.join(d, "out"),
                                 "--print-ssh-argv", "--json", env=e)
        return rc, (data or {}).get("argv", []), err

    def test_the_default_becomes_strict_yes_for_password_auth(self):
        with tempfile.TemporaryDirectory() as d:
            rc, argv, err = self.argv(d)
            self.assertEqual(rc, 0, err)
            self.assertIn("StrictHostKeyChecking=yes", argv,
                          "a password may not be sent to an unverified host key: %r" % argv)
            self.assertNotIn("StrictHostKeyChecking=accept-new", argv)

    def test_an_explicit_unsafe_policy_plus_a_password_is_refused(self):
        for policy in ("no", "accept-new"):
            with tempfile.TemporaryDirectory() as d:
                rc, _, err = self.argv(d, "strict_host_key = %s" % policy)
                self.assertEqual(rc, 11,
                                 "strict_host_key = %s with password auth must be refused, "
                                 "not silently accepted" % policy)
                self.assertIn("ssh-keyscan", err,
                              "and the message must name the fix: %r" % err)

    def test_key_auth_keeps_accept_new(self):
        with tempfile.TemporaryDirectory() as d:
            key = os.path.join(d, "id_key")
            open(key, "w", encoding="utf-8").write("x\n")
            os.chmod(key, 0o600)
            cfg = write_cfg(os.path.join(d, "s.ini"), """
                [stand]
                host = stand.example
                user = flink
                identity_file = %s
                """ % key)
            rc, data, _, err = run("--config", cfg, "--root", os.path.join(d, "out"),
                                   "--print-ssh-argv", "--json")
            self.assertEqual(rc, 0, err)
            self.assertIn("StrictHostKeyChecking=accept-new", data["argv"],
                          "with a key an unknown host costs a failed handshake, not a "
                          "credential — TOFU stays available there")

    def test_the_per_root_known_hosts_is_seeded_from_the_users_own(self):
        """Starting the per-root file EMPTY threw away the trust the operator already had,
        which is what made StrictHostKeyChecking=yes unusable and accept-new tempting."""
        with tempfile.TemporaryDirectory() as d:
            home = os.path.join(d, "home"); os.makedirs(os.path.join(home, ".ssh"))
            marker = "stand.example ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEKEYFORTESTS\n"
            with open(os.path.join(home, ".ssh", "known_hosts"), "w",
                      encoding="utf-8") as fh:
                fh.write(marker)
            s = Stand(d)
            s.serve(4, "AAAA")
            rc, _, _, err = run("--config", s.cfg, "--root", s.root, "--once", "--quiet",
                                env=s.env(HOME=home))
            self.assertEqual(rc, 0, err)
            kh = os.path.join(s.root, "stand", "state", "known_hosts")
            self.assertTrue(os.path.exists(kh), "the per-root known_hosts must exist")
            self.assertIn(marker, open(kh, encoding="utf-8").read(),
                          "existing trust must be carried in, read-only")
            self.assertEqual(open(os.path.join(home, ".ssh", "known_hosts"),
                                  encoding="utf-8").read(), marker,
                             "and the operator's own file must never be modified")

    def test_a_bare_check_seeds_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            home = os.path.join(d, "home"); os.makedirs(os.path.join(home, ".ssh"))
            open(os.path.join(home, ".ssh", "known_hosts"), "w").write("x\n")
            s = Stand(d)
            rc, _, _, err = run("--config", s.cfg, "--root", s.root, "--check",
                                env=s.env(HOME=home))
            self.assertEqual(rc, 0, err)
            self.assertFalse(os.path.exists(os.path.join(s.root, "stand", "state")),
                             "--check is documented as offline and must touch nothing")


class TheSecretIsDeliveredByteExact(unittest.TestCase):
    """One validation rule for BOTH delivery paths, and no silent mutation of secret bytes."""

    def cfg(self, d, body):
        return write_cfg(os.path.join(d, "s.ini"), body)

    GOOD = """
        [stand]
        host = h.example
        user = u
        strict_host_key = yes
        """

    def test_a_multiline_password_env_is_refused_not_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.cfg(d, self.GOOD + "password_env = PW\n")
            rc, _, _, err = run("--config", cfg, "--root", os.path.join(d, "o"), "--check",
                                env=dict(os.environ, PW="first\nsecond"))
            self.assertEqual(rc, 11,
                             "the askpass channel is ONE line, so ssh would authenticate "
                             "with «first» and the stand would look like it refused you")
            self.assertIn("control character", err, err)

    def test_the_same_rule_applies_to_an_inline_password(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.cfg(d, self.GOOD + "password = a\tb\n")
            rc, _, _, err = run("--config", cfg, "--root", os.path.join(d, "o"), "--check")
            self.assertEqual(rc, 11, "validation must not depend on the delivery path")

    def test_an_inline_password_is_not_trimmed_or_unquoted(self):
        """Trailing whitespace and quote characters are significant BYTES of a secret.
        Dropping them means authenticating with bytes the operator never typed and then
        reporting «стенд не пустил» — a wrong diagnosis for a parser bug."""
        with tempfile.TemporaryDirectory() as d:
            secret = '"pa ss"   '
            cfg = self.cfg(d, self.GOOD + "password = %s\n" % secret)
            s = Stand(d)                                  # for its stub ssh + meta capture
            os.remove(s.cfg)
            rc, _, _, err = run("--config", cfg, "--root", s.root, "--once", "--quiet",
                                env=s.env())
            self.assertEqual(rc, 0, err)
            meta = open(s.meta, encoding="utf-8").read()
            stdout = meta.split("=== askpass stdout", 1)[1].split("=== askpass end", 1)[0]
            self.assertEqual(stdout.strip("\n"), secret,
                             "ssh must receive the operator's exact bytes, got %r" % stdout)


class TheIPv6FormThatIsDocumentedActuallyWorks(unittest.TestCase):
    """validate_config accepted `[2001:db8::1]` and conf.example documented it, but OpenSSH
    does not strip the brackets from a positional host and getaddrinfo cannot resolve them —
    so the documented feature always failed as a nameless exit 20."""

    def test_a_bracketed_ipv6_reaches_argv_unbracketed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = write_cfg(os.path.join(d, "v6.ini"), """
                [stand]
                host = [2001:db8::1]
                user = ops
                password_env = SHERLOCK_STAND_PASSWORD
                strict_host_key = yes
                """)
            rc, data, _, err = run("--config", cfg, "--root", os.path.join(d, "o"),
                                   "--print-ssh-argv", "--json",
                                   env=dict(os.environ, SHERLOCK_STAND_PASSWORD=PASSWORD))
            self.assertEqual(rc, 0, err)
            argv = data["argv"]
            self.assertEqual(argv[-2], "2001:db8::1",
                             "brackets exist for host:port syntax, which this script never "
                             "builds — the port is -p: %r" % argv)
            self.assertNotIn("[2001:db8::1]", argv)


class TheMultiplexOptInIsOptIn(unittest.TestCase):
    """The spec's «no persistent connection is ever held» is motivated by the stand's observed
    auto-logout, so ControlMaster stays OFF by default. The lever exists for operators whose
    stand does not force-close idle sessions and who would otherwise pay one full password
    authentication per grown file per tick."""

    def argv(self, d, env=None):
        s = Stand(d, auth="identity")
        rc, data, _, err = run("--config", s.cfg, "--root", s.root, "--print-ssh-argv",
                               "--json", env=s.env(**(env or {})))
        self.assertEqual(rc, 0, err)
        return data["argv"]

    def test_off_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            argv = self.argv(d)
            self.assertIn("ControlMaster=no", argv)
            self.assertIn("ControlPath=none", argv)

    def test_the_opt_in_shares_one_connection_per_tick(self):
        with tempfile.TemporaryDirectory() as d:
            argv = self.argv(d, env={"SHERLOCK_SSH_MULTIPLEX": "1"})
            self.assertIn("ControlMaster=auto", argv)
            paths = [a for a in argv if a.startswith("ControlPath=")]
            self.assertEqual(len(paths), 1)
            self.assertNotEqual(paths[0], "ControlPath=none")
            self.assertTrue(any(a.startswith("ControlPersist=") for a in argv),
                            "a master with no persist window cannot be shared: %r" % argv)

    def test_the_socket_never_lives_under_the_run_root(self):
        """A unix socket path is capped at ~108 bytes and the run root can be arbitrarily
        deep."""
        with tempfile.TemporaryDirectory() as d:
            argv = self.argv(d, env={"SHERLOCK_SSH_MULTIPLEX": "1"})
            path = [a for a in argv if a.startswith("ControlPath=")][0]
            self.assertNotIn("/out/", path, path)
            self.assertLess(len(path.split("=", 1)[1]), 100, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
