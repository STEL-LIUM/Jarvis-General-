# Jarvis Combat AI — Pending Tasks

Ordered by dependency (earlier items unblock later items).
Mark `[x]` when done. Log date + result in `PROGRESS.md`.

---

## Priority 1 — Data First (do now, no hardware needed)

- [ ] **Generate first combat training dataset**
  ```bash
  python training/gen_combat_dataset.py --count 10000 --out training/combat_data.jsonl --seed 42
  ```
  Requires `pydantic>=2.0`, `numpy>=1.24`, `rich>=13.0`.

- [ ] **Fine-tune Jarvis on spatial/geometric reasoning**
  Feed `combat_data.jsonl` into `training/distill_recipes.py`.
  Target: Jarvis correctly labels Four-Act scenarios ≥90% accuracy.

- [ ] **Benchmark baseline response latency**
  Measure `qwen2.5:7b` and `deepseek-r1:14b` P50/P95 on Aryan's workstation.
  Document in `PROGRESS.md`. This is the baseline the Jetson must beat.

---

## Priority 2 — Sensor Protocol (spec before code)

- [ ] **Finalize sensor_protocol.md**
  Review `combat/sensor_protocol.md` — confirm field names, types, Hz rates
  match actual GUARDIAN-AI hardware spec when known. Update if hardware changes.

- [ ] **Define Four-Act state machine transitions**
  Write `src/jarvis_four_act.py`:
  - Inputs: parsed sensor packet (see sensor_protocol.md)
  - State: current Act (I–IV)
  - Transitions: threat count, range, suit health, N₂ pressure thresholds
  - Output: current Act + recommendation string for VISOR-Phi
  - Timing: must run on fast path (<10ms)

---

## Priority 3 — Sensor Fusion Module

- [ ] **`src/jarvis_combat.py` — sensor fusion**
  - Parse incoming sensor JSON (see `combat/sensor_protocol.md`)
  - Maintain rolling threat list (last 5 seconds)
  - Estimate wielder pose from IMU modules
  - Run Four-Act state machine
  - Emit VISOR-Phi overlay packet

- [ ] **Extend `src/jarvis_router.py`**
  Add `combat` routing path: if source is `sensor_packet`, bypass LLM routing
  and go directly to `jarvis_combat.py` fast path.

---

## Priority 4 — VISOR-Phi Output

- [ ] **`src/jarvis_visor.py` — BT 5.3 output**
  - WebSocket server on Jetson (or workstation for testing)
  - Serializes overlay packet to JSON
  - Sends to VISOR-Phi over BT 5.3 · target <5ms round-trip
  - Fallback: USB serial if BT link drops

- [ ] **Full-Sync pre-fire signal**
  In `jarvis_visor.py`: when Act IV is triggered, emit `{"cmd": "prefire", "delay_ms": 5}`
  to suit firmware 5ms BEFORE EM discharge command. Both firmware stacks must
  implement this handshake — define in `combat/sensor_protocol.md`.

---

## Priority 5 — Jetson Edge Deployment

- [ ] **Choose Jetson Orin NX vs Orin Nano**
  Research: 65 TOPS NX vs 40 TOPS Nano, weight/size/power. Document decision.

- [ ] **INT8/FP8 quantization**
  Adapt `training/distill_recipes.py` to produce Jetson-optimized model.
  Target: fast path <10ms on Jetson NX.

- [ ] **Slow path VPN offload**
  Design: Jetson fast path handles <10ms Act I/II.
  Slow path (Act III/IV deliberative) sends to CORE over WireGuard, gets response.
  Acceptable if total round-trip ≤500ms.

---

## Priority 6 — Integration Tests

- [ ] **Simulated sensor feed test**
  Feed `combat_data.jsonl` scenarios into `jarvis_combat.py` in replay mode.
  Confirm Four-Act labels match ground truth ≥90%.

- [ ] **First end-to-end test (no hardware)**
  Mock GUARDIAN-AI bearing input → Jarvis → VISOR-Phi WebSocket → display.

- [ ] **First end-to-end test (hardware)**
  Real GUARDIAN-AI bearing → Jarvis on workstation → VISOR-Phi HMD overlay.
  Measure latency. Target: <50ms total sensor-to-display.

- [ ] **Full-Sync pre-fire timing validation**
  Oscilloscope both the Jarvis `prefire` signal and the EM discharge trigger.
  Confirm ≥5ms gap. Adjust if needed.
