import math
from typing import Dict, List, Optional, Set, Tuple

from .models import GridCell, Mission, RouteWaypoint

DEFAULT_UAV_SPEED = 10.0      # m/s

def _center(cell: GridCell) -> Dict:
    return cell.center or {}


def _lat(cell: GridCell) -> float:
    return float(_center(cell).get("lat"))


def _lng(cell: GridCell) -> float:
    return float(_center(cell).get("lng"))


def _meters_between(a: GridCell, b: GridCell) -> float:
    """
    Tính khoảng cách gần đúng theo mét giữa 2 cell.
    Đủ dùng cho mô phỏng đồ án.
    """
    lat1 = _lat(a)
    lng1 = _lng(a)
    lat2 = _lat(b)
    lng2 = _lng(b)

    mean_lat_rad = math.radians((lat1 + lat2) / 2.0)

    dx = (lng2 - lng1) * 111_320.0 * math.cos(mean_lat_rad)
    dy = (lat2 - lat1) * 110_540.0

    return math.sqrt(dx * dx + dy * dy)

def _travel_time_seconds(
    from_cell: GridCell,
    to_cell: GridCell,
    uav_speed_mps: float,
):
    distance = _meters_between(from_cell, to_cell)
    return distance / uav_speed_mps

def _angle_from_a_to_b_deg(a: GridCell, b: GridCell) -> float:
    """
    Quy ước:
    0 độ   = Đông
    90 độ  = Bắc
    180 độ = Tây
    270 độ = Nam
    """
    lat1 = _lat(a)
    lng1 = _lng(a)
    lat2 = _lat(b)
    lng2 = _lng(b)

    mean_lat_rad = math.radians((lat1 + lat2) / 2.0)

    dx = (lng2 - lng1) * 111_320.0 * math.cos(mean_lat_rad)
    dy = (lat2 - lat1) * 110_540.0

    angle = math.degrees(math.atan2(dy, dx))
    return angle % 360.0


def _angle_diff_deg(a: float, b: float) -> float:
    """
    Trả về độ lệch nhỏ nhất giữa 2 góc, nằm trong [0, 180].
    """
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _build_zigzag_cell_route(cells: List[GridCell]) -> List[GridCell]:
    """
    Route baseline không K-means:
    đi toàn bộ grid theo zigzag row/col.
    """
    by_row: Dict[int, List[GridCell]] = {}

    for cell in cells:
        by_row.setdefault(cell.row, []).append(cell)

    route: List[GridCell] = []

    for row in sorted(by_row.keys()):
        row_cells = sorted(by_row[row], key=lambda c: c.col)

        if row % 2 == 1:
            row_cells.reverse()

        route.extend(row_cells)

    return route


def _build_baseline_routes(
    routes_by_uav: Dict[int, Dict],
    cells: List[GridCell],
) -> Dict[int, Dict]:
    """
    Baseline nhiều UAV nhưng KHÔNG dùng K-means.

    Toàn bộ grid được duyệt theo zigzag,
    sau đó chia tuyến thành các đoạn liên tiếp cho các UAV.
    """

    full_route = _build_zigzag_cell_route(cells)

    uav_items = list(routes_by_uav.values())

    if not uav_items:
        return {}

    routes = {}

    total = len(full_route)
    n = len(uav_items)

    chunk = math.ceil(total / n)

    for i, item in enumerate(uav_items):
        start = i * chunk
        end = min((i + 1) * chunk, total)

        routes[item["uav_id"]] = {
            "uav_id": item["uav_id"],
            "uav_name": item["uav_name"],
            "route": full_route[start:end],
        }

    return routes

def _center_key(center: Dict) -> Tuple[float, float]:
    """
    RouteWaypoint đang lưu point thay vì cell_id.
    Ta map ngược point -> GridCell bằng center lat/lng.
    """
    return (
        round(float(center.get("lng")), 7),
        round(float(center.get("lat")), 7),
    )


def _build_routes_by_uav(mission: Mission, cells: List[GridCell]) -> Dict[int, Dict]:
    """
    Trả về:
    {
      uav_id: {
        "uav_id": ...,
        "uav_name": ...,
        "route": [GridCell, GridCell, ...]
      }
    }
    """
    cell_by_center = {
        _center_key(cell.center): cell
        for cell in cells
    }

    routes: Dict[int, Dict] = {}

    waypoints = (
        RouteWaypoint.objects
        .filter(mission=mission)
        .select_related("uav")
        .order_by("uav_id", "order")
    )

    for wp in waypoints:
        cell = cell_by_center.get(_center_key(wp.point))

        if not cell:
            continue

        if wp.uav_id not in routes:
            routes[wp.uav_id] = {
                "uav_id": wp.uav_id,
                "uav_name": wp.uav.name,
                "route": [],
            }

        routes[wp.uav_id]["route"].append(cell)

    return routes


def _auto_radius_m(cells: List[GridCell], ratio: float = 0.35) -> float:
    """
    Tự chọn bán kính mô phỏng theo kích thước vùng.
    Có clamp để tránh quá nhỏ hoặc quá lớn.
    """
    if len(cells) < 2:
        return 500.0

    min_lat = min(_lat(c) for c in cells)
    max_lat = max(_lat(c) for c in cells)
    min_lng = min(_lng(c) for c in cells)
    max_lng = max(_lng(c) for c in cells)

    fake_a = type("PointCell", (), {
        "center": {"lat": min_lat, "lng": min_lng}
    })()
    fake_b = type("PointCell", (), {
        "center": {"lat": max_lat, "lng": max_lng}
    })()

    diag_m = _meters_between(fake_a, fake_b)

    return max(200.0, min(diag_m * ratio, 3000.0))

def _calculate_smoke_concentration(
    source_cell: GridCell,
    target_cell: GridCell,
    smoke_radius_m: float,
) -> float:
    """
    Tính nồng độ khói mô phỏng tại target_cell
    do nguồn cháy tại source_cell tạo ra.

    Đây là mô hình suy giảm nồng độ theo khoảng cách,
    dùng cho mô phỏng và so sánh thuật toán.

    Nồng độ lớn nhất tại nguồn cháy và giảm dần
    khi khoảng cách tới nguồn tăng.
    """

    distance_m = _meters_between(
        source_cell,
        target_cell,
    )

    if distance_m > smoke_radius_m:
        return 0.0

    if distance_m <= 0:
        return 1.0

    concentration = 1.0 - (
        distance_m / smoke_radius_m
    )

    return max(0.0, concentration)

def _is_smoke_detected(
    concentration: float,
    smoke_threshold: float,
) -> bool:
    """
    Kiểm tra UAV có phát hiện khói hay chưa.

    Nếu nồng độ khói lớn hơn hoặc bằng ngưỡng
    thì xem như UAV đã phát hiện khói.
    """

    return concentration >= smoke_threshold


def _build_smoke_cell_ids(
    fire_cell: GridCell,
    cells: List[GridCell],
    wind_direction_deg: float,
    smoke_angle_deg: float,
    smoke_radius_m: float,
) -> Set[int]:
    """
    Sinh vùng khói đơn giản dạng hình quạt xuôi theo hướng gió.

    wind_direction_deg là hướng khói bay tới:
    0 Đông, 90 Bắc, 180 Tây, 270 Nam.
    """
    smoke_ids: Set[int] = set()

    for cell in cells:
        if cell.id == fire_cell.id:
            continue

        distance_m = _meters_between(fire_cell, cell)
        if distance_m > smoke_radius_m:
            continue

        angle = _angle_from_a_to_b_deg(fire_cell, cell)
        diff = _angle_diff_deg(angle, wind_direction_deg)

        if diff <= smoke_angle_deg:
            smoke_ids.add(cell.id)

    return smoke_ids


def _infer_fire_candidates_from_smoke(
    smoke_cell: GridCell,
    cells: List[GridCell],
    wind_direction_deg: float,
    search_angle_deg: float,
    search_radius_m: float,
) -> List[GridCell]:
    """
    Khi UAV phát hiện khói ở smoke_cell,
    nguồn cháy có khả năng nằm ngược chiều gió.
    """
    upwind_direction_deg = (wind_direction_deg + 180.0) % 360.0

    candidates: List[GridCell] = []

    for cell in cells:
        if cell.id == smoke_cell.id:
            continue

        distance_m = _meters_between(smoke_cell, cell)
        if distance_m > search_radius_m:
            continue

        angle = _angle_from_a_to_b_deg(smoke_cell, cell)
        diff = _angle_diff_deg(angle, upwind_direction_deg)

        if diff <= search_angle_deg:
            candidates.append(cell)

    candidates.sort(
        key=lambda c: (
            _meters_between(smoke_cell, c),
            -float(c.risk or 1.0),
            c.id,
        )
    )

    return candidates


def _result(
    strategy: str,
    label: str,
    detected: bool,
    detected_time_seconds: float,
    detected_by_uav: Optional[str],
    checked_cells: int,
    extra: Optional[Dict] = None,
) -> Dict:

    data = {
        "strategy": strategy,
        "label": label,
        "detected": detected,
        "detected_time_seconds": detected_time_seconds,
        "detected_by_uav": detected_by_uav,
        "checked_cells": checked_cells,
    }

    if extra:
        data.update(extra)

    return data


def simulate_no_kmeans(
    routes_by_uav: Dict[int, Dict],
    fire_cell_id: int,
    uav_speed_mps: float,
) -> Dict:
    """
    Chiến lược 1

    Không K-means.
    N UAV chia đều tuyến zigzag và bay song song.

    Thời gian phát hiện = quãng đường UAV đã bay / vận tốc UAV.
    """

    if not routes_by_uav:
        return _result(
            strategy="no_kmeans",
            label="1) Không K-means",
            detected=False,
            detected_time_seconds=None,
            detected_by_uav=None,
            checked_cells=0,
        )

    checked_cells = 0

    # lưu thời gian hiện tại của từng UAV
    uav_times = {
        uav_id: 0.0
        for uav_id in routes_by_uav.keys()
    }

    uav_distances = {
        uav_id: 0.0
        for uav_id in routes_by_uav.keys()
    }

    # lưu cell trước đó của từng UAV
    previous_cells = {
        uav_id: None
        for uav_id in routes_by_uav.keys()
    }

    max_len = max(len(item["route"]) for item in routes_by_uav.values())

    for step in range(max_len):

        for uav_id in sorted(routes_by_uav.keys()):

            item = routes_by_uav[uav_id]
            route = item["route"]

            if step >= len(route):
                continue

            current_cell = route[step]

            # nếu không phải waypoint đầu tiên thì cộng thêm thời gian di chuyển
            if previous_cells[uav_id] is not None:

                distance = _meters_between(
                    previous_cells[uav_id],
                    current_cell,
                )

                uav_distances[uav_id] += distance

                uav_times[uav_id] += distance / uav_speed_mps

            previous_cells[uav_id] = current_cell

            checked_cells += 1

            if current_cell.id == fire_cell_id:

                return _result(
                    strategy="no_kmeans",
                    label="1) Không K-means",
                    detected=True,
                    detected_time_seconds=uav_times[uav_id],
                    detected_by_uav=item["uav_name"],
                    checked_cells=checked_cells,
                    extra={
                        "travel_distance_m": round(
                            uav_distances[uav_id],
                            2,
                        ),
                    },                   
                )

    return _result(
        strategy="no_kmeans",
        label="1) Không K-means",
        detected=False,
        detected_time_seconds=None,
        detected_by_uav=None,
        checked_cells=checked_cells,
    )


def simulate_kmeans(
    routes_by_uav: Dict[int, Dict],
    fire_cell_id: int,
    uav_speed_mps: float,
) -> Dict:
    """
    Chiến lược 2

    Có K-means.
    Mỗi UAV bay theo route của cụm được phân công.

    Thời gian phát hiện = quãng đường UAV đã bay / vận tốc UAV.
    """

    if not routes_by_uav:
        return _result(
            strategy="kmeans",
            label="2) Có K-means",
            detected=False,
            detected_time_seconds=None,
            detected_by_uav=None,
            checked_cells=0,
            extra={
                "detail": "Chưa có route K-means. Hãy Run K-means và Generate Routes trước."
            },
        )

    checked_cells = 0

    # Thời gian đã bay của từng UAV
    uav_times = {
        uav_id: 0.0
        for uav_id in routes_by_uav.keys()
    }

    uav_distances = {
        uav_id: 0.0
        for uav_id in routes_by_uav.keys()
    }

    # Cell trước đó của từng UAV
    previous_cells = {
        uav_id: None
        for uav_id in routes_by_uav.keys()
    }

    max_len = max(len(item["route"]) for item in routes_by_uav.values())

    for step in range(max_len):

        for uav_id in sorted(routes_by_uav.keys()):

            item = routes_by_uav[uav_id]
            route = item["route"]

            if step >= len(route):
                continue

            current_cell = route[step]

            # waypoint đầu tiên không mất thời gian di chuyển
            if previous_cells[uav_id] is not None:

                distance = _meters_between(
                    previous_cells[uav_id],
                    current_cell,
                )

                uav_distances[uav_id] += distance

                uav_times[uav_id] += distance / uav_speed_mps

            previous_cells[uav_id] = current_cell

            checked_cells += 1

            if current_cell.id == fire_cell_id:

                return _result(
                    strategy="kmeans",
                    label="2) Có K-means",
                    detected=True,
                    detected_time_seconds=uav_times[uav_id],
                    detected_by_uav=item["uav_name"],
                    checked_cells=checked_cells,
                    extra={
                        "travel_distance_m": round(
                            uav_distances[uav_id],
                            2,
                        ),
                    },
                )

    return _result(
        strategy="kmeans",
        label="2) Có K-means",
        detected=False,
        detected_time_seconds=None,
        detected_by_uav=None,
        checked_cells=checked_cells,
    )


def simulate_kmeans_wind(
    cells: List[GridCell],
    routes_by_uav: Dict[int, Dict],
    fire_cell: GridCell,
    wind_direction_deg: float,
    uav_speed_mps: float,
    smoke_angle_deg: float,
    smoke_radius_m: float,
    search_angle_deg: float,
    search_radius_m: float,
    smoke_threshold: float,
) -> Dict:
    """
    Chiến lược 3

    K-means + ngưỡng nồng độ khói + hướng gió.

    Quy trình:

        UAV bay theo route K-means
                    ↓
        tính nồng độ khói tại cell
                    ↓
        concentration >= threshold ?
              ↓                 ↓
             NO                YES
              ↓                 ↓
        tiếp tục bay       dùng hướng gió
                               ↓
                     tìm fire candidates
                               ↓
                         tiếp tục kiểm tra

    Hướng gió chỉ được sử dụng SAU KHI UAV
    phát hiện nồng độ khói vượt ngưỡng.
    """

    if not routes_by_uav:
        return _result(
            strategy="kmeans_wind",
            label="3) K-means + ngưỡng khói + hướng gió",
            detected=False,
            detected_time_seconds=None,
            detected_by_uav=None,
            checked_cells=0,
            extra={
                "detail": (
                    "Chưa có route K-means. "
                    "Hãy Run K-means và Generate Routes trước."
                )
            },
        )

    # =========================================================
    # INDEX ROUTE
    # =========================================================

    base_index_by_uav = {
        uav_id: 0
        for uav_id in routes_by_uav.keys()
    }

    # Route ưu tiên được tạo sau khi phát hiện khói
    priority_route_by_uav = {
        uav_id: []
        for uav_id in routes_by_uav.keys()
    }

    # =========================================================
    # THỜI GIAN / QUÃNG ĐƯỜNG
    # =========================================================

    uav_times = {
        uav_id: 0.0
        for uav_id in routes_by_uav.keys()
    }

    uav_distances = {
        uav_id: 0.0
        for uav_id in routes_by_uav.keys()
    }

    previous_cells = {
        uav_id: None
        for uav_id in routes_by_uav.keys()
    }

    # =========================================================
    # THỐNG KÊ
    # =========================================================

    checked_cells = 0

    first_smoke_cell_id = None
    first_smoke_by_uav = None
    first_smoke_concentration = None

    wind_reroute_events = []

    max_steps = (
        max(len(item["route"]) for item in routes_by_uav.values())
        + len(cells)
        + 10
    )

    # =========================================================
    # MÔ PHỎNG
    # =========================================================

    for step in range(max_steps):

        any_uav_moved = False

        for uav_id in sorted(routes_by_uav.keys()):

            item = routes_by_uav[uav_id]

            route = item["route"]

            current_cell = None
            movement_mode = "kmeans_route"

            # -------------------------------------------------
            # Nếu UAV đang có danh sách candidate ưu tiên
            # -------------------------------------------------

            if priority_route_by_uav[uav_id]:

                current_cell = priority_route_by_uav[uav_id].pop(0)

                movement_mode = "wind_candidate"

            # -------------------------------------------------
            # Nếu không có candidate thì tiếp tục route K-means
            # -------------------------------------------------

            else:

                base_index = base_index_by_uav[uav_id]

                if base_index < len(route):

                    current_cell = route[base_index]

                    base_index_by_uav[uav_id] = base_index + 1

            if current_cell is None:
                continue

            any_uav_moved = True

            checked_cells += 1

            # =================================================
            # TÍNH QUÃNG ĐƯỜNG + THỜI GIAN
            # =================================================

            if previous_cells[uav_id] is not None:

                distance = _meters_between(
                    previous_cells[uav_id],
                    current_cell,
                )

                uav_distances[uav_id] += distance

                uav_times[uav_id] += (
                    distance / uav_speed_mps
                )

            previous_cells[uav_id] = current_cell

            # =================================================
            # KIỂM TRA ĐÃ ĐẾN FIRE CELL CHƯA
            # =================================================

            if current_cell.id == fire_cell.id:

                return _result(
                    strategy="kmeans_wind",
                    label="3) K-means + ngưỡng khói + hướng gió",
                    detected=True,
                    detected_time_seconds=uav_times[uav_id],
                    detected_by_uav=item["uav_name"],
                    checked_cells=checked_cells,
                    extra={
                        "smoke_detected": (
                            first_smoke_cell_id is not None
                        ),

                        "first_smoke_cell_id": (
                            first_smoke_cell_id
                        ),

                        "first_smoke_by_uav": (
                            first_smoke_by_uav
                        ),

                        "first_smoke_concentration": (
                            first_smoke_concentration
                        ),

                        "smoke_threshold": smoke_threshold,

                        "movement_mode_when_detected": (
                            movement_mode
                        ),

                        "wind_reroute_events": (
                            wind_reroute_events
                        ),

                        "travel_distance_m": round(
                            uav_distances[uav_id],
                            2,
                        ),
                    },
                )

            # =================================================
            # TÍNH NỒNG ĐỘ KHÓI
            # =================================================

            concentration = _calculate_smoke_concentration(
                source_cell=fire_cell,
                target_cell=current_cell,
                smoke_radius_m=smoke_radius_m,
            )

            # =================================================
            # KIỂM TRA NGƯỠNG KHÓI
            # =================================================

            smoke_detected = _is_smoke_detected(
                concentration=concentration,
                smoke_threshold=smoke_threshold,
            )

            # Không đủ nồng độ → UAV tiếp tục route bình thường
            if not smoke_detected:
                continue

            # =================================================
            # ĐÃ PHÁT HIỆN KHÓI
            # =================================================

            if first_smoke_cell_id is None:

                first_smoke_cell_id = current_cell.id

                first_smoke_by_uav = item["uav_name"]

                first_smoke_concentration = round(
                    concentration,
                    6,
                )

            # =================================================
            # DÙNG HƯỚNG GIÓ ĐỂ TÌM CANDIDATE
            # =================================================

            candidates = _infer_fire_candidates_from_smoke(
                smoke_cell=current_cell,
                cells=item["route"],
                wind_direction_deg=wind_direction_deg,
                search_angle_deg=search_angle_deg,
                search_radius_m=search_radius_m,
            )

            wind_reroute_events.append({
                "uav_id": uav_id,

                "uav_name": item["uav_name"],

                "smoke_cell_id": current_cell.id,

                "smoke_concentration": round(
                    concentration,
                    6,
                ),

                "smoke_threshold": smoke_threshold,

                "candidate_count": len(candidates),

                "candidate_cell_ids": [
                    c.id
                    for c in candidates[:20]
                ],
            })

            # Chỉ tạo route ưu tiên nếu có candidate
            if candidates:

                priority_route_by_uav[uav_id] = candidates

    # =========================================================
    # KHÔNG PHÁT HIỆN FIRE
    # =========================================================

    return _result(
        strategy="kmeans_wind",
        label="3) K-means + ngưỡng khói + hướng gió",
        detected=False,
        detected_time_seconds=None,
        detected_by_uav=None,
        checked_cells=checked_cells,
        extra={
            "smoke_detected": (
                first_smoke_cell_id is not None
            ),

            "first_smoke_cell_id": (
                first_smoke_cell_id
            ),

            "first_smoke_by_uav": (
                first_smoke_by_uav
            ),

            "first_smoke_concentration": (
                first_smoke_concentration
            ),

            "smoke_threshold": (
                smoke_threshold
            ),

            "wind_reroute_events": (
                wind_reroute_events
            ),
        },
    )


# =========================================================
# STRATEGY 4 - K-MEANS + SWARM / PSO
# =========================================================

def _get_swarm_neighbors(
    current_cell: GridCell,
    allowed_cells: List[GridCell],
) -> List[GridCell]:
    """
    Lấy các GridCell lân cận của current_cell.

    UAV chỉ được di chuyển trong allowed_cells,
    tức là cluster K-means được giao cho UAV.

    Ưu tiên các cell có khoảng cách gần current_cell.
    """

    neighbors = []

    for cell in allowed_cells:

        if cell.id == current_cell.id:
            continue

        distance_m = _meters_between(
            current_cell,
            cell,
        )

        # GridCell lân cận:
        # dùng khoảng cách gần nhất trong cluster.
        #
        # Không dùng fire_cell ở đây.
        neighbors.append(
            (distance_m, cell)
        )

    neighbors.sort(
        key=lambda item: (
            item[0],
            item[1].id,
        )
    )

    # Chỉ lấy một số cell gần nhất để mô phỏng
    # việc UAV tìm kiếm trong vùng lân cận.
    return [
        cell
        for _, cell in neighbors[:8]
    ]


def _update_swarm_pbest(
    pbest_by_uav: Dict[int, Dict],
    uav_id: int,
    current_cell: GridCell,
    current_concentration: float,
):
    """
    Cập nhật Pbest của từng UAV.

    Pbest_i là vị trí có nồng độ khói cao nhất
    mà UAV i đã quan sát được.
    """

    current_best = pbest_by_uav.get(uav_id)

    if current_best is None:
        pbest_by_uav[uav_id] = {
            "cell": current_cell,
            "concentration": current_concentration,
        }
        return

    if current_concentration > current_best["concentration"]:
        pbest_by_uav[uav_id] = {
            "cell": current_cell,
            "concentration": current_concentration,
        }


def _get_swarm_gbest(
    pbest_by_uav: Dict[int, Dict],
) -> Optional[Dict]:
    """
    Gbest là Pbest có nồng độ cao nhất
    trong toàn bộ swarm.
    """

    if not pbest_by_uav:
        return None

    valid_items = [
        item
        for item in pbest_by_uav.values()
        if item is not None
    ]

    if not valid_items:
        return None

    return max(
        valid_items,
        key=lambda item: (
            item["concentration"],
            -item["cell"].id,
        ),
    )


def _select_swarm_next_cell(
    current_cell: GridCell,
    current_concentration: float,
    neighbors: List[GridCell],
    pbest: Optional[Dict],
    gbest: Optional[Dict],
    concentration_by_cell: Dict[int, float],
) -> Optional[GridCell]:
    """
    Chọn cell tiếp theo cho UAV trong swarm.

    Logic:

    1. Ưu tiên concentration cao.
    2. Xét Pbest của chính UAV.
    3. Xét Gbest của swarm.
    4. Nếu có nhiều lựa chọn tương đương,
       ưu tiên cell gần UAV hiện tại.

    Đây là phiên bản PSO rời rạc trên GridCell.

    Không sử dụng:
        - fire_cell_id
        - wind_direction_deg
    """

    if not neighbors:
        return None

    scored_candidates = []

    for cell in neighbors:

        concentration = concentration_by_cell.get(
            cell.id,
            0.0,
        )

        # -------------------------------------------------
        # Thành phần concentration
        # -------------------------------------------------

        concentration_score = concentration

        # -------------------------------------------------
        # Thành phần Pbest
        # -------------------------------------------------

        pbest_score = 0.0

        if pbest is not None:

            distance_to_pbest = _meters_between(
                cell,
                pbest["cell"],
            )

            # Càng gần Pbest càng tốt.
            pbest_score = 1.0 / (
                1.0 + distance_to_pbest
            )

        # -------------------------------------------------
        # Thành phần Gbest
        # -------------------------------------------------

        gbest_score = 0.0

        if gbest is not None:

            distance_to_gbest = _meters_between(
                cell,
                gbest["cell"],
            )

            # Càng gần Gbest càng tốt.
            gbest_score = 1.0 / (
                1.0 + distance_to_gbest
            )

        # -------------------------------------------------
        # Xu hướng tăng concentration
        # -------------------------------------------------

        concentration_gain = (
            concentration - current_concentration
        )

        # -------------------------------------------------
        # Fitness tổng hợp
        # -------------------------------------------------
        #
        # Concentration là thành phần chính.
        # Pbest / Gbest tạo hiệu ứng swarm.
        # concentration_gain giúp UAV có xu hướng
        # đi theo vùng khói tăng dần.
        #

        score = (
            0.60 * concentration_score
            + 0.15 * pbest_score
            + 0.15 * gbest_score
            + 0.10 * max(
                concentration_gain,
                0.0,
            )
        )

        scored_candidates.append(
            (
                score,
                concentration,
                -_meters_between(
                    current_cell,
                    cell,
                ),
                cell,
            )
        )

    scored_candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2],
        ),
        reverse=True,
    )

    return scored_candidates[0][3]


def _calculate_cluster_concentrations(
    source_cell: GridCell,
    cells: List[GridCell],
    smoke_radius_m: float,
) -> Dict[int, float]:
    """
    Tính concentration cho các cell trong một cluster.

    Chỉ dùng để mô phỏng môi trường khói.

    Strategy 4 không biết source_cell là gì
    khi quyết định bước di chuyển.
    """

    concentration_by_cell = {}

    for cell in cells:

        concentration = _calculate_smoke_concentration(
            source_cell=source_cell,
            target_cell=cell,
            smoke_radius_m=smoke_radius_m,
        )

        concentration_by_cell[cell.id] = concentration

    return concentration_by_cell


def simulate_kmeans_swarm(
    cells: List[GridCell],
    routes_by_uav: Dict[int, Dict],
    fire_cell: GridCell,
    uav_speed_mps: float,
    smoke_radius_m: float,
    smoke_threshold: float,
) -> Dict:
    """
    Chiến lược 4

    K-means + ngưỡng khói + Swarm / PSO.

    Quy trình:

        K-means
            ↓
        UAV bay trong cluster được giao
            ↓
        concentration >= threshold
            ↓
        kích hoạt swarm
            ↓
        Pbest_i
        Gbest
        C_i
            ↓
        tìm cell có concentration tăng
            ↓
        tiếp tục tìm kiếm
            ↓
        Fire

    Strategy 4 KHÔNG sử dụng wind_direction_deg.

    fire_cell chỉ được sử dụng để tạo
    concentration field mô phỏng và kiểm tra
    kết quả cuối cùng.
    """

    if not routes_by_uav:
        return _result(
            strategy="kmeans_swarm",
            label="4) K-means + Swarm",
            detected=False,
            detected_time_seconds=None,
            detected_by_uav=None,
            checked_cells=0,
            extra={
                "detail": (
                    "Chưa có route K-means. "
                    "Hãy Run K-means và Generate Routes trước."
                ),
            },
        )

    # =========================================================
    # CONCENTRATION FIELD
    # =========================================================

    concentration_by_cell = (
        _calculate_cluster_concentrations(
            source_cell=fire_cell,
            cells=cells,
            smoke_radius_m=smoke_radius_m,
        )
    )

    # =========================================================
    # ROUTE INDEX
    # =========================================================

    base_index_by_uav = {
        uav_id: 0
        for uav_id in routes_by_uav.keys()
    }

    # =========================================================
    # TRẠNG THÁI UAV
    # =========================================================

    current_cell_by_uav = {
        uav_id: None
        for uav_id in routes_by_uav.keys()
    }

    previous_cell_by_uav = {
        uav_id: None
        for uav_id in routes_by_uav.keys()
    }

    # =========================================================
    # PBest
    # =========================================================

    pbest_by_uav = {}

    # =========================================================
    # THỜI GIAN / QUÃNG ĐƯỜNG
    # =========================================================

    uav_times = {
        uav_id: 0.0
        for uav_id in routes_by_uav.keys()
    }

    uav_distances = {
        uav_id: 0.0
        for uav_id in routes_by_uav.keys()
    }

    # =========================================================
    # TRẠNG THÁI SWARM
    # =========================================================

    swarm_active_by_uav = {
        uav_id: False
        for uav_id in routes_by_uav.keys()
    }

    swarm_iterations_by_uav = {
        uav_id: 0
        for uav_id in routes_by_uav.keys()
    }

    # =========================================================
    # THỐNG KÊ
    # =========================================================

    checked_cells = 0

    first_smoke_cell_id = None
    first_smoke_by_uav = None
    first_smoke_concentration = None

    max_swarm_iterations = 100

    # =========================================================
    # MAIN SIMULATION
    # =========================================================

    max_steps = (
        sum(
            len(item["route"])
            for item in routes_by_uav.values()
        )
        + len(cells) * max_swarm_iterations
        + 100
    )

    for step in range(max_steps):

        any_uav_moved = False

        # -----------------------------------------------------
        # UAV LOOP
        # -----------------------------------------------------

        for uav_id in sorted(
            routes_by_uav.keys()
        ):

            item = routes_by_uav[uav_id]

            route = item["route"]

            if not route:
                continue

            # =================================================
            # XÁC ĐỊNH CELL HIỆN TẠI
            # =================================================

            current_cell = (
                current_cell_by_uav[uav_id]
            )

            # -------------------------------------------------
            # CHƯA KÍCH HOẠT SWARM
            # -------------------------------------------------

            if not swarm_active_by_uav[uav_id]:

                base_index = (
                    base_index_by_uav[uav_id]
                )

                if base_index >= len(route):
                    continue

                current_cell = route[base_index]

                base_index_by_uav[uav_id] = (
                    base_index + 1
                )

            # -------------------------------------------------
            # ĐANG Ở SWARM MODE
            # -------------------------------------------------

            else:

                current_concentration = (
                    concentration_by_cell.get(
                        current_cell.id,
                        0.0,
                    )
                )

                neighbors = _get_swarm_neighbors(
                    current_cell=current_cell,
                    allowed_cells=route,
                )

                if not neighbors:
                    continue

                # =============================================
                # PBest
                # =============================================

                pbest = pbest_by_uav.get(
                    uav_id
                )

                # =============================================
                # GBest
                # =============================================

                gbest = _get_swarm_gbest(
                    pbest_by_uav
                )

                # =============================================
                # ĐÁNH GIÁ CONCENTRATION CỦA NEIGHBORS
                # =============================================

                next_cell = _select_swarm_next_cell(
                    current_cell=current_cell,
                    current_concentration=(
                        current_concentration
                    ),
                    neighbors=neighbors,
                    pbest=pbest,
                    gbest=gbest,
                    concentration_by_cell=(
                        concentration_by_cell
                    ),
                )

                if next_cell is None:
                    continue

                current_cell = next_cell

                swarm_iterations_by_uav[
                    uav_id
                ] += 1

            # =================================================
            # UAV DI CHUYỂN
            # =================================================

            any_uav_moved = True

            previous_cell = (
                previous_cell_by_uav[uav_id]
            )

            if previous_cell is not None:

                distance = _meters_between(
                    previous_cell,
                    current_cell,
                )

                uav_distances[uav_id] += (
                    distance
                )

                uav_times[uav_id] += (
                    distance / uav_speed_mps
                )

            previous_cell_by_uav[uav_id] = (
                current_cell
            )

            current_cell_by_uav[uav_id] = (
                current_cell
            )

            checked_cells += 1

            # =================================================
            # KIỂM TRA FIRE
            # =================================================

            if current_cell.id == fire_cell.id:

                return _result(
                    strategy="kmeans_swarm",
                    label="4) K-means + Swarm",
                    detected=True,
                    detected_time_seconds=(
                        uav_times[uav_id]
                    ),
                    detected_by_uav=item["uav_name"],
                    checked_cells=checked_cells,
                    extra={
                        "smoke_detected": (
                            first_smoke_cell_id
                            is not None
                        ),

                        "first_smoke_cell_id": (
                            first_smoke_cell_id
                        ),

                        "first_smoke_by_uav": (
                            first_smoke_by_uav
                        ),

                        "first_smoke_concentration": (
                            first_smoke_concentration
                        ),

                        "smoke_threshold": (
                            smoke_threshold
                        ),

                        "swarm_active": True,

                        "swarm_iterations": (
                            swarm_iterations_by_uav[
                                uav_id
                            ]
                        ),

                        "pbest_concentration": (
                            pbest_by_uav.get(
                                uav_id,
                                {},
                            ).get(
                                "concentration",
                                None,
                            )
                        ),

                        "gbest_concentration": (
                            (
                                _get_swarm_gbest(
                                    pbest_by_uav
                                )
                                or {}
                            ).get(
                                "concentration",
                                None,
                            )
                        ),

                        "travel_distance_m": round(
                            uav_distances[uav_id],
                            2,
                        ),
                    },
                )

            # =================================================
            # CONCENTRATION
            # =================================================

            concentration = (
                concentration_by_cell.get(
                    current_cell.id,
                    0.0,
                )
            )

            # =================================================
            # PBest UPDATE
            # =================================================

            _update_swarm_pbest(
                pbest_by_uav=pbest_by_uav,
                uav_id=uav_id,
                current_cell=current_cell,
                current_concentration=(
                    concentration
                ),
            )

            # =================================================
            # THRESHOLD
            # =================================================

            smoke_detected = _is_smoke_detected(
                concentration=concentration,
                smoke_threshold=smoke_threshold,
            )

            if (
                smoke_detected
                and first_smoke_cell_id is None
            ):

                first_smoke_cell_id = (
                    current_cell.id
                )

                first_smoke_by_uav = (
                    item["uav_name"]
                )

                first_smoke_concentration = (
                    round(
                        concentration,
                        6,
                    )
                )

            # =================================================
            # KÍCH HOẠT SWARM
            # =================================================

            if (
                smoke_detected
                and not swarm_active_by_uav[
                    uav_id
                ]
            ):

                swarm_active_by_uav[
                    uav_id
                ] = True

                swarm_iterations_by_uav[
                    uav_id
                ] = 0

        # -----------------------------------------------------
        # KHÔNG CÒN UAV NÀO DI CHUYỂN
        # -----------------------------------------------------

        if not any_uav_moved:
            break

    # =========================================================
    # KHÔNG TÌM THẤY FIRE
    # =========================================================

    gbest = _get_swarm_gbest(
        pbest_by_uav
    )

    return _result(
        strategy="kmeans_swarm",
        label="4) K-means + Swarm",
        detected=False,
        detected_time_seconds=None,
        detected_by_uav=None,
        checked_cells=checked_cells,
        extra={
            "smoke_detected": (
                first_smoke_cell_id
                is not None
            ),

            "first_smoke_cell_id": (
                first_smoke_cell_id
            ),

            "first_smoke_by_uav": (
                first_smoke_by_uav
            ),

            "first_smoke_concentration": (
                first_smoke_concentration
            ),

            "smoke_threshold": (
                smoke_threshold
            ),

            "swarm_active": any(
                swarm_active_by_uav.values()
            ),

            "gbest_concentration": (
                gbest["concentration"]
                if gbest
                else None
            ),

            "gbest_cell_id": (
                gbest["cell"].id
                if gbest
                else None
            ),

            "swarm_iterations": sum(
                swarm_iterations_by_uav.values()
            ),
        },
    )


def compare_detection_strategies(
    mission: Mission,
    fire_cell_id: int,
    wind_direction_deg: float,
    uav_speed_mps: float = 10.0,
    smoke_angle_deg: float = 35.0,
    smoke_radius_m: Optional[float] = None,
    search_angle_deg: float = 40.0,
    search_radius_m: Optional[float] = None,
    smoke_threshold: float = 0.30,
) -> Dict:
    """
    Hàm chính gọi từ API.

    So sánh 4 chiến lược:

    1. Không K-means
    2. Có K-means
    3. K-means + ngưỡng nồng độ khói + hướng gió
    4. K-means + ngưỡng nồng độ khói + Swarm

    Thời gian được tính theo:

        Time = Distance / UAV Speed

    smoke_threshold:
        Ngưỡng nồng độ khói để UAV xác định
        đã phát hiện tín hiệu khói.

        0.0 <= smoke_threshold <= 1.0
    """

    # =========================================================
    # VALIDATE SMOKE THRESHOLD
    # =========================================================

    if not 0.0 <= smoke_threshold <= 1.0:
        raise ValueError(
            "smoke_threshold phải nằm trong khoảng từ 0 đến 1."
        )

    # =========================================================
    # LOAD GRID CELLS
    # =========================================================

    cells = list(
        GridCell.objects
        .filter(area=mission.area)
        .order_by("row", "col", "id")
    )

    if not cells:
        raise ValueError(
            "Mission area chưa có grid cells."
        )

    cell_by_id = {
        cell.id: cell
        for cell in cells
    }

    # =========================================================
    # FIRE CELL
    # =========================================================

    fire_cell = cell_by_id.get(fire_cell_id)

    if not fire_cell:
        raise ValueError(
            "fire_cell_id không thuộc vùng rừng "
            "của mission hiện tại."
        )

    # =========================================================
    # AUTO SMOKE RADIUS
    # =========================================================

    if smoke_radius_m is None:

        smoke_radius_m = _auto_radius_m(
            cells,
            ratio=0.35,
        )

    # =========================================================
    # AUTO SEARCH RADIUS
    # =========================================================

    if search_radius_m is None:

        search_radius_m = _auto_radius_m(
            cells,
            ratio=0.45,
        )

    # =========================================================
    # ROUTE K-MEANS
    # =========================================================

    routes_by_uav = _build_routes_by_uav(
        mission,
        cells,
    )

    # =========================================================
    # ROUTE BASELINE
    # =========================================================

    baseline_routes = _build_baseline_routes(
        routes_by_uav,
        cells,
    )

    # =========================================================
    # CHẠY 4 CHIẾN LƯỢC
    # =========================================================

    results = [

        # -----------------------------------------------------
        # Strategy 1
        # -----------------------------------------------------

        simulate_no_kmeans(
            routes_by_uav=baseline_routes,
            fire_cell_id=fire_cell_id,
            uav_speed_mps=uav_speed_mps,
        ),

        # -----------------------------------------------------
        # Strategy 2
        # -----------------------------------------------------

        simulate_kmeans(
            routes_by_uav=routes_by_uav,
            fire_cell_id=fire_cell_id,
            uav_speed_mps=uav_speed_mps,
        ),

        # -----------------------------------------------------
        # Strategy 3
        # -----------------------------------------------------

        simulate_kmeans_wind(
            cells=cells,
            routes_by_uav=routes_by_uav,
            fire_cell=fire_cell,
            wind_direction_deg=wind_direction_deg,
            uav_speed_mps=uav_speed_mps,
            smoke_angle_deg=smoke_angle_deg,
            smoke_radius_m=smoke_radius_m,
            search_angle_deg=search_angle_deg,
            search_radius_m=search_radius_m,
            smoke_threshold=smoke_threshold,
        ),

        # -----------------------------------------------------
        # Strategy 4
        # -----------------------------------------------------

        simulate_kmeans_swarm(
            cells=cells,
            routes_by_uav=routes_by_uav,
            fire_cell=fire_cell,
            uav_speed_mps=uav_speed_mps,
            smoke_radius_m=smoke_radius_m,
            smoke_threshold=smoke_threshold,
        ),

    ]

    # =========================================================
    # RESPONSE
    # =========================================================

    return {

        "ok": True,

        "mission_id": mission.id,

        "area_id": mission.area_id,

        "fire_cell_id": fire_cell_id,

        "fire_cell_center": fire_cell.center,

        "wind_direction_deg": wind_direction_deg,

        "uav_speed_mps": uav_speed_mps,

        "params": {

            "smoke_angle_deg": smoke_angle_deg,

            "smoke_radius_m": smoke_radius_m,

            "smoke_threshold": smoke_threshold,

            "search_angle_deg": search_angle_deg,

            "search_radius_m": search_radius_m,

            "wind_direction_note":
                "0=Đông, 90=Bắc, 180=Tây, 270=Nam. "
                "Đây là hướng khói bay tới.",

            "smoke_threshold_note":
                "UAV được xem là phát hiện khói "
                "khi concentration >= smoke_threshold.",

            "time_formula":
                "Time = Distance / UAV Speed",

        },

        "results": results,

    }