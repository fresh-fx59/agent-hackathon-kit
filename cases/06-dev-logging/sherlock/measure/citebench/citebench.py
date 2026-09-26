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
tests: with-secret.sh neuraldeep_api_key -> qwen-run run --tenant sherlock).
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
MICRO_WALL_S = 300
MICRO_CALLS = 30

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
    return json.loads(json.dumps(obj).replace("/%s/" % old, "/%s/" % new)
                      .replace("skills/%s" % old, "skills/%s" % new))


def build_spec(base, *, mode, pkg, run_id, stage_dir, base_pkg="v60"):
    spec = _swap_pkg(base, base_pkg, pkg)
    spec["run_id"] = run_id
    spec["task"]["workdir_src"] = str(Path(stage_dir) / "wd")
    spec["task"]["trusted"] = [{"name": pkg, "src": str(Path(stage_dir) / ("trusted-" + pkg))},
                               {"name": "corpus", "src": str(Path(stage_dir) / "wd" / "corpus")}]
    draft = next(p for p in base["phases"] if p["id"] == "draft")
    if mode == "micro":
        spec["limits"]["max_provider_calls"] = MICRO_CALLS
        spec["limits"]["run_wall_s"] = MICRO_WALL_S + 120
        spec["phases"] = [{"id": "micro", "prompt": MICRO_PROMPT,
                           "limits": {"max_wall_time_s": MICRO_WALL_S}}]
        spec["qwen"]["settings"].pop("hooks", None)      # no Stop hook: microcheck is the gate
        spec["task"]["env"] = {"PYTHONDONTWRITEBYTECODE": "1", "BENCH_PKG": "/work/skills/" + pkg}
    elif mode == "replay":
        spec["phases"] = [_swap_pkg(draft, base_pkg, pkg)]
        spec["limits"]["run_wall_s"] = draft["limits"]["max_wall_time_s"] + 600
    else:
        raise ValueError(mode)
    return spec


def seed_work(src, dst, base_pkg, pkg):
    """Copy a finished run's work/ and roll it back to the investigate handoff."""
    shutil.copytree(src, dst, symlinks=True)
    inner = Path(dst) / "work"
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
    (out / "run-spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    launch = out / "launch.sh"
    launch.write_text(
        "#!/usr/bin/env bash\nset -u\nBASE=%s\nWSS=%s\nQWEN_RUN=%s\ncd \"$BASE\"\n"
        "\"$WSS\" neuraldeep_api_key --file-env QWR_SECRET_FILE_neuraldeep_api_key -- "
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
