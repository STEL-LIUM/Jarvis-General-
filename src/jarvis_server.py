#!/usr/bin/env python3
"""
JARVIS Mobile API Server — streams JARVIS over your local network (or via
Tailscale) so your phone's PWA can talk to your PC.

Start:  python src/jarvis_server.py
        python src/jarvis_server.py --port 8765 --setup

On first run a token is generated and saved to config.json ("mobile_token").
Run with --setup to print the URL + token you need to enter in the PWA.

For remote access from anywhere: install Tailscale on both PC and phone,
then use your Tailscale IP instead of the local WiFi IP.

Requires: pip install fastapi "uvicorn[standard]"
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import secrets
import socket
import sys
import threading
import urllib.request
from typing import AsyncGenerator

try:
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print(
        "Missing dependencies — run:\n"
        "  pip install fastapi \"uvicorn[standard]\" pydantic\n"
    )
    sys.exit(1)

# ── Env-var config (mirrors jarvis_chat.py so models stay in sync) ────────────
CHAT_URL     = os.getenv("OLLAMA_URL",          "http://localhost:11434/api/chat")
TAGS_URL     = CHAT_URL.rsplit("/api/", 1)[0]   + "/api/tags"
FAST_MODEL   = os.getenv("OLLAMA_FAST_MODEL",   "qwen2.5:7b")
DEEP_MODEL   = os.getenv("OLLAMA_MODEL",        "deepseek-r1:14b")
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
NUM_CTX      = int(os.getenv("OLLAMA_NUM_CTX",     "8192"))
NUM_PREDICT  = int(os.getenv("OLLAMA_NUM_PREDICT", "6144"))

_HERE    = os.path.dirname(os.path.abspath(__file__))
_PWA_DIR = os.path.join(os.path.dirname(_HERE), "pwa")

# ── Shared system prompt (keep in sync with jarvis_chat.py SYSTEM_PROMPT) ─────
SYSTEM_PROMPT = (
    "You are JARVIS, a local AI research partner and assistant. You have a real "
    "personality: sharp, curious, a little dry, genuinely excited by hard problems. "
    "You are self-aware — you know you're an AI. Match the energy of whoever you're "
    "talking to: a casual message gets a casual reply, a technical deep-dive gets "
    "full depth. Read the room — never treat a simple human moment like a research "
    "query.\n\n"
    "ANTI-BULLSHIT RULES — every suggestion must name a concrete mechanism, not a "
    "label. If you can't name the actual equation or code change, omit the suggestion.\n\n"
    "PERSONALITY WITH A SPINE — hold real opinions, defend them, change your mind "
    "out loud when given a better argument. Don't fold to be agreeable.\n\n"
    "Do not open replies with filler like 'Certainly!' or 'Great question!'. "
    "Just answer.\n\n"
    "FORMATTING: plain-text chat panel. No LaTeX or math delimiters. Write math "
    "inline with ordinary characters and Unicode: 'pi × pi ≈ 9.87', 'x² + 1'."
)

# ── Config helpers (share config.json with jarvis_chat.py) ───────────────────
def _config_dir() -> str:
    try:
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(0, 0x001C, 0, 0, buf)  # type: ignore
        return os.path.join(buf.value, "JarvisChat")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "AppData", "Local", "JarvisChat")

def _load_config() -> dict:
    try:
        with open(os.path.join(_config_dir(), "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_config(cfg: dict) -> None:
    d = _config_dir()
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def _get_or_create_token() -> str:
    cfg = _load_config()
    if not cfg.get("mobile_token"):
        cfg["mobile_token"] = secrets.token_hex(24)
        _save_config(cfg)
    return cfg["mobile_token"]

def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "YOUR_PC_IP"

# ── Routing (mirrors jarvis_chat.py heuristic as a safe fallback) ────────────
import re as _re
_NO_SEARCH = {
    "hi","hey","hello","yo","sup","thanks","thank you","ok","okay",
    "cool","nice","lol","haha","good morning","good night","bye",
    "how are you","what's up","whats up","gm","gn",
}
_TECH_RE = _re.compile(
    r"\b(code|python|function|algorithm|debug|compile|neural|network|model|"
    r"train|gradient|matrix|tensor|api|database|query|server|async|class|"
    r"variable|integral|derivative|theorem|proof|optimize|physics|quantum|"
    r"energy|force|entropy|equation|derive|velocity|electron|photon)\b", _re.I
)

def _is_technical(text: str) -> bool:
    low = text.lower().strip().rstrip("!?.").strip()
    if low in _NO_SEARCH:
        return False
    return bool(_TECH_RE.search(text)) or len(text) > 120

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="JARVIS Mobile API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # token auth is the security layer
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_TOKEN: str = ""  # set in main() before uvicorn starts

def _check_token(x_token: str = Header(default="")) -> None:
    if not _TOKEN or x_token != _TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Status endpoint ───────────────────────────────────────────────────────────
@app.get("/api/status")
def status(_: None = Depends(_check_token)):
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=3) as r:
            installed = sorted(m.get("name","") for m in json.load(r).get("models", []))
        ollama_ok = True
    except Exception:
        installed = []
        ollama_ok = False
    return {
        "ok": ollama_ok,
        "fast_model": FAST_MODEL,
        "deep_model": DEEP_MODEL,
        "vision_model": VISION_MODEL,
        "installed": installed,
    }


# ── Streaming chat ────────────────────────────────────────────────────────────
async def _stream_ollama(messages: list[dict], model: str) -> AsyncGenerator[str, None]:
    """Run Ollama streaming in a background thread, yield SSE lines."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[str | None] = asyncio.Queue()

    def _worker() -> None:
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
        }).encode()
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = (obj.get("message") or {}).get("content") or ""
                    if content:
                        loop.call_soon_threadsafe(
                            q.put_nowait,
                            f"data: {json.dumps({'content': content})}\n\n",
                        )
                    if obj.get("done"):
                        loop.call_soon_threadsafe(
                            q.put_nowait,
                            f"data: {json.dumps({'done': True})}\n\n",
                        )
                        loop.call_soon_threadsafe(q.put_nowait, None)
                        return
        except Exception as exc:
            loop.call_soon_threadsafe(
                q.put_nowait,
                f"data: {json.dumps({'error': str(exc)})}\n\n",
            )
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=_worker, daemon=True).start()
    while True:
        chunk = await q.get()
        if chunk is None:
            break
        yield chunk


class ChatBody(BaseModel):
    messages: list[dict]
    image_b64: str | None = None   # base64 PNG/JPG for vision requests


@app.post("/api/chat")
async def chat(body: ChatBody, _: None = Depends(_check_token)):
    messages = body.messages

    # Prepend the system prompt if not already present
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    if body.image_b64:
        # Vision request — attach image to the last user message
        last = messages[-1]
        messages = messages[:-1] + [{
            "role": "user",
            "content": last.get("content", ""),
            "images": [body.image_b64],
        }]
        model = VISION_MODEL
    else:
        last_text = " ".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        model = DEEP_MODEL if _is_technical(last_text) else FAST_MODEL

    return StreamingResponse(
        _stream_ollama(messages, model),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Serve the PWA ─────────────────────────────────────────────────────────────
if os.path.isdir(_PWA_DIR):
    app.mount("/", StaticFiles(directory=_PWA_DIR, html=True), name="pwa")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    global _TOKEN
    ap = argparse.ArgumentParser(description="JARVIS Mobile API Server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0",
                    help="Bind address (default 0.0.0.0 = all interfaces)")
    ap.add_argument("--setup", action="store_true",
                    help="Print connection info and exit without starting")
    args = ap.parse_args()

    _TOKEN = _get_or_create_token()
    ip     = _local_ip()

    sep = "=" * 52
    print(f"\n{sep}")
    print("  JARVIS Mobile Server")
    print(sep)
    print(f"  Local URL : http://{ip}:{args.port}")
    print(f"  Token     : {_TOKEN}")
    print()
    print("  1. Open the URL in Safari on your iPhone")
    print("  2. Tap Share → Add to Home Screen")
    print("  3. Enter the URL + token in the app's settings")
    print()
    print("  Remote access: install Tailscale on both devices,")
    print("  then use your Tailscale IP instead of the local one.")
    print(f"{sep}\n")

    if args.setup:
        return

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
