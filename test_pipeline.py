from __future__ import annotations

import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OPT_DIR = ROOT / "optimization"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OPT_DIR))

from run_simulation import (  # noqa: E402
    load_dataset,
    run_simulation_from_dataset,
    compute_visits,
    load_leave_ratio_table,
)
from dashboard_data import build_dashboard_payload, build_optimization_payload  # noqa: E402
from optimization.se_bridge import SimulationEngineerBridge  # noqa: E402
import optimization.optimizer as opt  # noqa: E402


def test_dataset_validator():
    proc = subprocess.run(
        [sys.executable, "validate_data.py"],
        cwd=ROOT / "Dataset",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "STATUS: PASS" in proc.stdout
    print("[PASS] dataset validator")


def test_student_visit_logic():
    data = load_dataset(ROOT / "Dataset")
    table = load_leave_ratio_table(data["student_behavior"])

    consecutive = compute_visits([1, 2, 3], 1.0, "near", table)
    assert len(consecutive) == 1
    assert consecutive[0]["checkin_shift_idx"] == 1
    assert consecutive[0]["checkout_shift_idx"] == 3
    assert abs(consecutive[0]["weight"] - 1.0) < 1e-9

    one_gap = compute_visits([0, 2], 1.0, "near", table)
    # near gap_1 = 0.60: 40% stays, 60% leaves/re-enters.
    total = sum(v["weight"] for v in one_gap)
    assert abs(total - 1.6) < 1e-9  # one initial visit + 0.6 extra re-entry visit
    print("[PASS] student visit logic (consecutive stay + gap split)")


def test_simulation_schema_and_four_lots():
    data = load_dataset(ROOT / "Dataset")
    results = run_simulation_from_dataset(data, scenario="Normal", include_events=False, full_grid=True)
    required = {
        "day", "shift", "lot_id", "incoming", "outgoing",
        "parking_capacity", "checkin_capacity", "checkout_capacity",
        "checkin_util", "checkout_util", "worst_util",
        "bottleneck_direction", "status",
    }
    assert results and required <= set(results[0])
    assert {r["lot_id"] for r in results} == {"P1", "P2", "P3", "P4"}
    assert all(abs(r["worst_util"] - max(r["checkin_util"], r["checkout_util"])) < 1e-9 for r in results)
    print(f"[PASS] simulation schema + P1-P4 ({len(results)} rows)")


def test_event_toggle_changes_simulation():
    data = load_dataset(ROOT / "Dataset")
    off = run_simulation_from_dataset(data, scenario="Normal", include_events=False, full_grid=True)
    on = run_simulation_from_dataset(data, scenario="Normal", include_events=True, full_grid=True)
    off_map = {(r["day"], r["shift"], r["lot_id"]): (r["incoming"], r["outgoing"]) for r in off}
    on_map = {(r["day"], r["shift"], r["lot_id"]): (r["incoming"], r["outgoing"]) for r in on}
    assert any(on_map[key] != off_map.get(key) for key in on_map)
    print("[PASS] include_events changes flow")


def _student_conflicts(schedule, enrollments):
    class_slots = defaultdict(list)
    for row in schedule:
        class_slots[row["class_id"]].append((row["day_of_week"], row["shift"]))
    counter = Counter()
    for e in enrollments:
        for day, shift in class_slots.get(e["class_id"], []):
            counter[(e["student_id"], day, shift)] += 1
    return [key for key, value in counter.items() if value > 1]


def test_optimizer_integration():
    data = load_dataset(ROOT / "Dataset")
    payload = build_optimization_payload(
        scenario="Normal",
        include_events=False,
        max_moves=1,
        data=data,
    )
    assert payload["available"], payload.get("reason")
    assert "before" in payload and "after" in payload and "moves" in payload
    assert payload["after"]["metrics"]["total_overload_excess"] <= payload["before"]["metrics"]["total_overload_excess"]
    print(f"[PASS] optimizer integration ({payload['moves_applied']} move)")



def test_optimizer_with_events_enabled():
    """Regression test: fixed events must not block schedule optimization.

    The largest bottleneck can be event-heavy. OE must filter immutable event
    contributors and still find movable timetable sessions when they exist.
    """
    data = load_dataset(ROOT / "Dataset")
    payload = build_optimization_payload(
        scenario="Normal",
        include_events=True,
        max_moves=1,
        data=data,
    )
    assert payload["available"], payload.get("reason")
    assert payload["moves_applied"] >= 1, payload.get("stop_reason")
    assert (
        payload["after"]["metrics"]["total_overload_excess"]
        < payload["before"]["metrics"]["total_overload_excess"]
    )
    print("[PASS] optimizer works with Events ON (event contributors do not block MOVE search)")


def test_fast_candidate_matches_full_se():
    """Fast delta evaluator must match a full official SE candidate simulation."""
    data = load_dataset(ROOT / "Dataset")
    bridge = SimulationEngineerBridge(ROOT / "run_simulation.py")
    schedule = data["schedule"]
    context = bridge.prepare_fast_context(data, schedule, "Normal", True)
    move_context = opt._prepare_move_context(schedule, data["rooms"], data["enrollments"])
    index = {row["schedule_id"]: i for i, row in enumerate(schedule)}

    checked = 0
    for source in schedule:
        affected = move_context["class_students"].get(source["class_id"], set())
        if not affected:
            continue
        targets = opt._feasible_targets(schedule, source, move_context, 1)
        if not targets:
            continue
        day, shift, room = targets[0]
        candidate = opt._build_candidate_schedule(
            schedule, index[source["schedule_id"]], source, day, shift, room,
            move_context["templates"]
        )
        fast = bridge.evaluate_candidate_fast(
            context, data, schedule, candidate, affected, source["class_id"]
        )
        full, _ = bridge.simulate(data, candidate, "Normal", True)
        fast_map = {(r["day"], r["shift"], r["lot_id"]): r for r in fast}
        full_map = {(r["day"], r["shift"], r["lot_id"]): r for r in full}
        for key in set(fast_map) | set(full_map):
            a = fast_map.get(key, {})
            b = full_map.get(key, {})
            for field in ("incoming", "outgoing", "worst_util"):
                assert abs(float(a.get(field, 0.0)) - float(b.get(field, 0.0))) < 1e-8
        checked += 1
        if checked >= 3:
            break

    assert checked == 3
    print("[PASS] fast candidate delta matches full official SE")


def test_global_optimizer_search():
    data = load_dataset(ROOT / "Dataset")
    payload = build_optimization_payload(
        scenario="Normal", include_events=True, max_moves=3, data=data
    )
    assert payload["available"], payload.get("reason")
    assert payload["moves_applied"] >= 1
    # Each applied move stores how broadly that iteration searched.
    assert all(move.get("bottlenecks_considered", 0) >= 2 for move in payload["moves"])
    assert all(move.get("sources_considered", 0) >= 2 for move in payload["moves"])
    print("[PASS] optimizer considers multiple bottlenecks/sources globally")


def test_dashboard_payload():
    data = load_dataset(ROOT / "Dataset")
    payload = build_dashboard_payload("Normal", False, data=data)
    assert payload["simulation_model"] == "student-visit-based"
    assert payload["kpi"]["total_students"] == len(data["students"])
    assert len(payload["parking_view"]) == 6 * 5 * 4
    print("[PASS] dashboard payload uses student-based simulation")


if __name__ == "__main__":
    tests = [
        test_dataset_validator,
        test_student_visit_logic,
        test_simulation_schema_and_four_lots,
        test_event_toggle_changes_simulation,
        test_optimizer_integration,
        test_optimizer_with_events_enabled,
        test_fast_candidate_matches_full_se,
        test_global_optimizer_search,
        test_dashboard_payload,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {test.__name__}: {exc}")
    print(f"\n{len(tests)-failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
