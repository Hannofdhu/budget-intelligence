import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";
import "./tableTransfer.css";
import "./budgetDesign.css";
import "./budgetGlass.css";
import "./budgetMotion.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
