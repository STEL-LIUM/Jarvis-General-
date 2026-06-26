# JARVIS Chat 1.7.0

## 🌐 Live web search

JARVIS can now look things up in real time — no API key, no cloud account, just DuckDuckGo.

- **Automatic** — say "latest news on X", "what happened with Y", "is Z still running?", ask about anything with a date/recency signal and JARVIS fetches live results before answering.
- **Explicit** — `/search <query>` forces a web search regardless of intent.
- Results are cited inline [1] [2] so you know exactly where the answer came from.
- Fully local routing: the search fetch and LLM synthesis run on your PC. Nothing new leaves your machine beyond the DDG query.

## 🧠 80B model support

The auto-detection at startup now picks the heaviest model you actually have installed — including the 80B class:

| Priority | Models checked |
|---|---|
| 80B class | `qwen3:72b` · `qwen2.5:72b` · `llama3.3:70b` |
| Heavy reasoning | `deepseek-r1:70b` · `deepseek-r1:32b` |
| Standard (default) | `deepseek-r1:14b` · `qwen3:14b` |
| Lite fallback | `deepseek-r1:7b` |

The fast (casual) model now also auto-detects: `qwen3:8b` → `qwen2.5:14b` → `qwen2.5:7b`.

Vision model fallbacks updated to include `qwen2.5vl:72b` and `llama3.2-vision:11b`.

## 📱 JARVIS Mobile (new in this release)

A companion iOS PWA so you can talk to your PC's JARVIS from your phone.

- Installs from Safari — Share → Add to Home Screen. No App Store, completely free.
- Same streaming chat, dark JARVIS theme, image attachment for vision.
- Remote access via Tailscale (no port forwarding required).
- Start the server: `python src/jarvis_server.py`

---
*Local-first as always — web search queries go to DuckDuckGo (a privacy-respecting search engine). The results and JARVIS's answer stay on your machine.*
