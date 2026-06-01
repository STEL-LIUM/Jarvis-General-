#!/usr/bin/env python3
"""
JARVIS Chat — learned fast/deep router (PROMETHEUS).

Replaces the regex is_technical() heuristic with a learned classifier:

    text -> MiniLM embedding (ONNX) -> MinMax scale -> PROMETHEUS council -> bool

Runtime deps are ONLY onnxruntime + tokenizers + numpy (all already bundled for
the voice subsystem) plus the pickled model in router_models/. NO torch.

SAFETY: every failure path falls back to the original regex heuristic. If the
ONNX model, tokenizer, pickle, or any dependency is missing or errors, the app
keeps working exactly as before — route_is_technical() never raises.
"""
from __future__ import annotations

import os
import re
import pickle
import threading

# ── Regex fallback (mirrors the original is_technical in jarvis_chat.py) ───────
NO_SEARCH = {
    "hi", "hey", "hello", "yo", "sup", "thanks", "thank you", "ok", "okay",
    "cool", "nice", "lol", "haha", "good morning", "good night", "bye",
    "how are you", "what's up", "whats up", "gm", "gn",
}
_PHYS_RE = re.compile(r"\b(physics|quantum|energy|force|momentum|entropy|"
                      r"thermodynamic|relativity|equation|derive|velocity|"
                      r"acceleration|frequency|wavelength|voltage|current|"
                      r"electron|photon|particle|gravity|orbit)\b", re.I)
_TECH_RE = re.compile(r"\b(code|python|function|algorithm|debug|compile|"
                      r"neural|network|model|train|gradient|matrix|tensor|"
                      r"api|database|query|server|async|class|variable|"
                      r"integral|derivative|theorem|proof|optimize)\b", re.I)


def heuristic_is_technical(text: str) -> bool:
    """The original keyword/length heuristic. Used as the safety fallback."""
    low = text.lower().strip().rstrip("!?.").strip()
    if low in NO_SEARCH:
        return False
    if _PHYS_RE.search(text) or _TECH_RE.search(text):
        return True
    return len(text) > 120


# ── Lazy-loaded PROMETHEUS router ─────────────────────────────────────────────
_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "router_models")
_state = {"loaded": False, "ok": False, "err": "",
          "sess": None, "tok": None, "council": None, "scaler": None}
_lock = threading.Lock()


def _lazy_load() -> None:
    """Load ONNX embedder + tokenizer + pickled model once, on first use.
    Any failure leaves ok=False so route_is_technical falls back to regex."""
    if _state["loaded"]:
        return
    with _lock:
        if _state["loaded"]:
            return
        _state["loaded"] = True
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            onnx_path = os.path.join(_MODELS_DIR, "minilm.onnx")
            tok_path  = os.path.join(_MODELS_DIR, "tokenizer.json")
            pkl_path  = os.path.join(_MODELS_DIR, "router.pkl")
            if not (os.path.isfile(onnx_path) and os.path.isfile(tok_path)
                    and os.path.isfile(pkl_path)):
                _state["err"] = "router_models files missing"
                return
            sess = ort.InferenceSession(onnx_path,
                                        providers=["CPUExecutionProvider"])
            tok = Tokenizer.from_file(tok_path)
            tok.enable_truncation(max_length=128)
            with open(pkl_path, "rb") as f:
                payload = pickle.load(f)
            _state.update(sess=sess, tok=tok,
                          council=payload["council"],
                          scaler=payload["scaler"], ok=True)
        except Exception as e:           # noqa: BLE001 — never let load crash the app
            _state["ok"] = False
            _state["err"] = f"{type(e).__name__}: {e}"


def _embed_one(text: str):
    import numpy as np
    enc = _state["tok"].encode(text)
    ids  = np.array([enc.ids], dtype=np.int64)
    mask = np.array([enc.attention_mask], dtype=np.int64)
    h = _state["sess"].run(None, {"input_ids": ids, "attention_mask": mask})[0]
    m = mask[:, :, None].astype(np.float32)
    v = (h * m).sum(1) / m.sum(1).clip(min=1e-9)
    v = v / np.linalg.norm(v, axis=1, keepdims=True)
    return v[0]


def route_is_technical(text: str) -> bool:
    """Return True if `text` should go to the deep model. Uses the learned
    PROMETHEUS router when available, else the regex heuristic. Never raises."""
    try:
        _lazy_load()
        if not _state["ok"]:
            return heuristic_is_technical(text)
        x = _embed_one(text).reshape(1, -1)
        x = _state["scaler"].transform(x)[0]
        pred = _state["council"].predict_soft(x)[0]
        return bool(pred == 1)           # 1 = technical
    except Exception:
        return heuristic_is_technical(text)


def router_status() -> str:
    """'prometheus' if the learned router loaded, else 'fallback: <reason>'."""
    _lazy_load()
    return "prometheus" if _state["ok"] else f"fallback: {_state['err']}"


def embed_text(text: str):
    """Return the normalized MiniLM embedding for `text` (numpy float array),
    or None if the embedder isn't available. Shared with jarvis_council for
    cross-model disagreement detection so we only load the ONNX model once."""
    try:
        _lazy_load()
        if not _state["ok"]:
            return None
        return _embed_one(text)
    except Exception:
        return None
