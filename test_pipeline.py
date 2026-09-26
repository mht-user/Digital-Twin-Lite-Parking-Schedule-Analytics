import sys
import math
import subprocess
from pathlib import Path
from collections import Counter, defaultdict

from run_simulation import (
    load_dataset,
    run_simulation_from_data,
    make_what_if_schedule,
    compare_before_after,
)

TOL = 1e-6
VALID_STATUS = {"OK", "PEAK", "BOTTLENECK"}
VALID_DIRECTION = {"-", "Checkin", "Checkout", "Both"}
ASSERT_WORST_NOT_BETTER_THAN_NORMAL = True
REQUIRE_BOTTLENECK_IN_NORMAL = True
VALIDATOR_SCRIPT = "validate_data.py"
IMMUTABLE_SESSION_FIELDS = ("class_id", "num_students", "motorbike_ratio", "dorm_ratio")
ROOM_METADATA_FIELDS = ("building", "floor", "room_capacity")

class SkipTest(Exception):
    pass

try:
    from run_optimizer import optimize_schedule
    _OPTIMIZER_IMPORT_ERROR = None
except ImportError as e:
    optimize_schedule = None
    _OPTIMIZER_IMPORT_ERROR = e

def to_num(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default

def same_value(a, b) -> bool:
    na, nb = to_num(a), to_num(b)
    if na is not None and nb is not None:
        return math.isclose(na, nb, rel_tol=1e-9, abs_tol=TOL)
    return str(a) == str(b)

def run_sim(data, schedule, scenario="Normal", include_events=True):
    return run_simulation_from_data(schedule, data["events"], data["parking"],
                                     scenario=scenario, include_events=include_events)

def find_room_conflicts(schedule: list):
    counter = Counter((r["day_of_week"], r["shift"], r["room_id"]) for r in schedule)
    return sorted(k for k, n in counter.items() if n > 1)

def find_class_time_conflicts(schedule: list):
    counter = Counter((r["class_id"], r["day_of_week"], r["shift"]) for r in schedule)
    return sorted(k for k, n in counter.items() if n > 1)

def room_capacity_map(data: dict):
    return {r["room_id"]: to_num(r["room_capacity"]) for r in data["rooms"]}

def room_info_map(data: dict):
    return {r["room_id"]: r for r in data["rooms"]}

def rooms_by_building(data: dict):
    out = defaultdict(set)
    for r in data["rooms"]:
        out[r["building"]].add(r["room_id"])
    return out

def objective(results: list):
    n_bottleneck = sum(1 for r in results if r["status"] == "BOTTLENECK")
    overload = sum(max(0.0, r["worst_util"] - 1.0) for r in results)
    max_util = max((r["worst_util"] for r in results), default=0.0)
    return (n_bottleneck, round(overload, 9), round(max_util, 9))

def assert_not_mutated(current: list, snapshot: list, label: str):
    assert current == snapshot, (
        f"{label}: optimize_schedule() da sua truc tiep len schedule dau vao (in-place) - "
        "phai copy tung dong (vi du dict(r)) truoc khi doi, khong duoc sua thang vao dict goc")

def assert_schedule_valid(schedule: list, baseline: list, data: dict, label: str):
    assert isinstance(schedule, list), f"{label}: phai tra ve list[dict], nhan duoc {type(schedule).__name__}"
    assert len(schedule) == len(baseline), (
        f"{label}: so session thay doi ({len(baseline)} -> {len(schedule)}), khong duoc mat/them buoi hoc")

    before_ids = Counter(r["schedule_id"] for r in baseline)
    after_ids = Counter(r["schedule_id"] for r in schedule)
    assert after_ids == before_ids, f"{label}: tap schedule_id thay doi - khong duoc them/bot/trung."

    before_by_id = {r["schedule_id"]: r for r in baseline}
    after_by_id = {r["schedule_id"]: r for r in schedule}
    changed = []
    for sid, before_row in before_by_id.items():
        after_row = after_by_id[sid]
        for f in IMMUTABLE_SESSION_FIELDS:
            if not same_value(after_row.get(f), before_row.get(f)):
                changed.append(f"{sid}.{f}: {before_row.get(f)} -> {after_row.get(f)}")
    assert not changed, f"{label}: cac truong khong duoc doi: {changed[:5]}"

    room_conf = find_room_conflicts(schedule)
    assert not room_conf, f"{label}: xung dot phong (day, shift, room_id): {room_conf[:5]}"

    class_conf = find_class_time_conflicts(schedule)
    assert not class_conf, f"{label}: cung class_id trung ngay-ca: {class_conf[:5]}"

    room_info = room_info_map(data)
    caps = room_capacity_map(data)
    missing_room, too_small, stale_meta = [], [], []
    for r in schedule:
        rid = r["room_id"]
        info = room_info.get(rid)
        if info is None:
            missing_room.append((r["schedule_id"], rid))
            continue
        size = to_num(r["num_students"])
        if size is not None and rid in caps and size > caps[rid] + TOL:
            too_small.append((r["schedule_id"], rid, size, caps[rid]))
        for col in ROOM_METADATA_FIELDS:
            if not same_value(r.get(col), info.get(col)):
                stale_meta.append(f"{r['schedule_id']}.{col}: {r.get(col)} != rooms.csv {info.get(col)} (room {rid})")
    assert not missing_room, f"{label}: room_id khong ton tai trong rooms.csv: {missing_room[:5]}"
    assert not too_small, f"{label}: phong khong du suc chua: {too_small[:5]}"
    assert not stale_meta, f"{label}: metadata phong khong khop rooms.csv: {stale_meta[:5]}"

def find_validator_script():
    here = Path(__file__).resolve().parent
    for cand in (here / VALIDATOR_SCRIPT,
                 here / "Dataset" / VALIDATOR_SCRIPT,
                 Path.cwd() / VALIDATOR_SCRIPT,
                 Path.cwd() / "Dataset" / VALIDATOR_SCRIPT):
        if cand.is_file():
            return cand
    return None

def run_validator():
    script = find_validator_script()
    if script is None:
        raise SkipTest(f"khong tim thay {VALIDATOR_SCRIPT} (da thu ./ va ./Dataset/)")
    proc = subprocess.run([sys.executable, script.name], cwd=script.parent,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr

def test_validate_data():
    code, out = run_validator()
    if code != 0:
        errs = [l for l in out.splitlines() if l.startswith("ERROR:")]
        raise AssertionError(f"validate_data.py FAIL (exit {code}), {len(errs)} loi, vi du:\n  "
                             + "\n  ".join(errs[:5]))
    assert "STATUS: PASS" in out, f"validate_data.py exit 0 nhung khong in STATUS: PASS:\n{out[-300:]}"
    print("[PASS] test_validate_data (validate_data.py -> STATUS: PASS)")

REQUIRED_FIELDS = {
    "day", "shift", "lot_id", "incoming", "outgoing",
    "checkin_capacity", "checkout_capacity", "checkin_util",
    "checkout_util", "worst_util", "bottleneck_direction", "status",
}

def test_normal_load():
    data = load_dataset()
    results = run_sim(data, data["schedule"], scenario="Normal")

    assert isinstance(results, list), "phai tra ve list[dict]"
    assert len(results) > 0, "Ket qua khong duoc rong voi du lieu Normal"
    missing = REQUIRED_FIELDS - set(results[0].keys())
    assert not missing, f"Thieu truong: {missing}"
    assert all(r["incoming"] >= 0 and r["outgoing"] >= 0 for r in results), "incoming/outgoing khong duoc am"
    print(f"[PASS] test_normal_load ({len(results)} dong ket qua)")

def check_row_consistency(r: dict, where: str):
    errs = []
    ci, co, worst = r["checkin_util"], r["checkout_util"], r["worst_util"]
    cap_in, cap_out = r["checkin_capacity"], r["checkout_capacity"]
    status, direction = r["status"], r["bottleneck_direction"]
    tag = f"{where} {r['day']}/{r['shift']}/{r['lot_id']}"

    if ci < -TOL or co < -TOL:
        errs.append(f"{tag}: utilization am (checkin={ci}, checkout={co})")
    if cap_in <= 0 or cap_out <= 0:
        errs.append(f"{tag}: capacity phai > 0 (checkin={cap_in}, checkout={cap_out})")
    if not math.isclose(worst, max(ci, co), rel_tol=1e-9, abs_tol=TOL):
        errs.append(f"{tag}: worst_util={worst} != max(checkin={ci}, checkout={co})")
    if status not in VALID_STATUS:
        errs.append(f"{tag}: status '{status}' khong thuoc {sorted(VALID_STATUS)}")
    if direction not in VALID_DIRECTION:
        errs.append(f"{tag}: direction '{direction}' khong thuoc {sorted(VALID_DIRECTION)}")

    if direction == "Checkin" and ci < co - TOL:
        errs.append(f"{tag}: direction=Checkin nhung checkout_util ({co}) > checkin_util ({ci})")
    if direction == "Checkout" and co < ci - TOL:
        errs.append(f"{tag}: direction=Checkout nhung checkin_util ({ci}) > checkout_util ({co})")
    if direction == "Both" and min(ci, co) <= 0:
        errs.append(f"{tag}: direction=Both nhung mot ben co utilization = 0")
    if status == "BOTTLENECK" and direction == "-":
        errs.append(f"{tag}: status=BOTTLENECK nhung khong chi ra direction")
    if status == "OK" and direction != "-":
        errs.append(f"{tag}: status=OK nhung direction='{direction}' (ky vong '-')")
    return errs

def test_simulation_consistency():
    data = load_dataset()
    checked = 0
    errs = []
    for scenario in ("Normal", "Worst"):
        results = run_sim(data, data["schedule"], scenario=scenario)
        for r in results:
            errs.extend(check_row_consistency(r, scenario))
            checked += 1
    assert not errs, f"{len(errs)} vi pham rang buoc, vi du:\n  - " + "\n  - ".join(errs[:5])
    print(f"[PASS] test_simulation_consistency ({checked} dong deu thoa worst_util/capacity/status/direction)")

def test_bottleneck_detection():
    data = load_dataset()
    results = run_sim(data, data["schedule"], scenario="Normal")

    bottlenecks = [r for r in results if r["status"] == "BOTTLENECK"]
    flagged = [r for r in results if r["status"] in ("PEAK", "BOTTLENECK")]
    if REQUIRE_BOTTLENECK_IN_NORMAL:
        assert bottlenecks, ("Scenario Normal phai co it nhat 1 BOTTLENECK (nghen cau truc, SRS 8.3); "
                             f"hien chi co {len(flagged)} dong PEAK")
    elif not flagged:
        raise SkipTest("dataset khong co dong PEAK/BOTTLENECK nao de kiem tra direction")

    mismatched = []
    for r in flagged:
        ci, co, d = r["checkin_util"], r["checkout_util"], r["bottleneck_direction"]
        expected = "Checkin" if ci > co + TOL else ("Checkout" if co > ci + TOL else "Both")
        ok = (d == expected) or (d == "Both" and abs(ci - co) <= TOL)
        if not ok:
            mismatched.append(f"{r['day']}/{r['shift']}/{r['lot_id']}: "
                              f"direction={d} nhung checkin={ci:.2f}, checkout={co:.2f} (ky vong {expected})")
    assert not mismatched, ("bottleneck_direction khong khop ben util lon hon:\n  - "
                            + "\n  - ".join(mismatched[:5]))

    dist = Counter(r["bottleneck_direction"] for r in flagged)
    max_normal = max(r["worst_util"] for r in results)

    extra = ""
    if ASSERT_WORST_NOT_BETTER_THAN_NORMAL:
        results_worst = run_sim(data, data["schedule"], scenario="Worst")
        max_worst = max(r["worst_util"] for r in results_worst)
        assert max_worst >= max_normal - TOL, (
            f"Worst phai nghen bang hoac nang hon Normal (Worst={max_worst:.0%}, Normal={max_normal:.0%})")
        extra = f"; Worst max_util={max_worst:.0%} >= Normal {max_normal:.0%}"

    print(f"[PASS] test_bottleneck_detection ({len(bottlenecks)}/{len(results)} dong BOTTLENECK o Normal, "
          f"phan bo direction={dict(dist)}{extra})")

def event_slots(events) -> set:
    return {(e["day_of_week"], e["shift"]) for e in events}

def demand_map(results: list):
    return {(r["day"], r["shift"], r["lot_id"]): (r["incoming"], r["outgoing"]) for r in results}

def test_event_toggle():
    data = load_dataset()
    slots = event_slots(data["events"])
    if not slots:
        raise SkipTest("dataset khong co event nao de bat/tat")

    off = demand_map(run_sim(data, data["schedule"], scenario="Normal", include_events=False))
    on = demand_map(run_sim(data, data["schedule"], scenario="Normal", include_events=True))

    only_off = set(off) - set(on)
    assert not only_off, f"Tat event lai sinh them slot khong co khi bat event: {sorted(only_off)[:5]}"
    new_slots = set(on) - set(off)
    off = {**{k: (0, 0) for k in new_slots}, **off}

    def is_event_slot(key):
        day, shift, _ = key
        return (day, shift) in slots

    changed_event = [k for k in on if is_event_slot(k) and on[k] != off[k]]
    changed_other = [k for k in on if not is_event_slot(k) and on[k] != off[k]]

    assert changed_event, (
        f"Bat event khong lam doi demand o bat ky slot nao co event "
        f"(co {sum(1 for k in on if is_event_slot(k))} slot nhu vay)")
    assert not changed_other, f"Demand doi o slot KHONG co event, vi du: {changed_other[:5]}"
    assert all(on[k][0] >= off[k][0] and on[k][1] >= off[k][1] for k in changed_event), (
        "Bat event chi duoc lam demand tang hoac giu nguyen, khong duoc giam")

    print(f"[PASS] test_event_toggle ({len(changed_event)} slot co event doi demand"
          f"{f', trong do {len(new_slots)} slot moi sinh ra' if new_slots else ''}, "
          f"{len(on) - len(changed_event)} slot con lai giu nguyen)")

def pick_move(schedule: list, data: dict):
    caps = room_capacity_map(data)
    occupied = {(r["day_of_week"], r["shift"], r["room_id"]) for r in schedule}
    by_building = rooms_by_building(data)
    all_slots = sorted({(r["day_of_week"], r["shift"]) for r in schedule})
    class_slots = {(r["class_id"], r["day_of_week"], r["shift"]) for r in schedule}

    for row in schedule:
        day, shift, cur_room = row["day_of_week"], row["shift"], row["room_id"]
        size = to_num(row["num_students"])
        for cand in sorted(by_building[row["building"]] - {cur_room}):
            if (day, shift, cand) in occupied:
                continue
            if size is not None and caps.get(cand, 0) < size:
                continue
            return row, {"room_id": cand}

    for row in schedule:
        day, shift, cur_room, cls = row["day_of_week"], row["shift"], row["room_id"], row["class_id"]
        for (d2, s2) in all_slots:
            if (d2, s2) == (day, shift):
                continue
            if (d2, s2, cur_room) in occupied or (cls, d2, s2) in class_slots:
                continue
            return row, {"day_of_week": d2, "shift": s2}

    raise SkipTest("schedule day kin - khong tim duoc MOVE hop le nao de test")

def apply_move(schedule: list, row: dict, change: dict, data: dict):
    new_row = dict(row)
    new_row.update(change)
    if "room_id" in change:
        info = room_info_map(data).get(change["room_id"])
        if info:
            for col in ROOM_METADATA_FIELDS:
                new_row[col] = info[col]

    return make_what_if_schedule(
        schedule,
        day_of_week=row["day_of_week"], shift=row["shift"], building=row["building"],
        remove_class_ids=[row["class_id"]], add_rows=[new_row],
    )

def test_schedule_move_no_conflict():
    data = load_dataset()
    schedule, events, parking = data["schedule"], data["events"], data["parking"]

    assert not find_room_conflicts(schedule), "Du lieu goc dang co xung dot phong, chay validate_data.py truoc"
    assert not find_class_time_conflicts(schedule), "Du lieu goc dang co lop trung ngay-ca, chay validate_data.py truoc"

    row, change = pick_move(schedule, data)
    schedule_after = apply_move(schedule, row, change, data)

    assert_schedule_valid(schedule_after, schedule, data, "sau MOVE")

    results_before, results_after = compare_before_after(schedule, schedule_after, events, parking, scenario="Normal")
    assert results_before and results_after, "compare_before_after() phai tra ve ket qua cho ca 2 ban"

    print(f"[PASS] test_schedule_move_no_conflict (lop {row['class_id']} {row['day_of_week']}/{row['shift']}/"
          f"{row['room_id']} -> {change}, giu nguyen {len(schedule_after)} dong, 0 xung dot)")

def test_oe_integration():
    if optimize_schedule is None:
        raise SkipTest(f"khong import duoc run_optimizer.optimize_schedule ({_OPTIMIZER_IMPORT_ERROR})")

    data = load_dataset()
    baseline_snapshot = [dict(r) for r in data["schedule"]]
    baseline = run_sim(data, data["schedule"], scenario="Normal")
    obj_before = objective(baseline)

    optimized = optimize_schedule(data["schedule"], data["events"], data["parking"], scenario="Normal")
    assert isinstance(optimized, list), f"optimize_schedule() phai tra ve list[dict], nhan duoc {type(optimized).__name__}"
    assert optimized is not data["schedule"], "optimize_schedule() tra ve chinh list dau vao - phai tra ve list MOI"
    assert_not_mutated(data["schedule"], baseline_snapshot, "sau OPTIMIZE")
    assert_schedule_valid(optimized, baseline_snapshot, data, "sau OPTIMIZE")

    after = run_sim(data, optimized, scenario="Normal")
    for r in after:
        errs = check_row_consistency(r, "after-opt")
        assert not errs, f"Ket qua SE sau toi uu vi pham rang buoc: {errs[0]}"

    obj_after = objective(after)
    assert obj_after <= obj_before, (
        f"Objective sau toi uu te hon truoc: before={obj_before} -> after={obj_after} "
        "(thu tu so sanh: so BOTTLENECK, tong qua tai, max util)")

    verdict = "tot hon" if obj_after < obj_before else "khong doi (schedule goc da toi uu hoac optimizer la stub)"
    print(f"[PASS] test_oe_integration (optimize_schedule: objective {obj_before} -> {obj_after}, {verdict})")

def test_full_pipeline():
    steps = []
    data = load_dataset()

    try:
        code, _out = run_validator()
        assert code == 0, "validate_data.py FAIL - pipeline dung o buoc 1"
        steps.append("validate")
    except SkipTest:
        steps.append("validate(skip)")

    before = run_sim(data, data["schedule"], scenario="Normal")
    assert before, "run_simulation (before) khong tra ve ket qua"
    steps.append("sim-before")

    if optimize_schedule is None:
        raise SkipTest(f"pipeline can run_optimizer.optimize_schedule ({_OPTIMIZER_IMPORT_ERROR})")

    baseline_snapshot = [dict(r) for r in data["schedule"]]
    optimized = optimize_schedule(data["schedule"], data["events"], data["parking"], scenario="Normal")
    assert_not_mutated(data["schedule"], baseline_snapshot, "pipeline/optimize")
    assert_schedule_valid(optimized, baseline_snapshot, data, "pipeline/optimize")
    steps.append("optimize")

    after = run_sim(data, optimized, scenario="Normal")
    assert after, "run_simulation (after) khong tra ve ket qua"
    steps.append("sim-after")

    cmp_before, cmp_after = compare_before_after(
        data["schedule"], optimized, data["events"], data["parking"], scenario="Normal")
    assert cmp_before and cmp_after, "compare_before_after() phai tra ve ca 2 bo ket qua"
    assert objective(cmp_after) <= objective(cmp_before), "Ban After trong compare phai khong te hon Before"
    steps.append("compare")

    n_before = sum(1 for r in cmp_before if r["status"] == "BOTTLENECK")
    n_after = sum(1 for r in cmp_after if r["status"] == "BOTTLENECK")
    print(f"[PASS] test_full_pipeline ({' -> '.join(steps)}; BOTTLENECK {n_before} -> {n_after})")

TESTS = [
    test_validate_data,
    test_normal_load,
    test_simulation_consistency,
    test_bottleneck_detection,
    test_event_toggle,
    test_schedule_move_no_conflict,
    test_oe_integration,
    test_full_pipeline,
]

def main():
    failed = skipped = 0
    for t in TESTS:
        try:
            t()
        except SkipTest as e:
            skipped += 1
            print(f"[SKIP] {t.__name__}: {e}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
    passed = len(TESTS) - failed - skipped
    print(f"\n{passed}/{len(TESTS)} test(s) passed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
