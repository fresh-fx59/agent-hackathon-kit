#!/usr/bin/env python3
"""Seal a bench trace so it can be re-validated with nothing but itself.

Four defects, found by inspecting the trace of run `20260827T173511Z-v41` on the
paid host, are what this file exists for. Every one of them made a run's own
evidence directory quietly dependent on the machine that produced it:

  1. `gate-tools/` held the grader's SCRIPTS and not its DATA. `citecheck`
     resolves its enum EXTENSION table as `<tools>/../reference/enum-tables.tsv`,
     and `find . -name enum-tables.tsv` in that trace returned nothing. A replay
     therefore graded with whatever table the repo had that day — and fix 5 added
     28 Status/SubStatus rows to it, so the divergence grows with every fix.
  2. `status.json` said `FINISHED_UNCHECKED` while `gates.json` said
     `verdict=clean`. The gates ran and passed; nothing wrote the phase that
     says so, so a reader of the phase alone concludes the opposite.
  3. `upstream-inflight.json` and its `.lock` were still there — a live-run
     marker inside a dead run's evidence.
  4. `.sherlock/active.json` said `"active": true` and pointed `skill_root` at
     the LIVE `skills/v41` checkout and `corpus` at a directory outside the
     trace. A replay resolving tools through that path re-validates TODAY'S
     code, which is the one thing a replay must never do.

Subcommands:

  grader  --trace T --arm-tools DIR   copy the grader's reference DATA next to
                                      the sealed scripts, then audit it.
  inert   --trace T                   make the trace inert: no `active: true`,
                                      no path out of the trace, no inflight
                                      marker left behind.
  audit   --trace T                   the check on its own: fails if a gate
                                      reads a reference file the sealer did not
                                      copy, or if any path escapes the trace.

Every failure is fail-closed: a non-zero exit and a line on stderr. The caller
turns that into a non-zero run exit, because a trace that cannot be replayed is
not a result.
"""
import argparse
import ast
import errno
import fcntl
import hashlib
import json
import os
import shutil
import sys
import tempfile

# The grader's data lives in a sibling of its tools directory, and every gate
# resolves it that way (`os.path.join(TOOLS_DIR, "..", "reference")`). Sealing
# the tools as `gate-tools/` therefore makes `<trace>/reference` the directory
# the sealed gates will read — no env var, no patching, no argument.
REFERENCE_DIR = "reference"
GATE_TOOLS_DIR = "gate-tools"
STAGED_CORPUS = "staged-corpus"


def die(message):
    sys.stderr.write("✗ seal-trace: %s\n" % message)
    raise SystemExit(1)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 1. WHICH REFERENCE FILES DOES THE GRADER ACTUALLY READ?
#
# Derived, never listed. A hardcoded list is exactly how this defect returns:
# fixes 2-5 added three profiles to `reference/` and nobody remembered the
# sealer. So read the sealed gate SOURCE and follow `os.path.join`:
#
#   Rule A  a join whose arguments contain the literal "reference" names its
#           reference-relative path in the literals that follow it, e.g.
#           reportcheck.py:  os.path.join(base, "reference", "report-contract.corporate.json")
#   Rule B  a join whose first argument is a reference-directory variable names
#           it in the arguments that follow, resolving module-level constants:
#           citecheck.py:    reference_dir = os.path.join(TOOLS_DIR, "..", "reference")
#                            os.path.join(reference_dir, ENUM_TABLE_FILE)
#
# A profile added by a later fix travels automatically as long as the gate reads
# it the way every gate already reads one. If it is read some other way, the
# audit does not see it — so the audit also fails when the sealed reference
# directory is missing or empty, which is the failure mode that actually bit.
# ---------------------------------------------------------------------------
def _is_join(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join")


def _constants(tree):
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def _looks_like_reference_dir(node, tainted):
    if isinstance(node, ast.Name):
        return node.id in tainted or "reference" in node.id
    if _is_join(node):
        return any(isinstance(a, ast.Constant) and a.value == REFERENCE_DIR
                   for a in node.args)
    return False


def _tainted_names(tree):
    """Variables that hold a path to a reference directory."""
    names = set()
    for _ in range(3):                        # tiny fixpoint: assignment chains
        before = set(names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _looks_like_reference_dir(node.value, names):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        if names == before:
            break
    return names


def _resolve(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def reference_reads(source):
    """-> (set of reference-relative paths a gate reads, set of unresolved names)"""
    tree = ast.parse(source)
    constants = _constants(tree)
    tainted = _tainted_names(tree)
    wanted, unresolved = set(), set()
    for node in ast.walk(tree):
        if not _is_join(node):
            continue
        args = list(node.args)
        tail = None
        for index, arg in enumerate(args):
            if isinstance(arg, ast.Constant) and arg.value == REFERENCE_DIR:
                tail = args[index + 1:]                          # Rule A
                break
        if tail is None and args and _looks_like_reference_dir(args[0], tainted):
            tail = args[1:]                                       # Rule B
        if not tail:
            continue
        parts = []
        for arg in tail:
            value = _resolve(arg, constants)
            if value is None:
                unresolved.add(ast.dump(arg)[:80])
                parts = None
                break
            parts.append(value)
        if parts:
            path = os.path.normpath(os.path.join(*parts))
            if not path.startswith("..") and path not in (".", ""):
                wanted.add(path)
    return wanted, unresolved


def gate_sources(tools):
    for name in sorted(os.listdir(tools)):
        if name.endswith(".py"):
            path = os.path.join(tools, name)
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as handle:
                        yield name, handle.read()
                except (OSError, UnicodeDecodeError) as exc:
                    die("cannot read sealed gate %s: %s" % (name, exc))


def derive_required(tools):
    required = {}
    for name, source in gate_sources(tools):
        try:
            wanted, _ = reference_reads(source)
        except SyntaxError as exc:
            die("sealed gate %s does not parse: %s" % (name, exc))
        for path in wanted:
            required.setdefault(path, set()).add(name)
    return required


# ---------------------------------------------------------------------------
# grader: copy the DATA, then prove the copy is complete.
# ---------------------------------------------------------------------------
def copy_reference(source, target):
    if not os.path.isdir(source):
        die("grader reference directory not found: %s" % source)
    if os.path.exists(target):
        shutil.rmtree(target)
    try:
        shutil.copytree(source, target, symlinks=False,
                        ignore=shutil.ignore_patterns("__pycache__"))
    except (OSError, shutil.Error) as exc:
        die("could not copy the grader reference data: %s" % exc)
    copied = 0
    for root, _dirs, files in os.walk(source):
        for name in files:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, source)
            dst = os.path.join(target, rel)
            if not os.path.isfile(dst):
                die("reference file was not copied: %s" % rel)
            if sha256(src) != sha256(dst):
                die("reference file copied wrong: %s" % rel)
            copied += 1
    if not copied:
        die("grader reference directory is empty: %s" % source)
    return copied


def cmd_grader(args):
    trace = os.path.abspath(args.trace)
    tools = os.path.abspath(args.arm_tools)
    if not os.path.isdir(tools):
        die("arm tools directory not found: %s" % tools)
    source = os.path.join(os.path.dirname(tools), REFERENCE_DIR)
    # An arm old enough to have no reference data at all (v5-v9) reads none, and
    # the derivation says so. Nothing to seal is not a failure; a gate that DOES
    # read reference data and finds none is, and that is the next branch.
    if not os.path.isdir(source) and not derive_required(tools):
        print("  this arm's gates read no reference data — nothing to seal")
        return 0
    copied = copy_reference(source, os.path.join(trace, REFERENCE_DIR))
    print("  sealed %d grader reference file(s) into %s/" % (copied, REFERENCE_DIR))
    return 0


# ---------------------------------------------------------------------------
# 3. THE INFLIGHT MARKER — and how a sealed run is told from a live one.
#
# `inert` runs from `save_trace`, i.e. after the lane proxy has been killed and
# reaped and after `upstream-completed.jsonl` has been written: by then the run
# is over by construction. A run that DIES without sealing never reaches this
# code and keeps its marker, which is what crash recovery reads. That is the
# structural half of the distinction; the mechanical half is here, so the rule
# does not depend on the caller being right:
#
#   * the lock must be free  — a live proxy holds `upstream-inflight.json.lock`
#     under flock, so a lock we cannot take non-blockingly means a live writer;
#   * no listed request's pid may still exist.
#
# If either says "live", the marker stays and sealing fails loudly. We never
# delete the evidence of a run that is still running.
# ---------------------------------------------------------------------------
def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def drop_inflight(trace):
    marker = os.path.join(trace, "upstream-inflight.json")
    lock = marker + ".lock"
    if not os.path.exists(marker) and not os.path.exists(lock):
        return "absent"
    if os.path.exists(lock):
        try:
            fd = os.open(lock, os.O_RDWR)
        except OSError as exc:
            die("cannot inspect the inflight lock: %s" % exc)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                die("inflight lock is held — this run is still live, refusing to seal")
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    if os.path.exists(marker):
        try:
            with open(marker, encoding="utf-8") as handle:
                row = json.load(handle)
        except (OSError, ValueError):
            row = {}
        requests = row.get("requests") if isinstance(row, dict) else None
        if isinstance(requests, dict):
            for item in requests.values():
                if isinstance(item, dict) and _pid_alive(item.get("pid")):
                    die("inflight request pid %r is alive — refusing to seal"
                        % item.get("pid"))
    for path in (marker, lock):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            die("could not remove the inflight marker %s: %s" % (path, exc))
    return "removed"


# ---------------------------------------------------------------------------
# 4. INERTNESS. `active: true` in a sealed trace is a lie, and a path out of the
# trace is a door back into the working tree. Both are rewritten to point at the
# trace's own copies: within a trace, `gate-tools/` is the skill's tools
# directory and `reference/` its reference directory, so the trace itself IS the
# skill root the sealed gates resolve against.
# ---------------------------------------------------------------------------
def write_json(path, row):
    directory = os.path.dirname(os.path.abspath(path))
    fd, temporary = tempfile.mkstemp(prefix=".seal.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(row, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def make_marker_inert(trace):
    marker = os.path.join(trace, ".sherlock", "active.json")
    if not os.path.isfile(marker):
        return "absent"
    try:
        with open(marker, encoding="utf-8") as handle:
            row = json.load(handle)
    except (OSError, ValueError) as exc:
        die("sealed active marker is unreadable: %s" % exc)
    if not isinstance(row, dict):
        die("sealed active marker is not an object")
    row["active"] = False
    row["sealed"] = True
    row["workspace"] = trace
    row["out"] = os.path.join(trace, "work")
    row["skill_root"] = trace
    row["corpus"] = os.path.join(trace, STAGED_CORPUS)
    write_json(marker, row)
    return "inert"


def cmd_inert(args):
    trace = os.path.abspath(args.trace)
    if not os.path.isdir(trace):
        die("trace directory not found: %s" % trace)
    marker = make_marker_inert(trace)
    inflight = drop_inflight(trace)
    print("  trace sealed: active-marker=%s inflight=%s" % (marker, inflight))
    return 0


# ---------------------------------------------------------------------------
# THE AUDIT. Returns a list of human-readable problems; empty means the trace
# can be replayed with the working tree deleted.
# ---------------------------------------------------------------------------
def audit(trace):
    trace = os.path.abspath(trace)
    problems = []
    tools = os.path.join(trace, GATE_TOOLS_DIR)
    graded = os.path.isfile(os.path.join(trace, "gates.json"))
    if not os.path.isdir(tools):
        if graded:
            problems.append("gates.json exists but %s/ was not sealed" % GATE_TOOLS_DIR)
        return problems
    sealed = os.path.join(trace, REFERENCE_DIR)
    required = derive_required(tools)
    if required and not os.path.isdir(sealed):
        problems.append("%s/ was not sealed; the sealed gates would read the "
                        "live checkout" % REFERENCE_DIR)
    for path, readers in sorted(required.items()):
        if not os.path.isfile(os.path.join(sealed, path)):
            problems.append("gate(s) %s read %s/%s, which the sealer did not copy"
                            % (",".join(sorted(readers)), REFERENCE_DIR, path))
    if graded and not os.path.isdir(os.path.join(trace, STAGED_CORPUS)):
        problems.append("gates.json exists but %s/ was not sealed" % STAGED_CORPUS)
    for name in ("upstream-inflight.json", "upstream-inflight.json.lock"):
        if os.path.exists(os.path.join(trace, name)):
            problems.append("live-run artefact left in a sealed trace: %s" % name)
    marker = os.path.join(trace, ".sherlock", "active.json")
    if os.path.isfile(marker):
        try:
            with open(marker, encoding="utf-8") as handle:
                row = json.load(handle)
        except (OSError, ValueError):
            row = None
        if not isinstance(row, dict):
            problems.append(".sherlock/active.json is unreadable")
        else:
            if row.get("active") is not False:
                problems.append(".sherlock/active.json says active=%r in a sealed "
                                "trace" % row.get("active"))
            for key in ("corpus", "out", "skill_root", "workspace"):
                value = row.get(key)
                if not isinstance(value, str) or not value:
                    continue
                resolved = os.path.realpath(value)
                if os.path.commonpath([resolved, os.path.realpath(trace)]) \
                        != os.path.realpath(trace):
                    problems.append(".sherlock/active.json %s escapes the trace: %s"
                                    % (key, value))
    return problems


def cmd_audit(args):
    problems = audit(args.trace)
    for line in problems:
        sys.stderr.write("✗ seal-trace: %s\n" % line)
    if problems:
        raise SystemExit(1)
    print("  trace self-containment audit: clean")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("grader")
    one.add_argument("--trace", required=True)
    one.add_argument("--arm-tools", required=True)
    one.set_defaults(handler=cmd_grader)
    two = sub.add_parser("inert")
    two.add_argument("--trace", required=True)
    two.set_defaults(handler=cmd_inert)
    three = sub.add_parser("audit")
    three.add_argument("--trace", required=True)
    three.set_defaults(handler=cmd_audit)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
