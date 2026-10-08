#!/usr/bin/env python3
"""tools/comfyui_mcp.py - MCP server (stdio) that lets an AI agent run ComfyUI media jobs through the
llama-swap router, so VRAM swap rules apply automatically.

Registered in DSH as preset "ComfyUI media studio" (see docs/ROUTER.md). Speaks MCP JSON-RPC 2.0 over
stdin/stdout, Python standard library only. All ComfyUI traffic goes through the router's /comfyui/
endpoint: any tool call that touches ComfyUI starts it AND evicts loaded LLMs (matrix rule); when a
render finishes, the next LLM request fully unloads ComfyUI and reloads the model for review.

Tools: comfy_status, comfy_start, comfy_stop, comfy_queue, comfy_result, comfy_queue_status.
Safety: no shell, no free paths - only JSON workflow graphs and prompt ids; base URL is fixed to the
local router (override with COMFY_MCP_BASE).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
PROTOCOLS = ("2025-06-18", "2025-06-18", "2024-11-05")
VERSION = "0.1.0"

BASE = os.environ.get("COMFY_MCP_BASE", "http://localhost:8081/comfyui").rstrip("/")
ROUTER = BASE[: -len("/comfyui")] if BASE.endswith("/comfyui") else "http://localhost:8081"
OUTPUT_ROOT = os.environ.get(
    "COMFY_MCP_OUTPUT", r"D:\AI\Imagegen\ComfyUI_windows_portable\ComfyUI\output")

INSTRUCTIONS = (
    "ComfyUI is served through the llama-swap router at http://localhost:8081/comfyui/. Start with "
    "comfy_status. To make media: comfy_queue with an API-format workflow JSON you build (the user's "
    "saved workflows are in ComfyUI; plug prompts/reference file paths into the nodes), then comfy_result "
    "with the returned prompt_id to wait for completion - polling keeps ComfyUI loaded, and your next LLM "
    "request after that unloads ComfyUI automatically so the GPU returns to language models. Only one "
    "media job set runs at a time; big video models evict every LLM while they work.")


def log(msg: str) -> None:
    print(f"[comfyui-mcp] {msg}", file=sys.stderr, flush=True)


class ToolError(Exception):
    pass


def http(method: str, url: str, body=None, timeout: int = 60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")[:400]
        return e.code, raw
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)


class Tools:
    def defs(self):
        d = lambda name, desc, props=None, req=None: {
            "name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props or {}, "required": req or []}}
        return [
            d("comfy_status", "Is ComfyUI running (through the router)? Returns stats and which LLMs the router evicted."),
            d("comfy_start", "Start ComfyUI on demand. This fully unloads any loaded LLMs (matrix rule). Waits up to 120 s for it to become ready."),
            d("comfy_stop", "Stop ComfyUI and hand the GPU back to language models right now (router unload API)."),
            d("comfy_queue", "Queue a generation job.",
              {"workflow": {"type": "string", "description": "API-format ComfyUI workflow JSON (the node graph object, as saved by 'Export (API)')"}},
              ["workflow"]),
            d("comfy_result", "Wait for a job and list its output files. Polling keeps ComfyUI loaded; stop polling when done.",
              {"prompt_id": {"type": "string"}, "timeout_s": {"type": "integer", "description": "max seconds to wait, default 300"}},
              ["prompt_id"]),
            d("comfy_queue_status", "Current queue: running + pending job ids."),
        ]

    def call(self, name: str, args):
        args = args or {}
        if name == "comfy_status":
            st, body = http("GET", f"{BASE}/system_stats", timeout=15)
            _, run = http("GET", f"{ROUTER}/running", timeout=10)
            out = {"comfy_http": st, "router_models_running": run}
            if st == 409:
                out["note"] = "ComfyUI not loaded - comfy_start (this evicts loaded LLMs)"
            elif isinstance(body, dict):
                out["system_stats"] = body.get("system", {})
            return out
        if name == "comfy_start":
            http("GET", f"{BASE}/", timeout=90)          # a plain GET starts the model via the router
            deadline = time.time() + 120
            while time.time() < deadline:
                st, _ = http("GET", f"{BASE}/system_stats", timeout=15)
                if st == 200:
                    return {"started": True}
                time.sleep(3)
            return {"started": False, "note": "not ready after 120 s - check comfy_status / router logs"}
        if name == "comfy_stop":
            st, body = http("POST", f"{ROUTER}/api/models/unload/comfyui_auto", timeout=60)
            return {"unload_http": st, "body": body}
        if name == "comfy_queue":
            raw = args.get("workflow")
            if not isinstance(raw, str):
                raise ToolError("workflow must be a JSON string of the API-format node graph")
            try:
                graph = json.loads(raw)
            except ValueError as e:
                raise ToolError(f"workflow is not valid JSON: {e}")
            if not isinstance(graph, dict) or not all(isinstance(v, dict) and "class_type" in v for v in graph.values()):
                raise ToolError("workflow must be the API-format graph object: {node_id: {..., 'class_type': ...}}")
            st, body = http("POST", f"{BASE}/prompt", {"prompt": graph}, timeout=60)
            if st != 200:
                raise ToolError(f"queue failed ({st}): {body}")
            pid = (body or {}).get("prompt_id")
            return {"prompt_id": pid} if pid else {"error": "no prompt id returned", "body": body}
        if name == "comfy_result":
            pid = str(args.get("prompt_id") or "")
            if not pid:
                raise ToolError("prompt_id required")
            deadline = time.time() + int(args.get("timeout_s") or 300)
            while True:
                st, body = http("GET", f"{BASE}/history/{pid}", timeout=30)
                if st == 200 and isinstance(body, dict) and pid in body:
                    ent = body[pid]
                    status = (ent.get("status") or {})
                    files = []
                    for node in (ent.get("outputs") or {}).values():
                        for key in ("images", "gifs", "audio", "files"):
                            for f in node.get(key, []) or []:
                                rel = f.get("subfolder", "") + "/" + f.get("filename", "") if key != "files" else f.get("filename", "")
                                files.append({"rel_path": rel,
                                              "abs_path": os.path.join(OUTPUT_ROOT, *(rel.split("/"))),
                                              "type": key})
                    return {"done": True, "status": status.get("status_str", "?"),
                            "completed": bool(status.get("completed", True)), "outputs": files}
                if time.time() > deadline:
                    _, q = http("GET", f"{BASE}/queue", timeout=15)
                    return {"done": False, "note": "timeout; job may still be rendering - poll again",
                            "queue": q if isinstance(q, dict) else str(q)[:200]}
                time.sleep(4)
        if name == "comfy_queue_status":
            st, body = http("GET", f"{BASE}/queue_summary", timeout=15)
            return {"http": st, "body": body}
        raise KeyError(name)


class McpServer:
    def __init__(self, out):
        self.tools = Tools()
        self.out = out
        self.write_lock = threading.Lock()
        self.protocol = PROTOCOL

    def send(self, msg):
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self.write_lock:
            self.out.write(data)
            self.out.flush()

    @staticmethod
    def result(rid, res):
        return {"jsonrpc": "2.0", "id": rid, "result": res}

    @staticmethod
    def error(rid, code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    def content(self, res, error):
        out = {"content": [{"type": "text", "text": json.dumps(res, indent=1, ensure_ascii=False)}],
               "isError": error}
        if self.protocol >= "2025-06-18":
            out["structuredContent"] = res
        return out

    def handle(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self.error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        if "method" not in msg:
            return None
        method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
        if "id" not in msg:
            return None
        if not isinstance(params, dict):
            return self.error(rid, -32602, "params must be an object")
        if method == "initialize":
            asked = str(params.get("protocolVersion") or PROTOCOL)
            self.protocol = asked if asked in ("2025-06-18", "2025-03-26", "2024-11-05") else PROTOCOL
            return self.result(rid, {"protocolVersion": self.protocol,
                                     "capabilities": {"tools": {"listChanged": False}},
                                     "serverInfo": {"name": "comfyui", "title": "ComfyUI media studio",
                                                    "version": VERSION},
                                     "instructions": INSTRUCTIONS})
        if method == "ping":
            return self.result(rid, {})
        if method == "tools/list":
            return self.result(rid, {"tools": self.tools.defs()})
        if method == "tools/call":
            name = params.get("name")
            try:
                res = self.tools.call(name, params.get("arguments"))
                return self.result(rid, self.content(res, False))
            except KeyError:
                return self.error(rid, -32602, f"unknown tool: {name}")
            except ToolError as e:
                return self.result(rid, self.content({"error": str(e)}, True))
            except Exception as e:  # noqa: BLE001
                log("tool failed:\n" + traceback.format_exc())
                return self.result(rid, self.content({"error": f"internal error in {name}: {e}"}, True))
        if method in ("resources/list", "prompts/list"):
            return self.result(rid, {method.split("/")[0]: []})
        return self.error(rid, -32601, f"method not found: {method}")

    def serve(self, inp):
        threads = []
        for raw in inp:
            line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.send(self.error(None, -32700, "parse error"))
                continue
            batch = isinstance(msg, list)
            if batch or (isinstance(msg, dict) and msg.get("method") == "tools/call"):
                t = threading.Thread(target=self._run, args=(msg,), daemon=True)
                t.start()
                threads.append(t)
                threads = [x for x in threads if x.is_alive()]
            else:
                self._run(msg)
        for t in threads:
            t.join(timeout=5)

    def _run(self, msg):
        if isinstance(msg, list):
            out = [r for r in (self.handle(m) for m in msg) if r is not None]
            if out:
                data = (json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8")
                with self.write_lock:
                    self.out.write(data)
                    self.out.flush()
            return
        r = self.handle(msg)
        if r is not None:
            self.send(r)


def main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    server = McpServer(out)
    log(f"ready (base {BASE})")
    try:
        server.serve(sys.stdin.buffer)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

