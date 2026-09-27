# -*- coding: utf-8 -*-
"""
validate_data.py — Digital Twin Lite NEU: bo kiem tra tinh toan ven du lieu.

Chay: python3 validate_data.py
(Tu dong tim cac file .csv nam CUNG THU MUC voi chinh file script nay, nen
co the chay tu bat ky dau, khong phu thuoc thu muc lam viec hien tai.)

Thoat voi exit code 0 neu TAT CA kiem tra deu PASS, khac 0 neu co it nhat
1 kiem tra FAIL (phu hop de gan vao CI/pipeline).
"""
import csv
import os
import sys
from collections import defaultdict, Counter

BASE = os.path.dirname(os.path.abspath(__file__))

results = []  # list of (check_name, passed: bool, detail: str)

def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))

def load_csv(filename):
    path = os.path.join(BASE, filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

# ------------------------------------------------------------------
# 0. NAP DU LIEU
# ------------------------------------------------------------------
classes      = load_csv("classes.csv")
rooms        = load_csv("rooms.csv")
schedule     = load_csv("schedule.csv")
events       = load_csv("events.csv")
parking      = load_csv("parking.csv")
students     = load_csv("students.csv")
enrollments  = load_csv("enrollments.csv")
behavior     = load_csv("student_behavior.csv")

required_files = {
    "classes.csv": classes, "rooms.csv": rooms, "schedule.csv": schedule,
    "events.csv": events, "parking.csv": parking, "students.csv": students,
    "enrollments.csv": enrollments, "student_behavior.csv": behavior,
}
for fname, data in required_files.items():
    check(f"[File] {fname} ton tai va doc duoc", data is not None,
          "" if data is not None else f"Khong tim thay {fname} trong {BASE}")

# Neu thieu file nao thi dung lai, khong the kiem tra tiep cac phan lien quan
if any(v is None for v in required_files.values()):
    missing = [k for k, v in required_files.items() if v is None]
    print(f"DUNG KIEM TRA: thieu file {missing}")
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
    sys.exit(1)

# Index tra cuu nhanh
classes_by_id = {c["class_id"]: c for c in classes}
rooms_by_id = {r["room_id"]: r for r in rooms}
students_by_id = {s["student_id"]: s for s in students}

# ==================================================================
# A. KIEM TRA HA TANG PHONG HOC / LICH HOC (da co tu truoc)
# ==================================================================

# A1. room_id trong rooms.csv la duy nhat
room_ids = [r["room_id"] for r in rooms]
check("[rooms] room_id la duy nhat", len(room_ids) == len(set(room_ids)),
      f"So room_id trung: {len(room_ids) - len(set(room_ids))}")

# A2. building khop tien to room_id (ca trong rooms.csv lan schedule.csv)
bad_room_prefix = [r["room_id"] for r in rooms if not r["room_id"].startswith(r["building"] + "-")]
check("[rooms] building khop tien to room_id", len(bad_room_prefix) == 0,
      f"So dong lech: {len(bad_room_prefix)} (vd: {bad_room_prefix[:3]})")

bad_sched_prefix = [r["schedule_id"] for r in schedule if not r["room_id"].startswith(r["building"] + "-")]
check("[schedule] building khop tien to room_id", len(bad_sched_prefix) == 0,
      f"So dong lech: {len(bad_sched_prefix)} (vd: {bad_sched_prefix[:3]})")

# A3. schedule.csv.room_id/building/floor/room_capacity khop CHINH XAC voi rooms.csv
mismatch_master = []
for r in schedule:
    rm = rooms_by_id.get(r["room_id"])
    if rm is None:
        mismatch_master.append(r["schedule_id"])
        continue
    if (rm["building"] != r["building"] or rm["floor"] != r["floor"]
            or rm["room_capacity"] != r["room_capacity"]):
        mismatch_master.append(r["schedule_id"])
check("[schedule] building/floor/room_capacity khop rooms.csv", len(mismatch_master) == 0,
      f"So dong lech: {len(mismatch_master)} (vd: {mismatch_master[:3]})")

# A4. Khong co 2 lop dung chung 1 phong trong cung (ngay, ca)
seen_room_slot = defaultdict(list)
for r in schedule:
    key = (r["day_of_week"], r["shift"], r["room_id"])
    seen_room_slot[key].append(r["class_id"])
room_conflicts = {k: v for k, v in seen_room_slot.items() if len(set(v)) > 1}
check("[schedule] khong trung phong cung (ngay,ca)", len(room_conflicts) == 0,
      f"So truong hop trung: {len(room_conflicts)}")

# A5. Khong lop nao vuot suc chua phong
overflow = [r["schedule_id"] for r in schedule
            if int(r["num_students"]) > int(r["room_capacity"])]
check("[schedule] khong lop nao vuot suc chua phong", len(overflow) == 0,
      f"So dong vi pham: {len(overflow)} (vd: {overflow[:3]})")

# A6. class_id trong schedule.csv ton tai trong classes.csv
bad_class_ref = [r["schedule_id"] for r in schedule if r["class_id"] not in classes_by_id]
check("[schedule] class_id ton tai trong classes.csv", len(bad_class_ref) == 0,
      f"So dong loi: {len(bad_class_ref)}")

# A7. classes.csv: pham vi gia tri hop le
bad_num_students = [c["class_id"] for c in classes if not (30 <= int(c["num_students"]) <= 80)]
check("[classes] num_students trong khoang 30-80", len(bad_num_students) == 0,
      f"So lop vi pham: {len(bad_num_students)}")

bad_ratio = [c["class_id"] for c in classes
             if not (0 <= float(c["motorbike_ratio"]) <= 1) or not (0 <= float(c["dorm_ratio"]) <= 1)]
check("[classes] motorbike_ratio/dorm_ratio trong [0,1]", len(bad_ratio) == 0,
      f"So lop vi pham: {len(bad_ratio)}")

bad_sessions = [c["class_id"] for c in classes if c["sessions_per_week"] not in ("1", "2")]
check("[classes] sessions_per_week thuoc {1,2}", len(bad_sessions) == 0,
      f"So lop vi pham: {len(bad_sessions)}")

# A8. events.csv: building hop le, so lieu hop le
VALID_BUILDINGS = {"A2", "B", "C", "D"}
bad_event_building = [e["event_id"] for e in events if e["building"] not in VALID_BUILDINGS]
check("[events] building thuoc {A2,B,C,D}", len(bad_event_building) == 0,
      f"So su kien vi pham: {len(bad_event_building)}")

bad_event_num = [e["event_id"] for e in events if int(e["num_students"]) <= 0]
check("[events] num_students > 0", len(bad_event_num) == 0,
      f"So su kien vi pham: {len(bad_event_num)}")

bad_event_ratio = [e["event_id"] for e in events if not (0 <= float(e["motorbike_ratio"]) <= 1)]
check("[events] motorbike_ratio trong [0,1]", len(bad_event_ratio) == 0,
      f"So su kien vi pham: {len(bad_event_ratio)}")

# A9. parking.csv: scenario chi con Normal/Worst, thong so hop le
VALID_SCENARIOS = {"Normal", "Worst"}
bad_scenario = [p["parking_lot_id"] + "/" + p["scenario"] for p in parking
                if p["scenario"] not in VALID_SCENARIOS]
check("[parking] scenario chi thuoc {Normal,Worst}", len(bad_scenario) == 0,
      f"Gia tri la: {sorted(set(p['scenario'] for p in parking))}")

bad_gates = [p["parking_lot_id"] for p in parking
             if int(p["checkin_gates_open"]) <= 0 or int(p["checkout_gates_open"]) <= 0]
check("[parking] so cong vao/ra > 0", len(bad_gates) == 0,
      f"So dong vi pham: {len(bad_gates)}")

bad_sec = [p["parking_lot_id"] for p in parking
           if float(p["checkin_sec_per_vehicle"]) <= 0 or float(p["checkout_sec_per_vehicle"]) <= 0]
check("[parking] thoi gian xu ly/xe > 0", len(bad_sec) == 0,
      f"So dong vi pham: {len(bad_sec)}")

# ==================================================================
# B. KIEM TRA MOI: STUDENTS / ENROLLMENTS / STUDENT_BEHAVIOR
# ==================================================================

# B1. student_id trong students.csv la duy nhat
student_ids_all = [s["student_id"] for s in students]
check("[students] student_id la duy nhat", len(student_ids_all) == len(set(student_ids_all)),
      f"So student_id trung: {len(student_ids_all) - len(set(student_ids_all))}")

# B2. distance_group hop le
VALID_DISTANCE_GROUPS = {"near", "medium", "far"}
bad_dg = [s["student_id"] for s in students if s["distance_group"] not in VALID_DISTANCE_GROUPS]
check("[students] distance_group thuoc {near,medium,far}", len(bad_dg) == 0,
      f"So SV vi pham: {len(bad_dg)} (vd: {bad_dg[:3]})")

# B3. uses_motorbike la 0/1
bad_moto = [s["student_id"] for s in students if s["uses_motorbike"] not in ("0", "1")]
check("[students] uses_motorbike thuoc {0,1}", len(bad_moto) == 0,
      f"So SV vi pham: {len(bad_moto)}")

# B4. enrollments: student_id ton tai trong students.csv
bad_enr_student = [(e["student_id"], e["class_id"]) for e in enrollments
                   if e["student_id"] not in students_by_id]
check("[enrollments] student_id ton tai trong students.csv", len(bad_enr_student) == 0,
      f"So dong loi: {len(bad_enr_student)} (vd: {bad_enr_student[:3]})")

# B5. enrollments: class_id ton tai trong classes.csv
bad_enr_class = [(e["student_id"], e["class_id"]) for e in enrollments
                 if e["class_id"] not in classes_by_id]
check("[enrollments] class_id ton tai trong classes.csv", len(bad_enr_class) == 0,
      f"So dong loi: {len(bad_enr_class)} (vd: {bad_enr_class[:3]})")

# B6. Khong duplicate (student_id, class_id)
pair_counter = Counter((e["student_id"], e["class_id"]) for e in enrollments)
dup_pairs = [k for k, v in pair_counter.items() if v > 1]
check("[enrollments] khong duplicate (student_id, class_id)", len(dup_pairs) == 0,
      f"So cap trung: {len(dup_pairs)} (vd: {dup_pairs[:3]})")

# B7. So luong enrollment/lop KHOP DUNG voi num_students cua lop do
enroll_count_by_class = Counter(e["class_id"] for e in enrollments)
class_slot_ids = {r["class_id"] for r in schedule}  # cac lop thuc su co dong lich
mismatch_count = []
for c in classes:
    cid = c["class_id"]
    if cid not in class_slot_ids:
        continue  # lop khong co dong lich thi khong the ghi danh, bo qua kiem tra nay
    expected = int(c["num_students"])
    actual = enroll_count_by_class.get(cid, 0)
    if actual != expected:
        mismatch_count.append((cid, expected, actual))
check("[enrollments] so luong ghi danh/lop KHOP num_students", len(mismatch_count) == 0,
      f"So lop lech: {len(mismatch_count)} (vd: {mismatch_count[:3]})")

# B8. QUAN TRONG: khong SV nao bi xep 2 lop cung (day_of_week, shift)
class_slots = defaultdict(set)
for r in schedule:
    class_slots[r["class_id"]].add((r["day_of_week"], r["shift"]))

student_slot_usage = defaultdict(list)  # student_id -> list[(day,shift,class_id)]
for e in enrollments:
    cid = e["class_id"]
    if cid not in class_slots:
        continue
    for day, shift in class_slots[cid]:
        student_slot_usage[e["student_id"]].append((day, shift, cid))

time_conflicts = []
for sid, usage in student_slot_usage.items():
    seen = {}
    for day, shift, cid in usage:
        key = (day, shift)
        if key in seen and seen[key] != cid:
            time_conflicts.append((sid, key, seen[key], cid))
        else:
            seen[key] = cid
check("[enrollments] khong SV nao bi trung (ngay,ca) giua 2 lop", len(time_conflicts) == 0,
      f"So truong hop vi pham: {len(time_conflicts)} (vd: {time_conflicts[:3]})")

# B9. student_behavior.csv: distance_group hop le + du 3 nhom + khong trung
behavior_groups = [b["distance_group"] for b in behavior]
check("[student_behavior] distance_group hop le va du 3 nhom (near/medium/far), khong trung",
      set(behavior_groups) == VALID_DISTANCE_GROUPS and len(behavior_groups) == len(set(behavior_groups)),
      f"Cac nhom hien co: {behavior_groups}")

# B10. cac leave_ratio nam trong [0,1]
bad_leave_ratio = []
for b in behavior:
    for col in ("gap_0_leave_ratio", "gap_1_leave_ratio", "gap_2plus_leave_ratio"):
        val = float(b[col])
        if not (0 <= val <= 1):
            bad_leave_ratio.append((b["distance_group"], col, val))
check("[student_behavior] cac leave_ratio nam trong [0,1]", len(bad_leave_ratio) == 0,
      f"So gia tri vi pham: {len(bad_leave_ratio)} (vd: {bad_leave_ratio[:3]})")

# ==================================================================
# IN KET QUA
# ==================================================================
print("=" * 70)
print("KET QUA KIEM TRA DATASET - Digital Twin Lite NEU")
print("=" * 70)
n_pass = sum(1 for _, ok, _ in results if ok)
n_fail = len(results) - n_pass
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f" - {detail}"
    print(line)

print("-" * 70)
print(f"TONG: {len(results)} kiem tra | PASS: {n_pass} | FAIL: {n_fail}")

# Thong ke nhanh (khong phai kiem tra pass/fail, chi de tham khao)
print("-" * 70)
print("THONG KE THAM KHAO:")
print(f"  - So sinh vien (students.csv): {len(students)}")
print(f"  - So luot ghi danh (enrollments.csv): {len(enrollments)}")
if students:
    avg_classes = len(enrollments) / len(students)
    print(f"  - So lop trung binh/sinh vien: {avg_classes:.2f}")
dg_dist = Counter(s["distance_group"] for s in students)
print(f"  - Phan bo distance_group: {dict(dg_dist)}")
moto_dist = Counter(s["uses_motorbike"] for s in students)
print(f"  - Phan bo uses_motorbike: {dict(moto_dist)}")

sys.exit(0 if n_fail == 0 else 1)
