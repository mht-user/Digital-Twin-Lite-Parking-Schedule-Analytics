# Optimization Engineer

Module tối ưu lịch học theo kết quả mô phỏng bãi xe của Simulation Engineer.
Sử dụng **Iterative Multi-Move Optimization** và gọi trực tiếp
`run_simulation.py` của Simulation Engineer để đánh giá mọi phương án Before/After.

## 1. Cấu trúc thư mục

```text
project/
├── Dataset/
├── run_simulation.py
└── optimization/
    ├── optimizer.py
    ├── se_bridge.py
    ├── run_optimizer.py
    ├── test_optimizer.py
    └── README.md
```

## 2. Vai trò từng file

- `optimizer.py`: thuật toán Optimization chính. Sinh candidate MOVE, kiểm tra constraint,
  đánh giá candidate và chạy tối ưu nhiều bước.
- `se_bridge.py`: lớp kết nối với `run_simulation.py` của Simulation Engineer. File này
  **không viết lại công thức mô phỏng**.
- `run_optimizer.py`: entry point để chạy Optimization và xuất kết quả.
- `test_optimizer.py`: kiểm tra schedule sau tối ưu và xác minh lại kết quả bằng chính
  Simulation Engine.
- `README.md`: hướng dẫn của module.

## 3. Luồng hoạt động

```text
Current schedule
      ↓
Simulation Engineer
      ↓
Worst bottleneck
      ↓
Optimization tìm các session đóng góp vào bottleneck
      ↓
Sinh các MOVE candidate
      ↓
Kiểm tra phòng / capacity / conflict
      ↓
Simulation Engineer chạy từng candidate
      ↓
Chọn best move
      ↓
Cập nhật schedule và chạy Simulation lại
      ↓
Lặp cho tới điều kiện dừng
```

Optimizer dừng khi xảy ra một trong ba điều kiện:

1. Không còn `BOTTLENECK`.
2. Không còn candidate nào cải thiện objective.
3. Đạt `max_moves`.

Mặc định `max_moves = 3`.

## 4. Cách xác định session liên quan đến bottleneck

Simulation Engineer hiện sử dụng các flow slot `S0..S4` và tách riêng incoming/outgoing.
Optimizer dùng mapping của chính SE:

- bottleneck `Checkin` → xét session có **arrival** nằm trong slot đó;
- bottleneck `Checkout` → xét session có **departure** nằm trong slot đó;
- bottleneck `Both` → xét cả hai hướng.

Optimizer không tự suy diễn lại slot và không viết một Simulation Engine riêng.

## 5. Constraint khi MOVE

Một candidate chỉ được chấp nhận khi:

- giữ nguyên building;
- có phòng trống tại ngày/ca đích;
- `room_capacity >= num_students`;
- không có room conflict;
- cùng class không có session khác trùng ngày/ca đích.

Khi đổi ca, code đồng thời cập nhật:

- `start_time`, `end_time`;
- `arrival_window_start`, `arrival_window_end`;
- `departure_window_start`, `departure_window_end`;
- `room_id`, `floor`, `room_capacity`.

Việc cập nhật arrival/departure window là bắt buộc để SE mô phỏng đúng incoming/outgoing
sau khi lịch được chuyển.

## 6. Objective

Candidate được xếp hạng theo thứ tự ưu tiên lexicographic:

1. giảm `total_overload_excess`;
2. giảm `max_worst_util`;
3. giảm `bottleneck_points`;
4. giảm `daily_load_std`;
5. giảm `day_shift_load_std`.

`peak_points` vẫn được lưu để báo cáo nhưng **không dùng làm tiêu chí chính**. Ví dụ một
điểm giảm từ `BOTTLENECK 101%` xuống `PEAK 98%` là cải thiện dù số lượng PEAK có thể tăng.

## 7. Cách chạy

Từ thư mục gốc project:

```bash
python optimization/run_optimizer.py
```

Mặc định:

```text
scenario = Normal
include_events = False
max_moves = 3
```

Các lựa chọn khác:

```bash
python optimization/run_optimizer.py --max-moves 5
python optimization/run_optimizer.py --scenario Worst
python optimization/run_optimizer.py --include-events
```

Nếu muốn chỉ định đường dẫn khác:

```bash
python optimization/run_optimizer.py --se-file path/to/run_simulation.py --dataset-dir path/to/Dataset
```

## 8. Output sau khi chạy

Chương trình tự tạo:

```text
optimization/output/
├── recommendations.json
├── optimization_history.csv
├── candidate_history.csv
├── optimized_schedule.csv
├── baseline_simulation.csv
├── final_simulation.csv
└── before_after_metrics.csv
```

Ý nghĩa chính:

- `recommendations.json`: dữ liệu gọn để backend/Frontend đọc, gồm Before, After,
  `moves[]`, scenario, event option và stop reason.
- `optimization_history.csv`: một dòng cho mỗi move thực sự được áp dụng.
- `candidate_history.csv`: top candidate của từng vòng tối ưu.
- `optimized_schedule.csv`: lịch cuối sau tất cả move.
- `baseline_simulation.csv`: kết quả SE trước tối ưu.
- `final_simulation.csv`: kết quả SE sau tối ưu.
- `before_after_metrics.csv`: KPI Before/After.

## 9. Kiểm thử

Phải chạy optimizer trước để tạo output:

```bash
python optimization/run_optimizer.py
```

Sau đó:

```bash
python optimization/test_optimizer.py
```

Test kiểm tra:

- không mất session;
- tập `schedule_id` không đổi;
- số session thay đổi khớp số move;
- mỗi session chỉ được move một lần;
- giữ nguyên building/class/student count;
- phòng mới tồn tại và đủ capacity;
- không room conflict;
- không same-class same-slot conflict;
- objective cuối tốt hơn baseline;
- lịch sử tối ưu cải thiện qua từng move;
- các metric lưu ra khớp với việc chạy lại bằng Simulation Engine chính thức.

## 10. Phạm vi module

Optimization module **không**:

- sửa `run_simulation.py`;
- tự viết lại công thức Simulation;
- tạo backend/API;
- thực hiện cross-building move;
- thực hiện SWAP.

Backend/API và việc kết nối Frontend thuộc phần Integration/System Analyst.
