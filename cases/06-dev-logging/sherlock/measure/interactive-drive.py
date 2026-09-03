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
# THE BANNER THAT MEANS «I WILL NOT ACCEPT INPUT», as printed by qwen-code
# 0.22.0 — verbatim from 20260901T002401Z-v43's transcript, where it appeared
# 65 times while the driver reported a stall:
#   The session has reached the maximum number of turns: 600. Please update
#   this limit in your setting.json file.
#
# NOT a bare "maximum number of turns": this repo IS the corpus for the case
# that banner came from, so an honest RCA agent narrating its own
# investigation, or `command grep "maximum number of turns" ...`-ing a
# transcript on screen, would echo exactly that fragment — a false positive
# that would kill an honest run, caught in fix round 1. Two fragments of the
# CLI's own sentence must BOTH appear before the latch fires (see
# `AllOfWatch` below): a fragment naming the limit, and the DISTINCT tail
# that only the CLI's real wording carries — the instruction to edit
# setting.json. The digits (600) are deliberately excluded: the limit is
# configurable and this run's value is not a fact about the banner.
TARGET_REFUSAL_NEEDLES = (
    "reached the maximum number of turns",
    "this limit in your setting.json",
)
STAGES = ("triage", "draft", "repair", "done")
# THE ROOT CAUSE OF v44's free-run block, run 20260902T021751Z-v44: qwen-code
# 0.22.0 QUEUES typed input while a turn is in flight — «⏳ 2 queued» — instead
# of executing it. This driver used to type `/clear` the instant
# `boundary_seq` advanced on disk, with no regard for whether the target was
# still generating. The keystrokes landed in the TUI's own input queue, never
# reached the model, and `messages_count` climbed across every later boundary
# (23, 25, 27, 33, 37, 41 in that run) because the conversation was never
# actually reset.
#
# THE NEEDLE: the in-flight footer `esc to cancel`, verified against that
# run's real transcript (`command grep -c "esc to cancel"` found 3,977 hits,
# always inside the spinner line `⠋ ... (Ns · esc to cancel)`). It CANNOT
# appear on an idle screen: qwen-code only ever prints it as part of the
# busy-spinner footer, which is replaced by the plain
# `Enter to steer · Ctrl+Q to queue · ...` status line the instant a turn
# finishes — that line never carries "esc to cancel" (checked against the
# same transcript: every "esc to cancel" hit sits on a `⠋/⠙/⠹/⠸/⠼/⠴/⠦` spinner
# row, none on the bare status row). `Ctrl+Q to queue` itself is rejected as
# a needle: it is a STATIC hint painted on the status line whether or not
# anything is queued (it appears whenever the footer with "Enter to steer"
# is on screen, which is most of an idle session), so it would make the
# idle-wait time out on almost every boundary.
#
# `queued`, spelled exactly like this — the PAST PARTICIPLE, not the verb —
# is a SEPARATE, safe second marker (round 3 fix, run 20260902T053801Z-v44):
# the queued-input indicator only ever reads `⏳ N queued`, printed once
# something is actually sitting in the input queue; the idle footer's own
# `Ctrl+Q to queue` does not contain the substring "queued" (it ends in
# "queue", four characters short), so it cannot false-positive against this
# needle the way it would have against a needle of "queue".
BUSY_MARKER = "esc to cancel"
QUEUED_MARKER = "queued"
BUSY_MARKERS = (BUSY_MARKER, QUEUED_MARKER)
# THE FOOTER, THE ONLY SIGNAL THAT DESCRIBES *NOW*. Both markers above answer
# a question about the PAST — "did this ever get printed in the window" — and
# `screen_busy` used to accept that as a verdict about the present. It is not
# one: qwen-code 0.22.0 keeps REPAINTING after a turn ends (the context-usage
# line ticks, the prompt redraws), so the transcript keeps growing — the
# `BUSY_STALE_S` mtime guard never fires — but grows by far less than
# `BUSY_TAIL_BYTES`, leaving the finished turn's `esc to cancel` inside the
# window for good. Verdict: busy forever, on a target sitting at an idle
# prompt. That false-busy is what made every idle wait time out, after which
# the driver types anyway — the actual cause of the five `CLEAR_NOT_EFFECTIVE`
# free runs, and why `/sherlock` and the reseed kept getting swallowed.
#
# qwen-code paints a footer on EVERY frame, and it differs by state:
#   in flight   `Enter to steer · Ctrl+Q to queue · YOLO mode (shift + tab...`
#   idle prompt `YOLO mode (shift + tab to cycle)`
# So the LAST footer in the window is a statement about the current frame.
# MEASURED on real pty capture `probe2.raw` (1,072,966 B, qwen 0.22.0,
# gpt-5.5 via the broker, 2026-09-02): 1,124 busy footers, 12 idle ones, and
# the two hints below appear on busy frames ONLY. This supersedes the
# `BUSY_MARKER` comment's claim that `Ctrl+Q to queue` is "a STATIC hint
# painted... whether or not anything is queued" — that claim was wrong, and
# rejecting the hint is what left the detector with only past-tense needles.
FOOTER_ANCHOR = "YOLO mode"
FOOTER_BUSY_HINTS = ("Enter to steer", "Ctrl+Q to queue")
# STARTUP IS NOT A BOUNDARY, AND IT NEEDED ITS OWN GATE. Paid run
# 20260902T171049Z-v44 made ZERO upstream calls and sat silent for 28
# minutes. Its transcript says why: `/sherlock` was typed 7 seconds after
# launch, while the TUI still showed «Initializing...», and came back
# `✕ Unknown command: /sherlock` — thirteen times. The task prompt then
# went into a session with NO SKILL LOADED and nothing was ever sent.
#
# The cause was structural, not a tuning miss. `drive()` opened with
# `ses.pump(settle_s)  # let the UI come up` — a blind fixed wait written on
# 2026-08-27 (d1e2fbe), before any idleness detection existed — and every
# one of v44's four idle-wait fixes was wired into the BOUNDARY path
# (`/clear`, the `/sherlock` retype, `handoff --partial`, the reseed line).
# Startup was never given one. Free run 20260902T105204Z-v44 typed at the
# same +7s an hour earlier and won the race, which is why five prior runs
# proved nothing about it.
#
# AND AN IDLE WAIT ALONE WOULD NOT HAVE SAVED IT. While qwen-code is
# initializing, its footer carries NO busy hint — no `esc to cancel`, no
# `Ctrl+Q to queue`, no `queued` — so every busy marker this driver has,
# the footer rule above included, reads that screen as IDLE. Readiness is a
# POSITIVE signal, and the honest one is that the init spinner has STOPPED
# REPAINTING: it ticks continuously while loading (27 frames in that paid
# transcript) and stops only when loading ends, so arrival recency answers
# it correctly — unlike the busy question, where a marker going quiet
# mid-turn is exactly the trap.
INIT_MARKER = "Initializing"
# 2.0s: the real TUI repaints the init spinner several times a second (the
# frames in that transcript are well under a second apart), so two seconds
# of silence on it is many missed ticks, not one slow one.
INIT_QUIET_S = 2.0
# 180s bounds the wait so a target that never becomes ready still reaches a
# terminal instead of hanging — the paid run's 28 silent minutes, and the
# 21,600s it would have burned, are the reason this number exists at all.
INIT_WAIT_S = 180.0
# THE SECOND HALF OF THE SAME BUG: nothing checked the OUTCOME. The driver
# logged `typed /sherlock`, which records that keystrokes were sent, never
# that the command was accepted, and no code looked for the rejection. So
# the race had no way of being observed until it lost, with money on the
# table. `Unknown command: <the skill command>` is the target's own words,
# matched exactly — scoped to the bytes that arrive right after the command
# is typed, so a corpus that merely contains the phrase cannot trip it.
SKILL_REJECTED_FMT = "Unknown command: %s"
# 3 attempts, each preceded by a fresh readiness wait: enough for a target
# whose skills register late, bounded so a genuinely missing skill (the v43
# `settings.skills.directories` failure) is NAMED rather than run around.
SKILL_TYPE_ATTEMPTS = 3
# 1.0s bounds how long the rejection is waited for, but it is a CEILING and
# not a cost: the probe returns the moment the needle appears, and an
# accepted command pays one 0.2s tick. The real target answered instantly.
SKILL_REJECT_PROBE_S = 1.0
# QUIET AND IDLE IS «STOPPED». QUIET AND BUSY IS «BLOCKED IN A TOOL CALL»,
# and those are different components with different fixes. Free acceptance run
# 20260902T193433Z-v44 reported `STAGE_STALLED — the target stopped and will
# not restart` while its screen read
# «⠋ Communing with the machine spirit... (6m 21s · ↑ 11k tokens · esc to
# cancel)» under a shell call to the arm's own `stopcheck.py`, which was
# blocking forever on stdin. The terminal was the right call and the wording
# sent the reader at the wrong component; finding the truth took a hand read
# of a 190 MB transcript.
#
# Both signals were already here — the ledger for "has it talked to the
# provider", the footer for "is the screen busy" — they were simply never
# combined. And the distinction is not cosmetic: typing a nudge into a busy
# qwen-code 0.22.0 QUEUES instead of executing (the reason v44 waits for idle
# at every other typing site), so every nudge sent to a tool-blocked target is
# wasted by construction, and that run spent 603 s proving it.
TOOL_CALL_MARK = 'Shell {"command":"'
# How much of the transcript tail to search for the in-flight command. Sized
# to hold several frames of a wrapped tool banner plus its spinner repaints;
# the real banner in that run wrapped over four screen rows.
TOOL_TAIL_BYTES = 1 << 16
# TAIL WINDOW for the CURRENT-SCREEN read of the transcript file (see
# `screen_busy` below). Sized off the real run 20260902T053801Z-v44's own
# transcript: the raw byte gap between consecutive `esc to cancel` repaints
# has a median of 997 B and a p95 of 1613 B (measured directly against that
# transcript). 16 KiB is ~10x the p95 gap — comfortable margin for a slower
# or irregular repaint tick — while staying a fixed, cheap read (one seek +
# one read) against a transcript that reaches hundreds of MB.
BUSY_TAIL_BYTES = 1 << 14
# THE OTHER HALF OF THE RECENCY GUARANTEE. A byte window alone bounds
# recency only while the transcript keeps growing — a target that goes
# GENUINELY idle typically stops writing to the pty almost entirely (no
# more spinner, no more tokens), so the last busy repaint can sit inside
# `BUSY_TAIL_BYTES` of an otherwise-frozen file forever, and a pure
# byte-window check would then read "busy" for good. (Caught in testing:
# `test_idle_wait_v44.py`'s busy-then-truly-idle fixture hung past 90s on a
# byte-window-only version of this function — the transcript stopped
# growing the instant the busy window closed, so the stale marker never
# aged out.) `BUSY_STALE_S` closes that: the transcript file's own mtime is
# the wall-clock moment its last byte was written, so a busy verdict also
# requires that timestamp to be recent. 3.0s is comfortably above the
# measured repaint gap (p95 1613 B / roughly a second or so of real spinner
# cadence) so a genuinely busy target's own silences never trip it, while a
# target that has actually gone idle and stopped writing reads idle within
# a few seconds, not "forever".
BUSY_STALE_S = 3.0
# THE RECOVERABLE LATCH's bound. `handoff --partial` has no retry today: the
# `awaiting_boundary is None` check exists so ONE crossing produces ONE
# typed command, and the only thing that ever clears it is a REAL boundary
# advance seen on disk — so a `handoff --partial` that gets queued and
# swallowed (the same busy-swallow risk as /clear and /sherlock, and a
# forced low --threshold makes it fire repeatedly, which is exactly the
# condition that makes a swallow likely) leaves the latch set for the rest
# of the stage: no boundary ever comes, and the run reaches STAGE_TIMEOUT
# looking like the arm stalled when what actually happened is the driver's
# own nudge was queued and dropped.
#
# HANDOFF_RETRY_WINDOW_S = 45.0: the failing free-lane run's own cycles were
# 3-4 calls over roughly 36 seconds (checkpoint.json advances as a TOOL
# CALL, and the model keeps generating afterward) — 45s is ~1.25x that
# measured cycle, enough margin for one real cycle to land before giving up,
# not so long that a genuinely stalled target burns the whole stage budget
# retrying instead of reaching STAGE_TIMEOUT in a reasonable time.
# HANDOFF_MAX_RETRIES = 3: bounded so a target that truly refuses to advance
# still falls through to the EXISTING STAGE_TIMEOUT path — never a new
# terminal, just a latch that unsticks itself instead of hanging forever on
# one lost keystroke.
HANDOFF_RETRY_WINDOW_S = 45.0
HANDOFF_MAX_RETRIES = 3

# ESC-TO-CANCEL, BOUNDED. Run 20260902T045011Z-v44 proved a bounded IDLE WAIT
# cannot be the whole fix: the target was 4m21s into a single turn and never
# went idle in the window, so `/clear` got typed while busy and queued —
# `clear_typed_while_busy` followed by `CLEAR_NOT_EFFECTIVE`. The TUI names
# its own escape hatch on that exact busy footer: "esc to cancel". Sending a
# bare ESC (never followed by \r — that would submit an empty line, not
# cancel) asks qwen-code to abandon the in-flight turn and return to an idle
# prompt, which a bounded wait alone can never do for a multi-minute turn.
#
# WHY THIS IS SAFE ONLY AT /clear, NEVER AT `handoff --partial`: the driver
# only reaches the /clear call AFTER `boundary_seq`/`stage` has already
# advanced on disk (see the real-advance test above `wait_before("/clear")`'s
# call site) — the checkpoint is durable at that moment, which is the entire
# reason the boundary gate exists, so cancelling whatever the target is
# generating past that point discards only a continuation the next session
# will redo from the checkpoint anyway. `handoff --partial` is typed at a
# TOKEN-THRESHOLD crossing, before any boundary has advanced — cancelling
# there could discard in-progress work the checkpoint has not captured yet,
# so `wait_before` for that call site must NOT be given `allow_cancel=True`.
#
# BOUNDED, NOT LOOPED: one ESC may not register (a repaint mid-flight, or the
# TUI needs a moment to unwind the turn), so a second attempt is allowed, but
# never more — `ESCAPE_ATTEMPTS` attempts, each followed by its own bounded
# idle check, and the very first idle-idle check is the same `wait_idle` the
# driver already uses, unchanged, so a target that is ALREADY idle never sees
# an ESC at all. If the target is still busy after every attempt, the
# behaviour falls back to exactly today's: type anyway, log
# `clear_typed_while_busy`, never worse than before this fix.
ESCAPE_ATTEMPTS = 2
ESCAPE_VERIFY_WAIT_S = 8.0


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
        # `mark` is a single needle (HANDOFF_MARK, CLEAR_REFUSAL) or a tuple of
        # needles (TARGET_REFUSAL_NEEDLES) that all mean the same thing — one
        # latch, several literal spellings of the fact it watches for.
        self.mark = mark
        self.marks = (mark,) if isinstance(mark, str) else tuple(mark)
        self.overlap = max([0] + [len(m) - 1 for m in self.marks])
        self.reset()

    def reset(self):
        self._tail = ""
        self._want = 0        # chars still needed to finish the last context
        self.hits = []
        self.count = 0
        self.chars = 0        # how much text flowed through, for the record
        # WHEN the mark last ARRIVED, not whether it is on screen. For the
        # busy question that distinction is a trap (a thinking model stops
        # repainting and arrival goes quiet while the screen is still busy —
        # the bug `screen_busy` exists to avoid). For a mark that repaints on
        # a fixed tick and stops only when its phase ENDS, arrival recency is
        # exactly the right signal: see INIT_MARKER.
        self.last_at = 0.0

    @property
    def seen(self):
        return self.count > 0

    @property
    def text(self):
        """The on-screen text of the first match, for the event that reports it."""
        return self.hits[0] if self.hits else ""

    def feed(self, text):
        self.chars += len(text)
        if self._want and self.hits:
            take = text[:self._want]
            self.hits[-1] += take
            self._want -= len(take)
        hay = self._tail + text
        at = 0
        while True:
            i = -1
            for m in self.marks:
                j = hay.find(m, at)
                if j >= 0 and (i < 0 or j < i):
                    i = j
            if i < 0:
                break
            self.count += 1
            if len(self.hits) < self.MAX_HITS:
                ctx = hay[i:i + self.CONTEXT]
                self.hits.append(ctx)
                self._want = self.CONTEXT - len(ctx)
            at = i + 1
            self.last_at = time.time()
        self._tail = hay[-self.overlap:] if self.overlap else ""


class AllOfWatch(object):
    """Fires only once EVERY one of several literal fragments has appeared on
    screen (not necessarily adjacent, not necessarily in that order) — for the
    one case where a single fixed needle is not safe: a fragment an honest
    agent could plausibly narrate or grep on its own, matched against a target
    that is refusing input for real. Requiring several distinct fragments of
    the CLI's actual sentence makes an incidental echo of just one of them
    harmless, because no realistic single utterance reproduces all of them.

    Same interface as MarkWatch (`feed`, `.seen`, `.text`) so it drops into
    `Session.watches` and the nudge branch without either caring which one it
    is holding.
    """

    def __init__(self, marks):
        self.watches = [MarkWatch(m) for m in marks]

    def feed(self, text):
        for w in self.watches:
            w.feed(text)

    @property
    def seen(self):
        return all(w.seen for w in self.watches)

    @property
    def text(self):
        for w in self.watches:
            if w.hits:
                return w.hits[0]
        return ""


# NOTE ON HISTORY: an earlier `RecencyWatch` class lived here — "how long
# since this mark last appeared", fed only the bytes each `pump()` call had
# just read off the pty. It answered "has NEW OUTPUT containing the marker
# arrived recently", not "is the marker on screen right now" — the two agree
# only while the target keeps repainting on every single poll tick, and
# diverge (false idle) the instant a poll's chunk happens to miss a marker
# that is still sitting on screen. Replaced by `screen_busy` below, which
# re-reads the transcript FILE's current tail on every check instead of
# trusting incremental arrival — see `screen_busy` and `Session.wait_idle`.


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


def last_frame_busy(text):
    """Busy verdict from the LAST footer qwen-code painted, or None.

    None means "this window has no footer to read" — a fixture TUI that does
    not paint one, or a transcript tail that happens to start mid-frame — and
    the caller then falls back to the past-tense marker scan, which is the
    conservative direction (it errs toward busy, i.e. toward waiting).

    A queued-input indicator (`⏳ N queued`) printed AFTER the last footer is
    also busy: input is sitting unexecuted in the TUI's queue, which is the
    precise condition this whole gate exists to avoid typing into.
    """
    idx = text.rfind(FOOTER_ANCHOR)
    if idx < 0:
        return None
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    if end < 0:
        end = len(text)
    line = text[start:end]
    if any(h in line for h in FOOTER_BUSY_HINTS):
        return True
    if QUEUED_MARKER in text[end:]:
        return True
    return False


def screen_busy(path, tail_bytes=BUSY_TAIL_BYTES, markers=BUSY_MARKERS,
                 stale_s=BUSY_STALE_S):
    """Is the target busy RIGHT NOW, judged from CURRENT SCREEN CONTENT?

    THE BUG THIS REPLACES: the previous detector (`RecencyWatch`, still
    above for the git history but no longer wired to `wait_idle`) fed only
    the bytes each `pump()` call had just read, and aged its "last seen"
    clock whenever a poll's chunk did not happen to contain the marker. That
    measures NEW OUTPUT ARRIVAL, not idleness — a target that is genuinely
    busy but not repainting on a given 0.2s tick (measured against the real
    run 20260902T053801Z-v44: the byte gap between consecutive `esc to
    cancel` repaints in that transcript has a p95 of 1613 B, not a fixed
    cadence) reads as idle the instant one poll's chunk misses it, even
    though the busy footer is still sitting on screen. Run
    20260902T053801Z-v44 failed exactly this way: no `clear_typed_while_busy`
    and no ESC note in the events log, i.e. `wait_idle` decided the target
    was idle without ever waiting or cancelling, while the terminal was
    showing an in-flight spinner AND a queued `/clear`/`/sherlock`.

    THE FIX: re-read the CURRENT tail of the transcript FILE on every poll
    — not the incremental chunk just read, the last `tail_bytes` of what is
    on disk right now — strip ANSI, and test it for either busy marker. A
    marker byte written once stays in that window for as long as the file
    doesn't grow past it by `tail_bytes` — so a marker from a genuine repaint
    a moment ago is still visible even on a poll whose own fresh chunk had
    none, closing exactly the gap above.

    RECENCY BOUND, TWO PARTS. (1) Reading is always anchored to the file's
    CURRENT end (a fresh open+seek(-tail_bytes) every call, never a fixed
    offset), so the window slides forward with the file — a marker printed
    minutes ago is only still "seen" if the transcript has grown by less
    than `tail_bytes` since. (2) That alone is not enough for a target that
    has gone genuinely idle and all but stops writing to the pty — the
    window would then stay frozen on a stale marker indefinitely, so the
    verdict also requires the file's own mtime (the wall-clock moment its
    last byte landed) to be within `stale_s` — see `BUSY_STALE_S`. Either
    the file keeps growing past the marker, or it goes quiet and the mtime
    check catches it: neither path lets a stale marker read as busy forever
    the way a latch (`MarkWatch.seen`) would.
    """
    try:
        st = os.stat(path)
    except (OSError, IOError):
        return False
    if time.time() - st.st_mtime > stale_s:
        return False
    size = st.st_size
    start = max(0, size - int(tail_bytes))
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            blk = fh.read()
    except (OSError, IOError):
        return False
    text = strip(blk.decode("utf-8", "replace"))
    # CURRENT FRAME FIRST (see FOOTER_ANCHOR): the last footer describes now.
    # The marker scan below is only a fallback for a window with no footer in
    # it, because on its own it answers about the past and reads false-busy.
    verdict = last_frame_busy(text)
    if verdict is not None:
        return verdict
    return any(m in text for m in markers)


def inflight_tool(path, tail_bytes=TOOL_TAIL_BYTES):
    """The last shell command visible on screen, or "" if none is.

    Evidence, not a gate: it turns «the target stopped» into «the target is
    waiting on THIS», which is the difference between reading a verdict and
    reading a 190 MB transcript. The command is taken from the LAST banner in
    the window, since an earlier completed call is not what the target is
    waiting on now.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - int(tail_bytes)))
            text = strip(fh.read().decode("utf-8", "replace"))
    except (OSError, IOError):
        return ""
    at = text.rfind(TOOL_CALL_MARK)
    if at < 0:
        return ""
    start = at + len(TOOL_CALL_MARK)
    # The banner is JSON-ish but WRAPPED across screen rows, so it cannot be
    # parsed — read to the closing quote of the command value and squeeze the
    # wrap whitespace out of what is left.
    end = text.find('","', start)
    if end < 0:
        end = min(len(text), start + 400)
    return " ".join(text[start:end].split())[:300]


def ledger_quiet_s(ledger_path, since=None):
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

    THE "NO LEDGER" CASE WAS WRONG, and it cost a paid run. This used to
    answer None whenever the ledger did not exist yet, documented as
    «nothing has started, which is not a stall». Paid run
    20260902T171049Z-v44 is the counter-example: its `/sherlock` was
    rejected during startup, no request was EVER sent, so no ledger file was
    ever created — and a None here meant the nudge branch could not fire at
    all. It sat silent for 28 minutes and would have run its full 21,600s
    timeout. Nothing having started IS the stall, and the most common one:
    it is the only window in which the target has produced no evidence at
    all. `since` (the moment the first prompt was typed) gives that window a
    clock, so quiet is measured from the run's own start until the first
    call replaces it with the ledger's mtime.

    None now means only «no clock available at all» — no ledger and no
    `since` — which a caller reads as "do not judge".
    """
    if ledger_path:
        try:
            return max(0.0, time.time() - os.path.getmtime(ledger_path))
        except OSError:
            pass
    if since is not None:
        return max(0.0, time.time() - since)
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
    rows = fresh_rows_after(path, after_ms)
    return rows[0] if rows else None


def fresh_rows_after(path, after_ms):
    """Every completed CALL row logged after `after_ms`, oldest first.

    Same schema tolerance as `first_fresh_after` (which this now implements):
    refusal rows have no `kind == "call"` and are skipped, rows without
    `ts_ms` are pre-v44 and ignored. Returning the whole window — not just
    the first row — is what lets the /clear-verification check judge the
    WINDOW instead of a single race-prone row: on 20260902T014942Z-v44 the
    first call after the reseed was the tail of the pre-clear conversation
    still in flight (2 seconds later, 29 messages), with the real 2-message
    fresh-session call arriving later in the same window.
    """
    out = []
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
                    out.append(row)
    except OSError:
        return out
    return out


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
        # ALWAYS ON, NEVER RESET. Unlike `refusal` (scoped to one /clear and
        # reset at every boundary) this watches the whole run: a target that
        # has hit a process-lifetime limit like --max-session-turns does not
        # recover, so resetting this per boundary would let the driver keep
        # nudging a corpse instead of naming what actually happened once.
        self.target_refusal = AllOfWatch(TARGET_REFUSAL_NEEDLES)
        # NEVER RESET, and read only through `wait_ready`: this one is asked
        # «when did the init spinner last tick», so a per-stage reset would
        # throw away the only timestamp that answers it.
        self.init = MarkWatch(INIT_MARKER)
        self.watches = (self.handoff, self.refusal, self.target_refusal,
                        self.init)
        # Where this stage's output starts in the transcript, so the second
        # witness reads THIS stage and not the whole run.
        self.stage_offset = 0
        self.stage_bytes = 0
        # CURRENT-STATE BUSY CLOCK, sourced from `screen_busy` (a fresh read
        # of the transcript file's own tail), not from incremental byte
        # arrival. 0.0 == "never seen busy". Updated by `_mark_busy` from two
        # places: every `pump()` tick (so a target that goes busy WITHOUT
        # producing new pty bytes on a given tick is still caught — see
        # `screen_busy`'s docstring) and every `wait_idle` check (so the
        # very moment being judged is itself fresh, never stale). Keeping
        # this a timestamp rather than re-scanning history on every call is
        # what lets a session that has never looked busy return from
        # `wait_idle` immediately, the same performance the old (buggy)
        # `RecencyWatch` fast path had — but here the timestamp only ever
        # advances off a real tail-of-file read, never off "did this one
        # chunk happen to carry the marker".
        self.busy_last_seen = 0.0

    def _mark_busy(self):
        """Read the transcript's CURRENT tail; if busy, stamp `busy_last_seen`.

        Returns the busy bool. Cheap (one seek + a `BUSY_TAIL_BYTES` read),
        safe to call on every pump tick and every wait_idle poll.
        """
        busy = screen_busy(self.transcript_path)
        if busy:
            self.busy_last_seen = time.time()
        return busy

    def idle_for(self):
        """Seconds since the screen last looked busy, or +inf if never.

        Always starts with a FRESH read (`_mark_busy`) so the answer is
        never older than this call — the fast path in `wait_idle` relies on
        that freshness, not on a possibly-stale timestamp from a while ago.
        """
        if self._mark_busy():
            return 0.0
        if not self.busy_last_seen:
            return float("inf")
        return time.time() - self.busy_last_seen

    def pump(self, seconds):
        """Read for `seconds`, appending to the transcript and feeding the latches."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.2)
            except (OSError, ValueError):
                return False
            if ready:
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
            # CONTINUOUS COVERAGE, not just when a chunk arrived: a target
            # that is busy but not repainting on this exact 0.2s tick (see
            # `screen_busy`) must still keep `busy_last_seen` fresh, so this
            # runs every tick, `ready` or not.
            self._mark_busy()
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

    def wait_idle(self, bound_s, settle_s=1.0, poll_s=0.2):
        """Block until the target has looked idle for `settle_s` seconds, or
        `bound_s` total has elapsed — whichever comes first.

        BOUNDED, NEVER A NEW TERMINAL. Returns True if the screen went idle,
        False if the bound expired — the caller's fallback on False is to type
        anyway (today's behaviour), so this can only make a `/clear` land
        better than before, never worse.

        JUDGED FROM CURRENT SCREEN CONTENT, not from output arrival:
        `idle_for()` is backed by `screen_busy`, a fresh read of the
        transcript file's own tail on every check, kept warm every 0.2s by
        `pump()` even between `wait_idle` calls — never a latch, never
        "did the bytes *this* poll happened to read carry the marker". See
        `screen_busy`'s docstring for why the previous arrival-based
        detector (feeding only each poll's own chunk) produced a false idle
        on run 20260902T053801Z-v44: it aged `last_seen` back to idle the
        instant one 0.2s tick's chunk missed the marker, even though the
        marker was still sitting on screen a few hundred bytes back in the
        file. `idle_for() >= settle_s` requires the FULL settle window to
        read busy-free, exactly as before.
        """
        # FAST PATH: a session that has never looked busy (or has not
        # looked busy for a full settle period already) is idle right now —
        # answer without spending a poll's wall time on it. Safe because
        # `idle_for()` itself starts with a fresh tail read, not a stale
        # timestamp — see `idle_for`'s docstring.
        if self.idle_for() >= settle_s:
            return True
        deadline = time.time() + bound_s
        while True:
            remaining = deadline - time.time()
            self.pump(poll_s if remaining > 0 else 0.0)
            if self.idle_for() >= settle_s:
                return True
            if time.time() >= deadline:
                return False

    def type(self, text, settle=1.5):
        """Type a line the way a human does: the text, then Enter."""
        os.write(self.fd, text.encode("utf-8"))
        time.sleep(0.35)
        os.write(self.fd, b"\r")
        self.pump(settle)

    def wait_ready(self, bound_s=INIT_WAIT_S, quiet_s=INIT_QUIET_S,
                   poll_s=0.25):
        """Block until the target has FINISHED starting up, bounded.

        Ready means two things at once: the init spinner has not ticked for
        `quiet_s` (or never ticked at all — a fast start, or a stand-in that
        does not print one), AND the screen is not busy by the footer rule.
        Both are required: init looks idle to every busy marker, and a target
        that has gone straight from init into a turn is not ready for typed
        input either.

        Returns True if ready, False if `bound_s` ran out — the caller then
        decides, and the run still reaches a terminal rather than hanging,
        which is the whole lesson of paid run 20260902T171049Z-v44.
        """
        deadline = time.time() + bound_s
        while True:
            self.pump(poll_s)
            ticked = self.init.last_at
            quiet = (not ticked) or (time.time() - ticked >= quiet_s)
            if quiet and not self._mark_busy():
                return True
            if time.time() >= deadline:
                return False

    def type_skill(self, skill_command, settle, note,
                   attempts=SKILL_TYPE_ATTEMPTS):
        """Type the skill command and PROVE it was accepted, or say it wasn't.

        The rejection is read from the transcript FILE, from the byte offset
        that was current before the command was typed — so the needle is
        scoped to this command's own output and cannot be tripped by a corpus
        that happens to contain the phrase, nor missed because a ring buffer
        had already dropped it.

        Returns True once the target accepts the command. Returns False after
        `attempts` rejections, each one preceded by a fresh readiness wait —
        which is the honest answer when the skill genuinely is not installed
        (the v43 `settings.skills.directories` failure), and is reported as a
        terminal instead of being run around for six hours.
        """
        needle = SKILL_REJECTED_FMT % skill_command
        for attempt in range(1, attempts + 1):
            try:
                self.transcript.flush()
                before = self.transcript.tell()
            except (OSError, IOError, ValueError):
                before = 0
            self.type(skill_command, settle=settle)
            note("typed", skill_command)
            # POLL, DON'T SLEEP. A fixed extra settle here is pure latency on
            # the happy path — and this driver's startup latency is inside
            # every test's own timeout arithmetic, so a blind second is not
            # free. The rejection lands in the same instant the command is
            # rejected (the real target printed it immediately, thirteen
            # times), so read the disk on a short tick and stop as soon as
            # there is an answer; a clean screen costs one tick, not one
            # second.
            fresh = ""
            rejected = False
            probe_deadline = time.time() + SKILL_REJECT_PROBE_S
            while True:
                try:
                    with open(self.transcript_path, "rb") as fh:
                        fh.seek(before)
                        fresh = strip(fh.read().decode("utf-8", "replace"))
                except (OSError, IOError):
                    fresh = ""
                if needle in fresh:
                    rejected = True
                    break
                if time.time() >= probe_deadline:
                    break
                self.pump(0.2)
            if not rejected:
                return True
            note("skill_command_rejected",
                 "attempt %d/%d — the target answered «%s»; it was still "
                 "starting up, so waiting for readiness and retyping"
                 % (attempt, attempts, needle))
            self.wait_ready()
        return False

    def escape(self, settle=0.5):
        """Send a bare ESC — cancel the in-flight turn, the way the TUI's own
        "esc to cancel" footer names it. NEVER followed by \\r: this is not a
        line being submitted, and a trailing Enter risks accepting whatever
        the TUI puts on screen once the turn unwinds instead of a plain
        cancel. Written the same way `type()` writes to the pty."""
        os.write(self.fd, b"\x1b")
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
          handoff_grace_s=8.0, threshold=0, reseed_command="",
          clear_idle_wait_s=20.0, clear_idle_settle_s=1.0,
          handoff_retry_window_s=HANDOFF_RETRY_WINDOW_S,
          handoff_max_retries=HANDOFF_MAX_RETRIES):
    ses = Session(argv, cwd, dict(os.environ), transcript)
    events = []
    # THE LATCH: holds the boundary_seq the driver was at when it crossed the
    # threshold and typed `handoff --partial`, or None when no crossing is
    # pending. Its whole job is «one crossing produces one handoff» — without
    # it, every poll after the crossing would type another `handoff --partial`
    # until the boundary finally moved, and a slow arm would be typed at
    # dozens of times for one checkpoint.
    awaiting_boundary = None
    # THE RECOVERABLE LATCH's own state: when `awaiting_boundary` was set
    # (so a bounded window can be measured against it) and how many times
    # it has already been retried this crossing. Both reset alongside
    # `awaiting_boundary` at the ONE place that proves a real advance —
    # never a second clear path.
    awaiting_since = None
    handoff_retries = 0
    handoff_gave_up = False
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

    def wait_before(step_label, allow_cancel=False):
        """WAIT FOR IDLE BEFORE TYPING — see BUSY_MARKER above. A target that
        is still generating QUEUES the keystroke instead of acting on it:
        that is what turned 120 clears into 120 silent no-ops in stage
        `repair` on run 20260902T021751Z-v44, and the SAME swallow one step
        later is worse, not milder — if `/clear` lands but the retyped
        `/sherlock` is what gets queued instead, the reseed line that
        follows lands in a bare session with no skill loaded and nothing on
        disk looks wrong. Reused for all four risky keystrokes: /clear, the
        skill retype, the reseed line, and `handoff --partial` at a
        threshold crossing — one bounded wait, never a second
        implementation. The bound is finite and the fallback on expiry is
        to type anyway — never a new terminal, only a note — so this can
        only improve on today's behaviour.

        `allow_cancel`: after the idle wait times out, send a bounded number
        of ESC keystrokes (see ESCAPE_ATTEMPTS above `handoff --partial` and
        `/clear`/`/sherlock`/the reseed line — never the risky
        `handoff --partial` call site: cancelling BEFORE the boundary is
        durable is not safe, see the comment above ESCAPE_ATTEMPTS.
        """
        idle = ses.wait_idle(clear_idle_wait_s, settle_s=clear_idle_settle_s)
        if idle:
            return
        if allow_cancel:
            for attempt in range(1, ESCAPE_ATTEMPTS + 1):
                ses.escape()
                idle = ses.wait_idle(ESCAPE_VERIFY_WAIT_S,
                                      settle_s=clear_idle_settle_s)
                note("esc_sent",
                     "attempt %d/%d before %s — idle after ESC: %s"
                     % (attempt, ESCAPE_ATTEMPTS, step_label, idle))
                if idle:
                    break
            if idle:
                return
        note("clear_typed_while_busy",
             "%s — target never looked idle within %.0fs before %s; "
             "typing anyway (today's behaviour, never worse)"
             % (seen, clear_idle_wait_s, step_label))

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
        # WAIT FOR REAL READINESS, never a blind settle. See INIT_MARKER: the
        # paid run typed into «Initializing...» and ran a whole session with
        # no skill loaded and no request sent.
        if not ses.wait_ready():
            note("init_wait_timeout",
                 "target never looked ready within %.0fs; typing anyway "
                 "rather than hanging" % INIT_WAIT_S)
        if not ses.type_skill(skill_command, settle_s, note):
            note("SKILL_NOT_LOADED",
                 "the target rejected %s on every one of %d attempts — the "
                 "skill is not registered, so the session would run with no "
                 "skill at all (paid run 20260902T171049Z-v44 did exactly "
                 "that for 28 minutes and never sent a request)"
                 % (skill_command, SKILL_TYPE_ATTEMPTS))
            return 11, events
        ses.type(first_prompt, settle=2.0)
        note("typed", "the task prompt")
        # WHEN THE RUN ACTUALLY BEGAN, for the nudge. Until the first upstream
        # call there is no ledger to read, and `ledger_quiet_s` used to answer
        # None there and call it "not a stall" — so the paid run's 28 silent
        # minutes could not have been nudged, and would have burned the whole
        # 21,600s timeout. Quiet is measured from this moment instead.
        started_at = time.time()
        while True:
            deadline = time.time() + stage_budget_s
            nudges = 0
            # One note per blocked window, not one per poll: a mutable cell
            # because the branch that sets it sits inside the loop.
            tool_block_noted = [False]
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
                    awaiting_since = None
                    handoff_retries = 0
                    handoff_gave_up = False
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
                    wait_before("handoff --partial %s" % seen)
                    ses.type("handoff --partial %s" % seen, settle=2.0)
                    awaiting_boundary = now_boundary
                    awaiting_since = time.time()
                    continue
                # THE RECOVERABLE LATCH. A queued/swallowed `handoff
                # --partial` has no other way back: nothing else ever
                # clears `awaiting_boundary` except a real advance, and
                # a target that swallowed the keystroke will never produce
                # one. Bounded (HANDOFF_RETRY_WINDOW_S, see the constant's
                # comment) and capped (HANDOFF_MAX_RETRIES) — past the cap
                # this falls through to the EXISTING STAGE_TIMEOUT path
                # rather than looping or inventing a new terminal.
                elif (awaiting_boundary is not None and awaiting_since is not None
                        and time.time() - awaiting_since >= handoff_retry_window_s):
                    if handoff_retries < handoff_max_retries:
                        handoff_retries += 1
                        note("handoff_retry",
                             "%s — no boundary advance %ds after handoff "
                             "--partial (attempt %d/%d); it may have been "
                             "queued and swallowed, retyping"
                             % (seen, int(time.time() - awaiting_since),
                                handoff_retries, handoff_max_retries))
                        awaiting_boundary = None
                        awaiting_since = None
                        continue
                    elif not handoff_gave_up:
                        handoff_gave_up = True
                        note("handoff_retry_exhausted",
                             "%s — %d handoff retries produced no boundary "
                             "advance; falling through to the stage budget"
                             % (seen, handoff_max_retries))
                # A STALL IS NOT A LONG STAGE, and the ledger can tell them
                # apart. Bounded on purpose: a target that answers a nudge with
                # nothing is broken, and nudging it forever would hide that
                # behind an hour of silence.
                if idle_nudge_s > 0:
                    quiet = ledger_quiet_s(ledger_path, since=started_at)
                    since_nudge = (None if last_nudge is None
                                   else time.time() - last_nudge)
                    if (quiet is not None and quiet >= idle_nudge_s
                            and (since_nudge is None
                                 or since_nudge >= idle_nudge_s)):
                        if since_nudge is not None:
                            # A nudge was sent last pass and the ledger is
                            # STILL quiet: it did not help. Say what the
                            # screen said, so the artifact answers the
                            # question instead of sending the reader to a
                            # 190 MB transcript for it.
                            note("nudge_ineffective",
                                 "%s — nudge %d/%d produced no upstream call "
                                 "within %d s: %s"
                                 % (seen, nudges, max_nudges, int(idle_nudge_s),
                                    ses.target_refusal.text
                                    or "no reason visible on screen"))
                        # A REFUSING TARGET IS NOT A SLOW ONE, and nudging it
                        # is the one thing that cannot help. On
                        # 20260901T002401Z-v43 all three nudges were refused
                        # by the CLI and the driver still reported
                        # STAGE_STALLED after 1,204 s, sending everyone
                        # downstream to the wrong cause.
                        if ses.target_refusal.seen:
                            note("TARGET_REFUSED",
                                 "%s — the target refused input: %s"
                                 % (seen, ses.target_refusal.text))
                            return 10, events
                        # ASK THE SCREEN BEFORE NAMING THE CAUSE. See
                        # TOOL_CALL_MARK: a busy screen with a quiet ledger is
                        # a target waiting on a tool, and nudging it only
                        # queues the keystroke.
                        if ses.idle_for() <= 0.0:
                            waiting_on = inflight_tool(ses.transcript_path)
                            if quiet >= idle_nudge_s * (max_nudges + 1):
                                note("TOOL_CALL_BLOCKED",
                                     "%s — no upstream call for %d s, but the "
                                     "screen is BUSY: the target is waiting on "
                                     "a tool that has not returned%s"
                                     % (seen, int(quiet),
                                        (" — " + waiting_on) if waiting_on
                                        else " (no command visible on screen)"))
                                return 12, events
                            if not tool_block_noted[0]:
                                tool_block_noted[0] = True
                                note("blocked_in_tool",
                                     "%s — ledger quiet %d s while the screen "
                                     "is busy; NOT nudging (a keystroke into a "
                                     "busy TUI only queues)%s"
                                     % (seen, int(quiet),
                                        (" — " + waiting_on) if waiting_on
                                        else ""))
                            continue
                        if nudges >= max_nudges:
                            note("STAGE_STALLED",
                                 "no upstream call for %d s at stage %s after %d "
                                 "nudge(s) — the screen is idle and the target "
                                 "stopped; it will not restart"
                                 % (int(quiet), seen, nudges))
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
            wait_before("/clear", allow_cancel=True)
            reseed_at_ms = int(time.time() * 1000)
            ses.type("/clear", settle=settle_s)
            if ses.refusal.seen:
                note("CLEAR_REFUSED", "a background task was still alive")
                return 6, events
            wait_before(skill_command)
            ses.type(skill_command, settle=settle_s)
            line = reseed % {"work": os.path.abspath(work), "stage": seen}
            if reseed_command:
                # BUILT AT RESEED TIME, so it carries THIS boundary's numbers.
                # A static template cannot: v43's was byte-identical at all
                # ten reseeds and told the model nothing it did not already
                # assume. Single line ONLY — the driver types this straight
                # into a TUI, and a newline in it would submit early and
                # split the message, so only the first line is ever used.
                try:
                    out = subprocess.run(reseed_command, shell=True,
                                         capture_output=True, text=True,
                                         timeout=30)
                    if out.returncode == 0 and out.stdout.strip():
                        line = out.stdout.strip().splitlines()[0]
                    else:
                        note("reseed_command_failed",
                             "rc %s — falling back to the static template"
                             % out.returncode)
                except (OSError, subprocess.SubprocessError) as exc:
                    note("reseed_command_failed",
                         "%s — falling back to the static template" % exc)
            wait_before("the reseed line")
            ses.type(line, settle=2.0)
            note("reseeded", seen)
            # DO NOT BELIEVE YOUR OWN KEYSTROKES. The refusal needle above
            # catches a /clear that says no; it cannot catch one that says
            # nothing and keeps the conversation, which is exactly what run
            # 20260831T214240Z-v43 did 119 times out of 124. A fresh session
            # sends exactly two messages — system prompt and seed.
            #
            # Judge the WINDOW, not the first row after the reseed. The first
            # v44 acceptance run (20260902T014942Z-v44) proved the first-row
            # read is a false positive generator: the reseed was typed at
            # 01:54:58, and the first completed call after it (01:55:00,
            # messages_count=29) was the TAIL of the pre-clear conversation
            # still draining — not the new session. Earlier v43 forensics had
            # already measured this shape ("a race, 1-4 calls wide — the
            # first call of every cycle fires on /sherlock alone"). Separately,
            # with SHERLOCK_ALLOW_SUBAGENT=1 a child session opens its OWN
            # 2-message conversation with no reseed nearby, so
            # `messages_count == 2` alone never meant "the parent reset" —
            # only "some 2-message call showed up". So: poll for ANY
            # completed call inside the bounded window with messages_count
            # == 2. If one appears at all, the clear is verified. Only an
            # EMPTY window (or a window where every row is pre-v44 and
            # carries no messages_count) fails to confirm.
            #
            # This flips the residual risk to a false NEGATIVE — a
            # subagent's 2-message call could satisfy the check while the
            # parent conversation was never actually reset — but that is the
            # safe direction: it only restores the pre-v44 behaviour (wasted
            # budget on a stale conversation), never a false CLEAR_NOT_EFFECTIVE
            # that kills an honest run in five minutes. A genuinely inert
            # /clear (clear_noop) still fails: no row in the whole window
            # ever carries messages_count == 2, so the window expires empty
            # and rc 8 still fires.
            #
            # Token counts were considered and rejected as a STRENGTHENING
            # signal (e.g. "prompt_tokens must have dropped from its
            # pre-clear peak"): legitimate per-stage growth makes any
            # threshold an invented constant, and a subagent's own prompt
            # tokens are just as capable of dropping as a real reset's — it
            # would not close the false-negative gap, only add a new tunable
            # that could itself go stale and start flagging honest resets.
            if ledger_path:
                rows = []
                verify_end = time.time() + max(settle_s * 4, 60.0)
                while time.time() < verify_end:
                    if not ses.pump(2.0) and not ses.alive():
                        break
                    rows = fresh_rows_after(ledger_path, reseed_at_ms)
                    if any(r.get("messages_count") == 2 for r in rows):
                        break
                if not rows:
                    note("clear_unverified",
                         "no completed call after the reseed within the "
                         "verification window — cannot confirm the clear")
                else:
                    verified = next(
                        (r for r in rows if r.get("messages_count") == 2),
                        None)
                    if verified is not None:
                        note("clear_verified",
                             "%s — a fresh 2-message call appeared in the "
                             "verification window, prompt %s"
                             % (seen,
                                (verified.get("usage") or {}).get(
                                    "prompt_tokens")))
                    elif all(r.get("messages_count") is None for r in rows):
                        note("clear_unverified",
                             "every call after the reseed carries no "
                             "messages_count (pre-v44 proxy)")
                    else:
                        note("CLEAR_NOT_EFFECTIVE",
                             "no call in the verification window carried "
                             "messages_count == 2 (saw %s) — /clear did not "
                             "clear, and every later measurement would be "
                             "meaningless"
                             % [r.get("messages_count") for r in rows])
                        return 8, events
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
    ap.add_argument("--reseed-command", default="",
                    help="a shell command whose stdout is the reseed line — "
                         "built fresh at each boundary from the checkpoint, "
                         "so it carries THIS boundary's numbers rather than "
                         "the static --reseed template. Only its first "
                         "output line is used (a single line is typed into "
                         "the TUI); on a non-zero exit, empty stdout, or a "
                         "hang past 30s it falls back to --reseed.")
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
    ap.add_argument("--clear-idle-wait-s", type=float, default=20.0,
                    help="before typing /clear, wait up to this long for the "
                         "target to stop looking busy (\"esc to cancel\" gone "
                         "from the screen for --clear-idle-settle-s) — a busy "
                         "target QUEUES the keystroke instead of acting on "
                         "it. 0 disables the wait (types immediately, the "
                         "pre-v44 behaviour).")
    ap.add_argument("--clear-idle-settle-s", type=float, default=1.0,
                    help="how long the busy marker must have been absent "
                         "before the screen counts as idle")
    ap.add_argument("--handoff-retry-window-s", type=float,
                    default=HANDOFF_RETRY_WINDOW_S,
                    help="if no boundary advance follows a `handoff "
                         "--partial` within this long, assume it was queued "
                         "and swallowed and retype it — the failing "
                         "free-lane run's own cycles were 3-4 calls over "
                         "~36s, so the default is ~1.25x that")
    ap.add_argument("--handoff-max-retries", type=int,
                    default=HANDOFF_MAX_RETRIES,
                    help="cap on handoff --partial retries per crossing — "
                         "past this, fall through to the existing "
                         "STAGE_TIMEOUT path instead of retrying forever")
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
                       args.handoff_grace_s, args.threshold,
                       args.reseed_command, args.clear_idle_wait_s,
                       args.clear_idle_settle_s, args.handoff_retry_window_s,
                       args.handoff_max_retries)
    print("rc=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
