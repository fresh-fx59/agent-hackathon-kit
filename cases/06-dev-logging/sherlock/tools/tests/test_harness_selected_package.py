#!/usr/bin/env python3
"""Selected harness package preparation, using real target and version validators."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import os
import sys
import unittest
from unittest import mock
from tools.tests import test_target_contract_probe as probe_tests

ROOT = Path(__file__).resolve().parents[2]

def load():
    spec = importlib.util.spec_from_file_location('hq_selected', ROOT/'eval/bench/harness-qualification.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

class SelectedPackageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = probe_tests.TargetContractProbeTest(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.temp = self.fixture.temp.resolve()
        self.fixture.root = self.fixture.temp/'probe'
        self.fixture.qwen_stub = self.fixture.qwen_stub.resolve()
        self.fixture.args.root = self.fixture.root
        self.fixture.args.qwen_bin = str(self.fixture.qwen_stub)
        self.fixture.args.package_version = 'v45'
        self.fixture.args.operator_monitored = True
        self.fixture.probe.prepare(self.fixture.args)
        self.qual = load()
        self.output = self.fixture.temp/'harness'; self.output.mkdir()
        (self.output/'prompt.txt').write_text('Investigate this fresh synthetic corpus.\n')
        (self.output/'input-package.json').write_text('{"schema":1}\n')

    def prepare(self):
        return self.qual.prepare_selected(self.fixture.root, self.output, self.fixture.qwen_stub)

    def test_copies_exact_settings_and_binds_canonical_registered_package(self):
        row = self.prepare()
        profile = json.loads((self.output/'target-profile.json').read_text())
        self.assertEqual(profile['package_version'], 'v45')
        self.assertEqual(profile['requested_model'], 'gpt-5.5')
        self.assertEqual((self.output/'corporate-settings.json').read_bytes(),
                         (self.fixture.root/'corporate-settings.json').read_bytes())
        self.assertEqual(profile['package_sha256'], self.qual._version_gate().tree_digest(self.output/'runtime-package'))
        self.assertEqual(profile['tool_schema_sha256'], self.qual.digest((self.output/'tool-schema.json').read_bytes()))
        self.assertEqual(row['package_sha256'], profile['package_sha256'])
        budget=json.loads((self.output/'probe-budget.json').read_text())
        self.assertIsNone(budget['max_wall_seconds'])
        self.assertEqual(budget['request_timeout_ms'],600000)
        package=json.loads((self.output/'input-package.json').read_text())
        self.assertEqual(package.get('lifecycle_helper_sha256'),
                         self.qual.digest((ROOT/'eval/bench/lifecycle-supervisor.py').read_bytes()))

    def test_changed_settings_refused_without_model_or_secret_access(self):
        (self.fixture.root/'corporate-settings.json').chmod(0o600)
        (self.fixture.root/'corporate-settings.json').write_text('{}')
        with self.assertRaises(self.qual.QualificationFailure): self.prepare()
        self.assertFalse((self.output/'selected-package.json').exists())

    def test_runtime_symlink_refused(self):
        runtime=self.fixture.root/'runtime-package'
        runtime.chmod(0o700)
        (runtime/'injected').symlink_to(self.fixture.qwen_stub)
        with self.assertRaises(self.qual.QualificationFailure): self.prepare()

    def test_wrong_qwen_refused(self):
        other=self.fixture.temp/'other'; other.write_text('wrong')
        with self.assertRaises(self.qual.QualificationFailure):
            self.qual.prepare_selected(self.fixture.root,self.output,other)

    def test_source_mutation_after_copy_is_refused(self):
        original=shutil.copytree
        def copying(src,dst,*args,**kwargs):
            value=original(src,dst,*args,**kwargs)
            if Path(dst)==self.output/'runtime-package':
                path=self.fixture.root/'corporate-settings.json'; path.chmod(0o600); path.write_text('{}')
            return value
        with mock.patch('shutil.copytree',side_effect=copying):
            with self.assertRaises(self.qual.QualificationFailure): self.prepare()

    def test_selected_audit_uses_snapshot_and_refuses_changed_selection(self):
        row=self.prepare()
        profile_raw=(self.output/'target-profile.json').read_bytes()
        identity={'target_profile_sha256':self.qual.digest(profile_raw), 'settings_sha256':self.qual.digest((self.output/'corporate-settings.json').read_bytes())}
        selected=self.qual._selected_identity(self.output,identity)
        self.assertEqual(selected['version'],'v45')
        record=json.loads((self.output/'selected-package.json').read_text())
        record['package_version']='v44'
        (self.output/'selected-package.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(self.qual.QualificationFailure,'SELECTED_IDENTITY'):
            self.qual._selected_identity(self.output,identity)

    def test_selected_audit_refuses_snapshot_tool_mutation(self):
        self.prepare()
        identity={'target_profile_sha256':self.qual.digest((self.output/'target-profile.json').read_bytes()), 'settings_sha256':self.qual.digest((self.output/'corporate-settings.json').read_bytes())}
        path=self.output/'runtime-package/tools/finalize.py';path.chmod(0o600)
        path.write_text('changed')
        with self.assertRaisesRegex(self.qual.QualificationFailure,'SELECTED_IDENTITY'):
            self.qual._selected_identity(self.output,identity)

    def test_selected_cli_prepares_the_verified_bundle(self):
        result=subprocess.run([sys.executable,str(ROOT/'eval/bench/harness-qualification.py'),
            'prepare-selected','--target-input',str(self.fixture.root),'--output',str(self.output),
            '--qwen',str(self.fixture.qwen_stub)],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['package_version'],'v45')
        package=json.loads((self.output/'input-package.json').read_text())
        self.assertEqual(package['prompt_sha256'], self.qual.digest((self.output/'prompt.txt').read_bytes()))

    def test_launcher_refuses_invalid_input_before_qwen_and_missing_secret(self):
        fakebin=self.fixture.temp/'bin';fakebin.mkdir()
        marker=self.fixture.temp/'qwen-called'
        fake=fakebin/'qwen';fake.write_text('#!/bin/sh\necho called > '+str(marker)+'\n')
        fake.chmod(0o700)
        env={'PATH':str(fakebin)+os.pathsep+os.environ['PATH'],'HOME':str(self.fixture.temp)}
        result=subprocess.run([str(ROOT/'eval/bench/run-harness-qualification.sh'),
            str(self.fixture.temp/'refused-output'),'--target-input',str(self.fixture.temp/'missing')],
            env=env,text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('selected input refused',result.stderr)
        self.assertNotIn('SHERLOCK_API_KEY is required',result.stderr)
        self.assertFalse(marker.exists())

    def test_audit_uses_preserved_snapshot_after_source_changes(self):
        self.prepare()
        identity={'target_profile_sha256':self.qual.digest((self.output/'target-profile.json').read_bytes()), 'settings_sha256':self.qual.digest((self.output/'corporate-settings.json').read_bytes())}
        source=self.fixture.root/'runtime-package/SKILL.md';source.chmod(0o600);source.write_text('changed')
        self.assertEqual(self.qual._selected_identity(self.output,identity)['version'],'v45')

    def test_selected_audit_directly_binds_executed_settings(self):
        self.prepare()
        identity={'target_profile_sha256':self.qual.digest((self.output/'target-profile.json').read_bytes()),
                  'settings_sha256':'0'*64}
        with self.assertRaisesRegex(self.qual.QualificationFailure,'SELECTED_IDENTITY'):
            self.qual._selected_identity(self.output,identity)

    def test_shared_profile_validator_rejects_wrong_request_watchdog(self):
        self.prepare()
        profile=json.loads((self.output/'target-profile.json').read_text())
        profile['request_read_timeout_s']=601
        raw=self.qual.canonical(profile)+b'\n'
        (self.output/'target-profile.json').write_bytes(raw)
        record=json.loads((self.output/'selected-package.json').read_text())
        record['target_profile_sha256']=self.qual.digest(raw)
        (self.output/'selected-package.json').write_text(json.dumps(record))
        identity={'target_profile_sha256':self.qual.digest(raw),
                  'settings_sha256':self.qual.digest((self.output/'corporate-settings.json').read_bytes())}
        with self.assertRaisesRegex(self.qual.QualificationFailure,'SELECTED_IDENTITY'):
            self.qual._selected_identity(self.output,identity)

if __name__=='__main__':unittest.main()
