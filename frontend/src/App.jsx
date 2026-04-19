import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  MapContainer,
  TileLayer,
  Polygon,
  Polyline,
  CircleMarker,
  Marker,
  Popup,
  Tooltip,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "@geoman-io/leaflet-geoman-free";
import { post, get } from "./api";

const STORAGE_KEYS = {
  areaId: "ffuav_area_id",
  missionId: "ffuav_mission_id",
};

function DrawControl({ onPolygonChange }) {
  const map = useMap();
  const layerRef = useRef(null);

  React.useEffect(() => {
    map.pm.addControls({
      position: "topleft",
      drawCircle: false,
      drawMarker: false,
      drawPolyline: false,
      drawRectangle: false,
      drawCircleMarker: false,
      cutPolygon: false,
    });

    const onCreate = (e) => {
      if (layerRef.current) {
        map.removeLayer(layerRef.current);
      }
      layerRef.current = e.layer;

      const geo = e.layer.toGeoJSON().geometry;
      onPolygonChange?.(geo);
    };

    map.on("pm:create", onCreate);

    return () => {
      map.off("pm:create", onCreate);
      if (layerRef.current) {
        map.removeLayer(layerRef.current);
        layerRef.current = null;
      }
    };
  }, [map, onPolygonChange]);

  return null;
}

function FocusCellOnMap({ focusedCellId, cells }) {
  const map = useMap();

  useEffect(() => {
    if (!focusedCellId || !cells?.length) return;

    const target = cells.find((c) => c.id === focusedCellId);
    if (!target) return;

    map.flyTo([target.center.lat, target.center.lng], 16, {
      duration: 0.8,
    });
  }, [focusedCellId, cells, map]);

  return null;
}

function cellColor(label) {
  if (label === 2) return "red";
  if (label === 1) return "gray";
  return "#3388ff";
}

function cellFillColor(label, uavColor) {
  if (label === 2) return "#ff4d4f";   // lửa
  if (label === 1) return "#8c8c8c";   // khói
  return uavColor || "#3388ff";        // bình thường thì theo cluster
}

function cellFillOpacity(label) {
  if (label === 2) return 0.7;
  if (label === 1) return 0.5;
  return 0.12;
}

function labelText(label) {
  if (label === 2) return "Lửa";
  if (label === 1) return "Khói";
  return "Bình thường";
}

function formatDateTime(value) {
  if (!value) return "N/A";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

function makeUavIcon(color = "#3388ff", text = "U") {
  return L.divIcon({
    className: "",
    html: `
      <div style="
        width: 28px;
        height: 28px;
        border-radius: 50%;
        background: ${color};
        border: 2px solid white;
        box-shadow: 0 0 0 1px #333;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 11px;
        font-weight: 700;
        color: white;
      ">
        ${text}
      </div>
    `,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -14],
  });
}

export default function App() {
  const [areaId, setAreaId] = useState(null);
  const [missionId, setMissionId] = useState(null);
  const [rows, setRows] = useState(20);
  const [cols, setCols] = useState(20);
  const [k, setK] = useState(5);
  const [state, setState] = useState(null);
  const [polygonGeo, setPolygonGeo] = useState(null);
  const [restoring, setRestoring] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [focusedCellId, setFocusedCellId] = useState(null);
  const [autoSimulation, setAutoSimulation] = useState(false);

  const center = useMemo(() => [21.0285, 105.8542], []);

  async function refreshState(targetMissionId = missionId) {
    if (!targetMissionId) {
      alert("Create mission first");
      return;
    }

    const s = await get(`/missions/${targetMissionId}/state/`);
    setState(s);

    if (s?.mission?.id) setMissionId(s.mission.id);
    if (s?.area?.id) setAreaId(s.area.id);
    if (s?.area?.geometry?.type === "Polygon") {
      setPolygonGeo(s.area.geometry);
    }
  }

  useEffect(() => {
    const savedAreaId = localStorage.getItem(STORAGE_KEYS.areaId);
    const savedMissionId = localStorage.getItem(STORAGE_KEYS.missionId);

    if (savedAreaId) setAreaId(Number(savedAreaId));
    if (savedMissionId) {
      const mid = Number(savedMissionId);
      setMissionId(mid);

      refreshState(mid)
        .catch((err) => {
          console.error("Restore mission failed:", err);
          localStorage.removeItem(STORAGE_KEYS.missionId);
        })
        .finally(() => setRestoring(false));
    } else {
      setRestoring(false);
    }
  }, []);

  useEffect(() => {
    if (areaId) localStorage.setItem(STORAGE_KEYS.areaId, String(areaId));
    else localStorage.removeItem(STORAGE_KEYS.areaId);
  }, [areaId]);

  useEffect(() => {
    if (missionId) localStorage.setItem(STORAGE_KEYS.missionId, String(missionId));
    else localStorage.removeItem(STORAGE_KEYS.missionId);
  }, [missionId]);

  useEffect(() => {
    if (!autoRefresh) return;
    if (!missionId) return;
    if (restoring) return;

    const intervalId = setInterval(() => {
      refreshState(missionId).catch((err) => {
        console.error("Auto refresh failed:", err);
      });
    }, 5000);

    return () => clearInterval(intervalId);
  }, [autoRefresh, missionId, restoring]);

  useEffect(() => {
    if (!autoSimulation) return;
    if (!missionId) return;
    if (state?.mission?.status !== "running") return;

    const intervalId = setInterval(async () => {
      try {
        await post(`/missions/${missionId}/tick/`, {});
        await refreshState(missionId);
      } catch (err) {
        console.error("Auto simulation tick failed:", err);
      }
    }, 3000);

    return () => clearInterval(intervalId);
  }, [autoSimulation, missionId, state?.mission?.status]);

  useEffect(() => {
    if (state?.mission?.status !== "running" && autoSimulation) {
      setAutoSimulation(false);
    }
  }, [state?.mission?.status, autoSimulation]);

  async function handleCreateAreaFromPolygon() {
    if (!polygonGeo || polygonGeo.type !== "Polygon") {
      alert("Bạn chưa vẽ polygon. Hãy dùng tool Draw (góc trái bản đồ) để vẽ vùng rừng.");
      return;
    }

    const area = await post("/areas/", {
      name: "Forest Polygon",
      geometry: polygonGeo,
    });

    setAreaId(area.id);
    setMissionId(null);
    setState(null);

    localStorage.setItem(STORAGE_KEYS.areaId, String(area.id));
    localStorage.removeItem(STORAGE_KEYS.missionId);

    alert(`Created area ${area.id}`);
  }

  async function handleGrid() {
    if (!areaId) return alert("Create area first");
    const res = await post(`/areas/${areaId}/grid/`, {
      rows,
      cols,
      default_risk: 1.0,
    });
    alert(`Grid generated. Created: ${res.created}`);
  }

  async function handleCreateMission() {
    if (!areaId) return alert("Create area first");
    const mission = await post(`/areas/${areaId}/missions/`, { k });
    setMissionId(mission.id);
    localStorage.setItem(STORAGE_KEYS.missionId, String(mission.id));
    alert(`Created mission ${mission.id}`);
  }

  async function handleKmeans() {
    if (!missionId) return alert("Create mission first");
    await post(`/missions/${missionId}/kmeans/`, {});
    alert("K-means done");
  }

  async function handleRoute() {
    if (!missionId) return alert("Create mission first");
    await post(`/missions/${missionId}/routes/`, {});
    alert("Routes generated (zigzag)");
  }

  async function handleMissionStatusChange(nextStatus) {
    if (!missionId) {
      alert("Create mission first");
      return;
    }

    await post(`/missions/${missionId}/status/`, { status: nextStatus });
    await refreshState();
  }

  async function handleRestartMission() {
    if (!missionId) {
      alert("Create mission first");
      return;
    }

    await post(`/missions/${missionId}/restart/`, {});
    await refreshState();
  }

  async function handleSimulateTick() {
    if (!missionId) {
      alert("Create mission first");
      return;
    }

    await post(`/missions/${missionId}/tick/`, {});
    await refreshState();
  }

  async function handleToggleAutoSimulation() {
    if (!missionId) {
      alert("Create mission first");
      return;
    }

    if (state?.mission?.status !== "running" && !autoSimulation) {
      alert("Mission phải ở trạng thái running mới bật được Auto Simulation");
      return;
    }

    const next = !autoSimulation;
    await post(`/missions/${missionId}/auto-simulation/`, { enabled: next });
    setAutoSimulation(next);
  }

  async function handleResetSession() {
    localStorage.removeItem(STORAGE_KEYS.areaId);
    localStorage.removeItem(STORAGE_KEYS.missionId);
    setAreaId(null);
    setMissionId(null);
    setState(null);
    setPolygonGeo(null);
    alert("Đã xóa session frontend. Dữ liệu backend vẫn còn trong database.");
  }

  async function toggleCell(cell) {
    const next = (cell.label + 1) % 3;
    await post("/detections/", {
      cell_id: cell.id,
      label: next,
      confidence: 0.9,
      source: "mock",
      payload: { trigger: "map_click" },
    });
    await refreshState();
  }

  const polygons = useMemo(() => {
    if (!state?.cells) return [];
    return state.cells.map((c) => {
      const coords = c.polygon.coordinates[0].map(([lng, lat]) => [lat, lng]);
      return {
        id: c.id,
        coords,
        center: [c.center.lat, c.center.lng],
        label: c.label,
      };
    });
  }, [state]);

  const routesByUav = useMemo(() => {
    if (!state?.routes) return new Map();
    const m = new Map();
    for (const r of state.routes) {
      const key = r.uav_id;
      if (!m.has(key)) m.set(key, []);
      m.get(key).push([r.point.lat, r.point.lng]);
    }
    return m;
  }, [state]);

  const assignmentMap = useMemo(() => {
    const m = new Map();
    if (!state?.assignments) return m;
    for (const a of state.assignments) {
      if (a?.cell_id) m.set(a.cell_id, a);
    }
    return m;
  }, [state]);

  const uavLegend = useMemo(() => {
    const m = new Map();
    if (!state?.assignments) return [];

    for (const a of state.assignments) {
      if (!a?.uav) continue;
      const id = a.uav.id;
      if (!m.has(id)) m.set(id, { uav: a.uav, count: 0 });
      m.get(id).count += 1;
    }

    return Array.from(m.values()).sort((x, y) => x.uav.id - y.uav.id);
  }, [state]);

  const uavMarkers = useMemo(() => {
    if (!state?.assignments || !state?.cells) return [];

    const cellMap = new Map();
    for (const c of state.cells) {
      cellMap.set(c.id, c);
    }

    const grouped = new Map();

    for (const a of state.assignments) {
      if (!a?.uav?.id || !a?.cell_id) continue;

      const uavId = a.uav.id;
      const cell = cellMap.get(a.cell_id);
      if (!cell) continue;

      if (!grouped.has(uavId)) {
        grouped.set(uavId, {
          uav: a.uav,
          cells: [],
        });
      }

      grouped.get(uavId).cells.push(cell);
    }

    const markers = [];

    for (const [, group] of grouped.entries()) {
      const { uav, cells } = group;
      if (!cells.length) continue;

      let lat = 0;
      let lng = 0;

      for (const cell of cells) {
        lat += cell.center.lat;
        lng += cell.center.lng;
      }

      lat /= cells.length;
      lng /= cells.length;

      markers.push({
        uav,
        count: cells.length,
        center: { lat, lng },
      });
    }

    return markers.sort((a, b) => a.uav.id - b.uav.id);
  }, [state]);

  const recentAlerts = useMemo(() => {
    if (!state?.recent_detections) return [];
    return state.recent_detections.filter((d) => d.label === 1 || d.label === 2);
  }, [state]);

  const missionSummary = useMemo(() => {
    const cells = state?.cells || [];
    const assignments = state?.assignments || [];

    let normalCount = 0;
    let smokeCount = 0;
    let fireCount = 0;

    for (const cell of cells) {
      if (cell.label === 2) fireCount += 1;
      else if (cell.label === 1) smokeCount += 1;
      else normalCount += 1;
    }

    const uniqueUavIds = new Set();
    for (const a of assignments) {
      if (a?.uav?.id) uniqueUavIds.add(a.uav.id);
    }

    return {
      missionId: state?.mission?.id || null,
      totalCells: cells.length,
      uavCount: uniqueUavIds.size,
      normalCount,
      smokeCount,
      fireCount,
      activeAlerts: smokeCount + fireCount,
    };
  }, [state]);

  const uavStatusList = useMemo(() => {
    const cells = state?.cells || [];
    const assignments = state?.assignments || [];
    const routes = state?.routes || [];

    const cellMap = new Map();
    for (const cell of cells) {
      cellMap.set(cell.id, cell);
    }

    const grouped = new Map();

    for (const a of assignments) {
      if (!a?.uav?.id || !a?.cell_id) continue;

      const uavId = a.uav.id;
      const cell = cellMap.get(a.cell_id);
      if (!cell) continue;

      if (!grouped.has(uavId)) {
        grouped.set(uavId, {
          uav: a.uav,
          cells: [],
        });
      }

      grouped.get(uavId).cells.push(cell);
    }

    const routeSet = new Set(routes.map((r) => r.uav_id));

    const result = [];

    for (const [, group] of grouped.entries()) {
      const { uav, cells } = group;

      let smokeCount = 0;
      let fireCount = 0;
      let latSum = 0;
      let lngSum = 0;

      for (const cell of cells) {
        if (cell.label === 1) smokeCount += 1;
        if (cell.label === 2) fireCount += 1;
        latSum += cell.center.lat;
        lngSum += cell.center.lng;
      }

      const centroid = cells.length
        ? {
            lat: latSum / cells.length,
            lng: lngSum / cells.length,
          }
        : null;

      result.push({
        uav,
        status: uav.status || "assigned",
        assignedCells: cells.length,
        smokeCount,
        fireCount,
        alertCount: smokeCount + fireCount,
        centroid,
        routeReady: routeSet.has(uav.id),
        currentPosition: uav.current_position || null,
        currentWaypointIndex: uav.current_waypoint_index ?? 0,
        routeDirection: uav.route_direction ?? 1,
        batteryLevel: uav.battery_level ?? 100,
        lastSeenAt: uav.last_seen_at || null,
      });
    }

    return result.sort((a, b) => a.uav.id - b.uav.id);
  }, [state]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", height: "100vh" }}>
      <div style={{ padding: 12, borderRight: "1px solid #ddd" }}>
        <h3>Forest Fire UAV Dashboard</h3>

        {restoring && (
          <div style={{ marginBottom: 8, fontSize: 12, color: "#666" }}>
            Đang khôi phục mission trước đó...
          </div>
        )}

        <div style={{ fontSize: 12, color: "#444" }}>
          <div><b>Bước 1:</b> Dùng tool Draw polygon (góc trái bản đồ) để vẽ vùng rừng.</div>
          <div><b>Bước 2:</b> Bấm “Create Area from Polygon”.</div>
        </div>

        <button onClick={handleCreateAreaFromPolygon} style={{ marginTop: 8 }}>
          1) Create Area from Polygon
        </button>

        <div style={{ marginTop: 12 }}>
          <div>Grid:</div>
          <label>
            Rows:{" "}
            <input type="number" value={rows} onChange={(e) => setRows(+e.target.value)} />
          </label>
          <br />
          <label>
            Cols:{" "}
            <input type="number" value={cols} onChange={(e) => setCols(+e.target.value)} />
          </label>
          <br />
          <button onClick={handleGrid}>2) Generate Grid (inside polygon)</button>
        </div>

        <div style={{ marginTop: 12 }}>
          <div>Mission / K-means:</div>
          <label>
            K (UAV):{" "}
            <input type="number" value={k} onChange={(e) => setK(+e.target.value)} />
          </label>
          <br />
          <button onClick={handleCreateMission}>3) Create Mission</button>
          <button onClick={handleKmeans} style={{ marginLeft: 8 }}>
            4) Run K-means
          </button>
        </div>

        <div style={{ marginTop: 12 }}>
          <button onClick={handleRoute}>5) Generate Routes (zigzag)</button>
          <button onClick={() => refreshState()} style={{ marginLeft: 8 }}>
            Refresh
          </button>
          <button onClick={() => setAutoRefresh((v) => !v)} style={{ marginLeft: 8 }}>
            {autoRefresh ? "Auto Refresh: ON" : "Auto Refresh: OFF"}
          </button>
          <button onClick={() => setFocusedCellId(null)} style={{ marginLeft: 8 }}>
            Clear Focus
          </button>
          <button onClick={handleResetSession} style={{ marginLeft: 8 }}>
            Clear Session
          </button>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>Mission Control</div>

          <div style={{ marginTop: 6, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {state?.mission?.status === "planned" && (
              <button onClick={() => handleMissionStatusChange("running")}>
                Start Mission
              </button>
            )}

            {state?.mission?.status === "running" && (
              <button onClick={() => handleMissionStatusChange("paused")}>
                Pause Mission
              </button>
            )}

            {state?.mission?.status === "paused" && (
              <button onClick={() => handleMissionStatusChange("running")}>
                Resume Mission
              </button>
            )}

            {(state?.mission?.status === "planned" ||
              state?.mission?.status === "running" ||
              state?.mission?.status === "paused") &&
              missionId && (
                <button onClick={() => handleMissionStatusChange("finished")}>
                  Finish Mission
                </button>
              )}

            {state?.mission?.status === "finished" && missionId && (
              <button onClick={handleRestartMission}>
                Restart Mission
              </button>
            )}

            {state?.mission?.status === "running" && (
              <button onClick={handleSimulateTick}>
                Simulate Tick
              </button>
            )}

            {state?.mission?.status === "running" && (
              <button onClick={handleToggleAutoSimulation}>
                {autoSimulation ? "Auto Sim: ON" : "Auto Sim: OFF"}
              </button>
            )}
          </div>

          <div style={{ marginTop: 6, fontSize: 12, color: "#666" }}>
            {state?.mission?.status === "planned" &&
              "Mission đã được lập kế hoạch, sẵn sàng bắt đầu từ waypoint đầu."}

            {state?.mission?.status === "running" &&
              "Mission đang chạy. UAV tiếp tục tuần tra theo route hiện tại."}

            {state?.mission?.status === "paused" &&
              "Mission đang tạm dừng. Resume sẽ tiếp tục từ vị trí hiện tại."}

            {state?.mission?.status === "finished" &&
              "Mission đã kết thúc một phiên tuần tra. Restart sẽ bắt đầu lại từ đầu route."}
          </div>
        </div>

        <div style={{ marginTop: 12, fontSize: 12, color: "#444" }}>
          <div>areaId: {String(areaId)}</div>
          <div>missionId: {String(missionId)}</div>
          <div>Mission status: {state?.mission?.status || "N/A"}</div>
          <div>Polygon: {polygonGeo ? "OK ✅" : "Chưa có"}</div>
          <div>Auto refresh: {autoRefresh ? "ON (5s)" : "OFF"}</div>
          <div>Focused cell: {focusedCellId ? `#${focusedCellId}` : "None"}</div>
          <div>Tip: Click a cell to mock AI label (0→1→2).</div>
          <div>Resume: tự động restore mission sau reload nếu đã có missionId.</div>
          <div>UAV execution: {state?.mission?.status === "running" ? "Simulated" : "Idle"}</div>
          <div>Auto simulation: {autoSimulation ? "ON" : "OFF"}</div>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>Mission Summary</div>

          <div
            style={{
              marginTop: 6,
              padding: 8,
              border: "1px solid #ddd",
              fontSize: 12,
              display: "grid",
              gap: 4,
              background: "#fafafa",
            }}
          >
            <div><b>Mission ID:</b> {missionSummary.missionId ?? "N/A"}</div>
            <div><b>Status:</b> {state?.mission?.status || "N/A"}</div>
            <div><b>UAVs:</b> {missionSummary.uavCount}</div>
            <div><b>Total Cells:</b> {missionSummary.totalCells}</div>
            <div><b>Normal:</b> {missionSummary.normalCount}</div>
            <div><b>Smoke:</b> {missionSummary.smokeCount}</div>
            <div><b>Fire:</b> {missionSummary.fireCount}</div>
            <div>
              <b>Active Alerts:</b>{" "}
              <span
                style={{
                  color: missionSummary.activeAlerts > 0 ? "#cf1322" : "#389e0d",
                  fontWeight: 700,
                }}
              >
                {missionSummary.activeAlerts}
              </span>
            </div>
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>UAV Status</div>

          {uavStatusList.length === 0 ? (
            <div style={{ marginTop: 6, fontSize: 12, color: "#666" }}>
              Chưa có dữ liệu UAV. Hãy tạo mission, chạy K-means và route.
            </div>
          ) : (
            <div style={{ marginTop: 6, display: "grid", gap: 8 }}>
              {uavStatusList.map((item) => (
                <div
                  key={item.uav.id}
                  style={{
                    padding: 8,
                    border: "1px solid #ddd",
                    fontSize: 12,
                    background: "#fafafa",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span
                      style={{
                        width: 12,
                        height: 12,
                        background: item.uav.color || "#3388ff",
                        display: "inline-block",
                        border: "1px solid #333",
                      }}
                    />
                    <b>{item.uav.name}</b>
                  </div>

                  <div>UAV ID: {item.uav.id}</div>
                  <div>
                    Status:{" "}
                    <span
                      style={{
                        fontWeight: 700,
                        color:
                          item.status === "running"
                            ? "#389e0d"
                            : item.status === "paused"
                            ? "#d48806"
                            : item.status === "offline"
                            ? "#cf1322"
                            : "#595959",
                      }}
                    >
                      {item.status}
                    </span>
                  </div>
                  <div>Assigned Cells: {item.assignedCells}</div>
                  <div>Smoke in Region: {item.smokeCount}</div>
                  <div>Fire in Region: {item.fireCount}</div>
                  <div>
                    Alerts in Region:{" "}
                    <span
                      style={{
                        color: item.alertCount > 0 ? "#cf1322" : "#389e0d",
                        fontWeight: 700,
                      }}
                    >
                      {item.alertCount}
                    </span>
                  </div>
                  <div>Route: {item.routeReady ? "Ready" : "Not ready"}</div>
                  <div>Waypoint Index: {item.currentWaypointIndex}</div>
                  <div>Direction: {item.routeDirection === 1 ? "Forward" : "Backward"}</div>
                  <div>Battery: {Number(item.batteryLevel || 0).toFixed(1)}%</div>
                  <div>Last Seen: {formatDateTime(item.lastSeenAt)}</div>
                  <div>
                    Current Position:{" "}
                    {item.currentPosition
                      ? `[${Number(item.currentPosition.lat).toFixed(5)}, ${Number(item.currentPosition.lng).toFixed(5)}]`
                      : "N/A"}
                  </div>
                  <div>
                    Centroid:{" "}
                    {item.centroid
                      ? `[${item.centroid.lat.toFixed(5)}, ${item.centroid.lng.toFixed(5)}]`
                      : "N/A"}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>UAV clusters</div>
          {uavLegend.length === 0 ? (
            <div style={{ fontSize: 12, color: "#666" }}>
              Chưa có cụm. Hãy chạy K-means và Refresh.
            </div>
          ) : (
            <div style={{ marginTop: 6, display: "grid", gap: 6 }}>
              {uavLegend.map(({ uav, count }) => (
                <div key={uav.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span
                    style={{
                      width: 14,
                      height: 14,
                      background: uav.color || "#3388ff",
                      display: "inline-block",
                      border: "1px solid #333",
                    }}
                  />
                  <span style={{ fontSize: 12 }}>
                    {uav.name} (cells: {count})
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>Detection States</div>

          <div style={{ marginTop: 6, display: "grid", gap: 6 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                style={{
                  width: 14,
                  height: 14,
                  background: "#3388ff",
                  opacity: 0.12,
                  display: "inline-block",
                  border: "1px solid #333",
                }}
              />
              <span style={{ fontSize: 12 }}>Bình thường (theo màu cụm UAV)</span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                style={{
                  width: 14,
                  height: 14,
                  background: "#8c8c8c",
                  opacity: 0.5,
                  display: "inline-block",
                  border: "1px solid #333",
                }}
              />
              <span style={{ fontSize: 12 }}>Khói (Smoke)</span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                style={{
                  width: 14,
                  height: 14,
                  background: "#ff4d4f",
                  opacity: 0.7,
                  display: "inline-block",
                  border: "1px solid #333",
                }}
              />
              <span style={{ fontSize: 12 }}>Lửa (Fire)</span>
            </div>
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>UAV Centroids</div>
          {uavMarkers.length === 0 ? (
            <div style={{ fontSize: 12, color: "#666" }}>
              Chưa có centroid. Hãy chạy K-means và Refresh.
            </div>
          ) : (
            <div style={{ marginTop: 6, display: "grid", gap: 6 }}>
              {uavMarkers.map((m) => (
                <div key={m.uav.id} style={{ fontSize: 12 }}>
                  <b>{m.uav.name}</b>: [{m.center.lat.toFixed(5)}, {m.center.lng.toFixed(5)}]
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>Recent Alerts</div>
          {recentAlerts.length === 0 ? (
            <div style={{ fontSize: 12, color: "#666" }}>
              Chưa có cảnh báo gần đây.
            </div>
          ) : (
            <div style={{ marginTop: 6, display: "grid", gap: 8 }}>
              {recentAlerts.map((a) => (
                <div
                  key={a.id}
                  onClick={() => setFocusedCellId(a.cell_id)}
                  style={{
                    fontSize: 12,
                    padding: 8,
                    border: focusedCellId === a.cell_id ? "2px solid #222" : "1px solid #ddd",
                    background: a.label === 2 ? "#fff1f0" : "#f5f5f5",
                    cursor: "pointer",
                  }}
                  title={`Zoom tới Cell #${a.cell_id}`}
                >
                  <div>
                    <b>{labelText(a.label)}</b> — Cell #{a.cell_id}
                  </div>
                  <div>UAV: {a.uav?.name || "N/A"}</div>
                  <div>Confidence: {Number(a.confidence || 0).toFixed(2)}</div>
                  <div>Source: {a.source}</div>
                  <div style={{ color: "#666" }}>{String(a.created_at)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div>
        <MapContainer center={center} zoom={12} style={{ height: "100%", width: "100%" }}>
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution="&copy; OpenStreetMap contributors"
          />

          <DrawControl onPolygonChange={setPolygonGeo} />
          <FocusCellOnMap focusedCellId={focusedCellId} cells={state?.cells || []} />

          {polygons.map((p) => {
            const a = assignmentMap.get(p.id);
            const uavColor = a?.uav?.color || "#3388ff";
            const stroke = cellColor(p.label);
            const fill = cellFillColor(p.label, uavColor);
            const fillOpacity = cellFillOpacity(p.label);
            const isFocused = focusedCellId === p.id;

            return (
              <Polygon
                key={p.id}
                positions={p.coords}
                pathOptions={{
                  color: isFocused ? "#000" : stroke,
                  weight: isFocused ? 3 : (p.label === 2 ? 2 : 1),
                  fillColor: fill,
                  fillOpacity: isFocused ? Math.max(fillOpacity, 0.75) : fillOpacity,
                }}
                eventHandlers={{
                  click: () => {
                    const cell = state.cells.find((x) => x.id === p.id);
                    setFocusedCellId(cell.id);
                    toggleCell(cell);
                  },
                }}
              />
            );
          })}

          {[...routesByUav.entries()].map(([uavId, pts]) => (
            <Polyline key={uavId} positions={pts} />
          ))}

          {uavStatusList.map((m) => {
            const markerLat = m.currentPosition?.lat ?? m.centroid?.lat;
            const markerLng = m.currentPosition?.lng ?? m.centroid?.lng;

            if (markerLat == null || markerLng == null) return null;

            return (
              <Marker
                key={`uav-marker-${m.uav.id}`}
                position={[markerLat, markerLng]}
                icon={makeUavIcon(m.uav.color || "#3388ff", String(m.uav.id))}
              >
                <Tooltip direction="top" offset={[0, -10]} opacity={1}>
                  {m.uav.name}
                </Tooltip>
                <Popup>
                  <div style={{ minWidth: 220 }}>
                    <div><b>{m.uav.name}</b></div>
                    <div>UAV ID: {m.uav.id}</div>
                    <div>Status: {m.status}</div>
                    <div>Assigned Cells: {m.assignedCells}</div>
                    <div>Smoke in Region: {m.smokeCount}</div>
                    <div>Fire in Region: {m.fireCount}</div>
                    <div>Battery: {Number(m.batteryLevel || 0).toFixed(1)}%</div>
                    <div>Waypoint Index: {m.currentWaypointIndex}</div>
                    <div>Direction: {m.routeDirection === 1 ? "Forward" : "Backward"}</div>
                    <div>Last Seen: {formatDateTime(m.lastSeenAt)}</div>
                    <div>
                      Position: [{Number(markerLat).toFixed(6)}, {Number(markerLng).toFixed(6)}]
                    </div>
                  </div>
                </Popup>
              </Marker>
            );
          })}

          {polygons.slice(0, 400).map((p) => (
            <CircleMarker key={`c-${p.id}`} center={p.center} radius={1} />
          ))}
        </MapContainer>
      </div>
    </div>
  );
}