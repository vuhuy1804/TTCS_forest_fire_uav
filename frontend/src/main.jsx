import React from "react";
import ReactDOM from "react-dom/client";

import Router from "./router";

import "leaflet/dist/leaflet.css";
import "@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css";

ReactDOM.createRoot(document.getElementById("root")).render(

    <Router />

);