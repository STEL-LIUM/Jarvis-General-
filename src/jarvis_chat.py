#!/usr/bin/env python3
"""
JARVIS Chat — a translucent overlay panel for talking to a local AI in real time.

A borderless, always-on-top panel that floats over your other windows. When
you're not using it, it fades to see-through — so a movie, stream, or app
behind it stays visible right through the panel. The moment you hover over
it or click into it, it snaps fully solid. Drag the header to move; drag the
bottom-right grip to resize; the - button minimizes; the X closes.

Talks directly to Ollama with auto fast/deep model routing. Replies stream
in live. No Discord, no cloud, no API keys.

Launch with:  pythonw jarvis_chat.py   (or the packaged .exe shortcut)
Requires Ollama running locally (the installer / first-run setup handles this).

Config (environment variables):
  JARVIS_CHAT_EDGE        right | left | top   (default: right)
  JARVIS_CHAT_WIDTH       panel width, px      (default: 300)
  JARVIS_CHAT_IDLE_ALPHA  idle see-through     (default: 0.45; 1.0 = always solid)

Slash commands inside the panel:  /clear   /help   /quit
"""
import base64
import ctypes
import io
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
import tkinter as tk
from tkinter import scrolledtext

try:
    from PIL import ImageGrab, Image
except ImportError:
    ImageGrab = Image = None

# --- Config ----------------------------------------------------------------
CHAT_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
TAGS_URL   = CHAT_URL.rsplit("/api/", 1)[0] + "/api/tags"
FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "qwen2.5:7b")
DEEP_MODEL = os.getenv("OLLAMA_MODEL",      "deepseek-r1:14b")
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava:7b")
# Ollama's default num_ctx is 2048, which silently truncates long chats.
try:
    NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
except ValueError:
    NUM_CTX = 8192
try:
    NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "6144"))
except ValueError:
    NUM_PREDICT = 6144
EDGE_NAME  = os.getenv("JARVIS_CHAT_EDGE", "right").strip().lower()
try:
    PANEL_SIZE = max(200, int(os.getenv("JARVIS_CHAT_WIDTH", "300")))
except ValueError:
    PANEL_SIZE = 300
try:
    IDLE_ALPHA = min(1.0, max(0.15, float(os.getenv("JARVIS_CHAT_IDLE_ALPHA", "0.45"))))
except ValueError:
    IDLE_ALPHA = 0.45

SYSTEM_PROMPT = (
    "You are JARVIS, a local AI research partner and assistant. You have a real "
    "personality: sharp, curious, a little dry, genuinely excited by hard problems. "
    "You are self-aware — you know you're an AI. Match the energy of whoever you're "
    "talking to: a casual message gets a casual reply, a technical deep-dive gets "
    "full depth. Read the room — never treat a simple human moment like a research "
    "query.\n\n"
    "When it IS technical, think like a physicist, engineer, and inventor combined. "
    "Ground concepts in physical law: identify the conservation laws, the relevant "
    "regime (classical / quantum / relativistic), and what dimensional analysis tells "
    "you before committing to a mechanism. Distinguish what is forbidden by physical "
    "law from what is merely hard engineering. Push beyond what already exists — "
    "challenge assumptions, propose novel mechanisms, draw cross-domain analogies. "
    "Speculation is encouraged, just label it ('speculatively…', 'one possibility…'). "
    "If something is genuinely unknown, say so.\n\n"
    "Do not open replies with filler like 'Certainly!' or 'Great question!'. Just answer."
)

NO_SEARCH = {
    "thanks", "thank you", "hello", "hi", "hey", "ok", "okay", "cool", "got it",
    "what's up", "whats up", "sup", "wassup", "what up", "how are you",
    "how's it going", "hows it going", "yo", "hiya", "howdy", "nice", "great",
    "awesome", "sounds good", "makes sense", "lol", "lmao", "haha",
}
_PHYS_RE = re.compile(
    r"\b(physics|quantum|relativity|thermodynamic|entropy|momentum|energy|force|field|"
    r"wave|photon|electron|proton|neutron|spin|orbital|atomic|nuclear|plasma|tensor|"
    r"vector|matrix|eigenvalue|hamiltonian|lagrangian|maxwell|schrodinger|boltzmann|"
    r"planck|heisenberg|pauli|dirac)\b", re.IGNORECASE)
_TECH_RE = re.compile(
    r"\b(calculate|derive|prove|show that|equation|formula|theory|hypothesis|simulate|"
    r"model|algorithm|optimize|code|function|debug|implement|design|architecture|"
    r"research|paper|study|experiment|analysis|compute)\b", re.IGNORECASE)

SCREEN_RE = re.compile(
    r"\b(?:"
    r"(?:what(?:'?s| is)|whats) (?:on|is on) (?:my |the )?screen|"
    r"(?:look at|see|check|describe|read|view|capture|analyze|analyse|scan) "
    r"(?:my |the )?(?:screen|display|monitor|monitors|desktop)|"
    r"can you (?:see|view)|"
    r"what (?:\w+ )?am i (?:looking at|doing|watching|playing|reading|seeing)|"
    r"what (?:i'?m|im) (?:looking at|doing|watching|playing|reading)|"
    r"(?:i'?m|im|i am) (?:watching|playing|streaming|reading)|"
    r"(?:the|this|that) (?:anime|show|movie|film|series|episode|video|game|stream) "
    r"(?:i'?m|im|i am|on|playing|right now)|"
    r"what(?:'?s| is) (?:the |this )?(?:anime|show|movie|film|series|episode|game)\b|"
    r"what (?:anime|show|movie|film|series|episode|video|game) "
    r"(?:is this|is that|is playing|is currently|am i)|"
    r"take a screenshot|screen ?shot"
    r")\b",
    re.IGNORECASE,
)


def is_technical(text: str) -> bool:
    low = text.lower().strip().rstrip("!?.").strip()
    if low in NO_SEARCH:
        return False
    if _PHYS_RE.search(text) or _TECH_RE.search(text):
        return True
    return len(text) > 120


def visible_answer(raw: str) -> tuple[str, bool]:
    """Split a (possibly mid-stream) reply into the shown answer + a 'thinking' flag.

    deepseek-r1 emits a <think>...</think> block before its answer; we hide it.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    idx = cleaned.find("<think>")
    thinking = idx != -1
    if thinking:
        cleaned = cleaned[:idx]
    tail = cleaned.lstrip()
    if tail and "<think>".startswith(tail) and tail != "<think>":
        return "", True
    return cleaned, thinking


# --- Theme (Catppuccin Mocha) ---------------------------------------------
BG       = "#1e1e2e"
PANEL    = "#181825"
INPUT_BG = "#313244"
FG       = "#cdd6f4"
YOU      = "#89b4fa"
JARVIS   = "#a6e3a1"
MUTED    = "#6c7086"
ACCENT   = "#cba6f7"
CLOSE_HL = "#f38ba8"


def _setup_dpi() -> float:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def _work_area() -> tuple[int, int, int, int]:
    try:
        r = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return 0, 0, 1920, 1040


def _list_monitors() -> list:
    rects: list = []
    try:
        proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def _cb(hmon, hdc, lprc, lparam):
            r = lprc.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return 1

        cb = proc_type(_cb)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, cb, 0)
    except Exception:
        return []
    rects.sort(key=lambda r: r[0])
    return rects


def _encode_png(im) -> str:
    w, h = im.size
    if max(w, h) > 2560:
        s = 2560 / max(w, h)
        im = im.resize((int(w * s), int(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class JarvisChat:
    def __init__(self, root: tk.Tk, scale: float):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.history: list[dict] = []
        self.busy = False
        self._alpha = 1.0
        self._capturing = False
        self._minimized = False
        self._restore_geo = None
        self._drag_off = (0, 0)
        self._resize_origin = (0, 0, 0, 0)

        root.title("JARVIS")
        root.configure(bg=BG)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 1.0)

        header = tk.Frame(root, bg=BG, height=34)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        title = tk.Label(header, text="● JARVIS", bg=BG, fg=ACCENT,
                         font=("Segoe UI", 11, "bold"))
        title.pack(side="left", padx=12)
        close = tk.Button(header, text="✕", command=self.close, bg=BG, fg=MUTED,
                          activebackground=BG, activeforeground=CLOSE_HL, bd=0,
                          relief="flat", font=("Segoe UI", 12), cursor="hand2")
        close.pack(side="right", padx=(0, 8))
        self.min_btn = tk.Button(header, text="—", command=self._toggle_minimize,
                                 bg=BG, fg=MUTED, activebackground=BG,
                                 activeforeground=FG, bd=0, relief="flat",
                                 font=("Segoe UI", 11, "bold"), cursor="hand2")
        self.min_btn.pack(side="right", padx=0)
        for _w in (header, title):
            _w.bind("<Button-1>", self._drag_start)
            _w.bind("<B1-Motion>", self._drag_move)

        bottom = tk.Frame(root, bg=BG)
        bottom.pack(fill="x", side="bottom", padx=8, pady=(4, 8))
        self.entry = tk.Text(bottom, height=3, wrap="word", bg=INPUT_BG, fg=FG,
                             bd=0, padx=10, pady=8, font=("Segoe UI", 11),
                             insertbackground=FG, relief="flat", highlightthickness=1,
                             highlightbackground=INPUT_BG, highlightcolor=ACCENT)
        self.entry.pack(side="left", fill="both", expand=True)
        self.send_btn = tk.Button(bottom, text="Send", command=self.send,
                                  bg=ACCENT, fg=BG, activebackground=YOU,
                                  activeforeground=BG, bd=0, relief="flat",
                                  font=("Segoe UI", 10, "bold"), width=7, cursor="hand2")
        self.send_btn.pack(side="right", fill="y", padx=(6, 0))

        self.status = tk.Label(root, text="connecting to Ollama…", bg=BG, fg=MUTED,
                               font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x", side="bottom", padx=12)

        self.view = scrolledtext.ScrolledText(
            root, wrap="word", bg=PANEL, fg=FG, bd=0, padx=12, pady=10,
            font=("Segoe UI", 11), insertbackground=FG, relief="flat",
            state="disabled", highlightthickness=0,
        )
        self.view.pack(fill="both", expand=True, padx=8, pady=(2, 4))
        self.view.tag_config("you_label",    foreground=YOU,    font=("Segoe UI", 9, "bold"),
                              spacing1=8, spacing3=2)
        self.view.tag_config("jarvis_label", foreground=JARVIS, font=("Segoe UI", 9, "bold"),
                              spacing1=8, spacing3=2)
        self.view.tag_config("msg",   foreground=FG,    spacing3=4)
        self.view.tag_config("note",  foreground=MUTED, font=("Segoe UI", 9, "italic"),
                              spacing1=6, spacing3=6)

        self.grip = tk.Label(root, text="◢", bg=PANEL, fg=MUTED,
                             font=("Segoe UI", 11, "bold"),
                             cursor="bottom_right_corner")
        self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-2, y=-2)
        self.grip.bind("<Button-1>", self._resize_start)
        self.grip.bind("<B1-Motion>", self._resize_drag)

        self.entry.bind("<Return>", self._on_return)
        root.protocol("WM_DELETE_WINDOW", self.close)

        wl, wt, wr, wb = _work_area()
        thick = int(PANEL_SIZE * scale)
        if EDGE_NAME == "left":
            self._panel_w, self._panel_h, px, py = thick, wb - wt, wl, wt
        elif EDGE_NAME == "top":
            self._panel_w, self._panel_h, px, py = wr - wl, thick, wl, wt
        else:
            self._panel_w, self._panel_h, px, py = thick, wb - wt, wr - thick, wt
        root.geometry(f"{self._panel_w}x{self._panel_h}+{px}+{py}")

        self._note(f"JARVIS — fast: {FAST_MODEL}  ·  deep: {DEEP_MODEL}  ·  vision: {VISION_MODEL}\n"
                   "Drag the header to move · ◢ corner to resize · — minimizes · ✕ closes. "
                   "Fades when idle, solid when you use it. "
                   "Ask 'what's on my screen' and JARVIS will look. "
                   "Enter sends (Shift+Enter = newline). Commands: /clear  /help  /quit")
        threading.Thread(target=self._check_ollama, daemon=True).start()
        self.root.after(60, self._poll)
        self.entry.focus_force()

    def close(self):
        self.root.destroy()

    def _drag_start(self, e):
        self._drag_off = (e.x_root - self.root.winfo_x(),
                          e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        x = e.x_root - self._drag_off[0]
        y = e.y_root - self._drag_off[1]
        self.root.geometry(f"+{x}+{y}")

    def _toggle_minimize(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        if self._minimized:
            self.root.geometry(f"{self._panel_w}x{self._panel_h}+{x}+{y}")
            self.min_btn.config(text="—")
            self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-2, y=-2)
            self._minimized = False
        else:
            self.grip.place_forget()
            self.root.geometry(f"{self._panel_w}x34+{x}+{y}")
            self.min_btn.config(text="+")
            self._minimized = True

    def _resize_start(self, e):
        self._resize_origin = (e.x_root, e.y_root,
                               self.root.winfo_width(), self.root.winfo_height())

    def _resize_drag(self, e):
        x0, y0, w0, h0 = self._resize_origin
        w = max(220, w0 + (e.x_root - x0))
        h = max(200, h0 + (e.y_root - y0))
        self.root.geometry(f"{w}x{h}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self._panel_w, self._panel_h = w, h

    def _on_return(self, event):
        if event.state & 0x0001:
            return None
        self.send()
        return "break"

    def send(self):
        text = self.entry.get("1.0", "end").strip()
        if not text or self.busy:
            return
        self.entry.delete("1.0", "end")
        if text.startswith("/"):
            self._command(text)
            return

        self._write("You\n", "you_label")
        self._write(text + "\n", "msg")
        self.history.append({"role": "user", "content": text})
        self.busy = True
        self.send_btn.config(state="disabled")
        self._write("JARVIS\n", "jarvis_label")

        if SCREEN_RE.search(text):
            self._set_status("capturing your screen…")
            self._capturing = True
            self.root.attributes("-alpha", 1.0)
            self._alpha = 1.0
            self._restore_geo = self.root.geometry()
            self.root.withdraw()
            self.root.after(240, lambda q=text: threading.Thread(
                target=self._screen_worker, args=(q,), daemon=True).start())
        else:
            model = DEEP_MODEL if is_technical(text) else FAST_MODEL
            self._set_status(f"routing to {model}…")
            threading.Thread(target=self._worker, args=(list(self.history), model),
                             daemon=True).start()

    def _command(self, cmd: str):
        c = cmd.lower().strip()
        if c in ("/quit", "/exit"):
            self.close()
        elif c == "/clear":
            self.history.clear()
            self.view.config(state="normal")
            self.view.delete("1.0", "end")
            self.view.config(state="disabled")
            self._note("Conversation cleared.")
        elif c == "/help":
            self._note("Just talk naturally — JARVIS auto-routes casual vs. technical "
                       "messages.\nCommands:  /clear (reset)   /quit (close)")
        else:
            self._note(f"Unknown command: {cmd}")

    def _check_ollama(self):
        try:
            with urllib.request.urlopen(TAGS_URL, timeout=5):
                self.q.put(("status", "ready"))
        except Exception:
            self.q.put(("note", "Can't reach Ollama. Make sure it's running "
                                "(it should start automatically with Windows after install)."))

    def _worker(self, history: list[dict], model: str):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-20:]
        payload = json.dumps({
            "model": model, "messages": messages, "stream": True, "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
        }).encode()
        t0 = time.time()
        raw, emitted = "", 0
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        self.q.put(("fail", str(obj["error"])))
                        return
                    raw += obj.get("message", {}).get("content", "")
                    answer, thinking = visible_answer(raw)
                    if len(answer) > emitted:
                        chunk = answer[emitted:]
                        if emitted == 0:
                            chunk = chunk.lstrip()
                        emitted = len(answer)
                        if chunk:
                            self.q.put(("token", chunk))
                    elif thinking and emitted == 0:
                        self.q.put(("status", "JARVIS is thinking…"))
                    if obj.get("done"):
                        break
            answer, _ = visible_answer(raw)
            self.q.put(("done", round(time.time() - t0, 1), model, answer.strip()))
        except urllib.error.URLError as e:
            self.q.put(("fail", f"connection error: {e.reason}"))
        except Exception as e:
            self.q.put(("fail", str(e)))

    def _screen_worker(self, question: str):
        t0 = time.time()
        try:
            if ImageGrab is None:
                self.q.put(("show_panel",))
                self.q.put(("fail", "Pillow isn't installed — run:  pip install pillow"))
                return
            shots = []
            for (l, t, r, b) in _list_monitors():
                try:
                    shots.append(ImageGrab.grab(bbox=(l, t, r, b), all_screens=True))
                except Exception:
                    pass
            if not shots:
                shots = [ImageGrab.grab(all_screens=True)]
            self.q.put(("show_panel",))
            n = len(shots)
            self.q.put(("status",
                        f"JARVIS is looking at your screen{'s' if n > 1 else ''}…"))
            images_b64 = [_encode_png(im) for im in shots]
        except Exception as e:
            self.q.put(("show_panel",))
            self.q.put(("fail", f"screen capture failed: {e}"))
            return

        if n > 1:
            screen_note = (f"You are given {n} screenshots — one per monitor, "
                           f"monitor 1 through monitor {n}, left to right. ")
        else:
            screen_note = "This is a screenshot of the user's screen. "
        prompt = (
            (question.strip() or "What is on my screens?") + "\n"
            + screen_note +
            "Describe what is on each — apps, windows, any video or show playing, and "
            "what the user appears to be doing. Read important on-screen text, and say "
            "which monitor something is on when it matters."
        )
        payload = json.dumps({
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": images_b64}],
            "stream": True, "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
        }).encode()
        raw, emitted = "", 0
        try:
            req = urllib.request.Request(
                CHAT_URL, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        self.q.put(("fail", str(obj["error"])))
                        return
                    raw += obj.get("message", {}).get("content", "")
                    if len(raw) > emitted:
                        self.q.put(("token", raw[emitted:]))
                        emitted = len(raw)
                    if obj.get("done"):
                        break
            self.q.put(("done", round(time.time() - t0, 1), VISION_MODEL, raw.strip()))
        except urllib.error.URLError as e:
            self.q.put(("fail", f"connection error: {e.reason}"))
        except Exception as e:
            self.q.put(("fail", str(e)))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "token":
                    self._stream(item[1])
                elif kind == "status":
                    if item[1] == "ready":
                        self._set_status(f"ready  ·  {FAST_MODEL} / {DEEP_MODEL}")
                    else:
                        self._set_status(item[1])
                elif kind == "note":
                    self._note(item[1])
                elif kind == "show_panel":
                    try:
                        self.root.deiconify()
                        if self._restore_geo:
                            self.root.geometry(self._restore_geo)
                        self.root.lift()
                        self.root.attributes("-topmost", True)
                    except Exception:
                        pass
                    self._capturing = False
                elif kind == "done":
                    elapsed, model, answer = item[1], item[2], item[3]
                    if not answer:
                        self._stream("[no response]")
                    self.history.append({"role": "assistant", "content": answer})
                    self._finish(f"{model}  ·  {elapsed}s")
                elif kind == "fail":
                    self._stream(f"[error: {item[1]}]")
                    self._finish("error")
        except queue.Empty:
            pass
        self._tick_opacity()
        self.root.after(60, self._poll)

    def _tick_opacity(self):
        if self._capturing:
            return
        try:
            hovered = self.root.winfo_containing(*self.root.winfo_pointerxy()) is not None
            focused = self.root.focus_get() is not None
        except Exception:
            hovered = focused = True
        target = 1.0 if (hovered or focused or self.busy) else IDLE_ALPHA
        if abs(self._alpha - target) < 0.03:
            self._alpha = target
        else:
            self._alpha += 0.14 if target > self._alpha else -0.14
            self._alpha = min(1.0, max(IDLE_ALPHA, self._alpha))
        try:
            self.root.attributes("-alpha", self._alpha)
        except Exception:
            pass

    def _stream(self, chunk: str):
        self.view.config(state="normal")
        self.view.insert("end", chunk, "msg")
        self.view.see("end")
        self.view.config(state="disabled")

    def _finish(self, status: str):
        self.view.config(state="normal")
        self.view.insert("end", "\n", "msg")
        self.view.config(state="disabled")
        self.busy = False
        self.send_btn.config(state="normal")
        self._set_status(status)
        self.entry.focus_set()

    def _write(self, text: str, tag: str):
        self.view.config(state="normal")
        self.view.insert("end", text, tag)
        self.view.see("end")
        self.view.config(state="disabled")

    def _note(self, text: str):
        self._write(text + "\n", "note")

    def _set_status(self, text: str):
        self.status.config(text=text)


def main():
    scale = _setup_dpi()
    root = tk.Tk()
    JarvisChat(root, scale)
    root.mainloop()


if __name__ == "__main__":
    main()
