import net from "node:net";

const deadline = Date.now() + 10_000;
let socket;
let connected = false;
function connect() {
  socket = net.createConnection("/tmp/budget-ai-relay.sock");
  socket.on("connect", () => {
    connected = true;
    process.stdout.write(JSON.stringify({ type: "ready" }) + "\n");
    process.stdin.pipe(socket);
    socket.pipe(process.stdout);
  });
  socket.on("error", () => {
    if (!connected && Date.now() < deadline) setTimeout(connect, 100);
    else process.exit(1);
  });
  socket.on("close", () => { if (connected) process.exit(0); });
}
connect();
process.stdin.on("end", () => socket?.end());
process.on("SIGTERM", () => { socket?.destroy(); process.exit(0); });
