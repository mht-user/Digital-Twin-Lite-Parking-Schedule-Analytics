from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Dict, List, Tuple


class SimulationEngineerBridge:
    """
    Thin adapter around the team's official Simulation Engineer module.

    IMPORTANT:
    - No simulation formula is duplicated here.
    - Every baseline/candidate/final timetable is evaluated by
      run_simulation_from_data(...) from the SE file.
    """

    def __init__(self, se_file: str | Path):
        self.se_file = Path(se_file).resolve()
        if not self.se_file.exists():
            raise FileNotFoundError(f"SE file not found: {self.se_file}")

        spec = importlib.util.spec_from_file_location(
            "official_simulation_engineer", self.se_file
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot import SE module: {self.se_file}")

        self.se = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.se)

        required = [
            "load_dataset",
            "validate_dataset",
            "run_simulation_from_data",
            "get_distance_weights",
            "build_window_slot_map",
        ]
        missing = [name for name in required if not hasattr(self.se, name)]
        if missing:
            raise AttributeError(
                "SE module is missing required functions: " + ", ".join(missing)
            )

    def load_dataset(self, dataset_dir: str | Path) -> Dict[str, List[dict]]:
        data = self.se.load_dataset(Path(dataset_dir))
        self.se.validate_dataset(data)
        return data

    def simulate(
        self,
        schedule: List[dict],
        events: List[dict],
        parking: List[dict],
        scenario: str = "Normal",
        include_events: bool = False,
        full_grid: bool = False,
    ) -> List[dict]:
        results = self.se.run_simulation_from_data(
            schedule=schedule,
            events=events,
            parking=parking,
            scenario=scenario,
            include_events=include_events,
            full_grid=full_grid,
        )

        # The official SE stores the flow-slot code (S0-S4) under the key
        # "shift" (not to be confused with schedule.csv's Ca1-Ca4 shift).
        # optimizer.py and test_optimizer.py were written against a "slot"
        # key. Alias it here, at the adapter boundary, so no SE formula or
        # OE logic has to change.
        for row in results:
            row.setdefault("slot", row.get("shift"))

        return results

    def distance_weights(
        self, parking: List[dict], scenario: str
    ) -> Dict[str, Dict[str, float]]:
        return self.se.get_distance_weights(parking, scenario)

    def row_flow_slots(
        self, schedule: List[dict], row: dict
    ) -> Tuple[str, str]:
        """
        Ask the SE's own window->slot mapping which flow slots a session uses.
        Returns (arrival_slot, departure_slot).
        """
        window_slot_map, _ = self.se.build_window_slot_map(schedule)

        arrival_key = (
            "arrival",
            row["arrival_window_start"].strip(),
            row["arrival_window_end"].strip(),
        )
        departure_key = (
            "departure",
            row["departure_window_start"].strip(),
            row["departure_window_end"].strip(),
        )

        return (
            window_slot_map[arrival_key],
            window_slot_map[departure_key],
        )
