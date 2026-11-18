import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { E2AuthGate } from "./E2AuthGate";
import "./styles.css";
import "./tableTransfer.css";
import "./budgetDesign.css";
import "./budgetGlass.css";
import "./budgetMotion.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <E2AuthGate>
      <App />
    </E2AuthGate>
  </StrictMode>,
);
