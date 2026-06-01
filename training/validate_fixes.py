#!/usr/bin/env python3
"""
Validate the DESIGN_SYSTEM_BPY fixes: take a sample of prompts that PREVIOUSLY
FAILED, regenerate them with the (now-fixed) system prompt, run in Blender, and
report the new pass-rate. Reuses the exact generator pipeline.

    build/.venv/Scripts/python.exe training/validate_fixes.py --n 12
"""
from __future__ import annotations
import os, sys, json, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_design_dataset as G   # _gen_script, _run_blender, _repair, J


def failed_prompts():
    seen, out = set(), []
    for fn in ("design_failures.jsonl", "design_failures_human.jsonl",
               "design_failures_armor.jsonl"):
        p = os.path.join(HERE, fn)
        if not os.path.isfile(p):
            continue
        for ln in open(p, encoding="utf-8", errors="ignore"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                pr = json.loads(ln).get("prompt", "")
            except Exception:
                continue
            if pr and pr not in seen:
                seen.add(pr)
                out.append(pr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    args = ap.parse_args()
    prompts = failed_prompts()
    # spread the sample across the list (start, middle, end) for variety
    if len(prompts) > args.n:
        step = len(prompts) / args.n
        prompts = [prompts[int(i * step)] for i in range(args.n)]
    print(f"Validating fixes on {len(prompts)} PREVIOUSLY-FAILED prompts "
          f"(model={G.J.DEEP_MODEL}, coder={G.J.CODE_MODEL})\n")

    passed = passed_repair = 0
    for i, p in enumerate(prompts, 1):
        t0 = time.time()
        try:
            code = G._gen_script(p, G.J.DEEP_MODEL, G.J.DESIGN_SYSTEM_BPY)
        except Exception as e:
            print(f"[{i}] GEN-ERR {p!r}: {e}")
            continue
        ok, err = G._run_blender(code)
        tag = ""
        if not ok:
            fixed = G._repair(code, err, p)
            if fixed:
                ok2, _ = G._run_blender(fixed)
                if ok2:
                    ok, tag = True, " (after 1 repair)"
                    passed_repair += 1
        if ok:
            passed += 1
        dt = time.time() - t0
        status = "PASS" + tag if ok else "FAIL"
        line = "" if ok else f" -> {next((l.strip() for l in reversed((err or '').splitlines()) if 'Error' in l), '')[:90]}"
        print(f"[{i:>2}] {status:<18} {p!r}  ({dt:.0f}s){line}")

    n = len(prompts)
    print(f"\n=== RESULT: {passed}/{n} now PASS "
          f"({100*passed/max(1,n):.0f}%) — of those, {passed_repair} needed 1 repair ===")
    print("(These all FAILED before the prompt fixes.)")


if __name__ == "__main__":
    main()
