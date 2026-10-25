"""Deterministic crowd simulator. All output is SIMULATED and must be labelled as such."""
from __future__ import annotations

import random
from typing import Dict

from backend.engine.forecast import update_occupancy

SCENARIOS = ("normal_flow", "arrival_surge", "reduced_exit", "stale_sensor")


class Simulator:
    """One simulated minute per step. Same (scenario, seed, capacity) => same sequence."""

    def __init__(self, scenario: str = "normal_flow", seed: int = 42, capacity: float = 1000.0,
                 exit_capacity: float = 60.0, start_fraction: float = 0.45):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario: {scenario}")
        self.scenario = scenario
        self.capacity = float(capacity)
        self.exit_capacity = float(exit_capacity)
        self.rng = random.Random(seed)
        self.occupancy = round(self.capacity * start_fraction, 2)
        self.minute = 0

    def step(self) -> Dict[str, object]:
        self.minute += 1
        m = self.minute
        n_in = self.rng.uniform(-0.05, 0.05)
        n_out = self.rng.uniform(-0.05, 0.05)
        base_entry = self.capacity * 0.012
        entry = base_entry * (1 + n_in)
        exit_coeff = 0.02
        eff_exit_cap = self.exit_capacity
        emit = True
        if self.scenario == "arrival_surge" and m >= 3:
            entry = base_entry * (1 + min(2.5, (m - 3) * 0.5)) * (1 + n_in)
        elif self.scenario == "reduced_exit" and m >= 3:
            entry = base_entry * 1.5 * (1 + n_in)
            exit_coeff = 0.005
            eff_exit_cap = self.exit_capacity * 0.15
        elif self.scenario == "stale_sensor" and m >= 5:
            emit = False
        demand = self.occupancy * 0.02
        exit_rate = min(eff_exit_cap, self.occupancy * exit_coeff * (1 + n_out))
        entry = round(max(0.0, entry), 2)
        exit_rate = round(max(0.0, exit_rate), 2)
        self.occupancy = round(update_occupancy(self.occupancy, entry, exit_rate), 2)
        return {"minute": m, "scenario": self.scenario, "entry_rate": entry, "exit_rate": exit_rate,
                "occupancy": self.occupancy, "exit_demand": round(demand, 2),
                "exit_capacity": round(eff_exit_cap, 2), "emit": emit, "source": "simulated"}
