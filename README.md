# Digital Twin Lite - Parking & Schedule Analytics

Bản tích hợp hiện tại chạy theo luồng:

```text
Dataset student-based
→ Simulation theo parking visit
→ Global Multi-Move Optimization
→ Simulation After
→ Before/After Dashboard
```

## 1. Chạy dashboard

Windows: double-click:

```text
start_dashboard.bat
```

Hoặc terminal:

```bash
python serve_dashboard.py --port 8765
```

Mở:

```text
http://127.0.0.1:8765/index.html
```

Không mở bằng Live Server/port 5500 vì frontend dùng `/api/...` và cần chạy cùng backend.

---

## 2. Dataset hiện tại đã là student-based

Simulation sử dụng đồng thời:

```text
students.csv
+ enrollments.csv
+ schedule.csv
+ student_behavior.csv
```

Vì vậy hệ thống biết một sinh viên học những ca nào trong từng ngày.

Ví dụ một sinh viên học `Ca2 → Ca3 → Ca4` không còn bị tính là 3 lượt vào/ra. Simulation dựng **parking visit** của sinh viên và dùng `student_behavior.csv` để xử lý khả năng rời trường khi có khoảng trống giữa các ca.

`student_behavior.csv` hiện có các tỷ lệ:

```text
gap_0_leave_ratio
  ca liên tiếp

gap_1_leave_ratio
  cách 1 ca

gap_2plus_leave_ratio
  cách từ 2 ca trở lên
```

Các tỷ lệ được cấu hình riêng theo `near / medium / far`.

---

## 3. Heatmap và Bottleneck là hai khái niệm khác nhau

Heatmap trên dashboard hiển thị **số lượng sinh viên theo Ngày × Ca học**.

Ví dụ ô vàng nghĩa là ca đó có nhiều sinh viên, **không có nghĩa bản thân ô đó là parking bottleneck**.

Parking bottleneck được Simulation xác định tại:

```text
Ngày × khung giao ca S0..S4 × bãi P1..P4 × Checkin/Checkout
```

dựa trên:

```text
checkin_util  = incoming / checkin_throughput
checkout_util = outgoing / checkout_throughput
worst_util    = max(checkin_util, checkout_util)
```

Do đó optimizer có thể chuyển một lớp từ một ô heatmap không phải màu vàng nếu session đó đang trực tiếp đóng góp vào một bottleneck giao thông bãi xe.

---

## 4. Optimizer mới: Global Multi-Move

Phiên bản cũ có hai hạn chế:

1. ưu tiên bottleneck nghiêm trọng nhất rồi dừng tìm khi tìm được một move tốt tại điểm đó;
2. chỉ shortlist một số ít target có student load thấp nhất.

Điều này dễ làm nhiều move cùng xuất phát từ một ca và cùng đổ vào một vài target.

Phiên bản hiện tại đã đổi thành **global search theo từng iteration**:

```text
1. Simulation toàn hệ thống
2. Lấy TẤT CẢ bottleneck hiện tại
3. Lấy các session contributor có thể MOVE trên nhiều bottleneck/ngày khác nhau
4. Sinh target trên nhiều ngày/ca khác nhau
5. Kiểm tra:
   - room trống
   - room capacity
   - same-class conflict
   - student timetable conflict
6. Đánh giá candidate
7. Chọn candidate tốt nhất toàn cục
8. Apply move
9. Simulation lại và lặp tiếp
```

Không xét mọi session trong 559 session một cách mù quáng: chỉ những session thực sự đóng góp vào ít nhất một bottleneck mới là source có ý nghĩa để di chuyển. Tuy nhiên source được lấy **trên tất cả bottleneck**, không còn khóa vào một ngày/ca duy nhất.

### Objective

Thứ tự ưu tiên hiện tại:

1. giảm số `BOTTLENECK`;
2. giảm `total_overload_excess`;
3. giảm `max_worst_util`;
4. cân bằng tải giữa các ngày;
5. cân bằng tải giữa các ngày-ca.

`PEAK` chỉ là thông tin theo dõi.

---

## 5. Tăng tốc Optimization

Bản cũ chạy lại toàn bộ student-based Simulation cho gần như mọi candidate, nên `max_moves=3` cũng có thể mất khoảng hàng chục giây.

Bản mới sử dụng **exact affected-student delta evaluation**:

```text
Official SE baseline
-
flow cũ của các sinh viên thuộc class đang thử MOVE
+
flow mới của chính các sinh viên đó sau candidate MOVE
```

Chỉ các sinh viên thuộc lớp đang di chuyển có parking visit thay đổi. Candidate evaluator vì vậy không dựng lại toàn bộ lịch của hơn 4.000 sinh viên mỗi lần.

Candidate tốt nhất vẫn được **verify lại bằng full official Simulation Engine** trước khi apply, nên thuật toán tăng tốc không thay công thức SE.

Trên dataset hiện tại, kiểm thử nội bộ cho thấy xấp xỉ:

```text
3 moves  : ~2-3 giây
10 moves : ~5 giây
20 moves : ~9 giây
```

Thời gian thực tế phụ thuộc máy và dữ liệu chỉnh trên web.

---

## 6. Giới hạn Max Moves

Dashboard hiện hỗ trợ:

```text
1 .. 50 moves
```

Thay vì giới hạn 10 như bản cũ.

Optimizer vẫn có thể dừng trước `max_moves` khi:

```text
NO_BOTTLENECK
NO_MOVABLE_SOURCE
NO_IMPROVING_CANDIDATE
NO_VERIFIED_IMPROVEMENT
```

Điều này có nghĩa không nên hiểu `max_moves=30` là hệ thống bắt buộc phải chuyển đủ 30 lớp.

---

## 7. Events

Event là tải cố định của Simulation, không phải quyết định lịch học của OE.

Khi `Events ON`, bottleneck có thể bị event chi phối. Optimizer:

- không cố MOVE event;
- vẫn xét schedule contributors tại các bottleneck khác;
- tối ưu phần timetable mà hệ thống có quyền thay đổi.

Vì vậy một bottleneck do event tạo ra có thể vẫn còn dù đã dùng nhiều moves.

---

## 8. Chạy Optimization riêng

Mặc định:

```bash
python optimization/run_optimizer.py
```

Ví dụ:

```bash
python optimization/run_optimizer.py --max-moves 20
python optimization/run_optimizer.py --scenario Worst --max-moves 20
python optimization/run_optimizer.py --scenario Worst --max-moves 20 --include-events
```

### Điều chỉnh breadth của search

Mặc định interactive mode:

```text
source_limit = 12
target_limit = 12
```

Các source được phân bố qua nhiều bottleneck và các target được phân bố qua nhiều ngày.

Muốn search rộng hơn khi chạy offline:

```bash
python optimization/run_optimizer.py --max-moves 20 --source-limit 48 --target-limit 0
```

`target-limit=0` = xét toàn bộ target hợp lệ của mỗi source. Search rộng hơn sẽ chậm hơn.

---

## 9. API

### Simulation/dashboard

```text
GET /api/dashboard?scenario=Normal&include_events=true
```

### Optimization

```text
GET /api/optimize?scenario=Normal&include_events=true&max_moves=20
```

Backend chấp nhận `max_moves` từ 1 đến 50.

### Data Editor

```text
GET  /api/rooms/available
GET  /api/state
POST /api/schedule/add
POST /api/event/add
POST /api/parking/update
POST /api/behavior/update
POST /api/reset
```

Working dataset được giữ trong RAM. Khi user thay dữ liệu, optimizer cache được clear và Simulation/Optimization tiếp theo dùng dataset mới.

---

## 10. Output Optimization

Sau khi chạy CLI, `optimization/output/` gồm:

```text
recommendations.json
optimization_history.csv
candidate_history.csv
schedule_changes.csv
optimized_schedule.csv
baseline_simulation.csv
final_simulation.csv
before_after_metrics.csv
```

Frontend/API không phụ thuộc vào các CSV output này; dashboard chạy trực tiếp trên working dataset trong server.

---

## 11. Test

Windows:

```text
run_tests.bat
```

Hoặc:

```bash
python Dataset/validate_data.py
python test_pipeline.py
python optimization/run_optimizer.py --max-moves 3 --include-events
python optimization/test_optimizer.py
```

Bộ test kiểm tra dataset, student parking visits, P1-P4, Events ON/OFF, optimizer, room/student conflict và tính nhất quán với official Simulation Engine.
