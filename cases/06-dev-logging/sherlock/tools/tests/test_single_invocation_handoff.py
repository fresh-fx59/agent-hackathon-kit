import importlib.util
import os
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
PACKAGE=ROOT/'skills'/os.environ.get('SHERLOCK_TEST_PACKAGE','v48')
spec=importlib.util.spec_from_file_location('handoff_subject',PACKAGE/'tools/checkpoint.py')
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)
class HandoffInvocationTests(unittest.TestCase):
    def test_full_and_partial_handoffs_carry_arguments_in_skill_submission(self):
        for partial,stage in ((False,'draft'),(False,'repair'),(True,'triage'),(True,'draft'),(True,'repair')):
            with self.subTest(partial=partial,stage=stage):
                row={'stage':stage,'resolved':4,'total':4,'unresolved':0}
                text=subject.render_handoff('/tmp/fresh work','triage',row,partial=partial)
                self.assertIn('  1) /clear\n',text)
                self.assertIn('  2) /sherlock ПРОДОЛЖИ РАССЛЕДОВАНИЕ ИЗ /tmp/fresh work — СТУПЕНЬ '+stage,text)
                self.assertNotIn('  3)',text)
                self.assertNotIn('  2) /sherlock\n',text)
    def test_done_has_no_new_invocation(self):
        text=subject.render_handoff('/tmp/work','repair',{'stage':'done','resolved':4,'total':4})
        self.assertNotIn('/sherlock',text)
if __name__=='__main__':unittest.main()
