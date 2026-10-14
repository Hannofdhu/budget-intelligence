// No network is granted to this container. The host broker is reached only
// through a Unix socket attached to a non-TTY `docker exec` relay.
import http from "node:http";
import net from "node:net";
import { chmodSync, existsSync, unlinkSync } from "node:fs";
import { randomUUID } from "node:crypto";

const socketPath = "/tmp/budget-ai-relay.sock";
const maxBody = 8 * 1024 * 1024;
const maxLine = 16 * 1024 * 1024;
const pending = new Map();
let relay = null;

function fail(id, message, status = 502) {
  const request = pending.get(id);
  if (!request) return;
  pending.delete(id);
  clearTimeout(request.timeout);
  if (!request.response.headersSent) {
    request.response.writeHead(status, { "content-type": "application/json" });
    request.response.end(JSON.stringify({ error: { message } }));
  } else request.response.destroy();
}

function consume(frame) {
  const request = pending.get(frame.id);
  if (!request) return;
  const response = request.response;
  if (frame.type === "response_start") {
    if (response.headersSent) return fail(frame.id, "Duplicate response start");
    const headers = Object.fromEntries(Object.entries(frame.headers || {}).filter(([key]) =>
      !["connection", "transfer-encoding", "content-length", "set-cookie"].includes(key.toLowerCase())));
    response.writeHead(Number(frame.status) || 502, headers);
  } else if (frame.type === "response_chunk") {
    if (!response.headersSent) return fail(frame.id, "Missing response start");
    response.write(Buffer.from(frame.data || "", "base64"));
  } else if (frame.type === "response_end") {
    pending.delete(frame.id);
    clearTimeout(request.timeout);
    response.end();
  } else if (frame.type === "response_error") {
    fail(frame.id, frame.message || "Agent broker unavailable", Number(frame.status) || 502);
  }
}

if (existsSync(socketPath)) unlinkSync(socketPath);
const socketServer = net.createServer(connection => {
  if (relay) { connection.destroy(); return; }
  relay = connection;
  let buffered = "";
  connection.setEncoding("utf8");
  connection.on("data", data => {
    buffered += data;
    if (Buffer.byteLength(buffered) > maxLine) return connection.destroy();
    let end;
    while ((end = buffered.indexOf("\n")) >= 0) {
      const line = buffered.slice(0, end);
      buffered = buffered.slice(end + 1);
      try { consume(JSON.parse(line)); } catch { connection.destroy(); break; }
    }
  });
  connection.on("error", () => {});
  connection.on("close", () => {
    if (relay === connection) relay = null;
    for (const id of pending.keys()) fail(id, "Agent broker disconnected");
  });
});
socketServer.listen(socketPath, () => chmodSync(socketPath, 0o600));

const server = http.createServer(async (request, response) => {
  if (request.url === "/health" && request.method === "GET") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ ready: Boolean(relay) }));
    return;
  }
  if (!relay || pending.size >= 32) {
    response.writeHead(503, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: { message: "Agent broker not ready" } }));
    return;
  }
  if (!request.url?.startsWith("/model/") && !request.url?.startsWith("/budget/")) {
    response.writeHead(404); response.end(); return;
  }
  const id = randomUUID();
  const chunks = [];
  let size = 0;
  try {
    for await (const chunk of request) {
      size += chunk.length;
      if (size > maxBody) {
        response.writeHead(413); response.end(); request.destroy(); return;
      }
      chunks.push(chunk);
    }
    const timeout = setTimeout(() => fail(id, "Agent broker timeout", 504), 300_000);
    pending.set(id, { response, timeout });
    response.on("close", () => {
      const active = pending.get(id);
      if (active) { clearTimeout(active.timeout); pending.delete(id); }
    });
    relay.write(JSON.stringify({
      type: "request", id, method: request.method, path: request.url,
      headers: request.headers, body: Buffer.concat(chunks).toString("base64"),
    }) + "\n");
  } catch { fail(id, "Agent broker request failed"); }
});
server.requestTimeout = 310_000;
server.headersTimeout = 15_000;
server.listen(4081, "127.0.0.1");
function shutdown() {
  for (const id of pending.keys()) fail(id, "Agent runtime stopped");
  relay?.destroy();
  server.close();
  socketServer.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1500).unref();
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
