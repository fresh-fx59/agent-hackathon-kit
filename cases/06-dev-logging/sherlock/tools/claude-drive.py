#!/usr/bin/env python3
"""Drive a Sherlock run on Claude Code (subscription) instead of qwen-run.

MECHANICS stand-in only: reproduces a qwen-run spec (phases, prompts, wall
limits, Stop hook, trusted gates) with `claude -p`. Evidence that the harness
works; NOT evidence that Qwen Code + DeepSeek passes.

Usage: claude-drive.py --staged DIR --run-id ID [--model M] [--base DIR]
  DIR = staged qwen-run dir (run-spec.json, wd/, trusted-v70, trusted-harness).
Layout (mirrors qwen-run): <run>/work = agent cwd (copy of wd), so the skill's
./work is <run>/work/work; <run>/out/<phase>/stream.jsonl; <run>/trusted/*.
Every /work path in prompts, env and hooks is rewritten to <run>/work.
"""
import argparse, json, os, re, shutil, signal, subprocess, sys, time
from pathlib import Path

TOOLS = "Bash,Read,Write,Edit,Glob,Grep,TodoWrite,Skill,Task"
NORMALIZE = True
CLAUDE = "/run/current-system/sw/bin/claude"


def rewrite(s, run_work):
    return re.sub(r"(?<![\w.])/work(?=[/\s\"']|$)", run_work, s)


def rewrite_obj(o, run_work):
    if isinstance(o, str):
        return rewrite(o, run_work)
    if isinstance(o, list):
        return [rewrite_obj(x, run_work) for x in o]
    if isinstance(o, dict):
        return {k: rewrite_obj(v, run_work) for k, v in o.items()}
    return o


# Claude tool name -> qwen tool name. The trusted harness (triagegate, journalcheck)
# matches qwen names (e.g. "agent"); the driver writes a qwen-named stream.jsonl
# for gates and keeps the untouched stream in stream.raw.jsonl.
NAME_MAP = {"Agent": "agent", "Task": "agent", "Bash": "run_shell_command",
            "Read": "read_file", "Write": "write_file", "Edit": "edit",
            "Grep": "grep_search", "Glob": "glob", "Skill": "skill",
            "TodoWrite": "todo_write"}


def normalize_stream(out):
    raw = out / "stream.raw.jsonl"
    shutil.copy(out / "stream.jsonl", raw)
    with open(raw) as fi, open(out / "stream.jsonl", "w") as fo:
        for line in fi:
            try:
                ev = json.loads(line)
            except ValueError:
                fo.write(line)
                continue
            if ev.get("type") == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        b["name"] = NAME_MAP.get(b.get("name"), b.get("name"))
            if ev.get("type") == "user" and isinstance(ev.get("message"), dict):
                c = ev["message"].get("content")
                for b in c if isinstance(c, list) else []:
                    # qwen writes is_error:false on success; Claude omits it
                    if isinstance(b, dict) and b.get("type") == "tool_result" \
                            and b.get("is_error") is None:
                        b["is_error"] = False
            fo.write(json.dumps(ev, ensure_ascii=False) + "\n")


def run_phase(run, phase, env, model, settings_path):
    pid = phase["id"]
    out = run / "out" / pid
    out.mkdir(parents=True, exist_ok=True)
    prompt = rewrite(phase["prompt"], str(run / "work"))
    (out / "prompt.txt").write_text(prompt)
    wall = int(phase["limits"]["max_wall_time_s"])
    cmd = [CLAUDE, "-p", prompt, "--model", model, "--output-format", "stream-json",
           "--verbose", "--permission-mode", "bypassPermissions",
           "--settings", str(settings_path),
           "--tools", TOOLS, "--strict-mcp-config"]  # mirror qwen tools.core (no web/MCP)
    t0 = time.time()
    timed_out = False
    with open(out / "stream.jsonl", "w") as so, open(out / "stderr.txt", "w") as se:
        p = subprocess.Popen(cmd, cwd=run / "work", env=env, stdout=so, stderr=se,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            rc = p.wait(timeout=wall)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(p.pid, signal.SIGTERM)  # our own process group only
            try:
                rc = p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                rc = p.wait()
    wall_s = round(time.time() - t0, 1)
    if NORMALIZE:
        normalize_stream(out)
    (out / "exit.txt").write_text(str(rc))
    res = {"phase": pid, "exit": rc, "timed_out": timed_out, "wall_s": wall_s,
           "wall_limit_s": wall}
    tools = {}
    src = out / ("stream.raw.jsonl" if NORMALIZE else "stream.jsonl")
    models = set()
    for line in src.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("model"):
            models.add(ev["model"])
        if ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    n = b.get("name")
                    if n in ("Skill", "Task", "Agent"):
                        n += ":" + str(b.get("input", {}).get("skill")
                                       or b.get("input", {}).get("subagent_type") or "")
                    tools[n] = tools.get(n, 0) + 1
        if ev.get("type") == "result":
            for k in ("subtype", "is_error", "num_turns", "duration_ms", "total_cost_usd",
                      "usage", "modelUsage", "terminal_reason", "result"):
                if k in ev:
                    v = ev[k]
                    res["result_text_head" if k == "result" else k] = (
                        str(v)[:300] if k == "result" else v)
    res["models_seen"] = sorted(models)
    res["tool_use_counts"] = tools
    return res


def run_gate(run, phase, env_base):
    g = phase["gate"]
    pid = phase["id"]
    out = run / "out" / pid
    gi = run / "gate-index" / pid
    gi.mkdir(parents=True, exist_ok=True)
    env = dict(env_base)
    env.update(QWR_TRUSTED_DIR=str(run / "trusted"), QWR_RUN_DIR=str(run),
               QWR_GATE_INDEX_DIR=str(gi))
    t0 = time.time()
    try:
        cp = subprocess.run(g["argv"], env=env, capture_output=True, text=True,
                            timeout=g.get("timeout_s", 900), cwd=run)
        rc, so, se = cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as e:
        rc, so, se = 124, (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "gate timeout"
    (out / "gate.stdout").write_text(so)
    (out / "gate.stderr").write_text(se)
    (out / "gate.exit").write_text(str(rc))
    gj = None
    for cand in (so.strip(), *reversed([l for l in so.splitlines() if l.startswith("{")])):
        try:
            gj = json.loads(cand)
            break
        except ValueError:
            continue
    r = {"exit": rc, "pass": rc == 0, "wall_s": round(time.time() - t0, 1)}
    if isinstance(gj, dict):
        (out / "gate.json").write_text(json.dumps(gj, indent=1, ensure_ascii=False))
        for k in ("pass", "passed", "ok", "blocking", "blocking_count", "verdict", "status"):
            if k in gj:
                r["gate_" + k] = gj[k] if not isinstance(gj[k], (list, dict)) else (
                    len(gj[k]) if isinstance(gj[k], list) else gj[k])
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--base", default="/home/claude-developer/hack")
    ap.add_argument("--no-normalize", action="store_true",
                    help="keep Claude tool names in stream.jsonl (gates then cannot match 'agent')")
    a = ap.parse_args()
    global NORMALIZE
    NORMALIZE = not a.no_normalize
    staged = Path(a.staged)
    spec = json.loads((staged / "run-spec.json").read_text())
    run = Path(a.base) / a.run_id
    if run.exists():
        sys.exit("run dir exists: %s" % run)
    work = run / "work"
    shutil.copytree(staged / "wd", work, symlinks=True)
    (run / "out").mkdir()
    tr = run / "trusted"
    tr.mkdir()
    for t in spec["task"]["trusted"]:
        (tr / t["name"]).symlink_to(Path(t["src"]).resolve()
                                    if Path(t["src"]).exists() else
                                    staged / Path(t["src"]).name)
    rw = str(work)
    env = os.environ.copy()
    env.update(rewrite_obj(spec["task"]["env"], rw))
    # Claude Code project skill + agents (qwen read skills-root/ and agents/)
    cl = work / ".claude"
    (cl / "skills").mkdir(parents=True)
    (cl / "skills" / "sherlock").symlink_to("../../skills/v70")
    (cl / "agents").mkdir()
    for f in sorted((work / "skills" / "v70" / "agents").glob("*.md")):
        (cl / "agents" / f.name).write_text(rewrite(f.read_text(), rw))
    # Stop hook mapped from qwen settings
    hooks = rewrite_obj(spec["qwen"]["settings"].get("hooks", {}), rw)
    settings = {"hooks": hooks, "env": env_subset(env, spec["task"]["env"]),
                "permissions": {"defaultMode": "bypassPermissions"}}
    (cl / "settings.json").write_text(json.dumps(settings, indent=1))
    settings_path = cl / "settings.json"
    for s in spec["task"].get("setup", []):
        cp = subprocess.run(s, shell=True, cwd=work, env=env, capture_output=True, text=True)
        (run / "setup.log").write_text(cp.stdout + cp.stderr)
        if cp.returncode:
            sys.exit("setup failed: %s" % s)
    summary = {"run_id": a.run_id, "model_requested": a.model, "started": time.time(),
               "note": "MECHANICS stand-in on Claude Code; not Qwen+DeepSeek evidence",
               "phases": []}
    stop = False
    for ph in spec["phases"]:
        if stop:
            break
        res = run_phase(run, ph, env, a.model, settings_path)
        if "gate" in ph:
            res["gate"] = run_gate(run, ph, env)
            if not res["gate"]["pass"] and ph["gate"].get("on_fail") != "continue":
                res["stopped_run"] = True
                stop = True
        summary["phases"].append(res)
        (run / "summary.json").write_text(json.dumps(summary, indent=1))
    rp = work / "work" / "report.md"
    summary["report_md_bytes"] = rp.stat().st_size if rp.exists() else None
    summary["report_has_marker"] = (
        bool(re.search(r"(?i)(TODO|PLACEHOLDER|<<<|MARKER)", rp.read_text(errors="replace")))
        if rp.exists() else None)
    summary["finished"] = time.time()
    (run / "summary.json").write_text(json.dumps(summary, indent=1))
    (run / "driver.done").write_text("0\n")


def env_subset(env, keys):
    return {k: env[k] for k in keys}


if __name__ == "__main__":
    main()
