from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from optimizer import optimize_multi_move
from se_bridge import SimulationEngineerBridge

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen and not isinstance(row[key], (dict, list)):
                seen.add(key)
                keys.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in keys})


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Digital Twin Lite - multi-move optimizer")
    parser.add_argument("--se-file", default=str(PROJECT_ROOT / "run_simulation.py"))
    parser.add_argument("--dataset-dir", default=str(PROJECT_ROOT / "Dataset"))
    parser.add_argument("--scenario", choices=["Normal", "Worst"], default="Normal")
    parser.add_argument("--include-events", action="store_true")
    parser.add_argument("--max-moves", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--source-limit", type=int, default=12)
    parser.add_argument("--target-limit", type=int, default=12)
    parser.add_argument("--output-dir", default=str(HERE / "output"))
    args = parser.parse_args()

    bridge = SimulationEngineerBridge(args.se_file)
    data = bridge.load_dataset(args.dataset_dir)
    result = optimize_multi_move(
        bridge=bridge,
        data=data,
        scenario=args.scenario,
        include_events=args.include_events,
        max_moves=args.max_moves,
        top_k=args.top_k,
        source_limit=args.source_limit,
        target_limit=args.target_limit,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "baseline_simulation.csv", result.baseline_results)
    write_csv(output_dir / "final_simulation.csv", result.final_results)
    write_csv(output_dir / "optimized_schedule.csv", result.optimized_schedule)
    write_csv(output_dir / "optimization_history.csv", result.moves)
    write_csv(output_dir / "candidate_history.csv", result.candidate_history)
    write_csv(
        output_dir / "before_after_metrics.csv",
        [
            {"metric": key, "before": result.baseline_metrics[key], "after": result.final_metrics[key]}
            for key in result.baseline_metrics
        ],
    )
    write_csv(
        output_dir / "schedule_changes.csv",
        [
            {
                "move_no": move["move_no"],
                "schedule_id": move["schedule_id"],
                "class_id": move["class_id"],
                "students_affected": move["num_students"],
                "from_day": move["from_day"],
                "from_shift": move["from_shift"],
                "from_room": move["from_room"],
                "to_day": move["to_day"],
                "to_shift": move["to_shift"],
                "to_room": move["to_room"],
                "source_util_before": move["source_worst_util_before"],
                "source_util_after": move["source_worst_util_after"],
            }
            for move in result.moves
        ],
    )

    payload = {
        "scenario": result.scenario,
        "include_events": result.include_events,
        "max_moves": result.max_moves,
        "moves_applied": len(result.moves),
        "stop_reason": result.stop_reason,
        "total_evaluated_candidates": result.total_evaluated_candidates,
        "total_improving_candidates": result.total_improving_candidates,
        "before": result.baseline_metrics,
        "after": result.final_metrics,
        "moves": result.moves,
    }
    (output_dir / "recommendations.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("=" * 84)
    print("OPTIMIZATION RESULT")
    print("=" * 84)
    print(f"Scenario              : {result.scenario}")
    print(f"Include events        : {result.include_events}")
    print(f"Max moves             : {result.max_moves}")
    print(f"Moves applied         : {len(result.moves)}")
    print(f"Stop reason           : {result.stop_reason}")
    print(f"Candidates evaluated  : {result.total_evaluated_candidates}")
    print()

    if result.moves:
        print("MOVE HISTORY")
        for move in result.moves:
            print(
                f"  #{move['move_no']} {move['schedule_id']} | "
                f"{move['from_day']} {move['from_shift']} {move['from_room']} -> "
                f"{move['to_day']} {move['to_shift']} {move['to_room']}"
            )
            print(
                f"     {move['source_bottleneck_day']} {move['source_bottleneck_slot']} "
                f"{move['source_bottleneck_lot']} {move['source_bottleneck_direction']} | "
                f"{pct(move['source_worst_util_before'])} -> {pct(move['source_worst_util_after'])}"
            )
        print()

    before, after = result.baseline_metrics, result.final_metrics
    print("GLOBAL BEFORE -> AFTER")
    print(f"  Bottlenecks         : {before['bottleneck_points']} -> {after['bottleneck_points']}")
    print(f"  Peak points (info)  : {before['peak_points']} -> {after['peak_points']}")
    print(f"  Total overload      : {before['total_overload_excess']:.4f} -> {after['total_overload_excess']:.4f}")
    print(f"  Max worst util      : {pct(before['max_worst_util'])} -> {pct(after['max_worst_util'])}")
    print(f"  Daily load std      : {before['daily_load_std']:.2f} -> {after['daily_load_std']:.2f}")
    print(f"  Day/shift load std  : {before['day_shift_load_std']:.2f} -> {after['day_shift_load_std']:.2f}")
    print(f"Outputs               : {output_dir}")


if __name__ == "__main__":
    main()
