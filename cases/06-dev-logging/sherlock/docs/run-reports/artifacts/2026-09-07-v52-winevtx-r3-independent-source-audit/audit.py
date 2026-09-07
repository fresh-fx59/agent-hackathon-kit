import json, pathlib, collections, hashlib, ipaddress
from datetime import datetime
root=pathlib.Path('/tmp/winevtx-r3-independent-source')
out=pathlib.Path(__file__).parent
rows=[]; manifest=[]
for p in sorted(root.rglob('*.jsonl')):
 data=p.read_bytes(); lines=data.splitlines(); name=str(p.relative_to(root)); manifest.append({'file':name,'bytes':len(data),'lines':len(lines),'sha256':hashlib.sha256(data).hexdigest()})
 for ln,s in enumerate(lines,1):
  e=json.loads(s)['Event']; sy=e['System']; eid=sy['EventID']; eid=eid.get('#text') if isinstance(eid,dict) else eid
  rows.append(dict(file=name,line=ln,id=eid,time=sy['TimeCreated']['#attributes']['SystemTime'],rid=sy.get('EventRecordID'),provider=sy['Provider']['#attributes']['Name'],data=e.get('EventData'),user=e.get('UserData'),security=sy.get('Security')))
(out/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
def matching(name): return [r for r in rows if r['file']==name]
def emit(name,x): (out/(name+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2))
sec=matching('Security.jsonl'); fail=[r for r in sec if r['id']==4625]; suc=[r for r in sec if r['id']==4624]
def count(field,rs=fail):return dict(collections.Counter(str(r['data'].get(field)) for r in rs))
def ends(rs): return sorted(rs,key=lambda r:r['time'])[::len(rs)-1] if len(rs)>1 else rs
summary={'files':len(manifest),'rendered_files':sum(m['file'].startswith('rendered/') for m in manifest),'rows':len(rows),'line_counts':{m['file']:m['lines'] for m in manifest},'security_ids':dict(collections.Counter(r['id'] for r in sec)),'security_bounds':ends(sec),'failure_days':dict(collections.Counter(r['time'][:10] for r in fail)),'failure_usernames':len(count('TargetUserName')),'failure_logontype':count('LogonType'),'failure_auth':count('AuthenticationPackageName'),'failure_logonprocess':count('LogonProcessName'),'failure_process':count('ProcessName'),'failure_status':count('Status'),'failure_substatus':count('SubStatus'),'ips':count('IpAddress'),'top_ips':collections.Counter(r['data']['IpAddress'] for r in fail).most_common(10),'account_breakdown':{name:dict(collections.Counter(r['data']['SubStatus'] for r in fail if r['data']['TargetUserName']==name)) for name in ['АДМИНИСТРАТОР','ROOT','root','ГОСТЬ']},'success_accounts':count('TargetUserName',suc),'success_types':count('LogonType',suc),'absence_ids':{str(i):[{'file':r['file'],'line':r['line'],'provider':r['provider']} for r in rows if r['id']==i] for i in [4688,5156,5157,4103,4104,1116,1117,4720,4722,4724,4738,4726]},'public_ips':len({r['data']['IpAddress'] for r in fail if r['data']['IpAddress']!='-' and ipaddress.ip_address(r['data']['IpAddress']).is_global})}
emit('aggregate',summary)
rcm=[r for r in rows if r['file'].endswith('RemoteConnectionManager-4Operational.jsonl')]; rdpcore=[r for r in rows if r['file'].endswith('RdpCoreTS-4Operational.jsonl')]
lo,hi=[r['time'] for r in ends(rcm)]; emit('rdp',{'rcm_ids':dict(collections.Counter(r['id'] for r in rcm)),'rcm_bounds':ends(rcm),'security_overlap_count':sum(lo<=r['time']<=hi for r in fail),'rdpcore_bounds':ends(rdpcore),'rdpcore_ids':dict(collections.Counter(r['id'] for r in rdpcore))})
for name,rs in [('lsm',[r for r in rows if 'LocalSessionManager-4Operational' in r['file']]),('rcm-admin',[r for r in rows if 'RemoteConnectionManager-4Admin' in r['file']]),('firewall',[r for r in rows if 'Security-4Firewall' in r['file']]),('services',[r for r in rows if r['file']=='System.jsonl' and (r['id']==7045 or r['line']==192)]),('security-session',[r for r in sec if 13497<=r['line']<=13517]),('group-enumeration',[r for r in sec if r['id'] in (4798,4799)]),('time-changes',[r for r in sec if r['id']==4616]),('winlogon',[r for r in rows if 'Winlogon-4Operational' in r['file'] and r['id'] in (1,2)]),('credential-read',[r for r in sec if r['id']==5379])]: emit(name,rs)
cred=[r for r in sec if r['id']==5379]; emit('credential-aggregate',{'count':len(cred),'bounds':ends(cred),'fields':{k:count(k,cred) for k in cred[0]['data']}})
emit('3389-matches',[{'file':r['file'],'line':r['line'],'data':r['data'],'user':r['user'],'time':r['time']} for r in rows if '3389' in json.dumps(r)])
print(json.dumps({k:v for k,v in summary.items() if k not in ('line_counts','security_bounds','ips')},ensure_ascii=False,indent=2))
