from .experiment_services import compare_detection_strategies
from .models import Mission


def run_simulation(
    mission: Mission,
    fire_cell_id: int,
    wind_direction_deg: float,
    uav_speed: float,
    smoke_angle_deg: float = 35,
    smoke_radius_m=None,
    search_angle_deg: float = 40,
    search_radius_m=None,
    smoke_threshold: float = 0.30,
):
    """
    Hàm benchmark đơn giản.

    Nhận toàn bộ tham số đầu vào.

    Trả về thời gian tìm kiếm của từng chiến lược.

    """

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
    )

    output = []

    for r in result["results"]:

        output.append({

            "strategy": r["label"],

            "travel_distance_m": round(
                r.get("travel_distance_m", 0),
                2,
            ),

            "time_seconds": round(
                r.get("detected_time_seconds", 0),
                2,
            ),

            "checked_cells": r["checked_cells"],

            "detected": r["detected"],

            "detected_by": r["detected_by_uav"],

        })

    return {

        "mission_id": mission.id,

        "uav_speed": uav_speed,

        "wind_direction_deg": wind_direction_deg,

        "fire_cell_id": fire_cell_id,

        "results": output,

    }