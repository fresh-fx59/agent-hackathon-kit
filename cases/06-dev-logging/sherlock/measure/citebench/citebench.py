#!/usr/bin/env python3
"""citebench.py — citation micro-benchmark for Sherlock packages.

Stops paying for 20-40 min small tests to test citation/checker changes.

  stage     build a qwen-run staging dir + run-spec for ONE run
              --mode micro   : fixed 40-line corpus, the model writes 5-10 cited
                               claims to work/report.md and runs microcheck.py
                               until CLEAN. Hard cap: 300 s, 30 provider calls.
              --mode replay  : the draft stage started from the saved v60
                               investigate output (--seed-work), investigate
                               skipped. Normal draft wall (1800 s).
  collect   one finished qwen-run run dir -> one JSONL row
  summarize rows -> mean and spread per package

Paid runs are launched by the generated launch.sh (same path as the small
tests: with-secret.sh cliproxyapi_api_key -> qwen-run run --tenant sherlock).
"""
import argparse
import json
import os
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHERLOCK = HERE.parents[1]
# Part B (docs/specs/2026-09-27-replay-journalcheck-and-repair-loop-spec.md): the
# gate tool (hostgate.py) is staged from this separate, versioned harness location,
# never from skills/vNN — those tested trees must not be edited in place. --package
# in the gate argv keeps pointing at the real, untouched staged skill package.
HARNESS_DIR = HERE / "harness" / "v1"
MICRO_WALL_S = 300
MICRO_CALLS = 30
# v63: the replay harness used to be single-shot (draft only) — a model that
# correctly closed `draft` and stopped got no fresh process for repair at all,
# which hid the real bug (v62 ran draft + 11 check rounds in one session).
# Declare this many repeated repair phases; each carries its own copy of the
# base spec's trusted `gate` (hostgate.py, run by the harness OUTSIDE the
# sandbox) so a forged/tampered checkpoint.json can never skip a round — the
# gate recomputes pass/fail itself and does not read the model-written
# "stage" field.
MAX_REPAIR_ROUNDS = 6
# Part C (vault spec 2026-09-27-replay-journalcheck-and-repair-loop): each repair
# phase is a fresh session capped at 900 s, and its gate has `on_fail: continue`, so
# a failed gate starts the next repair phase instead of ending the run (qwen-run
# stops on pass, `no_progress`, or budget). Run wall = draft 1800 + 6 x 900 = 7200.
REPAIR_WALL_S = 900

MICRO_PROMPT = (
    "Микро-задача (цитирование). Корпус: /work/corpus (Security.jsonl, System.jsonl). "
    "Инструменты навыка: /work/skills-root/sherlock/tools (cite.py). "
    "Напиши файл /work/work/report.md строго по шаблону:\n"
    "## Находки\n\n### Н-1 · <заголовок>\n\n"
    "- [!PROVEN] <утверждение> — <ссылка>\n"
    "(5–10 таких пунктов о попытках входа в Security.jsonl; метка PROVEN, REPORTED или INFERENCE; "
    "ссылка — вида Security.jsonl:6#TargetUserName, её печатает первой строкой "
    "`python3 /work/skills-root/sherlock/tools/cite.py Security.jsonl:6`; "
    "хотя бы одно число по многим строкам — строкой `агрегат:` из "
    "`cite.py --corpus /work/corpus --file Security.jsonl --aggregate '<предикат>'`). "
    "Цитаты «…» руками не печатай. Затем запускай `python3 /work/bench/microcheck.py` "
    "и исправляй отчёт, пока он не напечатает CLEAN. Не больше 30 вызовов. "
    "Когда CLEAN — ответь одним словом ГОТОВО.")

REPLAY_PROMPT = "/sherlock ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ /work/work — СТУПЕНЬ draft"
# what the draft stage produced and investigate did not: removed from a seed
DRAFT_ARTIFACTS = ("validation", "report.md", "bench-rounds.jsonl")


def load_base_spec(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _swap_pkg(obj, old, new):
    """Swap every whole-word occurrence of the base package version (`old`,
    e.g. "v60") for the staged one (`new`) across a spec fragment.

    v63 bug (citebench-replay-v63-r2/v62-r2, 2026-09-27): this used to be two
    literal substring replacements, "/%s/" % old (needs a *trailing* slash)
    and "skills/%s" % old. Neither matches a bare trailing path segment like
    `--package $QWR_TRUSTED_DIR/v60` (end of the argv string, no trailing
    "/") — so that one gate argument silently kept pointing at the base
    package while every other v60 path in the same spec got swapped. A
    word-boundary regex over the whole dumped JSON has no such blind spot:
    it matches "v60" wherever it stands alone as a token (bounded by
    non-alnum/underscore on both sides), regardless of what follows it.
    """
    pattern = re.compile(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(old))
    return json.loads(pattern.sub(new, json.dumps(obj)))


class GateConfigError(Exception):
    """A gate argv references a tool/package path that was never staged.

    This is a setup error, not a report-gate failure: the model's report was
    never evaluated. Callers must surface it as a distinct status
    (`gate_error`), and `validate_gate_paths` must be called before any paid
    provider call, so this fails loudly at stage/validate time instead of
    silently as an indistinguishable `gate_failed` after the run already
    happened.
    """


def validate_gate_paths(spec, trusted_dirs):
    """Check every literal `$QWR_TRUSTED_DIR/<name>/...` path referenced by a
    phase's `gate.argv` actually exists under the staged trusted dir for
    <name> (`trusted_dirs[name]`, a local filesystem path). Raises
    GateConfigError naming every missing path if not.

    `trusted_dirs` maps each `task.trusted[].name` (e.g. "v63", "corpus") to
    the local directory it is staged from, i.e. what qwen-run will mount at
    $QWR_TRUSTED_DIR/<name> inside the gate's execution.
    """
    missing = []
    for phase in spec.get("phases", []):
        gate = phase.get("gate")
        if not gate:
            continue
        for arg in gate.get("argv", []):
            for m in re.finditer(r"\$QWR_TRUSTED_DIR/([^\s\"']+)", arg):
                rel = m.group(1)
                name, _, rest = rel.partition("/")
                local = trusted_dirs.get(name)
                if local is None:
                    missing.append((phase.get("id"), "$QWR_TRUSTED_DIR/" + rel,
                                    "no staged trusted dir named %r" % name))
                    continue
                path = Path(local) / rest if rest else Path(local)
                if not path.exists():
                    missing.append((phase.get("id"), "$QWR_TRUSTED_DIR/" + rel, str(path)))
    if missing:
        lines = ["phase %s: %s -> %s (missing)" % m for m in missing]
        raise GateConfigError(
            "gate argv references path(s) not present in the staged trusted dir "
            "(setup error, report never evaluated):\n" + "\n".join(lines))


def _redirect_hostgate(phase, pkg):
    """Point a phase's gate argv at the harness-staged hostgate.py instead of the
    staged skill package's own tools/hostgate.py, keeping --package pointed at the
    real (untouched) staged package for finalize.py/journalcheck.py/judge.py."""
    gate = phase.get("gate")
    if not gate:
        return phase
    pattern = re.compile(r"\$QWR_TRUSTED_DIR/%s/tools/hostgate\.py" % re.escape(pkg))
    gate["argv"] = [pattern.sub("$QWR_TRUSTED_DIR/harness/hostgate.py", s) for s in gate["argv"]]
    return phase


def build_spec(base, *, mode, pkg, run_id, stage_dir, base_pkg="v60",
               max_repair_rounds=MAX_REPAIR_ROUNDS):
    spec = _swap_pkg(base, base_pkg, pkg)
    spec["run_id"] = run_id
    spec["task"]["workdir_src"] = str(Path(stage_dir) / "wd")
    spec["task"]["trusted"] = [{"name": pkg, "src": str(Path(stage_dir) / ("trusted-" + pkg))},
                               {"name": "corpus", "src": str(Path(stage_dir) / "wd" / "corpus")},
                               {"name": "harness", "src": str(Path(stage_dir) / "trusted-harness")}]
    draft = next(p for p in base["phases"] if p["id"] == "draft")
    repair = next(p for p in base["phases"] if p.get("id") == "repair")
    if mode == "micro":
        spec["limits"]["max_provider_calls"] = MICRO_CALLS
        spec["limits"]["run_wall_s"] = MICRO_WALL_S + 120
        spec["phases"] = [{"id": "micro", "prompt": MICRO_PROMPT,
                           "limits": {"max_wall_time_s": MICRO_WALL_S}}]
        spec["qwen"]["settings"].pop("hooks", None)      # no Stop hook: microcheck is the gate
        spec["task"]["env"] = {"PYTHONDONTWRITEBYTECODE": "1", "BENCH_PKG": "/work/skills/" + pkg}
    elif mode == "replay":
        # A repeating repair phase, not a single shot: each round is its own
        # entry so the harness spawns a genuinely FRESH process per round, and
        # each round keeps the base spec's own trusted `gate` (hostgate.py) —
        # never the model-written checkpoint.json stage.
        repair_rounds = []
        for i in range(1, max_repair_rounds + 1):
            r = _redirect_hostgate(_swap_pkg(repair, base_pkg, pkg), pkg)
            r["id"] = "repair" if i == 1 else "repair-%d" % i
            r.setdefault("limits", {})["max_wall_time_s"] = REPAIR_WALL_S
            if r.get("gate"):
                r["gate"]["on_fail"] = "continue"
            repair_rounds.append(r)
        spec["phases"] = [_swap_pkg(draft, base_pkg, pkg)] + repair_rounds
        spec["limits"]["run_wall_s"] = (draft["limits"]["max_wall_time_s"]
                                        + max_repair_rounds * REPAIR_WALL_S)
    else:
        raise ValueError(mode)
    return spec


def seed_work(src, dst, base_pkg, pkg):
    """Copy a finished run's work/ and roll it back to the investigate handoff."""
    shutil.copytree(src, dst, symlinks=True)
    inner = Path(dst)            # the sandbox's /work/work
    for name in DRAFT_ARTIFACTS:
        p = inner / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    ck = inner / "checkpoint.jsonl"
    if ck.is_file():
        rows = [l for l in ck.read_text(encoding="utf-8").splitlines() if l.strip()]
        ck.write_text((rows[0] + "\n") if rows else "", encoding="utf-8")
    shown = inner / "cite-shown.tsv"
    if shown.is_file():
        shown.unlink()            # lines shown during the draft must be shown again
    for p in Path(dst).rglob("*"):
        if p.is_file() and not p.is_symlink() and p.suffix in (".json", ".jsonl", ".txt", ".md", ".tsv") \
                and p.stat().st_size < 5_000_000:
            t = p.read_text(encoding="utf-8", errors="replace")
            if "/skills/%s" % base_pkg in t:
                p.write_text(t.replace("/skills/%s" % base_pkg, "/skills/%s" % pkg), encoding="utf-8")


def stage(a):
    out = Path(a.out)
    wd = out / "wd"
    if out.exists():
        sys.exit("citebench: %s exists" % out)
    wd.mkdir(parents=True)
    pkg_src = Path(a.kit) / "skills" / a.pkg
    shutil.copytree(pkg_src, out / ("trusted-" + a.pkg))
    shutil.copytree(HARNESS_DIR, out / "trusted-harness")
    shutil.copytree(pkg_src, wd / "skills" / a.pkg)
    (wd / "skills-root").mkdir()
    os.symlink("../skills/" + a.pkg, wd / "skills-root" / "sherlock")
    shutil.copytree(a.corpus, wd / "corpus")
    if a.mode == "micro":
        (wd / "bench").mkdir()
        shutil.copy2(HERE / "microcheck.py", wd / "bench" / "microcheck.py")
        (wd / "work").mkdir()
    else:
        if not a.seed_work:
            sys.exit("citebench: --mode replay needs --seed-work <run>/work")
        seed = Path(a.seed_work)
        for sub in ("work", ".sherlock"):
            if (seed / sub).exists():
                seed_work(seed / sub, wd / sub, a.base_pkg, a.pkg) if sub == "work" else \
                    shutil.copytree(seed / sub, wd / sub)
        if (wd / ".sherlock").exists():
            for p in (wd / ".sherlock").rglob("*.json"):
                p.write_text(p.read_text(encoding="utf-8").replace(
                    "/skills/%s" % a.base_pkg, "/skills/%s" % a.pkg), encoding="utf-8")
        if a.tools_dir:
            shutil.copytree(a.tools_dir, wd / "tools")
    spec = build_spec(load_base_spec(a.base_spec), mode=a.mode, pkg=a.pkg, run_id=a.run_id,
                      stage_dir=out, base_pkg=a.base_pkg)
    trusted_dirs = {t["name"]: t["src"] for t in spec["task"]["trusted"]}
    validate_gate_paths(spec, trusted_dirs)  # setup error, not a report-gate failure: fail now
    (out / "run-spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    launch = out / "launch.sh"
    launch.write_text(
        "#!/usr/bin/env bash\nset -u\nBASE=%s\nWSS=%s\nQWEN_RUN=%s\ncd \"$BASE\"\n"
        "\"$WSS\" cliproxyapi_api_key --file-env QWR_SECRET_FILE_cliproxyapi_api_key -- "
        "\"$QWEN_RUN\" run --tenant sherlock \"$BASE/run-spec.json\" > \"$BASE/launch.log\" 2>&1\n"
        "echo $? > \"$BASE/launch.done\"\n" % (out, a.with_secret, a.qwen_run), encoding="utf-8")
    launch.chmod(0o755)
    print(out)


def _load_jsonl(path):
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def collect_row(run_dir, pkg, mode, rep):
    run = Path(run_dir)
    meta = json.loads((run / "run-meta.json").read_text(encoding="utf-8"))
    term = {}
    if (run / "run-terminal.json").is_file():
        term = json.loads((run / "run-terminal.json").read_text(encoding="utf-8"))
    rounds = _load_jsonl(run / "work" / "work" / "bench-rounds.jsonl")
    phases = meta.get("phases") or []
    tok = {"prompt": 0, "completion": 0, "cached": 0}
    calls, secs = 0, 0.0
    for ph in phases:
        for k in tok:
            tok[k] += (ph.get("tokens") or {}).get(k, 0) or 0
        calls += ph.get("calls") or 0
        secs += (ph.get("duration_s") or ph.get("wall_s") or 0) or 0
    if not secs and term.get("started_at") and term.get("finished_at"):
        secs = term["finished_at"] - term["started_at"]
    clean = [r for r in rounds if r.get("blocking") == 0]
    report = run / "work" / "work" / "report.md"
    final_typed = None
    if report.is_file():
        sys.path.insert(0, str(HERE))
        import microcheck
        final_typed = microcheck.typed_quotes(report.read_text(encoding="utf-8"))
    return {"schema": 1, "run_id": meta.get("run_id"), "pkg": pkg, "mode": mode, "rep": rep,
            "terminal": meta.get("terminal"),
            "pass": bool(clean) if mode == "micro" else meta.get("terminal") == "completed",
            "rounds": len(rounds), "rounds_to_clean": clean[0]["round"] if clean else None,
            "defects_per_round": [r.get("blocking") for r in rounds],
            "first_kinds": rounds[0].get("kinds") if rounds else None,
            "refs": rounds[-1].get("refs") if rounds else None,
            "typed_quotes": final_typed,
            "calls": calls, "tokens_total": (meta.get("spend") or {}).get("tokens_total"),
            "tokens": tok, "rub": (meta.get("spend") or {}).get("cost_rub"),
            "seconds": round(secs, 1) or None, "report_bytes": report.stat().st_size if report.is_file() else 0,
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def summarize(rows):
    out = {}
    for pkg in sorted({r["pkg"] for r in rows}):
        rs = [r for r in rows if r["pkg"] == pkg]
        res = {"n": len(rs), "pass": sum(1 for r in rs if r["pass"])}
        for key in ("rounds", "calls", "tokens_total", "rub", "seconds", "refs", "typed_quotes"):
            vals = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
            if vals:
                res[key] = {"mean": round(statistics.mean(vals), 2), "min": min(vals), "max": max(vals),
                            "sd": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0}
        firsts = [r["defects_per_round"][0] for r in rs if r.get("defects_per_round")]
        if firsts:
            res["first_round_defects"] = {"mean": round(statistics.mean(firsts), 2),
                                          "min": min(firsts), "max": max(firsts)}
        out[pkg] = res
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--mode", choices=("micro", "replay"), required=True)
    s.add_argument("--pkg", required=True)
    s.add_argument("--run-id", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--base-spec", required=True, help="a small-test run-spec.json (v60)")
    s.add_argument("--base-pkg", default="v60")
    s.add_argument("--kit", default=str(SHERLOCK))
    s.add_argument("--corpus", default=str(SHERLOCK / "tools/tests/fixtures/v60-smalltest/corpus"))
    s.add_argument("--seed-work", help="replay: <finished run>/work")
    s.add_argument("--tools-dir", help="replay: wd/tools (stop-hook-log.py)")
    s.add_argument("--with-secret", default="/home/claude-developer/personal-os/.claude/skills/secret-use/with-secret.sh")
    s.add_argument("--qwen-run", default="/home/claude-developer/qwr-m2/qwen-run/bin/qwen-run")
    c = sub.add_parser("collect")
    c.add_argument("run_dir")
    c.add_argument("--pkg", required=True)
    c.add_argument("--mode", default="micro")
    c.add_argument("--rep", type=int, default=1)
    c.add_argument("--out", required=True, help="append to this JSONL")
    m = sub.add_parser("summarize")
    m.add_argument("rows")
    a = ap.parse_args(argv)
    if a.cmd == "stage":
        stage(a)
    elif a.cmd == "collect":
        row = collect_row(a.run_dir, a.pkg, a.mode, a.rep)
        with open(a.out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=False))
    else:
        print(json.dumps(summarize(_load_jsonl(a.rows)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
