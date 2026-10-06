import { useState } from "react";
import axios from "axios";

const API = "http://127.0.0.1:8000/api";

export default function SimulationPage() {

    const [missionId, setMissionId] = useState(1);

    const [fireCellId, setFireCellId] = useState("");

    const [windDirection, setWindDirection] = useState(0);

    const [uavSpeed, setUavSpeed] = useState(10);

    const [smokeAngle, setSmokeAngle] = useState(35);

    const [smokeRadius, setSmokeRadius] = useState("");

    const [searchAngle, setSearchAngle] = useState(40);

    const [searchRadius, setSearchRadius] = useState("");

    const [smokeThreshold, setSmokeThreshold] = useState(0.30);

    const [loading, setLoading] = useState(false);

    const [result, setResult] = useState(null);

    const runSimulation = async () => {

        if (!fireCellId) {
            alert("Nhập Fire Cell ID");
            return;
        }

        setLoading(true);

        try {

            const res = await axios.post(

                `${API}/missions/${missionId}/benchmark/`,

                {

                    fire_cell_id: Number(fireCellId),

                    wind_direction_deg: Number(windDirection),

                    uav_speed: Number(uavSpeed),

                    smoke_angle_deg: Number(smokeAngle),

                    smoke_radius_m:
                        smokeRadius === "" ? null : Number(smokeRadius),

                    search_angle_deg: Number(searchAngle),

                    search_radius_m:
                        searchRadius === "" ? null : Number(searchRadius),

                    smoke_threshold: Number(smokeThreshold),

                }

            );

            setResult(res.data);

        }

        catch (err) {

            console.error(err);

            console.log(err.response);

            alert(JSON.stringify(err.response?.data));

        }

        setLoading(false);

    };

    return (

        <div
            style={{
                width: 900,
                margin: "40px auto",
                fontFamily: "Arial",
            }}
        >

            <h1>Forest Fire Simulation</h1>

            <hr />

            <table>

                <tbody>

                    <tr>

                        <td>Mission ID</td>

                        <td>

                            <input
                                value={missionId}
                                onChange={(e) =>
                                    setMissionId(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                    <tr>

                        <td>Fire Cell ID</td>

                        <td>

                            <input
                                value={fireCellId}
                                onChange={(e) =>
                                    setFireCellId(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                    <tr>

                        <td>Wind Direction (deg)</td>

                        <td>

                            <input
                                value={windDirection}
                                onChange={(e) =>
                                    setWindDirection(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                    <tr>

                        <td>UAV Speed (m/s)</td>

                        <td>

                            <input
                                value={uavSpeed}
                                onChange={(e) =>
                                    setUavSpeed(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                    <tr>

                        <td>Smoke Angle</td>

                        <td>

                            <input
                                value={smokeAngle}
                                onChange={(e) =>
                                    setSmokeAngle(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                    <tr>

                        <td>Smoke Radius</td>

                        <td>

                            <input
                                value={smokeRadius}
                                onChange={(e) =>
                                    setSmokeRadius(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                    <tr>

                        <td>Search Angle</td>

                        <td>

                            <input
                                value={searchAngle}
                                onChange={(e) =>
                                    setSearchAngle(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                    <tr>

                        <td>Search Radius</td>

                        <td>

                            <input
                                value={searchRadius}
                                onChange={(e) =>
                                    setSearchRadius(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                    <tr>

                        <td>Smoke Threshold</td>

                        <td>

                            <input
                                value={smokeThreshold}
                                onChange={(e) =>
                                    setSmokeThreshold(e.target.value)
                                }
                            />

                        </td>

                    </tr>

                </tbody>

            </table>

            <br />

            <button
                onClick={runSimulation}
                disabled={loading}
            >
                Run Simulation
            </button>

            <hr />

            {

                result &&

                <table
                    border="1"
                    cellPadding="10"
                >

                    <thead>

                        <tr>

                            <th>Strategy</th>

                            <th>Distance (m)</th>

                            <th>Time (s)</th>

                            <th>Checked Cells</th>

                            <th>Detected</th>

                            <th>UAV</th>

                        </tr>

                    </thead>

                    <tbody>

                        {

                            result.results.map((r, idx) => (

                                <tr key={idx}>

                                    <td>{r.strategy}</td>

                                    <td>{r.travel_distance_m}</td>

                                    <td>{r.time_seconds}</td>

                                    <td>{r.checked_cells}</td>

                                    <td>

                                        {r.detected ? "YES" : "NO"}

                                    </td>

                                    <td>{r.detected_by}</td>

                                </tr>

                            ))

                        }

                    </tbody>

                </table>

            }

        </div>

    );

}