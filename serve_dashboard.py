"""
serve_dashboard.py - Backend API tích hợp Digital Twin Campus Flow
Hỗ trợ:
  - GET  /api/dashboard           : Trạng thái hiện trạng (Heatmap, KPI, Parking View)
  - GET  /api/optimize            : Chạy multi-move optimizer (Before vs After, Move Details)
  - GET  /api/rooms/available     : Lọc danh sách phòng trống + đủ sức chứa
  - POST /api/schedule/add        : Thêm/dời lịch học (validate không trùng phòng/lớp)
  - POST /api/event/add           : Thêm sự kiện phát sinh
  - POST /api/parking/update      : Cập nhật hạ tầng bãi xe (Throughput tự tính)
  - POST /api/behavior/update     : Điều chỉnh assumption hành vi sinh viên & re-simulate
"""

from __future__ import annotations

import copy
import json
import socketserver
import urllib.parse
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List

from run_simulation import (
    DATASET_DIR,
    SHIFT_ORDER,
    load_dataset,
    run_simulation_from_data,
    build_window_slot_map,
)
from dashboard_data import (
    build_dashboard_payload,
    build_optimization_payload,
    build_slot_labels,
    build_heatmap,
    build_parking_view,
    build_kpi,
    _strip_internal,
)

PORT = 8080
_optimize_cache: Dict[tuple, Any] = {}

# Giữ dataset trong bộ nhớ để phục vụ chỉnh sửa tức thời (Digital Twin State)
_live_data = load_dataset(DATASET_DIR)
# Ban goc, KHONG BAO GIO sua - dung de /api/reset khoi phuc _live_data
_base_data = copy.deepcopy(_live_data)

# Gio hoc that theo schedule.csv: shift -> (start, end, arr_start, arr_end, dep_start, dep_end)
SHIFT_TIMES = {
    "Ca1": ("06:45", "09:25", "06:15", "06:45", "09:25", "09:55"),
    "Ca2": ("09:35", "12:15", "09:05", "09:35", "12:15", "12:45"),
    "Ca3": ("13:00", "15:40", "12:30", "13:00", "15:40", "16:10"),
    "Ca4": ("15:50", "18:30", "15:20", "15:50", "18:30", "19:00"),
}

def _recalc_parking_throughput(row: dict) -> None:
    """Tự động tính Throughput từ gates * 1800 / sec"""
    in_gates = int(row.get("checkin_gates_open", 2))
    in_sec = float(row.get("checkin_sec_per_vehicle", 3))
    out_gates = int(row.get("checkout_gates_open", 2))
    out_sec = float(row.get("checkout_sec_per_vehicle", 10))

    row["max_checkin_throughput_veh_per_30min"] = int(round(in_gates * 1800 / max(in_sec, 0.1)))
    row["max_checkout_throughput_veh_per_30min"] = int(round(out_gates * 1800 / max(out_sec, 0.1)))

class DashboardHandler(SimpleHTTPRequestHandler):
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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

        # 1. API DASHBOARD
        if parsed.path == "/api/dashboard":
            scenario = query.get("scenario", ["Normal"])[0]
            include_events = query.get("include_events", ["true"])[0].lower() not in ("false", "0", "no")

            try:
                payload = build_dashboard_payload(
                    scenario=scenario,
                    include_events=include_events,
                    data=_live_data,
                )
                self._send_json(payload)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        # 2. API OPTIMIZE
        if parsed.path == "/api/optimize":
            scenario = query.get("scenario", ["Normal"])[0]
            include_events = query.get("include_events", ["true"])[0].lower() not in ("false", "0", "no")
            max_moves = int(query.get("max_moves", ["3"])[0])

            cache_key = (scenario, include_events, max_moves)
            if cache_key in _optimize_cache:
                self._send_json(_optimize_cache[cache_key])
                return

            try:
                payload = build_optimization_payload(
                    scenario=scenario,
                    include_events=include_events,
                    max_moves=max_moves,
                    data=_live_data,
                )
                _optimize_cache[cache_key] = payload
                self._send_json(payload)
            except Exception as exc:
                self._send_json({"available": False, "reason": str(exc)}, status=500)
            return

        # 3. API ROOMS AVAILABLE: Lọc phòng đủ capacity + đang trống
        if parsed.path == "/api/rooms/available":
            day = query.get("day", ["Mon"])[0]
            shift = query.get("shift", ["Ca1"])[0]
            building = query.get("building", ["A2"])[0]
            min_cap = int(query.get("min_capacity", ["0"])[0])

            occupied_rooms = {
                r["room_id"] for r in _live_data["schedule"]
                if r["day_of_week"] == day and r["shift"] == shift
            }

            available_rooms = []
            for r in _live_data["rooms"]:
                if r["building"] == building:
                    cap = int(r["room_capacity"])
                    if cap >= min_cap and r["room_id"] not in occupied_rooms:
                        available_rooms.append({
                            "room_id": r["room_id"],
                            "building": r["building"],
                            "floor": r["floor"],
                            "room_capacity": cap,
                        })

            available_rooms.sort(key=lambda x: (x["room_capacity"], x["room_id"]))
            self._send_json({"rooms": available_rooms})
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length)
        data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}

        # 4. API THÊM / CẬP NHẬT SCHEDULE
        if parsed.path == "/api/schedule/add":
            class_id = data.get("class_id")
            day = data.get("day_of_week")
            shift = data.get("shift")
            room_id = data.get("room_id")

            # Validate xung đột
            for r in _live_data["schedule"]:
                if r["day_of_week"] == day and r["shift"] == shift:
                    if r["room_id"] == room_id:
                        self._send_json({"error": f"Phòng {room_id} đã có lớp học trong {day} {shift}!"}, status=400)
                        return
                    if r["class_id"] == class_id:
                        self._send_json({"error": f"Lớp {class_id} đã có lịch vào {day} {shift}!"}, status=400)
                        return

            room_info = next((rm for rm in _live_data["rooms"] if rm["room_id"] == room_id), None)
            class_info = next((c for c in _live_data["classes"] if c["class_id"] == class_id), None)
            if not room_info or not class_info:
                self._send_json({"error": "Không tìm thấy thông tin phòng hoặc lớp học"}, status=400)
                return

            if shift not in SHIFT_TIMES:
                self._send_json({"error": f"Ca học '{shift}' không hợp lệ (chỉ nhận Ca1, Ca2, Ca3, Ca4)"}, status=400)
                return

            if int(room_info["room_capacity"]) < int(class_info["num_students"]):
                self._send_json({
                    "error": f"Phòng {room_id} chỉ chứa {room_info['room_capacity']} chỗ, "
                             f"không đủ cho lớp {class_id} ({class_info['num_students']} sinh viên)!"
                }, status=400)
                return

            start_t, end_t, arr_s, arr_e, dep_s, dep_e = SHIFT_TIMES[shift]

            new_sched_id = f"SCH{len(_live_data['schedule']) + 1:05d}"
            new_row = {
                "schedule_id": new_sched_id,
                "class_id": class_id,
                "day_of_week": day,
                "day_vn": {"Mon":"Thu 2","Tue":"Thu 3","Wed":"Thu 4","Thu":"Thu 5","Fri":"Thu 6","Sat":"Thu 7"}.get(day, day),
                "shift": shift,
                "start_time": start_t,
                "end_time": end_t,
                "building": room_info["building"],
                "floor": str(room_info["floor"]),
                "room_id": room_id,
                "room_capacity": str(room_info["room_capacity"]),
                "num_students": str(class_info["num_students"]),
                "motorbike_ratio": str(class_info["motorbike_ratio"]),
                "dorm_ratio": str(class_info.get("dorm_ratio", "0.1")),
                "arrival_window_start": arr_s,
                "arrival_window_end": arr_e,
                "departure_window_start": dep_s,
                "departure_window_end": dep_e,
            }
            _live_data["schedule"].append(new_row)
            _optimize_cache.clear()
            self._send_json({"status": "success", "message": f"Đã thêm buổi học {new_sched_id}"})
            return

        # 5. API THÊM EVENT
        if parsed.path == "/api/event/add":
            new_evt_id = f"EVT{len(_live_data['events']) + 1:03d}"
            new_event = {
                "event_id": new_evt_id,
                "event_name": data.get("event_name", "Thi giữa kỳ"),
                "day_of_week": data.get("day_of_week", "Mon"),
                "day_vn": "Thu 2",
                "shift": data.get("shift", "Ca2"),
                "building": data.get("building", "D"),
                "num_students": str(data.get("num_students", 70)),
                "motorbike_ratio": str(data.get("motorbike_ratio", 0.85)),
            }
            _live_data["events"].append(new_event)
            _optimize_cache.clear()
            self._send_json({"status": "success", "message": f"Đã thêm sự kiện {new_evt_id}"})
            return

        # 6. API CẬP NHẬT PARKING INFRASTRUCTURE
        if parsed.path == "/api/parking/update":
            lot_id = data.get("parking_lot_id")
            scenario = data.get("scenario", "Normal")
            for r in _live_data["parking"]:
                if r["parking_lot_id"] == lot_id and r["scenario"] == scenario:
                    if "capacity_slots" in data: r["capacity_slots"] = str(data["capacity_slots"])
                    if "checkin_gates_open" in data: r["checkin_gates_open"] = str(data["checkin_gates_open"])
                    if "checkin_sec_per_vehicle" in data: r["checkin_sec_per_vehicle"] = str(data["checkin_sec_per_vehicle"])
                    if "checkout_gates_open" in data: r["checkout_gates_open"] = str(data["checkout_gates_open"])
                    if "checkout_sec_per_vehicle" in data: r["checkout_sec_per_vehicle"] = str(data["checkout_sec_per_vehicle"])
                    _recalc_parking_throughput(r)
            _optimize_cache.clear()
            self._send_json({"status": "success", "message": f"Cập nhật thành công nhà xe {lot_id}"})
            return

        # 7. API CẬP NHẬT STUDENT BEHAVIOR (DIGITAL TWIN ASSUMPTIONS)
        if parsed.path == "/api/behavior/update":
            near_gap1 = float(data.get("near_gap1", 0.6))
            far_gap1 = float(data.get("far_gap1", 0.1))
            default_ratio = float(data.get("default_motorbike_ratio", 0.85))

            # Điều chỉnh tỷ lệ phương tiện trực tiếp trên lịch hiện tại để xem phản hồi
            for row in _live_data["schedule"]:
                row["motorbike_ratio"] = str(round(default_ratio, 2))

            _optimize_cache.clear()
            self._send_json({
                "status": "success",
                "message": f"Đã cập nhật giả định Digital Twin: Near gap={near_gap1}, Far gap={far_gap1}, Motorbike={default_ratio}"
            })
            return

        # 8. API RESET: khôi phục _live_data về bản gốc
        if parsed.path == "/api/reset":
            # Sửa TẠI CHỖ (không gán lại) để mọi chỗ đang tham chiếu _live_data vẫn thấy dữ liệu mới
            _live_data.clear()
            _live_data.update(copy.deepcopy(_base_data))
            _optimize_cache.clear()
            self._send_json({"status": "success"})
            return

        self._send_json({"error": "Route không tồn tại"}, status=404)

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        httpd.allow_reuse_address = True
        print(f"Server Digital Twin API đang chạy tại: http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nĐã dừng server.")
