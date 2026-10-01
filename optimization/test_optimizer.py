from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from optimizer import calculate_metrics, objective_tuple
from se_bridge import SimulationEngineerBridge

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--se-file", default=str(PROJECT_ROOT / "run_simulation.py"))
    parser.add_argument("--dataset-dir", default=str(PROJECT_ROOT / "Dataset"))
    parser.add_argument("--output-dir", default=str(HERE / "output"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    payload_path = output_dir / "recommendations.json"
    schedule_path = output_dir / "optimized_schedule.csv"
    if not payload_path.exists() or not schedule_path.exists():
        print("Run optimization/run_optimizer.py first")
        return 1

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    optimized = read_csv(schedule_path)
    scenario = payload.get("scenario", "Normal")
    include_events = bool(payload.get("include_events", False))

    bridge = SimulationEngineerBridge(args.se_file)
    data = bridge.load_dataset(args.dataset_dir)
    baseline = data["schedule"]
    baseline_results, _ = bridge.simulate(data, baseline, scenario, include_events)
    final_results, _ = bridge.simulate(data, optimized, scenario, include_events)
    before_metrics = calculate_metrics(baseline, baseline_results)
    after_metrics = calculate_metrics(optimized, final_results)

    checks = []
    def check(name, condition):
        checks.append((name, bool(condition)))

    before_by_id = {r["schedule_id"]: r for r in baseline}
    after_by_id = {r["schedule_id"]: r for r in optimized}
    check("Same number of schedule rows", len(baseline) == len(optimized))
    check("schedule_id set unchanged", set(before_by_id) == set(after_by_id))

    changed = [sid for sid in before_by_id if any(
        before_by_id[sid][field] != after_by_id[sid][field]
        for field in ("day_of_week", "shift", "room_id")
    )]
    move_ids = [m["schedule_id"] for m in payload["moves"]]
    check("Changed sessions match move history", set(changed) == set(move_ids))
    check("Each moved session is unique", len(move_ids) == len(set(move_ids)))

    room_counter = Counter((r["day_of_week"], r["shift"], r["room_id"]) for r in optimized)
    check("No room conflict", all(v == 1 for v in room_counter.values()))

    class_counter = Counter((r["class_id"], r["day_of_week"], r["shift"]) for r in optimized)
    check("No same-class same-slot conflict", all(v == 1 for v in class_counter.values()))

    rooms = {r["room_id"]: r for r in data["rooms"]}
    check("All rooms exist and have enough capacity", all(
        r["room_id"] in rooms and int(rooms[r["room_id"]]["room_capacity"]) >= int(r["num_students"])
        for r in optimized
    ))

    # Student timetable conflicts after optimizer.
    class_slots = defaultdict(list)
    for row in optimized:
        class_slots[row["class_id"]].append((row["day_of_week"], row["shift"]))
    student_slots = Counter()
    for enrollment in data["enrollments"]:
        for day, shift in class_slots.get(enrollment["class_id"], []):
            student_slots[(enrollment["student_id"], day, shift)] += 1
    check("No student timetable conflict", all(v == 1 for v in student_slots.values()))

    if payload["moves_applied"] > 0:
        check("Final objective improves", objective_tuple(after_metrics) < objective_tuple(before_metrics))
    else:
        check("No-move result does not worsen objective", objective_tuple(after_metrics) <= objective_tuple(before_metrics))

    saved = payload["after"]
    check("Saved metrics match fresh SE simulation",
          abs(saved["total_overload_excess"] - after_metrics["total_overload_excess"]) < 1e-8
          and abs(saved["max_worst_util"] - after_metrics["max_worst_util"]) < 1e-8
          and int(saved["bottleneck_points"]) == int(after_metrics["bottleneck_points"]))

    print(f"OPTIMIZER TEST | scenario={scenario} | include_events={include_events}")
    print("-" * 72)
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL':4} | {name}")
    print("-" * 72)
    ok = all(value for _, value in checks)
    print("STATUS:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
