#!/usr/bin/env python3
"""Turn a staged Sherlock run dir of version OLD into a fresh one of version NEW (no launch).

usage: stage_vnext.py OLD NEW SRC_DIR DST_DIR PKG_ROOT SOURCE_COMMIT AUTHORIZATION
PKG_ROOT holds skills/<NEW> and measure/citebench/harness/v2.
Spend cap is removed (operator rule 2026-11-16). Prints the dry preflight result.
"""
import hashlib, json, os, pathlib, secrets, shutil, subprocess, sys

OLD, NEW = sys.argv[1], sys.argv[2]
src, dst, pkg = (pathlib.Path(a) for a in sys.argv[3:6])
commit, auth = sys.argv[6], sys.argv[7]
assert src.is_dir() and not dst.exists()
OLD_PKG = json.loads((src / 'launch-manifest.json').read_text())['package_sha256']


def tree(root):
    h = hashlib.sha256()
    fs = sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for p in fs:
        h.update(p.relative_to(root).as_posix().encode() + b'\0' + hashlib.sha256(p.read_bytes()).hexdigest().encode() + b'\n')
    return h.hexdigest()


sha = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

skip = {'launch.claim', 'launch.done', 'launch.log', 'launcher.log', 'trusted-'+OLD, 'trusted-harness'}
dst.mkdir()
for e in src.iterdir():
    if e.name in skip or e.name.endswith('.investigate-only.json'):
        continue
    if e.is_dir():
        shutil.copytree(e, dst / e.name, symlinks=True, ignore=shutil.ignore_patterns(OLD) if e.name == 'wd' else None)
    else:
        shutil.copy2(e, dst / e.name)
# wd/skills/OLD was skipped by the ignore; rebuild skills dir with NEW
(dst / 'wd/skills').mkdir(exist_ok=True)
shutil.copytree(pkg / 'skills' / NEW, dst / 'wd/skills' / NEW)
shutil.copytree(pkg / 'skills' / NEW, dst / ('trusted-'+NEW))
(dst / 'trusted-harness').mkdir()
for f in ('hostgate.py', 'journalcheck.py', 'triagegate.py'):
    shutil.copy2(pkg / 'measure/citebench/harness/v2' / f, dst / 'trusted-harness' / f)
link = dst / 'wd/skills-root/sherlock'
if link.is_symlink() or link.exists():
    link.unlink()
os.symlink('../skills/'+NEW, link)

pkg_sha = tree(dst / ('trusted-'+NEW))
assert pkg_sha == tree(dst / 'wd/skills' / NEW)

L = (dst / 'launch.sh').read_text()
m_old = json.loads((dst / 'launch-manifest.json').read_text())
assert L.count(m_old['source_commit']) == 1 and L.count(m_old['authorization']) == 1 and L.count(OLD_PKG) == 1
L = (L.replace(m_old['source_commit'], commit).replace(m_old['authorization'], '@@AUTH@@')
      .replace(OLD_PKG, pkg_sha).replace(OLD, NEW).replace('@@AUTH@@', auth))
(dst / 'launch.sh').write_text(L)

s = json.loads((dst / 'run-spec.json').read_text().replace(src.name, dst.name).replace(OLD, NEW))
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

assert OLD not in (dst / 'run-spec.json').read_text() and OLD not in (dst / 'launch.sh').read_text().replace(auth, '')
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
