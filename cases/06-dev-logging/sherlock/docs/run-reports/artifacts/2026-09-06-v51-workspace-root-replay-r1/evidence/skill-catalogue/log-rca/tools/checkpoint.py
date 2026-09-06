#!/usr/bin/env python3
import argparse, json
from pathlib import Path
parser=argparse.ArgumentParser(); parser.add_argument('operation'); parser.add_argument('--work', required=True); args=parser.parse_args()
work=Path(args.work)
if args.operation != 'resume' or not work.is_dir(): raise SystemExit(2)
(work/'resume-receipt.json').write_text(json.dumps({"cwd": str(Path.cwd())}, sort_keys=True)+'\n')
