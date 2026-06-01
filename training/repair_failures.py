#!/usr/bin/env python3
"""
Repair pass: go back through every DISCARDED design and actually fix it.

The generator only gives a failed prompt ONE repair attempt before discarding.
This script gives each failure real effort: regenerate fresh from the (fixed)
DESIGN_SYSTEM_BPY, and if it errors, run up to MAX_REPAIRS rounds of the same
error->fix->rerun loop the app uses — feeding Blender's actual error back each
round (most of these errors are self-describing, e.g. "run ensure_lookup_table()").

When a prompt finally renders, it moves OUT of the failures file and INTO the
kept dataset. Anything still broken after MAX_REPAIRS stays in the failures file.

Per set (objects/human/armor):
    kept       = design_dataset[_set].jsonl
    failures   = design_failures[_set].jsonl   (rewritten with only still-broken)

    build/.venv/Scripts/python.exe training/repair_failures.py
    # options: --set objects|human|armor|all (default all), --max-repairs 4
"""
from __future__ import annotations
import os, sys, json, time, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_design_dataset as G   # _gen_script, _run_blender, _repair, J

SETS = {
    "objects": ("design_dataset.jsonl",       "design_failures.jsonl"),
    "human":   ("design_dataset_human.jsonl",  "design_failures_human.jsonl"),
    "armor":   ("design_dataset_armor.jsonl",  "design_failures_armor.jsonl"),
}


def _load_jsonl(path):
    rows = []
    if os.path.isfile(path):
        for ln in open(path, encoding="utf-8", errors="ignore"):
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    pass
    return rows


def _kept_prompts(path):
    return {r.get("prompt", "") for r in _load_jsonl(path)}


def repair_set(name, kept_file, fail_file, max_repairs):
    kept_path = os.path.join(HERE, kept_file)
    fail_path = os.path.join(HERE, fail_file)
    failures = _load_jsonl(fail_path)
    already_kept = _kept_prompts(kept_path)
    if not failures:
        print(f"[{name}] no failures to repair.")
        return 0, 0

    # de-dup failed prompts (a prompt can appear multiple times)
    seen, todo = set(), []
    for r in failures:
        p = r.get("prompt", "")
        if p and p not in seen and p not in already_kept:
            seen.add(p)
            todo.append(p)

    print(f"[{name}] repairing {len(todo)} unique failed prompts "
          f"(max {max_repairs} repair rounds each)…")
    recovered, still_broken = [], []

    for i, p in enumerate(todo, 1):
        t0 = time.time()
        try:
            code = G._gen_script(p, G.J.DEEP_MODEL, G.J.DESIGN_SYSTEM_BPY)
        except Exception as e:
            still_broken.append({"prompt": p, "error": f"gen: {e}",
                                 "repair_attempted": False, "script": ""})
            print(f"  [{i}/{len(todo)}] GEN-ERR {p!r}: {e}")
            continue

        ok, err = G._run_blender(code)
        rounds = 0
        while not ok and rounds < max_repairs:
            rounds += 1
            fixed = G._repair(code, err, p)
            if not fixed or fixed.strip() == code.strip():
                break
            code = fixed
            ok, err = G._run_blender(code)

        dt = time.time() - t0
        if ok:
            with open(kept_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"prompt": p, "script": code,
                                    "repaired": rounds > 0},
                                   ensure_ascii=False) + "\n")
            recovered.append(p)
            tag = f" (after {rounds} repair{'s' if rounds != 1 else ''})" if rounds else ""
            print(f"  [{i}/{len(todo)}] FIXED{tag}  {p!r}  ({dt:.0f}s)")
        else:
            errline = next((l.strip() for l in reversed((err or "").splitlines())
                            if "Error" in l), "")[:90]
            still_broken.append({"prompt": p, "error": (err or "")[-800:],
                                 "repair_attempted": True, "script": code})
            print(f"  [{i}/{len(todo)}] STILL BROKEN  {p!r}  ({dt:.0f}s) -> {errline}")

    # rewrite the failures file with ONLY the still-broken ones
    with open(fail_path, "w", encoding="utf-8") as f:
        for r in still_broken:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n = len(todo)
    print(f"[{name}] recovered {len(recovered)}/{n} "
          f"({100*len(recovered)/max(1,n):.0f}%); {len(still_broken)} still broken.")
    return len(recovered), len(still_broken)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="which", default="all",
                    choices=list(SETS) + ["all"])
    ap.add_argument("--max-repairs", type=int, default=4)
    args = ap.parse_args()
    names = list(SETS) if args.which == "all" else [args.which]
    print(f"Repair pass over {names}  (model={G.J.DEEP_MODEL}, "
          f"coder={G.J.CODE_MODEL}, Blender={os.path.isfile(G.BLENDER)})\n")
    tot_fix = tot_broken = 0
    for nm in names:
        kept_file, fail_file = SETS[nm]
        r, b = repair_set(nm, kept_file, fail_file, args.max_repairs)
        tot_fix += r
        tot_broken += b
        print()
    print(f"=== REPAIR PASS DONE: recovered {tot_fix}, still broken {tot_broken} ===")


if __name__ == "__main__":
    main()
