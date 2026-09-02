#!/usr/bin/env python3
"""REGRESSION LOCK — a STALE busy marker still sitting in the transcript tail
must NOT read as busy while the target is at an idle prompt.

WHY THIS EXISTS. Five free acceptance runs of v44 ended `rc=2
CLEAR_NOT_EFFECTIVE`, and the last one (20260902T075608Z-v44) typed `/clear`
while idle but `/sherlock` and the reseed line while busy. The remaining
suspect was `/clear` itself — "maybe it cannot be driven through a pty at
all". A live probe against the real qwen-code 0.22.0 on 2026-09-02
(`/home/claude-developer/probe/clearprobe3.py`, model gpt-5.5 via the broker)
settled that: driven at a genuinely idle prompt, `/clear` DOES work. The
target was given a codeword, cleared, then asked for it and answered exactly
`NONE`, and the TUI repainted its startup banner. There is no
"Starting a new session" line — that expectation was simply wrong.

WHAT WAS ACTUALLY BROKEN is `screen_busy`. It asks whether either busy
marker appears ANYWHERE in the last `BUSY_TAIL_BYTES` of the transcript. Its
docstring argues an mtime check bounds recency: "either the file keeps
growing past the marker, or it goes quiet and the mtime check catches it".
Both halves fail together on the real TUI, because qwen-code keeps
REPAINTING its footer after the turn ends (the context-usage line ticks, the
prompt redraws). The file therefore keeps growing — mtime stays fresh, so
the `BUSY_STALE_S` guard never fires — but it grows by far less than 16 KiB,
so the previous busy window's `esc to cancel` is still inside the window.
Verdict: busy, forever, on a target that is sitting at an idle prompt.
That is why the idle waits kept timing out and the driver typed anyway.

MEASURED, not argued. `probe2.raw` (1,072,966 bytes of real pty capture from
that session) carries 1,124 busy footer lines and 12 idle ones. At its very
last byte the target is unambiguously idle — its final footer is the bare
`YOLO mode (shift + tab to cycle)` — yet `esc to cancel` is still present in
the final 16 KiB. The two fixtures here are verbatim 16 KiB slices of that
capture:

  qwen022_idle_tail.bin  the final 16 KiB — target IDLE, stale marker present
  qwen022_busy_tail.bin  bytes 196608..212992 — target genuinely BUSY

THE DISCRIMINATOR is the footer qwen-code paints on EVERY frame. While a turn
is in flight it reads `Enter to steer · Ctrl+Q to queue · YOLO mode ...`;
the instant the turn ends those two hints disappear and it reads plain
`YOLO mode ...`. So the question to ask is not "is a marker anywhere in the
window" (a question about the PAST) but "what does the LAST frame say" (a
question about NOW). Note this directly contradicts the `BUSY_MARKER`
comment's claim that `Ctrl+Q to queue` is "a STATIC hint painted on the
status line whether or not anything is queued": in 1,136 real footer lines
it appears only on busy frames.

RUN THIS ON ed2b296 (before the fix): the idle fixture asserts False and gets
True — the stale-marker false-busy, reproduced.
RUN THIS ON HEAD: both fixtures agree with the live target.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import importlib.util

spec = importlib.util.spec_from_file_location(
    "interactive_drive", os.path.join(HERE, "..", "interactive-drive.py"))
drive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drive)

FIX = os.path.join(HERE, "fixtures")


def lay_down(name, tmp):
    """Copy a fixture to a transcript path with a FRESH mtime.

    Fresh mtime is the whole point: it is the condition under which the
    `BUSY_STALE_S` guard does not fire, which is the real target's normal
    state (its footer keeps repainting after the turn ends).
    """
    with open(os.path.join(FIX, name), "rb") as fh:
        blob = fh.read()
    with open(tmp, "wb") as fh:
        fh.write(blob)
    now = time.time()
    os.utime(tmp, (now, now))
    return tmp


def main():
    import tempfile
    failures = []
    tmpdir = tempfile.mkdtemp(prefix="stalebusy-")

    idle = lay_down("qwen022_idle_tail.bin", os.path.join(tmpdir, "idle.log"))
    busy = lay_down("qwen022_busy_tail.bin", os.path.join(tmpdir, "busy.log"))

    # The capture's own ground truth, restated as an assertion so a future
    # edit to the fixtures cannot quietly invalidate the test's premise.
    for path, want_last in ((idle, False), (busy, True)):
        with open(path, "rb") as fh:
            text = drive.strip(fh.read().decode("utf-8", "replace"))
        assert "esc to cancel" in text, (
            "PREMISE BROKEN: %s must contain a stale busy marker, else it "
            "cannot exercise the bug at all" % path)
        footers = [l for l in text.splitlines() if "YOLO mode" in l]
        assert footers, "PREMISE BROKEN: no footer line in %s" % path
        got_last = ("Ctrl+Q to queue" in footers[-1]
                    or "Enter to steer" in footers[-1])
        assert got_last is want_last, (
            "PREMISE BROKEN: %s last footer busy=%s, expected %s"
            % (path, got_last, want_last))

    if drive.screen_busy(busy) is not True:
        failures.append(
            "screen_busy() said NOT busy on a genuinely busy capture "
            "(qwen022_busy_tail.bin) — the detector would type into a "
            "swallow window")

    if drive.screen_busy(idle) is not False:
        failures.append(
            "screen_busy() said BUSY on a capture whose last frame is an "
            "IDLE prompt (qwen022_idle_tail.bin) — this is the stale-marker "
            "false-busy that made every idle wait time out, so the driver "
            "typed /clear, /sherlock and the reseed anyway")

    if failures:
        print("FAIL test_stale_busy_marker_v44")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("PASS test_stale_busy_marker_v44 "
          "(idle capture reads idle, busy capture reads busy)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
