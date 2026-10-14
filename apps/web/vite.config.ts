import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
 const env = { ...loadEnv(mode, ".", "BUDGET_"), ...process.env };
 const target = env.BUDGET_LOCAL_API_URL || "http://127.0.0.1:8000";
 return ({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api/v1/ai-agent": {
        target,
        ws: true,
        configure(proxy) {
          const guard = (proxyReq: import("node:http").ClientRequest, req: import("node:http").IncomingMessage) => {
            // Overwrite caller-supplied values for HTTP and WebSocket upgrades.
            proxyReq.setHeader("x-budget-client-address", req.socket.remoteAddress || "unknown");
            proxyReq.setHeader("x-budget-ai-proxy", env.BUDGET_AI_AGENT_PROXY_SECRET || "disabled");
          };
          proxy.on("proxyReq", guard);
          proxy.on("proxyReqWs", guard);
        },
      },
      "/api": {
        target,
        bypass(req) {
          // Overwrite client-supplied values before forwarding real FY27 reads.
          req.headers["x-budget-client-address"] = req.socket.remoteAddress || "unknown";
        },
      },
    },
  },
 });
});
