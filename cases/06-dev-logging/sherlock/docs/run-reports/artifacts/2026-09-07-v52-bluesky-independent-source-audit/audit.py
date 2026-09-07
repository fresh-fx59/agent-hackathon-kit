import json,pathlib,hashlib,collections,re
source=pathlib.Path('/Users/a/hack/sherlock-final-inputs-20260906/independent/corpus/BlueSkyRansomware.jsonl')
out=pathlib.Path(__file__).parent
raw=source.read_bytes();sha=hashlib.sha256(raw).hexdigest();assert sha=='28b73f0be7b2a6ed7d102c8a8ba86d7c186887e0b71fcacfb8f73dcb0e37ff58'
rows=[]
for n,line in enumerate(raw.splitlines(),1):
 e=json.loads(line)['Event'];s=e['System'];i=s['EventID'];i=i.get('#text') if isinstance(i,dict) else i
 rows.append(dict(line=n,sha256=hashlib.sha256(line).hexdigest(),id=i,provider=s['Provider']['#attributes']['Name'],channel=s['Channel'],time=s['TimeCreated']['#attributes']['SystemTime'],computer=s['Computer'],data=e.get('EventData'),user=e.get('UserData'),security=s.get('Security'),execution=s.get('Execution')))
assert len(rows)==469
summary=dict(source=str(source),sha256=sha,bytes=len(raw),records=len(rows),providers=dict(collections.Counter(r['provider'] for r in rows)),channels=dict(collections.Counter(r['channel'] for r in rows)),provider_ids=dict(collections.Counter(r['provider']+':'+str(r['id']) for r in rows)),computers=dict(collections.Counter(r['computer'] for r in rows)),bounds=sorted(rows,key=lambda r:r['time'])[::len(rows)-1])
(out/'source-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));(out/'source-events.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k!='bounds'},ensure_ascii=False,indent=2))
sql=[r for r in rows if r['provider']=='MSSQLSERVER']; ps=[r for r in rows if r['provider']=='PowerShell']; failures=[r for r in sql if r['id']==18456]; sql_success=[r for r in sql if r['id']==18454]; integrated=[r for r in sql if r['id']==18453]
def payload(r):return (r.get('data') or {}).get('Data',{}).get('#text')
def bounds(rs):return [(r['line'],r['time']) for r in sorted(rs,key=lambda r:r['time'])[::max(1,len(rs)-1)]]
ps_groups=collections.defaultdict(list)
for r in ps:
 vals=payload(r); text='\n'.join(map(str,vals)) if isinstance(vals,list) else str(vals)
 fields={m.group(1):m.group(2).strip() for m in re.finditer(r'\t(\w+)=(.*?)(?=\r?\n\t\w+=|\Z)',text,re.S)}
 r['powershell_fields']=fields;ps_groups[(fields.get('HostName'),fields.get('HostApplication'))].append(r['line'])
evidence=dict(sql_failures=len(failures),sql_failure_payload_counts=dict(collections.Counter(str(payload(r)) for r in failures)),sql_failure_days=dict(collections.Counter(r['time'][:10] for r in failures)),sql_failure_bursts={day:bounds([r for r in failures if r['time'].startswith(day)]) for day in sorted({r['time'][:10] for r in failures})},sql_successes=sql_success,integrated_count=len(integrated),integrated_users=dict(collections.Counter(payload(r)[0] for r in integrated)),sql_configuration=[r for r in sql if r['id']==15457],sql_listener_records=[r for r in sql if r['id']==26022],powershell_ids=dict(collections.Counter(r['id'] for r in ps)),powershell_days=dict(collections.Counter(r['time'][:10] for r in ps)),powershell_host_groups=[dict(hostname=k[0],hostapplication=k[1],lines=v,count=len(v)) for k,v in ps_groups.items()],securitycenter_states=[r for r in rows if r['provider']=='SecurityCenter' and r['id']==15],absence_ids={str(i):[r['line'] for r in rows if r['id']==i] for i in [4688,4103,4104,1116,1117]},events_named_4625=[r for r in rows if r['id']==4625])
(out/'computed-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2));(out/'powershell-evidence.json').write_text(json.dumps(ps,ensure_ascii=False,indent=2))
assert len(failures)==44 and len(sql_success)==4 and len(integrated)==15
assert evidence['sql_failure_days']=={'2024-04-21':22,'2024-04-23':22}
assert evidence['powershell_ids']=={600:107,400:16,403:9,800:1}
assert len([r for r in ps if r['powershell_fields'].get('HostName')=='MSFConsole'])==9
