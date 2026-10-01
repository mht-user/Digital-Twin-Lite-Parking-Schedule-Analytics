from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set


class SimulationEngineerBridge:
    """Use the official Simulation Engineer module without duplicating its formulas.

    Full baseline/final simulations still go through the official SE entry point.
    Candidate search can use an exact delta evaluator that recomputes only the
    students affected by one proposed class move, then applies their flow delta
    to the official baseline. This keeps the same SE visit model while making
    large multi-move searches much faster.
    """

    def __init__(self, se_file: str | Path):
        self.se_file = Path(se_file).resolve()
        if not self.se_file.exists():
            raise FileNotFoundError(f"SE file not found: {self.se_file}")

        spec = importlib.util.spec_from_file_location("official_simulation_engineer", self.se_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot import SE module: {self.se_file}")
        self.se = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.se)

        required = [
            "load_dataset",
            "validate_dataset",
            "run_simulation_with_contributors",
            "get_top_contributors",
            "build_visits_from_dataset",
            "get_distance_weights",
            "get_gate_capacity",
            "classify_status",
            "get_bottleneck_direction",
        ]
        missing = [name for name in required if not hasattr(self.se, name)]
        if missing:
            raise AttributeError("SE module missing: " + ", ".join(missing))

    def load_dataset(self, dataset_dir: str | Path) -> Dict[str, List[dict]]:
        data = self.se.load_dataset(Path(dataset_dir))
        self.se.validate_dataset(data)
        return data

    def simulate(
        self,
        data: Dict[str, List[dict]],
        schedule: Optional[List[dict]] = None,
        scenario: str = "Normal",
        include_events: bool = False,
        full_grid: bool = False,
    ) -> Tuple[List[dict], dict]:
        simulation_data = dict(data)
        if schedule is not None:
            simulation_data["schedule"] = schedule
        return self.se.run_simulation_with_contributors(
            simulation_data,
            scenario=scenario,
            include_events=include_events,
            full_grid=full_grid,
        )

    def top_contributors(
        self,
        contributors: dict,
        day: str,
        slot: str,
        lot_id: str,
        direction: str,
        top_n: int,
    ):
        return self.se.get_top_contributors(
            contributors, day, slot, lot_id, direction, top_n
        )

    # ------------------------------------------------------------------
    # Exact fast candidate evaluation
    # ------------------------------------------------------------------
    def prepare_fast_context(
        self,
        data: Dict[str, List[dict]],
        schedule: List[dict],
        scenario: str,
        include_events: bool,
    ) -> dict:
        """Run one official SE baseline and prepare reusable arrays/maps."""
        results, contributors = self.simulate(
            data,
            schedule,
            scenario=scenario,
            include_events=include_events,
            full_grid=False,
        )

        incoming = {}
        outgoing = {}
        for row in results:
            key = (row["day"], row["shift"], row["lot_id"])
            incoming[key] = float(row["incoming"])
            outgoing[key] = float(row["outgoing"])

        return {
            "results": results,
            "contributors": contributors,
            "incoming": incoming,
            "outgoing": outgoing,
            "distance_weights": self.se.get_distance_weights(data["parking"], scenario),
            "gate_capacity": self.se.get_gate_capacity(data["parking"], scenario),
            "scenario": scenario,
            "include_events": include_events,
            "before_flow_cache": {},
            "student_by_id": {row["student_id"]: row for row in data["students"]},
            "enrollments_by_student": self._index_enrollments(data["enrollments"]),
        }

    @staticmethod
    def _index_enrollments(enrollments: List[dict]) -> Dict[str, List[dict]]:
        result: Dict[str, List[dict]] = defaultdict(list)
        for row in enrollments:
            result[row["student_id"]].append(row)
        return dict(result)

    @staticmethod
    def _filtered_student_data(
        data: Dict[str, List[dict]],
        schedule: List[dict],
        student_ids: Set[str],
        student_by_id: Dict[str, dict],
        enrollments_by_student: Dict[str, List[dict]],
    ) -> Dict[str, List[dict]]:
        # All classes of the affected students must remain in the subset because
        # moving one class can change the student's whole daily parking visit.
        students = [student_by_id[sid] for sid in student_ids if sid in student_by_id]
        enrollments = []
        for sid in student_ids:
            enrollments.extend(enrollments_by_student.get(sid, ()))
        return {
            "classes": data["classes"],
            "schedule": schedule,
            "rooms": data["rooms"],
            "parking": data["parking"],
            "events": [],
            "students": students,
            "enrollments": enrollments,
            "student_behavior": data["student_behavior"],
        }

    @staticmethod
    def _flow_maps_from_visits(visits: List[dict], distance_weights: dict):
        incoming = defaultdict(float)
        outgoing = defaultdict(float)
        for visit in visits:
            for lot_id, parking_weight in distance_weights[visit["building"]].items():
                flow = float(visit["weight"]) * float(parking_weight)
                if flow <= 1e-12:
                    continue
                incoming[(visit["day"], visit["checkin_slot"], lot_id)] += flow
                outgoing[(visit["day"], visit["checkout_slot"], lot_id)] += flow
        return dict(incoming), dict(outgoing)

    def _affected_flow_maps(
        self,
        context: dict,
        data: Dict[str, List[dict]],
        schedule: List[dict],
        student_ids: Set[str],
        distance_weights: dict,
    ):
        if not student_ids:
            return {}, {}
        subset = self._filtered_student_data(
            data, schedule, student_ids,
            context["student_by_id"], context["enrollments_by_student"]
        )
        visits = self.se.build_visits_from_dataset(subset)
        return self._flow_maps_from_visits(visits, distance_weights)

    def evaluate_candidate_fast(
        self,
        context: dict,
        data: Dict[str, List[dict]],
        baseline_schedule: List[dict],
        candidate_schedule: List[dict],
        affected_student_ids: Set[str],
        cache_key: str,
    ) -> List[dict]:
        """Exact candidate simulation by replacing only affected-student flows.

        The baseline was produced by the official SE. A one-session MOVE can only
        change parking visits of students enrolled in that class. We therefore:
          baseline total flow
          - affected students' old flow
          + affected students' new flow

        Final selected candidates are still verified with a full official SE run.
        """
        distance_weights = context["distance_weights"]
        before_cache = context["before_flow_cache"]

        if cache_key not in before_cache:
            before_cache[cache_key] = self._affected_flow_maps(
                context, data, baseline_schedule, affected_student_ids, distance_weights
            )
        before_in, before_out = before_cache[cache_key]
        after_in, after_out = self._affected_flow_maps(
            context, data, candidate_schedule, affected_student_ids, distance_weights
        )

        incoming = dict(context["incoming"])
        outgoing = dict(context["outgoing"])

        all_delta_keys = set(before_in) | set(before_out) | set(after_in) | set(after_out)
        for key in all_delta_keys:
            incoming[key] = (
                incoming.get(key, 0.0)
                - before_in.get(key, 0.0)
                + after_in.get(key, 0.0)
            )
            outgoing[key] = (
                outgoing.get(key, 0.0)
                - before_out.get(key, 0.0)
                + after_out.get(key, 0.0)
            )
            if abs(incoming[key]) < 1e-10:
                incoming[key] = 0.0
            if abs(outgoing[key]) < 1e-10:
                outgoing[key] = 0.0

        gate_capacity = context["gate_capacity"]
        keys = set(incoming) | set(outgoing)
        results = []
        for day, slot, lot_id in keys:
            cap = gate_capacity[lot_id]
            checkin_capacity = int(cap["checkin"])
            checkout_capacity = int(cap["checkout"])
            incoming_value = max(0.0, incoming.get((day, slot, lot_id), 0.0))
            outgoing_value = max(0.0, outgoing.get((day, slot, lot_id), 0.0))

            checkin_util = (
                incoming_value / checkin_capacity
                if checkin_capacity > 0
                else (float("inf") if incoming_value > 0 else 0.0)
            )
            checkout_util = (
                outgoing_value / checkout_capacity
                if checkout_capacity > 0
                else (float("inf") if outgoing_value > 0 else 0.0)
            )
            worst_util = max(checkin_util, checkout_util)

            results.append({
                "day": day,
                "shift": slot,
                "lot_id": lot_id,
                "incoming": incoming_value,
                "outgoing": outgoing_value,
                "checkin_capacity": checkin_capacity,
                "checkout_capacity": checkout_capacity,
                "parking_capacity": int(cap["capacity_slots"]),
                "checkin_util": checkin_util,
                "checkout_util": checkout_util,
                "worst_util": worst_util,
                "bottleneck_direction": self.se.get_bottleneck_direction(
                    checkin_util, checkout_util
                ),
                "status": self.se.classify_status(worst_util),
            })

        # Same stable ordering as SE output.
        if hasattr(self.se, "_sort_key"):
            results.sort(key=self.se._sort_key)
        else:
            results.sort(key=lambda row: (row["day"], row["shift"], row["lot_id"]))
        return results
