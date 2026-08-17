# Triton

**An AI agent that does the work, not just the talking — running entirely
on your own machine.**

Give Triton a goal and it plans, opens your files, writes and fixes code,
runs the tools, checks its own results, and keeps going until the job is
done. Your models, your GPUs, your data. No accounts, no API keys, no
per-token bill, and nothing leaves the building unless you ask it to.

Made by [Stellium](https://github.com/STEL-LIUM).

---

## Download

Get the installer from the [Releases](../../releases) page.

| Platform | File | Notes |
|---|---|---|
| Windows 10/11 (64-bit) | `TritonChat-Setup.exe` | Installs, creates shortcuts, runs first-time setup |
| Linux (x86-64) | `Triton-Chat-*-x86_64.AppImage` | `chmod +x` and run — no install needed |

Linux builds are tagged separately with an **L** suffix (for example
`v1.14.9L`), so the Windows release stays the one the in-app updater
follows.

### What you need

- **16 GB RAM** minimum, 24 GB+ for the larger reasoning models
- **An NVIDIA GPU with 8 GB+ VRAM** strongly recommended — CPU-only runs,
  but slowly
- **~20 GB free disk** for the default model set
- Optional: [Blender](https://www.blender.org) 4.x/5.x for 3D and CAD
  output, and a microphone for voice

First launch runs a setup wizard that fetches the models and starts the
local engine. After that it works offline.

---

## What it does

**Agent work.** Point it at a folder and say what you want changed. It
reads the real files, makes the edits, runs the tests, and verifies the
result before calling it done. It reads and writes PDFs, Word documents,
Excel and LibreOffice spreadsheets, archives and SQLite databases — with
no extra software to install.

**Runs simulations.** Ask for one in plain English — "simulate the force
at 3 kV", "plot pressure against voltage" — and it writes the model, runs
it, repairs it if it crashes, and shows you the result. Structural and
thermal FEA run on CalculiX.

**Designs in 3D and CAD.** Describe a part and it writes the Blender or
FreeCAD script, runs it headless, **looks at its own render**, and
reshapes the model if it doesn't match what you asked. Exports STL ready
for printing.

**Sees your screen.** Ask what's on screen, what an error message says, or
what's in an image — it captures each monitor at full resolution and reads
it with a local vision model.

**Makes images.** Local image generation, sketching, and manga colouring.

**Talks.** Wake-word voice in and spoken replies, fully local.

**Remembers.** Triton keeps continuity across sessions — what you're
building, decisions already made, how your machine is set up — so it stops
rediscovering the same things.

**Reachable from anywhere.** Optional online mode publishes your own Triton
over an encrypted tunnel, so your phone or laptop can talk to the machine
at home. Off by default.

---

## Is it really local?

Yes. Inference runs on a custom
[llama.cpp](https://github.com/ggerganov/llama.cpp) build on your own
hardware. The only network calls Triton makes on its own are the update
check against this repository, and web search when you explicitly ask for
it. Online mode is opt-in and points at your own machine.

---

## Updating

Triton checks this repository on launch and offers the update in-app — the
download, install and relaunch are handled for you.

---

## About this repository

This repo hosts **releases, documentation and issues**. Triton's source is
not published here.

Found a bug or want a feature? Open an [issue](../../issues).

## Licence

See [LICENSE](LICENSE).
