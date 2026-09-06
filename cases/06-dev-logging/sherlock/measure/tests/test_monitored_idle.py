#!/usr/bin/env python3
"""A stage boundary cannot cancel or overtake pending provider/tool work."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result
DRIVE = module('drive_idle_test', ROOT / 'measure/interactive-drive.py')
LIFE = module('life_idle_test', ROOT / 'eval/bench/lifecycle-supervisor.py')

class FakeSession:
    def __init__(self, idle):
        self.idle = iter(idle)
        self.polls = 0
        self.running = True
    def alive(self): return self.running
    def wait_idle(self, *args, **kwargs):
        self.polls += 1
        return next(self.idle)
    def pump(self, *args): pass
    def escape(self): raise AssertionError('must never cancel a monitored request')
    def type(self, *args): raise AssertionError('wait must never type while busy')

class MonitoredIdleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.trace = Path(self.temp.name).resolve()
        self.nonce = 'a' * 32
        self.observer = LIFE.init_segment(self.trace, self.nonce, 'boot')
    def tearDown(self): self.temp.cleanup()
    def test_delayed_natural_idle_does_not_cancel(self):
        ses = FakeSession([False, False, True])
        DRIVE.wait_monitored_idle(ses, self.observer, self.nonce, '/clear', lambda *a: None, .01, .001)
        self.assertEqual(ses.polls, 3)
    def test_screen_idle_is_insufficient_until_lifecycle_reconciles(self):
        ses = FakeSession([True, True, True])
        with patch.object(DRIVE, 'monitored_idle_reason', side_effect=['provider pending', 'tool pending', None]):
            DRIVE.wait_monitored_idle(ses, self.observer, self.nonce, '/clear', lambda *a: None, .01, .001)
        self.assertEqual(ses.polls, 3)
    def test_dead_client_stops_without_input(self):
        ses = FakeSession([]); ses.running = False
        with self.assertRaises(DRIVE.ClearProofError):
            DRIVE.wait_monitored_idle(ses, self.observer, self.nonce, '/clear', lambda *a: None, .01, .001)
    def test_active_provider_and_expected_tools_each_prevent_clear(self):
        path = self.trace / 'upstream-inflight.json'
        path.write_text(json.dumps({'requests': {'live-request': {'started_at': 'now'}}}))
        self.assertIn('provider', DRIVE.monitored_idle_reason(self.observer, self.nonce))
        path.unlink()
        self.assertIsNone(DRIVE.monitored_idle_reason(self.observer, self.nonce))
        LIFE.register_expected_tools(self.observer, self.nonce, 'boot', 'old-response', ['call-late'])
        self.assertIn('EXPECTED_TOOL_BATCH_MISSING', DRIVE.monitored_idle_reason(self.observer, self.nonce))
    def test_transient_idle_cannot_overtake_unaccepted_stop(self):
        ses = FakeSession([True, True])
        with patch.object(DRIVE, 'monitored_stage_reason', side_effect=['Stop pending', None]):
            DRIVE.wait_monitored_idle(ses, self.observer, self.nonce, '/clear', lambda *a: None,
                                      .01, .001, boundary_work=self.trace / 'work')
        self.assertEqual(ses.polls, 2)
    def test_stage_receipt_must_be_consumed_in_current_session(self):
        DRIVE.monitored_idle_reason(self.observer, self.nonce)
        work = self.trace / 'work'; work.mkdir()
        row = {'boundary_seq': 1, 'stage': 'draft', 'stage_partial': False,
               'pending_handoff': {'work': str(work), 'boundary_seq': 1, 'to_stage': 'draft',
                                   'stage_partial': False, 'state': 'pending'}}
        path = work / 'checkpoint.json'; path.write_text(json.dumps(row))
        self.assertIn('not accepted', DRIVE.monitored_stage_reason(work, self.observer, self.nonce))
        row['pending_handoff'].update(state='consumed', stop_session_id='old')
        path.write_text(json.dumps(row))
        with patch.object(DRIVE, 'capture_clear_anchor', return_value={'session_id': 'new'}):
            with self.assertRaises(DRIVE.ClearProofError):
                DRIVE.monitored_stage_reason(work, self.observer, self.nonce)
        with patch.object(DRIVE, 'capture_clear_anchor', return_value={'session_id': 'old'}):
            self.assertIsNone(DRIVE.monitored_stage_reason(work, self.observer, self.nonce))
        row['boundary_seq'] = 2; path.write_text(json.dumps(row))
        with self.assertRaises(DRIVE.ClearProofError):
            DRIVE.monitored_stage_reason(work, self.observer, self.nonce)
    def test_done_boundary_waits_for_final_stop_marker_retirement(self):
        DRIVE.monitored_idle_reason(self.observer, self.nonce)
        work = self.trace / 'work'; work.mkdir()
        marker = self.trace / '.sherlock'; marker.mkdir()
        value = {'workspace': str(self.trace), 'out': str(work), 'active': True}
        (marker / 'active.json').write_text(json.dumps(value))
        (marker / 'completed.json').write_text(json.dumps(value))
        self.assertIn('not retired', DRIVE.monitored_stage_reason(work, self.observer, self.nonce, True))
        (marker / 'active.json').unlink()
        self.assertIsNone(DRIVE.monitored_stage_reason(work, self.observer, self.nonce, True))

    def test_atomic_checkpoint_replacement_waits_for_stable_snapshot(self):
        DRIVE.monitored_idle_reason(self.observer, self.nonce)
        with patch.object(DRIVE._LIFECYCLE, '_load_json', side_effect=DRIVE._LIFECYCLE.LifecycleFault(
                'FILE_CHANGED_WHILE_READ', 'checkpoint.json')):
            self.assertIn('changing', DRIVE.monitored_stage_reason(
                self.trace / 'work', self.observer, self.nonce))

    def test_fault_and_wrong_identity_fail_closed(self):
        with self.assertRaises(DRIVE.ClearProofError):
            DRIVE.monitored_idle_reason(self.observer, 'b' * 32)
        LIFE.record_fault(self.observer, self.nonce, 'EXPECTED_TOOL_BATCH_MISSING', 'call-late')
        with self.assertRaises(DRIVE.ClearProofError):
            DRIVE.monitored_idle_reason(self.observer, self.nonce)

if __name__ == '__main__': unittest.main()
