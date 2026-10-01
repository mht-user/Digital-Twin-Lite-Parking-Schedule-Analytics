# NEU Digital Twin Lite - Parking & Schedule Analytics

## Giới thiệu chung
Dự án mô phỏng (Simulation) và tối ưu hóa (Optimization) lịch học nhằm giải quyết tình trạng quá tải tại các bãi gửi xe của trường Đại học Kinh tế Quốc dân (NEU). Hệ thống sử dụng dữ liệu giả lập dựa trên thực tế để phát hiện các "điểm nghẽn" (bottleneck) và tự động đưa ra các gợi ý thay đổi lịch học (chuyển ca, chuyển ngày) nhằm cân bằng tải cho các nhà xe.

## Đội ngũ phát triển
| STT | Họ và tên | Vai trò | Trách nhiệm chính |
|---|---|---|---|
| 1 | Trần Hoàng Trung | Data Engineer | Xây dựng bộ dataset mô phỏng (danh sách lớp, sinh viên, hành vi, bãi đỗ xe...); đảm bảo tính toàn vẹn của dữ liệu đầu vào. |
| 2 | Đỗ Mỹ Trang | Simulation Engineer | Mô phỏng luồng sinh viên/xe theo thời gian thực tế, phát hiện quá tải (bottleneck). |
| 3 | Đoàn Anh Tú | Optimization Engineer | Thuật toán tìm kiếm cách sắp xếp lại lịch học, đánh giá và đưa ra phương án chuyển lịch tốt nhất. |
| 4 | Dương Phúc An | Frontend Engineer | Xây dựng Web Dashboard tương tác (Heatmap, Parking View), hiển thị kịch bản Before/After. |
| 5 | Mai Huyền Trâm | System Analyst | Thiết kế tổng thể hệ thống (Workflow, KPI), tích hợp code các module và kiểm thử (Pipeline/Validation). |

## Cấu trúc thư mục (Repository Structure)
```text
.
├── Dataset/                     # Lưu trữ toàn bộ dữ liệu đầu vào và script kiểm tra
│   ├── assumption.md            # Các giả định của hệ thống (tỉ lệ xe máy, khoảng cách...)
│   ├── classes.csv              # Thông tin lớp học
│   ├── enrollments.csv          # Dữ liệu sinh viên đăng ký lớp học
│   ├── events.csv               # Dữ liệu sự kiện tác động đến bãi xe
│   ├── parking.csv              # Thông tin các nhà xe (sức chứa, phân bổ)
│   ├── rooms.csv                # Dữ liệu phòng học
│   ├── schedule.csv             # Lịch học chi tiết
│   ├── student_behavior.csv     # Cấu hình hành vi sinh viên (ở lại hay về giữa các ca)
│   ├── students.csv             # Danh sách sinh viên
│   └── validate_data.py         # Script kiểm tra tính hợp lệ của dữ liệu đầu vào
├── optimization/                # Module Tối ưu hóa
│   ├── optimizer.py             # Thuật toán sinh candidate và đánh giá lịch học
│   ├── run_optimizer.py         # Chạy module tối ưu độc lập
│   ├── se_bridge.py             # Cầu nối gọi dữ liệu từ Simulation sang Optimization
│   └── test_optimizer.py        # Kịch bản test riêng cho Optimization
├── .gitignore                   # File loại trừ git
├── README.md                    # Tài liệu giới thiệu dự án (File này)
├── SRS_DigitalTwinLite_NhaXeNEU # Đặc tả yêu cầu phần mềm (SRS)
├── dashboard_data.py            # Xử lý và chuẩn bị dữ liệu gửi lên Dashboard
├── run_simulation.py            # Module Mô phỏng chính
├── serve_dashboard.py           # Backend API đóng vai trò Server
└── test_pipeline.py             # Kịch bản kiểm thử tích hợp toàn bộ hệ thống
