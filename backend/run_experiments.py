# backend/run_experiments.py

import os
import sys
import csv
import json
import math
import statistics
from pathlib import Path
from datetime import datetime

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.models import Mission, GridCell
from core.experiment_services import compare_detection_strategies


# =========================
# CẤU HÌNH THỬ NGHIỆM
# =========================

DEFAULT_FIRE_LOCATIONS = 5
UAV_SPEED_MPS = 10.0
WIND_DIRECTION_DEG = 225.0
SMOKE_THRESHOLD = 0.50

OUTPUT_DIR = Path(__file__).resolve().parent / "benchmark_data"


def select_fire_cells(cells, number):
    """
    Chọn các ô phân bố đều theo danh sách ô lưới đã sắp xếp.
    Kết quả có thể tái lập khi danh sách ô không thay đổi.
    """
    cells = list(cells)

    if not cells:
        raise ValueError("Mission không có ô lưới nào.")

    if number < 1:
        raise ValueError("Số vị trí cháy phải >= 1.")

    if number > len(cells):
        raise ValueError(
            f"Yêu cầu {number} vị trí nhưng chỉ có {len(cells)} ô lưới."
        )

    if number == 1:
        return [cells[len(cells) // 2]]

    indexes = [
        round(i * (len(cells) - 1) / (number - 1))
        for i in range(number)
    ]

    return [cells[i] for i in indexes]


def main():
    if len(sys.argv) < 2:
        print(
            "Cách chạy: python run_experiments.py <MISSION_ID> [SO_VI_TRI_CHAY]"
        )
        sys.exit(1)

    mission_id = int(sys.argv[1])
    number_of_locations = (
        int(sys.argv[2]) if len(sys.argv) >= 3
        else DEFAULT_FIRE_LOCATIONS
    )

    mission = Mission.objects.select_related("area").get(id=mission_id)

    cells = list(
        GridCell.objects.filter(area=mission.area)
        .order_by("row", "col", "id")
    )

    fire_cells = select_fire_cells(cells, number_of_locations)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # File 1: Chi tiết từng lần chạy
    output_file = OUTPUT_DIR / (
        f"mission_{mission_id}_pilot_{timestamp}.csv"
    )

    # File 2: Bảng tổng hợp so sánh chỉ số
    summary_file = OUTPUT_DIR / (
        f"mission_{mission_id}_summary_{timestamp}.csv"
    )

    columns = [
        "experiment_id",
        "mission_id",
        "area_id",
        "fire_cell_id",
        "fire_row",
        "fire_col",
        "strategy",
        "detected",
        "detection_time_seconds",
        "travel_distance_m",
        "checked_cells",
        "detected_by_uav",
        "uav_speed_mps",
        "wind_direction_deg",
        "smoke_threshold",
        "status",
        "error",
        "raw_result_json",
    ]

    total_rows = 0

    # Bộ lưu trữ dữ liệu để tính toán bảng tổng hợp
    summary_data = {}

    with output_file.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()

        for index, fire_cell in enumerate(fire_cells, start=1):
            experiment_id = f"M{mission_id}_F{fire_cell.id}"

            print(
                f"[{index}/{len(fire_cells)}] "
                f"Mission {mission_id}, ô cháy {fire_cell.id}"
            )

            try:
                comparison = compare_detection_strategies(
                    mission=mission,
                    fire_cell_id=fire_cell.id,
                    wind_direction_deg=WIND_DIRECTION_DEG,
                    uav_speed_mps=UAV_SPEED_MPS,
                    smoke_threshold=SMOKE_THRESHOLD,
                )

                results = comparison.get("results", [])

                if not results:
                    raise ValueError("Không có kết quả chiến lược.")

                for result in results:
                    detected = bool(result.get("detected", False))
                    detection_time = result.get(
                        "detected_time_seconds"
                    )
                    distance = result.get("travel_distance_m")
                    strategy_label = result.get(
                        "label", result.get("strategy", "unknown")
                    )

                    # Không ghi thời gian bằng 0 nếu chưa phát hiện.
                    if not detected:
                        detection_time = None
                    else:
                        # Tích lũy dữ liệu tính tổng hợp cho các case phát hiện thành công
                        if strategy_label not in summary_data:
                            summary_data[strategy_label] = {
                                "times": [],
                                "checked_cells": [],
                            }
                        summary_data[strategy_label]["times"].append(detection_time)
                        summary_data[strategy_label]["checked_cells"].append(
                            result.get("checked_cells", 0)
                        )

                    writer.writerow({
                        "experiment_id": experiment_id,
                        "mission_id": mission.id,
                        "area_id": mission.area_id,
                        "fire_cell_id": fire_cell.id,
                        "fire_row": fire_cell.row,
                        "fire_col": fire_cell.col,
                        "strategy": strategy_label,
                        "detected": detected,
                        "detection_time_seconds": detection_time,
                        "travel_distance_m": distance,
                        "checked_cells": result.get("checked_cells"),
                        "detected_by_uav": result.get("detected_by_uav"),
                        "uav_speed_mps": UAV_SPEED_MPS,
                        "wind_direction_deg": WIND_DIRECTION_DEG,
                        "smoke_threshold": SMOKE_THRESHOLD,
                        "status": "ok",
                        "error": "",
                        # Giữ lại toàn bộ kết quả gốc để phân tích sau.
                        "raw_result_json": json.dumps(
                            result, ensure_ascii=False, default=str
                        ),
                    })
                    total_rows += 1

                file.flush()

            except Exception as exc:
                # Ghi lại lỗi để không âm thầm bỏ qua vị trí cháy.
                writer.writerow({
                    "experiment_id": experiment_id,
                    "mission_id": mission.id,
                    "area_id": mission.area_id,
                    "fire_cell_id": fire_cell.id,
                    "fire_row": fire_cell.row,
                    "fire_col": fire_cell.col,
                    "strategy": "ALL",
                    "detected": "",
                    "detection_time_seconds": "",
                    "travel_distance_m": "",
                    "checked_cells": "",
                    "detected_by_uav": "",
                    "uav_speed_mps": UAV_SPEED_MPS,
                    "wind_direction_deg": WIND_DIRECTION_DEG,
                    "smoke_threshold": SMOKE_THRESHOLD,
                    "status": "error",
                    "error": str(exc),
                    "raw_result_json": "",
                })
                file.flush()

                print(f"  LỖI: {exc}")

    # =========================================================
    # TẠO FILE CSV BẢNG TỔNG HỢP SO SÁNH
    # =========================================================
    summary_columns = [
        "Chiến lược",
        "Thời gian TB (giây)",
        "Trung vị thời gian (giây)",
        "Số ô kiểm tra TB",
    ]

    with summary_file.open("w", newline="", encoding="utf-8-sig") as sum_file:
        sum_writer = csv.DictWriter(sum_file, fieldnames=summary_columns)
        sum_writer.writeheader()

        for strat_label, data in summary_data.items():
            times = data["times"]
            checked = data["checked_cells"]

            mean_time = statistics.mean(times) if times else 0.0
            median_time = statistics.median(times) if times else 0.0
            mean_checked = statistics.mean(checked) if checked else 0.0

            sum_writer.writerow({
                "Chiến lược": strat_label,
                "Thời gian TB (giây)": round(mean_time, 2),
                "Trung vị thời gian (giây)": round(median_time, 2),
                "Số ô kiểm tra TB": round(mean_checked, 1),
            })

    print("\nHoàn thành thử nghiệm.")
    print(f"Mission ID: {mission_id}")
    print(f"Số vị trí cháy: {len(fire_cells)}")
    print(f"Số dòng kết quả chiến lược: {total_rows}")
    print(f"File CSV chi tiết : {output_file}")
    print(f"File CSV tổng hợp : {summary_file}")


if __name__ == "__main__":
    main()