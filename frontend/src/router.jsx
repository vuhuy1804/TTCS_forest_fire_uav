import {
    BrowserRouter,
    Routes,
    Route,
} from "react-router-dom";

import App from "./App";
import SimulationPage from "./SimulationPage";

export default function Router() {

    return (

        <BrowserRouter>

            <Routes>

                <Route
                    path="/"
                    element={<App />}
                />

                <Route
                    path="/simulation"
                    element={<SimulationPage />}
                />

            </Routes>

        </BrowserRouter>

    );
}