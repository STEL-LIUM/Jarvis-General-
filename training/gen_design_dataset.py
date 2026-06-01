#!/usr/bin/env python3
"""
Build a fine-tuning dataset for JARVIS's Blender design pipeline.

For each prompt: deepseek-r1 writes a bpy script (using the app's REAL
DESIGN_SYSTEM_BPY) -> we run it headless in Blender -> if it produces output
files, we KEEP the (prompt -> working script) pair. Failed scripts get ONE
auto-repair attempt (qwen2.5-coder, same as the shipped app); still-failing ones
are discarded. So every example in the dataset is a script that ACTUALLY RAN —
no guessed/broken training data.

Output: training/design_dataset.jsonl  (one JSON per line:
    {"prompt": ..., "script": ..., "repaired": bool})
Plus a running tally to training/design_dataset_stats.json.

Run (from JarvisChat-Release, with the build venv python + Blender + Ollama up):
    build/.venv/Scripts/python.exe training/gen_design_dataset.py --n 500
    # resumable: re-run and it skips prompts already in the jsonl.
"""
from __future__ import annotations
import os, sys, json, time, argparse, subprocess, tempfile, re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "src"))
sys.path.insert(0, SRC)
os.environ.setdefault("JARVIS_CHAT_CONFIG_DIR",
                      os.path.join(tempfile.gettempdir(), "jarvis_ds_gen"))

import jarvis_chat as J   # reuse the app's exact prompt + models + URLs
import urllib.request

OUT_JSONL = os.path.join(HERE, "design_dataset.jsonl")
FAIL_JSONL = os.path.join(HERE, "design_failures.jsonl")   # discarded: review later
STATS = os.path.join(HERE, "design_dataset_stats.json")
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"

# Prompt bank — varied objects across the construction recipes the system prompt
# knows (lathed, hollow, arrayed, mirrored, composite, extruded). Expanded by
# combining a noun list with phrasings so 500 are diverse, not repetitive.
NOUNS = [
    "vase", "coffee mug", "wine glass", "bowl", "teapot", "bottle", "pitcher",
    "gear", "bracket", "phone stand", "phone case", "desk organizer", "pen holder",
    "lamp shade", "table lamp", "candle holder", "picture frame", "bookend",
    "chess pawn", "chess knight", "chess rook", "dice", "domino", "keycap",
    "hexagonal nut", "bolt", "washer", "spring", "hinge", "hook", "clip",
    "spiral staircase", "ladder", "fence panel", "brick wall", "archway",
    "simple chair", "stool", "bench", "table", "shelf", "cabinet",
    "rocket", "spaceship hull", "drone frame", "propeller", "wheel", "axle",
    "robot arm segment", "gripper claw", "servo bracket",
    "ring", "pendant", "bracelet cuff", "comb", "fork", "spoon",
    "funnel", "cup holder", "planter pot", "watering can", "bird feeder",
    "gear assembly", "pulley", "cam", "crankshaft", "piston",
    "pyramid", "obelisk", "column", "pillar", "torus knot", "mobius strip",
    "honeycomb panel", "lattice cube", "voronoi sphere", "gyroid block",
    "house", "castle tower", "windmill", "lighthouse", "bridge segment",
    "mushroom", "tree", "cactus", "flower", "leaf", "shell", "egg cup",
    "speaker grille", "vent cover", "knob", "dial", "button", "switch plate",
    "wrench", "screwdriver handle", "hammer head", "mallet", "chisel handle",
    "boat hull", "paddle", "anchor", "buoy", "fish",
]
TEMPLATES = [
    "design a {}", "make a 3d model of a {}", "create a {} in blender",
    "build me a {}", "model a {}", "generate a 3d printable {}",
    "design a simple {}", "make a detailed {}", "create a stylized {}",
]

# --- Human / character anatomy set (--set human) ----------------------------
# Stylized base-mesh parts that mirror+skin+subdiv CAN actually produce. Phrased
# to push the model toward doable techniques (base mesh / low-poly / symmetric /
# skin modifier) rather than impossible photoreal sculpts.
HUMAN_NOUNS = [
    "hand", "left hand", "open hand", "fist", "finger", "thumb", "knuckle",
    "foot", "bare foot", "toe", "heel", "ankle joint",
    "arm", "forearm", "upper arm", "elbow joint", "wrist",
    "leg", "lower leg", "thigh", "knee joint", "calf",
    "head", "face", "skull", "ear", "nose", "jaw", "neck",
    "torso", "chest", "ribcage", "spine", "pelvis", "hip bone", "shoulder",
    "clavicle", "vertebra", "tooth",
    "humanoid figure", "human base mesh", "mannequin", "posed figure",
    "stick-figure skeleton", "robot hand", "robot foot", "prosthetic hand",
    "muscle arm", "hand skeleton", "foot skeleton",
]
HUMAN_TEMPLATES = [
    "design a stylized {} base mesh",
    "model a low-poly {}",
    "create a simple {} 3d model",
    "build a symmetrical {} base mesh",
    "model a {} using the skin modifier",
    "make a {} for 3d printing",
]

# --- Armor set (--set armor) ------------------------------------------------
# Mostly hard-surface: cube/cylinder/sphere shells + BOOLEAN cuts + MIRROR
# (symmetric pieces) + BEVEL + SOLIDIFY for plate thickness. Good keep-rate fit.
ARMOR_NOUNS = [
    "knight helmet", "great helm", "barbute helmet", "sallet helmet",
    "spartan helmet", "viking helmet", "samurai helmet", "morion helmet",
    "visor", "helmet crest", "gorget", "breastplate", "cuirass", "chestplate",
    "backplate", "pauldron", "shoulder armor", "spaulder", "rerebrace",
    "vambrace", "bracer", "gauntlet", "armored glove", "couter", "elbow guard",
    "faulds", "tasset", "hip armor", "cuisse", "thigh armor", "poleyn",
    "knee guard", "greave", "shin guard", "sabaton", "armored boot",
    "kite shield", "round shield", "heater shield", "buckler", "tower shield",
    "war shield", "chainmail section", "plate armor segment", "armor trim",
    "sci-fi chest armor", "sci-fi helmet", "power armor pauldron",
    "fantasy helmet", "dragon helmet", "horned helmet", "battle mask",
]
ARMOR_TEMPLATES = [
    "design a {}",
    "model a {} for a knight",
    "create a stylized {}",
    "make a low-poly {}",
    "design a fantasy {}",
    "model a {} for 3d printing",
]

SETS = {
    "objects": (NOUNS, TEMPLATES),
    "human":   (HUMAN_NOUNS, HUMAN_TEMPLATES),
    "armor":   (ARMOR_NOUNS, ARMOR_TEMPLATES),
}


def all_prompts(n: int, nouns=NOUNS, templates=TEMPLATES):
    out, i = [], 0
    for t in templates:
        for noun in nouns:
            out.append(t.format(noun))
            i += 1
            if i >= n:
                return out
    return out[:n]


def _gen_script(prompt: str, model: str, system: str, user_extra: str = "") -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt + user_extra},
        ],
        "stream": False, "keep_alive": "10m",
        "options": {"num_ctx": J.NUM_CTX, "num_predict": 3000, "temperature": 0.3},
    }).encode()
    req = urllib.request.Request(J.CHAT_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as resp:
        obj = json.loads(resp.read().decode("utf-8", "ignore"))
    raw = (obj.get("message", {}) or {}).get("content", "") or ""
    code = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    code = re.sub(r"^\s*```(?:python|py)?\s*\n", "", code)
    code = re.sub(r"\n?```\s*$", "", code).strip()
    return code


def _run_blender(code: str) -> tuple[bool, str]:
    """Run a script headless; same header injection as the app. Returns
    (ok, error_tail). ok = exited 0 AND produced an output file."""
    run_dir = tempfile.mkdtemp(prefix="ds_run_")
    script = os.path.join(run_dir, "s.py")
    header = (f"OUT = {run_dir!r}\nimport os, sys, math, random\n"
              "try:\n import bmesh\n import mathutils\n from mathutils import Vector, Matrix, Euler, Quaternion\n"
              "except Exception: pass\n\n")
    with open(script, "w", encoding="utf-8") as f:
        f.write(header + code + "\n")
    try:
        r = subprocess.run([BLENDER, "--background", "--python", script],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        return False, f"launch/timeout: {e}"
    outs = [x for x in os.listdir(run_dir)
            if x.endswith((".stl", ".blend", ".png", ".obj"))]
    combined = ((r.stderr or "") + (r.stdout or "")).strip()
    ok = (r.returncode == 0 and len(outs) > 0)
    return ok, combined[-1200:]


def _repair(code: str, error: str, prompt: str) -> str:
    sysmsg = (J.DESIGN_SYSTEM_BPY + "\n\nREPAIR the failed script. Output ONLY the "
              "corrected script. Change ONLY what the error requires; keep the rest "
              "identical.")
    usr = (f"Request: {prompt}\n\nScript:\n{code}\n\nError:\n{error}\n\nFixed script:")
    for m in (J.CODE_MODEL, J.FAST_MODEL):
        try:
            fixed = _gen_script("", m, sysmsg, user_extra=usr)
            if fixed:
                return fixed
        except Exception:
            continue
    return ""


def _done_prompts(out_jsonl: str = OUT_JSONL) -> set:
    s = set()
    if os.path.isfile(out_jsonl):
        with open(out_jsonl, encoding="utf-8") as f:
            for line in f:
                try:
                    s.add(json.loads(line)["prompt"])
                except Exception:
                    pass
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--set", dest="dset", choices=list(SETS), default="objects",
                    help="which prompt bank: 'objects' (default) or 'human'")
    args = ap.parse_args()

    nouns, templates = SETS[args.dset]
    # 'human' writes to its OWN files so it can run alongside the objects job
    # without racing on the same jsonl; merge before fine-tune.
    if args.dset == "objects":
        out_jsonl, fail_jsonl, stats = OUT_JSONL, FAIL_JSONL, STATS
    else:
        out_jsonl = os.path.join(HERE, f"design_dataset_{args.dset}.jsonl")
        fail_jsonl = os.path.join(HERE, f"design_failures_{args.dset}.jsonl")
        stats = os.path.join(HERE, f"design_dataset_stats_{args.dset}.json")

    prompts = all_prompts(args.n, nouns, templates)
    done = _done_prompts(out_jsonl)
    kept = sum(1 for _ in done)  # already-saved count
    attempted = 0
    print(f"[set={args.dset}] Target {len(prompts)} prompts; {len(done)} already "
          f"done. Blender={os.path.isfile(BLENDER)} model={J.DEEP_MODEL} "
          f"coder={J.CODE_MODEL} -> {os.path.basename(out_jsonl)}")
    for i, p in enumerate(prompts, 1):
        if p in done:
            continue
        attempted += 1
        t0 = time.time()
        try:
            code = _gen_script(p, J.DEEP_MODEL, J.DESIGN_SYSTEM_BPY)
        except Exception as e:
            print(f"[{i}] GEN FAIL {p!r}: {e}")
            continue
        if not code:
            print(f"[{i}] empty script {p!r}")
            continue
        ok, err = _run_blender(code)
        repaired = False
        repair_tried = False
        if not ok:
            repair_tried = True
            fixed = _repair(code, err, p)
            if fixed:
                ok2, err2 = _run_blender(fixed)
                if ok2:
                    code, ok, repaired = fixed, True, True
                else:
                    code, err = fixed, err2   # log the repaired script + its error
        if ok:
            with open(out_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps({"prompt": p, "script": code,
                                    "repaired": repaired},
                                   ensure_ascii=False) + "\n")
            kept += 1
            tag = " (repaired)" if repaired else ""
            print(f"[{i}] KEEP{tag}  {p!r}  ({time.time()-t0:.0f}s, kept={kept})")
        else:
            # Record WHY it failed so we can fix the system prompt later:
            # the prompt, the last error (post-repair if we tried), the discarded
            # script, and whether a repair was even attempted.
            with open(fail_jsonl, "a", encoding="utf-8") as ff:
                ff.write(json.dumps({
                    "prompt": p,
                    "error": (err or "")[-800:],
                    "repair_attempted": repair_tried,
                    "script": code,
                }, ensure_ascii=False) + "\n")
            # one-line error summary in the console too
            errline = next((ln.strip() for ln in reversed((err or "").splitlines())
                            if "Error" in ln or "error" in ln), "no clear error")
            print(f"[{i}] DISCARD  {p!r}  ({time.time()-t0:.0f}s)  -> {errline[:120]}")
        if attempted % 10 == 0:
            with open(stats, "w", encoding="utf-8") as f:
                json.dump({"attempted": attempted, "kept": kept,
                           "keep_rate": round(kept / max(1, attempted), 3)}, f)
    print(f"\nDONE. kept {kept} working scripts -> {out_jsonl}")


if __name__ == "__main__":
    main()
