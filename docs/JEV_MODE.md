# Jev mode in Strata - /v1/decision

**Jev mode** is a way of asking a model that answers by *choosing* instead of *writing*: you give it
instructions, the current state as text, and a schema whose every field has a finite set of allowed
values (enums, booleans, bounded integers, numbers on a grid), and it returns one answer per field in
one pass - each with a probability. Answers arrive in tens of milliseconds, always match the schema,
and never ramble. The name comes from Jev, a commercial decision engine that answers this way;
**Codacus** (thecodacus on GitHub) proved the same "way of asking" works on everyday GGUF models by
adding it to llama.cpp as the `parallel-decision` branch: a new `POST /v1/decision` endpoint on
llama-server that scores every allowed value as token paths forked from one cached context, all in a
single batched decode. (Video: "Can You Run Any LLM in Jev Mode Using llama.cpp?" -
youtube.com/watch?v=bcGO7xre46o; branch: github.com/thecodacus/llama.cpp `parallel-decision`;
UI: github.com/thecodacus/decision-playground. The idea's open proof came from Harsha's Apache-2.0
fine-tunes on Hugging Face (namespace harshatheg); the ecosystem around it includes models like
Qwen3-1.7B-Jev and guides for building your own.)

## How Strata serves it

Strata's own engine answers one sequence at a time (one chat request in flight, custom decode
kernels, MTP speculation) - the parallel branch scoring needs llama.cpp's unified KV cache and many
sequence slots, which is exactly what the fork adds. So Strata exposes the endpoint and routes it to
that backend:

```text
 your app / the decision-playground
        |  POST /v1/decision   (schema + instructions + up to 256 contexts)
        v
 Strata's server (:8080)  ------------------ proxies, adds CORS/API-key handling,
        |                                    advertises it in GET /v1/status
        v                                   (the big model is NOT touched: no queue,
 llama-server from the parallel-decision      no load/unload, chat keeps running)
 branch (:8096, --decision-seqs N), a small
 model on the spare VRAM - answers every field
 in one batched pass with probabilities
```

A decision never enters Strata's request queue and never loads or unloads the big model: it runs
while Swift/Qwen is idle, loading, mid-chat for another client, or unloaded. The backend is a second
process (a 1-2B model needs ~2-3 GB of VRAM - the `--idle-unload` / `min_free_vram_mib` settings
make room on busy cards).

## Turn it on (this PC)

1. Start the decision backend (the llama.cpp fork built at D:\AI\codacus_llamacpp):

   ```bat
   start-jev-backend.bat        (in C:\AI\Strata - runs Qwen3.5-2B on port 8096)
   ```

   ...which is exactly:

   ```bat
   "D:\AI\codacus_llamacpp\llama.cpp\build\bin\Release\llama-server.exe" ^
     -m "D:\AI\llama_cpp\llama-b9444-bin-win-cuda-12.4-x64\Qwen3.5-2B-Q6_K.gguf" ^
     --jinja -ngl 99 -fa on -c 16384 -np 1 --decision-seqs 24 --host 127.0.0.1 --port 8096
   ```

2. Start Strata as always (`START-HERE.bat`). The run config already has:

   ```json
   "decision_url": "http://127.0.0.1:8096",
   "cors_origins": ["http://localhost:5173", "http://127.0.0.1:5173"]
   ```

   `decision_url` can also be set with `--decision-url` or `$STRATA_DECISION_URL`;
   `decision_timeout_s` (default 120) caps one proxied request. With no backend set,
   `/v1/decision` answers a 503 that explains how to start it - nothing else about Strata changes.

3. Ask it things:

   ```bash
   curl http://127.0.0.1:8080/v1/decision -H "Content-Type: application/json" -d '{
     "instructions": "Answer each question about this support request from its state.",
     "schema": {
       "category": {"type": "enum", "choices": ["billing","technical","cancellation","other"]},
       "urgent":   {"type": "boolean"},
       "priority": {"type": "enum", "choices": ["low","medium","high","critical"]}
     },
     "contexts": ["I was charged twice and need this fixed today."]
   }'
   ```

   The request and response follow the fork's endpoint exactly (`contexts`: 1-256 strings sharing
   one schema; `mode`: auto/tree/greedy, `tree_max`, `cache_prompt`; numeric fields take
   `aggregate`: mode/median/mean). `GET /v1/status` lists `/v1/decision` in its dialects and shows
   the backend. The body is forwarded unchanged - in router mode (llama-server with
   `--models-preset`) the `model` field picks which model decides; a plain server ignores it.

4. Use the playground: clone github.com/thecodacus/decision-playground, set its server URL to Strata
   (`http://127.0.0.1:8080`, or `VITE_DEFAULT_SERVER=http://127.0.0.1:8080 npm run dev`). The CORS
   origins above let the page at localhost:5173 call through Strata; add the playground's origin to
   `cors_origins` if you serve it elsewhere. Measured on this PC (Qwen3.5-2B-Q6_K, warm cache):
   5-field arena preset with a 91-value integer field - **89 rows scored, ~155 ms server / 184 ms
   round trip through Strata**; support-ticket presets: ~32 ms per decision.

## What was changed for it

| File | Change |
| --- | --- |
| `serve/server.py` | `/v1/decision` route + `_decision()` proxy (validation, 503 help messages, timeout); `--decision-url`; `decision_url` / `decision_timeout_s` config keys; `/v1/status` advertises the dialect and backend |
| `strata-<model>.json` | `decision_url: http://127.0.0.1:8096`, playground `cors_origins` (setup.py writes these keys through untouched) |
| `start-jev-backend.bat` | starts the fork's llama-server with `--decision-seqs 24` |

The endpoint inherits Strata's API-key rule (`/v1/*`) and its CORS policy (#321): pages in
`cors_origins` only, preflight included. It is deliberately a proxy: the fork's engine is a moving
branch, and this keeps the install update-safe (an upstream `git pull` of Strata never conflicts
with it).

## Where it fits next to the big model

- **Reflexes**: game agents, routing, triage, per-request moderation, smart-home rules - tens of ms
  per decision, in bulk (up to 256 contexts share one cached prefix; warm cache: only the context's
  tail is read).
- **The big model stays the writer**: chat/code keep using Swift/Qwen at `/v1/chat/completions`;
  Jev answers are cheap structured signals you feed into code - or into Strata's next prompt.
- Limits to know (from the fork): attention-only models score in one pass; hybrid/recurrent models
  (Qwen3.5-class, and Swift/Qwen3.8-Flash-Next) work but batch in several passes; schema values must
  be finite (no free text); a low `probability` is honest uncertainty - the pick may simply be a
  close call, not a bug.

## Using it from other AI tools (MCP): `strata_decide`

`tools/strata_mcp.py` is a stdio MCP server (stdlib Python, no setup needed). Besides managing Strata
(status/start/stop/logs/benchmark) it now serves **`strata_decide`**: the Jev reflex as one tool call.
Any MCP-capable agent can use it - copy-paste setups:

**Claude Code:**  `claude mcp add strata -- python C:\AI\Strata-Jev\tools\strata_mcp.py`

**Claude Desktop / Cursor (json):**

```json
{ "mcpServers": { "strata": { "command": "python",
                   "args": ["C:\\AI\\Strata-Jev\\tools\\strata_mcp.py"] } } }
```

(VS Code uses `"servers"` with `"type": "stdio"`; Cursor: `.cursor/mcp.json`, Claude Desktop:
`claude_desktop_config.json`.). **DeepSeek Harness:** pick the **Jev mode reflex** preset - it mounts
this server as native tools (`mcp__strata__decide`, `mcp__strata__status`, ...) and the `jev-reflex`
skill teaches every session when to reach for them.

**Call payload (the whole contract - works verbatim in any client):**

```json
{ "instructions": "Answer each question about this machine's state.",
  "schema": {
    "is_safe": {"type": "boolean", "description": "Is it safe to run the update now?"},
    "service": {"type": "enum", "choices": ["running","stopped"], "description": "Is strata running or stopped?"},
    "reach":   {"type": "enum", "choices": ["online","offline"], "description": "Is the server reachable?"}
  },
  "contexts": ["GET /health answered 200 in 3 ms; GPU idle; no jobs queued.",
               "connection refused on 8080; no process listening."] }
```

Answer: `{decisions: [...], fields: [...], verdicts: [...], timings}`. **Agents should read
`verdicts`** - one entry per field with the chosen value + its percentage, the **runner-up's value and
its percentage**, the gap between them (`margin`) and a `confidence` label:

| margin (top1 − top2) | confidence | what your agent should do |
|---|---|---|
| >= 50 pts | `sure` | act on it, no second opinion needed |
| >= 20 pts | `confident` | act; note the odds in logs |
| >= 10 pts | `likely` | fine for cheap/reversible actions |
| < 10 pts  | `coin_flip` | **the model barely separated the options** - verify, re-ask with a sharper context, or escalate to a bigger model |

Real output from `tools/jev_decide_demo.py`:

```
is_safe=false at 73.0%, runner-up true at 27.0% -> confident
service="stopped" at 99.4%, runner-up "running" at 0.6% -> sure
```

A 21% winner among five ~20% options is answered with the winner but labeled `coin_flip`: the tool
answers confidently **and** tells you it isn't - so a dumb agent can escalate instead of acting blind.
`fields[].probabilities` carries the exact probability of every allowed value (tree mode); greedy mode
scores only the chosen path, so margins are lower bounds marked with `~` (e.g. `confident~`).

**Rules a strict backend enforces (violations answer with a clear error):** every field needs a
`description`; types are only enum/boolean/integer(min,max)/number(min,max,step); values must be
finite; `contexts` = 1-256 strings. A low probability is a close call, not a failure.

A runnable example client: **`tools/jev_decide_demo.py`** (`python tools/jev_decide_demo.py`) - the
three messages that matter are initialize -> tools/list -> tools/call; copy it into any app.


## Real-time control loops (visual browsing, games, on-site navigation)

Actions that need many small decisions per second - move a pointer, "is it over the button?",
click/wait/keep moving, game controls - run as a **reflex loop in a sub-agent**, not on the big model:

1. Each tick probes N target states (e.g. `WindowFromPoint` class + title under each point - dry-run
   needs NO cursor movement, your mouse stays free).
2. **ONE** Jev call scores all N points x all fields in a single parallel decode (~50-100 ms for
   several points; up to 256 contexts per call) - `over_button`, `click_now`, `action` answered together.
3. Act only on `sure`/`confident`. A `coin_flip` means the reflex is blind there: re-scan with a sharper
   context (screenshot region, control name) or hand that point to the smarter model - never act blind.

Delegation split: the big model does screenshots, target discovery, strategy and coin_flips; the loop
runs the frames at ~10 ticks/s. The LLM never writes prose in between - it picks from finite sets -
which is what makes visual browsing noticeably faster.

```bash
python tools/jev_cursor_demo.py --targets "400,300;960,540" --ticks 3   # dry-run: touches nothing
python tools/jev_cursor_demo.py --targets "400,300" --click             # real left-clicks on sure/confident click_now
```

**`tools/jev_cursor_demo.py`** is the runnable Windows demo (stdlib only): probe -> decide -> act,
verdict-gated. Swap `WindowFromPoint` for a screenshot tile or game pixel readout and the same loop
drives any visual agent.

**`tools/jev_start_menu_demo.py`** - watch-it-run version: finds every taskbar's Start button across
monitors (DPI-aware), flies the cursor to each, asks the reflex in one parallel call, clicks the
sure/confident ones and closes the menu with Esc after a preview delay. Real multi-monitor output:

```
found 2 Start button(s): (1387,1416) [primary], (3632,1656) [secondary]
one parallel reflex call for 2 points in 597 ms (incl. cursor moves):
  (1387,1416) over_button=true at 71.3% ... | click_now=true at 89.2% -> sure | action="click" -> sure
    -> CLICKED [sure] - start menu opening NOW on that monitor
```

## Native engine roadmap (Jev scored by Strata's own engine)

Scoring by Strata's engine itself (one model in memory, no second process) is a real option; the
primitives already exist: `src/core/conversation_snapshot.*` saves/restores the full KV + MTP state,
the generate path can decode token paths teacher-forced and read logits at any position
(`--dump-logits`, the batched-state scoring used by parity tests), and MTP verifies several tokens
per pass. A sketch:

1. `serve/server.py` renders instructions + schema catalogue + context with the chat template,
   tokenizes (the pack tokenizer is already loaded), builds each field's token trie of candidate
   values - all pieces exist for chat requests today.
2. New `--serve` command `DECIDE <json>` in `src/program/generate.cpp`: prefill the shared prefix
   once into the prompt cache, snapshot at each context's end, then per scored trie node restore ->
   decode the branch tokens teacher-forced -> read logits -> constrained softmax over candidate
   continuations; answer with one `SCORED <json>` line. Sequential (batch-1 engine): ~scored_rows
   decode steps per decision - roughly 0.2-0.5 s for a 12-field preset at 50-90 tok/s, improvable
   by packing branch tokens into the multi-token verify window.
3. `_decision()` then prefers `backend: "strata"` (engine scoring) and keeps the proxy path for
   llama.cpp backends; `/v1/status` reports which is live.

Effort: ~600-900 lines across `generate.cpp` + `serve/server.py`, plus an engine rebuild (CUDA
archs per `engine/BUILD.json`). The proxy path delivers the endpoint today with zero risk to the
engine build; the roadmap is where a single-model reflex (no second VRAM budget) goes.

## Reference links

- Codacus video: https://www.youtube.com/watch?v=bcGO7xre46o ("Can You Run Any LLM in Jev Mode...")
- Fork branch: https://github.com/thecodacus/llama.cpp (branch `parallel-decision`, README at
  `tools/parallel-decision/README.md`)
- Playground: https://github.com/thecodacus/decision-playground (local copy:
  D:\AI\codacus_llamacpp\decision-playground)
- Jev ecosystem: https://huggingface.co/xuhaodev/Qwen3-1.7B-Jev , https://huggingface.co/AlexWortega/openjev
