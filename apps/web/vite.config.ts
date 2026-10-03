import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: loadEnv(mode, ".", "BUDGET_").BUDGET_LOCAL_API_URL || "http://127.0.0.1:8000",
        bypass(req) {
          // Overwrite client-supplied values before forwarding real FY27 reads.
          req.headers["x-budget-client-address"] = req.socket.remoteAddress || "unknown";
        },
      },
    },
  },
}));
