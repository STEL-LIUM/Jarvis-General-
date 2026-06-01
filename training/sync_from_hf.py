#!/usr/bin/env python3
"""
Mirror the Hugging Face dataset DOWN into the local 'Jarvis Datasets' folder.

This is the reverse of push_to_hf.py:
  push_to_hf.py   local generated data  ->  Hugging Face
  sync_from_hf.py Hugging Face          ->  C:\\Jarvis Datasets   (this script)

Run:
    build/.venv/Scripts/python.exe training/sync_from_hf.py
    # optional: --dest "D:\\some\\other\\folder"
"""
from __future__ import annotations
import os, shutil, argparse
from huggingface_hub import hf_hub_download, list_repo_files

REPO = "STEL-LIUM/jarvis-datasets"
DEFAULT_DEST = r"C:\Jarvis Datasets"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=DEFAULT_DEST)
    args = ap.parse_args()
    os.makedirs(args.dest, exist_ok=True)
    files = [f for f in list_repo_files(REPO, repo_type="dataset")
             if not f.startswith(".")]
    print(f"Mirroring {REPO} -> {args.dest}")
    for f in files:
        src = hf_hub_download(repo_id=REPO, repo_type="dataset", filename=f)
        out = os.path.join(args.dest, *f.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(src, out)
        print(f"  {f}  ({os.path.getsize(out)} bytes)")
    print("DONE.")


if __name__ == "__main__":
    main()
