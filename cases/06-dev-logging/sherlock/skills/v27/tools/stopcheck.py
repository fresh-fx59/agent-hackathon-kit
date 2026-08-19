#!/usr/bin/env python3
"""Qwen Stop gate for Sherlock v27.

It is an optional seatbelt: the prose state machine remains the real contract, and
if there is no valid active marker for this workspace the hook allows Stop.
"""
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time

MARKER_DIR = ".sherlock"
MARKER_FILE = "active.json"
TOTAL_TIMEOUT = 50
CHILD_TIMEOUT = 24
MAX_REASON = 220
MAX_MANIFEST_WORKLISTS = 512


class ActiveStateError(Exception):
    pass


class UntrustedState(Exception):
    pass


class DeadlineExceeded(Exception):
    pass


HOST_RECORD_NAME = "sherlock-host"
HOST_RECORD_TAG = "stopcheck-v27"


def deadline_reason():
    return ("Sherlock: stopcheck deadline elapsed; rerun triagecheck and citecheck "
            "manually, then deliver work/report.md only after they pass.")


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise DeadlineExceeded(deadline_reason())


def _deadline_handler(_signum, _frame):
    raise DeadlineExceeded(deadline_reason())


def arm_watchdog(seconds):
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        return None
    old_handler, old_timer = None, None
    try:
        old_handler = signal.getsignal(signal.SIGALRM)
        old_timer = signal.getitimer(signal.ITIMER_REAL)
        start = time.monotonic()
        signal.signal(signal.SIGALRM, _deadline_handler)
        signal.setitimer(signal.ITIMER_REAL, max(0.001, float(seconds)))
        return old_handler, old_timer, start
    except (OSError, TypeError, ValueError):
        if old_handler is not None:
            try:
                signal.signal(signal.SIGALRM, old_handler)
            except (OSError, TypeError, ValueError):
                pass
        if old_timer is not None:
            try:
                signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])
            except (OSError, TypeError, ValueError):
                pass
        return None


def disarm_watchdog(old):
    if old is None or not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        return
    old_handler, old_timer, start = old
    elapsed = max(0.0, time.monotonic() - start)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
    except (OSError, TypeError, ValueError):
        pass
    try:
        signal.signal(signal.SIGALRM, old_handler)
    except (OSError, TypeError, ValueError):
        pass
    old_delay, old_interval = old_timer
    if old_delay <= 0:
        return
    if elapsed < old_delay:
        remaining = old_delay - elapsed
    elif old_interval > 0:
        phase = (elapsed - old_delay) % old_interval
        remaining = old_interval if phase <= 0.000001 else old_interval - phase
    else:
        return
    try:
        signal.setitimer(signal.ITIMER_REAL, max(0.001, remaining), old_interval)
    except (OSError, TypeError, ValueError):
        pass


def emit(decision, reason):
    payload = {"decision": decision, "reason": reason}
    if decision == "block":
        payload["stopReason"] = reason
    else:
        payload["suppressOutput"] = True
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


def allow(reason="Sherlock inactive"):
    return emit("allow", reason)


def block(reason):
    return emit("block", reason[:MAX_REASON])


def real(path):
    return os.path.realpath(os.path.abspath(path))


def inside(path, root):
    try:
        return os.path.commonpath([real(path), real(root)]) == real(root)
    except (TypeError, ValueError):
        return False


def _lstat(path):
    try:
        return os.lstat(path)
    except OSError:
        return None


def _is_link(path):
    st = _lstat(path)
    return bool(st and stat.S_ISLNK(st.st_mode))


def _has_symlink_ancestry(path, root):
    """True if any existing component from root to path is a symlink."""
    root_abs = os.path.abspath(root)
    path_abs = os.path.abspath(path)
    try:
        rel = os.path.relpath(path_abs, root_abs)
    except ValueError:
        return True
    if rel == ".":
        return _is_link(root_abs)
    cur = root_abs
    for part in rel.split(os.sep):
        if not part or part == ".":
            continue
        cur = os.path.join(cur, part)
        st = _lstat(cur)
        if st is None:
            return False
        if stat.S_ISLNK(st.st_mode):
            return True
    return False


def safe_dir(path, root=None):
    if not isinstance(path, str) or not path:
        return None
    path_abs = os.path.abspath(path)
    ap = real(path)
    if root is not None:
        if (not inside(ap, root)
                or _has_symlink_ancestry(path_abs, root)):
            return None
    elif _is_link(path):
        return None
    if not os.path.isdir(ap):
        return None
    return ap


def safe_file(path, root):
    if not isinstance(path, str) or not path:
        return None
    path_abs = os.path.abspath(path)
    ap = real(path)
    if (not inside(ap, root)
            or _has_symlink_ancestry(path_abs, root)):
        return None
    st = _lstat(path)
    if st is None or stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    return ap


def clean_relpath(value):
    if not isinstance(value, str):
        return None
    raw = value.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re_drive(raw):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def re_drive(path):
    return len(path) >= 3 and path[1] == ":" and path[2] == "/" and path[0].isalpha()


def safe_rel_file(root, relpath):
    rel = clean_relpath(relpath)
    if rel is None:
        return None
    return safe_file(os.path.join(root, rel.replace("/", os.sep)), root)


def _host_record_parts(line):
    if not line.startswith("#"):
        return None
    cols = line.rstrip("\n").split("\t")
    return cols if len(cols) == 2 and cols[0] == "# sherlock-host" else None


def is_reserved_host_record(line):
    return _host_record_parts(line) is not None


def validate_host_selector(host):
    if host is None:
        return None
    if (not isinstance(host, str) or not host.strip()
            or any(ch in host for ch in "\r\n\t")):
        raise ActiveStateError("Sherlock: trusted host selector is unsafe; rerun logmap MAP step.")
    return host.strip()


def generated_host_record(host):
    return "# %s\t%s\n" % (HOST_RECORD_NAME, host)


def read_hook_input():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def marker_path(workspace):
    return os.path.join(workspace, MARKER_DIR, MARKER_FILE)


def validate_active_marker(data, workspace):
    if data.get("active") is not True:
        raise ActiveStateError("Sherlock: active marker has invalid active flag; rerun logmap MAP step.")
    version = data.get("version")
    if type(version) is not int:
        raise ActiveStateError("Sherlock: active marker has invalid version; rerun logmap MAP step.")
    if version != 27:
        return False
    workspace_value = data.get("workspace")
    if not isinstance(workspace_value, str) or not workspace_value:
        raise ActiveStateError("Sherlock: active marker is incomplete; rerun logmap MAP step.")
    if real(workspace_value) != workspace:
        return False
    for key in ("corpus", "out", "skill_root", "mode", "worklists"):
        if key not in data:
            raise ActiveStateError("Sherlock: active marker misses %s; rerun logmap MAP step." % key)
    for key in ("corpus", "out", "skill_root"):
        if not isinstance(data.get(key), str) or not data.get(key):
            raise ActiveStateError("Sherlock: active marker has invalid %s; rerun logmap MAP step." % key)
    if data.get("mode") not in ("single", "multi"):
        raise ActiveStateError("Sherlock: active marker has invalid mode; rerun logmap MAP step.")
    raw_worklists = data.get("worklists")
    if not isinstance(raw_worklists, list) or not raw_worklists:
        raise ActiveStateError("Sherlock: active marker has invalid worklist manifest; rerun logmap MAP step.")
    for item in raw_worklists:
        if clean_relpath(item) is None:
            raise ActiveStateError("Sherlock: active marker has unsafe worklist path; rerun logmap MAP step.")
    if data.get("mode") == "multi":
        if not isinstance(data.get("hosts_manifest"), str):
            raise ActiveStateError("Sherlock: active marker misses hosts.tsv manifest; rerun logmap MAP step.")
        hosts = data.get("hosts")
        if not isinstance(hosts, list) or not hosts:
            raise ActiveStateError("Sherlock: active marker host list is inconsistent; rerun logmap MAP step.")
        for h in hosts:
            if not isinstance(h, dict):
                raise ActiveStateError("Sherlock: active marker host list is inconsistent; rerun logmap MAP step.")
            if not isinstance(h.get("name"), str) or not h.get("name"):
                raise ActiveStateError("Sherlock: active marker host list is inconsistent; rerun logmap MAP step.")
            if clean_relpath(h.get("worklist")) is None:
                raise ActiveStateError("Sherlock: active marker host list is inconsistent; rerun logmap MAP step.")
            if "map" in h and h.get("map") not in (None, "") and clean_relpath(h.get("map")) is None:
                raise ActiveStateError("Sherlock: active marker host list is inconsistent; rerun logmap MAP step.")
    elif "hosts" in data and data.get("hosts") not in (None, []):
        raise ActiveStateError("Sherlock: active marker has host data in single-host mode; rerun logmap MAP step.")
    elif "hosts_manifest" in data and data.get("hosts_manifest") not in (None, ""):
        raise ActiveStateError("Sherlock: active marker has hosts.tsv in single-host mode; rerun logmap MAP step.")
    return True


def load_marker(workspace, deadline=None):
    if deadline is not None:
        check_deadline(deadline)
    marker_dir = os.path.join(workspace, MARKER_DIR)
    marker = os.path.join(marker_dir, MARKER_FILE)
    st = _lstat(marker_dir)
    if st is None:
        return None, marker, None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise UntrustedState("unsafe marker directory")
    st = _lstat(marker)
    if st is None:
        return None, marker, None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise UntrustedState("unsafe marker file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(marker, flags)
        with os.fdopen(fd, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None, marker, None
    if deadline is not None:
        check_deadline(deadline)
    if not isinstance(data, dict):
        return None, marker, None
    active = data.get("active")
    if active is None or active is False:
        return None, marker, None
    if not validate_active_marker(data, workspace):
        return None, marker, None
    if deadline is not None:
        check_deadline(deadline)
    return data, marker, None


def skill_root():
    here = real(os.path.dirname(os.path.dirname(__file__)))
    root = real(os.environ.get("QWEN_SKILL_ROOT") or here)
    return root if root == here else here


def tool_path(root, name):
    tools = safe_dir(os.path.join(root, "tools"), root)
    if not tools:
        return None
    path = os.path.join(tools, name)
    if not inside(path, tools):
        return None
    st = _lstat(path)
    if st is None or stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    return real(path)


def rel(path, workspace):
    try:
        return os.path.relpath(path, workspace)
    except ValueError:
        return path


def run_child(argv, deadline):
    remaining = deadline - time.monotonic() - 0.5
    if remaining <= 0:
        class TimedOut:
            returncode = 124
            stdout = ""
            stderr = "timeout"
        return TimedOut()
    timeout = max(1.0, min(float(CHILD_TIMEOUT), remaining))
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        class TimedOut:
            returncode = 124
            stdout = ""
            stderr = "timeout"
        return TimedOut()
    except OSError as e:
        class Failed:
            returncode = 127
            stdout = ""
            stderr = str(e)
        return Failed()


def unresolved_rows(worklist, deadline=None):
    unresolved = []
    try:
        with open(worklist, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if deadline is not None:
                    check_deadline(deadline)
                if not line.strip() or line.startswith("#"):
                    continue
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 2 and cols[1].strip() == "?":
                    unresolved.append(cols[0])
    except OSError:
        return ["<cannot-read>"]
    return unresolved


def worklist_host_token(relpath):
    name = os.path.basename(relpath)
    if name.startswith("worklist-") and name.endswith(".tsv"):
        return name[len("worklist-"):-len(".tsv")]
    return None


def parse_hosts_manifest(path, out_dir, deadline=None):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if deadline is not None:
                    check_deadline(deadline)
                if not line.strip() or line.startswith("#"):
                    continue
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 7:
                    raise ActiveStateError("Sherlock: hosts.tsv is malformed; rerun logmap MAP step.")
                worklist_rel = clean_relpath(cols[5])
                map_rel = clean_relpath(cols[6]) if cols[6].strip() else ""
                if not worklist_rel or not safe_rel_file(out_dir, worklist_rel):
                    raise ActiveStateError("Sherlock: hosts.tsv names an unsafe worklist; rerun logmap MAP step.")
                if map_rel and not safe_rel_file(out_dir, map_rel):
                    raise ActiveStateError("Sherlock: hosts.tsv names an unsafe map; rerun logmap MAP step.")
                rows.append({"name": cols[0], "worklist": worklist_rel,
                             "host": worklist_host_token(worklist_rel), "map": map_rel})
    except OSError:
        raise ActiveStateError("Sherlock: hosts.tsv cannot be read; rerun logmap MAP step.")
    if not rows:
        raise ActiveStateError("Sherlock: hosts.tsv is empty; rerun logmap MAP step.")
    return rows


def manifest_worklists(marker, out_dir, deadline=None):
    if deadline is not None:
        check_deadline(deadline)
    mode = marker.get("mode")
    raw = marker.get("worklists")
    if mode not in ("single", "multi"):
        raise ActiveStateError("Sherlock: active marker has invalid mode; rerun logmap MAP step.")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_MANIFEST_WORKLISTS:
        raise ActiveStateError("Sherlock: active marker has invalid worklist manifest; rerun logmap MAP step.")
    rels = []
    seen = set()
    for item in raw:
        if deadline is not None:
            check_deadline(deadline)
        cleaned = clean_relpath(item)
        if cleaned is None or cleaned in seen:
            raise ActiveStateError("Sherlock: active marker has unsafe worklist path; rerun logmap MAP step.")
        seen.add(cleaned)
        rels.append(cleaned)
    if mode == "single" and rels != ["worklist.tsv"]:
        raise ActiveStateError("Sherlock: active marker says single-host but names another worklist; rerun logmap MAP step.")
    if mode == "multi":
        hosts_rel = clean_relpath(marker.get("hosts_manifest"))
        if hosts_rel != "hosts.tsv":
            raise ActiveStateError("Sherlock: active marker misses hosts.tsv manifest; rerun logmap MAP step.")
        hosts_file = safe_rel_file(out_dir, hosts_rel)
        if not hosts_file:
            raise ActiveStateError("Sherlock: hosts.tsv is missing or unsafe; rerun logmap MAP step.")
        hosts = parse_hosts_manifest(hosts_file, out_dir, deadline)
        host_rels = sorted(row["worklist"] for row in hosts)
        if host_rels != sorted(rels):
            raise ActiveStateError("Sherlock: hosts.tsv and active marker disagree; rerun logmap MAP step.")
        marker_hosts = marker.get("hosts")
        if isinstance(marker_hosts, list) and marker_hosts:
            marker_rels = []
            for h in marker_hosts:
                if deadline is not None:
                    check_deadline(deadline)
                cleaned = clean_relpath((h or {}).get("worklist"))
                if cleaned is None:
                    raise ActiveStateError("Sherlock: active marker host list is inconsistent; rerun logmap MAP step.")
                marker_rels.append(cleaned)
            if sorted(marker_rels) != host_rels:
                raise ActiveStateError("Sherlock: active marker host list is inconsistent; rerun logmap MAP step.")
    hosts_by_worklist = {}
    if mode == "multi":
        hosts_by_worklist = dict((row["worklist"], row.get("host")) for row in hosts)
    paths = []
    for r in rels:
        if deadline is not None:
            check_deadline(deadline)
        p = safe_rel_file(out_dir, r)
        if not p:
            raise ActiveStateError("Sherlock: worklist %s is missing or unsafe; rerun logmap MAP step." % r)
        paths.append({"path": p, "rel": r, "host": hosts_by_worklist.get(r)})
    return paths


def worklist_path(item):
    return item.get("path") if isinstance(item, dict) else item


def worklist_host(item):
    if isinstance(item, dict):
        return item.get("host")
    return worklist_host_token(item)


def compose_worklists(items, out_dir, deadline=None):
    if deadline is not None:
        check_deadline(deadline)
    fd, tmp = tempfile.mkstemp(prefix=".stopcheck-ledger-", suffix=".tsv", dir=out_dir, text=True)
    try:
        seen = {}
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            for item in items:
                if deadline is not None:
                    check_deadline(deadline)
                path = worklist_path(item)
                host = validate_host_selector(worklist_host(item))
                if host:
                    out.write(generated_host_record(host))
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            if deadline is not None:
                                check_deadline(deadline)
                            if is_reserved_host_record(line):
                                raise ActiveStateError("Sherlock: worklist contains reserved host control record; remove it and rerun checks.")
                            if line.strip() and not line.startswith("#"):
                                rid = line.rstrip("\n").split("\t", 1)[0].strip()
                                if rid:
                                    if rid in seen:
                                        raise ActiveStateError("Sherlock: duplicate worklist id %s in %s and %s; rerun logmap MAP step."
                                                               % (rid, rel(path, out_dir), rel(seen[rid], out_dir)))
                                    seen[rid] = path
                            out.write(line)
                except OSError:
                    raise ActiveStateError("Sherlock: worklist cannot be read; rerun logmap MAP step.")
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return real(tmp), tmp


def check_children(corpus, out_dir, report, lists, root, workspace, deadline):
    check_deadline(deadline)
    triage = tool_path(root, "triagecheck.py")
    cite = tool_path(root, "citecheck.py")
    if not triage or not cite:
        return "Sherlock: tools missing or unsafe; reinstall the v27 skill or run checks manually."
    rules_path = os.path.join(out_dir, "rules.tsv")
    if os.path.lexists(rules_path):
        rules = safe_file(rules_path, out_dir)
        if not rules:
            return "Sherlock: work/rules.tsv is unsafe; replace it with a regular file before stopping."
    else:
        rules = rules_path
    ledger, tmp = None, None
    try:
        ledger, tmp = compose_worklists(lists, out_dir, deadline)
        check_deadline(deadline)
        r = run_child([sys.executable, triage, "--worklist", ledger, "--rules", rules,
                       "--corpus", corpus], deadline)
        if r.returncode != 0:
            return ("Sherlock: triagecheck failed for %s; fix worklist/rules.tsv, rerun triagecheck, then deliver work/report.md."
                    % rel(ledger, workspace))
        check_deadline(deadline)
        r = run_child([sys.executable, cite, report, "--corpus", corpus,
                       "--require-quote", "--ledger", ledger], deadline)
        if r.returncode != 0:
            return ("Sherlock: citecheck failed for %s; fix citations/ledger, rerun citecheck, then deliver work/report.md."
                    % rel(ledger, workspace))
        check_deadline(deadline)
        return None
    except DeadlineExceeded:
        raise
    except ActiveStateError as e:
        return str(e)
    except Exception as e:
        return "Sherlock: stopcheck could not verify active investigation (%s); rerun checks manually before stopping." % type(e).__name__
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def retire(path, workspace, deadline=None):
    if deadline is not None:
        check_deadline(deadline)
    try:
        if inside(path, workspace) and not _has_symlink_ancestry(path, workspace):
            st = _lstat(path)
            if st and stat.S_ISREG(st.st_mode):
                os.remove(path)
    except OSError:
        pass
    if deadline is not None:
        check_deadline(deadline)


def _allow(reason="Sherlock inactive", retire_path=None):
    return "allow", reason, retire_path


def _block(reason):
    return "block", reason[:MAX_REASON], None


def evaluate_stop(event, workspace, deadline):
    check_deadline(deadline)
    try:
        marker, mpath, _why = load_marker(workspace, deadline)
    except UntrustedState:
        return _allow("Sherlock marker ignored: unsafe marker path")
    except ActiveStateError as e:
        return _block(str(e))
    if not marker:
        return _allow()

    try:
        check_deadline(deadline)
        out_dir = safe_dir(marker.get("out") or "", workspace)
        if not out_dir:
            raise ActiveStateError("Sherlock: active marker points outside the workspace; rerun logmap MAP step.")
        check_deadline(deadline)
        corpus = safe_dir(marker.get("corpus") or "")
        if not corpus:
            raise ActiveStateError("Sherlock: corpus is unavailable or unsafe; rerun logmap or restore the log directory before stopping.")
        check_deadline(deadline)
        expected_root = skill_root()
        if real(marker.get("skill_root") or "") != expected_root:
            raise ActiveStateError("Sherlock: active marker belongs to another skill copy; rerun logmap with this v27 skill.")
        lists = manifest_worklists(marker, out_dir, deadline)
    except ActiveStateError as e:
        return _block(str(e))

    try:
        for wl in lists:
            check_deadline(deadline)
            left = unresolved_rows(worklist_path(wl), deadline)
            if left:
                sample = ", ".join(left[:5])
                return _block("Sherlock: unresolved worklist rows remain (%s); finish TRIAGE and update the worklist." % sample)

        check_deadline(deadline)
        report = safe_file(os.path.join(out_dir, "report.md"), out_dir)
        if not report:
            return _block("Sherlock: work/report.md is missing or unsafe; complete DRAFT before stopping.")

        reason = check_children(corpus, out_dir, report, lists, expected_root, workspace, deadline)
        if reason:
            return _block(reason)

        check_deadline(deadline)
        try:
            with open(report, encoding="utf-8", errors="replace") as fh:
                expected = fh.read().strip()
        except OSError:
            return _block("Sherlock: work/report.md cannot be read; fix DRAFT before stopping.")
        check_deadline(deadline)
        got = (event.get("last_assistant_message") or "").strip()
        if got != expected:
            return _block("Sherlock: final message must exactly equal work/report.md; deliver that file verbatim.")

        return _allow("Sherlock complete", mpath)
    except DeadlineExceeded:
        raise
    except ActiveStateError as e:
        return _block(str(e))
    except Exception as e:
        return _block("Sherlock: stopcheck could not verify active investigation (%s); rerun checks manually before stopping." % type(e).__name__)


def cannot_determine_workspace_reason():
    return "Sherlock: cannot determine workspace; rerun checks manually before stopping."


def resolve_workspace(event):
    raw_cwd = event.get("cwd")
    candidates = []
    if (isinstance(raw_cwd, str) and raw_cwd.strip()
            and not any(ch in raw_cwd for ch in "\x00\r\n")):
        candidates.append(raw_cwd)
    try:
        candidates.append(os.getcwd())
    except (OSError, TypeError, ValueError):
        pass
    for candidate in candidates:
        try:
            return real(candidate)
        except (OSError, TypeError, ValueError):
            pass
    return None


def main():
    event = read_hook_input()
    workspace = resolve_workspace(event)
    if workspace is None:
        return block(cannot_determine_workspace_reason())
    deadline = time.monotonic() + TOTAL_TIMEOUT
    watchdog = arm_watchdog(deadline - time.monotonic())
    retire_path = None
    try:
        try:
            decision, reason, retire_path = evaluate_stop(event, workspace, deadline)
        except DeadlineExceeded:
            decision, reason, retire_path = _block(deadline_reason())
    finally:
        disarm_watchdog(watchdog)
    if decision == "allow" and retire_path:
        retire(retire_path, workspace)
    return block(reason) if decision == "block" else allow(reason)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DeadlineExceeded:
        sys.exit(block(deadline_reason()))
    except Exception:
        sys.exit(allow("Sherlock stopcheck failed open"))
