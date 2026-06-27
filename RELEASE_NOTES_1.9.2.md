# JarvisChat 1.9.2 — The Unified Chat Release

The biggest rethink since the Modding Studio (1.7.0). JARVIS is now **one chat
box** — a terminal-styled "Jarvis Chat" that auto-routes everything (chat · draw
· design · modding · agent · vision) instead of separate panels and modes. Plus a
full model upgrade to the **qwen3** lineup, a much smarter file-browsing agent,
and a faster, more reliable everyday experience.

> No release notes were published for 1.8.x / 1.9.0 / 1.9.1; this covers the
> unified-chat line through 1.9.2.

## What's new

### One unified chat (the new primary window)
- Brand-new `jarvis_chatview.py` — a terminal-styled chat that **streams, then
  folds** each turn (live steps collapse into "▸ worked on it · N steps"). It's
  the single primary interface; the old panel/Terminal/HUD remain as
  `/terminal` and `/hud` fallbacks.
- **Auto-routing** — just talk. "draw me a cat", "design a katana", "make a
  minecraft mod…", "make a folder in…", or attach an image — JARVIS picks the
  right subsystem and shows a route badge. No mode chips to manage.
- Clickable file/folder chips, follow-up suggestion chips, per-reply cost +
  tokens/sec footer, copy menu, slash autocomplete, save/load, drag-and-drop.

### Model upgrade — qwen3 ("big model, small VRAM")
- FAST `qwen3:8b` · DEEP `qwen3:30b-a3b` (MoE) · VISION `qwen3-vl:8b` · CODE
  `qwen3-coder:30b`. KV-cache quant (`OLLAMA_KV_CACHE_TYPE=q8_0`) + flash
  attention so the 30B MoE is usable on a 12 GB card.
- **Thinking-model audit** — qwen3 are thinking models; every short/structured/
  code-gen call now passes `think:false` so they don't burn the budget on
  `<think>` tokens.
- Single-resident **PIGGYBACK council** — every advisor role runs on one warm
  model, so the council never loads the multi-model zoo that froze small GPUs.

### Smarter PC agent (browse + edit, asks first)
- New `find` (recursive, case-insensitive, finds folders too) and `make_dir`
  tools. **Fast find**: broad-root searches probe your user profile first
  (8 s → ~0.5 s) with a short result cache.
- **make_dir self-heal** — small models mistype long paths; if the parent path
  doesn't exist, JARVIS locates the real folder by name (fuzzy-matched to what
  was typed) and creates inside it instead of building junk directory chains.
- **Conversation memory** — the agent now sees recent turns, so "that folder" /
  "this location" resolve to a path you mentioned a turn ago.
- Secret redaction (never reads `.env`/keys/`.ssh`/credentials), `/readonly`
  mode, `/undo` to revert the agent's last file change, edit-before-run confirm.

### Reliability & polish
- **Auto-recover Ollama** — if the tray app reverts off the `E:` model store,
  JARVIS restarts `ollama serve` on the right path automatically; zombie
  `llama-server` processes are cleaned up so closing JARVIS frees VRAM.
- **Visible council** (`/council on`) — technical questions convene a 3-advisor
  debate and show each advisor's take + the agreement score.
- **Auto crash analysis** — when a modding test client crashes, JARVIS reads the
  crash report and posts root-cause + fix automatically (no need to type
  `/crash`).
- `/selftest`, cross-session project memory, design-memory exemplars,
  file-search lag guards (skip system dirs, time/size budgets), disk guard, and
  auto-clean of old design outputs.

## Setup
- First-run setup now pulls the **qwen3** model packs (Lite / Standard / Heavy)
  matching the app defaults. Standard (`qwen3:8b` + `qwen3:30b-a3b` +
  `qwen3-vl:8b`) is recommended for 12 GB+ cards.

## Install
Run `JarvisChat-Setup.exe` (~204 MB). Everything runs locally via Ollama.
