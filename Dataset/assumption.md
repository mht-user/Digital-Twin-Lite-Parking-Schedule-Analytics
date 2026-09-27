# ASSUMPTION.md — Digital Twin Lite: Dữ liệu lịch học & bãi xe ĐH Kinh tế Quốc dân (NEU)

## 0. Phạm vi 
File này được viết bởi **Data Engineer** - Trần Hoàng Trung: cung cấp dữ liệu **thô, mô tả hiện trạng** (lịch học hiện
tại + hạ tầng phòng học/nhà xe) một cách chính xác, nhất quán.

## 1. Hạ tầng giảng đường
| Toà | Số tầng | Phòng/tầng | Tổng phòng | Sức chứa/phòng (giả định) |
|---|---|---|---|---|
| A2 | 10 | 14 | 140 | 80–120 SV |
| B  | 3  | 8  | 24  | 40–60 SV |
| C  | 3  | 8  | 24  | 40–60 SV |
| D  | 5  | 8  | 40  | 60–90 SV |
| **Tổng** | | | **228 phòng** | |

Quy tắc đặt `room_id`: `<Toà>-<Tầng 2 chữ số><Số thứ tự phòng trong tầng, 2 chữ số>`, ví dụ
`D-0508` = toà D, tầng 5, phòng số 08 trong tầng. Đã kiểm tra 100%: mọi `room_id` trong cả
`rooms.csv` và `schedule.csv` đều khớp đúng `building`/`floor` tương ứng; không có 2 lớp dùng
chung 1 phòng trong cùng (ngày, ca); không có lớp nào vượt sức chứa phòng được xếp.

## 2. Ca học (không đổi)
| Ca | Giờ học | Cửa sổ đến | Cửa sổ về |
|---|---|---|---|
| Ca1 | 06:45–09:25 | 06:15–06:45 | 09:25–09:55 |
| Ca2 | 09:35–12:15 | 09:05–09:35 | 12:15–12:45 |
| Ca3 | 13:00–15:40 | 12:30–13:00 | 15:40–16:10 |
| Ca4 | 15:50–18:30 | 15:20–15:50 | 18:30–19:00 |

SV đến trước giờ học 30 phút, rời trường trong vòng 30 phút sau khi tan ca (theo đề bài). Việc
phân bố lượng xe đến/đi trong khung 30 phút đó theo hàm phân phối nào là lựa chọn mô hình hoá của
Simulation Engineer, dataset không áp đặt sẵn.

## 3. Sinh viên & phương tiện
- `motorbike_ratio` (0.75–0.95/lớp), `dorm_ratio` (0.05–0.25/lớp): thuộc tính từng lớp học phần,
  dùng để quy đổi sĩ số ra số xe máy cần gửi.
- Nhà xe chỉ nhận xe máy, chưa tính ô tô (theo đề bài).
- `events.csv` có thêm `motorbike_ratio` riêng cho từng sự kiện — dữ liệu cần thiết để simulation
  chạy được, không phải một suy diễn về mức độ tắc.
- Việc SV thích gửi xe ở P1/P2 hơn P3 dù ở KTX là hành vi cần được Simulation/Optimization
  Engineer tự mô hình hoá; dataset chỉ cung cấp nguyên liệu thô (sĩ số, tỷ lệ xe máy, tỷ lệ SV ở
  KTX, khoảng cách từng toà đến từng nhà xe ở mục 5), không áp sẵn tỷ lệ phân bổ vào từng nhà xe.

## 4. Nhà xe — hạ tầng & khoảng cách vật lý (`parking.csv`)
| Nhà xe | Phục vụ khu vực | Sức chứa | Cổng vào/ra (Normal) | Thời gian/lượt (Normal) |
|---|---|---|---|---|
| P1 | Gần toà A2, B | 3.000 xe | 2 vào / 2 ra | Vào 3s (quẹt vé) / Ra 10s (thu tiền mặt) |
| P2 | Gần toà C, D | 3.000 xe | 2 vào / 2 ra | Vào 3s / Ra 10s |
| P3 | Gần khu KTX | 6.000 xe | 2 vào / 2 ra | Vào 3s / Ra 10s |

Khoảng cách đi bộ ước lượng (mét) từ mỗi toà đến mỗi nhà xe:

| Từ toà \ Đến nhà xe | P1 | P2 | P3 |
|---|---|---|---|
| A2 | 150m | 650m | 900m |
| B  | 100m | 600m | 850m |
| C  | 650m | 120m | 820m |
| D  | 700m | 180m | 780m |

### Cột `scenario` trong `parking.csv` — chỉ còn 2 mức điều kiện vận hành hạ tầng
| scenario | Số cổng vào/ra | Thời gian xử lý/xe | Diễn giải |
|---|---|---|---|
| **Normal** | 2/2 mỗi nhà xe | 3s vào / 10s ra | Vận hành tiêu chuẩn |
| **Worst** | P1 còn 1/2 cổng ra (bảo trì); P2, P3 vẫn 2/2 | 4s vào / 12–13s ra (mưa) | Có sự cố hạ tầng + yếu tố thời tiết bất lợi, dùng khi mô phỏng kịch bản xấu (kết hợp thêm `events.csv` nếu muốn) |

3 mức này **không gắn với ngày cụ thể nào** trong `schedule.csv`. Simulation Engineer tự quyết
định dùng thông số `Normal` hay `Worst` cho khung (ngày, ca) đang mô phỏng, tuỳ giả định kịch bản.

## 5. `events.csv` — Sự kiện phát sinh ngoài lịch thường lệ
24 ca thi giữa kỳ (ví dụ điển hình cho sự kiện gây tăng đột biến nhu cầu): `event_id`, `event_name`,
`day_of_week`, `day_vn`, `shift`, `building`, `num_students`, `motorbike_ratio`. Không có `room_id`
vì ở mức mô phỏng nhà xe chỉ cần biết toà và số SV là đủ.


## 6. Quan hệ Sinh viên ↔ Lớp học phần: `students.csv`, `enrollments.csv`,
     `student_behavior.csv`

### 6.1. Vì sao cần bổ sung
Dữ liệu mô phỏng đúng hành vi gửi/lấy xe giữa các ca (SV học liền 2-3 ca thường không lấy xe về giữa
giờ; SV có ca trống dài có thể về rồi quay lại).

### 6.2. `students.csv` — Danh sách sinh viên (synthetic)
`student_id, distance_group, uses_motorbike`. Không dùng dữ liệu cá nhân thật.
- `distance_group` (near/medium/far) được random theo phân bố giả định: near 35%, medium 40%,
  far 25% (có thể điều chỉnh lại nếu có số liệu khảo sát thật).
- `uses_motorbike` là thuộc tính **của riêng từng SV** (khác với `motorbike_ratio` ở cấp lớp trong
  `classes.csv`, vốn chỉ là một tỷ lệ trung bình giả định độc lập). Xác suất SV dùng xe máy được
  gán theo `distance_group` (SV càng xa trường càng có xu hướng cần xe máy vì khó đi bộ/xe đạp):
  near 55%, medium 85%, far 95%.
- **Lưu ý quan trọng:** với dữ liệu chi tiết đến từng SV này, Simulation Engineer nên **ưu tiên
  tính số xe máy thực tế bằng cách JOIN `enrollments.csv` → `students.csv` rồi đếm
  `uses_motorbike`**, thay vì dùng `motorbike_ratio` ở cấp lớp trong `classes.csv` (cột đó giờ chỉ
  còn là một giả định thô/hệ quả lịch sử từ phiên bản trước, được giữ lại để không phá vỡ cấu trúc
  file cũ, nhưng độ chính xác thấp hơn dữ liệu cấp SV mới này).

### 6.3. `enrollments.csv` — Quan hệ ghi danh SV ↔ Lớp học phần
`student_id, class_id`. Đây là bảng trung gian (many-to-many). Cách dùng để dựng lịch trong ngày
của 1 SV: `enrollments.csv` (lọc theo `student_id`) → lấy danh sách `class_id` → JOIN
`schedule.csv` (theo `class_id`) → được đầy đủ (ngày, ca, phòng) của từng buổi học → nhóm theo
`day_of_week` sẽ ra chuỗi ca học liên tiếp/rời rạc trong từng ngày của SV đó.

**Ràng buộc đã đảm bảo ngay từ lúc sinh dữ liệu (không chỉ kiểm tra sau):**
- Số SV ghi danh vào 1 lớp = **chính xác bằng** `num_students` của lớp đó (không xấp xỉ).
- Không SV nào bị ghi danh vào 2 lớp có chung `(day_of_week, shift)`.
- Thuật toán ưu tiên tái sử dụng SV đã tồn tại (còn "chỗ trống" trong tải học tập cá nhân, giả
  định mỗi SV học tối đa 3–7 lớp/tuần, random per SV) trước khi tạo SV mới, để tổng số SV không
  bị phình to không cần thiết. Kết quả: **4.620 SV**, tổng **22.394 lượt ghi danh**, trung bình
  **4.85 lớp/SV** — số liệu này do thuật toán tự xác định (không đặt cứng trước), phụ thuộc vào
  cách 400 lớp học phần chồng chéo lịch với nhau.

### 6.4. `student_behavior.csv` — Cấu hình hành vi rời trường giữa các ca có khoảng trống
`distance_group, gap_0_leave_ratio, gap_1_leave_ratio, gap_2plus_leave_ratio`.

| distance_group | gap_0 | gap_1 | gap_2plus |
|---|---|---|---|
| near | 0.00 | 0.60 | 0.90 |
| medium | 0.00 | 0.30 | 0.75 |
| far | 0.00 | 0.10 | 0.50 |

- `gap_0`: 2 ca học liên tiếp không có ca trống ở giữa (vd Ca2→Ca3) → hầu như không ai lấy xe về.
- `gap_1`: cách nhau đúng 1 ca trống (vd Ca1→Ca3, trống Ca2) → một phần SV có thể về, SV càng gần
  trường xác suất về càng cao (chi phí đi lại thấp).
- `gap_2plus`: cách nhau từ 2 ca trống trở lên (vd Ca1→Ca4) → khả năng rời trường cao hơn hẳn.

Các tỷ lệ trên là **giả định tham khảo ban đầu**, có thể tinh chỉnh khi có số liệu khảo sát thật.
**Đây chỉ là bảng cấu hình (input)** — việc đọc lịch học thực tế của từng SV (qua
`enrollments.csv` → `schedule.csv`), tính khoảng cách (gap) giữa các ca học trong cùng một ngày,
rồi áp tỷ lệ này để quyết định SV có rời trường giữa giờ hay không, là bước xử lý thuộc về
**Simulation Engineer** — dataset không tự tính sẵn kết quả đó.

### 6.5. `validate_data.py` — Bộ kiểm tra toàn vẹn dữ liệu tự động
Chạy độc lập bằng `python3 validate_data.py` (tự định vị các file `.csv` nằm cùng thư mục với
chính nó, không phụ thuộc thư mục làm việc hiện tại). Thực hiện 34 kiểm tra, gồm toàn bộ kiểm tra
hạ tầng/lịch học đã có trước đây (room_id/building khớp, không trùng phòng, không vượt sức chứa,
tham chiếu khoá ngoại hợp lệ...) **cộng thêm 10 kiểm tra mới** :
`student_id` duy nhất; `distance_group`/`uses_motorbike` hợp lệ; `student_id`/`class_id` trong
`enrollments.csv` tồn tại; không trùng cặp (student, class); số lượng ghi danh khớp đúng
`num_students`; **không SV nào bị xếp 2 lớp cùng (ngày, ca)**; `distance_group` trong
`student_behavior.csv` hợp lệ và đủ 3 nhóm; các `leave_ratio` nằm trong [0,1]. In ra PASS/FAIL cho
từng kiểm tra, thoát với exit code khác 0 nếu có bất kỳ FAIL nào (dùng được trong pipeline/CI).

## 7. Cấu trúc & quan hệ giữa các file (xem chi tiết từng cột tại `data_dictionary.csv`)

```
classes.csv (class_id) ──┐
                          ├──< schedule.csv >──┐
rooms.csv (room_id) ─────┘                     │
                                                 └── (building) ── parking.csv (khoảng cách)
events.csv (độc lập, building) ── (building) ── parking.csv (khoảng cách)

students.csv (student_id) ──┐
                             ├──< enrollments.csv >── classes.csv (class_id) ──< schedule.csv
student_behavior.csv (distance_group) ── (distance_group) ── students.csv
```


- `schedule.csv.class_id` → khoá ngoại tới `classes.csv.class_id`
- `schedule.csv.room_id` → khoá ngoại tới `rooms.csv.room_id` (và `building`/`floor`/
  `room_capacity` trong `schedule.csv` luôn là bản sao chính xác từ `rooms.csv`, đã kiểm chứng)
- `events.csv.building` và `schedule.csv.building`/`parking.csv.dist_from_<building>_m` dùng
  chung 4 mã toà: `A2`, `B`, `C`, `D`

## 8. Bằng chứng "vấn đề có thật" (chỉ thống kê thô, không so sánh phương án nào)
| Ngày - Ca | Tổng SV đang học cùng lúc (cộng dồn 4 toà) |
|---|---|
| Thứ 2 - Ca1 | 3.189 |
| Thứ 4 - Ca1 | 2.853 |
| Thứ 6 - Ca3 | 2.443 |
| Thứ 4 - Ca3 | 2.386 |
| Thứ 2 - Ca3 | 2.028 |

Đây là phép đếm cơ học (SUM theo GROUP BY) từ `schedule.csv`, không phải nhận định "đây là
Peak/Worst" .

## 9. Giới hạn của dataset
- Là dữ liệu **mô phỏng/tổng hợp**, không phải thời khoá biểu thật của NEU.
- Chưa mô hình hoá: xe đạp điện/xe đạp, SV đi bộ/xe buýt, ô tô giảng viên, ngày lễ/nghỉ, gửi xe
  ngoài trường.
- `events.csv` hiện chỉ có 1 bộ ví dụ (thi giữa kỳ, Thứ 2 - Ca2 - toà D); muốn thử kịch bản khác
  cần tạo thêm file cùng cấu trúc.
- Phân bố lượng xe đến/đi trong từng khung 30 phút không được áp đặt sẵn.


