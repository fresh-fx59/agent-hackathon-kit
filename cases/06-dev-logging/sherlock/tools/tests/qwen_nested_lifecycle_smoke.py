#!/usr/bin/env python3
"""Provider-free installed-Qwen nested lifecycle smoke.

This deliberately runs only against a frozen lifecycle-supervisor snapshot. It
uses a localhost scripted SSE server and an isolated Qwen HOME; no provider,
credential, Sherlock skill, or production configuration is involved.
"""
import argparse, hashlib, importlib.util, json, os, signal, subprocess, sys
import threading, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HELPER = ROOT / "eval/bench/lifecycle-supervisor.py"
MODES = ("normal", "capped", "missing-stop", "missing-parent-post", "missing-inner-batch")

BRIDGE = r'''import argparse, json, os, pathlib, subprocess, sys, time
p=argparse.ArgumentParser(); p.add_argument('--helper',required=True); p.add_argument('--observer',required=True); p.add_argument('--nonce',required=True); p.add_argument('--boot',required=True); p.add_argument('--mode',required=True); p.add_argument('--raw-dir',required=True); a=p.parse_args()
raw=sys.stdin.buffer.read(); root=pathlib.Path(a.raw_dir); root.mkdir(mode=0o700,parents=True,exist_ok=True)
path=root/(str(time.time_ns())+'.input.json'); path.write_bytes(raw)
event=json.loads(raw); name=event.get('hook_event_name'); call=event.get('tool_call_id')
ids=[x.get('tool_call_id') for x in event.get('tool_calls', []) if isinstance(x,dict)]
skip=(a.mode=='missing-stop' and name=='SubagentStop') or (a.mode=='missing-parent-post' and name=='PostToolUse' and call=='call_parent_agent') or (a.mode=='missing-inner-batch' and name=='PostToolBatch' and 'call_child_shell' in ids)
if skip:
 out={'continue':True,'hookSpecificOutput':{'hookEventName':name}}; meta={'skipped':True,'returncode':0,'stdout':json.dumps(out),'stderr':''}
else:
 argv=[sys.executable,a.helper,'hook','--observer-dir',a.observer,'--workspace',os.getcwd(),'--nonce',a.nonce,'--boot-id',a.boot]
 cp=subprocess.run(argv,input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 out_raw=cp.stdout or json.dumps({'continue':False,'stopReason':'empty helper output'}).encode()
 try: out=json.loads(out_raw)
 except json.JSONDecodeError: out={'continue':False,'stopReason':'invalid helper output'}
 meta={'skipped':False,'returncode':cp.returncode,'stdout':cp.stdout.decode(errors='replace'),'stderr':cp.stderr.decode(errors='replace')}
(path.with_suffix('.output.json')).write_text(json.dumps({'output':out,'helper':meta},sort_keys=True)+'\n')
print(json.dumps(out))
'''

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def once(path, value):
    data = value.encode() if isinstance(value, str) else value
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
def load(path):
    spec=importlib.util.spec_from_file_location("nested_helper",path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
def kill_group(proc, pgid):
    if proc is None or pgid is None: return
    try: os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError: return
    try: proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try: os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError: pass
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: pass

def sse(index, delta, finish):
    rows=[]
    for part, reason in ((delta,None), ({},finish)):
        rows.append({'id':f'nested-{index}','object':'chat.completion.chunk','created':int(time.time()),'model':'mock','choices':[{'index':0,'delta':part,'finish_reason':reason}]})
    return b''.join(b'data: '+json.dumps(row).encode()+b'\n\n' for row in rows)+b'data: [DONE]\n\n'

def run_case(qwen, output, helper_path, mode):
    trace=output/mode; trace.mkdir(mode=0o700); workspace=trace/'workspace'; workspace.mkdir(mode=0o700); home=workspace/'home'; home.mkdir(mode=0o700)
    helper=load(helper_path); nonce=(mode+'-nested-20260906').ljust(24,'x'); boot=helper.current_boot_id(); key=b'n'*32
    observer=helper.init_segment(trace,nonce,boot,capability=key); helper.publish_observation(observer,nonce,boot,sequence=0,monotonic_ns=time.monotonic_ns(),capability=key)
    turns=1 if mode=='capped' else 2
    agent_dir=workspace/'.qwen/agents'; agent_dir.mkdir(parents=True)
    (agent_dir/'mock-triage.md').write_text(f'---\nname: mock-triage\ndescription: lifecycle fixture\napprovalMode: yolo\nmaxTurns: {turns}\n---\nReply briefly.\n')
    bridge=trace/'hook_bridge.py'; bridge.write_text(BRIDGE); bridge.chmod(0o700)
    hook=(f'{sys.executable} {bridge} --helper {helper_path} --observer "$SHERLOCK_OBSERVER_DIR" --nonce "$SHERLOCK_RUN_NONCE" --boot "$SHERLOCK_BOOT_ID" --mode {mode} --raw-dir {trace}/raw-hooks')
    events=('PreToolUse','PostToolUse','PostToolUseFailure','PostToolBatch','SubagentStart','SubagentStop','UserPromptSubmit')
    (workspace/'.qwen/settings.json').write_text(json.dumps({'hooks':{x:[{'matcher':'*','hooks':[{'type':'command','command':hook,'timeout':10000}]}] for x in events}},sort_keys=True)+'\n')
    requests=[]; errors=[]; registered=threading.Event(); client=None
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            nonlocal client
            raw=self.rfile.read(int(self.headers['Content-Length'])); i=len(requests); requests.append(i); once(trace/f'request-{i}.json',raw)
            ref=hashlib.sha256(raw).hexdigest()
            if i:
                try: helper.check_dispatch(observer,nonce,boot,request_sha256=ref)
                except helper.LifecycleFault as exc:
                    errors.append({'index':i,'reason':exc.reason,'detail':exc.detail}); self.send_error(503,exc.reason); kill_group(client, client_pgid); return
            if i==0:
                calls=[{'index':0,'id':'call_parent_agent','type':'function','function':{'name':'agent','arguments':json.dumps({'description':'nested lifecycle','subagent_type':'mock-triage','run_in_background':False,'prompt':'run a shell command then finish'})}}, {'index':1,'id':'call_parent_sibling','type':'function','function':{'name':'run_shell_command','arguments':json.dumps({'command':'printf sibling > sibling-marker.txt'})}}]
                helper.register_expected_tools(observer,nonce,boot,ref,['call_parent_agent','call_parent_sibling']); registered.set(); body=sse(i,{'role':'assistant','tool_calls':calls},'tool_calls')
            elif i==1:
                call={'index':0,'id':'call_child_shell','type':'function','function':{'name':'run_shell_command','arguments':json.dumps({'command':'printf child > child-marker.txt'})}}
                helper.register_expected_tools(observer,nonce,boot,ref,['call_child_shell']); body=sse(i,{'role':'assistant','tool_calls':[call]},'tool_calls')
            else:
                body=sse(i,{'role':'assistant','content':'done'},'stop')
            once(trace/f'response-{i}.sse',body); self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','TMPDIR')}; env.update(HOME=str(home),QWEN_HOME=str(home),OPENAI_API_KEY='loopback-only',OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',NO_PROXY='127.0.0.1,localhost',SHERLOCK_OBSERVER_DIR=str(observer),SHERLOCK_RUN_NONCE=nonce,SHERLOCK_BOOT_ID=boot,SHERLOCK_LIFECYCLE_HELPER=str(helper_path))
    argv=[str(qwen.resolve()),'--auth-type','openai','--model','mock','--approval-mode','yolo','--output-format','json','-p','spawn the supplied agent and finish']
    once(trace/'launch.json',json.dumps({'argv':argv,'cwd':str(workspace),'qwen_sha256':digest(qwen),'lifecycle_helper_sha256':digest(helper_path),'provider':'loopback-only'},sort_keys=True)+'\n')
    stdout=stderr=''; guardian=None; guardian_out=guardian_err=''; exception=None; client_pgid=None
    try:
        client=subprocess.Popen(argv,cwd=workspace,env=env,text=True,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        client_pgid=os.getpgid(client.pid)
        if not registered.wait(15): raise RuntimeError('parent response was not registered')
        guardian_argv=[sys.executable,str(helper_path),'guardian','--observer-dir',str(observer),'--nonce',nonce,'--boot-id',boot,'--controller-pid',str(client.pid),'--controller-start-ticks',str(helper.process_start_ticks(client.pid)),'--controller-pgid',str(client_pgid),'--interval','0.02']
        once(trace/'guardian-launch.json',json.dumps({'argv':guardian_argv})+'\n'); guardian=subprocess.Popen(guardian_argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        stdout,stderr=client.communicate(timeout=45)
    except BaseException as exc:
        exception={'type':type(exc).__name__,'detail':str(exc)}; kill_group(client, client_pgid)
        if client is not None:
            try: stdout,stderr=client.communicate(timeout=2)
            except subprocess.TimeoutExpired: pass
    finally:
        alive=guardian is not None and guardian.poll() is None
        if guardian is not None:
            guardian.terminate()
            try: guardian_out,guardian_err=guardian.communicate(timeout=5)
            except subprocess.TimeoutExpired: guardian.kill(); guardian_out,guardian_err=guardian.communicate(timeout=2)
        server.shutdown(); server.server_close(); thread.join()
    once(trace/'stdout.json',stdout); once(trace/'stderr.txt',stderr); once(trace/'guardian.stdout.txt',guardian_out); once(trace/'guardian.stderr.txt',guardian_err)
    fault=json.loads((observer/'fault.json').read_text()).get('reason') if (observer/'fault.json').exists() else None
    result={'mode':mode,'child_max_turns':turns,'exit_code':None if client is None else client.returncode,'requests':len(requests),'successful_responses':len(list(trace.glob('response-*.sse'))),'dispatch_errors':errors,'fault':fault,'exception':exception,'guardian_alive_at_client_completion':alive,'lifecycle_helper_sha256':digest(helper_path),'qwen_sha256':digest(qwen),'raw_hook_files':len(list((trace/'raw-hooks').glob('*.input.json'))),'client_pgid':client_pgid}
    once(trace/'result.json',json.dumps(result,indent=2)+'\n'); return result

def main():
    p=argparse.ArgumentParser(); p.add_argument('--qwen',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--helper',type=Path,default=DEFAULT_HELPER); a=p.parse_args(); a.output.mkdir(mode=0o700)
    rows=[run_case(a.qwen,a.output,a.helper.resolve(strict=True),m) for m in MODES]
    by={row['mode']:row for row in rows}
    normal=by['normal']; capped=by['capped']
    ok=(normal['exit_code']==0 and normal['requests']==4 and normal['successful_responses']==4 and normal['fault'] is None and not normal['dispatch_errors'] and normal['guardian_alive_at_client_completion'] and normal['exception'] is None)
    ok=ok and (capped['exit_code']==0 and capped['requests']==3 and capped['successful_responses']==3 and capped['fault'] is None and not capped['dispatch_errors'] and capped['guardian_alive_at_client_completion'] and capped['exception'] is None)
    expected={'missing-stop':{'SUBAGENT_STOP_MISSING'},'missing-parent-post':{'HOOK_PAIR_MISSING','EXPECTED_TOOL_BATCH_MISSING'},'missing-inner-batch':{'EXPECTED_TOOL_BATCH_MISSING'}}
    negatives={}
    for mode,reasons in expected.items():
        row=by[mode]; negatives[mode]={'fault':row['fault'],'dispatch_errors':row['dispatch_errors'],'successful_responses':row['successful_responses']}
        # A negative may fault in its hook (no next request) or at dispatch; in
        # either case it must not receive the root continuation SSE response.
        ok=ok and row['fault'] in reasons and row['successful_responses'] <= 3 and row['exception'] is None
    summary={'rows':rows,'negative_assertions':negatives,'pass':ok}
    once(a.output/'summary.json',json.dumps(summary,indent=2)+'\n'); print(json.dumps(summary,indent=2))
    return 0 if ok else 1
if __name__=='__main__': raise SystemExit(main())
