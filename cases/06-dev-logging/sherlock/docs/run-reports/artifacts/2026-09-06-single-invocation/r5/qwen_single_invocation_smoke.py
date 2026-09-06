#!/usr/bin/env python3
"""Installed Qwen: full driver, discovered skill, combined arguments, real Stop.

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
import shutil
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

class FixtureDeadline(BaseException):
    """Escape driver's recoverable OSError handling; never used in real runs."""

def main():
    ap = argparse.ArgumentParser()
    for arg in ('output', 'qwen', 'helper', 'driver', 'skill'):
        ap.add_argument('--'+arg, type=Path, required=True)
    a = ap.parse_args()
    root=a.output.resolve(); root.mkdir(mode=0o700,parents=True)
    workspace=root/'workspace'; workspace.mkdir(); work=workspace/'work'
    corpus=workspace/'corpus'; corpus.mkdir(); (corpus/'toy.log').write_text('2026-09-06 INFO toy operation complete\n')
    home=root/'home'; home.mkdir(); rawhooks=root/'raw-hooks'; rawhooks.mkdir()
    catalog=root/"skill-catalogue"; catalog.mkdir(); skill=catalog/"log-rca"
    shutil.copytree(a.skill.resolve(),skill,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    helper=load('pause_life',a.helper); driver=load('pause_drive',a.driver)
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
    settings_path=a.driver.resolve().parent/'corporate-settings.py'
    settings_module=load('pause_settings',settings_path)
    hooks=settings_module.run_settings(262000,20000,skill_directory=str(catalog)).get('hooks',{})
    if 'Stop' not in hooks: raise AssertionError('production emitter did not install Stop hook')
    stop_hook=hooks['Stop'][0]['hooks'][0]
    original_stop_command=stop_hook['command']
    (root/'emitted-stop-command.txt').write_text(original_stop_command+'\n')
    stop_hook['command']=shlex.join([sys.executable,str(bridge),str(rawhooks),'Stop','bash','-c',original_stop_command])
    for event in ('SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','PostToolUseFailure','PostToolBatch'):
        target=[sys.executable,str(a.helper.resolve()),'hook','--observer-dir',str(observer),'--workspace',str(workspace),'--nonce',nonce,'--boot-id',boot]
        command=shlex.join([sys.executable,str(bridge),str(rawhooks),event]+target)
        hooks[event]=[{'hooks':[{'type':'command','command':command,'timeout':60000}]}]
    qdir=workspace/'.qwen'; qdir.mkdir()
    (qdir/'settings.json').write_text(json.dumps({'hooks':hooks,'skills':{'directories':[str(catalog)]}})+'\n')
    task='SYNTHETIC-START-ONLY: run the toy triage handoff and end the turn.'
    reseed='SYNTHETIC-RESEED-ONLY: run a partial draft handoff and end the turn.'
    normal_requests=[]
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
                body=json.loads(raw); message=body['messages'][-1]
                content=message.get('content','')
                last_text=content if isinstance(content,str) else ''.join(p.get('text','') for p in content)
                auxiliary=message.get('role')=='user' and last_text.startswith('[SUGGESTION MODE:')
                if auxiliary:
                    delta={'role':'assistant','content':''}; finish='stop'
                else:
                    with lock: ordinal=len(normal_requests);normal_requests.append(index)
                    if ordinal in (0,2):
                        expected=task if ordinal==0 else reseed
                        visible=json.dumps(body['messages'],ensure_ascii=False)
                        if expected not in visible: raise AssertionError('contextless skill request before task/reseed')
                        args=['--done','triage'] if ordinal==0 else ['--done','draft','--partial']
                        command=shlex.join([sys.executable,str(skill/'tools/checkpoint.py'),'handoff','--work',str(work)]+args)+' && sleep 2'
                        tool_id='call_pause_%d'%ordinal
                        delta={'role':'assistant','tool_calls':[{'index':0,'id':tool_id,'type':'function','function':{'name':'run_shell_command','arguments':json.dumps({'command':command})}}]}; finish='tool_calls'
                        helper.register_expected_tools(observer,nonce,boot,'fixture-response-%d'%index,[tool_id])
                    elif ordinal in (1,3):
                        time.sleep(3)
                        delta={'role':'assistant','content':(work/'handoff.txt').read_text()}; finish='stop'
                    else: raise AssertionError('unexpected investigation request %d'%ordinal)
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
    result={'passed':False,'provider':'loopback-only','scope':'full driver startup and first clear; intentional fixture stop after second pause',
        'source_sha256':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in
                         [('qwen',a.qwen),('helper',a.helper),('driver',a.driver),('settings',settings_path),('fixture',Path(__file__))]},
        'skill_files':{str(p.relative_to(skill)):hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(skill.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}}
    events_path=root/'driver-events.jsonl'; finished=threading.Event(); stop_requested=[]
    def rows(event):
        return [(p,json.loads(p.read_bytes())) for p in sorted(rawhooks.glob('*-'+event+'.input.json'))]
    def observe_fixture():
        while not finished.wait(.02):
            if errors:
                os.kill(os.getpid(),signal.SIGTERM);return
            try:
                events=[json.loads(l) for l in events_path.read_text().splitlines()]
                cp=json.loads((work/'checkpoint.json').read_text())
                with lock: quiet=not inflight
                if (quiet and cp.get('boundary_seq',0)>=2 and cp.get('pending_handoff',{}).get('state')=='consumed'
                        and any(e.get('event')=='clear_verified' for e in events)):
                    stop_requested.append(time.time_ns());os.kill(os.getpid(),signal.SIGTERM);return
            except (OSError,ValueError): pass
    watcher=threading.Thread(target=observe_fixture,daemon=True);watcher.start()
    def alarm(*args): raise FixtureDeadline('synthetic fixture watchdog')
    prior=signal.signal(signal.SIGALRM,alarm);signal.alarm(140)
    previous_env=dict(os.environ);os.environ.clear();os.environ.update(env)
    try:
        rc,events=driver.drive(argv,str(workspace),str(work),'/sherlock\n\n'+task,reseed,0,6.0,
            str(root/'pty-transcript.raw'),'/sherlock',events_path=str(events_path),
            observer_dir=str(observer),run_nonce=nonce,clear_idle_wait_s=.25,clear_idle_settle_s=1)
        result.update(driver_exit=rc,driver_events=events)
        if rc!=143 or not stop_requested:raise AssertionError('driver did not reach the intentional fixture stop')
        prompts=[r for _,r in rows('UserPromptSubmit') if 'submitted_prompt' in r]
        if len(prompts)!=2:raise AssertionError('expected exactly startup and combined reseed submissions')
        if prompts[0]['submitted_prompt']!='/sherlock\n\n'+task:raise AssertionError('startup arguments changed')
        if prompts[1]['submitted_prompt']!='/sherlock '+reseed:raise AssertionError('combined reseed not exact')
        clear_rows=[(p,r) for p,r in rows('SessionStart') if r.get('source')=='clear']
        clears=[r for _,r in clear_rows]
        if not clears or prompts[0]['session_id']==prompts[1]['session_id'] or clears[0]['session_id']!=prompts[1]['session_id']:
            raise AssertionError('fresh session correlation missing')
        stops=[]
        for p,e in rows('Stop'):
            output_path=p.with_name(p.name.replace('.input.json','.output.json'))
            o=json.loads(output_path.read_bytes());stops.append({'input':e,'output':o,'completed_at_ns':output_path.stat().st_mtime_ns})
        if len(stops)<2 or any(x['output'].get('decision')!='allow' for x in stops):raise AssertionError('skill-scoped Stop did not accept both pauses')
        clear_ns=int(clear_rows[0][0].name.split('-',1)[0])
        first_stops=[s for s in stops if s['input'].get('session_id')==prompts[0]['session_id']]
        if not first_stops or any(s['completed_at_ns']>=clear_ns for s in first_stops):
            raise AssertionError('first Stop did not complete before clear')
        initial_responses=[p for p in provider_events if p['index'] in normal_requests[:2]]
        if len(initial_responses)!=2 or any(p['completed_at_ns']>=clear_ns for p in initial_responses):
            raise AssertionError('initial provider completion did not precede clear')
        if not (workspace/'.sherlock/active.json').is_file():raise AssertionError('pause retired marker')
        pairs=json.loads((observer/'pairs.json').read_text())['pairs']
        if any(not row.get('post') for row in pairs.values()):raise AssertionError('tool completion missing')
        if len(provider_events)!=len(requests):raise AssertionError('provider completion missing')
        if len(normal_requests)!=4:raise AssertionError('unexpected normal request count')
        result.update(passed=True,submitted_prompts=prompts,stops=stops,marker_preserved=True,clear_sessions=clears)
    except BaseException as exc:result.update(passed=False,error=repr(exc))
    finally:
        finished.set();watcher.join(2);signal.alarm(0);signal.signal(signal.SIGALRM,prior)
        os.environ.clear();os.environ.update(previous_env);server.shutdown();server.server_close();thread.join(5)
        result.update(request_count=len(requests),normal_request_indices=normal_requests,provider_events=provider_events,provider_errors=errors,fixture_stop_requested=stop_requested)
        if errors:result['passed']=False
        (root/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result,indent=2,sort_keys=True));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
