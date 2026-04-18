from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

import numpy as np

from .models import ForestArea, GridCell, UAV, Mission, ClusterAssignment, RouteWaypoint, DetectionEvent
from .serializers import (
    ForestAreaSerializer, GridCellSerializer, UAVSerializer, MissionSerializer,
    ClusterAssignmentSerializer, RouteWaypointSerializer, DetectionEventSerializer
)
from .services import build_grid_cells, kmeans_assign, zigzag_route
from .services import bbox_from_polygon, build_grid_cells_filtered


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
    mission = Mission.objects.create(area=area, k=k)
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
