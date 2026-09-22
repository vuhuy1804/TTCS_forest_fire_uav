from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

import numpy as np

from .models import ForestArea, GridCell, UAV, Mission, ClusterAssignment, RouteWaypoint, DetectionEvent
from .serializers import (
    ForestAreaSerializer, GridCellSerializer, UAVSerializer, MissionSerializer,
    ClusterAssignmentSerializer, RouteWaypointSerializer, DetectionEventSerializer
)
from .services import build_grid_cells, kmeans_assign, zigzag_route
from .services import bbox_from_polygon, build_grid_cells_filtered
from .experiment_services import compare_detection_strategies
from .simulation_services import run_simulation as run_simulation_service


@api_view(["POST"])
def create_area(request):
    """
    { 
      "name": "Forest A",
      "geometry": {GeoJSON Polygon}
    }
    """
    name = request.data.get("name", "Forest Area")
    geometry = request.data.get("geometry")
    if not geometry or geometry.get("type") != "Polygon":
        return Response({"detail": "geometry (GeoJSON Polygon) is required"}, status=400)

    try:
        bbox = bbox_from_polygon(geometry)
    except Exception as e:
        return Response({"detail": f"Invalid polygon: {e}"}, status=400)

    area = ForestArea.objects.create(name=name, geometry=geometry, bbox=bbox)
    return Response(ForestAreaSerializer(area).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def generate_grid(request, area_id: int):
    """
    { "rows": 20, "cols": 20 }
    """
    area = ForestArea.objects.get(id=area_id)
    rows = int(request.data.get("rows", 20))
    cols = int(request.data.get("cols", 20))

    if not area.geometry:
        return Response({"detail": "Area has no geometry polygon."}, status=400)

    GridCell.objects.filter(area=area).delete()

    cells = build_grid_cells_filtered(area.bbox, rows, cols, area.geometry)

    objs = []
    for c in cells:
        objs.append(GridCell(
            area=area,
            row=c["row"],
            col=c["col"],
            polygon=c["polygon"],
            center=c["center"],
            risk=float(request.data.get("default_risk", 1.0)),
        ))
    GridCell.objects.bulk_create(objs)
    return Response({"created": len(objs)})


@api_view(["POST"])
def create_mission(request, area_id: int):
    """
    { "k": 5 }
    """
    area = ForestArea.objects.get(id=area_id)
    k = int(request.data.get("k", 3))
    mission = Mission.objects.create(area=area, k=k, status="draft")
    return Response(MissionSerializer(mission).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def run_kmeans(request, mission_id: int):
    """
    Chạy K-means phân cụm vùng giám sát (theo tọa độ cell center).
    Có thể dùng risk làm sample_weight (mở rộng theo báo cáo). :contentReference[oaicite:5]{index=5}
    """
    mission = Mission.objects.get(id=mission_id)
    cells = list(GridCell.objects.filter(area=mission.area).order_by("id"))

    if not cells:
        return Response({"detail": "No grid cells. Generate grid first."}, status=400)

    k = mission.k
    # đảm bảo có đủ UAV
    COLORS = ["#e6194B","#3cb44b","#4363d8","#f58231","#911eb4","#46f0f0","#f032e6","#bcf60c"]
    while UAV.objects.count() < k:
        idx = UAV.objects.count()
        UAV.objects.create(name=f"UAV-{idx+1}", color=COLORS[idx % len(COLORS)])

    uavs = list(UAV.objects.all().order_by("id")[:k])

    points = np.array([[c.center["lng"], c.center["lat"]] for c in cells], dtype=float)
    weights = np.array([c.risk for c in cells], dtype=float)

    labels, centers = kmeans_assign(points, k=k, sample_weight=weights)

    ClusterAssignment.objects.filter(mission=mission).delete()

    assigns = []
    for idx, cell in enumerate(cells):
        cluster_id = int(labels[idx])
        assigns.append(ClusterAssignment(
            mission=mission,
            cell=cell,
            cluster_id=cluster_id,
            uav=uavs[cluster_id],
        ))
    ClusterAssignment.objects.bulk_create(assigns)

    mission.status = "planned"
    mission.save(update_fields=["status"])

    used_uav_ids = list(
        ClusterAssignment.objects.filter(mission=mission)
        .values_list("uav_id", flat=True)
        .distinct()
    )

    if used_uav_ids:
        UAV.objects.filter(id__in=used_uav_ids).update(status="assigned")

    return Response({
        "k": k,
        "centers": [{"cluster_id": i, "lng": float(centers[i,0]), "lat": float(centers[i,1]), "uav_id": uavs[i].id} for i in range(k)]
    })


@api_view(["POST"])
def generate_routes(request, mission_id: int):
    """
    Tạo tuyến bay tối ưu trong giới hạn cụm (demo: zigzag). :contentReference[oaicite:6]{index=6}
    """
    mission = Mission.objects.get(id=mission_id)
    RouteWaypoint.objects.filter(mission=mission).delete()

    assigns = ClusterAssignment.objects.filter(mission=mission).select_related("cell", "uav")
    if not assigns.exists():
        return Response({"detail": "Run kmeans first."}, status=400)

    # gom cell theo UAV
    by_uav = {}
    for a in assigns:
        by_uav.setdefault(a.uav_id, {"uav": a.uav, "cells": []})
        by_uav[a.uav_id]["cells"].append({
            "row": a.cell.row,
            "col": a.cell.col,
            "center": a.cell.center,
        })

    total = 0
    for _, group in by_uav.items():
        uav = group["uav"]
        # sort cells deterministically then zigzag
        cells_sorted = sorted(group["cells"], key=lambda x: (x["row"], x["col"]))
        route_points = zigzag_route(cells_sorted)
        wps = [
            RouteWaypoint(mission=mission, uav=uav, order=i, point=pt)
            for i, pt in enumerate(route_points)
        ]
        RouteWaypoint.objects.bulk_create(wps)
        total += len(wps)

    return Response({"created_waypoints": total})


@api_view(["GET"])
def mission_state(request, mission_id: int):
    """
    Trả về toàn bộ dữ liệu để frontend vẽ map: area, grid, assignments, routes, detections.
    """
    mission = Mission.objects.get(id=mission_id)
    area = mission.area
    cells = GridCell.objects.filter(area=area).order_by("row", "col")
    assigns = ClusterAssignment.objects.filter(mission=mission).select_related("cell", "uav")
    routes = RouteWaypoint.objects.filter(mission=mission).select_related("uav")
    detections = DetectionEvent.objects.filter(mission=mission).select_related("cell", "uav")[:20]

    return Response({
        "mission": MissionSerializer(mission).data,
        "area": {
            **ForestAreaSerializer(area).data,
            "geometry": area.geometry,
        },
        "cells": GridCellSerializer(cells, many=True).data,
        "assignments": [{
            "cell_id": a.cell_id,
            "cluster_id": a.cluster_id,
            "uav": UAVSerializer(a.uav).data if a.uav else None,
        } for a in assigns],
        "routes": [{
            "uav_id": r.uav_id,
            "uav_name": r.uav.name,
            "order": r.order,
            "point": r.point,
        } for r in routes],
        "recent_detections": [{
            "id": d.id,
            "cell_id": d.cell_id,
            "uav": UAVSerializer(d.uav).data if d.uav else None,
            "label": d.label,
            "confidence": d.confidence,
            "source": d.source,
            "created_at": d.created_at,
        } for d in detections],
    })


@api_view(["POST"])
def ingest_detection(request):
    """
    Nhận detection từ mock frontend hoặc AI service.
    Payload tối thiểu:
    {
      "cell_id": 123,
      "label": 0|1|2,
      "confidence": 0.9,
      "source": "mock|ai_service|manual",
      "payload": {...}
    }
    """
    try:
        cell_id = int(request.data["cell_id"])
        label = int(request.data["label"])
    except (KeyError, ValueError, TypeError):
        return Response({"detail": "cell_id và label là bắt buộc"}, status=400)

    if label not in [0, 1, 2]:
        return Response({"detail": "label phải là 0, 1 hoặc 2"}, status=400)

    conf = float(request.data.get("confidence", 1.0))
    source = request.data.get("source", "mock")
    payload = request.data.get("payload", {})

    if source not in ["mock", "ai_service", "manual"]:
        source = "mock"

    cell = GridCell.objects.select_related("area").get(id=cell_id)

    # cập nhật trạng thái hiện tại của cell
    cell.label = label
    cell.confidence = conf
    cell.save(update_fields=["label", "confidence", "updated_at"])

    # lấy mission mới nhất của area này
    mission = Mission.objects.filter(area=cell.area).order_by("-created_at").first()

    assignment = None
    if mission:
        assignment = (
            ClusterAssignment.objects
            .filter(mission=mission, cell=cell)
            .select_related("uav")
            .first()
        )

    event = DetectionEvent.objects.create(
        mission=mission,
        cell=cell,
        uav=assignment.uav if assignment else None,
        label=label,
        confidence=conf,
        source=source,
        payload=payload if isinstance(payload, dict) else {},
    )

    return Response({
        "ok": True,
        "event_id": event.id,
    })


@api_view(["POST"])
def compare_experiment_strategies(request, mission_id: int):
    """
    So sánh 3 chiến lược tìm kiếm.

    Payload:

    {
        "fire_cell_id": 2614,
        "wind_direction_deg": 60,

        "uav_speed": 10,

        "smoke_angle_deg": 35,
        "smoke_radius_m": 1000,

        "search_angle_deg": 40,
        "search_radius_m": 1200
    }

    uav_speed đơn vị: m/s
    """

    try:
        mission = Mission.objects.get(id=mission_id)
    except Mission.DoesNotExist:
        return Response(
            {"detail": "Mission không tồn tại."},
            status=404,
        )

    # -------------------------
    # fire cell
    # -------------------------
    try:
        fire_cell_id = int(request.data["fire_cell_id"])
    except (KeyError, TypeError, ValueError):
        return Response(
            {"detail": "fire_cell_id là bắt buộc."},
            status=400,
        )

    # -------------------------
    # các tham số mô phỏng
    # -------------------------
    try:
        wind_direction_deg = float(
            request.data.get("wind_direction_deg", 0)
        )

        uav_speed = float(
            request.data.get("uav_speed", 10.0)
        )

        smoke_angle_deg = float(
            request.data.get("smoke_angle_deg", 35)
        )

        search_angle_deg = float(
            request.data.get("search_angle_deg", 40)
        )

    except (TypeError, ValueError):
        return Response(
            {
                "detail":
                "wind_direction_deg, uav_speed, smoke_angle_deg, search_angle_deg phải là số."
            },
            status=400,
        )

    smoke_radius_raw = request.data.get("smoke_radius_m")
    search_radius_raw = request.data.get("search_radius_m")

    try:
        smoke_radius_m = (
            float(smoke_radius_raw)
            if smoke_radius_raw not in [None, ""]
            else None
        )

        search_radius_m = (
            float(search_radius_raw)
            if search_radius_raw not in [None, ""]
            else None
        )

    except (TypeError, ValueError):
        return Response(
            {
                "detail":
                "smoke_radius_m và search_radius_m phải là số."
            },
            status=400,
        )

    # -------------------------
    # validate
    # -------------------------
    if uav_speed <= 0:
        return Response(
            {"detail": "uav_speed phải lớn hơn 0."},
            status=400,
        )

    if smoke_angle_deg <= 0 or smoke_angle_deg > 180:
        return Response(
            {
                "detail":
                "smoke_angle_deg phải nằm trong khoảng 0..180."
            },
            status=400,
        )

    if search_angle_deg <= 0 or search_angle_deg > 180:
        return Response(
            {
                "detail":
                "search_angle_deg phải nằm trong khoảng 0..180."
            },
            status=400,
        )

    # -------------------------
    # chạy mô phỏng
    # -------------------------
    try:

        result = compare_detection_strategies(
            mission=mission,
            fire_cell_id=fire_cell_id,
            wind_direction_deg=wind_direction_deg,

            uav_speed=uav_speed,

            smoke_angle_deg=smoke_angle_deg,
            smoke_radius_m=smoke_radius_m,

            search_angle_deg=search_angle_deg,
            search_radius_m=search_radius_m,
        )

    except ValueError as e:
        return Response(
            {"detail": str(e)},
            status=400,
        )

    return Response(
        result,
        content_type="application/json; charset=utf-8",
    )

@api_view(["POST"])
def run_simulation(request, mission_id):

    try:
        mission = Mission.objects.get(id=mission_id)

    except Mission.DoesNotExist:

        return Response(
            {"detail": "Mission not found"},
            status=404,
        )

    try:
        fire_cell_id = int(request.data["fire_cell_id"])
    except (KeyError, ValueError, TypeError):
        return Response(
            {"detail": "fire_cell_id is required"},
            status=400,
        )

    wind_direction_deg = float(
        request.data.get(
            "wind_direction_deg",
            0,
        )
    )

    uav_speed = float(
        request.data.get(
            "uav_speed",
            10,
        )
    )

    if uav_speed <= 0:
        return Response(
            {"detail": "uav_speed must be greater than 0"},
            status=400,
        )

    smoke_angle_deg = float(
        request.data.get(
            "smoke_angle_deg",
            35,
        )
    )

    search_angle_deg = float(
        request.data.get(
            "search_angle_deg",
            40,
        )
    )

    smoke_radius = request.data.get("smoke_radius_m")
    search_radius = request.data.get("search_radius_m")

    if smoke_radius not in [None, ""]:
        smoke_radius = float(smoke_radius)

    else:
        smoke_radius = None

    if search_radius not in [None, ""]:
        search_radius = float(search_radius)

    else:
        search_radius = None

    result = run_simulation_service(

        mission=mission,

        fire_cell_id=fire_cell_id,

        wind_direction_deg=wind_direction_deg,

        uav_speed=uav_speed,

        smoke_angle_deg=smoke_angle_deg,

        smoke_radius_m=smoke_radius,

        search_angle_deg=search_angle_deg,

        search_radius_m=search_radius,

    )

    return Response(result)

@api_view(["POST"])
def update_mission_status(request, mission_id: int):
    """
    Cập nhật trạng thái mission:
    draft | planned | running | paused | finished

    Logic:
    - planned -> running: Start Mission (khởi tạo UAV từ waypoint đầu)
    - paused -> running: Resume Mission (tiếp tục từ trạng thái hiện tại, không reset)
    - running -> paused: Pause Mission
    - * -> finished: Finish Mission (reset execution state)
    """
    allowed = ["draft", "planned", "running", "paused", "finished"]
    new_status = request.data.get("status")

    if new_status not in allowed:
      return Response(
          {"detail": f"status phải thuộc {allowed}"},
          status=400,
      )

    mission = Mission.objects.get(id=mission_id)
    old_status = mission.status

    used_uav_ids = list(
        ClusterAssignment.objects.filter(mission=mission)
        .values_list("uav_id", flat=True)
        .distinct()
    )

    # 1) Cập nhật mission status trước
    mission.status = new_status
    mission.save(update_fields=["status"])

    # 2) Xác định uav_status tương ứng
    uav_status = None
    if new_status == "draft":
        uav_status = "idle"
    elif new_status == "planned":
        uav_status = "assigned"
    elif new_status == "running":
        uav_status = "running"
    elif new_status == "paused":
        uav_status = "paused"
    elif new_status == "finished":
        uav_status = "idle"

    if used_uav_ids and uav_status:
        UAV.objects.filter(id__in=used_uav_ids).update(status=uav_status)

    # 3) Start Mission lần đầu: chỉ khi từ planned -> running
    if old_status == "planned" and new_status == "running":
        for uav_id in used_uav_ids:
            first_wp = (
                RouteWaypoint.objects
                .filter(mission=mission, uav_id=uav_id)
                .order_by("order")
                .first()
            )
            if first_wp:
                uav = UAV.objects.get(id=uav_id)
                uav.current_position = first_wp.point
                uav.current_waypoint_index = 0
                uav.route_direction = 1
                if not uav.battery_level:
                    uav.battery_level = 100.0
                uav.last_seen_at = timezone.now()
                uav.save(update_fields=[
                    "current_position",
                    "current_waypoint_index",
                    "route_direction",
                    "battery_level",
                    "last_seen_at",
                ])

    # 4) Resume Mission: paused -> running
    # KHÔNG reset vị trí, KHÔNG reset waypoint index, KHÔNG reset direction
    if old_status == "paused" and new_status == "running":
        for uav_id in used_uav_ids:
            uav = UAV.objects.get(id=uav_id)
            uav.last_seen_at = timezone.now()
            uav.save(update_fields=["last_seen_at"])

    # 5) Finish Mission: reset execution state
    if new_status == "finished":
        for uav_id in used_uav_ids:
            uav = UAV.objects.get(id=uav_id)
            uav.current_waypoint_index = 0
            uav.route_direction = 1
            uav.current_position = None
            uav.last_seen_at = timezone.now()
            uav.save(update_fields=[
                "current_waypoint_index",
                "route_direction",
                "current_position",
                "last_seen_at",
            ])

    return Response({
        "ok": True,
        "mission_id": mission.id,
        "old_status": old_status,
        "status": mission.status,
        "uav_status": uav_status,
    })

@api_view(["POST"])
def restart_mission(request, mission_id: int):
    """
    Restart một mission đã finished:
    - mission -> running
    - UAV -> running
    - reset execution state về đầu route
    - battery reset về 100
    """
    mission = Mission.objects.get(id=mission_id)

    if mission.status != "finished":
        return Response(
            {"detail": "Chỉ restart được mission đang ở trạng thái finished"},
            status=400,
        )

    used_uav_ids = list(
        ClusterAssignment.objects.filter(mission=mission)
        .values_list("uav_id", flat=True)
        .distinct()
    )

    mission.status = "running"
    mission.save(update_fields=["status"])

    if used_uav_ids:
        UAV.objects.filter(id__in=used_uav_ids).update(status="running")

    for uav_id in used_uav_ids:
        first_wp = (
            RouteWaypoint.objects
            .filter(mission=mission, uav_id=uav_id)
            .order_by("order")
            .first()
        )

        if not first_wp:
            continue

        uav = UAV.objects.get(id=uav_id)
        uav.current_position = first_wp.point
        uav.current_waypoint_index = 0
        uav.route_direction = 1
        uav.battery_level = 100.0
        uav.last_seen_at = timezone.now()
        uav.save(update_fields=[
            "current_position",
            "current_waypoint_index",
            "route_direction",
            "battery_level",
            "last_seen_at",
        ])

    return Response({
        "ok": True,
        "mission_id": mission.id,
        "status": mission.status,
        "uav_status": "running",
    })

@api_view(["POST"])
def simulate_mission_tick(request, mission_id: int):
    """
    Giả lập UAV tiến thêm 1 bước trên route.
    UAV sẽ đi tới cuối route rồi quay đầu tuần tra ngược lại.
    Chỉ hoạt động khi mission đang ở trạng thái running.
    """
    mission = Mission.objects.get(id=mission_id)

    if mission.status != "running":
        return Response(
            {"detail": "Mission phải ở trạng thái running để simulate tick"},
            status=400,
        )

    used_uav_ids = list(
        ClusterAssignment.objects.filter(mission=mission)
        .values_list("uav_id", flat=True)
        .distinct()
    )

    updated = []

    for uav_id in used_uav_ids:
        uav = UAV.objects.get(id=uav_id)

        waypoints = list(
            RouteWaypoint.objects
            .filter(mission=mission, uav_id=uav_id)
            .order_by("order")
        )

        if not waypoints:
            continue

        if len(waypoints) == 1:
            uav.current_waypoint_index = 0
            uav.current_position = waypoints[0].point
            uav.last_seen_at = timezone.now()
            uav.battery_level = max((uav.battery_level or 100.0) - 0.2, 0.0)
            uav.save(update_fields=[
                "current_waypoint_index",
                "current_position",
                "last_seen_at",
                "battery_level",
            ])

            updated.append({
                "uav_id": uav.id,
                "name": uav.name,
                "current_waypoint_index": uav.current_waypoint_index,
                "route_direction": uav.route_direction,
                "current_position": uav.current_position,
                "battery_level": uav.battery_level,
            })
            continue

        current_index = uav.current_waypoint_index or 0
        direction = uav.route_direction or 1

        next_index = current_index + direction

        if next_index >= len(waypoints):
            direction = -1
            next_index = len(waypoints) - 2

        elif next_index < 0:
            direction = 1
            next_index = 1

        next_wp = waypoints[next_index]

        uav.current_waypoint_index = next_index
        uav.route_direction = direction
        uav.current_position = next_wp.point
        uav.last_seen_at = timezone.now()
        uav.battery_level = max((uav.battery_level or 100.0) - 0.5, 0.0)

        uav.save(update_fields=[
            "current_waypoint_index",
            "route_direction",
            "current_position",
            "last_seen_at",
            "battery_level",
        ])

        updated.append({
            "uav_id": uav.id,
            "name": uav.name,
            "current_waypoint_index": uav.current_waypoint_index,
            "route_direction": uav.route_direction,
            "current_position": uav.current_position,
            "battery_level": uav.battery_level,
        })

    return Response({
        "ok": True,
        "mission_id": mission.id,
        "updated": updated,
    })

@api_view(["POST"])
def set_mission_auto_simulation(request, mission_id: int):
    """
    Bật/tắt auto simulation cho mission.
    Lưu tạm vào payload đơn giản trong response workflow hiện tại bằng cách
    trả về trạng thái cho frontend tự giữ.
    Endpoint này chủ yếu để chuẩn hóa API điều khiển.
    """
    enabled = bool(request.data.get("enabled", False))
    return Response({
        "ok": True,
        "mission_id": mission_id,
        "auto_simulation": enabled,
    })
