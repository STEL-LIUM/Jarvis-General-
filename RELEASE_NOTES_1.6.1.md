# JARVIS Chat 1.6.1

A refinement release on top of 1.6.0 — smarter 3D design, and **now on Linux too.**

## 🐧 Linux support (new)
- **`JARVIS-Chat-x86_64.AppImage`** — a single download-and-run file for Linux. No install: make it executable and launch. (Install Ollama via `curl -fsSL https://ollama.com/install.sh | sh` first.)
- Same app, one cross-platform codebase.

## 🧠 JARVIS now ships knowing 500+ designs
- The app comes **pre-seeded with 506 validated 3D designs** (every one actually rendered in Blender). From your very first "design a…" request, JARVIS retrieves his closest proven design as a starting template instead of building from scratch — so results are better immediately, not just after you've used it a while.

## 🛠 Smarter visual self-correction
- The "does it actually look right?" check is now **much more disciplined**: it only reshapes a model that's genuinely wrong (not merely imperfect), tries at most once, and — crucially — **only keeps the reshape if it's measurably better**. It can no longer "fix" a good model into a worse one.

## ✨ Also
- Design-memory capacity raised so the full design library fits.
- Dataset tooling: merge + seed utilities.

---
*Local-first as always. Your data — including the design memory — stays on your machine as plain files you can read or clear.*
