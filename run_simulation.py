from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# CAU HINH CHUNG

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "Dataset"

BUILDINGS = ["A2", "B", "C", "D"]
PARKING_LOTS = ["P1", "P2", "P3", "P4"]

DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

SHIFT_ORDER = ["Ca1", "Ca2", "Ca3", "Ca4"]

# 5 khung giao ca
SLOT_ORDER = ["S0", "S1", "S2", "S3", "S4"]

SLOT_LABELS = {
    "S0": "Truoc Ca1",
    "S1": "Giao Ca1-Ca2",
    "S2": "Giao Ca2-Ca3",
    "S3": "Giao Ca3-Ca4",
    "S4": "Sau Ca4",
}

# NGUONG PHAN LOAI

PEAK_THRESHOLD = 0.90        # >= 90%  va <= 100%  -> PEAK
BOTTLENECK_THRESHOLD = 1.0   # > 100%              -> BOTTLENECK

# Sai so khi so sanh 2 so thuc
EPS = 1e-9

# DOC CSV

def read_csv(path: Path) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Khong tim thay file: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_dataset(dataset_dir: Path = DATASET_DIR) -> Dict[str, List[dict]]:
    return {
        "classes": read_csv(dataset_dir / "classes.csv"),
        "schedule": read_csv(dataset_dir / "schedule.csv"),
        "rooms": read_csv(dataset_dir / "rooms.csv"),
        "parking": read_csv(dataset_dir / "parking.csv"),
        "events": read_csv(dataset_dir / "events.csv"),
    }

# VALIDATE DATASET

def validate_dataset(data: Dict[str, List[dict]]) -> None:
    schedule = data["schedule"]
    if not schedule:
        raise ValueError("schedule.csv khong co du lieu.")

    schedule_required = [
        "schedule_id", "class_id",
        "day_of_week", "day_vn", "shift",
        "building", "num_students", "motorbike_ratio",
        # Bat buoc cho Tuan 5
        "arrival_window_start", "arrival_window_end",
        "departure_window_start", "departure_window_end",
    ]
    for column in schedule_required:
        if column not in schedule[0]:
            raise ValueError(f"schedule.csv thieu cot: {column}")

    parking = data["parking"]
    if not parking:
        raise ValueError("parking.csv khong co du lieu.")

    parking_required = [
        "scenario", "parking_lot_id", "capacity_slots",
        "max_checkin_throughput_veh_per_30min",
        "max_checkout_throughput_veh_per_30min",
        "dist_from_A2_m", "dist_from_B_m",
        "dist_from_C_m", "dist_from_D_m",
    ]
    for column in parking_required:
        if column not in parking[0]:
            raise ValueError(f"parking.csv thieu cot: {column}")

    events = data["events"]
    if events:
        event_required = [
            "event_id", "day_of_week", "day_vn",
            "shift", "building", "num_students", "motorbike_ratio",
        ]
        for column in event_required:
            if column not in events[0]:
                raise ValueError(f"events.csv thieu cot: {column}")

# TIME HELPER

def time_to_minutes(time_string: str) -> int:
    hour, minute = time_string.strip().split(":")
    return int(hour) * 60 + int(minute)

# SLOT MAP  (neo theo CA, doc thoi gian tu WINDOW THUC TE)

def build_window_slot_map(
    schedule: List[dict],
) -> Tuple[
    Dict[Tuple[str, str, str], str],   
    Dict[str, Tuple[str, str]],        
]:

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
            raise ValueError(f"schedule.csv co shift khong hop le: {shift}")

        shift_index = SHIFT_ORDER.index(shift)

        # ARRIVAL -> S(N-1)
        a_start = row["arrival_window_start"].strip()
        a_end = row["arrival_window_end"].strip()
        a_slot = SLOT_ORDER[shift_index]

        a_key = ("arrival", a_start, a_end)
        if window_slot_map.get(a_key, a_slot) != a_slot:
            raise ValueError(
                f"Arrival window {a_start}-{a_end} bi dung cho nhieu ca khac nhau."
            )
        window_slot_map[a_key] = a_slot
        register(a_slot, a_start, a_end)

        # DEPARTURE -> S(N)
        d_start = row["departure_window_start"].strip()
        d_end = row["departure_window_end"].strip()
        d_slot = SLOT_ORDER[shift_index + 1]

        d_key = ("departure", d_start, d_end)
        if window_slot_map.get(d_key, d_slot) != d_slot:
            raise ValueError(
                f"Departure window {d_start}-{d_end} bi dung cho nhieu ca khac nhau."
            )
        window_slot_map[d_key] = d_slot
        register(d_slot, d_start, d_end)

    bounds = {
        slot: (value[0], value[1])
        for slot, value in slot_bounds.items()
    }
    return window_slot_map, bounds


def build_event_shift_slot_map() -> Dict[str, Tuple[str, str]]:
    return {
        shift: (SLOT_ORDER[index], SLOT_ORDER[index + 1])
        for index, shift in enumerate(SHIFT_ORDER)
    }

# BUILD BUILDING FLOWS  (INCOMING / OUTGOING DOC LAP)

def build_building_flows(
    schedule: List[dict],
    events: List[dict],
    include_events: bool = True,
) -> Tuple[
    Dict[Tuple[str, str, str], float],
    Dict[Tuple[str, str, str], float],
    Dict[str, Tuple[str, str]],
]:
    incoming: Dict[Tuple[str, str, str], float] = defaultdict(float)
    outgoing: Dict[Tuple[str, str, str], float] = defaultdict(float)

    window_slot_map, slot_bounds = build_window_slot_map(schedule)

    # SCHEDULE
    for row in schedule:
        day = row["day_of_week"]
        building = row["building"]

        vehicles = (
            float(row["num_students"])
            * float(row["motorbike_ratio"])
        )

        # INCOMING theo arrival_window
        arrival_key = (
            "arrival",
            row["arrival_window_start"].strip(),
            row["arrival_window_end"].strip(),
        )
        if arrival_key not in window_slot_map:
            raise ValueError(f"Khong tim thay slot cho arrival window {arrival_key[1]}-{arrival_key[2]}")
        incoming[(day, window_slot_map[arrival_key], building)] += vehicles

        # OUTGOING theo departure_window
        departure_key = (
            "departure",
            row["departure_window_start"].strip(),
            row["departure_window_end"].strip(),
        )
        if departure_key not in window_slot_map:
            raise ValueError(f"Khong tim thay slot cho departure window {departure_key[1]}-{departure_key[2]}")
        outgoing[(day, window_slot_map[departure_key], building)] += vehicles

    # EVENTS (bat/tat duoc)
    if include_events:
        event_shift_slot_map = build_event_shift_slot_map()

        for row in events:
            shift = row["shift"]
            if shift not in event_shift_slot_map:
                raise ValueError(f"Event co shift khong hop le: {shift}")

            day = row["day_of_week"]
            building = row["building"]

            vehicles = (
                float(row["num_students"])
                * float(row["motorbike_ratio"])
            )

            arrival_slot, departure_slot = event_shift_slot_map[shift]
            incoming[(day, arrival_slot, building)] += vehicles
            outgoing[(day, departure_slot, building)] += vehicles

    return incoming, outgoing, slot_bounds

# DISTANCE WEIGHTS

def get_distance_weights(
    parking_rows: List[dict],
    scenario: str,
) -> Dict[str, Dict[str, float]]:

    lots = [row for row in parking_rows if row["scenario"] == scenario]

    if len(lots) != len(PARKING_LOTS):
        raise ValueError(
            f"Khong tim thay du {len(PARKING_LOTS)} bai xe cho scenario '{scenario}'."
        )

    weights: Dict[str, Dict[str, float]] = {}

    for building in BUILDINGS:
        column = f"dist_from_{building}_m"
        inverse_distances = {}

        for lot in lots:
            distance = float(lot[column])
            if distance <= 0:
                raise ValueError(
                    f"Khoang cach khong hop le ({column}={distance}) "
                    f"cho bai {lot['parking_lot_id']}"
                )
            inverse_distances[lot["parking_lot_id"]] = 1.0 / distance

        total = sum(inverse_distances.values())
        weights[building] = {
            parking_id: inverse_distance / total
            for parking_id, inverse_distance in inverse_distances.items()
        }

    return weights

# PHAN BO LUONG VE P1-P4

def distribute_flows_to_lots(
    incoming_demand: Dict[Tuple[str, str, str], float],
    outgoing_demand: Dict[Tuple[str, str, str], float],
    distance_weights: Dict[str, Dict[str, float]],
) -> Tuple[
    Dict[Tuple[str, str, str], float],
    Dict[Tuple[str, str, str], float],
]:

    def distribute(demand):
        lot_demand = defaultdict(float)
        for (day, slot, building), total_motorbikes in demand.items():
            for parking_id, weight in distance_weights[building].items():
                lot_demand[(day, slot, parking_id)] += total_motorbikes * weight
        return lot_demand

    return distribute(incoming_demand), distribute(outgoing_demand)

# THROUGHPUT

def get_gate_capacity(
    parking_rows: List[dict],
    scenario: str,
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

# STATUS / DIRECTION

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

# CORE SIMULATION

def run_simulation_from_data(
    schedule: List[dict],
    events: List[dict],
    parking: List[dict],
    scenario: str = "Normal",
    include_events: bool = True,
    full_grid: bool = False,
) -> List[dict]:

    if scenario not in ("Normal", "Worst"):
        raise ValueError("scenario phai la 'Normal' hoac 'Worst'")

    # 1. INCOMING / OUTGOING DOC LAP
    incoming_demand, outgoing_demand, slot_bounds = build_building_flows(
        schedule, events, include_events=include_events
    )

    # 2. PHAN BO VE P1-P4
    distance_weights = get_distance_weights(parking, scenario)
    incoming_lot, outgoing_lot = distribute_flows_to_lots(
        incoming_demand, outgoing_demand, distance_weights
    )

    # 3. THROUGHPUT
    gate_capacity = get_gate_capacity(parking, scenario)

    # 4. TAP KEY
    if full_grid:
        days = sorted(
            {key[0] for key in incoming_demand} | {key[0] for key in outgoing_demand},
            key=lambda d: DAY_ORDER.index(d) if d in DAY_ORDER else 99,
        )
        all_keys = {
            (day, slot, lot_id)
            for day in days
            for slot in SLOT_ORDER
            for lot_id in PARKING_LOTS
        }
    else:
        all_keys = set(incoming_lot) | set(outgoing_lot)

    # 5. UTILIZATION
    results: List[dict] = []

    for day, slot, lot_id in all_keys:
        if lot_id not in gate_capacity:
            raise ValueError(f"Khong co throughput cho bai {lot_id}")

        capacity = gate_capacity[lot_id]
        checkin_capacity = capacity["checkin"]
        checkout_capacity = capacity["checkout"]

        incoming = incoming_lot.get((day, slot, lot_id), 0.0)
        outgoing = outgoing_lot.get((day, slot, lot_id), 0.0)

        # checkin_util  = incoming / max_checkin_throughput_veh_per_30min
        checkin_util = (
            incoming / checkin_capacity if checkin_capacity > 0
            else (float("inf") if incoming > 0 else 0.0)
        )

        # checkout_util = outgoing / max_checkout_throughput_veh_per_30min
        checkout_util = (
            outgoing / checkout_capacity if checkout_capacity > 0
            else (float("inf") if outgoing > 0 else 0.0)
        )

        worst_util = max(checkin_util, checkout_util)

        results.append({
            "day": day,
            "slot": slot,
            "slot_window": slot_bounds.get(slot),
            "lot_id": lot_id,
            "incoming": incoming,
            "outgoing": outgoing,
            "checkin_capacity": checkin_capacity,
            "checkout_capacity": checkout_capacity,
            "checkin_util": checkin_util,
            "checkout_util": checkout_util,
            "worst_util": worst_util,
            "bottleneck_direction": get_bottleneck_direction(checkin_util, checkout_util),
            "status": classify_status(worst_util),
        })

    results.sort(key=_sort_key)
    return results


def run_simulation(
    dataset_dir: Path = DATASET_DIR,
    scenario: str = "Normal",
    include_events: bool = True,
    full_grid: bool = False,
) -> List[dict]:
    data = load_dataset(dataset_dir)
    validate_dataset(data)
    return run_simulation_from_data(
        schedule=data["schedule"],
        events=data["events"],
        parking=data["parking"],
        scenario=scenario,
        include_events=include_events,
        full_grid=full_grid,
    )

# WHAT-IF

def make_what_if_schedule(
    schedule: List[dict],
    day_of_week: str,
    shift: str,
    building: Optional[str] = None,
    remove_class_ids: Optional[Iterable[str]] = None,
    add_rows: Optional[List[dict]] = None,
) -> List[dict]:
    remove_ids = set(remove_class_ids or [])

    def should_drop(row: dict) -> bool:
        if row["day_of_week"] != day_of_week:
            return False
        if row["shift"] != shift:
            return False
        if building is not None and row["building"] != building:
            return False
        return row["class_id"] in remove_ids

    new_schedule = [dict(row) for row in schedule if not should_drop(row)]

    if add_rows:
        new_schedule.extend(dict(row) for row in add_rows)

    return new_schedule


def move_classes(
    schedule: List[dict],
    from_day: str,
    to_day: str,
    shift: Optional[str] = None,
    building: Optional[str] = None,
    n_classes: int = 1,
    seed: int = 42,
) -> Tuple[List[dict], List[str]]:

    if to_day not in DAY_ORDER:
        raise ValueError(f"to_day khong hop le: {to_day}")

    candidates = [
        row for row in schedule
        if row["day_of_week"] == from_day
        and (shift is None or row["shift"] == shift)
        and (building is None or row["building"] == building)
    ]

    # Uu tien chuyen lop dong nhat -> giam tai nhanh nhat, va deterministic
    candidates.sort(
        key=lambda r: (
            -float(r["num_students"]) * float(r["motorbike_ratio"]),
            r["schedule_id"],
        )
    )
    chosen = {row["schedule_id"] for row in candidates[:n_classes]}

    day_vn_map = {
        "Mon": "Thu 2", "Tue": "Thu 3", "Wed": "Thu 4", "Thu": "Thu 5",
        "Fri": "Thu 6", "Sat": "Thu 7", "Sun": "Chu nhat",
    }

    new_schedule = []
    for row in schedule:
        new_row = dict(row)
        if new_row["schedule_id"] in chosen:
            new_row["day_of_week"] = to_day
            new_row["day_vn"] = day_vn_map.get(to_day, to_day)
        new_schedule.append(new_row)

    return new_schedule, sorted(chosen)


def run_scenarios(
    schedules: Dict[str, List[dict]],
    events: List[dict],
    parking: List[dict],
    scenario: str = "Normal",
    include_events: bool = True,
) -> Dict[str, List[dict]]:
    return {
        name: run_simulation_from_data(sched, events, parking, scenario, include_events)
        for name, sched in schedules.items()
    }


def compare_before_after(
    schedule_before: List[dict],
    schedule_after: List[dict],
    events: List[dict],
    parking: List[dict],
    scenario: str = "Normal",
    include_events: bool = True,
) -> Tuple[List[dict], List[dict]]:
    results = run_scenarios(
        {"before": schedule_before, "after": schedule_after},
        events, parking, scenario, include_events,
    )
    return results["before"], results["after"]

# SORT

def _sort_key(row: dict):
    day_idx = DAY_ORDER.index(row["day"]) if row["day"] in DAY_ORDER else 99
    slot_idx = SLOT_ORDER.index(row["slot"]) if row["slot"] in SLOT_ORDER else 99
    lot_idx = PARKING_LOTS.index(row["lot_id"]) if row["lot_id"] in PARKING_LOTS else 99
    return (day_idx, slot_idx, lot_idx)

# OUTPUT

COLUMN_HEADERS = [
    "Day",
    "Shift",
    "Parking Lot",
    "Incoming",
    "Outgoing",
    "Checkin Throughput",
    "Checkout Throughput",
    "Checkin Util",
    "Checkout Util",
    "Worst Util",
    "Bottleneck Direction",
    "Status",
]

COLUMN_ALIGN = ["<", "<", "<", ">", ">", ">", ">", ">", ">", ">", "<", "<"]


def format_shift(result: dict) -> str:
    slot = result["slot"]
    label = SLOT_LABELS.get(slot, slot)
    window = result.get("slot_window")
    if window:
        return f"{slot} {label} ({window[0]}-{window[1]})"
    return f"{slot} {label}"


def format_percent(value: float) -> str:
    if value == float("inf"):
        return "INF"
    return f"{value * 100:.0f}%"


def result_to_row(result: dict) -> List[str]:
    return [
        result["day"],
        format_shift(result),
        result["lot_id"],
        str(round(result["incoming"])),
        str(round(result["outgoing"])),
        str(result["checkin_capacity"]),
        str(result["checkout_capacity"]),
        format_percent(result["checkin_util"]),
        format_percent(result["checkout_util"]),
        format_percent(result["worst_util"]),
        result["bottleneck_direction"],
        result["status"],
    ]


def render_table(headers: List[str], rows: List[List[str]]) -> List[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for i in range(len(headers)):
            widths[i] = max(widths[i], len(row[i]))

    def fmt_row(cells):
        return " | ".join(
            f"{cell:{COLUMN_ALIGN[i]}{widths[i]}}"
            for i, cell in enumerate(cells)
        )

    lines = [fmt_row(headers), "-+-".join("-" * w for w in widths)]
    lines.extend(fmt_row(row) for row in rows)
    return lines


def print_report(results: List[dict], scenario: str, title: str = "Output") -> None:
    print(title)
    print(f"(Scenario: {scenario})")
    rows = [result_to_row(result) for result in results]
    for line in render_table(COLUMN_HEADERS, rows):
        print(line)

    n_bottleneck = sum(1 for r in results if r["status"] == "BOTTLENECK")
    n_peak = sum(1 for r in results if r["status"] == "PEAK")
    print(
        f"Tong: {len(results)} dong | "
        f"BOTTLENECK: {n_bottleneck} | PEAK: {n_peak} | "
        f"OK: {len(results) - n_bottleneck - n_peak}"
    )


def save_csv(rows: List[dict], out_path: Path) -> None:
    """rows: list ket qua da gan them khoa 'scenario'."""
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Kịch bản"] + COLUMN_HEADERS)
        for result in rows:
            writer.writerow([result["scenario"]] + result_to_row(result))

# CLI

def main():
    parser = argparse.ArgumentParser(
        description="Digital Twin Lite - Parking Lot Vehicle Flow Simulation"
    )
    parser.add_argument(
        "--scenario", choices=["Normal", "Worst", "Both"], default="Both",
        help="Kich ban van hanh nha xe (mac dinh: Both)",
    )
    parser.add_argument("--no-events", action="store_true", help="Bo qua events.csv")
    parser.add_argument("--dataset-dir", type=str, default=str(DATASET_DIR))
    parser.add_argument("--out", type=str, default=None, help="File CSV de luu ket qua")
    parser.add_argument("--only-bottleneck", action="store_true",
                        help="Chi in cac dong PEAK/BOTTLENECK")
    parser.add_argument("--full-grid", action="store_true",
                        help="In du 5 khung x 4 bai cho moi ngay, ke ca khi luong = 0")

    # What-if: chuyen lop tu ngay nay sang ngay khac
    parser.add_argument("--move-from", type=str, default=None)
    parser.add_argument("--move-to", type=str, default=None)
    parser.add_argument("--move-shift", type=str, default=None,
                        choices=SHIFT_ORDER)
    parser.add_argument("--move-building", type=str, default=None,
                        choices=BUILDINGS)
    parser.add_argument("--move-n", type=int, default=0,
                        help="So lop can chuyen")

    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir)
    data = load_dataset(dataset_dir)
    validate_dataset(data)

    scenarios = ["Normal", "Worst"] if args.scenario == "Both" else [args.scenario]
    include_events = not args.no_events

    do_whatif = bool(args.move_from and args.move_to and args.move_n > 0)

    all_results: List[dict] = []

    for scenario in scenarios:
        before = run_simulation_from_data(
            data["schedule"], data["events"], data["parking"],
            scenario, include_events, args.full_grid,
        )

        shown = (
            [r for r in before if r["status"] in ("PEAK", "BOTTLENECK")]
            if args.only_bottleneck else before
        )
        print_report(shown, scenario, "Output" if not do_whatif else "Output - BEFORE")
        print()

        all_results.extend({**r, "scenario": scenario} for r in shown)

        if do_whatif:
            new_schedule, moved = move_classes(
                data["schedule"],
                from_day=args.move_from,
                to_day=args.move_to,
                shift=args.move_shift,
                building=args.move_building,
                n_classes=args.move_n,
            )
            after = run_simulation_from_data(
                new_schedule, data["events"], data["parking"],
                scenario, include_events, args.full_grid,
            )
            shown_after = (
                [r for r in after if r["status"] in ("PEAK", "BOTTLENECK")]
                if args.only_bottleneck else after
            )
            print(f"Da chuyen {len(moved)} lop tu {args.move_from} sang {args.move_to}.")
            print_report(shown_after, scenario, "Output - AFTER")
            print()

            all_results.extend(
                {**r, "scenario": f"{scenario} (after)"} for r in shown_after
            )

    if args.out:
        out_path = Path(args.out)
        save_csv(all_results, out_path)
        print(f"Da luu ket qua vao: {out_path}")


if __name__ == "__main__":
    main()
