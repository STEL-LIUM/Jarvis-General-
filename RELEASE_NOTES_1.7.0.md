# JarvisChat 1.7.0 — The Modding Studio Release

The biggest jump since 1.6.0. JARVIS can now plan, author, 3D-model, build, and
ship Minecraft Forge mods end-to-end — described in plain English from the
Terminal. Plus a built-in MCreator-style test client and the manga drawing
agent wired into chat.

## What's new

### The modding studio (`:modding ...` in Terminal)

JARVIS authors Minecraft Forge 1.20.1 mods (pinned to **Forge 47.4.10**) from
prose. Phases 1 through 7.2 live-tested.

- **`:modding describe <prose>`** — full pipeline: prose → council plan
  (items / menus / screens / HUD / skins) → multi-class Java scaffold →
  gradle build → jar. "A backpack with a 27-slot GUI" → working jar in 51s.
- **`:modding new <modid> <behavior>`** — LLM-authored item class with
  forge_rag (RAG over the actual Forge 1.20.1 sources jar — 5452 .java files
  indexed from `~/.gradle/caches/forge_gradle/`) to keep the generator from
  hallucinating signatures.
- **`:modding build <modid>`** — gradle build with errors-only output.
- **`:modding repair <modid>`** — RAG-guided crash → council patch loop.
  Live result on a LightningRod test: 22 compile errors → 1 (95% reduction).
- **`:modding play <modid>` / `:modding stop` / `:modding playing`** —
  MCreator-style live test client. Spawns Minecraft Forge dev environment,
  streams log to the Terminal, surfaces crash reports inline.
- **`:modding ship <modid>`** — pushes the built jar to CurseForge via the
  Upload API. Requires `CURSEFORGE_API_TOKEN` env + `.cf_project_id` per mod.
- **`:modding roblox <modid> <behavior>`** — Luau ServerScript for Roblox
  Tools. Companion mod uses an inverted polling architecture (Roblox can't
  accept inbound HTTP).

### Multi-component mod support (Phase 5)

ModSpec now coordinates items + menus + screens + HUD overlays + screen
skins across a side-split (server / client) main class. Live-tested on:

- `chest_in_a_bottle` — item that opens a 3x3 storage GUI (4 Java classes,
  10s build).
- `enchantedgenesisui` — HUD overlay ("ENCHANTED GENESIS" banner) plus
  screen skins (inventory + pause tints). CF project 1571593 wired.

### Blender for 3D item models (Phase 6)

Blender 5.1.2 generates Forge cube-element item-model JSON from canned
shapes (sword, wand). Headless render via `blender --background --python`.
1.3s author + 11s build for a 4-cube sword.

### Drawing agent integration

The manga line-art Drawing Agent (system Python 3.14 + torch/CUDA) is now
reachable from Terminal:

- **`:draw <target>`** — strokes the matching target (fuzzy filename match
  in `DrawingAgent/targets/`). 250 thin strokes in ~0.5s on a 3080 Ti.
- **`:draw list` / `:draw open`** — list targets, open output dir.
- **Natural-language draw catcher** — "draw me a samurai" / "sketch a fox"
  / "please draw a tree" are caught BEFORE the LLM agent and routed to the
  drawing pipeline. Tested against 20 positive + negative cases.

### Other

- All `:modding` and `:draw` commands run on a background thread; live
  output streams into the Terminal scrollback without blocking the UI.
- CLIENT_VERSION 1.6.9 → 1.7.0; installer.iss bumped to match.

## Files added in 1.7.0

```
src/jarvis_modding/             18 modules + Forge & Roblox templates
src/jarvis_drawing.py            subprocess wrapper for the Drawing Agent
DrawingAgent/infer.py            single-shot inference entry (used by jarvis_drawing)
RELEASE_NOTES_1.7.0.md           this file
```

## Setup notes

- The modding studio needs JDK 17 (Forge 1.20.1 requires it exactly), gradle's
  bootstrap (handled by the project's gradlew), and ~2-5 min for the first
  `:modding play` cold-launch as Forge downloads MC assets.
- `:modding ship` needs `setx CURSEFORGE_API_TOKEN "<your-token>"` once, then
  a `.cf_project_id` file in each mod's workdir.
- `:draw` needs Blender + Python 3.14 with torch (already on the dev machine).
