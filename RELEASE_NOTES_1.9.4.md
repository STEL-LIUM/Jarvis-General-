# JARVIS Chat 1.9.4

## Model lineup — qwen3 across the board

All default models updated to the qwen3 family:

| Role | Model | Notes |
|---|---|---|
| Deep (default) | `qwen3:30b-a3b` | MoE — 30B params, ~3B active; fast on 12 GB VRAM |
| Fast / casual | `qwen3:8b` | Snappy for quick replies and routing |
| Vision | `qwen3-vl:8b` | Native multimodal, replaces qwen2.5vl:7b |
| Code / repair | `qwen3-coder:30b` | Design script repair and code tasks |

The autodetect ladder at startup still picks the heaviest model you have installed. The 80B-class tier (`qwen3:72b`, `qwen2.5:72b`, `llama3.3:70b`) sits above qwen3:30b-a3b so power users with larger VRAM get automatically promoted.

## 🌐 Live web search

JARVIS can now look things up in real time — no API key, no cloud account, just DuckDuckGo.

- **Automatic** — say "latest news on X", "what happened with Y", "is Z still running?", ask about anything with a date/recency signal and JARVIS fetches live results before answering.
- **Explicit** — `/search <query>` forces a web search regardless of intent.
- Results are cited inline [1] [2] so you know exactly where the answer came from.
- Fully local routing: the search fetch and LLM synthesis run on your PC. Nothing new leaves your machine beyond the DDG query.

## 📱 JARVIS Mobile

A companion iOS PWA so you can talk to your PC's JARVIS from your phone.

- Installs from Safari — Share → Add to Home Screen. No App Store, completely free.
- Same streaming chat, dark JARVIS theme, image attachment for vision queries.
- Remote access via Tailscale (no port forwarding required).
- Start the server: `python src/jarvis_server.py`

---
*Local-first as always — web search queries go to DuckDuckGo (a privacy-respecting search engine). The results and JARVIS's answer stay on your machine.*
