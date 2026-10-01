from __future__ import annotations

import sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional

from run_simulation import (
    DATASET_DIR,
    SHIFT_ORDER,
    load_dataset,
    build_window_slot_map,
    run_simulation_from_dataset,
)

HERE = Path(__file__).resolve().parent
OPTIMIZER_DIR = HERE / "optimization"

try:
    sys.path.insert(0, str(OPTIMIZER_DIR))
    from se_bridge import SimulationEngineerBridge  # type: ignore
    from optimizer import optimize_multi_move  # type: ignore
    _OPTIMIZER_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # surfaced through API payload
    SimulationEngineerBridge = None  # type: ignore
    optimize_multi_move = None  # type: ignore
    _OPTIMIZER_IMPORT_ERROR = exc

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def build_slot_labels(schedule: List[dict]) -> Dict[str, str]:
    _, bounds = build_window_slot_map(schedule)

    def touching(slot: str) -> str:
        idx = int(slot[1:])
        labels = []
        if 0 <= idx - 1 < len(SHIFT_ORDER):
            labels.append(f"tan {SHIFT_ORDER[idx - 1]}")
        if 0 <= idx < len(SHIFT_ORDER):
            labels.append(f"vao {SHIFT_ORDER[idx]}")
        return " / ".join(labels)

    return {
        slot: f"{start}-{end} ({touching(slot)})"
        for slot, (start, end) in bounds.items()
    }


def build_heatmap(schedule: List[dict]) -> dict:
    totals: Dict[tuple, int] = defaultdict(int)
    for row in schedule:
        totals[(row["day_of_week"], row["shift"])] += int(row["num_students"])

    heatmap = {day: {shift: 0 for shift in SHIFT_ORDER} for day in DAY_LABELS}
    for (day, shift), total in totals.items():
        if day in heatmap and shift in heatmap[day]:
            heatmap[day][shift] = total
    return heatmap


def build_parking_view(
    results: List[dict],
    parking_rows: List[dict],
    scenario: str,
    slot_labels: Dict[str, str],
) -> List[dict]:
    capacity_map = {
        row["parking_lot_id"]: int(row["capacity_slots"])
        for row in parking_rows
        if row["scenario"] == scenario
    }

    return [
        {
            "day": row["day"],
            "slot": row["shift"],
            "slot_label": slot_labels.get(row["shift"], row["shift"]),
            "parking_lot": row["lot_id"],
            "incoming": round(float(row["incoming"]), 2),
            "outgoing": round(float(row["outgoing"]), 2),
            "parking_capacity": capacity_map.get(row["lot_id"], row.get("parking_capacity", 0)),
            "checkin_capacity": row["checkin_capacity"],
            "checkout_capacity": row["checkout_capacity"],
            "checkin_utilization_pct": round(float(row["checkin_util"]) * 100, 1),
            "checkout_utilization_pct": round(float(row["checkout_util"]) * 100, 1),
            "worst_utilization_pct": round(float(row["worst_util"]) * 100, 1),
            "worst_util": float(row["worst_util"]),
            "bottleneck_direction": row["bottleneck_direction"],
            "status": row["status"],
        }
        for row in results
    ]


def build_kpi(data: Dict[str, List[dict]], parking_view: List[dict]) -> dict:
    schedule = data["schedule"]
    heatmap = build_heatmap(schedule)

    peak_day, peak_shift, peak_value = None, None, -1
    for day, shifts in heatmap.items():
        for shift, value in shifts.items():
            if value > peak_value:
                peak_day, peak_shift, peak_value = day, shift, value

    worst_row = max(parking_view, key=lambda row: row["worst_util"])
    return {
        "total_sessions": len(schedule),
        # Unique students, not the sum of all session headcounts.
        "total_students": len(data.get("students", [])),
        "peak_demand": {"day": peak_day, "shift": peak_shift, "value": peak_value},
        "worst_parking": {
            "lot": worst_row["parking_lot"],
            "day": worst_row["day"],
            "slot": worst_row["slot"],
            "slot_label": worst_row["slot_label"],
            "direction": worst_row["bottleneck_direction"],
            "utilization_pct": round(worst_row["worst_util"] * 100, 1),
        },
        "worst_day_time": {
            "day": worst_row["day"],
            "slot": worst_row["slot"],
            "slot_label": worst_row["slot_label"],
        },
    }


def _strip_internal(parking_view: List[dict]) -> List[dict]:
    return [{k: v for k, v in row.items() if k != "worst_util"} for row in parking_view]


def build_dashboard_payload(
    scenario: str = "Normal",
    include_events: bool = True,
    dataset_dir: Path = DATASET_DIR,
    data: Optional[Dict[str, List[dict]]] = None,
) -> dict:
    current = data if data is not None else load_dataset(dataset_dir)
    schedule = current["schedule"]
    results = run_simulation_from_dataset(
        current,
        scenario=scenario,
        include_events=include_events,
        full_grid=True,
    )
    slot_labels = build_slot_labels(schedule)
    parking_view = build_parking_view(results, current["parking"], scenario, slot_labels)
    return {
        "scenario": scenario,
        "include_events": include_events,
        "simulation_model": "student-visit-based",
        "heatmap": build_heatmap(schedule),
        "kpi": build_kpi(current, parking_view),
        "parking_view": _strip_internal(parking_view),
        "slot_labels": slot_labels,
    }


def build_optimization_payload(
    scenario: str = "Normal",
    include_events: bool = True,
    max_moves: int = 3,
    top_k: int = 10,
    dataset_dir: Path = DATASET_DIR,
    se_file: Optional[Path] = None,
    data: Optional[Dict[str, List[dict]]] = None,
) -> dict:
    if SimulationEngineerBridge is None or optimize_multi_move is None:
        return {"available": False, "reason": f"Optimizer unavailable: {_OPTIMIZER_IMPORT_ERROR}"}

    current = deepcopy(data) if data is not None else load_dataset(dataset_dir)
    bridge = SimulationEngineerBridge(str(se_file or (HERE / "run_simulation.py")))
    result = optimize_multi_move(
        bridge=bridge,
        data=current,
        scenario=scenario,
        include_events=include_events,
        max_moves=max_moves,
        top_k=top_k,
    )

    slot_labels = build_slot_labels(current["schedule"])
    before_view = _strip_internal(build_parking_view(
        result.baseline_results, current["parking"], scenario, slot_labels
    ))
    after_view = _strip_internal(build_parking_view(
        result.final_results, current["parking"], scenario, slot_labels
    ))

    return {
        "available": True,
        "scenario": scenario,
        "include_events": include_events,
        "simulation_model": "student-visit-based",
        "max_moves": max_moves,
        "moves_applied": len(result.moves),
        "stop_reason": result.stop_reason,
        "moves": result.moves,
        "before": {
            "metrics": result.baseline_metrics,
            "heatmap": build_heatmap(current["schedule"]),
            "parking_view": before_view,
        },
        "after": {
            "metrics": result.final_metrics,
            "heatmap": build_heatmap(result.optimized_schedule),
            "parking_view": after_view,
        },
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="Normal", choices=["Normal", "Worst"])
    parser.add_argument("--include-events", action="store_true")
    parser.add_argument("--optimize", action="store_true")
    parser.add_argument("--max-moves", type=int, default=3)
    args = parser.parse_args()

    payload = (
        build_optimization_payload(args.scenario, args.include_events, args.max_moves)
        if args.optimize
        else build_dashboard_payload(args.scenario, args.include_events)
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
