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
- **Drag-and-drop files** — drop a text file (`.txt`, `.md`, `.py`, `.json`,
  source code, etc.) or an image (`.png`, `.jpg`, `.webp`) onto the panel,
  type your question, hit Enter. Images route to the local vision model;
  text files are folded into the prompt and answered by the deep model.
- **Streaming replies** — tokens appear as they're generated.
- **Hidden reasoning** — DeepSeek's `<think>` blocks are stripped from the
  visible output so you just see the answer.
- **Opt-in feedback** — on first launch you decide whether to share your text
  chats to help train future JARVIS versions. Off by default; nothing leaves
  your PC unless you click *Yes*. See [Privacy](#privacy) below.

## First-run setup

When you launch JARVIS Chat for the first time, a small setup window appears:

1. **Check / install Ollama** — if `ollama.exe` isn't found, the setup
   downloads the official installer from ollama.com and runs it for you.
2. **Pick a model pack:**
   - **Lite** (~10 GB) — `qwen2.5:7b` + `llava:7b`. Works on most laptops.
   - **Standard** (~19 GB, *recommended*) — adds `deepseek-r1:14b` for deep reasoning.
   - **Heavy** (~30 GB) — uses `deepseek-r1:32b`. Best on 24 GB+ VRAM; smaller
     cards work via CPU offload (slower).
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

| Command    | What it does                                              |
|------------|-----------------------------------------------------------|
| `/clear`   | Wipe the conversation                                     |
| `/help`    | Show command help (also shows current sharing status)     |
| `/quit`    | Close the panel                                           |
| `/attach <path>` | Attach a file by path (alternative to drag-and-drop) |
| `/detach`  | Drop the currently attached file                          |
| `/optin`   | Start sharing future text exchanges to the public dataset |
| `/optout`  | Stop sharing — nothing more leaves your PC                |
| `/privacy` | Show exactly what's shared, where, and your install ID    |
| `/update notes` | Show release notes for the pending update, if any    |
| `/updates on` / `off` | Toggle the auto-check on launch (on by default) |

## Privacy

On first launch JARVIS Chat asks one question: *"Help improve JARVIS by
sharing your chats?"* Your answer is saved and you're never asked again
(use `/optin` / `/optout` to change it).

**If you click No** (the default): no chat data leaves your PC. The app
talks only to your local Ollama. There is no analytics and no error
reporting. The only background call is a check of the GitHub Releases
API on each launch (see *Updates* below) — disable it with `/updates off`.

**If you click Yes** — for *text* exchanges only:

- Each user message + JARVIS's reply is sent to a small proxy on Hugging
  Face that appends it to a public dataset:
  [`STEL-LIUM/jarvis-feedback`](https://huggingface.co/datasets/STEL-LIUM/jarvis-feedback).
  Anyone can browse what's been contributed.
- Submissions include: model name (e.g. `qwen2.5:7b`), an anonymous random
  install ID (a UUID generated once per install — no name, no IP), a
  session ID (resets on `/clear` and on app restart), and a timestamp.
- **Screenshots are never uploaded** — even when you ask JARVIS to look
  at your screen. The vision model runs locally and its replies are
  excluded from sharing.
- Your install ID and consent decision are stored locally at
  `%LOCALAPPDATA%\JarvisChat\config.json`. Delete the file to reset.

Type `/privacy` inside the panel any time to see the exact endpoint, the
dataset URL, and your config file path.

## Updates

JARVIS Chat checks the GitHub Releases API silently on every launch. If
you're already current, nothing appears — no banner, no nag. If a newer
release exists, an install dialog opens automatically a moment after the
panel loads. Accepting downloads `JarvisChat-Setup.exe` and runs it
silently with `/CLOSEAPPLICATIONS /RESTARTAPPLICATIONS`, which closes the
chat, replaces the install, and reopens it. Your config file and chat
history are preserved across updates.

The check is independent from the feedback opt-in — it runs whether you
share chats or not. Disable it entirely with `/updates off`. When an
update is pending, `/update notes` shows the release notes.

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

**Proprietary — All Rights Reserved.**
Copyright © 2026 Aryan Guerrero.

This is **not** open-source software. The source code is published for
transparency and audit only.

**Binary (`JarvisChat-Setup.exe`)** — free to install and use for **personal,
non-commercial** purposes. You may **not** redistribute, modify, decompile,
reverse-engineer, or include it in a commercial product.

**Source code** — no rights to use, copy, modify, or redistribute are granted.
Reading it for audit and transparency is the only permitted use.

**Third-party components** — bundled dependencies (Python, Pillow, tkinterdnd2,
etc.) remain under their own original open-source licenses.

For commercial or source-code licensing, contact
**[guerreroaryan@gmail.com](mailto:guerreroaryan@gmail.com)**.

Full terms: [LICENSE](LICENSE).

## Credits

- [Ollama](https://ollama.com) for local model serving
- [DeepSeek](https://deepseek.com), [Qwen](https://qwenlm.github.io),
  [LLaVA](https://llava-vl.github.io) for the underlying models
- Catppuccin Mocha for the color palette
