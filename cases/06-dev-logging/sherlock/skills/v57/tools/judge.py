#!/usr/bin/env python3
"""judge — host-side Opus review: do the report's conclusions follow from evidence?

v56 gate stage (G4), REQUIRED by operator decision 2026-09-26. Runs OUTSIDE the
sandbox, after the deterministic gates, after the agent phase has ended:

    python3 judge.py --report W/report.md --corpus C --out RUN/control/judge/judge.json

* Runtime: the Claude SUBSCRIPTION only — `claude -p --model opus` (Claude Code).
  With SHERLOCK_JUDGE_BROKER=1 the CLI may be pointed at the local broker
  (ANTHROPIC_BASE_URL=http://127.0.0.1:8317). Any other base URL, or a metered
  ANTHROPIC_API_KEY without the broker, is refused: never linkapi, never a
  metered API.
* Input: the report, the corpus line behind every citation, every aggregate
  with the count the corpus really returns, the prompt `judge-prompt.md`.
* Output: `--out` JSON {status, verdict, claims, verdict_check, model, prompt
  sha, report sha, evidence sha, wall_s}. The agent has no `claude` binary and no
  access to `--out` (outside the workspace), so it cannot run or forge this.
* Fail closed: CLI missing, refused runtime, timeout, non-JSON or schema error ->
  status `judge_unavailable`, exit 3. Never a pass.

Exit 0 = pass, 1 = fail, 3 = judge_unavailable, 2 = usage.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE = os.path.join(HERE, "judge-prompt.md")
BROKER = "http://127.0.0.1:8317"
STATUSES = ("supported", "unsupported", "contradicted", "uncited_absolute")
FAIL_STATUSES = ("contradicted", "uncited_absolute")
MAX_LINE = 600
MAX_EVIDENCE = 400


def _sibling(name):
    path = os.path.join(HERE, name + ".py")
    st = os.stat(path)
    key = "_sherlock_judge_%s_%x_%x" % (name, st.st_ino, int(st.st_mtime_ns))
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def runtime_refusal(env):
    """-> None when the subscription path is allowed, else the reason."""
    base = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    broker = env.get("SHERLOCK_JUDGE_BROKER") == "1"
    if base and not (broker and base == BROKER):
        return "ANTHROPIC_BASE_URL=%s is not the subscription broker %s" % (base, BROKER)
    if env.get("ANTHROPIC_API_KEY") and not broker:
        return "ANTHROPIC_API_KEY is set: that is a metered API, not the subscription"
    if "linkapi" in json.dumps({k: v for k, v in env.items()
                                if k.startswith(("ANTHROPIC", "CLAUDE"))}).lower():
        return "a linkapi endpoint is configured"
    return None


def build_evidence(report_text, report_path, corpus):
    CC = _sibling("citecheck")
    d = CC.check(report_text, corpus, require_quote=True, weak_quotes=True)
    ev = []
    for c in d["citations"][:MAX_EVIDENCE]:
        ev.append("[report line %s] %s -> %s\n    %s" % (
            c.get("report_line"), c.get("citation"), c.get("verdict"),
            (c.get("text") or "(no line)")[:MAX_LINE]))
    ag = []
    for a in (d.get("aggregates") or {}).get("items") or []:
        ag.append(json.dumps({k: a.get(k) for k in a if k in (
            "report_line", "path", "predicate", "claimed", "count", "actual",
            "verdict", "status", "raw")}, ensure_ascii=False)[:MAX_LINE])
    return "\n".join(ev), "\n".join(ag), len(d["citations"])


def ownership_table(report_text):
    out, on = [], False
    for line in report_text.splitlines():
        if re.match(r"^\s*#{1,6}\s", line):
            on = bool(re.search(r"принадлежност|ownership", line, re.I))
            continue
        if on and line.strip().startswith("|"):
            out.append(line[:MAX_LINE])
    return "\n".join(out) or "(no ownership table)"


def compose(report_text, evidence, aggregates, owners):
    with open(PROMPT_FILE, encoding="utf-8") as fh:
        prompt = fh.read()
    return (prompt + "\n\n=== REPORT ===\n" + report_text
            + "\n\n=== EVIDENCE (citation -> corpus line) ===\n" + (evidence or "(none)")
            + "\n\n=== AGGREGATES (claimed vs corpus) ===\n" + (aggregates or "(none)")
            + "\n\n=== OWNERSHIP TABLE ===\n" + owners + "\n"), prompt


def extract_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object in the judge reply")
    return json.loads(t[i:j + 1])


def validate(obj):
    if not isinstance(obj, dict) or obj.get("verdict") not in ("pass", "fail"):
        raise ValueError("verdict must be pass|fail")
    vc = obj.get("verdict_check")
    if not isinstance(vc, dict) or not isinstance(vc.get("follows"), bool):
        raise ValueError("verdict_check.follows must be a boolean")
    claims = obj.get("claims")
    if not isinstance(claims, list):
        raise ValueError("claims must be a list")
    for c in claims:
        if not isinstance(c, dict) or c.get("status") not in STATUSES:
            raise ValueError("claim status must be one of %s" % "|".join(STATUSES))
        if not isinstance(c.get("load_bearing"), bool):
            raise ValueError("claim load_bearing must be a boolean")
    return obj


def decide(obj, policy="conclusions"):
    """Our own rule, never looser than the model's own "fail".

    conclusions (default): fail when the verdict does not follow, or a
      LOAD-BEARING claim is contradicted by evidence. Uncited absolutes are
      flagged in the result; the deterministic reportcheck `absolute_uncited`
      is the gate that blocks them (one owner per defect).
    strict: also fail on a load-bearing uncited absolute.
    """
    fail_on = ("contradicted",) if policy == "conclusions" else FAIL_STATUSES
    bad = [c for c in obj["claims"] if c["status"] in fail_on and c["load_bearing"]]
    fail = bad or not obj["verdict_check"]["follows"]
    if policy == "strict" and obj["verdict"] == "fail":
        fail = True
    return "fail" if fail else "pass", bad


REPLY_SCHEMA = {
    "type": "object", "required": ["verdict", "verdict_check", "claims"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "verdict_check": {"type": "object", "required": ["stated", "follows", "reason"],
                          "properties": {"stated": {"type": "string"},
                                         "follows": {"type": "boolean"},
                                         "reason": {"type": "string"}}},
        "claims": {"type": "array", "items": {
            "type": "object",
            "required": ["section", "claim", "status", "load_bearing", "reason"],
            "properties": {"section": {"type": "string"}, "claim": {"type": "string"},
                           "status": {"type": "string", "enum": list(STATUSES)},
                           "load_bearing": {"type": "boolean"},
                           "reason": {"type": "string"}}}}}}


def call_claude(prompt, model, timeout, claude_bin):
    # v57: structured output. v56 parsed free text and a reply with one
    # unescaped quote inside a Russian claim (contabo broker run, 2026-09-26)
    # became judge_unavailable. --json-schema makes the CLI validate the object;
    # it needs a tool turn, so no --max-turns 1.
    argv = [claude_bin, "-p", "--model", model, "--output-format", "json",
            "--tools", "", "--no-session-persistence",
            "--json-schema", json.dumps(REPLY_SCHEMA)]
    r = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("claude exit %d: %s" % (r.returncode, (r.stderr or r.stdout)[-400:]))
    outer = json.loads(r.stdout)
    if outer.get("is_error"):
        raise RuntimeError("claude error: %s" % str(outer.get("result"))[:400])
    models = sorted((outer.get("modelUsage") or {}).keys())
    if isinstance(outer.get("structured_output"), dict):
        return json.dumps(outer["structured_output"], ensure_ascii=False), models, outer
    return outer.get("result") or "", models, outer


def run(report, corpus, out, model="opus", timeout=900, claude_bin="claude",
        env=None, caller=None, policy="conclusions"):
    env = os.environ if env is None else env
    t0 = time.time()
    with open(report, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    res = {"schema": 1, "tool": "sherlock-v57/judge.py",
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "report": os.path.abspath(report), "report_sha256": sha(text),
           "requested_model": model}
    try:
        evidence, aggregates, ncites = build_evidence(text, report, corpus)
        prompt, template = compose(text, evidence, aggregates, ownership_table(text))
        res.update(prompt_sha256=sha(template), input_sha256=sha(prompt),
                   citations=ncites)
        refusal = runtime_refusal(env)
        if refusal:
            raise PermissionError(refusal)
        if caller is None and not shutil.which(claude_bin):
            raise FileNotFoundError("no `%s` CLI on this host" % claude_bin)
        reply, models, outer = (caller or call_claude)(prompt, model, timeout, claude_bin)
        res["model"] = models
        res["usage"] = outer.get("usage") if isinstance(outer, dict) else None
        res["cost_note"] = "subscription (Claude Code); total_cost_usd is notional"
        obj = validate(extract_json(reply))
        status, bad = decide(obj, policy)
        res.update(status=status, verdict=obj["verdict"], policy=policy,
                   flagged=len([c for c in obj["claims"] if c["status"] != "supported"]),
                   verdict_check=obj["verdict_check"], claims=obj["claims"],
                   failing=len(bad))
    except Exception as exc:          # fail closed, whatever went wrong
        res.update(status="judge_unavailable", error="%s: %s" % (type(exc).__name__, exc))
    res["wall_s"] = round(time.time() - t0, 1)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--policy", choices=("conclusions", "strict"), default="conclusions")
    ap.add_argument("--print-prompt", action="store_true")
    a = ap.parse_args(argv)
    if a.print_prompt:
        text = open(a.report, encoding="utf-8", errors="replace").read()
        ev, ag, _n = build_evidence(text, a.report, a.corpus)
        print(compose(text, ev, ag, ownership_table(text))[0])
        return 0
    res = run(a.report, a.corpus, a.out, a.model, a.timeout, a.claude_bin,
              policy=a.policy)
    print(json.dumps({k: res.get(k) for k in ("status", "verdict", "failing", "model",
                                              "error", "wall_s")}, ensure_ascii=False))
    for c in res.get("claims") or []:
        if c["status"] != "supported":
            print("%s %s [%s] %s — %s" % ("✗" if c.get("load_bearing") else "·",
                                          c["status"], c.get("section"), c.get("claim", "")[:80],
                                        c.get("reason", "")[:160]))
    if res.get("verdict_check") and not res["verdict_check"].get("follows"):
        print("✗ verdict does not follow: %s" % res["verdict_check"].get("reason", "")[:200])
    return {"pass": 0, "fail": 1}.get(res["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
