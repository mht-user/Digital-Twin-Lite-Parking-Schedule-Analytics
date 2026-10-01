# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHECKS = []


def load_csv(name: str):
    path = BASE / name
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def check(name: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(condition), detail))


def minutes(value: str) -> int:
    h, m = value.strip().split(":")
    return int(h) * 60 + int(m)


files = {
    name: load_csv(name)
    for name in [
        "classes.csv", "rooms.csv", "schedule.csv", "events.csv", "parking.csv",
        "students.csv", "enrollments.csv", "student_behavior.csv",
    ]
}

for name, rows in files.items():
    check(f"[File] {name} ton tai va doc duoc", rows is not None)

if any(rows is None for rows in files.values()):
    for name, ok, detail in CHECKS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    sys.exit(1)

classes = files["classes.csv"]
rooms = files["rooms.csv"]
schedule = files["schedule.csv"]
events = files["events.csv"]
parking = files["parking.csv"]
students = files["students.csv"]
enrollments = files["enrollments.csv"]
behavior = files["student_behavior.csv"]

classes_by_id = {r["class_id"]: r for r in classes}
rooms_by_id = {r["room_id"]: r for r in rooms}
students_by_id = {r["student_id"]: r for r in students}

# ---------------------------
# IDs / references
# ---------------------------
for rows, key, label in [
    (classes, "class_id", "classes"),
    (rooms, "room_id", "rooms"),
    (schedule, "schedule_id", "schedule"),
    (events, "event_id", "events"),
    (students, "student_id", "students"),
]:
    ids = [r[key] for r in rows]
    check(f"[{label}] {key} la duy nhat", len(ids) == len(set(ids)),
          f"duplicate={len(ids)-len(set(ids))}")

bad_class_ref = [r["schedule_id"] for r in schedule if r["class_id"] not in classes_by_id]
check("[schedule] class_id ton tai trong classes.csv", not bad_class_ref,
      f"vi pham={bad_class_ref[:5]}")

bad_room_ref = [r["schedule_id"] for r in schedule if r["room_id"] not in rooms_by_id]
check("[schedule] room_id ton tai trong rooms.csv", not bad_room_ref,
      f"vi pham={bad_room_ref[:5]}")

# ---------------------------
# Rooms / schedule integrity
# ---------------------------
room_meta_mismatch = []
capacity_violations = []
for row in schedule:
    room = rooms_by_id.get(row["room_id"])
    if not room:
        continue
    if (
        row["building"] != room["building"]
        or str(row["floor"]) != str(room["floor"])
        or str(row["room_capacity"]) != str(room["room_capacity"])
    ):
        room_meta_mismatch.append(row["schedule_id"])
    if int(row["num_students"]) > int(room["room_capacity"]):
        capacity_violations.append(row["schedule_id"])

check("[schedule] building/floor/room_capacity khop rooms.csv", not room_meta_mismatch,
      f"vi pham={room_meta_mismatch[:5]}")
check("[schedule] room_capacity >= num_students", not capacity_violations,
      f"vi pham={capacity_violations[:5]}")

room_slot_counter = Counter((r["day_of_week"], r["shift"], r["room_id"]) for r in schedule)
room_conflicts = [key for key, count in room_slot_counter.items() if count > 1]
check("[schedule] khong trung phong cung ngay-ca", not room_conflicts,
      f"vi pham={room_conflicts[:5]}")

class_slot_counter = Counter((r["class_id"], r["day_of_week"], r["shift"]) for r in schedule)
class_conflicts = [key for key, count in class_slot_counter.items() if count > 1]
check("[schedule] cung class khong trung ngay-ca", not class_conflicts,
      f"vi pham={class_conflicts[:5]}")

# copied class fields and sessions/week
copied_mismatch = []
for row in schedule:
    cls = classes_by_id.get(row["class_id"])
    if not cls:
        continue
    for col in ("num_students", "motorbike_ratio", "dorm_ratio"):
        if abs(float(row[col]) - float(cls[col])) > 1e-9:
            copied_mismatch.append(f"{row['schedule_id']}.{col}")
check("[schedule] copied class fields khop classes.csv", not copied_mismatch,
      f"vi pham={copied_mismatch[:5]}")

actual_sessions = Counter(r["class_id"] for r in schedule)
session_mismatch = []
for cls in classes:
    if actual_sessions[cls["class_id"]] != int(cls["sessions_per_week"]):
        session_mismatch.append((cls["class_id"], cls["sessions_per_week"], actual_sessions[cls["class_id"]]))
check("[classes] sessions_per_week khop schedule.csv", not session_mismatch,
      f"vi pham={session_mismatch[:5]}")

# ---------------------------
# Time-window consistency
# ---------------------------
VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat"}
VALID_SHIFTS = {"Ca1", "Ca2", "Ca3", "Ca4"}
time_errors = []
for row in schedule:
    try:
        start = minutes(row["start_time"])
        end = minutes(row["end_time"])
        a0 = minutes(row["arrival_window_start"])
        a1 = minutes(row["arrival_window_end"])
        d0 = minutes(row["departure_window_start"])
        d1 = minutes(row["departure_window_end"])
        if not (a0 < a1 <= start < end <= d0 < d1):
            time_errors.append(row["schedule_id"])
    except Exception:
        time_errors.append(row["schedule_id"])
check("[schedule] arrival/start/end/departure window hop le", not time_errors,
      f"vi pham={time_errors[:5]}")

bad_schedule_domain = [r["schedule_id"] for r in schedule if r["day_of_week"] not in VALID_DAYS or r["shift"] not in VALID_SHIFTS]
check("[schedule] day/shift hop le", not bad_schedule_domain,
      f"vi pham={bad_schedule_domain[:5]}")

# ---------------------------
# Events
# ---------------------------
VALID_BUILDINGS = {"A2", "B", "C", "D"}
bad_events = []
for row in events:
    try:
        ok = (
            row["day_of_week"] in VALID_DAYS
            and row["shift"] in VALID_SHIFTS
            and row["building"] in VALID_BUILDINGS
            and int(row["num_students"]) > 0
            and 0 <= float(row["motorbike_ratio"]) <= 1
        )
    except Exception:
        ok = False
    if not ok:
        bad_events.append(row["event_id"])
check("[events] day/shift/building/num_students/motorbike_ratio hop le", not bad_events,
      f"vi pham={bad_events[:5]}")

# ---------------------------
# Parking: exactly Normal/Worst x P1-P4 + formulas
# ---------------------------
parking_keys = [(r["scenario"], r["parking_lot_id"]) for r in parking]
check("[parking] composite key (scenario, lot) la duy nhat",
      len(parking_keys) == len(set(parking_keys)))

scenario_lots = defaultdict(set)
parking_errors = []
for row in parking:
    scenario_lots[row["scenario"]].add(row["parking_lot_id"])
    try:
        ci_gates = int(row["checkin_gates_open"])
        co_gates = int(row["checkout_gates_open"])
        ci_sec = float(row["checkin_sec_per_vehicle"])
        co_sec = float(row["checkout_sec_per_vehicle"])
        capacity = int(row["capacity_slots"])
        expected_ci = int(round(ci_gates * 1800 / ci_sec))
        expected_co = int(round(co_gates * 1800 / co_sec))
        ok = (
            ci_gates > 0 and co_gates > 0 and ci_sec > 0 and co_sec > 0 and capacity > 0
            and int(row["max_checkin_throughput_veh_per_30min"]) == expected_ci
            and int(row["max_checkout_throughput_veh_per_30min"]) == expected_co
            and all(float(row[f"dist_from_{b}_m"]) > 0 for b in ("A2", "B", "C", "D"))
        )
    except Exception:
        ok = False
    if not ok:
        parking_errors.append((row["scenario"], row["parking_lot_id"]))

check("[parking] scenario dung Normal/Worst",
      set(scenario_lots) == {"Normal", "Worst"}, f"scenario={sorted(scenario_lots)}")
for scenario in ("Normal", "Worst"):
    check(f"[parking] {scenario} co dung P1/P2/P3/P4",
          scenario_lots.get(scenario) == {"P1", "P2", "P3", "P4"},
          f"hien co={sorted(scenario_lots.get(scenario, set()))}")
check("[parking] throughput/capacity/distance formula hop le", not parking_errors,
      f"vi pham={parking_errors[:5]}")

# ---------------------------
# Student/enrollment/behavior
# ---------------------------
VALID_GROUPS = {"near", "medium", "far"}
bad_students = [
    r["student_id"] for r in students
    if r["distance_group"] not in VALID_GROUPS or r["uses_motorbike"] not in ("0", "1")
]
check("[students] distance_group va uses_motorbike hop le", not bad_students,
      f"vi pham={bad_students[:5]}")

enrollment_pairs = [(r["student_id"], r["class_id"]) for r in enrollments]
check("[enrollments] khong duplicate student-class",
      len(enrollment_pairs) == len(set(enrollment_pairs)))

bad_enrollment_ref = [
    pair for pair in enrollment_pairs
    if pair[0] not in students_by_id or pair[1] not in classes_by_id
]
check("[enrollments] student_id/class_id ton tai", not bad_enrollment_ref,
      f"vi pham={bad_enrollment_ref[:5]}")

enrollment_count = Counter(r["class_id"] for r in enrollments)
enrollment_mismatch = [
    (cls["class_id"], int(cls["num_students"]), enrollment_count[cls["class_id"]])
    for cls in classes
    if enrollment_count[cls["class_id"]] != int(cls["num_students"])
]
check("[enrollments] enrollment count khop num_students", not enrollment_mismatch,
      f"vi pham={enrollment_mismatch[:5]}")

class_slots = defaultdict(list)
for row in schedule:
    class_slots[row["class_id"]].append((row["day_of_week"], row["shift"]))
student_slot_counter = Counter()
for enrollment in enrollments:
    for day, shift in class_slots.get(enrollment["class_id"], []):
        student_slot_counter[(enrollment["student_id"], day, shift)] += 1
student_conflicts = [key for key, count in student_slot_counter.items() if count > 1]
check("[enrollments] khong student conflict cung ngay-ca", not student_conflicts,
      f"vi pham={student_conflicts[:5]}")

behavior_groups = [r["distance_group"] for r in behavior]
check("[student_behavior] du near/medium/far va khong duplicate",
      set(behavior_groups) == VALID_GROUPS and len(behavior_groups) == len(set(behavior_groups)))

behavior_errors = []
for row in behavior:
    for col in ("gap_0_leave_ratio", "gap_1_leave_ratio", "gap_2plus_leave_ratio"):
        try:
            value = float(row[col])
            if not 0 <= value <= 1:
                behavior_errors.append((row["distance_group"], col, value))
        except Exception:
            behavior_errors.append((row["distance_group"], col, row.get(col)))
check("[student_behavior] leave ratios trong [0,1]", not behavior_errors,
      f"vi pham={behavior_errors[:5]}")

print("=" * 72)
print("DATASET VALIDATION - DIGITAL TWIN LITE")
print("=" * 72)
passed = 0
for name, ok, detail in CHECKS:
    passed += int(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
print("-" * 72)
print(f"TONG: {len(CHECKS)} | PASS: {passed} | FAIL: {len(CHECKS)-passed}")
print(f"Students: {len(students)} | Enrollments: {len(enrollments)} | Schedule rows: {len(schedule)}")
print("STATUS: PASS" if passed == len(CHECKS) else "STATUS: FAIL")
sys.exit(0 if passed == len(CHECKS) else 1)
