# JarvisChat 1.9.3 — Modding Studio Hardening + Smarter Agent

Builds on the unified-chat release (1.9.2) with a big pass on the **modding
studio** (mods that actually load, are findable in creative, and are playable in
survival), an **agent that acts instead of lecturing**, a rewritten agent system
prompt, and the auto-update / shutdown fixes.

## Modding studio
- **Creative tab (fixes a real 1.20.1 bug):** registered items now appear in the
  creative menu. Forge 1.20.1 hides modded items unless the mod registers a
  CreativeModeTab — JARVIS now auto-adds a registry-driven tab containing every
  item/block/tool the mod registers.
- **Survival recipes:** ore → gem smelting + blasting, and 9↔1 storage-block
  recipes, generated automatically (only when the item pairing is unambiguous).
- **Block tags:** modded blocks join `minecraft:mineable/pickaxe` so they break at
  the right speed with the right tool.
- **Pre-flight validation** before every build — catches missing textures/models,
  missing lang entries, and modid mismatches that `gradlew build` does NOT
  (the "I built it but the item is purple / invisible" class of bugs).
- **`:modding verify <modid>`** — fast asset check + `compileJava` only (no jar),
  surfacing Java errors in ~30s instead of a ~5-minute full build.
- **`:modding open <modid>`** — opens the project and points the agent's working
  root at it, so "add this file" lands inside the mod.
- **`:modding status`** — a board of every mod: built/not, class count, asset
  health.
- **Auto-repair:** `:modding describe` now runs the RAG-guided build→repair→rebuild
  loop on a build failure instead of just suggesting it.
- **Crash taxonomy:** crash analysis now leads with an instant, deterministic
  diagnosis (duplicate registration, missing texture/model, wrong Java, malformed
  mods.toml, hallucinated API, bad recipe JSON, …) with a targeted fix, before the
  detailed model read.

## Agent & chat
- **Acts instead of lecturing:** when you ask JARVIS to do something it has tools
  for ("add it to the files", "do it", "create the class"), it now performs the
  action via its agent instead of printing copy-paste instructions. It only walks
  you through steps when you explicitly ask ("how do I…", "explain…").
- **Rewritten agent prompt:** clearer understand → act → verify → report workflow,
  strict path discipline (find the real folder, never write to the drive root),
  and it always finishes with a summary of what changed.
- **Agent conversation memory:** "that folder" / "this location" resolve to a path
  you mentioned a turn earlier.
- **make_dir self-heal:** a mistyped parent path (`Desktop`→`Desktopt`) is
  auto-corrected to the real folder instead of creating a junk directory tree.
- **1.20.1-aware:** hand-edited mod code is steered to modern APIs, never the
  1.7.10/1.12 calls that don't compile.

## Reliability
- **Faster close:** the window closes instantly and evicts models in the
  background (no more multi-second "won't close" freeze), with a hard exit
  safety net.
- **Auto-update fixed:** the update prompt no longer crashes under the new chat
  window, and update notices + a version banner show in the chat. `/version` and
  `/update` commands added.

## Install
Run `JarvisChat-Setup.exe` (~204 MB). Everything runs locally via Ollama; the
first-run setup pulls the qwen3 model packs.
