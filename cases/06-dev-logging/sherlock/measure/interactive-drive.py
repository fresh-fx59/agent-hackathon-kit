#!/usr/bin/env python3
"""Drive a REAL interactive qwen session the way the corporate user drives it.

WHY A PTY AND NOT `qwen -p`. The acceptance gate must run the exact target
(CLAUDE.md), and the corporate lane is an interactive session: a TTY, `/sherlock`
typed by hand, `/clear` between stages. None of that path is exercised by
`qwen -p` — not the slash commands, not clearLoadedSkills, not the background-work
refusal. So this driver allocates a pty, types the same keystrokes a human types,
and records the transcript.

WHAT IT WATCHES, AND WHY IT IS THE DISK AND NOT THE SCREEN. A terminal UI is
ANSI-repainted, wrapped and re-rendered; parsing it for "the stage is done" is
guesswork. `work/checkpoint.json` is not: the arm advances `stage` there as the
LAST action of a stage. So the driver polls that file, and only uses the screen
to confirm the handoff block was actually printed. The disk is the trigger; the
screen is the receipt.

  stage advanced on disk  ->  send /clear  ->  send /sherlock  ->  send the
  reseed line  ->  wait for the next advance.

FAIL LOUD, NEVER FAIL QUIET. Every terminal here is named and non-zero:
  STAGE_TIMEOUT      a stage made no progress inside its budget
  NO_HANDOFF_BLOCK   the stage advanced but the block was never printed (the
                     human would have had nothing to copy)
  CLEAR_REFUSED      qwen refused /clear — a live background task, the exact
                     trap the skill is written to avoid
  DIED               the child exited before reaching stage=done
"""
import argparse
import errno
import json
import os
import pty
import shutil
import re
import select
import signal
import subprocess
import sys
import time

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")
HANDOFF_MARK = "СТУПЕНЬ ЗАВЕРШЕНА"
CLEAR_REFUSAL = "background tasks before starting a new session"
STAGES = ("triage", "draft", "repair", "done")


def strip(text):
    return ANSI.sub("", text)


def stage_of(work):
    try:
        row = json.load(open(os.path.join(work, "checkpoint.json"),
                             encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return row.get("stage") if isinstance(row, dict) else None


class Session(object):
    def __init__(self, argv, cwd, env, transcript):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:                      # child
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
            os._exit(127)
        self.transcript = open(transcript, "wb")
        self.buffer = ""

    def pump(self, seconds):
        """Read for `seconds`, appending to the transcript and the buffer."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.2)
            except (OSError, ValueError):
                return False
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):
                    return False
                raise
            if not chunk:
                return False
            self.transcript.write(chunk)
            self.transcript.flush()
            self.buffer += strip(chunk.decode("utf-8", "replace"))
            if len(self.buffer) > 400000:      # keep the tail, bound the RAM
                self.buffer = self.buffer[-200000:]
        return True

    def type(self, text, settle=1.5):
        """Type a line the way a human does: the text, then Enter."""
        os.write(self.fd, text.encode("utf-8"))
        time.sleep(0.35)
        os.write(self.fd, b"\r")
        self.pump(settle)

    def alive(self):
        try:
            done, _ = os.waitpid(self.pid, os.WNOHANG)
        except OSError:
            return False
        return done == 0

    def close(self):
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            self.transcript.close()
        except OSError:
            pass


def drive(argv, cwd, work, first_prompt, reseed, stage_budget_s, settle_s,
          transcript, skill_command):
    ses = Session(argv, cwd, dict(os.environ), transcript)
    events = []

    def note(kind, detail=""):
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "event": kind, "detail": detail}
        events.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    try:
        ses.pump(settle_s)                     # let the UI come up
        note("started", " ".join(argv))
        # SNAPSHOT THE STAGE BEFORE TYPING, NEVER AFTER. Found on the stand-in:
        # a fast target can finish the stage and advance `stage` on disk while
        # the driver is still settling, so a snapshot taken after the prompt
        # already reads the NEW stage — and the driver then waits a full stage
        # budget for an advance that has already happened. This one line is the
        # difference between a driver that works and one that reports
        # STAGE_TIMEOUT on a healthy run.
        seen = stage_of(work)
        ses.type(skill_command, settle=settle_s)
        note("typed", skill_command)
        ses.type(first_prompt, settle=2.0)
        note("typed", "the task prompt")
        while True:
            deadline = time.time() + stage_budget_s
            mark = len(ses.buffer)
            while time.time() < deadline:
                if not ses.pump(3.0) and not ses.alive():
                    note("DIED", "child exited at stage %s" % seen)
                    return 3, events
                now = stage_of(work)
                if now != seen and now is not None:
                    seen = now
                    break
            else:
                note("STAGE_TIMEOUT", "no stage advance in %ds (stage %s)"
                     % (stage_budget_s, seen))
                return 4, events

            tail = ses.buffer[max(0, mark - 2000):]
            if HANDOFF_MARK not in tail:
                # The arm advanced the stage but never showed the human what to
                # do next. On a real desk that is a stalled session.
                note("NO_HANDOFF_BLOCK", "stage is now %s" % seen)
                return 5, events
            note("stage_advanced", seen)

            if seen == "done":
                note("finished", "stage=done")
                return 0, events

            ses.type("/clear", settle=settle_s)
            if CLEAR_REFUSAL in ses.buffer[-4000:]:
                note("CLEAR_REFUSED", "a background task was still alive")
                return 6, events
            ses.type(skill_command, settle=settle_s)
            ses.type(reseed % {"work": os.path.abspath(work), "stage": seen},
                     settle=2.0)
            note("reseeded", seen)
    finally:
        ses.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True,
                    help="the work/ directory holding checkpoint.json")
    ap.add_argument("--cwd", default=".", help="the session's project directory")
    ap.add_argument("--prompt", required=True, help="the first task prompt")
    ap.add_argument("--reseed",
                    default="ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ %(work)s — СТУПЕНЬ %(stage)s")
    ap.add_argument("--skill-command", default="/sherlock")
    ap.add_argument("--stage-budget-s", type=int, default=3600)
    ap.add_argument("--settle-s", type=float, default=6.0)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--events", default=None, help="write the event log here too")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- the interactive command to drive (default: qwen)")
    args = ap.parse_args()
    argv = [a for a in args.command if a != "--"] or ["qwen"]
    # Resolve the program BEFORE the child chdir's into --cwd: a relative
    # command would otherwise be looked up in the session's project directory
    # and fail with a bare ENOENT that looks exactly like a dead target.
    found = shutil.which(argv[0]) or (os.path.abspath(argv[0])
                                      if os.path.exists(argv[0]) else None)
    if not found:
        sys.stderr.write("✗ cannot find the interactive command %r\n" % argv[0])
        return 2
    argv[0] = found
    rc, events = drive(argv, args.cwd, args.work, args.prompt, args.reseed,
                       args.stage_budget_s, args.settle_s, args.transcript,
                       args.skill_command)
    if args.events:
        with open(args.events, "w", encoding="utf-8") as fh:
            for row in events:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("rc=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
