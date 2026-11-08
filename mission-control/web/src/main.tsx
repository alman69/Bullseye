import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./mission-updates.css";
import App from "./App.tsx";
import RoverDemo from "./RoverDemo.tsx";
import "./rover-demo.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {window.location.pathname === "/bench" ? <RoverDemo /> : <App />}
  </StrictMode>,
);
