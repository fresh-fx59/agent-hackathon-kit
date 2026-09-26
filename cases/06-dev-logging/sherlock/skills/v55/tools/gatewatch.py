"""v53 gate runner: heartbeat watchdog, CPU-stall watchdog, hard ceiling,
verdict cache and the K=2 timeout rule (spec 2026-09-24 items 2-5, 9).

All limits live HERE, in one place, so a measured revision swaps them once.
"""
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time


def _sibling(name, _here=os.path.dirname(os.path.abspath(__file__))):
    """v55: load a sibling tool BY PATH under a path+identity-unique module name.

    Bare `import ccindex` resolves through sys.path and the process-wide
    sys.modules cache, so a process that has touched another package copy (the
    hook's stopcheck vs the expected-root finalize, a trusted tree vs the model's
    tree, or a test that loaded a since-deleted package) silently binds this
    package's gate to a DIFFERENT package's checker. Never use sys.path here.
    """
    import hashlib as _hl
    import importlib.util as _iu
    path = os.path.join(_here, name + ".py")
    st = os.stat(path)
    ident = "%s|%d|%d|%d" % (path, st.st_ino, st.st_size, st.st_mtime_ns)
    key = "sherlock_sib_%s_%s" % (_hl.sha256(ident.encode()).hexdigest()[:16], name)
    mod = sys.modules.get(key)
    if mod is None:
        spec = _iu.spec_from_file_location(key, path)
        mod = _iu.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return mod


HB = _sibling("heartbeat")

# Spec v4 item 4: no heartbeat for 30 s -> kill; no CPU progress for 5 s
# (25 x the 0.2 s max CPU gap measured, TM §2) -> kill. No I/O watchdog.
HEARTBEAT_LIMIT_S = 50.0  # spec v4.1 [SD]: slowest step 16.3 s x 3
CPU_STALL_LIMIT_S = 5.0
POLL_S = 0.2
# Spec item 3: outer Qwen Stop-hook timeout exported by the harness; the hook's
# own ceiling is outer - CEILING_MARGIN_S. Default when unset = 600 (spec).
OUTER_TIMEOUT_ENV = "SHERLOCK_STOP_HOOK_TIMEOUT_S"
DEFAULT_OUTER_TIMEOUT_S = 600.0
CEILING_MARGIN_S = 10.0
# Spec item 5: K consecutive timeouts on unchanged inputs -> checker_fault.
TIMEOUT_K = 2
CHECKER_FAULT_MULTIPLIER = 2.0
CHECKER_VERSION = "sherlock-v55"
STDERR_TAIL = 2048
CACHE_DIRNAME = "verdicts"
DETAIL_NAME = "stopcheck-detail.json"
FAULT_RE = re.compile(r"^CHECKER-FAULT:\s*(\S+)\s*(.*)$", re.M)
REF_RE = re.compile(r"^REPORT\s+(\S+)\s+sha256=([0-9a-f]{64})\s+bytes=(\d+)\s*$")


def outer_timeout_s():
    raw = os.environ.get(OUTER_TIMEOUT_ENV)
    try:
        value = float(raw) if raw else DEFAULT_OUTER_TIMEOUT_S
    except ValueError:
        value = DEFAULT_OUTER_TIMEOUT_S
    return value if value > CEILING_MARGIN_S else DEFAULT_OUTER_TIMEOUT_S


def ceiling_s():
    return outer_timeout_s() - CEILING_MARGIN_S


def timeout_message(gate, seconds, cause):
    return ("%s timed out after %d s (%s) — not a citation error; do not edit the report"
            % (gate, int(round(seconds)), cause))


# ------------------------------------------------------------------ CPU
def _cpu_seconds(pid):
    """utime+stime of pid, or None if unknown."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / float(os.sysconf("SC_CLK_TCK"))
    except (OSError, IndexError, ValueError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    try:
        total = 0.0
        day, _, rest = out.rpartition("-")
        for part in rest.split(":"):
            total = total * 60 + float(part)
        return total + (float(day) * 86400 if day else 0.0)
    except ValueError:
        return None


class Result:
    def __init__(self, rc, stdout, stderr, cause, elapsed, beats):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr
        self.cause, self.elapsed, self.beats = cause, elapsed, beats


def run_watched(argv, ceiling, cwd=None, env=None, hb_limit=None, cpu_limit=None):
    """Run one checker; kill on no heartbeat / no CPU / ceiling.

    cause is None (finished), "no heartbeat", "no CPU" or "ceiling".
    stdout/stderr are bytes.
    """
    hb_limit = HEARTBEAT_LIMIT_S if hb_limit is None else hb_limit
    cpu_limit = CPU_STALL_LIMIT_S if cpu_limit is None else cpu_limit
    child_env = dict(os.environ if env is None else env)
    child_env[HB.ENV] = "1"
    start = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=cwd, env=child_env)
    except OSError as error:
        return Result(127, b"", str(error).encode(), None, 0.0, 0)
    state = {"last": start, "beats": 0}
    out_chunks, err_chunks = [], []

    def pump_out():
        for chunk in iter(lambda: proc.stdout.read(65536), b""):
            out_chunks.append(chunk)

    def pump_err():
        for line in iter(proc.stderr.readline, b""):
            err_chunks.append(line)
            if HB.parse(line.decode("utf-8", "replace")):
                state["last"] = time.monotonic()
                state["beats"] += 1

    threads = [threading.Thread(target=pump_out, daemon=True),
               threading.Thread(target=pump_err, daemon=True)]
    for t in threads:
        t.start()
    cause = None
    cpu_last, cpu_at = _cpu_seconds(proc.pid), start
    while proc.poll() is None:
        time.sleep(POLL_S)
        now = time.monotonic()
        if now - start >= ceiling:
            cause = "ceiling"
        elif now - state["last"] >= hb_limit:
            cause = "no heartbeat"
        else:
            cpu = _cpu_seconds(proc.pid)
            if cpu is not None and (cpu_last is None or cpu > cpu_last):
                cpu_last, cpu_at = cpu, now
            elif cpu is not None and now - cpu_at >= cpu_limit:
                cause = "no CPU"
        if cause:
            proc.kill()
            break
    proc.wait()
    for t in threads:
        t.join(2)
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()
        except OSError:
            pass
    elapsed = time.monotonic() - start
    rc = 124 if cause else proc.returncode
    return Result(rc, b"".join(out_chunks), b"".join(err_chunks), cause, elapsed, state["beats"])


def stderr_tail(data):
    """Last STDERR_TAIL bytes, heartbeat lines dropped."""
    lines = [l for l in data.splitlines(True)
             if not HB.parse(l.decode("utf-8", "replace"))]
    return b"".join(lines)[-STDERR_TAIL:].decode("utf-8", "replace")


# ---------------------------------------------------------------- cache
def _sha(data):
    return hashlib.sha256(data).hexdigest()


def cache_key(inputs, data_sha256, package_sha256):
    """inputs: sorted [name, sha256] rows of EVERY file a gate reads (finalize.gate_inputs)."""
    row = [sorted([str(n), str(h)] for n, h in inputs), data_sha256, CHECKER_VERSION, package_sha256]
    return _sha(json.dumps(row, separators=(",", ":")).encode())


# Verdicts are signed with a harness-held key (spec v4.2 item 2). The harness
# passes the key-file path ONLY to the Stop-hook process (stop-hook-log.py sets
# it for stopcheck), never to Qwen, so a finalize run from the model shell writes
# unsigned entries and a hand-written file is rejected. No key -> no cache reads.
VERDICT_KEY_ENV = "SHERLOCK_VERDICT_KEY_FILE"
MAX_KEY_BYTES = 4096


def _verdict_hmac_key():
    path = os.environ.get(VERDICT_KEY_ENV)
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_KEY_BYTES + 1)
    except OSError:
        return None
    return data if 16 <= len(data) <= MAX_KEY_BYTES else None


def _signature(key, entry):
    body = {k: v for k, v in entry.items() if k != "sig"}
    msg = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def cache_dir(workspace):
    ccindex = _sibling("ccindex")
    root = os.environ.get(ccindex.INDEX_ROOT_ENV) or os.path.join(workspace, ccindex.INDEX_DIRNAME)
    return os.path.join(root, CACHE_DIRNAME)


def cache_read(workspace, key):
    path = os.path.join(cache_dir(workspace), key + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("key") != key:
        return None
    secret = _verdict_hmac_key()
    sig = value.get("sig")
    if secret is None or not isinstance(sig, str) or not hmac.compare_digest(sig, _signature(secret, value)):
        return None  # unsigned (model shell) or forged: treated as a miss
    return value


def cache_write(workspace, key, entry):
    d = cache_dir(workspace)
    try:
        os.makedirs(d, exist_ok=True)
        entry = dict(entry, key=key, written_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        entry.pop("sig", None)
        secret = _verdict_hmac_key()
        if secret is not None:
            entry["sig"] = _signature(secret, entry)
        tmp = os.path.join(d, ".%s.%d.tmp" % (key, os.getpid()))
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, os.path.join(d, key + ".json"))
    except OSError:
        return False
    return True


def detail_path(workspace):
    """Latest per-gate detail JSON; the harness wrapper copies it per Stop."""
    return os.path.join(cache_dir(workspace), DETAIL_NAME)


def detail_write(workspace, value):
    path = detail_path(workspace)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, sort_keys=True, indent=2)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def data_sha256_for(corpus, report=None):
    """data_sha256 of a FRESH index, else None (the hook never builds one)."""
    ccindex = _sibling("ccindex")
    status, _d, man = ccindex.find_index(corpus, report)
    return man.get("data_sha256") if status == "fresh" else None


def checker_fault_request(message):
    m = FAULT_RE.search(message or "")
    return (m.group(1), m.group(2).strip()) if m else None


def reference_matches(message, report_path, workspace):
    """True if the final message is `REPORT <path> sha256=<hex> bytes=<n>` for this file."""
    text = (message or "").strip()
    m = REF_RE.match(text)
    if not m:
        return None
    rel, hexd, size = m.group(1), m.group(2), int(m.group(3))
    target = os.path.realpath(os.path.join(workspace, rel))
    if target != os.path.realpath(report_path):
        return False
    try:
        data = open(report_path, "rb").read()
    except OSError:
        return False
    return len(data) == size and _sha(data) == hexd
