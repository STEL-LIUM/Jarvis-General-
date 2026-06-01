#!/usr/bin/env python3
"""
Recipe library — JARVIS's learned, reusable construction techniques.

Design memory ([[jarvis_design_memory]]) stores whole successful scripts and
retrieves the closest one as a worked example. The recipe library is the next
level of abstraction: a small set of VERIFIED, PARAMETERIZED helper snippets
distilled from many working designs of the same kind — e.g. a `build_mug()` or
`build_gear(teeth, radius)` that is known to run.

At design time, if the request matches a recipe's object type, the recipe is
injected into the prompt as a proven building block the model can call/adapt,
instead of reinventing the geometry from scratch.

Two halves:
  • RUNTIME (this module, imported by jarvis_chat): load recipes.json, and
    `recipe_prompt_block(prompt)` returns the most relevant recipe snippet.
  • BUILD (training/distill_recipes.py): clusters the kept dataset by object
    type, asks the model to distill a parameterized helper, VERIFIES it runs in
    Blender, and writes the ones that pass to recipes.json.

recipes.json ships next to the other router models. Fails open: no file → no
recipe block → pipeline behaves exactly as before.
"""
from __future__ import annotations
import os, json, re

# recipes.json lives with the other bundled models (router_models/), with a
# user-writable override in the config dir taking precedence if present.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BUNDLED = os.path.join(_HERE, "router_models", "recipes.json")
_CFG_DIR_FN = None
_cache = None   # loaded list of {"type","keywords","recipe","note"}


def configure(cfg_dir_fn) -> None:
    global _CFG_DIR_FN, _cache
    _CFG_DIR_FN = cfg_dir_fn
    _cache = None   # force reload against the (now known) config dir


def _user_path() -> str:
    d = _CFG_DIR_FN() if _CFG_DIR_FN else os.path.expanduser("~")
    return os.path.join(d, "recipes.json")


def _load() -> list:
    global _cache
    if _cache is not None:
        return _cache
    rows = []
    for path in (_user_path(), _BUNDLED):
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            if rows:
                break
        except (FileNotFoundError, OSError, ValueError):
            continue
    _cache = rows if isinstance(rows, list) else []
    return _cache


def best_recipe(prompt: str) -> dict | None:
    """Return the recipe whose keywords best overlap the request, or None."""
    rows = _load()
    if not rows:
        return None
    words = set(re.findall(r"[a-z]+", prompt.lower()))
    best, best_hits = None, 0
    for r in rows:
        kws = set(k.lower() for k in r.get("keywords", []))
        if not kws:
            t = str(r.get("type", "")).lower()
            kws = set(t.split())
        hits = len(words & kws)
        if hits > best_hits:
            best, best_hits = r, hits
    return best if best_hits >= 1 else None


def recipe_prompt_block(prompt: str) -> str:
    """A proven-helper block to append to the design system prompt, or ''."""
    r = best_recipe(prompt)
    if not r:
        return ""
    note = r.get("note", "")
    return (
        f"\n\nPROVEN RECIPE for a {r.get('type','')} — this helper is verified to "
        "run in Blender. Use it (call or inline + adapt the parameters) instead "
        "of building the geometry from scratch:"
        f"{(' ' + note) if note else ''}\n```python\n"
        + str(r.get("recipe", "")).strip() + "\n```")


def all_recipes() -> list:
    return list(_load())


def stats() -> dict:
    rows = _load()
    return {"count": len(rows),
            "types": [r.get("type") for r in rows],
            "user_path": _user_path(), "bundled": _BUNDLED}
