#!/usr/bin/env python3
"""The arm install path in run-bench.sh must be idempotent, and must never
touch an ARM_HOME it does not own.

Fix-round-1 regression: `chmod -R a-w "$ARM_HOME"` ran unconditionally at the
end of every install, which made the NEXT run's `rm -rf "$ARM_HOME"` fail
(removing an entry needs write on the directory it lives in, and the previous
run had just cleared that). `test_bench_v30_resume.py` caught it directly:
`rm: .../tools/statecheck.py: Permission denied`. On contabo this is worse
than a failing test — ARM_HOME there is root:root 0555 and run-bench runs as
claude-developer, so `rm -rf` can NEVER succeed; the install path must detect
that it cannot reclaim write and use the existing copy as-is (after verifying
it matches the shipped arm), never attempt to remove or overwrite it, and
never run silently against a stale copy.

This test extracts the actual install block out of run-bench.sh (between the
`ARM_HOME=` assignment and the `export QWEN_SKILL_ROOT="$ARM_HOME"` line) and
drives it directly in a temp sandbox, so it exercises the real code, not a
reimplementation of it.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
BENCH = os.path.join(SHERLOCK, "eval", "bench", "run-bench.sh")
FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)


def extract_install_block():
    src = open(BENCH, encoding="utf-8").read()
    # v43 moved ARM_HOME's resolution UP, beside the settings write that has to
    # name the arm's directory. Slice the INSTALL block alone — starting at the
    # resolution would now drag the whole settings section in with it, and this
    # test would be exercising code it says nothing about.
    start_marker = '  # ARM_HOME was resolved and range-checked above'
    end_marker = 'export QWEN_SKILL_ROOT="$ARM_HOME"'
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


def run_block(env_extra, skills, arm, arm_home, w="/nonexistent-W"):
    # The install block no longer resolves ARM_HOME itself, so the harness
    # supplies it the same way the real script does — from SHERLOCK_ARM_HOME,
    # through the one resolution line the script now runs earlier.
    block = extract_install_block()
    script = "set -uo pipefail\nset -e\n"
    script += 'SKILLS=%r\nARM=%r\nW=%r\n' % (skills, arm, w)
    script += 'ARM_HOME="${SHERLOCK_ARM_HOME:-$HOME/.qwen/skills/log-rca}"\n' 
    script += block
    script += '\necho "RESULT_ARM_HOME=$ARM_HOME"\n'
    env = {**os.environ, **env_extra, "SHERLOCK_ARM_HOME": arm_home}
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, env=env,
    )
    return proc


def make_shipped_arm(root, content="original"):
    arm_dir = os.path.join(root, "skills", "v43")
    os.makedirs(os.path.join(arm_dir, "tools"), exist_ok=True)
    # A real arm always carries SKILL.md, and run-bench now reads the arm's own
    # skill name out of it (to keep that name out of the mute list). A fixture
    # without it is not an arm.
    with open(os.path.join(arm_dir, "SKILL.md"), "w") as fh:
        fh.write("---\nname: sherlock\ndescription: fixture\n---\n")
    with open(os.path.join(arm_dir, "brief.py"), "w") as fh:
        fh.write(content)
    with open(os.path.join(arm_dir, "tools", "statecheck.py"), "w") as fh:
        fh.write("tool:" + content)
    return arm_dir


# --- Test 1: fresh install, then a SECOND install run — must succeed both times ---
tmp1 = tempfile.mkdtemp(prefix="arm-idem-1-")
try:
    skills_root = tmp1
    make_shipped_arm(skills_root)
    skills_root = os.path.join(tmp1, "skills")
    arm_home = os.path.join(tmp1, "home", "log-rca")

    proc1 = run_block({}, skills_root, "v43", arm_home)
    check(proc1.returncode == 0,
          "first install failed: rc=%s stderr=%s" % (proc1.returncode, proc1.stderr))
    check(os.path.isfile(os.path.join(arm_home, "brief.py")),
          "first install did not populate ARM_HOME")

    proc2 = run_block({}, skills_root, "v43", arm_home)
    check(proc2.returncode == 0,
          "SECOND install (the regression) failed: rc=%s stderr=%s"
          % (proc2.returncode, proc2.stderr))
    check(os.path.isfile(os.path.join(arm_home, "brief.py")),
          "second install left ARM_HOME without content")
finally:
    try:
        os.system('chmod -R u+w "%s" 2>/dev/null' % tmp1)
    except Exception:
        pass
    shutil.rmtree(tmp1, ignore_errors=True)


# --- Test 2 & 3: ARM_HOME we cannot reclaim write on (simulated via a macOS
#     immutable flag — the only way to make even the OWNER's chmod ineffective
#     without a second UID). Skipped where the platform has no chflags.
def chflags_available():
    return shutil.which("chflags") is not None


if chflags_available():
    # Test 2: not-owned, content MATCHES the shipped arm -> accepted, used as-is.
    tmp2 = tempfile.mkdtemp(prefix="arm-idem-2-")
    try:
        arm_dir = make_shipped_arm(tmp2, content="match-me")
        skills_root = os.path.join(tmp2, "skills")
        arm_home = os.path.join(tmp2, "home", "log-rca")
        os.makedirs(os.path.dirname(arm_home), exist_ok=True)
        shutil.copytree(arm_dir, arm_home)
        subprocess.run(["chflags", "-R", "uchg", arm_home], check=True)

        proc = run_block({}, skills_root, "v43", arm_home)
        check(proc.returncode == 0,
              "not-owned + matching content should be accepted, got rc=%s stderr=%s"
              % (proc.returncode, proc.stderr))
    finally:
        subprocess.run(["chflags", "-R", "nouchg", os.path.join(tmp2, "home", "log-rca")],
                        check=False)
        shutil.rmtree(tmp2, ignore_errors=True)

    # Test 3: not-owned, content DIFFERS from the shipped arm -> must abort,
    # naming the differing file, and must NOT modify ARM_HOME.
    tmp3 = tempfile.mkdtemp(prefix="arm-idem-3-")
    try:
        arm_dir = make_shipped_arm(tmp3, content="current-shipped")
        skills_root = os.path.join(tmp3, "skills")
        arm_home = os.path.join(tmp3, "home", "log-rca")
        os.makedirs(os.path.dirname(arm_home), exist_ok=True)
        shutil.copytree(arm_dir, arm_home)
        # diverge ARM_HOME's content from what's shipped now
        with open(os.path.join(arm_home, "brief.py"), "w") as fh:
            fh.write("stale-content")
        subprocess.run(["chflags", "-R", "uchg", arm_home], check=True)

        proc = run_block({}, skills_root, "v43", arm_home)
        check(proc.returncode != 0,
              "not-owned + diverging content must abort, got rc=0")
        check("brief.py" in proc.stderr,
              "abort message does not name the differing file: %r" % proc.stderr)
        check(os.path.isfile(os.path.join(arm_home, "brief.py")),
              "aborting must not delete the not-owned ARM_HOME")
    finally:
        subprocess.run(["chflags", "-R", "nouchg", os.path.join(tmp3, "home", "log-rca")],
                        check=False)
        shutil.rmtree(tmp3, ignore_errors=True)
else:
    print("SKIP: chflags not available on this platform — "
          "not-owned-ARM_HOME branch not exercised", file=sys.stderr)

for msg in FAILED:
    print("FAIL: %s" % msg)
print("OK" if not FAILED else "FAILED %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
