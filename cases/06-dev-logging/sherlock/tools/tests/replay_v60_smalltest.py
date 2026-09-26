#!/usr/bin/env python3
"""Replay the v60 small test's five report versions (one per check.py call,
reconstructed from draft-stream.jsonl.gz, fixtures/v60-smalltest/) through a
package's reportcheck + citecheck and print blocking defects per version.

    SHERLOCK_PKG=v60 python3 replay_v60_smalltest.py
    SHERLOCK_PKG=v61 python3 replay_v60_smalltest.py [--json]

v61 applies finalize.autofix_labels first, as its render step does. Offline."""
import collections, importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path

SHERLOCK = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures" / "v60-smalltest"


def load(pkg, name):
    spec = importlib.util.spec_from_file_location("%s_%s" % (pkg, name),
                                                  SHERLOCK / "skills" / pkg / "tools" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def replay(pkg):
    tools = SHERLOCK / "skills" / pkg / "tools"
    fin = load(pkg, "finalize")
    out = []
    for path in sorted(FIX.glob("check*-call???.md")):
        text = path.read_text(encoding="utf-8")
        fixed = []
        if hasattr(fin, "autofix_labels"):
            text, fixed = fin.autofix_labels(text)
            cc_mod = load(pkg, "citecheck")
            if hasattr(cc_mod, "enum_decode_autofix"):
                text, more = cc_mod.enum_decode_autofix(text)
                fixed += more
        tmp = Path(tempfile.mkdtemp(prefix="replay-")) / "report.md"
        tmp.write_text(text, encoding="utf-8")
        shown = path.with_suffix(".shown.tsv")   # cite.py addresses + worklist, from the stream
        if shown.is_file():
            (tmp.parent / "cite-shown.tsv").write_text(shown.read_text(encoding="utf-8"), encoding="utf-8")
        rc = json.loads(subprocess.run([sys.executable, str(tools / "reportcheck.py"), str(tmp), "--json"],
                                       capture_output=True, text=True).stdout)
        cc = subprocess.run([sys.executable, str(tools / "citecheck.py"), str(tmp), "--corpus",
                             str(FIX / "corpus"), "--require-quote", "--json"],
                            capture_output=True, text=True)
        d = json.loads(cc.stdout[cc.stdout.find("{"):])
        kinds = collections.Counter(x["defect"] for x in rc.get("defects") or [])
        for c in d.get("citations") or []:
            if c.get("verdict") not in ("ok", "repeat", "mention"):
                kinds["cite:" + c["verdict"]] += 1
        for key in ("outcomes", "report_evidence", "aggregates"):
            for it in (d.get(key) or {}).get("items") or []:
                if it.get("verdict", "ok") != "ok":
                    kinds["agg:" + it["verdict"]] += 1
            if key != "aggregates" and (d.get(key) or {}).get("blocking"):
                kinds[key] += (d.get(key) or {}).get("blocking")
        out.append({"version": path.stem, "autofixed": len(fixed),
                    "blocking": sum(kinds.values()), "kinds": dict(kinds)})
    return out


if __name__ == "__main__":
    res = replay(os.environ.get("SHERLOCK_PKG", "v61"))
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for r in res:
            print("%-18s blocking %2d autofixed %d  %s" % (r["version"], r["blocking"], r["autofixed"],
                                                          json.dumps(r["kinds"], ensure_ascii=False)))
