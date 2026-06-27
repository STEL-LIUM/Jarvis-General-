# Jarvis Combat AI — Sensor Data Protocol
**Rev A · June 2026**

This document defines the wire format for sensor data flowing from
APEX-SUIT + APEX-K1 hardware into Jarvis Combat AI.

All packets are JSON over a local WebSocket connection (or BT 5.3 serial).
Target update rate: **100 Hz** for fast-path data, **10 Hz** for slow-path.

---

## Packet Format

Every packet has a `type` field. Jarvis routes by `type`.

```json
{
  "type": "sensor_packet",
  "ts_ms": 1719432001234,
  "guardian": { ... },
  "suit": { ... },
  "blade": { ... }
}
```

---

## `guardian` — GUARDIAN-AI Sensor Block

```json
"guardian": {
  "acoustic": [
    {
      "id": 0,
      "bearing_deg": 47.3,
      "bearing_std_deg": 1.5,
      "range_m": 120.0,
      "range_confidence": 0.72,
      "event_type": "footstep",
      "detected": true
    }
  ],
  "thermal": [
    {
      "id": 0,
      "bearing_deg": 47.1,
      "elevation_deg": 2.0,
      "temp_delta_c": 8.4,
      "bbox": [0.31, 0.22, 0.68, 0.78],
      "label": "person",
      "confidence": 0.91
    }
  ],
  "gunshot_detected": false,
  "mic_active": true
}
```

| Field | Type | Notes |
|-------|------|-------|
| `acoustic[].bearing_deg` | float | 0–360°, clockwise from north |
| `acoustic[].bearing_std_deg` | float | ±3° max per GUARDIAN-AI spec |
| `acoustic[].range_m` | float | Estimated. 0 if unknown. Max 500m. |
| `acoustic[].range_confidence` | float | 0.0–1.0. <0.3 = range unknown |
| `acoustic[].event_type` | string | `footstep`, `gunshot`, `vehicle`, `impact`, `unknown` |
| `thermal[].bbox` | array[4] | [x1, y1, x2, y2] normalized 0–1 in LWIR frame |
| `thermal[].temp_delta_c` | float | Above ambient. >3°C = likely person. |
| `gunshot_detected` | bool | True if any mic detects ballistic signature |

---

## `suit` — APEX-SUIT Block

```json
"suit": {
  "modules": {
    "flexor":      { "contraction": 0.72, "imu": [0.1, -0.3, 9.8], "health": 1.0 },
    "extensor":    { "contraction": 0.10, "imu": [0.0, -0.1, 9.8], "health": 1.0 },
    "thumb":       { "contraction": 0.40, "imu": [0.0,  0.0, 9.8], "health": 0.95 },
    "wrist":       { "contraction": 0.55, "imu": [0.2, -0.1, 9.8], "health": 1.0 },
    "thenar":      { "contraction": 0.30, "imu": [0.0,  0.0, 9.8], "health": 1.0 },
    "hypothenar":  { "contraction": 0.20, "imu": [0.0,  0.0, 9.8], "health": 1.0 },
    "lumbricals":  { "contraction": 0.15, "imu": [0.0,  0.0, 9.8], "health": 1.0 }
  },
  "power_fraction": 0.87,
  "hv_enabled": true,
  "emergency_stop": false
}
```

| Field | Type | Notes |
|-------|------|-------|
| `modules.<name>.contraction` | float | 0.0 = fully relaxed, 1.0 = full contraction |
| `modules.<name>.imu` | array[3] | [ax, ay, az] in m/s². Local to module. |
| `modules.<name>.health` | float | 0.0 = failed, 1.0 = nominal. From AD5933 impedance scan. |
| `power_fraction` | float | APEX-CELL state-of-charge. <0.25 = low power alert. |
| `hv_enabled` | bool | Whether kV bus is live. |
| `emergency_stop` | bool | True = E-stop triggered, all HV disabled. |

---

## `blade` — APEX-K1 Block

```json
"blade": {
  "zones": {
    "kissaki":  { "integrity": 1.0, "temp_c": 22.1 },
    "ha":       { "integrity": 0.97, "temp_c": 22.3 },
    "shinogi":  { "integrity": 1.0, "temp_c": 22.0 },
    "hira":     { "integrity": 1.0, "temp_c": 22.0 },
    "mune":     { "integrity": 1.0, "temp_c": 22.1 }
  },
  "n2_psi": 780,
  "n2_bursts_remaining": 12,
  "supercap_j": 9800,
  "mach_delta_enabled": true,
  "mach_delta_stage": 1,
  "contact_detected": false
}
```

| Field | Type | Notes |
|-------|------|-------|
| `zones.<name>.integrity` | float | 0.0 = open circuit (damaged), 1.0 = nominal. EGaIn resistance. |
| `zones.<name>.temp_c` | float | Zone temperature. Saya thermal sleeve keeps >0°C. |
| `n2_psi` | int | Current pneumatic reservoir pressure. Nominal 800 psi. |
| `n2_bursts_remaining` | int | Estimated bursts at current pressure. |
| `supercap_j` | int | EM coil supercap charge in Joules. Full = 12000 J. |
| `mach_delta_enabled` | bool | Whether MACH-Delta is armed. |
| `mach_delta_stage` | int | 1 = piezo+pneumatic only. 2 = + EM coil (V2). |
| `contact_detected` | bool | EGaIn channel detects blade contact. |

---

## Command Packets (Jarvis → Hardware)

Jarvis sends command packets back to suit + sword firmware.

### Pre-fire Signal (Full-Sync Strike)
```json
{
  "type": "cmd",
  "cmd": "prefire",
  "delay_ms": 5,
  "groups": ["flexor", "extensor"]
}
```
Sent **5ms before** EM discharge. Suit firmware pre-loads listed muscle groups.
Delay is measured from packet receipt at suit MCU.

### Four-Act Recommendation
```json
{
  "type": "cmd",
  "cmd": "four_act",
  "act": 2,
  "reason": "single threat confirmed at 47° / 120m"
}
```

### Emergency Stop
```json
{
  "type": "cmd",
  "cmd": "emergency_stop",
  "scope": "all"
}
```
`scope` = `"all"` | `"suit"` | `"blade"`

---

## Timing Requirements

| Path | Target | Notes |
|------|--------|-------|
| Sensor → Jarvis fast path | <10ms | Act I/II reactive overlays |
| Sensor → Jarvis slow path | <500ms | Act III/IV deliberative plan |
| Jarvis → VISOR-Phi overlay | <5ms | BT 5.3 WebSocket |
| Pre-fire signal → suit MCU | ≥5ms before EM discharge | Full-Sync spec |
| GUARDIAN-AI latency | 17ms | Hardware spec, cannot improve in software |

---

## Transport

**Development / workstation testing:**
- WebSocket on `ws://localhost:9876` (Jarvis listens)
- Sensor simulator: `python training/gen_combat_dataset.py --replay` (future)

**Field (Jetson):**
- BT 5.3 serial framing: 4-byte length prefix + UTF-8 JSON payload
- Fallback: USB CDC serial at 921600 baud
- VISOR-Phi: separate BT 5.3 link (different MAC/channel from sensor link)

---

## Versioning

All packets carry `"protocol_version": "1.0"` once spec is stable.
Jarvis Combat AI must reject packets with unknown version and log a warning.
