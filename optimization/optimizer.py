from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from statistics import pstdev
from typing import Dict, List, Optional, Tuple

from se_bridge import SimulationEngineerBridge

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
SHIFTS = ["Ca1", "Ca2", "Ca3", "Ca4"]
DAY_VN = {
    "Mon": "Thu 2", "Tue": "Thu 3", "Wed": "Thu 4",
    "Thu": "Thu 5", "Fri": "Thu 6", "Sat": "Thu 7",
}
TIME_FIELDS = [
    "start_time", "end_time", "arrival_window_start", "arrival_window_end",
    "departure_window_start", "departure_window_end",
]


@dataclass
class MultiMoveOptimizationResult:
    scenario: str
    include_events: bool
    max_moves: int
    stop_reason: str
    baseline_results: List[dict]
    final_results: List[dict]
    baseline_metrics: dict
    final_metrics: dict
    moves: List[dict]
    candidate_history: List[dict]
    optimized_schedule: List[dict]
    total_evaluated_candidates: int
    total_improving_candidates: int


def _f(value) -> float:
    return float(value)


def _i(value) -> int:
    return int(float(value))


def _shift_templates(schedule: List[dict]) -> Dict[str, dict]:
    templates = {}
    for row in schedule:
        templates.setdefault(row["shift"], {field: row[field] for field in TIME_FIELDS})
    missing = [shift for shift in SHIFTS if shift not in templates]
    if missing:
        raise ValueError(f"Missing shift templates: {missing}")
    return templates


def _occupied_rooms(schedule: List[dict]) -> Dict[Tuple[str, str], set]:
    occupied: Dict[Tuple[str, str], set] = {}
    for row in schedule:
        occupied.setdefault((row["day_of_week"], row["shift"]), set()).add(row["room_id"])
    return occupied


def _rooms_by_building(rooms: List[dict]) -> Dict[str, List[dict]]:
    result: Dict[str, List[dict]] = {}
    for room in rooms:
        result.setdefault(room["building"], []).append(room)
    for building in result:
        result[building].sort(key=lambda r: (_i(r["room_capacity"]), r["room_id"]))
    return result


def _find_free_room_fast(
    rooms_by_building: Dict[str, List[dict]],
    occupied: Dict[Tuple[str, str], set],
    building: str,
    day: str,
    shift: str,
    num_students: int,
) -> Optional[dict]:
    used = occupied.get((day, shift), set())
    for room in rooms_by_building.get(building, []):
        if _i(room["room_capacity"]) >= num_students and room["room_id"] not in used:
            return room
    return None


def _class_students(enrollments: List[dict]) -> Dict[str, set]:
    result: Dict[str, set] = {}
    for row in enrollments:
        result.setdefault(row["class_id"], set()).add(row["student_id"])
    return result


def _student_slot_owners(schedule: List[dict], enrollments: List[dict]) -> Dict[Tuple[str, str, str], set]:
    schedule_by_class: Dict[str, List[dict]] = {}
    for row in schedule:
        schedule_by_class.setdefault(row["class_id"], []).append(row)

    owners: Dict[Tuple[str, str, str], set] = {}
    for enrollment in enrollments:
        for session in schedule_by_class.get(enrollment["class_id"], []):
            key = (enrollment["student_id"], session["day_of_week"], session["shift"])
            owners.setdefault(key, set()).add(session["schedule_id"])
    return owners


def _student_conflict_for_move(
    source: dict,
    target_day: str,
    target_shift: str,
    class_students: Dict[str, set],
    student_slots: Dict[Tuple[str, str, str], set],
) -> bool:
    for student_id in class_students.get(source["class_id"], set()):
        owners = student_slots.get((student_id, target_day, target_shift), set())
        if any(schedule_id != source["schedule_id"] for schedule_id in owners):
            return True
    return False


def _prepare_move_context(schedule: List[dict], rooms: List[dict], enrollments: List[dict]) -> dict:
    return {
        "occupied": _occupied_rooms(schedule),
        "rooms_by_building": _rooms_by_building(rooms),
        "templates": _shift_templates(schedule),
        "class_students": _class_students(enrollments),
        "student_slots": _student_slot_owners(schedule, enrollments),
    }


def _build_candidate_schedule(
    schedule: List[dict],
    source_idx: int,
    source: dict,
    target_day: str,
    target_shift: str,
    room: dict,
    templates: Dict[str, dict],
) -> List[dict]:
    updated = [dict(row) for row in schedule]
    row = updated[source_idx]
    row["day_of_week"] = target_day
    row["day_vn"] = DAY_VN[target_day]
    row["shift"] = target_shift
    row["room_id"] = room["room_id"]
    row["building"] = room["building"]
    row["floor"] = str(room["floor"])
    row["room_capacity"] = str(room["room_capacity"])
    for field in TIME_FIELDS:
        row[field] = templates[target_shift][field]
    return updated


def _load_balance_metrics(schedule: List[dict]) -> Tuple[float, float]:
    daily = {day: 0.0 for day in DAYS}
    day_shift = {(day, shift): 0.0 for day in DAYS for shift in SHIFTS}
    for row in schedule:
        if row["day_of_week"] in daily and row["shift"] in SHIFTS:
            n = _f(row["num_students"])
            daily[row["day_of_week"]] += n
            day_shift[(row["day_of_week"], row["shift"])] += n
    return pstdev(daily.values()), pstdev(day_shift.values())


def calculate_metrics(schedule: List[dict], simulation_results: List[dict]) -> dict:
    worst_utils = [_f(row["worst_util"]) for row in simulation_results]
    daily_std, day_shift_std = _load_balance_metrics(schedule)
    return {
        "bottleneck_points": sum(row["status"] == "BOTTLENECK" for row in simulation_results),
        "peak_points": sum(row["status"] == "PEAK" for row in simulation_results),
        "total_overload_excess": sum(max(value - 1.0, 0.0) for value in worst_utils),
        "max_worst_util": max(worst_utils, default=0.0),
        "daily_load_std": daily_std,
        "day_shift_load_std": day_shift_std,
    }


def objective_tuple(metrics: dict) -> tuple:
    # Primary goal: remove BOTTLENECK points. Among candidates with the same
    # bottleneck count, reduce total overload and then the worst remaining point.
    # PEAK count is informational only: BOTTLENECK -> PEAK is an improvement.
    return (
        metrics["bottleneck_points"],
        metrics["total_overload_excess"],
        metrics["max_worst_util"],
        metrics["daily_load_std"],
        metrics["day_shift_load_std"],
    )


def _find_same_point(results: List[dict], point: dict) -> Optional[dict]:
    return next((
        row for row in results
        if row["day"] == point["day"]
        and row["shift"] == point["shift"]
        and row["lot_id"] == point["lot_id"]
    ), None)


def _contributors_for_point(
    contributors: dict,
    bottleneck: dict,
    schedule_by_id: Dict[str, dict],
) -> Dict[str, float]:
    direction = bottleneck["bottleneck_direction"]
    directions = ["checkin", "checkout"] if direction == "Both" else [direction.lower()]
    aggregate: Dict[str, float] = {}
    for flow_direction in directions:
        key = (bottleneck["day"], bottleneck["shift"], bottleneck["lot_id"], flow_direction)
        for source_id, flow in contributors.get(key, []):
            if source_id not in schedule_by_id:
                # Events affect simulation but cannot be moved by OE.
                continue
            aggregate[source_id] = aggregate.get(source_id, 0.0) + float(flow)
    return aggregate


def _collect_global_sources(
    contributors: dict,
    bottlenecks: List[dict],
    schedule_by_id: Dict[str, dict],
    locked_schedule_ids: set,
    source_limit: int,
) -> List[dict]:
    """Select sources across ALL current bottlenecks, not only the worst one.

    Selection is round-robin across bottlenecks first, so a very large bottleneck
    cannot hide all sessions from other days/slots. Remaining positions are filled
    by global pressure score. source_limit <= 0 means all movable contributors.
    """
    per_point = []
    source_info: Dict[str, dict] = {}

    for point_index, point in enumerate(bottlenecks):
        by_source = _contributors_for_point(contributors, point, schedule_by_id)
        ranked = sorted(by_source.items(), key=lambda item: (-item[1], item[0]))
        point_rows = []
        severity = max(float(point["worst_util"]) - 1.0, 0.0)
        for schedule_id, contribution in ranked:
            if schedule_id in locked_schedule_ids:
                continue
            weighted = contribution * (1.0 + severity)
            point_rows.append((schedule_id, contribution, weighted))
            info = source_info.setdefault(schedule_id, {
                "source": schedule_by_id[schedule_id],
                "pressure_score": 0.0,
                "total_contribution": 0.0,
                "reasons": [],
            })
            info["pressure_score"] += weighted
            info["total_contribution"] += contribution
            info["reasons"].append({
                "point": point,
                "contribution": contribution,
                "weighted": weighted,
            })
        per_point.append(point_rows)

    if not source_info:
        return []

    if source_limit <= 0 or source_limit >= len(source_info):
        selected_ids = list(source_info)
    else:
        selected_ids = []
        selected_set = set()
        depth = 0
        # First guarantee breadth across bottlenecks.
        while len(selected_ids) < source_limit:
            added = False
            for point_rows in per_point:
                if depth < len(point_rows):
                    schedule_id = point_rows[depth][0]
                    if schedule_id not in selected_set:
                        selected_ids.append(schedule_id)
                        selected_set.add(schedule_id)
                        added = True
                        if len(selected_ids) >= source_limit:
                            break
            if not added:
                break
            depth += 1

        # Fill any remaining slots by global pressure.
        if len(selected_ids) < source_limit:
            remaining = sorted(
                (sid for sid in source_info if sid not in selected_set),
                key=lambda sid: (-source_info[sid]["pressure_score"], sid),
            )
            selected_ids.extend(remaining[:source_limit - len(selected_ids)])

    selected = [source_info[sid] for sid in selected_ids]
    selected.sort(key=lambda item: (-item["pressure_score"], item["source"]["schedule_id"]))
    for item in selected:
        item["primary_reason"] = max(
            item["reasons"], key=lambda reason: (reason["weighted"], reason["contribution"])
        )
    return selected


def _feasible_targets(
    schedule: List[dict],
    source: dict,
    move_context: dict,
    target_limit: int,
) -> List[Tuple[str, str, dict]]:
    occupied = move_context["occupied"]
    rooms_by_building = move_context["rooms_by_building"]
    class_students = move_context["class_students"]
    student_slots = move_context["student_slots"]

    # Heuristic is used only for ordering. target_limit<=0 evaluates ALL feasible
    # day/shift targets. When a positive limit is supplied, we keep targets spread
    # across different days rather than selecting only the globally emptiest slots.
    load = {(day, shift): 0 for day in DAYS for shift in SHIFTS}
    daily = {day: 0 for day in DAYS}
    for row in schedule:
        if row["day_of_week"] in DAYS and row["shift"] in SHIFTS:
            n = _i(row["num_students"])
            load[(row["day_of_week"], row["shift"])] += n
            daily[row["day_of_week"]] += n

    by_day: Dict[str, List[Tuple[int, int, int, str, str, dict]]] = {day: [] for day in DAYS}
    for day in DAYS:
        for shift in SHIFTS:
            if day == source["day_of_week"] and shift == source["shift"]:
                continue
            if any(
                row["schedule_id"] != source["schedule_id"]
                and row["class_id"] == source["class_id"]
                and row["day_of_week"] == day
                and row["shift"] == shift
                for row in schedule
            ):
                continue
            if _student_conflict_for_move(source, day, shift, class_students, student_slots):
                continue
            room = _find_free_room_fast(
                rooms_by_building, occupied, source["building"], day, shift,
                _i(source["num_students"])
            )
            if room is None:
                continue
            by_day[day].append((
                load[(day, shift)], daily[day], SHIFTS.index(shift), day, shift, room
            ))

    for rows in by_day.values():
        rows.sort()

    all_targets = [item for day in DAYS for item in by_day[day]]
    if target_limit <= 0 or target_limit >= len(all_targets):
        all_targets.sort()
        return [(item[3], item[4], item[5]) for item in all_targets]

    selected = []
    depth = 0
    while len(selected) < target_limit:
        added = False
        for day in DAYS:
            rows = by_day[day]
            if depth < len(rows):
                selected.append(rows[depth])
                added = True
                if len(selected) >= target_limit:
                    break
        if not added:
            break
        depth += 1
    selected.sort()
    return [(item[3], item[4], item[5]) for item in selected]


def _best_single_step(
    bridge: SimulationEngineerBridge,
    data: Dict[str, List[dict]],
    schedule: List[dict],
    scenario: str,
    include_events: bool,
    move_no: int,
    locked_schedule_ids: set,
    source_limit: int,
    target_limit: int,
    top_k: int,
) -> dict:
    """One global greedy iteration.

    Differences from the previous optimizer:
    - considers ALL current bottlenecks together instead of stopping at the first;
    - source selection is spread across bottleneck days/slots;
    - all feasible target day/shift slots are considered by default;
    - candidate simulation uses exact affected-student flow deltas for speed;
    - the selected winner is verified with a full official SE run.
    """
    fast_context = bridge.prepare_fast_context(
        data, schedule, scenario=scenario, include_events=include_events
    )
    baseline_results = fast_context["results"]
    contributors = fast_context["contributors"]
    baseline_metrics = calculate_metrics(schedule, baseline_results)

    bottlenecks = sorted(
        [row for row in baseline_results if row["status"] == "BOTTLENECK"],
        key=lambda row: (-_f(row["worst_util"]), row["day"], row["shift"], row["lot_id"]),
    )
    if not bottlenecks:
        return {
            "status": "NO_BOTTLENECK",
            "baseline_results": baseline_results,
            "baseline_metrics": baseline_metrics,
            "evaluated_candidates": 0,
            "improving_candidates": 0,
            "top_candidates": [],
        }

    schedule_by_id = {row["schedule_id"]: row for row in schedule}
    schedule_index = {row["schedule_id"]: idx for idx, row in enumerate(schedule)}
    move_context = _prepare_move_context(schedule, data["rooms"], data["enrollments"])

    source_infos = _collect_global_sources(
        contributors,
        bottlenecks,
        schedule_by_id,
        locked_schedule_ids,
        source_limit,
    )
    if not source_infos:
        return {
            "status": "NO_MOVABLE_SOURCE",
            "baseline_results": baseline_results,
            "baseline_metrics": baseline_metrics,
            "bottleneck": bottlenecks[0],
            "evaluated_candidates": 0,
            "improving_candidates": 0,
            "top_candidates": [],
        }

    improving = []
    total_evaluated = 0

    for source_info in source_infos:
        source = source_info["source"]
        affected_students = move_context["class_students"].get(source["class_id"], set())
        if not affected_students:
            continue

        targets = _feasible_targets(schedule, source, move_context, target_limit)
        source_idx = schedule_index[source["schedule_id"]]

        for target_day, target_shift, room in targets:
            candidate_schedule = _build_candidate_schedule(
                schedule,
                source_idx,
                source,
                target_day,
                target_shift,
                room,
                move_context["templates"],
            )

            candidate_results = bridge.evaluate_candidate_fast(
                context=fast_context,
                data=data,
                baseline_schedule=schedule,
                candidate_schedule=candidate_schedule,
                affected_student_ids=affected_students,
                cache_key=source["class_id"],
            )
            candidate_metrics = calculate_metrics(candidate_schedule, candidate_results)
            total_evaluated += 1

            if objective_tuple(candidate_metrics) >= objective_tuple(baseline_metrics):
                continue

            reason = source_info["primary_reason"]["point"]
            record = {
                "move_no": move_no,
                "schedule_id": source["schedule_id"],
                "class_id": source["class_id"],
                "num_students": _i(source["num_students"]),
                "building": source["building"],
                "from_day": source["day_of_week"],
                "from_shift": source["shift"],
                "from_room": source["room_id"],
                "to_day": target_day,
                "to_shift": target_shift,
                "to_room": room["room_id"],
                "contribution_to_source_bottleneck": source_info["primary_reason"]["contribution"],
                "source_pressure_score": source_info["pressure_score"],
                "reason_day": reason["day"],
                "reason_slot": reason["shift"],
                "reason_lot": reason["lot_id"],
                "reason_direction": reason["bottleneck_direction"],
                **candidate_metrics,
            }
            improving.append({
                "record": record,
                "schedule": candidate_schedule,
                "fast_results": candidate_results,
                "metrics": candidate_metrics,
                "source": source,
                "room": room,
                "reason": reason,
            })

    if not improving:
        return {
            "status": "NO_IMPROVING_CANDIDATE",
            "baseline_results": baseline_results,
            "baseline_metrics": baseline_metrics,
            "bottleneck": bottlenecks[0],
            "evaluated_candidates": total_evaluated,
            "improving_candidates": 0,
            "top_candidates": [],
        }

    improving.sort(key=lambda item: (
        objective_tuple(item["metrics"]),
        item["record"]["schedule_id"],
        item["record"]["to_day"],
        item["record"]["to_shift"],
        item["record"]["to_room"],
    ))

    # Verify candidates in objective order until one is confirmed by a full SE run.
    # The delta evaluator is mathematically exact for affected student flows, but
    # this verification protects against future SE changes.
    verified_best = None
    verification_attempts = min(5, len(improving))
    for candidate in improving[:verification_attempts]:
        verified_results, _ = bridge.simulate(
            data,
            candidate["schedule"],
            scenario=scenario,
            include_events=include_events,
        )
        verified_metrics = calculate_metrics(candidate["schedule"], verified_results)
        if objective_tuple(verified_metrics) < objective_tuple(baseline_metrics):
            candidate["verified_results"] = verified_results
            candidate["verified_metrics"] = verified_metrics
            verified_best = candidate
            break

    if verified_best is None:
        return {
            "status": "NO_VERIFIED_IMPROVEMENT",
            "baseline_results": baseline_results,
            "baseline_metrics": baseline_metrics,
            "bottleneck": bottlenecks[0],
            "evaluated_candidates": total_evaluated,
            "improving_candidates": len(improving),
            "top_candidates": [item["record"] for item in improving[:top_k]],
        }

    best = verified_best
    reason = best["reason"]
    source_after = _find_same_point(best["verified_results"], reason)

    return {
        "status": "MOVE_FOUND",
        "baseline_results": baseline_results,
        "baseline_metrics": baseline_metrics,
        "bottleneck": reason,
        "candidate_schedule": best["schedule"],
        "candidate_results": best["verified_results"],
        "candidate_metrics": best["verified_metrics"],
        "source": best["source"],
        "target_room": best["room"],
        "source_bottleneck_after": source_after,
        "evaluated_candidates": total_evaluated,
        "improving_candidates": len(improving),
        "top_candidates": [item["record"] for item in improving[:top_k]],
        "bottlenecks_considered": len(bottlenecks),
        "sources_considered": len(source_infos),
    }


def optimize_multi_move(
    bridge: SimulationEngineerBridge,
    data: Dict[str, List[dict]],
    scenario: str = "Normal",
    include_events: bool = False,
    max_moves: int = 3,
    top_k: int = 10,
    source_limit: int = 12,
    target_limit: int = 12,
) -> MultiMoveOptimizationResult:
    if max_moves < 1:
        raise ValueError("max_moves must be >= 1")

    current_schedule = deepcopy(data["schedule"])
    baseline_results, _ = bridge.simulate(
        data, current_schedule, scenario=scenario, include_events=include_events
    )
    baseline_metrics = calculate_metrics(current_schedule, baseline_results)

    moves = []
    candidate_history = []
    locked_schedule_ids = set()
    total_evaluated = 0
    total_improving = 0
    stop_reason = "MAX_MOVES"

    for move_no in range(1, max_moves + 1):
        step = _best_single_step(
            bridge=bridge,
            data=data,
            schedule=current_schedule,
            scenario=scenario,
            include_events=include_events,
            move_no=move_no,
            locked_schedule_ids=locked_schedule_ids,
            source_limit=source_limit,
            target_limit=target_limit,
            top_k=top_k,
        )
        total_evaluated += step["evaluated_candidates"]
        total_improving += step["improving_candidates"]

        if step["status"] != "MOVE_FOUND":
            stop_reason = step["status"]
            break

        for rank, record in enumerate(step["top_candidates"], start=1):
            candidate_history.append({"move_no": move_no, "rank": rank, **record})

        source = step["source"]
        room = step["target_room"]
        point = step["bottleneck"]
        point_after = step["source_bottleneck_after"]
        before_metrics = step["baseline_metrics"]
        after_metrics = step["candidate_metrics"]
        # The verified winner may not be the first stored Top-K if verification
        # skipped a future-incompatible fast candidate. Derive target directly.
        moved_after = next(
            row for row in step["candidate_schedule"]
            if row["schedule_id"] == source["schedule_id"]
        )

        move_record = {
            "move_no": move_no,
            "schedule_id": source["schedule_id"],
            "class_id": source["class_id"],
            "num_students": _i(source["num_students"]),
            "building": source["building"],
            "from_day": source["day_of_week"],
            "from_shift": source["shift"],
            "from_room": source["room_id"],
            "to_day": moved_after["day_of_week"],
            "to_shift": moved_after["shift"],
            "to_room": moved_after["room_id"],
            "source_bottleneck_day": point["day"],
            "source_bottleneck_slot": point["shift"],
            "source_bottleneck_lot": point["lot_id"],
            "source_bottleneck_direction": point["bottleneck_direction"],
            "source_worst_util_before": _f(point["worst_util"]),
            "source_worst_util_after": _f(point_after["worst_util"]) if point_after else 0.0,
            "total_overload_before": before_metrics["total_overload_excess"],
            "total_overload_after": after_metrics["total_overload_excess"],
            "max_worst_util_before": before_metrics["max_worst_util"],
            "max_worst_util_after": after_metrics["max_worst_util"],
            "bottleneck_points_before": before_metrics["bottleneck_points"],
            "bottleneck_points_after": after_metrics["bottleneck_points"],
            "peak_points_before": before_metrics["peak_points"],
            "peak_points_after": after_metrics["peak_points"],
            "evaluated_candidates": step["evaluated_candidates"],
            "improving_candidates": step["improving_candidates"],
            "bottlenecks_considered": step.get("bottlenecks_considered", 0),
            "sources_considered": step.get("sources_considered", 0),
            "changed_fields": {
                "day_of_week": {"before": source["day_of_week"], "after": moved_after["day_of_week"]},
                "shift": {"before": source["shift"], "after": moved_after["shift"]},
                "room_id": {"before": source["room_id"], "after": moved_after["room_id"]},
            },
        }
        moves.append(move_record)
        current_schedule = step["candidate_schedule"]
        locked_schedule_ids.add(source["schedule_id"])

    final_results, _ = bridge.simulate(
        data, current_schedule, scenario=scenario, include_events=include_events
    )
    final_metrics = calculate_metrics(current_schedule, final_results)

    return MultiMoveOptimizationResult(
        scenario=scenario,
        include_events=include_events,
        max_moves=max_moves,
        stop_reason=stop_reason,
        baseline_results=baseline_results,
        final_results=final_results,
        baseline_metrics=baseline_metrics,
        final_metrics=final_metrics,
        moves=moves,
        candidate_history=candidate_history,
        optimized_schedule=current_schedule,
        total_evaluated_candidates=total_evaluated,
        total_improving_candidates=total_improving,
    )
