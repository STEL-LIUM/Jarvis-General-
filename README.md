# JarvisChat - Consolidated Installation

**All JarvisChat files in one location on E: drive**

## Quick Start

Double-click to launch:
```
E:\AI\JarvisChat\JarvisChat.bat
```

Or from command line:
```bash
cd E:\AI\JarvisChat
python jarvis_launcher.py
```

## Directory Structure

```
E:\AI\JarvisChat\
├── JarvisChat.bat                    ← Main launcher
├── jarvis_launcher.py                ← Python launcher (auto-starts server)
├── jarvis_chat.py                    ← Main chat interface
├── jarvis_llamacpp_server.py         ← llama.cpp shim (Ollama API)
├── jarvis_brain.py                   ← Multi-model routing
├── jarvis_agent.py                   ← Agentic mode
├── jarvis_setup.py                   ← First-run setup wizard
├── jarvis_qt.py                      ← Qt HUD interface
├── jarvis_webui.py                   ← WebView HUD fallback
├── (50+ other modules)               ← Full JarvisChat functionality
└── llama.cpp\
    ├── llama-server.exe              ← Inference engine
    └── *.dll                         ← CUDA and other backends

E:\AI\llama-models\                   ← GGUF model files (separate)
├── ornith-1.0-35b-q4_k_m.gguf
├── qwen3-14b-q4_k_m.gguf
└── VibeThinker-3B.Q8_0.gguf
```

## What Happens on Launch

1. `JarvisChat.bat` runs `jarvis_launcher.py`
2. Launcher checks if llama.cpp shim is running
3. If not, starts `jarvis_llamacpp_server.py` (background, no window)
4. Shim launches `llama.cpp\llama-server.exe`
5. JarvisChat Qt HUD opens
6. Ready to chat!

## Features

- **Multi-model routing**: Automatically picks the right model for each task
- **Agentic mode**: Can read/write files, run commands
- **Council mode**: 3-role debate for complex decisions
- **Design tools**: Generate Blender 3D designs
- **Drawing**: Create original artwork
- **Memory**: Persistent context across sessions
- **Voice**: TTS and STT support
- **Modding**: Minecraft mod development tools

## Server Details

- **Shim API**: `http://localhost:11434` (Ollama-compatible)
- **Internal**: llama-server on port 8077
- **Models**: `E:\AI\llama-models\`
- **Auto-start**: Yes, handled by launcher

## Models

All models in `E:\AI\llama-models\`:

| Model | Size | Purpose |
|-------|------|---------|
| ornith-1.0-35b | 19.92 GB | Deep reasoning, coding, agent (MoE) |
| qwen3-14b | 8.38 GB | Fast narrator, general chat |
| VibeThinker-3B | 3.06 GB | Math verification, STEM |

## Configuration

Set these environment variables to customize (optional):

```powershell
# Change llama-server location
$env:JARVIS_LLAMA_SERVER = "E:\AI\JarvisChat\llama.cpp\llama-server.exe"

# Change model directory
$env:JARVIS_MODELS_DIR = "E:\AI\llama-models"

# Change ports
$env:JARVIS_LISTEN_PORT = "11434"
$env:JARVIS_INTERNAL_PORT = "8077"
```

## Troubleshooting

**"Missing webview" error:**
- Install PySide6: `pip install PySide6`
- Or fallback to PyWebView: `pip install pywebview`

**"Server not running":**
- Check Python: `python --version`
- Verify shim exists: `E:\AI\JarvisChat\jarvis_llamacpp_server.py`
- Verify binary exists: `E:\AI\JarvisChat\llama.cpp\llama-server.exe`

**"Model not found":**
- Run setup wizard: `python jarvis_launcher.py --setup`
- Verify models in `E:\AI\llama-models\`

## Old Locations (Can Delete)

After confirming JarvisChat works from E: drive:

- `C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\Jarvis code\` ← OLD
- `C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\Jarvis Agent\` ← OLD  
- `C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\JarvisAI\` ← OLD (parent folder)
- `C:\llama.cpp\` ← OLD

**Test first before deleting!**

## Interfaces

JarvisChat supports multiple interfaces:

- **Qt HUD** (default): `python jarvis_launcher.py` or `python jarvis_launcher.py --hud`
- **WebView HUD** (fallback): Automatic if Qt not available
- **Tkinter Chat** (legacy): `python jarvis_launcher.py --chat`
- **Console**: `python jarvis_console.py`
- **Finance UI**: `python jarvis_finance_ui.py`

## First Run

On first launch, the setup wizard will:
1. Check for llama.cpp binaries
2. Scan for existing models
3. Offer to download missing models
4. Configure preferences

Just follow the prompts!

---

**JarvisChat v1.11.0 · STELLIUM LLC · July 2026**

All files consolidated on E: drive for easy management.
