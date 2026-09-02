#!/usr/bin/env python3
"""A stand-in for interactive qwen: enough of the real thing to test the driver.

It reads typed lines from its tty, and it behaves like the target in the four
ways that matter: it only knows the stage from `work/checkpoint.json`, it prints
the handoff block, `/clear` forgets the loaded skill, and — in the modes the
tests ask for — it refuses `/clear`, or advances the stage without printing the
block, or dies. Operator time is expensive: the driver is proven here first.
"""
import json
import os
import subprocess
import sys
import threading
import time

WORK = os.environ["FAKE_WORK"]
CHECKPOINT = os.environ.get("FAKE_CHECKPOINT")     # checkpoint.py to call
MODE = os.environ.get("FAKE_MODE", "happy")
# THE v44 QUEUING BUG, reproduced. `busy_after_boundary`: for FAKE_BUSY_S
# seconds after every stage boundary, this stand-in (a) keeps repainting the
# real TUI's in-flight footer, and (b) SWALLOWS every typed line instead of
# acting on it — a keystroke typed into a busy qwen-code 0.22.0 queues
# («⏳ N queued») rather than executing, and 20260902T021751Z-v44 shows those
# queued clears never draining before the next boundary made the target busy
# again. A driver that types /clear the instant the boundary lands (the
# pre-fix behaviour) always loses its keystrokes here; one that waits for the
# footer to go quiet first (wait_idle) types after the window closes and gets
# through.
BUSY_S = float(os.environ.get("FAKE_BUSY_S", "0"))
_busy_until = [0.0]
_busy_lock = threading.Lock()


def _busy_footer():
    while True:
        time.sleep(0.2)
        with _busy_lock:
            busy = time.time() < _busy_until[0]
        if busy:
            say("  ⣋ Aligning the stars for optimal response "
                "(0s · esc to cancel)")


# THE FALSE-IDLE REGRESSION LOCK — reproduces the ACTUAL shape of run
# 20260902T053801Z-v44, confirmed by reading that transcript directly: the
# spinner line `esc to cancel` prints once when the turn starts and then
# goes QUIET (a model that is "thinking" — Compiling the 1s and 0s... —
# stops repainting that exact line for long stretches; measured against the
# real transcript, the max gap between consecutive "esc to cancel" repaints
# in one run was 1.7 MB / a long silent stretch), while the OTHER busy
# signal, the queued-input indicator (`⏳ N queued`), keeps repainting the
# whole time — `queued` was seen 261 times in that same transcript window,
# never once caught by the pre-fix detector because its `BUSY_MARKER` was
# `esc to cancel` ALONE (the pre-fix code explicitly rejected `queued` as a
# needle, conflating it with the always-present `Ctrl+Q to queue` hint — see
# the BUSY_MARKER comment above). A detector watching only `esc to cancel`
# goes idle the instant that one line stops repainting; a detector that also
# accepts `queued` (this fix's `BUSY_MARKERS`) does not.
REPAINT_S = float(os.environ.get("FAKE_REPAINT_S", "0.3"))
# THE PAID-RUN STARTUP RACE, reproduced. `slow_init`: for FAKE_INIT_S seconds
# after launch this stand-in behaves exactly as qwen-code 0.22.0 does while it
# is still loading — it repaints «Initializing...» with a footer that carries
# NO busy hint (no `esc to cancel`, no `Ctrl+Q to queue`, no `queued`), and it
# answers any typed slash command with `✕ Unknown command: <line>` because its
# skills are not registered yet.
#
# THIS IS THE SHAPE THAT KILLED THE PAID RUN 20260902T171049Z-v44. The driver
# typed `/sherlock` 7 seconds after launch, straight into that window; the
# transcript carries `✕ Unknown command: /sherlock` thirteen times; the task
# prompt went into a session with NO SKILL LOADED, no upstream call was ever
# made, and the run sat silent for 28 minutes until it was stopped. The free
# run an hour earlier typed at the same +7s and happened to win the race, so
# nothing in five prior runs had ever exposed it.
#
# Why a blind wait cannot pass this and an idle wait alone cannot either: the
# init footer looks IDLE by every busy marker the driver has. Readiness is a
# POSITIVE signal — the ready footer line — not the absence of a busy one.
INIT_S = float(os.environ.get("FAKE_INIT_S", "12"))


def _sparse_busy_footer():
    """Print the spinner ONCE, then only the queued-input indicator."""
    printed_spinner = False
    n = 0
    while True:
        time.sleep(REPAINT_S)
        with _busy_lock:
            busy = time.time() < _busy_until[0]
        if not busy:
            continue
        if not printed_spinner:
            say("  ⣋ Aligning the stars for optimal response "
                "(0s · esc to cancel)")
            printed_spinner = True
            continue
        n += 1
        say("  ⏳ %d queued" % n)


if MODE == "busy_after_boundary" and BUSY_S > 0:
    threading.Thread(target=_busy_footer, daemon=True).start()
if MODE == "sparse_busy_after_boundary" and BUSY_S > 0:
    threading.Thread(target=_sparse_busy_footer, daemon=True).start()
# THE THIRD SWALLOW WINDOW, one keystroke type further still: a queued
# `handoff --partial` at a threshold crossing. `handoff_busy_once` goes busy
# ONCE, right when the target starts "thinking" about the very first message
# of a stage (the same point threshold_then_happy holds a stage open at) —
# simulating the model beginning a real turn right as the driver's ledger
# check fires — then never again, so the RETRY (typed once the window has
# closed) is what proves the recoverable latch, not repeated luck.
_handoff_busy_armed = [False]
# A DETERMINISTIC alternative to the busy-window race above, for proving the
# RECOVERABLE LATCH itself rather than the timing of a real busy window:
# `handoff_drop_once` swallows the FIRST `handoff --partial ...` it ever
# receives — no matter when it arrives — and processes every one after that
# normally. This is exactly what a queued-and-never-delivered keystroke
# looks like from the fixture's side, without any real-time race against a
# footer thread, so the test is fast and does not depend on scheduler
# timing to land the swallow inside a window.
_handoff_dropped_once = [False]
# How much chatter the flood modes print before they hand off. The driver used
# to keep an 8 MB per-stage ring buffer, so «more than 8 MB» is the shape that
# made it answer `handoff_block_unknown` on the paid run.
FLOOD_MB = float(os.environ.get("FAKE_FLOOD_MB", "9"))
loaded = False

def say(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()

def flood(megabytes=None):
    """Print megabytes of plausible TUI chatter, the way a working stage does."""
    mb = FLOOD_MB if megabytes is None else megabytes
    line = (u"\x1b[2K\rЧитаю Security.evtx: " + u"событие " * 12) + u"\n"
    per = len(line.encode("utf-8"))
    for _ in range(int(mb * 1024 * 1024 / per) + 1):
        sys.stdout.write(line)
    sys.stdout.flush()


def stage():
    try:
        return json.load(open(os.path.join(WORK, "checkpoint.json")))["stage"]
    except Exception:
        return "triage"


def resolve_one_triage_row_or_finish():
    """Resolve exactly one open worklist row, or — if none are left — close
    `triage` for real (no --partial). Shared by `slow_but_honest` and
    `gate_tool_near_miss`: both need triage to eventually finish so the run
    can fall through to the generic draft/repair/done handling below rather
    than looping forever inside a stage that has nothing left to resolve.

    Fixture convention: an open row carries an EMPTY verdict column and the
    header is `#`-prefixed — inspect_worklists (checkpoint.py:55) counts any
    non-empty, non-"?"-prefixed verdict as resolved, so a placeholder like
    "D open" or an unprefixed header line would silently inflate `resolved`.
    """
    path = os.path.join(WORK, "worklist.tsv")
    lines = open(path, encoding="utf-8").read().splitlines()
    target = None
    for index, row in enumerate(lines[1:], start=1):
        cols = row.split("\t")
        if len(cols) >= 2 and not cols[1].strip():
            target = index
            lines[index] = cols[0] + "\tX closed"
            break
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    if target is not None:
        subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                        WORK, "--done", "triage", "--partial"],
                       capture_output=True, text=True)
        say("СТУПЕНЬ ЗАВЕРШЕНА: triage")
    else:
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff",
                              "--work", WORK, "--done", "triage"],
                             capture_output=True, text=True)
        say(out.stdout or ("handoff failed: " + out.stderr))

# THE v44-ROUND-2 QUEUING BUG, reproduced: a target that is BUSY FOR THE
# WHOLE TURN — no bounded idle wait ever reaches an idle prompt, because the
# turn genuinely never finishes inside any sane bound — and only returns to
# an idle prompt when it receives ESC (the TUI's own "esc to cancel"). Real
# transcript 20260902T045011Z-v44: 4m21s single turn, busy footer 54 times,
# "queued" 230 times, never idle. `busy_until_esc` reproduces this exactly:
# once the model has done its ONE piece of real work (the boundary that
# makes checkpoint.json durable — the fact the ESC-safety argument rests on)
# it stays busy FOREVER, ignoring every further keystroke, until the byte
# 0x1b arrives; only then does it drop back to idle and process input
# normally. Without the ESC-cancel fix a bounded wait_idle can never reach
# idle here — proving the regression the same way the real run failed.
FAKE_LEDGER = os.environ.get("FAKE_LEDGER", "")
_esc_cleared = [False]


def _append_ledger_row():
    """Simulate the upstream proxy's ledger: a call lands roughly every
    1.5s regardless of whether the screen looks busy or idle (a live model
    call is exactly what the busy footer is showing). Before a genuine
    /clear this reports a big, stale messages_count — the swallowed-clear
    conversation that never actually reset, matching the real run's shape
    (`messages` climbing instead of resetting). After a genuine /clear it
    reports messages_count == 2 — a fresh session — so the driver's window
    check can tell the two states apart exactly the way it tells them apart
    against a real target."""
    if not FAKE_LEDGER:
        return
    row = {"kind": "call", "ts_ms": int(time.time() * 1000),
           "usage": {"prompt_tokens": 2 if _esc_cleared[0] else 88000},
           "messages_count": 2 if _esc_cleared[0] else 57}
    try:
        with open(FAKE_LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
    except OSError:
        pass


def _ledger_writer():
    while True:
        _append_ledger_row()
        time.sleep(1.5)


if MODE == "busy_until_esc":
    import select
    import termios
    import tty

    if FAKE_LEDGER:
        threading.Thread(target=_ledger_writer, daemon=True).start()

    busy = [False]

    def _forever_busy_footer():
        while True:
            time.sleep(0.2)
            if busy[0]:
                say("  ⣋ Aligning the stars for optimal response "
                    "(0s · esc to cancel)")

    threading.Thread(target=_forever_busy_footer, daemon=True).start()

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    tty.setraw(fd)
    loaded = False
    buf = b""
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            chunk = os.read(fd, 1024)
            if not chunk:
                break
            if busy[0]:
                if b"\x1b" in chunk:
                    # THE FIX'S OWN NEEDLE: only an actual ESC byte, never a
                    # timeout, ever clears `busy` here — this is what proves
                    # a bounded idle-wait alone cannot pass this fixture.
                    busy[0] = False
                    say("\r\nCancelled.\r\n")
                # every other byte while busy is swallowed exactly like a
                # real qwen-code 0.22.0 session queuing a keystroke.
                continue
            buf += chunk
            while b"\r" in buf:
                raw_line, buf = buf.split(b"\r", 1)
                cmd = raw_line.decode("utf-8", "replace").strip()
                if not cmd:
                    continue
                if cmd == "/clear":
                    loaded = False
                    _esc_cleared[0] = True
                    say("\r\n\x1b[2JStarting a new session, resetting chat, "
                        "and clearing terminal.\r\n")
                    continue
                if cmd == "/sherlock":
                    loaded = True
                    say("\r\nBase directory for this skill: /fake/skills/v40\r\n")
                    continue
                if not loaded:
                    say("\r\nI have no skill loaded, so I do not know what "
                        "to do.\r\n")
                    continue
                if cmd.startswith("handoff --partial "):
                    continue
                # THE ONE REAL PIECE OF WORK: run checkpoint.py handoff
                # --partial for the current stage — making boundary_seq
                # durable on disk BEFORE going busy forever, which is
                # exactly the ordering the ESC-safety argument depends on —
                # then go busy forever, as if the model kept narrating after
                # its own tool call landed.
                st = stage()
                out = subprocess.run(
                    [sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                     "--done", st, "--partial"],
                    capture_output=True, text=True)
                say("\r\n" + (out.stdout or ("partial handoff failed: "
                                             + out.stderr)) + "\r\n")
                busy[0] = True
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        except termios.error:
            pass
    raise SystemExit(0)

if MODE == "slow_init":
    # Repaint the real TUI's init frame: banner, empty input box, a footer
    # with the ready arrow ABSENT and no busy hint anywhere.
    _init_until = time.time() + INIT_S

    def _init_footer():
        while time.time() < _init_until:
            say("  ⠋ Initializing...")
            say("  YOLO mode (shift + tab to cycle)")
            time.sleep(0.3)

    threading.Thread(target=_init_footer, daemon=True).start()
say("\x1b[32mfake qwen ready\x1b[0m")
while True:
    # readline(), not `for raw in sys.stdin`: iteration read-aheads a block, so
    # a line-at-a-time terminal dialogue would stall until the buffer filled.
    raw = sys.stdin.readline()
    if not raw:
        break
    line = raw.strip()
    if not line:
        continue
    if MODE == "no_skill":
        # THE v43 FAILURE, still possible and now NAMED. When
        # `settings.skills.directories` does not actually reach the target,
        # /sherlock is unknown for the whole life of the process — no amount
        # of waiting or retyping helps. Run 20260831's task-10 attempt hit
        # exactly this and was only caught by reading a transcript by hand.
        # The driver must reach a terminal that says so, not run a skill-less
        # session for hours.
        say("✕ Unknown command: %s" % line)
        continue
    if MODE == "slow_init" and time.time() < _init_until:
        # Skills are not registered yet, so a slash command is unknown and the
        # line is otherwise DISCARDED — no stage work, no upstream call. This
        # is verbatim what the real target printed thirteen times.
        say("✕ Unknown command: %s" % line)
        continue
    if MODE in ("busy_after_boundary", "sparse_busy_after_boundary", "handoff_busy_once"):
        with _busy_lock:
            busy = time.time() < _busy_until[0]
        if busy:
            # SWALLOWED, exactly like the real bug: the keystroke queues
            # instead of acting, and this stand-in never drains that queue —
            # the next boundary just opens a new busy window first.
            continue
    if MODE == "turns_exhausted":
        # THE BANNER THAT ACTUALLY ENDED THE PAID RUN, rendered the way the
        # real TUI actually wrapped it in 20260901T002401Z-v43's transcript —
        # not as one tidy line. Fix round 2: the real qwen-code box wraps the
        # sentence across two screen rows, with trailing padding and a
        # box-drawing border character on each, and the wrap point falls
        # INSIDE "update this limit" — "update" ends row 1, "this limit..."
        # starts row 2. A fixture that prints the banner as a single
        # unwrapped line proves nothing about needles that must survive real
        # rendering; this one reproduces the wrap exactly, so the test proves
        # the latch fires against what the screen actually looked like.
        say("● The session has reached the maximum number of turns: 600. "
            "Please update    │")
        say("  this limit in your setting.json file."
            "                                      █")
        continue
    if MODE == "grep_echo_only":
        # THE FALSE POSITIVE THIS FIXTURE PROVES CLOSED (fix round 1). This
        # echoes ONE bare fragment an honest RCA agent could plausibly put on
        # screen while investigating THIS VERY CASE — narrating the phrase,
        # or `command grep "maximum number of turns" transcript.log` over a
        # corpus that contains it — without ever printing the CLI's real
        # banner's distinguishing tail ("this limit in your setting.json").
        # It must NOT be read as a refusal: the target just
        # never advances, so the run should reach its normal STAGE_STALLED
        # terminal, not TARGET_REFUSED.
        say("note: transcript.log:41: target appears to have hit its "
            "maximum number of turns limit; continuing analysis.")
        continue
    if line == "/clear":
        if MODE == "clear_refused":
            say("Stop the current session's running background tasks before "
                "starting a new session.")
            continue
        if MODE == "clear_noop":
            # THE SILENT NO-OP, which is what actually happened. Run
            # 20260831T214240Z-v43's TUI accepted the keystroke, printed
            # nothing, and kept the conversation: `messages` climbed to 633
            # across 124 reseeds. It never refused — CLEAR_REFUSED's needle
            # matched 0 times in a 374 MB transcript — so a stand-in that
            # refuses cannot express this failure.
            continue
        loaded = False
        say("\x1b[2JStarting a new session, resetting chat, and clearing terminal.")
        if MODE in ("busy_after_boundary", "sparse_busy_after_boundary"):
            # THE SECOND SWALLOW WINDOW. A real qwen-code session does not
            # sit idle just because /clear landed — it starts doing
            # something right away (the fresh session's own first turn), so
            # the very next typed line (the /sherlock retype) is exposed to
            # the SAME queuing risk /clear was. A driver that waits once
            # before /clear but not again before /sherlock loses THIS
            # keystroke instead, and the reseed line that follows lands in a
            # bare session with no skill loaded.
            with _busy_lock:
                _busy_until[0] = time.time() + BUSY_S
        continue
    if line == "/sherlock":
        loaded = True
        say("Base directory for this skill: /fake/skills/v40")
        continue
    if loaded and line.startswith("handoff --partial "):
        if MODE == "handoff_drop_once" and not _handoff_dropped_once[0]:
            _handoff_dropped_once[0] = True
            # SWALLOWED — the fixture's stand-in for a keystroke that queued
            # and was never delivered. `awaiting_boundary` on the driver
            # side is now stuck until it recovers on its own.
            continue
        # The driver, having crossed the token threshold, types this the way a
        # human would ask the model to checkpoint mid-stage. An obedient target
        # runs checkpoint.py handoff --partial for real, advancing boundary_seq
        # on disk — the only fact the driver trusts.
        st = stage()
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                              WORK, "--done", st, "--partial"],
                             capture_output=True, text=True)
        say(out.stdout or ("partial handoff failed: " + out.stderr))
        continue
    if not loaded:
        say("I have no skill loaded, so I do not know what to do.")
        continue
    if MODE == "die":
        say("boom")
        raise SystemExit(1)
    if MODE in ("threshold_then_happy", "handoff_busy_once", "handoff_drop_once"):
        # A stage the driver must interrupt: the FIRST message of every stage
        # is answered with idle chatter and nothing else, so a driver that
        # never checks the ledger would sit here for the whole stage budget.
        # Only once `stage_partial` is true on disk — i.e. the driver's own
        # `handoff --partial` (handled generically above) already ran for
        # THIS stage — does the stage complete normally. This is the shape
        # the threshold branch exists for: crossing the token ceiling BEFORE
        # the arm would otherwise have finished on its own.
        try:
            row = json.load(open(os.path.join(WORK, "checkpoint.json"),
                                 encoding="utf-8"))
        except (OSError, ValueError):
            row = {}
        st = row.get("stage", "triage")
        if not row.get("stage_partial"):
            if MODE == "handoff_busy_once" and not _handoff_busy_armed[0]:
                _handoff_busy_armed[0] = True
                with _busy_lock:
                    _busy_until[0] = time.time() + BUSY_S
                threading.Thread(target=_busy_footer, daemon=True).start()
            say("Сейчас посмотрю логи...")
            continue
        if st == "triage":
            rows = open(os.path.join(WORK, "worklist.tsv"),
                       encoding="utf-8").read()
            open(os.path.join(WORK, "worklist.tsv"), "w",
                encoding="utf-8").write(rows.replace(
                    "\t?", "\tN a.log:1 «q» n=1 фон"))
        if st in ("draft", "repair"):
            open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
                "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                             WORK, "--done", st], capture_output=True,
                            text=True)
        say(out.stdout or ("handoff failed: " + out.stderr))
        continue
    if MODE == "partial_then_finish":
        # First turn of the triage stage: close HALF the rows and take a BATCH
        # boundary, exactly as a real arm does when its context is filling and
        # rows are still open. Then behave normally.
        import json as _json
        st = stage()
        if st == "triage" and not os.path.exists(os.path.join(WORK, ".half")):
            rows = open(os.path.join(WORK, "worklist.tsv"),
                        encoding="utf-8").read().splitlines()
            out = []
            closed = 0
            for line in rows:
                if line.startswith("#") or "\t?" not in line:
                    out.append(line); continue
                if closed < 3:
                    out.append(line.replace("\t?", "\tN a.log:1 «q» n=1 фон"))
                    closed += 1
                else:
                    out.append(line)
            open(os.path.join(WORK, "worklist.tsv"), "w",
                 encoding="utf-8").write("\n".join(out) + "\n")
            open(os.path.join(WORK, ".half"), "w").write("done")
            out = subprocess.run([sys.executable, CHECKPOINT, "handoff",
                                  "--work", WORK, "--done", "triage",
                                  "--partial"], capture_output=True, text=True)
            say(out.stdout or ("partial handoff failed: " + out.stderr))
            continue
        if st == "triage":
            rows = open(os.path.join(WORK, "worklist.tsv"),
                        encoding="utf-8").read()
            open(os.path.join(WORK, "worklist.tsv"), "w",
                 encoding="utf-8").write(
                rows.replace("\t?", "\tN a.log:1 «q» n=1 фон"))
        if st == "draft":
            open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
                "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                              WORK, "--done", st], capture_output=True,
                             text=True)
        say(out.stdout or ("handoff failed: " + out.stderr))
        continue
    if MODE in ("flood", "flood_silent", "flood_late"):
        # A LOUD STAGE. Print far more than any ring buffer would keep, then
        # take the boundary — and, depending on the mode, print the block
        # straight away, never, or a beat AFTER the checkpoint advanced (which
        # is what the real target did: the tool call lands on disk first and the
        # closing message is printed after it).
        st = stage()
        flood()
        if st == "draft":
            open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
                "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
        if st == "triage":
            rows = open(os.path.join(WORK, "worklist.tsv"),
                        encoding="utf-8").read()
            open(os.path.join(WORK, "worklist.tsv"), "w",
                 encoding="utf-8").write(
                rows.replace("\t?", "\tN a.log:1 «q» n=1 фон"))
        out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                              WORK, "--done", st], capture_output=True,
                             text=True)
        block = out.stdout or ("handoff failed: " + out.stderr)
        if MODE == "flood_silent":
            say("...done, and I printed megabytes but never the block.")
            continue
        if MODE == "flood_late":
            import time as _time
            _time.sleep(float(os.environ.get("FAKE_LATE_S", "2.0")))
        say(block)
        continue
    if MODE == "barren_boundaries":
        # THE FAILURE THE SUITE COULD NOT EXPRESS. Every existing mode does
        # the stage's real work in the same breath as the handoff, so progress
        # is a POSTCONDITION of taking a boundary and the livelock is
        # unreachable. This one takes the boundary and produces nothing —
        # exactly what 20260901T002401Z-v43 did 12 times.
        subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                        "--done", stage(), "--partial"],
                       capture_output=True, text=True)
        say("СТУПЕНЬ ЗАВЕРШЕНА: %s" % stage())
        continue
    if MODE == "slow_but_honest" and stage() == "triage":
        # THE CONTROL. Real progress, one worklist row per boundary — slower
        # than the gate's window but never zero. A gate that cannot tell this
        # from the mode above would have killed the paid run in its final four
        # minutes, which were the only productive minutes it had.
        resolve_one_triage_row_or_finish()
        continue
    if MODE == "gate_tool_near_miss" and stage() == "triage":
        # THE ESCAPE'S OWN CONTROL. Two — really three, since the first
        # boundary of a stage always passes on "first boundary" alone —
        # CONSECUTIVE boundaries that move none of resolved,
        # report_sections_written, report_bytes or a worklist seal, each one
        # running a gate tool (reportcheck, citecheck, statecheck — matching
        # GATE_TOOLS in interactive-drive.py). This is exactly the paid run
        # 20260901T002401Z-v43's final four minutes: probe.md -> probe4.md
        # tested against reportcheck.py/citecheck.py, which moves none of the
        # declared metrics. If boundary_advanced's gate-tool escape is not
        # wired to something real, this run trips NO_PROGRESS and dies at the
        # moment it starts to succeed — which is the failure Finding 1 fixed.
        counter_path = os.path.join(WORK, ".gate_tool_near_miss_calls")
        try:
            n = int(open(counter_path, encoding="utf-8").read().strip())
        except (OSError, ValueError):
            n = 0
        gate_tools = ("reportcheck", "citecheck", "statecheck")
        if n < len(gate_tools):
            subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                            WORK, "--done", "triage", "--partial",
                            "--gate-tool", gate_tools[n]],
                           capture_output=True, text=True)
            with open(counter_path, "w", encoding="utf-8") as fh:
                fh.write(str(n + 1))
            say("СТУПЕНЬ ЗАВЕРШЕНА: triage")
            continue
        resolve_one_triage_row_or_finish()
        continue
    if MODE == "gate_tool_bogus" and stage() == "triage":
        # THE ANTI-GAMING PROOF (fix round 2). `--gate-tool` is a flag the
        # model itself supplies under approval-mode yolo, so a name outside
        # GATE_TOOLS must NOT buy the escape. Every boundary here passes
        # `--gate-tool bogus` and changes nothing else — if checkpoint.py
        # filtered it in instead of dropping it, this would survive exactly
        # like gate_tool_near_miss above; it must NOT survive.
        subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work",
                        WORK, "--done", "triage", "--partial",
                        "--gate-tool", "bogus"],
                       capture_output=True, text=True)
        say("СТУПЕНЬ ЗАВЕРШЕНА: triage")
        continue
    if MODE == "stall":
        # Working, talkative, and never finishing a stage — the shape of a run
        # that has to be judged by its checkpoint and not by its chatter.
        say("Сейчас посмотрю логи...")
        continue
    st = stage()
    if MODE == "forged_advance":
        # Advance the stage by hand, exactly as something that is NOT
        # checkpoint.py handoff would: no handoff.txt, no block. This is the
        # only shape that may kill the run.
        import json as _json
        path = os.path.join(WORK, "checkpoint.json")
        row = _json.load(open(path))
        row["stage"] = {"triage": "draft", "draft": "repair"}.get(row["stage"], "done")
        open(path, "w").write(_json.dumps(row))
        say("...moved on without telling anyone.")
        continue
    if st == "draft":                       # a real report before handing off
        open(os.path.join(WORK, "report.md"), "w", encoding="utf-8").write(
            "# Отчёт Sherlock\n\n## Находки\n\n### Н-1 fake\n")
    if MODE == "silent_advance":
        subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                        "--done", st], capture_output=True)
        say("...done, but I said nothing useful.")
        continue
    if MODE == "sparse_busy_after_boundary":
        # ARM BEFORE THE BOUNDARY WRITE, not after. The driver detects
        # `stage_advanced` by polling checkpoint.json from a SEPARATE
        # process, so if the busy window only starts once that file is
        # written, the very first `wait_before` check can land before the
        # sparse footer thread has printed even once — genuinely idle, not a
        # test of the false-idle bug at all. Arming a few repaint ticks
        # early (`2 * REPAINT_S` sleep, footer already ticking during it)
        # guarantees at least one repaint has already landed, and that the
        # window is still open, by the time the driver notices the boundary
        # and calls `wait_before("/clear")`.
        with _busy_lock:
            _busy_until[0] = time.time() + REPAINT_S * 2 + BUSY_S
        time.sleep(REPAINT_S * 2)
    out = subprocess.run([sys.executable, CHECKPOINT, "handoff", "--work", WORK,
                          "--done", st], capture_output=True, text=True)
    say(out.stdout or ("handoff failed: " + out.stderr))
    if MODE == "busy_after_boundary":
        with _busy_lock:
            _busy_until[0] = time.time() + BUSY_S
