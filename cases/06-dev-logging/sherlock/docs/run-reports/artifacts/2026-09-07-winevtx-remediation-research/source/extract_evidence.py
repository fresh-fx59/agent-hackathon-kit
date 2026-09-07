#!/usr/bin/env python3
"""Read-only Winevtx evidence extraction; writes JSON to stdout."""
import argparse, hashlib, json, pathlib
from datetime import datetime

def attr(x, k):
    if isinstance(x, dict):
        return x.get(k) or x.get('#attributes', {}).get(k)

def flat(x):
    if isinstance(x, dict): return ' '.join(flat(v) for v in x.values())
    if isinstance(x, list): return ' '.join(flat(v) for v in x)
    return str(x)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', type=pathlib.Path)
    args = ap.parse_args()
    root = args.root
    out = {'source_root': str(root), 'files': [], 'credential_5379_success': [],
           'may_root_or_3proxy': [], 'powershell_host_application': [],
           'firewall_incoming_allow': [], 'may_interval': {}}
    for p in sorted(root.rglob('*.jsonl')):
        h = hashlib.sha256(); n = 0
        for n, raw in enumerate(p.open(), 1):
            h.update(raw.encode())
            try: obj = json.loads(raw)
            except json.JSONDecodeError: continue
            e = obj.get('Event', {}); s = e.get('System', {})
            eid = attr(s.get('EventID'), '') or s.get('EventID')
            t = attr(s.get('TimeCreated'), 'SystemTime')
            d = e.get('EventData') or {}
            text = flat(obj).lower()
            rel = str(p.relative_to(root))
            if str(eid) == '5379' and (str(d.get('ReturnCode')) == '0' or str(d.get('CountOfCredentialsReturned')) not in ('0','None','')):
                out['credential_5379_success'].append({'file': rel, 'line': n, 'time': t, 'event_id': eid, 'data': d})
            if ('3proxy' in text or 'imagepath' in text or '176.59.42.91' in text or 'targetusername' in text) and t and t.startswith('2021-05'):
                out['may_root_or_3proxy'].append({'file': rel, 'line': n, 'time': t, 'event_id': eid, 'data': d, 'user_data': e.get('UserData'), 'security': s.get('Security')})
            if 'powershell' in rel.lower() and ('hostapplication=' in text or str(eid) == '400'):
                out['powershell_host_application'].append({'file': rel, 'line': n, 'time': t, 'event_id': eid, 'data': d})
            if ('firewall' in rel.lower() and str(d.get('Direction')) == '1' and str(d.get('Action')) == '3'):
                out['firewall_incoming_allow'].append({'file': rel, 'line': n, 'time': t, 'event_id': eid, 'data': d})
        out['files'].append({'file': str(p.relative_to(root)), 'lines': n, 'sha256': h.hexdigest()})
    lsm = root / 'rendered/Microsoft-Windows-TerminalServices-LocalSessionManager-4Operational.jsonl'
    events = []
    for line_no, raw in enumerate(lsm.open(), 1):
        o = json.loads(raw); e=o['Event']; s=e['System']; eid=s['EventID']; eid=eid.get('#text') if isinstance(eid,dict) else eid
        t=s['TimeCreated']['#attributes']['SystemTime']; u=e.get('UserData') or {}
        x=flat(u)
        if t.startswith('2021-05-09') and ('22:11:55.638239Z' in t or str(eid) == '25' or 'Session' in x):
            events.append({'file': str(lsm.relative_to(root)), 'line': line_no, 'event_id': eid, 'time': t, 'user_data': u})
    install = datetime.fromisoformat('2021-05-09T22:25:01.786082+00:00')
    reconnect = datetime.fromisoformat('2021-05-09T22:11:55.638239+00:00')
    disconnect = datetime.fromisoformat('2021-05-09T23:34:00.597347+00:00')
    out['may_interval'] = {'reconnect': reconnect.isoformat(), 'install': install.isoformat(), 'next_disconnect': disconnect.isoformat(), 'reconnect_to_install_seconds': (install-reconnect).total_seconds(), 'install_to_next_disconnect_seconds': (disconnect-install).total_seconds(), 'lsm_records': events}
    prior = pathlib.Path('/Users/a/hack/wt-v42/cases/06-dev-logging/sherlock/docs/run-reports/artifacts/2026-09-07-v52-winevtx-r3-independent-source-audit/source-manifest.json')
    current = {x['file']: x for x in out['files']}
    expected = json.load(prior.open()) if prior.exists() else []
    mismatches = []
    for x in expected:
        y = current.get(x['file'])
        if not y or y['lines'] != x['lines'] or y['sha256'] != x['sha256']:
            mismatches.append({'file': x['file'], 'expected': x, 'actual': y})
    out['manifest_comparison'] = {'files_compared': len(out['files']), 'all_files_have_hash_and_line_count': all(x['sha256'] and x['lines'] >= 0 for x in out['files']), 'prior_manifest_path': str(prior), 'prior_manifest_entries': len(expected), 'mismatches': mismatches}
    out['counts'] = {k: len(v) for k,v in out.items() if isinstance(v, list) and k != 'files'}
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
if __name__ == '__main__': main()
