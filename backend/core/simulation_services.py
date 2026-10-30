from .experiment_services import compare_detection_strategies
from .models import Mission


def _round_optional(value, digits=2):
    return None if value is None else round(float(value), digits)


def run_simulation(
    mission: Mission,
    fire_cell_id: int,
    wind_direction_deg: float,
    uav_speed: float,
    smoke_angle_deg: float = 35,
    smoke_radius_m=None,
    search_angle_deg: float = 30,
    search_radius_m=None,
    smoke_threshold: float = 0.50,
    mission_horizon_seconds=None,
):
    """Adapter API; None represents a failure, never a zero-second detection."""
    result = compare_detection_strategies(
        mission=mission,
        fire_cell_id=fire_cell_id,
        wind_direction_deg=wind_direction_deg,
        uav_speed_mps=uav_speed,
        smoke_angle_deg=smoke_angle_deg,
        smoke_radius_m=smoke_radius_m,
        search_angle_deg=search_angle_deg,
        search_radius_m=search_radius_m,
        smoke_threshold=smoke_threshold,
        mission_horizon_seconds=mission_horizon_seconds,
    )
    output = []
    for r in result['results']:
        output.append({
            'strategy': r['label'],
            'strategy_id': r['strategy'],
            'travel_distance_m': _round_optional(r.get('travel_distance_m')),
            'fleet_travel_distance_m': _round_optional(r.get('fleet_travel_distance_m')),
            'time_seconds': _round_optional(r.get('detected_time_seconds')),
            'checked_cells': r['checked_cells'],
            'unique_checked_cells': r.get('unique_checked_cells'),
            'detected': r['detected'],
            'detected_by': r['detected_by_uav'],
            'termination_reason': r.get('termination_reason'),
            'search_triggers': r.get('search_triggers', 0),
            'swarm_iterations': r.get('swarm_iterations', 0),
            'pso_revisits': r.get('pso_revisits', 0),
            'pso_recovery_steps': r.get('pso_recovery_steps', 0),
        })
    return {
        'mission_id': mission.id,
        'uav_speed': uav_speed,
        'wind_direction_deg': wind_direction_deg,
        'fire_cell_id': fire_cell_id,
        'mission_horizon_seconds': mission_horizon_seconds,
        'results': output,
    }
