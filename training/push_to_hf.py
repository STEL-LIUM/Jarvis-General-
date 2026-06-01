#!/usr/bin/env python3
"""
Push JARVIS's datasets to a Hugging Face Hub dataset repo.

Uploads two things into ONE dataset repo (as separate files / "configs"):
  • design/   -> merged working Blender scripts (objects + human/anatomy)
  • design/   -> the failures (kept separate so they're easy to filter)
  • feedback/ -> the live intent_log.jsonl (real usage, for retraining the
                 intent classifier — the "feedback dataset")

Re-runnable: run it again any time to push the latest version.

PREREQS (one-time, done by YOU):
  1. Make a WRITE token at https://huggingface.co/settings/tokens
  2. In the JARVIS prompt run:   ! hf auth login      (paste the token)

USAGE (I run this for you once you've logged in):
  build/.venv/Scripts/python.exe training/push_to_hf.py --repo USERNAME/jarvis-datasets
  # add --private to make the repo private,  --public to make it public (default private)
"""
from __future__ import annotations
import os, sys, argparse, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "JarvisChat")

# (local file, path-inside-repo, human label)
SOURCES = [
    (os.path.join(HERE, "design_dataset.jsonl"),        "design/objects.jsonl",  "objects (working scripts)"),
    (os.path.join(HERE, "design_dataset_human.jsonl"),  "design/human.jsonl",    "human/anatomy (working scripts)"),
    (os.path.join(HERE, "design_dataset_armor.jsonl"),  "design/armor.jsonl",    "armor (working scripts)"),
    (os.path.join(HERE, "design_failures.jsonl"),       "design/failures_objects.jsonl", "objects failures"),
    (os.path.join(HERE, "design_failures_human.jsonl"), "design/failures_human.jsonl",   "human failures"),
    (os.path.join(HERE, "design_failures_armor.jsonl"), "design/failures_armor.jsonl",   "armor failures"),
    (os.path.join(CONFIG_DIR, "intent_log.jsonl"),      "feedback/intent_log.jsonl",     "intent feedback log"),
]


def _count(path: str) -> int:
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for ln in f if ln.strip())


def _make_card(repo: str, present: list) -> str:
    rows = "\n".join(f"- `{repo_path}` — {label}: **{n}** rows"
                     for _, repo_path, label, n in present)
    return (
        "---\n"
        "license: mit\n"
        "tags:\n  - blender\n  - bpy\n  - code-generation\n  - jarvis\n"
        "---\n\n"
        "# JARVIS Datasets\n\n"
        "Auto-generated training data for the JARVIS Chat assistant.\n\n"
        "## Files\n\n" + rows + "\n\n"
        "### design/*\n"
        "Each line: `{\"prompt\": ..., \"script\": ..., \"repaired\": bool}` — a "
        "natural-language request mapped to a **Blender `bpy` script that actually "
        "ran headless and produced a model**. `failures_*` hold the discarded "
        "attempts with their Blender error, for analysis.\n\n"
        "### feedback/intent_log.jsonl\n"
        "Each line: `{\"text\": ..., \"pred\": ..., \"conf\": float, \"fired\": ..., "
        "\"uncertain\": bool}` — real intent-classifier decisions from app usage. "
        "Rows with `\"uncertain\": true` are the highest-value ones to relabel.\n"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="dataset repo id, e.g. yourname/jarvis-datasets")
    ap.add_argument("--private", action="store_true", help="make repo private (default)")
    ap.add_argument("--public", action="store_true", help="make repo public")
    args = ap.parse_args()
    private = not args.public  # default private unless --public given

    try:
        from huggingface_hub import HfApi
    except ImportError:
        sys.exit("huggingface_hub not installed in this Python.")

    api = HfApi()
    # verify we're logged in with a usable token
    try:
        who = api.whoami()
        print(f"Logged in as: {who.get('name', '?')}")
    except Exception:
        sys.exit("NOT LOGGED IN. Run  ! hf auth login  (with a WRITE token) first.")

    present = [(p, rp, lbl, _count(p)) for (p, rp, lbl) in SOURCES if _count(p) > 0]
    if not present:
        sys.exit("No data files found to upload yet.")

    print(f"\nWill upload to '{args.repo}' (private={private}):")
    for _, rp, lbl, n in present:
        print(f"  {rp:<34} {n:>5} rows   [{lbl}]")

    api.create_repo(repo_id=args.repo, repo_type="dataset",
                    private=private, exist_ok=True)
    print(f"\nRepo ready: https://huggingface.co/datasets/{args.repo}")

    # dataset card
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(_make_card(args.repo, present))
        card = tf.name
    api.upload_file(path_or_fileobj=card, path_in_repo="README.md",
                    repo_id=args.repo, repo_type="dataset")
    os.unlink(card)

    for path, repo_path, label, n in present:
        print(f"  uploading {repo_path} ...")
        api.upload_file(path_or_fileobj=path, path_in_repo=repo_path,
                        repo_id=args.repo, repo_type="dataset")
    print(f"\nDONE. View it at: https://huggingface.co/datasets/{args.repo}")


if __name__ == "__main__":
    main()
