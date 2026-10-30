# Forest Fire UAV — Mô phỏng tìm kiếm cháy rừng và benchmark 4 thuật toán

Hệ thống gồm **Django REST Framework + SQLite** (backend) và **React + Vite + Leaflet** (frontend). Người dùng vẽ một vùng rừng trên bản đồ, sinh các ô lưới, tạo nhiệm vụ (Mission), chia cụm K-means, tạo tuyến bay và chạy thực nghiệm so sánh thời gian UAV tìm đến đúng ô chứa đám cháy.

> **Phạm vi nghiên cứu:** Mô phỏng 2D, không phải phần mềm điều khiển UAV thật. Thời gian tìm cháy là thời điểm một UAV **hoàn thành quan sát đúng cell cháy**; đây không phải độ trễ xác nhận cháy bằng AI/cảm biến ngoài thực địa. Thuật toán 4 là biến thể lấy cảm hứng từ ACAK-LoPSO, có thể bổ sung chống lặp/BFS tùy phiên bản `experiment_services.py`.

## 1. Chuẩn bị

Cài đặt trên Windows 11 (có thể dùng hệ điều hành khác với lệnh kích hoạt môi trường tương ứng):

- **Git** để clone repository.
- **Python 3.11 hoặc 3.12**, kèm `pip` (khuyến nghị dùng một phiên bản phù hợp với `backend/requirements.txt`).
- **Node.js 20 LTS hoặc mới hơn tương thích với Vite** và `npm`.
- Trình duyệt có Internet để hiển thị nền bản đồ Leaflet/OpenStreetMap.

Kiểm tra trong terminal:

```powershell
git --version
python --version
python -m pip --version
node --version
npm --version
```

## 2. Clone repository

```powershell
git clone https://github.com/vuhuy1804/TTCS_forest_fire_uav.git
cd TTCS_forest_fire_uav
```

Cấu trúc chính:

```text
TTCS_forest_fire_uav/
├── backend/
│   ├── config/
│   ├── core/
│   │   ├── experiment_services.py   # Mô phỏng 4 chiến lược
│   │   ├── simulation_services.py   # Định dạng kết quả benchmark API
│   │   ├── services.py              # Sinh grid, K-means và tuyến zigzag
│   │   ├── views.py, urls.py, models.py
│   │   └── migrations/
│   ├── manage.py
│   ├── requirements.txt
│   └── run_experiments.py          # Benchmark hàng loạt, xuất CSV
└── frontend/
    ├── package.json
    └── src/
        ├── App.jsx                 # Giao diện bản đồ, tạo khu vực/Mission
        └── SimulationPage.jsx      # Giao diện chạy benchmark đơn
```

**Lưu ý về phiên bản:** README mô tả cấu trúc dự án đã được kiểm tra cùng bản `run_experiments.py` mở rộng có các tùy chọn `--seed`, `--horizon`. Nếu nhánh bạn vừa clone vẫn dùng file script cũ, lệnh cơ bản ở mục 7 vẫn chạy, nhưng các tùy chọn bổ sung chỉ sử dụng được sau khi cập nhật script đó.

## 3. Cài đặt và chạy backend Django

Mở **terminal 1**, từ thư mục gốc repository:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py check
python manage.py runserver 127.0.0.1:8000
```

Backend mặc định chạy tại `http://127.0.0.1:8000/`, các API ở tiền tố `http://127.0.0.1:8000/api/`.

Nếu PowerShell chặn `Activate.ps1`, có thể dùng Command Prompt (CMD) với:

```bat
.venv\Scripts\activate.bat
```

Hoặc không cần kích hoạt môi trường, gọi trực tiếp `\.venv\Scripts\python.exe` thay cho `python` trong các lệnh backend.

**Quan trọng:** `backend/db.sqlite3` nằm trong `.gitignore`, nên máy vừa clone thường chưa có dữ liệu khu vực rừng, Mission hoặc UAV. `python manage.py migrate` tạo cấu trúc cơ sở dữ liệu, **không tự sinh khu vực rừng**. Bạn phải thực hiện các bước ở mục 5.

## 4. Cài đặt và chạy frontend React

Giữ terminal backend đang chạy. Mở **terminal 2** ở thư mục gốc repository:

```powershell
cd frontend
npm ci
npm run dev
```

Truy cập địa chỉ Vite hiển thị trên terminal, thường là **http://localhost:5173/**.

Frontend hiện gọi backend tại `http://127.0.0.1:8000/api` (trong `frontend/src/api.js` và `frontend/src/SimulationPage.jsx`). Nếu đổi cổng backend, phải cập nhật các địa chỉ này.

## 5. Tạo khu vực rừng, lưới ô và Mission

Thực hiện trên trang chính **`http://localhost:5173/`** theo đúng thứ tự sau:

1. **Vẽ vùng rừng:** Trên bản đồ Leaflet, sử dụng công cụ **Draw polygon** ở góc trái, nhấp các đỉnh của vùng rừng và hoàn thành polygon.
2. Nhấn **`1) Create Area from Polygon`**. Hệ thống tạo `ForestArea` và hiện thông báo `Created area <ID>`.
3. Nhập **Rows** và **Cols** (ví dụ `20` × `20`), nhấn **`2) Generate Grid (inside polygon)`**. Chương trình chỉ giữ các cell có **tâm nằm trong polygon**, nên số cell thực tế có thể **ít hơn 400**.
4. Nhập **K (UAV)**, ví dụ `5` hoặc `10` (K là số cụm và số UAV của Mission). K phải không vượt quá số cell hợp lệ.
5. Nhấn **`3) Create Mission`**. Ghi lại ID được báo, ví dụ `Created mission 17`. **17 chỉ là ví dụ**, Mission ID thực tế trên máy mới có thể là `1`.
6. Nhấn **`4) Run K-means`** để phân cụm, phân công UAV.
7. Nhấn **`5) Generate Routes (zigzag)`** để sinh các waypoint tuần tra của UAV. Đợi thông báo thành công.
8. Nhấn **Refresh** để kiểm tra các vùng, UAV và đường bay xuất hiện trên bản đồ.

**Không cần bấm Start Mission để chạy benchmark qua `run_experiments.py`.** Benchmark đọc Mission, grid và các tuyến đã tạo trong cơ sở dữ liệu; nó chạy mô phỏng độc lập. Chức năng Start/Pause/Auto Simulation trên giao diện bản đồ là một luồng mô phỏng trạng thái khác.

**Lưu ý:** Sinh grid lại sẽ xóa các GridCell cũ của cùng ForestArea; do đó các phân công/tuyến cũ có thể không còn hợp lệ. Nếu thay đổi hình học hoặc mật độ lưới, hãy tạo lại Mission, chạy lại K-means và Generate Routes.

## 6. Xác định Mission ID và số cell thực tế

Mission ID không phải Area ID. Nếu quên Mission ID, mở terminal backend (đã kích hoạt `.venv`), chạy:

```powershell
python manage.py shell
```

Trong Python shell:

```python
from core.models import Mission, GridCell, RouteWaypoint
for m in Mission.objects.all().order_by('id'):
    print('Mission:', m.id,
          '| Area:', m.area_id,
          '| K:', m.k,
          '| Cells:', GridCell.objects.filter(area=m.area).count(),
          '| Waypoints:', RouteWaypoint.objects.filter(mission=m).count())
```

Gõ `exit()` để thoát. Đảm bảo Mission cần benchmark có các waypoint được tạo. Nếu có 0 waypoint, quay lại giao diện chạy K-means và Generate Routes.

## 7. Chạy benchmark 4 thuật toán

Mở **terminal 3** hoặc terminal khác ở `backend` (không cần dừng backend/frontend):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
```

### Chạy 5 vị trí cháy mặc định

```powershell
python run_experiments.py <mission_id>
VD: python run_experiments.py 17
```

### Chạy 100 vị trí cháy

```powershell
python run_experiments.py <mission_id> 100 <so_vi_tri_chay>
VD: python run_experiments.py 17 100
```

**Thay `17` bằng Mission ID thực tế.** Tham số thứ hai (`100`) ghi đè `DEFAULT_FIRE_LOCATIONS = 5`; nó **không bị giới hạn ở 5**. Số vị trí cháy yêu cầu phải không vượt quá số GridCell thực tế của khu vực.

Mỗi vị trí cháy được chạy với cả bốn chiến lược:

| STT | Chiến lược | Ý nghĩa |
|---|---|---|
| 1 | Không K-means | Tuần tra zigzag theo phân đoạn lưới toàn vùng |
| 2 | Có K-means | Tuần tra trong cụm được giao |
| 3 | K-means + ngưỡng khói + hướng gió | Gặp khói đủ ngưỡng thì tìm ứng viên ngược gió |
| 4 | K-means + ngưỡng khói + Swarm/PSO | Điều tra cục bộ theo tín hiệu khói, fitness và bộ nhớ hướng |

### Các tùy chọn của script benchmark bản mở rộng

Nếu `python run_experiments.py --help` hiển thị các option dưới đây, bạn có thể dùng:

```powershell
python run_experiments.py 17 100 --seed 42 --wind 225 --speed 10 --threshold 0.50 --horizon 7200
```

| Tham số | Ý nghĩa |
|---|---|
| `17` | Mission ID |
| `100` | Số cell cháy khác nhau cần thử |
| `--seed 42` | Cố định cách chọn vị trí cháy để tái lập bộ kịch bản |
| `--wind 225` | Hướng khói bay tới, tính bằng độ: 0=Đông, 90=Bắc, 180=Tây, 270=Nam |
| `--speed 10` | Tốc độ UAV, mét/giây |
| `--threshold 0.50` | Ngưỡng phát hiện khói chuẩn hóa |
| `--horizon 7200` | Giới hạn thời gian **toàn nhiệm vụ** là 7200 giây (tùy chọn) |

Nếu không khai báo `--horizon`, script mới mặc định không áp đặt thời hạn giám sát chung; **ngân sách SEARCH cục bộ 600 giây là giới hạn khác**. Sử dụng một `--horizon` cố định khi muốn thống kê tỷ lệ thành công trong cùng thời hạn.

> Nếu lệnh báo `unrecognized arguments: --seed` hoặc `--horizon`, bạn đang dùng `run_experiments.py` đời cũ trên nhánh Git hiện tại. Cách chạy `python run_experiments.py <MISSION_ID> <SO_VI_TRI_CHAY>` vẫn dùng được; cần cập nhật đúng phiên bản script trước khi dùng các tùy chọn mới.

## 8. Đọc kết quả thực nghiệm

Các file đầu ra nằm trong **`backend/benchmark_data/`**. Với script mở rộng, mỗi lần chạy tạo các file mang tên tương tự:

```text
backend/benchmark_data/
├── mission_17_detail_<timestamp>.csv
├── mission_17_summary_<timestamp>.csv
└── mission_17_config_<timestamp>.json
```

- **`detail`**: Một dòng cho mỗi cặp (kịch bản cháy, thuật toán), kèm trạng thái phát hiện, thời gian, quãng đường, số lượt quan sát, số lần PSO lặp/phục hồi... hoặc lỗi.
- **`summary`**: Bảng tổng hợp theo thuật toán, gồm số thành công/thất bại, tỷ lệ thành công và thời gian trung bình **trong những lần thành công**.
- **`config`**: Tham số và danh sách `fire_cell_id` đã thử, dùng để đối chiếu và tái lập thí nghiệm.

**Khi đọc thống kê:**

- `detected_time_seconds` = thời điểm UAV đầu tiên hoàn thành quan sát **đúng cell cháy**; không phải latency AI.
- `checked_cells` = **số lượt quan sát của toàn đội UAV**, có thể gồm ô thăm lại, không nhất thiết là số ô duy nhất.
- Không coi trường hợp không tìm thấy cháy là thời gian 0. Kiểm tra riêng **tỷ lệ thành công** và các timeout.
- `--seed 42` chỉ cố định cách chọn cell cháy trong script mở rộng; 100 cell cháy khác nhau **không tương đương** 100 kịch bản môi trường độc lập có nhiễu cảm biến và gió thay đổi.
- Không so sánh trực tiếp hai bảng tổng hợp nếu **Mission, danh sách cell cháy, tuyến UAV, tham số gió/khói hoặc phiên bản thuật toán** khác nhau.
- Bản mô phỏng ACAK-LoPSO-2D có thể dùng exploration guard/BFS, là **mở rộng của nhóm**, không phải các bước nguyên bản của Algorithm A1 trong paper.

## 9. Chạy thử một kịch bản trên giao diện web

Truy cập **`http://localhost:5173/simulation`**.

1. Nhập **Mission ID** đã tạo (trang có thể hiển thị sẵn một ID ví dụ; cần sửa lại).
2. Nhập **Fire Cell ID** thực sự thuộc khu vực đó. Có thể tìm ID cell bằng `python manage.py shell`:

   ```python
   from core.models import Mission, GridCell
   m = Mission.objects.get(id=17)  # thay 17 bằng ID của bạn
   print(list(GridCell.objects.filter(area=m.area).values_list('id', flat=True)[:20]))
   ```

3. Chọn hướng gió, tốc độ, góc khói, bán kính và ngưỡng khói.
4. Chạy mô phỏng và xem kết quả trả về cho bốn chiến lược.

Trang này gọi API `/api/missions/<MISSION_ID>/benchmark/`. Nó chạy **một vị trí cháy**, khác với script `run_experiments.py` chạy hàng loạt và xuất CSV.

## 10. Lỗi thường gặp

| Hiện tượng | Cách kiểm tra/khắc phục |
|---|---|
| `ModuleNotFoundError: django` | Đang dùng sai Python; kích hoạt `.venv` rồi `python -m pip install -r requirements.txt` |
| `no such table: core_mission` | Chưa chạy `python manage.py migrate` trong `backend` |
| `Mission matching query does not exist` | Mission ID không tồn tại trong **database trên máy này**; xem mục 6 hoặc tạo Mission mới |
| `Mission area chưa có grid cells` | Chưa Generate Grid, hoặc chọn sai Mission |
| `Chưa có route hợp lệ` / 0 waypoint | Chạy **Run K-means** rồi **Generate Routes** trước benchmark |
| Yêu cầu 100 vị trí nhưng không đủ cell | Tạo khu vực lớn hơn hoặc tăng Rows/Cols, sau đó sinh grid và Mission mới |
| Frontend `Failed to fetch` / lỗi CORS/network | Xác nhận Django chạy tại `127.0.0.1:8000`; frontend hiện hardcode URL này |
| `unrecognized arguments: --seed` | Phiên bản script benchmark trong Git chưa hỗ trợ option đó |
| Không thấy bản đồ nền | Kiểm tra Internet và khả năng truy cập dịch vụ tile của Leaflet |
| Thời gian thuật toán 4 lớn | Kiểm tra `pso_revisits`, `pso_recovery_steps`, `search_triggers`, cấu hình khói và tỷ lệ thành công; không tự ép kết quả |

## 11. Thông tin kỹ thuật và giới hạn

- Backend: Django 5.x, Django REST Framework, CORS headers, NumPy và scikit-learn (xem phiên bản hỗ trợ trong `backend/requirements.txt`).
- Frontend: React, Vite, Leaflet, React Leaflet và Leaflet Geoman.
- Cơ sở dữ liệu phát triển: SQLite, file `backend/db.sqlite3` được tạo cục bộ và không theo Git.
- K-means chạy trên tâm các cell; tuyến tuần tra zigzag được sinh cho các cụm.
- Mô hình khói: hình quạt tĩnh, suy giảm nồng độ theo khoảng cách; không mô phỏng CFD, ROS 2/Gazebo hoặc động học UAV 3D.
- Cần chạy nhiều kịch bản ghép cặp, báo cáo cả thất bại và các giới hạn trước khi dùng kết quả trong bài báo.
- Các thiết lập Django trong repository (ví dụ `DEBUG=True`, secret key phát triển, CORS mở) **chỉ dùng cho môi trường phát triển cục bộ**, không triển khai công khai trên Internet khi chưa cấu hình bảo mật.

---

**Tóm tắt lệnh nhanh (sau khi đã tạo Area → Grid → Mission → K-means → Routes):**

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python run_experiments.py 17 100
```

`17` là ví dụ; thay bằng **Mission ID** trên máy bạn.
