#!/usr/bin/env python3
"""
JARVIS Chat — learned multi-class INTENT classifier (PROMETHEUS, Phase 2).

Augments the brittle SCREEN_RE / DESIGN_RE trigger regexes with a learned
classifier that catches keyword-less phrasings the regexes miss:

    text -> MiniLM embedding (ONNX) -> MinMax scale -> PROMETHEUS council
         -> intent in {CASUAL, TECHNICAL, DESIGN, SCREEN, PC_COMMAND,
                        SEARCH, MEMORY}

Runtime deps are ONLY onnxruntime + tokenizers + numpy + scikit-learn (all
already bundled for the router/voice) plus router_models/intent.pkl. NO torch.

SAFETY: every failure path returns ("", 0.0) so callers fall back to the regex.
If the model, embedder, or pickle is missing/errors, classify_intent never
raises — the app keeps working exactly as before. The embedder is SHARED with
jarvis_router (one ONNX session for both), so importing this adds no load cost
beyond the small intent.pkl.
"""
from __future__ import annotations

import os
import pickle
import threading

# Default confidence floor before a learned SCREEN/DESIGN trigger is trusted.
# Triggers are mildly consequential (screen capture / Blender run), so we only
# act on a confident learned prediction; below this, the regex alone decides.
# Override with JARVIS_INTENT_THRESHOLD.
try:
    INTENT_THRESHOLD = float(os.environ.get("JARVIS_INTENT_THRESHOLD", "0.55"))
except Exception:
    INTENT_THRESHOLD = 0.55

_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "router_models")
_state = {"loaded": False, "ok": False, "err": "",
          "council": None, "scaler": None, "classes": None}
_lock = threading.Lock()


def _lazy_load() -> None:
    """Load intent.pkl once, on first use. Reuses the embedder from
    jarvis_router (shared ONNX session). Any failure leaves ok=False."""
    if _state["loaded"]:
        return
    with _lock:
        if _state["loaded"]:
            return
        _state["loaded"] = True
        try:
            import jarvis_router  # shared ONNX embedder (embed_text)
            pkl_path = os.path.join(_MODELS_DIR, "intent.pkl")
            if not os.path.isfile(pkl_path):
                _state["err"] = "intent.pkl missing"
                return
            # Make sure the shared embedder actually loads, else we can't embed.
            if jarvis_router.embed_text("warmup") is None:
                _state["err"] = "embedder unavailable"
                return
            with open(pkl_path, "rb") as f:
                payload = pickle.load(f)
            _state.update(council=payload["council"],
                          scaler=payload["scaler"],
                          classes=payload["class_names"], ok=True)
        except Exception as e:           # noqa: BLE001 — never crash the app
            _state["ok"] = False
            _state["err"] = f"{type(e).__name__}: {e}"


def classify_intent(text: str) -> tuple[str, float]:
    """Return (intent_label, confidence) for `text`, or ("", 0.0) if the
    learned classifier is unavailable or errors. Confidence is the mean
    soft-vote probability of the winning class across the council. Never raises.
    """
    try:
        _lazy_load()
        if not _state["ok"]:
            return ("", 0.0)
        import jarvis_router
        v = jarvis_router.embed_text(text)
        if v is None:
            return ("", 0.0)
        x = _state["scaler"].transform(v.reshape(1, -1))[0]
        # SwarmCouncil.predict_soft(x) -> (pred_idx, mean_prob_vector) for a
        # single 1-D vector. It does NOT batch.
        i, probs = _state["council"].predict_soft(x)
        return (_state["classes"][int(i)], float(probs[int(i)]))
    except Exception:
        return ("", 0.0)


def classify_full(text: str) -> list[tuple[str, float]]:
    """Return the full per-class probability distribution as a list of
    (label, prob) sorted high->low, or [] if the classifier is unavailable.
    Used by the /intent diagnostic command. Never raises."""
    try:
        _lazy_load()
        if not _state["ok"]:
            return []
        import jarvis_router
        v = jarvis_router.embed_text(text)
        if v is None:
            return []
        x = _state["scaler"].transform(v.reshape(1, -1))[0]
        _, probs = _state["council"].predict_soft(x)
        pairs = list(zip(_state["classes"], (float(p) for p in probs)))
        pairs.sort(key=lambda kv: kv[1], reverse=True)
        return pairs
    except Exception:
        return []


def is_intent(text: str, label: str, threshold: float | None = None) -> bool:
    """True if `text` is confidently classified as `label`. Convenience wrapper
    for the trigger sites. Returns False (not an error) when the classifier is
    unavailable, so callers combine it with the regex via OR."""
    want = threshold if threshold is not None else INTENT_THRESHOLD
    pred, conf = classify_intent(text)
    return pred == label and conf >= want


def intent_status() -> str:
    """'prometheus' if the learned intent model loaded, else 'fallback: <reason>'."""
    _lazy_load()
    return "prometheus" if _state["ok"] else f"fallback: {_state['err']}"


# ── Active-learning loop ──────────────────────────────────────────────────────
# Every classified turn is logged locally (NEVER uploaded — same privacy rule as
# screenshots). Turns the classifier was UNSURE about (confidence in the
# uncertain band) are the highest-value examples to relabel and fold back into
# training. This is what lets the model climb past synthetic-data accuracy on the
# user's REAL phrasing distribution.

# Confidence band where the model is "not sure" — worth a human label.
UNCERTAIN_LO = float(os.environ.get("JARVIS_INTENT_UNCERTAIN_LO", "0.35"))
UNCERTAIN_HI = float(os.environ.get("JARVIS_INTENT_UNCERTAIN_HI", "0.65"))
# Logging on by default; opt out with JARVIS_INTENT_LOG=0.
_LOG_ENABLED = os.environ.get("JARVIS_INTENT_LOG", "1") != "0"
_LOG_MAX_LINES = 5000   # rotate to keep the file bounded


def _log_dir() -> str:
    """%LOCALAPPDATA%\\JarvisChat (matches the app's config dir), or ~ fallback."""
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "JarvisChat")
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


def _log_path() -> str:
    return os.path.join(_log_dir(), "intent_log.jsonl")


def observe(text: str, fired: "str | None" = None) -> tuple[str, float]:
    """Classify `text`, append one local log line, and return (label, conf).
    `fired` = the action actually triggered this turn ('SCREEN'/'DESIGN' or None
    for plain chat) so we can later see where the learned label and the real
    outcome diverged. Never raises; logging failures are swallowed."""
    label, conf = classify_intent(text)
    if not _LOG_ENABLED or not label:
        return label, conf
    try:
        import json
        rec = {"text": text, "pred": label, "conf": round(conf, 4),
               "fired": fired, "uncertain": UNCERTAIN_LO <= conf <= UNCERTAIN_HI}
        path = _log_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _maybe_rotate(path)
    except Exception:
        pass
    return label, conf


def _maybe_rotate(path: str) -> None:
    """Keep only the most recent _LOG_MAX_LINES so the log can't grow forever."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > _LOG_MAX_LINES:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-_LOG_MAX_LINES:])
    except Exception:
        pass


def uncertain_turns(limit: int = 25) -> list:
    """Return recent logged turns the classifier was unsure about — the
    relabeling queue for the next training round. Empty list on any error."""
    try:
        import json
        path = _log_path()
        if not os.path.isfile(path):
            return []
        out = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("uncertain"):
                    out.append(r)
        # Most recent first, de-duplicated by text.
        seen, uniq = set(), []
        for r in reversed(out):
            t = r.get("text")
            if t in seen:
                continue
            seen.add(t)
            uniq.append(r)
        return uniq[:limit]
    except Exception:
        return []


def log_stats() -> dict:
    """Summary of the local intent log (count, uncertain count, path)."""
    try:
        import json
        path = _log_path()
        if not os.path.isfile(path):
            return {"total": 0, "uncertain": 0, "path": path}
        total = unc = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    if json.loads(line).get("uncertain"):
                        unc += 1
                except Exception:
                    pass
        return {"total": total, "uncertain": unc, "path": path}
    except Exception:
        return {"total": 0, "uncertain": 0, "path": ""}
