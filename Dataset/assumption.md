# ASSUMPTIONS — Digital Twin Lite

## 1. Phạm vi dữ liệu

Dataset là dữ liệu synthetic phục vụ mô phỏng Digital Twin, không phải dữ liệu vận hành thật của NEU. Hệ thống hiện mô hình hoá lịch học, phòng học, sinh viên ghi danh, hành vi ở lại/rời trường giữa các ca, sự kiện phát sinh và 4 bãi xe P1–P4.

## 2. Ca học và khung giao ca

| Ca | Giờ học | Arrival window | Departure window |
|---|---|---|---|
| Ca1 | 06:45–09:25 | 06:15–06:45 | 09:25–09:55 |
| Ca2 | 09:35–12:15 | 09:05–09:35 | 12:15–12:45 |
| Ca3 | 13:00–15:40 | 12:30–13:00 | 15:40–16:10 |
| Ca4 | 15:50–18:30 | 15:20–15:50 | 18:30–19:00 |

Simulation quy đổi thành 5 flow-slot:

- S0: trước Ca1;
- S1: giao Ca1–Ca2;
- S2: giao Ca2–Ca3;
- S3: giao Ca3–Ca4;
- S4: sau Ca4.

## 3. Mô hình sinh viên — student/visit-based

Simulation chính không còn coi mỗi session là một lượt vào–ra độc lập. Quan hệ được dựng theo:

`students.csv → enrollments.csv → classes.csv → schedule.csv`.

Với từng sinh viên trong từng ngày, hệ thống sắp xếp toàn bộ ca học rồi tạo **parking visit**:

- học các ca liên tiếp: xe ở lại bãi, không checkout/checkin lại giữa hai ca;
- có ca trống: một phần sinh viên có thể rời trường rồi quay lại theo `student_behavior.csv`;
- xe checkout khỏi đúng bãi đã chọn lúc bắt đầu visit.

`uses_motorbike` trong `students.csv` là nguồn chính để xác định sinh viên có tạo lưu lượng xe máy. `motorbike_ratio` ở `classes.csv`/`schedule.csv` được giữ làm metadata và để tương thích với dữ liệu cũ; core student-based simulation không dùng nó để đếm xe của lịch học thường.

## 4. Hành vi giữa các ca

`student_behavior.csv`:

| distance_group | gap_0 | gap_1 | gap_2plus |
|---|---:|---:|---:|
| near | 0.00 | 0.60 | 0.90 |
| medium | 0.00 | 0.30 | 0.75 |
| far | 0.00 | 0.10 | 0.50 |

- `gap_0`: hai ca liên tiếp, mặc định không rời trường;
- `gap_1`: có đúng một ca trống ở giữa;
- `gap_2plus`: có từ hai ca trống trở lên.

Các tỷ lệ này là assumption ban đầu và có thể chỉnh từ Data Editor trên dashboard.

## 5. Phân bổ xe về bãi

Khi một parking visit bắt đầu, xe được phân bổ P1–P4 theo inverse-distance từ building của ca đầu visit:

`weight_i = (1 / distance_i) / sum(1 / distance_j)`.

Bãi đã chọn được giữ nguyên tới lúc visit kết thúc.

Khoảng cách đang dùng:

| Building | P1 | P2 | P3 | P4 |
|---|---:|---:|---:|---:|
| A2 | 200 | 100 | 300 | 200 |
| B | 120 | 40 | 240 | 120 |
| C | 60 | 100 | 200 | 100 |
| D | 80 | 40 | 240 | 140 |

## 6. Capacity và throughput

Hai khái niệm được tách riêng:

- `capacity_slots`: sức chứa vật lý của bãi;
- `max_checkin_throughput_veh_per_30min`: năng lực cổng vào/30 phút;
- `max_checkout_throughput_veh_per_30min`: năng lực cổng ra/30 phút.

Gate utilization:

- `checkin_util = incoming / checkin_throughput`;
- `checkout_util = outgoing / checkout_throughput`;
- `worst_util = max(checkin_util, checkout_util)`.

Phân loại:

- `< 90%`: OK;
- `90%–100%`: PEAK;
- `> 100%`: BOTTLENECK.

## 7. Parking scenarios

`Normal` và `Worst` mô tả trạng thái hạ tầng, không phải nhãn Peak/Bottleneck của lịch học.

- Normal: 4 bãi hoạt động theo thông số tiêu chuẩn.
- Worst: throughput giảm do điều kiện bất lợi; P1 còn 1 checkout gate.

## 8. Events

`events.csv` là tải phát sinh ngoài timetable. Event không có enrollment chi tiết nên được xử lý như một flow bổ sung: check-in trước ca event và checkout sau ca event. Người dùng có thể bật/tắt event trên dashboard.

## 9. Data Editor

Dashboard chỉnh **working dataset trong bộ nhớ** để chạy what-if. CSV gốc không bị ghi đè. Khi restart server hoặc gọi `/api/reset`, working dataset quay về dữ liệu gốc.

## 10. Giới hạn

- Dữ liệu synthetic;
- chưa mô phỏng occupancy theo từng phút trong bãi;
- chưa có dữ liệu phương tiện ngoài xe máy;
- event chưa có danh sách participant cụ thể;
- hành vi rời trường giữa ca là expected-flow theo tỷ lệ, không phải dự đoán cá nhân.
