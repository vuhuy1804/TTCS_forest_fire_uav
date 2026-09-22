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
) -> Dict:
    """
    Chiến lược 3

    K-means + hướng gió.

    UAV bay theo route K-means.
    Khi gặp cell có khói sẽ ưu tiên kiểm tra các candidate
    nằm ngược chiều gió.
    """

    if not routes_by_uav:
        return _result(
            strategy="kmeans_wind",
            label="3) K-means + hướng gió",
            detected=False,
            detected_time_seconds=None,
            detected_by_uav=None,
            checked_cells=0,
            extra={
                "detail": "Chưa có route K-means. Hãy Run K-means và Generate Routes trước."
            },
        )

    smoke_cell_ids = _build_smoke_cell_ids(
        fire_cell=fire_cell,
        cells=cells,
        wind_direction_deg=wind_direction_deg,
        smoke_angle_deg=smoke_angle_deg,
        smoke_radius_m=smoke_radius_m,
    )

    base_index_by_uav = {
        uav_id: 0
        for uav_id in routes_by_uav.keys()
    }

    priority_route_by_uav = {
        uav_id: []
        for uav_id in routes_by_uav.keys()
    }

    # ==========================
    # THÊM PHẦN TÍNH THỜI GIAN
    # ==========================

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

    # ==========================

    checked_cells = 0

    first_smoke_cell_id = None
    first_smoke_by_uav = None

    wind_reroute_events = []

    max_steps = (
        max(len(item["route"]) for item in routes_by_uav.values())
        + len(cells)
        + 10
    )

    for step in range(max_steps):

        any_uav_moved = False

        for uav_id in sorted(routes_by_uav.keys()):

            item = routes_by_uav[uav_id]

            route = item["route"]

            current_cell = None
            movement_mode = "kmeans_route"

            if priority_route_by_uav[uav_id]:

                current_cell = priority_route_by_uav[uav_id].pop(0)
                movement_mode = "wind_candidate"

            else:

                base_index = base_index_by_uav[uav_id]

                if base_index < len(route):

                    current_cell = route[base_index]

                    base_index_by_uav[uav_id] = base_index + 1

            if current_cell is None:
                continue

            any_uav_moved = True
            checked_cells += 1

            # ==================================
            # CỘNG THỜI GIAN DI CHUYỂN
            # ==================================

            if previous_cells[uav_id] is not None:

                distance = _meters_between(
                    previous_cells[uav_id],
                    current_cell,
                )

                uav_distances[uav_id] += distance

                uav_times[uav_id] += distance / uav_speed_mps

            previous_cells[uav_id] = current_cell

            # ==================================

            if current_cell.id == fire_cell.id:

                return _result(
                    strategy="kmeans_wind",
                    label="3) K-means + hướng gió",
                    detected=True,
                    detected_time_seconds=uav_times[uav_id],
                    detected_by_uav=item["uav_name"],
                    checked_cells=checked_cells,
                    extra={
                        "smoke_detected": first_smoke_cell_id is not None,
                        "first_smoke_cell_id": first_smoke_cell_id,
                        "first_smoke_by_uav": first_smoke_by_uav,
                        "movement_mode_when_detected": movement_mode,
                        "smoke_cell_count": len(smoke_cell_ids),
                        "wind_reroute_events": wind_reroute_events,
                        "travel_distance_m": round(
                            uav_distances[uav_id],
                            2,
                        ),
                    },
                )

            if (
                current_cell.id in smoke_cell_ids
                and not priority_route_by_uav[uav_id]
            ):

                if first_smoke_cell_id is None:
                    first_smoke_cell_id = current_cell.id
                    first_smoke_by_uav = item["uav_name"]

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
                    "candidate_count": len(candidates),
                    "candidate_cell_ids": [
                        c.id for c in candidates[:20]
                    ],
                })

                priority_route_by_uav[uav_id] = candidates

        if not any_uav_moved:
            break

    return _result(
        strategy="kmeans_wind",
        label="3) K-means + hướng gió",
        detected=False,
        detected_time_seconds=None,
        detected_by_uav=None,
        checked_cells=checked_cells,
        extra={
            "smoke_detected": first_smoke_cell_id is not None,
            "first_smoke_cell_id": first_smoke_cell_id,
            "first_smoke_by_uav": first_smoke_by_uav,
            "smoke_cell_count": len(smoke_cell_ids),
            "wind_reroute_events": wind_reroute_events,
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
) -> Dict:
    """
    Hàm chính gọi từ API.

    So sánh 3 chiến lược:

    1. Không K-means
    2. Có K-means
    3. K-means + hướng gió

    Thời gian được tính theo:
        Time = Distance / UAV Speed
    """

    cells = list(
        GridCell.objects
        .filter(area=mission.area)
        .order_by("row", "col", "id")
    )

    if not cells:
        raise ValueError("Mission area chưa có grid cells.")

    cell_by_id = {
        cell.id: cell
        for cell in cells
    }

    fire_cell = cell_by_id.get(fire_cell_id)

    if not fire_cell:
        raise ValueError(
            "fire_cell_id không thuộc vùng rừng của mission hiện tại."
        )

    if smoke_radius_m is None:
        smoke_radius_m = _auto_radius_m(
            cells,
            ratio=0.35,
        )

    if search_radius_m is None:
        search_radius_m = _auto_radius_m(
            cells,
            ratio=0.45,
        )

    # Route K-means
    routes_by_uav = _build_routes_by_uav(
        mission,
        cells,
    )

    # Route baseline
    baseline_routes = _build_baseline_routes(
        routes_by_uav,
        cells,
    )

    results = [

        simulate_no_kmeans(
            routes_by_uav=baseline_routes,
            fire_cell_id=fire_cell_id,
            uav_speed_mps=uav_speed_mps,
        ),

        simulate_kmeans(
            routes_by_uav=routes_by_uav,
            fire_cell_id=fire_cell_id,
            uav_speed_mps=uav_speed_mps,
        ),

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
        ),

    ]

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

            "search_angle_deg": search_angle_deg,

            "search_radius_m": search_radius_m,

            "wind_direction_note":
                "0=Đông, 90=Bắc, 180=Tây, 270=Nam. Đây là hướng khói bay tới.",

            "time_formula":
                "Time = Distance / UAV Speed",

        },

        "results": results,

    }