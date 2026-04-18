from dataclasses import dataclass
from typing import List, Dict, Tuple
import numpy as np
from sklearn.cluster import KMeans

def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def build_grid_cells(bbox, rows: int, cols: int):
    """
    bbox: [minLng, minLat, maxLng, maxLat]
    returns list of dict: {row,col,polygon,center}
    """
    min_lng, min_lat, max_lng, max_lat = bbox
    cells = []

    for r in range(rows):
        for c in range(cols):
            x0 = lerp(min_lng, max_lng, c / cols)
            x1 = lerp(min_lng, max_lng, (c + 1) / cols)
            y0 = lerp(min_lat, max_lat, r / rows)
            y1 = lerp(min_lat, max_lat, (r + 1) / rows)

            center = {"lng": (x0 + x1) / 2.0, "lat": (y0 + y1) / 2.0}
            polygon = {
                "type": "Polygon",
                "coordinates": [[
                    [x0, y0],
                    [x1, y0],
                    [x1, y1],
                    [x0, y1],
                    [x0, y0],
                ]]
            }
            cells.append({"row": r, "col": c, "polygon": polygon, "center": center})
    return cells

def kmeans_assign(points: np.ndarray, k: int, sample_weight=None) -> Tuple[np.ndarray, np.ndarray]:
    """
    points: Nx2 (lng, lat)
    returns labels (N,), centers (k,2)
    """
    km = KMeans(n_clusters=k, n_init="auto", random_state=42)
    km.fit(points, sample_weight=sample_weight)
    return km.labels_, km.cluster_centers_

def zigzag_route(cells_sorted: List[Dict]) -> List[Dict]:
    """
    cells_sorted: list of {row,col,center}
    strategy: zigzag theo hàng (row), đảo chiều mỗi hàng
    """
    # group by row
    by_row: Dict[int, List[Dict]] = {}
    for cell in cells_sorted:
        by_row.setdefault(cell["row"], []).append(cell)

    route = []
    for r in sorted(by_row.keys()):
        row_cells = sorted(by_row[r], key=lambda x: x["col"])
        if r % 2 == 1:
            row_cells.reverse()
        route.extend(row_cells)

    return [c["center"] for c in route]

def bbox_from_polygon(geometry: Dict) -> List[float]:
    """
    geometry: GeoJSON Polygon
    returns [minLng, minLat, maxLng, maxLat]
    """
    coords = geometry.get("coordinates", [])
    if not coords or not coords[0]:
        raise ValueError("Invalid GeoJSON Polygon")
    ring = coords[0]  # outer ring
    lngs = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return [min(lngs), min(lats), max(lngs), max(lats)]

def point_in_polygon(point: Tuple[float, float], polygon_ring: List[List[float]]) -> bool:
    """
    Ray casting algorithm
    point: (lng, lat)
    polygon_ring: [[lng,lat], ...] (outer ring, may include closing point)
    """
    x, y = point
    inside = False
    n = len(polygon_ring)
    if n < 3:
        return False

    for i in range(n):
        x1, y1 = polygon_ring[i]
        x2, y2 = polygon_ring[(i + 1) % n]
        # check intersection with horizontal ray
        intersect = ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-15) + x1)
        if intersect:
            inside = not inside
    return inside

def build_grid_cells_filtered(bbox, rows: int, cols: int, polygon_geometry: Dict):
    """
    Tạo grid trong bbox nhưng CHỈ giữ cell có center nằm trong polygon.
    """
    min_lng, min_lat, max_lng, max_lat = bbox
    ring = polygon_geometry["coordinates"][0]  # outer ring

    cells = []
    for r in range(rows):
        for c in range(cols):
            x0 = lerp(min_lng, max_lng, c / cols)
            x1 = lerp(min_lng, max_lng, (c + 1) / cols)
            y0 = lerp(min_lat, max_lat, r / rows)
            y1 = lerp(min_lat, max_lat, (r + 1) / rows)

            center_lng = (x0 + x1) / 2.0
            center_lat = (y0 + y1) / 2.0

            if not point_in_polygon((center_lng, center_lat), ring):
                continue

            center = {"lng": center_lng, "lat": center_lat}
            polygon = {
                "type": "Polygon",
                "coordinates": [[
                    [x0, y0],
                    [x1, y0],
                    [x1, y1],
                    [x0, y1],
                    [x0, y0],
                ]]
            }
            cells.append({"row": r, "col": c, "polygon": polygon, "center": center})
    return cells