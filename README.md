# JARVIS Chat

A translucent floating desktop chat panel for talking to a local AI assistant
in real time. Borderless, always-on-top, fades to see-through when you're not
using it — so a movie, stream, or app behind it stays visible right through it.
The moment you hover or click in, it snaps fully solid.

Runs **fully local** via [Ollama](https://ollama.com). No accounts, no API
keys, no cloud. Ask "what's on my screen?" and it'll actually look — using a
local vision model.

## Download

Grab the latest installer from the [Releases](../../releases) page:

- **`JarvisChat-Setup.exe`** — Windows installer (recommended).
  Sets up a Start Menu shortcut, registers an uninstaller, runs first-time
  setup on first launch (installs Ollama if missing, downloads models).

System requirements:
- Windows 10 / 11 (64-bit)
- ~20 GB free disk for the default model set
- 16 GB RAM minimum; 24 GB+ recommended for the deep model
- An NVIDIA GPU with 8 GB+ VRAM is strongly recommended (CPU-only works but is slow)

## What you get

- **Floating overlay** — borderless, draggable, resizable, minimizable. Snaps
  to the right / left / top edge of any monitor.
- **See-through when idle** — fades to ~45% opacity so anything behind it
  stays usable. Goes solid the instant you hover or click in.
- **Auto-routing** — short / casual messages go to a fast model; technical
  questions go to the deep reasoning model. You don't have to pick.
- **Screen vision** — ask *"what's on my screen?"*, *"what anime is this?"*,
  *"read this for me"*, etc. Hides the panel, captures **each monitor at
  full resolution**, asks the local vision model, brings the panel back.
- **Streaming replies** — tokens appear as they're generated.
- **Hidden reasoning** — DeepSeek's `<think>` blocks are stripped from the
  visible output so you just see the answer.
- **No telemetry. No internet calls** beyond what Ollama needs to pull models.

## First-run setup

When you launch JARVIS Chat for the first time, a small setup window appears:

1. **Check / install Ollama** — if `ollama.exe` isn't found, the setup
   downloads the official installer from ollama.com and runs it for you.
2. **Pick a model pack:**
   - **Lite** (~10 GB) — `qwen2.5:7b` + `llava:7b`. Works on most laptops.
   - **Standard** (~19 GB, *recommended*) — adds `deepseek-r1:14b` for deep reasoning.
   - **Heavy** (~30 GB) — uses `deepseek-r1:32b`. Needs a 24 GB+ VRAM card.
3. **Pull models** — progress bar shows download. Models stream from Ollama's CDN.

Setup only runs once. To re-run it manually, launch **"JARVIS Setup"** from
the Start Menu.

## Using it

- **Type** in the input box, press **Enter** to send (Shift+Enter = newline).
- **Drag the header** to move the panel anywhere on any monitor.
- **Drag the ◢ corner** to resize.
- **— button** minimizes to just the header strip.
- **✕ button** closes.

Slash commands inside the panel:

| Command  | What it does                |
|----------|-----------------------------|
| `/clear` | Wipe the conversation       |
| `/help`  | Show command help           |
| `/quit`  | Close the panel             |

## Configuration

All optional — set as environment variables before launching:

| Variable                  | Default                | What it does                       |
|---------------------------|------------------------|------------------------------------|
| `OLLAMA_URL`              | `http://localhost:11434/api/chat` | Ollama chat endpoint    |
| `OLLAMA_FAST_MODEL`       | `qwen2.5:7b`           | Routed for casual / short messages |
| `OLLAMA_MODEL`            | `deepseek-r1:14b`      | Routed for technical questions     |
| `OLLAMA_VISION_MODEL`     | `llava:7b`             | Used for screen captures           |
| `OLLAMA_NUM_CTX`          | `8192`                 | Context window                     |
| `OLLAMA_NUM_PREDICT`      | `6144`                 | Max tokens to generate             |
| `JARVIS_CHAT_EDGE`        | `right`                | `right`, `left`, or `top`          |
| `JARVIS_CHAT_WIDTH`       | `300`                  | Panel width (px)                   |
| `JARVIS_CHAT_IDLE_ALPHA`  | `0.45`                 | Idle opacity (0.15–1.0)            |

## Building from source

See [BUILDING.md](BUILDING.md) for how to compile the .exe and installer
yourself. The TL;DR is `build\build.ps1` from a developer PowerShell with
Python 3.11+ and [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed.

## License

 — see [LICENSE](LICENSE).

## Credits

- [Ollama](https://ollama.com) for local model serving
- [DeepSeek](https://deepseek.com), [Qwen](https://qwenlm.github.io),
  [LLaVA](https://llava-vl.github.io) for the underlying models
- Catppuccin Mocha for the color palette
