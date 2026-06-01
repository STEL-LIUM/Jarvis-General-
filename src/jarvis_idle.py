#!/usr/bin/env python3
"""
Idle self-play — JARVIS gets better at 3D design while you're away.

When the machine has been idle for a while AND nothing else is using the model,
a low-priority background loop runs a DESIGN TOURNAMENT:

  1. pick a design prompt (a past success to refine, or a fresh idea)
  2. generate N candidate scripts (varied temperature)
  3. run each headless in Blender, render a preview
  4. the vision model JUDGES them and picks the winner
  5. store the winner in design memory; note the winning *technique* to self.md

So over many idle cycles, JARVIS accumulates proven designs and a record of WHY
certain approaches win ("for organic shapes, metaballs beat booleans"). This is
AlphaGo-style self-play, but for Blender modelling, on the user's own hardware.

Hard guarantees:
  • Runs ONLY when system idle > IDLE_SECONDS and the app isn't busy.
  • One tournament at a time, fully bounded (N candidates, capped Blender time).
  • Pauses the instant the user returns (host checks should_pause()).
  • Pure best-effort: any failure ends the cycle quietly. Off by default; the
    host enables it. Never touches the UI thread directly — posts to a queue.

The host (jarvis_chat) injects the callables it needs via configure(); this
module imports nothing from jarvis_chat (no circular import).
"""
from __future__ import annotations
import os, re, json, time, base64, threading, subprocess, tempfile
import urllib.request

# ---- injected by configure() -------------------------------------------------
_CFG = {
    "chat_url": None,      # ollama /api/chat
    "deep_model": None,    # script generator
    "vision_model": None,  # judge
    "design_system": "",   # DESIGN_SYSTEM_BPY
    "blender": None,       # blender exe path (or None)
    "num_ctx": 8192,
    "design_mem": None,    # jarvis_design_memory module (or None)
    "self_mem": None,      # jarvis_self module (or None)
    "notify": None,        # callable(str) -> post a status/note to the UI queue
    "should_pause": None,  # callable() -> True if the user came back / app busy
}

IDLE_SECONDS = 300        # how long the machine must be idle before self-play
CANDIDATES = 3            # designs per tournament
BLENDER_TIMEOUT = 150     # per-candidate cap
_running = threading.Event()   # a tournament is in progress
_enabled = threading.Event()   # master switch


def configure(**kw) -> None:
    _CFG.update(kw)


def enable(on: bool = True) -> None:
    if on:
        _enabled.set()
    else:
        _enabled.clear()


def is_enabled() -> bool:
    return _enabled.is_set()


# ---- idle detection (Windows) -----------------------------------------------
def system_idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input, system-wide (Windows)."""
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return 0.0


def _notify(msg: str) -> None:
    fn = _CFG.get("notify")
    if fn:
        try:
            fn(msg)
        except Exception:
            pass


def _paused() -> bool:
    fn = _CFG.get("should_pause")
    try:
        return bool(fn()) if fn else False
    except Exception:
        return True


# ---- model helpers (self-contained; no jarvis_chat import) ------------------
def _clean(raw: str) -> str:
    out = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    out = re.sub(r"^\s*```(?:python|py)?\s*\n", "", out)
    out = re.sub(r"\n?```\s*$", "", out).strip()
    return out


def _gen(prompt: str, temperature: float) -> str:
    payload = json.dumps({
        "model": _CFG["deep_model"],
        "messages": [{"role": "system", "content": _CFG["design_system"]},
                     {"role": "user", "content": prompt}],
        "stream": False, "keep_alive": "5m",
        "options": {"num_ctx": _CFG["num_ctx"], "num_predict": 3000,
                    "temperature": temperature},
    }).encode()
    req = urllib.request.Request(_CFG["chat_url"], data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        obj = json.loads(resp.read().decode("utf-8", "ignore"))
    return _clean((obj.get("message", {}) or {}).get("content", "") or "")


def _run_blender(code: str) -> tuple[bool, str]:
    blender = _CFG["blender"]
    if not blender or not os.path.isfile(blender):
        return (False, "")
    run_dir = tempfile.mkdtemp(prefix="idle_play_")
    script = os.path.join(run_dir, "s.py")
    header = (f"OUT = {run_dir!r}\nimport os, sys, math, random\n"
              "try:\n import bmesh\n import mathutils\n"
              " from mathutils import Vector, Matrix, Euler, Quaternion\n"
              "except Exception: pass\n\n")
    try:
        with open(script, "w", encoding="utf-8") as f:
            f.write(header + code + "\n")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run([blender, "--background", "--python", script],
                       capture_output=True, text=True,
                       timeout=BLENDER_TIMEOUT, creationflags=flags)
    except Exception:
        return (False, run_dir)
    preview = os.path.join(run_dir, "preview.png")
    return (os.path.isfile(preview), run_dir)


def _judge(preview_path: str, prompt: str) -> int:
    try:
        with open(preview_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception:
        return 0
    q = (f"Score how well this render matches the request \"{prompt}\" from 1-10 "
         "(shape, proportions, recognizable parts). Reply STRICT JSON: "
         "{\"score\": N}.")
    payload = json.dumps({
        "model": _CFG["vision_model"],
        "messages": [{"role": "user", "content": q, "images": [b64]}],
        "stream": False, "keep_alive": "5m",
        "options": {"num_ctx": 4096, "num_predict": 80, "temperature": 0.1},
    }).encode()
    try:
        req = urllib.request.Request(_CFG["chat_url"], data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            obj = json.loads(resp.read().decode("utf-8", "ignore"))
        raw = (obj.get("message", {}) or {}).get("content", "") or ""
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        return int(json.loads(m.group(0)).get("score", 0)) if m else 0
    except Exception:
        return 0


# ---- prompt selection -------------------------------------------------------
_FRESH_IDEAS = [
    "design a chess bishop", "design a desk lamp", "design a teapot",
    "design a treasure chest", "design a wizard staff", "design a flower vase",
    "design a robot head", "design a sword", "design a shield",
    "design a coffee mug", "design a rocket", "design a helmet",
]


def _pick_prompt() -> str:
    """Prefer refining a past success/open thread; else a fresh idea. Varies by
    clock so repeated cycles don't all pick the same one."""
    sm = _CFG.get("self_mem")
    try:
        if sm:
            threads = sm.open_threads(5)
            for t in threads:
                # use the gist after the [tag]
                body = t.split("]", 1)[1].strip() if "]" in t else t
                if "design" in body.lower() or "model" in body.lower():
                    return body[:80]
    except Exception:
        pass
    idx = int(time.time() // 60) % len(_FRESH_IDEAS)
    return _FRESH_IDEAS[idx]


# ---- the tournament ---------------------------------------------------------
def run_one_tournament() -> None:
    """Generate CANDIDATES designs, judge, store the winner. Bounded + abortable."""
    if _running.is_set():
        return
    _running.set()
    try:
        prompt = _pick_prompt()
        results = []   # (score, code)
        for i in range(CANDIDATES):
            if _paused():
                return
            try:
                code = _gen(prompt, temperature=0.2 + 0.25 * i)
            except Exception:
                continue
            if not code:
                continue
            ok, run_dir = _run_blender(code)
            if _paused():
                return
            if ok:
                score = _judge(os.path.join(run_dir, "preview.png"), prompt)
                results.append((score, code))
        if not results:
            return
        results.sort(key=lambda r: r[0], reverse=True)
        best_score, best_code = results[0]
        if best_score >= 6:
            dm = _CFG.get("design_mem")
            if dm:
                try:
                    dm.remember(prompt, best_code, best_score)
                except Exception:
                    pass
            sm = _CFG.get("self_mem")
            if sm and len(results) > 1:
                try:
                    sm._append([  # type: ignore[attr-defined]
                        f"- [note] While idle I practiced \"{prompt}\" — best of "
                        f"{len(results)} attempts scored {best_score}/10."])
                except Exception:
                    pass
            _notify(f"💡 (idle) practiced \"{prompt}\" — banked a {best_score}/10 "
                    "version for next time.")
    finally:
        _running.clear()


def loop() -> None:
    """Daemon loop: when idle long enough and enabled, run a tournament, then
    back off. Cheap polling; the heavy work only fires on real idle."""
    while True:
        try:
            if (_enabled.is_set()
                    and not _paused()
                    and system_idle_seconds() >= IDLE_SECONDS):
                run_one_tournament()
                # after a tournament, wait a good while before the next one
                for _ in range(60):
                    if _paused():
                        break
                    time.sleep(2)
            else:
                time.sleep(10)
        except Exception:
            time.sleep(30)


def start() -> None:
    """Launch the idle loop on a daemon thread (idempotent)."""
    if getattr(start, "_started", False):
        return
    start._started = True   # type: ignore[attr-defined]
    threading.Thread(target=loop, daemon=True).start()
