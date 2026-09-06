#!/usr/bin/env python3
"""Provider-free installed-Qwen nested-agent hook/request capture.

The loopback server scripts an inline `agent` call, one child completion, then a
parent completion.  It persists every HTTP request/response and every Qwen hook
stdin.  It never contacts a provider or loads Sherlock.
"""
import argparse, hashlib, json, os
from pathlib import Path
import subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORDER = r'''import json, pathlib, sys, time
raw=sys.stdin.buffer.read(); root=pathlib.Path(sys.argv[1])
root.mkdir(mode=0o700, exist_ok=True)
p=root/(str(time.time_ns())+'.json'); p.write_bytes(raw)
event=json.loads(raw)
print(json.dumps({'continue':True,'hookSpecificOutput':{'hookEventName':event['hook_event_name']}}))
'''

def once(path, data):
    data=data.encode() if isinstance(data,str) else data
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())

def main():
    p=argparse.ArgumentParser(); p.add_argument('--qwen',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--child-tool',action='store_true'); p.add_argument('--sibling-tool',action='store_true'); p.add_argument('--child-max-turns',type=int,choices=(1,2),default=2); a=p.parse_args()
    root=a.output; root.parent.mkdir(mode=0o700, parents=True, exist_ok=True); root.mkdir(mode=0o700); work=root/'workspace'; work.mkdir(); home=work/'home'; home.mkdir(); hooks=root/'hooks'; hooks.mkdir()
    recorder=root/'record_hook.py'; recorder.write_text(RECORDER); recorder.chmod(0o700)
    agent=work/'.qwen/agents'; agent.mkdir(parents=True)
    (agent/'mock-triage.md').write_text(f'---\nname: mock-triage\ndescription: mock only\napprovalMode: yolo\nmaxTurns: {a.child_max_turns}\n---\nReply with one word.\n')
    command=f'{sys.executable} {recorder} {hooks}'
    events=('PreToolUse','PostToolUse','PostToolUseFailure','PostToolBatch','SubagentStart','SubagentStop','UserPromptSubmit')
    settings={'hooks':{e:[{'matcher':'*','hooks':[{'type':'command','command':command,'timeout':10000}]}] for e in events}}
    (work/'.qwen/settings.json').write_text(json.dumps(settings,sort_keys=True)+'\n')
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['Content-Length'])); n=len(requests); requests.append(n)
            once(root/f'request-{n}.json',raw)
            once(root/f'request-{n}.headers.json',json.dumps(dict(self.headers),sort_keys=True)+'\n')
            if n==0:
                calls=[{'index':0,'id':'call_parent_agent','type':'function','function':{'name':'agent','arguments':json.dumps({'description':'mock nested capture','subagent_type':'mock-triage','run_in_background':False,'prompt':'reply mock'})}}]
                if a.sibling_tool:
                    calls.append({'index':1,'id':'call_parent_sibling','type':'function','function':{'name':'run_shell_command','arguments':json.dumps({'command':'printf sibling > sibling-marker.txt'})}})
                delta={'role':'assistant','tool_calls':calls}; finish='tool_calls'
            elif a.child_tool and n==1:
                delta={'role':'assistant','tool_calls':[{'index':0,'id':'call_child_shell','type':'function','function':{'name':'run_shell_command','arguments':json.dumps({'command':'printf child > child-marker.txt'})}}]}; finish='tool_calls'
            else:
                delta={'role':'assistant','content':'mock completion'}; finish='stop'
            rows=[{'id':f'nested-{n}','object':'chat.completion.chunk','created':int(time.time()),'model':'mock','choices':[{'index':0,'delta':delta,'finish_reason':None}]},{'id':f'nested-{n}','object':'chat.completion.chunk','created':int(time.time()),'model':'mock','choices':[{'index':0,'delta':{},'finish_reason':finish}]}]
            body=b''.join(b'data: '+json.dumps(x).encode()+b'\n\n' for x in rows)+b'data: [DONE]\n\n'
            once(root/f'response-{n}.sse',body); self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','TMPDIR')}; env.update(HOME=str(home),QWEN_HOME=str(home),OPENAI_API_KEY='loopback-only',OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',NO_PROXY='127.0.0.1,localhost')
    argv=[str(a.qwen.resolve()),'--auth-type','openai','--model','mock','--approval-mode','yolo','--output-format','json','-p','spawn mock agent then finish']
    cp=subprocess.run(argv,cwd=work,env=env,text=True,capture_output=True,timeout=60)
    once(root/'stdout.json',cp.stdout); once(root/'stderr.txt',cp.stderr); server.shutdown(); server.server_close(); thread.join()
    hook_rows=[]
    for f in sorted(hooks.glob('*.json')):
        raw=f.read_bytes(); event=json.loads(raw); hook_rows.append({'event':event.get('hook_event_name'),'agent_id':event.get('agent_id'),'agent_type':event.get('agent_type'),'session_id':event.get('session_id'),'tool_use_id':event.get('tool_use_id'),'tool_call_id':event.get('tool_call_id'),'sha256':hashlib.sha256(raw).hexdigest()})
    expected_requests = (2 + a.child_max_turns) if a.child_tool else 2
    result={'exit_code':cp.returncode,'request_count':len(requests),'expected_request_count':expected_requests,'child_max_turns':a.child_max_turns,'hook_rows':hook_rows,'qwen_sha256':hashlib.sha256(a.qwen.read_bytes()).hexdigest(),'provider':'loopback-only'}
    once(root/'result.json',json.dumps(result,indent=2)+'\n'); print(json.dumps(result,indent=2)); return 0 if cp.returncode==0 and len(requests)==expected_requests else 1
if __name__=='__main__': raise SystemExit(main())
