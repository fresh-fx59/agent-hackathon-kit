"""Read-only source audit; does not execute model-generated commands."""
import collections, datetime, hashlib, json, pathlib, re

OUT = pathlib.Path(__file__).parent
SOURCE = pathlib.Path('/Users/a/hack/sherlock-final-inputs-20260906/independent/corpus/BlueSkyRansomware.jsonl')
REPORT = pathlib.Path('/Users/a/Documents/projects/personal-os/docs/run-reports/artifacts/2026-09-07-v52-bluesky-r5-live-snapshot/report.md')
raw = SOURCE.read_bytes()
report = REPORT.read_bytes()
assert hashlib.sha256(raw).hexdigest() == '28b73f0be7b2a6ed7d102c8a8ba86d7c186887e0b71fcacfb8f73dcb0e37ff58'
assert hashlib.sha256(report).hexdigest() == '53257f5296d2d92a3196a14873b2272424bffa22cf8b9c3265a14a8e20595024'
lines = raw.decode().splitlines()
rows = []
for n, line in enumerate(lines, 1):
    event = json.loads(line)['Event']; system = event['System']
    eid = system['EventID']; eid = eid.get('#text') if isinstance(eid, dict) else eid
    data = (event.get('EventData') or {}).get('Data', {})
    data = data.get('#text') if isinstance(data, dict) else data
    context = '\n'.join(map(str, data)) if isinstance(data, list) else str(data)
    fields = {m[1]: m[2].strip() for m in re.finditer(r'\t(\w+)=(.*?)(?=\r?\n\t\w+=|\Z)', context, re.S)}
    rows.append(dict(line=n, sha256=hashlib.sha256(line.encode()).hexdigest(), id=eid,
        provider=system['Provider']['#attributes']['Name'], channel=system['Channel'],
        record_id=system['EventRecordID'], time=system['TimeCreated']['#attributes']['SystemTime'],
        computer=system['Computer'], data=data, fields=fields, security=system.get('Security')))
assert len(rows) == 469 and len(raw) == 467262
def dt(value): return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
sql = [r for r in rows if r['provider'] == 'MSSQLSERVER']
ps = [r for r in rows if r['provider'] == 'PowerShell']
fail = [r for r in sql if r['id'] == 18456]
success = [r for r in sql if r['id'] == 18454]
integrated = [r for r in sql if r['id'] == 18453]
telemetry = [r for r in integrated if r['data'][0] == 'NT SERVICE\\SQLTELEMETRY']
windows = {}
for channel in sorted({r['channel'] for r in rows}):
    ids = [r['record_id'] for r in rows if r['channel'] == channel]
    windows[channel] = dict(count=len(ids), first=min(ids), last=max(ids), unique=len(set(ids)), missing=sorted(set(range(min(ids), max(ids)+1))-set(ids)))
citations = []
for rn, text in enumerate(report.decode().splitlines(), 1):
    for match in re.finditer(r'BlueSkyRansomware\.jsonl:(\d+)(?:\s*[—]?\s*«([^»]*)»)?', text):
        n = int(match[1]); quote = match[2]
        citations.append(dict(report_line=rn, source_line=n, exists=1 <= n <= len(lines), quote=quote,
            exact_quote=None if quote is None else quote in lines[n-1]))
facts = dict(source=dict(path=str(SOURCE), sha256=hashlib.sha256(raw).hexdigest(), records=len(rows), bytes=len(raw)),
    report=dict(path=str(REPORT), sha256=hashlib.sha256(report).hexdigest(), bytes=len(report), status='draft/live snapshot; semantic FAIL'),
    channels=dict(collections.Counter(r['channel'] for r in rows)), computers=dict(collections.Counter(r['computer'] for r in rows)),
    time_bounds=[min(r['time'] for r in rows), max(r['time'] for r in rows)], days=dict(collections.Counter(r['time'][:10] for r in rows)),
    record_windows=windows, provider_ids=dict(collections.Counter(r['provider']+':'+str(r['id']) for r in rows)),
    sql_failure_count=len(fail), failure_payload_counts=dict(collections.Counter(str(r['data']) for r in fail)),
    failure_days={day: dict(count=len(group), first=group[0]['time'], last=group[-1]['time'], elapsed_seconds=(dt(group[-1]['time'])-dt(group[0]['time'])).total_seconds()) for day in sorted({r['time'][:10] for r in fail}) for group in [[r for r in fail if r['time'].startswith(day)]]},
    sql_success_lines=[r['line'] for r in success], integrated_users=dict(collections.Counter(r['data'][0] for r in integrated)),
    telemetry_gaps_seconds=[(dt(b['time'])-dt(a['time'])).total_seconds() for a,b in zip(telemetry,telemetry[1:])],
    sa_trace_lines=[r['line'] for r in sql if r['data']==['1','sa']],
    ps_ids=dict(collections.Counter(r['id'] for r in ps)), ps_hostnames=dict(collections.Counter(r['fields'].get('HostName') for r in ps)),
    config_to_ps_seconds=(dt(rows[458]['time'])-dt(rows[456]['time'])).total_seconds(),
    securitycenter_states=dict(collections.Counter(str(r['data']) for r in rows if r['provider']=='SecurityCenter' and r['id']==15)),
    absent_ids={str(i):[r['line'] for r in rows if r['id']==i] for i in [4624,4672,4688,4103,4104,1116,1117]},
    citations=citations)
assert len(fail)==44 and len(success)==4 and len(integrated)==15
assert facts['ps_hostnames']=={'ConsoleHost':70,'Default Host':46,'Visual Studio Code Host':8,'MSFConsole':9}
assert facts['sa_trace_lines']==[109,295]
assert all(c['exists'] and c['exact_quote'] is not False for c in citations)
selected = {c['source_line'] for c in citations} | {295, 207, 210, 211, 317, 426, 469}
selected |= {r['line'] for r in rows if r['id'] in [18453,18454,15457] or r['provider']=='SecurityCenter'}
evidence = [dict(line=n,sha256=rows[n-1]['sha256'],raw=lines[n-1]) for n in sorted(selected)]
(OUT/'computed-facts.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n')
(OUT/'source-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(source_sha256=facts['source']['sha256'],report_sha256=facts['report']['sha256'],records=len(rows),citations=len(citations),unique_cited_lines=len({c['source_line'] for c in citations}),quotes=sum(c['quote'] is not None for c in citations),evidence_lines=len(evidence),assertions='PASS'),indent=2))
