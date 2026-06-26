"""Standalone combat scenario generator for Jarvis Combat AI training.

This is a self-contained copy of the generator in geterhun/exo-suit sim/combat/.
Run it here to build Jarvis training data without cloning the other repo.

Usage:
    pip install pydantic>=2.0 rich>=13.0
    python training/gen_combat_dataset.py --count 10000 --out training/combat_data.jsonl --seed 42

Output: JSONL file, one scenario per line:
    {"scenario": {...}, "prompt": "...", "completion": "..."}
Feed directly into training/distill_recipes.py.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from typing import List

try:
    from pydantic import BaseModel, Field
except ImportError:
    print("pydantic>=2.0 required: pip install pydantic>=2.0", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class Vec3(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def distance_to(self, other: "Vec3") -> float:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2)

    def bearing_to_deg(self, other: "Vec3") -> float:
        dx = other.x - self.x
        dy = other.y - self.y
        return (math.degrees(math.atan2(dx, dy)) + 360) % 360


class ThreatState(BaseModel):
    id: int
    position: Vec3
    velocity: Vec3 = Field(default_factory=Vec3)
    bearing_deg: float
    range_m: float
    bearing_noise_deg: float = 0.0
    detected_acoustic: bool = True
    detected_thermal: bool = False
    event_type: str = "footstep"
    is_active: bool = False
    has_firearm: bool = False


class SuitStatus(BaseModel):
    power_fraction: float = 1.0
    hv_enabled: bool = True
    emergency_stop: bool = False
    module_health: dict = Field(default_factory=lambda: {
        k: 1.0 for k in ["flexor", "extensor", "thumb", "wrist", "thenar", "hypothenar", "lumbricals"]
    })


class BladeStatus(BaseModel):
    n2_psi: int = 800
    n2_bursts_remaining: int = 15
    supercap_j: int = 12000
    mach_delta_enabled: bool = True
    mach_delta_stage: int = 1
    zone_integrity: dict = Field(default_factory=lambda: {
        z: 1.0 for z in ["kissaki", "ha", "shinogi", "hira", "mune"]
    })


class CombatScenario(BaseModel):
    scenario_id: int
    scenario_type: str
    wielder_pos: Vec3
    threats: List[ThreatState]
    suit: SuitStatus
    blade: BladeStatus
    gunshot_detected: bool = False
    obstacle_count: int = 0
    four_act: int = 1

    def to_training_prompt(self) -> str:
        lines = ["GUARDIAN-AI SENSOR REPORT"]
        if not self.threats:
            lines.append("Acoustic: no contacts")
            lines.append("Thermal: no contacts")
        else:
            acoustic = [t for t in self.threats if t.detected_acoustic]
            thermal = [t for t in self.threats if t.detected_thermal]
            if acoustic:
                for t in acoustic:
                    lines.append(
                        f"Acoustic T{t.id}: bearing {t.bearing_deg + t.bearing_noise_deg:.1f}° "
                        f"range {t.range_m:.0f}m [{t.event_type}]"
                    )
            else:
                lines.append("Acoustic: no contacts")
            if thermal:
                for t in thermal:
                    lines.append(f"Thermal T{t.id}: bearing {t.bearing_deg:.1f}° confirmed")
            else:
                lines.append("Thermal: no contacts")
        if self.gunshot_detected:
            lines.append("ALERT: ballistic signature detected")
        lines.append(f"Suit power: {self.suit.power_fraction * 100:.0f}%")
        min_zone = min(self.blade.zone_integrity.values())
        lines.append(f"Blade integrity: {min_zone * 100:.0f}% (worst zone)")
        lines.append(f"N2: {self.blade.n2_psi} psi ({self.blade.n2_bursts_remaining} bursts)")
        em_pct = self.blade.supercap_j / 12000 * 100
        lines.append(f"Supercap: {em_pct:.0f}%")
        if self.obstacle_count > 0:
            lines.append(f"Obstacles: {self.obstacle_count} detected")
        lines.append("\nWhat is the correct Four-Act Protocol response?")
        return "\n".join(lines)

    def to_training_completion(self) -> str:
        act_names = {
            1: "Act I — OBSERVE",
            2: "Act II — ENGAGE",
            3: "Act III — ESCALATE",
            4: "Act IV — FINAL",
        }
        act_desc = {
            1: "No threats confirmed. Continue GUARDIAN-AI full scan. No engagement. Jarvis assessment only.",
            2: "Single confirmed threat, not active. Activate Component 1 (piezo, +5-10%). Initial strike ready.",
            3: "Multiple threats or active engagement or low resources. Components 1+2 active (piezo + N2, +30-35%). Committed posture.",
            4: "Active engagement with firearm detected or multiple close threats. All components + Full-Sync suit pre-load (80-120% boost). Everything fires.",
        }
        act = self.four_act
        return f"{act_names[act]}\n{act_desc[act]}"


# ---------------------------------------------------------------------------
# Sensor noise model
# ---------------------------------------------------------------------------

ACOUSTIC_MAX_RANGE = 500.0
ACOUSTIC_BEARING_STD = 1.5
CLOSE_RANGE = 10.0
MED_RANGE = 50.0
LOW_POWER = 0.25
LOW_N2 = 200


def _detection_probability(range_m: float) -> float:
    if range_m <= 0:
        return 0.0
    p = 1.0 - (range_m / ACOUSTIC_MAX_RANGE) ** 1.5
    return max(0.0, min(1.0, p))


def _noisy_bearing(bearing_deg: float, rng: random.Random) -> float:
    noise = rng.gauss(0, ACOUSTIC_BEARING_STD)
    return (bearing_deg + noise) % 360


# ---------------------------------------------------------------------------
# Four-Act labeling
# ---------------------------------------------------------------------------

def _label_four_act(scenario: CombatScenario) -> int:
    active = [t for t in scenario.threats if t.is_active]
    confirmed = [t for t in scenario.threats if t.detected_acoustic]
    close = [t for t in confirmed if t.range_m <= CLOSE_RANGE]
    med = [t for t in confirmed if t.range_m <= MED_RANGE]

    low_power = scenario.suit.power_fraction < LOW_POWER
    low_n2 = scenario.blade.n2_psi < LOW_N2
    firearm = scenario.gunshot_detected or any(t.has_firearm for t in active)

    if active and (firearm or len(close) > 0) and scenario.blade.supercap_j > 6000:
        return 4
    if active or len(med) > 1 or low_power or low_n2:
        return 3
    if len(confirmed) >= 1:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Scenario generators
# ---------------------------------------------------------------------------

def _random_pos(rng: random.Random, max_xy: float = 200.0) -> Vec3:
    return Vec3(x=rng.uniform(-max_xy, max_xy), y=rng.uniform(-max_xy, max_xy), z=0.0)


def _make_threat(
    tid: int,
    pos: Vec3,
    wielder: Vec3,
    rng: random.Random,
    is_active: bool = False,
    has_firearm: bool = False,
) -> ThreatState:
    bearing = wielder.bearing_to_deg(pos)
    dist = wielder.distance_to(pos)
    det_acoustic = rng.random() < _detection_probability(dist)
    det_thermal = det_acoustic and dist <= 100.0 and rng.random() < 0.8
    noise = _noisy_bearing(bearing, rng) - bearing if det_acoustic else 0.0
    return ThreatState(
        id=tid,
        position=pos,
        bearing_deg=bearing,
        range_m=round(dist, 1),
        bearing_noise_deg=round(noise, 2),
        detected_acoustic=det_acoustic,
        detected_thermal=det_thermal,
        is_active=is_active,
        has_firearm=has_firearm,
        event_type="gunshot" if has_firearm and is_active else "footstep",
    )


def generate_scenario(sid: int, scenario_type: str, rng: random.Random) -> CombatScenario:
    wielder = Vec3(x=0.0, y=0.0, z=0.0)
    threats: list[ThreatState] = []
    suit = SuitStatus(power_fraction=rng.uniform(0.5, 1.0))
    blade = BladeStatus(
        n2_psi=rng.randint(400, 800),
        n2_bursts_remaining=rng.randint(5, 15),
        supercap_j=rng.randint(6000, 12000),
    )
    gunshot = False
    obstacles = rng.randint(0, 5)

    if scenario_type == "no_threat":
        pass

    elif scenario_type == "single_close":
        pos = Vec3(x=rng.uniform(-8, 8), y=rng.uniform(3, 9), z=0.0)
        threats.append(_make_threat(0, pos, wielder, rng, is_active=True))

    elif scenario_type == "active_engagement":
        pos = Vec3(x=rng.uniform(-5, 5), y=rng.uniform(2, 7), z=0.0)
        has_gun = rng.random() < 0.6
        t = _make_threat(0, pos, wielder, rng, is_active=True, has_firearm=has_gun)
        threats.append(t)
        gunshot = has_gun
        blade.supercap_j = rng.randint(8000, 12000)

    elif scenario_type == "multi_threat":
        n = rng.randint(2, 4)
        for i in range(n):
            pos = _random_pos(rng, max_xy=80.0)
            threats.append(_make_threat(i, pos, wielder, rng, is_active=(i == 0)))
        suit.power_fraction = rng.uniform(0.15, 0.5)

    else:  # random
        n = rng.randint(0, 3)
        for i in range(n):
            pos = _random_pos(rng)
            threats.append(_make_threat(i, pos, wielder, rng, is_active=rng.random() < 0.3))
        gunshot = rng.random() < 0.1

    sc = CombatScenario(
        scenario_id=sid,
        scenario_type=scenario_type,
        wielder_pos=wielder,
        threats=threats,
        suit=suit,
        blade=blade,
        gunshot_detected=gunshot,
        obstacle_count=obstacles,
        four_act=1,  # placeholder
    )
    sc.four_act = _label_four_act(sc)
    return sc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SCENARIO_TYPES = ["random", "no_threat", "single_close", "active_engagement", "multi_threat"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Jarvis Combat AI training scenarios")
    parser.add_argument("--count", type=int, default=1000, help="Number of scenarios")
    parser.add_argument("--out", default="training/combat_data.jsonl", help="Output JSONL path")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    try:
        from rich.progress import track
        iter_fn = lambda x, desc: track(x, description=desc)
    except ImportError:
        iter_fn = lambda x, desc: x

    counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    written = 0

    with open(args.out, "w") as f:
        indices = list(range(args.count))
        for i in iter_fn(indices, f"Generating {args.count} scenarios..."):
            stype = SCENARIO_TYPES[i % len(SCENARIO_TYPES)]
            sc = generate_scenario(i, stype, rng)
            record = {
                "scenario": sc.model_dump(),
                "prompt": sc.to_training_prompt(),
                "completion": sc.to_training_completion(),
            }
            f.write(json.dumps(record) + "\n")
            counts[sc.four_act] += 1
            written += 1

    print(f"Wrote {written} scenarios to {args.out}")
    print("Four-Act distribution:")
    for act, n in sorted(counts.items()):
        pct = n / written * 100
        print(f"  Act {act}: {n:>5} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
