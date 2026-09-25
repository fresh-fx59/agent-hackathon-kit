#!/usr/bin/env python3
"""Build the citecheck data index (v53, spec item 1).

    python3 tools/buildindex.py --corpus corpus [--index-root index] [--mem-cap-mb N]

Run it after corpus load or at first-stage start, from the workspace. Output:
<index-root>/<data_sha256>/ (files 0444). Default index root:
$SHERLOCK_INDEX_ROOT, else <parent of --corpus as given>/index. The Stop hook
never builds the index: this tool refuses to run inside it. Prints one JSON
line: dir, data_sha256, files, build stats. Rebuilding a fresh index is a no-op
unless --force.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccindex  # noqa: E402
import heartbeat as HB  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="построить индекс данных для citecheck")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--index-root")
    ap.add_argument("--mem-cap-mb", type=float, default=ccindex.INDEX_BUILD_MEM_CAP_MB,
                    help="потолок памяти под постинги, МБ (по умолчанию %d)"
                    % ccindex.INDEX_BUILD_MEM_CAP_MB)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    HB.beat("buildindex", "start")
    if any(os.environ.get(e) for e in ccindex.STOP_HOOK_ENVS):
        print("buildindex: refusing to build inside the Stop hook", file=sys.stderr)
        return 2
    if not os.path.isdir(args.corpus):
        print("нет такого каталога: %s" % args.corpus, file=sys.stderr)
        return 2
    root = args.index_root or os.environ.get(ccindex.INDEX_ROOT_ENV) or os.path.join(
        os.path.dirname(os.path.abspath(args.corpus)), ccindex.INDEX_DIRNAME)
    if not args.force:
        status, d, man = ccindex.find_index(args.corpus, roots=[os.path.abspath(root)])
        if status == "fresh":
            print(json.dumps({"dir": d, "data_sha256": man["data_sha256"], "fresh": True}))
            return 0
    man = ccindex.build(args.corpus, root, args.mem_cap_mb)
    print(json.dumps({"dir": man["dir"], "data_sha256": man["data_sha256"],
                      "files": len(man["files"]), "build": man["build"]}))
    HB.beat("buildindex", "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
