# Integrations: Jenkins, messenger bots, design exports

Three patterns for wiring the kit (or your event build) into the corporate
surroundings. Each is deliberately minimal — copy, adapt names, ship.

## 1. Jenkins: headless agent run gated on a benchmark score

Run the agent CLI in a pipeline stage, score its artifact with the case
benchmark, and fail the build below a threshold. This turns "the agent got
better/worse" into a red/green light — judges and reviewers love it.

> Ready-made implementation of this sketch now ships in the repo:
> `ci/Jenkinsfile` (parameterized pipeline) + `ci/gate.sh` (the score gate,
> also usable locally or in a git hook) — rollout steps in
> `docs/ci-setup-for-agent.md` (RU).

```groovy
pipeline {
  agent any
  environment {
    TRACKER_URL = 'http://127.0.0.1:8801'   // or the real system + token from credentials()
    SCORE_MIN   = '70'
  }
  stages {
    stage('Verify kit') {
      steps { sh 'bash scripts/verify.sh' }
    }
    stage('Run agent headless') {
      steps {
        // Most agent CLIs have a non-interactive mode: a -p/--prompt flag
        // reading the task from argv or stdin. Adapt the binary name.
        sh '''
          python3 mocks/run_all.py & MOCKS=$!
          sleep 2
          agent-cli --non-interactive \
            --mcp-config mcp/configs/all-servers.json \
            --prompt-file tracks/analytics/skill.md \
            --input cases/analytics-meeting/transcript-ru.md \
            --output out/br.md
          kill $MOCKS
        '''
      }
    }
    stage('Benchmark gate') {
      steps {
        sh '''
          SCORE=$(python3 cases/analytics-meeting/benchmark.py out/br.md --score-only)
          echo "benchmark score: $SCORE (min $SCORE_MIN)"
          python3 -c "import sys; sys.exit(0 if float('$SCORE') >= float('$SCORE_MIN') else 1)"
        '''
      }
    }
  }
  post { always { archiveArtifacts artifacts: 'out/**', allowEmptyArchive: true } }
}
```

Notes:

- Keep the gate stage separate from the run stage — a red gate with a green
  run tells you the agent regressed, not the plumbing.
- Store real-system tokens in Jenkins credentials, surface them as env vars
  (`TRACKER_TOKEN`), never in the Jenkinsfile.
- Print the score in a stable `benchmark score: N` line — trend it later by
  grepping build logs.

## 2. Messenger bot: webhook → agent → reply

The corporate messenger can drive the agent: a user sends a request, the bot
runs the pipeline, replies with the artifact link/summary. Stdlib-only
pseudo-code (~30 lines) — adapt field names to the messenger's webhook
payload:

```python
import json, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq

SEND_URL = "https://messenger.internal.example/api/send"  # corporate bot API

def run_agent(text):
    # Non-interactive agent call; returns the artifact/summary as text.
    proc = subprocess.run(
        ["agent-cli", "--non-interactive", "--prompt", text],
        capture_output=True, text=True, timeout=600)
    return proc.stdout.strip() or "agent produced no output"

def send_reply(chat_id, text):
    body = json.dumps({"chat_id": chat_id, "text": text[:4000]}).encode("utf-8")
    req = urlreq.Request(SEND_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "agent-hackathon-kit/0.1")
    urlreq.urlopen(req, timeout=30)

class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        update = json.loads(raw or b"{}")
        chat_id = update.get("chat_id")
        text = update.get("text", "")
        self.send_response(200); self.end_headers()  # ack fast, work after
        if chat_id and text:
            send_reply(chat_id, run_agent(text))

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8899), Hook).serve_forever()
```

Hard-won details:

- **Ack the webhook before running the agent** — messengers retry slow
  webhooks and you'll double-run a 5-minute job. (For production move
  `run_agent` to a worker thread/queue; at a hackathon the ack-first trick
  above is usually enough.)
- Always cap the agent with `timeout=`; a hung CLI must not wedge the bot.
- Truncate replies (`text[:4000]`) — every messenger has a message limit.
- For the demo, this bot is a strong opener: a judge types a request into
  the messenger and watches the tracker issue appear.

## 3. Pixso / design exports as skill input

Design-tool exports are a legitimate case input (e.g. "turn this screen
design into requirements"). The pipeline shape is the standard kit one —
the trick is step 1:

```
design export → structured description (JSON) → skill analysis → requirements/BR
```

1. **Export from the design tool** what you can get without plugins: PNG
   screens plus, ideally, a structural export (Pixso-class tools can export
   the layer tree / frames as JSON or via their open API; even a
   names-and-hierarchy dump is gold).
2. **Normalize to a structured description** the model reads better than a
   raw layer dump — one JSON object per screen:

   ```json
   {"screen": "checkout", "elements": [
     {"type": "input", "label": "Промокод", "state": "optional"},
     {"type": "button", "label": "Оплатить", "action": "submit-order"},
     {"type": "text", "content": "Итого: 2 400 ₽"}]}
   ```

   Write the converter with stdlib `json` — it's a filter over the export,
   ~50 lines. If all you have is PNGs and the model is text-only, a human
   spends 10 minutes writing these JSON descriptions by hand — that is
   still faster and more reliable than OCR hacks.
3. **Feed it to the analytics skill** (`tracks/analytics/skill.md` adapts
   cleanly: the "transcript" becomes the screen descriptions) and produce the
   BR / requirements artifact; write it to the tracker via
   `mcp/tracker_mcp.py` as usual.
4. **Benchmark:** rubric-based, same as the meeting→BR case — required
   sections + required facts ("every interactive element maps to a
   functional requirement"). Reuse `cases/analytics-meeting/benchmark.py`
   with a new `rubric.json`.
