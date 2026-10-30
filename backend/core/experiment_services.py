import math
import heapq
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


def _bounded_smoke_score(concentration: float) -> float:
    """Eq. (4): xi = max((C-Cbg)/C0,0), normalized g/2 = xi/(1+xi)."""
    xi = max(0.0, (float(concentration) - CONCENTRATION_BACKGROUND) / CONCENTRATION_SCALE)
    return xi / (1.0 + xi)


def _grid_neighbors(current_cell: GridCell, allowed_cells: List[GridCell],
                    already_observed: Set[int]) -> List[GridCell]:
    """At most 8 *grid-adjacent* in-cluster candidates (not 8 nearest globally).

    The route list is used as the accessible cell set because the existing
    database doesn't expose collision-free path polygons in this module.
    Unobserved neighbor preference guarantees a finite illustrative search.
    """
    result = []
    for c in allowed_cells:
        if c.id in already_observed or c.id == current_cell.id:
            continue
        if max(abs(int(c.row)-int(current_cell.row)),
               abs(int(c.col)-int(current_cell.col))) == 1:
            result.append(c)
    return sorted(result, key=lambda c: (_meters_between(current_cell, c), c.id))[:8]


def _local_observation_estimate(target: GridCell, observations: List[Tuple],
                                timestamp: float, length_scale_m: float) -> float:
    """Eq. (21), finite-memory inverse-distance/time weighting.

    With current API only wind DIRECTION is available, not wind SPEED,
    so no velocity-based advection can be calibrated. Observed sample
    positions stay fixed; this is explicitly the stationary approximation.
    Neither the source location nor the unobserved true plume is read here.
    """
    numerator = 0.0
    denominator = 0.0
    length2 = max(1.0, length_scale_m)**2
    for sample_time, sample_cell, sample_concentration in observations:
        age = timestamp - sample_time
        if age < -1e-9 or age > MEMORY_WINDOW_SECONDS:
            continue
        d = _meters_between(target, sample_cell)
        weight = math.exp(-max(0.0, age)/MEMORY_WINDOW_SECONDS)/(1.0+d*d/length2)
        numerator += weight * sample_concentration
        denominator += weight
    return numerator / denominator if denominator else 0.0


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


def _pick_discrete_pso_cell(state: Dict, now: float,
                            global_observations: List[Dict]) -> Optional[GridCell]:
    """Observation-only cluster-constrained ACAK-LoPSO horizontal policy.

    Uses paper: bounded concentration Eq.4, score Eq.5, sigmoid Eq.7,
    directional memory Eq.8 and final ranking Eq.6. No oracle concentrations.
    Shared record must have been observed, be fresh, and IN THIS CLUSTER.
    This is a 2D adaptation: Eq.9/10 canopy height not available.
    """
    current_cell = state['current']
    neighbors = _grid_neighbors(current_cell, state['route'], state['observed_ids'])
    if not neighbors:
        return None
    local = [r for r in state['samples'] if 0 <= now-r[0] <= MEMORY_WINDOW_SECONDS]
    if not local:
        return None
    best_sample = max(local, key=lambda s: (s[2], -s[0], -s[1].id))
    pbest = best_sample[1]
    # Shared memory is valid only when originally observed by another vehicle
    # and belongs to the current vehicle's assigned K-means region.
    shared = [r for r in global_observations
              if r['uav_id'] != state['id']
              and r['cell'].id in state['allowed_ids']
              and 0 <= now-r['time'] <= MEMORY_WINDOW_SECONDS]
    shared_best = max(shared, key=lambda r: (r['concentration'], -r['cell'].id)) if shared else None
    ref_conc = shared_best['concentration'] if shared_best else best_sample[2]
    ref = _bounded_smoke_score(ref_conc)
    if ref >= 0.70:
        wc, wp, wb, wg = (0.50, 0.15, 0.30, 0.05)
    else:
        wc, wp, wb, wg = (0.40, 0.15, 0.15, 0.30)
    length_scale = 2.0 * max(1.0, min(_meters_between(current_cell, c) for c in neighbors))
    current_est = _bounded_smoke_score(_local_observation_estimate(current_cell, local, now, length_scale))
    scored = []
    for c in neighbors:
        candidate_est = _local_observation_estimate(c, local, now, length_scale)
        evidence = _bounded_smoke_score(candidate_est)
        p_dist = _meters_between(c, pbest)
        d_p = 1.0/(1.0+p_dist/1.0)
        d_b = (1.0/(1.0+_meters_between(c, shared_best['cell'])/1.0)) if shared_best else 0.0
        gain = evidence-current_est        # signed difference per Eq.(25)
        risk = max(0.0, min(1.0, float(c.risk or 1.0)/5.0))
        base = wc*evidence + wp*d_p + (wb*d_b if shared_best else 0.0) + wg*gain + 0.05*risk
        # Paper Appendix B: *0.8 for last two positions; signed caveat applies.
        if c.id in state['history'][-2:]:
            base *= 0.80
        scored.append((base, c))
    # Compute direction memory *once per completed decision* (not per candidate).
    best_base, preferred = max(
        scored, key=lambda item: (item[0], -_meters_between(current_cell, item[1]), -item[1].id)
    )
    lam = 0.4 + 0.5/(1.0+math.exp(10.0*(ref-0.5)))
    direction = _vector_direction(current_cell, preferred)
    prev_e, prev_n = state['memory']
    memory = (lam*prev_e+(1-lam)*direction[0],
              lam*prev_n+(1-lam)*direction[1])
    state['memory'] = memory
    state['swarm_iterations'] += 1
    def sort_key(item):
        base, c = item
        d = _vector_direction(current_cell, c)
        score = base + 0.10*(memory[0]*d[0]+memory[1]*d[1])
        return (score, -_meters_between(current_cell, c), -c.id)
    return max(scored, key=sort_key)[1]


def _run_fleet_events(
    strategy: str, label: str, routes_by_uav: Dict[int, Dict],
    fire_cell_id: int, uav_speed_mps: float,
    fire_cell: Optional[GridCell] = None, smoke_radius_m: Optional[float] = None,
    wind_direction_deg: float = 0.0, smoke_angle_deg: float = 35.0,
    smoke_threshold: float = 0.5, search_angle_deg: float = 30.0,
    search_radius_m: Optional[float] = None,
    observation_seconds: float = OBSERVATION_SECONDS,
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
            'swarm_iterations': 0,
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
    while heap:
        now, uid, _, cell, leg_distance, mode = heapq.heappop(heap)
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
            if not st['active'] and concentration >= smoke_threshold:
                st['active'] = True
                st['search_start'] = now
                st['priority'] = []
                st['memory'] = (0.0,0.0)
            if st['active'] and now >= st['search_start']+LOCAL_SEARCH_BUDGET_SECONDS:
                st['active'] = False
                st['priority'] = []
                st['search_start'] = None

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
            next_cell = _pick_discrete_pso_cell(st, now, global_observations)
            if next_cell is not None:
                next_mode = 'swarm_candidate'
            else:
                st['active'] = False
                st['search_start'] = None

        # Reject candidate if it cannot be observed within local 10m budget.
        if (next_cell is not None and next_mode != 'patrol' and st['search_start'] is not None
                and now+_meters_between(cell,next_cell)/uav_speed_mps+observation_seconds
                   > st['search_start']+LOCAL_SEARCH_BUDGET_SECONDS):
            next_cell = None
            st['priority'] = []
            st['active'] = False
            st['search_start'] = None

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
        'elapsed_simulation_seconds': round(last_time,6),
        'wind_reroute_events': wind_events,
        'swarm_active': any(sta['swarm_iterations']>0 for sta in states.values()),
        'swarm_iterations': sum(sta['swarm_iterations'] for sta in states.values()),
        'broadcast_events': [],  # one UAV per region; cross-region best prohibited
        'pbest_concentration': max((s[2] for s in (st['samples'] if st else [])), default=None),
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
) -> Dict:
    return _run_fleet_events(
        'no_kmeans', '1) Không K-means', routes_by_uav,
        fire_cell_id, uav_speed_mps, fire_cell, smoke_radius_m,
        wind_direction_deg, smoke_angle_deg, smoke_threshold,
    )


def simulate_kmeans(
    routes_by_uav: Dict[int, Dict], fire_cell_id: int, uav_speed_mps: float,
    fire_cell=None, smoke_radius_m=None, wind_direction_deg=0.0,
    smoke_angle_deg=35.0, smoke_threshold=0.50,
) -> Dict:
    return _run_fleet_events(
        'kmeans', '2) Có K-means', routes_by_uav,
        fire_cell_id, uav_speed_mps, fire_cell, smoke_radius_m,
        wind_direction_deg, smoke_angle_deg, smoke_threshold,
    )


def simulate_kmeans_wind(
    cells: List[GridCell], routes_by_uav: Dict[int, Dict], fire_cell: GridCell,
    wind_direction_deg: float, uav_speed_mps: float, smoke_angle_deg: float,
    smoke_radius_m: float, search_angle_deg: float, search_radius_m: float,
    smoke_threshold: float,
) -> Dict:
    return _run_fleet_events(
        'kmeans_wind', '3) K-means + ngưỡng khói + hướng gió',
        routes_by_uav, fire_cell.id, uav_speed_mps, fire_cell,
        smoke_radius_m, wind_direction_deg, smoke_angle_deg,
        smoke_threshold, search_angle_deg, search_radius_m,
    )


def simulate_kmeans_swarm(
    cells: List[GridCell], routes_by_uav: Dict[int, Dict], fire_cell: GridCell,
    uav_speed_mps: float, smoke_radius_m: float, smoke_threshold: float,
    wind_direction_deg: float = 0.0, smoke_angle_deg: float = 35.0,
) -> Dict:
    return _run_fleet_events(
        'kmeans_swarm', '4) K-means + ngưỡng khói + Swarm/PSO',
        routes_by_uav, fire_cell.id, uav_speed_mps, fire_cell,
        smoke_radius_m, wind_direction_deg, smoke_angle_deg, smoke_threshold,
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
            wind_direction_deg=wind_direction_deg,
            smoke_angle_deg=smoke_angle_deg,
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