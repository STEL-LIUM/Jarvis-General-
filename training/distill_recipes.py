#!/usr/bin/env python3
"""
Distill a VERIFIED recipe library from the kept design dataset.

For each object type with enough working examples (gear, mug, vase, ...), feed a
few of its working scripts to the model and ask it to distill ONE clean,
parameterized helper function (e.g. `build_gear(teeth, radius)`). Then VERIFY the
helper actually runs in Blender (call it + export) before keeping it. Only
verified recipes are written.

Output: src/router_models/recipes.json  (list of
    {"type","keywords","recipe","note","from_examples"})
This ships with the app; jarvis_recipes.py injects the relevant one at design time.

    build/.venv/Scripts/python.exe training/distill_recipes.py --min 3 --max-types 40
"""
from __future__ import annotations
import os, sys, json, re, time, argparse, collections, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_design_dataset as G   # _gen_script, J, BLENDER

OUT = os.path.normpath(os.path.join(HERE, "..", "src", "router_models", "recipes.json"))
DATASETS = ["design_dataset.jsonl", "design_dataset_human.jsonl",
            "design_dataset_armor.jsonl"]


def _noun(prompt: str) -> str:
    p = prompt.lower()
    m = re.search(r"\b(?:a|an|the|stylized|low-poly|simple|detailed)\s+"
                  r"([a-z ]+?)(?:\s+(?:base mesh|for|in blender|using).*|$)", p)
    return (m.group(1).strip() if m else p.split()[-1]).strip()


def _load_by_type():
    by = collections.defaultdict(list)
    for fn in DATASETS:
        path = os.path.join(HERE, fn)
        if not os.path.isfile(path):
            continue
        for ln in open(path, encoding="utf-8", errors="ignore"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("script"):
                by[_noun(r.get("prompt", ""))].append(r["script"])
    return by


def _distill(noun: str, scripts: list) -> str:
    sysmsg = (
        "You are distilling REUSABLE Blender helper code. Given several working "
        "bpy scripts that build the same kind of object, extract ONE clean, "
        "PARAMETERIZED Python helper function that builds that object and returns "
        "its main bpy object. Use only the bpy/bmesh/math APIs the scripts use. "
        "Sensible default parameters. Output ONLY the function definition (no "
        "scene/camera/render/export code, no prose, no fences).")
    sample = "\n\n# ---- example ----\n".join(s[:1800] for s in scripts[:3])
    usr = (f"Object type: {noun}\n\nWorking scripts:\n{sample}\n\n"
           f"Write one reusable helper, e.g. def build_{re.sub(r'[^a-z]','_',noun.lower())}(...).")
    for m in (G.J.CODE_MODEL, G.J.DEEP_MODEL):
        try:
            out = G._gen_script("", m, sysmsg, user_extra=usr)
            if out and "def " in out:
                return out
        except Exception:
            continue
    return ""


def _verify(recipe: str, noun: str) -> bool:
    """Run the helper in Blender: define it, call it, export. True if a file lands."""
    fn = re.findall(r"def\s+(\w+)\s*\(", recipe)
    if not fn:
        return False
    run_dir = tempfile.mkdtemp(prefix="recipe_")
    test = (f"OUT = {run_dir!r}\nimport bpy, bmesh, math, mathutils\n"
            "from mathutils import Vector, Matrix, Euler, Quaternion\n"
            "bpy.ops.wm.read_factory_settings(use_empty=True)\n\n"
            + recipe + "\n\n"
            "import os\n"
            "try:\n"
            f"    obj = {fn[0]}()\n"
            "except TypeError:\n"
            "    obj = None\n"
            "bpy.ops.wm.stl_export(filepath=os.path.join(OUT,'r.stl'))\n")
    script = os.path.join(run_dir, "t.py")
    try:
        with open(script, "w", encoding="utf-8") as f:
            f.write(test)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run([G.BLENDER, "--background", "--python", script],
                           capture_output=True, text=True, timeout=120,
                           creationflags=flags)
    except Exception:
        return False
    return r.returncode == 0 and os.path.isfile(os.path.join(run_dir, "r.stl"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=3, help="min examples per type")
    ap.add_argument("--max-types", type=int, default=40)
    args = ap.parse_args()

    by = _load_by_type()
    cands = [(n, s) for n, s in by.items() if len(s) >= args.min]
    cands.sort(key=lambda x: len(x[1]), reverse=True)
    cands = cands[:args.max_types]
    print(f"Distilling recipes for {len(cands)} object types "
          f"(>= {args.min} examples each), verifying each in Blender…\n")

    recipes = []
    for i, (noun, scripts) in enumerate(cands, 1):
        t0 = time.time()
        recipe = _distill(noun, scripts)
        if not recipe:
            print(f"  [{i}/{len(cands)}] {noun}: no recipe produced")
            continue
        ok = _verify(recipe, noun)
        dt = time.time() - t0
        if ok:
            recipes.append({
                "type": noun,
                "keywords": list({w for w in re.findall(r"[a-z]+", noun.lower())}),
                "recipe": recipe,
                "note": f"distilled from {len(scripts)} working designs",
                "from_examples": len(scripts),
            })
            print(f"  [{i}/{len(cands)}] {noun}: VERIFIED ✓ ({dt:.0f}s)")
        else:
            print(f"  [{i}/{len(cands)}] {noun}: failed verify ({dt:.0f}s)")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(recipes, f, ensure_ascii=False, indent=1)
    print(f"\nDONE. {len(recipes)} verified recipes -> {OUT}")


if __name__ == "__main__":
    main()
