# Jarvis Combat AI — Development Roadmap

Jarvis Combat AI is the intelligence layer for the APEX-SUIT + APEX-K1 system,
delivering real-time sensor fusion, Four-Act Protocol decisions, and tactical
overlays to the VISOR-Phi HMD.

This is NOT a separate product — it is v2.x of Jarvis, built on the v1.6.0
foundation in this repo.

---

## Phase Roadmap

### Phase 1 — Foundation (Current)
**Goal:** Jarvis Chat answers spatial/combat questions accurately.

- [x] Vision model (qwen2.5vl) for scene understanding
- [x] Persistent self-memory across sessions
- [x] 3D spatial reasoning (Blender design + self-correction)
- [x] Intent classifier
- [ ] Fine-tune on combat scenario dataset (10K+ scenarios from `training/gen_combat_dataset.py`)
- [ ] Benchmark response latency on Aryan's workstation (RTX 3080 Ti + Tesla P40)

### Phase 2 — Sensor Integration
**Goal:** Jarvis ingests GUARDIAN-AI + suit sensor data and reasons about it.

- [ ] Define sensor data protocol (`combat/sensor_protocol.md`)
- [ ] `src/jarvis_combat.py` — sensor fusion module
- [ ] GUARDIAN-AI acoustic bearing → threat bearing overlay output
- [ ] GUARDIAN-AI thermal → bounding box overlay
- [ ] APEX-SUIT IMU per-module → arm/leg state estimation
- [ ] APEX-K1 EGaIn zone integrity → blade health display

### Phase 3 — Four-Act Protocol Engine
**Goal:** Jarvis recommends and triggers Act transitions in real time.

- [ ] Four-Act state machine (`src/jarvis_four_act.py`)
- [ ] Fast path (<10ms) for Act I/II reactive overlays
- [ ] Slow path (100-500ms) for Act III/IV deliberative planning
- [ ] Full-Sync pre-fire signal: Jarvis generates BT 5.3 signal 5ms before EM discharge
- [ ] VISOR-Phi output module (`src/jarvis_visor.py`)

### Phase 4 — VISOR-Phi Integration
**Goal:** All outputs display correctly on the HMD.

- [ ] BT 5.3 WebSocket protocol spec finalized
- [ ] Threat bearing rendered as directional overlay
- [ ] 2D situational map (top-down) + AR layer
- [ ] Suit health per module (7 groups, color-coded)
- [ ] Blade integrity per zone (5 zones)
- [ ] Four-Act status and recommendation overlay
- [ ] Latency target: <5ms from sensor event to VISOR-Phi update

### Phase 5 — Edge Deployment
**Goal:** Full Combat AI runs on Jetson Orin NX (backpack-mounted).

- [ ] Research Jetson Orin NX vs Orin Nano — TOPS/size/power tradeoff
- [ ] Quantize to INT8/FP8 using v1.6.0 distill tooling
- [ ] Fast path on Jetson (<10ms target)
- [ ] Slow path option: offload to CORE over WireGuard VPN
- [ ] Field test: GUARDIAN-AI bearing → Jarvis → VISOR-Phi overlay (end-to-end)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     APEX-SUIT (body)                    │
│  IMU ×7 modules ──┐                                     │
│  Pressure map ────┤                                     │
└───────────────────┤                                     │
                    │                                     │
┌─────────────────────────────────────────────────────────┐
│                     APEX-K1 (sword)                     │
│  GUARDIAN-AI ─────┤  acoustic ±3°, 500m, 17ms          │
│  GUARDIAN-AI ─────┤  LWIR thermal cam                  │
│  EGaIn zones ─────┤  blade integrity ×5                 │
│  N₂ pressure ─────┤  burst count                        │
│  Operator voice ──┘                                     │
└───────────────────┬─────────────────────────────────────┘
                    │  sensor packet (JSON, ~100 Hz)
                    ▼
┌─────────────────────────────────────────────────────────┐
│                Jarvis Combat AI                         │
│                                                         │
│  ┌─────────────────┐   ┌─────────────────────────────┐ │
│  │  Fast Path      │   │  Slow Path                  │ │
│  │  <10ms          │   │  100-500ms                  │ │
│  │  Quantized INT8 │   │  Full model                 │ │
│  │  Act I/II react │   │  Act III/IV tactical plan   │ │
│  └────────┬────────┘   └──────────────┬──────────────┘ │
│           └──────────────┬────────────┘                │
│                          │                             │
│              ┌───────────▼────────────┐               │
│              │   VISOR-Phi Output     │               │
│              │   BT 5.3 · <5ms        │               │
│              └────────────────────────┘               │
└─────────────────────────────────────────────────────────┘
                    │  BT 5.3
                    ▼
┌─────────────────────────────────────────────────────────┐
│                  VISOR-Phi HMD                          │
│  Threat overlays · Suit health · Blade status          │
│  Four-Act status · Situational map · Tactical voice    │
└─────────────────────────────────────────────────────────┘
```

---

## Key Constraints

- **Fully local** — no cloud inference in combat. Jetson Orin NX or CORE over VPN.
- **<5ms to VISOR-Phi** — fast path must stay quantized INT8 on Jetson.
- **Full-Sync timing** — pre-fire signal must leave Jarvis ≥5ms before EM discharge.
- **CORE location** — never in this repo. Nextcloud `/STELLIUM_FILES/RESTRICTED/`.
- **Combat AI is additive** — do not break existing v1.6.0 chat features.

---

## File Layout (planned)

```
src/
  jarvis_combat.py      ← sensor fusion + state tracking        [TODO]
  jarvis_four_act.py    ← Four-Act Protocol state machine        [TODO]
  jarvis_visor.py       ← BT 5.3 VISOR-Phi output               [TODO]
  jarvis_router.py      ← extend: add combat routing path        [TODO]
  (existing v1.6.0 files unchanged)

combat/
  README.md             ← this file
  pending.md            ← prioritized task list
  sensor_protocol.md    ← sensor data wire format

training/
  gen_combat_dataset.py ← combat scenario generator
  (existing training files unchanged)
```
