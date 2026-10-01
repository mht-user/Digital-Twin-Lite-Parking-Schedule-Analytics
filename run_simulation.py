from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "Dataset"

BUILDINGS = ["A2", "B", "C", "D"]
PARKING_LOTS = ["P1", "P2", "P3", "P4"]
DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SHIFT_ORDER = ["Ca1", "Ca2", "Ca3", "Ca4"]
SLOT_ORDER = ["S0", "S1", "S2", "S3", "S4"]
SLOT_LABELS = {
    "S0": "Truoc Ca1",
    "S1": "Giao Ca1-Ca2",
    "S2": "Giao Ca2-Ca3",
    "S3": "Giao Ca3-Ca4",
    "S4": "Sau Ca4",
}

PEAK_THRESHOLD = 0.90
BOTTLENECK_THRESHOLD = 1.0
EPS = 1e-9


def read_csv(path: Path) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Khong tim thay file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_dataset(dataset_dir: Path = DATASET_DIR) -> Dict[str, List[dict]]:
    dataset_dir = Path(dataset_dir)
    return {
        "classes": read_csv(dataset_dir / "classes.csv"),
        "schedule": read_csv(dataset_dir / "schedule.csv"),
        "rooms": read_csv(dataset_dir / "rooms.csv"),
        "parking": read_csv(dataset_dir / "parking.csv"),
        "events": read_csv(dataset_dir / "events.csv"),
        "students": read_csv(dataset_dir / "students.csv"),
        "enrollments": read_csv(dataset_dir / "enrollments.csv"),
        "student_behavior": read_csv(dataset_dir / "student_behavior.csv"),
    }


def clone_dataset(data: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    return {key: [dict(row) for row in rows] for key, rows in data.items()}


def validate_dataset(data: Dict[str, List[dict]]) -> None:
    required_files = {
        "classes": ["class_id", "num_students"],
        "rooms": ["room_id", "building", "floor", "room_capacity"],
        "schedule": [
            "schedule_id", "class_id", "day_of_week", "day_vn", "shift",
            "building", "room_id", "num_students", "arrival_window_start",
            "arrival_window_end", "departure_window_start", "departure_window_end",
        ],
        "parking": [
            "scenario", "parking_lot_id", "capacity_slots",
            "max_checkin_throughput_veh_per_30min",
            "max_checkout_throughput_veh_per_30min",
            "dist_from_A2_m", "dist_from_B_m", "dist_from_C_m", "dist_from_D_m",
        ],
        "events": [
            "event_id", "day_of_week", "shift", "building",
            "num_students", "motorbike_ratio",
        ],
        "students": ["student_id", "distance_group", "uses_motorbike"],
        "enrollments": ["student_id", "class_id"],
        "student_behavior": [
            "distance_group", "gap_0_leave_ratio", "gap_1_leave_ratio",
            "gap_2plus_leave_ratio",
        ],
    }

    for key, columns in required_files.items():
        rows = data.get(key)
        if rows is None or not rows:
            raise ValueError(f"Dataset thieu hoac rong: {key}")
        missing = [column for column in columns if column not in rows[0]]
        if missing:
            raise ValueError(f"{key} thieu cot: {missing}")

    scenarios = defaultdict(set)
    for row in data["parking"]:
        scenarios[row["scenario"]].add(row["parking_lot_id"])
    for scenario in ("Normal", "Worst"):
        if scenarios.get(scenario) != set(PARKING_LOTS):
            raise ValueError(
                f"parking.csv scenario {scenario} phai co dung P1/P2/P3/P4; "
                f"hien co {sorted(scenarios.get(scenario, set()))}"
            )


def time_to_minutes(value: str) -> int:
    hour, minute = value.strip().split(":")
    return int(hour) * 60 + int(minute)


def build_window_slot_map(
    schedule: List[dict],
) -> Tuple[Dict[Tuple[str, str, str], str], Dict[str, Tuple[str, str]]]:
    """Map schedule arrival/departure windows to the five traffic slots S0..S4."""
    window_slot_map: Dict[Tuple[str, str, str], str] = {}
    slot_bounds: Dict[str, List[str]] = {}

    def register(slot: str, start: str, end: str) -> None:
        if slot not in slot_bounds:
            slot_bounds[slot] = [start, end]
            return
        if time_to_minutes(start) < time_to_minutes(slot_bounds[slot][0]):
            slot_bounds[slot][0] = start
        if time_to_minutes(end) > time_to_minutes(slot_bounds[slot][1]):
            slot_bounds[slot][1] = end

    for row in schedule:
        shift = row["shift"]
        if shift not in SHIFT_ORDER:
            raise ValueError(f"Shift khong hop le: {shift}")
        idx = SHIFT_ORDER.index(shift)

        a_start = row["arrival_window_start"].strip()
        a_end = row["arrival_window_end"].strip()
        a_slot = SLOT_ORDER[idx]
        a_key = ("arrival", a_start, a_end)
        previous = window_slot_map.get(a_key)
        if previous is not None and previous != a_slot:
            raise ValueError(f"Arrival window {a_start}-{a_end} map vao nhieu slot")
        window_slot_map[a_key] = a_slot
        register(a_slot, a_start, a_end)

        d_start = row["departure_window_start"].strip()
        d_end = row["departure_window_end"].strip()
        d_slot = SLOT_ORDER[idx + 1]
        d_key = ("departure", d_start, d_end)
        previous = window_slot_map.get(d_key)
        if previous is not None and previous != d_slot:
            raise ValueError(f"Departure window {d_start}-{d_end} map vao nhieu slot")
        window_slot_map[d_key] = d_slot
        register(d_slot, d_start, d_end)

    return window_slot_map, {k: (v[0], v[1]) for k, v in slot_bounds.items()}


def build_event_shift_slot_map() -> Dict[str, Tuple[str, str]]:
    return {
        shift: (SLOT_ORDER[idx], SLOT_ORDER[idx + 1])
        for idx, shift in enumerate(SHIFT_ORDER)
    }


def load_leave_ratio_table(student_behavior: List[dict]) -> Dict[str, Dict[str, float]]:
    table: Dict[str, Dict[str, float]] = {}
    for row in student_behavior:
        table[row["distance_group"]] = {
            "gap_0": float(row["gap_0_leave_ratio"]),
            "gap_1": float(row["gap_1_leave_ratio"]),
            "gap_2plus": float(row["gap_2plus_leave_ratio"]),
        }
    return table


def gap_category(skipped_shifts: int) -> str:
    if skipped_shifts <= 0:
        return "gap_0"
    if skipped_shifts == 1:
        return "gap_1"
    return "gap_2plus"


def compute_visits(
    shift_idxs: List[int],
    weight: float,
    segment: str,
    leave_ratio_table: Dict[str, Dict[str, float]],
    default_leave_ratio: float = 0.0,
) -> List[dict]:
    """
    Convert one student's daily shift sequence to expected parking visits.

    Consecutive shifts stay in one visit when gap_0_leave_ratio=0.
    A gap splits the expected flow according to the configured leave ratio.
    """
    idxs = sorted(set(shift_idxs))
    if not idxs:
        return []

    active = [(idxs[0], float(weight))]
    closed: List[dict] = []

    for previous_idx, current_idx in zip(idxs, idxs[1:]):
        category = gap_category(current_idx - previous_idx - 1)
        ratio = leave_ratio_table.get(segment, {}).get(category, default_leave_ratio)
        ratio = min(1.0, max(0.0, float(ratio)))

        next_active = []
        for checkin_idx, flow_weight in active:
            leave_weight = flow_weight * ratio
            stay_weight = flow_weight - leave_weight

            if stay_weight > EPS:
                next_active.append((checkin_idx, stay_weight))
            if leave_weight > EPS:
                closed.append({
                    "checkin_shift_idx": checkin_idx,
                    "checkout_shift_idx": previous_idx,
                    "weight": leave_weight,
                })
                next_active.append((current_idx, leave_weight))
        active = next_active

    last_idx = idxs[-1]
    for checkin_idx, flow_weight in active:
        if flow_weight > EPS:
            closed.append({
                "checkin_shift_idx": checkin_idx,
                "checkout_shift_idx": last_idx,
                "weight": flow_weight,
            })

    return closed


def build_student_shift_rows(data: Dict[str, List[dict]]) -> List[dict]:
    student_info = {
        row["student_id"]: {
            "distance_group": row["distance_group"],
            "uses_motorbike": row["uses_motorbike"] in ("1", "true", "True", 1, True),
        }
        for row in data["students"]
    }

    schedule_by_class: Dict[str, List[dict]] = defaultdict(list)
    for row in data["schedule"]:
        schedule_by_class[row["class_id"]].append(row)

    rows: List[dict] = []
    for enrollment in data["enrollments"]:
        student_id = enrollment["student_id"]
        info = student_info.get(student_id)
        if not info or not info["uses_motorbike"]:
            continue

        for session in schedule_by_class.get(enrollment["class_id"], []):
            shift = session["shift"]
            if shift not in SHIFT_ORDER:
                raise ValueError(f"Shift khong hop le: {shift}")
            rows.append({
                "student_id": student_id,
                "day_of_week": session["day_of_week"],
                "shift_idx": SHIFT_ORDER.index(shift),
                "building": session["building"],
                "schedule_id": session["schedule_id"],
                "segment": info["distance_group"],
                "weight": 1.0,
            })
    return rows


def build_visits_from_dataset(
    data: Dict[str, List[dict]],
    default_leave_ratio: float = 0.0,
) -> List[dict]:
    student_shift_rows = build_student_shift_rows(data)
    leave_ratio_table = load_leave_ratio_table(data["student_behavior"])

    grouped: Dict[Tuple[str, str], Dict[int, Tuple[str, str]]] = defaultdict(dict)
    segment_of: Dict[Tuple[str, str], str] = {}

    for row in student_shift_rows:
        key = (row["student_id"], row["day_of_week"])
        shift_idx = row["shift_idx"]
        if shift_idx in grouped[key]:
            raise ValueError(
                f"Student timetable conflict: {row['student_id']} "
                f"{row['day_of_week']} {SHIFT_ORDER[shift_idx]}"
            )
        grouped[key][shift_idx] = (row["building"], row["schedule_id"])
        segment_of[key] = row["segment"]

    visits: List[dict] = []
    for (student_id, day), shift_meta in grouped.items():
        idxs = sorted(shift_meta)
        raw_visits = compute_visits(
            idxs,
            weight=1.0,
            segment=segment_of[(student_id, day)],
            leave_ratio_table=leave_ratio_table,
            default_leave_ratio=default_leave_ratio,
        )

        for visit in raw_visits:
            checkin_building, checkin_source_id = shift_meta[visit["checkin_shift_idx"]]
            _checkout_building, checkout_source_id = shift_meta[visit["checkout_shift_idx"]]
            visits.append({
                "student_id": student_id,
                "day": day,
                "checkin_slot": SLOT_ORDER[visit["checkin_shift_idx"]],
                "checkout_slot": SLOT_ORDER[visit["checkout_shift_idx"] + 1],
                # The vehicle is parked once at the start of the visit and leaves
                # the same parking lot at the end of that visit.
                "building": checkin_building,
                "checkin_source_id": checkin_source_id,
                "checkout_source_id": checkout_source_id,
                "weight": float(visit["weight"]),
            })
    return visits


def get_parking_lot_ids(parking_rows: List[dict], scenario: str) -> List[str]:
    ids = {row["parking_lot_id"] for row in parking_rows if row["scenario"] == scenario}
    return sorted(ids, key=lambda lot: PARKING_LOTS.index(lot) if lot in PARKING_LOTS else 99)


def get_distance_weights(
    parking_rows: List[dict], scenario: str
) -> Dict[str, Dict[str, float]]:
    lots = [row for row in parking_rows if row["scenario"] == scenario]
    if not lots:
        raise ValueError(f"Khong co parking data cho scenario {scenario}")

    weights: Dict[str, Dict[str, float]] = {}
    for building in BUILDINGS:
        column = f"dist_from_{building}_m"
        inverse = {}
        for lot in lots:
            distance = float(lot[column])
            if distance <= 0:
                raise ValueError(f"Khoang cach khong hop le: {lot['parking_lot_id']} {column}")
            inverse[lot["parking_lot_id"]] = 1.0 / distance
        total = sum(inverse.values())
        weights[building] = {lot: value / total for lot, value in inverse.items()}
    return weights


def get_gate_capacity(
    parking_rows: List[dict], scenario: str
) -> Dict[str, Dict[str, int]]:
    return {
        row["parking_lot_id"]: {
            "checkin": int(row["max_checkin_throughput_veh_per_30min"]),
            "checkout": int(row["max_checkout_throughput_veh_per_30min"]),
            "capacity_slots": int(row["capacity_slots"]),
        }
        for row in parking_rows
        if row["scenario"] == scenario
    }


def classify_status(worst_ratio: float) -> str:
    if worst_ratio > BOTTLENECK_THRESHOLD + EPS:
        return "BOTTLENECK"
    if worst_ratio >= PEAK_THRESHOLD - EPS:
        return "PEAK"
    return "OK"


def get_bottleneck_direction(checkin_ratio: float, checkout_ratio: float) -> str:
    if max(checkin_ratio, checkout_ratio) < PEAK_THRESHOLD - EPS:
        return "-"
    if abs(checkin_ratio - checkout_ratio) <= EPS:
        return "Both"
    return "Checkin" if checkin_ratio > checkout_ratio else "Checkout"


def run_simulation_with_contributors(
    data: Dict[str, List[dict]],
    scenario: str = "Normal",
    include_events: bool = True,
    full_grid: bool = False,
    default_leave_ratio: float = 0.0,
) -> Tuple[List[dict], Dict[Tuple[str, str, str, str], List[Tuple[str, float]]]]:
    """Canonical student/visit-based simulation used by dashboard and optimizer."""
    validate_dataset(data)
    if scenario not in ("Normal", "Worst"):
        raise ValueError("scenario phai la Normal hoac Worst")

    visits = build_visits_from_dataset(data, default_leave_ratio)
    distance_weights = get_distance_weights(data["parking"], scenario)

    incoming_lot: Dict[Tuple[str, str, str], float] = defaultdict(float)
    outgoing_lot: Dict[Tuple[str, str, str], float] = defaultdict(float)
    contributors: Dict[Tuple[str, str, str, str], List[Tuple[str, float]]] = defaultdict(list)

    for visit in visits:
        for lot_id, parking_weight in distance_weights[visit["building"]].items():
            flow = visit["weight"] * parking_weight
            if flow <= EPS:
                continue

            incoming_key = (visit["day"], visit["checkin_slot"], lot_id)
            outgoing_key = (visit["day"], visit["checkout_slot"], lot_id)
            incoming_lot[incoming_key] += flow
            outgoing_lot[outgoing_key] += flow

            contributors[(visit["day"], visit["checkin_slot"], lot_id, "checkin")].append(
                (visit["checkin_source_id"], flow)
            )
            contributors[(visit["day"], visit["checkout_slot"], lot_id, "checkout")].append(
                (visit["checkout_source_id"], flow)
            )

    if include_events:
        event_slot_map = build_event_shift_slot_map()
        for event in data["events"]:
            shift = event["shift"]
            if shift not in event_slot_map:
                raise ValueError(f"Event shift khong hop le: {shift}")
            arrival_slot, departure_slot = event_slot_map[shift]
            day = event["day_of_week"]
            building = event["building"]
            vehicles = float(event["num_students"]) * float(event["motorbike_ratio"])
            source_id = event.get("event_id", "EVENT")

            for lot_id, parking_weight in distance_weights[building].items():
                flow = vehicles * parking_weight
                incoming_lot[(day, arrival_slot, lot_id)] += flow
                outgoing_lot[(day, departure_slot, lot_id)] += flow
                contributors[(day, arrival_slot, lot_id, "checkin")].append((source_id, flow))
                contributors[(day, departure_slot, lot_id, "checkout")].append((source_id, flow))

    gate_capacity = get_gate_capacity(data["parking"], scenario)
    if full_grid:
        days = sorted(
            {key[0] for key in incoming_lot} | {key[0] for key in outgoing_lot},
            key=lambda day: DAY_ORDER.index(day) if day in DAY_ORDER else 99,
        )
        all_keys = {
            (day, slot, lot)
            for day in days
            for slot in SLOT_ORDER
            for lot in get_parking_lot_ids(data["parking"], scenario)
        }
    else:
        all_keys = set(incoming_lot) | set(outgoing_lot)

    results: List[dict] = []
    for day, slot, lot_id in all_keys:
        capacity = gate_capacity[lot_id]
        incoming = incoming_lot.get((day, slot, lot_id), 0.0)
        outgoing = outgoing_lot.get((day, slot, lot_id), 0.0)
        checkin_capacity = capacity["checkin"]
        checkout_capacity = capacity["checkout"]

        checkin_util = (
            incoming / checkin_capacity if checkin_capacity > 0
            else (float("inf") if incoming > 0 else 0.0)
        )
        checkout_util = (
            outgoing / checkout_capacity if checkout_capacity > 0
            else (float("inf") if outgoing > 0 else 0.0)
        )
        worst_util = max(checkin_util, checkout_util)

        results.append({
            "day": day,
            "shift": slot,
            "lot_id": lot_id,
            "incoming": incoming,
            "outgoing": outgoing,
            "checkin_capacity": checkin_capacity,
            "checkout_capacity": checkout_capacity,
            "parking_capacity": capacity["capacity_slots"],
            "checkin_util": checkin_util,
            "checkout_util": checkout_util,
            "worst_util": worst_util,
            "bottleneck_direction": get_bottleneck_direction(checkin_util, checkout_util),
            "status": classify_status(worst_util),
        })

    results.sort(key=_sort_key)
    return results, contributors


def run_simulation_from_dataset(
    data: Dict[str, List[dict]],
    scenario: str = "Normal",
    include_events: bool = True,
    full_grid: bool = False,
    default_leave_ratio: float = 0.0,
) -> List[dict]:
    results, _ = run_simulation_with_contributors(
        data,
        scenario=scenario,
        include_events=include_events,
        full_grid=full_grid,
        default_leave_ratio=default_leave_ratio,
    )
    return results


def simulate_schedule(
    base_data: Dict[str, List[dict]],
    schedule: List[dict],
    scenario: str = "Normal",
    include_events: bool = True,
    full_grid: bool = False,
) -> List[dict]:
    candidate_data = dict(base_data)
    candidate_data["schedule"] = schedule
    return run_simulation_from_dataset(
        candidate_data,
        scenario=scenario,
        include_events=include_events,
        full_grid=full_grid,
    )


def run_simulation(
    dataset_dir: Path = DATASET_DIR,
    scenario: str = "Normal",
    include_events: bool = True,
    full_grid: bool = False,
) -> List[dict]:
    data = load_dataset(dataset_dir)
    return run_simulation_from_dataset(
        data,
        scenario=scenario,
        include_events=include_events,
        full_grid=full_grid,
    )


def compare_before_after(
    data: Dict[str, List[dict]],
    schedule_before: List[dict],
    schedule_after: List[dict],
    scenario: str = "Normal",
    include_events: bool = True,
) -> Tuple[List[dict], List[dict]]:
    return (
        simulate_schedule(data, schedule_before, scenario, include_events),
        simulate_schedule(data, schedule_after, scenario, include_events),
    )


def get_top_contributors(
    contributors: Dict[Tuple[str, str, str, str], List[Tuple[str, float]]],
    day: str,
    slot: str,
    lot_id: str,
    direction: str,
    top_n: int = 10,
) -> List[Tuple[str, float]]:
    items = contributors.get((day, slot, lot_id, direction.lower()), [])
    aggregated: Dict[str, float] = defaultdict(float)
    for source_id, flow in items:
        aggregated[source_id] += flow
    return sorted(aggregated.items(), key=lambda item: (-item[1], item[0]))[:top_n]


def _sort_key(row: dict):
    day_idx = DAY_ORDER.index(row["day"]) if row["day"] in DAY_ORDER else 99
    slot_idx = SLOT_ORDER.index(row["shift"]) if row["shift"] in SLOT_ORDER else 99
    lot_idx = PARKING_LOTS.index(row["lot_id"]) if row["lot_id"] in PARKING_LOTS else 99
    return (day_idx, slot_idx, lot_idx)


def get_slot_windows(schedule: List[dict]) -> Dict[str, Tuple[str, str]]:
    _, bounds = build_window_slot_map(schedule)
    return bounds


def format_percent(value: float) -> str:
    return "INF" if value == float("inf") else f"{value * 100:.0f}%"


def print_report(results: List[dict], scenario: str, schedule: Optional[List[dict]] = None) -> None:
    if schedule:
        bounds = get_slot_windows(schedule)
        print("Khung giao ca:")
        for slot in SLOT_ORDER:
            if slot in bounds:
                print(f"  {slot}: {SLOT_LABELS[slot]} ({bounds[slot][0]}-{bounds[slot][1]})")

    print(f"Scenario: {scenario}")
    header = (
        "Day | Slot | Lot | Incoming | Outgoing | Checkin TP | Checkout TP | "
        "Checkin Util | Checkout Util | Worst | Direction | Status"
    )
    print(header)
    print("-" * len(header))
    for row in results:
        print(
            f"{row['day']:3} | {row['shift']:4} | {row['lot_id']:3} | "
            f"{row['incoming']:8.1f} | {row['outgoing']:8.1f} | "
            f"{row['checkin_capacity']:10} | {row['checkout_capacity']:11} | "
            f"{format_percent(row['checkin_util']):>12} | "
            f"{format_percent(row['checkout_util']):>13} | "
            f"{format_percent(row['worst_util']):>5} | "
            f"{row['bottleneck_direction']:9} | {row['status']}"
        )


def save_csv(results: List[dict], out_path: Path, scenario: str) -> None:
    fieldnames = [
        "scenario", "day", "shift", "lot_id", "incoming", "outgoing",
        "parking_capacity", "checkin_capacity", "checkout_capacity",
        "checkin_util", "checkout_util", "worst_util",
        "bottleneck_direction", "status",
    ]
    with Path(out_path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({"scenario": scenario, **row})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Digital Twin Lite - student/visit-based parking flow simulation"
    )
    parser.add_argument("--scenario", choices=["Normal", "Worst", "Both"], default="Normal")
    parser.add_argument("--include-events", action="store_true")
    parser.add_argument("--full-grid", action="store_true")
    parser.add_argument("--only-alerts", action="store_true", help="Chi in PEAK/BOTTLENECK")
    parser.add_argument("--explain", action="store_true", help="In top contributor cua cac diem canh bao")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--dataset-dir", default=str(DATASET_DIR))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    data = load_dataset(Path(args.dataset_dir))
    scenarios = ["Normal", "Worst"] if args.scenario == "Both" else [args.scenario]
    all_rows = []

    for scenario in scenarios:
        results, contributors = run_simulation_with_contributors(
            data,
            scenario=scenario,
            include_events=args.include_events,
            full_grid=args.full_grid,
        )
        shown = (
            [row for row in results if row["status"] in ("PEAK", "BOTTLENECK")]
            if args.only_alerts else results
        )
        print_report(shown, scenario, data["schedule"])

        if args.explain:
            for row in shown:
                direction = row["bottleneck_direction"]
                directions = ["checkin", "checkout"] if direction == "Both" else [direction.lower()]
                for flow_direction in directions:
                    print(
                        f"\nContributors {row['day']} {row['shift']} {row['lot_id']} "
                        f"{flow_direction}:"
                    )
                    for source_id, flow in get_top_contributors(
                        contributors,
                        row["day"], row["shift"], row["lot_id"], flow_direction, args.top_n,
                    ):
                        print(f"  {source_id}: {flow:.1f} xe")
        all_rows.extend({"scenario": scenario, **row} for row in shown)
        print()

    if args.out:
        # When Both is requested, write directly with all scenario-tagged rows.
        out_path = Path(args.out)
        fieldnames = list(all_rows[0].keys()) if all_rows else []
        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"Da luu: {out_path}")


if __name__ == "__main__":
    main()
