"""Paired 2D benchmark over distinct fire cells; not the full ROS/Gazebo experiment."""
import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from core.models import Mission, GridCell
from core.experiment_services import compare_detection_strategies

DEFAULT_FIRE_LOCATIONS = 5  # Used ONLY when positional argument is omitted
UAV_SPEED_MPS = 10.0
WIND_DIRECTION_DEG = 225.0
SMOKE_THRESHOLD = 0.50
OUTPUT_DIR = Path(__file__).resolve().parent / 'benchmark_data'
STRATEGY_IDS = ('no_kmeans', 'kmeans', 'kmeans_wind', 'kmeans_swarm')


def select_fire_cells(cells, number, seed=42):
    """Reproducible sample of distinct fire cells without replacement.

    Selecting different cells is NOT independent environmental randomization.
    """
    ordered = sorted(cells, key=lambda c: (c.row, c.col, c.id))
    if not 1 <= number <= len(ordered):
        raise ValueError(f'Số vị trí cháy phải trong [1,{len(ordered)}].')
    rng = random.Random(seed)
    return rng.sample(ordered, number)


def _num(x, digits=2):
    return '' if x is None else round(float(x), digits)


def _mean(values):
    return statistics.mean(values) if values else None


def main():
    parser = argparse.ArgumentParser(description='Thực nghiệm 4 thuật toán UAV trên cùng bộ cell cháy.')
    parser.add_argument('mission_id', type=int)
    parser.add_argument('number_of_locations', nargs='?', type=int, default=DEFAULT_FIRE_LOCATIONS)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--horizon', type=float, default=None,
                        help='Giới hạn thời gian nhiệm vụ chung, giây; mặc định không giới hạn.')
    parser.add_argument('--wind', type=float, default=WIND_DIRECTION_DEG)
    parser.add_argument('--speed', type=float, default=UAV_SPEED_MPS)
    parser.add_argument('--threshold', type=float, default=SMOKE_THRESHOLD)
    args = parser.parse_args()
    if args.horizon is not None and (not math.isfinite(args.horizon) or args.horizon <= 0):
        parser.error('--horizon phải hữu hạn và > 0')
    mission = Mission.objects.select_related('area').get(pk=args.mission_id)
    cells = list(GridCell.objects.filter(area=mission.area).order_by('row', 'col', 'id'))
    fires = select_fire_cells(cells, args.number_of_locations, args.seed)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    detail_path = OUTPUT_DIR / f'mission_{mission.id}_detail_{stamp}.csv'
    summary_path = OUTPUT_DIR / f'mission_{mission.id}_summary_{stamp}.csv'
    config_path = OUTPUT_DIR / f'mission_{mission.id}_config_{stamp}.json'
    config_path.write_text(json.dumps({
        'mission_id': mission.id, 'area_id': mission.area_id,
        'fire_cell_ids': [c.id for c in fires], 'seed': args.seed,
        'horizon_seconds': args.horizon, 'wind_direction_deg': args.wind,
        'uav_speed_mps': args.speed, 'smoke_threshold': args.threshold,
        'note': '100 distinct fire cells are not 100 independent noise/wind realizations',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = ['experiment_id','mission_id','area_id','fire_cell_id','fire_row','fire_col',
              'strategy_id','strategy','detected','detection_time_seconds',
              'elapsed_simulation_seconds','termination_reason','travel_distance_m',
              'fleet_travel_distance_m','checked_cells','unique_checked_cells',
              'detected_by_uav','first_smoke_time_seconds','search_triggers',
              'swarm_iterations','pso_revisits','pso_recovery_steps',
              'shared_best_used','wind_reroute_count','mission_horizon_seconds',
              'uav_speed_mps','wind_direction_deg','smoke_threshold','status','error','raw_result_json']
    grouped = defaultdict(list)
    errors = []
    with detail_path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, fire in enumerate(fires, 1):
            base = dict(experiment_id=f'M{mission.id}_F{fire.id}', mission_id=mission.id,
                        area_id=mission.area_id, fire_cell_id=fire.id,
                        fire_row=fire.row, fire_col=fire.col,
                        mission_horizon_seconds=args.horizon, uav_speed_mps=args.speed,
                        wind_direction_deg=args.wind, smoke_threshold=args.threshold)
            print(f'[{index}/{len(fires)}] cell cháy {fire.id}')
            try:
                response = compare_detection_strategies(
                    mission=mission, fire_cell_id=fire.id,
                    wind_direction_deg=args.wind, uav_speed_mps=args.speed,
                    smoke_threshold=args.threshold, mission_horizon_seconds=args.horizon)
                results = response.get('results', [])
                ids = [r.get('strategy') for r in results]
                if len(results) != len(STRATEGY_IDS) or set(ids) != set(STRATEGY_IDS):
                    raise ValueError(f'Thiếu hoặc trùng chiến lược: {ids}')
                # Commit all four results together only after validating the scenario.
                rows = []
                for r in results:
                    detected = bool(r.get('detected'))
                    t = r.get('detected_time_seconds')
                    if detected != (t is not None):
                        raise ValueError(f'Thời gian/trạng thái không nhất quán: {r.get("strategy")}')
                    row = dict(base, strategy_id=r['strategy'], strategy=r['label'],
                        detected=detected, detection_time_seconds=t,
                        elapsed_simulation_seconds=r.get('elapsed_simulation_seconds'),
                        termination_reason=r.get('termination_reason'),
                        travel_distance_m=r.get('travel_distance_m'),
                        fleet_travel_distance_m=r.get('fleet_travel_distance_m'),
                        checked_cells=r.get('checked_cells'),
                        unique_checked_cells=r.get('unique_checked_cells'),
                        detected_by_uav=r.get('detected_by_uav'),
                        first_smoke_time_seconds=r.get('first_smoke_time_seconds'),
                        search_triggers=r.get('search_triggers',0),
                        swarm_iterations=r.get('swarm_iterations',0),
                        pso_revisits=r.get('pso_revisits',0),
                        pso_recovery_steps=r.get('pso_recovery_steps',0),
                        shared_best_used=r.get('shared_best_used',0),
                        wind_reroute_count=len(r.get('wind_reroute_events',[])),
                        status='ok', error='', raw_result_json=json.dumps(r,ensure_ascii=False,default=str))
                    rows.append(row)
                for row in rows:
                    writer.writerow(row)
                    grouped[row['strategy_id']].append(row)
            except Exception as exc:
                errors.append((fire.id, str(exc)))
                writer.writerow(dict(base, strategy_id='ALL', strategy='ALL', status='error', error=str(exc)))
                print(f'  LỖI: {exc}')
            handle.flush()
    summaries = ['Chiến lược','Số kịch bản hợp lệ','Số thành công','Số thất bại',
                 'Tỷ lệ thành công (%)','Thời gian TB khi thành công (giây)',
                 'Trung vị khi thành công (giây)','Độ lệch chuẩn thời gian (giây)',
                 'Số lượt quan sát TB','Số cell duy nhất TB','Quãng đường toàn đội TB (m)',
                 'Số kích hoạt SEARCH TB','Số revisit PSO TB','Số bước phục hồi PSO TB']
    with summary_path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=summaries)
        writer.writeheader()
        for sid in STRATEGY_IDS:
            rows = grouped[sid]
            if not rows:
                continue
            successful = [r for r in rows if r['detected']]
            times = [float(r['detection_time_seconds']) for r in successful]
            def mean_field(key):
                vals = [float(r[key]) for r in rows if r.get(key) is not None]
                return _num(_mean(vals))
            writer.writerow({
                'Chiến lược': rows[0]['strategy'],
                'Số kịch bản hợp lệ': len(rows), 'Số thành công': len(successful),
                'Số thất bại': len(rows)-len(successful),
                'Tỷ lệ thành công (%)': _num(100*len(successful)/len(rows)),
                'Thời gian TB khi thành công (giây)': _num(_mean(times)),
                'Trung vị khi thành công (giây)': _num(statistics.median(times) if times else None),
                'Độ lệch chuẩn thời gian (giây)': _num(statistics.stdev(times) if len(times)>1 else None),
                'Số lượt quan sát TB': mean_field('checked_cells'),
                'Số cell duy nhất TB': mean_field('unique_checked_cells'),
                'Quãng đường toàn đội TB (m)': mean_field('fleet_travel_distance_m'),
                'Số kích hoạt SEARCH TB': mean_field('search_triggers'),
                'Số revisit PSO TB': mean_field('pso_revisits'),
                'Số bước phục hồi PSO TB': mean_field('pso_recovery_steps'),
            })
    print(f'Chi tiết: {detail_path}\nTổng hợp: {summary_path}\nCấu hình: {config_path}')
    print(f'Kịch bản lỗi: {len(errors)}; kịch bản hợp lệ: {len(fires)-len(errors)}/{len(fires)}')


if __name__ == '__main__':
    main()
