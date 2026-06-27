# JARVIS — CLAUDE CODE MEMORY FILE
**Rev C · June 2026 · Paste this at the start of any Claude Code session on this repo.**

For full STELLIUM project context (APEX-SUIT, APEX-K1, compute, business) see:
`geterhun/exo-suit` → `CLAUDE_MEMORY.md` on `main`.

This file covers Jarvis-specific context only.

---

## WHAT JARVIS IS

**Jarvis Chat** (v1.6.0, this repo) is a fully local Windows desktop AI overlay.
It is the **stepping stone** toward **Jarvis Combat AI**, which will power the
**VISOR-Phi HMD** on the APEX-SUIT/APEX-K1 system.

Development path:
```
Jarvis Chat (now, v1.6.0) → Jarvis Combat AI (v2.x) → VISOR-Phi HMD
```

Every capability built in Jarvis Chat transfers directly:
- `qwen2.5vl` vision → threat localization on VISOR-Phi
- 3D spatial reasoning → real-time situational map
- Self-memory across sessions → persistent threat tracking
- Distillation pipeline → workstation model → edge model (Jetson Orin NX)
- Intent classifier (`jarvis_intent.py`) → Four-Act Protocol trigger

---

## CURRENT STATE — v1.6.0

Branch: `jarvis-chat-release`

| Module | What it does |
|--------|--------------|
| `src/jarvis_chat.py` | Main UI — tkinter floating overlay, 190 KB |
| `src/jarvis_router.py` | Routes messages to fast/deep/vision/code models |
| `src/jarvis_intent.py` | Learned intent classifier |
| `src/jarvis_self.py` | Persistent personality + self-notes across sessions |
| `src/jarvis_idle.py` | `/idle` — practices 3D designs while PC is idle |
| `src/jarvis_voice.py` | Whisper STT + Piper TTS, wake word "hey JARVIS" |
| `src/jarvis_design_memory.py` | Remembers successful 3D designs |
| `src/jarvis_recipes.py` | Recipe library for Blender scripts |
| `src/core.py` | Ollama client, streaming, model management |
| `src/council.py` | Multi-model consensus (council mode) |
| `training/gen_design_dataset.py` | Generates Blender design training data |
| `training/distill_recipes.py` | Knowledge distillation pipeline |
| `training/push_to_hf.py` | Pushes datasets to HuggingFace |

**Models (Ollama):**
- Fast: `qwen2.5:7b`
- Deep: `deepseek-r1:14b`
- Vision: `qwen2.5vl:7b` (fallback: `llava:7b`)
- Code: `qwen2.5-coder:7b`

---

## JARVIS COMBAT AI — WHAT IT NEEDS TO BECOME

### Sensor Inputs (real-time)
```
GUARDIAN-AI acoustic:   bearing ±3°, range estimate, confidence, 17ms latency
GUARDIAN-AI thermal:    LWIR bounding boxes, temp delta, 500m range
APEX-SUIT IMU:          per-module acceleration + orientation (7 groups)
APEX-SUIT pressure map: per-strip contraction state (binary + analog)
APEX-K1 EGaIn:          zone integrity (5 zones, 0.0–1.0)
APEX-K1 N₂ pressure:    current psi, burst count remaining
Operator voice:         via GUARDIAN-AI 8-mic MEMS array
```

### Outputs to VISOR-Phi (<5ms)
```
Threat bearing + distance overlay
Situational awareness map (2D top-down + 3D AR layer)
Suit health per module (color-coded)
Blade integrity per zone
Four-Act Protocol current act + recommendation
Alert overlays (gunshot detection, thermal anomaly)
Jarvis tactical voice (Piper TTS)
```

### Four-Act Protocol
```
Act I   — OBSERVE:   No engagement. GUARDIAN-AI scan. Jarvis assessment.
Act II  — ENGAGE:    Piezo active (+5-10%). Initial strike.
Act III — ESCALATE:  Piezo + N₂ (+30-35%). Committed.
Act IV  — FINAL:     All 3 + Full-Sync suit pre-load (80-120%). Everything fires.
```
Full-Sync: sword sends BT 5.3 pre-fire signal 5ms BEFORE EM discharge so suit
pre-loads flexor/extensor groups. Jarvis must generate this signal.

### Dual-Path Inference Architecture
```
Fast path:  small quantized model — <10ms — reactive overlays + Act I/II triggers
Slow path:  full model — 100-500ms — deliberative tactical planning + Act III/IV
```

### Edge Deployment
**NVIDIA Jetson Orin NX** (~65 TOPS, ~750g, backpack-mountable)
- Quantize to INT8/FP8
- Use v1.6.0 distill tooling from `training/`
- Connect to VISOR-Phi via BT 5.3
- Fast path runs on Jetson; slow path can offload to CORE over WireGuard VPN

---

## TRAINING DATA

Combat training JSONL lives in `geterhun/exo-suit` → `sim/combat/`.
This repo also has a standalone copy: `training/gen_combat_dataset.py`

```bash
python training/gen_combat_dataset.py --count 10000 --out training/combat_data.jsonl --seed 42
```

Output schema per line:
```json
{"scenario": {...}, "prompt": "...", "completion": "..."}
```
Drop straight into the distillation pipeline (`training/distill_recipes.py`).

---

## BANKAI IDENTITY

```
APEX-K1 alone          = Shikai  (initial release — already dangerous)
APEX-K1 + APEX-SUIT    = Bankai  (full release — all power unlocked)
Jarvis Combat AI        = Zanpakuto spirit (intelligence behind both)
```

Jarvis is NOT just a UI assistant. It is the intelligence that makes
the Bankai possible — real-time threat awareness, Four-Act decision engine,
tactical voice, sensor fusion across sword + suit + environment.

---

## COMBAT AI PENDING TASKS

See `combat/pending.md` for full prioritized list.

Top 3 right now:
1. Define sensor data protocol — see `combat/sensor_protocol.md`
2. Run `training/gen_combat_dataset.py --count 10000` for first training set
3. Fine-tune Jarvis on spatial/geometric reasoning from that data

---

## WHAT CLAUDE CODE SHOULD FOCUS ON (this repo)

**Best used for:**
1. Extending `src/jarvis_router.py` — add Combat AI routing path
2. New `src/jarvis_combat.py` module — Four-Act Protocol + sensor fusion
3. `src/jarvis_visor.py` — BT 5.3 output to VISOR-Phi
4. `training/gen_combat_dataset.py` — expand scenario types
5. Jetson deployment scripts — quantization, INT8 export
6. Benchmarking Jarvis Chat response latency (baseline for Combat AI target)

**Do NOT:**
- Break the existing v1.6.0 chat features — Combat AI is additive
- Add cloud dependencies — stays fully local
- Put CORE location or STELLIUM restricted info in this repo

---

*STELLIUM LLC · Confidential Engineering Reference · Rev C · June 2026*
