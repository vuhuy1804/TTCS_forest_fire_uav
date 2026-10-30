import math
import heapq
from collections import deque
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
            # Same launch position across all four methods, if available.
            "launch_cell": item.get("launch_cell") or (
                item["route"][0] if item.get("route") else None
            ),
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

    for item in routes.values():
        # No launch coordinates in this model: the first K-means waypoint is
        # the declared initial position. Baseline inherits that same start.
        item["launch_cell"] = item["route"][0] if item["route"] else None
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
    wind_direction_deg: Optional[float] = None,
    smoke_angle_deg: float = 35.0,
) -> float:
    """
    Tính nồng độ khói mô phỏng tại target_cell do nguồn cháy tại source_cell tạo ra.
    Khói lan theo hình quạt dạt xuôi theo hướng gió (wind_direction_deg).
    """
    distance_m = _meters_between(source_cell, target_cell)

    if distance_m > smoke_radius_m:
        return 0.0

    if distance_m <= 0:
        return 1.0

    # Nếu có thông số hướng gió, kiểm tra xem target_cell có nằm trong vệt khói xuôi chiều gió không
    if wind_direction_deg is not None:
        angle_to_target = _angle_from_a_to_b_deg(source_cell, target_cell)
        angle_diff = _angle_diff_deg(angle_to_target, wind_direction_deg)

        # Nếu ô nằm ngoài góc quạt vệt khói do gió thổi -> Nồng độ khói bằng 0
        if angle_diff > smoke_angle_deg:
            return 0.0

    # Nồng độ khói suy giảm theo khoảng cách
    concentration = 1.0 - (distance_m / smoke_radius_m)

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

    return candidates[:5]


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


# =============================================================
# FOUR CONTROLLERS — matched 2D grid experiment
# =============================================================
# Source: ACAK-LoPSO, Sections IV–V and Appendices A/B.
# NOTE: this 2-D simulator does NOT implement canopy/3-D path safety,
# jetson inference, real plume dynamics, or physical sensor response.
# The result named "detected" means source-grid-cell observation, not
# evidence-verified source localization. Do not rename it "AI recall".
#
# Four policies share identical observation/travel rules and plume truth.
# The plume evaluator can ONLY be queried at an actually observed cell.

OBSERVATION_SECONDS = 1.0           # fixed modelled sensor acquisition time
LOCAL_SEARCH_BUDGET_SECONDS = 600.0 # proposed 10-minute search + verification budget
MEMORY_WINDOW_SECONDS = 30.0        # paper's local sensor observation window
CONCENTRATION_BACKGROUND = 0.0
CONCENTRATION_SCALE = 0.25          # chosen simulation scaling; not calibrated
MAX_RECORDED_EVENTS = 250           # avoid excessively large API responses
# Optional common monitoring horizon; None keeps legacy until-route-end behavior.
DEFAULT_MISSION_HORIZON_SECONDS = None


def _bounded_smoke_score(concentration: float) -> float:
    """Eq. (4): xi = max((C-Cbg)/C0,0), normalized g/2 = xi/(1+xi)."""
    xi = max(0.0, (float(concentration) - CONCENTRATION_BACKGROUND) / CONCENTRATION_SCALE)
    return xi / (1.0 + xi)


def _grid_neighbors(current_cell: GridCell, allowed_cells: List[GridCell],
                    already_observed: Set[int]) -> List[GridCell]:
    """Paper A1: at most eight adjacent in-region cells, INCLUDING revisits.

    The original implementation excluded visited cells and made the paper's
    revisit penalty unreachable. Grid adjacency is checked by row/column.
    """
    result = [c for c in allowed_cells
              if c.id != current_cell.id
              and max(abs(int(c.row)-int(current_cell.row)),
                      abs(int(c.col)-int(current_cell.col))) == 1]
    return sorted(result, key=lambda c: (_meters_between(current_cell, c), c.id))[:8]


def _local_observation_estimate(target: GridCell, observations: List[Tuple],
                                timestamp: float, length_scale_m: float,
                                wind_direction_deg: float = 0.0,
                                wind_speed_mps: float = 0.0) -> float:
    """Paper Eq.(21): finite-memory time/distance weighted concentration.

    A sample can be transported *forward* along wind transport direction.
    Wind speed defaults to ZERO because the existing static cone simulation
    supplies direction but no time-dependent flow velocity. Nonzero wind speed
    should only be selected with a consistent time-dependent plume model.
    """
    numerator = denominator = 0.0
    length2 = max(1e-6, float(length_scale_m))**2
    angle = math.radians(wind_direction_deg)
    for sample_time, sample_cell, sample_concentration in observations:
        age = timestamp-sample_time
        if not 0 <= age <= MEMORY_WINDOW_SECONDS:
            continue
        sample_east, sample_north = _relative_meters(target, sample_cell)
        east = sample_east + wind_speed_mps*age*math.cos(angle)
        north = sample_north + wind_speed_mps*age*math.sin(angle)
        weight = math.exp(-age/MEMORY_WINDOW_SECONDS)/(1.0+(east*east+north*north)/length2)
        numerator += weight*max(0.0, float(sample_concentration))
        denominator += weight
    return numerator/denominator if denominator > 0 else 0.0


def _relative_meters(a: GridCell, b: GridCell) -> Tuple[float, float]:
    """Metric east/north offset from a to b."""
    middle_lat = math.radians((_lat(a)+_lat(b))/2.0)
    return ((_lng(b)-_lng(a))*111_320.0*math.cos(middle_lat),
            (_lat(b)-_lat(a))*110_540.0)


def _cell_spacing_m(cells: List[GridCell], current: GridCell) -> float:
    """Grid spacing Δ for paper's l=2Δ interpolation bandwidth."""
    distances = [_meters_between(current, c) for c in cells
                 if c.id != current.id and
                 max(abs(int(c.row)-int(current.row)),
                     abs(int(c.col)-int(current.col))) == 1]
    return min(distances) if distances else 1.0


def _vector_direction(a: GridCell, b: GridCell) -> Tuple[float, float]:
    """Unit (east, north) direction from a to b in a local metric plane."""
    middle_lat = math.radians((_lat(a)+_lat(b))/2.0)
    east = (_lng(b)-_lng(a))*111_320.0*math.cos(middle_lat)
    north = (_lat(b)-_lat(a))*110_540.0
    length = math.hypot(east, north)
    if length <= 1e-9:
        return (0.0, 0.0)
    return (east/length, north/length)


def _upwind_candidates_for_observation(
    current_cell: GridCell, allowed_cells: List[GridCell],
    observed_ids: Set[int], wind_direction_deg: float,
    search_angle_deg: float, search_radius_m: float,
) -> List[GridCell]:
    """Eq. (12), candidate search upwind; constrained to assigned region."""
    upwind = (wind_direction_deg + 180.0) % 360.0
    candidates = []
    for c in allowed_cells:
        if c.id in observed_ids:
            continue
        distance = _meters_between(current_cell, c)
        if distance <= 0 or distance > search_radius_m:
            continue
        if _angle_diff_deg(_angle_from_a_to_b_deg(current_cell, c), upwind) <= search_angle_deg:
            candidates.append(c)
    candidates.sort(key=lambda c: (
        _meters_between(current_cell, c), -float(c.risk or 1.0), c.id
    ))
    return candidates


def _pso_recovery_step(state: Dict) -> Optional[GridCell]:
    """Optional *2D simulation* escape from a fully observed neighborhood.

    BFS uses only cluster cells and the UAV's own visited set; it never reads
    the hidden fire location or plume. This is NOT a step in paper Algorithm A1.
    Each returned step is adjacent, and its travel time is charged normally.
    """
    current = state['current']
    by_position = {(int(c.row), int(c.col)): c for c in state['route']}
    start = (int(current.row), int(current.col))
    queue = deque([start])
    predecessor = {start: None}
    destination = None
    while queue:
        pos = queue.popleft()
        if pos != start and by_position[pos].id not in state['observed_ids']:
            destination = pos
            break
        r, col = pos
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if not (dr or dc):
                    continue
                nxt = (r + dr, col + dc)
                if nxt in by_position and nxt not in predecessor:
                    predecessor[nxt] = pos
                    queue.append(nxt)
    if destination is None:
        return None
    while predecessor[destination] != start:
        destination = predecessor[destination]
    return by_position[destination]


def _pick_discrete_pso_cell(state: Dict, now: float,
                            global_observations: List[Dict]) -> Optional[GridCell]:
    """ACAK-LoPSO-inspired *2D simulation variant*.

    Retains paper's bounded concentration, pbest, eligible shared record,
    dynamic weights, 8-neighbor ranking, two-cell revisit penalty and sigmoid
    direction memory. Adds an explicitly labeled exploration safeguard to
    prevent repeated A-B oscillations observed in the 2D simulation.
    """
    current = state['current']
    neighbors = _grid_neighbors(current, state['route'], state['observed_ids'])
    if not neighbors:
        return None
    samples = [s for s in state['samples'] if 0 <= now - s[0] <= MEMORY_WINDOW_SECONDS]
    length_scale = 2.0 * _cell_spacing_m(state['route'], current)
    wind_dir = state.get('wind_direction_deg', 0.0)
    wind_speed = state.get('plume_advection_mps', 0.0)
    def estimate(c, obs=samples):
        return _bounded_smoke_score(_local_observation_estimate(
            c, obs, now, length_scale, wind_dir, wind_speed))

    visited = state.get('search_visited', {})
    scored_visited = [(estimate(c), c) for c in visited.values()]
    informative = [(score, c) for score, c in scored_visited if score > 1e-12]
    # Do not fabricate a pbest from an arbitrary cell ID when all evidence expired.
    if informative:
        pbest_evidence, pbest = max(informative, key=lambda x: (x[0], -x[1].id))
    else:
        pbest, pbest_evidence = current, 0.0
    state['pbest'] = (now, pbest, pbest_evidence)

    shared = [r for r in global_observations
              if r['uav_id'] != state['id'] and r['cell'].id in state['allowed_ids']
              and 0 <= now - r['time'] <= MEMORY_WINDOW_SECONDS]
    # Shared records evaluated with THEIR measured evidence, without allowing
    # the target controller to access any unobserved ground-truth concentration.
    # Compare shared samples on the same normalized evidence scale as pbest.
    shared_best = max(shared, key=lambda r: (_bounded_smoke_score(r['concentration']), -r['cell'].id)) if shared else None
    shared_evidence = (_bounded_smoke_score(shared_best['concentration'])
                       if shared_best else None)
    state['eligible_shared_count'] += len(shared)
    if shared_best:
        state['shared_best_used'] += 1
    reference = shared_evidence if shared_evidence is not None else pbest_evidence
    wc, wp, wb, wg = ((0.50, 0.15, 0.30, 0.05) if reference >= 0.70
                      else (0.40, 0.15, 0.15, 0.30))
    current_estimate = estimate(current)
    candidates = []
    for c in neighbors:
        evidence = estimate(c)
        dp = 1.0 / (1.0 + _meters_between(c, pbest) / 1.0)  # Eq.(24): d0=1m
        db = (1.0 / (1.0 + _meters_between(c, shared_best['cell']) / 1.0)
              if shared_best else 0.0)
        risk = max(0.0, min(1.0, float(c.risk or 1.0) / 5.0))
        base = wc*evidence + wp*dp + (wb*db if shared_best else 0.0) \
               + wg*(evidence-current_estimate) + 0.05*risk
        if c.id in state['history'][-2:]:
            base *= 0.80
        candidates.append((base, c))

    preferred = max(candidates, key=lambda item: (
        item[0], -_meters_between(current, item[1]), -item[1].id))[1]
    lam = 0.4 + 0.5 / (1.0 + math.exp(10.0 * (pbest_evidence - 0.5)))
    direction = _vector_direction(current, preferred)
    old_e, old_n = state['memory']
    memory = (lam*old_e+(1-lam)*direction[0],
              lam*old_n+(1-lam)*direction[1])
    def final_rank(item):
        base, c = item
        d = _vector_direction(current, c)
        return (base + 0.10*(memory[0]*d[0]+memory[1]*d[1]),
                -_meters_between(current,c), -c.id)

    # Critical simulation safeguard: a previously visited neighbor cannot
    # monopolize the flight when there are unseen neighbors. All neighbors
    # were still evaluated by the paper score above. This is a deliberate
    # anti-oscillation modification, NOT claimed as identical to paper A1.
    unseen = [item for item in candidates if item[1].id not in state['observed_ids']]
    if unseen:
        chosen = max(unseen, key=final_rank)[1]
    else:
        chosen = _pso_recovery_step(state)
        # Recovery count is committed by the scheduler, not here.
    if chosen is None:
        return None
    # Proposal only: scheduler commits counters and memory after budget validation.
    state['pending_pso'] = {
        'cell_id': chosen.id, 'memory': memory,
        'revisit': int(chosen.id in state['observed_ids']),
        'recovery': int(not unseen),
    }
    return chosen

def _run_fleet_events(
    strategy: str, label: str, routes_by_uav: Dict[int, Dict],
    fire_cell_id: int, uav_speed_mps: float,
    fire_cell: Optional[GridCell] = None, smoke_radius_m: Optional[float] = None,
    wind_direction_deg: float = 0.0, smoke_angle_deg: float = 35.0,
    smoke_threshold: float = 0.5, search_angle_deg: float = 30.0,
    search_radius_m: Optional[float] = None,
    observation_seconds: float = OBSERVATION_SECONDS,
    mission_horizon_seconds: Optional[float] = DEFAULT_MISSION_HORIZON_SECONDS,
) -> Dict:
    """Discrete-event fleet simulator; all aircraft fly *simultaneously*.

    Min-heap events are observation COMPLETION times. Scheduler never knows
    fire location except the evaluator's success check at observation time.
    Time model = per-leg distance / constant ground speed + sensor acquisition.
    """
    if not math.isfinite(uav_speed_mps) or uav_speed_mps <= 0:
        raise ValueError('uav_speed_mps phải hữu hạn và > 0.')
    if not math.isfinite(observation_seconds) or observation_seconds < 0:
        raise ValueError('observation_seconds phải hữu hạn và >= 0.')
    if mission_horizon_seconds is not None and (not math.isfinite(mission_horizon_seconds) or mission_horizon_seconds <= 0):
        raise ValueError('mission_horizon_seconds phải hữu hạn và > 0, hoặc None.')
    if not 0 < smoke_threshold <= 1.0:
        raise ValueError('smoke_threshold phải thuộc (0,1].')
    if fire_cell is not None and (smoke_radius_m is None or not math.isfinite(smoke_radius_m) or smoke_radius_m <= 0):
        raise ValueError('smoke_radius_m phải hữu hạn và > 0.')
    if strategy in ('kmeans_wind','kmeans_swarm') and fire_cell is None:
        raise ValueError('Mô phỏng khói cần fire_cell làm ground-truth (chỉ cho môi trường).')
    if strategy == 'kmeans_wind' and (search_radius_m is None or search_radius_m <= 0):
        raise ValueError('search_radius_m phải > 0.')

    # (completed timestamp, UAV-ID, event sequence, cell, travel distance, mode)
    heap = []
    states = {}
    seq = 0
    for uid in sorted(routes_by_uav):
        item = routes_by_uav[uid]
        route = item.get('route') or []
        if not route:
            continue
        launch = item.get('launch_cell') or route[0]
        states[uid] = {
            'id': uid, 'name': item.get('uav_name') or f'UAV-{uid}',
            # Observe the common launch cell first for EVERY method, including
            # no-K-means. Otherwise a baseline UAV could start on the fire
            # and unknowingly fly away before looking, invalidating fairness.
            'route': route, 'route_index': 0,
            'allowed_ids': {c.id for c in route},
            'current': None, 'time': 0.0, 'distance': 0.0,
            'observed_ids': set(), 'samples': [], 'history': [],
            'active': False, 'search_start': None, 'priority': [],
            'processed_smoke': set(), 'memory': (0.0,0.0),
            'swarm_iterations': 0, 'pbest': None,
            'search_visited': {}, 'eligible_shared_count': 0,
            'shared_best_used': 0, 'pso_revisits': 0, 'pso_recovery_steps': 0,
            'wind_direction_deg': wind_direction_deg,
            'plume_advection_mps': 0.0,  # stationary cone: no dynamic wind velocity
            'pending_pso': None, 'search_triggers': 0,
        }
        heapq.heappush(heap, (observation_seconds,
                              uid, seq, launch, 0.0, 'launch'))
        seq += 1
    if not states:
        return _result(strategy, label, False, None, None, 0, {
            'detail': 'Chưa có route hợp lệ.', 'first_smoke_time_seconds': None,
            'time_metric': 'source_cell_observation_time',
        })

    first_smoke = None
    checked = 0
    last_time = 0.0
    winner = None
    winner_mode = None
    wind_events = []
    global_observations = []
    horizon_reached = False
    while heap:
        now, uid, _, cell, leg_distance, mode = heapq.heappop(heap)
        if mission_horizon_seconds is not None and now > mission_horizon_seconds + 1e-9:
            horizon_reached = True
            break
        st = states[uid]
        st['current'], st['time'] = cell, now
        st['distance'] += leg_distance
        st['observed_ids'].add(cell.id)
        st['history'].append(cell.id)
        checked += 1
        last_time = now

        # Only the simulator environment, NOT a controller, has fire_cell.
        # Read physical synthetic concentration only at an observed location.
        concentration = _calculate_smoke_concentration(
            source_cell=fire_cell, target_cell=cell,
            smoke_radius_m=smoke_radius_m,
            wind_direction_deg=wind_direction_deg,
            smoke_angle_deg=smoke_angle_deg,
        ) if fire_cell is not None else 0.0
        st['samples'].append((now, cell, concentration))
        st['samples'] = [r for r in st['samples'] if 0 <= now-r[0] <= MEMORY_WINDOW_SECONDS]
        global_observations.append({
            'uav_id': uid, 'time': now, 'cell': cell,
            'concentration': concentration,
        })
        # Share only recent records (avoid memory increasing indefinitely).
        if len(global_observations) > 5000:
            global_observations = [r for r in global_observations if now-r['time'] <= MEMORY_WINDOW_SECONDS]

        if first_smoke is None and fire_cell is not None and concentration >= smoke_threshold:
            first_smoke = (now, cell.id, st['name'], concentration)

        # Fire truth is used strictly for evaluation; if detected, earliest
        # event time in heap guarantees fleet's earliest completed observation.
        if cell.id == fire_cell_id:
            winner = uid
            winner_mode = mode
            break

        if strategy in ('kmeans_wind','kmeans_swarm'):
            if (not st['active'] and concentration >= smoke_threshold
                    and cell.id not in st['processed_smoke']):
                st['active'] = True
                st['search_triggers'] += 1
                st['processed_smoke'].add(cell.id) if strategy == 'kmeans_swarm' else None
                st['search_start'] = now
                st['priority'] = []
                st['memory'] = (0.0,0.0)
                st['pbest'] = None
                st['search_visited'] = {cell.id: cell}
                # Wind candidate generation is performed below on the first trigger.
            if st['active'] and strategy == 'kmeans_swarm':
                st['search_visited'][cell.id] = cell
            if st['active'] and now >= st['search_start']+LOCAL_SEARCH_BUDGET_SECONDS:
                st['active'] = False
                st['priority'] = []
                st['search_start'] = None
                st['pbest'] = None
                st['search_visited'] = {}

        next_cell = None
        next_mode = 'patrol'
        if strategy == 'kmeans_wind' and st['active']:
            if not st['priority'] and concentration >= smoke_threshold and cell.id not in st['processed_smoke']:
                st['processed_smoke'].add(cell.id)
                st['priority'] = _upwind_candidates_for_observation(
                    cell, st['route'], st['observed_ids'], wind_direction_deg,
                    search_angle_deg, search_radius_m,
                )
                if len(wind_events) < MAX_RECORDED_EVENTS:
                    wind_events.append({
                        'uav_id': uid, 'uav_name': st['name'],
                        'time_seconds': round(now, 6),
                        'smoke_cell_id': cell.id,
                        'smoke_concentration': round(concentration,6),
                        'candidate_count': len(st['priority']),
                        'candidate_cell_ids': [c.id for c in st['priority'][:20]],
                    })
            while st['priority'] and st['priority'][0].id in st['observed_ids']:
                st['priority'].pop(0)
            if st['priority']:
                next_cell = st['priority'].pop(0)
                next_mode = 'wind_candidate'
            else:
                st['active'] = False
                st['search_start'] = None
        if strategy == 'kmeans_swarm' and st['active']:
            st['pending_pso'] = None
            next_cell = _pick_discrete_pso_cell(st, now, global_observations)
            if next_cell is not None:
                next_mode = 'swarm_candidate'
            else:
                st['active'] = False
                st['search_start'] = None
                st['pbest'] = None
                st['search_visited'] = {}

        # Reject candidate if it cannot be observed within local 10m budget.
        if (next_cell is not None and next_mode != 'patrol' and st['search_start'] is not None
                and now+_meters_between(cell,next_cell)/uav_speed_mps+observation_seconds
                   > st['search_start']+LOCAL_SEARCH_BUDGET_SECONDS):
            next_cell = None
            st['priority'] = []
            st['active'] = False
            st['search_start'] = None
            st['pbest'] = None
            st['search_visited'] = {}
            st['pending_pso'] = None

        # Commit only an accepted PSO proposal.
        if next_cell is not None and next_mode == 'swarm_candidate':
            pending = st.get('pending_pso')
            if pending and pending['cell_id'] == next_cell.id:
                st['memory'] = pending['memory']
                st['swarm_iterations'] += 1
                st['pso_revisits'] += pending['revisit']
                st['pso_recovery_steps'] += pending['recovery']
        st['pending_pso'] = None

        # Continue the saved K-means route, skipping visited rerouting cells.
        if next_cell is None:
            while st['route_index'] < len(st['route']):
                candidate = st['route'][st['route_index']]
                st['route_index'] += 1
                if candidate.id not in st['observed_ids']:
                    next_cell = candidate
                    break
        if next_cell is not None:
            distance = _meters_between(cell, next_cell)
            finish = now+distance/uav_speed_mps+observation_seconds
            heapq.heappush(heap, (finish, uid, seq, next_cell, distance, next_mode))
            seq += 1

    success = winner is not None
    st = states[winner] if success else None
    extra = {
        'time_metric': 'source_cell_observation_time',
        'success_criterion': 'first completed observation of exact source grid cell (simulation proxy)',
        'initial_condition': 'fire exists at t=0; same K-means launch locations for all strategies',
        'observation_seconds': observation_seconds,
        'smoke_detected': first_smoke is not None,
        'first_smoke_time_seconds': round(first_smoke[0],6) if first_smoke else None,
        'first_smoke_cell_id': first_smoke[1] if first_smoke else None,
        'first_smoke_by_uav': first_smoke[2] if first_smoke else None,
        'first_smoke_concentration': round(first_smoke[3],6) if first_smoke else None,
        'smoke_threshold': smoke_threshold,
        'movement_mode_when_detected': winner_mode,
        'travel_distance_m': round(st['distance'],2) if st else None,
        'fleet_travel_distance_m': round(sum(x['distance'] for x in states.values()), 2),
        'unique_checked_cells': len(set().union(*(x['observed_ids'] for x in states.values()))),
        'search_triggers': sum(x['search_triggers'] for x in states.values()),
        'mission_horizon_seconds': mission_horizon_seconds,
        'termination_reason': 'detected' if success else ('mission_horizon' if horizon_reached else 'routes_exhausted'),
        'elapsed_simulation_seconds': round(last_time,6),
        'wind_reroute_events': wind_events,
        'swarm_active': any(sta['swarm_iterations']>0 for sta in states.values()),
        'swarm_iterations': sum(sta['swarm_iterations'] for sta in states.values()),
        'pso_revisits': sum(sta['pso_revisits'] for sta in states.values()),
        'pso_recovery_steps': sum(sta['pso_recovery_steps'] for sta in states.values()),
        'shared_best_used': sum(sta['shared_best_used'] for sta in states.values()),
        'eligible_shared_count': sum(sta['eligible_shared_count'] for sta in states.values()),
        'broadcast_events': [],  # cross-region movement prohibited
        'pso_variant': '2D ACAK-LoPSO-inspired: 8-neighbor scoring + exploration guard + BFS dead-end recovery; static plume',
        'pbest_concentration': (st['pbest'][2] if st and st['pbest'] else None),
        'gbest_concentration': None,
        'gbest_cell_id': None,
    }
    return _result(strategy, label, success,
                   round(st['time'],6) if success else None,
                   st['name'] if success else None,
                   checked, extra)


def simulate_no_kmeans(
    routes_by_uav: Dict[int, Dict], fire_cell_id: int, uav_speed_mps: float,
    fire_cell=None, smoke_radius_m=None, wind_direction_deg=0.0,
    smoke_angle_deg=35.0, smoke_threshold=0.50,
    mission_horizon_seconds: Optional[float] = DEFAULT_MISSION_HORIZON_SECONDS,
) -> Dict:
    return _run_fleet_events(
        'no_kmeans', '1) Không K-means', routes_by_uav,
        fire_cell_id, uav_speed_mps, fire_cell, smoke_radius_m,
        wind_direction_deg, smoke_angle_deg, smoke_threshold,
        mission_horizon_seconds=mission_horizon_seconds,
    )


def simulate_kmeans(
    routes_by_uav: Dict[int, Dict], fire_cell_id: int, uav_speed_mps: float,
    fire_cell=None, smoke_radius_m=None, wind_direction_deg=0.0,
    smoke_angle_deg=35.0, smoke_threshold=0.50,
    mission_horizon_seconds: Optional[float] = DEFAULT_MISSION_HORIZON_SECONDS,
) -> Dict:
    return _run_fleet_events(
        'kmeans', '2) Có K-means', routes_by_uav,
        fire_cell_id, uav_speed_mps, fire_cell, smoke_radius_m,
        wind_direction_deg, smoke_angle_deg, smoke_threshold,
        mission_horizon_seconds=mission_horizon_seconds,
    )


def simulate_kmeans_wind(
    cells: List[GridCell], routes_by_uav: Dict[int, Dict], fire_cell: GridCell,
    wind_direction_deg: float, uav_speed_mps: float, smoke_angle_deg: float,
    smoke_radius_m: float, search_angle_deg: float, search_radius_m: float,
    smoke_threshold: float,
    mission_horizon_seconds: Optional[float] = DEFAULT_MISSION_HORIZON_SECONDS,
) -> Dict:
    return _run_fleet_events(
        'kmeans_wind', '3) K-means + ngưỡng khói + hướng gió',
        routes_by_uav, fire_cell.id, uav_speed_mps, fire_cell,
        smoke_radius_m, wind_direction_deg, smoke_angle_deg,
        smoke_threshold, search_angle_deg, search_radius_m,
        mission_horizon_seconds=mission_horizon_seconds,
    )


def simulate_kmeans_swarm(
    cells: List[GridCell], routes_by_uav: Dict[int, Dict], fire_cell: GridCell,
    uav_speed_mps: float, smoke_radius_m: float, smoke_threshold: float,
    wind_direction_deg: float = 0.0, smoke_angle_deg: float = 35.0,
    mission_horizon_seconds: Optional[float] = DEFAULT_MISSION_HORIZON_SECONDS,
) -> Dict:
    return _run_fleet_events(
        'kmeans_swarm', '4) K-means + ngưỡng khói + Swarm/PSO',
        routes_by_uav, fire_cell.id, uav_speed_mps, fire_cell,
        smoke_radius_m, wind_direction_deg, smoke_angle_deg, smoke_threshold,
        mission_horizon_seconds=mission_horizon_seconds,
    )


def compare_detection_strategies(
    mission: Mission,
    fire_cell_id: int,
    wind_direction_deg: float,
    uav_speed_mps: float = 10.0,
    smoke_angle_deg: float = 35.0,
    smoke_radius_m: Optional[float] = None,
    search_angle_deg: float = 30.0,
    search_radius_m: Optional[float] = None,
    smoke_threshold: float = 0.50,
    uav_speed: Optional[float] = None,
    mission_horizon_seconds: Optional[float] = DEFAULT_MISSION_HORIZON_SECONDS,
) -> Dict:
    """
    Hàm chính gọi từ API.

    So sánh 4 chiến lược:

    1. Không K-means
    2. Có K-means
    3. K-means + ngưỡng nồng độ khói + hướng gió
    4. K-means + ngưỡng nồng độ khói + Swarm

    Các UAV được mô phỏng đồng thời bằng hàng đợi sự kiện (event queue).
    Time = tổng(distance/speed + observation_seconds).
    detected_time_seconds đo thời điểm quan sát xong đúng ô nguồn cháy,
    chỉ là chỉ số proxy của bài toán mô phỏng, không phải độ trễ AI đã xác nhận.

    smoke_threshold:
        Ngưỡng nồng độ khói để UAV xác định
        đã phát hiện tín hiệu khói.

        0.0 < smoke_threshold <= 1.0
    """

    # Compatibility with original views.py using uav_speed=... .
    if uav_speed is not None:
        uav_speed_mps = float(uav_speed)

    # =========================================================
    # VALIDATE SMOKE THRESHOLD
    # =========================================================

    if not 0.0 < smoke_threshold <= 1.0:
        raise ValueError(
            "smoke_threshold phải thuộc khoảng (0, 1]."
        )

    if not math.isfinite(uav_speed_mps) or uav_speed_mps <= 0:
        raise ValueError("uav_speed_mps phải là số hữu hạn lớn hơn 0.")
    for name, val in (("wind_direction_deg", wind_direction_deg),
                      ("smoke_angle_deg", smoke_angle_deg),
                      ("search_angle_deg", search_angle_deg)):
        if not math.isfinite(float(val)):
            raise ValueError(f"{name} phải là số hữu hạn.")
    if not 0 <= smoke_angle_deg <= 180 or not 0 <= search_angle_deg <= 180:
        raise ValueError("Các góc mở phải nằm trong khoảng [0,180] độ.")

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

    if (not math.isfinite(smoke_radius_m) or smoke_radius_m <= 0
            or not math.isfinite(search_radius_m) or search_radius_m <= 0):
        raise ValueError("smoke_radius_m và search_radius_m phải hữu hạn, lớn hơn 0.")

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
            fire_cell=fire_cell,
            smoke_radius_m=smoke_radius_m,
            wind_direction_deg=wind_direction_deg,
            smoke_angle_deg=smoke_angle_deg,
            smoke_threshold=smoke_threshold,
            mission_horizon_seconds=mission_horizon_seconds,
        ),

        # -----------------------------------------------------
        # Strategy 2
        # -----------------------------------------------------

        simulate_kmeans(
            routes_by_uav=routes_by_uav,
            fire_cell_id=fire_cell_id,
            uav_speed_mps=uav_speed_mps,
            fire_cell=fire_cell,
            smoke_radius_m=smoke_radius_m,
            wind_direction_deg=wind_direction_deg,
            smoke_angle_deg=smoke_angle_deg,
            smoke_threshold=smoke_threshold,
            mission_horizon_seconds=mission_horizon_seconds,
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
            mission_horizon_seconds=mission_horizon_seconds,
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
            wind_direction_deg=wind_direction_deg,
            smoke_angle_deg=smoke_angle_deg,
            mission_horizon_seconds=mission_horizon_seconds,
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
            "mission_horizon_seconds": mission_horizon_seconds,

            "search_angle_deg": search_angle_deg,

            "search_radius_m": search_radius_m,

            "wind_direction_note":
                "0=Đông, 90=Bắc, 180=Tây, 270=Nam. "
                "Đây là hướng khói bay tới.",

            "smoke_threshold_note":
                "UAV được xem là phát hiện khói "
                "khi concentration >= smoke_threshold.",

            "time_formula":
                "Observation finish = previous finish + distance/speed + 1s sampling",

            "time_metric": "source_cell_observation_time",
            "time_metric_note": (
                "detected_time_seconds: hoan thanh quan sat o nguon chay "
                "(simulated ground-truth proxy), KHONG phai AI localization da xac nhan. "
                "first_smoke_time_seconds: lan dau mau khoi dat nguong."
            ),

        },

        "results": results,

    }