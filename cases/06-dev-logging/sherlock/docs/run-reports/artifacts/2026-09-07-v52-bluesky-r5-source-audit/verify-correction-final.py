"""Recheck immutable final report citations and material source aggregates.

Reads the original corpus, never executes a command quoted by the model.
"""
import collections
import datetime
import hashlib
import json
import pathlib
import re

OUT = pathlib.Path(__file__).parent
SOURCE = pathlib.Path('/Users/a/hack/sherlock-final-inputs-20260906/independent/corpus/BlueSkyRansomware.jsonl')
REPORT = OUT.parent / '2026-09-07-v52-bluesky-r5-terminal-evidence/report-correction-final.md'
raw = SOURCE.read_bytes()
report = REPORT.read_bytes()
assert hashlib.sha256(raw).hexdigest() == '28b73f0be7b2a6ed7d102c8a8ba86d7c186887e0b71fcacfb8f73dcb0e37ff58'
assert hashlib.sha256(report).hexdigest() == '182b30ddb6e52bd2d033d563452fc38f46fc04c669f1882c98f287e51b7017b5'
lines = raw.decode().splitlines()
rows = []
for n, line in enumerate(lines, 1):
    event = json.loads(line)['Event']
    system = event['System']
    eid = system['EventID']
    eid = eid.get('#text') if isinstance(eid, dict) else eid
    data = (event.get('EventData') or {}).get('Data', {})
    data = data.get('#text') if isinstance(data, dict) else data
    context = '\n'.join(map(str, data)) if isinstance(data, list) else str(data)
    fields = {m[1]: m[2].strip() for m in re.finditer(r'\t(\w+)=(.*?)(?=\r?\n\t\w+=|\Z)', context, re.S)}
    rows.append(dict(line=n, id=eid, provider=system['Provider']['#attributes']['Name'], channel=system['Channel'],
        time=system['TimeCreated']['#attributes']['SystemTime'], data=data, fields=fields))
assert len(rows) == 469 and len(raw) == 467262
sql = [r for r in rows if r['provider'] == 'MSSQLSERVER']
ps = [r for r in rows if r['provider'] == 'PowerShell']
citations = []
for rn, text in enumerate(report.decode().splitlines(), 1):
    for m in re.finditer(r'BlueSkyRansomware\.jsonl:(\d+)(?:\s*[—]?\s*«([^»]*)»)?', text):
        n = int(m[1])
        citations.append(dict(report_line=rn, source_line=n, exists=1 <= n <= len(lines),
            quote=m[2], exact_quote=None if m[2] is None else m[2] in lines[n-1]))
assert all(c['exists'] and c['exact_quote'] is True for c in citations)
hosts = dict(collections.Counter(r['fields'].get('HostName') for r in ps))
assert hosts == {'ConsoleHost':70, 'Default Host':46, 'Visual Studio Code Host':8, 'MSFConsole':9}
non_msf = [r for r in ps if r['fields']['HostName'] != 'MSFConsole']
msf = [r for r in ps if r['fields']['HostName'] == 'MSFConsole']
assert len(non_msf) == 124 and all('powershell.exe' in r['fields']['HostApplication'].lower() for r in non_msf)
assert len(msf) == 9 and all(r['fields']['HostApplication'] == 'winlogon.exe' for r in msf)
dt = lambda s: datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
facts = dict(source_sha256=hashlib.sha256(raw).hexdigest(), source_bytes=len(raw), source_records=len(rows),
    report_sha256=hashlib.sha256(report).hexdigest(), report_bytes=len(report), report_lines=len(report.decode().splitlines()),
    channels=dict(collections.Counter(r['channel'] for r in rows)),
    sql_ids=dict(collections.Counter(r['id'] for r in sql)),
    sa_trace_lines=[r['line'] for r in sql if r['data'] == ['1','sa']],
    sql_success_lines=[r['line'] for r in sql if r['id'] == 18454],
    config_events=[r for r in sql if r['id'] == 15457],
    ps_ids=dict(collections.Counter(r['id'] for r in ps)), ps_hostnames=hosts,
    non_msf_powershell_hostapplication=124, msf_winlogon_hostapplication=9,
    config_to_ps_seconds=(dt(rows[458]['time'])-dt(rows[456]['time'])).total_seconds(),
    securitycenter_states=dict(collections.Counter(str(r['data']) for r in rows if r['provider']=='SecurityCenter' and r['id']==15)),
    citation_count=len(citations), unique_cited_lines=len({c['source_line'] for c in citations}), citations=citations,
    mechanical_source_checks='PASS', semantic_verdict='FAIL: see correction-final-review.md; exact quotes do not prove surrounding inferences')
assert facts['sa_trace_lines'] == [109,295]
assert facts['sql_success_lines'] == [186,454,455,458]
assert facts['sql_ids'][18456] == 44 and facts['sql_ids'][18453] == 15
assert facts['config_to_ps_seconds'] == 65.593929
(OUT/'correction-final-checks.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({k:v for k,v in facts.items() if k in ['source_sha256','source_records','report_sha256','citation_count','unique_cited_lines','mechanical_source_checks','semantic_verdict']}, indent=2))
