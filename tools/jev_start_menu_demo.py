"""tools/jev_start_menu_demo.py - watch the Jev reflex find and click the Windows Start button.

Finds every taskbar's Start button by window class (Shell_TrayWnd / Shell_SecondaryTrayWnd child
class "Start" -> GetWindowRect), moves the cursor onto each one, asks the reflex per point
(over_button? click_now? action) in ONE parallel call, then left-clicks the ones the verdict
says are sure/confident. Opens the start menu on screen and closes it with Esc a few seconds
later so you can watch the whole probe->decide->act cycle. Multi-monitor aware (DPI-aware coords).

    python tools/jev_start_menu_demo.py            # find + decide + click primary, preview 4s
    python tools/jev_start_menu_demo.py --no-click # cursor moves and decides, never clicks
"""
import argparse, ctypes, json, sys, time, urllib.request
from ctypes import wintypes

u = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # physical pixels on every monitor
except Exception:
    pass

EnumChildren = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def find_start_buttons() -> list:
    """All Start buttons across primary + secondary taskbars: [{hwnd, x, y, tray}]."""
    out = []
    tray = None
    while True:
        tray = u.FindWindowExW(None, tray, "Shell_TrayWnd", None) or \
               u.FindWindowExW(None, tray, "Shell_SecondaryTrayWnd", None)
        if not tray:
            break
        tc = ctypes.create_unicode_buffer(64); u.GetClassNameW(tray, tc, 64)
        kids = []
        def cb(h, lparam, kids=kids):
            b = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, b, 64)
            if b.value == "Start":
                kids.append(h)
            return True
        u.EnumChildWindows(tray, EnumChildren(cb), 0)
        for h in kids:
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            out.append({"hwnd": h, "x": (r.left + r.right)//2, "y": (r.top + r.bottom)//2,
                        "tray": "primary" if tc.value == "Shell_TrayWnd" else "secondary",
                        "rect": (r.left, r.top, r.right, r.bottom)})
        # classify tray
    return out


SCHEMA = {
    "over_button": {"type": "boolean", "description": "Is the cursor over a clickable button?"},
    "click_now":   {"type": "boolean", "description": "Should we click right now at this point to open what it is for?"},
    "action":      {"type": "enum", "choices": ["move", "click", "wait"], "description": "Best next action."},
}


def decide(url, contexts):
    body = {"instructions": "You are the fast reflex of a browsing agent controlling the mouse.",
            "schema": SCHEMA, "contexts": contexts}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def verdict_of(name, info):
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
    lab = ("sure" if margin >= .5 else "confident" if margin >= .2 else "likely" if margin >= .1
           else "coin_flip") + ("" if exact else "~")
    line = f"{name}={json.dumps(info.get('value'))} at {round(p*100,1)}%"
    if runner_v is not None:
        line += f", runner-up {runner_v} at {round(runner_p*100,1)}%"
    return {"value": info.get("value"), "confidence": lab, "line": line + " -> " + lab}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080/v1/decision")
    ap.add_argument("--no-click", action="store_true", help="move + decide only, never click")
    ap.add_argument("--preview", type=int, default=4, help="seconds the menu stays open before auto-Esc")
    a = ap.parse_args()

    btns = find_start_buttons()
    if not btns:
        print("no Start button found via Shell_TrayWnd/Shell_SecondaryTrayWnd child class 'Start'")
        return 1
    print(f"found {len(btns)} Start button(s): " + ", ".join(f"({b['x']},{b['y']}) [{b['tray']}]" for b in btns))

    old = wintypes.POINT(); u.GetCursorPos(ctypes.byref(old))
    contexts, t0 = [], time.perf_counter()
    for i, b in enumerate(btns):
        u.SetCursorPos(b["x"], b["y"])                      # visible: cursor flies to each button
        time.sleep(0.25)
        contexts.append(f"point {i}: the Windows Start button (taskbar class Start), window rect "
                        f"{b['rect']}; clicking opens the start menu; agent goal: open and close it")
    out = decide(a.url, contexts)
    print(f"one parallel reflex call for {len(btns)} points in {round((time.perf_counter()-t0)*1000)} ms (incl. cursor moves):")

    clicked = None
    for b, verdicts in zip(btns, out.get("results", [])):
        fs = {k: verdict_of(k, v) for k, v in (verdicts.get("fields") or {}).items()}
        print(f"  ({b['x']},{b['y']}) [{b['tray']}]: " + " | ".join(v["line"] for v in fs.values()))
        cn = fs.get("click_now", {})
        if not a.no_click and cn.get("value") is True and cn.get("confidence", "").startswith(("sure", "confident")):
            u.SetCursorPos(b["x"], b["y"])
            u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0)   # left down+up
            clicked = b
            print(f"    -> CLICKED ({b['x']},{b['y']}) [{cn['confidence']}] - start menu opening NOW on that monitor")

    if clicked:
        time.sleep(a.preview)
        k = ctypes.windll.user32
        k.keybd_event(0x17, 0, 2, 0); k.keybd_event(0x17, 0, 0, 0); k.keybd_event(0x17, 0, 3, 0)   # VK_ESCAPE up/down/up
        print(f"    -> closed the menu with Esc after {a.preview}s")
    u.SetCursorPos(old.x, old.y)
    return 0


if __name__ == "__main__":
    sys.exit(main())
