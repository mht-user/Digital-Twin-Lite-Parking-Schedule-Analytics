from __future__ import annotations

import argparse
import hashlib
import json
import threading
import urllib.parse
import webbrowser
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from run_simulation import DATASET_DIR, SHIFT_ORDER, load_dataset
from dashboard_data import build_dashboard_payload, build_optimization_payload

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8080
DAY_VN = {
    "Mon": "Thu 2", "Tue": "Thu 3", "Wed": "Thu 4",
    "Thu": "Thu 5", "Fri": "Thu 6", "Sat": "Thu 7",
}
VALID_DAYS = set(DAY_VN)
VALID_BUILDINGS = {"A2", "B", "C", "D"}

_base_data = load_dataset(DATASET_DIR)
_live_data = deepcopy(_base_data)
_dataset_version = 1
_optimize_cache: Dict[tuple, Any] = {}
_state_lock = threading.RLock()


def _invalidate() -> None:
    global _dataset_version
    _dataset_version += 1
    _optimize_cache.clear()


def _recalc_parking_throughput(row: dict) -> None:
    in_gates = int(row["checkin_gates_open"])
    in_sec = float(row["checkin_sec_per_vehicle"])
    out_gates = int(row["checkout_gates_open"])
    out_sec = float(row["checkout_sec_per_vehicle"])
    if min(in_gates, out_gates) <= 0 or min(in_sec, out_sec) <= 0:
        raise ValueError("So cong va thoi gian xu ly phai > 0")
    row["max_checkin_throughput_veh_per_30min"] = str(int(round(in_gates * 1800 / in_sec)))
    row["max_checkout_throughput_veh_per_30min"] = str(int(round(out_gates * 1800 / out_sec)))


def _shift_template(shift: str) -> dict:
    for row in _live_data["schedule"]:
        if row["shift"] == shift:
            return {
                key: row[key]
                for key in [
                    "start_time", "end_time", "arrival_window_start", "arrival_window_end",
                    "departure_window_start", "departure_window_end",
                ]
            }
    raise ValueError(f"Khong tim thay template cho {shift}")


def _next_id(rows: list[dict], field: str, prefix: str, width: int) -> str:
    maximum = 0
    for row in rows:
        value = str(row.get(field, ""))
        if value.startswith(prefix) and value[len(prefix):].isdigit():
            maximum = max(maximum, int(value[len(prefix):]))
    return f"{prefix}{maximum + 1:0{width}d}"


def _student_conflict(class_id: str, day: str, shift: str) -> bool:
    enrolled = {
        row["student_id"] for row in _live_data["enrollments"]
        if row["class_id"] == class_id
    }
    if not enrolled:
        return False

    other_classes = {
        row["class_id"] for row in _live_data["schedule"]
        if row["day_of_week"] == day and row["shift"] == shift and row["class_id"] != class_id
    }
    if not other_classes:
        return False

    return any(
        row["student_id"] in enrolled and row["class_id"] in other_classes
        for row in _live_data["enrollments"]
    )


def _set_global_motorbike_ratio(target_ratio: float) -> None:
    """Deterministically update synthetic student motorbike usage to the requested ratio."""
    target_ratio = min(1.0, max(0.0, target_ratio))
    students = _live_data["students"]
    target_count = round(len(students) * target_ratio)
    ranked = sorted(
        students,
        key=lambda row: hashlib.sha256(row["student_id"].encode("utf-8")).hexdigest(),
    )
    enabled = {row["student_id"] for row in ranked[:target_count]}
    for row in students:
        row["uses_motorbike"] = "1" if row["student_id"] in enabled else "0"

    # Keep class/session motorbike_ratio metadata synchronized with the detailed student data.
    enrolled_by_class: Dict[str, list[str]] = {}
    for enrollment in _live_data["enrollments"]:
        enrolled_by_class.setdefault(enrollment["class_id"], []).append(enrollment["student_id"])
    uses = {row["student_id"]: row["uses_motorbike"] == "1" for row in students}

    ratio_by_class = {}
    for cls in _live_data["classes"]:
        members = enrolled_by_class.get(cls["class_id"], [])
        if members:
            ratio = sum(uses.get(student_id, False) for student_id in members) / len(members)
            cls["motorbike_ratio"] = f"{ratio:.6f}"
            ratio_by_class[cls["class_id"]] = cls["motorbike_ratio"]
    for session in _live_data["schedule"]:
        if session["class_id"] in ratio_by_class:
            session["motorbike_ratio"] = ratio_by_class[session["class_id"]]


class DashboardHandler(SimpleHTTPRequestHandler):
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/dashboard":
            scenario = query.get("scenario", ["Normal"])[0]
            include_events = query.get("include_events", ["true"])[0].lower() not in ("false", "0", "no")
            try:
                with _state_lock:
                    payload = build_dashboard_payload(
                        scenario=scenario,
                        include_events=include_events,
                        data=deepcopy(_live_data),
                    )
                    payload["dataset_version"] = _dataset_version
                self._send_json(payload)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if parsed.path == "/api/optimize":
            scenario = query.get("scenario", ["Normal"])[0]
            include_events = query.get("include_events", ["true"])[0].lower() not in ("false", "0", "no")
            try:
                max_moves = max(1, min(50, int(query.get("max_moves", ["3"])[0])))
            except ValueError:
                self._send_json({"available": False, "reason": "max_moves phai la so nguyen"}, status=400)
                return

            with _state_lock:
                version = _dataset_version
                cache_key = (version, scenario, include_events, max_moves)
                if cache_key in _optimize_cache:
                    self._send_json(_optimize_cache[cache_key])
                    return
                snapshot = deepcopy(_live_data)

            try:
                payload = build_optimization_payload(
                    scenario=scenario,
                    include_events=include_events,
                    max_moves=max_moves,
                    data=snapshot,
                )
                payload["dataset_version"] = version
                with _state_lock:
                    # Cache only if data was not edited while optimizer was running.
                    if version == _dataset_version:
                        _optimize_cache[cache_key] = payload
                self._send_json(payload)
            except Exception as exc:
                self._send_json({"available": False, "reason": str(exc)}, status=500)
            return

        if parsed.path == "/api/rooms/available":
            day = query.get("day", ["Mon"])[0]
            shift = query.get("shift", ["Ca1"])[0]
            building = query.get("building", ["A2"])[0]
            try:
                min_capacity = int(query.get("min_capacity", ["0"])[0])
            except ValueError:
                min_capacity = 0

            with _state_lock:
                occupied = {
                    row["room_id"] for row in _live_data["schedule"]
                    if row["day_of_week"] == day and row["shift"] == shift
                }
                rooms = [
                    {
                        "room_id": row["room_id"],
                        "building": row["building"],
                        "floor": row["floor"],
                        "room_capacity": int(row["room_capacity"]),
                    }
                    for row in _live_data["rooms"]
                    if row["building"] == building
                    and int(row["room_capacity"]) >= min_capacity
                    and row["room_id"] not in occupied
                ]
            rooms.sort(key=lambda row: (row["room_capacity"], row["room_id"]))
            self._send_json({"rooms": rooms})
            return

        if parsed.path == "/api/state":
            with _state_lock:
                self._send_json({
                    "dataset_version": _dataset_version,
                    "sessions": len(_live_data["schedule"]),
                    "events": len(_live_data["events"]),
                    "students": len(_live_data["students"]),
                })
            return

        super().do_GET()

    def do_POST(self) -> None:
        global _live_data
        parsed = urllib.parse.urlparse(self.path)
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            self._send_json({"error": "JSON request khong hop le"}, status=400)
            return

        if parsed.path == "/api/schedule/add":
            try:
                class_id = str(data["class_id"])
                day = str(data["day_of_week"])
                shift = str(data["shift"])
                room_id = str(data["room_id"])
                if day not in VALID_DAYS or shift not in SHIFT_ORDER:
                    raise ValueError("Day/shift khong hop le")

                with _state_lock:
                    room = next((r for r in _live_data["rooms"] if r["room_id"] == room_id), None)
                    cls = next((r for r in _live_data["classes"] if r["class_id"] == class_id), None)
                    if room is None or cls is None:
                        raise ValueError("Khong tim thay class hoac room")
                    if int(room["room_capacity"]) < int(cls["num_students"]):
                        raise ValueError("Phong khong du suc chua")
                    if any(
                        r["day_of_week"] == day and r["shift"] == shift and r["room_id"] == room_id
                        for r in _live_data["schedule"]
                    ):
                        raise ValueError(f"Phong {room_id} da co lop tai {day} {shift}")
                    if any(
                        r["class_id"] == class_id and r["day_of_week"] == day and r["shift"] == shift
                        for r in _live_data["schedule"]
                    ):
                        raise ValueError(f"Class {class_id} da co session tai {day} {shift}")
                    if _student_conflict(class_id, day, shift):
                        raise ValueError("Session moi gay trung lich cho sinh vien da ghi danh")

                    template = _shift_template(shift)
                    new_id = _next_id(_live_data["schedule"], "schedule_id", "SCH", 5)
                    new_row = {
                        "schedule_id": new_id,
                        "class_id": class_id,
                        "day_of_week": day,
                        "day_vn": DAY_VN[day],
                        "shift": shift,
                        **template,
                        "building": room["building"],
                        "floor": str(room["floor"]),
                        "room_id": room_id,
                        "room_capacity": str(room["room_capacity"]),
                        "num_students": str(cls["num_students"]),
                        "motorbike_ratio": str(cls["motorbike_ratio"]),
                        "dorm_ratio": str(cls.get("dorm_ratio", "0")),
                    }
                    _live_data["schedule"].append(new_row)
                    # Working-data edit: keep metadata consistent with the added session.
                    cls["sessions_per_week"] = str(int(cls.get("sessions_per_week", "0")) + 1)
                    _invalidate()

                self._send_json({"status": "success", "message": f"Da them session {new_id}"})
            except (KeyError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/event/add":
            try:
                day = str(data.get("day_of_week", "Mon"))
                shift = str(data.get("shift", "Ca2"))
                building = str(data.get("building", "D"))
                num_students = int(data.get("num_students", 0))
                motorbike_ratio = float(data.get("motorbike_ratio", 0))
                if day not in VALID_DAYS or shift not in SHIFT_ORDER or building not in VALID_BUILDINGS:
                    raise ValueError("Day/shift/building khong hop le")
                if num_students <= 0 or not 0 <= motorbike_ratio <= 1:
                    raise ValueError("num_students > 0 va motorbike_ratio trong [0,1]")

                with _state_lock:
                    event_id = _next_id(_live_data["events"], "event_id", "EVT", 3)
                    _live_data["events"].append({
                        "event_id": event_id,
                        "event_name": str(data.get("event_name", "Custom Event")),
                        "day_of_week": day,
                        "day_vn": DAY_VN[day],
                        "shift": shift,
                        "building": building,
                        "num_students": str(num_students),
                        "motorbike_ratio": str(motorbike_ratio),
                    })
                    _invalidate()
                self._send_json({"status": "success", "message": f"Da them event {event_id}"})
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/parking/update":
            try:
                lot_id = str(data.get("parking_lot_id", ""))
                scenario = str(data.get("scenario", "Normal"))
                if lot_id not in {"P1", "P2", "P3", "P4"} or scenario not in {"Normal", "Worst"}:
                    raise ValueError("Parking lot/scenario khong hop le")

                with _state_lock:
                    row = next((
                        r for r in _live_data["parking"]
                        if r["parking_lot_id"] == lot_id and r["scenario"] == scenario
                    ), None)
                    if row is None:
                        raise ValueError("Khong tim thay parking row")

                    mapping = {
                        "capacity_slots": int,
                        "checkin_gates_open": int,
                        "checkout_gates_open": int,
                        "checkin_sec_per_vehicle": float,
                        "checkout_sec_per_vehicle": float,
                    }
                    for key, caster in mapping.items():
                        if key in data:
                            value = caster(data[key])
                            if value <= 0:
                                raise ValueError(f"{key} phai > 0")
                            row[key] = str(value)
                    _recalc_parking_throughput(row)
                    _invalidate()
                self._send_json({"status": "success", "message": f"Da cap nhat {lot_id} / {scenario}"})
            except (TypeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/behavior/update":
            try:
                near_gap1 = float(data.get("near_gap1", 0.6))
                far_gap1 = float(data.get("far_gap1", 0.1))
                motorbike_ratio = float(data.get("default_motorbike_ratio", 0.85))
                if not all(0 <= value <= 1 for value in (near_gap1, far_gap1, motorbike_ratio)):
                    raise ValueError("Cac ratio phai nam trong [0,1]")

                with _state_lock:
                    for row in _live_data["student_behavior"]:
                        if row["distance_group"] == "near":
                            row["gap_1_leave_ratio"] = str(near_gap1)
                        elif row["distance_group"] == "far":
                            row["gap_1_leave_ratio"] = str(far_gap1)
                    _set_global_motorbike_ratio(motorbike_ratio)
                    _invalidate()
                self._send_json({
                    "status": "success",
                    "message": (
                        f"Da cap nhat behavior: near gap1={near_gap1:.0%}, "
                        f"far gap1={far_gap1:.0%}, motorbike={motorbike_ratio:.0%}"
                    ),
                })
            except (TypeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/reset":
            with _state_lock:
                _live_data = deepcopy(_base_data)
                _invalidate()
            self._send_json({"status": "success", "message": "Da reset working dataset"})
            return

        self._send_json({"error": "Route khong ton tai"}, status=404)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Digital Twin Lite dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    handler = partial(DashboardHandler, directory=str(BASE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/index.html"
    print("=" * 72)
    print("Digital Twin Lite dashboard")
    print(f"Open: {url}")
    print("IMPORTANT: mo bang URL tren, KHONG dung Live Server port 5500.")
    print("Ctrl+C de dung server.")
    print("=" * 72)
    try:
        if not args.no_browser:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDa dung server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
