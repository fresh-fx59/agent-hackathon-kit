#!/usr/bin/env python3
"""FIX 9: the on-screen handoff signal must never answer «I cannot say».

WHAT THE PAID RUN DID. 20260827T173511Z-v41 reported `handoff_block_unknown` on
two of its three boundaries — «this stage printed more than 8 MB, so the driver
cannot say whether the block was shown» — and was accepted anyway. The one
measurement that exists to observe the skill's obedience went blind exactly where
the run was longest.

AND THE MESSAGE BLAMED THE WRONG THING. The truncation was real (the triage stage
printed 65.6 MB) but it was not the cause. From the run's own transcript, byte
offsets of the literal «СТУПЕНЬ ЗАВЕРШЕНА» and of the `/clear` the driver typed
after judging the boundary:

    boundary 1 → draft:   /clear at 65,480,805    first mark at 65,611,697
    boundary 2 → repair:  /clear at 105,150,232   first mark at 105,285,133

The block was printed 131 KB and 135 KB AFTER the driver had already asked
whether it had been printed. `checkpoint.py handoff` lands on disk as a tool
call; the arm prints the block afterwards as its closing message. The driver was
reading the screen before the screen had spoken — and the 8 MB ring buffer merely
supplied a comforting excuse for the empty answer.

Worse, the third boundary reported `handoff_block_on_screen` for the WRONG
REASON: no «СТУПЕНЬ ЗАВЕРШЕНА» exists in the transcript after byte 105,323,108,
so what that boundary matched was the draft block, still being repainted inside
the repair stage's own buffer. Two blind boundaries and one false positive.

THE FIX, and what these tests pin:
  * a LATCH instead of a window (MarkWatch): scan as text arrives, keep the fact
    forever, keep only len(mark)-1 characters between chunks. Truncation becomes
    irrelevant and a mark split across two reads is still found.
  * a GRACE window after the advance, bounded, exited the moment the latch fires.
  * a SECOND witness: a streamed scan of the transcript from the stage's own
    start offset — exact, cheap, and never trimmed.
  * stage attribution, so a block that names another stage cannot be counted.
  * `work/handoff.txt` is still the only terminal. The tolerance is unchanged;
    only the third state, «unknown», is gone.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHERLOCK = os.path.normpath(os.path.join(HERE, "..", ".."))
MEASURE = os.path.join(SHERLOCK, "measure")
SKILLS = os.path.join(SHERLOCK, "skills")
DRIVER = os.path.join(MEASURE, "interactive-drive.py")
CHECKPOINT = os.path.join(SKILLS, "v41", "tools", "checkpoint.py")
FAKE = os.path.join(HERE, "fixtures", "fake_qwen.py")
FAILED = []

HEADER = u"# id\tвердикт\n"
ROW = u"W-%d\t%s\n"


def check(name, cond, detail=""):
    print(("✓ " if cond else "✗ ") + name
          + (("  — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def load_driver():
    spec = importlib.util.spec_from_file_location("interactive_drive", DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = load_driver()
MARK = D.HANDOFF_MARK


def make_work(rows=3, closed=False):
    d = tempfile.mkdtemp(prefix="obs9-")
    w = os.path.join(d, "work")
    os.makedirs(w)
    verdict = u"N a.log:1 «q» n=1 фон" if closed else u"?"
    body = HEADER + "".join(ROW % (i, verdict) for i in range(1, rows + 1))
    io.open(os.path.join(w, "worklist.tsv"), "w",
            encoding="utf-8").write(body)
    subprocess.run([sys.executable, CHECKPOINT, "init", "--work", w],
                   capture_output=True)
    return d, w


def drive(mode, budget=180, flood_mb="9", extra=(), late_s="2.0",
          closed=False, grace="6"):
    d, w = make_work(closed=closed)
    env = dict(os.environ, FAKE_WORK=w, FAKE_CHECKPOINT=CHECKPOINT,
               FAKE_MODE=mode, FAKE_FLOOD_MB=flood_mb, FAKE_LATE_S=late_s,
               PYTHONUNBUFFERED="1")
    transcript = os.path.join(d, "t.log")
    events = os.path.join(d, "e.jsonl")
    p = subprocess.run(
        [sys.executable, DRIVER, "--work", w, "--cwd", d,
         "--prompt", u"РАЗБЕРИ ИНЦИДЕНТ", "--transcript", transcript,
         "--events", events, "--stage-budget-s", str(budget),
         "--settle-s", "0.8", "--handoff-grace-s", grace] + list(extra)
        + ["--", sys.executable, "-u", FAKE],
        capture_output=True, text=True, env=env, timeout=budget * 4)
    rows = ([json.loads(l) for l in io.open(events, encoding="utf-8")]
            if os.path.exists(events) else [])
    size = os.path.getsize(transcript) if os.path.exists(transcript) else 0
    return p, w, rows, size


def kinds(rows, kind):
    return [r for r in rows if r["event"] == kind]


# --------------------------------------------------------------------- 1. unit
print("\n— the latch itself")

w = D.MarkWatch(MARK)
w.feed(u"шум " + MARK + u": triage хвост")
check("the latch sees a mark in one chunk", w.seen and w.count == 1, w.count)

w = D.MarkWatch(MARK)
half = len(MARK) // 2
w.feed(u"шум " + MARK[:half])
check("half a mark is not a mark", not w.seen)
w.feed(MARK[half:] + u": draft")
check("a mark SPLIT ACROSS TWO CHUNKS is still found", w.seen and w.count == 1,
      w.count)

# one character at a time is the worst case a pty can hand us
w = D.MarkWatch(MARK)
for ch in (u"..." + MARK + u": repair ..."):
    w.feed(ch)
check("a mark arriving one character per chunk is found", w.count == 1, w.count)

w = D.MarkWatch(MARK)
w.feed(u"начало " + MARK + u": triage\n")
blob = u"Читаю Security.evtx: событие\n" * 40000        # ~1.1 MB per feed
for _ in range(20):
    w.feed(blob)
check("a mark scrolled MEGABYTES into the past is still found",
      w.seen and w.count == 1, w.count)
check("...and the run really was multi-megabyte",
      w.chars > 20 * 1024 * 1024, w.chars)
held = (len(w._tail) + len(w.mark) + sum(len(h) for h in w.hits))
check("...and memory stayed bounded (< 100 kB of retained text)",
      held < 100000, held)
check("...far below the text that flowed through", held * 100 < w.chars, held)

# the same feed, with the mark NEVER printed: an honest negative, not «unknown»
w = D.MarkWatch(MARK)
for _ in range(10):
    w.feed(blob)
check("megabytes without the mark answer NO, definitely", not w.seen)

print("\n— stage attribution")
check("a block naming the closed stage counts",
      D.hit_names_stage(MARK + u": triage", "triage"))
check("a block naming ANOTHER stage does not count",
      not D.hit_names_stage(MARK + u": draft", "repair"))
check("a wrapped context naming no stage still counts (tolerant on purpose)",
      D.hit_names_stage(MARK + u": tri", "triage"))

print("\n— the transcript witness")
td = tempfile.mkdtemp(prefix="obs9t-")
tp = os.path.join(td, "t.log")
fh = open(tp, "wb")
fh.write((u"старое " + MARK + u": triage\n").encode("utf-8"))
offset = fh.tell()
fh.write(b"x" * (3 * 1024 * 1024))
fh.write((u"\x1b[2K" + MARK + u": draft\n").encode("utf-8"))
fh.write(b"y" * (3 * 1024 * 1024))
fh.close()
hits = D.scan_transcript(tp, offset)
check("the transcript witness finds a mark 3 MB into the file",
      len(hits) == 1, hits)
check("...and it does NOT see the previous stage's mark before the offset",
      hits and "draft" in hits[0], hits)
check("...and reading before the offset does see both",
      len(D.scan_transcript(tp, 0)) == 2, D.scan_transcript(tp, 0))
check("a missing transcript is not a crash", D.scan_transcript(tp + ".nope", 0)
      == [])

print("\n— the third state is gone from the source")
src = io.open(DRIVER, encoding="utf-8").read()
check("no `handoff_block_unknown` event can be emitted any more",
      'note("handoff_block_unknown"' not in src)
# The names may survive in the prose that explains why they are gone; what
# must not survive is a field anything can read.
check("no dead per-stage ring buffer is left behind",
      "self.stage_buffer" not in src and "stage_truncated" not in src)
check("the /clear refusal is no longer read out of a trimmed slice",
      "ses.buffer[-4000:]" not in src and "self.buffer" not in src)

# --------------------------------------------------------------------- 2. e2e
print("\n— end to end through a real pty: a stage that prints 9 MB")

p, w, rows, size = drive("flood")
seen = [r["event"] for r in rows]
check("the loud run finished", p.returncode == 0, seen)
check("...it really pushed megabytes through the pty", size > 8000000, size)
check("...and NOT ONE boundary came back unknown",
      not [r for r in rows if r["event"] == "handoff_block_unknown"], seen)
check("...every boundary reported the block on screen",
      len(kinds(rows, "handoff_block_on_screen")) == 3
      and not kinds(rows, "handoff_block_not_shown"),
      [r["detail"] for r in rows if r["event"].startswith("handoff_block")])

print("\n— 9 MB and the block never printed: the honest negative")
p, w, rows, size = drive("flood_silent")
seen = [r["event"] for r in rows]
check("the silent-but-loud run still finished (the screen is never fatal)",
      p.returncode == 0, seen)
check("...it is reported NOT SHOWN, not unknown",
      len(kinds(rows, "handoff_block_not_shown")) == 3
      and not kinds(rows, "handoff_block_unknown")
      and not kinds(rows, "handoff_block_on_screen"),
      [r["detail"] for r in rows if r["event"].startswith("handoff_block")])

print("\n— the real run's shape: 9 MB, then the block AFTER the checkpoint moved")
# The lateness has to exceed the driver's own poll interval (it notices the
# advance within 3 s), or the block would already be latched by the time the
# question is asked and the grace window would not be under test at all.
p, w, rows, size = drive("flood_late", late_s="9.0", grace="20")
seen = [r["event"] for r in rows]
check("the late-block run finished", p.returncode == 0, seen)
check("...all three boundaries got a DEFINITE answer",
      len(kinds(rows, "handoff_block_on_screen"))
      + len(kinds(rows, "handoff_block_not_shown")) == 3
      and not kinds(rows, "handoff_block_unknown"), seen)
check("...and the answer is ON SCREEN — v41 called two of these unknown",
      len(kinds(rows, "handoff_block_on_screen")) == 3,
      [r["detail"] for r in rows if r["event"].startswith("handoff_block")])

print("\n— the terminal did not move")
p, w, rows, size = drive("forged_advance", budget=60, flood_mb="0")
seen = [r["event"] for r in rows]
check("a stage advanced without checkpoint.py handoff is still fatal",
      p.returncode == 5, (p.returncode, seen))
check("...and it is still NO_HANDOFF_BLOCK",
      bool(kinds(rows, "NO_HANDOFF_BLOCK")), seen)
check("...work/handoff.txt really is absent",
      not os.path.exists(os.path.join(w, "handoff.txt")))

print("\n— an ordinary quiet run is unchanged")
p, w, rows, size = drive("happy", budget=60, flood_mb="0", closed=True)
check("the happy path still finishes at stage=done", p.returncode == 0,
      [r["event"] for r in rows])
check("...with three boundaries on screen",
      len(kinds(rows, "handoff_block_on_screen")) == 3,
      [r["event"] for r in rows])

print("")
if FAILED:
    print("✗ %d check(s) failed: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("✓ all checks passed")
