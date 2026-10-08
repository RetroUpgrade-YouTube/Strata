"""tools/jev_cursor_demo.py - a REAL-TIME reflex loop: move the mouse, ask Jev what's under it.

Pattern for visual browsing / games / any on-site navigation where a smarter model delegates
fast repeated micro-decisions to the reflex (mcp__strata__decide over HTTP here):

    every tick:  probe each target point -> ONE decision call scores ALL points x ALL fields
                 in parallel (~30-60 ms total) -> act on sure/confident, skip coin_flips.

Each field answer carries a verdict (value + pct + runner-up + margin + confidence), so the loop
never acts blind: coin_flip = don't touch it, re-scan with a better screenshot/context instead.

    python tools/jev_cursor_demo.py --targets "400,300;960,540" --ticks 3
    python tools/jev_cursor_demo.py --help

Dry-run by default: it probes points via WindowFromPoint WITHOUT moving your cursor and decides,
but never clicks. Add --click to let it move the cursor and left-click sure/confident click_now
points - during a live loop keep your hands off the mouse, ticks take over the pointer briefly;
the original position is restored on exit. Stdlib only. Windows.
"""
import argparse, ctypes, json, sys, time, urllib.request
from ctypes import wintypes

user32 = ctypes.windll.user32


def probe(x: int, y: int, move: bool = True) -> str:
    """Describe what is under point (x,y). WindowFromPoint works on raw coordinates - the cursor
    is only moved when real clicks are enabled (--click), so dry-run never touches your mouse."""
    if move:
        user32.SetCursorPos(x, y)
    pt = wintypes.POINT(x, y)
    hwnd = user32.WindowFromPoint(pt)
    cls, title = "", ""
    if hwnd:
        buf_c = ctypes.create_unicode_buffer(256); buf_t = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf_c, 256); user32.GetWindowTextW(hwnd, buf_t, 256)
        cls, title = buf_c.value, buf_t.value
    return (f"cursor at ({x},{y}); window under cursor class='{cls}' title='{title}'; "
            f"decide if this point is a clickable button and whether to click now")


SCHEMA = {
    "over_button": {"type": "boolean", "description": "Is the cursor over a clickable button?"},
    "click_now":   {"type": "boolean", "description": "Should we click right now at this point?"},
    "action":      {"type": "enum", "choices": ["move", "click", "wait"],
                    "description": "Best next action for the cursor at this state."},
}


def decide(url: str, contexts: list) -> dict:
    body = {"instructions": "You are the fast reflex of a browsing agent. Answer per state, quickly and strictly.",
            "schema": SCHEMA, "contexts": contexts}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def verdict_of(name: str, info: dict) -> dict:
    """Same verdict rules as strata_decide: winner + runner-up + margin + confidence."""
    p = float(info.get("probability") or 0.0)
    dist = [d for d in (info.get("probabilities") or []) if isinstance(d, dict)]
    runner_v, runner_p, exact = None, 0.0, False
    if len(dist) > 1:
        scored = sorted((float(d.get("probability") or 0.0), d.get("value")) for d in dist)
        rest = [s for s in scored if s[1] != info.get("value")]
        runner_p, runner_v = (rest[-1] if rest else (0.0, None))
        margin, exact = scored[-1][0] - runner_p, True
    else:
        margin = max(0.0, 2*p - 1)
    lab = ("sure" if margin >= 0.50 else "confident" if margin >= 0.20
           else "likely" if margin >= 0.10 else "coin_flip") + ("" if exact else "~")
    line = f"{name}={json.dumps(info.get('value'))} at {round(p*100, 1)}%"
    if runner_v is not None:
        line += f", runner-up {json.dumps(runner_v)} at {round(runner_p*100, 1)}%"
    return {"value": info.get("value"), "margin": round(margin, 4), "confidence": lab, "line": line + " -> " + lab}


def verdicts_raw(out: dict) -> list:
    """Verdicts straight from the raw HTTP answer (same semantics as the MCP tool's)."""
    return [{k: verdict_of(k, v) for k, v in (res.get("fields") or {}).items()} for res in out.get("results", [])]


def main() -> int:
    ap = argparse.ArgumentParser(description="Jev real-time cursor reflex loop")
    ap.add_argument("--targets", default="80,80;960,540", help="'x,y;x,y;...' points to probe each tick")
    ap.add_argument("--ticks", type=int, default=3, help="loop iterations (default 3)")
    ap.add_argument("--interval", type=int, default=120, help="ms between ticks (frame budget)")
    ap.add_argument("--url", default="http://127.0.0.1:8080/v1/decision", help="Strata /v1/decision")
    ap.add_argument("--click", action="store_true", help="allow real clicks on sure/confident click_now (default off)")
    a = ap.parse_args()

    pts = []
    for t in a.targets.split(";"):
        x, y = t.strip().split(","); pts.append((int(x), int(y)))
    old = wintypes.POINT(); user32.GetCursorPos(ctypes.byref(old))
    print(f"targets: {pts}  ticks={a.ticks}  click={'on' if a.click else 'DRY-RUN'}")
    try:
        for tick in range(a.ticks):
            t0 = time.perf_counter()
            contexts = [probe(x, y, move=a.click) for (x, y) in pts]
            out = decide(a.url, contexts)
            dt = round((time.perf_counter() - t0) * 1000)
            print(f"--- tick {tick}: {len(pts)} points scored in {dt} ms total ---")
            for (x, y), verdicts in zip(pts, verdicts_raw(out)):
                vs = {k: v for k, v in verdicts.items()}
                line = " | ".join(v["line"] for v in vs.values())
                print(f"  ({x},{y}) {line}")
                act = vs.get("action", {})
                if a.click and vs.get("click_now", {}).get("value") is True \
                        and vs.get("click_now", {}).get("confidence", "").startswith(("sure", "confident")):
                    print(f"    -> CLICK ({x},{y})  [{vs['click_now']['confidence']}]")
                    user32.SetCursorPos(x, y)
                    user32.mouse_event(0x0002, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTDOWN
                    user32.mouse_event(0x0004, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTUP
                elif act.get("value") == "click":
                    print(f"    -> skipped click: confidence {act.get('confidence')} (coin_flip/likely = don't act blind)")
            time.sleep(a.interval / 1000.0)
        print("note: every tick asked ALL points x ALL fields in ONE parallel call - the smarter model")
        print("only steps in for coin_flips, screenshots and strategy; the reflex runs the loop.")
    finally:
        user32.SetCursorPos(old.x, old.y)
    return 0


if __name__ == "__main__":
    sys.exit(main())
