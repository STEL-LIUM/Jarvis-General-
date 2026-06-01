#!/usr/bin/env python3
"""
Self-improving design memory — JARVIS gets better at 3D modelling the more he
does it, by learning from his own VISUALLY-APPROVED designs.

Every design that passes the visual self-check is stored as
    {prompt, script, score, ts}
plus its request embedding. On a NEW request, we retrieve the highest-scoring
PAST design whose prompt is semantically similar, and inject it into the design
prompt as a worked example ("here's a sword you made before that scored 9/10 —
build on this approach"). So each good model makes future models better.

Reuses the ONNX MiniLM embedder already loaded by jarvis_router (embed_text) —
no new dependency, no extra load cost. Cosine similarity == dot product because
embed_text returns L2-normalized vectors.

Everything is local JSON the user can read or wipe. Fails silently/open: if the
embedder or store is unavailable, retrieval just returns nothing and the design
pipeline behaves exactly as before.
"""
from __future__ import annotations
import os, json, time, threading

_CFG_DIR_FN = None       # callable -> config dir
_lock = threading.Lock()

MAX_EXEMPLARS = 200      # cap the store; prune lowest-score oldest beyond this
SIM_THRESHOLD = 0.55     # min cosine sim for a retrieved exemplar to be relevant
MIN_SCORE = 6            # only store/retrieve designs the critic scored >= this


def configure(cfg_dir_fn) -> None:
    global _CFG_DIR_FN
    _CFG_DIR_FN = cfg_dir_fn


def _path() -> str:
    d = _CFG_DIR_FN() if _CFG_DIR_FN else os.path.expanduser("~")
    return os.path.join(d, "design_memory.jsonl")


def _embed(text: str):
    """Shared MiniLM embedding (L2-normalized) or None if embedder unavailable."""
    try:
        import jarvis_router
        return jarvis_router.embed_text(text)
    except Exception:
        return None


def _load() -> list:
    rows = []
    try:
        with open(_path(), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    rows.append(json.loads(ln))
    except (FileNotFoundError, OSError, ValueError):
        pass
    return rows


def remember(prompt: str, script: str, score: int) -> None:
    """Store a visually-approved design. Background-safe, best-effort."""
    if not prompt or not script or score < MIN_SCORE:
        return
    vec = _embed(prompt)
    emb = list(map(float, vec.ravel())) if vec is not None else None

    def _go() -> None:
        with _lock:
            rows = _load()
            # replace any existing exemplar for the same prompt if this scores >=
            rows = [r for r in rows
                    if not (r.get("prompt") == prompt and r.get("score", 0) <= score)]
            rows.append({"prompt": prompt, "script": script, "score": int(score),
                         "ts": int(time.time()), "emb": emb})
            # prune: keep the best MAX_EXEMPLARS (by score, then recency)
            if len(rows) > MAX_EXEMPLARS:
                rows.sort(key=lambda r: (r.get("score", 0), r.get("ts", 0)),
                          reverse=True)
                rows = rows[:MAX_EXEMPLARS]
            try:
                os.makedirs(os.path.dirname(_path()), exist_ok=True)
                with open(_path(), "w", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            except OSError:
                pass

    threading.Thread(target=_go, daemon=True).start()


def best_exemplar(prompt: str) -> dict | None:
    """Return the most relevant high-scoring past design for this request, or
    None. Match = highest (cosine_sim * score) above SIM_THRESHOLD."""
    rows = _load()
    if not rows:
        return None
    qv = _embed(prompt)
    best, best_rank = None, 0.0
    if qv is not None:
        q = qv.ravel()
        for r in rows:
            emb = r.get("emb")
            if not emb:
                continue
            # cosine sim (both normalized) = dot product
            sim = sum(a * b for a, b in zip(q, emb))
            if sim < SIM_THRESHOLD:
                continue
            rank = sim * (r.get("score", 0) / 10.0)
            if rank > best_rank:
                best, best_rank = r, rank
    else:
        # no embedder — fall back to a crude word-overlap match
        pset = set(prompt.lower().split())
        for r in rows:
            ov = len(pset & set(str(r.get("prompt", "")).lower().split()))
            rank = ov * (r.get("score", 0) / 10.0)
            if ov >= 2 and rank > best_rank:
                best, best_rank = r, rank
    return best


def exemplar_prompt_block(prompt: str) -> str:
    """A worked-example block to append to the design system prompt, or '' if no
    relevant past success exists."""
    ex = best_exemplar(prompt)
    if not ex:
        return ""
    return (
        "\n\nWORKED EXAMPLE FROM YOUR OWN PAST SUCCESS — you previously made "
        f"\"{ex.get('prompt','')}\" and it scored {ex.get('score','?')}/10 on the "
        "visual check. Reuse this proven approach/structure as a starting point, "
        "adapting it to the current request:\n```python\n"
        + str(ex.get("script", "")).strip() + "\n```")


def stats() -> dict:
    rows = _load()
    if not rows:
        return {"count": 0, "avg_score": 0, "path": _path()}
    return {"count": len(rows),
            "avg_score": round(sum(r.get("score", 0) for r in rows) / len(rows), 1),
            "path": _path()}
