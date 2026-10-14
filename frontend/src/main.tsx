import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { initializeAppearance } from "./theme/ThemeProvider";
import "./index.css";
import "./theme/themes.css";

initializeAppearance();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
