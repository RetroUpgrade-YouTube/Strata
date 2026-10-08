"""tools/jev_decide_demo.py - a minimal MCP client that calls Strata's strata_decide tool.

Copy this to teach any agent/app how the Jev decision call works over stdio MCP
(the same protocol Claude Code / Desktop, Cursor, VS Code and DeepSeek Harness use).

    python tools/jev_decide_demo.py            # runs the built-in example below

The three messages that matter: initialize -> tools/list -> tools/call. Everything else is
the server's answer as one JSON line per request. Requires only Python 3.10+ (stdlib).
"""
import json, subprocess, sys

SERVER = __file__.replace("jev_decide_demo.py", "strata_mcp.py")   # the MCP server sits beside this file


def main() -> int:
    proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)

    def send(msg):
        proc.stdin.write(json.dumps(msg) + "\n"); proc.stdin.flush()

    def recv():
        line = proc.stdout.readline()
        return json.loads(line) if line.strip() else None

    # 1) handshake
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "demo", "version": "0"}}})
    recv()
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # 2) what can it do?
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = [t["name"] for t in (recv() or {}).get("result", {}).get("tools", [])]
    print("server tools:", ", ".join(tools))

    # 3) the reflex call: fixed answers, milliseconds, probabilities.
    #    Every field needs a description; types are enum/boolean/integer/number only.
    arguments = {
        "instructions": "Answer each question about this machine's state.",
        "schema": {
            "is_safe": {"type": "boolean", "description": "Is it safe to run the update now?"},
            "service": {"type": "enum", "choices": ["running", "stopped"], "description": "Is strata running or stopped?"},
            "reach":   {"type": "enum", "choices": ["online", "offline"], "description": "Is the server reachable?"},
        },
        "contexts": [
            "GET /health answered 200 in 3 ms; GPU idle; no jobs queued.",
            "connection refused on 8080; no process listening.",
        ],
    }
    send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
          "params": {"name": "strata_decide", "arguments": arguments}})
    r = recv() or {}
    res = r.get("result") or {}
    text = "\n".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    print("isError:", res.get("isError"))
    try:
        out = json.loads(text)
        # verdicts: per field the chosen value + probability, runner-up + its probability, margin, confidence
        for ctx, verdicts in zip(arguments["contexts"], out.get("verdicts", [])):
            print(f"[{ctx[:40]}...]")
            for v in verdicts.values():
                print("   ", v["line"])
        print(out.get("summary"))
    except ValueError:
        print(text)   # an error message from the server (e.g. backend not running: start-jev-backend.bat)

    proc.stdin.close(); proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
