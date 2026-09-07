Работаю над воспроизведением корректных агрегатных строк перед финализацией.

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="run_shell_command">
<｜｜DSML｜｜parameter name="command" string="true">python3 - <<'EOF'
import json
lines=open('corpus/BlueSkyRansomware.jsonl',encoding='utf-8',errors='surrogateescape').read().splitlines()
from collections import Counter
c=Counter(); cc=Counter(); ps_day=Counter(); ps_msf=0; ps_winlogon=0; e600_ps=0
for i,l in enumerate(lines,1):
    try: e=json.loads(l)
    except Exception as ex: print('bad',i,ex); continue
    ch=e['Event']['System']['Channel']
    c[ch]+=1
    evid=e['Event']['System']['EventID'].get('#text')
    if evid=='600': cc['600']+=1
    if ch=='Windows PowerShell':
        ps_day[e['Event']['System']['TimeCreated']['#attributes']['SystemTime'][:10]]+=1
        if evid=='600': e600_ps+=1
        s=json.dumps(e['Event'],ensure_ascii=False)
        if 'MSFConsole' in s: ps_msf+=1
        if 'winlogon.exe' in s: ps_winlogon+=1
print('channels',c)
print('600 total',cc['600'])
print('ps_day',dict(ps_day),'ps_msf',ps_msf,'ps_winlogon',ps_winlogon,'e600_ps',e600_ps)
EOF</｜｜DSML｜｜parameter>
<｜｜DSML｜｜parameter name="description" string="true">Aggregate counts for line probes</｜｜DSML｜｜parameter>
<｜｜DSML｜｜parameter name="directory" string="true">/home/claude-developer/hack/sherlock-v52-bluesky-isolated-20260907-r4</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
<｜｜DSML｜｜invoke name="run_shell_command">
<｜｜DSML｜｜parameter name="command" string="true">head -80 work/map*.tsv 2>/dev/null; echo ===; cat work/coverage.md; echo ===; cat work/axis3.tsv 2>/dev/null; echo ===; ls work/</｜｜DSML｜｜parameter>
<｜｜DSML｜｜parameter name="description" string="true">Check coverage and rollover content</｜｜DSML｜｜parameter>
<｜｜DSML｜｜parameter name="directory" string="true">/home/claude-developer/hack/sherlock-v52-bluesky-isolated-20260907-r4</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
</｜｜DSML｜｜tool_calls>