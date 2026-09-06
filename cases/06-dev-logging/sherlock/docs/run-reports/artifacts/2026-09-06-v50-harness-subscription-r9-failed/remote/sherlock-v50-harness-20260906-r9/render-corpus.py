#!/usr/bin/env python3
import pathlib,sys
sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())
