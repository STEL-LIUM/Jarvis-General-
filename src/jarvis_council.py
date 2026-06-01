#!/usr/bin/env python3
"""
JARVIS Chat — LLM Council (Mixture-of-Agents, PROMETHEUS-conducted).

Several diverse local models independently answer a question, then a strong
aggregator synthesizes the single best answer. Research (Mixture-of-Agents)
shows this lets open models rival frontier ones. This is the SwarmCouncil idea
from PROMETHEUS, applied to LLMs.

Pipeline:
    query ─► [proposer 1] ┐
            [proposer 2]  ├─► disagreement check ─► [aggregator] ─► answer
            [proposer 3] ┘    (low agreement =          synthesizes
                               hallucination flag)       best answer

Conducted by PROMETHEUS in two ways already wired here:
  • Router-gated upstream (jarvis_chat only convenes the council for technical
    questions — casual chat stays on the single fast model).
  • Disagreement detection reuses the oscillator-council's agreement signal:
    proposal embeddings (the same ONNX MiniLM the router uses) are compared;
    low pairwise similarity flags a likely hallucination / genuine uncertainty.

Configurable via env:
  JARVIS_COUNCIL_PROPOSERS   comma-separated model list
  JARVIS_COUNCIL_AGGREGATOR  aggregator model
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import threading
import urllib.request
import urllib.error

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")

# Coder model used to write verification snippets for computable disputes.
VERIFY_CODER = os.getenv("JARVIS_COUNCIL_VERIFIER", "qwen2.5-coder:7b")

# Diverse families = diverse mistakes = better aggregation. Defaults are tuned
# for SPEED: three lightweight proposers that co-reside in VRAM (no eviction
# between them) + a single reasoning aggregator loaded once at the end. This
# lineup ran in ~49s vs ~300s for a deepseek-heavy proposer set, with the same
# self-correction quality. Override via env. For max quality (slower), set the
# aggregator to deepseek-r1:32b.
# NOTE: full co-residence also needs the Ollama server set to keep >=3 models
# loaded: OLLAMA_MAX_LOADED_MODELS=3 (and OLLAMA_KEEP_ALIVE long enough).
COUNCIL_PROPOSERS = [
    m.strip() for m in os.getenv(
        "JARVIS_COUNCIL_PROPOSERS",
        "qwen3:8b,qwen2.5:7b,llama3:latest"
    ).split(",") if m.strip()
]
COUNCIL_AGGREGATOR = os.getenv("JARVIS_COUNCIL_AGGREGATOR", "deepseek-r1:14b")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _ollama_chat(model: str, messages: list[dict],
                 num_ctx: int = 8192, num_predict: int = 4096,
                 timeout: int = 600) -> str:
    """Non-streaming Ollama chat call. Returns the full reply text."""
    payload = json.dumps({
        "model": model, "messages": messages, "stream": False,
        "keep_alive": "10m",
        "options": {"num_ctx": num_ctx, "num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        obj = json.loads(resp.read().decode("utf-8", "ignore"))
    return obj.get("message", {}).get("content", "")


# ── Prompts ───────────────────────────────────────────────────────────────────

# Role specialization: each proposer gets a distinct lens. Same number of
# calls, but real diversity beyond model family. The SKEPTIC specifically
# targets the failure mode we keep hitting — the intuitive-but-wrong answer
# (bat-and-ball, distractor details, miscounts). Roles cycle across proposers.
COUNCIL_ROLES = [
    ("direct",
     "Give the most accurate, direct answer. Be clear and correct."),
    ("skeptic",
     "Be a careful skeptic: the obvious or intuitive answer is often a TRAP. "
     "Before committing, re-check the arithmetic, re-count anything countable, "
     "and watch for distractor details, hidden assumptions, and trick framing. "
     "Prefer the carefully-verified answer over the first one that comes to mind."),
    ("alternative",
     "Consider whether a less obvious interpretation or a different method "
     "yields a better answer. Question the framing; don't just restate the "
     "obvious approach."),
]

_FINAL_INSTR = ("\n\nEnd your response with a final line in exactly this form:\n"
                "FINAL: <your bottom-line answer in as few words as possible>\n"
                "For a number, put just the number. For yes/no, put just yes or no.")


def _proposer_messages(query: str, history: list[dict],
                       role_lens: str = "") -> list[dict]:
    sys = ("You are one expert advisor on a panel. Answer the user's question "
           "as accurately and concisely as you can. Show your reasoning briefly "
           "where it matters. Another model will later synthesize the panel's "
           "answers, so focus on being correct, not on formatting.")
    if role_lens:
        sys += "\n\nYour role on this panel: " + role_lens
    sys += _FINAL_INSTR
    return [{"role": "system", "content": sys}] + history[-8:] + \
           [{"role": "user", "content": query}]


def _aggregator_messages(query: str, proposals: list[tuple[str, str]],
                         agreement: float, verified: str | None = None) -> list[dict]:
    blocks = []
    for i, (model, ans) in enumerate(proposals, 1):
        blocks.append(f"--- Advisor {i} ({model}) ---\n{ans}")
    # The aggregator is only invoked when the advisors' bottom-line claims
    # actually differ, so always tell it that and how badly they split.
    severity = ("are SPLIT roughly evenly" if agreement <= 0.5
                else "mostly agree but at least one dissents")
    disagree_note = (
        f"\n\nIMPORTANT: the advisors' bottom-line answers {severity} "
        f"(claim agreement {agreement:.2f}). Do not just side with the majority "
        "— reason from first principles about which answer is actually correct, "
        "and explicitly flag any point where you remain uncertain.")
    sys = (
        "You are the aggregator on an expert panel. Below are independent "
        "answers from several advisor models to the user's question. Produce the "
        "single best, most accurate answer.\n"
        "- Where advisors agree, trust that and state it confidently.\n"
        "- Where they disagree, reason about which is correct; do not just "
        "average or concatenate them.\n"
        "- Correct any factual errors you can identify.\n"
        "- Be direct and well-organized. Do not mention 'advisors' or that this "
        "was a panel — just give the final answer to the user."
        + disagree_note)
    verify_block = ""
    if verified:
        verify_block = (
            f"\n\nVERIFIED RESULT: a sandboxed program was run to compute this "
            f"deterministically and produced: {verified}\n"
            "Trust this verified result over the advisors for the computable "
            "part of the answer, unless it is obviously malformed.")
    user = (f"User question:\n{query}\n\n"
            f"Advisor answers:\n\n" + "\n\n".join(blocks) + verify_block +
            "\n\nNow give the single best final answer.")
    return [{"role": "system", "content": sys},
            {"role": "user", "content": user}]


# ── Claim extraction + agreement (precise — measures FACTS, not topic) ─────────
# Embedding the whole verbose answer measured TOPIC similarity, not factual
# agreement ("two r's" vs "three r's" embed nearly identically). Instead we
# extract each proposal's bottom-line claim (the FINAL: line) and cluster those.
# Agreement = fraction of proposers in the plurality cluster — exactly the
# PROMETHEUS oscillator-council agreement signal, now at the claim level.

_FINAL_RE = re.compile(r"(?:^|\n)\s*FINAL\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_NUM_RE   = re.compile(r"-?\d+(?:\.\d+)?")


def _extract_final(text: str) -> str:
    """Pull the proposer's bottom-line claim: the FINAL: line if present,
    else the last non-empty line."""
    if not text:
        return ""
    m = _FINAL_RE.findall(text)
    if m:
        return m[-1].strip()
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _normalize_claim(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s.]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _same_claim(a: str, b: str, embed_fn=None) -> bool:
    """Do two bottom-line claims assert the same thing?"""
    na, nb = _normalize_claim(a), _normalize_claim(b)
    if na == nb and na:
        return True
    nums_a, nums_b = _NUM_RE.findall(na), _NUM_RE.findall(nb)
    # If both are numeric answers, the numbers decide it (catches 2 vs 3).
    if nums_a and nums_b:
        return set(nums_a) == set(nums_b)
    # One numeric, one not → different kinds of answer → disagree.
    if bool(nums_a) != bool(nums_b):
        return False
    # Short free-text: embedding cosine is meaningful on a one-liner.
    if embed_fn is not None:
        try:
            import numpy as np
            va, vb = embed_fn(a), embed_fn(b)
            if va is not None and vb is not None:
                va, vb = np.asarray(va, float), np.asarray(vb, float)
                denom = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1.0
                return float(np.dot(va, vb) / denom) > 0.85
        except Exception:
            pass
    return na == nb


def _claim_agreement(finals: list[str], embed_fn=None):
    """Cluster bottom-line claims. Returns (agreement_fraction, groups) where
    agreement = size of the largest cluster / number of proposers."""
    groups: list[list[int]] = []
    for i, f in enumerate(finals):
        for g in groups:
            if _same_claim(finals[g[0]], f, embed_fn):
                g.append(i)
                break
        else:
            groups.append([i])
    if not finals:
        return 1.0, groups
    plurality = max(len(g) for g in groups)
    return plurality / len(finals), groups


# ── Verify-by-execution (deterministic grounding for computable disputes) ──────
# When advisors disagree on a NUMBER, don't vote — compute it. A coder model
# writes a tiny Python snippet; we AST-validate it (pure computation only, no
# imports / IO / dangerous builtins) and run it in a sandboxed subprocess with
# a hard timeout. The deterministic result anchors the aggregator.

# Builtins allowed inside verification snippets — pure computation only.
_SAFE_BUILTINS = {
    "len", "sum", "range", "sorted", "min", "max", "abs", "round", "int",
    "float", "str", "list", "dict", "set", "tuple", "enumerate", "zip", "map",
    "filter", "all", "any", "print", "bool", "reversed", "ord", "chr",
    "divmod", "pow", "True", "False", "None",
}
# AST nodes that must never appear — block imports, with-blocks, and while
# loops (the only easy way to write an unbounded loop; counting/math snippets
# never need them, and blocking them bounds runaway execution).
_FORBIDDEN_NODES = (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith,
                    ast.Global, ast.Nonlocal, ast.Lambda, ast.ClassDef,
                    ast.AsyncFunctionDef, ast.While)


def _ast_is_safe(code: str) -> bool:
    """True only if `code` is pure computation: no imports, no dunder access,
    no calls to anything outside _SAFE_BUILTINS. Defends the sandbox."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODES):
            return False
        # Block dunder attribute access (e.g. ().__class__.__bases__ escapes)
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return False
        # Calls may only target bare names in the safe-builtins set, or methods
        # (Attribute calls like "abc".count(...) — string/list methods are fine).
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id not in _SAFE_BUILTINS:
                return False
    return True


_CODE_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_code(text: str) -> str:
    m = _CODE_FENCE.findall(text or "")
    return (m[0].strip() if m else (text or "").strip())


def _gen_verify_code(question: str) -> str:
    sys_p = (
        "You write a TINY Python snippet that deterministically computes the "
        "answer to the user's question and prints ONLY the answer.\n"
        "STRICT RULES:\n"
        "- No imports. No file/network/OS access. Pure computation only.\n"
        "- Use only basic builtins (len, sum, range, sorted, string methods...).\n"
        "- print() exactly one line: the final answer, nothing else.\n"
        "- Output ONLY the code, in a ```python block.")
    msgs = [{"role": "system", "content": sys_p},
            {"role": "user", "content": question}]
    return _extract_code(_strip_think(_ollama_chat(VERIFY_CODER, msgs,
                                                    num_predict=512)))


def _run_sandboxed(code: str, timeout: int = 8) -> str | None:
    """Run AST-validated pure-computation code IN-PROCESS with restricted
    builtins, capturing its stdout, on a daemon thread with a timeout.

    In-process (not subprocess) because in a PyInstaller-frozen app
    sys.executable is JarvisChat.exe, not Python — a subprocess would relaunch
    the GUI. Safety here rests on the AST whitelist (no imports / IO / dunders /
    dangerous builtins / while-loops) plus the restricted builtins, so the code
    can only do bounded pure computation. The timeout abandons a slow thread."""
    if not _ast_is_safe(code):
        return None
    import builtins as _b
    safe = {k: getattr(_b, k) for k in _SAFE_BUILTINS if hasattr(_b, k)}
    result: dict = {}

    def _target():
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<verify>", "exec"), {"__builtins__": safe})
            result["out"] = buf.getvalue().strip()
        except Exception:
            result["out"] = None

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():           # timed out — abandon the daemon thread
        return None
    return result.get("out") or None


def verify_answer(question: str) -> str | None:
    """Best-effort deterministic answer via generated + sandboxed code.
    Returns the computed answer string, or None if it couldn't verify."""
    try:
        code = _gen_verify_code(question)
        if not code:
            return None
        return _run_sandboxed(code)
    except Exception:
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

def run_council(query: str,
                history: list[dict] | None = None,
                status_cb=None,
                proposers: list[str] | None = None,
                aggregator: str | None = None,
                verify: bool = True) -> dict:
    """Run the full council. Returns:
        {answer, agreement, disagreed, proposals:[(model,text)], aggregator}
    `status_cb(msg)` is called with progress strings. Never raises — on total
    failure returns {answer: ""} so the caller can fall back to a single model.
    """
    history = history or []
    proposers = proposers or COUNCIL_PROPOSERS
    aggregator = aggregator or COUNCIL_AGGREGATOR

    def _status(m):
        if status_cb:
            try:
                status_cb(m)
            except Exception:
                pass

    try:
        from jarvis_router import embed_text as _embed_fn
    except Exception:
        _embed_fn = None

    proposals: list[tuple[str, str]] = []
    for i, model in enumerate(proposers):
        role_name, role_lens = COUNCIL_ROLES[i % len(COUNCIL_ROLES)]
        _status(f"council · advisor {i+1}/{len(proposers)} "
                f"({model}, {role_name})…")
        try:
            ans = _strip_think(_ollama_chat(
                model, _proposer_messages(query, history, role_lens)))
            if ans:
                proposals.append((model, ans))
        except Exception as e:
            _status(f"council · advisor {model} unavailable ({e})")

    if not proposals:
        return {"answer": "", "agreement": 1.0, "disagreed": False,
                "proposals": [], "aggregator": aggregator, "skipped_aggregator": False}

    if len(proposals) == 1:
        return {"answer": proposals[0][1], "agreement": 1.0, "disagreed": False,
                "proposals": proposals, "aggregator": proposals[0][0],
                "skipped_aggregator": True}

    # Claim-level agreement (precise — compares bottom-line answers, not topic).
    finals = [_extract_final(a) for _, a in proposals]
    agreement, groups = _claim_agreement(finals, _embed_fn)
    disagreed = agreement < 1.0

    # Adaptive fast path: if every advisor reached the SAME bottom line, there's
    # nothing for the aggregator to resolve — return the most complete proposal
    # from the unanimous group and skip the ~26s aggregation. (PROMETHEUS-style
    # "stop when confident".)
    if not disagreed:
        _status("council · unanimous — returning consensus (skipped aggregator)")
        best = max((proposals[i][1] for i in groups[0]), key=len)
        return {"answer": best, "agreement": agreement, "disagreed": False,
                "proposals": proposals, "aggregator": None,
                "skipped_aggregator": True}

    # If the dispute is over a NUMBER, don't just vote — compute it. Generate
    # + sandbox-run a snippet to deterministically settle the computable part.
    verified = None
    distinct_nums = {tuple(_NUM_RE.findall(_normalize_claim(f))) for f in finals}
    numeric_dispute = (sum(1 for f in finals if _NUM_RE.search(f)) >= 2
                       and len(distinct_nums) > 1)
    if verify and numeric_dispute:
        _status("council · advisors split on a number — verifying by execution…")
        verified = verify_answer(query)
        if verified:
            _status(f"council · verified result: {verified[:60]}")

    _status(f"council · advisors split (agreement {agreement:.2f}) — "
            f"aggregating ({aggregator})…")
    try:
        final = _strip_think(_ollama_chat(
            aggregator,
            _aggregator_messages(query, proposals, agreement, verified),
            num_predict=6144))
    except Exception as e:
        _status(f"council · aggregator failed ({e}); using plurality/verified")
        final = verified or max(
            (proposals[i][1] for i in max(groups, key=len)), key=len)

    return {"answer": final, "agreement": agreement, "disagreed": disagreed,
            "proposals": proposals, "aggregator": aggregator,
            "skipped_aggregator": False, "verified": verified}
