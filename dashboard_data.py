"""
dashboard_data.py
=========================================================================
Module cung cap du lieu cho serve_dashboard.py. Khong tu chay simulation
theo cong thuc rieng - moi con so deu lay tu run_simulation.py (SE) va
optimize/ (OE), dung nguyen ham cong khai cua 2 module do.

2 nhom ham chinh:

  - build_dashboard_payload(...)   : NHANH (1 lan simulate). Dung cho
                                      /api/dashboard - heatmap + KPI +
                                      parking_view cua LICH HIEN TAI.

  - build_optimization_payload(...): CHAM (goi optimize_multi_move, ~10s
                                      voi max_moves=3 tren dataset that -
                                      da do gio thuc te). Dung cho
                                      /api/optimize - so sanh before/after
                                      THAT bang optimizer that cua OE,
                                      khong bia so.

LUU Y VE "shift" (quan trong - day la loi da gap va sua o test_pipeline.py,
gio ap dung lai o day):
  run_simulation_from_data() luu FLOW-SLOT (S0..S4 - khung gio giao ca
  đến/đi, dung chung cho ca 4 bai xe) vao truong "shift" cua tung dong
  ket qua - KHONG PHAI Ca-hoc (Ca1..Ca4) trong schedule.csv. Mot slot
  (vi du S1) la ranh gioi DUNG CHUNG giua 2 Ca (tan Ca1 + vao Ca2) nen
  KHONG the quy nguoc ve dung 1 Ca duy nhat. Thay vi gian nhan "Ca1..Ca4"
  len parking_view (sai ngu nghia, se khong bao gio khop filter), moi
  dong parking_view o day tra ve DUNG ca hai:
    - "slot": "S0".."S4"          (gia tri that, dung de filter/so sanh)
    - "slot_label": vd "09:05-09:55 (tan Ca1 / vao Ca2)"  (hien thi)
  Khung gio lay THAT tu build_window_slot_map(schedule) - khong doan.
=========================================================================
"""

from __future__ import annotations

import copy
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from run_simulation import (
    DATASET_DIR,
    SHIFT_ORDER,
    build_window_slot_map,
    load_dataset,
    run_simulation_from_data,
)

HERE = Path(__file__).resolve().parent
# Hỗ trợ nhận cả 2 tên thư mục: 'optimization' hoặc 'optimize'
OPTIMIZER_DIR = HERE / "optimization" if (HERE / "optimization").is_dir() else HERE / "optimize"

# ---------------------------------------------------------------------
# Adapter voi package OE (./optimization/ hoặc ./optimize/)
# ---------------------------------------------------------------------
try:
    sys.path.insert(0, str(OPTIMIZER_DIR))
    from se_bridge import SimulationEngineerBridge  # type: ignore
    from optimizer import optimize_multi_move        # type: ignore

    _OPTIMIZER_IMPORT_ERROR: Optional[Exception] = None
except (ImportError, FileNotFoundError, AttributeError) as exc:
    SimulationEngineerBridge = None  # type: ignore
    optimize_multi_move = None  # type: ignore
    _OPTIMIZER_IMPORT_ERROR = exc

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


# ---------------------------------------------------------------------
# Nhan khung gio cho tung flow-slot - lay THAT tu schedule, khong doan
# ---------------------------------------------------------------------

def build_slot_labels(schedule: List[dict]) -> Dict[str, str]:
    _, bounds = build_window_slot_map(schedule)

    def shift_touching(slot: str) -> List[str]:
        # slot S(i) la: departure cua SHIFT_ORDER[i-1] va arrival cua
        # SHIFT_ORDER[i] (xem build_event_shift_slot_map trong run_simulation.py)
        idx = int(slot[1:])
        touching = []
        if 0 <= idx - 1 < len(SHIFT_ORDER):
            touching.append(f"tan {SHIFT_ORDER[idx - 1]}")
        if 0 <= idx < len(SHIFT_ORDER):
            touching.append(f"vao {SHIFT_ORDER[idx]}")
        return touching

    labels = {}
    for slot, (start, end) in bounds.items():
        touching = " / ".join(shift_touching(slot))
        labels[slot] = f"{start}-{end} ({touching})"
    return labels


# ---------------------------------------------------------------------
# Heatmap + KPI (dung Ca-hoc that tu schedule.csv - phan nay KHONG dinh
# loi flow-slot vi lay thang tu row["shift"] cua schedule, khong phai
# cua ket qua mo phong)
# ---------------------------------------------------------------------

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
    cap_slots_map = {
        r["parking_lot_id"]: int(r["capacity_slots"])
        for r in parking_rows
        if r["scenario"] == scenario
    }

    view = []
    for r in results:
        lot = r["lot_id"]
        view.append(
            {
                "day": r["day"],
                "slot": r["shift"],  # gia tri THAT: S0..S4 (flow-slot)
                "slot_label": slot_labels.get(r["shift"], r["shift"]),
                "parking_lot": lot,
                "incoming": round(r["incoming"], 2),
                "outgoing": round(r["outgoing"], 2),
                "parking_capacity": cap_slots_map.get(lot, 0),
                "checkin_capacity": r["checkin_capacity"],
                "checkout_capacity": r["checkout_capacity"],
                "checkin_utilization_pct": round(r["checkin_util"] * 100, 1),
                "checkout_utilization_pct": round(r["checkout_util"] * 100, 1),
                "worst_utilization_pct": round(r["worst_util"] * 100, 1),
                "worst_util": r["worst_util"],
                "bottleneck_direction": r["bottleneck_direction"],
                "status": r["status"],
            }
        )
    return view


def build_kpi(schedule: List[dict], parking_view: List[dict]) -> dict:
    total_sessions = len(schedule)
    total_students = sum(int(r["num_students"]) for r in schedule)

    heatmap = build_heatmap(schedule)
    peak_day, peak_shift, peak_value = None, None, -1
    for day, shifts in heatmap.items():
        for shift, value in shifts.items():
            if value > peak_value:
                peak_day, peak_shift, peak_value = day, shift, value
    peak_demand = {"day": peak_day, "shift": peak_shift, "value": peak_value}

    worst_row = max(parking_view, key=lambda r: r["worst_util"])
    worst_parking = {
        "lot": worst_row["parking_lot"],
        "day": worst_row["day"],
        "slot": worst_row["slot"],
        "slot_label": worst_row["slot_label"],
        "direction": worst_row["bottleneck_direction"],
        "utilization_pct": round(worst_row["worst_util"] * 100, 1),
    }
    worst_day_time = {
        "day": worst_row["day"],
        "slot": worst_row["slot"],
        "slot_label": worst_row["slot_label"],
    }

    return {
        "total_sessions": total_sessions,
        "total_students": total_students,
        "peak_demand": peak_demand,
        "worst_parking": worst_parking,
        "worst_day_time": worst_day_time,
    }


def _strip_internal(parking_view: List[dict]) -> List[dict]:
    return [{k: v for k, v in row.items() if k != "worst_util"} for row in parking_view]


# ---------------------------------------------------------------------
# /api/dashboard - nhanh, 1 lan simulate
# ---------------------------------------------------------------------

def build_dashboard_payload(
    scenario: str = "Normal",
    include_events: bool = True,
    dataset_dir: Path = DATASET_DIR,
    data: Optional[dict] = None,
) -> dict:
    # Neu server truyen data (_live_data) thi dung data do; chi tu load khi khong co
    if data is None:
        data = load_dataset(dataset_dir)
    schedule, events, parking = data["schedule"], data["events"], data["parking"]

    results = run_simulation_from_data(
        schedule, events, parking, scenario=scenario, include_events=include_events
    )
    slot_labels = build_slot_labels(schedule)
    parking_view = build_parking_view(results, parking, scenario, slot_labels)

    return {
        "scenario": scenario,
        "include_events": include_events,
        "heatmap": build_heatmap(schedule),
        "kpi": build_kpi(schedule, parking_view),
        "parking_view": _strip_internal(parking_view),
        "slot_labels": slot_labels,
    }


# ---------------------------------------------------------------------
# /api/optimize - goi optimizer THAT (optimize_multi_move). Cham (~10s
# tren dataset that voi max_moves=3) - server phia tren PHAI cache lai,
# khong goi lai ham nay moi request.
# ---------------------------------------------------------------------

def build_optimization_payload(
    scenario: str = "Normal",
    include_events: bool = True,
    max_moves: int = 3,
    top_k: int = 10,
    dataset_dir: Path = DATASET_DIR,
    se_file: Optional[Path] = None,
    data: Optional[dict] = None,
) -> dict:
    if SimulationEngineerBridge is None or optimize_multi_move is None:
        return {
            "available": False,
            "reason": f"optimize/ khong san sang ({_OPTIMIZER_IMPORT_ERROR})",
        }

    # Neu server truyen data (_live_data) thi dung data do; chi tu load khi khong co
    if data is None:
        data = load_dataset(dataset_dir)
    schedule, events, parking, rooms = (
        data["schedule"],
        data["events"],
        data["parking"],
        data["rooms"],
    )

    bridge = SimulationEngineerBridge(str(se_file or (HERE / "run_simulation.py")))
    result = optimize_multi_move(
        bridge=bridge,
        schedule=copy.deepcopy(schedule),  # phong optimizer sua tai cho lam hong _live_data
        rooms=rooms,
        parking=parking,
        events=events,
        scenario=scenario,
        include_events=include_events,
        max_moves=max_moves,
        top_k=top_k,
    )

    slot_labels = build_slot_labels(schedule)

    before_results = run_simulation_from_data(
        schedule, events, parking, scenario=scenario, include_events=include_events
    )
    after_results = run_simulation_from_data(
        result.optimized_schedule, events, parking, scenario=scenario, include_events=include_events
    )
    before_view = _strip_internal(
        build_parking_view(before_results, parking, scenario, slot_labels)
    )
    after_view = _strip_internal(
        build_parking_view(after_results, parking, scenario, slot_labels)
    )

    return {
        "available": True,
        "scenario": scenario,
        "include_events": include_events,
        "max_moves": max_moves,
        "moves_applied": len(result.moves),
        "stop_reason": result.stop_reason,
        "moves": result.moves,
        "before": {
            "metrics": result.baseline_metrics,
            "heatmap": build_heatmap(schedule),
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

    parser = argparse.ArgumentParser(description="In thu payload dashboard (debug)")
    parser.add_argument("--scenario", default="Normal", choices=["Normal", "Worst"])
    parser.add_argument("--no-events", action="store_true")
    parser.add_argument("--optimize", action="store_true", help="In payload /api/optimize thay vi /api/dashboard")
    parser.add_argument("--max-moves", type=int, default=3)
    args = parser.parse_args()

    if args.optimize:
        payload = build_optimization_payload(
            scenario=args.scenario, include_events=not args.no_events, max_moves=args.max_moves
        )
    else:
        payload = build_dashboard_payload(scenario=args.scenario, include_events=not args.no_events)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
