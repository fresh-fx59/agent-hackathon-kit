#!/usr/bin/env python3
"""Capture installed Qwen PostToolBatch input for a prevalidation-rejected shell call."""
import argparse, hashlib, json, os
from pathlib import Path
import subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def once(p, b):
 p.parent.mkdir(parents=True, exist_ok=True); fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'wb') as f: f.write(b); f.flush(); os.fsync(f.fileno())

def capture():
 raw=sys.stdin.buffer.read(); out=Path(os.environ['QWEN_BATCH_CAPTURE']); once(out,raw); once(out.with_suffix('.sha256'),(hashlib.sha256(raw).hexdigest()+'\n').encode())

def run(qwen,out):
 out.mkdir(mode=0o700); workspace=out/'workspace'; workspace.mkdir(); home=workspace/'home'; home.mkdir()
 settings={'hooks':{'PostToolBatch':[{'hooks':[{'type':'command','command':f'{sys.executable} {Path(__file__).resolve()} --capture','timeout':10000}]}]}}
 (workspace/'.qwen').mkdir(); (workspace/'.qwen/settings.json').write_text(json.dumps(settings)+'\n')
 requests=[]
 class H(BaseHTTPRequestHandler):
  def log_message(self,*a): pass
  def do_POST(self):
   raw=self.rfile.read(int(self.headers['Content-Length'])); i=len(requests); requests.append(i); once(out/f'request-{i}.json',raw)
   if i==0:
    delta={'role':'assistant','tool_calls':[{'index':0,'id':'call_invalid_directory','type':'function','function':{'name':'run_shell_command','arguments':json.dumps({'command':'printf invalid','directory':'/outside-fixture-workspace'})}}]}; finish='tool_calls'
   else: delta={'role':'assistant','content':'done'}; finish='stop'
   rows=[{'id':f'x{i}','object':'chat.completion.chunk','created':int(time.time()),'model':'mock','choices':[{'index':0,'delta':delta,'finish_reason':None}]},{'id':f'x{i}','object':'chat.completion.chunk','created':int(time.time()),'model':'mock','choices':[{'index':0,'delta':{},'finish_reason':finish}]}]
   body=b''.join(b'data: '+json.dumps(x).encode()+b'\n\n' for x in rows)+b'data: [DONE]\n\n'; once(out/f'response-{i}.sse',body)
   self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
 server=ThreadingHTTPServer(('127.0.0.1',0),H); t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
 env={'PATH':os.environ['PATH'],'HOME':str(home),'QWEN_HOME':str(home),'OPENAI_API_KEY':'loopback-only','OPENAI_BASE_URL':f'http://127.0.0.1:{server.server_port}/v1','NO_PROXY':'127.0.0.1,localhost','QWEN_BATCH_CAPTURE':str(out/'post-tool-batch.stdin.json')}
 p=subprocess.run([str(qwen),'--auth-type','openai','--model','mock','--approval-mode','yolo','--output-format','json','-p','Attempt the supplied tool then finish.'],cwd=workspace,env=env,text=True,capture_output=True,timeout=30)
 once(out/'stdout.json',p.stdout.encode()); once(out/'stderr.txt',p.stderr.encode()); server.shutdown(); server.server_close(); t.join()
 raw=(out/'post-tool-batch.stdin.json').read_bytes(); x=json.loads(raw)
 summary={'exit_code':p.returncode,'requests':len(requests),'batch_sha256':hashlib.sha256(raw).hexdigest(),'keys':sorted(x),'tool_calls':x.get('tool_calls'),'hook_event_name':x.get('hook_event_name')}
 once(out/'summary.json',(json.dumps(summary,indent=2)+'\n').encode()); print(json.dumps(summary,indent=2)); return 0
if __name__=='__main__':
 ap=argparse.ArgumentParser(); ap.add_argument('--capture',action='store_true'); ap.add_argument('--qwen',type=Path); ap.add_argument('--output',type=Path)
 a=ap.parse_args(); raise SystemExit(capture() if a.capture else run(a.qwen,a.output))
