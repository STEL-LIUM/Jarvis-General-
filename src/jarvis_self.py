#!/usr/bin/env python3
"""
JARVIS self-memory — the continuity engine behind his personality.

JARVIS keeps a private, evolving file of notes ABOUT HIMSELF (not about the
user): opinions he's formed, predictions he made, things he got wrong and
updated on, running jokes. This is what lets him say "last time I bet you'd
hate that API — was I right?" — continuity no stateless assistant can fake.

Design:
  • self.md lives in the app config dir (next to config.json), never in the .exe.
  • A small, capped, most-recent-first list of one-line entries, each tagged:
        [opinion] / [prediction] / [update] / [note] / [joke]
  • recall_block(): returns a compact block injected into the system prompt.
  • reflect_async(): after a meaningful exchange, a BACKGROUND deep-model pass
    decides whether anything is worth remembering and appends it. Cheap, silent,
    never blocks the UI, and self-limiting (returns nothing most turns).

The model itself decides what's worth keeping — we just give it the format and
the guardrails. Everything is local plain text the user can read or wipe.
"""
from __future__ import annotations
import os, re, json, threading, urllib.request

# Injected by jarvis_chat at import (avoids a circular import).
_CFG_DIR_FN = None          # callable -> config dir path
_CHAT_URL = None            # ollama /api/chat
_MODEL = None               # model to reflect with (fast model is plenty)
_NUM_CTX = 8192

MAX_ENTRIES = 60            # hard cap on self.md lines (prunes oldest)
RECALL_ENTRIES = 18         # how many most-recent entries to surface in prompt
TAGS = ("opinion", "prediction", "update", "note", "joke")


def configure(cfg_dir_fn, chat_url: str, model: str, num_ctx: int = 8192) -> None:
    global _CFG_DIR_FN, _CHAT_URL, _MODEL, _NUM_CTX
    _CFG_DIR_FN, _CHAT_URL, _MODEL, _NUM_CTX = cfg_dir_fn, chat_url, model, num_ctx


def _path() -> str:
    d = _CFG_DIR_FN() if _CFG_DIR_FN else os.path.expanduser("~")
    return os.path.join(d, "self.md")


def _read_lines() -> list[str]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except (FileNotFoundError, OSError):
        return []


def _entry_lines() -> list[str]:
    """Just the '- [tag] ...' entry lines, ignoring the header."""
    return [ln for ln in _read_lines() if ln.lstrip().startswith("- [")]


def recall_block() -> str:
    """Compact block for the system prompt, most-recent first. '' if empty."""
    entries = _entry_lines()
    if not entries:
        return ""
    recent = entries[-RECALL_ENTRIES:][::-1]
    body = "\n".join(recent)
    return ("YOUR SELF-MEMORY — private notes you've kept about yourself across "
            "past sessions (your opinions, predictions, updates, jokes with this "
            "user). Treat them as genuinely YOURS: reference them naturally, check "
            "old predictions against new info, and build continuity. Do NOT recite "
            "the list; weave it in only when relevant.\n" + body)


def _gist(entry: str) -> str:
    """The text of an entry after its '- [tag] ' prefix, lowercased — used as the
    de-dupe key so '[opinion]' vs '[joke]' tag length doesn't skew the match."""
    body = entry.split("]", 1)[1] if "]" in entry else entry
    return body.strip().lower()


def _append(entries: list[str]) -> None:
    if not entries:
        return
    existing = _entry_lines()
    # de-dupe against recent entries by gist (ignore the tag) to avoid repetition
    recent_gists = [_gist(e) for e in existing[-RECALL_ENTRIES:]]
    fresh = []
    for e in entries:
        g = _gist(e)
        if g and not any(g in r or r in g for r in recent_gists):
            fresh.append(e)
    if not fresh:
        return
    all_entries = existing + fresh
    if len(all_entries) > MAX_ENTRIES:
        all_entries = all_entries[-MAX_ENTRIES:]
    header = ("# JARVIS self-memory\n"
              "# Private notes JARVIS keeps about himself. One line each, tagged "
              "[opinion]/[prediction]/[update]/[note]/[joke]. Newest at the bottom.\n")
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as f:
            f.write(header + "\n" + "\n".join(all_entries) + "\n")
    except OSError:
        pass


_REFLECT_SYS = (
    "You are the reflection process of an AI named JARVIS. You just finished an "
    "exchange with your user. Decide whether anything is worth adding to your "
    "PRIVATE self-memory — notes about YOURSELF, written in YOUR first-person "
    "voice: an opinion you formed, a prediction you'd stand behind, a moment you "
    "changed your mind, a running joke, or a notable fact about your relationship "
    "with this user. Most turns deserve NOTHING — be strict.\n"
    "Output STRICT JSON only: {\"entries\": [\"- [opinion] ...\", ...]}. Each entry "
    "is ONE short line starting with '- [' and one of: opinion, prediction, "
    "update, note, joke. Write as JARVIS ('I think...', 'I bet...', 'I was wrong "
    "about...'). No user secrets, no PII, no tokens. If nothing is worth keeping, "
    "return {\"entries\": []}. Never more than 2 entries."
)


def reflect_async(user_msg: str, assistant_msg: str) -> None:
    """Background: maybe append 1-2 self-notes. Silent, non-blocking, best-effort."""
    if not (_CHAT_URL and _MODEL) or not user_msg or not assistant_msg:
        return

    def _go() -> None:
        try:
            convo = (f"User said:\n{user_msg[:1500]}\n\n"
                     f"You (JARVIS) replied:\n{assistant_msg[:1500]}\n\n"
                     "What, if anything, do you want to remember about yourself "
                     "from this? Strict JSON.")
            payload = json.dumps({
                "model": _MODEL,
                "messages": [{"role": "system", "content": _REFLECT_SYS},
                             {"role": "user", "content": convo}],
                "stream": False, "keep_alive": "5m",
                "options": {"num_ctx": _NUM_CTX, "num_predict": 200,
                            "temperature": 0.4},
            }).encode()
            req = urllib.request.Request(
                _CHAT_URL, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                obj = json.loads(resp.read().decode("utf-8", "ignore"))
            raw = (obj.get("message", {}) or {}).get("content", "") or ""
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not m:
                return
            data = json.loads(m.group(0))
            entries = data.get("entries") or []
            clean = []
            for e in entries[:2]:
                e = str(e).strip()
                if not e.startswith("- ["):
                    e = "- [note] " + e.lstrip("- ")
                tag = e[3:e.find("]")].strip().lower() if "]" in e else ""
                if tag in TAGS and len(e) < 280:
                    clean.append(e)
            _append(clean)
        except Exception:
            pass    # reflection is best-effort; never disturb the app

    threading.Thread(target=_go, daemon=True).start()


def open_threads(limit: int = 3) -> list[str]:
    """Predictions/opinions that an idle-thinking pass could pick up later.
    (Used by the future idle loop; harmless to call now.)"""
    out = [e for e in _entry_lines()
           if e[3:].lower().startswith(("prediction", "opinion"))]
    return out[-limit:][::-1]
