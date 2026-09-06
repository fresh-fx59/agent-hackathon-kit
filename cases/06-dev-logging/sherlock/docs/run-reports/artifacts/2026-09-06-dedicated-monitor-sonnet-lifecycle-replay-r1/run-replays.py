#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path
root=Path(sys.argv[1]).resolve()
argv=json.loads((root/"review-command.json").read_text())["argv"]
summary={"schema":1,"uid":os.getuid(),"gid":os.getgid(),"argv":argv,"replays":[]}
for name in ("r9","r3-fault"):
    before=time.time()
    completed=subprocess.run(argv,input=(root/(name+".input.txt")).read_bytes(),
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    (root/(name+".stdout.json")).write_bytes(completed.stdout)
    (root/(name+".stderr.txt")).write_bytes(completed.stderr)
    summary["replays"].append({"name":name,"exit_code":completed.returncode,
        "wall_seconds":time.time()-before,"stdout_bytes":len(completed.stdout),
        "stderr_bytes":len(completed.stderr)})
(root/"execution-summary.json").write_text(json.dumps(summary,sort_keys=True,separators=(",",":"))+"\n")
print(json.dumps(summary,sort_keys=True))
