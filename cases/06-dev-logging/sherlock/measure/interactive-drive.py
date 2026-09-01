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


class MarkWatch(object):
    """A LATCH, NOT A WINDOW: «has this mark EVER appeared» in O(1) memory.

    WHY THIS REPLACED THE BUFFERS. The driver used to ask a ring buffer a
    question about the past — `HANDOFF_MARK in ses.stage_buffer`, with the buffer
    trimmed to its last 4 MB once it passed 8 MB — and when the trim had fired it
    answered `handoff_block_unknown`. The paid run 20260827T173511Z-v41 came back
    «unknown» on two of its three boundaries, so the one measurement that exists
    to observe the skill's obedience stopped answering exactly where it mattered.

    A latch cannot have that failure mode. Text is scanned as it arrives and the
    fact is kept forever; the only state carried between chunks is the last
    len(mark)-1 characters, so a mark split across two reads is still found and
    the memory is a constant regardless of how many megabytes flow through.

    A bounded number of match CONTEXTS is kept as well — not to gate anything,
    but so the event log can quote what was on screen, and so a block that names
    a DIFFERENT stage can be told apart from this stage's own (see
    `hit_names_stage`; the real run's last boundary reported
    `handoff_block_on_screen` on a mark left over from the stage before it).
    """
    MAX_HITS = 32
    CONTEXT = 160

    def __init__(self, mark):
        self.mark = mark
        self.overlap = max(0, len(mark) - 1)
        self.reset()

    def reset(self):
        self._tail = ""
        self._want = 0        # chars still needed to finish the last context
        self.hits = []
        self.count = 0
        self.chars = 0        # how much text flowed through, for the record

    @property
    def seen(self):
        return self.count > 0

    def feed(self, text):
        self.chars += len(text)
        if self._want and self.hits:
            take = text[:self._want]
            self.hits[-1] += take
            self._want -= len(take)
        hay = self._tail + text
        at = 0
        while True:
            i = hay.find(self.mark, at)
            if i < 0:
                break
            self.count += 1
            if len(self.hits) < self.MAX_HITS:
                ctx = hay[i:i + self.CONTEXT]
                self.hits.append(ctx)
                self._want = self.CONTEXT - len(ctx)
            at = i + 1
        self._tail = hay[-self.overlap:] if self.overlap else ""


def hit_names_stage(context, completed):
    """Does this on-screen block belong to the stage that just closed?

    The block prints the stage it CLOSED — «СТУПЕНЬ ЗАВЕРШЕНА: triage» is what
    appears at the triage→draft boundary — so the name to look for is the stage
    the arm finished, not the one it moved to. MEASURED on
    20260827T173511Z-v41: the draft block was still being repainted 135 KB after
    `/clear` had been typed, i.e. inside the NEXT stage's window, and the driver
    counted it as the next boundary's receipt. Tolerant on purpose: a wrapped or
    truncated context names no stage at all, and that still counts — the point
    is to reject a block that provably belongs to someone else.
    """
    if completed and completed in context:
        return True
    for other in STAGES:
        if other != completed and other in context:
            return False
    return True


def scan_transcript(path, start, mark=HANDOFF_MARK, chunk=1 << 20):
    """A SECOND WITNESS, read off the disk instead of held in RAM.

    The transcript is the raw pty bytes and it is never trimmed (112 MB on the
    paid run), so it can answer the same question exactly — and a streamed scan
    from the stage's own start offset costs one 1 MB block of memory, not the
    file. It is complementary rather than redundant: the latch sees text with the
    ANSI escapes removed, this sees the bytes as they arrived, and a mark that
    one of them loses to a repaint or a split escape the other still finds.
    """
    hits = []
    needle = mark.encode("utf-8")
    over = max(0, len(needle) - 1)
    try:
        fh = open(path, "rb")
    except (OSError, IOError):
        return hits
    with fh:
        try:
            fh.seek(max(0, int(start or 0)))
        except (OSError, IOError):
            return hits
        carry = b""
        while True:
            blk = fh.read(chunk)
            if not blk:
                break
            hay = carry + blk
            at = 0
            while len(hits) < MarkWatch.MAX_HITS:
                i = hay.find(needle, at)
                if i < 0:
                    break
                hits.append(strip(hay[i:i + 400].decode("utf-8", "replace")))
                at = i + 1
            carry = hay[-over:] if over else b""
    return hits


def ledger_quiet_s(ledger_path):
    """Seconds since the upstream ledger last grew, or None if there is none yet.

    THE ONLY HONEST IDLE SIGNAL AVAILABLE. A TUI always shows its input prompt, so
    the screen cannot distinguish "waiting for you" from "thinking"; and a stage
    that has not advanced might simply be a long stage. But the ledger is written
    by the proxy on every upstream call, so its mtime is the last moment the target
    actually talked to the provider.

    MEASURED on the paid run 20260827T104334Z-v40: the last call landed at
    11:18:03 and ended `finish_reason: error`; the session then sat at its prompt
    until it was stopped at 11:36 — eighteen minutes — and would have burned the
    whole 5,400-second stage budget before STAGE_TIMEOUT. A human would have typed
    «продолжай» in ten seconds.

    None means "no ledger": nothing has started, which is not a stall.
    """
    if not ledger_path:
        return None
    try:
        return max(0.0, time.time() - os.path.getmtime(ledger_path))
    except OSError:
        return None


def stage_index(stage):
    """Where a stage sits in the machine, or -1 for "no checkpoint yet".

    ORDERING, NOT INEQUALITY. Caught on the first live rehearsal, three minutes
    in: the arm writes checkpoint.json for the FIRST time partway through the
    triage stage, so a driver that treats "the stage string changed" as an
    advance reads `None -> triage` as a completed stage and starts hunting for a
    handoff block that was never printed. Only a move FORWARD through STAGES is
    an advance.
    """
    if stage is None:
        # NO CHECKPOINT YET *IS* the triage stage — the skill says so: "if it
        # fails because there is no checkpoint, this is a new investigation: the
        # stage is triage". Mapping absence to -1 instead is what made the first
        # live rehearsal report NO_HANDOFF_BLOCK at 10:05:30, because the arm
        # creates checkpoint.json partway through triage and `0 > -1` read that
        # as a completed stage. The baseline is the stage the run STARTS in.
        return STAGES.index("triage")
    try:
        return STAGES.index(stage)
    except ValueError:
        return -1


def checkpoint_row(work):
    try:
        row = json.load(open(os.path.join(work, "checkpoint.json"),
                             encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return row if isinstance(row, dict) else {}


def ledger_prompt_tokens(path):
    """The last completed call's PROVIDER-MEASURED prompt tokens, or 0.

    Provider-measured, not estimated: this is the same source lane_guard uses
    for the 262,000 ceiling. The arm's own byte count was considered and
    rejected — read_file returns into the model's context without passing
    through any arm tool, so an arm-side meter is an estimate the MODEL owns,
    in a run where the model has twice edited measurement inputs.
    """
    last = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                usage = row.get("usage") or {}
                n = usage.get("prompt_tokens")
                if isinstance(n, int) and n > 0:
                    last = n
    except OSError:
        return 0
    return last


def first_fresh_after(path, after_ms):
    """The first completed CALL row logged after `after_ms`, or None.

    Refusal rows are skipped by `kind`: they carry no usage and no body, and a
    reader that assumes one schema raises on them — which is how four
    context-overflow refusals stayed invisible in two prior investigations.
    Rows without `ts_ms` are pre-v44 and are ignored rather than guessed at.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("kind") != "call":
                    continue
                stamp = row.get("ts_ms")
                if isinstance(stamp, int) and stamp > after_ms:
                    return row
    except OSError:
        return None
    return None


# THE CANONICAL LIST LIVES IN THE ARM, NOT HERE. This tuple used to be
# duplicated here and referenced by nothing — dead documentation, and the
# exact shape Finding 1 (fix round 1) found: a duplicate that can silently
# drift out of sync with the copy that actually matters. The name validation
# that makes the gate-tool escape safe against a self-granted "--gate-tool
# please" (fix round 2) has to live where `handoff` parses its arguments —
# skills/v44/tools/checkpoint.py's GATE_TOOLS, enforced there via
# `choices=GATE_TOOLS` on `--gate-tool` — because the arm ships standalone
# (installed to /opt/sherlock-arm/log-rca) and cannot import anything from
# measure/ at runtime, so a shared module under measure/ is not reachable
# from both sides. The driver does not need its own copy: it never inspects
# WHICH gate tool ran, only whether `gate_tools_run` is non-empty, and by the
# time a row reaches checkpoint.jsonl the arm has already refused to write
# one for an unrecognized name.


def boundary_history(work):
    """Every boundary row on disk, oldest first. Missing file reads as empty."""
    rows = []
    try:
        with open(os.path.join(work, "checkpoint.jsonl"),
                  encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def boundary_advanced(previous, current):
    """Did anything DELIVERABLE change between two boundaries?

    Deliberately narrow, and deliberately not file size. A committed report
    section, a worklist row moving to resolved, or a stage advance — those are
    the deliverable. A changed byte count alone is churn: run
    20260831T214240Z-v43's repair transcript never stood still for 1h49m and
    produced nothing.

    The escape is not charity, it is a measured correction. Paid run
    20260901T002401Z-v43's final four minutes ran probe.md through
    reportcheck.py and citecheck.py to learn what the gate would accept before
    writing — real work toward the deliverable that moves none of the counters
    above. A gate without this clause kills a run at the moment it starts to
    succeed.
    """
    if previous is None:
        return True, "first boundary in this stage"
    if current.get("stage") != previous.get("stage"):
        return True, "stage advanced"
    if (current.get("report_sections_written", 0)
            > previous.get("report_sections_written", 0)):
        return True, "a report section was committed"
    if current.get("resolved", 0) > previous.get("resolved", 0):
        return True, "worklist rows were resolved"
    if current.get("worklist_seals") != previous.get("worklist_seals"):
        return True, "a worklist seal changed"
    if current.get("gate_tools_run"):
        return True, "a gate tool was run against a candidate"
    return False, ""


def stage_of(work):
    return checkpoint_row(work).get("stage")


def boundary_of(work):
    """How many handoffs the arm has taken — partial ones included.

    THE SIGNAL THE DRIVER ACTUALLY NEEDS, and the reason it is separate from the
    stage. `handoff --partial` closes a BATCH and deliberately leaves the stage
    where it is, so a driver that watched only the stage saw nothing, nudged the
    model to carry on in the same session, and eventually called a healthy batch
    boundary a stall. Caught in review before the v41 paid launch.
    """
    try:
        return int(checkpoint_row(work).get("boundary_seq", 0) or 0)
    except (TypeError, ValueError):
        return 0


class Session(object):
    def __init__(self, argv, cwd, env, transcript):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:                      # child
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
            os._exit(127)
        self.transcript_path = transcript
        self.transcript = open(transcript, "wb")
        # NO SCREEN BUFFER AT ALL — and that is the fix, not an omission.
        # Every question this driver asks of the screen is «did this string ever
        # appear», and a latch answers that in constant memory and without ever
        # being wrong about the past. The accumulators that used to answer it
        # (a 400 KB tail for the /clear refusal, an 8 MB per-stage ring for the
        # handoff block) could both be asked about text they had already dropped;
        # the ring's version of «I dropped it» was the `handoff_block_unknown`
        # event that two of three boundaries of the paid run came back with.
        # The full text still exists: it is the transcript on disk.
        self.handoff = MarkWatch(HANDOFF_MARK)
        self.refusal = MarkWatch(CLEAR_REFUSAL)
        self.watches = (self.handoff, self.refusal)
        # Where this stage's output starts in the transcript, so the second
        # witness reads THIS stage and not the whole run.
        self.stage_offset = 0
        self.stage_bytes = 0

    def pump(self, seconds):
        """Read for `seconds`, appending to the transcript and feeding the latches."""
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
            self.stage_bytes += len(chunk)
            text = strip(chunk.decode("utf-8", "replace"))
            for watch in self.watches:
                watch.feed(text)
        return True

    def stage_reset(self):
        """Start a fresh stage: forget the latch, and remember where we are.

        Called AFTER the boundary has been judged, so the offset it records is
        the first byte of the next stage's output — which is what keeps a block
        printed late by the previous stage from being read as this one's receipt.
        """
        self.handoff.reset()
        self.stage_bytes = 0
        try:
            self.transcript.flush()
            self.stage_offset = self.transcript.tell()
        except (OSError, IOError, ValueError):
            self.stage_offset = 0

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
          transcript, skill_command, events_path=None, ledger_path="",
          idle_nudge_s=0, max_nudges=3, nudge_text="продолжай",
          handoff_grace_s=8.0, threshold=0):
    ses = Session(argv, cwd, dict(os.environ), transcript)
    events = []
    # THE LATCH: holds the boundary_seq the driver was at when it crossed the
    # threshold and typed `handoff --partial`, or None when no crossing is
    # pending. Its whole job is «one crossing produces one handoff» — without
    # it, every poll after the crossing would type another `handoff --partial`
    # until the boundary finally moved, and a slow arm would be typed at
    # dozens of times for one checkpoint.
    awaiting_boundary = None
    # THE PROGRESS GATE'S COUNTER. Consecutive barren boundaries within the
    # CURRENT stage — reset to 0 whenever a boundary shows deliverable
    # progress and whenever `seen` (the stage) changes, so a run that just
    # crossed into `repair` gets its two-boundary budget fresh rather than
    # inheriting a near-miss from `draft`. `boundary_advanced` already returns
    # True with "stage advanced" on the first boundary of a new stage (because
    # `same_stage` is pre-filtered to the CURRENT `seen`, so `previous` is None
    # right after a stage change) — the explicit `last_seen` check below is a
    # second, independent reset that does not depend on that filtering, so a
    # future refactor of `same_stage` cannot silently reintroduce carry-over.
    barren = 0
    last_seen = None

    def note(kind, detail=""):
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "event": kind, "detail": detail}
        events.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        # APPENDED AS IT HAPPENS, not written at the end. A run that lasts hours
        # has to be observable while it is alive: on the first live rehearsal the
        # event file stayed empty until the driver exited, so the only way to see
        # where a stage had got to was to read the ANSI transcript.
        if events_path:
            try:
                with open(events_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
            except OSError:
                pass

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
        seen_boundary = boundary_of(work)
        # THE LATCH IS RESET BEFORE TYPING, NEVER AT THE TOP OF THE
        # WAIT LOOP. A fast target answers during the settle that follows the
        # reseed line, so a reset at the top of the next iteration WIPES the
        # block it is about to look for — measured on the stand-in, where the
        # happy path recorded `not_shown` at every boundary while printing the
        # block at every boundary. Reset, type, wait, judge.
        ses.stage_reset()
        ses.type(skill_command, settle=settle_s)
        note("typed", skill_command)
        ses.type(first_prompt, settle=2.0)
        note("typed", "the task prompt")
        while True:
            deadline = time.time() + stage_budget_s
            nudges = 0
            completed = seen
            # THE NUDGE'S OWN CLOCK, separate from the ledger's. Measured on the
            # paid run 20260827T150830Z-v41 and on the free rehearsal before it:
            # the nudges fired in PAIRS five seconds apart (15:17:14 and
            # 15:17:19), because typing a nudge does not touch the ledger, so
            # the very next pass still saw the same quiet time and spent another
            # nudge on it. A budget of 3 bought 2 real attempts.
            #
            # A nudge deserves the same silence before it is judged as the
            # target did: wait a full idle_nudge_s after sending one before
            # sending or escalating again.
            last_nudge = None
            while time.time() < deadline:
                if not ses.pump(3.0) and not ses.alive():
                    note("DIED", "child exited at stage %s" % seen)
                    return 3, events
                now = stage_of(work)
                now_boundary = boundary_of(work)
                # NEVER CLEAR BEFORE THE CHECKPOINT IS DURABLE ON DISK. This
                # real-advance test runs FIRST, ahead of the threshold check
                # below: the arm can advance the boundary/stage on its own in
                # the very poll that also crosses the threshold (measured
                # live — the target's reply to the reseed line both finished
                # the batch AND the ledger was still over threshold on the
                # next poll), and firing the threshold branch anyway would
                # type a redundant `handoff --partial` for a boundary that
                # already closed. Checking the real advance first costs
                # nothing — the threshold is checked again next poll if it is
                # still crossed.
                if (stage_index(now) > stage_index(seen)
                        or now_boundary > seen_boundary):
                    # The latch is cleared here, at the ONE place that already
                    # proves the boundary advanced through the real
                    # `batch_boundary` / `stage_advanced` path — never a second
                    # clear path, the one that exists.
                    awaiting_boundary = None
                    partial = (stage_index(now) <= stage_index(seen))
                    # The block names the stage that CLOSED, which for a full
                    # advance is the one we were on, not the one we move to.
                    completed = seen
                    seen = now if now is not None else seen
                    seen_boundary = now_boundary
                    break
                # Sized to TURN_GROWTH_HEADROOM (corporate_settings.py): the
                # largest single jump this project has ever measured between
                # two consecutive completed calls, so one turn cannot carry
                # the prompt past `auto` before this fires. `awaiting_boundary
                # is None` is the latch — one crossing produces one `handoff
                # --partial`, not one per poll. It MUST be an `is None` check,
                # not a truthiness check: a run's first crossing latches at
                # boundary_seq 0, which is falsy, so `not awaiting_boundary`
                # would still read True on the very next poll and refire.
                elif (ledger_path and threshold and awaiting_boundary is None
                        and ledger_prompt_tokens(ledger_path) >= threshold):
                    note("threshold_handoff",
                         "%s (prompt %d >= %d)"
                         % (seen, ledger_prompt_tokens(ledger_path), threshold))
                    ses.type("handoff --partial %s" % seen, settle=2.0)
                    awaiting_boundary = now_boundary
                    continue
                # A STALL IS NOT A LONG STAGE, and the ledger can tell them
                # apart. Bounded on purpose: a target that answers a nudge with
                # nothing is broken, and nudging it forever would hide that
                # behind an hour of silence.
                if idle_nudge_s > 0:
                    quiet = ledger_quiet_s(ledger_path)
                    since_nudge = (None if last_nudge is None
                                   else time.time() - last_nudge)
                    if (quiet is not None and quiet >= idle_nudge_s
                            and (since_nudge is None
                                 or since_nudge >= idle_nudge_s)):
                        if nudges >= max_nudges:
                            note("STAGE_STALLED",
                                 "no upstream call for %d s at stage %s after %d "
                                 "nudge(s) — the target stopped and will not "
                                 "restart" % (int(quiet), seen, nudges))
                            return 7, events
                        nudges += 1
                        note("nudged", "%s (quiet %d s, nudge %d/%d)"
                             % (seen, int(quiet), nudges, max_nudges))
                        last_nudge = time.time()
                        ses.type(nudge_text, settle=2.0)
            else:
                note("STAGE_TIMEOUT", "no stage advance in %ds (stage %s)"
                     % (stage_budget_s, seen))
                return 4, events

            # TWO SIGNALS, AND THEY ANSWER DIFFERENT QUESTIONS. Learned on the
            # free-lane rehearsal at 10:11:41, which reported NO_HANDOFF_BLOCK
            # on a run whose stage machine had worked perfectly.
            #
            # `work/handoff.txt` is written by checkpoint.py handoff at the
            # instant the stage advances, so its presence proves the boundary was
            # taken THROUGH the contract rather than by something else editing
            # the checkpoint. That is a hard fact and it is the terminal.
            #
            # Whether the block also appeared ON SCREEN is a question about the
            # skill's obedience, and a repainting TUI is a poor witness: it
            # wraps, truncates and scrolls, so a block that was printed can be
            # gone from the visible buffer moments later. Judging a run on that
            # would fail obedient runs. So it is MEASURED per boundary and
            # reported, never used to kill the run.
            # AND THE BLOCK ARRIVES AFTER THE DISK DOES — so ask a moment
            # later. `checkpoint.py handoff` writes handoff.txt and advances
            # the stage as a TOOL CALL; the arm then prints the block as its
            # closing message. MEASURED on 20260827T173511Z-v41: the driver
            # typed `/clear` at transcript byte 65,480,805 and the first
            # «СТУПЕНЬ ЗАВЕРШЕНА» of that boundary appeared at 65,611,697 —
            # 131 KB, and several seconds, LATER. The screen was judged before
            # the screen had spoken. The grace is bounded and exits the instant
            # the latch fires, so an obedient target costs nothing.
            grace_end = time.time() + max(0.0, handoff_grace_s)
            while not ses.handoff.seen and time.time() < grace_end:
                if not ses.pump(0.5) and not ses.alive():
                    break
            # NEVER «UNKNOWN» — two independent witnesses, both exact.
            live = [h for h in ses.handoff.hits
                    if hit_names_stage(h, completed)]
            # The disk is only consulted when the latch came up empty: a loud
            # stage means re-reading tens of megabytes, and there is nothing to
            # settle once the live witness has already answered yes.
            disk = [] if live else [
                h for h in scan_transcript(ses.transcript_path,
                                           ses.stage_offset)
                if hit_names_stage(h, completed)]
            # More marks than kept contexts: we cannot attribute them, but we
            # DID see them, and «saw it» is the honest answer.
            overflow = ses.handoff.count > len(ses.handoff.hits)
            witness = ("latch" if live
                       else "transcript" if disk
                       else "latch (unattributed)" if overflow else "")
            on_screen = bool(live or disk or overflow)
            handoff_file = os.path.join(work, "handoff.txt")
            if not os.path.exists(handoff_file):
                note("NO_HANDOFF_BLOCK",
                     "stage is now %s but work/handoff.txt does not exist — the "
                     "boundary was not taken through checkpoint.py handoff" % seen)
                return 5, events
            note("batch_boundary" if partial else "stage_advanced", seen)
            # THE PROGRESS GATE. Two barren boundaries in one stage, and the
            # run stops. Two, not three: on 20260901T002401Z-v43 that ends the
            # run at 01:26 instead of 04:05, saving ~2.6 h and ~85M prompt
            # tokens. It is a STOP AND ESCALATE, following Claude Code's own
            # thrash rule — refusing to clear would only trade a livelock for
            # a window overflow, which is a different failure, not a fix.
            if seen != last_seen:
                barren = 0
                last_seen = seen
            history = boundary_history(work)
            same_stage = [r for r in history if r.get("stage") == seen]
            if same_stage:
                previous = same_stage[-2] if len(same_stage) >= 2 else None
                moved, why = boundary_advanced(previous, same_stage[-1])
                if moved:
                    barren = 0
                    if why:
                        note("progress", "%s — %s" % (seen, why))
                else:
                    barren += 1
                    note("no_progress_boundary",
                         "%s — boundary %s changed nothing deliverable "
                         "(%d in a row)"
                         % (seen, same_stage[-1].get("boundary_seq"), barren))
                    if barren >= 2:
                        note("NO_PROGRESS",
                             "%s — %d consecutive boundaries with no "
                             "deliverable change: resolved %s, "
                             "report_sections_written %s, report_bytes %s. "
                             "Stopping rather than burning the budget."
                             % (seen, barren,
                                [r.get("resolved") for r in same_stage[-3:]],
                                [r.get("report_sections_written")
                                 for r in same_stage[-3:]],
                                [r.get("report_bytes") for r in same_stage[-3:]]))
                        return 9, events
            volume = "%s — closed %s, %d B on screen, %d mark(s)" % (
                seen, completed, ses.stage_bytes, ses.handoff.count)
            if on_screen:
                note("handoff_block_on_screen", "%s, witness %s"
                     % (volume, witness))
            else:
                note("handoff_block_not_shown", volume)

            if seen == "done" and not partial:
                note("finished", "stage=done")
                return 0, events

            ses.stage_reset()
            # THE REFUSAL GETS A LATCH TOO — it used to be read out of the last
            # 4,000 characters of a buffer that trims itself, so a refusal
            # followed by one repaint of a wide TUI could scroll out of the
            # question before it was asked. Reset here, so only THIS `/clear`
            # can answer for itself.
            ses.refusal.reset()
            reseed_at_ms = int(time.time() * 1000)
            ses.type("/clear", settle=settle_s)
            if ses.refusal.seen:
                note("CLEAR_REFUSED", "a background task was still alive")
                return 6, events
            ses.type(skill_command, settle=settle_s)
            ses.type(reseed % {"work": os.path.abspath(work), "stage": seen},
                     settle=2.0)
            note("reseeded", seen)
            # DO NOT BELIEVE YOUR OWN KEYSTROKES. The refusal needle above
            # catches a /clear that says no; it cannot catch one that says
            # nothing and keeps the conversation, which is exactly what run
            # 20260831T214240Z-v43 did 119 times out of 124. A fresh session
            # sends exactly two messages — system prompt and seed — so the
            # first call after the reseed settles it as a fact, not a heuristic.
            # Token counts were considered and rejected: legitimate per-stage
            # growth makes any multiple of the floor an invented constant.
            if ledger_path:
                fresh = None
                verify_end = time.time() + max(settle_s * 4, 60.0)
                while time.time() < verify_end:
                    if not ses.pump(2.0) and not ses.alive():
                        break
                    fresh = first_fresh_after(ledger_path, reseed_at_ms)
                    if fresh is not None:
                        break
                if fresh is None:
                    note("clear_unverified",
                         "no completed call after the reseed within the "
                         "verification window — cannot confirm the clear")
                else:
                    count = fresh.get("messages_count")
                    if count is None:
                        note("clear_unverified",
                             "the first call after the reseed carries no "
                             "messages_count (pre-v44 proxy)")
                    elif count != 2:
                        note("CLEAR_NOT_EFFECTIVE",
                             "the first call after the reseed carried %d "
                             "messages, not 2 — /clear did not clear, and "
                             "every later measurement would be meaningless"
                             % count)
                        return 8, events
                    else:
                        note("clear_verified",
                             "%s — fresh session, %d messages, prompt %s"
                             % (seen, count,
                                (fresh.get("usage") or {}).get("prompt_tokens")))
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
    ap.add_argument("--ledger", default="",
                    help="the proxy's upstream ledger; its mtime is the only "
                         "honest signal that the target has stopped talking")
    ap.add_argument("--idle-nudge-s", type=int, default=0,
                    help="nudge when the ledger has been quiet this long "
                         "(0 = never nudge, the old behaviour)")
    ap.add_argument("--max-nudges", type=int, default=3,
                    help="after this many unanswered nudges the stage is STALLED")
    ap.add_argument("--nudge-text", default="продолжай",
                    help="what a human would type to restart a stopped turn")
    ap.add_argument("--settle-s", type=float, default=6.0)
    ap.add_argument("--handoff-grace-s", type=float, default=8.0,
                    help="after the checkpoint advances, wait up to this long "
                         "for the handoff block to finish printing before "
                         "judging whether it was shown (0 = judge instantly)")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--events", default=None, help="write the event log here too")
    ap.add_argument("--threshold", type=int, default=0,
                    help="ledger prompt_tokens at which the driver forces a "
                         "partial handoff and clears — 0 disables it "
                         "(corporate_settings.handoff_threshold())")
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
    if args.events:
        open(args.events, "w", encoding="utf-8").close()   # fresh log per run
    rc, events = drive(argv, args.cwd, args.work, args.prompt, args.reseed,
                       args.stage_budget_s, args.settle_s, args.transcript,
                       args.skill_command, args.events, args.ledger,
                       args.idle_nudge_s, args.max_nudges, args.nudge_text,
                       args.handoff_grace_s, args.threshold)
    print("rc=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
