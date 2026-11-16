#!/usr/bin/env python3
"""Turn a staged v68 Sherlock run dir into a fresh v69 one (no launch).

usage: stage_v69.py SRC_DIR DST_DIR PKG_ROOT SOURCE_COMMIT AUTHORIZATION
PKG_ROOT holds skills/v69 and measure/citebench/harness/v2.
Spend cap is removed (operator rule 2026-11-16). Prints the dry preflight result.
"""
import hashlib, json, os, pathlib, secrets, shutil, subprocess, sys

src, dst, pkg = (pathlib.Path(a) for a in sys.argv[1:4])
commit, auth = sys.argv[4], sys.argv[5]
assert src.is_dir() and not dst.exists()
OLD_PKG = 'f67ed0aac67bc0dd6316b7dddea291c34b116e2800b36c03b46101f7796b568c'


def tree(root):
    h = hashlib.sha256()
    fs = sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for p in fs:
        h.update(p.relative_to(root).as_posix().encode() + b'\0' + hashlib.sha256(p.read_bytes()).hexdigest().encode() + b'\n')
    return h.hexdigest()


sha = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

skip = {'launch.claim', 'launch.done', 'launch.log', 'launcher.log', 'trusted-v68', 'trusted-harness'}
dst.mkdir()
for e in src.iterdir():
    if e.name in skip or e.name.endswith('.investigate-only.json'):
        continue
    if e.is_dir():
        shutil.copytree(e, dst / e.name, symlinks=True, ignore=shutil.ignore_patterns('v68') if e.name == 'wd' else None)
    else:
        shutil.copy2(e, dst / e.name)
# wd/skills/v68 was skipped by ignore('v68'); rebuild skills dir with v69
(dst / 'wd/skills').mkdir(exist_ok=True)
shutil.copytree(pkg / 'skills/v69', dst / 'wd/skills/v69')
shutil.copytree(pkg / 'skills/v69', dst / 'trusted-v69')
(dst / 'trusted-harness').mkdir()
for f in ('hostgate.py', 'journalcheck.py', 'triagegate.py'):
    shutil.copy2(pkg / 'measure/citebench/harness/v2' / f, dst / 'trusted-harness' / f)
link = dst / 'wd/skills-root/sherlock'
if link.is_symlink() or link.exists():
    link.unlink()
os.symlink('../skills/v69', link)

pkg_sha = tree(dst / 'trusted-v69')
assert pkg_sha == tree(dst / 'wd/skills/v69')

L = (dst / 'launch.sh').read_text()
m_old = json.loads((dst / 'launch-manifest.json').read_text())
assert L.count(m_old['source_commit']) == 1 and L.count(m_old['authorization']) == 1 and L.count(OLD_PKG) == 1
L = (L.replace(m_old['source_commit'], commit).replace(m_old['authorization'], '@@AUTH@@')
      .replace(OLD_PKG, pkg_sha).replace('v68', 'v69').replace('@@AUTH@@', auth))
(dst / 'launch.sh').write_text(L)

s = json.loads((dst / 'run-spec.json').read_text().replace(src.name, dst.name).replace('v68', 'v69'))
s['limits'].pop('spend_cap_rub', None)
(dst / 'run-spec.json').write_text(json.dumps(s, ensure_ascii=False, indent=1))

m = json.loads((dst / 'launch-manifest.json').read_text().replace(src.name, dst.name))
corp = {p.relative_to(dst / 'wd/corpus').as_posix(): sha(p) for p in sorted((dst / 'wd/corpus').rglob('*')) if p.is_file()}
assert corp == m_old['corpus_files']
m.update(authorization=auth, source_commit=commit, nonce=secrets.token_hex(16), nonce_state='unused',
         package_sha256=pkg_sha, staged_skill_sha256=pkg_sha, harness_sha256=tree(dst / 'trusted-harness'),
         run_spec_sha256=sha(dst / 'run-spec.json'), limits=s['limits'],
         qwen_settings_sha256=hashlib.sha256(json.dumps(s['qwen']['settings'], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
         phase_prompt_sha256={p['id']: hashlib.sha256(p['prompt'].encode()).hexdigest() for p in s['phases']},
         qwr_runner_sha256=sha('/home/claude-developer/personal-os/tools/qwen-run/qwen_run/runner.py'))
(dst / 'launch-manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=1))

assert 'v68' not in (dst / 'run-spec.json').read_text() and 'v68' not in (dst / 'launch.sh').read_text().replace(auth, '')
print('run', s['run_id'], 'pkg', pkg_sha, 'harness', m['harness_sha256'], 'limits', s['limits'])
print('phases', [(p['id'], p['limits']['max_wall_time_s']) for p in s['phases']])

# dry preflight: the launch.sh python block with the claim replaced
body, on = [], False
for line in L.splitlines():
    if line.startswith('python3 - "$BASE" <<'):
        on = True; continue
    if on and line == 'PY':
        break
    if on:
        body.append('raise SystemExit("DRY-OK")' if line.startswith('fd=os.open') else line)
r = subprocess.run([sys.executable, '-', str(dst.resolve())], input='\n'.join(body), text=True, capture_output=True)
print((r.stdout + r.stderr).strip()[-600:])
