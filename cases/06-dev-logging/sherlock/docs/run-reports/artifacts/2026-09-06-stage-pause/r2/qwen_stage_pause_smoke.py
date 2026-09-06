#!/usr/bin/env python3
"""Installed Qwen: real handoff tool, delayed response, real Stop, safe clear.

Only a scripted localhost provider and synthetic one-line evidence are used.
This tests composition, not investigation quality or final report acceptance.
"""
import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

BRIDGE = '''import pathlib,subprocess,sys,time
root=pathlib.Path(sys.argv[1]); event=sys.argv[2]; raw=sys.stdin.buffer.read()
p=root/('%020d-'%time.time_ns()+event); p.with_suffix('.input.json').write_bytes(raw)
r=subprocess.run(sys.argv[3:],input=raw,capture_output=True)
p.with_suffix('.output.json').write_bytes(r.stdout); p.with_suffix('.stderr').write_bytes(r.stderr)
p.with_suffix('.exit').write_text(str(r.returncode))
sys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr);raise SystemExit(r.returncode)
'''

def main():
    ap = argparse.ArgumentParser()
    for arg in ('output', 'qwen', 'helper', 'driver', 'skill'):
        ap.add_argument('--'+arg, type=Path, required=True)
    a = ap.parse_args()
    root=a.output.resolve(); root.mkdir(mode=0o700,parents=True)
    workspace=root/'workspace'; workspace.mkdir(); work=workspace/'work'
    corpus=workspace/'corpus'; corpus.mkdir(); (corpus/'toy.log').write_text('2026-09-06 INFO toy operation complete\n')
    home=root/'home'; home.mkdir(); rawhooks=root/'raw-hooks'; rawhooks.mkdir()
    skill=a.skill.resolve(); helper=load('pause_life',a.helper); driver=load('pause_drive',a.driver)
    nonce='stage-pause-fixture-20260906'; boot=helper.current_boot_id(); key=b'p'*32
    observer=helper.init_segment(root,nonce,boot,capability=key)
    helper.publish_observation(observer,nonce,boot,sequence=0,monotonic_ns=time.monotonic_ns(),capability=key)
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL')}
    env.update(HOME=str(home),QWEN_HOME=str(home),QWEN_SKILL_ROOT=str(skill),
               SHERLOCK_STRICT_MARKER_LIFECYCLE='1',TERM='xterm-256color',OPENAI_API_KEY='loopback-only',NO_PROXY='127.0.0.1,localhost')
    cp=subprocess.run([sys.executable,str(skill/'tools/logmap.py'),str(corpus),'--out',str(work),'--single-host'],cwd=workspace,env=env,capture_output=True)
    (root/'logmap.stdout').write_bytes(cp.stdout); (root/'logmap.stderr').write_bytes(cp.stderr)
    if cp.returncode: raise RuntimeError('toy logmap failed')
    wl=work/'worklist.tsv'; lines=wl.read_text().splitlines()
    for i,line in enumerate(lines):
        if line and not line.startswith('#'):
            cols=line.split('\t'); cols[1]='N toy.log:1 «toy operation complete» n=1 fixture'; lines[i]='\t'.join(cols)
    wl.write_text('\n'.join(lines)+'\n')
    cp=subprocess.run([sys.executable,str(skill/'tools/checkpoint.py'),'init','--work',str(work)],cwd=workspace,env=env,capture_output=True)
    (root/'checkpoint-init.stdout').write_bytes(cp.stdout); (root/'checkpoint-init.stderr').write_bytes(cp.stderr)
    if cp.returncode: raise RuntimeError('toy checkpoint init failed')
    bridge=root/'bridge.py'; bridge.write_text(BRIDGE)
    hooks={}
    for event in ('SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','PostToolUseFailure','PostToolBatch','Stop'):
        target=[sys.executable,str(skill/'tools/stopcheck.py')] if event=='Stop' else [sys.executable,str(a.helper.resolve()),'hook','--observer-dir',str(observer),'--workspace',str(workspace),'--nonce',nonce,'--boot-id',boot]
        command=shlex.join([sys.executable,str(bridge),str(rawhooks),event]+target)
        hooks[event]=[{'hooks':[{'type':'command','command':command,'timeout':60000}]}]
    qdir=workspace/'.qwen'; qdir.mkdir(); (qdir/'settings.json').write_text(json.dumps({'hooks':hooks})+'\n')
    requests=[]; provider_events=[]; errors=[]; inflight={}; lock=threading.Lock()
    def update_inflight(index, add):
        with lock:
            if add: inflight[str(index)]={'fixture':True}
            else: inflight.pop(str(index),None)
            p=root/'upstream-inflight.json'
            if inflight:
                tmp=root/'inflight.tmp'; tmp.write_text(json.dumps({'requests':inflight})); tmp.replace(p)
            else: p.unlink(missing_ok=True)
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['Content-Length']))
            with lock: index=len(requests); requests.append(raw)
            (root/('request-%02d.json'%index)).write_bytes(raw); update_inflight(index,True)
            try:
                if index==0:
                    command=shlex.join([sys.executable,str(skill/'tools/checkpoint.py'),'handoff','--work',str(work),'--done','triage'])+' && sleep 2'
                    delta={'role':'assistant','tool_calls':[{'index':0,'id':'call_pause','type':'function','function':{'name':'run_shell_command','arguments':json.dumps({'command':command})}}]}; finish='tool_calls'
                    helper.register_expected_tools(observer,nonce,boot,'fixture-response-0',['call_pause'])
                else:
                    # A boundary already exists while this response remains live.
                    time.sleep(3)
                    delta={'role':'assistant','content':(work/'handoff.txt').read_text()}; finish='stop'
                parts=[]
                for d,f in ((delta,None),({},finish)):
                    row={'id':'pause-%d'%index,'object':'chat.completion.chunk','created':int(time.time()),'model':'mock','choices':[{'index':0,'delta':d,'finish_reason':f}]}
                    parts.append(b'data: '+json.dumps(row).encode()+b'\n\n')
                sse=b''.join(parts)+b'data: [DONE]\n\n'
                (root/('response-%02d.sse'%index)).write_bytes(sse)
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(sse)));self.end_headers();self.wfile.write(sse);self.wfile.flush()
                provider_events.append({'index':index,'completed_at_ns':time.time_ns()})
            except BaseException as exc: errors.append(repr(exc))
            finally: update_inflight(index,False)
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    env['OPENAI_BASE_URL']='http://127.0.0.1:%d/v1'%server.server_port
    argv=['node',str(a.qwen.resolve()),'--auth-type','openai','--model','mock','--approval-mode','yolo']
    (root/'argv.json').write_text(json.dumps(argv)+'\n')
    session=driver.Session(argv,str(workspace),env,str(root/'pty-transcript.raw'))
    events=[]; result={'passed':False,'provider':'loopback-only',
        'source_sha256':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in
                         [('qwen',a.qwen),('helper',a.helper),('driver',a.driver),('fixture',Path(__file__))]},
        'skill_files':{str(p.relative_to(skill)):hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(skill.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}}

    def alarm(*args): raise TimeoutError('fixture watchdog')
    prior=signal.signal(signal.SIGALRM,alarm); signal.alarm(100)
    def rows(event):
        return [(p,json.loads(p.read_bytes())) for p in sorted(rawhooks.glob('*-'+event+'.input.json'))]
    def note(kind,detail):
        events.append({'event':kind,'detail':detail,'at_ns':time.time_ns()})
        for path in sorted(rawhooks.glob('*-Stop.output.json')):
            try: output=json.loads(path.read_bytes())
            except json.JSONDecodeError: continue
            if output.get('decision')=='block':
                raise AssertionError('real Stop rejected durable handoff: '+output.get('reason',''))
    try:
        while not rows('SessionStart'): session.pump(.2)
        session.pump(.5); session.type('Run the toy triage handoff tool, then print its handoff block.',settle=.1)
        while not (work/'handoff.txt').exists(): session.pump(.2)
        started=time.time_ns()
        driver.wait_monitored_idle(session,observer,nonce,'/clear',note,.25,.5,boundary_work=work)
        ready=time.time_ns()
        stop_rows=rows('Stop')
        if not stop_rows: raise AssertionError('idle before Stop hook')
        stop_path,stop=stop_rows[-1]; output=json.loads(stop_path.with_name(stop_path.name.replace('.input.json','.output.json')).read_bytes())
        marker=workspace/'.sherlock/active.json'
        if not marker.is_file(): raise AssertionError('stage pause retired active marker')
        if output.get('decision')!='allow' or 'handoff' not in output.get('reason','').lower(): raise AssertionError('stage pause allowance missing')
        anchor=driver.capture_clear_anchor(observer,nonce)
        clear_at=time.time_ns();session.type('/clear',settle=.1)
        while not any(r.get('source')=='clear' for _,r in rows('SessionStart')): session.pump(.2)
        clear=[r for _,r in rows('SessionStart') if r.get('source')=='clear'][-1]
        result.update(passed=True,wait_started_at_ns=started,idle_at_ns=ready,clear_sent_at_ns=clear_at,
                      stop_input=stop,stop_output=output,marker_preserved=marker.is_file(),
                      clear_session_id=clear['session_id'],old_session_id=anchor['session_id'])
        if clear['session_id']==anchor['session_id']: raise AssertionError('clear did not change session')
        if len(requests)!=2: raise AssertionError('unexpected continuation count: %d'%len(requests))
        if not events: raise AssertionError('delayed busy path was not exercised')
        if any(e['completed_at_ns']>=clear_at for e in provider_events): raise AssertionError('clear overtook provider completion')
    except BaseException as exc: result.update(passed=False,error=repr(exc))
    finally:
        signal.alarm(0);signal.signal(signal.SIGALRM,prior);session.close();server.shutdown();server.server_close();thread.join(5)
        result.update(request_count=len(requests),provider_events=provider_events,provider_errors=errors,driver_events=events)
        if errors: result['passed']=False
        (root/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result,indent=2,sort_keys=True));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
