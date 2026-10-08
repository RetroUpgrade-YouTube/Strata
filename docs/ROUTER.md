# Codacus Router (llama-swap) — swarm of small models + ComfyUI on one GPU

One 24 GB card, three endpoints, strict VRAM etiquette. llama-swap hot-swaps the
small-model pool: **one big model at a time, small models may coexist, ComfyUI
runs alone** (it evicts every LLM; when an LLM is requested again it fully
unloads ComfyUI and reloads).

## Endpoints

| Port | Endpoint | Role |
|---|---|---|
| :8080 | **Strata** (swift-1.5-iq3_xxs) | hard tasks, tie-breaker, Jev proxy — own launcher, NOT swapped by the router |
| :8081 | **llama-swap** (Codacus pool) | quick-task swarm + ComfyUI, config `D:\AI\llama_swap\config.yaml` |
| :8096 | Jev decision backend (Qwen3.5-2B) | fast reflexes for Strata; stop it when VRAM is tight |

## Swarm models (all vision-capable via mmproj, all run from the codacus llama.cpp build → `/v1/decision` works on them too)

| Model id | File | Weights+mmproj | Notes |
|---|---|---|---|
| gemma-e2b | Gemma-4-E2B-Uncensored Q6_K_P | ~4.5 GB | fits alongside Strata (tested: 104 tok/s) |
| gemma-e4b | Gemma-4-E4B-Uncensored Q6_K_P | ~6.7 GB | fits with Strata stopped |
| gemma-12b | Gemma4-12B-QAT-Uncensored Q4_K_M | ~7.1 GB | pairs with e2b/e4b in swarm sets |
| qwen-27b | Qwen3.8-27B Q4_K_M | ~16.5 GB | **needs Strata + Jev backend stopped** |
| embed-jina | jina-embeddings-v5-small Q8_0 | ~0.7 GB | `/v1/embeddings` |

Matrix sets: `e2b&g12`, `e4b&g12`, `e2b&e4b`, `q27 alone`, `comfyui alone`.
Idle models auto-unload (ttl 900 s; ComfyUI 3600 s). Force unload:
`POST :8081/api/models/unload/<id>` or `.../api/models/unload` for all.

## Using it from agents

Chat like any OpenAI server — the router swaps models on demand:

```bash
curl http://localhost:8081/v1/chat/completions -d '{"model":"gemma-e2b","messages":[{"role":"user","content":"..."}]}'
# "hot-small" = selector: uses whichever small model is ALREADY loaded (no swap wait),
# cold-starts gemma-e2b if none:
curl http://localhost:8081/v1/chat/completions -d '{"model":"hot-small","messages":[...]}'
```

Jev decisions on any swarm model via passthrough (codacus build, `--decision-seqs 24`):

```bash
curl http://localhost:8081/upstream/gemma-e2b/v1/decision -d '{"model":"gemma-e2b","contexts":[...],"schema":{...}}'
```

State & control: `GET :8081/running`, `GET :8081/v1/models` (status per model),
`GET :8081/logs`, web UI at `http://localhost:8081/ui`.

## ComfyUI flow (`:8081/comfyui/`)

Name reserved: `comfyui_auto`. Opening `http://localhost:8081/comfyui/` starts
ComfyUI (portable install) and evicts every LLM; websockets/static GETs never
restart it. Queueing a job (`POST /prompt`) keeps it loaded. When the render
finishes, any LLM request fully unloads ComfyUI and reloads the model for review:

1. agent writes prompt + reference files → `POST :8081/comfyui/prompt` (workflow JSON)
2. poll `GET :8081/comfyui/history/<id>` until outputs appear
3. request e.g. `gemma-12b` for the review pass — ComfyUI is auto-unloaded, VRAM returns

## VRAM etiquette (single 3090)

- Strata loaded ⇒ only E2B (+small embeds) reliably fit; stop Strata & Jev backend
  for E4B/G12 combos, Qwen-27b, and ComfyUI video (MiniMax H3 needs ~15 GB+).
- Router costs 0 VRAM while idle — keep it always running.
- LM Studio's own server (:1234) must NOT run at the same time as the router loads models.

## DSH integration

`~/.dsh/settings.yaml` → provider `llama-swap` ("Codacus router (swarm)") with
models hot-small / gemma-e2b / gemma-e4b / gemma-12b / qwen-27b. Default model
stays Strata; pick a swarm model when starting a quick-task session.

## Files

- binary: `D:\AI\llama_swap\bin\llama-swap.exe` (v262)
- config: `D:\AI\llama_swap\config.yaml` — edit + restart, llama-swap watches/reloads
- start: `& 'D:\AI\llama_swap\bin\llama-swap.exe' -config 'D:\AI\llama_swap\config.yaml' --listen localhost:8081`
- test helpers: `test_chat.ps1`, `show_logs.ps1`, `test_jev.ps1` (same folder)

## ComfyUI MCP for agents (`mcp__comfyui__*`)

`tools/comfyui_mcp.py` - stdlib-only MCP server (no pip install), registered by DSH preset
**"ComfyUI media studio"** (`~/.dsh/.agent-presets/comfyui/`). Tools: `comfy_status`,
`comfy_start`, `comfy_stop`, `comfy_queue` (API-format workflow JSON -> prompt_id),
`comfy_result` (wait + output file list under the ComfyUI `output` folder - feed straight to
image/vision review), `comfy_queue_status`. All requests go through `:8081/comfyui/`, so the
matrix swap rules are automatic: queueing media evicts LLMs; the next LLM request unloads ComfyUI.

Verified flow (this PC): router boots portable ComfyUI from D: in ~30 s, `/running` shows
`comfyui_auto ready`, unload returns VRAM instantly. Nothing to start by hand - llama-swap owns
the process; the codacus llama.cpp server is launched per model request the same way.

## Starting things at boot

- Router: autostarts at logon (Startup-folder shortcut -> `D:\AI\llama_swap\start-router.bat`, guarded against double-start).
- Strata: start as usual when you want the big agent; Jev reflex backend = `C:\AI\Strata-Jev\start-jev-backend.bat`.
- LM Studio's server (:1234) and the router should not both hold models - pick one.

## Router-managed Strata (full auto-swap, always-on endpoint)

`strata` is now a model in the pool: `cmd = .venv python setup.py --port ${PORT} --no-browser --yes`,
tree-killed via `cmdStop: taskkill /F /T /PID`. The matrix may evict it for Qwen-27b, ComfyUI video,
or the gemma swarm sets; requesting `strata` again reloads it automatically (minutes - 70 GB MoE),
so **the endpoint :8081 is always up**: worst case is a slow first token, never a dead backend.

- DSH default model = `llama-swap / hot-small`: every session starts on an instant small model;
  escalate by switching the session's model to `strata` (same URL - router swaps brains for you).
- Matrix: set `brain: s & e2b` lets Strata + E2B coexist; evict cost `s: 90` keeps it resident
  unless a request genuinely needs its VRAM. ttl 3600.
- Cutover: restart DSH web to pick up settings.yaml, then STOP launching START-HERE.bat by hand -
  the router owns Strata now (double-loading = OOM). Direct :8080 stays valid only for manual mode.
- Media sessions are brain killers: queueing MiniMax H3 video evicts Strata; a DSH session running
  on `strata` pauses until its next request reloads the model (~4 min). Run media in dedicated sessions.

## Jev decisions through the router

Any loaded pool model answers decisions via passthrough - no fixed ports:

```bash
curl http://localhost:8081/upstream/gemma-e2b/v1/decision   # small-model reflex
curl http://localhost:8081/upstream/strata/v1/decision      # big-brain tie-breaker (loads it if needed)
```

The dedicated :8096 Qwen3.5-2B backend remains the fastest path for sessions on direct Strata (:8080).


